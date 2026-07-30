from __future__ import annotations

import base64
import hashlib
import json
import multiprocessing
import os
import stat
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from ecomsre.evidence.store import ObserverEvidenceStore
from ecomsre.environment.lifecycle import ReadinessEvidence
from ecomsre.phase0.models import Outcome
from ecomsre.scenarios.ad_service_failure import (
    AdServiceFailureController,
    ControlMutationUncertain,
    MutationState,
    ObserverControlEventSink,
    TransitionGuardCleanupError,
)
from ecomsre.scenarios.ground_truth import (
    FlagdGroundTruthRuntime,
    OfrepReadback,
    prepare_flagd_runtime,
)
from ecomsre.scenarios import ground_truth


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "a" * 32
CORRELATION_ID = "b" * 32


class FixtureClock:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self.started_at = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.started_at + timedelta(seconds=self.elapsed)

    def monotonic(self) -> float:
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        self.elapsed += seconds


class FixtureOfrepClient:
    def __init__(self, values: list[bool]) -> None:
        self.values = list(values)
        self.calls: list[dict[str, object]] = []

    def evaluate(
        self,
        *,
        endpoint: str,
        flag_key: str,
        timeout_seconds: float,
    ) -> OfrepReadback:
        assert timeout_seconds > 0
        self.calls.append(
            {
                "endpoint": endpoint,
                "flag_key": flag_key,
                "timeout_seconds": timeout_seconds,
            }
        )
        if len(self.values) > 1:
            value = self.values.pop(0)
        else:
            value = self.values[0]
        raw = json.dumps(
            {
                "value": value,
                "variant": "on" if value else "off",
                "reason": "STATIC",
            },
            separators=(",", ":"),
        ).encode()
        return OfrepReadback(
            schema_version="phase0.ofrep-readback.v1",
            http_status=200,
            raw_response_body_b64=base64.b64encode(raw).decode("ascii"),
            raw_body_truncated=False,
            parsed_value=value,
            parsed_variant="on" if value else "off",
            parsed_reason="STATIC",
            received_at=datetime(2026, 7, 30, 8, 0, tzinfo=UTC),
            received_monotonic=float(len(self.calls)),
            content_sha256=hashlib.sha256(raw).hexdigest(),
            error_code="NONE",
            request_metadata={
                "method": "POST",
                "content_type": "application/json",
                "request_body_sha256": hashlib.sha256(b"{}").hexdigest(),
            },
        )


class RuntimeReflectingOfrepClient:
    def __init__(self) -> None:
        self.runtime_path: Path | None = None

    def evaluate(
        self,
        *,
        endpoint: str,
        flag_key: str,
        timeout_seconds: float,
    ) -> OfrepReadback:
        del endpoint
        assert flag_key == "adFailure"
        assert timeout_seconds > 0
        assert self.runtime_path is not None
        payload = json.loads(self.runtime_path.read_text(encoding="utf-8"))
        variant = payload["flags"]["adFailure"]["defaultVariant"]
        value = variant == "on"
        raw = json.dumps(
            {"value": value, "variant": variant, "reason": "STATIC"},
            separators=(",", ":"),
        ).encode()
        return OfrepReadback(
            schema_version="phase0.ofrep-readback.v1",
            http_status=200,
            raw_response_body_b64=base64.b64encode(raw).decode("ascii"),
            raw_body_truncated=False,
            parsed_value=value,
            parsed_variant=variant,
            parsed_reason="STATIC",
            received_at=datetime(2026, 7, 30, 8, 0, tzinfo=UTC),
            received_monotonic=time.monotonic(),
            content_sha256=hashlib.sha256(raw).hexdigest(),
            error_code="NONE",
            request_metadata={
                "method": "POST",
                "content_type": "application/json",
                "request_body_sha256": hashlib.sha256(b"{}").hexdigest(),
            },
        )


def _multiprocess_guard_worker(
    artifacts_root: str,
    acquired,
    release,
) -> None:
    runtime = FlagdGroundTruthRuntime.open_existing(
        project_root=ROOT,
        artifacts_root=Path(artifacts_root),
        run_id=RUN_ID,
        ofrep_client=FixtureOfrepClient([False]),
        ofrep_endpoint="http://127.0.0.1:18016",
    )
    try:
        with runtime.transition_guard(timeout_seconds=2) as locked:
            if locked:
                acquired.set()
                release.wait(timeout=2)
    finally:
        runtime.close()


def _build_controller(
    tmp_path: Path,
    values: list[bool],
    *,
    timeout_seconds: float = 2,
):
    client = FixtureOfrepClient(values)
    runtime = FlagdGroundTruthRuntime.bootstrap(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        ofrep_client=client,
        ofrep_endpoint="http://127.0.0.1:18016",
    )
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    controller = AdServiceFailureController(
        adapter=runtime,
        observer_sink=ObserverControlEventSink(observer),
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        clock=FixtureClock(),
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=1,
    )
    return controller, runtime, client, observer


def test_fixture_inject_idempotent_readback_and_reset_are_fully_separated(
    tmp_path: Path,
) -> None:
    controller, runtime, client, observer = _build_controller(
        tmp_path,
        [False, True, True, True, False],
    )
    baseline_inode = os.lstat(runtime.runtime_path).st_ino

    injected = controller.inject()
    injected_inode = os.lstat(runtime.runtime_path).st_ino
    repeated = controller.inject()
    repeated_inode = os.lstat(runtime.runtime_path).st_ino
    reset = controller.reset()

    assert injected.terminal_result.outcome is Outcome.SUCCESS
    assert injected.mutation_state is MutationState.APPLIED
    assert injected_inode != baseline_inode
    assert repeated.terminal_result.outcome is Outcome.SUCCESS
    assert repeated.mutation_state is MutationState.NOT_APPLIED
    assert repeated_inode == injected_inode
    assert reset.terminal_result.outcome is Outcome.SUCCESS
    assert reset.mutation_state is MutationState.APPLIED

    runtime_payload = json.loads(runtime.runtime_path.read_text(encoding="utf-8"))
    assert runtime_payload["flags"]["adFailure"]["defaultVariant"] == "off"
    assert [call["flag_key"] for call in client.calls] == ["adFailure"] * 5
    assert all(call["endpoint"] == "http://127.0.0.1:18016" for call in client.calls)

    hidden_records = [
        json.loads(line)
        for line in (tmp_path / "evaluator-only" / RUN_ID / "control-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(hidden_records) == 3
    assert all(
        record["scenario_identity"] == "adServiceFailure" for record in hidden_records
    )
    assert all(record["physical_flag_key"] == "adFailure" for record in hidden_records)
    assert hidden_records[0]["expected_transition"] == "BASELINE_TO_INJECTED"
    assert hidden_records[1]["mutation_state"] == "NOT_APPLIED"
    assert hidden_records[2]["expected_transition"] == "INJECTED_TO_BASELINE"

    observer_records = [
        json.loads(line)
        for line in (observer.root / "changes" / "changes.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    serialized_observer = json.dumps(observer_records, sort_keys=True)
    assert len(observer_records) == 3
    assert "adFailure" not in serialized_observer
    assert "adServiceFailure" not in serialized_observer
    assert "defaultVariant" not in serialized_observer
    assert "evaluator-only" not in serialized_observer
    assert "http://" not in serialized_observer

    raw_records = [
        json.loads(line)
        for line in (
            tmp_path / "evaluator-only" / RUN_ID / "readbacks" / "ofrep-attempts.jsonl"
        )
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(raw_records) == 5
    assert all(record["physical_flag_key"] == "adFailure" for record in raw_records)
    assert all(record["readback_sha256"] for record in raw_records)
    assert all(record["readback"]["content_sha256"] for record in raw_records)
    assert "readbacks" not in serialized_observer
    assert "ofrep" not in serialized_observer.casefold()


def test_conflicting_pre_state_is_manual_and_zero_write(tmp_path: Path) -> None:
    controller, runtime, _client, _observer = _build_controller(tmp_path, [True])
    before = runtime.runtime_path.read_bytes()
    before_inode = os.lstat(runtime.runtime_path).st_ino

    result = controller.inject()

    assert result.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert result.terminal_result.reason_code == "MUTATION_STATE_UNKNOWN"
    assert result.mutation_state is MutationState.NOT_APPLIED
    assert runtime.runtime_path.read_bytes() == before
    assert os.lstat(runtime.runtime_path).st_ino == before_inode


def test_write_without_matching_ofrep_readback_is_immediate_manual(
    tmp_path: Path,
) -> None:
    controller, runtime, client, _observer = _build_controller(
        tmp_path,
        [False],
        timeout_seconds=2,
    )

    result = controller.inject()

    assert result.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert result.terminal_result.reason_code == "MUTATION_STATE_UNKNOWN"
    assert result.observer_event.transition_succeeded is False
    assert len(client.calls) == 2
    payload = json.loads(runtime.runtime_path.read_text(encoding="utf-8"))
    assert payload["flags"]["adFailure"]["defaultVariant"] == "on"


def test_tampered_upstream_source_fails_before_evaluator_write(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    source = (
        project_root
        / "third_party"
        / "opentelemetry-demo"
        / "src"
        / "flagd"
        / "demo.flagd.json"
    )
    source.parent.mkdir(parents=True)
    source.write_text('{"$schema":"tampered","flags":{}}', encoding="utf-8")
    artifacts = tmp_path / "artifacts"

    with pytest.raises(ValueError, match="hash"):
        FlagdGroundTruthRuntime.bootstrap(
            project_root=project_root,
            artifacts_root=artifacts,
            run_id=RUN_ID,
            ofrep_client=FixtureOfrepClient([False]),
            ofrep_endpoint="http://127.0.0.1:18016",
        )

    assert not artifacts.exists()


def test_symlinked_control_directory_is_rejected_without_escape_write(
    tmp_path: Path,
) -> None:
    evaluator_root = tmp_path / "evaluator-only" / RUN_ID
    evaluator_root.mkdir(parents=True, mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    (evaluator_root / "control").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="capability|escape"):
        FlagdGroundTruthRuntime.bootstrap(
            project_root=ROOT,
            artifacts_root=tmp_path,
            run_id=RUN_ID,
            ofrep_client=FixtureOfrepClient([False]),
            ofrep_endpoint="http://127.0.0.1:18016",
        )

    assert list(outside.iterdir()) == []


def test_prepared_runtime_can_be_opened_read_only_without_rewriting(
    tmp_path: Path,
) -> None:
    runtime_path = prepare_flagd_runtime(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
    )
    inode = os.lstat(runtime_path).st_ino
    content = runtime_path.read_bytes()

    runtime = FlagdGroundTruthRuntime.open_existing(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        ofrep_client=FixtureOfrepClient([False]),
        ofrep_endpoint="http://127.0.0.1:18016",
    )
    runtime.close()

    assert os.lstat(runtime_path).st_ino == inode
    assert runtime_path.read_bytes() == content


def test_open_existing_missing_runtime_is_zero_write(tmp_path: Path) -> None:
    artifacts = tmp_path / "missing-artifacts"

    with pytest.raises(ValueError, match="unavailable"):
        FlagdGroundTruthRuntime.open_existing(
            project_root=ROOT,
            artifacts_root=artifacts,
            run_id=RUN_ID,
            ofrep_client=FixtureOfrepClient([False]),
            ofrep_endpoint="http://127.0.0.1:18016",
        )

    assert not artifacts.exists()


def test_atomic_mutation_uncertainty_is_manual_and_never_acknowledged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller, runtime, _client, _observer = _build_controller(
        tmp_path,
        [False],
    )
    before = runtime.runtime_path.read_bytes()

    monkeypatch.setattr(
        ground_truth,
        "_atomic_replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ControlMutationUncertain("fixture uncertainty")
        ),
    )

    result = controller.inject()

    assert result.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert result.terminal_result.reason_code == "CONTROL_MUTATION_UNCERTAIN"
    assert result.observer_event.transition_succeeded is False
    assert result.observer_event.error_category == "SAFETY"
    assert result.mutation_state is MutationState.UNKNOWN
    assert runtime.runtime_path.read_bytes() == before


def test_changed_file_then_evaluator_transition_failure_is_typed_41(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller, runtime, _client, _observer = _build_controller(
        tmp_path,
        [False, True],
    )
    monkeypatch.setattr(
        runtime,
        "finalize_transition",
        lambda _execution, **_kwargs: (_ for _ in ()).throw(
            OSError("fixture evaluator evidence failure")
        ),
    )

    result = controller.inject()

    payload = json.loads(runtime.runtime_path.read_text(encoding="utf-8"))
    assert payload["flags"]["adFailure"]["defaultVariant"] == "on"
    assert result.mutation_state is MutationState.APPLIED
    assert result.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert result.terminal_result.reason_code == "EVIDENCE_PERSISTENCE_FAILED"
    assert result.terminal_result.exit_code == 41
    emergency = (
        tmp_path / "evaluator-only" / RUN_ID / "emergency" / "evidence-failures.jsonl"
    )
    assert emergency.exists()
    assert json.loads(emergency.read_text(encoding="utf-8"))["failure_stage"] == (
        "EVALUATOR_TRANSITION"
    )


def test_changed_file_then_observer_transition_failure_is_typed_41(
    tmp_path: Path,
) -> None:
    client = FixtureOfrepClient([False, True])
    runtime = FlagdGroundTruthRuntime.bootstrap(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        ofrep_client=client,
        ofrep_endpoint="http://127.0.0.1:18016",
    )

    class FailingObserverSink:
        def write_event(self, _event) -> None:
            raise OSError("fixture observer evidence failure")

    controller = AdServiceFailureController(
        adapter=runtime,
        observer_sink=FailingObserverSink(),
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        clock=FixtureClock(),
        timeout_seconds=2,
        poll_interval_seconds=1,
    )

    result = controller.inject()

    payload = json.loads(runtime.runtime_path.read_text(encoding="utf-8"))
    assert payload["flags"]["adFailure"]["defaultVariant"] == "on"
    assert result.mutation_state is MutationState.APPLIED
    assert result.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert result.terminal_result.reason_code == "EVIDENCE_PERSISTENCE_FAILED"
    assert result.terminal_result.exit_code == 41
    emergency = (
        tmp_path / "evaluator-only" / RUN_ID / "emergency" / "evidence-failures.jsonl"
    )
    assert emergency.exists()
    assert json.loads(emergency.read_text(encoding="utf-8"))["failure_stage"] == (
        "OBSERVER_TRANSITION"
    )


def test_changed_file_then_raw_readback_persistence_failure_is_typed_41(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller, runtime, _client, _observer = _build_controller(
        tmp_path,
        [False, True],
    )
    original = runtime._persist_readback
    attempts = 0

    def fail_second(readback):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise ground_truth.EvidencePersistenceError(
                "fixture raw persistence failure"
            )
        return original(readback)

    monkeypatch.setattr(runtime, "_persist_readback", fail_second)

    result = controller.inject()

    payload = json.loads(runtime.runtime_path.read_text(encoding="utf-8"))
    assert payload["flags"]["adFailure"]["defaultVariant"] == "on"
    assert result.mutation_state is MutationState.APPLIED
    assert result.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert result.terminal_result.reason_code == "EVIDENCE_PERSISTENCE_FAILED"
    assert result.terminal_result.exit_code == 41


def test_cli_fixture_happy_path_inject_reset_and_read_only_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ecomsre import cli

    prepare_flagd_runtime(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
    )
    port = SimpleNamespace(
        kind="port",
        labels={"com.docker.compose.service": "flagd"},
        identity_evidence=(
            "service:flagd",
            "published_port:32768",
            "target_port:8016",
            "protocol:tcp",
        ),
    )
    ownership = SimpleNamespace(
        run_id=RUN_ID,
        manifest=SimpleNamespace(resources=(port,)),
    )
    readiness = ReadinessEvidence(
        schema_version="phase0.readiness-evidence.v1",
        run_id=RUN_ID,
        ownership_resources_complete=True,
        load_generator_ready=True,
        collector_ready=True,
        prometheus_fresh=True,
        jaeger_fresh=True,
        opensearch_fresh=True,
    )
    monkeypatch.setattr(cli, "_verify_upstream", lambda _context: None)
    monkeypatch.setattr(
        cli,
        "_current_docker_endpoint",
        lambda _context, _run_id: "unix:///var/run/docker.sock",
    )
    monkeypatch.setattr(
        cli,
        "load_authenticated_ownership_context",
        lambda *_args: ownership,
    )
    monkeypatch.setattr(
        cli,
        "health_environment",
        lambda *_args, **_kwargs: cli.TerminalResult(
            outcome=Outcome.SUCCESS,
            reason_code="ENVIRONMENT_HEALTHY",
        ),
    )
    monkeypatch.setattr(
        cli,
        "status_environment",
        lambda *_args, **_kwargs: cli.TerminalResult(
            outcome=Outcome.SUCCESS,
            reason_code="ENVIRONMENT_STATUS_CAPTURED",
        ),
    )

    def context(values: list[bool]) -> cli.HandlerContext:
        return cli.HandlerContext(
            runner=SimpleNamespace(),
            project_root=ROOT,
            artifacts_root=tmp_path,
            preflight_evidence=SimpleNamespace(),
            readiness_evidence=readiness,
            ofrep_client=FixtureOfrepClient(values),
        )

    injected = cli._handle_inject(
        SimpleNamespace(run_id=RUN_ID),
        context([False, True]),
    )
    reset = cli._handle_reset(
        SimpleNamespace(run_id=RUN_ID),
        context([True, False]),
    )
    events = tmp_path / "evaluator-only" / RUN_ID / "control-events.jsonl"
    event_count = len(events.read_text(encoding="utf-8").splitlines())
    runtime = tmp_path / "evaluator-only" / RUN_ID / "control" / "demo.flagd.json"
    status_inode = os.lstat(runtime).st_ino
    status = cli._handle_status(
        SimpleNamespace(run_id=RUN_ID),
        context([False]),
    )

    assert injected.outcome is Outcome.SUCCESS
    assert reset.outcome is Outcome.SUCCESS
    assert status.outcome is Outcome.SUCCESS
    assert status.reason_code == "CONTROL_STATE_CONFIRMED"
    assert len(events.read_text(encoding="utf-8").splitlines()) == event_count
    assert os.lstat(runtime).st_ino == status_inode


def test_cli_fixture_returns_typed_41_after_mutation_evidence_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ecomsre import cli

    prepare_flagd_runtime(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
    )
    port = SimpleNamespace(
        kind="port",
        labels={"com.docker.compose.service": "flagd"},
        identity_evidence=(
            "service:flagd",
            "published_port:32768",
            "target_port:8016",
            "protocol:tcp",
        ),
    )
    ownership = SimpleNamespace(
        run_id=RUN_ID,
        manifest=SimpleNamespace(resources=(port,)),
    )
    readiness = ReadinessEvidence(
        schema_version="phase0.readiness-evidence.v1",
        run_id=RUN_ID,
        ownership_resources_complete=True,
        load_generator_ready=True,
        collector_ready=True,
        prometheus_fresh=True,
        jaeger_fresh=True,
        opensearch_fresh=True,
    )
    monkeypatch.setattr(cli, "_verify_upstream", lambda _context: None)
    monkeypatch.setattr(
        cli,
        "_current_docker_endpoint",
        lambda _context, _run_id: "unix:///var/run/docker.sock",
    )
    monkeypatch.setattr(
        cli,
        "load_authenticated_ownership_context",
        lambda *_args: ownership,
    )
    monkeypatch.setattr(
        cli,
        "health_environment",
        lambda *_args, **_kwargs: cli.TerminalResult(
            outcome=Outcome.SUCCESS,
            reason_code="ENVIRONMENT_HEALTHY",
        ),
    )

    class FailingObserverSink:
        def __init__(self, _observer) -> None:
            pass

        def write_event(self, _event) -> None:
            raise OSError("fixture observer persistence failure")

    monkeypatch.setattr(cli, "ObserverControlEventSink", FailingObserverSink)
    context = cli.HandlerContext(
        runner=SimpleNamespace(),
        project_root=ROOT,
        artifacts_root=tmp_path,
        preflight_evidence=SimpleNamespace(),
        readiness_evidence=readiness,
        ofrep_client=FixtureOfrepClient([False, True]),
    )

    result = cli._handle_inject(
        SimpleNamespace(run_id=RUN_ID),
        context,
    )

    runtime = tmp_path / "evaluator-only" / RUN_ID / "control" / "demo.flagd.json"
    payload = json.loads(runtime.read_text(encoding="utf-8"))
    assert payload["flags"]["adFailure"]["defaultVariant"] == "on"
    assert result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert result.reason_code == "EVIDENCE_PERSISTENCE_FAILED"
    assert result.exit_code == 41


def test_ground_truth_rejects_non_opaque_run_id_before_any_write(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"

    with pytest.raises(ValueError, match="run_id"):
        prepare_flagd_runtime(
            project_root=ROOT,
            artifacts_root=artifacts,
            run_id="../semantic-scenario",
        )

    assert not artifacts.exists()


def test_single_file_inode_view_reproduces_stale_content_after_atomic_replace(
    tmp_path: Path,
) -> None:
    runtime = FlagdGroundTruthRuntime.bootstrap(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        ofrep_client=FixtureOfrepClient([False]),
        ofrep_endpoint="http://127.0.0.1:18016",
    )
    pinned_descriptor = os.open(runtime.runtime_path, os.O_RDONLY)
    try:
        runtime.apply_state(ground_truth.ScenarioState.INJECTED)
        os.lseek(pinned_descriptor, 0, os.SEEK_SET)
        stale_payload = json.loads(os.read(pinned_descriptor, 1024 * 1024))
        current_payload = json.loads(runtime.runtime_path.read_bytes())
    finally:
        os.close(pinned_descriptor)
        runtime.close()

    assert stale_payload["flags"]["adFailure"]["defaultVariant"] == "off"
    assert current_payload["flags"]["adFailure"]["defaultVariant"] == "on"


def test_directory_bound_view_reopens_replaced_runtime_entry(
    tmp_path: Path,
) -> None:
    runtime = FlagdGroundTruthRuntime.bootstrap(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        ofrep_client=FixtureOfrepClient([False]),
        ofrep_endpoint="http://127.0.0.1:18016",
    )
    directory_descriptor = os.open(runtime.runtime_path.parent, os.O_RDONLY)
    try:
        old_inode = os.stat(
            runtime.runtime_path.name,
            dir_fd=directory_descriptor,
        ).st_ino
        runtime.apply_state(ground_truth.ScenarioState.INJECTED)
        reopened = os.open(
            runtime.runtime_path.name,
            os.O_RDONLY,
            dir_fd=directory_descriptor,
        )
        try:
            payload = json.loads(os.read(reopened, 1024 * 1024))
            new_inode = os.fstat(reopened).st_ino
        finally:
            os.close(reopened)
    finally:
        os.close(directory_descriptor)
        runtime.close()

    assert new_inode != old_inode
    assert payload["flags"]["adFailure"]["defaultVariant"] == "on"


def test_same_process_concurrent_injects_linearize_to_one_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = RuntimeReflectingOfrepClient()
    runtime = FlagdGroundTruthRuntime.bootstrap(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        ofrep_client=client,
        ofrep_endpoint="http://127.0.0.1:18016",
    )
    client.runtime_path = runtime.runtime_path
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    first_apply_entered = threading.Event()
    release_first_apply = threading.Event()
    mutation_count = 0
    count_lock = threading.Lock()
    original_apply = runtime.apply_state

    def coordinated_apply(target):
        nonlocal mutation_count
        with count_lock:
            mutation_count += 1
            attempt = mutation_count
        if attempt == 1:
            first_apply_entered.set()
            assert release_first_apply.wait(timeout=2)
        return original_apply(target)

    monkeypatch.setattr(runtime, "apply_state", coordinated_apply)
    results = []

    def inject() -> None:
        controller = AdServiceFailureController(
            adapter=runtime,
            observer_sink=ObserverControlEventSink(observer),
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            timeout_seconds=2,
            poll_interval_seconds=0.01,
        )
        results.append(controller.inject())

    first = threading.Thread(target=inject)
    second = threading.Thread(target=inject)
    first.start()
    assert first_apply_entered.wait(timeout=2)
    second.start()
    time.sleep(0.05)
    release_first_apply.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert mutation_count == 1
    assert len(results) == 2
    assert all(result.terminal_result.outcome is Outcome.SUCCESS for result in results)
    assert {result.mutation_state for result in results} == {
        MutationState.APPLIED,
        MutationState.NOT_APPLIED,
    }
    lock_path = tmp_path / "evaluator-only" / RUN_ID / "locks" / "scenario-control.lock"
    metadata = os.lstat(lock_path)
    assert stat.S_ISREG(metadata.st_mode)
    assert metadata.st_uid == os.getuid()
    assert metadata.st_nlink == 1
    assert stat.S_IMODE(metadata.st_mode) == 0o600


def test_concurrent_inject_reset_evidence_matches_serialized_mutation_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = RuntimeReflectingOfrepClient()
    runtime = FlagdGroundTruthRuntime.bootstrap(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        ofrep_client=client,
        ofrep_endpoint="http://127.0.0.1:18016",
    )
    client.runtime_path = runtime.runtime_path
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    original_apply = runtime.apply_state
    original_finalize = runtime.finalize_transition
    first_record_entered = threading.Event()
    second_record_entered = threading.Event()
    release_first_record = threading.Event()
    mutation_order: list[tuple[str, str]] = []

    def tracked_apply(target):
        result = original_apply(target)
        mutation_order.append(
            (
                target.value,
                hashlib.sha256(runtime.runtime_path.read_bytes()).hexdigest(),
            )
        )
        return result

    def coordinated_finalize(
        execution,
        *,
        preparation,
        record_status,
    ):
        assert preparation is not None
        if preparation.transition_sequence == 1:
            first_record_entered.set()
            assert release_first_record.wait(timeout=2)
        else:
            second_record_entered.set()
        original_finalize(
            execution,
            preparation=preparation,
            record_status=record_status,
        )

    monkeypatch.setattr(runtime, "apply_state", tracked_apply)
    monkeypatch.setattr(runtime, "finalize_transition", coordinated_finalize)
    results = []

    def transition(action: str) -> None:
        controller = AdServiceFailureController(
            adapter=runtime,
            observer_sink=ObserverControlEventSink(observer),
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            timeout_seconds=2,
            poll_interval_seconds=0.01,
        )
        results.append(
            controller.inject() if action == "INJECT" else controller.reset()
        )

    inject = threading.Thread(target=transition, args=("INJECT",))
    reset = threading.Thread(target=transition, args=("RESET",))
    inject.start()
    assert first_record_entered.wait(timeout=2)
    reset.start()
    assert second_record_entered.wait(timeout=2)
    release_first_record.set()
    inject.join(timeout=2)
    reset.join(timeout=2)

    assert not inject.is_alive()
    assert not reset.is_alive()
    assert [target for target, _hash in mutation_order] == [
        "INJECTED",
        "BASELINE",
    ]
    hidden_records = [
        json.loads(line)
        for line in (tmp_path / "evaluator-only" / RUN_ID / "control-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    hidden_by_sequence = sorted(
        hidden_records,
        key=lambda record: record["transition_sequence"],
    )
    assert [record["action"] for record in hidden_by_sequence] == ["INJECT", "RESET"]
    assert [record["target_logical_state"] for record in hidden_by_sequence] == [
        "INJECTED",
        "BASELINE",
    ]
    assert [record["runtime_config_sha256"] for record in hidden_by_sequence] == [
        config_hash for _target, config_hash in mutation_order
    ]
    assert [record["transition_sequence"] for record in hidden_by_sequence] == [1, 2]
    assert len(results) == 2
    assert all(result.terminal_result.outcome is Outcome.SUCCESS for result in results)


def test_run_scoped_transition_guard_is_exclusive_across_processes(
    tmp_path: Path,
) -> None:
    runtime_path = prepare_flagd_runtime(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
    )
    assert runtime_path.exists()
    context = multiprocessing.get_context("fork")
    first_acquired = context.Event()
    second_acquired = context.Event()
    release_first = context.Event()
    release_second = context.Event()
    first = context.Process(
        target=_multiprocess_guard_worker,
        args=(str(tmp_path), first_acquired, release_first),
    )
    second = context.Process(
        target=_multiprocess_guard_worker,
        args=(str(tmp_path), second_acquired, release_second),
    )

    first.start()
    assert first_acquired.wait(timeout=2)
    second.start()
    assert not second_acquired.wait(timeout=0.1)
    release_first.set()
    assert second_acquired.wait(timeout=2)
    release_second.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert first.exitcode == 0
    assert second.exitcode == 0


def test_lock_acquisition_timeout_persists_legal_prelock_hidden_event(
    tmp_path: Path,
) -> None:
    client = RuntimeReflectingOfrepClient()
    runtime = FlagdGroundTruthRuntime.bootstrap(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        ofrep_client=client,
        ofrep_endpoint="http://127.0.0.1:18016",
    )
    client.runtime_path = runtime.runtime_path
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    holder_acquired = threading.Event()
    release_holder = threading.Event()

    def hold_guard() -> None:
        with runtime.transition_guard(timeout_seconds=2) as locked:
            assert locked is True
            holder_acquired.set()
            assert release_holder.wait(timeout=2)

    holder = threading.Thread(target=hold_guard)
    holder.start()
    assert holder_acquired.wait(timeout=2)
    result = AdServiceFailureController(
        adapter=runtime,
        observer_sink=ObserverControlEventSink(observer),
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        timeout_seconds=0.05,
        poll_interval_seconds=0.01,
    ).inject()
    release_holder.set()
    holder.join(timeout=2)

    assert not holder.is_alive()
    assert result.terminal_result.outcome is Outcome.FAILED_ACCEPTANCE
    assert result.terminal_result.reason_code == "INJECT_TIMEOUT"
    assert result.terminal_result.exit_code == 30
    assert result.mutation_state is MutationState.NOT_APPLIED
    hidden = [
        json.loads(line)
        for line in (tmp_path / "evaluator-only" / RUN_ID / "control-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(hidden) == 1
    assert hidden[0]["record_status"] == "PRELOCK_TIMEOUT"
    assert hidden[0]["observation_basis"] == "PRELOCK_UNAVAILABLE"
    assert hidden[0]["runtime_config_sha256"] is None
    assert hidden[0]["before_logical_state"] == "UNKNOWN"
    assert hidden[0]["mutation_state"] == "NOT_APPLIED"
    assert hidden[0]["transition_succeeded"] is False
    assert not (
        tmp_path / "evaluator-only" / RUN_ID / "control-prepared.jsonl"
    ).exists()
    runtime.close()


def test_transition_guard_cleanup_attempts_unlock_close_and_thread_release(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = FlagdGroundTruthRuntime.bootstrap(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        ofrep_client=FixtureOfrepClient([False]),
        ofrep_endpoint="http://127.0.0.1:18016",
    )
    real_flock = ground_truth.fcntl.flock
    unlock_descriptors: list[int] = []

    def fail_unlock(descriptor: int, operation: int) -> None:
        if operation == ground_truth.fcntl.LOCK_UN:
            unlock_descriptors.append(descriptor)
            raise OSError("fixture unlock failure")
        real_flock(descriptor, operation)

    monkeypatch.setattr(ground_truth.fcntl, "flock", fail_unlock)
    try:
        with pytest.raises(TransitionGuardCleanupError):
            with runtime.transition_guard(timeout_seconds=1) as locked:
                assert locked is True

        assert len(unlock_descriptors) == 1
        with pytest.raises(OSError):
            os.fstat(unlock_descriptors[0])

        monkeypatch.setattr(ground_truth.fcntl, "flock", real_flock)
        with runtime.transition_guard(timeout_seconds=1) as locked:
            assert locked is True
    finally:
        monkeypatch.setattr(ground_truth.fcntl, "flock", real_flock)
        for descriptor in unlock_descriptors:
            try:
                real_flock(descriptor, ground_truth.fcntl.LOCK_UN)
                os.close(descriptor)
            except OSError:
                pass
        runtime.close()


def test_cleanup_failure_records_linked_failure_without_any_success_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = RuntimeReflectingOfrepClient()
    runtime = FlagdGroundTruthRuntime.bootstrap(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        ofrep_client=client,
        ofrep_endpoint="http://127.0.0.1:18016",
    )
    client.runtime_path = runtime.runtime_path
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    real_flock = ground_truth.fcntl.flock
    unlock_descriptors: list[int] = []
    transition_descriptors: set[int] = set()
    failed_transition_unlock = False

    def fail_unlock(descriptor: int, operation: int) -> None:
        nonlocal failed_transition_unlock
        if operation == (ground_truth.fcntl.LOCK_EX | ground_truth.fcntl.LOCK_NB):
            transition_descriptors.add(descriptor)
        if (
            operation == ground_truth.fcntl.LOCK_UN
            and descriptor in transition_descriptors
            and not failed_transition_unlock
        ):
            failed_transition_unlock = True
            unlock_descriptors.append(descriptor)
            raise OSError("fixture unlock failure")
        real_flock(descriptor, operation)

    monkeypatch.setattr(ground_truth.fcntl, "flock", fail_unlock)
    result = AdServiceFailureController(
        adapter=runtime,
        observer_sink=ObserverControlEventSink(observer),
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        timeout_seconds=2,
        poll_interval_seconds=0.01,
    ).inject()
    monkeypatch.setattr(ground_truth.fcntl, "flock", real_flock)

    prepared = [
        json.loads(line)
        for line in (tmp_path / "evaluator-only" / RUN_ID / "control-prepared.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    hidden = [
        json.loads(line)
        for line in (tmp_path / "evaluator-only" / RUN_ID / "control-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    observer_path = observer.root / "changes" / "changes.jsonl"
    observer_records = (
        [
            json.loads(line)
            for line in observer_path.read_text(encoding="utf-8").splitlines()
        ]
        if observer_path.exists()
        else []
    )

    assert result.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert result.terminal_result.exit_code == 41
    assert len(prepared) == 1
    assert prepared[0]["record_status"] == "PREPARED"
    assert prepared[0]["transition_sequence"] == 1
    assert hidden[-1]["record_status"] == "CLEANUP_FAILED"
    assert hidden[-1]["preparation_id"] == prepared[0]["preparation_id"]
    assert hidden[-1]["transition_sequence"] == prepared[0]["transition_sequence"]
    assert all(record["transition_succeeded"] is False for record in hidden)
    assert all(record["transition_succeeded"] is False for record in observer_records)
    assert len(unlock_descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(unlock_descriptors[0])
    with runtime.transition_guard(timeout_seconds=1) as locked:
        assert locked is True
    runtime.close()


def test_cleanup_keyboard_interrupt_propagates_after_linked_interrupted_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = RuntimeReflectingOfrepClient()
    runtime = FlagdGroundTruthRuntime.bootstrap(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        ofrep_client=client,
        ofrep_endpoint="http://127.0.0.1:18016",
    )
    client.runtime_path = runtime.runtime_path
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    real_flock = ground_truth.fcntl.flock
    unlock_descriptors: list[int] = []
    transition_descriptors: set[int] = set()
    interrupted_transition_unlock = False

    def interrupt_unlock(descriptor: int, operation: int) -> None:
        nonlocal interrupted_transition_unlock
        if operation == (ground_truth.fcntl.LOCK_EX | ground_truth.fcntl.LOCK_NB):
            transition_descriptors.add(descriptor)
        if (
            operation == ground_truth.fcntl.LOCK_UN
            and descriptor in transition_descriptors
            and not interrupted_transition_unlock
        ):
            interrupted_transition_unlock = True
            unlock_descriptors.append(descriptor)
            raise KeyboardInterrupt
        real_flock(descriptor, operation)

    monkeypatch.setattr(ground_truth.fcntl, "flock", interrupt_unlock)
    with pytest.raises(KeyboardInterrupt):
        AdServiceFailureController(
            adapter=runtime,
            observer_sink=ObserverControlEventSink(observer),
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            timeout_seconds=2,
            poll_interval_seconds=0.01,
        ).inject()
    monkeypatch.setattr(ground_truth.fcntl, "flock", real_flock)

    prepared = [
        json.loads(line)
        for line in (tmp_path / "evaluator-only" / RUN_ID / "control-prepared.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    hidden = [
        json.loads(line)
        for line in (tmp_path / "evaluator-only" / RUN_ID / "control-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    observer_path = observer.root / "changes" / "changes.jsonl"
    observer_records = (
        [
            json.loads(line)
            for line in observer_path.read_text(encoding="utf-8").splitlines()
        ]
        if observer_path.exists()
        else []
    )

    assert prepared[0]["record_status"] == "PREPARED"
    assert hidden[-1]["record_status"] == "INTERRUPTED"
    assert hidden[-1]["preparation_id"] == prepared[0]["preparation_id"]
    assert hidden[-1]["transition_succeeded"] is False
    assert all(record["transition_succeeded"] is False for record in observer_records)
    assert len(unlock_descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(unlock_descriptors[0])
    with runtime.transition_guard(timeout_seconds=1) as locked:
        assert locked is True
    runtime.close()


def test_transition_guard_preserves_body_base_exception_when_cleanup_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = FlagdGroundTruthRuntime.bootstrap(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        ofrep_client=FixtureOfrepClient([False]),
        ofrep_endpoint="http://127.0.0.1:18016",
    )
    real_flock = ground_truth.fcntl.flock
    unlock_descriptors: list[int] = []

    def fail_unlock(descriptor: int, operation: int) -> None:
        if operation == ground_truth.fcntl.LOCK_UN:
            unlock_descriptors.append(descriptor)
            raise OSError("fixture unlock failure")
        real_flock(descriptor, operation)

    monkeypatch.setattr(ground_truth.fcntl, "flock", fail_unlock)
    try:
        with pytest.raises(KeyboardInterrupt):
            with runtime.transition_guard(timeout_seconds=1) as locked:
                assert locked is True
                raise KeyboardInterrupt

        assert len(unlock_descriptors) == 1
        with pytest.raises(OSError):
            os.fstat(unlock_descriptors[0])
    finally:
        monkeypatch.setattr(ground_truth.fcntl, "flock", real_flock)
        for descriptor in unlock_descriptors:
            try:
                real_flock(descriptor, ground_truth.fcntl.LOCK_UN)
                os.close(descriptor)
            except OSError:
                pass
        runtime.close()


def test_thread_lock_registry_returns_to_baseline_for_unique_run_roots(
    tmp_path: Path,
) -> None:
    baseline = len(ground_truth._THREAD_LOCKS)

    for index in range(20):
        runtime = FlagdGroundTruthRuntime.bootstrap(
            project_root=ROOT,
            artifacts_root=tmp_path / f"root-{index}",
            run_id=RUN_ID,
            ofrep_client=FixtureOfrepClient([False]),
            ofrep_endpoint="http://127.0.0.1:18016",
        )
        try:
            with runtime.transition_guard(timeout_seconds=1) as locked:
                assert locked is True
        finally:
            runtime.close()

    assert len(ground_truth._THREAD_LOCKS) == baseline


def test_mutation_then_keyboard_interrupt_preserves_durable_pending_intent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = RuntimeReflectingOfrepClient()
    runtime = FlagdGroundTruthRuntime.bootstrap(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        ofrep_client=client,
        ofrep_endpoint="http://127.0.0.1:18016",
    )
    client.runtime_path = runtime.runtime_path
    baseline_hash = hashlib.sha256(runtime.runtime_path.read_bytes()).hexdigest()
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    original_apply = runtime.apply_state

    def interrupt_after_mutation(target):
        original_apply(target)
        raise KeyboardInterrupt

    monkeypatch.setattr(runtime, "apply_state", interrupt_after_mutation)
    controller = AdServiceFailureController(
        adapter=runtime,
        observer_sink=ObserverControlEventSink(observer),
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        timeout_seconds=2,
        poll_interval_seconds=0.01,
    )

    with pytest.raises(KeyboardInterrupt):
        controller.inject()

    payload = json.loads(runtime.runtime_path.read_text(encoding="utf-8"))
    assert payload["flags"]["adFailure"]["defaultVariant"] == "on"
    intent_path = tmp_path / "evaluator-only" / RUN_ID / "control-intents.jsonl"
    records = [
        json.loads(line)
        for line in intent_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    pending = records[0]
    assert pending["event_type"] == "PENDING"
    assert pending["action"] == "INJECT"
    assert pending["before_state"] == "BASELINE"
    assert pending["target_state"] == "INJECTED"
    assert pending["mutation_state"] == "UNKNOWN"
    assert pending["runtime_config_sha256"] == baseline_hash
    assert pending["deadline_monotonic"] > pending["started_monotonic"]
    assert pending["event_id"]


def test_retry_reconciles_and_links_crash_pending_before_idempotent_success(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = RuntimeReflectingOfrepClient()
    runtime = FlagdGroundTruthRuntime.bootstrap(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        ofrep_client=client,
        ofrep_endpoint="http://127.0.0.1:18016",
    )
    client.runtime_path = runtime.runtime_path
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    original_apply = runtime.apply_state
    mutation_count = 0

    def interrupt_after_mutation(target):
        nonlocal mutation_count
        mutation_count += 1
        original_apply(target)
        raise KeyboardInterrupt

    monkeypatch.setattr(runtime, "apply_state", interrupt_after_mutation)
    crashed = AdServiceFailureController(
        adapter=runtime,
        observer_sink=ObserverControlEventSink(observer),
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        timeout_seconds=2,
        poll_interval_seconds=0.01,
    )
    with pytest.raises(KeyboardInterrupt):
        crashed.inject()

    monkeypatch.setattr(runtime, "apply_state", original_apply)
    retried = AdServiceFailureController(
        adapter=runtime,
        observer_sink=ObserverControlEventSink(observer),
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        timeout_seconds=2,
        poll_interval_seconds=0.01,
    ).inject()

    assert retried.terminal_result.outcome is Outcome.SUCCESS
    assert retried.mutation_state is MutationState.NOT_APPLIED
    assert mutation_count == 1
    intent_path = tmp_path / "evaluator-only" / RUN_ID / "control-intents.jsonl"
    records = [
        json.loads(line)
        for line in intent_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["event_type"] for record in records] == [
        "PENDING",
        "RECOVERY",
    ]
    assert records[1]["linked_intent_id"] == records[0]["event_id"]
    assert records[1]["observed_state"] == "INJECTED"
    assert records[1]["resolution"] == "TARGET_CONFIRMED"


def test_pending_target_raw_recovery_evidence_failure_is_manual_41_then_retryable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = RuntimeReflectingOfrepClient()
    runtime = FlagdGroundTruthRuntime.bootstrap(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        ofrep_client=client,
        ofrep_endpoint="http://127.0.0.1:18016",
    )
    client.runtime_path = runtime.runtime_path
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    original_apply = runtime.apply_state

    def interrupt_after_mutation(target):
        original_apply(target)
        raise KeyboardInterrupt

    monkeypatch.setattr(runtime, "apply_state", interrupt_after_mutation)
    with pytest.raises(KeyboardInterrupt):
        AdServiceFailureController(
            adapter=runtime,
            observer_sink=ObserverControlEventSink(observer),
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            timeout_seconds=2,
            poll_interval_seconds=0.01,
        ).inject()
    monkeypatch.setattr(runtime, "apply_state", original_apply)

    original_persist = runtime._persist_readback

    def fail_recovery_readback(_readback):
        raise ground_truth.EvidencePersistenceError(
            "fixture pending recovery raw evidence failure"
        )

    monkeypatch.setattr(runtime, "_persist_readback", fail_recovery_readback)
    failed = AdServiceFailureController(
        adapter=runtime,
        observer_sink=ObserverControlEventSink(observer),
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        timeout_seconds=2,
        poll_interval_seconds=0.01,
    ).inject()

    intent_path = tmp_path / "evaluator-only" / RUN_ID / "control-intents.jsonl"
    assert failed.before_state is ground_truth.ScenarioState.INJECTED
    assert failed.mutation_state is MutationState.UNKNOWN
    assert failed.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert failed.terminal_result.reason_code == "EVIDENCE_PERSISTENCE_FAILED"
    assert failed.terminal_result.exit_code == 41
    assert [
        json.loads(line)["event_type"]
        for line in intent_path.read_text(encoding="utf-8").splitlines()
    ] == ["PENDING"]
    emergency = (
        tmp_path / "evaluator-only" / RUN_ID / "emergency" / "evidence-failures.jsonl"
    )
    assert json.loads(emergency.read_text(encoding="utf-8"))["failure_stage"] == (
        "PENDING_RECOVERY_EVIDENCE"
    )

    monkeypatch.setattr(runtime, "_persist_readback", original_persist)
    retried = AdServiceFailureController(
        adapter=runtime,
        observer_sink=ObserverControlEventSink(observer),
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        timeout_seconds=2,
        poll_interval_seconds=0.01,
    ).inject()

    assert retried.terminal_result.outcome is Outcome.SUCCESS
    assert retried.mutation_state is MutationState.NOT_APPLIED
    assert [
        json.loads(line)["event_type"]
        for line in intent_path.read_text(encoding="utf-8").splitlines()
    ] == ["PENDING", "RECOVERY"]


def test_pending_target_resolution_write_failure_is_manual_41_then_retryable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = RuntimeReflectingOfrepClient()
    runtime = FlagdGroundTruthRuntime.bootstrap(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        ofrep_client=client,
        ofrep_endpoint="http://127.0.0.1:18016",
    )
    client.runtime_path = runtime.runtime_path
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)
    original_apply = runtime.apply_state

    def interrupt_after_mutation(target):
        original_apply(target)
        raise KeyboardInterrupt

    monkeypatch.setattr(runtime, "apply_state", interrupt_after_mutation)
    with pytest.raises(KeyboardInterrupt):
        AdServiceFailureController(
            adapter=runtime,
            observer_sink=ObserverControlEventSink(observer),
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            timeout_seconds=2,
            poll_interval_seconds=0.01,
        ).inject()
    monkeypatch.setattr(runtime, "apply_state", original_apply)

    original_append = runtime._append_intent_record

    def fail_recovery_resolution(record):
        if (
            isinstance(record, ground_truth._ControlIntentResolution)
            and record.event_type == "RECOVERY"
        ):
            raise ground_truth.EvidencePersistenceError(
                "fixture pending recovery resolution failure"
            )
        original_append(record)

    monkeypatch.setattr(runtime, "_append_intent_record", fail_recovery_resolution)
    failed = AdServiceFailureController(
        adapter=runtime,
        observer_sink=ObserverControlEventSink(observer),
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        timeout_seconds=2,
        poll_interval_seconds=0.01,
    ).inject()

    intent_path = tmp_path / "evaluator-only" / RUN_ID / "control-intents.jsonl"
    assert failed.before_state is ground_truth.ScenarioState.INJECTED
    assert failed.mutation_state is MutationState.UNKNOWN
    assert failed.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert failed.terminal_result.reason_code == "EVIDENCE_PERSISTENCE_FAILED"
    assert failed.terminal_result.exit_code == 41
    assert [
        json.loads(line)["event_type"]
        for line in intent_path.read_text(encoding="utf-8").splitlines()
    ] == ["PENDING"]

    monkeypatch.setattr(runtime, "_append_intent_record", original_append)
    retried = AdServiceFailureController(
        adapter=runtime,
        observer_sink=ObserverControlEventSink(observer),
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        timeout_seconds=2,
        poll_interval_seconds=0.01,
    ).inject()

    assert retried.terminal_result.outcome is Outcome.SUCCESS
    assert retried.mutation_state is MutationState.NOT_APPLIED
    assert [
        json.loads(line)["event_type"]
        for line in intent_path.read_text(encoding="utf-8").splitlines()
    ] == ["PENDING", "RECOVERY"]
