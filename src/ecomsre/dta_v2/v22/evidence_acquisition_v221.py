"""Runtime-only terminal exploration policy for the DTA v2.2.1 study."""

from __future__ import annotations

from enum import Enum

from ecomsre.dta_v2.v22.action_catalog import ActionCatalogV22
from ecomsre.dta_v2.v22.controller_contracts import ControllerDecisionKindV22
from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22


class TerminalExplorationPolicyV221(str, Enum):
    LEGACY = "LEGACY"
    MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN = "MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN"


class TerminalExplorationDispositionV221(str, Enum):
    ALLOW = "ALLOW"
    PREMATURE_ABSTENTION = "PREMATURE_ABSTENTION"


class StudyCombinationV221(str, Enum):
    FLAT_LEGACY = "FLAT_LEGACY"
    FLAT_GATE = "FLAT_GATE"
    PLANNER_LEGACY = "PLANNER_LEGACY"
    PLANNER_GATE = "PLANNER_GATE"

    @property
    def arm(self) -> ControllerArmV22:
        return (
            ControllerArmV22.FLAT_CANONICAL
            if self in {self.FLAT_LEGACY, self.FLAT_GATE}
            else ControllerArmV22.PLANNER_LITE
        )

    @property
    def policy(self) -> TerminalExplorationPolicyV221:
        return (
            TerminalExplorationPolicyV221.LEGACY
            if self in {self.FLAT_LEGACY, self.PLANNER_LEGACY}
            else TerminalExplorationPolicyV221.MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN
        )


def evaluate_terminal_exploration_policy_v221(
    *,
    policy: TerminalExplorationPolicyV221,
    decision: ControllerDecisionKindV22,
    session_read_dispatches: int,
    action_catalog: ActionCatalogV22,
    remaining_evidence_budget: float,
    policy_redirect_used: bool,
) -> TerminalExplorationDispositionV221:
    """Reject only one first-read ABSTAIN without selecting an evidence action."""

    if not isinstance(policy, TerminalExplorationPolicyV221):
        raise TypeError("terminal exploration policy is invalid")
    if not isinstance(decision, ControllerDecisionKindV22):
        raise TypeError("controller decision kind is invalid")
    if type(session_read_dispatches) is not int or session_read_dispatches < 0:
        raise ValueError("session read dispatch count is invalid")
    if not isinstance(action_catalog, ActionCatalogV22):
        raise TypeError("action catalog is invalid")
    if type(remaining_evidence_budget) not in {int, float} or remaining_evidence_budget < 0:
        raise ValueError("remaining evidence budget is invalid")
    if type(policy_redirect_used) is not bool:
        raise TypeError("policy redirect state is invalid")
    if action_catalog.remaining_budget != float(remaining_evidence_budget):
        raise ValueError("policy budget differs from the current action catalog")

    if policy is TerminalExplorationPolicyV221.LEGACY:
        return TerminalExplorationDispositionV221.ALLOW
    if (
        decision is ControllerDecisionKindV22.ABSTAIN
        and session_read_dispatches == 0
        and bool(action_catalog.actions)
        and remaining_evidence_budget > 0
        and not policy_redirect_used
    ):
        return TerminalExplorationDispositionV221.PREMATURE_ABSTENTION
    return TerminalExplorationDispositionV221.ALLOW


__all__ = (
    "StudyCombinationV221",
    "TerminalExplorationDispositionV221",
    "TerminalExplorationPolicyV221",
    "evaluate_terminal_exploration_policy_v221",
)
