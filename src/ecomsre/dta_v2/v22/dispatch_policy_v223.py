"""Model Top-4 and deterministic Runtime Top-1 dispatch policies."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import StrictBool, model_validator

from ecomsre.dta_v2.v22.gap_graph_v222 import GapGraphV222
from ecomsre.dta_v2.v22.gap_router_v223 import (
    GapRoutingResultV223,
    action_can_observe_gap_v223,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22


class EvidenceDispatchModeV223(str, Enum):
    MODEL_TOP4 = "MODEL_TOP4"
    RUNTIME_TOP1 = "RUNTIME_TOP1"


class AutomaticDispatchUnavailableV223(RuntimeError):
    pass


class AutomaticDispatchDecisionV223(DtaModelV22):
    schema_version: Literal["dta-v22.3.automatic-dispatch-decision.v1"]
    mode: Literal[EvidenceDispatchModeV223.RUNTIME_TOP1]
    automatic: Literal[True]
    action_id: str
    focus_hypothesis_id: str
    ranking_action_ids: tuple[str, ...]
    terminal_ids_at_dispatch: tuple[()]
    exact_top1_selected: StrictBool
    truth_consulted: Literal[False]
    decision_sha256: str

    @model_validator(mode="after")
    def require_decision(self) -> "AutomaticDispatchDecisionV223":
        if not self.ranking_action_ids or self.action_id != self.ranking_action_ids[0]:
            raise ValueError("automatic dispatch differs from exact ranking[0]")
        if not self.exact_top1_selected:
            raise ValueError("automatic dispatch did not record exact top-1")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"decision_sha256"})
        )
        if self.decision_sha256 != expected:
            raise ValueError("automatic dispatch decision digest differs")
        return self


def _focus_for_action_v223(*, action: object, gap_graph: GapGraphV222) -> str:
    scored: list[tuple[tuple[object, ...], str]] = []
    for hypothesis in gap_graph.hypotheses:
        if hypothesis.complete:
            continue
        completes = 0
        observable: set[tuple[str, str]] = set()
        for clause in hypothesis.clauses:
            if clause.missing_count != hypothesis.minimum_missing_count:
                continue
            hits = tuple(
                gap
                for gap in clause.missing_requirements
                if action_can_observe_gap_v223(action=action, gap=gap)  # type: ignore[arg-type]
            )
            if hits and len(hits) == len(clause.missing_requirements):
                completes += 1
            observable.update(
                (gap.predicate_kind.value, gap.target_service) for gap in hits
            )
        scored.append(
            (
                (-completes, -len(observable), hypothesis.hypothesis_id),
                hypothesis.hypothesis_id,
            )
        )
    if not scored:
        raise AutomaticDispatchUnavailableV223("NO_INCOMPLETE_HYPOTHESIS")
    return min(scored, key=lambda item: item[0])[1]


def automatic_dispatch_v223(
    *,
    mode: EvidenceDispatchModeV223,
    routing: GapRoutingResultV223,
    gap_graph: GapGraphV222,
    terminal_ids: tuple[str, ...],
) -> AutomaticDispatchDecisionV223 | None:
    if mode is EvidenceDispatchModeV223.MODEL_TOP4 or terminal_ids:
        return None
    if not routing.ranking or not routing.actions:
        raise AutomaticDispatchUnavailableV223("NO_RANKED_ACTION_REMAINS")
    top1 = routing.ranking[0]
    executable = {item.action_id for item in routing.actions}
    if top1.action.action_id not in executable:
        raise AutomaticDispatchUnavailableV223("TOP1_ACTION_NOT_EXECUTABLE")
    focus = _focus_for_action_v223(action=top1.action, gap_graph=gap_graph)
    payload = {
        "schema_version": "dta-v22.3.automatic-dispatch-decision.v1",
        "mode": EvidenceDispatchModeV223.RUNTIME_TOP1.value,
        "automatic": True,
        "action_id": top1.action.action_id,
        "focus_hypothesis_id": focus,
        "ranking_action_ids": tuple(item.action.action_id for item in routing.ranking),
        "terminal_ids_at_dispatch": (),
        "exact_top1_selected": True,
        "truth_consulted": False,
    }
    return AutomaticDispatchDecisionV223(
        schema_version="dta-v22.3.automatic-dispatch-decision.v1",
        mode=EvidenceDispatchModeV223.RUNTIME_TOP1,
        automatic=True,
        action_id=top1.action.action_id,
        focus_hypothesis_id=focus,
        ranking_action_ids=tuple(item.action.action_id for item in routing.ranking),
        terminal_ids_at_dispatch=(),
        exact_top1_selected=True,
        truth_consulted=False,
        decision_sha256=semantic_sha256_v22(payload),
    )


__all__ = (
    "AutomaticDispatchDecisionV223",
    "AutomaticDispatchUnavailableV223",
    "EvidenceDispatchModeV223",
    "automatic_dispatch_v223",
)
