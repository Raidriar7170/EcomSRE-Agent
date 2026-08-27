"""Immutable, explicitly promoted environment baseline versions."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from enum import Enum
import json
import math
import re
import statistics
from typing import Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.memory import BaselineProfileV22
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    MetricFactV22,
    MetricKindV22,
    ReadSourceStatusV22,
    ResourceUsageRecordV22,
    TraceSpanV22,
    semantic_sha256_v22,
)
from ecomsre.product.connectors.base import ConnectorQueryResultV1
from ecomsre.product.connectors.base import (
    ConnectorQueryContextV1,
    ConnectorQueryPurposeV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.registry import ConnectorRegistryV1
from ecomsre.product.contracts import (
    ConnectorKindV1,
    EnvironmentRecordV1,
    ProductModelV1,
    ServiceIdentityMapV1,
)
from ecomsre.product.environment.capabilities import (
    EnvironmentCapabilityMatrixV1,
    SourceCapabilityStatusV1,
)
from ecomsre.product.errors import ProductError, not_found
from ecomsre.product.ids import new_product_id
from ecomsre.product.jobs.contracts import JobLeaseFenceV1
from ecomsre.product.jobs.fencing import require_live_job_fence
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


class BaselineBuildModeV1(str, Enum):
    HISTORICAL = "HISTORICAL"
    DEMO_ONLY = "DEMO_ONLY"


class BaselineBuildPolicyV1(ProductModelV1):
    schema_version: Literal["ecomsre.product.baseline-build-policy.v1"] = (
        "ecomsre.product.baseline-build-policy.v1"
    )
    mode: BaselineBuildModeV1 = BaselineBuildModeV1.HISTORICAL
    lookback_seconds: int = Field(default=3600, ge=1, le=3600)
    window_count: int = Field(default=6, ge=1, le=60)
    minimum_successful_windows: int = Field(default=4, ge=1, le=60)
    warmup_seconds: int = Field(default=0, ge=0, le=3600)

    @model_validator(mode="after")
    def require_supported_policy(self) -> "BaselineBuildPolicyV1":
        if self.minimum_successful_windows > self.window_count:
            raise ValueError("baseline minimum exceeds window count")
        if self.mode is BaselineBuildModeV1.HISTORICAL:
            if (
                self.lookback_seconds != 3600
                or self.window_count != 6
                or self.minimum_successful_windows != 4
                or self.warmup_seconds != 0
            ):
                raise ValueError("historical baseline policy differs from the MVP contract")
        elif (
            self.lookback_seconds > 180
            or self.window_count != 5
            or self.warmup_seconds != 180
        ):
            raise ValueError("DEMO_ONLY baseline policy differs from the bounded demo")
        return self


class BaselineJobCreateV1(ProductModelV1):
    build_policy: BaselineBuildPolicyV1 = Field(default_factory=BaselineBuildPolicyV1)
    activate: bool = False


class TopologyEdgeV1(ProductModelV1):
    parent_service: str
    child_service: str


class NormalLogTemplateV1(ProductModelV1):
    service: str
    template: str = Field(min_length=1, max_length=500)
    observation_count: int = Field(ge=1)


class EnvironmentBaselineV1(ProductModelV1):
    schema_version: Literal["ecomsre.product.environment-baseline.v1"] = (
        "ecomsre.product.environment-baseline.v1"
    )
    baseline_id: str = Field(pattern=r"^base-[0-9a-f]{24}$")
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    service_ids: tuple[str, ...]
    source_capability_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    v22_baseline_profile: BaselineProfileV22
    topology_edges: tuple[TopologyEdgeV1, ...]
    normal_log_templates: tuple[NormalLogTemplateV1, ...]
    build_policy: BaselineBuildPolicyV1
    window_count: int = Field(ge=1)
    successful_windows: int = Field(ge=1)
    built_at: datetime
    active: bool = False
    baseline_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_immutable_baseline(self) -> "EnvironmentBaselineV1":
        if self.built_at.tzinfo is None or self.built_at.utcoffset() != timedelta(0):
            raise ValueError("baseline build time must be UTC")
        if self.service_ids != tuple(sorted(set(self.service_ids))):
            raise ValueError("baseline service IDs are not canonical")
        if self.window_count != self.build_policy.window_count:
            raise ValueError("baseline window count differs from policy")
        if self.successful_windows < self.build_policy.minimum_successful_windows:
            raise ValueError("baseline has too few successful windows")
        expected = semantic_sha256_v22(
            self.model_dump(
                mode="json",
                exclude={"baseline_sha256", "active"},
            )
        )
        if self.baseline_sha256 != expected:
            raise ValueError("environment baseline digest differs")
        return self


class BaselineListV1(ProductModelV1):
    items: tuple[EnvironmentBaselineV1, ...]


def _normal_log_template(message: str) -> str:
    normalized = re.sub(r"\b(?:0x)?[0-9a-fA-F]{8,}\b", "<id>", message)
    normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "<num>", normalized)
    return normalized[:500]


def _nearest_rank_p95(values: list[float]) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def build_environment_baseline(
    *,
    environment_id: str,
    identity_map: ServiceIdentityMapV1,
    source_capability_sha256: str,
    build_policy: BaselineBuildPolicyV1,
    window_results: tuple[tuple[ConnectorQueryResultV1, ...], ...],
    built_at: datetime,
    baseline_id: str | None = None,
    required_complete_sources: tuple[EvidenceSourceV22, ...] = (),
) -> EnvironmentBaselineV1:
    if len(window_results) > build_policy.window_count:
        raise ProductError(
            "BASELINE_WINDOW_COUNT_INVALID",
            "The baseline contains more windows than its policy allows.",
        )
    successful = tuple(
        results
        for results in window_results
        if results
        and all(
            item.status
            in {
                ReadSourceStatusV22.SUCCESS_NONEMPTY,
                ReadSourceStatusV22.SUCCESS_EMPTY,
            }
            and not item.truncated
            for item in results
        )
        and any(item.records for item in results)
        and all(
            len(source_results) == 1
            and set(source_results[0].requested_services).issubset(
                source_results[0].covered_services
            )
            for required_source in required_complete_sources
            for source_results in (
                tuple(item for item in results if item.source is required_source),
            )
        )
    )
    if len(successful) < build_policy.minimum_successful_windows:
        raise ProductError(
            "BASELINE_INSUFFICIENT_WINDOWS",
            "The baseline did not produce enough successful windows.",
        )
    metric_values: dict[tuple[str, MetricKindV22], list[float]] = defaultdict(list)
    trace_values: dict[tuple[str, str], list[float]] = defaultdict(list)
    resource_cpu: dict[str, list[float]] = defaultdict(list)
    resource_slopes: dict[str, list[float]] = defaultdict(list)
    topology: set[tuple[str, str]] = set()
    log_templates: Counter[tuple[str, str]] = Counter()
    for results in successful:
        for result in results:
            for record in result.records:
                if isinstance(record, MetricFactV22) and record.value is not None:
                    metric_values[(record.service, record.metric_kind)].append(record.value)
                elif isinstance(record, TraceSpanV22):
                    trace_values[(record.service, record.operation)].append(record.duration_ms)
                    if record.parent_service is not None:
                        topology.add((record.parent_service, record.service))
                elif isinstance(record, ResourceUsageRecordV22):
                    resource_cpu[record.service].extend(
                        sample.cpu_percent for sample in record.samples
                    )
                    resource_slopes[record.service].append(
                        record.memory_slope_bytes_per_second
                    )
                elif isinstance(record, LogRecordV22):
                    log_templates[(record.service, _normal_log_template(record.message))] += 1
    profile = BaselineProfileV22.build(
        metric_stats=tuple(
            (
                service,
                kind,
                statistics.fmean(values),
                statistics.pstdev(values),
            )
            for (service, kind), values in metric_values.items()
        ),
        trace_stats=tuple(
            (service, operation, statistics.fmean(values))
            for (service, operation), values in trace_values.items()
        ),
        resource_stats=tuple(
            (
                service,
                _nearest_rank_p95(resource_cpu[service]),
                statistics.fmean(resource_slopes[service]),
            )
            for service in sorted(set(resource_cpu).intersection(resource_slopes))
        ),
    )
    draft = EnvironmentBaselineV1.model_construct(
        baseline_id=baseline_id or new_product_id("base"),
        environment_id=environment_id,
        service_ids=tuple(sorted(item.service_id for item in identity_map.services)),
        source_capability_sha256=source_capability_sha256,
        v22_baseline_profile=profile,
        topology_edges=tuple(
            TopologyEdgeV1(parent_service=parent, child_service=child)
            for parent, child in sorted(topology)
        ),
        normal_log_templates=tuple(
            NormalLogTemplateV1(
                service=service,
                template=template,
                observation_count=count,
            )
            for (service, template), count in sorted(log_templates.items())
        ),
        build_policy=build_policy,
        window_count=build_policy.window_count,
        successful_windows=len(successful),
        built_at=built_at,
        active=False,
        baseline_sha256="0" * 64,
    )
    payload = draft.model_dump(
        mode="json",
        exclude={"baseline_sha256", "active"},
    )
    return EnvironmentBaselineV1.model_validate(
        {
            "baseline_id": draft.baseline_id,
            "environment_id": environment_id,
            "service_ids": draft.service_ids,
            "source_capability_sha256": source_capability_sha256,
            "v22_baseline_profile": profile,
            "topology_edges": draft.topology_edges,
            "normal_log_templates": draft.normal_log_templates,
            "build_policy": build_policy,
            "window_count": build_policy.window_count,
            "successful_windows": len(successful),
            "built_at": built_at,
            "active": False,
            "baseline_sha256": semantic_sha256_v22(payload),
        }
    )


class BaselineRepositoryV1:
    def __init__(self, store: SqliteStoreV1) -> None:
        self.store = store

    def put(
        self,
        baseline: EnvironmentBaselineV1,
        *,
        activate: bool,
        fence: JobLeaseFenceV1 | None = None,
    ) -> None:
        stored = baseline.model_copy(update={"active": False})
        serialized = json.dumps(
            stored.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                require_live_job_fence(connection, fence)
                existing = connection.execute(
                    "SELECT payload_json, active FROM baseline_versions WHERE baseline_id = ?",
                    (baseline.baseline_id,),
                ).fetchone()
                if existing is not None:
                    if existing["payload_json"] != serialized or bool(
                        existing["active"]
                    ) != activate:
                        raise ProductError(
                            "BASELINE_IMMUTABLE_CONFLICT",
                            "The baseline version already exists with different content.",
                            status_code=409,
                        )
                    connection.execute("COMMIT")
                    return
                if activate:
                    connection.execute(
                        "UPDATE baseline_versions SET active = 0 WHERE environment_id = ?",
                        (baseline.environment_id,),
                    )
                connection.execute(
                    """INSERT INTO baseline_versions(
                        baseline_id, environment_id, payload_json, active, created_at
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        baseline.baseline_id,
                        baseline.environment_id,
                        serialized,
                        int(activate),
                        baseline.built_at.isoformat(),
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def get_optional(self, baseline_id: str) -> EnvironmentBaselineV1 | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json, active FROM baseline_versions WHERE baseline_id = ?",
                (baseline_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload["active"] = bool(row["active"])
        return EnvironmentBaselineV1.model_validate_json(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )

    def get_active(self, environment_id: str) -> EnvironmentBaselineV1:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM baseline_versions "
                "WHERE environment_id = ? AND active = 1",
                (environment_id,),
            ).fetchone()
        if row is None:
            raise ProductError(
                "BASELINE_REQUIRED",
                "The environment must have one active baseline before incident ingestion.",
            )
        payload = json.loads(row["payload_json"])
        payload["active"] = True
        return EnvironmentBaselineV1.model_validate_json(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )

    def list(self, environment_id: str) -> tuple[EnvironmentBaselineV1, ...]:
        with self.store.connect() as connection:
            environment = connection.execute(
                "SELECT 1 FROM environments WHERE environment_id = ?",
                (environment_id,),
            ).fetchone()
            if environment is None:
                raise not_found(
                    "ENVIRONMENT_NOT_FOUND",
                    "The requested environment does not exist.",
                )
            rows = connection.execute(
                """SELECT payload_json, active FROM baseline_versions
                   WHERE environment_id = ?
                   ORDER BY created_at DESC, baseline_id DESC""",
                (environment_id,),
            ).fetchall()
        records: list[EnvironmentBaselineV1] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            payload["active"] = bool(row["active"])
            records.append(
                EnvironmentBaselineV1.model_validate_json(
                    json.dumps(payload, sort_keys=True, separators=(",", ":"))
                )
            )
        return tuple(records)


_ALIAS_FIELD_BY_KIND = {
    ConnectorKindV1.PROMETHEUS: "prometheus",
    ConnectorKindV1.OPENSEARCH: "opensearch",
    ConnectorKindV1.JAEGER: "jaeger",
    ConnectorKindV1.HTTP_HEALTH: "http_health",
    ConnectorKindV1.FIXTURE: None,
}


class HistoricalBaselineServiceV1:
    def __init__(
        self,
        *,
        connectors: ConnectorRegistryV1,
        repository: BaselineRepositoryV1,
        maximum_records_per_source: int,
    ) -> None:
        self._connectors = connectors
        self._repository = repository
        self._maximum_records_per_source = maximum_records_per_source

    def build(
        self,
        *,
        environment: EnvironmentRecordV1,
        identity_map: ServiceIdentityMapV1,
        capability_matrix: EnvironmentCapabilityMatrixV1,
        request: BaselineJobCreateV1,
        built_at: datetime,
        baseline_id: str | None = None,
        fence: JobLeaseFenceV1 | None = None,
    ) -> EnvironmentBaselineV1:
        if baseline_id is not None:
            existing = self._repository.get_optional(baseline_id)
            if existing is not None:
                if existing.environment_id != environment.environment_id:
                    raise ProductError(
                        "BASELINE_IMMUTABLE_CONFLICT",
                        "The baseline job ID is bound to a different environment.",
                        status_code=409,
                    )
                return existing
        logical_services = tuple(
            sorted(item.logical_service for item in identity_map.services)
        )
        if not logical_services:
            raise ProductError(
                "BASELINE_SERVICE_CATALOG_EMPTY",
                "The environment has no verified canonical services.",
            )
        if len(logical_services) > 20:
            raise ProductError(
                "BASELINE_SERVICE_CATALOG_TOO_LARGE",
                "The environment service catalog exceeds the Product query bound.",
            )
        policy = request.build_policy
        window_seconds = policy.lookback_seconds / policy.window_count
        if not float(window_seconds).is_integer() or window_seconds < 1:
            raise ProductError(
                "BASELINE_WINDOW_POLICY_INVALID",
                "The baseline window schedule is not integral.",
            )
        end = built_at - timedelta(seconds=policy.warmup_seconds)
        windows = tuple(
            ConnectorWindowV1(
                started_at=end
                - timedelta(seconds=int(window_seconds) * (policy.window_count - index)),
                ended_at=end
                - timedelta(
                    seconds=int(window_seconds) * (policy.window_count - index - 1)
                ),
            )
            for index in range(policy.window_count)
        )
        connector_instances = []
        try:
            for config in environment.connector_configs:
                connector = self._connectors.create(config)
                if any(
                    capability.supports_baseline
                    and capability.supports_historical_range
                    for capability in connector.capabilities()
                ):
                    connector_instances.append((config.kind, connector))
                else:
                    connector.close()
            if not connector_instances:
                raise ProductError(
                    "BASELINE_SOURCE_UNAVAILABLE",
                    "No configured connector supports a historical baseline range.",
                )
            window_results: list[tuple[ConnectorQueryResultV1, ...]] = []
            for window in windows:
                results: list[ConnectorQueryResultV1] = []
                for kind, connector in connector_instances:
                    alias_field = _ALIAS_FIELD_BY_KIND[kind]
                    alias_map = (
                        {}
                        if alias_field is None
                        else {
                            alias: identity.logical_service
                            for identity in identity_map.services
                            for alias in getattr(identity.aliases, alias_field)
                        }
                    )
                    context = ConnectorQueryContextV1(
                        environment_id=environment.environment_id,
                        requested_services=logical_services,
                        service_aliases=dict(sorted(alias_map.items())),
                        window=window,
                        maximum_records=min(self._maximum_records_per_source, 200),
                        purpose=ConnectorQueryPurposeV1.BASELINE,
                    )
                    connector_results = connector.query(context)
                    expected_sources = {
                        capability.source
                        for capability in connector.capabilities()
                        if capability.supports_baseline
                        and capability.supports_historical_range
                    }
                    if {item.source for item in connector_results} != expected_sources:
                        raise ProductError(
                            "BASELINE_CONNECTOR_CONTRACT_INVALID",
                            "A connector omitted or added a historical baseline source.",
                        )
                    results.extend(connector_results)
                window_results.append(tuple(results))
        finally:
            for _kind, connector in connector_instances:
                connector.close()
        baseline = build_environment_baseline(
            environment_id=environment.environment_id,
            identity_map=identity_map,
            source_capability_sha256=capability_matrix.capability_sha256,
            build_policy=policy,
            window_results=tuple(window_results),
            built_at=built_at,
            baseline_id=baseline_id,
            required_complete_sources=tuple(
                item.source
                for item in capability_matrix.sources
                if item.target_complete_coverage
                and item.status is SourceCapabilityStatusV1.AVAILABLE
                and item.source is not EvidenceSourceV22.CHANGES
            ),
        )
        self._repository.put(baseline, activate=request.activate, fence=fence)
        listed = self._repository.list(environment.environment_id)
        return next(item for item in listed if item.baseline_id == baseline.baseline_id)


__all__ = (
    "BaselineBuildModeV1",
    "BaselineBuildPolicyV1",
    "BaselineJobCreateV1",
    "BaselineListV1",
    "BaselineRepositoryV1",
    "EnvironmentBaselineV1",
    "HistoricalBaselineServiceV1",
    "build_environment_baseline",
)
