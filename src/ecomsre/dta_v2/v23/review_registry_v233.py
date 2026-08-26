"""Read-only review and Shadow projection adapters for DTA v2.3.3."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.contracts_v231 import ReportUncertaintyModeV231
from ecomsre.dta_v2.v23.contracts_v233 import ProvisionalIncidentReportV233
from ecomsre.dta_v2.v23.domain_projection_v233 import DomainScoreV233


class ShadowFaultProjectionV233(DtaModelV22):
    schema_version: Literal["dta-v233.shadow-fault-projection.v1"]
    shadow_fault_id: str = Field(pattern=r"^shadow-v233-[0-9a-f]{16}$")
    status: Literal["SHADOW"]
    canonical_label: str
    runtime_selected_domain: ProvisionalFaultDomainV23
    domain_candidate_scores: tuple[DomainScoreV233, ...]
    guard_witness_ids: tuple[str, ...]
    uncertainty_mode: ReportUncertaintyModeV231
    positive_report_ids: tuple[str, ...]
    review_record_id: str
    remediation_authority: Literal["NONE"]
    entry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_projection(self) -> "ShadowFaultProjectionV233":
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", self.canonical_label):
            raise ValueError("v2.3.3 shadow label is not a lowercase slug")
        for values, label in (
            (self.guard_witness_ids, "witness IDs"),
            (self.positive_report_ids, "report IDs"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"v2.3.3 shadow {label} are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"entry_sha256"})
        )
        if self.entry_sha256 != expected:
            raise ValueError("v2.3.3 shadow projection digest differs")
        return self


def render_review_display_v233(
    report: ProvisionalIncidentReportV233,
) -> dict[str, object]:
    by_id = {item.hypothesis_id: item for item in report.runtime_hypotheses}
    preferred = by_id[report.preferred_hypothesis_id]
    alternatives = tuple(
        by_id[item]
        for item in report.alternative_hypothesis_ids
        if item in by_id
    )
    return {
        "report_id": report.report_id,
        "runtime_selected_root": report.runtime_selected_root_service,
        "runtime_selected_broad_domain": report.broad_fault_domain.value,
        "domain_score_table": [
            item.model_dump(mode="json") for item in report.domain_candidate_scores
        ],
        "supporting_evidence": list(report.supporting_evidence_refs),
        "contradicting_evidence": list(report.contradicting_evidence_refs),
        "contradiction_witness_ids": list(report.contradiction_witness_ids),
        "guard_disposition": report.guard_disposition.value,
        "leading_hypothesis": preferred.model_dump(mode="json"),
        "leading_mechanism_narrative": report.mechanism_description,
        "alternatives": [item.model_dump(mode="json") for item in alternatives],
        "unresolved_questions": list(report.unresolved_questions),
        "review_recommendation": report.review_recommendation.value,
        "confidence_band": report.confidence_band.value,
        "action_authority": "NONE",
    }


def build_shadow_projection_v233(
    *,
    report: ProvisionalIncidentReportV233,
    canonical_label: str,
    review_record_id: str,
) -> ShadowFaultProjectionV233:
    label = canonical_label.strip()
    identity: dict[str, Any] = {
        "canonical_label": label,
        "report_id": report.report_id,
        "review_record_id": review_record_id,
    }
    payload: dict[str, Any] = {
        "schema_version": "dta-v233.shadow-fault-projection.v1",
        "shadow_fault_id": f"shadow-v233-{semantic_sha256_v22(identity)[:16]}",
        "status": "SHADOW",
        "canonical_label": label,
        "runtime_selected_domain": report.broad_fault_domain,
        "domain_candidate_scores": report.domain_candidate_scores,
        "guard_witness_ids": report.contradiction_witness_ids,
        "uncertainty_mode": report.uncertainty_mode,
        "positive_report_ids": (report.report_id,),
        "review_record_id": review_record_id,
        "remediation_authority": "NONE",
    }
    draft = ShadowFaultProjectionV233.model_construct(
        **payload,
        entry_sha256="0" * 64,
    )
    return ShadowFaultProjectionV233.model_validate(
        {
            **payload,
            "entry_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"entry_sha256"})
            ),
        }
    )


__all__ = (
    "ShadowFaultProjectionV233",
    "build_shadow_projection_v233",
    "render_review_display_v233",
)
