"""Deterministic 50-transition protocol harness for DTA v2.2 PR-D."""

from __future__ import annotations

from enum import Enum
from collections.abc import Mapping
from typing import Any, Literal, Protocol

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.action_catalog import (
    ActionCatalogV22,
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
    build_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.controller_contracts import (
    ABSTAIN_HYPOTHESIS_ID_V22,
    NO_ACTION_ID_V22,
    NO_INCIDENT_HYPOTHESIS_ID_V22,
    ControllerDecisionKindV22,
    ControllerDecisionV22,
    ControllerProtocolErrorCodeV22,
    HypothesisCatalogV22,
    build_hypothesis_catalog_v22,
)
from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.controller_modes import (
    PRIMARY_MODEL_V22,
    ProviderModeCapabilityReportV22,
    ProviderOutputModeV22,
    build_controller_identity_manifests_v22,
)
from ecomsre.dta_v2.v22.controller_provider import ProviderControllerTurnV22
from ecomsre.dta_v2.v22.controller_runtime import (
    ControllerProtocolDispositionV22,
    ControllerSessionStateV22,
    initialize_controller_session_v22,
    process_controller_decision_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    Sha256V22,
    semantic_sha256_v22,
)


_KNOWN_REF_V22 = "e:a:changes:payment:0:111111111111"


class SyntheticTransitionCategoryV22(str, Enum):
    VALID_READ = "VALID_READ"
    VALID_COMMIT = "VALID_COMMIT"
    VALID_NO_INCIDENT = "VALID_NO_INCIDENT"
    VALID_ABSTAIN = "VALID_ABSTAIN"
    BUDGET_EXHAUSTION = "BUDGET_EXHAUSTION"
    EMPTY_SOURCE = "EMPTY_SOURCE"
    UNAVAILABLE_SOURCE = "UNAVAILABLE_SOURCE"
    STALE_ACTION_CORRECTION = "STALE_ACTION_CORRECTION"
    INVALID_REF_CORRECTION = "INVALID_REF_CORRECTION"


_CANONICAL_CATEGORIES_V22 = (
    *(SyntheticTransitionCategoryV22.VALID_READ for _ in range(8)),
    *(SyntheticTransitionCategoryV22.VALID_COMMIT for _ in range(8)),
    *(SyntheticTransitionCategoryV22.VALID_NO_INCIDENT for _ in range(8)),
    *(SyntheticTransitionCategoryV22.VALID_ABSTAIN for _ in range(8)),
    *(SyntheticTransitionCategoryV22.BUDGET_EXHAUSTION for _ in range(6)),
    *(SyntheticTransitionCategoryV22.EMPTY_SOURCE for _ in range(5)),
    *(SyntheticTransitionCategoryV22.UNAVAILABLE_SOURCE for _ in range(5)),
    SyntheticTransitionCategoryV22.STALE_ACTION_CORRECTION,
    SyntheticTransitionCategoryV22.INVALID_REF_CORRECTION,
)


class SyntheticTransitionResultV22(DtaModelV22):
    schema_version: Literal["dta-v22.synthetic-transition-result.v1"]
    transition_id: str = Field(pattern=r"^dta-v22-protocol-[0-9]{3}$")
    category: SyntheticTransitionCategoryV22
    first_pass_accepted: StrictBool
    post_correction_accepted: StrictBool
    correction_used: StrictBool
    first_error_code: ControllerProtocolErrorCodeV22 | None
    invalid_dispatches: StrictInt = Field(ge=0, le=0)
    transition_sha256: Sha256V22

    @model_validator(mode="after")
    def require_transition(self) -> SyntheticTransitionResultV22:
        correction_category = self.category in {
            SyntheticTransitionCategoryV22.STALE_ACTION_CORRECTION,
            SyntheticTransitionCategoryV22.INVALID_REF_CORRECTION,
        }
        if (
            self.correction_used != correction_category
            or self.first_pass_accepted == correction_category
            or (self.first_error_code is not None) != correction_category
        ):
            raise ValueError("synthetic transition correction semantics differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"transition_sha256"})
        )
        if self.transition_sha256 != expected:
            raise ValueError("synthetic transition digest differs")
        return self


def _transition_result_v22(
    *,
    ordinal: int,
    category: SyntheticTransitionCategoryV22,
    first_pass_accepted: bool,
    post_correction_accepted: bool,
    correction_used: bool,
    first_error_code: ControllerProtocolErrorCodeV22 | None,
    invalid_dispatches: int,
) -> SyntheticTransitionResultV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.synthetic-transition-result.v1",
        "transition_id": f"dta-v22-protocol-{ordinal:03d}",
        "category": category,
        "first_pass_accepted": first_pass_accepted,
        "post_correction_accepted": post_correction_accepted,
        "correction_used": correction_used,
        "first_error_code": first_error_code,
        "invalid_dispatches": invalid_dispatches,
    }
    return SyntheticTransitionResultV22.model_validate(
        {**payload, "transition_sha256": semantic_sha256_v22(payload)}
    )


class ProtocolSuiteTerminalV22(str, Enum):
    LOCAL_HARNESS_PASS = "LOCAL_HARNESS_PASS"
    LOCAL_HARNESS_FAILED = "LOCAL_HARNESS_FAILED"
    PROVIDER_PROTOCOL_GATE_PASS = "PROVIDER_PROTOCOL_GATE_PASS"


class ProtocolCapabilitySuiteReportV22(DtaModelV22):
    schema_version: Literal["dta-v22.protocol-capability-suite-report.v1"]
    execution_mode: Literal["LOCAL_DETERMINISTIC_HARNESS"]
    transitions: tuple[SyntheticTransitionResultV22, ...] = Field(min_length=40)
    transition_count: StrictInt = Field(ge=40)
    first_pass_accepted_count: StrictInt = Field(ge=0)
    post_correction_accepted_count: StrictInt = Field(ge=0)
    correction_count: StrictInt = Field(ge=0)
    first_pass_protocol_acceptance: StrictFloat = Field(ge=0, le=1)
    post_correction_protocol_acceptance: StrictFloat = Field(ge=0, le=1)
    correction_rate: StrictFloat = Field(ge=0, le=1)
    invalid_dispatches: StrictInt = Field(ge=0)
    provider_calls: StrictInt = Field(ge=0, le=0)
    provider_gate_eligible: Literal[False]
    terminal: ProtocolSuiteTerminalV22
    report_sha256: Sha256V22

    @model_validator(mode="after")
    def require_report(self) -> ProtocolCapabilitySuiteReportV22:
        ids = tuple(item.transition_id for item in self.transitions)
        expected_ids = tuple(
            f"dta-v22-protocol-{index:03d}"
            for index in range(1, len(_CANONICAL_CATEGORIES_V22) + 1)
        )
        if (
            ids != expected_ids
            or tuple(item.category for item in self.transitions)
            != _CANONICAL_CATEGORIES_V22
        ):
            raise ValueError("protocol suite differs from canonical transition matrix")
        count = len(self.transitions)
        first = sum(item.first_pass_accepted for item in self.transitions)
        post = sum(item.post_correction_accepted for item in self.transitions)
        corrections = sum(item.correction_used for item in self.transitions)
        invalid = sum(item.invalid_dispatches for item in self.transitions)
        first_rate = first / count
        post_rate = post / count
        correction_rate = corrections / count
        gate_pass = first_rate >= 0.95 and post_rate >= 0.98 and invalid == 0
        expected_terminal = (
            ProtocolSuiteTerminalV22.LOCAL_HARNESS_PASS
            if gate_pass
            else ProtocolSuiteTerminalV22.LOCAL_HARNESS_FAILED
        )
        if (
            self.transition_count != count
            or self.first_pass_accepted_count != first
            or self.post_correction_accepted_count != post
            or self.correction_count != corrections
            or self.first_pass_protocol_acceptance != first_rate
            or self.post_correction_protocol_acceptance != post_rate
            or self.correction_rate != correction_rate
            or self.invalid_dispatches != invalid
            or self.terminal is not expected_terminal
        ):
            raise ValueError("protocol suite gate metrics differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("protocol suite report digest differs")
        return self


def _topology_v22() -> StaticTopologyV22:
    return StaticTopologyV22.build(
        services=("checkout", "payment"),
        edges=(("checkout", "payment"),),
    )


def _actions_v22(
    *,
    executed: tuple[str, ...] = (),
    remaining_budget: float = 3.0,
    disabled_sources: tuple[EvidenceSourceV22, ...] = (),
) -> ActionCatalogV22:
    registry = (
        build_default_tool_capability_registry_v22()
        if not disabled_sources
        else build_tool_capability_registry_v22(
            disabled_sources=disabled_sources
        )
    )
    return build_action_catalog_v22(
        candidate_services=("checkout", "payment"),
        topology=_topology_v22(),
        capability_registry=registry,
        executed_action_ids=executed,
        remaining_budget=remaining_budget,
    )


def _terminal_decision_v22(
    category: SyntheticTransitionCategoryV22,
) -> ControllerDecisionV22:
    if category is SyntheticTransitionCategoryV22.VALID_COMMIT:
        return ControllerDecisionV22(
            decision=ControllerDecisionKindV22.COMMIT,
            working_hypothesis_id="h:payment:configuration-error",
            action_id=NO_ACTION_ID_V22,
            supporting_evidence_refs=(_KNOWN_REF_V22,),
            contradicting_evidence_refs=(),
        )
    if category is SyntheticTransitionCategoryV22.VALID_NO_INCIDENT:
        return ControllerDecisionV22(
            decision=ControllerDecisionKindV22.NO_INCIDENT,
            working_hypothesis_id=NO_INCIDENT_HYPOTHESIS_ID_V22,
            action_id=NO_ACTION_ID_V22,
            supporting_evidence_refs=(),
            contradicting_evidence_refs=(),
        )
    return ControllerDecisionV22(
        decision=ControllerDecisionKindV22.ABSTAIN,
        working_hypothesis_id=ABSTAIN_HYPOTHESIS_ID_V22,
        action_id=NO_ACTION_ID_V22,
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
    )


def _process_v22(
    *,
    session: ControllerSessionStateV22,
    decision: object,
    hypotheses: HypothesisCatalogV22,
    actions: ActionCatalogV22,
) -> tuple[ControllerProtocolDispositionV22, ControllerSessionStateV22, int, ControllerProtocolErrorCodeV22 | None]:
    result = process_controller_decision_v22(
        session=session,
        raw_decision=decision,
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        known_evidence_refs=(_KNOWN_REF_V22,),
    )
    return (
        result.disposition,
        result.session,
        result.invalid_dispatches,
        result.error_code,
    )


def _run_transition_v22(
    *,
    ordinal: int,
    category: SyntheticTransitionCategoryV22,
    hypotheses: HypothesisCatalogV22,
) -> SyntheticTransitionResultV22:
    session = initialize_controller_session_v22(
        arm=ControllerArmV22.FLAT_CANONICAL,
        hypothesis_catalog=hypotheses,
    )
    actions = _actions_v22()
    if category in {
        SyntheticTransitionCategoryV22.VALID_READ,
        SyntheticTransitionCategoryV22.EMPTY_SOURCE,
    }:
        action = actions.actions[ordinal % len(actions.actions)]
        decision: object = ControllerDecisionV22(
            decision=ControllerDecisionKindV22.READ,
            working_hypothesis_id=ABSTAIN_HYPOTHESIS_ID_V22,
            action_id=action.action_id,
            supporting_evidence_refs=(),
            contradicting_evidence_refs=(),
        )
    elif category is SyntheticTransitionCategoryV22.UNAVAILABLE_SOURCE:
        actions = _actions_v22(disabled_sources=(EvidenceSourceV22.LOGS,))
        decision = _terminal_decision_v22(category)
    elif category is SyntheticTransitionCategoryV22.BUDGET_EXHAUSTION:
        for _ in range(3):
            action = next(item for item in actions.actions if item.weighted_cost == 1.0)
            disposition, session, _, _ = _process_v22(
                session=session,
                decision=ControllerDecisionV22(
                    decision=ControllerDecisionKindV22.READ,
                    working_hypothesis_id=ABSTAIN_HYPOTHESIS_ID_V22,
                    action_id=action.action_id,
                    supporting_evidence_refs=(),
                    contradicting_evidence_refs=(),
                ),
                hypotheses=hypotheses,
                actions=actions,
            )
            if disposition is not ControllerProtocolDispositionV22.ACCEPTED:
                raise AssertionError("budget setup READ was not accepted")
            actions = _actions_v22(
                executed=session.ledger.executed_action_ids,
                remaining_budget=3.0 - session.ledger.weighted_evidence_cost,
            )
        decision = _terminal_decision_v22(category)
    elif category is SyntheticTransitionCategoryV22.STALE_ACTION_CORRECTION:
        selected = actions.actions[0]
        read = ControllerDecisionV22(
            decision=ControllerDecisionKindV22.READ,
            working_hypothesis_id=ABSTAIN_HYPOTHESIS_ID_V22,
            action_id=selected.action_id,
            supporting_evidence_refs=(),
            contradicting_evidence_refs=(),
        )
        _, session, _, _ = _process_v22(
            session=session,
            decision=read,
            hypotheses=hypotheses,
            actions=actions,
        )
        actions = _actions_v22(
            executed=session.ledger.executed_action_ids,
            remaining_budget=3.0 - session.ledger.weighted_evidence_cost,
        )
        first, session, invalid, error = _process_v22(
            session=session,
            decision=read,
            hypotheses=hypotheses,
            actions=actions,
        )
        final, session, final_invalid, _ = _process_v22(
            session=session,
            decision=_terminal_decision_v22(category),
            hypotheses=hypotheses,
            actions=actions,
        )
        return _transition_result_v22(
            ordinal=ordinal,
            category=category,
            first_pass_accepted=first is ControllerProtocolDispositionV22.ACCEPTED,
            post_correction_accepted=final is ControllerProtocolDispositionV22.ACCEPTED,
            correction_used=True,
            first_error_code=error,
            invalid_dispatches=invalid + final_invalid,
        )
    elif category is SyntheticTransitionCategoryV22.INVALID_REF_CORRECTION:
        first, session, invalid, error = _process_v22(
            session=session,
            decision={
                "decision": "COMMIT",
                "working_hypothesis_id": "h:payment:configuration-error",
                "action_id": "NONE",
                "supporting_evidence_refs": [
                    "e:a:logs:payment:0:222222222222"
                ],
                "contradicting_evidence_refs": [],
            },
            hypotheses=hypotheses,
            actions=actions,
        )
        final, session, final_invalid, _ = _process_v22(
            session=session,
            decision=_terminal_decision_v22(category),
            hypotheses=hypotheses,
            actions=actions,
        )
        return _transition_result_v22(
            ordinal=ordinal,
            category=category,
            first_pass_accepted=first is ControllerProtocolDispositionV22.ACCEPTED,
            post_correction_accepted=final is ControllerProtocolDispositionV22.ACCEPTED,
            correction_used=True,
            first_error_code=error,
            invalid_dispatches=invalid + final_invalid,
        )
    else:
        decision = _terminal_decision_v22(category)
    first, _session, invalid, error = _process_v22(
        session=session,
        decision=decision,
        hypotheses=hypotheses,
        actions=actions,
    )
    accepted = first is ControllerProtocolDispositionV22.ACCEPTED
    return _transition_result_v22(
        ordinal=ordinal,
        category=category,
        first_pass_accepted=accepted,
        post_correction_accepted=accepted,
        correction_used=False,
        first_error_code=error,
        invalid_dispatches=invalid,
    )


def run_local_protocol_capability_suite_v22() -> ProtocolCapabilitySuiteReportV22:
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=("checkout", "payment")
    )
    transitions = tuple(
        _run_transition_v22(
            ordinal=index,
            category=category,
            hypotheses=hypotheses,
        )
        for index, category in enumerate(_CANONICAL_CATEGORIES_V22, start=1)
    )
    count = len(transitions)
    first = sum(item.first_pass_accepted for item in transitions)
    post = sum(item.post_correction_accepted for item in transitions)
    corrections = sum(item.correction_used for item in transitions)
    invalid = sum(item.invalid_dispatches for item in transitions)
    gate_pass = first / count >= 0.95 and post / count >= 0.98 and invalid == 0
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.protocol-capability-suite-report.v1",
        "execution_mode": "LOCAL_DETERMINISTIC_HARNESS",
        "transitions": transitions,
        "transition_count": count,
        "first_pass_accepted_count": first,
        "post_correction_accepted_count": post,
        "correction_count": corrections,
        "first_pass_protocol_acceptance": first / count,
        "post_correction_protocol_acceptance": post / count,
        "correction_rate": corrections / count,
        "invalid_dispatches": invalid,
        "provider_calls": 0,
        "provider_gate_eligible": False,
        "terminal": (
            ProtocolSuiteTerminalV22.LOCAL_HARNESS_PASS
            if gate_pass
            else ProtocolSuiteTerminalV22.LOCAL_HARNESS_FAILED
        ),
    }
    draft = ProtocolCapabilitySuiteReportV22.model_construct(
        **payload,
        report_sha256="0" * 64,
    )
    return ProtocolCapabilitySuiteReportV22.model_validate(
        {
            **payload,
            "report_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )


class ProviderSyntheticTransitionResultV22(DtaModelV22):
    schema_version: Literal["dta-v22.provider-synthetic-transition-result.v1"]
    transition_id: str = Field(pattern=r"^dta-v22-protocol-[0-9]{3}$")
    category: SyntheticTransitionCategoryV22
    arm: ControllerArmV22
    provider_turns: tuple[ProviderControllerTurnV22, ...] = Field(
        min_length=1,
        max_length=2,
    )
    first_pass_accepted: StrictBool
    post_correction_accepted: StrictBool
    correction_used: StrictBool
    first_error_code: ControllerProtocolErrorCodeV22 | None
    invalid_dispatches: StrictInt = Field(ge=0, le=0)
    transition_sha256: Sha256V22

    @model_validator(mode="after")
    def require_transition(self) -> ProviderSyntheticTransitionResultV22:
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"transition_sha256"})
        )
        if self.transition_sha256 != expected:
            raise ValueError("Provider synthetic transition digest differs")
        return self


class ProviderProtocolSuiteTerminalV22(str, Enum):
    PROVIDER_PROTOCOL_GATE_PASS = "PROVIDER_PROTOCOL_GATE_PASS"
    BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE = (
        "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"
    )


class ProviderProtocolCapabilityReportV22(DtaModelV22):
    schema_version: Literal["dta-v22.provider-protocol-capability-report.v1"]
    execution_mode: Literal["PROVIDER_PROTOCOL_ONLY"]
    model: str
    selected_mode: ProviderOutputModeV22
    provider_probe: ProviderModeCapabilityReportV22
    controller_identity_sha256s: tuple[Sha256V22, ...] = Field(
        min_length=4,
        max_length=4,
    )
    transitions: tuple[ProviderSyntheticTransitionResultV22, ...] = Field(
        min_length=40
    )
    transition_count: StrictInt = Field(ge=40)
    first_pass_accepted_count: StrictInt = Field(ge=0)
    post_correction_accepted_count: StrictInt = Field(ge=0)
    correction_count: StrictInt = Field(ge=0)
    first_pass_protocol_acceptance: StrictFloat = Field(ge=0, le=1)
    post_correction_protocol_acceptance: StrictFloat = Field(ge=0, le=1)
    correction_rate: StrictFloat = Field(ge=0, le=1)
    invalid_dispatches: StrictInt = Field(ge=0)
    provider_calls: StrictInt = Field(ge=1)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    provider_gate_eligible: StrictBool
    terminal: ProviderProtocolSuiteTerminalV22
    report_sha256: Sha256V22

    @model_validator(mode="after")
    def require_report(self) -> ProviderProtocolCapabilityReportV22:
        expected_ids = tuple(
            f"dta-v22-protocol-{index:03d}"
            for index in range(1, len(_CANONICAL_CATEGORIES_V22) + 1)
        )
        expected_arms = tuple(
            _provider_arm_v22(index)
            for index in range(1, len(_CANONICAL_CATEGORIES_V22) + 1)
        )
        if (
            tuple(item.transition_id for item in self.transitions) != expected_ids
            or tuple(item.category for item in self.transitions)
            != _CANONICAL_CATEGORIES_V22
            or tuple(item.arm for item in self.transitions) != expected_arms
        ):
            raise ValueError("Provider protocol suite differs from canonical transition matrix")
        if (
            self.model != PRIMARY_MODEL_V22
            or self.provider_probe.model != PRIMARY_MODEL_V22
            or self.selected_mode is not self.provider_probe.selected_mode
        ):
            raise ValueError("Provider protocol suite violates model or mode continuity")
        expected_identities = tuple(
            item.identity_sha256
            for item in build_controller_identity_manifests_v22(
                provider_probe=self.provider_probe
            )
        )
        if self.controller_identity_sha256s != expected_identities:
            raise ValueError("Provider protocol suite controller identities differ")
        for index, transition in enumerate(self.transitions, start=1):
            expected_metrics = _evaluate_provider_transition_v22(
                ordinal=index,
                category=transition.category,
                arm=transition.arm,
                provider_turns=transition.provider_turns,
            )
            actual_metrics = (
                transition.first_pass_accepted,
                transition.post_correction_accepted,
                transition.correction_used,
                transition.first_error_code,
                transition.invalid_dispatches,
            )
            if actual_metrics != expected_metrics:
                raise ValueError("Provider transition differs from runtime replay")
            if any(
                turn.mode is not self.selected_mode
                for turn in transition.provider_turns
            ):
                raise ValueError("Provider transition differs from selected Provider mode")
        turns = tuple(
            turn
            for transition in self.transitions
            for turn in transition.provider_turns
        )
        if (
            len({turn.raw_response_sha256 for turn in turns}) != len(turns)
            or len({turn.turn_sha256 for turn in turns}) != len(turns)
        ):
            raise ValueError("Provider protocol suite call evidence is not unique")
        count = len(self.transitions)
        first = sum(item.first_pass_accepted for item in self.transitions)
        post = sum(item.post_correction_accepted for item in self.transitions)
        corrections = sum(item.correction_used for item in self.transitions)
        invalid = sum(item.invalid_dispatches for item in self.transitions)
        first_rate = first / count
        post_rate = post / count
        correction_rate = corrections / count
        gate_pass = first_rate >= 0.95 and post_rate >= 0.98 and invalid == 0
        expected_terminal = (
            ProviderProtocolSuiteTerminalV22.PROVIDER_PROTOCOL_GATE_PASS
            if gate_pass
            else ProviderProtocolSuiteTerminalV22.BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE
        )
        if (
            self.transition_count != count
            or self.first_pass_accepted_count != first
            or self.post_correction_accepted_count != post
            or self.correction_count != corrections
            or self.first_pass_protocol_acceptance != first_rate
            or self.post_correction_protocol_acceptance != post_rate
            or self.correction_rate != correction_rate
            or self.invalid_dispatches != invalid
            or self.provider_calls != len(turns)
            or self.input_tokens != sum(turn.input_tokens for turn in turns)
            or self.output_tokens != sum(turn.output_tokens for turn in turns)
            or self.total_tokens != sum(turn.total_tokens for turn in turns)
            or self.provider_gate_eligible != gate_pass
            or self.terminal is not expected_terminal
        ):
            raise ValueError("Provider protocol suite gate metrics differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("Provider protocol suite report digest differs")
        return self


class ProviderCompleteCallableV22(Protocol):
    def __call__(
        self,
        *,
        mode: ProviderOutputModeV22,
        visible_state: Mapping[str, object],
    ) -> ProviderControllerTurnV22: ...


def _provider_arm_v22(ordinal: int) -> ControllerArmV22:
    return (
        ControllerArmV22.FLAT_CANONICAL
        if ordinal % 2 == 1
        else ControllerArmV22.PLANNER_LITE
    )


def _configuration_hypothesis_v22() -> str:
    return "h:payment:configuration-error"


def _abstain_decision_v22() -> ControllerDecisionV22:
    return ControllerDecisionV22(
        decision=ControllerDecisionKindV22.ABSTAIN,
        working_hypothesis_id=ABSTAIN_HYPOTHESIS_ID_V22,
        action_id=NO_ACTION_ID_V22,
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
    )


def _provider_transition_setup_v22(
    *,
    ordinal: int,
    category: SyntheticTransitionCategoryV22,
    arm: ControllerArmV22,
    hypotheses: HypothesisCatalogV22,
) -> tuple[
    ControllerSessionStateV22,
    ActionCatalogV22,
    tuple[str, ...],
    tuple[ControllerDecisionV22, ...],
]:
    session = initialize_controller_session_v22(
        arm=arm,
        hypothesis_catalog=hypotheses,
    )
    actions = _actions_v22()
    known_refs = (_KNOWN_REF_V22,)
    required: tuple[ControllerDecisionV22, ...]
    if category in {
        SyntheticTransitionCategoryV22.VALID_READ,
        SyntheticTransitionCategoryV22.EMPTY_SOURCE,
    }:
        action = actions.actions[ordinal % len(actions.actions)]
        required = (
            ControllerDecisionV22(
                decision=ControllerDecisionKindV22.READ,
                working_hypothesis_id=_configuration_hypothesis_v22(),
                action_id=action.action_id,
                supporting_evidence_refs=(),
                contradicting_evidence_refs=(),
            ),
        )
    elif category is SyntheticTransitionCategoryV22.VALID_COMMIT:
        required = (_terminal_decision_v22(category),)
    elif category is SyntheticTransitionCategoryV22.VALID_NO_INCIDENT:
        required = (_terminal_decision_v22(category),)
    elif category in {
        SyntheticTransitionCategoryV22.VALID_ABSTAIN,
        SyntheticTransitionCategoryV22.BUDGET_EXHAUSTION,
        SyntheticTransitionCategoryV22.UNAVAILABLE_SOURCE,
    }:
        if category is SyntheticTransitionCategoryV22.BUDGET_EXHAUSTION:
            for _ in range(3):
                action = next(
                    item for item in actions.actions if item.weighted_cost == 1.0
                )
                result = process_controller_decision_v22(
                    session=session,
                    raw_decision=ControllerDecisionV22(
                        decision=ControllerDecisionKindV22.READ,
                        working_hypothesis_id=_configuration_hypothesis_v22(),
                        action_id=action.action_id,
                        supporting_evidence_refs=(),
                        contradicting_evidence_refs=(),
                    ),
                    hypothesis_catalog=hypotheses,
                    action_catalog=actions,
                    known_evidence_refs=known_refs,
                )
                if result.disposition is not ControllerProtocolDispositionV22.ACCEPTED:
                    raise AssertionError("Provider budget setup READ was not accepted")
                session = result.session
                actions = _actions_v22(
                    executed=session.ledger.executed_action_ids,
                    remaining_budget=3.0 - session.ledger.weighted_evidence_cost,
                )
        elif category is SyntheticTransitionCategoryV22.UNAVAILABLE_SOURCE:
            actions = _actions_v22(disabled_sources=(EvidenceSourceV22.LOGS,))
        required = (_abstain_decision_v22(),)
    elif category is SyntheticTransitionCategoryV22.STALE_ACTION_CORRECTION:
        selected = actions.actions[0]
        stale = ControllerDecisionV22(
            decision=ControllerDecisionKindV22.READ,
            working_hypothesis_id=_configuration_hypothesis_v22(),
            action_id=selected.action_id,
            supporting_evidence_refs=(),
            contradicting_evidence_refs=(),
        )
        accepted = process_controller_decision_v22(
            session=session,
            raw_decision=stale,
            hypothesis_catalog=hypotheses,
            action_catalog=actions,
            known_evidence_refs=known_refs,
        )
        session = accepted.session
        actions = _actions_v22(
            executed=session.ledger.executed_action_ids,
            remaining_budget=3.0 - session.ledger.weighted_evidence_cost,
        )
        required = (stale, _abstain_decision_v22())
    else:
        invalid = ControllerDecisionV22(
            decision=ControllerDecisionKindV22.COMMIT,
            working_hypothesis_id=_configuration_hypothesis_v22(),
            action_id=NO_ACTION_ID_V22,
            supporting_evidence_refs=("e:a:logs:payment:0:222222222222",),
            contradicting_evidence_refs=(),
        )
        required = (invalid, _abstain_decision_v22())
    return session, actions, known_refs, required


def _evaluate_provider_transition_v22(
    *,
    ordinal: int,
    category: SyntheticTransitionCategoryV22,
    arm: ControllerArmV22,
    provider_turns: tuple[ProviderControllerTurnV22, ...],
) -> tuple[bool, bool, bool, ControllerProtocolErrorCodeV22 | None, int]:
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=("checkout", "payment")
    )
    session, actions, known_refs, required = _provider_transition_setup_v22(
        ordinal=ordinal,
        category=category,
        arm=arm,
        hypotheses=hypotheses,
    )
    first_result = process_controller_decision_v22(
        session=session,
        raw_decision=provider_turns[0].decision,
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        known_evidence_refs=known_refs,
    )
    deliberate_correction = len(required) == 2
    first_expected = provider_turns[0].decision == required[0]
    first_accepted = (
        first_expected
        and first_result.disposition is ControllerProtocolDispositionV22.ACCEPTED
    )
    correction_used = (
        first_result.disposition
        is ControllerProtocolDispositionV22.CORRECTION_REQUIRED
        and len(provider_turns) == 2
    )
    if correction_used:
        corrected_expected = required[1] if deliberate_correction else required[0]
        corrected = process_controller_decision_v22(
            session=first_result.session,
            raw_decision=provider_turns[1].decision,
            hypothesis_catalog=hypotheses,
            action_catalog=actions,
            known_evidence_refs=known_refs,
        )
        post_accepted = (
            provider_turns[1].decision == corrected_expected
            and corrected.disposition is ControllerProtocolDispositionV22.ACCEPTED
        )
        invalid = first_result.invalid_dispatches + corrected.invalid_dispatches
    else:
        post_accepted = first_accepted
        invalid = first_result.invalid_dispatches
    return (
        first_accepted,
        post_accepted,
        correction_used,
        first_result.error_code,
        invalid,
    )


def _provider_visible_state_v22(
    *,
    ordinal: int,
    category: SyntheticTransitionCategoryV22,
    arm: ControllerArmV22,
    session: ControllerSessionStateV22,
    actions: ActionCatalogV22,
    hypotheses: HypothesisCatalogV22,
    known_refs: tuple[str, ...],
    required_decision: ControllerDecisionV22,
    correction: object | None,
    correction_ordinal: int,
) -> dict[str, object]:
    return {
        "schema_version": "dta-v22.provider-protocol-visible-state.v1",
        "transition_id": f"dta-v22-protocol-{ordinal:03d}",
        "transition_category": category.value,
        "controller_arm": arm.value,
        "correction_ordinal": correction_ordinal,
        "protocol_task": (
            "Copy required_decision exactly into ControllerDecisionV22 output."
        ),
        "required_decision": required_decision.model_dump(mode="json"),
        "current_state": {
            "hypothesis_ids": [
                item.hypothesis_id for item in hypotheses.hypotheses
            ],
            "valid_action_ids": [item.action_id for item in actions.actions],
            "previously_executed_action_ids": list(
                session.ledger.executed_action_ids
            ),
            "known_evidence_refs": list(known_refs),
            "remaining_evidence_budget": actions.remaining_budget,
            "remaining_provider_turns": 5 - session.provider_turns_used,
        },
        "plan_correction": correction,
    }


def _provider_transition_result_v22(
    *,
    ordinal: int,
    category: SyntheticTransitionCategoryV22,
    arm: ControllerArmV22,
    provider_turns: tuple[ProviderControllerTurnV22, ...],
) -> ProviderSyntheticTransitionResultV22:
    metrics = _evaluate_provider_transition_v22(
        ordinal=ordinal,
        category=category,
        arm=arm,
        provider_turns=provider_turns,
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.provider-synthetic-transition-result.v1",
        "transition_id": f"dta-v22-protocol-{ordinal:03d}",
        "category": category,
        "arm": arm,
        "provider_turns": provider_turns,
        "first_pass_accepted": metrics[0],
        "post_correction_accepted": metrics[1],
        "correction_used": metrics[2],
        "first_error_code": metrics[3],
        "invalid_dispatches": metrics[4],
    }
    draft = ProviderSyntheticTransitionResultV22.model_construct(
        **payload,
        transition_sha256="0" * 64,
    )
    return ProviderSyntheticTransitionResultV22.model_validate(
        {
            **payload,
            "transition_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"transition_sha256"})
            ),
        }
    )


def run_provider_protocol_capability_suite_v22(
    *,
    provider_probe: ProviderModeCapabilityReportV22,
    complete: ProviderCompleteCallableV22,
) -> ProviderProtocolCapabilityReportV22:
    probe = ProviderModeCapabilityReportV22.model_validate(
        provider_probe.model_dump(mode="python")
    )
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=("checkout", "payment")
    )
    transitions: list[ProviderSyntheticTransitionResultV22] = []
    for ordinal, category in enumerate(_CANONICAL_CATEGORIES_V22, start=1):
        arm = _provider_arm_v22(ordinal)
        session, actions, known_refs, required = _provider_transition_setup_v22(
            ordinal=ordinal,
            category=category,
            arm=arm,
            hypotheses=hypotheses,
        )
        first_turn = complete(
            mode=probe.selected_mode,
            visible_state=_provider_visible_state_v22(
                ordinal=ordinal,
                category=category,
                arm=arm,
                session=session,
                actions=actions,
                hypotheses=hypotheses,
                known_refs=known_refs,
                required_decision=required[0],
                correction=None,
                correction_ordinal=0,
            ),
        )
        provider_turns = [first_turn]
        first_result = process_controller_decision_v22(
            session=session,
            raw_decision=first_turn.decision,
            hypothesis_catalog=hypotheses,
            action_catalog=actions,
            known_evidence_refs=known_refs,
        )
        if (
            first_result.disposition
            is ControllerProtocolDispositionV22.CORRECTION_REQUIRED
        ):
            if first_result.correction is None:
                raise AssertionError("Provider correction result is missing its contract")
            corrected_required = required[1] if len(required) == 2 else required[0]
            provider_turns.append(
                complete(
                    mode=probe.selected_mode,
                    visible_state=_provider_visible_state_v22(
                        ordinal=ordinal,
                        category=category,
                        arm=arm,
                        session=first_result.session,
                        actions=actions,
                        hypotheses=hypotheses,
                        known_refs=known_refs,
                        required_decision=corrected_required,
                        correction=first_result.correction.model_dump(mode="json"),
                        correction_ordinal=1,
                    ),
                )
            )
        transitions.append(
            _provider_transition_result_v22(
                ordinal=ordinal,
                category=category,
                arm=arm,
                provider_turns=tuple(provider_turns),
            )
        )
    transition_tuple = tuple(transitions)
    turns = tuple(
        turn for transition in transition_tuple for turn in transition.provider_turns
    )
    count = len(transition_tuple)
    first = sum(item.first_pass_accepted for item in transition_tuple)
    post = sum(item.post_correction_accepted for item in transition_tuple)
    corrections = sum(item.correction_used for item in transition_tuple)
    invalid = sum(item.invalid_dispatches for item in transition_tuple)
    gate_pass = first / count >= 0.95 and post / count >= 0.98 and invalid == 0
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.provider-protocol-capability-report.v1",
        "execution_mode": "PROVIDER_PROTOCOL_ONLY",
        "model": PRIMARY_MODEL_V22,
        "selected_mode": probe.selected_mode,
        "provider_probe": probe,
        "controller_identity_sha256s": tuple(
            item.identity_sha256
            for item in build_controller_identity_manifests_v22(
                provider_probe=probe
            )
        ),
        "transitions": transition_tuple,
        "transition_count": count,
        "first_pass_accepted_count": first,
        "post_correction_accepted_count": post,
        "correction_count": corrections,
        "first_pass_protocol_acceptance": first / count,
        "post_correction_protocol_acceptance": post / count,
        "correction_rate": corrections / count,
        "invalid_dispatches": invalid,
        "provider_calls": len(turns),
        "input_tokens": sum(turn.input_tokens for turn in turns),
        "output_tokens": sum(turn.output_tokens for turn in turns),
        "total_tokens": sum(turn.total_tokens for turn in turns),
        "provider_gate_eligible": gate_pass,
        "terminal": (
            ProviderProtocolSuiteTerminalV22.PROVIDER_PROTOCOL_GATE_PASS
            if gate_pass
            else ProviderProtocolSuiteTerminalV22.BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE
        ),
    }
    draft = ProviderProtocolCapabilityReportV22.model_construct(
        **payload,
        report_sha256="0" * 64,
    )
    return ProviderProtocolCapabilityReportV22.model_validate(
        {
            **payload,
            "report_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )


__all__ = (
    "ProtocolCapabilitySuiteReportV22",
    "ProtocolSuiteTerminalV22",
    "ProviderProtocolCapabilityReportV22",
    "ProviderProtocolSuiteTerminalV22",
    "ProviderSyntheticTransitionResultV22",
    "SyntheticTransitionCategoryV22",
    "SyntheticTransitionResultV22",
    "run_local_protocol_capability_suite_v22",
    "run_provider_protocol_capability_suite_v22",
)
