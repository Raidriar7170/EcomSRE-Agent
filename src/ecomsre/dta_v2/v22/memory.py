"""Deterministic Full and Salient evidence-memory representations for DTA v2.2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import math
import re
from typing import Any, Literal, TypeAlias

from pydantic import (
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    ValidationInfo,
    model_validator,
)

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.tool_contracts import (
    EndpointState,
    HealthState,
    ObservationStatus,
    ReadToolObservation,
    RuntimeRecord,
    ToolName,
)
from ecomsre.dta_v2.v22.read_contracts import (
    ChangeCategoryV22,
    DtaModelV22,
    EvidenceSourceV22,
    LogRecordV22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    MetricUnitV22,
    ReadSourceStatusV22,
    RecentChangeRecordV22,
    ResourceUsageRecordV22,
    RolloutStateV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    Sha256V22,
    SpanStatusV22,
    TraceSpanV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22


class SignalStrengthV22(str, Enum):
    NONE = "NONE"
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"


_SIGNAL_RANK = {
    SignalStrengthV22.NONE: 0,
    SignalStrengthV22.WEAK: 1,
    SignalStrengthV22.MODERATE: 2,
    SignalStrengthV22.STRONG: 3,
}


_FROZEN_PREDICATE_THRESHOLDS_V22: dict[str, object] = {
    "schema_version": "dta-v22.predicate-thresholds.v1",
    "error_rate_strong_ratio": 2.0,
    "error_rate_strong_delta": 0.05,
    "latency_strong_ratio": 2.0,
    "latency_strong_delta_ms": 5.0,
    "trace_latency_strong_ratio": 2.0,
    "trace_latency_strong_delta_ms": 5.0,
    "cpu_strong_p95_percent": 80.0,
    "cpu_strong_baseline_ratio": 2.0,
    "memory_growth_strong_bytes_per_second": 1.0,
    "restart_pressure_count": 2,
    "recent_change_seconds": 900,
}


class PredicateThresholdsV22(DtaModelV22):
    schema_version: Literal["dta-v22.predicate-thresholds.v1"]
    error_rate_strong_ratio: StrictFloat = Field(gt=1)
    error_rate_strong_delta: StrictFloat = Field(gt=0)
    latency_strong_ratio: StrictFloat = Field(gt=1)
    latency_strong_delta_ms: StrictFloat = Field(gt=0)
    trace_latency_strong_ratio: StrictFloat = Field(gt=1)
    trace_latency_strong_delta_ms: StrictFloat = Field(gt=0)
    cpu_strong_p95_percent: StrictFloat = Field(gt=0)
    cpu_strong_baseline_ratio: StrictFloat = Field(gt=1)
    memory_growth_strong_bytes_per_second: StrictFloat = Field(gt=0)
    restart_pressure_count: StrictInt = Field(ge=1)
    recent_change_seconds: StrictInt = Field(ge=1)
    thresholds_sha256: Sha256V22

    @classmethod
    def frozen(cls) -> PredicateThresholdsV22:
        payload = dict(_FROZEN_PREDICATE_THRESHOLDS_V22)
        return cls.model_validate(
            {**payload, "thresholds_sha256": semantic_sha256_v22(payload)}
        )

    @model_validator(mode="after")
    def require_digest(self) -> PredicateThresholdsV22:
        actual = self.model_dump(mode="json", exclude={"thresholds_sha256"})
        if actual != _FROZEN_PREDICATE_THRESHOLDS_V22:
            raise ValueError("predicate thresholds differ from frozen development values")
        expected = semantic_sha256_v22(actual)
        if self.thresholds_sha256 != expected:
            raise ValueError("predicate thresholds digest differs")
        return self


class BaselineMetricStatV22(DtaModelV22):
    service: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    metric_kind: MetricKindV22
    mean: StrictFloat = Field(ge=0)
    standard_deviation: StrictFloat = Field(ge=0)


class BaselineTraceStatV22(DtaModelV22):
    service: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    operation: str = Field(min_length=1, max_length=160)
    duration_ms: StrictFloat = Field(ge=0)


class BaselineResourceStatV22(DtaModelV22):
    service: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    cpu_p95_percent: StrictFloat = Field(ge=0)
    memory_slope_bytes_per_second: StrictFloat


class BaselineProfileV22(DtaModelV22):
    schema_version: Literal["dta-v22.baseline-profile.v1"]
    metric_stats: tuple[BaselineMetricStatV22, ...]
    trace_stats: tuple[BaselineTraceStatV22, ...]
    resource_stats: tuple[BaselineResourceStatV22, ...]
    baseline_sha256: Sha256V22

    @classmethod
    def build(
        cls,
        *,
        metric_stats: tuple[tuple[str, MetricKindV22, float, float], ...],
        trace_stats: tuple[tuple[str, str, float], ...],
        resource_stats: tuple[tuple[str, float, float], ...],
    ) -> BaselineProfileV22:
        metrics = tuple(
            sorted(
                (
                    BaselineMetricStatV22(
                        service=service,
                        metric_kind=kind,
                        mean=float(mean),
                        standard_deviation=float(stddev),
                    )
                    for service, kind, mean, stddev in metric_stats
                ),
                key=lambda item: (item.service, item.metric_kind.value),
            )
        )
        traces = tuple(
            sorted(
                (
                    BaselineTraceStatV22(
                        service=service,
                        operation=operation,
                        duration_ms=float(duration),
                    )
                    for service, operation, duration in trace_stats
                ),
                key=lambda item: (item.service, item.operation),
            )
        )
        resources = tuple(
            sorted(
                (
                    BaselineResourceStatV22(
                        service=service,
                        cpu_p95_percent=float(cpu),
                        memory_slope_bytes_per_second=float(slope),
                    )
                    for service, cpu, slope in resource_stats
                ),
                key=lambda item: item.service,
            )
        )
        payload: dict[str, Any] = {
            "schema_version": "dta-v22.baseline-profile.v1",
            "metric_stats": metrics,
            "trace_stats": traces,
            "resource_stats": resources,
        }
        draft = cls.model_construct(**payload, baseline_sha256="0" * 64)
        return cls.model_validate(
            {
                **payload,
                "baseline_sha256": semantic_sha256_v22(
                    draft.model_dump(mode="json", exclude={"baseline_sha256"})
                ),
            }
        )

    @model_validator(mode="after")
    def require_profile(self) -> BaselineProfileV22:
        metric_keys = tuple((item.service, item.metric_kind) for item in self.metric_stats)
        trace_keys = tuple((item.service, item.operation) for item in self.trace_stats)
        resource_keys = tuple(item.service for item in self.resource_stats)
        for values, label in (
            (metric_keys, "metric"),
            (trace_keys, "trace"),
            (resource_keys, "resource"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"baseline contains duplicate {label} keys")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"baseline_sha256"})
        )
        if self.baseline_sha256 != expected:
            raise ValueError("baseline profile digest differs")
        return self

    def metric(self, service: str, kind: MetricKindV22) -> BaselineMetricStatV22 | None:
        return next(
            (
                item
                for item in self.metric_stats
                if item.service == service and item.metric_kind is kind
            ),
            None,
        )

    def trace(self, service: str, operation: str) -> BaselineTraceStatV22 | None:
        return next(
            (
                item
                for item in self.trace_stats
                if item.service == service and item.operation == operation
            ),
            None,
        )

    def resource(self, service: str) -> BaselineResourceStatV22 | None:
        return next((item for item in self.resource_stats if item.service == service), None)


class EvidenceRefV22(DtaModelV22):
    schema_version: Literal["dta-v22.evidence-ref.v1"]
    evidence_ref: str = Field(pattern=r"^e:[a-z0-9:+-]+:[0-9]+:[0-9a-f]{12}$")
    action_id: str = Field(pattern=r"^a:[a-z0-9][a-z0-9:+-]*$")
    source: EvidenceSourceV22
    outcome_sha256: Sha256V22
    record_index: StrictInt = Field(ge=0)
    record_sha256: Sha256V22

    @model_validator(mode="after")
    def require_reference(self) -> EvidenceRefV22:
        expected = f"e:{self.action_id}:{self.record_index}:{self.record_sha256[:12]}"
        if self.evidence_ref != expected:
            raise ValueError("evidence ref identity differs from its binding")
        return self


class MetricSalientPayloadV22(DtaModelV22):
    schema_version: Literal["dta-v22.salient-metric.v1"]
    metric_kind: MetricKindV22
    support_status: MetricSupportStatusV22
    sample_count: StrictInt = Field(ge=0)
    value: StrictFloat | None
    unit: MetricUnitV22
    baseline_value: StrictFloat | None
    baseline_ratio: StrictFloat | None
    delta: StrictFloat | None
    z_score: StrictFloat | None


class LogCategoryV22(str, Enum):
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    DEPENDENCY_TIMEOUT = "DEPENDENCY_TIMEOUT"
    MEMORY_PRESSURE = "MEMORY_PRESSURE"
    OTHER = "OTHER"


class LogSalientPayloadV22(DtaModelV22):
    schema_version: Literal["dta-v22.salient-log.v1"]
    severity: Literal["WARN", "ERROR", "FATAL", "DIAGNOSTIC"]
    normalized_template: str = Field(min_length=1, max_length=500)
    category: LogCategoryV22
    downstream_service: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9-]*$",
    )
    count: StrictInt = Field(ge=1)


class TraceSalientPayloadV22(DtaModelV22):
    schema_version: Literal["dta-v22.salient-trace.v1"]
    operation: str = Field(min_length=1, max_length=160)
    service_path: tuple[str, ...] = Field(min_length=1, max_length=12)
    parent_service: str | None
    status: SpanStatusV22
    first_error_location: StrictBool
    duration_ms: StrictFloat = Field(ge=0)
    baseline_duration_ms: StrictFloat | None
    baseline_ratio: StrictFloat | None
    delta_ms: StrictFloat | None


class RuntimeSalientPayloadV22(DtaModelV22):
    schema_version: Literal["dta-v22.salient-runtime.v1"]
    state: RuntimeStateV22
    healthy: StrictBool
    endpoint: EndpointState
    restart_count: StrictInt = Field(ge=0)
    exit_code: StrictInt | None = Field(default=None, ge=0, le=255)

    @model_validator(mode="after")
    def require_exit_semantics(self) -> RuntimeSalientPayloadV22:
        if (
            self.state is RuntimeStateV22.RUNNING
            and self.exit_code not in {None, 0}
        ):
            raise ValueError("running runtime fact has an impossible exit code")
        if self.state is not RuntimeStateV22.RUNNING and self.healthy:
            raise ValueError("non-running runtime fact cannot be healthy")
        return self


class RuntimeObservationV22(DtaModelV22):
    """Bounded runtime projection whose provenance is part of the outcome digest."""

    schema_version: Literal["dta-v22.runtime-observation.v1"]
    service: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    state: RuntimeStateV22
    healthy: StrictBool
    endpoint: EndpointState
    restart_count: StrictInt = Field(ge=0)
    exit_code: StrictInt | None = Field(default=None, ge=0, le=255)

    @model_validator(mode="after")
    def require_exit_semantics(self) -> RuntimeObservationV22:
        if (
            self.state is RuntimeStateV22.RUNNING
            and self.exit_code not in {None, 0}
        ):
            raise ValueError("running runtime observation has an impossible exit code")
        if self.state is not RuntimeStateV22.RUNNING and self.healthy:
            raise ValueError("non-running runtime observation cannot be healthy")
        return self


_FAILURE_STATUSES_V22 = {
    ReadSourceStatusV22.FAILURE_UNAVAILABLE,
    ReadSourceStatusV22.FAILURE_TIMEOUT,
    ReadSourceStatusV22.FAILURE_SCHEMA,
}


class RuntimeReadOutcomeV22(DtaModelV22):
    """Canonical PR-B runtime outcome projected into the PR-C memory schema."""

    schema_version: Literal["dta-v22.runtime-read-outcome.v1"]
    action_id: str
    source: Literal[EvidenceSourceV22.RUNTIME]
    request_sha256: Sha256V22
    status: ReadSourceStatusV22
    records: tuple[RuntimeObservationV22, ...]
    truncated: StrictBool
    action: EvidenceActionV22
    source_outcome: ReadOutcomeV22
    source_observation: ReadToolObservation
    projection_policy: Literal[
        "dta-v22.runtime-projection.from-pr-b-and-v2-authority.v1"
    ]
    outcome_sha256: Sha256V22

    @classmethod
    def from_pr_b(
        cls,
        *,
        action: EvidenceActionV22,
        source_outcome: ReadOutcomeV22,
        source_observation: ReadToolObservation,
    ) -> RuntimeReadOutcomeV22:
        action = EvidenceActionV22.model_validate(action.model_dump(mode="python"))
        source_outcome = ReadOutcomeV22.model_validate(
            source_outcome.model_dump(mode="python")
        )
        source_observation = ReadToolObservation.model_validate(
            source_observation.model_dump(mode="python")
        )
        records = _project_pr_b_runtime_records(
            source_outcome,
            source_observation,
        )
        payload: dict[str, Any] = {
            "schema_version": "dta-v22.runtime-read-outcome.v1",
            "action_id": source_outcome.action_id,
            "source": EvidenceSourceV22.RUNTIME,
            "request_sha256": source_outcome.request_sha256,
            "status": source_outcome.status,
            "records": records,
            "truncated": source_outcome.truncated,
            "action": action,
            "source_outcome": source_outcome,
            "source_observation": source_observation,
            "projection_policy": (
                "dta-v22.runtime-projection.from-pr-b-and-v2-authority.v1"
            ),
        }
        draft = cls.model_construct(**payload, outcome_sha256="0" * 64)
        return cls.model_validate(
            {
                **payload,
                "outcome_sha256": semantic_sha256_v22(
                    draft.model_dump(mode="json", exclude={"outcome_sha256"})
                ),
            }
        )

    @model_validator(mode="after")
    def require_outcome(self) -> RuntimeReadOutcomeV22:
        if self.status is ReadSourceStatusV22.SUCCESS_NONEMPTY and not self.records:
            raise ValueError("nonempty runtime outcome has no records")
        if self.status is not ReadSourceStatusV22.SUCCESS_NONEMPTY and self.records:
            raise ValueError("empty or failed runtime outcome contains records")
        if self.status in _FAILURE_STATUSES_V22 and self.truncated:
            raise ValueError("failed runtime outcome cannot be truncated")
        services = tuple(item.service for item in self.records)
        if len(services) != len(set(services)):
            raise ValueError("runtime outcome contains duplicate services")
        if (
            self.action.source is not EvidenceSourceV22.RUNTIME
            or self.source_outcome.source is not EvidenceSourceV22.RUNTIME
            or self.source_observation.tool is not ToolName.INSPECT_SERVICE_RUNTIME
            or self.source_observation.source.value != EvidenceSourceV22.RUNTIME.value
            or self.action_id != self.action.action_id
            or self.action_id != self.source_outcome.action_id
            or self.request_sha256 != self.action.request_sha256
            or self.request_sha256 != self.source_outcome.request_sha256
            or self.status is not self.source_outcome.status
            or self.truncated != self.source_outcome.truncated
            or self.records
            != _project_pr_b_runtime_records(
                self.source_outcome,
                self.source_observation,
            )
            or (
                bool(self.records)
                and tuple(item.service for item in self.records)
                != self.action.target_services
            )
        ):
            raise ValueError("runtime outcome differs from canonical PR-B authority")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"outcome_sha256"})
        )
        if self.outcome_sha256 != expected:
            raise ValueError("runtime outcome digest does not bind observations")
        return self


MemoryReadOutcomeV22: TypeAlias = ReadOutcomeV22 | RuntimeReadOutcomeV22


class ResourceSalientPayloadV22(DtaModelV22):
    schema_version: Literal["dta-v22.salient-resource.v1"]
    cpu_p50_percent: StrictFloat = Field(ge=0)
    cpu_p95_percent: StrictFloat = Field(ge=0)
    cpu_max_percent: StrictFloat = Field(ge=0)
    memory_start_bytes: StrictInt = Field(ge=0)
    memory_end_bytes: StrictInt = Field(ge=0)
    memory_delta_bytes: StrictInt
    memory_slope_bytes_per_second: StrictFloat
    sample_count: StrictInt = Field(ge=2)
    baseline_cpu_p95_percent: StrictFloat | None
    cpu_baseline_ratio: StrictFloat | None
    baseline_memory_slope_bytes_per_second: StrictFloat | None


class ChangeSalientPayloadV22(DtaModelV22):
    schema_version: Literal["dta-v22.salient-change.v1"]
    category: ChangeCategoryV22
    relative_seconds: StrictInt = Field(ge=0)
    rollout_state: RolloutStateV22
    revision_digest: Sha256V22


SalientPayloadV22 = (
    MetricSalientPayloadV22
    | LogSalientPayloadV22
    | TraceSalientPayloadV22
    | RuntimeSalientPayloadV22
    | ResourceSalientPayloadV22
    | ChangeSalientPayloadV22
)


class SalientFactV22(DtaModelV22):
    schema_version: Literal["dta-v22.salient-fact.v1"]
    fact_id: str = Field(pattern=r"^f:[a-z]+:[0-9a-f]{16}$")
    source: EvidenceSourceV22
    service: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    signal_strength: SignalStrengthV22
    payload: SalientPayloadV22
    fact_sha256: Sha256V22

    @model_validator(mode="after")
    def require_fact(self) -> SalientFactV22:
        expected_payloads: dict[EvidenceSourceV22, type[DtaModelV22]] = {
            EvidenceSourceV22.METRICS: MetricSalientPayloadV22,
            EvidenceSourceV22.LOGS: LogSalientPayloadV22,
            EvidenceSourceV22.TRACES: TraceSalientPayloadV22,
            EvidenceSourceV22.RUNTIME: RuntimeSalientPayloadV22,
            EvidenceSourceV22.RESOURCES: ResourceSalientPayloadV22,
            EvidenceSourceV22.CHANGES: ChangeSalientPayloadV22,
        }
        if type(self.payload) is not expected_payloads[self.source]:
            raise ValueError("salient fact payload differs from source")
        if self.evidence_refs != tuple(sorted(set(self.evidence_refs))):
            raise ValueError("salient fact refs are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"fact_sha256"})
        )
        if self.fact_sha256 != expected:
            raise ValueError("salient fact digest differs")
        return self


class ObservationSummaryV22(DtaModelV22):
    schema_version: Literal["dta-v22.observation-summary.v1"]
    action_id: str
    source: EvidenceSourceV22
    status: ReadSourceStatusV22
    request_sha256: Sha256V22
    outcome_sha256: Sha256V22
    evidence_refs: tuple[str, ...]
    retained_fact_ids: tuple[str, ...]
    summary_sha256: Sha256V22

    @model_validator(mode="after")
    def require_summary(self) -> ObservationSummaryV22:
        for values in (self.evidence_refs, self.retained_fact_ids):
            if values != tuple(sorted(set(values))):
                raise ValueError("observation summary references are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"summary_sha256"})
        )
        if self.summary_sha256 != expected:
            raise ValueError("observation summary digest differs")
        return self


class MemoryLossEntryV22(DtaModelV22):
    schema_version: Literal["dta-v22.memory-loss-entry.v1"]
    action_id: str
    source: EvidenceSourceV22
    outcome_sha256: Sha256V22
    original_record_count: StrictInt = Field(ge=0)
    retained_fact_count: StrictInt = Field(ge=0)
    omitted_record_count: StrictInt = Field(ge=0)
    omitted_field_categories: tuple[str, ...]
    truncated: StrictBool
    artifact_sha256: Sha256V22

    @model_validator(mode="after")
    def require_counts(self) -> MemoryLossEntryV22:
        if (
            self.retained_fact_count > self.original_record_count
            or self.omitted_record_count > self.original_record_count
        ):
            raise ValueError("memory loss counts exceed original records")
        if self.omitted_field_categories != tuple(
            sorted(set(self.omitted_field_categories))
        ):
            raise ValueError("omitted field categories are not canonical")
        if self.artifact_sha256 != self.outcome_sha256:
            raise ValueError("memory loss artifact does not bind its outcome")
        return self


class MemoryLossLedgerV22(DtaModelV22):
    schema_version: Literal["dta-v22.memory-loss-ledger.v1"]
    entries: tuple[MemoryLossEntryV22, ...]
    ledger_sha256: Sha256V22

    @model_validator(mode="after")
    def require_ledger(self) -> MemoryLossLedgerV22:
        outcomes = tuple(item.outcome_sha256 for item in self.entries)
        if len(outcomes) != len(set(outcomes)):
            raise ValueError("memory loss ledger contains duplicate outcomes")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"ledger_sha256"})
        )
        if self.ledger_sha256 != expected:
            raise ValueError("memory loss ledger digest differs")
        return self


class MinimalObservationIndexV22(DtaModelV22):
    schema_version: Literal["dta-v22.minimal-observation-index.v1"]
    action_id: str
    source: EvidenceSourceV22
    status: ReadSourceStatusV22
    request_sha256: Sha256V22
    outcome_sha256: Sha256V22
    evidence_refs: tuple[str, ...]


class PredicateKindV22(str, Enum):
    METRIC_ERROR_RATE_STRONG = "METRIC_ERROR_RATE_STRONG"
    METRIC_LATENCY_STRONG = "METRIC_LATENCY_STRONG"
    METRIC_MEMORY_STRONG = "METRIC_MEMORY_STRONG"
    RUNTIME_NOT_RUNNING = "RUNTIME_NOT_RUNNING"
    RUNTIME_UNHEALTHY = "RUNTIME_UNHEALTHY"
    RUNTIME_HEALTHY = "RUNTIME_HEALTHY"
    RUNTIME_RESTART_PRESSURE = "RUNTIME_RESTART_PRESSURE"
    RESOURCE_CPU_STRONG = "RESOURCE_CPU_STRONG"
    RESOURCE_MEMORY_GROWTH_STRONG = "RESOURCE_MEMORY_GROWTH_STRONG"
    TRACE_FIRST_ERROR = "TRACE_FIRST_ERROR"
    TRACE_DEPENDENCY_LATENCY = "TRACE_DEPENDENCY_LATENCY"
    LOG_CONFIGURATION_ERROR = "LOG_CONFIGURATION_ERROR"
    LOG_DEPENDENCY_TIMEOUT = "LOG_DEPENDENCY_TIMEOUT"
    LOG_MEMORY_PRESSURE = "LOG_MEMORY_PRESSURE"
    CHANGE_RECENT_ROLLOUT = "CHANGE_RECENT_ROLLOUT"


class EvidencePredicateV22(DtaModelV22):
    schema_version: Literal["dta-v22.evidence-predicate.v1"]
    predicate_id: str = Field(pattern=r"^p:[a-z0-9-]+:[a-z0-9-]+:[0-9a-f]{12}$")
    predicate_kind: PredicateKindV22
    source: EvidenceSourceV22
    service: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    parent_service: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$")
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    predicate_sha256: Sha256V22

    @model_validator(mode="after")
    def require_predicate(self) -> EvidencePredicateV22:
        if self.evidence_refs != tuple(sorted(set(self.evidence_refs))):
            raise ValueError("predicate evidence refs are not canonical")
        identity = semantic_sha256_v22(
            {
                "kind": self.predicate_kind.value,
                "source": self.source.value,
                "service": self.service,
                "parent_service": self.parent_service,
                "evidence_refs": self.evidence_refs,
            }
        )
        expected_id = (
            f"p:{self.predicate_kind.value.casefold().replace('_', '-')}:"
            f"{self.service}:{identity[:12]}"
        )
        if self.predicate_id != expected_id:
            raise ValueError("predicate identity differs from its semantic binding")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"predicate_sha256"})
        )
        if self.predicate_sha256 != expected:
            raise ValueError("predicate digest differs")
        return self


class SalientEvidenceMemoryV22(DtaModelV22):
    schema_version: Literal["dta-v22.salient-evidence-memory.v1"]
    baseline_sha256: Sha256V22
    thresholds_sha256: Sha256V22
    observed_at: datetime
    evidence_refs: tuple[EvidenceRefV22, ...]
    observation_summaries: tuple[ObservationSummaryV22, ...]
    predicates: tuple[EvidencePredicateV22, ...]
    salient_facts: tuple[SalientFactV22, ...]
    loss_ledger: MemoryLossLedgerV22
    memory_sha256: Sha256V22

    @model_validator(mode="after")
    def require_memory(self, info: ValidationInfo) -> SalientEvidenceMemoryV22:
        _require_utc(self.observed_at)
        if self.thresholds_sha256 != PredicateThresholdsV22.frozen().thresholds_sha256:
            raise ValueError("salient memory thresholds are not frozen")
        refs = tuple(item.evidence_ref for item in self.evidence_refs)
        if refs != tuple(sorted(set(refs))):
            raise ValueError("salient evidence refs are not canonical")
        ref_set = set(refs)
        refs_by_value = {item.evidence_ref: item for item in self.evidence_refs}
        summaries_by_outcome = {
            item.outcome_sha256: item for item in self.observation_summaries
        }
        if len(summaries_by_outcome) != len(self.observation_summaries):
            raise ValueError("salient memory contains duplicate outcome summaries")
        facts_by_id = {item.fact_id: item for item in self.salient_facts}
        if len(facts_by_id) != len(self.salient_facts):
            raise ValueError("salient memory contains duplicate facts")
        for summary in self.observation_summaries:
            expected_refs = tuple(
                sorted(
                    ref.evidence_ref
                    for ref in self.evidence_refs
                    if ref.outcome_sha256 == summary.outcome_sha256
                )
            )
            if summary.evidence_refs != expected_refs:
                raise ValueError("observation summary evidence refs differ")
            if any(
                refs_by_value[ref].action_id != summary.action_id
                or refs_by_value[ref].source is not summary.source
                for ref in summary.evidence_refs
            ):
                raise ValueError("observation summary identity differs from refs")
            expected_retained = tuple(
                sorted(
                    fact.fact_id
                    for fact in self.salient_facts
                    if fact.evidence_refs
                    and {
                        refs_by_value[ref].outcome_sha256
                        for ref in fact.evidence_refs
                    }
                    == {summary.outcome_sha256}
                )
            )
            if summary.retained_fact_ids != expected_retained:
                raise ValueError("observation summary retained facts differ")
        for fact in self.salient_facts:
            if any(refs_by_value[ref].source is not fact.source for ref in fact.evidence_refs):
                raise ValueError("salient fact source differs from evidence refs")
            if len({refs_by_value[ref].outcome_sha256 for ref in fact.evidence_refs}) != 1:
                raise ValueError("salient fact spans multiple observations")
        ledger_by_outcome = {
            item.outcome_sha256: item for item in self.loss_ledger.entries
        }
        if set(ledger_by_outcome) != set(summaries_by_outcome):
            raise ValueError("loss ledger and observation summaries differ")
        for outcome_sha256, summary in summaries_by_outcome.items():
            entry = ledger_by_outcome[outcome_sha256]
            retained_refs = {
                ref
                for fact_id in summary.retained_fact_ids
                for ref in facts_by_id[fact_id].evidence_refs
            }
            if (
                entry.action_id != summary.action_id
                or entry.source is not summary.source
                or entry.original_record_count != len(summary.evidence_refs)
                or entry.retained_fact_count != len(summary.retained_fact_ids)
                or entry.omitted_record_count
                != len(summary.evidence_refs) - len(retained_refs)
                or entry.omitted_field_categories != _OMITTED_FIELDS[summary.source]
            ):
                raise ValueError("memory loss entry differs from observation summary")
        if any(not set(item.evidence_refs).issubset(ref_set) for item in self.salient_facts):
            raise ValueError("salient fact contains an unresolved evidence ref")
        if any(not set(item.evidence_refs).issubset(ref_set) for item in self.predicates):
            raise ValueError("predicate contains an unresolved evidence ref")
        source_by_predicate = {
            PredicateKindV22.METRIC_ERROR_RATE_STRONG: EvidenceSourceV22.METRICS,
            PredicateKindV22.METRIC_LATENCY_STRONG: EvidenceSourceV22.METRICS,
            PredicateKindV22.METRIC_MEMORY_STRONG: EvidenceSourceV22.METRICS,
            PredicateKindV22.RUNTIME_NOT_RUNNING: EvidenceSourceV22.RUNTIME,
            PredicateKindV22.RUNTIME_UNHEALTHY: EvidenceSourceV22.RUNTIME,
            PredicateKindV22.RUNTIME_HEALTHY: EvidenceSourceV22.RUNTIME,
            PredicateKindV22.RUNTIME_RESTART_PRESSURE: EvidenceSourceV22.RUNTIME,
            PredicateKindV22.RESOURCE_CPU_STRONG: EvidenceSourceV22.RESOURCES,
            PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG: EvidenceSourceV22.RESOURCES,
            PredicateKindV22.TRACE_FIRST_ERROR: EvidenceSourceV22.TRACES,
            PredicateKindV22.TRACE_DEPENDENCY_LATENCY: EvidenceSourceV22.TRACES,
            PredicateKindV22.LOG_CONFIGURATION_ERROR: EvidenceSourceV22.LOGS,
            PredicateKindV22.LOG_DEPENDENCY_TIMEOUT: EvidenceSourceV22.LOGS,
            PredicateKindV22.LOG_MEMORY_PRESSURE: EvidenceSourceV22.LOGS,
            PredicateKindV22.CHANGE_RECENT_ROLLOUT: EvidenceSourceV22.CHANGES,
        }
        for predicate in self.predicates:
            if predicate.source is not source_by_predicate[predicate.predicate_kind]:
                raise ValueError("predicate source differs from kind")
            if any(
                refs_by_value[ref].source is not predicate.source
                for ref in predicate.evidence_refs
            ):
                raise ValueError("predicate source differs from evidence refs")
            selected_sources = {
                fact.service
                for fact in self.salient_facts
                if set(fact.evidence_refs).intersection(predicate.evidence_refs)
            }
            if selected_sources and selected_sources != {predicate.service}:
                raise ValueError("predicate service differs from retained fact")
        context = info.context if isinstance(info.context, dict) else None
        if (
            context is None
            or not isinstance(context.get("baseline"), BaselineProfileV22)
            or not isinstance(context.get("outcomes"), tuple)
            or not isinstance(context.get("top_k"), int)
        ):
            raise ValueError("salient memory requires authoritative predicate provenance")
        baseline = BaselineProfileV22.model_validate(
            context["baseline"].model_dump(mode="python")
        )
        if baseline.baseline_sha256 != self.baseline_sha256:
            raise ValueError("salient memory baseline differs from provenance")
        material = _materialize_trajectory(
            outcomes=context["outcomes"],
            baseline=baseline,
            observed_at=self.observed_at,
            thresholds=PredicateThresholdsV22.frozen(),
        )
        expected_authoritative_refs = tuple(
            sorted(material.all_refs, key=lambda item: item.evidence_ref)
        )
        expected_facts = _select_salient_facts(
            facts=material.all_facts,
            top_k=context["top_k"],
        )
        expected_projection = _build_memory_projection(
            material=material,
            selected=expected_facts,
        )
        from ecomsre.dta_v2.v22.predicates import PredicateExtractorV22

        expected_predicates = PredicateExtractorV22(
            thresholds=PredicateThresholdsV22.frozen()
        ).extract(facts=tuple(sorted(material.all_facts, key=lambda item: item.fact_id)))
        if self.evidence_refs != expected_authoritative_refs:
            raise ValueError("salient evidence refs differ from authoritative provenance")
        if self.salient_facts != expected_facts:
            raise ValueError("salient facts differ from authoritative provenance")
        if self.predicates != expected_predicates:
            raise ValueError("salient predicates differ from authoritative predicate provenance")
        if self.observation_summaries != expected_projection.summaries:
            raise ValueError("observation summaries differ from authoritative provenance")
        if self.loss_ledger != expected_projection.loss_ledger:
            raise ValueError("memory loss ledger differs from authoritative provenance")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"memory_sha256"})
        )
        if self.memory_sha256 != expected:
            raise ValueError("salient memory digest differs")
        return self


class FullEvidenceMemoryV22(DtaModelV22):
    schema_version: Literal["dta-v22.full-evidence-memory.v1"]
    baseline_sha256: Sha256V22
    observed_at: datetime
    minimal_index: tuple[MinimalObservationIndexV22, ...]
    full_observations: tuple[MemoryReadOutcomeV22, ...]
    memory_sha256: Sha256V22

    @model_validator(mode="after")
    def require_memory(self) -> FullEvidenceMemoryV22:
        _require_utc(self.observed_at)
        if len(self.minimal_index) != len(self.full_observations):
            raise ValueError("full memory index and observations differ")
        for index, outcome in zip(self.minimal_index, self.full_observations):
            expected_refs = tuple(
                _evidence_ref(outcome, ordinal, record).evidence_ref
                for ordinal, record in enumerate(outcome.records)
            )
            if (
                index.action_id != outcome.action_id
                or index.source is not outcome.source
                or index.status is not outcome.status
                or index.request_sha256 != outcome.request_sha256
                or index.outcome_sha256 != outcome.outcome_sha256
                or index.evidence_refs != expected_refs
            ):
                raise ValueError("full memory index does not bind observation")
            if (
                outcome.source is EvidenceSourceV22.RUNTIME
                and not isinstance(outcome, RuntimeReadOutcomeV22)
            ):
                raise ValueError("full memory lacks an authoritative runtime observation")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"memory_sha256"})
        )
        if self.memory_sha256 != expected:
            raise ValueError("full memory digest differs")
        return self


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("memory timestamp must be timezone-aware UTC")


def _project_pr_b_runtime_records(
    source_outcome: ReadOutcomeV22,
    source_observation: ReadToolObservation,
) -> tuple[RuntimeObservationV22, ...]:
    if source_outcome.source is not EvidenceSourceV22.RUNTIME:
        raise ValueError("runtime projection requires a PR-B runtime outcome")
    expected_observation_status = (
        ObservationStatus.SUCCESS
        if source_outcome.status
        in {
            ReadSourceStatusV22.SUCCESS_NONEMPTY,
            ReadSourceStatusV22.SUCCESS_EMPTY,
        }
        else ObservationStatus.FAILURE
    )
    if (
        source_observation.tool is not ToolName.INSPECT_SERVICE_RUNTIME
        or source_observation.source.value != EvidenceSourceV22.RUNTIME.value
        or source_observation.status is not expected_observation_status
    ):
        raise ValueError("runtime projection source lacks approved read authority")
    source_records = tuple(
        item for item in source_observation.results if isinstance(item, RuntimeRecord)
    )
    if len(source_records) != len(source_observation.results):
        raise ValueError("runtime projection source contains a non-runtime record")
    source_by_service = {item.logical_service: item for item in source_records}
    if len(source_by_service) != len(source_records):
        raise ValueError("runtime projection source contains duplicate services")
    records: list[RuntimeObservationV22] = []
    for record in source_outcome.records:
        if not isinstance(record, RuntimeRecordV22):
            raise ValueError("runtime projection source contains a non-runtime record")
        source = source_by_service.get(record.service)
        if (
            source is None
            or source.state.value != record.state.value
            or (source.health is HealthState.HEALTHY) != record.healthy
            or source.restart_count != record.restart_count
        ):
            raise ValueError("runtime projection differs from approved read observation")
        records.append(
            RuntimeObservationV22(
                schema_version="dta-v22.runtime-observation.v1",
                service=record.service,
                state=record.state,
                healthy=record.healthy,
                endpoint=source.endpoint_state,
                restart_count=record.restart_count,
                exit_code=source.exit_code,
            )
        )
    if set(source_by_service) != {item.service for item in records}:
        raise ValueError("runtime projection source service set differs")
    return tuple(records)


def _ratio(value: float, baseline: float) -> float | None:
    if baseline == 0:
        return None
    return value / baseline


def _metric_strength(
    record: MetricFactV22,
    baseline: BaselineMetricStatV22 | None,
    thresholds: PredicateThresholdsV22,
) -> SignalStrengthV22:
    if (
        record.support_status is MetricSupportStatusV22.UNSUPPORTED
        or record.value is None
        or baseline is None
    ):
        return SignalStrengthV22.NONE
    ratio = _ratio(record.value, baseline.mean)
    delta = record.value - baseline.mean
    if record.metric_kind is MetricKindV22.ERROR_RATE:
        if (
            ratio is not None
            and ratio >= thresholds.error_rate_strong_ratio
            and delta >= thresholds.error_rate_strong_delta
        ):
            return SignalStrengthV22.STRONG
    elif record.metric_kind is MetricKindV22.LATENCY_P95_MS:
        if (
            ratio is not None
            and ratio >= thresholds.latency_strong_ratio
            and delta >= thresholds.latency_strong_delta_ms
        ):
            return SignalStrengthV22.STRONG
    return SignalStrengthV22.NONE


def _normalize_log(message: str) -> str:
    normalized = message.casefold()
    normalized = re.sub(
        r"\b(?:authorization|api[_-]?key|access[_-]?token|token|client[_-]?secret|"
        r"secret|password|passwd|cookie|session[_-]?id)"
        r"\s*(?::|=|\s)\s*[^\s,;]+",
        "credential=<redacted>",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\bbearer\s+[^\s,;]+",
        "bearer <redacted>",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(['\"])[^'\"\r\n]+\1",
        "<quoted>",
        normalized,
    )
    normalized = re.sub(
        r"\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\b",
        "<email>",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\b[a-z][a-z0-9+.-]*://[^\s,;]+",
        "<url>",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![a-z0-9])(?:/[a-z0-9._-]+){2,}(?:/[^\s,;]*)?",
        "<path>",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\b[a-z]:\\(?:[^\\\s]+\\)+[^\s,;]+",
        "<path>",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\b[0-9a-f]{8,}\b",
        "<hex>",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\b(?=[a-z0-9_-]{20,}\b)(?=[a-z0-9_-]*[a-z])"
        r"(?=[a-z0-9_-]*[0-9])[a-z0-9_-]+\b",
        "<opaque>",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\b([a-z][a-z0-9_-]{1,40})\s*[:=]\s*(?!<)[^\s,;]+",
        r"\1=<value>",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "<num>", normalized)
    normalized = " ".join(normalized.split())
    residual_sensitive = (
        r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+",
        r"[a-z][a-z0-9+.-]*://",
        r"(?:^|\s)/(?:users|home|private|var|tmp)/",
        r"\b(?:api[_-]?key|token|secret|password|passwd|cookie)\s*[:=]"
        r"\s*(?!<redacted>)",
    )
    if any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in residual_sensitive):
        return "<redacted-log-template>"
    return normalized


def _log_category(template: str) -> LogCategoryV22:
    if any(token in template for token in ("invalid config", "configuration", "parse config")):
        return LogCategoryV22.CONFIGURATION_ERROR
    if "timeout" in template:
        return LogCategoryV22.DEPENDENCY_TIMEOUT
    if any(token in template for token in ("out of memory", "oom", "memory pressure")):
        return LogCategoryV22.MEMORY_PRESSURE
    return LogCategoryV22.OTHER


def _downstream(message: str) -> str | None:
    match = re.search(r"\bdownstream[=:]([a-z][a-z0-9-]*)\b", message.casefold())
    return match.group(1) if match else None


def _nearest_rank(values: tuple[float, ...], percentile: float) -> float:
    ordered = tuple(sorted(values))
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _evidence_ref(
    outcome: MemoryReadOutcomeV22,
    index: int,
    record: DtaModelV22,
) -> EvidenceRefV22:
    record_sha = semantic_sha256_v22(record.model_dump(mode="json"))
    return EvidenceRefV22(
        schema_version="dta-v22.evidence-ref.v1",
        evidence_ref=f"e:{outcome.action_id}:{index}:{record_sha[:12]}",
        action_id=outcome.action_id,
        source=outcome.source,
        outcome_sha256=outcome.outcome_sha256,
        record_index=index,
        record_sha256=record_sha,
    )


def _build_fact(
    *,
    source: EvidenceSourceV22,
    service: str,
    evidence_refs: tuple[str, ...],
    signal_strength: SignalStrengthV22,
    payload: SalientPayloadV22,
) -> SalientFactV22:
    canonical_refs = tuple(sorted(set(evidence_refs)))
    identity = semantic_sha256_v22(
        {
            "source": source.value,
            "service": service,
            "evidence_refs": canonical_refs,
            "payload": payload.model_dump(mode="json"),
        }
    )
    value: dict[str, Any] = {
        "schema_version": "dta-v22.salient-fact.v1",
        "fact_id": f"f:{source.value.casefold()}:{identity[:16]}",
        "source": source,
        "service": service,
        "evidence_refs": canonical_refs,
        "signal_strength": signal_strength,
        "payload": payload,
    }
    draft = SalientFactV22.model_construct(**value, fact_sha256="0" * 64)
    return SalientFactV22.model_validate(
        {
            **value,
            "fact_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"fact_sha256"})
            ),
        }
    )


def _materialize_record(
    *,
    outcome: MemoryReadOutcomeV22,
    record: DtaModelV22,
    evidence_ref: EvidenceRefV22,
    baseline: BaselineProfileV22,
    observed_at: datetime,
    thresholds: PredicateThresholdsV22,
) -> SalientFactV22:
    reference = (evidence_ref.evidence_ref,)
    if isinstance(record, MetricFactV22):
        stat = baseline.metric(record.service, record.metric_kind)
        baseline_value = stat.mean if stat else None
        value = record.value
        metric_payload = MetricSalientPayloadV22(
            schema_version="dta-v22.salient-metric.v1",
            metric_kind=record.metric_kind,
            support_status=record.support_status,
            sample_count=record.sample_count,
            value=value,
            unit=record.unit,
            baseline_value=baseline_value,
            baseline_ratio=(
                _ratio(value, baseline_value)
                if value is not None and baseline_value is not None
                else None
            ),
            delta=(
                value - baseline_value
                if value is not None and baseline_value is not None
                else None
            ),
            z_score=(
                (value - stat.mean) / stat.standard_deviation
                if value is not None and stat is not None and stat.standard_deviation > 0
                else None
            ),
        )
        return _build_fact(
            source=outcome.source,
            service=record.service,
            evidence_refs=reference,
            signal_strength=_metric_strength(record, stat, thresholds),
            payload=metric_payload,
        )
    if isinstance(record, LogRecordV22):
        template = _normalize_log(record.message)
        category = _log_category(template)
        log_payload = LogSalientPayloadV22(
            schema_version="dta-v22.salient-log.v1",
            severity=record.severity,
            normalized_template=template,
            category=category,
            downstream_service=_downstream(record.message),
            count=1,
        )
        strength = (
            SignalStrengthV22.STRONG
            if category is not LogCategoryV22.OTHER
            else SignalStrengthV22.NONE
        )
        return _build_fact(
            source=outcome.source,
            service=record.service,
            evidence_refs=reference,
            signal_strength=strength,
            payload=log_payload,
        )
    if isinstance(record, TraceSpanV22):
        trace_stat = baseline.trace(record.service, record.operation)
        ratio = (
            _ratio(record.duration_ms, trace_stat.duration_ms)
            if trace_stat
            else None
        )
        delta = (
            record.duration_ms - trace_stat.duration_ms if trace_stat else None
        )
        strength = (
            SignalStrengthV22.STRONG
            if record.first_error_location
            or record.status is SpanStatusV22.ERROR
            or (
                ratio is not None
                and delta is not None
                and ratio >= thresholds.trace_latency_strong_ratio
                and delta >= thresholds.trace_latency_strong_delta_ms
            )
            else SignalStrengthV22.NONE
        )
        trace_payload = TraceSalientPayloadV22(
            schema_version="dta-v22.salient-trace.v1",
            operation=record.operation,
            service_path=record.service_path,
            parent_service=record.parent_service,
            status=record.status,
            first_error_location=record.first_error_location,
            duration_ms=record.duration_ms,
            baseline_duration_ms=trace_stat.duration_ms if trace_stat else None,
            baseline_ratio=ratio,
            delta_ms=delta,
        )
        return _build_fact(
            source=outcome.source,
            service=record.service,
            evidence_refs=reference,
            signal_strength=strength,
            payload=trace_payload,
        )
    if isinstance(record, RuntimeObservationV22):
        strength = (
            SignalStrengthV22.STRONG
            if record.state is not RuntimeStateV22.RUNNING
            or not record.healthy
            or record.restart_count >= thresholds.restart_pressure_count
            else SignalStrengthV22.NONE
        )
        runtime_payload = RuntimeSalientPayloadV22(
            schema_version="dta-v22.salient-runtime.v1",
            state=record.state,
            healthy=record.healthy,
            endpoint=record.endpoint,
            restart_count=record.restart_count,
            exit_code=record.exit_code,
        )
        return _build_fact(
            source=outcome.source,
            service=record.service,
            evidence_refs=reference,
            signal_strength=strength,
            payload=runtime_payload,
        )
    if isinstance(record, ResourceUsageRecordV22):
        cpu = tuple(item.cpu_percent for item in record.samples)
        cpu_p95 = _nearest_rank(cpu, 0.95)
        resource_stat = baseline.resource(record.service)
        ratio = (
            _ratio(cpu_p95, resource_stat.cpu_p95_percent)
            if resource_stat
            else None
        )
        cpu_strong = (
            ratio is not None
            and cpu_p95 >= thresholds.cpu_strong_p95_percent
            and ratio >= thresholds.cpu_strong_baseline_ratio
        )
        memory_strong = (
            record.memory_slope_bytes_per_second
            >= thresholds.memory_growth_strong_bytes_per_second
        )
        resource_payload = ResourceSalientPayloadV22(
            schema_version="dta-v22.salient-resource.v1",
            cpu_p50_percent=_nearest_rank(cpu, 0.50),
            cpu_p95_percent=cpu_p95,
            cpu_max_percent=max(cpu),
            memory_start_bytes=record.samples[0].memory_bytes,
            memory_end_bytes=record.samples[-1].memory_bytes,
            memory_delta_bytes=(
                record.samples[-1].memory_bytes - record.samples[0].memory_bytes
            ),
            memory_slope_bytes_per_second=record.memory_slope_bytes_per_second,
            sample_count=len(record.samples),
            baseline_cpu_p95_percent=(
                resource_stat.cpu_p95_percent if resource_stat else None
            ),
            cpu_baseline_ratio=ratio,
            baseline_memory_slope_bytes_per_second=(
                resource_stat.memory_slope_bytes_per_second
                if resource_stat
                else None
            ),
        )
        return _build_fact(
            source=outcome.source,
            service=record.service,
            evidence_refs=reference,
            signal_strength=(
                SignalStrengthV22.STRONG
                if cpu_strong or memory_strong
                else SignalStrengthV22.NONE
            ),
            payload=resource_payload,
        )
    if not isinstance(record, RecentChangeRecordV22):
        raise TypeError("unsupported v2.2 read record")
    relative = int((observed_at - record.observed_at).total_seconds())
    if relative < 0:
        raise ValueError("change observation occurs after memory timestamp")
    change_payload = ChangeSalientPayloadV22(
        schema_version="dta-v22.salient-change.v1",
        category=record.category,
        relative_seconds=relative,
        rollout_state=record.rollout_state,
        revision_digest=record.revision_digest,
    )
    return _build_fact(
        source=outcome.source,
        service=record.service,
        evidence_refs=reference,
        signal_strength=(
            SignalStrengthV22.STRONG
            if relative <= thresholds.recent_change_seconds
            else SignalStrengthV22.NONE
        ),
        payload=change_payload,
    )


def _materialize_log_group(
    *,
    outcome: MemoryReadOutcomeV22,
    records_and_refs: tuple[tuple[LogRecordV22, EvidenceRefV22], ...],
) -> SalientFactV22:
    first = records_and_refs[0][0]
    template = _normalize_log(first.message)
    category = _log_category(template)
    downstream = _downstream(first.message)
    expected_key = (first.service, first.severity, template, category, downstream)
    if any(
        (
            record.service,
            record.severity,
            _normalize_log(record.message),
            _log_category(_normalize_log(record.message)),
            _downstream(record.message),
        )
        != expected_key
        for record, _reference in records_and_refs
    ):
        raise ValueError("log aggregation group is not semantically identical")
    payload = LogSalientPayloadV22(
        schema_version="dta-v22.salient-log.v1",
        severity=first.severity,
        normalized_template=template,
        category=category,
        downstream_service=downstream,
        count=len(records_and_refs),
    )
    return _build_fact(
        source=outcome.source,
        service=first.service,
        evidence_refs=tuple(
            reference.evidence_ref for _record, reference in records_and_refs
        ),
        signal_strength=(
            SignalStrengthV22.STRONG
            if category is not LogCategoryV22.OTHER
            else SignalStrengthV22.NONE
        ),
        payload=payload,
    )


_OMITTED_FIELDS = {
    EvidenceSourceV22.METRICS: ("absolute_window_timestamps",),
    EvidenceSourceV22.LOGS: ("absolute_timestamp", "raw_message"),
    EvidenceSourceV22.TRACES: ("absolute_timestamp",),
    EvidenceSourceV22.RUNTIME: (),
    EvidenceSourceV22.RESOURCES: ("individual_samples",),
    EvidenceSourceV22.CHANGES: ("absolute_timestamp", "opaque_change_id"),
}


def _fact_order(item: SalientFactV22) -> tuple[object, ...]:
    trace = item.payload if isinstance(item.payload, TraceSalientPayloadV22) else None
    trace_priority = (
        0
        if trace is not None and trace.first_error_location
        else 1
        if trace is not None and trace.status is SpanStatusV22.ERROR
        else 2
        if trace is not None
        else 3
    )
    trace_latency = -(trace.baseline_ratio or trace.duration_ms) if trace else 0.0
    return (
        -_SIGNAL_RANK[item.signal_strength],
        trace_priority,
        trace_latency,
        item.source.value,
        item.service,
        item.fact_id,
    )


def _summary(
    outcome: MemoryReadOutcomeV22,
    refs: tuple[str, ...],
    retained: tuple[str, ...],
) -> ObservationSummaryV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.observation-summary.v1",
        "action_id": outcome.action_id,
        "source": outcome.source,
        "status": outcome.status,
        "request_sha256": outcome.request_sha256,
        "outcome_sha256": outcome.outcome_sha256,
        "evidence_refs": tuple(sorted(set(refs))),
        "retained_fact_ids": tuple(sorted(set(retained))),
    }
    draft = ObservationSummaryV22.model_construct(**payload, summary_sha256="0" * 64)
    return ObservationSummaryV22.model_validate(
        {
            **payload,
            "summary_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"summary_sha256"})
            ),
        }
    )


@dataclass(frozen=True)
class _MemoryMaterialV22:
    outcomes: tuple[MemoryReadOutcomeV22, ...]
    refs_by_outcome: dict[str, tuple[EvidenceRefV22, ...]]
    facts_by_outcome: dict[str, tuple[SalientFactV22, ...]]
    all_refs: tuple[EvidenceRefV22, ...]
    all_facts: tuple[SalientFactV22, ...]


@dataclass(frozen=True)
class _MemoryProjectionV22:
    summaries: tuple[ObservationSummaryV22, ...]
    loss_ledger: MemoryLossLedgerV22
    minimal_index: tuple[MinimalObservationIndexV22, ...]


def _validate_memory_outcomes(
    outcomes: tuple[MemoryReadOutcomeV22, ...],
) -> tuple[MemoryReadOutcomeV22, ...]:
    validated: list[MemoryReadOutcomeV22] = []
    for item in outcomes:
        if isinstance(item, RuntimeReadOutcomeV22):
            validated.append(
                RuntimeReadOutcomeV22.model_validate(item.model_dump(mode="python"))
            )
        elif isinstance(item, ReadOutcomeV22):
            read = ReadOutcomeV22.model_validate(item.model_dump(mode="python"))
            if read.source is EvidenceSourceV22.RUNTIME:
                raise ValueError(
                    "runtime memory requires an authoritative runtime observation"
                )
            validated.append(read)
        else:
            raise TypeError("memory trajectory contains an unsupported outcome")
    outcome_ids = tuple(item.outcome_sha256 for item in validated)
    if len(outcome_ids) != len(set(outcome_ids)):
        raise ValueError("memory trajectory contains duplicate outcomes")
    return tuple(validated)


def _materialize_trajectory(
    *,
    outcomes: tuple[MemoryReadOutcomeV22, ...],
    baseline: BaselineProfileV22,
    observed_at: datetime,
    thresholds: PredicateThresholdsV22,
) -> _MemoryMaterialV22:
    validated = _validate_memory_outcomes(outcomes)
    refs_by_outcome: dict[str, tuple[EvidenceRefV22, ...]] = {}
    facts_by_outcome: dict[str, tuple[SalientFactV22, ...]] = {}
    all_refs: list[EvidenceRefV22] = []
    all_facts: list[SalientFactV22] = []
    for outcome in validated:
        refs: list[EvidenceRefV22] = []
        facts: list[SalientFactV22] = []
        log_groups: dict[
            tuple[str, str, str, LogCategoryV22, str | None],
            list[tuple[LogRecordV22, EvidenceRefV22]],
        ] = {}
        for index, record in enumerate(outcome.records):
            reference = _evidence_ref(outcome, index, record)
            refs.append(reference)
            if isinstance(record, LogRecordV22):
                template = _normalize_log(record.message)
                key = (
                    record.service,
                    record.severity,
                    template,
                    _log_category(template),
                    _downstream(record.message),
                )
                log_groups.setdefault(key, []).append((record, reference))
            else:
                facts.append(
                    _materialize_record(
                        outcome=outcome,
                        record=record,
                        evidence_ref=reference,
                        baseline=baseline,
                        observed_at=observed_at,
                        thresholds=thresholds,
                    )
                )
        for group_key in sorted(
            log_groups,
            key=lambda item: tuple(str(value) for value in item),
        ):
            facts.append(
                _materialize_log_group(
                    outcome=outcome,
                    records_and_refs=tuple(log_groups[group_key]),
                )
            )
        refs_by_outcome[outcome.outcome_sha256] = tuple(refs)
        facts_by_outcome[outcome.outcome_sha256] = tuple(facts)
        all_refs.extend(refs)
        all_facts.extend(facts)
    return _MemoryMaterialV22(
        outcomes=validated,
        refs_by_outcome=refs_by_outcome,
        facts_by_outcome=facts_by_outcome,
        all_refs=tuple(all_refs),
        all_facts=tuple(all_facts),
    )


def _select_salient_facts(
    *,
    facts: tuple[SalientFactV22, ...],
    top_k: int,
) -> tuple[SalientFactV22, ...]:
    if not 1 <= top_k <= 256:
        raise ValueError("salient top_k must be between one and 256")
    core_metrics = {
        MetricKindV22.ERROR_RATE,
        MetricKindV22.LATENCY_P95_MS,
        MetricKindV22.REQUEST_SUPPORT,
    }
    mandatory = tuple(
        sorted(
            (
                item
                for item in facts
                if isinstance(item.payload, RuntimeSalientPayloadV22)
                or (
                    isinstance(item.payload, MetricSalientPayloadV22)
                    and item.payload.metric_kind in core_metrics
                    and item.payload.support_status
                    is MetricSupportStatusV22.SUPPORTED
                )
            ),
            key=_fact_order,
        )
    )
    if len(mandatory) > top_k:
        raise ValueError("top_k is smaller than mandatory salient facts")
    mandatory_ids = {item.fact_id for item in mandatory}
    optional = tuple(
        item for item in sorted(facts, key=_fact_order) if item.fact_id not in mandatory_ids
    )
    return tuple(
        sorted((*mandatory, *optional[: top_k - len(mandatory)]), key=_fact_order)
    )


def _build_memory_projection(
    *,
    material: _MemoryMaterialV22,
    selected: tuple[SalientFactV22, ...],
) -> _MemoryProjectionV22:
    selected_ids = {item.fact_id for item in selected}
    summaries: list[ObservationSummaryV22] = []
    loss_entries: list[MemoryLossEntryV22] = []
    minimal: list[MinimalObservationIndexV22] = []
    for outcome in material.outcomes:
        outcome_refs = tuple(
            item.evidence_ref
            for item in material.refs_by_outcome[outcome.outcome_sha256]
        )
        outcome_facts = material.facts_by_outcome[outcome.outcome_sha256]
        retained = tuple(
            item.fact_id for item in outcome_facts if item.fact_id in selected_ids
        )
        retained_refs = {
            ref
            for fact in outcome_facts
            if fact.fact_id in selected_ids
            for ref in fact.evidence_refs
        }
        summaries.append(_summary(outcome, outcome_refs, retained))
        loss_entries.append(
            MemoryLossEntryV22(
                schema_version="dta-v22.memory-loss-entry.v1",
                action_id=outcome.action_id,
                source=outcome.source,
                outcome_sha256=outcome.outcome_sha256,
                original_record_count=len(outcome.records),
                retained_fact_count=len(retained),
                omitted_record_count=len(outcome.records) - len(retained_refs),
                omitted_field_categories=_OMITTED_FIELDS[outcome.source],
                truncated=outcome.truncated,
                artifact_sha256=outcome.outcome_sha256,
            )
        )
        minimal.append(
            MinimalObservationIndexV22(
                schema_version="dta-v22.minimal-observation-index.v1",
                action_id=outcome.action_id,
                source=outcome.source,
                status=outcome.status,
                request_sha256=outcome.request_sha256,
                outcome_sha256=outcome.outcome_sha256,
                evidence_refs=outcome_refs,
            )
        )
    ledger_payload: dict[str, Any] = {
        "schema_version": "dta-v22.memory-loss-ledger.v1",
        "entries": tuple(loss_entries),
    }
    ledger_draft = MemoryLossLedgerV22.model_construct(
        **ledger_payload,
        ledger_sha256="0" * 64,
    )
    ledger = MemoryLossLedgerV22.model_validate(
        {
            **ledger_payload,
            "ledger_sha256": semantic_sha256_v22(
                ledger_draft.model_dump(mode="json", exclude={"ledger_sha256"})
            ),
        }
    )
    return _MemoryProjectionV22(
        summaries=tuple(summaries),
        loss_ledger=ledger,
        minimal_index=tuple(minimal),
    )


def build_memory_views_v22(
    *,
    outcomes: tuple[MemoryReadOutcomeV22, ...],
    baseline: BaselineProfileV22,
    observed_at: datetime,
    top_k: int,
) -> tuple[SalientEvidenceMemoryV22, FullEvidenceMemoryV22]:
    """Build paired representations from one fixed outcome trajectory."""

    _require_utc(observed_at)
    thresholds = PredicateThresholdsV22.frozen()
    material = _materialize_trajectory(
        outcomes=outcomes,
        baseline=baseline,
        observed_at=observed_at,
        thresholds=thresholds,
    )
    validated = material.outcomes
    all_refs = material.all_refs
    all_facts = material.all_facts
    selected = _select_salient_facts(facts=all_facts, top_k=top_k)
    projection = _build_memory_projection(material=material, selected=selected)

    # Local import keeps the shared predicate contract in this module while the
    # deterministic extractor remains independently testable.
    from ecomsre.dta_v2.v22.predicates import PredicateExtractorV22

    predicates = PredicateExtractorV22(thresholds=thresholds).extract(
        facts=tuple(sorted(all_facts, key=lambda item: item.fact_id))
    )
    salient_payload: dict[str, Any] = {
        "schema_version": "dta-v22.salient-evidence-memory.v1",
        "baseline_sha256": baseline.baseline_sha256,
        "thresholds_sha256": thresholds.thresholds_sha256,
        "observed_at": observed_at,
        "evidence_refs": tuple(sorted(all_refs, key=lambda item: item.evidence_ref)),
        "observation_summaries": projection.summaries,
        "predicates": predicates,
        "salient_facts": selected,
        "loss_ledger": projection.loss_ledger,
    }
    salient_draft = SalientEvidenceMemoryV22.model_construct(
        **salient_payload,
        memory_sha256="0" * 64,
    )
    salient = SalientEvidenceMemoryV22.model_validate(
        {
            **salient_payload,
            "memory_sha256": semantic_sha256_v22(
                salient_draft.model_dump(mode="json", exclude={"memory_sha256"})
            ),
        },
        context={"outcomes": outcomes, "baseline": baseline, "top_k": top_k},
    )

    full_payload: dict[str, Any] = {
        "schema_version": "dta-v22.full-evidence-memory.v1",
        "baseline_sha256": baseline.baseline_sha256,
        "observed_at": observed_at,
        "minimal_index": projection.minimal_index,
        "full_observations": validated,
    }
    full_draft = FullEvidenceMemoryV22.model_construct(
        **full_payload,
        memory_sha256="0" * 64,
    )
    full = FullEvidenceMemoryV22.model_validate(
        {
            **full_payload,
            "memory_sha256": semantic_sha256_v22(
                full_draft.model_dump(mode="json", exclude={"memory_sha256"})
            ),
        }
    )
    return salient, full


__all__ = (
    "BaselineProfileV22",
    "ChangeSalientPayloadV22",
    "EvidencePredicateV22",
    "EvidenceRefV22",
    "FullEvidenceMemoryV22",
    "LogCategoryV22",
    "LogSalientPayloadV22",
    "MemoryReadOutcomeV22",
    "MemoryLossLedgerV22",
    "MetricSalientPayloadV22",
    "ObservationSummaryV22",
    "PredicateKindV22",
    "PredicateThresholdsV22",
    "ResourceSalientPayloadV22",
    "RuntimeObservationV22",
    "RuntimeReadOutcomeV22",
    "RuntimeSalientPayloadV22",
    "SalientEvidenceMemoryV22",
    "SalientFactV22",
    "SignalStrengthV22",
    "TraceSalientPayloadV22",
    "build_memory_views_v22",
)
