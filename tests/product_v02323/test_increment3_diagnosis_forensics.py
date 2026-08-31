from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from ecomsre.dta_v2.v22.read_contracts import ReadSourceStatusV22, semantic_sha256_v22
from ecomsre.product.incidents.diagnosis_pipeline_v02322 import (
    DiagnosisPrivateFailureEnvelopeV02322,
    DiagnosisPipelineStageV02322,
    DiagnosisPipelineV02322,
)
from ecomsre.product.incidents.diagnosis_stage_journal_v02322 import (
    DiagnosisStageJournalRepositoryV02322,
)
from ecomsre.product.jobs.contracts import ProductJobTypeV1
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.pilot.diagnosis_replay_v02323 import (
    DIAGNOSIS_FORENSICS_PASS_V02323,
    DiagnosisForensicsEvidenceV02323,
    FrozenIncidentReplayInputV02323,
    build_frozen_replay_input_v02323,
    build_structural_acquisition_v02323,
    clone_and_apply_migration9_v02323,
    freeze_root_cause_unproven_v02323,
)
from ecomsre.product.storage.migrations import MIGRATIONS
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from scripts.ci.verify_product_v02323_increment3 import (
    _EXPECTED_FAILED_SEQUENCE,
    _EXPECTED_SUCCESS_SEQUENCE,
    _schema_inventory_payload,
    _validate_structural_acquisition,
)
from scripts.product_v02323.run_increment3_diagnosis_forensics import (
    _acquisition_payload,
    _forensics_failure_boundary,
)


NOW = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
REPLAY_ID = "replay-0123456789abcdef01234567"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _schema8_product(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "objects/sha256").mkdir(parents=True)
    (root / "pilot").mkdir()
    database = root / "product.sqlite3"
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        for version, name, statements in MIGRATIONS:
            if version > 8:
                break
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, NOW.isoformat()),
            )
        connection.execute("COMMIT")
    finally:
        connection.close()
    return root


def _replay_input() -> FrozenIncidentReplayInputV02323:
    incident = SimpleNamespace(
        incident_id="inc-0123456789abcdef01234567",
        incident_sha256="1" * 64,
    )
    return build_frozen_replay_input_v02323(
        replay_id=REPLAY_ID,
        reconstruction_disposition_sha256="2" * 64,
        reconstruction_sha256="3" * 64,
        schema8_projection_sha256="4" * 64,
        incident=incident,  # type: ignore[arg-type]
        original_failed_job_id="job-0123456789abcdef01234567",
        structural_input_sha256_by_kind={"incident": "5" * 64},
    )


def _forensics() -> DiagnosisForensicsEvidenceV02323:
    body = {
        "schema_version": "ecomsre.product.diagnosis-forensics.v02323",
        "terminal": DIAGNOSIS_FORENSICS_PASS_V02323,
        "replay_input_sha256": _replay_input().replay_input_sha256,
        "replay_classification": "STRUCTURAL_CONTRACT_REPLAY",
        "diagnosis_result_sha256": "6" * 64,
        "diagnosis_terminal": "NO_INCIDENT",
        "bridge_sha256": "7" * 64,
        "persistence_plan_sha256": "8" * 64,
        "evidence_bundle_sha256": "9" * 64,
        "evidence_index_sha256": "a" * 64,
        "decision_trace_sha256": "b" * 64,
        "stage_event_count": 42,
        "last_completed_stage": "SQL_TRANSACTION_STARTED",
        "journal_tail_sha256": "c" * 64,
        "rollback_only_sql_validation": True,
        "diagnosis_count_before": 1,
        "diagnosis_count_after": 1,
        "evidence_index_count_before": 0,
        "evidence_index_count_after": 0,
        "evidence_object_count_before": 6,
        "evidence_object_count_after": 6,
        "original_failed_job_unchanged": True,
        "diagnosis_persistence_replay_attempt_count": 0,
        "provider_agent_runbook_docker_calls": 0,
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
    }
    return DiagnosisForensicsEvidenceV02323.model_validate(
        {**body, "forensics_sha256": semantic_sha256_v22(body)}
    )


def test_migration9_is_applied_only_to_the_clone(tmp_path: Path) -> None:
    source = _schema8_product(tmp_path / "source")
    source_sha256 = _sha256(source / "product.sqlite3")

    evidence = clone_and_apply_migration9_v02323(
        source, tmp_path / "clone", applied_at=NOW
    )

    assert _sha256(source / "product.sqlite3") == source_sha256
    assert evidence["before_database_file_sha256"] == source_sha256
    assert evidence["before_versions"] == tuple(range(1, 9))
    assert evidence["after_versions"] == tuple(range(1, 10))
    assert evidence["diagnosis_stage_event_count"] == 0
    non_null_counts = evidence["new_diagnosis_job_column_non_null_counts"]
    assert isinstance(non_null_counts, dict)
    assert set(non_null_counts.values()) == {0}


def test_structural_acquisition_is_deterministic_and_contains_no_evaluator_truth() -> (
    None
):
    incident = SimpleNamespace(
        candidate_logical_services=("checkout",),
        diagnosis_observed_at=NOW,
    )
    baseline = SimpleNamespace(topology_edges=())

    first = build_structural_acquisition_v02323(
        incident=incident,  # type: ignore[arg-type]
        baseline=baseline,  # type: ignore[arg-type]
    )
    second = build_structural_acquisition_v02323(
        incident=incident,  # type: ignore[arg-type]
        baseline=baseline,  # type: ignore[arg-type]
    )

    assert len(first.raw_outcomes) == 6
    assert len(first.memory_outcomes) == 5
    assert all(
        item.status is ReadSourceStatusV22.SUCCESS_EMPTY for item in first.raw_outcomes
    )
    assert first == second
    assert first.capability_limitations == (
        "RUNTIME_DIAGNOSIS_UNAVAILABLE",
        "RUNTIME_MEMORY_AUTHORITY_UNAVAILABLE",
    )
    serialized = json.dumps(first.snapshots, sort_keys=True).casefold()
    assert "root_cause" not in serialized
    assert "evaluator" not in serialized
    assert all(
        services == ("checkout",)
        for services in first.covered_services_by_source.values()
    )


def test_structural_replay_input_cannot_be_resealed_as_exact() -> None:
    replay_input = _replay_input()
    payload = replay_input.model_dump(mode="json")
    payload["replay_classification"] = "EXACT_FROZEN_ACQUISITION_REPLAY"
    body = dict(payload)
    body.pop("replay_input_sha256")
    payload["replay_input_sha256"] = semantic_sha256_v22(body)

    with pytest.raises(ValueError, match="classification differs"):
        FrozenIncidentReplayInputV02323.model_validate(payload)


def test_root_cause_unproven_is_a_sealed_non_repair_disposition() -> None:
    disposition = freeze_root_cause_unproven_v02323(_replay_input(), _forensics())

    assert disposition.disposition == (
        "ECOMSRE_PRODUCT_V02323_ORIGINAL_ROOT_CAUSE_UNPROVEN"
    )
    assert disposition.deterministic_structural_defect_identified is False
    assert disposition.exact_original_failure_identity_claimed is False
    assert disposition.targeted_repair == "NOT_APPLICABLE"
    assert disposition.diagnosis_persistence_replay_attempt_count == 0


def test_schema_inventory_detects_an_extra_migration_object(tmp_path: Path) -> None:
    source = _schema8_product(tmp_path / "source")
    clone = tmp_path / "clone"
    clone_and_apply_migration9_v02323(source, clone, applied_at=NOW)
    expected = _schema_inventory_payload(clone / "product.sqlite3")

    connection = sqlite3.connect(clone / "product.sqlite3", isolation_level=None)
    try:
        connection.execute(
            "CREATE INDEX unexpected_v02323_idx ON diagnosis_jobs(status)"
        )
    finally:
        connection.close()

    assert _schema_inventory_payload(clone / "product.sqlite3") != expected


def test_structural_wrapper_rejects_allowlisted_payload_drift(tmp_path: Path) -> None:
    incident = SimpleNamespace(
        candidate_logical_services=("checkout",),
        diagnosis_observed_at=NOW,
        incident_id="inc-0123456789abcdef01234567",
        incident_sha256="1" * 64,
    )
    acquisition = build_structural_acquisition_v02323(
        incident=incident,  # type: ignore[arg-type]
        baseline=SimpleNamespace(topology_edges=()),  # type: ignore[arg-type]
    )
    acquisition_payload = _acquisition_payload(acquisition)
    replay = build_frozen_replay_input_v02323(
        replay_id=REPLAY_ID,
        reconstruction_disposition_sha256="2" * 64,
        reconstruction_sha256="3" * 64,
        schema8_projection_sha256="4" * 64,
        incident=incident,  # type: ignore[arg-type]
        original_failed_job_id="job-0123456789abcdef01234567",
        structural_input_sha256_by_kind={
            "acquisition": semantic_sha256_v22(acquisition_payload)
        },
    )
    drifted_acquisition = {**acquisition_payload, "evaluator_truth": "hidden"}
    body = {
        "schema_version": "ecomsre.product.structural-acquisition-private.v02323",
        "replay_input_sha256": replay.replay_input_sha256,
        "acquisition": drifted_acquisition,
        "evaluator_truth_field_count": 0,
    }
    path = tmp_path / "structural-acquisition.json"
    path.write_text(
        json.dumps(
            {
                **body,
                "structural_acquisition_sha256": semantic_sha256_v22(body),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        _validate_structural_acquisition(
            path,
            replay,
            expected_acquisition_payload=acquisition_payload,
        )


def test_forensics_stage_sequences_are_exact_and_distinct() -> None:
    assert len(_EXPECTED_FAILED_SEQUENCE) == 28
    assert _EXPECTED_FAILED_SEQUENCE[-2:] == (
        ("BRIDGE_DIAGNOSIS_STARTED", "STARTED"),
        ("FAILED", "FAILED"),
    )
    assert len(_EXPECTED_SUCCESS_SEQUENCE) == 48
    assert _EXPECTED_SUCCESS_SEQUENCE[-2:] == (
        ("SQL_TRANSACTION_STARTED", "STARTED"),
        ("SQL_TRANSACTION_STARTED", "PASSED"),
    )
    assert _EXPECTED_FAILED_SEQUENCE != _EXPECTED_SUCCESS_SEQUENCE[:28]


def test_final_forensics_validation_failure_is_captured_and_sealed(
    tmp_path: Path,
) -> None:
    attempt_root = tmp_path / "attempt"
    product_root = attempt_root / "product"
    store = SqliteStoreV1(product_root / "product.sqlite3")
    job = JobRepositoryV1(store).enqueue(
        ProductJobTypeV1.DIAGNOSIS,
        {"incident_id": "inc-0123456789abcdef01234567"},
        now=NOW.timestamp(),
    )
    pipeline = DiagnosisPipelineV02322(
        DiagnosisStageJournalRepositoryV02322(store),
        job_id=job.job_id,
        incident_id="inc-0123456789abcdef01234567",
        observed_at=NOW,
    )
    pipeline.run(
        DiagnosisPipelineStageV02322.SQL_TRANSACTION_STARTED,
        input_binding_sha256="1" * 64,
        operation=lambda: {"rolled_back": True},
    )
    with pytest.raises(ValueError):
        with _forensics_failure_boundary(
            pipeline=pipeline,
            product_root=product_root,
            job_payload=job.payload,
        ):
            DiagnosisForensicsEvidenceV02323.model_validate({"invalid": True})

    failure_path = attempt_root / "diagnosis-forensics-failure.json"
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    body = dict(failure)
    supplied_sha256 = body.pop("failure_sha256")
    assert supplied_sha256 == semantic_sha256_v22(body)
    assert failure["failure_stage"] == "SQL_TRANSACTION_STARTED"
    assert failure["failure_stage_semantics"] == "AFTER_LAST_PASSED_STAGE"
    assert failure["diagnosis_persistence_replay_attempt_count"] == 0
    private_paths = tuple(
        (product_root / "private/diagnosis-failures" / job.job_id).glob(
            "failure-*.json"
        )
    )
    assert len(private_paths) == 1
    private = DiagnosisPrivateFailureEnvelopeV02322.model_validate_json(
        private_paths[0].read_text(encoding="utf-8")
    )
    assert private.failing_stage is DiagnosisPipelineStageV02322.SQL_TRANSACTION_STARTED
    assert (
        private.last_passed_stage
        is DiagnosisPipelineStageV02322.SQL_TRANSACTION_STARTED
    )
    assert not (attempt_root.stat().st_mode & 0o222)
