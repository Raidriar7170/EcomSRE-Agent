"""Read-time ambiguity coverage retained independently from closure timing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.v22.negative_coverage_v222 import ReadUtilityClassV222
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)

if TYPE_CHECKING:
    from ecomsre.dta_v2.v22.ambiguity_set_v225 import EvidenceAmbiguitySetV225


class AmbiguityCoverageEventV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.ambiguity-coverage-event.v1"]
    action_id: str
    source: EvidenceSourceV22
    target_services: tuple[str, ...]
    set_ids_covered: tuple[str, ...]
    outcome_class: ReadUtilityClassV222
    new_predicate_kinds: tuple[str, ...]
    recorded_at_read_ordinal: StrictInt = Field(ge=1)
    event_sha256: str

    @model_validator(mode="after")
    def require_event(self) -> "AmbiguityCoverageEventV225":
        for values in (
            self.target_services,
            self.set_ids_covered,
            self.new_predicate_kinds,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("ambiguity coverage event values are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"event_sha256"})
        )
        if self.event_sha256 != expected:
            raise ValueError("ambiguity coverage event digest differs")
        return self


class AmbiguityCoverageLedgerV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.ambiguity-coverage-ledger.v1"]
    events: tuple[AmbiguityCoverageEventV225, ...]
    covered_targets_by_set: dict[str, tuple[str, ...]]
    source_failures_by_set: dict[str, tuple[str, ...]]
    ledger_sha256: str

    @classmethod
    def empty(cls) -> "AmbiguityCoverageLedgerV225":
        payload = {
            "schema_version": "dta-v22.5.ambiguity-coverage-ledger.v1",
            "events": (),
            "covered_targets_by_set": {},
            "source_failures_by_set": {},
        }
        return cls.model_validate(
            {**payload, "ledger_sha256": semantic_sha256_v22(payload)}
        )

    @model_validator(mode="after")
    def require_ledger(self) -> "AmbiguityCoverageLedgerV225":
        ordinals = tuple(item.recorded_at_read_ordinal for item in self.events)
        if ordinals != tuple(range(1, len(self.events) + 1)):
            raise ValueError("ambiguity coverage ledger read ordinals differ")
        for mapping in (self.covered_targets_by_set, self.source_failures_by_set):
            if tuple(mapping) != tuple(sorted(mapping)):
                raise ValueError("ambiguity coverage ledger keys are not canonical")
            if any(values != tuple(sorted(set(values))) for values in mapping.values()):
                raise ValueError("ambiguity coverage ledger values are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"ledger_sha256"})
        )
        if self.ledger_sha256 != expected:
            raise ValueError("ambiguity coverage ledger digest differs")
        return self


def _matching_targets(
    event: AmbiguityCoverageEventV225,
    ambiguity_set: "EvidenceAmbiguitySetV225",
) -> tuple[str, ...]:
    if event.source is not ambiguity_set.source:
        return ()
    return tuple(
        target for target in event.target_services if target in set(ambiguity_set.target_services)
    )


def record_ambiguity_coverage_event_v225(
    *,
    ledger: AmbiguityCoverageLedgerV225,
    action_id: str,
    source: EvidenceSourceV22,
    target_services: tuple[str, ...],
    ambiguity_sets: tuple["EvidenceAmbiguitySetV225", ...],
    outcome_class: ReadUtilityClassV222,
    new_predicate_kinds: tuple[str, ...],
    read_ordinal: int,
) -> AmbiguityCoverageLedgerV225:
    if read_ordinal != len(ledger.events) + 1:
        raise ValueError("ambiguity coverage event must be recorded at the next read ordinal")
    canonical_targets = tuple(sorted(set(target_services)))
    matching = tuple(
        sorted(
            item.set_id
            for item in ambiguity_sets
            if source is item.source and set(canonical_targets).intersection(item.target_services)
        )
    )
    event_payload = {
        "schema_version": "dta-v22.5.ambiguity-coverage-event.v1",
        "action_id": action_id,
        "source": source,
        "target_services": canonical_targets,
        "set_ids_covered": matching,
        "outcome_class": outcome_class,
        "new_predicate_kinds": tuple(sorted(set(new_predicate_kinds))),
        "recorded_at_read_ordinal": read_ordinal,
    }
    event = AmbiguityCoverageEventV225.model_validate(
        {**event_payload, "event_sha256": semantic_sha256_v22(event_payload)}
    )
    events = (*ledger.events, event)
    covered: dict[str, tuple[str, ...]] = {}
    failures: dict[str, tuple[str, ...]] = {}
    for ambiguity_set in sorted(ambiguity_sets, key=lambda item: item.set_id):
        set_targets: set[str] = set()
        failure_actions: set[str] = set()
        for recorded in events:
            matched = _matching_targets(recorded, ambiguity_set)
            if not matched:
                continue
            if recorded.outcome_class is ReadUtilityClassV222.SOURCE_FAILURE:
                failure_actions.add(recorded.action_id)
            else:
                set_targets.update(matched)
        covered[ambiguity_set.set_id] = tuple(sorted(set_targets))
        failures[ambiguity_set.set_id] = tuple(sorted(failure_actions))
    ledger_payload = {
        "schema_version": "dta-v22.5.ambiguity-coverage-ledger.v1",
        "events": events,
        "covered_targets_by_set": dict(sorted(covered.items())),
        "source_failures_by_set": dict(sorted(failures.items())),
    }
    digest_payload = {
        **ledger_payload,
        "events": tuple(item.model_dump(mode="json") for item in events),
    }
    return AmbiguityCoverageLedgerV225.model_validate(
        {**ledger_payload, "ledger_sha256": semantic_sha256_v22(digest_payload)}
    )


def rebuild_ambiguity_set_coverage_v225(
    *,
    ambiguity_set: "EvidenceAmbiguitySetV225",
    ledger: AmbiguityCoverageLedgerV225,
) -> "EvidenceAmbiguitySetV225":
    from ecomsre.dta_v2.v22.ambiguity_set_v225 import update_ambiguity_set_coverage_v225

    covered = {
        target
        for event in ledger.events
        if event.outcome_class is not ReadUtilityClassV222.SOURCE_FAILURE
        for target in _matching_targets(event, ambiguity_set)
    }
    return update_ambiguity_set_coverage_v225(
        ambiguity_set=ambiguity_set,
        covered_targets=tuple(sorted(covered)),
    )


def source_failures_for_set_v225(
    *,
    ambiguity_set: "EvidenceAmbiguitySetV225",
    ledger: AmbiguityCoverageLedgerV225,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                event.action_id
                for event in ledger.events
                if event.outcome_class is ReadUtilityClassV222.SOURCE_FAILURE
                and _matching_targets(event, ambiguity_set)
            }
        )
    )


__all__ = (
    "AmbiguityCoverageEventV225",
    "AmbiguityCoverageLedgerV225",
    "rebuild_ambiguity_set_coverage_v225",
    "record_ambiguity_coverage_event_v225",
    "source_failures_for_set_v225",
)
