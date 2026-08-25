"""Frozen v2.3.1 state policy using v2.3.2 total interpretations."""

from __future__ import annotations

from typing import Any

from ecomsre.dta_v2.v22.action_catalog import ActionCatalogV22
from ecomsre.dta_v2.v22.memory import SalientEvidenceMemoryV22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.conflict_model_v232 import assess_conflict_v232
from ecomsre.dta_v2.v23.contracts_v231 import (
    build_competing_hypothesis_set_v231,
    build_competing_report_v231,
    build_single_report_v231,
)
from ecomsre.dta_v2.v23.discovery_router import (
    MAX_DISCOVERY_READS_V23,
    NegativeCoverageLedgerV23,
)
from ecomsre.dta_v2.v23.discovery_runtime_v231 import ConflictAwareDiscoveryStateV231
from ecomsre.dta_v2.v23.discriminating_router_v231 import (
    build_discriminating_plan_v231,
)
from ecomsre.dta_v2.v23.novelty_gate_v231 import (
    NoveltyDispositionV231,
    evaluate_novelty_gate_v231,
)
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


def build_conflict_aware_state_total_v232(
    *,
    graph: ResidualEvidenceGraphV23,
    memory: SalientEvidenceMemoryV22,
    catalog: ActionCatalogV22,
    topology_edges: tuple[tuple[str, str], ...],
    no_incident_admissible: bool,
    negative_coverage: NegativeCoverageLedgerV23,
    discovery_reads_used: int,
    remaining_weighted_budget: float,
    conflict_resolution_read_used: bool,
    normal_resource_services: tuple[str, ...],
    required_source_failures: tuple[EvidenceSourceV22, ...] = (),
    excluded_action_ids: tuple[str, ...] = (),
) -> ConflictAwareDiscoveryStateV231:
    if not 0 <= discovery_reads_used <= MAX_DISCOVERY_READS_V23:
        raise ValueError("v2.3.2 discovery reads are outside the shared bound")
    legal_sources = tuple(
        sorted(
            {
                action.source
                for action in catalog.registry_actions
                if action.weighted_cost <= remaining_weighted_budget
                and action.action_id not in set(excluded_action_ids)
            },
            key=lambda item: item.value,
        )
    )
    remaining_conflict_reads = (
        0
        if conflict_resolution_read_used
        else min(1, MAX_DISCOVERY_READS_V23 - discovery_reads_used)
    )
    assessment = assess_conflict_v232(
        graph=graph,
        memory=memory,
        topology_edges=topology_edges,
        legal_sources=legal_sources,
        remaining_reads=remaining_conflict_reads,
        normal_resource_services=normal_resource_services,
    )
    plan = build_discriminating_plan_v231(
        catalog=catalog,
        graph=graph,
        assessment=assessment,
        negative_coverage=negative_coverage,
        reads_used=discovery_reads_used,
        remaining_weighted_budget=remaining_weighted_budget,
        excluded_action_ids=excluded_action_ids,
    )
    decision = evaluate_novelty_gate_v231(
        graph=graph,
        no_incident_admissible=no_incident_admissible,
        assessment=assessment,
        discriminating_plan=plan,
        required_source_failures=required_source_failures,
    )
    hypotheses = None
    report = None
    if (
        decision.disposition
        is NoveltyDispositionV231.UNREGISTERED_INCIDENT_WITH_COMPETING_HYPOTHESES
    ):
        hypotheses = build_competing_hypothesis_set_v231(
            graph=graph,
            assessment=assessment,
        )
        report = build_competing_report_v231(
            graph=graph,
            assessment=assessment,
            hypothesis_set=hypotheses,
        )
    elif decision.disposition is NoveltyDispositionV231.UNREGISTERED_INCIDENT_SUSPECTED:
        report = build_single_report_v231(
            graph=graph,
            assessment=assessment,
        )
    payload: dict[str, Any] = {
        "schema_version": "dta-v231.conflict-aware-discovery-state.v1",
        "residual_graph": graph,
        "conflict_assessment": assessment,
        "novelty_decision": decision,
        "discriminating_plan": decision.discriminating_plan,
        "competing_hypothesis_set": hypotheses,
        "provisional_report": report,
        "discovery_reads_used": discovery_reads_used,
        "conflict_resolution_read_used": conflict_resolution_read_used,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    draft = ConflictAwareDiscoveryStateV231.model_construct(
        **payload,
        state_sha256="0" * 64,
    )
    return ConflictAwareDiscoveryStateV231.model_validate(
        {
            **payload,
            "state_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"state_sha256"})
            ),
        }
    )


__all__ = ("build_conflict_aware_state_total_v232",)
