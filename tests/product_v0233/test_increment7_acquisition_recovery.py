from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
from ecomsre.product.connectors.base import (
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.incidents.read_backend import ProductReadAcquisitionV1
from ecomsre.product.incidents.diagnosis_pipeline_v02322 import (
    DiagnosisPipelineStageV02322,
    DiagnosisPipelineV02322,
)
from ecomsre.product.incidents.diagnosis_stage_journal_v02322 import (
    DiagnosisStageJournalRepositoryV02322,
)
from ecomsre.product.errors import ProductError
from ecomsre.product.jobs.contracts import ProductJobTypeV1
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.jobs.worker import run_one_job
from ecomsre.product.pilot.diagnosis_recovery_v0233 import (
    FormalDiagnosisRecoverySubmissionV0233,
    FormalDiagnosisJobContextV0233,
    build_diagnosis_acquisition_checkpoint_v0233,
    final_diagnosis_idempotency_key_v0233,
    restore_diagnosis_acquisition_v0233,
)
from ecomsre.product.pilot.formal_recovery_v0233 import (
    DiagnosisAcquisitionCheckpointV0233,
)
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from ecomsre_live_sandbox.contracts import write_private_json
from scripts.product_v0233 import resume_formal_nofault as resume_command


def _sha(character: str) -> str:
    return character * 64


def _empty_outcome() -> ReadOutcomeV22:
    body = {
        "schema_version": "dta-v22.read-outcome.v1",
        "action_id": "a:runtime:inspect",
        "source": EvidenceSourceV22.RUNTIME,
        "request_sha256": _sha("1"),
        "status": ReadSourceStatusV22.SUCCESS_EMPTY,
        "records": (),
        "truncated": False,
    }
    return ReadOutcomeV22.model_validate(
        {**body, "outcome_sha256": semantic_sha256_v22(body)}
    )


def _acquisition() -> ProductReadAcquisitionV1:
    started = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
    result = ConnectorQueryResultV1.build(
        source=EvidenceSourceV22.RUNTIME,
        status=ReadSourceStatusV22.SUCCESS_EMPTY,
        requested_services=("checkout",),
        covered_services=("checkout",),
        window=ConnectorWindowV1(
            started_at=started,
            ended_at=started + timedelta(seconds=60),
        ),
        records=(),
        truncated=False,
        safe_error_code=None,
        latency_ms=1.0,
    )
    outcome = _empty_outcome()
    snapshot = {
        "schema_version": "ecomsre.product.read-snapshot.v1",
        "incident_id": "inc-" + "1" * 24,
        "action": {"action_id": outcome.action_id},
        "connector_components": [result.model_dump(mode="json")],
        "connector_diagnostics": [],
        "connector_bindings_v0232": [
            {
                "connector_binding": {
                    "binding_kind": "RUNTIME_SNAPSHOT",
                    "binding_sha256": _sha("2"),
                },
                "binding_payload": {"binding_sha256": _sha("3")},
            },
            {
                "connector_binding": {
                    "binding_kind": "OPENSEARCH_PROFILE",
                    "binding_sha256": _sha("a"),
                },
                "binding_payload": {
                    "binding_sha256": _sha("b"),
                    "active_profile_sha256": _sha("4"),
                    "selected_candidate_alias": "P01",
                },
            },
        ],
        "connector_result": result.model_dump(mode="json"),
        "read_outcome": outcome.model_dump(mode="json"),
        "memory_outcome": None,
    }
    return ProductReadAcquisitionV1(
        raw_outcomes=(outcome,),
        memory_outcomes=(),
        snapshots=(snapshot,),
        covered_services_by_source={
            EvidenceSourceV22.RUNTIME: ("checkout",),
        },
        capability_limitations=(),
        capability_observations_v0232=(),
        capability_limitation_candidates_v0232=(),
    )


def test_acquisition_checkpoint_round_trips_exact_frozen_inputs() -> None:
    started = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
    context = FormalDiagnosisJobContextV0233.build(
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=2,
        attempt_id="attempt-2",
        diagnosis_generation=1,
        active_profile_sha256=_sha("4"),
        semantic_surface_sha256=_sha("5"),
        acquisition_sha256=None,
    )
    acquisition = _acquisition()
    checkpoint = build_diagnosis_acquisition_checkpoint_v0233(
        context=context,
        acquisition=acquisition,
        incident_id="inc-" + "1" * 24,
        incident_sha256=_sha("6"),
        incident_observation_started_at=started,
        incident_observation_ended_at=started + timedelta(seconds=300),
        baseline_sha256=_sha("7"),
        service_identity_sha256=_sha("8"),
        capability_sha256=_sha("9"),
    )
    recovery_context = FormalDiagnosisJobContextV0233.build(
        **context.model_dump(
            mode="python",
            exclude={
                "schema_version",
                "context_sha256",
                "acquisition_checkpoint_locator",
                "acquisition_sha256",
            },
        ),
        acquisition_sha256=checkpoint.acquisition_sha256,
    )
    restored = restore_diagnosis_acquisition_v0233(
        checkpoint,
        context=recovery_context,
        incident_id=checkpoint.incident_id,
        incident_sha256=checkpoint.incident_sha256,
    )

    assert restored == acquisition
    assert checkpoint.connector_query_results == (
        acquisition.snapshots[0]["connector_result"],
    )
    assert checkpoint.runtime_snapshot_binding_sha256 == _sha("3")
    assert final_diagnosis_idempotency_key_v0233(
        context=recovery_context,
        incident_sha256=checkpoint.incident_sha256,
        acquisition_sha256=checkpoint.acquisition_sha256,
    ).startswith("formal-v0233-diagnosis-")


def test_recovery_submission_reuses_same_incident_and_frozen_acquisition() -> None:
    started = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
    original_context = FormalDiagnosisJobContextV0233.build(
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=2,
        attempt_id="attempt-2",
        diagnosis_generation=1,
        active_profile_sha256=_sha("4"),
        semantic_surface_sha256=_sha("5"),
        acquisition_sha256=None,
    )
    checkpoint = build_diagnosis_acquisition_checkpoint_v0233(
        context=original_context,
        acquisition=_acquisition(),
        incident_id="inc-" + "1" * 24,
        incident_sha256=_sha("6"),
        incident_observation_started_at=started,
        incident_observation_ended_at=started + timedelta(seconds=300),
        baseline_sha256=_sha("7"),
        service_identity_sha256=_sha("8"),
        capability_sha256=_sha("9"),
    )
    submission = FormalDiagnosisRecoverySubmissionV0233.build(
        checkpoint=checkpoint,
        diagnosis_generation=2,
        preserved_failed_job_ids=("job-" + "a" * 24,),
    )

    assert submission.incident_id == checkpoint.incident_id
    assert submission.context.acquisition_sha256 == checkpoint.acquisition_sha256
    assert submission.context.diagnosis_generation == 2
    assert submission.job_payload == {
        "incident_id": checkpoint.incident_id,
        "formal_recovery_v0233": submission.context.model_dump(mode="json"),
    }


def test_resume_reuses_unfinished_generation_and_advances_after_failure(
    tmp_path,
) -> None:
    started = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
    context = FormalDiagnosisJobContextV0233.build(
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=2,
        attempt_id="attempt-2",
        diagnosis_generation=1,
        active_profile_sha256=_sha("4"),
        semantic_surface_sha256=_sha("5"),
        acquisition_sha256=None,
    )
    checkpoint = build_diagnosis_acquisition_checkpoint_v0233(
        context=context,
        acquisition=_acquisition(),
        incident_id="inc-" + "1" * 24,
        incident_sha256=_sha("6"),
        incident_observation_started_at=started,
        incident_observation_ended_at=started + timedelta(seconds=300),
        baseline_sha256=_sha("7"),
        service_identity_sha256=_sha("8"),
        capability_sha256=_sha("9"),
    )
    recovery_root = tmp_path / "recovery"
    assert resume_command._recovery_generation_v0233(recovery_root) == (2, None)
    submission = FormalDiagnosisRecoverySubmissionV0233.build(
        checkpoint=checkpoint,
        diagnosis_generation=2,
        preserved_failed_job_ids=("job-" + "a" * 24,),
    )
    generation_root = recovery_root / "diagnosis-generation-0002"
    write_private_json(
        generation_root / "submission.json",
        submission.model_dump(mode="json"),
        create_once=True,
    )

    generation, recovered = resume_command._recovery_generation_v0233(recovery_root)
    assert generation == 2
    assert recovered == submission

    write_private_json(
        generation_root / "diagnosis-job-completion.json",
        {"status": "FAILED"},
        create_once=True,
    )
    assert resume_command._recovery_generation_v0233(recovery_root) == (3, None)


def test_running_job_rebinds_to_final_diagnosis_idempotency_once(tmp_path) -> None:
    jobs = JobRepositoryV1(SqliteStoreV1(tmp_path / "product.sqlite3"))
    first = jobs.enqueue(
        ProductJobTypeV1.DIAGNOSIS,
        {"incident_id": "inc-" + "1" * 24},
        idempotency_key="formal-v0233-acquisition-first",
        now=1.0,
    )
    claimed = jobs.claim_next("worker-1", lease_seconds=30, now=2.0)
    assert claimed is not None and claimed.job_id == first.job_id

    rebound = jobs.bind_idempotency_key(
        claimed.job_id,
        "worker-1",
        claimed.attempt_count,
        "formal-v0233-diagnosis-final",
        now=3.0,
    )
    assert rebound.idempotency_key == "formal-v0233-diagnosis-final"
    assert (
        jobs.bind_idempotency_key(
            claimed.job_id,
            "worker-1",
            claimed.attempt_count,
            "formal-v0233-diagnosis-final",
            now=4.0,
        ).idempotency_key
        == "formal-v0233-diagnosis-final"
    )

    second = jobs.enqueue(
        ProductJobTypeV1.DIAGNOSIS,
        {"incident_id": "inc-" + "2" * 24},
        idempotency_key="formal-v0233-acquisition-second",
        now=5.0,
    )
    second_claim = jobs.claim_next("worker-2", lease_seconds=30, now=6.0)
    assert second_claim is not None and second_claim.job_id == second.job_id
    with pytest.raises(ProductError, match="idempotency"):
        jobs.bind_idempotency_key(
            second_claim.job_id,
            "worker-2",
            second_claim.attempt_count,
            "formal-v0233-diagnosis-final",
            now=7.0,
        )


def test_only_expired_target_job_can_be_reclaimed_for_failure_sealing(
    tmp_path,
) -> None:
    jobs = JobRepositoryV1(SqliteStoreV1(tmp_path / "product.sqlite3"))
    queued = jobs.enqueue(
        ProductJobTypeV1.DIAGNOSIS,
        {"incident_id": "inc-" + "1" * 24},
        idempotency_key="formal-v0233-acquisition-first",
        now=1.0,
    )
    claimed = jobs.claim_next("worker-original", lease_seconds=10, now=2.0)
    assert claimed is not None

    with pytest.raises(ProductError, match="lease"):
        jobs.reclaim_expired(
            queued.job_id,
            expected_attempt_count=claimed.attempt_count,
            worker_id="worker-recovery",
            lease_seconds=30,
            now=11.0,
        )

    reclaimed = jobs.reclaim_expired(
        queued.job_id,
        expected_attempt_count=claimed.attempt_count,
        worker_id="worker-recovery",
        lease_seconds=30,
        now=13.0,
    )
    assert reclaimed.job_id == queued.job_id
    assert reclaimed.claimed_by == "worker-recovery"
    assert reclaimed.attempt_count == claimed.attempt_count + 1
    with pytest.raises(ProductError, match="lease"):
        jobs.fail(
            queued.job_id,
            "worker-original",
            claimed.attempt_count,
            "FORMAL_WORKER_INTERRUPTED",
            now=14.0,
        )
    assert jobs.fail(
        queued.job_id,
        "worker-recovery",
        reclaimed.attempt_count,
        "FORMAL_WORKER_INTERRUPTED",
        now=14.0,
    ).status.value == "FAILED"


def test_interrupted_job_is_terminally_sealed_before_recovery_generation(
    tmp_path,
    monkeypatch,
) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    jobs = JobRepositoryV1(store)
    context = FormalDiagnosisJobContextV0233.build(
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=2,
        attempt_id="attempt-2",
        diagnosis_generation=1,
        active_profile_sha256=_sha("4"),
        semantic_surface_sha256=_sha("5"),
        acquisition_sha256=None,
    )
    checkpoint = build_diagnosis_acquisition_checkpoint_v0233(
        context=context,
        acquisition=_acquisition(),
        incident_id="inc-" + "1" * 24,
        incident_sha256=_sha("6"),
        incident_observation_started_at=datetime(2026, 9, 1, 2, 0, tzinfo=UTC),
        incident_observation_ended_at=datetime(2026, 9, 1, 2, 5, tzinfo=UTC),
        baseline_sha256=_sha("7"),
        service_identity_sha256=_sha("8"),
        capability_sha256=_sha("9"),
    )
    queued = jobs.enqueue(
        ProductJobTypeV1.DIAGNOSIS,
        {
            "incident_id": checkpoint.incident_id,
            "formal_recovery_v0233": context.model_dump(mode="json"),
        },
        idempotency_key="formal-v0233-acquisition-first",
        now=1.0,
    )
    claimed = jobs.claim_next("worker-original", lease_seconds=10, now=2.0)
    assert claimed is not None
    pipeline = DiagnosisPipelineV02322(
        DiagnosisStageJournalRepositoryV02322(store),
        job_id=queued.job_id,
        incident_id=checkpoint.incident_id,
        observed_at=datetime(2026, 9, 1, 2, 0, tzinfo=UTC),
    )
    pipeline.run(
        DiagnosisPipelineStageV02322.JOB_CLAIMED,
        input_binding_sha256=_sha("a"),
        operation=lambda: {"attempt_count": 1},
    )
    pipeline.run(
        DiagnosisPipelineStageV02322.READ_ACQUISITION_COMPLETED,
        input_binding_sha256=_sha("b"),
        operation=lambda: {"acquisition_sha256": checkpoint.acquisition_sha256},
    )
    monkeypatch.setattr(resume_command.time, "time", lambda: 13.0)

    failed = resume_command._seal_interrupted_job_v0233(
        jobs=jobs,
        product_root=tmp_path,
        job=claimed,
        acquisition=checkpoint,
        cleanup_clean=True,
    )

    events = DiagnosisStageJournalRepositoryV02322(store).list_events(queued.job_id)
    assert failed.status.value == "FAILED"
    assert failed.safe_error_code == "FORMAL_WORKER_INTERRUPTED"
    assert failed.failure_stage == "JOB_CLAIMED"
    assert events[-1].stage.value == "FAILED"
    assert events[-1].event_sha256 == failed.journal_tail_sha256
    assert tuple((tmp_path / "private/diagnosis-failures" / queued.job_id).glob("*.json"))


def test_failed_job_preserved_and_recovery_job_uses_exact_acquisition(
    tmp_path, monkeypatch
) -> None:
    settings = ProductSettingsV1(
        data_root=tmp_path,
        sqlite_path=tmp_path / "product.sqlite3",
        object_store_root=tmp_path / "objects",
    )
    jobs = JobRepositoryV1(SqliteStoreV1(settings.sqlite_path))
    incident = SimpleNamespace(
        incident_id="inc-" + "1" * 24,
        incident_sha256=_sha("6"),
        environment_id="env-" + "2" * 24,
        started_at=datetime(2026, 9, 1, 2, 0, tzinfo=UTC),
        diagnosis_observed_at=datetime(2026, 9, 1, 2, 5, tzinfo=UTC),
        labels={"fault": "none"},
    )
    monkeypatch.setattr(
        "ecomsre.product.jobs.worker.IncidentRepositoryV1.get",
        lambda _self, _incident_id: incident,
    )
    monkeypatch.setattr(
        "ecomsre.product.jobs.worker.KnowledgeRepositoryV1.active_extensions",
        lambda _self, _environment_id: (),
    )
    context = FormalDiagnosisJobContextV0233.build(
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=2,
        attempt_id="attempt-2",
        diagnosis_generation=1,
        active_profile_sha256=_sha("4"),
        semantic_surface_sha256=_sha("5"),
        acquisition_sha256=None,
    )
    first = jobs.enqueue(
        ProductJobTypeV1.DIAGNOSIS,
        {
            "incident_id": incident.incident_id,
            "formal_recovery_v0233": context.model_dump(mode="json"),
        },
        idempotency_key="formal-v0233-acquisition-attempt-2",
        now=1.0,
    )
    calls = 0
    exact_acquisition = _acquisition()

    def fail_then_recover(
        *_args,
        frozen_acquisition_v0233=None,
        seal_acquisition_v0233=None,
        loaded_incident_v02322=None,
        **_kwargs,
    ):
        nonlocal calls
        calls += 1
        assert seal_acquisition_v0233 is not None
        if calls == 1:
            assert frozen_acquisition_v0233 is None
            sealed = exact_acquisition
        else:
            assert frozen_acquisition_v0233 == exact_acquisition
            sealed = frozen_acquisition_v0233
        seal_acquisition_v0233(
            sealed,
            loaded_incident_v02322,
            _sha("7"),
            _sha("8"),
            _sha("9"),
        )
        if calls == 1:
            raise RuntimeError("injected persistence failure after acquisition")
        return {"terminal": "NO_INCIDENT"}

    monkeypatch.setattr(
        "ecomsre.product.jobs.worker.handle_incident_diagnosis",
        fail_then_recover,
    )

    assert run_one_job(settings, worker_id="worker-1", now=2.0)
    failed = jobs.get(first.job_id)
    checkpoint_path = settings.data_root / context.acquisition_checkpoint_locator
    checkpoint = DiagnosisAcquisitionCheckpointV0233.model_validate_json(
        checkpoint_path.read_bytes()
    )
    assert failed.status.value == "FAILED"
    assert failed.idempotency_key == final_diagnosis_idempotency_key_v0233(
        context=context,
        incident_sha256=incident.incident_sha256,
        acquisition_sha256=checkpoint.acquisition_sha256,
    )
    assert checkpoint_path.stat().st_mode & 0o077 == 0

    recovery_context = FormalDiagnosisJobContextV0233.build(
        **context.model_dump(
            mode="python",
            exclude={
                "schema_version",
                "context_sha256",
                "acquisition_checkpoint_locator",
                "acquisition_sha256",
                "diagnosis_generation",
            },
        ),
        diagnosis_generation=2,
        acquisition_sha256=checkpoint.acquisition_sha256,
    )
    recovery_key = final_diagnosis_idempotency_key_v0233(
        context=recovery_context,
        incident_sha256=incident.incident_sha256,
        acquisition_sha256=checkpoint.acquisition_sha256,
    )
    recovery = jobs.enqueue(
        ProductJobTypeV1.DIAGNOSIS,
        {
            "incident_id": incident.incident_id,
            "formal_recovery_v0233": recovery_context.model_dump(mode="json"),
        },
        idempotency_key=recovery_key,
        now=3.0,
    )

    assert run_one_job(settings, worker_id="worker-2", now=4.0)
    assert jobs.get(first.job_id).status.value == "FAILED"
    assert jobs.get(recovery.job_id).status.value == "SUCCEEDED"
    assert jobs.get(recovery.job_id).idempotency_key == recovery_key
    assert calls == 2
