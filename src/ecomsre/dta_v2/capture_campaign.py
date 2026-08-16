"""Capture-only PR-E campaign contracts and failure-safe orchestration."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import Field, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.contracts import DtaModel, Sha256, semantic_sha256
from ecomsre.dta_v2.evaluation_contracts import (
    EvaluationSplit,
    GitCommit,
    ScenarioFamily,
)
from ecomsre.dta_v2.tool_contracts import ToolName


class CaptureTerminal(str, Enum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"


class CaptureFailureCode(str, Enum):
    ADMISSION_FAILED = "ADMISSION_FAILED"
    START_FAILED = "START_FAILED"
    EMAIL_CALIBRATION_FAILED = "EMAIL_CALIBRATION_FAILED"
    CASE_CAPTURE_FAILED = "CASE_CAPTURE_FAILED"
    BASELINE_RESTORE_FAILED = "BASELINE_RESTORE_FAILED"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    CLEANUP_BLOCKED = "CLEANUP_BLOCKED"


class CaptureFailureOperation(str, Enum):
    ADMIT = "ADMIT"
    START = "START"
    WAIT_READY = "WAIT_READY"
    VERIFY_BASELINE = "VERIFY_BASELINE"
    OBSERVE_BASELINE_MEMORY = "OBSERVE_BASELINE_MEMORY"
    APPLY_EMAIL_CALIBRATION = "APPLY_EMAIL_CALIBRATION"
    OBSERVE_EMAIL_CALIBRATION = "OBSERVE_EMAIL_CALIBRATION"
    APPLY_CASE = "APPLY_CASE"
    CAPTURE_CASE = "CAPTURE_CASE"
    RECOMMENDATION_IDENTITY = "RECOMMENDATION_IDENTITY"
    RECOMMENDATION_STOP_POST = "RECOMMENDATION_STOP_POST"
    RECOMMENDATION_WAIT_STOPPED = "RECOMMENDATION_WAIT_STOPPED"
    RECOMMENDATION_START_POST = "RECOMMENDATION_START_POST"
    RECOMMENDATION_WAIT_RUNNING = "RECOMMENDATION_WAIT_RUNNING"
    EMAIL_IDENTITY = "EMAIL_IDENTITY"
    EMAIL_RESTART_POST = "EMAIL_RESTART_POST"
    EMAIL_WAIT_RUNNING = "EMAIL_WAIT_RUNNING"
    RESTORE_FLAGS = "RESTORE_FLAGS"
    RESTORE_BASELINE = "RESTORE_BASELINE"
    CLEANUP = "CLEANUP"


class CaptureCaseQualityFailureCode(str, Enum):
    REQUIRED_SOURCE_UNAVAILABLE = "REQUIRED_SOURCE_UNAVAILABLE"
    REQUIRED_TARGET_MISSING = "REQUIRED_TARGET_MISSING"
    PAYMENT_LOCALIZED_ERROR_MISSING = "PAYMENT_LOCALIZED_ERROR_MISSING"
    RECOMMENDATION_NOT_STOPPED = "RECOMMENDATION_NOT_STOPPED"
    EMAIL_MEMORY_GROWTH_MISSING = "EMAIL_MEMORY_GROWTH_MISSING"


class CaptureCaseQualityFailure(RuntimeError):
    """Fixed evidence-grade failure marker without backend text."""

    def __init__(self, code: CaptureCaseQualityFailureCode) -> None:
        super().__init__(code.value)
        self.code = code


class CaptureOperationFailure(RuntimeError):
    """Fixed safe operation marker; never retains backend exception text."""

    def __init__(
        self,
        operation: CaptureFailureOperation,
        *,
        http_status: int | None = None,
    ) -> None:
        super().__init__(operation.value)
        self.operation = operation
        self.http_status = http_status


def _failure_operation(
    error: Exception, *, default: CaptureFailureOperation
) -> CaptureFailureOperation:
    return error.operation if isinstance(error, CaptureOperationFailure) else default


def _failure_http_status(error: Exception) -> int | None:
    return error.http_status if isinstance(error, CaptureOperationFailure) else None


class OperationalFamily(str, Enum):
    PAYMENT = "PAYMENT"
    RECOMMENDATION = "RECOMMENDATION"
    EMAIL = "EMAIL"
    NO_ACTION = "NO_ACTION"


class CaptureCondition(str, Enum):
    PAYMENT_FLAG = "PAYMENT_FLAG"
    RECOMMENDATION_STOP = "RECOMMENDATION_STOP"
    EMAIL_LEAK = "EMAIL_LEAK"
    NO_FAULT = "NO_FAULT"
    RECOVERY_TRANSITION = "RECOVERY_TRANSITION"
    OBSERVER_UNKNOWN = "OBSERVER_UNKNOWN"


class CaptureCasePlan(DtaModel):
    schema_version: Literal["dta-v2.capture-case-plan.v1"]
    case_id: str = Field(pattern=r"^dta-case-[0-9]{3}$")
    scenario_id: str = Field(pattern=r"^dta-dev-00[1-3]$")
    split: EvaluationSplit
    operational_family: OperationalFamily
    evaluator_family: ScenarioFamily
    condition: CaptureCondition
    fault_variant: str = Field(min_length=1, max_length=32)
    load_vus: StrictInt
    observation_window_seconds: StrictInt = Field(ge=10, le=60)
    meaningful_observation_differences: tuple[str, ...] = Field(
        min_length=1, max_length=5
    )
    full_context_tools: tuple[ToolName, ToolName, ToolName, ToolName]
    case_plan_sha256: Sha256

    @model_validator(mode="after")
    def require_case_plan(self) -> CaptureCasePlan:
        if self.load_vus not in (5, 10, 25, 50):
            raise ValueError("capture load is outside frozen upstream variants")
        if (
            len(self.full_context_tools) != len(set(self.full_context_tools))
            or self.full_context_tools
            != tuple(sorted(self.full_context_tools, key=lambda item: item.value))
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
                "operational_family": self.operational_family,
                "condition": self.condition,
                "fault_variant": self.fault_variant,
                "load_vus": self.load_vus,
                "observation_window_seconds": self.observation_window_seconds,
                "full_context_tools": self.full_context_tools,
            }
        )


class CaptureCampaignPlan(DtaModel):
    schema_version: Literal["dta-v2.capture-campaign-plan.v1"]
    base_head: GitCommit
    email_calibration_variants: tuple[Literal["10x", "100x", "1000x"], ...]
    email_maximum_memory_bytes: StrictInt = Field(ge=1)
    email_maximum_delta_bytes: StrictInt = Field(ge=1)
    email_maximum_slope_bytes_per_second: StrictFloat = Field(gt=0)
    email_minimum_measurable_delta_bytes: StrictInt = Field(ge=1)
    cases: tuple[CaptureCasePlan, ...]
    plan_sha256: Sha256

    @model_validator(mode="after")
    def require_campaign_plan(self) -> CaptureCampaignPlan:
        if self.email_calibration_variants != ("10x", "100x", "1000x"):
            raise ValueError("Email calibration variants are not ascending and frozen")
        if len(self.cases) != 12 or len({item.case_id for item in self.cases}) != 12:
            raise ValueError("capture campaign does not contain exact twelve cases")
        counts = {
            split: sum(item.split is split for item in self.cases)
            for split in EvaluationSplit
        }
        if counts != {
            EvaluationSplit.DEVELOPMENT: 6,
            EvaluationSplit.HELD_OUT: 3,
            EvaluationSplit.NO_ACTION: 3,
        }:
            raise ValueError("capture campaign split cardinalities differ")
        for family in (
            OperationalFamily.PAYMENT,
            OperationalFamily.RECOMMENDATION,
            OperationalFamily.EMAIL,
        ):
            development = tuple(
                item
                for item in self.cases
                if item.operational_family is family
                and item.split is EvaluationSplit.DEVELOPMENT
            )
            held_out = tuple(
                item
                for item in self.cases
                if item.operational_family is family
                and item.split is EvaluationSplit.HELD_OUT
            )
            if len(development) != 2 or len(held_out) != 1:
                raise ValueError("capture family split matrix differs")
            if any(
                item.condition_signature == held_out[0].condition_signature
                for item in development
            ):
                raise ValueError("held-out condition differs only by identity")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"plan_sha256"})
        )
        if self.plan_sha256 != expected:
            raise ValueError("capture campaign plan digest differs")
        return self


def _case_plan(**values: object) -> CaptureCasePlan:
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.capture-case-plan.v1",
        **values,
    }
    draft = CaptureCasePlan.model_construct(
        **payload, case_plan_sha256="0" * 64
    )
    return CaptureCasePlan.model_validate(
        {
            **payload,
            "case_plan_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"case_plan_sha256"})
            ),
        }
    )


_PAYMENT_CONTEXT = tuple(
    sorted(
        (
            ToolName.INSPECT_SERVICE_RUNTIME,
            ToolName.QUERY_METRICS,
            ToolName.QUERY_TRACE_NEIGHBORHOOD,
            ToolName.SEARCH_LOGS,
        ),
        key=lambda item: item.value,
    )
)
_RECOMMENDATION_CONTEXT = _PAYMENT_CONTEXT
_EMAIL_CONTEXT = tuple(
    sorted(
        (
            ToolName.INSPECT_RESOURCE_USAGE,
            ToolName.INSPECT_SERVICE_RUNTIME,
            ToolName.QUERY_METRICS,
            ToolName.SEARCH_LOGS,
        ),
        key=lambda item: item.value,
    )
)


def build_default_capture_plan(*, base_head: str) -> CaptureCampaignPlan:
    """Freeze split assignment and meaningful variants before Provider development."""

    raw_cases = (
        ("dta-case-001", "dta-dev-001", EvaluationSplit.DEVELOPMENT, OperationalFamily.PAYMENT, ScenarioFamily.PAYMENT, CaptureCondition.PAYMENT_FLAG, "100%", 25, 30, ("fault_strength",), _PAYMENT_CONTEXT),
        ("dta-case-002", "dta-dev-001", EvaluationSplit.DEVELOPMENT, OperationalFamily.PAYMENT, ScenarioFamily.PAYMENT, CaptureCondition.PAYMENT_FLAG, "50%", 10, 30, ("fault_strength", "load_level"), _PAYMENT_CONTEXT),
        ("dta-case-003", "dta-dev-002", EvaluationSplit.DEVELOPMENT, OperationalFamily.RECOMMENDATION, ScenarioFamily.RECOMMENDATION, CaptureCondition.RECOMMENDATION_STOP, "STOPPED", 25, 30, ("load_level",), _RECOMMENDATION_CONTEXT),
        ("dta-case-004", "dta-dev-002", EvaluationSplit.DEVELOPMENT, OperationalFamily.RECOMMENDATION, ScenarioFamily.RECOMMENDATION, CaptureCondition.RECOMMENDATION_STOP, "STOPPED", 10, 20, ("load_level", "timing_window"), _RECOMMENDATION_CONTEXT),
        ("dta-case-005", "dta-dev-003", EvaluationSplit.DEVELOPMENT, OperationalFamily.EMAIL, ScenarioFamily.EMAIL, CaptureCondition.EMAIL_LEAK, "SELECTED", 25, 30, ("load_level",), _EMAIL_CONTEXT),
        ("dta-case-006", "dta-dev-003", EvaluationSplit.DEVELOPMENT, OperationalFamily.EMAIL, ScenarioFamily.EMAIL, CaptureCondition.EMAIL_LEAK, "SELECTED", 10, 20, ("fault_strength", "timing_window"), _EMAIL_CONTEXT),
        ("dta-case-007", "dta-dev-001", EvaluationSplit.HELD_OUT, OperationalFamily.PAYMENT, ScenarioFamily.PAYMENT, CaptureCondition.PAYMENT_FLAG, "75%", 5, 40, ("fault_strength", "load_level", "timing_window"), _PAYMENT_CONTEXT),
        ("dta-case-008", "dta-dev-002", EvaluationSplit.HELD_OUT, OperationalFamily.RECOMMENDATION, ScenarioFamily.RECOMMENDATION, CaptureCondition.RECOMMENDATION_STOP, "STOPPED", 5, 40, ("load_level", "timing_window"), _RECOMMENDATION_CONTEXT),
        ("dta-case-009", "dta-dev-003", EvaluationSplit.HELD_OUT, OperationalFamily.EMAIL, ScenarioFamily.EMAIL, CaptureCondition.EMAIL_LEAK, "SELECTED", 5, 40, ("load_level", "timing_window"), _EMAIL_CONTEXT),
        ("dta-case-010", "dta-dev-001", EvaluationSplit.NO_ACTION, OperationalFamily.NO_ACTION, ScenarioFamily.NO_REAL_FAULT, CaptureCondition.NO_FAULT, "BASELINE", 5, 30, ("no_real_fault",), _PAYMENT_CONTEXT),
        ("dta-case-011", "dta-dev-002", EvaluationSplit.NO_ACTION, OperationalFamily.NO_ACTION, ScenarioFamily.CONFLICTING_EVIDENCE, CaptureCondition.RECOVERY_TRANSITION, "RECOVERY", 10, 30, ("conflicting_evidence",), _RECOMMENDATION_CONTEXT),
        ("dta-case-012", "dta-dev-003", EvaluationSplit.NO_ACTION, OperationalFamily.NO_ACTION, ScenarioFamily.UNKNOWN_MECHANISM, CaptureCondition.OBSERVER_UNKNOWN, "UNKNOWN", 5, 30, ("unknown_mechanism",), _EMAIL_CONTEXT),
    )
    cases = tuple(
        _case_plan(
            case_id=item[0],
            scenario_id=item[1],
            split=item[2],
            operational_family=item[3],
            evaluator_family=item[4],
            condition=item[5],
            fault_variant=item[6],
            load_vus=item[7],
            observation_window_seconds=item[8],
            meaningful_observation_differences=item[9],
            full_context_tools=item[10],
        )
        for item in raw_cases
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.capture-campaign-plan.v1",
        "base_head": base_head,
        "email_calibration_variants": ("10x", "100x", "1000x"),
        "email_maximum_memory_bytes": 512_000_000,
        "email_maximum_delta_bytes": 256_000_000,
        "email_maximum_slope_bytes_per_second": 20_000_000.0,
        "email_minimum_measurable_delta_bytes": 1_000_000,
        "cases": cases,
    }
    draft = CaptureCampaignPlan.model_construct(**payload, plan_sha256="0" * 64)
    return CaptureCampaignPlan.model_validate(
        {
            **payload,
            "plan_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"plan_sha256"})
            ),
        }
    )


class EmailMemoryObservation(DtaModel):
    maximum_memory_bytes: StrictInt = Field(ge=0)
    memory_delta_bytes: StrictInt
    maximum_slope_bytes_per_second: StrictFloat


class EmailCalibrationTrial(DtaModel):
    variant: Literal["10x", "100x", "1000x"]
    observation: EmailMemoryObservation
    measurable: bool
    safe: bool


class CaptureProhibitedActionCounters(DtaModel):
    agent_calls: Literal[0] = 0
    provider_calls: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    executor_calls: Literal[0] = 0
    verifier_calls: Literal[0] = 0
    remediation_writes: Literal[0] = 0


class CaptureCampaignClosure(DtaModel):
    schema_version: Literal["dta-v2.capture-campaign-closure.v1"]
    plan_sha256: Sha256
    terminal: CaptureTerminal
    failure_code: CaptureFailureCode | None
    cleanup_failure_code: CaptureFailureCode | None
    failure_operation: CaptureFailureOperation | None = None
    recovery_failure_operation: CaptureFailureOperation | None = None
    failure_http_status: StrictInt | None = Field(default=None, ge=100, le=599)
    recovery_failure_http_status: StrictInt | None = Field(
        default=None, ge=100, le=599
    )
    failed_case_id: str | None = Field(
        default=None, pattern=r"^dta-case-[0-9]{3}$"
    )
    quality_failure_code: CaptureCaseQualityFailureCode | None = None
    baseline_memory: EmailMemoryObservation | None
    calibration_observations: tuple[EmailCalibrationTrial, ...]
    selected_email_variant: Literal["10x", "100x", "1000x"] | None
    captured_case_sha256s: tuple[Sha256, ...] = Field(max_length=12)
    baseline_restored: bool
    cleanup_attempted: bool
    cleanup_verdict: Literal["CLEAN", "BLOCKED", "NOT_ATTEMPTED"]
    owned_containers_after: StrictInt | None = Field(default=None, ge=0)
    owned_networks_after: StrictInt | None = Field(default=None, ge=0)
    owned_volumes_after: StrictInt | None = Field(default=None, ge=0)
    non_owned_resources_changed: bool | None
    prohibited_action_counters: CaptureProhibitedActionCounters
    closure_sha256: Sha256

    @model_validator(mode="after")
    def require_closure(self) -> CaptureCampaignClosure:
        if self.quality_failure_code is not None and (
            self.failure_code is not CaptureFailureCode.CASE_CAPTURE_FAILED
            or self.failure_operation is not CaptureFailureOperation.CAPTURE_CASE
            or self.failed_case_id is None
        ):
            raise ValueError("capture quality failure lacks exact case binding")
        passed = (
            self.failure_code is None
            and self.cleanup_failure_code is None
            and self.selected_email_variant is not None
            and len(self.captured_case_sha256s) == 12
            and self.baseline_restored
            and self.cleanup_attempted
            and self.cleanup_verdict == "CLEAN"
            and self.owned_containers_after == 0
            and self.owned_networks_after == 0
            and self.owned_volumes_after == 0
            and self.non_owned_resources_changed is False
        )
        if (self.terminal is CaptureTerminal.PASS) != passed:
            raise ValueError("capture campaign terminal differs from closure state")
        full_payload = self.model_dump(
            mode="json", exclude={"closure_sha256"}
        )
        compatibility_exclude = {
            "closure_sha256",
            "failure_operation",
            "recovery_failure_operation",
            "failed_case_id",
            "failure_http_status",
            "recovery_failure_http_status",
        }
        if "quality_failure_code" not in self.model_fields_set:
            compatibility_exclude.add("quality_failure_code")
        legacy_payload = self.model_dump(
            mode="json",
            exclude=compatibility_exclude,
        )
        expected_digests = {
            semantic_sha256(full_payload),
            semantic_sha256(legacy_payload),
        }
        if self.closure_sha256 not in expected_digests:
            raise ValueError("capture campaign closure digest differs")
        return self


class CaptureLifecycle(Protocol):
    def admit(self) -> None: ...
    def start(self) -> None: ...
    def wait_ready(self) -> None: ...
    def observe_baseline_memory(self) -> EmailMemoryObservation: ...
    def apply_email_calibration(self, variant: str) -> None: ...
    def observe_email_calibration(self, variant: str) -> EmailMemoryObservation: ...
    def apply_case(
        self, case: CaptureCasePlan, *, selected_email_variant: str
    ) -> None: ...
    def capture_case(self, case: CaptureCasePlan) -> str: ...
    def restore_baseline(self) -> None: ...
    def verify_baseline(self) -> None: ...
    def cleanup(self, *, baseline_restored: bool) -> dict[str, object]: ...


def run_capture_campaign_attempt(
    *, plan: CaptureCampaignPlan, lifecycle: CaptureLifecycle
) -> CaptureCampaignClosure:
    """Run one create-once campaign lifecycle with reset and cleanup always attempted."""

    plan = CaptureCampaignPlan.model_validate(plan.model_dump())
    failure: CaptureFailureCode | None = None
    cleanup_failure: CaptureFailureCode | None = None
    failure_operation: CaptureFailureOperation | None = None
    recovery_failure_operation: CaptureFailureOperation | None = None
    failure_http_status: int | None = None
    recovery_failure_http_status: int | None = None
    failed_case_id: str | None = None
    quality_failure_code: CaptureCaseQualityFailureCode | None = None
    started = False
    baseline_restored = False
    baseline_memory: EmailMemoryObservation | None = None
    trials: list[EmailCalibrationTrial] = []
    selected: str | None = None
    captured: list[str] = []
    cleanup_attempted = False
    cleanup: dict[str, object] = {
        "verdict": "NOT_ATTEMPTED",
        "owned_containers": None,
        "owned_networks": None,
        "owned_volumes": None,
        "non_owned_resources_changed": None,
    }
    try:
        try:
            lifecycle.admit()
        except Exception:
            failure = CaptureFailureCode.ADMISSION_FAILED
            failure_operation = CaptureFailureOperation.ADMIT
        if failure is None:
            try:
                # A start call may have mutated owned state before raising.
                # Once dispatch begins, cleanup authority must be retained.
                started = True
                lifecycle.start()
            except Exception:
                failure = CaptureFailureCode.START_FAILED
                failure_operation = CaptureFailureOperation.START
        if failure is None:
            try:
                lifecycle.wait_ready()
            except Exception:
                failure = CaptureFailureCode.START_FAILED
                failure_operation = CaptureFailureOperation.WAIT_READY
        if failure is None:
            try:
                lifecycle.verify_baseline()
                baseline_restored = True
            except Exception:
                failure = CaptureFailureCode.START_FAILED
                failure_operation = CaptureFailureOperation.VERIFY_BASELINE

        if failure is None:
            try:
                baseline_memory = EmailMemoryObservation.model_validate(
                    lifecycle.observe_baseline_memory().model_dump()
                )
            except Exception:
                failure = CaptureFailureCode.EMAIL_CALIBRATION_FAILED
                failure_operation = CaptureFailureOperation.OBSERVE_BASELINE_MEMORY
        if failure is None:
            for variant in plan.email_calibration_variants:
                baseline_restored = False
                try:
                    lifecycle.apply_email_calibration(variant)
                except Exception:
                    failure = CaptureFailureCode.EMAIL_CALIBRATION_FAILED
                    failure_operation = (
                        CaptureFailureOperation.APPLY_EMAIL_CALIBRATION
                    )
                    break
                try:
                    observation = EmailMemoryObservation.model_validate(
                        lifecycle.observe_email_calibration(variant).model_dump()
                    )
                except Exception:
                    failure = CaptureFailureCode.EMAIL_CALIBRATION_FAILED
                    failure_operation = (
                        CaptureFailureOperation.OBSERVE_EMAIL_CALIBRATION
                    )
                    break
                safe = (
                    observation.maximum_memory_bytes
                    <= plan.email_maximum_memory_bytes
                    and observation.memory_delta_bytes
                    <= plan.email_maximum_delta_bytes
                    and observation.maximum_slope_bytes_per_second
                    <= plan.email_maximum_slope_bytes_per_second
                )
                measurable = (
                    observation.memory_delta_bytes
                    >= plan.email_minimum_measurable_delta_bytes
                )
                trials.append(
                    EmailCalibrationTrial(
                        variant=variant,
                        observation=observation,
                        measurable=measurable,
                        safe=safe,
                    )
                )
                try:
                    lifecycle.restore_baseline()
                    lifecycle.verify_baseline()
                    baseline_restored = True
                except Exception as error:
                    failure = CaptureFailureCode.BASELINE_RESTORE_FAILED
                    failure_operation = _failure_operation(
                        error, default=CaptureFailureOperation.RESTORE_BASELINE
                    )
                    break
                if not safe:
                    break
                if measurable:
                    selected = variant
            if selected is None and failure is None:
                failure = CaptureFailureCode.EMAIL_CALIBRATION_FAILED

        if failure is None:
            assert selected is not None
            for case in plan.cases:
                baseline_restored = False
                try:
                    lifecycle.apply_case(
                        case, selected_email_variant=selected
                    )
                except Exception as error:
                    failure = CaptureFailureCode.CASE_CAPTURE_FAILED
                    failure_operation = _failure_operation(
                        error, default=CaptureFailureOperation.APPLY_CASE
                    )
                    failure_http_status = _failure_http_status(error)
                    failed_case_id = case.case_id
                    break
                try:
                    digest = lifecycle.capture_case(case)
                    if (
                        not isinstance(digest, str)
                        or len(digest) != 64
                        or any(item not in "0123456789abcdef" for item in digest)
                    ):
                        raise ValueError("capture case digest is invalid")
                    captured.append(digest)
                except Exception as error:
                    failure = CaptureFailureCode.CASE_CAPTURE_FAILED
                    failure_operation = CaptureFailureOperation.CAPTURE_CASE
                    failed_case_id = case.case_id
                    if isinstance(error, CaptureCaseQualityFailure):
                        quality_failure_code = error.code
                    break
                try:
                    lifecycle.restore_baseline()
                    lifecycle.verify_baseline()
                    baseline_restored = True
                except Exception as error:
                    failure = CaptureFailureCode.BASELINE_RESTORE_FAILED
                    failure_operation = _failure_operation(
                        error, default=CaptureFailureOperation.RESTORE_BASELINE
                    )
                    failed_case_id = case.case_id
                    break
    finally:
        if started:
            if not baseline_restored:
                try:
                    lifecycle.restore_baseline()
                    lifecycle.verify_baseline()
                    baseline_restored = True
                except Exception as error:
                    baseline_restored = False
                    recovery_failure_operation = _failure_operation(
                        error,
                        default=CaptureFailureOperation.RESTORE_BASELINE,
                    )
                    recovery_failure_http_status = _failure_http_status(error)
            cleanup_attempted = True
            try:
                cleanup = lifecycle.cleanup(
                    baseline_restored=baseline_restored
                )
                if cleanup.get("verdict") != "CLEAN":
                    cleanup_failure = CaptureFailureCode.CLEANUP_BLOCKED
            except Exception:
                cleanup_failure = CaptureFailureCode.CLEANUP_FAILED
                if failure_operation is None:
                    failure_operation = CaptureFailureOperation.CLEANUP
                cleanup = {
                    "verdict": "BLOCKED",
                    "owned_containers": None,
                    "owned_networks": None,
                    "owned_volumes": None,
                    "non_owned_resources_changed": None,
                }

    payload: dict[str, Any] = {
        "schema_version": "dta-v2.capture-campaign-closure.v1",
        "plan_sha256": plan.plan_sha256,
        "terminal": (
            CaptureTerminal.PASS
            if failure is None and cleanup_failure is None
            else CaptureTerminal.BLOCKED
        ),
        "failure_code": failure,
        "cleanup_failure_code": cleanup_failure,
        "failure_operation": failure_operation,
        "recovery_failure_operation": recovery_failure_operation,
        "failure_http_status": failure_http_status,
        "recovery_failure_http_status": recovery_failure_http_status,
        "failed_case_id": failed_case_id,
        "quality_failure_code": quality_failure_code,
        "baseline_memory": baseline_memory,
        "calibration_observations": tuple(trials),
        "selected_email_variant": selected,
        "captured_case_sha256s": tuple(captured),
        "baseline_restored": baseline_restored,
        "cleanup_attempted": cleanup_attempted,
        "cleanup_verdict": cleanup.get("verdict", "BLOCKED"),
        "owned_containers_after": cleanup.get("owned_containers"),
        "owned_networks_after": cleanup.get("owned_networks"),
        "owned_volumes_after": cleanup.get("owned_volumes"),
        "non_owned_resources_changed": cleanup.get(
            "non_owned_resources_changed"
        ),
        "prohibited_action_counters": CaptureProhibitedActionCounters(),
    }
    draft = CaptureCampaignClosure.model_construct(
        **payload, closure_sha256="0" * 64
    )
    return CaptureCampaignClosure.model_validate(
        {
            **payload,
            "closure_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"closure_sha256"})
            ),
        }
    )


__all__ = [
    "CaptureCampaignClosure",
    "CaptureCampaignPlan",
    "CaptureCasePlan",
    "CaptureCondition",
    "CaptureFailureCode",
    "CaptureFailureOperation",
    "CaptureOperationFailure",
    "CaptureProhibitedActionCounters",
    "CaptureTerminal",
    "EmailCalibrationTrial",
    "EmailMemoryObservation",
    "OperationalFamily",
    "build_default_capture_plan",
    "run_capture_campaign_attempt",
]
