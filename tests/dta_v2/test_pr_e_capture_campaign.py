from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace
import os

import pytest
import ecomsre.dta_v2.owned_capture as owned_capture_module

from ecomsre.dta_v2.capture_campaign import (
    CaptureCaseQualityFailure,
    CaptureCaseQualityFailureCode,
    CaptureCampaignClosure,
    CaptureFailureCode,
    CaptureFailureOperation,
    CaptureOperationFailure,
    CaptureTerminal,
    EmailMemoryObservation,
    build_default_capture_plan,
    run_capture_campaign_attempt,
)
from ecomsre.dta_v2.contracts import semantic_sha256
from ecomsre.dta_v2.docker_read_adapters import DockerReadAdapter
from ecomsre.dta_v2.evaluation_contracts import EvaluationSplit
from ecomsre.dta_v2.owned_capture import (
    OwnedCaptureLifecycle,
    OwnedEmailController,
    OwnedRecommendationController,
    build_capture_flag_document,
    build_evaluator_truth,
    require_capture_case_quality,
)
from ecomsre.dta_v2.evaluation_contracts import AgentVisibleReplayCase
from ecomsre.dta_v2.read_tools import ReadBackendFailure
from ecomsre.dta_v2.tool_contracts import (
    RuntimeState,
    ToolErrorCode,
    build_inspect_resource_usage_request,
)


ROOT = Path(__file__).resolve().parents[2]


@dataclass
class FakeCaptureLifecycle:
    calibration: dict[str, EmailMemoryObservation]
    fail_restore_case: str | None = None
    fail_capture_case: str | None = None
    fail_quality_case: str | None = None

    def __post_init__(self) -> None:
        self.events: list[str] = []
        self.active_condition: str | None = None
        self.cleanup_calls = 0

    def admit(self) -> None:
        self.events.append("admit")

    def start(self) -> None:
        self.events.append("start")

    def wait_ready(self) -> None:
        self.events.append("ready")

    def observe_baseline_memory(self) -> EmailMemoryObservation:
        self.events.append("baseline-memory")
        return EmailMemoryObservation(
            maximum_memory_bytes=100_000_000,
            memory_delta_bytes=100_000,
            maximum_slope_bytes_per_second=10_000.0,
        )

    def apply_email_calibration(self, variant: str) -> None:
        assert self.active_condition is None
        self.active_condition = f"calibration:{variant}"
        self.events.append(self.active_condition)

    def observe_email_calibration(self, variant: str) -> EmailMemoryObservation:
        assert self.active_condition == f"calibration:{variant}"
        self.events.append(f"observe:{variant}")
        return self.calibration[variant]

    def apply_case(self, case, *, selected_email_variant: str) -> None:
        assert self.active_condition is None
        self.active_condition = case.case_id
        self.events.append(f"apply:{case.case_id}:{selected_email_variant}")

    def capture_case(self, case):
        assert self.active_condition == case.case_id
        self.events.append(f"capture:{case.case_id}")
        if case.case_id == self.fail_quality_case:
            raise CaptureCaseQualityFailure(
                CaptureCaseQualityFailureCode.REQUIRED_SOURCE_UNAVAILABLE
            )
        if case.case_id == self.fail_capture_case:
            raise RuntimeError("typed capture failure")
        return f"{int(case.case_id[-3:]):064x}"

    def restore_baseline(self) -> None:
        assert self.active_condition is not None
        current = self.active_condition
        self.events.append(f"restore:{current}")
        self.active_condition = None
        if current == self.fail_restore_case:
            raise RuntimeError("typed reset failure")

    def verify_baseline(self) -> None:
        assert self.active_condition is None
        self.events.append("verify-baseline")

    def cleanup(self, *, baseline_restored: bool):
        self.cleanup_calls += 1
        self.events.append(f"cleanup:{baseline_restored}")
        return {
            "verdict": "CLEAN" if baseline_restored else "BLOCKED",
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
        }


def _safe_calibration() -> dict[str, EmailMemoryObservation]:
    return {
        "10x": EmailMemoryObservation(
            maximum_memory_bytes=105_000_000,
            memory_delta_bytes=5_000_000,
            maximum_slope_bytes_per_second=100_000.0,
        ),
        "100x": EmailMemoryObservation(
            maximum_memory_bytes=130_000_000,
            memory_delta_bytes=30_000_000,
            maximum_slope_bytes_per_second=1_000_000.0,
        ),
        "1000x": EmailMemoryObservation(
            maximum_memory_bytes=300_000_000,
            memory_delta_bytes=200_000_000,
            maximum_slope_bytes_per_second=5_000_000.0,
        ),
    }


def test_default_capture_plan_freezes_exact_matrix_and_meaningful_held_out() -> None:
    plan = build_default_capture_plan(base_head="a" * 40)

    assert len(plan.cases) == 12
    assert sum(item.split is EvaluationSplit.DEVELOPMENT for item in plan.cases) == 6
    assert sum(item.split is EvaluationSplit.HELD_OUT for item in plan.cases) == 3
    assert sum(item.split is EvaluationSplit.NO_ACTION for item in plan.cases) == 3
    assert len({item.case_id for item in plan.cases}) == 12
    assert next(item for item in plan.cases if item.case_id == "dta-case-005").fault_variant == "SELECTED"
    for held_out in (item for item in plan.cases if item.split is EvaluationSplit.HELD_OUT):
        same_family_dev = [
            item
            for item in plan.cases
            if item.split is EvaluationSplit.DEVELOPMENT
            and item.operational_family == held_out.operational_family
        ]
        assert same_family_dev
        assert all(item.condition_signature != held_out.condition_signature for item in same_family_dev)


def test_capture_campaign_calibrates_ascending_and_closes_clean() -> None:
    lifecycle = FakeCaptureLifecycle(_safe_calibration())
    closure = run_capture_campaign_attempt(
        plan=build_default_capture_plan(base_head="a" * 40),
        lifecycle=lifecycle,
    )

    assert closure.terminal is CaptureTerminal.PASS
    assert closure.failure_code is None
    assert closure.selected_email_variant == "1000x"
    assert len(closure.captured_case_sha256s) == 12
    assert lifecycle.cleanup_calls == 1
    assert lifecycle.active_condition is None
    assert closure.cleanup_verdict == "CLEAN"
    assert closure.prohibited_action_counters.model_dump() == {
        "agent_calls": 0,
        "provider_calls": 0,
        "runbook_executions": 0,
        "executor_calls": 0,
        "verifier_calls": 0,
        "remediation_writes": 0,
    }


def test_unsafe_1000x_selects_100x_before_case_capture() -> None:
    calibration = _safe_calibration()
    calibration["1000x"] = EmailMemoryObservation(
        maximum_memory_bytes=900_000_000,
        memory_delta_bytes=800_000_000,
        maximum_slope_bytes_per_second=30_000_000.0,
    )
    closure = run_capture_campaign_attempt(
        plan=build_default_capture_plan(base_head="a" * 40),
        lifecycle=FakeCaptureLifecycle(calibration),
    )

    assert closure.terminal is CaptureTerminal.PASS
    assert closure.selected_email_variant == "100x"
    assert closure.calibration_observations[-1].safe is False


def test_restore_failure_stops_campaign_and_cleanup_remains_attempted() -> None:
    lifecycle = FakeCaptureLifecycle(
        _safe_calibration(), fail_restore_case="dta-case-003"
    )
    closure = run_capture_campaign_attempt(
        plan=build_default_capture_plan(base_head="a" * 40),
        lifecycle=lifecycle,
    )

    assert closure.terminal is CaptureTerminal.BLOCKED
    assert closure.failure_code is CaptureFailureCode.BASELINE_RESTORE_FAILED
    assert len(closure.captured_case_sha256s) == 3
    assert lifecycle.cleanup_calls == 1
    assert closure.cleanup_verdict == "BLOCKED"


def test_case_capture_failure_restores_baseline_before_cleaning_up() -> None:
    lifecycle = FakeCaptureLifecycle(
        _safe_calibration(), fail_capture_case="dta-case-003"
    )
    closure = run_capture_campaign_attempt(
        plan=build_default_capture_plan(base_head="a" * 40),
        lifecycle=lifecycle,
    )

    assert closure.terminal is CaptureTerminal.BLOCKED
    assert closure.failure_code is CaptureFailureCode.CASE_CAPTURE_FAILED
    assert closure.failure_operation is CaptureFailureOperation.CAPTURE_CASE
    assert closure.failed_case_id == "dta-case-003"
    assert len(closure.captured_case_sha256s) == 2
    assert lifecycle.events[-3:] == [
        "restore:dta-case-003",
        "verify-baseline",
        "cleanup:True",
    ]
    assert closure.baseline_restored is True
    assert closure.cleanup_verdict == "CLEAN"
    assert closure.cleanup_failure_code is None


def test_case_quality_failure_is_typed_without_backend_text() -> None:
    lifecycle = FakeCaptureLifecycle(
        _safe_calibration(), fail_quality_case="dta-case-002"
    )
    closure = run_capture_campaign_attempt(
        plan=build_default_capture_plan(base_head="a" * 40),
        lifecycle=lifecycle,
    )

    assert closure.terminal is CaptureTerminal.BLOCKED
    assert closure.failure_code is CaptureFailureCode.CASE_CAPTURE_FAILED
    assert closure.failure_operation is CaptureFailureOperation.CAPTURE_CASE
    assert closure.failed_case_id == "dta-case-002"
    assert closure.quality_failure_code is (
        CaptureCaseQualityFailureCode.REQUIRED_SOURCE_UNAVAILABLE
    )
    assert closure.baseline_restored is True
    assert closure.cleanup_verdict == "CLEAN"


def test_legacy_capture_closure_and_round_trip_keep_original_digest() -> None:
    current = run_capture_campaign_attempt(
        plan=build_default_capture_plan(base_head="a" * 40),
        lifecycle=FakeCaptureLifecycle(_safe_calibration()),
    )
    legacy = current.model_dump(
        mode="json",
        exclude={
            "failure_operation",
            "recovery_failure_operation",
            "failed_case_id",
            "failure_http_status",
            "recovery_failure_http_status",
            "closure_sha256",
        },
    )
    legacy["closure_sha256"] = semantic_sha256(legacy)

    parsed = CaptureCampaignClosure.model_validate_json(json.dumps(legacy))
    assert parsed.closure_sha256 == legacy["closure_sha256"]
    assert CaptureCampaignClosure.model_validate_json(
        parsed.model_dump_json()
    ).closure_sha256 == legacy["closure_sha256"]


class _UnexpectedBackend:
    def execute(self, request):
        raise RuntimeError("dynamic container identity must never be retained")


def test_capture_fixture_maps_unexpected_backend_error_to_fixed_safe_code(
    tmp_path: Path,
) -> None:
    lifecycle = OwnedCaptureLifecycle(
        repository_root=ROOT,
        private_root=tmp_path,
        plan=build_default_capture_plan(base_head="a" * 40),
        stabilization_seconds=0,
    )
    lifecycle.backend = _UnexpectedBackend()  # type: ignore[assignment]
    request = build_inspect_resource_usage_request(
        run_id="a" * 32,
        services=("recommendation",),
        sampling_window_seconds=5,
        sample_count=3,
    )

    fixture = lifecycle._capture_fixture(request)
    assert fixture.error_code is ToolErrorCode.INTERNAL_CONTRACT_VIOLATION
    assert fixture.records == ()
    assert "dynamic container" not in fixture.model_dump_json()


class _RecordingFlags:
    def __init__(self) -> None:
        self.applied = 0

    def apply(self, document):
        self.applied += 1
        return "a" * 64


class _StopFailsOnceRecommendation:
    def __init__(self) -> None:
        self.stop_calls = 0
        self.ensure_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1
        raise RuntimeError("safe fixed stop failure")

    def ensure_running(self) -> None:
        self.ensure_calls += 1


class _EmailRestartRecorder:
    def __init__(self) -> None:
        self.restart_calls = 0

    def restart(self) -> None:
        self.restart_calls += 1


class _OwnedEmailDocker:
    def _owned_container_identity(self, service: str) -> str | None:
        assert service == "email"
        return "b" * 64

    def _runtime_for(self, service: str):
        assert service == "email"
        return SimpleNamespace(
            state=RuntimeState.RUNNING,
            health=SimpleNamespace(value="HEALTHY"),
        )


class _RecordingMutationClient:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def post(self, path: str) -> None:
        self.paths.append(path)


def test_owned_email_restart_uses_exact_owned_container_endpoint() -> None:
    backend = SimpleNamespace(
        config=SimpleNamespace(
            docker_endpoint="unix:///private/tmp/never-used.sock",
            timeout_seconds=1.0,
        ),
        docker=_OwnedEmailDocker(),
    )
    controller = OwnedEmailController(backend)  # type: ignore[arg-type]
    client = _RecordingMutationClient()
    controller.client = client  # type: ignore[assignment]

    controller.restart()

    assert client.paths == [f"/containers/{'b' * 64}/restart?t=15"]


def test_owned_case_registers_restore_authority_before_first_mutation(
    tmp_path: Path,
) -> None:
    plan = build_default_capture_plan(base_head="a" * 40)
    lifecycle = OwnedCaptureLifecycle(
        repository_root=ROOT,
        private_root=tmp_path,
        plan=plan,
        stabilization_seconds=0,
    )
    upstream = json.loads(
        (
            ROOT / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
        ).read_text(encoding="utf-8")
    )
    lifecycle.upstream_flag = upstream
    lifecycle.baseline_document = build_capture_flag_document(upstream, load_vus=25)
    flags = _RecordingFlags()
    recommendation = _StopFailsOnceRecommendation()
    lifecycle.flag_controller = flags  # type: ignore[assignment]
    lifecycle.recommendation = recommendation  # type: ignore[assignment]
    case = next(item for item in plan.cases if item.case_id == "dta-case-003")

    with pytest.raises(RuntimeError):
        lifecycle.apply_case(case, selected_email_variant="1000x")
    assert lifecycle.active_condition == case.case_id

    lifecycle.restore_baseline()
    assert lifecycle.active_condition is None
    assert recommendation.ensure_calls == 1
    assert flags.applied == 2


def test_email_fault_restore_restarts_owned_email_after_baseline_flags(
    tmp_path: Path,
) -> None:
    plan = build_default_capture_plan(base_head="a" * 40)
    lifecycle = OwnedCaptureLifecycle(
        repository_root=ROOT,
        private_root=tmp_path,
        plan=plan,
        stabilization_seconds=0,
    )
    upstream = json.loads(
        (
            ROOT / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
        ).read_text(encoding="utf-8")
    )
    lifecycle.upstream_flag = upstream
    lifecycle.baseline_document = build_capture_flag_document(upstream, load_vus=25)
    flags = _RecordingFlags()
    recommendation = _StopFailsOnceRecommendation()
    email = _EmailRestartRecorder()
    lifecycle.flag_controller = flags  # type: ignore[assignment]
    lifecycle.recommendation = recommendation  # type: ignore[assignment]
    lifecycle.email = email  # type: ignore[attr-defined]
    lifecycle.active_condition = "calibration:1000x"
    lifecycle.email_restart_required = True  # type: ignore[attr-defined]

    lifecycle.restore_baseline()

    assert lifecycle.active_condition is None
    assert flags.applied == 1
    assert recommendation.ensure_calls == 1
    assert email.restart_calls == 1


class _OwnedIdentityDocker:
    def _owned_container_identity(self, service: str) -> str | None:
        assert service == "recommendation"
        return "a" * 64


class _PostFails:
    def post(self, path: str) -> None:
        raise RuntimeError(f"dynamic identity must not escape: {path}")


class _PostRejected:
    def post(self, path: str) -> None:
        raise owned_capture_module._DockerMutationHTTPError(409)


def test_recommendation_stop_post_failure_exposes_only_fixed_operation() -> None:
    backend = SimpleNamespace(
        config=SimpleNamespace(
            docker_endpoint="unix:///private/tmp/never-used.sock",
            timeout_seconds=1.0,
        ),
        docker=_OwnedIdentityDocker(),
    )
    controller = OwnedRecommendationController(backend)  # type: ignore[arg-type]
    assert controller.client.timeout_seconds == 45.0
    controller.client = _PostFails()  # type: ignore[assignment]

    with pytest.raises(CaptureOperationFailure) as caught:
        controller.stop()
    assert str(caught.value) == "RECOMMENDATION_STOP_POST"
    assert "dynamic identity" not in str(caught.value)

    controller.client = _PostRejected()  # type: ignore[assignment]
    with pytest.raises(CaptureOperationFailure) as rejected:
        controller.stop()
    assert rejected.value.http_status == 409


class _SuccessResponse:
    status = 200

    def read(self, limit: int) -> bytes:
        assert limit == 1_000_001
        return b""


class _SuccessConnection:
    def __init__(self, socket_path: str, *, timeout: float) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    def request(self, method: str, path: str, *, headers) -> None:
        assert method == "POST"
        assert path.endswith("/stop?t=15")

    def getresponse(self) -> _SuccessResponse:
        return _SuccessResponse()

    def close(self) -> None:
        pass


class _RestartSuccessConnection(_SuccessConnection):
    def request(self, method: str, path: str, *, headers) -> None:
        assert method == "POST"
        assert path == f"/containers/{'b' * 64}/restart?t=15"


def test_exact_docker_mutation_accepts_2xx_only_then_requires_state_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        owned_capture_module,
        "_UnixSocketHTTPConnection",
        _SuccessConnection,
    )
    client = owned_capture_module._UnixSocketDockerMutationClient(
        "/private/tmp/fake-docker.sock", timeout_seconds=1.0
    )

    client.post(f"/containers/{'a' * 64}/stop?t=15")


def test_exact_docker_mutation_accepts_only_fixed_owned_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        owned_capture_module,
        "_UnixSocketHTTPConnection",
        _RestartSuccessConnection,
    )
    client = owned_capture_module._UnixSocketDockerMutationClient(
        "/private/tmp/fake-docker.sock", timeout_seconds=1.0
    )

    client.post(f"/containers/{'b' * 64}/restart?t=15")
    for path in (
        f"/containers/{'b' * 64}/restart",
        f"/containers/{'b' * 64}/restart?t=1",
        "/containers/email/restart?t=15",
    ):
        with pytest.raises(ValueError, match="outside exact"):
            client.post(path)


class _CleanEnvironment:
    def cleanup(self, *, baseline_restored: bool):
        assert baseline_restored is True
        return SimpleNamespace(
            model_dump=lambda **_: {
                "baseline_restored": True,
                "owned_containers": 0,
                "owned_networks": 0,
                "owned_volumes": 0,
                "non_owned_resources_changed": False,
                "verdict": "CLEAN",
            }
        )


def test_owned_cleanup_restricts_runtime_flag_after_container_shutdown(
    tmp_path: Path,
) -> None:
    flag = tmp_path / "runtime" / "flagd" / "demo.flagd.json"
    flag.parent.mkdir(parents=True)
    flag.write_text("{}", encoding="utf-8")
    flag.chmod(0o644)
    lifecycle = OwnedCaptureLifecycle(
        repository_root=ROOT,
        private_root=tmp_path,
        plan=build_default_capture_plan(base_head="a" * 40),
        stabilization_seconds=0,
    )
    lifecycle.environment = _CleanEnvironment()  # type: ignore[assignment]
    lifecycle.flag_file = flag

    result = lifecycle.cleanup(baseline_restored=True)

    assert result["verdict"] == "CLEAN"
    assert os.stat(flag).st_mode & 0o777 == 0o600


class _ExitedOwnedDocker:
    def get_json(self, path: str) -> object:
        labels = {
            "com.docker.compose.project": "ecomsre-live-sandbox-v1",
            "io.ecomsre.sandbox.id": "ecomsre-live-v1",
            "com.docker.compose.service": "recommendation",
        }
        if path.startswith("/containers/json?"):
            return [{"Id": "a" * 64, "Labels": labels}]
        if path == f"/containers/{'a' * 64}/json":
            return {
                "Config": {"Labels": labels},
                "State": {"Status": "exited", "ExitCode": 0},
                "RestartCount": 0,
            }
        raise AssertionError(f"stopped-container stats must not be read: {path}")


def test_stopped_owned_container_resource_read_is_typed_unavailable() -> None:
    adapter = DockerReadAdapter(
        docker=_ExitedOwnedDocker(),
        compose_project="ecomsre-live-sandbox-v1",
        sandbox_label_key="io.ecomsre.sandbox.id",
        sandbox_label_value="ecomsre-live-v1",
        sleep=lambda _: None,
    )
    request = build_inspect_resource_usage_request(
        run_id="a" * 32,
        services=("recommendation",),
        sampling_window_seconds=5,
        sample_count=3,
    )

    with pytest.raises(ReadBackendFailure) as caught:
        adapter.inspect_resources(request)
    assert caught.value.error_code is ToolErrorCode.SOURCE_UNAVAILABLE


def test_capture_flag_builder_changes_only_three_exact_upstream_fields() -> None:
    upstream = json.loads(
        (
            ROOT / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
        ).read_text(encoding="utf-8")
    )
    document = build_capture_flag_document(
        upstream, load_vus=10, payment_variant="75%", email_variant="100x"
    )

    expected = json.loads(json.dumps(upstream))
    expected["flags"]["loadGeneratorVUs"]["defaultVariant"] = "10"
    expected["flags"]["paymentFailure"]["defaultVariant"] = "75%"
    expected["flags"]["emailMemoryLeak"]["defaultVariant"] = "100x"
    assert document == expected
    with pytest.raises(ValueError):
        build_capture_flag_document(upstream, load_vus=10, email_variant="10000x")


def test_default_plan_builds_exact_evaluator_truth_without_safe_case_coupling() -> None:
    plan = build_default_capture_plan(base_head="a" * 40)
    truths = tuple(build_evaluator_truth(item) for item in plan.cases)

    assert len(truths) == 12
    assert truths[0].expected_root_service == "payment"
    assert truths[2].expected_root_service == "recommendation"
    assert truths[4].expected_root_service == "email"
    assert truths[9].expected_terminal.value == "ABSTAIN"
    assert truths[10].expected_terminal.value == "NEED_MORE_EVIDENCE"
    assert truths[11].expected_terminal.value == "ABSTAIN"


def test_promoted_positive_cases_meet_mechanism_specific_capture_quality() -> None:
    plan = build_default_capture_plan(base_head="a" * 40)
    by_id = {item.case_id: item for item in plan.cases}
    for index in range(1, 7):
        case_id = f"dta-case-{index:03d}"
        case = AgentVisibleReplayCase.model_validate_json(
            (
                ROOT
                / "config/dta-v2/evaluation/development/agent-visible"
                / f"{case_id}.json"
            ).read_text(encoding="utf-8")
        )
        require_capture_case_quality(by_id[case_id], case.observations)
