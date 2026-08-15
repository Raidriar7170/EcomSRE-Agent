from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from ecomsre.dta_v2 import telemetry_adapters
from ecomsre.dta_v2.contracts import semantic_sha256
from ecomsre.dta_v2.read_only_smoke import (
    CleanupObservation,
    SmokeTerminal,
    _SandboxOwnedSmokeLifecycle,
    run_owned_read_only_smoke_attempt,
)
from ecomsre.dta_v2.read_tools import (
    BackendResult,
    FakeReadBackend,
    InvestigationReadTools,
    ReadBackendFailure,
)
from ecomsre.dta_v2.telemetry_adapters import (
    LocalReadBackendConfig,
    LocalSandboxReadBackend,
    _OwnedReadCapability,
    _issue_owned_read_capability,
)
from ecomsre.dta_v2.tool_contracts import (
    ReadAuthorityContext,
    ReadAuthorityMode,
    MetricKind,
    ToolErrorCode,
    build_fake_read_authority,
    build_query_metrics_request,
    build_search_logs_request,
)


RUN_ID = "b" * 32
START = datetime(2026, 8, 16, 6, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=2)


class EmptyHttp:
    def request_json(self, **kwargs: object) -> object:
        del kwargs
        return {}


class EmptyDocker:
    def get_json(self, path: str) -> object:
        del path
        return []


def _fabricated_owned_authority() -> ReadAuthorityContext:
    payload: dict[str, object] = {
        "schema_version": "dta-v2.read-authority.v1",
        "mode": ReadAuthorityMode.OWNED_LOCAL,
        "daemon_identity_sha256": semantic_sha256({"daemon_identity": "forged"}),
        "docker_context_sha256": semantic_sha256({"docker_context": "forged"}),
        "config_bundle_sha256": "1" * 64,
        "resolved_sandbox_sha256": "2" * 64,
        "resolved_endpoints_sha256": semantic_sha256(
            {
                "prometheus": "http://127.0.0.1:19090",
                "opensearch": "http://127.0.0.1:19200",
                "jaeger": "http://127.0.0.1:11686",
                "docker": "unix:///var/run/docker.sock",
            }
        ),
        "ownership_scope_sha256": semantic_sha256(
            {
                "compose_project": "ecomsre-live-sandbox-v1",
                "sandbox_label_key": "io.ecomsre.sandbox.id",
                "sandbox_label_value": "sandbox-opaque",
            }
        ),
    }
    return ReadAuthorityContext.model_validate(
        {**payload, "authority_sha256": semantic_sha256(payload)}
    )


def _config(authority: ReadAuthorityContext) -> LocalReadBackendConfig:
    return LocalReadBackendConfig(
        prometheus_base_url="http://127.0.0.1:19090",
        opensearch_base_url="http://127.0.0.1:19200",
        jaeger_base_url="http://127.0.0.1:11686",
        opensearch_index="otel-logs-*",
        docker_endpoint="unix:///var/run/docker.sock",
        compose_project="ecomsre-live-sandbox-v1",
        sandbox_label_key="io.ecomsre.sandbox.id",
        sandbox_label_value="sandbox-opaque",
        timeout_seconds=1.0,
        authority=authority,
    )


def test_owned_authority_has_no_public_assertion_builder() -> None:
    assert not hasattr(telemetry_adapters, "build_owned_read_authority")


def test_generic_backend_rejects_self_issued_owned_authority() -> None:
    config = _config(_fabricated_owned_authority())
    with pytest.raises(TypeError, match="lifecycle"):
        _OwnedReadCapability(config=config)
    with pytest.raises(TypeError, match="lifecycle|capability"):
        LocalSandboxReadBackend(
            config=config,
            http=EmptyHttp(),
            docker=EmptyDocker(),
        )


def test_injected_test_transports_use_fake_replay_authority() -> None:
    backend = LocalSandboxReadBackend(
        config=_config(build_fake_read_authority()),
        http=EmptyHttp(),
        docker=EmptyDocker(),
        sleep=lambda _: None,
    )
    assert backend.authority.mode is ReadAuthorityMode.FAKE_REPLAY


def test_observation_and_snapshot_persist_full_authority_context() -> None:
    backend = FakeReadBackend.healthy()
    request = build_query_metrics_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        metric_kinds=(MetricKind.ERROR_RATE,),
        max_results=1,
    )
    tools = InvestigationReadTools(run_id=RUN_ID, backend=backend)
    observation = tools.dispatch(request)
    snapshot = tools.snapshot()
    assert observation.authority == backend.authority
    assert snapshot.authority == backend.authority
    assert snapshot.observations[0].authority == snapshot.authority
    forged = observation.model_copy(
        update={"authority": _fabricated_owned_authority()}
    )
    with pytest.raises(ValueError, match="authority"):
        type(observation).model_validate(forged.model_dump())


@pytest.mark.parametrize(
    "leak",
    (
        "span=0123456789abcdef",
        "trace=0123456789abcdef0123456789abcdef",
        "id=550e8400-e29b-41d4-a716-446655440000",
        "id=00000000-0000-0000-0000-000000000000",
        "trace=0123456789аbcdef",
        "id=550e8400-e29b-41d4-α716-446655440000",
        "trace=0123456789ɑbcdef",
        "trace=0123456789abcԁef",
        "trace=0123456789aɑԁαаβ",
        "trace=ɑԁαаβсԁеɑԁαаβсԁе",
        "trace=0123456789abϲdef",
        "trace=0123456789abϹdef",
        "trace=0123456789abcdeϜ",
        "trace=0123456789aᎪᏴᏟᎠᎬ",
        "trace=0123456789abcdeꓓ",
        "еxpected rооt payment",
        "gοld label payment",
        "scenariο cοntrοller",
    ),
)
def test_identity_and_mixed_script_truth_bypasses_fail_closed(leak: str) -> None:
    request = build_search_logs_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        max_records=2,
    )
    observation = InvestigationReadTools(
        run_id=RUN_ID, backend=FakeReadBackend.with_log_message(leak)
    ).dispatch(request)
    assert observation.error_code is ToolErrorCode.TRUTH_ISOLATION_VIOLATION


@pytest.mark.parametrize(
    "message",
    (
        "支付服务暂时不可用",
        "неработоспособность сервиса",
        "ηλεκτροεγκεφαλογράφημα",
        "db-сервис-недоступен",
        "500-ошибка-подключения",
        "db:неработоспособность сервиса",
    ),
)
def test_ordinary_non_latin_diagnostic_remains_model_visible(message: str) -> None:
    request = build_search_logs_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        max_records=2,
    )
    tools = InvestigationReadTools(
        run_id=RUN_ID,
        backend=FakeReadBackend.with_log_message(message),
    )

    observation = tools.dispatch(request)

    assert observation.error_code is None
    assert observation.results[0].message == message


class DuplicateEmittingBackend:
    authority = build_fake_read_authority()

    def execute(self, request: object) -> BackendResult:
        del request
        raise ReadBackendFailure(ToolErrorCode.DUPLICATE_REQUEST)


def test_backend_cannot_emit_dispatcher_only_duplicate_terminal() -> None:
    request = build_search_logs_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        max_records=2,
    )
    tools = InvestigationReadTools(run_id=RUN_ID, backend=DuplicateEmittingBackend())
    observation = tools.dispatch(request)
    assert observation.error_code in {
        ToolErrorCode.INTERNAL_CONTRACT_VIOLATION,
        ToolErrorCode.SOURCE_SCHEMA_INVALID,
    }
    assert tools.snapshot().observations == (observation,)


@dataclass
class OrderedLifecycle:
    events: list[str] = field(default_factory=list)

    def admit(self) -> None:
        self.events.append("admit")

    def start(self) -> None:
        self.events.append("start")

    def wait_ready(self) -> None:
        self.events.append("ready")

    def authorize_reads(self):
        self.events.append("authorize")
        return FakeReadBackend.healthy()

    def read_baseline_sha256(self) -> str:
        self.events.append("baseline")
        return "a" * 64

    def cleanup_owned(self, *, baseline_unchanged: bool) -> CleanupObservation:
        assert baseline_unchanged
        self.events.append("cleanup")
        return CleanupObservation.clean()


def test_read_authority_is_issued_after_readiness_immediately_before_baseline(
    tmp_path: Path,
) -> None:
    lifecycle = OrderedLifecycle()
    closure = run_owned_read_only_smoke_attempt(
        private_root=tmp_path,
        smoke_id="c" * 32,
        service="payment",
        lifecycle=lifecycle,
    )
    assert closure.terminal is SmokeTerminal.PASS
    assert lifecycle.events == [
        "admit",
        "start",
        "ready",
        "authorize",
        "baseline",
        "baseline",
        "cleanup",
    ]


def test_concrete_sandbox_lifecycle_stabilizes_then_issues_read_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    lifecycle = _SandboxOwnedSmokeLifecycle(
        repository_root=tmp_path,
        private_root=tmp_path / "private",
        stabilization_seconds=7,
    )
    lifecycle.environment = SimpleNamespace(
        wait_healthy=lambda: events.append("ready")
    )
    lifecycle.bundle = object()
    lifecycle.admitted_resolved_sha256 = "a" * 64
    lifecycle.flag_file = tmp_path / "flag.json"
    lifecycle.baseline_document = {"baseline": True}
    lifecycle.fault_document = {"fault": True}
    backend = FakeReadBackend.healthy()
    capability = SimpleNamespace(
        resolved_sandbox=SimpleNamespace(endpoints=object())
    )

    def issue(**kwargs: object) -> object:
        assert kwargs["environment"] is lifecycle.environment
        assert kwargs["bundle"] is lifecycle.bundle
        assert kwargs["admitted_resolved_sha256"] == "a" * 64
        events.append("reauth")
        return capability

    monkeypatch.setattr(
        "ecomsre.dta_v2.read_only_smoke.time.sleep",
        lambda seconds: events.append(f"stabilize:{seconds}"),
    )
    monkeypatch.setattr(
        "ecomsre.dta_v2.read_only_smoke._issue_owned_read_capability", issue
    )
    monkeypatch.setattr(
        LocalSandboxReadBackend,
        "_from_owned_capability",
        staticmethod(lambda capability: backend),
    )
    monkeypatch.setattr(
        "ecomsre_live_sandbox.control.SandboxFaultController",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )

    lifecycle.wait_ready()
    issued = lifecycle.authorize_reads()
    assert issued.authority == backend.authority
    assert events == ["ready", "stabilize:7", "reauth"]


def test_concrete_sandbox_partial_start_attempts_cleanup_and_keeps_both_causes(
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class PartialEnvironment:
        _baseline_snapshot = object()

        def start(self) -> None:
            events.append("partial-start")
            raise RuntimeError("partial start")

        def cleanup(self, *, baseline_restored: bool):
            assert not baseline_restored
            events.append("cleanup")
            return SimpleNamespace(
                verdict="BLOCKED",
                owned_containers=0,
                owned_networks=0,
                owned_volumes=0,
                non_owned_resources_changed=False,
            )

    lifecycle = _SandboxOwnedSmokeLifecycle(
        repository_root=tmp_path,
        private_root=tmp_path / "private",
        stabilization_seconds=0,
    )
    lifecycle.environment = PartialEnvironment()
    lifecycle.admit = lambda: events.append("admit")  # type: ignore[method-assign]
    closure = run_owned_read_only_smoke_attempt(
        private_root=tmp_path / "attempt",
        smoke_id="d" * 32,
        service="payment",
        lifecycle=lifecycle,
    )
    assert closure.primary_failure_code.value == "START_FAILED"
    assert closure.cleanup_failure_code.value == "CLEANUP_BLOCKED"
    assert closure.cleanup_verdict == "BLOCKED"
    assert events == ["admit", "partial-start", "cleanup"]


def test_owned_capability_issuer_reauthenticates_exact_sandbox_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ecomsre_live_sandbox.contracts import (
        LocalEndpoints,
        ResolvedSandbox,
        load_bundle,
    )
    from ecomsre_live_sandbox.environment import (
        CommandResult,
        SandboxEnvironment,
    )

    class ReadOnlyRunner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def run(self, arguments, **kwargs):
            del kwargs
            self.calls.append(arguments)
            if arguments == ("docker", "context", "show"):
                output = "desktop-linux\n"
            elif arguments[:3] == ("docker", "context", "inspect"):
                output = '"unix:///var/run/docker.sock"\n'
            elif arguments[:2] == ("docker", "info"):
                output = (
                    '{"OSType":"linux","Architecture":"arm64",'
                    '"ID":"fresh-daemon"}\n'
                )
            else:
                raise AssertionError(arguments)
            return CommandResult(arguments=arguments, stdout=output, stderr="")

    root = Path(__file__).resolve().parents[2]
    bundle = load_bundle(root / "config/live-telemetry-controlled-remediation-v1")
    runner = ReadOnlyRunner()
    environment = SandboxEnvironment(
        repository_root=root,
        bundle=bundle,
        flagd_directory=tmp_path,
        runner=runner,
    )
    resolved = ResolvedSandbox(
        compose_sha256="e" * 64,
        services=(),
        image_references=(),
        endpoints=LocalEndpoints(
            frontend="http://127.0.0.1:18080",
            flag_control="http://127.0.0.1:18080/feature/api",
            flag_evaluation="http://127.0.0.1:18016",
            prometheus="http://127.0.0.1:19090",
            opensearch="http://127.0.0.1:19200",
            jaeger="http://127.0.0.1:11686",
        ),
    )
    resolve_calls: list[str] = []

    def fresh_resolve():
        resolve_calls.append("resolve")
        return resolved, {"fresh": True}

    monkeypatch.setattr(environment, "resolve", fresh_resolve)
    environment.verify_local_docker()
    admitted_call_count = len(runner.calls)
    capability = _issue_owned_read_capability(
        environment=environment,
        bundle=bundle,
        admitted_resolved_sha256=semantic_sha256(resolved.model_dump(mode="json")),
        timeout_seconds=1.0,
    )
    backend = LocalSandboxReadBackend._from_owned_capability(capability)
    assert len(runner.calls) == admitted_call_count + 3
    assert resolve_calls == ["resolve"]
    assert backend.authority.mode is ReadAuthorityMode.OWNED_LOCAL
    assert backend.authority == capability.config.authority
    drifted = resolved.model_copy(update={"compose_sha256": "f" * 64})
    monkeypatch.setattr(
        environment,
        "resolve",
        lambda: (drifted, {"fresh": False}),
    )
    with pytest.raises(ValueError, match="resolved.*drift"):
        _issue_owned_read_capability(
            environment=environment,
            bundle=bundle,
            admitted_resolved_sha256=semantic_sha256(
                resolved.model_dump(mode="json")
            ),
            timeout_seconds=1.0,
        )


def test_owned_capability_issuer_accepts_no_caller_resolved_object() -> None:
    assert "resolved" not in inspect.signature(
        _issue_owned_read_capability
    ).parameters
