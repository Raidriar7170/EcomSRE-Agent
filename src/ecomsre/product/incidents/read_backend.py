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
    ProductConnectorEvidenceV0232,
)
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    OpenSearchConnectorDiagnosticsV023,
    OpenSearchConnectorProfileBindingV023,
)
from ecomsre.product.connectors.pilot_runtime import PilotRuntimeSnapshotV02
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
from ecomsre.product.incidents.evidence_binding_v0232 import (
    CapabilityEvidenceObservationV0232,
    CapabilityLimitationCandidateV0232,
    build_connector_evidence_binding_v0232,
    build_opensearch_profile_evidence_binding_v0232,
    build_runtime_snapshot_evidence_binding_v0232,
)
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
    capability_observations_v0232: tuple[CapabilityEvidenceObservationV0232, ...]
    capability_limitation_candidates_v0232: tuple[
        CapabilityLimitationCandidateV0232,
        ...,
    ]


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


def _combine_results_without_metrics_contract(
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
    if action.source is not EvidenceSourceV22.TRACES and set(covered) - set(
        action.target_services
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
        metric_records = tuple(
            item for item in records if isinstance(item, MetricFactV22)
        )
        return (
            len(metric_records) == len(records)
            and {item.metric_kind for item in metric_records}
            == set(request.metric_kinds)
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
            item.service in targets
            and window.started_at <= item.observed_at <= window.ended_at
            for item in log_records
        )
    if action.source is EvidenceSourceV22.TRACES:
        trace_records = tuple(
            item for item in records if isinstance(item, TraceSpanV22)
        )
        hops = request.neighborhood_hops
        return (
            len(trace_records) == len(records)
            and hops is not None
            and all(
                window.started_at <= item.observed_at <= window.ended_at
                and item.service in item.service_path
                and any(
                    target in item.service_path
                    and abs(
                        item.service_path.index(target)
                        - item.service_path.index(item.service)
                    )
                    <= hops
                    for target in targets.intersection(item.service_path)
                )
                for item in trace_records
            )
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
        item.service in targets
        and window.started_at <= item.observed_at <= window.ended_at
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


def _metrics_contract_failure(
    *,
    action: EvidenceActionV22,
    window: ConnectorWindowV1,
    results: tuple[ConnectorQueryResultV1, ...],
    records: tuple[ReadRecordV22, ...],
) -> tuple[ReadSourceStatusV22, str] | None:
    targets = set(action.target_services)
    requested_kinds = set(action.request.metric_kinds)
    if any(item.window != window for item in results) or any(
        isinstance(item, MetricFactV22)
        and (
            item.window_started_at != window.started_at
            or item.window_ended_at != window.ended_at
        )
        for item in records
    ):
        return ReadSourceStatusV22.FAILURE_SCHEMA, "METRICS_WINDOW_MISMATCH"
    if (
        set().union(*(set(item.requested_services) for item in results)) != targets
        or any(
            not set(item.requested_services).issubset(targets)
            for item in results
        )
        or any(set(item.covered_services) - targets for item in results)
        or any(
            isinstance(item, MetricFactV22) and item.service not in targets
            for item in records
        )
    ):
        return ReadSourceStatusV22.FAILURE_SCHEMA, "METRICS_TARGET_MISMATCH"
    if any(
        item.status
        not in {ReadSourceStatusV22.SUCCESS_EMPTY, ReadSourceStatusV22.SUCCESS_NONEMPTY}
        for item in results
    ):
        return None
    if len(records) > _maximum_records(action):
        return (
            ReadSourceStatusV22.FAILURE_SCHEMA,
            "METRICS_RECORD_LIMIT_EXCEEDED",
        )
    metric_records = tuple(
        item for item in records if isinstance(item, MetricFactV22)
    )
    if len(metric_records) != len(records) or any(
        item.metric_kind not in requested_kinds for item in metric_records
    ):
        return ReadSourceStatusV22.FAILURE_SCHEMA, "METRICS_UNEXPECTED_KIND"
    keys = tuple((item.service, item.metric_kind) for item in metric_records)
    if len(keys) != len(set(keys)):
        return ReadSourceStatusV22.FAILURE_SCHEMA, "METRICS_DUPLICATE_KIND"
    expected_keys = {
        (service, metric_kind)
        for service in action.target_services
        for metric_kind in action.request.metric_kinds
    }
    if set(keys) != expected_keys:
        return ReadSourceStatusV22.FAILURE_UNAVAILABLE, "METRICS_MISSING_KIND"
    return None


def _combine_results(
    *,
    action: EvidenceActionV22,
    window: ConnectorWindowV1,
    results: tuple[ConnectorQueryResultV1, ...],
) -> ConnectorQueryResultV1:
    if not results or any(item.source is not action.source for item in results):
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
    records = tuple(record for item in results for record in item.records)
    if action.source is EvidenceSourceV22.METRICS:
        failure = _metrics_contract_failure(
            action=action,
            window=window,
            results=results,
            records=records,
        )
        if failure is not None:
            status, safe_error_code = failure
            return ConnectorQueryResultV1.build(
                source=action.source,
                status=status,
                requested_services=action.target_services,
                covered_services=(),
                window=window,
                records=(),
                truncated=False,
                safe_error_code=safe_error_code,
                latency_ms=sum(item.latency_ms for item in results),
            )
    return _combine_results_without_metrics_contract(
        action=action,
        window=window,
        results=results,
    )


def _result_limitation(
    action: EvidenceActionV22,
    result: ConnectorQueryResultV1,
) -> tuple[str, str] | None:
    if result.status not in {
        ReadSourceStatusV22.SUCCESS_EMPTY,
        ReadSourceStatusV22.SUCCESS_NONEMPTY,
    }:
        if (
            action.source is EvidenceSourceV22.METRICS
            and result.safe_error_code == "METRICS_MISSING_KIND"
        ):
            return "SOURCE_METRICS_COVERAGE_GAP", "COVERAGE_GAP"
        return f"SOURCE_{action.source.value}_QUERY_FAILURE", "QUERY_FAILURE"
    if not set(action.target_services).issubset(result.covered_services):
        return f"SOURCE_{action.source.value}_COVERAGE_GAP", "COVERAGE_GAP"
    return None


def _project_capability_scope_v0232(
    *,
    status: SourceCapabilityStatusV1,
    covered_services: tuple[str, ...],
    required_services: tuple[str, ...],
) -> tuple[SourceCapabilityStatusV1, tuple[str, ...]]:
    available_services = (
        ()
        if status is SourceCapabilityStatusV1.UNAVAILABLE
        else tuple(sorted(set(covered_services).intersection(required_services)))
    )
    if not available_services:
        return SourceCapabilityStatusV1.UNAVAILABLE, ()
    if available_services == required_services:
        return SourceCapabilityStatusV1.AVAILABLE, available_services
    return SourceCapabilityStatusV1.PARTIAL, available_services


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
        self._last_connector_diagnostics_v023: tuple[dict[str, Any], ...] = ()
        self._last_connector_bindings_v0232: tuple[dict[str, Any], ...] = ()

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
        capability_scope_by_source = {
            item.source: _project_capability_scope_v0232(
                status=item.status,
                covered_services=item.covered_services,
                required_services=candidates,
            )
            for item in capability_matrix.sources
        }
        enabled = tuple(
            item.source
            for item in capability_matrix.sources
            if capability_scope_by_source[item.source][0]
            is not SourceCapabilityStatusV1.UNAVAILABLE
        )
        registry = build_tool_capability_registry_v22(
            disabled_sources=tuple(
                source for source in EvidenceSourceV22 if source not in enabled
            )
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
            f"SOURCE_{item.source.value}_{capability_scope_by_source[item.source][0].value}"
            for item in capability_matrix.sources
            if capability_scope_by_source[item.source][0]
            is SourceCapabilityStatusV1.UNAVAILABLE
        }
        capability_status_by_source = {
            source: scoped[0] for source, scoped in capability_scope_by_source.items()
        }
        capability_observations: dict[
            str,
            CapabilityEvidenceObservationV0232,
        ] = {}
        limitation_candidates: dict[
            str,
            CapabilityLimitationCandidateV0232,
        ] = {}
        for item in capability_matrix.sources:
            scoped_status, available_services = capability_scope_by_source[item.source]
            if scoped_status is SourceCapabilityStatusV1.AVAILABLE:
                continue
            limitation_code = f"SOURCE_{item.source.value}_{scoped_status.value}"
            observation = CapabilityEvidenceObservationV0232.build(
                source=item.source,
                capability_matrix_sha256=capability_matrix.capability_sha256,
                capability_status=scoped_status,
                required_services=candidates,
                available_services=available_services,
                reason_code=limitation_code,
            )
            capability_observations[limitation_code] = observation
            if scoped_status is SourceCapabilityStatusV1.UNAVAILABLE:
                limitation_candidates[limitation_code] = (
                    CapabilityLimitationCandidateV0232.build(
                        limitation_code=limitation_code,
                        category="SOURCE_UNAVAILABLE",
                        source=item.source,
                        capability_status=scoped_status,
                        connector_action_id=None,
                        connector_result_sha256=None,
                        safe_error_code=None,
                        coverage_required_services=candidates,
                        coverage_observed_services=(),
                    )
                )
        identity_by_logical = {
            item.logical_service: item for item in identity_map.services
        }
        pilot_runtime_authority = self._pilot_runtime_authority
        for action in actions:
            seconds = (
                action.request.lookback_seconds
                or action.request.sampling_window_seconds
                or 60
            )
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
            connector_diagnostics = self._last_connector_diagnostics_v023
            self._metrics.increment(
                "ecomsre_connector_requests_total",
                {"source": action.source.value, "status": result.status.value},
            )
            limitation = _result_limitation(action, result)
            if limitation is not None:
                limitation_code, limitation_category = limitation
                if limitation_category == "QUERY_FAILURE":
                    self._metrics.increment(
                        "ecomsre_connector_failures_total",
                        {"source": action.source.value, "status": result.status.value},
                    )
                limitations.add(limitation_code)
                limitation_candidates[limitation_code] = (
                    CapabilityLimitationCandidateV0232.build(
                        limitation_code=limitation_code,
                        category=limitation_category,
                        source=action.source,
                        capability_status=capability_status_by_source[action.source],
                        connector_action_id=action.action_id,
                        connector_result_sha256=result.result_sha256,
                        safe_error_code=result.safe_error_code,
                        coverage_required_services=action.target_services,
                        coverage_observed_services=result.covered_services,
                    )
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
                for limitation_code in (
                    "RUNTIME_MEMORY_AUTHORITY_UNAVAILABLE",
                    "RUNTIME_DIAGNOSIS_UNAVAILABLE",
                ):
                    limitations.add(limitation_code)
                    limitation_candidates[limitation_code] = (
                        CapabilityLimitationCandidateV0232.build(
                            limitation_code=limitation_code,
                            category="RUNTIME_AUTHORITY_UNAVAILABLE",
                            source=EvidenceSourceV22.RUNTIME,
                            capability_status=capability_status_by_source[
                                EvidenceSourceV22.RUNTIME
                            ],
                            connector_action_id=action.action_id,
                            connector_result_sha256=result.result_sha256,
                            safe_error_code="RUNTIME_AUTHORITY_UNAVAILABLE",
                            coverage_required_services=action.target_services,
                            coverage_observed_services=result.covered_services,
                        )
                    )
            snapshots.append(
                {
                    "schema_version": "ecomsre.product.read-snapshot.v1",
                    "incident_id": incident.incident_id,
                    "action": action.model_dump(mode="json"),
                    "connector_components": [
                        item.model_dump(mode="json") for item in components
                    ],
                    "connector_diagnostics": list(connector_diagnostics),
                    "connector_bindings_v0232": list(
                        self._last_connector_bindings_v0232
                    ),
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
        limitations.update(
            f"SOURCE_{item.source.value}_{SourceCapabilityStatusV1.PARTIAL.value}"
            for item in capability_matrix.sources
            if capability_status_by_source[item.source]
            is SourceCapabilityStatusV1.PARTIAL
            and not set(candidates).issubset(coverage[item.source])
        )
        for limitation_code in tuple(sorted(limitations)):
            if limitation_code in limitation_candidates:
                continue
            capability_observation = capability_observations.get(limitation_code)
            if capability_observation is None:
                continue
            limitation_candidates[limitation_code] = (
                CapabilityLimitationCandidateV0232.build(
                    limitation_code=limitation_code,
                    category="SOURCE_PARTIAL",
                    source=capability_observation.source,
                    capability_status=capability_observation.capability_status,
                    connector_action_id=None,
                    connector_result_sha256=None,
                    safe_error_code=None,
                    coverage_required_services=capability_observation.required_services,
                    coverage_observed_services=capability_observation.available_services,
                )
            )
        return ProductReadAcquisitionV1(
            raw_outcomes=tuple(raw),
            memory_outcomes=tuple(memory),
            snapshots=tuple(snapshots),
            covered_services_by_source={
                source: tuple(sorted(services)) for source, services in coverage.items()
            },
            capability_limitations=tuple(sorted(limitations)),
            capability_observations_v0232=tuple(
                capability_observations[code]
                for code in sorted(limitations)
                if code in capability_observations
            ),
            capability_limitation_candidates_v0232=tuple(
                limitation_candidates[code] for code in sorted(limitation_candidates)
            ),
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
        self._last_connector_diagnostics_v023 = ()
        self._last_connector_bindings_v0232 = ()
        if action.source is EvidenceSourceV22.CHANGES and not any(
            config.kind is ConnectorKindV1.FIXTURE
            for config in environment.connector_configs
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
        connector_diagnostics: list[dict[str, Any]] = []
        binding_inputs: list[
            tuple[
                Any,
                ConnectorQueryContextV1,
                ConnectorQueryResultV1,
                object | None,
            ]
        ] = []
        pilot_runtime_selected = (
            action.source is EvidenceSourceV22.RUNTIME
            and self._pilot_runtime_admitted(
                environment=environment,
                services=action.target_services,
            )
        )
        for config in environment.connector_configs:
            if (
                pilot_runtime_selected
                and config.kind is not ConnectorKindV1.PILOT_RUNTIME
            ):
                continue
            connector = self._connectors.create(config)
            try:
                if not any(
                    item.source is action.source for item in connector.capabilities()
                ):
                    continue
                matching_kinds.append(config.kind)
                alias_field = _ALIAS_FIELD_BY_KIND[config.kind]
                aliases = (
                    {}
                    if alias_field is None
                    else {
                        alias: logical
                        for logical in action.target_services
                        for alias in getattr(
                            identity_by_logical[logical].aliases, alias_field
                        )
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
                source_results = tuple(
                    item for item in returned if item.source is action.source
                )
                matching.extend(source_results)
                evidence_input = (
                    connector.evidence_binding_v0232()
                    if isinstance(connector, ProductConnectorEvidenceV0232)
                    else None
                )
                binding_inputs.extend(
                    (config, context, item, evidence_input) for item in source_results
                )
                profile_diagnostics = getattr(
                    connector,
                    "profile_diagnostics",
                    lambda: None,
                )()
                if profile_diagnostics is not None:
                    connector_diagnostics.append(
                        profile_diagnostics.model_dump(mode="json")
                    )
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
        self._last_connector_diagnostics_v023 = tuple(connector_diagnostics)
        connector_bindings: list[dict[str, Any]] = []
        for config, context, component, evidence_input in sorted(
            binding_inputs,
            key=lambda item: (item[0].name, item[2].result_sha256),
        ):
            binding_kind = "GENERIC"
            binding_payload = None
            binding_payload_sha256 = component.result_sha256
            if (
                config.kind is ConnectorKindV1.OPENSEARCH
                and isinstance(evidence_input, tuple)
                and len(evidence_input) == 2
            ):
                try:
                    profile_binding = (
                        OpenSearchConnectorProfileBindingV023.model_validate(
                            evidence_input[0]
                        )
                    )
                    diagnostics = OpenSearchConnectorDiagnosticsV023.model_validate(
                        evidence_input[1]
                    )
                    opensearch_specialized = (
                        build_opensearch_profile_evidence_binding_v0232(
                            profile_binding=profile_binding,
                            diagnostics=diagnostics,
                            connector_result=component,
                        )
                    )
                except ValueError:
                    opensearch_specialized = None
                if opensearch_specialized is not None:
                    binding_kind = "OPENSEARCH_PROFILE"
                    binding_payload = opensearch_specialized.model_dump(mode="json")
                    binding_payload_sha256 = opensearch_specialized.binding_sha256
            elif (
                config.kind is ConnectorKindV1.PILOT_RUNTIME
                and isinstance(evidence_input, PilotRuntimeSnapshotV02)
                and self._pilot_runtime_authority is not None
            ):
                try:
                    runtime_specialized = build_runtime_snapshot_evidence_binding_v0232(
                        snapshot=evidence_input,
                        config=config,
                        runtime_authority=self._pilot_runtime_authority,
                        connector_result=component,
                        formal_traffic_started_at=incident.started_at,
                        diagnosis_observed_at=incident.diagnosis_observed_at,
                    )
                except ValueError:
                    runtime_specialized = None
                if runtime_specialized is not None:
                    binding_kind = "RUNTIME_SNAPSHOT"
                    binding_payload = runtime_specialized.model_dump(mode="json")
                    binding_payload_sha256 = runtime_specialized.binding_sha256
            generic = build_connector_evidence_binding_v0232(
                incident_id=incident.incident_id,
                action_id=action.action_id,
                config=config,
                context=context,
                component_result=component,
                combined_result=combined,
                binding_kind=binding_kind,
                binding_payload_sha256=binding_payload_sha256,
            )
            connector_bindings.append(
                {
                    "connector_binding": generic.model_dump(mode="json"),
                    "binding_payload": binding_payload,
                }
            )
        self._last_connector_bindings_v0232 = tuple(connector_bindings)
        return combined, fixture_backed, tuple(matching)


__all__ = ("ProductReadAcquisitionV1", "ProductReadBackendV1")
