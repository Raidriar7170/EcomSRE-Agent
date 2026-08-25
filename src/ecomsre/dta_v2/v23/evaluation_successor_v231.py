"""Independent data and admission contracts for the DTA v2.3.1 successor."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Literal, cast

from pydantic import Field, StrictBool, StrictInt, model_validator

from ecomsre.dta_v2.v22.memory import build_memory_views_v22
from ecomsre.dta_v2.v22.predicates import (
    MechanismV22,
    build_default_evidence_support_policy_v22,
    evaluate_support_v22,
)
from ecomsre.dta_v2.v22.practical_runner import _baseline, _memory_outcome
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import QuerySpecificReplayBackendV22
from ecomsre.dta_v2.v23.conflict_model_v231 import (
    ConflictTypeV231,
    assess_conflict_v231,
)
from ecomsre.dta_v2.v23.evaluation import (
    EvaluationArmRunV23,
    _build_common_context_v23,
    load_evaluation_case_set_v23,
    materialize_evaluation_case_v23,
)
from ecomsre.dta_v2.v23.evaluation_v231 import (
    EvaluationArmRunV231,
    EvaluationCasePairV231,
    EvaluationCaseSpecV231,
    EvaluationCategoryV231,
    EvaluationMetricsV231,
    EvaluationOntologyViewSpecV231,
    EvaluationPolicyV231,
    EvaluationTruthV231,
    ManifestFileBindingV231,
    LazyTruthStoreV231,
    MeasuredResultTerminalV231,
    OpenAICompatibleDiscoveryTransportV231,
    _normal_resource_services_v231,
    _residual_graph_v231,
    load_evaluation_case_set_v231,
    materialize_evaluation_case_v231,
    run_evaluation_case_pair_v231,
    score_evaluation_pairs_v231,
    score_measured_terminal_v231,
)
from ecomsre.dta_v2.v23.discovery_router import NegativeCoverageLedgerV23
from ecomsre.dta_v2.v23.discovery_runtime_v231 import (
    build_conflict_aware_state_v231,
)
from ecomsre.dta_v2.v23.novelty_gate_v231 import NoveltyDispositionV231
from ecomsre.dta_v2.v23.known_admission import build_known_admission_state_v23


class AdmissionStratumV231Successor(str, Enum):
    NOVEL_HIDDEN = "NOVEL_HIDDEN"
    NOVEL_UNREGISTERED = "NOVEL_UNREGISTERED"
    REGISTERED_KNOWN = "REGISTERED_KNOWN"
    NO_INCIDENT = "NO_INCIDENT"
    INSUFFICIENT_IRRECONCILABLE = "INSUFFICIENT_IRRECONCILABLE"


class SuccessorCaseSetV231(DtaModelV22):
    schema_version: Literal["dta-v231.successor-case-set.v1"]
    freeze_id: Literal["dta-v231-successor-independent-freeze-20260825-a"]
    cases: tuple[EvaluationCaseSpecV231, ...] = Field(min_length=24, max_length=24)

    @model_validator(mode="after")
    def require_cases(self) -> "SuccessorCaseSetV231":
        ids = tuple(item.case_id for item in self.cases)
        if ids != tuple(f"vx-{ordinal:03d}" for ordinal in range(101, 125)):
            raise ValueError("successor case IDs differ")
        if len({item.source_bytes_sha256 for item in self.cases}) != 24:
            raise ValueError("successor observer bytes are not unique")
        return self

    def require(self, case_id: str) -> EvaluationCaseSpecV231:
        item = next((value for value in self.cases if value.case_id == case_id), None)
        if item is None:
            raise ValueError("successor evaluation case is absent")
        return item


class SuccessorTruthRecordV231(DtaModelV22):
    evaluator_truth: EvaluationTruthV231
    admission_stratum: AdmissionStratumV231Successor
    expected_known_mechanism: MechanismV22 | None
    counterfactual_target_role: Literal["TARGET_LOW", "TARGET_HIGH"] | None

    @model_validator(mode="after")
    def require_truth_projection(self) -> "SuccessorTruthRecordV231":
        category = self.evaluator_truth.category
        expected_stratum = {
            EvaluationCategoryV231.NOVEL_HIDDEN: AdmissionStratumV231Successor.NOVEL_HIDDEN,
            EvaluationCategoryV231.NOVEL_UNREGISTERED: AdmissionStratumV231Successor.NOVEL_UNREGISTERED,
            EvaluationCategoryV231.REGISTERED_KNOWN: AdmissionStratumV231Successor.REGISTERED_KNOWN,
            EvaluationCategoryV231.NO_INCIDENT: AdmissionStratumV231Successor.NO_INCIDENT,
            EvaluationCategoryV231.INSUFFICIENT_CONFLICT: AdmissionStratumV231Successor.INSUFFICIENT_IRRECONCILABLE,
        }[category]
        if self.admission_stratum is not expected_stratum:
            raise ValueError("successor admission stratum differs from evaluator truth")
        if (
            self.admission_stratum is AdmissionStratumV231Successor.REGISTERED_KNOWN
        ) != (self.expected_known_mechanism is not None):
            raise ValueError("successor expected known mechanism differs")
        if (self.evaluator_truth.counterfactual_pair_id is None) != (
            self.counterfactual_target_role is None
        ):
            raise ValueError("successor counterfactual role differs")
        return self


class SuccessorTruthSetV231(DtaModelV22):
    schema_version: Literal["dta-v231.successor-truth-set.v1"]
    truths: tuple[SuccessorTruthRecordV231, ...] = Field(
        min_length=24,
        max_length=24,
    )

    @model_validator(mode="after")
    def require_truths(self) -> "SuccessorTruthSetV231":
        ids = tuple(item.evaluator_truth.case_id for item in self.truths)
        if ids != tuple(f"vx-{ordinal:03d}" for ordinal in range(101, 125)):
            raise ValueError("successor truth IDs differ")
        counts = {
            stratum: sum(item.admission_stratum is stratum for item in self.truths)
            for stratum in AdmissionStratumV231Successor
        }
        expected = {
            AdmissionStratumV231Successor.NOVEL_HIDDEN: 10,
            AdmissionStratumV231Successor.NOVEL_UNREGISTERED: 4,
            AdmissionStratumV231Successor.REGISTERED_KNOWN: 4,
            AdmissionStratumV231Successor.NO_INCIDENT: 3,
            AdmissionStratumV231Successor.INSUFFICIENT_IRRECONCILABLE: 3,
        }
        if counts != expected:
            raise ValueError("successor truth composition differs")
        return self

    def require(self, case_id: str) -> SuccessorTruthRecordV231:
        item = next(
            (value for value in self.truths if value.evaluator_truth.case_id == case_id),
            None,
        )
        if item is None:
            raise ValueError("successor evaluator truth is absent")
        return item


class SuccessorTruthShardBindingV231(DtaModelV22):
    case_id: str
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SuccessorTruthIndexV231(DtaModelV22):
    schema_version: Literal["dta-v231.successor-truth-index.v1"]
    shards: tuple[SuccessorTruthShardBindingV231, ...] = Field(
        min_length=24,
        max_length=24,
    )
    index_sha256: str

    @model_validator(mode="after")
    def require_index(self) -> "SuccessorTruthIndexV231":
        ids = tuple(item.case_id for item in self.shards)
        if ids != tuple(f"vx-{ordinal:03d}" for ordinal in range(101, 125)):
            raise ValueError("successor truth index IDs differ")
        paths = tuple(item.path for item in self.shards)
        if paths != tuple(f"truth/{case_id}.json" for case_id in ids):
            raise ValueError("successor truth shard paths differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"index_sha256"})
        )
        if self.index_sha256 != expected:
            raise ValueError("successor truth index digest differs")
        return self

    def require(self, case_id: str) -> SuccessorTruthShardBindingV231:
        item = next((value for value in self.shards if value.case_id == case_id), None)
        if item is None:
            raise ValueError("successor truth shard binding is absent")
        return item


class SuccessorTruthShardV231(DtaModelV22):
    schema_version: Literal["dta-v231.successor-truth-shard.v1"]
    record: SuccessorTruthRecordV231

    @model_validator(mode="after")
    def require_shard(self) -> "SuccessorTruthShardV231":
        if not self.record.evaluator_truth.case_id.startswith("vx-"):
            raise ValueError("successor truth shard case ID differs")
        return self


class SuccessorOntologyViewSetV231(DtaModelV22):
    schema_version: Literal["dta-v231.successor-ontology-view-set.v1"]
    views: tuple[EvaluationOntologyViewSpecV231, ...] = Field(
        min_length=24,
        max_length=24,
    )

    @model_validator(mode="after")
    def require_views(self) -> "SuccessorOntologyViewSetV231":
        ids = tuple(item.case_id for item in self.views)
        if ids != tuple(f"vx-{ordinal:03d}" for ordinal in range(101, 125)):
            raise ValueError("successor ontology-view IDs differ")
        return self

    def require(self, case_id: str) -> EvaluationOntologyViewSpecV231:
        item = next((value for value in self.views if value.case_id == case_id), None)
        if item is None:
            raise ValueError("successor ontology view is absent")
        return item


class AdmissionMatrixEntryV231Successor(DtaModelV22):
    schema_version: Literal["dta-v231.successor-admission-entry.v1"]
    case_id: str
    stratum: AdmissionStratumV231Successor
    case_bytes_sha256: str
    active_view_sha256: str
    common_memory_sha256: str
    common_read_count: StrictInt = Field(ge=0, le=2)
    common_action_ids: tuple[str, ...]
    contradiction_proof_action_ids: tuple[str, ...]
    admission_memory_sha256: str
    support_policy_sha256: str
    known_admission_sha256: str
    active_known_terminal_count: StrictInt = Field(ge=0)
    active_known_terminal_mechanisms: tuple[MechanismV22, ...]
    active_known_terminal_roots: tuple[str, ...]
    active_known_terminal_parents: tuple[str, ...]
    matched_clause_ids: tuple[str, ...]
    supporting_evidence_refs: tuple[str, ...]
    active_known_hypothesis_count: StrictInt = Field(ge=1)
    accepted_active_known_support_count: StrictInt = Field(ge=0)
    rejected_active_known_support_count: StrictInt = Field(ge=0)
    active_support_decision_sha256: tuple[str, ...]
    no_incident_admissible: StrictBool
    conflicting_evidence: StrictBool
    conflict_prone_case: StrictBool
    initial_conflict_type: ConflictTypeV231
    initial_cluster_root_counts: tuple[StrictInt, ...]
    initial_cluster_domain_counts: tuple[StrictInt, ...]
    initial_novelty_disposition: NoveltyDispositionV231
    discriminating_plan_sha256: str | None
    selected_discriminating_source: EvidenceSourceV22 | None
    selected_candidate_order_roles: tuple[
        Literal["CANDIDATE_HIGH", "CANDIDATE_LOW"], ...
    ]
    selected_truth_relative_roles: tuple[Literal["PEER", "TARGET"], ...]
    conflict_prone_design_pass: StrictBool
    expected_known_terminal_matched: StrictBool
    registered_support_incomplete: StrictBool
    conflict_type: ConflictTypeV231 | None
    explicit_contradiction_reason_codes: tuple[str, ...]
    contradiction_witness_ids: tuple[str, ...]
    contradiction_evidence_refs: tuple[str, ...]
    explicit_unresolvable_contradiction: StrictBool
    counterfactual_pair_id: str | None
    counterfactual_target_role: Literal["TARGET_LOW", "TARGET_HIGH"] | None
    contract_pass: StrictBool
    entry_sha256: str

    @model_validator(mode="after")
    def require_entry(self) -> "AdmissionMatrixEntryV231Successor":
        for values, label in (
            (self.common_action_ids, "common action IDs"),
            (self.contradiction_proof_action_ids, "proof action IDs"),
            (self.active_known_terminal_mechanisms, "known mechanisms"),
            (self.active_known_terminal_roots, "known roots"),
            (self.active_known_terminal_parents, "known parents"),
            (self.matched_clause_ids, "matched clauses"),
            (self.supporting_evidence_refs, "supporting evidence refs"),
            (self.active_support_decision_sha256, "active support decisions"),
            (self.initial_cluster_root_counts, "initial cluster root counts"),
            (self.initial_cluster_domain_counts, "initial cluster domain counts"),
            (self.selected_candidate_order_roles, "selected candidate roles"),
            (self.selected_truth_relative_roles, "selected truth roles"),
            (self.explicit_contradiction_reason_codes, "contradiction codes"),
            (self.contradiction_witness_ids, "contradiction witnesses"),
            (self.contradiction_evidence_refs, "contradiction evidence refs"),
        ):
            if values != tuple(sorted(set(values), key=str)):
                raise ValueError(f"successor matrix {label} are not canonical")
        if (
            self.accepted_active_known_support_count
            + self.rejected_active_known_support_count
            != self.active_known_hypothesis_count
            or self.accepted_active_known_support_count
            != self.active_known_terminal_count
        ):
            raise ValueError("successor active known support accounting differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"entry_sha256"})
        )
        if self.entry_sha256 != expected:
            raise ValueError("successor admission entry digest differs")
        return self


class CounterfactualPairAdmissionV231Successor(DtaModelV22):
    pair_id: str
    case_ids: tuple[str, str]
    stratum: AdmissionStratumV231Successor
    stratum_preserved: Literal[True]
    target_roles: tuple[Literal["TARGET_HIGH", "TARGET_LOW"], ...]
    admission_shape_preserved: Literal[True]
    admission_shape_sha256: str
    discriminating_plan_shape_preserved: Literal[True]
    discriminating_plan_shape_sha256: str


class AdmissionMatrixV231Successor(DtaModelV22):
    schema_version: Literal["dta-v231.successor-admission-matrix.v1"]
    case_count: Literal[24]
    provider_calls: Literal[0]
    entries: tuple[AdmissionMatrixEntryV231Successor, ...] = Field(
        min_length=24,
        max_length=24,
    )
    counterfactual_pairs: tuple[CounterfactualPairAdmissionV231Successor, ...] = Field(
        min_length=4
    )
    status: Literal["DTA_V231_SUCCESSOR_EVALUATION_DATA_PASS"]
    matrix_sha256: str

    @model_validator(mode="after")
    def require_matrix(self) -> "AdmissionMatrixV231Successor":
        ids = tuple(item.case_id for item in self.entries)
        if ids != tuple(sorted(set(ids))) or not all(
            item.contract_pass for item in self.entries
        ):
            raise ValueError("successor admission matrix does not pass every case")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"matrix_sha256"})
        )
        if self.matrix_sha256 != expected:
            raise ValueError("successor admission matrix digest differs")
        return self


def load_successor_case_set_v231(path: Path) -> SuccessorCaseSetV231:
    return SuccessorCaseSetV231.model_validate_json(path.read_bytes())


def load_successor_truth_set_v231(path: Path) -> SuccessorTruthSetV231:
    index = load_successor_truth_index_v231(path)
    records = tuple(
        load_successor_truth_shard_v231(
            index_path=path,
            binding=binding,
        ).record
        for binding in index.shards
    )
    return SuccessorTruthSetV231(
        schema_version="dta-v231.successor-truth-set.v1",
        truths=records,
    )


def load_successor_truth_index_v231(path: Path) -> SuccessorTruthIndexV231:
    return SuccessorTruthIndexV231.model_validate_json(path.read_bytes())


def load_successor_truth_shard_v231(
    *,
    index_path: Path,
    binding: SuccessorTruthShardBindingV231,
) -> SuccessorTruthShardV231:
    path = index_path.parent / binding.path
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding.sha256:
        raise ValueError("successor truth shard bytes differ")
    shard = SuccessorTruthShardV231.model_validate_json(raw)
    if shard.record.evaluator_truth.case_id != binding.case_id:
        raise ValueError("successor truth shard and binding differ")
    return shard


def load_successor_views_v231(path: Path) -> SuccessorOntologyViewSetV231:
    return SuccessorOntologyViewSetV231.model_validate_json(path.read_bytes())


def _conflict_proof_v231_successor(
    *,
    context: Any,
) -> tuple[Any, Any, Any, tuple[str, ...]]:
    outcomes = list(context.outcomes)
    existing = {item.action_id for item in outcomes}
    backend = QuerySpecificReplayBackendV22(context.case.capture)
    run_id = semantic_sha256_v22(
        {"case": context.case.case_id, "lane": "dta-v231-successor-admission-proof"}
    )[:32]
    for action in context.catalog.registry_actions:
        if action.source is not EvidenceSourceV22.TRACES or action.action_id in existing:
            continue
        source = backend.execute(action)
        outcome = _memory_outcome(
            action=action,
            outcome=source,
            run_id=run_id,
            dispatch_ordinal=len(outcomes) + 1,
            observed_at=context.case.capture.captured_at,
        )
        if outcome.outcome_sha256 not in {item.outcome_sha256 for item in outcomes}:
            outcomes.append(outcome)
        break
    memory, _ = build_memory_views_v22(
        outcomes=tuple(outcomes),
        baseline=_baseline(context.case),
        observed_at=context.case.capture.captured_at,
        top_k=64,
    )
    admission = build_known_admission_state_v23(
        view=context.view,
        memory=memory,
        topology_edges=context.case.topology_edges,
    )
    graph = _residual_graph_v231(context=context, memory=memory)
    assessment = assess_conflict_v231(
        graph=graph,
        topology_edges=context.case.topology_edges,
        legal_sources=(),
        remaining_reads=0,
        normal_resource_services=_normal_resource_services_v231(
            graph=graph,
            memory=memory,
        ),
    )
    return admission, assessment, memory, tuple(
        sorted(item.action_id for item in outcomes if item.action_id not in existing)
    )


def _entry_v231_successor(
    *,
    repository_root: Path,
    spec: EvaluationCaseSpecV231,
    truth: SuccessorTruthRecordV231,
    view: EvaluationOntologyViewSpecV231,
) -> AdmissionMatrixEntryV231Successor:
    case = materialize_evaluation_case_v231(
        repository_root=repository_root,
        spec=spec,
    )
    context = _build_common_context_v23(
        case=case,
        hidden_mechanism=view.hidden_mechanism,
    )
    admission = context.admission
    initial_graph = _residual_graph_v231(context=context, memory=context.memory)
    initial_state = build_conflict_aware_state_v231(
        graph=initial_graph,
        catalog=context.catalog,
        topology_edges=context.case.topology_edges,
        no_incident_admissible=admission.no_incident_admissible,
        negative_coverage=NegativeCoverageLedgerV23.empty(),
        discovery_reads_used=0,
        remaining_weighted_budget=3.0,
        conflict_resolution_read_used=False,
        normal_resource_services=_normal_resource_services_v231(
            graph=initial_graph,
            memory=context.memory,
        ),
        excluded_action_ids=tuple(
            sorted(item.action_id for item in context.outcomes)
        ),
    )
    initial_assessment = initial_state.conflict_assessment
    initial_plan = initial_state.discriminating_plan
    selected_targets = (
        () if initial_plan is None else initial_plan.selected_action.target_services
    )
    candidates = context.case.candidate_services
    expected_root = truth.evaluator_truth.expected_root_service
    selected_candidate_roles = tuple(
        sorted(
            "CANDIDATE_LOW" if target == candidates[0] else "CANDIDATE_HIGH"
            for target in selected_targets
        )
    )
    selected_truth_roles = tuple(
        sorted(
            "TARGET" if target == expected_root else "PEER"
            for target in selected_targets
        )
    )
    conflict_prone = truth.evaluator_truth.conflict_prone_novelty
    conflict_prone_design_pass = not conflict_prone or (
        initial_assessment.conflict_type is ConflictTypeV231.RESOLVABLE_CONFLICT
        and initial_state.novelty_decision.disposition
        is NoveltyDispositionV231.DISCOVERY_READ_REQUIRED
        and initial_plan is not None
        and any(
            len(cluster.candidate_root_services) >= 2
            and len(cluster.broad_domains) >= 2
            for cluster in initial_assessment.interpretation_clusters
        )
    )
    proof_admission = admission
    proof_memory = context.memory
    proof_action_ids: tuple[str, ...] = ()
    known_count = len(proof_admission.admitted_diagnoses)
    no_incident = proof_admission.no_incident_admissible
    conflict_type = None
    contradiction_reasons: tuple[str, ...] = ()
    contradiction_witness_ids: tuple[str, ...] = ()
    contradiction_evidence_refs: tuple[str, ...] = ()
    unresolvable = False
    if (
        truth.admission_stratum
        is AdmissionStratumV231Successor.INSUFFICIENT_IRRECONCILABLE
    ):
        proof_admission, assessment, proof_memory, proof_action_ids = (
            _conflict_proof_v231_successor(context=context)
        )
        known_count = len(proof_admission.admitted_diagnoses)
        no_incident = proof_admission.no_incident_admissible
        conflict_type = assessment.conflict_type
        contradiction_reasons = tuple(
            sorted(
                {
                    edge.reason_code
                    for cluster in assessment.interpretation_clusters
                    for edge in cluster.contradiction_edges
                }
            )
        )
        contradiction_witness_ids = tuple(
            sorted(
                {
                    witness
                    for cluster in assessment.interpretation_clusters
                    for edge in cluster.contradiction_edges
                    for witness in (edge.left_id, edge.right_id)
                }
            )
        )
        contradiction_evidence_refs = tuple(
            sorted(
                {
                    ref
                    for cluster in assessment.interpretation_clusters
                    if cluster.contradiction_edges
                    for ref in cluster.evidence_refs
                }
            )
        )
        unresolvable = (
            known_count == 0
            and not no_incident
            and conflict_type is ConflictTypeV231.IRRECONCILABLE_CONFLICT
            and bool(contradiction_reasons)
        )
    mechanisms = tuple(
        sorted(
            (item.mechanism for item in proof_admission.admitted_diagnoses),
            key=lambda item: item.value,
        )
    )
    roots = tuple(
        sorted(item.root_service for item in proof_admission.admitted_diagnoses)
    )
    parents = tuple(
        sorted(
            item.parent_service
            for item in proof_admission.admitted_diagnoses
            if item.parent_service is not None
        )
    )
    matched_clauses = tuple(
        sorted(item.matched_clause_id for item in proof_admission.admitted_diagnoses)
    )
    supporting_refs = tuple(
        sorted(
            {
                ref
                for item in proof_admission.admitted_diagnoses
                for ref in item.supporting_evidence_refs
            }
        )
    )
    policy = build_default_evidence_support_policy_v22()
    support_decisions = []
    for hypothesis in context.view.active_hypotheses:
        if hypothesis.target_service is None or hypothesis.mechanism in {
            MechanismV22.NO_INCIDENT,
            MechanismV22.UNKNOWN,
        }:
            continue
        parent = None
        if hypothesis.mechanism is MechanismV22.DEPENDENCY_LATENCY:
            parent = next(
                (
                    right if left == hypothesis.target_service else left
                    for left, right in context.case.topology_edges
                    if hypothesis.target_service in {left, right}
                ),
                None,
            )
        support_decisions.append(
            evaluate_support_v22(
                policy=policy,
                mechanism=hypothesis.mechanism,
                target_service=hypothesis.target_service,
                parent_service=parent,
                predicates=proof_memory.predicates,
            )
        )
    accepted_support_count = sum(item.accepted for item in support_decisions)
    rejected_support_count = len(support_decisions) - accepted_support_count
    expected_match = (
        known_count == 1
        and truth.expected_known_mechanism is not None
        and mechanisms == (truth.expected_known_mechanism,)
        and roots == (truth.evaluator_truth.expected_root_service,)
    )
    incomplete = (
        known_count == 0
        and bool(support_decisions)
        and rejected_support_count == len(support_decisions)
    )
    stratum = truth.admission_stratum
    if stratum in {
        AdmissionStratumV231Successor.NOVEL_HIDDEN,
        AdmissionStratumV231Successor.NOVEL_UNREGISTERED,
    }:
        contract_pass = (
            known_count == 0
            and not no_incident
            and conflict_prone_design_pass
        )
    elif stratum is AdmissionStratumV231Successor.REGISTERED_KNOWN:
        contract_pass = expected_match and not no_incident
    elif stratum is AdmissionStratumV231Successor.NO_INCIDENT:
        contract_pass = known_count == 0 and no_incident
    else:
        contract_pass = incomplete and not no_incident and unresolvable
    payload: dict[str, Any] = {
        "schema_version": "dta-v231.successor-admission-entry.v1",
        "case_id": spec.case_id,
        "stratum": stratum,
        "case_bytes_sha256": case.source_bytes_sha256,
        "active_view_sha256": context.view.view_sha256,
        "common_memory_sha256": context.memory.memory_sha256,
        "common_read_count": context.common_read_count,
        "common_action_ids": context.common_action_ids,
        "contradiction_proof_action_ids": proof_action_ids,
        "admission_memory_sha256": proof_memory.memory_sha256,
        "support_policy_sha256": proof_admission.support_policy_sha256,
        "known_admission_sha256": proof_admission.state_sha256,
        "active_known_terminal_count": known_count,
        "active_known_terminal_mechanisms": mechanisms,
        "active_known_terminal_roots": roots,
        "active_known_terminal_parents": parents,
        "matched_clause_ids": matched_clauses,
        "supporting_evidence_refs": supporting_refs,
        "active_known_hypothesis_count": len(support_decisions),
        "accepted_active_known_support_count": accepted_support_count,
        "rejected_active_known_support_count": rejected_support_count,
        "active_support_decision_sha256": tuple(
            sorted(item.decision_sha256 for item in support_decisions)
        ),
        "no_incident_admissible": no_incident,
        "conflicting_evidence": proof_admission.conflicting_evidence,
        "conflict_prone_case": conflict_prone,
        "initial_conflict_type": initial_assessment.conflict_type,
        "initial_cluster_root_counts": tuple(
            sorted(
                len(cluster.candidate_root_services)
                for cluster in initial_assessment.interpretation_clusters
            )
        ),
        "initial_cluster_domain_counts": tuple(
            sorted(
                len(cluster.broad_domains)
                for cluster in initial_assessment.interpretation_clusters
            )
        ),
        "initial_novelty_disposition": (
            initial_state.novelty_decision.disposition
        ),
        "discriminating_plan_sha256": (
            None if initial_plan is None else initial_plan.plan_sha256
        ),
        "selected_discriminating_source": (
            None if initial_plan is None else initial_plan.selected_action.source
        ),
        "selected_candidate_order_roles": selected_candidate_roles,
        "selected_truth_relative_roles": selected_truth_roles,
        "conflict_prone_design_pass": conflict_prone_design_pass,
        "expected_known_terminal_matched": expected_match,
        "registered_support_incomplete": incomplete,
        "conflict_type": conflict_type,
        "explicit_contradiction_reason_codes": contradiction_reasons,
        "contradiction_witness_ids": contradiction_witness_ids,
        "contradiction_evidence_refs": contradiction_evidence_refs,
        "explicit_unresolvable_contradiction": unresolvable,
        "counterfactual_pair_id": truth.evaluator_truth.counterfactual_pair_id,
        "counterfactual_target_role": truth.counterfactual_target_role,
        "contract_pass": contract_pass,
    }
    draft = AdmissionMatrixEntryV231Successor.model_construct(
        **payload,
        entry_sha256="0" * 64,
    )
    return AdmissionMatrixEntryV231Successor.model_validate(
        {
            **payload,
            "entry_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"entry_sha256"})
            ),
        }
    )


def _admission_shape_sha256_v231_successor(
    entry: AdmissionMatrixEntryV231Successor,
) -> str:
    return semantic_sha256_v22(
        {
            "stratum": entry.stratum,
            "common_read_count": entry.common_read_count,
            "active_known_terminal_count": entry.active_known_terminal_count,
            "active_known_terminal_mechanisms": entry.active_known_terminal_mechanisms,
            "no_incident_admissible": entry.no_incident_admissible,
            "conflicting_evidence": entry.conflicting_evidence,
            "registered_support_incomplete": entry.registered_support_incomplete,
            "conflict_type": entry.conflict_type,
            "explicit_contradiction_reason_codes": (
                entry.explicit_contradiction_reason_codes
            ),
            "explicit_unresolvable_contradiction": (
                entry.explicit_unresolvable_contradiction
            ),
        }
    )


def _discriminating_plan_shape_sha256_v231_successor(
    entry: AdmissionMatrixEntryV231Successor,
) -> str:
    return semantic_sha256_v22(
        {
            "conflict_prone_case": entry.conflict_prone_case,
            "initial_conflict_type": entry.initial_conflict_type,
            "initial_cluster_root_counts": entry.initial_cluster_root_counts,
            "initial_cluster_domain_counts": entry.initial_cluster_domain_counts,
            "initial_novelty_disposition": entry.initial_novelty_disposition,
            "selected_discriminating_source": entry.selected_discriminating_source,
            "selected_candidate_order_roles": entry.selected_candidate_order_roles,
        }
    )


def build_admission_matrix_v231_successor(
    *,
    repository_root: Path,
    cases: SuccessorCaseSetV231,
    truths: SuccessorTruthSetV231,
    views: SuccessorOntologyViewSetV231,
) -> AdmissionMatrixV231Successor:
    entries = tuple(
        _entry_v231_successor(
            repository_root=repository_root,
            spec=spec,
            truth=truths.require(spec.case_id),
            view=views.require(spec.case_id),
        )
        for spec in cases.cases
    )
    pair_ids = tuple(
        sorted(
            {
                item.counterfactual_pair_id
                for item in entries
                if item.counterfactual_pair_id is not None
            }
        )
    )
    pairs: list[CounterfactualPairAdmissionV231Successor] = []
    for pair_id in pair_ids:
        pair_entries = tuple(
            item for item in entries if item.counterfactual_pair_id == pair_id
        )
        if len(pair_entries) != 2:
            raise ValueError("successor counterfactual pair denominator differs")
        strata = {item.stratum for item in pair_entries}
        roles = tuple(
            sorted(
                item.counterfactual_target_role
                for item in pair_entries
                if item.counterfactual_target_role is not None
            )
        )
        if len(strata) != 1 or roles != ("TARGET_HIGH", "TARGET_LOW"):
            raise ValueError("successor counterfactual pair does not preserve stratum")
        shapes = {
            _admission_shape_sha256_v231_successor(item) for item in pair_entries
        }
        if len(shapes) != 1:
            raise ValueError("successor counterfactual admission shape differs")
        plan_shapes = {
            _discriminating_plan_shape_sha256_v231_successor(item)
            for item in pair_entries
        }
        if len(plan_shapes) != 1:
            raise ValueError("successor counterfactual plan shape differs")
        pairs.append(
            CounterfactualPairAdmissionV231Successor(
                pair_id=pair_id,
                case_ids=cast(
                    tuple[str, str],
                    tuple(sorted(item.case_id for item in pair_entries)),
                ),
                stratum=next(iter(strata)),
                stratum_preserved=True,
                target_roles=roles,
                admission_shape_preserved=True,
                admission_shape_sha256=next(iter(shapes)),
                discriminating_plan_shape_preserved=True,
                discriminating_plan_shape_sha256=next(iter(plan_shapes)),
            )
        )
    payload: dict[str, Any] = {
        "schema_version": "dta-v231.successor-admission-matrix.v1",
        "case_count": 24,
        "provider_calls": 0,
        "entries": entries,
        "counterfactual_pairs": tuple(pairs),
        "status": "DTA_V231_SUCCESSOR_EVALUATION_DATA_PASS",
    }
    draft = AdmissionMatrixV231Successor.model_construct(
        **payload,
        matrix_sha256="0" * 64,
    )
    return AdmissionMatrixV231Successor.model_validate(
        {
            **payload,
            "matrix_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"matrix_sha256"})
            ),
        }
    )


class SuccessorEvaluationManifestV231(DtaModelV22):
    schema_version: Literal["dta-v231.successor-evaluation-manifest.v1"]
    base_commit: Literal["7fe2bff7186cca1cedd2513f7984709057fc19e5"]
    branch: Literal["codex/dta-v231-successor-evaluation"]
    provider_model: str
    planned_case_count: Literal[24]
    planned_run_count: Literal[48]
    planned_execution_count: Literal[1]
    predecessor_study_disposition: Literal[
        "BLOCKED_DTA_V231_EVALUATION_DATA"
    ] = "BLOCKED_DTA_V231_EVALUATION_DATA"
    study_relation: Literal[
        "INDEPENDENT_SUCCESSOR_NOT_RERUN"
    ] = "INDEPENDENT_SUCCESSOR_NOT_RERUN"
    predecessor_freeze: ManifestFileBindingV231
    predecessor_freeze_verifier: ManifestFileBindingV231
    predecessor_runtime_manifest: ManifestFileBindingV231
    cases: ManifestFileBindingV231
    truth_index: ManifestFileBindingV231
    truth_shards: tuple[ManifestFileBindingV231, ...] = Field(
        min_length=24,
        max_length=24,
    )
    ontology_views: ManifestFileBindingV231
    admission_matrix: ManifestFileBindingV231
    dataset_builder: ManifestFileBindingV231
    admission_matrix_builder: ManifestFileBindingV231
    successor_runtime_sources: tuple[ManifestFileBindingV231, ...] = Field(
        min_length=2
    )
    strict_system_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    treatment_system_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_json: str
    output_markdown: str
    independent_review: str
    fixed_at_utc: datetime

    @model_validator(mode="after")
    def require_manifest(self) -> "SuccessorEvaluationManifestV231":
        if (
            self.fixed_at_utc.tzinfo is None
            or self.fixed_at_utc.utcoffset() != timedelta(0)
        ):
            raise ValueError("successor manifest timestamp is not UTC")
        paths = tuple(item.path for item in self.successor_runtime_sources)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("successor runtime source bindings are not canonical")
        truth_paths = tuple(item.path for item in self.truth_shards)
        if truth_paths != tuple(
            f"config/dta-v231-successor/evaluation/truth/vx-{ordinal:03d}.json"
            for ordinal in range(101, 125)
        ):
            raise ValueError("successor truth shard bindings differ")
        return self


class SuccessorPreExecutionReviewV231(DtaModelV22):
    schema_version: Literal["dta-v231.successor-pre-execution-review.v1"]
    reviewer_identity: str
    reviewer_task: str
    reviewed_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_admission_matrix_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    predecessor_preservation: Literal["PASS"]
    data_admission: Literal["PASS"]
    truth_blinding: Literal["PASS"]
    write_once_execution: Literal["PASS"]
    claim_accuracy: Literal["PASS"]
    must_fix_count: Literal[0]
    status: Literal["MUST_FIX_0_CLAIM_ACCURACY_PASS"]
    reviewed_at_utc: datetime
    review_sha256: str

    @model_validator(mode="after")
    def require_review(self) -> "SuccessorPreExecutionReviewV231":
        if (
            self.reviewed_at_utc.tzinfo is None
            or self.reviewed_at_utc.utcoffset() != timedelta(0)
        ):
            raise ValueError("successor review timestamp is not UTC")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"review_sha256"})
        )
        if self.review_sha256 != expected:
            raise ValueError("successor independent review digest differs")
        return self


class SuccessorEvaluationPreflightV231(DtaModelV22):
    schema_version: Literal["dta-v231.successor-evaluation-preflight.v1"]
    case_count: Literal[24]
    planned_runs: Literal[48]
    execution_count_before: Literal[0]
    provider_model: str
    cases_sha256: str
    truth_index_sha256: str
    truth_shard_set_sha256: str
    ontology_views_sha256: str
    admission_matrix_sha256: str
    manifest_sha256: str
    independent_review_sha256: str
    new_case_bytes_sha256: tuple[str, ...] = Field(min_length=24, max_length=24)
    admission_status: Literal["DTA_V231_SUCCESSOR_EVALUATION_DATA_PASS"]
    independent_review_status: Literal["MUST_FIX_0_CLAIM_ACCURACY_PASS"]
    predecessor_study_disposition: Literal[
        "BLOCKED_DTA_V231_EVALUATION_DATA"
    ] = "BLOCKED_DTA_V231_EVALUATION_DATA"
    study_relation: Literal[
        "INDEPENDENT_SUCCESSOR_NOT_RERUN"
    ] = "INDEPENDENT_SUCCESSOR_NOT_RERUN"
    output_path: str
    output_markdown_path: str
    status: Literal["DTA_V231_SUCCESSOR_EVALUATION_PREFLIGHT_PASS"]


def _file_sha256_v231_successor(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_successor_admission_matrix_v231(
    path: Path,
) -> AdmissionMatrixV231Successor:
    return AdmissionMatrixV231Successor.model_validate_json(path.read_bytes())


def load_successor_evaluation_manifest_v231(
    path: Path,
) -> SuccessorEvaluationManifestV231:
    return SuccessorEvaluationManifestV231.model_validate_json(path.read_bytes())


def load_successor_pre_execution_review_v231(
    path: Path,
) -> SuccessorPreExecutionReviewV231:
    return SuccessorPreExecutionReviewV231.model_validate_json(path.read_bytes())


def _verify_successor_binding_v231(
    *,
    repository_root: Path,
    binding: ManifestFileBindingV231,
) -> None:
    path = repository_root / binding.path
    if not path.is_file() or _file_sha256_v231_successor(path) != binding.sha256:
        raise ValueError(f"successor frozen binding differs: {binding.path}")


def _verify_predecessor_freeze_v231_successor(repository_root: Path) -> None:
    freeze_path = repository_root / (
        "config/dta-v231-successor/predecessor-freeze.json"
    )
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if (
        freeze.get("predecessor_engineering_state")
        != "BLOCKED_DTA_V231_EVALUATION_DATA"
        or freeze.get("predecessor_execution_count") != 1
    ):
        raise ValueError("successor predecessor study identity differs")
    for binding in freeze.get("bindings", ()):
        if not isinstance(binding, dict):
            raise ValueError("successor predecessor binding is invalid")
        path = repository_root / str(binding.get("path", ""))
        if (
            not path.is_file()
            or _file_sha256_v231_successor(path) != binding.get("sha256")
        ):
            raise ValueError(f"successor predecessor artifact differs: {path}")
    runtime_manifest_path = (
        repository_root / "config/dta-v231/evaluation/manifest.json"
    )
    if _file_sha256_v231_successor(runtime_manifest_path) != freeze.get(
        "frozen_runtime_manifest_sha256"
    ):
        raise ValueError("successor predecessor runtime manifest differs")
    runtime_manifest = json.loads(
        runtime_manifest_path.read_text(encoding="utf-8")
    )
    runtime_sources = runtime_manifest.get("runtime_sources", ())
    if len(runtime_sources) != freeze.get("frozen_runtime_source_count"):
        raise ValueError("successor predecessor runtime denominator differs")
    for binding in runtime_sources:
        path = repository_root / str(binding.get("path", ""))
        if (
            not path.is_file()
            or _file_sha256_v231_successor(path) != binding.get("sha256")
        ):
            raise ValueError(f"successor algorithm source differs: {path}")


def build_successor_evaluation_preflight_v231(
    *,
    repository_root: Path,
    cases_path: Path,
    truth_index_path: Path,
    ontology_views_path: Path,
    admission_matrix_path: Path,
    manifest_path: Path,
    independent_review_path: Path,
    output_path: Path,
    output_markdown_path: Path,
    expected_provider_model: str,
) -> SuccessorEvaluationPreflightV231:
    manifest = load_successor_evaluation_manifest_v231(manifest_path)
    if manifest.provider_model != expected_provider_model:
        raise ValueError("successor Provider model differs from the frozen manifest")
    expected_paths = {
        cases_path.resolve(): manifest.cases,
        truth_index_path.resolve(): manifest.truth_index,
        ontology_views_path.resolve(): manifest.ontology_views,
        admission_matrix_path.resolve(): manifest.admission_matrix,
    }
    for path, binding in expected_paths.items():
        if (repository_root / binding.path).resolve() != path:
            raise ValueError("successor fixed input path differs from manifest")
        _verify_successor_binding_v231(
            repository_root=repository_root,
            binding=binding,
        )
    for binding in (
        manifest.predecessor_freeze,
        manifest.predecessor_freeze_verifier,
        manifest.predecessor_runtime_manifest,
        manifest.dataset_builder,
        manifest.admission_matrix_builder,
        *manifest.truth_shards,
        *manifest.successor_runtime_sources,
    ):
        _verify_successor_binding_v231(
            repository_root=repository_root,
            binding=binding,
        )
    _verify_predecessor_freeze_v231_successor(repository_root)
    from ecomsre.dta_v2.v23.discovery_provider import DISCOVERY_SYSTEM_PROMPT_V23
    from ecomsre.dta_v2.v23.discovery_provider_v231 import (
        DISCOVERY_SYSTEM_PROMPT_V231,
    )

    if manifest.strict_system_prompt_sha256 != hashlib.sha256(
        DISCOVERY_SYSTEM_PROMPT_V23.encode("utf-8")
    ).hexdigest():
        raise ValueError("successor strict Provider prompt binding differs")
    if manifest.treatment_system_prompt_sha256 != hashlib.sha256(
        DISCOVERY_SYSTEM_PROMPT_V231.encode("utf-8")
    ).hexdigest():
        raise ValueError("successor treatment Provider prompt binding differs")
    if (repository_root / manifest.output_json).resolve() != output_path.resolve():
        raise ValueError("successor output path differs from manifest")
    if (
        repository_root / manifest.output_markdown
    ).resolve() != output_markdown_path.resolve():
        raise ValueError("successor markdown output path differs from manifest")
    if (
        repository_root / manifest.independent_review
    ).resolve() != independent_review_path.resolve():
        raise ValueError("successor review path differs from manifest")
    review = load_successor_pre_execution_review_v231(independent_review_path)
    manifest_sha256 = _file_sha256_v231_successor(manifest_path)
    matrix_file_sha256 = _file_sha256_v231_successor(admission_matrix_path)
    if review.reviewed_manifest_sha256 != manifest_sha256:
        raise ValueError("successor review does not bind the frozen manifest")
    if review.reviewed_admission_matrix_sha256 != matrix_file_sha256:
        raise ValueError("successor review does not bind the admission matrix")
    matrix = load_successor_admission_matrix_v231(admission_matrix_path)
    cases = load_successor_case_set_v231(cases_path)
    views = load_successor_views_v231(ontology_views_path)
    case_ids = tuple(item.case_id for item in cases.cases)
    if case_ids != tuple(item.case_id for item in views.views):
        raise ValueError("successor fixed case and view IDs differ")
    if case_ids != tuple(item.case_id for item in matrix.entries):
        raise ValueError("successor admission matrix case IDs differ")
    new_hashes = tuple(
        materialize_evaluation_case_v231(
            repository_root=repository_root,
            spec=spec,
        ).source_bytes_sha256
        for spec in cases.cases
    )
    if len(set(new_hashes)) != 24:
        raise ValueError("successor capture bytes are not unique")
    old_v23 = load_evaluation_case_set_v23(
        repository_root / "config/dta-v23/evaluation/cases.json"
    )
    blocked_v231 = load_evaluation_case_set_v231(
        repository_root / "config/dta-v231/evaluation/cases.json"
    )
    predecessor_hashes = {
        materialize_evaluation_case_v23(repository_root=repository_root, spec=spec)
        .source_bytes_sha256
        for spec in old_v23.cases
    } | {
        materialize_evaluation_case_v231(repository_root=repository_root, spec=spec)
        .source_bytes_sha256
        for spec in blocked_v231.cases
    }
    if not predecessor_hashes.isdisjoint(new_hashes):
        raise ValueError("successor set reuses predecessor case bytes")
    local_root = repository_root / ".local/dta-v231-successor"
    if any(
        path.exists()
        for path in (
            local_root / "successor-evaluation.started.json",
            local_root / "successor-evaluation.partial.jsonl",
            output_path,
            output_markdown_path,
        )
    ):
        raise FileExistsError("successor write-once boundary already exists")
    return SuccessorEvaluationPreflightV231(
        schema_version="dta-v231.successor-evaluation-preflight.v1",
        case_count=24,
        planned_runs=48,
        execution_count_before=0,
        provider_model=expected_provider_model,
        cases_sha256=_file_sha256_v231_successor(cases_path),
        truth_index_sha256=_file_sha256_v231_successor(truth_index_path),
        truth_shard_set_sha256=semantic_sha256_v22(
            tuple(item.model_dump(mode="json") for item in manifest.truth_shards)
        ),
        ontology_views_sha256=_file_sha256_v231_successor(ontology_views_path),
        admission_matrix_sha256=matrix_file_sha256,
        manifest_sha256=manifest_sha256,
        independent_review_sha256=_file_sha256_v231_successor(
            independent_review_path
        ),
        new_case_bytes_sha256=new_hashes,
        admission_status=matrix.status,
        independent_review_status=review.status,
        output_path=str(output_path.relative_to(repository_root)),
        output_markdown_path=str(output_markdown_path.relative_to(repository_root)),
        status="DTA_V231_SUCCESSOR_EVALUATION_PREFLIGHT_PASS",
    )


class SuccessorLazyTruthStoreV231:
    """Open one successor truth record only after that case completes both arms."""

    def __init__(self, path: Path) -> None:
        self._index_path = path
        self._index = load_successor_truth_index_v231(path)
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
            raise ValueError("successor truth unlock arm case differs")
        if case_id in self._loaded_case_ids:
            raise ValueError("successor evaluator truth case was loaded twice")
        binding = self._index.require(case_id)
        truth = load_successor_truth_shard_v231(
            index_path=self._index_path,
            binding=binding,
        ).record.evaluator_truth
        self._loaded_case_ids.add(case_id)
        return truth


class SuccessorFixedEvaluationArtifactV231(DtaModelV22):
    schema_version: Literal["dta-v231.successor-fixed-evaluation.v1"]
    execution_count: Literal[1]
    case_count: Literal[24]
    run_count: Literal[48]
    baseline_policy: Literal[EvaluationPolicyV231.V23_STRICT_CONFLICT_GATE]
    treatment_policy: Literal[EvaluationPolicyV231.V231_CONFLICT_AWARE_GATE]
    predecessor_study_disposition: Literal[
        "BLOCKED_DTA_V231_EVALUATION_DATA"
    ] = "BLOCKED_DTA_V231_EVALUATION_DATA"
    study_relation: Literal[
        "INDEPENDENT_SUCCESSOR_NOT_RERUN"
    ] = "INDEPENDENT_SUCCESSOR_NOT_RERUN"
    provider_model: str
    preflight: SuccessorEvaluationPreflightV231
    pairs: tuple[EvaluationCasePairV231, ...] = Field(min_length=24, max_length=24)
    metrics: EvaluationMetricsV231
    measured_result_terminal: MeasuredResultTerminalV231
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    docker_calls: Literal[0]
    new_live_faults: Literal[0]
    artifact_sha256: str

    @model_validator(mode="after")
    def require_artifact(self) -> "SuccessorFixedEvaluationArtifactV231":
        ids = tuple(item.case_id for item in self.pairs)
        if ids != tuple(sorted(set(ids))) or len(ids) != 24:
            raise ValueError("successor pair denominator differs")
        if self.provider_model != self.preflight.provider_model:
            raise ValueError("successor Provider binding differs")
        expected_terminal = _score_successor_terminal_v231(self.metrics)
        if self.measured_result_terminal is not expected_terminal:
            raise ValueError("successor measured terminal differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"artifact_sha256"})
        )
        if self.artifact_sha256 != expected:
            raise ValueError("successor artifact digest differs")
        return self


def _score_successor_terminal_v231(
    metrics: EvaluationMetricsV231,
) -> MeasuredResultTerminalV231:
    return score_measured_terminal_v231(
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


def _build_successor_artifact_v231(
    *,
    preflight: SuccessorEvaluationPreflightV231,
    pairs: tuple[EvaluationCasePairV231, ...],
) -> SuccessorFixedEvaluationArtifactV231:
    metrics = score_evaluation_pairs_v231(pairs)
    payload: dict[str, Any] = {
        "schema_version": "dta-v231.successor-fixed-evaluation.v1",
        "execution_count": 1,
        "case_count": 24,
        "run_count": 48,
        "baseline_policy": EvaluationPolicyV231.V23_STRICT_CONFLICT_GATE,
        "treatment_policy": EvaluationPolicyV231.V231_CONFLICT_AWARE_GATE,
        "provider_model": preflight.provider_model,
        "preflight": preflight,
        "pairs": pairs,
        "metrics": metrics,
        "measured_result_terminal": _score_successor_terminal_v231(metrics),
        "agent_writes": 0,
        "runbook_executions": 0,
        "docker_calls": 0,
        "new_live_faults": 0,
    }
    draft = SuccessorFixedEvaluationArtifactV231.model_construct(
        **payload,
        artifact_sha256="0" * 64,
    )
    return SuccessorFixedEvaluationArtifactV231.model_validate(
        {
            **payload,
            "artifact_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"artifact_sha256"})
            ),
        }
    )


def render_successor_evaluation_markdown_v231(
    artifact: SuccessorFixedEvaluationArtifactV231,
) -> str:
    metrics = artifact.metrics
    return "\n".join(
        (
            "# DTA v2.3.1 Independent Successor Evaluation",
            "",
            f"Measured terminal: `{artifact.measured_result_terminal.value}`",
            "",
            f"- Study relation: `{artifact.study_relation}`",
            f"- Preserved predecessor: `{artifact.predecessor_study_disposition}`",
            f"- Execution count: `{artifact.execution_count}`",
            f"- Cases / runs: `{artifact.case_count}` / `{artifact.run_count}`",
            f"- Baseline novelty recall: `{metrics.baseline_novelty_recall:.3f}`",
            f"- Treatment novelty recall: `{metrics.treatment_novelty_recall:.3f}`",
            f"- Recall improvement: `{metrics.novelty_recall_improvement:.3f}`",
            f"- Conflict-prone treatment recall: `{metrics.conflict_prone_treatment_recall:.3f}`",
            f"- Treatment root localization: `{metrics.treatment_root_localization:.3f}`",
            f"- Treatment broad-domain accuracy: `{metrics.treatment_broad_domain_accuracy:.3f}`",
            f"- Treatment evidence-ref validity: `{metrics.treatment_evidence_ref_validity:.3f}`",
            f"- Treatment false-novel rate: `{metrics.treatment_false_novel_rate:.3f}`",
            f"- Known / No-Incident drops: `{metrics.known_accuracy_drop_cases}` / `{metrics.no_incident_accuracy_drop_cases}`",
            f"- True conflicts converted to novelty: `{metrics.true_conflict_converted_cases}`",
            f"- Provider calls (baseline / treatment): `{metrics.baseline_provider_calls}` / `{metrics.treatment_provider_calls}`",
            f"- Protocol repairs / transport retries: `{metrics.protocol_repairs}` / `{metrics.transport_retries}`",
            f"- Action-authority violations: `{metrics.action_authority_violations}`",
            "",
            "This is an independent successor with new fixed bytes, not a rerun of the consumed blocked study. It called neither Docker nor a live fault.",
            "",
        )
    )


def run_successor_evaluation_once_v231(
    *,
    repository_root: Path,
    cases_path: Path,
    truth_index_path: Path,
    ontology_views_path: Path,
    admission_matrix_path: Path,
    manifest_path: Path,
    independent_review_path: Path,
    output_path: Path,
    output_markdown_path: Path,
    provider_transport: OpenAICompatibleDiscoveryTransportV231,
    observer: Callable[[EvaluationCasePairV231], None] | None = None,
) -> SuccessorFixedEvaluationArtifactV231:
    preflight = build_successor_evaluation_preflight_v231(
        repository_root=repository_root,
        cases_path=cases_path,
        truth_index_path=truth_index_path,
        ontology_views_path=ontology_views_path,
        admission_matrix_path=admission_matrix_path,
        manifest_path=manifest_path,
        independent_review_path=independent_review_path,
        output_path=output_path,
        output_markdown_path=output_markdown_path,
        expected_provider_model=provider_transport.config.model,
    )
    local_root = repository_root / ".local/dta-v231-successor"
    local_root.mkdir(parents=True, exist_ok=True)
    sentinel = local_root / "successor-evaluation.started.json"
    partial = local_root / "successor-evaluation.partial.jsonl"
    with sentinel.open("x", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "status": "STARTED",
                    "planned_execution_count": 1,
                    "study_relation": preflight.study_relation,
                    "cases_sha256": preflight.cases_sha256,
                    "truth_index_sha256": preflight.truth_index_sha256,
                    "truth_shard_set_sha256": preflight.truth_shard_set_sha256,
                    "admission_matrix_sha256": preflight.admission_matrix_sha256,
                    "manifest_sha256": preflight.manifest_sha256,
                    "independent_review_sha256": (
                        preflight.independent_review_sha256
                    ),
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
    cases = load_successor_case_set_v231(cases_path)
    views = load_successor_views_v231(ontology_views_path)
    truth_store = SuccessorLazyTruthStoreV231(truth_index_path)
    pairs: list[EvaluationCasePairV231] = []
    with partial.open("x", encoding="utf-8") as handle:
        for spec in cases.cases:
            pair = run_evaluation_case_pair_v231(
                repository_root=repository_root,
                spec=spec,
                view_spec=views.require(spec.case_id),
                truth_store=cast(LazyTruthStoreV231, truth_store),
                provider_transport=provider_transport,
            )
            pairs.append(pair)
            handle.write(pair.model_dump_json() + "\n")
            handle.flush()
            if observer is not None:
                observer(pair)
    expected_ids = tuple(item.case_id for item in cases.cases)
    if truth_store.loaded_case_ids != expected_ids:
        raise ValueError("successor truth coverage differs after both arms")
    artifact = _build_successor_artifact_v231(
        preflight=preflight,
        pairs=tuple(pairs),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        handle.write(artifact.model_dump_json(indent=2) + "\n")
    with output_markdown_path.open("x", encoding="utf-8") as handle:
        handle.write(render_successor_evaluation_markdown_v231(artifact))
    sentinel.write_text(
        json.dumps(
            {
                "status": "COMPLETE",
                "execution_count": 1,
                "study_relation": artifact.study_relation,
                "artifact_sha256": artifact.artifact_sha256,
                "output_json_sha256": _file_sha256_v231_successor(output_path),
                "output_markdown_sha256": _file_sha256_v231_successor(
                    output_markdown_path
                ),
                "measured_result_terminal": (
                    artifact.measured_result_terminal.value
                ),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return artifact


__all__ = (
    "AdmissionMatrixV231Successor",
    "AdmissionStratumV231Successor",
    "SuccessorCaseSetV231",
    "SuccessorEvaluationManifestV231",
    "SuccessorEvaluationPreflightV231",
    "SuccessorFixedEvaluationArtifactV231",
    "SuccessorLazyTruthStoreV231",
    "SuccessorOntologyViewSetV231",
    "SuccessorPreExecutionReviewV231",
    "SuccessorTruthSetV231",
    "build_admission_matrix_v231_successor",
    "build_successor_evaluation_preflight_v231",
    "load_successor_case_set_v231",
    "load_successor_evaluation_manifest_v231",
    "load_successor_pre_execution_review_v231",
    "load_successor_truth_set_v231",
    "load_successor_views_v231",
    "render_successor_evaluation_markdown_v231",
    "run_successor_evaluation_once_v231",
)
