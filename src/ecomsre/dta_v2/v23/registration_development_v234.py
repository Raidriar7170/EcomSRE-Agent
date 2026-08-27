"""Deterministic vx-311 development registration flow for DTA v2.3.4."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import model_validator

from ecomsre.dta_v2.v22.action_catalog import StaticTopologyV22
from ecomsre.dta_v2.v22.memory import build_memory_views_v22
from ecomsre.dta_v2.v22.practical_runner import _baseline, _bootstrap
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import QuerySpecificReplayBackendV22
from ecomsre.dta_v2.v23.contracts import (
    ProvisionalFaultDomainV23,
    build_provisional_report_v23,
)
from ecomsre.dta_v2.v23.evaluation_data_v233 import load_evaluation_cases_v233
from ecomsre.dta_v2.v23.evaluation_v231 import materialize_evaluation_case_v231
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    extract_generic_anomalies_v23,
)
from ecomsre.dta_v2.v23.ontology_expansion_v234 import (
    DraftGenerationAuthorizationResultV234,
    LocalOntologyExpansionStoreV234,
)
from ecomsre.dta_v2.v23.registration_compiler_v234 import (
    CompiledFaultRegistrationV234,
    RegistrationPatchBundleV234,
    compile_registration_v234,
    render_registration_patch_bundle_v234,
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    FormalFaultRegistrationDraftV234,
    hashed_model_v234,
)
from ecomsre.dta_v2.v23.registration_provider_v234 import (
    RegistrationDraftProviderV234,
)
from ecomsre.dta_v2.v23.registration_store_v234 import (
    LocalRegistrationDraftStoreV234,
)
from ecomsre.dta_v2.v23.registration_validator_v234 import (
    DraftValidationStatusV234,
    RegistrationDraftValidationV234,
    validate_registration_draft_v234,
)
from ecomsre.dta_v2.v23.extension_registry_v234 import (
    ExtensionOntologyEntryV234,
    ExtensionOntologyRegistryV234,
    LocalExtensionOntologyStoreV234,
    OntologyPromotionDecisionV234,
    OntologyPromotionReviewRecordV234,
)
from ecomsre.dta_v2.v23.extension_runtime_v234 import (
    ExtensionAdmittedDiagnosisV234,
    ExtensionDiagnosisRouteV234,
    diagnose_extension_enabled_v234,
)
from ecomsre.dta_v2.v23.registration_evaluator_v234 import (
    ExtensionShadowEvaluationResultV234,
    ExtensionShadowEvaluationStatusV234,
    evaluate_increment3_development_shadow_v234,
)
from ecomsre.dta_v2.v23.registration_review_v234 import (
    OntologyDraftReviewDecisionV234,
    OntologyDraftReviewRecordV234,
    build_ontology_draft_review_v234,
)
from ecomsre.dta_v2.v23.residual_graph import (
    build_residual_evidence_graph_v23,
)
from ecomsre.dta_v2.v23.review_registry import (
    HumanReviewDecisionV23,
    LocalReviewStoreV23,
    ReviewQueueItemV23,
    ShadowFaultEntryV23,
    TEST_REVIEWER_V23,
    build_review_queue_item_v23,
)


VX311_DEVELOPMENT_EVIDENCE_REFS_V234 = (
    "e:a:logs:svc-28037ae9fb:0:0cfba90b96b8",
    "e:a:metrics:svc-28037ae9fb:core:0:887ff5bde1c6",
    "e:a:metrics:svc-28037ae9fb:core:1:86618e2797ec",
)


class Increment2DevelopmentDemoV234(DtaModelV22):
    schema_version: Literal["dta-v234.increment2-development-demo.v1"]
    source_case_id: Literal["vx-311"]
    review_queue_item: ReviewQueueItemV23
    shadow_fault: ShadowFaultEntryV23
    authorization: DraftGenerationAuthorizationResultV234
    formal_draft: FormalFaultRegistrationDraftV234
    validation: RegistrationDraftValidationV234
    compiled_registration: CompiledFaultRegistrationV234
    patch_bundle: RegistrationPatchBundleV234
    open_world_provider_calls: Literal[0]
    registration_provider_calls: Literal[0]
    action_authority: Literal["NONE"]
    remediation_registration: Literal["NOT_INCLUDED"]
    demo_sha256: str

    @model_validator(mode="after")
    def require_demo(self) -> "Increment2DevelopmentDemoV234":
        if self.validation.status is not DraftValidationStatusV234.VALID:
            raise ValueError("increment-2 development demo validation is not valid")
        if (
            self.formal_draft.provider_trace.provider_calls != 0
            or self.patch_bundle.source_draft_id != self.formal_draft.draft_id
        ):
            raise ValueError("increment-2 development demo bindings differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"demo_sha256"})
        )
        if self.demo_sha256 != expected:
            raise ValueError("increment-2 development demo digest differs")
        return self


class Increment3DevelopmentDemoV234(DtaModelV22):
    schema_version: Literal["dta-v234.increment3-development-demo.v1"]
    source_registration_case_id: Literal["vx-311"]
    disjoint_diagnosis_case_id: Literal["vx-312"]
    draft_review: OntologyDraftReviewRecordV234
    shadow_evaluation: ExtensionShadowEvaluationResultV234
    promotion_record: OntologyPromotionReviewRecordV234
    extension_entry: ExtensionOntologyEntryV234
    extension_registry: ExtensionOntologyRegistryV234
    diagnosis: ExtensionAdmittedDiagnosisV234
    accepted_positive_report_count: Literal[2]
    open_world_provider_calls: Literal[0]
    registration_provider_calls: Literal[0]
    action_authority: Literal["NONE"]
    remediation_registration: Literal["NOT_INCLUDED"]
    human_review_label: Literal["SIMULATED HUMAN REVIEW"]
    demo_sha256: str

    @model_validator(mode="after")
    def require_demo(self) -> "Increment3DevelopmentDemoV234":
        if (
            not self.draft_review.simulation
            or not self.promotion_record.simulation
            or self.shadow_evaluation.status
            is not ExtensionShadowEvaluationStatusV234.PROMOTION_READY
            or self.extension_entry.status != "ACTIVE"
            or self.extension_registry.entries != (self.extension_entry,)
            or self.diagnosis.registration_id != self.extension_entry.registration_id
        ):
            raise ValueError("increment-3 development demo bindings differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"demo_sha256"})
        )
        if self.demo_sha256 != expected:
            raise ValueError("increment-3 development demo digest differs")
        return self


def _build_connection_pool_review_item_for_case_v234(
    *,
    repository_root: Path,
    case_id: Literal["vx-311", "vx-312"],
    queued_at: datetime,
) -> ReviewQueueItemV23:
    """Replay committed connection-pool reads into a simulated v2.3 report."""

    cases = load_evaluation_cases_v233(
        repository_root / "config/dta-v233/evaluation/cases.json"
    )
    spec = cases.require(case_id)
    case = materialize_evaluation_case_v231(
        repository_root=repository_root,
        spec=spec,
    )
    topology = StaticTopologyV22.build(
        services=case.candidate_services,
        edges=case.topology_edges,
    )
    outcomes, _snapshot, _full, catalog = _bootstrap(
        case=case,
        topology=topology,
        run_id=semantic_sha256_v22(
            {"case_id": case_id, "lane": "dta-v234-development-registration"}
        )[:32],
    )
    matching_logs = tuple(
        item
        for item in case.capture.logs
        if "worker pool semaphore wait" in item.message.casefold()
    )
    if len({item.service for item in matching_logs}) != 1:
        raise ValueError(f"{case_id} lacks one connection-pool log target")
    root_service = matching_logs[0].service
    log_action = next(
        item
        for item in catalog.registry_actions
        if item.source is EvidenceSourceV22.LOGS
        and item.target_services == (root_service,)
    )
    log_outcome = QuerySpecificReplayBackendV22(case.capture).execute(log_action)
    memory, _ = build_memory_views_v22(
        outcomes=(*outcomes, log_outcome),  # type: ignore[arg-type]
        baseline=_baseline(case),
        observed_at=case.capture.captured_at,
        top_k=64,
    )
    selected_kinds = {
        GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN,
        GenericAnomalyKindV23.METRIC_ERROR_OUTLIER,
        GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER,
    }
    anomalies = tuple(
        item
        for item in extract_generic_anomalies_v23(
            memory=memory,
            candidate_services=case.candidate_services,
        )
        if item.service == root_service and item.kind in selected_kinds
    )
    if {item.kind for item in anomalies} != selected_kinds:
        raise ValueError(f"{case_id} development replay lacks connection-pool evidence")
    evidence_refs = tuple(
        sorted({ref for item in anomalies for ref in item.evidence_refs})
    )
    if case_id == "vx-311" and evidence_refs != VX311_DEVELOPMENT_EVIDENCE_REFS_V234:
        raise ValueError("vx-311 development evidence refs differ from frozen replay")
    graph = build_residual_evidence_graph_v23(
        candidate_services=case.candidate_services,
        generic_anomalies=anomalies,
        known_terminal_candidates=(),
        memory=memory,
    )
    residual_refs = {item.anomaly_id: item.evidence_refs for item in anomalies}
    report = build_provisional_report_v23(
        terminal="UNREGISTERED_INCIDENT_SUSPECTED",
        candidate_services=case.candidate_services,
        suspected_root_services=(root_service,),
        affected_services=case.candidate_services,
        broad_fault_domain=ProvisionalFaultDomainV23.CONCURRENCY,
        provisional_mechanism_label="connection pool exhaustion",
        mechanism_description=(
            "Concurrent work waits at a worker-pool semaphore while strong target "
            "error-rate and latency evidence isolates local pool admission pressure."
        ),
        observed_symptoms=(
            "error rate and latency are strong at the selected target",
            "worker pool semaphore wait observed under load",
        ),
        supporting_evidence_refs=evidence_refs,
        contradicting_evidence_refs=(),
        unexplained_anomaly_ids=tuple(
            sorted(item.anomaly_id for item in anomalies)
        ),
        alternative_hypotheses=(
            "DEPENDENCY_LATENCY",
            "SERVICE_UNAVAILABLE",
        ),
        recommended_next_observations=(),
        confidence=0.76,
        memory=memory,
        residual_anomaly_refs=residual_refs,
    )
    return build_review_queue_item_v23(
        report=report,
        graph=graph,
        source_case_id=f"dta-v234-{case_id}-connection-pool-development",
        queued_at=queued_at,
        automated_fixture=True,
    )


def build_connection_pool_review_item_v234(
    *,
    repository_root: Path,
    queued_at: datetime,
) -> ReviewQueueItemV23:
    """Replay committed vx-311 reads; create a new simulated v2.3 report."""

    return _build_connection_pool_review_item_for_case_v234(
        repository_root=repository_root,
        case_id="vx-311",
        queued_at=queued_at,
    )


def run_increment2_development_demo_v234(
    *,
    repository_root: Path,
    local_root: Path,
    run_at: datetime,
) -> Increment2DevelopmentDemoV234:
    item = build_connection_pool_review_item_v234(
        repository_root=repository_root,
        queued_at=run_at,
    )
    review_store = LocalReviewStoreV23(local_root)
    review_store.enqueue(item)
    accepted = review_store.decide(
        report_id=item.report.report_id,
        decision=HumanReviewDecisionV23.ACCEPT_AS_NEW,
        reviewer=TEST_REVIEWER_V23,
        review_note=(
            "SIMULATED HUMAN REVIEW: accept the vx-311 development incident as a "
            "new connection-pool fault."
        ),
        canonical_label="connection-pool-exhaustion",
        merge_target=None,
        requested_observations=(),
        reviewed_at=run_at,
    )
    if accepted.shadow_entry is None:
        raise ValueError("increment-2 development review did not create a Shadow Fault")
    ontology_store = LocalOntologyExpansionStoreV234(local_root)
    authorization = ontology_store.authorize_draft_generation(
        shadow_fault_id=accepted.shadow_entry.shadow_fault_id,
        reviewer=TEST_REVIEWER_V23,
        authorization_note=(
            "SIMULATED HUMAN REVIEW: generate a formal registration draft only."
        ),
        authorized_at=run_at,
    )
    draft = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=accepted.shadow_entry,
        accepted_reports=(item,),
    )
    validation = validate_registration_draft_v234(
        draft=draft,
        authorization_context=authorization,
        shadow=accepted.shadow_entry,
        accepted_reports=(item,),
        promoted_mechanism_slugs=(),
        shadow_mechanism_slugs=(),
    )
    compiled = compile_registration_v234(
        draft=draft,
        validation=validation,
        snapshot=authorization.core_ontology_snapshot,
    )
    draft_store = LocalRegistrationDraftStoreV234(local_root)
    draft_store.save_draft(draft)
    draft_store.record_draft_generated(
        context=authorization,
        draft=draft,
        transitioned_at=run_at,
    )
    draft_store.save_validation(validation)
    draft_store.record_validation(
        context=authorization,
        draft=draft,
        validation=validation,
        transitioned_at=run_at,
    )
    bundle = render_registration_patch_bundle_v234(
        compiled=compiled,
        output_root=draft_store.bundles_dir,
    )
    draft_store.record_patch_rendered(
        context=authorization,
        draft=draft,
        validation=validation,
        compiled=compiled,
        bundle=bundle,
        transitioned_at=run_at,
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.increment2-development-demo.v1",
        "source_case_id": "vx-311",
        "review_queue_item": item,
        "shadow_fault": accepted.shadow_entry,
        "authorization": authorization,
        "formal_draft": draft,
        "validation": validation,
        "compiled_registration": compiled,
        "patch_bundle": bundle,
        "open_world_provider_calls": 0,
        "registration_provider_calls": 0,
        "action_authority": "NONE",
        "remediation_registration": "NOT_INCLUDED",
    }
    return hashed_model_v234(
        Increment2DevelopmentDemoV234,
        payload,
        "demo_sha256",
    )


def run_increment3_development_demo_v234(
    *,
    repository_root: Path,
    local_root: Path,
    run_at: datetime,
) -> Increment3DevelopmentDemoV234:
    increment2, accepted_shadow = build_increment3_development_shadow_v234(
        repository_root=repository_root,
        local_root=local_root,
        run_at=run_at,
    )

    draft_review = build_ontology_draft_review_v234(
        draft=increment2.formal_draft,
        validation=increment2.validation,
        decision=OntologyDraftReviewDecisionV234.APPROVE_SHADOW_EVALUATION,
        reviewer=TEST_REVIEWER_V23,
        review_note=(
            "SIMULATED HUMAN REVIEW: approve isolated shadow evaluation only."
        ),
        requested_changes=(),
        reviewed_at=run_at,
    )
    shadow_result = evaluate_increment3_development_shadow_v234(
        repository_root=repository_root,
        compiled=increment2.compiled_registration,
        draft_review=draft_review,
        shadow=accepted_shadow,
        accepted_reports=tuple(
            LocalReviewStoreV23(local_root).load_item(report_id)
            for report_id in accepted_shadow.positive_report_ids
        ),
        evaluated_at=run_at,
    )
    extension_store = LocalExtensionOntologyStoreV234(local_root)
    extension_store.save_draft_review(draft_review)
    extension_store.save_shadow_result(shadow_result)
    entry, promotion = extension_store.promote(
        compiled=increment2.compiled_registration,
        validation=increment2.validation,
        draft_review=draft_review,
        shadow_result=shadow_result,
        shadow=accepted_shadow,
        decision=OntologyPromotionDecisionV234.PROMOTE_TO_EXTENSION_ONTOLOGY,
        reviewer=TEST_REVIEWER_V23,
        review_note=(
            "SIMULATED HUMAN REVIEW: promote the passing development registration."
        ),
        reviewed_at=run_at,
    )
    registry = extension_store.load_registry()
    routed = diagnose_extension_enabled_v234(
        repository_root=repository_root,
        case_id="vx-312",
        registry=registry,
        core_known_diagnosis=None,
        no_incident_admitted=False,
    )
    if (
        routed.route is not ExtensionDiagnosisRouteV234.EXTENSION
        or routed.extension_diagnosis is None
    ):
        raise ValueError("increment-3 disjoint case did not use the extension ontology")
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.increment3-development-demo.v1",
        "source_registration_case_id": "vx-311",
        "disjoint_diagnosis_case_id": "vx-312",
        "draft_review": draft_review,
        "shadow_evaluation": shadow_result,
        "promotion_record": promotion,
        "extension_entry": entry,
        "extension_registry": registry,
        "diagnosis": routed.extension_diagnosis,
        "accepted_positive_report_count": len(accepted_shadow.positive_report_ids),
        "open_world_provider_calls": routed.open_world_provider_calls,
        "registration_provider_calls": 0,
        "action_authority": "NONE",
        "remediation_registration": "NOT_INCLUDED",
        "human_review_label": "SIMULATED HUMAN REVIEW",
    }
    return hashed_model_v234(
        Increment3DevelopmentDemoV234,
        payload,
        "demo_sha256",
    )


def build_increment3_development_shadow_v234(
    *,
    repository_root: Path,
    local_root: Path,
    run_at: datetime,
) -> tuple[Increment2DevelopmentDemoV234, ShadowFaultEntryV23]:
    """Create the second accepted report and return its bound Shadow entry."""

    increment2 = run_increment2_development_demo_v234(
        repository_root=repository_root,
        local_root=local_root,
        run_at=run_at,
    )
    disjoint_item = _build_connection_pool_review_item_for_case_v234(
        repository_root=repository_root,
        case_id="vx-312",
        queued_at=run_at,
    )
    review_store = LocalReviewStoreV23(local_root)
    review_store.enqueue(disjoint_item)
    merged = review_store.decide(
        report_id=disjoint_item.report.report_id,
        decision=HumanReviewDecisionV23.MERGE_WITH_EXISTING,
        reviewer=TEST_REVIEWER_V23,
        review_note=(
            "SIMULATED HUMAN REVIEW: bind the disjoint vx-312 positive report to "
            "the connection-pool Shadow Fault."
        ),
        canonical_label=None,
        merge_target=increment2.shadow_fault.shadow_fault_id,
        requested_observations=(),
        reviewed_at=run_at,
    )
    if merged.shadow_entry is None or len(merged.shadow_entry.positive_report_ids) != 2:
        raise ValueError("increment-3 demo lacks two accepted positive reports")
    return increment2, merged.shadow_entry


__all__ = (
    "Increment2DevelopmentDemoV234",
    "Increment3DevelopmentDemoV234",
    "VX311_DEVELOPMENT_EVIDENCE_REFS_V234",
    "build_connection_pool_review_item_v234",
    "build_increment3_development_shadow_v234",
    "run_increment2_development_demo_v234",
    "run_increment3_development_demo_v234",
)
