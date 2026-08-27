"""Closed contracts shared by every Product connector."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import re
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    MetricFactV22,
    MetricKindV22,
    ReadRecordV22,
    ReadSourceStatusV22,
    RecentChangeRecordV22,
    ResourceUsageRecordV22,
    RuntimeRecordV22,
    TraceSpanV22,
    semantic_sha256_v22,
)
from ecomsre.product.contracts import ConnectorConfigV1, ConnectorKindV1, ProductModelV1


_RECORD_SOURCE_BY_TYPE = {
    MetricFactV22: EvidenceSourceV22.METRICS,
    LogRecordV22: EvidenceSourceV22.LOGS,
    TraceSpanV22: EvidenceSourceV22.TRACES,
    RuntimeRecordV22: EvidenceSourceV22.RUNTIME,
    ResourceUsageRecordV22: EvidenceSourceV22.RESOURCES,
    RecentChangeRecordV22: EvidenceSourceV22.CHANGES,
}
_FAILURE_STATUSES = {
    ReadSourceStatusV22.FAILURE_UNAVAILABLE,
    ReadSourceStatusV22.FAILURE_TIMEOUT,
    ReadSourceStatusV22.FAILURE_SCHEMA,
}


def _require_utc(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


class ConnectorWindowV1(ProductModelV1):
    started_at: datetime
    ended_at: datetime

    @model_validator(mode="after")
    def require_bounded_utc_window(self) -> "ConnectorWindowV1":
        _require_utc(self.started_at, field_name="started_at")
        _require_utc(self.ended_at, field_name="ended_at")
        if self.ended_at <= self.started_at:
            raise ValueError("connector window must end after it starts")
        if self.ended_at - self.started_at > timedelta(hours=1):
            raise ValueError("connector window exceeds one hour")
        return self


class ConnectorCapabilityV1(ProductModelV1):
    schema_version: Literal["ecomsre.product.connector-capability.v1"] = (
        "ecomsre.product.connector-capability.v1"
    )
    source: EvidenceSourceV22
    supports_historical_range: bool
    supports_multi_target: bool
    supports_service_discovery: bool
    supports_baseline: bool
    supports_target_complete_coverage: bool
    maximum_window_seconds: int = Field(ge=0, le=3600)


class ConnectorAvailabilityV1(str, Enum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class ConnectorQueryPurposeV1(str, Enum):
    BASELINE = "BASELINE"
    INCIDENT = "INCIDENT"


class ConnectorQueryContextV1(ProductModelV1):
    schema_version: Literal["ecomsre.product.connector-query-context.v1"] = (
        "ecomsre.product.connector-query-context.v1"
    )
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    requested_services: tuple[str, ...] = Field(min_length=1, max_length=20)
    service_aliases: dict[str, str] = Field(default_factory=dict, max_length=1000)
    window: ConnectorWindowV1
    maximum_records: int = Field(ge=1, le=10_000)
    purpose: ConnectorQueryPurposeV1 = ConnectorQueryPurposeV1.INCIDENT
    requested_source: EvidenceSourceV22 | None = None
    request_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    metric_kinds: tuple[MetricKindV22, ...] = ()
    neighborhood_hops: int | None = Field(default=None, ge=1, le=2)
    sampling_window_seconds: int | None = Field(default=None, ge=1, le=30)
    sample_count: int | None = Field(default=None, ge=2, le=10)

    @model_validator(mode="after")
    def require_canonical_services(self) -> "ConnectorQueryContextV1":
        if self.requested_services != tuple(sorted(set(self.requested_services))):
            raise ValueError("requested services are not canonical")
        if tuple(self.service_aliases) != tuple(sorted(self.service_aliases)):
            raise ValueError("service alias map is not canonical")
        if self.metric_kinds != tuple(
            sorted(set(self.metric_kinds), key=lambda item: item.value)
        ):
            raise ValueError("connector metric kinds are not canonical")
        if any(
            not alias
            or len(alias) > 120
            or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", logical)
            for alias, logical in self.service_aliases.items()
        ):
            raise ValueError("service alias map is invalid")
        return self

    def aliases_for(self, logical_service: str) -> tuple[str, ...]:
        aliases = tuple(
            alias
            for alias, logical in self.service_aliases.items()
            if logical == logical_service
        )
        return aliases or (logical_service,)

    def normalize_service(self, source_alias: str) -> str:
        return self.service_aliases.get(source_alias, source_alias)


class ConnectorHealthResultV1(ProductModelV1):
    schema_version: Literal["ecomsre.product.connector-health-result.v1"] = (
        "ecomsre.product.connector-health-result.v1"
    )
    connector_name: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    kind: ConnectorKindV1
    status: ConnectorAvailabilityV1
    capabilities: tuple[ConnectorCapabilityV1, ...] = Field(min_length=1)
    discovered_services: tuple[str, ...] = Field(max_length=1000)
    safe_error_code: str | None = Field(
        default=None,
        pattern=r"^[A-Z][A-Z0-9_]{0,95}$",
    )
    latency_ms: float = Field(ge=0, le=3_600_000, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_health_semantics(self) -> "ConnectorHealthResultV1":
        sources = tuple(item.source for item in self.capabilities)
        if sources != tuple(sorted(set(sources), key=lambda item: item.value)):
            raise ValueError("connector capabilities are not canonical")
        if self.discovered_services != tuple(sorted(set(self.discovered_services))):
            raise ValueError("discovered services are not canonical")
        if (self.status is ConnectorAvailabilityV1.AVAILABLE) != (
            self.safe_error_code is None
        ):
            raise ValueError("connector health safe-error semantics differ")
        return self


class ConnectorQueryResultV1(ProductModelV1):
    schema_version: Literal["ecomsre.product.connector-query-result.v1"] = (
        "ecomsre.product.connector-query-result.v1"
    )
    source: EvidenceSourceV22
    status: ReadSourceStatusV22
    requested_services: tuple[str, ...] = Field(max_length=20)
    covered_services: tuple[str, ...] = Field(max_length=20)
    window: ConnectorWindowV1
    records: tuple[ReadRecordV22, ...] = Field(max_length=10_000)
    truncated: bool
    safe_error_code: str | None = Field(
        default=None,
        pattern=r"^[A-Z][A-Z0-9_]{0,95}$",
    )
    latency_ms: float = Field(ge=0, le=3_600_000, allow_inf_nan=False)
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def build(
        cls,
        *,
        source: EvidenceSourceV22,
        status: ReadSourceStatusV22,
        requested_services: tuple[str, ...],
        covered_services: tuple[str, ...],
        window: ConnectorWindowV1,
        records: tuple[ReadRecordV22, ...],
        truncated: bool,
        safe_error_code: str | None,
        latency_ms: float,
    ) -> "ConnectorQueryResultV1":
        payload: dict[str, Any] = {
            "schema_version": "ecomsre.product.connector-query-result.v1",
            "source": source,
            "status": status,
            "requested_services": requested_services,
            "covered_services": covered_services,
            "window": window,
            "records": records,
            "truncated": truncated,
            "safe_error_code": safe_error_code,
            "latency_ms": latency_ms,
        }
        draft = cls.model_construct(**payload, result_sha256="0" * 64)
        return cls.model_validate(
            {
                **payload,
                "result_sha256": semantic_sha256_v22(
                    draft.model_dump(mode="json", exclude={"result_sha256"})
                ),
            }
        )

    @model_validator(mode="after")
    def require_closed_result(self) -> "ConnectorQueryResultV1":
        if self.requested_services != tuple(sorted(set(self.requested_services))):
            raise ValueError("requested services are not canonical")
        if self.covered_services != tuple(sorted(set(self.covered_services))):
            raise ValueError("covered services are not canonical")
        if (
            self.source is not EvidenceSourceV22.TRACES
            and set(self.covered_services) - set(self.requested_services)
        ):
            raise ValueError("covered services exceed requested services")
        if self.status is ReadSourceStatusV22.SUCCESS_NONEMPTY:
            if not self.records or self.safe_error_code is not None:
                raise ValueError("nonempty success result semantics differ")
        elif self.status is ReadSourceStatusV22.SUCCESS_EMPTY:
            if self.records or self.safe_error_code is not None:
                raise ValueError("empty success result semantics differ")
        elif self.status in _FAILURE_STATUSES:
            if self.records or self.covered_services or self.safe_error_code is None:
                raise ValueError("failure result requires one safe error without records")
            if self.truncated:
                raise ValueError("failure result cannot be truncated")
        else:
            raise ValueError("connector result status is unsupported")
        for record in self.records:
            expected_source = _RECORD_SOURCE_BY_TYPE.get(type(record))
            if expected_source is not self.source:
                raise ValueError("connector record type differs from source")
            if record.service not in self.covered_services:
                raise ValueError("connector record service is not covered")
        payload = self.model_dump(mode="json", exclude={"result_sha256"})
        if self.result_sha256 != semantic_sha256_v22(payload):
            raise ValueError("connector result digest differs")
        return self


class ProductConnectorV1(Protocol):
    config: ConnectorConfigV1

    def capabilities(self) -> tuple[ConnectorCapabilityV1, ...]: ...

    def verify(self) -> ConnectorHealthResultV1: ...

    def query(
        self,
        context: ConnectorQueryContextV1,
    ) -> tuple[ConnectorQueryResultV1, ...]: ...

    def close(self) -> None: ...


__all__ = (
    "ConnectorAvailabilityV1",
    "ConnectorCapabilityV1",
    "ConnectorHealthResultV1",
    "ConnectorQueryContextV1",
    "ConnectorQueryPurposeV1",
    "ConnectorQueryResultV1",
    "ConnectorWindowV1",
    "ProductConnectorV1",
)
