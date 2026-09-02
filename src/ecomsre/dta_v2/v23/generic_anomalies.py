"""Mechanism-independent anomaly extraction from frozen v2.2 memory."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.memory import (
    ChangeSalientPayloadV22,
    LogCategoryV22,
    LogSalientPayloadV22,
    MetricSalientPayloadV22,
    PredicateKindV22,
    ResourceSalientPayloadV22,
    RuntimeSalientPayloadV22,
    SalientEvidenceMemoryV22,
    SignalStrengthV22,
    TraceSalientPayloadV22,
    _normalize_log as _normalize_log_template_v024,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    MetricKindV22,
    RuntimeStateV22,
    semantic_sha256_v22,
)


_LATENCY_BASELINE_NOISE_FLOOR_MS_V024 = 50.0
_LATENCY_ABSOLUTE_DEVIATION_FLOOR_MS_V024 = 50.0
_MEMORY_SLOPE_NOISE_FLOOR_BYTES_PER_SECOND_V024 = 100_000.0
_MEMORY_DELTA_NOISE_FLOOR_BYTES_V024 = 1_000_000
_RESOURCE_MINIMUM_SAMPLE_COUNT_V024 = 5
_V024_REEVALUATED_PREDICATES = {
    PredicateKindV22.METRIC_LATENCY_STRONG,
    PredicateKindV22.RESOURCE_CPU_STRONG,
    PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG,
}
_LOG_PREDICATES = {
    PredicateKindV22.LOG_CONFIGURATION_ERROR,
    PredicateKindV22.LOG_DEPENDENCY_TIMEOUT,
    PredicateKindV22.LOG_MEMORY_PRESSURE,
}


class GenericAnomalyKindV23(str, Enum):
    METRIC_ERROR_OUTLIER = "METRIC_ERROR_OUTLIER"
    METRIC_LATENCY_OUTLIER = "METRIC_LATENCY_OUTLIER"
    RUNTIME_NOT_RUNNING = "RUNTIME_NOT_RUNNING"
    RUNTIME_UNHEALTHY = "RUNTIME_UNHEALTHY"
    RUNTIME_RESTART_ANOMALY = "RUNTIME_RESTART_ANOMALY"
    RESOURCE_CPU_OUTLIER = "RESOURCE_CPU_OUTLIER"
    RESOURCE_MEMORY_TREND = "RESOURCE_MEMORY_TREND"
    TRACE_ERROR_LOCALIZATION = "TRACE_ERROR_LOCALIZATION"
    TRACE_LATENCY_OUTLIER = "TRACE_LATENCY_OUTLIER"
    LOG_ERROR_CLUSTER = "LOG_ERROR_CLUSTER"
    LOG_UNKNOWN_ERROR_PATTERN = "LOG_UNKNOWN_ERROR_PATTERN"
    RECENT_CHANGE_CORRELATION = "RECENT_CHANGE_CORRELATION"
    SOURCE_COVERAGE_GAP = "SOURCE_COVERAGE_GAP"


class ObservedValueV23(DtaModelV22):
    key: str = Field(min_length=1, max_length=80)
    value: str = Field(max_length=240)


class GenericAnomalyV23(DtaModelV22):
    schema_version: Literal["dta-v23.generic-anomaly.v1"]
    anomaly_id: str = Field(pattern=r"^ga:[a-z0-9-]+:[a-z0-9-]+:[0-9a-f]{12}$")
    kind: GenericAnomalyKindV23
    source: EvidenceSourceV22
    service: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    related_services: tuple[str, ...]
    strength: SignalStrengthV22
    summary: str = Field(min_length=1, max_length=500)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    observed_values: tuple[ObservedValueV23, ...]
    anomaly_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_anomaly(self) -> "GenericAnomalyV23":
        if self.related_services != tuple(sorted(set(self.related_services))):
            raise ValueError("generic anomaly related services are not canonical")
        if self.evidence_refs != tuple(sorted(set(self.evidence_refs))):
            raise ValueError("generic anomaly evidence refs are not canonical")
        keys = tuple(item.key for item in self.observed_values)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("generic anomaly observed values are not canonical")
        identity = {
            "kind": self.kind.value,
            "source": self.source.value,
            "service": self.service,
            "related_services": self.related_services,
            "evidence_refs": self.evidence_refs,
        }
        expected_id = (
            f"ga:{self.kind.value.casefold().replace('_', '-')}:"
            f"{self.service}:{semantic_sha256_v22(identity)[:12]}"
        )
        if self.anomaly_id != expected_id:
            raise ValueError("generic anomaly identity differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"anomaly_sha256"})
        )
        if self.anomaly_sha256 != expected:
            raise ValueError("generic anomaly digest differs")
        return self


def _build_anomaly(
    *,
    kind: GenericAnomalyKindV23,
    source: EvidenceSourceV22,
    service: str,
    related_services: tuple[str, ...],
    strength: SignalStrengthV22,
    summary: str,
    evidence_refs: tuple[str, ...],
    observed_values: dict[str, object],
) -> GenericAnomalyV23:
    refs = tuple(sorted(set(evidence_refs)))
    related = tuple(sorted(set(related_services)))
    identity = {
        "kind": kind.value,
        "source": source.value,
        "service": service,
        "related_services": related,
        "evidence_refs": refs,
    }
    values = tuple(
        ObservedValueV23(key=key, value=str(value))
        for key, value in sorted(observed_values.items())
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.generic-anomaly.v1",
        "anomaly_id": (
            f"ga:{kind.value.casefold().replace('_', '-')}:"
            f"{service}:{semantic_sha256_v22(identity)[:12]}"
        ),
        "kind": kind,
        "source": source,
        "service": service,
        "related_services": related,
        "strength": strength,
        "summary": summary,
        "evidence_refs": refs,
        "observed_values": values,
    }
    draft = GenericAnomalyV23.model_construct(
        **payload,
        anomaly_sha256="0" * 64,
    )
    return GenericAnomalyV23.model_validate(
        {
            **payload,
            "anomaly_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"anomaly_sha256"})
            ),
        }
    )


_PREDICATE_KIND_MAP = {
    PredicateKindV22.METRIC_ERROR_RATE_STRONG: GenericAnomalyKindV23.METRIC_ERROR_OUTLIER,
    PredicateKindV22.METRIC_LATENCY_STRONG: GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER,
    PredicateKindV22.RUNTIME_NOT_RUNNING: GenericAnomalyKindV23.RUNTIME_NOT_RUNNING,
    PredicateKindV22.RUNTIME_UNHEALTHY: GenericAnomalyKindV23.RUNTIME_UNHEALTHY,
    PredicateKindV22.RUNTIME_RESTART_PRESSURE: GenericAnomalyKindV23.RUNTIME_RESTART_ANOMALY,
    PredicateKindV22.RESOURCE_CPU_STRONG: GenericAnomalyKindV23.RESOURCE_CPU_OUTLIER,
    PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG: GenericAnomalyKindV23.RESOURCE_MEMORY_TREND,
    PredicateKindV22.TRACE_FIRST_ERROR: GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION,
    PredicateKindV22.TRACE_DEPENDENCY_LATENCY: GenericAnomalyKindV23.TRACE_LATENCY_OUTLIER,
    PredicateKindV22.LOG_CONFIGURATION_ERROR: GenericAnomalyKindV23.LOG_ERROR_CLUSTER,
    PredicateKindV22.LOG_DEPENDENCY_TIMEOUT: GenericAnomalyKindV23.LOG_ERROR_CLUSTER,
    PredicateKindV22.LOG_MEMORY_PRESSURE: GenericAnomalyKindV23.LOG_ERROR_CLUSTER,
    PredicateKindV22.CHANGE_RECENT_ROLLOUT: GenericAnomalyKindV23.RECENT_CHANGE_CORRELATION,
}


def extract_generic_anomalies_v23(
    *,
    memory: SalientEvidenceMemoryV22,
    candidate_services: tuple[str, ...],
    baseline_known_log_templates: tuple[tuple[str, str], ...] = (),
    healthy_noise_guard_v024: bool = False,
) -> tuple[GenericAnomalyV23, ...]:
    """Extract visible symptoms only; evaluator truth is not an input."""

    candidates = set(candidate_services)
    baseline_logs = {
        (service, _normalize_log_template_v024(template))
        for service, template in baseline_known_log_templates
    }
    suppressed_log_refs: set[str] = set()
    anomalies: dict[tuple[object, ...], GenericAnomalyV23] = {}

    def add(anomaly: GenericAnomalyV23) -> None:
        if anomaly.service not in candidates:
            return
        key = (anomaly.kind, anomaly.source, anomaly.service, anomaly.evidence_refs)
        anomalies[key] = anomaly

    for fact in memory.salient_facts:
        if fact.service not in candidates:
            continue
        payload = fact.payload
        if isinstance(payload, MetricSalientPayloadV22):
            kind = None
            strength = fact.signal_strength
            if payload.metric_kind is MetricKindV22.ERROR_RATE and strength is SignalStrengthV22.STRONG:
                kind = GenericAnomalyKindV23.METRIC_ERROR_OUTLIER
            elif payload.metric_kind is MetricKindV22.LATENCY_P95_MS:
                if not healthy_noise_guard_v024 and strength is SignalStrengthV22.STRONG:
                    kind = GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER
                elif (
                    not healthy_noise_guard_v024
                    and payload.baseline_ratio is not None
                    and payload.delta is not None
                    and payload.baseline_ratio >= 1.5
                    and payload.delta >= 5.0
                ):
                    kind = GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER
                    strength = SignalStrengthV22.MODERATE
                elif (
                    healthy_noise_guard_v024
                    and payload.value is not None
                    and payload.baseline_value is not None
                    and payload.delta is not None
                    and payload.value
                    / max(payload.baseline_value, _LATENCY_BASELINE_NOISE_FLOOR_MS_V024)
                    >= 1.5
                    and payload.delta >= _LATENCY_ABSOLUTE_DEVIATION_FLOOR_MS_V024
                ):
                    kind = GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER
                    strength = (
                        SignalStrengthV22.STRONG
                        if payload.value
                        / max(payload.baseline_value, _LATENCY_BASELINE_NOISE_FLOOR_MS_V024)
                        >= 2.0
                        else SignalStrengthV22.MODERATE
                    )
            if kind is not None:
                add(
                    _build_anomaly(
                        kind=kind,
                        source=fact.source,
                        service=fact.service,
                        related_services=(),
                        strength=strength,
                        summary=f"{fact.service} has an abnormal {payload.metric_kind.value} signal",
                        evidence_refs=fact.evidence_refs,
                        observed_values={
                            "baseline_ratio": payload.baseline_ratio,
                            "delta": payload.delta,
                            "value": payload.value,
                        },
                    )
                )
        elif isinstance(payload, RuntimeSalientPayloadV22):
            runtime_kinds = []
            if payload.state is not RuntimeStateV22.RUNNING:
                runtime_kinds.append(GenericAnomalyKindV23.RUNTIME_NOT_RUNNING)
            if not payload.healthy:
                runtime_kinds.append(GenericAnomalyKindV23.RUNTIME_UNHEALTHY)
            if payload.restart_count >= 2:
                runtime_kinds.append(GenericAnomalyKindV23.RUNTIME_RESTART_ANOMALY)
            for kind in runtime_kinds:
                add(
                    _build_anomaly(
                        kind=kind,
                        source=fact.source,
                        service=fact.service,
                        related_services=(),
                        strength=SignalStrengthV22.STRONG,
                        summary=f"{fact.service} has an abnormal runtime state",
                        evidence_refs=fact.evidence_refs,
                        observed_values={
                            "healthy": payload.healthy,
                            "restart_count": payload.restart_count,
                            "state": payload.state.value,
                        },
                    )
                )
        elif isinstance(payload, ResourceSalientPayloadV22):
            if (
                payload.cpu_baseline_ratio is not None
                and (not healthy_noise_guard_v024 or payload.cpu_p50_percent >= 80.0)
                and payload.cpu_p95_percent >= 80.0
                and payload.cpu_baseline_ratio >= 2.0
            ):
                add(
                    _build_anomaly(
                        kind=GenericAnomalyKindV23.RESOURCE_CPU_OUTLIER,
                        source=fact.source,
                        service=fact.service,
                        related_services=(),
                        strength=SignalStrengthV22.STRONG,
                        summary=f"{fact.service} has a strong compute-resource outlier",
                        evidence_refs=fact.evidence_refs,
                        observed_values={
                            "baseline_ratio": payload.cpu_baseline_ratio,
                            "cpu_p95_percent": payload.cpu_p95_percent,
                        },
                    )
                )
            baseline_slope = payload.baseline_memory_slope_bytes_per_second
            if healthy_noise_guard_v024 and (
                baseline_slope is not None
                and payload.memory_slope_bytes_per_second
                >= max(
                    _MEMORY_SLOPE_NOISE_FLOOR_BYTES_PER_SECOND_V024,
                    max(0.0, baseline_slope)
                    + _MEMORY_SLOPE_NOISE_FLOOR_BYTES_PER_SECOND_V024,
                )
                and payload.memory_delta_bytes >= _MEMORY_DELTA_NOISE_FLOOR_BYTES_V024
                and payload.sample_count >= _RESOURCE_MINIMUM_SAMPLE_COUNT_V024
            ):
                strength = SignalStrengthV22.STRONG
            elif (
                not healthy_noise_guard_v024
                and baseline_slope is not None
                and payload.memory_slope_bytes_per_second
                > max(1.0, baseline_slope * 1.5)
            ):
                strength = (
                    SignalStrengthV22.STRONG
                    if payload.memory_slope_bytes_per_second > max(1.0, baseline_slope * 2.0)
                    else SignalStrengthV22.MODERATE
                )
            else:
                strength = None
            if strength is not None:
                add(
                    _build_anomaly(
                        kind=GenericAnomalyKindV23.RESOURCE_MEMORY_TREND,
                        source=fact.source,
                        service=fact.service,
                        related_services=(),
                        strength=strength,
                        summary=f"{fact.service} has an abnormal memory trend",
                        evidence_refs=fact.evidence_refs,
                        observed_values={
                            "baseline_slope": baseline_slope,
                            "memory_slope": payload.memory_slope_bytes_per_second,
                        },
                    )
                )
        elif isinstance(payload, TraceSalientPayloadV22):
            if payload.first_error_location:
                add(
                    _build_anomaly(
                        kind=GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION,
                        source=fact.source,
                        service=fact.service,
                        related_services=tuple(
                            item for item in payload.service_path if item != fact.service
                        ),
                        strength=SignalStrengthV22.STRONG,
                        summary=f"{fact.service} is the first visible trace error",
                        evidence_refs=fact.evidence_refs,
                        observed_values={"operation": payload.operation},
                    )
                )
            if (
                payload.baseline_ratio is not None
                and payload.delta_ms is not None
                and payload.baseline_ratio >= 1.5
                and payload.delta_ms >= 5.0
            ):
                add(
                    _build_anomaly(
                        kind=GenericAnomalyKindV23.TRACE_LATENCY_OUTLIER,
                        source=fact.source,
                        service=fact.service,
                        related_services=tuple(
                            item for item in payload.service_path if item != fact.service
                        ),
                        strength=fact.signal_strength,
                        summary=f"{fact.service} has abnormal trace latency",
                        evidence_refs=fact.evidence_refs,
                        observed_values={
                            "baseline_ratio": payload.baseline_ratio,
                            "delta_ms": payload.delta_ms,
                        },
                    )
                )
        elif isinstance(payload, LogSalientPayloadV22):
            if healthy_noise_guard_v024:
                baseline_known = (
                    fact.service,
                    payload.normalized_template,
                ) in baseline_logs
                if payload.severity == "DIAGNOSTIC" or (
                    baseline_known and payload.severity not in {"ERROR", "FATAL"}
                ):
                    suppressed_log_refs.update(fact.evidence_refs)
                    continue
            if payload.category is not LogCategoryV22.OTHER:
                kind = GenericAnomalyKindV23.LOG_ERROR_CLUSTER
            elif payload.severity in {"ERROR", "FATAL"}:
                kind = GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN
            else:
                continue
            add(
                _build_anomaly(
                    kind=kind,
                    source=fact.source,
                    service=fact.service,
                    related_services=(
                        ()
                        if payload.downstream_service is None
                        else (payload.downstream_service,)
                    ),
                    strength=SignalStrengthV22.STRONG,
                    summary=f"{fact.service} has an error log cluster",
                    evidence_refs=fact.evidence_refs,
                    observed_values={
                        "count": payload.count,
                        "template": payload.normalized_template,
                    },
                )
            )
        elif (
            isinstance(payload, ChangeSalientPayloadV22)
            and fact.signal_strength is SignalStrengthV22.STRONG
        ):
            add(
                _build_anomaly(
                    kind=GenericAnomalyKindV23.RECENT_CHANGE_CORRELATION,
                    source=fact.source,
                    service=fact.service,
                    related_services=(),
                    strength=SignalStrengthV22.MODERATE,
                    summary=f"{fact.service} has a recent visible change",
                    evidence_refs=fact.evidence_refs,
                    observed_values={
                        "category": payload.category.value,
                        "relative_seconds": payload.relative_seconds,
                    },
                )
            )

    # Predicate fallback preserves generic facts that may have fallen outside a
    # bounded Salient top-k view. It does not inspect mechanism or case truth.
    for predicate in memory.predicates:
        if predicate.service not in candidates or predicate.predicate_kind in {
            PredicateKindV22.RUNTIME_HEALTHY,
            PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG,
        }:
            continue
        if healthy_noise_guard_v024 and (
            predicate.predicate_kind in _V024_REEVALUATED_PREDICATES
            or (
                predicate.predicate_kind in _LOG_PREDICATES
                and set(predicate.evidence_refs).issubset(suppressed_log_refs)
            )
        ):
            continue
        kind = _PREDICATE_KIND_MAP.get(predicate.predicate_kind)
        if kind is None:
            continue
        key = (kind, predicate.source, predicate.service, predicate.evidence_refs)
        if key not in anomalies:
            add(
                _build_anomaly(
                    kind=kind,
                    source=predicate.source,
                    service=predicate.service,
                    related_services=(
                        ()
                        if predicate.parent_service is None
                        else (predicate.parent_service,)
                    ),
                    strength=SignalStrengthV22.STRONG,
                    summary=(
                        f"{predicate.service} has a generic anomaly backed by "
                        f"{predicate.source.value}"
                    ),
                    evidence_refs=predicate.evidence_refs,
                    observed_values={"predicate": predicate.predicate_kind.value},
                )
            )
    return tuple(sorted(anomalies.values(), key=lambda item: item.anomaly_id))


__all__ = (
    "GenericAnomalyKindV23",
    "GenericAnomalyV23",
    "ObservedValueV23",
    "extract_generic_anomalies_v23",
)
