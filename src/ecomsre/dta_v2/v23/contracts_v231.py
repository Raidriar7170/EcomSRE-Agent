"""Non-actionable competing-hypothesis contracts for DTA v2.3.1."""

from __future__ import annotations

from enum import Enum
import re
from typing import Any, Literal

from pydantic import Field, StrictFloat, TypeAdapter, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.conflict_model_v231 import (
    ConflictAssessmentV231,
    InterpretationClusterV231,
)
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


class ReportUncertaintyModeV231(str, Enum):
    SINGLE_LEADING_HYPOTHESIS = "SINGLE_LEADING_HYPOTHESIS"
    COMPETING_HYPOTHESES = "COMPETING_HYPOTHESES"


class ConfidenceBandV231(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ReviewRecommendationV231(str, Enum):
    REQUEST_MORE_EVIDENCE = "REQUEST_MORE_EVIDENCE"
    SAVE_AS_INCIDENT_ONLY = "SAVE_AS_INCIDENT_ONLY"
    CONSIDER_SHADOW_REGISTRATION = "CONSIDER_SHADOW_REGISTRATION"


class CompetingHypothesisV231(DtaModelV22):
    schema_version: Literal["dta-v231.competing-hypothesis.v1"]
    hypothesis_id: str = Field(pattern=r"^ch-v231-[0-9a-f]{16}$")
    provisional_label: str = Field(min_length=1, max_length=96)
    suspected_root_services: tuple[str, ...] = Field(min_length=1, max_length=4)
    broad_fault_domain: ProvisionalFaultDomainV23
    supporting_anomaly_ids: tuple[str, ...] = Field(min_length=1)
    supporting_evidence_refs: tuple[str, ...] = Field(min_length=1)
    contradicting_evidence_refs: tuple[str, ...]
    unexplained_questions: tuple[str, ...] = Field(min_length=1, max_length=8)
    discriminating_evidence_goals: tuple[str, ...] = Field(min_length=1, max_length=8)
    relative_support_score: StrictFloat = Field(ge=0.0)
    hypothesis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_hypothesis(self) -> "CompetingHypothesisV231":
        for values, label in (
            (self.suspected_root_services, "roots"),
            (self.supporting_anomaly_ids, "anomaly IDs"),
            (self.supporting_evidence_refs, "support refs"),
            (self.contradicting_evidence_refs, "contradiction refs"),
            (self.unexplained_questions, "questions"),
            (self.discriminating_evidence_goals, "evidence goals"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"competing hypothesis {label} are not canonical")
        if set(self.supporting_evidence_refs).intersection(
            self.contradicting_evidence_refs
        ):
            raise ValueError("competing hypothesis support and contradiction overlap")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"hypothesis_sha256"})
        )
        if self.hypothesis_sha256 != expected:
            raise ValueError("competing hypothesis digest differs")
        return self


class CompetingHypothesisSetV231(DtaModelV22):
    schema_version: Literal["dta-v231.competing-hypothesis-set.v1"]
    hypotheses: tuple[CompetingHypothesisV231, ...] = Field(min_length=2, max_length=4)
    leading_hypothesis_id: str
    unresolved_dimensions: tuple[str, ...] = Field(min_length=1)
    set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_set(self) -> "CompetingHypothesisSetV231":
        ids = tuple(item.hypothesis_id for item in self.hypotheses)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("competing hypotheses are not canonical")
        if self.leading_hypothesis_id not in set(ids):
            raise ValueError("competing leading hypothesis is unknown")
        leading = next(
            item for item in self.hypotheses if item.hypothesis_id == self.leading_hypothesis_id
        )
        if any(
            item.relative_support_score > leading.relative_support_score
            for item in self.hypotheses
        ):
            raise ValueError("competing leading hypothesis lacks highest support")
        if self.unresolved_dimensions != tuple(sorted(set(self.unresolved_dimensions))):
            raise ValueError("competing unresolved dimensions are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"set_sha256"})
        )
        if self.set_sha256 != expected:
            raise ValueError("competing hypothesis set digest differs")
        return self


class ProvisionalIncidentReportV231(DtaModelV22):
    schema_version: Literal["dta-v231.provisional-incident-report.v1"]
    report_id: str = Field(pattern=r"^report-v231-[0-9a-f]{16}$")
    terminal: Literal[
        "UNREGISTERED_INCIDENT_SUSPECTED",
        "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY",
    ]
    suspected_root_services: tuple[str, ...] = Field(min_length=1, max_length=4)
    affected_services: tuple[str, ...] = Field(min_length=1, max_length=4)
    broad_fault_domain: ProvisionalFaultDomainV23
    provisional_mechanism_label: str = Field(min_length=1, max_length=96)
    mechanism_description: str = Field(min_length=1, max_length=1000)
    observed_symptoms: tuple[str, ...] = Field(min_length=1, max_length=16)
    supporting_evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=60)
    contradicting_evidence_refs: tuple[str, ...] = Field(max_length=60)
    unexplained_anomaly_ids: tuple[str, ...] = Field(min_length=1, max_length=60)
    alternative_hypotheses: tuple[str, ...] = Field(max_length=8)
    recommended_next_observations: tuple[str, ...] = Field(max_length=8)
    uncertainty_mode: ReportUncertaintyModeV231
    competing_hypotheses: tuple[CompetingHypothesisV231, ...] = Field(max_length=4)
    preferred_hypothesis_id: str | None
    unresolved_questions: tuple[str, ...] = Field(max_length=12)
    conflict_summary: str = Field(min_length=1, max_length=1000)
    recommended_discriminating_observations: tuple[str, ...] = Field(max_length=8)
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    confidence_band: ConfidenceBandV231
    review_recommendation: ReviewRecommendationV231
    action_authority: Literal["NONE"]
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_report(self) -> "ProvisionalIncidentReportV231":
        for values, label in (
            (self.suspected_root_services, "roots"),
            (self.affected_services, "affected services"),
            (self.supporting_evidence_refs, "support refs"),
            (self.contradicting_evidence_refs, "contradiction refs"),
            (self.unexplained_anomaly_ids, "anomaly IDs"),
            (self.unresolved_questions, "questions"),
            (
                self.recommended_discriminating_observations,
                "discriminating observations",
            ),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"v2.3.1 report {label} are not canonical")
        if not set(self.suspected_root_services).issubset(self.affected_services):
            raise ValueError("v2.3.1 report roots escape affected services")
        if set(self.supporting_evidence_refs).intersection(
            self.contradicting_evidence_refs
        ):
            raise ValueError("v2.3.1 report support and contradiction overlap")
        forbidden_text = " ".join(
            (
                self.provisional_mechanism_label,
                self.mechanism_description,
                self.conflict_summary,
                *self.recommended_next_observations,
                *self.recommended_discriminating_observations,
            )
        )
        if re.search(r"(?:\brunbook:|https?://|\bsudo\b|\bsh\s+-c\b)", forbidden_text, re.I):
            raise ValueError("v2.3.1 report contains an executable authority hint")
        if self.uncertainty_mode is ReportUncertaintyModeV231.COMPETING_HYPOTHESES:
            if len(self.competing_hypotheses) < 2:
                raise ValueError("competing report lacks two structured hypotheses")
            if len(self.alternative_hypotheses) < 2:
                raise ValueError("competing report lacks two human-readable hypotheses")
            if not self.unresolved_questions:
                raise ValueError("competing report lacks an unresolved question")
            if self.preferred_hypothesis_id not in {
                item.hypothesis_id for item in self.competing_hypotheses
            }:
                raise ValueError("competing report preferred hypothesis is unknown")
            if self.confidence > 0.65 or self.confidence_band is ConfidenceBandV231.HIGH:
                raise ValueError("competing report confidence exceeds its cap")
            if (
                self.review_recommendation
                is ReviewRecommendationV231.CONSIDER_SHADOW_REGISTRATION
            ):
                raise ValueError("competing report recommends automatic-like registration")
        elif self.competing_hypotheses or self.preferred_hypothesis_id is not None:
            raise ValueError("single-leading report carries competing hypotheses")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("v2.3.1 report digest differs")
        return self


_LABEL_BY_DOMAIN = {
    ProvisionalFaultDomainV23.CONFIGURATION: "change-correlated failure",
    ProvisionalFaultDomainV23.DEPENDENCY: "dependency-path degradation",
    ProvisionalFaultDomainV23.CONCURRENCY: "local concurrency or pool exhaustion",
    ProvisionalFaultDomainV23.RUNTIME: "runtime availability failure",
    ProvisionalFaultDomainV23.RESOURCE: "resource-pressure pattern",
    ProvisionalFaultDomainV23.UNKNOWN: "unknown cross-service degradation",
}


def _build_hypothesis(
    *,
    cluster: InterpretationClusterV231,
    candidate_services: set[str],
    root: str,
    domain: ProvisionalFaultDomainV23,
) -> CompetingHypothesisV231:
    if root not in candidate_services or root not in set(
        cluster.candidate_root_services
    ):
        raise ValueError("competing hypothesis roots escape current candidates")
    if domain not in set(cluster.broad_domains):
        raise ValueError("competing hypothesis domain escapes its cluster")
    roots = (root,)
    identity = {
        "cluster_id": cluster.cluster_id,
        "roots": roots,
        "domain": domain.value,
        "anomaly_ids": cluster.anomaly_ids,
    }
    payload: dict[str, Any] = {
        "schema_version": "dta-v231.competing-hypothesis.v1",
        "hypothesis_id": f"ch-v231-{semantic_sha256_v22(identity)[:16]}",
        "provisional_label": _LABEL_BY_DOMAIN.get(
            domain,
            "unknown cross-service degradation",
        ),
        "suspected_root_services": roots,
        "broad_fault_domain": domain,
        "supporting_anomaly_ids": cluster.anomaly_ids,
        "supporting_evidence_refs": cluster.evidence_refs,
        "contradicting_evidence_refs": (),
        "unexplained_questions": (
            f"Which observation distinguishes {domain.value.casefold()} from the competing interpretations?",
        ),
        "discriminating_evidence_goals": (
            f"Resolve {domain.value} support against the competing evidence surface",
        ),
        "relative_support_score": cluster.cluster_strength + 0.1 * len(cluster.coherence_edges),
    }
    draft = CompetingHypothesisV231.model_construct(
        **payload,
        hypothesis_sha256="0" * 64,
    )
    return CompetingHypothesisV231.model_validate(
        {
            **payload,
            "hypothesis_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"hypothesis_sha256"})
            ),
        }
    )


def build_competing_hypothesis_set_v231(
    *,
    graph: ResidualEvidenceGraphV23,
    assessment: ConflictAssessmentV231,
) -> CompetingHypothesisSetV231:
    ranked_clusters = tuple(sorted(
        assessment.interpretation_clusters,
        key=lambda item: (-item.cluster_strength, item.cluster_id),
    ))
    variants = tuple(
        (cluster, root, domain)
        for cluster in ranked_clusters
        for root in cluster.candidate_root_services
        for domain in cluster.broad_domains
    )[:4]
    if len(variants) < 2:
        raise ValueError("competing hypothesis set requires two material interpretations")
    built = tuple(
        _build_hypothesis(
            cluster=cluster,
            candidate_services=set(graph.candidate_services),
            root=root,
            domain=domain,
        )
        for cluster, root, domain in variants
    )
    leading = built[0].hypothesis_id
    hypotheses = tuple(sorted(built, key=lambda item: item.hypothesis_id))
    payload: dict[str, Any] = {
        "schema_version": "dta-v231.competing-hypothesis-set.v1",
        "hypotheses": hypotheses,
        "leading_hypothesis_id": leading,
        "unresolved_dimensions": assessment.unresolved_dimensions,
    }
    draft = CompetingHypothesisSetV231.model_construct(
        **payload,
        set_sha256="0" * 64,
    )
    return CompetingHypothesisSetV231.model_validate(
        {
            **payload,
            "set_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"set_sha256"})
            ),
        }
    )


def build_competing_report_v231(
    *,
    graph: ResidualEvidenceGraphV23,
    assessment: ConflictAssessmentV231,
    hypothesis_set: CompetingHypothesisSetV231,
) -> ProvisionalIncidentReportV231:
    by_id = {item.hypothesis_id: item for item in hypothesis_set.hypotheses}
    leading = by_id[hypothesis_set.leading_hypothesis_id]
    residual_ids = set(graph.residual_anomaly_ids)
    residual_refs = {
        ref
        for item in graph.generic_anomalies
        if item.anomaly_id in residual_ids
        for ref in item.evidence_refs
    }
    hypothesis_refs = {
        ref for item in hypothesis_set.hypotheses for ref in item.supporting_evidence_refs
    }
    if not hypothesis_refs or not hypothesis_refs.issubset(residual_refs):
        raise ValueError("competing report hypotheses cite non-residual evidence")
    affected = tuple(
        sorted(
            {
                service
                for item in hypothesis_set.hypotheses
                for service in item.suspected_root_services
            }
        )
    )[:4]
    questions = tuple(
        sorted(
            {
                question
                for item in hypothesis_set.hypotheses
                for question in item.unexplained_questions
            }
        )
    )
    observations = tuple(
        sorted(
            f"Read {source.value} to resolve {', '.join(assessment.unresolved_dimensions).casefold()}"
            for source in assessment.discriminating_sources
        )
    )
    human_hypotheses = tuple(
        sorted(
            f"{item.provisional_label}: {', '.join(item.suspected_root_services)}"
            for item in hypothesis_set.hypotheses
        )
    )
    competing = tuple(sorted(hypothesis_set.hypotheses, key=lambda item: item.hypothesis_id))
    identity: dict[str, Any] = {
        "terminal": "UNREGISTERED_INCIDENT_SUSPECTED",
        "suspected_root_services": leading.suspected_root_services,
        "affected_services": affected,
        "broad_fault_domain": leading.broad_fault_domain,
        "provisional_mechanism_label": leading.provisional_label,
        "mechanism_description": (
            "Strong residual evidence supports one incident surface while multiple "
            "causal mechanisms remain plausible."
        ),
        "observed_symptoms": tuple(
            sorted(
                item.summary
                for item in graph.generic_anomalies
                if item.anomaly_id
                in {
                    anomaly_id
                    for value in hypothesis_set.hypotheses
                    for anomaly_id in value.supporting_anomaly_ids
                }
            )
        )[:16],
        "supporting_evidence_refs": tuple(sorted(hypothesis_refs)),
        "contradicting_evidence_refs": tuple(
            sorted(
                {
                    ref
                    for item in hypothesis_set.hypotheses
                    for ref in item.contradicting_evidence_refs
                }
            )
        ),
        "unexplained_anomaly_ids": tuple(
            sorted(
                {
                    anomaly_id
                    for item in hypothesis_set.hypotheses
                    for anomaly_id in item.supporting_anomaly_ids
                }
            )
        ),
        "alternative_hypotheses": human_hypotheses,
        "recommended_next_observations": observations,
        "uncertainty_mode": ReportUncertaintyModeV231.COMPETING_HYPOTHESES,
        "competing_hypotheses": competing,
        "preferred_hypothesis_id": hypothesis_set.leading_hypothesis_id,
        "unresolved_questions": questions,
        "conflict_summary": (
            "The visible interpretations are causally coherent alternatives; no "
            "explicit contradiction requires a hard conflict terminal."
        ),
        "recommended_discriminating_observations": observations,
        "confidence": 0.60,
        "confidence_band": ConfidenceBandV231.MEDIUM,
        "review_recommendation": ReviewRecommendationV231.REQUEST_MORE_EVIDENCE,
        "action_authority": "NONE",
    }
    report_identity = {
        **identity,
        "competing_hypotheses": tuple(
            item.model_dump(mode="json") for item in competing
        ),
    }
    report_id = f"report-v231-{semantic_sha256_v22(report_identity)[:16]}"
    payload = {
        "schema_version": "dta-v231.provisional-incident-report.v1",
        "report_id": report_id,
        **identity,
    }
    draft = ProvisionalIncidentReportV231.model_construct(
        **payload,
        report_sha256="0" * 64,
    )
    return ProvisionalIncidentReportV231.model_validate(
        {
            **payload,
            "report_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )


def build_single_report_v231(
    *,
    graph: ResidualEvidenceGraphV23,
    assessment: ConflictAssessmentV231,
) -> ProvisionalIncidentReportV231:
    """Build a conservative single-leading report when no competition remains."""

    if len(assessment.interpretation_clusters) != 1:
        raise ValueError("single-leading report requires one interpretation cluster")
    cluster = assessment.interpretation_clusters[0]
    hypothesis = _build_hypothesis(
        cluster=cluster,
        candidate_services=set(graph.candidate_services),
        root=cluster.candidate_root_services[0],
        domain=cluster.broad_domains[0],
    )
    observations = tuple(
        sorted(
            item.summary
            for item in graph.generic_anomalies
            if item.anomaly_id in set(cluster.anomaly_ids)
        )
    )[:16]
    payload: dict[str, Any] = {
        "schema_version": "dta-v231.provisional-incident-report.v1",
        "terminal": "UNREGISTERED_INCIDENT_SUSPECTED",
        "suspected_root_services": hypothesis.suspected_root_services,
        "affected_services": hypothesis.suspected_root_services,
        "broad_fault_domain": hypothesis.broad_fault_domain,
        "provisional_mechanism_label": hypothesis.provisional_label,
        "mechanism_description": (
            "Strong residual evidence supports one provisional incident "
            "interpretation without granting action authority."
        ),
        "observed_symptoms": observations,
        "supporting_evidence_refs": hypothesis.supporting_evidence_refs,
        "contradicting_evidence_refs": hypothesis.contradicting_evidence_refs,
        "unexplained_anomaly_ids": hypothesis.supporting_anomaly_ids,
        "alternative_hypotheses": (),
        "recommended_next_observations": (),
        "uncertainty_mode": ReportUncertaintyModeV231.SINGLE_LEADING_HYPOTHESIS,
        "competing_hypotheses": (),
        "preferred_hypothesis_id": None,
        "unresolved_questions": (),
        "conflict_summary": "No material competing interpretation remains visible.",
        "recommended_discriminating_observations": (),
        "confidence": 0.70,
        "confidence_band": ConfidenceBandV231.MEDIUM,
        "review_recommendation": ReviewRecommendationV231.SAVE_AS_INCIDENT_ONLY,
        "action_authority": "NONE",
    }
    identity = {
        key: value
        for key, value in payload.items()
        if key != "schema_version"
    }
    payload["report_id"] = f"report-v231-{semantic_sha256_v22(identity)[:16]}"
    draft = ProvisionalIncidentReportV231.model_construct(
        **payload,
        report_sha256="0" * 64,
    )
    return ProvisionalIncidentReportV231.model_validate(
        {
            **payload,
            "report_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )


def build_provider_report_v231(
    *,
    response_payload: dict[str, Any],
    graph: ResidualEvidenceGraphV23,
    hypothesis_set: CompetingHypothesisSetV231,
) -> ProvisionalIncidentReportV231:
    """Bind a Provider rendering to the prevalidated graph and hypothesis set."""

    expected_hypotheses = tuple(
        item.model_dump(mode="json") for item in hypothesis_set.hypotheses
    )
    if tuple(response_payload.get("competing_hypotheses", ())) != expected_hypotheses:
        raise ValueError("Provider changed the evidence-bound competing hypotheses")
    response_payload = dict(response_payload)
    for field in (
        "suspected_root_services",
        "affected_services",
        "supporting_evidence_refs",
        "contradicting_evidence_refs",
        "unexplained_anomaly_ids",
        "unresolved_questions",
        "recommended_discriminating_observations",
    ):
        response_payload[field] = tuple(
            sorted(set(response_payload.get(field, ())))
        )
    candidates = set(graph.candidate_services)
    roots = tuple(response_payload.get("suspected_root_services", ()))
    affected = tuple(response_payload.get("affected_services", ()))
    if not set((*roots, *affected)).issubset(candidates):
        raise ValueError("Provider report services escape current candidates")
    residual_ids = set(graph.residual_anomaly_ids)
    residual_refs = {
        ref
        for item in graph.generic_anomalies
        if item.anomaly_id in residual_ids
        for ref in item.evidence_refs
    }
    cited = {
        *response_payload.get("supporting_evidence_refs", ()),
        *response_payload.get("contradicting_evidence_refs", ()),
    }
    if not cited or not cited.issubset(residual_refs):
        raise ValueError("Provider report cites evidence outside the residual graph")
    unexplained = set(response_payload.get("unexplained_anomaly_ids", ()))
    if not unexplained or not unexplained.issubset(residual_ids):
        raise ValueError("Provider report cites a non-residual anomaly")
    identity = dict(response_payload)
    report_id = f"report-v231-{semantic_sha256_v22(identity)[:16]}"
    payload: dict[str, Any] = {
        "schema_version": "dta-v231.provisional-incident-report.v1",
        "report_id": report_id,
        **response_payload,
    }
    typed_payload: dict[str, Any] = {}
    for field_name, model_field in ProvisionalIncidentReportV231.model_fields.items():
        if field_name == "report_sha256":
            continue
        typed_payload[field_name] = TypeAdapter(
            model_field.rebuild_annotation()
        ).validate_python(
            payload[field_name],
            strict=False,
        )
    draft = ProvisionalIncidentReportV231.model_construct(
        **typed_payload,
        report_sha256="0" * 64,
    )
    return ProvisionalIncidentReportV231.model_validate(
        {
            **typed_payload,
            "report_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )


__all__ = (
    "CompetingHypothesisSetV231",
    "CompetingHypothesisV231",
    "ConfidenceBandV231",
    "ProvisionalIncidentReportV231",
    "ReportUncertaintyModeV231",
    "ReviewRecommendationV231",
    "build_competing_hypothesis_set_v231",
    "build_competing_report_v231",
    "build_provider_report_v231",
    "build_single_report_v231",
)
