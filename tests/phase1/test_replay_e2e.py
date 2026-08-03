from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre.phase1.contracts import EvidenceSource, RCADecision
from evaluator_loader import load_phase1_evaluator

evaluation_module = load_phase1_evaluator()
EVALUATION_CASE_IDS = evaluation_module.EVALUATION_CASE_IDS
GROUND_TRUTH_ROOT = evaluation_module.GROUND_TRUTH_ROOT
PROJECT_ROOT = evaluation_module.PROJECT_ROOT
_load_ground_truth = evaluation_module._load_ground_truth
_run_scripted_report = evaluation_module._run_scripted_report
run_evaluation = evaluation_module.run_evaluation


EXPECTED = {
    "ad-partial-failure-complete": (
        "RCA_CONFIRMED",
        "ad",
        "runtime_configuration_failure",
    ),
    "ad-partial-failure-without-logs": (
        "RCA_CONFIRMED",
        "ad",
        "request_processing_failure",
    ),
    "ad-partial-failure-frontend-decoy": (
        "RCA_CONFIRMED",
        "ad",
        "request_processing_failure",
    ),
    "ad-change-with-normal-sli": ("ABSTAIN", None, None),
    "telemetry-insufficient": ("NEED_MORE_EVIDENCE", None, None),
    "no-real-incident": ("ABSTAIN", None, None),
    "recommendation-cache-failure": (
        "RCA_CONFIRMED",
        "recommendation",
        "cache_backend_timeout",
    ),
}


@pytest.mark.parametrize("case_id", tuple(EXPECTED))
def test_each_frozen_case_runs_through_the_real_replay_agent_pipeline(
    case_id: str,
) -> None:
    report = _run_scripted_report(PROJECT_ROOT, case_id)

    assert report.terminal_status == "COMPLETED"
    assert report.schema_valid is True
    assert report.evidence_references_valid is True
    assert report.final_rca is not None
    expected_decision, expected_root, expected_mechanism = EXPECTED[case_id]
    assert report.final_rca.decision.value == expected_decision
    if expected_decision == "RCA_CONFIRMED":
        assert report.final_rca.root_service == expected_root
        assert (
            report.final_rca.fault_mechanism.value
            if report.final_rca.fault_mechanism is not None
            else None
        ) == expected_mechanism

    evidence_by_ref = {
        item.evidence_ref: item for item in report.evidence_index
    }
    cited = {
        *report.final_rca.supporting_evidence,
        *report.final_rca.contradicting_evidence,
    }
    assert cited <= evidence_by_ref.keys()
    assert all(
        reference in evidence_by_ref
        for record in report.tool_call_records
        for reference in record.evidence_refs
    )

    if case_id == "ad-partial-failure-frontend-decoy":
        decoy = next(
            item
            for item in report.evidence_index
            if item.source is EvidenceSource.CHANGES
            and item.service == "frontend"
            and item.observation_type == "deployment"
        )
        assert decoy.evidence_ref not in report.final_rca.supporting_evidence
        assert report.final_rca.root_service == "ad"

    if report.final_rca.decision is RCADecision.RCA_CONFIRMED:
        assert len(
            {
                evidence_by_ref[reference].source
                for reference in report.final_rca.supporting_evidence
            }
        ) >= 2


def test_evaluator_reads_each_ground_truth_only_after_its_report_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    returned_reports: list[str] = []
    truth_reads: list[str] = []
    original_report_runner = evaluation_module._run_scripted_report
    original_truth_loader = evaluation_module._load_ground_truth

    def tracked_report_runner(project_root: Path, case_id: str):
        report = original_report_runner(project_root, case_id)
        returned_reports.append(case_id)
        return report

    def tracked_truth_loader(
        path: Path,
        case_id: str,
        *,
        allowed_root: Path | None = None,
    ):
        assert returned_reports == [*truth_reads, case_id]
        truth = original_truth_loader(
            path,
            case_id,
            allowed_root=allowed_root,
        )
        truth_reads.append(case_id)
        return truth

    monkeypatch.setattr(
        evaluation_module,
        "_run_scripted_report",
        tracked_report_runner,
    )
    monkeypatch.setattr(
        evaluation_module,
        "_load_ground_truth",
        tracked_truth_loader,
    )

    report = run_evaluation(PROJECT_ROOT)

    assert report["status"] == "PASSED"
    assert tuple(returned_reports) == EVALUATION_CASE_IDS
    assert tuple(truth_reads) == EVALUATION_CASE_IDS


def test_ground_truth_loader_is_separate_from_agent_visible_root() -> None:
    for case_id in EVALUATION_CASE_IDS:
        truth = _load_ground_truth(
            GROUND_TRUTH_ROOT / f"{case_id}.json",
            case_id,
        )
        assert truth.case_id == case_id
