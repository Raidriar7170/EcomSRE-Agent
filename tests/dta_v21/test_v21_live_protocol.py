from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest
from pydantic_core import to_jsonable_python

from ecomsre.dta_v2.v21.contracts import semantic_sha256
from ecomsre.dta_v2.v21.live_protocol import (
    AD_CPU_RESOURCE_QUERY_ID_V1,
    AdCpuBusinessGuardrailResult,
    AdCpuResourceRecoveryProtocolV1,
    AdCpuResourceRecoveryResult,
    AdCpuResourceWindow,
    CalibrationBindingErrorV21,
    admit_ad_cpu_forward_step,
    build_ad_cpu_business_guardrail_result,
    build_ad_cpu_resource_recovery_result,
    build_ad_cpu_resource_window,
    build_public_ad_cpu_resource_recovery_projection,
    load_ad_cpu_resource_recovery_protocol_v1,
    verify_accepted_ad_cpu_calibration_binding,
    verify_public_ad_cpu_claim_text,
    verify_public_ad_cpu_resource_recovery_projection,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = REPO_ROOT / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
ACCEPTED_PRIVATE_RELATIVE = Path(
    "pr-d/captures/7d5e988b133f1a46bd04c82d9cfabd04/"
    "capture-campaign-closure.json"
)


def _protocol() -> AdCpuResourceRecoveryProtocolV1:
    return load_ad_cpu_resource_recovery_protocol_v1(PROTOCOL_PATH)


def _window(
    *,
    ordinal: int,
    started_at: datetime,
    cpu_p95: str = "5.000",
    capacity_ratio: str = "0.0034",
    business_latency: str = "3.500",
    run_id: str = "a" * 32,
    attempt_id: str = "prf-live-02-ad-cpu",
    service: str = "ad",
    query_id: str = AD_CPU_RESOURCE_QUERY_ID_V1,
    unit: str = "CPU_PERCENT",
    sample_count: int = 5,
    service_health_passed: bool = True,
    endpoint_reachable: bool = True,
    post_mitigation_started_at: datetime | None = None,
) -> AdCpuResourceWindow:
    return build_ad_cpu_resource_window(
        run_id=run_id,
        attempt_id=attempt_id,
        ordinal=ordinal,
        logical_service=service,
        query_id=query_id,
        unit=unit,
        sample_count=sample_count,
        window_started_at=started_at,
        window_ended_at=started_at + timedelta(seconds=10),
        post_mitigation_started_at=(
            post_mitigation_started_at
            or started_at - timedelta(seconds=(10 * (ordinal - 1)) + 21)
        ),
        cpu_p95_percent=cpu_p95,
        capacity_ratio=capacity_ratio,
        business_latency_p95_ms=business_latency,
        business_query_id="DTA_V21_AD_BUSINESS_LATENCY_P95_V1",
        business_aggregation="HISTOGRAM_QUANTILE_P95",
        business_query_window_seconds=30,
        business_query_started_at=started_at - timedelta(seconds=20),
        business_query_ended_at=started_at + timedelta(seconds=10),
        service_health_passed=service_health_passed,
        endpoint_reachable=endpoint_reachable,
        business_guardrail_binding_sha256=(
            _protocol().business_guardrail_binding_sha256
        ),
    )


def _guardrail(window: AdCpuResourceWindow) -> AdCpuBusinessGuardrailResult:
    return build_ad_cpu_business_guardrail_result(
        protocol=_protocol(), window=window
    )


def _result(
    windows: tuple[AdCpuResourceWindow, ...],
    guardrails: tuple[AdCpuBusinessGuardrailResult, ...] | None = None,
    *,
    flag_restored: bool = True,
) -> AdCpuResourceRecoveryResult:
    return build_ad_cpu_resource_recovery_result(
        protocol=_protocol(),
        windows=windows,
        guardrails=(guardrails or tuple(_guardrail(item) for item in windows)),
        baseline_flag_restored=flag_restored,
        baseline_state_digest_restored=True,
        non_owned_changes=0,
        unsafe_proposal_attempts=0,
        arbitrary_shell_attempts=0,
    )


def test_protocol_freezes_decimal_threshold_and_calibration_sources() -> None:
    protocol = _protocol()

    assert protocol.schema_version == "dta-v21.ad-cpu-resource-recovery-protocol.v1"
    assert protocol.accepted_before_first_prf_live_attempt is True
    assert protocol.resource_fault_observed is True
    assert protocol.resource_recovery_windows_required == 2
    assert protocol.resource_recovery_formula_id == (
        "BASELINE_PLUS_10PP_AND_90_PERCENT_FAULT_REDUCTION_V1"
    )
    assert protocol.baseline_cpu_p95_percent == "1.162"
    assert protocol.calibration_fault_cpu_p95_percent == "406.326"
    assert protocol.baseline_plus_10pp_cpu_p95_percent == "11.162"
    assert protocol.ten_percent_fault_cpu_p95_percent == "40.6326"
    assert protocol.resource_recovery_threshold_cpu_p95_percent == "11.162"
    assert protocol.calibration_capacity_ratio == "0.2709"
    assert protocol.capacity_ratio_ceiling == "0.5"
    assert protocol.business_impact_observed is False
    assert protocol.user_visible_recovery_claimed is False
    assert tuple(item.logical_path for item in protocol.calibration_source_artifacts) == (
        "private://dta-v21-p0-master-v1/pr-d/captures/"
        "7d5e988b133f1a46bd04c82d9cfabd04/capture-campaign-closure.json",
        "repo://docs/review-evidence/dta-v21-evaluation-freeze/"
        "capture-calibration-limitations.md",
        "repo://src/ecomsre/dta_v2/v21/owned_capture.py",
    )


def test_private_calibration_binding_requires_exact_accepted_bytes(
    tmp_path: Path,
) -> None:
    accepted_root = os.environ.get("DTA_V21_ACCEPTED_PRIVATE_ROOT")
    if accepted_root is None:
        pytest.skip("DTA_V21_ACCEPTED_PRIVATE_ROOT is not configured")
    source = Path(accepted_root) / ACCEPTED_PRIVATE_RELATIVE
    if not source.is_file():
        pytest.skip("accepted private PR-D calibration is unavailable")
    private_root = tmp_path / "private"
    target = private_root / ACCEPTED_PRIVATE_RELATIVE
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())

    verified = verify_accepted_ad_cpu_calibration_binding(
        protocol=_protocol(), repository_root=REPO_ROOT, private_root=private_root
    )
    assert verified.business_impact_observed is False
    assert verified.baseline_cpu_p95_percent == "1.162"
    assert verified.fault_cpu_p95_percent == "406.326"

    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["calibrations"][4]["cpu_p95_percent"] = 405.0
    target.write_text(json.dumps(raw, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(CalibrationBindingErrorV21, match="raw SHA-256"):
        verify_accepted_ad_cpu_calibration_binding(
            protocol=_protocol(), repository_root=REPO_ROOT, private_root=private_root
        )


def test_two_consecutive_fresh_windows_pass() -> None:
    started = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)
    first = _window(ordinal=1, started_at=started)
    second = _window(ordinal=2, started_at=started + timedelta(seconds=10))

    result = _result((first, second))

    assert result.terminal == "AD_CPU_RESOURCE_RECOVERY_PASS"
    assert result.resource_recovery_windows_passed == 2


@pytest.mark.parametrize(
    "windows",
    [
        lambda start: (_window(ordinal=1, started_at=start),),
        lambda start: (
            _window(ordinal=1, started_at=start),
            _window(ordinal=2, started_at=start + timedelta(seconds=10), cpu_p95="12.000"),
            _window(ordinal=3, started_at=start + timedelta(seconds=20)),
        ),
    ],
)
def test_recovery_requires_exactly_two_consecutive_passing_windows(windows) -> None:
    started = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="two consecutive"):
        _result(windows(started))


def test_recovery_rejects_a_gap_between_nominally_passing_windows() -> None:
    started = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)
    first = _window(ordinal=1, started_at=started)
    delayed = _window(ordinal=2, started_at=started + timedelta(minutes=10))

    with pytest.raises(ValueError, match="consecutive"):
        _result((first, delayed))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"run_id": "b" * 32}, "same run"),
        ({"attempt_id": "other-attempt"}, "same attempt"),
        ({"service": "email"}, "logical service"),
        ({"query_id": "other-query"}, "query"),
        ({"unit": "MILLISECONDS"}, "unit"),
        ({"sample_count": 4}, "sample count"),
        ({"capacity_ratio": "0.5001"}, "capacity ratio"),
    ],
)
def test_recovery_rejects_scope_and_measurement_drift(change, message: str) -> None:
    started = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)
    first = _window(ordinal=1, started_at=started)
    second = _window(
        ordinal=2, started_at=started + timedelta(seconds=10), **change
    )

    with pytest.raises(ValueError, match=message):
        _result((first, second))


def test_recovery_rejects_stale_window_and_unrestored_flag() -> None:
    started = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)
    stale = _window(
        ordinal=1,
        started_at=started,
        post_mitigation_started_at=started + timedelta(seconds=1),
    )
    fresh = _window(ordinal=2, started_at=started + timedelta(seconds=10))
    with pytest.raises(ValueError, match="post-mitigation"):
        _result((stale, fresh))

    first = _window(ordinal=1, started_at=started)
    with pytest.raises(ValueError, match="flag baseline"):
        _result((first, fresh), flag_restored=False)


def test_business_query_must_be_fresh_after_the_mitigation_anchor() -> None:
    started = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)
    anchor = started - timedelta(seconds=10)
    first = _window(
        ordinal=1,
        started_at=started,
        post_mitigation_started_at=anchor,
    )
    second = _window(
        ordinal=2,
        started_at=started + timedelta(seconds=10),
        post_mitigation_started_at=anchor,
    )

    with pytest.raises(ValueError, match="pre-mitigation data"):
        _result((first, second))


@pytest.mark.parametrize(
    ("health", "endpoint", "business_latency", "message"),
    [
        (False, True, "3.500", "service health"),
        (True, False, "3.500", "endpoint"),
        (True, True, "9.000", "business non-regression"),
    ],
)
def test_business_guardrail_fails_closed(
    health: bool, endpoint: bool, business_latency: str, message: str
) -> None:
    started = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)
    first = _window(
        ordinal=1,
        started_at=started,
        service_health_passed=health,
        endpoint_reachable=endpoint,
        business_latency=business_latency,
    )
    second = _window(ordinal=2, started_at=started + timedelta(seconds=10))
    with pytest.raises(ValueError, match=message):
        _result((first, second))


def test_guardrail_observation_must_match_its_resource_window() -> None:
    started = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)
    high_latency = _window(
        ordinal=1, started_at=started, business_latency="9.000"
    )
    declared_low = _guardrail(
        _window(ordinal=1, started_at=started, business_latency="3.500")
    )
    payload = declared_low.model_dump(mode="json")
    payload["window_sha256"] = high_latency.window_sha256
    payload["result_sha256"] = semantic_sha256(
        {key: value for key, value in payload.items() if key != "result_sha256"}
    )
    forged = AdCpuBusinessGuardrailResult.model_validate(payload)
    second = _window(ordinal=2, started_at=started + timedelta(seconds=10))

    with pytest.raises(ValueError, match="observed latency"):
        _result((high_latency, second), (forged, _guardrail(second)))


def test_public_projection_forbids_business_or_user_recovery_claims() -> None:
    started = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)
    result = _result(
        (
            _window(ordinal=1, started_at=started),
            _window(ordinal=2, started_at=started + timedelta(seconds=10)),
        )
    )

    public = build_public_ad_cpu_resource_recovery_projection(
        protocol=_protocol(), result=result
    )

    assert public["business_impact_observed"] is False
    assert public["user_visible_recovery_claimed"] is False
    assert public["business_sli_interpretation"] == "NON_REGRESSION_GUARDRAIL"
    assert public["terminal"] == "AD_CPU_RESOURCE_RECOVERY_PASS"
    assert public["resource_fault_observed"] is True
    assert public["resource_recovery_formula_id"] == (
        "BASELINE_PLUS_10PP_AND_90_PERCENT_FAULT_REDUCTION_V1"
    )
    assert public["resource_recovery_windows_required"] == 2
    assert public["service_health_passed"] is True
    assert public["endpoint_reachable"] is True
    assert public["baseline_flag_restored"] is True
    assert "business_sli_recovered" not in public
    assert "user_impact_recovered" not in public
    verify_public_ad_cpu_resource_recovery_projection(public)

    for forbidden_key in ("business_sli_recovered", "user_impact_recovered"):
        altered = {**public, forbidden_key: True}
        with pytest.raises(ValueError, match="Extra inputs"):
            verify_public_ad_cpu_resource_recovery_projection(altered)


@pytest.mark.parametrize(
    "wording",
    [
        "The business SLI recovered after mitigation.",
        "The business-SLI recovered.",
        "Customer impact recovered.",
        "Customer-impact recovered.",
        "The customer impact has recovered.",
        "User experience recovered.",
        "Service latency recovered from the CPU incident.",
        "Production incident resolved.",
    ],
)
def test_public_claim_verifier_rejects_impact_recovery_wording(wording: str) -> None:
    with pytest.raises(ValueError, match="forbidden impact recovery"):
        verify_public_ad_cpu_claim_text(wording)


def test_public_projection_reverifies_a_deserialized_recovery_result() -> None:
    started = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)
    valid = _result(
        (
            _window(ordinal=1, started_at=started),
            _window(ordinal=2, started_at=started + timedelta(seconds=10)),
        )
    )
    high_latency = _window(
        ordinal=1, started_at=started, business_latency="9.000"
    )
    low_guardrail = _guardrail(
        _window(ordinal=1, started_at=started, business_latency="3.500")
    )
    guardrail_payload = low_guardrail.model_dump(mode="json")
    guardrail_payload["window_sha256"] = high_latency.window_sha256
    guardrail_payload["result_sha256"] = semantic_sha256(
        {
            key: value
            for key, value in guardrail_payload.items()
            if key != "result_sha256"
        }
    )
    forged_guardrail = AdCpuBusinessGuardrailResult.model_validate(
        guardrail_payload
    )
    result_payload = valid.model_dump(mode="json")
    result_payload["windows"][0] = high_latency.model_dump(mode="json")
    result_payload["business_guardrails"][0] = forged_guardrail.model_dump(
        mode="json"
    )
    result_payload["windows"] = tuple(result_payload["windows"])
    result_payload["business_guardrails"] = tuple(
        result_payload["business_guardrails"]
    )
    result_payload["result_sha256"] = semantic_sha256(
        to_jsonable_python(
            {
                key: value
                for key, value in result_payload.items()
                if key != "result_sha256"
            }
        )
    )
    persisted = AdCpuResourceRecoveryResult.model_validate_json(
        json.dumps(result_payload)
    )

    with pytest.raises(ValueError, match="observed latency"):
        build_public_ad_cpu_resource_recovery_projection(
            protocol=_protocol(), result=persisted
        )


def test_only_fixed_ad_cpu_forward_step_is_admitted() -> None:
    admission = admit_ad_cpu_forward_step(
        logical_service="ad", selected_action="MITIGATE_CPU_SATURATION"
    )

    assert admission.admitted_step == "DISABLE_AD_HIGH_CPU_FLAG"
    assert admission.maximum_forward_steps == 1
    assert admission.generic_flag_write_admitted is False

    with pytest.raises(ValueError, match="exact owned"):
        admit_ad_cpu_forward_step(
            logical_service="email", selected_action="MITIGATE_CPU_SATURATION"
        )
    with pytest.raises(ValueError, match="allowlisted"):
        admit_ad_cpu_forward_step(
            logical_service="ad", selected_action="SET_FLAG"
        )
    with pytest.raises(ValueError, match="model-produced flag material"):
        admit_ad_cpu_forward_step(
            logical_service="ad",
            selected_action="MITIGATE_CPU_SATURATION",
            model_supplied_step="SET_FLAG",
            model_supplied_flag_key="adHighCpu",
            model_supplied_flag_value=False,
        )
