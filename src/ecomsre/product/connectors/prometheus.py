"""Bounded read-only Prometheus Product connector."""

from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Callable, Mapping
from urllib.parse import quote

import httpx

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    ReadSourceStatusV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
)
from ecomsre.product.connectors._http import (
    BoundedHttpTransportV1,
    ConnectorRequestError,
    require_mapping,
)
from ecomsre.product.connectors.base import (
    ConnectorAvailabilityV1,
    ConnectorCapabilityV1,
    ConnectorHealthResultV1,
    ConnectorQueryContextV1,
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.contracts import (
    ConnectorConfigV1,
    ConnectorKindV1,
    PrometheusConnectorSettingsV1,
)
from ecomsre.product.pilot.baseline_readiness_v023 import (
    PrometheusTemplateDiagnosticV023,
    PrometheusWindowDiagnosticsV023,
)


_METRIC_KIND_BY_TEMPLATE = {
    "queue_lag": MetricKindV22.QUEUE_LAG,
    "request_support": MetricKindV22.REQUEST_SUPPORT,
    "error_rate": MetricKindV22.ERROR_RATE,
    "latency": MetricKindV22.LATENCY_P95_MS,
    "cpu": MetricKindV22.CPU_PERCENT,
    "memory": MetricKindV22.MEMORY_BYTES,
}
_RESOURCE_ALIGNMENT_TOLERANCE_SECONDS = 0.25


def _query_result(
    *,
    source: EvidenceSourceV22,
    context: ConnectorQueryContextV1,
    records: tuple[MetricFactV22 | ResourceUsageRecordV22, ...],
    covered_services: set[str],
    truncated: bool,
    latency_ms: float,
) -> ConnectorQueryResultV1:
    return ConnectorQueryResultV1.build(
        source=source,
        status=(
            ReadSourceStatusV22.SUCCESS_NONEMPTY
            if records
            else ReadSourceStatusV22.SUCCESS_EMPTY
        ),
        requested_services=context.requested_services,
        covered_services=tuple(sorted(covered_services)),
        window=context.window,
        records=records,
        truncated=truncated,
        safe_error_code=None,
        latency_ms=latency_ms,
    )


def _failure_results(
    context: ConnectorQueryContextV1,
    error: ConnectorRequestError,
) -> tuple[ConnectorQueryResultV1, ...]:
    return tuple(
        ConnectorQueryResultV1.build(
            source=source,
            status=error.status,
            requested_services=context.requested_services,
            covered_services=(),
            window=context.window,
            records=(),
            truncated=False,
            safe_error_code=error.safe_error_code,
            latency_ms=error.latency_ms,
        )
        for source in (EvidenceSourceV22.METRICS, EvidenceSourceV22.RESOURCES)
    )


class PrometheusConnectorV1:
    def __init__(
        self,
        config: ConnectorConfigV1,
        *,
        credential_resolver: CredentialResolverV1,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
        before_request: Callable[[], None] | None = None,
    ) -> None:
        if config.kind is not ConnectorKindV1.PROMETHEUS or config.endpoint is None:
            raise ValueError("Prometheus connector configuration is invalid")
        self.config = config
        self._settings = PrometheusConnectorSettingsV1.model_validate(config.settings)
        self._endpoint = config.endpoint.rstrip("/")
        self._http = BoundedHttpTransportV1(
            credential_resolver=credential_resolver,
            credential_refs=config.credential_refs,
            timeout_seconds=timeout_seconds,
            maximum_response_bytes=self._settings.maximum_response_bytes,
            transport=transport,
            before_request=before_request,
        )
        self._last_baseline_diagnostics_v023: (
            PrometheusWindowDiagnosticsV023 | None
        ) = None

    def capabilities(self) -> tuple[ConnectorCapabilityV1, ...]:
        return tuple(
            ConnectorCapabilityV1(
                source=source,
                supports_historical_range=True,
                supports_multi_target=True,
                supports_service_discovery=True,
                supports_baseline=True,
                # Label discovery is endpoint-wide. It does not prove that every
                # configured template has samples for every discovered service.
                supports_target_complete_coverage=False,
                maximum_window_seconds=3600,
            )
            for source in (EvidenceSourceV22.METRICS, EvidenceSourceV22.RESOURCES)
        )

    def verify(self) -> ConnectorHealthResultV1:
        try:
            services, latency_ms = self._label_values()
        except ConnectorRequestError as error:
            return ConnectorHealthResultV1(
                connector_name=self.config.name,
                kind=self.config.kind,
                status=ConnectorAvailabilityV1.UNAVAILABLE,
                capabilities=self.capabilities(),
                discovered_services=(),
                safe_error_code=error.safe_error_code,
                latency_ms=error.latency_ms,
            )
        return ConnectorHealthResultV1(
            connector_name=self.config.name,
            kind=self.config.kind,
            status=ConnectorAvailabilityV1.AVAILABLE,
            capabilities=self.capabilities(),
            discovered_services=services,
            safe_error_code=None,
            latency_ms=latency_ms,
        )

    def query(
        self,
        context: ConnectorQueryContextV1,
    ) -> tuple[ConnectorQueryResultV1, ...]:
        context = ConnectorQueryContextV1.model_validate(context.model_dump())
        self._last_baseline_diagnostics_v023 = None
        if (
            context.requested_source is EvidenceSourceV22.METRICS
            and not context.metric_kinds
        ) or (
            context.requested_source is EvidenceSourceV22.RESOURCES
            and (
                context.sampling_window_seconds is None
                or context.sample_count is None
            )
        ):
            return _failure_results(
                context,
                ConnectorRequestError(
                    ReadSourceStatusV22.FAILURE_SCHEMA,
                    "CONNECTOR_ACTION_CONTRACT_INVALID",
                    0.0,
                ),
            )
        selected_templates = tuple(
            (name, template)
            for name, template in self._settings.query_templates.items()
            if context.requested_source is None
            or (
                context.requested_source is EvidenceSourceV22.METRICS
                and _METRIC_KIND_BY_TEMPLATE[name] in set(context.metric_kinds)
            )
            or (
                context.requested_source is EvidenceSourceV22.RESOURCES
                and name in {"cpu", "memory"}
            )
        )
        metric_samples: dict[
            tuple[str, MetricKindV22],
            list[tuple[float, float]],
        ] = {}
        series_by_service: dict[str, dict[str, list[tuple[float, float]]]] = {}
        template_sample_counts: dict[tuple[str, str], int] = {}
        attempted_templates: set[tuple[str, str]] = set()
        failed_template: tuple[str, str] | None = None
        truncated = False
        latency_ms = 0.0
        try:
            for service in context.requested_services:
                for source_alias in context.aliases_for(service):
                    for template_name, template in selected_templates:
                        failed_template = (service, template_name)
                        samples, query_truncated, query_latency = self._query_range(
                            template=self._render_template(template, source_alias, context),
                            context=context,
                        )
                        latency_ms += query_latency
                        truncated = truncated or query_truncated
                        attempted_templates.add((service, template_name))
                        template_sample_counts[(service, template_name)] = (
                            template_sample_counts.get((service, template_name), 0)
                            + len(samples)
                        )
                        failed_template = None
                        if not samples:
                            continue
                        metric_kind = _METRIC_KIND_BY_TEMPLATE[template_name]
                        if context.requested_source is not EvidenceSourceV22.RESOURCES:
                            metric_samples.setdefault(
                                (service, metric_kind),
                                [],
                            ).extend(samples)
                        series_by_service.setdefault(service, {}).setdefault(
                            template_name,
                            [],
                        ).extend(samples)
        except (ConnectorRequestError, ValueError) as error:
            failure = (
                error
                if isinstance(error, ConnectorRequestError)
                else ConnectorRequestError(
                    ReadSourceStatusV22.FAILURE_SCHEMA,
                    "CONNECTOR_SCHEMA_INVALID",
                    latency_ms,
                )
            )
            failed_results = _failure_results(context, failure)
            self._capture_baseline_diagnostics_v023(
                context=context,
                results=failed_results,
                attempted_templates=attempted_templates,
                template_sample_counts=template_sample_counts,
                failed_template=failed_template,
                failure=failure,
            )
            return failed_results

        metric_records: list[MetricFactV22] = []
        for (service, metric_kind), samples in sorted(
            metric_samples.items(),
            key=lambda item: (item[0][0], item[0][1].value),
        ):
            bounded_samples = samples[: self._settings.maximum_sample_count]
            if len(bounded_samples) != len(samples):
                truncated = True
            metric_records.append(
                MetricFactV22(
                schema_version="dta-v22.metric-fact.v1",
                service=service,
                metric_kind=metric_kind,
                support_status=MetricSupportStatusV22.SUPPORTED,
                sample_count=len(bounded_samples),
                value=(
                    sum(value for _, value in bounded_samples)
                    / len(bounded_samples)
                ),
                unit=METRIC_UNIT_BY_KIND_V22[metric_kind],
                window_started_at=context.window.started_at,
                window_ended_at=context.window.ended_at,
            )
            )
        maximum_records = min(context.maximum_records, 200)
        if len(metric_records) > maximum_records:
            metric_records = metric_records[:maximum_records]
            truncated = True
        resource_records: list[ResourceUsageRecordV22] = []
        for service in context.requested_services:
            series = series_by_service.get(service, {})
            resource = self._resource_record(
                service,
                series.get("cpu"),
                series.get("memory"),
                sampling_window_seconds=context.sampling_window_seconds,
                sample_count=context.sample_count,
            )
            if resource is not None:
                resource_records.append(resource)
        if len(resource_records) > maximum_records:
            resource_records = resource_records[:maximum_records]
            truncated = True
        results = (
            _query_result(
                source=EvidenceSourceV22.METRICS,
                context=context,
                records=tuple(metric_records),
                covered_services={item.service for item in metric_records},
                truncated=truncated,
                latency_ms=latency_ms,
            ),
            _query_result(
                source=EvidenceSourceV22.RESOURCES,
                context=context,
                records=tuple(resource_records),
                covered_services={item.service for item in resource_records},
                truncated=truncated,
                latency_ms=latency_ms,
            ),
        )
        self._capture_baseline_diagnostics_v023(
            context=context,
            results=results,
            attempted_templates=attempted_templates,
            template_sample_counts=template_sample_counts,
        )
        return tuple(
            result
            for result in results
            if context.requested_source is None
            or result.source is context.requested_source
        )

    def baseline_diagnostics_v023(self) -> PrometheusWindowDiagnosticsV023 | None:
        """Return the immutable provenance captured by the most recent range query."""

        return self._last_baseline_diagnostics_v023

    def _capture_baseline_diagnostics_v023(
        self,
        *,
        context: ConnectorQueryContextV1,
        results: tuple[ConnectorQueryResultV1, ...],
        attempted_templates: set[tuple[str, str]],
        template_sample_counts: Mapping[tuple[str, str], int],
        failed_template: tuple[str, str] | None = None,
        failure: ConnectorRequestError | None = None,
    ) -> None:
        if context.requested_source is not None or len(results) != 2:
            return
        by_source = {item.source: item for item in results}
        metrics = by_source.get(EvidenceSourceV22.METRICS)
        resources = by_source.get(EvidenceSourceV22.RESOURCES)
        if metrics is None or resources is None:
            return
        keys = set(attempted_templates)
        if failed_template is not None:
            keys.add(failed_template)
        diagnostics = []
        for service, template_name in sorted(keys):
            sample_count = template_sample_counts.get((service, template_name), 0)
            is_failure = failed_template == (service, template_name) and failure is not None
            failure_status = None if failure is None else failure.status
            failure_code = None if failure is None else failure.safe_error_code
            if is_failure:
                if failure_status is None:
                    raise RuntimeError("Prometheus failure diagnostic lacks a status")
                template_status = failure_status
            else:
                template_status = (
                    ReadSourceStatusV22.SUCCESS_NONEMPTY
                    if sample_count
                    else ReadSourceStatusV22.SUCCESS_EMPTY
                )
            diagnostics.append(
                PrometheusTemplateDiagnosticV023.build(
                    template_name=template_name,
                    logical_service=service,
                    status=template_status,
                    sample_count=0 if is_failure else sample_count,
                    safe_error_code=failure_code if is_failure else None,
                )
            )
        self._last_baseline_diagnostics_v023 = PrometheusWindowDiagnosticsV023.build(
            window=context.window,
            metric_result_sha256=metrics.result_sha256,
            resource_result_sha256=resources.result_sha256,
            templates=tuple(diagnostics),
        )

    def query_instant(
        self,
        *,
        template_name: str,
        logical_service: str,
        source_alias: str,
        observed_at: datetime,
    ) -> MetricFactV22 | None:
        template = self._settings.query_templates.get(template_name)
        if template is None:
            raise ValueError("Prometheus instant query template is unavailable")
        window = ConnectorWindowV1(
            started_at=observed_at - timedelta(seconds=1),
            ended_at=observed_at,
        )
        rendered = template
        for marker, value in {
            "{service}": source_alias,
            "{start}": str(int(window.started_at.timestamp())),
            "{end}": str(int(window.ended_at.timestamp())),
            "{step}": str(self._settings.step_seconds),
        }.items():
            rendered = rendered.replace(marker, value)
        payload, _, _latency_ms = self._http.request_json(
            "GET",
            f"{self._endpoint}/api/v1/query",
            params={"query": rendered, "time": observed_at.timestamp()},
        )
        body = require_mapping(payload)
        data = require_mapping(body.get("data"))
        result = data.get("result")
        if (
            body.get("status") != "success"
            or data.get("resultType") != "vector"
            or not isinstance(result, list)
            or len(result) > self._settings.maximum_series_count
        ):
            raise ConnectorRequestError(
                ReadSourceStatusV22.FAILURE_SCHEMA,
                "CONNECTOR_SCHEMA_INVALID",
                0,
            )
        values: list[float] = []
        for raw_series in result:
            raw_value = require_mapping(raw_series).get("value")
            if not isinstance(raw_value, list) or len(raw_value) != 2:
                raise ValueError("Prometheus instant sample is invalid")
            numeric = float(raw_value[1])
            if not math.isfinite(numeric):
                raise ValueError("Prometheus instant sample is non-finite")
            values.append(numeric)
        if not values:
            return None
        metric_kind = _METRIC_KIND_BY_TEMPLATE[template_name]
        return MetricFactV22(
            schema_version="dta-v22.metric-fact.v1",
            service=logical_service,
            metric_kind=metric_kind,
            support_status=MetricSupportStatusV22.SUPPORTED,
            sample_count=len(values),
            value=sum(values) / len(values),
            unit=METRIC_UNIT_BY_KIND_V22[metric_kind],
            window_started_at=window.started_at,
            window_ended_at=window.ended_at,
        )

    def query_series(
        self,
        *,
        matcher: str,
        window: ConnectorWindowV1,
    ) -> tuple[Mapping[str, str], ...]:
        if not matcher or len(matcher) > 4000:
            raise ValueError("Prometheus series matcher is invalid")
        payload, _, latency_ms = self._http.request_json(
            "GET",
            f"{self._endpoint}/api/v1/series",
            params={
                "match[]": matcher,
                "start": window.started_at.timestamp(),
                "end": window.ended_at.timestamp(),
            },
        )
        body = require_mapping(payload)
        data = body.get("data")
        if (
            body.get("status") != "success"
            or not isinstance(data, list)
            or len(data) > self._settings.maximum_series_count
        ):
            raise ConnectorRequestError(
                ReadSourceStatusV22.FAILURE_SCHEMA,
                "CONNECTOR_SCHEMA_INVALID",
                latency_ms,
            )
        series: list[Mapping[str, str]] = []
        for raw_series in data:
            item = require_mapping(raw_series)
            if any(not isinstance(key, str) or not isinstance(value, str) for key, value in item.items()):
                raise ValueError("Prometheus series labels are invalid")
            series.append(dict(sorted(item.items())))
        return tuple(series)

    def _label_values(self) -> tuple[tuple[str, ...], float]:
        label = quote(self._settings.service_label, safe="_:")
        payload, _, latency_ms = self._http.request_json(
            "GET",
            f"{self._endpoint}/api/v1/label/{label}/values",
        )
        body = require_mapping(payload)
        if body.get("status") != "success" or not isinstance(body.get("data"), list):
            raise ConnectorRequestError(
                ReadSourceStatusV22.FAILURE_SCHEMA,
                "CONNECTOR_SCHEMA_INVALID",
                latency_ms,
            )
        data = body["data"]
        if len(data) > self._settings.maximum_series_count or any(
            not isinstance(item, str) or not item for item in data
        ):
            raise ConnectorRequestError(
                ReadSourceStatusV22.FAILURE_SCHEMA,
                "CONNECTOR_SCHEMA_INVALID",
                latency_ms,
            )
        return tuple(sorted(set(data))), latency_ms

    def _query_range(
        self,
        *,
        template: str,
        context: ConnectorQueryContextV1,
    ) -> tuple[list[tuple[float, float]], bool, float]:
        payload, _, latency_ms = self._http.request_json(
            "GET",
            f"{self._endpoint}/api/v1/query_range",
            params={
                "query": template,
                "start": context.window.started_at.timestamp(),
                "end": context.window.ended_at.timestamp(),
                "step": self._query_step(context),
            },
        )
        body = require_mapping(payload)
        data = require_mapping(body.get("data"))
        result = data.get("result")
        if (
            body.get("status") != "success"
            or data.get("resultType") != "matrix"
            or not isinstance(result, list)
        ):
            raise ValueError("Prometheus query result is invalid")
        truncated = len(result) > self._settings.maximum_series_count
        samples: list[tuple[float, float]] = []
        for series in result[: self._settings.maximum_series_count]:
            series_body = require_mapping(series)
            values = series_body.get("values")
            if not isinstance(values, list):
                raise ValueError("Prometheus sample series is invalid")
            for pair in values:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("Prometheus sample is invalid")
                timestamp = float(pair[0])
                value = float(pair[1])
                if not math.isfinite(timestamp):
                    raise ValueError("Prometheus sample timestamp is non-finite")
                # Prometheus legitimately returns NaN for sparse histogram
                # quantiles. It is missing evidence, not a schema failure.
                if not math.isfinite(value):
                    continue
                tolerance_seconds = 1.0
                if not (
                    context.window.started_at.timestamp() - tolerance_seconds
                    <= timestamp
                    <= context.window.ended_at.timestamp() + tolerance_seconds
                ):
                    raise ValueError("Prometheus sample is outside the requested window")
                timestamp = min(
                    context.window.ended_at.timestamp(),
                    max(context.window.started_at.timestamp(), timestamp),
                )
                samples.append((timestamp, value))
        if len(samples) > self._settings.maximum_sample_count:
            samples = samples[: self._settings.maximum_sample_count]
            truncated = True
        samples.sort(key=lambda item: item[0])
        return samples, truncated, latency_ms

    def _render_template(
        self,
        template: str,
        service: str,
        context: ConnectorQueryContextV1,
    ) -> str:
        replacements = {
            "{service}": service,
            "{start}": str(int(context.window.started_at.timestamp())),
            "{end}": str(int(context.window.ended_at.timestamp())),
            "{step}": str(self._query_step(context)),
        }
        rendered = template
        for marker, value in replacements.items():
            rendered = rendered.replace(marker, value)
        return rendered

    @staticmethod
    def _resource_record(
        service: str,
        cpu_samples: list[tuple[float, float]] | None,
        memory_samples: list[tuple[float, float]] | None,
        *,
        sampling_window_seconds: int | None,
        sample_count: int | None,
    ) -> ResourceUsageRecordV22 | None:
        if not cpu_samples or not memory_samples:
            return None
        if sampling_window_seconds is not None and sample_count is not None:
            aligned = PrometheusConnectorV1._align_resource_samples(
                cpu_samples,
                memory_samples,
                sampling_window_seconds=sampling_window_seconds,
                sample_count=sample_count,
            )
            if aligned is None:
                return None
            if any(not memory_value.is_integer() for _, _, memory_value in aligned):
                return None
            samples = tuple(
                ResourceSampleV22(
                    offset_ms=int(
                        round(
                            index
                            * sampling_window_seconds
                            / (sample_count - 1)
                            * 1000
                        )
                    ),
                    cpu_percent=cpu_value,
                    memory_bytes=int(memory_value),
                )
                for index, (_, cpu_value, memory_value) in enumerate(aligned)
            )
            return ResourceUsageRecordV22(
                schema_version="dta-v22.resource-usage-record.v1",
                service=service,
                sampling_window_seconds=sampling_window_seconds,
                samples=samples,
                memory_slope_bytes_per_second=(
                    samples[-1].memory_bytes - samples[0].memory_bytes
                )
                / sampling_window_seconds,
            )
        cpu = {timestamp: value for timestamp, value in cpu_samples}
        memory = {timestamp: value for timestamp, value in memory_samples}
        timestamps = sorted(set(cpu).intersection(memory))
        if len(timestamps) < 2:
            return None
        end = timestamps[-1]
        requested_window = sampling_window_seconds or 30
        timestamps = [
            item for item in timestamps if end - item <= requested_window
        ]
        if sampling_window_seconds is not None and sample_count is not None:
            started = end - sampling_window_seconds
            if (
                len(timestamps) < sample_count
                or not math.isclose(timestamps[0], started, abs_tol=1e-6)
            ):
                return None
            indexes = tuple(
                round(index * (len(timestamps) - 1) / (sample_count - 1))
                for index in range(sample_count)
            )
            timestamps = [timestamps[index] for index in indexes]
        else:
            timestamps = timestamps[-10:]
        if len(timestamps) < 2:
            return None
        started = timestamps[0]
        duration = end - started
        if (
            duration < 1
            or duration > requested_window
            or not float(duration).is_integer()
            or (
                sampling_window_seconds is not None
                and duration != sampling_window_seconds
            )
        ):
            return None
        if any(not memory[item].is_integer() for item in timestamps):
            return None
        samples = tuple(
            ResourceSampleV22(
                offset_ms=int(round((timestamp - started) * 1000)),
                cpu_percent=cpu[timestamp],
                memory_bytes=int(memory[timestamp]),
            )
            for timestamp in timestamps
        )
        return ResourceUsageRecordV22(
            schema_version="dta-v22.resource-usage-record.v1",
            service=service,
            sampling_window_seconds=int(duration),
            samples=samples,
            memory_slope_bytes_per_second=(
                samples[-1].memory_bytes - samples[0].memory_bytes
            )
            / duration,
        )

    @staticmethod
    def _align_resource_samples(
        cpu_samples: list[tuple[float, float]],
        memory_samples: list[tuple[float, float]],
        *,
        sampling_window_seconds: int,
        sample_count: int,
    ) -> tuple[tuple[float, float, float], ...] | None:
        if sample_count < 2:
            return None
        cpu = sorted(cpu_samples)
        memory = sorted(memory_samples)
        if len(cpu) < sample_count or len(memory) < sample_count:
            return None
        step = sampling_window_seconds / (sample_count - 1)
        end = min(cpu[-1][0], memory[-1][0])
        targets = tuple(
            end - sampling_window_seconds + index * step
            for index in range(sample_count)
        )

        def nearest(
            series: list[tuple[float, float]],
        ) -> tuple[tuple[float, float], ...] | None:
            selected: list[tuple[float, float]] = []
            used: set[int] = set()
            for target in targets:
                candidates = (
                    (abs(timestamp - target), index, timestamp, value)
                    for index, (timestamp, value) in enumerate(series)
                    if index not in used
                )
                distance, index, timestamp, value = min(candidates)
                if distance > _RESOURCE_ALIGNMENT_TOLERANCE_SECONDS:
                    return None
                used.add(index)
                selected.append((timestamp, value))
            return tuple(selected)

        selected_cpu = nearest(cpu)
        selected_memory = nearest(memory)
        if selected_cpu is None or selected_memory is None:
            return None
        return tuple(
            (
                targets[index],
                selected_cpu[index][1],
                selected_memory[index][1],
            )
            for index in range(sample_count)
        )

    def _query_step(self, context: ConnectorQueryContextV1) -> float | int:
        if (
            context.requested_source is EvidenceSourceV22.RESOURCES
            and context.sampling_window_seconds is not None
            and context.sample_count is not None
        ):
            return context.sampling_window_seconds / (context.sample_count - 1)
        return self._settings.step_seconds

    def close(self) -> None:
        self._http.close()


__all__ = ("PrometheusConnectorV1",)
