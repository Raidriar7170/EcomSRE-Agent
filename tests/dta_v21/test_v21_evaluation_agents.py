from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ecomsre.dta_v2.agent_contracts import ProviderUsage
from ecomsre.dta_v2.read_tools import FakeReadBackend
from ecomsre.dta_v2.tool_contracts import (
    MetricKind,
    ReadToolRequest,
    build_inspect_service_runtime_request,
    build_query_metrics_request,
    build_search_logs_request,
    build_trace_neighborhood_request,
)
from ecomsre.dta_v2.v21.agent import AgentRunTerminalV21
from ecomsre.dta_v2.v21.agent_contracts import (
    ActionSelectionDecisionV21,
    AgentArmV21,
    build_alert_context_v21,
)
from ecomsre.dta_v2.v21.agent_provider import ProviderTurnV21
from ecomsre.dta_v2.v21.contracts import (
    ActionDispositionV21,
    DtaDiagnosisV21,
    EvidenceSourceV21,
    TerminalV21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.evaluation_agents import (
    build_evaluation_prediction_v21,
    execute_evaluation_arm_v21,
    score_and_persist_evaluation_execution_v21,
)
from ecomsre.dta_v2.v21.evaluation_contracts import (
    AgentVisibleReplayCaseV21,
    EvaluationArmV21,
    EvaluationSplitV21,
    EvaluatorCaseTruthV21,
    GeneralizationSliceV21,
    ReplayObservationFixtureV21,
    ScenarioFamilyV21,
)
from ecomsre.dta_v2.v21.identity import build_three_arm_identities_v21
from ecomsre.dta_v2.v21.registry import (
    load_default_runbook_registry,
    load_default_scenario_registries,
)


ROOT = Path(__file__).resolve().parents[2]
START = datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc)
END = START + timedelta(seconds=30)
RUN_ID = "d" * 32


def _digest(model_type, payload: Mapping[str, object], field: str) -> str:
    draft = model_type.model_construct(**payload, **{field: "0" * 64})
    return semantic_sha256(draft.model_dump(mode="json", exclude={field}))


def _requests() -> tuple[ReadToolRequest, ...]:
    return (
        build_inspect_service_runtime_request(
            run_id=RUN_ID, services=("email",), max_results=1
        ),
        build_query_metrics_request(
            run_id=RUN_ID,
            service="email",
            started_at=START,
            ended_at=END,
            metric_kinds=(MetricKind.ERROR_RATE,),
            max_results=1,
        ),
        build_trace_neighborhood_request(
            run_id=RUN_ID,
            service="email",
            started_at=START,
            ended_at=END,
            max_spans=4,
        ),
        build_search_logs_request(
            run_id=RUN_ID,
            service="email",
            started_at=START,
            ended_at=END,
            max_records=4,
        ),
    )


def _case() -> AgentVisibleReplayCaseV21:
    backend = FakeReadBackend.healthy()
    fixtures = []
    for request in _requests():
        result = backend.execute(request)
        service_scope = (
            request.services if hasattr(request, "services") else (request.service,)
        )
        payload = {
            "schema_version": "dta-v21.replay-observation-fixture.v1",
            "tool": request.tool,
            "service_scope": tuple(sorted(service_scope)),
            "records": result.records,
            "truncated": result.truncated,
            "error_code": None,
        }
        fixtures.append(
            ReplayObservationFixtureV21.model_validate(
                {
                    **payload,
                    "fixture_sha256": _digest(
                        ReplayObservationFixtureV21, payload, "fixture_sha256"
                    ),
                }
            )
        )
    fixtures.sort(key=lambda item: item.tool.value)
    case_payload: dict[str, object] = {
        "schema_version": "dta-v21.agent-visible-replay-case.v1",
        "case_id": "dta21-case-001",
        "scenario_id": "dta21-dev-006",
        "captured_started_at": START,
        "captured_ended_at": END,
        "observations": tuple(fixtures),
        "full_context_tools": tuple(item.tool for item in fixtures),
    }
    return AgentVisibleReplayCaseV21.model_validate(
        {
            **case_payload,
            "case_sha256": _digest(
                AgentVisibleReplayCaseV21, case_payload, "case_sha256"
            ),
        }
    )


def _truth() -> EvaluatorCaseTruthV21:
    payload = {
        "schema_version": "dta-v21.evaluator-case-truth.v1",
        "case_id": "dta21-case-001",
        "split": EvaluationSplitV21.DEVELOPMENT,
        "scenario_family": ScenarioFamilyV21.NO_FAULT,
        "generalization_slice": GeneralizationSliceV21.NO_FAULT,
        "meaningful_observation_differences": ("load_level",),
        "expected_terminal": TerminalV21.COMPLETED,
        "expected_root_service": None,
        "expected_fault_domain": None,
        "expected_mechanism": None,
        "expected_disposition": ActionDispositionV21.NO_ACTION,
        "expected_runbook": None,
        "expected_evidence_sources": (EvidenceSourceV21.RUNTIME,),
    }
    return EvaluatorCaseTruthV21.model_validate(
        {
            **payload,
            "truth_sha256": _digest(EvaluatorCaseTruthV21, payload, "truth_sha256"),
        }
    )


class _NoFaultOneShotProvider:
    def __init__(self) -> None:
        self.attempted_calls = 0
        self.identity = next(
            identity
            for identity in build_three_arm_identities_v21(
                model_id="gpt-5.4-mini-2026-03-17",
                max_completion_tokens=1600,
            )
            if identity.arm is AgentArmV21.ONE_SHOT_FULL_CONTEXT
        )

    def investigation_turn(self, *, context, visible_state, read_tools_enabled):
        self.attempted_calls += 1
        assert not read_tools_enabled
        evidence = next(
            item
            for item in visible_state.observations
            if item.source.value == EvidenceSourceV21.RUNTIME.value
        )
        diagnosis = DtaDiagnosisV21(
            schema_version="dta-v21.diagnosis.v1",
            run_id=context.run_id,
            terminal=TerminalV21.COMPLETED,
            root_service=None,
            root_entity_ref=None,
            fault_domain=None,
            mechanism=None,
            confidence=None,
            supporting_evidence_refs=(evidence.evidence_ref,),
            contradicting_evidence_refs=(),
            evidence_source_types=(EvidenceSourceV21.RUNTIME,),
            uncertainties=(),
            summary="All visible services are healthy, so no action is warranted.",
        )
        return ProviderTurnV21(
            function_name="scripted_investigation",
            tool_call_id="scripted-1",
            raw_response_sha256=semantic_sha256({"id": "scripted-1"}),
            usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            monotonic_latency_ms=1,
            diagnosis=diagnosis,
        )

    def action_selection_turn(self, *, diagnosis, resolved_evidence, candidate_view):
        self.attempted_calls += 1
        del diagnosis, candidate_view
        return ProviderTurnV21(
            function_name="scripted_action_selection",
            tool_call_id="scripted-2",
            raw_response_sha256=semantic_sha256({"id": "scripted-2"}),
            usage=ProviderUsage(input_tokens=8, output_tokens=4, total_tokens=12),
            monotonic_latency_ms=1,
            action_selection=ActionSelectionDecisionV21(
                schema_version="dta-v21.action-selection-decision.v1",
                disposition=ActionDispositionV21.NO_ACTION,
                runbook_id=None,
                target_service=None,
                parameters=(),
                supporting_evidence_refs=tuple(
                    item.evidence_ref for item in resolved_evidence.evidence
                ),
                rationale="Healthy evidence supports no action.",
            ),
        )


def test_one_shot_execution_is_truth_free_then_scores_privately(tmp_path: Path) -> None:
    case = _case()
    scenarios, _, _ = load_default_scenario_registries(ROOT)
    scenario = next(
        item for item in scenarios.scenarios if item.scenario_id == case.scenario_id
    )
    context = build_alert_context_v21(
        scenario=scenario,
        run_id=RUN_ID,
        started_at=case.captured_started_at,
        ended_at=case.captured_ended_at,
    )

    execution = execute_evaluation_arm_v21(
        case=case,
        context=context,
        arm=EvaluationArmV21.ONE_SHOT_FULL_CONTEXT,
        registry=load_default_runbook_registry(ROOT),
        provider=_NoFaultOneShotProvider(),
    )
    prediction = build_evaluation_prediction_v21(execution)

    assert not hasattr(execution, "truth")
    assert execution.agent_result.terminal is AgentRunTerminalV21.COMPLETED
    assert prediction.disposition is ActionDispositionV21.NO_ACTION
    assert prediction.context_materialization_reads == 4
    assert prediction.read_tool_dispatches == 0
    assert set(prediction.requested_evidence_sources) == {
        EvidenceSourceV21.METRICS,
        EvidenceSourceV21.LOGS,
        EvidenceSourceV21.TRACES,
        EvidenceSourceV21.RUNTIME,
    }

    private_root = tmp_path / "entry"
    entry = score_and_persist_evaluation_execution_v21(
        execution=execution,
        truth=_truth(),
        execution_id="e" * 32,
        private_root=private_root,
    )

    assert entry.score.protocol_acceptance
    assert entry.score.no_action_accuracy
    assert entry.score.evidence_validity
    assert (private_root.stat().st_mode & 0o777) == 0o700
    assert all(
        (path.stat().st_mode & 0o777) == 0o600 for path in private_root.iterdir()
    )
