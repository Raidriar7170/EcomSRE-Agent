"""Deterministic hierarchical paired bootstrap for development-only endpoints."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
import random


@dataclass(frozen=True, slots=True)
class PairedObservation:
    system: str
    service: str
    fault: str
    baseline: float
    candidate: float


@dataclass(frozen=True, slots=True)
class BootstrapInterval:
    iterations: int
    seed: int
    point_estimate: float
    lower_95: float
    upper_95: float


def _quantile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values or not 0.0 <= probability <= 1.0:
        raise ValueError("bootstrap quantile input is invalid")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = probability * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def hierarchical_paired_bootstrap(
    observations: tuple[PairedObservation, ...],
    *,
    iterations: int = 10_000,
    seed: int = 20_260_807,
) -> BootstrapInterval:
    """Resample system, service, fault, then paired instances with replacement."""

    if iterations <= 0 or type(seed) is not int:
        raise ValueError("bootstrap configuration is invalid")
    if not observations:
        raise ValueError("paired bootstrap requires observations")
    grouped: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    for item in observations:
        if not all((item.system, item.service, item.fault)):
            raise ValueError("bootstrap stratum identity is invalid")
        difference = item.candidate - item.baseline
        if not math.isfinite(difference):
            raise ValueError("bootstrap observation must be finite")
        grouped[item.system][item.service][item.fault].append(difference)
    systems = tuple(sorted(grouped))
    if not systems:
        raise ValueError("bootstrap contains no systems")
    rng = random.Random(seed)
    replicates: list[float] = []
    for _ in range(iterations):
        sampled_values: list[float] = []
        for _system_index in systems:
            system = rng.choice(systems)
            services = tuple(sorted(grouped[system]))
            for _service_index in services:
                service = rng.choice(services)
                faults = tuple(sorted(grouped[system][service]))
                for _fault_index in faults:
                    fault = rng.choice(faults)
                    values = grouped[system][service][fault]
                    for _case_index in values:
                        sampled_values.append(rng.choice(values))
        replicates.append(sum(sampled_values) / len(sampled_values))
    replicates.sort()
    point = sum(item.candidate - item.baseline for item in observations) / len(
        observations
    )
    return BootstrapInterval(
        iterations=iterations,
        seed=seed,
        point_estimate=float(point),
        lower_95=float(_quantile(replicates, 0.025)),
        upper_95=float(_quantile(replicates, 0.975)),
    )
