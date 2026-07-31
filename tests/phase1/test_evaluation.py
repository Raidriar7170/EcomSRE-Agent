from __future__ import annotations

import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from evaluator_loader import load_phase1_evaluator

evaluation_module = load_phase1_evaluator()
GROUND_TRUTH_ROOT = evaluation_module.GROUND_TRUTH_ROOT
PROJECT_ROOT = evaluation_module.PROJECT_ROOT
_case_semantic_projection = evaluation_module._case_semantic_projection
_load_ground_truth = evaluation_module._load_ground_truth
_run_scripted_report = evaluation_module._run_scripted_report
run_evaluation = evaluation_module.run_evaluation


METRIC_KEYS = {
    "Decision Accuracy",
    "Schema Valid Rate",
    "Root Service Accuracy",
    "Fault Mechanism Accuracy",
    "Evidence Reference Validity",
    "Abstention Accuracy",
    "Decoy Resistance",
    "Average Tool Calls",
    "Token Usage",
    "Wall-clock Latency",
}
RATE_KEYS = {
    "Decision Accuracy",
    "Schema Valid Rate",
    "Root Service Accuracy",
    "Fault Mechanism Accuracy",
    "Evidence Reference Validity",
    "Abstention Accuracy",
    "Decoy Resistance",
}
EXPECTED_DENOMINATORS = {
    "Decision Accuracy": 7,
    "Schema Valid Rate": 7,
    "Root Service Accuracy": 4,
    "Fault Mechanism Accuracy": 4,
    "Evidence Reference Validity": 7,
    "Abstention Accuracy": 3,
    "Decoy Resistance": 1,
}


def _abstain_truth(case_id: str = "case-one") -> dict[str, object]:
    return {
        "schema_version": "phase1.ground-truth.v1",
        "case_id": case_id,
        "expected_decision": "ABSTAIN",
        "expected_root_service": None,
        "expected_fault_mechanism": None,
        "decoys": [],
    }


def _write_truth(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_ground_truth_loader_rejects_duplicate_nonfinite_and_wrong_types(
    tmp_path: Path,
) -> None:
    path = tmp_path / "case-one.json"
    path.write_text(
        '{"schema_version":"phase1.ground-truth.v1",'
        '"schema_version":"phase1.ground-truth.v1",'
        '"case_id":"case-one","expected_decision":"ABSTAIN",'
        '"expected_root_service":null,"expected_fault_mechanism":null,'
        '"decoys":[]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        _load_ground_truth(path, "case-one")

    path.write_text(
        '{"schema_version":"phase1.ground-truth.v1",'
        '"case_id":"case-one","expected_decision":NaN,'
        '"expected_root_service":null,"expected_fault_mechanism":null,'
        '"decoys":[]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-finite"):
        _load_ground_truth(path, "case-one")

    payload = _abstain_truth()
    payload["case_id"] = 1
    _write_truth(path, payload)
    with pytest.raises((TypeError, ValueError)):
        _load_ground_truth(path, "case-one")


def test_ground_truth_loader_rejects_symlink_and_oversize(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.json"
    _write_truth(target, _abstain_truth())
    symlink = tmp_path / "case-one.json"
    symlink.symlink_to(target)
    with pytest.raises(ValueError, match="regular|open"):
        _load_ground_truth(symlink, "case-one")
    symlink.unlink()
    symlink.write_bytes(b"{" + b" " * (64 * 1024) + b"}")
    with pytest.raises(ValueError, match="limit"):
        _load_ground_truth(symlink, "case-one")


def test_ground_truth_loader_rejects_symlinked_parent_directory(
    tmp_path: Path,
) -> None:
    real_root = tmp_path / "real-ground-truth"
    real_root.mkdir()
    _write_truth(real_root / "case-one.json", _abstain_truth())
    linked_root = tmp_path / "linked-ground-truth"
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(ValueError, match="directory|symlink|capability"):
        _load_ground_truth(linked_root / "case-one.json", "case-one")


def test_ground_truth_loader_rejects_path_replacement_during_fd_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "case-one.json"
    replacement = tmp_path / "replacement.json"
    _write_truth(path, _abstain_truth())
    _write_truth(replacement, _abstain_truth())
    original_read = os.read
    replaced = False

    def racing_read(file_descriptor: int, count: int) -> bytes:
        nonlocal replaced
        content = original_read(file_descriptor, count)
        if not replaced:
            replaced = True
            os.replace(replacement, path)
        return content

    monkeypatch.setattr(evaluation_module.os, "read", racing_read)
    with pytest.raises(ValueError, match="changed|replaced"):
        _load_ground_truth(path, "case-one")
    assert replaced is True


def test_ground_truth_bounded_read_rejects_growth_after_pre_fstat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "case-one.json"
    _write_truth(path, _abstain_truth())
    original_read = os.read
    grew = False

    def growing_read(file_descriptor: int, count: int) -> bytes:
        nonlocal grew
        if not grew:
            grew = True
            with path.open("ab") as stream:
                stream.write(b" " * (65 * 1024))
        return original_read(file_descriptor, count)

    monkeypatch.setattr(evaluation_module.os, "read", growing_read)
    with pytest.raises(ValueError, match="limit|changed"):
        _load_ground_truth(path, "case-one")
    assert grew is True


def test_clean_agent_subprocess_denies_evaluator_import_and_all_gt_reads() -> None:
    probe = evaluation_module._run_clean_agent_probe(PROJECT_ROOT)
    assert probe == {
        "isolated_sys_path": True,
        "import_eval": "DENIED",
        "builtins_open": "DENIED",
        "io_core_open": "DENIED",
        "io_open": "DENIED",
        "os_open": "DENIED",
        "posix_open": "DENIED",
        "path_open": "DENIED",
        "path_read_bytes": "DENIED",
        "path_read_text": "DENIED",
        "import_ctypes": "ALLOWED",
        "import_subprocess": "ALLOWED",
        "import_multiprocessing": "ALLOWED",
        "ctypes_cdll": "DENIED",
        "subprocess_popen": "DENIED",
        "subprocess_run": "DENIED",
        "multiprocessing_start": "DENIED",
        "os_system": "DENIED",
        "os_fork": "DENIED",
        "os_posix_spawn": "DENIED",
        "socket_connect": "DENIED",
        "os_listdir": "DENIED",
        "os_scandir": "DENIED",
        "path_iterdir": "DENIED",
        "path_glob": "DENIED",
    }


def test_ground_truth_root_closes_new_child_fd_when_fstat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = os.open
    original_close = os.close
    original_fstat = os.fstat
    opened: list[int] = []
    closed: list[int] = []

    def tracked_open(*args: object, **kwargs: object) -> int:
        descriptor = original_open(*args, **kwargs)  # type: ignore[arg-type]
        opened.append(descriptor)
        return descriptor

    def tracked_close(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)

    def failing_child_fstat(descriptor: int) -> os.stat_result:
        if len(opened) >= 2 and descriptor == opened[-1]:
            raise OSError("forced child fstat failure")
        return original_fstat(descriptor)

    with monkeypatch.context() as scoped:
        scoped.setattr(evaluation_module.os, "open", tracked_open)
        scoped.setattr(evaluation_module.os, "close", tracked_close)
        scoped.setattr(evaluation_module.os, "fstat", failing_child_fstat)
        with pytest.raises(OSError, match="forced child"):
            evaluation_module._open_ground_truth_root(tmp_path)
    leaked = set(opened) - set(closed)
    for descriptor in leaked:
        original_close(descriptor)
    assert not leaked


def test_agent_worker_request_rejects_oversized_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["worker"],
        returncode=0,
        stdout=b"x" * (4 * 1024 * 1024 + 1),
        stderr=b"",
    )
    monkeypatch.setattr(
        evaluation_module.subprocess,
        "run",
        lambda *_args, **_kwargs: completed,
    )
    with pytest.raises(ValueError, match="size limit"):
        evaluation_module._agent_worker_request(
            PROJECT_ROOT,
            {"mode": "probe", "project_root": str(PROJECT_ROOT)},
        )


def test_darwin_os_sandbox_denies_native_and_descendant_gt_reads() -> None:
    if evaluation_module.sys.platform != "darwin":
        pytest.skip("Darwin sandbox-exec backend only")
    assert evaluation_module._run_os_sandbox_probe(PROJECT_ROOT) == {
        "ctypes_open": "DENIED",
        "subprocess_cat": "DENIED",
        "os_listdir": "DENIED",
    }


def test_agent_worker_platform_route_fails_closed_without_sandbox_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evaluation_module.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="sandbox backend"):
        evaluation_module._agent_worker_command(PROJECT_ROOT)


def test_semantic_projection_keeps_all_non_timing_report_evidence() -> None:
    report = _run_scripted_report(PROJECT_ROOT, "ad-partial-failure-complete")
    baseline = _case_semantic_projection(
        "ad-partial-failure-complete",
        report,
    )
    payload = report.model_dump(mode="json")

    for mutation in (
        lambda item: item["model_call_records"][0]["response"].__setitem__(
            "provider_name", "mutated-provider"
        ),
        lambda item: item["model_call_records"][0].__setitem__(
            "status", "ERROR"
        ),
        lambda item: item["tool_call_records"][0].__setitem__(
            "status", "ERROR"
        ),
        lambda item: item["tool_call_records"][0].__setitem__(
            "error_code", "TIMEOUT"
        ),
        lambda item: item.__setitem__("terminal_error_code", "TIMEOUT"),
        lambda item: item["model_configuration"].__setitem__(
            "model_name", "mutated-model"
        ),
        lambda item: item["evidence_index"][0].__setitem__(
            "summary", "Mutated evidence payload."
        ),
        lambda item: item["evidence_index"][0].__setitem__(
            "raw_artifact_sha256", "f" * 64
        ),
    ):
        candidate = deepcopy(payload)
        mutation(candidate)
        assert (
            evaluation_module._semantic_projection_from_payload(
                "ad-partial-failure-complete",
                candidate,
            )
            != baseline
        )


def test_semantic_projection_excludes_only_runtime_timing_fields() -> None:
    report = _run_scripted_report(PROJECT_ROOT, "ad-partial-failure-complete")
    baseline = _case_semantic_projection(
        "ad-partial-failure-complete",
        report,
    )
    payload = report.model_dump(mode="json")
    payload["started_at"] = "2099-01-01T00:00:00Z"
    payload["ended_at"] = "2099-01-01T00:00:01Z"
    payload["monotonic_duration_seconds"] = 999.0
    for record in payload["model_call_records"]:
        record["started_at"] = "2099-01-01T00:00:00Z"
        record["ended_at"] = "2099-01-01T00:00:01Z"
        record["monotonic_duration_seconds"] = 999.0
        if record["response"] is not None:
            record["response"]["started_at"] = "2099-01-01T00:00:00Z"
            record["response"]["ended_at"] = "2099-01-01T00:00:01Z"
            record["response"]["monotonic_duration_seconds"] = 999.0
    for record in payload["tool_call_records"]:
        record["started_at"] = "2099-01-01T00:00:00Z"
        record["ended_at"] = "2099-01-01T00:00:01Z"
        record["monotonic_duration_seconds"] = 999.0

    assert (
        evaluation_module._semantic_projection_from_payload(
            "ad-partial-failure-complete",
            payload,
        )
        == baseline
    )
    # Evidence observation time remains substantive and changes the projection.
    payload["evidence_index"][0]["started_at"] = "2098-01-01T00:00:00Z"
    assert (
        evaluation_module._semantic_projection_from_payload(
            "ad-partial-failure-complete",
            payload,
        )
        != baseline
    )


def test_evaluation_report_has_exact_metrics_raw_counts_and_case_values() -> None:
    report = run_evaluation(PROJECT_ROOT)

    assert report["schema_version"] == "phase1.evaluation-report.v1"
    assert report["status"] == "PASSED"
    metrics = report["metrics"]
    assert set(metrics) == METRIC_KEYS
    for name in RATE_KEYS:
        metric = metrics[name]
        assert type(metric["numerator"]) is int
        assert type(metric["denominator"]) is int
        assert metric["numerator"] == metric["denominator"]
        assert metric["denominator"] == EXPECTED_DENOMINATORS[name]

    case_ids = [item["case_id"] for item in report["case_results"]]
    assert len(case_ids) == len(set(case_ids)) == 7
    assert set(case_ids) == set(metrics["Average Tool Calls"]["per_case"])
    assert set(case_ids) == set(metrics["Token Usage"]["per_case"])
    assert set(case_ids) == set(metrics["Wall-clock Latency"]["per_case"])
    for name in ("Average Tool Calls", "Token Usage"):
        metric = metrics[name]
        assert type(metric["total"]) is int
        assert type(metric["average"]) is float
        assert metric["total"] == sum(metric["per_case"].values())
    latency = metrics["Wall-clock Latency"]
    assert latency["total_seconds"] > 0
    assert latency["average_seconds"] > 0
    assert all(value > 0 for value in latency["per_case"].values())

    assert any(
        len(item["tool_sequence"]) < 4
        for item in report["case_results"]
    )
    decoy_case = next(
        item
        for item in report["case_results"]
        if item["case_id"] == "ad-partial-failure-frontend-decoy"
    )
    assert decoy_case["decoy_resistant"] is True


def test_scripted_reruns_have_exact_deterministic_semantic_projection() -> None:
    first = run_evaluation(PROJECT_ROOT)
    second = run_evaluation(PROJECT_ROOT)

    assert (
        first["deterministic_semantic_projection"]
        == second["deterministic_semantic_projection"]
    )
    assert (
        first["deterministic_semantic_sha256"]
        == second["deterministic_semantic_sha256"]
    )
    assert first["timing_semantics"] == {
        "clock": "monotonic wall-clock",
        "deterministic": False,
        "excluded_from_semantic_fingerprint": True,
        "scope": "local scripted replay only",
    }
    # The measured timing remains honest and therefore is not required to be
    # byte-identical across local reruns.
    assert first["metrics"]["Wall-clock Latency"]["total_seconds"] > 0
    assert second["metrics"]["Wall-clock Latency"]["total_seconds"] > 0


def test_evaluation_report_is_strict_json_serializable() -> None:
    report = run_evaluation(PROJECT_ROOT)
    encoded = json.dumps(
        report,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert json.loads(encoded)["status"] == "PASSED"
