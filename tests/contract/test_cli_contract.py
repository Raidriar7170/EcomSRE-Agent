import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from ecomsre.environment.lifecycle import (
    LifecycleArtifactPaths,
    LifecycleExecution,
)
from ecomsre.environment.manifests import load_image_lock
from ecomsre.phase0.models import Outcome, TerminalResult

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_EXIT_CODES = {
    "SUCCESS": 0,
    "BLOCKED_ENVIRONMENT": 20,
    "BLOCKED_UPSTREAM": 21,
    "FAILED_ACCEPTANCE": 30,
    "UNSAFE": 40,
    "MANUAL_INTERVENTION_REQUIRED": 41,
    "INVALID_INVOCATION": 64,
}

CANONICAL_COMMANDS = {
    "bootstrap",
    "preflight",
    "up",
    "health",
    "inject",
    "reset",
    "status",
    "accept",
    "stop",
}
DIAGNOSTIC_COMMANDS = {"smoke"}
MAINTENANCE_COMMANDS = {"cleanup-owned-volumes"}

RUN_ID_REQUIRED_COMMANDS = {
    "up",
    "health",
    "inject",
    "reset",
    "status",
    "stop",
    "cleanup-owned-volumes",
}

IMPLEMENTED_COMMANDS = {
    "bootstrap",
    "preflight",
    "up",
    "health",
    "inject",
    "reset",
    "status",
    "smoke",
    "stop",
    "cleanup-owned-volumes",
}


def _stub_minimal_direct_stop(monkeypatch, cli) -> None:
    ownership = SimpleNamespace(is_authentic=lambda: True)
    monkeypatch.setattr(
        cli,
        "load_authenticated_ownership_context",
        lambda *_args: ownership,
    )
    monkeypatch.setattr(
        cli,
        "collect_direct_stop_docker_snapshot",
        lambda *_args: SimpleNamespace(
            daemon_available=True,
            docker_endpoint="unix:///var/run/docker.sock",
            daemon_id="fixture-daemon",
            context_name="desktop-linux",
        ),
    )
    monkeypatch.setattr(
        cli,
        "collect_fresh_stop_authority",
        lambda **_kwargs: SimpleNamespace(
            docker_endpoint="unix:///var/run/docker.sock",
            evidence_persistence_error=None,
            is_authentic=lambda candidate: candidate is ownership,
        ),
    )


def test_exit_code_mapping_matches_acceptance_contract() -> None:
    from ecomsre.cli import EXIT_CODES

    assert EXIT_CODES == EXPECTED_EXIT_CODES


def test_cli_parser_exposes_only_explicit_phase0_commands() -> None:
    from ecomsre.cli import build_parser

    parser = build_parser()
    phase0_action = next(action for action in parser._actions if action.dest == "area")
    phase0_parser = phase0_action.choices["phase0"]
    command_action = next(
        action for action in phase0_parser._actions if action.dest == "command"
    )

    assert set(command_action.choices) == (
        CANONICAL_COMMANDS | DIAGNOSTIC_COMMANDS | MAINTENANCE_COMMANDS
    )


def test_existing_run_commands_require_a_valid_opaque_run_id() -> None:
    from ecomsre.cli import build_parser

    parser = build_parser()
    area_action = next(action for action in parser._actions if action.dest == "area")
    phase0_parser = area_action.choices["phase0"]
    command_action = next(
        action for action in phase0_parser._actions if action.dest == "command"
    )

    for name in RUN_ID_REQUIRED_COMMANDS:
        run_id_action = next(
            action
            for action in command_action.choices[name]._actions
            if action.dest == "run_id"
        )
        assert run_id_action.required is False


@pytest.mark.parametrize(
    "command",
    sorted(CANONICAL_COMMANDS - RUN_ID_REQUIRED_COMMANDS - IMPLEMENTED_COMMANDS),
)
def test_cli_command_is_non_interactive_and_returns_stable_placeholder(
    command: str,
) -> None:
    env = os.environ.copy()
    src = str(ROOT / "src")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{env.get('PYTHONPATH', '')}"

    completed = subprocess.run(
        [sys.executable, "-m", "ecomsre.cli", "phase0", command],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == EXPECTED_EXIT_CODES["INVALID_INVOCATION"]
    assert completed.stdout == ""
    assert "not implemented" in completed.stderr.lower()


@pytest.mark.parametrize("command", sorted(RUN_ID_REQUIRED_COMMANDS))
def test_existing_run_command_without_run_id_returns_invalid_invocation(
    command: str,
) -> None:
    completed = _run_cli("phase0", command)

    assert completed.returncode == EXPECTED_EXIT_CODES["INVALID_INVOCATION"]
    assert "required" in completed.stderr.lower()


@pytest.mark.parametrize("command", sorted(RUN_ID_REQUIRED_COMMANDS))
def test_existing_run_command_rejects_malformed_run_id(command: str) -> None:
    completed = _run_cli("phase0", command, "--run-id", "../semantic-name")

    assert completed.returncode == EXPECTED_EXIT_CODES["INVALID_INVOCATION"]
    assert "run_id" in completed.stderr.lower()


def test_formal_up_without_bootstrap_lock_returns_blocked_upstream(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import ecomsre.cli as cli

    lock_path = tmp_path / "image-lock.json"
    lock_path.write_text(
        __import__("json").dumps(
            {
                "schema_version": "phase0.image-lock.v1",
                "status": "UNINITIALIZED",
                "upstream_tag": "3.0.0",
                "upstream_commit": (
                    "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
                ),
                "compose_config_sha256": None,
                "created_at": None,
                "allowed_source_references": [],
                "images": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "_load_lock", lambda _root: load_image_lock(lock_path))
    context = cli.HandlerContext(
        runner=SimpleNamespace(
            run=lambda *_args, **_kwargs: pytest.fail(
                "uninitialized lock must block before commands"
            )
        ),
        project_root=tmp_path,
        artifacts_root=tmp_path / "artifacts",
    )

    result = cli._handle_up(SimpleNamespace(run_id="a" * 32), context)

    assert result.outcome is Outcome.BLOCKED_UPSTREAM
    assert result.reason_code == "IMAGE_LOCK_UNINITIALIZED"


def test_stop_reseals_existing_terminal_smoke_bundle_after_successful_down(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import ecomsre.cli as cli
    from ecomsre.phase0.smoke import validate_current_recovery_seal

    run_id = "a" * 32
    for zone in ("observer-visible", "evaluator-only"):
        run_root = tmp_path / zone / run_id
        run_root.mkdir(parents=True)
        (run_root / "evidence.json").write_text("{}", encoding="utf-8")
    report_root = tmp_path / "reports" / run_id
    report_root.mkdir(parents=True)
    (report_root / "smoke-report.json").write_text(
        '{"diagnostic_status":"UNSAFE"}',
        encoding="utf-8",
    )
    (report_root / "checksums.sha256").write_text(
        "a" * 64 + f"  reports/{run_id}/smoke-report.json\n",
        encoding="utf-8",
    )
    _stub_minimal_direct_stop(monkeypatch, cli)
    monkeypatch.setattr(
        cli,
        "down_environment",
        lambda *_args, **_kwargs: TerminalResult(
            outcome=Outcome.SUCCESS,
            reason_code="ENVIRONMENT_STOPPED",
        ),
    )
    context = cli.HandlerContext(
        runner=SimpleNamespace(),
        project_root=tmp_path,
        artifacts_root=tmp_path,
    )

    result = cli._handle_stop(SimpleNamespace(run_id=run_id), context)

    assert result.outcome is Outcome.SUCCESS
    assert (report_root / "recovery" / "001.json").is_file()
    assert (report_root / "seals" / "001.sha256").is_file()
    assert validate_current_recovery_seal(tmp_path, run_id=run_id)


def test_stop_returns_manual_when_required_recovery_reseal_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import ecomsre.cli as cli

    run_id = "a" * 32
    report_root = tmp_path / "reports" / run_id
    report_root.mkdir(parents=True)
    (report_root / "smoke-report.json").write_text("{}", encoding="utf-8")
    (report_root / "checksums.sha256").write_text("", encoding="utf-8")
    _stub_minimal_direct_stop(monkeypatch, cli)
    monkeypatch.setattr(
        cli,
        "down_environment",
        lambda *_args, **_kwargs: TerminalResult(
            outcome=Outcome.SUCCESS,
            reason_code="ENVIRONMENT_STOPPED",
        ),
    )
    monkeypatch.setattr(
        cli,
        "reseal_recovery_evidence",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("fixture seal failure")),
        raising=False,
    )
    context = cli.HandlerContext(
        runner=SimpleNamespace(),
        project_root=tmp_path,
        artifacts_root=tmp_path,
    )

    result = cli._handle_stop(SimpleNamespace(run_id=run_id), context)

    assert result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert result.reason_code == "RECOVERY_EVIDENCE_PERSISTENCE_FAILED"


def test_stop_without_existing_smoke_report_does_not_reseal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import ecomsre.cli as cli

    run_id = "a" * 32
    _stub_minimal_direct_stop(monkeypatch, cli)
    monkeypatch.setattr(
        cli,
        "down_environment",
        lambda *_args, **_kwargs: TerminalResult(
            outcome=Outcome.SUCCESS,
            reason_code="ENVIRONMENT_STOPPED",
        ),
    )
    monkeypatch.setattr(
        cli,
        "reseal_recovery_evidence",
        lambda **_kwargs: pytest.fail("stop without report must not reseal"),
    )
    context = cli.HandlerContext(
        runner=SimpleNamespace(),
        project_root=tmp_path,
        artifacts_root=tmp_path,
    )

    result = cli._handle_stop(SimpleNamespace(run_id=run_id), context)

    assert result.outcome is Outcome.SUCCESS
    assert not (tmp_path / "reports" / run_id).exists()


def test_task6_registry_exposes_lifecycle_and_control_handlers() -> None:
    from ecomsre.cli import build_handler_registry

    registry = build_handler_registry()

    assert set(registry) == IMPLEMENTED_COMMANDS


def test_smoke_generates_one_opaque_run_id_when_make_exports_empty_value(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    import ecomsre.cli as cli

    observed: list[str] = []

    def smoke_handler(args, _context):
        observed.append(args.run_id)
        return TerminalResult(
            outcome=Outcome.BLOCKED_ENVIRONMENT,
            reason_code="TEST_STOP",
        )

    monkeypatch.setenv("ECOMSRE_RUN_ID", "")
    monkeypatch.setattr(cli, "build_handler_registry", lambda: {"smoke": smoke_handler})

    exit_code = cli.main(
        ["phase0", "smoke"],
        runner=object(),
        project_root=tmp_path,
        artifacts_root=tmp_path / "artifacts",
    )

    assert exit_code == 20
    assert len(observed) == 1
    assert len(observed[0]) == 32
    int(observed[0], 16)
    capsys.readouterr()


def test_task5_cli_preserves_unknown_ownership_truth_marker() -> None:
    completed = _run_cli(
        "phase0",
        "status",
        "--run-id",
        "a" * 32,
    )

    assert completed.returncode == EXPECTED_EXIT_CODES["UNSAFE"]
    payload = __import__("json").loads(completed.stdout)
    assert payload["outcome"] == "UNSAFE"
    assert payload["reason_code"] == "RESOURCE_OWNERSHIP_UNKNOWN"
    assert completed.stderr == ""


@pytest.mark.parametrize("command", ["inject", "reset"])
def test_control_cli_requires_current_preflight_before_any_runtime_write(
    tmp_path: Path,
    monkeypatch,
    command: str,
) -> None:
    from ecomsre import cli

    monkeypatch.setattr(cli, "_verify_upstream", lambda _context: None)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("control runtime touched before current preflight")

    monkeypatch.setattr(
        cli,
        "load_authenticated_ownership_context",
        forbidden,
    )
    monkeypatch.setattr(
        cli, "FlagdGroundTruthRuntime", SimpleNamespace(open_existing=forbidden)
    )
    context = cli.HandlerContext(
        runner=SimpleNamespace(),
        project_root=ROOT,
        artifacts_root=tmp_path,
        preflight_evidence=None,
        readiness_evidence=None,
    )

    handler = cli._handle_inject if command == "inject" else cli._handle_reset
    result = handler(SimpleNamespace(run_id="a" * 32), context)

    assert result.outcome is Outcome.BLOCKED_ENVIRONMENT
    assert result.reason_code == "PREFLIGHT_BLOCKED"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("command", ["inject", "reset", "status"])
def test_control_cli_requires_current_complete_readiness_before_runtime_open(
    tmp_path: Path,
    monkeypatch,
    command: str,
) -> None:
    from ecomsre import cli

    monkeypatch.setattr(cli, "_verify_upstream", lambda _context: None)
    monkeypatch.setattr(
        cli,
        "_current_docker_endpoint",
        lambda _context, _run_id: "unix:///var/run/docker.sock",
    )
    monkeypatch.setattr(
        cli,
        "load_authenticated_ownership_context",
        lambda *_args: SimpleNamespace(run_id="a" * 32),
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("runtime opened before readiness gate")

    monkeypatch.setattr(
        cli, "FlagdGroundTruthRuntime", SimpleNamespace(open_existing=forbidden)
    )
    context = cli.HandlerContext(
        runner=SimpleNamespace(),
        project_root=ROOT,
        artifacts_root=tmp_path,
        preflight_evidence=SimpleNamespace(),
        readiness_evidence=None,
    )

    handler = {
        "inject": cli._handle_inject,
        "reset": cli._handle_reset,
        "status": cli._handle_status,
    }[command]
    result = handler(SimpleNamespace(run_id="a" * 32), context)

    assert result.outcome is Outcome.BLOCKED_ENVIRONMENT
    assert result.reason_code == "READINESS_INCOMPLETE"
    assert list(tmp_path.iterdir()) == []


def test_ofrep_endpoint_is_derived_only_from_owned_flagd_port_binding() -> None:
    from ecomsre.cli import _owned_ofrep_endpoint

    resource = SimpleNamespace(
        kind="port",
        labels={"com.docker.compose.service": "flagd"},
        identity_evidence=(
            "service:flagd",
            "published_port:32768",
            "target_port:8016",
            "protocol:tcp",
        ),
    )
    context = SimpleNamespace(
        run_id="a" * 32,
        manifest=SimpleNamespace(resources=(resource,)),
    )

    assert _owned_ofrep_endpoint(context) == "http://127.0.0.1:32768"


@pytest.mark.parametrize(
    "identity_evidence",
    [
        ("service:flagd", "published_port:32768", "target_port:8013", "protocol:tcp"),
        ("service:flagd", "published_port:0", "target_port:8016", "protocol:tcp"),
        ("service:other", "published_port:32768", "target_port:8016", "protocol:tcp"),
    ],
)
def test_ofrep_endpoint_rejects_missing_or_non_ofrep_owned_binding(
    identity_evidence: tuple[str, ...],
) -> None:
    from ecomsre.cli import _owned_ofrep_endpoint

    resource = SimpleNamespace(
        kind="port",
        labels={"com.docker.compose.service": "flagd"},
        identity_evidence=identity_evidence,
    )
    context = SimpleNamespace(
        run_id="a" * 32,
        manifest=SimpleNamespace(resources=(resource,)),
    )

    assert _owned_ofrep_endpoint(context) is None


def test_control_runtime_unavailable_has_typed_blocked_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ecomsre import cli

    monkeypatch.setattr(
        cli.FlagdGroundTruthRuntime,
        "open_existing",
        lambda **_kwargs: (_ for _ in ()).throw(
            cli.FlagdRuntimeUnavailable("fixture missing")
        ),
    )
    context = cli.HandlerContext(
        runner=SimpleNamespace(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    runtime, failure = cli._open_control_runtime(
        context,
        run_id="a" * 32,
        endpoint="http://127.0.0.1:32768",
    )

    assert runtime is None
    assert failure is not None
    assert failure.outcome is Outcome.BLOCKED_ENVIRONMENT
    assert failure.reason_code == "CONTROL_RUNTIME_UNAVAILABLE"
    assert list(tmp_path.iterdir()) == []


def test_status_raw_evidence_failure_is_typed_pre_mutation_blocked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ecomsre import cli
    from ecomsre.scenarios.ad_service_failure import EvidencePersistenceError

    resource = SimpleNamespace(
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
        run_id="a" * 32,
        manifest=SimpleNamespace(resources=(resource,)),
    )
    readiness = cli.ReadinessEvidence(
        schema_version="phase0.readiness-evidence.v1",
        run_id="a" * 32,
        ownership_resources_complete=True,
        load_generator_ready=True,
        collector_ready=True,
        prometheus_fresh=True,
        jaeger_fresh=True,
        opensearch_fresh=True,
    )
    runtime = SimpleNamespace(
        observe_state=lambda **_kwargs: (_ for _ in ()).throw(
            EvidencePersistenceError("fixture raw append failure")
        ),
        close=lambda: None,
    )
    monkeypatch.setattr(cli, "_verify_upstream", lambda _context: None)
    monkeypatch.setattr(
        cli,
        "load_authenticated_ownership_context",
        lambda *_args: ownership,
    )
    monkeypatch.setattr(
        cli,
        "_current_docker_endpoint",
        lambda *_args: "unix:///var/run/docker.sock",
    )
    monkeypatch.setattr(
        cli,
        "status_environment",
        lambda *_args, **_kwargs: TerminalResult(
            outcome=Outcome.SUCCESS,
            reason_code="ENVIRONMENT_STATUS_CAPTURED",
        ),
    )
    monkeypatch.setattr(
        cli,
        "health_environment",
        lambda *_args, **_kwargs: TerminalResult(
            outcome=Outcome.SUCCESS,
            reason_code="ENVIRONMENT_HEALTHY",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_open_control_runtime",
        lambda *_args, **_kwargs: (runtime, None),
    )
    context = cli.HandlerContext(
        runner=SimpleNamespace(),
        project_root=ROOT,
        artifacts_root=tmp_path,
        preflight_evidence=SimpleNamespace(),
        readiness_evidence=readiness,
    )

    result = cli._handle_status(
        SimpleNamespace(run_id="a" * 32),
        context,
    )

    assert result.outcome is Outcome.BLOCKED_ENVIRONMENT
    assert result.reason_code == "EVIDENCE_PERSISTENCE_FAILED"
    assert result.exit_code == 20


def test_observer_capability_init_failure_is_typed_before_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ecomsre import cli

    resource = SimpleNamespace(
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
        run_id="a" * 32,
        manifest=SimpleNamespace(resources=(resource,)),
    )
    readiness = cli.ReadinessEvidence(
        schema_version="phase0.readiness-evidence.v1",
        run_id="a" * 32,
        ownership_resources_complete=True,
        load_generator_ready=True,
        collector_ready=True,
        prometheus_fresh=True,
        jaeger_fresh=True,
        opensearch_fresh=True,
    )
    runtime = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(cli, "_verify_upstream", lambda _context: None)
    monkeypatch.setattr(
        cli,
        "load_authenticated_ownership_context",
        lambda *_args: ownership,
    )
    monkeypatch.setattr(
        cli,
        "_current_docker_endpoint",
        lambda *_args: "unix:///var/run/docker.sock",
    )
    monkeypatch.setattr(
        cli,
        "health_environment",
        lambda *_args, **_kwargs: TerminalResult(
            outcome=Outcome.SUCCESS,
            reason_code="ENVIRONMENT_HEALTHY",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_open_control_runtime",
        lambda *_args, **_kwargs: (runtime, None),
    )
    monkeypatch.setattr(
        cli,
        "ObserverEvidenceStore",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("fixture observer capability failure")
        ),
    )
    context = cli.HandlerContext(
        runner=SimpleNamespace(),
        project_root=ROOT,
        artifacts_root=tmp_path,
        preflight_evidence=SimpleNamespace(),
        readiness_evidence=readiness,
    )

    result = cli._handle_inject(
        SimpleNamespace(run_id="a" * 32),
        context,
    )

    assert result.outcome is Outcome.BLOCKED_ENVIRONMENT
    assert result.reason_code == "EVIDENCE_CAPABILITY_UNAVAILABLE"
    assert result.exit_code == 20


def test_cli_preserves_manual_41_for_unknown_pending_recovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ecomsre import cli
    from ecomsre.scenarios.ad_service_failure import (
        PendingRecoveryEvidenceError,
        ScenarioState,
        TransitionPreparation,
    )

    resource = SimpleNamespace(
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
        run_id="a" * 32,
        manifest=SimpleNamespace(resources=(resource,)),
    )
    readiness = cli.ReadinessEvidence(
        schema_version="phase0.readiness-evidence.v1",
        run_id="a" * 32,
        ownership_resources_complete=True,
        load_generator_ready=True,
        collector_ready=True,
        prometheus_fresh=True,
        jaeger_fresh=True,
        opensearch_fresh=True,
    )

    class UnknownPendingRuntime:
        @contextmanager
        def transition_guard(self, *, timeout_seconds: float):
            assert timeout_seconds > 0
            yield True

        def reconcile_pending_intent(self, *, timeout_seconds: float):
            assert timeout_seconds > 0
            raise PendingRecoveryEvidenceError(
                "fixture unresolved pending recovery",
                observed_state=ScenarioState.UNKNOWN,
            )

        def prepare_transition(self, _execution):
            return TransitionPreparation(
                schema_version="phase0.transition-preparation.v1",
                preparation_id="d" * 32,
                transition_sequence=1,
            )

        def finalize_transition(self, _execution, **_kwargs) -> None:
            pass

        def record_emergency_diagnostic(self, _execution, **_kwargs) -> None:
            pass

        def close(self) -> None:
            pass

    runtime = UnknownPendingRuntime()
    monkeypatch.setattr(cli, "_verify_upstream", lambda _context: None)
    monkeypatch.setattr(
        cli,
        "load_authenticated_ownership_context",
        lambda *_args: ownership,
    )
    monkeypatch.setattr(
        cli,
        "_current_docker_endpoint",
        lambda *_args: "unix:///var/run/docker.sock",
    )
    monkeypatch.setattr(
        cli,
        "health_environment",
        lambda *_args, **_kwargs: TerminalResult(
            outcome=Outcome.SUCCESS,
            reason_code="ENVIRONMENT_HEALTHY",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_open_control_runtime",
        lambda *_args, **_kwargs: (runtime, None),
    )
    context = cli.HandlerContext(
        runner=SimpleNamespace(),
        project_root=ROOT,
        artifacts_root=tmp_path,
        preflight_evidence=SimpleNamespace(),
        readiness_evidence=readiness,
    )

    result = cli._handle_inject(
        SimpleNamespace(run_id="a" * 32),
        context,
    )

    assert result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert result.reason_code == "EVIDENCE_PERSISTENCE_FAILED"
    assert result.exit_code == 41


def test_evaluator_capability_io_failure_has_dedicated_typed_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ecomsre import cli

    monkeypatch.setattr(
        cli.FlagdGroundTruthRuntime,
        "open_existing",
        lambda **_kwargs: (_ for _ in ()).throw(
            OSError("fixture evaluator capability failure")
        ),
    )
    context = cli.HandlerContext(
        runner=SimpleNamespace(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    runtime, failure = cli._open_control_runtime(
        context,
        run_id="a" * 32,
        endpoint="http://127.0.0.1:32768",
    )

    assert runtime is None
    assert failure is not None
    assert failure.outcome is Outcome.BLOCKED_ENVIRONMENT
    assert failure.reason_code == "EVIDENCE_CAPABILITY_UNAVAILABLE"
    assert failure.exit_code == 20


def test_cli_up_collects_fresh_preflight_when_no_evidence_is_injected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ecomsre import cli
    from ecomsre.environment.manifests import ImageLockStatus

    monkeypatch.setattr(
        cli,
        "_load_lock",
        lambda _root: SimpleNamespace(status=ImageLockStatus.LOCKED),
    )
    monkeypatch.setattr(cli, "_verify_upstream", lambda _context: None)

    evidence = SimpleNamespace(
        run_id="a" * 32,
        is_current=lambda: True,
        inputs=SimpleNamespace(ownership_context=None),
        result=SimpleNamespace(outcome=Outcome.SUCCESS),
    )
    collected = []
    monkeypatch.setattr(
        cli,
        "collect_fresh_preflight",
        lambda **kwargs: collected.append(kwargs) or evidence,
    )
    monkeypatch.setattr(
        cli,
        "prepare_flagd_runtime",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        cli,
        "load_authenticated_ownership_context",
        lambda *_args: (_ for _ in ()).throw(cli.OwnershipAuthorityError()),
    )
    monkeypatch.setattr(
        cli,
        "up_environment",
        lambda *_args, **kwargs: LifecycleExecution(
            result=TerminalResult(
                outcome=Outcome.BLOCKED_ENVIRONMENT,
                reason_code=(
                    "FRESH_EVIDENCE_USED"
                    if kwargs["preflight_evidence"] is evidence
                    else "STALE_EVIDENCE_USED"
                ),
            )
        ),
    )
    context = cli.HandlerContext(
        runner=SimpleNamespace(),
        project_root=ROOT,
        artifacts_root=tmp_path,
        preflight_evidence=None,
    )

    result = cli._handle_up(
        SimpleNamespace(run_id="a" * 32),
        context,
    )

    assert result.outcome is Outcome.BLOCKED_ENVIRONMENT
    assert result.reason_code == "FRESH_EVIDENCE_USED"
    assert len(collected) == 1
    assert collected[0]["run_id"] == "a" * 32


def test_cli_up_prepares_verified_run_config_before_compose_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ecomsre import cli
    from ecomsre.environment.manifests import ImageLockStatus

    prepared: list[tuple[Path, Path, str]] = []
    evidence = SimpleNamespace(
        run_id="a" * 32,
        is_current=lambda: True,
        inputs=SimpleNamespace(ownership_context=None),
    )
    monkeypatch.setattr(
        cli,
        "_load_lock",
        lambda _root: SimpleNamespace(status=ImageLockStatus.LOCKED),
    )
    monkeypatch.setattr(cli, "_verify_upstream", lambda _context: None)
    monkeypatch.setattr(
        cli,
        "prepare_flagd_runtime",
        lambda *, project_root, artifacts_root, run_id: prepared.append(
            (project_root, artifacts_root, run_id)
        ),
    )
    monkeypatch.setattr(
        cli,
        "load_authenticated_ownership_context",
        lambda *_args: (_ for _ in ()).throw(cli.OwnershipAuthorityError()),
    )

    def fake_up(*_args, **_kwargs):
        assert prepared == [(ROOT, tmp_path, "a" * 32)]
        return LifecycleExecution(
            result=TerminalResult(
                outcome=Outcome.BLOCKED_ENVIRONMENT,
                reason_code="FIXTURE_STOP",
            )
        )

    monkeypatch.setattr(cli, "up_environment", fake_up)
    context = cli.HandlerContext(
        runner=SimpleNamespace(),
        project_root=ROOT,
        artifacts_root=tmp_path,
        preflight_evidence=evidence,
    )

    result = cli._handle_up(SimpleNamespace(run_id="a" * 32), context)

    assert result.outcome is Outcome.BLOCKED_ENVIRONMENT
    assert result.reason_code == "FIXTURE_STOP"
    assert prepared == [(ROOT, tmp_path, "a" * 32)]


def test_subprocess_runner_never_forwards_ambient_docker_proxy_or_compose_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import ecomsre.environment.command_runner as command_runner
    from ecomsre.cli import SubprocessRunner

    for name in (
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "COMPOSE_FILE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "AWS_SECRET_ACCESS_KEY",
    ):
        monkeypatch.setenv(name, f"ambient-{name}")
    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        def communicate(self, timeout):
            assert timeout == 10
            return "", ""

    def fake_popen(arguments, **kwargs):
        captured["arguments"] = arguments
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(command_runner, "_Popen", fake_popen)
    runner = SubprocessRunner(cwd=tmp_path, run_id="a" * 32)

    runner.run(
        ("docker", "--context", "desktop-linux", "info"),
        timeout_seconds=10,
        environment={"ECOMSRE_RUN_ID": "a" * 32},
    )

    process_environment = captured["env"]
    assert isinstance(process_environment, dict)
    assert set(process_environment) == {
        "ECOMSRE_RUN_ID",
        "LANG",
        "LC_ALL",
        "PATH",
        "TMPDIR",
    }
    assert process_environment["ECOMSRE_RUN_ID"] == "a" * 32
    assert process_environment["PATH"] == (
        "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    )
    assert all(
        not name.startswith(("DOCKER_", "COMPOSE_"))
        and "PROXY" not in name
        and "CREDENTIAL" not in name
        and "SECRET" not in name
        for name in process_environment
    )


def test_cli_lifecycle_summary_preserves_typed_evidence_paths(
    tmp_path: Path,
) -> None:
    from ecomsre.cli import summarize_lifecycle_execution

    run_root = tmp_path / "observer-visible" / ("a" * 32)
    run_root.mkdir(parents=True)
    intent = run_root / "ownership-intent.json"
    diagnostic = run_root / "lifecycle" / "manual-diagnostic.json"
    diagnostic.parent.mkdir()
    intent.write_text("{}", encoding="utf-8")
    diagnostic.write_text("{}", encoding="utf-8")
    execution = LifecycleExecution(
        result=TerminalResult(
            outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
            reason_code="POST_UP_OWNERSHIP_UNPROVEN",
        ),
        artifact_paths=LifecycleArtifactPaths(
            artifacts_root=tmp_path,
            ownership_intent=intent,
            manual_diagnostic=diagnostic,
        ),
    )

    summary = summarize_lifecycle_execution(execution)

    assert summary.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert summary.exit_code == 41
    assert summary.reason_code == "POST_UP_OWNERSHIP_UNPROVEN"
    assert summary.evidence_paths == (
        str(intent),
        str(diagnostic),
    )


def test_cli_lifecycle_summary_omits_phantom_and_out_of_root_paths(
    tmp_path: Path,
) -> None:
    from ecomsre.cli import summarize_lifecycle_execution

    execution = LifecycleExecution(
        result=TerminalResult(
            outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
            reason_code="COMPOSE_UP_MUTATION_UNCERTAIN",
        ),
        artifact_paths=LifecycleArtifactPaths(
            artifacts_root=tmp_path,
            ownership_intent=tmp_path / "missing.json",
            manual_diagnostic=tmp_path.parent / "outside.json",
        ),
    )

    summary = summarize_lifecycle_execution(execution)

    assert summary.evidence_paths == ()


def test_environment_run_id_is_validated_before_dispatch() -> None:
    completed = _run_cli(
        "phase0",
        "up",
        extra_env={"ECOMSRE_RUN_ID": "bad;touch-marker"},
    )

    assert completed.returncode == EXPECTED_EXIT_CODES["INVALID_INVOCATION"]
    assert "run_id" in completed.stderr.lower()


def test_pyproject_declares_python_311_and_only_approved_dependencies() -> None:
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["requires-python"] == ">=3.11"
    assert pyproject["project"]["dependencies"] == [
        "fastapi>=0.116,<1",
        "httpx>=0.28,<1",
        "pydantic>=2.0,<3",
        "tiktoken==0.13.0",
        "uvicorn>=0.35,<1",
    ]
    assert pyproject["dependency-groups"]["dev"] == ["pytest>=8.0,<9"]
    assert "scripts" not in pyproject["project"]


def _run_cli(
    *arguments: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    src = str(ROOT / "src")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-m", "ecomsre.cli", *arguments],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
