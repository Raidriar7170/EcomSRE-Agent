"""Exact-action Product connector bridge into frozen DTA v2.2 read contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from ecomsre.dta_v2.tool_contracts import (
    EndpointState,
    HealthState,
    ObservationStatus,
    RuntimeRecord,
    RuntimeState as RuntimeStateV2,
    ToolCounters,
    ReadAuthorityContext,
    build_fake_read_authority,
    build_inspect_service_runtime_request,
    build_read_tool_observation,
)
from ecomsre.dta_v2.v22.action_catalog import (
    EvidenceActionV22,
    StaticTopologyV22,
    build_action_catalog_v22,
    build_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.memory import MemoryReadOutcomeV22, RuntimeReadOutcomeV22
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    MetricFactV22,
    RecentChangeRecordV22,
    ReadRecordV22,
    ReadSourceStatusV22,
    ResourceUsageRecordV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    TraceSpanV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
from ecomsre.product.changes import ChangeEventRepositoryV1
from ecomsre.product.connectors.base import (
    ConnectorQueryContextV1,
    ConnectorQueryPurposeV1,
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.registry import ConnectorRegistryV1
from ecomsre.product.contracts import (
    ConnectorKindV1,
    EnvironmentRecordV1,
    ServiceIdentityMapV1,
)
from ecomsre.product.environment.capabilities import (
    EnvironmentCapabilityMatrixV1,
    SourceCapabilityStatusV1,
)
from ecomsre.product.incidents.contracts import IncidentRecordV1
from ecomsre.product.telemetry.metrics import ProductMetricsV1


_ALIAS_FIELD_BY_KIND = {
    ConnectorKindV1.PROMETHEUS: "prometheus",
    ConnectorKindV1.OPENSEARCH: "opensearch",
    ConnectorKindV1.JAEGER: "jaeger",
    ConnectorKindV1.HTTP_HEALTH: "http_health",
    ConnectorKindV1.PILOT_RUNTIME: None,
    ConnectorKindV1.FIXTURE: None,
}


@dataclass(frozen=True)
class ProductReadAcquisitionV1:
    raw_outcomes: tuple[ReadOutcomeV22, ...]
    memory_outcomes: tuple[MemoryReadOutcomeV22, ...]
    snapshots: tuple[dict[str, Any], ...]
    covered_services_by_source: dict[EvidenceSourceV22, tuple[str, ...]]
    capability_limitations: tuple[str, ...]


def _build_outcome(
    action: EvidenceActionV22,
    result: ConnectorQueryResultV1,
) -> ReadOutcomeV22:
    records: tuple[ReadRecordV22, ...] = result.records
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.read-outcome.v1",
        "action_id": action.action_id,
        "source": action.source,
        "request_sha256": action.request_sha256,
        "status": result.status,
        "records": records,
        "truncated": result.truncated,
    }
    draft = ReadOutcomeV22.model_construct(**payload, outcome_sha256="0" * 64)
    return ReadOutcomeV22.model_validate(
        {
            **payload,
            "outcome_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"outcome_sha256"})
            ),
        }
    )


def _maximum_records(action: EvidenceActionV22) -> int:
    request = action.request
    return int(
        request.max_results
        or request.max_records
        or request.max_spans
        or len(action.target_services)
    )


def _combine_results(
    *,
    action: EvidenceActionV22,
    window: ConnectorWindowV1,
    results: tuple[ConnectorQueryResultV1, ...],
) -> ConnectorQueryResultV1:
    if (
        not results
        or any(
            item.source is not action.source or item.window != window
            for item in results
        )
        or set().union(*(set(item.requested_services) for item in results))
        != set(action.target_services)
        or any(
            not set(item.requested_services).issubset(action.target_services)
            for item in results
        )
    ):
        return ConnectorQueryResultV1.build(
            source=action.source,
            status=ReadSourceStatusV22.FAILURE_SCHEMA,
            requested_services=action.target_services,
            covered_services=(),
            window=window,
            records=(),
            truncated=False,
            safe_error_code="CONNECTOR_ACTION_CONTRACT_INVALID",
            latency_ms=0.0,
        )
    failures = tuple(
        item
        for item in results
        if item.status
        not in {ReadSourceStatusV22.SUCCESS_EMPTY, ReadSourceStatusV22.SUCCESS_NONEMPTY}
    )
    if failures:
        first = failures[0]
        return ConnectorQueryResultV1.build(
            source=action.source,
            status=first.status,
            requested_services=action.target_services,
            covered_services=(),
            window=window,
            records=(),
            truncated=False,
            safe_error_code=first.safe_error_code or "CONNECTOR_SOURCE_FAILED",
            latency_ms=sum(item.latency_ms for item in results),
        )
    records = tuple(record for item in results for record in item.records)
    limit = _maximum_records(action)
    if len(records) > limit or not _records_match_action(action, records, window):
        return ConnectorQueryResultV1.build(
            source=action.source,
            status=ReadSourceStatusV22.FAILURE_SCHEMA,
            requested_services=action.target_services,
            covered_services=(),
            window=window,
            records=(),
            truncated=False,
            safe_error_code="CONNECTOR_ACTION_CONTRACT_INVALID",
            latency_ms=sum(item.latency_ms for item in results),
        )
    covered = tuple(
        sorted({service for item in results for service in item.covered_services})
    )
    if (
        action.source is not EvidenceSourceV22.TRACES
        and set(covered) - set(action.target_services)
    ):
        return ConnectorQueryResultV1.build(
            source=action.source,
            status=ReadSourceStatusV22.FAILURE_SCHEMA,
            requested_services=action.target_services,
            covered_services=(),
            window=window,
            records=(),
            truncated=False,
            safe_error_code="CONNECTOR_TARGET_SCOPE_EXCEEDED",
            latency_ms=sum(item.latency_ms for item in results),
        )
    return ConnectorQueryResultV1.build(
        source=action.source,
        status=(
            ReadSourceStatusV22.SUCCESS_NONEMPTY
            if records
            else ReadSourceStatusV22.SUCCESS_EMPTY
        ),
        requested_services=action.target_services,
        covered_services=covered,
        window=window,
        records=records,
        truncated=any(item.truncated for item in results),
        safe_error_code=None,
        latency_ms=sum(item.latency_ms for item in results),
    )


def _records_match_action(
    action: EvidenceActionV22,
    records: tuple[ReadRecordV22, ...],
    window: ConnectorWindowV1,
) -> bool:
    if not records:
        return True
    targets = set(action.target_services)
    request = action.request
    if action.source is EvidenceSourceV22.METRICS:
        metric_records = tuple(item for item in records if isinstance(item, MetricFactV22))
        return (
            len(metric_records) == len(records)
            and {item.metric_kind for item in metric_records} == set(request.metric_kinds)
            and {item.service for item in metric_records} == targets
            and all(
                item.window_started_at == window.started_at
                and item.window_ended_at == window.ended_at
                for item in metric_records
            )
        )
    if action.source is EvidenceSourceV22.LOGS:
        log_records = tuple(item for item in records if isinstance(item, LogRecordV22))
        return len(log_records) == len(records) and all(
            item.service in targets and window.started_at <= item.observed_at <= window.ended_at
            for item in log_records
        )
    if action.source is EvidenceSourceV22.TRACES:
        trace_records = tuple(item for item in records if isinstance(item, TraceSpanV22))
        hops = request.neighborhood_hops
        return len(trace_records) == len(records) and hops is not None and all(
            window.started_at <= item.observed_at <= window.ended_at
            and item.service in item.service_path
            and any(
                target in item.service_path
                and abs(item.service_path.index(target) - item.service_path.index(item.service))
                <= hops
                for target in targets.intersection(item.service_path)
            )
            for item in trace_records
        )
    if action.source is EvidenceSourceV22.RUNTIME:
        runtime_records = tuple(
            item for item in records if isinstance(item, RuntimeRecordV22)
        )
        return (
            len(runtime_records) == len(records)
            and {item.service for item in runtime_records} == targets
        )
    if action.source is EvidenceSourceV22.RESOURCES:
        resource_records = tuple(
            item for item in records if isinstance(item, ResourceUsageRecordV22)
        )
        return (
            len(resource_records) == len(records)
            and request.sampling_window_seconds is not None
            and request.sample_count is not None
            and {item.service for item in resource_records} == targets
            and all(
                item.sampling_window_seconds == request.sampling_window_seconds
                and len(item.samples) == request.sample_count
                for item in resource_records
            )
        )
    change_records = tuple(
        item for item in records if isinstance(item, RecentChangeRecordV22)
    )
    return len(change_records) == len(records) and all(
        item.service in targets and window.started_at <= item.observed_at <= window.ended_at
        for item in change_records
    )


def _runtime_memory(
    *,
    incident: IncidentRecordV1,
    action: EvidenceActionV22,
    outcome: ReadOutcomeV22,
    window: ConnectorWindowV1,
    latency_ms: float,
    authority: ReadAuthorityContext,
) -> RuntimeReadOutcomeV22:
    runtime_records = tuple(
        record for record in outcome.records if isinstance(record, RuntimeRecordV22)
    )
    source_records = tuple(
        RuntimeRecord(
            logical_service=record.service,
            owned_container_present=record.state is not RuntimeStateV22.ABSENT,
            state=RuntimeStateV2(record.state.value),
            health=(HealthState.HEALTHY if record.healthy else HealthState.UNHEALTHY),
            restart_count=record.restart_count,
            exit_code=(0 if record.state is RuntimeStateV22.RUNNING else None),
            endpoint_probe_performed=record.state is RuntimeStateV22.RUNNING,
            endpoint_state=(
                EndpointState.READY
                if record.healthy
                else (
                    EndpointState.NOT_READY
                    if record.state is RuntimeStateV22.RUNNING
                    else EndpointState.NOT_APPLICABLE
                )
            ),
        )
        for record in runtime_records
    )
    request = build_inspect_service_runtime_request(
        run_id=incident.incident_sha256[:32],
        services=action.target_services,
        max_results=len(action.target_services),
    )
    observation = build_read_tool_observation(
        request=request,
        authority=authority,
        duplicate_of_request_sha256=None,
        status=ObservationStatus.SUCCESS,
        error_code=None,
        results=source_records,
        truncated=False,
        observed_at_start=window.started_at,
        observed_at_end=window.ended_at,
        monotonic_latency_ms=max(0, round(latency_ms)),
        counters=ToolCounters(
            dispatch_ordinal=1,
            backend_call_count=1,
            success_count=1,
            failure_count=0,
        ),
    )
    return RuntimeReadOutcomeV22.from_pr_b(
        action=action,
        source_outcome=outcome,
        source_observation=observation,
    )


class ProductReadBackendV1:
    def __init__(
        self,
        *,
        connectors: ConnectorRegistryV1,
        changes: ChangeEventRepositoryV1,
        metrics: ProductMetricsV1,
        pilot_runtime_authority: Any | None = None,
    ) -> None:
        self._connectors = connectors
        self._changes = changes
        self._metrics = metrics
        self._pilot_runtime_authority = pilot_runtime_authority

    def _pilot_runtime_admitted(
        self,
        *,
        environment: EnvironmentRecordV1,
        services: tuple[str, ...],
    ) -> bool:
        return (
            self._pilot_runtime_authority is not None
            and self._pilot_runtime_authority.admits(
                environment_id=environment.environment_id,
                services=services,
            )
            and any(
                config.kind is ConnectorKindV1.PILOT_RUNTIME
                and config.settings.get("authority_sha256")
                == self._pilot_runtime_authority.connector_binding_sha256
                for config in environment.connector_configs
            )
        )

    def acquire(
        self,
        *,
        incident: IncidentRecordV1,
        environment: EnvironmentRecordV1,
        identity_map: ServiceIdentityMapV1,
        capability_matrix: EnvironmentCapabilityMatrixV1,
        topology_edges: tuple[tuple[str, str], ...],
    ) -> ProductReadAcquisitionV1:
        candidates = incident.candidate_logical_services
        enabled = tuple(
            item.source
            for item in capability_matrix.sources
            if item.status is not SourceCapabilityStatusV1.UNAVAILABLE
        )
        registry = build_tool_capability_registry_v22(
            disabled_sources=tuple(source for source in EvidenceSourceV22 if source not in enabled)
        )
        catalog = build_action_catalog_v22(
            candidate_services=candidates,
            topology=StaticTopologyV22.build(
                services=candidates,
                edges=tuple(
                    edge
                    for edge in topology_edges
                    if set(edge).issubset(set(candidates))
                ),
            ),
            capability_registry=registry,
            executed_action_ids=(),
            remaining_budget=100.0,
        )
        actions = tuple(
            action
            for action in catalog.registry_actions
            if action.source in set(enabled)
            and (
                action.target_services == candidates
                if action.source is EvidenceSourceV22.RUNTIME
                else len(action.target_services) == 1
            )
        )
        raw: list[ReadOutcomeV22] = []
        memory: list[MemoryReadOutcomeV22] = []
        snapshots: list[dict[str, Any]] = []
        coverage: dict[EvidenceSourceV22, set[str]] = {
            source: set() for source in EvidenceSourceV22
        }
        limitations: set[str] = {
            f"SOURCE_{item.source.value}_{item.status.value}"
            for item in capability_matrix.sources
            if item.status is not SourceCapabilityStatusV1.AVAILABLE
        }
        identity_by_logical = {item.logical_service: item for item in identity_map.services}
        pilot_runtime_authority = self._pilot_runtime_authority
        for action in actions:
            seconds = action.request.lookback_seconds or action.request.sampling_window_seconds or 60
            window = ConnectorWindowV1(
                started_at=incident.diagnosis_observed_at - timedelta(seconds=seconds),
                ended_at=incident.diagnosis_observed_at,
            )
            result, fixture_backed, components = self._execute(
                action=action,
                incident=incident,
                environment=environment,
                identity_by_logical=identity_by_logical,
                window=window,
            )
            self._metrics.increment(
                "ecomsre_connector_requests_total",
                {"source": action.source.value, "status": result.status.value},
            )
            if result.status not in {
                ReadSourceStatusV22.SUCCESS_EMPTY,
                ReadSourceStatusV22.SUCCESS_NONEMPTY,
            }:
                self._metrics.increment(
                    "ecomsre_connector_failures_total",
                    {"source": action.source.value, "status": result.status.value},
                )
            outcome = _build_outcome(action, result)
            raw.append(outcome)
            coverage[action.source].update(result.covered_services)
            memory_outcome: MemoryReadOutcomeV22 | None
            if action.source is not EvidenceSourceV22.RUNTIME:
                memory_outcome = outcome
            elif (
                fixture_backed
                and outcome.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
            ):
                memory_outcome = _runtime_memory(
                    incident=incident,
                    action=action,
                    outcome=outcome,
                    window=window,
                    latency_ms=result.latency_ms,
                    authority=build_fake_read_authority(),
                )
            elif (
                pilot_runtime_authority is not None
                and self._pilot_runtime_admitted(
                    environment=environment,
                    services=action.target_services,
                )
                and outcome.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
            ):
                memory_outcome = _runtime_memory(
                    incident=incident,
                    action=action,
                    outcome=outcome,
                    window=window,
                    latency_ms=result.latency_ms,
                    authority=pilot_runtime_authority.read_authority,
                )
            else:
                memory_outcome = None
                limitations.add("RUNTIME_MEMORY_AUTHORITY_UNAVAILABLE")
                limitations.add("RUNTIME_DIAGNOSIS_UNAVAILABLE")
            snapshots.append(
                {
                    "schema_version": "ecomsre.product.read-snapshot.v1",
                    "incident_id": incident.incident_id,
                    "action": action.model_dump(mode="json"),
                    "connector_components": [item.model_dump(mode="json") for item in components],
                    "connector_result": result.model_dump(mode="json"),
                    "read_outcome": outcome.model_dump(mode="json"),
                    "memory_outcome": (
                        None
                        if memory_outcome is None
                        else memory_outcome.model_dump(mode="json")
                    ),
                }
            )
            if memory_outcome is not None:
                memory.append(memory_outcome)
        return ProductReadAcquisitionV1(
            raw_outcomes=tuple(raw),
            memory_outcomes=tuple(memory),
            snapshots=tuple(snapshots),
            covered_services_by_source={
                source: tuple(sorted(services)) for source, services in coverage.items()
            },
            capability_limitations=tuple(sorted(limitations)),
        )

    def _execute(
        self,
        *,
        action: EvidenceActionV22,
        incident: IncidentRecordV1,
        environment: EnvironmentRecordV1,
        identity_by_logical: dict[str, Any],
        window: ConnectorWindowV1,
    ) -> tuple[ConnectorQueryResultV1, bool, tuple[ConnectorQueryResultV1, ...]]:
        if action.source is EvidenceSourceV22.CHANGES and not any(
            config.kind is ConnectorKindV1.FIXTURE for config in environment.connector_configs
        ):
            records, truncated = self._changes.list_v22(
                environment_id=incident.environment_id,
                logical_services=action.target_services,
                started_at=window.started_at,
                ended_at=window.ended_at,
                limit=_maximum_records(action),
            )
            result = ConnectorQueryResultV1.build(
                source=action.source,
                status=(
                    ReadSourceStatusV22.SUCCESS_NONEMPTY
                    if records
                    else ReadSourceStatusV22.SUCCESS_EMPTY
                ),
                requested_services=action.target_services,
                covered_services=action.target_services,
                window=window,
                records=records,
                truncated=truncated,
                safe_error_code=None,
                latency_ms=0.0,
            )
            return result, False, (result,)
        matching: list[ConnectorQueryResultV1] = []
        matching_kinds: list[ConnectorKindV1] = []
        pilot_runtime_selected = (
            action.source is EvidenceSourceV22.RUNTIME
            and self._pilot_runtime_admitted(
                environment=environment,
                services=action.target_services,
            )
        )
        for config in environment.connector_configs:
            if pilot_runtime_selected and config.kind is not ConnectorKindV1.PILOT_RUNTIME:
                continue
            connector = self._connectors.create(config)
            try:
                if not any(item.source is action.source for item in connector.capabilities()):
                    continue
                matching_kinds.append(config.kind)
                alias_field = _ALIAS_FIELD_BY_KIND[config.kind]
                aliases = (
                    {}
                    if alias_field is None
                    else {
                        alias: logical
                        for logical in action.target_services
                        for alias in getattr(identity_by_logical[logical].aliases, alias_field)
                    }
                )
                context = ConnectorQueryContextV1(
                    environment_id=incident.environment_id,
                    requested_services=action.target_services,
                    service_aliases=dict(sorted(aliases.items())),
                    window=window,
                    maximum_records=_maximum_records(action),
                    purpose=ConnectorQueryPurposeV1.INCIDENT,
                    requested_source=action.source,
                    request_sha256=action.request_sha256,
                    metric_kinds=action.request.metric_kinds,
                    neighborhood_hops=action.request.neighborhood_hops,
                    sampling_window_seconds=action.request.sampling_window_seconds,
                    sample_count=action.request.sample_count,
                )
                returned = connector.query(context)
                matching.extend(item for item in returned if item.source is action.source)
            finally:
                connector.close()
        combined = _combine_results(
            action=action,
            window=window,
            results=tuple(matching),
        )
        fixture_backed = bool(matching_kinds) and all(
            kind is ConnectorKindV1.FIXTURE for kind in matching_kinds
        )
        return combined, fixture_backed, tuple(matching)


__all__ = ("ProductReadAcquisitionV1", "ProductReadBackendV1")
