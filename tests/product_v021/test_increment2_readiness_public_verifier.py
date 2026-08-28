from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.ci.verify_product_v021_increment1 as increment1_verifier
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.product.baselines import BaselineBuildModeV1, BaselineBuildPolicyV1
from ecomsre.product.connectors.base import (
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.contracts import ConnectorKindV1
from ecomsre.product.pilot.baseline_audit_v021 import (
    BaselineConnectorBindingV021,
    BaselineConnectorExpectationV021,
    build_baseline_readiness_audit_v021,
)
from ecomsre.product.pilot.baseline_readiness_v021 import (
    PilotBaselineReadinessProfileV021,
    render_public_readiness_markdown_v021,
)
from ecomsre.product.pilot.live_baseline_readiness_v021 import (
    _write_public_readiness_v021,
)
from ecomsre.product.pilot.readiness_attempts_v021 import (
    PublicReadinessAttemptV021,
    READINESS_PASS_V021,
    write_public_readiness_attempt_v021,
)
from scripts.ci.verify_product_v021_increment1 import (
    _verify_readiness_attempt_sequence_v021,
    _verify_readiness_terminal_artifacts_v021,
)


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 28, 1, 0, tzinfo=UTC)
POLICY = BaselineBuildPolicyV1(
    mode=BaselineBuildModeV1.DEMO_ONLY,
    lookback_seconds=180,
    window_count=5,
    minimum_successful_windows=4,
    warmup_seconds=180,
)


def _window(index: int) -> ConnectorWindowV1:
    started = NOW + timedelta(seconds=index * 36)
    return ConnectorWindowV1(
        started_at=started,
        ended_at=started + timedelta(seconds=36),
    )


def _accepted_window(index: int) -> tuple[ConnectorQueryResultV1, ...]:
    window = _window(index)
    log = LogRecordV22(
        schema_version="dta-v22.log-record.v1",
        observed_at=window.ended_at - timedelta(seconds=1),
        service="checkout",
        severity="DIAGNOSTIC",
        message="checkout completed",
    )
    return (
        ConnectorQueryResultV1.build(
            source=EvidenceSourceV22.METRICS,
            status=ReadSourceStatusV22.SUCCESS_EMPTY,
            requested_services=("checkout",),
            covered_services=("checkout",),
            window=window,
            records=(),
            truncated=False,
            safe_error_code=None,
            latency_ms=2.5,
        ),
        ConnectorQueryResultV1.build(
            source=EvidenceSourceV22.LOGS,
            status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
            requested_services=("checkout",),
            covered_services=("checkout",),
            window=window,
            records=(log,),
            truncated=False,
            safe_error_code=None,
            latency_ms=2.5,
        ),
    )


def _rejected_window(index: int) -> tuple[ConnectorQueryResultV1, ...]:
    window = _window(index)
    return tuple(
        ConnectorQueryResultV1.build(
            source=source,
            status=ReadSourceStatusV22.SUCCESS_EMPTY,
            requested_services=("checkout",),
            covered_services=("checkout",),
            window=window,
            records=(),
            truncated=False,
            safe_error_code=None,
            latency_ms=2.5,
        )
        for source in (EvidenceSourceV22.METRICS, EvidenceSourceV22.LOGS)
    )


def _bindings() -> tuple[BaselineConnectorBindingV021, ...]:
    return (
        BaselineConnectorBindingV021(
            connector_name="prometheus",
            connector_kind=ConnectorKindV1.PROMETHEUS,
        ),
        BaselineConnectorBindingV021(
            connector_name="opensearch",
            connector_kind=ConnectorKindV1.OPENSEARCH,
        ),
    )


def _expectations() -> tuple[BaselineConnectorExpectationV021, ...]:
    return (
        BaselineConnectorExpectationV021(
            connector_name="opensearch",
            connector_kind=ConnectorKindV1.OPENSEARCH,
            expected_sources=(EvidenceSourceV22.LOGS,),
        ),
        BaselineConnectorExpectationV021(
            connector_name="prometheus",
            connector_kind=ConnectorKindV1.PROMETHEUS,
            expected_sources=(EvidenceSourceV22.METRICS,),
        ),
    )


def _traffic_result() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "ecomsre.product.healthy-traffic-result.v021",
        "request_seed": 501,
        "attempted": 180,
        "succeeded": 180,
        "failed": 0,
        "stopped_on_error_budget": False,
        "duration_seconds": 360.0,
    }
    payload["result_sha256"] = semantic_sha256_v22(payload)
    return payload


def _write_readiness_pass_artifacts(
    root: Path,
) -> tuple[dict[str, object], PilotBaselineReadinessProfileV021, object]:
    analysis = root / "docs/analysis"
    analysis.mkdir(parents=True)
    profile = PilotBaselineReadinessProfileV021.model_validate_json(
        (ROOT / "config/product-v021/baseline-readiness/profile.json").read_bytes()
    )
    windows = tuple(_accepted_window(index) for index in range(5))
    audit = build_baseline_readiness_audit_v021(
        environment_id="env-" + "1" * 24,
        service_ids=("checkout",),
        baseline_entity_service_ids=("svc-" + "2" * 24,),
        build_policy=POLICY.model_dump(mode="json"),
        capability_sha256="3" * 64,
        required_complete_sources=(EvidenceSourceV22.METRICS,),
        window_results=windows,
        expected_windows=tuple(_window(index) for index in range(5)),
        connector_bindings=tuple(_bindings() for _index in range(5)),
        connector_expectations=tuple(_expectations() for _index in range(5)),
    )
    attempt = write_public_readiness_attempt_v021(
        analysis / "product-v021-baseline-readiness-attempt-1.json",
        {
            "schema_version": (
                "ecomsre.product.public-baseline-readiness-attempt.v021"
            ),
            "run_number": 1,
            "changed_attempt_number": 1,
            "attempt_signature_sha256": "4" * 64,
            "changed_parameter": "INITIAL",
            "infrastructure_replacement": False,
            "terminal": READINESS_PASS_V021,
            "disposition": "PASS",
            "failure_domain": "NONE",
            "observed_at": NOW.isoformat(),
            "environment_id": audit.environment_id,
            "baseline_id": "base-" + "5" * 24,
            "baseline_sha256": "6" * 64,
            "baseline_active": True,
            "audit": audit.model_dump(mode="json"),
            "audit_sha256": audit.audit_sha256,
            "parity_sha256": audit.parity_sha256,
            "scheduled_window_count": 5,
            "accepted_window_count": 5,
            "traffic_result": _traffic_result(),
            "queue_default_unchanged": True,
            "healthy_traffic_stopped": True,
            "api_restart_verified": True,
            "worker_restart_verified": True,
            "outer_baseline_restored": True,
            "owned_demo_cleanup": "CLEAN",
            "baseline_job_safe_error_code": None,
            "safe_error_type": None,
            "private_report_sha256": "7" * 64,
            "failure_before_cleanup_sha256": None,
            "fault_attempt_count": 0,
            "action_authority": "NONE",
            "action_authority_violations": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
        },
    )
    _write_public_readiness_v021(
        repository_root=root,
        terminal=READINESS_PASS_V021,
        observed_at=NOW,
        normalized_attempt=attempt,
    )
    progress = json.loads(
        (analysis / "product-v021-progress.json").read_text(encoding="utf-8")
    )
    binding = SimpleNamespace(
        terminal=READINESS_PASS_V021,
        environment_id=audit.environment_id,
        baseline_id=attempt.baseline_id,
        baseline_sha256=attempt.baseline_sha256,
        build_policy=POLICY,
        accepted_window_ordinals=(1, 2, 3, 4, 5),
        source_coverage_matrix=audit.coverage_matrix,
        capability_matrix_sha256=audit.capability_sha256,
        healthy_traffic_profile_sha256=semantic_sha256_v22(
            profile.healthy_traffic_profile.model_dump(mode="json")
        ),
        audit_sha256=audit.audit_sha256,
        parity_sha256=audit.parity_sha256,
    )
    return progress, profile, binding


def _rehashed_two_attempt_sequence(
    root: Path,
    *,
    first_updates: dict[str, object] | None = None,
    second_updates: dict[str, object] | None = None,
) -> tuple[PublicReadinessAttemptV021, PublicReadinessAttemptV021]:
    _write_readiness_pass_artifacts(root)
    path = root / "docs/analysis/product-v021-baseline-readiness-attempt-1.json"
    original = json.loads(path.read_text(encoding="utf-8"))
    original.pop("report_sha256")
    path.unlink()
    failing_audit = build_baseline_readiness_audit_v021(
        environment_id="env-" + "1" * 24,
        service_ids=("checkout",),
        baseline_entity_service_ids=("svc-" + "2" * 24,),
        build_policy=POLICY.model_dump(mode="json"),
        capability_sha256="3" * 64,
        required_complete_sources=(EvidenceSourceV22.METRICS,),
        window_results=tuple(
            _accepted_window(index) if index < 3 else _rejected_window(index)
            for index in range(5)
        ),
        expected_windows=tuple(_window(index) for index in range(5)),
        connector_bindings=tuple(_bindings() for _index in range(5)),
        connector_expectations=tuple(_expectations() for _index in range(5)),
    )
    first_payload = {
        **original,
        "terminal": "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_REPAIR_REQUIRED",
        "disposition": "TARGETED_REPAIR_ELIGIBLE",
        "failure_domain": "CAMPAIGN",
        "baseline_id": None,
        "baseline_sha256": None,
        "baseline_active": False,
        "audit": failing_audit.model_dump(mode="json"),
        "audit_sha256": failing_audit.audit_sha256,
        "parity_sha256": failing_audit.parity_sha256,
        "accepted_window_count": failing_audit.accepted_window_count,
        "failure_before_cleanup_sha256": "9" * 64,
        "baseline_job_safe_error_code": "BASELINE_INSUFFICIENT_VALID_WINDOWS",
        "safe_error_type": "RuntimeError",
        **(first_updates or {}),
    }
    second_payload = {
        **original,
        "run_number": 2,
        "changed_attempt_number": 2,
        "attempt_signature_sha256": "8" * 64,
        "changed_parameter": "HEALTHY_TRAFFIC_RATE",
        **(second_updates or {}),
    }
    first = write_public_readiness_attempt_v021(path, first_payload)
    second = write_public_readiness_attempt_v021(
        root / "docs/analysis/product-v021-baseline-readiness-attempt-2.json",
        second_payload,
    )
    return first, second


def test_readiness_terminal_verifier_accepts_exact_bound_public_pass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    progress, profile, binding = _write_readiness_pass_artifacts(tmp_path)
    monkeypatch.setattr(
        increment1_verifier,
        "load_pilot_baseline_binding_v021",
        lambda _path: binding,
    )

    _verify_readiness_terminal_artifacts_v021(
        tmp_path,
        progress=progress,
        profile=profile,
    )


def test_readiness_terminal_verifier_persists_through_calibration_progress(
    tmp_path: Path,
    monkeypatch,
) -> None:
    progress, profile, binding = _write_readiness_pass_artifacts(tmp_path)
    progress["terminal"] = "ECOMSRE_PRODUCT_V021_UNKNOWN_FAULT_PROFILE_PASS"
    progress["profile_calibration_iteration_count"] = 1
    progress["profile_calibration_changed_iteration_count"] = 0
    progress["calibration_execution_count"] = 1
    monkeypatch.setattr(
        increment1_verifier,
        "load_pilot_baseline_binding_v021",
        lambda _path: binding,
    )

    _verify_readiness_terminal_artifacts_v021(
        tmp_path,
        progress=progress,
        profile=profile,
    )


def test_public_readiness_attempt_sequence_accepts_targeted_semantic_repair(
    tmp_path: Path,
) -> None:
    attempts = _rehashed_two_attempt_sequence(tmp_path)

    _verify_readiness_attempt_sequence_v021(attempts)


def test_public_readiness_attempt_sequence_accepts_identical_infrastructure_retry(
    tmp_path: Path,
) -> None:
    attempts = _rehashed_two_attempt_sequence(
        tmp_path,
        first_updates={
            "disposition": "INFRASTRUCTURE_REPLACEMENT_ELIGIBLE",
            "failure_domain": "INFRASTRUCTURE_STARTUP",
            "environment_id": None,
            "audit": None,
            "audit_sha256": None,
            "parity_sha256": None,
            "scheduled_window_count": 0,
            "accepted_window_count": 0,
            "traffic_result": None,
            "healthy_traffic_stopped": False,
            "api_restart_verified": False,
            "worker_restart_verified": False,
            "baseline_job_safe_error_code": None,
        },
        second_updates={
            "changed_attempt_number": 1,
            "attempt_signature_sha256": "4" * 64,
            "changed_parameter": "INITIAL",
            "infrastructure_replacement": True,
        },
    )

    _verify_readiness_attempt_sequence_v021(attempts)


@pytest.mark.parametrize(
    "first_updates",
    (
        {"failure_before_cleanup_sha256": None},
        {"owned_demo_cleanup": "BLOCKED"},
        {"failure_domain": "NONE"},
        {"changed_attempt_number": 2},
    ),
)
def test_public_targeted_repair_rejects_rehashed_ineligible_prestate(
    tmp_path: Path,
    first_updates: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="targeted readiness repair is not eligible"):
        _rehashed_two_attempt_sequence(tmp_path, first_updates=first_updates)


@pytest.mark.parametrize(
    "first_updates",
    (
        {
            "disposition": "INFRASTRUCTURE_REPLACEMENT_ELIGIBLE",
            "failure_domain": "INFRASTRUCTURE_STARTUP",
        },
        {
            "disposition": "INFRASTRUCTURE_REPLACEMENT_ELIGIBLE",
            "failure_domain": "CAMPAIGN",
            "environment_id": None,
            "audit": None,
            "audit_sha256": None,
            "parity_sha256": None,
            "scheduled_window_count": 0,
            "accepted_window_count": 0,
        },
        {
            "disposition": "INFRASTRUCTURE_REPLACEMENT_ELIGIBLE",
            "failure_domain": "INFRASTRUCTURE_STARTUP",
            "environment_id": None,
            "audit": None,
            "audit_sha256": None,
            "parity_sha256": None,
            "scheduled_window_count": 0,
            "accepted_window_count": 0,
            "infrastructure_replacement": True,
        },
        {
            "disposition": "INFRASTRUCTURE_REPLACEMENT_ELIGIBLE",
            "failure_domain": "INFRASTRUCTURE_STARTUP",
            "environment_id": None,
            "audit": None,
            "audit_sha256": None,
            "parity_sha256": None,
            "scheduled_window_count": 5,
            "accepted_window_count": 5,
            "traffic_result": None,
            "healthy_traffic_stopped": False,
            "api_restart_verified": False,
            "worker_restart_verified": False,
        },
    ),
)
def test_public_infrastructure_retry_rejects_rehashed_ineligible_prestate(
    tmp_path: Path,
    first_updates: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="infrastructure replacement is not eligible"):
        _rehashed_two_attempt_sequence(tmp_path, first_updates=first_updates)


@pytest.mark.parametrize(
    ("first_updates", "second_updates"),
    (
        ({"changed_parameter": "HEALTHY_TRAFFIC_RATE"}, {}),
        ({}, {"infrastructure_replacement": True}),
        ({}, {"attempt_signature_sha256": "4" * 64}),
        ({}, {"changed_attempt_number": 1}),
    ),
)
def test_public_readiness_attempt_sequence_rejects_rehashed_state_bypass(
    tmp_path: Path,
    first_updates: dict[str, object],
    second_updates: dict[str, object],
) -> None:
    attempts = _rehashed_two_attempt_sequence(
        tmp_path,
        first_updates=first_updates,
        second_updates=second_updates,
    )

    with pytest.raises(ValueError, match="sequence"):
        _verify_readiness_attempt_sequence_v021(attempts)


@pytest.mark.parametrize(
    "artifact_name",
    (
        "product-v021-baseline-readiness.json",
        "product-v021-baseline-readiness.md",
        "product-v021-baseline-readiness-attempt-1.json",
    ),
)
def test_readiness_terminal_verifier_rejects_missing_public_evidence(
    tmp_path: Path,
    monkeypatch,
    artifact_name: str,
) -> None:
    progress, profile, binding = _write_readiness_pass_artifacts(tmp_path)
    (tmp_path / "docs/analysis" / artifact_name).unlink()
    monkeypatch.setattr(
        increment1_verifier,
        "load_pilot_baseline_binding_v021",
        lambda _path: binding,
    )

    with pytest.raises(ValueError, match="public readiness"):
        _verify_readiness_terminal_artifacts_v021(
            tmp_path,
            progress=progress,
            profile=profile,
        )


def test_readiness_terminal_verifier_rejects_rehashed_binding_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    progress, profile, binding = _write_readiness_pass_artifacts(tmp_path)
    path = tmp_path / "docs/analysis/product-v021-baseline-readiness.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["latest_attempt"]["baseline_sha256"] = "f" * 64
    payload.pop("result_sha256")
    payload["result_sha256"] = semantic_sha256_v22(payload)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    (tmp_path / "docs/analysis/product-v021-baseline-readiness.md").write_text(
        render_public_readiness_markdown_v021(payload),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        increment1_verifier,
        "load_pilot_baseline_binding_v021",
        lambda _path: binding,
    )

    with pytest.raises(ValueError, match="public readiness"):
        _verify_readiness_terminal_artifacts_v021(
            tmp_path,
            progress=progress,
            profile=profile,
        )
