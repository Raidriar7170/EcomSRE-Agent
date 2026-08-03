import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import ecomsre.cli as cli_module
import ecomsre.environment.bootstrap as bootstrap_module
import ecomsre.environment.live_preflight as live_preflight_module
import ecomsre.environment.manifests as manifests_module
import ecomsre.phase0.smoke as smoke_module
from ecomsre.environment.manifests import (
    InspectedImage,
    ResolvedComposeConfig,
    generate_candidate_image_lock,
)
from ecomsre.environment.ownership_authority import OwnershipAuthorityError
from ecomsre.environment.preflight import CommandResult
from ecomsre.evidence.hashes import sha256_bytes
from ecomsre.phase0.models import DiagnosticStatus, Outcome, TerminalResult


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "b" * 32
DOCKER_ENDPOINT = "unix:///var/run/docker.sock"
DAEMON_ID = "fixture-daemon"
SOURCE = "otel/demo:3.0.0-ad"
OLD_COMPOSE = ResolvedComposeConfig.from_stdout(
    json.dumps(
        {
            "services": {
                "ad": {
                    "container_name": "ecomsre-phase0-ad",
                    "image": SOURCE,
                    "ports": [],
                }
            }
        },
        sort_keys=True,
    )
)
NEW_COMPOSE = ResolvedComposeConfig.from_stdout(
    json.dumps(
        {
            "name": "ecomsre-phase0",
            "services": {
                "ad": {
                    "container_name": "ecomsre-phase0-ad",
                    "image": SOURCE,
                    "ports": [],
                }
            },
        },
        sort_keys=True,
    )
)


def _image(**overrides: object) -> InspectedImage:
    values: dict[str, object] = {
        "logical_name": "ad",
        "source_reference": SOURCE,
        "image_index_digest": "sha256:" + "1" * 64,
        "resolved_platform_digest": "sha256:" + "2" * 64,
        "architecture": "arm64",
        "platform": "linux/arm64",
        "image_id": "sha256:" + "3" * 64,
    }
    values.update(overrides)
    return InspectedImage(**values)


def _locked_manifest():
    return generate_candidate_image_lock(
        images=(_image(),),
        resolved_compose=OLD_COMPOSE,
        acquired_at=datetime(2026, 7, 30, 10, 37, tzinfo=UTC),
    )


def _serialized_lock(lock=None) -> bytes:
    active = lock or _locked_manifest()
    return (
        json.dumps(
            active.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_locked_path(tmp_path: Path) -> tuple[Path, bytes, str]:
    lock_path = tmp_path / "config" / "phase0" / "image-lock.json"
    lock_path.parent.mkdir(parents=True)
    original = _serialized_lock()
    lock_path.write_bytes(original)
    lock_path.chmod(0o600)
    return lock_path, original, sha256_bytes(original)


def _rotation_kwargs(lock_path: Path, old_sha256: str) -> dict[str, object]:
    return {
        "path": lock_path,
        "resolved_compose": NEW_COMPOSE,
        "cached_images": (_image(),),
        "expected_old_lock_sha256": old_sha256,
        "rotation_reason": "COMPOSE_OVERRIDE_CHANGED",
        "rotated_at": datetime(2026, 7, 31, 1, 0, tzinfo=UTC),
    }


def _resolved_mount_plan() -> ResolvedComposeConfig:
    upstream = ROOT / "third_party" / "opentelemetry-demo"
    labels = {
        "io.ecomsre.project": "ecomsre-phase0",
        "io.ecomsre.run": RUN_ID,
    }
    volumes = {
        name: {
            "name": f"ecomsre-phase0-{RUN_ID}-{name}",
            "labels": labels,
        }
        for name in (
            "astronomy-db-data",
            "jaeger-data",
            "prometheus-data",
        )
    }
    services = {
        "astronomy-db": {
            "image": "postgres:18.4",
            "volumes": [
                {
                    "type": "volume",
                    "source": "astronomy-db-data",
                    "target": "/var/lib/postgresql",
                }
            ],
        },
        "jaeger": {
            "image": "quay.io/jaegertracing/jaeger:2.19.0",
            "volumes": [
                {
                    "type": "bind",
                    "source": str(upstream / "src" / "jaeger" / "config.yml"),
                    "target": "/etc/jaeger/config.yml",
                    "read_only": True,
                },
                {
                    "type": "bind",
                    "source": str(upstream / "src" / "jaeger" / "ui-config.json"),
                    "target": "/etc/jaeger/ui-config.json",
                    "read_only": True,
                },
                {
                    "type": "volume",
                    "source": "jaeger-data",
                    "target": "/tmp",
                },
            ],
        },
        "prometheus": {
            "image": "quay.io/prometheus/prometheus:v3.13.1",
            "volumes": [
                {
                    "type": "bind",
                    "source": str(
                        upstream
                        / "src"
                        / "prometheus"
                        / "prometheus-config.yaml"
                    ),
                    "target": "/etc/prometheus/prometheus-config.yaml",
                    "read_only": True,
                },
                {
                    "type": "volume",
                    "source": "prometheus-data",
                    "target": "/prometheus",
                },
            ],
        },
    }
    return ResolvedComposeConfig.from_stdout(
        json.dumps(
            {"services": services, "volumes": volumes},
            sort_keys=True,
        )
    )


def test_override_preserves_required_observability_config_binds() -> None:
    text = (ROOT / "config" / "phase0" / "compose.phase0.yaml").read_text(
        encoding="utf-8"
    )

    assert "source: ./src/jaeger/config.yml" in text
    assert "target: /etc/jaeger/config.yml" in text
    assert "source: ./src/jaeger/ui-config.json" in text
    assert "target: /etc/jaeger/ui-config.json" in text
    assert "source: ./src/prometheus/prometheus-config.yaml" in text
    assert "target: /etc/prometheus/prometheus-config.yaml" in text
    assert text.count("read_only: true") >= 5


def test_resolved_mount_plan_requires_exact_read_only_upstream_config_binds() -> None:
    from ecomsre.environment import lifecycle

    resolved = _resolved_mount_plan()

    lifecycle._require_explicit_volume_plan(
        resolved,
        run_id=RUN_ID,
        project_root=ROOT,
    )

    payload = json.loads(resolved.stdout)
    payload["services"]["jaeger"]["volumes"] = [
        mount
        for mount in payload["services"]["jaeger"]["volumes"]
        if mount["target"] != "/etc/jaeger/ui-config.json"
    ]
    missing = ResolvedComposeConfig.from_stdout(json.dumps(payload, sort_keys=True))
    with pytest.raises(ValueError, match="config bind"):
        lifecycle._require_explicit_volume_plan(
            missing,
            run_id=RUN_ID,
            project_root=ROOT,
        )

    payload = json.loads(resolved.stdout)
    payload["services"]["prometheus"]["volumes"][0]["read_only"] = False
    writable = ResolvedComposeConfig.from_stdout(json.dumps(payload, sort_keys=True))
    with pytest.raises(ValueError, match="config bind"):
        lifecycle._require_explicit_volume_plan(
            writable,
            run_id=RUN_ID,
            project_root=ROOT,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "/outside/frozen-upstream/config.yml"),
        ("target", "/etc/jaeger/wrong-config.yml"),
    ],
)
def test_resolved_mount_plan_rejects_wrong_config_bind_identity(
    field: str,
    value: str,
) -> None:
    from ecomsre.environment import lifecycle

    payload = json.loads(_resolved_mount_plan().stdout)
    payload["services"]["jaeger"]["volumes"][0][field] = value

    with pytest.raises(ValueError, match="config bind"):
        lifecycle._require_explicit_volume_plan(
            ResolvedComposeConfig.from_stdout(
                json.dumps(payload, sort_keys=True)
            ),
            run_id=RUN_ID,
            project_root=ROOT,
        )


def test_observer_projection_omits_validated_bind_sources() -> None:
    from ecomsre.environment import lifecycle

    projected = lifecycle._project_resolved_compose_for_observer(
        _resolved_mount_plan().stdout
    )
    serialized = json.dumps(projected, sort_keys=True)

    assert "volumes" not in serialized
    assert "third_party/opentelemetry-demo" not in serialized
    assert "/etc/jaeger" not in serialized
    assert "/etc/prometheus" not in serialized


def test_stale_locked_bootstrap_requires_explicit_rotation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    lock_path = project / "config" / "phase0" / "image-lock.json"
    lock_path.parent.mkdir(parents=True)
    original = _serialized_lock()
    lock_path.write_bytes(original)
    runner = SimpleNamespace(
        run=lambda *_args, **_kwargs: CommandResult(
            arguments=(),
            exit_code=0,
            stdout=NEW_COMPOSE.stdout,
            stderr="",
        )
    )
    monkeypatch.setattr(
        bootstrap_module,
        "_compose_command_evidence_link",
        lambda *_args, **_kwargs: {},
    )

    with pytest.raises(
        bootstrap_module.ImageLockRotationRequired,
        match="IMAGE_LOCK_ROTATION_REQUIRED",
    ):
        bootstrap_module.bootstrap_image_lock(
            project_root=project,
            artifacts_root=tmp_path / "artifacts",
            run_id=RUN_ID,
            runner=runner,
            docker_endpoint=DOCKER_ENDPOINT,
        )

    assert lock_path.read_bytes() == original


def test_bootstrap_rotation_requires_all_explicit_authorization_fields(
    tmp_path: Path,
) -> None:
    context = cli_module.HandlerContext(
        runner=SimpleNamespace(
            run=lambda *_args, **_kwargs: pytest.fail(
                "partial rotation authorization must block before commands"
            )
        ),
        project_root=tmp_path,
        artifacts_root=tmp_path / "artifacts",
    )

    result = cli_module._handle_bootstrap(
        SimpleNamespace(
            run_id=RUN_ID,
            rotate_image_lock=True,
            expected_old_lock_sha256=None,
            rotation_reason="COMPOSE_OVERRIDE_CHANGED",
        ),
        context,
    )

    assert result.outcome is Outcome.INVALID_INVOCATION
    assert result.reason_code == "IMAGE_LOCK_ROTATION_ARGUMENTS_REQUIRED"


def test_rotation_expected_hash_mismatch_preserves_current_lock(
    tmp_path: Path,
) -> None:
    lock_path, original, _old_sha256 = _write_locked_path(tmp_path)
    kwargs = _rotation_kwargs(lock_path, "0" * 64)

    with pytest.raises(ValueError, match="expected old lock"):
        manifests_module.rotate_candidate_image_lock(**kwargs)

    assert lock_path.read_bytes() == original


def test_rotation_rejects_source_set_change_without_writing(
    tmp_path: Path,
) -> None:
    lock_path, original, old_sha256 = _write_locked_path(tmp_path)
    changed_source = "otel/demo:3.0.0-cart"
    changed = ResolvedComposeConfig.from_stdout(
        json.dumps(
            {"services": {"cart": {"image": changed_source}}},
            sort_keys=True,
        )
    )
    kwargs = _rotation_kwargs(lock_path, old_sha256)
    kwargs["resolved_compose"] = changed

    with pytest.raises(
        ValueError,
        match="IMAGE_LOCK_SOURCE_SET_CHANGED_REQUIRES_FULL_BOOTSTRAP",
    ):
        manifests_module.rotate_candidate_image_lock(**kwargs)

    assert lock_path.read_bytes() == original


@pytest.mark.parametrize(
    "overrides",
    [
        {"architecture": "amd64", "platform": "linux/amd64"},
        {"image_id": "sha256:" + "9" * 64},
        {"resolved_platform_digest": "sha256:" + "8" * 64},
    ],
)
def test_rotation_rejects_cached_image_drift_and_preserves_current_lock(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    lock_path, original, old_sha256 = _write_locked_path(tmp_path)
    kwargs = _rotation_kwargs(lock_path, old_sha256)
    kwargs["cached_images"] = (_image(**overrides),)

    with pytest.raises(ValueError, match="cached image"):
        manifests_module.rotate_candidate_image_lock(**kwargs)

    assert lock_path.read_bytes() == original


def test_rotation_preserves_history_and_revalidates_new_lock(tmp_path: Path) -> None:
    lock_path, original, old_sha256 = _write_locked_path(tmp_path)

    result = manifests_module.rotate_candidate_image_lock(
        **_rotation_kwargs(lock_path, old_sha256)
    )

    history = (
        lock_path.parent / "image-lock-history" / f"{old_sha256}.json"
    )
    assert history.read_bytes() == original
    assert result.lock.compose_config_sha256 == NEW_COMPOSE.sha256
    assert result.verification.passed
    assert result.evidence.rotation_reason == "COMPOSE_OVERRIDE_CHANGED"
    assert result.evidence.old_compose_config_sha256 == OLD_COMPOSE.sha256
    assert result.evidence.new_compose_config_sha256 == NEW_COMPOSE.sha256
    assert result.evidence.old_lock_sha256 == old_sha256
    assert result.evidence.new_lock_sha256 == sha256_bytes(lock_path.read_bytes())


def test_rotation_accepts_identical_existing_history(tmp_path: Path) -> None:
    lock_path, original, old_sha256 = _write_locked_path(tmp_path)
    history = lock_path.parent / "image-lock-history" / f"{old_sha256}.json"
    history.parent.mkdir()
    history.write_bytes(original)

    result = manifests_module.rotate_candidate_image_lock(
        **_rotation_kwargs(lock_path, old_sha256)
    )

    assert result.verification.passed
    assert history.read_bytes() == original


def test_rotation_rejects_conflicting_existing_history(tmp_path: Path) -> None:
    lock_path, original, old_sha256 = _write_locked_path(tmp_path)
    history = lock_path.parent / "image-lock-history" / f"{old_sha256}.json"
    history.parent.mkdir()
    history.write_bytes(b"conflict\n")

    with pytest.raises(ValueError, match="history"):
        manifests_module.rotate_candidate_image_lock(
            **_rotation_kwargs(lock_path, old_sha256)
        )

    assert lock_path.read_bytes() == original


def test_rotation_cas_rejects_concurrent_current_lock_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path, _original, old_sha256 = _write_locked_path(tmp_path)
    original_persist = manifests_module._persist_image_lock_history
    concurrent = b'{"concurrent":true}\n'

    def persist_then_replace(*args: object, **kwargs: object):
        result = original_persist(*args, **kwargs)
        lock_path.write_bytes(concurrent)
        return result

    monkeypatch.setattr(
        manifests_module,
        "_persist_image_lock_history",
        persist_then_replace,
    )

    with pytest.raises(ValueError, match="changed before rotation"):
        manifests_module.rotate_candidate_image_lock(
            **_rotation_kwargs(lock_path, old_sha256)
        )

    assert lock_path.read_bytes() == concurrent


class _StartOperations:
    def __init__(self, start):
        self.start = start
        self.events: list[str] = []

    def start_environment(self):
        self.events.append("up")
        return self.start

    def fresh_authority(self, boundary: str):
        self.events.append(f"authority:{boundary}")
        return boundary

    def initial_readiness(self, authority):
        self.events.append("readiness")

    def open_control(self, authority):
        self.events.append("control")
        return object()

    def promote(self, authority, control):
        return object()

    def frozen_readiness(self, authority):
        return None

    def diagnostic(self, authority, control):
        return None

    def final_readiness(self, authority):
        return None

    def refresh_before_reset(self, control):
        return None

    def reset(self, control):
        return TerminalResult(outcome=Outcome.SUCCESS, reason_code="RESET")

    def close_control(self, control):
        return None

    def fresh_stop_authority(self):
        self.events.append("authority:stop")
        return SimpleNamespace(evidence_persistence_error=None)

    def stop_environment(self, authority):
        self.events.append("down")
        return TerminalResult(outcome=Outcome.SUCCESS, reason_code="DOWN")

    def cleanup_owned_volumes(self, authority):
        self.events.append("cleanup:volumes")
        return TerminalResult(
            outcome=Outcome.SUCCESS,
            reason_code="OWNED_NAMED_VOLUMES_CLEANED",
        )

    def finalize(self, state):
        self.events.append("finalize")
        return state

    def write_minimal_terminal(self, state, reason):
        raise AssertionError(reason)


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (Outcome.BLOCKED_UPSTREAM, "IMAGE_LOCK_ROTATION_REQUIRED"),
        (Outcome.BLOCKED_ENVIRONMENT, "PREFLIGHT_BLOCKED"),
        (Outcome.UNSAFE, "UNSAFE_VOLUME_PLAN"),
    ],
)
def test_pre_mutation_start_failure_never_attempts_stop(
    outcome: Outcome,
    reason: str,
) -> None:
    start = smoke_module.SmokeEnvironmentStart(
        result=TerminalResult(outcome=outcome, reason_code=reason),
        disposition=smoke_module.EnvironmentStartDisposition.PRE_MUTATION_BLOCKED,
    )
    operations = _StartOperations(start)

    state = smoke_module.supervise_smoke_attempt(
        run_id=RUN_ID,
        operations=operations,
    )

    assert operations.events == ["up", "finalize"]
    assert not state.stop_required
    assert not state.stop_attempted
    assert not state.stop_succeeded
    assert state.failure_reason_codes == [reason]


def test_mutation_possible_start_failure_requires_safe_stop() -> None:
    start = smoke_module.SmokeEnvironmentStart(
        result=TerminalResult(
            outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
            reason_code="COMPOSE_UP_MUTATION_UNCERTAIN",
        ),
        disposition=(
            smoke_module.EnvironmentStartDisposition.MUTATION_MAY_HAVE_OCCURRED
        ),
    )
    operations = _StartOperations(start)

    state = smoke_module.supervise_smoke_attempt(
        run_id=RUN_ID,
        operations=operations,
    )

    assert operations.events == [
        "up",
        "authority:stop",
        "down",
        "cleanup:volumes",
        "finalize",
    ]
    assert state.stop_required
    assert state.stop_attempted
    assert state.stop_succeeded
    assert state.failure_reason_codes == ["COMPOSE_UP_MUTATION_UNCERTAIN"]


def test_mutation_failure_keeps_original_reason_when_stop_authority_fails() -> None:
    start = smoke_module.SmokeEnvironmentStart(
        result=TerminalResult(
            outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
            reason_code="POST_UP_EVIDENCE_PERSISTENCE_FAILED",
        ),
        disposition=(
            smoke_module.EnvironmentStartDisposition.MUTATION_MAY_HAVE_OCCURRED
        ),
    )

    class StopAuthorityFailure(_StartOperations):
        def fresh_stop_authority(self):
            self.events.append("authority:stop")
            raise RuntimeError("STOP_AUTHORITY_FAILED")

    operations = StopAuthorityFailure(start)

    state = smoke_module.supervise_smoke_attempt(
        run_id=RUN_ID,
        operations=operations,
    )

    assert state.failure_reason_codes == [
        "POST_UP_EVIDENCE_PERSISTENCE_FAILED",
        "STOP_AUTHORITY_FAILED",
    ]
    assert state.stop_required
    assert not state.stop_attempted


def test_blocked_smoke_report_marks_stop_not_required(tmp_path: Path) -> None:
    for zone in ("observer-visible", "evaluator-only"):
        run_root = tmp_path / zone / RUN_ID
        run_root.mkdir(parents=True)
        (run_root / "evidence.json").write_text("{}", encoding="utf-8")
    state = smoke_module.SmokeSupervisorState(
        run_id=RUN_ID,
        start_disposition=(
            smoke_module.EnvironmentStartDisposition.PRE_MUTATION_BLOCKED
        ),
        stop_required=False,
        failure_reason_codes=["IMAGE_LOCK_ROTATION_REQUIRED"],
        failure_statuses=[DiagnosticStatus.BLOCKED],
    )

    report = smoke_module.finalize_supervised_smoke(
        state=state,
        artifacts_root=tmp_path,
    )

    assert report.diagnostic_status is DiagnosticStatus.BLOCKED
    assert not report.attempts[0].safe_stop_required
    assert not report.attempts[0].safe_stop_attempted
    assert "SAFE_STOP_NOT_CONFIRMED" not in report.failure_reason_codes


def test_direct_stop_uses_minimal_fresh_stop_authority_without_full_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ownership = SimpleNamespace(
        run_id=RUN_ID,
        manifest_sha256="4" * 64,
        is_authentic=lambda: True,
    )
    authority = SimpleNamespace(
        docker_endpoint=DOCKER_ENDPOINT,
        evidence_persistence_error="OBSERVER_PERSISTENCE_FAILED",
        is_authentic=lambda candidate: candidate is ownership,
    )
    calls: list[str] = []
    monkeypatch.setattr(
        cli_module,
        "_resolve_fresh_preflight",
        lambda *_args, **_kwargs: pytest.fail("full preflight is forbidden"),
    )
    monkeypatch.setattr(
        cli_module,
        "_verify_upstream",
        lambda *_args, **_kwargs: pytest.fail("upstream verification is forbidden"),
    )
    monkeypatch.setattr(
        cli_module,
        "load_authenticated_ownership_context",
        lambda *_args, **_kwargs: ownership,
    )
    monkeypatch.setattr(
        cli_module,
        "collect_docker_snapshot",
        lambda *_args, **_kwargs: pytest.fail(
            "full Docker snapshot is forbidden"
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "collect_direct_stop_docker_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(
            daemon_available=True,
            docker_endpoint=DOCKER_ENDPOINT,
            daemon_id=DAEMON_ID,
            context_name="desktop-linux",
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "collect_fresh_stop_authority",
        lambda **_kwargs: authority,
    )
    monkeypatch.setattr(
        cli_module,
        "down_environment",
        lambda *_args, **_kwargs: (
            calls.append("down")
            or TerminalResult(
                outcome=Outcome.SUCCESS,
                reason_code="OWNED_ENVIRONMENT_STOPPED",
            )
        ),
    )
    context = cli_module.HandlerContext(
        runner=SimpleNamespace(),
        project_root=tmp_path,
        artifacts_root=tmp_path / "artifacts",
    )

    result = cli_module._handle_stop(SimpleNamespace(run_id=RUN_ID), context)

    assert result.outcome is Outcome.SUCCESS
    assert result.evidence_persistence_error == "OBSERVER_PERSISTENCE_FAILED"
    assert calls == ["down"]


@pytest.mark.parametrize(
    ("snapshot", "reason"),
    [
        (
            SimpleNamespace(
                daemon_available=True,
                docker_endpoint="tcp://127.0.0.1:2375",
                daemon_id=DAEMON_ID,
                context_name="desktop-linux",
            ),
            "FRESH_STOP_DOCKER_ENDPOINT_UNSAFE",
        ),
        (
            SimpleNamespace(
                daemon_available=True,
                docker_endpoint=DOCKER_ENDPOINT,
                daemon_id="",
                context_name="desktop-linux",
            ),
            "FRESH_STOP_DAEMON_ID_UNAVAILABLE",
        ),
    ],
)
def test_direct_stop_fails_closed_on_unsafe_minimal_docker_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot: object,
    reason: str,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "load_authenticated_ownership_context",
        lambda *_args, **_kwargs: SimpleNamespace(is_authentic=lambda: True),
    )
    monkeypatch.setattr(
        cli_module,
        "collect_direct_stop_docker_snapshot",
        lambda *_args, **_kwargs: snapshot,
    )
    monkeypatch.setattr(
        cli_module,
        "collect_fresh_stop_authority",
        lambda **_kwargs: pytest.fail("unsafe snapshot must block authority"),
    )
    monkeypatch.setattr(
        cli_module,
        "down_environment",
        lambda *_args, **_kwargs: pytest.fail("unsafe snapshot must block down"),
    )
    context = cli_module.HandlerContext(
        runner=SimpleNamespace(),
        project_root=tmp_path,
        artifacts_root=tmp_path / "artifacts",
    )

    result = cli_module._handle_stop(SimpleNamespace(run_id=RUN_ID), context)

    assert result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert result.reason_code == reason


def test_direct_stop_resource_drift_fails_closed_without_down(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "load_authenticated_ownership_context",
        lambda *_args, **_kwargs: SimpleNamespace(is_authentic=lambda: True),
    )
    monkeypatch.setattr(
        cli_module,
        "collect_direct_stop_docker_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(
            daemon_available=True,
            docker_endpoint=DOCKER_ENDPOINT,
            daemon_id=DAEMON_ID,
            context_name="desktop-linux",
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "collect_fresh_stop_authority",
        lambda **_kwargs: (_ for _ in ()).throw(
            OwnershipAuthorityError("resource drift")
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "down_environment",
        lambda *_args, **_kwargs: pytest.fail("resource drift must block down"),
    )
    context = cli_module.HandlerContext(
        runner=SimpleNamespace(),
        project_root=tmp_path,
        artifacts_root=tmp_path / "artifacts",
    )

    result = cli_module._handle_stop(SimpleNamespace(run_id=RUN_ID), context)

    assert result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert result.reason_code == "FRESH_STOP_AUTHORITY_UNAVAILABLE"


def test_direct_stop_snapshot_collects_only_context_and_daemon_identity() -> None:
    context_arguments = (
        "docker",
        "--context",
        "desktop-linux",
        "context",
        "inspect",
        "desktop-linux",
        "--format",
        "{{json .}}",
    )
    daemon_arguments = (
        "docker",
        "--host",
        DOCKER_ENDPOINT,
        "info",
        "--format",
        "{{json .ID}}",
    )
    responses = {
        context_arguments: CommandResult(
            arguments=context_arguments,
            exit_code=0,
            stdout=json.dumps(
                {
                    "Name": "desktop-linux",
                    "Endpoints": {
                        "docker": {"Host": DOCKER_ENDPOINT},
                    },
                }
            ),
            stderr="",
        ),
        daemon_arguments: CommandResult(
            arguments=daemon_arguments,
            exit_code=0,
            stdout=json.dumps(DAEMON_ID),
            stderr="",
        ),
    }
    calls: list[tuple[str, ...]] = []

    class Runner:
        def run(self, arguments, *, timeout_seconds):
            del timeout_seconds
            calls.append(arguments)
            return responses[arguments]

    snapshot = live_preflight_module.collect_direct_stop_docker_snapshot(
        Runner()
    )

    assert snapshot.daemon_available
    assert snapshot.context_name == "desktop-linux"
    assert snapshot.docker_endpoint == DOCKER_ENDPOINT
    assert snapshot.daemon_id == DAEMON_ID
    assert calls == [context_arguments, daemon_arguments]
