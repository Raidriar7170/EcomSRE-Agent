"""Runtime-owned synthesis and provisional report contracts for DTA v2.3.3."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, StrictFloat, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.contradiction_witness_v233 import (
    ContradictionWitnessV233,
)
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.contracts_v231 import (
    ConfidenceBandV231,
    ReportUncertaintyModeV231,
    ReviewRecommendationV231,
)
from ecomsre.dta_v2.v23.domain_projection_v233 import (
    DomainProjectionStatusV233,
    DomainProjectionV233,
    DomainScoreV233,
)
from ecomsre.dta_v2.v23.irreconcilable_guard_v233 import (
    IrreconcilableGuardDecisionV233,
    IrreconcilableGuardDispositionV233,
)
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


class RuntimeHypothesisV233(DtaModelV22):
    schema_version: Literal["dta-v233.runtime-hypothesis.v1"]
    hypothesis_id: str = Field(pattern=r"^ch-v233-[0-9a-f]{16}$")
    provisional_label: str = Field(min_length=1, max_length=96)
    runtime_selected_root_service: str
    candidate_domain: ProvisionalFaultDomainV23
    supporting_anomaly_ids: tuple[str, ...] = Field(min_length=1)
    supporting_evidence_refs: tuple[str, ...] = Field(min_length=1)
    contradicting_evidence_refs: tuple[str, ...]
    unresolved_questions: tuple[str, ...] = Field(min_length=1, max_length=8)
    relative_support_score: StrictFloat
    hypothesis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_hypothesis(self) -> "RuntimeHypothesisV233":
        for values, label in (
            (self.supporting_anomaly_ids, "anomaly IDs"),
            (self.supporting_evidence_refs, "support refs"),
            (self.contradicting_evidence_refs, "contradiction refs"),
            (self.unresolved_questions, "questions"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"v2.3.3 hypothesis {label} are not canonical")
        if set(self.supporting_evidence_refs).intersection(
            self.contradicting_evidence_refs
        ):
            raise ValueError("v2.3.3 hypothesis support and contradiction overlap")
        identity = self.model_dump(
            mode="json",
            exclude={"hypothesis_id", "hypothesis_sha256"},
        )
        if self.hypothesis_id != (
            f"ch-v233-{semantic_sha256_v22(identity)[:16]}"
        ):
            raise ValueError("v2.3.3 hypothesis identity differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"hypothesis_sha256"})
        )
        if self.hypothesis_sha256 != expected:
            raise ValueError("v2.3.3 hypothesis digest differs")
        return self


class ResidualAnomalySummaryV233(DtaModelV22):
    anomaly_id: str
    kind: str
    source: str
    service: str
    related_services: tuple[str, ...]
    summary: str
    evidence_refs: tuple[str, ...]


class DiscoverySynthesisRequestV233(DtaModelV22):
    schema_version: Literal["dta-v233.discovery-synthesis-request.v1"]
    runtime_selected_root_service: str
    runtime_domain_projection: DomainProjectionV233
    competing_hypotheses: tuple[RuntimeHypothesisV233, ...] = Field(
        min_length=1,
        max_length=4,
    )
    residual_anomaly_summaries: tuple[ResidualAnomalySummaryV233, ...] = Field(
        min_length=1
    )
    contradiction_witness_summary: tuple[ContradictionWitnessV233, ...]
    guard_decision: IrreconcilableGuardDecisionV233
    unresolved_dimensions: tuple[str, ...]
    top_shadow_matches: tuple[dict[str, Any], ...] = Field(max_length=3)
    validation_graph: ResidualEvidenceGraphV23 = Field(exclude=True, repr=False)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_request(self) -> "DiscoverySynthesisRequestV233":
        if self.runtime_domain_projection.selected_root_service != (
            self.runtime_selected_root_service
        ):
            raise ValueError("v2.3.3 synthesis root binding differs")
        if self.runtime_selected_root_service not in set(
            self.validation_graph.candidate_services
        ):
            raise ValueError("v2.3.3 synthesis root escapes candidates")
        hypothesis_ids = tuple(
            item.hypothesis_id for item in self.competing_hypotheses
        )
        if hypothesis_ids != tuple(sorted(set(hypothesis_ids))):
            raise ValueError("v2.3.3 synthesis hypotheses are not canonical")
        if any(
            item.runtime_selected_root_service != self.runtime_selected_root_service
            for item in self.competing_hypotheses
        ):
            raise ValueError("v2.3.3 synthesis hypothesis root differs")
        residual_ids = tuple(
            item.anomaly_id for item in self.residual_anomaly_summaries
        )
        if residual_ids != self.validation_graph.residual_anomaly_ids:
            raise ValueError("v2.3.3 synthesis residual binding differs")
        if self.unresolved_dimensions != tuple(
            sorted(set(self.unresolved_dimensions))
        ):
            raise ValueError("v2.3.3 synthesis unresolved dimensions are not canonical")
        if (
            self.guard_decision.disposition
            is not IrreconcilableGuardDispositionV233.OPEN
        ):
            raise ValueError("v2.3.3 Provider synthesis requires an OPEN guard")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"request_sha256"})
        )
        if self.request_sha256 != expected:
            raise ValueError("v2.3.3 synthesis request digest differs")
        return self


class DiscoverySynthesisResponseV233(DtaModelV22):
    preferred_hypothesis_id: str = Field(pattern=r"^ch-v233-[0-9a-f]{16}$")
    provisional_mechanism_label: str = Field(min_length=1, max_length=96)
    mechanism_description: str = Field(min_length=1, max_length=1000)
    alternative_hypothesis_ids: tuple[str, ...] = Field(max_length=3)
    unresolved_questions: tuple[str, ...] = Field(max_length=8)
    recommended_next_observations: tuple[str, ...] = Field(max_length=8)
    review_recommendation: ReviewRecommendationV231

    @model_validator(mode="after")
    def require_response(self) -> "DiscoverySynthesisResponseV233":
        for values, label in (
            (self.alternative_hypothesis_ids, "alternative IDs"),
            (self.unresolved_questions, "questions"),
            (self.recommended_next_observations, "observations"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"v2.3.3 synthesis {label} are not canonical")
        if self.preferred_hypothesis_id in set(self.alternative_hypothesis_ids):
            raise ValueError("v2.3.3 preferred hypothesis is also an alternative")
        forbidden = " ".join(
            (
                self.provisional_mechanism_label,
                self.mechanism_description,
                *self.recommended_next_observations,
            )
        )
        if re.search(
            r"(?:\brunbook:|https?://|\bsudo\b|\bsh\s+-c\b|\bdocker\b)",
            forbidden,
            re.I,
        ):
            raise ValueError("v2.3.3 synthesis contains an authority hint")
        return self


class DiscoverySynthesisOutcomeV233(DtaModelV22):
    schema_version: Literal["dta-v233.discovery-synthesis-outcome.v1"]
    synthesis: DiscoverySynthesisResponseV233
    protocol_repairs: int = Field(ge=0, le=2)
    transport_retries: int = Field(ge=0, le=9)
    provider_calls: int = Field(ge=1)


class ProvisionalIncidentReportV233(DtaModelV22):
    schema_version: Literal["dta-v233.provisional-incident-report.v1"]
    report_id: str = Field(pattern=r"^report-v233-[0-9a-f]{16}$")
    terminal: Literal[
        "UNREGISTERED_INCIDENT_SUSPECTED",
        "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY",
    ]
    runtime_selected_root_service: str
    affected_services: tuple[str, ...] = Field(min_length=1, max_length=8)
    broad_fault_domain: ProvisionalFaultDomainV23
    projection_status: DomainProjectionStatusV233
    domain_candidate_scores: tuple[DomainScoreV233, ...]
    score_margin: StrictFloat
    runtime_hypotheses: tuple[RuntimeHypothesisV233, ...] = Field(
        min_length=1,
        max_length=4,
    )
    preferred_hypothesis_id: str
    alternative_hypothesis_ids: tuple[str, ...]
    provisional_mechanism_label: str = Field(min_length=1, max_length=96)
    mechanism_description: str = Field(min_length=1, max_length=1000)
    supporting_evidence_refs: tuple[str, ...] = Field(min_length=1)
    contradicting_evidence_refs: tuple[str, ...]
    residual_anomaly_ids: tuple[str, ...] = Field(min_length=1)
    guard_disposition: IrreconcilableGuardDispositionV233
    contradiction_witness_ids: tuple[str, ...]
    unresolved_dimensions: tuple[str, ...]
    unresolved_questions: tuple[str, ...]
    recommended_next_observations: tuple[str, ...]
    uncertainty_mode: ReportUncertaintyModeV231
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    confidence_band: ConfidenceBandV231
    review_recommendation: ReviewRecommendationV231
    action_authority: Literal["NONE"]
    domain_projection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    guard_decision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    synthesis_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_report(self) -> "ProvisionalIncidentReportV233":
        for values, label in (
            (self.affected_services, "affected services"),
            (self.alternative_hypothesis_ids, "alternative IDs"),
            (self.supporting_evidence_refs, "support refs"),
            (self.contradicting_evidence_refs, "contradiction refs"),
            (self.residual_anomaly_ids, "residual IDs"),
            (self.contradiction_witness_ids, "witness IDs"),
            (self.unresolved_dimensions, "unresolved dimensions"),
            (self.unresolved_questions, "questions"),
            (self.recommended_next_observations, "observations"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"v2.3.3 report {label} are not canonical")
        if self.runtime_selected_root_service not in set(self.affected_services):
            raise ValueError("v2.3.3 report root escapes affected services")
        ids = {item.hypothesis_id for item in self.runtime_hypotheses}
        if self.preferred_hypothesis_id not in ids:
            raise ValueError("v2.3.3 report preferred hypothesis is unknown")
        if not set(self.alternative_hypothesis_ids).issubset(ids):
            raise ValueError("v2.3.3 report alternative hypothesis is unknown")
        if self.guard_disposition is not IrreconcilableGuardDispositionV233.OPEN:
            raise ValueError("v2.3.3 provisional report requires an OPEN guard")
        if self.action_authority != "NONE":
            raise ValueError("v2.3.3 provisional report gained action authority")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("v2.3.3 provisional report digest differs")
        return self


_LABEL_BY_DOMAIN_V233 = {
    ProvisionalFaultDomainV23.CONFIGURATION: "configuration-state inconsistency",
    ProvisionalFaultDomainV23.RUNTIME: "runtime availability degradation",
    ProvisionalFaultDomainV23.RESOURCE: "resource pressure",
    ProvisionalFaultDomainV23.DEPENDENCY: "dependency path degradation",
    ProvisionalFaultDomainV23.NETWORK: "network transport degradation",
    ProvisionalFaultDomainV23.CONCURRENCY: "local concurrency saturation",
    ProvisionalFaultDomainV23.DATA: "data-state degradation",
    ProvisionalFaultDomainV23.EXTERNAL: "external dependency degradation",
    ProvisionalFaultDomainV23.UNKNOWN: "unresolved incident mechanism",
}


def _hashed_hypothesis(payload: dict[str, Any]) -> RuntimeHypothesisV233:
    identity_draft = RuntimeHypothesisV233.model_construct(
        **payload,
        hypothesis_id="ch-v233-0000000000000000",
        hypothesis_sha256="0" * 64,
    )
    identity = identity_draft.model_dump(
        mode="json",
        exclude={"hypothesis_id", "hypothesis_sha256"},
    )
    hypothesis_id = f"ch-v233-{semantic_sha256_v22(identity)[:16]}"
    draft = RuntimeHypothesisV233.model_construct(
        **payload,
        hypothesis_id=hypothesis_id,
        hypothesis_sha256="0" * 64,
    )
    return RuntimeHypothesisV233.model_validate(
        {
            **payload,
            "hypothesis_id": hypothesis_id,
            "hypothesis_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"hypothesis_sha256"})
            ),
        }
    )


def build_runtime_hypotheses_v233(
    *,
    graph: ResidualEvidenceGraphV23,
    projection: DomainProjectionV233,
) -> tuple[RuntimeHypothesisV233, ...]:
    root = projection.selected_root_service
    if root is None:
        raise ValueError("v2.3.3 runtime hypotheses require a selected root")
    residual = tuple(
        item
        for item in graph.generic_anomalies
        if item.anomaly_id in set(graph.residual_anomaly_ids)
    )
    if not residual:
        raise ValueError("v2.3.3 runtime hypotheses require residual anomalies")
    ranked = tuple(
        sorted(
            (
                item
                for item in projection.domain_scores
                if item.domain is not ProvisionalFaultDomainV23.UNKNOWN
                and item.score > 0.0
            ),
            key=lambda item: (-item.score, item.domain.value),
        )[:4]
    )
    if not ranked:
        raise ValueError("v2.3.3 runtime hypotheses lack a supported domain")
    built: list[RuntimeHypothesisV233] = []
    for score in ranked:
        score_refs = set(score.supporting_evidence_refs)
        anomalies = tuple(
            sorted(
                item.anomaly_id
                for item in residual
                if score_refs.intersection(item.evidence_refs)
            )
        ) or projection.supporting_anomaly_ids
        support = score.supporting_evidence_refs or projection.supporting_evidence_refs
        built.append(
            _hashed_hypothesis(
                {
                    "schema_version": "dta-v233.runtime-hypothesis.v1",
                    "provisional_label": _LABEL_BY_DOMAIN_V233[score.domain],
                    "runtime_selected_root_service": root,
                    "candidate_domain": score.domain,
                    "supporting_anomaly_ids": tuple(sorted(set(anomalies))),
                    "supporting_evidence_refs": tuple(sorted(set(support))),
                    "contradicting_evidence_refs": tuple(
                        sorted(set(score.contradicting_evidence_refs))
                    ),
                    "unresolved_questions": (
                        f"What observation separates {score.domain.value.casefold()} "
                        "from the remaining domain candidates?",
                    ),
                    "relative_support_score": float(score.score),
                }
            )
        )
    return tuple(sorted(built, key=lambda item: item.hypothesis_id))


def _runtime_confidence_v233(
    *,
    projection: DomainProjectionV233,
    guard: IrreconcilableGuardDecisionV233,
    unresolved_dimensions: tuple[str, ...],
) -> tuple[float, ConfidenceBandV231]:
    value = 0.2
    if projection.status is DomainProjectionStatusV233.RESOLVED:
        value += 0.3
    elif projection.status is DomainProjectionStatusV233.AMBIGUOUS:
        value += 0.15
    value += min(max(projection.score_margin, 0.0), 4.0) * 0.05
    if projection.selected_root_service is not None:
        value += 0.2
    if guard.disposition is IrreconcilableGuardDispositionV233.OPEN:
        value += 0.1
    value -= min(len(unresolved_dimensions), 4) * 0.05
    confidence = round(min(max(value, 0.1), 0.9), 2)
    band = (
        ConfidenceBandV231.HIGH
        if confidence >= 0.75
        else ConfidenceBandV231.MEDIUM
        if confidence >= 0.45
        else ConfidenceBandV231.LOW
    )
    return confidence, band


def build_provisional_report_v233(
    *,
    terminal: Literal[
        "UNREGISTERED_INCIDENT_SUSPECTED",
        "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY",
    ],
    request: DiscoverySynthesisRequestV233,
    synthesis: DiscoverySynthesisResponseV233,
) -> ProvisionalIncidentReportV233:
    guard = request.guard_decision
    if guard.disposition is not IrreconcilableGuardDispositionV233.OPEN:
        raise ValueError("v2.3.3 provisional report requires an OPEN guard")
    projection = request.runtime_domain_projection
    if projection.selected_root_service is None:
        raise ValueError("v2.3.3 provisional report lacks a runtime root")
    hypothesis_ids = {item.hypothesis_id for item in request.competing_hypotheses}
    if synthesis.preferred_hypothesis_id not in hypothesis_ids:
        raise ValueError("v2.3.3 synthesis preferred hypothesis is unknown")
    if not set(synthesis.alternative_hypothesis_ids).issubset(hypothesis_ids):
        raise ValueError("v2.3.3 synthesis alternative hypothesis is unknown")
    confidence, band = _runtime_confidence_v233(
        projection=projection,
        guard=guard,
        unresolved_dimensions=request.unresolved_dimensions,
    )
    uncertainty = (
        ReportUncertaintyModeV231.COMPETING_HYPOTHESES
        if projection.status is not DomainProjectionStatusV233.RESOLVED
        or (
            len(request.competing_hypotheses) > 1
            and projection.score_margin < 3.0
        )
        else ReportUncertaintyModeV231.SINGLE_LEADING_HYPOTHESIS
    )
    identity: dict[str, Any] = {
        "terminal": terminal,
        "runtime_selected_root_service": projection.selected_root_service,
        "affected_services": request.validation_graph.candidate_services,
        "broad_fault_domain": projection.selected_domain,
        "projection_status": projection.status,
        "domain_candidate_scores": projection.domain_scores,
        "score_margin": projection.score_margin,
        "runtime_hypotheses": request.competing_hypotheses,
        "preferred_hypothesis_id": synthesis.preferred_hypothesis_id,
        "alternative_hypothesis_ids": tuple(
            sorted(set(synthesis.alternative_hypothesis_ids))
        ),
        "provisional_mechanism_label": synthesis.provisional_mechanism_label.strip(),
        "mechanism_description": synthesis.mechanism_description.strip(),
        "supporting_evidence_refs": projection.supporting_evidence_refs,
        "contradicting_evidence_refs": projection.contradicting_evidence_refs,
        "residual_anomaly_ids": request.validation_graph.residual_anomaly_ids,
        "guard_disposition": guard.disposition,
        "contradiction_witness_ids": tuple(
            item.witness_id for item in guard.witnesses
        ),
        "unresolved_dimensions": request.unresolved_dimensions,
        "unresolved_questions": tuple(
            sorted(set(synthesis.unresolved_questions))
        ),
        "recommended_next_observations": tuple(
            sorted(set(synthesis.recommended_next_observations))
        ),
        "uncertainty_mode": uncertainty,
        "confidence": confidence,
        "confidence_band": band,
        "review_recommendation": synthesis.review_recommendation,
        "action_authority": "NONE",
        "domain_projection_sha256": projection.projection_sha256,
        "guard_decision_sha256": guard.decision_sha256,
        "synthesis_request_sha256": request.request_sha256,
    }
    identity_draft = ProvisionalIncidentReportV233.model_construct(
        schema_version="dta-v233.provisional-incident-report.v1",
        report_id="report-v233-0000000000000000",
        **identity,
        report_sha256="0" * 64,
    )
    report_identity = identity_draft.model_dump(
        mode="json",
        exclude={"schema_version", "report_id", "report_sha256"},
    )
    report_id = f"report-v233-{semantic_sha256_v22(report_identity)[:16]}"
    payload = {
        "schema_version": "dta-v233.provisional-incident-report.v1",
        "report_id": report_id,
        **identity,
    }
    draft = ProvisionalIncidentReportV233.model_construct(
        **payload,
        report_sha256="0" * 64,
    )
    return ProvisionalIncidentReportV233.model_validate(
        {
            **payload,
            "report_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )


__all__ = (
    "DiscoverySynthesisOutcomeV233",
    "DiscoverySynthesisRequestV233",
    "DiscoverySynthesisResponseV233",
    "ProvisionalIncidentReportV233",
    "ResidualAnomalySummaryV233",
    "RuntimeHypothesisV233",
    "build_provisional_report_v233",
    "build_runtime_hypotheses_v233",
)
