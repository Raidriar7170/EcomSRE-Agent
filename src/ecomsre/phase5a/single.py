"""Capability-parity Single-Agent v2 policy adapter."""

from __future__ import annotations

from ecomsre.phase1.contracts import Evidence, EvidenceSource, Incident
from ecomsre.phase5a.contracts import DiagnosisResultV2
from ecomsre.phase5a.judge import judge_diagnosis_v2


SINGLE_V2_SOURCE_ORDER = (
    EvidenceSource.METRICS,
    EvidenceSource.LOGS,
    EvidenceSource.TRACES,
    EvidenceSource.CHANGES,
)


def finalize_single_diagnosis_v2(
    *,
    run_id: str,
    incident: Incident,
    evidence: tuple[Evidence, ...],
) -> DiagnosisResultV2:
    """Use the same unified evidence semantics and final contract as v2 teams."""

    return judge_diagnosis_v2(
        run_id=run_id,
        incident=incident,
        findings=(),
        evidence=evidence,
    ).result
