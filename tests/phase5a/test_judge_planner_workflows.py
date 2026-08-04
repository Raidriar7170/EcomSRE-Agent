from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre.backends.replay import load_replay_case
from ecomsre.phase1.contracts import EvidenceSource
from ecomsre.phase5a.contracts import (
    DiagnosisDecisionV2,
    ObservationsStatusV2,
    UnifiedMechanismV2,
)
from ecomsre.phase5a.workflows import (
    DiagnosisVariantV2,
    run_diagnosis_v2,
)
from ecomsre.phase5a import workflows


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PHASE1_CASE_ROOT = PROJECT_ROOT / "config/phase1/replay-cases/agent-visible"
PHASE4_CASE_ROOT = PROJECT_ROOT / "config/phase4/replay-cases/agent-visible"


def run(case_id: str, variant: DiagnosisVariantV2, *, phase4: bool = False):
    replay_case = load_replay_case(
        PHASE4_CASE_ROOT if phase4 else PHASE1_CASE_ROOT,
        case_id,
    )
    return run_diagnosis_v2(
        project_root=PROJECT_ROOT,
        replay_case=replay_case,
        variant=variant,
    )


@pytest.mark.parametrize(
    "variant",
    (
        DiagnosisVariantV2.FIXED_SPECIALIST_V2,
        DiagnosisVariantV2.DYNAMIC_MULTI_AGENT_V2,
    ),
)
def test_missing_logs_is_typed_and_does_not_fail_workflow(
    variant: DiagnosisVariantV2,
) -> None:
    trace = run("ad-partial-failure-without-logs", variant)

    assert trace.status == "COMPLETED"
    assert trace.terminal_reason is None
    assert trace.final_diagnosis is not None
    assert trace.final_diagnosis.decision is DiagnosisDecisionV2.RCA_CONFIRMED
    assert trace.final_diagnosis.root_service == "ad"
    assert trace.final_diagnosis.fault_mechanism is (
        UnifiedMechanismV2.REQUEST_PROCESSING_FAILURE
    )
    logs = next(
        finding
        for finding in trace.findings
        if finding.source is EvidenceSource.LOGS
    )
    assert logs.observations_status is ObservationsStatusV2.SOURCE_UNAVAILABLE
    assert logs.missing_evidence


def test_dynamic_stages_metrics_then_two_evidence_driven_sources() -> None:
    dynamic = run(
        "ad-partial-failure-without-logs",
        DiagnosisVariantV2.DYNAMIC_MULTI_AGENT_V2,
    )
    fixed = run(
        "ad-partial-failure-without-logs",
        DiagnosisVariantV2.FIXED_SPECIALIST_V2,
    )

    assert dynamic.investigated_sources == (
        EvidenceSource.METRICS,
        EvidenceSource.LOGS,
        EvidenceSource.TRACES,
    )
    assert dynamic.admitted_graphs[0].initial_plan.nodes[0].source is (
        EvidenceSource.METRICS
    )
    assert tuple(
        node.source for node in dynamic.admitted_graphs[1].initial_plan.nodes
    ) == (EvidenceSource.LOGS, EvidenceSource.TRACES)
    assert dynamic.final_budget_snapshot.charged_tool_calls <= (
        fixed.final_budget_snapshot.charged_tool_calls
    )


def test_dynamic_stops_after_normal_metrics_and_abstains() -> None:
    trace = run(
        "ranking-change-with-normal-search-sli",
        DiagnosisVariantV2.DYNAMIC_MULTI_AGENT_V2,
        phase4=True,
    )

    assert trace.status == "COMPLETED"
    assert trace.final_diagnosis is not None
    assert trace.final_diagnosis.decision is DiagnosisDecisionV2.ABSTAIN
    assert trace.investigated_sources == (EvidenceSource.METRICS,)
    assert trace.final_budget_snapshot.charged_tool_calls == 1


@pytest.mark.parametrize("variant", tuple(DiagnosisVariantV2))
def test_post_charge_exception_is_retained_as_typed_failed_trace(
    monkeypatch: pytest.MonkeyPatch,
    variant: DiagnosisVariantV2,
) -> None:
    replay_case = load_replay_case(
        PHASE1_CASE_ROOT,
        "ad-partial-failure-complete",
    )

    def fail_after_admission(*_args, **_kwargs):
        raise RuntimeError("injected post-charge failure")

    monkeypatch.setattr(workflows, "_dispatch_tool", fail_after_admission)
    trace = run_diagnosis_v2(
        project_root=PROJECT_ROOT,
        replay_case=replay_case,
        variant=variant,
    )

    assert trace.status == "FAILED"
    assert trace.final_diagnosis is None
    assert trace.terminal_reason in {"RuntimeError", "ToolIsolationError"}
    assert trace.final_budget_snapshot.charged_tool_calls >= len(
        trace.tool_call_records
    )


@pytest.mark.parametrize(
    ("case_id", "decision", "root", "mechanism"),
    (
        (
            "ad-partial-failure-complete",
            DiagnosisDecisionV2.RCA_CONFIRMED,
            "ad",
            UnifiedMechanismV2.RUNTIME_CONFIGURATION_FAILURE,
        ),
        (
            "ad-partial-failure-frontend-decoy",
            DiagnosisDecisionV2.RCA_CONFIRMED,
            "ad",
            UnifiedMechanismV2.REQUEST_PROCESSING_FAILURE,
        ),
        (
            "recommendation-cache-failure",
            DiagnosisDecisionV2.RCA_CONFIRMED,
            "recommendation",
            UnifiedMechanismV2.CACHE_BACKEND_TIMEOUT,
        ),
        (
            "telemetry-insufficient",
            DiagnosisDecisionV2.NEED_MORE_EVIDENCE,
            None,
            None,
        ),
        (
            "no-real-incident",
            DiagnosisDecisionV2.ABSTAIN,
            None,
            None,
        ),
    ),
)
@pytest.mark.parametrize("variant", tuple(DiagnosisVariantV2))
def test_original_cases_share_one_v2_diagnosis_contract(
    case_id: str,
    decision: DiagnosisDecisionV2,
    root: str | None,
    mechanism: UnifiedMechanismV2 | None,
    variant: DiagnosisVariantV2,
) -> None:
    trace = run(case_id, variant)

    assert trace.status == "COMPLETED"
    assert trace.final_diagnosis is not None
    assert trace.final_diagnosis.decision is decision
    assert trace.final_diagnosis.root_service == root
    assert trace.final_diagnosis.fault_mechanism is mechanism


@pytest.mark.parametrize(
    ("case_id", "decision", "root", "mechanism"),
    (
        (
            "search-feature-freshness-lag-complete",
            DiagnosisDecisionV2.RCA_CONFIRMED,
            "feature",
            UnifiedMechanismV2.FEATURE_FRESHNESS_LAG,
        ),
        (
            "recommendation-model-feature-schema-mismatch",
            DiagnosisDecisionV2.RCA_CONFIRMED,
            "ranking",
            UnifiedMechanismV2.MODEL_FEATURE_SCHEMA_MISMATCH,
        ),
        (
            "search-ranking-configuration-frontend-decoy",
            DiagnosisDecisionV2.RCA_CONFIRMED,
            "ranking",
            UnifiedMechanismV2.RANKING_CONFIGURATION_FAILURE,
        ),
        (
            "recommendation-feature-evidence-insufficient",
            DiagnosisDecisionV2.NEED_MORE_EVIDENCE,
            None,
            None,
        ),
    ),
)
@pytest.mark.parametrize("variant", tuple(DiagnosisVariantV2))
def test_phase4_domain_cases_use_the_same_v2_contract(
    case_id: str,
    decision: DiagnosisDecisionV2,
    root: str | None,
    mechanism: UnifiedMechanismV2 | None,
    variant: DiagnosisVariantV2,
) -> None:
    trace = run(case_id, variant, phase4=True)

    assert trace.status == "COMPLETED"
    assert trace.final_diagnosis is not None
    assert trace.final_diagnosis.decision is decision
    assert trace.final_diagnosis.root_service == root
    assert trace.final_diagnosis.fault_mechanism is mechanism
