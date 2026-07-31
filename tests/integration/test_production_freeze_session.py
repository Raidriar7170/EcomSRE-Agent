from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import ecomsre.telemetry.probe as probe_module
from ecomsre.environment.manifests import LockMatchChecks, LockVerification
from ecomsre.environment.ownership import (
    PROJECT_LABEL,
    PROJECT_NAMESPACE,
    RUN_LABEL,
    OwnedResource,
    OwnershipManifest,
)
from ecomsre.environment.ownership_authority import (
    create_ownership_authority_artifacts,
    load_authenticated_ownership_context,
)
from ecomsre.environment.preflight import (
    DockerSnapshot,
    HostSnapshot,
    PortObservation,
    PreflightInputs,
    issue_authenticated_preflight_evidence,
)
from ecomsre.evidence.hashes import canonical_json_bytes, canonical_json_sha256
from ecomsre.evidence.store import ObserverEvidenceStore
from ecomsre.phase0.models import MeasurementPhase, Outcome
from ecomsre.telemetry.http import OwnedHttpClient, PhaseWindow
from ecomsre.telemetry.probe import (
    AuthenticatedLifecycleRunner,
    execute_lifecycle_readiness,
)
from ecomsre.telemetry.prometheus import (
    discover_and_freeze_registry,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "telemetry"
RUN_ID = "a" * 32
START = datetime(2026, 7, 30, 1, 2, 0, tzinfo=UTC)
PORTS = {
    "prometheus": (32771, 9090),
    "jaeger": (32772, 16686),
    "opensearch": (32773, 9200),
    "frontend-proxy": (32774, 8080),
}


class _Response:
    status = 200

    def __init__(self, body: bytes) -> None:
        self._body = body
        self._sent = False

    def getheaders(self):
        return [("Content-Type", "application/json")]

    def read(self, _size: int) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return self._body


class _Connection:
    def __init__(self, body: bytes) -> None:
        self.response = _Response(body)
        self.sock = SimpleNamespace(settimeout=lambda _timeout: None)
        self.timeout = None

    def connect(self) -> None:
        return None

    def request(self, _method, _target, _body, _headers) -> None:
        return None

    def getresponse(self) -> _Response:
        return self.response

    def close(self) -> None:
        return None


class _Factory:
    def __init__(self, bodies: list[bytes]) -> None:
        self._bodies = iter(bodies)

    def __call__(self, _host: str, _port: int, _timeout: float) -> _Connection:
        return _Connection(next(self._bodies))


def _resource_pair(service: str) -> tuple[OwnedResource, OwnedResource]:
    published, target = PORTS[service]
    container_id = f"container-{service}"
    container_name = f"ecomsre-phase0-{service}"
    labels = {
        PROJECT_LABEL: PROJECT_NAMESPACE,
        RUN_LABEL: RUN_ID,
        "com.docker.compose.project": PROJECT_NAMESPACE,
        "com.docker.compose.service": service,
    }
    binding = {
        "service": service,
        "container_name": container_name,
        "container_id": container_id,
        "host_ip": "127.0.0.1",
        "host_family": "ipv4",
        "published_port": published,
        "target_port": target,
        "protocol": "tcp",
    }
    port_id = f"port-binding:{canonical_json_sha256(binding)}"
    return (
        OwnedResource(
            kind="container",
            name=container_name,
            resource_id=container_id,
            labels=labels,
            identity_evidence=(
                f"container:{container_id}",
                f"container_name:{container_name}",
                f"service:{service}",
            ),
        ),
        OwnedResource(
            kind="port",
            name=f"{service}:{target}->{published}/tcp@ipv4",
            resource_id=port_id,
            labels=labels,
            identity_evidence=(
                f"port:{port_id}",
                f"container:{container_id}",
                f"container_name:{container_name}",
                f"service:{service}",
                "host_ip:127.0.0.1",
                "host_family:ipv4",
                f"published_port:{published}",
                f"target_port:{target}",
                "protocol:tcp",
                f"binding:127.0.0.1:{published}->{target}/tcp",
                f"raw_binding:127.0.0.1:{published}->{target}/tcp",
            ),
        ),
    )


def _context(tmp_path: Path):
    resources = tuple(
        resource for service in PORTS for resource in _resource_pair(service)
    )
    create_ownership_authority_artifacts(
        tmp_path,
        OwnershipManifest(run_id=RUN_ID, resources=resources),
        created_at=START,
    )
    return load_authenticated_ownership_context(tmp_path, RUN_ID)


def _preflight():
    monotonic_now = time.monotonic_ns()
    compose_hash = "a" * 64
    inputs = PreflightInputs(
        host=HostSnapshot(
            macos_version="26.5.2",
            macos_build="25F84",
            architecture="arm64",
            cpu_model="Apple M5 Pro",
            cpu_count=12,
            total_memory_bytes=48 * 1024**3,
            available_memory_bytes=32 * 1024**3,
            available_disk_bytes=100 * 1024**3,
        ),
        docker=DockerSnapshot(
            client_available=True,
            client_version="29.6.1",
            daemon_available=True,
            server_version="29.6.1",
            desktop_version="4.50.0",
            engine="Docker Desktop",
            desktop_identity_verified=True,
            compose_available=True,
            compose_version="v5.3.0",
            compose_plugin_v2=True,
            server_os_type="linux",
            server_architecture="arm64",
            native_platform="linux/arm64",
            cpu_count=12,
            memory_bytes=24 * 1024**3,
            disk_bytes=100 * 1024**3,
            resource_fields_verified=True,
            context_name="desktop-linux",
            endpoint="unix:///var/run/docker.sock",
            daemon_id="fixture-daemon-id",
        ),
        ports=(PortObservation(port=8080, occupied=False, ownership="NONE"),),
        resources=(),
        ownership_context=None,
        observed_upstream_commit=("1755859a9de82c2e5e225be68abc401a5ebf2b4f"),
        runtime_compose_instance_sha256=compose_hash,
        observed_canonical_compose_contract_sha256=compose_hash,
        expected_canonical_compose_contract_sha256=compose_hash,
        compose_canonicalization_schema_version=(
            "phase0.compose-canonicalization.v1"
        ),
        image_lock_verification=LockVerification(
            passed=True,
            outcome=Outcome.SUCCESS,
            reason_codes=(),
            checks=LockMatchChecks.all_passed(),
        ),
        pull_policy="never",
    )
    return issue_authenticated_preflight_evidence(
        run_id=RUN_ID,
        inputs=inputs,
        collected_at=datetime.now(UTC),
        monotonic_started_ns=monotonic_now - 1_000,
        monotonic_finished_ns=monotonic_now,
    )


def _vector(labels: list[dict[str, str]], value: str) -> bytes:
    return canonical_json_bytes(
        {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {
                        "metric": identity,
                        "value": [(START + timedelta(seconds=3)).timestamp(), value],
                    }
                    for identity in labels
                ],
            },
        }
    )


def _windows() -> tuple[PhaseWindow, PhaseWindow, PhaseWindow]:
    return tuple(
        PhaseWindow(
            run_id=RUN_ID,
            cycle_number=1,
            scenario_phase=phase,
            utc_started_at=START + timedelta(seconds=offset),
            utc_ended_at=START + timedelta(seconds=offset + 30),
            monotonic_started_at=float(offset),
            monotonic_ended_at=float(offset + 30),
        )
        for phase, offset in (
            (MeasurementPhase.BASELINE, 0),
            (MeasurementPhase.FAULT, 40),
            (MeasurementPhase.RECOVERY, 80),
        )
    )


def test_injected_transport_cannot_issue_production_seal(
    tmp_path: Path,
) -> None:
    fixture = json.loads(
        (FIXTURES / "frozen-query-registry.json").read_text(encoding="utf-8")
    )
    prometheus = fixture["prometheus"]
    total_labels = [series["labels"] for series in prometheus["expected_total_series"]]
    error_labels = [
        labels
        for labels in total_labels
        if labels["status_code"] == "STATUS_CODE_ERROR"
    ]
    incarnation_labels = [prometheus["expected_target_incarnation_series"]["labels"]]
    bodies = [
        _vector(total_labels, "10"),
        _vector(error_labels, "0"),
        _vector(incarnation_labels, "1000"),
        (FIXTURES / "jaeger-current.json").read_bytes(),
        (FIXTURES / "opensearch-current.json").read_bytes(),
        *((FIXTURES / "probe-current.json").read_bytes() for _ in range(3)),
    ]
    context = _context(tmp_path)
    utc_calls = 0

    def utc_now() -> datetime:
        nonlocal utc_calls
        request_index = utc_calls // 2
        is_end = utc_calls % 2
        utc_calls += 1
        offsets = (1, 2, 3, 4, 5, 6, 41, 81)
        return START + timedelta(seconds=offsets[request_index] + is_end * 0.1)

    monotonic_calls: dict[int, int] = {}

    def monotonic() -> float:
        request_index = max(0, (utc_calls - 1) // 2)
        monotonic_calls[request_index] = monotonic_calls.get(request_index, 0) + 1
        offsets = (1, 2, 3, 4, 5, 6, 41, 81)
        return offsets[request_index] + monotonic_calls[request_index] * 0.01

    client = OwnedHttpClient(
        context=context,
        connection_factory=_Factory(bodies),
        monotonic=monotonic,
        utc_now=utc_now,
    )
    with ObserverEvidenceStore(tmp_path, RUN_ID) as store:
        with pytest.raises(TypeError, match="injected test transport"):
            discover_and_freeze_registry(
                fixture,
                evidence_store=store,
                client=client,
                windows=_windows(),
                base_urls={
                    service: f"http://127.0.0.1:{published}"
                    for service, (published, _target) in PORTS.items()
                    if service != "frontend-proxy"
                }
                | {"probe": "http://127.0.0.1:32774"},
            )


def _base_urls() -> dict[str, str]:
    return {
        service: f"http://127.0.0.1:{published}"
        for service, (published, _target) in PORTS.items()
        if service != "frontend-proxy"
    } | {"probe": "http://127.0.0.1:32774"}


def test_owned_http_client_instance_cannot_override_request(tmp_path: Path) -> None:
    client = OwnedHttpClient(context=_context(tmp_path))

    with pytest.raises(AttributeError):
        client.request = lambda _request: None  # type: ignore[method-assign]


def test_production_seal_rejects_class_method_monkeypatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = json.loads(
        (FIXTURES / "frozen-query-registry.json").read_text(encoding="utf-8")
    )
    client = OwnedHttpClient(context=_context(tmp_path))

    def fabricated_request(_self: OwnedHttpClient, _request: object) -> object:
        raise AssertionError("monkeypatched transport must not run")

    monkeypatch.setattr(OwnedHttpClient, "request", fabricated_request)
    with ObserverEvidenceStore(tmp_path, RUN_ID) as store:
        with pytest.raises(TypeError, match="method integrity"):
            discover_and_freeze_registry(
                fixture,
                evidence_store=store,
                client=client,
                windows=_windows(),
                base_urls=_base_urls(),
            )


@pytest.mark.parametrize(
    "windows",
    [
        (
            PhaseWindow(
                run_id=RUN_ID,
                cycle_number=1,
                scenario_phase=MeasurementPhase.BASELINE,
                utc_started_at=START,
                utc_ended_at=START + timedelta(seconds=30),
                monotonic_started_at=0,
                monotonic_ended_at=30,
            ),
            PhaseWindow(
                run_id=RUN_ID,
                cycle_number=1,
                scenario_phase=MeasurementPhase.FAULT,
                utc_started_at=START,
                utc_ended_at=START + timedelta(seconds=30),
                monotonic_started_at=0,
                monotonic_ended_at=30,
            ),
            PhaseWindow(
                run_id=RUN_ID,
                cycle_number=1,
                scenario_phase=MeasurementPhase.RECOVERY,
                utc_started_at=START,
                utc_ended_at=START + timedelta(seconds=30),
                monotonic_started_at=0,
                monotonic_ended_at=30,
            ),
        ),
        (
            PhaseWindow(
                run_id=RUN_ID,
                cycle_number=1,
                scenario_phase=MeasurementPhase.BASELINE,
                utc_started_at=START,
                utc_ended_at=START + timedelta(seconds=30),
                monotonic_started_at=0,
                monotonic_ended_at=30,
            ),
            PhaseWindow(
                run_id=RUN_ID,
                cycle_number=2,
                scenario_phase=MeasurementPhase.FAULT,
                utc_started_at=START + timedelta(seconds=40),
                utc_ended_at=START + timedelta(seconds=70),
                monotonic_started_at=40,
                monotonic_ended_at=70,
            ),
            PhaseWindow(
                run_id=RUN_ID,
                cycle_number=3,
                scenario_phase=MeasurementPhase.RECOVERY,
                utc_started_at=START + timedelta(seconds=80),
                utc_ended_at=START + timedelta(seconds=110),
                monotonic_started_at=80,
                monotonic_ended_at=110,
            ),
        ),
    ],
)
def test_production_seal_rejects_nonsequential_phase_windows(
    tmp_path: Path,
    windows: tuple[PhaseWindow, PhaseWindow, PhaseWindow],
) -> None:
    fixture = json.loads(
        (FIXTURES / "frozen-query-registry.json").read_text(encoding="utf-8")
    )
    client = OwnedHttpClient(context=_context(tmp_path))

    with ObserverEvidenceStore(tmp_path, RUN_ID) as store:
        with pytest.raises(ValueError, match="strictly ordered"):
            discover_and_freeze_registry(
                fixture,
                evidence_store=store,
                client=client,
                windows=windows,
                base_urls=_base_urls(),
            )


def test_authenticated_lifecycle_runner_is_slotted_and_authority_issued(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    factory = getattr(probe_module, "create_authenticated_lifecycle_runner", None)
    assert callable(factory)
    runner = factory(
        preflight=_preflight(),
        context=context,
    )

    assert type(runner) is AuthenticatedLifecycleRunner
    assert not hasattr(runner, "__dict__")
    with pytest.raises((AttributeError, TypeError)):
        runner.run = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    with pytest.raises(TypeError, match="locked preflight"):
        AuthenticatedLifecycleRunner()


def test_lifecycle_execution_rejects_runner_class_monkeypatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    preflight = _preflight()
    factory = getattr(probe_module, "create_authenticated_lifecycle_runner", None)
    assert callable(factory)
    runner = factory(
        preflight=preflight,
        context=context,
    )

    def fabricated_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("monkeypatched executor must not run")

    monkeypatch.setattr(AuthenticatedLifecycleRunner, "run", fabricated_run)
    with pytest.raises(TypeError, match="method integrity"):
        execute_lifecycle_readiness(
            runner,
            preflight=preflight,
            context=context,
        )


def test_lifecycle_execution_rejects_authenticator_monkeypatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    preflight = _preflight()
    factory = getattr(probe_module, "create_authenticated_lifecycle_runner", None)
    assert callable(factory)
    runner = factory(preflight=preflight, context=context)

    monkeypatch.setattr(
        AuthenticatedLifecycleRunner,
        "is_authentic",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        AuthenticatedLifecycleRunner,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("monkeypatched executor must not run")
        ),
    )
    with pytest.raises(TypeError, match="method integrity"):
        execute_lifecycle_readiness(
            runner,
            preflight=preflight,
            context=context,
        )


@pytest.mark.parametrize(
    ("function_name", "extra_arguments"),
    [
        (
            "acquire_load_generator_telemetry_receipt",
            {"jaeger_base_url": "http://127.0.0.1:32772"},
        ),
        (
            "acquire_collector_pipeline_receipt",
            {
                "context": object(),
                "execution": object(),
                "prometheus": object(),
                "jaeger": object(),
                "opensearch": object(),
            },
        ),
    ],
)
def test_specialized_receipt_acquisition_rejects_injected_transport(
    tmp_path: Path,
    function_name: str,
    extra_arguments: dict[str, object],
) -> None:
    acquire = getattr(probe_module, function_name, None)
    assert callable(acquire)
    client = OwnedHttpClient(
        context=_context(tmp_path),
        connection_factory=_Factory([]),
    )

    with pytest.raises(TypeError, match="production OwnedHttpClient"):
        acquire(
            client=client,
            evidence_store=object(),
            registry_capability=object(),
            window=_windows()[0],
            **extra_arguments,
        )
