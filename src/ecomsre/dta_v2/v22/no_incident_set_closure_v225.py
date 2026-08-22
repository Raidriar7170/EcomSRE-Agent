"""Fail-closed ambiguity-set No-Incident closure for DTA v2.2.5."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import StrictBool, model_validator

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.ambiguity_set_v225 import (
    EvidenceAmbiguitySetV225,
    update_ambiguity_set_coverage_v225,
)
from ecomsre.dta_v2.v22.negative_coverage_v222 import ReadUtilityClassV222
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22


class NoIncidentClosureScopeV225(str, Enum):
    ONE_TARGET_ATTEMPT = "ONE_TARGET_ATTEMPT"
    AMBIGUITY_SET_COMPLETE = "AMBIGUITY_SET_COMPLETE"


class ClosureDispositionV225(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    READABLE_INCOMPLETE = "READABLE_INCOMPLETE"
    BUDGET_INSUFFICIENT = "BUDGET_INSUFFICIENT"
    SOURCE_FAILURE = "SOURCE_FAILURE"
    COMPLETE_NORMAL = "COMPLETE_NORMAL"
    INCIDENT_PREDICATE_YIELDED = "INCIDENT_PREDICATE_YIELDED"


AbstainReasonV225 = Literal[
    "INSUFFICIENT_BUDGET_FOR_AMBIGUITY_CLOSURE",
    "REQUIRED_AMBIGUITY_SOURCE_FAILURE",
    "INCOMPLETE_AMBIGUITY_EVIDENCE",
]


class NoIncidentSetClosureStateV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.no-incident-set-closure-state.v1"]
    scope: NoIncidentClosureScopeV225
    closure_required: StrictBool
    closure_satisfied: StrictBool
    no_incident_withheld: StrictBool
    ambiguity_set: EvidenceAmbiguitySetV225 | None
    attempted_action_ids: tuple[str, ...]
    predicate_yield: StrictBool
    source_failure: StrictBool
    closure_disposition: ClosureDispositionV225
    abstain_reason: AbstainReasonV225 | None
    state_sha256: str

    @model_validator(mode="after")
    def require_state(self) -> "NoIncidentSetClosureStateV225":
        if self.attempted_action_ids != tuple(sorted(set(self.attempted_action_ids))):
            raise ValueError("set-closure attempted actions are not canonical")
        if self.closure_disposition is ClosureDispositionV225.READABLE_INCOMPLETE:
            if not self.closure_required or not self.no_incident_withheld or self.abstain_reason:
                raise ValueError("readable incomplete closure state differs")
        if self.closure_disposition is ClosureDispositionV225.BUDGET_INSUFFICIENT:
            if (
                self.closure_required
                or not self.no_incident_withheld
                or self.abstain_reason
                != "INSUFFICIENT_BUDGET_FOR_AMBIGUITY_CLOSURE"
            ):
                raise ValueError("budget-insufficient closure did not fail closed")
        if self.closure_disposition is ClosureDispositionV225.SOURCE_FAILURE:
            if self.closure_satisfied or not self.no_incident_withheld or self.abstain_reason not in {
                "REQUIRED_AMBIGUITY_SOURCE_FAILURE",
                "INCOMPLETE_AMBIGUITY_EVIDENCE",
            }:
                raise ValueError("source-failure closure did not fail closed")
        if self.closure_disposition is ClosureDispositionV225.COMPLETE_NORMAL:
            if not self.closure_satisfied or self.no_incident_withheld or self.abstain_reason:
                raise ValueError("complete normal closure did not open No-Incident")
        if self.closure_disposition is ClosureDispositionV225.INCIDENT_PREDICATE_YIELDED:
            if not self.predicate_yield or not self.closure_satisfied or not self.no_incident_withheld:
                raise ValueError("incident predicate yield did not keep No-Incident closed")
        if self.source_failure != (
            self.closure_disposition is ClosureDispositionV225.SOURCE_FAILURE
            and self.abstain_reason == "REQUIRED_AMBIGUITY_SOURCE_FAILURE"
        ):
            raise ValueError("set-closure source-failure accounting differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"state_sha256"})
        )
        if self.state_sha256 != expected:
            raise ValueError("set-closure state digest differs")
        return self


def _state(
    *,
    scope: NoIncidentClosureScopeV225,
    closure_required: bool,
    closure_satisfied: bool,
    no_incident_withheld: bool,
    ambiguity_set: EvidenceAmbiguitySetV225 | None,
    attempted_action_ids: tuple[str, ...],
    predicate_yield: bool,
    source_failure: bool,
    closure_disposition: ClosureDispositionV225,
    abstain_reason: AbstainReasonV225 | None,
) -> NoIncidentSetClosureStateV225:
    payload = {
        "schema_version": "dta-v22.5.no-incident-set-closure-state.v1",
        "scope": scope,
        "closure_required": closure_required,
        "closure_satisfied": closure_satisfied,
        "no_incident_withheld": no_incident_withheld,
        "ambiguity_set": ambiguity_set,
        "attempted_action_ids": tuple(sorted(set(attempted_action_ids))),
        "predicate_yield": predicate_yield,
        "source_failure": source_failure,
        "closure_disposition": closure_disposition,
        "abstain_reason": abstain_reason,
    }
    digest_payload = {
        **payload,
        "ambiguity_set": (
            None if ambiguity_set is None else ambiguity_set.model_dump(mode="json")
        ),
    }
    return NoIncidentSetClosureStateV225.model_validate(
        {**payload, "state_sha256": semantic_sha256_v22(digest_payload)}
    )


def initial_no_incident_set_closure_state_v225(
    scope: NoIncidentClosureScopeV225,
) -> NoIncidentSetClosureStateV225:
    return _state(
        scope=scope,
        closure_required=False,
        closure_satisfied=False,
        no_incident_withheld=False,
        ambiguity_set=None,
        attempted_action_ids=(),
        predicate_yield=False,
        source_failure=False,
        closure_disposition=ClosureDispositionV225.NOT_APPLICABLE,
        abstain_reason=None,
    )


def minimum_completion_cost_v225(
    *,
    ambiguity_set: EvidenceAmbiguitySetV225,
    individual_actions: tuple[EvidenceActionV22, ...],
    bundle_action: EvidenceActionV22 | None,
    prefer_bundle: bool,
) -> float | None:
    remaining = set(ambiguity_set.remaining_target_services)
    if not remaining:
        return 0.0
    if (
        prefer_bundle
        and bundle_action is not None
        and remaining.issubset(bundle_action.target_services)
    ):
        return bundle_action.weighted_cost
    total = 0.0
    for target in sorted(remaining):
        costs = [
            action.weighted_cost
            for action in individual_actions
            if len(action.target_services) == 1
            and action.target_services[0] == target
            and action.action_id in set(ambiguity_set.individual_action_ids)
        ]
        if not costs:
            return None
        total += min(costs)
    return total


def evaluate_no_incident_set_closure_v225(
    *,
    state: NoIncidentSetClosureStateV225,
    legacy_no_incident_exposed: bool,
    ambiguity_set: EvidenceAmbiguitySetV225 | None,
    target_complete: bool,
    remaining_evidence_budget: float,
    minimum_completion_cost: float | None,
) -> NoIncidentSetClosureStateV225:
    if remaining_evidence_budget < 0 or (
        minimum_completion_cost is not None and minimum_completion_cost < 0
    ):
        raise ValueError("set-closure budget cannot be negative")
    current = ambiguity_set or state.ambiguity_set
    if (
        state.ambiguity_set is not None
        and current is not None
        and state.ambiguity_set.set_id != current.set_id
    ):
        raise ValueError("set-closure ambiguity-set identity changed")
    common = {
        "scope": state.scope,
        "ambiguity_set": current,
        "attempted_action_ids": state.attempted_action_ids,
    }
    if state.source_failure:
        return _state(
            **common,
            closure_required=False,
            closure_satisfied=False,
            no_incident_withheld=True,
            predicate_yield=False,
            source_failure=True,
            closure_disposition=ClosureDispositionV225.SOURCE_FAILURE,
            abstain_reason="REQUIRED_AMBIGUITY_SOURCE_FAILURE",
        )
    if state.predicate_yield:
        return _state(
            **common,
            closure_required=False,
            closure_satisfied=True,
            no_incident_withheld=True,
            predicate_yield=True,
            source_failure=False,
            closure_disposition=ClosureDispositionV225.INCIDENT_PREDICATE_YIELDED,
            abstain_reason=None,
        )
    if not legacy_no_incident_exposed:
        return _state(
            **common,
            closure_required=False,
            closure_satisfied=state.closure_satisfied,
            no_incident_withheld=False,
            predicate_yield=False,
            source_failure=False,
            closure_disposition=ClosureDispositionV225.NOT_APPLICABLE,
            abstain_reason=None,
        )
    if current is None:
        return _state(
            **common,
            closure_required=False,
            closure_satisfied=False,
            no_incident_withheld=False,
            predicate_yield=False,
            source_failure=False,
            closure_disposition=ClosureDispositionV225.NOT_APPLICABLE,
            abstain_reason=None,
        )
    if current.complete or (
        state.scope is NoIncidentClosureScopeV225.ONE_TARGET_ATTEMPT
        and state.closure_satisfied
    ):
        return _state(
            **common,
            closure_required=False,
            closure_satisfied=True,
            no_incident_withheld=False,
            predicate_yield=False,
            source_failure=False,
            closure_disposition=ClosureDispositionV225.COMPLETE_NORMAL,
            abstain_reason=None,
        )
    if not target_complete or minimum_completion_cost is None:
        return _state(
            **common,
            closure_required=False,
            closure_satisfied=False,
            no_incident_withheld=True,
            predicate_yield=False,
            source_failure=False,
            closure_disposition=ClosureDispositionV225.SOURCE_FAILURE,
            abstain_reason="INCOMPLETE_AMBIGUITY_EVIDENCE",
        )
    if remaining_evidence_budget < minimum_completion_cost:
        return _state(
            **common,
            closure_required=False,
            closure_satisfied=False,
            no_incident_withheld=True,
            predicate_yield=False,
            source_failure=False,
            closure_disposition=ClosureDispositionV225.BUDGET_INSUFFICIENT,
            abstain_reason="INSUFFICIENT_BUDGET_FOR_AMBIGUITY_CLOSURE",
        )
    return _state(
        **common,
        closure_required=True,
        closure_satisfied=False,
        no_incident_withheld=True,
        predicate_yield=False,
        source_failure=False,
        closure_disposition=ClosureDispositionV225.READABLE_INCOMPLETE,
        abstain_reason=None,
    )


def record_no_incident_set_closure_attempt_v225(
    *,
    state: NoIncidentSetClosureStateV225,
    action: EvidenceActionV22,
    outcome_class: ReadUtilityClassV222,
) -> NoIncidentSetClosureStateV225:
    current = state.ambiguity_set
    if current is None:
        return state
    covered = tuple(
        target for target in action.target_services if target in current.target_services
    )
    if not covered:
        return state
    attempted = tuple(sorted({*state.attempted_action_ids, action.action_id}))
    if outcome_class is ReadUtilityClassV222.SOURCE_FAILURE:
        return _state(
            scope=state.scope,
            closure_required=False,
            closure_satisfied=False,
            no_incident_withheld=True,
            ambiguity_set=current,
            attempted_action_ids=attempted,
            predicate_yield=False,
            source_failure=True,
            closure_disposition=ClosureDispositionV225.SOURCE_FAILURE,
            abstain_reason="REQUIRED_AMBIGUITY_SOURCE_FAILURE",
        )
    updated = update_ambiguity_set_coverage_v225(
        ambiguity_set=current,
        covered_targets=covered,
    )
    yielded = outcome_class is ReadUtilityClassV222.PREDICATE_YIELD
    satisfied = (
        yielded
        or state.scope is NoIncidentClosureScopeV225.ONE_TARGET_ATTEMPT
        or updated.complete
    )
    disposition = (
        ClosureDispositionV225.INCIDENT_PREDICATE_YIELDED
        if yielded
        else ClosureDispositionV225.COMPLETE_NORMAL
        if satisfied
        else ClosureDispositionV225.READABLE_INCOMPLETE
    )
    return _state(
        scope=state.scope,
        closure_required=not satisfied,
        closure_satisfied=satisfied,
        no_incident_withheld=yielded or not satisfied,
        ambiguity_set=updated,
        attempted_action_ids=attempted,
        predicate_yield=yielded,
        source_failure=False,
        closure_disposition=disposition,
        abstain_reason=None,
    )


__all__ = (
    "AbstainReasonV225",
    "ClosureDispositionV225",
    "NoIncidentClosureScopeV225",
    "NoIncidentSetClosureStateV225",
    "evaluate_no_incident_set_closure_v225",
    "initial_no_incident_set_closure_state_v225",
    "minimum_completion_cost_v225",
    "record_no_incident_set_closure_attempt_v225",
)
