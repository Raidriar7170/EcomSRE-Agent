"""Fresh evaluation data and deterministic admission contracts for DTA v2.3.2."""

from __future__ import annotations

from enum import Enum
import hashlib
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, StrictBool, StrictInt, model_validator

from ecomsre.dta_v2.v22.memory import (
    LogCategoryV22,
    SignalStrengthV22,
    _log_category,
    _normalize_log,
)
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v23.anomaly_interpretation_v232 import (
    DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232,
)
from ecomsre.dta_v2.v23.conflict_model_v231 import ConflictTypeV231
from ecomsre.dta_v2.v23.conflict_model_v232 import assess_conflict_v232
from ecomsre.dta_v2.v23.discovery_router import NegativeCoverageLedgerV23
from ecomsre.dta_v2.v23.discovery_runtime_v232 import (
    build_conflict_aware_state_total_v232,
)
from ecomsre.dta_v2.v23.evaluation import _build_common_context_v23
from ecomsre.dta_v2.v23.evaluation_successor_v231 import (
    _conflict_proof_v231_successor,
)
from ecomsre.dta_v2.v23.evaluation_v231 import (
    EvaluationCaseSpecV231,
    EvaluationCategoryV231,
    EvaluationOntologyViewSpecV231,
    EvaluationTruthV231,
    _normal_resource_services_v231,
    _residual_graph_v231,
    materialize_evaluation_case_v231,
)
from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyKindV23


class AdmissionStratumV232(str, Enum):
    NOVEL_HIDDEN = "NOVEL_HIDDEN"
    NOVEL_UNREGISTERED = "NOVEL_UNREGISTERED"
    REGISTERED_KNOWN = "REGISTERED_KNOWN"
    NO_INCIDENT = "NO_INCIDENT"
    INSUFFICIENT_IRRECONCILABLE = "INSUFFICIENT_IRRECONCILABLE"


class EvaluationCaseSetV232(DtaModelV22):
    schema_version: Literal["dta-v232.evaluation-case-set.v1"]
    freeze_id: Literal["dta-v232-total-interpretation-freeze-20260826-a"]
    cases: tuple[EvaluationCaseSpecV231, ...] = Field(min_length=24, max_length=24)

    @model_validator(mode="after")
    def require_set(self) -> "EvaluationCaseSetV232":
        ids = tuple(item.case_id for item in self.cases)
        if ids != tuple(f"vx-{ordinal:03d}" for ordinal in range(201, 225)):
            raise ValueError("v2.3.2 evaluation case IDs differ")
        if len({item.source_bytes_sha256 for item in self.cases}) != 24:
            raise ValueError("v2.3.2 observer bytes are not unique")
        services = tuple(
            service for item in self.cases for service in item.candidate_services
        )
        if len(set(services)) != len(services):
            raise ValueError("v2.3.2 opaque service IDs are not unique")
        return self

    def require(self, case_id: str) -> EvaluationCaseSpecV231:
        item = next((value for value in self.cases if value.case_id == case_id), None)
        if item is None:
            raise ValueError("v2.3.2 evaluation case is absent")
        return item


class EvaluationTruthRecordV232(DtaModelV22):
    evaluator_truth: EvaluationTruthV231
    admission_stratum: AdmissionStratumV232
    expected_known_mechanism: MechanismV22 | None
    counterfactual_target_role: Literal["TARGET_LOW", "TARGET_HIGH"] | None

    @model_validator(mode="after")
    def require_projection(self) -> "EvaluationTruthRecordV232":
        expected = {
            EvaluationCategoryV231.NOVEL_HIDDEN: AdmissionStratumV232.NOVEL_HIDDEN,
            EvaluationCategoryV231.NOVEL_UNREGISTERED: AdmissionStratumV232.NOVEL_UNREGISTERED,
            EvaluationCategoryV231.REGISTERED_KNOWN: AdmissionStratumV232.REGISTERED_KNOWN,
            EvaluationCategoryV231.NO_INCIDENT: AdmissionStratumV232.NO_INCIDENT,
            EvaluationCategoryV231.INSUFFICIENT_CONFLICT: (
                AdmissionStratumV232.INSUFFICIENT_IRRECONCILABLE
            ),
        }[self.evaluator_truth.category]
        if self.admission_stratum is not expected:
            raise ValueError("v2.3.2 truth stratum differs")
        if (
            self.admission_stratum is AdmissionStratumV232.REGISTERED_KNOWN
        ) != (self.expected_known_mechanism is not None):
            raise ValueError("v2.3.2 known mechanism projection differs")
        if (self.evaluator_truth.counterfactual_pair_id is None) != (
            self.counterfactual_target_role is None
        ):
            raise ValueError("v2.3.2 counterfactual role differs")
        return self


class EvaluationTruthSetV232(DtaModelV22):
    schema_version: Literal["dta-v232.evaluation-truth-set.v1"]
    truths: tuple[EvaluationTruthRecordV232, ...] = Field(
        min_length=24,
        max_length=24,
    )

    @model_validator(mode="after")
    def require_set(self) -> "EvaluationTruthSetV232":
        ids = tuple(item.evaluator_truth.case_id for item in self.truths)
        if ids != tuple(f"vx-{ordinal:03d}" for ordinal in range(201, 225)):
            raise ValueError("v2.3.2 truth IDs differ")
        expected = {
            AdmissionStratumV232.NOVEL_HIDDEN: 10,
            AdmissionStratumV232.NOVEL_UNREGISTERED: 4,
            AdmissionStratumV232.REGISTERED_KNOWN: 4,
            AdmissionStratumV232.NO_INCIDENT: 3,
            AdmissionStratumV232.INSUFFICIENT_IRRECONCILABLE: 3,
        }
        counts = {
            stratum: sum(item.admission_stratum is stratum for item in self.truths)
            for stratum in AdmissionStratumV232
        }
        if counts != expected:
            raise ValueError("v2.3.2 truth composition differs")
        return self

    def require(self, case_id: str) -> EvaluationTruthRecordV232:
        item = next(
            (
                value
                for value in self.truths
                if value.evaluator_truth.case_id == case_id
            ),
            None,
        )
        if item is None:
            raise ValueError("v2.3.2 truth is absent")
        return item


class EvaluationTruthShardBindingV232(DtaModelV22):
    case_id: str
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationTruthIndexV232(DtaModelV22):
    schema_version: Literal["dta-v232.evaluation-truth-index.v1"]
    shards: tuple[EvaluationTruthShardBindingV232, ...] = Field(
        min_length=24,
        max_length=24,
    )
    index_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_index(self) -> "EvaluationTruthIndexV232":
        ids = tuple(item.case_id for item in self.shards)
        if ids != tuple(f"vx-{ordinal:03d}" for ordinal in range(201, 225)):
            raise ValueError("v2.3.2 truth index IDs differ")
        if tuple(item.path for item in self.shards) != tuple(
            f"truth/{case_id}.json" for case_id in ids
        ):
            raise ValueError("v2.3.2 truth shard paths differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"index_sha256"})
        )
        if self.index_sha256 != expected:
            raise ValueError("v2.3.2 truth index digest differs")
        return self

    def require(self, case_id: str) -> EvaluationTruthShardBindingV232:
        item = next((value for value in self.shards if value.case_id == case_id), None)
        if item is None:
            raise ValueError("v2.3.2 truth binding is absent")
        return item


class EvaluationTruthShardV232(DtaModelV22):
    schema_version: Literal["dta-v232.evaluation-truth-shard.v1"]
    record: EvaluationTruthRecordV232


class EvaluationOntologyViewSetV232(DtaModelV22):
    schema_version: Literal["dta-v232.ontology-view-set.v1"]
    views: tuple[EvaluationOntologyViewSpecV231, ...] = Field(
        min_length=24,
        max_length=24,
    )

    @model_validator(mode="after")
    def require_set(self) -> "EvaluationOntologyViewSetV232":
        ids = tuple(item.case_id for item in self.views)
        if ids != tuple(f"vx-{ordinal:03d}" for ordinal in range(201, 225)):
            raise ValueError("v2.3.2 ontology view IDs differ")
        return self

    def require(self, case_id: str) -> EvaluationOntologyViewSpecV231:
        item = next((value for value in self.views if value.case_id == case_id), None)
        if item is None:
            raise ValueError("v2.3.2 ontology view is absent")
        return item


class EvaluationStratumEntryV232(DtaModelV22):
    name: AdmissionStratumV232
    case_ids: tuple[str, ...]


class EvaluationStrataV232(DtaModelV22):
    schema_version: Literal["dta-v232.evaluation-strata.v1"]
    strata: tuple[EvaluationStratumEntryV232, ...]

    @model_validator(mode="after")
    def require_strata(self) -> "EvaluationStrataV232":
        names = tuple(item.name for item in self.strata)
        if names != tuple(sorted(AdmissionStratumV232, key=lambda item: item.value)):
            raise ValueError("v2.3.2 strata are not canonical")
        case_ids = tuple(case for item in self.strata for case in item.case_ids)
        if set(case_ids) != {f"vx-{ordinal:03d}" for ordinal in range(201, 225)}:
            raise ValueError("v2.3.2 strata do not cover all cases")
        return self


class AdmissionMatrixEntryV232(DtaModelV22):
    schema_version: Literal["dta-v232.admission-entry.v1"]
    case_id: str
    stratum: AdmissionStratumV232
    case_bytes_sha256: str
    active_view_sha256: str
    known_terminal_count: StrictInt = Field(ge=0)
    known_terminal_mechanisms: tuple[MechanismV22, ...]
    known_terminal_roots: tuple[str, ...]
    no_incident_admissible: StrictBool
    strong_residual_anomaly_count: StrictInt = Field(ge=0)
    residual_anomaly_kinds: tuple[GenericAnomalyKindV23, ...]
    interpreted_anomaly_count: StrictInt = Field(ge=0)
    runtime_coverage_complete: StrictBool
    metrics_coverage_complete: StrictBool
    initial_conflict_type: ConflictTypeV231
    intended_state_reachable: StrictBool
    log_error_cluster_present: StrictBool
    counterfactual_pair_id: str | None
    counterfactual_target_role: Literal["TARGET_LOW", "TARGET_HIGH"] | None
    contract_pass: StrictBool
    entry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_entry(self) -> "AdmissionMatrixEntryV232":
        for values in (
            self.known_terminal_mechanisms,
            self.known_terminal_roots,
            self.residual_anomaly_kinds,
        ):
            if values != tuple(sorted(set(values), key=str)):
                raise ValueError("v2.3.2 admission entry values are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"entry_sha256"})
        )
        if self.entry_sha256 != expected:
            raise ValueError("v2.3.2 admission entry digest differs")
        return self


class CounterfactualPairAdmissionV232(DtaModelV22):
    pair_id: str
    case_ids: tuple[str, str]
    stratum: AdmissionStratumV232
    target_roles: tuple[Literal["TARGET_HIGH", "TARGET_LOW"], ...]
    stratum_preserved: Literal[True]


class AdmissionMatrixV232(DtaModelV22):
    schema_version: Literal["dta-v232.admission-matrix.v1"]
    case_count: Literal[24]
    provider_calls: Literal[0]
    registry_sha256: str
    entries: tuple[AdmissionMatrixEntryV232, ...] = Field(
        min_length=24,
        max_length=24,
    )
    counterfactual_pairs: tuple[CounterfactualPairAdmissionV232, ...] = Field(
        min_length=4,
        max_length=4,
    )
    log_error_cluster_coverage: dict[str, StrictInt]
    status: Literal["DTA_V232_SUCCESSOR_EVALUATION_DATA_PASS"]
    matrix_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_matrix(self) -> "AdmissionMatrixV232":
        ids = tuple(item.case_id for item in self.entries)
        if ids != tuple(f"vx-{ordinal:03d}" for ordinal in range(201, 225)):
            raise ValueError("v2.3.2 admission IDs differ")
        if not all(item.contract_pass for item in self.entries):
            raise ValueError("v2.3.2 admission matrix contains a failed case")
        required_log_coverage = {
            "novelty": 2,
            "registered_known": 1,
            "irreconcilable": 1,
        }
        if any(
            self.log_error_cluster_coverage.get(key, 0) < minimum
            for key, minimum in required_log_coverage.items()
        ):
            raise ValueError("v2.3.2 LOG_ERROR_CLUSTER coverage is insufficient")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"matrix_sha256"})
        )
        if self.matrix_sha256 != expected:
            raise ValueError("v2.3.2 admission matrix digest differs")
        return self


def load_evaluation_cases_v232(path: Path) -> EvaluationCaseSetV232:
    return EvaluationCaseSetV232.model_validate_json(path.read_bytes())


def load_evaluation_truth_index_v232(path: Path) -> EvaluationTruthIndexV232:
    return EvaluationTruthIndexV232.model_validate_json(path.read_bytes())


def load_evaluation_truth_shard_v232(
    *,
    index_path: Path,
    binding: EvaluationTruthShardBindingV232,
) -> EvaluationTruthShardV232:
    raw = (index_path.parent / binding.path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding.sha256:
        raise ValueError("v2.3.2 truth shard bytes differ")
    shard = EvaluationTruthShardV232.model_validate_json(raw)
    if shard.record.evaluator_truth.case_id != binding.case_id:
        raise ValueError("v2.3.2 truth shard ID differs")
    return shard


def load_evaluation_truths_v232(path: Path) -> EvaluationTruthSetV232:
    index = load_evaluation_truth_index_v232(path)
    return EvaluationTruthSetV232(
        schema_version="dta-v232.evaluation-truth-set.v1",
        truths=tuple(
            load_evaluation_truth_shard_v232(
                index_path=path,
                binding=binding,
            ).record
            for binding in index.shards
        ),
    )


def load_evaluation_views_v232(path: Path) -> EvaluationOntologyViewSetV232:
    return EvaluationOntologyViewSetV232.model_validate_json(path.read_bytes())


def load_evaluation_strata_v232(path: Path) -> EvaluationStrataV232:
    return EvaluationStrataV232.model_validate_json(path.read_bytes())


def _entry_v232(
    *,
    repository_root: Path,
    spec: EvaluationCaseSpecV231,
    truth: EvaluationTruthRecordV232,
    view: EvaluationOntologyViewSpecV231,
) -> AdmissionMatrixEntryV232:
    case = materialize_evaluation_case_v231(
        repository_root=repository_root,
        spec=spec,
    )
    context = _build_common_context_v23(
        case=case,
        hidden_mechanism=view.hidden_mechanism,
    )
    admission = context.admission
    memory = context.memory
    graph = _residual_graph_v231(context=context, memory=memory)
    assessment = None
    if truth.admission_stratum is AdmissionStratumV232.INSUFFICIENT_IRRECONCILABLE:
        admission, _old_assessment, memory, _proof_actions = (
            _conflict_proof_v231_successor(context=context)
        )
        graph = _residual_graph_v231(context=context, memory=memory)
        assessment = assess_conflict_v232(
            graph=graph,
            memory=memory,
            topology_edges=context.case.topology_edges,
            legal_sources=(),
            remaining_reads=0,
            normal_resource_services=_normal_resource_services_v231(
                graph=graph,
                memory=memory,
            ),
        )
    if assessment is None:
        state = build_conflict_aware_state_total_v232(
            graph=graph,
            memory=memory,
            catalog=context.catalog,
            topology_edges=context.case.topology_edges,
            no_incident_admissible=admission.no_incident_admissible,
            negative_coverage=NegativeCoverageLedgerV23.empty(),
            discovery_reads_used=0,
            remaining_weighted_budget=3.0,
            conflict_resolution_read_used=False,
            normal_resource_services=_normal_resource_services_v231(
                graph=graph,
                memory=memory,
            ),
            excluded_action_ids=tuple(
                sorted(item.action_id for item in context.outcomes)
            ),
        )
        assessment = state.conflict_assessment
    coverage = {item.source: item for item in graph.source_coverage}
    candidates = set(graph.candidate_services)
    runtime_complete = (
        set(coverage[EvidenceSourceV22.RUNTIME].covered_services) == candidates
    )
    metrics_complete = (
        set(coverage[EvidenceSourceV22.METRICS].covered_services) == candidates
    )
    residual_ids = set(graph.residual_anomaly_ids)
    residual = tuple(
        item for item in graph.generic_anomalies if item.anomaly_id in residual_ids
    )
    strong_count = sum(
        item.strength is SignalStrengthV22.STRONG for item in residual
    )
    interpretations = tuple(
        DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232.interpret(
            anomaly=item,
            memory=memory,
        )
        for item in residual
    )
    known_count = len(admission.admitted_diagnoses)
    mechanisms = tuple(
        sorted(
            (item.mechanism for item in admission.admitted_diagnoses),
            key=lambda item: item.value,
        )
    )
    roots = tuple(sorted(item.root_service for item in admission.admitted_diagnoses))
    expected_root = truth.evaluator_truth.expected_root_service
    stratum = truth.admission_stratum
    if stratum in {
        AdmissionStratumV232.NOVEL_HIDDEN,
        AdmissionStratumV232.NOVEL_UNREGISTERED,
    }:
        conflict_reachable = (
            not truth.evaluator_truth.conflict_prone_novelty
            or assessment.conflict_type is ConflictTypeV231.RESOLVABLE_CONFLICT
        )
        contract_pass = (
            known_count == 0
            and not admission.no_incident_admissible
            and strong_count > 0
            and runtime_complete
            and metrics_complete
            and conflict_reachable
        )
    elif stratum is AdmissionStratumV232.REGISTERED_KNOWN:
        contract_pass = (
            known_count == 1
            and mechanisms == (truth.expected_known_mechanism,)
            and roots == (expected_root,)
            and not admission.no_incident_admissible
        )
    elif stratum is AdmissionStratumV232.NO_INCIDENT:
        contract_pass = known_count == 0 and admission.no_incident_admissible
    else:
        contract_pass = (
            known_count == 0
            and not admission.no_incident_admissible
            and assessment.conflict_type is ConflictTypeV231.IRRECONCILABLE_CONFLICT
        )
    kinds = tuple(sorted({item.kind for item in residual}, key=lambda item: item.value))
    payload: dict[str, Any] = {
        "schema_version": "dta-v232.admission-entry.v1",
        "case_id": spec.case_id,
        "stratum": stratum,
        "case_bytes_sha256": spec.source_bytes_sha256,
        "active_view_sha256": context.view.view_sha256,
        "known_terminal_count": known_count,
        "known_terminal_mechanisms": mechanisms,
        "known_terminal_roots": roots,
        "no_incident_admissible": admission.no_incident_admissible,
        "strong_residual_anomaly_count": strong_count,
        "residual_anomaly_kinds": kinds,
        "interpreted_anomaly_count": len(interpretations),
        "runtime_coverage_complete": runtime_complete,
        "metrics_coverage_complete": metrics_complete,
        "initial_conflict_type": assessment.conflict_type,
        "intended_state_reachable": contract_pass,
        "log_error_cluster_present": any(
            _log_category(_normalize_log(item.message)) is not LogCategoryV22.OTHER
            for item in context.case.capture.logs
        ),
        "counterfactual_pair_id": truth.evaluator_truth.counterfactual_pair_id,
        "counterfactual_target_role": truth.counterfactual_target_role,
        "contract_pass": contract_pass,
    }
    draft = AdmissionMatrixEntryV232.model_construct(
        **payload,
        entry_sha256="0" * 64,
    )
    return AdmissionMatrixEntryV232.model_validate(
        {
            **payload,
            "entry_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"entry_sha256"})
            ),
        }
    )


def build_admission_matrix_v232(
    *,
    repository_root: Path,
    cases: EvaluationCaseSetV232,
    truths: EvaluationTruthSetV232,
    views: EvaluationOntologyViewSetV232,
) -> AdmissionMatrixV232:
    entries = tuple(
        _entry_v232(
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
    pairs: list[CounterfactualPairAdmissionV232] = []
    for pair_id in pair_ids:
        pair_entries = tuple(
            item for item in entries if item.counterfactual_pair_id == pair_id
        )
        strata = {item.stratum for item in pair_entries}
        roles = tuple(
            sorted(
                cast(
                    Literal["TARGET_LOW", "TARGET_HIGH"],
                    item.counterfactual_target_role,
                )
                for item in pair_entries
                if item.counterfactual_target_role is not None
            )
        )
        if len(pair_entries) != 2 or len(strata) != 1 or roles != (
            "TARGET_HIGH",
            "TARGET_LOW",
        ):
            raise ValueError("v2.3.2 counterfactual pair differs")
        pairs.append(
            CounterfactualPairAdmissionV232(
                pair_id=cast(str, pair_id),
                case_ids=cast(
                    tuple[str, str],
                    tuple(sorted(item.case_id for item in pair_entries)),
                ),
                stratum=next(iter(strata)),
                target_roles=roles,
                stratum_preserved=True,
            )
        )
    novelty_strata = {
        AdmissionStratumV232.NOVEL_HIDDEN,
        AdmissionStratumV232.NOVEL_UNREGISTERED,
    }
    log_coverage = {
        "novelty": sum(
            item.log_error_cluster_present and item.stratum in novelty_strata
            for item in entries
        ),
        "registered_known": sum(
            item.log_error_cluster_present
            and item.stratum is AdmissionStratumV232.REGISTERED_KNOWN
            for item in entries
        ),
        "irreconcilable": sum(
            item.log_error_cluster_present
            and item.stratum is AdmissionStratumV232.INSUFFICIENT_IRRECONCILABLE
            for item in entries
        ),
    }
    payload: dict[str, Any] = {
        "schema_version": "dta-v232.admission-matrix.v1",
        "case_count": 24,
        "provider_calls": 0,
        "registry_sha256": (
            DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232.registry_sha256
        ),
        "entries": entries,
        "counterfactual_pairs": tuple(pairs),
        "log_error_cluster_coverage": log_coverage,
        "status": "DTA_V232_SUCCESSOR_EVALUATION_DATA_PASS",
    }
    draft = AdmissionMatrixV232.model_construct(**payload, matrix_sha256="0" * 64)
    return AdmissionMatrixV232.model_validate(
        {
            **payload,
            "matrix_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"matrix_sha256"})
            ),
        }
    )


__all__ = (
    "AdmissionMatrixV232",
    "AdmissionStratumV232",
    "EvaluationCaseSetV232",
    "EvaluationOntologyViewSetV232",
    "EvaluationStrataV232",
    "EvaluationTruthSetV232",
    "build_admission_matrix_v232",
    "load_evaluation_cases_v232",
    "load_evaluation_strata_v232",
    "load_evaluation_truth_index_v232",
    "load_evaluation_truth_shard_v232",
    "load_evaluation_truths_v232",
    "load_evaluation_views_v232",
)
