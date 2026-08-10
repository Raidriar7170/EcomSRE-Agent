"""Ground-Truth-independent RCA100 topology canonicalization."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import re
import unicodedata
from typing import Any

from ecomsre_rca100.contracts import CanonicalRCA100Entity


def normalize_entity_name(value: str) -> str:
    """Apply only the frozen Unicode/whitespace/case normalization contract."""

    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value).strip()).casefold()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("RCA100 topology contains a duplicate JSON key")
        output[key] = value
    return output


def _entity_ref(entity_type: str, entity_id: str) -> str:
    domain = entity_type.split(".", 1)[0]
    return f"{domain}|{entity_type}|{entity_id}"


@dataclass(frozen=True, slots=True)
class EntityCatalog:
    by_ref: dict[str, CanonicalRCA100Entity]
    by_id: dict[str, CanonicalRCA100Entity]
    by_type_name: dict[tuple[str, str], tuple[CanonicalRCA100Entity, ...]]
    apm_service_aliases: dict[str, tuple[CanonicalRCA100Entity, ...]]

    def resolve_exact(
        self,
        *,
        entity_id: str | None,
        entity_type: str | None,
        entity_name: str | None,
    ) -> CanonicalRCA100Entity | None:
        if entity_id:
            entity = self.by_id.get(entity_id)
            if entity is not None and (
                entity_type is None or entity.type == entity_type
            ):
                return entity
        if entity_type and entity_name:
            matches = self.by_type_name.get(
                (entity_type, normalize_entity_name(entity_name)), ()
            )
            if len(matches) == 1:
                return matches[0]
        return None

    def resolve_metric_entity(
        self,
        *,
        entity_id: str,
        entity_set: str,
        entity_name: str,
        service: str,
    ) -> CanonicalRCA100Entity | None:
        if entity_id:
            return self.by_id.get(entity_id)
        if entity_set != "apm.service.legacy" and not entity_set.startswith(
            "apm.metric."
        ):
            return None
        value = entity_name or service
        if not value:
            return None
        matches = self.apm_service_aliases.get(normalize_entity_name(value), ())
        return matches[0] if len(matches) == 1 else None

    def resolve_log_entity(
        self,
        *,
        pod_uid: str,
        pod_name: str,
        container_name: str,
    ) -> CanonicalRCA100Entity | None:
        if pod_uid:
            entity = self.by_id.get(pod_uid)
            if entity is not None:
                return entity
        for entity_type, value in (
            ("k8s.pod", pod_name),
            ("k8s.container", container_name),
        ):
            if not value:
                continue
            matches = self.by_type_name.get(
                (entity_type, normalize_entity_name(value)), ()
            )
            if len(matches) == 1:
                return matches[0]
        return None

    def resolve_trace_entity(
        self, *, service_name: str
    ) -> CanonicalRCA100Entity | None:
        if not service_name:
            return None
        matches = self.apm_service_aliases.get(
            normalize_entity_name(service_name), ()
        )
        return matches[0] if len(matches) == 1 else None


def load_entity_catalog(path: Path) -> EntityCatalog:
    if path.is_symlink() or not path.is_file():
        raise ValueError("RCA100 topology must be a regular non-symlink file")
    payload = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("entities"), list):
        raise ValueError("RCA100 topology entity schema is invalid")
    raw_by_id: dict[str, dict[str, Any]] = {}
    for raw in payload["entities"]:
        if not isinstance(raw, dict):
            raise ValueError("RCA100 topology entity must be an object")
        entity_id = raw.get("id")
        entity_type = raw.get("type")
        entity_name = raw.get("name")
        if not all(isinstance(item, str) and item for item in (entity_id, entity_type, entity_name)):
            raise ValueError("RCA100 topology entity identity is invalid")
        assert isinstance(entity_id, str)
        assert isinstance(entity_type, str)
        assert isinstance(entity_name, str)
        if entity_id in raw_by_id:
            raise ValueError("RCA100 topology contains a duplicate entity ID")
        raw_by_id[entity_id] = raw

    refs = {
        entity_id: _entity_ref(str(raw["type"]), entity_id)
        for entity_id, raw in raw_by_id.items()
    }
    same_as: dict[str, set[str]] = defaultdict(set)
    direct_service_parents: dict[str, set[str]] = defaultdict(set)
    host_targets: dict[str, set[str]] = defaultdict(set)
    edges = payload.get("edges", [])
    if not isinstance(edges, list):
        raise ValueError("RCA100 topology edges must be a list")
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("RCA100 topology edge must be an object")
        src = str(edge.get("src", ""))
        dst = str(edge.get("dst", ""))
        relation = edge.get("relation")
        if src not in raw_by_id or dst not in raw_by_id:
            raise ValueError("RCA100 topology edge contains a dangling entity")
        if relation == "same_as":
            same_as[src].add(dst)
            same_as[dst].add(src)
        elif relation == "contains" and raw_by_id[src]["type"] == "apm.service":
            direct_service_parents[dst].add(src)
        elif relation == "hosts":
            host_targets[src].add(dst)

    parent_candidates: dict[str, set[str]] = defaultdict(set)
    for entity_id, parents in direct_service_parents.items():
        parent_candidates[entity_id].update(parents)
    for src, targets in host_targets.items():
        for target in targets:
            if raw_by_id[target]["type"] == "apm.service":
                parent_candidates[src].add(target)
            parent_candidates[src].update(direct_service_parents.get(target, ()))

    by_ref: dict[str, CanonicalRCA100Entity] = {}
    by_id: dict[str, CanonicalRCA100Entity] = {}
    by_type_name_lists: dict[
        tuple[str, str], list[CanonicalRCA100Entity]
    ] = defaultdict(list)
    apm_alias_lists: dict[str, list[CanonicalRCA100Entity]] = defaultdict(list)
    for entity_id, raw in raw_by_id.items():
        entity_type = str(raw["type"])
        entity_name = str(raw["name"])
        domain = entity_type.split(".", 1)[0]
        if domain not in {"apm", "k8s"}:
            raise ValueError("RCA100 topology entity domain is unsupported")
        parents = parent_candidates.get(entity_id, set())
        parent_ref = refs[next(iter(parents))] if len(parents) == 1 else None
        entity = CanonicalRCA100Entity(
            entity_ref=refs[entity_id],
            domain=domain,  # type: ignore[arg-type]
            type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            normalized_name=normalize_entity_name(entity_name),
            parent_service_ref_or_none=parent_ref,
            same_as_refs=tuple(sorted(refs[item] for item in same_as.get(entity_id, ()))),
        )
        by_ref[entity.entity_ref] = entity
        by_id[entity_id] = entity
        by_type_name_lists[(entity.type, entity.normalized_name)].append(entity)
        if entity.type == "apm.service":
            apm_alias_lists[entity.normalized_name].append(entity)
            props = raw.get("props")
            if isinstance(props, dict):
                service = props.get("service")
                if isinstance(service, str) and service.strip():
                    apm_alias_lists[normalize_entity_name(service)].append(entity)

    return EntityCatalog(
        by_ref=by_ref,
        by_id=by_id,
        by_type_name={
            key: tuple(sorted(values, key=lambda item: item.entity_ref))
            for key, values in by_type_name_lists.items()
        },
        apm_service_aliases={
            key: tuple(
                sorted(
                    {item.entity_ref: item for item in values}.values(),
                    key=lambda item: item.entity_ref,
                )
            )
            for key, values in apm_alias_lists.items()
        },
    )


__all__ = ["EntityCatalog", "load_entity_catalog", "normalize_entity_name"]
