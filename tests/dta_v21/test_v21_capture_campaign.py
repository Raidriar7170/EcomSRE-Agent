from __future__ import annotations

from collections import Counter

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v21.capture_campaign import (
    CalibrationKindV21,
    CaptureCalibrationFailureV21,
    CaptureCalibrationObservationV21,
    CaptureConditionV21,
    CaptureFailureCodeV21,
    CaptureTerminalV21,
    CapturedCaseArtifactV21,
    OperationalFamilyV21,
    build_default_capture_plan_v21,
    run_capture_campaign_attempt_v21,
)
from ecomsre.dta_v2.v21.contracts import semantic_sha256
from ecomsre.dta_v2.v21.evaluation_contracts import EvaluationSplitV21


BASE_HEAD = "c0a541fec48f11b02dc2cd6ba41673a777e55eee"


def test_default_capture_plan_freezes_required_12_plus_8_matrix() -> None:
    plan = build_default_capture_plan_v21(base_head=BASE_HEAD)

    assert len(plan.cases) == 20
    assert Counter(item.split for item in plan.cases) == {
        EvaluationSplitV21.DEVELOPMENT: 12,
        EvaluationSplitV21.HELD_OUT: 8,
    }
    development = Counter(
        item.operational_family
        for item in plan.cases
        if item.split is EvaluationSplitV21.DEVELOPMENT
    )
    assert development == {
        OperationalFamilyV21.PAYMENT_CONFIGURATION: 2,
        OperationalFamilyV21.EMAIL_MEMORY_LEAK: 2,
        OperationalFamilyV21.RECOMMENDATION_UNAVAILABLE: 1,
        OperationalFamilyV21.AD_CPU_SATURATION: 2,
        OperationalFamilyV21.EMAIL_UNAVAILABLE: 1,
        OperationalFamilyV21.PRODUCT_CATALOG_UNAVAILABLE: 1,
        OperationalFamilyV21.SHIPPING_DEPENDENCY_LATENCY: 1,
        OperationalFamilyV21.NO_FAULT: 1,
        OperationalFamilyV21.MISSING_CONFLICTING_EVIDENCE: 1,
    }
    held_out = Counter(
        item.operational_family
        for item in plan.cases
        if item.split is EvaluationSplitV21.HELD_OUT
    )
    assert held_out == {
        OperationalFamilyV21.PAYMENT_CONFIGURATION: 1,
        OperationalFamilyV21.EMAIL_MEMORY_LEAK: 1,
        OperationalFamilyV21.AD_CPU_SATURATION: 1,
        OperationalFamilyV21.EMAIL_UNAVAILABLE: 1,
        OperationalFamilyV21.PRODUCT_CATALOG_UNAVAILABLE: 1,
        OperationalFamilyV21.SHIPPING_DEPENDENCY_LATENCY: 1,
        OperationalFamilyV21.NO_FAULT: 1,
        OperationalFamilyV21.MISSING_CONFLICTING_EVIDENCE: 1,
    }


def test_held_out_conditions_are_not_identity_only_variants() -> None:
    plan = build_default_capture_plan_v21(base_head=BASE_HEAD)

    for held_out in (
        item for item in plan.cases if item.split is EvaluationSplitV21.HELD_OUT
    ):
        same_family_development = tuple(
            item
            for item in plan.cases
            if item.split is EvaluationSplitV21.DEVELOPMENT
            and item.operational_family is held_out.operational_family
        )
        assert same_family_development
        assert all(
            item.condition_signature != held_out.condition_signature
            for item in same_family_development
        )


def test_capture_plan_rejects_undeclared_fault_variant() -> None:
    plan = build_default_capture_plan_v21(base_head=BASE_HEAD)
    case = next(
        item for item in plan.cases if item.condition is CaptureConditionV21.AD_HIGH_CPU
    )

    with pytest.raises(ValueError, match="Ad CPU variant"):
        type(case).model_validate(
            {**case.model_dump(mode="python"), "fault_variant": "turbo"}
        )


def test_ad_cpu_calibration_accepts_multicore_percent_and_binds_host_ratio() -> None:
    payload = {
        "schema_version": "dta-v21.capture-calibration-observation.v1",
        "kind": CalibrationKindV21.AD_CPU,
        "target_service": "ad",
        "variant": "on",
        "maximum_memory_bytes": None,
        "memory_delta_bytes": None,
        "memory_slope_bytes_per_second": None,
        "cpu_p50_percent": 180.0,
        "cpu_p95_percent": 240.0,
        "cpu_capacity_percent": 1200.0,
        "cpu_p95_capacity_ratio": 0.2,
        "cpu_safety_ceiling_ratio": 0.5,
        "business_error_rate": None,
        "business_latency_p95_ms": 20.0,
        "business_impact_observed": False,
        "attributable_trace_latency_ms": None,
        "target_runtime_stopped": None,
        "safe": True,
        "measurable": True,
    }
    observation = CaptureCalibrationObservationV21.model_validate(
        {**payload, "observation_sha256": semantic_sha256(payload)}
    )

    assert observation.cpu_p95_percent == 240.0
    assert observation.cpu_p95_capacity_ratio == 0.2
    with pytest.raises(ValueError, match="host-capacity ratio"):
        CaptureCalibrationObservationV21.model_validate(
            {
                **payload,
                "cpu_p95_capacity_ratio": 0.3,
                "observation_sha256": semantic_sha256(
                    {**payload, "cpu_p95_capacity_ratio": 0.3}
                ),
            }
        )


def _calibration(kind, service, variant):
    payload = {
        "schema_version": "dta-v21.capture-calibration-observation.v1",
        "kind": kind,
        "target_service": service,
        "variant": variant,
        "maximum_memory_bytes": 100_000_000
        if kind is CalibrationKindV21.EMAIL_MEMORY
        else None,
        "memory_delta_bytes": 2_000_000
        if kind is CalibrationKindV21.EMAIL_MEMORY
        else None,
        "memory_slope_bytes_per_second": 200_000.0
        if kind is CalibrationKindV21.EMAIL_MEMORY
        else None,
        "cpu_p50_percent": 30.0 if kind is CalibrationKindV21.AD_CPU else None,
        "cpu_p95_percent": (90.0 if variant == "on" else 35.0)
        if kind is CalibrationKindV21.AD_CPU
        else None,
        "business_error_rate": 0.5
        if kind is CalibrationKindV21.SERVICE_UNAVAILABLE
        else None,
        "business_latency_p95_ms": 500.0
        if kind in (CalibrationKindV21.AD_CPU, CalibrationKindV21.SHIPPING_LATENCY)
        else None,
        "business_impact_observed": True
        if kind is CalibrationKindV21.SERVICE_UNAVAILABLE
        else None,
        "attributable_trace_latency_ms": 400.0
        if kind is CalibrationKindV21.SHIPPING_LATENCY
        else None,
        "target_runtime_stopped": True
        if kind is CalibrationKindV21.SERVICE_UNAVAILABLE
        else None,
        "safe": True,
        "measurable": variant not in ("off", "10x"),
    }
    return CaptureCalibrationObservationV21.model_validate(
        {**payload, "observation_sha256": semantic_sha256(payload)}
    )


class _FakeCaptureLifecycleV21:
    def __init__(
        self, *, fail_case: str | None = None, fail_ad_calibration: bool = False
    ) -> None:
        self.fail_case = fail_case
        self.fail_ad_calibration = fail_ad_calibration
        self.active = False
        self.restores = 0
        self.verifications = 0

    def admit(self) -> None:
        pass

    def start(self) -> None:
        pass

    def wait_ready(self) -> None:
        pass

    def verify_baseline(self) -> None:
        assert not self.active
        self.verifications += 1

    def calibrate(self, *, kind, target_service, variant):
        assert not self.active
        self.active = True
        if (
            self.fail_ad_calibration
            and kind is CalibrationKindV21.AD_CPU
            and variant == "on"
        ):
            raise CaptureCalibrationFailureV21(
                kind=kind,
                target_service=target_service,
                variant=variant,
                step="BUSINESS_METRIC",
                cause=RuntimeError("private dynamic failure"),
            )
        return _calibration(kind, target_service, variant)

    def apply_case(self, case, *, selected_email_variant, selected_shipping_variant):
        assert selected_email_variant == "1000x"
        assert selected_shipping_variant == "5sec"
        assert not self.active
        self.active = True

    def capture_case(self, case):
        if case.case_id == self.fail_case:
            raise RuntimeError("typed fake capture failure")
        return CapturedCaseArtifactV21(
            case_id=case.case_id,
            case_sha256=semantic_sha256({"case": case.case_id}),
            truth_sha256=semantic_sha256({"truth": case.case_id}),
            fault_operation_count=(
                0
                if case.condition
                in (
                    CaptureConditionV21.NO_FAULT,
                    CaptureConditionV21.SOURCE_PARTIAL_FAILURE,
                )
                else 1
            ),
        )

    def restore_baseline(self) -> None:
        assert self.active
        self.active = False
        self.restores += 1

    def cleanup(self, *, baseline_restored):
        assert baseline_restored
        return {
            "verdict": "CLEAN",
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
        }


def test_capture_orchestrator_calibrates_restores_each_case_and_closes_clean() -> None:
    lifecycle = _FakeCaptureLifecycleV21()
    closure = run_capture_campaign_attempt_v21(
        plan=build_default_capture_plan_v21(base_head=BASE_HEAD),
        lifecycle=lifecycle,
    )

    assert closure.terminal is CaptureTerminalV21.PASS
    assert closure.failure_code is None
    assert closure.selected_email_variant == "1000x"
    assert closure.selected_ad_cpu_variant == "on"
    assert closure.selected_shipping_variant == "5sec"
    assert len(closure.calibrations) == 10
    assert len(closure.case_receipts) == 20
    assert lifecycle.restores == 30
    assert all(
        item.agent_calls == item.provider_calls == 0 for item in closure.case_receipts
    )


def test_capture_failure_restores_before_clean_owned_shutdown() -> None:
    lifecycle = _FakeCaptureLifecycleV21(fail_case="dta21-case-004")
    closure = run_capture_campaign_attempt_v21(
        plan=build_default_capture_plan_v21(base_head=BASE_HEAD),
        lifecycle=lifecycle,
    )

    assert closure.terminal is CaptureTerminalV21.BLOCKED
    assert closure.failure_code is CaptureFailureCodeV21.CASE_CAPTURE_FAILED
    assert closure.failed_case_id == "dta21-case-004"
    assert closure.baseline_restored
    assert closure.cleanup_verdict == "CLEAN"


def test_calibration_failure_records_only_typed_stage_and_detail_hash() -> None:
    lifecycle = _FakeCaptureLifecycleV21(fail_ad_calibration=True)
    closure = run_capture_campaign_attempt_v21(
        plan=build_default_capture_plan_v21(base_head=BASE_HEAD),
        lifecycle=lifecycle,
    )

    assert closure.terminal is CaptureTerminalV21.BLOCKED
    assert closure.failure_code is CaptureFailureCodeV21.CALIBRATION_FAILED
    assert closure.failure_stage == "CALIBRATION:AD_CPU:ad:on:BUSINESS_METRIC"
    assert closure.failure_cause_type == "RuntimeError"
    assert closure.failure_detail_sha256
    assert "private dynamic failure" not in closure.model_dump_json()
    assert closure.baseline_restored
    assert closure.cleanup_verdict == "CLEAN"


def test_calibration_validation_error_retains_safe_field_codes() -> None:
    with pytest.raises(ValidationError) as captured:
        CaptureCalibrationObservationV21.model_validate({})

    failure = CaptureCalibrationFailureV21(
        kind=CalibrationKindV21.AD_CPU,
        target_service="ad",
        variant="on",
        step="BUSINESS_METRIC",
        cause=captured.value,
    )

    assert failure.validation_codes
    assert all(":" in item for item in failure.validation_codes)
    assert "input" not in " ".join(failure.validation_codes).casefold()
