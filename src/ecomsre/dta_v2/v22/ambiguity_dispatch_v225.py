"""Runtime-directed per-target and contrastive ambiguity dispatch."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import model_validator

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.ambiguity_set_v225 import EvidenceAmbiguitySetV225
from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    ContrastiveResourceActionV225,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22


class ActionGranularityV225(str, Enum):
    PER_TARGET = "PER_TARGET"
    CONTRASTIVE_BUNDLE = "CONTRASTIVE_BUNDLE"


class AmbiguityDispatchDecisionV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.ambiguity-dispatch-decision.v1"]
    granularity: ActionGranularityV225
    action: EvidenceActionV22 | ContrastiveResourceActionV225
    reason: Literal["UNCOVERED_TARGET", "CONTRASTIVE_BUNDLE"]
    ranking_action_ids: tuple[str, ...]
    automatic: Literal[True]
    truth_consulted: Literal[False]
    decision_sha256: str

    @model_validator(mode="after")
    def require_decision(self) -> "AmbiguityDispatchDecisionV225":
        if (
            self.reason == "CONTRASTIVE_BUNDLE"
            and len(self.action.target_services) <= 1
        ):
            raise ValueError("bundle dispatch selected a single target")
        if self.reason == "UNCOVERED_TARGET" and len(self.action.target_services) != 1:
            raise ValueError("per-target dispatch selected multiple targets")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"decision_sha256"})
        )
        if self.decision_sha256 != expected:
            raise ValueError("ambiguity dispatch decision digest differs")
        return self


def _decision(
    *,
    granularity: ActionGranularityV225,
    action: EvidenceActionV22 | ContrastiveResourceActionV225,
    reason: Literal["UNCOVERED_TARGET", "CONTRASTIVE_BUNDLE"],
    ranked_action_ids: tuple[str, ...],
) -> AmbiguityDispatchDecisionV225:
    payload = {
        "schema_version": "dta-v22.5.ambiguity-dispatch-decision.v1",
        "granularity": granularity,
        "action": action.model_dump(mode="json"),
        "reason": reason,
        "ranking_action_ids": ranked_action_ids,
        "automatic": True,
        "truth_consulted": False,
    }
    return AmbiguityDispatchDecisionV225(
        schema_version="dta-v22.5.ambiguity-dispatch-decision.v1",
        granularity=granularity,
        action=action,
        reason=reason,
        ranking_action_ids=ranked_action_ids,
        automatic=True,
        truth_consulted=False,
        decision_sha256=semantic_sha256_v22(payload),
    )


def dispatch_ambiguity_action_v225(
    *,
    granularity: ActionGranularityV225,
    ambiguity_set: EvidenceAmbiguitySetV225,
    individual_actions: tuple[EvidenceActionV22, ...],
    bundle_action: ContrastiveResourceActionV225 | None,
    ranked_action_ids: tuple[str, ...],
    terminal_ids: tuple[str, ...],
    remaining_evidence_budget: float,
) -> AmbiguityDispatchDecisionV225 | None:
    if remaining_evidence_budget < 0:
        raise ValueError("remaining evidence budget cannot be negative")
    if terminal_ids or ambiguity_set.complete:
        return None
    if (
        granularity is ActionGranularityV225.CONTRASTIVE_BUNDLE
        and bundle_action is not None
        and bundle_action.action_id == ambiguity_set.bundle_action_id
        and bundle_action.weighted_cost <= remaining_evidence_budget
    ):
        return _decision(
            granularity=granularity,
            action=bundle_action,
            reason="CONTRASTIVE_BUNDLE",
            ranked_action_ids=ranked_action_ids,
        )
    rank = {action_id: index for index, action_id in enumerate(ranked_action_ids)}
    remaining = set(ambiguity_set.remaining_target_services)
    candidates = tuple(
        item
        for item in individual_actions
        if item.action_id in set(ambiguity_set.individual_action_ids)
        and len(item.target_services) == 1
        and item.target_services[0] in remaining
        and item.weighted_cost <= remaining_evidence_budget
    )
    if not candidates:
        return None
    selected = min(
        candidates,
        key=lambda item: (rank.get(item.action_id, len(rank)), item.action_id),
    )
    return _decision(
        granularity=granularity,
        action=selected,
        reason="UNCOVERED_TARGET",
        ranked_action_ids=ranked_action_ids,
    )


__all__ = (
    "ActionGranularityV225",
    "AmbiguityDispatchDecisionV225",
    "dispatch_ambiguity_action_v225",
)
