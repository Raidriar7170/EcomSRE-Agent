"""Explicit post-read evidence and gap delta projected before full memory."""

from __future__ import annotations

from typing import Literal, Mapping

from pydantic import Field, StrictBool

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.gap_graph_v222 import GapGraphV222
from ecomsre.dta_v2.v22.memory import PredicateKindV22
from ecomsre.dta_v2.v22.negative_coverage_v222 import (
    ReadUtilityClassV222,
    ReadUtilityV222,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, EvidenceSourceV22
from ecomsre.dta_v2.v22.terminal_catalog_v222 import TerminalCatalogV222


class RemainingGapProjectionV222(DtaModelV22):
    hypothesis_id: str
    clause_id: str
    missing_count: int = Field(ge=1)
    missing_predicate_kinds: tuple[PredicateKindV22, ...]


class PostReadDeltaV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.post-read-delta.v1"]
    last_action_alias: str
    last_action_source: EvidenceSourceV22
    last_action_targets: tuple[str, ...]
    outcome_class: ReadUtilityClassV222
    new_evidence_aliases: tuple[str, ...]
    new_predicates: tuple[PredicateKindV22, ...]
    negative_coverage: StrictBool
    minimum_missing_gap_before: int = Field(ge=0)
    minimum_missing_gap_after: int = Field(ge=0)
    newly_available_terminal_aliases: tuple[str, ...]
    remaining_top_gaps: tuple[RemainingGapProjectionV222, ...]
    ranked_next_action_aliases: tuple[str, ...]


def build_post_read_delta_v222(
    *,
    action_alias: str,
    action: EvidenceActionV22,
    utility: ReadUtilityV222,
    minimum_gap_before: int,
    minimum_gap_after: int,
    before_terminal_ids: tuple[str, ...],
    after_terminal_catalog: TerminalCatalogV222,
    remaining_top_gaps: GapGraphV222,
    ranked_next_action_aliases: tuple[str, ...],
    evidence_aliases: Mapping[str, str],
) -> PostReadDeltaV222:
    before = set(before_terminal_ids)
    gaps = tuple(
        sorted(
            (
                RemainingGapProjectionV222(
                    hypothesis_id=hypothesis.hypothesis_id,
                    clause_id=clause.clause_id,
                    missing_count=clause.missing_count,
                    missing_predicate_kinds=tuple(
                        item.predicate_kind for item in clause.missing_requirements
                    ),
                )
                for hypothesis in remaining_top_gaps.hypotheses
                if not hypothesis.complete
                for clause in hypothesis.clauses
                if clause.missing_count > 0
                and clause.missing_count == hypothesis.minimum_missing_count
            ),
            key=lambda item: (
                item.missing_count,
                item.hypothesis_id,
                item.clause_id,
            ),
        )[:6]
    )
    return PostReadDeltaV222(
        schema_version="dta-v22.2.post-read-delta.v1",
        last_action_alias=action_alias,
        last_action_source=action.source,
        last_action_targets=action.target_services,
        outcome_class=utility.outcome_class,
        new_evidence_aliases=tuple(
            sorted(
                evidence_aliases[ref]
                for ref in utility.new_evidence_refs
                if ref in evidence_aliases
            )
        ),
        new_predicates=utility.new_predicate_kinds,
        negative_coverage=utility.outcome_class
        in {
            ReadUtilityClassV222.EMPTY_CAPTURED,
            ReadUtilityClassV222.NONEMPTY_NO_PREDICATE,
        },
        minimum_missing_gap_before=minimum_gap_before,
        minimum_missing_gap_after=minimum_gap_after,
        newly_available_terminal_aliases=tuple(
            item.terminal_alias
            for item in after_terminal_catalog.candidates
            if item.terminal_id not in before
        ),
        remaining_top_gaps=gaps,
        ranked_next_action_aliases=ranked_next_action_aliases,
    )


__all__ = (
    "PostReadDeltaV222",
    "RemainingGapProjectionV222",
    "build_post_read_delta_v222",
)
