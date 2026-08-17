from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ecomsre.dta_v2.agent_contracts import ProviderUsage
from ecomsre.dta_v2.read_tools import FakeReadBackend
from ecomsre.dta_v2.tool_contracts import (
    MetricKind,
    ReadToolRequest,
    build_inspect_resource_usage_request,
    build_inspect_service_runtime_request,
    build_query_metrics_request,
    build_search_logs_request,
    build_trace_neighborhood_request,
)
from ecomsre.dta_v2.v21.agent import (
    AgentFailureCodeV21,
    AgentRunTerminalV21,
    DiagnosisBindingFailureCodeV21,
    run_evidence_guided_agent_v21,
    run_flat_adaptive_agent_v21,
    run_one_shot_agent_v21,
)
from ecomsre.dta_v2.v21.agent_contracts import (
    ActionSelectionDecisionV21,
    AgentArmV21,
    build_alert_context_v21,
)
from ecomsre.dta_v2.v21.agent_provider import ProviderTurnV21
from ecomsre.dta_v2.v21.contracts import (
    ActionDispositionV21,
    ActionParameterV21,
    DtaDiagnosisV21,
    EvidenceSourceV21,
    FaultDomainV21,
    FaultMechanismV21,
    RunbookIdV21,
    TerminalV21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.identity import build_three_arm_identities_v21
from ecomsre.dta_v2.v21.planner_contracts import (
    DiagnosticHypothesisV21,
    EvidencePlanDecisionV21,
    HypothesisStatusV21,
    PlannerNextStepV21,
    build_evidence_plan_decision_v21,
)
from ecomsre.dta_v2.v21.registry import (
    load_default_runbook_registry,
    load_default_scenario_registries,
)
from ecomsre.model.gateway import ProviderProtocolError


ROOT = Path(__file__).resolve().parents[2]
START = datetime(2026, 8, 17, 2, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=5)
MODEL = "gpt-5.4-mini-2026-03-17"


class ScriptedProviderV21:
    def __init__(self, *, arm, investigation, action=None):
        self.identity = next(
            item
            for item in build_three_arm_identities_v21(
                model_id=MODEL, max_completion_tokens=1600
            )
            if item.arm is arm
        )
        self.investigation = list(investigation)
        self.action = action
        self.attempted_calls = 0
        self.visible_states = []

    def investigation_turn(self, *, context, visible_state, read_tools_enabled):
        del context, read_tools_enabled
        self.visible_states.append(visible_state)
        self.attempted_calls += 1
        value = self.investigation.pop(0)
        if isinstance(value, Exception):
            raise value
        response = {"id": f"scripted-{self.attempted_calls}"}
        return ProviderTurnV21(
            function_name="scripted_investigation",
            tool_call_id=f"scripted-{self.attempted_calls}",
            raw_response_sha256=semantic_sha256(response),
            usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            monotonic_latency_ms=1,
            plan_decision=(
                value if isinstance(value, EvidencePlanDecisionV21) else None
            ),
            read_request=(value if _is_request(value) else None),
            diagnosis=(value if isinstance(value, DtaDiagnosisV21) else None),
        )

    def action_selection_turn(self, *, diagnosis, resolved_evidence, candidate_view):
        del diagnosis, resolved_evidence, candidate_view
        self.attempted_calls += 1
        assert self.action is not None
        response = {"id": f"scripted-{self.attempted_calls}"}
        return ProviderTurnV21(
            function_name="scripted_action_selection",
            tool_call_id=f"scripted-{self.attempted_calls}",
            raw_response_sha256=semantic_sha256(response),
            usage=ProviderUsage(input_tokens=8, output_tokens=4, total_tokens=12),
            monotonic_latency_ms=1,
            action_selection=self.action,
        )


def _is_request(value: object) -> bool:
    return hasattr(value, "normalized_request_sha256") and hasattr(value, "tool")


def _context(run_id: str, scenario_index: int):
    scenarios, _, _ = load_default_scenario_registries(ROOT)
    return build_alert_context_v21(
        scenario=scenarios.scenarios[scenario_index],
        run_id=run_id,
        started_at=START,
        ended_at=END,
    )


def test_provider_protocol_failure_retains_only_fixed_safe_validation_codes() -> None:
    run_id = "8" * 32
    provider = ScriptedProviderV21(
        arm=AgentArmV21.FLAT_ADAPTIVE,
        investigation=[
            ProviderProtocolError(
                "Provider investigation output is invalid "
                "[codes=diagnosis:missing,output:planner_gap_mismatch]"
            )
        ],
    )

    result = run_flat_adaptive_agent_v21(
        context=_context(run_id, 1),
        backend=FakeReadBackend.healthy(),
        registry=load_default_runbook_registry(ROOT),
        provider=provider,
    )

    assert result.terminal is AgentRunTerminalV21.FAILED
    assert result.failure_code is AgentFailureCodeV21.PROVIDER_PROTOCOL_FAILURE
    assert result.provider_failure_codes == (
        "diagnosis:missing",
        "output:planner_gap_mismatch",
    )


def test_provider_protocol_failure_does_not_retain_unstructured_detail() -> None:
    run_id = "7" * 32
    private_value = "private-provider-output-must-not-leak"
    provider = ScriptedProviderV21(
        arm=AgentArmV21.FLAT_ADAPTIVE,
        investigation=[ProviderProtocolError(private_value)],
    )

    result = run_flat_adaptive_agent_v21(
        context=_context(run_id, 1),
        backend=FakeReadBackend.healthy(),
        registry=load_default_runbook_registry(ROOT),
        provider=provider,
    )

    assert result.provider_failure_codes == ()
    assert private_value not in result.model_dump_json()


def _metrics(run_id: str, service: str):
    return build_query_metrics_request(
        run_id=run_id,
        service=service,
        started_at=START,
        ended_at=END,
        metric_kinds=(MetricKind.ERROR_RATE, MetricKind.REQUEST_SUPPORT),
        max_results=4,
    )


def _runtime(run_id: str, service: str):
    return build_inspect_service_runtime_request(
        run_id=run_id, services=(service,), max_results=1
    )


def _resources(run_id: str, service: str):
    return build_inspect_resource_usage_request(
        run_id=run_id,
        services=(service,),
        sampling_window_seconds=3,
        sample_count=3,
    )


def _trace(run_id: str, service: str):
    return build_trace_neighborhood_request(
        run_id=run_id,
        service=service,
        started_at=START,
        ended_at=END,
        max_spans=10,
    )


def _logs(run_id: str, service: str):
    return build_search_logs_request(
        run_id=run_id,
        service=service,
        started_at=START,
        ended_at=END,
        max_records=10,
    )


def _hypothesis(service, domain, mechanism, gaps, refs=()):
    return DiagnosticHypothesisV21(
        hypothesis_id="h1",
        root_service=service,
        fault_domain=domain,
        fault_mechanism=mechanism,
        status=HypothesisStatusV21.ACTIVE,
        supporting_evidence_refs=refs,
        contradicting_evidence_refs=(),
        unresolved_evidence_sources=gaps,
    )


def _request_plan(*, run_id, turn, hypothesis, source, request):
    return build_evidence_plan_decision_v21(
        run_id=run_id,
        turn_ordinal=turn,
        hypotheses=(hypothesis,),
        next_step=PlannerNextStepV21.REQUEST_EVIDENCE,
        evidence_gap_sources=(source,),
        read_request=request,
        diagnosis=None,
        bounded_rationale="One typed source can close the active evidence gap.",
    )


def _diagnosis(*, run_id, service, domain, mechanism, refs, sources):
    return DtaDiagnosisV21(
        schema_version="dta-v21.diagnosis.v1",
        run_id=run_id,
        terminal=TerminalV21.COMPLETED,
        root_service=service,
        root_entity_ref=None if service is None else f"service:{service}",
        fault_domain=domain,
        mechanism=mechanism,
        confidence=None if service is None else 0.9,
        supporting_evidence_refs=refs,
        contradicting_evidence_refs=(),
        evidence_source_types=sources,
        uncertainties=(),
        summary="The bounded typed observations support this submitted diagnosis.",
    )


def _submit_plan(run_id, turn, hypothesis, diagnosis):
    return build_evidence_plan_decision_v21(
        run_id=run_id,
        turn_ordinal=turn,
        hypotheses=(hypothesis,),
        next_step=PlannerNextStepV21.SUBMIT_DIAGNOSIS,
        evidence_gap_sources=(),
        read_request=None,
        diagnosis=diagnosis,
        bounded_rationale="The cited observations are sufficient for early stopping.",
    )


def _action(*, disposition, runbook=None, service=None, parameters=(), refs=()):
    return ActionSelectionDecisionV21(
        schema_version="dta-v21.action-selection-decision.v1",
        disposition=disposition,
        runbook_id=runbook,
        target_service=service,
        parameters=parameters,
        supporting_evidence_refs=refs,
        rationale="The exact visible candidate matches the cited observations.",
    )


def _abstain(*, run_id: str) -> DtaDiagnosisV21:
    return DtaDiagnosisV21(
        schema_version="dta-v21.diagnosis.v1",
        run_id=run_id,
        terminal=TerminalV21.ABSTAIN,
        root_service=None,
        root_entity_ref=None,
        fault_domain=None,
        mechanism=None,
        confidence=None,
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
        evidence_source_types=(),
        uncertainties=("The bounded evidence is insufficient.",),
        summary="The bounded investigation abstained.",
    )


def test_flat_and_one_shot_reject_empty_ref_diagnosis_from_another_run() -> None:
    run_id = "a" * 32
    wrong_run = "b" * 32
    registry = load_default_runbook_registry(ROOT)

    flat = run_flat_adaptive_agent_v21(
        context=_context(run_id, 0),
        backend=FakeReadBackend.healthy(),
        registry=registry,
        provider=ScriptedProviderV21(
            arm=AgentArmV21.FLAT_ADAPTIVE,
            investigation=[_abstain(run_id=wrong_run)],
        ),
    )
    one_shot = run_one_shot_agent_v21(
        context=_context(run_id, 0),
        backend=FakeReadBackend.healthy(),
        registry=registry,
        provider=ScriptedProviderV21(
            arm=AgentArmV21.ONE_SHOT_FULL_CONTEXT,
            investigation=[_abstain(run_id=wrong_run)],
        ),
        materialization_requests=(
            _metrics(run_id, "ad"),
            _runtime(run_id, "ad"),
            _resources(run_id, "ad"),
            _logs(run_id, "ad"),
        ),
    )

    for result in (flat, one_shot):
        assert result.terminal is AgentRunTerminalV21.FAILED
        assert result.failure_code is AgentFailureCodeV21.DIAGNOSIS_BINDING_FAILURE
        assert (
            result.diagnosis_binding_failure_code
            is DiagnosisBindingFailureCodeV21.RUN_ID_MISMATCH
        )
        assert result.diagnosis is None


def test_completed_diagnosis_with_unresolved_ref_is_typed_binding_failure() -> None:
    run_id = "c" * 32
    diagnosis = _diagnosis(
        run_id=run_id,
        service="ad",
        domain=FaultDomainV21.LOCAL_RESOURCE,
        mechanism=FaultMechanismV21.CPU_SATURATION,
        refs=(f"evidence://{run_id}/metrics/0001",),
        sources=(EvidenceSourceV21.METRICS,),
    )
    result = run_flat_adaptive_agent_v21(
        context=_context(run_id, 0),
        backend=FakeReadBackend.healthy(),
        registry=load_default_runbook_registry(ROOT),
        provider=ScriptedProviderV21(
            arm=AgentArmV21.FLAT_ADAPTIVE,
            investigation=[diagnosis],
        ),
    )

    assert result.failure_code is AgentFailureCodeV21.DIAGNOSIS_BINDING_FAILURE
    assert (
        result.diagnosis_binding_failure_code
        is DiagnosisBindingFailureCodeV21.EVIDENCE_RESOLUTION_FAILURE
    )


def test_planner_arm_runs_cpu_case_with_compact_state_and_early_stop() -> None:
    run_id = "3" * 32
    requests = (_metrics(run_id, "ad"), _runtime(run_id, "ad"), _resources(run_id, "ad"))
    refs = (
        f"evidence://{run_id}/metrics/0001",
        f"evidence://{run_id}/runtime/0002",
        f"evidence://{run_id}/resources/0003",
    )
    diagnosis = _diagnosis(
        run_id=run_id,
        service="ad",
        domain=FaultDomainV21.LOCAL_RESOURCE,
        mechanism=FaultMechanismV21.CPU_SATURATION,
        refs=refs,
        sources=(EvidenceSourceV21.METRICS, EvidenceSourceV21.RUNTIME, EvidenceSourceV21.RESOURCES),
    )
    hypotheses = (
        _hypothesis("ad", FaultDomainV21.LOCAL_RESOURCE, FaultMechanismV21.CPU_SATURATION, (EvidenceSourceV21.METRICS,)),
        _hypothesis("ad", FaultDomainV21.LOCAL_RESOURCE, FaultMechanismV21.CPU_SATURATION, (EvidenceSourceV21.RUNTIME,), refs=refs[:1]),
        _hypothesis("ad", FaultDomainV21.LOCAL_RESOURCE, FaultMechanismV21.CPU_SATURATION, (EvidenceSourceV21.RESOURCES,), refs=refs[:2]),
    )
    provider = ScriptedProviderV21(
        arm=AgentArmV21.EVIDENCE_GUIDED_PLANNER,
        investigation=[
            _request_plan(run_id=run_id, turn=1, hypothesis=hypotheses[0], source=EvidenceSourceV21.METRICS, request=requests[0]),
            _request_plan(run_id=run_id, turn=2, hypothesis=hypotheses[1], source=EvidenceSourceV21.RUNTIME, request=requests[1]),
            _request_plan(run_id=run_id, turn=3, hypothesis=hypotheses[2], source=EvidenceSourceV21.RESOURCES, request=requests[2]),
            _submit_plan(run_id, 4, _hypothesis("ad", FaultDomainV21.LOCAL_RESOURCE, FaultMechanismV21.CPU_SATURATION, (), refs=refs), diagnosis),
        ],
        action=_action(
            disposition=ActionDispositionV21.EXECUTE_RUNBOOK,
            runbook=RunbookIdV21.MITIGATE_CPU_SATURATION,
            service="ad",
            refs=refs,
        ),
    )

    result = run_evidence_guided_agent_v21(
        context=_context(run_id, 0),
        backend=FakeReadBackend.healthy(),
        registry=load_default_runbook_registry(ROOT),
        provider=provider,
    )

    assert result.terminal is AgentRunTerminalV21.COMPLETED
    assert result.semantic_read_tool_dispatch_count == 3
    assert result.context_materialization_read_count == 0
    assert len(result.planner_trace) == 4
    assert result.action_proposal is not None
    assert result.action_proposal.runbook_id is RunbookIdV21.MITIGATE_CPU_SATURATION
    assert provider.visible_states[0].evidence_index.entries == ()
    assert len(provider.visible_states[-1].evidence_index.entries) == 3


def test_flat_adaptive_discriminates_same_service_email_unavailability() -> None:
    run_id = "4" * 32
    requests = (_metrics(run_id, "email"), _runtime(run_id, "email"))
    refs = (
        f"evidence://{run_id}/metrics/0001",
        f"evidence://{run_id}/runtime/0002",
    )
    diagnosis = _diagnosis(
        run_id=run_id,
        service="email",
        domain=FaultDomainV21.SERVICE_RUNTIME,
        mechanism=FaultMechanismV21.SERVICE_UNAVAILABLE,
        refs=refs,
        sources=(EvidenceSourceV21.METRICS, EvidenceSourceV21.RUNTIME),
    )
    provider = ScriptedProviderV21(
        arm=AgentArmV21.FLAT_ADAPTIVE,
        investigation=[*requests, diagnosis],
        action=_action(
            disposition=ActionDispositionV21.EXECUTE_RUNBOOK,
            runbook=RunbookIdV21.RESTORE_SERVICE_AVAILABILITY,
            service="email",
            parameters=(ActionParameterV21(name="wait_for_health_seconds", value=30),),
            refs=refs,
        ),
    )

    result = run_flat_adaptive_agent_v21(
        context=_context(run_id, 1),
        backend=FakeReadBackend.healthy(),
        registry=load_default_runbook_registry(ROOT),
        provider=provider,
    )

    assert result.terminal is AgentRunTerminalV21.COMPLETED
    assert result.planner_trace == ()
    assert result.diagnosis is not None
    assert result.diagnosis.mechanism is FaultMechanismV21.SERVICE_UNAVAILABLE
    assert result.action_proposal is not None
    assert result.action_proposal.runbook_id is RunbookIdV21.RESTORE_SERVICE_AVAILABILITY
    assert provider.visible_states[-1].prior_requests == requests
    assert provider.visible_states[-1].prior_normalized_request_sha256 == tuple(
        item.normalized_request_sha256 for item in requests
    )


def test_planner_dependency_case_is_traces_led_and_candidate_bound() -> None:
    run_id = "5" * 32
    trace = _trace(run_id, "shipping")
    metrics = _metrics(run_id, "shipping")
    refs = (
        f"evidence://{run_id}/metrics/0002",
        f"evidence://{run_id}/traces/0001",
    )
    diagnosis = _diagnosis(
        run_id=run_id,
        service="shipping",
        domain=FaultDomainV21.DEPENDENCY,
        mechanism=FaultMechanismV21.DEPENDENCY_LATENCY,
        refs=refs,
        sources=(EvidenceSourceV21.METRICS, EvidenceSourceV21.TRACES),
    )
    first = _hypothesis("shipping", FaultDomainV21.DEPENDENCY, FaultMechanismV21.DEPENDENCY_LATENCY, (EvidenceSourceV21.TRACES,))
    second = _hypothesis("shipping", FaultDomainV21.DEPENDENCY, FaultMechanismV21.DEPENDENCY_LATENCY, (EvidenceSourceV21.METRICS,), refs=(refs[1],))
    provider = ScriptedProviderV21(
        arm=AgentArmV21.EVIDENCE_GUIDED_PLANNER,
        investigation=[
            _request_plan(run_id=run_id, turn=1, hypothesis=first, source=EvidenceSourceV21.TRACES, request=trace),
            _request_plan(run_id=run_id, turn=2, hypothesis=second, source=EvidenceSourceV21.METRICS, request=metrics),
            _submit_plan(run_id, 3, _hypothesis("shipping", FaultDomainV21.DEPENDENCY, FaultMechanismV21.DEPENDENCY_LATENCY, (), refs=refs), diagnosis),
        ],
        action=_action(
            disposition=ActionDispositionV21.EXECUTE_RUNBOOK,
            runbook=RunbookIdV21.RESTORE_DEPENDENCY_LATENCY,
            service="shipping",
            refs=refs,
        ),
    )

    result = run_evidence_guided_agent_v21(
        context=_context(run_id, 3),
        backend=FakeReadBackend.healthy(),
        registry=load_default_runbook_registry(ROOT),
        provider=provider,
    )

    assert result.terminal is AgentRunTerminalV21.COMPLETED
    assert result.provider_turns[0].parsed_read_request is not None
    assert result.provider_turns[0].parsed_read_request.tool.value == "query_trace_neighborhood"
    assert result.action_proposal is not None
    assert result.action_proposal.runbook_id is RunbookIdV21.RESTORE_DEPENDENCY_LATENCY


def test_one_shot_materializes_four_reads_and_selects_no_action() -> None:
    run_id = "6" * 32
    requests: tuple[ReadToolRequest, ...] = (
        _metrics(run_id, "email"),
        _logs(run_id, "email"),
        _trace(run_id, "checkout"),
        _runtime(run_id, "email"),
    )
    ref = f"evidence://{run_id}/runtime/0004"
    diagnosis = _diagnosis(
        run_id=run_id,
        service=None,
        domain=None,
        mechanism=None,
        refs=(ref,),
        sources=(EvidenceSourceV21.RUNTIME,),
    )
    provider = ScriptedProviderV21(
        arm=AgentArmV21.ONE_SHOT_FULL_CONTEXT,
        investigation=[diagnosis],
        action=_action(disposition=ActionDispositionV21.NO_ACTION, refs=(ref,)),
    )

    result = run_one_shot_agent_v21(
        context=_context(run_id, 5),
        backend=FakeReadBackend.healthy(),
        registry=load_default_runbook_registry(ROOT),
        provider=provider,
        materialization_requests=requests,
    )

    assert result.terminal is AgentRunTerminalV21.COMPLETED
    assert result.semantic_read_tool_dispatch_count == 0
    assert result.context_materialization_read_count == 4
    assert result.action_proposal is not None
    assert result.action_proposal.disposition is ActionDispositionV21.NO_ACTION


def test_duplicate_read_is_terminally_rejected_without_backend_repeat() -> None:
    run_id = "7" * 32
    request = _metrics(run_id, "payment")
    hypothesis = _hypothesis("payment", FaultDomainV21.CONFIGURATION, FaultMechanismV21.CONFIGURATION_ERROR, (EvidenceSourceV21.METRICS,))
    abstain = build_evidence_plan_decision_v21(
        run_id=run_id,
        turn_ordinal=3,
        hypotheses=(hypothesis,),
        next_step=PlannerNextStepV21.ABSTAIN,
        evidence_gap_sources=(EvidenceSourceV21.METRICS,),
        read_request=None,
        diagnosis=None,
        bounded_rationale="The repeated request did not close the unresolved gap.",
    )
    provider = ScriptedProviderV21(
        arm=AgentArmV21.EVIDENCE_GUIDED_PLANNER,
        investigation=[
            _request_plan(run_id=run_id, turn=1, hypothesis=hypothesis, source=EvidenceSourceV21.METRICS, request=request),
            _request_plan(run_id=run_id, turn=2, hypothesis=hypothesis, source=EvidenceSourceV21.METRICS, request=request),
            abstain,
        ],
    )
    backend = FakeReadBackend.healthy()

    result = run_evidence_guided_agent_v21(
        context=_context(run_id, 4),
        backend=backend,
        registry=load_default_runbook_registry(ROOT),
        provider=provider,
    )

    assert result.terminal is AgentRunTerminalV21.FAILED
    assert result.failure_code is AgentFailureCodeV21.DUPLICATE_READ_REQUEST
    assert result.evidence_store.dispatch_count == 1
    assert backend.call_count == 1
    assert result.provider_turn_count == 2
    assert len(result.provider_turns) == 2
    assert result.provider_turns[-1].observation is None
    assert (
        result.provider_turns[-1].protocol_failure
        is AgentFailureCodeV21.DUPLICATE_READ_REQUEST
    )
    assert len(result.planner_trace) == 1


def test_planner_cross_run_read_is_typed_failure_without_backend_call() -> None:
    run_id = "7" * 32
    wrong_run_id = "8" * 32
    hypothesis = _hypothesis(
        "payment",
        FaultDomainV21.CONFIGURATION,
        FaultMechanismV21.CONFIGURATION_ERROR,
        (EvidenceSourceV21.METRICS,),
    )
    provider = ScriptedProviderV21(
        arm=AgentArmV21.EVIDENCE_GUIDED_PLANNER,
        investigation=[
            _request_plan(
                run_id=run_id,
                turn=1,
                hypothesis=hypothesis,
                source=EvidenceSourceV21.METRICS,
                request=_metrics(wrong_run_id, "payment"),
            )
        ],
    )
    backend = FakeReadBackend.healthy()

    result = run_evidence_guided_agent_v21(
        context=_context(run_id, 4),
        backend=backend,
        registry=load_default_runbook_registry(ROOT),
        provider=provider,
    )

    assert result.terminal is AgentRunTerminalV21.FAILED
    assert result.failure_code is AgentFailureCodeV21.PLANNER_CONTRACT_FAILURE
    assert result.evidence_store.dispatch_count == 0
    assert backend.call_count == 0
    assert result.provider_turn_count == 1
    assert result.provider_turns[-1].observation is None
    assert (
        result.provider_turns[-1].protocol_failure
        is AgentFailureCodeV21.PLANNER_CONTRACT_FAILURE
    )


def test_flat_duplicate_read_is_terminally_rejected_without_backend_repeat() -> None:
    run_id = "6" * 32
    request = _metrics(run_id, "payment")
    provider = ScriptedProviderV21(
        arm=AgentArmV21.FLAT_ADAPTIVE,
        investigation=[request, request, _abstain(run_id=run_id)],
    )
    backend = FakeReadBackend.healthy()

    result = run_flat_adaptive_agent_v21(
        context=_context(run_id, 4),
        backend=backend,
        registry=load_default_runbook_registry(ROOT),
        provider=provider,
    )

    assert result.terminal is AgentRunTerminalV21.FAILED
    assert result.failure_code is AgentFailureCodeV21.DUPLICATE_READ_REQUEST
    assert result.evidence_store.dispatch_count == 1
    assert backend.call_count == 1
    assert result.provider_turn_count == 2
    assert result.provider_turns[-1].observation is None
    assert (
        result.provider_turns[-1].protocol_failure
        is AgentFailureCodeV21.DUPLICATE_READ_REQUEST
    )


def test_fifth_flat_read_is_typed_budget_failure_without_backend_call() -> None:
    run_id = "8" * 32
    reads = [
        _metrics(run_id, "payment"),
        _logs(run_id, "payment"),
        _trace(run_id, "payment"),
        _runtime(run_id, "payment"),
        _resources(run_id, "payment"),
    ]
    provider = ScriptedProviderV21(
        arm=AgentArmV21.FLAT_ADAPTIVE,
        investigation=reads,
    )
    backend = FakeReadBackend.healthy()

    result = run_flat_adaptive_agent_v21(
        context=_context(run_id, 4),
        backend=backend,
        registry=load_default_runbook_registry(ROOT),
        provider=provider,
    )

    assert result.terminal is AgentRunTerminalV21.FAILED
    assert result.failure_code is AgentFailureCodeV21.READ_BUDGET_EXHAUSTED
    assert result.evidence_store.dispatch_count == 4
    assert backend.call_count == 4
