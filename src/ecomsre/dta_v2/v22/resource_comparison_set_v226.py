"""Target-complete Resources comparison sets for DTA v2.2.6."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictBool, model_validator

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.ambiguity_set_v225 import (
    build_resource_ambiguity_sets_v225,
)
from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    ContrastiveResourceActionV225,
)
from ecomsre.dta_v2.v22.gap_graph_v222 import GapGraphV222
from ecomsre.dta_v2.v22.gap_router_v222 import (
    SOURCE_PREDICATE_CAPABILITIES_V222,
)
from ecomsre.dta_v2.v22.memory import SalientEvidenceMemoryV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)


class ResourceComparisonSetV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.resource-comparison-set.v1"]
    set_id: str = Field(pattern=r"^rcs:resources:[0-9a-f]{16}$")
    source: Literal[EvidenceSourceV22.RESOURCES]
    candidate_services: tuple[str, ...] = Field(min_length=2, max_length=4)
    hypothesis_ids: tuple[str, ...] = Field(min_length=2)
    missing_predicate_kinds: tuple[str, ...] = Field(min_length=1)
    individual_action_ids: tuple[str, ...] = Field(min_length=2, max_length=4)
    bundle_action_id: str
    strict_ambiguity_set_id: str | None
    strictly_ambiguous: StrictBool
    target_complete: StrictBool
    covered_targets: tuple[str, ...]
    remaining_targets: tuple[str, ...]
    set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_comparison_set(self) -> ResourceComparisonSetV226:
        for values in (
            self.candidate_services,
            self.hypothesis_ids,
            self.missing_predicate_kinds,
            self.individual_action_ids,
            self.covered_targets,
            self.remaining_targets,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("resource comparison values are not canonical")
        if len(self.individual_action_ids) != len(self.candidate_services):
            raise ValueError("resource comparison lacks one action per target")
        covered = set(self.covered_targets)
        remaining = set(self.remaining_targets)
        if covered.intersection(remaining) or covered | remaining != set(
            self.candidate_services
        ):
            raise ValueError("resource comparison target partition differs")
        if not self.target_complete:
            raise ValueError("resource comparison source is not target-complete")
        if self.strictly_ambiguous != (self.strict_ambiguity_set_id is not None):
            raise ValueError("strict ambiguity diagnostic differs")
        identity = {
            "source": self.source.value,
            "candidate_services": self.candidate_services,
            "hypothesis_ids": self.hypothesis_ids,
            "missing_predicate_kinds": self.missing_predicate_kinds,
            "individual_action_ids": self.individual_action_ids,
            "bundle_action_id": self.bundle_action_id,
        }
        if self.set_id != f"rcs:resources:{semantic_sha256_v22(identity)[:16]}":
            raise ValueError("resource comparison set ID differs")
        if self.set_sha256 != self.recompute_sha256():
            raise ValueError("resource comparison set digest differs")
        return self

    def recompute_sha256(self) -> str:
        return semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"set_sha256"})
        )


def build_resource_comparison_set_v226(
    *,
    memory: SalientEvidenceMemoryV22,
    gap_graph: GapGraphV222,
    candidate_services: tuple[str, ...],
    topology_edges: tuple[tuple[str, str], ...],
    individual_actions: tuple[EvidenceActionV22, ...],
    bundle_action: ContrastiveResourceActionV225 | None,
    target_complete: bool,
    covered_targets: tuple[str, ...],
) -> ResourceComparisonSetV226 | None:
    """Build from unresolved Resources-observable gaps, never evaluator truth."""

    if type(target_complete) is not bool:
        raise TypeError("resource comparison target_complete must be bool")
    if candidate_services != tuple(sorted(set(candidate_services))):
        raise ValueError("resource comparison candidates are not canonical")
    if covered_targets != tuple(sorted(set(covered_targets))):
        raise ValueError("resource comparison covered targets are not canonical")
    if not set(covered_targets).issubset(candidate_services):
        raise ValueError("resource comparison covered target is not a candidate")
    if not target_complete or bundle_action is None:
        return None

    resource_kinds = SOURCE_PREDICATE_CAPABILITIES_V222[
        EvidenceSourceV22.RESOURCES
    ]
    hypotheses_by_target: dict[str, set[str]] = {}
    kinds_by_target: dict[str, set[str]] = {}
    for hypothesis in gap_graph.hypotheses:
        if hypothesis.complete or hypothesis.target_service not in set(candidate_services):
            continue
        for clause in hypothesis.clauses:
            if clause.missing_count != hypothesis.minimum_missing_count:
                continue
            relevant = {
                gap.predicate_kind
                for gap in clause.missing_requirements
                if gap.predicate_kind in resource_kinds
            }
            if relevant:
                hypotheses_by_target.setdefault(hypothesis.target_service, set()).add(
                    hypothesis.hypothesis_id
                )
                kinds_by_target.setdefault(hypothesis.target_service, set()).update(
                    item.value for item in relevant
                )

    action_by_target = {
        action.target_services[0]: action
        for action in individual_actions
        if action.source is EvidenceSourceV22.RESOURCES
        and len(action.target_services) == 1
        and action.target_services[0] in set(candidate_services)
    }
    targets = tuple(
        service
        for service in candidate_services
        if service in hypotheses_by_target and service in action_by_target
    )
    if (
        len(targets) < 2
        or bundle_action.target_services != targets
        or bundle_action.source is not EvidenceSourceV22.RESOURCES
    ):
        return None

    hypothesis_ids = tuple(
        sorted(
            {
                hypothesis_id
                for target in targets
                for hypothesis_id in hypotheses_by_target[target]
            }
        )
    )
    predicate_kinds = tuple(
        sorted(
            {
                predicate_kind
                for target in targets
                for predicate_kind in kinds_by_target[target]
            }
        )
    )
    individual_ids = tuple(sorted(action_by_target[target].action_id for target in targets))
    covered = tuple(target for target in targets if target in set(covered_targets))
    remaining = tuple(target for target in targets if target not in set(covered_targets))

    strict_sets = build_resource_ambiguity_sets_v225(
        memory=memory,
        gap_graph=gap_graph,
        candidate_services=candidate_services,
        topology_edges=topology_edges,
        individual_actions=individual_actions,
        bundle_action=bundle_action,
        covered_target_services=covered_targets,
    )
    strict_match = next(
        (item for item in strict_sets if item.target_services == targets), None
    )
    identity = {
        "source": EvidenceSourceV22.RESOURCES.value,
        "candidate_services": targets,
        "hypothesis_ids": hypothesis_ids,
        "missing_predicate_kinds": predicate_kinds,
        "individual_action_ids": individual_ids,
        "bundle_action_id": bundle_action.action_id,
    }
    payload = {
        "schema_version": "dta-v226-real-fault.resource-comparison-set.v1",
        "set_id": f"rcs:resources:{semantic_sha256_v22(identity)[:16]}",
        "source": EvidenceSourceV22.RESOURCES,
        "candidate_services": targets,
        "hypothesis_ids": hypothesis_ids,
        "missing_predicate_kinds": predicate_kinds,
        "individual_action_ids": individual_ids,
        "bundle_action_id": bundle_action.action_id,
        "strict_ambiguity_set_id": None if strict_match is None else strict_match.set_id,
        "strictly_ambiguous": strict_match is not None,
        "target_complete": True,
        "covered_targets": covered,
        "remaining_targets": remaining,
    }
    return ResourceComparisonSetV226.model_validate(
        {**payload, "set_sha256": semantic_sha256_v22(payload)}
    )


__all__ = (
    "ResourceComparisonSetV226",
    "build_resource_comparison_set_v226",
)
