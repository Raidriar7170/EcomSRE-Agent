"""Answer-free typed 50-transition protocol capability suite for DTA v2.2."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.tool_contracts import (
    EndpointState,
    HealthState,
    ObservationStatus,
    ReadToolObservation,
    RuntimeRecord,
    RuntimeState as RuntimeStateV2,
    ToolCounters,
    build_fake_read_authority,
    build_inspect_service_runtime_request,
    build_read_tool_observation,
)
from ecomsre.dta_v2.v22.action_catalog import (
    ActionCatalogV22,
    EvidenceActionV22,
    StaticTopologyV22,
    ToolCapabilityRegistryV22,
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
    build_belief_ledger_view_v22,
    build_hypothesis_catalog_v22,
)
from ecomsre.dta_v2.v22.controller_inputs import (
    ControllerArmV22,
    ControllerRuntimeContextV22,
    ControllerTurnInputV22,
    TriageSnapshotV22,
    build_common_triage_snapshot_v22,
    build_controller_turn_input_v22,
)
from ecomsre.dta_v2.v22.controller_modes import (
    PRIMARY_MODEL_V22,
    ControllerIdentityManifestV22,
    EvaluationArmV22,
    ProviderModeCapabilityReportV22,
    ProviderOutputModeV22,
    build_controller_identity_manifests_v22,
)
from ecomsre.dta_v2.v22.controller_provider import (
    ProviderControllerTurnV22,
    ProviderTurnRequestV22,
    build_provider_turn_request_v22,
)
from ecomsre.dta_v2.v22.controller_runtime import (
    ControllerProtocolDispositionV22,
    ControllerSessionStateV22,
    ControllerSessionTerminalV22,
    PlanCorrectionV22,
    initialize_controller_session_v22,
    process_controller_decision_v22,
    record_controller_read_dispatch_v22,
    record_controller_read_outcome_v22,
)
from ecomsre.dta_v2.v22.diagnosis import DiagnosisTerminalV22
from ecomsre.dta_v2.v22.memory import (
    BaselineProfileV22,
    FullEvidenceMemoryV22,
    MemoryReadOutcomeV22,
    RuntimeReadOutcomeV22,
    SalientEvidenceMemoryV22,
    build_memory_views_v22,
)
from ecomsre.dta_v2.v22.predicates import (
    build_default_evidence_support_policy_v22,
    evaluate_support_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    ChangeCategoryV22,
    DtaModelV22,
    EvidenceSourceV22,
    LogRecordV22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    MetricUnitV22,
    RecentChangeRecordV22,
    ReadRecordV22,
    ReadSourceStatusV22,
    RolloutStateV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    Sha256V22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22


_NOW_V22 = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)


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


def _arm_v22(ordinal: int) -> ControllerArmV22:
    return (
        ControllerArmV22.FLAT_CANONICAL
        if ordinal % 2 == 1
        else ControllerArmV22.PLANNER_LITE
    )


def _evaluation_arm_v22(arm: ControllerArmV22) -> EvaluationArmV22:
    return (
        EvaluationArmV22.FLAT_CANONICAL_SALIENT
        if arm is ControllerArmV22.FLAT_CANONICAL
        else EvaluationArmV22.PLANNER_LITE_SALIENT
    )


def _topology_v22() -> StaticTopologyV22:
    return StaticTopologyV22.build(
        services=("checkout", "payment"),
        edges=(("checkout", "payment"),),
    )


def _registry_v22(
    *, disabled_sources: tuple[EvidenceSourceV22, ...] = ()
) -> ToolCapabilityRegistryV22:
    return (
        build_default_tool_capability_registry_v22()
        if not disabled_sources
        else build_tool_capability_registry_v22(disabled_sources=disabled_sources)
    )


def _actions_v22(
    *,
    executed: tuple[str, ...] = (),
    remaining_budget: float = 3.0,
    disabled_sources: tuple[EvidenceSourceV22, ...] = (),
) -> ActionCatalogV22:
    return build_action_catalog_v22(
        candidate_services=("checkout", "payment"),
        topology=_topology_v22(),
        capability_registry=_registry_v22(disabled_sources=disabled_sources),
        executed_action_ids=executed,
        remaining_budget=remaining_budget,
    )


def _action_v22(
    catalog: ActionCatalogV22,
    *,
    source: EvidenceSourceV22,
    targets: tuple[str, ...],
) -> EvidenceActionV22:
    return next(
        item
        for item in catalog.registry_actions
        if item.source is source and item.target_services == targets
    )


def _metric_v22(
    service: str,
    kind: MetricKindV22,
    *,
    value: float,
) -> MetricFactV22:
    unit = {
        MetricKindV22.ERROR_RATE: MetricUnitV22.RATIO,
        MetricKindV22.LATENCY_P95_MS: MetricUnitV22.MILLISECONDS,
        MetricKindV22.REQUEST_SUPPORT: MetricUnitV22.COUNT,
    }[kind]
    return MetricFactV22(
        schema_version="dta-v22.metric-fact.v1",
        service=service,
        metric_kind=kind,
        support_status=MetricSupportStatusV22.SUPPORTED,
        sample_count=20,
        value=value,
        unit=unit,
        window_started_at=_NOW_V22 - timedelta(seconds=300),
        window_ended_at=_NOW_V22,
    )


def _outcome_v22(
    *,
    action: EvidenceActionV22,
    status: ReadSourceStatusV22,
    records: tuple[ReadRecordV22, ...],
) -> ReadOutcomeV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.read-outcome.v1",
        "action_id": action.action_id,
        "source": action.source,
        "request_sha256": action.request_sha256,
        "status": status,
        "records": records,
        "truncated": False,
    }
    draft = ReadOutcomeV22.model_construct(**payload, outcome_sha256="0" * 64)
    return ReadOutcomeV22.model_validate(
        {
            **payload,
            "outcome_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"outcome_sha256"})
            ),
        }
    )


def _runtime_source_observation_v22(
    *, action: EvidenceActionV22, records: tuple[RuntimeRecordV22, ...]
) -> ReadToolObservation:
    source_records = tuple(
        RuntimeRecord(
            logical_service=record.service,
            owned_container_present=True,
            state=RuntimeStateV2.RUNNING,
            health=HealthState.HEALTHY,
            restart_count=record.restart_count,
            exit_code=0,
            endpoint_probe_performed=True,
            endpoint_state=EndpointState.READY,
        )
        for record in records
    )
    request = build_inspect_service_runtime_request(
        run_id="1" * 32,
        services=action.target_services,
        max_results=len(action.target_services),
    )
    return build_read_tool_observation(
        request=request,
        authority=build_fake_read_authority(),
        duplicate_of_request_sha256=None,
        status=ObservationStatus.SUCCESS,
        error_code=None,
        results=source_records,
        truncated=False,
        observed_at_start=_NOW_V22,
        observed_at_end=_NOW_V22,
        monotonic_latency_ms=0,
        counters=ToolCounters(
            dispatch_ordinal=1,
            backend_call_count=1,
            success_count=1,
            failure_count=0,
        ),
    )


def _runtime_outcome_v22(
    *, action: EvidenceActionV22, records: tuple[RuntimeRecordV22, ...]
) -> RuntimeReadOutcomeV22:
    source = _outcome_v22(
        action=action,
        status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
        records=records,
    )
    return RuntimeReadOutcomeV22.from_pr_b(
        action=action,
        source_outcome=source,
        source_observation=_runtime_source_observation_v22(
            action=action,
            records=records,
        ),
    )


def _baseline_v22() -> BaselineProfileV22:
    return BaselineProfileV22.build(
        metric_stats=tuple(
            (service, kind, mean, deviation)
            for service in ("checkout", "payment")
            for kind, mean, deviation in (
                (MetricKindV22.ERROR_RATE, 0.01, 0.005),
                (MetricKindV22.LATENCY_P95_MS, 100.0, 10.0),
                (MetricKindV22.REQUEST_SUPPORT, 1000.0, 100.0),
            )
        ),
        trace_stats=(),
        resource_stats=(),
    )


def _memory_v22(
    *,
    anomaly: bool,
    configuration_support: bool = False,
    log_status: ReadSourceStatusV22 | None = None,
) -> tuple[SalientEvidenceMemoryV22, FullEvidenceMemoryV22]:
    catalog = _actions_v22()
    runtime = _action_v22(
        catalog,
        source=EvidenceSourceV22.RUNTIME,
        targets=("checkout", "payment"),
    )
    outcomes: list[MemoryReadOutcomeV22] = [
        _runtime_outcome_v22(
            action=runtime,
            records=tuple(
                RuntimeRecordV22(
                    schema_version="dta-v22.runtime-record.v1",
                    service=service,
                    state=RuntimeStateV22.RUNNING,
                    healthy=True,
                    restart_count=0,
                )
                for service in ("checkout", "payment")
            ),
        )
    ]
    for service in ("checkout", "payment"):
        action = _action_v22(
            catalog,
            source=EvidenceSourceV22.METRICS,
            targets=(service,),
        )
        outcomes.append(
            _outcome_v22(
                action=action,
                status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
                records=(
                    _metric_v22(
                        service,
                        MetricKindV22.ERROR_RATE,
                        value=0.20 if anomaly and service == "payment" else 0.01,
                    ),
                    _metric_v22(
                        service,
                        MetricKindV22.LATENCY_P95_MS,
                        value=100.0,
                    ),
                    _metric_v22(
                        service,
                        MetricKindV22.REQUEST_SUPPORT,
                        value=1000.0,
                    ),
                ),
            )
        )
    if configuration_support:
        log_action = _action_v22(
            catalog,
            source=EvidenceSourceV22.LOGS,
            targets=("payment",),
        )
        outcomes.append(
            _outcome_v22(
                action=log_action,
                status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
                records=(
                    LogRecordV22(
                        schema_version="dta-v22.log-record.v1",
                        observed_at=_NOW_V22,
                        service="payment",
                        severity="ERROR",
                        message="invalid config revision 482 downstream=checkout",
                    ),
                ),
            )
        )
        change_action = _action_v22(
            catalog,
            source=EvidenceSourceV22.CHANGES,
            targets=("payment",),
        )
        outcomes.append(
            _outcome_v22(
                action=change_action,
                status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
                records=(
                    RecentChangeRecordV22(
                        schema_version="dta-v22.recent-change-record.v1",
                        opaque_change_id="chg_0123456789abcdef",
                        service="payment",
                        observed_at=_NOW_V22 - timedelta(seconds=120),
                        category=ChangeCategoryV22.CONFIGURATION,
                        rollout_state=RolloutStateV22.COMPLETED,
                        revision_digest="2" * 64,
                    ),
                ),
            )
        )
    if log_status is not None:
        log_action = _action_v22(
            catalog,
            source=EvidenceSourceV22.LOGS,
            targets=("payment",),
        )
        outcomes.append(
            _outcome_v22(
                action=log_action,
                status=log_status,
                records=(),
            )
        )
    return build_memory_views_v22(
        outcomes=tuple(outcomes),
        baseline=_baseline_v22(),
        observed_at=_NOW_V22,
        top_k=64,
    )


def _identity_v22(
    *, probe: ProviderModeCapabilityReportV22, arm: ControllerArmV22
) -> ControllerIdentityManifestV22:
    return next(
        item
        for item in build_controller_identity_manifests_v22(provider_probe=probe)
        if item.arm is _evaluation_arm_v22(arm)
    )


def _turn_input_v22(
    *,
    ordinal: int,
    identity: ControllerIdentityManifestV22,
    session: ControllerSessionStateV22,
    bootstrap: TriageSnapshotV22,
    hypotheses: HypothesisCatalogV22,
    actions: ActionCatalogV22,
    memory: SalientEvidenceMemoryV22,
) -> ControllerTurnInputV22:
    context = ControllerRuntimeContextV22.build(
        run_id=f"{ordinal:032x}",
        turn_ordinal=session.provider_turns_used + 1,
        controller_identity_sha256=identity.identity_sha256,
        remaining_evidence_budget=(
            session.initial_evidence_budget - session.ledger.weighted_evidence_cost
        ),
        remaining_provider_turns=5 - session.provider_turns_used,
        correction_remaining=not session.ledger.correction_used,
    )
    view = (
        build_belief_ledger_view_v22(
            ledger=session.ledger,
            hypothesis_catalog=hypotheses,
        )
        if session.arm is ControllerArmV22.PLANNER_LITE
        else None
    )
    return build_controller_turn_input_v22(
        arm=session.arm,
        runtime_context=context,
        bootstrap=bootstrap,
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        salient_memory=memory,
        belief_ledger_view=view,
        evidence_support_policy=build_default_evidence_support_policy_v22(),
    )


class _TransitionSetupV22:
    def __init__(
        self,
        *,
        session: ControllerSessionStateV22,
        request: ProviderTurnRequestV22,
        injected_error: ControllerProtocolErrorCodeV22 | None,
    ) -> None:
        self.session = session
        self.request = request
        self.injected_error = injected_error


def _setup_transition_v22(
    *,
    ordinal: int,
    category: SyntheticTransitionCategoryV22,
    probe: ProviderModeCapabilityReportV22,
) -> _TransitionSetupV22:
    arm = _arm_v22(ordinal)
    identity = _identity_v22(probe=probe, arm=arm)
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=("checkout", "payment")
    )
    if category is SyntheticTransitionCategoryV22.VALID_COMMIT:
        memory, _ = _memory_v22(anomaly=True, configuration_support=True)
    elif category is SyntheticTransitionCategoryV22.VALID_NO_INCIDENT:
        memory, _ = _memory_v22(anomaly=False)
    elif category in {
        SyntheticTransitionCategoryV22.VALID_ABSTAIN,
        SyntheticTransitionCategoryV22.UNAVAILABLE_SOURCE,
    }:
        memory, _ = _memory_v22(
            anomaly=True,
            log_status=ReadSourceStatusV22.FAILURE_UNAVAILABLE,
        )
    elif category is SyntheticTransitionCategoryV22.EMPTY_SOURCE:
        memory, _ = _memory_v22(
            anomaly=True,
            log_status=ReadSourceStatusV22.SUCCESS_EMPTY,
        )
    else:
        memory, _ = _memory_v22(anomaly=True)
    initial_actions = _actions_v22()
    bootstrap = build_common_triage_snapshot_v22(
        memory=memory,
        candidate_services=("checkout", "payment"),
        topology=_topology_v22(),
        capability_registry=_registry_v22(),
    )
    session = initialize_controller_session_v22(
        identity=identity,
        hypothesis_catalog=hypotheses,
        bootstrap=bootstrap,
        support_policy_sha256=build_default_evidence_support_policy_v22().policy_sha256,
    )
    actions = initial_actions
    correction: PlanCorrectionV22 | None = None
    injected_error: ControllerProtocolErrorCodeV22 | None = None
    if category is SyntheticTransitionCategoryV22.BUDGET_EXHAUSTION:
        for source, targets in (
            (EvidenceSourceV22.LOGS, ("checkout",)),
            (EvidenceSourceV22.LOGS, ("payment",)),
            (EvidenceSourceV22.METRICS, ("checkout",)),
        ):
            selected = _action_v22(actions, source=source, targets=targets)
            read_input = _turn_input_v22(
                ordinal=ordinal,
                identity=identity,
                session=session,
                bootstrap=bootstrap,
                hypotheses=hypotheses,
                actions=actions,
                memory=memory,
            )
            read = ControllerDecisionV22(
                decision=ControllerDecisionKindV22.READ,
                working_hypothesis_id="h:payment:configuration-error",
                action_id=selected.action_id,
                supporting_evidence_refs=(),
                contradicting_evidence_refs=(),
            )
            authorized = process_controller_decision_v22(
                session=session,
                raw_decision=read,
                turn_input=read_input,
            )
            assert authorized.session.pending_read is not None
            dispatched = record_controller_read_dispatch_v22(
                session=authorized.session,
                authorization_sha256=(
                    authorized.session.pending_read.authorization_sha256
                ),
            )
            session = record_controller_read_outcome_v22(
                session=dispatched,
                turn_input=read_input,
                outcome=_outcome_v22(
                    action=selected,
                    status=ReadSourceStatusV22.SUCCESS_EMPTY,
                    records=(),
                ),
            )
            actions = _actions_v22(
                executed=session.ledger.executed_action_ids,
                remaining_budget=(
                    session.initial_evidence_budget
                    - session.ledger.weighted_evidence_cost
                ),
            )
        assert session.ledger.weighted_evidence_cost == 3.0
    elif category is SyntheticTransitionCategoryV22.STALE_ACTION_CORRECTION:
        selected = _action_v22(
            actions,
            source=EvidenceSourceV22.LOGS,
            targets=("payment",),
        )
        initial_input = _turn_input_v22(
            ordinal=ordinal,
            identity=identity,
            session=session,
            bootstrap=bootstrap,
            hypotheses=hypotheses,
            actions=actions,
            memory=memory,
        )
        read = ControllerDecisionV22(
            decision=ControllerDecisionKindV22.READ,
            working_hypothesis_id="h:payment:configuration-error",
            action_id=selected.action_id,
            supporting_evidence_refs=(),
            contradicting_evidence_refs=(),
        )
        authorized = process_controller_decision_v22(
            session=session,
            raw_decision=read,
            turn_input=initial_input,
        )
        assert authorized.session.pending_read is not None
        dispatched = record_controller_read_dispatch_v22(
            session=authorized.session,
            authorization_sha256=(
                authorized.session.pending_read.authorization_sha256
            ),
        )
        session = record_controller_read_outcome_v22(
            session=dispatched,
            turn_input=initial_input,
            outcome=_outcome_v22(
                action=selected,
                status=ReadSourceStatusV22.SUCCESS_EMPTY,
                records=(),
            ),
        )
        actions = _actions_v22(
            executed=session.ledger.executed_action_ids,
            remaining_budget=3.0 - session.ledger.weighted_evidence_cost,
        )
        stale_input = _turn_input_v22(
            ordinal=ordinal,
            identity=identity,
            session=session,
            bootstrap=bootstrap,
            hypotheses=hypotheses,
            actions=actions,
            memory=memory,
        )
        correction_result = process_controller_decision_v22(
            session=session,
            raw_decision=read,
            turn_input=stale_input,
        )
        assert correction_result.correction is not None
        session = correction_result.session
        correction = correction_result.correction
        injected_error = correction_result.error_code
    elif category is SyntheticTransitionCategoryV22.INVALID_REF_CORRECTION:
        invalid_input = _turn_input_v22(
            ordinal=ordinal,
            identity=identity,
            session=session,
            bootstrap=bootstrap,
            hypotheses=hypotheses,
            actions=actions,
            memory=memory,
        )
        correction_result = process_controller_decision_v22(
            session=session,
            raw_decision={
                "decision": "COMMIT",
                "working_hypothesis_id": "h:payment:configuration-error",
                "action_id": "NONE",
                "supporting_evidence_refs": [
                    "e:a:logs:payment:0:222222222222"
                ],
                "contradicting_evidence_refs": [],
            },
            turn_input=invalid_input,
        )
        assert correction_result.correction is not None
        session = correction_result.session
        correction = correction_result.correction
        injected_error = correction_result.error_code
    controller_input = _turn_input_v22(
        ordinal=ordinal,
        identity=identity,
        session=session,
        bootstrap=bootstrap,
        hypotheses=hypotheses,
        actions=actions,
        memory=memory,
    )
    request = build_provider_turn_request_v22(
        execution_mode="PROTOCOL_ONLY",
        identity=identity,
        controller_input=controller_input,
        plan_correction=correction,
    )
    return _TransitionSetupV22(
        session=session,
        request=request,
        injected_error=injected_error,
    )


def _acceptable_result_v22(
    *,
    category: SyntheticTransitionCategoryV22,
    result: Any,
) -> bool:
    if result.disposition is not ControllerProtocolDispositionV22.ACCEPTED:
        return False
    decision = result.accepted_decision
    assert decision is not None
    if category in {
        SyntheticTransitionCategoryV22.VALID_READ,
        SyntheticTransitionCategoryV22.EMPTY_SOURCE,
        SyntheticTransitionCategoryV22.STALE_ACTION_CORRECTION,
        SyntheticTransitionCategoryV22.INVALID_REF_CORRECTION,
    }:
        return (
            decision.decision is ControllerDecisionKindV22.READ
            and result.read_dispatch_authorized
            and result.session.terminal is ControllerSessionTerminalV22.ACTIVE
        )
    if result.semantic_admission is None:
        return False
    if category is SyntheticTransitionCategoryV22.VALID_COMMIT:
        return (
            decision.decision is ControllerDecisionKindV22.COMMIT
            and result.semantic_admission.terminal is DiagnosisTerminalV22.DIAGNOSED
        )
    if category is SyntheticTransitionCategoryV22.VALID_NO_INCIDENT:
        return (
            decision.decision is ControllerDecisionKindV22.NO_INCIDENT
            and result.semantic_admission.terminal is DiagnosisTerminalV22.NO_INCIDENT
        )
    return (
        decision.decision is ControllerDecisionKindV22.ABSTAIN
        and result.semantic_admission.terminal is DiagnosisTerminalV22.ABSTAIN
    )


class SyntheticTransitionResultV22(DtaModelV22):
    schema_version: Literal["dta-v22.synthetic-transition-result.v2"]
    transition_id: str = Field(pattern=r"^dta-v22-protocol-[0-9]{3}$")
    category: SyntheticTransitionCategoryV22
    arm: ControllerArmV22
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
            or self.first_pass_accepted
            != (
                False
                if correction_category
                else self.post_correction_accepted
            )
            or (self.first_error_code is not None) != correction_category
        ):
            raise ValueError("synthetic transition correction semantics differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"transition_sha256"})
        )
        if self.transition_sha256 != expected:
            raise ValueError("synthetic transition digest differs")
        return self


class ProtocolSuiteTerminalV22(str, Enum):
    LOCAL_HARNESS_PASS = "LOCAL_HARNESS_PASS"
    LOCAL_HARNESS_FAILED = "LOCAL_HARNESS_FAILED"


class ProtocolCapabilitySuiteReportV22(DtaModelV22):
    schema_version: Literal["dta-v22.protocol-capability-suite-report.v2"]
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
    provider_calls: Literal[0]
    provider_gate_eligible: Literal[False]
    terminal: ProtocolSuiteTerminalV22
    report_sha256: Sha256V22

    @model_validator(mode="after")
    def require_report(self) -> ProtocolCapabilitySuiteReportV22:
        expected_ids = tuple(
            f"dta-v22-protocol-{index:03d}"
            for index in range(1, len(_CANONICAL_CATEGORIES_V22) + 1)
        )
        if (
            tuple(item.transition_id for item in self.transitions) != expected_ids
            or tuple(item.category for item in self.transitions)
            != _CANONICAL_CATEGORIES_V22
        ):
            raise ValueError("protocol suite differs from canonical transition matrix")
        count = len(self.transitions)
        first = sum(item.first_pass_accepted for item in self.transitions)
        post = sum(item.post_correction_accepted for item in self.transitions)
        corrections = sum(item.correction_used for item in self.transitions)
        invalid = sum(item.invalid_dispatches for item in self.transitions)
        gate = first / count >= 0.95 and post / count >= 0.98 and invalid == 0
        terminal = (
            ProtocolSuiteTerminalV22.LOCAL_HARNESS_PASS
            if gate
            else ProtocolSuiteTerminalV22.LOCAL_HARNESS_FAILED
        )
        if (
            self.transition_count != count
            or self.first_pass_accepted_count != first
            or self.post_correction_accepted_count != post
            or self.correction_count != corrections
            or self.first_pass_protocol_acceptance != first / count
            or self.post_correction_protocol_acceptance != post / count
            or self.correction_rate != corrections / count
            or self.invalid_dispatches != invalid
            or self.terminal is not terminal
        ):
            raise ValueError("protocol suite gate metrics differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("protocol suite report digest differs")
        return self


def _deterministic_decision_v22(
    *, category: SyntheticTransitionCategoryV22, request: ProviderTurnRequestV22
) -> ControllerDecisionV22:
    controller_input = request.controller_input
    if category is SyntheticTransitionCategoryV22.VALID_COMMIT:
        entry = controller_input.hypothesis_catalog.require(
            "h:payment:configuration-error"
        )
        support = evaluate_support_v22(
            policy=controller_input.evidence_support_policy,
            mechanism=entry.mechanism,
            target_service="payment",
            parent_service=None,
            predicates=controller_input.salient_memory.predicates,
        ).supporting_evidence_refs
        return ControllerDecisionV22(
            decision=ControllerDecisionKindV22.COMMIT,
            working_hypothesis_id="h:payment:configuration-error",
            action_id=NO_ACTION_ID_V22,
            supporting_evidence_refs=tuple(sorted(set(support))),
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
    if category in {
        SyntheticTransitionCategoryV22.VALID_ABSTAIN,
        SyntheticTransitionCategoryV22.BUDGET_EXHAUSTION,
        SyntheticTransitionCategoryV22.UNAVAILABLE_SOURCE,
    }:
        return ControllerDecisionV22(
            decision=ControllerDecisionKindV22.ABSTAIN,
            working_hypothesis_id=ABSTAIN_HYPOTHESIS_ID_V22,
            action_id=NO_ACTION_ID_V22,
            supporting_evidence_refs=(),
            contradicting_evidence_refs=(),
        )
    action = controller_input.action_catalog.actions[0]
    return ControllerDecisionV22(
        decision=ControllerDecisionKindV22.READ,
        working_hypothesis_id="h:payment:configuration-error",
        action_id=action.action_id,
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
    )


def _transition_payload_v22(
    *,
    ordinal: int,
    category: SyntheticTransitionCategoryV22,
    first: bool,
    post: bool,
    error: ControllerProtocolErrorCodeV22 | None,
) -> dict[str, Any]:
    return {
        "schema_version": "dta-v22.synthetic-transition-result.v2",
        "transition_id": f"dta-v22-protocol-{ordinal:03d}",
        "category": category,
        "arm": _arm_v22(ordinal),
        "first_pass_accepted": first,
        "post_correction_accepted": post,
        "correction_used": error is not None,
        "first_error_code": error,
        "invalid_dispatches": 0,
    }


def run_local_protocol_capability_suite_v22(
    *, provider_probe: ProviderModeCapabilityReportV22
) -> ProtocolCapabilitySuiteReportV22:
    transitions: list[SyntheticTransitionResultV22] = []
    for ordinal, category in enumerate(_CANONICAL_CATEGORIES_V22, start=1):
        setup = _setup_transition_v22(
            ordinal=ordinal,
            category=category,
            probe=provider_probe,
        )
        decision = _deterministic_decision_v22(
            category=category,
            request=setup.request,
        )
        result = process_controller_decision_v22(
            session=setup.session,
            raw_decision=decision,
            turn_input=setup.request.controller_input,
        )
        accepted = _acceptable_result_v22(category=category, result=result)
        correction = setup.injected_error is not None
        transition_payload = _transition_payload_v22(
            ordinal=ordinal,
            category=category,
            first=False if correction else accepted,
            post=accepted,
            error=setup.injected_error,
        )
        transitions.append(
            SyntheticTransitionResultV22.model_validate(
                {
                    **transition_payload,
                    "transition_sha256": semantic_sha256_v22(transition_payload),
                }
            )
        )
    values = tuple(transitions)
    count = len(values)
    first = sum(item.first_pass_accepted for item in values)
    post = sum(item.post_correction_accepted for item in values)
    corrections = sum(item.correction_used for item in values)
    invalid = sum(item.invalid_dispatches for item in values)
    gate = first / count >= 0.95 and post / count >= 0.98 and invalid == 0
    report_payload: dict[str, Any] = {
        "schema_version": "dta-v22.protocol-capability-suite-report.v2",
        "execution_mode": "LOCAL_DETERMINISTIC_HARNESS",
        "transitions": values,
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
            if gate
            else ProtocolSuiteTerminalV22.LOCAL_HARNESS_FAILED
        ),
    }
    draft = ProtocolCapabilitySuiteReportV22.model_construct(
        **report_payload, report_sha256="0" * 64
    )
    return ProtocolCapabilitySuiteReportV22.model_validate(
        {
            **report_payload,
            "report_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )


class ProviderSyntheticTransitionResultV22(DtaModelV22):
    schema_version: Literal["dta-v22.provider-synthetic-transition-result.v2"]
    transition_id: str = Field(pattern=r"^dta-v22-protocol-[0-9]{3}$")
    category: SyntheticTransitionCategoryV22
    arm: ControllerArmV22
    session_before: ControllerSessionStateV22
    provider_request: ProviderTurnRequestV22
    provider_turn: ProviderControllerTurnV22
    first_pass_accepted: StrictBool
    post_correction_accepted: StrictBool
    correction_used: StrictBool
    first_error_code: ControllerProtocolErrorCodeV22 | None
    invalid_dispatches: StrictInt = Field(ge=0, le=0)
    transition_sha256: Sha256V22

    @model_validator(mode="after")
    def require_transition(self) -> ProviderSyntheticTransitionResultV22:
        self.provider_request.require_request()  # type: ignore[operator]
        correction = self.category in {
            SyntheticTransitionCategoryV22.STALE_ACTION_CORRECTION,
            SyntheticTransitionCategoryV22.INVALID_REF_CORRECTION,
        }
        if (
            self.provider_request.execution_mode != "PROTOCOL_ONLY"
            or self.provider_request.controller_input.arm is not self.arm
            or self.provider_turn.provider_request_sha256
            != self.provider_request.request_sha256
            or self.provider_turn.mode
            is not self.provider_request.identity.provider_output_mode
            or self.provider_turn.controller_identity_sha256
            != self.provider_request.identity.identity_sha256
            or self.provider_turn.prompt_sha256
            != self.provider_request.identity.prompt_sha256
            or self.provider_turn.visible_input_sha256
            != semantic_sha256_v22(self.provider_request.visible_state())
            or self.correction_used != correction
            or self.first_pass_accepted
            != (False if correction else self.post_correction_accepted)
            or (self.first_error_code is not None) != correction
        ):
            raise ValueError("Provider synthetic transition binding differs")
        raw: ControllerDecisionV22 | dict[str, object] = (
            self.provider_turn.decision or {}
        )
        result = process_controller_decision_v22(
            session=self.session_before,
            raw_decision=raw,
            turn_input=self.provider_request.controller_input,
        )
        accepted = _acceptable_result_v22(category=self.category, result=result)
        if self.post_correction_accepted != accepted:
            raise ValueError("Provider synthetic transition runtime replay differs")
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
    schema_version: Literal["dta-v22.provider-protocol-capability-report.v2"]
    execution_mode: Literal["PROVIDER_PROTOCOL_ONLY"]
    model: str
    selected_mode: ProviderOutputModeV22
    provider_probe: ProviderModeCapabilityReportV22
    controller_identity_sha256s: tuple[Sha256V22, ...] = Field(
        min_length=4, max_length=4
    )
    answer_free_state: Literal[True]
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
        for transition in self.transitions:
            transition.require_transition()  # type: ignore[operator]
        expected_ids = tuple(
            f"dta-v22-protocol-{index:03d}"
            for index in range(1, len(_CANONICAL_CATEGORIES_V22) + 1)
        )
        expected_arms = tuple(
            _arm_v22(index)
            for index in range(1, len(_CANONICAL_CATEGORIES_V22) + 1)
        )
        expected_identities = tuple(
            item.identity_sha256
            for item in build_controller_identity_manifests_v22(
                provider_probe=self.provider_probe
            )
        )
        if (
            tuple(item.transition_id for item in self.transitions) != expected_ids
            or tuple(item.category for item in self.transitions)
            != _CANONICAL_CATEGORIES_V22
            or tuple(item.arm for item in self.transitions) != expected_arms
            or self.model != PRIMARY_MODEL_V22
            or self.provider_probe.model != PRIMARY_MODEL_V22
            or self.selected_mode is not self.provider_probe.selected_mode
            or self.controller_identity_sha256s != expected_identities
        ):
            raise ValueError("Provider protocol suite identity or matrix differs")
        turns = tuple(item.provider_turn for item in self.transitions)
        if (
            len({turn.raw_response_sha256 for turn in turns}) != len(turns)
            or len({turn.turn_sha256 for turn in turns}) != len(turns)
            or len({item.provider_request.request_sha256 for item in self.transitions})
            != len(self.transitions)
        ):
            raise ValueError("Provider protocol suite call evidence is not unique")
        count = len(self.transitions)
        first = sum(item.first_pass_accepted for item in self.transitions)
        post = sum(item.post_correction_accepted for item in self.transitions)
        corrections = sum(item.correction_used for item in self.transitions)
        invalid = sum(item.invalid_dispatches for item in self.transitions)
        gate = first / count >= 0.95 and post / count >= 0.98 and invalid == 0
        terminal = (
            ProviderProtocolSuiteTerminalV22.PROVIDER_PROTOCOL_GATE_PASS
            if gate
            else ProviderProtocolSuiteTerminalV22.BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE
        )
        if (
            self.transition_count != count
            or self.first_pass_accepted_count != first
            or self.post_correction_accepted_count != post
            or self.correction_count != corrections
            or self.first_pass_protocol_acceptance != first / count
            or self.post_correction_protocol_acceptance != post / count
            or self.correction_rate != corrections / count
            or self.invalid_dispatches != invalid
            or self.provider_calls != len(turns)
            or self.input_tokens != sum(turn.input_tokens for turn in turns)
            or self.output_tokens != sum(turn.output_tokens for turn in turns)
            or self.total_tokens != sum(turn.total_tokens for turn in turns)
            or self.provider_gate_eligible != gate
            or self.terminal is not terminal
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
        self, *, request: ProviderTurnRequestV22
    ) -> ProviderControllerTurnV22: ...


def run_provider_protocol_capability_suite_v22(
    *,
    provider_probe: ProviderModeCapabilityReportV22,
    complete: ProviderCompleteCallableV22,
) -> ProviderProtocolCapabilityReportV22:
    probe = ProviderModeCapabilityReportV22.model_validate(
        provider_probe.model_dump(mode="python")
    )
    transitions: list[ProviderSyntheticTransitionResultV22] = []
    for ordinal, category in enumerate(_CANONICAL_CATEGORIES_V22, start=1):
        setup = _setup_transition_v22(
            ordinal=ordinal,
            category=category,
            probe=probe,
        )
        turn = complete(request=setup.request)
        result = process_controller_decision_v22(
            session=setup.session,
            raw_decision=turn.decision or {},
            turn_input=setup.request.controller_input,
        )
        accepted = _acceptable_result_v22(category=category, result=result)
        correction = setup.injected_error is not None
        transition_payload: dict[str, Any] = {
            "schema_version": "dta-v22.provider-synthetic-transition-result.v2",
            "transition_id": f"dta-v22-protocol-{ordinal:03d}",
            "category": category,
            "arm": _arm_v22(ordinal),
            "session_before": setup.session,
            "provider_request": setup.request,
            "provider_turn": turn,
            "first_pass_accepted": False if correction else accepted,
            "post_correction_accepted": accepted,
            "correction_used": correction,
            "first_error_code": setup.injected_error,
            "invalid_dispatches": result.invalid_dispatches,
        }
        transition_draft = ProviderSyntheticTransitionResultV22.model_construct(
            **transition_payload, transition_sha256="0" * 64
        )
        transitions.append(
            ProviderSyntheticTransitionResultV22.model_validate(
                {
                    **transition_payload,
                    "transition_sha256": semantic_sha256_v22(
                        transition_draft.model_dump(
                            mode="json", exclude={"transition_sha256"}
                        )
                    ),
                }
            )
        )
    values = tuple(transitions)
    turns = tuple(item.provider_turn for item in values)
    count = len(values)
    first = sum(item.first_pass_accepted for item in values)
    post = sum(item.post_correction_accepted for item in values)
    corrections = sum(item.correction_used for item in values)
    invalid = sum(item.invalid_dispatches for item in values)
    gate = first / count >= 0.95 and post / count >= 0.98 and invalid == 0
    report_payload: dict[str, Any] = {
        "schema_version": "dta-v22.provider-protocol-capability-report.v2",
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
        "answer_free_state": True,
        "transitions": values,
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
        "provider_gate_eligible": gate,
        "terminal": (
            ProviderProtocolSuiteTerminalV22.PROVIDER_PROTOCOL_GATE_PASS
            if gate
            else ProviderProtocolSuiteTerminalV22.BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE
        ),
    }
    report_draft = ProviderProtocolCapabilityReportV22.model_construct(
        **report_payload, report_sha256="0" * 64
    )
    return ProviderProtocolCapabilityReportV22.model_validate(
        {
            **report_payload,
            "report_sha256": semantic_sha256_v22(
                report_draft.model_dump(mode="json", exclude={"report_sha256"})
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
