from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ecomsre.dta_v2.agent import AgentRunTerminal
from ecomsre.dta_v2.agent_contracts import (
    ActionSelectionDecision,
    ProviderUsage,
    build_alert_context,
)
from ecomsre.dta_v2.agent_provider import ProviderTurn, build_provider_identity
from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    DtaDiagnosis,
    EvidenceSource,
    FaultDomain,
    FaultMechanism,
    RunbookId,
    Terminal,
    semantic_sha256,
)
from ecomsre.dta_v2.evaluation_agents import run_full_context_agent
from ecomsre.dta_v2.evaluation_contracts import (
    AgentVisibleReplayCase,
    EvaluationArm,
    EvaluationSplit,
    EvaluatorCaseTruth,
    ReplayObservationFixture,
    ScenarioFamily,
)
from ecomsre.dta_v2.evaluation_runner import (
    execute_evaluation_arm,
    score_and_persist_evaluation_execution,
)
from ecomsre.dta_v2.evaluation_dataset import load_public_evaluation_dataset
from ecomsre.dta_v2.read_tools import InvestigationReadTools
from ecomsre.dta_v2.evaluation_replay import (
    ReplayCaseReadBackend,
    build_materialization_request,
)
from ecomsre.dta_v2.registry import load_runbook_registry, load_scenario_registry
from ecomsre.dta_v2.tool_contracts import (
    DiagnosticLogRecord,
    EndpointState,
    HealthState,
    LogSeverity,
    MetricKind,
    MetricRecord,
    MetricUnit,
    ResourceSample,
    ResourceUsageRecord,
    RuntimeRecord,
    RuntimeState,
    SpanRelationship,
    SpanStatus,
    ToolName,
    TraceNeighborhoodRecord,
    ToolErrorCode,
    ObservationStatus,
    build_trace_neighborhood_request,
)


ROOT = Path(__file__).resolve().parents[2]
START = datetime(2026, 8, 16, 7, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=5)
RUN_ID = "a" * 32


def _fixture(tool: ToolName, records: tuple[object, ...]) -> ReplayObservationFixture:
    record = records[0]
    service = getattr(
        record,
        "service",
        getattr(record, "anchor_service", getattr(record, "logical_service", None)),
    )
    assert isinstance(service, str)
    payload = {
        "schema_version": "dta-v2.replay-observation-fixture.v1",
        "tool": tool,
        "service_scope": (service,),
        "records": records,
        "truncated": False,
        "error_code": None,
    }
    draft = ReplayObservationFixture.model_construct(
        **payload, fixture_sha256="0" * 64
    )
    return ReplayObservationFixture.model_validate(
        {
            **payload,
            "fixture_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"fixture_sha256"})
            ),
        }
    )


def _case() -> AgentVisibleReplayCase:
    observations = tuple(
        sorted(
            (
                _fixture(
                    ToolName.QUERY_METRICS,
                    (
                        MetricRecord(
                            service="payment",
                            metric_kind=MetricKind.ERROR_RATE,
                            value=0.75,
                            unit=MetricUnit.RATIO,
                            sample_count=30,
                        ),
                        MetricRecord(
                            service="payment",
                            metric_kind=MetricKind.REQUEST_SUPPORT,
                            value=120.0,
                            unit=MetricUnit.COUNT,
                            sample_count=30,
                        ),
                    ),
                ),
                _fixture(
                    ToolName.SEARCH_LOGS,
                    (
                        DiagnosticLogRecord(
                            observed_at=START + timedelta(minutes=2),
                            service="payment",
                            severity=LogSeverity.ERROR,
                            message="charge requests fail after a bounded configuration change",
                        ),
                    ),
                ),
                _fixture(
                    ToolName.QUERY_TRACE_NEIGHBORHOOD,
                    (
                        TraceNeighborhoodRecord(
                            anchor_service="payment",
                            service_path=("checkout", "payment"),
                            relationship=SpanRelationship.CHILD,
                            service="payment",
                            parent_service="checkout",
                            operation="charge",
                            status=SpanStatus.ERROR,
                            duration_ms=12.0,
                            first_error_location=True,
                        ),
                        TraceNeighborhoodRecord(
                            anchor_service="payment",
                            service_path=("checkout",),
                            relationship=SpanRelationship.ROOT,
                            service="checkout",
                            parent_service=None,
                            operation="place-order",
                            status=SpanStatus.OK,
                            duration_ms=8.0,
                            first_error_location=False,
                        ),
                    ),
                ),
                _fixture(
                    ToolName.INSPECT_SERVICE_RUNTIME,
                    (
                        RuntimeRecord(
                            logical_service="payment",
                            owned_container_present=True,
                            state=RuntimeState.RUNNING,
                            health=HealthState.HEALTHY,
                            restart_count=0,
                            exit_code=0,
                            endpoint_probe_performed=False,
                            endpoint_state=EndpointState.NOT_APPLICABLE,
                        ),
                    ),
                ),
                _fixture(
                    ToolName.INSPECT_RESOURCE_USAGE,
                    (
                        ResourceUsageRecord(
                            logical_service="payment",
                            sampling_window_seconds=3,
                            samples=(
                                ResourceSample(
                                    offset_ms=0,
                                    cpu_percent=1.0,
                                    memory_bytes=10_000_000,
                                ),
                                ResourceSample(
                                    offset_ms=3000,
                                    cpu_percent=2.0,
                                    memory_bytes=10_100_000,
                                ),
                            ),
                            memory_slope_bytes_per_second=33_333.0,
                        ),
                    ),
                ),
            ),
            key=lambda item: item.tool.value,
        )
    )
    payload = {
        "schema_version": "dta-v2.agent-visible-replay-case.v1",
        "case_id": "dta-case-001",
        "scenario_id": "dta-dev-001",
        "captured_started_at": START,
        "captured_ended_at": END,
        "observations": observations,
        "full_context_tools": tuple(item.tool for item in observations[:4]),
    }
    draft = AgentVisibleReplayCase.model_construct(**payload, case_sha256="0" * 64)
    return AgentVisibleReplayCase.model_validate(
        {
            **payload,
            "case_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"case_sha256"})
            ),
        }
    )


class FullContextProvider:
    def __init__(self) -> None:
        self.identity = build_provider_identity("gpt-5.4-mini-2026-03-17")
        self.attempted_calls = 0
        self.transcript_lengths: list[int] = []
        self.read_tools_enabled: list[bool] = []

    def investigation_turn(self, *, context, transcript, read_tools_enabled):
        self.attempted_calls += 1
        self.transcript_lengths.append(len(transcript))
        self.read_tools_enabled.append(read_tools_enabled)
        diagnosis = DtaDiagnosis(
            schema_version="dta-v2.diagnosis.v1",
            run_id=context.run_id,
            terminal=Terminal.COMPLETED,
            root_service="payment",
            root_entity_ref="service:payment",
            fault_domain=FaultDomain.CONFIGURATION,
            mechanism=FaultMechanism.CONFIGURATION_ERROR,
            confidence=0.9,
            supporting_evidence_refs=(
                f"evidence://{context.run_id}/metrics/0003",
                f"evidence://{context.run_id}/traces/0004",
            ),
            contradicting_evidence_refs=(),
            evidence_source_types=(EvidenceSource.METRICS, EvidenceSource.TRACES),
            uncertainties=(),
            summary="Metrics and traces support a payment configuration failure.",
        )
        return ProviderTurn(
            function_name="submit_dta_diagnosis",
            tool_call_id="one-shot-diagnosis",
            raw_response={"id": "one-shot-diagnosis"},
            raw_response_sha256=semantic_sha256({"id": "one-shot-diagnosis"}),
            raw_arguments=diagnosis.model_dump(mode="json"),
            usage=ProviderUsage(input_tokens=100, output_tokens=20, total_tokens=120),
            monotonic_latency_ms=10,
            diagnosis=diagnosis,
        )

    def action_selection_turn(self, *, diagnosis, candidate_view):
        self.attempted_calls += 1
        assert diagnosis.root_service == "payment"
        assert candidate_view.write_candidates[0].runbook_id is RunbookId.ROLLBACK_CONFIGURATION
        decision = ActionSelectionDecision(
            schema_version="dta-v2.action-selection-decision.v1",
            disposition=ActionDisposition.EXECUTE_RUNBOOK,
            runbook_id=RunbookId.ROLLBACK_CONFIGURATION,
            target_service="payment",
            parameters=(),
            supporting_evidence_refs=diagnosis.supporting_evidence_refs,
            rationale="The only safe candidate matches the cited sources.",
        )
        return ProviderTurn(
            function_name="submit_dta_action_selection",
            tool_call_id="one-shot-action",
            raw_response={"id": "one-shot-action"},
            raw_response_sha256=semantic_sha256({"id": "one-shot-action"}),
            raw_arguments=decision.model_dump(mode="json"),
            usage=ProviderUsage(input_tokens=50, output_tokens=10, total_tokens=60),
            monotonic_latency_ms=5,
            action_selection=decision,
        )


def test_replay_backend_materializes_all_five_typed_tools() -> None:
    case = _case()
    backend = ReplayCaseReadBackend(case)

    for fixture in case.observations:
        request = build_materialization_request(
            run_id=RUN_ID,
            case=case,
            fixture=fixture,
        )
        result = backend.execute(request)
        assert result.records
        assert all(type(item) is type(fixture.records[0]) for item in result.records)
    assert backend.call_count == 5


def test_replay_materializes_typed_failure_and_trace_path_anchor() -> None:
    case = _case()
    payload = {
        "schema_version": "dta-v2.replay-observation-fixture.v1",
        "tool": ToolName.QUERY_METRICS,
        "service_scope": ("payment",),
        "records": (),
        "truncated": False,
        "error_code": ToolErrorCode.SOURCE_SCHEMA_INVALID,
    }
    failed = ReplayObservationFixture.model_validate(
        {**payload, "fixture_sha256": semantic_sha256(payload)}
    )
    request = build_materialization_request(
        run_id=RUN_ID, case=case, fixture=failed
    )
    assert request.tool is ToolName.QUERY_METRICS

    backend = ReplayCaseReadBackend(case)
    upstream = build_trace_neighborhood_request(
        run_id=RUN_ID,
        service="checkout",
        started_at=case.captured_started_at,
        ended_at=case.captured_ended_at,
        max_spans=1,
    )
    result = backend.execute(upstream)
    assert result.records
    assert all(item.anchor_service == "checkout" for item in result.records)
    assert all("checkout" in item.service_path for item in result.records)
    assert result.records[0].service == "payment"
    assert result.records[0].first_error_location is True


def test_promoted_payment_full_context_trace_replays_without_schema_drift() -> None:
    _, loaded = load_public_evaluation_dataset(ROOT / "config/dta-v2/evaluation")
    case = next(item.case for item in loaded if item.case.case_id == "dta-case-001")
    fixture = next(
        item
        for item in case.observations
        if item.tool is ToolName.QUERY_TRACE_NEIGHBORHOOD
    )
    request = build_materialization_request(
        run_id=RUN_ID,
        case=case,
        fixture=fixture,
    )

    observation = InvestigationReadTools(
        run_id=RUN_ID,
        backend=ReplayCaseReadBackend(case),
    ).dispatch(request)

    assert observation.status is ObservationStatus.SUCCESS
    assert observation.error_code is None
    assert any(item.first_error_location for item in observation.results)


def test_full_context_arm_uses_four_materialized_observations_and_zero_agent_reads() -> None:
    case = _case()
    scenario = next(item for item in load_scenario_registry(
        ROOT / "config/dta-v2/scenarios/agent-visible"
    ).scenarios if item.scenario_id == case.scenario_id)
    context = build_alert_context(
        scenario=scenario,
        run_id=RUN_ID,
        started_at=case.captured_started_at,
        ended_at=case.captured_ended_at,
    )
    provider = FullContextProvider()

    result = run_full_context_agent(
        case=case,
        context=context,
        backend=ReplayCaseReadBackend(case),
        registry=load_runbook_registry(ROOT / "config/dta-v2/runbooks"),
        provider=provider,
    )

    assert result.agent_read_tool_dispatches == 0
    assert result.context_materialization_reads == 4
    assert result.agent_result.terminal is AgentRunTerminal.COMPLETED
    assert result.agent_result.action_proposal is not None
    assert result.agent_result.action_proposal.runbook_id is RunbookId.ROLLBACK_CONFIGURATION
    assert result.agent_result.read_tool_dispatch_count == 4
    assert provider.transcript_lengths == [4]
    assert provider.read_tools_enabled == [False]
    assert provider.attempted_calls == 2


def test_evaluation_runner_scores_after_agent_execution_and_persists_private(
    tmp_path: Path,
) -> None:
    case = _case()
    scenario = next(
        item
        for item in load_scenario_registry(
            ROOT / "config/dta-v2/scenarios/agent-visible"
        ).scenarios
        if item.scenario_id == case.scenario_id
    )
    context = build_alert_context(
        scenario=scenario,
        run_id=RUN_ID,
        started_at=case.captured_started_at,
        ended_at=case.captured_ended_at,
    )
    truth_payload = {
        "schema_version": "dta-v2.evaluator-case-truth.v1",
        "case_id": case.case_id,
        "split": EvaluationSplit.DEVELOPMENT,
        "scenario_family": ScenarioFamily.PAYMENT,
        "meaningful_observation_differences": ("fault_strength",),
        "expected_terminal": Terminal.COMPLETED,
        "expected_root_service": "payment",
        "expected_fault_domain": FaultDomain.CONFIGURATION,
        "expected_mechanism": FaultMechanism.CONFIGURATION_ERROR,
        "expected_disposition": ActionDisposition.EXECUTE_RUNBOOK,
        "expected_runbook": RunbookId.ROLLBACK_CONFIGURATION,
        "expected_evidence_sources": (
            EvidenceSource.METRICS,
            EvidenceSource.TRACES,
        ),
    }
    truth = EvaluatorCaseTruth.model_validate(
        {**truth_payload, "truth_sha256": semantic_sha256(truth_payload)}
    )

    execution = execute_evaluation_arm(
        case=case,
        context=context,
        arm=EvaluationArm.ONE_SHOT_FULL_CONTEXT,
        registry=load_runbook_registry(ROOT / "config/dta-v2/runbooks"),
        provider=FullContextProvider(),
    )
    entry = score_and_persist_evaluation_execution(
        execution=execution,
        truth=truth,
        execution_id="b" * 32,
        private_root=tmp_path,
    )

    assert entry.score.root_exact_match is True
    assert entry.score.runbook_top1_accuracy is True
    assert entry.prediction.read_tool_dispatches == 0
    assert entry.prediction.context_materialization_reads == 4
    assert entry.prohibited_action_counters.model_dump() == {
        "docker_calls": 0,
        "fault_injections": 0,
        "runbook_executions": 0,
        "executor_calls": 0,
        "verifier_calls": 0,
        "forward_writes": 0,
        "configuration_mutations": 0,
        "service_mutations": 0,
        "public_writes": 0,
    }
    assert (tmp_path / "agent/manifest.json").stat().st_mode & 0o777 == 0o600
