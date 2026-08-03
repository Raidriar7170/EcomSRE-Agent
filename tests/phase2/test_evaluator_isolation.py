"""Security-boundary checks for the isolated Phase 2 replay worker."""

from pathlib import Path

import pytest

from .evaluator_loader import load_phase2_evaluator


comparison = load_phase2_evaluator()


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_worker_probe_denies_evaluator_ground_truth_and_runtime_escape() -> None:
    probe = comparison.run_worker_probe(PROJECT_ROOT)

    assert probe["isolated_sys_path"] is True
    assert {
        probe["import_ctypes"],
        probe["import_subprocess"],
        probe["import_multiprocessing"],
    } == {"ALLOWED"}
    assert all(
        value == "DENIED"
        for key, value in probe.items()
        if key
        not in {
            "isolated_sys_path",
            "import_ctypes",
            "import_subprocess",
            "import_multiprocessing",
        }
    )


def test_evaluator_reads_truth_only_after_each_isolated_trace_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    returned: list[tuple[str, str]] = []
    truth_reads: list[tuple[str, str]] = []
    original_runner = comparison._run_workflow_trace
    original_loader = comparison._load_ground_truth

    def tracked_runner(project_root, case_id, variant):
        trace = original_runner(project_root, case_id, variant)
        returned.append((variant.value, case_id))
        return trace

    def tracked_loader(path, case_id, *, allowed_root=None):
        current_variant = returned[-1][0]
        assert returned == [*truth_reads, (current_variant, case_id)]
        truth = original_loader(path, case_id, allowed_root=allowed_root)
        truth_reads.append((current_variant, case_id))
        return truth

    monkeypatch.setattr(comparison, "_run_workflow_trace", tracked_runner)
    monkeypatch.setattr(comparison, "_load_ground_truth", tracked_loader)

    report = comparison.run_comparison(PROJECT_ROOT, verify_baseline=False)

    assert report["status"] == "COMPLETED"
    assert returned == truth_reads
    assert len(returned) == 21
