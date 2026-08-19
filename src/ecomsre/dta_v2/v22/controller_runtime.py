"""Fail-closed controller validation and one bounded correction for DTA v2.2."""

from __future__ import annotations

from enum import Enum
import json
from typing import Any, Literal

from pydantic import (
    Field,
    InstanceOf,
    StrictBool,
    StrictFloat,
    StrictInt,
    ValidationError,
    ValidationInfo,
    model_validator,
)

from ecomsre.dta_v2.v22.action_catalog import ActionCatalogV22
from ecomsre.dta_v2.v22.controller_contracts import (
    ABSTAIN_HYPOTHESIS_ID_V22,
    NO_INCIDENT_HYPOTHESIS_ID_V22,
    BeliefLedgerV22,
    ControllerDecisionKindV22,
    ControllerDecisionV22,
    ControllerProtocolErrorCodeV22,
    HypothesisCatalogV22,
    initialize_belief_ledger_v22,
    record_belief_correction_v22,
    record_belief_turn_v22,
)
from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    Sha256V22,
    semantic_sha256_v22,
)


class ControllerSessionTerminalV22(str, Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ControllerProtocolDispositionV22(str, Enum):
    ACCEPTED = "ACCEPTED"
    CORRECTION_REQUIRED = "CORRECTION_REQUIRED"
    FAILED = "FAILED"


class PlanCorrectionV22(DtaModelV22):
    schema_version: Literal["dta-v22.plan-correction.v1"]
    safe_error_code: ControllerProtocolErrorCodeV22
    current_valid_action_ids: tuple[str, ...]
    remaining_evidence_budget: StrictFloat = Field(ge=0, le=3)
    read_dispatches: StrictInt = Field(ge=0, le=0)
    write_authority: StrictInt = Field(ge=0, le=0)
    correction_sha256: Sha256V22

    @model_validator(mode="after")
    def require_correction(self, info: ValidationInfo) -> PlanCorrectionV22:
        context = info.context if isinstance(info.context, dict) else None
        if context is None or not isinstance(
            context.get("action_catalog"),
            ActionCatalogV22,
        ):
            raise ValueError("plan correction requires current action catalog provenance")
        catalog = context["action_catalog"]
        if (
            self.current_valid_action_ids
            != tuple(item.action_id for item in catalog.actions)
            or self.remaining_evidence_budget != catalog.remaining_budget
        ):
            raise ValueError("plan correction differs from current action catalog")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"correction_sha256"})
        )
        if self.correction_sha256 != expected:
            raise ValueError("plan correction digest differs")
        return self


def _build_correction_v22(
    *,
    error_code: ControllerProtocolErrorCodeV22,
    action_catalog: ActionCatalogV22,
) -> PlanCorrectionV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.plan-correction.v1",
        "safe_error_code": error_code,
        "current_valid_action_ids": tuple(
            item.action_id for item in action_catalog.actions
        ),
        "remaining_evidence_budget": action_catalog.remaining_budget,
        "read_dispatches": 0,
        "write_authority": 0,
    }
    return PlanCorrectionV22.model_validate(
        {**payload, "correction_sha256": semantic_sha256_v22(payload)},
        context={"action_catalog": action_catalog},
    )


class ControllerSessionStateV22(DtaModelV22):
    schema_version: Literal["dta-v22.controller-session-state.v1"]
    arm: ControllerArmV22
    hypothesis_catalog_sha256: Sha256V22
    initial_evidence_budget: StrictFloat = Field(ge=3.0, le=3.0)
    provider_turns_used: StrictInt = Field(ge=0, le=5)
    read_dispatches: StrictInt = Field(ge=0, le=3)
    invalid_attempt_codes: tuple[ControllerProtocolErrorCodeV22, ...] = Field(
        max_length=2
    )
    terminal: ControllerSessionTerminalV22
    ledger: BeliefLedgerV22
    session_sha256: Sha256V22

    @model_validator(mode="after")
    def require_session(self) -> ControllerSessionStateV22:
        expected_turns = len(self.ledger.turn_records) + len(
            self.invalid_attempt_codes
        )
        expected_reads = len(self.ledger.executed_action_ids)
        if len(self.invalid_attempt_codes) >= 2:
            expected_terminal = ControllerSessionTerminalV22.FAILED
        elif (
            self.ledger.turn_records
            and self.ledger.turn_records[-1].decision
            is not ControllerDecisionKindV22.READ
        ):
            expected_terminal = ControllerSessionTerminalV22.COMPLETED
        else:
            expected_terminal = ControllerSessionTerminalV22.ACTIVE
        if (
            self.provider_turns_used != expected_turns
            or self.read_dispatches != expected_reads
            or self.terminal is not expected_terminal
            or self.ledger.correction_used != bool(self.invalid_attempt_codes)
            or (
                self.invalid_attempt_codes
                and self.ledger.correction_error_code
                is not self.invalid_attempt_codes[0]
            )
            or self.ledger.weighted_evidence_cost > self.initial_evidence_budget
        ):
            raise ValueError("controller session differs from runtime-owned state")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"session_sha256"})
        )
        if self.session_sha256 != expected:
            raise ValueError("controller session digest differs")
        return self


def _build_session_v22(
    *,
    arm: ControllerArmV22,
    hypothesis_catalog_sha256: str,
    ledger: BeliefLedgerV22,
    invalid_attempt_codes: tuple[ControllerProtocolErrorCodeV22, ...],
) -> ControllerSessionStateV22:
    provider_turns = len(ledger.turn_records) + len(invalid_attempt_codes)
    if len(invalid_attempt_codes) >= 2:
        terminal = ControllerSessionTerminalV22.FAILED
    elif (
        ledger.turn_records
        and ledger.turn_records[-1].decision is not ControllerDecisionKindV22.READ
    ):
        terminal = ControllerSessionTerminalV22.COMPLETED
    else:
        terminal = ControllerSessionTerminalV22.ACTIVE
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.controller-session-state.v1",
        "arm": arm,
        "hypothesis_catalog_sha256": hypothesis_catalog_sha256,
        "initial_evidence_budget": 3.0,
        "provider_turns_used": provider_turns,
        "read_dispatches": len(ledger.executed_action_ids),
        "invalid_attempt_codes": invalid_attempt_codes,
        "terminal": terminal,
        "ledger": ledger,
    }
    draft = ControllerSessionStateV22.model_construct(
        **payload,
        session_sha256="0" * 64,
    )
    return ControllerSessionStateV22.model_validate(
        {
            **payload,
            "session_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"session_sha256"})
            ),
        }
    )


def initialize_controller_session_v22(
    *,
    arm: ControllerArmV22,
    hypothesis_catalog: HypothesisCatalogV22,
) -> ControllerSessionStateV22:
    catalog = HypothesisCatalogV22.model_validate(
        hypothesis_catalog.model_dump(mode="python")
    )
    return _build_session_v22(
        arm=arm,
        hypothesis_catalog_sha256=catalog.catalog_sha256,
        ledger=initialize_belief_ledger_v22(catalog=catalog),
        invalid_attempt_codes=(),
    )


class ControllerProtocolResultV22(DtaModelV22):
    schema_version: Literal["dta-v22.controller-protocol-result.v1"]
    disposition: ControllerProtocolDispositionV22
    error_code: ControllerProtocolErrorCodeV22 | None
    accepted_decision: ControllerDecisionV22 | None
    correction: InstanceOf[PlanCorrectionV22] | None
    read_dispatch_authorized: StrictBool
    invalid_dispatches: StrictInt = Field(ge=0, le=0)
    session: ControllerSessionStateV22
    result_sha256: Sha256V22

    @model_validator(mode="after")
    def require_result(self) -> ControllerProtocolResultV22:
        if self.disposition is ControllerProtocolDispositionV22.ACCEPTED:
            valid_shape = (
                self.accepted_decision is not None
                and self.correction is None
                and self.error_code is None
                and self.session.terminal is not ControllerSessionTerminalV22.FAILED
            )
        elif self.disposition is ControllerProtocolDispositionV22.CORRECTION_REQUIRED:
            valid_shape = (
                self.accepted_decision is None
                and self.correction is not None
                and self.error_code is not None
                and self.session.terminal is ControllerSessionTerminalV22.ACTIVE
            )
        else:
            valid_shape = (
                self.accepted_decision is None
                and self.correction is None
                and self.error_code is not None
                and self.session.terminal is ControllerSessionTerminalV22.FAILED
            )
        if not valid_shape:
            raise ValueError("controller protocol result shape differs")
        if self.read_dispatch_authorized != (
            self.accepted_decision is not None
            and self.accepted_decision.decision is ControllerDecisionKindV22.READ
        ):
            raise ValueError("controller read dispatch authority differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("controller protocol result digest differs")
        return self


def _build_result_v22(
    *,
    disposition: ControllerProtocolDispositionV22,
    error_code: ControllerProtocolErrorCodeV22 | None,
    accepted_decision: ControllerDecisionV22 | None,
    correction: PlanCorrectionV22 | None,
    session: ControllerSessionStateV22,
) -> ControllerProtocolResultV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.controller-protocol-result.v1",
        "disposition": disposition,
        "error_code": error_code,
        "accepted_decision": accepted_decision,
        "correction": correction,
        "read_dispatch_authorized": (
            accepted_decision is not None
            and accepted_decision.decision is ControllerDecisionKindV22.READ
        ),
        "invalid_dispatches": 0,
        "session": session,
    }
    draft = ControllerProtocolResultV22.model_construct(
        **payload,
        result_sha256="0" * 64,
    )
    return ControllerProtocolResultV22.model_validate(
        {
            **payload,
            "result_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"result_sha256"})
            ),
        }
    )


def _decision_error_v22(
    *,
    session: ControllerSessionStateV22,
    decision: ControllerDecisionV22,
    hypothesis_catalog: HypothesisCatalogV22,
    action_catalog: ActionCatalogV22,
    known_evidence_refs: tuple[str, ...],
) -> ControllerProtocolErrorCodeV22 | None:
    try:
        hypothesis_catalog.require(decision.working_hypothesis_id)
    except ValueError:
        return ControllerProtocolErrorCodeV22.INVALID_DECISION_SHAPE
    if (
        session.arm is ControllerArmV22.PLANNER_LITE
        and decision.decision is ControllerDecisionKindV22.READ
        and decision.working_hypothesis_id
        in {NO_INCIDENT_HYPOTHESIS_ID_V22, ABSTAIN_HYPOTHESIS_ID_V22}
    ):
        return ControllerProtocolErrorCodeV22.INVALID_DECISION_SHAPE
    proposed_refs = set(decision.supporting_evidence_refs) | set(
        decision.contradicting_evidence_refs
    )
    if not proposed_refs.issubset(known_evidence_refs):
        return ControllerProtocolErrorCodeV22.INVALID_EVIDENCE_REF
    if decision.decision is not ControllerDecisionKindV22.READ:
        return None
    if decision.action_id in set(session.ledger.executed_action_ids):
        return ControllerProtocolErrorCodeV22.STALE_ACTION_ID
    registry_ids = {item.action_id for item in action_catalog.registry_actions}
    if decision.action_id not in registry_ids:
        return ControllerProtocolErrorCodeV22.INVALID_ACTION_ID
    if decision.action_id not in {item.action_id for item in action_catalog.actions}:
        return ControllerProtocolErrorCodeV22.ACTION_NO_LONGER_AVAILABLE
    return None


def process_controller_decision_v22(
    *,
    session: ControllerSessionStateV22,
    raw_decision: object,
    hypothesis_catalog: HypothesisCatalogV22,
    action_catalog: ActionCatalogV22,
    known_evidence_refs: tuple[str, ...],
) -> ControllerProtocolResultV22:
    session = ControllerSessionStateV22.model_validate(
        session.model_dump(mode="python")
    )
    hypotheses = HypothesisCatalogV22.model_validate(
        hypothesis_catalog.model_dump(mode="python")
    )
    actions = ActionCatalogV22.model_validate(action_catalog.model_dump(mode="python"))
    if session.terminal is not ControllerSessionTerminalV22.ACTIVE:
        raise ValueError("controller session is already terminal")
    if session.provider_turns_used >= 5:
        raise ValueError("controller Provider turn budget is exhausted")
    if session.hypothesis_catalog_sha256 != hypotheses.catalog_sha256:
        raise ValueError("controller session hypothesis catalog differs")
    if (
        actions.action_coverage.executed_action_ids
        != session.ledger.executed_action_ids
        or actions.action_coverage.covered_capability_keys
        != session.ledger.covered_capability_keys
        or actions.remaining_budget
        != session.initial_evidence_budget - session.ledger.weighted_evidence_cost
    ):
        raise ValueError("controller action catalog differs from runtime session")
    decision: ControllerDecisionV22 | None
    error_code: ControllerProtocolErrorCodeV22 | None
    try:
        decision = (
            ControllerDecisionV22.model_validate(raw_decision)
            if isinstance(raw_decision, ControllerDecisionV22)
            else ControllerDecisionV22.model_validate_json(
                json.dumps(
                    raw_decision,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        )
    except (ValidationError, TypeError, ValueError, json.JSONDecodeError):
        decision = None
        error_code = ControllerProtocolErrorCodeV22.INVALID_DECISION_SHAPE
    else:
        error_code = _decision_error_v22(
            session=session,
            decision=decision,
            hypothesis_catalog=hypotheses,
            action_catalog=actions,
            known_evidence_refs=known_evidence_refs,
        )
    if error_code is not None:
        invalid_codes = (*session.invalid_attempt_codes, error_code)
        if not session.ledger.correction_used:
            ledger = record_belief_correction_v22(
                ledger=session.ledger,
                error_code=error_code,
            )
            updated = _build_session_v22(
                arm=session.arm,
                hypothesis_catalog_sha256=hypotheses.catalog_sha256,
                ledger=ledger,
                invalid_attempt_codes=invalid_codes,
            )
            return _build_result_v22(
                disposition=ControllerProtocolDispositionV22.CORRECTION_REQUIRED,
                error_code=error_code,
                accepted_decision=None,
                correction=_build_correction_v22(
                    error_code=error_code,
                    action_catalog=actions,
                ),
                session=updated,
            )
        updated = _build_session_v22(
            arm=session.arm,
            hypothesis_catalog_sha256=hypotheses.catalog_sha256,
            ledger=session.ledger,
            invalid_attempt_codes=invalid_codes,
        )
        return _build_result_v22(
            disposition=ControllerProtocolDispositionV22.FAILED,
            error_code=error_code,
            accepted_decision=None,
            correction=None,
            session=updated,
        )
    assert decision is not None
    ledger = record_belief_turn_v22(
        ledger=session.ledger,
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        decision=decision,
        known_evidence_refs=known_evidence_refs,
    )
    updated = _build_session_v22(
        arm=session.arm,
        hypothesis_catalog_sha256=hypotheses.catalog_sha256,
        ledger=ledger,
        invalid_attempt_codes=session.invalid_attempt_codes,
    )
    return _build_result_v22(
        disposition=ControllerProtocolDispositionV22.ACCEPTED,
        error_code=None,
        accepted_decision=decision,
        correction=None,
        session=updated,
    )


__all__ = (
    "ControllerProtocolDispositionV22",
    "ControllerProtocolResultV22",
    "ControllerSessionStateV22",
    "ControllerSessionTerminalV22",
    "PlanCorrectionV22",
    "initialize_controller_session_v22",
    "process_controller_decision_v22",
)
