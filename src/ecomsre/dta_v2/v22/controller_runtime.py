"""Fail-closed controller admission, dispatch, and outcome state for DTA v2.2."""

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
    HypothesisCatalogEntryV22,
    HypothesisCatalogV22,
    initialize_belief_ledger_v22,
    record_belief_correction_v22,
    record_belief_turn_v22,
)
from ecomsre.dta_v2.v22.controller_inputs import (
    ControllerArmV22,
    ControllerTurnInputV22,
    TriageSnapshotV22,
)
from ecomsre.dta_v2.v22.diagnosis import (
    DiagnosisAdmissionResultV22,
    DiagnosisTerminalV22,
    HypothesisDefinitionV22,
    RawSemanticDiagnosisProposalV22,
    admit_diagnosis_v22,
)
from ecomsre.dta_v2.v22.memory import MemoryReadOutcomeV22, RuntimeReadOutcomeV22
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    ReadSourceStatusV22,
    Sha256V22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22


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
            context.get("action_catalog"), ActionCatalogV22
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


class PendingReadAuthorizationV22(DtaModelV22):
    schema_version: Literal["dta-v22.pending-read-authorization.v1"]
    decision: ControllerDecisionV22
    action_sha256: Sha256V22
    request_sha256: Sha256V22
    controller_input_sha256: Sha256V22
    dispatched: StrictBool
    authorization_sha256: Sha256V22

    @model_validator(mode="after")
    def require_authorization(self) -> PendingReadAuthorizationV22:
        if self.decision.decision is not ControllerDecisionKindV22.READ:
            raise ValueError("pending read authorization lacks a READ decision")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"authorization_sha256"})
        )
        if self.authorization_sha256 != expected:
            raise ValueError("pending read authorization digest differs")
        return self


def _pending_read_v22(
    *,
    decision: ControllerDecisionV22,
    action_sha256: str,
    request_sha256: str,
    controller_input_sha256: str,
    dispatched: bool,
) -> PendingReadAuthorizationV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.pending-read-authorization.v1",
        "decision": decision,
        "action_sha256": action_sha256,
        "request_sha256": request_sha256,
        "controller_input_sha256": controller_input_sha256,
        "dispatched": dispatched,
    }
    draft = PendingReadAuthorizationV22.model_construct(
        **payload, authorization_sha256="0" * 64
    )
    return PendingReadAuthorizationV22.model_validate(
        {
            **payload,
            "authorization_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"authorization_sha256"})
            ),
        }
    )


class ControllerSessionStateV22(DtaModelV22):
    schema_version: Literal["dta-v22.controller-session-state.v2"]
    arm: ControllerArmV22
    candidate_services: tuple[str, ...]
    hypothesis_catalog_sha256: Sha256V22
    bootstrap_sha256: Sha256V22
    topology_sha256: Sha256V22
    capability_registry_sha256: Sha256V22
    support_policy_sha256: Sha256V22
    controller_identity_sha256: Sha256V22
    initial_evidence_budget: StrictFloat = Field(ge=3.0, le=3.0)
    bootstrap_evidence_cost: StrictFloat = Field(ge=1.0, le=1.0)
    total_evidence_cost: StrictFloat = Field(ge=1.0, le=4.0)
    provider_turns_used: StrictInt = Field(ge=0, le=5)
    read_dispatches: StrictInt = Field(ge=0, le=3)
    accepted_input_sha256s: tuple[Sha256V22, ...] = Field(max_length=5)
    invalid_attempt_codes: tuple[ControllerProtocolErrorCodeV22, ...] = Field(
        max_length=2
    )
    pending_read: PendingReadAuthorizationV22 | None
    terminal_admission: DiagnosisAdmissionResultV22 | None
    terminal: ControllerSessionTerminalV22
    ledger: BeliefLedgerV22
    session_sha256: Sha256V22

    @model_validator(mode="after")
    def require_session(self) -> ControllerSessionStateV22:
        expected_turns = (
            len(self.ledger.turn_records)
            + len(self.invalid_attempt_codes)
            + int(self.pending_read is not None)
        )
        expected_dispatches = len(self.ledger.executed_action_ids) + int(
            self.pending_read is not None and self.pending_read.dispatched
        )
        if len(self.invalid_attempt_codes) >= 2:
            expected_terminal = ControllerSessionTerminalV22.FAILED
        elif self.terminal_admission is not None:
            expected_terminal = (
                ControllerSessionTerminalV22.FAILED
                if self.terminal_admission.terminal is DiagnosisTerminalV22.FAILED
                else ControllerSessionTerminalV22.COMPLETED
            )
        else:
            expected_terminal = ControllerSessionTerminalV22.ACTIVE
        if (
            self.candidate_services != tuple(sorted(set(self.candidate_services)))
            or self.provider_turns_used != expected_turns
            or len(self.accepted_input_sha256s) != expected_turns
            or len(set(self.accepted_input_sha256s)) != expected_turns
            or self.read_dispatches != expected_dispatches
            or self.terminal is not expected_terminal
            or (self.pending_read is not None and self.terminal_admission is not None)
            or self.ledger.correction_used != bool(self.invalid_attempt_codes)
            or (
                self.invalid_attempt_codes
                and self.ledger.correction_error_code
                is not self.invalid_attempt_codes[0]
            )
            or self.total_evidence_cost
            != self.bootstrap_evidence_cost + self.ledger.weighted_evidence_cost
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
    candidate_services: tuple[str, ...],
    hypothesis_catalog_sha256: str,
    bootstrap_sha256: str,
    topology_sha256: str,
    capability_registry_sha256: str,
    support_policy_sha256: str,
    controller_identity_sha256: str,
    ledger: BeliefLedgerV22,
    accepted_input_sha256s: tuple[str, ...],
    invalid_attempt_codes: tuple[ControllerProtocolErrorCodeV22, ...],
    pending_read: PendingReadAuthorizationV22 | None,
    terminal_admission: DiagnosisAdmissionResultV22 | None,
) -> ControllerSessionStateV22:
    provider_turns = (
        len(ledger.turn_records)
        + len(invalid_attempt_codes)
        + int(pending_read is not None)
    )
    read_dispatches = len(ledger.executed_action_ids) + int(
        pending_read is not None and pending_read.dispatched
    )
    if len(invalid_attempt_codes) >= 2:
        terminal = ControllerSessionTerminalV22.FAILED
    elif terminal_admission is not None:
        terminal = (
            ControllerSessionTerminalV22.FAILED
            if terminal_admission.terminal is DiagnosisTerminalV22.FAILED
            else ControllerSessionTerminalV22.COMPLETED
        )
    else:
        terminal = ControllerSessionTerminalV22.ACTIVE
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.controller-session-state.v2",
        "arm": arm,
        "candidate_services": candidate_services,
        "hypothesis_catalog_sha256": hypothesis_catalog_sha256,
        "bootstrap_sha256": bootstrap_sha256,
        "topology_sha256": topology_sha256,
        "capability_registry_sha256": capability_registry_sha256,
        "support_policy_sha256": support_policy_sha256,
        "controller_identity_sha256": controller_identity_sha256,
        "initial_evidence_budget": 3.0,
        "bootstrap_evidence_cost": 1.0,
        "total_evidence_cost": 1.0 + ledger.weighted_evidence_cost,
        "provider_turns_used": provider_turns,
        "read_dispatches": read_dispatches,
        "accepted_input_sha256s": accepted_input_sha256s,
        "invalid_attempt_codes": invalid_attempt_codes,
        "pending_read": pending_read,
        "terminal_admission": terminal_admission,
        "terminal": terminal,
        "ledger": ledger,
    }
    draft = ControllerSessionStateV22.model_construct(
        **payload, session_sha256="0" * 64
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
    controller_identity_sha256: str,
    hypothesis_catalog: HypothesisCatalogV22,
    bootstrap: TriageSnapshotV22,
    support_policy_sha256: str,
) -> ControllerSessionStateV22:
    if not isinstance(arm, ControllerArmV22):
        raise TypeError("controller arm is invalid")
    catalog = HypothesisCatalogV22.model_validate(
        hypothesis_catalog.model_dump(mode="python")
    )
    if catalog.candidate_services != bootstrap.candidate_services:
        raise ValueError("controller session bootstrap candidates differ")
    return _build_session_v22(
        arm=arm,
        candidate_services=catalog.candidate_services,
        hypothesis_catalog_sha256=catalog.catalog_sha256,
        bootstrap_sha256=bootstrap.snapshot_sha256,
        topology_sha256=bootstrap.topology_sha256,
        capability_registry_sha256=bootstrap.capability_registry_sha256,
        support_policy_sha256=support_policy_sha256,
        controller_identity_sha256=controller_identity_sha256,
        ledger=initialize_belief_ledger_v22(catalog=catalog),
        accepted_input_sha256s=(),
        invalid_attempt_codes=(),
        pending_read=None,
        terminal_admission=None,
    )


class ControllerProtocolResultV22(DtaModelV22):
    schema_version: Literal["dta-v22.controller-protocol-result.v2"]
    disposition: ControllerProtocolDispositionV22
    error_code: ControllerProtocolErrorCodeV22 | None
    accepted_decision: ControllerDecisionV22 | None
    correction: InstanceOf[PlanCorrectionV22] | None
    semantic_admission: DiagnosisAdmissionResultV22 | None
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
                and (
                    (self.accepted_decision.decision is ControllerDecisionKindV22.READ)
                    == (self.semantic_admission is None)
                )
            )
        elif self.disposition is ControllerProtocolDispositionV22.CORRECTION_REQUIRED:
            valid_shape = (
                self.accepted_decision is None
                and self.correction is not None
                and self.error_code is not None
                and self.semantic_admission is None
                and self.session.terminal is ControllerSessionTerminalV22.ACTIVE
            )
        else:
            valid_shape = (
                self.accepted_decision is None
                and self.correction is None
                and self.error_code is not None
                and self.semantic_admission is None
                and self.session.terminal is ControllerSessionTerminalV22.FAILED
            )
        if not valid_shape:
            raise ValueError("controller protocol result shape differs")
        if self.read_dispatch_authorized != (
            self.accepted_decision is not None
            and self.accepted_decision.decision is ControllerDecisionKindV22.READ
            and self.session.pending_read is not None
            and not self.session.pending_read.dispatched
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
    semantic_admission: DiagnosisAdmissionResultV22 | None,
    session: ControllerSessionStateV22,
) -> ControllerProtocolResultV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.controller-protocol-result.v2",
        "disposition": disposition,
        "error_code": error_code,
        "accepted_decision": accepted_decision,
        "correction": correction,
        "semantic_admission": semantic_admission,
        "read_dispatch_authorized": (
            accepted_decision is not None
            and accepted_decision.decision is ControllerDecisionKindV22.READ
            and session.pending_read is not None
            and not session.pending_read.dispatched
        ),
        "invalid_dispatches": 0,
        "session": session,
    }
    draft = ControllerProtocolResultV22.model_construct(
        **payload, result_sha256="0" * 64
    )
    return ControllerProtocolResultV22.model_validate(
        {
            **payload,
            "result_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"result_sha256"})
            ),
        }
    )


def _validate_turn_input_v22(
    *, session: ControllerSessionStateV22, turn_input: ControllerTurnInputV22
) -> None:
    if not isinstance(turn_input, ControllerTurnInputV22):
        raise TypeError("controller runtime requires a typed controller turn input")
    turn_input.runtime_context.__class__.model_validate(
        turn_input.runtime_context.model_dump(mode="python")
    )
    HypothesisCatalogV22.model_validate(
        turn_input.hypothesis_catalog.model_dump(mode="python")
    )
    ActionCatalogV22.model_validate(turn_input.action_catalog.model_dump(mode="python"))
    turn_input.evidence_support_policy.__class__.model_validate(
        turn_input.evidence_support_policy.model_dump(mode="python")
    )
    context = turn_input.runtime_context
    if (
        turn_input.input_sha256
        != semantic_sha256_v22(
            turn_input.model_dump(mode="json", exclude={"input_sha256"})
        )
        or turn_input.bootstrap.snapshot_sha256
        != semantic_sha256_v22(
            turn_input.bootstrap.model_dump(
                mode="json", exclude={"snapshot_sha256"}
            )
        )
        or turn_input.salient_memory.memory_sha256
        != semantic_sha256_v22(
            turn_input.salient_memory.model_dump(
                mode="json", exclude={"memory_sha256"}
            )
        )
        or (
            turn_input.belief_ledger_view is not None
            and turn_input.belief_ledger_view.view_sha256
            != semantic_sha256_v22(
                turn_input.belief_ledger_view.model_dump(
                    mode="json", exclude={"view_sha256"}
                )
            )
        )
        or (
        turn_input.arm is not session.arm
        or turn_input.bootstrap.snapshot_sha256 != session.bootstrap_sha256
        or turn_input.hypothesis_catalog.catalog_sha256
        != session.hypothesis_catalog_sha256
        or turn_input.action_catalog.topology_sha256 != session.topology_sha256
        or turn_input.action_catalog.capability_registry_sha256
        != session.capability_registry_sha256
        or turn_input.evidence_support_policy.policy_sha256
        != session.support_policy_sha256
        or context.controller_identity_sha256 != session.controller_identity_sha256
        or context.turn_ordinal != session.provider_turns_used + 1
        or context.remaining_provider_turns != 5 - session.provider_turns_used
        or context.correction_remaining != (not session.ledger.correction_used)
        or context.remaining_evidence_budget
        != session.initial_evidence_budget - session.ledger.weighted_evidence_cost
        or turn_input.action_catalog.action_coverage.executed_action_ids
        != session.ledger.executed_action_ids
        or turn_input.action_catalog.action_coverage.covered_capability_keys
        != session.ledger.covered_capability_keys
        )
    ):
        raise ValueError("controller turn input differs from runtime session authority")


def _decision_error_v22(
    *,
    session: ControllerSessionStateV22,
    decision: ControllerDecisionV22,
    turn_input: ControllerTurnInputV22,
) -> ControllerProtocolErrorCodeV22 | None:
    hypotheses = turn_input.hypothesis_catalog
    actions = turn_input.action_catalog
    try:
        hypotheses.require(decision.working_hypothesis_id)
    except ValueError:
        return ControllerProtocolErrorCodeV22.INVALID_DECISION_SHAPE
    if (
        session.arm is ControllerArmV22.PLANNER_LITE
        and decision.decision is ControllerDecisionKindV22.READ
        and decision.working_hypothesis_id
        in {NO_INCIDENT_HYPOTHESIS_ID_V22, ABSTAIN_HYPOTHESIS_ID_V22}
    ):
        return ControllerProtocolErrorCodeV22.INVALID_DECISION_SHAPE
    known_refs = {item.evidence_ref for item in turn_input.salient_memory.evidence_refs}
    proposed_refs = set(decision.supporting_evidence_refs) | set(
        decision.contradicting_evidence_refs
    )
    if not proposed_refs.issubset(known_refs):
        return ControllerProtocolErrorCodeV22.INVALID_EVIDENCE_REF
    if decision.decision is not ControllerDecisionKindV22.READ:
        return None
    if decision.action_id in set(session.ledger.executed_action_ids):
        return ControllerProtocolErrorCodeV22.STALE_ACTION_ID
    registry_ids = {item.action_id for item in actions.registry_actions}
    if decision.action_id not in registry_ids:
        return ControllerProtocolErrorCodeV22.INVALID_ACTION_ID
    if decision.action_id not in {item.action_id for item in actions.actions}:
        return ControllerProtocolErrorCodeV22.ACTION_NO_LONGER_AVAILABLE
    return None


def _hypothesis_definition_v22(
    *, entry: HypothesisCatalogEntryV22, turn_input: ControllerTurnInputV22
) -> HypothesisDefinitionV22:
    target = entry.target_service or turn_input.hypothesis_catalog.candidate_services[0]
    parent: str | None = None
    if entry.mechanism is MechanismV22.DEPENDENCY_LATENCY:
        parent = next(
            (
                right if left == target else left
                for left, right in turn_input.bootstrap.candidate_subgraph_edges
                if target in {left, right}
            ),
            None,
        )
        if parent is None:
            raise ValueError("dependency hypothesis lacks a canonical topology parent")
    return HypothesisDefinitionV22.build(
        hypothesis_id=(
            "h:none:no-incident"
            if entry.hypothesis_id == NO_INCIDENT_HYPOTHESIS_ID_V22
            else entry.hypothesis_id
        ),
        target_service=target,
        parent_service=parent,
        root_service=target,
        fault_domain=entry.fault_domain,
        mechanism=entry.mechanism,
        root_entity_ref=f"service:{target}",
    )


_FAILED_SOURCE_STATUSES_V22 = {
    ReadSourceStatusV22.FAILURE_UNAVAILABLE,
    ReadSourceStatusV22.FAILURE_TIMEOUT,
    ReadSourceStatusV22.FAILURE_SCHEMA,
}


def _admit_terminal_v22(
    *, decision: ControllerDecisionV22, turn_input: ControllerTurnInputV22
) -> DiagnosisAdmissionResultV22:
    selected = turn_input.hypothesis_catalog.require(decision.working_hypothesis_id)
    hypothesis = _hypothesis_definition_v22(entry=selected, turn_input=turn_input)
    proposal = RawSemanticDiagnosisProposalV22.build(
        hypothesis_id=hypothesis.hypothesis_id,
        supporting_evidence_refs=decision.supporting_evidence_refs,
        contradicting_evidence_refs=decision.contradicting_evidence_refs,
    )
    unavailable = any(
        item.status in _FAILED_SOURCE_STATUSES_V22
        for item in turn_input.salient_memory.observation_summaries
    )
    return admit_diagnosis_v22(
        proposal=proposal,
        hypotheses=(hypothesis,),
        memory=turn_input.salient_memory,
        policy=turn_input.evidence_support_policy,
        candidate_services=turn_input.hypothesis_catalog.candidate_services,
        budget_exhausted=turn_input.action_catalog.remaining_budget == 0,
        evidence_source_unavailable=unavailable,
        conflicting_evidence=bool(decision.contradicting_evidence_refs),
    )


def process_controller_decision_v22(
    *,
    session: ControllerSessionStateV22,
    raw_decision: object,
    turn_input: ControllerTurnInputV22,
) -> ControllerProtocolResultV22:
    session = ControllerSessionStateV22.model_validate(session.model_dump(mode="python"))
    if session.terminal is not ControllerSessionTerminalV22.ACTIVE:
        raise ValueError("controller session is already terminal")
    if session.pending_read is not None:
        raise ValueError("controller read outcome is still pending")
    if session.provider_turns_used >= 5:
        raise ValueError("controller Provider turn budget is exhausted")
    _validate_turn_input_v22(session=session, turn_input=turn_input)
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
            turn_input=turn_input,
        )
    input_hashes = (*session.accepted_input_sha256s, turn_input.input_sha256)
    if error_code is not None:
        invalid_codes = (*session.invalid_attempt_codes, error_code)
        if not session.ledger.correction_used:
            ledger = record_belief_correction_v22(
                ledger=session.ledger, error_code=error_code
            )
            updated = _build_session_v22(
                arm=session.arm,
                candidate_services=session.candidate_services,
                hypothesis_catalog_sha256=session.hypothesis_catalog_sha256,
                bootstrap_sha256=session.bootstrap_sha256,
                topology_sha256=session.topology_sha256,
                capability_registry_sha256=session.capability_registry_sha256,
                support_policy_sha256=session.support_policy_sha256,
                controller_identity_sha256=session.controller_identity_sha256,
                ledger=ledger,
                accepted_input_sha256s=input_hashes,
                invalid_attempt_codes=invalid_codes,
                pending_read=None,
                terminal_admission=None,
            )
            return _build_result_v22(
                disposition=ControllerProtocolDispositionV22.CORRECTION_REQUIRED,
                error_code=error_code,
                accepted_decision=None,
                correction=_build_correction_v22(
                    error_code=error_code,
                    action_catalog=turn_input.action_catalog,
                ),
                semantic_admission=None,
                session=updated,
            )
        updated = _build_session_v22(
            arm=session.arm,
            candidate_services=session.candidate_services,
            hypothesis_catalog_sha256=session.hypothesis_catalog_sha256,
            bootstrap_sha256=session.bootstrap_sha256,
            topology_sha256=session.topology_sha256,
            capability_registry_sha256=session.capability_registry_sha256,
            support_policy_sha256=session.support_policy_sha256,
            controller_identity_sha256=session.controller_identity_sha256,
            ledger=session.ledger,
            accepted_input_sha256s=input_hashes,
            invalid_attempt_codes=invalid_codes,
            pending_read=None,
            terminal_admission=None,
        )
        return _build_result_v22(
            disposition=ControllerProtocolDispositionV22.FAILED,
            error_code=error_code,
            accepted_decision=None,
            correction=None,
            semantic_admission=None,
            session=updated,
        )
    assert decision is not None
    if decision.decision is ControllerDecisionKindV22.READ:
        action = next(
            item
            for item in turn_input.action_catalog.actions
            if item.action_id == decision.action_id
        )
        pending = _pending_read_v22(
            decision=decision,
            action_sha256=action.action_sha256,
            request_sha256=action.request_sha256,
            controller_input_sha256=turn_input.input_sha256,
            dispatched=False,
        )
        updated = _build_session_v22(
            arm=session.arm,
            candidate_services=session.candidate_services,
            hypothesis_catalog_sha256=session.hypothesis_catalog_sha256,
            bootstrap_sha256=session.bootstrap_sha256,
            topology_sha256=session.topology_sha256,
            capability_registry_sha256=session.capability_registry_sha256,
            support_policy_sha256=session.support_policy_sha256,
            controller_identity_sha256=session.controller_identity_sha256,
            ledger=session.ledger,
            accepted_input_sha256s=input_hashes,
            invalid_attempt_codes=session.invalid_attempt_codes,
            pending_read=pending,
            terminal_admission=None,
        )
        admission = None
    else:
        admission = _admit_terminal_v22(decision=decision, turn_input=turn_input)
        ledger = record_belief_turn_v22(
            ledger=session.ledger,
            hypothesis_catalog=turn_input.hypothesis_catalog,
            action_catalog=turn_input.action_catalog,
            decision=decision,
            known_evidence_refs=tuple(
                item.evidence_ref for item in turn_input.salient_memory.evidence_refs
            ),
            read_outcome_sha256=None,
            semantic_admitted=admission.terminal is not DiagnosisTerminalV22.FAILED,
        )
        updated = _build_session_v22(
            arm=session.arm,
            candidate_services=session.candidate_services,
            hypothesis_catalog_sha256=session.hypothesis_catalog_sha256,
            bootstrap_sha256=session.bootstrap_sha256,
            topology_sha256=session.topology_sha256,
            capability_registry_sha256=session.capability_registry_sha256,
            support_policy_sha256=session.support_policy_sha256,
            controller_identity_sha256=session.controller_identity_sha256,
            ledger=ledger,
            accepted_input_sha256s=input_hashes,
            invalid_attempt_codes=session.invalid_attempt_codes,
            pending_read=None,
            terminal_admission=admission,
        )
    return _build_result_v22(
        disposition=ControllerProtocolDispositionV22.ACCEPTED,
        error_code=None,
        accepted_decision=decision,
        correction=None,
        semantic_admission=admission,
        session=updated,
    )


def record_controller_read_dispatch_v22(
    *, session: ControllerSessionStateV22, authorization_sha256: str
) -> ControllerSessionStateV22:
    session = ControllerSessionStateV22.model_validate(session.model_dump(mode="python"))
    pending = session.pending_read
    if (
        session.terminal is not ControllerSessionTerminalV22.ACTIVE
        or pending is None
        or pending.dispatched
        or pending.authorization_sha256 != authorization_sha256
    ):
        raise ValueError("controller read dispatch lacks exact pending authority")
    dispatched = _pending_read_v22(
        decision=pending.decision,
        action_sha256=pending.action_sha256,
        request_sha256=pending.request_sha256,
        controller_input_sha256=pending.controller_input_sha256,
        dispatched=True,
    )
    return _build_session_v22(
        arm=session.arm,
        candidate_services=session.candidate_services,
        hypothesis_catalog_sha256=session.hypothesis_catalog_sha256,
        bootstrap_sha256=session.bootstrap_sha256,
        topology_sha256=session.topology_sha256,
        capability_registry_sha256=session.capability_registry_sha256,
        support_policy_sha256=session.support_policy_sha256,
        controller_identity_sha256=session.controller_identity_sha256,
        ledger=session.ledger,
        accepted_input_sha256s=session.accepted_input_sha256s,
        invalid_attempt_codes=session.invalid_attempt_codes,
        pending_read=dispatched,
        terminal_admission=None,
    )


def record_controller_read_outcome_v22(
    *,
    session: ControllerSessionStateV22,
    turn_input: ControllerTurnInputV22,
    outcome: MemoryReadOutcomeV22,
) -> ControllerSessionStateV22:
    session = ControllerSessionStateV22.model_validate(session.model_dump(mode="python"))
    pending = session.pending_read
    if (
        session.terminal is not ControllerSessionTerminalV22.ACTIVE
        or pending is None
        or not pending.dispatched
        or pending.controller_input_sha256 != turn_input.input_sha256
    ):
        raise ValueError("controller read outcome lacks dispatched authority")
    _validate_turn_input_v22_for_outcome(session=session, turn_input=turn_input)
    action = next(
        (
            item
            for item in turn_input.action_catalog.registry_actions
            if item.action_id == pending.decision.action_id
        ),
        None,
    )
    if action is None:
        raise ValueError("controller read outcome action is outside registry")
    validated: MemoryReadOutcomeV22
    if isinstance(outcome, RuntimeReadOutcomeV22):
        validated = RuntimeReadOutcomeV22.model_validate(outcome.model_dump(mode="python"))
    else:
        validated = ReadOutcomeV22.model_validate(outcome.model_dump(mode="python"))
    if (
        validated.action_id != action.action_id
        or validated.source is not action.source
        or validated.request_sha256 != action.request_sha256
        or pending.action_sha256 != action.action_sha256
        or pending.request_sha256 != action.request_sha256
    ):
        raise ValueError("controller read outcome differs from authorized action")
    ledger = record_belief_turn_v22(
        ledger=session.ledger,
        hypothesis_catalog=turn_input.hypothesis_catalog,
        action_catalog=turn_input.action_catalog,
        decision=pending.decision,
        known_evidence_refs=tuple(
            item.evidence_ref for item in turn_input.salient_memory.evidence_refs
        ),
        read_outcome_sha256=validated.outcome_sha256,
        semantic_admitted=False,
    )
    return _build_session_v22(
        arm=session.arm,
        candidate_services=session.candidate_services,
        hypothesis_catalog_sha256=session.hypothesis_catalog_sha256,
        bootstrap_sha256=session.bootstrap_sha256,
        topology_sha256=session.topology_sha256,
        capability_registry_sha256=session.capability_registry_sha256,
        support_policy_sha256=session.support_policy_sha256,
        controller_identity_sha256=session.controller_identity_sha256,
        ledger=ledger,
        accepted_input_sha256s=session.accepted_input_sha256s,
        invalid_attempt_codes=session.invalid_attempt_codes,
        pending_read=None,
        terminal_admission=None,
    )


def _validate_turn_input_v22_for_outcome(
    *, session: ControllerSessionStateV22, turn_input: ControllerTurnInputV22
) -> None:
    if (
        turn_input.arm is not session.arm
        or turn_input.bootstrap.snapshot_sha256 != session.bootstrap_sha256
        or turn_input.hypothesis_catalog.catalog_sha256
        != session.hypothesis_catalog_sha256
        or turn_input.action_catalog.topology_sha256 != session.topology_sha256
        or turn_input.action_catalog.capability_registry_sha256
        != session.capability_registry_sha256
        or turn_input.evidence_support_policy.policy_sha256
        != session.support_policy_sha256
    ):
        raise ValueError("controller read outcome input authority differs")


__all__ = (
    "ControllerProtocolDispositionV22",
    "ControllerProtocolResultV22",
    "ControllerSessionStateV22",
    "ControllerSessionTerminalV22",
    "PendingReadAuthorizationV22",
    "PlanCorrectionV22",
    "initialize_controller_session_v22",
    "process_controller_decision_v22",
    "record_controller_read_dispatch_v22",
    "record_controller_read_outcome_v22",
)
