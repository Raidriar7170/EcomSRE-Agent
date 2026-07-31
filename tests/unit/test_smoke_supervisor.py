import inspect
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import ecomsre.cli as cli_module
import ecomsre.phase0.smoke as smoke_module
from ecomsre.environment.readiness import (
    CandidateReadinessPolicy,
    ReadinessCollectionError,
)
from ecomsre.phase0.smoke import (
    EnvironmentStartDisposition,
    SmokeEnvironmentStart,
    SmokeExecutionError,
    SmokeSupervisorState,
    finalize_supervised_smoke,
    supervise_smoke_attempt,
)
from ecomsre.phase0.models import (
    DiagnosticStatus,
    MeasurementPhase,
    Outcome,
    TerminalResult,
)
from ecomsre.telemetry.prometheus import _validate_promotion_windows
from ecomsre.telemetry.prometheus import FixtureState


RUN_ID = "e" * 32


@dataclass
class FakeOperations:
    fail_at: str | None = None

    def __post_init__(self) -> None:
        self.events: list[str] = []

    def _step(self, name: str):
        self.events.append(name)
        if self.fail_at == name:
            raise RuntimeError(name)
        return name

    def start_environment(self):
        self._step("up")
        return SmokeEnvironmentStart(
            result=TerminalResult(
                outcome=Outcome.SUCCESS,
                reason_code="UP",
            ),
            disposition=(
                EnvironmentStartDisposition.OWNED_ENVIRONMENT_STARTED
            ),
        )

    def stabilize_initial_readiness(self, seconds: float):
        assert seconds == 65.0
        self._step(f"stabilize:{seconds}")

    def fresh_authority(self, boundary: str):
        return self._step(f"authority:{boundary}")

    def initial_readiness(self, authority):
        assert authority == "authority:initial"
        self._step("readiness:initial")

    def open_control(self, authority):
        assert authority == "authority:initial"
        return self._step("control:open")

    def promote(self, authority, control):
        assert control == "control:open"
        self._step("promotion")
        return {"origin_run_id": RUN_ID}

    def frozen_readiness(self, authority):
        self._step("readiness:post-promotion")

    def diagnostic(self, authority, control):
        self._step("diagnostic")
        return {"passed": True}

    def final_readiness(self, authority):
        self._step("readiness:final")

    def refresh_before_reset(self, control):
        self._step("refresh:final-reset")

    def reset(self, control):
        self._step("reset")
        return TerminalResult(outcome=Outcome.SUCCESS, reason_code="RESET")

    def close_control(self, control):
        self._step("control:close")

    def fresh_stop_authority(self):
        return self._step("authority:stop")

    def stop_environment(self, authority):
        self._step("down")
        return TerminalResult(outcome=Outcome.SUCCESS, reason_code="DOWN")

    def cleanup_owned_volumes(self, authority):
        self._step("cleanup:volumes")
        return TerminalResult(
            outcome=Outcome.SUCCESS,
            reason_code="OWNED_NAMED_VOLUMES_CLEANED",
        )

    def finalize(self, state: SmokeSupervisorState):
        self._step("finalize")
        return state

    def write_minimal_terminal(self, state: SmokeSupervisorState, reason: str):
        self.events.append(f"minimal:{reason}")


def test_supervisor_orders_initial_readiness_before_control_and_refreshes_boundaries():
    operations = FakeOperations()

    state = supervise_smoke_attempt(run_id=RUN_ID, operations=operations)

    assert operations.events == [
        "up",
        "stabilize:65.0",
        "authority:initial",
        "readiness:initial",
        "control:open",
        "promotion",
        "authority:post-promotion",
        "readiness:post-promotion",
        "diagnostic",
        "authority:final",
        "readiness:final",
        "refresh:final-reset",
        "reset",
        "control:close",
        "authority:stop",
        "down",
        "cleanup:volumes",
        "finalize",
    ]
    assert state.environment_started
    assert state.reset_succeeded
    assert state.stop_succeeded
    assert state.owned_volume_cleanup_succeeded


def test_supervisor_reports_owned_volume_cleanup_failure_after_successful_stop():
    class CleanupFailure(FakeOperations):
        def cleanup_owned_volumes(self, authority):
            self._step("cleanup:volumes")
            return TerminalResult(
                outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
                reason_code="OWNED_VOLUME_CLEANUP_FAILED",
            )

    operations = CleanupFailure()

    state = supervise_smoke_attempt(run_id=RUN_ID, operations=operations)

    assert state.stop_succeeded
    assert state.owned_volume_cleanup_attempted
    assert not state.owned_volume_cleanup_succeeded
    assert "OWNED_VOLUME_CLEANUP_FAILED" in state.failure_reason_codes
    assert operations.events[-2:] == ["cleanup:volumes", "finalize"]


@pytest.mark.parametrize(
    "failure_step",
    [
        "up",
        "stabilize:65.0",
        "authority:initial",
        "readiness:initial",
        "control:open",
        "promotion",
        "authority:post-promotion",
        "readiness:post-promotion",
        "diagnostic",
        "authority:final",
        "readiness:final",
        "refresh:final-reset",
        "reset",
        "control:close",
        "authority:stop",
        "down",
    ],
)
def test_supervisor_finalizes_and_stops_after_every_post_up_failure(
    failure_step: str,
) -> None:
    operations = FakeOperations(fail_at=failure_step)

    state = supervise_smoke_attempt(run_id=RUN_ID, operations=operations)

    assert operations.events[-1] == "finalize"
    assert failure_step in state.failure_reason_codes
    if failure_step not in {"up", "authority:stop", "down"}:
        assert "authority:stop" in operations.events
    if failure_step not in {"up", "authority:stop"}:
        assert "down" in operations.events
    if failure_step != "up":
        assert state.environment_started
    if failure_step not in {"up", "authority:stop", "down"}:
        assert state.stop_succeeded
    if failure_step in {
        "promotion",
        "readiness:post-promotion",
        "diagnostic",
        "readiness:final",
        "refresh:final-reset",
        "reset",
        "control:close",
    }:
        assert ("reset" in operations.events) is (
            failure_step != "refresh:final-reset"
        )


def test_supervisor_does_not_stop_after_pre_mutation_start_result() -> None:
    class FailedStart(FakeOperations):
        def start_environment(self):
            self._step("up")
            return SmokeEnvironmentStart(
                result=TerminalResult(
                    outcome=Outcome.BLOCKED_ENVIRONMENT,
                    reason_code="UP_FAILED",
                ),
                disposition=(
                    EnvironmentStartDisposition.PRE_MUTATION_BLOCKED
                ),
            )

    operations = FailedStart()

    state = supervise_smoke_attempt(run_id=RUN_ID, operations=operations)

    assert operations.events == ["up", "finalize"]
    assert not state.environment_started
    assert not state.stop_required
    assert not state.stop_attempted
    assert "UP_FAILED" in state.failure_reason_codes
    assert state.failure_statuses == [DiagnosticStatus.BLOCKED]


def test_stop_authority_observer_failure_is_reported_but_stop_continues(
    tmp_path: Path,
) -> None:
    for zone in ("observer-visible", "evaluator-only"):
        run_root = tmp_path / zone / RUN_ID
        run_root.mkdir(parents=True)
        (run_root / "evidence.json").write_text("{}", encoding="utf-8")

    class ObserverFailure(FakeOperations):
        def fresh_stop_authority(self):
            self._step("authority:stop")
            return SimpleNamespace(
                evidence_persistence_error="OBSERVER_PERSISTENCE_FAILED"
            )

        def finalize(self, state: SmokeSupervisorState):
            self._step("finalize")
            return finalize_supervised_smoke(
                state=state,
                artifacts_root=tmp_path,
            )

    operations = ObserverFailure()

    report = supervise_smoke_attempt(run_id=RUN_ID, operations=operations)

    assert "authority:stop" in operations.events
    assert "down" in operations.events
    assert report.safe_stop_completed
    assert report.diagnostic_status is DiagnosticStatus.UNSAFE
    assert "STOP_AUTHORITY_OBSERVER_PERSISTENCE_FAILED" in (
        report.failure_reason_codes
    )


def test_supervisor_preserves_typed_unsafe_execution_error() -> None:
    class UnsafeDiagnostic(FakeOperations):
        def diagnostic(self, authority, control):
            self._step("diagnostic")
            raise SmokeExecutionError(
                "POLICY_GATE_UNSAFE",
                status=DiagnosticStatus.UNSAFE,
            )

    state = supervise_smoke_attempt(
        run_id=RUN_ID,
        operations=UnsafeDiagnostic(),
    )

    assert "POLICY_GATE_UNSAFE" in state.failure_reason_codes
    assert DiagnosticStatus.UNSAFE in state.failure_statuses


def test_pre_http_readiness_failure_artifact_is_referenced_by_smoke_report(
    tmp_path: Path,
) -> None:
    for zone in ("observer-visible", "evaluator-only"):
        run_root = tmp_path / zone / RUN_ID
        run_root.mkdir(parents=True)
        (run_root / "evidence.json").write_text("{}", encoding="utf-8")
    artifact = (
        tmp_path
        / "observer-visible"
        / RUN_ID
        / "lifecycle"
        / "initial-readiness"
        / "123"
        / "pre-http-failure.json"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}", encoding="utf-8")
    state = SmokeSupervisorState(
        run_id=RUN_ID,
        failure_reason_codes=["INITIAL_READINESS_LIFECYCLE_AUTHORITY_INVALID"],
        failure_statuses=[DiagnosticStatus.FAILED],
    )

    report = finalize_supervised_smoke(
        state=state,
        artifacts_root=tmp_path,
    )

    assert report.attempts[0].initial_readiness_artifacts == (str(artifact),)


def test_post_http_v2_failure_summary_is_referenced_by_supervised_smoke_report(
    tmp_path: Path,
) -> None:
    for zone in ("observer-visible", "evaluator-only"):
        run_root = tmp_path / zone / RUN_ID
        run_root.mkdir(parents=True)
        (run_root / "evidence.json").write_text("{}", encoding="utf-8")
    artifact = (
        tmp_path
        / "observer-visible"
        / RUN_ID
        / "lifecycle"
        / "initial-readiness"
        / "456"
        / "summary.json"
    )

    class PostHttpReadinessFailure(FakeOperations):
        def initial_readiness(self, authority):
            assert authority == "authority:initial"
            self._step("readiness:initial")
            artifact.parent.mkdir(parents=True)
            artifact.write_text(
                '{"schema_version":"phase0.candidate-initial-readiness.v2"}',
                encoding="utf-8",
            )
            raise ReadinessCollectionError(
                "INITIAL_CANDIDATE_READINESS_INCOMPLETE",
                artifact_path=str(artifact),
            )

        def finalize(self, state: SmokeSupervisorState):
            self._step("finalize")
            return finalize_supervised_smoke(
                state=state,
                artifacts_root=tmp_path,
            )

    report = supervise_smoke_attempt(
        run_id=RUN_ID,
        operations=PostHttpReadinessFailure(),
    )

    assert report.attempts[0].initial_readiness_artifacts == (str(artifact),)
    assert "INITIAL_CANDIDATE_READINESS_INCOMPLETE" in report.failure_reason_codes


@pytest.mark.parametrize(
    ("step", "outcome", "expected"),
    [
        ("reset", Outcome.FAILED_ACCEPTANCE, DiagnosticStatus.FAILED),
        ("down", Outcome.MANUAL_INTERVENTION_REQUIRED, DiagnosticStatus.UNSAFE),
    ],
)
def test_supervisor_preserves_reset_and_stop_outcome_types(
    step: str,
    outcome: Outcome,
    expected: DiagnosticStatus,
) -> None:
    class TypedOutcome(FakeOperations):
        def reset(self, control):
            self._step("reset")
            if step == "reset":
                return TerminalResult(outcome=outcome, reason_code="RESET_TYPED")
            return TerminalResult(outcome=Outcome.SUCCESS, reason_code="RESET")

        def stop_environment(self, authority):
            self._step("down")
            if step == "down":
                return TerminalResult(outcome=outcome, reason_code="STOP_TYPED")
            return TerminalResult(outcome=Outcome.SUCCESS, reason_code="DOWN")

    state = supervise_smoke_attempt(run_id=RUN_ID, operations=TypedOutcome())

    assert expected in state.failure_statuses


def test_final_report_uses_typed_supervisor_status_and_exit_code(
    tmp_path: Path,
) -> None:
    for zone in ("observer-visible", "evaluator-only"):
        run_root = tmp_path / zone / RUN_ID
        run_root.mkdir(parents=True)
        (run_root / "evidence.json").write_text("{}", encoding="utf-8")
    state = SmokeSupervisorState(
        run_id=RUN_ID,
        failure_reason_codes=["MANUAL_REVIEW_REQUIRED"],
        failure_statuses=[DiagnosticStatus.UNSAFE],
    )

    report = finalize_supervised_smoke(state=state, artifacts_root=tmp_path)

    assert report.diagnostic_status is DiagnosticStatus.UNSAFE
    assert report.exit_code == DiagnosticStatus.UNSAFE.exit_code


def test_production_start_retains_available_authority_for_guarded_cleanup(
    monkeypatch,
) -> None:
    ownership = SimpleNamespace(
        run_id=RUN_ID,
        manifest_sha256="a" * 64,
        is_authentic=lambda: True,
    )
    monkeypatch.setattr(
        cli_module,
        "_execute_up",
        lambda *_args: cli_module.LifecycleExecution.model_construct(
            result=TerminalResult(
                outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
                reason_code="POST_UP_EVIDENCE_PERSISTENCE_FAILED",
            ),
            ownership_context=ownership,
            docker_endpoint="unix:///var/run/docker.sock",
            daemon_id="fixture-daemon",
            mutation_may_have_occurred=True,
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "_resolve_fresh_preflight",
        lambda *_args, **_kwargs: pytest.fail(
            "start must not recollect full preflight for stop authority"
        ),
    )
    operations = cli_module._ProductionSmokeOperations(
        args=SimpleNamespace(run_id=RUN_ID),
        context=SimpleNamespace(artifacts_root=Path("/unused")),
    )

    result = operations.start_environment()

    assert (
        result.result.reason_code
        == "POST_UP_EVIDENCE_PERSISTENCE_FAILED"
    )
    assert (
        result.disposition
        is EnvironmentStartDisposition.MUTATION_MAY_HAVE_OCCURRED
    )
    assert operations.stop_ownership is ownership
    assert operations.stop_docker_endpoint == "unix:///var/run/docker.sock"
    assert operations.stop_daemon_id == "fixture-daemon"


def test_production_initial_stabilization_sleeps_exactly_65_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = CandidateReadinessPolicy()
    sleeps: list[float] = []
    monkeypatch.setattr(cli_module.time, "sleep", sleeps.append)
    operations = cli_module._ProductionSmokeOperations(
        args=SimpleNamespace(run_id=RUN_ID),
        context=SimpleNamespace(),
    )

    operations.stabilize_initial_readiness(policy.initial_delay_seconds)

    assert sleeps == [policy.initial_delay_seconds]
    with pytest.raises(
        ValueError,
        match="initial readiness stabilization must remain 65 seconds",
    ):
        operations.stabilize_initial_readiness(64.0)
    assert sleeps == [policy.initial_delay_seconds]


def test_initial_stabilization_uses_candidate_policy_as_single_source() -> None:
    assert not hasattr(
        smoke_module,
        "INITIAL_READINESS_STABILIZATION_SECONDS",
    )
    production_source = inspect.getsource(
        cli_module._ProductionSmokeOperations.stabilize_initial_readiness
    )
    assert "65.0" not in production_source
    assert "CandidateReadinessPolicy" in production_source


@pytest.mark.parametrize(
    ("invalidated_field", "expected_reason"),
    [
        ("current", "CONTROL_MUTATION_AUTHORITY_EXPIRED"),
        ("preflight_authentic", "CONTROL_MUTATION_AUTHORITY_INVALID"),
        ("ownership_authentic", "CONTROL_MUTATION_AUTHORITY_INVALID"),
        ("run_id", "CONTROL_MUTATION_AUTHORITY_INVALID"),
    ],
)
def test_control_mutation_revalidates_authority_after_candidate_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalidated_field: str,
    expected_reason: str,
) -> None:
    state = {
        "current": True,
        "preflight_authentic": True,
        "ownership_authentic": True,
    }
    preflight = SimpleNamespace(
        run_id=RUN_ID,
        is_current=lambda: state["current"],
        is_authentic=lambda: state["preflight_authentic"],
    )
    ownership = SimpleNamespace(
        run_id=RUN_ID,
        is_authentic=lambda: state["ownership_authentic"],
    )
    operations = cli_module._ProductionSmokeOperations(
        args=SimpleNamespace(run_id=RUN_ID),
        context=SimpleNamespace(
            project_root=tmp_path,
            artifacts_root=tmp_path,
        ),
    )
    monkeypatch.setattr(
        operations,
        "fresh_authority",
        lambda _boundary: (preflight, ownership),
    )

    def collect(**_kwargs):
        if invalidated_field == "run_id":
            ownership.run_id = "f" * 32
        else:
            state[invalidated_field] = False
        return object()

    monkeypatch.setattr(
        cli_module,
        "collect_candidate_initial_readiness",
        collect,
    )

    with pytest.raises(SmokeExecutionError) as captured:
        operations._refresh_control_mutation(
            stage="diagnostic",
            phase=MeasurementPhase.FAULT,
        )

    assert captured.value.reason_code == expected_reason
    assert captured.value.status is DiagnosticStatus.UNSAFE


def test_supervisor_uses_minimal_terminal_when_report_finalization_fails() -> None:
    operations = FakeOperations(fail_at="finalize")

    with pytest.raises(RuntimeError, match="finalize"):
        supervise_smoke_attempt(run_id=RUN_ID, operations=operations)

    assert operations.events[-1] == "minimal:finalize"


def test_minimal_terminal_failure_does_not_mask_finalization_failure() -> None:
    class BrokenFallback(FakeOperations):
        def write_minimal_terminal(
            self,
            state: SmokeSupervisorState,
            reason: str,
        ):
            self.events.append(f"minimal:{reason}")
            raise RuntimeError("minimal")

    operations = BrokenFallback(fail_at="finalize")

    with pytest.raises(RuntimeError, match="finalize"):
        supervise_smoke_attempt(run_id=RUN_ID, operations=operations)

    assert operations.events[-1] == "minimal:finalize"


def test_promotion_provider_waits_past_actual_prior_window_end(
    monkeypatch,
) -> None:
    clock = {"value": 10.0}
    origin = datetime(2026, 7, 30, 1, 0, tzinfo=UTC)

    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = origin + timedelta(seconds=clock["value"])
            return value if tz is not None else value.replace(tzinfo=None)

    def sleep(seconds: float) -> None:
        clock["value"] += seconds

    class Controller:
        def reset(self):
            return _execution()

        def inject(self):
            return _execution()

    class Store:
        run_id = RUN_ID

        def __init__(self) -> None:
            self.paths = []

        def write_immutable(self, path, _payload):
            self.paths.append(path)

    def _execution():
        return SimpleNamespace(
            terminal_result=TerminalResult(
                outcome=Outcome.SUCCESS,
                reason_code="CONTROL_STATE_CONFIRMED",
            ),
            observer_event=SimpleNamespace(monotonic_duration_seconds=0.1),
        )

    monkeypatch.setattr(smoke_module.time, "monotonic", lambda: clock["value"])
    monkeypatch.setattr(smoke_module, "datetime", ClockDateTime)
    store = Store()
    refreshes = []
    provider = smoke_module._PromotionWindowProvider(
        run_id=RUN_ID,
        controller=Controller(),
        store=store,
        sleep=sleep,
        before_mutation=refreshes.append,
        duration_seconds=2,
    )

    windows = tuple(provider(phase) for phase in MeasurementPhase)

    _validate_promotion_windows(
        windows,
        run_id=RUN_ID,
        store_run_id=RUN_ID,
    )
    assert all(
        earlier.monotonic_ended_at < later.monotonic_started_at
        for earlier, later in zip(windows, windows[1:])
    )
    assert len(store.paths) == 3
    assert refreshes == list(MeasurementPhase)


def test_confirmed_transition_refreshes_before_controller_mutation() -> None:
    events = []

    class Controller:
        def inject(self):
            events.append("inject")
            return _execution()

        def reset(self):
            events.append("reset")
            return _execution()

    def _execution():
        return SimpleNamespace(
            terminal_result=TerminalResult(
                outcome=Outcome.SUCCESS,
                reason_code="CONTROL_STATE_CONFIRMED",
            ),
            observer_event=SimpleNamespace(monotonic_duration_seconds=0.1),
        )

    smoke_module._confirmed_transition(
        Controller(),
        MeasurementPhase.FAULT,
        before_mutation=lambda phase: events.append(f"refresh:{phase.value}"),
    )

    assert events == ["refresh:fault", "inject"]


def test_frozen_registry_revalidation_covers_all_current_run_promotion_phases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows: list[MeasurementPhase] = []
    revalidations: list[MeasurementPhase] = []

    class Capability:
        def __init__(self, phase: MeasurementPhase) -> None:
            self.phase = phase

        def is_authentic(self) -> bool:
            return True

    def provider(phase: MeasurementPhase) -> MeasurementPhase:
        windows.append(phase)
        return phase

    monkeypatch.setattr(
        smoke_module,
        "load_query_registry",
        lambda _path: SimpleNamespace(
            registry=SimpleNamespace(state=FixtureState.FROZEN)
        ),
    )
    monkeypatch.setattr(
        smoke_module,
        "_PromotionWindowProvider",
        lambda **_kwargs: provider,
    )
    monkeypatch.setattr(
        smoke_module,
        "revalidate_frozen_query_capability",
        lambda _path, **kwargs: (
            revalidations.append(kwargs["window"])
            or Capability(kwargs["window"])
        ),
    )

    capability = smoke_module.promote_or_revalidate_registry(
        project_root=tmp_path,
        store=SimpleNamespace(run_id=RUN_ID),
        client=object(),
        controller=object(),
        base_urls={"probe": "http://127.0.0.1:8080"},
        sleep=lambda _seconds: None,
        before_mutation=lambda _phase: None,
    )

    assert windows == list(MeasurementPhase)
    assert revalidations == list(MeasurementPhase)
    assert capability.phase is MeasurementPhase.RECOVERY


def test_production_promotion_maps_unfreezable_fixture_to_typed_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = SimpleNamespace(
        telemetry_store=None,
        client=None,
        base_urls=None,
        capability=None,
        controller=object(),
    )

    class Store:
        def __init__(self, *_args) -> None:
            self.run_id = RUN_ID

        def __enter__(self):
            return self

    monkeypatch.setattr(
        cli_module._ProductionSmokeOperations,
        "_authority",
        staticmethod(lambda _authority: (object(), object())),
    )
    monkeypatch.setattr(
        cli_module._ProductionSmokeOperations,
        "_control",
        staticmethod(lambda _control: handle),
    )
    monkeypatch.setattr(cli_module, "ObserverEvidenceStore", Store)
    monkeypatch.setattr(cli_module, "OwnedHttpClient", lambda **_kwargs: object())
    monkeypatch.setattr(
        cli_module,
        "_owned_base_urls",
        lambda _ownership: {"probe": "http://127.0.0.1:8080"},
    )
    monkeypatch.setattr(
        cli_module,
        "promote_or_revalidate_registry",
        lambda **_kwargs: (_ for _ in ()).throw(
            ValueError("required backend unavailable")
        ),
    )
    operations = cli_module._ProductionSmokeOperations(
        args=SimpleNamespace(run_id=RUN_ID),
        context=SimpleNamespace(
            artifacts_root=tmp_path,
            project_root=tmp_path,
        ),
    )

    with pytest.raises(SmokeExecutionError) as captured:
        operations.promote(object(), object())

    assert captured.value.reason_code == "BLOCKED_TELEMETRY_FIXTURE_UNRESOLVED"
    assert captured.value.status is DiagnosticStatus.BLOCKED


def test_production_promotion_preserves_existing_typed_unsafe_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = SimpleNamespace(
        telemetry_store=None,
        client=None,
        base_urls=None,
        capability=None,
        controller=object(),
    )

    class Store:
        def __init__(self, *_args) -> None:
            self.run_id = RUN_ID

        def __enter__(self):
            return self

    unsafe = SmokeExecutionError(
        "OWNERSHIP_BECAME_UNSAFE",
        status=DiagnosticStatus.UNSAFE,
    )
    monkeypatch.setattr(
        cli_module._ProductionSmokeOperations,
        "_authority",
        staticmethod(lambda _authority: (object(), object())),
    )
    monkeypatch.setattr(
        cli_module._ProductionSmokeOperations,
        "_control",
        staticmethod(lambda _control: handle),
    )
    monkeypatch.setattr(cli_module, "ObserverEvidenceStore", Store)
    monkeypatch.setattr(cli_module, "OwnedHttpClient", lambda **_kwargs: object())
    monkeypatch.setattr(
        cli_module,
        "_owned_base_urls",
        lambda _ownership: {"probe": "http://127.0.0.1:8080"},
    )
    monkeypatch.setattr(
        cli_module,
        "promote_or_revalidate_registry",
        lambda **_kwargs: (_ for _ in ()).throw(unsafe),
    )
    operations = cli_module._ProductionSmokeOperations(
        args=SimpleNamespace(run_id=RUN_ID),
        context=SimpleNamespace(
            artifacts_root=tmp_path,
            project_root=tmp_path,
        ),
    )

    with pytest.raises(SmokeExecutionError) as captured:
        operations.promote(object(), object())

    assert captured.value is unsafe
