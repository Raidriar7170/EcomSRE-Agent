#!/usr/bin/env python3
"""Verify the public Product v0.2.3.3 blocked repository terminal."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.environment.capabilities import EnvironmentCapabilityMatrixV1
from ecomsre.product.incidents.contracts import (
    DiagnosisResultV1,
    EvidenceBundleV1,
    IncidentRecordV1,
)
from ecomsre.product.incidents.diagnosis_pipeline_v02322 import (
    DiagnosisPrivateFailureEnvelopeV02322,
)
from ecomsre.product.incidents.evidence_binding_v0232 import (
    DiagnosisDecisionTraceV0232,
    DiagnosisEvidenceIndexV0232,
    RuntimeSnapshotEvidenceBindingV0232,
)
from ecomsre.product.incidents.diagnosis_stage_journal_v02322 import (
    DiagnosisPipelineStageV02322,
    DiagnosisStageEventV02322,
)
from ecomsre.product.jobs.contracts import (
    ProductJobRecordV1,
    ProductJobStatusV1,
    ProductJobTypeV1,
)
from ecomsre.product.pilot.formal_live_v0233 import (
    BaselineRestartProofV0233,
    FormalClosureProofV0233,
    FormalExecutionBlockerV0233,
    FormalTrafficResultV0233,
    FreshRuntimeSnapshotProofV0233,
    InterruptedAttemptCleanupProofV0233,
    RuntimeAuthorityProofV0233,
)
from ecomsre.product.pilot.fresh_formal_source_v0233 import (
    FreshFormalSourceSelectionV0233,
    FreshFormalStateCloneV0233,
)
from ecomsre.product.pilot.formal_recovery_v0233 import (
    DiagnosisAcquisitionCheckpointV0233,
    FormalAttemptLedgerV0233,
    FormalExecutionCheckpointV0233,
    LiveCaptureBundleV0233,
    RecoveryPreExecutionReviewV0233,
    build_legacy_attempt1_record_v0233,
)
from ecomsre.product.pilot.diagnosis_recovery_v0233 import (
    FormalDiagnosisJobContextV0233,
    final_diagnosis_idempotency_key_v0233,
    restore_diagnosis_acquisition_v0233,
)
from ecomsre.product.pilot.fresh_formal_acceptance_v0233 import (
    DiagnosisPipelineAcceptanceV0233,
    NoFaultAcceptanceResultV0233,
    load_fresh_traffic_profile_v0233,
)
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    IncidentTrafficBindingV0232,
    load_checkout_traffic_contract_v0232,
)
from ecomsre.product.pilot.nofault_acceptance_v0232 import (
    NoFaultEvidenceAssessmentV0232,
    score_nofault_evidence_v0232,
)
from ecomsre.product.pilot.repository_state_v0233 import (
    ProductV0233RepositoryStateManifest,
    RepositoryPhaseV0233,
)
from scripts.product_v0233.run_formal_nofault import (  # noqa: PLC2701
    _measured_claim_documents_v0233,
    _verified_formal_closure_observation_v0233,
)


_TERMINAL = "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS"
_EXECUTION_HEAD = "466796648c2c4a3360b911a12be1ee806d39124e"
_MANIFEST_SHA256 = "08fdbd61e3fa439b55b1ef903bdea26dee6a3c839129bef53ee99c19a3c61014"
_ATTEMPT3_DIAGNOSIS_FAILURE_SUPPLEMENT_SHA256 = (
    "45ede4fd8453047ea9c9b6057491fcdd6243d3262f435eb2d07b9839d343f1ae"
)
_ATTEMPT4_MEASURED_RESULT_SHA256 = (
    "9832687fcf71781e6b6bbe26e1de3fe574326e643245569680d8d41d0fbfa11b"
)
_ATTEMPT4_ORIGINAL_PUBLICATION_SHA256 = (
    "2dd3fb981aeee3a443a76c42b5d431b1e7140284c13c422d813d38d46414f6f0"
)
_ATTEMPT4_MEASURED_CLAIM_CORRECTION_SHA256 = (
    "1061f9463d4f312c492283701ae2d3cf09442b693fb8b3618754492d7eb21ada"
)
_ATTEMPT3_DIAGNOSIS_FAILURE_EXPECTED = {
    "job_id": "job-558b022c696cecd3041de475",
    "incident_id": "inc-221cf5618424bad99df8aa63",
    "incident_sha256": "20a2a39b9b33d4965ca05717b39e7639d4a12cba546a33aaff91db6b7fa72157",
    "capability_sha256": "b278a6694b1c9596e291ee7cb514298319c4d3bb0989b0addb041c25690d511e",
    "exception_fingerprint": (
        "de6d2e8b91fbb7cbb0fbcd76ad99fb077725780eb2828b1b984f56c02c7de24a"
    ),
    "read_started_event_sha256": (
        "073fcc87170beec64f7da499ff936b604be1aab768a51ccbea325cda5fc8057f"
    ),
    "failure_event_sha256": (
        "4fcca7dfe6966e91d5532d767f450a2e9c4613e39f64f2f02a74f955471bc0c2"
    ),
    "failure_envelope_sha256": (
        "b2cc3e72677280229235dd05b163d6179204462a6643d2f7ecef4f71cf70cdd6"
    ),
}
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


def _verify_attempt3_diagnosis_failure_supplement_v0233(
    project: Path,
    ledger: Mapping[str, Any],
) -> dict[str, Any]:
    path = (
        project
        / "docs/analysis/product-v0233-attempts/attempt-3/"
        "diagnosis-failure-supplement.json"
    )
    supplement = _object(path)
    body = {
        key: value for key, value in supplement.items() if key != "supplement_sha256"
    }
    attempts = ledger.get("attempts")
    attempt = next(
        (
            item
            for item in attempts
            if isinstance(item, dict) and item.get("attempt_id") == "attempt-3"
        ),
        None,
    ) if isinstance(attempts, list) else None
    if not isinstance(attempt, dict):
        raise ValueError("Product v0.2.3.3 Attempt 3 supplement ledger differs")
    blocker_path = path.parent / "formal-blocker.json"
    blocker = FormalExecutionBlockerV0233.model_validate_json(
        blocker_path.read_bytes()
    )
    job = ProductJobRecordV1.model_validate(supplement.get("diagnosis_job"))
    context = FormalDiagnosisJobContextV0233.model_validate(
        job.payload.get("formal_recovery_v0233")
    )
    incident = IncidentRecordV1.model_validate(supplement.get("incident"))
    capability = EnvironmentCapabilityMatrixV1.model_validate(
        supplement.get("capability_matrix")
    )
    journal_payload = supplement.get("journal_tail_events")
    if not isinstance(journal_payload, list):
        raise ValueError("Product v0.2.3.3 Attempt 3 journal supplement differs")
    journal = tuple(DiagnosisStageEventV02322.model_validate(item) for item in journal_payload)
    envelope = DiagnosisPrivateFailureEnvelopeV02322.model_validate(
        supplement.get("failure_envelope")
    )
    source_scope = tuple(
        (
            item.source.value,
            item.status.value,
            "checkout" in item.covered_services,
        )
        for item in capability.sources
    )
    if (
        supplement.get("schema_version")
        != "ecomsre.product.attempt-diagnosis-failure-supplement.v0233"
        or supplement.get("supplement_sha256") != semantic_sha256_v22(body)
        or supplement.get("supplement_sha256")
        != _ATTEMPT3_DIAGNOSIS_FAILURE_SUPPLEMENT_SHA256
        or supplement.get("attempt_id") != "attempt-3"
        or supplement.get("semantic_generation") != 2
        or supplement.get("prior_semantic_surface_sha256")
        != context.semantic_surface_sha256
        or supplement.get("original_attempt_record_sha256")
        != attempt.get("record_sha256")
        or supplement.get("original_latest_checkpoint_sha256")
        != attempt.get("latest_checkpoint_sha256")
        or supplement.get("original_blocker_file_sha256")
        != _sha256_file(blocker_path)
        or supplement.get("original_blocker_sha256") != blocker.blocker_sha256
        or supplement.get("candidate_logical_services") != ["checkout"]
        or supplement.get("root_cause_code")
        != "CANDIDATE_SCOPED_CAPABILITY_STATUS_MISMATCH"
        or supplement.get("repair_classification")
        != "SEMANTIC_GENERATION_CHANGE_REQUIRED"
        or supplement.get("successor_semantic_generation") != 3
        or ledger.get("campaign_id") != context.campaign_id
        or attempt.get("semantic_generation") != 2
        or job.job_type is not ProductJobTypeV1.DIAGNOSIS
        or job.job_id != _ATTEMPT3_DIAGNOSIS_FAILURE_EXPECTED["job_id"]
        or job.status is not ProductJobStatusV1.FAILED
        or job.result is not None
        or job.failure_stage != "READ_ACQUISITION_STARTED"
        or job.safe_error_code != "INTERNAL_CONTRACT_FAILURE"
        or context.attempt_id != "attempt-3"
        or context.semantic_generation != 2
        or context.diagnosis_generation != 1
        or context.acquisition_sha256 is not None
        or context.acquisition_checkpoint_locator
        != "private/formal-v0233/attempt-3/diagnosis-acquisition-checkpoint.json"
        or incident.incident_id != job.payload.get("incident_id")
        or incident.incident_id
        != _ATTEMPT3_DIAGNOSIS_FAILURE_EXPECTED["incident_id"]
        or incident.incident_sha256
        != _ATTEMPT3_DIAGNOSIS_FAILURE_EXPECTED["incident_sha256"]
        or incident.candidate_logical_services != ("checkout",)
        or incident.source_capability_sha256 != capability.capability_sha256
        or capability.capability_sha256
        != _ATTEMPT3_DIAGNOSIS_FAILURE_EXPECTED["capability_sha256"]
        or len(journal) != 2
        or any(item.job_id != job.job_id for item in journal)
        or any(item.incident_id != incident.incident_id for item in journal)
        or journal[0].journal_id != journal[1].journal_id
        or journal[0].ordinal != 23
        or journal[0].stage is not DiagnosisPipelineStageV02322.READ_ACQUISITION_STARTED
        or journal[0].status.value != "STARTED"
        or journal[0].event_sha256
        != _ATTEMPT3_DIAGNOSIS_FAILURE_EXPECTED["read_started_event_sha256"]
        or journal[1].ordinal != 24
        or journal[1].stage is not DiagnosisPipelineStageV02322.FAILED
        or journal[1].status.value != "FAILED"
        or journal[1].event_sha256
        != _ATTEMPT3_DIAGNOSIS_FAILURE_EXPECTED["failure_event_sha256"]
        or journal[1].previous_event_sha256 != journal[0].event_sha256
        or journal[1].input_binding_sha256 != journal[0].event_sha256
        or journal[1].output_artifact_sha256 != envelope.failure_envelope_sha256
        or job.journal_tail_sha256 != journal[1].event_sha256
        or job.exception_fingerprint != journal[1].exception_fingerprint
        or job.safe_error_code != journal[1].safe_error_code
        or envelope.journal_tail_sha256 != journal[0].event_sha256
        or envelope.job_id != job.job_id
        or envelope.incident_id != incident.incident_id
        or envelope.incident_sha256 != incident.incident_sha256
        or envelope.capability_sha256 != capability.capability_sha256
        or envelope.failing_stage.value != job.failure_stage
        or envelope.exception_fingerprint != job.exception_fingerprint
        or envelope.exception_fingerprint
        != _ATTEMPT3_DIAGNOSIS_FAILURE_EXPECTED["exception_fingerprint"]
        or envelope.failure_envelope_sha256
        != _ATTEMPT3_DIAGNOSIS_FAILURE_EXPECTED["failure_envelope_sha256"]
        or envelope.job_payload_sha256 != semantic_sha256_v22(job.payload)
        or envelope.last_passed_stage is not DiagnosisPipelineStageV02322.ENVIRONMENT_LOADED
        or "partial capability observation differs" not in envelope.bounded_message
        or source_scope
        != (
            ("CHANGES", "AVAILABLE", True),
            ("LOGS", "PARTIAL", True),
            ("METRICS", "PARTIAL", True),
            ("RESOURCES", "PARTIAL", True),
            ("RUNTIME", "PARTIAL", True),
            ("TRACES", "PARTIAL", True),
        )
        or blocker.terminal != "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS"
        or blocker.failure_stage != "DIAGNOSIS_SUBMITTED"
        or blocker.new_incident_count != 1
        or blocker.new_diagnosis_count != 1
        or blocker.exception_fingerprint is not None
        or blocker.journal_tail_sha256 is not None
        or blocker.private_failure_envelope_sha256 is not None
    ):
        raise ValueError("Product v0.2.3.3 Attempt 3 supplement differs")
    return {
        "attempt_id": "attempt-3",
        "failure_stage": job.failure_stage,
        "exception_fingerprint": job.exception_fingerprint,
        "repair_classification": supplement["repair_classification"],
        "successor_semantic_generation": supplement["successor_semantic_generation"],
        "supplement_sha256": supplement["supplement_sha256"],
    }


def _job_projection_context_exact_v0233(
    projection: Mapping[str, Any],
    *,
    campaign_id: str,
    attempt_id: str,
    semantic_generation: int,
    diagnosis_generation: int,
    acquisition: DiagnosisAcquisitionCheckpointV0233,
) -> bool:
    try:
        context = FormalDiagnosisJobContextV0233.model_validate(
            projection.get("formal_recovery_context")
        )
    except ValueError:
        return False
    expected_payload = {
        "incident_id": acquisition.incident_id,
        "formal_recovery_v0233": context.model_dump(mode="json"),
    }
    last_passed = projection.get("last_passed_stage")
    interruption_after = projection.get("interruption_after_stage")
    interrupted = projection.get("safe_error_code") == "FORMAL_WORKER_INTERRUPTED"
    stages = tuple(
        stage for stage in DiagnosisPipelineStageV02322 if stage.value != "FAILED"
    )
    expected_failure_stage: str | None = None
    if interrupted:
        try:
            next_index = (
                0
                if last_passed is None
                else tuple(stage.value for stage in stages).index(str(last_passed)) + 1
            )
            expected_failure_stage = stages[next_index].value
        except (IndexError, ValueError):
            return False
    acquisition_binding_exact = (
        context.acquisition_sha256 is None
        if context.diagnosis_generation == 1
        else context.acquisition_sha256 == acquisition.acquisition_sha256
    )
    expected_idempotency_key = final_diagnosis_idempotency_key_v0233(
        context=context.model_copy(
            update={"acquisition_sha256": acquisition.acquisition_sha256}
        ),
        incident_sha256=acquisition.incident_sha256,
        acquisition_sha256=acquisition.acquisition_sha256,
    )
    return (
        projection.get("job_type") == "DIAGNOSIS"
        and projection.get("incident_id") == acquisition.incident_id
        and projection.get("payload_sha256") == semantic_sha256_v22(expected_payload)
        and context.campaign_id == campaign_id
        and context.attempt_id == attempt_id
        and context.semantic_generation == semantic_generation
        and context.diagnosis_generation == diagnosis_generation
        and context.active_profile_sha256 == acquisition.active_profile_sha256
        and context.semantic_surface_sha256 == acquisition.semantic_surface_sha256
        and acquisition_binding_exact
        and projection.get("idempotency_key") == expected_idempotency_key
        and (
            not interrupted
            or (
                interruption_after == last_passed
                and projection.get("failure_stage") == expected_failure_stage
            )
        )
    )


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


def _verify_measured_claim_documents_v0233(
    project: Path,
    *,
    result: Any,
    diagnosis: DiagnosisResultV1,
    new_diagnosis_count: int,
) -> None:
    expected = _measured_claim_documents_v0233(
        measured_terminal=result.measured_terminal,
        result_sha256=result.result_sha256,
        reasons=result.reasons,
        capability_limitations=diagnosis.capability_limitations,
        new_diagnosis_count=new_diagnosis_count,
    )
    if any(
        (path := project / relative).is_symlink()
        or not path.is_file()
        or path.read_text(encoding="utf-8") != payload
        for relative, payload in expected.items()
    ):
        raise ValueError("Product v0.2.3.3 measured claim documents differ")


def _verify_measured_claim_correction_v0233(
    project: Path,
    *,
    attempt_id: str,
    result: Any,
    diagnosis: DiagnosisResultV1,
) -> None:
    path = (
        project
        / "docs/analysis/product-v0233-attempts"
        / attempt_id
        / "measured-claim-correction.json"
    )
    correction_required = (
        attempt_id == "attempt-4"
        and result.result_sha256 == _ATTEMPT4_MEASURED_RESULT_SHA256
    )
    if not path.exists() and not path.is_symlink():
        if correction_required:
            raise ValueError("Product v0.2.3.3 measured claim correction differs")
        return
    if path.is_symlink() or not path.is_file():
        raise ValueError("Product v0.2.3.3 measured claim correction differs")
    correction = _object(path)
    body = {
        key: value for key, value in correction.items() if key != "correction_sha256"
    }
    artifacts = correction.get("artifacts")
    expected_paths = (
        "config/product-v0233/formal-attempt-ledger.json",
        "docs/results/product-v0233-limitations.md",
        "docs/results/product-v0233-nofault-acceptance.md",
    )
    if (
        set(correction)
        != {
            "schema_version",
            "attempt_id",
            "correction_code",
            "original_publication_sha256",
            "measured_result_sha256",
            "diagnosis_result_sha256",
            "capability_limitations",
            "artifacts",
            "correction_sha256",
        }
        or correction.get("schema_version")
        != "ecomsre.product.measured-claim-correction.v0233"
        or correction.get("attempt_id") != attempt_id
        or correction.get("correction_code")
        != "CAPABILITY_LIMITATIONS_RENDERING_SOURCE"
        or correction.get("measured_result_sha256") != result.result_sha256
        or correction.get("diagnosis_result_sha256") != diagnosis.result_sha256
        or correction.get("capability_limitations")
        != list(diagnosis.capability_limitations)
        or (
            correction_required
            and correction.get("original_publication_sha256")
            != _ATTEMPT4_ORIGINAL_PUBLICATION_SHA256
        )
        or (
            correction_required
            and correction.get("correction_sha256")
            != _ATTEMPT4_MEASURED_CLAIM_CORRECTION_SHA256
        )
        or not isinstance(correction.get("original_publication_sha256"), str)
        or len(correction["original_publication_sha256"]) != 64
        or any(
            character not in "0123456789abcdef"
            for character in correction["original_publication_sha256"]
        )
        or not isinstance(artifacts, list)
        or tuple(
            item.get("path") if isinstance(item, Mapping) else None
            for item in artifacts
        )
        != expected_paths
        or correction.get("correction_sha256") != semantic_sha256_v22(body)
    ):
        raise ValueError("Product v0.2.3.3 measured claim correction differs")
    for item in artifacts:
        if not isinstance(item, Mapping):
            raise ValueError("Product v0.2.3.3 measured claim correction differs")
        relative = item.get("path")
        previous_sha256 = item.get("previous_sha256")
        corrected_sha256 = item.get("corrected_sha256")
        if (
            set(item) != {"path", "previous_sha256", "corrected_sha256"}
            or not isinstance(relative, str)
            or not isinstance(previous_sha256, str)
            or len(previous_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in previous_sha256
            )
            or not isinstance(corrected_sha256, str)
            or len(corrected_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in corrected_sha256
            )
            or previous_sha256 == corrected_sha256
            or (project / relative).is_symlink()
            or not (project / relative).is_file()
            or _sha256_file(project / relative) != corrected_sha256
        ):
            raise ValueError("Product v0.2.3.3 measured claim correction differs")


def _verify_checkpoint_chain_v0233(
    payload: Mapping[str, Any],
    *,
    attempt_id: str,
    semantic_generation: int,
    latest_checkpoint_sha256: str,
    latest_state: str,
    campaign_id: str,
    semantic_surface_sha256: str,
    operational_surface_sha256: str | None,
    source_selection_sha256: str,
) -> None:
    body = {key: value for key, value in payload.items() if key != "chain_sha256"}
    checkpoints = payload.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        raise ValueError("Product v0.2.3.3 checkpoint chain differs")
    forward = {
        "PREPARED": "CLONE_SEALED",
        "TRAFFIC_PREFLIGHT_PASS": "CLONE_SEALED",
        "CLONE_SEALED": "FORMAL_ENVIRONMENT_READY",
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
    previous_checkpoint: FormalExecutionCheckpointV0233 | None = None
    first_checkpoint: FormalExecutionCheckpointV0233 | None = None
    previous_created_at = None
    for sequence, checkpoint in enumerate(checkpoints, start=1):
        if not isinstance(checkpoint, dict):
            raise ValueError("Product v0.2.3.3 checkpoint chain differs")
        try:
            typed_checkpoint = FormalExecutionCheckpointV0233.model_validate(checkpoint)
        except ValueError as error:
            raise ValueError("Product v0.2.3.3 checkpoint chain differs") from error
        if first_checkpoint is None:
            first_checkpoint = typed_checkpoint
        state = checkpoint.get("state")
        allowed = (
            {"PREPARED"}
            if previous_state is None
            else (
                set()
                if previous_state in {"CLOSED", "NONRECOVERABLE_FAILURE"}
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
        )
        allowed.discard(None)
        checkpoint_body = {
            key: value
            for key, value in checkpoint.items()
            if key != "checkpoint_sha256"
        }
        observed_sha256 = checkpoint.get("checkpoint_sha256")
        if (
            checkpoint.get("sequence") != sequence
            or checkpoint.get("previous_checkpoint_sha256") != previous_sha256
            or state not in allowed
            or not isinstance(observed_sha256, str)
            or len(observed_sha256) != 64
            or any(character not in "0123456789abcdef" for character in observed_sha256)
            or observed_sha256 != semantic_sha256_v22(checkpoint_body)
            or (
                previous_created_at is not None
                and typed_checkpoint.created_at < previous_created_at
            )
            or typed_checkpoint.campaign_id != first_checkpoint.campaign_id
            or typed_checkpoint.attempt_id != attempt_id
            or typed_checkpoint.semantic_generation != semantic_generation
            or typed_checkpoint.semantic_surface_sha256
            != first_checkpoint.semantic_surface_sha256
            or typed_checkpoint.source_selection_sha256
            != first_checkpoint.source_selection_sha256
            or typed_checkpoint.input_artifact_sha256s
            != first_checkpoint.input_artifact_sha256s
            or (
                previous_checkpoint is not None
                and typed_checkpoint.operational_surface_sha256
                != previous_checkpoint.operational_surface_sha256
                and previous_state != "RECOVERABLE_FAILURE"
            )
            or (
                previous_checkpoint is not None
                and any(
                    typed_checkpoint.output_artifact_sha256s.get(path) != digest
                    for path, digest in (
                        previous_checkpoint.output_artifact_sha256s.items()
                    )
                )
            )
            or (
                previous_checkpoint is not None
                and previous_checkpoint.formal_clone_sha256 is not None
                and typed_checkpoint.formal_clone_sha256
                != previous_checkpoint.formal_clone_sha256
            )
        ):
            raise ValueError("Product v0.2.3.3 checkpoint chain differs")
        previous_state = state
        previous_sha256 = observed_sha256
        previous_checkpoint = typed_checkpoint
        previous_created_at = typed_checkpoint.created_at
    if (
        payload.get("attempt_id") != attempt_id
        or payload.get("semantic_generation") != semantic_generation
        or payload.get("checkpoint_count") != len(checkpoints)
        or payload.get("latest_checkpoint_sha256") != previous_sha256
        or previous_sha256 != latest_checkpoint_sha256
        or previous_state != latest_state
        or first_checkpoint is None
        or first_checkpoint.campaign_id != campaign_id
        or first_checkpoint.semantic_surface_sha256 != semantic_surface_sha256
        or (
            operational_surface_sha256 is not None
            and first_checkpoint.operational_surface_sha256
            != operational_surface_sha256
        )
        or first_checkpoint.source_selection_sha256 != source_selection_sha256
        or payload.get("chain_sha256") != semantic_sha256_v22(body)
    ):
        raise ValueError("Product v0.2.3.3 checkpoint chain differs")


def _verify_cleanup_proof_v0233(
    payload: Mapping[str, Any],
    *,
    attempt_id: str,
    latest_checkpoint_sha256: str,
    blocker: FormalExecutionBlockerV0233,
) -> str:
    schema_version = payload.get("schema_version")
    if schema_version == "ecomsre.product.formal-closure-proof.v0233":
        closure = FormalClosureProofV0233.model_validate(payload)
        closure_sha256 = closure.closure_sha256
        safety = closure.safety_observation
    elif schema_version == "ecomsre.product.interrupted-attempt-cleanup.v0233":
        interrupted = InterruptedAttemptCleanupProofV0233.model_validate(payload)
        if (
            interrupted.attempt_id != attempt_id
            or interrupted.latest_checkpoint_sha256 != latest_checkpoint_sha256
        ):
            raise ValueError("Product v0.2.3.3 interrupted cleanup proof differs")
        closure_sha256 = interrupted.closure_sha256
        safety = interrupted.safety_observation
    elif schema_version == "ecomsre.product.formal-closure-observation.v0233":
        closure_sha256, safety = _verified_formal_closure_observation_v0233(
            payload,
            blocker=blocker,
        )
    else:
        raise ValueError("Product v0.2.3.3 formal closure schema differs")
    if safety != blocker.safety_observation:
        raise ValueError("Product v0.2.3.3 formal closure safety differs")
    return closure_sha256


def _verify_nonrecoverable_history_v0233(
    project: Path,
    ledger: FormalAttemptLedgerV0233,
    attempts: Sequence[Any],
) -> None:
    selection = FreshFormalSourceSelectionV0233.model_validate_json(
        (project / "config/product-v0233/source-selection.json").read_bytes()
    )
    ledger_payload = ledger.model_dump(mode="json")
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
        review = RecoveryPreExecutionReviewV0233.model_validate_json(
            (project / f"{prefix}pre-execution-review.json").read_bytes()
        )
        checkpoints = checkpoint_chain.get("checkpoints")
        private_review_relative = (
            f".local/product-v0233/attempts/{attempt.attempt_id}/"
            "execution/pre-execution-review.json"
        )
        review_checkpoint_exact = bool(
            isinstance(checkpoints, list)
            and checkpoints
            and isinstance(checkpoints[0], dict)
            and isinstance(checkpoints[0].get("output_artifact_sha256s"), dict)
            and checkpoints[0]["output_artifact_sha256s"].get(private_review_relative)
            == _sha256_file(project / f"{prefix}pre-execution-review.json")
        )
        repository = ProductV0233RepositoryStateManifest.model_validate_json(
            (project / f"{prefix}repository-state-manifest.json").read_bytes()
        )
        progress = _object(project / f"{prefix}progress.json")
        progress_body = {
            key: value for key, value in progress.items() if key != "progress_sha256"
        }
        staging_cleanup_relative = (
            f"{prefix}interrupted-clone-staging-cleanup.json"
        )
        staging_cleanup_exact = staging_cleanup_relative not in evidence
        if staging_cleanup_relative in evidence:
            staging_cleanup = _object(project / staging_cleanup_relative)
            staging_cleanup_body = {
                key: value
                for key, value in staging_cleanup.items()
                if key != "proof_sha256"
            }
            staging_cleanup_exact = (
                blocker.formal_clone_count == 0
                and blocker.cleanup_proof_sha256 is None
                and staging_cleanup.get("schema_version")
                == "ecomsre.product.interrupted-clone-staging-cleanup.v0233"
                and staging_cleanup.get("verdict") == "CLEAN"
                and staging_cleanup.get("attempt_id") == attempt.attempt_id
                and staging_cleanup.get("source_selection_sha256")
                == selection.selection_sha256
                and staging_cleanup.get("proof_sha256")
                == semantic_sha256_v22(staging_cleanup_body)
            )
        private_attempt_root = (
            project / ".local/product-v0233/attempts" / attempt.attempt_id
        )
        staging_absent = not any(
            private_attempt_root.glob(".product-state-clone-v0233-*")
        )
        closure_exact = (
            blocker.formal_clone_count == 0
            and blocker.cleanup_proof_sha256 is None
            and staging_cleanup_exact
            and staging_absent
        )
        if blocker.formal_clone_count == 1:
            closure_relative = f"{prefix}formal-closure.json"
            closure = _object(project / closure_relative)
            assert attempt.latest_checkpoint_sha256 is not None
            closure_sha256 = _verify_cleanup_proof_v0233(
                closure,
                attempt_id=attempt.attempt_id,
                latest_checkpoint_sha256=attempt.latest_checkpoint_sha256,
                blocker=blocker,
            )
            closure_exact = (
                evidence.get(closure_relative)
                == _sha256_file(project / closure_relative)
                and blocker.cleanup_proof_sha256 == closure_sha256
            )
        live_capture_relative = f"{prefix}live-capture-bundle.json"
        acquisition_relative = f"{prefix}diagnosis-acquisition-checkpoint.json"
        lineage_relative = f"{prefix}interrupted-diagnosis-lineage.json"
        live_capture: LiveCaptureBundleV0233 | None = None
        acquisition: DiagnosisAcquisitionCheckpointV0233 | None = None
        if live_capture_relative in evidence:
            live_capture = LiveCaptureBundleV0233.model_validate_json(
                (project / live_capture_relative).read_bytes()
            )
        if acquisition_relative in evidence:
            acquisition = DiagnosisAcquisitionCheckpointV0233.model_validate_json(
                (project / acquisition_relative).read_bytes()
            )
            restored_context = FormalDiagnosisJobContextV0233.build(
                campaign_id=acquisition.campaign_id,
                semantic_generation=acquisition.semantic_generation,
                attempt_id=acquisition.attempt_id,
                diagnosis_generation=1,
                active_profile_sha256=acquisition.active_profile_sha256,
                semantic_surface_sha256=acquisition.semantic_surface_sha256,
                acquisition_sha256=acquisition.acquisition_sha256,
            )
            restore_diagnosis_acquisition_v0233(
                acquisition,
                context=restored_context,
                incident_id=acquisition.incident_id,
                incident_sha256=acquisition.incident_sha256,
            )
        optional_exact = (acquisition is None or live_capture is not None) and (
            acquisition is None
        ) == (lineage_relative not in evidence)
        if acquisition is not None:
            lineage = _object(project / lineage_relative)
            failed_projections = lineage.get("failed_jobs")
            successful_projection = lineage.get("successful_job")
            lineage_body = {
                key: value for key, value in lineage.items() if key != "lineage_sha256"
            }
            optional_exact = bool(
                optional_exact
                and live_capture is not None
                and acquisition.campaign_id == live_capture.campaign_id
                and acquisition.attempt_id == attempt.attempt_id
                and acquisition.attempt_id == live_capture.attempt_id
                and acquisition.semantic_generation == attempt.semantic_generation
                and acquisition.semantic_generation == live_capture.semantic_generation
                and acquisition.semantic_surface_sha256
                == live_capture.semantic_surface_sha256
                and lineage.get("attempt_id") == attempt.attempt_id
                and lineage.get("incident_id") == acquisition.incident_id
                and lineage.get("incident_sha256") == acquisition.incident_sha256
                and lineage.get("acquisition_sha256") == acquisition.acquisition_sha256
                and isinstance(failed_projections, list)
                and lineage.get("failed_job_count") == len(failed_projections)
                and lineage.get("successful_job_count")
                == (1 if isinstance(successful_projection, dict) else 0)
                and lineage.get("terminal_job_count")
                == len(failed_projections)
                + (1 if isinstance(successful_projection, dict) else 0)
                and lineage.get("terminal_job_count", 0) > 0
                and all(
                    isinstance(projection, dict)
                    and projection.get("status") == "FAILED"
                    and projection.get("result_sha256") is None
                    and _job_projection_context_exact_v0233(
                        projection,
                        campaign_id=acquisition.campaign_id,
                        attempt_id=attempt.attempt_id,
                        semantic_generation=attempt.semantic_generation,
                        diagnosis_generation=ordinal + 1,
                        acquisition=acquisition,
                    )
                    and projection.get("projection_sha256")
                    == semantic_sha256_v22(
                        {
                            key: value
                            for key, value in projection.items()
                            if key != "projection_sha256"
                        }
                    )
                    for ordinal, projection in enumerate(failed_projections)
                )
                and (
                    successful_projection is None
                    or (
                        isinstance(successful_projection, dict)
                        and successful_projection.get("status") == "SUCCEEDED"
                        and successful_projection.get("result_sha256") is not None
                        and _job_projection_context_exact_v0233(
                            successful_projection,
                            campaign_id=acquisition.campaign_id,
                            attempt_id=attempt.attempt_id,
                            semantic_generation=attempt.semantic_generation,
                            diagnosis_generation=len(failed_projections) + 1,
                            acquisition=acquisition,
                        )
                        and successful_projection.get("projection_sha256")
                        == semantic_sha256_v22(
                            {
                                key: value
                                for key, value in successful_projection.items()
                                if key != "projection_sha256"
                            }
                        )
                    )
                )
                and lineage.get("lineage_sha256") == semantic_sha256_v22(lineage_body)
            )
        if (
            blocker.terminal != attempt.blocker_terminal
            or not review_checkpoint_exact
            or not closure_exact
            or not optional_exact
            or repository.phase is not RepositoryPhaseV0233.FORMAL_BLOCKED
            or repository.formal_blocker_sha256 != blocker.blocker_sha256
            or repository.cleanup_proof_sha256 != blocker.cleanup_proof_sha256
            or repository.formal_clone_count != blocker.formal_clone_count
            or repository.formal_execution_count != 1
            or repository.new_incident_count != blocker.new_incident_count
            or repository.new_diagnosis_count != blocker.new_diagnosis_count
            or repository.measured_result_count != 0
            or progress.get("progress_sha256") != semantic_sha256_v22(progress_body)
            or progress.get("current_terminal") != blocker.terminal
            or progress.get("formal_blocker_sha256") != blocker.blocker_sha256
            or progress.get("cleanup_proof_sha256") != blocker.cleanup_proof_sha256
            or progress.get("repository_state_manifest_sha256")
            != repository.manifest_sha256
        ):
            raise ValueError("Product v0.2.3.3 recovery attempt history differs")
        assert attempt.latest_checkpoint_sha256 is not None
        _verify_checkpoint_chain_v0233(
            checkpoint_chain,
            attempt_id=attempt.attempt_id,
            semantic_generation=attempt.semantic_generation,
            latest_checkpoint_sha256=attempt.latest_checkpoint_sha256,
            latest_state=attempt.latest_state.value,
            campaign_id=ledger.campaign_id,
            semantic_surface_sha256=review.semantic_surface_sha256,
            operational_surface_sha256=review.operational_surface_sha256,
            source_selection_sha256=selection.selection_sha256,
        )
        if attempt.attempt_id == "attempt-3":
            _verify_attempt3_diagnosis_failure_supplement_v0233(
                project,
                ledger_payload,
            )


def _verify_successor_generations_v0233(
    project: Path,
    attempts: Sequence[Any],
) -> None:
    previous_generation = 1
    previous_semantic_sha256: str | None = None
    for attempt in attempts:
        chain = _object(
            project
            / "docs/analysis/product-v0233-attempts"
            / attempt.attempt_id
            / "checkpoint-chain.json"
        )
        checkpoints = chain.get("checkpoints")
        if not isinstance(checkpoints, list) or not checkpoints:
            raise ValueError("Product v0.2.3.3 recovery generation differs")
        first = FormalExecutionCheckpointV0233.model_validate(checkpoints[0])
        if attempt.semantic_generation not in {
            previous_generation,
            previous_generation + 1,
        } or (
            previous_semantic_sha256 is not None
            and (
                (attempt.semantic_generation == previous_generation)
                != (first.semantic_surface_sha256 == previous_semantic_sha256)
            )
        ):
            raise ValueError("Product v0.2.3.3 recovery generation differs")
        previous_generation = attempt.semantic_generation
        previous_semantic_sha256 = first.semantic_surface_sha256
    if not attempts or attempts[0].semantic_generation != 2:
        raise ValueError("Product v0.2.3.3 recovery generation differs")


def _verify_recovery_blocked_terminal_v0233(
    project: Path,
    ledger: FormalAttemptLedgerV0233,
) -> dict[str, object]:
    expected_ids = tuple(
        f"attempt-{ordinal}" for ordinal in range(1, len(ledger.attempts) + 1)
    )
    if (
        len(ledger.attempts) < 2
        or ledger.attempts[0] != build_legacy_attempt1_record_v0233(project)
        or tuple(attempt.attempt_id for attempt in ledger.attempts) != expected_ids
        or ledger.measured_result_count != 0
        or any(
            attempt.disposition != "NONRECOVERABLE_FAILURE"
            for attempt in ledger.attempts[1:]
        )
        or (project / "docs/results/product-v0233-nofault-acceptance.json").exists()
    ):
        raise ValueError("Product v0.2.3.3 recovery blocked ledger differs")
    _verify_nonrecoverable_history_v0233(project, ledger, ledger.attempts[1:])
    _verify_successor_generations_v0233(project, ledger.attempts[1:])
    latest = ledger.attempts[-1]
    prefix = f"docs/analysis/product-v0233-attempts/{latest.attempt_id}/"
    latest_repository_path = project / f"{prefix}repository-state-manifest.json"
    latest_progress_path = project / f"{prefix}progress.json"
    canonical_repository_path = (
        project / "config/product-v0233/recovery-repository-state-manifest.json"
    )
    canonical_progress_path = (
        project / "docs/analysis/product-v0233-recovery-progress.json"
    )
    repository = ProductV0233RepositoryStateManifest.model_validate_json(
        canonical_repository_path.read_bytes()
    )
    progress = _object(canonical_progress_path)
    if (
        canonical_repository_path.read_bytes() != latest_repository_path.read_bytes()
        or canonical_progress_path.read_bytes() != latest_progress_path.read_bytes()
        or repository.formal_blocker_sha256 is None
        or repository.measured_result_count != 0
        or progress.get("current_terminal") != latest.blocker_terminal
    ):
        raise ValueError("Product v0.2.3.3 recovery canonical terminal differs")
    return {
        "terminal": latest.blocker_terminal,
        "attempt_id": latest.attempt_id,
        "semantic_generation": latest.semantic_generation,
        "formal_clone_count": repository.formal_clone_count,
        "formal_execution_count": repository.formal_execution_count,
        "new_incident_count": repository.new_incident_count,
        "new_diagnosis_count": repository.new_diagnosis_count,
        "measured_result_count": 0,
        "action_authority": "NONE",
        "closure": (
            "CLEAN"
            if repository.formal_clone_count == 1
            or f"{prefix}interrupted-clone-staging-cleanup.json"
            in latest.evidence_sha256_by_path
            else "NOT_APPLICABLE"
        ),
    }


def _verify_measured_traffic_capture_v0233(
    *,
    traffic: FormalTrafficResultV0233,
    incident_binding: IncidentTrafficBindingV0232,
    live_capture: LiveCaptureBundleV0233,
    acquisition: DiagnosisAcquisitionCheckpointV0233,
    selection: FreshFormalSourceSelectionV0233,
    clone: FreshFormalStateCloneV0233,
    restart: BaselineRestartProofV0233,
    tracked_formal_profile_sha256: str,
    tracked_engine_profile_sha256: str,
    tracked_traffic_contract_sha256: str,
) -> None:
    raw_runtime = live_capture.fresh_runtime_snapshot_raw
    if (
        traffic.formal_profile_sha256 != tracked_formal_profile_sha256
        or traffic.execution.run.profile_sha256 != tracked_engine_profile_sha256
        or traffic.traffic_contract_sha256 != tracked_traffic_contract_sha256
        or traffic.execution.run.contract_sha256 != tracked_traffic_contract_sha256
        or live_capture.traffic_contract_sha256 != traffic.traffic_contract_sha256
        or live_capture.formal_profile_sha256 != traffic.formal_profile_sha256
        or incident_binding.incident_id != acquisition.incident_id
        or incident_binding.traffic_execution_sha256
        != traffic.execution.execution_sha256
        or incident_binding.contract_sha256 != traffic.execution.run.contract_sha256
        or incident_binding.contract_sha256 != traffic.traffic_contract_sha256
        or incident_binding.formal_profile_sha256
        != traffic.execution.run.profile_sha256
        or incident_binding.episode_started_at != live_capture.episode_started_at
        or incident_binding.episode_ended_at != live_capture.episode_ended_at
        or incident_binding.traffic_started_at != traffic.execution.run.started_at
        or incident_binding.traffic_ended_at != traffic.execution.run.ended_at
        or traffic.episode_started_at != live_capture.episode_started_at
        or traffic.episode_ended_at > live_capture.episode_ended_at
        or raw_runtime.environment_id != restart.environment_id
        or raw_runtime.environment_id != selection.active_environment_id
        or raw_runtime.environment_id != clone.active_environment_id
        or restart.environment_id != selection.active_environment_id
        or restart.environment_id != clone.active_environment_id
        or raw_runtime.observed_at != live_capture.episode_ended_at
        or live_capture.source_selection_sha256 != selection.selection_sha256
        or clone.source_selection_sha256 != selection.selection_sha256
        or acquisition.incident_observation_started_at
        != live_capture.episode_started_at
        or acquisition.incident_observation_ended_at != live_capture.episode_ended_at
        or acquisition.incident_observation_started_at
        != incident_binding.episode_started_at
        or acquisition.incident_observation_ended_at
        != incident_binding.episode_ended_at
    ):
        raise ValueError("Product v0.2.3.3 measured traffic binding differs")


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
    _verify_nonrecoverable_history_v0233(project, ledger, ledger.attempts[1:-1])
    _verify_successor_generations_v0233(project, ledger.attempts[1:])
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
    live_capture = LiveCaptureBundleV0233.model_validate_json(
        (attempt_root / "live-capture-bundle.json").read_bytes()
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
    attempt_review = RecoveryPreExecutionReviewV0233.model_validate_json(
        (attempt_root / "pre-execution-review.json").read_bytes()
    )
    selection = FreshFormalSourceSelectionV0233.model_validate_json(
        (project / "config/product-v0233/source-selection.json").read_bytes()
    )
    tracked_formal_profile = load_fresh_traffic_profile_v0233(
        project, role="FORMAL"
    )
    tracked_engine_profile = tracked_formal_profile.engine_profile_v0232()
    tracked_traffic_contract = load_checkout_traffic_contract_v0232(project)
    restored_context = FormalDiagnosisJobContextV0233.build(
        campaign_id=acquisition.campaign_id,
        semantic_generation=acquisition.semantic_generation,
        attempt_id=acquisition.attempt_id,
        diagnosis_generation=1,
        active_profile_sha256=acquisition.active_profile_sha256,
        semantic_surface_sha256=acquisition.semantic_surface_sha256,
        acquisition_sha256=acquisition.acquisition_sha256,
    )
    restore_diagnosis_acquisition_v0233(
        acquisition,
        context=restored_context,
        incident_id=acquisition.incident_id,
        incident_sha256=acquisition.incident_sha256,
    )
    runtime_bindings = tuple(
        binding.binding_payload
        for binding in acquisition.connector_provenance_bindings
        if isinstance(binding.binding_payload, RuntimeSnapshotEvidenceBindingV0232)
    )
    if len(runtime_bindings) != 1:
        raise ValueError("Product v0.2.3.3 recovered Runtime binding differs")
    runtime_binding = runtime_bindings[0]
    raw_runtime = live_capture.fresh_runtime_snapshot_raw
    checkout_services = tuple(
        service for service in raw_runtime.services if service.logical_service == "checkout"
    )
    _verify_measured_traffic_capture_v0233(
        traffic=traffic,
        incident_binding=incident_binding,
        live_capture=live_capture,
        acquisition=acquisition,
        selection=selection,
        clone=clone,
        restart=restart,
        tracked_formal_profile_sha256=tracked_formal_profile.profile_sha256,
        tracked_engine_profile_sha256=tracked_engine_profile.profile_sha256,
        tracked_traffic_contract_sha256=tracked_traffic_contract.contract_sha256,
    )
    checkpoint_chain = _object(attempt_root / "checkpoint-chain.json")
    assert attempt.latest_checkpoint_sha256 is not None
    _verify_checkpoint_chain_v0233(
        checkpoint_chain,
        attempt_id=attempt.attempt_id,
        semantic_generation=attempt.semantic_generation,
        latest_checkpoint_sha256=attempt.latest_checkpoint_sha256,
        latest_state=attempt.latest_state.value,
        campaign_id=ledger.campaign_id,
        semantic_surface_sha256=attempt_review.semantic_surface_sha256,
        operational_surface_sha256=None,
        source_selection_sha256=selection.selection_sha256,
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
        (
            project / "config/product-v0233/recovery-repository-state-manifest.json"
        ).read_bytes()
    )
    review = RecoveryPreExecutionReviewV0233.model_validate_json(
        (
            project / "docs/analysis/product-v0233-recovery-pre-execution-review.json"
        ).read_bytes()
    )
    progress = _object(project / "docs/analysis/product-v0233-recovery-progress.json")
    progress_body = {
        key: value for key, value in progress.items() if key != "progress_sha256"
    }
    failed_jobs = lineage.get("preserved_failed_job_ids")
    failed_job_projections = lineage.get("preserved_failed_jobs")
    successful_job = lineage.get("successful_job")
    failed_job_count = len(failed_jobs) if isinstance(failed_jobs, list) else -1
    new_diagnosis_count = repository.new_diagnosis_count
    if new_diagnosis_count is None:
        raise ValueError("Product v0.2.3.3 recovered terminal differs")
    _verify_measured_claim_documents_v0233(
        project,
        result=result,
        diagnosis=diagnosis,
        new_diagnosis_count=new_diagnosis_count,
    )
    _verify_measured_claim_correction_v0233(
        project,
        attempt_id=attempt.attempt_id,
        result=result,
        diagnosis=diagnosis,
    )
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
            and _job_projection_context_exact_v0233(
                projection,
                campaign_id=ledger.campaign_id,
                attempt_id=attempt.attempt_id,
                semantic_generation=attempt.semantic_generation,
                diagnosis_generation=ordinal + 1,
                acquisition=acquisition,
            )
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
        and _job_projection_context_exact_v0233(
            successful_job,
            campaign_id=ledger.campaign_id,
            attempt_id=attempt.attempt_id,
            semantic_generation=attempt.semantic_generation,
            diagnosis_generation=len(failed_jobs) + 1,
            acquisition=acquisition,
        )
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
        and lineage.get("diagnosis_result_sha256") == result.diagnosis_result_sha256
        and lineage.get("lineage_sha256") == semantic_sha256_v22(lineage_body)
    )
    required_public = (
        "docs/results/product-v0233-nofault-acceptance.md",
        "docs/results/product-v0233-limitations.md",
        "docs/results/product-v0233-interview-brief.md",
        "docs/analysis/product-v0233-knowledge-loop-handoff.md",
    )
    expected_evidence_paths = {
        *(
            f"docs/analysis/product-v0233-attempts/{attempt.attempt_id}/{name}"
            for name in (
                "formal-state-clone.json",
                "pre-execution-review.json",
                "checkpoint-chain.json",
                "formal-closure.json",
                "live-capture-bundle.json",
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
            )
        ),
        "docs/results/product-v0233-nofault-acceptance.json",
        "docs/results/product-v0233-nofault-acceptance.md",
        "docs/results/product-v0233-limitations.md",
        "docs/results/product-v0233-interview-brief.md",
        "docs/analysis/product-v0233-knowledge-loop-handoff.md",
    }
    evidence_exact = set(
        attempt.evidence_sha256_by_path
    ) == expected_evidence_paths and all(
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
        or pipeline.get("terminal") != "ECOMSRE_PRODUCT_V0233_DIAGNOSIS_PIPELINE_PASS"
        or typed_pipeline.job_id != lineage.get("successful_job_id")
        or not isinstance(successful_job, dict)
        or successful_job.get("journal_tail_sha256")
        != typed_pipeline.journal_tail_sha256
        or typed_pipeline.journal_tail_sha256 != result.stage_journal_tail_sha256
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
        or result.formal_traffic_execution_sha256 != traffic.execution.execution_sha256
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
        or live_capture.active_profile_sha256 != selection.active_profile_sha256
        or live_capture.active_profile_sha256 != restart.active_profile_sha256
        or live_capture.active_profile_sha256 != acquisition.active_profile_sha256
        or live_capture.active_baseline_id != selection.active_baseline_id
        or live_capture.active_baseline_id != restart.active_baseline_id
        or live_capture.active_baseline_sha256 != selection.active_baseline_sha256
        or live_capture.active_baseline_sha256 != restart.active_baseline_sha256
        or live_capture.active_baseline_sha256 != acquisition.baseline_sha256
        or traffic.formal_profile_sha256 != tracked_formal_profile.profile_sha256
        or traffic.execution.run.profile_sha256
        != tracked_engine_profile.profile_sha256
        or traffic.traffic_contract_sha256
        != tracked_traffic_contract.contract_sha256
        or traffic.execution.run.contract_sha256
        != tracked_traffic_contract.contract_sha256
        or live_capture.traffic_contract_sha256 != traffic.traffic_contract_sha256
        or live_capture.formal_profile_sha256 != traffic.formal_profile_sha256
        or incident_binding.incident_id != acquisition.incident_id
        or incident_binding.traffic_execution_sha256
        != traffic.execution.execution_sha256
        or incident_binding.contract_sha256 != traffic.execution.run.contract_sha256
        or incident_binding.contract_sha256 != traffic.traffic_contract_sha256
        or incident_binding.formal_profile_sha256
        != traffic.execution.run.profile_sha256
        or incident_binding.episode_started_at != live_capture.episode_started_at
        or incident_binding.episode_ended_at != live_capture.episode_ended_at
        or incident_binding.traffic_started_at != traffic.execution.run.started_at
        or incident_binding.traffic_ended_at != traffic.execution.run.ended_at
        or traffic.episode_started_at != live_capture.episode_started_at
        or traffic.episode_ended_at > live_capture.episode_ended_at
        or live_capture.runtime_connector_binding_sha256
        != authority.runtime_connector_binding_sha256
        or live_capture.runtime_connector_binding_sha256
        != fresh_snapshot.runtime_connector_binding_sha256
        or live_capture.runtime_connector_binding_sha256 != raw_runtime.authority_sha256
        or authority.pilot_runtime_authority_sha256
        != fresh_snapshot.pilot_runtime_authority_sha256
        or authority.pilot_runtime_authority_sha256
        != runtime_binding.pilot_runtime_authority_sha256
        or authority.runtime_continuity_descriptor_sha256
        != fresh_snapshot.runtime_continuity_descriptor_sha256
        or raw_runtime.snapshot_sha256 != fresh_snapshot.runtime_snapshot_sha256
        or raw_runtime.snapshot_sha256 != runtime_binding.runtime_snapshot_sha256
        or raw_runtime.authority_sha256
        != runtime_binding.runtime_snapshot_authority_sha256
        or raw_runtime.authority_sha256 != runtime_binding.connector_binding_sha256
        or raw_runtime.environment_id != restart.environment_id
        or raw_runtime.environment_id != selection.active_environment_id
        or raw_runtime.environment_id != clone.active_environment_id
        or restart.environment_id != selection.active_environment_id
        or restart.environment_id != clone.active_environment_id
        or raw_runtime.environment_id
        != runtime_binding.runtime_snapshot_environment_id
        or raw_runtime.observed_at != fresh_snapshot.observed_at
        or raw_runtime.observed_at != runtime_binding.runtime_snapshot_observed_at
        or raw_runtime.observed_at != live_capture.episode_ended_at
        or acquisition.runtime_snapshot_binding_sha256
        != runtime_binding.binding_sha256
        or len(checkout_services) != 1
        or len(raw_runtime.services) != 1
        or checkout_services[0].state.value != "RUNNING"
        or checkout_services[0].healthy is not True
        or checkout_services[0].restart_count != 0
        or live_capture.campaign_id != ledger.campaign_id
        or live_capture.attempt_id != attempt.attempt_id
        or live_capture.semantic_generation != attempt.semantic_generation
        or live_capture.semantic_surface_sha256 != review.semantic_surface_sha256
        or live_capture.source_selection_sha256 != selection.selection_sha256
        or clone.source_selection_sha256 != selection.selection_sha256
        or live_capture.formal_clone_sha256 != clone.clone_sha256
        or live_capture.runtime_authority_proof_sha256 != authority.proof_sha256
        or live_capture.baseline_restart_proof_sha256 != restart.proof_sha256
        or live_capture.formal_traffic_result_sha256 != traffic.result_sha256
        or live_capture.traffic_execution_sha256 != traffic.execution.execution_sha256
        or live_capture.fresh_runtime_snapshot_raw.snapshot_sha256
        != fresh_snapshot.runtime_snapshot_sha256
        or live_capture.service_identity_sha256 != acquisition.service_identity_sha256
        or live_capture.capability_sha256 != acquisition.capability_sha256
        or acquisition.incident_observation_started_at
        != live_capture.episode_started_at
        or acquisition.incident_observation_ended_at != live_capture.episode_ended_at
        or acquisition.incident_observation_started_at
        != incident_binding.episode_started_at
        or acquisition.incident_observation_ended_at
        != incident_binding.episode_ended_at
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
            (project / relative).is_symlink() or not (project / relative).is_file()
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
    if len(ledger.attempts) > 1:
        return _verify_recovery_blocked_terminal_v0233(project, ledger)
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
