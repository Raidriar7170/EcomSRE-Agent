"""Closed read contracts for the DTA v2.2 canonical action surface."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StringConstraints,
    model_validator,
)


Sha256V22 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
LogicalServiceV22 = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9-]*$",
    ),
]
ActionIdV22 = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=160,
        pattern=r"^a:[a-z0-9][a-z0-9:+-]*$",
    ),
]


class DtaModelV22(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )


def semantic_sha256_v22(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_utc(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


class EvidenceSourceV22(str, Enum):
    METRICS = "METRICS"
    LOGS = "LOGS"
    TRACES = "TRACES"
    RUNTIME = "RUNTIME"
    RESOURCES = "RESOURCES"
    CHANGES = "CHANGES"


class ReadSourceStatusV22(str, Enum):
    SUCCESS_NONEMPTY = "SUCCESS_NONEMPTY"
    SUCCESS_EMPTY = "SUCCESS_EMPTY"
    FAILURE_UNAVAILABLE = "FAILURE_UNAVAILABLE"
    FAILURE_TIMEOUT = "FAILURE_TIMEOUT"
    FAILURE_SCHEMA = "FAILURE_SCHEMA"


class MetricKindV22(str, Enum):
    ERROR_RATE = "ERROR_RATE"
    LATENCY_P95_MS = "LATENCY_P95_MS"
    REQUEST_SUPPORT = "REQUEST_SUPPORT"
    CPU_PERCENT = "CPU_PERCENT"
    MEMORY_BYTES = "MEMORY_BYTES"


class MetricUnitV22(str, Enum):
    RATIO = "RATIO"
    MILLISECONDS = "MILLISECONDS"
    COUNT = "COUNT"
    PERCENT = "PERCENT"
    BYTES = "BYTES"


METRIC_UNIT_BY_KIND_V22 = {
    MetricKindV22.ERROR_RATE: MetricUnitV22.RATIO,
    MetricKindV22.LATENCY_P95_MS: MetricUnitV22.MILLISECONDS,
    MetricKindV22.REQUEST_SUPPORT: MetricUnitV22.COUNT,
    MetricKindV22.CPU_PERCENT: MetricUnitV22.PERCENT,
    MetricKindV22.MEMORY_BYTES: MetricUnitV22.BYTES,
}


class MetricSupportStatusV22(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"


class SpanStatusV22(str, Enum):
    OK = "OK"
    ERROR = "ERROR"
    UNSET = "UNSET"


class RuntimeStateV22(str, Enum):
    RUNNING = "RUNNING"
    EXITED = "EXITED"
    ABSENT = "ABSENT"
    OTHER = "OTHER"


class ChangeCategoryV22(str, Enum):
    CONFIGURATION = "CONFIGURATION"
    DEPLOYMENT = "DEPLOYMENT"
    DEPENDENCY = "DEPENDENCY"
    CAPACITY = "CAPACITY"
    OTHER = "OTHER"


class RolloutStateV22(str, Enum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    ROLLED_BACK = "ROLLED_BACK"
    CANCELLED = "CANCELLED"


class CanonicalReadRequestV22(DtaModelV22):
    schema_version: Literal["dta-v22.canonical-read-request.v1"]
    source: EvidenceSourceV22
    target_services: tuple[LogicalServiceV22, ...] = Field(min_length=1, max_length=4)
    metric_kinds: tuple[MetricKindV22, ...] = Field(max_length=5)
    lookback_seconds: StrictInt | None = Field(default=None, ge=1, le=3600)
    max_results: StrictInt | None = Field(default=None, ge=1, le=20)
    max_records: StrictInt | None = Field(default=None, ge=1, le=20)
    max_spans: StrictInt | None = Field(default=None, ge=1, le=40)
    neighborhood_hops: StrictInt | None = Field(default=None, ge=1, le=2)
    sampling_window_seconds: StrictInt | None = Field(default=None, ge=1, le=30)
    sample_count: StrictInt | None = Field(default=None, ge=2, le=10)
    request_sha256: Sha256V22

    @model_validator(mode="after")
    def require_canonical_request(self) -> CanonicalReadRequestV22:
        if self.target_services != tuple(sorted(set(self.target_services))):
            raise ValueError("canonical request targets are not sorted and unique")
        if self.source not in {
            EvidenceSourceV22.RUNTIME,
            EvidenceSourceV22.RESOURCES,
        } and len(self.target_services) != 1:
            raise ValueError(
                "non-runtime/non-resources canonical request requires exactly one target"
            )
        if self.metric_kinds != tuple(
            sorted(set(self.metric_kinds), key=lambda item: item.value)
        ):
            raise ValueError("canonical metric bundle is not sorted and unique")
        populated = {
            "lookback_seconds": self.lookback_seconds,
            "max_results": self.max_results,
            "max_records": self.max_records,
            "max_spans": self.max_spans,
            "neighborhood_hops": self.neighborhood_hops,
            "sampling_window_seconds": self.sampling_window_seconds,
            "sample_count": self.sample_count,
        }
        expected_fields: dict[EvidenceSourceV22, set[str]] = {
            EvidenceSourceV22.METRICS: {"lookback_seconds", "max_results"},
            EvidenceSourceV22.LOGS: {"lookback_seconds", "max_records"},
            EvidenceSourceV22.TRACES: {
                "lookback_seconds",
                "max_spans",
                "neighborhood_hops",
            },
            EvidenceSourceV22.RUNTIME: {"max_results"},
            EvidenceSourceV22.RESOURCES: {
                "sampling_window_seconds",
                "sample_count",
            },
            EvidenceSourceV22.CHANGES: {"lookback_seconds", "max_records"},
        }
        observed_fields = {key for key, value in populated.items() if value is not None}
        if observed_fields != expected_fields[self.source]:
            raise ValueError("canonical request parameters differ from source contract")
        if self.source is EvidenceSourceV22.METRICS:
            if not self.metric_kinds or self.max_results != len(self.metric_kinds):
                raise ValueError("metrics request does not bind its fixed bundle")
        elif self.metric_kinds:
            raise ValueError("non-metrics request contains metric kinds")
        if (
            self.source is EvidenceSourceV22.RUNTIME
            and self.max_results != len(self.target_services)
        ):
            raise ValueError("runtime result limit does not cover exact targets")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"request_sha256"})
        )
        if self.request_sha256 != expected:
            raise ValueError("canonical request digest does not bind request")
        return self


class MetricFactV22(DtaModelV22):
    schema_version: Literal["dta-v22.metric-fact.v1"]
    service: LogicalServiceV22
    metric_kind: MetricKindV22
    support_status: MetricSupportStatusV22
    sample_count: StrictInt = Field(ge=0, le=10000)
    value: StrictFloat | None
    unit: MetricUnitV22
    window_started_at: datetime
    window_ended_at: datetime

    @model_validator(mode="after")
    def require_support_semantics(self) -> MetricFactV22:
        if self.unit is not METRIC_UNIT_BY_KIND_V22[self.metric_kind]:
            raise ValueError("metric unit differs from metric kind")
        _require_utc(self.window_started_at, field_name="window_started_at")
        _require_utc(self.window_ended_at, field_name="window_ended_at")
        if self.window_ended_at <= self.window_started_at:
            raise ValueError("metric window must end after it starts")
        if self.sample_count == 0:
            if (
                self.support_status is not MetricSupportStatusV22.UNSUPPORTED
                or self.value is not None
            ):
                raise ValueError("zero-sample metric must be UNSUPPORTED without value")
        elif (
            self.support_status is not MetricSupportStatusV22.SUPPORTED
            or self.value is None
        ):
            raise ValueError("supported metric requires samples and a value")
        if self.value is not None:
            if self.metric_kind is MetricKindV22.ERROR_RATE and not 0 <= self.value <= 1:
                raise ValueError("metric error rate must be between zero and one")
            if self.metric_kind in {
                MetricKindV22.LATENCY_P95_MS,
                MetricKindV22.REQUEST_SUPPORT,
                MetricKindV22.CPU_PERCENT,
                MetricKindV22.MEMORY_BYTES,
            } and self.value < 0:
                raise ValueError("metric value cannot be negative for this kind")
        return self


class LogRecordV22(DtaModelV22):
    schema_version: Literal["dta-v22.log-record.v1"]
    observed_at: datetime
    service: LogicalServiceV22
    severity: Literal["WARN", "ERROR", "FATAL", "DIAGNOSTIC"]
    message: Annotated[
        str,
        StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=500),
    ]

    @model_validator(mode="after")
    def require_utc_timestamp(self) -> LogRecordV22:
        _require_utc(self.observed_at, field_name="observed_at")
        return self


class TraceSpanV22(DtaModelV22):
    schema_version: Literal["dta-v22.trace-span.v1"]
    observed_at: datetime
    service_path: tuple[LogicalServiceV22, ...] = Field(min_length=1, max_length=12)
    service: LogicalServiceV22
    parent_service: LogicalServiceV22 | None
    operation: Annotated[
        str,
        StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=160),
    ]
    status: SpanStatusV22
    duration_ms: StrictFloat = Field(ge=0)
    first_error_location: StrictBool

    @model_validator(mode="after")
    def require_causal_path(self) -> TraceSpanV22:
        _require_utc(self.observed_at, field_name="observed_at")
        if self.service_path[-1] != self.service:
            raise ValueError("trace path does not terminate at span service")
        if self.parent_service is None:
            if len(self.service_path) != 1:
                raise ValueError("root trace span has a non-root path")
        elif len(self.service_path) < 2 or self.service_path[-2] != self.parent_service:
            raise ValueError("trace parent is not the immediate causal predecessor")
        if self.first_error_location and self.status is not SpanStatusV22.ERROR:
            raise ValueError("first-error trace span must have ERROR status")
        return self


class RuntimeRecordV22(DtaModelV22):
    schema_version: Literal["dta-v22.runtime-record.v1"]
    service: LogicalServiceV22
    state: RuntimeStateV22
    healthy: StrictBool
    restart_count: StrictInt = Field(ge=0)


class ResourceSampleV22(DtaModelV22):
    offset_ms: StrictInt = Field(ge=0, le=30000)
    cpu_percent: StrictFloat = Field(ge=0)
    memory_bytes: StrictInt = Field(ge=0)


class ResourceUsageRecordV22(DtaModelV22):
    schema_version: Literal["dta-v22.resource-usage-record.v1"]
    service: LogicalServiceV22
    sampling_window_seconds: StrictInt = Field(ge=1, le=30)
    samples: tuple[ResourceSampleV22, ...] = Field(min_length=2, max_length=10)
    memory_slope_bytes_per_second: StrictFloat

    @model_validator(mode="after")
    def require_sampling_schedule(self) -> ResourceUsageRecordV22:
        offsets = tuple(item.offset_ms for item in self.samples)
        if offsets[0] != 0 or offsets[-1] != self.sampling_window_seconds * 1000:
            raise ValueError("resource samples do not span the sampling window")
        if any(left >= right for left, right in zip(offsets, offsets[1:])):
            raise ValueError("resource sample offsets are not strictly increasing")
        return self


class RecentChangeRecordV22(DtaModelV22):
    schema_version: Literal["dta-v22.recent-change-record.v1"]
    opaque_change_id: Annotated[
        str,
        StringConstraints(strict=True, pattern=r"^chg_[0-9a-f]{16,32}$"),
    ]
    service: LogicalServiceV22
    observed_at: datetime
    category: ChangeCategoryV22
    rollout_state: RolloutStateV22
    revision_digest: Sha256V22

    @model_validator(mode="after")
    def require_utc_timestamp(self) -> RecentChangeRecordV22:
        _require_utc(self.observed_at, field_name="observed_at")
        return self


ReadRecordV22: TypeAlias = (
    MetricFactV22
    | LogRecordV22
    | TraceSpanV22
    | RuntimeRecordV22
    | ResourceUsageRecordV22
    | RecentChangeRecordV22
)


def build_canonical_read_request_v22(
    *,
    source: EvidenceSourceV22,
    target_services: tuple[str, ...],
    metric_kinds: tuple[MetricKindV22, ...] = (),
    lookback_seconds: int | None = None,
    max_results: int | None = None,
    max_records: int | None = None,
    max_spans: int | None = None,
    neighborhood_hops: int | None = None,
    sampling_window_seconds: int | None = None,
    sample_count: int | None = None,
) -> CanonicalReadRequestV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.canonical-read-request.v1",
        "source": source,
        "target_services": tuple(sorted(item.strip() for item in target_services)),
        "metric_kinds": tuple(sorted(metric_kinds, key=lambda item: item.value)),
        "lookback_seconds": lookback_seconds,
        "max_results": max_results,
        "max_records": max_records,
        "max_spans": max_spans,
        "neighborhood_hops": neighborhood_hops,
        "sampling_window_seconds": sampling_window_seconds,
        "sample_count": sample_count,
    }
    draft = CanonicalReadRequestV22.model_construct(
        **payload,
        request_sha256="0" * 64,
    )
    return CanonicalReadRequestV22.model_validate(
        {
            **payload,
            "request_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"request_sha256"})
            ),
        }
    )


__all__ = (
    "ActionIdV22",
    "CanonicalReadRequestV22",
    "ChangeCategoryV22",
    "DtaModelV22",
    "EvidenceSourceV22",
    "LogRecordV22",
    "METRIC_UNIT_BY_KIND_V22",
    "MetricFactV22",
    "MetricKindV22",
    "MetricSupportStatusV22",
    "MetricUnitV22",
    "ReadRecordV22",
    "ReadSourceStatusV22",
    "RecentChangeRecordV22",
    "ResourceSampleV22",
    "ResourceUsageRecordV22",
    "RolloutStateV22",
    "RuntimeRecordV22",
    "RuntimeStateV22",
    "Sha256V22",
    "SpanStatusV22",
    "TraceSpanV22",
    "build_canonical_read_request_v22",
    "semantic_sha256_v22",
)
