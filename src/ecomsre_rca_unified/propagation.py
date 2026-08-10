"""Frozen temporal anomaly and deterministic causal graph primitives."""

from __future__ import annotations

from collections import deque
import math
from statistics import median
from typing import Iterable, Sequence


def first_metric_anomaly(
    *,
    samples: Sequence[tuple[float, float]],
    anchor: float,
    minimum_pre_samples: int,
    minimum_post_samples: int,
    mad_multiplier: float,
    relative_floor: float,
    epsilon: float,
) -> float | None:
    ordered = sorted(samples)
    pre = [value for timestamp, value in ordered if timestamp < anchor]
    post = [(timestamp, value) for timestamp, value in ordered if timestamp >= anchor]
    if len(pre) < minimum_pre_samples or len(post) < minimum_post_samples:
        return None
    baseline = float(median(pre))
    mad = float(median(abs(value - baseline) for value in pre))
    threshold = max(
        mad_multiplier * mad,
        relative_floor * abs(baseline),
        epsilon,
    )
    return next(
        (
            timestamp
            for timestamp, value in post
            if math.isfinite(value) and abs(value - baseline) > threshold
        ),
        None,
    )


def first_marked_log_anomaly(
    *,
    samples: Sequence[tuple[float, str]],
    anchor: float,
    markers: Sequence[str],
) -> float | None:
    normalized = tuple(marker.casefold() for marker in markers)
    return next(
        (
            timestamp
            for timestamp, content in sorted(samples)
            if timestamp >= anchor
            and any(marker in content.casefold() for marker in normalized)
        ),
        None,
    )


def first_trace_anomaly(
    *,
    samples: Sequence[tuple[float, float, bool]],
    anchor: float,
    minimum_pre_samples: int,
    slow_multiplier: float,
) -> float | None:
    ordered = sorted(samples)
    pre = sorted(
        duration
        for timestamp, duration, _failed in ordered
        if timestamp < anchor and math.isfinite(duration)
    )
    if len(pre) < minimum_pre_samples:
        slow_threshold = None
    else:
        rank = max(0, math.ceil(0.95 * len(pre)) - 1)
        slow_threshold = pre[rank] * slow_multiplier
    return next(
        (
            timestamp
            for timestamp, duration, failed in ordered
            if timestamp >= anchor
            and (
                failed
                or (
                    slow_threshold is not None
                    and math.isfinite(duration)
                    and duration > slow_threshold
                )
            )
        ),
        None,
    )


class EvidenceGraph:
    """Directed causal edges plus explicitly undirected component links."""

    def __init__(
        self,
        *,
        nodes: frozenset[str],
        directed_edges: Iterable[tuple[str, str]] = (),
        undirected_edges: Iterable[tuple[str, str]] = (),
    ) -> None:
        self.nodes = nodes
        self.directed = {node: set[str]() for node in nodes}
        self.undirected = {node: set[str]() for node in nodes}
        for upstream, downstream in directed_edges:
            self._require(upstream, downstream)
            self.directed[upstream].add(downstream)
        for left, right in undirected_edges:
            self._require(left, right)
            self.undirected[left].add(right)
            self.undirected[right].add(left)

    def _require(self, *nodes: str) -> None:
        if any(node not in self.nodes for node in nodes):
            raise ValueError("evidence graph edge contains an unknown node")

    @staticmethod
    def _reachable(adjacency: dict[str, set[str]], source: str, target: str) -> bool:
        queue = deque(adjacency[source])
        visited = {source}
        while queue:
            current = queue.popleft()
            if current == target:
                return True
            if current in visited:
                continue
            visited.add(current)
            queue.extend(adjacency[current])
        return False

    def relation(self, candidate: str, symptom: str) -> str:
        if candidate not in self.nodes or symptom not in self.nodes:
            return "UNKNOWN"
        if candidate == symptom:
            return "ROOT"
        if self._reachable(self.directed, candidate, symptom):
            return "UPSTREAM"
        if self._reachable(self.directed, symptom, candidate):
            return "DOWNSTREAM"
        if self._reachable(self.undirected, candidate, symptom):
            return "LATERAL"
        return "UNKNOWN"

    def directed_distance(self, source: str, target: str) -> int | None:
        """Return the shortest causal-edge hop count, or None when unreachable."""

        if source not in self.nodes or target not in self.nodes:
            return None
        if source == target:
            return 0
        queue = deque((child, 1) for child in self.directed[source])
        visited = {source}
        while queue:
            current, distance = queue.popleft()
            if current == target:
                return distance
            if current in visited:
                continue
            visited.add(current)
            queue.extend(
                (child, distance + 1) for child in self.directed[current]
            )
        return None


__all__ = [
    "EvidenceGraph",
    "first_marked_log_anomaly",
    "first_metric_anomaly",
    "first_trace_anomaly",
]
