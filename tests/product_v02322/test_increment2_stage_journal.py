from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from ecomsre.product.incidents.diagnosis_pipeline_v02322 import (
    DIAGNOSIS_STAGE_JOURNAL_PASS_V02322,
    PRIVATE_FAILURE_EVIDENCE_PASS_V02322,
    DiagnosisAcquisitionArtifactV02322,
    DiagnosisBridgeArtifactV02322,
    DiagnosisPersistencePlanV02322,
    DiagnosisPipelineContextV02322,
    DiagnosisPipelineStageV02322,
    DiagnosisPipelineV02322,
)
from ecomsre.product.incidents.diagnosis_stage_journal_v02322 import (
    DiagnosisStageJournalRepositoryV02322,
    DiagnosisStageStatusV02322,
)
from ecomsre.product.jobs.contracts import ProductJobTypeV1
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.jobs.worker import run_one_job
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.storage.schema import REQUIRED_TABLES, SCHEMA_VERSION
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
INCIDENT_ID = "inc-0123456789abcdef01234567"


def _settings(tmp_path: Path) -> ProductSettingsV1:
    return ProductSettingsV1(
        data_root=tmp_path,
        sqlite_path=tmp_path / "product.sqlite3",
        object_store_root=tmp_path / "objects",
    )


def test_stage_contract_and_schema_are_exact() -> None:
    assert tuple(stage.value for stage in DiagnosisPipelineStageV02322) == (
        "JOB_CLAIMED",
        "INCIDENT_LOAD_STARTED",
        "INCIDENT_LOADED",
        "BASELINE_BINDING_STARTED",
        "BASELINE_BOUND",
        "SERVICE_IDENTITY_BINDING_STARTED",
        "SERVICE_IDENTITY_BOUND",
        "CAPABILITY_BINDING_STARTED",
        "CAPABILITY_BOUND",
        "ENVIRONMENT_LOAD_STARTED",
        "ENVIRONMENT_LOADED",
        "READ_ACQUISITION_STARTED",
        "READ_ACQUISITION_COMPLETED",
        "BRIDGE_DIAGNOSIS_STARTED",
        "BRIDGE_DIAGNOSIS_COMPLETED",
        "EVIDENCE_PREPARE_STARTED",
        "EVIDENCE_OBJECTS_PREPARED",
        "LIMITATION_BINDING_STARTED",
        "LIMITATION_BINDING_COMPLETED",
        "EVIDENCE_INDEX_STARTED",
        "EVIDENCE_INDEX_VALIDATED",
        "OBJECT_STORE_PREPARE_STARTED",
        "OBJECT_STORE_PREPARED",
        "SQL_TRANSACTION_STARTED",
        "DIAGNOSIS_PERSISTED",
        "JOB_RESULT_PREPARED",
        "JOB_SUCCEEDED",
        "FAILED",
    )
    assert SCHEMA_VERSION == 9
    assert "diagnosis_stage_events_v02322" in REQUIRED_TABLES


def test_journal_is_append_only_chained_and_has_one_terminal(tmp_path: Path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    job = JobRepositoryV1(store).enqueue(
        ProductJobTypeV1.DIAGNOSIS,
        {"incident_id": INCIDENT_ID},
        now=NOW.timestamp(),
    )
    repository = DiagnosisStageJournalRepositoryV02322(store)
    pipeline = DiagnosisPipelineV02322(
        repository,
        job_id=job.job_id,
        incident_id=INCIDENT_ID,
        observed_at=NOW,
    )

    pipeline.run(
        DiagnosisPipelineStageV02322.JOB_CLAIMED,
        input_binding_sha256="a" * 64,
        operation=lambda: {"claimed": True},
    )
    pipeline.complete_success(result={"terminal": "NO_INCIDENT"})
    events = repository.list_events(job.job_id)

    assert events[0].ordinal == 1
    assert events[0].previous_event_sha256 == "0" * 64
    assert all(
        current.previous_event_sha256 == previous.event_sha256
        for previous, current in zip(events, events[1:], strict=False)
    )
    assert events[-1].stage is DiagnosisPipelineStageV02322.JOB_SUCCEEDED
    assert events[-1].status is DiagnosisStageStatusV02322.PASSED
    assert repository.verify(job.job_id) == {
        "terminal": DIAGNOSIS_STAGE_JOURNAL_PASS_V02322,
        "job_id": job.job_id,
        "incident_id": INCIDENT_ID,
        "event_count": len(events),
        "terminal_stage": "JOB_SUCCEEDED",
        "journal_tail_sha256": events[-1].event_sha256,
    }
    assert not hasattr(repository, "update")
    assert not hasattr(repository, "delete")
    with pytest.raises(sqlite3.IntegrityError):
        repository.append_event(events[-1])


def test_pipeline_artifacts_are_typed_self_sealed_and_cross_bound() -> None:
    context = DiagnosisPipelineContextV02322.build(
        incident_id=INCIDENT_ID,
        incident_sha256="1" * 64,
        baseline_sha256="2" * 64,
        identity_sha256="3" * 64,
        capability_sha256="4" * 64,
        environment_sha256="5" * 64,
    )
    acquisition = DiagnosisAcquisitionArtifactV02322.build(
        incident_id=INCIDENT_ID,
        raw_outcomes_sha256="6" * 64,
        memory_outcomes_sha256="7" * 64,
        read_snapshots_sha256="8" * 64,
        source_coverage_sha256="9" * 64,
        capability_observations_sha256="a" * 64,
        limitation_candidates_sha256="b" * 64,
    )
    bridge = DiagnosisBridgeArtifactV02322.build(
        incident_id=INCIDENT_ID,
        diagnosis_id="diag-0123456789abcdef01234567",
        result_sha256="c" * 64,
        observations_sha256="d" * 64,
        decision_trace_sha256="e" * 64,
    )
    plan = DiagnosisPersistencePlanV02322.build(
        incident_id=INCIDENT_ID,
        diagnosis_id=bridge.diagnosis_id,
        bridge_sha256=bridge.bridge_sha256,
        evidence_object_sha256_by_ref={"ev-1": "f" * 64},
        limitation_bindings_sha256="0" * 64,
        evidence_bundle_sha256="1" * 64,
        evidence_index_sha256="2" * 64,
        decision_trace_sha256=bridge.decision_trace_sha256,
    )

    assert len(
        {
            context.context_sha256,
            acquisition.acquisition_sha256,
            bridge.bridge_sha256,
            plan.persistence_plan_sha256,
        }
    ) == 4
    with pytest.raises(ValueError, match="context digest differs"):
        DiagnosisPipelineContextV02322.model_validate(
            {**context.model_dump(mode="json"), "baseline_sha256": "0" * 64}
        )


@pytest.mark.parametrize(
    "failure_stage",
    tuple(
        stage
        for stage in DiagnosisPipelineStageV02322
        if stage is not DiagnosisPipelineStageV02322.FAILED
    ),
)
def test_every_diagnosis_stage_preserves_exact_private_failure_evidence(
    tmp_path: Path,
    failure_stage: DiagnosisPipelineStageV02322,
) -> None:
    case_root = tmp_path / failure_stage.value.lower()
    store = SqliteStoreV1(case_root / "product.sqlite3")
    job = JobRepositoryV1(store).enqueue(
        ProductJobTypeV1.DIAGNOSIS,
        {"incident_id": INCIDENT_ID, "authorization": "must-not-persist"},
        now=NOW.timestamp(),
    )
    repository = DiagnosisStageJournalRepositoryV02322(store)

    def inject(stage: DiagnosisPipelineStageV02322) -> None:
        if stage is failure_stage:
            raise RuntimeError(
                "Authorization: Bearer top-secret password=hidden connector failed"
            )

    pipeline = DiagnosisPipelineV02322(
        repository,
        job_id=job.job_id,
        incident_id=INCIDENT_ID,
        observed_at=NOW,
        failure_injector=inject,
    )
    pipeline.bind_artifacts(
        incident_sha256="1" * 64,
        baseline_sha256="2" * 64,
        identity_sha256="3" * 64,
        capability_sha256="4" * 64,
        read_acquisition_sha256="5" * 64,
        bridge_output_sha256="6" * 64,
        prepared_evidence_sha256="7" * 64,
    )
    caught: Exception | None = None
    for stage in DiagnosisPipelineStageV02322:
        if stage is DiagnosisPipelineStageV02322.FAILED:
            continue
        try:
            pipeline.run(
                stage,
                input_binding_sha256="b" * 64,
                operation=lambda: {"stage": stage.value},
            )
        except Exception as error:  # noqa: PERF203 - exact failure probe
            caught = error
            break
    assert caught is not None

    projection, envelope, path = pipeline.capture_failure(
        caught,
        data_root=case_root,
        job_payload=job.payload,
    )
    events = repository.list_events(job.job_id)
    private_bytes = path.read_bytes()

    assert projection.safe_error_code == "INTERNAL_CONTRACT_FAILURE"
    assert projection.failure_stage is failure_stage
    assert projection.exception_fingerprint == envelope.exception_fingerprint
    assert projection.journal_tail_sha256 == events[-1].event_sha256
    assert envelope.failing_stage is failure_stage
    assert envelope.incident_sha256 == "1" * 64
    assert envelope.prepared_evidence_sha256 == "7" * 64
    assert len(envelope.bounded_stack_frames) <= 12
    assert all(
        frame.file.startswith("src/ecomsre/product/")
        for frame in envelope.bounded_stack_frames
    )
    assert b"top-secret" not in private_bytes
    assert b"password=hidden" not in private_bytes
    assert b"authorization" not in private_bytes.lower()
    assert json.loads(private_bytes)["bounded_message"].count("[REDACTED]") >= 1
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert events[-1].stage is DiagnosisPipelineStageV02322.FAILED
    assert events[-1].status is DiagnosisStageStatusV02322.FAILED
    assert repository.verify(job.job_id)["terminal"] == (
        DIAGNOSIS_STAGE_JOURNAL_PASS_V02322
    )


def test_worker_generic_exception_writes_private_and_safe_public_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    store = SqliteStoreV1(settings.sqlite_path)
    jobs = JobRepositoryV1(store)
    queued = jobs.enqueue(
        ProductJobTypeV1.DIAGNOSIS,
        {"incident_id": INCIDENT_ID},
        now=NOW.timestamp(),
    )
    fake_incident = SimpleNamespace(
        incident_id=INCIDENT_ID,
        incident_sha256="d" * 64,
        environment_id="env-0123456789abcdef01234567",
        labels={"fault": "none"},
    )
    monkeypatch.setattr(
        "ecomsre.product.jobs.worker.IncidentRepositoryV1.get",
        lambda _self, _incident_id: fake_incident,
    )
    monkeypatch.setattr(
        "ecomsre.product.jobs.worker.KnowledgeRepositoryV1.active_extensions",
        lambda _self, _environment_id: (),
    )

    def explode(*_args, stage_pipeline_v02322=None, **_kwargs):
        assert stage_pipeline_v02322 is not None

        def fail() -> None:
            raise TypeError("secret=private Authorization: Bearer hidden")

        return stage_pipeline_v02322.run(
            DiagnosisPipelineStageV02322.EVIDENCE_INDEX_STARTED,
            input_binding_sha256="c" * 64,
            operation=fail,
        )

    monkeypatch.setattr(
        "ecomsre.product.jobs.worker.handle_incident_diagnosis",
        explode,
    )

    assert run_one_job(settings, worker_id="worker-v02322", now=NOW.timestamp())
    failed = jobs.get(queued.job_id)
    envelope_paths = tuple(
        (settings.data_root / "private/diagnosis-failures" / queued.job_id).glob(
            "failure-*.json"
        )
    )

    assert failed.status.value == "FAILED"
    assert failed.safe_error_code == "INTERNAL_CONTRACT_FAILURE"
    assert failed.failure_stage == "EVIDENCE_INDEX_STARTED"
    assert failed.exception_fingerprint is not None
    assert failed.journal_tail_sha256 is not None
    assert failed.result is None
    assert len(envelope_paths) == 1
    public = failed.model_dump(mode="json")
    assert "bounded_message" not in public
    assert "bounded_stack_frames" not in public
    private_bytes = envelope_paths[0].read_bytes()
    assert b"secret=private" not in private_bytes
    assert b"Bearer hidden" not in private_bytes

    journal = DiagnosisStageJournalRepositoryV02322(store)
    assert journal.verify(queued.job_id)["terminal"] == (
        DIAGNOSIS_STAGE_JOURNAL_PASS_V02322
    )
    assert PRIVATE_FAILURE_EVIDENCE_PASS_V02322 == (
        "ECOMSRE_PRODUCT_V02322_PRIVATE_FAILURE_EVIDENCE_PASS"
    )
