"""Development-only v2.3.2 domain audit for the v2.3.3 projector."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    ContrastiveResourceActionV225,
    contrastive_resource_action_if_eligible_v225,
)
from ecomsre.dta_v2.v22.memory import SalientEvidenceMemoryV22, build_memory_views_v22
from ecomsre.dta_v2.v22.practical_runner import _baseline
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import QuerySpecificReplayBackendV22
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import (
    build_replay_target_coverage_v225,
)
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.discovery_runtime import _build_read_outcome_v23
from ecomsre.dta_v2.v23.domain_projection_v233 import (
    CONCURRENCY_LEXICON_V233,
    DomainProjectionStatusV233,
    DomainProjectionV233,
    project_domain_v233,
)
from ecomsre.dta_v2.v23.evaluation import _build_common_context_v23
from ecomsre.dta_v2.v23.evaluation_data_v232 import (
    load_evaluation_cases_v232,
    load_evaluation_truth_index_v232,
    load_evaluation_truth_shard_v232,
    load_evaluation_views_v232,
)
from ecomsre.dta_v2.v23.evaluation_v231 import (
    EvaluationCaseSpecV231,
    EvaluationCategoryV231,
    EvaluationOntologyViewSpecV231,
    _residual_graph_v231,
    materialize_evaluation_case_v231,
)
from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyKindV23


class DomainDevelopmentReadReasonV233(str, Enum):
    CORROBORATE_CONCURRENCY = "CORROBORATE_CONCURRENCY"
    LOCALIZE_DEPENDENCY_PATH = "LOCALIZE_DEPENDENCY_PATH"
    CORRELATE_RECENT_CHANGE = "CORRELATE_RECENT_CHANGE"
    CHECK_RESOURCE_STATE = "CHECK_RESOURCE_STATE"


class DomainDevelopmentReadV233(DtaModelV22):
    source: EvidenceSourceV22
    target_services: tuple[str, ...]
    reason: DomainDevelopmentReadReasonV233


class DomainAuditEntryV233(DtaModelV22):
    case_id: str
    expected_root_service: str
    expected_broad_domain: ProvisionalFaultDomainV23
    projection: DomainProjectionV233
    top_two_domains: tuple[ProvisionalFaultDomainV23, ProvisionalFaultDomainV23]
    selected_root_correct: StrictBool
    broad_domain_correct: StrictBool
    evaluator_domain_in_top_two: StrictBool
    evidence_refs_valid: StrictBool
    discovery_reads: tuple[DomainDevelopmentReadV233, ...]


class DomainAuditV233(DtaModelV22):
    schema_version: Literal["dta-v233.domain-audit.v1"]
    development_set: Literal["dta-v232-fixed-24-case"]
    case_count: Literal[14]
    selected_root_correct: StrictInt = Field(ge=0, le=14)
    broad_domain_correct: StrictInt = Field(ge=0, le=14)
    evaluator_domain_top_two: StrictInt = Field(ge=0, le=14)
    resolved_or_ambiguous: StrictInt = Field(ge=0, le=14)
    evidence_ref_validity: StrictFloat = Field(ge=0.0, le=1.0)
    maximum_discovery_reads: StrictInt = Field(ge=0, le=3)
    provider_calls: Literal[0]
    projection_iteration_count: Literal[1]
    entries: tuple[DomainAuditEntryV233, ...] = Field(min_length=14, max_length=14)
    audit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_audit(self) -> "DomainAuditV233":
        if tuple(item.case_id for item in self.entries) != tuple(
            sorted(item.case_id for item in self.entries)
        ):
            raise ValueError("v2.3.3 domain audit cases are not canonical")
        if self.selected_root_correct != sum(
            item.selected_root_correct for item in self.entries
        ):
            raise ValueError("v2.3.3 domain audit root count differs")
        if self.broad_domain_correct != sum(
            item.broad_domain_correct for item in self.entries
        ):
            raise ValueError("v2.3.3 domain audit domain count differs")
        if self.evaluator_domain_top_two != sum(
            item.evaluator_domain_in_top_two for item in self.entries
        ):
            raise ValueError("v2.3.3 domain audit top-two count differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"audit_sha256"})
        )
        if self.audit_sha256 != expected:
            raise ValueError("v2.3.3 domain audit digest differs")
        return self


def _executed_action_ids(outcomes: tuple[object, ...]) -> set[str]:
    return {str(getattr(item, "action_id")) for item in outcomes}


def _standard_action(
    *,
    context: Any,
    source: EvidenceSourceV22,
    target_order: tuple[str, ...],
    outcomes: tuple[object, ...],
) -> EvidenceActionV22 | None:
    executed = _executed_action_ids(outcomes)
    by_target = {
        item.target_services[0]: item
        for item in context.catalog.registry_actions
        if item.source is source
        and len(item.target_services) == 1
        and item.action_id not in executed
    }
    return next((by_target[target] for target in target_order if target in by_target), None)


def _resource_action(
    *,
    context: Any,
    outcomes: tuple[object, ...],
) -> ContrastiveResourceActionV225 | None:
    if any(
        getattr(item, "source", None) is EvidenceSourceV22.RESOURCES
        for item in outcomes
    ):
        return None
    coverage = build_replay_target_coverage_v225(
        source=EvidenceSourceV22.RESOURCES,
        candidate_services=context.case.candidate_services,
        covered_target_services=context.case.candidate_services,
    )
    return contrastive_resource_action_if_eligible_v225(
        coverage=coverage,
        resources_enabled=True,
        unresolved_resource_hypotheses=len(context.case.candidate_services),
        remaining_budget=3.0,
        bundle_mode=True,
    )


def _has_concurrency_lexicon(graph: Any) -> bool:
    for item in graph.generic_anomalies:
        if item.kind is not GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN:
            continue
        text = " ".join(value.value for value in item.observed_values).casefold()
        if any(token in text.split() for token in CONCURRENCY_LEXICON_V233):
            return True
    return False


def _next_development_read_v233(
    *,
    context: Any,
    graph: Any,
    projection: DomainProjectionV233,
    outcomes: tuple[object, ...],
) -> tuple[object, DomainDevelopmentReadReasonV233] | None:
    selected = projection.selected_root_service
    targets = tuple(
        dict.fromkeys(
            (
                *((selected,) if selected is not None else ()),
                *context.case.candidate_services,
            )
        )
    )
    kinds = {item.kind for item in graph.generic_anomalies}
    if _has_concurrency_lexicon(graph):
        resource_action = _resource_action(context=context, outcomes=outcomes)
        if resource_action is not None:
            return resource_action, DomainDevelopmentReadReasonV233.CORROBORATE_CONCURRENCY
    if GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER in kinds:
        trace_action = _standard_action(
            context=context,
            source=EvidenceSourceV22.TRACES,
            target_order=targets,
            outcomes=outcomes,
        )
        if trace_action is not None:
            return trace_action, DomainDevelopmentReadReasonV233.LOCALIZE_DEPENDENCY_PATH
    if GenericAnomalyKindV23.METRIC_ERROR_OUTLIER in kinds:
        change_action = _standard_action(
            context=context,
            source=EvidenceSourceV22.CHANGES,
            target_order=targets,
            outcomes=outcomes,
        )
        if change_action is not None:
            return change_action, DomainDevelopmentReadReasonV233.CORRELATE_RECENT_CHANGE
    resource_action = _resource_action(context=context, outcomes=outcomes)
    if resource_action is not None:
        return resource_action, DomainDevelopmentReadReasonV233.CHECK_RESOURCE_STATE
    return None


def project_development_case_v233(
    *,
    repository_root: Path,
    spec: EvaluationCaseSpecV231,
    view_spec: EvaluationOntologyViewSpecV231,
) -> tuple[DomainProjectionV233, SalientEvidenceMemoryV22, tuple[DomainDevelopmentReadV233, ...]]:
    case = materialize_evaluation_case_v231(
        repository_root=repository_root,
        spec=spec,
    )
    context = _build_common_context_v23(
        case=case,
        hidden_mechanism=view_spec.hidden_mechanism,
    )
    outcomes: tuple[object, ...] = tuple(context.outcomes)
    memory = context.memory
    backend = QuerySpecificReplayBackendV22(case.capture)
    reads: list[DomainDevelopmentReadV233] = []
    while True:
        graph = _residual_graph_v231(context=context, memory=memory)
        projection = project_domain_v233(graph=graph, memory=memory)
        if projection.status is DomainProjectionStatusV233.RESOLVED or len(reads) == 3:
            return projection, memory, tuple(reads)
        planned = _next_development_read_v233(
            context=context,
            graph=graph,
            projection=projection,
            outcomes=outcomes,
        )
        if planned is None:
            return projection, memory, tuple(reads)
        action, reason = planned
        if isinstance(action, ContrastiveResourceActionV225):
            outcome = _build_read_outcome_v23(action=action, capture=case.capture)
        elif isinstance(action, EvidenceActionV22):
            outcome = backend.execute(action)
        else:
            raise TypeError("v2.3.3 domain development read is unsupported")
        outcomes = (*outcomes, outcome)
        memory, _ = build_memory_views_v22(
            outcomes=outcomes,  # type: ignore[arg-type]
            baseline=_baseline(case),
            observed_at=case.capture.captured_at,
            top_k=64,
        )
        reads.append(
            DomainDevelopmentReadV233(
                source=getattr(action, "source"),
                target_services=getattr(action, "target_services"),
                reason=reason,
            )
        )


def build_v232_domain_audit_v233(*, repository_root: Path) -> DomainAuditV233:
    evaluation_root = repository_root / "config/dta-v232/evaluation"
    cases = load_evaluation_cases_v232(evaluation_root / "cases.json")
    views = load_evaluation_views_v232(evaluation_root / "ontology-views.json")
    truth_index = load_evaluation_truth_index_v232(evaluation_root / "truth.json")
    entries: list[DomainAuditEntryV233] = []
    for spec in cases.cases:
        truth = load_evaluation_truth_shard_v232(
            index_path=evaluation_root / "truth.json",
            binding=truth_index.require(spec.case_id),
        ).record.evaluator_truth
        if truth.category not in {
            EvaluationCategoryV231.NOVEL_HIDDEN,
            EvaluationCategoryV231.NOVEL_UNREGISTERED,
        }:
            continue
        projection, memory, reads = project_development_case_v233(
            repository_root=repository_root,
            spec=spec,
            view_spec=views.require(spec.case_id),
        )
        expected_domain = ProvisionalFaultDomainV23(str(truth.expected_broad_domain))
        top_two = tuple(
            item.domain
            for item in sorted(
                (
                    item
                    for item in projection.domain_scores
                    if item.domain is not ProvisionalFaultDomainV23.UNKNOWN
                ),
                key=lambda item: (-item.score, item.domain.value),
            )[:2]
        )
        memory_refs = {item.evidence_ref for item in memory.evidence_refs}
        projected_refs = {
            *projection.supporting_evidence_refs,
            *projection.contradicting_evidence_refs,
        }
        entries.append(
            DomainAuditEntryV233(
                case_id=spec.case_id,
                expected_root_service=str(truth.expected_root_service),
                expected_broad_domain=expected_domain,
                projection=projection,
                top_two_domains=top_two,  # type: ignore[arg-type]
                selected_root_correct=(
                    projection.selected_root_service == truth.expected_root_service
                ),
                broad_domain_correct=(projection.selected_domain is expected_domain),
                evaluator_domain_in_top_two=expected_domain in set(top_two),
                evidence_refs_valid=projected_refs.issubset(memory_refs),
                discovery_reads=reads,
            )
        )
    canonical = tuple(sorted(entries, key=lambda item: item.case_id))
    valid = sum(item.evidence_refs_valid for item in canonical)
    payload: dict[str, Any] = {
        "schema_version": "dta-v233.domain-audit.v1",
        "development_set": "dta-v232-fixed-24-case",
        "case_count": 14,
        "selected_root_correct": sum(item.selected_root_correct for item in canonical),
        "broad_domain_correct": sum(item.broad_domain_correct for item in canonical),
        "evaluator_domain_top_two": sum(
            item.evaluator_domain_in_top_two for item in canonical
        ),
        "resolved_or_ambiguous": sum(
            item.projection.status
            in {DomainProjectionStatusV233.RESOLVED, DomainProjectionStatusV233.AMBIGUOUS}
            for item in canonical
        ),
        "evidence_ref_validity": float(valid / len(canonical)),
        "maximum_discovery_reads": max(len(item.discovery_reads) for item in canonical),
        "provider_calls": 0,
        "projection_iteration_count": 1,
        "entries": canonical,
    }
    draft = DomainAuditV233.model_construct(**payload, audit_sha256="0" * 64)
    return DomainAuditV233.model_validate(
        {
            **payload,
            "audit_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"audit_sha256"})
            ),
        }
    )


def render_domain_audit_markdown_v233(audit: DomainAuditV233) -> str:
    lines = [
        "# DTA v2.3.3 Domain Projection Development Audit",
        "",
        "Development-only audit over the frozen v2.3.2 novelty cases. No Provider was called.",
        "",
        f"- Selected root: `{audit.selected_root_correct} / {audit.case_count}`",
        f"- Broad domain: `{audit.broad_domain_correct} / {audit.case_count}`",
        f"- Evaluator domain in top two: `{audit.evaluator_domain_top_two} / {audit.case_count}`",
        f"- Evidence-ref validity: `{audit.evidence_ref_validity:.3f}`",
        f"- Maximum discovery reads after common bootstrap: `{audit.maximum_discovery_reads}`",
        f"- Provider calls: `{audit.provider_calls}`",
        "",
        "| Case | Root | Domain | Top two | Status | Reads |",
        "|---|---:|---:|---|---|---:|",
    ]
    for item in audit.entries:
        lines.append(
            "| "
            f"{item.case_id} | {int(item.selected_root_correct)} | "
            f"{int(item.broad_domain_correct)} | "
            f"{', '.join(value.value for value in item.top_two_domains)} | "
            f"{item.projection.status.value} | {len(item.discovery_reads)} |"
        )
    return "\n".join(lines) + "\n"


__all__ = (
    "DomainAuditEntryV233",
    "DomainAuditV233",
    "DomainDevelopmentReadReasonV233",
    "DomainDevelopmentReadV233",
    "build_v232_domain_audit_v233",
    "project_development_case_v233",
    "render_domain_audit_markdown_v233",
)
