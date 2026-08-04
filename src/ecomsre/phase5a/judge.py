"""Deterministic Judge v2 over current-run evidence and typed findings."""

from __future__ import annotations

from dataclasses import dataclass

from ecomsre.phase1.contracts import (
    Evidence,
    EvidenceSource,
    Incident,
    RecommendedNextAction,
)
from ecomsre.phase5a.contracts import (
    DiagnosisDecisionV2,
    DiagnosisResultV2,
    SpecialistFindingV2,
)
from ecomsre.phase5a.semantics import (
    classify_evidence_candidate,
    evidence_contradicts_candidate,
    evidence_supports_candidate,
    is_anomalous_service_signal,
    is_normal_sli_signal,
)


@dataclass(frozen=True, slots=True)
class JudgeAssessmentV2:
    result: DiagnosisResultV2


def _need_more(
    run_id: str,
    incident: Incident,
    *,
    supporting: tuple[Evidence, ...],
    contradicting: tuple[Evidence, ...] = (),
    gap: str,
) -> DiagnosisResultV2:
    return DiagnosisResultV2(
        schema_version="phase5a.diagnosis-result.v2",
        run_id=run_id,
        decision=DiagnosisDecisionV2.NEED_MORE_EVIDENCE,
        root_service=None,
        fault_mechanism=None,
        causal_chain=(),
        affected_sli=incident.affected_sli,
        supporting_evidence=tuple(item.evidence_ref for item in supporting),
        contradicting_evidence=tuple(
            item.evidence_ref for item in contradicting
            if item.evidence_ref not in {entry.evidence_ref for entry in supporting}
        ),
        missing_evidence=(gap,),
        confidence=0.35,
        decision_rationale=(
            "Additional evidence is required because one root and mechanism "
            "are not uniquely supported by the current observations."
        ),
        recommended_next_action=(
            RecommendedNextAction.COLLECT_ADDITIONAL_READ_ONLY_TELEMETRY_EVIDENCE
        ),
    )


def _abstain(
    *,
    run_id: str,
    contradicting: tuple[Evidence, ...],
) -> DiagnosisResultV2:
    return DiagnosisResultV2(
        schema_version="phase5a.diagnosis-result.v2",
        run_id=run_id,
        decision=DiagnosisDecisionV2.ABSTAIN,
        root_service=None,
        fault_mechanism=None,
        causal_chain=(),
        affected_sli=None,
        supporting_evidence=(),
        contradicting_evidence=tuple(
            item.evidence_ref for item in contradicting
        ),
        missing_evidence=(),
        confidence=0.0,
        decision_rationale=(
            "There is no confirmed incident in the bounded current observations."
        ),
        recommended_next_action=(
            RecommendedNextAction.CONTINUE_MONITORING_AFFECTED_SLI
        ),
    )


def _is_business_sli(item: Evidence) -> bool:
    return (
        item.source is EvidenceSource.METRICS
        and item.observation_type in {"search_sli", "recommendation_sli"}
    )


def judge_diagnosis_v2(
    *,
    run_id: str,
    incident: Incident,
    findings: tuple[SpecialistFindingV2, ...],
    evidence: tuple[Evidence, ...],
) -> JudgeAssessmentV2:
    """Return a typed final decision without case, fixture, or truth access."""

    ordered = tuple(sorted(evidence, key=lambda item: item.evidence_ref))
    run_ids = {item.run_id for item in ordered}
    if any(item_run_id != run_id for item_run_id in run_ids):
        raise ValueError("Judge evidence crosses run boundaries")
    if any(finding.run_id != run_id for finding in findings):
        raise ValueError("Judge finding crosses the current evidence run")

    anomaly_signals = tuple(
        item for item in ordered if is_anomalous_service_signal(item)
    )
    normal_signals = tuple(item for item in ordered if is_normal_sli_signal(item))
    if not anomaly_signals:
        return JudgeAssessmentV2(
            result=_abstain(run_id=run_id, contradicting=normal_signals)
        )

    root_metrics = tuple(
        item for item in anomaly_signals if not _is_business_sli(item)
    )
    if not root_metrics:
        return JudgeAssessmentV2(
            result=_need_more(
                run_id,
                incident,
                supporting=anomaly_signals,
                gap="One typed anomalous root-service metric is required.",
            )
        )
    root_services = {item.service for item in root_metrics}
    if len(root_services) != 1:
        return JudgeAssessmentV2(
            result=_need_more(
                run_id,
                incident,
                supporting=root_metrics,
                gap="Multiple anomalous root services remain ambiguous.",
            )
        )
    root_service = next(iter(root_services))
    mechanisms = {
        classification[1]
        for item in ordered
        if (classification := classify_evidence_candidate(item)) is not None
        and classification[0] == root_service
    }
    if len(mechanisms) != 1:
        return JudgeAssessmentV2(
            result=_need_more(
                run_id,
                incident,
                supporting=root_metrics,
                gap="A single evidence-supported fault mechanism is required.",
            )
        )
    mechanism = next(iter(mechanisms))
    supporting = tuple(
        item
        for item in ordered
        if evidence_supports_candidate(
            item,
            root_service=root_service,
            fault_mechanism=mechanism,
        )
    )
    contradicting = tuple(
        item
        for item in ordered
        if item.evidence_ref not in {entry.evidence_ref for entry in supporting}
        and evidence_contradicts_candidate(
            item,
            root_service=root_service,
            fault_mechanism=mechanism,
        )
    )
    if contradicting:
        return JudgeAssessmentV2(
            result=_need_more(
                run_id,
                incident,
                supporting=supporting,
                contradicting=contradicting,
                gap="A critical contradiction for the root mechanism is unresolved.",
            )
        )
    sources = {item.source for item in supporting}
    if len(sources) < 2:
        return JudgeAssessmentV2(
            result=_need_more(
                run_id,
                incident,
                supporting=supporting or root_metrics,
                gap="A complementary evidence source for the mechanism is required.",
            )
        )
    result = DiagnosisResultV2(
        schema_version="phase5a.diagnosis-result.v2",
        run_id=run_id,
        decision=DiagnosisDecisionV2.RCA_CONFIRMED,
        root_service=root_service,
        fault_mechanism=mechanism,
        causal_chain=(
            f"{root_service} emitted an anomalous bounded service signal.",
            (
                "Independent current-run observations support "
                f"{mechanism.value} for the affected SLI."
            ),
        ),
        affected_sli=incident.affected_sli,
        supporting_evidence=tuple(item.evidence_ref for item in supporting),
        contradicting_evidence=(),
        missing_evidence=(),
        confidence=0.9,
        decision_rationale=(
            "Two complementary current-run evidence sources confirm one "
            "mechanism for one anomalous root service."
        ),
        recommended_next_action=(
            RecommendedNextAction.REVIEW_BOUNDED_REPLAY_EVIDENCE
        ),
    )
    return JudgeAssessmentV2(result=result)
