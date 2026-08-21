"""One-step evidence-closed No-Incident admission for DTA v2.2.3."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from ecomsre.dta_v2.v22.gap_router_v223 import GapRoutingResultV223
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22


class NoIncidentClosureModeV223(str, Enum):
    LEGACY = "LEGACY"
    ONE_GAP_RELEVANT_READ = "ONE_GAP_RELEVANT_READ"


class ClosureOutcomeClassV223(str, Enum):
    PREDICATE_YIELD = "PREDICATE_YIELD"
    NONEMPTY_NO_PREDICATE = "NONEMPTY_NO_PREDICATE"
    EMPTY_CAPTURED = "EMPTY_CAPTURED"
    SOURCE_FAILURE = "SOURCE_FAILURE"


class ClosureActionCandidateV223(DtaModelV22):
    action_id: str
    rank_ordinal: StrictInt = Field(ge=1)
    executable: StrictBool
    shortest_clauses_completable: StrictInt = Field(ge=0)

    @property
    def gap_relevant(self) -> bool:
        return self.executable and self.shortest_clauses_completable > 0


class NoIncidentClosureStateV223(DtaModelV22):
    schema_version: Literal["dta-v22.3.no-incident-closure-state.v1"]
    mode: NoIncidentClosureModeV223
    closure_required: StrictBool
    closure_attempted: StrictBool
    closure_satisfied: StrictBool
    no_incident_withheld: StrictBool
    closure_action_id: str | None
    closure_action_rank: StrictInt | None = Field(default=None, ge=1)
    closure_outcome_class: ClosureOutcomeClassV223 | None
    closure_predicate_yield: StrictBool
    state_sha256: str

    @model_validator(mode="after")
    def require_state(self) -> "NoIncidentClosureStateV223":
        if self.closure_satisfied and not self.closure_attempted:
            raise ValueError("closure cannot be satisfied before an attempt")
        action_bound = self.closure_action_id is not None
        if action_bound != (self.closure_action_rank is not None):
            raise ValueError("closure action binding differs")
        if action_bound != self.closure_attempted:
            raise ValueError("closure attempt action binding differs")
        if self.closure_attempted != (self.closure_outcome_class is not None):
            raise ValueError("closure attempt outcome binding differs")
        if self.closure_predicate_yield != (
            self.closure_outcome_class is ClosureOutcomeClassV223.PREDICATE_YIELD
        ):
            raise ValueError("closure predicate-yield accounting differs")
        if self.mode is NoIncidentClosureModeV223.LEGACY and (
            self.closure_required
            or self.closure_attempted
            or self.closure_satisfied
            or self.no_incident_withheld
        ):
            raise ValueError("legacy closure mode changed No-Incident behavior")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"state_sha256"})
        )
        if self.state_sha256 != expected:
            raise ValueError("No-Incident closure state digest differs")
        return self


def _state(
    *,
    mode: NoIncidentClosureModeV223,
    closure_required: bool,
    closure_attempted: bool,
    closure_satisfied: bool,
    no_incident_withheld: bool,
    closure_action_id: str | None,
    closure_action_rank: int | None,
    closure_outcome_class: ClosureOutcomeClassV223 | None,
) -> NoIncidentClosureStateV223:
    payload = {
        "schema_version": "dta-v22.3.no-incident-closure-state.v1",
        "mode": mode.value,
        "closure_required": closure_required,
        "closure_attempted": closure_attempted,
        "closure_satisfied": closure_satisfied,
        "no_incident_withheld": no_incident_withheld,
        "closure_action_id": closure_action_id,
        "closure_action_rank": closure_action_rank,
        "closure_outcome_class": (
            None if closure_outcome_class is None else closure_outcome_class.value
        ),
        "closure_predicate_yield": (
            closure_outcome_class is ClosureOutcomeClassV223.PREDICATE_YIELD
        ),
    }
    return NoIncidentClosureStateV223(
        schema_version="dta-v22.3.no-incident-closure-state.v1",
        mode=mode,
        closure_required=closure_required,
        closure_attempted=closure_attempted,
        closure_satisfied=closure_satisfied,
        no_incident_withheld=no_incident_withheld,
        closure_action_id=closure_action_id,
        closure_action_rank=closure_action_rank,
        closure_outcome_class=closure_outcome_class,
        closure_predicate_yield=(
            closure_outcome_class is ClosureOutcomeClassV223.PREDICATE_YIELD
        ),
        state_sha256=semantic_sha256_v22(payload),
    )


def initial_no_incident_closure_state_v223(
    mode: NoIncidentClosureModeV223,
) -> NoIncidentClosureStateV223:
    return _state(
        mode=mode,
        closure_required=False,
        closure_attempted=False,
        closure_satisfied=False,
        no_incident_withheld=False,
        closure_action_id=None,
        closure_action_rank=None,
        closure_outcome_class=None,
    )


def closure_candidates_from_routing_v223(
    routing: GapRoutingResultV223,
) -> tuple[ClosureActionCandidateV223, ...]:
    executable = {item.action_id for item in routing.actions}
    return tuple(
        ClosureActionCandidateV223(
            action_id=item.action.action_id,
            rank_ordinal=item.rank_ordinal,
            executable=item.action.action_id in executable,
            shortest_clauses_completable=item.shortest_clauses_completable,
        )
        for item in routing.ranking
    )


def evaluate_no_incident_closure_v223(
    *,
    state: NoIncidentClosureStateV223,
    legacy_no_incident_exposed: bool,
    remaining_evidence_budget: float,
    ranked_actions: tuple[ClosureActionCandidateV223, ...],
) -> NoIncidentClosureStateV223:
    if remaining_evidence_budget < 0:
        raise ValueError("remaining evidence budget cannot be negative")
    if state.mode is NoIncidentClosureModeV223.LEGACY:
        return initial_no_incident_closure_state_v223(state.mode)
    if not legacy_no_incident_exposed:
        return _state(
            mode=state.mode,
            closure_required=False,
            closure_attempted=state.closure_attempted,
            closure_satisfied=state.closure_satisfied,
            no_incident_withheld=False,
            closure_action_id=state.closure_action_id,
            closure_action_rank=state.closure_action_rank,
            closure_outcome_class=state.closure_outcome_class,
        )
    if state.closure_attempted:
        return _state(
            mode=state.mode,
            closure_required=False,
            closure_attempted=True,
            closure_satisfied=state.closure_satisfied,
            no_incident_withheld=not state.closure_satisfied,
            closure_action_id=state.closure_action_id,
            closure_action_rank=state.closure_action_rank,
            closure_outcome_class=state.closure_outcome_class,
        )
    required = remaining_evidence_budget > 0 and any(
        item.gap_relevant for item in ranked_actions
    )
    return _state(
        mode=state.mode,
        closure_required=required,
        closure_attempted=False,
        closure_satisfied=False,
        no_incident_withheld=required,
        closure_action_id=None,
        closure_action_rank=None,
        closure_outcome_class=None,
    )


def record_no_incident_closure_attempt_v223(
    *,
    state: NoIncidentClosureStateV223,
    action: ClosureActionCandidateV223,
    outcome_class: ClosureOutcomeClassV223,
) -> NoIncidentClosureStateV223:
    if state.mode is NoIncidentClosureModeV223.LEGACY:
        return state
    if state.closure_attempted:
        raise ValueError("No-Incident closure was already attempted")
    if not state.closure_required or not action.gap_relevant:
        return state
    satisfied = outcome_class is not ClosureOutcomeClassV223.SOURCE_FAILURE
    return _state(
        mode=state.mode,
        closure_required=False,
        closure_attempted=True,
        closure_satisfied=satisfied,
        no_incident_withheld=not satisfied,
        closure_action_id=action.action_id,
        closure_action_rank=action.rank_ordinal,
        closure_outcome_class=outcome_class,
    )


__all__ = (
    "ClosureActionCandidateV223",
    "ClosureOutcomeClassV223",
    "NoIncidentClosureModeV223",
    "NoIncidentClosureStateV223",
    "closure_candidates_from_routing_v223",
    "evaluate_no_incident_closure_v223",
    "initial_no_incident_closure_state_v223",
    "record_no_incident_closure_attempt_v223",
)
