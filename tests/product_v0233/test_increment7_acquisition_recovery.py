from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import pytest

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    RuntimeStateV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.action_catalog import (
    EvidenceActionV22,
    StaticTopologyV22,
    build_action_catalog_v22,
    build_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
from ecomsre.product.connectors.base import (
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    ACTIVE_PROFILE_BINDING_SHA256_V023,
    ACTIVE_PROFILE_SHA256_V023,
    CANDIDATE_SET_SHA256_V023,
    OPERATOR_DECISION_SHA256_V023,
)
from ecomsre.product.connectors.pilot_runtime import PilotRuntimeSnapshotV02
from ecomsre.product.incidents.read_backend import ProductReadAcquisitionV1
from ecomsre.product.incidents.evidence_binding_v0232 import (
    ConnectorEvidenceBindingV0232,
    OpenSearchProfileEvidenceBindingV0232,
    RuntimeSnapshotEvidenceBindingV0232,
)
from ecomsre.product.contracts import ConnectorKindV1
from ecomsre.product.incidents.diagnosis_pipeline_v02322 import (
    DiagnosisPrivateFailureEnvelopeV02322,
    DiagnosisPipelineStageV02322,
    DiagnosisPipelineV02322,
)
from ecomsre.product.incidents.diagnosis_stage_journal_v02322 import (
    DiagnosisStageJournalRepositoryV02322,
    DiagnosisStageStatusV02322,
)
from ecomsre.product.errors import ProductError
from ecomsre.product.jobs.contracts import ProductJobStatusV1, ProductJobTypeV1
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
    LiveCaptureBundleV0233,
)
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from ecomsre_live_sandbox.contracts import write_private_json
from scripts.product_v0233 import resume_formal_nofault as resume_command
from scripts.product_v0233 import run_formal_nofault as run_command


def _sha(character: str) -> str:
    return character * 64


def _action(source: EvidenceSourceV22) -> EvidenceActionV22:
    catalog = build_action_catalog_v22(
        candidate_services=("checkout",),
        topology=StaticTopologyV22.build(services=("checkout",), edges=()),
        capability_registry=build_tool_capability_registry_v22(),
        executed_action_ids=(),
        remaining_budget=100.0,
    )
    return next(
        action for action in catalog.registry_actions if action.source is source
    )


def _empty_outcome(action: EvidenceActionV22) -> ReadOutcomeV22:
    body = {
        "schema_version": "dta-v22.read-outcome.v1",
        "action_id": action.action_id,
        "source": action.source,
        "request_sha256": action.request_sha256,
        "status": ReadSourceStatusV22.SUCCESS_EMPTY,
        "records": (),
        "truncated": False,
    }
    return ReadOutcomeV22.model_validate(
        {**body, "outcome_sha256": semantic_sha256_v22(body)}
    )


def _acquisition() -> ProductReadAcquisitionV1:
    started = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
    window = ConnectorWindowV1(
        started_at=started,
        ended_at=started + timedelta(seconds=60),
    )
    runtime_action = _action(EvidenceSourceV22.RUNTIME)
    logs_action = _action(EvidenceSourceV22.LOGS)
    runtime_result = ConnectorQueryResultV1.build(
        source=EvidenceSourceV22.RUNTIME,
        status=ReadSourceStatusV22.SUCCESS_EMPTY,
        requested_services=("checkout",),
        covered_services=("checkout",),
        window=window,
        records=(),
        truncated=False,
        safe_error_code=None,
        latency_ms=1.0,
    )
    logs_result = ConnectorQueryResultV1.build(
        source=EvidenceSourceV22.LOGS,
        status=ReadSourceStatusV22.SUCCESS_EMPTY,
        requested_services=("checkout",),
        covered_services=("checkout",),
        window=window,
        records=(),
        truncated=False,
        safe_error_code=None,
        latency_ms=1.0,
    )
    runtime_outcome = _empty_outcome(runtime_action)
    logs_outcome = _empty_outcome(logs_action)
    specialized = RuntimeSnapshotEvidenceBindingV0232.build(
        runtime_snapshot_sha256=_sha("2"),
        runtime_snapshot_observed_at=started + timedelta(seconds=30),
        runtime_snapshot_environment_id="env-" + "1" * 24,
        runtime_snapshot_authority_sha256=_sha("3"),
        pilot_runtime_authority_sha256=_sha("4"),
        read_authority_sha256=_sha("5"),
        connector_binding_sha256=_sha("3"),
        maximum_age_seconds=60,
        age_at_query_seconds=30.0,
        requested_services=("checkout",),
        covered_services=("checkout",),
        connector_result_sha256=runtime_result.result_sha256,
        query_window=runtime_result.window,
    )
    generic = ConnectorEvidenceBindingV0232.build(
        binding_id="binding:v0232:" + "1" * 24,
        incident_id="inc-" + "1" * 24,
        action_id=runtime_action.action_id,
        source=EvidenceSourceV22.RUNTIME,
        connector_name="pilot-runtime",
        connector_kind=ConnectorKindV1.PILOT_RUNTIME,
        environment_id="env-" + "1" * 24,
        connector_config_sha256=_sha("6"),
        query_context_sha256=_sha("7"),
        component_result_sha256=runtime_result.result_sha256,
        combined_result_sha256=runtime_result.result_sha256,
        requested_services=("checkout",),
        covered_services=("checkout",),
        window=runtime_result.window,
        binding_kind="RUNTIME_SNAPSHOT",
        binding_payload_sha256=specialized.binding_sha256,
    )
    profile = OpenSearchProfileEvidenceBindingV0232.build(
        active_profile_id="product-v0222-operator-selected-profile",
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        profile_binding_sha256=ACTIVE_PROFILE_BINDING_SHA256_V023,
        selected_candidate_alias="P01",
        candidate_set_sha256=CANDIDATE_SET_SHA256_V023,
        operator_decision_sha256=OPERATOR_DECISION_SHA256_V023,
        query_diagnostics_sha256=_sha("8"),
        accepted_record_count=0,
        rejected_record_count=0,
        rejection_reason_codes=(),
        connector_result_sha256=logs_result.result_sha256,
        query_window=logs_result.window,
    )
    profile_generic = ConnectorEvidenceBindingV0232.build(
        binding_id="binding:v0232:" + "2" * 24,
        incident_id="inc-" + "1" * 24,
        action_id=logs_action.action_id,
        source=EvidenceSourceV22.LOGS,
        connector_name="opensearch",
        connector_kind=ConnectorKindV1.OPENSEARCH,
        environment_id="env-" + "1" * 24,
        connector_config_sha256=_sha("9"),
        query_context_sha256=_sha("a"),
        component_result_sha256=logs_result.result_sha256,
        combined_result_sha256=logs_result.result_sha256,
        requested_services=("checkout",),
        covered_services=("checkout",),
        window=logs_result.window,
        binding_kind="OPENSEARCH_PROFILE",
        binding_payload_sha256=profile.binding_sha256,
    )
    runtime_snapshot = {
        "schema_version": "ecomsre.product.read-snapshot.v1",
        "incident_id": "inc-" + "1" * 24,
        "action": runtime_action.model_dump(mode="json"),
        "connector_components": [runtime_result.model_dump(mode="json")],
        "connector_diagnostics": [],
        "connector_bindings_v0232": [
            {
                "connector_binding": generic.model_dump(mode="json"),
                "binding_payload": specialized.model_dump(mode="json"),
            }
        ],
        "connector_result": runtime_result.model_dump(mode="json"),
        "read_outcome": runtime_outcome.model_dump(mode="json"),
        "memory_outcome": None,
    }
    logs_snapshot = {
        "schema_version": "ecomsre.product.read-snapshot.v1",
        "incident_id": "inc-" + "1" * 24,
        "action": logs_action.model_dump(mode="json"),
        "connector_components": [logs_result.model_dump(mode="json")],
        "connector_diagnostics": [],
        "connector_bindings_v0232": [
            {
                "connector_binding": profile_generic.model_dump(mode="json"),
                "binding_payload": profile.model_dump(mode="json"),
            }
        ],
        "connector_result": logs_result.model_dump(mode="json"),
        "read_outcome": logs_outcome.model_dump(mode="json"),
        "memory_outcome": None,
    }
    return ProductReadAcquisitionV1(
        raw_outcomes=(runtime_outcome, logs_outcome),
        memory_outcomes=(),
        snapshots=(runtime_snapshot, logs_snapshot),
        covered_services_by_source={
            EvidenceSourceV22.RUNTIME: ("checkout",),
            EvidenceSourceV22.LOGS: ("checkout",),
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
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
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
    assert (
        checkpoint.connector_query_results[0].model_dump(mode="json")
        == (acquisition.snapshots[0]["connector_result"])
    )
    assert (
        checkpoint.runtime_snapshot_binding_sha256
        == acquisition.snapshots[0]["connector_bindings_v0232"][0]["binding_payload"][
            "binding_sha256"
        ]
    )
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
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
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


@pytest.mark.parametrize("terminal_before_rollover", [False, True])
@pytest.mark.parametrize("private_acquisition_exists", [False, True])
def test_semantic_rollover_fences_running_or_preserves_completed_job(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_before_rollover: bool,
    private_acquisition_exists: bool,
) -> None:
    started = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
    attempt_id = "attempt-2"
    context = FormalDiagnosisJobContextV0233.build(
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=2,
        attempt_id=attempt_id,
        diagnosis_generation=1,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
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
    product_root = tmp_path / run_command._attempt_product_locator_v0233(attempt_id)
    product_root.mkdir(parents=True)
    store = SqliteStoreV1(product_root / "product.sqlite3")
    jobs = JobRepositoryV1(store)
    raw_runtime = PilotRuntimeSnapshotV02.build(
        environment_id="env-" + "1" * 24,
        authority_sha256=_sha("0"),
        observed_at=checkpoint.incident_observation_ended_at,
        services={
            "checkout": {
                "state": RuntimeStateV22.RUNNING,
                "healthy": True,
                "restart_count": 0,
            }
        },
    )
    live_capture = LiveCaptureBundleV0233.build(
        campaign_id=context.campaign_id,
        semantic_generation=context.semantic_generation,
        attempt_id=attempt_id,
        formal_clone_sha256=_sha("1"),
        source_selection_sha256=_sha("2"),
        runtime_authority_proof_sha256=_sha("3"),
        baseline_restart_proof_sha256=_sha("4"),
        traffic_contract_sha256=_sha("a"),
        formal_profile_sha256=_sha("b"),
        formal_traffic_result_sha256=_sha("c"),
        traffic_execution_sha256=_sha("d"),
        episode_started_at=checkpoint.incident_observation_started_at,
        episode_ended_at=checkpoint.incident_observation_ended_at,
        fresh_runtime_snapshot_raw=raw_runtime,
        runtime_connector_binding_sha256=_sha("0"),
        queue_before_sha256=_sha("e"),
        queue_after_sha256=_sha("e"),
        outer_baseline_before_sha256=_sha("f"),
        outer_baseline_after_sha256=_sha("f"),
        active_profile_sha256=checkpoint.active_profile_sha256,
        active_baseline_id="base-" + "1" * 24,
        active_baseline_sha256=checkpoint.baseline_sha256,
        service_identity_sha256=checkpoint.service_identity_sha256,
        capability_sha256=checkpoint.capability_sha256,
        semantic_surface_sha256=checkpoint.semantic_surface_sha256,
    )
    rebound = context.model_copy(
        update={"acquisition_sha256": checkpoint.acquisition_sha256}
    )
    initial_idempotency_key = (
        "formal-v0233-acquisition-"
        f"{live_capture.live_capture_bundle_sha256[:32]}"
    )
    final_idempotency_key = final_diagnosis_idempotency_key_v0233(
        context=rebound,
        incident_sha256=checkpoint.incident_sha256,
        acquisition_sha256=checkpoint.acquisition_sha256,
    )
    job = jobs.enqueue(
        ProductJobTypeV1.DIAGNOSIS,
        {
            "incident_id": checkpoint.incident_id,
            "formal_recovery_v0233": context.model_dump(mode="json"),
        },
        idempotency_key=initial_idempotency_key,
        now=started.timestamp(),
    )
    claimed = jobs.claim_next(
        "worker-before-rollover",
        lease_seconds=3600,
        now=started.timestamp() + 1,
    )
    assert claimed is not None
    rebound_job = jobs.bind_idempotency_key(
        claimed.job_id,
        str(claimed.claimed_by),
        claimed.attempt_count,
        final_idempotency_key,
        now=started.timestamp() + 1.5,
    )
    assert job.idempotency_key == initial_idempotency_key
    assert rebound_job.idempotency_key == final_idempotency_key
    journal = DiagnosisStageJournalRepositoryV02322(store)
    claimed_event = journal.append(
        journal_id="journal-" + "1" * 24,
        job_id=job.job_id,
        incident_id=checkpoint.incident_id,
        stage=DiagnosisPipelineStageV02322.JOB_CLAIMED,
        status=DiagnosisStageStatusV02322.PASSED,
        input_binding_sha256=_sha("a"),
        output_artifact_sha256=None,
        source_code_sha256=_sha("b"),
        observed_at=started + timedelta(seconds=1),
    )
    if terminal_before_rollover:
        jobs.succeed(
            claimed.job_id,
            str(claimed.claimed_by),
            claimed.attempt_count,
            {"result_sha256": _sha("c")},
            now=started.timestamp() + 2,
        )
        journal.append(
            journal_id=claimed_event.journal_id,
            job_id=job.job_id,
            incident_id=checkpoint.incident_id,
            stage=DiagnosisPipelineStageV02322.JOB_SUCCEEDED,
            status=DiagnosisStageStatusV02322.PASSED,
            input_binding_sha256=_sha("c"),
            output_artifact_sha256=None,
            source_code_sha256=_sha("b"),
            observed_at=started + timedelta(seconds=2),
        )
    private_root = (
        tmp_path / run_command._attempt_private_locator_v0233(attempt_id) / "execution"
    )
    write_private_json(
        private_root / "live-capture-bundle.json",
        live_capture.model_dump(mode="json"),
        create_once=True,
    )
    acquisition_path = private_root / "diagnosis-acquisition-checkpoint.json"
    if private_acquisition_exists:
        write_private_json(
            acquisition_path,
            checkpoint.model_dump(mode="json"),
            create_once=True,
        )
    else:
        write_private_json(
            product_root / context.acquisition_checkpoint_locator,
            checkpoint.model_dump(mode="json"),
            create_once=True,
        )
        write_private_json(
            private_root / "diagnosis-job.json",
            job.model_dump(mode="json"),
            create_once=True,
        )
    monkeypatch.setattr(
        resume_command,
        "_recover_owned_product_processes_v0233",
        lambda **_kwargs: {"verdict": "CLEAN"},
    )

    paths = resume_command._reconcile_semantic_rollover_lineage_v0233(
        root=tmp_path,
        attempt_id=attempt_id,
        latest=SimpleNamespace(
            semantic_generation=2,
            semantic_surface_sha256=_sha("5"),
            source_selection_sha256=_sha("2"),
        ),
        successor_semantic_surface_sha256=_sha("d"),
    )

    observed = jobs.get(job.job_id)
    lineage_path = private_root / "interrupted-diagnosis-lineage.json"
    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    assert acquisition_path in paths
    assert DiagnosisAcquisitionCheckpointV0233.model_validate_json(
        acquisition_path.read_bytes()
    ) == checkpoint
    assert lineage_path in paths
    assert lineage["terminal_job_count"] == 1
    if terminal_before_rollover:
        assert observed.status is ProductJobStatusV1.SUCCEEDED
        assert observed.result == {"result_sha256": _sha("c")}
        assert lineage["failed_job_count"] == 0
        assert lineage["successful_job_count"] == 1
    else:
        assert observed.status is ProductJobStatusV1.FAILED
        assert observed.safe_error_code == "FORMAL_SEMANTIC_GENERATION_CHANGED"
        assert lineage["failed_job_count"] == 1
        assert lineage["successful_job_count"] == 0
        with pytest.raises(ProductError) as lost:
            jobs.succeed(
                claimed.job_id,
                str(claimed.claimed_by),
                claimed.attempt_count,
                {"result_sha256": _sha("e")},
                now=started.timestamp() + 3,
            )
        assert lost.value.code == "JOB_LEASE_LOST"


def test_resume_reuses_unfinished_generation_and_advances_after_failure(
    tmp_path,
) -> None:
    started = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
    context = FormalDiagnosisJobContextV0233.build(
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=2,
        attempt_id="attempt-2",
        diagnosis_generation=1,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
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
    assert (
        jobs.fail(
            queued.job_id,
            "worker-recovery",
            reclaimed.attempt_count,
            "FORMAL_WORKER_INTERRUPTED",
            now=14.0,
        ).status.value
        == "FAILED"
    )


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
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
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
    assert failed.failure_stage == "BRIDGE_DIAGNOSIS_STARTED"
    assert events[-1].stage.value == "FAILED"
    assert events[-1].event_sha256 == failed.journal_tail_sha256
    failure_files = tuple(
        (tmp_path / "private/diagnosis-failures" / queued.job_id).glob("*.json")
    )
    assert len(failure_files) == 1
    envelope = DiagnosisPrivateFailureEnvelopeV02322.model_validate_json(
        failure_files[0].read_bytes()
    )
    assert (
        envelope.last_passed_stage
        is DiagnosisPipelineStageV02322.READ_ACQUISITION_COMPLETED
    )
    assert (
        envelope.failing_stage is DiagnosisPipelineStageV02322.BRIDGE_DIAGNOSIS_STARTED
    )

    projection = run_command._job_lineage_projection_v0233(
        failed,
        product_root=tmp_path,
    )
    assert projection["failure_stage"] == "BRIDGE_DIAGNOSIS_STARTED"
    assert projection["last_passed_stage"] == "READ_ACQUISITION_COMPLETED"
    assert projection["interruption_after_stage"] == "READ_ACQUISITION_COMPLETED"
    assert projection["formal_recovery_context"] == context.model_dump(mode="json")
    assert (
        projection["private_failure_envelope_sha256"]
        == envelope.failure_envelope_sha256
    )


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
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
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
