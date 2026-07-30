"""Independent storefront probe and complete Task 7 readiness handoff."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlencode

from ecomsre.environment.lifecycle import (
    LifecycleRunner,
    ReadinessEvidence,
    _parse_owned_resources,
    build_ownership_discovery_invocations,
)
from ecomsre.environment.ownership import (
    PROJECT_LABEL,
    PROJECT_NAMESPACE,
    RUN_LABEL,
    OwnedResource,
    OwnershipError,
    verify_owned_resources,
)
from ecomsre.environment.ownership_authority import AuthenticatedOwnershipContext
from ecomsre.environment.preflight import (
    AuthenticatedPreflightEvidence,
    CommandResult,
    docker_host_prefix,
)
from ecomsre.evidence.hashes import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from ecomsre.evidence.store import ObserverEvidenceStore
from ecomsre.telemetry.http import (
    HttpExchange,
    HttpReason,
    HttpRequest,
    OwnedEndpoint,
    OwnedHttpClient,
    PhaseWindow,
    _owned_http_client_has_production_integrity,
)
from ecomsre.telemetry.prometheus import (
    FixtureState,
    FrozenTelemetryQueryCapability,
    RegistryAccess,
    _registry_access_is_frozen_for_adapter,
)


_RUN_ID_PATTERN = r"^[0-9a-f]{32}$"
_COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
_DISCOVERY_TOKEN = object()
_SERVICE_PROOF_TOKEN = object()
_READINESS_GATE_TOKEN = object()
_READINESS_INTEGRITY_KEY = secrets.token_bytes(32)
_PROBE_RECEIPT_TOKEN = object()
_LIFECYCLE_READINESS_TOKEN = object()
_PRODUCTION_DOCKER_RUNNER_TOKEN = object()
_AUTHENTICATED_LIFECYCLE_RUNNER_TOKEN = object()
_LOAD_GENERATOR_RECEIPT_TOKEN = object()
_COLLECTOR_PIPELINE_RECEIPT_TOKEN = object()
_SUBPROCESS_RUN = subprocess.run


@dataclass(frozen=True, slots=True, init=False)
class ProductionDockerRunner:
    """Exact subprocess executor locked to one local Docker daemon."""

    _run_id: str
    _docker_endpoint: str
    _daemon_id: str
    _token: object

    def __init__(
        self,
        *,
        _token: object | None = None,
        run_id: str = "",
        docker_endpoint: str = "",
        daemon_id: str = "",
    ) -> None:
        if _token is not _PRODUCTION_DOCKER_RUNNER_TOKEN:
            raise TypeError("ProductionDockerRunner must come from locked preflight")
        for name, value in {
            "_run_id": run_id,
            "_docker_endpoint": docker_endpoint,
            "_daemon_id": daemon_id,
            "_token": _PRODUCTION_DOCKER_RUNNER_TOKEN,
        }.items():
            object.__setattr__(self, name, value)

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
        environment: dict[str, str] | None = None,
    ) -> CommandResult:
        expected_environment = {"ECOMSRE_RUN_ID": self._run_id}
        if (
            not isinstance(arguments, tuple)
            or arguments[:3] != docker_host_prefix(self._docker_endpoint)
            or timeout_seconds != 30
            or environment != expected_environment
        ):
            raise ValueError("production Docker invocation is not exact")
        clean_environment = {
            "PATH": os.defpath,
            "LANG": "C",
            "LC_ALL": "C",
            **expected_environment,
        }
        completed = _SUBPROCESS_RUN(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=clean_environment,
        )
        return CommandResult(
            arguments=arguments,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


_PRODUCTION_DOCKER_RUNNER_METHODS = (
    ProductionDockerRunner.__init__,
    ProductionDockerRunner.run,
)


@dataclass(frozen=True, slots=True, init=False)
class AuthenticatedLifecycleRunner:
    """Authority-issued lifecycle executor bound to preflight and ownership."""

    _executor: ProductionDockerRunner
    _run_id: str
    _preflight_sha256: str
    _manifest_sha256: str
    _docker_endpoint: str
    _daemon_id: str
    _token: object

    def __init__(
        self,
        *,
        _token: object | None = None,
        executor: ProductionDockerRunner | None = None,
        run_id: str = "",
        preflight_sha256: str = "",
        manifest_sha256: str = "",
        docker_endpoint: str = "",
        daemon_id: str = "",
    ) -> None:
        if (
            _token is not _AUTHENTICATED_LIFECYCLE_RUNNER_TOKEN
            or type(executor) is not ProductionDockerRunner
        ):
            raise TypeError(
                "AuthenticatedLifecycleRunner must come from locked preflight"
            )
        for name, value in {
            "_executor": executor,
            "_run_id": run_id,
            "_preflight_sha256": preflight_sha256,
            "_manifest_sha256": manifest_sha256,
            "_docker_endpoint": docker_endpoint,
            "_daemon_id": daemon_id,
            "_token": _AUTHENTICATED_LIFECYCLE_RUNNER_TOKEN,
        }.items():
            object.__setattr__(self, name, value)

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
        environment: dict[str, str] | None = None,
    ) -> CommandResult:
        return self._executor.run(
            arguments,
            timeout_seconds=timeout_seconds,
            environment=environment,
        )

    def is_authentic(
        self,
        *,
        preflight: AuthenticatedPreflightEvidence,
        context: AuthenticatedOwnershipContext,
    ) -> bool:
        return _authenticated_lifecycle_runner_has_integrity(
            self,
            preflight=preflight,
            context=context,
        )


def _authenticated_lifecycle_runner_has_integrity(
    runner: object,
    *,
    preflight: AuthenticatedPreflightEvidence,
    context: AuthenticatedOwnershipContext,
) -> bool:
    if (
        type(runner) is not AuthenticatedLifecycleRunner
        or not isinstance(preflight, AuthenticatedPreflightEvidence)
        or not isinstance(context, AuthenticatedOwnershipContext)
    ):
        return False
    docker = preflight.inputs.docker
    return (
        runner._token is _AUTHENTICATED_LIFECYCLE_RUNNER_TOKEN
        and type(runner._executor) is ProductionDockerRunner
        and runner._executor._token is _PRODUCTION_DOCKER_RUNNER_TOKEN
        and not hasattr(runner, "__dict__")
        and not hasattr(runner._executor, "__dict__")
        and (
            ProductionDockerRunner.__init__,
            ProductionDockerRunner.run,
        )
        == _PRODUCTION_DOCKER_RUNNER_METHODS
        and (
            AuthenticatedLifecycleRunner.__init__,
            AuthenticatedLifecycleRunner.run,
            AuthenticatedLifecycleRunner.is_authentic,
        )
        == _AUTHENTICATED_LIFECYCLE_RUNNER_METHODS
        and preflight.is_current()
        and context.is_authentic()
        and runner._run_id == preflight.run_id == context.run_id
        and runner._preflight_sha256 == preflight.content_sha256
        and runner._manifest_sha256 == context.manifest_sha256
        and runner._docker_endpoint == docker.endpoint
        and runner._daemon_id == docker.daemon_id
        and runner._executor._run_id == runner._run_id
        and runner._executor._docker_endpoint == runner._docker_endpoint
        and runner._executor._daemon_id == runner._daemon_id
    )


_AUTHENTICATED_LIFECYCLE_RUNNER_METHODS = (
    AuthenticatedLifecycleRunner.__init__,
    AuthenticatedLifecycleRunner.run,
    AuthenticatedLifecycleRunner.is_authentic,
)


def create_authenticated_lifecycle_runner(
    *,
    preflight: AuthenticatedPreflightEvidence,
    context: AuthenticatedOwnershipContext,
) -> AuthenticatedLifecycleRunner:
    """Bind a direct Docker executor to one current locked preflight."""
    if (
        not isinstance(preflight, AuthenticatedPreflightEvidence)
        or not preflight.is_current()
        or not isinstance(context, AuthenticatedOwnershipContext)
        or not context.is_authentic()
        or preflight.run_id != context.run_id
    ):
        raise ValueError("lifecycle runner authority is invalid")
    docker = preflight.inputs.docker
    prefix = docker_host_prefix(docker.endpoint)
    if prefix[:1] != ("docker",) or not docker.daemon_id:
        raise ValueError("lifecycle runner Docker binding is invalid")
    executor = ProductionDockerRunner(
        _token=_PRODUCTION_DOCKER_RUNNER_TOKEN,
        run_id=context.run_id,
        docker_endpoint=docker.endpoint,
        daemon_id=docker.daemon_id,
    )
    return AuthenticatedLifecycleRunner(
        _token=_AUTHENTICATED_LIFECYCLE_RUNNER_TOKEN,
        executor=executor,
        run_id=context.run_id,
        preflight_sha256=preflight.content_sha256,
        manifest_sha256=context.manifest_sha256,
        docker_endpoint=docker.endpoint,
        daemon_id=docker.daemon_id,
    )


class ProbeReason(str, Enum):
    OBSERVED = "OBSERVED"
    THREE_PHASE_COVERAGE = "THREE_PHASE_COVERAGE"
    THREE_PHASE_COVERAGE_INCOMPLETE = "THREE_PHASE_COVERAGE_INCOMPLETE"
    QUERY_FIXTURE_NOT_FROZEN = "QUERY_FIXTURE_NOT_FROZEN"
    RESOURCE_OWNERSHIP_UNKNOWN = "RESOURCE_OWNERSHIP_UNKNOWN"
    HTTP_DEADLINE_EXCEEDED = "HTTP_DEADLINE_EXCEEDED"
    HTTP_TRANSPORT_ERROR = "HTTP_TRANSPORT_ERROR"
    HTTP_REDIRECT_FORBIDDEN = "HTTP_REDIRECT_FORBIDDEN"
    HTTP_STATUS_ERROR = "HTTP_STATUS_ERROR"
    HTTP_HEADER_LIMIT_EXCEEDED = "HTTP_HEADER_LIMIT_EXCEEDED"
    HTTP_BODY_LIMIT_EXCEEDED = "HTTP_BODY_LIMIT_EXCEEDED"
    PROBE_SCHEMA_INVALID = "PROBE_SCHEMA_INVALID"
    PROBE_STALE_OBSERVATION = "PROBE_STALE_OBSERVATION"
    EVIDENCE_PERSISTENCE_FAILED = "EVIDENCE_PERSISTENCE_FAILED"

    @property
    def exit_code(self) -> int:
        if self in {ProbeReason.OBSERVED, ProbeReason.THREE_PHASE_COVERAGE}:
            return 0
        if self is ProbeReason.QUERY_FIXTURE_NOT_FROZEN:
            return 21
        if self is ProbeReason.RESOURCE_OWNERSHIP_UNKNOWN:
            return 40
        return 30


@dataclass(frozen=True)
class ProbeObservation:
    run_id: str
    cycle_number: int
    phase: str
    fixture_version: str
    fixture_sha256: str
    reason: ProbeReason
    exit_code: int
    trace_id: str | None = None
    request_id: str | None = None
    artifact_paths: tuple[str, ...] = ()
    artifact_sha256: tuple[tuple[str, str], ...] = ()
    _receipt_token: object | None = field(default=None, repr=False, compare=False)
    _production_receipt: bool = field(default=False, repr=False, compare=False)
    _store_root: str | None = field(default=None, repr=False, compare=False)

    @property
    def observed(self) -> bool:
        return self.reason is ProbeReason.OBSERVED

    def is_production_receipt(
        self,
        *,
        capability: FrozenTelemetryQueryCapability,
        store: ObserverEvidenceStore,
    ) -> bool:
        return (
            self._receipt_token is _PROBE_RECEIPT_TOKEN
            and self._production_receipt
            and self._store_root == str(store.root)
            and capability.store is store
            and capability.is_authentic()
            and self.run_id == capability.run_id
            and self.fixture_version == capability.registry.fixture_version
            and self.fixture_sha256 == capability.content_sha256
        )


@dataclass(frozen=True)
class ProbePhaseCoverage:
    reason: ProbeReason
    phases: tuple[str, ...]
    artifact_path: str | None = None

    @property
    def complete(self) -> bool:
        return self.reason is ProbeReason.THREE_PHASE_COVERAGE


class _Artifact(Protocol):
    path: Path
    sha256: str


class _EvidenceStore(Protocol):
    def write_immutable(
        self,
        relative_path: str,
        value: dict[str, Any],
    ) -> _Artifact: ...


class _HttpClient(Protocol):
    @property
    def run_id(self) -> str: ...

    def request(self, request: HttpRequest) -> HttpExchange: ...


@dataclass(frozen=True)
class _ParsedProbe:
    trace_id: str | None
    request_id: str | None
    ad_items: int


class ProbeAdapter:
    def __init__(
        self,
        *,
        client: _HttpClient,
        evidence_store: _EvidenceStore,
        fixture: RegistryAccess,
    ) -> None:
        self._client = client
        self._store = evidence_store
        self._loaded = fixture

    def observe(
        self,
        *,
        window: PhaseWindow,
        base_url: str,
        artifact_prefix: str,
    ) -> ProbeObservation:
        registry = self._loaded.registry
        fixture = registry.probe
        if (
            not _registry_access_is_frozen_for_adapter(
                self._loaded,
                run_id=window.run_id,
                evidence_store=self._store,
                client=self._client,
            )
            or fixture.state is not FixtureState.FROZEN
        ):
            return self._observation(
                window,
                ProbeReason.QUERY_FIXTURE_NOT_FROZEN,
            )
        if self._client.run_id != window.run_id:
            return self._observation(
                window,
                ProbeReason.RESOURCE_OWNERSHIP_UNKNOWN,
            )
        assert fixture.method is not None
        assert fixture.path is not None
        assert fixture.getads_proof_artifact is not None
        request = HttpRequest(
            endpoint=OwnedEndpoint(
                base_url=base_url,
                service=fixture.target.service,
                target_port=fixture.target.target_port,
                protocol=fixture.target.protocol,
            ),
            method=fixture.method,
            target=fixture.path,
            absolute_deadline_monotonic=window.monotonic_ended_at,
        )
        exchange = self._client.request(request)
        raw_path = f"{artifact_prefix}/telemetry/probe/observation-raw.json"
        paths: list[str] = []
        hashes: list[tuple[str, str]] = []
        if not self._persist_raw(
            raw_path,
            exchange,
            window=window,
            paths=paths,
            hashes=hashes,
        ):
            return self._observation(
                window,
                ProbeReason.EVIDENCE_PERSISTENCE_FAILED,
            )

        parsed: _ParsedProbe | None = None
        if not exchange.succeeded:
            reason = _http_reason(exchange.reason)
        elif not (
            window.contains_observation(exchange.started_at)
            and window.contains_observation(exchange.ended_at)
        ):
            reason = ProbeReason.PROBE_STALE_OBSERVATION
        else:
            try:
                parsed = _parse_probe(exchange)
            except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
                reason = ProbeReason.PROBE_SCHEMA_INVALID
            else:
                reason = ProbeReason.OBSERVED

        decision_path = raw_path.removesuffix("raw.json") + "decision.json"
        decision = {
            "schema_version": "phase0.probe-decision.v1",
            "run_id": window.run_id,
            "cycle_number": window.cycle_number,
            "scenario_phase": window.scenario_phase.value,
            "fixture_version": registry.fixture_version,
            "fixture_sha256": self._loaded.content_sha256,
            "raw_response_artifact": raw_path,
            "decision": reason is ProbeReason.OBSERVED,
            "reason": reason.value,
            "exit_code": reason.exit_code,
            "getads_attribution_proof_artifact": fixture.getads_proof_artifact,
            "parsed_trace_id": parsed.trace_id if parsed is not None else None,
            "parsed_request_id": parsed.request_id if parsed is not None else None,
            "parsed_ad_items": parsed.ad_items if parsed is not None else None,
        }
        if not _write(self._store, decision_path, decision, paths, hashes):
            return self._observation(
                window,
                ProbeReason.EVIDENCE_PERSISTENCE_FAILED,
                artifact_paths=tuple(paths),
                artifact_sha256=tuple(hashes),
            )
        return self._observation(
            window,
            reason,
            trace_id=parsed.trace_id if parsed is not None else None,
            request_id=parsed.request_id if parsed is not None else None,
            artifact_paths=tuple(paths),
            artifact_sha256=tuple(hashes),
        )

    def validate_phase_coverage(
        self,
        *,
        observations: tuple[ProbeObservation, ...],
        artifact_prefix: str,
    ) -> ProbePhaseCoverage:
        expected = ("baseline", "fault", "recovery")
        cycle_numbers = {observation.cycle_number for observation in observations}
        cycle_number = next(iter(cycle_numbers)) if len(cycle_numbers) == 1 else None
        exact_prefix = (
            f"cycles/{cycle_number:03d}" if cycle_number is not None else None
        )
        valid = (
            len(observations) == 3
            and tuple(observation.phase for observation in observations) == expected
            and all(observation.observed for observation in observations)
            and {observation.run_id for observation in observations}
            == {self._client.run_id}
            and isinstance(self._loaded, FrozenTelemetryQueryCapability)
            and self._loaded.run_id == self._client.run_id
            and len(cycle_numbers) == 1
            and {observation.fixture_version for observation in observations}
            == {self._loaded.registry.fixture_version}
            and {observation.fixture_sha256 for observation in observations}
            == {self._loaded.content_sha256}
            and artifact_prefix == exact_prefix
            and isinstance(self._store, ObserverEvidenceStore)
            and self._loaded.store is self._store
            and self._loaded.is_authentic()
            and all(
                observation.is_production_receipt(
                    capability=self._loaded,
                    store=self._store,
                )
                for observation in observations
            )
            and all(
                _observation_artifacts_are_bound(
                    observation,
                    store=self._store,
                    capability=self._loaded,
                    artifact_prefix=artifact_prefix,
                )
                for observation in observations
            )
        )
        reason = (
            ProbeReason.THREE_PHASE_COVERAGE
            if valid
            else ProbeReason.THREE_PHASE_COVERAGE_INCOMPLETE
        )
        path = f"{artifact_prefix}/telemetry/probe/phase-coverage.json"
        payload = {
            "schema_version": "phase0.probe-phase-coverage.v1",
            "run_id": observations[0].run_id if observations else self._client.run_id,
            "fixture_version": (
                observations[0].fixture_version
                if observations
                else self._loaded.registry.fixture_version
            ),
            "fixture_sha256": (
                observations[0].fixture_sha256
                if observations
                else self._loaded.content_sha256
            ),
            "phases": tuple(observation.phase for observation in observations),
            "observation_artifacts": tuple(
                path
                for observation in observations
                for path in observation.artifact_paths
            ),
            "decision": valid,
            "reason": reason.value,
        }
        paths: list[str] = []
        if not _write(self._store, path, payload, paths):
            return ProbePhaseCoverage(
                reason=ProbeReason.EVIDENCE_PERSISTENCE_FAILED,
                phases=tuple(observation.phase for observation in observations),
            )
        return ProbePhaseCoverage(
            reason=reason,
            phases=tuple(observation.phase for observation in observations),
            artifact_path=paths[0],
        )

    def _persist_raw(
        self,
        path: str,
        exchange: HttpExchange,
        *,
        window: PhaseWindow,
        paths: list[str],
        hashes: list[tuple[str, str]],
    ) -> bool:
        fixture = self._loaded.registry.probe
        assert fixture.method is not None and fixture.path is not None
        boundary_passed = _observer_input_boundary_passed(exchange, fixture)
        payload = {
            "schema_version": "phase0.probe-raw.v1",
            "run_id": window.run_id,
            "cycle_number": window.cycle_number,
            "scenario_phase": window.scenario_phase.value,
            "fixture_version": self._loaded.registry.fixture_version,
            "fixture_sha256": self._loaded.content_sha256,
            "upstream_commit": self._loaded.registry.upstream_commit,
            "compose_config_sha256": self._loaded.registry.compose_config_sha256,
            "sanitized_command": ["HTTP", fixture.method, fixture.path],
            "fixed_input": fixture.input,
            "input_capability_schema": "phase0.probe-observer-input.v1",
            "unexpected_input_count": 0 if boundary_passed else 1,
            "observer_input_boundary_passed": boundary_passed,
            "exact_local_request": exchange.request.target,
            "request_started_at": exchange.started_at.isoformat(),
            "response_ended_at": exchange.ended_at.isoformat(),
            "monotonic_started_at": exchange.monotonic_started_at,
            "monotonic_ended_at": exchange.monotonic_ended_at,
            "http_status": exchange.status_code,
            "transport_reason": exchange.reason.value,
            "transport_exit_code": (0 if exchange.reason is HttpReason.OK else 30),
            "raw_response_base64": base64.b64encode(exchange.raw_body).decode("ascii"),
            "raw_response_sha256": exchange.raw_sha256,
            "raw_response_partial": exchange.raw_body_partial,
        }
        return _write(self._store, path, payload, paths, hashes)

    def _observation(
        self,
        window: PhaseWindow,
        reason: ProbeReason,
        *,
        trace_id: str | None = None,
        request_id: str | None = None,
        artifact_paths: tuple[str, ...] = (),
        artifact_sha256: tuple[tuple[str, str], ...] = (),
    ) -> ProbeObservation:
        production = (
            isinstance(self._loaded, FrozenTelemetryQueryCapability)
            and type(self._client) is OwnedHttpClient
            and _owned_http_client_has_production_integrity(self._client)
            and isinstance(self._store, ObserverEvidenceStore)
            and self._loaded.store is self._store
        )
        return ProbeObservation(
            run_id=window.run_id,
            cycle_number=window.cycle_number,
            phase=window.scenario_phase.value,
            fixture_version=self._loaded.registry.fixture_version,
            fixture_sha256=self._loaded.content_sha256,
            reason=reason,
            exit_code=reason.exit_code,
            trace_id=trace_id,
            request_id=request_id,
            artifact_paths=artifact_paths,
            artifact_sha256=artifact_sha256,
            _receipt_token=_PROBE_RECEIPT_TOKEN,
            _production_receipt=production,
            _store_root=str(self._store.root) if production else None,
        )


class ReadinessGateName(str, Enum):
    OWNERSHIP_RESOURCES_COMPLETE = "ownership_resources_complete"
    LOAD_GENERATOR_READY = "load_generator_ready"
    COLLECTOR_READY = "collector_ready"
    PROMETHEUS_FRESH = "prometheus_fresh"
    JAEGER_FRESH = "jaeger_fresh"
    OPENSEARCH_FRESH = "opensearch_fresh"


@dataclass(frozen=True, init=False)
class LifecycleReadinessExecution:
    """Opaque results emitted only by the locked-daemon readiness executor."""

    run_id: str
    manifest_sha256: str
    docker_endpoint: str
    daemon_id: str
    command_results: tuple[tuple[str, CommandResult], ...]
    _token: object

    def __init__(
        self,
        *,
        _token: object | None = None,
        run_id: str,
        manifest_sha256: str,
        docker_endpoint: str,
        daemon_id: str,
        command_results: tuple[tuple[str, CommandResult], ...],
    ) -> None:
        if _token is not _LIFECYCLE_READINESS_TOKEN:
            raise TypeError("readiness execution must come from lifecycle runner")
        for name, value in {
            "run_id": run_id,
            "manifest_sha256": manifest_sha256,
            "docker_endpoint": docker_endpoint,
            "daemon_id": daemon_id,
            "command_results": command_results,
            "_token": _LIFECYCLE_READINESS_TOKEN,
        }.items():
            object.__setattr__(self, name, value)

    def is_authentic(self, context: AuthenticatedOwnershipContext) -> bool:
        return (
            self._token is _LIFECYCLE_READINESS_TOKEN
            and context.is_authentic()
            and self.run_id == context.run_id
            and self.manifest_sha256 == context.manifest_sha256
            and bool(self.docker_endpoint)
            and bool(self.daemon_id)
            and all(
                result.exit_code == 0 and bool(result.arguments)
                for _purpose, result in self.command_results
            )
        )


def execute_lifecycle_readiness(
    runner: LifecycleRunner,
    *,
    preflight: AuthenticatedPreflightEvidence,
    context: AuthenticatedOwnershipContext,
) -> LifecycleReadinessExecution:
    """Execute exact discovery/status commands against the preflight-locked daemon."""
    if type(runner) is not AuthenticatedLifecycleRunner:
        raise TypeError("production readiness requires AuthenticatedLifecycleRunner")
    if not _authenticated_lifecycle_runner_has_integrity(
        runner,
        preflight=preflight,
        context=context,
    ):
        raise TypeError("production lifecycle runner method integrity is invalid")
    if (
        not isinstance(preflight, AuthenticatedPreflightEvidence)
        or not preflight.is_current()
        or not context.is_authentic()
        or preflight.run_id != context.run_id
    ):
        raise ValueError("readiness lifecycle authority is invalid")
    docker = preflight.inputs.docker
    prefix = docker_host_prefix(docker.endpoint)
    commands: list[tuple[str, tuple[str, ...]]] = [
        (
            "revalidate_daemon_id",
            (*prefix, "info", "--format", "{{json .ID}}"),
        )
    ]
    commands.extend(
        (invocation.purpose, invocation.arguments)
        for invocation in build_ownership_discovery_invocations(
            project_root=Path("."),
            run_id=context.run_id,
            docker_endpoint=docker.endpoint,
        )
    )
    for service in ("load-generator", "otel-collector"):
        containers = [
            resource
            for resource in context.manifest.resources
            if resource.kind == "container"
            and resource.labels.get(_COMPOSE_SERVICE_LABEL) == service
        ]
        if len(containers) != 1:
            raise ValueError("service container identity is not exact")
        commands.append(
            (
                f"{service}_status",
                (
                    *prefix,
                    "inspect",
                    "--format",
                    "{{json .}}",
                    containers[0].resource_id,
                ),
            )
        )
    results: list[tuple[str, CommandResult]] = []
    for purpose, arguments in commands:
        result = runner.run(
            arguments,
            timeout_seconds=30,
            environment={"ECOMSRE_RUN_ID": context.run_id},
        )
        if result.arguments != arguments or result.exit_code != 0:
            raise ValueError("readiness lifecycle command failed")
        results.append((purpose, result))
    try:
        observed_daemon_id = json.loads(results[0][1].stdout)
    except json.JSONDecodeError as error:
        raise ValueError("readiness daemon identity output is invalid") from error
    if observed_daemon_id != docker.daemon_id:
        raise ValueError("readiness daemon identity changed")
    return LifecycleReadinessExecution(
        _token=_LIFECYCLE_READINESS_TOKEN,
        run_id=context.run_id,
        manifest_sha256=context.manifest_sha256,
        docker_endpoint=docker.endpoint,
        daemon_id=docker.daemon_id,
        command_results=tuple(results),
    )


@dataclass(frozen=True, init=False)
class CurrentResourceDiscovery:
    """Authenticated, hash-bound result of exact no-trunc Docker discovery."""

    run_id: str
    complete_no_trunc: bool
    resources: tuple[OwnedResource, ...]
    evidence_artifact: str
    evidence_sha256: str
    supporting_artifacts: tuple[tuple[str, str], ...]
    _integrity_hmac: str
    _token: object

    def __init__(
        self,
        *,
        _token: object | None = None,
        run_id: str,
        complete_no_trunc: bool,
        resources: tuple[OwnedResource, ...],
        evidence_artifact: str,
        evidence_sha256: str,
        supporting_artifacts: tuple[tuple[str, str], ...],
    ) -> None:
        if _token is not _DISCOVERY_TOKEN:
            raise TypeError("resource discovery must come from evidence loader")
        values = {
            "run_id": run_id,
            "complete_no_trunc": complete_no_trunc,
            "resources": resources,
            "evidence_artifact": evidence_artifact,
            "evidence_sha256": evidence_sha256,
            "supporting_artifacts": supporting_artifacts,
            "_token": _DISCOVERY_TOKEN,
        }
        values["_integrity_hmac"] = _readiness_integrity(
            "discovery",
            {
                "run_id": run_id,
                "complete_no_trunc": complete_no_trunc,
                "resources": [
                    resource.model_dump(mode="json") for resource in resources
                ],
                "evidence_artifact": evidence_artifact,
                "evidence_sha256": evidence_sha256,
                "supporting_artifacts": [list(item) for item in supporting_artifacts],
            },
        )
        for name, value in values.items():
            object.__setattr__(self, name, value)


@dataclass(frozen=True, init=False)
class ServiceReadinessProof:
    """Authenticated, hash-bound service-local readiness or ingestion proof."""

    run_id: str
    service: str
    running: bool
    healthy: bool
    attributable_current_run_evidence: bool
    source: str
    evidence_artifact: str
    evidence_sha256: str
    supporting_artifacts: tuple[tuple[str, str], ...]
    _integrity_hmac: str
    _token: object

    def __init__(
        self,
        *,
        _token: object | None = None,
        run_id: str,
        service: str,
        running: bool,
        healthy: bool,
        attributable_current_run_evidence: bool,
        source: str,
        evidence_artifact: str,
        evidence_sha256: str,
        supporting_artifacts: tuple[tuple[str, str], ...],
    ) -> None:
        if _token is not _SERVICE_PROOF_TOKEN:
            raise TypeError("service proof must come from evidence loader")
        payload = {
            "run_id": run_id,
            "service": service,
            "running": running,
            "healthy": healthy,
            "attributable_current_run_evidence": attributable_current_run_evidence,
            "source": source,
            "evidence_artifact": evidence_artifact,
            "evidence_sha256": evidence_sha256,
            "supporting_artifacts": supporting_artifacts,
        }
        values = {
            **payload,
            "_integrity_hmac": _readiness_integrity("service", payload),
            "_token": _SERVICE_PROOF_TOKEN,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)


@dataclass(frozen=True, init=False)
class ReadinessGate:
    """Opaque gate issued only from live observer evidence and frozen authority."""

    run_id: str
    name: ReadinessGateName
    passed: bool
    reason: str
    cycle_number: int | None
    phase: str | None
    fixture_sha256: str
    ownership_manifest_sha256: str
    evidence_artifacts: tuple[str, ...]
    evidence_sha256: tuple[tuple[str, str], ...]
    _store_root: str
    _integrity_hmac: str
    _token: object

    def __init__(
        self,
        *,
        _token: object | None = None,
        run_id: str,
        name: ReadinessGateName,
        passed: bool,
        reason: str,
        cycle_number: int | None,
        phase: str | None,
        fixture_sha256: str,
        ownership_manifest_sha256: str,
        evidence_sha256: tuple[tuple[str, str], ...],
        store_root: Path,
    ) -> None:
        if _token is not _READINESS_GATE_TOKEN:
            raise TypeError("readiness gate must come from evidence evaluator")
        integrity_payload = {
            "run_id": run_id,
            "name": name.value,
            "passed": passed,
            "reason": reason,
            "cycle_number": cycle_number,
            "phase": phase,
            "fixture_sha256": fixture_sha256,
            "ownership_manifest_sha256": ownership_manifest_sha256,
            "evidence_sha256": [list(item) for item in evidence_sha256],
            "_store_root": str(store_root),
        }
        values = {
            **integrity_payload,
            "name": name,
            "evidence_artifacts": tuple(path for path, _digest in evidence_sha256),
            "evidence_sha256": evidence_sha256,
            "_integrity_hmac": _readiness_integrity("gate", integrity_payload),
            "_token": _READINESS_GATE_TOKEN,
        }
        for field_name, value in values.items():
            object.__setattr__(self, field_name, value)


class _BackendResult(Protocol):
    @property
    def ready(self) -> bool: ...

    @property
    def reason(self) -> object: ...

    @property
    def artifact_paths(self) -> tuple[str, ...]: ...

    @property
    def run_id(self) -> str | None: ...

    @property
    def cycle_number(self) -> int | None: ...

    @property
    def phase(self) -> str | None: ...

    @property
    def fixture_sha256(self) -> str | None: ...

    @property
    def artifact_sha256(self) -> tuple[tuple[str, str], ...]: ...


@dataclass(frozen=True, slots=True, init=False)
class LoadGeneratorTelemetryReceipt:
    """Dedicated proof of current load-generator user_get_ads traffic."""

    run_id: str
    cycle_number: int
    phase: str
    trace_id: str
    fixture_sha256: str
    artifact_paths: tuple[str, ...]
    artifact_sha256: tuple[tuple[str, str], ...]
    ready: bool
    reason: str
    _store_root: str
    _token: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        _token: object | None = None,
        run_id: str = "",
        cycle_number: int = 0,
        phase: str = "",
        trace_id: str = "",
        fixture_sha256: str = "",
        artifact_sha256: tuple[tuple[str, str], ...] = (),
        store_root: Path | str = "",
    ) -> None:
        if _token is not _LOAD_GENERATOR_RECEIPT_TOKEN:
            raise TypeError("LoadGeneratorTelemetryReceipt must come from adapter")
        values = {
            "run_id": run_id,
            "cycle_number": cycle_number,
            "phase": phase,
            "trace_id": trace_id,
            "fixture_sha256": fixture_sha256,
            "artifact_paths": tuple(path for path, _digest in artifact_sha256),
            "artifact_sha256": artifact_sha256,
            "ready": True,
            "reason": "READY",
            "_store_root": str(store_root),
            "_token": _LOAD_GENERATOR_RECEIPT_TOKEN,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)

    def is_production_receipt(
        self,
        *,
        capability: FrozenTelemetryQueryCapability,
        store: ObserverEvidenceStore,
        window: PhaseWindow,
    ) -> bool:
        return _specialized_receipt_is_current(
            self,
            token=_LOAD_GENERATOR_RECEIPT_TOKEN,
            capability=capability,
            store=store,
            window=window,
        ) and _service_trace_artifact_is_valid(
            self,
            store=store,
            window=window,
            service="load-generator",
        )


@dataclass(frozen=True, slots=True, init=False)
class CollectorPipelineReceipt:
    """Dedicated proof that a current trace traversed the Collector pipeline."""

    run_id: str
    cycle_number: int
    phase: str
    trace_id: str
    fixture_sha256: str
    collector_config_sha256: str
    artifact_paths: tuple[str, ...]
    artifact_sha256: tuple[tuple[str, str], ...]
    ready: bool
    reason: str
    _store_root: str
    _token: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        _token: object | None = None,
        run_id: str = "",
        cycle_number: int = 0,
        phase: str = "",
        trace_id: str = "",
        fixture_sha256: str = "",
        collector_config_sha256: str = "",
        artifact_sha256: tuple[tuple[str, str], ...] = (),
        store_root: Path | str = "",
    ) -> None:
        if _token is not _COLLECTOR_PIPELINE_RECEIPT_TOKEN:
            raise TypeError("CollectorPipelineReceipt must come from adapter")
        values = {
            "run_id": run_id,
            "cycle_number": cycle_number,
            "phase": phase,
            "trace_id": trace_id,
            "fixture_sha256": fixture_sha256,
            "collector_config_sha256": collector_config_sha256,
            "artifact_paths": tuple(path for path, _digest in artifact_sha256),
            "artifact_sha256": artifact_sha256,
            "ready": True,
            "reason": "READY",
            "_store_root": str(store_root),
            "_token": _COLLECTOR_PIPELINE_RECEIPT_TOKEN,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)

    def is_production_receipt(
        self,
        *,
        capability: FrozenTelemetryQueryCapability,
        store: ObserverEvidenceStore,
        window: PhaseWindow,
    ) -> bool:
        if not _specialized_receipt_is_current(
            self,
            token=_COLLECTOR_PIPELINE_RECEIPT_TOKEN,
            capability=capability,
            store=store,
            window=window,
        ):
            return False
        config = (
            Path(__file__).resolve().parents[3]
            / "third_party/opentelemetry-demo/src/otel-collector/"
            "otelcol-config-observability.yml"
        )
        return (
            config.is_file()
            and sha256_file(config) == self.collector_config_sha256
            and _service_trace_artifact_is_valid(
                self,
                store=store,
                window=window,
                service="otel-collector",
            )
        )


def _specialized_receipt_is_current(
    receipt: LoadGeneratorTelemetryReceipt | CollectorPipelineReceipt,
    *,
    token: object,
    capability: FrozenTelemetryQueryCapability,
    store: ObserverEvidenceStore,
    window: PhaseWindow,
) -> bool:
    return (
        receipt._token is token
        and capability.is_authentic()
        and capability.store is store
        and receipt.run_id == store.run_id == window.run_id
        and receipt.cycle_number == window.cycle_number
        and receipt.phase == window.scenario_phase.value
        and receipt.fixture_sha256 == capability.content_sha256
        and receipt._store_root == str(store.root)
        and len(receipt.trace_id) == 32
        and bool(receipt.artifact_sha256)
        and all(
            path in receipt.artifact_paths
            and _specialized_artifact_matches(store, path, digest)
            for path, digest in receipt.artifact_sha256
        )
    )


def _specialized_artifact_matches(
    store: ObserverEvidenceStore,
    path: str,
    digest: str,
) -> bool:
    candidate = Path(path)
    if not candidate.is_absolute():
        prefix = f"observer-visible/{store.run_id}/"
        if not path.startswith(prefix):
            return False
        candidate = store.root / path.removeprefix(prefix)
    try:
        resolved = candidate.resolve(strict=True)
        root = store.root.resolve(strict=True)
    except OSError:
        return False
    return (
        root in resolved.parents
        and resolved.is_file()
        and sha256_file(resolved) == digest
    )


def _jaeger_trace_proves_load_generator_and_getads(
    payload: Any,
    *,
    window: PhaseWindow,
) -> str | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return None
    for trace in payload["data"]:
        if (
            not isinstance(trace, dict)
            or not isinstance(trace.get("traceID"), str)
            or len(trace["traceID"]) != 32
            or not isinstance(trace.get("spans"), list)
            or not isinstance(trace.get("processes"), dict)
        ):
            continue
        trace_id = trace["traceID"]
        load_generator = False
        getads = False
        for span in trace["spans"]:
            if not isinstance(span, dict) or span.get("traceID") != trace_id:
                continue
            process = trace["processes"].get(span.get("processID"))
            started = span.get("startTime")
            duration = span.get("duration")
            if (
                not isinstance(process, dict)
                or not isinstance(started, int)
                or isinstance(started, bool)
                or not isinstance(duration, int)
                or isinstance(duration, bool)
                or duration < 0
            ):
                continue
            try:
                utc_started = datetime.fromtimestamp(started / 1_000_000, tz=UTC)
                utc_ended = datetime.fromtimestamp(
                    (started + duration) / 1_000_000,
                    tz=UTC,
                )
            except (OSError, OverflowError, ValueError):
                continue
            if not (
                window.utc_started_at <= utc_started <= utc_ended <= window.utc_ended_at
            ):
                continue
            service = process.get("serviceName")
            operation = span.get("operationName")
            load_generator |= (
                service == "load-generator" and operation == "user_get_ads"
            )
            getads |= service == "ad" and operation == "oteldemo.AdService/GetAds"
        if load_generator and getads:
            return trace_id
    return None


def acquire_load_generator_telemetry_receipt(
    *,
    client: OwnedHttpClient,
    evidence_store: ObserverEvidenceStore,
    registry_capability: FrozenTelemetryQueryCapability,
    window: PhaseWindow,
    jaeger_base_url: str,
) -> LoadGeneratorTelemetryReceipt:
    """Acquire a current load-generator/user_get_ads trace and linked GetAds span."""
    trace_id, artifact = _acquire_service_trace_receipt(
        client=client,
        evidence_store=evidence_store,
        registry_capability=registry_capability,
        window=window,
        jaeger_base_url=jaeger_base_url,
        service="load-generator",
    )
    return LoadGeneratorTelemetryReceipt(
        _token=_LOAD_GENERATOR_RECEIPT_TOKEN,
        run_id=window.run_id,
        cycle_number=window.cycle_number,
        phase=window.scenario_phase.value,
        trace_id=trace_id,
        fixture_sha256=registry_capability.content_sha256,
        artifact_sha256=(artifact,),
        store_root=evidence_store.root,
    )


def acquire_collector_pipeline_receipt(
    *,
    client: OwnedHttpClient,
    evidence_store: ObserverEvidenceStore,
    registry_capability: FrozenTelemetryQueryCapability,
    window: PhaseWindow,
    jaeger_base_url: str,
) -> CollectorPipelineReceipt:
    """Prove current Collector pipeline ingestion via an exported linked trace."""
    trace_id, artifact = _acquire_service_trace_receipt(
        client=client,
        evidence_store=evidence_store,
        registry_capability=registry_capability,
        window=window,
        jaeger_base_url=jaeger_base_url,
        service="otel-collector",
    )
    config = (
        Path(__file__).resolve().parents[3]
        / "third_party/opentelemetry-demo/src/otel-collector/"
        "otelcol-config-observability.yml"
    )
    content = config.read_text(encoding="utf-8")
    if not all(
        marker in content
        for marker in (
            "traces:",
            "otlp_grpc/jaeger",
            "span_metrics",
        )
    ):
        raise ValueError("pinned Collector trace pipeline contract differs")
    return CollectorPipelineReceipt(
        _token=_COLLECTOR_PIPELINE_RECEIPT_TOKEN,
        run_id=window.run_id,
        cycle_number=window.cycle_number,
        phase=window.scenario_phase.value,
        trace_id=trace_id,
        fixture_sha256=registry_capability.content_sha256,
        collector_config_sha256=sha256_file(config),
        artifact_sha256=(artifact,),
        store_root=evidence_store.root,
    )


def _acquire_service_trace_receipt(
    *,
    client: OwnedHttpClient,
    evidence_store: ObserverEvidenceStore,
    registry_capability: FrozenTelemetryQueryCapability,
    window: PhaseWindow,
    jaeger_base_url: str,
    service: str,
) -> tuple[str, tuple[str, str]]:
    if type(
        client
    ) is not OwnedHttpClient or not _owned_http_client_has_production_integrity(client):
        raise TypeError("specialized receipt requires production OwnedHttpClient")
    if (
        not isinstance(evidence_store, ObserverEvidenceStore)
        or not isinstance(registry_capability, FrozenTelemetryQueryCapability)
        or not registry_capability.is_authentic()
        or registry_capability.store is not evidence_store
        or window.run_id != evidence_store.run_id
    ):
        raise ValueError("specialized receipt authority is invalid")
    fixture = registry_capability.registry.jaeger
    target = "/api/traces?" + urlencode(
        {
            "service": "load-generator",
            "operation": "user_get_ads",
            "start": int(window.utc_started_at.timestamp() * 1_000_000),
            "end": int(window.utc_ended_at.timestamp() * 1_000_000),
            "limit": 100,
        }
    )
    exchange = client.request(
        HttpRequest(
            endpoint=OwnedEndpoint(
                base_url=jaeger_base_url,
                service=fixture.target.service,
                target_port=fixture.target.target_port,
                protocol=fixture.target.protocol,
            ),
            method="GET",
            target=target,
            absolute_deadline_monotonic=window.monotonic_ended_at,
        )
    )
    artifact = evidence_store.write_immutable(
        f"lifecycle/signals/{service}-jaeger-trace.json",
        {
            "schema_version": "phase0.service-trace-receipt.v1",
            "run_id": window.run_id,
            "cycle_number": window.cycle_number,
            "phase": window.scenario_phase.value,
            "service": service,
            "exact_request": target,
            "request_started_at": exchange.started_at.isoformat(),
            "response_ended_at": exchange.ended_at.isoformat(),
            "monotonic_started_at": exchange.monotonic_started_at,
            "monotonic_ended_at": exchange.monotonic_ended_at,
            "http_status": exchange.status_code,
            "transport_reason": exchange.reason.value,
            "raw_response_base64": base64.b64encode(exchange.raw_body).decode("ascii"),
            "raw_response_sha256": exchange.raw_sha256,
            "raw_response_partial": exchange.raw_body_partial,
        },
    )
    if not exchange.succeeded:
        raise ValueError(
            f"specialized service trace acquisition failed: {exchange.reason.value}"
        )
    try:
        payload = json.loads(exchange.raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(
            "specialized service trace response is invalid JSON"
        ) from error
    trace_id = _jaeger_trace_proves_load_generator_and_getads(
        payload,
        window=window,
    )
    if trace_id is None:
        raise ValueError("specialized service trace lacks linked GetAds evidence")
    return trace_id, (str(artifact.path), artifact.sha256)


def _service_trace_artifact_is_valid(
    receipt: LoadGeneratorTelemetryReceipt | CollectorPipelineReceipt,
    *,
    store: ObserverEvidenceStore,
    window: PhaseWindow,
    service: str,
) -> bool:
    if len(receipt.artifact_sha256) != 1:
        return False
    path, digest = receipt.artifact_sha256[0]
    if not _specialized_artifact_matches(store, path, digest):
        return False
    try:
        payload = json.loads(Path(path).read_bytes())
        body = base64.b64decode(payload["raw_response_base64"], validate=True)
        utc_started = datetime.fromisoformat(payload["request_started_at"])
        utc_ended = datetime.fromisoformat(payload["response_ended_at"])
    except (
        OSError,
        KeyError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        TypeError,
        ValueError,
    ):
        return False
    expected_request = "/api/traces?" + urlencode(
        {
            "service": "load-generator",
            "operation": "user_get_ads",
            "start": int(window.utc_started_at.timestamp() * 1_000_000),
            "end": int(window.utc_ended_at.timestamp() * 1_000_000),
            "limit": 100,
        }
    )
    if (
        set(payload)
        != {
            "schema_version",
            "run_id",
            "cycle_number",
            "phase",
            "service",
            "exact_request",
            "request_started_at",
            "response_ended_at",
            "monotonic_started_at",
            "monotonic_ended_at",
            "http_status",
            "transport_reason",
            "raw_response_base64",
            "raw_response_sha256",
            "raw_response_partial",
        }
        or payload.get("schema_version") != "phase0.service-trace-receipt.v1"
        or payload.get("run_id") != window.run_id
        or payload.get("cycle_number") != window.cycle_number
        or payload.get("phase") != window.scenario_phase.value
        or payload.get("service") != service
        or payload.get("exact_request") != expected_request
        or payload.get("http_status") != 200
        or payload.get("transport_reason") != HttpReason.OK.value
        or payload.get("raw_response_partial") is not False
        or sha256_bytes(body) != payload.get("raw_response_sha256")
        or not window.utc_started_at <= utc_started <= utc_ended <= window.utc_ended_at
        or not window.monotonic_started_at
        <= payload.get("monotonic_started_at", -1)
        <= payload.get("monotonic_ended_at", -1)
        <= window.monotonic_ended_at
    ):
        return False
    try:
        trace_id = _jaeger_trace_proves_load_generator_and_getads(
            json.loads(body),
            window=window,
        )
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return trace_id == receipt.trace_id


@dataclass(frozen=True)
class ReadinessHandoff:
    reason: str
    evidence: ReadinessEvidence | None
    artifact_path: str | None = None

    @property
    def ready(self) -> bool:
        return self.evidence is not None and self.evidence.all_passed


def derive_current_resource_discovery(
    context: AuthenticatedOwnershipContext,
    evidence_store: ObserverEvidenceStore,
    execution: LifecycleReadinessExecution,
) -> CurrentResourceDiscovery:
    """Derive ownership only from an opaque lifecycle execution receipt."""
    if not execution.is_authentic(context) or evidence_store.run_id != context.run_id:
        raise ValueError("resource discovery execution receipt is invalid")
    by_purpose = dict(execution.command_results)
    expected = build_ownership_discovery_invocations(
        project_root=Path("."),
        run_id=context.run_id,
        docker_endpoint=execution.docker_endpoint,
    )
    parsed: dict[tuple[str, str], tuple[OwnedResource, ...]] = {}
    supporting: list[tuple[str, str]] = []
    for invocation in expected:
        result = by_purpose.get(invocation.purpose)
        if result is None or result.arguments != invocation.arguments:
            raise ValueError("resource discovery execution set is incomplete")
        scope, plural = invocation.purpose.split("_", 1)
        kind = plural.removesuffix("s")
        required_labels = {"com.docker.compose.project": PROJECT_NAMESPACE}
        if scope == "owned":
            required_labels.update(
                {
                    PROJECT_LABEL: PROJECT_NAMESPACE,
                    RUN_LABEL: context.run_id,
                }
            )
        parsed[(scope, kind)] = _parse_owned_resources(
            result.stdout,
            kind=kind,
            required_labels=required_labels,
        )
        supporting.append(
            _persist_lifecycle_result(
                evidence_store,
                purpose=invocation.purpose,
                result=result,
            )
        )
    resources: list[OwnedResource] = []
    for kind in ("container", "network", "volume"):
        if parsed[("potential", kind)] != parsed[("owned", kind)]:
            raise ValueError("potential and owned resource discovery differs")
        resources.extend(parsed[("owned", kind)])
    frozen_resources = tuple(
        sorted(
            resources,
            key=lambda resource: (
                resource.kind,
                resource.name,
                resource.resource_id,
            ),
        )
    )
    verify_owned_resources(frozen_resources, context.manifest)
    index = evidence_store.write_immutable(
        "lifecycle/current-resource-discovery.json",
        {
            "schema_version": "phase0.current-resource-discovery-index.v2",
            "run_id": context.run_id,
            "ownership_manifest_sha256": context.manifest_sha256,
            "docker_endpoint_sha256": sha256_bytes(execution.docker_endpoint.encode()),
            "daemon_id_sha256": sha256_bytes(execution.daemon_id.encode()),
            "command_artifacts": dict(supporting),
        },
    )
    return CurrentResourceDiscovery(
        _token=_DISCOVERY_TOKEN,
        run_id=context.run_id,
        complete_no_trunc=True,
        resources=frozen_resources,
        evidence_artifact=str(index.path),
        evidence_sha256=index.sha256,
        supporting_artifacts=tuple(supporting),
    )


def derive_service_readiness_proof(
    context: AuthenticatedOwnershipContext,
    evidence_store: ObserverEvidenceStore,
    execution: LifecycleReadinessExecution,
    *,
    service: str,
    telemetry_receipt: (LoadGeneratorTelemetryReceipt | CollectorPipelineReceipt),
    registry_capability: FrozenTelemetryQueryCapability,
    window: PhaseWindow,
) -> ServiceReadinessProof:
    """Bind exact Docker state to an adapter-issued telemetry receipt."""
    expected_receipt_type = (
        LoadGeneratorTelemetryReceipt
        if service == "load-generator"
        else CollectorPipelineReceipt
        if service == "otel-collector"
        else None
    )
    if expected_receipt_type is None:
        raise ValueError("service readiness execution receipt is invalid")
    if type(telemetry_receipt) is not expected_receipt_type:
        raise TypeError(
            f"{service} readiness requires {expected_receipt_type.__name__}"
        )
    if not execution.is_authentic(context) or evidence_store.run_id != context.run_id:
        raise ValueError("service readiness execution receipt is invalid")
    validator = getattr(telemetry_receipt, "is_production_receipt", None)
    if not callable(validator) or not validator(
        capability=registry_capability,
        store=evidence_store,
        window=window,
    ):
        raise ValueError("service signal requires production adapter receipt")
    by_purpose = dict(execution.command_results)
    result = by_purpose.get(f"{service}_status")
    containers = [
        resource
        for resource in context.manifest.resources
        if resource.kind == "container"
        and resource.labels.get(_COMPOSE_SERVICE_LABEL) == service
    ]
    expected_arguments = (
        *docker_host_prefix(execution.docker_endpoint),
        "inspect",
        "--format",
        "{{json .}}",
        containers[0].resource_id if len(containers) == 1 else "",
    )
    if result is None or result.arguments != expected_arguments:
        raise ValueError("service status command is not exact")
    status = _parse_service_container_inspect(
        result.stdout,
        resource=containers[0],
    )
    if status is None:
        raise ValueError("Docker state output or container identity is invalid")
    health = status.get("Health")
    healthy = isinstance(health, dict) and health.get("Status") == "healthy"
    status_artifact = _persist_lifecycle_result(
        evidence_store,
        purpose=f"{service}_status",
        result=result,
    )
    signal_artifacts = tuple(telemetry_receipt.artifact_sha256)
    index = evidence_store.write_immutable(
        f"lifecycle/{service}-readiness.json",
        {
            "schema_version": "phase0.service-readiness-index.v2",
            "run_id": context.run_id,
            "ownership_manifest_sha256": context.manifest_sha256,
            "service": service,
            "container_id": containers[0].resource_id,
            "docker_endpoint_sha256": sha256_bytes(execution.docker_endpoint.encode()),
            "daemon_id_sha256": sha256_bytes(execution.daemon_id.encode()),
            "status_artifact": {
                "path": status_artifact[0],
                "sha256": status_artifact[1],
            },
            "telemetry_artifacts": dict(signal_artifacts),
        },
    )
    return ServiceReadinessProof(
        _token=_SERVICE_PROOF_TOKEN,
        run_id=context.run_id,
        service=service,
        running=status.get("Running") is True and status.get("Status") == "running",
        healthy=healthy,
        attributable_current_run_evidence=bool(telemetry_receipt.ready),
        source="adapter_receipt",
        evidence_artifact=str(index.path),
        evidence_sha256=index.sha256,
        supporting_artifacts=(status_artifact, *signal_artifacts),
    )


def _parse_service_container_inspect(
    stdout: str,
    *,
    resource: OwnedResource,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("Id") != resource.resource_id
        or payload.get("Name") not in {resource.name, f"/{resource.name}"}
        or not isinstance(payload.get("State"), dict)
    ):
        return None
    return payload["State"]


def _persist_lifecycle_result(
    store: ObserverEvidenceStore,
    *,
    purpose: str,
    result: CommandResult,
) -> tuple[str, str]:
    stdout = result.stdout.encode()
    stderr = result.stderr.encode()
    artifact = store.write_immutable(
        f"lifecycle/raw/{purpose}.json",
        {
            "schema_version": "phase0.lifecycle-execution-result.v1",
            "run_id": store.run_id,
            "purpose": purpose,
            "arguments": list(result.arguments),
            "exit_code": result.exit_code,
            "stdout_base64": base64.b64encode(stdout).decode("ascii"),
            "stdout_sha256": sha256_bytes(stdout),
            "stderr_base64": base64.b64encode(stderr).decode("ascii"),
            "stderr_sha256": sha256_bytes(stderr),
        },
    )
    return str(artifact.path), artifact.sha256


def load_current_resource_discovery(
    context: AuthenticatedOwnershipContext,
    evidence_store: ObserverEvidenceStore,
    *,
    artifact_path: str,
    artifact_sha256: str,
) -> CurrentResourceDiscovery:
    del context, evidence_store, artifact_path, artifact_sha256
    raise TypeError(
        "JSON discovery cannot issue readiness; use lifecycle execution receipt"
    )


def _load_legacy_current_resource_discovery(
    context: AuthenticatedOwnershipContext,
    evidence_store: ObserverEvidenceStore,
    *,
    artifact_path: str,
    artifact_sha256: str,
) -> CurrentResourceDiscovery:
    payload = _read_verified_observer_artifact(
        evidence_store,
        artifact_path,
        artifact_sha256,
    )
    if (
        not context.is_authentic()
        or evidence_store.run_id != context.run_id
        or set(payload)
        != {
            "schema_version",
            "run_id",
            "ownership_manifest_sha256",
            "docker_endpoint",
            "command_artifacts",
        }
        or payload.get("schema_version") != "phase0.current-resource-discovery-index.v1"
        or payload.get("run_id") != context.run_id
        or payload.get("ownership_manifest_sha256") != context.manifest_sha256
        or not isinstance(payload.get("docker_endpoint"), str)
        or not payload["docker_endpoint"]
        or not isinstance(payload.get("command_artifacts"), dict)
    ):
        raise ValueError("resource discovery evidence is unauthenticated")
    expected_invocations = build_ownership_discovery_invocations(
        project_root=Path("."),
        run_id=context.run_id,
        docker_endpoint=payload["docker_endpoint"],
    )
    references = payload["command_artifacts"]
    if set(references) != {invocation.purpose for invocation in expected_invocations}:
        raise ValueError("resource discovery command set is incomplete")
    parsed: dict[tuple[str, str], tuple[OwnedResource, ...]] = {}
    supporting: list[tuple[str, str]] = []
    for invocation in expected_invocations:
        reference = references[invocation.purpose]
        result = _read_command_result(
            evidence_store,
            reference,
            run_id=context.run_id,
            purpose=invocation.purpose,
            arguments=invocation.arguments,
        )
        supporting.append((reference["path"], reference["sha256"]))
        scope, plural = invocation.purpose.split("_", 1)
        kind = plural.removesuffix("s")
        required_labels = {"com.docker.compose.project": PROJECT_NAMESPACE}
        if scope == "owned":
            required_labels.update(
                {
                    PROJECT_LABEL: PROJECT_NAMESPACE,
                    RUN_LABEL: context.run_id,
                }
            )
        parsed[(scope, kind)] = _parse_owned_resources(
            result,
            kind=kind,
            required_labels=required_labels,
        )
    resources: list[OwnedResource] = []
    for kind in ("container", "network", "volume"):
        if parsed[("potential", kind)] != parsed[("owned", kind)]:
            raise ValueError("potential and owned resource discovery differs")
        resources.extend(parsed[("owned", kind)])
    frozen_resources = tuple(
        sorted(
            resources,
            key=lambda resource: (
                resource.kind,
                resource.name,
                resource.resource_id,
            ),
        )
    )
    verify_owned_resources(frozen_resources, context.manifest)
    return CurrentResourceDiscovery(
        _token=_DISCOVERY_TOKEN,
        run_id=context.run_id,
        complete_no_trunc=True,
        resources=frozen_resources,
        evidence_artifact=artifact_path,
        evidence_sha256=artifact_sha256,
        supporting_artifacts=tuple(supporting),
    )


def load_service_readiness_proof(
    context: AuthenticatedOwnershipContext,
    evidence_store: ObserverEvidenceStore,
    *,
    artifact_path: str,
    artifact_sha256: str,
) -> ServiceReadinessProof:
    del context, evidence_store, artifact_path, artifact_sha256
    raise TypeError(
        "JSON service proof cannot issue readiness; use lifecycle execution receipt"
    )


def _load_legacy_service_readiness_proof(
    context: AuthenticatedOwnershipContext,
    evidence_store: ObserverEvidenceStore,
    *,
    artifact_path: str,
    artifact_sha256: str,
) -> ServiceReadinessProof:
    payload = _read_verified_observer_artifact(
        evidence_store,
        artifact_path,
        artifact_sha256,
    )
    if (
        not context.is_authentic()
        or evidence_store.run_id != context.run_id
        or set(payload)
        != {
            "schema_version",
            "run_id",
            "ownership_manifest_sha256",
            "service",
            "source",
            "status_artifact",
            "signal_artifact",
        }
        or payload.get("schema_version") != "phase0.service-readiness-index.v1"
        or payload.get("run_id") != context.run_id
        or payload.get("ownership_manifest_sha256") != context.manifest_sha256
        or payload.get("service") not in {"load-generator", "otel-collector"}
        or not isinstance(payload.get("status_artifact"), dict)
        or not isinstance(payload.get("signal_artifact"), dict)
    ):
        raise ValueError("service readiness proof is unauthenticated")
    service = payload["service"]
    status_reference = payload["status_artifact"]
    signal_reference = payload["signal_artifact"]
    status_stdout = _read_command_result(
        evidence_store,
        status_reference,
        run_id=context.run_id,
        purpose=f"{service}_status",
        arguments=(
            "docker",
            "inspect",
            "--format",
            "{{json .State}}",
            service,
        ),
    )
    try:
        status = json.loads(status_stdout)
    except json.JSONDecodeError as error:
        raise ValueError("service status raw output is invalid") from error
    if (
        not isinstance(status, dict)
        or set(status) != {"run_id", "service", "running", "health_status"}
        or status.get("run_id") != context.run_id
        or status.get("service") != service
    ):
        raise ValueError("service status identity differs")
    source = payload.get("source")
    allowed_arguments = {
        ("load-generator", "load_generator_contract"): (
            "GET",
            "/load-generator/ready",
        ),
        ("load-generator", "emitted_traffic"): (
            "QUERY",
            "load-generator-emitted-traffic",
        ),
        ("otel-collector", "pipeline_ingestion"): (
            "QUERY",
            "collector-pipeline-ingestion",
        ),
    }
    signal_arguments = allowed_arguments.get((service, source))
    if signal_arguments is None:
        raise ValueError("service readiness source is not attributable")
    signal_stdout = _read_command_result(
        evidence_store,
        signal_reference,
        run_id=context.run_id,
        purpose=f"{service}_{source}",
        arguments=signal_arguments,
    )
    try:
        signal = json.loads(signal_stdout)
    except json.JSONDecodeError as error:
        raise ValueError("service signal raw output is invalid") from error
    signal_proven = (
        isinstance(signal, dict)
        and signal.get("run_id") == context.run_id
        and signal.get("service") == service
        and (
            (
                service == "load-generator"
                and set(signal)
                == {
                    "run_id",
                    "service",
                    "getads_requests_emitted",
                    "readiness_contract",
                }
                and isinstance(signal.get("getads_requests_emitted"), int)
                and not isinstance(signal["getads_requests_emitted"], bool)
                and signal.get("getads_requests_emitted", 0) > 0
                and signal.get("readiness_contract") is True
            )
            or (
                service == "otel-collector"
                and set(signal)
                == {
                    "run_id",
                    "service",
                    "pipeline",
                    "ingested_records",
                }
                and signal.get("pipeline") == "traces->spanmetrics"
                and isinstance(signal.get("ingested_records"), int)
                and not isinstance(signal["ingested_records"], bool)
                and signal.get("ingested_records", 0) > 0
            )
        )
    )
    supporting = (
        (status_reference["path"], status_reference["sha256"]),
        (signal_reference["path"], signal_reference["sha256"]),
    )
    return ServiceReadinessProof(
        _token=_SERVICE_PROOF_TOKEN,
        run_id=context.run_id,
        service=service,
        running=status.get("running") is True,
        healthy=status.get("health_status") == "healthy",
        attributable_current_run_evidence=signal_proven,
        source=source,
        evidence_artifact=artifact_path,
        evidence_sha256=artifact_sha256,
        supporting_artifacts=supporting,
    )


def evaluate_ownership_resources(
    context: AuthenticatedOwnershipContext,
    discovery: CurrentResourceDiscovery,
    *,
    registry_capability: FrozenTelemetryQueryCapability,
    evidence_store: ObserverEvidenceStore,
) -> ReadinessGate:
    reason = "OWNERSHIP_RESOURCES_COMPLETE"
    passed = False
    if (
        not isinstance(context, AuthenticatedOwnershipContext)
        or not context.is_authentic()
        or not _discovery_is_authentic(discovery)
        or discovery.run_id != context.run_id
        or not _readiness_authority_is_valid(
            context,
            registry_capability,
            evidence_store,
        )
        or not _artifact_matches(
            evidence_store,
            discovery.evidence_artifact,
            discovery.evidence_sha256,
        )
        or not all(
            _artifact_matches(evidence_store, path, digest)
            for path, digest in discovery.supporting_artifacts
        )
    ):
        reason = "RESOURCE_OWNERSHIP_UNKNOWN"
    elif not discovery.complete_no_trunc:
        reason = "RESOURCE_DISCOVERY_INCOMPLETE"
    else:
        try:
            verify_owned_resources(discovery.resources, context.manifest)
        except (OwnershipError, ValueError):
            reason = "RESOURCE_OWNERSHIP_UNKNOWN"
        else:
            passed = True
    return _issue_gate(
        context=context,
        registry_capability=registry_capability,
        evidence_store=evidence_store,
        name=ReadinessGateName.OWNERSHIP_RESOURCES_COMPLETE,
        passed=passed,
        reason=reason,
        evidence_sha256=(
            (discovery.evidence_artifact, discovery.evidence_sha256),
            *discovery.supporting_artifacts,
        ),
    )


def evaluate_load_generator_readiness(
    context: AuthenticatedOwnershipContext,
    proof: ServiceReadinessProof,
    *,
    registry_capability: FrozenTelemetryQueryCapability,
    evidence_store: ObserverEvidenceStore,
) -> ReadinessGate:
    return _evaluate_service(
        context,
        proof,
        service="load-generator",
        sources={"adapter_receipt"},
        name=ReadinessGateName.LOAD_GENERATOR_READY,
        registry_capability=registry_capability,
        evidence_store=evidence_store,
    )


def evaluate_collector_readiness(
    context: AuthenticatedOwnershipContext,
    proof: ServiceReadinessProof,
    *,
    registry_capability: FrozenTelemetryQueryCapability,
    evidence_store: ObserverEvidenceStore,
) -> ReadinessGate:
    return _evaluate_service(
        context,
        proof,
        service="otel-collector",
        sources={"adapter_receipt"},
        name=ReadinessGateName.COLLECTOR_READY,
        registry_capability=registry_capability,
        evidence_store=evidence_store,
    )


def evaluate_backend_readiness(
    name: ReadinessGateName,
    window: PhaseWindow,
    result: _BackendResult,
    *,
    context: AuthenticatedOwnershipContext,
    registry_capability: FrozenTelemetryQueryCapability,
    evidence_store: ObserverEvidenceStore,
) -> ReadinessGate:
    if name not in {
        ReadinessGateName.PROMETHEUS_FRESH,
        ReadinessGateName.JAEGER_FRESH,
        ReadinessGateName.OPENSEARCH_FRESH,
    }:
        raise ValueError("backend readiness factory requires a telemetry gate")
    receipt_validator = getattr(result, "is_production_receipt", None)
    production_receipt = False
    if callable(receipt_validator):
        try:
            production_receipt = receipt_validator(
                capability=registry_capability,
                store=evidence_store,
                window=window,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            production_receipt = False
    reason_value = getattr(result.reason, "value", str(result.reason))
    identity_matches = (
        result.run_id == window.run_id == context.run_id
        and result.cycle_number == window.cycle_number
        and result.phase == window.scenario_phase.value
        and result.fixture_sha256 == registry_capability.content_sha256
    )
    hashes = tuple(result.artifact_sha256)
    artifacts_match = (
        tuple(path for path, _digest in hashes) == tuple(result.artifact_paths)
        and bool(hashes)
        and _backend_artifacts_match(
            evidence_store,
            hashes,
            name=name,
            window=window,
            capability=registry_capability,
        )
    )
    authority_valid = _readiness_authority_is_valid(
        context,
        registry_capability,
        evidence_store,
    )
    passed = (
        bool(result.ready)
        and production_receipt
        and identity_matches
        and artifacts_match
        and authority_valid
    )
    reason = (
        str(reason_value)
        if passed
        else (
            "BACKEND_PROVENANCE_MISMATCH"
            if not production_receipt or not identity_matches or not authority_valid
            else "EVIDENCE_PERSISTENCE_FAILED"
        )
    )
    return _issue_gate(
        context=context,
        registry_capability=registry_capability,
        evidence_store=evidence_store,
        name=name,
        passed=passed,
        reason=reason,
        cycle_number=window.cycle_number,
        phase=window.scenario_phase.value,
        evidence_sha256=hashes if artifacts_match else (),
    )


def _evaluate_service(
    context: AuthenticatedOwnershipContext,
    proof: ServiceReadinessProof,
    *,
    service: str,
    sources: set[str],
    name: ReadinessGateName,
    registry_capability: FrozenTelemetryQueryCapability,
    evidence_store: ObserverEvidenceStore,
) -> ReadinessGate:
    resource_present = any(
        resource.kind == "container"
        and resource.labels.get(_COMPOSE_SERVICE_LABEL) == service
        for resource in context.manifest.resources
    )
    artifact_matches = _artifact_matches(
        evidence_store,
        proof.evidence_artifact,
        proof.evidence_sha256,
    ) and all(
        _artifact_matches(evidence_store, path, digest)
        for path, digest in proof.supporting_artifacts
    )
    passed = (
        isinstance(context, AuthenticatedOwnershipContext)
        and context.is_authentic()
        and _service_proof_is_authentic(proof)
        and proof.run_id == context.run_id
        and proof.service == service
        and proof.source in sources
        and resource_present
        and proof.running
        and proof.healthy
        and proof.attributable_current_run_evidence
        and artifact_matches
        and _readiness_authority_is_valid(
            context,
            registry_capability,
            evidence_store,
        )
    )
    return _issue_gate(
        context=context,
        registry_capability=registry_capability,
        evidence_store=evidence_store,
        name=name,
        passed=passed,
        reason=(
            f"{name.value.upper()}_PROVEN"
            if passed
            else f"{name.value.upper()}_INCOMPLETE"
        ),
        evidence_sha256=(
            (
                (proof.evidence_artifact, proof.evidence_sha256),
                *proof.supporting_artifacts,
            )
            if artifact_matches
            else ()
        ),
    )


def _issue_gate(
    *,
    context: AuthenticatedOwnershipContext,
    registry_capability: FrozenTelemetryQueryCapability,
    evidence_store: ObserverEvidenceStore,
    name: ReadinessGateName,
    passed: bool,
    reason: str,
    evidence_sha256: tuple[tuple[str, str], ...],
    cycle_number: int | None = None,
    phase: str | None = None,
) -> ReadinessGate:
    return ReadinessGate(
        _token=_READINESS_GATE_TOKEN,
        run_id=context.run_id,
        name=name,
        passed=passed,
        reason=reason,
        cycle_number=cycle_number,
        phase=phase,
        fixture_sha256=registry_capability.content_sha256,
        ownership_manifest_sha256=context.manifest_sha256,
        evidence_sha256=evidence_sha256,
        store_root=evidence_store.root,
    )


def build_readiness_handoff(
    *,
    context: AuthenticatedOwnershipContext,
    gates: tuple[ReadinessGate, ...],
    evidence_store: ObserverEvidenceStore,
    registry_capability: FrozenTelemetryQueryCapability,
) -> ReadinessHandoff:
    if (
        not isinstance(context, AuthenticatedOwnershipContext)
        or not context.is_authentic()
    ):
        return ReadinessHandoff(
            reason="RESOURCE_OWNERSHIP_UNKNOWN",
            evidence=None,
        )
    if not _readiness_authority_is_valid(
        context,
        registry_capability,
        evidence_store,
    ):
        return ReadinessHandoff(
            reason="QUERY_FIXTURE_NOT_FROZEN",
            evidence=None,
        )
    if any(
        not _gate_is_authentic(
            gate,
            context,
            registry_capability,
            evidence_store,
        )
        for gate in gates
    ):
        return ReadinessHandoff(
            reason="READINESS_PROVENANCE_INVALID",
            evidence=None,
        )
    by_name: dict[ReadinessGateName, ReadinessGate] = {}
    for gate in gates:
        if gate.name in by_name:
            return ReadinessHandoff(
                reason="READINESS_GATE_DUPLICATED",
                evidence=None,
            )
        by_name[gate.name] = gate
    decisions = {
        name: by_name[name].passed if name in by_name else False
        for name in ReadinessGateName
    }
    evidence = ReadinessEvidence(
        schema_version="phase0.readiness-evidence.v1",
        run_id=context.run_id,
        ownership_resources_complete=decisions[
            ReadinessGateName.OWNERSHIP_RESOURCES_COMPLETE
        ],
        load_generator_ready=decisions[ReadinessGateName.LOAD_GENERATOR_READY],
        collector_ready=decisions[ReadinessGateName.COLLECTOR_READY],
        prometheus_fresh=decisions[ReadinessGateName.PROMETHEUS_FRESH],
        jaeger_fresh=decisions[ReadinessGateName.JAEGER_FRESH],
        opensearch_fresh=decisions[ReadinessGateName.OPENSEARCH_FRESH],
    )
    path = "lifecycle/readiness-evidence.json"
    payload = {
        **evidence.model_dump(mode="json"),
        "gate_decisions": {name.value: decisions[name] for name in ReadinessGateName},
        "gate_reasons": {
            name.value: (
                by_name[name].reason if name in by_name else "READINESS_GATE_MISSING"
            )
            for name in ReadinessGateName
        },
        "gate_artifacts": {
            name.value: (dict(by_name[name].evidence_sha256) if name in by_name else {})
            for name in ReadinessGateName
        },
        "ownership_manifest_sha256": context.manifest_sha256,
        "fixture_sha256": registry_capability.content_sha256,
    }
    paths: list[str] = []
    if not _write(evidence_store, path, payload, paths):
        return ReadinessHandoff(
            reason="EVIDENCE_PERSISTENCE_FAILED",
            evidence=None,
        )
    return ReadinessHandoff(
        reason="READINESS_COMPLETE" if evidence.all_passed else "READINESS_INCOMPLETE",
        evidence=evidence,
        artifact_path=paths[0],
    )


def _readiness_authority_is_valid(
    context: AuthenticatedOwnershipContext,
    registry_capability: FrozenTelemetryQueryCapability,
    evidence_store: ObserverEvidenceStore,
) -> bool:
    return (
        isinstance(context, AuthenticatedOwnershipContext)
        and context.is_authentic()
        and isinstance(registry_capability, FrozenTelemetryQueryCapability)
        and registry_capability.store is evidence_store
        and registry_capability.run_id == context.run_id == evidence_store.run_id
        and registry_capability.is_authentic()
    )


def _gate_is_authentic(
    gate: ReadinessGate,
    context: AuthenticatedOwnershipContext,
    registry_capability: FrozenTelemetryQueryCapability,
    evidence_store: ObserverEvidenceStore,
) -> bool:
    if (
        not isinstance(gate, ReadinessGate)
        or gate._token is not _READINESS_GATE_TOKEN
        or gate.run_id != context.run_id
        or gate.fixture_sha256 != registry_capability.content_sha256
        or gate.ownership_manifest_sha256 != context.manifest_sha256
        or gate._store_root != str(evidence_store.root)
        or not hmac.compare_digest(
            gate._integrity_hmac,
            _readiness_integrity(
                "gate",
                {
                    "run_id": gate.run_id,
                    "name": gate.name.value,
                    "passed": gate.passed,
                    "reason": gate.reason,
                    "cycle_number": gate.cycle_number,
                    "phase": gate.phase,
                    "fixture_sha256": gate.fixture_sha256,
                    "ownership_manifest_sha256": gate.ownership_manifest_sha256,
                    "evidence_sha256": [list(item) for item in gate.evidence_sha256],
                    "_store_root": gate._store_root,
                },
            ),
        )
        or (gate.passed and not gate.evidence_sha256)
    ):
        return False
    return all(
        _artifact_matches(evidence_store, path, digest)
        for path, digest in gate.evidence_sha256
    )


def _read_verified_observer_artifact(
    store: ObserverEvidenceStore,
    artifact_path: str,
    expected_sha256: str,
) -> dict[str, Any]:
    if not _artifact_matches(store, artifact_path, expected_sha256):
        raise ValueError("observer evidence path or hash is invalid")
    try:
        payload = json.loads(Path(artifact_path).read_bytes())
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("observer evidence is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("observer evidence must be a JSON object")
    return payload


def _read_command_result(
    store: ObserverEvidenceStore,
    reference: dict[str, Any],
    *,
    run_id: str,
    purpose: str,
    arguments: tuple[str, ...],
) -> str:
    if set(reference) != {"path", "sha256"}:
        raise ValueError("command artifact reference is invalid")
    payload = _read_verified_observer_artifact(
        store,
        reference["path"],
        reference["sha256"],
    )
    if (
        set(payload)
        != {
            "schema_version",
            "run_id",
            "purpose",
            "arguments",
            "exit_code",
            "stdout_base64",
            "stdout_sha256",
            "stderr_base64",
            "stderr_sha256",
        }
        or payload.get("schema_version") != "phase0.readiness-command-result.v1"
        or payload.get("run_id") != run_id
        or payload.get("purpose") != purpose
        or payload.get("arguments") != list(arguments)
        or payload.get("exit_code") != 0
        or not isinstance(payload.get("stdout_base64"), str)
        or not isinstance(payload.get("stdout_sha256"), str)
        or not isinstance(payload.get("stderr_base64"), str)
        or not isinstance(payload.get("stderr_sha256"), str)
    ):
        raise ValueError("readiness command evidence differs")
    try:
        stdout_bytes = base64.b64decode(payload["stdout_base64"], validate=True)
        stderr_bytes = base64.b64decode(payload["stderr_base64"], validate=True)
        stdout = stdout_bytes.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("readiness command stdout is invalid") from error
    if (
        sha256_bytes(stdout_bytes) != payload["stdout_sha256"]
        or sha256_bytes(stderr_bytes) != payload["stderr_sha256"]
    ):
        raise ValueError("readiness command output hash differs")
    return stdout


def _artifact_matches(
    store: ObserverEvidenceStore,
    artifact_path: str,
    expected_sha256: str,
) -> bool:
    if (
        not isinstance(store, ObserverEvidenceStore)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        return False
    try:
        root = store.root.resolve(strict=True)
        path = Path(artifact_path)
        resolved = path.resolve(strict=True)
        return (
            root in resolved.parents
            and resolved == path
            and sha256_file(path) == expected_sha256
        )
    except (OSError, RuntimeError, ValueError):
        return False


def _discovery_is_authentic(discovery: CurrentResourceDiscovery) -> bool:
    return (
        isinstance(discovery, CurrentResourceDiscovery)
        and discovery._token is _DISCOVERY_TOKEN
        and hmac.compare_digest(
            discovery._integrity_hmac,
            _readiness_integrity(
                "discovery",
                {
                    "run_id": discovery.run_id,
                    "complete_no_trunc": discovery.complete_no_trunc,
                    "resources": [
                        resource.model_dump(mode="json")
                        for resource in discovery.resources
                    ],
                    "evidence_artifact": discovery.evidence_artifact,
                    "evidence_sha256": discovery.evidence_sha256,
                    "supporting_artifacts": [
                        list(item) for item in discovery.supporting_artifacts
                    ],
                },
            ),
        )
    )


def _service_proof_is_authentic(proof: ServiceReadinessProof) -> bool:
    return (
        isinstance(proof, ServiceReadinessProof)
        and proof._token is _SERVICE_PROOF_TOKEN
        and hmac.compare_digest(
            proof._integrity_hmac,
            _readiness_integrity(
                "service",
                {
                    "run_id": proof.run_id,
                    "service": proof.service,
                    "running": proof.running,
                    "healthy": proof.healthy,
                    "attributable_current_run_evidence": (
                        proof.attributable_current_run_evidence
                    ),
                    "source": proof.source,
                    "evidence_artifact": proof.evidence_artifact,
                    "evidence_sha256": proof.evidence_sha256,
                    "supporting_artifacts": proof.supporting_artifacts,
                },
            ),
        )
    )


def _readiness_integrity(kind: str, payload: dict[str, Any]) -> str:
    return hmac.new(
        _READINESS_INTEGRITY_KEY,
        canonical_json_bytes({"kind": kind, "payload": payload}),
        hashlib.sha256,
    ).hexdigest()


def _backend_artifacts_match(
    store: ObserverEvidenceStore,
    artifacts: tuple[tuple[str, str], ...],
    *,
    name: ReadinessGateName,
    window: PhaseWindow,
    capability: FrozenTelemetryQueryCapability,
) -> bool:
    fixture_sha256 = capability.content_sha256
    expected_backend = {
        ReadinessGateName.PROMETHEUS_FRESH: "prometheus",
        ReadinessGateName.JAEGER_FRESH: "jaeger",
        ReadinessGateName.OPENSEARCH_FRESH: "opensearch",
    }[name]
    try:
        if len({path for path, _digest in artifacts}) != len(artifacts):
            return False
        payloads: dict[str, dict[str, Any]] = {}
        for path, digest in artifacts:
            payload = _read_verified_observer_artifact(store, path, digest)
            if (
                payload.get("run_id") != window.run_id
                or payload.get("cycle_number") != window.cycle_number
                or payload.get("scenario_phase") != window.scenario_phase.value
                or payload.get("fixture_sha256") != fixture_sha256
                or payload.get("backend") != expected_backend
            ):
                return False
            payloads[path] = payload
    except (OSError, RuntimeError, ValueError):
        return False

    raw_paths = {
        path
        for path, payload in payloads.items()
        if payload.get("schema_version") == "phase0.telemetry-raw.v1"
    }
    if not raw_paths or any(
        not _raw_backend_artifact_is_valid(payloads[path]) for path in raw_paths
    ):
        return False
    if not _backend_raw_payloads_match_frozen_contract(
        payloads,
        raw_paths=raw_paths,
        name=name,
        window=window,
        capability=capability,
    ):
        return False

    terminal_schema = (
        "phase0.prometheus-measurement-decision.v1"
        if name is ReadinessGateName.PROMETHEUS_FRESH
        else "phase0.telemetry-gate-decision.v1"
    )
    terminal_paths = {
        path
        for path, payload in payloads.items()
        if payload.get("schema_version") == terminal_schema
    }
    if len(terminal_paths) != 1:
        return False
    terminal_path = next(iter(terminal_paths))
    terminal = payloads[terminal_path]
    if terminal.get("decision") is not True or terminal.get("reason") != "READY":
        return False

    if name is not ReadinessGateName.PROMETHEUS_FRESH:
        return (
            len(artifacts) == 2
            and len(raw_paths) == 1
            and _reference_resolves_to_artifact(
                terminal.get("raw_response_artifact"),
                raw_paths,
            )
        )

    parse_paths = {
        path
        for path, payload in payloads.items()
        if payload.get("schema_version") == "phase0.telemetry-parse-decision.v1"
    }
    expected_prior_paths = [path for path, _digest in artifacts[:-1]]
    if (
        not parse_paths
        or len(parse_paths) != len(raw_paths)
        or set(payloads) != raw_paths | parse_paths | {terminal_path}
        or terminal.get("raw_and_parse_artifacts") != expected_prior_paths
        or artifacts[-1][0] != terminal_path
    ):
        return False
    referenced_raw_paths: set[str] = set()
    for parse_path in parse_paths:
        decision = payloads[parse_path]
        if decision.get("decision") is not True or decision.get("reason") != "READY":
            return False
        raw_path = _resolve_artifact_reference(
            decision.get("raw_response_artifact"),
            raw_paths,
        )
        if raw_path is None or raw_path in referenced_raw_paths:
            return False
        referenced_raw_paths.add(raw_path)
    return referenced_raw_paths == raw_paths


def _backend_raw_payloads_match_frozen_contract(
    payloads: dict[str, dict[str, Any]],
    *,
    raw_paths: set[str],
    name: ReadinessGateName,
    window: PhaseWindow,
    capability: FrozenTelemetryQueryCapability,
) -> bool:
    registry = capability.registry
    try:
        bodies = {
            path: base64.b64decode(
                payloads[path]["raw_response_base64"],
                validate=True,
            )
            for path in raw_paths
        }
        if name is ReadinessGateName.PROMETHEUS_FRESH:
            from ecomsre.telemetry.prometheus import (
                _verify_prometheus_promotion_vector,
            )

            fixture = registry.prometheus
            expected_queries = {
                "total": fixture.total_query,
                "error": fixture.error_query,
                "target_incarnation": fixture.target_incarnation_query,
            }
            seen: dict[str, int] = {}
            for path in raw_paths:
                payload = payloads[path]
                query_kind = payload.get("query_kind")
                query = expected_queries.get(query_kind)
                if (
                    query is None
                    or payload.get("raw_query") != query
                    or payload.get("request_target")
                    != f"/api/v1/query?query={quote(query, safe='')}"
                ):
                    return False
                _verify_prometheus_promotion_vector(
                    json.loads(bodies[path]),
                    query_kind=query_kind,
                    registry=registry,
                    utc_window=(window.utc_started_at, window.utc_ended_at),
                )
                seen[query_kind] = seen.get(query_kind, 0) + 1
            return set(seen) == set(expected_queries) and len(set(seen.values())) == 1
        if name is ReadinessGateName.JAEGER_FRESH:
            from ecomsre.telemetry.jaeger import _select_span

            fixture = registry.jaeger
            assert fixture.service_identity is not None
            assert fixture.operation is not None
            expected_query = urlencode(
                {
                    "service": fixture.service_identity,
                    "operation": fixture.operation,
                    "start": int(window.utc_started_at.timestamp() * 1_000_000),
                    "end": int(window.utc_ended_at.timestamp() * 1_000_000),
                    "limit": 100,
                }
            )
            payload = payloads[next(iter(raw_paths))]
            if payload.get("exact_request") != f"/api/traces?{expected_query}":
                return False
            _select_span(
                bodies[next(iter(raw_paths))],
                service=fixture.service_identity,
                operation=fixture.operation,
                window=window,
            )
            return True
        from ecomsre.telemetry.opensearch import _select_log

        fixture = registry.opensearch
        assert fixture.index is not None
        assert fixture.service_identity_field is not None
        assert fixture.service_identity is not None
        assert fixture.timestamp_field is not None
        path = next(iter(raw_paths))
        payload = payloads[path]
        if payload.get("exact_request") != f"/{quote(fixture.index, safe='')}/_search":
            return False
        request_body = base64.b64decode(
            payload.get("request_body_base64"),
            validate=True,
        )
        if sha256_bytes(request_body) != payload.get("request_body_sha256"):
            return False
        expected_body = canonical_json_bytes(
            {
                "size": 100,
                "sort": [{fixture.timestamp_field: {"order": "asc"}}],
                "query": {
                    "bool": {
                        "filter": [
                            {
                                "term": {
                                    fixture.service_identity_field: (
                                        fixture.service_identity
                                    )
                                }
                            },
                            {
                                "range": {
                                    fixture.timestamp_field: {
                                        "gte": window.utc_started_at.isoformat(),
                                        "lte": window.utc_ended_at.isoformat(),
                                        "format": "strict_date_optional_time",
                                    }
                                }
                            },
                        ]
                    }
                },
            }
        )
        if request_body != expected_body:
            return False
        _select_log(
            bodies[path],
            service_field=fixture.service_identity_field,
            service=fixture.service_identity,
            timestamp_field=fixture.timestamp_field,
            trace_id_field=fixture.trace_id_field,
            span_id_field=fixture.span_id_field,
            window=window,
        )
        return True
    except (
        KeyError,
        OSError,
        OverflowError,
        RuntimeError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return False


def _raw_backend_artifact_is_valid(payload: dict[str, Any]) -> bool:
    try:
        body = base64.b64decode(payload.get("raw_response_base64"), validate=True)
    except (TypeError, ValueError):
        return False
    started = payload.get("monotonic_started_at")
    ended = payload.get("monotonic_ended_at")
    try:
        utc_started = datetime.fromisoformat(payload.get("started_at"))
        utc_ended = datetime.fromisoformat(payload.get("ended_at"))
    except (TypeError, ValueError):
        return False
    return (
        payload.get("http_status") == 200
        and payload.get("http_reason") == "OK"
        and payload.get("raw_response_partial") is False
        and isinstance(payload.get("raw_response_sha256"), str)
        and sha256_bytes(body) == payload["raw_response_sha256"]
        and isinstance(started, (int, float))
        and not isinstance(started, bool)
        and isinstance(ended, (int, float))
        and not isinstance(ended, bool)
        and math.isfinite(started)
        and math.isfinite(ended)
        and ended >= started
        and utc_started.tzinfo is not None
        and utc_ended.tzinfo is not None
        and utc_ended >= utc_started
    )


def _reference_resolves_to_artifact(
    reference: object,
    candidates: set[str],
) -> bool:
    return _resolve_artifact_reference(reference, candidates) is not None


def _resolve_artifact_reference(
    reference: object,
    candidates: set[str],
) -> str | None:
    if not isinstance(reference, str) or not reference:
        return None
    matches = {
        candidate
        for candidate in candidates
        if candidate == reference or candidate.endswith(f"/{reference}")
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _parse_probe(exchange: HttpExchange) -> _ParsedProbe:
    content_types = [
        value.casefold()
        for name, value in exchange.response_headers
        if name.casefold() == "content-type"
    ]
    if len(content_types) != 1 or "application/json" not in content_types[0]:
        raise TypeError("probe response is not JSON")
    payload = json.loads(exchange.raw_body, object_pairs_hook=_reject_duplicates)
    if not isinstance(payload, list) or not payload:
        raise TypeError("probe response contract is incomplete")
    if any(
        not isinstance(ad, dict)
        or set(ad) != {"redirectUrl", "text"}
        or not isinstance(ad["redirectUrl"], str)
        or not isinstance(ad["text"], str)
        for ad in payload
    ):
        raise TypeError("probe response contains an invalid Ad")
    return _ParsedProbe(
        trace_id=None,
        request_id=None,
        ad_items=len(payload),
    )


def _observer_input_boundary_passed(
    exchange: HttpExchange,
    fixture: Any,
) -> bool:
    envelope = exchange.observer_input_envelope
    return (
        envelope is not None
        and envelope.is_authentic(exchange.request)
        and exchange.request.method == fixture.method
        and exchange.request.target == fixture.path
        and exchange.request.body == b""
        and not exchange.request.headers
    )


def _observation_artifacts_are_bound(
    observation: ProbeObservation,
    *,
    store: ObserverEvidenceStore,
    capability: FrozenTelemetryQueryCapability,
    artifact_prefix: str,
) -> bool:
    expected_directory = (
        store.root / artifact_prefix / observation.phase / "telemetry" / "probe"
    )
    expected_relative_raw = (
        f"{artifact_prefix}/{observation.phase}/telemetry/probe/observation-raw.json"
    )
    expected_raw = expected_directory / "observation-raw.json"
    expected_decision = expected_directory / "observation-decision.json"
    if (
        tuple(path for path, _digest in observation.artifact_sha256)
        != observation.artifact_paths
        or len(observation.artifact_sha256) != 2
        or observation.artifact_paths
        != (
            str(expected_raw),
            str(expected_decision),
        )
    ):
        return False
    payloads: list[dict[str, Any]] = []
    for path, digest in observation.artifact_sha256:
        candidate = Path(path)
        if candidate.parent != expected_directory:
            return False
        try:
            payloads.append(_read_verified_observer_artifact(store, path, digest))
        except ValueError:
            return False
    by_schema = {payload.get("schema_version"): payload for payload in payloads}
    raw = by_schema.get("phase0.probe-raw.v1")
    decision = by_schema.get("phase0.probe-decision.v1")
    return (
        raw is not None
        and decision is not None
        and set(raw)
        == {
            "schema_version",
            "run_id",
            "cycle_number",
            "scenario_phase",
            "fixture_version",
            "fixture_sha256",
            "upstream_commit",
            "compose_config_sha256",
            "sanitized_command",
            "fixed_input",
            "input_capability_schema",
            "unexpected_input_count",
            "observer_input_boundary_passed",
            "exact_local_request",
            "request_started_at",
            "response_ended_at",
            "monotonic_started_at",
            "monotonic_ended_at",
            "http_status",
            "transport_reason",
            "transport_exit_code",
            "raw_response_base64",
            "raw_response_sha256",
            "raw_response_partial",
        }
        and set(decision)
        == {
            "schema_version",
            "run_id",
            "cycle_number",
            "scenario_phase",
            "fixture_version",
            "fixture_sha256",
            "raw_response_artifact",
            "decision",
            "reason",
            "exit_code",
            "getads_attribution_proof_artifact",
            "parsed_trace_id",
            "parsed_request_id",
            "parsed_ad_items",
        }
        and _probe_raw_artifact_is_valid(
            raw,
            capability=capability,
            expected_relative_raw=expected_relative_raw,
        )
        and all(
            payload.get("run_id") == observation.run_id
            and payload.get("cycle_number") == observation.cycle_number
            and payload.get("scenario_phase") == observation.phase
            and payload.get("fixture_version") == observation.fixture_version
            and payload.get("fixture_sha256") == observation.fixture_sha256
            for payload in (raw, decision)
        )
        and decision.get("raw_response_artifact") == expected_relative_raw
        and decision.get("decision") is True
        and decision.get("reason") == ProbeReason.OBSERVED.value
        and decision.get("exit_code") == ProbeReason.OBSERVED.exit_code
        and decision.get("getads_attribution_proof_artifact")
        == capability.registry.probe.getads_proof_artifact
        and decision.get("parsed_trace_id") is None
        and decision.get("parsed_request_id") is None
        and isinstance(decision.get("parsed_ad_items"), int)
        and not isinstance(decision["parsed_ad_items"], bool)
        and decision["parsed_ad_items"] > 0
    )


def _probe_raw_artifact_is_valid(
    payload: dict[str, Any],
    *,
    capability: FrozenTelemetryQueryCapability,
    expected_relative_raw: str,
) -> bool:
    fixture = capability.registry.probe
    assert fixture.method is not None
    assert fixture.path is not None
    try:
        body = base64.b64decode(payload.get("raw_response_base64"), validate=True)
        utc_started = datetime.fromisoformat(payload.get("request_started_at"))
        utc_ended = datetime.fromisoformat(payload.get("response_ended_at"))
        ads = json.loads(body, object_pairs_hook=_reject_duplicates)
    except (
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return False
    monotonic_started = payload.get("monotonic_started_at")
    monotonic_ended = payload.get("monotonic_ended_at")
    return (
        payload.get("upstream_commit") == capability.registry.upstream_commit
        and payload.get("compose_config_sha256")
        == capability.registry.compose_config_sha256
        and payload.get("sanitized_command") == ["HTTP", fixture.method, fixture.path]
        and payload.get("fixed_input") == fixture.input
        and payload.get("input_capability_schema") == "phase0.probe-observer-input.v1"
        and payload.get("unexpected_input_count") == 0
        and payload.get("observer_input_boundary_passed") is True
        and payload.get("exact_local_request") == fixture.path
        and payload.get("http_status") == 200
        and payload.get("transport_reason") == HttpReason.OK.value
        and payload.get("transport_exit_code") == 0
        and payload.get("raw_response_partial") is False
        and sha256_bytes(body) == payload.get("raw_response_sha256")
        and isinstance(monotonic_started, (int, float))
        and not isinstance(monotonic_started, bool)
        and isinstance(monotonic_ended, (int, float))
        and not isinstance(monotonic_ended, bool)
        and math.isfinite(monotonic_started)
        and math.isfinite(monotonic_ended)
        and monotonic_ended >= monotonic_started
        and utc_started.tzinfo is not None
        and utc_ended.tzinfo is not None
        and utc_ended >= utc_started
        and isinstance(ads, list)
        and bool(ads)
        and all(
            isinstance(ad, dict)
            and set(ad) == {"redirectUrl", "text"}
            and isinstance(ad["redirectUrl"], str)
            and isinstance(ad["text"], str)
            for ad in ads
        )
        and expected_relative_raw.endswith(
            f"/{payload.get('scenario_phase')}/telemetry/probe/observation-raw.json"
        )
    )


def _http_reason(reason: HttpReason) -> ProbeReason:
    try:
        return ProbeReason(reason.value)
    except ValueError:
        return ProbeReason.HTTP_TRANSPORT_ERROR


def _write(
    store: _EvidenceStore,
    path: str,
    payload: dict[str, Any],
    paths: list[str],
    hashes: list[tuple[str, str]] | None = None,
) -> bool:
    try:
        artifact = store.write_immutable(path, payload)
    except (OSError, RuntimeError, ValueError):
        return False
    paths.append(str(artifact.path))
    if hashes is not None:
        hashes.append((str(artifact.path), artifact.sha256))
    return True


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
