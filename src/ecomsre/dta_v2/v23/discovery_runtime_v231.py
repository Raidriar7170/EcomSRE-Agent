"""Conflict-aware state transition for the DTA v2.3.1 discovery lane."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import StrictBool, StrictInt, model_validator

from ecomsre.dta_v2.v22.action_catalog import ActionCatalogV22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, EvidenceSourceV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.conflict_model_v231 import ConflictAssessmentV231, assess_conflict_v231
from ecomsre.dta_v2.v23.contracts_v231 import (
    CompetingHypothesisSetV231,
    ProvisionalIncidentReportV231,
    build_competing_hypothesis_set_v231,
    build_competing_report_v231,
    build_single_report_v231,
)
from ecomsre.dta_v2.v23.discovery_router import MAX_DISCOVERY_READS_V23, NegativeCoverageLedgerV23
from ecomsre.dta_v2.v23.discriminating_router_v231 import (
    DiscriminatingPlanV231,
    build_discriminating_plan_v231,
)
from ecomsre.dta_v2.v23.novelty_gate_v231 import (
    NoveltyDispositionV231,
    NoveltyGateDecisionV231,
    evaluate_novelty_gate_v231,
)
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


class ConflictAwareDiscoveryStateV231(DtaModelV22):
    schema_version: Literal["dta-v231.conflict-aware-discovery-state.v1"]
    residual_graph: ResidualEvidenceGraphV23
    conflict_assessment: ConflictAssessmentV231
    novelty_decision: NoveltyGateDecisionV231
    discriminating_plan: DiscriminatingPlanV231 | None
    competing_hypothesis_set: CompetingHypothesisSetV231 | None
    provisional_report: ProvisionalIncidentReportV231 | None
    discovery_reads_used: StrictInt = 0
    conflict_resolution_read_used: StrictBool
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    state_sha256: str

    @model_validator(mode="after")
    def require_state(self) -> "ConflictAwareDiscoveryStateV231":
        if not 0 <= self.discovery_reads_used <= MAX_DISCOVERY_READS_V23:
            raise ValueError("v2.3.1 state exceeds the shared discovery cap")
        if self.conflict_resolution_read_used and self.discovery_reads_used < 1:
            raise ValueError("v2.3.1 conflict read is not charged to discovery")
        competing_reportable = (
            self.novelty_decision.disposition
            is NoveltyDispositionV231.UNREGISTERED_INCIDENT_WITH_COMPETING_HYPOTHESES
        )
        if competing_reportable != (self.competing_hypothesis_set is not None):
            raise ValueError("v2.3.1 competing state differs from its report")
        reportable = self.novelty_decision.disposition in {
            NoveltyDispositionV231.UNREGISTERED_INCIDENT_SUSPECTED,
            NoveltyDispositionV231.UNREGISTERED_INCIDENT_WITH_COMPETING_HYPOTHESES,
            NoveltyDispositionV231.KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY,
        }
        if reportable != (self.provisional_report is not None):
            raise ValueError("v2.3.1 report differs from its disposition")
        if self.discriminating_plan != self.novelty_decision.discriminating_plan:
            raise ValueError("v2.3.1 state plan differs from the Novelty Gate")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"state_sha256"})
        )
        if self.state_sha256 != expected:
            raise ValueError("v2.3.1 discovery state digest differs")
        return self


def build_conflict_aware_state_v231(
    *,
    graph: ResidualEvidenceGraphV23,
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
        raise ValueError("v2.3.1 discovery reads are outside the shared bound")
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
    assessment = assess_conflict_v231(
        graph=graph,
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
    elif (
        decision.disposition
        is NoveltyDispositionV231.UNREGISTERED_INCIDENT_SUSPECTED
    ):
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


__all__ = (
    "ConflictAwareDiscoveryStateV231",
    "build_conflict_aware_state_v231",
)
