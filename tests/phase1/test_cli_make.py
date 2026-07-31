from __future__ import annotations

import json
import io
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from ecomsre.phase1.cli import (
    PROJECT_ROOT,
    atomic_write_json,
    main,
    stable_run_id,
)
from ecomsre.phase1.runtime_config import load_agent_settings
from ecomsre.phase1 import replay_worker
import ecomsre.phase1.cli as cli_module


MAKEFILE = PROJECT_ROOT / "Makefile"


def test_makefile_exposes_exactly_four_phase1_user_workflows() -> None:
    source = MAKEFILE.read_text(encoding="utf-8")
    user_targets = set(
        re.findall(
            r"^(phase1-(?:replay-smoke|eval|test|provider-smoke))\s*:",
            source,
            flags=re.MULTILINE,
        )
    )
    assert user_targets == {
        "phase1-replay-smoke",
        "phase1-eval",
        "phase1-test",
        "phase1-provider-smoke",
    }
    assert re.findall(r"^phase1-prerequisites\s*:", source, re.MULTILINE)

    offline_block = source[
        source.index("phase1-replay-smoke:") : source.index(
            "phase1-provider-smoke:"
        )
    ].casefold()
    for forbidden in (
        "provider-smoke",
        "docker",
        "compose",
        "phase0",
        "read -",
        "input(",
    ):
        assert forbidden not in offline_block


def test_environment_example_has_only_the_three_placeholders() -> None:
    assert (PROJECT_ROOT / ".env.example").read_bytes() == (
        b"ECOMSRE_LLM_BASE_URL=https://provider.example/v1\n"
        b"ECOMSRE_LLM_API_KEY=replace-with-local-secret\n"
        b"ECOMSRE_LLM_MODEL=replace-with-model-id\n"
    )


def test_pytest_does_not_expose_repo_root_or_tests_to_agent_imports() -> None:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'pythonpath = ["src"]' in pyproject
    assert 'pythonpath = [".",' not in pyproject
    assert 'pythonpath = ["tests"' not in pyproject


def test_stable_run_id_is_stable_and_domain_separated() -> None:
    first = stable_run_id("evaluation", "case-one")
    assert first == stable_run_id("evaluation", "case-one")
    assert first != stable_run_id("replay-smoke", "case-one")
    assert first != stable_run_id("evaluation", "case-two")
    assert re.fullmatch(r"[0-9a-f]{32}", first)


def test_agent_settings_loader_is_shared_and_strict(tmp_path: Path) -> None:
    settings = load_agent_settings(PROJECT_ROOT)
    assert settings.budgets.max_model_calls == 8
    assert settings.model_timeout_seconds == 30.0
    assert settings.tool_timeout_seconds == 5.0

    config_dir = tmp_path / "config/phase1"
    config_dir.mkdir(parents=True)
    path = config_dir / "agent.json"
    valid = json.loads(
        (PROJECT_ROOT / "config/phase1/agent.json").read_bytes()
    )
    invalid_payloads = []
    for field, value in (
        ("model_timeout_seconds", 0),
        ("tool_timeout_seconds", -1),
        ("temperature", True),
        ("max_model_calls", True),
    ):
        candidate = dict(valid)
        candidate[field] = value
        invalid_payloads.append(candidate)
    invalid_payloads.append({**valid, "unexpected": True})
    for candidate in invalid_payloads:
        path.write_text(json.dumps(candidate), encoding="utf-8")
        with pytest.raises(ValueError):
            load_agent_settings(tmp_path)
    path.write_text("{\"model_timeout_seconds\":NaN}", encoding="utf-8")
    with pytest.raises(ValueError, match="configuration"):
        load_agent_settings(tmp_path)


def test_worker_directory_fd_resolution_prefers_linux_proc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    fcntl_called = False

    def proc_readlink(path: object) -> str:
        assert path == f"/proc/self/fd/{file_descriptor}"
        return str(tmp_path)

    def forbidden_fcntl(*_args: object) -> object:
        nonlocal fcntl_called
        fcntl_called = True
        raise AssertionError("fcntl fallback should not run when proc is usable")

    monkeypatch.setattr(replay_worker.os, "readlink", proc_readlink)
    monkeypatch.setattr(replay_worker.fcntl, "fcntl", forbidden_fcntl)
    try:
        assert replay_worker._directory_fd_path(file_descriptor) == tmp_path
        assert fcntl_called is False
    finally:
        os.close(file_descriptor)


def test_worker_stdin_is_bounded_before_json_parsing() -> None:
    oversized = io.BytesIO(b"x" * (64 * 1024 + 1))
    with pytest.raises(ValueError, match="size limit"):
        replay_worker._read_bounded_worker_request(oversized)


def _seed_passed_artifact(
    artifact_root: Path,
    relative_path: Path,
) -> Path:
    return atomic_write_json(
        artifact_root=artifact_root,
        relative_path=relative_path,
        payload={"schema_version": "old.v1", "status": "PASSED"},
    )


def _assert_failed_attempt(path: Path, command: str) -> None:
    payload = json.loads(path.read_bytes())
    assert payload == {
        "schema_version": "phase1.command-attempt.v1",
        "command": command,
        "error_code": "PHASE1_COMMAND_FAILED",
        "status": "FAILED",
    }


def test_replay_failure_replaces_stale_passed_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = stable_run_id("replay-smoke", "ad-partial-failure-complete")
    relative = Path(f"reports/{run_id}/agent-run-report.json")
    path = _seed_passed_artifact(tmp_path, relative)
    monkeypatch.setattr(
        cli_module,
        "run_case",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("forced")),
    )
    assert main(
        ["replay-smoke"],
        project_root=PROJECT_ROOT,
        artifact_root=tmp_path,
        environment={},
    ) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "FAILED"
    _assert_failed_attempt(path, "replay-smoke")


def test_evaluation_failure_replaces_stale_passed_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    relative = Path("evaluation/evaluation-report.json")
    path = _seed_passed_artifact(tmp_path, relative)
    failing = SimpleNamespace(
        run_evaluation=lambda _root: (_ for _ in ()).throw(
            RuntimeError("forced")
        )
    )
    monkeypatch.setattr(cli_module, "_load_evaluator_module", lambda _root: failing)
    assert main(
        ["eval"],
        project_root=PROJECT_ROOT,
        artifact_root=tmp_path,
        environment={},
    ) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "FAILED"
    _assert_failed_attempt(path, "eval")


def test_provider_failure_replaces_stale_passed_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    relative = Path("provider-smoke/provider-smoke-report.json")
    path = _seed_passed_artifact(tmp_path, relative)
    monkeypatch.setattr(
        cli_module,
        "run_provider_smoke",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("forced")),
    )
    assert main(
        ["provider-smoke"],
        project_root=PROJECT_ROOT,
        artifact_root=tmp_path,
        environment={},
    ) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "FAILED"
    _assert_failed_attempt(path, "provider-smoke")


class _RaisingGateway:
    def complete(self, _request: object) -> object:
        raise RuntimeError("provider failed")


def test_replay_legitimate_failed_report_preserves_full_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    failed_report = cli_module.run_case(
        project_root=PROJECT_ROOT,
        case_id="ad-partial-failure-complete",
        namespace="replay-smoke",
        gateway=_RaisingGateway(),  # type: ignore[arg-type]
        model_name="scripted-replay-v1",
    )
    assert failed_report.terminal_status.value == "TERMINATED"
    monkeypatch.setattr(cli_module, "run_case", lambda **_kwargs: failed_report)

    assert main(
        ["replay-smoke"],
        project_root=PROJECT_ROOT,
        artifact_root=tmp_path,
        environment={},
    ) == 1
    stdout = json.loads(capsys.readouterr().out)
    assert stdout["status"] == "FAILED"
    path = tmp_path / (
        "reports/"
        f"{stable_run_id('replay-smoke', 'ad-partial-failure-complete')}/"
        "agent-run-report.json"
    )
    persisted = json.loads(path.read_bytes())
    assert persisted == failed_report.model_dump(mode="json")
    assert persisted["terminal_error_code"] == "MODEL_PROTOCOL_VIOLATION"
    assert "evidence_index" in persisted
    assert "model_call_records" in persisted


def test_eval_legitimate_failed_report_preserves_case_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    failed_report = {
        "schema_version": "phase1.evaluation-report.v1",
        "status": "FAILED",
        "case_results": [
            {
                "case_id": "case-one",
                "evidence_references_valid": False,
                "error_code": "EVIDENCE_REFERENCE_INVALID",
            }
        ],
        "metrics": {},
    }
    evaluator = SimpleNamespace(run_evaluation=lambda _root: failed_report)
    monkeypatch.setattr(cli_module, "_load_evaluator_module", lambda _root: evaluator)

    assert main(
        ["eval"],
        project_root=PROJECT_ROOT,
        artifact_root=tmp_path,
        environment={},
    ) == 1
    assert json.loads(capsys.readouterr().out) == failed_report
    persisted = json.loads(
        (tmp_path / "evaluation/evaluation-report.json").read_bytes()
    )
    assert persisted == failed_report


def test_provider_legitimate_failed_report_preserves_case_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    failed_report = {
        "schema_version": "phase1.provider-smoke-report.v1",
        "status": "FAILED",
        "provider": "openai-compatible",
        "model": "provider-model",
        "case_results": [
            {
                "case_id": "case-one",
                "evidence_references_valid": False,
                "error_code": "MODEL_PROTOCOL_VIOLATION",
            }
        ],
        "requirements": {
            "validated_confirmed": False,
            "validated_non_confirmed": False,
        },
    }
    monkeypatch.setattr(
        cli_module,
        "run_provider_smoke",
        lambda **_kwargs: failed_report,
    )

    assert main(
        ["provider-smoke"],
        project_root=PROJECT_ROOT,
        artifact_root=tmp_path,
        environment={},
    ) == 1
    lines = capsys.readouterr().out.splitlines()
    assert json.loads(lines[0]) == failed_report
    assert lines[-1] == "FAILED"
    persisted = json.loads(
        (tmp_path / "provider-smoke/provider-smoke-report.json").read_bytes()
    )
    assert persisted == failed_report


def test_atomic_json_writer_is_canonical_and_rejects_unsafe_paths(
    tmp_path: Path,
) -> None:
    path = atomic_write_json(
        artifact_root=tmp_path,
        relative_path=Path("evaluation/evaluation-report.json"),
        payload={"z": 1, "a": "evidence"},
    )
    assert path.read_bytes() == b'{"a":"evidence","z":1}\n'

    replaced = atomic_write_json(
        artifact_root=tmp_path,
        relative_path=Path("evaluation/evaluation-report.json"),
        payload={"version": 2},
    )
    assert replaced == path
    assert path.read_bytes() == b'{"version":2}\n'
    assert not tuple(path.parent.glob(".*.tmp"))

    with pytest.raises(ValueError, match="relative"):
        atomic_write_json(
            artifact_root=tmp_path,
            relative_path=Path("../escape.json"),
            payload={},
        )
    unsafe = tmp_path / "provider-smoke"
    unsafe.unlink(missing_ok=True)
    unsafe.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="directory"):
        atomic_write_json(
            artifact_root=tmp_path,
            relative_path=Path("provider-smoke/report.json"),
            payload={},
        )


def test_replay_smoke_and_eval_cli_write_only_beneath_artifact_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(
        ["replay-smoke"],
        project_root=PROJECT_ROOT,
        artifact_root=tmp_path,
        environment={},
    ) == 0
    smoke_summary = json.loads(capsys.readouterr().out)
    smoke_path = Path(smoke_summary["report_path"])
    assert smoke_path.is_relative_to(tmp_path)
    smoke_report = json.loads(smoke_path.read_bytes())
    assert smoke_report["terminal_status"] == "COMPLETED"
    assert smoke_report["final_rca"]["decision"] == "RCA_CONFIRMED"

    assert main(
        ["eval"],
        project_root=PROJECT_ROOT,
        artifact_root=tmp_path,
        environment={},
    ) == 0
    evaluation_stdout = json.loads(capsys.readouterr().out)
    assert evaluation_stdout["status"] == "PASSED"
    evaluation_path = tmp_path / "evaluation/evaluation-report.json"
    assert evaluation_path.is_file()
    assert json.loads(evaluation_path.read_bytes())["status"] == "PASSED"
