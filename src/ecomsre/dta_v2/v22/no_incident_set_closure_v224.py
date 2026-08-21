"""Ambiguity-set-complete No-Incident closure for DTA v2.2.4."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import StrictBool, model_validator

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.ambiguity_set_v224 import (
    EvidenceAmbiguitySetV224,
    update_ambiguity_set_coverage_v224,
)
from ecomsre.dta_v2.v22.negative_coverage_v222 import ReadUtilityClassV222
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22


class NoIncidentClosureScopeV224(str, Enum):
    ONE_TARGET_ATTEMPT = "ONE_TARGET_ATTEMPT"
    AMBIGUITY_SET_COMPLETE = "AMBIGUITY_SET_COMPLETE"


class NoIncidentSetClosureStateV224(DtaModelV22):
    schema_version: Literal["dta-v22.4.no-incident-set-closure-state.v1"]
    scope: NoIncidentClosureScopeV224
    closure_required: StrictBool
    closure_satisfied: StrictBool
    no_incident_withheld: StrictBool
    ambiguity_set: EvidenceAmbiguitySetV224 | None
    attempted_action_ids: tuple[str, ...]
    predicate_yield: StrictBool
    source_failure: StrictBool
    state_sha256: str

    @model_validator(mode="after")
    def require_state(self) -> "NoIncidentSetClosureStateV224":
        if self.attempted_action_ids != tuple(sorted(set(self.attempted_action_ids))):
            raise ValueError("set-closure attempted actions are not canonical")
        if self.source_failure and (
            self.closure_satisfied or not self.no_incident_withheld
        ):
            raise ValueError("set-closure source failure opened No-Incident")
        if self.predicate_yield and not self.closure_satisfied:
            raise ValueError("set-closure predicate yield was not satisfied")
        if (
            self.scope is NoIncidentClosureScopeV224.AMBIGUITY_SET_COMPLETE
            and self.ambiguity_set is not None
            and self.ambiguity_set.complete
            and not self.source_failure
            and not self.closure_satisfied
        ):
            raise ValueError("complete ambiguity set did not satisfy closure")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"state_sha256"})
        )
        if self.state_sha256 != expected:
            raise ValueError("set-closure state digest differs")
        return self


def _state(
    *,
    scope: NoIncidentClosureScopeV224,
    closure_required: bool,
    closure_satisfied: bool,
    no_incident_withheld: bool,
    ambiguity_set: EvidenceAmbiguitySetV224 | None,
    attempted_action_ids: tuple[str, ...],
    predicate_yield: bool,
    source_failure: bool,
) -> NoIncidentSetClosureStateV224:
    payload = {
        "schema_version": "dta-v22.4.no-incident-set-closure-state.v1",
        "scope": scope,
        "closure_required": closure_required,
        "closure_satisfied": closure_satisfied,
        "no_incident_withheld": no_incident_withheld,
        "ambiguity_set": (
            None if ambiguity_set is None else ambiguity_set.model_dump(mode="json")
        ),
        "attempted_action_ids": attempted_action_ids,
        "predicate_yield": predicate_yield,
        "source_failure": source_failure,
    }
    return NoIncidentSetClosureStateV224(
        schema_version="dta-v22.4.no-incident-set-closure-state.v1",
        scope=scope,
        closure_required=closure_required,
        closure_satisfied=closure_satisfied,
        no_incident_withheld=no_incident_withheld,
        ambiguity_set=ambiguity_set,
        attempted_action_ids=attempted_action_ids,
        predicate_yield=predicate_yield,
        source_failure=source_failure,
        state_sha256=semantic_sha256_v22(payload),
    )


def initial_no_incident_set_closure_state_v224(
    scope: NoIncidentClosureScopeV224,
) -> NoIncidentSetClosureStateV224:
    return _state(
        scope=scope,
        closure_required=False,
        closure_satisfied=False,
        no_incident_withheld=False,
        ambiguity_set=None,
        attempted_action_ids=(),
        predicate_yield=False,
        source_failure=False,
    )


def evaluate_no_incident_set_closure_v224(
    *,
    state: NoIncidentSetClosureStateV224,
    legacy_no_incident_exposed: bool,
    ambiguity_set: EvidenceAmbiguitySetV224 | None,
    target_complete: bool,
    remaining_evidence_budget: float,
    minimum_completion_cost: float,
) -> NoIncidentSetClosureStateV224:
    if remaining_evidence_budget < 0 or minimum_completion_cost < 0:
        raise ValueError("set-closure budget cannot be negative")
    current = ambiguity_set or state.ambiguity_set
    if (
        state.ambiguity_set is not None
        and current is not None
        and state.ambiguity_set.set_id != current.set_id
    ):
        raise ValueError("set-closure ambiguity-set identity changed")
    if not legacy_no_incident_exposed:
        return _state(
            scope=state.scope,
            closure_required=False,
            closure_satisfied=state.closure_satisfied,
            no_incident_withheld=False,
            ambiguity_set=current,
            attempted_action_ids=state.attempted_action_ids,
            predicate_yield=state.predicate_yield,
            source_failure=state.source_failure,
        )
    if state.source_failure:
        return _state(
            scope=state.scope,
            closure_required=False,
            closure_satisfied=False,
            no_incident_withheld=True,
            ambiguity_set=current,
            attempted_action_ids=state.attempted_action_ids,
            predicate_yield=False,
            source_failure=True,
        )
    if state.predicate_yield or state.closure_satisfied:
        return _state(
            scope=state.scope,
            closure_required=False,
            closure_satisfied=True,
            no_incident_withheld=False,
            ambiguity_set=current,
            attempted_action_ids=state.attempted_action_ids,
            predicate_yield=state.predicate_yield,
            source_failure=False,
        )
    if current is None:
        return initial_no_incident_set_closure_state_v224(state.scope)
    if (
        state.scope is NoIncidentClosureScopeV224.AMBIGUITY_SET_COMPLETE
        and current.complete
    ):
        return _state(
            scope=state.scope,
            closure_required=False,
            closure_satisfied=True,
            no_incident_withheld=False,
            ambiguity_set=current,
            attempted_action_ids=state.attempted_action_ids,
            predicate_yield=False,
            source_failure=False,
        )
    required = (
        target_complete
        and bool(current.remaining_target_services)
        and remaining_evidence_budget >= minimum_completion_cost
    )
    return _state(
        scope=state.scope,
        closure_required=required,
        closure_satisfied=False,
        no_incident_withheld=required,
        ambiguity_set=current,
        attempted_action_ids=state.attempted_action_ids,
        predicate_yield=False,
        source_failure=False,
    )


def record_no_incident_set_closure_attempt_v224(
    *,
    state: NoIncidentSetClosureStateV224,
    action: EvidenceActionV22,
    outcome_class: ReadUtilityClassV222,
) -> NoIncidentSetClosureStateV224:
    current = state.ambiguity_set
    if current is None or not state.closure_required:
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
        )
    updated = update_ambiguity_set_coverage_v224(
        ambiguity_set=current,
        covered_targets=covered,
    )
    yielded = outcome_class is ReadUtilityClassV222.PREDICATE_YIELD
    satisfied = (
        yielded
        or state.scope is NoIncidentClosureScopeV224.ONE_TARGET_ATTEMPT
        or updated.complete
    )
    return _state(
        scope=state.scope,
        closure_required=not satisfied,
        closure_satisfied=satisfied,
        no_incident_withheld=not satisfied,
        ambiguity_set=updated,
        attempted_action_ids=attempted,
        predicate_yield=yielded,
        source_failure=False,
    )


__all__ = (
    "NoIncidentClosureScopeV224",
    "NoIncidentSetClosureStateV224",
    "evaluate_no_incident_set_closure_v224",
    "initial_no_incident_set_closure_state_v224",
    "record_no_incident_set_closure_attempt_v224",
)
