"""Deterministic in-process connector used only by Product tests and demos."""

from __future__ import annotations

from datetime import timedelta

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    ReadRecordV22,
    ReadSourceStatusV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    SpanStatusV22,
    TraceSpanV22,
)
from ecomsre.product.connectors.base import (
    ConnectorAvailabilityV1,
    ConnectorCapabilityV1,
    ConnectorHealthResultV1,
    ConnectorQueryContextV1,
    ConnectorQueryPurposeV1,
    ConnectorQueryResultV1,
)
from ecomsre.product.contracts import (
    ConnectorConfigV1,
    ConnectorKindV1,
    FixtureConnectorSettingsV1,
)


_SUPPORTED_DATASETS = {
    "increment-1",
    "product-increment-1",
    "capture-7f31",
    "capture-c2aa",
    "product-mvp-demo",
    "product-knowledge-loop",
}


class FixtureConnectorV1:
    """Produce observer-shaped records without evaluator-only truth fields."""

    def __init__(self, config: ConnectorConfigV1) -> None:
        if config.kind is not ConnectorKindV1.FIXTURE:
            raise ValueError("Fixture connector configuration is invalid")
        self.config = config
        self._settings = FixtureConnectorSettingsV1.model_validate(config.settings)

    def capabilities(self) -> tuple[ConnectorCapabilityV1, ...]:
        return tuple(sorted((
            ConnectorCapabilityV1(
                source=source,
                supports_historical_range=True,
                supports_multi_target=True,
                supports_service_discovery=False,
                supports_baseline=True,
                supports_target_complete_coverage=True,
                maximum_window_seconds=3600,
            )
            for source in EvidenceSourceV22
        ), key=lambda item: item.source.value))

    def verify(self) -> ConnectorHealthResultV1:
        available = self._settings.dataset in _SUPPORTED_DATASETS
        return ConnectorHealthResultV1(
            connector_name=self.config.name,
            kind=self.config.kind,
            status=(
                ConnectorAvailabilityV1.AVAILABLE
                if available
                else ConnectorAvailabilityV1.UNAVAILABLE
            ),
            capabilities=self.capabilities(),
            discovered_services=(),
            safe_error_code=None if available else "FIXTURE_DATASET_UNKNOWN",
            latency_ms=0.0,
        )

    def query(
        self,
        context: ConnectorQueryContextV1,
    ) -> tuple[ConnectorQueryResultV1, ...]:
        if self._settings.dataset not in _SUPPORTED_DATASETS:
            return tuple(
                self._result(
                    source=source,
                    context=context,
                    records=(),
                    status=ReadSourceStatusV22.FAILURE_UNAVAILABLE,
                    safe_error_code="FIXTURE_DATASET_UNKNOWN",
                )
                for source in EvidenceSourceV22
            )
        sources = (
            tuple(EvidenceSourceV22)
            if context.requested_source is None
            else (context.requested_source,)
        )
        current_observation = context.purpose is ConnectorQueryPurposeV1.INCIDENT
        knowledge_failure_slot = (
            current_observation
            and self._settings.dataset == "product-knowledge-loop"
            and context.window.ended_at.minute % 10 == 8
        )
        results = []
        for source in sources:
            if knowledge_failure_slot and source is EvidenceSourceV22.LOGS:
                results.append(
                    self._result(
                        source=source,
                        context=context,
                        records=(),
                        status=ReadSourceStatusV22.FAILURE_UNAVAILABLE,
                        safe_error_code="FIXTURE_SOURCE_UNAVAILABLE",
                    )
                )
            else:
                results.append(
                    self._result(
                        source=source,
                        context=context,
                        records=self._records(
                            source=source,
                            context=context,
                            current_observation=current_observation,
                        ),
                    )
                )
        return tuple(results)

    def _records(
        self,
        *,
        source: EvidenceSourceV22,
        context: ConnectorQueryContextV1,
        current_observation: bool,
    ) -> tuple[ReadRecordV22, ...]:
        records: list[ReadRecordV22] = []
        observed_at = context.window.ended_at - timedelta(milliseconds=1)
        knowledge_slot = (
            context.window.ended_at.minute % 10
            if current_observation
            and self._settings.dataset == "product-knowledge-loop"
            else None
        )
        for service in context.requested_services:
            if source is EvidenceSourceV22.METRICS:
                metric_kinds = context.metric_kinds or (
                    MetricKindV22.REQUEST_SUPPORT,
                    MetricKindV22.ERROR_RATE,
                    MetricKindV22.LATENCY_P95_MS,
                )
                values = {
                    MetricKindV22.REQUEST_SUPPORT: 100.0,
                    MetricKindV22.ERROR_RATE: 0.01,
                    MetricKindV22.LATENCY_P95_MS: 10.0,
                }
                records.extend(
                    MetricFactV22(
                        schema_version="dta-v22.metric-fact.v1",
                        service=service,
                        metric_kind=kind,
                        support_status=MetricSupportStatusV22.SUPPORTED,
                        sample_count=4,
                        value=values[kind],
                        unit=METRIC_UNIT_BY_KIND_V22[kind],
                        window_started_at=context.window.started_at,
                        window_ended_at=context.window.ended_at,
                    )
                    for kind in metric_kinds
                    if kind in values
                )
            elif source is EvidenceSourceV22.LOGS:
                unknown = (
                    current_observation
                    and (
                        self._settings.dataset == "capture-c2aa"
                        or knowledge_slot in {0, 1, 2, 6, 7, 8, 9}
                    )
                )
                records.append(
                    LogRecordV22(
                        schema_version="dta-v22.log-record.v1",
                        observed_at=observed_at,
                        service=service,
                        severity="ERROR" if unknown else "DIAGNOSTIC",
                        message=(
                            "opaque mutex convoy detected"
                            if unknown
                            else "bounded fixture observation is healthy"
                        ),
                    )
                )
            elif source is EvidenceSourceV22.TRACES:
                records.append(
                    TraceSpanV22(
                        schema_version="dta-v22.trace-span.v1",
                        observed_at=observed_at,
                        service_path=(service,),
                        service=service,
                        parent_service=None,
                        operation="fixture-observation",
                        status=SpanStatusV22.OK,
                        duration_ms=10.0,
                        first_error_location=False,
                    )
                )
            elif source is EvidenceSourceV22.RUNTIME:
                unavailable = (
                    current_observation
                    and (
                        self._settings.dataset == "capture-7f31"
                        or knowledge_slot == 3
                    )
                )
                records.append(
                    RuntimeRecordV22(
                        schema_version="dta-v22.runtime-record.v1",
                        service=service,
                        state=(
                            RuntimeStateV22.EXITED
                            if unavailable
                            else RuntimeStateV22.RUNNING
                        ),
                        healthy=not unavailable,
                        restart_count=0,
                    )
                )
            elif source is EvidenceSourceV22.RESOURCES:
                sampling_window = context.sampling_window_seconds or 30
                sample_count = context.sample_count or 2
                records.append(
                    ResourceUsageRecordV22(
                        schema_version="dta-v22.resource-usage-record.v1",
                        service=service,
                        sampling_window_seconds=sampling_window,
                        samples=tuple(
                            ResourceSampleV22(
                                offset_ms=round(
                                    index * sampling_window * 1000 / (sample_count - 1)
                                ),
                                cpu_percent=10.0 + index,
                                memory_bytes=1000,
                            )
                            for index in range(sample_count)
                        ),
                        memory_slope_bytes_per_second=0.0,
                    )
                )
        return tuple(records)

    @staticmethod
    def _result(
        *,
        source: EvidenceSourceV22,
        context: ConnectorQueryContextV1,
        records: tuple[ReadRecordV22, ...],
        status: ReadSourceStatusV22 | None = None,
        safe_error_code: str | None = None,
    ) -> ConnectorQueryResultV1:
        bounded = records[: context.maximum_records]
        resolved_status = status or (
            ReadSourceStatusV22.SUCCESS_NONEMPTY
            if bounded
            else ReadSourceStatusV22.SUCCESS_EMPTY
        )
        success = resolved_status in {
            ReadSourceStatusV22.SUCCESS_EMPTY,
            ReadSourceStatusV22.SUCCESS_NONEMPTY,
        }
        return ConnectorQueryResultV1.build(
            source=source,
            status=resolved_status,
            requested_services=context.requested_services,
            covered_services=context.requested_services if success else (),
            window=context.window,
            records=bounded if success else (),
            truncated=len(records) > len(bounded),
            safe_error_code=safe_error_code,
            latency_ms=0.0,
        )

    def close(self) -> None:
        return None


__all__ = ("FixtureConnectorV1",)
