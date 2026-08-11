from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

import pytest

from ecomsre_live_sandbox.e2e_diagnostics import (
    DiagnosticCommandError,
    DiagnosticCommandIdentity,
    DiagnosticEventStatus,
    DiagnosticFailureCode,
    DiagnosticJournal,
    DiagnosticRunKind,
    DiagnosticStage,
    ExceptionArtifactStore,
    RecordingCommandRunner,
    failure_code_for_stage,
)


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_stage_order_and_failure_mapping_are_closed_and_deterministic() -> None:
    stages = tuple(DiagnosticStage)

    assert stages[0] is DiagnosticStage.PRIVATE_ROOT_BOUND
    assert stages[-1] is DiagnosticStage.TERMINAL_SEALED
    assert len(stages) == len(set(stages))
    assert all(isinstance(failure_code_for_stage(stage), DiagnosticFailureCode) for stage in stages)
    assert failure_code_for_stage(DiagnosticStage.COMPOSE_START_REQUESTED) is (
        DiagnosticFailureCode.COMPOSE_UP_FAILED
    )
    assert failure_code_for_stage(DiagnosticStage.SERVICE_HEALTH_WAIT_STARTED) is (
        DiagnosticFailureCode.SERVICE_HEALTH_TIMEOUT
    )
    assert failure_code_for_stage(DiagnosticStage.BASELINE_CONFIGURATION_READ_STARTED) is (
        DiagnosticFailureCode.BASELINE_CONFIGURATION_UNAVAILABLE
    )
    assert failure_code_for_stage(DiagnosticStage.METRICS_PREFLIGHT_STARTED) is (
        DiagnosticFailureCode.METRICS_PREFLIGHT_FAILED
    )
    assert failure_code_for_stage(DiagnosticStage.LOGS_PREFLIGHT_STARTED) is (
        DiagnosticFailureCode.LOGS_PREFLIGHT_FAILED
    )
    assert failure_code_for_stage(DiagnosticStage.TRACES_PREFLIGHT_STARTED) is (
        DiagnosticFailureCode.TRACES_PREFLIGHT_FAILED
    )
    assert failure_code_for_stage(DiagnosticStage.MULTISERVICE_PROJECTION_STARTED) is (
        DiagnosticFailureCode.MULTISERVICE_PROJECTION_FAILED
    )


def test_event_journal_is_append_only_strictly_sequenced_and_private(tmp_path: Path) -> None:
    journal = DiagnosticJournal(
        tmp_path / "probe" / "events.jsonl",
        run_kind=DiagnosticRunKind.DIAGNOSTIC_PROBE,
        run_id="probe-01",
    )
    journal.record(
        stage=DiagnosticStage.PRIVATE_ROOT_BOUND,
        status=DiagnosticEventStatus.STARTED,
        started_at=datetime.now(timezone.utc),
    )
    prefix = journal.path.read_bytes()
    journal.record(
        stage=DiagnosticStage.PRIVATE_ROOT_BOUND,
        status=DiagnosticEventStatus.PASSED,
        started_at=datetime.now(timezone.utc),
        ended_at=datetime.now(timezone.utc),
        output_value={"bound": True},
    )

    assert journal.path.read_bytes().startswith(prefix)
    events = [json.loads(line) for line in journal.path.read_text().splitlines()]
    assert [event["sequence"] for event in events] == [1, 2]
    assert all(event["run_id"] == "probe-01" for event in events)
    assert _mode(journal.path.parent) == 0o700
    assert _mode(journal.path) == 0o600


def test_event_journal_rejects_a_later_stage_before_current_stage_passes(tmp_path: Path) -> None:
    journal = DiagnosticJournal(
        tmp_path / "events.jsonl",
        run_kind=DiagnosticRunKind.CANONICAL_INVOCATION_A,
        run_id="invocation-a",
    )
    journal.record(
        stage=DiagnosticStage.PRIVATE_ROOT_BOUND,
        status=DiagnosticEventStatus.STARTED,
        started_at=datetime.now(timezone.utc),
    )

    with pytest.raises(RuntimeError, match="active stage"):
        journal.record(
            stage=DiagnosticStage.AUTHORITY_VERIFIED,
            status=DiagnosticEventStatus.STARTED,
            started_at=datetime.now(timezone.utc),
        )


def test_exception_artifact_retains_raw_text_privately_and_exposes_only_hashes(
    tmp_path: Path,
) -> None:
    store = ExceptionArtifactStore(tmp_path / "exceptions")
    secret = "raw endpoint http://127.0.0.1:19090 must remain private"

    try:
        raise RuntimeError(secret)
    except RuntimeError as error:
        reference = store.capture(
            error,
            stage=DiagnosticStage.METRICS_PREFLIGHT_STARTED,
            sequence=7,
        )

    artifact = next((tmp_path / "exceptions").glob("exception-*.json"))
    assert secret in artifact.read_text(encoding="utf-8")
    assert _mode(artifact.parent) == 0o700
    assert _mode(artifact) == 0o600
    public = reference.model_dump(mode="json")
    serialized = json.dumps(public, sort_keys=True)
    assert secret not in serialized
    assert "127.0.0.1" not in serialized
    assert len(reference.exception_message_sha256) == 64
    assert len(reference.traceback_sha256) == 64
    assert len(reference.artifact_sha256) == 64


def test_recording_runner_keeps_stdout_stderr_private_and_public_record_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(*_: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("shell") is not True
        return subprocess.CompletedProcess(
            args=["docker", "context", "show"],
            returncode=0,
            stdout="private-context-name\n",
            stderr="private-warning\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = RecordingCommandRunner(tmp_path / "commands")
    result = runner.run(("docker", "context", "show"), cwd=tmp_path)

    assert result.stdout == "private-context-name\n"
    record_path = next((tmp_path / "commands").glob("command-*.json"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["command_identity"] == DiagnosticCommandIdentity.DOCKER_CONTEXT_SHOW.value
    assert "private-context-name" not in json.dumps(record)
    assert "private-warning" not in json.dumps(record)
    assert record["stdout_byte_count"] == len("private-context-name\n".encode())
    assert record["stderr_byte_count"] == len("private-warning\n".encode())
    assert all(_mode(path) == 0o600 for path in (tmp_path / "commands").iterdir())


@pytest.mark.parametrize(
    ("returncode", "timed_out"),
    ((17, False), (None, True)),
)
def test_recording_runner_distinguishes_nonzero_and_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int | None,
    timed_out: bool,
) -> None:
    if timed_out:
        def fake_run(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(
                cmd=("docker", "compose", "up"), timeout=1, output="partial", stderr="late"
            )
    else:
        def fake_run(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=["docker", "compose", "up"],
                returncode=returncode,
                stdout="partial",
                stderr="failed",
            )

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = RecordingCommandRunner(tmp_path / "commands")

    with pytest.raises(DiagnosticCommandError) as raised:
        runner.run(("docker", "compose", "up"), cwd=tmp_path, timeout_seconds=1)

    assert raised.value.identity is DiagnosticCommandIdentity.COMPOSE_UP
    assert raised.value.timed_out is timed_out
    assert raised.value.return_code == returncode
    record = json.loads(next((tmp_path / "commands").glob("command-*.json")).read_text())
    assert record["timed_out"] is timed_out
    assert record["return_code"] == returncode
    assert "partial" not in json.dumps(record)


def test_recording_runner_rejects_non_allowlisted_argv(tmp_path: Path) -> None:
    runner = RecordingCommandRunner(tmp_path / "commands")

    with pytest.raises(ValueError, match="allowlisted diagnostic identity"):
        runner.run(("sh", "-c", "echo forbidden"), cwd=tmp_path)

    assert not (tmp_path / "commands").exists()
