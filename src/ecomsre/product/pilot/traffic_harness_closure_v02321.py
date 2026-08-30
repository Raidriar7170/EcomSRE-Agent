"""Session, attempt, and cleanup contracts for Product v0.2.3.2.1."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Annotated, Any, Callable, Literal, TypeAlias, TypeVar

from pydantic import ConfigDict, Field, TypeAdapter, field_validator, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre_live_sandbox.contracts import canonical_json_bytes


PREFLIGHT_CLOSURE_CONTRACT_PASS_V02321 = (
    "ECOMSRE_PRODUCT_V02321_PREFLIGHT_CLOSURE_CONTRACT_PASS"
)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SESSION_ID_PATTERN = r"^session-[0-9a-f]{32}$"
_ATTEMPT_ID_PATTERN = r"^attempt-[0-9a-f]{32}$"
_ReturnT = TypeVar("_ReturnT")


class TrafficHarnessStageV02321(str, Enum):
    REQUEST_PLAN_CONSTRUCTION = "REQUEST_PLAN_CONSTRUCTION"
    REQUEST_PLAN_VALIDATED = "REQUEST_PLAN_VALIDATED"
    SANDBOX_START_REQUESTED = "SANDBOX_START_REQUESTED"
    SANDBOX_READY = "SANDBOX_READY"
    RUNTIME_AUTHORITY_VERIFICATION_REQUESTED = (
        "RUNTIME_AUTHORITY_VERIFICATION_REQUESTED"
    )
    RUNTIME_AUTHORITY_VERIFIED = "RUNTIME_AUTHORITY_VERIFIED"
    QUEUE_PRESTATE_CAPTURED = "QUEUE_PRESTATE_CAPTURED"
    BASELINE_PRESTATE_CAPTURED = "BASELINE_PRESTATE_CAPTURED"
    RUNTIME_INSPECT_REQUESTED = "RUNTIME_INSPECT_REQUESTED"
    RUNTIME_INSPECTED = "RUNTIME_INSPECTED"
    TRAFFIC_ATTEMPT_CONSUMED = "TRAFFIC_ATTEMPT_CONSUMED"
    FIRST_CART_SEND_REQUESTED = "FIRST_CART_SEND_REQUESTED"
    TRAFFIC_EXECUTION_COMPLETE = "TRAFFIC_EXECUTION_COMPLETE"
    QUEUE_POSTSTATE_CAPTURED = "QUEUE_POSTSTATE_CAPTURED"
    BASELINE_POSTSTATE_CAPTURED = "BASELINE_POSTSTATE_CAPTURED"
    CLEANUP_COMPLETE = "CLEANUP_COMPLETE"


_STAGE_ORDER_V02321 = tuple(TrafficHarnessStageV02321)
_STAGE_RANK_V02321 = {
    stage: ordinal for ordinal, stage in enumerate(_STAGE_ORDER_V02321)
}


def _require_utc_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("traffic preflight timestamp is not ISO-8601") from error
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("traffic preflight timestamp is not UTC")
    return value


def _require_changed_source_paths(value: tuple[str, ...]) -> tuple[str, ...]:
    if tuple(sorted(set(value))) != value or any(
        not item
        or item.startswith("/")
        or ".." in Path(item).parts
        or "\\" in item
        for item in value
    ):
        raise ValueError("changed source paths differ")
    return value


def _sealed_model(
    model: type[ProductModelV1],
    *,
    digest_field: str,
    body: dict[str, Any],
) -> ProductModelV1:
    normalized = json.loads(canonical_json_bytes(body))
    return model.model_validate(
        {**normalized, digest_field: semantic_sha256_v22(normalized)}
    )


def _event_sha256(event: TrafficPreflightEventV02321) -> str:
    if isinstance(event, TrafficHarnessClosureV02321):
        return event.closure_sha256
    return event.event_sha256


def _derive_session_id(
    *,
    request_plan_sha256: str,
    runtime_continuity_descriptor_sha256: str,
    state_clone_sha256: str,
    infrastructure_session_count_after: int,
) -> str:
    return "session-" + semantic_sha256_v22(
        {
            "namespace": "ECOMSRE_PRODUCT_V02321_INFRASTRUCTURE_SESSION",
            "request_plan_sha256": request_plan_sha256,
            "runtime_continuity_descriptor_sha256": (
                runtime_continuity_descriptor_sha256
            ),
            "state_clone_sha256": state_clone_sha256,
            "infrastructure_session_count_after": (
                infrastructure_session_count_after
            ),
        }
    )[:32]


def _derive_attempt_id(
    *,
    session_id: str,
    request_plan_sha256: str,
    traffic_contract_sha256: str,
    traffic_attempt_count_after: int,
) -> str:
    return "attempt-" + semantic_sha256_v22(
        {
            "namespace": "ECOMSRE_PRODUCT_V02321_TRAFFIC_ATTEMPT",
            "session_id": session_id,
            "request_plan_sha256": request_plan_sha256,
            "traffic_contract_sha256": traffic_contract_sha256,
            "traffic_attempt_count_after": traffic_attempt_count_after,
        }
    )[:32]


class OwnedResourceCountsV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    containers: int | None = Field(default=None, ge=0)
    networks: int | None = Field(default=None, ge=0)
    volumes: int | None = Field(default=None, ge=0)
    host_processes: int | None = Field(default=None, ge=0)

    @property
    def all_zero(self) -> bool:
        return all(
            value == 0
            for value in (
                self.containers,
                self.networks,
                self.volumes,
                self.host_processes,
            )
        )


class ChangedSourceBindingV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    size_bytes: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def require_safe_relative_path(cls, value: str) -> str:
        candidate = Path(value)
        if (
            candidate.is_absolute()
            or ".." in candidate.parts
            or "\\" in value
            or candidate.as_posix() != value
        ):
            raise ValueError("changed source path differs")
        return value


def bind_changed_source_files_v02321(
    project_root: Path,
    paths: tuple[str, ...],
) -> tuple[ChangedSourceBindingV02321, ...]:
    root = Path(project_root).resolve(strict=True)
    validated_paths = _require_changed_source_paths(paths)
    bindings: list[ChangedSourceBindingV02321] = []
    for relative in validated_paths:
        source = root / relative
        if source.is_symlink() or not source.is_file():
            raise ValueError("changed source file differs")
        content = source.read_bytes()
        bindings.append(
            ChangedSourceBindingV02321(
                path=relative,
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )
        )
    return tuple(bindings)


def _changed_implementation_sha256(
    bindings: tuple[ChangedSourceBindingV02321, ...],
) -> str:
    return semantic_sha256_v22(
        {
            "changed_source_bindings": [
                item.model_dump(mode="json") for item in bindings
            ]
        }
    )


def _changed_surface_evidence_sha256(
    *,
    prior_attempt_completion_sha256: str | None,
    prior_failure_stage: str | None,
    prior_safe_error_code: str | None,
    prior_implementation_sha256: str | None,
    changed_surface: str,
    changed_implementation_sha256: str,
    repair_rationale: str,
) -> str:
    return semantic_sha256_v22(
        {
            "prior_attempt_completion_sha256": prior_attempt_completion_sha256,
            "prior_failure_stage": prior_failure_stage,
            "prior_safe_error_code": prior_safe_error_code,
            "prior_implementation_sha256": prior_implementation_sha256,
            "changed_surface": changed_surface,
            "changed_implementation_sha256": changed_implementation_sha256,
            "repair_rationale": repair_rationale,
        }
    )


class DemoCleanupObservationV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_complete: bool
    verdict: Literal["CLEAN", "BLOCKED"]
    owned_containers: int | None = Field(default=None, ge=0)
    owned_networks: int | None = Field(default=None, ge=0)
    owned_volumes: int | None = Field(default=None, ge=0)
    non_owned_resources_changed: bool | None = None
    safe_error_code: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def require_truthful_demo_observation(self) -> "DemoCleanupObservationV02321":
        clean = (
            self.observation_complete
            and self.owned_containers == 0
            and self.owned_networks == 0
            and self.owned_volumes == 0
            and self.non_owned_resources_changed is False
            and self.safe_error_code is None
        )
        if (self.verdict == "CLEAN") != clean:
            raise ValueError("Demo cleanup observation verdict differs")
        return self

    @property
    def clean(self) -> bool:
        return self.verdict == "CLEAN"


class ProductCleanupObservationV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_complete: bool
    verdict: Literal["CLEAN", "BLOCKED"]
    owned_host_processes: int | None = Field(default=None, ge=0)
    database_owner_count_before: int | None = Field(default=None, ge=0)
    database_owner_count_after: int | None = Field(default=None, ge=0)
    product_api_port_available: bool | None = None
    non_owned_resources_changed: bool | None = None
    safe_error_code: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def require_truthful_product_observation(
        self,
    ) -> "ProductCleanupObservationV02321":
        clean = (
            self.observation_complete
            and self.owned_host_processes == 0
            and self.database_owner_count_before == 0
            and self.database_owner_count_after == 0
            and self.product_api_port_available is True
            and self.non_owned_resources_changed is False
            and self.safe_error_code is None
        )
        if (self.verdict == "CLEAN") != clean:
            raise ValueError("Product cleanup observation verdict differs")
        return self

    @property
    def clean(self) -> bool:
        return self.verdict == "CLEAN"


class _TrafficPreflightEventBaseV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: str
    event_ordinal: int = Field(ge=1)
    prior_event_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    observed_at_utc: str
    event_sha256: str = Field(pattern=_SHA256_PATTERN)

    _utc_timestamp = field_validator("observed_at_utc")(_require_utc_timestamp)

    @model_validator(mode="after")
    def require_self_sealed_event(self) -> "_TrafficPreflightEventBaseV02321":
        body = self.model_dump(mode="json", exclude={"event_sha256"})
        if self.event_sha256 != semantic_sha256_v22(body):
            raise ValueError("traffic preflight event digest differs")
        return self


class InfrastructureSessionStartV02321(_TrafficPreflightEventBaseV02321):
    event_type: Literal["INFRASTRUCTURE_SESSION_START"] = (
        "INFRASTRUCTURE_SESSION_START"
    )
    session_id: str = Field(pattern=_SESSION_ID_PATTERN)
    request_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_inspect_request_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_continuity_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    state_clone_sha256: str = Field(pattern=_SHA256_PATTERN)
    stage: Literal[TrafficHarnessStageV02321.SANDBOX_START_REQUESTED]
    sandbox_start_requested: Literal[True]
    infrastructure_session_count_after: int = Field(ge=1)

    @model_validator(mode="after")
    def require_semantic_session_id(self) -> "InfrastructureSessionStartV02321":
        expected = _derive_session_id(
            request_plan_sha256=self.request_plan_sha256,
            runtime_continuity_descriptor_sha256=(
                self.runtime_continuity_descriptor_sha256
            ),
            state_clone_sha256=self.state_clone_sha256,
            infrastructure_session_count_after=self.infrastructure_session_count_after,
        )
        if self.session_id != expected:
            raise ValueError("infrastructure session ID binding differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "InfrastructureSessionStartV02321":
        body = {"event_type": "INFRASTRUCTURE_SESSION_START", **payload}
        body.setdefault(
            "session_id",
            _derive_session_id(
                request_plan_sha256=body["request_plan_sha256"],
                runtime_continuity_descriptor_sha256=body[
                    "runtime_continuity_descriptor_sha256"
                ],
                state_clone_sha256=body["state_clone_sha256"],
                infrastructure_session_count_after=body[
                    "infrastructure_session_count_after"
                ],
            ),
        )
        return cls.model_validate(
            _sealed_model(cls, digest_field="event_sha256", body=body)
        )


class InfrastructureSessionCompletionV02321(_TrafficPreflightEventBaseV02321):
    event_type: Literal["INFRASTRUCTURE_SESSION_COMPLETION"] = (
        "INFRASTRUCTURE_SESSION_COMPLETION"
    )
    session_id: str = Field(pattern=_SESSION_ID_PATTERN)
    session_start_sha256: str = Field(pattern=_SHA256_PATTERN)
    closure_sha256: str = Field(pattern=_SHA256_PATTERN)
    stage: Literal[TrafficHarnessStageV02321.CLEANUP_COMPLETE]
    stage_reached: TrafficHarnessStageV02321
    monotonic_duration_ms: int = Field(ge=0)
    infrastructure_session_count_after: int = Field(ge=1)
    cleanup_stage: Literal[
        "NOT_REQUIRED", "REQUESTED", "OBSERVATION_COMPLETE", "BLOCKED"
    ]
    terminal: Literal["SESSION_CLOSED_CLEAN", "SESSION_CLOSED_BLOCKED"]

    @classmethod
    def build(cls, **payload: Any) -> "InfrastructureSessionCompletionV02321":
        body = {"event_type": "INFRASTRUCTURE_SESSION_COMPLETION", **payload}
        return cls.model_validate(
            _sealed_model(cls, digest_field="event_sha256", body=body)
        )


class TrafficPreflightAttemptStartV02321(_TrafficPreflightEventBaseV02321):
    event_type: Literal["TRAFFIC_ATTEMPT_START"] = "TRAFFIC_ATTEMPT_START"
    attempt_id: str = Field(pattern=_ATTEMPT_ID_PATTERN)
    attempt_ordinal: int = Field(ge=1)
    prior_attempt_completion_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    prior_failure_stage: str | None = Field(default=None, min_length=1, max_length=80)
    prior_safe_error_code: str | None = Field(
        default=None, min_length=1, max_length=120
    )
    prior_implementation_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    changed_surface: str = Field(min_length=1, max_length=160)
    changed_surface_evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    changed_source_bindings: tuple[ChangedSourceBindingV02321, ...]
    changed_implementation_sha256: str = Field(pattern=_SHA256_PATTERN)
    repair_rationale: str = Field(min_length=1, max_length=500)
    session_id: str = Field(pattern=_SESSION_ID_PATTERN)
    session_start_sha256: str = Field(pattern=_SHA256_PATTERN)
    request_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_inspect_request_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    endpoint_sha256: str = Field(pattern=_SHA256_PATTERN)
    first_cart_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    queue_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    outer_baseline_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    sandbox_ready: bool
    runtime_authority_equal: bool
    request_plan_equal: bool
    checkout_state: str
    checkout_healthy: bool
    checkout_restart_count: int = Field(ge=0)
    endpoint_validator_ready: bool
    payload_validator_ready: bool
    stage: Literal[TrafficHarnessStageV02321.TRAFFIC_ATTEMPT_CONSUMED]
    traffic_attempt_count_after: int = Field(ge=1)

    @model_validator(mode="after")
    def require_attempt_admission_gates(self) -> "TrafficPreflightAttemptStartV02321":
        if (
            not self.sandbox_ready
            or not self.runtime_authority_equal
            or not self.request_plan_equal
            or self.checkout_state != "RUNNING"
            or not self.checkout_healthy
            or self.checkout_restart_count != 0
            or not self.endpoint_validator_ready
            or not self.payload_validator_ready
        ):
            raise ValueError("traffic attempt admission gates are incomplete")
        if self.attempt_ordinal == 1:
            if (
                self.prior_attempt_completion_sha256 is not None
                or self.prior_failure_stage is not None
                or self.prior_safe_error_code is not None
                or self.prior_implementation_sha256 is not None
                or self.changed_surface != "INITIAL"
            ):
                raise ValueError("initial traffic attempt change binding differs")
        elif (
            self.prior_attempt_completion_sha256 is None
            or self.prior_failure_stage is None
            or self.prior_safe_error_code is None
            or self.prior_implementation_sha256 is None
            or self.changed_surface == "INITIAL"
            or not self.changed_source_bindings
            or self.changed_implementation_sha256
            == self.prior_implementation_sha256
        ):
            raise ValueError("successor traffic attempt lacks changed-surface evidence")
        binding_paths = tuple(item.path for item in self.changed_source_bindings)
        if tuple(sorted(set(binding_paths))) != binding_paths:
            raise ValueError("changed source bindings differ")
        expected_implementation = _changed_implementation_sha256(
            self.changed_source_bindings
        )
        if self.changed_implementation_sha256 != expected_implementation:
            raise ValueError("changed implementation binding differs")
        expected_changed_surface = _changed_surface_evidence_sha256(
            prior_attempt_completion_sha256=self.prior_attempt_completion_sha256,
            prior_failure_stage=self.prior_failure_stage,
            prior_safe_error_code=self.prior_safe_error_code,
            prior_implementation_sha256=self.prior_implementation_sha256,
            changed_surface=self.changed_surface,
            changed_implementation_sha256=self.changed_implementation_sha256,
            repair_rationale=self.repair_rationale,
        )
        if self.changed_surface_evidence_sha256 != expected_changed_surface:
            raise ValueError("changed-surface evidence binding differs")
        expected = _derive_attempt_id(
            session_id=self.session_id,
            request_plan_sha256=self.request_plan_sha256,
            traffic_contract_sha256=self.traffic_contract_sha256,
            traffic_attempt_count_after=self.traffic_attempt_count_after,
        )
        if self.attempt_id != expected:
            raise ValueError("traffic attempt ID binding differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "TrafficPreflightAttemptStartV02321":
        body = {"event_type": "TRAFFIC_ATTEMPT_START", **payload}
        bindings = tuple(
            ChangedSourceBindingV02321.model_validate(item)
            for item in body["changed_source_bindings"]
        )
        body["changed_source_bindings"] = [
            item.model_dump(mode="json") for item in bindings
        ]
        body.setdefault(
            "changed_implementation_sha256",
            _changed_implementation_sha256(bindings),
        )
        body.setdefault(
            "changed_surface_evidence_sha256",
            _changed_surface_evidence_sha256(
                prior_attempt_completion_sha256=body.get(
                    "prior_attempt_completion_sha256"
                ),
                prior_failure_stage=body.get("prior_failure_stage"),
                prior_safe_error_code=body.get("prior_safe_error_code"),
                prior_implementation_sha256=body.get(
                    "prior_implementation_sha256"
                ),
                changed_surface=body["changed_surface"],
                changed_implementation_sha256=body[
                    "changed_implementation_sha256"
                ],
                repair_rationale=body["repair_rationale"],
            ),
        )
        body.setdefault(
            "attempt_id",
            _derive_attempt_id(
                session_id=body["session_id"],
                request_plan_sha256=body["request_plan_sha256"],
                traffic_contract_sha256=body["traffic_contract_sha256"],
                traffic_attempt_count_after=body["traffic_attempt_count_after"],
            ),
        )
        return cls.model_validate(
            _sealed_model(cls, digest_field="event_sha256", body=body)
        )


class TrafficDispatchFailureEvidenceV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.traffic-dispatch-failure.v02321"] = (
        "ecomsre.product.traffic-dispatch-failure.v02321"
    )
    attempt_id: str = Field(pattern=_ATTEMPT_ID_PATTERN)
    endpoint_sha256: str = Field(pattern=_SHA256_PATTERN)
    first_cart_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    transport_invoked: Literal[True]
    remote_delivery: Literal["UNKNOWN"]
    safe_error_code: str = Field(min_length=1, max_length=120)
    dispatch_failure_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_self_sealed_dispatch(self) -> "TrafficDispatchFailureEvidenceV02321":
        body = self.model_dump(mode="json", exclude={"dispatch_failure_sha256"})
        if self.dispatch_failure_sha256 != semantic_sha256_v22(body):
            raise ValueError("traffic dispatch failure digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "TrafficDispatchFailureEvidenceV02321":
        body = {
            "schema_version": "ecomsre.product.traffic-dispatch-failure.v02321",
            **payload,
        }
        return cls.model_validate(
            _sealed_model(cls, digest_field="dispatch_failure_sha256", body=body)
        )


class TrafficPreflightAttemptCompletionV02321(_TrafficPreflightEventBaseV02321):
    event_type: Literal["TRAFFIC_ATTEMPT_COMPLETION"] = (
        "TRAFFIC_ATTEMPT_COMPLETION"
    )
    attempt_id: str = Field(pattern=_ATTEMPT_ID_PATTERN)
    attempt_ordinal: int = Field(ge=1)
    attempt_start_sha256: str = Field(pattern=_SHA256_PATTERN)
    session_id: str = Field(pattern=_SESSION_ID_PATTERN)
    traffic_execution_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    traffic_dispatch_failure: TrafficDispatchFailureEvidenceV02321 | None = None
    stage: Literal[
        TrafficHarnessStageV02321.FIRST_CART_SEND_REQUESTED,
        TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE,
    ]
    first_cart_transport_invoked: Literal[True]
    planned_transactions: Literal[10]
    completed_transactions: int = Field(ge=0, le=10)
    successful_transactions: int = Field(ge=0, le=10)
    failed_transactions: int = Field(ge=0, le=10)
    safe_error_code: str | None = Field(default=None, min_length=1, max_length=120)
    terminal: Literal["ATTEMPT_PASS", "ATTEMPT_FAILED"]
    monotonic_duration_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def require_attempt_completion_truth(
        self,
    ) -> "TrafficPreflightAttemptCompletionV02321":
        if (
            self.successful_transactions + self.failed_transactions
            > self.completed_transactions
        ):
            raise ValueError("traffic attempt transaction counts differ")
        if (self.traffic_execution_sha256 is None) == (
            self.traffic_dispatch_failure is None
        ):
            raise ValueError("traffic attempt evidence cardinality differs")
        if self.traffic_dispatch_failure is not None and (
            self.traffic_dispatch_failure.attempt_id != self.attempt_id
            or self.traffic_dispatch_failure.safe_error_code != self.safe_error_code
            or self.stage
            is not TrafficHarnessStageV02321.FIRST_CART_SEND_REQUESTED
        ):
            raise ValueError("traffic dispatch failure binding differs")
        passed = (
            self.completed_transactions == 10
            and self.successful_transactions == 10
            and self.failed_transactions == 0
            and self.safe_error_code is None
            and self.stage is TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE
            and self.traffic_execution_sha256 is not None
            and self.traffic_dispatch_failure is None
        )
        if (self.terminal == "ATTEMPT_PASS") != passed:
            raise ValueError("traffic attempt completion terminal differs")
        if not passed and self.safe_error_code is None:
            raise ValueError("failed traffic attempt lacks a safe error")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "TrafficPreflightAttemptCompletionV02321":
        body = {"event_type": "TRAFFIC_ATTEMPT_COMPLETION", **payload}
        return cls.model_validate(
            _sealed_model(cls, digest_field="event_sha256", body=body)
        )


class TrafficHarnessClosureV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: Literal["TRAFFIC_HARNESS_CLOSURE"] = "TRAFFIC_HARNESS_CLOSURE"
    event_ordinal: int = Field(ge=1)
    prior_event_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    observed_at_utc: str
    session_id: str | None = Field(default=None, pattern=_SESSION_ID_PATTERN)
    attempt_id: str | None = Field(default=None, pattern=_ATTEMPT_ID_PATTERN)
    stage_reached: TrafficHarnessStageV02321
    observed_stage_sequence: tuple[TrafficHarnessStageV02321, ...] = Field(
        min_length=1
    )
    request_plan_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    queue_before_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    queue_after_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    outer_baseline_before_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    outer_baseline_after_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    runtime_inspect_request_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    traffic_execution_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    traffic_dispatch_failure_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    product_cleanup: ProductCleanupObservationV02321
    demo_cleanup: DemoCleanupObservationV02321
    owned_resource_counts: OwnedResourceCountsV02321
    non_owned_resources_changed: bool | None
    failure_stage: TrafficHarnessStageV02321 | None = None
    safe_error_code: str | None = Field(default=None, min_length=1, max_length=120)
    closure_terminal: Literal[
        "CLEAN_PRE_TRAFFIC",
        "CLEAN_POST_TRAFFIC",
        "BLOCKED_PRESTATE_UNAVAILABLE",
        "BLOCKED_RUNTIME_AUTHORITY",
        "BLOCKED_QUEUE_CHANGED",
        "BLOCKED_BASELINE_CHANGED",
        "BLOCKED_RESOURCE_CLEANUP",
    ]
    closure_sha256: str = Field(pattern=_SHA256_PATTERN)

    _utc_timestamp = field_validator("observed_at_utc")(_require_utc_timestamp)

    @model_validator(mode="after")
    def require_truthful_closure(self) -> "TrafficHarnessClosureV02321":
        stages = self.observed_stage_sequence
        if (
            len(set(stages)) != len(stages)
            or any(
                _STAGE_RANK_V02321[left] >= _STAGE_RANK_V02321[right]
                for left, right in zip(stages, stages[1:])
            )
            or self.stage_reached not in stages
        ):
            raise ValueError("traffic harness stage sequence differs")
        if (self.failure_stage is None) != (self.safe_error_code is None):
            raise ValueError("traffic harness failure binding differs")
        if self.failure_stage is not None and self.failure_stage not in stages:
            raise ValueError("traffic harness failure stage was not observed")

        prestate_values = (
            self.queue_before_sha256,
            self.queue_after_sha256,
            self.outer_baseline_before_sha256,
            self.outer_baseline_after_sha256,
        )
        prestate_complete = all(value is not None for value in prestate_values)
        prestate_partial = (
            any(value is not None for value in prestate_values)
            and not prestate_complete
        )
        queue_equal = (
            prestate_complete and self.queue_before_sha256 == self.queue_after_sha256
        )
        baseline_equal = (
            prestate_complete
            and self.outer_baseline_before_sha256
            == self.outer_baseline_after_sha256
        )
        expected_non_owned: bool | None = None
        if (
            self.product_cleanup.non_owned_resources_changed is not None
            and self.demo_cleanup.non_owned_resources_changed is not None
        ):
            expected_non_owned = (
                self.product_cleanup.non_owned_resources_changed
                or self.demo_cleanup.non_owned_resources_changed
            )
        if (
            self.owned_resource_counts.containers
            != self.demo_cleanup.owned_containers
            or self.owned_resource_counts.networks
            != self.demo_cleanup.owned_networks
            or self.owned_resource_counts.volumes != self.demo_cleanup.owned_volumes
            or self.owned_resource_counts.host_processes
            != self.product_cleanup.owned_host_processes
            or self.non_owned_resources_changed != expected_non_owned
        ):
            raise ValueError("nested and aggregate resource cleanup differ")
        resources_clean = (
            self.product_cleanup.clean
            and self.demo_cleanup.clean
            and self.owned_resource_counts.all_zero
            and self.non_owned_resources_changed is False
        )

        if self.session_id is None:
            if (
                self.attempt_id is not None
                or self.request_plan_sha256 is not None
                or self.stage_reached
                is not TrafficHarnessStageV02321.REQUEST_PLAN_CONSTRUCTION
            ):
                raise ValueError("sessionless closure differs")
        elif self.request_plan_sha256 is None:
            raise ValueError("live closure lacks a typed request plan")

        if self.attempt_id is None:
            if TrafficHarnessStageV02321.TRAFFIC_ATTEMPT_CONSUMED in stages:
                raise ValueError("unconsumed traffic attempt has attempt stage")
            if (
                self.traffic_execution_sha256 is not None
                or self.traffic_dispatch_failure_sha256 is not None
            ):
                raise ValueError("unconsumed traffic attempt has traffic evidence")
        else:
            try:
                attempt_index = stages.index(
                    TrafficHarnessStageV02321.TRAFFIC_ATTEMPT_CONSUMED
                )
                first_cart_index = stages.index(
                    TrafficHarnessStageV02321.FIRST_CART_SEND_REQUESTED
                )
            except ValueError as error:
                raise ValueError("consumed traffic attempt lacks send stages") from error
            if attempt_index + 1 != first_cart_index:
                raise ValueError("traffic attempt was not consumed immediately before Cart")
            if (self.traffic_execution_sha256 is None) == (
                self.traffic_dispatch_failure_sha256 is None
            ):
                raise ValueError("post-traffic closure evidence cardinality differs")
            if self.failure_stage is None and (
                self.traffic_execution_sha256 is None
                or self.traffic_dispatch_failure_sha256 is not None
                or TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE
                not in stages
            ):
                raise ValueError("successful traffic closure evidence differs")

        if self.runtime_inspect_request_sha256 is not None and (
            TrafficHarnessStageV02321.QUEUE_PRESTATE_CAPTURED not in stages
            or TrafficHarnessStageV02321.BASELINE_PRESTATE_CAPTURED not in stages
            or stages.index(TrafficHarnessStageV02321.BASELINE_PRESTATE_CAPTURED)
            > stages.index(TrafficHarnessStageV02321.RUNTIME_INSPECT_REQUESTED)
        ):
            raise ValueError("Runtime inspection preceded queue or Baseline pre-state")

        if self.closure_terminal in {"CLEAN_PRE_TRAFFIC", "CLEAN_POST_TRAFFIC"}:
            if not prestate_complete:
                raise ValueError("clean closure lacks observable pre-state")
            if not queue_equal:
                raise ValueError("clean closure queue changed")
            if not baseline_equal:
                raise ValueError("clean closure Baseline changed")
            if not resources_clean:
                raise ValueError("clean closure resource cleanup differs")
        if self.closure_terminal == "CLEAN_PRE_TRAFFIC" and self.failure_stage is None:
            raise ValueError("pre-traffic failure closure lacks failure evidence")
        if self.closure_terminal == "CLEAN_PRE_TRAFFIC" and self.attempt_id is not None:
            raise ValueError("pre-traffic closure consumed a traffic attempt")
        if self.closure_terminal == "CLEAN_POST_TRAFFIC" and self.attempt_id is None:
            raise ValueError("post-traffic closure lacks a traffic attempt")
        if self.closure_terminal == "BLOCKED_PRESTATE_UNAVAILABLE" and prestate_complete:
            raise ValueError("pre-state blocker has complete pre-state")
        if prestate_partial and self.closure_terminal not in {
            "BLOCKED_PRESTATE_UNAVAILABLE",
            "BLOCKED_RESOURCE_CLEANUP",
        }:
            raise ValueError("partial pre-state was not blocked")
        if self.closure_terminal == "BLOCKED_RUNTIME_AUTHORITY" and (
            self.failure_stage
            is not TrafficHarnessStageV02321.RUNTIME_AUTHORITY_VERIFICATION_REQUESTED
            or self.runtime_inspect_request_sha256 is not None
            or self.attempt_id is not None
            or any(value is not None for value in prestate_values)
        ):
            raise ValueError("Runtime authority blocker evidence differs")
        if self.closure_terminal == "BLOCKED_QUEUE_CHANGED" and (
            not prestate_complete or queue_equal
        ):
            raise ValueError("queue blocker lacks queue drift")
        if self.closure_terminal == "BLOCKED_BASELINE_CHANGED" and (
            not prestate_complete or baseline_equal
        ):
            raise ValueError("Baseline blocker lacks Baseline drift")
        if self.closure_terminal == "BLOCKED_RESOURCE_CLEANUP" and resources_clean:
            raise ValueError("resource cleanup blocker is clean")
        if self.closure_terminal.startswith("BLOCKED_") and self.failure_stage is None:
            raise ValueError("blocked closure lacks failure evidence")
        if not resources_clean and self.closure_terminal != "BLOCKED_RESOURCE_CLEANUP":
            raise ValueError("resource cleanup failure was not blocked")

        body = self.model_dump(mode="json", exclude={"closure_sha256"})
        if self.closure_sha256 != semantic_sha256_v22(body):
            raise ValueError("traffic harness closure digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "TrafficHarnessClosureV02321":
        body = {"event_type": "TRAFFIC_HARNESS_CLOSURE", **payload}
        return cls.model_validate(
            _sealed_model(cls, digest_field="closure_sha256", body=body)
        )


TrafficPreflightEventV02321: TypeAlias = Annotated[
    InfrastructureSessionStartV02321
    | InfrastructureSessionCompletionV02321
    | TrafficPreflightAttemptStartV02321
    | TrafficPreflightAttemptCompletionV02321
    | TrafficHarnessClosureV02321,
    Field(discriminator="event_type"),
]


class TrafficPreflightLedgerV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.traffic-preflight-ledger.v02321"] = (
        "ecomsre.product.traffic-preflight-ledger.v02321"
    )
    events: tuple[TrafficPreflightEventV02321, ...]
    infrastructure_session_count: int = Field(ge=0)
    traffic_attempt_count: int = Field(ge=0)
    head_event_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    ledger_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_append_only_event_chain(self) -> "TrafficPreflightLedgerV02321":
        previous: str | None = None
        seen_event_shas: set[str] = set()
        session_starts: dict[str, InfrastructureSessionStartV02321] = {}
        open_sessions: set[str] = set()
        attempt_starts: dict[str, TrafficPreflightAttemptStartV02321] = {}
        attempt_completions: dict[
            int, TrafficPreflightAttemptCompletionV02321
        ] = {}
        open_attempts: set[str] = set()
        closures: dict[str, TrafficHarnessClosureV02321] = {}

        for ordinal, event in enumerate(self.events, start=1):
            digest = _event_sha256(event)
            if (
                event.event_ordinal != ordinal
                or event.prior_event_sha256 != previous
                or digest in seen_event_shas
            ):
                raise ValueError("traffic preflight ledger event chain differs")
            seen_event_shas.add(digest)
            previous = digest

            if isinstance(event, InfrastructureSessionStartV02321):
                if (
                    event.session_id in session_starts
                    or open_sessions
                    or event.infrastructure_session_count_after
                    != len(session_starts) + 1
                ):
                    raise ValueError("duplicate infrastructure session ID")
                session_starts[event.session_id] = event
                open_sessions.add(event.session_id)
            elif isinstance(event, TrafficPreflightAttemptStartV02321):
                session = session_starts.get(event.session_id)
                previous_completion = attempt_completions.get(
                    event.attempt_ordinal - 1
                )
                previous_start = (
                    attempt_starts.get(previous_completion.attempt_id)
                    if previous_completion is not None
                    else None
                )
                previous_closure = (
                    closures.get(previous_start.session_id)
                    if previous_start is not None
                    else None
                )
                first_attempt = next(iter(attempt_starts.values()), None)
                if (
                    session is None
                    or event.session_id not in open_sessions
                    or event.session_start_sha256 != session.event_sha256
                    or event.request_plan_sha256 != session.request_plan_sha256
                    or event.runtime_inspect_request_sha256
                    != session.runtime_inspect_request_sha256
                    or event.runtime_authority_sha256
                    != session.runtime_continuity_descriptor_sha256
                    or event.attempt_ordinal != len(attempt_starts) + 1
                    or event.traffic_attempt_count_after != event.attempt_ordinal
                    or (
                        event.attempt_ordinal == 1
                        and event.prior_attempt_completion_sha256 is not None
                    )
                    or (
                        event.attempt_ordinal > 1
                        and (
                            previous_completion is None
                            or previous_start is None
                            or previous_closure is None
                            or event.prior_attempt_completion_sha256
                            != previous_completion.event_sha256
                            or previous_completion.terminal != "ATTEMPT_FAILED"
                            or event.prior_failure_stage
                            != previous_completion.stage.value
                            or event.prior_safe_error_code
                            != previous_completion.safe_error_code
                            or event.prior_implementation_sha256
                            != previous_start.changed_implementation_sha256
                            or previous_closure.closure_terminal
                            not in {"CLEAN_PRE_TRAFFIC", "CLEAN_POST_TRAFFIC"}
                        )
                    )
                    or (
                        first_attempt is not None
                        and (
                            event.traffic_contract_sha256
                            != first_attempt.traffic_contract_sha256
                            or event.profile_sha256 != first_attempt.profile_sha256
                            or session.state_clone_sha256
                            != session_starts[first_attempt.session_id].state_clone_sha256
                        )
                    )
                ):
                    raise ValueError("traffic attempt start session binding differs")
                if event.attempt_id in attempt_starts:
                    raise ValueError("duplicate traffic attempt ID")
                attempt_starts[event.attempt_id] = event
                open_attempts.add(event.attempt_id)
            elif isinstance(event, TrafficPreflightAttemptCompletionV02321):
                attempt_start = attempt_starts.get(event.attempt_id)
                if (
                    attempt_start is None
                    or event.attempt_id not in open_attempts
                    or event.attempt_start_sha256 != attempt_start.event_sha256
                    or event.session_id != attempt_start.session_id
                    or event.attempt_ordinal != attempt_start.attempt_ordinal
                    or (
                        event.traffic_dispatch_failure is not None
                        and (
                            event.traffic_dispatch_failure.endpoint_sha256
                            != attempt_start.endpoint_sha256
                            or event.traffic_dispatch_failure.first_cart_payload_sha256
                            != attempt_start.first_cart_payload_sha256
                        )
                    )
                ):
                    raise ValueError("traffic attempt completion binding differs")
                open_attempts.remove(event.attempt_id)
                attempt_completions[event.attempt_ordinal] = event
            elif isinstance(event, TrafficHarnessClosureV02321):
                if event.session_id is None:
                    if session_starts or attempt_starts or closures:
                        raise ValueError("sessionless closure ledger differs")
                else:
                    session_start = session_starts[event.session_id]
                    runtime_inspection_observed = (
                        TrafficHarnessStageV02321.RUNTIME_INSPECT_REQUESTED
                        in event.observed_stage_sequence
                    )
                    if (
                        event.session_id not in open_sessions
                        or event.request_plan_sha256
                        != session_start.request_plan_sha256
                        or (
                            runtime_inspection_observed
                            != (event.runtime_inspect_request_sha256 is not None)
                        )
                        or (
                            runtime_inspection_observed
                            and event.runtime_inspect_request_sha256
                            != session_start.runtime_inspect_request_sha256
                        )
                    ):
                        raise ValueError("closure session binding differs")
                    if event.session_id in closures:
                        raise ValueError("duplicate session closure")
                    if event.attempt_id is not None:
                        attempt_start = attempt_starts.get(event.attempt_id)
                        attempt_completion = (
                            attempt_completions.get(attempt_start.attempt_ordinal)
                            if attempt_start is not None
                            else None
                        )
                        dispatch_sha256 = (
                            attempt_completion.traffic_dispatch_failure.dispatch_failure_sha256
                            if attempt_completion is not None
                            and attempt_completion.traffic_dispatch_failure is not None
                            else None
                        )
                        if (
                            attempt_start is None
                            or attempt_completion is None
                            or attempt_start.session_id != event.session_id
                            or event.attempt_id in open_attempts
                            or event.queue_before_sha256
                            != attempt_start.queue_before_sha256
                            or event.outer_baseline_before_sha256
                            != attempt_start.outer_baseline_before_sha256
                            or event.traffic_execution_sha256
                            != attempt_completion.traffic_execution_sha256
                            or event.traffic_dispatch_failure_sha256
                            != dispatch_sha256
                            or (
                                attempt_completion.terminal == "ATTEMPT_FAILED"
                                and (
                                    event.failure_stage != attempt_completion.stage
                                    or event.safe_error_code
                                    != attempt_completion.safe_error_code
                                )
                            )
                            or (
                                attempt_completion.terminal == "ATTEMPT_PASS"
                                and (
                                    TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE
                                    not in event.observed_stage_sequence
                                    or (
                                        event.failure_stage is not None
                                        and _STAGE_RANK_V02321[event.failure_stage]
                                        <= _STAGE_RANK_V02321[
                                            TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE
                                        ]
                                    )
                                )
                            )
                        ):
                            raise ValueError("closure attempt binding differs")
                    closures[event.session_id] = event
            elif isinstance(event, InfrastructureSessionCompletionV02321):
                completion_session_start = session_starts.get(event.session_id)
                closure = closures.get(event.session_id)
                if (
                    completion_session_start is None
                    or closure is None
                    or event.session_id not in open_sessions
                    or event.session_start_sha256
                    != completion_session_start.event_sha256
                    or event.closure_sha256 != closure.closure_sha256
                    or event.stage_reached != closure.stage_reached
                    or event.infrastructure_session_count_after
                    != completion_session_start.infrastructure_session_count_after
                    or any(
                        attempt_id in open_attempts
                        for attempt_id, attempt in attempt_starts.items()
                        if attempt.session_id == event.session_id
                    )
                ):
                    raise ValueError("infrastructure session completion binding differs")
                clean = closure.closure_terminal in {
                    "CLEAN_PRE_TRAFFIC",
                    "CLEAN_POST_TRAFFIC",
                }
                if (event.terminal == "SESSION_CLOSED_CLEAN") != clean:
                    raise ValueError("infrastructure session terminal differs")
                expected_cleanup_stage = (
                    "BLOCKED"
                    if closure.closure_terminal == "BLOCKED_RESOURCE_CLEANUP"
                    else "OBSERVATION_COMPLETE"
                )
                if event.cleanup_stage != expected_cleanup_stage:
                    raise ValueError("infrastructure cleanup stage differs")
                open_sessions.remove(event.session_id)

        if self.infrastructure_session_count != len(session_starts):
            raise ValueError("infrastructure session count differs")
        if self.traffic_attempt_count != len(attempt_starts):
            raise ValueError("traffic attempt count differs")
        if self.head_event_sha256 != previous:
            raise ValueError("traffic preflight ledger head differs")
        body = self.model_dump(mode="json", exclude={"ledger_sha256"})
        if self.ledger_sha256 != semantic_sha256_v22(body):
            raise ValueError("traffic preflight ledger digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        events: tuple[TrafficPreflightEventV02321, ...],
    ) -> "TrafficPreflightLedgerV02321":
        body: dict[str, Any] = {
            "schema_version": "ecomsre.product.traffic-preflight-ledger.v02321",
            "events": [event.model_dump(mode="json") for event in events],
            "infrastructure_session_count": sum(
                isinstance(event, InfrastructureSessionStartV02321)
                for event in events
            ),
            "traffic_attempt_count": sum(
                isinstance(event, TrafficPreflightAttemptStartV02321)
                for event in events
            ),
            "head_event_sha256": _event_sha256(events[-1]) if events else None,
        }
        return cls.model_validate(
            _sealed_model(cls, digest_field="ledger_sha256", body=body)
        )


class TrafficHarnessFailureInjectionScenarioV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: Literal[
        "REQUEST_PLAN_CONSTRUCTION_FAILURE",
        "SANDBOX_START_FAILURE",
        "RUNTIME_INSPECT_FAILURE",
        "FIRST_CART_SEND_FAILURE",
    ]
    safe_error_code: str = Field(min_length=1, max_length=120)
    expected_infrastructure_session_count: int = Field(ge=0, le=1)
    expected_traffic_attempt_count: int = Field(ge=0, le=1)
    execution_trace: tuple[TrafficHarnessStageV02321, ...] = Field(min_length=2)
    closure: TrafficHarnessClosureV02321
    ledger: TrafficPreflightLedgerV02321
    scenario_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_scenario_bindings(self) -> "TrafficHarnessFailureInjectionScenarioV02321":
        if (
            self.ledger.infrastructure_session_count
            != self.expected_infrastructure_session_count
            or self.ledger.traffic_attempt_count
            != self.expected_traffic_attempt_count
            or self.safe_error_code != self.closure.safe_error_code
            or self.execution_trace != self.closure.observed_stage_sequence
            or self.closure.closure_sha256
            not in {_event_sha256(event) for event in self.ledger.events}
        ):
            raise ValueError("failure-injection scenario binding differs")
        session_starts = sum(
            isinstance(event, InfrastructureSessionStartV02321)
            for event in self.ledger.events
        )
        session_completions = sum(
            isinstance(event, InfrastructureSessionCompletionV02321)
            for event in self.ledger.events
        )
        attempt_starts = sum(
            isinstance(event, TrafficPreflightAttemptStartV02321)
            for event in self.ledger.events
        )
        attempt_completions = sum(
            isinstance(event, TrafficPreflightAttemptCompletionV02321)
            for event in self.ledger.events
        )
        if session_starts != session_completions or attempt_starts != attempt_completions:
            raise ValueError("failure-injection scenario lacks a completed ledger")
        body = self.model_dump(mode="json", exclude={"scenario_sha256"})
        if self.scenario_sha256 != semantic_sha256_v22(body):
            raise ValueError("failure-injection scenario digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "TrafficHarnessFailureInjectionScenarioV02321":
        return cls.model_validate(
            _sealed_model(cls, digest_field="scenario_sha256", body=dict(payload))
        )


class TrafficHarnessClosureContractV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.preflight-closure-contract.v02321"] = (
        "ecomsre.product.preflight-closure-contract.v02321"
    )
    terminal: Literal[
        "ECOMSRE_PRODUCT_V02321_PREFLIGHT_CLOSURE_CONTRACT_PASS"
    ]
    typed_request_plan_terminal: Literal[
        "ECOMSRE_PRODUCT_V02321_TYPED_REQUEST_PLAN_PASS"
    ]
    offline_fixture_request_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    offline_fixture_state_clone_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_continuity_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_contract_sha256: Literal[
        "8e2e6fabb139413ff5ff54efe516023e00f7d04c7b84b4d296b1aa42bf39ce1b"
    ]
    scenarios: tuple[TrafficHarnessFailureInjectionScenarioV02321, ...] = Field(
        min_length=4, max_length=4
    )
    request_plan_failure_consumes_neither: Literal[True]
    sandbox_start_consumes_session_only: Literal[True]
    runtime_failure_consumes_session_only: Literal[True]
    first_cart_send_consumes_attempt: Literal[True]
    queue_baseline_prestate_before_runtime_inspect: Literal[True]
    resource_absence_not_promoted_to_clean: Literal[True]
    append_only_ledger: Literal[True]
    live_authorization: Literal[False]
    infrastructure_session_count: Literal[0]
    traffic_attempt_count: Literal[0]
    formal_healthy_traffic_execution_count: Literal[0]
    contract_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_complete_contract(self) -> "TrafficHarnessClosureContractV02321":
        expected = (
            ("REQUEST_PLAN_CONSTRUCTION_FAILURE", 0, 0),
            ("SANDBOX_START_FAILURE", 1, 0),
            ("RUNTIME_INSPECT_FAILURE", 1, 0),
            ("FIRST_CART_SEND_FAILURE", 1, 1),
        )
        observed = tuple(
            (
                item.scenario_id,
                item.expected_infrastructure_session_count,
                item.expected_traffic_attempt_count,
            )
            for item in self.scenarios
        )
        if observed != expected:
            raise ValueError("preflight closure scenario matrix differs")
        body = self.model_dump(mode="json", exclude={"contract_sha256"})
        if self.contract_sha256 != semantic_sha256_v22(body):
            raise ValueError("preflight closure contract digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "TrafficHarnessClosureContractV02321":
        body = {
            "schema_version": "ecomsre.product.preflight-closure-contract.v02321",
            **payload,
        }
        return cls.model_validate(
            _sealed_model(cls, digest_field="contract_sha256", body=body)
        )


def append_traffic_preflight_event_file_v02321(
    project_root: Path,
    private_attempt_id: str,
    event: TrafficPreflightEventV02321,
) -> Path:
    """Append one event under the private successor ledger root."""

    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", private_attempt_id) is None:
        raise ValueError("private traffic preflight attempt ID differs")
    root = Path(project_root).resolve(strict=True)
    ledger_root = (
        root
        / ".local"
        / "product-v02321"
        / "traffic-preflight"
        / private_attempt_id
        / "ledger"
    )
    cursor = root
    for component in ledger_root.relative_to(root).parts:
        cursor = cursor / component
        if cursor.is_symlink() or (cursor.exists() and not cursor.is_dir()):
            raise ValueError("traffic preflight ledger root differs")
    ledger_root.mkdir(parents=True, exist_ok=True)

    adapter: TypeAdapter[TrafficPreflightEventV02321] = TypeAdapter(
        TrafficPreflightEventV02321
    )
    persisted: list[TrafficPreflightEventV02321] = []
    children = sorted(ledger_root.iterdir())
    for expected_ordinal, path in enumerate(children, start=1):
        if (
            path.is_symlink()
            or not path.is_file()
            or path.name != f"event-{expected_ordinal:06d}.json"
        ):
            raise ValueError("traffic preflight persisted event sequence differs")
        persisted.append(adapter.validate_json(path.read_bytes()))
    if persisted:
        TrafficPreflightLedgerV02321.build(events=tuple(persisted))
    expected_prior = _event_sha256(persisted[-1]) if persisted else None
    if (
        event.event_ordinal != len(persisted) + 1
        or event.prior_event_sha256 != expected_prior
    ):
        raise ValueError("traffic preflight append head differs")
    TrafficPreflightLedgerV02321.build(events=(*persisted, event))

    destination = ledger_root / f"event-{event.event_ordinal:06d}.json"
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(event.model_dump(mode="json")))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def request_sandbox_start_v02321(
    session_start: InfrastructureSessionStartV02321,
    *,
    persist_start: Callable[[InfrastructureSessionStartV02321], object],
    request_start: Callable[[], _ReturnT],
) -> _ReturnT:
    """Persist session admission immediately before requesting Sandbox start."""

    persist_start(session_start)
    return request_start()


def invoke_first_cart_transport_v02321(
    attempt_start: TrafficPreflightAttemptStartV02321,
    *,
    persist_start: Callable[[TrafficPreflightAttemptStartV02321], object],
    invoke_transport: Callable[[], _ReturnT],
) -> _ReturnT:
    """Persist attempt admission immediately before the first Cart transport call."""

    persist_start(attempt_start)
    return invoke_transport()


__all__ = (
    "ChangedSourceBindingV02321",
    "InfrastructureSessionCompletionV02321",
    "InfrastructureSessionStartV02321",
    "DemoCleanupObservationV02321",
    "OwnedResourceCountsV02321",
    "PREFLIGHT_CLOSURE_CONTRACT_PASS_V02321",
    "ProductCleanupObservationV02321",
    "TrafficHarnessClosureContractV02321",
    "TrafficHarnessClosureV02321",
    "TrafficHarnessFailureInjectionScenarioV02321",
    "TrafficHarnessStageV02321",
    "TrafficDispatchFailureEvidenceV02321",
    "TrafficPreflightAttemptCompletionV02321",
    "TrafficPreflightAttemptStartV02321",
    "TrafficPreflightEventV02321",
    "TrafficPreflightLedgerV02321",
    "append_traffic_preflight_event_file_v02321",
    "bind_changed_source_files_v02321",
    "invoke_first_cart_transport_v02321",
    "request_sandbox_start_v02321",
)
