"""Frozen 28-case data and admission contracts for DTA v2.3.3."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool, model_validator

from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.evaluation_v231 import (
    EvaluationCaseSpecV231,
    EvaluationOntologyViewSpecV231,
)


class EvaluationStratumV233(str, Enum):
    HIDDEN_CONFIGURATION = "HIDDEN_CONFIGURATION"
    HIDDEN_DEPENDENCY_LATENCY = "HIDDEN_DEPENDENCY_LATENCY"
    HIDDEN_RUNTIME = "HIDDEN_RUNTIME"
    HIDDEN_CPU = "HIDDEN_CPU"
    HIDDEN_MEMORY = "HIDDEN_MEMORY"
    UNREGISTERED_CONCURRENCY = "UNREGISTERED_CONCURRENCY"
    UNREGISTERED_QUEUE_BACKLOG = "UNREGISTERED_QUEUE_BACKLOG"
    UNREGISTERED_NETWORK_EXTERNAL = "UNREGISTERED_NETWORK_EXTERNAL"
    REGISTERED_KNOWN = "REGISTERED_KNOWN"
    NO_INCIDENT = "NO_INCIDENT"
    IRRECONCILABLE_CONTROL = "IRRECONCILABLE_CONTROL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class EvaluationClassV233(str, Enum):
    NOVELTY = "NOVELTY"
    REGISTERED_KNOWN = "REGISTERED_KNOWN"
    NO_INCIDENT = "NO_INCIDENT"
    IRRECONCILABLE_CONTROL = "IRRECONCILABLE_CONTROL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


_NOVELTY_STRATA_V233 = frozenset(
    {
        EvaluationStratumV233.HIDDEN_CONFIGURATION,
        EvaluationStratumV233.HIDDEN_DEPENDENCY_LATENCY,
        EvaluationStratumV233.HIDDEN_RUNTIME,
        EvaluationStratumV233.HIDDEN_CPU,
        EvaluationStratumV233.HIDDEN_MEMORY,
        EvaluationStratumV233.UNREGISTERED_CONCURRENCY,
        EvaluationStratumV233.UNREGISTERED_QUEUE_BACKLOG,
        EvaluationStratumV233.UNREGISTERED_NETWORK_EXTERNAL,
    }
)


class EvaluationCaseSetV233(DtaModelV22):
    schema_version: Literal["dta-v233.evaluation-case-set.v1"]
    freeze_id: Literal["dta-v233-domain-witness-freeze-20260826-a"]
    cases: tuple[EvaluationCaseSpecV231, ...] = Field(min_length=28, max_length=28)

    @model_validator(mode="after")
    def require_set(self) -> "EvaluationCaseSetV233":
        ids = tuple(item.case_id for item in self.cases)
        if ids != tuple(f"vx-{ordinal:03d}" for ordinal in range(301, 329)):
            raise ValueError("v2.3.3 evaluation case IDs differ")
        if len({item.source_bytes_sha256 for item in self.cases}) != 28:
            raise ValueError("v2.3.3 observer bytes are not unique")
        services = tuple(
            service for item in self.cases for service in item.candidate_services
        )
        if len(set(services)) != len(services):
            raise ValueError("v2.3.3 opaque service IDs are not unique")
        return self

    def require(self, case_id: str) -> EvaluationCaseSpecV231:
        item = next((value for value in self.cases if value.case_id == case_id), None)
        if item is None:
            raise ValueError("v2.3.3 evaluation case is absent")
        return item


class EvaluationTruthV233(DtaModelV22):
    case_id: str
    stratum: EvaluationStratumV233
    evaluation_class: EvaluationClassV233
    expected_terminal: str
    expected_root_service: str | None
    expected_broad_domain: ProvisionalFaultDomainV23 | None
    expected_known_mechanism: MechanismV22 | None
    hidden_mechanism: MechanismV22 | None
    counterfactual_pair_id: str | None
    counterfactual_target_role: Literal["TARGET_LOW", "TARGET_HIGH"] | None

    @model_validator(mode="after")
    def require_truth(self) -> "EvaluationTruthV233":
        expected_class = (
            EvaluationClassV233.NOVELTY
            if self.stratum in _NOVELTY_STRATA_V233
            else EvaluationClassV233(self.stratum.value)
        )
        if self.evaluation_class is not expected_class:
            raise ValueError("v2.3.3 truth class differs from stratum")
        if self.evaluation_class is EvaluationClassV233.NOVELTY:
            if self.expected_root_service is None or self.expected_broad_domain is None:
                raise ValueError("v2.3.3 novelty truth lacks root or domain")
            if self.expected_known_mechanism is not None:
                raise ValueError("v2.3.3 novelty truth carries a known mechanism")
        elif self.evaluation_class is EvaluationClassV233.REGISTERED_KNOWN:
            if (
                self.expected_root_service is None
                or self.expected_broad_domain is None
                or self.expected_known_mechanism is None
            ):
                raise ValueError("v2.3.3 known truth is incomplete")
        elif any(
            value is not None
            for value in (
                self.expected_root_service,
                self.expected_broad_domain,
                self.expected_known_mechanism,
                self.hidden_mechanism,
            )
        ):
            raise ValueError("v2.3.3 non-incident truth carries causal labels")
        if (self.counterfactual_pair_id is None) != (
            self.counterfactual_target_role is None
        ):
            raise ValueError("v2.3.3 counterfactual binding differs")
        return self


class EvaluationTruthSetV233(DtaModelV22):
    schema_version: Literal["dta-v233.evaluation-truth-set.v1"]
    truths: tuple[EvaluationTruthV233, ...] = Field(min_length=28, max_length=28)
    truth_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_set(self) -> "EvaluationTruthSetV233":
        ids = tuple(item.case_id for item in self.truths)
        if ids != tuple(f"vx-{ordinal:03d}" for ordinal in range(301, 329)):
            raise ValueError("v2.3.3 truth IDs differ")
        expected = {
            EvaluationClassV233.NOVELTY: 16,
            EvaluationClassV233.REGISTERED_KNOWN: 4,
            EvaluationClassV233.NO_INCIDENT: 3,
            EvaluationClassV233.IRRECONCILABLE_CONTROL: 4,
            EvaluationClassV233.INSUFFICIENT_EVIDENCE: 1,
        }
        actual = {
            category: sum(
                item.evaluation_class is category for item in self.truths
            )
            for category in EvaluationClassV233
        }
        if actual != expected:
            raise ValueError("v2.3.3 truth composition differs")
        stratum_counts = {
            stratum: sum(item.stratum is stratum for item in self.truths)
            for stratum in _NOVELTY_STRATA_V233
        }
        if set(stratum_counts.values()) != {2}:
            raise ValueError("v2.3.3 novelty strata are not paired")
        expected_sha = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"truth_sha256"})
        )
        if self.truth_sha256 != expected_sha:
            raise ValueError("v2.3.3 truth digest differs")
        return self

    def require(self, case_id: str) -> EvaluationTruthV233:
        item = next((value for value in self.truths if value.case_id == case_id), None)
        if item is None:
            raise ValueError("v2.3.3 truth is absent")
        return item


class EvaluationOntologyViewSetV233(DtaModelV22):
    schema_version: Literal["dta-v233.ontology-view-set.v1"]
    views: tuple[EvaluationOntologyViewSpecV231, ...] = Field(
        min_length=28,
        max_length=28,
    )

    @model_validator(mode="after")
    def require_set(self) -> "EvaluationOntologyViewSetV233":
        ids = tuple(item.case_id for item in self.views)
        if ids != tuple(f"vx-{ordinal:03d}" for ordinal in range(301, 329)):
            raise ValueError("v2.3.3 ontology view IDs differ")
        return self

    def require(self, case_id: str) -> EvaluationOntologyViewSpecV231:
        item = next((value for value in self.views if value.case_id == case_id), None)
        if item is None:
            raise ValueError("v2.3.3 ontology view is absent")
        return item


class EvaluationStratumEntryV233(DtaModelV22):
    name: EvaluationStratumV233
    case_ids: tuple[str, ...]


class EvaluationStrataV233(DtaModelV22):
    schema_version: Literal["dta-v233.evaluation-strata.v1"]
    strata: tuple[EvaluationStratumEntryV233, ...]

    @model_validator(mode="after")
    def require_strata(self) -> "EvaluationStrataV233":
        names = tuple(item.name for item in self.strata)
        if names != tuple(
            sorted(EvaluationStratumV233, key=lambda item: item.value)
        ):
            raise ValueError("v2.3.3 strata are not canonical")
        ids = tuple(case for item in self.strata for case in item.case_ids)
        if set(ids) != {f"vx-{ordinal:03d}" for ordinal in range(301, 329)}:
            raise ValueError("v2.3.3 strata do not cover the fixed set")
        if len(ids) != len(set(ids)):
            raise ValueError("v2.3.3 strata overlap")
        return self


class AdmissionMatrixEntryV233(DtaModelV22):
    case_id: str
    stratum: EvaluationStratumV233
    known_terminal_count: int
    no_incident: StrictBool
    incident_terminal_count: int
    domain_candidate_exists: StrictBool
    strong_witness_exists: StrictBool
    strong_report_support: StrictBool
    false_irreconcilable_witness: StrictBool
    admission_pass: StrictBool


class AdmissionMatrixV233(DtaModelV22):
    schema_version: Literal["dta-v233.admission-matrix.v1"]
    case_count: Literal[28]
    novelty_passed: Literal[16]
    registered_known_passed: Literal[4]
    no_incident_passed: Literal[3]
    irreconcilable_passed: Literal[4]
    insufficient_passed: Literal[1]
    novelty_multi_domain_candidates: int = Field(ge=8, le=16)
    novelty_report_eligible: int = Field(ge=12, le=16)
    log_error_cluster_cases: int = Field(ge=6, le=28)
    counterfactual_target_swaps: int = Field(ge=4, le=28)
    entries: tuple[AdmissionMatrixEntryV233, ...] = Field(
        min_length=28,
        max_length=28,
    )
    terminal: Literal["DTA_V233_EVALUATION_DATA_PASS"]
    matrix_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_matrix(self) -> "AdmissionMatrixV233":
        ids = tuple(item.case_id for item in self.entries)
        if ids != tuple(f"vx-{ordinal:03d}" for ordinal in range(301, 329)):
            raise ValueError("v2.3.3 admission cases differ")
        if not all(item.admission_pass for item in self.entries):
            raise ValueError("v2.3.3 admission matrix carries a failed case")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"matrix_sha256"})
        )
        if self.matrix_sha256 != expected:
            raise ValueError("v2.3.3 admission digest differs")
        return self


def load_evaluation_cases_v233(path: Path) -> EvaluationCaseSetV233:
    return EvaluationCaseSetV233.model_validate_json(path.read_bytes())


def load_evaluation_truth_v233(path: Path) -> EvaluationTruthSetV233:
    return EvaluationTruthSetV233.model_validate_json(path.read_bytes())


def load_evaluation_views_v233(path: Path) -> EvaluationOntologyViewSetV233:
    return EvaluationOntologyViewSetV233.model_validate_json(path.read_bytes())


def load_evaluation_strata_v233(path: Path) -> EvaluationStrataV233:
    return EvaluationStrataV233.model_validate_json(path.read_bytes())


def load_admission_matrix_v233(path: Path) -> AdmissionMatrixV233:
    return AdmissionMatrixV233.model_validate_json(path.read_bytes())


__all__ = (
    "AdmissionMatrixEntryV233",
    "AdmissionMatrixV233",
    "EvaluationCaseSetV233",
    "EvaluationClassV233",
    "EvaluationOntologyViewSetV233",
    "EvaluationStrataV233",
    "EvaluationStratumV233",
    "EvaluationTruthSetV233",
    "EvaluationTruthV233",
    "load_admission_matrix_v233",
    "load_evaluation_cases_v233",
    "load_evaluation_strata_v233",
    "load_evaluation_truth_v233",
    "load_evaluation_views_v233",
)
