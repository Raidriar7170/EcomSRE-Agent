"""Frozen practical case specifications and honest synthetic derivations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.practical_replay import (
    NormalizedPracticalCaseV22,
    normalize_practical_case_bytes_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    SpanStatusV22,
    TraceSpanV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReplayCaptureV22


class PracticalCaptureKindV22(str, Enum):
    REAL_PUBLIC_REPLAY = "REAL_PUBLIC_REPLAY"
    SYNTHETIC_COUNTERFACTUAL_DERIVED = "SYNTHETIC_COUNTERFACTUAL_DERIVED"
    SYNTHETIC_V22_FIXTURE_DERIVED = "SYNTHETIC_V22_FIXTURE_DERIVED"


class PracticalCaseModifierV22(str, Enum):
    NONE = "NONE"
    SHIPPING_DEPENDENCY_DERIVED = "SHIPPING_DEPENDENCY_DERIVED"
    SHIPPING_HEALTHY_COUNTERFACTUAL = "SHIPPING_HEALTHY_COUNTERFACTUAL"
    PAYMENT_DEPENDENCY_FIXTURE = "PAYMENT_DEPENDENCY_FIXTURE"


class PracticalCaseSpecV22(DtaModelV22):
    case_id: str = Field(pattern=r"^[de][0-9]{2}$")
    source_path: str | None
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    capture_kind: PracticalCaptureKindV22
    modifier: PracticalCaseModifierV22
    derivation: str | None
    counterfactual_pair_ids: tuple[str, ...]
    bootstrap_insufficient_expected: bool

    @model_validator(mode="after")
    def require_source(self) -> "PracticalCaseSpecV22":
        inline = self.modifier is PracticalCaseModifierV22.PAYMENT_DEPENDENCY_FIXTURE
        if inline != (self.source_path is None and self.source_sha256 is None):
            raise ValueError("practical case source binding differs from modifier")
        if self.capture_kind is PracticalCaptureKindV22.REAL_PUBLIC_REPLAY:
            if self.modifier is not PracticalCaseModifierV22.NONE or self.derivation:
                raise ValueError("real replay cannot claim a synthetic modifier")
        elif not self.derivation:
            raise ValueError("synthetic practical case lacks derivation")
        if self.source_path is not None:
            source = Path(self.source_path)
            if source.is_absolute() or ".." in source.parts:
                raise ValueError("practical case source path escapes the repository")
        return self


class PracticalCaseSetV22(DtaModelV22):
    schema_version: Literal["dta-v22.practical-case-set.v1"]
    cases: tuple[PracticalCaseSpecV22, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_set(self) -> "PracticalCaseSetV22":
        ids = tuple(item.case_id for item in self.cases)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("practical case set is not canonical and unique")
        return self


def practical_case_bytes_sha256_v22(spec: PracticalCaseSpecV22) -> str:
    return semantic_sha256_v22(spec.model_dump(mode="json"))


def load_practical_case_set_v22(path: Path) -> PracticalCaseSetV22:
    return PracticalCaseSetV22.model_validate_json(path.read_bytes())


def _rebuild_case(
    *,
    source: NormalizedPracticalCaseV22,
    spec: PracticalCaseSpecV22,
    capture: ReplayCaptureV22,
) -> NormalizedPracticalCaseV22:
    notes = list(source.normalization_notes)
    if spec.derivation is not None:
        notes.append(spec.derivation)
    return NormalizedPracticalCaseV22(
        schema_version="dta-v22.practical-normalized-case.v1",
        case_id=spec.case_id,
        source_bytes_sha256=practical_case_bytes_sha256_v22(spec),
        candidate_services=source.candidate_services,
        topology_edges=source.topology_edges,
        capture=capture,
        normalization_notes=tuple(notes),
    )


def _shipping_dependency(
    source: NormalizedPracticalCaseV22,
    spec: PracticalCaseSpecV22,
) -> NormalizedPracticalCaseV22:
    metrics = tuple(
        MetricFactV22(
            **{
                **item.model_dump(mode="python"),
                "sample_count": 3,
                "support_status": MetricSupportStatusV22.SUPPORTED,
                "value": 50.0,
            }
        )
        if item.service == "checkout"
        and item.metric_kind is MetricKindV22.LATENCY_P95_MS
        else item
        for item in source.capture.metrics
    )
    traces = tuple(
        TraceSpanV22(
            **{
                **item.model_dump(mode="python"),
                "duration_ms": 4200.0,
            }
        )
        if item.service == "shipping" and item.parent_service == "checkout"
        else item
        for item in source.capture.traces
    )
    capture = ReplayCaptureV22(
        **{
            **source.capture.model_dump(mode="python"),
            "metrics": metrics,
            "traces": traces,
        }
    )
    return _rebuild_case(source=source, spec=spec, capture=capture)


def _shipping_healthy(
    source: NormalizedPracticalCaseV22,
    spec: PracticalCaseSpecV22,
) -> NormalizedPracticalCaseV22:
    healthy_values = {
        MetricKindV22.ERROR_RATE: 0.01,
        MetricKindV22.LATENCY_P95_MS: 10.0,
        MetricKindV22.REQUEST_SUPPORT: 100.0,
    }
    metrics = tuple(
        MetricFactV22(
            **{
                **item.model_dump(mode="python"),
                "sample_count": 3,
                "support_status": MetricSupportStatusV22.SUPPORTED,
                "value": healthy_values[item.metric_kind],
            }
        )
        if item.metric_kind in healthy_values
        else item
        for item in source.capture.metrics
    )
    traces = tuple(
        TraceSpanV22(
            **{
                **item.model_dump(mode="python"),
                "duration_ms": min(item.duration_ms, 10.0),
                "status": SpanStatusV22.OK,
                "first_error_location": False,
            }
        )
        for item in source.capture.traces
    )
    resources = tuple(
        ResourceUsageRecordV22(
            schema_version="dta-v22.resource-usage-record.v1",
            service=item.service,
            sampling_window_seconds=10,
            samples=tuple(
                ResourceSampleV22(
                    offset_ms=offset,
                    cpu_percent=10.0,
                    memory_bytes=item.samples[0].memory_bytes,
                )
                for offset in (0, 2500, 5000, 7500, 10_000)
            ),
            memory_slope_bytes_per_second=0.0,
        )
        for item in source.capture.resources
    )
    runtime = tuple(
        RuntimeRecordV22(
            schema_version="dta-v22.runtime-record.v1",
            service=service,
            state=RuntimeStateV22.RUNNING,
            healthy=True,
            restart_count=0,
        )
        for service in source.candidate_services
    )
    capture = ReplayCaptureV22(
        **{
            **source.capture.model_dump(mode="python"),
            "metrics": metrics,
            "traces": traces,
            "resources": resources,
            "runtime": runtime,
            "source_failures": (),
        }
    )
    return _rebuild_case(source=source, spec=spec, capture=capture)


def _payment_dependency(spec: PracticalCaseSpecV22) -> NormalizedPracticalCaseV22:
    captured_at = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
    metrics = tuple(
        MetricFactV22(
            schema_version="dta-v22.metric-fact.v1",
            service=service,
            metric_kind=kind,
            support_status=MetricSupportStatusV22.SUPPORTED,
            sample_count=3,
            value=(
                60.0
                if service == "checkout" and kind is MetricKindV22.LATENCY_P95_MS
                else 0.01
                if kind is MetricKindV22.ERROR_RATE
                else 10.0
                if kind is MetricKindV22.LATENCY_P95_MS
                else 100.0
            ),
            unit=METRIC_UNIT_BY_KIND_V22[kind],
            window_started_at=captured_at - timedelta(seconds=300),
            window_ended_at=captured_at,
        )
        for service in ("checkout", "payment")
        for kind in (
            MetricKindV22.ERROR_RATE,
            MetricKindV22.LATENCY_P95_MS,
            MetricKindV22.REQUEST_SUPPORT,
        )
    )
    runtime = tuple(
        RuntimeRecordV22(
            schema_version="dta-v22.runtime-record.v1",
            service=service,
            state=RuntimeStateV22.RUNNING,
            healthy=True,
            restart_count=0,
        )
        for service in ("checkout", "payment")
    )
    capture = ReplayCaptureV22(
        schema_version="dta-v22.replay-capture.v1",
        captured_at=captured_at,
        metrics=metrics,
        logs=(),
        traces=(
            TraceSpanV22(
                schema_version="dta-v22.trace-span.v1",
                observed_at=captured_at,
                service_path=("checkout", "payment"),
                service="payment",
                parent_service="checkout",
                operation="Charge",
                status=SpanStatusV22.UNSET,
                duration_ms=300.0,
                first_error_location=False,
            ),
        ),
        runtime=runtime,
        resources=(),
        changes=(),
        source_failures=(),
    )
    source = NormalizedPracticalCaseV22(
        schema_version="dta-v22.practical-normalized-case.v1",
        case_id=spec.case_id,
        source_bytes_sha256=practical_case_bytes_sha256_v22(spec),
        candidate_services=("checkout", "payment"),
        topology_edges=(("checkout", "payment"),),
        capture=capture,
        normalization_notes=(),
    )
    return _rebuild_case(source=source, spec=spec, capture=capture)


def materialize_practical_case_v22(
    *,
    spec: PracticalCaseSpecV22,
    repository_root: Path,
) -> NormalizedPracticalCaseV22:
    if spec.modifier is PracticalCaseModifierV22.PAYMENT_DEPENDENCY_FIXTURE:
        return _payment_dependency(spec)
    assert spec.source_path is not None and spec.source_sha256 is not None
    path = repository_root / spec.source_path
    source_bytes = path.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != spec.source_sha256:
        raise ValueError("practical case source bytes differ from frozen binding")
    source = normalize_practical_case_bytes_v22(source_bytes)
    if spec.modifier is PracticalCaseModifierV22.NONE:
        return _rebuild_case(source=source, spec=spec, capture=source.capture)
    if spec.modifier is PracticalCaseModifierV22.SHIPPING_DEPENDENCY_DERIVED:
        return _shipping_dependency(source, spec)
    if spec.modifier is PracticalCaseModifierV22.SHIPPING_HEALTHY_COUNTERFACTUAL:
        return _shipping_healthy(source, spec)
    raise AssertionError("unknown practical case modifier")


def case_set_file_sha256_v22(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = (
    "PracticalCaptureKindV22",
    "PracticalCaseModifierV22",
    "PracticalCaseSetV22",
    "PracticalCaseSpecV22",
    "case_set_file_sha256_v22",
    "load_practical_case_set_v22",
    "materialize_practical_case_v22",
    "practical_case_bytes_sha256_v22",
)
