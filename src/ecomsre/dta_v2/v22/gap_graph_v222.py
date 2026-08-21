"""Predicate Gap Graph derived only from current runtime-owned evidence."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from ecomsre.dta_v2.v22.controller_contracts import HypothesisCatalogV22
from ecomsre.dta_v2.v22.effective_policy_v222 import (
    EffectiveSupportPolicyV222,
    predicate_matches_requirement_v222,
)
from ecomsre.dta_v2.v22.memory import PredicateKindV22, SalientEvidenceMemoryV22
from ecomsre.dta_v2.v22.predicates import (
    MechanismV22,
    RequirementServiceBindingV22,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22


class PredicateGapV222(DtaModelV22):
    predicate_kind: PredicateKindV22
    service_binding: RequirementServiceBindingV22
    require_exact_parent: StrictBool
    target_service: str
    parent_service: str | None


class ClauseProgressV222(DtaModelV22):
    hypothesis_id: str
    mechanism: MechanismV22
    target_service: str
    parent_service: str | None
    clause_id: str
    satisfied_predicate_kinds: tuple[PredicateKindV22, ...]
    supporting_evidence_refs: tuple[str, ...]
    missing_requirements: tuple[PredicateGapV222, ...]
    missing_count: StrictInt = Field(ge=0)
    complete: StrictBool

    @model_validator(mode="after")
    def require_progress(self) -> "ClauseProgressV222":
        if self.missing_count != len(self.missing_requirements):
            raise ValueError("clause gap count differs")
        if self.complete != (self.missing_count == 0):
            raise ValueError("clause completion differs from gaps")
        if self.supporting_evidence_refs != tuple(
            sorted(set(self.supporting_evidence_refs))
        ):
            raise ValueError("clause evidence refs are not canonical")
        return self


class HypothesisGapStateV222(DtaModelV22):
    hypothesis_id: str
    mechanism: MechanismV22
    target_service: str
    parent_service: str | None
    clauses: tuple[ClauseProgressV222, ...]
    minimum_missing_count: StrictInt = Field(ge=0)
    complete: StrictBool
    planner_focus: StrictBool

    @model_validator(mode="after")
    def require_state(self) -> "HypothesisGapStateV222":
        expected = min(item.missing_count for item in self.clauses)
        if self.minimum_missing_count != expected:
            raise ValueError("hypothesis minimum gap differs")
        if self.complete != any(item.complete for item in self.clauses):
            raise ValueError("hypothesis completion differs from DNF clauses")
        return self


class GapGraphV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.predicate-gap-graph.v1"]
    policy_sha256: str
    memory_sha256: str
    hypotheses: tuple[HypothesisGapStateV222, ...]
    planner_focus_hypothesis_id: str | None
    prior_negative_coverage: tuple[str, ...]
    truth_consulted: Literal[False]
    graph_sha256: str

    @model_validator(mode="after")
    def require_graph(self) -> "GapGraphV222":
        ids = tuple(item.hypothesis_id for item in self.hypotheses)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("gap graph hypotheses are not canonical")
        if self.prior_negative_coverage != tuple(
            sorted(set(self.prior_negative_coverage))
        ):
            raise ValueError("gap graph negative coverage is not canonical")
        if self.planner_focus_hypothesis_id is not None and (
            self.planner_focus_hypothesis_id not in set(ids)
        ):
            raise ValueError("planner focus is outside incident hypotheses")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"graph_sha256"})
        )
        if self.graph_sha256 != expected:
            raise ValueError("gap graph digest differs")
        return self


def _parent_for(
    *, target: str, mechanism: MechanismV22, edges: tuple[tuple[str, str], ...]
) -> str | None:
    if mechanism is not MechanismV22.DEPENDENCY_LATENCY:
        return None
    return next(
        (right if left == target else left for left, right in edges if target in {left, right}),
        None,
    )


def build_gap_graph_v222(
    *,
    policy: EffectiveSupportPolicyV222,
    hypothesis_catalog: HypothesisCatalogV22,
    memory: SalientEvidenceMemoryV22,
    topology_edges: tuple[tuple[str, str], ...],
    planner_focus_hypothesis_id: str | None,
    prior_negative_coverage: tuple[str, ...],
) -> GapGraphV222:
    """Flat recomputes from memory; Planner may add only runtime-owned prior focus."""

    states: list[HypothesisGapStateV222] = []
    for entry in hypothesis_catalog.hypotheses:
        target = entry.target_service
        if target is None or entry.mechanism in {
            MechanismV22.NO_INCIDENT,
            MechanismV22.UNKNOWN,
        }:
            continue
        parent = _parent_for(
            target=target,
            mechanism=entry.mechanism,
            edges=topology_edges,
        )
        progress: list[ClauseProgressV222] = []
        for clause in policy.clauses:
            if clause.mechanism is not entry.mechanism:
                continue
            satisfied_kinds: list[PredicateKindV22] = []
            refs: set[str] = set()
            gaps: list[PredicateGapV222] = []
            for requirement in clause.requirements:
                predicate = next(
                    (
                        item
                        for item in memory.predicates
                        if predicate_matches_requirement_v222(
                            predicate=item,
                            requirement=requirement,
                            target_service=target,
                            parent_service=parent,
                        )
                    ),
                    None,
                )
                if predicate is None:
                    gaps.append(
                        PredicateGapV222(
                            predicate_kind=requirement.predicate_kind,
                            service_binding=requirement.service_binding,
                            require_exact_parent=requirement.require_exact_parent,
                            target_service=target,
                            parent_service=parent,
                        )
                    )
                else:
                    satisfied_kinds.append(predicate.predicate_kind)
                    refs.update(predicate.evidence_refs)
            progress.append(
                ClauseProgressV222(
                    hypothesis_id=entry.hypothesis_id,
                    mechanism=entry.mechanism,
                    target_service=target,
                    parent_service=parent,
                    clause_id=clause.clause_id,
                    satisfied_predicate_kinds=tuple(satisfied_kinds),
                    supporting_evidence_refs=tuple(sorted(refs)),
                    missing_requirements=tuple(gaps),
                    missing_count=len(gaps),
                    complete=not gaps,
                )
            )
        states.append(
            HypothesisGapStateV222(
                hypothesis_id=entry.hypothesis_id,
                mechanism=entry.mechanism,
                target_service=target,
                parent_service=parent,
                clauses=tuple(sorted(progress, key=lambda item: item.clause_id)),
                minimum_missing_count=min(item.missing_count for item in progress),
                complete=any(item.complete for item in progress),
                planner_focus=entry.hypothesis_id == planner_focus_hypothesis_id,
            )
        )
    payload = {
        "schema_version": "dta-v22.2.predicate-gap-graph.v1",
        "policy_sha256": policy.policy_sha256,
        "memory_sha256": memory.memory_sha256,
        "hypotheses": tuple(sorted(states, key=lambda item: item.hypothesis_id)),
        "planner_focus_hypothesis_id": planner_focus_hypothesis_id,
        "prior_negative_coverage": tuple(sorted(set(prior_negative_coverage))),
        "truth_consulted": False,
    }
    draft = GapGraphV222.model_construct(**payload, graph_sha256="0" * 64)
    return GapGraphV222.model_validate(
        {
            **payload,
            "graph_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"graph_sha256"})
            ),
        }
    )


__all__ = (
    "ClauseProgressV222",
    "GapGraphV222",
    "HypothesisGapStateV222",
    "PredicateGapV222",
    "build_gap_graph_v222",
)
