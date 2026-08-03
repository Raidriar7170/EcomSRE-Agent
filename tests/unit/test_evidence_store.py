import base64
import json
import multiprocessing
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.evidence.hashes import (
    canonical_json_bytes,
    canonical_json_sha256,
    sha256_bytes,
)
from ecomsre.evidence.models import (
    CanonicalState,
    CommandLog,
    ControlEvent,
    CycleReport,
    FinalReport,
    IntegrityManifest,
    RunManifest,
    StatisticalEvidence,
)
from ecomsre.evidence.store import (
    EvaluatorEvidenceStore,
    ObserverEvidenceStore,
    ReportEvidenceStore,
    redact_command,
)
from ecomsre.phase0.models import Outcome


RUN_ID = "b" * 32
EVENT_ID = "d" * 32
STARTED = datetime(2026, 7, 30, 1, 0, tzinfo=UTC)
ENDED = datetime(2026, 7, 30, 1, 10, tzinfo=UTC)


def _append_from_process(base_root: str, index: int) -> None:
    ObserverEvidenceStore(Path(base_root), RUN_ID).append_event(
        "lifecycle/concurrent.jsonl",
        {"index": index},
    )


def _run_manifest(
    *,
    canonical_state: CanonicalState = CanonicalState.CANONICAL,
    final_outcome: Outcome = Outcome.FAILED_ACCEPTANCE,
    exit_code: int = 30,
) -> RunManifest:
    return RunManifest(
        schema_version="phase0.run-manifest.v1",
        run_id=RUN_ID,
        scenario_instance_ref="c" * 32,
        canonical_state=canonical_state,
        started_at=STARTED,
        ended_at=ENDED,
        final_outcome=final_outcome,
        exit_code=exit_code,
    )


def _final_report(
    *,
    canonical_state: CanonicalState = CanonicalState.CANONICAL,
    cycles: tuple[CycleReport, ...] | None = None,
    telemetry: dict[str, bool] | None = None,
    outcome: Outcome = Outcome.SUCCESS,
    exit_code: int = 0,
    reasons: tuple[str, ...] = (),
    disposition: str = "STOPPED",
) -> FinalReport:
    return FinalReport(
        schema_version="phase0.final-report.v1",
        run_id=RUN_ID,
        canonical_state=canonical_state,
        cycle_decisions=cycles
        if cycles is not None
        else tuple(
            CycleReport(
                cycle_number=number,
                passed=True,
                reason_codes=(),
            )
            for number in (1, 2, 3)
        ),
        telemetry_gate_decisions=telemetry
        if telemetry is not None
        else {
            "prometheus": True,
            "jaeger": True,
            "opensearch": True,
        },
        overall_outcome=outcome,
        exit_code=exit_code,
        failure_reason_codes=reasons,
        environment_disposition=disposition,
    )


def test_capability_stores_expose_only_their_separate_root(tmp_path) -> None:
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    evaluator = EvaluatorEvidenceStore(tmp_path, RUN_ID)
    reports = ReportEvidenceStore(tmp_path, RUN_ID)

    assert observer.root == tmp_path / "observer-visible" / RUN_ID
    assert evaluator.root == tmp_path / "evaluator-only" / RUN_ID
    assert reports.root == tmp_path / "reports" / RUN_ID
    assert not hasattr(observer, "evaluator_root")
    assert not hasattr(observer, "write_control_event")
    assert not hasattr(evaluator, "write_observer_immutable")


def test_evidence_store_context_closes_fd_and_close_is_idempotent(
    tmp_path,
) -> None:
    with ObserverEvidenceStore(tmp_path, RUN_ID) as observer:
        descriptor = observer._capability._root_descriptor
        observer.append_event(
            "lifecycle/events.jsonl",
            {"state": "STARTED"},
        )
        os.fstat(descriptor)

    with pytest.raises(OSError):
        os.fstat(descriptor)
    observer.close()
    observer.close()
    with pytest.raises(RuntimeError, match="closed"):
        observer.append_event(
            "lifecycle/events.jsonl",
            {"state": "STOPPED"},
        )


def test_run_manifest_uses_observer_path_not_acceptance_report_path(tmp_path) -> None:
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)

    stored = observer.write_run_manifest(_run_manifest())

    assert stored.path == observer.root / "run-manifest.json"
    assert "acceptance-report.json" not in str(stored.path)
    with pytest.raises(FileExistsError):
        observer.write_run_manifest(_run_manifest())


def test_failed_cycle_evidence_is_append_only_and_cannot_be_overwritten(
    tmp_path,
) -> None:
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    relative = "cycles/01/fault/verdict.json"
    failed = {"passed": False, "reason_code": "FAULT_THRESHOLD_FAILED"}

    stored = observer.write_immutable(relative, failed)

    with pytest.raises(FileExistsError):
        observer.write_immutable(
            relative,
            {"passed": True, "reason_code": "THRESHOLD_PASSED"},
        )
    assert json.loads(stored.path.read_text()) == failed


def test_jsonl_append_retries_short_os_writes_without_truncation(
    tmp_path,
    monkeypatch,
) -> None:
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    original_write = os.write

    def short_write(descriptor: int, content: bytes) -> int:
        return original_write(descriptor, content[:3])

    monkeypatch.setattr("ecomsre.evidence.store.os.write", short_write)
    relative = "lifecycle/events.jsonl"
    observer.append_event(relative, {"state": "FAILED", "cycle": 1})
    observer.append_event(relative, {"state": "STARTED", "cycle": 2})

    records = [
        json.loads(line) for line in (observer.root / relative).read_text().splitlines()
    ]
    assert records == [
        {"cycle": 1, "state": "FAILED"},
        {"cycle": 2, "state": "STARTED"},
    ]


@pytest.mark.parametrize(
    "leaked_value",
    [
        "../../evaluator-only/run/expected-outcome.json",
        "/artifacts/phase0/evaluator-only/run/expected-outcome.json",
        r"..\..\evaluator_only\run\expected_outcome.json",
        "%2e%2e%2fevaluator%2donly%2frun%2fexpected%2doutcome.json",
        base64.b64encode(b"../../evaluator-only/run/expected-outcome.json").decode(
            "ascii"
        ),
    ],
)
def test_observer_payload_rejects_normalized_or_encoded_semantic_leakage(
    tmp_path,
    leaked_value: str,
) -> None:
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)

    with pytest.raises(ValueError, match="semantic leakage"):
        observer.write_immutable(
            "changes/change.json",
            {"nested": [{"artifact_uri": leaked_value}]},
        )


def test_observer_path_and_payload_cannot_reach_or_reveal_evaluator_truth(
    tmp_path,
) -> None:
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)

    with pytest.raises(ValueError, match="observer"):
        observer.write_immutable(
            "../evaluator-only/ground-truth.json",
            {"safe": True},
        )
    with pytest.raises(ValueError, match="observer"):
        observer.write_immutable(
            "hidden/scenario-ground-truth.json",
            {"safe": True},
        )
    with pytest.raises(ValueError, match="semantic leakage"):
        observer.write_immutable(
            "changes/change.json",
            {"feature_flag_key": "adServiceFailure"},
        )
    with pytest.raises(ValueError, match="semantic leakage"):
        observer.write_immutable(
            "changes/change.json",
            {"nested": {"physical_truth": "fault"}},
        )


def test_observer_allows_nonsemantic_operational_payload(tmp_path) -> None:
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)

    stored = observer.write_immutable(
        "changes/change.json",
        {
            "change_ref": "c" * 32,
            "evaluation_status": "complete",
            "expected_latency_ms": 250,
            "physical_memory_bytes": 1024,
        },
    )

    assert stored.path.exists()


def test_observer_symlink_cannot_escape_to_evaluator_root(tmp_path) -> None:
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    evaluator = EvaluatorEvidenceStore(tmp_path, RUN_ID)
    (observer.root / "cycles").symlink_to(evaluator.root, target_is_directory=True)

    with pytest.raises(ValueError, match="observer"):
        observer.write_immutable("cycles/leak.json", {"safe": True})


def test_store_rejects_symlinked_evidence_root_without_external_write(
    tmp_path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    symlinked_root = tmp_path / "evidence-link"
    symlinked_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="capability root"):
        ObserverEvidenceStore(symlinked_root, RUN_ID)

    assert list(outside.iterdir()) == []


def test_store_detects_run_root_replacement_before_write(tmp_path) -> None:
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    original = tmp_path / "detached-run"
    outside = tmp_path / "outside"
    outside.mkdir()
    observer.root.rename(original)
    observer.root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="replaced"):
        observer.append_event("lifecycle/events.jsonl", {"state": "STARTED"})

    assert list(outside.iterdir()) == []


def test_jsonl_rejects_hardlinks_and_fifos(tmp_path) -> None:
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    lifecycle = observer.root / "lifecycle"
    lifecycle.mkdir()
    source = tmp_path / "external.jsonl"
    source.write_text('{"external":true}\n')
    os.link(source, lifecycle / "events.jsonl")

    with pytest.raises(ValueError, match="link count"):
        observer.append_event("lifecycle/events.jsonl", {"state": "STARTED"})
    assert source.read_text() == '{"external":true}\n'

    (lifecycle / "events.jsonl").unlink()
    os.mkfifo(lifecycle / "events.jsonl", mode=0o600)
    with pytest.raises(ValueError, match="regular file"):
        observer.append_event("lifecycle/events.jsonl", {"state": "STARTED"})


def test_jsonl_recovers_truncated_tail_and_rolls_back_failed_append(
    tmp_path,
    monkeypatch,
) -> None:
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    target = observer.root / "lifecycle" / "events.jsonl"
    target.parent.mkdir()
    target.write_bytes(b'{"valid":1}\n{"truncated":')
    target.chmod(0o600)

    observer.append_event("lifecycle/events.jsonl", {"valid": 2})
    assert target.read_bytes() == b'{"valid":1}\n{"valid":2}\n'

    def partial_then_fail(descriptor: int, _content: bytes) -> None:
        os.write(descriptor, b'{"partial":')
        raise OSError("injected append failure")

    monkeypatch.setattr(
        "ecomsre.evidence.store._write_all",
        partial_then_fail,
    )
    with pytest.raises(OSError, match="injected"):
        observer.append_event("lifecycle/events.jsonl", {"valid": 3})
    assert target.read_bytes() == b'{"valid":1}\n{"valid":2}\n'


def test_jsonl_multiprocess_appends_are_complete_and_noninterleaved(
    tmp_path,
) -> None:
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_append_from_process, args=(str(tmp_path), index))
        for index in range(8)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    records = [
        json.loads(line)
        for line in (
            tmp_path / "observer-visible" / RUN_ID / "lifecycle" / "concurrent.jsonl"
        )
        .read_text()
        .splitlines()
    ]
    assert sorted(record["index"] for record in records) == list(range(8))


def test_evaluator_control_events_append_to_exact_jsonl_path(tmp_path) -> None:
    evaluator = EvaluatorEvidenceStore(tmp_path, RUN_ID)
    event = ControlEvent(
        schema_version="phase0.control-event.v1",
        run_id=RUN_ID,
        event_id=EVENT_ID,
        occurred_at=STARTED,
        control_action="inject",
        feature_flag_key="adServiceFailure",
        feature_flag_value="on",
    )

    stored = evaluator.write_control_event(event)
    second = event.model_copy(
        update={
            "event_id": "e" * 32,
            "occurred_at": ENDED,
            "control_action": "reset",
        }
    )
    evaluator.write_control_event(second)

    assert stored.path == evaluator.root / "control-events.jsonl"
    records = [json.loads(line) for line in stored.path.read_text().splitlines()]
    assert [record["event_id"] for record in records] == [
        EVENT_ID,
        "e" * 32,
    ]


def test_evidence_models_reject_naive_timestamps() -> None:
    with pytest.raises(ValidationError, match="UTC"):
        ControlEvent(
            schema_version="phase0.control-event.v1",
            run_id=RUN_ID,
            event_id=EVENT_ID,
            occurred_at=datetime(2026, 7, 30, 1, 0),
            control_action="inject",
            feature_flag_key="adServiceFailure",
            feature_flag_value="on",
        )


def test_report_store_writes_final_failure_and_checksums_to_distinct_paths(
    tmp_path,
) -> None:
    reports = ReportEvidenceStore(tmp_path, RUN_ID)
    final = _final_report()
    failure = _final_report(
        cycles=(
            CycleReport(
                cycle_number=1,
                passed=False,
                reason_codes=("FAULT_THRESHOLD_FAILED",),
            ),
        ),
        telemetry={"prometheus": True},
        outcome=Outcome.FAILED_ACCEPTANCE,
        exit_code=30,
        reasons=("FAULT_THRESHOLD_FAILED",),
        disposition="STOPPED",
    )
    content_hashes = {"observer/run-manifest.json": "a" * 64}
    checksums = IntegrityManifest(
        schema_version="phase0.integrity.v1",
        run_id=RUN_ID,
        content_hashes=content_hashes,
        manifest_sha256=canonical_json_sha256(content_hashes),
    )

    final_stored = reports.write_final_report(final)
    failure_stored = reports.write_failure_report(failure)
    checksums_stored = reports.write_checksums(checksums)

    assert final_stored.path == reports.root / "acceptance-report.json"
    assert failure_stored.path == reports.root / "failure-report.json"
    assert checksums_stored.path == reports.root / "checksums.sha256"
    assert checksums_stored.path.read_text() == (
        f"{'a' * 64}  observer/run-manifest.json\n"
    )
    assert not list(reports.root.glob(".*.tmp"))
    with pytest.raises(FileExistsError):
        reports.write_final_report(final)


def test_integrity_manifest_hash_mapping_is_deeply_immutable() -> None:
    content_hashes = {"observer/run-manifest.json": "a" * 64}
    manifest = IntegrityManifest(
        schema_version="phase0.integrity.v1",
        run_id=RUN_ID,
        content_hashes=content_hashes,
        manifest_sha256=canonical_json_sha256(content_hashes),
    )

    with pytest.raises(TypeError, match="immutable"):
        manifest.content_hashes["observer/run-manifest.json"] = "b" * 64
    assert manifest.content_hashes["observer/run-manifest.json"] == "a" * 64


def test_checksums_writer_revalidates_adversarially_mutated_manifest(
    tmp_path,
) -> None:
    content_hashes = {"observer/run-manifest.json": "a" * 64}
    manifest = IntegrityManifest(
        schema_version="phase0.integrity.v1",
        run_id=RUN_ID,
        content_hashes=content_hashes,
        manifest_sha256=canonical_json_sha256(content_hashes),
    )
    object.__setattr__(
        manifest,
        "content_hashes",
        {"observer/run-manifest.json": "b" * 64},
    )
    reports = ReportEvidenceStore(tmp_path, RUN_ID)

    with pytest.raises(ValidationError, match="hash is inconsistent"):
        reports.write_checksums(manifest)

    assert not (reports.root / "checksums.sha256").exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"exit_code": 30},
        {"canonical_state": CanonicalState.NON_CANONICAL},
        {"cycles": (CycleReport(cycle_number=1, passed=True, reason_codes=()),)},
        {"telemetry": {"prometheus": True, "jaeger": True}},
        {
            "cycles": tuple(
                CycleReport(
                    cycle_number=number,
                    passed=number != 2,
                    reason_codes=() if number != 2 else ("CYCLE_FAILED",),
                )
                for number in (1, 2, 3)
            )
        },
    ],
)
def test_success_report_rejects_cross_field_inconsistency(overrides: dict) -> None:
    with pytest.raises(ValidationError, match="SUCCESS"):
        _final_report(**overrides)


def test_failure_writer_rejects_success_report(tmp_path) -> None:
    reports = ReportEvidenceStore(tmp_path, RUN_ID)

    with pytest.raises(ValueError, match="failure"):
        reports.write_failure_report(_final_report())


def test_run_manifest_rejects_noncanonical_or_wrong_exit_success() -> None:
    with pytest.raises(ValidationError, match="SUCCESS"):
        _run_manifest(
            canonical_state=CanonicalState.NON_CANONICAL,
            final_outcome=Outcome.SUCCESS,
            exit_code=0,
        )
    with pytest.raises(ValidationError, match="exit"):
        _run_manifest(
            final_outcome=Outcome.SUCCESS,
            exit_code=30,
        )


def test_command_arguments_are_redacted_before_persistence() -> None:
    command = redact_command(
        (
            "tool",
            "--token",
            "secret-token",
            "--password=hunter2",
            "API_KEY=abc123",
            "--safe",
            "value",
        )
    )

    assert command == (
        "tool",
        "--token",
        "[REDACTED]",
        "--password=[REDACTED]",
        "API_KEY=[REDACTED]",
        "--safe",
        "value",
    )
    assert "secret-token" not in " ".join(command)
    assert "hunter2" not in " ".join(command)
    assert "abc123" not in " ".join(command)


def test_command_redaction_covers_headers_urls_and_embedded_json() -> None:
    command = redact_command(
        (
            "curl",
            "-H",
            "Authorization: Bearer header-secret",
            "https://user:password@example.test/path",
            '{"nested":{"private_key":"key-material"},"safe":1}',
            "--aws-secret-access-key=cloud-secret",
        )
    )

    rendered = " ".join(command)
    assert command[2] == "Authorization: [REDACTED]"
    assert command[3] == "https://[REDACTED]@example.test/path"
    assert '"private_key":"[REDACTED]"' in command[4]
    assert command[5] == "--aws-secret-access-key=[REDACTED]"
    for secret in (
        "header-secret",
        "user:password",
        "key-material",
        "cloud-secret",
    ):
        assert secret not in rendered


@pytest.mark.parametrize(
    "uri",
    [
        "postgresql://alice:p%40ss@db.example/app",
        "mysql://alice:secret@db.example/app",
        "redis://:secret@cache.example/0",
        "amqps://alice:secret@mq.example/vhost",
        "custom+tls://alice:secret@service.example/path",
    ],
)
def test_command_redaction_covers_any_uri_scheme_userinfo(uri: str) -> None:
    redacted = redact_command(("tool", uri))

    assert redacted == (
        "tool",
        uri.split("://", 1)[0] + "://[REDACTED]@" + uri.rsplit("@", 1)[1],
    )
    assert "alice" not in redacted[1]
    assert "secret" not in redacted[1]
    assert "p%40ss" not in redacted[1]


def test_command_redaction_covers_database_url_dsn_and_connection_string() -> None:
    command = redact_command(
        (
            "tool",
            "DATABASE_URL=postgresql://alice:secret@db/app",
            "DSN=mysql://alice:secret@db/app",
            ("Server=db.example;User ID=alice;Password=p%40ss;Database=app"),
            "file:///project/config.db",
        )
    )

    assert command[1] == "DATABASE_URL=[REDACTED]"
    assert command[2] == "DSN=[REDACTED]"
    assert command[3] == (
        "Server=db.example;User ID=[REDACTED];Password=[REDACTED];Database=app"
    )
    assert command[4] == "file:///project/config.db"


@pytest.mark.parametrize(
    ("payload", "expected_uri"),
    [
        (
            '{"endpoint":"postgres:\\/\\/alice:secret@db.example/app"}',
            "postgres://[REDACTED]@db.example/app",
        ),
        (
            '{"endpoint":"redis://:secret\\u0040cache.example/0"}',
            "redis://[REDACTED]@cache.example/0",
        ),
        (
            '{"endpoint":"mysql://alice%3Asecret%40db.example/app"}',
            "mysql://[REDACTED]@db.example/app",
        ),
    ],
)
def test_json_scalar_uri_redaction_blocks_reviewer_encoding_bypasses(
    payload: str,
    expected_uri: str,
) -> None:
    redacted = redact_command(("tool", payload))

    assert json.loads(redacted[1]) == {"endpoint": expected_uri}
    assert "alice" not in redacted[1]
    assert "secret" not in redacted[1]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            "Server=db;UID=alice;PWD={p;ass};Database=app",
            ("Server=db;UID=[REDACTED];PWD=[REDACTED];Database=app"),
        ),
        (
            'Server=db;User ID=alice;Password="p;ass";Database=app',
            ("Server=db;User ID=[REDACTED];Password=[REDACTED];Database=app"),
        ),
    ],
)
def test_connection_string_redaction_is_quote_and_brace_aware(
    payload: str,
    expected: str,
) -> None:
    redacted = redact_command(("tool", payload))

    assert redacted == ("tool", expected)
    assert "p;ass" not in redacted[1]
    assert "alice" not in redacted[1]


def test_redaction_limits_fail_closed_for_excessive_size_or_json_depth() -> None:
    nested: object = "safe"
    for _ in range(70):
        nested = {"value": nested}
    deeply_nested = json.dumps(nested)
    parser_exhaustion = "[" * 1_100 + "0" + "]" * 1_100

    assert redact_command(("tool", "x" * 70_000))[1] == "[REDACTED]"
    assert redact_command(("tool", deeply_nested))[1] == "[REDACTED]"
    assert redact_command(("tool", parser_exhaustion))[1] == "[REDACTED]"


def test_command_log_rejects_unsanitized_secret_or_wrong_terminal_code() -> None:
    common = {
        "schema_version": "phase0.command-log.v2",
        "run_id": RUN_ID,
        "command": "tool",
        "working_directory": "/project",
        "started_at": STARTED,
        "ended_at": ENDED,
        "monotonic_started_seconds": 1.0,
        "monotonic_ended_seconds": 2.0,
        "timeout_seconds": 30.0,
        "process_exit_code": 1,
        "process_timed_out": False,
        "classification": Outcome.FAILED_ACCEPTANCE,
        "terminal_exit_code": Outcome.FAILED_ACCEPTANCE.exit_code,
        "reason_code": "COMMAND_FAILED",
        "stdout_artifact": "commands/01.stdout.json",
        "stdout_sha256": "a" * 64,
        "stderr_artifact": "commands/01.stderr.json",
        "stderr_sha256": "b" * 64,
        "network_access_declared": False,
        "network_access_scope": "NONE",
        "filesystem_write_scope": (),
        "observed_effect_scope": (),
    }
    with pytest.raises(ValidationError, match="unsanitized"):
        CommandLog(
            **common,
            arguments=("--token", "secret-token"),
        )
    with pytest.raises(ValidationError, match="terminal"):
        CommandLog(
            **{**common, "terminal_exit_code": 0},
            arguments=("--safe", "value"),
        )

    for arguments in (
        ("-H", "Authorization: Bearer raw-secret"),
        ("https://user:password@example.test",),
        ('{"nested":{"credential":"raw-secret"}}',),
        ("postgresql://alice:p%40ss@db.example/app",),
        ("DATABASE_URL=postgresql://alice:secret@db/app",),
        ("Server=db;User ID=alice;Password=secret",),
        ('{"endpoint":"postgres:\\/\\/alice:secret@db/app"}',),
        ('{"endpoint":"redis://:secret\\u0040cache/0"}',),
        ('{"endpoint":"mysql://alice%3Asecret%40db/app"}',),
        ("Server=db;UID=alice;PWD={p;ass};Database=app",),
        ('Server=db;User ID=alice;Password="p;ass";Database=app',),
    ):
        with pytest.raises(ValidationError, match="unsanitized"):
            CommandLog(
                **common,
                arguments=arguments,
            )

    safe_file_uri = CommandLog(
        **common,
        arguments=("file:///project/config.db",),
    )
    assert safe_file_uri.arguments == ("file:///project/config.db",)


def test_command_log_separates_process_classification_and_terminal_exit() -> None:
    log = CommandLog(
        schema_version="phase0.command-log.v2",
        run_id=RUN_ID,
        command="docker",
        arguments=("docker", "version"),
        working_directory="/project",
        started_at=STARTED,
        ended_at=ENDED,
        monotonic_started_seconds=10.0,
        monotonic_ended_seconds=10.5,
        timeout_seconds=30.0,
        process_exit_code=1,
        process_timed_out=False,
        classification=Outcome.BLOCKED_ENVIRONMENT,
        terminal_exit_code=Outcome.BLOCKED_ENVIRONMENT.exit_code,
        reason_code="DOCKER_DAEMON_UNAVAILABLE",
        stdout_artifact="commands/01.stdout.json",
        stdout_sha256="a" * 64,
        stderr_artifact="commands/01.stderr.json",
        stderr_sha256="b" * 64,
        network_access_declared=False,
        network_access_scope="LOCAL_DOCKER_DAEMON",
        filesystem_write_scope=(),
        observed_effect_scope=("docker-daemon-read",),
    )

    assert log.process_exit_code == 1
    assert log.terminal_exit_code == Outcome.BLOCKED_ENVIRONMENT.exit_code
    assert log.process_exit_code != log.terminal_exit_code


def test_statistical_evidence_rejects_nonfinite_or_contradictory_values() -> None:
    common = {
        "schema_version": "phase0.statistical-evidence.v1",
        "run_id": RUN_ID,
        "cycle_number": 1,
        "scenario_phase": "fault",
        "getads_attempts": 200,
        "getads_errors": 20,
        "error_rate": 0.1,
        "wilson_lower": 0.06,
        "wilson_upper": 0.15,
        "threshold_passed": True,
        "sample_timeout": False,
    }
    with pytest.raises(ValidationError):
        StatisticalEvidence(**{**common, "error_rate": float("nan")})
    with pytest.raises(ValidationError, match="errors"):
        StatisticalEvidence(
            **{
                **common,
                "getads_errors": 201,
                "error_rate": 1.0,
            }
        )
    with pytest.raises(ValidationError, match="timeout"):
        StatisticalEvidence(**{**common, "sample_timeout": True})


def test_store_revalidates_mutated_nested_model_before_persistence(
    tmp_path,
) -> None:
    report = _final_report()
    report.telemetry_gate_decisions["prometheus"] = False

    with pytest.raises(ValidationError, match="SUCCESS"):
        ReportEvidenceStore(tmp_path, RUN_ID).write_final_report(report)


def test_content_hashes_cover_exact_bytes_and_canonical_json() -> None:
    assert sha256_bytes(b"phase0") == (
        "bb4402d9610155c96945fa3217fc69c12cc7d15f43f80c8afb9e7a34a84bef93"
    )
    assert canonical_json_sha256({"b": 2, "a": 1}) == canonical_json_sha256(
        {"a": 1, "b": 2}
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"value": float("nan")},
        {"nested": [1, {"value": float("inf")}]},
        [1, {"deep": [float("-inf")]}],
    ],
)
def test_strict_json_boundary_rejects_nested_nonfinite_values(
    tmp_path,
    payload,
) -> None:
    with pytest.raises(ValueError, match="NON_FINITE_JSON_VALUE"):
        canonical_json_bytes(payload)
    with pytest.raises(ValueError, match="NON_FINITE_JSON_VALUE"):
        canonical_json_sha256(payload)

    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    with pytest.raises(ValueError, match="NON_FINITE_JSON_VALUE"):
        observer.write_immutable("changes/nonfinite.json", payload)
    with pytest.raises(ValueError, match="NON_FINITE_JSON_VALUE"):
        observer.append_event("lifecycle/nonfinite.jsonl", payload)

    assert not (observer.root / "changes" / "nonfinite.json").exists()
    assert not (observer.root / "lifecycle" / "nonfinite.jsonl").exists()
    assert not (observer.root / "changes").exists()
    assert not (observer.root / "lifecycle").exists()
