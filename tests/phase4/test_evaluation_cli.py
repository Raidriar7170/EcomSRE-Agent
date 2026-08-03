from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecomsre.phase4 import cli
from ecomsre.phase4.demo import build_domain_demo_report
from ecomsre.phase4 import evaluation


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_worker_probe_denies_evaluator_and_runtime_escape() -> None:
    probe = evaluation.run_worker_probe(PROJECT_ROOT)
    assert probe["isolated_sys_path"] is True
    assert probe["phase4_evaluator_read"] == "DENIED"
    assert probe["socket_connect"] == "DENIED"
    assert probe["subprocess_run"] == "DENIED"
    assert probe["os_open"] == "DENIED"


def test_evaluator_reads_truth_only_after_each_trace_returns(monkeypatch) -> None:
    returned: list[tuple[str, str]] = []
    truth_reads: list[tuple[str, str]] = []
    original_runner = evaluation._run_workflow_trace
    original_loader = evaluation._load_ground_truth

    def tracked_runner(project_root, case_id, variant):
        trace = original_runner(project_root, case_id, variant)
        returned.append((variant.value, case_id))
        return trace

    def tracked_loader(root, case_id):
        current_variant = returned[-1][0]
        assert returned == [*truth_reads, (current_variant, case_id)]
        truth = original_loader(root, case_id)
        truth_reads.append((current_variant, case_id))
        return truth

    monkeypatch.setattr(evaluation, "_run_workflow_trace", tracked_runner)
    monkeypatch.setattr(evaluation, "_load_ground_truth", tracked_loader)
    report = evaluation.run_domain_evaluation(PROJECT_ROOT)

    assert report["status"] == "COMPLETED"
    assert returned == truth_reads
    assert len(returned) == 10


def test_domain_evaluation_retains_ten_runs_and_all_required_metrics() -> None:
    report = evaluation.run_domain_evaluation(PROJECT_ROOT)
    assert report["schema_version"] == "phase4.domain-comparison-report.v1"
    assert report["run_count"] == 10
    assert len(report["run_results"]) == 10
    assert report["failure_denominator_policy"] == "all ten runs are retained"
    assert report["superiority_claim"] is False
    assert report["phase5_entered"] is False
    for name in (
        "Decision Accuracy",
        "Root Service Accuracy",
        "Domain Mechanism Accuracy",
        "Evidence Reference Validity",
        "Need-More-Evidence Accuracy",
        "Abstention Accuracy",
        "Decoy Resistance",
        "Schema Valid Rate",
        "DAG Validity",
        "Specialist Tool Isolation",
        "Budget Compliance",
    ):
        assert report["metrics"][name]["rate"] == 1.0


def test_ground_truth_loader_rejects_symlink_before_resolution(
    tmp_path: Path,
) -> None:
    case_id = "ranking-change-with-normal-search-sli"
    root = tmp_path / "ground-truth"
    root.mkdir()
    target = root / "target.json"
    target.write_bytes(
        (PROJECT_ROOT / "eval/phase4/ground-truth" / f"{case_id}.json").read_bytes()
    )
    (root / f"{case_id}.json").symlink_to(target.name)

    with pytest.raises(ValueError, match="symlink"):
        evaluation._load_ground_truth(root, case_id)


def test_compare_verify_and_demo_are_byte_deterministic(tmp_path: Path) -> None:
    report_path = tmp_path / "comparison.json"
    assert cli.main(["compare", "--output", str(report_path)]) == 0
    first_report = report_path.read_bytes()
    assert cli.main(["verify", "--report", str(report_path)]) == 0
    assert report_path.read_bytes() == first_report

    demo_path = tmp_path / "demo.json"
    assert cli.main(["demo", "--output", str(demo_path)]) == 0
    first_demo = demo_path.read_bytes()
    assert cli.main(["demo", "--output", str(demo_path)]) == 0
    assert demo_path.read_bytes() == first_demo
    payload = json.loads(first_demo)
    assert payload == build_domain_demo_report(PROJECT_ROOT)
    assert payload["remediation_disposition"] == "NO_SUPPORTED_REMEDIATION"
    assert payload["live_mutation"] is False
    assert payload["remediation_backend"] == "NONE"
