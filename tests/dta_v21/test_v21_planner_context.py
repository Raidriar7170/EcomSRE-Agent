from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ecomsre.dta_v2.read_tools import FakeReadBackend, InvestigationReadTools
from ecomsre.dta_v2.tool_contracts import (
    MetricKind,
    ToolName,
    build_inspect_service_runtime_request,
    build_query_metrics_request,
)
from ecomsre.dta_v2.v21.agent_contracts import build_alert_context_v21
from ecomsre.dta_v2.v21.context_projection import (
    MAX_INVESTIGATION_STATE_BYTES,
    build_evidence_index_v21,
    build_investigation_state_view_v21,
)
from ecomsre.dta_v2.v21.contracts import (
    EvidenceSourceV21,
    FaultDomainV21,
    FaultMechanismV21,
)
from ecomsre.dta_v2.v21.planner import validate_plan_decision_v21
from ecomsre.dta_v2.v21.planner_contracts import (
    DiagnosticHypothesisV21,
    HypothesisStatusV21,
    PlannerNextStepV21,
    build_evidence_plan_decision_v21,
)
from ecomsre.dta_v2.v21.registry import load_default_scenario_registries


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "1" * 32
START = datetime(2026, 8, 17, 1, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=5)


def _context():
    scenarios, _, _ = load_default_scenario_registries(ROOT)
    return build_alert_context_v21(
        scenario=scenarios.scenarios[4],
        run_id=RUN_ID,
        started_at=START,
        ended_at=END,
    )


def _hypothesis(*, unresolved=(EvidenceSourceV21.METRICS,)):
    return DiagnosticHypothesisV21(
        hypothesis_id="h1",
        root_service="payment",
        fault_domain=FaultDomainV21.CONFIGURATION,
        fault_mechanism=FaultMechanismV21.CONFIGURATION_ERROR,
        status=HypothesisStatusV21.ACTIVE,
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
        unresolved_evidence_sources=unresolved,
    )


def test_planner_request_requires_an_active_matching_gap_and_exact_context_scope() -> None:
    context = _context()
    request = build_query_metrics_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        metric_kinds=(MetricKind.ERROR_RATE,),
        max_results=4,
    )
    decision = build_evidence_plan_decision_v21(
        run_id=RUN_ID,
        turn_ordinal=1,
        hypotheses=(_hypothesis(),),
        next_step=PlannerNextStepV21.REQUEST_EVIDENCE,
        evidence_gap_sources=(EvidenceSourceV21.METRICS,),
        read_request=request,
        diagnosis=None,
        bounded_rationale="Metrics can distinguish the active configuration hypothesis.",
    )

    assert validate_plan_decision_v21(
        decision=decision,
        context=context,
        seen_request_sha256=(),
        completed_read_dispatches=0,
    ) == decision

    with pytest.raises(ValueError, match="active unresolved gap"):
        build_evidence_plan_decision_v21(
            run_id=RUN_ID,
            turn_ordinal=1,
            hypotheses=(_hypothesis(unresolved=(EvidenceSourceV21.RUNTIME,)),),
            next_step=PlannerNextStepV21.REQUEST_EVIDENCE,
            evidence_gap_sources=(EvidenceSourceV21.RUNTIME,),
            read_request=request,
            diagnosis=None,
            bounded_rationale="The request does not match the declared active gap.",
        )

    out_of_scope = build_query_metrics_request(
        run_id=RUN_ID,
        service="ad",
        started_at=START,
        ended_at=END,
        metric_kinds=(MetricKind.ERROR_RATE,),
        max_results=4,
    )
    out_of_scope_decision = build_evidence_plan_decision_v21(
        run_id=RUN_ID,
        turn_ordinal=1,
        hypotheses=(_hypothesis(),),
        next_step=PlannerNextStepV21.REQUEST_EVIDENCE,
        evidence_gap_sources=(EvidenceSourceV21.METRICS,),
        read_request=out_of_scope,
        diagnosis=None,
        bounded_rationale="Metrics can distinguish the active configuration hypothesis.",
    )
    with pytest.raises(ValueError, match="candidate services"):
        validate_plan_decision_v21(
            decision=out_of_scope_decision,
            context=context,
            seen_request_sha256=(),
            completed_read_dispatches=0,
        )


def test_duplicate_is_detected_and_budget_exhaustion_is_rejected_before_backend() -> None:
    context = _context()
    request = build_query_metrics_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        metric_kinds=(MetricKind.ERROR_RATE,),
        max_results=4,
    )
    decision = build_evidence_plan_decision_v21(
        run_id=RUN_ID,
        turn_ordinal=2,
        hypotheses=(_hypothesis(),),
        next_step=PlannerNextStepV21.REQUEST_EVIDENCE,
        evidence_gap_sources=(EvidenceSourceV21.METRICS,),
        read_request=request,
        diagnosis=None,
        bounded_rationale="The requested source remains unresolved.",
    )

    with pytest.raises(ValueError, match="duplicate"):
        validate_plan_decision_v21(
            decision=decision,
            context=context,
            seen_request_sha256=(request.normalized_request_sha256,),
            completed_read_dispatches=1,
        )
    with pytest.raises(ValueError, match="budget"):
        validate_plan_decision_v21(
            decision=decision,
            context=context,
            seen_request_sha256=(),
            completed_read_dispatches=4,
        )


def test_compact_state_is_deterministic_bounded_and_keeps_canonical_refs() -> None:
    context = _context()
    tools = InvestigationReadTools(run_id=RUN_ID, backend=FakeReadBackend.healthy())
    metrics = tools.dispatch(
        build_query_metrics_request(
            run_id=RUN_ID,
            service="payment",
            started_at=START,
            ended_at=END,
            metric_kinds=(MetricKind.ERROR_RATE, MetricKind.REQUEST_SUPPORT),
            max_results=4,
        )
    )
    runtime = tools.dispatch(
        build_inspect_service_runtime_request(
            run_id=RUN_ID,
            services=("payment",),
            max_results=1,
        )
    )
    snapshot = tools.snapshot()
    index = build_evidence_index_v21(snapshot)
    view = build_investigation_state_view_v21(
        context=context,
        hypotheses=(_hypothesis(unresolved=(EvidenceSourceV21.TRACES,)),),
        evidence_store=snapshot,
        newest_observation=runtime,
    )

    assert tuple(item.evidence_ref for item in index.entries) == (
        metrics.evidence_ref,
        runtime.evidence_ref,
    )
    assert view.evidence_index == index
    assert view.newest_observation is not None
    assert view.newest_observation.evidence_ref == runtime.evidence_ref
    assert view.next_turn_ordinal == 3
    assert view.serialized_size_bytes <= MAX_INVESTIGATION_STATE_BYTES
    assert view == build_investigation_state_view_v21(
        context=context,
        hypotheses=(_hypothesis(unresolved=(EvidenceSourceV21.TRACES,)),),
        evidence_store=snapshot,
        newest_observation=runtime,
    )
    assert ToolName.QUERY_METRICS in view.prior_tools
