"""Immutable, explicitly promoted environment baseline versions."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from enum import Enum
import json
import math
import re
import sqlite3
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
    OpenSearchConnectorSettingsModeV1,
    OpenSearchConnectorSettingsV1,
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
from ecomsre.product.pilot.baseline_audit_v021 import (
    BaselineConnectorBindingV021,
    BaselineConnectorExpectationV021,
    BaselineReadinessAuditV021,
    BaselineReadinessAuditRepositoryV021,
    _put_readiness_audit_in_transaction_v021,
    build_baseline_readiness_audit_v021,
    evaluate_baseline_windows_v021,
)
from ecomsre.product.pilot.baseline_readiness_v023 import (
    BaselineWindowEvaluationV023,
    OpenSearchWindowDiagnosticsV023,
    ProductBaselineReadinessAuditRepositoryV023,
    ProductBaselineReadinessAuditV023,
    ProductBaselineReadinessProfileV023,
    PrometheusWindowDiagnosticsV023,
    evaluate_baseline_windows_v023,
    put_readiness_audit_in_transaction_v023,
)
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


BASELINE_REQUIRED_COMPLETE_SOURCE_POLICY_V021 = "GLOBAL_AVAILABLE_TARGET_COMPLETE_V1"


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
                raise ValueError(
                    "historical baseline policy differs from the MVP contract"
                )
        elif (
            self.lookback_seconds > 180
            or self.window_count != 5
            or self.warmup_seconds != 180
        ):
            raise ValueError("DEMO_ONLY baseline policy differs from the bounded demo")
        return self


class BaselineJobCreateV1(ProductModelV1):
    build_policy: BaselineBuildPolicyV1 = Field(default_factory=BaselineBuildPolicyV1)
    candidate_services: tuple[str, ...] | None = Field(
        default=None,
        min_length=1,
        max_length=20,
    )
    planned_windows: tuple[ConnectorWindowV1, ...] | None = None
    activate: bool = False

    @model_validator(mode="after")
    def require_canonical_candidate_services(self) -> "BaselineJobCreateV1":
        if self.candidate_services is not None and self.candidate_services != tuple(
            sorted(set(self.candidate_services))
        ):
            raise ValueError("baseline candidate services are not canonical")
        if self.planned_windows is not None:
            policy = self.build_policy
            window_seconds = policy.lookback_seconds / policy.window_count
            if (
                policy.mode is not BaselineBuildModeV1.DEMO_ONLY
                or len(self.planned_windows) != policy.window_count
                or not float(window_seconds).is_integer()
                or any(
                    window.ended_at - window.started_at
                    != timedelta(seconds=int(window_seconds))
                    for window in self.planned_windows
                )
                or any(
                    left.ended_at != right.started_at
                    for left, right in zip(
                        self.planned_windows, self.planned_windows[1:]
                    )
                )
            ):
                raise ValueError("baseline planned-window schedule differs")
        return self


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
    expected_windows_v021: tuple[ConnectorWindowV1, ...] | None = None,
    connector_bindings_v021: tuple[tuple[BaselineConnectorBindingV021, ...], ...]
    | None = None,
    connector_expectations_v021: tuple[
        tuple[BaselineConnectorExpectationV021, ...], ...
    ]
    | None = None,
    evaluation_v023: BaselineWindowEvaluationV023 | None = None,
) -> EnvironmentBaselineV1:
    if len(window_results) > build_policy.window_count:
        raise ProductError(
            "BASELINE_WINDOW_COUNT_INVALID",
            "The baseline contains more windows than its policy allows.",
        )
    if (
        expected_windows_v021 is not None
        and len(window_results) != build_policy.window_count
    ):
        raise ProductError(
            "BASELINE_WINDOW_COUNT_INVALID",
            "The audited baseline window count differs from its policy.",
            details={
                "actual_window_count": len(window_results),
                "required_window_count": build_policy.window_count,
            },
        )
    if evaluation_v023 is not None:
        expected_result_sha256s = tuple(
            tuple(sorted(item.result_sha256 for item in results))
            for results in window_results
        )
        evaluated_result_sha256s = tuple(
            item.result_sha256s for item in evaluation_v023.windows
        )
        expected_window_values = tuple(
            results[0].window if results else None for results in window_results
        )
        evaluated_window_values = tuple(item.window for item in evaluation_v023.windows)
        policy_is_v023 = (
            build_policy.mode is BaselineBuildModeV1.DEMO_ONLY
            and build_policy.lookback_seconds == 180
            and build_policy.window_count == 5
            and build_policy.minimum_successful_windows == 4
            and build_policy.warmup_seconds == 180
        )
        if (
            not policy_is_v023
            or not evaluation_v023.final_builder_would_pass
            or len(window_results) != build_policy.window_count
            or expected_result_sha256s != evaluated_result_sha256s
            or expected_window_values != evaluated_window_values
        ):
            raise ProductError(
                "BASELINE_V023_EVALUATION_PARITY_INVALID",
                "The Product v0.2.3 evaluation does not bind the Builder inputs.",
                status_code=409,
                details={"parity_sha256": evaluation_v023.parity_sha256},
            )
        accepted_ordinals = evaluation_v023.accepted_ordinals
        rejected_windows = [
            {
                "window_ordinal": item.window_ordinal,
                "rejection_reason_codes": [
                    reason.value for reason in item.rejection_reason_codes
                ],
            }
            for item in evaluation_v023.windows
            if not item.accepted
        ]
        parity_sha256 = evaluation_v023.parity_sha256
    else:
        expected_windows = expected_windows_v021 or tuple(
            (
                results[0].window
                if results
                else ConnectorWindowV1(
                    started_at=built_at - timedelta(seconds=index + 1),
                    ended_at=built_at - timedelta(seconds=index),
                )
            )
            for index, results in enumerate(window_results)
        )
        evaluation = evaluate_baseline_windows_v021(
            window_results=window_results,
            required_complete_sources=required_complete_sources,
            expected_windows=expected_windows,
            connector_bindings=connector_bindings_v021,
            connector_expectations=connector_expectations_v021,
        )
        accepted_ordinals = evaluation.accepted_ordinals
        rejected_windows = [
            {
                "window_ordinal": item.window_ordinal,
                "rejection_reason_codes": [
                    reason.value for reason in item.rejection_reason_codes
                ],
            }
            for item in evaluation.windows
            if not item.accepted
        ]
        parity_sha256 = evaluation.parity_sha256
    successful = tuple(window_results[ordinal - 1] for ordinal in accepted_ordinals)
    if len(successful) < build_policy.minimum_successful_windows:
        raise ProductError(
            "BASELINE_INSUFFICIENT_WINDOWS",
            "The baseline did not produce enough successful windows.",
            details={
                "accepted_window_ordinals": list(accepted_ordinals),
                "rejected_windows": rejected_windows,
                "parity_sha256": parity_sha256,
            },
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
                    metric_values[(record.service, record.metric_kind)].append(
                        record.value
                    )
                elif isinstance(record, TraceSpanV22):
                    trace_values[(record.service, record.operation)].append(
                        record.duration_ms
                    )
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
                    log_templates[
                        (record.service, _normal_log_template(record.message))
                    ] += 1
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
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                require_live_job_fence(connection, fence)
                self._put_in_transaction(connection, baseline, activate=activate)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _put_in_transaction(
        connection: sqlite3.Connection,
        baseline: EnvironmentBaselineV1,
        *,
        activate: bool,
    ) -> None:
        stored = baseline.model_copy(update={"active": False})
        serialized = json.dumps(
            stored.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        existing = connection.execute(
            "SELECT payload_json, active FROM baseline_versions WHERE baseline_id = ?",
            (baseline.baseline_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing["payload_json"] != serialized
                or bool(existing["active"]) != activate
            ):
                raise ProductError(
                    "BASELINE_IMMUTABLE_CONFLICT",
                    "The baseline version already exists with different content.",
                    status_code=409,
                )
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

    def put_with_readiness_audit_v021(
        self,
        baseline: EnvironmentBaselineV1,
        audit: BaselineReadinessAuditV021,
        *,
        activate: bool,
        created_at: datetime,
        fence: JobLeaseFenceV1 | None = None,
    ) -> None:
        """Atomically persist a passing v0.2.1 audit and its baseline."""

        if (
            not audit.final_builder_would_pass
            or audit.environment_id != baseline.environment_id
            or audit.baseline_entity_service_ids != baseline.service_ids
            or audit.capability_sha256 != baseline.source_capability_sha256
            or audit.build_policy != baseline.build_policy.model_dump(mode="json")
            or audit.accepted_window_count != baseline.successful_windows
        ):
            raise ProductError(
                "BASELINE_READINESS_AUDIT_PARITY_INVALID",
                "The baseline and readiness audit do not describe the same accepted inputs.",
                status_code=409,
            )
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                require_live_job_fence(connection, fence)
                _put_readiness_audit_in_transaction_v021(
                    connection,
                    audit,
                    baseline_id=baseline.baseline_id,
                    created_at=created_at,
                )
                self._put_in_transaction(connection, baseline, activate=activate)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def put_with_readiness_audit_v023(
        self,
        baseline: EnvironmentBaselineV1,
        audit: ProductBaselineReadinessAuditV023,
        *,
        activate: bool,
        created_at: datetime,
        fence: JobLeaseFenceV1 | None = None,
    ) -> None:
        """Atomically persist the strict v0.2.3 audit and real baseline."""

        if (
            not audit.final_builder_would_pass
            or audit.baseline_id != baseline.baseline_id
            or audit.baseline_sha256 != baseline.baseline_sha256
            or audit.environment_id != baseline.environment_id
            or audit.baseline_entity_service_ids != baseline.service_ids
            or audit.capability_sha256 != baseline.source_capability_sha256
            or audit.build_policy != baseline.build_policy.model_dump(mode="json")
            or len(audit.evaluation.accepted_ordinals) != baseline.successful_windows
        ):
            raise ProductError(
                "BASELINE_V023_AUDIT_PARITY_INVALID",
                "The Product v0.2.3 baseline and audit bindings differ.",
                status_code=409,
            )
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                require_live_job_fence(connection, fence)
                put_readiness_audit_in_transaction_v023(
                    connection,
                    audit,
                    created_at=created_at,
                )
                self._put_in_transaction(connection, baseline, activate=activate)
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


def _select_candidate_identity_map_v021(
    identity_map: ServiceIdentityMapV1,
    request: BaselineJobCreateV1,
) -> ServiceIdentityMapV1:
    if request.candidate_services is None:
        return identity_map
    by_logical = {item.logical_service: item for item in identity_map.services}
    missing = tuple(
        service for service in request.candidate_services if service not in by_logical
    )
    if missing:
        raise ProductError(
            "BASELINE_CANDIDATE_SERVICE_UNRESOLVED",
            "One or more baseline candidate services are not verified.",
            details={"missing_candidate_services": list(missing)},
        )
    return ServiceIdentityMapV1.build(
        environment_id=identity_map.environment_id,
        services=tuple(by_logical[service] for service in request.candidate_services),
    )


class HistoricalBaselineServiceV1:
    def __init__(
        self,
        *,
        connectors: ConnectorRegistryV1,
        repository: BaselineRepositoryV1,
        maximum_records_per_source: int,
        audit_repository: BaselineReadinessAuditRepositoryV021 | None = None,
        audit_repository_v023: (
            ProductBaselineReadinessAuditRepositoryV023 | None
        ) = None,
        readiness_profile_v023: ProductBaselineReadinessProfileV023 | None = None,
    ) -> None:
        self._connectors = connectors
        self._repository = repository
        self._maximum_records_per_source = maximum_records_per_source
        self._audit_repository = audit_repository
        self._audit_repository_v023 = audit_repository_v023
        self._readiness_profile_v023 = (
            readiness_profile_v023 or ProductBaselineReadinessProfileV023.default()
        )

    def get_readiness_audit_v023_optional(
        self,
        baseline_id: str,
    ) -> ProductBaselineReadinessAuditV023 | None:
        if self._audit_repository_v023 is None:
            return None
        try:
            return self._audit_repository_v023.get_by_baseline(baseline_id)
        except ProductError as error:
            if error.code == "BASELINE_V023_AUDIT_NOT_FOUND":
                return None
            raise

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
        resolved_baseline_id = baseline_id or new_product_id("base")
        identity_map = _select_candidate_identity_map_v021(identity_map, request)
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
        v023_profile_bound = any(
            config.kind is ConnectorKindV1.OPENSEARCH
            and OpenSearchConnectorSettingsV1.model_validate(config.settings).mode
            is OpenSearchConnectorSettingsModeV1.PROFILE_BOUND
            for config in environment.connector_configs
        )
        use_v023_readiness = (
            policy.mode is BaselineBuildModeV1.DEMO_ONLY
            and logical_services == self._readiness_profile_v023.candidate_services
            and v023_profile_bound
        )
        window_seconds = policy.lookback_seconds / policy.window_count
        if not float(window_seconds).is_integer() or window_seconds < 1:
            raise ProductError(
                "BASELINE_WINDOW_POLICY_INVALID",
                "The baseline window schedule is not integral.",
            )
        if request.planned_windows is not None:
            if not use_v023_readiness or request.planned_windows[
                -1
            ].ended_at > built_at - timedelta(seconds=policy.warmup_seconds):
                raise ProductError(
                    "BASELINE_PLANNED_WINDOWS_INVALID",
                    "The explicit v0.2.3 Baseline window schedule is not admissible.",
                )
            windows = request.planned_windows
        else:
            end = built_at - timedelta(seconds=policy.warmup_seconds)
            windows = tuple(
                ConnectorWindowV1(
                    started_at=end
                    - timedelta(
                        seconds=int(window_seconds) * (policy.window_count - index)
                    ),
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
                    connector_instances.append((config, connector))
                else:
                    connector.close()
            if not connector_instances:
                raise ProductError(
                    "BASELINE_SOURCE_UNAVAILABLE",
                    "No configured connector supports a historical baseline range.",
                )
            window_results: list[tuple[ConnectorQueryResultV1, ...]] = []
            window_bindings: list[tuple[BaselineConnectorBindingV021, ...]] = []
            window_expectations: list[tuple[BaselineConnectorExpectationV021, ...]] = []
            window_prometheus_diagnostics: list[PrometheusWindowDiagnosticsV023] = []
            window_opensearch_diagnostics: list[OpenSearchWindowDiagnosticsV023] = []
            for window in windows:
                results: list[ConnectorQueryResultV1] = []
                bindings: list[BaselineConnectorBindingV021] = []
                expectations: list[BaselineConnectorExpectationV021] = []
                prometheus_diagnostic: PrometheusWindowDiagnosticsV023 | None = None
                opensearch_diagnostic: OpenSearchWindowDiagnosticsV023 | None = None
                for config, connector in connector_instances:
                    alias_field = _ALIAS_FIELD_BY_KIND[config.kind]
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
                    if use_v023_readiness and config.kind is ConnectorKindV1.PROMETHEUS:
                        captured = getattr(
                            connector,
                            "baseline_diagnostics_v023",
                            lambda: None,
                        )()
                        if not isinstance(captured, PrometheusWindowDiagnosticsV023):
                            raise ProductError(
                                "BASELINE_V023_PROMETHEUS_DIAGNOSTICS_REQUIRED",
                                "The v0.2.3 baseline lacks Prometheus template provenance.",
                            )
                        if prometheus_diagnostic is not None:
                            raise ProductError(
                                "BASELINE_V023_PROMETHEUS_SOURCE_AMBIGUOUS",
                                "The v0.2.3 baseline has multiple Prometheus sources.",
                            )
                        prometheus_diagnostic = captured
                    if use_v023_readiness and config.kind is ConnectorKindV1.OPENSEARCH:
                        captured = getattr(
                            connector,
                            "profile_diagnostics",
                            lambda: None,
                        )()
                        log_results = tuple(
                            item
                            for item in connector_results
                            if item.source is EvidenceSourceV22.LOGS
                        )
                        if captured is None or len(log_results) != 1:
                            raise ProductError(
                                "BASELINE_V023_OPENSEARCH_DIAGNOSTICS_REQUIRED",
                                "The v0.2.3 baseline lacks profile-bound Logs provenance.",
                            )
                        if opensearch_diagnostic is not None:
                            raise ProductError(
                                "BASELINE_V023_OPENSEARCH_SOURCE_AMBIGUOUS",
                                "The v0.2.3 baseline has multiple OpenSearch sources.",
                            )
                        if captured.last_query_status is None:
                            raise ProductError(
                                "BASELINE_V023_OPENSEARCH_DIAGNOSTICS_REQUIRED",
                                "The v0.2.3 profile diagnostics lack a query result.",
                            )
                        opensearch_diagnostic = OpenSearchWindowDiagnosticsV023.build(
                            window=window,
                            log_result_sha256=log_results[0].result_sha256,
                            profile_sha256=captured.profile_sha256,
                            query_status=ReadSourceStatusV22(
                                captured.last_query_status
                            ),
                            safe_error_code=captured.last_safe_error_code,
                            sampled_record_count=captured.last_sampled_record_count,
                            accepted_record_count=captured.last_accepted_record_count,
                            rejected_record_count=captured.last_rejected_record_count,
                            rejection_fraction=captured.last_rejection_fraction,
                            rejection_codes_by_count=(
                                captured.last_rejection_codes_by_count
                            ),
                        )
                    expected_sources = {
                        capability.source
                        for capability in connector.capabilities()
                        if capability.supports_baseline
                        and capability.supports_historical_range
                    }
                    expectations.append(
                        BaselineConnectorExpectationV021(
                            connector_name=config.name,
                            connector_kind=config.kind,
                            expected_sources=tuple(
                                sorted(expected_sources, key=lambda item: item.value)
                            ),
                        )
                    )
                    results.extend(connector_results)
                    bindings.extend(
                        BaselineConnectorBindingV021(
                            connector_name=config.name,
                            connector_kind=config.kind,
                        )
                        for _item in connector_results
                    )
                window_results.append(tuple(results))
                window_bindings.append(tuple(bindings))
                window_expectations.append(tuple(expectations))
                if use_v023_readiness:
                    if prometheus_diagnostic is None or opensearch_diagnostic is None:
                        raise ProductError(
                            "BASELINE_V023_SOURCE_DIAGNOSTICS_REQUIRED",
                            "The v0.2.3 baseline source diagnostics are incomplete.",
                        )
                    window_prometheus_diagnostics.append(prometheus_diagnostic)
                    window_opensearch_diagnostics.append(opensearch_diagnostic)
        finally:
            for _config, connector in connector_instances:
                connector.close()
        required_complete_sources = tuple(
            item.source
            for item in capability_matrix.sources
            if item.target_complete_coverage
            and item.status is SourceCapabilityStatusV1.AVAILABLE
            and item.source is not EvidenceSourceV22.CHANGES
        )
        if use_v023_readiness:
            evaluation_v023 = evaluate_baseline_windows_v023(
                profile=self._readiness_profile_v023,
                window_results=tuple(window_results),
                expected_windows=windows,
                connector_bindings=tuple(window_bindings),
                connector_expectations=tuple(window_expectations),
                prometheus_diagnostics=tuple(window_prometheus_diagnostics),
                opensearch_diagnostics=tuple(window_opensearch_diagnostics),
            )
            entity_service_ids = tuple(
                sorted(item.service_id for item in identity_map.services)
            )
            if not evaluation_v023.final_builder_would_pass:
                failed_audit = ProductBaselineReadinessAuditV023.build(
                    environment_id=environment.environment_id,
                    baseline_id=resolved_baseline_id,
                    baseline_sha256=None,
                    service_ids=logical_services,
                    baseline_entity_service_ids=entity_service_ids,
                    build_policy=policy.model_dump(mode="json"),
                    service_identity_sha256=identity_map.identity_sha256,
                    capability_sha256=capability_matrix.capability_sha256,
                    evaluation=evaluation_v023,
                )
                if self._audit_repository_v023 is not None:
                    self._audit_repository_v023.put(
                        failed_audit,
                        created_at=built_at,
                        fence=fence,
                    )
                raise ProductError(
                    "BASELINE_V023_PREFLIGHT_BLOCKED",
                    "The Product v0.2.3 source-aware baseline preflight failed.",
                    details={
                        "parity_sha256": evaluation_v023.parity_sha256,
                        "rejection_reason_codes": list(
                            evaluation_v023.aggregate_rejection_reason_codes
                        ),
                    },
                )
            baseline = build_environment_baseline(
                environment_id=environment.environment_id,
                identity_map=identity_map,
                source_capability_sha256=capability_matrix.capability_sha256,
                build_policy=policy,
                window_results=tuple(window_results),
                built_at=built_at,
                baseline_id=resolved_baseline_id,
                evaluation_v023=evaluation_v023,
            )
            readiness_audit_v023 = ProductBaselineReadinessAuditV023.build(
                environment_id=environment.environment_id,
                baseline_id=baseline.baseline_id,
                baseline_sha256=baseline.baseline_sha256,
                service_ids=logical_services,
                baseline_entity_service_ids=entity_service_ids,
                build_policy=policy.model_dump(mode="json"),
                service_identity_sha256=identity_map.identity_sha256,
                capability_sha256=capability_matrix.capability_sha256,
                evaluation=evaluation_v023,
            )
            if self._audit_repository_v023 is None:
                raise ProductError(
                    "BASELINE_V023_AUDIT_REPOSITORY_REQUIRED",
                    "The Product v0.2.3 Builder requires its immutable audit store.",
                    status_code=500,
                )
            if self._audit_repository_v023.store.path != self._repository.store.path:
                raise ProductError(
                    "BASELINE_V023_READINESS_STORE_MISMATCH",
                    "The Product v0.2.3 baseline and audit must use the same store.",
                    status_code=500,
                )
            self._repository.put_with_readiness_audit_v023(
                baseline,
                readiness_audit_v023,
                activate=request.activate,
                created_at=built_at,
                fence=fence,
            )
            listed = self._repository.list(environment.environment_id)
            return next(
                item for item in listed if item.baseline_id == baseline.baseline_id
            )
        readiness_audit = build_baseline_readiness_audit_v021(
            environment_id=environment.environment_id,
            service_ids=logical_services,
            baseline_entity_service_ids=tuple(
                sorted(item.service_id for item in identity_map.services)
            ),
            build_policy=policy.model_dump(mode="json"),
            capability_sha256=capability_matrix.capability_sha256,
            required_complete_sources=required_complete_sources,
            window_results=tuple(window_results),
            expected_windows=windows,
            connector_bindings=tuple(window_bindings),
            connector_expectations=tuple(window_expectations),
        )
        if (
            self._audit_repository is not None
            and not readiness_audit.final_builder_would_pass
        ):
            self._audit_repository.put(
                readiness_audit,
                baseline_id=resolved_baseline_id,
                created_at=built_at,
                fence=fence,
            )
        baseline = build_environment_baseline(
            environment_id=environment.environment_id,
            identity_map=identity_map,
            source_capability_sha256=capability_matrix.capability_sha256,
            build_policy=policy,
            window_results=tuple(window_results),
            built_at=built_at,
            baseline_id=resolved_baseline_id,
            required_complete_sources=required_complete_sources,
            expected_windows_v021=windows,
            connector_bindings_v021=tuple(window_bindings),
            connector_expectations_v021=tuple(window_expectations),
        )
        if self._audit_repository is None:
            self._repository.put(baseline, activate=request.activate, fence=fence)
        else:
            if self._audit_repository.store.path != self._repository.store.path:
                raise ProductError(
                    "BASELINE_READINESS_STORE_MISMATCH",
                    "The baseline and readiness audit must use the same store.",
                    status_code=500,
                )
            self._repository.put_with_readiness_audit_v021(
                baseline,
                readiness_audit,
                activate=request.activate,
                created_at=built_at,
                fence=fence,
            )
        listed = self._repository.list(environment.environment_id)
        return next(item for item in listed if item.baseline_id == baseline.baseline_id)


__all__ = (
    "BASELINE_REQUIRED_COMPLETE_SOURCE_POLICY_V021",
    "BaselineBuildModeV1",
    "BaselineBuildPolicyV1",
    "BaselineJobCreateV1",
    "BaselineListV1",
    "BaselineRepositoryV1",
    "EnvironmentBaselineV1",
    "HistoricalBaselineServiceV1",
    "build_environment_baseline",
)
