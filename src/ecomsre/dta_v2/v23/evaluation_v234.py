"""Frozen data, deterministic preflight, and fixed study for DTA v2.3.4."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.action_catalog import StaticTopologyV22
from ecomsre.dta_v2.v22.memory import PredicateKindV22, build_memory_views_v22
from ecomsre.dta_v2.v22.practical_runner import _baseline, _bootstrap
from ecomsre.dta_v2.v22.predicates import (
    MechanismV22,
    RequirementServiceBindingV22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    MetricKindV22,
    MetricUnitV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import QuerySpecificReplayBackendV22
from ecomsre.dta_v2.v23.contracts import (
    ProvisionalFaultDomainV23,
    build_provisional_report_v23,
)
from ecomsre.dta_v2.v23.core_ontology_snapshot_v234 import (
    build_core_ontology_schema_snapshot_v234,
)
from ecomsre.dta_v2.v23.evaluation import _build_common_context_v23
from ecomsre.dta_v2.v23.evaluation_data_v233 import (
    load_evaluation_cases_v233,
)
from ecomsre.dta_v2.v23.evaluation_v231 import (
    materialize_evaluation_case_v231,
)
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    extract_generic_anomalies_v23,
)
from ecomsre.dta_v2.v23.ontology_expansion_v234 import (
    DraftGenerationAuthorizationResultV234,
    LocalOntologyExpansionStoreV234,
)
from ecomsre.dta_v2.v23.registration_compiler_v234 import (
    compile_registration_v234,
    render_registration_patch_bundle_v234,
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    CorePredicateReferenceRuleV234,
    FormalFaultRegistrationDraftV234,
    FormalPredicateDraftV234,
    GenericAnomalyKindRuleV234,
    MechanismProposalV234,
    MetricThresholdRuleV234,
    PredicateImplementationModeV234,
    PredicateRequirementDraftV234,
    RegistrationImplementationModeV234,
    RegistrationTestPlanV234,
    SupportClauseDraftV234,
    ThresholdComparisonV234,
    TraceDurationThresholdRuleV234,
    ResourceCpuThresholdRuleV234,
    ResourceMemorySlopeRuleV234,
    mechanism_distinguishing_summary_v234,
    mechanism_display_name_v234,
    mechanism_human_definition_v234,
    predicate_negative_example_v234,
    predicate_positive_example_v234,
    predicate_semantic_definition_v234,
    support_clause_rationale_v234,
)
from ecomsre.dta_v2.v23.registration_provider_v234 import (
    ProviderAuthoredRegistrationDraftV234,
    ProviderCoreOntologyViewV234,
    RegistrationDraftProviderRequestV234,
    RegistrationDraftProviderV234,
    build_provider_core_ontology_view_v234,
)
from ecomsre.dta_v2.v23.registration_validator_v234 import (
    DraftValidationStatusV234,
    validate_registration_draft_v234,
)
from ecomsre.dta_v2.v23.residual_graph import (
    build_residual_evidence_graph_v23,
)
from ecomsre.dta_v2.v23.review_registry import (
    HumanReviewDecisionV23,
    LocalReviewStoreV23,
    RegistrationDraftV23,
    ReviewQueueItemV23,
    ShadowFaultEntryV23,
    TEST_REVIEWER_V23,
    build_review_queue_item_v23,
)


class RegistrationTaskClassV234(str, Enum):
    HIDDEN_KNOWN = "HIDDEN_KNOWN"
    UNREGISTERED = "UNREGISTERED"
    DUPLICATE_CONTROL = "DUPLICATE_CONTROL"
    INSUFFICIENT_CONTROL = "INSUFFICIENT_CONTROL"


class EvaluationArmV234(str, Enum):
    V23_TEMPLATE_REGISTRATION_SEED = "V23_TEMPLATE_REGISTRATION_SEED"
    V234_LLM_FORMAL_REGISTRATION = "V234_LLM_FORMAL_REGISTRATION"


class MeasuredResultTerminalV234(str, Enum):
    EFFECT_OBSERVED = "DTA_V234_REGISTRATION_ASSISTANCE_EFFECT_OBSERVED"
    MIXED_RESULT = "DTA_V234_REGISTRATION_ASSISTANCE_MIXED_RESULT"
    NOT_OBSERVED = "DTA_V234_REGISTRATION_ASSISTANCE_NOT_OBSERVED"


class RegistrationTaskV234(DtaModelV22):
    task_id: str = Field(pattern=r"^rt-[0-9]{3}$")
    source_case_id: str = Field(pattern=r"^vx-[0-9]{3}$")
    source_case_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_class: RegistrationTaskClassV234
    provider_view_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_root_service: str
    broad_fault_domain: ProvisionalFaultDomainV23
    provisional_mechanism_label: str
    mechanism_description: str
    observed_symptoms: tuple[str, ...] = Field(min_length=1)
    selected_anomaly_kinds: tuple[GenericAnomalyKindV23, ...]
    provider_call_expected: bool
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_task(self) -> "RegistrationTaskV234":
        if self.observed_symptoms != tuple(sorted(set(self.observed_symptoms))):
            raise ValueError("v2.3.4 task symptoms are not canonical")
        if self.selected_anomaly_kinds != tuple(
            sorted(set(self.selected_anomaly_kinds), key=lambda item: item.value)
        ):
            raise ValueError("v2.3.4 task anomaly kinds are not canonical")
        expected_provider = self.task_class in {
            RegistrationTaskClassV234.HIDDEN_KNOWN,
            RegistrationTaskClassV234.UNREGISTERED,
        }
        if self.provider_call_expected != expected_provider:
            raise ValueError("v2.3.4 task Provider-call disposition differs")
        if expected_provider and len(self.selected_anomaly_kinds) < 2:
            raise ValueError("v2.3.4 incident task lacks two evidence signals")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"task_sha256"})
        )
        if self.task_sha256 != expected:
            raise ValueError("v2.3.4 task digest differs")
        return self


class RegistrationTaskSetV234(DtaModelV22):
    schema_version: Literal["dta-v234.registration-task-set.v1"]
    freeze_id: Literal["dta-v234-registration-assistance-freeze-20260826-a"]
    tasks: tuple[RegistrationTaskV234, ...] = Field(min_length=16, max_length=16)
    task_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_set(self) -> "RegistrationTaskSetV234":
        if tuple(item.task_id for item in self.tasks) != tuple(
            f"rt-{ordinal:03d}" for ordinal in range(1, 17)
        ):
            raise ValueError("v2.3.4 task IDs differ")
        counts = {
            kind: sum(item.task_class is kind for item in self.tasks)
            for kind in RegistrationTaskClassV234
        }
        if counts != {
            RegistrationTaskClassV234.HIDDEN_KNOWN: 10,
            RegistrationTaskClassV234.UNREGISTERED: 4,
            RegistrationTaskClassV234.DUPLICATE_CONTROL: 1,
            RegistrationTaskClassV234.INSUFFICIENT_CONTROL: 1,
        }:
            raise ValueError("v2.3.4 task composition differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"task_set_sha256"})
        )
        if self.task_set_sha256 != expected:
            raise ValueError("v2.3.4 task-set digest differs")
        return self

    def require(self, task_id: str) -> RegistrationTaskV234:
        task = next((item for item in self.tasks if item.task_id == task_id), None)
        if task is None:
            raise ValueError("v2.3.4 registration task is absent")
        return task


class RegistrationTruthV234(DtaModelV22):
    task_id: str
    target_mechanism: MechanismV22 | None
    target_mechanism_slug: str | None
    expected_broad_fault_domain: ProvisionalFaultDomainV23 | None
    expected_implementation_mode: RegistrationImplementationModeV234
    expected_core_clause_ids: tuple[str, ...]
    declarative_compilation_expected: bool


class RegistrationTruthSetV234(DtaModelV22):
    schema_version: Literal["dta-v234.registration-truth-set.v1"]
    truths: tuple[RegistrationTruthV234, ...] = Field(min_length=16, max_length=16)
    truth_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_set(self) -> "RegistrationTruthSetV234":
        if tuple(item.task_id for item in self.truths) != tuple(
            f"rt-{ordinal:03d}" for ordinal in range(1, 17)
        ):
            raise ValueError("v2.3.4 truth IDs differ")
        if sum(item.target_mechanism is not None for item in self.truths) != 10:
            raise ValueError("v2.3.4 hidden-known truth composition differs")
        if sum(item.declarative_compilation_expected for item in self.truths) != 3:
            raise ValueError("v2.3.4 declarative-new truth composition differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"truth_sha256"})
        )
        if self.truth_sha256 != expected:
            raise ValueError("v2.3.4 truth digest differs")
        return self

    def require(self, task_id: str) -> RegistrationTruthV234:
        truth = next((item for item in self.truths if item.task_id == task_id), None)
        if truth is None:
            raise ValueError("v2.3.4 registration truth is absent")
        return truth


class ProviderCoreViewBindingV234(DtaModelV22):
    task_id: str
    provider_view: dict[str, Any]
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_binding(self) -> "ProviderCoreViewBindingV234":
        ProviderCoreOntologyViewV234.model_validate_json(
            json.dumps({**self.provider_view, "hidden_mechanism": None})
        )
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"binding_sha256"})
        )
        if self.binding_sha256 != expected:
            raise ValueError("v2.3.4 Provider-view binding digest differs")
        return self

    def materialize(self) -> ProviderCoreOntologyViewV234:
        return ProviderCoreOntologyViewV234.model_validate_json(
            json.dumps({**self.provider_view, "hidden_mechanism": None})
        )


class ProviderCoreViewSetV234(DtaModelV22):
    schema_version: Literal["dta-v234.provider-core-view-set.v1"]
    authoritative_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    views: tuple[ProviderCoreViewBindingV234, ...] = Field(
        min_length=16, max_length=16
    )
    view_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_set(self) -> "ProviderCoreViewSetV234":
        if tuple(item.task_id for item in self.views) != tuple(
            f"rt-{ordinal:03d}" for ordinal in range(1, 17)
        ):
            raise ValueError("v2.3.4 Provider view IDs differ")
        if any(
            item.materialize().authoritative_snapshot_sha256
            != self.authoritative_snapshot_sha256
            for item in self.views
        ):
            raise ValueError("v2.3.4 Provider views differ from snapshot")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"view_set_sha256"})
        )
        if self.view_set_sha256 != expected:
            raise ValueError("v2.3.4 Provider view-set digest differs")
        return self

    def require(self, task_id: str) -> ProviderCoreOntologyViewV234:
        binding = next((item for item in self.views if item.task_id == task_id), None)
        if binding is None:
            raise ValueError("v2.3.4 Provider view is absent")
        return binding.materialize()


def _hashed(model: type[DtaModelV22], payload: dict[str, Any], field: str) -> Any:
    factory: Any = model
    draft = factory.model_construct(**payload, **{field: "0" * 64})
    return model.model_validate(
        {
            **payload,
            field: semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={field})
            ),
        }
    )


def load_registration_tasks_v234(path: Path) -> RegistrationTaskSetV234:
    return RegistrationTaskSetV234.model_validate_json(path.read_bytes())


def load_registration_truth_v234(path: Path) -> RegistrationTruthSetV234:
    return RegistrationTruthSetV234.model_validate_json(path.read_bytes())


def load_core_schema_views_v234(path: Path) -> ProviderCoreViewSetV234:
    return ProviderCoreViewSetV234.model_validate_json(path.read_bytes())


class LazyRegistrationTruthStoreV234:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._completed: dict[str, set[EvaluationArmV234]] = {}
        self._loaded: RegistrationTruthSetV234 | None = None
        self._loaded_ids: set[str] = set()

    @property
    def load_count(self) -> int:
        return len(self._loaded_ids)

    def mark_complete(self, task_id: str, arm: EvaluationArmV234) -> None:
        completed = self._completed.setdefault(task_id, set())
        if arm in completed:
            raise ValueError("v2.3.4 evaluation arm completed more than once")
        completed.add(arm)

    def require(self, task_id: str) -> RegistrationTruthV234:
        if self._completed.get(task_id) != set(EvaluationArmV234):
            raise ValueError("v2.3.4 truth requires both evaluation arms")
        if self._loaded is None:
            self._loaded = load_registration_truth_v234(self.path)
        self._loaded_ids.add(task_id)
        return self._loaded.require(task_id)


class RegistrationEvaluationAuditV234(DtaModelV22):
    schema_version: Literal["dta-v234.registration-evaluation-audit.v1"]
    task_count: Literal[16]
    hidden_known_task_count: Literal[10]
    unregistered_task_count: Literal[4]
    control_task_count: Literal[2]
    hidden_view_pass_count: Literal[10]
    hidden_identifier_leaks: Literal[0]
    unregistered_core_clause_match_count: Literal[0]
    duplicate_control_core_match_count: Literal[1]
    insufficient_control_evidence_source_count: int = Field(ge=0, le=1)
    truth_evaluator_only: Literal[True]
    premature_truth_reads: Literal[0]
    action_authority_violations: Literal[0]
    terminal: Literal["DTA_V234_EVALUATION_DATA_PASS"]
    audit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_audit(self) -> "RegistrationEvaluationAuditV234":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"audit_sha256"})
        )
        if self.audit_sha256 != expected:
            raise ValueError("v2.3.4 evaluation audit digest differs")
        return self


def _case_context(
    *, repository_root: Path, task: RegistrationTaskV234, hide: MechanismV22 | None
) -> Any:
    cases = load_evaluation_cases_v233(
        repository_root / "config/dta-v233/evaluation/cases.json"
    )
    spec = cases.require(task.source_case_id)
    if spec.source_bytes_sha256 != task.source_case_sha256:
        raise ValueError("v2.3.4 source case bytes differ")
    case = materialize_evaluation_case_v231(
        repository_root=repository_root,
        spec=spec,
    )
    return _build_common_context_v23(case=case, hidden_mechanism=hide)


def run_evaluation_data_audit_v234(
    *, repository_root: Path, evaluation_root: Path
) -> RegistrationEvaluationAuditV234:
    tasks = load_registration_tasks_v234(evaluation_root / "tasks.json")
    truths = load_registration_truth_v234(evaluation_root / "truth.json")
    views = load_core_schema_views_v234(
        evaluation_root / "core-schema-snapshot.json"
    )
    hidden_pass = 0
    hidden_leaks = 0
    unregistered_matches = 0
    duplicate_matches = 0
    insufficient_sources = 0
    for task in tasks.tasks:
        truth = truths.require(task.task_id)
        view = views.require(task.task_id)
        if task.provider_view_sha256 != view.view_sha256:
            raise ValueError("v2.3.4 task view binding differs")
        if task.task_class is RegistrationTaskClassV234.HIDDEN_KNOWN:
            assert truth.target_mechanism is not None
            rendered = json.dumps(view.model_dump(mode="json"), sort_keys=True).casefold()
            labels = {
                truth.target_mechanism.value.casefold(),
                truth.target_mechanism.value.casefold().replace("_", "-"),
                truth.target_mechanism.value.casefold().replace("_", " "),
            }
            if truth.target_mechanism not in view.runtime_known_mechanisms and not any(
                item in rendered for item in labels
            ):
                hidden_pass += 1
            identifier_text = f"{task.task_id} {task.source_case_id}".casefold()
            hidden_leaks += int(any(item in identifier_text for item in labels))
        context = _case_context(repository_root=repository_root, task=task, hide=None)
        if task.task_class is RegistrationTaskClassV234.UNREGISTERED:
            unregistered_matches += int(context.admission.admitted_diagnosis is not None)
        elif task.task_class is RegistrationTaskClassV234.DUPLICATE_CONTROL:
            duplicate_matches += int(context.admission.admitted_diagnosis is not None)
        elif task.task_class is RegistrationTaskClassV234.INSUFFICIENT_CONTROL:
            insufficient_sources = len(
                {
                    item.source
                    for item in extract_generic_anomalies_v23(
                        memory=context.memory,
                        candidate_services=context.case.candidate_services,
                    )
                }
            )
            if context.admission.admitted_diagnosis is not None:
                raise ValueError("v2.3.4 insufficient control matched a core clause")
    if (
        hidden_pass != 10
        or hidden_leaks != 0
        or unregistered_matches != 0
        or duplicate_matches != 1
        or insufficient_sources > 1
    ):
        raise ValueError("v2.3.4 evaluation data admission failed")
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.registration-evaluation-audit.v1",
        "task_count": 16,
        "hidden_known_task_count": 10,
        "unregistered_task_count": 4,
        "control_task_count": 2,
        "hidden_view_pass_count": hidden_pass,
        "hidden_identifier_leaks": hidden_leaks,
        "unregistered_core_clause_match_count": unregistered_matches,
        "duplicate_control_core_match_count": duplicate_matches,
        "insufficient_control_evidence_source_count": insufficient_sources,
        "truth_evaluator_only": True,
        "premature_truth_reads": 0,
        "action_authority_violations": 0,
        "terminal": "DTA_V234_EVALUATION_DATA_PASS",
    }
    return _hashed(RegistrationEvaluationAuditV234, payload, "audit_sha256")


_FIXTURE_MECHANISM_BY_TASK_V234 = {
    "rt-001": "CONFIGURATION_ERROR",
    "rt-002": "CONFIGURATION_ERROR",
    "rt-003": "DEPENDENCY_LATENCY",
    "rt-004": "DEPENDENCY_LATENCY",
    "rt-005": "SERVICE_UNAVAILABLE",
    "rt-006": "SERVICE_UNAVAILABLE",
    "rt-007": "CPU_SATURATION",
    "rt-008": "CPU_SATURATION",
    "rt-009": "MEMORY_LEAK",
    "rt-010": "MEMORY_LEAK",
    "rt-011": "CONNECTION_POOL_EXHAUSTION",
    "rt-012": "QUEUE_BACKLOG_SATURATION",
    "rt-013": "EXTERNAL_QUOTA_THROTTLING",
    "rt-014": "NETWORK_TRANSPORT_DEGRADATION",
}


def _source_refs(
    request: RegistrationDraftProviderRequestV234,
) -> dict[EvidenceSourceV22, tuple[str, ...]]:
    values: dict[EvidenceSourceV22, list[str]] = {
        source: [] for source in EvidenceSourceV22
    }
    for report in request.accepted_reports:
        for evidence in report.evidence_summaries:
            values[evidence.source].append(evidence.evidence_ref)
    return {source: tuple(sorted(set(refs))) for source, refs in values.items()}


def _predicate(
    *,
    name: str,
    source: EvidenceSourceV22,
    refs: tuple[str, ...],
    rule: Any,
    implementation_mode: PredicateImplementationModeV234 = (
        PredicateImplementationModeV234.DECLARATIVE_EXTENSION_PREDICATE
    ),
    service_binding: RequirementServiceBindingV22 = (
        RequirementServiceBindingV22.TARGET
    ),
    require_exact_parent: bool = False,
) -> FormalPredicateDraftV234:
    slug = name.casefold().replace("_", "-")
    return FormalPredicateDraftV234(
        predicate_name=name,
        predicate_slug=slug,
        implementation_mode=implementation_mode,
        evidence_source=source,
        service_binding=service_binding,
        require_exact_parent=require_exact_parent,
        semantic_definition=predicate_semantic_definition_v234(slug),
        extraction_rule=rule,
        threshold_rule=(
            "RULE_EMBEDS_TYPED_THRESHOLD"
            if isinstance(
                rule,
                (
                    MetricThresholdRuleV234,
                    TraceDurationThresholdRuleV234,
                    ResourceCpuThresholdRuleV234,
                    ResourceMemorySlopeRuleV234,
                ),
            )
            else None
        ),
        supporting_report_evidence_refs=refs,
        positive_examples=(predicate_positive_example_v234(slug),),
        negative_examples=(predicate_negative_example_v234(slug),),
    )


def _core_predicate(
    *,
    kind: PredicateKindV22,
    source: EvidenceSourceV22,
    refs: tuple[str, ...],
    service_binding: RequirementServiceBindingV22 = (
        RequirementServiceBindingV22.TARGET
    ),
    require_exact_parent: bool = False,
) -> FormalPredicateDraftV234:
    return _predicate(
        name=kind.value,
        source=source,
        refs=refs,
        rule=CorePredicateReferenceRuleV234(
            kind="CORE_PREDICATE_REFERENCE", predicate_kind=kind
        ),
        implementation_mode=PredicateImplementationModeV234.REUSE_CORE_PREDICATE,
        service_binding=service_binding,
        require_exact_parent=require_exact_parent,
    )


def _fixture_provider_content_v234(
    *, task_id: str, request: RegistrationDraftProviderRequestV234
) -> ProviderAuthoredRegistrationDraftV234:
    mechanism_name = _FIXTURE_MECHANISM_BY_TASK_V234[task_id]
    slug = mechanism_name.casefold().replace("_", "-")
    refs = _source_refs(request)
    mode = (
        RegistrationImplementationModeV234.ENGINEERING_REQUIRED
        if task_id == "rt-014"
        else RegistrationImplementationModeV234.DECLARATIVE_READY
    )
    domain = request.shadow_fault.broad_fault_domain
    confusables = tuple(
        sorted(
            {MechanismV22.DEPENDENCY_LATENCY, MechanismV22.SERVICE_UNAVAILABLE},
            key=lambda item: item.value,
        )
    )
    mechanism = MechanismProposalV234(
        mechanism_enum_name=mechanism_name,
        mechanism_slug=slug,
        display_name=mechanism_display_name_v234(mechanism_name),
        broad_fault_domain=domain,
        human_definition=mechanism_human_definition_v234(slug),
        distinguishing_summary=mechanism_distinguishing_summary_v234(slug),
        confusable_core_mechanisms=confusables,
        confusable_extension_mechanisms=(),
    )
    predicates: tuple[FormalPredicateDraftV234, ...]
    if task_id in {"rt-001", "rt-002"}:
        predicates = (
            _core_predicate(
                kind=PredicateKindV22.CHANGE_RECENT_ROLLOUT,
                source=EvidenceSourceV22.CHANGES,
                refs=refs[EvidenceSourceV22.CHANGES],
            ),
            _core_predicate(
                kind=PredicateKindV22.METRIC_ERROR_RATE_STRONG,
                source=EvidenceSourceV22.METRICS,
                refs=refs[EvidenceSourceV22.METRICS],
            ),
        )
    elif task_id in {"rt-003", "rt-004"}:
        predicates = (
            _core_predicate(
                kind=PredicateKindV22.METRIC_LATENCY_STRONG,
                source=EvidenceSourceV22.METRICS,
                refs=refs[EvidenceSourceV22.METRICS],
                service_binding=RequirementServiceBindingV22.TARGET_OR_PARENT,
            ),
            _core_predicate(
                kind=PredicateKindV22.TRACE_DEPENDENCY_LATENCY,
                source=EvidenceSourceV22.TRACES,
                refs=refs[EvidenceSourceV22.TRACES],
                require_exact_parent=True,
            ),
        )
    elif task_id in {"rt-005", "rt-006"}:
        predicates = (
            _core_predicate(
                kind=PredicateKindV22.RUNTIME_NOT_RUNNING,
                source=EvidenceSourceV22.RUNTIME,
                refs=refs[EvidenceSourceV22.RUNTIME],
            ),
        )
    elif task_id in {"rt-007", "rt-008"}:
        predicates = (
            _core_predicate(
                kind=PredicateKindV22.METRIC_ERROR_RATE_STRONG,
                source=EvidenceSourceV22.METRICS,
                refs=refs[EvidenceSourceV22.METRICS],
            ),
            _core_predicate(
                kind=PredicateKindV22.RESOURCE_CPU_STRONG,
                source=EvidenceSourceV22.RESOURCES,
                refs=refs[EvidenceSourceV22.RESOURCES],
            ),
        )
    elif task_id in {"rt-009", "rt-010"}:
        predicates = (
            _core_predicate(
                kind=PredicateKindV22.LOG_MEMORY_PRESSURE,
                source=EvidenceSourceV22.LOGS,
                refs=refs[EvidenceSourceV22.LOGS],
            ),
            _core_predicate(
                kind=PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG,
                source=EvidenceSourceV22.RESOURCES,
                refs=refs[EvidenceSourceV22.RESOURCES],
            ),
        )
    elif task_id == "rt-014":
        predicates = (
            _predicate(
                name="NETWORK_TRANSPORT_SIGNATURE",
                source=EvidenceSourceV22.LOGS,
                refs=refs[EvidenceSourceV22.LOGS],
                rule=None,
                implementation_mode=(
                    PredicateImplementationModeV234.REQUIRES_CODE_IMPLEMENTATION
                ),
            ),
        )
    else:
        predicates = (
            _predicate(
                name=f"{mechanism_name}_LOG_SIGNAL",
                source=EvidenceSourceV22.LOGS,
                refs=refs[EvidenceSourceV22.LOGS],
                rule=GenericAnomalyKindRuleV234(
                    kind="GENERIC_ANOMALY_KIND",
                    anomaly_kind=GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN,
                ),
            ),
            _predicate(
                name=f"{mechanism_name}_METRIC_SIGNAL",
                source=EvidenceSourceV22.METRICS,
                refs=refs[EvidenceSourceV22.METRICS],
                rule=MetricThresholdRuleV234(
                    kind="METRIC_THRESHOLD",
                    metric_kind=MetricKindV22.ERROR_RATE,
                    comparison=ThresholdComparisonV234.GREATER_THAN,
                    threshold=0.05,
                    unit=MetricUnitV22.RATIO,
                ),
            ),
        )
    predicates = tuple(sorted(predicates, key=lambda item: item.predicate_name))
    clauses: tuple[SupportClauseDraftV234, ...] = ()
    if mode is RegistrationImplementationModeV234.DECLARATIVE_READY:
        clauses = (
            SupportClauseDraftV234(
                clause_id=f"{slug}:primary-signal",
                mechanism_slug=slug,
                requirements=tuple(
                    sorted(
                        (
                            PredicateRequirementDraftV234(
                                predicate_name=item.predicate_name,
                                service_binding=item.service_binding,
                                require_exact_parent=item.require_exact_parent,
                            )
                            for item in predicates
                        ),
                        key=lambda item: (
                            item.predicate_name,
                            item.service_binding.value,
                            item.require_exact_parent,
                        ),
                    )
                ),
                rationale=support_clause_rationale_v234(),
            ),
        )
    reports = tuple(
        sorted(item.accepted_seed_report_id for item in request.accepted_reports)
    )
    cases = tuple(sorted(item.source_case_id for item in request.accepted_reports))
    test_plan = RegistrationTestPlanV234(
        positive_report_ids=reports,
        positive_case_ids=cases,
        confusable_core_mechanisms=confusables,
        required_known_controls=("known-control-a",),
        required_no_incident_controls=("no-incident-control-a",),
        required_counterfactuals=("counterfactual-control-a",),
        required_source_failure_tests=("source-failure-control-a",),
        required_clause_binding_tests=("clause-binding-control-a",),
    )
    questions = (
        ("Define the bounded engineering gap for network-transport-signature.",)
        if mode is RegistrationImplementationModeV234.ENGINEERING_REQUIRED
        else ()
    )
    return ProviderAuthoredRegistrationDraftV234(
        implementation_mode=mode,
        mechanism=mechanism,
        predicates=predicates,
        support_clauses=clauses,
        test_plan=test_plan,
        unresolved_engineering_questions=questions,
    )


class DeterministicRegistrationFixtureTransportV234:
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    latency_ms = 0.0

    def __call__(self, body: str) -> str:
        value = json.loads(body)
        request_payload = value["request"]
        request_payload["core_ontology_view"]["hidden_mechanism"] = None
        request = RegistrationDraftProviderRequestV234.model_validate_json(
            json.dumps(request_payload)
        )
        cases = request.legacy_registration_seed["positive_case_ids"]
        task_id = str(cases[0]).removeprefix("dta-v234-")
        return _fixture_provider_content_v234(
            task_id=task_id,
            request=request,
        ).model_dump_json()


def _build_report_item_v234(
    *, repository_root: Path, task: RegistrationTaskV234
) -> ReviewQueueItemV23:
    cases = load_evaluation_cases_v233(
        repository_root / "config/dta-v233/evaluation/cases.json"
    )
    spec = cases.require(task.source_case_id)
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
            {"task_id": task.task_id, "lane": "dta-v234-registration-evaluation"}
        )[:32],
    )
    completed = {item.action_id for item in outcomes}
    backend = QuerySpecificReplayBackendV22(case.capture)
    extra = tuple(
        backend.execute(action)
        for action in catalog.registry_actions
        if action.action_id not in completed
        and action.source is not EvidenceSourceV22.RUNTIME
        and task.selected_root_service in action.target_services
    )
    memory, _ = build_memory_views_v22(
        outcomes=(*outcomes, *extra),  # type: ignore[arg-type]
        baseline=_baseline(case),
        observed_at=case.capture.captured_at,
        top_k=64,
    )
    anomalies = tuple(
        item
        for item in extract_generic_anomalies_v23(
            memory=memory,
            candidate_services=case.candidate_services,
        )
        if item.service == task.selected_root_service
        and item.kind in set(task.selected_anomaly_kinds)
    )
    kinds = {item.kind for item in anomalies}
    if kinds != set(task.selected_anomaly_kinds):
        raise ValueError(f"{task.task_id} lacks its frozen report evidence")
    selected_by_kind = {
        kind: next(item for item in anomalies if item.kind is kind)
        for kind in task.selected_anomaly_kinds
    }
    selected = tuple(selected_by_kind[kind] for kind in task.selected_anomaly_kinds)
    evidence_refs = tuple(
        sorted({ref for item in selected for ref in item.evidence_refs})
    )
    graph = build_residual_evidence_graph_v23(
        candidate_services=case.candidate_services,
        generic_anomalies=anomalies,
        known_terminal_candidates=(),
        memory=memory,
    )
    report = build_provisional_report_v23(
        terminal="UNREGISTERED_INCIDENT_SUSPECTED",
        candidate_services=case.candidate_services,
        suspected_root_services=(task.selected_root_service,),
        affected_services=case.candidate_services,
        broad_fault_domain=task.broad_fault_domain,
        provisional_mechanism_label=task.provisional_mechanism_label,
        mechanism_description=task.mechanism_description,
        observed_symptoms=task.observed_symptoms,
        supporting_evidence_refs=evidence_refs,
        contradicting_evidence_refs=(),
        unexplained_anomaly_ids=tuple(
            sorted(item.anomaly_id for item in selected)
        ),
        alternative_hypotheses=("known-control-a",),
        recommended_next_observations=(),
        confidence=0.72,
        memory=memory,
        residual_anomaly_refs={
            item.anomaly_id: item.evidence_refs for item in anomalies
        },
    )
    return build_review_queue_item_v23(
        report=report,
        graph=graph,
        source_case_id=f"dta-v234-{task.task_id}",
        queued_at=case.capture.captured_at,
        automated_fixture=True,
    )


def _prepare_authorized_task_v234(
    *, repository_root: Path, task: RegistrationTaskV234, local_root: Path
) -> tuple[
    ReviewQueueItemV23,
    ShadowFaultEntryV23,
    DraftGenerationAuthorizationResultV234,
]:
    item = _build_report_item_v234(repository_root=repository_root, task=task)
    review_store = LocalReviewStoreV23(local_root)
    review_store.enqueue(item)
    accepted = review_store.decide(
        report_id=item.report.report_id,
        decision=HumanReviewDecisionV23.ACCEPT_AS_NEW,
        reviewer=TEST_REVIEWER_V23,
        review_note="SIMULATED HUMAN REVIEW for fixed registration assistance.",
        canonical_label=f"candidate-pattern-{task.task_id.removeprefix('rt-')}",
        merge_target=None,
        requested_observations=(),
        reviewed_at=item.queued_at,
    )
    if accepted.shadow_entry is None:
        raise ValueError("v2.3.4 simulated acceptance lacks a Shadow Fault")
    authorization = LocalOntologyExpansionStoreV234(
        local_root
    ).authorize_draft_generation(
        shadow_fault_id=accepted.shadow_entry.shadow_fault_id,
        reviewer=TEST_REVIEWER_V23,
        authorization_note=(
            "SIMULATED HUMAN REVIEW authorizes one formal registration draft only."
        ),
        authorized_at=item.queued_at,
    )
    return item, accepted.shadow_entry, authorization


class RuntimePreflightTaskV234(DtaModelV22):
    task_id: str
    baseline_complete: bool
    treatment_complete: bool
    provider_call_expected: bool
    provider_calls: int
    protocol_repairs: int
    transport_retries: int
    validation_status: str | None
    compiled: bool
    error_code: str | None


class RuntimePreflightArtifactV234(DtaModelV22):
    schema_version: Literal["dta-v234.runtime-preflight.v1"]
    task_count: Literal[16]
    arm_path_count: Literal[32]
    completed_arm_path_count: Literal[32]
    tasks: tuple[RuntimePreflightTaskV234, ...]
    runtime_exceptions: Literal[0]
    invalid_authorization_transitions: Literal[0]
    unmapped_predicate_dsl_rules: Literal[0]
    invalid_clause_references: Literal[0]
    compiler_exceptions: Literal[0]
    premature_truth_reads: Literal[0]
    action_authority_violations: Literal[0]
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    terminal: Literal["DTA_V234_RUNTIME_PREFLIGHT_PASS"]
    preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_preflight(self) -> "RuntimePreflightArtifactV234":
        if len(self.tasks) != 16 or any(
            not item.baseline_complete or not item.treatment_complete
            for item in self.tasks
        ):
            raise ValueError("v2.3.4 runtime preflight denominator differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"preflight_sha256"})
        )
        if self.preflight_sha256 != expected:
            raise ValueError("v2.3.4 runtime preflight digest differs")
        return self


def run_runtime_preflight_v234(
    *, repository_root: Path, evaluation_root: Path, local_root: Path
) -> RuntimePreflightArtifactV234:
    audit = run_evaluation_data_audit_v234(
        repository_root=repository_root,
        evaluation_root=evaluation_root,
    )
    if audit.terminal != "DTA_V234_EVALUATION_DATA_PASS":
        raise ValueError("v2.3.4 evaluation data gate did not pass")
    tasks = load_registration_tasks_v234(evaluation_root / "tasks.json")
    views = load_core_schema_views_v234(
        evaluation_root / "core-schema-snapshot.json"
    )
    results: list[RuntimePreflightTaskV234] = []
    runtime_exceptions = 0
    invalid_authorizations = 0
    unmapped_rules = 0
    invalid_references = 0
    compiler_exceptions = 0
    for task in tasks.tasks:
        if not task.provider_call_expected:
            results.append(
                RuntimePreflightTaskV234(
                    task_id=task.task_id,
                    baseline_complete=True,
                    treatment_complete=True,
                    provider_call_expected=False,
                    provider_calls=0,
                    protocol_repairs=0,
                    transport_retries=0,
                    validation_status=(
                        RegistrationImplementationModeV234.DUPLICATE_EXISTING.value
                        if task.task_class
                        is RegistrationTaskClassV234.DUPLICATE_CONTROL
                        else RegistrationImplementationModeV234.INSUFFICIENT_EVIDENCE.value
                    ),
                    compiled=False,
                    error_code=None,
                )
            )
            continue
        try:
            task_root = local_root / task.task_id
            item, shadow, authorization = _prepare_authorized_task_v234(
                repository_root=repository_root,
                task=task,
                local_root=task_root,
            )
            draft = RegistrationDraftProviderV234(
                DeterministicRegistrationFixtureTransportV234()
            ).generate(
                authorization_context=authorization,
                shadow=shadow,
                accepted_reports=(item,),
                ontology_view=views.require(task.task_id),
            )
            validation = validate_registration_draft_v234(
                draft=draft,
                authorization_context=authorization,
                shadow=shadow,
                accepted_reports=(item,),
                promoted_mechanism_slugs=(),
                shadow_mechanism_slugs=(),
            )
            invalid_authorizations += sum(
                code.startswith("AUTHORIZATION_") for code in validation.error_codes
            )
            unmapped_rules += sum(
                "UNSUPPORTED" in code or "UNREACHABLE_SOURCE_RULE" in code
                for code in validation.error_codes
            )
            invalid_references += sum(
                code.startswith("UNRESOLVED_REQUIREMENT")
                or code.startswith("CLAUSE_BINDING_MISMATCH")
                for code in validation.error_codes
            )
            compiled = False
            if (
                task.task_class is RegistrationTaskClassV234.UNREGISTERED
                and draft.implementation_mode
                is RegistrationImplementationModeV234.DECLARATIVE_READY
            ):
                try:
                    compile_registration_v234(
                        draft=draft,
                        validation=validation,
                        snapshot=authorization.core_ontology_snapshot,
                    )
                except ValueError:
                    compiler_exceptions += 1
                else:
                    compiled = True
            results.append(
                RuntimePreflightTaskV234(
                    task_id=task.task_id,
                    baseline_complete=isinstance(
                        authorization.registration_seed.legacy_registration_draft,
                        RegistrationDraftV23,
                    ),
                    treatment_complete=True,
                    provider_call_expected=True,
                    provider_calls=draft.provider_trace.provider_calls,
                    protocol_repairs=draft.provider_trace.protocol_repairs,
                    transport_retries=draft.provider_trace.transport_retries,
                    validation_status=validation.status.value,
                    compiled=compiled,
                    error_code=None,
                )
            )
        except Exception as exc:  # fail-closed artifact construction below
            runtime_exceptions += 1
            results.append(
                RuntimePreflightTaskV234(
                    task_id=task.task_id,
                    baseline_complete=False,
                    treatment_complete=False,
                    provider_call_expected=True,
                    provider_calls=0,
                    protocol_repairs=0,
                    transport_retries=0,
                    validation_status=None,
                    compiled=False,
                    error_code=type(exc).__name__,
                )
            )
    if any(
        (
            runtime_exceptions,
            invalid_authorizations,
            unmapped_rules,
            invalid_references,
            compiler_exceptions,
        )
    ):
        details = ", ".join(
            f"{item.task_id}:{item.error_code}"
            for item in results
            if item.error_code is not None
        )
        raise ValueError(f"v2.3.4 runtime preflight failed: {details}")
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.runtime-preflight.v1",
        "task_count": 16,
        "arm_path_count": 32,
        "completed_arm_path_count": 32,
        "tasks": tuple(results),
        "runtime_exceptions": 0,
        "invalid_authorization_transitions": 0,
        "unmapped_predicate_dsl_rules": 0,
        "invalid_clause_references": 0,
        "compiler_exceptions": 0,
        "premature_truth_reads": 0,
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "terminal": "DTA_V234_RUNTIME_PREFLIGHT_PASS",
    }
    return _hashed(RuntimePreflightArtifactV234, payload, "preflight_sha256")


_TASK_SPECS_V234: tuple[dict[str, Any], ...] = (
    {"source_case_id": "vx-301", "task_class": "HIDDEN_KNOWN", "domain": "CONFIGURATION", "root": "svc-50b4ddc9a9", "label": "recent setting inconsistency", "description": "A recent opaque setting change coincides with strong target error evidence.", "symptoms": ("changes show a recent opaque setting rollout", "metrics show a strong target error rate"), "kinds": ("METRIC_ERROR_OUTLIER", "RECENT_CHANGE_CORRELATION")},
    {"source_case_id": "vx-302", "task_class": "HIDDEN_KNOWN", "domain": "CONFIGURATION", "root": "svc-77d4c9fe68", "label": "recent setting inconsistency", "description": "A recent opaque setting change coincides with strong target error evidence.", "symptoms": ("changes show a recent opaque setting rollout", "metrics show a strong target error rate"), "kinds": ("METRIC_ERROR_OUTLIER", "RECENT_CHANGE_CORRELATION")},
    {"source_case_id": "vx-303", "task_class": "HIDDEN_KNOWN", "domain": "DEPENDENCY", "root": "svc-64b3d3907c", "label": "dependency path delay", "description": "A slow dependency path coincides with strong target latency evidence.", "symptoms": ("metrics show strong target latency", "traces show a slow downstream dependency path"), "kinds": ("METRIC_LATENCY_OUTLIER", "TRACE_LATENCY_OUTLIER")},
    {"source_case_id": "vx-304", "task_class": "HIDDEN_KNOWN", "domain": "DEPENDENCY", "root": "svc-c7902c06b9", "label": "dependency path delay", "description": "A slow dependency path coincides with strong target latency evidence.", "symptoms": ("metrics show strong target latency", "traces show a slow downstream dependency path"), "kinds": ("METRIC_LATENCY_OUTLIER", "TRACE_LATENCY_OUTLIER")},
    {"source_case_id": "vx-305", "task_class": "HIDDEN_KNOWN", "domain": "RUNTIME", "root": "svc-8452f9b829", "label": "runtime availability state", "description": "The target runtime is absent while target error evidence remains strong.", "symptoms": ("metrics show a strong target error rate", "runtime shows the target is not running"), "kinds": ("METRIC_ERROR_OUTLIER", "RUNTIME_NOT_RUNNING")},
    {"source_case_id": "vx-306", "task_class": "HIDDEN_KNOWN", "domain": "RUNTIME", "root": "svc-75a0bd2dea", "label": "runtime availability state", "description": "The target runtime is absent while target error evidence remains strong.", "symptoms": ("metrics show a strong target error rate", "runtime shows the target is not running"), "kinds": ("METRIC_ERROR_OUTLIER", "RUNTIME_NOT_RUNNING")},
    {"source_case_id": "vx-307", "task_class": "HIDDEN_KNOWN", "domain": "RESOURCE", "root": "svc-c64512f0e1", "label": "compute resource pressure", "description": "Sustained target compute utilization coincides with strong error evidence.", "symptoms": ("metrics show a strong target error rate", "resources show sustained target compute utilization"), "kinds": ("METRIC_ERROR_OUTLIER", "RESOURCE_CPU_OUTLIER")},
    {"source_case_id": "vx-308", "task_class": "HIDDEN_KNOWN", "domain": "RESOURCE", "root": "svc-aee314eeda", "label": "compute resource pressure", "description": "Sustained target compute utilization coincides with strong error evidence.", "symptoms": ("metrics show a strong target error rate", "resources show sustained target compute utilization"), "kinds": ("METRIC_ERROR_OUTLIER", "RESOURCE_CPU_OUTLIER")},
    {"source_case_id": "vx-309", "task_class": "HIDDEN_KNOWN", "domain": "RESOURCE", "root": "svc-0d7b707a36", "label": "growing memory pressure", "description": "Monotonic target memory growth coincides with an error log cluster.", "symptoms": ("logs show a target memory pressure cluster", "resources show monotonic target memory growth"), "kinds": ("LOG_ERROR_CLUSTER", "RESOURCE_MEMORY_TREND")},
    {"source_case_id": "vx-310", "task_class": "HIDDEN_KNOWN", "domain": "RESOURCE", "root": "svc-e51b9e1bcc", "label": "growing memory pressure", "description": "Monotonic target memory growth coincides with an error log cluster.", "symptoms": ("logs show a target memory pressure cluster", "resources show monotonic target memory growth"), "kinds": ("LOG_ERROR_CLUSTER", "RESOURCE_MEMORY_TREND")},
    {"source_case_id": "vx-311", "task_class": "UNREGISTERED", "domain": "CONCURRENCY", "root": "svc-28037ae9fb", "label": "worker capacity exhaustion", "description": "Concurrent work waits for worker capacity while target errors rise.", "symptoms": ("logs show worker pool semaphore wait under load", "metrics show a strong target error rate"), "kinds": ("LOG_UNKNOWN_ERROR_PATTERN", "METRIC_ERROR_OUTLIER")},
    {"source_case_id": "vx-313", "task_class": "UNREGISTERED", "domain": "CONCURRENCY", "root": "svc-1dc3d6375c", "label": "queue backlog saturation", "description": "Queue backlog pressure delays workers while target errors rise.", "symptoms": ("logs show queue backlog worker wait", "metrics show a strong target error rate"), "kinds": ("LOG_UNKNOWN_ERROR_PATTERN", "METRIC_ERROR_OUTLIER")},
    {"source_case_id": "vx-316", "task_class": "UNREGISTERED", "domain": "EXTERNAL", "root": "svc-9470751930", "label": "external quota throttling", "description": "External quota responses coincide with strong target error evidence.", "symptoms": ("logs show external rate limit responses", "metrics show a strong target error rate"), "kinds": ("LOG_UNKNOWN_ERROR_PATTERN", "METRIC_ERROR_OUTLIER")},
    {"source_case_id": "vx-315", "task_class": "UNREGISTERED", "domain": "NETWORK", "root": "svc-4c39ea767b", "label": "network transport degradation", "description": "Ordering sensitive transport resets require correlation beyond the bounded declarative rules.", "symptoms": ("logs show transport reset ordering during reads", "metrics show a strong target error rate"), "kinds": ("LOG_UNKNOWN_ERROR_PATTERN", "METRIC_ERROR_OUTLIER")},
    {"source_case_id": "vx-317", "task_class": "DUPLICATE_CONTROL", "domain": "CONFIGURATION", "root": "svc-9e87901b82", "label": "known configuration pattern", "description": "A recent opaque setting change already satisfies an active core clause.", "symptoms": ("changes show a recent opaque setting rollout", "metrics show a strong target error rate"), "kinds": ()},
    {"source_case_id": "vx-328", "task_class": "INSUFFICIENT_CONTROL", "domain": "UNKNOWN", "root": "svc-01a294e042", "label": "weak incident signal", "description": "The available observation does not establish a formal incident mechanism.", "symptoms": ("runtime remains healthy with no corroborating anomaly",), "kinds": ()},
)


_TRUTH_SPECS_V234: tuple[dict[str, Any], ...] = (
    {"target": "CONFIGURATION_ERROR", "slug": "configuration-error", "domain": "CONFIGURATION", "mode": "DECLARATIVE_READY", "clauses": ("configuration:change-and-error-metric", "configuration:change-and-log", "configuration:error-metric-and-first-error-trace"), "compile": False},
    {"target": "CONFIGURATION_ERROR", "slug": "configuration-error", "domain": "CONFIGURATION", "mode": "DECLARATIVE_READY", "clauses": ("configuration:change-and-error-metric", "configuration:change-and-log", "configuration:error-metric-and-first-error-trace"), "compile": False},
    {"target": "DEPENDENCY_LATENCY", "slug": "dependency-latency", "domain": "DEPENDENCY", "mode": "DECLARATIVE_READY", "clauses": ("dependency-latency:trace-and-metric",), "compile": False},
    {"target": "DEPENDENCY_LATENCY", "slug": "dependency-latency", "domain": "DEPENDENCY", "mode": "DECLARATIVE_READY", "clauses": ("dependency-latency:trace-and-metric",), "compile": False},
    {"target": "SERVICE_UNAVAILABLE", "slug": "service-unavailable", "domain": "RUNTIME", "mode": "DECLARATIVE_READY", "clauses": ("service-unavailable:not-running", "service-unavailable:unhealthy-error-metric", "service-unavailable:unhealthy-first-error"), "compile": False},
    {"target": "SERVICE_UNAVAILABLE", "slug": "service-unavailable", "domain": "RUNTIME", "mode": "DECLARATIVE_READY", "clauses": ("service-unavailable:not-running", "service-unavailable:unhealthy-error-metric", "service-unavailable:unhealthy-first-error"), "compile": False},
    {"target": "CPU_SATURATION", "slug": "cpu-saturation", "domain": "RESOURCE", "mode": "DECLARATIVE_READY", "clauses": ("cpu-saturation:resource-and-healthy",), "compile": False},
    {"target": "CPU_SATURATION", "slug": "cpu-saturation", "domain": "RESOURCE", "mode": "DECLARATIVE_READY", "clauses": ("cpu-saturation:resource-and-healthy",), "compile": False},
    {"target": "MEMORY_LEAK", "slug": "memory-leak", "domain": "RESOURCE", "mode": "DECLARATIVE_READY", "clauses": ("memory-leak:growth-and-healthy", "memory-leak:growth-and-log", "memory-leak:growth-and-restarts"), "compile": False},
    {"target": "MEMORY_LEAK", "slug": "memory-leak", "domain": "RESOURCE", "mode": "DECLARATIVE_READY", "clauses": ("memory-leak:growth-and-healthy", "memory-leak:growth-and-log", "memory-leak:growth-and-restarts"), "compile": False},
    {"target": None, "slug": "connection-pool-exhaustion", "domain": "CONCURRENCY", "mode": "DECLARATIVE_READY", "clauses": (), "compile": True},
    {"target": None, "slug": "queue-backlog-saturation", "domain": "CONCURRENCY", "mode": "DECLARATIVE_READY", "clauses": (), "compile": True},
    {"target": None, "slug": "external-quota-throttling", "domain": "EXTERNAL", "mode": "DECLARATIVE_READY", "clauses": (), "compile": True},
    {"target": None, "slug": "network-transport-degradation", "domain": "NETWORK", "mode": "ENGINEERING_REQUIRED", "clauses": (), "compile": False},
    {"target": None, "slug": "configuration-error", "domain": "CONFIGURATION", "mode": "DUPLICATE_EXISTING", "clauses": (), "compile": False},
    {"target": None, "slug": None, "domain": None, "mode": "INSUFFICIENT_EVIDENCE", "clauses": (), "compile": False},
)


def build_default_evaluation_data_v234(
    *, repository_root: Path
) -> tuple[RegistrationTaskSetV234, RegistrationTruthSetV234, ProviderCoreViewSetV234]:
    cases = load_evaluation_cases_v233(
        repository_root / "config/dta-v233/evaluation/cases.json"
    )
    snapshot = build_core_ontology_schema_snapshot_v234()
    truths: list[RegistrationTruthV234] = []
    views: list[ProviderCoreViewBindingV234] = []
    tasks: list[RegistrationTaskV234] = []
    for ordinal, (task_spec, truth_spec) in enumerate(
        zip(_TASK_SPECS_V234, _TRUTH_SPECS_V234, strict=True), start=1
    ):
        task_id = f"rt-{ordinal:03d}"
        target = (
            MechanismV22(truth_spec["target"])
            if truth_spec["target"] is not None
            else None
        )
        view = build_provider_core_ontology_view_v234(
            snapshot=snapshot,
            hidden_mechanism=target,
        )
        view_payload = view.model_dump(mode="json")
        binding = _hashed(
            ProviderCoreViewBindingV234,
            {"task_id": task_id, "provider_view": view_payload},
            "binding_sha256",
        )
        views.append(binding)
        source = cases.require(task_spec["source_case_id"])
        task_payload: dict[str, Any] = {
            "task_id": task_id,
            "source_case_id": task_spec["source_case_id"],
            "source_case_sha256": source.source_bytes_sha256,
            "task_class": RegistrationTaskClassV234(task_spec["task_class"]),
            "provider_view_sha256": view.view_sha256,
            "selected_root_service": task_spec["root"],
            "broad_fault_domain": ProvisionalFaultDomainV23(task_spec["domain"]),
            "provisional_mechanism_label": task_spec["label"],
            "mechanism_description": task_spec["description"],
            "observed_symptoms": tuple(sorted(task_spec["symptoms"])),
            "selected_anomaly_kinds": tuple(
                sorted(
                    (GenericAnomalyKindV23(item) for item in task_spec["kinds"]),
                    key=lambda item: item.value,
                )
            ),
            "provider_call_expected": ordinal <= 14,
        }
        tasks.append(_hashed(RegistrationTaskV234, task_payload, "task_sha256"))
        truths.append(
            RegistrationTruthV234(
                task_id=task_id,
                target_mechanism=target,
                target_mechanism_slug=truth_spec["slug"],
                expected_broad_fault_domain=(
                    ProvisionalFaultDomainV23(truth_spec["domain"])
                    if truth_spec["domain"] is not None
                    else None
                ),
                expected_implementation_mode=RegistrationImplementationModeV234(
                    truth_spec["mode"]
                ),
                expected_core_clause_ids=tuple(sorted(truth_spec["clauses"])),
                declarative_compilation_expected=truth_spec["compile"],
            )
        )
    task_set = _hashed(
        RegistrationTaskSetV234,
        {
            "schema_version": "dta-v234.registration-task-set.v1",
            "freeze_id": "dta-v234-registration-assistance-freeze-20260826-a",
            "tasks": tuple(tasks),
        },
        "task_set_sha256",
    )
    truth_set = _hashed(
        RegistrationTruthSetV234,
        {
            "schema_version": "dta-v234.registration-truth-set.v1",
            "truths": tuple(truths),
        },
        "truth_sha256",
    )
    view_set = _hashed(
        ProviderCoreViewSetV234,
        {
            "schema_version": "dta-v234.provider-core-view-set.v1",
            "authoritative_snapshot_sha256": snapshot.snapshot_sha256,
            "views": tuple(views),
        },
        "view_set_sha256",
    )
    return task_set, truth_set, view_set


class ManifestFileBindingV234(DtaModelV22):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationManifestV234(DtaModelV22):
    schema_version: Literal["dta-v234.evaluation-manifest.v1"]
    base_commit: Literal["da423b9104ac532f0bf323f314d37b527671c679"]
    branch: Literal["codex/dta-v234-human-ontology-expansion"]
    provider_model: str
    planned_task_count: Literal[16]
    planned_run_count: Literal[32]
    planned_execution_count: Literal[1]
    arms: tuple[EvaluationArmV234, EvaluationArmV234]
    frozen_files: tuple[ManifestFileBindingV234, ...]
    provider_system_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_smoke_output: Literal[
        "docs/analysis/dta-v234-provider-smoke.json"
    ]
    output_json: Literal[
        "docs/results/dta-v234-registration-assistance-evaluation.json"
    ]
    output_markdown: Literal[
        "docs/results/dta-v234-registration-assistance-evaluation.md"
    ]
    independent_review: Literal[
        "docs/external-reviews/dta-v234-pre-execution-review.md"
    ]
    fixed_at_utc: datetime

    @model_validator(mode="after")
    def require_manifest(self) -> "EvaluationManifestV234":
        if self.arms != tuple(EvaluationArmV234):
            raise ValueError("v2.3.4 evaluation arm order differs")
        paths = tuple(item.path for item in self.frozen_files)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("v2.3.4 frozen bindings are not canonical")
        if (
            self.fixed_at_utc.tzinfo is None
            or self.fixed_at_utc.utcoffset() != timedelta(0)
        ):
            raise ValueError("v2.3.4 manifest timestamp is not UTC")
        return self


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_evaluation_manifest_v234(path: Path) -> EvaluationManifestV234:
    return EvaluationManifestV234.model_validate_json(path.read_bytes())


def verify_frozen_surface_v234(
    *, repository_root: Path, manifest_path: Path, expected_provider_model: str
) -> EvaluationManifestV234:
    manifest = load_evaluation_manifest_v234(manifest_path)
    if manifest.provider_model != expected_provider_model:
        raise ValueError("v2.3.4 Provider model differs from manifest")
    for binding in manifest.frozen_files:
        path = repository_root / binding.path
        if not path.is_file() or _file_sha256(path) != binding.sha256:
            raise ValueError(f"v2.3.4 frozen binding differs: {binding.path}")
    from ecomsre.dta_v2.v23.registration_provider_v234 import (
        REGISTRATION_DRAFT_SYSTEM_PROMPT_V234,
    )

    if manifest.provider_system_prompt_sha256 != hashlib.sha256(
        REGISTRATION_DRAFT_SYSTEM_PROMPT_V234.encode("utf-8")
    ).hexdigest():
        raise ValueError("v2.3.4 Provider Prompt binding differs")
    audit_path = repository_root / "docs/analysis/dta-v234-registration-audit.json"
    audit = RegistrationEvaluationAuditV234.model_validate_json(audit_path.read_bytes())
    preflight_path = repository_root / "docs/analysis/dta-v234-runtime-preflight.json"
    preflight = RuntimePreflightArtifactV234.model_validate_json(
        preflight_path.read_bytes()
    )
    if (
        audit.terminal != "DTA_V234_EVALUATION_DATA_PASS"
        or preflight.terminal != "DTA_V234_RUNTIME_PREFLIGHT_PASS"
    ):
        raise ValueError("v2.3.4 frozen gates did not pass")
    return manifest


class EvaluationArmRunV234(DtaModelV22):
    schema_version: Literal["dta-v234.registration-arm-run.v1"]
    task_id: str
    arm: EvaluationArmV234
    baseline_seed: RegistrationDraftV23 | None
    formal_draft: FormalFaultRegistrationDraftV234 | None
    typed_disposition: RegistrationImplementationModeV234 | None
    validation_status: DraftValidationStatusV234 | None
    validation_error_codes: tuple[str, ...]
    compiled_registration_sha256: str | None
    patch_bundle_sha256: str | None
    patch_bundle_file_count: int
    provider_calls: int
    protocol_repairs: int
    transport_retries: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: float
    provider_error_code: str | None
    simulation: Literal[True]
    action_authority_violations: Literal[0]
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_run(self) -> "EvaluationArmRunV234":
        if self.arm is EvaluationArmV234.V23_TEMPLATE_REGISTRATION_SEED:
            if self.baseline_seed is None or self.formal_draft is not None:
                raise ValueError("v2.3.4 baseline arm contents differ")
        elif self.baseline_seed is not None:
            raise ValueError("v2.3.4 treatment arm carries a baseline seed")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"run_sha256"})
        )
        if self.run_sha256 != expected:
            raise ValueError("v2.3.4 arm-run digest differs")
        return self


def _baseline_seed_for_control_v234(task: RegistrationTaskV234) -> RegistrationDraftV23:
    slug = task.provisional_mechanism_label.replace(" ", "-")
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.registration-draft.v1",
        "proposed_mechanism_slug": slug,
        "broad_fault_domain": task.broad_fault_domain,
        "human_definition": task.mechanism_description,
        "candidate_generic_anomalies": task.selected_anomaly_kinds,
        "candidate_evidence_sources": (),
        "candidate_support_clause_description": (
            "Require corroborating generic anomalies before any formal registration."
        ),
        "distinguishing_negative_examples": ("known-control-a",),
        "positive_case_ids": (f"dta-v234-{task.task_id}",),
        "required_replay_tests": ("negative-confusable-control",),
        "suggested_formal_files": (
            "src/ecomsre/dta_v2/v22/predicates.py",
            "tests/dta_v22/test_v22_memory_predicates_diagnosis.py",
        ),
        "remediation_registration": "NOT_INCLUDED",
    }
    return _hashed(RegistrationDraftV23, payload, "draft_sha256")


def _baseline_run_v234(
    *, task: RegistrationTaskV234, seed: RegistrationDraftV23
) -> EvaluationArmRunV234:
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.registration-arm-run.v1",
        "task_id": task.task_id,
        "arm": EvaluationArmV234.V23_TEMPLATE_REGISTRATION_SEED,
        "baseline_seed": seed,
        "formal_draft": None,
        "typed_disposition": None,
        "validation_status": None,
        "validation_error_codes": (),
        "compiled_registration_sha256": None,
        "patch_bundle_sha256": None,
        "patch_bundle_file_count": 0,
        "provider_calls": 0,
        "protocol_repairs": 0,
        "transport_retries": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "latency_ms": 0.0,
        "provider_error_code": None,
        "simulation": True,
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    return _hashed(EvaluationArmRunV234, payload, "run_sha256")


def _transport_counters_v234(transport: object) -> tuple[int, int, int, float]:
    return (
        int(getattr(transport, "input_tokens", 0)),
        int(getattr(transport, "output_tokens", 0)),
        int(getattr(transport, "total_tokens", 0)),
        float(getattr(transport, "latency_ms", 0.0)),
    )


def _control_treatment_run_v234(
    *, task: RegistrationTaskV234
) -> EvaluationArmRunV234:
    disposition = (
        RegistrationImplementationModeV234.DUPLICATE_EXISTING
        if task.task_class is RegistrationTaskClassV234.DUPLICATE_CONTROL
        else RegistrationImplementationModeV234.INSUFFICIENT_EVIDENCE
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.registration-arm-run.v1",
        "task_id": task.task_id,
        "arm": EvaluationArmV234.V234_LLM_FORMAL_REGISTRATION,
        "baseline_seed": None,
        "formal_draft": None,
        "typed_disposition": disposition,
        "validation_status": DraftValidationStatusV234.NON_REGISTRABLE,
        "validation_error_codes": (),
        "compiled_registration_sha256": None,
        "patch_bundle_sha256": None,
        "patch_bundle_file_count": 0,
        "provider_calls": 0,
        "protocol_repairs": 0,
        "transport_retries": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "latency_ms": 0.0,
        "provider_error_code": None,
        "simulation": True,
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    return _hashed(EvaluationArmRunV234, payload, "run_sha256")


def _treatment_run_v234(
    *,
    repository_root: Path,
    task: RegistrationTaskV234,
    view: ProviderCoreOntologyViewV234,
    local_root: Path,
    provider_transport: Callable[[str], str],
) -> tuple[EvaluationArmRunV234, RegistrationDraftV23]:
    local_root = local_root.resolve(strict=False)
    item, shadow, authorization = _prepare_authorized_task_v234(
        repository_root=repository_root,
        task=task,
        local_root=local_root,
    )
    before = _transport_counters_v234(provider_transport)
    try:
        draft = RegistrationDraftProviderV234(provider_transport).generate(
            authorization_context=authorization,
            shadow=shadow,
            accepted_reports=(item,),
            ontology_view=view,
        )
    except Exception as exc:
        after = _transport_counters_v234(provider_transport)
        payload: dict[str, Any] = {
            "schema_version": "dta-v234.registration-arm-run.v1",
            "task_id": task.task_id,
            "arm": EvaluationArmV234.V234_LLM_FORMAL_REGISTRATION,
            "baseline_seed": None,
            "formal_draft": None,
            "typed_disposition": None,
            "validation_status": None,
            "validation_error_codes": (),
            "compiled_registration_sha256": None,
            "patch_bundle_sha256": None,
            "patch_bundle_file_count": 0,
            "provider_calls": 0,
            "protocol_repairs": 0,
            "transport_retries": 0,
            "input_tokens": after[0] - before[0],
            "output_tokens": after[1] - before[1],
            "total_tokens": after[2] - before[2],
            "latency_ms": after[3] - before[3],
            "provider_error_code": type(exc).__name__,
            "simulation": True,
            "action_authority_violations": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
        }
        return (
            _hashed(EvaluationArmRunV234, payload, "run_sha256"),
            authorization.registration_seed.legacy_registration_draft,
        )
    validation = validate_registration_draft_v234(
        draft=draft,
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
        promoted_mechanism_slugs=(),
        shadow_mechanism_slugs=(),
    )
    compiled_sha: str | None = None
    bundle_sha: str | None = None
    bundle_count = 0
    if (
        validation.status is DraftValidationStatusV234.VALID
        and draft.implementation_mode
        is RegistrationImplementationModeV234.DECLARATIVE_READY
    ):
        compiled = compile_registration_v234(
            draft=draft,
            validation=validation,
            snapshot=authorization.core_ontology_snapshot,
        )
        bundle = render_registration_patch_bundle_v234(
            compiled=compiled,
            output_root=local_root / ".local/dta-v234/registration-bundles",
        )
        compiled_sha = compiled.compiled_sha256
        bundle_sha = bundle.bundle_sha256
        bundle_count = len(bundle.files)
    after = _transport_counters_v234(provider_transport)
    payload = {
        "schema_version": "dta-v234.registration-arm-run.v1",
        "task_id": task.task_id,
        "arm": EvaluationArmV234.V234_LLM_FORMAL_REGISTRATION,
        "baseline_seed": None,
        "formal_draft": draft,
        "typed_disposition": draft.implementation_mode,
        "validation_status": validation.status,
        "validation_error_codes": validation.error_codes,
        "compiled_registration_sha256": compiled_sha,
        "patch_bundle_sha256": bundle_sha,
        "patch_bundle_file_count": bundle_count,
        "provider_calls": draft.provider_trace.provider_calls,
        "protocol_repairs": draft.provider_trace.protocol_repairs,
        "transport_retries": draft.provider_trace.transport_retries,
        "input_tokens": after[0] - before[0],
        "output_tokens": after[1] - before[1],
        "total_tokens": after[2] - before[2],
        "latency_ms": after[3] - before[3],
        "provider_error_code": None,
        "simulation": True,
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    return (
        _hashed(EvaluationArmRunV234, payload, "run_sha256"),
        authorization.registration_seed.legacy_registration_draft,
    )


class RegistrationCaseComparisonV234(DtaModelV22):
    task_id: str
    runs: tuple[EvaluationArmRunV234, EvaluationArmRunV234]
    expected_implementation_mode: RegistrationImplementationModeV234
    draft_schema_valid: bool
    existing_format_structural_valid: bool
    mechanism_identity_accurate: bool | None
    broad_domain_accurate: bool | None
    core_predicate_reuse_precision: float | None
    core_predicate_reuse_recall: float | None
    behavioral_clause_equivalent: bool | None
    confusable_negative_coverage: bool | None
    new_mode_correct: bool | None
    control_non_promotable: bool | None
    declarative_compile_valid: bool | None
    patch_bundle_complete: bool | None
    shadow_plan_complete: bool | None
    comparison_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_comparison(self) -> "RegistrationCaseComparisonV234":
        if tuple(item.arm for item in self.runs) != tuple(EvaluationArmV234):
            raise ValueError("v2.3.4 comparison arm order differs")
        if any(item.task_id != self.task_id for item in self.runs):
            raise ValueError("v2.3.4 comparison task binding differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"comparison_sha256"})
        )
        if self.comparison_sha256 != expected:
            raise ValueError("v2.3.4 comparison digest differs")
        return self


def _core_requirement_signatures_v234(
    *, draft: FormalFaultRegistrationDraftV234
) -> set[frozenset[tuple[str, str, bool]]]:
    by_name = {item.predicate_name: item for item in draft.predicates}
    signatures: set[frozenset[tuple[str, str, bool]]] = set()
    for clause in draft.support_clauses:
        values: set[tuple[str, str, bool]] = set()
        for requirement in clause.requirements:
            predicate = by_name.get(requirement.predicate_name)
            if predicate is None or not isinstance(
                predicate.extraction_rule, CorePredicateReferenceRuleV234
            ):
                values = set()
                break
            values.add(
                (
                    predicate.extraction_rule.predicate_kind.value,
                    requirement.service_binding.value,
                    requirement.require_exact_parent,
                )
            )
        if values:
            signatures.add(frozenset(values))
    return signatures


def _score_comparison_v234(
    *,
    truth: RegistrationTruthV234,
    baseline: EvaluationArmRunV234,
    treatment: EvaluationArmRunV234,
    snapshot: Any,
) -> RegistrationCaseComparisonV234:
    draft = treatment.formal_draft
    is_control = truth.expected_implementation_mode in {
        RegistrationImplementationModeV234.DUPLICATE_EXISTING,
        RegistrationImplementationModeV234.INSUFFICIENT_EVIDENCE,
    }
    schema_valid = (draft is not None) or (
        is_control
        and treatment.typed_disposition is truth.expected_implementation_mode
    )
    structural = schema_valid and treatment.provider_error_code is None
    identity: bool | None = None
    domain: bool | None = None
    precision: float | None = None
    recall: float | None = None
    equivalent: bool | None = None
    confusable: bool | None = None
    new_mode: bool | None = None
    control_safe: bool | None = None
    compile_valid: bool | None = None
    bundle_complete: bool | None = None
    shadow_plan: bool | None = None
    if truth.target_mechanism is not None:
        identity = draft is not None and (
            draft.mechanism.mechanism_enum_name == truth.target_mechanism.value
            and draft.mechanism.mechanism_slug == truth.target_mechanism_slug
        )
        domain = draft is not None and (
            draft.mechanism.broad_fault_domain is truth.expected_broad_fault_domain
        )
        expected_predicates = {
            requirement.predicate_kind
            for clause in snapshot.frozen_core_support_clauses
            if clause.mechanism is truth.target_mechanism
            for requirement in clause.requirements
        }
        reused = (
            {
                item.extraction_rule.predicate_kind
                for item in draft.predicates
                if isinstance(item.extraction_rule, CorePredicateReferenceRuleV234)
            }
            if draft is not None
            else set()
        )
        precision = (
            len(reused & expected_predicates) / len(reused) if reused else 0.0
        )
        recall = (
            len(reused & expected_predicates) / len(expected_predicates)
            if expected_predicates
            else 0.0
        )
        expected_signatures = {
            frozenset(
                (
                    requirement.predicate_kind.value,
                    requirement.service_binding.value,
                    requirement.require_exact_parent,
                )
                for requirement in clause.requirements
            )
            for clause in snapshot.frozen_core_support_clauses
            if clause.mechanism is truth.target_mechanism
        }
        equivalent = bool(
            draft is not None
            and _core_requirement_signatures_v234(draft=draft)
            & expected_signatures
        )
        confusable = bool(
            draft is not None
            and len(draft.test_plan.confusable_core_mechanisms) >= 2
            and draft.test_plan.required_known_controls
        )
    elif is_control:
        control_safe = (
            treatment.typed_disposition is truth.expected_implementation_mode
            and treatment.compiled_registration_sha256 is None
        )
    else:
        new_mode = treatment.typed_disposition is truth.expected_implementation_mode
        if truth.declarative_compilation_expected:
            compile_valid = treatment.compiled_registration_sha256 is not None
            bundle_complete = treatment.patch_bundle_file_count == 7
        else:
            compile_valid = treatment.compiled_registration_sha256 is None
            bundle_complete = treatment.patch_bundle_sha256 is None
    if draft is not None:
        plan = draft.test_plan
        shadow_plan = all(
            (
                plan.positive_report_ids,
                plan.positive_case_ids,
                plan.required_known_controls,
                plan.required_no_incident_controls,
                plan.required_counterfactuals,
                plan.required_source_failure_tests,
                plan.required_clause_binding_tests,
            )
        )
    payload: dict[str, Any] = {
        "task_id": truth.task_id,
        "runs": (baseline, treatment),
        "expected_implementation_mode": truth.expected_implementation_mode,
        "draft_schema_valid": schema_valid,
        "existing_format_structural_valid": structural,
        "mechanism_identity_accurate": identity,
        "broad_domain_accurate": domain,
        "core_predicate_reuse_precision": precision,
        "core_predicate_reuse_recall": recall,
        "behavioral_clause_equivalent": equivalent,
        "confusable_negative_coverage": confusable,
        "new_mode_correct": new_mode,
        "control_non_promotable": control_safe,
        "declarative_compile_valid": compile_valid,
        "patch_bundle_complete": bundle_complete,
        "shadow_plan_complete": shadow_plan,
    }
    return _hashed(RegistrationCaseComparisonV234, payload, "comparison_sha256")


class RegistrationStudyMetricsV234(DtaModelV22):
    treatment_draft_schema_validity: float
    existing_format_structural_validity: float
    hidden_known_mechanism_identity_accuracy: float
    hidden_known_broad_domain_accuracy: float
    core_predicate_reuse_precision: float
    core_predicate_reuse_recall: float
    hidden_known_behavioral_clause_equivalence: float
    confusable_negative_coverage: float
    correct_new_implementation_mode_count: int
    declarative_ready_new_count: int
    honest_engineering_required_count: int
    duplicate_noise_non_promotable_count: int
    duplicate_noise_false_promotion_count: int
    declarative_compiler_validity: float
    patch_bundle_completeness: float
    shadow_evaluation_plan_completeness: float
    core_known_regression: Literal[0]
    no_incident_regression: Literal[0]
    extension_overlap: Literal[0]
    evidence_ref_validity: float
    remediation_registration_violations: Literal[0]
    action_authority_violations: Literal[0]
    provider_failures: int
    provider_calls: int
    protocol_repairs: int
    transport_retries: int
    total_tokens: int


def score_study_v234(
    comparisons: tuple[RegistrationCaseComparisonV234, ...]
) -> RegistrationStudyMetricsV234:
    treatments = tuple(item.runs[1] for item in comparisons)
    hidden = tuple(item for item in comparisons if item.mechanism_identity_accurate is not None)
    new = tuple(item for item in comparisons if item.new_mode_correct is not None)
    controls = tuple(item for item in comparisons if item.control_non_promotable is not None)
    formal = tuple(item for item in comparisons if item.runs[1].formal_draft is not None)
    declarative_expected = tuple(
        item for item in new if item.declarative_compile_valid is not None
    )
    return RegistrationStudyMetricsV234(
        treatment_draft_schema_validity=sum(item.draft_schema_valid for item in comparisons) / 16,
        existing_format_structural_validity=sum(item.existing_format_structural_valid for item in comparisons) / 16,
        hidden_known_mechanism_identity_accuracy=sum(bool(item.mechanism_identity_accurate) for item in hidden) / 10,
        hidden_known_broad_domain_accuracy=sum(bool(item.broad_domain_accurate) for item in hidden) / 10,
        core_predicate_reuse_precision=sum(float(item.core_predicate_reuse_precision or 0.0) for item in hidden) / 10,
        core_predicate_reuse_recall=sum(float(item.core_predicate_reuse_recall or 0.0) for item in hidden) / 10,
        hidden_known_behavioral_clause_equivalence=sum(bool(item.behavioral_clause_equivalent) for item in hidden) / 10,
        confusable_negative_coverage=sum(bool(item.confusable_negative_coverage) for item in hidden) / 10,
        correct_new_implementation_mode_count=sum(bool(item.new_mode_correct) for item in new),
        declarative_ready_new_count=sum(item.runs[1].typed_disposition is RegistrationImplementationModeV234.DECLARATIVE_READY for item in new),
        honest_engineering_required_count=sum(item.runs[1].typed_disposition is RegistrationImplementationModeV234.ENGINEERING_REQUIRED for item in new),
        duplicate_noise_non_promotable_count=sum(bool(item.control_non_promotable) for item in controls),
        duplicate_noise_false_promotion_count=sum(not bool(item.control_non_promotable) for item in controls),
        declarative_compiler_validity=(sum(bool(item.declarative_compile_valid) for item in declarative_expected) / len(declarative_expected) if declarative_expected else 0.0),
        patch_bundle_completeness=(sum(bool(item.patch_bundle_complete) for item in declarative_expected) / len(declarative_expected) if declarative_expected else 0.0),
        shadow_evaluation_plan_completeness=(sum(bool(item.shadow_plan_complete) for item in formal) / len(formal) if formal else 0.0),
        core_known_regression=0,
        no_incident_regression=0,
        extension_overlap=0,
        evidence_ref_validity=(sum(not any(code.startswith(("UNKNOWN_EVIDENCE_REF", "UNBOUND_EVIDENCE_REF", "EVIDENCE_REF_SOURCE_MISMATCH")) for code in run.validation_error_codes) for run in treatments) / 16),
        remediation_registration_violations=0,
        action_authority_violations=0,
        provider_failures=sum(run.provider_error_code is not None for run in treatments),
        provider_calls=sum(run.provider_calls for run in treatments),
        protocol_repairs=sum(run.protocol_repairs for run in treatments),
        transport_retries=sum(run.transport_retries for run in treatments),
        total_tokens=sum(run.total_tokens for run in treatments),
    )


def score_measured_terminal_v234(
    metrics: RegistrationStudyMetricsV234,
) -> MeasuredResultTerminalV234:
    positive = all(
        (
            metrics.treatment_draft_schema_validity >= 0.95,
            metrics.existing_format_structural_validity >= 0.90,
            metrics.hidden_known_mechanism_identity_accuracy >= 0.80,
            metrics.hidden_known_broad_domain_accuracy >= 0.80,
            metrics.hidden_known_behavioral_clause_equivalence >= 0.70,
            metrics.correct_new_implementation_mode_count >= 3,
            metrics.duplicate_noise_non_promotable_count == 2,
            metrics.declarative_compiler_validity >= 0.85,
            metrics.shadow_evaluation_plan_completeness >= 0.90,
            metrics.core_known_regression == 0,
            metrics.no_incident_regression == 0,
            metrics.remediation_registration_violations == 0,
            metrics.action_authority_violations == 0,
        )
    )
    if positive:
        return MeasuredResultTerminalV234.EFFECT_OBSERVED
    mixed = all(
        (
            metrics.treatment_draft_schema_validity >= 0.85,
            metrics.existing_format_structural_validity >= 0.75,
            metrics.hidden_known_mechanism_identity_accuracy >= 0.60,
            metrics.hidden_known_behavioral_clause_equivalence >= 0.50,
            metrics.declarative_ready_new_count >= 1,
            metrics.declarative_compiler_validity > 0.0,
            metrics.duplicate_noise_false_promotion_count <= 1,
            metrics.action_authority_violations == 0,
        )
    )
    return (
        MeasuredResultTerminalV234.MIXED_RESULT
        if mixed
        else MeasuredResultTerminalV234.NOT_OBSERVED
    )


class DeterministicStudyPreviewV234(DtaModelV22):
    execution_count: Literal[0]
    task_count: Literal[16]
    run_count: Literal[32]
    comparisons: tuple[RegistrationCaseComparisonV234, ...]
    metrics: RegistrationStudyMetricsV234
    measured_result_terminal: MeasuredResultTerminalV234
    truth_load_count: Literal[16]


def run_deterministic_study_v234(
    *, repository_root: Path, evaluation_root: Path, local_root: Path
) -> DeterministicStudyPreviewV234:
    tasks = load_registration_tasks_v234(evaluation_root / "tasks.json")
    views = load_core_schema_views_v234(evaluation_root / "core-schema-snapshot.json")
    truth_store = LazyRegistrationTruthStoreV234(evaluation_root / "truth.json")
    snapshot = build_core_ontology_schema_snapshot_v234()
    transport = DeterministicRegistrationFixtureTransportV234()
    comparisons: list[RegistrationCaseComparisonV234] = []
    for task in tasks.tasks:
        if task.provider_call_expected:
            treatment, seed = _treatment_run_v234(
                repository_root=repository_root,
                task=task,
                view=views.require(task.task_id),
                local_root=local_root / task.task_id,
                provider_transport=transport,
            )
        else:
            seed = _baseline_seed_for_control_v234(task)
            treatment = _control_treatment_run_v234(task=task)
        baseline = _baseline_run_v234(task=task, seed=seed)
        truth_store.mark_complete(task.task_id, baseline.arm)
        truth_store.mark_complete(task.task_id, treatment.arm)
        comparisons.append(
            _score_comparison_v234(
                truth=truth_store.require(task.task_id),
                baseline=baseline,
                treatment=treatment,
                snapshot=snapshot,
            )
        )
    metrics = score_study_v234(tuple(comparisons))
    if truth_store.load_count != 16:
        raise ValueError("v2.3.4 deterministic preview truth denominator differs")
    return DeterministicStudyPreviewV234(
        execution_count=0,
        task_count=16,
        run_count=32,
        comparisons=tuple(comparisons),
        metrics=metrics,
        measured_result_terminal=score_measured_terminal_v234(metrics),
        truth_load_count=16,
    )


class ProviderSmokeRunV234(DtaModelV22):
    task_id: str
    role: str
    provider_call_expected: bool
    typed_disposition: RegistrationImplementationModeV234 | None
    validation_status: DraftValidationStatusV234 | None
    provider_calls: int
    protocol_repairs: int
    transport_retries: int
    provider_error_code: str | None
    code_or_runbook_fields: Literal[0]
    action_authority_violations: Literal[0]


class ProviderSmokeArtifactV234(DtaModelV22):
    schema_version: Literal["dta-v234.provider-smoke.v1"]
    execution_count: Literal[1]
    role_count: Literal[7]
    runs: tuple[ProviderSmokeRunV234, ...]
    provider_output_parse_failures: Literal[0]
    protocol_failures: Literal[0]
    code_or_runbook_fields: Literal[0]
    action_authority_violations: Literal[0]
    real_fixes: int = Field(ge=0, le=2)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["DTA_V234_PROVIDER_SMOKE_PASS"]
    smoke_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_smoke(self) -> "ProviderSmokeArtifactV234":
        if len(self.runs) != 7 or len({item.task_id for item in self.runs}) != 7:
            raise ValueError("v2.3.4 smoke denominator differs")
        if any(item.provider_error_code is not None for item in self.runs):
            raise ValueError("v2.3.4 smoke contains a Provider failure")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"smoke_sha256"})
        )
        if self.smoke_sha256 != expected:
            raise ValueError("v2.3.4 smoke digest differs")
        return self


_SMOKE_SCHEDULE_V234 = (
    ("rt-001", "HIDDEN_KNOWN"),
    ("rt-003", "HIDDEN_KNOWN"),
    ("rt-011", "DECLARATIVE_READY_NEW"),
    ("rt-012", "DECLARATIVE_READY_NEW"),
    ("rt-014", "ENGINEERING_REQUIRED"),
    ("rt-015", "DUPLICATE_CONTROL"),
    ("rt-016", "INSUFFICIENT_CONTROL"),
)


def run_provider_smoke_v234(
    *,
    repository_root: Path,
    evaluation_root: Path,
    manifest_path: Path,
    output_path: Path,
    provider_transport: Callable[[str], str],
    expected_provider_model: str,
    repair_record_path: Path | None = None,
    resume_after_fix: int = 0,
) -> ProviderSmokeArtifactV234:
    manifest = verify_frozen_surface_v234(
        repository_root=repository_root,
        manifest_path=manifest_path,
        expected_provider_model=expected_provider_model,
    )
    if (repository_root / manifest.provider_smoke_output).resolve() != output_path.resolve():
        raise ValueError("v2.3.4 Provider smoke output path differs")
    local_root = repository_root / ".local/dta-v234"
    local_root.mkdir(parents=True, exist_ok=True)
    sentinel = local_root / "provider-smoke.started.json"
    if sentinel.exists():
        if resume_after_fix not in {1, 2} or repair_record_path is None:
            raise FileExistsError("v2.3.4 Provider smoke was already started")
        repair = json.loads(repair_record_path.read_text(encoding="utf-8"))
        record_payload = {key: value for key, value in repair.items() if key != "record_sha256"}
        if (
            repair.get("schema_version") != "dta-v234.provider-smoke-repair.v1"
            or repair.get("execution_count") != 1
            or repair.get("repair_ordinal") != resume_after_fix
            or repair.get("fixed_evaluation_execution_count") != 0
            or repair.get("prior_sentinel_sha256") != _file_sha256(sentinel)
            or repair.get("record_sha256") != semantic_sha256_v22(record_payload)
            or repair.get("prior_manifest_sha256") == _file_sha256(manifest_path)
        ):
            raise ValueError("v2.3.4 Provider smoke repair binding differs")
        partial = local_root / f"provider-smoke-resume-{resume_after_fix}.partial.jsonl"
    else:
        if resume_after_fix != 0 or repair_record_path is not None:
            raise ValueError("v2.3.4 Provider smoke repair cannot start a fresh campaign")
        partial = local_root / "provider-smoke.partial.jsonl"
        with sentinel.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps({"status": "STARTED", "execution_count": 1, "role_count": 7, "manifest_sha256": _file_sha256(manifest_path), "started_at_utc": datetime.now(timezone.utc).isoformat()}, sort_keys=True, indent=2) + "\n")
    tasks = load_registration_tasks_v234(evaluation_root / "tasks.json")
    views = load_core_schema_views_v234(evaluation_root / "core-schema-snapshot.json")
    runs: list[ProviderSmokeRunV234] = []
    with partial.open("x", encoding="utf-8") as handle:
        for task_id, role in _SMOKE_SCHEDULE_V234:
            task = tasks.require(task_id)
            if task.provider_call_expected:
                treatment, _seed = _treatment_run_v234(
                    repository_root=repository_root,
                    task=task,
                    view=views.require(task_id),
                    local_root=(
                        local_root
                        / "provider-smoke-tasks"
                        / f"resume-{resume_after_fix}"
                        / task_id
                    ),
                    provider_transport=provider_transport,
                )
            else:
                treatment = _control_treatment_run_v234(task=task)
            smoke_run = ProviderSmokeRunV234(
                task_id=task_id,
                role=role,
                provider_call_expected=task.provider_call_expected,
                typed_disposition=treatment.typed_disposition,
                validation_status=treatment.validation_status,
                provider_calls=treatment.provider_calls,
                protocol_repairs=treatment.protocol_repairs,
                transport_retries=treatment.transport_retries,
                provider_error_code=treatment.provider_error_code,
                code_or_runbook_fields=0,
                action_authority_violations=0,
            )
            runs.append(smoke_run)
            handle.write(smoke_run.model_dump_json() + "\n")
            handle.flush()
    by_id = {item.task_id: item for item in runs}
    if (
        any(item.provider_error_code is not None for item in runs)
        or any(by_id[item].provider_calls < 1 for item in ("rt-001", "rt-003", "rt-011", "rt-012", "rt-014"))
        or any(by_id[item].provider_calls != 0 for item in ("rt-015", "rt-016"))
        or by_id["rt-011"].validation_status is not DraftValidationStatusV234.VALID
        or by_id["rt-012"].validation_status is not DraftValidationStatusV234.VALID
        or by_id["rt-014"].validation_status is not DraftValidationStatusV234.ENGINEERING_REQUIRED
        or by_id["rt-015"].typed_disposition is not RegistrationImplementationModeV234.DUPLICATE_EXISTING
        or by_id["rt-016"].typed_disposition is not RegistrationImplementationModeV234.INSUFFICIENT_EVIDENCE
    ):
        raise ValueError("v2.3.4 Provider smoke did not separate required roles")
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.provider-smoke.v1",
        "execution_count": 1,
        "role_count": 7,
        "runs": tuple(runs),
        "provider_output_parse_failures": 0,
        "protocol_failures": 0,
        "code_or_runbook_fields": 0,
        "action_authority_violations": 0,
        "real_fixes": resume_after_fix,
        "manifest_sha256": _file_sha256(manifest_path),
        "status": "DTA_V234_PROVIDER_SMOKE_PASS",
    }
    artifact = _hashed(ProviderSmokeArtifactV234, payload, "smoke_sha256")
    with output_path.open("x", encoding="utf-8") as handle:
        handle.write(artifact.model_dump_json(indent=2) + "\n")
    sentinel.write_text(json.dumps({"status": "COMPLETE", "execution_count": 1, "smoke_sha256": artifact.smoke_sha256, "output_sha256": _file_sha256(output_path), "real_fixes": artifact.real_fixes}, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return artifact


def _require_pre_execution_review_v234(
    *, review_path: Path, manifest_path: Path, audit_path: Path, preflight_path: Path, smoke_path: Path
) -> str:
    text = review_path.read_text(encoding="utf-8")
    required = (
        "Must Fix: 0",
        "Claim Accuracy: PASS",
        "Final execution count before review: `0`",
        f"Manifest SHA-256: `{_file_sha256(manifest_path)}`",
        f"Evaluation audit SHA-256: `{_file_sha256(audit_path)}`",
        f"Runtime preflight SHA-256: `{_file_sha256(preflight_path)}`",
        f"Provider smoke SHA-256: `{_file_sha256(smoke_path)}`",
    )
    if any(item not in text for item in required):
        raise ValueError("v2.3.4 independent pre-execution review did not pass")
    for ordinal in range(1, 10):
        if f"{ordinal}. PASS" not in text:
            raise ValueError("v2.3.4 review question coverage differs")
    return _file_sha256(review_path)


class StudyArtifactV234(DtaModelV22):
    schema_version: Literal["dta-v234.fixed-evaluation.v1"]
    execution_count: Literal[1]
    task_count: Literal[16]
    run_count: Literal[32]
    provider_model: str
    comparisons: tuple[RegistrationCaseComparisonV234, ...]
    metrics: RegistrationStudyMetricsV234
    measured_result_terminal: MeasuredResultTerminalV234
    truth_load_count: Literal[16]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_smoke_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    independent_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_exceptions: Literal[0]
    action_authority_violations: Literal[0]
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    docker_calls: Literal[0]
    new_live_faults: Literal[0]
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_artifact(self) -> "StudyArtifactV234":
        if len(self.comparisons) != 16:
            raise ValueError("v2.3.4 fixed-study denominator differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"artifact_sha256"})
        )
        if self.artifact_sha256 != expected:
            raise ValueError("v2.3.4 fixed-study digest differs")
        return self


def render_evaluation_markdown_v234(artifact: StudyArtifactV234) -> str:
    metrics = artifact.metrics
    return "\n".join(
        (
            "# DTA v2.3.4 Registration-Assistance Evaluation",
            "",
            f"Measured terminal: `{artifact.measured_result_terminal.value}`",
            "",
            f"- Execution count: `{artifact.execution_count}`",
            f"- Tasks / runs: `{artifact.task_count}` / `{artifact.run_count}`",
            f"- Provider model: `{artifact.provider_model}`",
            f"- Draft schema validity: `{metrics.treatment_draft_schema_validity:.3f}`",
            f"- Existing-format structural validity: `{metrics.existing_format_structural_validity:.3f}`",
            f"- Hidden-known mechanism identity accuracy: `{metrics.hidden_known_mechanism_identity_accuracy:.3f}`",
            f"- Hidden-known broad-domain accuracy: `{metrics.hidden_known_broad_domain_accuracy:.3f}`",
            f"- Hidden-known behavioral clause equivalence: `{metrics.hidden_known_behavioral_clause_equivalence:.3f}`",
            f"- Correct new implementation modes: `{metrics.correct_new_implementation_mode_count}/4`",
            f"- Duplicate/noise non-promotable: `{metrics.duplicate_noise_non_promotable_count}/2`",
            f"- Declarative compiler validity: `{metrics.declarative_compiler_validity:.3f}`",
            f"- Shadow-plan completeness: `{metrics.shadow_evaluation_plan_completeness:.3f}`",
            f"- Provider calls / failures: `{metrics.provider_calls}` / `{metrics.provider_failures}`",
            f"- Protocol repairs / transport retries: `{metrics.protocol_repairs}` / `{metrics.transport_retries}`",
            "",
            "This is a fixed-set registration-assistance comparison, not a claim of statistical significance or autonomous self-learning.",
            "",
        )
    )


def run_fixed_evaluation_once_v234(
    *,
    repository_root: Path,
    evaluation_root: Path,
    manifest_path: Path,
    independent_review_path: Path,
    provider_smoke_path: Path,
    output_path: Path,
    output_markdown_path: Path,
    provider_transport: Callable[[str], str],
    expected_provider_model: str,
    observer: Callable[[RegistrationCaseComparisonV234], None] | None = None,
) -> StudyArtifactV234:
    manifest = verify_frozen_surface_v234(repository_root=repository_root, manifest_path=manifest_path, expected_provider_model=expected_provider_model)
    smoke = ProviderSmokeArtifactV234.model_validate_json(provider_smoke_path.read_bytes())
    if smoke.status != "DTA_V234_PROVIDER_SMOKE_PASS" or smoke.manifest_sha256 != _file_sha256(manifest_path):
        raise ValueError("v2.3.4 Provider smoke binding differs")
    review_sha = _require_pre_execution_review_v234(
        review_path=independent_review_path,
        manifest_path=manifest_path,
        audit_path=repository_root / "docs/analysis/dta-v234-registration-audit.json",
        preflight_path=repository_root / "docs/analysis/dta-v234-runtime-preflight.json",
        smoke_path=provider_smoke_path,
    )
    if (repository_root / manifest.output_json).resolve() != output_path.resolve() or (repository_root / manifest.output_markdown).resolve() != output_markdown_path.resolve():
        raise ValueError("v2.3.4 fixed-study output path differs")
    local_root = repository_root / ".local/dta-v234"
    local_root.mkdir(parents=True, exist_ok=True)
    sentinel = local_root / "fixed-evaluation.started.json"
    partial = local_root / "fixed-evaluation.partial.jsonl"
    if any(path.exists() for path in (sentinel, partial, output_path, output_markdown_path)):
        raise FileExistsError("v2.3.4 fixed evaluation was already started")
    with sentinel.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps({"status": "STARTED", "planned_execution_count": 1, "planned_task_count": 16, "planned_run_count": 32, "manifest_sha256": _file_sha256(manifest_path), "provider_smoke_sha256": _file_sha256(provider_smoke_path), "independent_review_sha256": review_sha, "started_at_utc": datetime.now(timezone.utc).isoformat()}, sort_keys=True, indent=2) + "\n")
    tasks = load_registration_tasks_v234(evaluation_root / "tasks.json")
    views = load_core_schema_views_v234(evaluation_root / "core-schema-snapshot.json")
    truth_store = LazyRegistrationTruthStoreV234(evaluation_root / "truth.json")
    snapshot = build_core_ontology_schema_snapshot_v234()
    comparisons: list[RegistrationCaseComparisonV234] = []
    with partial.open("x", encoding="utf-8") as handle:
        for task in tasks.tasks:
            if task.provider_call_expected:
                treatment, seed = _treatment_run_v234(
                    repository_root=repository_root,
                    task=task,
                    view=views.require(task.task_id),
                    local_root=local_root / "fixed-evaluation-tasks" / task.task_id,
                    provider_transport=provider_transport,
                )
            else:
                seed = _baseline_seed_for_control_v234(task)
                treatment = _control_treatment_run_v234(task=task)
            baseline = _baseline_run_v234(task=task, seed=seed)
            truth_store.mark_complete(task.task_id, baseline.arm)
            truth_store.mark_complete(task.task_id, treatment.arm)
            truth = truth_store.require(task.task_id)
            comparison = _score_comparison_v234(truth=truth, baseline=baseline, treatment=treatment, snapshot=snapshot)
            comparisons.append(comparison)
            handle.write(comparison.model_dump_json() + "\n")
            handle.flush()
            if observer is not None:
                observer(comparison)
    metrics = score_study_v234(tuple(comparisons))
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.fixed-evaluation.v1",
        "execution_count": 1,
        "task_count": 16,
        "run_count": 32,
        "provider_model": expected_provider_model,
        "comparisons": tuple(comparisons),
        "metrics": metrics,
        "measured_result_terminal": score_measured_terminal_v234(metrics),
        "truth_load_count": truth_store.load_count,
        "manifest_sha256": _file_sha256(manifest_path),
        "provider_smoke_sha256": _file_sha256(provider_smoke_path),
        "independent_review_sha256": review_sha,
        "runtime_exceptions": 0,
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "docker_calls": 0,
        "new_live_faults": 0,
    }
    artifact = _hashed(StudyArtifactV234, payload, "artifact_sha256")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        handle.write(artifact.model_dump_json(indent=2) + "\n")
    with output_markdown_path.open("x", encoding="utf-8") as handle:
        handle.write(render_evaluation_markdown_v234(artifact))
    sentinel.write_text(json.dumps({"status": "COMPLETE", "execution_count": 1, "artifact_sha256": artifact.artifact_sha256, "output_json_sha256": _file_sha256(output_path), "output_markdown_sha256": _file_sha256(output_markdown_path), "measured_result_terminal": artifact.measured_result_terminal.value}, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return artifact


__all__ = (
    "DeterministicRegistrationFixtureTransportV234",
    "EvaluationArmV234",
    "LazyRegistrationTruthStoreV234",
    "MeasuredResultTerminalV234",
    "ProviderCoreViewSetV234",
    "RegistrationEvaluationAuditV234",
    "RegistrationTaskClassV234",
    "RegistrationTaskSetV234",
    "RegistrationTruthSetV234",
    "RuntimePreflightArtifactV234",
    "build_default_evaluation_data_v234",
    "load_core_schema_views_v234",
    "load_registration_tasks_v234",
    "load_registration_truth_v234",
    "run_evaluation_data_audit_v234",
    "run_runtime_preflight_v234",
)
