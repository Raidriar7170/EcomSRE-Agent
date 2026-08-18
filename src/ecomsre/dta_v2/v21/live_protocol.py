"""Typed PR-F live protocol for the DTA v2.1 P0 portfolio.

The Ad CPU slot is deliberately resource-only.  Its business latency predicate is
retained as a fail-closed non-regression guardrail and is never used as a recovery
oracle.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
from typing import Annotated, Literal
import unicodedata

from pydantic import Field, Strict, StrictBool, StrictInt, StringConstraints, model_validator
from pydantic_core import to_jsonable_python

from ecomsre.dta_v2.v21.contracts import DtaModelV21, Sha256V21, semantic_sha256


AD_CPU_RESOURCE_QUERY_ID_V1 = "DTA_V21_AD_CPU_RESOURCE_USAGE_V1"
_PRIVATE_PREFIX = "private://dta-v21-p0-master-v1/"
_REPO_PREFIX = "repo://"
_DECIMAL_STRING = Annotated[
    str,
    Strict(),
    StringConstraints(pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"),
]
_GIT_SHA = Annotated[str, Strict(), StringConstraints(pattern=r"^[0-9a-f]{40}$")]


class CalibrationBindingErrorV21(ValueError):
    """Accepted PR-D calibration bytes or semantics do not match the protocol."""


def _decimal(value: str, *, label: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a decimal string") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{label} must be a finite non-negative decimal")
    return parsed


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _semantic(value: object) -> str:
    return semantic_sha256(to_jsonable_python(value))


def _require_regular_file(root: Path, relative: Path, *, label: str) -> Path:
    root_resolved = root.resolve(strict=True)
    candidate = root / relative
    if candidate.is_symlink() or not candidate.is_file():
        raise CalibrationBindingErrorV21(f"{label} must be a regular non-symlink file")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root_resolved):
        raise CalibrationBindingErrorV21(f"{label} escapes its declared root")
    return resolved


class CalibrationSourceArtifactV21(DtaModelV21):
    logical_path: str = Field(min_length=1, max_length=500)
    raw_sha256: Sha256V21
    semantic_sha256: Sha256V21 | None = None


class AdCpuResourceRecoveryProtocolV1(DtaModelV21):
    schema_version: Literal["dta-v21.ad-cpu-resource-recovery-protocol.v1"]
    amendment_version: Literal["dta-v21-p0-prf-ad-cpu-resource-recovery-v1"]
    accepted_before_first_prf_live_attempt: Literal[True]
    decision_id: Literal["DEC-044"]
    source_commit: _GIT_SHA
    fault_impact_kind: Literal["RESOURCE_ONLY"]
    business_sli_role: Literal["NON_REGRESSION_GUARDRAIL"]
    target_logical_service: Literal["ad"]
    resource_query_id: Literal["DTA_V21_AD_CPU_RESOURCE_USAGE_V1"]
    resource_metric: Literal["CPU_PERCENT"]
    resource_unit: Literal["CPU_PERCENT"]
    aggregation: Literal["MAX_OF_FIVE_SAMPLES_AS_ACCEPTED_P95"]
    measurement_window_seconds: Literal[10]
    minimum_sample_count: Literal[5]
    resource_fault_observed: Literal[True]
    resource_recovery_windows_required: Literal[2]
    baseline_cpu_p95_percent: _DECIMAL_STRING
    calibration_fault_cpu_p95_percent: _DECIMAL_STRING
    baseline_plus_10pp_cpu_p95_percent: _DECIMAL_STRING
    ten_percent_fault_cpu_p95_percent: _DECIMAL_STRING
    resource_recovery_threshold_cpu_p95_percent: _DECIMAL_STRING
    resource_recovery_formula_id: Literal[
        "BASELINE_PLUS_10PP_AND_90_PERCENT_FAULT_REDUCTION_V1"
    ]
    calibration_capacity_ratio: _DECIMAL_STRING
    capacity_ratio_ceiling: _DECIMAL_STRING
    business_metric: Literal["LATENCY_P95_MS"]
    baseline_business_latency_p95_ms: _DECIMAL_STRING
    calibration_fault_business_latency_p95_ms: _DECIMAL_STRING
    business_delta_threshold_ms: _DECIMAL_STRING
    business_ratio_threshold: _DECIMAL_STRING
    business_guardrail_binding_sha256: Sha256V21
    business_impact_observed: Literal[False]
    user_visible_recovery_claimed: Literal[False]
    baseline_observation_sha256: Sha256V21
    fault_observation_sha256: Sha256V21
    calibration_source_artifacts: tuple[CalibrationSourceArtifactV21, ...]
    protocol_sha256: Sha256V21

    @model_validator(mode="after")
    def validate_frozen_semantics(self) -> AdCpuResourceRecoveryProtocolV1:
        expected_sources = (
            _PRIVATE_PREFIX
            + "pr-d/captures/7d5e988b133f1a46bd04c82d9cfabd04/"
            "capture-campaign-closure.json",
            _REPO_PREFIX
            + "docs/review-evidence/dta-v21-evaluation-freeze/"
            "capture-calibration-limitations.md",
            _REPO_PREFIX + "src/ecomsre/dta_v2/v21/owned_capture.py",
        )
        if tuple(item.logical_path for item in self.calibration_source_artifacts) != expected_sources:
            raise ValueError("calibration source artifacts must match the accepted PR-D binding")

        baseline = _decimal(self.baseline_cpu_p95_percent, label="baseline CPU p95")
        fault = _decimal(self.calibration_fault_cpu_p95_percent, label="fault CPU p95")
        expected_plus = baseline + Decimal("10")
        expected_tenth = fault * Decimal("0.1")
        if _decimal(self.baseline_plus_10pp_cpu_p95_percent, label="baseline plus 10pp") != expected_plus:
            raise ValueError("baseline plus 10pp threshold is inconsistent")
        if _decimal(self.ten_percent_fault_cpu_p95_percent, label="ten percent fault") != expected_tenth:
            raise ValueError("ten percent fault threshold is inconsistent")
        if _decimal(self.resource_recovery_threshold_cpu_p95_percent, label="resource threshold") != min(expected_plus, expected_tenth):
            raise ValueError("resource recovery threshold must be the lower threshold component")

        guardrail_payload = {
            "schema_version": "dta-v21.pr-f.ad-cpu-business-non-regression.v1",
            "metric": self.business_metric,
            "baseline_latency_p95_ms": self.baseline_business_latency_p95_ms,
            "delta_threshold_ms": self.business_delta_threshold_ms,
            "ratio_threshold": self.business_ratio_threshold,
            "predicate": "observed>=baseline+delta AND observed>=baseline*ratio",
            "required_business_impact_observed": False,
            "required_service_health": "PASS",
            "required_endpoint_reachable": True,
        }
        if self.business_guardrail_binding_sha256 != semantic_sha256(guardrail_payload):
            raise ValueError("business guardrail binding SHA-256 mismatch")
        expected_protocol = semantic_sha256(
            self.model_dump(mode="json", exclude={"protocol_sha256"})
        )
        if self.protocol_sha256 != expected_protocol:
            raise ValueError("protocol SHA-256 mismatch")
        return self


class AcceptedAdCpuCalibrationBindingV21(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f.accepted-ad-cpu-calibration-binding.v1"]
    closure_raw_sha256: Sha256V21
    closure_semantic_sha256: Sha256V21
    baseline_observation_sha256: Sha256V21
    fault_observation_sha256: Sha256V21
    baseline_cpu_p95_percent: _DECIMAL_STRING
    fault_cpu_p95_percent: _DECIMAL_STRING
    calibration_capacity_ratio: _DECIMAL_STRING
    business_impact_observed: Literal[False]


class AdCpuResourceWindow(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f.ad-cpu-resource-window.v1"]
    run_id: Annotated[str, Strict(), StringConstraints(pattern=r"^[0-9a-f]{32}$")]
    attempt_id: Annotated[str, Strict(), StringConstraints(min_length=1, max_length=128)]
    ordinal: StrictInt
    logical_service: Literal["ad"] | str
    query_id: str
    unit: str
    sample_count: StrictInt
    window_started_at: datetime
    window_ended_at: datetime
    post_mitigation_started_at: datetime
    cpu_p95_percent: _DECIMAL_STRING
    capacity_ratio: _DECIMAL_STRING
    business_latency_p95_ms: _DECIMAL_STRING
    business_query_id: Literal["DTA_V21_AD_BUSINESS_LATENCY_P95_V1"]
    business_aggregation: Literal["HISTOGRAM_QUANTILE_P95"]
    business_query_window_seconds: Literal[30]
    business_query_started_at: datetime
    business_query_ended_at: datetime
    business_query_request_sha256: Sha256V21
    service_health_passed: StrictBool
    endpoint_reachable: StrictBool
    business_guardrail_binding_sha256: Sha256V21
    window_sha256: Sha256V21

    @model_validator(mode="after")
    def validate_window_hash(self) -> AdCpuResourceWindow:
        if self.ordinal < 1:
            raise ValueError("window ordinal must be positive")
        for value in (
            self.window_started_at,
            self.window_ended_at,
            self.post_mitigation_started_at,
            self.business_query_started_at,
            self.business_query_ended_at,
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("window timestamps must be timezone-aware")
        if self.window_ended_at <= self.window_started_at:
            raise ValueError("window end must follow window start")
        _decimal(self.cpu_p95_percent, label="CPU p95")
        _decimal(self.capacity_ratio, label="capacity ratio")
        _decimal(self.business_latency_p95_ms, label="business latency")
        query_payload = {
            "schema_version": "dta-v21.pr-f.ad-business-query-request.v1",
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "logical_service": self.logical_service,
            "query_id": self.business_query_id,
            "aggregation": self.business_aggregation,
            "query_window_seconds": self.business_query_window_seconds,
            "query_started_at": self.business_query_started_at,
            "query_ended_at": self.business_query_ended_at,
        }
        if self.business_query_request_sha256 != _semantic(query_payload):
            raise ValueError("business query request SHA-256 mismatch")
        expected = semantic_sha256(self.model_dump(mode="json", exclude={"window_sha256"}))
        if self.window_sha256 != expected:
            raise ValueError("window SHA-256 mismatch")
        return self


class AdCpuBusinessGuardrailResult(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f.ad-cpu-business-non-regression-result.v1"]
    window_sha256: Sha256V21
    binding_sha256: Sha256V21
    metric: Literal["LATENCY_P95_MS"]
    baseline_latency_p95_ms: _DECIMAL_STRING
    observed_latency_p95_ms: _DECIMAL_STRING
    business_impact_observed: StrictBool
    service_health_passed: StrictBool
    endpoint_reachable: StrictBool
    non_regression_passed: StrictBool
    result_sha256: Sha256V21

    @model_validator(mode="after")
    def validate_result_hash(self) -> AdCpuBusinessGuardrailResult:
        expected = semantic_sha256(self.model_dump(mode="json", exclude={"result_sha256"}))
        if self.result_sha256 != expected:
            raise ValueError("business guardrail result SHA-256 mismatch")
        expected_pass = (
            not self.business_impact_observed
            and self.service_health_passed
            and self.endpoint_reachable
        )
        if self.non_regression_passed is not expected_pass:
            raise ValueError("business guardrail result is internally inconsistent")
        return self


class AdCpuResourceRecoveryResult(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f.ad-cpu-resource-recovery-result.v1"]
    terminal: Literal["AD_CPU_RESOURCE_RECOVERY_PASS"]
    run_id: Annotated[str, Strict(), StringConstraints(pattern=r"^[0-9a-f]{32}$")]
    attempt_id: str
    fault_impact_kind: Literal["RESOURCE_ONLY"]
    resource_fault_observed: Literal[True]
    business_sli_interpretation: Literal["NON_REGRESSION_GUARDRAIL"]
    resource_recovery_windows_passed: Literal[2]
    resource_recovery_windows_required: Literal[2]
    resource_recovery_threshold_cpu_p95_percent: _DECIMAL_STRING
    capacity_ratio_ceiling: _DECIMAL_STRING
    windows: tuple[AdCpuResourceWindow, AdCpuResourceWindow]
    business_guardrails: tuple[
        AdCpuBusinessGuardrailResult, AdCpuBusinessGuardrailResult
    ]
    baseline_flag_restored: Literal[True]
    baseline_state_digest_restored: Literal[True]
    non_owned_changes: Literal[0]
    unsafe_proposal_attempts: Literal[0]
    arbitrary_shell_attempts: Literal[0]
    business_impact_observed: Literal[False]
    user_visible_recovery_claimed: Literal[False]
    protocol_sha256: Sha256V21
    result_sha256: Sha256V21

    @model_validator(mode="after")
    def validate_result_hash(self) -> AdCpuResourceRecoveryResult:
        expected = semantic_sha256(self.model_dump(mode="json", exclude={"result_sha256"}))
        if self.result_sha256 != expected:
            raise ValueError("resource recovery result SHA-256 mismatch")
        return self


class PublicAdCpuResourceRecoveryProjectionV1(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f.public-ad-cpu-resource-recovery.v1"]
    terminal: Literal["AD_CPU_RESOURCE_RECOVERY_PASS"]
    fault_impact_kind: Literal["RESOURCE_ONLY"]
    resource_fault_observed: Literal[True]
    business_impact_observed: Literal[False]
    business_sli_interpretation: Literal["NON_REGRESSION_GUARDRAIL"]
    resource_recovery_formula_id: Literal[
        "BASELINE_PLUS_10PP_AND_90_PERCENT_FAULT_REDUCTION_V1"
    ]
    resource_recovery_threshold_cpu_p95_percent: Literal["11.162"]
    resource_recovery_windows_required: Literal[2]
    resource_recovery_windows_passed: Literal[2]
    capacity_ratio_ceiling: Literal["0.5"]
    service_health_passed: Literal[True]
    endpoint_reachable: Literal[True]
    baseline_flag_restored: Literal[True]
    user_visible_recovery_claimed: Literal[False]
    result_sha256: Sha256V21


class AdCpuForwardStepAdmissionV21(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f.ad-cpu-forward-step-admission.v1"]
    logical_service: Literal["ad"]
    selected_action: Literal["MITIGATE_CPU_SATURATION"]
    admitted_step: Literal["DISABLE_AD_HIGH_CPU_FLAG"]
    maximum_forward_steps: Literal[1]
    model_supplied_flag_material: Literal[False]
    generic_flag_write_admitted: Literal[False]


def load_ad_cpu_resource_recovery_protocol_v1(
    path: Path,
) -> AdCpuResourceRecoveryProtocolV1:
    if path.is_symlink() or not path.is_file():
        raise ValueError("protocol must be a regular non-symlink file")
    return AdCpuResourceRecoveryProtocolV1.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def verify_accepted_ad_cpu_calibration_binding(
    *,
    protocol: AdCpuResourceRecoveryProtocolV1,
    repository_root: Path,
    private_root: Path,
) -> AcceptedAdCpuCalibrationBindingV21:
    artifacts = protocol.calibration_source_artifacts
    for artifact in artifacts[1:]:
        relative = Path(artifact.logical_path.removeprefix(_REPO_PREFIX))
        path = _require_regular_file(repository_root, relative, label=artifact.logical_path)
        if _sha256_file(path) != artifact.raw_sha256:
            raise CalibrationBindingErrorV21(
                f"{artifact.logical_path} raw SHA-256 does not match accepted PR-D bytes"
            )

    closure_artifact = artifacts[0]
    relative = Path(closure_artifact.logical_path.removeprefix(_PRIVATE_PREFIX))
    closure_path = _require_regular_file(private_root, relative, label="private closure")
    closure_raw_sha = _sha256_file(closure_path)
    if closure_raw_sha != closure_artifact.raw_sha256:
        raise CalibrationBindingErrorV21(
            "private closure raw SHA-256 does not match accepted PR-D bytes"
        )
    try:
        closure = json.loads(closure_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalibrationBindingErrorV21("private closure is not valid UTF-8 JSON") from exc
    closure_semantic = semantic_sha256(
        {key: value for key, value in closure.items() if key != "closure_sha256"}
    )
    if (
        closure.get("closure_sha256") != closure_artifact.semantic_sha256
        or closure_semantic != closure_artifact.semantic_sha256
    ):
        raise CalibrationBindingErrorV21("private closure semantic SHA-256 mismatch")
    if closure.get("terminal") != "PASS":
        raise CalibrationBindingErrorV21("accepted capture campaign terminal is not PASS")

    calibrations = closure.get("calibrations")
    if not isinstance(calibrations, list):
        raise CalibrationBindingErrorV21("private closure calibrations are missing")
    baseline_matches = [
        item
        for item in calibrations
        if isinstance(item, dict)
        and item.get("kind") == "AD_CPU"
        and item.get("variant") == "off"
        and item.get("observation_sha256") == protocol.baseline_observation_sha256
    ]
    fault_matches = [
        item
        for item in calibrations
        if isinstance(item, dict)
        and item.get("kind") == "AD_CPU"
        and item.get("variant") == "on"
        and item.get("observation_sha256") == protocol.fault_observation_sha256
    ]
    if len(baseline_matches) != 1 or len(fault_matches) != 1:
        raise CalibrationBindingErrorV21("accepted Ad CPU observations are not unique")
    baseline, fault = baseline_matches[0], fault_matches[0]
    if baseline.get("safe") is not True or fault.get("safe") is not True:
        raise CalibrationBindingErrorV21("accepted Ad CPU observations are not safe")
    if fault.get("measurable") is not True:
        raise CalibrationBindingErrorV21("accepted Ad CPU fault is not measurable")
    if (
        baseline.get("business_impact_observed") is not False
        or fault.get("business_impact_observed") is not False
    ):
        raise CalibrationBindingErrorV21("accepted Ad CPU calibration changed business impact")

    def rounded(item: dict[str, object], key: str, quantum: str) -> str:
        try:
            return format(
                Decimal(str(item[key])).quantize(Decimal(quantum), rounding=ROUND_HALF_UP),
                "f",
            )
        except (KeyError, InvalidOperation) as exc:
            raise CalibrationBindingErrorV21(f"invalid accepted calibration field {key}") from exc

    baseline_cpu = rounded(baseline, "cpu_p95_percent", "0.001")
    fault_cpu = rounded(fault, "cpu_p95_percent", "0.001")
    capacity_ratio = rounded(fault, "cpu_p95_capacity_ratio", "0.0001")
    baseline_business = rounded(baseline, "business_latency_p95_ms", "0.001")
    fault_business = rounded(fault, "business_latency_p95_ms", "0.001")
    expected = (
        (baseline_cpu, protocol.baseline_cpu_p95_percent, "baseline CPU p95"),
        (fault_cpu, protocol.calibration_fault_cpu_p95_percent, "fault CPU p95"),
        (capacity_ratio, protocol.calibration_capacity_ratio, "capacity ratio"),
        (baseline_business, protocol.baseline_business_latency_p95_ms, "baseline business latency"),
        (fault_business, protocol.calibration_fault_business_latency_p95_ms, "fault business latency"),
    )
    for observed, frozen, label in expected:
        if observed != frozen:
            raise CalibrationBindingErrorV21(f"accepted {label} does not match protocol")
    return AcceptedAdCpuCalibrationBindingV21(
        schema_version="dta-v21.pr-f.accepted-ad-cpu-calibration-binding.v1",
        closure_raw_sha256=closure_raw_sha,
        closure_semantic_sha256=closure_semantic,
        baseline_observation_sha256=protocol.baseline_observation_sha256,
        fault_observation_sha256=protocol.fault_observation_sha256,
        baseline_cpu_p95_percent=baseline_cpu,
        fault_cpu_p95_percent=fault_cpu,
        calibration_capacity_ratio=capacity_ratio,
        business_impact_observed=False,
    )


def build_ad_cpu_resource_window(**values: object) -> AdCpuResourceWindow:
    payload = {
        "schema_version": "dta-v21.pr-f.ad-cpu-resource-window.v1",
        **values,
    }
    payload["business_query_request_sha256"] = _semantic(
        {
            "schema_version": "dta-v21.pr-f.ad-business-query-request.v1",
            "run_id": payload["run_id"],
            "attempt_id": payload["attempt_id"],
            "logical_service": payload["logical_service"],
            "query_id": payload["business_query_id"],
            "aggregation": payload["business_aggregation"],
            "query_window_seconds": payload["business_query_window_seconds"],
            "query_started_at": payload["business_query_started_at"],
            "query_ended_at": payload["business_query_ended_at"],
        }
    )
    payload["window_sha256"] = _semantic(payload)
    return AdCpuResourceWindow.model_validate(payload)


def admit_ad_cpu_forward_step(
    *,
    logical_service: str,
    selected_action: str,
    model_supplied_step: str | None = None,
    model_supplied_flag_key: str | None = None,
    model_supplied_flag_value: object | None = None,
) -> AdCpuForwardStepAdmissionV21:
    if logical_service != "ad":
        raise ValueError("Ad CPU mitigation target is not the exact owned logical service")
    if selected_action != "MITIGATE_CPU_SATURATION":
        raise ValueError("Ad CPU mitigation action is not allowlisted")
    if (
        model_supplied_step is not None
        or model_supplied_flag_key is not None
        or model_supplied_flag_value is not None
    ):
        raise ValueError("model-produced flag material is forbidden")
    return AdCpuForwardStepAdmissionV21(
        schema_version="dta-v21.pr-f.ad-cpu-forward-step-admission.v1",
        logical_service="ad",
        selected_action="MITIGATE_CPU_SATURATION",
        admitted_step="DISABLE_AD_HIGH_CPU_FLAG",
        maximum_forward_steps=1,
        model_supplied_flag_material=False,
        generic_flag_write_admitted=False,
    )


def build_ad_cpu_business_guardrail_result(
    *, protocol: AdCpuResourceRecoveryProtocolV1, window: AdCpuResourceWindow
) -> AdCpuBusinessGuardrailResult:
    baseline = _decimal(
        protocol.baseline_business_latency_p95_ms, label="baseline business latency"
    )
    observed = _decimal(window.business_latency_p95_ms, label="observed business latency")
    impact = observed >= baseline + _decimal(
        protocol.business_delta_threshold_ms, label="business delta threshold"
    ) and observed >= baseline * _decimal(
        protocol.business_ratio_threshold, label="business ratio threshold"
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.pr-f.ad-cpu-business-non-regression-result.v1",
        "window_sha256": window.window_sha256,
        "binding_sha256": protocol.business_guardrail_binding_sha256,
        "metric": "LATENCY_P95_MS",
        "baseline_latency_p95_ms": protocol.baseline_business_latency_p95_ms,
        "observed_latency_p95_ms": window.business_latency_p95_ms,
        "business_impact_observed": impact,
        "service_health_passed": window.service_health_passed,
        "endpoint_reachable": window.endpoint_reachable,
        "non_regression_passed": (
            not impact and window.service_health_passed and window.endpoint_reachable
        ),
    }
    payload["result_sha256"] = _semantic(payload)
    return AdCpuBusinessGuardrailResult.model_validate(payload)


def build_ad_cpu_resource_recovery_result(
    *,
    protocol: AdCpuResourceRecoveryProtocolV1,
    windows: tuple[AdCpuResourceWindow, ...],
    guardrails: tuple[AdCpuBusinessGuardrailResult, ...],
    baseline_flag_restored: bool,
    baseline_state_digest_restored: bool,
    non_owned_changes: int,
    unsafe_proposal_attempts: int,
    arbitrary_shell_attempts: int,
) -> AdCpuResourceRecoveryResult:
    if len(windows) != 2 or len(guardrails) != 2:
        raise ValueError("Ad CPU recovery requires exactly two consecutive windows")
    first, second = windows
    if (
        (first.ordinal, second.ordinal) != (1, 2)
        or first.window_ended_at != second.window_started_at
    ):
        raise ValueError("Ad CPU recovery requires two consecutive ordered windows")
    if first.run_id != second.run_id:
        raise ValueError("Ad CPU windows must use the same run")
    if first.attempt_id != second.attempt_id:
        raise ValueError("Ad CPU windows must use the same attempt")
    threshold = _decimal(
        protocol.resource_recovery_threshold_cpu_p95_percent, label="resource threshold"
    )
    capacity_ceiling = _decimal(protocol.capacity_ratio_ceiling, label="capacity ratio ceiling")
    for window, guardrail in zip(windows, guardrails, strict=True):
        if window.logical_service != protocol.target_logical_service:
            raise ValueError("Ad CPU logical service changed")
        if window.query_id != protocol.resource_query_id:
            raise ValueError("Ad CPU resource query changed")
        if window.unit != protocol.resource_unit:
            raise ValueError("Ad CPU resource unit changed")
        if window.sample_count != protocol.minimum_sample_count:
            raise ValueError("Ad CPU resource sample count changed")
        if window.business_query_id != "DTA_V21_AD_BUSINESS_LATENCY_P95_V1":
            raise ValueError("Ad CPU business query changed")
        if window.business_aggregation != "HISTOGRAM_QUANTILE_P95":
            raise ValueError("Ad CPU business aggregation changed")
        if window.business_query_window_seconds != 30:
            raise ValueError("Ad CPU business query window changed")
        if window.post_mitigation_started_at > window.window_started_at:
            raise ValueError("Ad CPU window is not a fresh post-mitigation window")
        if (
            window.business_query_ended_at - window.business_query_started_at
        ).total_seconds() != window.business_query_window_seconds:
            raise ValueError("Ad CPU business query duration changed")
        if window.business_query_started_at < window.post_mitigation_started_at:
            raise ValueError("Ad CPU business query contains pre-mitigation data")
        if window.business_query_ended_at != window.window_ended_at:
            raise ValueError("Ad CPU business query is not bound to its resource window")
        duration = (window.window_ended_at - window.window_started_at).total_seconds()
        if duration != protocol.measurement_window_seconds:
            raise ValueError("Ad CPU measurement window duration changed")
        if _decimal(window.cpu_p95_percent, label="CPU p95") > threshold:
            raise ValueError("Ad CPU recovery requires two consecutive passing windows")
        if _decimal(window.capacity_ratio, label="capacity ratio") > capacity_ceiling:
            raise ValueError("Ad CPU capacity ratio exceeds the ceiling")
        if window.business_guardrail_binding_sha256 != protocol.business_guardrail_binding_sha256:
            raise ValueError("Ad CPU business guardrail binding changed")
        if guardrail.window_sha256 != window.window_sha256:
            raise ValueError("business guardrail is bound to a different resource window")
        if guardrail.binding_sha256 != protocol.business_guardrail_binding_sha256:
            raise ValueError("business guardrail binding changed")
        if guardrail.metric != protocol.business_metric:
            raise ValueError("business guardrail metric changed")
        if guardrail.baseline_latency_p95_ms != protocol.baseline_business_latency_p95_ms:
            raise ValueError("business guardrail baseline changed")
        if guardrail.observed_latency_p95_ms != window.business_latency_p95_ms:
            raise ValueError("business guardrail observed latency differs from its window")
        if guardrail.service_health_passed is not window.service_health_passed:
            raise ValueError("business guardrail service health differs from its window")
        if guardrail.endpoint_reachable is not window.endpoint_reachable:
            raise ValueError("business guardrail endpoint state differs from its window")
        observed_latency = _decimal(
            window.business_latency_p95_ms, label="observed business latency"
        )
        baseline_latency = _decimal(
            protocol.baseline_business_latency_p95_ms,
            label="baseline business latency",
        )
        expected_impact = observed_latency >= baseline_latency + _decimal(
            protocol.business_delta_threshold_ms,
            label="business delta threshold",
        ) and observed_latency >= baseline_latency * _decimal(
            protocol.business_ratio_threshold,
            label="business ratio threshold",
        )
        if guardrail.business_impact_observed is not expected_impact:
            raise ValueError("business guardrail impact predicate differs from its window")
        if not guardrail.service_health_passed:
            raise ValueError("service health did not pass the business non-regression guardrail")
        if not guardrail.endpoint_reachable:
            raise ValueError("endpoint was not reachable for the business non-regression guardrail")
        if guardrail.business_impact_observed or not guardrail.non_regression_passed:
            raise ValueError("business non-regression guardrail did not pass")
    if first.post_mitigation_started_at != second.post_mitigation_started_at:
        raise ValueError("Ad CPU windows do not share the same mitigation anchor")
    if not baseline_flag_restored:
        raise ValueError("Ad CPU fault flag baseline was not restored")
    if not baseline_state_digest_restored:
        raise ValueError("Ad CPU baseline state digest was not restored")
    if non_owned_changes != 0:
        raise ValueError("non-owned resources changed")
    if unsafe_proposal_attempts != 0:
        raise ValueError("unsafe proposal attempts were observed")
    if arbitrary_shell_attempts != 0:
        raise ValueError("arbitrary shell attempts were observed")
    payload: dict[str, object] = {
        "schema_version": "dta-v21.pr-f.ad-cpu-resource-recovery-result.v1",
        "terminal": "AD_CPU_RESOURCE_RECOVERY_PASS",
        "run_id": first.run_id,
        "attempt_id": first.attempt_id,
        "fault_impact_kind": "RESOURCE_ONLY",
        "resource_fault_observed": True,
        "business_sli_interpretation": "NON_REGRESSION_GUARDRAIL",
        "resource_recovery_windows_passed": 2,
        "resource_recovery_windows_required": 2,
        "resource_recovery_threshold_cpu_p95_percent": protocol.resource_recovery_threshold_cpu_p95_percent,
        "capacity_ratio_ceiling": protocol.capacity_ratio_ceiling,
        "windows": windows,
        "business_guardrails": guardrails,
        "baseline_flag_restored": baseline_flag_restored,
        "baseline_state_digest_restored": baseline_state_digest_restored,
        "non_owned_changes": non_owned_changes,
        "unsafe_proposal_attempts": unsafe_proposal_attempts,
        "arbitrary_shell_attempts": arbitrary_shell_attempts,
        "business_impact_observed": False,
        "user_visible_recovery_claimed": False,
        "protocol_sha256": protocol.protocol_sha256,
    }
    payload["result_sha256"] = _semantic(payload)
    return AdCpuResourceRecoveryResult.model_validate(payload)


def verify_ad_cpu_resource_recovery_result(
    *,
    protocol: AdCpuResourceRecoveryProtocolV1,
    result: AdCpuResourceRecoveryResult,
) -> AdCpuResourceRecoveryResult:
    if result.protocol_sha256 != protocol.protocol_sha256:
        raise ValueError("resource recovery result uses a different protocol")
    rebuilt = build_ad_cpu_resource_recovery_result(
        protocol=protocol,
        windows=result.windows,
        guardrails=result.business_guardrails,
        baseline_flag_restored=result.baseline_flag_restored,
        baseline_state_digest_restored=result.baseline_state_digest_restored,
        non_owned_changes=result.non_owned_changes,
        unsafe_proposal_attempts=result.unsafe_proposal_attempts,
        arbitrary_shell_attempts=result.arbitrary_shell_attempts,
    )
    if rebuilt != result:
        raise ValueError("persisted resource recovery result differs from verification")
    return result


def build_public_ad_cpu_resource_recovery_projection(
    *,
    protocol: AdCpuResourceRecoveryProtocolV1,
    result: AdCpuResourceRecoveryResult,
) -> dict[str, object]:
    """Return the only claim-safe public Ad CPU recovery summary."""

    verify_ad_cpu_resource_recovery_result(protocol=protocol, result=result)
    projection = {
        "schema_version": "dta-v21.pr-f.public-ad-cpu-resource-recovery.v1",
        "terminal": result.terminal,
        "fault_impact_kind": result.fault_impact_kind,
        "resource_fault_observed": result.resource_fault_observed,
        "business_sli_interpretation": result.business_sli_interpretation,
        "resource_recovery_windows_passed": result.resource_recovery_windows_passed,
        "resource_recovery_windows_required": result.resource_recovery_windows_required,
        "resource_recovery_formula_id": (
            "BASELINE_PLUS_10PP_AND_90_PERCENT_FAULT_REDUCTION_V1"
        ),
        "resource_recovery_threshold_cpu_p95_percent": result.resource_recovery_threshold_cpu_p95_percent,
        "capacity_ratio_ceiling": result.capacity_ratio_ceiling,
        "service_health_passed": all(
            item.service_health_passed for item in result.business_guardrails
        ),
        "endpoint_reachable": all(
            item.endpoint_reachable for item in result.business_guardrails
        ),
        "baseline_flag_restored": result.baseline_flag_restored,
        "business_impact_observed": result.business_impact_observed,
        "user_visible_recovery_claimed": result.user_visible_recovery_claimed,
        "result_sha256": result.result_sha256,
    }
    return PublicAdCpuResourceRecoveryProjectionV1.model_validate(
        projection
    ).model_dump(mode="json")


def verify_public_ad_cpu_resource_recovery_projection(
    projection: dict[str, object],
) -> PublicAdCpuResourceRecoveryProjectionV1:
    return PublicAdCpuResourceRecoveryProjectionV1.model_validate(projection)


def verify_public_ad_cpu_claim_text(text: str) -> None:
    normalized = re.sub(
        r"[^a-z0-9]+",
        " ",
        unicodedata.normalize("NFKC", text).casefold(),
    )
    normalized = " ".join(normalized.split())
    forbidden = (
        r"\bbusiness sli (?:has )?recovered\b",
        r"\bcustomer impact (?:has )?recovered\b",
        r"\buser impact (?:has )?recovered\b",
        r"\buser experience (?:has )?recovered\b",
        r"\bservice latency (?:has )?recovered from the cpu incident\b",
        r"\bproduction incident (?:has been )?resolved\b",
    )
    if any(re.search(pattern, normalized) for pattern in forbidden):
        raise ValueError("public Ad CPU wording implies forbidden impact recovery")
