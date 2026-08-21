"""Runtime-owned Evidence Ambiguity Sets for DTA v2.2.4."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import StrictBool, model_validator

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.contrastive_actions_v224 import (
    ContrastiveResourceActionV224,
)
from ecomsre.dta_v2.v22.gap_graph_v222 import GapGraphV222
from ecomsre.dta_v2.v22.gap_router_v222 import SOURCE_PREDICATE_CAPABILITIES_V222
from ecomsre.dta_v2.v22.memory import SalientEvidenceMemoryV22
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)


_RESOURCE_MECHANISMS = {
    MechanismV22.CPU_SATURATION,
    MechanismV22.MEMORY_LEAK,
}


class EvidenceAmbiguitySetV224(DtaModelV22):
    schema_version: Literal["dta-v22.4.evidence-ambiguity-set.v1"]
    set_id: str
    source: EvidenceSourceV22
    predicate_kinds: tuple[str, ...]
    hypothesis_ids: tuple[str, ...]
    target_services: tuple[str, ...]
    individual_action_ids: tuple[str, ...]
    bundle_action_id: str | None
    covered_target_services: tuple[str, ...]
    remaining_target_services: tuple[str, ...]
    complete: StrictBool
    set_sha256: str

    @model_validator(mode="after")
    def require_set(self) -> "EvidenceAmbiguitySetV224":
        if self.source is not EvidenceSourceV22.RESOURCES:
            raise ValueError("v2.2.4 ambiguity set source differs")
        for values in (
            self.predicate_kinds,
            self.hypothesis_ids,
            self.target_services,
            self.individual_action_ids,
            self.covered_target_services,
            self.remaining_target_services,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("v2.2.4 ambiguity set values are not canonical")
        covered = set(self.covered_target_services)
        remaining = set(self.remaining_target_services)
        if covered.intersection(remaining) or covered | remaining != set(
            self.target_services
        ):
            raise ValueError("v2.2.4 ambiguity set target partition differs")
        if self.complete != (not self.remaining_target_services):
            raise ValueError("v2.2.4 ambiguity set completion differs")
        identity = {
            "source": self.source.value,
            "predicate_kinds": self.predicate_kinds,
            "hypothesis_ids": self.hypothesis_ids,
            "target_services": self.target_services,
            "individual_action_ids": self.individual_action_ids,
            "bundle_action_id": self.bundle_action_id,
        }
        if self.set_id != f"eas:resources:{semantic_sha256_v22(identity)[:16]}":
            raise ValueError("v2.2.4 ambiguity set ID differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"set_sha256"})
        )
        if self.set_sha256 != expected:
            raise ValueError("v2.2.4 ambiguity set digest differs")
        return self


def _resource_gap_requirements(
    *, graph: GapGraphV222, service: str
) -> tuple[dict[str, object], ...]:
    values: list[dict[str, object]] = []
    for hypothesis in graph.hypotheses:
        if hypothesis.target_service != service or hypothesis.mechanism not in _RESOURCE_MECHANISMS:
            continue
        for clause in hypothesis.clauses:
            if clause.missing_count != hypothesis.minimum_missing_count:
                continue
            for gap in clause.missing_requirements:
                values.append(
                    {
                        "mechanism": hypothesis.mechanism.value,
                        "predicate_kind": gap.predicate_kind.value,
                        "service_binding": gap.service_binding.value,
                        "require_exact_parent": gap.require_exact_parent,
                        "target_relation": "SELF",
                        "parent_relation": None if gap.parent_service is None else "ADJACENT",
                    }
                )
    return tuple(sorted(values, key=lambda item: json.dumps(item, sort_keys=True)))


def resource_target_visibility_signature_v224(
    *,
    service: str,
    candidate_services: tuple[str, ...],
    topology_edges: tuple[tuple[str, str], ...],
    memory: SalientEvidenceMemoryV22,
    gap_graph: GapGraphV222,
    negative_coverage: tuple[str, ...] = (),
) -> str:
    """Hash only pre-dispatch runtime-visible state; never truth or future yield."""

    runtime_predicates = tuple(
        sorted(
            item.predicate_kind.value
            for item in memory.predicates
            if item.service == service and item.source is EvidenceSourceV22.RUNTIME
        )
    )
    metric_facts = tuple(
        sorted(
            (
                {
                    "signal_strength": item.signal_strength.value,
                    "payload": item.payload.model_dump(mode="json"),
                }
                for item in memory.salient_facts
                if item.service == service and item.source is EvidenceSourceV22.METRICS
            ),
            key=lambda item: json.dumps(item, sort_keys=True),
        )
    )
    neighbors = {
        right if left == service else left
        for left, right in topology_edges
        if service in {left, right}
    }
    covered_sources = tuple(
        source.value
        for source in EvidenceSourceV22
        if any(
            item.service == service and item.source is source
            for item in memory.salient_facts
        )
    )
    normalized_negative = tuple(
        sorted(
            item.split(":", 1)[0]
            for item in negative_coverage
            if item.endswith(f":{service}")
        )
    )
    payload = {
        "runtime_predicates": runtime_predicates,
        "metric_predicates_and_support": metric_facts,
        "topology_role": {
            "candidate_neighbor_count": len(neighbors.intersection(candidate_services)),
            "external_neighbor_count": len(neighbors.difference(candidate_services)),
        },
        "already_covered_sources": covered_sources,
        "current_gap_requirements": _resource_gap_requirements(
            graph=gap_graph,
            service=service,
        ),
        "negative_coverage": normalized_negative,
    }
    return semantic_sha256_v22(payload)


def _new_set(
    *,
    predicate_kinds: tuple[str, ...],
    hypothesis_ids: tuple[str, ...],
    target_services: tuple[str, ...],
    individual_action_ids: tuple[str, ...],
    bundle_action_id: str | None,
    covered_target_services: tuple[str, ...],
) -> EvidenceAmbiguitySetV224:
    identity = {
        "source": EvidenceSourceV22.RESOURCES.value,
        "predicate_kinds": predicate_kinds,
        "hypothesis_ids": hypothesis_ids,
        "target_services": target_services,
        "individual_action_ids": individual_action_ids,
        "bundle_action_id": bundle_action_id,
    }
    set_id = f"eas:resources:{semantic_sha256_v22(identity)[:16]}"
    remaining = tuple(
        service for service in target_services if service not in set(covered_target_services)
    )
    payload = {
        "schema_version": "dta-v22.4.evidence-ambiguity-set.v1",
        "set_id": set_id,
        "source": EvidenceSourceV22.RESOURCES,
        "predicate_kinds": predicate_kinds,
        "hypothesis_ids": hypothesis_ids,
        "target_services": target_services,
        "individual_action_ids": individual_action_ids,
        "bundle_action_id": bundle_action_id,
        "covered_target_services": covered_target_services,
        "remaining_target_services": remaining,
        "complete": not remaining,
    }
    draft = EvidenceAmbiguitySetV224.model_construct(
        **payload,
        set_sha256="0" * 64,
    )
    return EvidenceAmbiguitySetV224.model_validate(
        {
            **payload,
            "set_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"set_sha256"})
            ),
        }
    )


def build_resource_ambiguity_sets_v224(
    *,
    memory: SalientEvidenceMemoryV22,
    gap_graph: GapGraphV222,
    candidate_services: tuple[str, ...],
    topology_edges: tuple[tuple[str, str], ...],
    individual_actions: tuple[EvidenceActionV22, ...],
    bundle_action: ContrastiveResourceActionV224 | None,
    covered_target_services: tuple[str, ...],
    negative_coverage: tuple[str, ...] = (),
) -> tuple[EvidenceAmbiguitySetV224, ...]:
    if candidate_services != tuple(sorted(set(candidate_services))):
        raise ValueError("ambiguity-set candidates are not canonical")
    if covered_target_services != tuple(sorted(set(covered_target_services))):
        raise ValueError("ambiguity-set covered targets are not canonical")
    if not set(covered_target_services).issubset(candidate_services):
        raise ValueError("ambiguity-set covered target is not a candidate")
    signatures = {
        service: resource_target_visibility_signature_v224(
            service=service,
            candidate_services=candidate_services,
            topology_edges=topology_edges,
            memory=memory,
            gap_graph=gap_graph,
            negative_coverage=negative_coverage,
        )
        for service in candidate_services
    }
    groups: dict[str, list[str]] = {}
    for service, signature in signatures.items():
        groups.setdefault(signature, []).append(service)
    resource_kinds = SOURCE_PREDICATE_CAPABILITIES_V222[
        EvidenceSourceV22.RESOURCES
    ]
    result: list[EvidenceAmbiguitySetV224] = []
    for targets_list in groups.values():
        targets = tuple(sorted(targets_list))
        if len(targets) < 2:
            continue
        hypotheses: list[str] = []
        kinds: set[str] = set()
        for hypothesis in gap_graph.hypotheses:
            if hypothesis.complete or hypothesis.target_service not in set(targets):
                continue
            relevant = {
                gap.predicate_kind
                for clause in hypothesis.clauses
                if clause.missing_count == hypothesis.minimum_missing_count
                for gap in clause.missing_requirements
                if gap.predicate_kind in resource_kinds
            }
            if relevant:
                hypotheses.append(hypothesis.hypothesis_id)
                kinds.update(item.value for item in relevant)
        if len(hypotheses) < 2:
            continue
        individual_ids = tuple(
            sorted(
                item.action_id
                for item in individual_actions
                if item.source is EvidenceSourceV22.RESOURCES
                and len(item.target_services) == 1
                and item.target_services[0] in set(targets)
            )
        )
        if len(individual_ids) != len(targets):
            raise ValueError("ambiguity set lacks one individual action per target")
        bundle_id = (
            bundle_action.action_id
            if bundle_action is not None and bundle_action.target_services == targets
            else None
        )
        covered = tuple(
            service for service in targets if service in set(covered_target_services)
        )
        result.append(
            _new_set(
                predicate_kinds=tuple(sorted(kinds)),
                hypothesis_ids=tuple(sorted(hypotheses)),
                target_services=targets,
                individual_action_ids=individual_ids,
                bundle_action_id=bundle_id,
                covered_target_services=covered,
            )
        )
    return tuple(sorted(result, key=lambda item: item.set_id))


def update_ambiguity_set_coverage_v224(
    *, ambiguity_set: EvidenceAmbiguitySetV224, covered_targets: tuple[str, ...]
) -> EvidenceAmbiguitySetV224:
    merged = tuple(
        sorted({*ambiguity_set.covered_target_services, *covered_targets})
    )
    if not set(merged).issubset(ambiguity_set.target_services):
        raise ValueError("ambiguity-set update covers a nonmember target")
    return _new_set(
        predicate_kinds=ambiguity_set.predicate_kinds,
        hypothesis_ids=ambiguity_set.hypothesis_ids,
        target_services=ambiguity_set.target_services,
        individual_action_ids=ambiguity_set.individual_action_ids,
        bundle_action_id=ambiguity_set.bundle_action_id,
        covered_target_services=merged,
    )


__all__ = (
    "EvidenceAmbiguitySetV224",
    "build_resource_ambiguity_sets_v224",
    "resource_target_visibility_signature_v224",
    "update_ambiguity_set_coverage_v224",
)
