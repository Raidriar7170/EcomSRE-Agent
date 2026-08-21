"""Typed read utility and negative coverage ledger for DTA v2.2.2."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import StrictBool, model_validator

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.memory import PredicateKindV22, SalientEvidenceMemoryV22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, ReadSourceStatusV22
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22


class ReadUtilityClassV222(str, Enum):
    PREDICATE_YIELD = "PREDICATE_YIELD"
    NONEMPTY_NO_PREDICATE = "NONEMPTY_NO_PREDICATE"
    EMPTY_CAPTURED = "EMPTY_CAPTURED"
    SOURCE_FAILURE = "SOURCE_FAILURE"


class ReadUtilityV222(DtaModelV22):
    outcome_class: ReadUtilityClassV222
    new_predicate_kinds: tuple[PredicateKindV22, ...]
    new_evidence_refs: tuple[str, ...]


class NegativeCoverageEntryV222(DtaModelV22):
    action_id: str
    source: str
    target_services: tuple[str, ...]
    outcome_class: ReadUtilityClassV222
    new_predicate_kinds: tuple[PredicateKindV22, ...]
    new_evidence_refs: tuple[str, ...]
    queried_capability_keys: tuple[str, ...]
    minimum_clause_gap_decreased: StrictBool
    hypothesis_contradicted: Literal[False]


class NegativeCoverageLedgerV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.negative-coverage-ledger.v1"]
    entries: tuple[NegativeCoverageEntryV222, ...]

    @classmethod
    def empty(cls) -> "NegativeCoverageLedgerV222":
        return cls(
            schema_version="dta-v22.2.negative-coverage-ledger.v1",
            entries=(),
        )

    @model_validator(mode="after")
    def require_ledger(self) -> "NegativeCoverageLedgerV222":
        ids = tuple(item.action_id for item in self.entries)
        if len(ids) != len(set(ids)):
            raise ValueError("negative coverage contains duplicate actions")
        return self

    @property
    def empty_source_target_keys(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    f"{item.source}:{target}"
                    for item in self.entries
                    if item.outcome_class
                    in {
                        ReadUtilityClassV222.EMPTY_CAPTURED,
                        ReadUtilityClassV222.NONEMPTY_NO_PREDICATE,
                    }
                    for target in item.target_services
                }
            )
        )


def classify_read_utility_v222(
    *,
    before_memory: SalientEvidenceMemoryV22,
    after_memory: SalientEvidenceMemoryV22,
    read_outcome: ReadOutcomeV22,
) -> ReadUtilityV222:
    before_predicates = {item.predicate_id for item in before_memory.predicates}
    before_refs = {item.evidence_ref for item in before_memory.evidence_refs}
    new_predicates = tuple(
        item for item in after_memory.predicates if item.predicate_id not in before_predicates
    )
    new_refs = tuple(
        item.evidence_ref
        for item in after_memory.evidence_refs
        if item.evidence_ref not in before_refs
    )
    if read_outcome.status in {
        ReadSourceStatusV22.FAILURE_UNAVAILABLE,
        ReadSourceStatusV22.FAILURE_TIMEOUT,
        ReadSourceStatusV22.FAILURE_SCHEMA,
    }:
        outcome_class = ReadUtilityClassV222.SOURCE_FAILURE
    elif new_predicates:
        outcome_class = ReadUtilityClassV222.PREDICATE_YIELD
    elif read_outcome.status is ReadSourceStatusV22.SUCCESS_EMPTY:
        outcome_class = ReadUtilityClassV222.EMPTY_CAPTURED
    else:
        outcome_class = ReadUtilityClassV222.NONEMPTY_NO_PREDICATE
    return ReadUtilityV222(
        outcome_class=outcome_class,
        new_predicate_kinds=tuple(
            sorted(
                {item.predicate_kind for item in new_predicates},
                key=lambda item: item.value,
            )
        ),
        new_evidence_refs=tuple(sorted(new_refs)),
    )


def record_negative_coverage_v222(
    *,
    ledger: NegativeCoverageLedgerV222,
    action: EvidenceActionV22,
    utility: ReadUtilityV222,
    minimum_gap_before: int,
    minimum_gap_after: int,
) -> NegativeCoverageLedgerV222:
    if any(item.action_id == action.action_id for item in ledger.entries):
        raise ValueError("negative coverage action was already recorded")
    entry = NegativeCoverageEntryV222(
        action_id=action.action_id,
        source=action.source.value,
        target_services=action.target_services,
        outcome_class=utility.outcome_class,
        new_predicate_kinds=utility.new_predicate_kinds,
        new_evidence_refs=utility.new_evidence_refs,
        queried_capability_keys=action.coverage_keys,
        minimum_clause_gap_decreased=minimum_gap_after < minimum_gap_before,
        hypothesis_contradicted=False,
    )
    return NegativeCoverageLedgerV222(
        schema_version="dta-v22.2.negative-coverage-ledger.v1",
        entries=(*ledger.entries, entry),
    )


__all__ = (
    "NegativeCoverageEntryV222",
    "NegativeCoverageLedgerV222",
    "ReadUtilityClassV222",
    "ReadUtilityV222",
    "classify_read_utility_v222",
    "record_negative_coverage_v222",
)
