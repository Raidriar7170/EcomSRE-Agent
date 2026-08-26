"""Isolated shadow evaluator and promotion gate for DTA v2.3.4."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.extension_runtime_v234 import (
    ExtensionRuntimeInputV234,
    ExtensionSupportDecisionV234,
    ExtensionSupportPolicyV234,
    build_extension_replay_input_v234,
)
from ecomsre.dta_v2.v23.registration_compiler_v234 import (
    CompiledFaultRegistrationV234,
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import hashed_model_v234
from ecomsre.dta_v2.v23.registration_review_v234 import (
    OntologyDraftReviewDecisionV234,
    OntologyDraftReviewRecordV234,
)
from ecomsre.dta_v2.v23.ontology_expansion_v234 import AcceptedReportBindingV234
from ecomsre.dta_v2.v23.review_registry import ReviewQueueItemV23, ShadowFaultEntryV23


class ExtensionShadowEvaluationStratumV234(str, Enum):
    POSITIVE_INCIDENT = "POSITIVE_INCIDENT"
    CONFUSABLE_CORE_KNOWN = "CONFUSABLE_CORE_KNOWN"
    OTHER_EXTENSION = "OTHER_EXTENSION"
    NO_INCIDENT = "NO_INCIDENT"
    INSUFFICIENT_OR_CONFLICT = "INSUFFICIENT_OR_CONFLICT"
    TARGET_SERVICE_COUNTERFACTUAL = "TARGET_SERVICE_COUNTERFACTUAL"
    SOURCE_FAILURE = "SOURCE_FAILURE"


class ExtensionShadowEvaluationStatusV234(str, Enum):
    PROMOTION_READY = "PROMOTION_READY"
    RETAINED_FAILED = "RETAINED_FAILED"


class ExtensionShadowEvaluationCaseV234(DtaModelV22):
    schema_version: Literal["dta-v234.extension-shadow-case.v1"]
    evaluation_case_id: str
    stratum: ExtensionShadowEvaluationStratumV234
    runtime_input: ExtensionRuntimeInputV234
    target_services: tuple[str, ...] = Field(min_length=1)
    expected_match: StrictBool
    expected_root_service: str | None
    case_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_case(self) -> "ExtensionShadowEvaluationCaseV234":
        if self.target_services != tuple(sorted(set(self.target_services))):
            raise ValueError("shadow evaluation targets are not canonical")
        if not set(self.target_services).issubset(self.runtime_input.candidate_services):
            raise ValueError("shadow evaluation target escapes runtime input")
        if self.expected_match != (self.expected_root_service is not None):
            raise ValueError("shadow expected root differs from match expectation")
        if self.expected_root_service is not None and self.expected_root_service not in self.target_services:
            raise ValueError("shadow expected root escapes selected targets")
        if self.stratum is ExtensionShadowEvaluationStratumV234.POSITIVE_INCIDENT:
            if not self.expected_match:
                raise ValueError("positive shadow case must expect a match")
        elif self.expected_match:
            raise ValueError("negative shadow stratum cannot expect a match")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"case_sha256"})
        )
        if self.case_sha256 != expected:
            raise ValueError("shadow evaluation case digest differs")
        return self


class ExtensionShadowCaseOutcomeV234(DtaModelV22):
    schema_version: Literal["dta-v234.extension-shadow-case-outcome.v1"]
    evaluation_case_id: str
    evaluation_case_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stratum: ExtensionShadowEvaluationStratumV234
    expected_match: StrictBool
    selected_decision: ExtensionSupportDecisionV234 | None
    matched: StrictBool
    root_localization_correct: StrictBool
    evidence_refs_valid: StrictBool
    source_reachable: StrictBool
    action_authority_violations: Literal[0]
    outcome_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_outcome(self) -> "ExtensionShadowCaseOutcomeV234":
        if self.matched != (self.selected_decision is not None):
            raise ValueError("shadow outcome match differs from decision")
        if self.selected_decision is not None and not self.selected_decision.admitted:
            raise ValueError("shadow outcome selected a failed decision")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"outcome_sha256"})
        )
        if self.outcome_sha256 != expected:
            raise ValueError("shadow evaluation outcome digest differs")
        return self


class ExtensionShadowEvaluationResultV234(DtaModelV22):
    schema_version: Literal["dta-v234.extension-shadow-evaluation.v1"]
    evaluation_id: str = Field(pattern=r"^shadow-evaluation-v234-[0-9a-f]{16}$")
    registration_id: str
    source_draft_id: str
    source_draft_sha256: str
    source_compiled_sha256: str
    draft_review_sha256: str
    shadow_fault_id: str = Field(pattern=r"^shadow-v23-[0-9a-f]{16}$")
    shadow_entry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    accepted_positive_report_ids: tuple[str, ...] = Field(min_length=1)
    accepted_positive_report_bindings: tuple[AcceptedReportBindingV234, ...] = Field(
        min_length=1
    )
    accepted_positive_report_count: StrictInt = Field(ge=0)
    positive_replay_case_count: StrictInt = Field(ge=0)
    positive_recall: StrictFloat = Field(ge=0.0, le=1.0)
    false_positive_rate: StrictFloat = Field(ge=0.0, le=1.0)
    root_localization: StrictFloat = Field(ge=0.0, le=1.0)
    core_known_overlap: StrictInt = Field(ge=0)
    no_incident_regression: StrictInt = Field(ge=0)
    other_extension_destructive_overlap: StrictInt = Field(ge=0)
    evidence_ref_validity: StrictFloat = Field(ge=0.0, le=1.0)
    source_reachability: StrictFloat = Field(ge=0.0, le=1.0)
    counterfactual_consistency: StrictFloat = Field(ge=0.0, le=1.0)
    source_failure_safe: StrictFloat = Field(ge=0.0, le=1.0)
    action_authority_violations: Literal[0]
    status: ExtensionShadowEvaluationStatusV234
    reviewer: str
    simulation: StrictBool
    human_review_label: Literal["SIMULATED HUMAN REVIEW"] | None
    outcomes: tuple[ExtensionShadowCaseOutcomeV234, ...]
    evaluated_at: datetime
    shadow_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_result(self) -> "ExtensionShadowEvaluationResultV234":
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() != timedelta(0):
            raise ValueError("shadow evaluation timestamp must be UTC")
        if self.simulation != (self.human_review_label == "SIMULATED HUMAN REVIEW"):
            raise ValueError("shadow evaluation simulation label differs")
        ids = tuple(item.evaluation_case_id for item in self.outcomes)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("shadow evaluation outcomes are not canonical")
        if self.accepted_positive_report_ids != tuple(
            sorted(set(self.accepted_positive_report_ids))
        ):
            raise ValueError("accepted positive report IDs are not canonical")
        if self.accepted_positive_report_count != len(
            self.accepted_positive_report_ids
        ):
            raise ValueError("accepted positive report count differs from bound IDs")
        if tuple(
            item.report_id for item in self.accepted_positive_report_bindings
        ) != self.accepted_positive_report_ids:
            raise ValueError("accepted positive report bindings differ from IDs")
        positive_inputs = tuple(
            item.runtime_input_sha256
            for item in self.outcomes
            if item.stratum is ExtensionShadowEvaluationStratumV234.POSITIVE_INCIDENT
        )
        if len(positive_inputs) != len(set(positive_inputs)):
            raise ValueError("positive shadow replay inputs are not disjoint")
        if self.positive_replay_case_count != len(set(positive_inputs)):
            raise ValueError("positive replay count differs from disjoint inputs")
        evidence_minimum = self.accepted_positive_report_count >= 2 or (
            self.accepted_positive_report_count >= 1
            and self.positive_replay_case_count >= 3
        )
        promotion_ready = all(
            (
                evidence_minimum,
                self.positive_recall >= 0.75,
                self.false_positive_rate <= 0.10,
                self.core_known_overlap == 0,
                self.no_incident_regression == 0,
                self.other_extension_destructive_overlap == 0,
                self.evidence_ref_validity == 1.0,
                self.source_reachability == 1.0,
                self.counterfactual_consistency >= 0.80,
                self.source_failure_safe == 1.0,
                self.action_authority_violations == 0,
            )
        )
        expected_status = (
            ExtensionShadowEvaluationStatusV234.PROMOTION_READY
            if promotion_ready
            else ExtensionShadowEvaluationStatusV234.RETAINED_FAILED
        )
        if self.status is not expected_status:
            raise ValueError("shadow evaluation promotion status differs from metrics")
        expected_id = f"shadow-evaluation-v234-{semantic_sha256_v22({'registration_id': self.registration_id, 'source_compiled_sha256': self.source_compiled_sha256, 'draft_review_sha256': self.draft_review_sha256, 'outcomes': tuple(item.outcome_sha256 for item in self.outcomes)})[:16]}"
        if self.evaluation_id != expected_id:
            raise ValueError("shadow evaluation identity differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"shadow_result_sha256"})
        )
        if self.shadow_result_sha256 != expected:
            raise ValueError("shadow evaluation digest differs")
        return self


def build_shadow_evaluation_case_v234(
    *,
    evaluation_case_id: str,
    stratum: ExtensionShadowEvaluationStratumV234,
    runtime_input: ExtensionRuntimeInputV234,
    target_services: tuple[str, ...] | None = None,
    expected_root_service: str | None = None,
) -> ExtensionShadowEvaluationCaseV234:
    selected = runtime_input.candidate_services if target_services is None else target_services
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.extension-shadow-case.v1",
        "evaluation_case_id": evaluation_case_id,
        "stratum": stratum,
        "runtime_input": runtime_input,
        "target_services": tuple(sorted(set(selected))),
        "expected_match": expected_root_service is not None,
        "expected_root_service": expected_root_service,
    }
    return hashed_model_v234(
        ExtensionShadowEvaluationCaseV234,
        payload,
        "case_sha256",
    )


def evaluate_extension_shadow_v234(
    *,
    compiled: CompiledFaultRegistrationV234,
    draft_review: OntologyDraftReviewRecordV234,
    shadow: ShadowFaultEntryV23,
    accepted_reports: tuple[ReviewQueueItemV23, ...],
    cases: tuple[ExtensionShadowEvaluationCaseV234, ...],
    evaluated_at: datetime,
) -> ExtensionShadowEvaluationResultV234:
    if (
        draft_review.decision
        is not OntologyDraftReviewDecisionV234.APPROVE_SHADOW_EVALUATION
        or draft_review.draft_id != compiled.source_draft_id
        or draft_review.draft_sha256 != compiled.source_draft_sha256
        or draft_review.validation_sha256 != compiled.source_validation_sha256
        or draft_review.shadow_fault_id != shadow.shadow_fault_id
    ):
        raise ValueError("shadow evaluation lacks a bound human draft approval")
    if shadow.positive_report_ids != tuple(sorted(set(shadow.positive_report_ids))):
        raise ValueError("shadow positive report IDs are not canonical")
    canonical_reports = tuple(
        sorted(accepted_reports, key=lambda item: item.report.report_id)
    )
    if tuple(item.report.report_id for item in canonical_reports) != (
        shadow.positive_report_ids
    ):
        raise ValueError("accepted report artifacts differ from the Shadow entry")
    accepted_report_bindings = tuple(
        AcceptedReportBindingV234(
            report_id=item.report.report_id,
            source_case_id=item.source_case_id,
            report_sha256=item.report.report_sha256,
            queue_item_sha256=item.queue_item_sha256,
        )
        for item in canonical_reports
    )
    required = set(ExtensionShadowEvaluationStratumV234)
    if {item.stratum for item in cases} != required:
        raise ValueError("shadow evaluation does not cover every required stratum")
    ids = tuple(item.evaluation_case_id for item in cases)
    if ids != tuple(sorted(set(ids))):
        raise ValueError("shadow evaluation cases are not canonical")
    positive_runtime_inputs = tuple(
        item.runtime_input.runtime_input_sha256
        for item in cases
        if item.stratum is ExtensionShadowEvaluationStratumV234.POSITIVE_INCIDENT
    )
    if len(positive_runtime_inputs) != len(set(positive_runtime_inputs)):
        raise ValueError("positive shadow replay inputs are not disjoint")
    required_sources = {item.evidence_source for item in compiled.predicates}
    outcomes = []
    for case in cases:
        decisions = ExtensionSupportPolicyV234().evaluate(
            registration=compiled,
            runtime_input=case.runtime_input,
            target_services=case.target_services,
        )
        selected = next((item for item in decisions if item.admitted), None)
        refs = {item.evidence_ref for item in case.runtime_input.memory.evidence_refs}
        evidence_valid = selected is None or set(selected.supporting_evidence_refs).issubset(refs)
        source_reachable = all(
            case.runtime_input.source_is_reachable(source)
            for source in required_sources
        )
        root_correct = (
            not case.expected_match
            if selected is None
            else selected.target_service == case.expected_root_service
        )
        payload: dict[str, Any] = {
            "schema_version": "dta-v234.extension-shadow-case-outcome.v1",
            "evaluation_case_id": case.evaluation_case_id,
            "evaluation_case_sha256": case.case_sha256,
            "runtime_input_sha256": case.runtime_input.runtime_input_sha256,
            "stratum": case.stratum,
            "expected_match": case.expected_match,
            "selected_decision": selected,
            "matched": selected is not None,
            "root_localization_correct": root_correct,
            "evidence_refs_valid": evidence_valid,
            "source_reachable": source_reachable,
            "action_authority_violations": 0,
        }
        outcomes.append(
            hashed_model_v234(
                ExtensionShadowCaseOutcomeV234,
                payload,
                "outcome_sha256",
            )
        )
    outcomes_tuple = tuple(sorted(outcomes, key=lambda item: item.evaluation_case_id))
    positives = tuple(
        item
        for item in outcomes_tuple
        if item.stratum is ExtensionShadowEvaluationStratumV234.POSITIVE_INCIDENT
    )
    negatives = tuple(
        item
        for item in outcomes_tuple
        if item.stratum is not ExtensionShadowEvaluationStratumV234.POSITIVE_INCIDENT
    )
    counterfactuals = tuple(
        item
        for item in outcomes_tuple
        if item.stratum
        is ExtensionShadowEvaluationStratumV234.TARGET_SERVICE_COUNTERFACTUAL
    )
    source_failures = tuple(
        item
        for item in outcomes_tuple
        if item.stratum is ExtensionShadowEvaluationStratumV234.SOURCE_FAILURE
    )
    positive_recall = sum(item.matched for item in positives) / len(positives)
    false_positive_rate = sum(item.matched for item in negatives) / len(negatives)
    root_localization = sum(item.root_localization_correct for item in positives) / len(positives)
    evidence_ref_validity = sum(item.evidence_refs_valid for item in outcomes_tuple) / len(outcomes_tuple)
    source_reachability = sum(item.source_reachable for item in positives) / len(positives)
    counterfactual_consistency = sum(not item.matched for item in counterfactuals) / len(counterfactuals)
    source_failure_safe = sum(not item.matched for item in source_failures) / len(source_failures)
    core_overlap = sum(
        item.matched
        for item in outcomes_tuple
        if item.stratum is ExtensionShadowEvaluationStratumV234.CONFUSABLE_CORE_KNOWN
    )
    no_incident_regression = sum(
        item.matched
        for item in outcomes_tuple
        if item.stratum is ExtensionShadowEvaluationStratumV234.NO_INCIDENT
    )
    other_extension_overlap = sum(
        item.matched
        for item in outcomes_tuple
        if item.stratum is ExtensionShadowEvaluationStratumV234.OTHER_EXTENSION
    )
    accepted_positive_report_count = len(shadow.positive_report_ids)
    positive_replay_case_count = len(set(positive_runtime_inputs))
    evidence_minimum = accepted_positive_report_count >= 2 or (
        accepted_positive_report_count >= 1 and positive_replay_case_count >= 3
    )
    ready = all(
        (
            evidence_minimum,
            positive_recall >= 0.75,
            false_positive_rate <= 0.10,
            core_overlap == 0,
            no_incident_regression == 0,
            other_extension_overlap == 0,
            evidence_ref_validity == 1.0,
            source_reachability == 1.0,
            counterfactual_consistency >= 0.80,
            source_failure_safe == 1.0,
        )
    )
    identity = {
        "registration_id": compiled.registration_id,
        "source_compiled_sha256": compiled.compiled_sha256,
        "draft_review_sha256": draft_review.review_sha256,
        "outcomes": tuple(item.outcome_sha256 for item in outcomes_tuple),
    }
    payload = {
        "schema_version": "dta-v234.extension-shadow-evaluation.v1",
        "evaluation_id": (
            f"shadow-evaluation-v234-{semantic_sha256_v22(identity)[:16]}"
        ),
        "registration_id": compiled.registration_id,
        "source_draft_id": compiled.source_draft_id,
        "source_draft_sha256": compiled.source_draft_sha256,
        "source_compiled_sha256": compiled.compiled_sha256,
        "draft_review_sha256": draft_review.review_sha256,
        "shadow_fault_id": shadow.shadow_fault_id,
        "shadow_entry_sha256": shadow.entry_sha256,
        "accepted_positive_report_ids": shadow.positive_report_ids,
        "accepted_positive_report_bindings": accepted_report_bindings,
        "accepted_positive_report_count": accepted_positive_report_count,
        "positive_replay_case_count": positive_replay_case_count,
        "positive_recall": positive_recall,
        "false_positive_rate": false_positive_rate,
        "root_localization": root_localization,
        "core_known_overlap": core_overlap,
        "no_incident_regression": no_incident_regression,
        "other_extension_destructive_overlap": other_extension_overlap,
        "evidence_ref_validity": evidence_ref_validity,
        "source_reachability": source_reachability,
        "counterfactual_consistency": counterfactual_consistency,
        "source_failure_safe": source_failure_safe,
        "action_authority_violations": 0,
        "status": (
            ExtensionShadowEvaluationStatusV234.PROMOTION_READY
            if ready
            else ExtensionShadowEvaluationStatusV234.RETAINED_FAILED
        ),
        "reviewer": draft_review.reviewer,
        "simulation": draft_review.simulation,
        "human_review_label": (
            "SIMULATED HUMAN REVIEW" if draft_review.simulation else None
        ),
        "outcomes": outcomes_tuple,
        "evaluated_at": evaluated_at,
    }
    return hashed_model_v234(
        ExtensionShadowEvaluationResultV234,
        payload,
        "shadow_result_sha256",
    )


def evaluate_increment3_development_shadow_v234(
    *,
    repository_root: Path,
    compiled: CompiledFaultRegistrationV234,
    draft_review: OntologyDraftReviewRecordV234,
    shadow: ShadowFaultEntryV23,
    accepted_reports: tuple[ReviewQueueItemV23, ...],
    evaluated_at: datetime,
) -> ExtensionShadowEvaluationResultV234:
    cases = build_increment3_development_shadow_cases_v234(
        repository_root=repository_root,
        compiled=compiled,
    )
    return evaluate_extension_shadow_v234(
        compiled=compiled,
        draft_review=draft_review,
        shadow=shadow,
        accepted_reports=accepted_reports,
        cases=cases,
        evaluated_at=evaluated_at,
    )


def build_increment3_development_shadow_cases_v234(
    *,
    repository_root: Path,
    compiled: CompiledFaultRegistrationV234,
) -> tuple[ExtensionShadowEvaluationCaseV234, ...]:
    runtime_inputs = {
        case_id: build_extension_replay_input_v234(
            repository_root=repository_root,
            case_id=case_id,
        )
        for case_id in (
            "vx-312",
            "vx-313",
            "vx-317",
            "vx-321",
            "vx-324",
            "vx-328",
        )
    }
    positive = runtime_inputs["vx-312"]
    selected = ExtensionSupportPolicyV234().evaluate(
        registration=compiled,
        runtime_input=positive,
    )
    admitted = next((item for item in selected if item.admitted), None)
    if admitted is None:
        raise ValueError("vx-312 development positive is not admitted")
    wrong_target = next(
        item for item in positive.candidate_services if item != admitted.target_service
    )
    cases = (
        build_shadow_evaluation_case_v234(
            evaluation_case_id="dev-confusable-core-vx-317",
            stratum=ExtensionShadowEvaluationStratumV234.CONFUSABLE_CORE_KNOWN,
            runtime_input=runtime_inputs["vx-317"],
        ),
        build_shadow_evaluation_case_v234(
            evaluation_case_id="dev-counterfactual-vx-312",
            stratum=ExtensionShadowEvaluationStratumV234.TARGET_SERVICE_COUNTERFACTUAL,
            runtime_input=positive,
            target_services=(wrong_target,),
        ),
        build_shadow_evaluation_case_v234(
            evaluation_case_id="dev-insufficient-conflict-vx-324",
            stratum=ExtensionShadowEvaluationStratumV234.INSUFFICIENT_OR_CONFLICT,
            runtime_input=runtime_inputs["vx-324"],
        ),
        build_shadow_evaluation_case_v234(
            evaluation_case_id="dev-no-incident-vx-321",
            stratum=ExtensionShadowEvaluationStratumV234.NO_INCIDENT,
            runtime_input=runtime_inputs["vx-321"],
        ),
        build_shadow_evaluation_case_v234(
            evaluation_case_id="dev-other-extension-vx-313",
            stratum=ExtensionShadowEvaluationStratumV234.OTHER_EXTENSION,
            runtime_input=runtime_inputs["vx-313"],
        ),
        build_shadow_evaluation_case_v234(
            evaluation_case_id="dev-positive-vx-312",
            stratum=ExtensionShadowEvaluationStratumV234.POSITIVE_INCIDENT,
            runtime_input=positive,
            expected_root_service=admitted.target_service,
        ),
        build_shadow_evaluation_case_v234(
            evaluation_case_id="dev-source-failure-vx-328",
            stratum=ExtensionShadowEvaluationStratumV234.SOURCE_FAILURE,
            runtime_input=runtime_inputs["vx-328"],
        ),
    )
    return cases


__all__ = (
    "ExtensionShadowCaseOutcomeV234",
    "ExtensionShadowEvaluationCaseV234",
    "ExtensionShadowEvaluationResultV234",
    "ExtensionShadowEvaluationStatusV234",
    "ExtensionShadowEvaluationStratumV234",
    "build_shadow_evaluation_case_v234",
    "build_increment3_development_shadow_cases_v234",
    "evaluate_extension_shadow_v234",
    "evaluate_increment3_development_shadow_v234",
)
