from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ecomsre.dta_v2.v22.action_catalog import (
    EvidenceActionV22,
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
from ecomsre.product.contracts import ConnectorKindV1
from ecomsre.product.incidents.contracts import (
    ActionAuthorityV1,
    DiagnosisLaneV1,
    DiagnosisResultV1,
    DiagnosisTerminalV1,
    EvidenceBundleV1,
)
from ecomsre.product.incidents.evidence_binding_v0232 import (
    ConnectorEvidenceBindingV0232,
    DiagnosisDecisionTraceV0232,
    DiagnosisEvidenceIndexV0232,
    OpenSearchProfileEvidenceBindingV0232,
    RuntimeSnapshotEvidenceBindingV0232,
)
from ecomsre.product.incidents.read_backend import ProductReadAcquisitionV1
from ecomsre.product.pilot.fresh_formal_acceptance_v0233 import (
    DiagnosisPipelineAcceptanceV0233,
)
from ecomsre.product.pilot.formal_recovery_v0233 import (
    FormalAttemptLedgerV0233,
    FormalAttemptRecordV0233,
    FormalExecutionCheckpointV0233,
    FormalExecutionStateV0233,
)
from ecomsre.product.pilot.diagnosis_recovery_v0233 import (
    FormalDiagnosisJobContextV0233,
    build_diagnosis_acquisition_checkpoint_v0233,
    final_diagnosis_idempotency_key_v0233,
)
from ecomsre.product.pilot.repository_state_v0233 import RepositoryPhaseV0233
from ecomsre.product.pilot.nofault_acceptance_v0232 import (
    score_nofault_evidence_v0232,
)
from scripts.ci import verify_product_v0233_terminal as verifier


def _sha(character: str) -> str:
    return character * 64


def _sealed(body: dict[str, object], field: str) -> dict[str, object]:
    return {**body, field: semantic_sha256_v22(body)}


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validated_as(value: object) -> SimpleNamespace:
    return SimpleNamespace(model_validate_json=lambda _payload: value)


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


def _acquisition(incident_id: str) -> ProductReadAcquisitionV1:
    started = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
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
    runtime_binding = RuntimeSnapshotEvidenceBindingV0232.build(
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
    runtime_generic = ConnectorEvidenceBindingV0232.build(
        binding_id="binding:v0232:" + "1" * 24,
        incident_id=incident_id,
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
        binding_payload_sha256=runtime_binding.binding_sha256,
    )
    profile_binding = OpenSearchProfileEvidenceBindingV0232.build(
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
        incident_id=incident_id,
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
        binding_payload_sha256=profile_binding.binding_sha256,
    )
    runtime_snapshot = {
        "schema_version": "ecomsre.product.read-snapshot.v1",
        "incident_id": incident_id,
        "action": runtime_action.model_dump(mode="json"),
        "connector_components": [runtime_result.model_dump(mode="json")],
        "connector_diagnostics": [],
        "connector_bindings_v0232": [
            {
                "connector_binding": runtime_generic.model_dump(mode="json"),
                "binding_payload": runtime_binding.model_dump(mode="json"),
            }
        ],
        "connector_result": runtime_result.model_dump(mode="json"),
        "read_outcome": runtime_outcome.model_dump(mode="json"),
        "memory_outcome": None,
    }
    logs_snapshot = {
        "schema_version": "ecomsre.product.read-snapshot.v1",
        "incident_id": incident_id,
        "action": logs_action.model_dump(mode="json"),
        "connector_components": [logs_result.model_dump(mode="json")],
        "connector_diagnostics": [],
        "connector_bindings_v0232": [
            {
                "connector_binding": profile_generic.model_dump(mode="json"),
                "binding_payload": profile_binding.model_dump(mode="json"),
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


@pytest.mark.parametrize("recovery_required", (False, True))
def test_public_measured_verifier_binds_direct_and_recovery_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recovery_required: bool,
) -> None:
    attempt_id = "attempt-2"
    terminal = "ECOMSRE_PRODUCT_V0233_NOFAULT_NOT_SUPPORTED"
    attempt_root = tmp_path / "docs/analysis/product-v0233-attempts" / attempt_id
    result_path = tmp_path / "docs/results/product-v0233-nofault-acceptance.json"
    incident_id = "inc-" + "1" * 24
    failed_job_id = "job-" + "2" * 24
    successful_job_id = "job-" + "3" * 24
    diagnosis_id = "diag-" + "4" * 24
    diagnosis_body = {
        "schema_version": "ecomsre.product.diagnosis-result.v1",
        "diagnosis_id": diagnosis_id,
        "incident_id": incident_id,
        "terminal": DiagnosisTerminalV1.NO_INCIDENT,
        "core_or_extension_or_open_world": DiagnosisLaneV1.NO_INCIDENT,
        "root_service_ids": (),
        "mechanism": None,
        "broad_domain": None,
        "supporting_evidence_refs": (),
        "contradicting_evidence_refs": (),
        "capability_limitations": (),
        "provisional_report": None,
        "action_authority": ActionAuthorityV1.NONE,
        "agent_writes": 0,
        "runbook_executions": 0,
        "provider_calls": 0,
        "memory_sha256": None,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    normalized_diagnosis = DiagnosisResultV1.model_construct(
        **diagnosis_body,
        result_sha256="0" * 64,
    ).model_dump(mode="json", exclude={"result_sha256"})
    diagnosis = DiagnosisResultV1.model_validate(
        {
            **diagnosis_body,
            "result_sha256": semantic_sha256_v22(normalized_diagnosis),
        }
    )
    diagnosis_sha256 = diagnosis.result_sha256
    evidence_bundle = EvidenceBundleV1(
        incident_id=incident_id,
        diagnosis_id=diagnosis_id,
        objects=(),
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
    )
    evidence_sha256 = semantic_sha256_v22(evidence_bundle.model_dump(mode="json"))
    decision_trace = DiagnosisDecisionTraceV0232.build(
        incident_id=incident_id,
        diagnosis_id=diagnosis_id,
        known_admission_status="NONE",
        extension_match_count=0,
        no_incident_admissible=True,
        required_coverage_satisfied=False,
        failed_sources=(),
        novelty_gate_disposition=None,
        novelty_gate_reason_codes=(),
        residual_anomaly_ids=(),
    )
    evidence_index = DiagnosisEvidenceIndexV0232.build(
        incident_id=incident_id,
        diagnosis_id=diagnosis_id,
        evidence_bundle_sha256=evidence_sha256,
        all_object_refs=(),
        all_object_sha256_by_ref={},
        linked_support_refs=(),
        linked_contradiction_refs=(),
        successful_source_refs=(),
        failed_source_refs=(),
        open_search_profile_binding_ref=None,
        runtime_snapshot_binding_ref=None,
        capability_limitation_bindings=(),
        decision_trace_sha256=decision_trace.trace_sha256,
    )
    assessment = score_nofault_evidence_v0232(
        diagnosis=diagnosis,
        bundle=evidence_bundle,
        index=evidence_index,
        decision_trace=decision_trace,
    )
    journal_tail_sha256 = _sha("9")

    initial_context = FormalDiagnosisJobContextV0233.build(
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=2,
        attempt_id=attempt_id,
        diagnosis_generation=1,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        semantic_surface_sha256=_sha("b"),
        acquisition_sha256=None,
    )
    acquisition_checkpoint = build_diagnosis_acquisition_checkpoint_v0233(
        context=initial_context,
        acquisition=_acquisition(incident_id),
        incident_id=incident_id,
        incident_sha256=_sha("e"),
        incident_observation_started_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        incident_observation_ended_at=datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
        baseline_sha256=_sha("b"),
        service_identity_sha256=_sha("c"),
        capability_sha256=_sha("d"),
    )
    acquisition_sha256 = acquisition_checkpoint.acquisition_sha256
    failed_context = initial_context
    successful_context = (
        FormalDiagnosisJobContextV0233.build(
            campaign_id="product-v0233-fresh-formal-nofault",
            semantic_generation=2,
            attempt_id=attempt_id,
            diagnosis_generation=2,
            active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
            semantic_surface_sha256=_sha("b"),
            acquisition_sha256=acquisition_sha256,
        )
        if recovery_required
        else failed_context
    )
    failed_payload = {
        "incident_id": incident_id,
        "formal_recovery_v0233": failed_context.model_dump(mode="json"),
    }
    successful_payload = {
        "incident_id": incident_id,
        "formal_recovery_v0233": successful_context.model_dump(mode="json"),
    }

    failed_job_body = {
        "job_id": failed_job_id,
        "job_type": "DIAGNOSIS",
        "status": "FAILED",
        "idempotency_key": final_diagnosis_idempotency_key_v0233(
            context=failed_context,
            incident_sha256=_sha("e"),
            acquisition_sha256=acquisition_sha256,
        ),
        "attempt_count": 1,
        "incident_id": incident_id,
        "formal_recovery_context": failed_context.model_dump(mode="json"),
        "payload_sha256": semantic_sha256_v22(failed_payload),
        "result_sha256": None,
        "diagnosis_result_sha256": None,
        "safe_error_code": "FORMAL_WORKER_INTERRUPTED",
        "failure_stage": "BRIDGE_DIAGNOSIS_STARTED",
        "last_passed_stage": "READ_ACQUISITION_COMPLETED",
        "interruption_after_stage": "READ_ACQUISITION_COMPLETED",
        "exception_fingerprint": _sha("2"),
        "journal_tail_sha256": _sha("3"),
    }
    failed_job = _sealed(failed_job_body, "projection_sha256")
    successful_job_body = {
        "job_id": successful_job_id,
        "job_type": "DIAGNOSIS",
        "status": "SUCCEEDED",
        "idempotency_key": final_diagnosis_idempotency_key_v0233(
            context=successful_context,
            incident_sha256=_sha("e"),
            acquisition_sha256=acquisition_sha256,
        ),
        "attempt_count": 1,
        "incident_id": incident_id,
        "formal_recovery_context": successful_context.model_dump(mode="json"),
        "payload_sha256": semantic_sha256_v22(successful_payload),
        "result_sha256": _sha("5"),
        "diagnosis_result_sha256": diagnosis_sha256,
        "safe_error_code": None,
        "failure_stage": None,
        "last_passed_stage": "JOB_SUCCEEDED",
        "interruption_after_stage": None,
        "exception_fingerprint": None,
        "journal_tail_sha256": journal_tail_sha256,
    }
    successful_job = _sealed(successful_job_body, "projection_sha256")
    failed_job_ids = [failed_job_id] if recovery_required else []
    failed_job_projections = [failed_job] if recovery_required else []
    lineage_body = {
        "schema_version": "ecomsre.product.diagnosis-recovery-lineage.v0233",
        "attempt_id": attempt_id,
        "incident_id": incident_id,
        "incident_sha256": _sha("e"),
        "acquisition_sha256": acquisition_sha256,
        "preserved_failed_job_ids": failed_job_ids,
        "preserved_failed_jobs": failed_job_projections,
        "successful_job_id": successful_job_id,
        "successful_job": successful_job,
        "successful_diagnosis_generation": 2 if recovery_required else 1,
        "diagnosis_result_sha256": diagnosis_sha256,
    }
    lineage = _sealed(lineage_body, "lineage_sha256")
    index_sha256 = evidence_index.index_sha256
    decision_trace_sha256 = decision_trace.trace_sha256
    pipeline_acceptance = DiagnosisPipelineAcceptanceV0233.build_success(
        job_id=successful_job_id,
        journal_tail_sha256=journal_tail_sha256,
        event_count=1,
        diagnosis_result_sha256=diagnosis_sha256,
        evidence_bundle_sha256=evidence_sha256,
        evidence_index_sha256=index_sha256,
        decision_trace_sha256=decision_trace_sha256,
    )
    pipeline_body = {
        **pipeline_acceptance.model_dump(mode="json"),
        "terminal": "ECOMSRE_PRODUCT_V0233_DIAGNOSIS_PIPELINE_PASS",
    }
    pipeline = _sealed(pipeline_body, "public_projection_sha256")
    result_sha256 = _sha("r")
    handoff_body = {
        "terminal": "ECOMSRE_PRODUCT_V0233_KNOWLEDGE_LOOP_HANDOFF_NOT_AUTHORIZED",
        "nofault_result_sha256": result_sha256,
        "measured_terminal": terminal,
        "knowledge_loop_campaigns": 0,
        "action_authority": "NONE",
    }
    handoff = _sealed(handoff_body, "handoff_sha256")
    progress_body = {
        "measured_terminal": terminal,
        "nofault_result_sha256": result_sha256,
        "new_incident_count": 1,
        "new_diagnosis_count": 2 if recovery_required else 1,
        "measured_result_count": 1,
    }
    progress = _sealed(progress_body, "progress_sha256")
    checkpoint_states = (
        "PREPARED",
        "CLONE_SEALED",
        "FORMAL_ENVIRONMENT_READY",
        "FORMAL_TRAFFIC_RUNNING",
        "FORMAL_TRAFFIC_PASS",
        "LIVE_CAPTURE_SEALED",
        "INCIDENT_CREATED",
        "ACQUISITION_SEALED",
        "DIAGNOSIS_RUNNING",
        "DIAGNOSIS_PERSISTED",
        "SCORED",
        "CLOSED",
    )
    checkpoint_records: list[FormalExecutionCheckpointV0233] = []
    previous_checkpoint = None
    checkpoint_time = datetime(2026, 1, 1, tzinfo=UTC)
    for sequence, state in enumerate(checkpoint_states, start=1):
        checkpoint = FormalExecutionCheckpointV0233.build(
            previous=previous_checkpoint,
            state=FormalExecutionStateV0233(state),
            campaign_id="product-v0233-fresh-formal-nofault",
            semantic_generation=2,
            attempt_id=attempt_id,
            semantic_surface_sha256=_sha("b"),
            operational_surface_sha256=_sha("c"),
            source_selection_sha256=_sha("d"),
            formal_clone_sha256=(None if sequence == 1 else _sha("1")),
            input_artifact_sha256s={},
            output_artifact_sha256s={},
            created_at=checkpoint_time + timedelta(seconds=sequence),
        )
        checkpoint_records.append(checkpoint)
        previous_checkpoint = checkpoint
    checkpoint_hashes = tuple(
        checkpoint.checkpoint_sha256 for checkpoint in checkpoint_records
    )
    checkpoint_chain_body = {
        "schema_version": "ecomsre.product.checkpoint-chain.v0233",
        "attempt_id": attempt_id,
        "semantic_generation": 2,
        "checkpoint_count": len(checkpoint_states),
        "checkpoints": [
            checkpoint.model_dump(mode="json") for checkpoint in checkpoint_records
        ],
        "latest_checkpoint_sha256": checkpoint_hashes[-1],
    }
    checkpoint_chain = _sealed(checkpoint_chain_body, "chain_sha256")

    for name, payload in (
        ("formal-state-clone.json", {}),
        ("pre-execution-review.json", {}),
        ("checkpoint-chain.json", checkpoint_chain),
        ("runtime-authority.json", {}),
        ("baseline-restart.json", {}),
        ("formal-traffic.json", {}),
        ("fresh-runtime-snapshot.json", {}),
        ("incident-traffic-binding.json", {}),
        ("evidence-assessment.json", assessment.model_dump(mode="json")),
        ("formal-closure.json", {}),
        ("live-capture-bundle.json", {}),
        (
            "diagnosis-acquisition-checkpoint.json",
            acquisition_checkpoint.model_dump(mode="json"),
        ),
        ("diagnosis-recovery-lineage.json", lineage),
        ("diagnosis-result.json", diagnosis.model_dump(mode="json")),
        ("evidence-bundle.json", evidence_bundle.model_dump(mode="json")),
        ("evidence-index.json", evidence_index.model_dump(mode="json")),
        ("decision-trace.json", decision_trace.model_dump(mode="json")),
        ("diagnosis-stage-journal.json", pipeline),
        ("knowledge-loop-handoff.json", handoff),
    ):
        _write_json(attempt_root / name, payload)
    _write_json(result_path, {})
    _write_json(
        tmp_path / "config/product-v0233/recovery-repository-state-manifest.json",
        {},
    )
    _write_json(
        tmp_path / "docs/analysis/product-v0233-recovery-progress.json", progress
    )
    for relative in (
        "docs/results/product-v0233-nofault-acceptance.md",
        "docs/results/product-v0233-limitations.md",
        "docs/results/product-v0233-interview-brief.md",
        "docs/analysis/product-v0233-knowledge-loop-handoff.md",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("verified recovery evidence\n", encoding="utf-8")

    evidence_paths = (
        *tuple(path for path in attempt_root.iterdir() if path.is_file()),
        result_path,
        tmp_path / "docs/results/product-v0233-nofault-acceptance.md",
        tmp_path / "docs/results/product-v0233-limitations.md",
        tmp_path / "docs/results/product-v0233-interview-brief.md",
        tmp_path / "docs/analysis/product-v0233-knowledge-loop-handoff.md",
    )
    evidence = {
        path.relative_to(tmp_path).as_posix(): _sha256_file(path)
        for path in evidence_paths
    }
    legacy = FormalAttemptRecordV0233.build(
        attempt_id="attempt-1",
        ordinal=1,
        semantic_generation=1,
        disposition="LEGACY_BLOCKED",
        latest_state=FormalExecutionStateV0233.RECOVERABLE_FAILURE,
        latest_checkpoint_sha256=None,
        blocker_terminal="BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS",
        measured_terminal=None,
        evidence_sha256_by_path={},
    )
    measured = FormalAttemptRecordV0233.build(
        attempt_id=attempt_id,
        ordinal=2,
        semantic_generation=2,
        disposition="MEASURED",
        latest_state=FormalExecutionStateV0233.CLOSED,
        latest_checkpoint_sha256=checkpoint_hashes[-1],
        blocker_terminal=None,
        measured_terminal=terminal,
        evidence_sha256_by_path=evidence,
    )
    ledger = FormalAttemptLedgerV0233.build(
        campaign_id="product-v0233-fresh-formal-nofault",
        attempts=(legacy, measured),
    )
    _write_json(
        tmp_path / "config/product-v0233/formal-attempt-ledger.json",
        ledger.model_dump(mode="json"),
    )

    result = SimpleNamespace(
        measured_terminal=terminal,
        result_sha256=result_sha256,
        diagnosis_result_sha256=diagnosis_sha256,
        evidence_bundle_sha256=evidence_sha256,
        evidence_index_sha256=index_sha256,
        decision_trace_sha256=decision_trace_sha256,
        incident_sha256=_sha("e"),
        stage_journal_tail_sha256=journal_tail_sha256,
        formal_clone_sha256=_sha("1"),
        runtime_authority_proof_sha256=_sha("2"),
        baseline_restart_proof_sha256=_sha("3"),
        formal_traffic_execution_sha256=_sha("4"),
        fresh_runtime_snapshot_sha256=_sha("2"),
        incident_traffic_binding_sha256=_sha("6"),
        v0232_assessment_sha256=assessment.result_sha256,
        reasons=assessment.reasons,
        cleanup_proof_sha256=_sha("8"),
        safety_counters=SimpleNamespace(
            model_dump=lambda mode="json": {
                "agent_writes": 0,
                "runbook_executions": 0,
                "provider_calls": 0,
                "fault_attempts": 0,
                "knowledge_loop_executions": 0,
            }
        ),
    )
    repository = SimpleNamespace(
        phase=RepositoryPhaseV0233.MEASURED_COMPLETE,
        formal_result_sha256=result_sha256,
        formal_blocker_sha256=None,
        cleanup_proof_sha256=_sha("8"),
        formal_clone_count=1,
        formal_execution_count=1,
        new_incident_count=1,
        new_diagnosis_count=2 if recovery_required else 1,
        measured_result_count=1,
    )
    monkeypatch.setattr(
        verifier, "build_legacy_attempt1_record_v0233", lambda _root: legacy
    )
    monkeypatch.setattr(verifier, "NoFaultAcceptanceResultV0233", _validated_as(result))
    monkeypatch.setattr(
        verifier,
        "FreshFormalStateCloneV0233",
        _validated_as(SimpleNamespace(clone_sha256=_sha("1"))),
    )
    monkeypatch.setattr(
        verifier,
        "RuntimeAuthorityProofV0233",
        _validated_as(
            SimpleNamespace(
                proof_sha256=_sha("2"),
                runtime_connector_binding_sha256=_sha("3"),
                pilot_runtime_authority_sha256=_sha("4"),
                runtime_continuity_descriptor_sha256=_sha("f"),
            )
        ),
    )
    monkeypatch.setattr(
        verifier,
        "BaselineRestartProofV0233",
        _validated_as(
            SimpleNamespace(
                proof_sha256=_sha("3"),
                environment_id="env-" + "1" * 24,
                active_baseline_id="base-" + "1" * 24,
                active_baseline_sha256=_sha("b"),
                active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
            )
        ),
    )
    monkeypatch.setattr(
        verifier,
        "FormalTrafficResultV0233",
        _validated_as(
            SimpleNamespace(
                result_sha256=_sha("7"),
                traffic_contract_sha256=_sha("a"),
                formal_profile_sha256=_sha("f"),
                execution=SimpleNamespace(
                    execution_sha256=_sha("4"),
                    run=SimpleNamespace(successful_transactions=30),
                ),
            )
        ),
    )
    monkeypatch.setattr(
        verifier,
        "FreshRuntimeSnapshotProofV0233",
        _validated_as(
            SimpleNamespace(
                runtime_snapshot_sha256=_sha("2"),
                observed_at=datetime(2026, 1, 1, 0, 1, 30, tzinfo=UTC),
                pilot_runtime_authority_sha256=_sha("4"),
                runtime_continuity_descriptor_sha256=_sha("f"),
                runtime_connector_binding_sha256=_sha("3"),
            )
        ),
    )
    live_capture = SimpleNamespace(
        campaign_id=ledger.campaign_id,
        attempt_id=attempt_id,
        semantic_generation=2,
        semantic_surface_sha256=_sha("b"),
        source_selection_sha256=_sha("d"),
        formal_clone_sha256=_sha("1"),
        runtime_authority_proof_sha256=_sha("2"),
        baseline_restart_proof_sha256=_sha("3"),
        traffic_contract_sha256=_sha("a"),
        formal_profile_sha256=_sha("f"),
        formal_traffic_result_sha256=_sha("7"),
        traffic_execution_sha256=_sha("4"),
        fresh_runtime_snapshot_raw=SimpleNamespace(
            snapshot_sha256=_sha("2"),
            authority_sha256=_sha("3"),
            environment_id="env-" + "1" * 24,
            observed_at=datetime(2026, 1, 1, 0, 1, 30, tzinfo=UTC),
            services=(
                SimpleNamespace(
                    logical_service="checkout",
                    state=SimpleNamespace(value="RUNNING"),
                    healthy=True,
                    restart_count=0,
                ),
            ),
        ),
        runtime_connector_binding_sha256=_sha("3"),
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        active_baseline_id="base-" + "1" * 24,
        active_baseline_sha256=_sha("b"),
        service_identity_sha256=_sha("c"),
        capability_sha256=_sha("d"),
        episode_started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(
        verifier,
        "LiveCaptureBundleV0233",
        _validated_as(live_capture),
    )
    monkeypatch.setattr(
        verifier,
        "IncidentTrafficBindingV0232",
        _validated_as(
            SimpleNamespace(incident_id=incident_id, binding_sha256=_sha("6"))
        ),
    )
    monkeypatch.setattr(
        verifier,
        "FormalClosureProofV0233",
        _validated_as(
            SimpleNamespace(
                closure_sha256=_sha("8"),
                safety_observation=SimpleNamespace(
                    safe=True,
                    new_incident_count=1,
                    new_diagnosis_count=2 if recovery_required else 1,
                ),
                verdict="CLEAN",
            )
        ),
    )
    _write_json(
        tmp_path / "docs/analysis/product-v0233-recovery-pre-execution-review.json",
        {},
    )
    _write_json(tmp_path / "config/product-v0233/source-selection.json", {})
    monkeypatch.setattr(
        verifier,
        "RecoveryPreExecutionReviewV0233",
        _validated_as(
            SimpleNamespace(
                semantic_generation=2,
                semantic_surface_sha256=_sha("b"),
                operational_surface_sha256=_sha("c"),
            )
        ),
    )
    monkeypatch.setattr(
        verifier,
        "FreshFormalSourceSelectionV0233",
        _validated_as(
            SimpleNamespace(
                active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
                active_baseline_id="base-" + "1" * 24,
                active_baseline_sha256=_sha("b"),
                selection_sha256=_sha("d"),
            )
        ),
    )
    monkeypatch.setattr(
        verifier,
        "ProductV0233RepositoryStateManifest",
        _validated_as(repository),
    )

    observed = verifier.verify_product_v0233_terminal(tmp_path)

    assert observed["terminal"] == "ECOMSRE_PRODUCT_V0233_NOFAULT_ACCEPTANCE_COMPLETE"
    assert observed["failed_diagnosis_job_count"] == (1 if recovery_required else 0)
    assert observed["new_diagnosis_count"] == (2 if recovery_required else 1)

    mismatched_live_capture = SimpleNamespace(
        **{
            **vars(live_capture),
            "fresh_runtime_snapshot_raw": SimpleNamespace(
                **{
                    **vars(live_capture.fresh_runtime_snapshot_raw),
                    "snapshot_sha256": _sha("5"),
                }
            ),
        }
    )
    monkeypatch.setattr(
        verifier,
        "LiveCaptureBundleV0233",
        _validated_as(mismatched_live_capture),
    )
    with pytest.raises(ValueError, match="recovered terminal"):
        verifier.verify_product_v0233_terminal(tmp_path)
    monkeypatch.setattr(
        verifier,
        "LiveCaptureBundleV0233",
        _validated_as(live_capture),
    )

    unsealed_intermediate = FormalAttemptRecordV0233.build(
        attempt_id="attempt-2",
        ordinal=2,
        semantic_generation=2,
        disposition="RECOVERABLE_FAILURE",
        latest_state=FormalExecutionStateV0233.RECOVERABLE_FAILURE,
        latest_checkpoint_sha256=_sha("a"),
        blocker_terminal="BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_REQUIRED",
        measured_terminal=None,
        evidence_sha256_by_path={},
    )
    shifted_measured = FormalAttemptRecordV0233.build(
        **{
            **measured.model_dump(mode="json", exclude={"record_sha256"}),
            "attempt_id": "attempt-3",
            "ordinal": 3,
        }
    )
    invalid_history = FormalAttemptLedgerV0233.build(
        campaign_id=ledger.campaign_id,
        attempts=(legacy, unsealed_intermediate, shifted_measured),
    )
    _write_json(
        tmp_path / "config/product-v0233/formal-attempt-ledger.json",
        invalid_history.model_dump(mode="json"),
    )
    with pytest.raises(ValueError, match="attempt ledger"):
        verifier.verify_product_v0233_terminal(tmp_path)
    _write_json(
        tmp_path / "config/product-v0233/formal-attempt-ledger.json",
        ledger.model_dump(mode="json"),
    )

    contradictory_result = SimpleNamespace(
        **{**vars(result), "reasons": ("CONTRADICTORY_MEASURED_CLAIM",)}
    )
    monkeypatch.setattr(
        verifier,
        "NoFaultAcceptanceResultV0233",
        _validated_as(contradictory_result),
    )
    with pytest.raises(ValueError, match="recovered terminal"):
        verifier.verify_product_v0233_terminal(tmp_path)
    monkeypatch.setattr(
        verifier,
        "NoFaultAcceptanceResultV0233",
        _validated_as(result),
    )
    contradictory_closure = SimpleNamespace(
        closure_sha256=_sha("8"),
        safety_observation=SimpleNamespace(
            safe=True,
            new_incident_count=1,
            new_diagnosis_count=99,
        ),
        verdict="CLEAN",
    )
    monkeypatch.setattr(
        verifier,
        "FormalClosureProofV0233",
        _validated_as(contradictory_closure),
    )
    with pytest.raises(ValueError, match="recovered terminal"):
        verifier.verify_product_v0233_terminal(tmp_path)
    monkeypatch.setattr(
        verifier,
        "FormalClosureProofV0233",
        _validated_as(
            SimpleNamespace(
                closure_sha256=_sha("8"),
                safety_observation=SimpleNamespace(
                    safe=True,
                    new_incident_count=1,
                    new_diagnosis_count=2 if recovery_required else 1,
                ),
                verdict="CLEAN",
            )
        ),
    )

    omitted_path, *_remaining_paths = measured.evidence_sha256_by_path
    incomplete = FormalAttemptRecordV0233.build(
        **{
            **measured.model_dump(mode="json", exclude={"record_sha256"}),
            "evidence_sha256_by_path": {
                path: digest
                for path, digest in measured.evidence_sha256_by_path.items()
                if path != omitted_path
            },
        }
    )
    incomplete_ledger = FormalAttemptLedgerV0233.build(
        campaign_id=ledger.campaign_id,
        attempts=(legacy, incomplete),
    )
    _write_json(
        tmp_path / "config/product-v0233/formal-attempt-ledger.json",
        incomplete_ledger.model_dump(mode="json"),
    )
    with pytest.raises(ValueError, match="recovered terminal"):
        verifier.verify_product_v0233_terminal(tmp_path)
    _write_json(
        tmp_path / "config/product-v0233/formal-attempt-ledger.json",
        ledger.model_dump(mode="json"),
    )

    lineage["successful_job_id"] = "job-" + "9" * 24
    _write_json(attempt_root / "diagnosis-recovery-lineage.json", lineage)
    with pytest.raises(ValueError, match="recovered terminal"):
        verifier.verify_product_v0233_terminal(tmp_path)
