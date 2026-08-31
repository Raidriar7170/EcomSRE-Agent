"""One-shot formal No-Fault contracts for Product v0.2.3.2.1."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.connectors.pilot_runtime import PilotRuntimeSnapshotV02
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    HealthyTrafficExecutionV0232,
    CheckoutTransactionObservationV0232,
)
from ecomsre.product.pilot.nofault_acceptance_v0232 import (
    NoFaultMeasuredTerminalV0232,
)
from ecomsre.product.pilot.runtime_session_v0231 import (
    BaselineRestartProofV0231,
    RuntimeAuthorityContinuityProofV0231,
)
from ecomsre.product.pilot.traffic_harness_closure_v02321 import (
    DemoCleanupObservationV02321,
    ProductCleanupObservationV02321,
)


RUNTIME_AUTHORITY_CONTINUITY_PASS_V02321: Literal[
    "ECOMSRE_PRODUCT_V02321_RUNTIME_AUTHORITY_CONTINUITY_PASS"
] = "ECOMSRE_PRODUCT_V02321_RUNTIME_AUTHORITY_CONTINUITY_PASS"
BASELINE_RESTART_PASS_V02321: Literal[
    "ECOMSRE_PRODUCT_V02321_BASELINE_RESTART_PASS"
] = "ECOMSRE_PRODUCT_V02321_BASELINE_RESTART_PASS"
FORMAL_HEALTHY_TRAFFIC_PASS_V02321: Literal[
    "ECOMSRE_PRODUCT_V02321_FORMAL_HEALTHY_TRAFFIC_PASS"
] = "ECOMSRE_PRODUCT_V02321_FORMAL_HEALTHY_TRAFFIC_PASS"
NOFAULT_ACCEPTANCE_COMPLETE_V02321: Literal[
    "ECOMSRE_PRODUCT_V02321_NOFAULT_ACCEPTANCE_COMPLETE"
] = "ECOMSRE_PRODUCT_V02321_NOFAULT_ACCEPTANCE_COMPLETE"
NOFAULT_FULLY_SUPPORTED_V02321: Literal[
    "ECOMSRE_PRODUCT_V02321_NOFAULT_FULLY_SUPPORTED"
] = "ECOMSRE_PRODUCT_V02321_NOFAULT_FULLY_SUPPORTED"
NOFAULT_CAPABILITY_LIMITED_V02321: Literal[
    "ECOMSRE_PRODUCT_V02321_NOFAULT_CAPABILITY_LIMITED"
] = "ECOMSRE_PRODUCT_V02321_NOFAULT_CAPABILITY_LIMITED"
NOFAULT_NOT_SUPPORTED_V02321: Literal[
    "ECOMSRE_PRODUCT_V02321_NOFAULT_NOT_SUPPORTED"
] = "ECOMSRE_PRODUCT_V02321_NOFAULT_NOT_SUPPORTED"

MeasuredTerminalV02321 = Literal[
    "ECOMSRE_PRODUCT_V02321_NOFAULT_FULLY_SUPPORTED",
    "ECOMSRE_PRODUCT_V02321_NOFAULT_CAPABILITY_LIMITED",
    "ECOMSRE_PRODUCT_V02321_NOFAULT_NOT_SUPPORTED",
]

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_HEAD_PATTERN = r"^[0-9a-f]{40}$"


def _sealed_payload(model: ProductModelV1, seal: str) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude={seal})


def _require_seal(model: ProductModelV1, seal: str, *, label: str) -> None:
    expected = semantic_sha256_v22(_sealed_payload(model, seal))
    if getattr(model, seal) != expected:
        raise ValueError(f"Product v0.2.3.2.1 {label} digest differs")


def measured_terminal_v02321(
    terminal: NoFaultMeasuredTerminalV0232,
) -> MeasuredTerminalV02321:
    mapping: dict[NoFaultMeasuredTerminalV0232, MeasuredTerminalV02321] = {
        NoFaultMeasuredTerminalV0232.FULLY_SUPPORTED: (NOFAULT_FULLY_SUPPORTED_V02321),
        NoFaultMeasuredTerminalV0232.CAPABILITY_LIMITED: (
            NOFAULT_CAPABILITY_LIMITED_V02321
        ),
        NoFaultMeasuredTerminalV0232.NOT_SUPPORTED: NOFAULT_NOT_SUPPORTED_V02321,
    }
    return mapping[terminal]


class FormalExecutionAdmissionV02321(ProductModelV1):
    """Self-sealed proof that the strict clean-state review gate passed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-admission.v02321"] = (
        "ecomsre.product.formal-admission.v02321"
    )
    execution_head: str = Field(pattern=_HEAD_PATTERN)
    formal_contract_freeze_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_contract_freeze_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    pre_execution_review_sha256: str = Field(pattern=_SHA256_PATTERN)
    pre_execution_review_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_state_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_clone_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_clone_destination_locator: str
    formal_runner_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_contract_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_continuity_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_continuity_descriptor_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_healthy_traffic_execution_count: Literal[0] = 0
    accepted_successor_incident_count: Literal[0] = 0
    successor_diagnosis_count: Literal[0] = 0
    fault_attempt_count: Literal[0] = 0
    knowledge_loop_campaign_count: Literal[0] = 0
    agent_writes: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    provider_calls: Literal[0] = 0
    action_authority: Literal["NONE"] = "NONE"
    admission_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("formal_clone_destination_locator")
    @classmethod
    def require_formal_clone_locator(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or len(path.parts) != 5
            or path.parts[:3] != (".local", "product-v02321", "product-state")
            or not path.parts[3].startswith("formal-")
            or path.parts[4] != "product"
        ):
            raise ValueError("Product v0.2.3.2.1 formal clone locator differs")
        return value

    @model_validator(mode="after")
    def require_sealed_admission(self) -> "FormalExecutionAdmissionV02321":
        _require_seal(self, "admission_sha256", label="formal admission")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "FormalExecutionAdmissionV02321":
        body = {
            "schema_version": "ecomsre.product.formal-admission.v02321",
            **payload,
            "formal_healthy_traffic_execution_count": 0,
            "accepted_successor_incident_count": 0,
            "successor_diagnosis_count": 0,
            "fault_attempt_count": 0,
            "knowledge_loop_campaign_count": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
            "provider_calls": 0,
            "action_authority": "NONE",
        }
        return cls.model_validate(
            {**body, "admission_sha256": semantic_sha256_v22(body)}
        )


class FormalCloneReservationV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-clone-reservation.v02321"] = (
        "ecomsre.product.formal-clone-reservation.v02321"
    )
    admission: FormalExecutionAdmissionV02321
    stage: Literal["RESERVED_BEFORE_CLONE"] = "RESERVED_BEFORE_CLONE"
    formal_clone_created: Literal[False] = False
    formal_healthy_traffic_execution_count: Literal[0] = 0
    accepted_successor_incident_count: Literal[0] = 0
    successor_diagnosis_count: Literal[0] = 0
    reservation_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_sealed_reservation(self) -> "FormalCloneReservationV02321":
        _require_seal(self, "reservation_sha256", label="formal clone reservation")
        return self

    @classmethod
    def build(
        cls, *, admission: FormalExecutionAdmissionV02321
    ) -> "FormalCloneReservationV02321":
        body = {
            "schema_version": "ecomsre.product.formal-clone-reservation.v02321",
            "admission": admission.model_dump(mode="json"),
            "stage": "RESERVED_BEFORE_CLONE",
            "formal_clone_created": False,
            "formal_healthy_traffic_execution_count": 0,
            "accepted_successor_incident_count": 0,
            "successor_diagnosis_count": 0,
        }
        return cls.model_validate(
            {**body, "reservation_sha256": semantic_sha256_v22(body)}
        )


class FormalBlockerClosureV02321(ProductModelV1):
    """Typed cleanup and source-state evidence frozen before any blocker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-blocker-closure.v02321"] = (
        "ecomsre.product.formal-blocker-closure.v02321"
    )
    product_cleanup: ProductCleanupObservationV02321
    demo_cleanup: DemoCleanupObservationV02321
    evidence_origin: Literal["LIVE_OBSERVATION", "RECOVERY_UNPROVEN"]
    queue_state_status: Literal["OBSERVED", "PARTIAL", "UNPROVEN"]
    queue_before_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    queue_after_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    outer_baseline_state_status: Literal["OBSERVED", "PARTIAL", "UNPROVEN"]
    outer_baseline_before_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    outer_baseline_after_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    source_state_status: Literal["UNCHANGED", "CHANGED", "UNPROVEN"]
    source_state_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_state_after_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    verdict: Literal["CLEAN", "BLOCKED"]
    closure_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_truthful_closure(self) -> "FormalBlockerClosureV02321":
        def pair_exact(
            status: str,
            before: str | None,
            after: str | None,
        ) -> bool:
            if status == "UNPROVEN":
                return before is None and after is None
            if status == "PARTIAL":
                return (before is None) != (after is None)
            return before is not None and after is not None

        if not pair_exact(
            self.queue_state_status,
            self.queue_before_sha256,
            self.queue_after_sha256,
        ) or not pair_exact(
            self.outer_baseline_state_status,
            self.outer_baseline_before_sha256,
            self.outer_baseline_after_sha256,
        ):
            raise ValueError("Product v0.2.3.2.1 blocker prestate differs")
        source_clean = (
            self.source_state_status == "UNCHANGED"
            and self.source_state_after_sha256 == self.source_state_before_sha256
        )
        if self.source_state_status == "UNPROVEN":
            source_exact = self.source_state_after_sha256 is None
        elif self.source_state_status == "UNCHANGED":
            source_exact = source_clean
        else:
            source_exact = (
                self.source_state_after_sha256 is not None
                and self.source_state_after_sha256 != self.source_state_before_sha256
            )
        queue_clean = (
            self.queue_state_status == "OBSERVED"
            and self.queue_before_sha256 == self.queue_after_sha256
        )
        baseline_clean = (
            self.outer_baseline_state_status == "OBSERVED"
            and self.outer_baseline_before_sha256 == self.outer_baseline_after_sha256
        )
        clean = (
            self.product_cleanup.clean
            and self.demo_cleanup.clean
            and queue_clean
            and baseline_clean
            and source_clean
        )
        recovery_unproven = (
            self.evidence_origin == "RECOVERY_UNPROVEN"
            and self.queue_state_status == "UNPROVEN"
            and self.outer_baseline_state_status == "UNPROVEN"
            and self.source_state_status == "UNPROVEN"
            and not self.product_cleanup.observation_complete
            and not self.demo_cleanup.observation_complete
            and self.verdict == "BLOCKED"
        )
        if (
            not source_exact
            or (self.verdict == "CLEAN") != clean
            or (self.evidence_origin == "RECOVERY_UNPROVEN" and not recovery_unproven)
        ):
            raise ValueError("Product v0.2.3.2.1 blocker closure differs")
        _require_seal(self, "closure_sha256", label="formal blocker closure")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "FormalBlockerClosureV02321":
        product = ProductCleanupObservationV02321.model_validate(
            payload["product_cleanup"]
        )
        demo = DemoCleanupObservationV02321.model_validate(payload["demo_cleanup"])
        source_status = payload["source_state_status"]
        source_before = payload["source_state_before_sha256"]
        source_after = payload.get("source_state_after_sha256")
        queue_status = payload["queue_state_status"]
        queue_before = payload.get("queue_before_sha256")
        queue_after = payload.get("queue_after_sha256")
        baseline_status = payload["outer_baseline_state_status"]
        baseline_before = payload.get("outer_baseline_before_sha256")
        baseline_after = payload.get("outer_baseline_after_sha256")
        verdict = (
            "CLEAN"
            if product.clean
            and demo.clean
            and queue_status == "OBSERVED"
            and queue_before == queue_after
            and baseline_status == "OBSERVED"
            and baseline_before == baseline_after
            and source_status == "UNCHANGED"
            and source_after == source_before
            else "BLOCKED"
        )
        body = {
            "schema_version": "ecomsre.product.formal-blocker-closure.v02321",
            "product_cleanup": product.model_dump(mode="json"),
            "demo_cleanup": demo.model_dump(mode="json"),
            "evidence_origin": payload["evidence_origin"],
            "queue_state_status": queue_status,
            "queue_before_sha256": queue_before,
            "queue_after_sha256": queue_after,
            "outer_baseline_state_status": baseline_status,
            "outer_baseline_before_sha256": baseline_before,
            "outer_baseline_after_sha256": baseline_after,
            "source_state_status": source_status,
            "source_state_before_sha256": source_before,
            "source_state_after_sha256": source_after,
            "verdict": verdict,
        }
        return cls.model_validate({**body, "closure_sha256": semantic_sha256_v22(body)})


class FormalExecutionBlockerV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-blocker.v02321"] = (
        "ecomsre.product.formal-blocker.v02321"
    )
    terminal: Literal["BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE"] = (
        "BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE"
    )
    execution_head: str = Field(pattern=_HEAD_PATTERN)
    admission_sha256: str = Field(pattern=_SHA256_PATTERN)
    stage: Literal[
        "PROCESS_INTERRUPTED_AFTER_FORMAL_START",
        "PROCESS_INTERRUPTED_AFTER_FORMAL_TRAFFIC_PASS",
    ]
    safe_error_code: str = Field(min_length=1, max_length=1000)
    formal_healthy_traffic_execution_count: Literal[0, 1]
    observed_state_status: Literal["OBSERVED", "UNAVAILABLE"]
    starting_incident_count: Literal[1] = 1
    starting_diagnosis_count: Literal[1] = 1
    observed_incident_count: int | None = Field(default=None, ge=1, le=2)
    observed_diagnosis_count: int | None = Field(default=None, ge=1, le=2)
    observed_diagnosis_job_count: int | None = Field(default=None, ge=1, le=2)
    observed_fault_family_count: int | None = Field(default=None, ge=0)
    observed_knowledge_artifact_count: int | None = Field(default=None, ge=0)
    observed_provider_calls: int | None = Field(default=None, ge=0)
    observed_agent_writes: int | None = Field(default=None, ge=0)
    observed_runbook_executions: int | None = Field(default=None, ge=0)
    accepted_successor_incident_count: int | None = Field(default=None, ge=0, le=1)
    successor_diagnosis_count: int | None = Field(default=None, ge=0, le=1)
    closure: FormalBlockerClosureV02321
    action_authority: str | None = Field(default=None, min_length=1, max_length=120)
    blocker_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_truthful_observation(self) -> "FormalExecutionBlockerV02321":
        observed = self.observed_state_status == "OBSERVED"
        fields = (
            self.observed_incident_count,
            self.observed_diagnosis_count,
            self.observed_diagnosis_job_count,
            self.observed_fault_family_count,
            self.observed_knowledge_artifact_count,
            self.observed_provider_calls,
            self.observed_agent_writes,
            self.observed_runbook_executions,
            self.accepted_successor_incident_count,
            self.successor_diagnosis_count,
            self.action_authority,
        )
        if observed != all(value is not None for value in fields):
            raise ValueError("Product v0.2.3.2.1 blocker observation differs")
        if observed:
            assert self.observed_incident_count is not None
            assert self.observed_diagnosis_count is not None
            assert self.accepted_successor_incident_count is not None
            assert self.successor_diagnosis_count is not None
            if (
                self.accepted_successor_incident_count
                != self.observed_incident_count - self.starting_incident_count
                or self.successor_diagnosis_count
                != self.observed_diagnosis_count - self.starting_diagnosis_count
            ):
                raise ValueError("Product v0.2.3.2.1 blocker cardinality differs")
        _require_seal(self, "blocker_sha256", label="formal blocker")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "FormalExecutionBlockerV02321":
        closure = FormalBlockerClosureV02321.model_validate(payload["closure"])
        body = {
            "schema_version": "ecomsre.product.formal-blocker.v02321",
            "terminal": ("BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE"),
            **payload,
            "closure": closure.model_dump(mode="json"),
            "starting_incident_count": 1,
            "starting_diagnosis_count": 1,
            "observed_fault_family_count": payload.get("observed_fault_family_count"),
            "observed_knowledge_artifact_count": payload.get(
                "observed_knowledge_artifact_count"
            ),
            "observed_provider_calls": payload.get("observed_provider_calls"),
            "observed_agent_writes": payload.get("observed_agent_writes"),
            "observed_runbook_executions": payload.get("observed_runbook_executions"),
            "action_authority": payload.get("action_authority"),
        }
        return cls.model_validate({**body, "blocker_sha256": semantic_sha256_v22(body)})


class FormalTrafficConsumptionV02321(ProductModelV1):
    """Durable one-shot checkpoint written immediately before the first Cart."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-traffic-consumption.v02321"] = (
        "ecomsre.product.formal-traffic-consumption.v02321"
    )
    admission_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_head: str = Field(pattern=_HEAD_PATTERN)
    traffic_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    episode_started_at: datetime
    stage: Literal["CONSUMED_BEFORE_FIRST_CART"] = "CONSUMED_BEFORE_FIRST_CART"
    formal_healthy_traffic_execution_count_before: Literal[0] = 0
    formal_healthy_traffic_execution_count_after: Literal[1] = 1
    accepted_successor_incident_count: Literal[0] = 0
    successor_diagnosis_count: Literal[0] = 0
    consumption_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_sealed_consumption(self) -> "FormalTrafficConsumptionV02321":
        if self.episode_started_at.tzinfo is None:
            raise ValueError("Product v0.2.3.2.1 formal episode time differs")
        _require_seal(self, "consumption_sha256", label="formal traffic consumption")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "FormalTrafficConsumptionV02321":
        body = {
            "schema_version": "ecomsre.product.formal-traffic-consumption.v02321",
            **payload,
            "stage": "CONSUMED_BEFORE_FIRST_CART",
            "formal_healthy_traffic_execution_count_before": 0,
            "formal_healthy_traffic_execution_count_after": 1,
            "accepted_successor_incident_count": 0,
            "successor_diagnosis_count": 0,
        }
        draft = cls.model_construct(**body, consumption_sha256="0" * 64)
        normalized = draft.model_dump(mode="json", exclude={"consumption_sha256"})
        return cls.model_validate(
            {
                **normalized,
                "consumption_sha256": semantic_sha256_v22(normalized),
            }
        )

    @staticmethod
    def require_unconsumed(
        checkpoint: "FormalTrafficConsumptionV02321 | None",
    ) -> None:
        if checkpoint is not None:
            raise ValueError("Product v0.2.3.2.1 formal traffic already consumed")


class FormalTrafficDispatchCheckpointV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-traffic-dispatch.v02321"] = (
        "ecomsre.product.formal-traffic-dispatch.v02321"
    )
    consumption_sha256: str = Field(pattern=_SHA256_PATTERN)
    ordinal: int = Field(ge=1, le=30)
    cart_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    checkout_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    stage: Literal["DISPATCH_REQUESTED"] = "DISPATCH_REQUESTED"
    remote_delivery: Literal["UNKNOWN"] = "UNKNOWN"
    checkpoint_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_sealed_dispatch(self) -> "FormalTrafficDispatchCheckpointV02321":
        _require_seal(self, "checkpoint_sha256", label="formal traffic dispatch")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "FormalTrafficDispatchCheckpointV02321":
        body = {
            "schema_version": "ecomsre.product.formal-traffic-dispatch.v02321",
            **payload,
            "stage": "DISPATCH_REQUESTED",
            "remote_delivery": "UNKNOWN",
        }
        return cls.model_validate(
            {**body, "checkpoint_sha256": semantic_sha256_v22(body)}
        )


class FormalTrafficObservationCheckpointV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-traffic-observation.v02321"] = (
        "ecomsre.product.formal-traffic-observation.v02321"
    )
    consumption_sha256: str = Field(pattern=_SHA256_PATTERN)
    dispatch_checkpoint_sha256: str = Field(pattern=_SHA256_PATTERN)
    observation: CheckoutTransactionObservationV0232
    stage: Literal["OBSERVATION_PERSISTED"] = "OBSERVATION_PERSISTED"
    remote_delivery: Literal["OBSERVED"] = "OBSERVED"
    checkpoint_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_sealed_observation(
        self,
    ) -> "FormalTrafficObservationCheckpointV02321":
        _require_seal(
            self,
            "checkpoint_sha256",
            label="formal traffic observation",
        )
        return self

    @classmethod
    def build(cls, **payload: Any) -> "FormalTrafficObservationCheckpointV02321":
        observation = CheckoutTransactionObservationV0232.model_validate(
            payload["observation"]
        )
        body = {
            "schema_version": "ecomsre.product.formal-traffic-observation.v02321",
            **payload,
            "observation": observation.model_dump(mode="json"),
            "stage": "OBSERVATION_PERSISTED",
            "remote_delivery": "OBSERVED",
        }
        return cls.model_validate(
            {**body, "checkpoint_sha256": semantic_sha256_v22(body)}
        )


class FormalTrafficBlockerV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-traffic-blocker.v02321"] = (
        "ecomsre.product.formal-traffic-blocker.v02321"
    )
    terminal: Literal["BLOCKED_ECOMSRE_PRODUCT_V02321_FORMAL_HEALTHY_TRAFFIC"] = (
        "BLOCKED_ECOMSRE_PRODUCT_V02321_FORMAL_HEALTHY_TRAFFIC"
    )
    execution_head: str = Field(pattern=_HEAD_PATTERN)
    admission_sha256: str = Field(pattern=_SHA256_PATTERN)
    consumption_sha256: str = Field(pattern=_SHA256_PATTERN)
    stage: Literal[
        "CONSUMED_BEFORE_FIRST_CART",
        "DISPATCH_REQUESTED",
        "OBSERVATION_PERSISTED",
        "EXECUTION_RETURNED",
        "EXECUTION_PERSISTENCE_FAILED",
    ]
    traffic_execution: HealthyTrafficExecutionV0232 | None = None
    dispatch_checkpoints: tuple[FormalTrafficDispatchCheckpointV02321, ...]
    observation_checkpoints: tuple[FormalTrafficObservationCheckpointV02321, ...]
    pending_dispatch_ordinal: int | None = Field(default=None, ge=1, le=30)
    remote_delivery: Literal["NOT_STARTED", "UNKNOWN", "OBSERVED"]
    planned_transactions: Literal[30] = 30
    completed_transactions: int = Field(ge=0, le=30)
    successful_transactions: int = Field(ge=0, le=30)
    failed_transactions: int = Field(ge=0, le=30)
    hidden_retry_count: Literal[0] = 0
    safe_error_code: str = Field(min_length=1, max_length=1000)
    closure: FormalBlockerClosureV02321
    fault_family_count: Literal[0] = 0
    knowledge_artifact_count: Literal[0] = 0
    provider_calls: Literal[0] = 0
    agent_writes: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    formal_healthy_traffic_execution_count: Literal[1] = 1
    accepted_successor_incident_count: Literal[0] = 0
    successor_diagnosis_count: Literal[0] = 0
    action_authority: Literal["NONE"] = "NONE"
    blocker_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_total_traffic_blocker(self) -> "FormalTrafficBlockerV02321":
        dispatch_ordinals = tuple(item.ordinal for item in self.dispatch_checkpoints)
        observations = tuple(item.observation for item in self.observation_checkpoints)
        observation_ordinals = tuple(item.ordinal for item in observations)
        successful = sum(item.business_success for item in observations)
        completed = len(observations)
        exact_chain = (
            dispatch_ordinals == tuple(range(1, len(dispatch_ordinals) + 1))
            and observation_ordinals == tuple(range(1, completed + 1))
            and len(dispatch_ordinals) in {completed, completed + 1}
            and all(
                observation.dispatch_checkpoint_sha256
                == self.dispatch_checkpoints[index].checkpoint_sha256
                for index, observation in enumerate(self.observation_checkpoints)
            )
            and all(
                checkpoint.consumption_sha256 == self.consumption_sha256
                for checkpoint in self.dispatch_checkpoints
            )
            and all(
                checkpoint.consumption_sha256 == self.consumption_sha256
                for checkpoint in self.observation_checkpoints
            )
        )
        exact_stage = {
            "CONSUMED_BEFORE_FIRST_CART": (
                not self.dispatch_checkpoints
                and not self.observation_checkpoints
                and self.pending_dispatch_ordinal is None
                and self.remote_delivery == "NOT_STARTED"
                and self.traffic_execution is None
            ),
            "DISPATCH_REQUESTED": (
                len(self.dispatch_checkpoints) == completed + 1
                and self.pending_dispatch_ordinal == completed + 1
                and self.remote_delivery == "UNKNOWN"
                and self.traffic_execution is None
            ),
            "OBSERVATION_PERSISTED": (
                len(self.dispatch_checkpoints) == completed
                and completed > 0
                and self.pending_dispatch_ordinal is None
                and self.remote_delivery == "OBSERVED"
                and self.traffic_execution is None
            ),
            "EXECUTION_RETURNED": (
                len(self.dispatch_checkpoints) == completed
                and completed == 30
                and self.pending_dispatch_ordinal is None
                and self.remote_delivery == "OBSERVED"
                and self.traffic_execution is not None
            ),
            "EXECUTION_PERSISTENCE_FAILED": (
                len(self.dispatch_checkpoints) == completed
                and completed == 30
                and self.pending_dispatch_ordinal is None
                and self.remote_delivery == "OBSERVED"
                and self.traffic_execution is not None
            ),
        }[self.stage]
        if (
            not exact_chain
            or not exact_stage
            or self.completed_transactions != completed
            or self.successful_transactions != successful
            or self.failed_transactions != completed - successful
            or (
                self.traffic_execution is not None
                and self.traffic_execution.observations != observations
            )
        ):
            raise ValueError("Product v0.2.3.2.1 traffic blocker counts differ")
        _require_seal(self, "blocker_sha256", label="formal traffic blocker")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "FormalTrafficBlockerV02321":
        execution_payload = payload.get("traffic_execution")
        execution = (
            None
            if execution_payload is None
            else HealthyTrafficExecutionV0232.model_validate(execution_payload)
        )
        dispatch_checkpoints = tuple(
            FormalTrafficDispatchCheckpointV02321.model_validate(item)
            for item in payload["dispatch_checkpoints"]
        )
        observation_checkpoints = tuple(
            FormalTrafficObservationCheckpointV02321.model_validate(item)
            for item in payload["observation_checkpoints"]
        )
        observations = tuple(item.observation for item in observation_checkpoints)
        successful = sum(item.business_success for item in observations)
        closure = FormalBlockerClosureV02321.model_validate(payload["closure"])
        body = {
            "schema_version": "ecomsre.product.formal-traffic-blocker.v02321",
            "terminal": ("BLOCKED_ECOMSRE_PRODUCT_V02321_FORMAL_HEALTHY_TRAFFIC"),
            **payload,
            "closure": closure.model_dump(mode="json"),
            "traffic_execution": (
                None if execution is None else execution.model_dump(mode="json")
            ),
            "dispatch_checkpoints": [
                item.model_dump(mode="json") for item in dispatch_checkpoints
            ],
            "observation_checkpoints": [
                item.model_dump(mode="json") for item in observation_checkpoints
            ],
            "planned_transactions": 30,
            "completed_transactions": len(observations),
            "successful_transactions": successful,
            "failed_transactions": len(observations) - successful,
            "hidden_retry_count": 0,
            "fault_family_count": 0,
            "knowledge_artifact_count": 0,
            "provider_calls": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
            "formal_healthy_traffic_execution_count": 1,
            "accepted_successor_incident_count": 0,
            "successor_diagnosis_count": 0,
            "action_authority": "NONE",
        }
        return cls.model_validate({**body, "blocker_sha256": semantic_sha256_v22(body)})


class FreshRuntimeSnapshotProofV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.fresh-runtime-snapshot.v02321"] = (
        "ecomsre.product.fresh-runtime-snapshot.v02321"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V02321_FRESH_RUNTIME_SNAPSHOT_PASS"] = (
        "ECOMSRE_PRODUCT_V02321_FRESH_RUNTIME_SNAPSHOT_PASS"
    )
    execution_head: str = Field(pattern=_HEAD_PATTERN)
    traffic_result_sha256: str = Field(pattern=_SHA256_PATTERN)
    snapshot: PilotRuntimeSnapshotV02
    runtime_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_continuity_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    connector_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    checkout_state: Literal["RUNNING"] = "RUNNING"
    checkout_healthy: Literal[True] = True
    checkout_restart_count: Literal[0] = 0
    proof_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_fresh_checkout(self) -> "FreshRuntimeSnapshotProofV02321":
        checkout = tuple(
            item
            for item in self.snapshot.services
            if item.logical_service == "checkout"
        )
        if (
            len(checkout) != 1
            or checkout[0].state.value != "RUNNING"
            or checkout[0].healthy is not True
            or checkout[0].restart_count != 0
            or self.snapshot.authority_sha256 != self.connector_binding_sha256
        ):
            raise ValueError("Product v0.2.3.2.1 fresh Runtime binding differs")
        _require_seal(self, "proof_sha256", label="fresh Runtime snapshot")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "FreshRuntimeSnapshotProofV02321":
        snapshot = PilotRuntimeSnapshotV02.model_validate(payload["snapshot"])
        body = {
            "schema_version": "ecomsre.product.fresh-runtime-snapshot.v02321",
            "terminal": "ECOMSRE_PRODUCT_V02321_FRESH_RUNTIME_SNAPSHOT_PASS",
            **payload,
            "snapshot": snapshot.model_dump(mode="json"),
            "checkout_state": "RUNNING",
            "checkout_healthy": True,
            "checkout_restart_count": 0,
        }
        return cls.model_validate({**body, "proof_sha256": semantic_sha256_v22(body)})


class RuntimeAuthorityProofV02321(ProductModelV1):
    """Typed successor wrapper around the exact v0.2.3.1 authority proof."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.runtime-authority-proof.v02321"] = (
        "ecomsre.product.runtime-authority-proof.v02321"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V02321_RUNTIME_AUTHORITY_CONTINUITY_PASS"] = (
        RUNTIME_AUTHORITY_CONTINUITY_PASS_V02321
    )
    execution_head: str = Field(pattern=_HEAD_PATTERN)
    admission_sha256: str = Field(pattern=_SHA256_PATTERN)
    continuity_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    inner_proof: RuntimeAuthorityContinuityProofV0231
    checkout_state: Literal["RUNNING"] = "RUNNING"
    checkout_healthy: Literal[True] = True
    checkout_restart_count: Literal[0] = 0
    proof_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_authority(self) -> "RuntimeAuthorityProofV02321":
        if (
            self.continuity_descriptor_sha256
            != self.inner_proof.continuity_descriptor_sha256
        ):
            raise ValueError("Product v0.2.3.2.1 Runtime descriptor binding differs")
        _require_seal(self, "proof_sha256", label="Runtime authority proof")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "RuntimeAuthorityProofV02321":
        inner = RuntimeAuthorityContinuityProofV0231.model_validate(
            payload["inner_proof"]
        )
        body = {
            "schema_version": "ecomsre.product.runtime-authority-proof.v02321",
            "terminal": RUNTIME_AUTHORITY_CONTINUITY_PASS_V02321,
            **payload,
            "inner_proof": inner.model_dump(mode="json"),
            "checkout_state": "RUNNING",
            "checkout_healthy": True,
            "checkout_restart_count": 0,
        }
        return cls.model_validate({**body, "proof_sha256": semantic_sha256_v22(body)})


class BaselineRestartProofV02321(ProductModelV1):
    """Typed successor wrapper around the exact ordinary Product restart proof."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.baseline-restart-proof.v02321"] = (
        "ecomsre.product.baseline-restart-proof.v02321"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V02321_BASELINE_RESTART_PASS"] = (
        BASELINE_RESTART_PASS_V02321
    )
    execution_head: str = Field(pattern=_HEAD_PATTERN)
    admission_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_baseline_id: str = Field(pattern=r"^base-[0-9a-f]{24}$")
    active_baseline_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    inner_proof: BaselineRestartProofV0231
    new_baseline_count: Literal[0] = 0
    proof_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_restart(self) -> "BaselineRestartProofV02321":
        after = self.inner_proof.inner_proof.after
        if (
            self.active_baseline_id != after.active_baseline_id
            or self.active_baseline_sha256 != after.active_baseline_sha256
            or self.active_profile_sha256 != self.inner_proof.active_profile_sha256
        ):
            raise ValueError("Product v0.2.3.2.1 Baseline restart binding differs")
        _require_seal(self, "proof_sha256", label="Baseline restart proof")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "BaselineRestartProofV02321":
        inner = BaselineRestartProofV0231.model_validate(payload["inner_proof"])
        body = {
            "schema_version": "ecomsre.product.baseline-restart-proof.v02321",
            "terminal": BASELINE_RESTART_PASS_V02321,
            **payload,
            "inner_proof": inner.model_dump(mode="json"),
            "new_baseline_count": 0,
        }
        return cls.model_validate({**body, "proof_sha256": semantic_sha256_v22(body)})


class FormalTrafficResultV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-traffic-result.v02321"] = (
        "ecomsre.product.formal-traffic-result.v02321"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V02321_FORMAL_HEALTHY_TRAFFIC_PASS"] = (
        FORMAL_HEALTHY_TRAFFIC_PASS_V02321
    )
    admission_sha256: str = Field(pattern=_SHA256_PATTERN)
    consumption_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution: HealthyTrafficExecutionV0232
    episode_started_at: datetime
    episode_ended_at: datetime
    minimum_episode_duration_seconds: Literal[300] = 300
    monotonic_duration_ms: int = Field(ge=300_000)
    hidden_retry_count: Literal[0] = 0
    formal_healthy_traffic_execution_count: Literal[1] = 1
    result_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_formal_traffic(self) -> "FormalTrafficResultV02321":
        run = self.execution.run
        if (
            run.role != "FORMAL"
            or not run.passed
            or run.planned_transactions != 30
            or run.completed_transactions != 30
            or run.successful_transactions != 30
            or run.failed_transactions != 0
            or run.transport_retry_count != 0
            or len(self.execution.observations) != 30
            or any(not item.business_success for item in self.execution.observations)
        ):
            raise ValueError("Product v0.2.3.2.1 formal traffic is not 30 / 30")
        duration = (self.episode_ended_at - self.episode_started_at).total_seconds()
        if (
            self.episode_started_at.tzinfo is None
            or self.episode_ended_at.tzinfo is None
            or not (
                self.episode_started_at
                <= run.started_at
                <= run.ended_at
                <= self.episode_ended_at
            )
            or duration < self.minimum_episode_duration_seconds
        ):
            raise ValueError(
                "Product v0.2.3.2.1 formal episode must be at least 300 seconds"
            )
        _require_seal(self, "result_sha256", label="formal traffic result")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "FormalTrafficResultV02321":
        execution = HealthyTrafficExecutionV0232.model_validate(payload["execution"])
        body = {
            "schema_version": "ecomsre.product.formal-traffic-result.v02321",
            "terminal": FORMAL_HEALTHY_TRAFFIC_PASS_V02321,
            **payload,
            "execution": execution,
            "minimum_episode_duration_seconds": 300,
            "hidden_retry_count": 0,
            "formal_healthy_traffic_execution_count": 1,
        }
        draft = cls.model_construct(**body, result_sha256="0" * 64)
        normalized = draft.model_dump(mode="json", exclude={"result_sha256"})
        return cls.model_validate(
            {**normalized, "result_sha256": semantic_sha256_v22(normalized)}
        )


class NoFaultAcceptanceResultV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.nofault-acceptance.v02321"] = (
        "ecomsre.product.nofault-acceptance.v02321"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V02321_NOFAULT_ACCEPTANCE_COMPLETE"] = (
        NOFAULT_ACCEPTANCE_COMPLETE_V02321
    )
    execution_head: str = Field(pattern=_HEAD_PATTERN)
    admission_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_clone_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_poststate_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_poststate_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_authority_terminal: Literal[
        "ECOMSRE_PRODUCT_V02321_RUNTIME_AUTHORITY_CONTINUITY_PASS"
    ] = RUNTIME_AUTHORITY_CONTINUITY_PASS_V02321
    runtime_authority_proof_sha256: str = Field(pattern=_SHA256_PATTERN)
    baseline_restart_terminal: Literal[
        "ECOMSRE_PRODUCT_V02321_BASELINE_RESTART_PASS"
    ] = BASELINE_RESTART_PASS_V02321
    baseline_restart_proof_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_traffic_terminal: Literal[
        "ECOMSRE_PRODUCT_V02321_FORMAL_HEALTHY_TRAFFIC_PASS"
    ] = FORMAL_HEALTHY_TRAFFIC_PASS_V02321
    formal_traffic_result_sha256: str = Field(pattern=_SHA256_PATTERN)
    fresh_runtime_snapshot_proof_sha256: str = Field(pattern=_SHA256_PATTERN)
    incident_traffic_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    diagnosis_id: str = Field(pattern=r"^diag-[0-9a-f]{24}$")
    diagnosis_incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    evidence_incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    evidence_diagnosis_id: str = Field(pattern=r"^diag-[0-9a-f]{24}$")
    index_incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    index_diagnosis_id: str = Field(pattern=r"^diag-[0-9a-f]{24}$")
    trace_incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    trace_diagnosis_id: str = Field(pattern=r"^diag-[0-9a-f]{24}$")
    assessment_incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    assessment_diagnosis_id: str = Field(pattern=r"^diag-[0-9a-f]{24}$")
    diagnosis_result_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_index_sha256: str = Field(pattern=_SHA256_PATTERN)
    decision_trace_sha256: str = Field(pattern=_SHA256_PATTERN)
    assessment_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_assessment_terminal: NoFaultMeasuredTerminalV0232
    measured_terminal: MeasuredTerminalV02321
    source_incident_count_after: Literal[1] = 1
    source_diagnosis_count_after: Literal[1] = 1
    starting_incident_count: Literal[1] = 1
    starting_diagnosis_count: Literal[1] = 1
    ending_incident_count: Literal[2] = 2
    ending_diagnosis_count: Literal[2] = 2
    successor_incident_delta: Literal[1] = 1
    successor_diagnosis_delta: Literal[1] = 1
    fault_family_count: Literal[0] = 0
    knowledge_artifact_count: Literal[0] = 0
    fault_attempt_count: Literal[0] = 0
    knowledge_loop_campaign_count: Literal[0] = 0
    agent_writes: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    provider_calls: Literal[0] = 0
    action_authority: Literal["NONE"] = "NONE"
    product_cleanup: Literal["CLEAN"] = "CLEAN"
    demo_cleanup: Literal["CLEAN"] = "CLEAN"
    source_product_state_unchanged: Literal[True] = True
    result_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_complete_episode(self) -> "NoFaultAcceptanceResultV02321":
        if (
            self.ending_incident_count - self.starting_incident_count
            != self.successor_incident_delta
            or self.ending_diagnosis_count - self.starting_diagnosis_count
            != self.successor_diagnosis_delta
            or self.measured_terminal
            != measured_terminal_v02321(self.source_assessment_terminal)
            or any(
                value != self.incident_id
                for value in (
                    self.diagnosis_incident_id,
                    self.evidence_incident_id,
                    self.index_incident_id,
                    self.trace_incident_id,
                    self.assessment_incident_id,
                )
            )
            or any(
                value != self.diagnosis_id
                for value in (
                    self.evidence_diagnosis_id,
                    self.index_diagnosis_id,
                    self.trace_diagnosis_id,
                    self.assessment_diagnosis_id,
                )
            )
        ):
            raise ValueError(
                "Product v0.2.3.2.1 successor, terminal, or evidence identity differs"
            )
        _require_seal(self, "result_sha256", label="No-Fault acceptance")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "NoFaultAcceptanceResultV02321":
        body = {
            "schema_version": "ecomsre.product.nofault-acceptance.v02321",
            "terminal": NOFAULT_ACCEPTANCE_COMPLETE_V02321,
            **payload,
            "runtime_authority_terminal": (RUNTIME_AUTHORITY_CONTINUITY_PASS_V02321),
            "baseline_restart_terminal": BASELINE_RESTART_PASS_V02321,
            "formal_traffic_terminal": FORMAL_HEALTHY_TRAFFIC_PASS_V02321,
            "successor_incident_delta": 1,
            "successor_diagnosis_delta": 1,
        }
        return cls.model_validate({**body, "result_sha256": semantic_sha256_v22(body)})


class FormalProgressV02321(ProductModelV1):
    """Typed final public progress surface for the one formal successor episode."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.progress.v02321"]
    campaign_sha256: str = Field(pattern=_SHA256_PATTERN)
    goal_version: Literal["ecomsre-product-v02321-traffic-harness-repair-nofault-v1"]
    harness_contract_preflight_sha256: str = Field(pattern=_SHA256_PATTERN)
    history_terminal: Literal["ECOMSRE_PRODUCT_V02321_HISTORY_AND_REUSE_PASS"]
    live_request_plan_status: Literal["PASS"]
    live_traffic_preflight_status: Literal["PASS"]
    offline_failure_injection_scenario_count: Literal[4]
    offline_fixture_request_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    offline_harness_iteration_count: Literal[2]
    predecessor_head: str = Field(pattern=_HEAD_PATTERN)
    preflight_closure_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    product_state_clone_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    product_state_clone_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_state_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_preflight_attempt_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_preflight_ledger_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_preflight_sha256: str = Field(pattern=_SHA256_PATTERN)
    typed_request_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    typed_request_plan_terminal: Literal[
        "ECOMSRE_PRODUCT_V02321_TYPED_REQUEST_PLAN_PASS"
    ]
    terminal: Literal["ECOMSRE_PRODUCT_V02321_NOFAULT_ACCEPTANCE_COMPLETE"] = (
        NOFAULT_ACCEPTANCE_COMPLETE_V02321
    )
    increment: Literal[4] = 4
    live_authorization: Literal[False] = False
    infrastructure_session_count: Literal[2] = 2
    live_traffic_preflight_attempt_count: Literal[1] = 1
    traffic_attempt_count: Literal[1] = 1
    formal_state_clone_status: Literal["PASS"] = "PASS"
    formal_state_clone_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_state_clone_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_authority_status: Literal["PASS"] = "PASS"
    runtime_authority_proof_sha256: str = Field(pattern=_SHA256_PATTERN)
    baseline_restart_status: Literal["PASS"] = "PASS"
    baseline_restart_proof_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_traffic_status: Literal["PASS"] = "PASS"
    formal_traffic_result_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_healthy_traffic_execution_count: Literal[1] = 1
    fresh_runtime_snapshot_status: Literal["PASS"] = "PASS"
    fresh_runtime_snapshot_proof_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_poststate_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_poststate_sha256: str = Field(pattern=_SHA256_PATTERN)
    accepted_successor_incident_count: Literal[1] = 1
    successor_diagnosis_count: Literal[1] = 1
    measured_terminal: MeasuredTerminalV02321
    nofault_acceptance_result_sha256: str = Field(pattern=_SHA256_PATTERN)
    fault_family_count: Literal[0] = 0
    knowledge_artifact_count: Literal[0] = 0
    fault_attempt_count: Literal[0] = 0
    knowledge_loop_campaign_count: Literal[0] = 0
    new_baseline_attempt_count: Literal[0] = 0
    agent_writes: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    provider_calls: Literal[0] = 0
    action_authority: Literal["NONE"] = "NONE"
    progress_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_self_seal(self) -> "FormalProgressV02321":
        _require_seal(self, "progress_sha256", label="formal progress")
        return self


__all__ = (
    "BASELINE_RESTART_PASS_V02321",
    "FORMAL_HEALTHY_TRAFFIC_PASS_V02321",
    "NOFAULT_ACCEPTANCE_COMPLETE_V02321",
    "NOFAULT_CAPABILITY_LIMITED_V02321",
    "NOFAULT_FULLY_SUPPORTED_V02321",
    "NOFAULT_NOT_SUPPORTED_V02321",
    "RUNTIME_AUTHORITY_CONTINUITY_PASS_V02321",
    "FormalCloneReservationV02321",
    "FormalBlockerClosureV02321",
    "FormalExecutionAdmissionV02321",
    "FormalExecutionBlockerV02321",
    "FormalProgressV02321",
    "FormalTrafficBlockerV02321",
    "FormalTrafficConsumptionV02321",
    "FormalTrafficDispatchCheckpointV02321",
    "FormalTrafficObservationCheckpointV02321",
    "FormalTrafficResultV02321",
    "FreshRuntimeSnapshotProofV02321",
    "MeasuredTerminalV02321",
    "NoFaultAcceptanceResultV02321",
    "BaselineRestartProofV02321",
    "RuntimeAuthorityProofV02321",
    "measured_terminal_v02321",
)
