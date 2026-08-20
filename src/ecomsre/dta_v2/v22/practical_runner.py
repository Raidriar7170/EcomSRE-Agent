"""End-to-end read-only controller execution over normalized replay cases."""

from __future__ import annotations

from enum import Enum
import hashlib
from typing import Any, Literal, Protocol, cast

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.tool_contracts import (
    EndpointState,
    HealthState,
    ObservationStatus,
    ReadToolObservation,
    RuntimeRecord,
    RuntimeState as RuntimeStateV2,
    ToolCounters,
    ToolErrorCode,
    build_fake_read_authority,
    build_inspect_service_runtime_request,
    build_read_tool_observation,
)
from ecomsre.dta_v2.v22.action_catalog import (
    ActionCatalogV22,
    EvidenceActionV22,
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.controller_contracts import (
    ControllerDecisionKindV22,
    build_belief_ledger_view_v22,
    build_hypothesis_catalog_v22,
)
from ecomsre.dta_v2.v22.controller_inputs import (
    ControllerArmV22,
    ControllerRuntimeContextV22,
    ControllerTurnInputV22,
    build_common_triage_snapshot_v22,
    build_controller_turn_input_v22,
)
from ecomsre.dta_v2.v22.controller_runtime import (
    ControllerProtocolDispositionV22,
    ControllerSessionStateV22,
    ControllerSessionTerminalV22,
    initialize_controller_session_v22,
    process_controller_decision_v22,
    record_controller_read_dispatch_v22,
    record_controller_read_outcome_v22,
)
from ecomsre.dta_v2.v22.diagnosis import DiagnosisTerminalV22
from ecomsre.dta_v2.v22.evidence_acquisition_v221 import (
    TerminalExplorationDispositionV221,
    TerminalExplorationPolicyV221,
    evaluate_terminal_exploration_policy_v221,
)
from ecomsre.dta_v2.v22.memory import (
    BaselineProfileV22,
    MemoryReadOutcomeV22,
    RuntimeReadOutcomeV22,
    build_memory_views_v22,
)
from ecomsre.dta_v2.v22.predicates import (
    build_default_evidence_support_policy_v22,
)
from ecomsre.dta_v2.v22.practical_replay import NormalizedPracticalCaseV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    MetricKindV22,
    ReadSourceStatusV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import (
    QuerySpecificReplayBackendV22,
    ReadOutcomeV22,
)
from ecomsre.dta_v2.v22.simple_provider import (
    ProviderProtocolFailureV22,
    ProviderTurnOutcomeV22,
    SHARED_SYSTEM_PROMPT_V22,
    SHARED_SYSTEM_PROMPT_V221,
)


class PracticalRunStatusV22(str, Enum):
    VALID_TERMINAL = "VALID_TERMINAL"
    PROTOCOL_FAILED = "PROTOCOL_FAILED"
    TRANSPORT_FAILED = "TRANSPORT_FAILED"
    RUNNER_EXCEPTION = "RUNNER_EXCEPTION"


class PracticalProviderV22(Protocol):
    def complete_turn(
        self,
        *,
        turn_input: ControllerTurnInputV22,
        run_id: str,
        system_prompt: str,
        allow_semantic_repair: bool,
    ) -> ProviderTurnOutcomeV22: ...

    def complete_repair_turn(
        self,
        *,
        turn_input: ControllerTurnInputV22,
        run_id: str,
        safe_error_code: str,
        system_prompt: str,
    ) -> ProviderTurnOutcomeV22: ...


class PracticalProviderV221(PracticalProviderV22, Protocol):
    def complete_turn_v221(
        self,
        *,
        turn_input: ControllerTurnInputV22,
        run_id: str,
        system_prompt: str,
        allow_semantic_repair: bool,
        terminal_exploration_policy: TerminalExplorationPolicyV221,
        adaptive_reads_so_far: int,
        policy_redirect_remaining: bool,
    ) -> ProviderTurnOutcomeV22: ...

    def complete_policy_redirect_turn_v221(
        self,
        *,
        turn_input: ControllerTurnInputV22,
        run_id: str,
        safe_error_code: str,
        system_prompt: str,
        terminal_exploration_policy: TerminalExplorationPolicyV221,
        adaptive_reads_so_far: int,
        policy_redirect_remaining: bool,
    ) -> ProviderTurnOutcomeV22: ...

    def complete_repair_turn_v221(
        self,
        *,
        turn_input: ControllerTurnInputV22,
        run_id: str,
        safe_error_code: str,
        system_prompt: str,
        terminal_exploration_policy: TerminalExplorationPolicyV221,
        adaptive_reads_so_far: int,
        policy_redirect_remaining: bool,
    ) -> ProviderTurnOutcomeV22: ...

class PracticalCaseRunV22(DtaModelV22):
    schema_version: str = Field(pattern=r"^dta-v22\.practical-case-run\.v1$")
    case_id: str
    arm: ControllerArmV22
    case_bytes_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: PracticalRunStatusV22
    terminal: str | None
    root_service: str | None
    mechanism: str | None
    supporting_evidence_refs: tuple[str, ...]
    evidence_ref_valid: StrictBool
    semantic_clause_valid: StrictBool
    adaptive_reads: StrictInt = Field(ge=0, le=3)
    duplicate_read_attempts: StrictInt = Field(ge=0)
    provider_turns: StrictInt = Field(ge=0, le=5)
    provider_calls: StrictInt = Field(ge=0)
    first_pass_protocol_successes: StrictInt = Field(ge=0)
    post_repair_protocol_successes: StrictInt = Field(ge=0)
    semantic_repairs: StrictInt = Field(ge=0, le=1)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    latency_ms: StrictFloat = Field(ge=0)
    transport_retry_count: StrictInt = Field(ge=0)
    uncaught_exceptions: StrictInt = Field(ge=0, le=1)
    safe_error_code: str | None
    agent_writes: StrictInt = Field(ge=0, le=0)
    planner_ledger_visible: StrictBool


class PracticalAdaptiveReadEventV221(DtaModelV22):
    schema_version: Literal["dta-v22.1.practical-adaptive-read-event.v1"]
    ordinal: StrictInt = Field(ge=1, le=3)
    action_id: str
    source: EvidenceSourceV22
    status: ReadSourceStatusV22


class PracticalCaseRunV221(PracticalCaseRunV22):
    schema_version: Literal["dta-v22.1.practical-case-run.v1"]
    normalized_case_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    terminal_exploration_policy: TerminalExplorationPolicyV221
    policy_redirects: StrictInt = Field(ge=0, le=1)
    premature_abstention_proposals: StrictInt = Field(ge=0, le=2)
    repeated_premature_abstentions: StrictInt = Field(ge=0, le=1)
    redirect_response_kind: ControllerDecisionKindV22 | None
    logical_decision_attempts: StrictInt = Field(ge=0)
    adaptive_read_events: tuple[PracticalAdaptiveReadEventV221, ...] = Field(
        max_length=3
    )

    @model_validator(mode="after")
    def require_policy_accounting(self) -> "PracticalCaseRunV221":
        if (
            self.premature_abstention_proposals
            != self.policy_redirects + self.repeated_premature_abstentions
            or self.repeated_premature_abstentions > self.policy_redirects
            or len(self.adaptive_read_events) != self.adaptive_reads
        ):
            raise ValueError("v2.2.1 policy accounting differs")
        if self.terminal_exploration_policy is TerminalExplorationPolicyV221.LEGACY and (
            self.policy_redirects
            or self.premature_abstention_proposals
            or self.repeated_premature_abstentions
            or self.redirect_response_kind is not None
        ):
            raise ValueError("Legacy terminal policy cannot carry redirect state")
        return self


def _baseline(case: NormalizedPracticalCaseV22) -> BaselineProfileV22:
    return BaselineProfileV22.build(
        metric_stats=tuple(
            (
                service,
                kind,
                0.01
                if kind is MetricKindV22.ERROR_RATE
                else 10.0
                if kind is MetricKindV22.LATENCY_P95_MS
                else 100.0,
                0.01 if kind is MetricKindV22.ERROR_RATE else 1.0,
            )
            for service in case.candidate_services
            for kind in (
                MetricKindV22.ERROR_RATE,
                MetricKindV22.LATENCY_P95_MS,
                MetricKindV22.REQUEST_SUPPORT,
            )
        ),
        trace_stats=tuple(
            sorted(
                {
                    (item.service, item.operation, 10.0)
                    for item in case.capture.traces
                    if item.service in set(case.candidate_services)
                }
            )
        ),
        resource_stats=tuple((service, 20.0, 0.0) for service in case.candidate_services),
    )


def _runtime_observation(
    *,
    action: EvidenceActionV22,
    outcome: ReadOutcomeV22,
    run_id: str,
    dispatch_ordinal: int,
    observed_at: object,
) -> ReadToolObservation:
    records = tuple(
        item for item in outcome.records if isinstance(item, RuntimeRecordV22)
    )
    source_records = tuple(
        RuntimeRecord(
            logical_service=record.service,
            owned_container_present=record.state is not RuntimeStateV22.ABSENT,
            state=RuntimeStateV2(record.state.value),
            health=HealthState.HEALTHY if record.healthy else HealthState.UNHEALTHY,
            restart_count=record.restart_count,
            exit_code=0 if record.state is RuntimeStateV22.RUNNING else 137,
            endpoint_probe_performed=record.state is RuntimeStateV22.RUNNING,
            endpoint_state=(
                EndpointState.READY
                if record.healthy
                else EndpointState.NOT_READY
                if record.state is RuntimeStateV22.RUNNING
                else EndpointState.NOT_APPLICABLE
            ),
        )
        for record in records
    )
    request = build_inspect_service_runtime_request(
        run_id=run_id,
        services=action.target_services,
        max_results=len(action.target_services),
    )
    success = outcome.status in {
        ReadSourceStatusV22.SUCCESS_NONEMPTY,
        ReadSourceStatusV22.SUCCESS_EMPTY,
    }
    error_by_status = {
        ReadSourceStatusV22.FAILURE_UNAVAILABLE: ToolErrorCode.SOURCE_UNAVAILABLE,
        ReadSourceStatusV22.FAILURE_TIMEOUT: ToolErrorCode.SOURCE_TIMEOUT,
        ReadSourceStatusV22.FAILURE_SCHEMA: ToolErrorCode.SOURCE_SCHEMA_INVALID,
    }
    del dispatch_ordinal
    return build_read_tool_observation(
        request=request,
        authority=build_fake_read_authority(),
        duplicate_of_request_sha256=None,
        status=ObservationStatus.SUCCESS if success else ObservationStatus.FAILURE,
        error_code=None if success else error_by_status[outcome.status],
        results=source_records if success else (),
        truncated=outcome.truncated if success else False,
        observed_at_start=observed_at,  # type: ignore[arg-type]
        observed_at_end=observed_at,  # type: ignore[arg-type]
        monotonic_latency_ms=0,
        counters=ToolCounters(
            dispatch_ordinal=1,
            backend_call_count=1,
            success_count=1 if success else 0,
            failure_count=0 if success else 1,
        ),
    )


def _memory_outcome(
    *,
    action: EvidenceActionV22,
    outcome: ReadOutcomeV22,
    run_id: str,
    dispatch_ordinal: int,
    observed_at: object,
) -> MemoryReadOutcomeV22:
    if action.source is not EvidenceSourceV22.RUNTIME:
        return outcome
    return RuntimeReadOutcomeV22.from_pr_b(
        action=action,
        source_outcome=outcome,
        source_observation=_runtime_observation(
            action=action,
            outcome=outcome,
            run_id=run_id,
            dispatch_ordinal=dispatch_ordinal,
            observed_at=observed_at,
        ),
    )


def _bootstrap(
    *,
    case: NormalizedPracticalCaseV22,
    topology: StaticTopologyV22,
    run_id: str,
) -> tuple[tuple[MemoryReadOutcomeV22, ...], object, object, ActionCatalogV22]:
    capabilities = build_default_tool_capability_registry_v22()
    catalog = build_action_catalog_v22(
        candidate_services=case.candidate_services,
        topology=topology,
        capability_registry=capabilities,
        executed_action_ids=(),
        remaining_budget=3.0,
    )
    runtime_action = next(
        item
        for item in catalog.registry_actions
        if item.source is EvidenceSourceV22.RUNTIME
        and item.target_services == case.candidate_services
    )
    metric_actions = tuple(
        next(
            item
            for item in catalog.registry_actions
            if item.source is EvidenceSourceV22.METRICS
            and item.target_services == (service,)
        )
        for service in case.candidate_services
    )
    backend = QuerySpecificReplayBackendV22(case.capture)
    outcomes: list[MemoryReadOutcomeV22] = []
    for ordinal, action in enumerate((runtime_action, *metric_actions), start=1):
        source = backend.execute(action)
        outcomes.append(
            _memory_outcome(
                action=action,
                outcome=source,
                run_id=run_id,
                dispatch_ordinal=ordinal,
                observed_at=case.capture.captured_at,
            )
        )
    salient, full = build_memory_views_v22(
        outcomes=tuple(outcomes),
        baseline=_baseline(case),
        observed_at=case.capture.captured_at,
        top_k=64,
    )
    snapshot = build_common_triage_snapshot_v22(
        memory=salient,
        candidate_services=case.candidate_services,
        topology=topology,
        capability_registry=capabilities,
    )
    return tuple(outcomes), snapshot, full, catalog


def _turn_input(
    *,
    arm: ControllerArmV22,
    run_id: str,
    identity_sha256: str,
    session: ControllerSessionStateV22,
    bootstrap: object,
    hypotheses: object,
    catalog: ActionCatalogV22,
    salient: object,
) -> ControllerTurnInputV22:
    context = ControllerRuntimeContextV22.build(
        run_id=run_id,
        turn_ordinal=session.provider_turns_used + 1,
        controller_identity_sha256=identity_sha256,
        remaining_evidence_budget=3.0 - session.ledger.weighted_evidence_cost,
        remaining_provider_turns=5 - session.provider_turns_used,
        correction_remaining=not session.ledger.correction_used,
    )
    ledger_view = (
        None
        if arm is ControllerArmV22.FLAT_CANONICAL
        else build_belief_ledger_view_v22(
            ledger=session.ledger,
            hypothesis_catalog=hypotheses,  # type: ignore[arg-type]
        )
    )
    return build_controller_turn_input_v22(
        arm=arm,
        runtime_context=context,
        bootstrap=bootstrap,  # type: ignore[arg-type]
        hypothesis_catalog=hypotheses,  # type: ignore[arg-type]
        action_catalog=catalog,
        salient_memory=salient,  # type: ignore[arg-type]
        belief_ledger_view=ledger_view,
    )


def _failure_result(
    *,
    case: NormalizedPracticalCaseV22,
    arm: ControllerArmV22,
    status: PracticalRunStatusV22,
    session: ControllerSessionStateV22,
    provider_calls: int,
    first_pass: int,
    post_repair: int,
    repairs: int,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    latency_ms: float,
    retries: int,
    duplicate_reads: int,
    safe_error_code: str,
    uncaught: int = 0,
) -> PracticalCaseRunV22:
    return PracticalCaseRunV22(
        schema_version="dta-v22.practical-case-run.v1",
        case_id=case.case_id,
        arm=arm,
        case_bytes_sha256=case.source_bytes_sha256,
        status=status,
        terminal=None,
        root_service=None,
        mechanism=None,
        supporting_evidence_refs=(),
        evidence_ref_valid=False,
        semantic_clause_valid=False,
        adaptive_reads=session.read_dispatches,
        duplicate_read_attempts=duplicate_reads,
        provider_turns=session.provider_turns_used,
        provider_calls=provider_calls,
        first_pass_protocol_successes=first_pass,
        post_repair_protocol_successes=post_repair,
        semantic_repairs=repairs,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        transport_retry_count=retries,
        uncaught_exceptions=uncaught,
        safe_error_code=safe_error_code,
        agent_writes=0,
        planner_ledger_visible=arm is ControllerArmV22.PLANNER_LITE,
    )


def _execute_practical_case(
    *,
    case: NormalizedPracticalCaseV22,
    arm: ControllerArmV22,
    provider: PracticalProviderV22 | PracticalProviderV221,
    system_prompt: str,
    terminal_exploration_policy: TerminalExplorationPolicyV221 | None,
) -> PracticalCaseRunV22 | PracticalCaseRunV221:
    """Run one arm without truth, writes, Runbooks, Docker, or live mutation."""

    case = NormalizedPracticalCaseV22.model_validate(case.model_dump(mode="python"))
    topology = StaticTopologyV22.build(
        services=case.candidate_services,
        edges=case.topology_edges,
    )
    run_identity = (
        case.case_id
        if terminal_exploration_policy is None
        else ":".join((case.case_id, arm.value, terminal_exploration_policy.value))
    )
    run_id = hashlib.sha256(run_identity.encode("utf-8")).hexdigest()[:32]
    identity_sha256 = semantic_sha256_v22(
        {"adapter": "simple-provider-v22", "prompt": system_prompt}
        if terminal_exploration_policy is None
        else {
            "adapter": "simple-provider-v221",
            "prompt": system_prompt,
            "terminal_exploration_policy": terminal_exploration_policy.value,
        }
    )
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=case.candidate_services
    )
    support_policy = build_default_evidence_support_policy_v22()
    bootstrap_outcomes, bootstrap, _, initial_catalog = _bootstrap(
        case=case,
        topology=topology,
        run_id=run_id,
    )
    session = initialize_controller_session_v22(
        arm=arm,
        controller_identity_sha256=identity_sha256,
        hypothesis_catalog=hypotheses,
        bootstrap=bootstrap,  # type: ignore[arg-type]
        support_policy_sha256=support_policy.policy_sha256,
    )
    capabilities = build_default_tool_capability_registry_v22()
    backend = QuerySpecificReplayBackendV22(case.capture)
    outcomes = list(bootstrap_outcomes)
    baseline = _baseline(case)
    requested_actions: list[str] = []
    provider_calls = first_pass = post_repair = repairs = 0
    input_tokens = output_tokens = total_tokens = retries = 0
    latency_ms = 0.0
    pending_repair: str | None = None
    adapter_repair_consumed = False
    policy_redirects = repeated_premature_abstentions = 0
    redirect_response_kind: ControllerDecisionKindV22 | None = None
    logical_decision_attempts = 0
    adaptive_read_events: list[PracticalAdaptiveReadEventV221] = []

    def finalize(
        result: PracticalCaseRunV22,
    ) -> PracticalCaseRunV22 | PracticalCaseRunV221:
        if terminal_exploration_policy is None:
            return result
        return PracticalCaseRunV221.model_validate(
            {
                **result.model_dump(mode="python"),
                "schema_version": "dta-v22.1.practical-case-run.v1",
                "normalized_case_sha256": semantic_sha256_v22(
                    case.model_dump(mode="json")
                ),
                "terminal_exploration_policy": terminal_exploration_policy,
                "policy_redirects": policy_redirects,
                "premature_abstention_proposals": (
                    policy_redirects + repeated_premature_abstentions
                ),
                "repeated_premature_abstentions": repeated_premature_abstentions,
                "redirect_response_kind": redirect_response_kind,
                "logical_decision_attempts": logical_decision_attempts,
                "adaptive_read_events": tuple(adaptive_read_events),
            }
        )

    def finish_failure(**kwargs: Any) -> PracticalCaseRunV22 | PracticalCaseRunV221:
        return finalize(_failure_result(**kwargs))

    def account_provider_outcome(outcome: ProviderTurnOutcomeV22) -> None:
        nonlocal provider_calls, first_pass, post_repair, repairs
        nonlocal adapter_repair_consumed, input_tokens, output_tokens
        nonlocal total_tokens, latency_ms, retries
        provider_calls += outcome.provider_calls
        first_pass += int(outcome.first_pass_protocol_success)
        post_repair += int(outcome.post_repair_protocol_success)
        repairs += int(outcome.semantic_repair_used)
        adapter_repair_consumed = (
            adapter_repair_consumed or outcome.semantic_repair_used
        )
        input_tokens += outcome.input_tokens
        output_tokens += outcome.output_tokens
        total_tokens += outcome.total_tokens
        latency_ms += outcome.latency_ms
        retries += outcome.transport_retry_count

    try:
        while session.terminal is ControllerSessionTerminalV22.ACTIVE:
            if session.provider_turns_used >= 5:
                return finish_failure(
                    case=case,
                    arm=arm,
                    status=PracticalRunStatusV22.PROTOCOL_FAILED,
                    session=session,
                    provider_calls=provider_calls,
                    first_pass=first_pass,
                    post_repair=post_repair,
                    repairs=repairs,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    latency_ms=latency_ms,
                    retries=retries,
                    duplicate_reads=len(requested_actions) - len(set(requested_actions)),
                    safe_error_code="PROVIDER_TURN_BUDGET_EXHAUSTED",
                )
            salient, _ = build_memory_views_v22(
                outcomes=tuple(outcomes),
                baseline=baseline,
                observed_at=case.capture.captured_at,
                top_k=64,
            )
            remaining = 3.0 - session.ledger.weighted_evidence_cost
            catalog = build_action_catalog_v22(
                candidate_services=case.candidate_services,
                topology=topology,
                capability_registry=capabilities,
                executed_action_ids=session.ledger.executed_action_ids,
                covered_capability_keys=session.ledger.covered_capability_keys,
                remaining_budget=remaining,
            )
            turn_input = _turn_input(
                arm=arm,
                run_id=run_id,
                identity_sha256=identity_sha256,
                session=session,
                bootstrap=bootstrap,
                hypotheses=hypotheses,
                catalog=catalog,
                salient=salient,
            )
            logical_decision_attempts += 1
            if pending_repair is None:
                if terminal_exploration_policy is None:
                    provider_outcome = provider.complete_turn(
                        turn_input=turn_input,
                        run_id=run_id,
                        system_prompt=system_prompt,
                        allow_semantic_repair=(
                            not adapter_repair_consumed
                            and not session.ledger.correction_used
                        ),
                    )
                else:
                    provider_outcome = cast(
                        PracticalProviderV221, provider
                    ).complete_turn_v221(
                        turn_input=turn_input,
                        run_id=run_id,
                        system_prompt=system_prompt,
                        allow_semantic_repair=(
                            not adapter_repair_consumed
                            and not session.ledger.correction_used
                        ),
                        terminal_exploration_policy=terminal_exploration_policy,
                        adaptive_reads_so_far=session.read_dispatches,
                        policy_redirect_remaining=(
                            terminal_exploration_policy
                            is TerminalExplorationPolicyV221.MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN
                            and not bool(policy_redirects)
                        ),
                    )
            else:
                if terminal_exploration_policy is None:
                    provider_outcome = provider.complete_repair_turn(
                        turn_input=turn_input,
                        run_id=run_id,
                        safe_error_code=pending_repair,
                        system_prompt=system_prompt,
                    )
                else:
                    provider_outcome = cast(
                        PracticalProviderV221, provider
                    ).complete_repair_turn_v221(
                        turn_input=turn_input,
                        run_id=run_id,
                        safe_error_code=pending_repair,
                        system_prompt=system_prompt,
                        terminal_exploration_policy=terminal_exploration_policy,
                        adaptive_reads_so_far=session.read_dispatches,
                        policy_redirect_remaining=(
                            terminal_exploration_policy
                            is TerminalExplorationPolicyV221.MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN
                            and not bool(policy_redirects)
                        ),
                    )
                pending_repair = None
            account_provider_outcome(provider_outcome)
            if terminal_exploration_policy is not None:
                disposition = evaluate_terminal_exploration_policy_v221(
                    policy=terminal_exploration_policy,
                    decision=provider_outcome.decision.decision,
                    session_read_dispatches=session.read_dispatches,
                    action_catalog=catalog,
                    remaining_evidence_budget=remaining,
                    policy_redirect_used=bool(policy_redirects),
                )
                if (
                    disposition
                    is TerminalExplorationDispositionV221.PREMATURE_ABSTENTION
                ):
                    policy_redirects = 1
                    logical_decision_attempts += 1
                    provider_outcome = cast(
                        PracticalProviderV221, provider
                    ).complete_policy_redirect_turn_v221(
                        turn_input=turn_input,
                        run_id=run_id,
                        safe_error_code="PREMATURE_ABSTENTION",
                        system_prompt=system_prompt,
                        terminal_exploration_policy=terminal_exploration_policy,
                        adaptive_reads_so_far=session.read_dispatches,
                        policy_redirect_remaining=False,
                    )
                    account_provider_outcome(provider_outcome)
                    redirect_response_kind = provider_outcome.decision.decision
                if (
                    policy_redirects
                    and provider_outcome.decision.decision
                    is ControllerDecisionKindV22.ABSTAIN
                    and session.read_dispatches == 0
                    and bool(catalog.actions)
                    and remaining > 0
                ):
                    repeated_premature_abstentions = 1
                    return finish_failure(
                        case=case,
                        arm=arm,
                        status=PracticalRunStatusV22.PROTOCOL_FAILED,
                        session=session,
                        provider_calls=provider_calls,
                        first_pass=first_pass,
                        post_repair=post_repair,
                        repairs=repairs,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                        latency_ms=latency_ms,
                        retries=retries,
                        duplicate_reads=len(requested_actions)
                        - len(set(requested_actions)),
                        safe_error_code="PREMATURE_ABSTENTION_REPEATED",
                    )
            protocol = process_controller_decision_v22(
                session=session,
                raw_decision=provider_outcome.decision,
                turn_input=turn_input,
            )
            session = protocol.session
            if protocol.disposition is ControllerProtocolDispositionV22.FAILED:
                return finish_failure(
                    case=case,
                    arm=arm,
                    status=PracticalRunStatusV22.PROTOCOL_FAILED,
                    session=session,
                    provider_calls=provider_calls,
                    first_pass=first_pass,
                    post_repair=post_repair,
                    repairs=repairs,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    latency_ms=latency_ms,
                    retries=retries,
                    duplicate_reads=len(requested_actions) - len(set(requested_actions)),
                    safe_error_code=(
                        protocol.error_code.value
                        if protocol.error_code is not None
                        else "PROTOCOL_FAILED"
                    ),
                )
            if protocol.disposition is ControllerProtocolDispositionV22.CORRECTION_REQUIRED:
                if adapter_repair_consumed or protocol.error_code is None:
                    return finish_failure(
                        case=case,
                        arm=arm,
                        status=PracticalRunStatusV22.PROTOCOL_FAILED,
                        session=session,
                        provider_calls=provider_calls,
                        first_pass=first_pass,
                        post_repair=post_repair,
                        repairs=repairs,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                        latency_ms=latency_ms,
                        retries=retries,
                        duplicate_reads=len(requested_actions) - len(set(requested_actions)),
                        safe_error_code="REPAIR_ALREADY_CONSUMED",
                    )
                pending_repair = protocol.error_code.value
                continue
            decision = protocol.accepted_decision
            if decision is None:
                raise RuntimeError("accepted controller result lacks a decision")
            if protocol.read_dispatch_authorized:
                requested_actions.append(decision.action_id)
                if session.pending_read is None:
                    raise RuntimeError("authorized read lacks pending dispatch binding")
                session = record_controller_read_dispatch_v22(
                    session=session,
                    authorization_sha256=session.pending_read.authorization_sha256,
                )
                action = next(
                    item
                    for item in initial_catalog.registry_actions
                    if item.action_id == decision.action_id
                )
                source_outcome = backend.execute(action)
                adaptive_read_events.append(
                    PracticalAdaptiveReadEventV221(
                        schema_version="dta-v22.1.practical-adaptive-read-event.v1",
                        ordinal=len(requested_actions),
                        action_id=action.action_id,
                        source=action.source,
                        status=source_outcome.status,
                    )
                )
                outcome = _memory_outcome(
                    action=action,
                    outcome=source_outcome,
                    run_id=run_id,
                    dispatch_ordinal=len(bootstrap_outcomes) + len(requested_actions),
                    observed_at=case.capture.captured_at,
                )
                session = record_controller_read_outcome_v22(
                    session=session,
                    turn_input=turn_input,
                    outcome=outcome,
                )
                if outcome.outcome_sha256 not in {
                    item.outcome_sha256 for item in outcomes
                }:
                    outcomes.append(outcome)
                continue
            admission = protocol.semantic_admission
            if admission is None or admission.terminal is DiagnosisTerminalV22.FAILED:
                return finish_failure(
                    case=case,
                    arm=arm,
                    status=PracticalRunStatusV22.PROTOCOL_FAILED,
                    session=session,
                    provider_calls=provider_calls,
                    first_pass=first_pass,
                    post_repair=post_repair,
                    repairs=repairs,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    latency_ms=latency_ms,
                    retries=retries,
                    duplicate_reads=len(requested_actions) - len(set(requested_actions)),
                    safe_error_code=(
                        "MISSING_SEMANTIC_ADMISSION"
                        if admission is None
                        else admission.result_code
                    ),
                )
            admitted = admission.admitted_diagnosis
            return finalize(
                PracticalCaseRunV22(
                    schema_version="dta-v22.practical-case-run.v1",
                    case_id=case.case_id,
                    arm=arm,
                    case_bytes_sha256=case.source_bytes_sha256,
                    status=PracticalRunStatusV22.VALID_TERMINAL,
                    terminal=admission.terminal.value,
                    root_service=None if admitted is None else admitted.root_service,
                    mechanism=(
                        admitted.mechanism.value
                        if admitted is not None
                        else "NO_INCIDENT"
                        if admission.terminal is DiagnosisTerminalV22.NO_INCIDENT
                        else "UNKNOWN"
                    ),
                    supporting_evidence_refs=(
                        () if admitted is None else admitted.supporting_evidence_refs
                    ),
                    evidence_ref_valid=True,
                    semantic_clause_valid=True,
                    adaptive_reads=session.read_dispatches,
                    duplicate_read_attempts=len(requested_actions)
                    - len(set(requested_actions)),
                    provider_turns=session.provider_turns_used,
                    provider_calls=provider_calls,
                    first_pass_protocol_successes=first_pass,
                    post_repair_protocol_successes=post_repair,
                    semantic_repairs=repairs,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    latency_ms=latency_ms,
                    transport_retry_count=retries,
                    uncaught_exceptions=0,
                    safe_error_code=None,
                    agent_writes=0,
                    planner_ledger_visible=arm is ControllerArmV22.PLANNER_LITE,
                )
            )
    except ProviderProtocolFailureV22 as error:
        if terminal_exploration_policy is not None:
            provider_calls += error.provider_calls
            repairs += int(error.semantic_repair_used)
            input_tokens += error.input_tokens
            output_tokens += error.output_tokens
            total_tokens += error.total_tokens
            latency_ms += error.latency_ms
            retries += error.transport_retry_count
        status = (
            PracticalRunStatusV22.TRANSPORT_FAILED
            if error.safe_code == "TRANSPORT_FAILED"
            else PracticalRunStatusV22.PROTOCOL_FAILED
        )
        return finish_failure(
            case=case,
            arm=arm,
            status=status,
            session=session,
            provider_calls=provider_calls,
            first_pass=first_pass,
            post_repair=post_repair,
            repairs=repairs,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            retries=retries,
            duplicate_reads=len(requested_actions) - len(set(requested_actions)),
            safe_error_code=error.safe_code,
        )
    except Exception as error:  # runner boundary: preserve the case instead of aborting
        return finish_failure(
            case=case,
            arm=arm,
            status=PracticalRunStatusV22.RUNNER_EXCEPTION,
            session=session,
            provider_calls=provider_calls,
            first_pass=first_pass,
            post_repair=post_repair,
            repairs=repairs,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            retries=retries,
            duplicate_reads=len(requested_actions) - len(set(requested_actions)),
            safe_error_code=type(error).__name__,
            uncaught=1,
        )
    raise AssertionError("controller loop exited without a terminal result")


def execute_practical_case_v22(
    *,
    case: NormalizedPracticalCaseV22,
    arm: ControllerArmV22,
    provider: PracticalProviderV22,
    system_prompt: str = SHARED_SYSTEM_PROMPT_V22,
) -> PracticalCaseRunV22:
    return cast(
        PracticalCaseRunV22,
        _execute_practical_case(
            case=case,
            arm=arm,
            provider=provider,
            system_prompt=system_prompt,
            terminal_exploration_policy=None,
        ),
    )


def execute_practical_case_v221(
    *,
    case: NormalizedPracticalCaseV22,
    arm: ControllerArmV22,
    provider: PracticalProviderV221,
    terminal_exploration_policy: TerminalExplorationPolicyV221,
    system_prompt: str = SHARED_SYSTEM_PROMPT_V221,
) -> PracticalCaseRunV221:
    return cast(
        PracticalCaseRunV221,
        _execute_practical_case(
            case=case,
            arm=arm,
            provider=provider,
            system_prompt=system_prompt,
            terminal_exploration_policy=terminal_exploration_policy,
        ),
    )


__all__ = (
    "PracticalAdaptiveReadEventV221",
    "PracticalCaseRunV22",
    "PracticalCaseRunV221",
    "PracticalProviderV22",
    "PracticalProviderV221",
    "PracticalRunStatusV22",
    "execute_practical_case_v22",
    "execute_practical_case_v221",
)
