from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

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
from ecomsre.evidence.hashes import canonical_json_sha256, sha256_bytes
from ecomsre.evidence.store import ObserverEvidenceStore
from ecomsre.phase0.models import MeasurementPhase
from ecomsre.telemetry.http import HttpExchange, HttpReason, HttpRequest, PhaseWindow
from ecomsre.telemetry.probe import (
    CurrentResourceDiscovery,
    ProbeAdapter,
    ReadinessGate,
    ReadinessGateName,
    ServiceReadinessProof,
    build_ownership_discovery_invocations,
    build_readiness_handoff,
    evaluate_backend_readiness,
    execute_lifecycle_readiness,
    load_current_resource_discovery,
    load_service_readiness_proof,
)
from ecomsre.telemetry.prometheus import (
    TestTelemetryQueryCapability as SyntheticTelemetryCapability,
    _load_test_query_registry,
)
from telemetry_promotion_support import issue_strict_frozen_test_capability


RUN_ID = "5" * 32
NOW = datetime(2026, 7, 30, 1, 2, 3, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "telemetry"


class ProbeFixtureClient:
    run_id = RUN_ID

    def __init__(
        self,
        body: bytes,
        *,
        observed_at: datetime | None = None,
        monotonic_ended: float = 2.0,
    ) -> None:
        self.body = body
        self.observed_at = observed_at or NOW + timedelta(seconds=2)
        self.monotonic_ended = monotonic_ended

    def request(self, request: HttpRequest) -> HttpExchange:
        return HttpExchange(
            reason=HttpReason.OK,
            request=request,
            started_at=self.observed_at - timedelta(seconds=1),
            ended_at=self.observed_at,
            monotonic_started_at=self.monotonic_ended - 1.0,
            monotonic_ended_at=self.monotonic_ended,
            status_code=200,
            response_headers=(("Content-Type", "application/json"),),
            raw_body=self.body,
            raw_sha256=sha256_bytes(self.body),
            raw_body_partial=False,
        )


def _resource(service: str) -> OwnedResource:
    labels = {
        PROJECT_LABEL: PROJECT_NAMESPACE,
        RUN_LABEL: RUN_ID,
        "com.docker.compose.project": PROJECT_NAMESPACE,
        "com.docker.compose.service": service,
    }
    return OwnedResource(
        kind="container",
        name=f"ecomsre-phase0-{service}",
        resource_id=f"container-{service}",
        labels=labels,
        identity_evidence=(
            f"container:container-{service}",
            f"container_name:ecomsre-phase0-{service}",
            f"service:{service}",
        ),
    )


def _load_generator_port() -> OwnedResource:
    labels = {
        PROJECT_LABEL: PROJECT_NAMESPACE,
        RUN_LABEL: RUN_ID,
        "com.docker.compose.project": PROJECT_NAMESPACE,
        "com.docker.compose.service": "load-generator",
    }
    binding = {
        "service": "load-generator",
        "container_name": "ecomsre-phase0-load-generator",
        "container_id": "container-load-generator",
        "host_ip": "127.0.0.1",
        "host_family": "ipv4",
        "published_port": 32774,
        "target_port": 8080,
        "protocol": "tcp",
    }
    resource_id = f"port-binding:{canonical_json_sha256(binding)}"
    return OwnedResource(
        kind="port",
        name="load-generator:8080->32774/tcp@ipv4",
        resource_id=resource_id,
        labels=labels,
        identity_evidence=(
            f"port:{resource_id}",
            "container:container-load-generator",
            "container_name:ecomsre-phase0-load-generator",
            "service:load-generator",
            "host_ip:127.0.0.1",
            "host_family:ipv4",
            "published_port:32774",
            "target_port:8080",
            "protocol:tcp",
            "binding:127.0.0.1:32774->8080/tcp",
            "raw_binding:127.0.0.1:32774->8080/tcp",
        ),
    )


def _context(tmp_path: Path):
    manifest = OwnershipManifest(
        run_id=RUN_ID,
        resources=(
            _resource("load-generator"),
            _resource("otel-collector"),
            _load_generator_port(),
        ),
    )
    create_ownership_authority_artifacts(tmp_path, manifest, created_at=NOW)
    return load_authenticated_ownership_context(tmp_path, RUN_ID)


def _window() -> PhaseWindow:
    return PhaseWindow(
        run_id=RUN_ID,
        cycle_number=1,
        scenario_phase=MeasurementPhase.BASELINE,
        utc_started_at=NOW,
        utc_ended_at=NOW + timedelta(seconds=30),
        monotonic_started_at=0.0,
        monotonic_ended_at=30.0,
    )


def _phase_window(phase: MeasurementPhase, offset: int) -> PhaseWindow:
    return PhaseWindow(
        run_id=RUN_ID,
        cycle_number=1,
        scenario_phase=phase,
        utc_started_at=NOW + timedelta(seconds=offset),
        utc_ended_at=NOW + timedelta(seconds=offset + 30),
        monotonic_started_at=float(offset),
        monotonic_ended_at=float(offset + 30),
    )


def _frozen_capability(
    store: ObserverEvidenceStore,
) -> SyntheticTelemetryCapability:
    _payload, capability = issue_strict_frozen_test_capability(
        store,
        run_id=RUN_ID,
        fixture_path=FIXTURES / "frozen-query-registry.json",
    )
    return capability


def _discovery(context, store: ObserverEvidenceStore):
    docker_endpoint = "unix:///var/run/docker.sock"
    invocations = build_ownership_discovery_invocations(
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=docker_endpoint,
    )
    container_stdout = "\n".join(
        json.dumps(
            {
                "ID": resource.resource_id,
                "Names": resource.name,
                "Labels": ",".join(
                    f"{key}={value}" for key, value in sorted(resource.labels.items())
                ),
                "Ports": (
                    "127.0.0.1:32774->8080/tcp"
                    if resource.labels["com.docker.compose.service"] == "load-generator"
                    else ""
                ),
            },
            sort_keys=True,
        )
        for resource in context.manifest.resources
        if resource.kind == "container"
    )
    command_artifacts = {}
    for invocation in invocations:
        stdout = container_stdout if invocation.purpose.endswith("containers") else ""
        command_artifacts[invocation.purpose] = _command_artifact(
            store,
            purpose=invocation.purpose,
            arguments=invocation.arguments,
            stdout=stdout,
        )
    payload = {
        "schema_version": "phase0.current-resource-discovery-index.v1",
        "run_id": RUN_ID,
        "ownership_manifest_sha256": context.manifest_sha256,
        "docker_endpoint": docker_endpoint,
        "command_artifacts": command_artifacts,
    }
    artifact = store.write_immutable(
        "lifecycle/current-resource-discovery.json",
        payload,
    )
    return load_current_resource_discovery(
        context,
        store,
        artifact_path=str(artifact.path),
        artifact_sha256=artifact.sha256,
    )


def _service_proof(
    context,
    store: ObserverEvidenceStore,
    *,
    service: str,
    source: str,
):
    status = _command_artifact(
        store,
        purpose=f"{service}_status",
        arguments=(
            "docker",
            "inspect",
            "--format",
            "{{json .State}}",
            service,
        ),
        stdout=json.dumps(
            {
                "run_id": RUN_ID,
                "service": service,
                "running": True,
                "health_status": "healthy",
            },
            sort_keys=True,
        ),
    )
    signal_arguments = {
        "load_generator_contract": ("GET", "/load-generator/ready"),
        "emitted_traffic": ("QUERY", "load-generator-emitted-traffic"),
        "pipeline_ingestion": ("QUERY", "collector-pipeline-ingestion"),
    }[source]
    signal_payload = {
        "run_id": RUN_ID,
        "service": service,
        **(
            {
                "getads_requests_emitted": 200,
                "readiness_contract": True,
            }
            if service == "load-generator"
            else {
                "pipeline": "traces->spanmetrics",
                "ingested_records": 200,
            }
        ),
    }
    signal = _command_artifact(
        store,
        purpose=f"{service}_{source}",
        arguments=signal_arguments,
        stdout=json.dumps(signal_payload, sort_keys=True),
    )
    payload = {
        "schema_version": "phase0.service-readiness-index.v1",
        "run_id": RUN_ID,
        "ownership_manifest_sha256": context.manifest_sha256,
        "service": service,
        "source": source,
        "status_artifact": status,
        "signal_artifact": signal,
    }
    artifact = store.write_immutable(
        f"lifecycle/{service}-readiness.json",
        payload,
    )
    return load_service_readiness_proof(
        context,
        store,
        artifact_path=str(artifact.path),
        artifact_sha256=artifact.sha256,
    )


def _command_artifact(
    store: ObserverEvidenceStore,
    *,
    purpose: str,
    arguments: tuple[str, ...],
    stdout: str,
) -> dict[str, str]:
    stdout_bytes = stdout.encode()
    artifact = store.write_immutable(
        f"lifecycle/raw/{purpose}.json",
        {
            "schema_version": "phase0.readiness-command-result.v1",
            "run_id": RUN_ID,
            "purpose": purpose,
            "arguments": list(arguments),
            "exit_code": 0,
            "stdout_base64": base64.b64encode(stdout_bytes).decode("ascii"),
            "stdout_sha256": sha256_bytes(stdout_bytes),
            "stderr_base64": "",
            "stderr_sha256": sha256_bytes(b""),
        },
    )
    return {"path": str(artifact.path), "sha256": artifact.sha256}


def _backend_gate(
    name: ReadinessGateName,
    *,
    context,
    store: ObserverEvidenceStore,
    capability: SyntheticTelemetryCapability,
    run_id: str = RUN_ID,
    artifact_exists: bool = True,
):
    window = _window()
    artifact_paths: tuple[str, ...] = ()
    hashes: tuple[tuple[str, str], ...] = ()
    if artifact_exists:
        backend = name.value.removesuffix("_fresh")
        prefix = f"cycles/001/baseline/telemetry/{backend}"
        common = {
            "run_id": RUN_ID,
            "cycle_number": 1,
            "scenario_phase": "baseline",
            "fixture_sha256": capability.content_sha256,
            "backend": backend,
        }
        body = b'{"status":"success"}'
        raw = store.write_immutable(
            f"{prefix}/response-raw-{run_id[:2]}.json",
            {
                **common,
                "schema_version": "phase0.telemetry-raw.v1",
                "started_at": NOW.isoformat(),
                "ended_at": (NOW + timedelta(seconds=1)).isoformat(),
                "monotonic_started_at": 1.0,
                "monotonic_ended_at": 2.0,
                "http_status": 200,
                "http_reason": "OK",
                "raw_response_base64": base64.b64encode(body).decode("ascii"),
                "raw_response_sha256": sha256_bytes(body),
                "raw_response_partial": False,
            },
        )
        artifacts = [raw]
        if name is ReadinessGateName.PROMETHEUS_FRESH:
            parsed = store.write_immutable(
                f"{prefix}/response-decision-{run_id[:2]}.json",
                {
                    **common,
                    "schema_version": "phase0.telemetry-parse-decision.v1",
                    "raw_response_artifact": str(raw.path),
                    "decision": True,
                    "reason": "READY",
                },
            )
            artifacts.append(parsed)
            terminal_extra = {
                "raw_and_parse_artifacts": [
                    str(raw.path),
                    str(parsed.path),
                ]
            }
            terminal_schema = "phase0.prometheus-measurement-decision.v1"
        else:
            terminal_extra = {"raw_response_artifact": str(raw.path)}
            terminal_schema = "phase0.telemetry-gate-decision.v1"
        terminal = store.write_immutable(
            f"{prefix}/terminal-{run_id[:2]}.json",
            {
                **common,
                **terminal_extra,
                "schema_version": terminal_schema,
                "decision": True,
                "reason": "READY",
            },
        )
        artifacts.append(terminal)
        artifact_paths = tuple(str(artifact.path) for artifact in artifacts)
        hashes = tuple((str(artifact.path), artifact.sha256) for artifact in artifacts)
    result = SimpleNamespace(
        ready=True,
        reason=SimpleNamespace(value="READY"),
        run_id=run_id,
        cycle_number=1,
        phase="baseline",
        fixture_sha256=capability.content_sha256,
        artifact_paths=artifact_paths,
        artifact_sha256=hashes,
    )
    return evaluate_backend_readiness(
        name,
        window,
        result,
        context=context,
        registry_capability=capability,
        evidence_store=store,
    )


def test_readiness_provenance_types_cannot_be_publicly_self_attested() -> None:
    with pytest.raises(TypeError):
        CurrentResourceDiscovery(
            run_id=RUN_ID,
            complete_no_trunc=True,
            resources=(),
            evidence_artifact="fake.json",
            evidence_sha256="a" * 64,
        )
    with pytest.raises(TypeError):
        ServiceReadinessProof(
            run_id=RUN_ID,
            service="load-generator",
            running=True,
            healthy=True,
            attributable_current_run_evidence=True,
            source="storefront_probe",
            evidence_artifact="fake.json",
            evidence_sha256="a" * 64,
        )
    with pytest.raises(TypeError):
        ReadinessGate(
            run_id=RUN_ID,
            name=ReadinessGateName.PROMETHEUS_FRESH,
            passed=True,
            reason="SELF_ATTESTED",
            cycle_number=1,
            phase="baseline",
            fixture_sha256="a" * 64,
            ownership_manifest_sha256="b" * 64,
            evidence_sha256=(("fake.json", "c" * 64),),
            store_root=Path("."),
        )


def test_test_only_capability_cannot_authorize_any_production_readiness_gate(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    with ObserverEvidenceStore(tmp_path, RUN_ID) as store:
        capability = _frozen_capability(store)
        backends = tuple(
            _backend_gate(
                name,
                context=context,
                store=store,
                capability=capability,
            )
            for name in (
                ReadinessGateName.PROMETHEUS_FRESH,
                ReadinessGateName.JAEGER_FRESH,
                ReadinessGateName.OPENSEARCH_FRESH,
            )
        )

        handoff = build_readiness_handoff(
            context=context,
            gates=backends,
            evidence_store=store,
            registry_capability=capability,
        )

        assert not handoff.ready
        assert handoff.evidence is None
        assert handoff.reason == "QUERY_FIXTURE_NOT_FROZEN"
        assert not any(gate.passed for gate in backends)


def test_discovery_and_service_loaders_reject_wrong_command_hash_and_source(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    with ObserverEvidenceStore(tmp_path, RUN_ID) as store:
        bad_discovery = store.write_immutable(
            "lifecycle/bad-discovery.json",
            {
                "schema_version": "phase0.current-resource-discovery.v1",
                "run_id": RUN_ID,
                "ownership_manifest_sha256": context.manifest_sha256,
                "label_filters": {
                    PROJECT_LABEL: PROJECT_NAMESPACE,
                    RUN_LABEL: RUN_ID,
                },
                "sanitized_command": ["docker", "ps"],
                "command_exit_code": 0,
                "complete_no_trunc": True,
                "resources": [],
            },
        )
        with pytest.raises(TypeError, match="lifecycle execution receipt"):
            load_current_resource_discovery(
                context,
                store,
                artifact_path=str(bad_discovery.path),
                artifact_sha256=bad_discovery.sha256,
            )
        with pytest.raises(TypeError, match="lifecycle execution receipt"):
            load_current_resource_discovery(
                context,
                store,
                artifact_path=str(bad_discovery.path),
                artifact_sha256="0" * 64,
            )

        status = _command_artifact(
            store,
            purpose="load-generator_status",
            arguments=(
                "docker",
                "inspect",
                "--format",
                "{{json .State}}",
                "load-generator",
            ),
            stdout=json.dumps(
                {
                    "run_id": RUN_ID,
                    "service": "load-generator",
                    "running": True,
                    "health_status": "healthy",
                },
                sort_keys=True,
            ),
        )
        bad_service = store.write_immutable(
            "lifecycle/storefront-substitute.json",
            {
                "schema_version": "phase0.service-readiness-index.v1",
                "run_id": RUN_ID,
                "ownership_manifest_sha256": context.manifest_sha256,
                "service": "load-generator",
                "source": "storefront_probe",
                "status_artifact": status,
                "signal_artifact": status,
            },
        )
        with pytest.raises(TypeError, match="lifecycle execution receipt"):
            load_service_readiness_proof(
                context,
                store,
                artifact_path=str(bad_service.path),
                artifact_sha256=bad_service.sha256,
            )


def test_backend_gate_rejects_old_run_or_missing_artifact_and_three_are_incomplete(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    with ObserverEvidenceStore(tmp_path, RUN_ID) as store:
        capability = _frozen_capability(store)
        old_run = _backend_gate(
            ReadinessGateName.PROMETHEUS_FRESH,
            context=context,
            store=store,
            capability=capability,
            run_id="6" * 32,
        )
        missing = _backend_gate(
            ReadinessGateName.JAEGER_FRESH,
            context=context,
            store=store,
            capability=capability,
            artifact_exists=False,
        )
        assert not old_run.passed
        assert old_run.reason == "BACKEND_PROVENANCE_MISMATCH"
        assert not missing.passed
        assert missing.reason == "BACKEND_PROVENANCE_MISMATCH"
        object.__setattr__(old_run, "passed", True)
        forged = build_readiness_handoff(
            context=context,
            gates=(old_run,),
            evidence_store=store,
            registry_capability=capability,
        )
        assert not forged.ready
        assert forged.evidence is None
        assert forged.reason == "QUERY_FIXTURE_NOT_FROZEN"

        backend_only = tuple(
            _backend_gate(
                name,
                context=context,
                store=store,
                capability=capability,
            )
            for name in (
                ReadinessGateName.PROMETHEUS_FRESH,
                ReadinessGateName.JAEGER_FRESH,
                ReadinessGateName.OPENSEARCH_FRESH,
            )
        )
        handoff = build_readiness_handoff(
            context=context,
            gates=backend_only,
            evidence_store=store,
            registry_capability=capability,
        )
        assert not handoff.ready
        assert handoff.evidence is None
        assert handoff.reason == "QUERY_FIXTURE_NOT_FROZEN"


def test_backend_gate_rejects_hashed_terminal_only_ready_self_attestation(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    with ObserverEvidenceStore(tmp_path, RUN_ID) as store:
        capability = _frozen_capability(store)
        terminal = store.write_immutable(
            "cycles/001/baseline/telemetry/prometheus/forged-terminal.json",
            {
                "schema_version": "phase0.prometheus-measurement-decision.v1",
                "run_id": RUN_ID,
                "cycle_number": 1,
                "scenario_phase": "baseline",
                "fixture_sha256": capability.content_sha256,
                "backend": "prometheus",
                "decision": True,
                "reason": "READY",
                "raw_and_parse_artifacts": [],
            },
        )
        result = SimpleNamespace(
            ready=True,
            reason=SimpleNamespace(value="READY"),
            run_id=RUN_ID,
            cycle_number=1,
            phase="baseline",
            fixture_sha256=capability.content_sha256,
            artifact_paths=(str(terminal.path),),
            artifact_sha256=((str(terminal.path), terminal.sha256),),
        )

        gate = evaluate_backend_readiness(
            ReadinessGateName.PROMETHEUS_FRESH,
            _window(),
            result,
            context=context,
            registry_capability=capability,
            evidence_store=store,
        )

        assert not gate.passed
        assert gate.reason == "BACKEND_PROVENANCE_MISMATCH"
        assert gate.evidence_sha256 == ()


def test_readiness_persistence_failure_returns_no_usable_evidence(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    with ObserverEvidenceStore(tmp_path, RUN_ID) as store:
        capability = _frozen_capability(store)
        store.write_immutable(
            "lifecycle/readiness-evidence.json",
            {"run_id": RUN_ID, "occupied": True},
        )
        handoff = build_readiness_handoff(
            context=context,
            gates=(),
            evidence_store=store,
            registry_capability=capability,
        )
        assert not handoff.ready
        assert handoff.evidence is None
        assert handoff.reason == "QUERY_FIXTURE_NOT_FROZEN"


def test_probe_fixture_cannot_use_test_capability_with_real_observer_store(
    tmp_path: Path,
) -> None:
    body = (FIXTURES / "probe-current.json").read_bytes()
    with ObserverEvidenceStore(tmp_path, RUN_ID) as store:
        registry = _frozen_capability(store)
        result = ProbeAdapter(
            client=ProbeFixtureClient(body),
            evidence_store=store,
            fixture=registry,
        ).observe(
            window=_window(),
            base_url="http://127.0.0.1:32774",
            artifact_prefix="cycles/01/baseline",
        )

    assert not result.observed
    assert result.reason.value == "QUERY_FIXTURE_NOT_FROZEN"
    assert result.artifact_paths == ()


def test_synthetic_frozen_fixture_cannot_authorize_real_transport_store(
    tmp_path: Path,
) -> None:
    synthetic = _load_test_query_registry(FIXTURES / "frozen-query-registry.json")
    with ObserverEvidenceStore(tmp_path, RUN_ID) as store:
        result = ProbeAdapter(
            client=ProbeFixtureClient((FIXTURES / "probe-current.json").read_bytes()),
            evidence_store=store,
            fixture=synthetic,
        ).observe(
            window=_window(),
            base_url="http://127.0.0.1:32774",
            artifact_prefix="cycles/001/baseline",
        )
    assert not result.observed
    assert result.reason.value == "QUERY_FIXTURE_NOT_FROZEN"


def test_probe_phase_coverage_rejects_test_only_receipts_even_with_exact_prefix(
    tmp_path: Path,
) -> None:
    body = (FIXTURES / "probe-current.json").read_bytes()
    with ObserverEvidenceStore(tmp_path, RUN_ID) as store:
        capability = _frozen_capability(store)
        observations = []
        adapter = None
        for phase, offset in (
            (MeasurementPhase.BASELINE, 0),
            (MeasurementPhase.FAULT, 40),
            (MeasurementPhase.RECOVERY, 80),
        ):
            adapter = ProbeAdapter(
                client=ProbeFixtureClient(
                    body,
                    observed_at=NOW + timedelta(seconds=offset + 2),
                    monotonic_ended=float(offset + 2),
                ),
                evidence_store=store,
                fixture=capability,
            )
            observations.append(
                adapter.observe(
                    window=_phase_window(phase, offset),
                    base_url="http://127.0.0.1:32774",
                    artifact_prefix=f"cycles/001/{phase.value}",
                )
            )
        assert adapter is not None
        coverage = adapter.validate_phase_coverage(
            observations=tuple(observations),
            artifact_prefix="cycles/001",
        )
        wrong_prefix = adapter.validate_phase_coverage(
            observations=tuple(observations),
            artifact_prefix="cycles/999",
        )

        assert not coverage.complete
        assert not wrong_prefix.complete


def test_fabricated_lifecycle_runner_cannot_issue_production_receipt() -> None:
    class FabricatedRunner:
        def run(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("fabricated runner must not execute")

    with pytest.raises(TypeError, match="AuthenticatedLifecycleRunner"):
        execute_lifecycle_readiness(
            FabricatedRunner(),  # type: ignore[arg-type]
            preflight=object(),  # type: ignore[arg-type]
            context=object(),  # type: ignore[arg-type]
        )
