"""Fixed conflict-aware comparison contracts and runtime for DTA v2.3.1."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator
from ecomsre.model.gateway import OpenAICompatibleConfig

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.contrastive_actions_v225 import ContrastiveResourceActionV225
from ecomsre.dta_v2.v22.diagnosis import AdmittedDiagnosisV22
from ecomsre.dta_v2.v22.memory import (
    ResourceSalientPayloadV22,
    SalientEvidenceMemoryV22,
    SignalStrengthV22,
    build_memory_views_v22,
)
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.practical_replay import NormalizedPracticalCaseV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReplayCaptureV22
from ecomsre.dta_v2.v22.practical_runner import _baseline
from ecomsre.dta_v2.v22.replay import QuerySpecificReplayBackendV22
from ecomsre.dta_v2.v23.conflict_model_v231 import (
    ConflictAssessmentV231,
    ConflictTypeV231,
)
from ecomsre.dta_v2.v23.contracts_v231 import ProvisionalIncidentReportV231
from ecomsre.dta_v2.v23.discovery_provider import (
    DiscoveryProviderProtocolFailureV23,
    DiscoveryProviderTransportErrorV23,
)
from ecomsre.dta_v2.v23.discovery_provider_v231 import (
    build_discovery_provider_request_v231,
    call_discovery_provider_v231,
)
from ecomsre.dta_v2.v23.discovery_router import (
    DiscoveryReadOutcomeClassV23,
    NegativeCoverageLedgerV23,
    build_discovery_plan_v23,
    record_discovery_outcome_v23,
    resolve_discovery_action_v23,
)
from ecomsre.dta_v2.v23.discovery_runtime import (
    _build_read_outcome_v23,
    _classify_discovery_outcome,
)
from ecomsre.dta_v2.v23.discovery_runtime_v231 import (
    ConflictAwareDiscoveryStateV231,
    build_conflict_aware_state_v231,
)
from ecomsre.dta_v2.v23.evaluation import (
    EvaluationArmRunV23,
    EvaluationArmV23,
    ProviderCostV23,
    OpenAICompatibleDiscoveryTransportV23,
    _CommonContextV23,
    _build_common_context_v23,
    load_evaluation_case_set_v23,
    materialize_evaluation_case_v23,
    run_open_world_arm_v23,
)
from ecomsre.dta_v2.v23.contracts_v231 import ReportUncertaintyModeV231
from ecomsre.dta_v2.v23.discovery_provider_v231 import DISCOVERY_SYSTEM_PROMPT_V231
from ecomsre.dta_v2.v23.generic_anomalies import extract_generic_anomalies_v23
from ecomsre.dta_v2.v23.novelty_gate_v231 import NoveltyDispositionV231, NoveltyGateDecisionV231
from ecomsre.dta_v2.v23.residual_graph import (
    ResidualEvidenceGraphV23,
    build_known_terminal_candidates_v23,
    build_residual_evidence_graph_v23,
)


class EvaluationCategoryV231(str, Enum):
    NOVEL_HIDDEN = "NOVEL_HIDDEN"
    NOVEL_UNREGISTERED = "NOVEL_UNREGISTERED"
    REGISTERED_KNOWN = "REGISTERED_KNOWN"
    NO_INCIDENT = "NO_INCIDENT"
    INSUFFICIENT_CONFLICT = "INSUFFICIENT_CONFLICT"


class MeasuredResultTerminalV231(str, Enum):
    EFFECT_OBSERVED = "DTA_V231_CONFLICT_AWARE_DISCOVERY_EFFECT_OBSERVED"
    MIXED_RESULT = "DTA_V231_CONFLICT_AWARE_DISCOVERY_MIXED_RESULT"
    NOT_OBSERVED = "DTA_V231_CONFLICT_AWARE_DISCOVERY_NOT_OBSERVED"


class EvaluationCaseSpecV231(DtaModelV22):
    case_id: str = Field(pattern=r"^vx-[0-9]{3}$")
    source_bytes_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_services: tuple[str, ...] = Field(min_length=2, max_length=4)
    topology_edges: tuple[tuple[str, str], ...]
    capture: ReplayCaptureV22

    @model_validator(mode="after")
    def require_observer_case(self) -> "EvaluationCaseSpecV231":
        if self.candidate_services != tuple(sorted(set(self.candidate_services))):
            raise ValueError("v2.3.1 fixed candidates are not canonical")
        canonical_edges = tuple(
            sorted(
                {
                    (left, right) if left < right else (right, left)
                    for left, right in self.topology_edges
                }
            )
        )
        if self.topology_edges != canonical_edges:
            raise ValueError("v2.3.1 fixed topology is not canonical")
        candidates = set(self.candidate_services)
        if any(set(edge) - candidates for edge in self.topology_edges):
            raise ValueError("v2.3.1 fixed topology escapes candidates")
        observed_services = {
            *(item.service for item in self.capture.metrics),
            *(item.service for item in self.capture.logs),
            *(item.service for item in self.capture.traces),
            *(item.service for item in self.capture.runtime),
            *(item.service for item in self.capture.resources),
            *(item.service for item in self.capture.changes),
        }
        if observed_services - candidates:
            raise ValueError("v2.3.1 fixed capture escapes candidates")
        expected = semantic_sha256_v22(
            {
                "case_id": self.case_id,
                "candidate_services": self.candidate_services,
                "topology_edges": self.topology_edges,
                "capture": self.capture.model_dump(mode="json"),
            }
        )
        if self.source_bytes_sha256 != expected:
            raise ValueError("v2.3.1 fixed observer bytes differ")
        return self


class EvaluationCaseSetV231(DtaModelV22):
    schema_version: Literal["dta-v231.evaluation-case-set.v1"]
    freeze_id: Literal["dta-v231-independent-freeze-20260825-c"]
    cases: tuple[EvaluationCaseSpecV231, ...] = Field(min_length=24, max_length=24)

    @model_validator(mode="after")
    def require_set(self) -> "EvaluationCaseSetV231":
        ids = tuple(item.case_id for item in self.cases)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("v2.3.1 evaluation cases are not canonical")
        if len({item.source_bytes_sha256 for item in self.cases}) != len(self.cases):
            raise ValueError("v2.3.1 fixed observer bytes are not unique")
        return self


class EvaluationTruthV231(DtaModelV22):
    case_id: str = Field(pattern=r"^vx-[0-9]{3}$")
    category: EvaluationCategoryV231
    expected_disposition: str
    expected_root_service: str | None
    expected_broad_domain: str | None
    expected_mechanism: MechanismV22 | None
    semantic_concepts: tuple[str, ...]
    counterfactual_pair_id: str | None
    requires_discovery_read: StrictBool
    empty_or_misleading_action: StrictBool
    conflict_prone_novelty: StrictBool
    multi_coherent_interpretations: StrictBool
    true_irreconcilable_conflict: StrictBool

    @model_validator(mode="after")
    def require_truth(self) -> "EvaluationTruthV231":
        novelty = self.category in {
            EvaluationCategoryV231.NOVEL_HIDDEN,
            EvaluationCategoryV231.NOVEL_UNREGISTERED,
        }
        if self.conflict_prone_novelty and not novelty:
            raise ValueError("conflict-prone marker is outside novelty")
        if self.true_irreconcilable_conflict != (
            self.category is EvaluationCategoryV231.INSUFFICIENT_CONFLICT
        ):
            raise ValueError("irreconcilable marker differs from control category")
        if novelty and self.expected_root_service is None:
            raise ValueError("novelty truth lacks a root service")
        if self.category is EvaluationCategoryV231.REGISTERED_KNOWN and (
            self.expected_root_service is None or self.expected_mechanism is None
        ):
            raise ValueError("known truth lacks a mechanism or root")
        return self


class EvaluationTruthSetV231(DtaModelV22):
    schema_version: Literal["dta-v231.evaluation-truth-set.v1"]
    truths: tuple[EvaluationTruthV231, ...] = Field(min_length=24, max_length=24)

    @model_validator(mode="after")
    def require_set(self) -> "EvaluationTruthSetV231":
        ids = tuple(item.case_id for item in self.truths)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("v2.3.1 evaluation truths are not canonical")
        expected = {
            EvaluationCategoryV231.NOVEL_HIDDEN: 10,
            EvaluationCategoryV231.NOVEL_UNREGISTERED: 4,
            EvaluationCategoryV231.REGISTERED_KNOWN: 4,
            EvaluationCategoryV231.NO_INCIDENT: 3,
            EvaluationCategoryV231.INSUFFICIENT_CONFLICT: 3,
        }
        counts = {
            category: sum(item.category is category for item in self.truths)
            for category in EvaluationCategoryV231
        }
        if counts != expected:
            raise ValueError("v2.3.1 evaluation truth composition differs")
        if sum(item.conflict_prone_novelty for item in self.truths) != 8:
            raise ValueError("v2.3.1 evaluation lacks eight conflict-prone novelty cases")
        if sum(item.multi_coherent_interpretations for item in self.truths) < 4:
            raise ValueError("v2.3.1 evaluation lacks four coherent competitions")
        if sum(item.true_irreconcilable_conflict for item in self.truths) != 3:
            raise ValueError("v2.3.1 evaluation lacks three irreconcilable controls")
        if sum(item.requires_discovery_read for item in self.truths) < 8:
            raise ValueError("v2.3.1 evaluation lacks eight discovery-read cases")
        pairs = {
            item.counterfactual_pair_id
            for item in self.truths
            if item.counterfactual_pair_id is not None
        }
        if len(pairs) < 4 or any(
            sum(item.counterfactual_pair_id == pair for item in self.truths) != 2
            for pair in pairs
        ):
            raise ValueError("v2.3.1 counterfactual pairs differ")
        return self

    def require(self, case_id: str) -> EvaluationTruthV231:
        item = next((value for value in self.truths if value.case_id == case_id), None)
        if item is None:
            raise ValueError("v2.3.1 evaluation truth case is absent")
        return item


class EvaluationOntologyViewSpecV231(DtaModelV22):
    case_id: str = Field(pattern=r"^vx-[0-9]{3}$")
    hidden_mechanism: MechanismV22 | None


class EvaluationOntologyViewSetV231(DtaModelV22):
    schema_version: Literal["dta-v231.evaluation-ontology-view-set.v1"]
    views: tuple[EvaluationOntologyViewSpecV231, ...] = Field(
        min_length=24,
        max_length=24,
    )

    @model_validator(mode="after")
    def require_set(self) -> "EvaluationOntologyViewSetV231":
        ids = tuple(item.case_id for item in self.views)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("v2.3.1 ontology views are not canonical")
        registered = (
            MechanismV22.CONFIGURATION_ERROR,
            MechanismV22.SERVICE_UNAVAILABLE,
            MechanismV22.CPU_SATURATION,
            MechanismV22.MEMORY_LEAK,
            MechanismV22.DEPENDENCY_LATENCY,
        )
        if any(
            sum(item.hidden_mechanism is mechanism for item in self.views) < 2
            for mechanism in registered
        ):
            raise ValueError("v2.3.1 hidden ontology composition differs")
        return self

    def require(self, case_id: str) -> EvaluationOntologyViewSpecV231:
        item = next((value for value in self.views if value.case_id == case_id), None)
        if item is None:
            raise ValueError("v2.3.1 ontology view is absent")
        return item


def load_evaluation_case_set_v231(path: Path) -> EvaluationCaseSetV231:
    return EvaluationCaseSetV231.model_validate_json(path.read_bytes())


def load_evaluation_truth_set_v231(path: Path) -> EvaluationTruthSetV231:
    return EvaluationTruthSetV231.model_validate_json(path.read_bytes())


def load_evaluation_views_v231(path: Path) -> EvaluationOntologyViewSetV231:
    return EvaluationOntologyViewSetV231.model_validate_json(path.read_bytes())


def materialize_evaluation_case_v231(
    *,
    repository_root: Path,
    spec: EvaluationCaseSpecV231,
) -> NormalizedPracticalCaseV22:
    del repository_root
    return NormalizedPracticalCaseV22(
        schema_version="dta-v22.practical-normalized-case.v1",
        case_id=spec.case_id,
        source_bytes_sha256=spec.source_bytes_sha256,
        candidate_services=spec.candidate_services,
        topology_edges=spec.topology_edges,
        capture=spec.capture,
        normalization_notes=(
            "Independent DTA v2.3.1 observer-complete fixed case.",
            "Evaluator truth is stored separately from observer-visible bytes.",
        ),
    )


class EvaluationPolicyV231(str, Enum):
    V23_STRICT_CONFLICT_GATE = "V23_STRICT_CONFLICT_GATE"
    V231_CONFLICT_AWARE_GATE = "V231_CONFLICT_AWARE_GATE"


class EvaluationArmRunV231(DtaModelV22):
    schema_version: Literal["dta-v231.evaluation-arm-run.v1"]
    case_id: str = Field(pattern=r"^vx-[0-9]{3}$")
    policy: Literal[EvaluationPolicyV231.V231_CONFLICT_AWARE_GATE]
    case_bytes_sha256: str
    active_view_sha256: str
    bootstrap_memory_sha256: str
    common_memory_sha256: str
    common_read_count: StrictInt = Field(ge=0, le=2)
    discovery_read_count: StrictInt = Field(ge=0, le=3)
    conflict_resolution_read_used: StrictBool
    conflict_assessment_before_read: ConflictAssessmentV231 | None
    conflict_resolution_outcome_class: DiscoveryReadOutcomeClassV23 | None
    final_disposition: str
    known_admission_sha256: str
    admitted_diagnosis: AdmittedDiagnosisV22 | None
    known_mechanism: MechanismV22 | None
    known_root_service: str | None
    no_incident_admissible: StrictBool
    residual_graph: ResidualEvidenceGraphV23
    conflict_assessment: ConflictAssessmentV231
    novelty_decision: NoveltyGateDecisionV231
    memory_evidence_refs: tuple[str, ...]
    negative_coverage: NegativeCoverageLedgerV23
    provisional_report: ProvisionalIncidentReportV231 | None
    provider_error_code: str | None
    provider_cost: ProviderCostV23
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    action_authority_violations: Literal[0]
    run_sha256: str

    @model_validator(mode="after")
    def require_run(self) -> "EvaluationArmRunV231":
        if self.conflict_resolution_read_used and self.discovery_read_count < 1:
            raise ValueError("v2.3.1 conflict read is not charged")
        if self.conflict_resolution_read_used != (
            self.conflict_assessment_before_read is not None
            and self.conflict_resolution_outcome_class is not None
        ):
            raise ValueError("v2.3.1 conflict read accounting is incomplete")
        if (self.admitted_diagnosis is None) != (self.known_mechanism is None):
            raise ValueError("v2.3.1 known projection differs from admission")
        if self.admitted_diagnosis is not None and (
            self.known_mechanism is not self.admitted_diagnosis.mechanism
            or self.known_root_service != self.admitted_diagnosis.root_service
        ):
            raise ValueError("v2.3.1 known fields differ from admission")
        reportable = self.final_disposition in {
            NoveltyDispositionV231.UNREGISTERED_INCIDENT_SUSPECTED.value,
            NoveltyDispositionV231.UNREGISTERED_INCIDENT_WITH_COMPETING_HYPOTHESES.value,
            NoveltyDispositionV231.KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY.value,
        }
        if self.provider_error_code is None and reportable != (
            self.provisional_report is not None
        ):
            raise ValueError("v2.3.1 evaluation report differs from disposition")
        if self.provisional_report is not None:
            if self.provisional_report.action_authority != "NONE":
                raise ValueError("v2.3.1 evaluation report has action authority")
            cited = {
                *self.provisional_report.supporting_evidence_refs,
                *self.provisional_report.contradicting_evidence_refs,
            }
            if not cited.issubset(self.memory_evidence_refs):
                raise ValueError("v2.3.1 evaluation report cites outside memory")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"run_sha256"})
        )
        if self.run_sha256 != expected:
            raise ValueError("v2.3.1 evaluation arm digest differs")
        return self


class EvaluationCasePairV231(DtaModelV22):
    schema_version: Literal["dta-v231.evaluation-case-pair.v1"]
    case_id: str = Field(pattern=r"^vx-[0-9]{3}$")
    baseline_policy: Literal[EvaluationPolicyV231.V23_STRICT_CONFLICT_GATE]
    treatment_policy: Literal[EvaluationPolicyV231.V231_CONFLICT_AWARE_GATE]
    strict: EvaluationArmRunV23
    treatment: EvaluationArmRunV231
    evaluator_truth: EvaluationTruthV231
    pair_sha256: str

    @model_validator(mode="after")
    def require_pair(self) -> "EvaluationCasePairV231":
        if {
            self.case_id,
            self.strict.case_id,
            self.treatment.case_id,
            self.evaluator_truth.case_id,
        } != {self.case_id}:
            raise ValueError("v2.3.1 pair case IDs differ")
        if self.strict.arm is not EvaluationArmV23.OPEN_WORLD_DISCOVERY:
            raise ValueError("v2.3.1 baseline is not the exact v2.3 open-world arm")
        for field in (
            "case_bytes_sha256",
            "active_view_sha256",
            "bootstrap_memory_sha256",
            "common_memory_sha256",
            "common_read_count",
            "known_admission_sha256",
        ):
            if getattr(self.strict, field) != getattr(self.treatment, field):
                raise ValueError(f"v2.3.1 pair common input differs: {field}")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"pair_sha256"})
        )
        if self.pair_sha256 != expected:
            raise ValueError("v2.3.1 evaluation pair digest differs")
        return self


class LazyTruthStoreV231:
    """Keep evaluator truth unopened until both policy runs are complete."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._loaded_case_ids: set[str] = set()

    @property
    def loaded_case_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._loaded_case_ids))

    def load_case_after_both_arms(
        self,
        *,
        case_id: str,
        strict: EvaluationArmRunV23,
        treatment: EvaluationArmRunV231,
    ) -> EvaluationTruthV231:
        if strict.case_id != case_id or treatment.case_id != case_id:
            raise ValueError("truth unlock arm case differs")
        if case_id in self._loaded_case_ids:
            raise ValueError("v2.3.1 evaluator truth case was loaded twice")
        truth = load_evaluation_truth_set_v231(self._path).require(case_id)
        self._loaded_case_ids.add(case_id)
        return truth


def _residual_graph_v231(
    *,
    context: _CommonContextV23,
    memory: SalientEvidenceMemoryV22,
) -> ResidualEvidenceGraphV23:
    known = build_known_terminal_candidates_v23(
        admitted_diagnoses=context.admission.admitted_diagnoses,
    )
    return build_residual_evidence_graph_v23(
        candidate_services=context.case.candidate_services,
        generic_anomalies=extract_generic_anomalies_v23(
            memory=memory,
            candidate_services=context.case.candidate_services,
        ),
        known_terminal_candidates=known,
        memory=memory,
    )


def _normal_resource_services_v231(
    *,
    graph: ResidualEvidenceGraphV23,
    memory: SalientEvidenceMemoryV22,
) -> tuple[str, ...]:
    coverage = next(
        item
        for item in graph.source_coverage
        if item.source is EvidenceSourceV22.RESOURCES
    )
    candidates = set(graph.candidate_services)
    if set(coverage.covered_services) != candidates:
        return ()
    by_service = {
        service: tuple(
            fact
            for fact in memory.salient_facts
            if fact.source is EvidenceSourceV22.RESOURCES
            and fact.service == service
            and isinstance(fact.payload, ResourceSalientPayloadV22)
        )
        for service in candidates
    }
    return tuple(
        sorted(
            service
            for service, facts in by_service.items()
            if facts
            and all(fact.signal_strength is SignalStrengthV22.NONE for fact in facts)
        )
    )
def _provider_cost_zero_v231() -> ProviderCostV23:
    return ProviderCostV23(
        provider_calls=0,
        protocol_repairs=0,
        transport_retries=0,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        latency_ms=0.0,
    )


def _build_treatment_run_v231(
    *,
    context: _CommonContextV23,
    state: ConflictAwareDiscoveryStateV231,
    negative: NegativeCoverageLedgerV23,
    memory: SalientEvidenceMemoryV22,
    discovery_reads: int,
    conflict_resolution_read_used: bool,
    conflict_assessment_before_read: ConflictAssessmentV231 | None,
    conflict_resolution_outcome_class: DiscoveryReadOutcomeClassV23 | None,
    report: ProvisionalIncidentReportV231 | None,
    provider_error_code: str | None,
    provider_cost: ProviderCostV23,
) -> EvaluationArmRunV231:
    admitted = context.admission.admitted_diagnosis
    final = (
        "PROVIDER_FAILED"
        if provider_error_code is not None
        else state.novelty_decision.disposition.value
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v231.evaluation-arm-run.v1",
        "case_id": context.case.case_id,
        "policy": EvaluationPolicyV231.V231_CONFLICT_AWARE_GATE,
        "case_bytes_sha256": context.case.source_bytes_sha256,
        "active_view_sha256": context.view.view_sha256,
        "bootstrap_memory_sha256": context.bootstrap_memory_sha256,
        "common_memory_sha256": context.memory.memory_sha256,
        "common_read_count": context.common_read_count,
        "discovery_read_count": discovery_reads,
        "conflict_resolution_read_used": conflict_resolution_read_used,
        "conflict_assessment_before_read": conflict_assessment_before_read,
        "conflict_resolution_outcome_class": conflict_resolution_outcome_class,
        "final_disposition": final,
        "known_admission_sha256": context.admission.state_sha256,
        "admitted_diagnosis": admitted,
        "known_mechanism": None if admitted is None else admitted.mechanism,
        "known_root_service": None if admitted is None else admitted.root_service,
        "no_incident_admissible": context.admission.no_incident_admissible,
        "residual_graph": state.residual_graph,
        "conflict_assessment": state.conflict_assessment,
        "novelty_decision": state.novelty_decision,
        "memory_evidence_refs": tuple(
            sorted(item.evidence_ref for item in memory.evidence_refs)
        ),
        "negative_coverage": negative,
        "provisional_report": report,
        "provider_error_code": provider_error_code,
        "provider_cost": provider_cost,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority_violations": 0,
    }
    draft = EvaluationArmRunV231.model_construct(**payload, run_sha256="0" * 64)
    return EvaluationArmRunV231.model_validate(
        {
            **payload,
            "run_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"run_sha256"})
            ),
        }
    )


def run_conflict_aware_arm_v231(
    context: _CommonContextV23,
    *,
    provider_transport: Callable[[str], str] | None,
) -> EvaluationArmRunV231:
    outcomes = context.outcomes
    memory = context.memory
    negative = NegativeCoverageLedgerV23.empty()
    backend = QuerySpecificReplayBackendV22(context.case.capture)
    discovery_reads = 0
    remaining_budget = 3.0
    conflict_read_used = False
    conflict_assessment_before_read = None
    conflict_resolution_outcome_class = None
    state: ConflictAwareDiscoveryStateV231 | None = None
    while True:
        graph = _residual_graph_v231(context=context, memory=memory)
        executed_action_ids = tuple(
            sorted({item.action_id for item in outcomes})
        )
        failures = tuple(
            sorted(
                {
                    item.source
                    for item in negative.entries
                    if item.outcome_class
                    is DiscoveryReadOutcomeClassV23.SOURCE_FAILURE
                },
                key=lambda item: item.value,
            )
        )
        state = build_conflict_aware_state_v231(
            graph=graph,
            catalog=context.catalog,
            topology_edges=context.case.topology_edges,
            no_incident_admissible=context.admission.no_incident_admissible,
            negative_coverage=negative,
            discovery_reads_used=discovery_reads,
            remaining_weighted_budget=remaining_budget,
            conflict_resolution_read_used=conflict_read_used,
            normal_resource_services=_normal_resource_services_v231(
                graph=graph,
                memory=memory,
            ),
            required_source_failures=failures,
            excluded_action_ids=executed_action_ids,
        )
        selected = None
        is_conflict_read = False
        if (
            state.novelty_decision.disposition
            is NoveltyDispositionV231.DISCOVERY_READ_REQUIRED
        ):
            assert state.discriminating_plan is not None
            selected = state.discriminating_plan.selected_action
            is_conflict_read = True
        elif (
            (
                state.novelty_decision.disposition
                is NoveltyDispositionV231.INSUFFICIENT_EVIDENCE
                or (
                    discovery_reads == 0
                    and state.novelty_decision.disposition
                    is NoveltyDispositionV231.UNREGISTERED_INCIDENT_SUSPECTED
                )
            )
            and discovery_reads < 3
        ):
            coverage_plan = build_discovery_plan_v23(
                catalog=context.catalog,
                graph=graph,
                negative_coverage=negative,
                reads_used=discovery_reads,
                remaining_weighted_budget=remaining_budget,
                target_complete_resource_coverage=True,
                excluded_action_ids=executed_action_ids,
            )
            if coverage_plan is not None:
                selected = coverage_plan.selected_action
        if selected is None:
            break
        if is_conflict_read:
            conflict_assessment_before_read = state.conflict_assessment
        action = resolve_discovery_action_v23(
            option=selected,
            catalog=context.catalog,
            target_complete_resource_coverage=True,
        )
        before_ids = {item.anomaly_id for item in graph.generic_anomalies}
        if isinstance(action, ContrastiveResourceActionV225):
            outcome = _build_read_outcome_v23(
                action=action,
                capture=context.case.capture,
            )
        elif isinstance(action, EvidenceActionV22):
            outcome = backend.execute(action)
        else:
            raise TypeError("v2.3.1 evaluation discovery action is unsupported")
        outcomes = (*outcomes, outcome)
        memory, _ = build_memory_views_v22(
            outcomes=outcomes,
            baseline=_baseline(context.case),
            observed_at=context.case.capture.captured_at,
            top_k=64,
        )
        after = extract_generic_anomalies_v23(
            memory=memory,
            candidate_services=context.case.candidate_services,
        )
        outcome_class, new_ids = _classify_discovery_outcome(
            outcome=outcome,
            before_anomaly_ids=before_ids,
            after_anomaly_ids={item.anomaly_id for item in after},
        )
        if is_conflict_read:
            conflict_resolution_outcome_class = outcome_class
        negative = record_discovery_outcome_v23(
            ledger=negative,
            action=selected,
            outcome_class=outcome_class,
            new_anomaly_ids=new_ids,
        )
        discovery_reads += 1
        remaining_budget = max(0.0, remaining_budget - selected.weighted_cost)
        conflict_read_used = conflict_read_used or is_conflict_read
    assert state is not None
    report = state.provisional_report
    provider_error = None
    cost = _provider_cost_zero_v231()
    if report is not None and provider_transport is not None:
        hypotheses = state.competing_hypothesis_set
        if hypotheses is not None:
            request = build_discovery_provider_request_v231(
                active_ontology=context.view,
                graph=state.residual_graph,
                assessment=state.conflict_assessment,
                hypothesis_set=hypotheses,
                top_shadow_matches=(),
            )
            before_input = int(getattr(provider_transport, "input_tokens", 0))
            before_output = int(getattr(provider_transport, "output_tokens", 0))
            before_total = int(getattr(provider_transport, "total_tokens", 0))
            before_latency = float(getattr(provider_transport, "latency_ms", 0.0))
            before_calls = int(getattr(provider_transport, "provider_calls", 0))
            before_repairs = int(getattr(provider_transport, "protocol_repairs", 0))
            before_retries = int(getattr(provider_transport, "transport_retries", 0))
            outcome_calls = 0
            outcome_repairs = 0
            outcome_retries = 0
            try:
                provider_outcome = call_discovery_provider_v231(
                    request=request,
                    transport=provider_transport,
                )
            except DiscoveryProviderProtocolFailureV23:
                provider_error = "PROTOCOL_FAILED"
                report = None
            except DiscoveryProviderTransportErrorV23 as exc:
                provider_error = f"TRANSPORT_FAILED:{exc.safe_code}"
                report = None
            else:
                report = provider_outcome.report
                outcome_calls = provider_outcome.provider_calls
                outcome_repairs = provider_outcome.protocol_repairs
                outcome_retries = provider_outcome.transport_retries
            cost = ProviderCostV23(
                provider_calls=max(
                    outcome_calls,
                    int(getattr(provider_transport, "provider_calls", before_calls))
                    - before_calls,
                ),
                protocol_repairs=max(
                    outcome_repairs,
                    int(getattr(provider_transport, "protocol_repairs", before_repairs))
                    - before_repairs,
                ),
                transport_retries=max(
                    outcome_retries,
                    int(getattr(provider_transport, "transport_retries", before_retries))
                    - before_retries,
                ),
                input_tokens=int(getattr(provider_transport, "input_tokens", 0))
                - before_input,
                output_tokens=int(getattr(provider_transport, "output_tokens", 0))
                - before_output,
                total_tokens=int(getattr(provider_transport, "total_tokens", 0))
                - before_total,
                latency_ms=float(getattr(provider_transport, "latency_ms", 0.0))
                - before_latency,
            )
    return _build_treatment_run_v231(
        context=context,
        state=state,
        negative=negative,
        memory=memory,
        discovery_reads=discovery_reads,
        conflict_resolution_read_used=conflict_read_used,
        conflict_assessment_before_read=conflict_assessment_before_read,
        conflict_resolution_outcome_class=conflict_resolution_outcome_class,
        report=report,
        provider_error_code=provider_error,
        provider_cost=cost,
    )


def run_evaluation_case_pair_v231(
    *,
    repository_root: Path,
    spec: EvaluationCaseSpecV231,
    view_spec: EvaluationOntologyViewSpecV231,
    truth_store: LazyTruthStoreV231,
    provider_transport: Callable[[str], str] | None,
) -> EvaluationCasePairV231:
    if view_spec.case_id != spec.case_id:
        raise ValueError("v2.3.1 evaluation case and ontology view differ")
    case = materialize_evaluation_case_v231(
        repository_root=repository_root,
        spec=spec,
    )
    context = _build_common_context_v23(
        case=case,
        hidden_mechanism=view_spec.hidden_mechanism,
    )
    strict = _run_strict_arm_v231(
        context=context,
        provider_transport=provider_transport,
    )
    treatment = run_conflict_aware_arm_v231(
        context,
        provider_transport=provider_transport,
    )
    truth = truth_store.load_case_after_both_arms(
        case_id=spec.case_id,
        strict=strict,
        treatment=treatment,
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v231.evaluation-case-pair.v1",
        "case_id": spec.case_id,
        "baseline_policy": EvaluationPolicyV231.V23_STRICT_CONFLICT_GATE,
        "treatment_policy": EvaluationPolicyV231.V231_CONFLICT_AWARE_GATE,
        "strict": strict,
        "treatment": treatment,
        "evaluator_truth": truth,
    }
    draft = EvaluationCasePairV231.model_construct(
        **payload,
        pair_sha256="0" * 64,
    )
    return EvaluationCasePairV231.model_validate(
        {
            **payload,
            "pair_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"pair_sha256"})
            ),
        }
    )


def run_evaluation_policy_v231(
    *,
    repository_root: Path,
    spec: EvaluationCaseSpecV231,
    view_spec: EvaluationOntologyViewSpecV231,
    policy: EvaluationPolicyV231,
    provider_transport: Callable[[str], str] | None,
) -> EvaluationArmRunV23 | EvaluationArmRunV231:
    """Run one policy without loading evaluator truth or executing the peer arm."""

    if view_spec.case_id != spec.case_id:
        raise ValueError("v2.3.1 policy case and ontology view differ")
    case = materialize_evaluation_case_v231(
        repository_root=repository_root,
        spec=spec,
    )
    context = _build_common_context_v23(
        case=case,
        hidden_mechanism=view_spec.hidden_mechanism,
    )
    if policy is EvaluationPolicyV231.V23_STRICT_CONFLICT_GATE:
        return _run_strict_arm_v231(
            context=context,
            provider_transport=provider_transport,
        )
    return run_conflict_aware_arm_v231(
        context,
        provider_transport=provider_transport,
    )


def _provider_snapshot_v231(
    transport: Callable[[str], str] | None,
) -> tuple[int, int, int, int, int, int, float]:
    return (
        int(getattr(transport, "provider_calls", 0)),
        int(getattr(transport, "protocol_repairs", 0)),
        int(getattr(transport, "transport_retries", 0)),
        int(getattr(transport, "input_tokens", 0)),
        int(getattr(transport, "output_tokens", 0)),
        int(getattr(transport, "total_tokens", 0)),
        float(getattr(transport, "latency_ms", 0.0)),
    )


def _run_strict_arm_v231(
    *,
    context: _CommonContextV23,
    provider_transport: Callable[[str], str] | None,
) -> EvaluationArmRunV23:
    """Run the exact v2.3 arm and reconcile only v2.3.1 study telemetry."""

    before = _provider_snapshot_v231(provider_transport)
    run = run_open_world_arm_v23(
        context,
        provider_transport=provider_transport,
    )
    after = _provider_snapshot_v231(provider_transport)
    observed = ProviderCostV23(
        provider_calls=after[0] - before[0],
        protocol_repairs=after[1] - before[1],
        transport_retries=after[2] - before[2],
        input_tokens=after[3] - before[3],
        output_tokens=after[4] - before[4],
        total_tokens=after[5] - before[5],
        latency_ms=after[6] - before[6],
    )
    reconciled = ProviderCostV23(
        provider_calls=max(run.provider_cost.provider_calls, observed.provider_calls),
        protocol_repairs=max(
            run.provider_cost.protocol_repairs,
            observed.protocol_repairs,
        ),
        transport_retries=max(
            run.provider_cost.transport_retries,
            observed.transport_retries,
        ),
        input_tokens=max(run.provider_cost.input_tokens, observed.input_tokens),
        output_tokens=max(run.provider_cost.output_tokens, observed.output_tokens),
        total_tokens=max(run.provider_cost.total_tokens, observed.total_tokens),
        latency_ms=max(run.provider_cost.latency_ms, observed.latency_ms),
    )
    if reconciled == run.provider_cost:
        return run
    payload = {
        field: getattr(run, field)
        for field in type(run).model_fields
        if field != "run_sha256"
    }
    payload["provider_cost"] = reconciled
    draft = EvaluationArmRunV23.model_construct(**payload, run_sha256="0" * 64)
    return EvaluationArmRunV23.model_validate(
        {
            **payload,
            "run_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"run_sha256"})
            ),
        }
    )


class EvaluationMetricsV231(DtaModelV22):
    schema_version: Literal["dta-v231.evaluation-metrics.v1"]
    baseline_novelty_recall: StrictFloat = Field(ge=0.0, le=1.0)
    treatment_novelty_recall: StrictFloat = Field(ge=0.0, le=1.0)
    novelty_recall_improvement: StrictFloat = Field(ge=-1.0, le=1.0)
    conflict_prone_baseline_recall: StrictFloat = Field(ge=0.0, le=1.0)
    conflict_prone_treatment_recall: StrictFloat = Field(ge=0.0, le=1.0)
    non_conflict_baseline_recall: StrictFloat = Field(ge=0.0, le=1.0)
    non_conflict_treatment_recall: StrictFloat = Field(ge=0.0, le=1.0)
    baseline_hard_conflict_rate_on_novelty: StrictFloat = Field(ge=0.0, le=1.0)
    treatment_hard_conflict_rate_on_novelty: StrictFloat = Field(ge=0.0, le=1.0)
    treatment_competing_report_rate: StrictFloat = Field(ge=0.0, le=1.0)
    treatment_root_localization: StrictFloat = Field(ge=0.0, le=1.0)
    treatment_broad_domain_accuracy: StrictFloat = Field(ge=0.0, le=1.0)
    treatment_evidence_ref_validity: StrictFloat = Field(ge=0.0, le=1.0)
    treatment_residual_anomaly_citation_validity: StrictFloat = Field(
        ge=0.0, le=1.0
    )
    competing_hypothesis_evidence_validity: StrictFloat = Field(ge=0.0, le=1.0)
    leading_hypothesis_root_validity: StrictFloat = Field(ge=0.0, le=1.0)
    alternative_hypothesis_completeness: StrictFloat = Field(ge=0.0, le=1.0)
    unresolved_question_completeness: StrictFloat = Field(ge=0.0, le=1.0)
    treatment_false_novel_rate: StrictFloat = Field(ge=0.0, le=1.0)
    registered_known_baseline_accuracy: StrictFloat = Field(ge=0.0, le=1.0)
    registered_known_treatment_accuracy: StrictFloat = Field(ge=0.0, le=1.0)
    no_incident_baseline_accuracy: StrictFloat = Field(ge=0.0, le=1.0)
    no_incident_treatment_accuracy: StrictFloat = Field(ge=0.0, le=1.0)
    insufficient_conflict_baseline_accuracy: StrictFloat = Field(ge=0.0, le=1.0)
    insufficient_conflict_treatment_accuracy: StrictFloat = Field(ge=0.0, le=1.0)
    known_accuracy_drop_cases: StrictInt = Field(ge=0)
    no_incident_accuracy_drop_cases: StrictInt = Field(ge=0)
    true_conflict_converted_cases: StrictInt = Field(ge=0, le=3)
    competing_report_count: StrictInt = Field(ge=0, le=24)
    no_conflict_count: StrictInt = Field(ge=0, le=24)
    coherent_competition_count: StrictInt = Field(ge=0, le=24)
    resolvable_conflict_count: StrictInt = Field(ge=0, le=24)
    irreconcilable_conflict_count: StrictInt = Field(ge=0, le=24)
    conflict_resolution_read_count: StrictInt = Field(ge=0, le=24)
    discriminating_read_execution_rate: StrictFloat = Field(ge=0.0, le=1.0)
    discriminating_read_anomaly_yield: StrictFloat = Field(ge=0.0, le=1.0)
    post_read_conflict_resolution_rate: StrictFloat = Field(ge=0.0, le=1.0)
    persistent_competition_report_rate: StrictFloat = Field(ge=0.0, le=1.0)
    mean_baseline_discovery_reads: StrictFloat = Field(ge=0.0, le=3.0)
    mean_treatment_discovery_reads: StrictFloat = Field(ge=0.0, le=3.0)
    baseline_provider_calls: StrictInt = Field(ge=0)
    treatment_provider_calls: StrictInt = Field(ge=0)
    protocol_repairs: StrictInt = Field(ge=0)
    transport_retries: StrictInt = Field(ge=0)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    provider_latency_ms: StrictFloat = Field(ge=0.0)
    action_authority_violations: StrictInt = Field(ge=0)


def _ratio_v231(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def _strict_novel_v231(run: EvaluationArmRunV23) -> bool:
    return run.provisional_report is not None and run.final_disposition in {
        "UNREGISTERED_INCIDENT_SUSPECTED",
        "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY",
    }


def _treatment_novel_v231(run: EvaluationArmRunV231) -> bool:
    return run.provisional_report is not None and run.final_disposition in {
        NoveltyDispositionV231.UNREGISTERED_INCIDENT_SUSPECTED.value,
        NoveltyDispositionV231.UNREGISTERED_INCIDENT_WITH_COMPETING_HYPOTHESES.value,
        NoveltyDispositionV231.KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY.value,
    }


def _leading_report_projection_v231(
    report: ProvisionalIncidentReportV231,
) -> tuple[tuple[str, ...], str]:
    if report.preferred_hypothesis_id is None:
        return report.suspected_root_services, report.broad_fault_domain.value
    leading = next(
        item
        for item in report.competing_hypotheses
        if item.hypothesis_id == report.preferred_hypothesis_id
    )
    return leading.suspected_root_services, leading.broad_fault_domain.value


def score_evaluation_pairs_v231(
    pairs: tuple[EvaluationCasePairV231, ...],
) -> EvaluationMetricsV231:
    if len(pairs) != 24:
        raise ValueError("v2.3.1 evaluation scoring requires exactly 24 pairs")
    novelty = tuple(
        item
        for item in pairs
        if item.evaluator_truth.category
        in {
            EvaluationCategoryV231.NOVEL_HIDDEN,
            EvaluationCategoryV231.NOVEL_UNREGISTERED,
        }
    )
    controls = tuple(item for item in pairs if item not in novelty)
    conflict_prone = tuple(
        item for item in novelty if item.evaluator_truth.conflict_prone_novelty
    )
    non_conflict = tuple(
        item for item in novelty if not item.evaluator_truth.conflict_prone_novelty
    )
    known = tuple(
        item
        for item in pairs
        if item.evaluator_truth.category is EvaluationCategoryV231.REGISTERED_KNOWN
    )
    no_incident = tuple(
        item
        for item in pairs
        if item.evaluator_truth.category is EvaluationCategoryV231.NO_INCIDENT
    )
    true_conflicts = tuple(
        item for item in pairs if item.evaluator_truth.true_irreconcilable_conflict
    )
    insufficient = tuple(
        item
        for item in pairs
        if item.evaluator_truth.category
        is EvaluationCategoryV231.INSUFFICIENT_CONFLICT
    )
    strict_detected = tuple(item for item in novelty if _strict_novel_v231(item.strict))
    treatment_detected = tuple(
        item for item in novelty if _treatment_novel_v231(item.treatment)
    )
    root_correct = 0
    domain_correct = 0
    cited_count = 0
    valid_cited_count = 0
    residual_citation_valid = 0
    competing_evidence_valid = 0
    alternative_complete = 0
    unresolved_complete = 0
    competing_report_total = 0
    for item in treatment_detected:
        report = item.treatment.provisional_report
        assert report is not None
        leading_roots, leading_domain = _leading_report_projection_v231(report)
        root_correct += int(
            item.evaluator_truth.expected_root_service in set(leading_roots)
        )
        domain_correct += int(
            leading_domain == item.evaluator_truth.expected_broad_domain
        )
        cited = {
            *report.supporting_evidence_refs,
            *report.contradicting_evidence_refs,
        }
        cited_count += len(cited)
        valid_cited_count += len(cited.intersection(item.treatment.memory_evidence_refs))
        residual_ids = set(item.treatment.residual_graph.residual_anomaly_ids)
        residual_refs = {
            ref
            for anomaly in item.treatment.residual_graph.generic_anomalies
            if anomaly.anomaly_id in residual_ids
            for ref in anomaly.evidence_refs
        }
        residual_citation_valid += int(
            set(report.unexplained_anomaly_ids).issubset(residual_ids)
            and bool(set(report.supporting_evidence_refs).intersection(residual_refs))
        )
        if report.uncertainty_mode is ReportUncertaintyModeV231.COMPETING_HYPOTHESES:
            competing_report_total += 1
            competing_evidence_valid += int(
                len(report.competing_hypotheses) >= 2
                and all(
                    set(hypothesis.supporting_anomaly_ids).issubset(residual_ids)
                    and bool(hypothesis.supporting_evidence_refs)
                    and set(hypothesis.supporting_evidence_refs).issubset(residual_refs)
                    for hypothesis in report.competing_hypotheses
                )
            )
            alternative_complete += int(
                len(report.competing_hypotheses) >= 2
                and len(report.alternative_hypotheses) >= 2
            )
            unresolved_complete += int(bool(report.unresolved_questions))
    strict_known = sum(
        item.strict.final_disposition == "KNOWN_INCIDENT"
        and item.strict.known_mechanism is item.evaluator_truth.expected_mechanism
        and item.strict.known_root_service == item.evaluator_truth.expected_root_service
        for item in known
    )
    treatment_known = sum(
        item.treatment.final_disposition == "KNOWN_INCIDENT"
        and item.treatment.known_mechanism is item.evaluator_truth.expected_mechanism
        and item.treatment.known_root_service
        == item.evaluator_truth.expected_root_service
        for item in known
    )
    strict_no = sum(
        item.strict.final_disposition == "NO_INCIDENT" for item in no_incident
    )
    treatment_no = sum(
        item.treatment.final_disposition == "NO_INCIDENT" for item in no_incident
    )
    strict_insufficient = sum(
        item.strict.final_disposition
        in {"INSUFFICIENT_EVIDENCE", "CONFLICTING_EVIDENCE"}
        for item in insufficient
    )
    treatment_insufficient = sum(
        item.treatment.final_disposition
        in {"INSUFFICIENT_EVIDENCE", "CONFLICTING_EVIDENCE"}
        for item in insufficient
    )
    strict_costs = tuple(item.strict.provider_cost for item in pairs)
    treatment_costs = tuple(item.treatment.provider_cost for item in pairs)
    all_costs = (*strict_costs, *treatment_costs)
    baseline_recall = _ratio_v231(len(strict_detected), len(novelty))
    treatment_recall = _ratio_v231(len(treatment_detected), len(novelty))
    conflict_counts = Counter(
        item.treatment.conflict_assessment.conflict_type for item in pairs
    )
    conflict_reads = tuple(
        item for item in pairs if item.treatment.conflict_resolution_read_used
    )
    conflict_reads_on_novelty = sum(
        item.treatment.conflict_resolution_read_used for item in novelty
    )
    post_read_resolved = sum(
        item.treatment.conflict_assessment.conflict_type
        in {ConflictTypeV231.NO_CONFLICT, ConflictTypeV231.COHERENT_COMPETITION}
        for item in conflict_reads
    )
    persistent_competition = sum(
        item.treatment.conflict_assessment.conflict_type
        is ConflictTypeV231.COHERENT_COMPETITION
        and item.treatment.provisional_report is not None
        and item.treatment.provisional_report.uncertainty_mode
        is ReportUncertaintyModeV231.COMPETING_HYPOTHESES
        for item in conflict_reads
    )
    payload = EvaluationMetricsV231(
        schema_version="dta-v231.evaluation-metrics.v1",
        baseline_novelty_recall=baseline_recall,
        treatment_novelty_recall=treatment_recall,
        novelty_recall_improvement=treatment_recall - baseline_recall,
        conflict_prone_baseline_recall=_ratio_v231(
            sum(_strict_novel_v231(item.strict) for item in conflict_prone),
            len(conflict_prone),
        ),
        conflict_prone_treatment_recall=_ratio_v231(
            sum(_treatment_novel_v231(item.treatment) for item in conflict_prone),
            len(conflict_prone),
        ),
        non_conflict_baseline_recall=_ratio_v231(
            sum(_strict_novel_v231(item.strict) for item in non_conflict),
            len(non_conflict),
        ),
        non_conflict_treatment_recall=_ratio_v231(
            sum(_treatment_novel_v231(item.treatment) for item in non_conflict),
            len(non_conflict),
        ),
        baseline_hard_conflict_rate_on_novelty=_ratio_v231(
            sum(item.strict.final_disposition == "CONFLICTING_EVIDENCE" for item in novelty),
            len(novelty),
        ),
        treatment_hard_conflict_rate_on_novelty=_ratio_v231(
            sum(
                item.treatment.final_disposition == "CONFLICTING_EVIDENCE"
                for item in novelty
            ),
            len(novelty),
        ),
        treatment_competing_report_rate=_ratio_v231(
            competing_report_total,
            len(novelty),
        ),
        treatment_root_localization=_ratio_v231(root_correct, len(novelty)),
        treatment_broad_domain_accuracy=_ratio_v231(domain_correct, len(novelty)),
        treatment_evidence_ref_validity=_ratio_v231(
            valid_cited_count,
            cited_count,
        ),
        treatment_residual_anomaly_citation_validity=_ratio_v231(
            residual_citation_valid,
            len(treatment_detected),
        ),
        competing_hypothesis_evidence_validity=_ratio_v231(
            competing_evidence_valid,
            competing_report_total,
        ),
        leading_hypothesis_root_validity=_ratio_v231(
            root_correct,
            len(treatment_detected),
        ),
        alternative_hypothesis_completeness=_ratio_v231(
            alternative_complete,
            competing_report_total,
        ),
        unresolved_question_completeness=_ratio_v231(
            unresolved_complete,
            competing_report_total,
        ),
        treatment_false_novel_rate=_ratio_v231(
            sum(_treatment_novel_v231(item.treatment) for item in controls),
            len(controls),
        ),
        registered_known_baseline_accuracy=_ratio_v231(strict_known, len(known)),
        registered_known_treatment_accuracy=_ratio_v231(treatment_known, len(known)),
        no_incident_baseline_accuracy=_ratio_v231(strict_no, len(no_incident)),
        no_incident_treatment_accuracy=_ratio_v231(treatment_no, len(no_incident)),
        insufficient_conflict_baseline_accuracy=_ratio_v231(
            strict_insufficient,
            len(insufficient),
        ),
        insufficient_conflict_treatment_accuracy=_ratio_v231(
            treatment_insufficient,
            len(insufficient),
        ),
        known_accuracy_drop_cases=max(0, strict_known - treatment_known),
        no_incident_accuracy_drop_cases=max(0, strict_no - treatment_no),
        true_conflict_converted_cases=sum(
            _treatment_novel_v231(item.treatment) for item in true_conflicts
        ),
        competing_report_count=competing_report_total,
        no_conflict_count=conflict_counts[ConflictTypeV231.NO_CONFLICT],
        coherent_competition_count=conflict_counts[
            ConflictTypeV231.COHERENT_COMPETITION
        ],
        resolvable_conflict_count=conflict_counts[ConflictTypeV231.RESOLVABLE_CONFLICT],
        irreconcilable_conflict_count=conflict_counts[
            ConflictTypeV231.IRRECONCILABLE_CONFLICT
        ],
        conflict_resolution_read_count=len(conflict_reads),
        discriminating_read_execution_rate=_ratio_v231(
            conflict_reads_on_novelty,
            len(novelty),
        ),
        discriminating_read_anomaly_yield=_ratio_v231(
            sum(
                item.treatment.conflict_resolution_outcome_class
                is DiscoveryReadOutcomeClassV23.ANOMALY_YIELD
                for item in conflict_reads
            ),
            len(conflict_reads),
        ),
        post_read_conflict_resolution_rate=_ratio_v231(
            post_read_resolved,
            len(conflict_reads),
        ),
        persistent_competition_report_rate=_ratio_v231(
            persistent_competition,
            len(conflict_reads),
        ),
        mean_baseline_discovery_reads=sum(
            item.strict.discovery_read_count for item in pairs
        )
        / len(pairs),
        mean_treatment_discovery_reads=sum(
            item.treatment.discovery_read_count for item in pairs
        )
        / len(pairs),
        baseline_provider_calls=sum(item.provider_calls for item in strict_costs),
        treatment_provider_calls=sum(item.provider_calls for item in treatment_costs),
        protocol_repairs=sum(item.protocol_repairs for item in all_costs),
        transport_retries=sum(item.transport_retries for item in all_costs),
        input_tokens=sum(item.input_tokens for item in all_costs),
        output_tokens=sum(item.output_tokens for item in all_costs),
        total_tokens=sum(item.total_tokens for item in all_costs),
        provider_latency_ms=sum(item.latency_ms for item in all_costs),
        action_authority_violations=sum(
            item.strict.action_authority_violations
            + item.treatment.action_authority_violations
            for item in pairs
        ),
    )
    return payload


class ManifestFileBindingV231(DtaModelV22):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationManifestV231(DtaModelV22):
    schema_version: Literal["dta-v231.evaluation-manifest.v1"]
    base_commit: Literal["7fe2bff7186cca1cedd2513f7984709057fc19e5"]
    branch: Literal["codex/dta-v231-conflict-aware-discovery"]
    provider_model: str
    planned_case_count: Literal[24]
    planned_run_count: Literal[48]
    planned_execution_count: Literal[1]
    cases: ManifestFileBindingV231
    truth: ManifestFileBindingV231
    ontology_views: ManifestFileBindingV231
    dataset_builder: ManifestFileBindingV231
    runtime_sources: tuple[ManifestFileBindingV231, ...] = Field(min_length=10)
    strict_system_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    treatment_system_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_json: str
    output_markdown: str
    fixed_at_utc: datetime

    @model_validator(mode="after")
    def require_manifest(self) -> "EvaluationManifestV231":
        if (
            self.fixed_at_utc.tzinfo is None
            or self.fixed_at_utc.utcoffset() != timedelta(0)
        ):
            raise ValueError("v2.3.1 manifest timestamp is not UTC")
        for values, label in ((self.runtime_sources, "runtime sources"),):
            paths = tuple(item.path for item in values)
            if paths != tuple(sorted(set(paths))):
                raise ValueError(f"v2.3.1 manifest {label} are not canonical")
        return self


class EvaluationPreflightV231(DtaModelV22):
    schema_version: Literal["dta-v231.evaluation-preflight.v1"]
    case_count: Literal[24]
    planned_runs: Literal[48]
    execution_count_before: Literal[0]
    provider_model: str
    cases_sha256: str
    truth_sha256: str
    ontology_views_sha256: str
    manifest_sha256: str
    new_case_bytes_sha256: tuple[str, ...] = Field(min_length=24, max_length=24)
    output_path: str
    output_markdown_path: str
    status: Literal["DTA_V231_FIXED_EVALUATION_PREFLIGHT_PASS"]


def load_evaluation_manifest_v231(path: Path) -> EvaluationManifestV231:
    return EvaluationManifestV231.model_validate_json(path.read_bytes())


def _verify_binding_v231(
    *, repository_root: Path, binding: ManifestFileBindingV231
) -> None:
    path = repository_root / binding.path
    if not path.is_file() or file_sha256_v231(path) != binding.sha256:
        raise ValueError(f"v2.3.1 frozen binding differs: {binding.path}")


def build_evaluation_preflight_v231(
    *,
    repository_root: Path,
    cases_path: Path,
    truth_path: Path,
    ontology_views_path: Path,
    manifest_path: Path,
    output_path: Path,
    output_markdown_path: Path,
    expected_provider_model: str,
) -> EvaluationPreflightV231:
    manifest = load_evaluation_manifest_v231(manifest_path)
    if manifest.provider_model != expected_provider_model:
        raise ValueError("v2.3.1 Provider model differs from the frozen manifest")
    expected_paths = {
        cases_path.resolve(): manifest.cases,
        truth_path.resolve(): manifest.truth,
        ontology_views_path.resolve(): manifest.ontology_views,
    }
    for path, binding in expected_paths.items():
        if (repository_root / binding.path).resolve() != path:
            raise ValueError("v2.3.1 fixed input path differs from manifest")
        _verify_binding_v231(repository_root=repository_root, binding=binding)
    for binding in (manifest.dataset_builder, *manifest.runtime_sources):
        _verify_binding_v231(repository_root=repository_root, binding=binding)
    from ecomsre.dta_v2.v23.discovery_provider import DISCOVERY_SYSTEM_PROMPT_V23

    if manifest.strict_system_prompt_sha256 != hashlib.sha256(
        DISCOVERY_SYSTEM_PROMPT_V23.encode("utf-8")
    ).hexdigest():
        raise ValueError("v2.3.1 strict prompt binding differs")
    if manifest.treatment_system_prompt_sha256 != hashlib.sha256(
        DISCOVERY_SYSTEM_PROMPT_V231.encode("utf-8")
    ).hexdigest():
        raise ValueError("v2.3.1 treatment prompt binding differs")
    if (repository_root / manifest.output_json).resolve() != output_path.resolve():
        raise ValueError("v2.3.1 fixed output path differs from manifest")
    if (
        repository_root / manifest.output_markdown
    ).resolve() != output_markdown_path.resolve():
        raise ValueError("v2.3.1 fixed markdown output path differs from manifest")
    cases = load_evaluation_case_set_v231(cases_path)
    views = load_evaluation_views_v231(ontology_views_path)
    case_ids = tuple(item.case_id for item in cases.cases)
    if case_ids != tuple(item.case_id for item in views.views):
        raise ValueError("v2.3.1 fixed case and view IDs differ")
    new_hashes = tuple(
        materialize_evaluation_case_v231(
            repository_root=repository_root,
            spec=spec,
        ).source_bytes_sha256
        for spec in cases.cases
    )
    if len(set(new_hashes)) != 24:
        raise ValueError("v2.3.1 fixed capture bytes are not unique")
    old_case_set = load_evaluation_case_set_v23(
        repository_root / "config/dta-v23/evaluation/cases.json"
    )
    old_hashes = {
        materialize_evaluation_case_v23(repository_root=repository_root, spec=spec)
        .source_bytes_sha256
        for spec in old_case_set.cases
    }
    if not old_hashes.isdisjoint(new_hashes):
        raise ValueError("v2.3.1 fixed set reuses v2.3 case bytes")
    return EvaluationPreflightV231(
        schema_version="dta-v231.evaluation-preflight.v1",
        case_count=24,
        planned_runs=48,
        execution_count_before=0,
        provider_model=expected_provider_model,
        cases_sha256=file_sha256_v231(cases_path),
        truth_sha256=file_sha256_v231(truth_path),
        ontology_views_sha256=file_sha256_v231(ontology_views_path),
        manifest_sha256=file_sha256_v231(manifest_path),
        new_case_bytes_sha256=new_hashes,
        output_path=str(output_path.relative_to(repository_root)),
        output_markdown_path=str(output_markdown_path.relative_to(repository_root)),
        status="DTA_V231_FIXED_EVALUATION_PREFLIGHT_PASS",
    )


class OpenAICompatibleDiscoveryTransportV231(OpenAICompatibleDiscoveryTransportV23):
    """Use one Provider configuration with arm-specific response schemas."""

    _v231_mode: bool = False

    def __init__(
        self,
        *,
        config: OpenAICompatibleConfig,
        minimum_request_interval_seconds: float = 6.0,
        timeout_seconds: float = 120.0,
    ) -> None:
        super().__init__(
            config=config,
            minimum_request_interval_seconds=minimum_request_interval_seconds,
            timeout_seconds=timeout_seconds,
        )
        self.provider_calls = 0
        self.protocol_repairs = 0
        self.transport_retries = 0
        self._seen_request_bodies: set[str] = set()

    @staticmethod
    def _tool() -> dict[str, object]:
        if not OpenAICompatibleDiscoveryTransportV231._v231_mode:
            return OpenAICompatibleDiscoveryTransportV23._tool()
        schema = ProvisionalIncidentReportV231.model_json_schema()
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise ValueError("v2.3.1 report JSON schema is invalid")
        for generated in ("schema_version", "report_id", "report_sha256"):
            properties.pop(generated, None)
        properties["confidence"] = {
            "type": "number",
            "minimum": 0,
            "maximum": 0.65,
        }
        properties["confidence_band"] = {
            "type": "string",
            "enum": ["LOW", "MEDIUM"],
        }
        properties["review_recommendation"] = {
            "type": "string",
            "enum": ["REQUEST_MORE_EVIDENCE", "SAVE_AS_INCIDENT_ONLY"],
        }
        schema["required"] = [
            item
            for item in required
            if item not in {"schema_version", "report_id", "report_sha256"}
        ]
        schema["additionalProperties"] = False
        return {
            "type": "function",
            "function": {
                "name": "submit_provisional_incident_report",
                "description": (
                    "Submit one evidence-bound non-actionable conflict-aware report."
                ),
                "strict": False,
                "parameters": schema,
            },
        }

    def __call__(self, body: str) -> str:
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise DiscoveryProviderTransportErrorV23(
                "INVALID_LOCAL_REQUEST",
                retryable=False,
            )
        self.provider_calls += 1
        if body in self._seen_request_bodies:
            self.transport_retries += 1
        else:
            self._seen_request_bodies.add(body)
            repair = parsed.get("protocol_repair")
            if isinstance(repair, dict) and repair.get("ordinal") in {1, 2}:
                self.protocol_repairs += 1
        type(self)._v231_mode = parsed.get("system") == DISCOVERY_SYSTEM_PROMPT_V231
        try:
            return super().__call__(body)
        finally:
            type(self)._v231_mode = False


class FixedEvaluationArtifactV231(DtaModelV22):
    schema_version: Literal["dta-v231.fixed-evaluation.v1"]
    execution_count: Literal[1]
    case_count: Literal[24]
    run_count: Literal[48]
    baseline_policy: Literal[EvaluationPolicyV231.V23_STRICT_CONFLICT_GATE]
    treatment_policy: Literal[EvaluationPolicyV231.V231_CONFLICT_AWARE_GATE]
    provider_model: str
    preflight: EvaluationPreflightV231
    pairs: tuple[EvaluationCasePairV231, ...] = Field(min_length=24, max_length=24)
    metrics: EvaluationMetricsV231
    measured_result_terminal: MeasuredResultTerminalV231
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    docker_calls: Literal[0]
    new_live_faults: Literal[0]
    artifact_sha256: str

    @model_validator(mode="after")
    def require_artifact(self) -> "FixedEvaluationArtifactV231":
        ids = tuple(item.case_id for item in self.pairs)
        if ids != tuple(sorted(set(ids))) or len(ids) != 24:
            raise ValueError("v2.3.1 fixed pair denominator differs")
        if self.provider_model != self.preflight.provider_model:
            raise ValueError("v2.3.1 artifact Provider binding differs")
        expected_terminal = score_measured_terminal_v231(
            baseline_novelty_recall=self.metrics.baseline_novelty_recall,
            treatment_novelty_recall=self.metrics.treatment_novelty_recall,
            conflict_prone_novelty_recall=(
                self.metrics.conflict_prone_treatment_recall
            ),
            root_localization=self.metrics.treatment_root_localization,
            broad_domain_accuracy=self.metrics.treatment_broad_domain_accuracy,
            evidence_ref_validity=self.metrics.treatment_evidence_ref_validity,
            false_novel_rate=self.metrics.treatment_false_novel_rate,
            known_accuracy_drop_cases=self.metrics.known_accuracy_drop_cases,
            no_incident_accuracy_drop_cases=(
                self.metrics.no_incident_accuracy_drop_cases
            ),
            true_conflict_converted_cases=(
                self.metrics.true_conflict_converted_cases
            ),
            action_authority_violations=(
                self.metrics.action_authority_violations
            ),
        )
        if self.measured_result_terminal is not expected_terminal:
            raise ValueError("v2.3.1 measured terminal differs from thresholds")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"artifact_sha256"})
        )
        if self.artifact_sha256 != expected:
            raise ValueError("v2.3.1 fixed artifact digest differs")
        return self


def score_measured_terminal_v231(
    *,
    baseline_novelty_recall: float,
    treatment_novelty_recall: float,
    conflict_prone_novelty_recall: float,
    root_localization: float,
    broad_domain_accuracy: float,
    evidence_ref_validity: float,
    false_novel_rate: float,
    known_accuracy_drop_cases: int,
    no_incident_accuracy_drop_cases: int,
    true_conflict_converted_cases: int,
    action_authority_violations: int,
) -> MeasuredResultTerminalV231:
    improvement = treatment_novelty_recall - baseline_novelty_recall
    positive = (
        treatment_novelty_recall >= 0.70
        and improvement >= 0.20
        and conflict_prone_novelty_recall >= 0.625
        and root_localization >= 0.65
        and broad_domain_accuracy >= 0.55
        and evidence_ref_validity >= 0.90
        and false_novel_rate <= 0.20
        and known_accuracy_drop_cases <= 1
        and no_incident_accuracy_drop_cases <= 1
        and true_conflict_converted_cases <= 1
        and action_authority_violations == 0
    )
    if positive:
        return MeasuredResultTerminalV231.EFFECT_OBSERVED
    mixed = (
        improvement >= 0.15
        and conflict_prone_novelty_recall >= 0.50
        and evidence_ref_validity >= 0.85
        and false_novel_rate <= 0.25
        and action_authority_violations == 0
    )
    return (
        MeasuredResultTerminalV231.MIXED_RESULT
        if mixed
        else MeasuredResultTerminalV231.NOT_OBSERVED
    )


def file_sha256_v231(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_fixed_artifact_v231(
    *,
    preflight: EvaluationPreflightV231,
    pairs: tuple[EvaluationCasePairV231, ...],
) -> FixedEvaluationArtifactV231:
    metrics = score_evaluation_pairs_v231(pairs)
    terminal = score_measured_terminal_v231(
        baseline_novelty_recall=metrics.baseline_novelty_recall,
        treatment_novelty_recall=metrics.treatment_novelty_recall,
        conflict_prone_novelty_recall=metrics.conflict_prone_treatment_recall,
        root_localization=metrics.treatment_root_localization,
        broad_domain_accuracy=metrics.treatment_broad_domain_accuracy,
        evidence_ref_validity=metrics.treatment_evidence_ref_validity,
        false_novel_rate=metrics.treatment_false_novel_rate,
        known_accuracy_drop_cases=metrics.known_accuracy_drop_cases,
        no_incident_accuracy_drop_cases=metrics.no_incident_accuracy_drop_cases,
        true_conflict_converted_cases=metrics.true_conflict_converted_cases,
        action_authority_violations=metrics.action_authority_violations,
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v231.fixed-evaluation.v1",
        "execution_count": 1,
        "case_count": 24,
        "run_count": 48,
        "baseline_policy": EvaluationPolicyV231.V23_STRICT_CONFLICT_GATE,
        "treatment_policy": EvaluationPolicyV231.V231_CONFLICT_AWARE_GATE,
        "provider_model": preflight.provider_model,
        "preflight": preflight,
        "pairs": pairs,
        "metrics": metrics,
        "measured_result_terminal": terminal,
        "agent_writes": 0,
        "runbook_executions": 0,
        "docker_calls": 0,
        "new_live_faults": 0,
    }
    draft = FixedEvaluationArtifactV231.model_construct(
        **payload,
        artifact_sha256="0" * 64,
    )
    return FixedEvaluationArtifactV231.model_validate(
        {
            **payload,
            "artifact_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"artifact_sha256"})
            ),
        }
    )


def run_fixed_evaluation_once_v231(
    *,
    repository_root: Path,
    cases_path: Path,
    truth_path: Path,
    ontology_views_path: Path,
    manifest_path: Path,
    output_path: Path,
    output_markdown_path: Path,
    provider_transport: OpenAICompatibleDiscoveryTransportV231,
    observer: Callable[[EvaluationCasePairV231], None] | None = None,
) -> FixedEvaluationArtifactV231:
    preflight = build_evaluation_preflight_v231(
        repository_root=repository_root,
        cases_path=cases_path,
        truth_path=truth_path,
        ontology_views_path=ontology_views_path,
        manifest_path=manifest_path,
        output_path=output_path,
        output_markdown_path=output_markdown_path,
        expected_provider_model=provider_transport.config.model,
    )
    local_root = repository_root / ".local/dta-v231"
    local_root.mkdir(parents=True, exist_ok=True)
    sentinel = local_root / "fixed-evaluation.started.json"
    partial = local_root / "fixed-evaluation.partial.jsonl"
    if (
        sentinel.exists()
        or partial.exists()
        or output_path.exists()
        or output_markdown_path.exists()
    ):
        raise FileExistsError("v2.3.1 fixed evaluation write-once boundary exists")
    sentinel.write_text(
        json.dumps(
            {
                "status": "STARTED",
                "planned_execution_count": 1,
                "cases_sha256": preflight.cases_sha256,
                "truth_sha256": preflight.truth_sha256,
                "ontology_views_sha256": preflight.ontology_views_sha256,
                "manifest_sha256": preflight.manifest_sha256,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    cases = load_evaluation_case_set_v231(cases_path)
    views = load_evaluation_views_v231(ontology_views_path)
    truth_store = LazyTruthStoreV231(truth_path)
    pairs: list[EvaluationCasePairV231] = []
    with partial.open("x", encoding="utf-8") as handle:
        for spec in cases.cases:
            pair = run_evaluation_case_pair_v231(
                repository_root=repository_root,
                spec=spec,
                view_spec=views.require(spec.case_id),
                truth_store=truth_store,
                provider_transport=provider_transport,
            )
            pairs.append(pair)
            handle.write(pair.model_dump_json() + "\n")
            handle.flush()
            if observer is not None:
                observer(pair)
    if truth_store.loaded_case_ids != tuple(item.case_id for item in cases.cases):
        raise ValueError("v2.3.1 evaluator truth coverage differs after both arms")
    artifact = _build_fixed_artifact_v231(
        preflight=preflight,
        pairs=tuple(pairs),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        handle.write(artifact.model_dump_json(indent=2) + "\n")
    with output_markdown_path.open("x", encoding="utf-8") as handle:
        handle.write(render_evaluation_markdown_v231(artifact))
    sentinel.write_text(
        json.dumps(
            {
                "status": "COMPLETE",
                "execution_count": 1,
                "artifact_sha256": artifact.artifact_sha256,
                "output_json_sha256": file_sha256_v231(output_path),
                "output_markdown_sha256": file_sha256_v231(
                    output_markdown_path
                ),
                "measured_result_terminal": artifact.measured_result_terminal.value,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return artifact


def render_evaluation_markdown_v231(
    artifact: FixedEvaluationArtifactV231,
) -> str:
    metrics = artifact.metrics
    return "\n".join(
        (
            "# DTA v2.3.1 Conflict-Aware Discovery — Fixed Evaluation",
            "",
            f"Measured terminal: `{artifact.measured_result_terminal.value}`",
            "",
            f"- Execution count: `{artifact.execution_count}`",
            f"- Cases / runs: `{artifact.case_count}` / `{artifact.run_count}`",
            f"- Baseline / treatment novelty recall: `{metrics.baseline_novelty_recall:.3f}` / `{metrics.treatment_novelty_recall:.3f}`",
            f"- Recall improvement: `{metrics.novelty_recall_improvement:.3f}`",
            f"- Conflict-prone baseline / treatment recall: `{metrics.conflict_prone_baseline_recall:.3f}` / `{metrics.conflict_prone_treatment_recall:.3f}`",
            f"- Non-conflict baseline / treatment recall: `{metrics.non_conflict_baseline_recall:.3f}` / `{metrics.non_conflict_treatment_recall:.3f}`",
            f"- Hard-conflict rate on novelty (baseline / treatment): `{metrics.baseline_hard_conflict_rate_on_novelty:.3f}` / `{metrics.treatment_hard_conflict_rate_on_novelty:.3f}`",
            f"- Treatment competing-report rate: `{metrics.treatment_competing_report_rate:.3f}`",
            f"- Treatment root localization: `{metrics.treatment_root_localization:.3f}`",
            f"- Treatment broad-domain accuracy: `{metrics.treatment_broad_domain_accuracy:.3f}`",
            "",
            "## Conflict behavior",
            "",
            f"- Final conflict counts (none / coherent / resolvable / irreconcilable): `{metrics.no_conflict_count}` / `{metrics.coherent_competition_count}` / `{metrics.resolvable_conflict_count}` / `{metrics.irreconcilable_conflict_count}`",
            f"- Discriminating-read execution rate: `{metrics.discriminating_read_execution_rate:.3f}`",
            f"- Discriminating-read anomaly yield: `{metrics.discriminating_read_anomaly_yield:.3f}`",
            f"- Post-read conflict-resolution rate: `{metrics.post_read_conflict_resolution_rate:.3f}`",
            f"- Persistent-competition report rate: `{metrics.persistent_competition_report_rate:.3f}`",
            "",
            "## Report quality and controls",
            "",
            f"- Treatment evidence-ref validity: `{metrics.treatment_evidence_ref_validity:.3f}`",
            f"- Residual-anomaly citation validity: `{metrics.treatment_residual_anomaly_citation_validity:.3f}`",
            f"- Competing-hypothesis evidence validity: `{metrics.competing_hypothesis_evidence_validity:.3f}`",
            f"- Leading-hypothesis root validity: `{metrics.leading_hypothesis_root_validity:.3f}`",
            f"- Alternative-hypothesis completeness: `{metrics.alternative_hypothesis_completeness:.3f}`",
            f"- Unresolved-question completeness: `{metrics.unresolved_question_completeness:.3f}`",
            f"- Treatment false-novel rate: `{metrics.treatment_false_novel_rate:.3f}`",
            f"- Registered-known accuracy (baseline / treatment): `{metrics.registered_known_baseline_accuracy:.3f}` / `{metrics.registered_known_treatment_accuracy:.3f}`",
            f"- No-Incident accuracy (baseline / treatment): `{metrics.no_incident_baseline_accuracy:.3f}` / `{metrics.no_incident_treatment_accuracy:.3f}`",
            f"- Insufficient/conflict accuracy (baseline / treatment): `{metrics.insufficient_conflict_baseline_accuracy:.3f}` / `{metrics.insufficient_conflict_treatment_accuracy:.3f}`",
            f"- Known / No-Incident accuracy-drop cases: `{metrics.known_accuracy_drop_cases}` / `{metrics.no_incident_accuracy_drop_cases}`",
            f"- True conflicts converted to novelty: `{metrics.true_conflict_converted_cases}`",
            "",
            "## Cost and safety",
            "",
            f"- Mean discovery reads (baseline / treatment): `{metrics.mean_baseline_discovery_reads:.3f}` / `{metrics.mean_treatment_discovery_reads:.3f}`",
            f"- Provider calls (baseline / treatment): `{metrics.baseline_provider_calls}` / `{metrics.treatment_provider_calls}`",
            f"- Provider tokens (input / output / total): `{metrics.input_tokens}` / `{metrics.output_tokens}` / `{metrics.total_tokens}`",
            f"- Provider latency: `{metrics.provider_latency_ms:.3f} ms`",
            f"- Protocol repairs / transport retries: `{metrics.protocol_repairs}` / `{metrics.transport_retries}`",
            f"- Action-authority violations: `{metrics.action_authority_violations}`",
            "",
            "The study used committed replay-derived bytes only. It did not call Docker, create a live fault, execute a Runbook, or grant Agent write authority.",
            "",
        )
    )


__all__ = (
    "EvaluationCaseSetV231",
    "EvaluationCasePairV231",
    "EvaluationCaseSpecV231",
    "EvaluationCategoryV231",
    "EvaluationManifestV231",
    "EvaluationMetricsV231",
    "EvaluationPreflightV231",
    "EvaluationArmRunV231",
    "EvaluationOntologyViewSetV231",
    "EvaluationPolicyV231",
    "EvaluationTruthSetV231",
    "LazyTruthStoreV231",
    "MeasuredResultTerminalV231",
    "FixedEvaluationArtifactV231",
    "OpenAICompatibleDiscoveryTransportV231",
    "build_evaluation_preflight_v231",
    "load_evaluation_case_set_v231",
    "load_evaluation_truth_set_v231",
    "load_evaluation_views_v231",
    "materialize_evaluation_case_v231",
    "render_evaluation_markdown_v231",
    "run_conflict_aware_arm_v231",
    "run_evaluation_case_pair_v231",
    "run_evaluation_policy_v231",
    "run_fixed_evaluation_once_v231",
    "score_measured_terminal_v231",
    "score_evaluation_pairs_v231",
)
