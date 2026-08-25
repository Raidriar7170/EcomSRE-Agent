"""Typed contracts for provisional, non-actionable DTA v2.3 reports."""

from __future__ import annotations

from enum import Enum
import re
from typing import Any, Literal

from pydantic import Field, StrictFloat, model_validator

from ecomsre.dta_v2.v22.memory import SalientEvidenceMemoryV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    LogicalServiceV22,
    Sha256V22,
    semantic_sha256_v22,
)


class ProvisionalFaultDomainV23(str, Enum):
    CONFIGURATION = "CONFIGURATION"
    RUNTIME = "RUNTIME"
    RESOURCE = "RESOURCE"
    DEPENDENCY = "DEPENDENCY"
    NETWORK = "NETWORK"
    CONCURRENCY = "CONCURRENCY"
    DATA = "DATA"
    EXTERNAL = "EXTERNAL"
    UNKNOWN = "UNKNOWN"


class ProvisionalIncidentReportV23(DtaModelV22):
    """Evidence-backed discovery output with no operational authority."""

    schema_version: Literal["dta-v23.provisional-incident-report.v1"]
    report_id: str = Field(pattern=r"^report-v23-[0-9a-f]{16}$")
    terminal: Literal[
        "UNREGISTERED_INCIDENT_SUSPECTED",
        "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY",
    ]
    suspected_root_services: tuple[LogicalServiceV22, ...] = Field(
        min_length=1,
        max_length=3,
    )
    affected_services: tuple[LogicalServiceV22, ...] = Field(min_length=1, max_length=4)
    broad_fault_domain: ProvisionalFaultDomainV23
    provisional_mechanism_label: str = Field(min_length=1, max_length=96)
    mechanism_description: str = Field(min_length=1, max_length=1000)
    observed_symptoms: tuple[str, ...] = Field(min_length=1, max_length=12)
    supporting_evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=40)
    contradicting_evidence_refs: tuple[str, ...] = Field(max_length=40)
    unexplained_anomaly_ids: tuple[str, ...] = Field(min_length=1, max_length=40)
    alternative_hypotheses: tuple[str, ...] = Field(max_length=6)
    recommended_next_observations: tuple[str, ...] = Field(max_length=6)
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    action_authority: Literal["NONE"]
    report_sha256: Sha256V22

    @model_validator(mode="after")
    def require_report(self) -> "ProvisionalIncidentReportV23":
        for values, label in (
            (self.suspected_root_services, "root services"),
            (self.affected_services, "affected services"),
            (self.supporting_evidence_refs, "supporting refs"),
            (self.contradicting_evidence_refs, "contradicting refs"),
            (self.unexplained_anomaly_ids, "anomaly IDs"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"provisional report {label} are not canonical")
        if not set(self.suspected_root_services).issubset(self.affected_services):
            raise ValueError("provisional roots escape affected services")
        if set(self.supporting_evidence_refs).intersection(
            self.contradicting_evidence_refs
        ):
            raise ValueError("provisional support and contradiction refs overlap")
        forbidden_text = " ".join(
            (
                self.provisional_mechanism_label,
                self.mechanism_description,
                *self.recommended_next_observations,
            )
        )
        if re.search(r"(?:\brunbook:|https?://|\bsudo\b|\bsh\s+-c\b)", forbidden_text, re.I):
            raise ValueError("provisional report contains an executable authority hint")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("provisional report digest differs")
        return self


def build_provisional_report_v23(
    *,
    terminal: Literal[
        "UNREGISTERED_INCIDENT_SUSPECTED",
        "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY",
    ],
    candidate_services: tuple[str, ...],
    suspected_root_services: tuple[str, ...],
    affected_services: tuple[str, ...],
    broad_fault_domain: ProvisionalFaultDomainV23,
    provisional_mechanism_label: str,
    mechanism_description: str,
    observed_symptoms: tuple[str, ...],
    supporting_evidence_refs: tuple[str, ...],
    contradicting_evidence_refs: tuple[str, ...],
    unexplained_anomaly_ids: tuple[str, ...],
    alternative_hypotheses: tuple[str, ...],
    recommended_next_observations: tuple[str, ...],
    confidence: float,
    memory: SalientEvidenceMemoryV22,
    residual_anomaly_refs: dict[str, tuple[str, ...]],
) -> ProvisionalIncidentReportV23:
    candidates = set(candidate_services)
    roots = tuple(sorted(set(suspected_root_services)))
    affected = tuple(sorted(set(affected_services)))
    if not set(roots).issubset(candidates) or not set(affected).issubset(candidates):
        raise ValueError("provisional report services escape current candidates")
    known_refs = {item.evidence_ref for item in memory.evidence_refs}
    support = tuple(sorted(set(supporting_evidence_refs)))
    contradict = tuple(sorted(set(contradicting_evidence_refs)))
    if not set((*support, *contradict)).issubset(known_refs):
        raise ValueError("provisional report cites an unknown evidence ref")
    unexplained = tuple(sorted(set(unexplained_anomaly_ids)))
    if not set(unexplained).issubset(residual_anomaly_refs):
        raise ValueError("provisional report cites a non-residual anomaly")
    residual_refs = {
        ref for anomaly_id in unexplained for ref in residual_anomaly_refs[anomaly_id]
    }
    if not set(support).intersection(residual_refs):
        raise ValueError("provisional report does not cite residual evidence")
    identity: dict[str, Any] = {
        "terminal": terminal,
        "suspected_root_services": roots,
        "affected_services": affected,
        "broad_fault_domain": broad_fault_domain,
        "provisional_mechanism_label": provisional_mechanism_label.strip(),
        "mechanism_description": mechanism_description.strip(),
        "observed_symptoms": observed_symptoms,
        "supporting_evidence_refs": support,
        "contradicting_evidence_refs": contradict,
        "unexplained_anomaly_ids": unexplained,
        "alternative_hypotheses": alternative_hypotheses,
        "recommended_next_observations": recommended_next_observations,
        "confidence": float(confidence),
        "action_authority": "NONE",
    }
    report_id = f"report-v23-{semantic_sha256_v22(identity)[:16]}"
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.provisional-incident-report.v1",
        "report_id": report_id,
        **identity,
    }
    draft = ProvisionalIncidentReportV23.model_construct(
        **payload,
        report_sha256="0" * 64,
    )
    return ProvisionalIncidentReportV23.model_validate(
        {
            **payload,
            "report_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )


__all__ = (
    "ProvisionalFaultDomainV23",
    "ProvisionalIncidentReportV23",
    "build_provisional_report_v23",
)
