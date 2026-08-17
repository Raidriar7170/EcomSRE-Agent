"""Frozen PR-D owned-capture plan and calibration contracts."""

from __future__ import annotations

from collections import Counter
from enum import Enum
from typing import Any, Literal, Protocol, cast

from pydantic import Field, StrictFloat, StrictInt, ValidationError, model_validator

from ecomsre.dta_v2.tool_contracts import ToolName
from ecomsre.dta_v2.v21.contracts import DtaModelV21, Sha256V21, semantic_sha256
from ecomsre.dta_v2.v21.evaluation_contracts import (
    EvaluationSplitV21,
    ScenarioFamilyV21,
)


class OperationalFamilyV21(str, Enum):
    PAYMENT_CONFIGURATION = "PAYMENT_CONFIGURATION"
    EMAIL_MEMORY_LEAK = "EMAIL_MEMORY_LEAK"
    RECOMMENDATION_UNAVAILABLE = "RECOMMENDATION_UNAVAILABLE"
    AD_CPU_SATURATION = "AD_CPU_SATURATION"
    EMAIL_UNAVAILABLE = "EMAIL_UNAVAILABLE"
    PRODUCT_CATALOG_UNAVAILABLE = "PRODUCT_CATALOG_UNAVAILABLE"
    SHIPPING_DEPENDENCY_LATENCY = "SHIPPING_DEPENDENCY_LATENCY"
    NO_FAULT = "NO_FAULT"
    MISSING_CONFLICTING_EVIDENCE = "MISSING_CONFLICTING_EVIDENCE"


class CaptureConditionV21(str, Enum):
    PAYMENT_FLAG = "PAYMENT_FLAG"
    EMAIL_MEMORY_LEAK = "EMAIL_MEMORY_LEAK"
    RECOMMENDATION_STOP = "RECOMMENDATION_STOP"
    AD_HIGH_CPU = "AD_HIGH_CPU"
    EMAIL_STOP = "EMAIL_STOP"
    PRODUCT_CATALOG_STOP = "PRODUCT_CATALOG_STOP"
    SHIPPING_SLOWDOWN = "SHIPPING_SLOWDOWN"
    NO_FAULT = "NO_FAULT"
    RECOVERY_TRANSITION = "RECOVERY_TRANSITION"
    SOURCE_PARTIAL_FAILURE = "SOURCE_PARTIAL_FAILURE"


class CaptureTerminalV21(str, Enum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"


class CaptureFailureCodeV21(str, Enum):
    ADMISSION_FAILED = "ADMISSION_FAILED"
    START_FAILED = "START_FAILED"
    CALIBRATION_FAILED = "CALIBRATION_FAILED"
    CASE_CAPTURE_FAILED = "CASE_CAPTURE_FAILED"
    BASELINE_RESTORE_FAILED = "BASELINE_RESTORE_FAILED"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    CLEANUP_BLOCKED = "CLEANUP_BLOCKED"


class CalibrationKindV21(str, Enum):
    EMAIL_MEMORY = "EMAIL_MEMORY"
    AD_CPU = "AD_CPU"
    SHIPPING_LATENCY = "SHIPPING_LATENCY"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


class CaptureCalibrationFailureV21(RuntimeError):
    """Sanitized typed calibration failure propagated across the lifecycle."""

    def __init__(
        self,
        *,
        kind: CalibrationKindV21,
        target_service: str,
        variant: str,
        step: str,
        cause: Exception,
    ) -> None:
        super().__init__("capture calibration step failed")
        self.stage = f"CALIBRATION:{kind.value}:{target_service}:{variant}:{step}"
        self.cause_type = type(cause).__name__
        self.detail_sha256 = semantic_sha256(
            {
                "stage": self.stage,
                "cause_type": self.cause_type,
                "cause_message": str(cause),
            }
        )
        self.validation_codes = (
            tuple(
                sorted(
                    {
                        f"{'.'.join(str(part) for part in item['loc'])}:{item['type']}"
                        for item in cause.errors(
                            include_url=False,
                            include_context=False,
                            include_input=False,
                        )
                    }
                )
            )
            if isinstance(cause, ValidationError)
            else ()
        )


_ALLOWED_VARIANTS = {
    CaptureConditionV21.PAYMENT_FLAG: {"50%", "75%", "100%"},
    CaptureConditionV21.EMAIL_MEMORY_LEAK: {"SELECTED"},
    CaptureConditionV21.RECOMMENDATION_STOP: {"STOPPED"},
    CaptureConditionV21.AD_HIGH_CPU: {"on"},
    CaptureConditionV21.EMAIL_STOP: {"STOPPED"},
    CaptureConditionV21.PRODUCT_CATALOG_STOP: {"STOPPED"},
    CaptureConditionV21.SHIPPING_SLOWDOWN: {"SELECTED"},
    CaptureConditionV21.NO_FAULT: {"BASELINE"},
    CaptureConditionV21.RECOVERY_TRANSITION: {"RECOVERY"},
    CaptureConditionV21.SOURCE_PARTIAL_FAILURE: {"SOURCE_UNAVAILABLE"},
}


class CaptureCasePlanV21(DtaModelV21):
    schema_version: Literal["dta-v21.capture-case-plan.v1"]
    case_id: str = Field(pattern=r"^dta21-case-[0-9]{3}$")
    scenario_id: str = Field(pattern=r"^dta21-(?:dev-00[1-6]|legacy-recommendation)$")
    split: EvaluationSplitV21
    operational_family: OperationalFamilyV21
    evaluator_family: ScenarioFamilyV21
    condition: CaptureConditionV21
    fault_variant: str = Field(min_length=1, max_length=32)
    load_vus: StrictInt
    observation_window_seconds: StrictInt = Field(ge=10, le=60)
    meaningful_observation_differences: tuple[str, ...] = Field(
        min_length=1, max_length=8
    )
    full_context_tools: tuple[ToolName, ...] = Field(min_length=1, max_length=5)
    case_plan_sha256: Sha256V21

    @model_validator(mode="after")
    def require_case(self) -> CaptureCasePlanV21:
        if self.load_vus not in (5, 10, 25, 50):
            raise ValueError("capture load is outside frozen upstream variants")
        if self.fault_variant not in _ALLOWED_VARIANTS[self.condition]:
            label = (
                "Ad CPU variant"
                if self.condition is CaptureConditionV21.AD_HIGH_CPU
                else "capture fault variant"
            )
            raise ValueError(f"{label} is outside the declared allowlist")
        if len(self.full_context_tools) != len(
            set(self.full_context_tools)
        ) or self.full_context_tools != tuple(
            sorted(self.full_context_tools, key=lambda item: item.value)
        ):
            raise ValueError("capture full-context tools are not canonical")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"case_plan_sha256"})
        )
        if self.case_plan_sha256 != expected:
            raise ValueError("capture case plan digest differs")
        return self

    @property
    def condition_signature(self) -> str:
        return semantic_sha256(
            {
                "operational_family": self.operational_family.value,
                "condition": self.condition.value,
                "fault_variant": self.fault_variant,
                "load_vus": self.load_vus,
                "observation_window_seconds": self.observation_window_seconds,
                "meaningful_observation_differences": (
                    self.meaningful_observation_differences
                ),
                "full_context_tools": [item.value for item in self.full_context_tools],
            }
        )


class CaptureCampaignPlanV21(DtaModelV21):
    schema_version: Literal["dta-v21.capture-campaign-plan.v1"]
    base_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    email_calibration_variants: tuple[Literal["10x", "100x", "1000x"], ...]
    ad_cpu_calibration_variants: tuple[Literal["off", "on"], ...]
    shipping_calibration_variants: tuple[Literal["off", "5sec", "10sec"], ...]
    cases: tuple[CaptureCasePlanV21, ...]
    plan_sha256: Sha256V21

    @model_validator(mode="after")
    def require_plan(self) -> CaptureCampaignPlanV21:
        if self.email_calibration_variants != ("10x", "100x", "1000x"):
            raise ValueError("Email calibration variants differ")
        if self.ad_cpu_calibration_variants != ("off", "on"):
            raise ValueError("Ad CPU calibration variants differ")
        if self.shipping_calibration_variants != ("off", "5sec", "10sec"):
            raise ValueError("Shipping calibration variants differ")
        if len(self.cases) != 20 or len({item.case_id for item in self.cases}) != 20:
            raise ValueError("capture campaign requires exact twenty cases")
        split_counts = Counter(item.split for item in self.cases)
        if split_counts != {
            EvaluationSplitV21.DEVELOPMENT: 12,
            EvaluationSplitV21.HELD_OUT: 8,
        }:
            raise ValueError("capture campaign split cardinalities differ")
        development_expected = {
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
        held_out_expected = {
            OperationalFamilyV21.PAYMENT_CONFIGURATION: 1,
            OperationalFamilyV21.EMAIL_MEMORY_LEAK: 1,
            OperationalFamilyV21.AD_CPU_SATURATION: 1,
            OperationalFamilyV21.EMAIL_UNAVAILABLE: 1,
            OperationalFamilyV21.PRODUCT_CATALOG_UNAVAILABLE: 1,
            OperationalFamilyV21.SHIPPING_DEPENDENCY_LATENCY: 1,
            OperationalFamilyV21.NO_FAULT: 1,
            OperationalFamilyV21.MISSING_CONFLICTING_EVIDENCE: 1,
        }
        for split, expected in (
            (EvaluationSplitV21.DEVELOPMENT, development_expected),
            (EvaluationSplitV21.HELD_OUT, held_out_expected),
        ):
            actual = Counter(
                item.operational_family for item in self.cases if item.split is split
            )
            if actual != expected:
                raise ValueError(f"{split.value} capture allocation differs")
        for held_out in (
            item for item in self.cases if item.split is EvaluationSplitV21.HELD_OUT
        ):
            development = tuple(
                item
                for item in self.cases
                if item.split is EvaluationSplitV21.DEVELOPMENT
                and item.operational_family is held_out.operational_family
            )
            if not development or any(
                item.condition_signature == held_out.condition_signature
                for item in development
            ):
                raise ValueError("held-out condition differs only by identity")
        expected_digest = semantic_sha256(
            self.model_dump(mode="json", exclude={"plan_sha256"})
        )
        if self.plan_sha256 != expected_digest:
            raise ValueError("capture campaign plan digest differs")
        return self


class CaptureCalibrationObservationV21(DtaModelV21):
    schema_version: Literal["dta-v21.capture-calibration-observation.v1"]
    kind: CalibrationKindV21
    target_service: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    variant: str = Field(min_length=1, max_length=32)
    maximum_memory_bytes: StrictInt | None = Field(default=None, ge=0)
    memory_delta_bytes: StrictInt | None = None
    memory_slope_bytes_per_second: StrictFloat | None = None
    cpu_p50_percent: StrictFloat | None = Field(default=None, ge=0.0)
    cpu_p95_percent: StrictFloat | None = Field(default=None, ge=0.0)
    cpu_capacity_percent: StrictFloat | None = Field(default=None, ge=100.0)
    cpu_p95_capacity_ratio: StrictFloat | None = Field(default=None, ge=0.0)
    cpu_safety_ceiling_ratio: StrictFloat | None = Field(default=None, ge=0.0, le=1.0)
    business_error_rate: StrictFloat | None = Field(default=None, ge=0.0, le=1.0)
    business_latency_p95_ms: StrictFloat | None = Field(default=None, ge=0.0)
    business_impact_observed: bool | None = None
    business_impact_service: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9-]*$"
    )
    attributable_trace_latency_ms: StrictFloat | None = Field(default=None, ge=0.0)
    target_runtime_stopped: bool | None = None
    safe: bool
    measurable: bool
    observation_sha256: Sha256V21

    @model_validator(mode="after")
    def require_observation(self) -> CaptureCalibrationObservationV21:
        required = {
            CalibrationKindV21.EMAIL_MEMORY: (
                self.maximum_memory_bytes,
                self.memory_delta_bytes,
                self.memory_slope_bytes_per_second,
            ),
            CalibrationKindV21.AD_CPU: (
                self.cpu_p50_percent,
                self.cpu_p95_percent,
                self.business_latency_p95_ms,
            ),
            CalibrationKindV21.SHIPPING_LATENCY: (
                self.business_latency_p95_ms,
                self.attributable_trace_latency_ms,
            ),
            CalibrationKindV21.SERVICE_UNAVAILABLE: (
                self.target_runtime_stopped,
                self.business_impact_observed,
            ),
        }[self.kind]
        if any(item is None for item in required):
            raise ValueError("capture calibration observation lacks required measures")
        additive_fields = (
            "cpu_capacity_percent",
            "cpu_p95_capacity_ratio",
            "cpu_safety_ceiling_ratio",
            "business_impact_service",
        )
        cpu_capacity_fields = additive_fields[:3]
        if self.kind is CalibrationKindV21.AD_CPU and any(
            field in self.model_fields_set for field in cpu_capacity_fields
        ):
            if any(getattr(self, field) is None for field in cpu_capacity_fields):
                raise ValueError("Ad CPU calibration lacks host-capacity evidence")
            assert self.cpu_capacity_percent is not None
            assert self.cpu_p95_percent is not None
            assert self.cpu_p95_capacity_ratio is not None
            assert self.cpu_safety_ceiling_ratio is not None
            expected_ratio = self.cpu_p95_percent / self.cpu_capacity_percent
            if abs(self.cpu_p95_capacity_ratio - expected_ratio) > 1e-9:
                raise ValueError("Ad CPU host-capacity ratio differs")
            if self.cpu_safety_ceiling_ratio != 0.5:
                raise ValueError("Ad CPU safety ceiling differs")
            if (
                "business_impact_service" in self.model_fields_set
                and self.business_impact_observed is None
            ):
                raise ValueError("Ad CPU calibration lacks business-impact observation")
        if (
            self.kind is CalibrationKindV21.SERVICE_UNAVAILABLE
            and "business_impact_service" in self.model_fields_set
            and self.business_impact_observed is True
            and self.business_impact_service is None
        ):
            raise ValueError("service-unavailable calibration lacks impact service")
        if (
            self.kind is CalibrationKindV21.SERVICE_UNAVAILABLE
            and self.business_impact_observed is False
            and self.business_impact_service is not None
        ):
            raise ValueError("service-unavailable impact service lacks observed impact")
        digest_exclusions = {"observation_sha256"}
        for field in additive_fields:
            if field not in self.model_fields_set:
                digest_exclusions.add(field)
        digest_payload = self.model_dump(mode="json", exclude=digest_exclusions)
        expected = semantic_sha256(digest_payload)
        if self.observation_sha256 != expected:
            raise ValueError("capture calibration observation digest differs")
        return self


class CaptureCaseReceiptV21(DtaModelV21):
    schema_version: Literal["dta-v21.capture-case-receipt.v1"]
    case_id: str = Field(pattern=r"^dta21-case-[0-9]{3}$")
    case_sha256: Sha256V21
    truth_sha256: Sha256V21
    baseline_verified_before: Literal[True]
    fault_operation_count: StrictInt = Field(ge=0, le=1)
    agent_calls: Literal[0] = 0
    provider_calls: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    baseline_restored_after: Literal[True]
    receipt_sha256: Sha256V21

    @model_validator(mode="after")
    def require_receipt(self) -> CaptureCaseReceiptV21:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"receipt_sha256"})
        )
        if self.receipt_sha256 != expected:
            raise ValueError("capture case receipt digest differs")
        return self


class CapturedCaseArtifactV21(DtaModelV21):
    case_id: str = Field(pattern=r"^dta21-case-[0-9]{3}$")
    case_sha256: Sha256V21
    truth_sha256: Sha256V21
    fault_operation_count: StrictInt = Field(ge=0, le=1)


class CaptureCampaignClosureV21(DtaModelV21):
    schema_version: Literal["dta-v21.capture-campaign-closure.v1"]
    plan_sha256: Sha256V21
    terminal: CaptureTerminalV21
    failure_code: CaptureFailureCodeV21 | None
    failure_stage: str | None = Field(default=None, pattern=r"^[A-Za-z0-9:_-]{1,160}$")
    failure_cause_type: str | None = Field(
        default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$"
    )
    failure_detail_sha256: Sha256V21 | None = None
    failure_validation_codes: tuple[str, ...] = Field(default=(), max_length=8)
    failed_case_id: str | None = Field(default=None, pattern=r"^dta21-case-[0-9]{3}$")
    selected_email_variant: Literal["10x", "100x", "1000x"] | None
    selected_ad_cpu_variant: Literal["on"] | None
    selected_shipping_variant: Literal["5sec", "10sec"] | None
    calibrations: tuple[CaptureCalibrationObservationV21, ...]
    case_receipts: tuple[CaptureCaseReceiptV21, ...] = Field(max_length=20)
    baseline_restored: bool
    cleanup_attempted: bool
    cleanup_verdict: Literal["CLEAN", "BLOCKED", "NOT_ATTEMPTED"]
    owned_containers_after: StrictInt | None = Field(default=None, ge=0)
    owned_networks_after: StrictInt | None = Field(default=None, ge=0)
    owned_volumes_after: StrictInt | None = Field(default=None, ge=0)
    non_owned_resources_changed: bool | None
    closure_sha256: Sha256V21

    def _digest_payload(self) -> dict[str, Any]:
        digest_exclusions = {"closure_sha256"}
        for field in (
            "failure_stage",
            "failure_cause_type",
            "failure_detail_sha256",
            "failure_validation_codes",
        ):
            if field not in self.model_fields_set:
                digest_exclusions.add(field)
        digest_payload = self.model_dump(mode="json", exclude=digest_exclusions)
        if any(
            field not in observation.model_fields_set
            for observation in self.calibrations
            for field in (
                "cpu_capacity_percent",
                "cpu_p95_capacity_ratio",
                "cpu_safety_ceiling_ratio",
                "business_impact_service",
            )
        ):
            digest_payload["calibrations"] = [
                observation.model_dump(mode="json", exclude_unset=True)
                for observation in self.calibrations
            ]
        return digest_payload

    @model_validator(mode="after")
    def require_closure(self) -> CaptureCampaignClosureV21:
        passed = (
            self.failure_code is None
            and self.selected_email_variant is not None
            and self.selected_ad_cpu_variant == "on"
            and self.selected_shipping_variant is not None
            and len(self.case_receipts) == 20
            and self.baseline_restored
            and self.cleanup_attempted
            and self.cleanup_verdict == "CLEAN"
            and self.owned_containers_after == 0
            and self.owned_networks_after == 0
            and self.owned_volumes_after == 0
            and self.non_owned_resources_changed is False
        )
        if (self.terminal is CaptureTerminalV21.PASS) != passed:
            raise ValueError("capture campaign terminal differs from closure evidence")
        failure_details = (
            self.failure_stage,
            self.failure_cause_type,
            self.failure_detail_sha256,
        )
        has_no_details = all(item is None for item in failure_details)
        has_complete_details = all(item is not None for item in failure_details)
        if self.failure_code is None and (
            not has_no_details or self.failure_validation_codes
        ):
            raise ValueError("capture campaign failure detail differs from terminal")
        if self.failure_code is not None and not (
            has_no_details or has_complete_details
        ):
            raise ValueError("capture campaign failure detail is incomplete")
        expected = semantic_sha256(self._digest_payload())
        if self.closure_sha256 != expected:
            raise ValueError("capture campaign closure digest differs")
        return self


class CaptureLifecycleV21(Protocol):
    def admit(self) -> None: ...
    def start(self) -> None: ...
    def wait_ready(self) -> None: ...
    def verify_baseline(self) -> None: ...
    def calibrate(
        self, *, kind: CalibrationKindV21, target_service: str, variant: str
    ) -> CaptureCalibrationObservationV21: ...
    def apply_case(
        self,
        case: CaptureCasePlanV21,
        *,
        selected_email_variant: str,
        selected_shipping_variant: str,
    ) -> None: ...
    def capture_case(self, case: CaptureCasePlanV21) -> CapturedCaseArtifactV21: ...
    def restore_baseline(self) -> None: ...
    def cleanup(self, *, baseline_restored: bool) -> dict[str, object]: ...


def _case(**values: object) -> CaptureCasePlanV21:
    payload: dict[str, Any] = {
        "schema_version": "dta-v21.capture-case-plan.v1",
        **values,
    }
    draft = CaptureCasePlanV21.model_construct(**payload, case_plan_sha256="0" * 64)
    return CaptureCasePlanV21.model_validate(
        {
            **payload,
            "case_plan_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"case_plan_sha256"})
            ),
        }
    )


def _tools(*values: ToolName) -> tuple[ToolName, ...]:
    return tuple(sorted(values, key=lambda item: item.value))


_METRICS_TRACES_RUNTIME_LOGS = _tools(
    ToolName.QUERY_METRICS,
    ToolName.QUERY_TRACE_NEIGHBORHOOD,
    ToolName.INSPECT_SERVICE_RUNTIME,
    ToolName.SEARCH_LOGS,
)
_METRICS_RESOURCES_RUNTIME_LOGS = _tools(
    ToolName.QUERY_METRICS,
    ToolName.INSPECT_RESOURCE_USAGE,
    ToolName.INSPECT_SERVICE_RUNTIME,
    ToolName.SEARCH_LOGS,
)


def build_default_capture_plan_v21(*, base_head: str) -> CaptureCampaignPlanV21:
    raw = (
        (
            1,
            "dta21-dev-005",
            "D",
            "PAYMENT_CONFIGURATION",
            "PAYMENT_FLAG",
            "100%",
            25,
            30,
            ("fault_strength",),
            _METRICS_TRACES_RUNTIME_LOGS,
        ),
        (
            2,
            "dta21-dev-005",
            "D",
            "PAYMENT_CONFIGURATION",
            "PAYMENT_FLAG",
            "50%",
            10,
            20,
            ("fault_strength", "load_level"),
            _METRICS_TRACES_RUNTIME_LOGS,
        ),
        (
            3,
            "dta21-dev-002",
            "D",
            "EMAIL_MEMORY_LEAK",
            "EMAIL_MEMORY_LEAK",
            "SELECTED",
            25,
            30,
            ("load_level",),
            _METRICS_RESOURCES_RUNTIME_LOGS,
        ),
        (
            4,
            "dta21-dev-002",
            "D",
            "EMAIL_MEMORY_LEAK",
            "EMAIL_MEMORY_LEAK",
            "SELECTED",
            50,
            20,
            ("load_level", "time_window"),
            _METRICS_RESOURCES_RUNTIME_LOGS,
        ),
        (
            5,
            "dta21-legacy-recommendation",
            "D",
            "RECOMMENDATION_UNAVAILABLE",
            "RECOMMENDATION_STOP",
            "STOPPED",
            25,
            30,
            ("load_level",),
            _METRICS_TRACES_RUNTIME_LOGS,
        ),
        (
            6,
            "dta21-dev-001",
            "D",
            "AD_CPU_SATURATION",
            "AD_HIGH_CPU",
            "on",
            25,
            30,
            ("load_level",),
            _METRICS_RESOURCES_RUNTIME_LOGS,
        ),
        (
            7,
            "dta21-dev-001",
            "D",
            "AD_CPU_SATURATION",
            "AD_HIGH_CPU",
            "on",
            10,
            20,
            ("load_level", "time_window"),
            _METRICS_RESOURCES_RUNTIME_LOGS,
        ),
        (
            8,
            "dta21-dev-002",
            "D",
            "EMAIL_UNAVAILABLE",
            "EMAIL_STOP",
            "STOPPED",
            25,
            30,
            ("service_symptom_distribution",),
            _METRICS_TRACES_RUNTIME_LOGS,
        ),
        (
            9,
            "dta21-dev-003",
            "D",
            "PRODUCT_CATALOG_UNAVAILABLE",
            "PRODUCT_CATALOG_STOP",
            "STOPPED",
            25,
            30,
            ("service_symptom_distribution",),
            _METRICS_TRACES_RUNTIME_LOGS,
        ),
        (
            10,
            "dta21-dev-004",
            "D",
            "SHIPPING_DEPENDENCY_LATENCY",
            "SHIPPING_SLOWDOWN",
            "SELECTED",
            10,
            30,
            ("load_level",),
            _METRICS_TRACES_RUNTIME_LOGS,
        ),
        (
            11,
            "dta21-dev-005",
            "D",
            "NO_FAULT",
            "NO_FAULT",
            "BASELINE",
            5,
            20,
            ("noise_decoy_evidence",),
            _METRICS_TRACES_RUNTIME_LOGS,
        ),
        (
            12,
            "dta21-dev-006",
            "D",
            "MISSING_CONFLICTING_EVIDENCE",
            "RECOVERY_TRANSITION",
            "RECOVERY",
            10,
            30,
            ("evidence_availability", "time_window"),
            _METRICS_TRACES_RUNTIME_LOGS,
        ),
        (
            13,
            "dta21-dev-005",
            "H",
            "PAYMENT_CONFIGURATION",
            "PAYMENT_FLAG",
            "75%",
            5,
            40,
            ("fault_strength", "load_level", "time_window"),
            _METRICS_TRACES_RUNTIME_LOGS,
        ),
        (
            14,
            "dta21-dev-002",
            "H",
            "EMAIL_MEMORY_LEAK",
            "EMAIL_MEMORY_LEAK",
            "SELECTED",
            10,
            40,
            ("load_level", "time_window"),
            _METRICS_RESOURCES_RUNTIME_LOGS,
        ),
        (
            15,
            "dta21-dev-001",
            "H",
            "AD_CPU_SATURATION",
            "AD_HIGH_CPU",
            "on",
            5,
            40,
            ("load_level", "time_window"),
            _METRICS_RESOURCES_RUNTIME_LOGS,
        ),
        (
            16,
            "dta21-dev-002",
            "H",
            "EMAIL_UNAVAILABLE",
            "EMAIL_STOP",
            "STOPPED",
            5,
            40,
            ("load_level", "time_window"),
            _METRICS_TRACES_RUNTIME_LOGS,
        ),
        (
            17,
            "dta21-dev-003",
            "H",
            "PRODUCT_CATALOG_UNAVAILABLE",
            "PRODUCT_CATALOG_STOP",
            "STOPPED",
            5,
            40,
            ("load_level", "time_window"),
            _METRICS_TRACES_RUNTIME_LOGS,
        ),
        (
            18,
            "dta21-dev-004",
            "H",
            "SHIPPING_DEPENDENCY_LATENCY",
            "SHIPPING_SLOWDOWN",
            "SELECTED",
            5,
            40,
            ("load_level", "time_window"),
            _METRICS_TRACES_RUNTIME_LOGS,
        ),
        (
            19,
            "dta21-dev-005",
            "H",
            "NO_FAULT",
            "NO_FAULT",
            "BASELINE",
            10,
            40,
            ("load_level", "time_window"),
            _METRICS_TRACES_RUNTIME_LOGS,
        ),
        (
            20,
            "dta21-dev-006",
            "H",
            "MISSING_CONFLICTING_EVIDENCE",
            "SOURCE_PARTIAL_FAILURE",
            "SOURCE_UNAVAILABLE",
            5,
            40,
            ("tool_source_partial_failure", "record_truncation"),
            _METRICS_TRACES_RUNTIME_LOGS,
        ),
    )
    cases = tuple(
        _case(
            case_id=f"dta21-case-{item[0]:03d}",
            scenario_id=item[1],
            split=(
                EvaluationSplitV21.DEVELOPMENT
                if item[2] == "D"
                else EvaluationSplitV21.HELD_OUT
            ),
            operational_family=OperationalFamilyV21(item[3]),
            evaluator_family=ScenarioFamilyV21(item[3]),
            condition=CaptureConditionV21(item[4]),
            fault_variant=item[5],
            load_vus=item[6],
            observation_window_seconds=item[7],
            meaningful_observation_differences=item[8],
            full_context_tools=item[9],
        )
        for item in raw
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.capture-campaign-plan.v1",
        "base_head": base_head,
        "email_calibration_variants": ("10x", "100x", "1000x"),
        "ad_cpu_calibration_variants": ("off", "on"),
        "shipping_calibration_variants": ("off", "5sec", "10sec"),
        "cases": cases,
    }
    draft = cast(Any, CaptureCampaignPlanV21).model_construct(
        **payload, plan_sha256="0" * 64
    )
    return CaptureCampaignPlanV21.model_validate(
        {
            **payload,
            "plan_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"plan_sha256"})
            ),
        }
    )


def _capture_receipt(
    *, case: CaptureCasePlanV21, artifact: CapturedCaseArtifactV21
) -> CaptureCaseReceiptV21:
    expected_fault_operations = (
        0
        if case.condition
        in (CaptureConditionV21.NO_FAULT, CaptureConditionV21.SOURCE_PARTIAL_FAILURE)
        else 1
    )
    if artifact.case_id != case.case_id:
        raise ValueError("captured artifact belongs to another case")
    if artifact.fault_operation_count != expected_fault_operations:
        raise ValueError("capture fault-operation count differs from the case")
    payload: dict[str, object] = {
        "schema_version": "dta-v21.capture-case-receipt.v1",
        "case_id": artifact.case_id,
        "case_sha256": artifact.case_sha256,
        "truth_sha256": artifact.truth_sha256,
        "baseline_verified_before": True,
        "fault_operation_count": artifact.fault_operation_count,
        "agent_calls": 0,
        "provider_calls": 0,
        "runbook_executions": 0,
        "baseline_restored_after": True,
    }
    return CaptureCaseReceiptV21.model_validate(
        {**payload, "receipt_sha256": semantic_sha256(payload)}
    )


def run_capture_campaign_attempt_v21(
    *, plan: CaptureCampaignPlanV21, lifecycle: CaptureLifecycleV21
) -> CaptureCampaignClosureV21:
    """Run one exact owned campaign, restoring baseline after every mutation."""

    plan = CaptureCampaignPlanV21.model_validate(plan.model_dump(mode="python"))
    failure: CaptureFailureCodeV21 | None = None
    failure_stage: str | None = None
    failure_cause_type: str | None = None
    failure_detail_sha256: str | None = None
    failure_validation_codes: tuple[str, ...] = ()
    failed_case_id: str | None = None
    started = False
    baseline_restored = False
    calibrations: list[CaptureCalibrationObservationV21] = []
    receipts: list[CaptureCaseReceiptV21] = []
    selected_email: str | None = None
    selected_ad: str | None = None
    selected_shipping: str | None = None
    cleanup_attempted = False
    cleanup: dict[str, object] = {
        "verdict": "NOT_ATTEMPTED",
        "owned_containers": None,
        "owned_networks": None,
        "owned_volumes": None,
        "non_owned_resources_changed": None,
    }

    def record_failure(
        code: CaptureFailureCodeV21, *, stage: str, error: Exception
    ) -> None:
        nonlocal failure, failure_stage, failure_cause_type
        nonlocal failure_detail_sha256, failure_validation_codes
        failure = code
        if isinstance(error, CaptureCalibrationFailureV21):
            failure_stage = error.stage
            failure_cause_type = error.cause_type
            failure_detail_sha256 = error.detail_sha256
            failure_validation_codes = error.validation_codes
            return
        failure_stage = stage
        failure_cause_type = type(error).__name__
        failure_detail_sha256 = semantic_sha256(
            {
                "stage": stage,
                "cause_type": failure_cause_type,
                "cause_message": str(error),
            }
        )
        failure_validation_codes = ()

    def restore() -> None:
        nonlocal baseline_restored
        lifecycle.restore_baseline()
        lifecycle.verify_baseline()
        baseline_restored = True

    def calibrate(
        *, kind: CalibrationKindV21, service: str, variant: str
    ) -> CaptureCalibrationObservationV21:
        nonlocal baseline_restored
        baseline_restored = False
        observation = lifecycle.calibrate(
            kind=kind, target_service=service, variant=variant
        )
        observation = CaptureCalibrationObservationV21.model_validate_json(
            observation.model_dump_json(exclude_unset=True)
        )
        if (
            observation.kind is not kind
            or observation.target_service != service
            or observation.variant != variant
        ):
            raise CaptureCalibrationFailureV21(
                kind=kind,
                target_service=service,
                variant=variant,
                step="RESPONSE_VALIDATION",
                cause=ValueError("capture calibration response differs from request"),
            )
        try:
            restore()
        except Exception as error:
            raise CaptureCalibrationFailureV21(
                kind=kind,
                target_service=service,
                variant=variant,
                step="BASELINE_RESTORE",
                cause=error,
            ) from error
        calibrations.append(observation)
        return observation

    try:
        try:
            lifecycle.admit()
        except Exception as error:
            record_failure(
                CaptureFailureCodeV21.ADMISSION_FAILED,
                stage="ADMISSION",
                error=error,
            )
        if failure is None:
            try:
                started = True
                lifecycle.start()
                lifecycle.wait_ready()
                lifecycle.verify_baseline()
                baseline_restored = True
            except Exception as error:
                record_failure(
                    CaptureFailureCodeV21.START_FAILED,
                    stage="START_AND_READINESS",
                    error=error,
                )

        if failure is None:
            try:
                email_trials = tuple(
                    calibrate(
                        kind=CalibrationKindV21.EMAIL_MEMORY,
                        service="email",
                        variant=variant,
                    )
                    for variant in plan.email_calibration_variants
                )
                email_candidates = tuple(
                    item.variant
                    for item in email_trials
                    if item.safe and item.measurable
                )
                selected_email = email_candidates[-1] if email_candidates else None

                ad_trials = tuple(
                    calibrate(
                        kind=CalibrationKindV21.AD_CPU,
                        service="ad",
                        variant=variant,
                    )
                    for variant in plan.ad_cpu_calibration_variants
                )
                ad_fault = next(item for item in ad_trials if item.variant == "on")
                selected_ad = "on" if ad_fault.safe and ad_fault.measurable else None

                shipping_trials = tuple(
                    calibrate(
                        kind=CalibrationKindV21.SHIPPING_LATENCY,
                        service="shipping",
                        variant=variant,
                    )
                    for variant in plan.shipping_calibration_variants
                )
                shipping_candidates = tuple(
                    item.variant
                    for item in shipping_trials
                    if item.variant != "off" and item.safe and item.measurable
                )
                selected_shipping = (
                    shipping_candidates[0] if shipping_candidates else None
                )

                unavailable = tuple(
                    calibrate(
                        kind=CalibrationKindV21.SERVICE_UNAVAILABLE,
                        service=service,
                        variant="STOPPED",
                    )
                    for service in ("email", "product-catalog")
                )
                if (
                    selected_email is None
                    or selected_ad is None
                    or selected_shipping is None
                    or not all(item.safe and item.measurable for item in unavailable)
                ):
                    record_failure(
                        CaptureFailureCodeV21.CALIBRATION_FAILED,
                        stage="CALIBRATION:SELECTION",
                        error=ValueError("calibration threshold selection failed"),
                    )
            except Exception as error:
                record_failure(
                    CaptureFailureCodeV21.CALIBRATION_FAILED,
                    stage="CALIBRATION:UNCLASSIFIED",
                    error=error,
                )

        if failure is None:
            assert selected_email is not None
            assert selected_shipping is not None
            for case in plan.cases:
                try:
                    lifecycle.verify_baseline()
                    baseline_restored = False
                    lifecycle.apply_case(
                        case,
                        selected_email_variant=selected_email,
                        selected_shipping_variant=selected_shipping,
                    )
                    artifact = CapturedCaseArtifactV21.model_validate(
                        lifecycle.capture_case(case).model_dump(mode="python")
                    )
                    restore()
                    receipts.append(_capture_receipt(case=case, artifact=artifact))
                except Exception as error:
                    record_failure(
                        CaptureFailureCodeV21.CASE_CAPTURE_FAILED,
                        stage=f"CASE:{case.case_id}",
                        error=error,
                    )
                    failed_case_id = case.case_id
                    break
    finally:
        if started:
            if not baseline_restored:
                try:
                    restore()
                except Exception as error:
                    record_failure(
                        CaptureFailureCodeV21.BASELINE_RESTORE_FAILED,
                        stage="FINAL_BASELINE_RESTORE",
                        error=error,
                    )
                    baseline_restored = False
            cleanup_attempted = True
            try:
                cleanup = lifecycle.cleanup(baseline_restored=baseline_restored)
                if cleanup.get("verdict") != "CLEAN":
                    record_failure(
                        CaptureFailureCodeV21.CLEANUP_BLOCKED,
                        stage="CLEANUP_VERDICT",
                        error=ValueError("owned cleanup verdict is not CLEAN"),
                    )
            except Exception as error:
                record_failure(
                    CaptureFailureCodeV21.CLEANUP_FAILED,
                    stage="CLEANUP_EXECUTION",
                    error=error,
                )
                cleanup = {
                    "verdict": "BLOCKED",
                    "owned_containers": None,
                    "owned_networks": None,
                    "owned_volumes": None,
                    "non_owned_resources_changed": None,
                }

    payload: dict[str, object] = {
        "schema_version": "dta-v21.capture-campaign-closure.v1",
        "plan_sha256": plan.plan_sha256,
        "terminal": (
            CaptureTerminalV21.PASS if failure is None else CaptureTerminalV21.BLOCKED
        ),
        "failure_code": failure,
        "failure_stage": failure_stage,
        "failure_cause_type": failure_cause_type,
        "failure_detail_sha256": failure_detail_sha256,
        "failure_validation_codes": failure_validation_codes,
        "failed_case_id": failed_case_id,
        "selected_email_variant": selected_email,
        "selected_ad_cpu_variant": selected_ad,
        "selected_shipping_variant": selected_shipping,
        "calibrations": tuple(calibrations),
        "case_receipts": tuple(receipts),
        "baseline_restored": baseline_restored,
        "cleanup_attempted": cleanup_attempted,
        "cleanup_verdict": cleanup.get("verdict", "BLOCKED"),
        "owned_containers_after": cleanup.get("owned_containers"),
        "owned_networks_after": cleanup.get("owned_networks"),
        "owned_volumes_after": cleanup.get("owned_volumes"),
        "non_owned_resources_changed": cleanup.get("non_owned_resources_changed"),
    }
    draft = cast(Any, CaptureCampaignClosureV21).model_construct(
        **payload, closure_sha256="0" * 64
    )
    return CaptureCampaignClosureV21.model_validate(
        {
            **payload,
            "closure_sha256": semantic_sha256(draft._digest_payload()),
        }
    )


__all__ = (
    "CalibrationKindV21",
    "CaptureCalibrationFailureV21",
    "CaptureCalibrationObservationV21",
    "CaptureCampaignClosureV21",
    "CaptureCampaignPlanV21",
    "CaptureCaseReceiptV21",
    "CaptureCasePlanV21",
    "CaptureConditionV21",
    "CaptureFailureCodeV21",
    "CaptureTerminalV21",
    "CapturedCaseArtifactV21",
    "OperationalFamilyV21",
    "build_default_capture_plan_v21",
    "run_capture_campaign_attempt_v21",
)
