#!/usr/bin/env python3
"""Verify Product v0.2.3.2.3 Increment 2 evidence and private reconstruction."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.forensic_schema8_v02323 import (
    ForensicRawSourceSnapshotV02323,
    verify_forensic_source_immutability_v02323,
)
from ecomsre.product.pilot.schema8_reconstruction_v02323 import (
    FormalProductDeltaV02323,
    GOAL_VERSION_V02323,
    PostFormalProductStateV02323,
    PR83_HEAD_V02323,
    ReconstructionDispositionV02323,
    Schema8DefinitionV02323,
    Schema8ProjectionExportV02323,
    Schema8ReconstructionV02323,
    Schema9ContaminationAuditV02323,
    admit_pristine_base_v02323,
    audit_schema9_contamination_v02323,
    build_formal_product_delta_v02323,
    export_schema8_projection_v02323,
    freeze_reconstruction_disposition_v02323,
    inspect_post_formal_state_v02323,
    load_schema8_definition_v02323,
    load_schema9_definition_v02323,
    verify_schema8_reconstruction_v02323,
)
from scripts.ci.verify_product_v02323_increment1 import (
    verify_product_v02323_increment1,
)
from scripts.product_v02323.run_increment1_forensics import (
    _git_bytes,
    _owner_count,
)
from scripts.product_v02323.run_increment2_reconstruction import (
    FORMAL_COMPLETION_LOCATOR,
    FORMAL_INCIDENT_LOCATOR,
    FORMAL_PENDING_JOB_LOCATOR,
    PRISTINE_LOCATOR,
    _attempt_summaries,
    _private_path,
    _private_rows_payload,
    _validate_formal_artifacts,
)


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _require_seal(payload: dict[str, Any], field: str) -> str:
    body = dict(payload)
    supplied = body.pop(field, None)
    if not isinstance(supplied, str) or supplied != semantic_sha256_v22(body):
        raise ValueError(f"Product v0.2.3.2.3 seal differs: {field}")
    return supplied


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def verify_product_v02323_increment2(
    root: Path,
    *,
    source_root: Path,
    pristine_root: Path,
    formal_private_root: Path,
    allow_later_phase_artifacts: bool = False,
) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    verify_product_v02323_increment1(
        project,
        source_root=source_root,
        private_root=project,
        allow_later_phase_artifacts=True,
    )
    predecessor = _load(project / "docs/analysis/product-v02323-predecessor-audit.json")
    digest = _load(project / "docs/analysis/product-v02323-digest-semantics.json")
    definition = Schema8DefinitionV02323.model_validate_json(
        (project / "config/product-v02323/schema8-definition.json").read_text(
            encoding="utf-8"
        )
    )
    if definition.model_dump(mode="json") != load_schema8_definition_v02323(
        project
    ).model_dump(mode="json"):
        raise ValueError("Product v0.2.3.2.3 schema-8 definition differs")
    schema9_definition = load_schema9_definition_v02323(project, definition)

    snapshot = ForensicRawSourceSnapshotV02323.model_validate_json(
        (project / "docs/analysis/product-v02323-forensic-source-snapshot.json").read_text(
            encoding="utf-8"
        )
    )
    snapshot_root = _private_path(project, snapshot.snapshot_locator)
    snapshot_product = snapshot_root / "product"
    snapshot_database = (
        snapshot_root / "consistent-image" / "product.sqlite3"
        if snapshot.snapshot_consistent_database_present
        else snapshot_product / "product.sqlite3"
    )
    incident_bytes = _private_path(
        formal_private_root, FORMAL_INCIDENT_LOCATOR
    ).read_bytes()
    pending_bytes = _private_path(
        formal_private_root, FORMAL_PENDING_JOB_LOCATOR
    ).read_bytes()
    completion_bytes = _private_path(
        formal_private_root, FORMAL_COMPLETION_LOCATOR
    ).read_bytes()
    _validate_formal_artifacts(
        snapshot_database,
        incident_bytes=incident_bytes,
        pending_job_bytes=pending_bytes,
        completion_bytes=completion_bytes,
    )
    migrations_source = _git_bytes(
        project, PR83_HEAD_V02323, "src/ecomsre/product/storage/migrations.py"
    )
    job_repository_source = _git_bytes(
        project, PR83_HEAD_V02323, "src/ecomsre/product/jobs/repository.py"
    )
    formal_runner_source = _git_bytes(
        project, PR83_HEAD_V02323, "scripts/product_v02321/run_formal_nofault.py"
    )
    repository_locator = (
        f"git:{PR83_HEAD_V02323}:src/ecomsre/product/jobs/repository.py"
    )
    runner_locator = (
        f"git:{PR83_HEAD_V02323}:scripts/product_v02321/run_formal_nofault.py"
    )
    migrations_locator = (
        f"git:{PR83_HEAD_V02323}:src/ecomsre/product/storage/migrations.py"
    )
    bindings = {
        FORMAL_INCIDENT_LOCATOR: _sha256(incident_bytes),
        FORMAL_PENDING_JOB_LOCATOR: _sha256(pending_bytes),
        FORMAL_COMPLETION_LOCATOR: _sha256(completion_bytes),
        repository_locator: _sha256(job_repository_source),
        runner_locator: _sha256(formal_runner_source),
        migrations_locator: _sha256(migrations_source),
    }
    projection = Schema8ProjectionExportV02323.model_validate_json(
        (project / "docs/analysis/product-v02323-schema8-projection.json").read_text(
            encoding="utf-8"
        )
    )
    observed_projection, post_rows = export_schema8_projection_v02323(
        snapshot_database,
        definition,
        formal_artifact_bindings=bindings,
    )
    if projection != observed_projection:
        raise ValueError("Product v0.2.3.2.3 schema-8 projection differs")
    admission = admit_pristine_base_v02323(
        pristine_root,
        source_locator=PRISTINE_LOCATOR,
    )
    _base_export, base_rows = export_schema8_projection_v02323(
        pristine_root / "product.sqlite3",
        definition,
        formal_artifact_bindings={},
        allow_missing_schema8_tables=True,
    )
    observed_delta = build_formal_product_delta_v02323(
        definition,
        base_rows,
        post_rows,
        provenance_by_table={
            "incidents": (
                FORMAL_INCIDENT_LOCATOR,
                bindings[FORMAL_INCIDENT_LOCATOR],
                "formal Incident creation",
            ),
            "diagnosis_jobs": (
                FORMAL_COMPLETION_LOCATOR,
                bindings[FORMAL_COMPLETION_LOCATOR],
                "formal Diagnosis job final FAILED state",
            ),
            "job_events": (
                repository_locator,
                bindings[repository_locator],
                "submit, claim, and fail lifecycle events",
            ),
            "product_metric_counters": (
                runner_locator,
                bindings[runner_locator],
                "formal HTTP and job-lifecycle observations",
            ),
            "schema_migrations": (
                migrations_locator,
                bindings[migrations_locator],
                "schema-8 migration admission",
            ),
        },
        require_goal_delta=True,
    )
    delta = FormalProductDeltaV02323.model_validate_json(
        (project / "docs/analysis/product-v02323-formal-delta.json").read_text(
            encoding="utf-8"
        )
    )
    if delta != observed_delta:
        raise ValueError("Product v0.2.3.2.3 formal delta differs")

    reconstruction_evidence = _load(
        project / "docs/analysis/product-v02323-schema8-reconstruction.json"
    )
    reconstruction_evidence_sha256 = _require_seal(
        reconstruction_evidence, "reconstruction_evidence_sha256"
    )
    reconstruction = Schema8ReconstructionV02323.model_validate(
        reconstruction_evidence.get("reconstruction")
    )
    post_formal_state = PostFormalProductStateV02323.model_validate(
        reconstruction_evidence.get("post_formal_state")
    )
    if reconstruction_evidence.get("pristine_base_admission") != admission.model_dump(
        mode="json"
    ):
        raise ValueError("Product v0.2.3.2.3 pristine admission evidence differs")
    reconstructed_product = _private_path(project, reconstruction.reconstruction_locator)
    attempt_summaries = _attempt_summaries(reconstructed_product.parent.parent)
    success_envelope = _load(reconstructed_product.parent / "attempt-pass.json")
    success_sha256 = _require_seal(success_envelope, "attempt_sha256")
    if (
        reconstruction_evidence.get("schema_version")
        != "ecomsre.product.schema8-reconstruction-evidence.v02323"
        or reconstruction_evidence.get("goal_version") != GOAL_VERSION_V02323
        or reconstruction_evidence.get("terminal")
        != "ECOMSRE_PRODUCT_V02323_PRISTINE_BASE_DELTA_RECONSTRUCTION_PASS"
        or reconstruction_evidence.get("reconstruction_attempt_count")
        != len(attempt_summaries)
        or reconstruction_evidence.get("reconstruction_attempts") != attempt_summaries
        or reconstruction_evidence.get("successful_attempt_sha256") != success_sha256
        or success_envelope.get("reconstruction_sha256")
        != reconstruction.reconstruction_sha256
        or success_envelope.get("status") != "PASS"
    ):
        raise ValueError("Product v0.2.3.2.3 attempt evidence differs")
    reconstruction_verification_sha256 = verify_schema8_reconstruction_v02323(
        reconstructed_product,
        definition=definition,
        projection=projection,
        reconstruction=reconstruction,
    )
    if (
        reconstruction_evidence.get("reconstruction_verification_sha256")
        != reconstruction_verification_sha256
        or inspect_post_formal_state_v02323(reconstructed_product) != post_formal_state
    ):
        raise ValueError("Product v0.2.3.2.3 reconstruction verification differs")
    private_rows = _load(reconstructed_product.parent / "schema8-row-exports.json")
    private_rows_sha256 = _require_seal(private_rows, "row_exports_sha256")
    expected_private_rows = {
        "schema_version": "ecomsre.product.private-schema8-row-exports.v02323",
        "base_rows": _private_rows_payload(base_rows),
        "post_formal_rows": _private_rows_payload(post_rows),
    }
    if (
        private_rows != {
            **expected_private_rows,
            "row_exports_sha256": private_rows_sha256,
        }
        or reconstruction_evidence.get("private_row_exports_sha256")
        != private_rows_sha256
    ):
        raise ValueError("Product v0.2.3.2.3 private row export differs")

    immutability = verify_forensic_source_immutability_v02323(
        source_root,
        snapshot,
        owner_counter=_owner_count,
    )
    observed_contamination = audit_schema9_contamination_v02323(
        snapshot_product,
        definition,
        schema9_definition,
        projection,
        reconstructed_projection_sha256=(
            reconstruction.reconstructed_projection_sha256
        ),
        formal_artifact_bindings=bindings,
        source_immutability_proof_sha256=immutability.proof_sha256,
    )
    contamination = Schema9ContaminationAuditV02323.model_validate_json(
        (
            project
            / "docs/analysis/product-v02323-schema9-contamination-audit.json"
        ).read_text(encoding="utf-8")
    )
    if contamination != observed_contamination:
        raise ValueError("Product v0.2.3.2.3 contamination audit differs")
    observed_disposition = freeze_reconstruction_disposition_v02323(
        admission=admission,
        delta=delta,
        projection=projection,
        reconstruction=reconstruction,
        post_formal_state=post_formal_state,
        contamination=contamination,
    )
    disposition = ReconstructionDispositionV02323.model_validate_json(
        (
            project / "docs/analysis/product-v02323-reconstruction-disposition.json"
        ).read_text(encoding="utf-8")
    )
    if disposition != observed_disposition:
        raise ValueError("Product v0.2.3.2.3 reconstruction disposition differs")

    progress = _load(project / "docs/analysis/product-v02323-progress.json")
    progress_sha256 = _require_seal(progress, "progress_sha256")
    expected_progress: dict[str, object] = {
        "schema_version": "ecomsre.product.progress.v02323",
        "goal_version": GOAL_VERSION_V02323,
        "increment": 2,
        "phase": "SCHEMA8_RECONSTRUCTION_FROZEN",
        "terminals": [
            "ECOMSRE_PRODUCT_V02323_HISTORY_AND_BLOCKER_PASS",
            "ECOMSRE_PRODUCT_V02323_FORENSIC_SOURCE_SNAPSHOT_PASS",
            "ECOMSRE_PRODUCT_V02323_DIGEST_SEMANTICS_PASS",
            "ECOMSRE_PRODUCT_V02323_SCHEMA9_CONTAMINATION_AUDIT_PASS",
            "ECOMSRE_PRODUCT_V02323_RECONSTRUCTION_DISPOSITION_FROZEN",
        ],
        "history_audit_sha256": predecessor["audit_sha256"],
        "forensic_source_snapshot_sha256": snapshot.snapshot_sha256,
        "source_immutability_proof_sha256": immutability.proof_sha256,
        "digest_semantics_audit_sha256": digest["audit_sha256"],
        "schema8_definition_sha256": definition.schema8_definition_sha256,
        "schema9_contamination_audit_sha256": contamination.audit_sha256,
        "formal_delta_sha256": delta.delta_sha256,
        "schema8_projection_export_sha256": projection.export_sha256,
        "reconstruction_evidence_sha256": reconstruction_evidence_sha256,
        "reconstruction_disposition_sha256": disposition.disposition_sha256,
        "source_schema_version": 9,
        "reconstructed_schema_version": 8,
        "source_database_file_sha256": snapshot.source_database_file_sha256,
        "reconstructed_database_file_sha256": (
            reconstruction.reconstructed_database_file_sha256
        ),
        "reconstructed_database_logical_sha256": (
            reconstruction.reconstructed_database_logical_sha256
        ),
        "reconstructed_product_state_semantic_sha256": (
            reconstruction.reconstructed_product_state_semantic_sha256
        ),
        "reconstruction_disposition": "PRISTINE_BASE_DELTA_RECONSTRUCTION",
        "reconstruction_attempt_count": len(attempt_summaries),
        "historical_raw_byte_authority": "LOST_RAW_BYTES_NOT_RECONSTRUCTED",
        "historical_logical_authority": "PRISTINE_BASE_DELTA_RECONSTRUCTION",
        "fault_attempt_count": 0,
        "new_baseline_attempt_count": 0,
        "new_business_traffic_execution_count": 0,
        "new_product_incident_count": 0,
        "diagnosis_persistence_replay_attempt_count": 0,
        "provider_calls": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "docker_calls": 0,
        "action_authority": "NONE",
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
        "next_gate": "INCREMENT_3_REPLAY_INPUT_AND_ROOT_CAUSE_DISPOSITION",
    }
    if allow_later_phase_artifacts:
        later_ignored = {
            "increment",
            "phase",
            "terminals",
            "diagnosis_persistence_replay_attempt_count",
            "next_gate",
        }
        required_terminals = {
            "ECOMSRE_PRODUCT_V02323_HISTORY_AND_BLOCKER_PASS",
            "ECOMSRE_PRODUCT_V02323_FORENSIC_SOURCE_SNAPSHOT_PASS",
            "ECOMSRE_PRODUCT_V02323_DIGEST_SEMANTICS_PASS",
            "ECOMSRE_PRODUCT_V02323_SCHEMA9_CONTAMINATION_AUDIT_PASS",
            "ECOMSRE_PRODUCT_V02323_RECONSTRUCTION_DISPOSITION_FROZEN",
        }
        observed_terminals = progress.get("terminals")
        if (
            not isinstance(observed_terminals, list)
            or not required_terminals.issubset(set(observed_terminals))
            or not isinstance(progress.get("increment"), int)
            or int(progress["increment"]) < 2
            or progress.get("diagnosis_persistence_replay_attempt_count")
            not in {0, 1}
            or any(
                progress.get(key) != value
                for key, value in expected_progress.items()
                if key not in later_ignored
            )
        ):
            raise ValueError("Product v0.2.3.2.3 later progress breaks Increment 2")
    elif progress != {**expected_progress, "progress_sha256": progress_sha256}:
        raise ValueError("Product v0.2.3.2.3 Increment 2 progress differs")
    for relative, required in (
        (
            "docs/analysis/product-v02323-schema9-contamination-audit.md",
            contamination.audit_sha256,
        ),
        (
            "docs/analysis/product-v02323-reconstruction-disposition.md",
            disposition.disposition_sha256,
        ),
    ):
        if required not in (project / relative).read_text(encoding="utf-8"):
            raise ValueError(f"Product v0.2.3.2.3 Markdown evidence differs: {relative}")
    return {
        "schema8_definition_sha256": definition.schema8_definition_sha256,
        "contamination_terminal": contamination.terminal,
        "contamination_class": contamination.contamination_class.value,
        "reconstruction_terminal": disposition.reconstruction_terminal,
        "disposition_terminal": disposition.terminal,
        "reconstruction_disposition": disposition.disposition,
        "formal_delta_sha256": delta.delta_sha256,
        "projection_sha256": projection.overall_projection_sha256,
        "reconstructed_database_file_sha256": (
            reconstruction.reconstructed_database_file_sha256
        ),
        "reconstructed_database_logical_sha256": (
            reconstruction.reconstructed_database_logical_sha256
        ),
        "reconstruction_verification_sha256": reconstruction_verification_sha256,
        "source_immutability_proof_sha256": immutability.proof_sha256,
        "progress_sha256": progress_sha256,
        "diagnosis_persistence_replay_attempt_count": 0,
        "provider_agent_runbook_docker_calls": 0,
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--pristine-root", type=Path, required=True)
    parser.add_argument("--formal-private-root", type=Path, required=True)
    arguments = parser.parse_args(argv)
    print(
        json.dumps(
            verify_product_v02323_increment2(
                arguments.root,
                source_root=arguments.source_root,
                pristine_root=arguments.pristine_root,
                formal_private_root=arguments.formal_private_root,
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_product_v02323_increment2",)
