"""Explicit RCA100 capacity contract for live fault-time projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ecomsre_rca100.projection import (
    RCA100MetricsProjection,
    RCA100SourceProjection,
)


@dataclass(frozen=True, slots=True)
class RCA100LiveProjectionCapacity:
    metrics_evidence: int = 6
    metrics_ranking: int = 6
    source_evidence: int = 6


@dataclass(frozen=True, slots=True)
class EffectiveProjectionLimits:
    metrics: int
    logs: int
    traces: int


RCA100_LIVE_PROJECTION_CAPACITY = RCA100LiveProjectionCapacity()


def _schema_max_items(model: type[Any], field: str) -> int:
    schema = model.model_json_schema()
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise RuntimeError("RCA100 projection schema properties are absent")
    field_schema = properties.get(field)
    if not isinstance(field_schema, dict):
        raise RuntimeError(f"RCA100 projection schema field is absent: {field}")
    maximum = field_schema.get("maxItems")
    if not isinstance(maximum, int) or maximum < 1:
        raise RuntimeError(
            f"RCA100 projection schema maxItems is invalid: {field}"
        )
    return maximum


def assert_live_projection_capacity_conforms(
    capacity: RCA100LiveProjectionCapacity = RCA100_LIVE_PROJECTION_CAPACITY,
) -> None:
    actual = RCA100LiveProjectionCapacity(
        metrics_evidence=_schema_max_items(RCA100MetricsProjection, "evidence"),
        metrics_ranking=_schema_max_items(RCA100MetricsProjection, "ranking"),
        source_evidence=_schema_max_items(RCA100SourceProjection, "evidence"),
    )
    if actual != capacity:
        raise RuntimeError(
            "live projection capacity differs from the RCA100 typed schema"
        )


def effective_projection_limits(
    projection: Any,
    *,
    capacity: RCA100LiveProjectionCapacity = RCA100_LIVE_PROJECTION_CAPACITY,
) -> EffectiveProjectionLimits:
    assert_live_projection_capacity_conforms(capacity)
    metrics_capacity = min(capacity.metrics_evidence, capacity.metrics_ranking)
    return EffectiveProjectionLimits(
        metrics=min(int(projection.metric_candidate_limit), metrics_capacity),
        logs=min(int(projection.log_evidence_limit), capacity.source_evidence),
        traces=min(int(projection.trace_evidence_limit), capacity.source_evidence),
    )


__all__ = [
    "EffectiveProjectionLimits",
    "RCA100LiveProjectionCapacity",
    "RCA100_LIVE_PROJECTION_CAPACITY",
    "assert_live_projection_capacity_conforms",
    "effective_projection_limits",
]
