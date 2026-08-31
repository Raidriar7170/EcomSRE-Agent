#!/usr/bin/env python3
"""Run Product v0.2.3.2.3 Increment 3 without persistence replay or live I/O."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Any, Sequence, cast

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.baselines import BaselineRepositoryV1
from ecomsre.product.environment.capabilities import CapabilityMatrixRepositoryV1
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.incidents.diagnosis_bridge import ProductDiagnosisBridgeV1
from ecomsre.product.incidents.diagnosis_pipeline_v02322 import (
    DiagnosisAcquisitionArtifactV02322,
    DiagnosisBridgeArtifactV02322,
    DiagnosisPersistencePlanV02322,
    DiagnosisPipelineContextV02322,
    DiagnosisPipelineStageV02322,
    DiagnosisPipelineV02322,
)
from ecomsre.product.incidents.contracts import EvidenceBundleV1
from ecomsre.product.incidents.evidence_binding_v0232 import (
    DiagnosisEvidenceIndexV0232,
)
from ecomsre.product.incidents.diagnosis_stage_journal_v02322 import (
    DiagnosisStageJournalRepositoryV02322,
    DiagnosisStageStatusV02322,
)
from ecomsre.product.incidents.repository import IncidentRepositoryV1
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.pilot.diagnosis_replay_v02323 import (
    DIAGNOSIS_FORENSICS_PASS_V02323,
    GOAL_VERSION_V02323,
    DiagnosisForensicsEvidenceV02323,
    DiagnosisReplayContractErrorV02323,
    DiagnosisReplayReadBackendV02323,
    ReplayCloneEvidenceV02323,
    build_frozen_replay_input_v02323,
    build_structural_acquisition_v02323,
    clone_and_apply_migration9_v02323,
    freeze_root_cause_unproven_v02323,
    is_read_only_tree_v02323,
    seal_tree_v02323,
    validate_persistence_rollback_only_v02323,
)
from ecomsre.product.pilot.schema8_reconstruction_v02323 import (
    ReconstructionDispositionV02323,
    Schema8DefinitionV02323,
    Schema8ProjectionExportV02323,
    Schema8ReconstructionV02323,
    export_schema8_projection_v02323,
    verify_schema8_reconstruction_v02323,
)
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from scripts.ci.verify_product_v02323_increment2 import (
    verify_product_v02323_increment2,
)


FORMAL_INCIDENT_ID = "inc-a5a8df708ab77c2f2e19da63"
ORIGINAL_FAILED_JOB_ID = "job-216dd1caac0b92270b1870a2"


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sealed(body: dict[str, object], field: str) -> dict[str, object]:
    return {**body, field: semantic_sha256_v22(body)}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_forensics_attempts_manifest(
    project: Path,
    *,
    successful_replay_id: str,
    replay_input_sha256: str,
    forensics_sha256: str,
) -> dict[str, object]:
    root = project / ".local/product-v02323/diagnosis-forensics"
    attempts: list[tuple[str, dict[str, object]]] = []
    for attempt_root in root.iterdir():
        if not attempt_root.is_dir() or attempt_root.is_symlink():
            raise DiagnosisReplayContractErrorV02323(
                "BLOCKED_ECOMSRE_PRODUCT_V02323_ROOT_CAUSE_DISPOSITION"
            )
        database = attempt_root / "product/product.sqlite3"
        connection = sqlite3.connect(
            f"{database.resolve(strict=True).as_uri()}?mode=ro&immutable=1",
            uri=True,
        )
        try:
            observed_at = str(
                connection.execute(
                    "SELECT MIN(created_at) FROM diagnosis_stage_events_v02322"
                ).fetchone()[0]
            )
        finally:
            connection.close()
        failure_path = attempt_root / "diagnosis-forensics-failure.json"
        if failure_path.is_file():
            failure = json.loads(failure_path.read_text(encoding="utf-8"))
            failure_body = dict(failure)
            failure_sha256 = failure_body.pop("failure_sha256", None)
            if (
                failure_sha256 != semantic_sha256_v22(failure_body)
                or failure.get("diagnosis_persistence_replay_attempt_count") != 0
            ):
                raise DiagnosisReplayContractErrorV02323(
                    "BLOCKED_ECOMSRE_PRODUCT_V02323_ROOT_CAUSE_DISPOSITION"
                )
            record: dict[str, object] = {
                "failure_sha256": failure_sha256,
                "failure_stage": failure.get("failure_stage"),
                "read_only": is_read_only_tree_v02323(attempt_root),
                "replay_id": attempt_root.name,
                "safe_error_code": failure.get("safe_error_code"),
                "status": "FAILED_CLOSED",
            }
        elif attempt_root.name == successful_replay_id:
            record = {
                "forensics_sha256": forensics_sha256,
                "read_only": is_read_only_tree_v02323(attempt_root),
                "replay_id": attempt_root.name,
                "replay_input_sha256": replay_input_sha256,
                "status": "PASS",
            }
        else:
            raise DiagnosisReplayContractErrorV02323(
                "BLOCKED_ECOMSRE_PRODUCT_V02323_ROOT_CAUSE_DISPOSITION"
            )
        attempts.append((observed_at, record))
    ordered = [
        {"attempt_ordinal": ordinal, **record}
        for ordinal, (_observed_at, record) in enumerate(sorted(attempts), start=1)
    ]
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.diagnosis-forensics-attempts.v02323",
        "attempt_count": len(ordered),
        "attempts": ordered,
        "diagnosis_persistence_replay_attempt_count": 0,
        "provider_agent_runbook_docker_calls": 0,
    }
    return _sealed(body, "attempts_sha256")


def _write_create_once(path: Path, payload: bytes, *, replace: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace:
        raise DiagnosisReplayContractErrorV02323(
            f"create-once public artifact exists: {path}"
        )
    temporary = path.with_name(f".{path.name}.v02323-increment3.tmp")
    if temporary.exists():
        raise DiagnosisReplayContractErrorV02323(
            f"temporary public artifact exists: {temporary}"
        )
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _make_tree_owner_writable(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        os.chmod(path, path.stat().st_mode | 0o200, follow_symlinks=False)


def _acquisition_payload(acquisition: Any) -> dict[str, object]:
    return {
        "raw_outcomes": [
            item.model_dump(mode="json") for item in acquisition.raw_outcomes
        ],
        "memory_outcomes": [
            item.model_dump(mode="json") for item in acquisition.memory_outcomes
        ],
        "snapshots": list(acquisition.snapshots),
        "covered_services_by_source": {
            source.value: list(services)
            for source, services in sorted(
                acquisition.covered_services_by_source.items(),
                key=lambda item: item[0].value,
            )
        },
        "capability_limitations": list(acquisition.capability_limitations),
        "capability_observations_v0232": [
            item.model_dump(mode="json")
            for item in acquisition.capability_observations_v0232
        ],
        "capability_limitation_candidates_v0232": [
            item.model_dump(mode="json")
            for item in acquisition.capability_limitation_candidates_v0232
        ],
    }


def _counts(store: SqliteStoreV1) -> dict[str, int]:
    with store.connect() as connection:
        return {
            "diagnosis_results": int(
                connection.execute("SELECT COUNT(*) FROM diagnosis_results").fetchone()[
                    0
                ]
            ),
            "diagnosis_evidence_indexes": int(
                connection.execute(
                    "SELECT COUNT(*) FROM diagnosis_evidence_indexes"
                ).fetchone()[0]
            ),
            "evidence_objects": int(
                connection.execute("SELECT COUNT(*) FROM evidence_objects").fetchone()[
                    0
                ]
            ),
        }


def _load_context(store: SqliteStoreV1):
    environments = EnvironmentRepositoryV1(store)
    services = ServiceCatalogRepositoryV1(store)
    capabilities = CapabilityMatrixRepositoryV1(store)
    baselines = BaselineRepositoryV1(store)
    incidents = IncidentRepositoryV1(
        store,
        environments=environments,
        services=services,
        capabilities=capabilities,
        baselines=baselines,
    )
    incident = incidents.get(FORMAL_INCIDENT_ID)
    environment = environments.get(incident.environment_id)
    identity = services.get_map(incident.environment_id)
    capability = capabilities.get(incident.environment_id)
    baseline = baselines.get_optional(incident.baseline_id)
    if (
        baseline is None
        or baseline.baseline_sha256 != incident.baseline_sha256
        or identity.identity_sha256 != incident.service_identity_sha256
        or capability.capability_sha256 != incident.source_capability_sha256
    ):
        raise DiagnosisReplayContractErrorV02323(
            "BLOCKED_ECOMSRE_PRODUCT_V02323_REPLAY_INPUT"
        )
    return incident, environment, identity, capability, baseline


@contextmanager
def _forensics_failure_boundary(
    *,
    pipeline: DiagnosisPipelineV02322,
    product_root: Path,
    job_payload: Any,
) -> Iterator[None]:
    try:
        yield
    except Exception as error:
        post_stage_failure = (
            pipeline.failing_stage is None and pipeline.last_passed_stage is not None
        )
        if post_stage_failure:
            pipeline.failing_stage = pipeline.last_passed_stage
        projection, _envelope, _path = pipeline.capture_failure(
            error,
            data_root=product_root,
            job_payload=job_payload,
        )
        safe_failure = _sealed(
            {
                "schema_version": "ecomsre.product.diagnosis-forensics-failure.v02323",
                "safe_error_code": projection.safe_error_code,
                "failure_stage": projection.failure_stage.value,
                "failure_stage_semantics": (
                    "AFTER_LAST_PASSED_STAGE" if post_stage_failure else "ACTIVE_STAGE"
                ),
                "exception_fingerprint": projection.exception_fingerprint,
                "journal_tail_sha256": projection.journal_tail_sha256,
                "diagnosis_persistence_replay_attempt_count": 0,
            },
            "failure_sha256",
        )
        _write_create_once(
            product_root.parent / "diagnosis-forensics-failure.json",
            _json_bytes(safe_failure),
        )
        seal_tree_v02323(product_root.parent)
        raise


def _run_forensics(
    product_root: Path,
    *,
    replay_input_sha256: str,
    acquisition: Any,
    observed_at: datetime,
) -> DiagnosisForensicsEvidenceV02323:
    store = SqliteStoreV1(product_root / "product.sqlite3")
    incident, environment, identity, capability, baseline = _load_context(store)
    jobs = JobRepositoryV1(store)
    original_job_before = jobs.get(ORIGINAL_FAILED_JOB_ID)
    if (
        original_job_before.status.value != "FAILED"
        or original_job_before.safe_error_code != "INTERNAL_CONTRACT_FAILURE"
        or original_job_before.result is not None
    ):
        raise DiagnosisReplayContractErrorV02323(
            "BLOCKED_ECOMSRE_PRODUCT_V02323_ROOT_CAUSE_DISPOSITION"
        )
    before = _counts(store)
    journal = DiagnosisStageJournalRepositoryV02322(store)
    pipeline = DiagnosisPipelineV02322(
        journal,
        job_id=ORIGINAL_FAILED_JOB_ID,
        incident_id=FORMAL_INCIDENT_ID,
        observed_at=observed_at,
    )
    pipeline.bind_artifacts(incident_sha256=incident.incident_sha256)
    with _forensics_failure_boundary(
        pipeline=pipeline,
        product_root=product_root,
        job_payload=original_job_before.payload,
    ):
        pipeline.run(
            DiagnosisPipelineStageV02322.JOB_CLAIMED,
            input_binding_sha256=semantic_sha256_v22(
                original_job_before.model_dump(mode="json")
            ),
            operation=lambda: {
                "forensic_replay_of_status": original_job_before.status.value,
                "job_id": original_job_before.job_id,
            },
        )
        loaded_incident = pipeline.run(
            DiagnosisPipelineStageV02322.INCIDENT_LOAD_STARTED,
            input_binding_sha256=semantic_sha256_v22(original_job_before.payload),
            operation=lambda: incident,
        )
        pipeline.run(
            DiagnosisPipelineStageV02322.INCIDENT_LOADED,
            input_binding_sha256=loaded_incident.incident_sha256,
            operation=lambda: loaded_incident,
        )
        loaded_baseline = pipeline.run(
            DiagnosisPipelineStageV02322.BASELINE_BINDING_STARTED,
            input_binding_sha256=incident.baseline_sha256,
            operation=lambda: baseline,
        )
        pipeline.run(
            DiagnosisPipelineStageV02322.BASELINE_BOUND,
            input_binding_sha256=loaded_baseline.baseline_sha256,
            operation=lambda: loaded_baseline,
        )
        pipeline.bind_artifacts(baseline_sha256=loaded_baseline.baseline_sha256)
        loaded_identity = pipeline.run(
            DiagnosisPipelineStageV02322.SERVICE_IDENTITY_BINDING_STARTED,
            input_binding_sha256=incident.service_identity_sha256,
            operation=lambda: identity,
        )
        pipeline.run(
            DiagnosisPipelineStageV02322.SERVICE_IDENTITY_BOUND,
            input_binding_sha256=loaded_identity.identity_sha256,
            operation=lambda: loaded_identity,
        )
        pipeline.bind_artifacts(identity_sha256=loaded_identity.identity_sha256)
        loaded_capability = pipeline.run(
            DiagnosisPipelineStageV02322.CAPABILITY_BINDING_STARTED,
            input_binding_sha256=incident.source_capability_sha256,
            operation=lambda: capability,
        )
        pipeline.run(
            DiagnosisPipelineStageV02322.CAPABILITY_BOUND,
            input_binding_sha256=loaded_capability.capability_sha256,
            operation=lambda: loaded_capability,
        )
        pipeline.bind_artifacts(capability_sha256=loaded_capability.capability_sha256)
        loaded_environment = pipeline.run(
            DiagnosisPipelineStageV02322.ENVIRONMENT_LOAD_STARTED,
            input_binding_sha256=incident.incident_sha256,
            operation=lambda: environment,
        )
        environment_sha256 = semantic_sha256_v22(
            loaded_environment.model_dump(mode="json")
        )
        pipeline.run(
            DiagnosisPipelineStageV02322.ENVIRONMENT_LOADED,
            input_binding_sha256=environment_sha256,
            operation=lambda: loaded_environment,
        )
        context = DiagnosisPipelineContextV02322.build(
            incident_id=incident.incident_id,
            incident_sha256=incident.incident_sha256,
            baseline_sha256=baseline.baseline_sha256,
            identity_sha256=identity.identity_sha256,
            capability_sha256=capability.capability_sha256,
            environment_sha256=environment_sha256,
        )
        backend = DiagnosisReplayReadBackendV02323(acquisition)
        acquired = pipeline.run(
            DiagnosisPipelineStageV02322.READ_ACQUISITION_STARTED,
            input_binding_sha256=context.context_sha256,
            operation=lambda: backend.acquire(
                incident=incident,
                environment=environment,
                identity_map=identity,
                capability_matrix=capability,
                topology_edges=tuple(
                    (item.parent_service, item.child_service)
                    for item in baseline.topology_edges
                    if {
                        item.parent_service,
                        item.child_service,
                    }.issubset(set(incident.candidate_logical_services))
                ),
            ),
        )
        acquisition_artifact = DiagnosisAcquisitionArtifactV02322.build(
            incident_id=incident.incident_id,
            raw_outcomes_sha256=semantic_sha256_v22(
                [item.model_dump(mode="json") for item in acquired.raw_outcomes]
            ),
            memory_outcomes_sha256=semantic_sha256_v22(
                [item.model_dump(mode="json") for item in acquired.memory_outcomes]
            ),
            read_snapshots_sha256=semantic_sha256_v22(list(acquired.snapshots)),
            source_coverage_sha256=semantic_sha256_v22(
                {
                    source.value: list(services)
                    for source, services in sorted(
                        acquired.covered_services_by_source.items(),
                        key=lambda item: item[0].value,
                    )
                }
            ),
            capability_observations_sha256=semantic_sha256_v22(
                [
                    item.model_dump(mode="json")
                    for item in acquired.capability_observations_v0232
                ]
            ),
            limitation_candidates_sha256=semantic_sha256_v22(
                [
                    item.model_dump(mode="json")
                    for item in acquired.capability_limitation_candidates_v0232
                ]
            ),
        )
        pipeline.bind_artifacts(
            read_acquisition_sha256=acquisition_artifact.acquisition_sha256
        )
        pipeline.run(
            DiagnosisPipelineStageV02322.READ_ACQUISITION_COMPLETED,
            input_binding_sha256=acquisition_artifact.acquisition_sha256,
            operation=lambda: acquired,
        )
        diagnosis_id = (
            "diag-"
            + semantic_sha256_v22(
                {
                    "replay_input_sha256": replay_input_sha256,
                    "incident_id": incident.incident_id,
                    "phase": "ROLLBACK_ONLY_FORENSICS",
                }
            )[:24]
        )
        diagnosed = pipeline.run(
            DiagnosisPipelineStageV02322.BRIDGE_DIAGNOSIS_STARTED,
            input_binding_sha256=acquisition_artifact.acquisition_sha256,
            operation=lambda: ProductDiagnosisBridgeV1().diagnose(
                incident=incident,
                baseline=baseline,
                identity_map=identity,
                acquisition=acquired,
                diagnosis_id=diagnosis_id,
                created_at=observed_at,
            ),
        )
        result, observations, decision_trace = diagnosed
        bridge_artifact = DiagnosisBridgeArtifactV02322.build(
            incident_id=incident.incident_id,
            diagnosis_id=result.diagnosis_id,
            result_sha256=result.result_sha256,
            observations_sha256=semantic_sha256_v22(list(observations)),
            decision_trace_sha256=decision_trace.trace_sha256,
        )
        pipeline.bind_artifacts(bridge_output_sha256=bridge_artifact.bridge_sha256)
        pipeline.run(
            DiagnosisPipelineStageV02322.BRIDGE_DIAGNOSIS_COMPLETED,
            input_binding_sha256=bridge_artifact.bridge_sha256,
            operation=lambda: bridge_artifact,
        )
        temporary_store = ContentAddressedObjectStoreV1(
            product_root.parent / "temporary-cas", metadata_store=store
        )
        prepared = validate_persistence_rollback_only_v02323(
            store=store,
            temporary_object_store=temporary_store,
            result=result,
            observations=observations,
            decision_trace=decision_trace,
            limitation_candidates=acquired.capability_limitation_candidates_v0232,
            bridge_artifact=bridge_artifact,
            pipeline=pipeline,
        )
        events = journal.list_events(ORIGINAL_FAILED_JOB_ID)
        after = _counts(store)
        original_job_after = jobs.get(ORIGINAL_FAILED_JOB_ID)
        if (
            not events
            or events[-1].stage
            is not DiagnosisPipelineStageV02322.SQL_TRANSACTION_STARTED
            or events[-1].status is not DiagnosisStageStatusV02322.PASSED
            or any(
                event.ordinal != ordinal
                or event.previous_event_sha256
                != ("0" * 64 if ordinal == 1 else events[ordinal - 2].event_sha256)
                for ordinal, event in enumerate(events, start=1)
            )
            or original_job_before != original_job_after
            or before != after
        ):
            raise DiagnosisReplayContractErrorV02323(
                "BLOCKED_ECOMSRE_PRODUCT_V02323_ROOT_CAUSE_DISPOSITION"
            )
        persistence_plan = cast(
            DiagnosisPersistencePlanV02322, prepared["persistence_plan"]
        )
        bundle = cast(EvidenceBundleV1, prepared["bundle"])
        index = cast(DiagnosisEvidenceIndexV0232, prepared["index"])
        body: dict[str, object] = {
            "schema_version": "ecomsre.product.diagnosis-forensics.v02323",
            "terminal": DIAGNOSIS_FORENSICS_PASS_V02323,
            "replay_input_sha256": replay_input_sha256,
            "replay_classification": "STRUCTURAL_CONTRACT_REPLAY",
            "diagnosis_result_sha256": result.result_sha256,
            "diagnosis_terminal": result.terminal.value,
            "bridge_sha256": bridge_artifact.bridge_sha256,
            "persistence_plan_sha256": persistence_plan.persistence_plan_sha256,
            "evidence_bundle_sha256": semantic_sha256_v22(
                bundle.model_dump(mode="json")
            ),
            "evidence_index_sha256": index.index_sha256,
            "decision_trace_sha256": decision_trace.trace_sha256,
            "stage_event_count": len(events),
            "last_completed_stage": "SQL_TRANSACTION_STARTED",
            "journal_tail_sha256": events[-1].event_sha256,
            "rollback_only_sql_validation": True,
            "diagnosis_count_before": before["diagnosis_results"],
            "diagnosis_count_after": after["diagnosis_results"],
            "evidence_index_count_before": before["diagnosis_evidence_indexes"],
            "evidence_index_count_after": after["diagnosis_evidence_indexes"],
            "evidence_object_count_before": before["evidence_objects"],
            "evidence_object_count_after": after["evidence_objects"],
            "original_failed_job_unchanged": True,
            "diagnosis_persistence_replay_attempt_count": 0,
            "provider_agent_runbook_docker_calls": 0,
            "measured_nofault_authority": "NONE",
            "knowledge_loop_authority": "NONE",
        }
        forensics = DiagnosisForensicsEvidenceV02323.model_validate(
            {**body, "forensics_sha256": semantic_sha256_v22(body)}
        )
    return forensics


def run_increment3(
    root: Path,
    *,
    source_root: Path,
    pristine_root: Path,
    formal_private_root: Path,
    replay_id: str,
    observed_at: datetime,
) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    if (
        re.fullmatch(r"replay-[0-9a-f]{24}", replay_id) is None
        or observed_at.tzinfo is None
        or observed_at.utcoffset() != timedelta(0)
    ):
        raise DiagnosisReplayContractErrorV02323(
            "BLOCKED_ECOMSRE_PRODUCT_V02323_REPLAY_INPUT"
        )
    verify_product_v02323_increment2(
        project,
        source_root=source_root,
        pristine_root=pristine_root,
        formal_private_root=formal_private_root,
    )
    definition = Schema8DefinitionV02323.model_validate_json(
        (project / "config/product-v02323/schema8-definition.json").read_text(
            encoding="utf-8"
        )
    )
    projection = Schema8ProjectionExportV02323.model_validate_json(
        (project / "docs/analysis/product-v02323-schema8-projection.json").read_text(
            encoding="utf-8"
        )
    )
    reconstruction_payload = json.loads(
        (
            project / "docs/analysis/product-v02323-schema8-reconstruction.json"
        ).read_text(encoding="utf-8")
    )
    reconstruction = Schema8ReconstructionV02323.model_validate(
        reconstruction_payload["reconstruction"]
    )
    disposition = ReconstructionDispositionV02323.model_validate_json(
        (
            project / "docs/analysis/product-v02323-reconstruction-disposition.json"
        ).read_text(encoding="utf-8")
    )
    reconstruction_root = project / reconstruction.reconstruction_locator
    initial_reconstruction_verification = verify_schema8_reconstruction_v02323(
        reconstruction_root,
        definition=definition,
        projection=projection,
        reconstruction=reconstruction,
    )
    replay_root = project / ".local/product-v02323/replay-input" / replay_id
    replay_product = replay_root / "product"
    migration = clone_and_apply_migration9_v02323(
        reconstruction_root,
        replay_product,
        applied_at=observed_at,
    )
    replay_store = SqliteStoreV1(replay_product / "product.sqlite3")
    incident, environment, identity, capability, baseline = _load_context(replay_store)
    acquisition = build_structural_acquisition_v02323(
        incident=incident, baseline=baseline
    )
    acquisition_payload = _acquisition_payload(acquisition)
    structural_sha = {
        "acquisition": semantic_sha256_v22(acquisition_payload),
        "baseline": baseline.baseline_sha256,
        "capability": capability.capability_sha256,
        "environment": semantic_sha256_v22(environment.model_dump(mode="json")),
        "incident": incident.incident_sha256,
        "service_identity": identity.identity_sha256,
    }
    replay_input = build_frozen_replay_input_v02323(
        replay_id=replay_id,
        reconstruction_disposition_sha256=disposition.disposition_sha256,
        reconstruction_sha256=reconstruction.reconstruction_sha256,
        schema8_projection_sha256=projection.overall_projection_sha256,
        incident=incident,
        original_failed_job_id=ORIGINAL_FAILED_JOB_ID,
        structural_input_sha256_by_kind=structural_sha,
    )
    _write_create_once(
        replay_root / "structural-acquisition.json",
        _json_bytes(
            _sealed(
                {
                    "schema_version": (
                        "ecomsre.product.structural-acquisition-private.v02323"
                    ),
                    "replay_input_sha256": replay_input.replay_input_sha256,
                    "acquisition": acquisition_payload,
                    "evaluator_truth_field_count": 0,
                },
                "structural_acquisition_sha256",
            )
        ),
    )
    after_projection, _rows = export_schema8_projection_v02323(
        replay_product / "product.sqlite3",
        definition,
        formal_artifact_bindings=projection.formal_artifact_bindings,
    )
    if (
        after_projection.overall_projection_sha256
        != projection.overall_projection_sha256
    ):
        raise DiagnosisReplayContractErrorV02323(
            "BLOCKED_ECOMSRE_PRODUCT_V02323_REPLAY_INPUT"
        )
    final_replay_database_sha256 = _sha256_file(replay_product / "product.sqlite3")
    seal_tree_v02323(replay_product)
    final_reconstruction_verification = verify_schema8_reconstruction_v02323(
        reconstruction_root,
        definition=definition,
        projection=projection,
        reconstruction=reconstruction,
    )
    if final_reconstruction_verification != initial_reconstruction_verification:
        raise DiagnosisReplayContractErrorV02323(
            "BLOCKED_ECOMSRE_PRODUCT_V02323_REPLAY_INPUT"
        )
    clone_body: dict[str, object] = {
        "schema_version": "ecomsre.product.replay-clone-evidence.v02323",
        "replay_id": replay_id,
        "reconstruction_sha256": reconstruction.reconstruction_sha256,
        "source_locator": reconstruction.reconstruction_locator,
        "replay_clone_locator": str(replay_product.relative_to(project)),
        "source_schema_version": 8,
        "replay_schema_version": 9,
        "source_database_file_sha256": reconstruction.reconstructed_database_file_sha256,
        "replay_database_before_migration_sha256": migration[
            "before_database_file_sha256"
        ],
        "replay_database_after_migration_sha256": final_replay_database_sha256,
        "schema8_projection_before_sha256": projection.overall_projection_sha256,
        "schema8_projection_after_sha256": after_projection.overall_projection_sha256,
        "migration9_name": migration["migration9_name"],
        "migration9_only": True,
        "diagnosis_stage_event_count_before_forensics": migration[
            "diagnosis_stage_event_count"
        ],
        "new_diagnosis_job_column_non_null_counts": migration[
            "new_diagnosis_job_column_non_null_counts"
        ],
        "replay_clone_read_only": is_read_only_tree_v02323(replay_product),
        "sealed_reconstruction_unchanged": True,
        "reconstruction_verification_sha256": final_reconstruction_verification,
    }
    clone_evidence = ReplayCloneEvidenceV02323.model_validate(
        {
            **clone_body,
            "clone_evidence_sha256": semantic_sha256_v22(clone_body),
        }
    )
    seal_tree_v02323(replay_root)
    forensics_root = project / ".local/product-v02323/diagnosis-forensics" / replay_id
    forensics_product = forensics_root / "product"
    if forensics_root.exists():
        raise DiagnosisReplayContractErrorV02323(
            "BLOCKED_ECOMSRE_PRODUCT_V02323_ROOT_CAUSE_DISPOSITION"
        )
    shutil.copytree(replay_product, forensics_product, copy_function=shutil.copy2)
    _make_tree_owner_writable(forensics_root)
    forensics = _run_forensics(
        forensics_product,
        replay_input_sha256=replay_input.replay_input_sha256,
        acquisition=acquisition,
        observed_at=observed_at + timedelta(seconds=1),
    )
    seal_tree_v02323(forensics_root)
    root_cause = freeze_root_cause_unproven_v02323(replay_input, forensics)
    attempts_manifest = _build_forensics_attempts_manifest(
        project,
        successful_replay_id=replay_id,
        replay_input_sha256=replay_input.replay_input_sha256,
        forensics_sha256=forensics.forensics_sha256,
    )
    replay_evidence_body: dict[str, object] = {
        "schema_version": "ecomsre.product.replay-input-evidence.v02323",
        "goal_version": GOAL_VERSION_V02323,
        "terminal": replay_input.terminal,
        "replay_input": replay_input.model_dump(mode="json"),
        "clone_evidence": clone_evidence.model_dump(mode="json"),
        "exact_acquisition_persistence_audit": {
            "database_acquisition_table_count": 0,
            "persisted_exact_acquisition_bundle_count": 0,
            "missing_fields": list(replay_input.missing_exact_acquisition_fields),
            "classification": "STRUCTURAL_CONTRACT_REPLAY",
        },
        "diagnosis_persistence_replay_attempt_count": 0,
        "provider_agent_runbook_docker_calls": 0,
    }
    replay_evidence = _sealed(replay_evidence_body, "replay_evidence_sha256")
    progress_before = json.loads(
        (project / "docs/analysis/product-v02323-progress.json").read_text(
            encoding="utf-8"
        )
    )
    progress_body: dict[str, object] = {
        **{
            key: value
            for key, value in progress_before.items()
            if key
            not in {"progress_sha256", "increment", "phase", "terminals", "next_gate"}
        },
        "increment": 3,
        "phase": "ROOT_CAUSE_DISPOSITION_FROZEN",
        "terminals": [
            *progress_before["terminals"],
            replay_input.terminal,
            root_cause.terminal,
        ],
        "replay_input_sha256": replay_input.replay_input_sha256,
        "replay_clone_evidence_sha256": clone_evidence.clone_evidence_sha256,
        "diagnosis_forensics_sha256": forensics.forensics_sha256,
        "diagnosis_forensics_attempt_count": attempts_manifest["attempt_count"],
        "diagnosis_forensics_attempts_sha256": attempts_manifest["attempts_sha256"],
        "root_cause_disposition_sha256": root_cause.disposition_sha256,
        "replay_classification": replay_input.replay_classification.value,
        "root_cause_disposition": root_cause.disposition,
        "targeted_repair": "NOT_APPLICABLE",
        "diagnosis_persistence_replay_attempt_count": 0,
        "provider_calls": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "docker_calls": 0,
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
        "next_gate": "INCREMENT_4_NO_DEFECT_PATH_AND_SINGLE_PERSISTENCE_REPLAY",
    }
    progress = _sealed(progress_body, "progress_sha256")
    public_payloads = {
        "config/product-v02323/replay/replay-input.json": _json_bytes(
            replay_input.model_dump(mode="json")
        ),
        "docs/analysis/product-v02323-replay-input.json": _json_bytes(replay_evidence),
        "docs/analysis/product-v02323-diagnosis-forensics.json": _json_bytes(
            forensics.model_dump(mode="json")
        ),
        "docs/analysis/product-v02323-diagnosis-forensics-attempts.json": _json_bytes(
            attempts_manifest
        ),
        "docs/analysis/product-v02323-diagnosis-root-cause.json": _json_bytes(
            root_cause.model_dump(mode="json")
        ),
        "docs/analysis/product-v02323-diagnosis-root-cause.md": (
            "# Product v0.2.3.2.3 Diagnosis root-cause disposition\n\n"
            f"- Terminal: `{root_cause.terminal}`\n"
            f"- Disposition: `{root_cause.disposition}`\n"
            "- Replay class: `STRUCTURAL_CONTRACT_REPLAY`\n"
            "- Exact original acquisition: unavailable\n"
            "- Deterministic structural defect: not identified\n"
            "- Targeted repair: `NOT_APPLICABLE`\n"
            f"- Disposition SHA-256: `{root_cause.disposition_sha256}`\n\n"
            "The structural pipeline completed through rollback-only SQL validation. "
            "Because the original acquisition artifacts were not persisted, this does "
            "not reproduce or identify the exact historical INTERNAL_CONTRACT_FAILURE. "
            "No repair is invented. The next gate is one fresh Diagnosis-only "
            "persistence replay after independent review.\n"
        ).encode("utf-8"),
        "docs/analysis/product-v02323-progress.json": _json_bytes(progress),
    }
    for relative, payload in public_payloads.items():
        _write_create_once(
            project / relative,
            payload,
            replace=relative == "docs/analysis/product-v02323-progress.json",
        )
    return {
        "replay_input_terminal": replay_input.terminal,
        "replay_input_sha256": replay_input.replay_input_sha256,
        "replay_classification": replay_input.replay_classification.value,
        "clone_evidence_sha256": clone_evidence.clone_evidence_sha256,
        "diagnosis_forensics_terminal": forensics.terminal,
        "diagnosis_forensics_sha256": forensics.forensics_sha256,
        "root_cause_terminal": root_cause.terminal,
        "root_cause_disposition": root_cause.disposition,
        "root_cause_disposition_sha256": root_cause.disposition_sha256,
        "targeted_repair": root_cause.targeted_repair,
        "diagnosis_persistence_replay_attempt_count": 0,
        "provider_agent_runbook_docker_calls": 0,
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
        "progress_sha256": progress["progress_sha256"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--pristine-root", type=Path, required=True)
    parser.add_argument("--formal-private-root", type=Path, required=True)
    parser.add_argument("--replay-id", required=True)
    parser.add_argument("--observed-at", required=True)
    arguments = parser.parse_args(argv)
    observed_at = datetime.fromisoformat(arguments.observed_at.replace("Z", "+00:00"))
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    result = run_increment3(
        arguments.root,
        source_root=arguments.source_root,
        pristine_root=arguments.pristine_root,
        formal_private_root=arguments.formal_private_root,
        replay_id=arguments.replay_id,
        observed_at=observed_at,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("run_increment3",)
