"""Deterministic benchmark-independent entity hierarchy utilities."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import re

from ecomsre_rca_unified.contracts import CanonicalEntityLayer, EntityHierarchyPath


_TYPE_LAYER_RULES: tuple[tuple[re.Pattern[str], CanonicalEntityLayer], ...] = (
    (re.compile(r"(?:operation|span|endpoint|transaction)"), CanonicalEntityLayer.OPERATION),
    (re.compile(r"(?:service|application)"), CanonicalEntityLayer.SERVICE),
    (re.compile(r"(?:workload|deployment|statefulset|daemonset|replicaset)"), CanonicalEntityLayer.WORKLOAD),
    (re.compile(r"(?:^|\.)pod(?:$|\.)"), CanonicalEntityLayer.POD),
    (re.compile(r"container"), CanonicalEntityLayer.CONTAINER),
    (re.compile(r"(?:^|\.)(?:node|host)(?:$|\.)"), CanonicalEntityLayer.NODE),
    (re.compile(r"(?:database|mysql|postgres|mongodb)"), CanonicalEntityLayer.DATABASE),
    (re.compile(r"(?:cache|redis)"), CanonicalEntityLayer.CACHE),
    (re.compile(r"(?:queue|kafka|rabbitmq|broker)"), CanonicalEntityLayer.MESSAGE_QUEUE),
    (re.compile(r"(?:network|loadbalancer|gateway|ingress)"), CanonicalEntityLayer.NETWORK_COMPONENT),
    (re.compile(r"cluster"), CanonicalEntityLayer.CLUSTER),
    (re.compile(r"(?:infrastructure|region|zone|disk|volume)"), CanonicalEntityLayer.INFRASTRUCTURE),
)


def normalize_entity_layer(entity_type: str) -> CanonicalEntityLayer:
    value = entity_type.strip().casefold()
    for pattern, layer in _TYPE_LAYER_RULES:
        if pattern.search(value):
            return layer
    return CanonicalEntityLayer.UNKNOWN


@dataclass(frozen=True, slots=True)
class EntityNode:
    entity_ref: str
    layer: CanonicalEntityLayer
    normalized_name: str


class EntityHierarchy:
    def __init__(
        self,
        *,
        nodes: tuple[EntityNode, ...],
        parent_edges: tuple[tuple[str, str], ...] = (),
        same_as_edges: tuple[tuple[str, str], ...] = (),
        directed_edges: tuple[tuple[str, str], ...] = (),
        undirected_edges: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.nodes = {item.entity_ref: item for item in nodes}
        if len(self.nodes) != len(nodes):
            raise ValueError("hierarchy contains duplicate entities")
        self.parents: dict[str, set[str]] = {key: set() for key in self.nodes}
        self.same_as: dict[str, set[str]] = {key: set() for key in self.nodes}
        self.directed: dict[str, set[str]] = {key: set() for key in self.nodes}
        self.undirected: dict[str, set[str]] = {key: set() for key in self.nodes}
        for child, parent in parent_edges:
            self._require_known(child, parent)
            self.parents[child].add(parent)
        for left, right in same_as_edges:
            self._require_known(left, right)
            self.same_as[left].add(right)
            self.same_as[right].add(left)
        for upstream, downstream in directed_edges:
            self._require_known(upstream, downstream)
            self.directed[upstream].add(downstream)
        for left, right in undirected_edges:
            self._require_known(left, right)
            self.undirected[left].add(right)
            self.undirected[right].add(left)
        for entity in self.nodes:
            self.parent_chain(entity)

    def _require_known(self, *entities: str) -> None:
        if any(entity not in self.nodes for entity in entities):
            raise ValueError("hierarchy edge contains an unknown entity")

    def parent_chain(self, entity: str) -> tuple[str, ...]:
        self._require_known(entity)
        chain = [entity]
        current = entity
        visited = {entity}
        while self.parents[current]:
            parents = sorted(self.parents[current])
            if len(parents) != 1:
                break
            current = parents[0]
            if current in visited:
                raise ValueError("hierarchy parent cycle detected")
            chain.append(current)
            visited.add(current)
        return tuple(chain)

    def path(self, entity: str) -> EntityHierarchyPath:
        chain = self.parent_chain(entity)
        service = next(
            (item for item in chain if self.nodes[item].layer is CanonicalEntityLayer.SERVICE),
            None,
        )
        infrastructure = next(
            (
                item
                for item in chain
                if self.nodes[item].layer
                in {
                    CanonicalEntityLayer.NODE,
                    CanonicalEntityLayer.CLUSTER,
                    CanonicalEntityLayer.INFRASTRUCTURE,
                }
            ),
            None,
        )
        return EntityHierarchyPath(
            entity=entity,
            explicit_parents=chain[1:],
            service_ancestor_or_none=service,
            infrastructure_ancestor_or_none=infrastructure,
        )

    def service_ancestor(self, entity: str) -> str | None:
        return self.path(entity).service_ancestor_or_none

    def _ancestors(self, entity: str) -> set[str]:
        output: set[str] = set()
        queue = deque(self.parents[entity])
        while queue:
            current = queue.popleft()
            if current in output:
                continue
            output.add(current)
            queue.extend(self.parents[current])
        return output

    def _directed_reachable(self, source: str, target: str) -> bool:
        queue = deque(self.directed[source])
        visited: set[str] = set()
        while queue:
            current = queue.popleft()
            if current == target:
                return True
            if current in visited:
                continue
            visited.add(current)
            queue.extend(self.directed[current])
        return False

    def _same_as_reachable(self, source: str, target: str) -> bool:
        queue = deque(self.same_as[source])
        visited = {source}
        while queue:
            current = queue.popleft()
            if current == target:
                return True
            if current in visited:
                continue
            visited.add(current)
            queue.extend(self.same_as[current])
        return False

    def node_ancestor(self, entity: str) -> str | None:
        return next(
            (
                item
                for item in self.parent_chain(entity)
                if self.nodes[item].layer is CanonicalEntityLayer.NODE
            ),
            None,
        )

    def same_node(self, left: str, right: str) -> bool:
        if left not in self.nodes or right not in self.nodes:
            return False
        left_node = self.node_ancestor(left)
        return left_node is not None and left_node == self.node_ancestor(right)

    def _component_connected(self, source: str, target: str) -> bool:
        neighbors = {
            entity: self.undirected[entity]
            | self.same_as[entity]
            | self.parents[entity]
            | {child for child, values in self.parents.items() if entity in values}
            | self.directed[entity]
            | {left for left, values in self.directed.items() if entity in values}
            for entity in self.nodes
        }
        queue = deque(neighbors[source])
        visited = {source}
        while queue:
            current = queue.popleft()
            if current == target:
                return True
            if current in visited:
                continue
            visited.add(current)
            queue.extend(neighbors[current])
        return False

    def relation(self, predicted: str, truth: str) -> str:
        if predicted not in self.nodes or truth not in self.nodes:
            return "UNRESOLVED"
        if predicted == truth or self._same_as_reachable(predicted, truth):
            return "EXACT_MATCH"
        if predicted in self._ancestors(truth):
            return "PREDICTED_ANCESTOR"
        if truth in self._ancestors(predicted):
            return "PREDICTED_DESCENDANT"
        predicted_parents = self.parents[predicted]
        truth_parents = self.parents[truth]
        if predicted_parents.intersection(truth_parents):
            return "SIBLING_SAME_PARENT"
        predicted_service = self.service_ancestor(predicted)
        truth_service = self.service_ancestor(truth)
        if predicted_service is not None and predicted_service == truth_service:
            return "SAME_SERVICE_DIFFERENT_INSTANCE"
        if self.same_node(predicted, truth):
            return "SAME_NODE"
        if self._directed_reachable(predicted, truth):
            return "CONNECTED_UPSTREAM"
        if self._directed_reachable(truth, predicted):
            return "CONNECTED_DOWNSTREAM"
        if self._component_connected(predicted, truth):
            return "SAME_COMPONENT_UNDIRECTED"
        return "UNRELATED"


__all__ = ["EntityHierarchy", "EntityNode", "normalize_entity_layer"]
