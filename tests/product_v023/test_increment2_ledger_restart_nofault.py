from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    MetricKindV22,
    ReadSourceStatusV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    semantic_sha256_v22,
)
from ecomsre.product.baselines import (
    BaselineBuildModeV1,
    BaselineBuildPolicyV1,
    EnvironmentBaselineV1,
    build_environment_baseline,
)
from ecomsre.product.connectors.base import ConnectorQueryResultV1, ConnectorWindowV1
from ecomsre.product.incidents.contracts import (
    ActionAuthorityV1,
    DiagnosisLaneV1,
    DiagnosisResultV1,
    DiagnosisTerminalV1,
    EvidenceBundleV1,
    EvidenceObjectV1,
    IncidentRecordV1,
)
from ecomsre.product.contracts import ServiceIdentityMapV1, ServiceIdentityV1
from ecomsre.product.errors import ProductError
from ecomsre.product.jobs.worker import should_ingest_open_world_v023
from ecomsre.product.jobs.contracts import (
    ProductJobRecordV1,
    ProductJobStatusV1,
    ProductJobTypeV1,
)
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    ACTIVE_PROFILE_BINDING_SHA256_V023,
)
from ecomsre.product.pilot.baseline_attempts_v023 import (
    BASELINE_READINESS_BLOCKED_V023,
    BASELINE_READINESS_PASS_V023,
    BASELINE_REPAIR_REQUIRED_V023,
    BaselineAttemptCompletionV023,
    BaselineAttemptFailureKindV023,
    BaselineAttemptLedgerV023,
    BaselineAttemptStartV023,
    BaselineAttemptV023,
    BaselineChangedParameterV023,
    BaselineTrafficResultV023,
    baseline_builder_job_evidence_sha256_v023,
    baseline_builder_interruption_evidence_sha256_v023,
    baseline_builder_submission_failure_evidence_sha256_v023,
    baseline_builder_transport_failure_evidence_sha256_v023,
    validate_changed_attempt_parameter_v023,
)
from ecomsre.product.pilot.baseline_readiness_v023 import (
    ACTIVE_PROFILE_SHA256_V023,
    BaselineRejectionReasonCodeV023,
    BaselineWindowAuditV023,
    BaselineWindowEvaluationV023,
    ProductBaselineReadinessAuditV023,
    ProductBaselineReadinessAuditRepositoryV023,
)
from ecomsre.product.pilot.baseline_restart_v023 import (
    BASELINE_RESTART_PASS_V023,
    BaselineRestartProofV023,
    BaselineRestartSnapshotV023,
)
from ecomsre.product.pilot.live_baseline_readiness_v023 import (
    _failed_builder_kind_v023,
    _read_latest_baseline_audit_from_store_v023,
)
from ecomsre.product.pilot.nofault_acceptance_v023 import (
    NOFAULT_CAPABILITY_LIMITED_V023,
    NOFAULT_FULLY_SUPPORTED_V023,
    NOFAULT_NOT_SUPPORTED_V023,
    NoFaultCapabilityAssessmentV023,
    NoFaultExecutionProfileV023,
    NoFaultQueueSnapshotV023,
    NoFaultTrafficResultV023,
    score_nofault_v023,
)
from ecomsre.product.storage.sqlite_store import SqliteStoreV1

from tests.product_v023.test_increment2_baseline_preflight import (
    NOW,
    _evaluation,
    _logs,
    _metric,
    _result,
    _window,
    _window_evidence,
)


def _traffic(
    *,
    passed: bool,
    semantics: dict[str, Any],
    profile_sha256: str,
) -> BaselineTrafficResultV023:
    return BaselineTrafficResultV023.build(
        planned_request_count=semantics["healthy_traffic_request_count"],
        completed_request_count=(
            semantics["healthy_traffic_request_count"] if passed else 120
        ),
        error_count=0 if passed else 10,
        requests_per_second=semantics["healthy_traffic_requests_per_second"],
        maximum_error_fraction=semantics["maximum_error_fraction"],
        queue_fault_flag=0,
        profile_sha256=profile_sha256,
        semantics_sha256=semantic_sha256_v22(semantics),
        passed=passed,
    )


def _builder_job_record(
    *,
    job_id: str,
    audit: ProductBaselineReadinessAuditV023,
    baseline: EnvironmentBaselineV1 | None,
    status: ProductJobStatusV1,
    safe_error_code: str | None = None,
) -> ProductJobRecordV1:
    return ProductJobRecordV1(
        job_id=job_id,
        job_type=ProductJobTypeV1.BASELINE_BUILD,
        status=status,
        payload={
            "environment_id": audit.environment_id,
            "request": {
                "build_policy": audit.build_policy,
                "candidate_services": ["checkout"],
                "planned_windows": [
                    item.window.model_dump(mode="json")
                    for item in audit.evaluation.windows
                ],
                "activate": True,
            },
        },
        result=(
            None
            if status is ProductJobStatusV1.FAILED
            else {
                **baseline.model_dump(mode="json"),
                "readiness_audit_v023": audit.model_dump(mode="json"),
            }
        ),
        safe_error_code=safe_error_code,
        idempotency_key="product-v023-baseline",
        claimed_by="worker-v023",
        lease_expires_at=None,
        attempt_count=1,
        created_at=1.0,
        updated_at=2.0,
    )


def _attempt_semantics(
    *,
    connector_query_binding_sha256: str = "a" * 64,
    service_alias_binding_sha256: str = "b" * 64,
    implementation_revision_sha256: str = "c" * 64,
) -> dict[str, float | int | str]:
    return {
        "healthy_traffic_request_count": 180,
        "healthy_traffic_requests_per_second": 0.5,
        "maximum_error_fraction": 0.01,
        "warmup_seconds": 180,
        "baseline_accumulation_seconds": 360,
        "minimum_accepted_windows": 4,
        "queue_fault_flag": 0,
        "connector_query_binding_sha256": connector_query_binding_sha256,
        "service_alias_binding_sha256": service_alias_binding_sha256,
        "implementation_revision_sha256": implementation_revision_sha256,
    }


def _windows(*, offset_minutes: int) -> tuple:
    return tuple(
        _window(index).model_copy(
            update={
                "started_at": _window(index).started_at
                + timedelta(minutes=offset_minutes),
                "ended_at": _window(index).ended_at + timedelta(minutes=offset_minutes),
            }
        )
        for index in range(5)
    )


def test_attempt_two_requires_exactly_one_declared_semantic_change() -> None:
    semantics = _attempt_semantics()
    first_start = BaselineAttemptStartV023.build(
        attempt_ordinal=1,
        changed_parameter=BaselineChangedParameterV023.INITIAL,
        prior_completion_sha256=None,
        environment_id="env-" + "1" * 24,
        product_data_root="/tmp/product-v023-attempt-1",
        profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        planned_windows=_windows(offset_minutes=0),
        semantic_inputs=semantics,
        started_at=NOW,
    )
    failed_traffic = _traffic(
        passed=False,
        semantics=semantics,
        profile_sha256=first_start.profile_sha256,
    )
    first_completion = BaselineAttemptCompletionV023.build(
        attempt_ordinal=1,
        start_sha256=first_start.start_sha256,
        traffic_result=failed_traffic,
        per_window_audit=None,
        per_window_audit_sha256=None,
        builder_job_id=None,
        builder_job_record=None,
        builder_job_evidence_sha256=None,
        builder_job_disposition="NOT_SUBMITTED",
        active_baseline_id=None,
        active_baseline_sha256=None,
        cleanup="CLEAN",
        failure_kind=BaselineAttemptFailureKindV023.HEALTHY_TRAFFIC,
        failure_code="HEALTHY_TRAFFIC_INCOMPLETE",
        failure_evidence_sha256=failed_traffic.result_sha256,
        terminal=BASELINE_REPAIR_REQUIRED_V023,
        completed_at=NOW + timedelta(minutes=4),
    )
    validate_changed_attempt_parameter_v023(
        prior_completion=first_completion,
        changed_parameter=BaselineChangedParameterV023.IMPLEMENTATION_REVISION_SHA256,
    )
    with pytest.raises(ValueError, match="does not match prior failure"):
        validate_changed_attempt_parameter_v023(
            prior_completion=first_completion,
            changed_parameter=(
                BaselineChangedParameterV023.CONNECTOR_QUERY_BINDING_SHA256
            ),
        )
    second_start = BaselineAttemptStartV023.build(
        attempt_ordinal=2,
        changed_parameter=(BaselineChangedParameterV023.IMPLEMENTATION_REVISION_SHA256),
        prior_completion_sha256=first_completion.completion_sha256,
        environment_id="env-" + "3" * 24,
        product_data_root="/tmp/product-v023-attempt-2",
        profile_sha256=first_start.profile_sha256,
        planned_windows=_windows(offset_minutes=6),
        semantic_inputs={**semantics, "implementation_revision_sha256": "d" * 64},
        started_at=NOW + timedelta(minutes=5),
    )
    second_baseline, second_audit = _baseline_audit(
        environment_id=second_start.environment_id,
        baseline_id="base-" + "5" * 24,
        window_offset_minutes=6,
    )
    second_job = _builder_job_record(
        job_id="job-" + "4" * 24,
        audit=second_audit,
        baseline=second_baseline,
        status=ProductJobStatusV1.SUCCEEDED,
    )
    second_completion = BaselineAttemptCompletionV023.build(
        attempt_ordinal=2,
        start_sha256=second_start.start_sha256,
        traffic_result=_traffic(
            passed=True,
            semantics={**semantics, "implementation_revision_sha256": "d" * 64},
            profile_sha256=first_start.profile_sha256,
        ),
        per_window_audit=second_audit,
        per_window_audit_sha256=second_audit.audit_sha256,
        builder_job_id="job-" + "4" * 24,
        builder_job_record=second_job,
        builder_job_evidence_sha256=baseline_builder_job_evidence_sha256_v023(
            second_job
        ),
        builder_job_disposition="SUCCEEDED",
        active_baseline_id="base-" + "5" * 24,
        active_baseline_sha256=second_baseline.baseline_sha256,
        cleanup="CLEAN",
        terminal=BASELINE_READINESS_PASS_V023,
        completed_at=NOW + timedelta(minutes=10),
    )
    attempts = (
        BaselineAttemptV023(start=first_start, completion=first_completion),
        BaselineAttemptV023(start=second_start, completion=second_completion),
    )

    ledger = BaselineAttemptLedgerV023.build(attempts)

    assert ledger.attempts[1].start.prior_completion_sha256 == (
        first_completion.completion_sha256
    )
    assert ledger.attempts[1].start.changed_parameter.value == (
        "implementation_revision_sha256"
    )

    changed_twice = second_start.model_dump(mode="json")
    changed_twice["semantic_inputs"]["service_alias_binding_sha256"] = "e" * 64
    changed_twice["semantics_sha256"] = semantic_sha256_v22(
        changed_twice["semantic_inputs"]
    )
    changed_twice["start_sha256"] = semantic_sha256_v22(
        {key: value for key, value in changed_twice.items() if key != "start_sha256"}
    )
    replacement_completion = second_completion.model_dump(mode="python")
    for key in (
        "schema_version",
        "completion_sha256",
        "action_authority",
        "agent_writes",
        "runbook_executions",
    ):
        replacement_completion.pop(key)
    replacement_completion["start_sha256"] = changed_twice["start_sha256"]
    replacement_completion["traffic_result"] = _traffic(
        passed=True,
        semantics=changed_twice["semantic_inputs"],
        profile_sha256=first_start.profile_sha256,
    )
    invalid_attempts = (
        attempts[0],
        BaselineAttemptV023(
            start=BaselineAttemptStartV023.model_validate(changed_twice),
            completion=BaselineAttemptCompletionV023.build(**replacement_completion),
        ),
    )
    with pytest.raises(ValidationError, match="more than one parameter"):
        BaselineAttemptLedgerV023.build(invalid_attempts)

    mismatched_start = BaselineAttemptStartV023.build(
        attempt_ordinal=2,
        changed_parameter=BaselineChangedParameterV023.CONNECTOR_QUERY_BINDING_SHA256,
        prior_completion_sha256=first_completion.completion_sha256,
        environment_id="env-" + "6" * 24,
        product_data_root="/tmp/product-v023-attempt-mismatch",
        profile_sha256=first_start.profile_sha256,
        planned_windows=_windows(offset_minutes=6),
        semantic_inputs={**semantics, "connector_query_binding_sha256": "d" * 64},
        started_at=NOW + timedelta(minutes=5),
    )
    mismatched_baseline, mismatched_audit = _baseline_audit(
        environment_id=mismatched_start.environment_id,
        baseline_id="base-" + "8" * 24,
        window_offset_minutes=6,
    )
    mismatched_job = _builder_job_record(
        job_id="job-" + "7" * 24,
        audit=mismatched_audit,
        baseline=mismatched_baseline,
        status=ProductJobStatusV1.SUCCEEDED,
    )
    mismatched_completion = BaselineAttemptCompletionV023.build(
        attempt_ordinal=2,
        start_sha256=mismatched_start.start_sha256,
        traffic_result=_traffic(
            passed=True,
            semantics={**semantics, "connector_query_binding_sha256": "d" * 64},
            profile_sha256=first_start.profile_sha256,
        ),
        per_window_audit=mismatched_audit,
        per_window_audit_sha256=mismatched_audit.audit_sha256,
        builder_job_id="job-" + "7" * 24,
        builder_job_record=mismatched_job,
        builder_job_evidence_sha256=baseline_builder_job_evidence_sha256_v023(
            mismatched_job
        ),
        builder_job_disposition="SUCCEEDED",
        active_baseline_id="base-" + "8" * 24,
        active_baseline_sha256=mismatched_baseline.baseline_sha256,
        cleanup="CLEAN",
        terminal=BASELINE_READINESS_PASS_V023,
        completed_at=NOW + timedelta(minutes=10),
    )
    with pytest.raises(ValidationError, match="does not match prior failure"):
        BaselineAttemptLedgerV023.build(
            (
                attempts[0],
                BaselineAttemptV023(
                    start=mismatched_start,
                    completion=mismatched_completion,
                ),
            )
        )


def test_repair_terminal_cannot_relabel_a_successful_baseline() -> None:
    semantics = _attempt_semantics()
    start = BaselineAttemptStartV023.build(
        attempt_ordinal=1,
        changed_parameter=BaselineChangedParameterV023.INITIAL,
        prior_completion_sha256=None,
        environment_id="env-" + "1" * 24,
        product_data_root="/tmp/product-v023-success",
        profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        planned_windows=_windows(offset_minutes=0),
        semantic_inputs=semantics,
        started_at=NOW,
    )
    baseline, audit = _baseline_audit(
        environment_id=start.environment_id,
        baseline_id="base-" + "3" * 24,
    )
    job = _builder_job_record(
        job_id="job-" + "2" * 24,
        audit=audit,
        baseline=baseline,
        status=ProductJobStatusV1.SUCCEEDED,
    )
    with pytest.raises(ValidationError, match="contradictory"):
        BaselineAttemptCompletionV023.build(
            attempt_ordinal=1,
            start_sha256=start.start_sha256,
            traffic_result=_traffic(
                passed=True,
                semantics=semantics,
                profile_sha256=start.profile_sha256,
            ),
            per_window_audit=audit,
            per_window_audit_sha256=audit.audit_sha256,
            builder_job_id="job-" + "2" * 24,
            builder_job_record=job,
            builder_job_evidence_sha256=baseline_builder_job_evidence_sha256_v023(job),
            builder_job_disposition="SUCCEEDED",
            active_baseline_id="base-" + "3" * 24,
            active_baseline_sha256=baseline.baseline_sha256,
            cleanup="CLEAN",
            failure_kind=BaselineAttemptFailureKindV023.IMPLEMENTATION,
            failure_code="FALSE_REPAIR_TERMINAL",
            failure_evidence_sha256="e" * 64,
            terminal=BASELINE_REPAIR_REQUIRED_V023,
            completed_at=NOW + timedelta(minutes=4),
        )


def test_passing_attempt_requires_active_checkout_builder_request_and_result() -> None:
    semantics = _attempt_semantics()
    start = BaselineAttemptStartV023.build(
        attempt_ordinal=1,
        changed_parameter=BaselineChangedParameterV023.INITIAL,
        prior_completion_sha256=None,
        environment_id="env-" + "1" * 24,
        product_data_root="/tmp/product-v023-wrong-builder-request",
        profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        planned_windows=_windows(offset_minutes=0),
        semantic_inputs=semantics,
        started_at=NOW,
    )
    baseline, audit = _baseline_audit(
        environment_id=start.environment_id,
        baseline_id="base-" + "3" * 24,
    )
    job = _builder_job_record(
        job_id="job-" + "2" * 24,
        audit=audit,
        baseline=baseline,
        status=ProductJobStatusV1.SUCCEEDED,
    )
    wrong_payload = job.model_dump(mode="python")
    wrong_payload["payload"]["request"]["activate"] = False
    wrong_payload["payload"]["request"]["candidate_services"] = ["cart"]
    wrong_payload["result"]["active"] = False
    wrong_job = ProductJobRecordV1.model_validate(wrong_payload)

    with pytest.raises(ValidationError, match="JobRecord binding differs"):
        BaselineAttemptCompletionV023.build(
            attempt_ordinal=1,
            start_sha256=start.start_sha256,
            traffic_result=_traffic(
                passed=True,
                semantics=semantics,
                profile_sha256=start.profile_sha256,
            ),
            per_window_audit=audit,
            per_window_audit_sha256=audit.audit_sha256,
            builder_job_id=wrong_job.job_id,
            builder_job_record=wrong_job,
            builder_job_evidence_sha256=(
                baseline_builder_job_evidence_sha256_v023(wrong_job)
            ),
            builder_job_disposition="SUCCEEDED",
            active_baseline_id=baseline.baseline_id,
            active_baseline_sha256=baseline.baseline_sha256,
            cleanup="CLEAN",
            terminal=BASELINE_READINESS_PASS_V023,
            completed_at=NOW + timedelta(minutes=4),
        )


def test_failed_builder_cannot_claim_an_active_baseline() -> None:
    semantics = _attempt_semantics()
    start = BaselineAttemptStartV023.build(
        attempt_ordinal=1,
        changed_parameter=BaselineChangedParameterV023.INITIAL,
        prior_completion_sha256=None,
        environment_id="env-" + "1" * 24,
        product_data_root="/tmp/product-v023-contradictory-builder",
        profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        planned_windows=_windows(offset_minutes=0),
        semantic_inputs=semantics,
        started_at=NOW,
    )
    baseline, audit = _baseline_audit(
        environment_id=start.environment_id,
        baseline_id="base-" + "3" * 24,
    )
    job = _builder_job_record(
        job_id="job-" + "2" * 24,
        audit=audit,
        baseline=None,
        status=ProductJobStatusV1.FAILED,
        safe_error_code="BUILDER_FAILED",
    )
    with pytest.raises(ValidationError, match="contradictory"):
        BaselineAttemptCompletionV023.build(
            attempt_ordinal=1,
            start_sha256=start.start_sha256,
            traffic_result=_traffic(
                passed=True,
                semantics=semantics,
                profile_sha256=start.profile_sha256,
            ),
            per_window_audit=audit,
            per_window_audit_sha256=audit.audit_sha256,
            builder_job_id="job-" + "2" * 24,
            builder_job_record=job,
            builder_job_evidence_sha256=baseline_builder_job_evidence_sha256_v023(job),
            builder_job_disposition="FAILED",
            active_baseline_id="base-" + "3" * 24,
            active_baseline_sha256=baseline.baseline_sha256,
            cleanup="CLEAN",
            failure_kind=BaselineAttemptFailureKindV023.BUILDER,
            failure_code="BUILDER_FAILED",
            failure_evidence_sha256="d" * 64,
            terminal=BASELINE_REPAIR_REQUIRED_V023,
            completed_at=NOW + timedelta(minutes=4),
        )


def test_attempt_two_requires_clean_attempt_one_cleanup() -> None:
    semantics = _attempt_semantics()
    first_start = BaselineAttemptStartV023.build(
        attempt_ordinal=1,
        changed_parameter=BaselineChangedParameterV023.INITIAL,
        prior_completion_sha256=None,
        environment_id="env-" + "1" * 24,
        product_data_root="/tmp/product-v023-blocked-cleanup-1",
        profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        planned_windows=_windows(offset_minutes=0),
        semantic_inputs=semantics,
        started_at=NOW,
    )
    failed_traffic = _traffic(
        passed=False,
        semantics=semantics,
        profile_sha256=first_start.profile_sha256,
    )
    first_completion = BaselineAttemptCompletionV023.build(
        attempt_ordinal=1,
        start_sha256=first_start.start_sha256,
        traffic_result=failed_traffic,
        per_window_audit=None,
        per_window_audit_sha256=None,
        builder_job_id=None,
        builder_job_record=None,
        builder_job_evidence_sha256=None,
        builder_job_disposition="NOT_SUBMITTED",
        active_baseline_id=None,
        active_baseline_sha256=None,
        cleanup="BLOCKED",
        failure_kind=BaselineAttemptFailureKindV023.HEALTHY_TRAFFIC,
        failure_code="HEALTHY_TRAFFIC_INCOMPLETE",
        failure_evidence_sha256=failed_traffic.result_sha256,
        terminal=BASELINE_REPAIR_REQUIRED_V023,
        completed_at=NOW + timedelta(minutes=4),
    )
    second_start = BaselineAttemptStartV023.build(
        attempt_ordinal=2,
        changed_parameter=BaselineChangedParameterV023.IMPLEMENTATION_REVISION_SHA256,
        prior_completion_sha256=first_completion.completion_sha256,
        environment_id="env-" + "2" * 24,
        product_data_root="/tmp/product-v023-blocked-cleanup-2",
        profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        planned_windows=_windows(offset_minutes=6),
        semantic_inputs={**semantics, "implementation_revision_sha256": "d" * 64},
        started_at=NOW + timedelta(minutes=5),
    )
    baseline, audit = _baseline_audit(
        environment_id=second_start.environment_id,
        baseline_id="base-" + "4" * 24,
        window_offset_minutes=6,
    )
    job = _builder_job_record(
        job_id="job-" + "3" * 24,
        audit=audit,
        baseline=baseline,
        status=ProductJobStatusV1.SUCCEEDED,
    )
    second_completion = BaselineAttemptCompletionV023.build(
        attempt_ordinal=2,
        start_sha256=second_start.start_sha256,
        traffic_result=_traffic(
            passed=True,
            semantics={**semantics, "implementation_revision_sha256": "d" * 64},
            profile_sha256=second_start.profile_sha256,
        ),
        per_window_audit=audit,
        per_window_audit_sha256=audit.audit_sha256,
        builder_job_id=job.job_id,
        builder_job_record=job,
        builder_job_evidence_sha256=baseline_builder_job_evidence_sha256_v023(job),
        builder_job_disposition="SUCCEEDED",
        active_baseline_id="base-" + "4" * 24,
        active_baseline_sha256=baseline.baseline_sha256,
        cleanup="CLEAN",
        terminal=BASELINE_READINESS_PASS_V023,
        completed_at=NOW + timedelta(minutes=10),
    )

    with pytest.raises(ValidationError, match="eligible prior evidence"):
        BaselineAttemptLedgerV023.build(
            (
                BaselineAttemptV023(
                    start=first_start,
                    completion=first_completion,
                ),
                BaselineAttemptV023(
                    start=second_start,
                    completion=second_completion,
                ),
            )
        )


def test_baseline_traffic_pass_is_derived_from_measured_counts() -> None:
    semantics = _attempt_semantics()
    with pytest.raises(ValidationError, match="disposition differs"):
        BaselineTrafficResultV023.build(
            planned_request_count=180,
            completed_request_count=0,
            error_count=0,
            requests_per_second=0.5,
            maximum_error_fraction=0.01,
            queue_fault_flag=0,
            profile_sha256=ACTIVE_PROFILE_SHA256_V023,
            semantics_sha256=semantic_sha256_v22(semantics),
            passed=True,
        )


@pytest.mark.parametrize(
    "invalid_case",
    ("same_environment", "same_data_root", "stale_windows"),
)
def test_attempt_two_requires_fresh_namespace_and_frozen_profile_schedule(
    invalid_case: str,
) -> None:
    semantics = _attempt_semantics()
    profile_sha256 = ACTIVE_PROFILE_SHA256_V023
    first_start = BaselineAttemptStartV023.build(
        attempt_ordinal=1,
        changed_parameter=BaselineChangedParameterV023.INITIAL,
        prior_completion_sha256=None,
        environment_id="env-" + "1" * 24,
        product_data_root="/tmp/product-v023-attempt",
        profile_sha256=profile_sha256,
        planned_windows=_windows(offset_minutes=0),
        semantic_inputs=semantics,
        started_at=NOW,
    )
    failed_traffic = _traffic(
        passed=False,
        semantics=semantics,
        profile_sha256=profile_sha256,
    )
    first_completion = BaselineAttemptCompletionV023.build(
        attempt_ordinal=1,
        start_sha256=first_start.start_sha256,
        traffic_result=failed_traffic,
        per_window_audit=None,
        per_window_audit_sha256=None,
        builder_job_id=None,
        builder_job_record=None,
        builder_job_evidence_sha256=None,
        builder_job_disposition="NOT_SUBMITTED",
        active_baseline_id=None,
        active_baseline_sha256=None,
        cleanup="CLEAN",
        failure_kind=BaselineAttemptFailureKindV023.HEALTHY_TRAFFIC,
        failure_code="HEALTHY_TRAFFIC_INCOMPLETE",
        failure_evidence_sha256=failed_traffic.result_sha256,
        terminal=BASELINE_REPAIR_REQUIRED_V023,
        completed_at=NOW + timedelta(minutes=4),
    )
    environment_id = "env-" + "3" * 24
    product_data_root = "/tmp/product-v023-other"
    second_profile_sha256 = profile_sha256
    planned_windows = _windows(offset_minutes=6)
    second_started_at = NOW + timedelta(minutes=5)
    if invalid_case == "same_environment":
        environment_id = first_start.environment_id
    elif invalid_case == "same_data_root":
        product_data_root = first_start.product_data_root
    else:
        planned_windows = _windows(offset_minutes=0)
        second_started_at = NOW
    second_start = BaselineAttemptStartV023.build(
        attempt_ordinal=2,
        changed_parameter=BaselineChangedParameterV023.IMPLEMENTATION_REVISION_SHA256,
        prior_completion_sha256=first_completion.completion_sha256,
        environment_id=environment_id,
        product_data_root=product_data_root,
        profile_sha256=second_profile_sha256,
        planned_windows=planned_windows,
        semantic_inputs={**semantics, "implementation_revision_sha256": "d" * 64},
        started_at=second_started_at,
    )
    second_baseline, second_audit = _baseline_audit(
        environment_id=second_start.environment_id,
        baseline_id="base-" + "5" * 24,
        window_offset_minutes=(
            0 if planned_windows[0] == _windows(offset_minutes=0)[0] else 6
        ),
    )
    second_job = _builder_job_record(
        job_id="job-" + "4" * 24,
        audit=second_audit,
        baseline=second_baseline,
        status=ProductJobStatusV1.SUCCEEDED,
    )
    second_completion = BaselineAttemptCompletionV023.build(
        attempt_ordinal=2,
        start_sha256=second_start.start_sha256,
        traffic_result=_traffic(
            passed=True,
            semantics={**semantics, "implementation_revision_sha256": "d" * 64},
            profile_sha256=second_start.profile_sha256,
        ),
        per_window_audit=second_audit,
        per_window_audit_sha256=second_audit.audit_sha256,
        builder_job_id="job-" + "4" * 24,
        builder_job_record=second_job,
        builder_job_evidence_sha256=baseline_builder_job_evidence_sha256_v023(
            second_job
        ),
        builder_job_disposition="SUCCEEDED",
        active_baseline_id="base-" + "5" * 24,
        active_baseline_sha256=second_baseline.baseline_sha256,
        cleanup="CLEAN",
        terminal=BASELINE_READINESS_PASS_V023,
        completed_at=NOW + timedelta(minutes=10),
    )
    with pytest.raises(ValidationError):
        BaselineAttemptLedgerV023.build(
            (
                BaselineAttemptV023(
                    start=first_start,
                    completion=first_completion,
                ),
                BaselineAttemptV023(
                    start=second_start,
                    completion=second_completion,
                ),
            )
        )


def test_attempt_start_rejects_non_authoritative_profiles_and_alias_roots() -> None:
    with pytest.raises(ValidationError, match="frozen v0.2.3 profiles"):
        BaselineAttemptStartV023.build(
            attempt_ordinal=1,
            changed_parameter=BaselineChangedParameterV023.INITIAL,
            prior_completion_sha256=None,
            environment_id="env-" + "1" * 24,
            product_data_root="/tmp/product-v023-attempt",
            profile_sha256="f" * 64,
            planned_windows=_windows(offset_minutes=0),
            semantic_inputs=_attempt_semantics(),
            started_at=NOW,
        )
    with pytest.raises(ValidationError, match="data root"):
        BaselineAttemptStartV023.build(
            attempt_ordinal=1,
            changed_parameter=BaselineChangedParameterV023.INITIAL,
            prior_completion_sha256=None,
            environment_id="env-" + "1" * 24,
            product_data_root="/tmp/../tmp/product-v023-attempt",
            profile_sha256=ACTIVE_PROFILE_SHA256_V023,
            planned_windows=_windows(offset_minutes=0),
            semantic_inputs=_attempt_semantics(),
            started_at=NOW,
        )
    with pytest.raises(ValidationError, match="frozen v0.2.3 profiles"):
        BaselineAttemptStartV023.build(
            attempt_ordinal=1,
            changed_parameter=BaselineChangedParameterV023.INITIAL,
            prior_completion_sha256=None,
            environment_id="env-" + "1" * 24,
            product_data_root="/tmp/product-v023-attempt",
            profile_sha256=ACTIVE_PROFILE_SHA256_V023,
            planned_windows=_windows(offset_minutes=0),
            semantic_inputs={
                **_attempt_semantics(),
                "maximum_error_fraction": 0.02,
            },
            started_at=NOW,
        )


def _baseline_audit(
    *,
    environment_id: str = "env-" + "1" * 24,
    baseline_id: str = "base-" + "2" * 24,
    window_offset_minutes: int = 0,
) -> tuple[EnvironmentBaselineV1, ProductBaselineReadinessAuditV023]:
    evaluation = _evaluation()
    identity = ServiceIdentityMapV1.build(
        environment_id=environment_id,
        services=(
            ServiceIdentityV1(
                service_id="svc-" + "4" * 24,
                logical_service="checkout",
            ),
        ),
    )
    policy = BaselineBuildPolicyV1(
        mode=BaselineBuildModeV1.DEMO_ONLY,
        lookback_seconds=180,
        window_count=5,
        minimum_successful_windows=4,
        warmup_seconds=180,
    )
    baseline = build_environment_baseline(
        environment_id=environment_id,
        identity_map=identity,
        source_capability_sha256="6" * 64,
        build_policy=policy,
        window_results=tuple(_window_evidence(index)[0] for index in range(5)),
        built_at=NOW + timedelta(minutes=10),
        baseline_id=baseline_id,
        evaluation_v023=evaluation,
    ).model_copy(update={"active": True})
    audit = ProductBaselineReadinessAuditV023.build(
        environment_id=environment_id,
        baseline_id=baseline_id,
        baseline_sha256=baseline.baseline_sha256,
        service_ids=("checkout",),
        baseline_entity_service_ids=("svc-" + "4" * 24,),
        build_policy=policy.model_dump(mode="json"),
        service_identity_sha256=identity.identity_sha256,
        capability_sha256="6" * 64,
        evaluation=evaluation,
    )
    if window_offset_minutes:
        payload = audit.model_dump(mode="json")
        evaluation_payload = payload["evaluation"]
        for item in evaluation_payload["windows"]:
            window = item["window"]
            window["started_at"] = (
                datetime.fromisoformat(window["started_at"])
                + timedelta(minutes=window_offset_minutes)
            ).isoformat()
            window["ended_at"] = (
                datetime.fromisoformat(window["ended_at"])
                + timedelta(minutes=window_offset_minutes)
            ).isoformat()
            draft_window = BaselineWindowAuditV023.model_construct(
                schema_version=item["schema_version"],
                window_ordinal=item["window_ordinal"],
                window=ConnectorWindowV1.model_validate(item["window"]),
                result_sha256s=tuple(item["result_sha256s"]),
                prometheus_diagnostics_sha256=item["prometheus_diagnostics_sha256"],
                opensearch_diagnostics_sha256=item["opensearch_diagnostics_sha256"],
                opensearch_rejection_codes=tuple(
                    item.get("opensearch_rejection_codes", ())
                ),
                accepted=item["accepted"],
                rejection_reason_codes=tuple(
                    BaselineRejectionReasonCodeV023(value)
                    for value in item["rejection_reason_codes"]
                ),
                window_sha256="0" * 64,
            )
            normalized_window = draft_window.model_dump(
                mode="json", exclude={"window_sha256"}
            )
            item["window_sha256"] = semantic_sha256_v22(normalized_window)
        typed_windows = tuple(
            BaselineWindowAuditV023.model_validate(item)
            for item in evaluation_payload["windows"]
        )
        draft_evaluation = BaselineWindowEvaluationV023.model_construct(
            schema_version=evaluation_payload["schema_version"],
            terminal=evaluation_payload["terminal"],
            profile_sha256=evaluation_payload["profile_sha256"],
            active_opensearch_profile_sha256=evaluation_payload[
                "active_opensearch_profile_sha256"
            ],
            windows=typed_windows,
            accepted_ordinals=tuple(evaluation_payload["accepted_ordinals"]),
            logs_nonempty_window_count=evaluation_payload["logs_nonempty_window_count"],
            accepted_checkout_log_record_count=evaluation_payload[
                "accepted_checkout_log_record_count"
            ],
            has_normal_checkout_log_template=evaluation_payload[
                "has_normal_checkout_log_template"
            ],
            aggregate_rejection_reason_codes=tuple(
                evaluation_payload["aggregate_rejection_reason_codes"]
            ),
            final_builder_would_pass=evaluation_payload["final_builder_would_pass"],
            parity_sha256="0" * 64,
        )
        normalized_evaluation = draft_evaluation.model_dump(
            mode="json", exclude={"parity_sha256"}
        )
        evaluation_payload["parity_sha256"] = semantic_sha256_v22(normalized_evaluation)
        typed_evaluation = BaselineWindowEvaluationV023.model_validate(
            evaluation_payload
        )
        payload["parity_sha256"] = evaluation_payload["parity_sha256"]
        draft_audit = ProductBaselineReadinessAuditV023.model_construct(
            schema_version=payload["schema_version"],
            environment_id=payload["environment_id"],
            baseline_id=payload["baseline_id"],
            baseline_sha256=payload["baseline_sha256"],
            service_ids=tuple(payload["service_ids"]),
            baseline_entity_service_ids=tuple(payload["baseline_entity_service_ids"]),
            build_policy=payload["build_policy"],
            profile_sha256=payload["profile_sha256"],
            active_opensearch_profile_sha256=payload[
                "active_opensearch_profile_sha256"
            ],
            service_identity_sha256=payload["service_identity_sha256"],
            capability_sha256=payload["capability_sha256"],
            evaluation=typed_evaluation,
            final_builder_would_pass=payload["final_builder_would_pass"],
            parity_sha256=payload["parity_sha256"],
            audit_sha256="0" * 64,
        )
        normalized_audit = draft_audit.model_dump(mode="json", exclude={"audit_sha256"})
        payload["audit_sha256"] = semantic_sha256_v22(normalized_audit)
        audit = ProductBaselineReadinessAuditV023.model_validate(payload)
    return baseline, audit


def _audit() -> ProductBaselineReadinessAuditV023:
    return _baseline_audit()[1]


def test_late_failed_job_recovers_its_persisted_readiness_audit(
    tmp_path: Path,
) -> None:
    audit = _audit()
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    with store.connect() as connection:
        connection.execute(
            "INSERT INTO environments(environment_id, name, description, timezone, "
            "service_identity_policy_json, explicit_service_catalog_json, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                audit.environment_id,
                "late-audit",
                "late-audit",
                "UTC",
                "{}",
                "[]",
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
    ProductBaselineReadinessAuditRepositoryV023(store).put(
        audit,
        created_at=NOW,
    )

    recovered = _read_latest_baseline_audit_from_store_v023(
        tmp_path,
        audit.environment_id,
    )

    assert recovered == audit


def _failed_audit(
    evaluation: BaselineWindowEvaluationV023,
) -> ProductBaselineReadinessAuditV023:
    identity = ServiceIdentityMapV1.build(
        environment_id="env-" + "1" * 24,
        services=(
            ServiceIdentityV1(
                service_id="svc-" + "4" * 24,
                logical_service="checkout",
            ),
        ),
    )
    return ProductBaselineReadinessAuditV023.build(
        environment_id=identity.environment_id,
        baseline_id="base-" + "2" * 24,
        baseline_sha256=None,
        service_ids=("checkout",),
        baseline_entity_service_ids=("svc-" + "4" * 24,),
        build_policy=BaselineBuildPolicyV1(
            mode=BaselineBuildModeV1.DEMO_ONLY,
            lookback_seconds=180,
            window_count=5,
            minimum_successful_windows=4,
            warmup_seconds=180,
        ).model_dump(mode="json"),
        service_identity_sha256=identity.identity_sha256,
        capability_sha256="6" * 64,
        evaluation=evaluation,
    )


def test_failed_builder_classification_uses_exact_window_evidence() -> None:
    metrics_audit = _failed_audit(_evaluation(missing_request_support_ordinals=(1, 2)))
    metrics_job = _builder_job_record(
        job_id="job-" + "7" * 24,
        audit=metrics_audit,
        baseline=None,
        status=ProductJobStatusV1.FAILED,
        safe_error_code="BASELINE_V023_PREFLIGHT_BLOCKED",
    )
    alias_audit = _failed_audit(_evaluation(opensearch_service_failure_ordinals=(1, 2)))
    alias_job = _builder_job_record(
        job_id="job-" + "8" * 24,
        audit=alias_audit,
        baseline=None,
        status=ProductJobStatusV1.FAILED,
        safe_error_code="BASELINE_V023_PREFLIGHT_BLOCKED",
    )

    assert (
        _failed_builder_kind_v023(metrics_job, metrics_audit)
        is BaselineAttemptFailureKindV023.CONNECTOR_QUERY_BINDING
    )
    assert (
        _failed_builder_kind_v023(alias_job, alias_audit)
        is BaselineAttemptFailureKindV023.SERVICE_ALIAS_BINDING
    )

    field_missing_audit = cast(
        ProductBaselineReadinessAuditV023,
        SimpleNamespace(
            evaluation=SimpleNamespace(
                aggregate_rejection_reason_codes=(),
                windows=(
                    SimpleNamespace(
                        rejection_reason_codes=(
                            BaselineRejectionReasonCodeV023.OPENSEARCH_QUERY_FAILED,
                        ),
                        opensearch_rejection_codes=(
                            "OPENSEARCH_SERVICE_FIELD_MISSING",
                        ),
                    ),
                ),
            )
        ),
    )
    assert (
        _failed_builder_kind_v023(alias_job, field_missing_audit)
        is BaselineAttemptFailureKindV023.CONNECTOR_QUERY_BINDING
    )

    mixed_audit = cast(
        ProductBaselineReadinessAuditV023,
        SimpleNamespace(
            evaluation=SimpleNamespace(
                aggregate_rejection_reason_codes=("MINIMUM_ACCEPTED_WINDOWS_NOT_MET",),
                windows=(
                    SimpleNamespace(
                        rejection_reason_codes=(
                            BaselineRejectionReasonCodeV023.OPENSEARCH_REQUIRED_EXTRACTION_FAILED,
                            BaselineRejectionReasonCodeV023.METRICS_REQUEST_SUPPORT_EMPTY,
                        ),
                        opensearch_rejection_codes=(
                            "OPENSEARCH_SERVICE_ALIAS_UNMAPPED",
                            "OPENSEARCH_SERVICE_FIELD_MISSING",
                        ),
                    ),
                ),
            )
        ),
    )
    assert (
        _failed_builder_kind_v023(alias_job, mixed_audit)
        is BaselineAttemptFailureKindV023.IMPLEMENTATION
    )


@pytest.mark.parametrize(
    ("disposition", "status", "safe_error_code", "failure_code"),
    (
        ("FAILED", ProductJobStatusV1.FAILED, "BUILDER_FAILED", "BUILDER_FAILED"),
        (
            "CANCELLED",
            ProductJobStatusV1.CANCELLED,
            None,
            "BASELINE_BUILDER_CANCELLED",
        ),
        (
            "TIMED_OUT",
            ProductJobStatusV1.RUNNING,
            None,
            "BASELINE_BUILDER_TIMEOUT",
        ),
    ),
)
def test_builder_terminal_without_audit_can_close_the_attempt_ledger(
    disposition: str,
    status: ProductJobStatusV1,
    safe_error_code: str | None,
    failure_code: str,
) -> None:
    semantics = _attempt_semantics()
    start = BaselineAttemptStartV023.build(
        attempt_ordinal=1,
        changed_parameter=BaselineChangedParameterV023.INITIAL,
        prior_completion_sha256=None,
        environment_id="env-" + "1" * 24,
        product_data_root="/tmp/product-v023-builder-terminal",
        profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        planned_windows=_windows(offset_minutes=0),
        semantic_inputs=semantics,
        started_at=NOW,
    )
    job = ProductJobRecordV1(
        job_id="job-" + "9" * 24,
        job_type=ProductJobTypeV1.BASELINE_BUILD,
        status=status,
        payload={
            "environment_id": start.environment_id,
            "request": {
                "build_policy": {
                    "mode": "DEMO_ONLY",
                    "lookback_seconds": 180,
                    "window_count": 5,
                    "minimum_successful_windows": 4,
                    "warmup_seconds": 180,
                },
                "candidate_services": ["checkout"],
                "planned_windows": [
                    item.model_dump(mode="json") for item in start.planned_windows
                ],
                "activate": True,
            },
        },
        result=None,
        safe_error_code=safe_error_code,
        idempotency_key="product-v023-builder-terminal",
        claimed_by="worker-v023",
        lease_expires_at=None,
        attempt_count=1,
        created_at=1.0,
        updated_at=2.0,
    )
    completion = BaselineAttemptCompletionV023.build(
        attempt_ordinal=1,
        start_sha256=start.start_sha256,
        traffic_result=_traffic(
            passed=True,
            semantics=semantics,
            profile_sha256=start.profile_sha256,
        ),
        per_window_audit=None,
        per_window_audit_sha256=None,
        builder_job_id=job.job_id,
        builder_job_record=job,
        builder_job_evidence_sha256=baseline_builder_job_evidence_sha256_v023(job),
        builder_job_disposition=disposition,
        active_baseline_id=None,
        active_baseline_sha256=None,
        cleanup="CLEAN",
        failure_kind=BaselineAttemptFailureKindV023.BUILDER,
        failure_code=failure_code,
        failure_evidence_sha256=baseline_builder_job_evidence_sha256_v023(job),
        terminal=BASELINE_REPAIR_REQUIRED_V023,
        completed_at=NOW + timedelta(minutes=4),
    )

    ledger = BaselineAttemptLedgerV023.build(
        (BaselineAttemptV023(start=start, completion=completion),)
    )

    assert ledger.attempts[0].completion.builder_job_disposition == disposition


def test_builder_submission_failure_can_close_the_attempt_ledger() -> None:
    semantics = _attempt_semantics()
    start = BaselineAttemptStartV023.build(
        attempt_ordinal=1,
        changed_parameter=BaselineChangedParameterV023.INITIAL,
        prior_completion_sha256=None,
        environment_id="env-" + "1" * 24,
        product_data_root="/tmp/product-v023-builder-submission",
        profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        planned_windows=_windows(offset_minutes=0),
        semantic_inputs=semantics,
        started_at=NOW,
    )
    traffic = _traffic(
        passed=True,
        semantics=semantics,
        profile_sha256=start.profile_sha256,
    )
    failure_evidence = baseline_builder_submission_failure_evidence_sha256_v023(
        start_sha256=start.start_sha256,
        traffic_result_sha256=traffic.result_sha256,
    )
    completion = BaselineAttemptCompletionV023.build(
        attempt_ordinal=1,
        start_sha256=start.start_sha256,
        traffic_result=traffic,
        per_window_audit=None,
        per_window_audit_sha256=None,
        builder_job_id=None,
        builder_job_record=None,
        builder_job_evidence_sha256=None,
        builder_job_disposition="NOT_SUBMITTED",
        active_baseline_id=None,
        active_baseline_sha256=None,
        cleanup="CLEAN",
        failure_kind=BaselineAttemptFailureKindV023.IMPLEMENTATION,
        failure_code="BASELINE_BUILDER_SUBMISSION_FAILED",
        failure_evidence_sha256=failure_evidence,
        terminal=BASELINE_REPAIR_REQUIRED_V023,
        completed_at=NOW + timedelta(minutes=4),
    )

    ledger = BaselineAttemptLedgerV023.build(
        (BaselineAttemptV023(start=start, completion=completion),)
    )

    assert ledger.attempts[0].completion.failure_evidence_sha256 == failure_evidence


def test_exhausted_transport_retries_close_blocked_without_repair_authority() -> None:
    semantics = _attempt_semantics()
    start = BaselineAttemptStartV023.build(
        attempt_ordinal=1,
        changed_parameter=BaselineChangedParameterV023.INITIAL,
        prior_completion_sha256=None,
        environment_id="env-" + "1" * 24,
        product_data_root="/tmp/product-v023-builder-transport",
        profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        planned_windows=_windows(offset_minutes=0),
        semantic_inputs=semantics,
        started_at=NOW,
    )
    traffic = _traffic(
        passed=True,
        semantics=semantics,
        profile_sha256=start.profile_sha256,
    )
    failure_codes = ("TIMEOUT", "HTTP_5XX", "HTTP_429", "CONNECTION_RESET")
    idempotency_sha256 = "d" * 64
    failure_evidence = baseline_builder_transport_failure_evidence_sha256_v023(
        start_sha256=start.start_sha256,
        traffic_result_sha256=traffic.result_sha256,
        idempotency_key_sha256=idempotency_sha256,
        failure_codes=failure_codes,
        retry_count=3,
    )
    completion = BaselineAttemptCompletionV023.build(
        attempt_ordinal=1,
        start_sha256=start.start_sha256,
        traffic_result=traffic,
        per_window_audit=None,
        per_window_audit_sha256=None,
        builder_job_id=None,
        builder_job_record=None,
        builder_job_evidence_sha256=None,
        builder_job_disposition="NOT_SUBMITTED",
        builder_transport_failure_codes=failure_codes,
        builder_transport_retry_count=3,
        builder_idempotency_key_sha256=idempotency_sha256,
        active_baseline_id=None,
        active_baseline_sha256=None,
        cleanup="CLEAN",
        failure_kind=BaselineAttemptFailureKindV023.TRANSPORT,
        failure_code="BASELINE_BUILDER_TRANSPORT_RETRIES_EXHAUSTED",
        failure_evidence_sha256=failure_evidence,
        terminal=BASELINE_READINESS_BLOCKED_V023,
        completed_at=NOW + timedelta(minutes=4),
    )

    ledger = BaselineAttemptLedgerV023.build(
        (BaselineAttemptV023(start=start, completion=completion),)
    )

    assert ledger.attempts[0].completion.terminal == BASELINE_READINESS_BLOCKED_V023
    with pytest.raises(ValueError, match="does not match prior failure"):
        validate_changed_attempt_parameter_v023(
            prior_completion=completion,
            changed_parameter=BaselineChangedParameterV023.IMPLEMENTATION_REVISION_SHA256,
        )


def test_late_acknowledgement_preserves_transport_evidence_without_timeout() -> None:
    semantics = _attempt_semantics()
    start = BaselineAttemptStartV023.build(
        attempt_ordinal=1,
        changed_parameter=BaselineChangedParameterV023.INITIAL,
        prior_completion_sha256=None,
        environment_id="env-" + "1" * 24,
        product_data_root="/tmp/product-v023-late-ack",
        profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        planned_windows=_windows(offset_minutes=0),
        semantic_inputs=semantics,
        started_at=NOW,
    )
    traffic = _traffic(
        passed=True,
        semantics=semantics,
        profile_sha256=start.profile_sha256,
    )
    job = ProductJobRecordV1(
        job_id="job-" + "b" * 24,
        job_type=ProductJobTypeV1.BASELINE_BUILD,
        status=ProductJobStatusV1.PENDING,
        payload={
            "environment_id": start.environment_id,
            "request": {
                "build_policy": {
                    "mode": "DEMO_ONLY",
                    "lookback_seconds": 180,
                    "window_count": 5,
                    "minimum_successful_windows": 4,
                    "warmup_seconds": 180,
                },
                "candidate_services": ["checkout"],
                "planned_windows": [
                    item.model_dump(mode="json") for item in start.planned_windows
                ],
                "activate": True,
            },
        },
        result=None,
        safe_error_code=None,
        idempotency_key="product-v023-late-ack",
        claimed_by=None,
        lease_expires_at=None,
        attempt_count=0,
        created_at=1.0,
        updated_at=1.0,
    )
    job_evidence = baseline_builder_job_evidence_sha256_v023(job)
    failure_codes = ("TIMEOUT",) * 4
    idempotency_sha256 = "e" * 64
    failure_evidence = baseline_builder_transport_failure_evidence_sha256_v023(
        start_sha256=start.start_sha256,
        traffic_result_sha256=traffic.result_sha256,
        idempotency_key_sha256=idempotency_sha256,
        failure_codes=failure_codes,
        retry_count=3,
        builder_job_disposition="LATE_ACKNOWLEDGED",
        builder_job_evidence_sha256=job_evidence,
    )
    completion = BaselineAttemptCompletionV023.build(
        attempt_ordinal=1,
        start_sha256=start.start_sha256,
        traffic_result=traffic,
        per_window_audit=None,
        per_window_audit_sha256=None,
        builder_job_id=job.job_id,
        builder_job_record=job,
        builder_job_evidence_sha256=job_evidence,
        builder_job_disposition="LATE_ACKNOWLEDGED",
        builder_transport_failure_codes=failure_codes,
        builder_transport_retry_count=3,
        builder_idempotency_key_sha256=idempotency_sha256,
        active_baseline_id=None,
        active_baseline_sha256=None,
        cleanup="CLEAN",
        failure_kind=BaselineAttemptFailureKindV023.TRANSPORT,
        failure_code="BASELINE_BUILDER_TRANSPORT_RETRIES_EXHAUSTED",
        failure_evidence_sha256=failure_evidence,
        terminal=BASELINE_READINESS_BLOCKED_V023,
        completed_at=NOW + timedelta(minutes=4),
    )

    attempt = BaselineAttemptV023(start=start, completion=completion)

    assert attempt.completion.builder_job_disposition == "LATE_ACKNOWLEDGED"
    assert attempt.completion.failure_kind is BaselineAttemptFailureKindV023.TRANSPORT

    contradictory_job = job.model_copy(
        update={
            "result": {"forged": True},
            "safe_error_code": "FORGED_ERROR_ON_PENDING",
        }
    )
    contradictory_evidence = baseline_builder_job_evidence_sha256_v023(
        contradictory_job
    )
    with pytest.raises(
        ValueError,
        match="interrupted baseline JobRecord status matrix differs",
    ):
        BaselineAttemptCompletionV023.build(
            attempt_ordinal=1,
            start_sha256=start.start_sha256,
            traffic_result=traffic,
            per_window_audit=None,
            per_window_audit_sha256=None,
            builder_job_id=contradictory_job.job_id,
            builder_job_record=contradictory_job,
            builder_job_evidence_sha256=contradictory_evidence,
            builder_job_disposition="INTERRUPTED",
            active_baseline_id=None,
            active_baseline_sha256=None,
            cleanup="CLEAN",
            failure_kind=BaselineAttemptFailureKindV023.INTERRUPTED,
            failure_code="BASELINE_ATTEMPT_INTERRUPTED",
            failure_evidence_sha256=(
                baseline_builder_interruption_evidence_sha256_v023(
                    start_sha256=start.start_sha256,
                    traffic_result_sha256=traffic.result_sha256,
                    builder_job_evidence_sha256=contradictory_evidence,
                )
            ),
            terminal=BASELINE_READINESS_BLOCKED_V023,
            completed_at=NOW + timedelta(minutes=4),
        )


def test_failed_job_without_audit_must_match_the_frozen_build_policy() -> None:
    semantics = _attempt_semantics()
    start = BaselineAttemptStartV023.build(
        attempt_ordinal=1,
        changed_parameter=BaselineChangedParameterV023.INITIAL,
        prior_completion_sha256=None,
        environment_id="env-" + "1" * 24,
        product_data_root="/tmp/product-v023-policy-mismatch",
        profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        planned_windows=_windows(offset_minutes=0),
        semantic_inputs=semantics,
        started_at=NOW,
    )
    job = ProductJobRecordV1(
        job_id="job-" + "a" * 24,
        job_type=ProductJobTypeV1.BASELINE_BUILD,
        status=ProductJobStatusV1.FAILED,
        payload={
            "environment_id": start.environment_id,
            "request": {
                "build_policy": {
                    "mode": "DEMO_ONLY",
                    "lookback_seconds": 180,
                    "window_count": 5,
                    "minimum_successful_windows": 3,
                    "warmup_seconds": 180,
                },
                "candidate_services": ["checkout"],
                "planned_windows": [
                    item.model_dump(mode="json") for item in start.planned_windows
                ],
                "activate": True,
            },
        },
        result=None,
        safe_error_code="BUILDER_FAILED",
        idempotency_key="product-v023-policy-mismatch",
        claimed_by="worker-v023",
        lease_expires_at=None,
        attempt_count=1,
        created_at=1.0,
        updated_at=2.0,
    )
    job_evidence = baseline_builder_job_evidence_sha256_v023(job)
    completion = BaselineAttemptCompletionV023.build(
        attempt_ordinal=1,
        start_sha256=start.start_sha256,
        traffic_result=_traffic(
            passed=True,
            semantics=semantics,
            profile_sha256=start.profile_sha256,
        ),
        per_window_audit=None,
        per_window_audit_sha256=None,
        builder_job_id=job.job_id,
        builder_job_record=job,
        builder_job_evidence_sha256=job_evidence,
        builder_job_disposition="FAILED",
        active_baseline_id=None,
        active_baseline_sha256=None,
        cleanup="CLEAN",
        failure_kind=BaselineAttemptFailureKindV023.BUILDER,
        failure_code="BUILDER_FAILED",
        failure_evidence_sha256=job_evidence,
        terminal=BASELINE_REPAIR_REQUIRED_V023,
        completed_at=NOW + timedelta(minutes=4),
    )

    with pytest.raises(ValueError, match="Builder request differs from start"):
        BaselineAttemptV023(start=start, completion=completion)


def _snapshot(
    audit: ProductBaselineReadinessAuditV023,
    *,
    instance_marker: str,
    observed_at: datetime,
    **updates,
):
    payload = {
        "environment_id": audit.environment_id,
        "environment_payload_sha256": "7" * 64,
        "profile_binding_sha256": ACTIVE_PROFILE_BINDING_SHA256_V023,
        "profile_sha256": audit.active_opensearch_profile_sha256,
        "active_baseline_id": audit.baseline_id,
        "active_baseline_sha256": audit.baseline_sha256,
        "baseline_count": 1,
        "service_identity_sha256": audit.service_identity_sha256,
        "capability_sha256": audit.capability_sha256,
        "api_instance_id": "api-" + instance_marker * 24,
        "worker_instance_id": "worker-" + instance_marker * 24,
        "observed_at": observed_at,
        "pending_jobs": 0,
        "running_jobs": 0,
        "failed_jobs": 0,
        **updates,
    }
    return BaselineRestartSnapshotV023.build(**payload)


def test_restart_proof_preserves_every_binding_and_does_not_build_again() -> None:
    audit = _audit()
    before = _snapshot(audit, instance_marker="1", observed_at=NOW)
    after = _snapshot(
        audit,
        instance_marker="2",
        observed_at=NOW + timedelta(seconds=1),
    )

    proof = BaselineRestartProofV023.build(before=before, after=after)

    assert proof.terminal == BASELINE_RESTART_PASS_V023
    assert proof.new_baseline_count == 0
    assert proof.queue_healthy is True

    with pytest.raises(ValidationError):
        BaselineRestartProofV023.build(
            before=before,
            after=_snapshot(
                audit,
                instance_marker="2",
                observed_at=NOW + timedelta(seconds=1),
                baseline_count=2,
            ),
        )

    with pytest.raises(ValidationError, match="restart profile binding differs"):
        _snapshot(
            audit,
            instance_marker="2",
            observed_at=NOW + timedelta(seconds=1),
            profile_binding_sha256="8" * 64,
        )

    with pytest.raises(ValidationError, match="restart is not proven"):
        BaselineRestartProofV023.build(before=before, after=before)


def _seal(model_type, payload: dict, digest_field: str):
    draft = model_type.model_construct(**payload, **{digest_field: "0" * 64})
    normalized = draft.model_dump(mode="json", exclude={digest_field})
    return model_type.model_validate(
        {**normalized, digest_field: semantic_sha256_v22(normalized)}
    )


def _object(ref: str, source: EvidenceSourceV22, payload: dict) -> EvidenceObjectV1:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return EvidenceObjectV1(
        evidence_ref=ref,
        source=source,
        action_id=f"action-{source.value.lower()}",
        object_sha256=hashlib.sha256(encoded).hexdigest(),
        payload=payload,
    )


def _profile_diagnostics(
    *,
    accepted_record_count: int,
    status: str = "SUCCESS_NONEMPTY",
) -> dict[str, Any]:
    succeeded = status == "SUCCESS_NONEMPTY"
    body = {
        "schema_version": "ecomsre.product.opensearch-connector-diagnostics.v023",
        "terminal": "ECOMSRE_PRODUCT_V023_PROFILE_BINDING_PASS",
        "settings_mode": "PROFILE_BOUND",
        "profile_binding_sha256": ACTIVE_PROFILE_BINDING_SHA256_V023,
        "profile_sha256": ACTIVE_PROFILE_SHA256_V023,
        "index_pattern": "otel-logs-*",
        "timestamp_query_field": "@timestamp",
        "service_source_field": "resource.service.name",
        "service_query_field": "resource.service.name.keyword",
        "severity_field": "severity.text",
        "message_field": "body",
        "trace_id_field": "traceId",
        "maximum_record_rejection_fraction": 0.2,
        "last_query_status": status,
        "last_normalization_status": "SUCCESS_NONEMPTY" if succeeded else None,
        "last_query_batch_sha256": "d" * 64 if succeeded else None,
        "last_safe_error_code": None if succeeded else "CONNECTOR_SCHEMA_INVALID",
        "last_sampled_record_count": accepted_record_count if succeeded else 0,
        "last_accepted_record_count": accepted_record_count if succeeded else 0,
        "last_rejected_record_count": 0,
        "last_rejection_fraction": 0.0,
        "last_rejection_codes_by_count": {},
    }
    return {**body, "diagnostics_sha256": semantic_sha256_v22(body)}


def _nofault_inputs(
    *,
    terminal: str = "NO_INCIDENT",
    hidden_failure: bool = False,
    runtime_healthy: bool = True,
):
    audit = _audit()
    restart = BaselineRestartProofV023.build(
        before=_snapshot(
            audit,
            instance_marker="1",
            observed_at=NOW + timedelta(minutes=4),
        ),
        after=_snapshot(
            audit,
            instance_marker="2",
            observed_at=NOW + timedelta(minutes=4, seconds=30),
        ),
    )
    incident_payload = {
        "schema_version": "ecomsre.product.incident.v1",
        "environment_id": audit.environment_id,
        "external_incident_key": "v023-nofault-1",
        "alert_name": "No-Fault acceptance",
        "summary": "Healthy checkout observation",
        "started_at": NOW + timedelta(minutes=5),
        "ended_at": NOW + timedelta(minutes=6),
        "candidate_service_ids": ("svc-" + "4" * 24,),
        "labels": {"fault": "none"},
        "incident_id": "inc-" + "9" * 24,
        "baseline_id": audit.baseline_id,
        "baseline_sha256": audit.baseline_sha256,
        "service_identity_sha256": audit.service_identity_sha256,
        "source_capability_sha256": audit.capability_sha256,
        "candidate_logical_services": ("checkout",),
        "diagnosis_observed_at": NOW + timedelta(minutes=6),
        "created_at": NOW + timedelta(minutes=6),
    }
    incident = _seal(IncidentRecordV1, incident_payload, "incident_sha256")
    limitations = (
        ("TRACES_DIAGNOSIS_UNAVAILABLE",) if terminal == "INSUFFICIENT_EVIDENCE" else ()
    )
    lane_by_terminal = {
        "CORE_KNOWN": DiagnosisLaneV1.CORE,
        "EXTENSION_KNOWN": DiagnosisLaneV1.EXTENSION,
        "NO_INCIDENT": DiagnosisLaneV1.NO_INCIDENT,
        "OPEN_WORLD": DiagnosisLaneV1.OPEN_WORLD,
        "INSUFFICIENT_EVIDENCE": DiagnosisLaneV1.ABSTAIN,
        "CONFLICTING_EVIDENCE": DiagnosisLaneV1.ABSTAIN,
    }
    classified = terminal in {"CORE_KNOWN", "EXTENSION_KNOWN", "OPEN_WORLD"}
    supporting_refs = (
        ("logs:1", "metrics:1", "runtime:1", "traces:1")
        if limitations
        else ("logs:1", "metrics:1", "runtime:1")
    )
    diagnosis_payload = {
        "schema_version": "ecomsre.product.diagnosis-result.v1",
        "diagnosis_id": "diag-" + "a" * 24,
        "incident_id": incident.incident_id,
        "terminal": DiagnosisTerminalV1(terminal),
        "core_or_extension_or_open_world": lane_by_terminal[terminal],
        "root_service_ids": (("svc-" + "4" * 24,) if classified else ()),
        "mechanism": "observed-anomaly" if classified else None,
        "broad_domain": "runtime" if classified else None,
        "supporting_evidence_refs": supporting_refs,
        "contradicting_evidence_refs": (),
        "capability_limitations": limitations,
        "provisional_report": (
            {"summary": "unexpected anomaly"} if terminal == "OPEN_WORLD" else None
        ),
        "action_authority": ActionAuthorityV1.NONE,
        "agent_writes": 0,
        "runbook_executions": 0,
        "provider_calls": 0,
        "memory_sha256": None,
        "created_at": NOW + timedelta(minutes=7),
    }
    diagnosis = _seal(DiagnosisResultV1, diagnosis_payload, "result_sha256")
    evidence_window = ConnectorWindowV1(
        started_at=incident.started_at,
        ended_at=incident.diagnosis_observed_at,
    )
    if hidden_failure:
        logs_result = ConnectorQueryResultV1.build(
            source=EvidenceSourceV22.LOGS,
            status=ReadSourceStatusV22.FAILURE_SCHEMA,
            requested_services=("checkout",),
            covered_services=(),
            window=evidence_window,
            records=(),
            truncated=False,
            safe_error_code="CONNECTOR_SCHEMA_INVALID",
            latency_ms=1.0,
        )
    else:
        logs_result = _result(
            EvidenceSourceV22.LOGS,
            evidence_window,
            _logs(evidence_window, 0),
        )
    metrics_result = _result(
        EvidenceSourceV22.METRICS,
        evidence_window,
        (_metric(evidence_window, MetricKindV22.REQUEST_SUPPORT, 30.0),),
    )
    runtime_result = ConnectorQueryResultV1.build(
        source=EvidenceSourceV22.RUNTIME,
        status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
        requested_services=("checkout",),
        covered_services=("checkout",),
        window=evidence_window,
        records=(
            RuntimeRecordV22(
                schema_version="dta-v22.runtime-record.v1",
                service="checkout",
                state=RuntimeStateV22.RUNNING,
                healthy=runtime_healthy,
                restart_count=0,
            ),
        ),
        truncated=False,
        safe_error_code=None,
        latency_ms=1.0,
    )
    logs_payload = {
        "connector_diagnostics": [
            _profile_diagnostics(
                accepted_record_count=len(logs_result.records),
                status=logs_result.status.value,
            )
        ],
        "connector_result": logs_result.model_dump(mode="json"),
        "read_outcome": {
            "status": "SUCCESS_NONEMPTY",
        },
    }
    runtime_payload = {"connector_result": runtime_result.model_dump(mode="json")}
    metrics_payload = {"connector_result": metrics_result.model_dump(mode="json")}
    evidence_objects = [
        _object("logs:1", EvidenceSourceV22.LOGS, logs_payload),
        _object("metrics:1", EvidenceSourceV22.METRICS, metrics_payload),
        _object("runtime:1", EvidenceSourceV22.RUNTIME, runtime_payload),
    ]
    if limitations:
        traces_result = ConnectorQueryResultV1.build(
            source=EvidenceSourceV22.TRACES,
            status=ReadSourceStatusV22.FAILURE_UNAVAILABLE,
            requested_services=("checkout",),
            covered_services=(),
            window=evidence_window,
            records=(),
            truncated=False,
            safe_error_code="TRACES_UNAVAILABLE",
            latency_ms=1.0,
        )
        evidence_objects.append(
            _object(
                "traces:1",
                EvidenceSourceV22.TRACES,
                {"connector_result": traces_result.model_dump(mode="json")},
            )
        )
    bundle = EvidenceBundleV1(
        incident_id=incident.incident_id,
        diagnosis_id=diagnosis.diagnosis_id,
        objects=tuple(evidence_objects),
        supporting_evidence_refs=diagnosis.supporting_evidence_refs,
        contradicting_evidence_refs=(),
    )
    execution_profile = NoFaultExecutionProfileV023.default()
    traffic_result = NoFaultTrafficResultV023.build(
        environment_id=incident.environment_id,
        incident_id=incident.incident_id,
        window=evidence_window,
        profile_sha256=execution_profile.profile_sha256,
        planned_request_count=execution_profile.request_count,
        completed_request_count=execution_profile.request_count,
        error_count=0,
        requests_per_second=execution_profile.requests_per_second,
        maximum_error_fraction=execution_profile.maximum_error_fraction,
        queue_fault_flag=execution_profile.queue_fault_flag,
        passed=True,
    )
    assessment = NoFaultCapabilityAssessmentV023.build(
        runtime_healthy=True,
        runtime_evidence_ref="runtime:1",
        successful_sources=(
            EvidenceSourceV22.LOGS,
            EvidenceSourceV22.METRICS,
            EvidenceSourceV22.RUNTIME,
        ),
        healthy_traffic_passed=True,
        healthy_traffic_result_sha256=traffic_result.result_sha256,
        limitation_evidence_refs=(
            {"TRACES_DIAGNOSIS_UNAVAILABLE": "traces:1"} if limitations else {}
        ),
    )
    queue_snapshot = NoFaultQueueSnapshotV023.build(
        environment_id=audit.environment_id,
        observed_at=NOW + timedelta(minutes=7),
        pending_jobs=0,
        running_jobs=0,
        failed_jobs=0,
        queue_fault_flag=0,
    )
    return (
        audit,
        restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        queue_snapshot,
    )


@pytest.mark.parametrize(
    ("diagnosis_terminal", "hidden_failure", "expected"),
    (
        ("NO_INCIDENT", False, NOFAULT_FULLY_SUPPORTED_V023),
        ("INSUFFICIENT_EVIDENCE", False, NOFAULT_CAPABILITY_LIMITED_V023),
        ("CORE_KNOWN", False, NOFAULT_NOT_SUPPORTED_V023),
        ("EXTENSION_KNOWN", False, NOFAULT_NOT_SUPPORTED_V023),
        ("OPEN_WORLD", False, NOFAULT_NOT_SUPPORTED_V023),
        ("CONFLICTING_EVIDENCE", False, NOFAULT_NOT_SUPPORTED_V023),
        ("NO_INCIDENT", True, NOFAULT_NOT_SUPPORTED_V023),
    ),
)
def test_nofault_scorer_mints_only_the_three_measured_terminals(
    diagnosis_terminal: str,
    hidden_failure: bool,
    expected: str,
) -> None:
    (
        audit,
        restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        queue_snapshot,
    ) = _nofault_inputs(
        terminal=diagnosis_terminal,
        hidden_failure=hidden_failure,
    )

    result = score_nofault_v023(
        baseline_audit=audit,
        restart_proof=restart,
        incident=incident,
        diagnosis=diagnosis,
        bundle=bundle,
        capability_assessment=assessment,
        execution_profile=execution_profile,
        traffic_result=traffic_result,
        queue_snapshot=queue_snapshot,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        incident_count=1,
        diagnosis_count=1,
        fault_family_count=0,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
    )

    assert result.terminal.value == expected
    if hidden_failure:
        assert "HIDDEN_CONNECTOR_FAILURE" in result.reasons


def test_nofault_runtime_health_is_read_from_runtime_evidence() -> None:
    (
        audit,
        restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        queue_snapshot,
    ) = _nofault_inputs(runtime_healthy=False)

    result = score_nofault_v023(
        baseline_audit=audit,
        restart_proof=restart,
        incident=incident,
        diagnosis=diagnosis,
        bundle=bundle,
        capability_assessment=assessment,
        execution_profile=execution_profile,
        traffic_result=traffic_result,
        queue_snapshot=queue_snapshot,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        incident_count=1,
        diagnosis_count=1,
        fault_family_count=0,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
    )

    assert result.terminal.value == NOFAULT_NOT_SUPPORTED_V023
    assert "FRESH_HEALTHY_RUNTIME_MISSING" in result.reasons


def test_nofault_traffic_pass_is_derived_from_frozen_profile_and_counts() -> None:
    execution_profile = NoFaultExecutionProfileV023.default()
    with pytest.raises(ValidationError, match="disposition differs"):
        NoFaultTrafficResultV023.build(
            environment_id="env-" + "1" * 24,
            incident_id="inc-" + "2" * 24,
            window=ConnectorWindowV1(
                started_at=NOW,
                ended_at=NOW + timedelta(minutes=1),
            ),
            profile_sha256=execution_profile.profile_sha256,
            planned_request_count=execution_profile.request_count,
            completed_request_count=0,
            error_count=0,
            requests_per_second=execution_profile.requests_per_second,
            maximum_error_fraction=execution_profile.maximum_error_fraction,
            queue_fault_flag=execution_profile.queue_fault_flag,
            passed=True,
        )


@pytest.mark.parametrize(
    "traffic_update",
    (
        {"environment_id": "env-" + "f" * 24},
        {"incident_id": "inc-" + "f" * 24},
        {
            "window": ConnectorWindowV1(
                started_at=NOW + timedelta(minutes=4),
                ended_at=NOW + timedelta(minutes=5),
            )
        },
    ),
)
def test_nofault_traffic_result_cannot_be_replayed_across_episodes(
    traffic_update: dict[str, object],
) -> None:
    (
        audit,
        restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        queue_snapshot,
    ) = _nofault_inputs()
    traffic_payload = traffic_result.model_dump(
        mode="python",
        exclude={"schema_version", "result_sha256"},
    )
    replayed = NoFaultTrafficResultV023.build(**{**traffic_payload, **traffic_update})

    result = score_nofault_v023(
        baseline_audit=audit,
        restart_proof=restart,
        incident=incident,
        diagnosis=diagnosis,
        bundle=bundle,
        capability_assessment=assessment,
        execution_profile=execution_profile,
        traffic_result=replayed,
        queue_snapshot=queue_snapshot,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        incident_count=1,
        diagnosis_count=1,
        fault_family_count=0,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
    )

    assert result.terminal.value == NOFAULT_NOT_SUPPORTED_V023
    assert "HEALTHY_TRAFFIC_FAILED_OR_UNBOUND" in result.reasons


def test_nofault_execution_profile_is_the_frozen_config_profile() -> None:
    payload = NoFaultExecutionProfileV023.default().model_dump(
        mode="json",
        exclude={"profile_sha256"},
    )
    payload["seed"] = 1
    payload["maximum_error_fraction"] = 0.05
    with pytest.raises(ValidationError, match="profile digest differs"):
        NoFaultExecutionProfileV023.model_validate(
            {**payload, "profile_sha256": semantic_sha256_v22(payload)}
        )


def test_nofault_no_incident_requires_sufficient_source_coverage() -> None:
    (
        audit,
        restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        queue_snapshot,
    ) = _nofault_inputs()
    diagnosis_payload = diagnosis.model_dump(
        mode="python",
        exclude={"result_sha256"},
    )
    diagnosis_payload["supporting_evidence_refs"] = ("logs:1", "runtime:1")
    diagnosis = _seal(DiagnosisResultV1, diagnosis_payload, "result_sha256")
    bundle = EvidenceBundleV1(
        incident_id=incident.incident_id,
        diagnosis_id=diagnosis.diagnosis_id,
        objects=tuple(
            item
            for item in bundle.objects
            if item.source is not EvidenceSourceV22.METRICS
        ),
        supporting_evidence_refs=diagnosis.supporting_evidence_refs,
        contradicting_evidence_refs=(),
    )
    assessment_payload = assessment.model_dump(
        mode="python",
        exclude={"schema_version", "assessment_sha256"},
    )
    assessment_payload["successful_sources"] = (
        EvidenceSourceV22.LOGS,
        EvidenceSourceV22.RUNTIME,
    )
    insufficient = NoFaultCapabilityAssessmentV023.build(**assessment_payload)

    result = score_nofault_v023(
        baseline_audit=audit,
        restart_proof=restart,
        incident=incident,
        diagnosis=diagnosis,
        bundle=bundle,
        capability_assessment=insufficient,
        execution_profile=execution_profile,
        traffic_result=traffic_result,
        queue_snapshot=queue_snapshot,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        incident_count=1,
        diagnosis_count=1,
        fault_family_count=0,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
    )

    assert result.terminal.value == NOFAULT_NOT_SUPPORTED_V023
    assert "REQUIRED_SOURCE_COVERAGE_INSUFFICIENT" in result.reasons


def test_nofault_successful_source_query_must_target_exactly_checkout() -> None:
    (
        audit,
        restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        queue_snapshot,
    ) = _nofault_inputs()
    metrics = next(
        item for item in bundle.objects if item.source is EvidenceSourceV22.METRICS
    )
    payload = dict(metrics.payload)
    original = ConnectorQueryResultV1.model_validate(
        payload["connector_result"],
        strict=False,
    )
    payload["connector_result"] = ConnectorQueryResultV1.build(
        source=original.source,
        status=original.status,
        requested_services=("cart", "checkout"),
        covered_services=original.covered_services,
        window=original.window,
        records=original.records,
        truncated=original.truncated,
        safe_error_code=original.safe_error_code,
        latency_ms=original.latency_ms,
    ).model_dump(mode="json")
    bundle = bundle.model_copy(
        update={
            "objects": tuple(
                _object(metrics.evidence_ref, metrics.source, payload)
                if item.evidence_ref == metrics.evidence_ref
                else item
                for item in bundle.objects
            )
        }
    )

    result = score_nofault_v023(
        baseline_audit=audit,
        restart_proof=restart,
        incident=incident,
        diagnosis=diagnosis,
        bundle=bundle,
        capability_assessment=assessment,
        execution_profile=execution_profile,
        traffic_result=traffic_result,
        queue_snapshot=queue_snapshot,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        incident_count=1,
        diagnosis_count=1,
        fault_family_count=0,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
    )

    assert result.terminal.value == NOFAULT_NOT_SUPPORTED_V023
    assert "REQUIRED_SOURCE_COVERAGE_INSUFFICIENT" in result.reasons


def test_nofault_capability_limitation_must_match_failed_source() -> None:
    (
        audit,
        restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        queue_snapshot,
    ) = _nofault_inputs(terminal="INSUFFICIENT_EVIDENCE")
    assessment_payload = assessment.model_dump(
        mode="python",
        exclude={"schema_version", "assessment_sha256"},
    )
    assessment_payload["limitation_evidence_refs"] = {
        "TRACES_DIAGNOSIS_UNAVAILABLE": "logs:1"
    }
    wrong_source = NoFaultCapabilityAssessmentV023.build(**assessment_payload)

    result = score_nofault_v023(
        baseline_audit=audit,
        restart_proof=restart,
        incident=incident,
        diagnosis=diagnosis,
        bundle=bundle,
        capability_assessment=wrong_source,
        execution_profile=execution_profile,
        traffic_result=traffic_result,
        queue_snapshot=queue_snapshot,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        incident_count=1,
        diagnosis_count=1,
        fault_family_count=0,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
    )

    assert result.terminal.value == NOFAULT_NOT_SUPPORTED_V023
    assert "CAPABILITY_LIMITATION_NOT_EVIDENCE_BACKED" in result.reasons


def test_nofault_capability_limitation_rejects_self_reported_failure() -> None:
    (
        audit,
        restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        queue_snapshot,
    ) = _nofault_inputs(terminal="INSUFFICIENT_EVIDENCE")
    traces = next(
        item for item in bundle.objects if item.source is EvidenceSourceV22.TRACES
    )
    malformed = _object(
        traces.evidence_ref,
        traces.source,
        {
            "connector_result": {
                "status": "FAILURE_UNAVAILABLE",
                "safe_error_code": "TRACES_UNAVAILABLE",
            }
        },
    )
    bundle = bundle.model_copy(
        update={
            "objects": tuple(
                malformed if item.evidence_ref == traces.evidence_ref else item
                for item in bundle.objects
            )
        }
    )

    result = score_nofault_v023(
        baseline_audit=audit,
        restart_proof=restart,
        incident=incident,
        diagnosis=diagnosis,
        bundle=bundle,
        capability_assessment=assessment,
        execution_profile=execution_profile,
        traffic_result=traffic_result,
        queue_snapshot=queue_snapshot,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        incident_count=1,
        diagnosis_count=1,
        fault_family_count=0,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
    )

    assert result.terminal.value == NOFAULT_NOT_SUPPORTED_V023
    assert "CAPABILITY_LIMITATION_NOT_EVIDENCE_BACKED" in result.reasons


def test_nofault_runtime_window_must_start_with_the_incident_episode() -> None:
    (
        audit,
        restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        queue_snapshot,
    ) = _nofault_inputs()
    runtime = next(
        item for item in bundle.objects if item.source is EvidenceSourceV22.RUNTIME
    )
    payload = dict(runtime.payload)
    original_result = ConnectorQueryResultV1.model_validate(
        payload["connector_result"],
        strict=False,
    )
    payload["connector_result"] = ConnectorQueryResultV1.build(
        source=original_result.source,
        status=original_result.status,
        requested_services=original_result.requested_services,
        covered_services=original_result.covered_services,
        window=ConnectorWindowV1(
            started_at=NOW + timedelta(minutes=3),
            ended_at=incident.diagnosis_observed_at,
        ),
        records=original_result.records,
        truncated=original_result.truncated,
        safe_error_code=original_result.safe_error_code,
        latency_ms=original_result.latency_ms,
    ).model_dump(mode="json")
    bundle = bundle.model_copy(
        update={
            "objects": tuple(
                _object(runtime.evidence_ref, runtime.source, payload)
                if item.evidence_ref == runtime.evidence_ref
                else item
                for item in bundle.objects
            )
        }
    )

    result = score_nofault_v023(
        baseline_audit=audit,
        restart_proof=restart,
        incident=incident,
        diagnosis=diagnosis,
        bundle=bundle,
        capability_assessment=assessment,
        execution_profile=execution_profile,
        traffic_result=traffic_result,
        queue_snapshot=queue_snapshot,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        incident_count=1,
        diagnosis_count=1,
        fault_family_count=0,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
    )

    assert result.terminal.value == NOFAULT_NOT_SUPPORTED_V023
    assert "FRESH_HEALTHY_RUNTIME_MISSING" in result.reasons


@pytest.mark.parametrize(
    ("incident_update",),
    (
        ({"candidate_logical_services": ("cart",)},),
        ({"candidate_service_ids": ("svc-" + "8" * 24,)},),
    ),
)
def test_nofault_incident_candidates_must_match_checkout_baseline_entity(
    incident_update: dict[str, object],
) -> None:
    (
        audit,
        restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        queue_snapshot,
    ) = _nofault_inputs()
    incident_payload = incident.model_dump(mode="python", exclude={"incident_sha256"})
    incident = _seal(
        IncidentRecordV1,
        {**incident_payload, **incident_update},
        "incident_sha256",
    )

    result = score_nofault_v023(
        baseline_audit=audit,
        restart_proof=restart,
        incident=incident,
        diagnosis=diagnosis,
        bundle=bundle,
        capability_assessment=assessment,
        execution_profile=execution_profile,
        traffic_result=traffic_result,
        queue_snapshot=queue_snapshot,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        incident_count=1,
        diagnosis_count=1,
        fault_family_count=0,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
    )

    assert result.terminal.value == NOFAULT_NOT_SUPPORTED_V023
    assert "NOFAULT_EPISODE_BINDING_INVALID" in result.reasons


@pytest.mark.parametrize(
    ("source", "truncated", "started_at"),
    (
        (EvidenceSourceV22.LOGS, True, NOW + timedelta(minutes=5)),
        (EvidenceSourceV22.METRICS, False, NOW + timedelta(minutes=4)),
    ),
)
def test_nofault_required_sources_must_be_complete_and_episode_fresh(
    source: EvidenceSourceV22,
    truncated: bool,
    started_at: datetime,
) -> None:
    (
        audit,
        restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        queue_snapshot,
    ) = _nofault_inputs()
    evidence = next(item for item in bundle.objects if item.source is source)
    payload = dict(evidence.payload)
    original_result = ConnectorQueryResultV1.model_validate(
        payload["connector_result"],
        strict=False,
    )
    payload["connector_result"] = ConnectorQueryResultV1.build(
        source=original_result.source,
        status=original_result.status,
        requested_services=original_result.requested_services,
        covered_services=original_result.covered_services,
        window=ConnectorWindowV1(
            started_at=started_at,
            ended_at=incident.diagnosis_observed_at,
        ),
        records=original_result.records,
        truncated=truncated,
        safe_error_code=original_result.safe_error_code,
        latency_ms=original_result.latency_ms,
    ).model_dump(mode="json")
    bundle = bundle.model_copy(
        update={
            "objects": tuple(
                _object(evidence.evidence_ref, evidence.source, payload)
                if item.evidence_ref == evidence.evidence_ref
                else item
                for item in bundle.objects
            )
        }
    )

    result = score_nofault_v023(
        baseline_audit=audit,
        restart_proof=restart,
        incident=incident,
        diagnosis=diagnosis,
        bundle=bundle,
        capability_assessment=assessment,
        execution_profile=execution_profile,
        traffic_result=traffic_result,
        queue_snapshot=queue_snapshot,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        incident_count=1,
        diagnosis_count=1,
        fault_family_count=0,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
    )

    assert result.terminal.value == NOFAULT_NOT_SUPPORTED_V023
    assert "REQUIRED_SOURCE_COVERAGE_INSUFFICIENT" in result.reasons


def test_nofault_logs_profile_binding_requires_connector_diagnostics() -> None:
    (
        audit,
        restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        queue_snapshot,
    ) = _nofault_inputs()
    logs = next(
        item for item in bundle.objects if item.source is EvidenceSourceV22.LOGS
    )
    payload = dict(logs.payload)
    payload["connector_diagnostics"] = [{"profile_sha256": ACTIVE_PROFILE_SHA256_V023}]
    bundle = bundle.model_copy(
        update={
            "objects": tuple(
                _object(logs.evidence_ref, logs.source, payload)
                if item.evidence_ref == logs.evidence_ref
                else item
                for item in bundle.objects
            )
        }
    )

    result = score_nofault_v023(
        baseline_audit=audit,
        restart_proof=restart,
        incident=incident,
        diagnosis=diagnosis,
        bundle=bundle,
        capability_assessment=assessment,
        execution_profile=execution_profile,
        traffic_result=traffic_result,
        queue_snapshot=queue_snapshot,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        incident_count=1,
        diagnosis_count=1,
        fault_family_count=0,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
    )

    assert result.terminal.value == NOFAULT_NOT_SUPPORTED_V023
    assert "LOGS_PROFILE_BINDING_MISSING" in result.reasons


def test_nofault_queue_is_measured_at_episode_time() -> None:
    (
        audit,
        restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        _queue_snapshot,
    ) = _nofault_inputs()
    queue_snapshot = NoFaultQueueSnapshotV023.build(
        environment_id=audit.environment_id,
        observed_at=NOW + timedelta(minutes=7),
        pending_jobs=1,
        running_jobs=0,
        failed_jobs=0,
        queue_fault_flag=0,
    )

    result = score_nofault_v023(
        baseline_audit=audit,
        restart_proof=restart,
        incident=incident,
        diagnosis=diagnosis,
        bundle=bundle,
        capability_assessment=assessment,
        execution_profile=execution_profile,
        traffic_result=traffic_result,
        queue_snapshot=queue_snapshot,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        incident_count=1,
        diagnosis_count=1,
        fault_family_count=0,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
    )

    assert result.terminal.value == NOFAULT_NOT_SUPPORTED_V023
    assert "NOFAULT_QUEUE_NOT_EMPTY_OR_FRESH" in result.reasons


def test_nofault_queue_snapshot_must_be_post_diagnosis() -> None:
    (
        audit,
        restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        _queue_snapshot,
    ) = _nofault_inputs()
    queue_snapshot = NoFaultQueueSnapshotV023.build(
        environment_id=audit.environment_id,
        observed_at=diagnosis.created_at - timedelta(seconds=1),
        pending_jobs=0,
        running_jobs=0,
        failed_jobs=0,
        queue_fault_flag=0,
    )

    result = score_nofault_v023(
        baseline_audit=audit,
        restart_proof=restart,
        incident=incident,
        diagnosis=diagnosis,
        bundle=bundle,
        capability_assessment=assessment,
        execution_profile=execution_profile,
        traffic_result=traffic_result,
        queue_snapshot=queue_snapshot,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        incident_count=1,
        diagnosis_count=1,
        fault_family_count=0,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
    )

    assert result.terminal.value == NOFAULT_NOT_SUPPORTED_V023
    assert "NOFAULT_QUEUE_NOT_EMPTY_OR_FRESH" in result.reasons


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("environment_id", "env-" + "f" * 24),
        ("service_identity_sha256", "f" * 64),
        ("capability_sha256", "f" * 64),
    ),
)
def test_nofault_requires_restart_proof_bound_to_readiness_audit(
    field: str,
    value: str,
) -> None:
    (
        audit,
        _restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        queue_snapshot,
    ) = _nofault_inputs()
    restart = BaselineRestartProofV023.build(
        before=_snapshot(
            audit,
            instance_marker="1",
            observed_at=NOW,
            **{field: value},
        ),
        after=_snapshot(
            audit,
            instance_marker="2",
            observed_at=NOW + timedelta(seconds=1),
            **{field: value},
        ),
    )

    with pytest.raises(ProductError, match="restart proof"):
        score_nofault_v023(
            baseline_audit=audit,
            restart_proof=restart,
            incident=incident,
            diagnosis=diagnosis,
            bundle=bundle,
            capability_assessment=assessment,
            execution_profile=execution_profile,
            traffic_result=traffic_result,
            queue_snapshot=queue_snapshot,
            active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
            incident_count=1,
            diagnosis_count=1,
            fault_family_count=0,
            action_authority_violations=0,
            agent_writes=0,
            runbook_executions=0,
        )


def test_nofault_restart_must_finish_before_incident_episode() -> None:
    (
        audit,
        _restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        queue_snapshot,
    ) = _nofault_inputs()
    late_restart = BaselineRestartProofV023.build(
        before=_snapshot(
            audit,
            instance_marker="3",
            observed_at=NOW + timedelta(minutes=8),
        ),
        after=_snapshot(
            audit,
            instance_marker="4",
            observed_at=NOW + timedelta(minutes=9),
        ),
    )

    result = score_nofault_v023(
        baseline_audit=audit,
        restart_proof=late_restart,
        incident=incident,
        diagnosis=diagnosis,
        bundle=bundle,
        capability_assessment=assessment,
        execution_profile=execution_profile,
        traffic_result=traffic_result,
        queue_snapshot=queue_snapshot,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        incident_count=1,
        diagnosis_count=1,
        fault_family_count=0,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
    )

    assert result.terminal.value == NOFAULT_NOT_SUPPORTED_V023
    assert "NOFAULT_EPISODE_BINDING_INVALID" in result.reasons


def test_nofault_result_digest_binds_specific_evidence_object_bytes() -> None:
    (
        audit,
        restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        queue_snapshot,
    ) = _nofault_inputs()
    original_acceptance = score_nofault_v023(
        baseline_audit=audit,
        restart_proof=restart,
        incident=incident,
        diagnosis=diagnosis,
        bundle=bundle,
        capability_assessment=assessment,
        execution_profile=execution_profile,
        traffic_result=traffic_result,
        queue_snapshot=queue_snapshot,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        incident_count=1,
        diagnosis_count=1,
        fault_family_count=0,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
    )
    metrics = next(
        item for item in bundle.objects if item.source is EvidenceSourceV22.METRICS
    )
    payload = dict(metrics.payload)
    connector = ConnectorQueryResultV1.model_validate(
        payload["connector_result"],
        strict=False,
    )
    changed_record = connector.records[0].model_copy(update={"value": 31.0})
    payload["connector_result"] = ConnectorQueryResultV1.build(
        source=connector.source,
        status=connector.status,
        requested_services=connector.requested_services,
        covered_services=connector.covered_services,
        window=connector.window,
        records=(changed_record,),
        truncated=connector.truncated,
        safe_error_code=connector.safe_error_code,
        latency_ms=connector.latency_ms,
    ).model_dump(mode="json")
    changed_metrics = _object(metrics.evidence_ref, metrics.source, payload)
    changed_bundle = bundle.model_copy(
        update={
            "objects": tuple(
                changed_metrics if item.evidence_ref == metrics.evidence_ref else item
                for item in bundle.objects
            )
        }
    )
    changed_acceptance = score_nofault_v023(
        baseline_audit=audit,
        restart_proof=restart,
        incident=incident,
        diagnosis=diagnosis,
        bundle=changed_bundle,
        capability_assessment=assessment,
        execution_profile=execution_profile,
        traffic_result=traffic_result,
        queue_snapshot=queue_snapshot,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        incident_count=1,
        diagnosis_count=1,
        fault_family_count=0,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
    )

    assert changed_acceptance.terminal == original_acceptance.terminal
    assert changed_metrics.object_sha256 != metrics.object_sha256
    assert changed_acceptance.evidence_bundle_sha256 != (
        original_acceptance.evidence_bundle_sha256
    )
    assert changed_acceptance.result_sha256 != original_acceptance.result_sha256


def test_nofault_external_action_counters_fail_closed() -> None:
    (
        audit,
        restart,
        incident,
        diagnosis,
        bundle,
        assessment,
        execution_profile,
        traffic_result,
        queue_snapshot,
    ) = _nofault_inputs()

    result = score_nofault_v023(
        baseline_audit=audit,
        restart_proof=restart,
        incident=incident,
        diagnosis=diagnosis,
        bundle=bundle,
        capability_assessment=assessment,
        execution_profile=execution_profile,
        traffic_result=traffic_result,
        queue_snapshot=queue_snapshot,
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        incident_count=1,
        diagnosis_count=1,
        fault_family_count=0,
        action_authority_violations=0,
        agent_writes=1,
        runbook_executions=0,
    )

    assert result.terminal.value == NOFAULT_NOT_SUPPORTED_V023
    assert result.agent_writes == 1
    assert "UNEXPECTED_ACTION_COUNTER" in result.reasons


def test_nofault_open_world_is_preserved_without_fault_family_ingestion() -> None:
    assert (
        should_ingest_open_world_v023(
            diagnosis_terminal="OPEN_WORLD",
            incident_labels={"fault": "none"},
        )
        is False
    )
    assert (
        should_ingest_open_world_v023(
            diagnosis_terminal="OPEN_WORLD",
            incident_labels={"fault": "cart_failure"},
        )
        is True
    )
