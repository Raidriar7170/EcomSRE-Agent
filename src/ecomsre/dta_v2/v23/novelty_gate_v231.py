"""Conflict-aware Novelty Gate for DTA v2.3.1."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import StrictBool, StrictFloat, model_validator

from ecomsre.dta_v2.v22.memory import SignalStrengthV22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, EvidenceSourceV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.conflict_model_v231 import ConflictAssessmentV231, ConflictTypeV231
from ecomsre.dta_v2.v23.discriminating_router_v231 import DiscriminatingPlanV231
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


class NoveltyDispositionV231(str, Enum):
    KNOWN_INCIDENT = "KNOWN_INCIDENT"
    NO_INCIDENT = "NO_INCIDENT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    DISCOVERY_READ_REQUIRED = "DISCOVERY_READ_REQUIRED"
    UNREGISTERED_INCIDENT_SUSPECTED = "UNREGISTERED_INCIDENT_SUSPECTED"
    UNREGISTERED_INCIDENT_WITH_COMPETING_HYPOTHESES = (
        "UNREGISTERED_INCIDENT_WITH_COMPETING_HYPOTHESES"
    )
    KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY = "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY"


class NoveltyGateDecisionV231(DtaModelV22):
    schema_version: Literal["dta-v231.novelty-gate-decision.v1"]
    disposition: NoveltyDispositionV231
    coverage_ready: StrictBool
    residual_support_sufficient: StrictBool
    reason_codes: tuple[str, ...]
    residual_support_count: int
    residual_sources: tuple[EvidenceSourceV22, ...]
    explanation_coverage: StrictFloat
    conflict_assessment: ConflictAssessmentV231
    discriminating_plan: DiscriminatingPlanV231 | None
    decision_sha256: str

    @model_validator(mode="after")
    def require_decision(self) -> "NoveltyGateDecisionV231":
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("v2.3.1 novelty reasons are not canonical")
        if self.residual_sources != tuple(
            sorted(set(self.residual_sources), key=lambda item: item.value)
        ):
            raise ValueError("v2.3.1 residual sources are not canonical")
        if (
            self.disposition is NoveltyDispositionV231.DISCOVERY_READ_REQUIRED
        ) != (self.discriminating_plan is not None):
            raise ValueError("v2.3.1 discovery disposition differs from its plan")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"decision_sha256"})
        )
        if self.decision_sha256 != expected:
            raise ValueError("v2.3.1 novelty decision digest differs")
        return self


def evaluate_novelty_gate_v231(
    *,
    graph: ResidualEvidenceGraphV23,
    no_incident_admissible: bool,
    assessment: ConflictAssessmentV231,
    discriminating_plan: DiscriminatingPlanV231 | None,
    required_source_failures: tuple[EvidenceSourceV22, ...] = (),
) -> NoveltyGateDecisionV231:
    residual_ids = set(graph.residual_anomaly_ids)
    residual = tuple(
        item
        for item in graph.generic_anomalies
        if item.anomaly_id in residual_ids
        and item.strength in {SignalStrengthV22.MODERATE, SignalStrengthV22.STRONG}
    )
    sources = tuple(sorted({item.source for item in residual}, key=lambda item: item.value))
    coverage = {item.source: item for item in graph.source_coverage}
    candidates = set(graph.candidate_services)
    runtime_ready = set(coverage[EvidenceSourceV22.RUNTIME].covered_services) == candidates
    metrics_ready = set(coverage[EvidenceSourceV22.METRICS].covered_services) == candidates
    discriminating_read = any(
        coverage[source].queried
        for source in (
            EvidenceSourceV22.LOGS,
            EvidenceSourceV22.TRACES,
            EvidenceSourceV22.RESOURCES,
            EvidenceSourceV22.CHANGES,
        )
    )
    coverage_ready = (
        runtime_ready
        and metrics_ready
        and discriminating_read
        and not required_source_failures
    )
    strong = tuple(item for item in residual if item.strength is SignalStrengthV22.STRONG)
    strong_with_runtime = any(
        item.service in set(graph.healthy_runtime_services) for item in strong
    )
    supported = (len(residual) >= 2 and len(sources) >= 2) or (
        bool(strong) and (strong_with_runtime or graph.contrastive_target_present)
    )
    reasons: list[str] = []
    if graph.known_terminal_candidates:
        disposition = NoveltyDispositionV231.KNOWN_INCIDENT
        reasons.append("KNOWN_TERMINAL_ADMISSIBLE")
    elif no_incident_admissible:
        disposition = NoveltyDispositionV231.NO_INCIDENT
        reasons.append("NO_INCIDENT_ADMISSIBLE")
    elif not coverage_ready:
        disposition = NoveltyDispositionV231.INSUFFICIENT_EVIDENCE
        if not runtime_ready:
            reasons.append("RUNTIME_COVERAGE_INCOMPLETE")
        if not metrics_ready:
            reasons.append("METRICS_COVERAGE_INCOMPLETE")
        if not discriminating_read:
            reasons.append("NO_DISCRIMINATING_READ")
        if required_source_failures:
            reasons.append("REQUIRED_SOURCE_FAILED")
    elif not supported:
        disposition = NoveltyDispositionV231.INSUFFICIENT_EVIDENCE
        reasons.append("RESIDUAL_SUPPORT_TOO_WEAK")
    elif assessment.conflict_type is ConflictTypeV231.IRRECONCILABLE_CONFLICT:
        disposition = NoveltyDispositionV231.CONFLICTING_EVIDENCE
        reasons.append("EXPLICIT_IRRECONCILABLE_CONTRADICTION")
    elif (
        assessment.conflict_type is ConflictTypeV231.RESOLVABLE_CONFLICT
        and discriminating_plan is not None
    ):
        disposition = NoveltyDispositionV231.DISCOVERY_READ_REQUIRED
        reasons.append("DISCRIMINATING_READ_CAN_RESOLVE_COMPETITION")
    elif assessment.conflict_type in {
        ConflictTypeV231.COHERENT_COMPETITION,
        ConflictTypeV231.RESOLVABLE_CONFLICT,
    }:
        if "EXPLICIT_INCOMPATIBLE_OBSERVATIONS" in set(assessment.reason_codes):
            disposition = NoveltyDispositionV231.CONFLICTING_EVIDENCE
            reasons.append("EXPLICIT_CONTRADICTION_HAS_NO_LEGAL_READ")
        else:
            disposition = (
                NoveltyDispositionV231.UNREGISTERED_INCIDENT_WITH_COMPETING_HYPOTHESES
            )
            reasons.append("STRONG_COHERENT_COMPETITION_IS_REPORTABLE")
    else:
        disposition = NoveltyDispositionV231.UNREGISTERED_INCIDENT_SUSPECTED
        reasons.append("STRONG_RESIDUAL_WITH_READY_COVERAGE")
    payload: dict[str, Any] = {
        "schema_version": "dta-v231.novelty-gate-decision.v1",
        "disposition": disposition,
        "coverage_ready": coverage_ready,
        "residual_support_sufficient": supported,
        "reason_codes": tuple(sorted(reasons)),
        "residual_support_count": len(residual),
        "residual_sources": sources,
        "explanation_coverage": graph.explanation_coverage,
        "conflict_assessment": assessment,
        "discriminating_plan": (
            discriminating_plan
            if disposition is NoveltyDispositionV231.DISCOVERY_READ_REQUIRED
            else None
        ),
    }
    draft = NoveltyGateDecisionV231.model_construct(
        **payload,
        decision_sha256="0" * 64,
    )
    return NoveltyGateDecisionV231.model_validate(
        {
            **payload,
            "decision_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"decision_sha256"})
            ),
        }
    )


__all__ = (
    "NoveltyDispositionV231",
    "NoveltyGateDecisionV231",
    "evaluate_novelty_gate_v231",
)
