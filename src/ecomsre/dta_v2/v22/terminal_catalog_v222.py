"""Runtime-admissible terminal candidates for the short v2.2.2 protocol."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import StrictBool, model_validator

from ecomsre.dta_v2.v22.controller_contracts import (
    ABSTAIN_HYPOTHESIS_ID_V22,
    NO_INCIDENT_HYPOTHESIS_ID_V22,
    HypothesisCatalogV22,
)
from ecomsre.dta_v2.v22.effective_policy_v222 import (
    EffectiveSupportPolicyV222,
    evaluate_effective_support_v222,
    evaluate_replay_no_incident_coverage_v222,
)
from ecomsre.dta_v2.v22.gap_graph_v222 import GapGraphV222
from ecomsre.dta_v2.v22.gap_router_v222 import GapRoutingResultV222
from ecomsre.dta_v2.v22.memory import SalientEvidenceMemoryV22
from ecomsre.dta_v2.v22.predicates import MechanismV22, evaluate_no_incident_v22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22


class TerminalKindV222(str, Enum):
    DIAGNOSED = "DIAGNOSED"
    NO_INCIDENT = "NO_INCIDENT"
    ABSTAIN = "ABSTAIN"


class TerminalCandidateV222(DtaModelV22):
    terminal_alias: str
    terminal_id: str
    terminal_kind: TerminalKindV222
    hypothesis_id: str
    root_service: str | None
    mechanism: MechanismV22 | None
    matched_clause_id: str | None
    supporting_evidence_refs: tuple[str, ...]


class TerminalCatalogV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.terminal-catalog.v1"]
    policy_sha256: str
    memory_sha256: str
    candidates: tuple[TerminalCandidateV222, ...]
    early_abstain_exposed: StrictBool
    catalog_sha256: str

    @model_validator(mode="after")
    def require_catalog(self) -> "TerminalCatalogV222":
        aliases = tuple(item.terminal_alias for item in self.candidates)
        expected_aliases = tuple(f"T{index:02d}" for index in range(len(aliases)))
        if aliases != expected_aliases:
            raise ValueError("terminal aliases are not contiguous")
        ids = tuple(item.terminal_id for item in self.candidates)
        if len(ids) != len(set(ids)):
            raise ValueError("terminal candidates are not unique")
        abstain = any(
            item.terminal_kind is TerminalKindV222.ABSTAIN
            for item in self.candidates
        )
        if self.early_abstain_exposed != abstain:
            raise ValueError("terminal catalog abstain accounting differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"catalog_sha256"})
        )
        if self.catalog_sha256 != expected:
            raise ValueError("terminal catalog digest differs")
        return self


def _parent(
    *, target: str, mechanism: MechanismV22, edges: tuple[tuple[str, str], ...]
) -> str | None:
    if mechanism is not MechanismV22.DEPENDENCY_LATENCY:
        return None
    return next(
        (right if left == target else left for left, right in edges if target in {left, right}),
        None,
    )


def build_terminal_catalog_v222(
    *,
    policy: EffectiveSupportPolicyV222,
    hypothesis_catalog: HypothesisCatalogV22,
    memory: SalientEvidenceMemoryV22,
    gap_graph: GapGraphV222,
    routed_actions: GapRoutingResultV222,
    candidate_services: tuple[str, ...],
    topology_edges: tuple[tuple[str, str], ...],
    budget_exhausted: bool,
    required_source_unavailable: bool,
    conflicting_evidence: bool,
) -> TerminalCatalogV222:
    raw: list[dict[str, object]] = []
    for entry in hypothesis_catalog.hypotheses:
        if entry.target_service is None or entry.mechanism in {
            MechanismV22.NO_INCIDENT,
            MechanismV22.UNKNOWN,
        }:
            continue
        parent = _parent(
            target=entry.target_service,
            mechanism=entry.mechanism,
            edges=topology_edges,
        )
        decision = evaluate_effective_support_v222(
            policy=policy,
            mechanism=entry.mechanism,
            target_service=entry.target_service,
            parent_service=parent,
            predicates=memory.predicates,
        )
        if decision.accepted:
            raw.append(
                {
                    "terminal_id": f"terminal:diagnosed:{entry.hypothesis_id}",
                    "terminal_kind": TerminalKindV222.DIAGNOSED,
                    "hypothesis_id": entry.hypothesis_id,
                    "root_service": entry.target_service,
                    "mechanism": entry.mechanism,
                    "matched_clause_id": decision.matched_clause_id,
                    "supporting_evidence_refs": decision.supporting_evidence_refs,
                }
            )
    no_incident = evaluate_no_incident_v22(
        memory=memory,
        candidate_services=candidate_services,
    )
    if no_incident.accepted or evaluate_replay_no_incident_coverage_v222(
        memory=memory,
        candidate_services=candidate_services,
    ):
        raw.append(
            {
                "terminal_id": "terminal:no-incident",
                "terminal_kind": TerminalKindV222.NO_INCIDENT,
                "hypothesis_id": NO_INCIDENT_HYPOTHESIS_ID_V22,
                "root_service": None,
                "mechanism": None,
                "matched_clause_id": None,
                "supporting_evidence_refs": (),
            }
        )
    any_gap_hit = any(
        item.active_hypotheses_reduced > 0 for item in routed_actions.ranking
    )
    abstain_allowed = (
        budget_exhausted
        or not any_gap_hit
        or required_source_unavailable
        or conflicting_evidence
    )
    if abstain_allowed and not raw:
        raw.append(
            {
                "terminal_id": "terminal:abstain",
                "terminal_kind": TerminalKindV222.ABSTAIN,
                "hypothesis_id": ABSTAIN_HYPOTHESIS_ID_V22,
                "root_service": None,
                "mechanism": None,
                "matched_clause_id": None,
                "supporting_evidence_refs": (),
            }
        )
    ordered = tuple(
        sorted(
            raw,
            key=lambda item: (
                str(item["terminal_kind"]),
                str(item["terminal_id"]),
            ),
        )
    )
    candidates = tuple(
        TerminalCandidateV222.model_validate(
            {"terminal_alias": f"T{index:02d}", **item}
        )
        for index, item in enumerate(ordered)
    )
    early_abstain = any(
        item.terminal_kind is TerminalKindV222.ABSTAIN for item in candidates
    )
    digest_payload = {
        "schema_version": "dta-v22.2.terminal-catalog.v1",
        "policy_sha256": policy.policy_sha256,
        "memory_sha256": memory.memory_sha256,
        "candidates": tuple(item.model_dump(mode="json") for item in candidates),
        "early_abstain_exposed": early_abstain,
    }
    return TerminalCatalogV222(
        schema_version="dta-v22.2.terminal-catalog.v1",
        policy_sha256=policy.policy_sha256,
        memory_sha256=memory.memory_sha256,
        candidates=candidates,
        early_abstain_exposed=early_abstain,
        catalog_sha256=semantic_sha256_v22(digest_payload),
    )


__all__ = (
    "TerminalCandidateV222",
    "TerminalCatalogV222",
    "TerminalKindV222",
    "build_terminal_catalog_v222",
)
