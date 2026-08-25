"""Fail-closed novelty classification over residual evidence."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import StrictBool, StrictFloat, model_validator

from ecomsre.dta_v2.v22.memory import SignalStrengthV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


class NoveltyDispositionV23(str, Enum):
    KNOWN_INCIDENT = "KNOWN_INCIDENT"
    NO_INCIDENT = "NO_INCIDENT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    UNREGISTERED_INCIDENT_SUSPECTED = "UNREGISTERED_INCIDENT_SUSPECTED"
    KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY = (
        "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY"
    )


class NoveltyGateDecisionV23(DtaModelV22):
    schema_version: Literal["dta-v23.novelty-gate-decision.v1"]
    disposition: NoveltyDispositionV23
    coverage_ready: StrictBool
    reason_codes: tuple[str, ...]
    residual_support_count: int
    residual_sources: tuple[EvidenceSourceV22, ...]
    explanation_coverage: StrictFloat
    decision_sha256: str

    @model_validator(mode="after")
    def require_decision(self) -> "NoveltyGateDecisionV23":
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("novelty reason codes are not canonical")
        if self.residual_sources != tuple(
            sorted(set(self.residual_sources), key=lambda item: item.value)
        ):
            raise ValueError("novelty residual sources are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"decision_sha256"})
        )
        if self.decision_sha256 != expected:
            raise ValueError("novelty decision digest differs")
        return self


def evaluate_novelty_gate_v23(
    *,
    graph: ResidualEvidenceGraphV23,
    no_incident_admissible: bool,
    remaining_budget_before_discovery: float,
    required_source_failures: tuple[EvidenceSourceV22, ...] = (),
    conflicting_evidence: bool = False,
) -> NoveltyGateDecisionV23:
    if remaining_budget_before_discovery < 0:
        raise ValueError("novelty gate evidence budget cannot be negative")
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
        and remaining_budget_before_discovery > 0
    )
    reasons: list[str] = []
    if graph.known_terminal_candidates:
        if (
            graph.explanation_coverage < 0.70
            and len(residual) >= 2
            and len(sources) >= 2
        ):
            disposition = NoveltyDispositionV23.KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY
            reasons.append("KNOWN_TERMINAL_LEAVES_STRONG_CROSS_SOURCE_RESIDUAL")
        else:
            disposition = NoveltyDispositionV23.KNOWN_INCIDENT
            reasons.append("KNOWN_TERMINAL_ADMISSIBLE")
    elif no_incident_admissible:
        disposition = NoveltyDispositionV23.NO_INCIDENT
        reasons.append("NO_INCIDENT_ADMISSIBLE")
    elif conflicting_evidence:
        disposition = NoveltyDispositionV23.CONFLICTING_EVIDENCE
        reasons.append("STRONG_INTERPRETATIONS_CONFLICT")
    elif not coverage_ready:
        disposition = NoveltyDispositionV23.INSUFFICIENT_EVIDENCE
        if not runtime_ready:
            reasons.append("RUNTIME_COVERAGE_INCOMPLETE")
        if not metrics_ready:
            reasons.append("METRICS_COVERAGE_INCOMPLETE")
        if not discriminating_read:
            reasons.append("NO_DISCRIMINATING_READ")
        if required_source_failures:
            reasons.append("REQUIRED_SOURCE_FAILED")
        if remaining_budget_before_discovery <= 0:
            reasons.append("NO_INITIAL_DISCOVERY_BUDGET")
    else:
        strong = tuple(
            item for item in residual if item.strength is SignalStrengthV22.STRONG
        )
        strong_with_runtime = any(
            item.service in set(graph.healthy_runtime_services) for item in strong
        )
        supported = (
            len(residual) >= 2 and len(sources) >= 2
        ) or (
            bool(strong)
            and strong_with_runtime
            and graph.contrastive_target_present
        )
        if supported:
            disposition = NoveltyDispositionV23.UNREGISTERED_INCIDENT_SUSPECTED
            reasons.append("STRONG_RESIDUAL_WITH_READY_COVERAGE")
        else:
            disposition = NoveltyDispositionV23.INSUFFICIENT_EVIDENCE
            reasons.append("RESIDUAL_SUPPORT_TOO_WEAK")
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.novelty-gate-decision.v1",
        "disposition": disposition,
        "coverage_ready": coverage_ready,
        "reason_codes": tuple(sorted(reasons)),
        "residual_support_count": len(residual),
        "residual_sources": sources,
        "explanation_coverage": graph.explanation_coverage,
    }
    draft = NoveltyGateDecisionV23.model_construct(
        **payload,
        decision_sha256="0" * 64,
    )
    return NoveltyGateDecisionV23.model_validate(
        {
            **payload,
            "decision_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"decision_sha256"})
            ),
        }
    )


__all__ = (
    "NoveltyDispositionV23",
    "NoveltyGateDecisionV23",
    "evaluate_novelty_gate_v23",
)
