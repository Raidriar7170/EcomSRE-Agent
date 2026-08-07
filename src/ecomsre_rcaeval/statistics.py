"""Preregistered hierarchical paired bootstrap for RCAEval RE2."""

from __future__ import annotations

from collections import defaultdict
from enum import Enum
import hashlib
import math
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt

from ecomsre_rcaeval.contracts import Architecture, RCAEvalModel


class BootstrapMetric(str, Enum):
    ROOT_SERVICE_AC1 = "root_service_ac1"
    RELATIVE_TOOL_REDUCTION = "relative_tool_reduction"


class ScoredObservation(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.bootstrap-observation.v1"] = (
        "rcaeval-re2.bootstrap-observation.v1"
    )
    stratum: str = Field(min_length=1, max_length=256)
    instance: str = Field(min_length=1, max_length=64)
    architecture: Architecture
    root_service_correct: StrictBool
    tool_calls: StrictInt = Field(ge=0)


class BootstrapResult(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.bootstrap-result.v1"] = (
        "rcaeval-re2.bootstrap-result.v1"
    )
    metric: BootstrapMetric
    left: Architecture
    right: Architecture
    stratum_count: StrictInt = Field(gt=0)
    pairing_unit_count: StrictInt = Field(gt=0)
    replicates: StrictInt = Field(gt=0)
    seed: StrictInt
    point_estimate: StrictFloat
    ci_lower: StrictFloat
    ci_upper: StrictFloat


Pair = tuple[ScoredObservation, ScoredObservation]


def _paired(
    observations: tuple[ScoredObservation, ...],
    *,
    left: Architecture,
    right: Architecture,
    require_locked_distribution: bool,
) -> dict[str, tuple[Pair, ...]]:
    grouped: dict[tuple[str, str], dict[Architecture, ScoredObservation]] = (
        defaultdict(dict)
    )
    for item in observations:
        unit = grouped[(item.stratum, item.instance)]
        if item.architecture in unit:
            raise ValueError("duplicate architecture arm in pairing unit")
        unit[item.architecture] = item
    by_stratum: dict[str, list[tuple[str, Pair]]] = defaultdict(list)
    for (stratum, instance), arms in grouped.items():
        if left not in arms or right not in arms:
            raise ValueError("paired bootstrap requires both comparison arms")
        by_stratum[stratum].append((instance, (arms[left], arms[right])))
    if not by_stratum:
        raise ValueError("paired bootstrap denominator is zero")
    instance_counts = {len(items) for items in by_stratum.values()}
    if len(instance_counts) != 1:
        raise ValueError("all RCAEval strata require the same instance count")
    if require_locked_distribution and (
        len(by_stratum) != 30 or instance_counts != {3}
    ):
        raise ValueError("RCAEval holdout requires exactly 30 strata by three instances")
    return {
        stratum: tuple(pair for _, pair in sorted(items))
        for stratum, items in sorted(by_stratum.items())
    }


def _draw(seed: int, replicate: int, scope: str, draw: int, upper: int) -> int:
    material = b"\0".join(
        str(item).encode("utf-8") for item in (seed, replicate, scope, draw)
    )
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % upper


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def _aggregate(pairs: list[Pair], metric: BootstrapMetric) -> float:
    if metric is BootstrapMetric.ROOT_SERVICE_AC1:
        differences = [
            float(left.root_service_correct) - float(right.root_service_correct)
            for left, right in pairs
        ]
        return sum(differences) / len(differences)
    left_mean = sum(left.tool_calls for left, _ in pairs) / len(pairs)
    right_mean = sum(right.tool_calls for _, right in pairs) / len(pairs)
    if right_mean <= 0:
        raise ValueError("tool reduction denominator is zero")
    return (right_mean - left_mean) / right_mean


def hierarchical_paired_bootstrap(
    observations: tuple[ScoredObservation, ...],
    *,
    left: Architecture,
    right: Architecture,
    metric: BootstrapMetric,
    replicates: int = 10_000,
    seed: int = 20_260_806,
    require_locked_distribution: bool = True,
) -> BootstrapResult:
    if left is right:
        raise ValueError("bootstrap comparison arms must differ")
    if replicates <= 0:
        raise ValueError("bootstrap replicate count must be positive")
    pairs_by_stratum = _paired(
        observations,
        left=left,
        right=right,
        require_locked_distribution=require_locked_distribution,
    )
    strata = tuple(pairs_by_stratum)
    observed = [pair for pairs in pairs_by_stratum.values() for pair in pairs]
    replicate_values: list[float] = []
    for replicate in range(replicates):
        sampled: list[Pair] = []
        for stratum_draw in range(len(strata)):
            stratum = strata[
                _draw(seed, replicate, "stratum", stratum_draw, len(strata))
            ]
            instances = pairs_by_stratum[stratum]
            for instance_draw in range(len(instances)):
                sampled.append(
                    instances[
                        _draw(
                            seed,
                            replicate,
                            f"instance:{stratum_draw}:{stratum}",
                            instance_draw,
                            len(instances),
                        )
                    ]
                )
        replicate_values.append(_aggregate(sampled, metric))
    return BootstrapResult(
        metric=metric,
        left=left,
        right=right,
        stratum_count=len(strata),
        pairing_unit_count=len(observed),
        replicates=replicates,
        seed=seed,
        point_estimate=_aggregate(observed, metric),
        ci_lower=_percentile(replicate_values, 0.025),
        ci_upper=_percentile(replicate_values, 0.975),
    )
