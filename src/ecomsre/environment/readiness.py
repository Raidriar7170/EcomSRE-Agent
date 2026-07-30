"""Fresh production readiness composition for one Phase 0 command process."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlencode

from pydantic import BaseModel, ConfigDict, Field

from ecomsre.environment.lifecycle import ReadinessEvidence
from ecomsre.environment.ownership_authority import AuthenticatedOwnershipContext
from ecomsre.environment.preflight import AuthenticatedPreflightEvidence
from ecomsre.evidence.store import ObserverEvidenceStore
from ecomsre.evidence.hashes import canonical_json_bytes
from ecomsre.phase0.models import MeasurementPhase
from ecomsre.telemetry.http import (
    HttpRequest,
    OwnedEndpoint,
    OwnedHttpClient,
    PhaseWindow,
)
from ecomsre.telemetry.jaeger import JaegerAdapter
from ecomsre.telemetry.opensearch import OpenSearchAdapter
from ecomsre.telemetry.probe import (
    ReadinessGateName,
    acquire_collector_pipeline_receipt,
    acquire_load_generator_telemetry_receipt,
    build_readiness_handoff,
    create_authenticated_lifecycle_runner,
    derive_current_resource_discovery,
    derive_service_readiness_proof,
    evaluate_backend_readiness,
    evaluate_collector_readiness,
    evaluate_load_generator_readiness,
    evaluate_ownership_resources,
    execute_lifecycle_readiness,
    _jaeger_trace_proves_load_generator_and_getads,
    _parse_service_container_inspect,
)
from ecomsre.environment.ownership import verify_owned_resources
from ecomsre.telemetry.prometheus import _verify_direct_ad_array
from ecomsre.telemetry.prometheus import (
    FixtureState,
    PrometheusAdapter,
    load_query_registry,
    revalidate_frozen_query_capability,
)


class ReadinessCollectionError(RuntimeError):
    """Fresh readiness could not be proven without widening authority."""


class CandidateInitialReadiness(BaseModel):
    """Pre-control endpoint/lifecycle proof that makes no frozen-query claim."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "phase0.candidate-initial-readiness.v1"
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ownership_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    purpose: Literal["INITIAL", "CONTROL_MUTATION"] = "INITIAL"
    endpoint_gates: dict[str, bool]
    propagation_authority: Literal["CANDIDATE_OWNED_CURRENT_RUN"]
    attempt_count: int = Field(ge=1, le=6)
    max_attempts: Literal[6] = 6
    window_started_at: datetime
    window_ended_at: datetime
    propagation_gates: dict[str, bool]
    raw_artifacts: tuple[str, ...]
    registry_frozen_claimed: bool = False
    lifecycle_artifact: str
    lifecycle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_artifact: str
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def ready(self) -> bool:
        return (
            set(self.endpoint_gates)
            == {"prometheus", "jaeger", "opensearch", "probe"}
            and (
                all(self.endpoint_gates.values())
                if self.purpose == "INITIAL"
                else all(
                    self.endpoint_gates[name]
                    for name in ("prometheus", "jaeger", "opensearch")
                )
            )
            and set(self.propagation_gates)
            == {
                "prometheus_ad_getads_current",
                "jaeger_load_to_ad_getads_current",
                "opensearch_ad_log_current",
                "load_generator_healthy",
                "otel_collector_healthy",
            }
            and all(self.propagation_gates.values())
            and bool(self.raw_artifacts)
            and not self.registry_frozen_claimed
        )


class CandidateReadinessPolicy(BaseModel):
    """Frozen budget for pre-Task7 current-run propagation evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: Literal[6] = 6
    retry_interval_seconds: Literal[5.0] = 5.0
    window_seconds: Literal[60] = 60


def collect_candidate_initial_readiness(
    *,
    project_root: Path,
    artifacts_root: Path,
    preflight: AuthenticatedPreflightEvidence,
    ownership: AuthenticatedOwnershipContext,
    purpose: Literal["INITIAL", "CONTROL_MUTATION"] = "INITIAL",
    retry_sleep: Callable[[float], None] = time.sleep,
) -> CandidateInitialReadiness:
    """Prove lifecycle ownership and candidate endpoints before any control write."""
    if (
        not preflight.is_current()
        or not ownership.is_authentic()
        or preflight.run_id != ownership.run_id
    ):
        raise ReadinessCollectionError("INITIAL_READINESS_AUTHORITY_INVALID")
    (
        lifecycle_artifact,
        lifecycle_sha256,
        lifecycle_gates,
    ) = _verify_initial_lifecycle_ownership(
        project_root=project_root,
        artifacts_root=artifacts_root,
        preflight=preflight,
        ownership=ownership,
    )
    base_urls = _owned_base_urls(ownership)
    client = OwnedHttpClient(context=ownership)
    policy = CandidateReadinessPolicy()
    started_at = datetime.now(UTC)
    monotonic_started = time.monotonic()
    window = PhaseWindow(
        run_id=ownership.run_id,
        cycle_number=1,
        scenario_phase=MeasurementPhase.BASELINE,
        utc_started_at=started_at,
        utc_ended_at=started_at + timedelta(seconds=policy.window_seconds),
        monotonic_started_at=monotonic_started,
        monotonic_ended_at=monotonic_started + policy.window_seconds,
    )
    gates = {name: False for name in ("prometheus", "jaeger", "opensearch", "probe")}
    propagation = {
        "prometheus_ad_getads_current": False,
        "jaeger_load_to_ad_getads_current": False,
        "opensearch_ad_log_current": False,
        **lifecycle_gates,
    }
    raw_artifacts: list[str] = []
    attempt_count = 0
    with ObserverEvidenceStore(artifacts_root, ownership.run_id) as store:
        prefix = f"lifecycle/initial-readiness/{time.monotonic_ns()}"
        for attempt in range(1, policy.max_attempts + 1):
            attempt_count = attempt
            exchanges = _candidate_signal_exchanges(
                client=client,
                base_urls=base_urls,
                window=window,
            )
            for name, exchange in exchanges.items():
                raw = store.write_immutable(
                    f"{prefix}/attempt-{attempt:02d}-{name}-raw.json",
                    {
                        "schema_version": "phase0.candidate-signal-raw.v1",
                        "run_id": ownership.run_id,
                        "attempt": attempt,
                        "endpoint": name,
                        "request_method": exchange.request.method,
                        "request_target": exchange.request.target,
                        "request_started_at": exchange.started_at.isoformat(),
                        "response_ended_at": exchange.ended_at.isoformat(),
                        "http_status": exchange.status_code,
                        "transport_reason": exchange.reason.value,
                        "raw_response_base64": base64.b64encode(
                            exchange.raw_body
                        ).decode("ascii"),
                        "raw_response_sha256": exchange.raw_sha256,
                    },
                )
                raw_artifacts.append(str(raw.path))
                gates[name] = _candidate_endpoint_ready(
                    name,
                    exchange,
                    window=window,
                )
            propagation.update(
                {
                    "prometheus_ad_getads_current": gates["prometheus"],
                    "jaeger_load_to_ad_getads_current": gates["jaeger"],
                    "opensearch_ad_log_current": gates["opensearch"],
                }
            )
            endpoints_ready = (
                all(gates.values())
                if purpose == "INITIAL"
                else all(
                    gates[name]
                    for name in ("prometheus", "jaeger", "opensearch")
                )
            )
            if endpoints_ready and all(propagation.values()):
                break
            if (
                attempt < policy.max_attempts
                and time.monotonic() + policy.retry_interval_seconds
                < window.monotonic_ended_at
            ):
                retry_sleep(policy.retry_interval_seconds)
        summary_payload = {
            "schema_version": "phase0.candidate-initial-readiness.v1",
            "run_id": ownership.run_id,
            "preflight_sha256": preflight.content_sha256,
            "ownership_manifest_sha256": ownership.manifest_sha256,
            "purpose": purpose,
            "endpoint_gates": gates,
            "propagation_authority": "CANDIDATE_OWNED_CURRENT_RUN",
            "attempt_count": attempt_count,
            "max_attempts": policy.max_attempts,
            "window_started_at": window.utc_started_at.isoformat(),
            "window_ended_at": window.utc_ended_at.isoformat(),
            "propagation_gates": propagation,
            "registry_frozen_claimed": False,
            "lifecycle_artifact": lifecycle_artifact,
            "lifecycle_sha256": lifecycle_sha256,
            "raw_artifacts": raw_artifacts,
        }
        summary = store.write_immutable(f"{prefix}/summary.json", summary_payload)
    evidence = CandidateInitialReadiness(
        run_id=ownership.run_id,
        preflight_sha256=preflight.content_sha256,
        ownership_manifest_sha256=ownership.manifest_sha256,
        purpose=purpose,
        endpoint_gates=gates,
        propagation_authority="CANDIDATE_OWNED_CURRENT_RUN",
        attempt_count=attempt_count,
        window_started_at=window.utc_started_at,
        window_ended_at=window.utc_ended_at,
        propagation_gates=propagation,
        raw_artifacts=tuple(raw_artifacts),
        registry_frozen_claimed=False,
        lifecycle_artifact=lifecycle_artifact,
        lifecycle_sha256=lifecycle_sha256,
        evidence_artifact=str(summary.path),
        evidence_sha256=summary.sha256,
    )
    if not evidence.ready:
        raise ReadinessCollectionError("INITIAL_CANDIDATE_READINESS_INCOMPLETE")
    return evidence


def _candidate_signal_exchanges(
    *,
    client: OwnedHttpClient,
    base_urls: dict[str, str],
    window: PhaseWindow,
) -> dict[str, object]:
    prometheus_query = (
        'traces_span_metrics_calls_total{service_name="ad",'
        'span_name="oteldemo.AdService/GetAds"}'
    )
    opensearch_body = canonical_json_bytes(
        {
            "size": 100,
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"resource.service.name": "ad"}},
                        {
                            "range": {
                                "@timestamp": {
                                    "gte": window.utc_started_at.isoformat(),
                                    "lte": window.utc_ended_at.isoformat(),
                                }
                            }
                        },
                    ]
                }
            },
        }
    )
    targets = {
        "prometheus": HttpRequest(
            endpoint=OwnedEndpoint(
                base_url=base_urls["prometheus"],
                service="prometheus",
                target_port=9090,
            ),
            method="GET",
            target=f"/api/v1/query?query={quote(prometheus_query, safe='')}",
            absolute_deadline_monotonic=window.monotonic_ended_at,
        ),
        "jaeger": HttpRequest(
            endpoint=OwnedEndpoint(
                base_url=base_urls["jaeger"],
                service="jaeger",
                target_port=16686,
            ),
            method="GET",
            target="/api/traces?"
            + urlencode(
                {
                    "service": "load-generator",
                    "operation": "user_get_ads",
                    "start": int(
                        window.utc_started_at.timestamp() * 1_000_000
                    ),
                    "end": int(window.utc_ended_at.timestamp() * 1_000_000),
                    "limit": 100,
                }
            ),
            absolute_deadline_monotonic=window.monotonic_ended_at,
        ),
        "opensearch": HttpRequest(
            endpoint=OwnedEndpoint(
                base_url=base_urls["opensearch"],
                service="opensearch",
                target_port=9200,
            ),
            method="POST",
            target="/otel-logs-*/_search",
            headers=(
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(opensearch_body))),
            ),
            body=opensearch_body,
            absolute_deadline_monotonic=window.monotonic_ended_at,
        ),
        "probe": HttpRequest(
            endpoint=OwnedEndpoint(
                base_url=base_urls["probe"],
                service="frontend-proxy",
                target_port=8080,
            ),
            method="GET",
            target="/api/data?contextKeys=telescopes",
            absolute_deadline_monotonic=window.monotonic_ended_at,
        ),
    }
    return {name: client.request(request) for name, request in targets.items()}


def _candidate_endpoint_ready(
    name: str,
    exchange: object,
    *,
    window: PhaseWindow,
) -> bool:
    if not getattr(exchange, "succeeded", False):
        return False
    body = exchange.raw_body
    try:
        payload = json.loads(body)
        if name == "prometheus":
            return _candidate_prometheus_has_current_ad_getads(payload, window)
        if name == "jaeger":
            return (
                _jaeger_trace_proves_load_generator_and_getads(
                    payload,
                    window=window,
                )
                is not None
            )
        if name == "opensearch":
            return _candidate_opensearch_has_current_ad_log(payload, window)
        _verify_direct_ad_array(body)
        return True
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        return False


def _candidate_prometheus_has_current_ad_getads(
    payload: object,
    window: PhaseWindow,
) -> bool:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return False
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("resultType") != "vector":
        return False
    result = data.get("result")
    if not isinstance(result, list):
        return False
    for item in result:
        if not isinstance(item, dict) or not isinstance(item.get("metric"), dict):
            continue
        metric = item["metric"]
        value = item.get("value")
        if (
            metric.get("service_name") != "ad"
            or metric.get("span_name") != "oteldemo.AdService/GetAds"
            or not isinstance(value, list)
            or len(value) != 2
            or isinstance(value[0], bool)
            or not isinstance(value[0], (int, float))
        ):
            continue
        observed = datetime.fromtimestamp(value[0], tz=UTC)
        if window.utc_started_at <= observed <= window.utc_ended_at:
            return True
    return False


def _candidate_opensearch_has_current_ad_log(
    payload: object,
    window: PhaseWindow,
) -> bool:
    if not isinstance(payload, dict):
        return False
    hits = payload.get("hits")
    if not isinstance(hits, dict) or not isinstance(hits.get("hits"), list):
        return False
    for hit in hits["hits"]:
        source = hit.get("_source") if isinstance(hit, dict) else None
        if not isinstance(source, dict):
            continue
        resource = source.get("resource")
        service = resource.get("service") if isinstance(resource, dict) else None
        timestamp = source.get("@timestamp")
        if (
            not isinstance(service, dict)
            or service.get("name") != "ad"
            or not isinstance(timestamp, str)
        ):
            continue
        try:
            observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        if window.utc_started_at <= observed <= window.utc_ended_at:
            return True
    return False


def _verify_initial_lifecycle_ownership(
    *,
    project_root: Path,
    artifacts_root: Path,
    preflight: AuthenticatedPreflightEvidence,
    ownership: AuthenticatedOwnershipContext,
) -> tuple[str, str, dict[str, bool]]:
    with ObserverEvidenceStore(artifacts_root, ownership.run_id) as store:
        prefix = f"lifecycle/initial-ownership/{time.monotonic_ns()}"
        runner = create_authenticated_lifecycle_runner(
            preflight=preflight,
            context=ownership,
            project_root=project_root,
            artifacts_root=artifacts_root,
        )
        execution = execute_lifecycle_readiness(
            runner,
            preflight=preflight,
            context=ownership,
        )
        discovery = derive_current_resource_discovery(
            ownership,
            store,
            execution,
            artifact_prefix=prefix,
        )
        verify_owned_resources(discovery.resources, ownership.manifest)
        service_gates = _candidate_lifecycle_service_gates(
            ownership,
            execution,
        )
        artifact = store.write_immutable(
            f"{prefix}/verified.json",
            {
                "schema_version": "phase0.initial-lifecycle-ownership.v1",
                "run_id": ownership.run_id,
                "manifest_sha256": ownership.manifest_sha256,
                "resource_count": len(discovery.resources),
                "service_gates": service_gates,
                "discovery_artifact": discovery.evidence_artifact,
                "discovery_sha256": discovery.evidence_sha256,
            },
        )
    return str(artifact.path), artifact.sha256, service_gates


def _candidate_lifecycle_service_gates(
    ownership: AuthenticatedOwnershipContext,
    execution: object,
) -> dict[str, bool]:
    by_purpose = dict(getattr(execution, "command_results", ()))
    gates: dict[str, bool] = {}
    for service, gate in (
        ("load-generator", "load_generator_healthy"),
        ("otel-collector", "otel_collector_healthy"),
    ):
        resources = [
            resource
            for resource in ownership.manifest.resources
            if resource.kind == "container"
            and resource.labels.get("com.docker.compose.service") == service
        ]
        result = by_purpose.get(f"{service}_status")
        state = (
            _parse_service_container_inspect(
                result.stdout,
                resource=resources[0],
            )
            if len(resources) == 1 and result is not None
            else None
        )
        health = state.get("Health") if isinstance(state, dict) else None
        gates[gate] = bool(
            isinstance(state, dict)
            and state.get("Running") is True
            and state.get("Status") == "running"
            and isinstance(health, dict)
            and health.get("Status") == "healthy"
        )
    return gates


def collect_fresh_readiness(
    *,
    project_root: Path,
    artifacts_root: Path,
    preflight: AuthenticatedPreflightEvidence,
    ownership: AuthenticatedOwnershipContext,
    boundary: str = "unspecified",
) -> ReadinessEvidence:
    if (
        not preflight.is_current()
        or not ownership.is_authentic()
        or preflight.run_id != ownership.run_id
    ):
        raise ReadinessCollectionError("READINESS_AUTHORITY_INVALID")
    registry_path = (
        Path(project_root)
        / "config"
        / "phase0"
        / "telemetry-queries-v3.0.0.json"
    )
    loaded = load_query_registry(registry_path)
    if loaded.registry.state is not FixtureState.FROZEN:
        raise ReadinessCollectionError("BLOCKED_TELEMETRY_FIXTURE_UNRESOLVED")
    base_urls = _owned_base_urls(ownership)
    now_utc = datetime.now(UTC)
    now_monotonic = time.monotonic()
    window = PhaseWindow(
        run_id=ownership.run_id,
        cycle_number=1,
        scenario_phase=MeasurementPhase.BASELINE,
        utc_started_at=now_utc,
        utc_ended_at=now_utc + timedelta(seconds=180),
        monotonic_started_at=now_monotonic,
        monotonic_ended_at=now_monotonic + 180,
    )
    if boundary not in {"post-promotion", "final", "unspecified"}:
        raise ReadinessCollectionError("READINESS_BOUNDARY_INVALID")
    session_prefix = f"readiness-sessions/{boundary}-{time.monotonic_ns()}"
    with ObserverEvidenceStore(artifacts_root, ownership.run_id) as store:
        client = OwnedHttpClient(context=ownership)
        capability = revalidate_frozen_query_capability(
            registry_path,
            evidence_store=store,
            client=client,
            window=window,
            probe_base_url=base_urls["probe"],
        )
        lifecycle_runner = create_authenticated_lifecycle_runner(
            preflight=preflight,
            context=ownership,
            project_root=project_root,
            artifacts_root=artifacts_root,
        )
        execution = execute_lifecycle_readiness(
            lifecycle_runner,
            preflight=preflight,
            context=ownership,
        )
        discovery = derive_current_resource_discovery(
            ownership,
            store,
            execution,
            artifact_prefix=session_prefix,
        )
        load_receipt = acquire_load_generator_telemetry_receipt(
            client=client,
            evidence_store=store,
            registry_capability=capability,
            window=window,
            jaeger_base_url=base_urls["jaeger"],
            artifact_prefix=session_prefix,
        )
        load_proof = derive_service_readiness_proof(
            ownership,
            store,
            execution,
            service="load-generator",
            telemetry_receipt=load_receipt,
            registry_capability=capability,
            window=window,
            artifact_prefix=session_prefix,
        )
        prometheus = PrometheusAdapter(
            client=client,
            evidence_store=store,
            fixture=capability,
        ).measure_getads(
            window=window,
            base_url=base_urls["prometheus"],
            artifact_prefix=session_prefix,
        )
        jaeger = JaegerAdapter(
            client=client,
            evidence_store=store,
            fixture=capability,
        ).check_readiness(
            window=window,
            base_url=base_urls["jaeger"],
            artifact_prefix=session_prefix,
        )
        opensearch = OpenSearchAdapter(
            client=client,
            evidence_store=store,
            fixture=capability,
        ).check_readiness(
            window=window,
            base_url=base_urls["opensearch"],
            artifact_prefix=session_prefix,
        )
        collector_receipt = acquire_collector_pipeline_receipt(
            client=client,
            evidence_store=store,
            registry_capability=capability,
            window=window,
            context=ownership,
            execution=execution,
            prometheus=prometheus,
            jaeger=jaeger,
            opensearch=opensearch,
            artifact_prefix=session_prefix,
        )
        collector_proof = derive_service_readiness_proof(
            ownership,
            store,
            execution,
            service="otel-collector",
            telemetry_receipt=collector_receipt,
            registry_capability=capability,
            window=window,
            artifact_prefix=session_prefix,
        )
        gates = (
            evaluate_ownership_resources(
                ownership,
                discovery,
                registry_capability=capability,
                evidence_store=store,
            ),
            evaluate_load_generator_readiness(
                ownership,
                load_proof,
                registry_capability=capability,
                evidence_store=store,
            ),
            evaluate_collector_readiness(
                ownership,
                collector_proof,
                registry_capability=capability,
                evidence_store=store,
            ),
            evaluate_backend_readiness(
                ReadinessGateName.PROMETHEUS_FRESH,
                window,
                prometheus,
                context=ownership,
                registry_capability=capability,
                evidence_store=store,
            ),
            evaluate_backend_readiness(
                ReadinessGateName.JAEGER_FRESH,
                window,
                jaeger,
                context=ownership,
                registry_capability=capability,
                evidence_store=store,
            ),
            evaluate_backend_readiness(
                ReadinessGateName.OPENSEARCH_FRESH,
                window,
                opensearch,
                context=ownership,
                registry_capability=capability,
                evidence_store=store,
            ),
        )
        handoff = build_readiness_handoff(
            context=ownership,
            gates=gates,
            evidence_store=store,
            registry_capability=capability,
            artifact_prefix=session_prefix,
        )
    if not handoff.ready or handoff.evidence is None:
        raise ReadinessCollectionError(handoff.reason)
    return handoff.evidence


def _owned_base_urls(
    ownership: AuthenticatedOwnershipContext,
) -> dict[str, str]:
    required = {
        "prometheus": ("prometheus", 9090),
        "jaeger": ("jaeger", 16686),
        "opensearch": ("opensearch", 9200),
        "probe": ("frontend-proxy", 8080),
    }
    resolved: dict[str, str] = {}
    for logical, (service, target) in required.items():
        matches: set[int] = set()
        for resource in ownership.manifest.resources:
            evidence = set(resource.identity_evidence)
            if (
                resource.kind != "port"
                or f"service:{service}" not in evidence
                or f"target_port:{target}" not in evidence
                or "protocol:tcp" not in evidence
                or not (
                    "host_ip:127.0.0.1" in evidence
                    or "host_ip:::1" in evidence
                )
            ):
                continue
            values = [
                value.removeprefix("published_port:")
                for value in evidence
                if value.startswith("published_port:")
            ]
            if len(values) == 1 and values[0].isdigit():
                matches.add(int(values[0]))
        if len(matches) != 1:
            raise ReadinessCollectionError(
                f"READINESS_ENDPOINT_UNPROVEN:{logical}"
            )
        resolved[logical] = f"http://127.0.0.1:{matches.pop()}"
    return resolved
