#!/usr/bin/env python3
"""Verify the public Product v0.2.3.3 blocked repository terminal."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.incidents.contracts import DiagnosisResultV1, EvidenceBundleV1
from ecomsre.product.incidents.evidence_binding_v0232 import (
    DiagnosisDecisionTraceV0232,
    DiagnosisEvidenceIndexV0232,
)
from ecomsre.product.pilot.formal_live_v0233 import (
    BaselineRestartProofV0233,
    FormalClosureProofV0233,
    FormalExecutionBlockerV0233,
    FormalTrafficResultV0233,
    FreshRuntimeSnapshotProofV0233,
    RuntimeAuthorityProofV0233,
)
from ecomsre.product.pilot.fresh_formal_source_v0233 import (
    FreshFormalSourceSelectionV0233,
    FreshFormalStateCloneV0233,
)
from ecomsre.product.pilot.formal_recovery_v0233 import (
    DiagnosisAcquisitionCheckpointV0233,
    FormalAttemptLedgerV0233,
    RecoveryPreExecutionReviewV0233,
    build_legacy_attempt1_record_v0233,
)
from ecomsre.product.pilot.fresh_formal_acceptance_v0233 import (
    DiagnosisPipelineAcceptanceV0233,
    NoFaultAcceptanceResultV0233,
)
from ecomsre.product.pilot.healthy_traffic_v0232 import IncidentTrafficBindingV0232
from ecomsre.product.pilot.nofault_acceptance_v0232 import (
    NoFaultEvidenceAssessmentV0232,
    score_nofault_evidence_v0232,
)
from ecomsre.product.pilot.repository_state_v0233 import (
    ProductV0233RepositoryStateManifest,
    RepositoryPhaseV0233,
)


_TERMINAL = "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS"
_EXECUTION_HEAD = "466796648c2c4a3360b911a12be1ee806d39124e"
_MANIFEST_SHA256 = "08fdbd61e3fa439b55b1ef903bdea26dee6a3c839129bef53ee99c19a3c61014"
_V0233_TERMINAL_BY_V0232 = {
    "ECOMSRE_PRODUCT_V0232_NOFAULT_FULLY_SUPPORTED": (
        "ECOMSRE_PRODUCT_V0233_NOFAULT_FULLY_SUPPORTED"
    ),
    "ECOMSRE_PRODUCT_V0232_NOFAULT_CAPABILITY_LIMITED": (
        "ECOMSRE_PRODUCT_V0233_NOFAULT_CAPABILITY_LIMITED"
    ),
    "ECOMSRE_PRODUCT_V0232_NOFAULT_NOT_SUPPORTED": (
        "ECOMSRE_PRODUCT_V0233_NOFAULT_NOT_SUPPORTED"
    ),
}
_REQUIRED_ABSENCES = (
    "docs/analysis/product-v0233-fresh-runtime-snapshot.json",
    "docs/analysis/product-v0233-incident-traffic-binding.json",
    "docs/analysis/product-v0233-evidence-assessment.json",
    "docs/analysis/product-v0233-diagnosis-stage-journal.json",
    "docs/analysis/product-v0233-diagnosis-blocker.json",
    "docs/analysis/product-v0233-diagnosis-blocker.md",
    "docs/analysis/product-v0233-knowledge-loop-handoff.json",
    "docs/analysis/product-v0233-knowledge-loop-handoff.md",
    "docs/results/product-v0233-nofault-acceptance.json",
    "docs/results/product-v0233-nofault-acceptance.md",
    "docs/results/product-v0233-limitations.md",
    "docs/results/product-v0233-interview-brief.md",
)


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Product v0.2.3.3 JSON object differs: {path.name}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_public_file(
    root: Path,
    artifact: Mapping[str, Any],
    *,
    file_field: str = "file_sha256",
) -> Path:
    relative = artifact.get("public_path")
    if not isinstance(relative, str) or relative.startswith(("/", ".local/")):
        raise ValueError("Product v0.2.3.3 public evidence path differs")
    path = root / relative
    expected = artifact.get(file_field)
    if (
        path.is_symlink()
        or not path.is_file()
        or not isinstance(expected, str)
        or _sha256_file(path) != expected
    ):
        raise ValueError(f"Product v0.2.3.3 public evidence differs: {relative}")
    return path


def _verify_required_absences(root: Path, declared: list[object]) -> None:
    if tuple(declared) != _REQUIRED_ABSENCES:
        raise ValueError("Product v0.2.3.3 required absence inventory differs")
    for relative in _REQUIRED_ABSENCES:
        path = root / relative
        if path.exists() or path.is_symlink():
            raise ValueError(
                f"Product v0.2.3.3 forbidden terminal artifact exists: {relative}"
            )


def _verify_checkpoint_chain_v0233(
    payload: Mapping[str, Any],
    *,
    attempt_id: str,
    semantic_generation: int,
    latest_checkpoint_sha256: str,
    latest_state: str,
) -> None:
    body = {key: value for key, value in payload.items() if key != "chain_sha256"}
    checkpoints = payload.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        raise ValueError("Product v0.2.3.3 checkpoint chain differs")
    forward = {
        "PREPARED": "FORMAL_ENVIRONMENT_READY",
        "TRAFFIC_PREFLIGHT_PASS": "FORMAL_ENVIRONMENT_READY",
        "FORMAL_ENVIRONMENT_READY": "FORMAL_TRAFFIC_RUNNING",
        "FORMAL_TRAFFIC_RUNNING": "FORMAL_TRAFFIC_PASS",
        "FORMAL_TRAFFIC_PASS": "LIVE_CAPTURE_SEALED",
        "LIVE_CAPTURE_SEALED": "INCIDENT_CREATED",
        "INCIDENT_CREATED": "ACQUISITION_SEALED",
        "ACQUISITION_SEALED": "DIAGNOSIS_RUNNING",
        "DIAGNOSIS_RUNNING": "DIAGNOSIS_PERSISTED",
        "DIAGNOSIS_PERSISTED": "SCORED",
        "SCORED": "CLOSED",
    }
    recovery_resume = set(forward)
    previous_state: str | None = None
    previous_sha256 = "0" * 64
    for sequence, checkpoint in enumerate(checkpoints, start=1):
        if not isinstance(checkpoint, dict):
            raise ValueError("Product v0.2.3.3 checkpoint chain differs")
        state = checkpoint.get("state")
        allowed = (
            {"PREPARED"}
            if previous_state is None
            else (
                recovery_resume | {"RECOVERABLE_FAILURE", "NONRECOVERABLE_FAILURE"}
                if previous_state == "RECOVERABLE_FAILURE"
                else {
                    forward.get(previous_state),
                    "RECOVERABLE_FAILURE",
                    "NONRECOVERABLE_FAILURE",
                }
            )
        )
        allowed.discard(None)
        observed_sha256 = checkpoint.get("checkpoint_sha256")
        if (
            checkpoint.get("sequence") != sequence
            or checkpoint.get("previous_checkpoint_sha256") != previous_sha256
            or state not in allowed
            or not isinstance(observed_sha256, str)
            or len(observed_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in observed_sha256
            )
        ):
            raise ValueError("Product v0.2.3.3 checkpoint chain differs")
        previous_state = state
        previous_sha256 = observed_sha256
    if (
        payload.get("attempt_id") != attempt_id
        or payload.get("semantic_generation") != semantic_generation
        or payload.get("checkpoint_count") != len(checkpoints)
        or payload.get("latest_checkpoint_sha256") != previous_sha256
        or previous_sha256 != latest_checkpoint_sha256
        or previous_state != latest_state
        or payload.get("chain_sha256") != semantic_sha256_v22(body)
    ):
        raise ValueError("Product v0.2.3.3 checkpoint chain differs")


def _verify_nonrecoverable_history_v0233(
    project: Path,
    attempts: Sequence[Any],
) -> None:
    for attempt in attempts:
        prefix = f"docs/analysis/product-v0233-attempts/{attempt.attempt_id}/"
        required = {
            f"{prefix}checkpoint-chain.json",
            f"{prefix}formal-blocker.json",
            f"{prefix}repository-state-manifest.json",
            f"{prefix}progress.json",
        }
        evidence = attempt.evidence_sha256_by_path
        if (
            attempt.disposition != "NONRECOVERABLE_FAILURE"
            or not evidence
            or not required.issubset(evidence)
            or any(not relative.startswith(prefix) for relative in evidence)
            or any(
                (project / relative).is_symlink()
                or not (project / relative).is_file()
                or _sha256_file(project / relative) != digest
                for relative, digest in evidence.items()
            )
        ):
            raise ValueError("Product v0.2.3.3 recovery attempt history differs")
        blocker = FormalExecutionBlockerV0233.model_validate_json(
            (project / f"{prefix}formal-blocker.json").read_bytes()
        )
        checkpoint_chain = _object(project / f"{prefix}checkpoint-chain.json")
        if blocker.terminal != attempt.blocker_terminal:
            raise ValueError("Product v0.2.3.3 recovery attempt history differs")
        assert attempt.latest_checkpoint_sha256 is not None
        _verify_checkpoint_chain_v0233(
            checkpoint_chain,
            attempt_id=attempt.attempt_id,
            semantic_generation=attempt.semantic_generation,
            latest_checkpoint_sha256=attempt.latest_checkpoint_sha256,
            latest_state=attempt.latest_state.value,
        )


def _verify_measured_terminal(
    project: Path,
    ledger: FormalAttemptLedgerV0233,
) -> dict[str, object]:
    expected_attempt_ids = tuple(
        f"attempt-{ordinal}" for ordinal in range(1, len(ledger.attempts) + 1)
    )
    if (
        len(ledger.attempts) < 2
        or ledger.attempts[0] != build_legacy_attempt1_record_v0233(project)
        or tuple(attempt.attempt_id for attempt in ledger.attempts)
        != expected_attempt_ids
        or any(
            attempt.disposition != "NONRECOVERABLE_FAILURE"
            for attempt in ledger.attempts[1:-1]
        )
        or ledger.measured_result_count != 1
        or ledger.attempts[-1].disposition != "MEASURED"
    ):
        raise ValueError("Product v0.2.3.3 recovery attempt ledger differs")
    _verify_nonrecoverable_history_v0233(project, ledger.attempts[1:-1])
    attempt = ledger.attempts[-1]
    attempt_root = project / "docs/analysis/product-v0233-attempts" / attempt.attempt_id
    result = NoFaultAcceptanceResultV0233.model_validate_json(
        (project / "docs/results/product-v0233-nofault-acceptance.json").read_bytes()
    )
    clone = FreshFormalStateCloneV0233.model_validate_json(
        (attempt_root / "formal-state-clone.json").read_bytes()
    )
    authority = RuntimeAuthorityProofV0233.model_validate_json(
        (attempt_root / "runtime-authority.json").read_bytes()
    )
    restart = BaselineRestartProofV0233.model_validate_json(
        (attempt_root / "baseline-restart.json").read_bytes()
    )
    traffic = FormalTrafficResultV0233.model_validate_json(
        (attempt_root / "formal-traffic.json").read_bytes()
    )
    fresh_snapshot = FreshRuntimeSnapshotProofV0233.model_validate_json(
        (attempt_root / "fresh-runtime-snapshot.json").read_bytes()
    )
    incident_binding = IncidentTrafficBindingV0232.model_validate_json(
        (attempt_root / "incident-traffic-binding.json").read_bytes()
    )
    assessment = NoFaultEvidenceAssessmentV0232.model_validate_json(
        (attempt_root / "evidence-assessment.json").read_bytes()
    )
    diagnosis = DiagnosisResultV1.model_validate_json(
        (attempt_root / "diagnosis-result.json").read_bytes()
    )
    evidence = EvidenceBundleV1.model_validate_json(
        (attempt_root / "evidence-bundle.json").read_bytes()
    )
    index = DiagnosisEvidenceIndexV0232.model_validate_json(
        (attempt_root / "evidence-index.json").read_bytes()
    )
    decision_trace = DiagnosisDecisionTraceV0232.model_validate_json(
        (attempt_root / "decision-trace.json").read_bytes()
    )
    rescored_assessment = score_nofault_evidence_v0232(
        diagnosis=diagnosis,
        bundle=evidence,
        index=index,
        decision_trace=decision_trace,
    )
    closure = FormalClosureProofV0233.model_validate_json(
        (attempt_root / "formal-closure.json").read_bytes()
    )
    lineage_path = attempt_root / "diagnosis-recovery-lineage.json"
    lineage = _object(lineage_path)
    acquisition_path = attempt_root / "diagnosis-acquisition-checkpoint.json"
    acquisition = DiagnosisAcquisitionCheckpointV0233.model_validate_json(
        acquisition_path.read_bytes()
    )
    checkpoint_chain = _object(attempt_root / "checkpoint-chain.json")
    assert attempt.latest_checkpoint_sha256 is not None
    _verify_checkpoint_chain_v0233(
        checkpoint_chain,
        attempt_id=attempt.attempt_id,
        semantic_generation=attempt.semantic_generation,
        latest_checkpoint_sha256=attempt.latest_checkpoint_sha256,
        latest_state=attempt.latest_state.value,
    )
    lineage_body = {
        key: value for key, value in lineage.items() if key != "lineage_sha256"
    }
    pipeline = _object(attempt_root / "diagnosis-stage-journal.json")
    pipeline_body = {
        key: value
        for key, value in pipeline.items()
        if key != "public_projection_sha256"
    }
    typed_pipeline = DiagnosisPipelineAcceptanceV0233.model_validate(
        {key: value for key, value in pipeline_body.items() if key != "terminal"}
    )
    handoff = _object(attempt_root / "knowledge-loop-handoff.json")
    handoff_body = {
        key: value for key, value in handoff.items() if key != "handoff_sha256"
    }
    repository = ProductV0233RepositoryStateManifest.model_validate_json(
        (project / "config/product-v0233/repository-state-manifest.json").read_bytes()
    )
    review = RecoveryPreExecutionReviewV0233.model_validate_json(
        (
            project
            / "docs/analysis/product-v0233-recovery-pre-execution-review.json"
        ).read_bytes()
    )
    selection = FreshFormalSourceSelectionV0233.model_validate_json(
        (project / "config/product-v0233/source-selection.json").read_bytes()
    )
    progress = _object(project / "docs/analysis/product-v0233-progress.json")
    progress_body = {
        key: value for key, value in progress.items() if key != "progress_sha256"
    }
    failed_jobs = lineage.get("preserved_failed_job_ids")
    failed_job_projections = lineage.get("preserved_failed_jobs")
    successful_job = lineage.get("successful_job")
    failed_job_count = len(failed_jobs) if isinstance(failed_jobs, list) else -1
    new_diagnosis_count = repository.new_diagnosis_count
    lineage_exact = (
        isinstance(failed_jobs, list)
        and isinstance(failed_job_projections, list)
        and isinstance(successful_job, dict)
        and len(failed_jobs) == len(set(failed_jobs))
        and failed_jobs == sorted(failed_jobs)
        and len(failed_job_projections) == len(failed_jobs)
        and all(
            isinstance(projection, dict)
            and projection.get("job_id") == failed_jobs[ordinal]
            and projection.get("status") == "FAILED"
            and projection.get("result_sha256") is None
            and projection.get("projection_sha256")
            == semantic_sha256_v22(
                {
                    key: value
                    for key, value in projection.items()
                    if key != "projection_sha256"
                }
            )
            for ordinal, projection in enumerate(failed_job_projections)
        )
        and successful_job.get("job_id") == lineage.get("successful_job_id")
        and successful_job.get("status") == "SUCCEEDED"
        and successful_job.get("diagnosis_result_sha256")
        == result.diagnosis_result_sha256
        and successful_job.get("projection_sha256")
        == semantic_sha256_v22(
            {
                key: value
                for key, value in successful_job.items()
                if key != "projection_sha256"
            }
        )
        and new_diagnosis_count == len(failed_jobs) + 1
        and attempt.latest_state.value == "CLOSED"
        and lineage.get("attempt_id") == attempt.attempt_id
        and lineage.get("incident_id") == incident_binding.incident_id
        and lineage.get("incident_sha256") == result.incident_sha256
        and acquisition.attempt_id == attempt.attempt_id
        and acquisition.incident_id == incident_binding.incident_id
        and acquisition.incident_sha256 == result.incident_sha256
        and acquisition.acquisition_sha256 == lineage.get("acquisition_sha256")
        and lineage.get("successful_diagnosis_generation") == len(failed_jobs) + 1
        and lineage.get("diagnosis_result_sha256")
        == result.diagnosis_result_sha256
        and lineage.get("lineage_sha256") == semantic_sha256_v22(lineage_body)
    )
    required_public = (
        "docs/results/product-v0233-nofault-acceptance.md",
        "docs/results/product-v0233-limitations.md",
        "docs/results/product-v0233-interview-brief.md",
        "docs/analysis/product-v0233-knowledge-loop-handoff.md",
    )
    expected_evidence_paths = {
        *(f"docs/analysis/product-v0233-attempts/{attempt.attempt_id}/{name}" for name in (
            "formal-state-clone.json",
            "checkpoint-chain.json",
            "formal-closure.json",
            "diagnosis-acquisition-checkpoint.json",
            "diagnosis-recovery-lineage.json",
            "diagnosis-result.json",
            "evidence-bundle.json",
            "evidence-index.json",
            "decision-trace.json",
            "runtime-authority.json",
            "baseline-restart.json",
            "formal-traffic.json",
            "fresh-runtime-snapshot.json",
            "incident-traffic-binding.json",
            "evidence-assessment.json",
            "diagnosis-stage-journal.json",
            "knowledge-loop-handoff.json",
        )),
        "docs/results/product-v0233-nofault-acceptance.json",
        "docs/results/product-v0233-nofault-acceptance.md",
        "docs/results/product-v0233-limitations.md",
        "docs/results/product-v0233-interview-brief.md",
        "docs/analysis/product-v0233-knowledge-loop-handoff.md",
        "config/product-v0233/repository-state-manifest.json",
        "docs/analysis/product-v0233-progress.json",
    }
    evidence_exact = set(attempt.evidence_sha256_by_path) == expected_evidence_paths and all(
        not (project / relative).is_symlink()
        and (project / relative).is_file()
        and _sha256_file(project / relative) == expected
        for relative, expected in attempt.evidence_sha256_by_path.items()
    )
    evidence_sha256 = semantic_sha256_v22(evidence.model_dump(mode="json"))
    if (
        attempt.measured_terminal != result.measured_terminal
        or assessment != rescored_assessment
        or _V0233_TERMINAL_BY_V0232.get(assessment.terminal.value)
        != result.measured_terminal
        or assessment.reasons != result.reasons
        or not evidence_exact
        or not lineage_exact
        or typed_pipeline.job_status != "SUCCEEDED"
        or typed_pipeline.stage_journal_terminal != "JOB_SUCCEEDED"
        or pipeline.get("terminal")
        != "ECOMSRE_PRODUCT_V0233_DIAGNOSIS_PIPELINE_PASS"
        or typed_pipeline.job_id != lineage.get("successful_job_id")
        or not isinstance(successful_job, dict)
        or successful_job.get("journal_tail_sha256")
        != typed_pipeline.journal_tail_sha256
        or typed_pipeline.journal_tail_sha256
        != result.stage_journal_tail_sha256
        or pipeline.get("public_projection_sha256")
        != semantic_sha256_v22(pipeline_body)
        or handoff.get("nofault_result_sha256") != result.result_sha256
        or handoff.get("measured_terminal") != result.measured_terminal
        or handoff.get("terminal")
        != (
            "ECOMSRE_PRODUCT_V0233_KNOWLEDGE_LOOP_HANDOFF_READY"
            if result.measured_terminal.endswith("FULLY_SUPPORTED")
            else "ECOMSRE_PRODUCT_V0233_KNOWLEDGE_LOOP_HANDOFF_NOT_AUTHORIZED"
        )
        or handoff.get("knowledge_loop_campaigns") != 0
        or handoff.get("action_authority") != "NONE"
        or handoff.get("handoff_sha256") != semantic_sha256_v22(handoff_body)
        or result.formal_clone_sha256 != clone.clone_sha256
        or result.runtime_authority_proof_sha256 != authority.proof_sha256
        or result.baseline_restart_proof_sha256 != restart.proof_sha256
        or result.formal_traffic_execution_sha256
        != traffic.execution.execution_sha256
        or result.fresh_runtime_snapshot_sha256
        != fresh_snapshot.runtime_snapshot_sha256
        or result.incident_traffic_binding_sha256 != incident_binding.binding_sha256
        or result.v0232_assessment_sha256 != assessment.result_sha256
        or result.diagnosis_result_sha256 != diagnosis.result_sha256
        or result.evidence_bundle_sha256 != evidence_sha256
        or result.evidence_index_sha256 != index.index_sha256
        or result.decision_trace_sha256 != decision_trace.trace_sha256
        or assessment.incident_id != incident_binding.incident_id
        or assessment.diagnosis_id != diagnosis.diagnosis_id
        or assessment.diagnosis_result_sha256 != diagnosis.result_sha256
        or assessment.evidence_bundle_sha256 != evidence_sha256
        or assessment.evidence_index_sha256 != index.index_sha256
        or assessment.decision_trace_sha256 != decision_trace.trace_sha256
        or evidence.incident_id != incident_binding.incident_id
        or evidence.diagnosis_id != diagnosis.diagnosis_id
        or index.incident_id != incident_binding.incident_id
        or index.diagnosis_id != diagnosis.diagnosis_id
        or index.evidence_bundle_sha256 != evidence_sha256
        or index.decision_trace_sha256 != decision_trace.trace_sha256
        or typed_pipeline.diagnosis_result_sha256 != diagnosis.result_sha256
        or typed_pipeline.evidence_bundle_sha256 != evidence_sha256
        or typed_pipeline.evidence_index_sha256 != index.index_sha256
        or typed_pipeline.decision_trace_sha256 != decision_trace.trace_sha256
        or acquisition.campaign_id != ledger.campaign_id
        or acquisition.semantic_generation != attempt.semantic_generation
        or acquisition.semantic_generation != review.semantic_generation
        or acquisition.semantic_surface_sha256 != review.semantic_surface_sha256
        or acquisition.active_profile_sha256 != selection.active_profile_sha256
        or acquisition.baseline_sha256 != selection.active_baseline_sha256
        or result.measured_terminal
        not in {
            "ECOMSRE_PRODUCT_V0233_NOFAULT_FULLY_SUPPORTED",
            "ECOMSRE_PRODUCT_V0233_NOFAULT_CAPABILITY_LIMITED",
            "ECOMSRE_PRODUCT_V0233_NOFAULT_NOT_SUPPORTED",
        }
        or any(result.safety_counters.model_dump(mode="json").values())
        or result.cleanup_proof_sha256 != closure.closure_sha256
        or not closure.safety_observation.safe
        or closure.safety_observation.new_incident_count != 1
        or closure.safety_observation.new_diagnosis_count != new_diagnosis_count
        or repository.phase is not RepositoryPhaseV0233.MEASURED_COMPLETE
        or repository.formal_result_sha256 != result.result_sha256
        or repository.formal_blocker_sha256 is not None
        or repository.cleanup_proof_sha256 != closure.closure_sha256
        or repository.formal_clone_count != 1
        or repository.formal_execution_count != 1
        or repository.new_incident_count != 1
        or new_diagnosis_count is None
        or new_diagnosis_count < 1
        or repository.measured_result_count != 1
        or progress.get("progress_sha256") != semantic_sha256_v22(progress_body)
        or progress.get("measured_terminal") != result.measured_terminal
        or progress.get("nofault_result_sha256") != result.result_sha256
        or progress.get("new_incident_count") != 1
        or progress.get("new_diagnosis_count") != new_diagnosis_count
        or progress.get("measured_result_count") != 1
        or any(
            (project / relative).is_symlink()
            or not (project / relative).is_file()
            for relative in required_public
        )
    ):
        raise ValueError("Product v0.2.3.3 recovered terminal differs")
    return {
        "terminal": "ECOMSRE_PRODUCT_V0233_NOFAULT_ACCEPTANCE_COMPLETE",
        "attempt_id": attempt.attempt_id,
        "measured_terminal": result.measured_terminal,
        "formal_clone_count": 1,
        "formal_execution_count": 1,
        "formal_transaction_count": traffic.execution.run.successful_transactions,
        "new_incident_count": 1,
        "new_diagnosis_count": new_diagnosis_count,
        "failed_diagnosis_job_count": failed_job_count,
        "measured_result_count": 1,
        "action_authority": "NONE",
        "closure": closure.verdict,
    }


def verify_product_v0233_terminal(root: Path) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    ledger = FormalAttemptLedgerV0233.model_validate_json(
        (project / "config/product-v0233/formal-attempt-ledger.json").read_bytes()
    )
    if ledger.measured_result_count == 1:
        return _verify_measured_terminal(project, ledger)
    manifest_path = (
        project / "docs/analysis/product-v0233-formal-blocker-evidence-manifest.json"
    )
    manifest = _object(manifest_path)
    supplied_manifest_sha256 = manifest.pop("manifest_sha256", None)
    if (
        supplied_manifest_sha256 != semantic_sha256_v22(manifest)
        or supplied_manifest_sha256 != _MANIFEST_SHA256
        or manifest.get("schema_version")
        != "ecomsre.product.formal-blocker-evidence-manifest.v0233"
        or manifest.get("goal_version")
        != "ecomsre-product-v0233-fresh-formal-evidence-bound-nofault-v1"
        or manifest.get("terminal") != _TERMINAL
        or manifest.get("execution_head") != _EXECUTION_HEAD
        or manifest.get("failure_stage") != "FORMAL_TRAFFIC_PASS"
        or manifest.get("safe_error_code") != "TypeError:FORMAL_TRAFFIC_PASS"
        or manifest.get("one_shot_consumed") is not True
        or manifest.get("formal_rerun_authorized") is not False
        or manifest.get("diagnosis_retry_authorized") is not False
        or manifest.get("formal_healthy_traffic_execution_count") != 1
        or manifest.get("formal_clone_count") != 1
        or manifest.get("completed_transactions") != 30
        or manifest.get("new_incident_count") != 0
        or manifest.get("new_diagnosis_count") != 0
        or manifest.get("measured_result_count") != 0
        or manifest.get("measured_terminal") is not None
        or manifest.get("nofault_acceptance_complete") is not False
        or manifest.get("knowledge_loop_handoff_authorized") is not False
    ):
        raise ValueError("Product v0.2.3.3 blocker evidence manifest differs")

    artifacts = manifest.get("artifacts")
    closure_claim = manifest.get("authority_and_closure")
    required_absences = manifest.get("required_absences")
    if (
        not isinstance(artifacts, dict)
        or not isinstance(closure_claim, dict)
        or not isinstance(required_absences, list)
        or any(not isinstance(item, str) for item in required_absences)
    ):
        raise ValueError("Product v0.2.3.3 blocker evidence inventory differs")
    _verify_required_absences(project, required_absences)

    clone_path = _require_public_file(project, artifacts["formal_state_clone"])
    authority_path = _require_public_file(
        project, artifacts["runtime_authority"], file_field="public_file_sha256"
    )
    restart_path = _require_public_file(
        project, artifacts["baseline_restart"], file_field="public_file_sha256"
    )
    traffic_path = _require_public_file(
        project, artifacts["formal_traffic"], file_field="public_file_sha256"
    )
    closure_path = _require_public_file(project, artifacts["formal_closure"])
    blocker_path = _require_public_file(project, artifacts["formal_blocker"])
    repository_path = _require_public_file(project, artifacts["repository_state"])
    progress_path = _require_public_file(project, artifacts["progress"])

    clone = FreshFormalStateCloneV0233.model_validate_json(clone_path.read_bytes())
    authority = RuntimeAuthorityProofV0233.model_validate_json(
        authority_path.read_bytes()
    )
    restart = BaselineRestartProofV0233.model_validate_json(restart_path.read_bytes())
    traffic = FormalTrafficResultV0233.model_validate_json(traffic_path.read_bytes())
    closure = FormalClosureProofV0233.model_validate_json(closure_path.read_bytes())
    blocker = FormalExecutionBlockerV0233.model_validate_json(blocker_path.read_bytes())
    repository = ProductV0233RepositoryStateManifest.model_validate_json(
        repository_path.read_bytes()
    )
    progress = _object(progress_path)
    progress_sha256 = progress.pop("progress_sha256", None)

    public_semantic = {
        "formal_state_clone": clone.clone_sha256,
        "runtime_authority": authority.proof_sha256,
        "baseline_restart": restart.proof_sha256,
        "formal_traffic": traffic.result_sha256,
        "formal_closure": closure.closure_sha256,
        "formal_blocker": blocker.blocker_sha256,
        "repository_state": repository.manifest_sha256,
        "progress": progress_sha256,
    }
    if any(
        artifacts[name].get("semantic_sha256") != value
        for name, value in public_semantic.items()
    ):
        raise ValueError("Product v0.2.3.3 public semantic binding differs")

    zero_safety = (
        blocker.new_incident_count,
        blocker.new_diagnosis_count,
        blocker.measured_result_count,
        blocker.safety_observation.provider_calls,
        blocker.safety_observation.agent_writes,
        blocker.safety_observation.runbook_executions,
        blocker.safety_observation.fault_attempts,
        blocker.safety_observation.knowledge_loop_executions,
    )
    exact = (
        authority.admission_sha256
        == restart.admission_sha256
        == traffic.admission_sha256
        == blocker.admission_sha256
        and blocker.terminal == _TERMINAL
        and blocker.failure_stage == "FORMAL_TRAFFIC_PASS"
        and blocker.safe_error_code == "TypeError:FORMAL_TRAFFIC_PASS"
        and blocker.formal_clone_count == 1
        and blocker.formal_execution_count == 1
        and blocker.formal_clone_sha256 == clone.clone_sha256
        and traffic.execution.execution_sha256
        == artifacts["formal_traffic"].get("execution_sha256")
        and traffic.execution.run.result_sha256
        == artifacts["formal_traffic"].get("traffic_run_sha256")
        and traffic.execution.run.successful_transactions == 30
        and traffic.execution.run.failed_transactions == 0
        and traffic.execution.run.transport_retry_count == 0
        and traffic.monotonic_duration_ms >= 300_000
        and closure.verdict == "CLEAN"
        and closure.safety_observation == blocker.safety_observation
        and closure.source_database_before_sha256
        == closure.source_database_after_sha256
        and closure.frozen_semantic_surface_before_sha256
        == closure.frozen_semantic_surface_after_sha256
        and not any(zero_safety)
        and repository.phase is RepositoryPhaseV0233.FORMAL_BLOCKED
        and repository.formal_blocker_sha256 == blocker.blocker_sha256
        and repository.cleanup_proof_sha256 == closure.closure_sha256
        and repository.formal_clone_count == 1
        and repository.formal_execution_count == 1
        and repository.new_incident_count == 0
        and repository.new_diagnosis_count == 0
        and repository.measured_result_count == 0
        and repository.formal_result_sha256 is None
        and repository.knowledge_handoff_sha256 is None
        and progress_sha256 == semantic_sha256_v22(progress)
        and progress.get("current_terminal") == _TERMINAL
        and progress.get("phase") == "INCREMENT_4_FORMAL_BLOCKED"
        and progress.get("formal_transaction_count") == 30
        and progress.get("new_incident_count") == 0
        and progress.get("new_diagnosis_count") == 0
        and progress.get("measured_result_count") == 0
        and progress.get("next_gate") == "NONE"
        and closure_claim
        == {
            "action_authority": "NONE",
            "product_cleanup": "CLEAN",
            "demo_cleanup": "CLEAN",
            "source_state_unchanged": True,
            "frozen_semantic_surface_unchanged_during_execution": True,
            "provider_calls": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
            "fault_attempts": 0,
            "knowledge_loop_executions": 0,
            "non_owned_resources_changed": False,
        }
    )
    if not exact:
        raise ValueError("Product v0.2.3.3 blocked repository binding differs")
    return {
        "terminal": _TERMINAL,
        "failure_stage": blocker.failure_stage,
        "safe_error_code": blocker.safe_error_code,
        "one_shot_consumed": True,
        "formal_clone_count": blocker.formal_clone_count,
        "formal_execution_count": blocker.formal_execution_count,
        "formal_transaction_count": traffic.execution.run.successful_transactions,
        "new_incident_count": blocker.new_incident_count,
        "new_diagnosis_count": blocker.new_diagnosis_count,
        "measured_result_count": blocker.measured_result_count,
        "measured_terminal": blocker.measured_terminal,
        "formal_rerun_authorized": False,
        "diagnosis_retry_authorized": False,
        "action_authority": blocker.action_authority,
        "closure": closure.verdict,
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(argv)
    print(
        json.dumps(
            verify_product_v0233_terminal(arguments.project_root), sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_product_v0233_terminal",)
