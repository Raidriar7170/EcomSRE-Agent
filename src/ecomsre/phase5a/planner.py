"""Bounded evidence-driven staged planning for Dynamic v2."""

from __future__ import annotations

from dataclasses import dataclass

from ecomsre.phase1.contracts import EvidenceSource
from ecomsre.phase5a.contracts import (
    DiagnosisDecisionV2,
    DiagnosisResultV2,
    SpecialistFindingV2,
    UnifiedMechanismV2,
)


STAGE1_SOURCES = (EvidenceSource.METRICS,)


@dataclass(frozen=True, slots=True)
class TargetedRefinementV2:
    source: EvidenceSource
    target_candidate_id: str
    missing_question: str


def select_stage2_sources(
    metrics_finding: SpecialistFindingV2,
) -> tuple[EvidenceSource, ...]:
    """Select at most two follow-up sources from the Metrics finding only."""

    if metrics_finding.source is not EvidenceSource.METRICS:
        raise ValueError("stage two planning requires the Metrics finding")
    if not metrics_finding.candidates and not metrics_finding.missing_evidence:
        return ()
    return (EvidenceSource.LOGS, EvidenceSource.TRACES)


def select_targeted_refinement(
    *,
    result: DiagnosisResultV2,
    findings: tuple[SpecialistFindingV2, ...],
    investigated_sources: tuple[EvidenceSource, ...],
) -> TargetedRefinementV2 | None:
    """Select no more than one source for one explicit missing question."""

    if result.decision is not DiagnosisDecisionV2.NEED_MORE_EVIDENCE:
        return None
    candidates = tuple(
        candidate for finding in findings for candidate in finding.candidates
    )
    identities = {
        (candidate.root_service, candidate.fault_mechanism)
        for candidate in candidates
    }
    if len(identities) != 1:
        return None
    target = min(candidates, key=lambda item: item.candidate_id)
    investigated = set(investigated_sources)
    if target.fault_mechanism in {
        UnifiedMechanismV2.RUNTIME_CONFIGURATION_FAILURE,
        UnifiedMechanismV2.RANKING_CONFIGURATION_FAILURE,
    } and EvidenceSource.CHANGES not in investigated:
        return TargetedRefinementV2(
            source=EvidenceSource.CHANGES,
            target_candidate_id=target.candidate_id,
            missing_question=(
                "Which matching configuration change corroborates this candidate?"
            ),
        )
    for source in (EvidenceSource.TRACES, EvidenceSource.LOGS):
        if source not in investigated:
            return TargetedRefinementV2(
                source=source,
                target_candidate_id=target.candidate_id,
                missing_question=(
                    f"Which {source.value} observation corroborates this candidate?"
                ),
            )
    return None
