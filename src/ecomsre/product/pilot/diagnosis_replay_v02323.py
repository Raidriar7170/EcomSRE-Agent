"""Forensic-only Product v0.2.3.2.3 replay-input and no-write Diagnosis checks."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Literal, Mapping

from pydantic import ConfigDict, Field, model_validator

from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
from ecomsre.product.baselines import EnvironmentBaselineV1
from ecomsre.product.connectors.base import (
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.contracts import (
    EnvironmentRecordV1,
    ProductModelV1,
    ServiceIdentityMapV1,
)
from ecomsre.product.environment.capabilities import (
    EnvironmentCapabilityMatrixV1,
    SourceCapabilityStatusV1,
)
from ecomsre.product.incidents.contracts import (
    DiagnosisResultV1,
    EvidenceBundleV1,
    EvidenceObjectV1,
    IncidentRecordV1,
)
from ecomsre.product.incidents.diagnosis_pipeline_v02322 import (
    DiagnosisBridgeArtifactV02322,
    DiagnosisPersistencePlanV02322,
    DiagnosisPipelineStageV02322,
    DiagnosisPipelineV02322,
)
from ecomsre.product.incidents.diagnosis_stage_journal_v02322 import (
    DiagnosisStageEventV02322,
    DiagnosisStageStatusV02322,
)
from ecomsre.product.incidents.evidence_binding_v0232 import (
    CapabilityLimitationCandidateV0232,
    DiagnosisDecisionTraceV0232,
    DiagnosisEvidenceIndexV0232,
)
from ecomsre.product.incidents.read_backend import ProductReadAcquisitionV1
from ecomsre.product.incidents.repository import (
    _build_limitation_bindings,
    _source_disposition,
    _specialized_binding_refs,
)
from ecomsre.product.storage.migrations import MIGRATIONS
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


GOAL_VERSION_V02323 = (
    "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
)
REPLAY_INPUT_PASS_V02323 = "ECOMSRE_PRODUCT_V02323_REPLAY_INPUT_PASS"
DIAGNOSIS_FORENSICS_PASS_V02323 = "ECOMSRE_PRODUCT_V02323_DIAGNOSIS_FORENSICS_PASS"
ROOT_CAUSE_DISPOSITION_FROZEN_V02323 = (
    "ECOMSRE_PRODUCT_V02323_ROOT_CAUSE_DISPOSITION_FROZEN"
)
DIAGNOSIS_PIPELINE_REPLAY_PASS_V02323 = (
    "ECOMSRE_PRODUCT_V02323_DIAGNOSIS_PIPELINE_REPLAY_PASS"
)
ORIGINAL_ROOT_CAUSE_UNPROVEN_V02323 = (
    "ECOMSRE_PRODUCT_V02323_ORIGINAL_ROOT_CAUSE_UNPROVEN"
)
REPLAY_INPUT_BLOCKER_V02323 = "BLOCKED_ECOMSRE_PRODUCT_V02323_REPLAY_INPUT"
ROOT_CAUSE_BLOCKER_V02323 = "BLOCKED_ECOMSRE_PRODUCT_V02323_ROOT_CAUSE_DISPOSITION"
DIAGNOSIS_REPLAY_BLOCKER_V02323 = "BLOCKED_ECOMSRE_PRODUCT_V02323_DIAGNOSIS_REPLAY"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_EXACT_ACQUISITION_FIELDS = (
    "capability_observations",
    "connector_result_payloads",
    "limitation_candidates",
    "memory_outcomes",
    "p01_binding",
    "raw_read_outcomes",
    "runtime_snapshot_binding",
    "source_query_windows",
)


class DiagnosisReplayContractErrorV02323(RuntimeError):
    """A replay-input or no-write-forensics contract failed closed."""


class DiagnosisReplayClassificationV02323(str, Enum):
    EXACT_FROZEN_ACQUISITION_REPLAY = "EXACT_FROZEN_ACQUISITION_REPLAY"
    STRUCTURAL_CONTRACT_REPLAY = "STRUCTURAL_CONTRACT_REPLAY"


class FrozenIncidentReplayInputV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.frozen-incident-replay-input.v02323"]
    goal_version: Literal[
        "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
    ]
    terminal: Literal["ECOMSRE_PRODUCT_V02323_REPLAY_INPUT_PASS"]
    replay_id: str = Field(pattern=r"^replay-[0-9a-f]{24}$")
    replay_classification: DiagnosisReplayClassificationV02323
    reconstruction_disposition: Literal["PRISTINE_BASE_DELTA_RECONSTRUCTION"]
    reconstruction_disposition_sha256: str = Field(pattern=_SHA256_PATTERN)
    reconstruction_sha256: str = Field(pattern=_SHA256_PATTERN)
    schema8_projection_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    formal_incident_sha256: str = Field(pattern=_SHA256_PATTERN)
    original_failed_job_id: str = Field(pattern=r"^job-[0-9a-f]{24}$")
    original_failed_job_status: Literal["FAILED"]
    original_failed_job_safe_error_code: Literal["INTERNAL_CONTRACT_FAILURE"]
    exact_acquisition_available: bool
    missing_exact_acquisition_fields: tuple[str, ...]
    structural_input_sha256_by_kind: dict[str, str]
    evaluator_truth_field_count: Literal[0]
    historical_acquisition_authority: Literal["NONE"]
    replay_authority: Literal["STRUCTURAL_ONLY"]
    measured_nofault_authority: Literal["NONE"]
    knowledge_loop_authority: Literal["NONE"]
    provider_agent_runbook_docker_calls: Literal[0]
    replay_input_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def replay_input_is_bounded_and_sealed(self) -> FrozenIncidentReplayInputV02323:
        if (
            self.replay_classification
            is not DiagnosisReplayClassificationV02323.STRUCTURAL_CONTRACT_REPLAY
            or self.exact_acquisition_available
            or self.missing_exact_acquisition_fields != _EXACT_ACQUISITION_FIELDS
            or tuple(self.structural_input_sha256_by_kind)
            != tuple(sorted(self.structural_input_sha256_by_kind))
            or any(
                not isinstance(value, str)
                or len(value) != 64
                or set(value) - set("0123456789abcdef")
                for value in self.structural_input_sha256_by_kind.values()
            )
        ):
            raise ValueError("replay-input classification differs")
        body = self.model_dump(mode="json", exclude={"replay_input_sha256"})
        if self.replay_input_sha256 != semantic_sha256_v22(body):
            raise ValueError("replay-input seal differs")
        return self


class ReplayCloneEvidenceV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.replay-clone-evidence.v02323"]
    replay_id: str = Field(pattern=r"^replay-[0-9a-f]{24}$")
    reconstruction_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_locator: str
    replay_clone_locator: str
    source_schema_version: Literal[8]
    replay_schema_version: Literal[9]
    source_database_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    replay_database_before_migration_sha256: str = Field(pattern=_SHA256_PATTERN)
    replay_database_after_migration_sha256: str = Field(pattern=_SHA256_PATTERN)
    schema8_projection_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    schema8_projection_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    migration9_name: Literal["product-v02322-diagnosis-stage-journal"]
    migration9_only: bool
    diagnosis_stage_event_count_before_forensics: Literal[0]
    new_diagnosis_job_column_non_null_counts: dict[str, Literal[0]]
    replay_clone_read_only: bool
    sealed_reconstruction_unchanged: bool
    reconstruction_verification_sha256: str = Field(pattern=_SHA256_PATTERN)
    clone_evidence_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def replay_clone_is_migration9_only(self) -> ReplayCloneEvidenceV02323:
        if (
            not self.migration9_only
            or not self.replay_clone_read_only
            or not self.sealed_reconstruction_unchanged
            or self.source_database_file_sha256
            != self.replay_database_before_migration_sha256
            or self.schema8_projection_before_sha256
            != self.schema8_projection_after_sha256
            or set(self.new_diagnosis_job_column_non_null_counts)
            != {"exception_fingerprint", "failure_stage", "journal_tail_sha256"}
            or set(self.new_diagnosis_job_column_non_null_counts.values()) != {0}
        ):
            raise ValueError("replay clone boundary differs")
        body = self.model_dump(mode="json", exclude={"clone_evidence_sha256"})
        if self.clone_evidence_sha256 != semantic_sha256_v22(body):
            raise ValueError("replay clone evidence seal differs")
        return self


class DiagnosisForensicsEvidenceV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.diagnosis-forensics.v02323"]
    terminal: Literal["ECOMSRE_PRODUCT_V02323_DIAGNOSIS_FORENSICS_PASS"]
    replay_input_sha256: str = Field(pattern=_SHA256_PATTERN)
    replay_classification: Literal["STRUCTURAL_CONTRACT_REPLAY"]
    diagnosis_result_sha256: str = Field(pattern=_SHA256_PATTERN)
    diagnosis_terminal: str
    bridge_sha256: str = Field(pattern=_SHA256_PATTERN)
    persistence_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_index_sha256: str = Field(pattern=_SHA256_PATTERN)
    decision_trace_sha256: str = Field(pattern=_SHA256_PATTERN)
    stage_event_count: int = Field(ge=2)
    last_completed_stage: Literal["SQL_TRANSACTION_STARTED"]
    journal_tail_sha256: str = Field(pattern=_SHA256_PATTERN)
    rollback_only_sql_validation: bool
    diagnosis_count_before: int = Field(ge=0)
    diagnosis_count_after: int = Field(ge=0)
    evidence_index_count_before: int = Field(ge=0)
    evidence_index_count_after: int = Field(ge=0)
    evidence_object_count_before: int = Field(ge=0)
    evidence_object_count_after: int = Field(ge=0)
    original_failed_job_unchanged: bool
    diagnosis_persistence_replay_attempt_count: Literal[0]
    provider_agent_runbook_docker_calls: Literal[0]
    measured_nofault_authority: Literal["NONE"]
    knowledge_loop_authority: Literal["NONE"]
    forensics_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def forensics_are_no_write_and_sealed(self) -> DiagnosisForensicsEvidenceV02323:
        if (
            not self.rollback_only_sql_validation
            or not self.original_failed_job_unchanged
            or self.diagnosis_count_before != self.diagnosis_count_after
            or self.evidence_index_count_before != self.evidence_index_count_after
            or self.evidence_object_count_before != self.evidence_object_count_after
        ):
            raise ValueError("Diagnosis forensics write boundary differs")
        body = self.model_dump(mode="json", exclude={"forensics_sha256"})
        if self.forensics_sha256 != semantic_sha256_v22(body):
            raise ValueError("Diagnosis forensics seal differs")
        return self


class DiagnosisRootCauseDispositionV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.diagnosis-root-cause.v02323"]
    goal_version: Literal[
        "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
    ]
    terminal: Literal["ECOMSRE_PRODUCT_V02323_ROOT_CAUSE_DISPOSITION_FROZEN"]
    disposition: Literal["ECOMSRE_PRODUCT_V02323_ORIGINAL_ROOT_CAUSE_UNPROVEN"]
    replay_classification: Literal["STRUCTURAL_CONTRACT_REPLAY"]
    replay_input_sha256: str = Field(pattern=_SHA256_PATTERN)
    forensics_sha256: str = Field(pattern=_SHA256_PATTERN)
    exact_original_acquisition_available: Literal[False]
    deterministic_structural_defect_identified: Literal[False]
    exact_original_failure_identity_claimed: Literal[False]
    targeted_repair: Literal["NOT_APPLICABLE"]
    bounded_reason: Literal[
        "ORIGINAL_ACQUISITION_NOT_PERSISTED_AND_STRUCTURAL_PIPELINE_PASSED"
    ]
    diagnosis_persistence_replay_attempt_count: Literal[0]
    measured_nofault_authority: Literal["NONE"]
    knowledge_loop_authority: Literal["NONE"]
    disposition_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def root_cause_is_bounded_and_sealed(self) -> DiagnosisRootCauseDispositionV02323:
        body = self.model_dump(mode="json", exclude={"disposition_sha256"})
        if self.disposition_sha256 != semantic_sha256_v22(body):
            raise ValueError("root-cause disposition seal differs")
        return self


class DiagnosisPipelineReplayResultV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.diagnosis-pipeline-replay-result.v02323"]
    goal_version: Literal[
        "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
    ]
    terminal: Literal["ECOMSRE_PRODUCT_V02323_DIAGNOSIS_PIPELINE_REPLAY_PASS"]
    replay_id: str = Field(pattern=r"^replay-[0-9a-f]{24}$")
    replay_input_sha256: str = Field(pattern=_SHA256_PATTERN)
    reconstruction_disposition: Literal["PRISTINE_BASE_DELTA_RECONSTRUCTION"]
    reconstruction_disposition_sha256: str = Field(pattern=_SHA256_PATTERN)
    replay_classification: Literal["STRUCTURAL_CONTRACT_REPLAY"]
    root_cause_disposition: Literal[
        "ECOMSRE_PRODUCT_V02323_ORIGINAL_ROOT_CAUSE_UNPROVEN"
    ]
    root_cause_disposition_sha256: str = Field(pattern=_SHA256_PATTERN)
    targeted_repair_sha256: None
    formal_incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    original_failed_job_id: str = Field(pattern=r"^job-[0-9a-f]{24}$")
    original_failed_job_sha256_before: str = Field(pattern=_SHA256_PATTERN)
    original_failed_job_sha256_after: str = Field(pattern=_SHA256_PATTERN)
    recovery_job_id: str = Field(pattern=r"^job-[0-9a-f]{24}$")
    recovery_job_sha256: str = Field(pattern=_SHA256_PATTERN)
    recovery_job_status: Literal["SUCCEEDED"]
    diagnosis_result_sha256: str = Field(pattern=_SHA256_PATTERN)
    diagnosis_terminal: str
    evidence_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_index_sha256: str = Field(pattern=_SHA256_PATTERN)
    decision_trace_sha256: str = Field(pattern=_SHA256_PATTERN)
    decision_trace_object_sha256: str = Field(pattern=_SHA256_PATTERN)
    stage_event_count: Literal[54]
    stage_journal_terminal: Literal["JOB_SUCCEEDED"]
    journal_tail_sha256: str = Field(pattern=_SHA256_PATTERN)
    diagnosis_count_before: int = Field(ge=0)
    diagnosis_count_after: int = Field(ge=1)
    evidence_index_count_before: int = Field(ge=0)
    evidence_index_count_after: int = Field(ge=1)
    evidence_object_count_before: int = Field(ge=0)
    evidence_object_count_after: int = Field(ge=7)
    evidence_link_count_before: int = Field(ge=0)
    evidence_link_count_after: int = Field(ge=6)
    job_count_before: int = Field(ge=1)
    job_count_after: int = Field(ge=2)
    replay_backend_call_count: Literal[1]
    original_failed_job_unchanged: Literal[True]
    sealed_reconstruction_unchanged: Literal[True]
    forensic_source_snapshot_unchanged: Literal[True]
    diagnosis_persistence_replay_attempt_count: Literal[1]
    fault_attempts: Literal[0]
    new_baseline_attempts: Literal[0]
    new_business_traffic_executions: Literal[0]
    new_product_incidents: Literal[0]
    provider_agent_runbook_docker_calls: Literal[0]
    measured_nofault_authority: Literal["NONE"]
    knowledge_loop_authority: Literal["NONE"]
    result_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def persistence_replay_is_exact_and_sealed(
        self,
    ) -> DiagnosisPipelineReplayResultV02323:
        if (
            self.original_failed_job_id == self.recovery_job_id
            or self.original_failed_job_sha256_before
            != self.original_failed_job_sha256_after
            or self.diagnosis_count_after != self.diagnosis_count_before + 1
            or self.evidence_index_count_after != self.evidence_index_count_before + 1
            or self.evidence_object_count_after != self.evidence_object_count_before + 7
            or self.evidence_link_count_after != self.evidence_link_count_before + 6
            or self.job_count_after != self.job_count_before + 1
        ):
            raise ValueError("Diagnosis persistence replay delta differs")
        body = self.model_dump(mode="json", exclude={"result_sha256"})
        if self.result_sha256 != semantic_sha256_v22(body):
            raise ValueError("Diagnosis persistence replay result seal differs")
        return self


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_tree_owner_writable(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        os.chmod(path, path.stat().st_mode | 0o200, follow_symlinks=False)


def seal_tree_v02323(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        os.chmod(path, path.stat().st_mode & ~0o222, follow_symlinks=False)
    os.chmod(root, root.stat().st_mode & ~0o222, follow_symlinks=False)


def is_read_only_tree_v02323(root: Path) -> bool:
    return all(not path.stat().st_mode & 0o222 for path in (root, *root.rglob("*")))


def clone_and_apply_migration9_v02323(
    source_product_root: Path,
    destination_product_root: Path,
    *,
    applied_at: datetime,
) -> dict[str, object]:
    source = Path(source_product_root).resolve(strict=True)
    destination = Path(destination_product_root).resolve(strict=False)
    if (
        destination.exists()
        or applied_at.tzinfo is None
        or applied_at.utcoffset() != timedelta(0)
    ):
        raise DiagnosisReplayContractErrorV02323(REPLAY_INPUT_BLOCKER_V02323)
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    _make_tree_owner_writable(destination)
    database = destination / "product.sqlite3"
    before_sha256 = _sha256_file(database)
    connection = sqlite3.connect(database, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        before_versions = tuple(
            int(row[0])
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        )
        if before_versions != tuple(range(1, 9)):
            raise DiagnosisReplayContractErrorV02323(REPLAY_INPUT_BLOCKER_V02323)
        migration = next(item for item in MIGRATIONS if item[0] == 9)
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in migration[2]:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (migration[0], migration[1], applied_at.isoformat()),
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        after_versions = tuple(
            int(row[0])
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        )
        stage_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM diagnosis_stage_events_v02322"
            ).fetchone()[0]
        )
        non_null_counts = {
            column: int(
                connection.execute(
                    f"SELECT COUNT(*) FROM diagnosis_jobs WHERE {column} IS NOT NULL"
                ).fetchone()[0]
            )
            for column in (
                "exception_fingerprint",
                "failure_stage",
                "journal_tail_sha256",
            )
        }
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()
    if (
        after_versions != tuple(range(1, 10))
        or stage_count != 0
        or set(non_null_counts.values()) != {0}
        or integrity != "ok"
        or foreign_keys
    ):
        raise DiagnosisReplayContractErrorV02323(REPLAY_INPUT_BLOCKER_V02323)
    return {
        "before_database_file_sha256": before_sha256,
        "after_database_file_sha256": _sha256_file(database),
        "before_versions": before_versions,
        "after_versions": after_versions,
        "migration9_name": migration[1],
        "diagnosis_stage_event_count": stage_count,
        "new_diagnosis_job_column_non_null_counts": non_null_counts,
    }


def build_structural_acquisition_v02323(
    *, incident: IncidentRecordV1, baseline: EnvironmentBaselineV1
) -> ProductReadAcquisitionV1:
    candidates = incident.candidate_logical_services
    topology = tuple(
        (item.parent_service, item.child_service)
        for item in baseline.topology_edges
        if {item.parent_service, item.child_service}.issubset(set(candidates))
    )
    catalog = build_action_catalog_v22(
        candidate_services=candidates,
        topology=StaticTopologyV22.build(services=candidates, edges=topology),
        capability_registry=build_tool_capability_registry_v22(),
        executed_action_ids=(),
        remaining_budget=100.0,
    )
    outcomes: list[ReadOutcomeV22] = []
    memory_outcomes: list[ReadOutcomeV22] = []
    snapshots: list[dict[str, Any]] = []
    coverage: dict[EvidenceSourceV22, tuple[str, ...]] = {
        source: () for source in EvidenceSourceV22
    }
    for action in catalog.registry_actions:
        seconds = (
            action.request.lookback_seconds
            or action.request.sampling_window_seconds
            or 60
        )
        window = ConnectorWindowV1(
            started_at=incident.diagnosis_observed_at - timedelta(seconds=seconds),
            ended_at=incident.diagnosis_observed_at,
        )
        connector_result = ConnectorQueryResultV1.build(
            source=action.source,
            status=ReadSourceStatusV22.SUCCESS_EMPTY,
            requested_services=action.target_services,
            covered_services=action.target_services,
            window=window,
            records=(),
            truncated=False,
            safe_error_code=None,
            latency_ms=0.0,
        )
        outcome_body: dict[str, object] = {
            "schema_version": "dta-v22.read-outcome.v1",
            "action_id": action.action_id,
            "source": action.source,
            "request_sha256": action.request_sha256,
            "status": ReadSourceStatusV22.SUCCESS_EMPTY,
            "records": (),
            "truncated": False,
        }
        outcome = ReadOutcomeV22.model_validate(
            {
                **outcome_body,
                "outcome_sha256": semantic_sha256_v22(outcome_body),
            }
        )
        outcomes.append(outcome)
        if action.source is not EvidenceSourceV22.RUNTIME:
            memory_outcomes.append(outcome)
        coverage[action.source] = tuple(
            sorted(set(coverage[action.source]).union(action.target_services))
        )
        snapshots.append(
            {
                "schema_version": "ecomsre.product.structural-read-snapshot.v02323",
                "structural_replay": True,
                "action": action.model_dump(mode="json"),
                "connector_components": [],
                "connector_diagnostics": [],
                "connector_bindings_v0232": [],
                "connector_result": connector_result.model_dump(mode="json"),
                "read_outcome": outcome.model_dump(mode="json"),
                "memory_outcome": (
                    None
                    if action.source is EvidenceSourceV22.RUNTIME
                    else outcome.model_dump(mode="json")
                ),
            }
        )
    runtime_action = next(
        action
        for action in catalog.registry_actions
        if action.source is EvidenceSourceV22.RUNTIME
    )
    runtime_snapshot = next(
        snapshot
        for snapshot in snapshots
        if snapshot["action"]["source"] == EvidenceSourceV22.RUNTIME.value
    )
    runtime_result_sha256 = str(runtime_snapshot["connector_result"]["result_sha256"])
    limitations = (
        "RUNTIME_DIAGNOSIS_UNAVAILABLE",
        "RUNTIME_MEMORY_AUTHORITY_UNAVAILABLE",
    )
    limitation_candidates = tuple(
        CapabilityLimitationCandidateV0232.build(
            limitation_code=code,
            category="RUNTIME_AUTHORITY_UNAVAILABLE",
            source=EvidenceSourceV22.RUNTIME,
            capability_status=SourceCapabilityStatusV1.AVAILABLE,
            connector_action_id=runtime_action.action_id,
            connector_result_sha256=runtime_result_sha256,
            safe_error_code="RUNTIME_AUTHORITY_UNAVAILABLE",
            coverage_required_services=runtime_action.target_services,
            coverage_observed_services=runtime_action.target_services,
        )
        for code in limitations
    )
    return ProductReadAcquisitionV1(
        raw_outcomes=tuple(outcomes),
        memory_outcomes=tuple(memory_outcomes),
        snapshots=tuple(snapshots),
        covered_services_by_source=coverage,
        capability_limitations=limitations,
        capability_observations_v0232=(),
        capability_limitation_candidates_v0232=limitation_candidates,
    )


class DiagnosisReplayReadBackendV02323:
    """Forensic-only backend; it is not registered with Product connectors."""

    def __init__(self, acquisition: ProductReadAcquisitionV1) -> None:
        self._acquisition = acquisition
        self.call_count = 0

    def acquire(
        self,
        *,
        incident: IncidentRecordV1,
        environment: EnvironmentRecordV1,
        identity_map: ServiceIdentityMapV1,
        capability_matrix: EnvironmentCapabilityMatrixV1,
        topology_edges: tuple[tuple[str, str], ...],
    ) -> ProductReadAcquisitionV1:
        if (
            incident.environment_id != environment.environment_id
            or incident.service_identity_sha256 != identity_map.identity_sha256
            or incident.source_capability_sha256 != capability_matrix.capability_sha256
            or any(
                left not in incident.candidate_logical_services
                or right not in incident.candidate_logical_services
                for left, right in topology_edges
            )
        ):
            raise DiagnosisReplayContractErrorV02323(REPLAY_INPUT_BLOCKER_V02323)
        self.call_count += 1
        if self.call_count != 1:
            raise DiagnosisReplayContractErrorV02323(REPLAY_INPUT_BLOCKER_V02323)
        return self._acquisition


def build_frozen_replay_input_v02323(
    *,
    replay_id: str,
    reconstruction_disposition_sha256: str,
    reconstruction_sha256: str,
    schema8_projection_sha256: str,
    incident: IncidentRecordV1,
    original_failed_job_id: str,
    structural_input_sha256_by_kind: Mapping[str, str],
) -> FrozenIncidentReplayInputV02323:
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.frozen-incident-replay-input.v02323",
        "goal_version": GOAL_VERSION_V02323,
        "terminal": REPLAY_INPUT_PASS_V02323,
        "replay_id": replay_id,
        "replay_classification": "STRUCTURAL_CONTRACT_REPLAY",
        "reconstruction_disposition": "PRISTINE_BASE_DELTA_RECONSTRUCTION",
        "reconstruction_disposition_sha256": reconstruction_disposition_sha256,
        "reconstruction_sha256": reconstruction_sha256,
        "schema8_projection_sha256": schema8_projection_sha256,
        "formal_incident_id": incident.incident_id,
        "formal_incident_sha256": incident.incident_sha256,
        "original_failed_job_id": original_failed_job_id,
        "original_failed_job_status": "FAILED",
        "original_failed_job_safe_error_code": "INTERNAL_CONTRACT_FAILURE",
        "exact_acquisition_available": False,
        "missing_exact_acquisition_fields": _EXACT_ACQUISITION_FIELDS,
        "structural_input_sha256_by_kind": dict(
            sorted(structural_input_sha256_by_kind.items())
        ),
        "evaluator_truth_field_count": 0,
        "historical_acquisition_authority": "NONE",
        "replay_authority": "STRUCTURAL_ONLY",
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
        "provider_agent_runbook_docker_calls": 0,
    }
    try:
        return FrozenIncidentReplayInputV02323.model_validate(
            {**body, "replay_input_sha256": semantic_sha256_v22(body)}
        )
    except ValueError as error:
        raise DiagnosisReplayContractErrorV02323(REPLAY_INPUT_BLOCKER_V02323) from error


def _stage_chain_is_valid(events: tuple[DiagnosisStageEventV02322, ...]) -> bool:
    previous = "0" * 64
    for ordinal, event in enumerate(events, start=1):
        if (
            event.ordinal != ordinal
            or event.previous_event_sha256 != previous
            or event.status
            not in {
                DiagnosisStageStatusV02322.STARTED,
                DiagnosisStageStatusV02322.PASSED,
            }
        ):
            return False
        previous = event.event_sha256
    return bool(events)


def validate_persistence_rollback_only_v02323(
    *,
    store: SqliteStoreV1,
    temporary_object_store: ContentAddressedObjectStoreV1,
    result: DiagnosisResultV1,
    observations: tuple[dict[str, Any], ...],
    decision_trace: DiagnosisDecisionTraceV0232,
    limitation_candidates: tuple[CapabilityLimitationCandidateV0232, ...],
    bridge_artifact: DiagnosisBridgeArtifactV02322,
    pipeline: DiagnosisPipelineV02322,
) -> dict[str, object]:
    def run_stage(stage: DiagnosisPipelineStageV02322, binding: str, operation):
        return pipeline.run(stage, input_binding_sha256=binding, operation=operation)

    def validate_observations() -> tuple[dict[str, Any], ...]:
        if len({str(item["evidence_ref"]) for item in observations}) != len(
            observations
        ):
            raise ValueError("Diagnosis Evidence object references are not unique")
        return observations

    prepared = run_stage(
        DiagnosisPipelineStageV02322.EVIDENCE_PREPARE_STARTED,
        result.result_sha256,
        validate_observations,
    )
    observation_sha256 = {
        str(item["evidence_ref"]): semantic_sha256_v22(item["payload"])
        for item in prepared
    }
    run_stage(
        DiagnosisPipelineStageV02322.EVIDENCE_OBJECTS_PREPARED,
        semantic_sha256_v22(observation_sha256),
        lambda: observation_sha256,
    )
    limitation_bindings = run_stage(
        DiagnosisPipelineStageV02322.LIMITATION_BINDING_STARTED,
        result.result_sha256,
        lambda: _build_limitation_bindings(
            result=result,
            observations=prepared,
            candidates=limitation_candidates,
        ),
    )
    run_stage(
        DiagnosisPipelineStageV02322.LIMITATION_BINDING_COMPLETED,
        semantic_sha256_v22(
            [item.model_dump(mode="json") for item in limitation_bindings]
        ),
        lambda: limitation_bindings,
    )
    objects = tuple(
        sorted(
            (
                EvidenceObjectV1(
                    evidence_ref=str(item["evidence_ref"]),
                    source=item["source"],
                    action_id=str(item["action_id"]),
                    object_sha256=hashlib.sha256(
                        _json(item["payload"]).encode("utf-8")
                    ).hexdigest(),
                    payload=item["payload"],
                )
                for item in prepared
            ),
            key=lambda item: item.evidence_ref,
        )
    )
    bundle = EvidenceBundleV1(
        incident_id=result.incident_id,
        diagnosis_id=result.diagnosis_id,
        objects=objects,
        supporting_evidence_refs=result.supporting_evidence_refs,
        contradicting_evidence_refs=result.contradicting_evidence_refs,
    )

    def build_index() -> DiagnosisEvidenceIndexV0232:
        dispositions = {
            str(item["evidence_ref"]): _source_disposition(item["payload"])
            for item in prepared
        }
        opensearch_refs = _specialized_binding_refs(
            prepared, binding_kind="OPENSEARCH_PROFILE"
        )
        runtime_refs = _specialized_binding_refs(
            prepared, binding_kind="RUNTIME_SNAPSHOT"
        )
        return DiagnosisEvidenceIndexV0232.build(
            incident_id=result.incident_id,
            diagnosis_id=result.diagnosis_id,
            evidence_bundle_sha256=semantic_sha256_v22(bundle.model_dump(mode="json")),
            all_object_refs=tuple(item.evidence_ref for item in objects),
            all_object_sha256_by_ref={
                item.evidence_ref: item.object_sha256 for item in objects
            },
            linked_support_refs=result.supporting_evidence_refs,
            linked_contradiction_refs=result.contradicting_evidence_refs,
            successful_source_refs=tuple(
                sorted(
                    ref for ref, value in dispositions.items() if value == "SUCCESSFUL"
                )
            ),
            failed_source_refs=tuple(
                sorted(ref for ref, value in dispositions.items() if value == "FAILED")
            ),
            open_search_profile_binding_ref=(
                opensearch_refs[0] if opensearch_refs else None
            ),
            runtime_snapshot_binding_ref=(runtime_refs[0] if runtime_refs else None),
            capability_limitation_bindings=limitation_bindings,
            decision_trace_sha256=decision_trace.trace_sha256,
        )

    index = run_stage(
        DiagnosisPipelineStageV02322.EVIDENCE_INDEX_STARTED,
        result.result_sha256,
        build_index,
    )
    run_stage(
        DiagnosisPipelineStageV02322.EVIDENCE_INDEX_VALIDATED,
        index.index_sha256,
        lambda: index,
    )
    persistence_plan = DiagnosisPersistencePlanV02322.build(
        incident_id=result.incident_id,
        diagnosis_id=result.diagnosis_id,
        bridge_sha256=bridge_artifact.bridge_sha256,
        evidence_object_sha256_by_ref=dict(sorted(observation_sha256.items())),
        limitation_bindings_sha256=semantic_sha256_v22(
            [item.model_dump(mode="json") for item in limitation_bindings]
        ),
        evidence_bundle_sha256=index.evidence_bundle_sha256,
        evidence_index_sha256=index.index_sha256,
        decision_trace_sha256=decision_trace.trace_sha256,
    )
    pipeline.bind_artifacts(
        prepared_evidence_sha256=persistence_plan.persistence_plan_sha256
    )

    def prepare_objects():
        stored = tuple(
            (
                item,
                temporary_object_store.prepare_json(item["payload"]),
            )
            for item in prepared
        )
        trace = temporary_object_store.prepare_json(
            decision_trace.model_dump(mode="json")
        )
        return stored, trace

    stored, stored_trace = run_stage(
        DiagnosisPipelineStageV02322.OBJECT_STORE_PREPARE_STARTED,
        index.index_sha256,
        prepare_objects,
    )
    run_stage(
        DiagnosisPipelineStageV02322.OBJECT_STORE_PREPARED,
        semantic_sha256_v22(
            {str(item["evidence_ref"]): obj.object_sha256 for item, obj in stored}
        ),
        lambda: stored,
    )

    def rollback_transaction() -> dict[str, int]:
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for _item, stored_object in stored:
                    temporary_object_store.bind_prepared(
                        connection, stored_object, created_at=result.created_at
                    )
                temporary_object_store.bind_prepared(
                    connection, stored_trace, created_at=result.created_at
                )
                connection.execute(
                    "INSERT INTO diagnosis_results(diagnosis_id, incident_id, "
                    "payload_json, created_at) VALUES (?, ?, ?, ?)",
                    (
                        result.diagnosis_id,
                        result.incident_id,
                        _json(result.model_dump(mode="json")),
                        result.created_at.isoformat(),
                    ),
                )
                for item, stored_object in stored:
                    connection.execute(
                        "INSERT INTO diagnosis_evidence_links(diagnosis_id, incident_id, "
                        "object_sha256, evidence_ref, source, action_id, role, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, 'OBSERVATION', ?)",
                        (
                            result.diagnosis_id,
                            result.incident_id,
                            stored_object.object_sha256,
                            item["evidence_ref"],
                            item["source"],
                            item["action_id"],
                            result.created_at.isoformat(),
                        ),
                    )
                connection.execute(
                    "INSERT INTO diagnosis_evidence_indexes(diagnosis_id, incident_id, "
                    "payload_json, index_sha256, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        result.diagnosis_id,
                        result.incident_id,
                        _json(index.model_dump(mode="json")),
                        index.index_sha256,
                        result.created_at.isoformat(),
                    ),
                )
                observed = {
                    "diagnosis_rows": int(
                        connection.execute(
                            "SELECT COUNT(*) FROM diagnosis_results WHERE diagnosis_id = ?",
                            (result.diagnosis_id,),
                        ).fetchone()[0]
                    ),
                    "index_rows": int(
                        connection.execute(
                            "SELECT COUNT(*) FROM diagnosis_evidence_indexes "
                            "WHERE diagnosis_id = ?",
                            (result.diagnosis_id,),
                        ).fetchone()[0]
                    ),
                    "link_rows": int(
                        connection.execute(
                            "SELECT COUNT(*) FROM diagnosis_evidence_links "
                            "WHERE diagnosis_id = ?",
                            (result.diagnosis_id,),
                        ).fetchone()[0]
                    ),
                }
                if observed != {
                    "diagnosis_rows": 1,
                    "index_rows": 1,
                    "link_rows": len(stored),
                }:
                    raise ValueError("rollback-only SQL rows differ")
            finally:
                connection.execute("ROLLBACK")
        return observed

    sql_validation = run_stage(
        DiagnosisPipelineStageV02322.SQL_TRANSACTION_STARTED,
        result.result_sha256,
        rollback_transaction,
    )
    return {
        "persistence_plan": persistence_plan,
        "bundle": bundle,
        "index": index,
        "sql_validation": sql_validation,
    }


def freeze_root_cause_unproven_v02323(
    replay_input: FrozenIncidentReplayInputV02323,
    forensics: DiagnosisForensicsEvidenceV02323,
) -> DiagnosisRootCauseDispositionV02323:
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.diagnosis-root-cause.v02323",
        "goal_version": GOAL_VERSION_V02323,
        "terminal": ROOT_CAUSE_DISPOSITION_FROZEN_V02323,
        "disposition": ORIGINAL_ROOT_CAUSE_UNPROVEN_V02323,
        "replay_classification": "STRUCTURAL_CONTRACT_REPLAY",
        "replay_input_sha256": replay_input.replay_input_sha256,
        "forensics_sha256": forensics.forensics_sha256,
        "exact_original_acquisition_available": False,
        "deterministic_structural_defect_identified": False,
        "exact_original_failure_identity_claimed": False,
        "targeted_repair": "NOT_APPLICABLE",
        "bounded_reason": (
            "ORIGINAL_ACQUISITION_NOT_PERSISTED_AND_STRUCTURAL_PIPELINE_PASSED"
        ),
        "diagnosis_persistence_replay_attempt_count": 0,
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
    }
    try:
        return DiagnosisRootCauseDispositionV02323.model_validate(
            {**body, "disposition_sha256": semantic_sha256_v22(body)}
        )
    except ValueError as error:
        raise DiagnosisReplayContractErrorV02323(ROOT_CAUSE_BLOCKER_V02323) from error


__all__ = (
    "DIAGNOSIS_FORENSICS_PASS_V02323",
    "DIAGNOSIS_PIPELINE_REPLAY_PASS_V02323",
    "DiagnosisForensicsEvidenceV02323",
    "DiagnosisPipelineReplayResultV02323",
    "DiagnosisReplayClassificationV02323",
    "DiagnosisReplayContractErrorV02323",
    "DiagnosisReplayReadBackendV02323",
    "DiagnosisRootCauseDispositionV02323",
    "FrozenIncidentReplayInputV02323",
    "ORIGINAL_ROOT_CAUSE_UNPROVEN_V02323",
    "REPLAY_INPUT_PASS_V02323",
    "ROOT_CAUSE_DISPOSITION_FROZEN_V02323",
    "ReplayCloneEvidenceV02323",
    "build_frozen_replay_input_v02323",
    "build_structural_acquisition_v02323",
    "clone_and_apply_migration9_v02323",
    "freeze_root_cause_unproven_v02323",
    "is_read_only_tree_v02323",
    "seal_tree_v02323",
    "validate_persistence_rollback_only_v02323",
)
