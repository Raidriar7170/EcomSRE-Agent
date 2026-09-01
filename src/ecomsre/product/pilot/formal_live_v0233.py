"""Typed one-shot live execution and closure contracts for Product v0.2.3.3."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator
from pydantic_core import to_jsonable_python

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.pilot.healthy_traffic_v0232 import HealthyTrafficExecutionV0232
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _semantic_json_sha256_v0233(value: Any) -> str:
    return semantic_sha256_v22(to_jsonable_python(value))

FORMAL_EXECUTION_ADMITTED_V0233: Literal[
    "ECOMSRE_PRODUCT_V0233_FORMAL_EXECUTION_ADMITTED"
] = "ECOMSRE_PRODUCT_V0233_FORMAL_EXECUTION_ADMITTED"
RUNTIME_AUTHORITY_CONTINUITY_PASS_V0233: Literal[
    "ECOMSRE_PRODUCT_V0233_RUNTIME_AUTHORITY_CONTINUITY_PASS"
] = "ECOMSRE_PRODUCT_V0233_RUNTIME_AUTHORITY_CONTINUITY_PASS"
BASELINE_RESTART_PASS_V0233: Literal["ECOMSRE_PRODUCT_V0233_BASELINE_RESTART_PASS"] = (
    "ECOMSRE_PRODUCT_V0233_BASELINE_RESTART_PASS"
)
FORMAL_HEALTHY_TRAFFIC_PASS_V0233: Literal[
    "ECOMSRE_PRODUCT_V0233_FORMAL_HEALTHY_TRAFFIC_PASS"
] = "ECOMSRE_PRODUCT_V0233_FORMAL_HEALTHY_TRAFFIC_PASS"
FRESH_RUNTIME_SNAPSHOT_PASS_V0233: Literal[
    "ECOMSRE_PRODUCT_V0233_FRESH_RUNTIME_SNAPSHOT_PASS"
] = "ECOMSRE_PRODUCT_V0233_FRESH_RUNTIME_SNAPSHOT_PASS"

FormalBlockerTerminalV0233 = Literal[
    "BLOCKED_ECOMSRE_PRODUCT_V0233_STATE_CLONE",
    "BLOCKED_ECOMSRE_PRODUCT_V0233_RUNTIME_AUTHORITY",
    "BLOCKED_ECOMSRE_PRODUCT_V0233_PRODUCT_RESTART",
    "BLOCKED_ECOMSRE_PRODUCT_V0233_FORMAL_HEALTHY_TRAFFIC",
    "BLOCKED_ECOMSRE_PRODUCT_V0233_DIAGNOSIS_PIPELINE",
    "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS",
]


def _sealed_body(model: ProductModelV1, field: str) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude={field})


class FormalExecutionAdmissionV0233(ProductModelV1):
    """Immutable gate snapshot taken before the formal authority is consumed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-execution-admission.v0233"] = (
        "ecomsre.product.formal-execution-admission.v0233"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V0233_FORMAL_EXECUTION_ADMITTED"] = (
        FORMAL_EXECUTION_ADMITTED_V0233
    )
    execution_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    campaign_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_selection_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_clone_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_contract_freeze_sha256: str = Field(pattern=_SHA256_PATTERN)
    pre_execution_review_sha256: str = Field(pattern=_SHA256_PATTERN)
    repository_state_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_clone_count: Literal[0] = 0
    formal_execution_count: Literal[0] = 0
    new_incident_count: Literal[0] = 0
    new_diagnosis_count: Literal[0] = 0
    measured_result_count: Literal[0] = 0
    action_authority: Literal["NONE"] = "NONE"
    admission_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_admission_seal(self) -> FormalExecutionAdmissionV0233:
        if self.admission_sha256 != semantic_sha256_v22(
            _sealed_body(self, "admission_sha256")
        ):
            raise ValueError("Product v0.2.3.3 formal admission digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> FormalExecutionAdmissionV0233:
        body = {
            "schema_version": "ecomsre.product.formal-execution-admission.v0233",
            "terminal": FORMAL_EXECUTION_ADMITTED_V0233,
            **payload,
            "formal_clone_count": 0,
            "formal_execution_count": 0,
            "new_incident_count": 0,
            "new_diagnosis_count": 0,
            "measured_result_count": 0,
            "action_authority": "NONE",
        }
        return cls.model_validate(
            {**body, "admission_sha256": _semantic_json_sha256_v0233(body)}
        )


class FormalExecutionReservationV0233(ProductModelV1):
    """Create-once marker that consumes the one formal execution authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-execution-reservation.v0233"] = (
        "ecomsre.product.formal-execution-reservation.v0233"
    )
    admission: FormalExecutionAdmissionV0233
    reserved_at: datetime
    formal_execution_ordinal: Literal[1] = 1
    action_authority: Literal["NONE"] = "NONE"
    reservation_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_reservation_seal(self) -> FormalExecutionReservationV0233:
        if (
            self.reserved_at.tzinfo is None
            or self.reservation_sha256
            != semantic_sha256_v22(_sealed_body(self, "reservation_sha256"))
        ):
            raise ValueError("Product v0.2.3.3 formal reservation differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        admission: FormalExecutionAdmissionV0233,
        reserved_at: datetime,
    ) -> FormalExecutionReservationV0233:
        body = {
            "schema_version": "ecomsre.product.formal-execution-reservation.v0233",
            "admission": admission.model_dump(mode="json"),
            "reserved_at": reserved_at,
            "formal_execution_ordinal": 1,
            "action_authority": "NONE",
        }
        return cls.model_validate(
            {
                **body,
                "reservation_sha256": _semantic_json_sha256_v0233(body),
            }
        )


class RuntimeAuthorityProofV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.runtime-authority-proof.v0233"] = (
        "ecomsre.product.runtime-authority-proof.v0233"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V0233_RUNTIME_AUTHORITY_CONTINUITY_PASS"] = (
        RUNTIME_AUTHORITY_CONTINUITY_PASS_V0233
    )
    admission_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_continuity_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    pilot_runtime_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_connector_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)
    checkout_state: Literal["RUNNING"] = "RUNNING"
    checkout_healthy: Literal[True] = True
    checkout_restart_count: Literal[0] = 0
    proof_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_proof_seal(self) -> RuntimeAuthorityProofV0233:
        if self.proof_sha256 != semantic_sha256_v22(_sealed_body(self, "proof_sha256")):
            raise ValueError("Product v0.2.3.3 Runtime authority proof differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> RuntimeAuthorityProofV0233:
        body = {
            "schema_version": "ecomsre.product.runtime-authority-proof.v0233",
            "terminal": RUNTIME_AUTHORITY_CONTINUITY_PASS_V0233,
            **payload,
        }
        return cls.model_validate(
            {**body, "proof_sha256": _semantic_json_sha256_v0233(body)}
        )


class BaselineRestartProofV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.baseline-restart-proof.v0233"] = (
        "ecomsre.product.baseline-restart-proof.v0233"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V0233_BASELINE_RESTART_PASS"] = (
        BASELINE_RESTART_PASS_V0233
    )
    admission_sha256: str = Field(pattern=_SHA256_PATTERN)
    environment_id: str
    active_baseline_id: str
    active_baseline_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    readiness_audit_sha256: str = Field(pattern=_SHA256_PATTERN)
    api_instance_id_before: str
    api_instance_id_after: str
    worker_instance_id_before: str
    worker_instance_id_after: str
    new_baseline_count: Literal[0] = 0
    pending_jobs_after: Literal[0] = 0
    running_jobs_after: Literal[0] = 0
    failed_jobs_before: int = Field(ge=0)
    failed_jobs_after: int = Field(ge=0)
    proof_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_restart_and_seal(self) -> BaselineRestartProofV0233:
        if (
            self.api_instance_id_before == self.api_instance_id_after
            or self.worker_instance_id_before == self.worker_instance_id_after
            or self.failed_jobs_before != self.failed_jobs_after
            or self.proof_sha256
            != semantic_sha256_v22(_sealed_body(self, "proof_sha256"))
        ):
            raise ValueError("Product v0.2.3.3 Baseline restart proof differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> BaselineRestartProofV0233:
        body = {
            "schema_version": "ecomsre.product.baseline-restart-proof.v0233",
            "terminal": BASELINE_RESTART_PASS_V0233,
            **payload,
            "new_baseline_count": 0,
            "pending_jobs_after": 0,
            "running_jobs_after": 0,
        }
        return cls.model_validate(
            {**body, "proof_sha256": _semantic_json_sha256_v0233(body)}
        )


class FormalTrafficResultV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-traffic-result.v0233"] = (
        "ecomsre.product.formal-traffic-result.v0233"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V0233_FORMAL_HEALTHY_TRAFFIC_PASS"] = (
        FORMAL_HEALTHY_TRAFFIC_PASS_V0233
    )
    admission_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution: HealthyTrafficExecutionV0232
    episode_started_at: datetime
    episode_ended_at: datetime
    monotonic_duration_ms: int = Field(ge=0)
    result_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_formal_traffic(self) -> FormalTrafficResultV0233:
        run = self.execution.run
        exact = (
            run.role == "FORMAL"
            and run.planned_transactions == 30
            and run.completed_transactions == 30
            and run.successful_transactions == 30
            and run.failed_transactions == 0
            and run.transport_retry_count == 0
            and run.passed
            and run.contract_sha256 == self.traffic_contract_sha256
            and len(self.execution.observations) == 30
            and self.episode_started_at.tzinfo is not None
            and self.episode_ended_at.tzinfo is not None
            and self.episode_ended_at >= self.episode_started_at
            and self.monotonic_duration_ms >= 300_000
        )
        if not exact or self.result_sha256 != semantic_sha256_v22(
            _sealed_body(self, "result_sha256")
        ):
            raise ValueError("Product v0.2.3.3 formal traffic differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> FormalTrafficResultV0233:
        body = {
            "schema_version": "ecomsre.product.formal-traffic-result.v0233",
            "terminal": FORMAL_HEALTHY_TRAFFIC_PASS_V0233,
            **payload,
        }
        return cls.model_validate(
            {
                **body,
                "result_sha256": _semantic_json_sha256_v0233(body),
            }
        )


class FreshRuntimeSnapshotProofV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.fresh-runtime-snapshot-proof.v0233"] = (
        "ecomsre.product.fresh-runtime-snapshot-proof.v0233"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V0233_FRESH_RUNTIME_SNAPSHOT_PASS"] = (
        FRESH_RUNTIME_SNAPSHOT_PASS_V0233
    )
    admission_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_traffic_result_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)
    observed_at: datetime
    pilot_runtime_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_continuity_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_connector_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    checkout_state: Literal["RUNNING"] = "RUNNING"
    checkout_healthy: Literal[True] = True
    checkout_restart_count: Literal[0] = 0
    proof_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_snapshot_and_seal(self) -> FreshRuntimeSnapshotProofV0233:
        if self.observed_at.tzinfo is None or self.proof_sha256 != semantic_sha256_v22(
            _sealed_body(self, "proof_sha256")
        ):
            raise ValueError("Product v0.2.3.3 fresh Runtime snapshot differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> FreshRuntimeSnapshotProofV0233:
        body = {
            "schema_version": "ecomsre.product.fresh-runtime-snapshot-proof.v0233",
            "terminal": FRESH_RUNTIME_SNAPSHOT_PASS_V0233,
            **payload,
            "checkout_state": "RUNNING",
            "checkout_healthy": True,
            "checkout_restart_count": 0,
        }
        return cls.model_validate(
            {**body, "proof_sha256": _semantic_json_sha256_v0233(body)}
        )


class FormalObservedStateCountsV0233(ProductModelV1):
    """Unconstrained raw counts, including unsafe drift, used at terminalization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_count: int = Field(ge=0)
    active_baseline_count: int = Field(ge=0)
    baseline_job_count: int = Field(ge=0)
    verify_job_count: int = Field(ge=0)
    diagnosis_job_count: int = Field(ge=0)
    incident_count: int = Field(ge=0)
    diagnosis_count: int = Field(ge=0)
    evidence_object_count: int = Field(ge=0)
    diagnosis_evidence_index_count: int = Field(ge=0)
    diagnosis_stage_event_count: int = Field(ge=0)
    fault_family_count: int = Field(ge=0)
    knowledge_artifact_count: int = Field(ge=0)
    pending_job_count: int = Field(ge=0)
    running_job_count: int = Field(ge=0)
    failed_job_count: int = Field(ge=0)


FormalActionEventV0233 = Literal[
    "RESERVATION_CONSUMED",
    "FORMAL_CLONE_REQUESTED",
    "DEMO_START_REQUESTED",
    "PRODUCT_START_REQUESTED",
    "PRODUCT_RESTART_REQUESTED",
    "FORMAL_TRAFFIC_REQUESTED",
    "INCIDENT_CREATE_REQUESTED",
    "DIAGNOSIS_CREATE_REQUESTED",
    "FAULT_ATTEMPT_REQUESTED",
    "KNOWLEDGE_LOOP_REQUESTED",
]


class FormalActionJournalV0233(ProductModelV1):
    """Sealed observation of every mutation-capable formal dispatch site."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-action-journal.v0233"] = (
        "ecomsre.product.formal-action-journal.v0233"
    )
    observation_status: Literal["COMPLETE", "UNAVAILABLE"]
    events: tuple[FormalActionEventV0233, ...]
    fault_attempts: int = Field(ge=0)
    knowledge_loop_executions: int = Field(ge=0)
    journal_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_event_counts_and_seal(self) -> FormalActionJournalV0233:
        exact = self.fault_attempts == self.events.count(
            "FAULT_ATTEMPT_REQUESTED"
        ) and self.knowledge_loop_executions == self.events.count(
            "KNOWLEDGE_LOOP_REQUESTED"
        )
        if not exact or self.journal_sha256 != semantic_sha256_v22(
            _sealed_body(self, "journal_sha256")
        ):
            raise ValueError("Product v0.2.3.3 formal action journal differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        observation_status: Literal["COMPLETE", "UNAVAILABLE"],
        events: tuple[FormalActionEventV0233, ...],
    ) -> FormalActionJournalV0233:
        body = {
            "schema_version": "ecomsre.product.formal-action-journal.v0233",
            "observation_status": observation_status,
            "events": events,
            "fault_attempts": events.count("FAULT_ATTEMPT_REQUESTED"),
            "knowledge_loop_executions": events.count("KNOWLEDGE_LOOP_REQUESTED"),
        }
        return cls.model_validate(
            {**body, "journal_sha256": _semantic_json_sha256_v0233(body)}
        )


class FormalSafetyObservationV0233(ProductModelV1):
    """Truthful post-cleanup state and action observation for every terminal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-safety-observation.v0233"] = (
        "ecomsre.product.formal-safety-observation.v0233"
    )
    observation_status: Literal["OBSERVED", "UNAVAILABLE"]
    action_journal: FormalActionJournalV0233
    starting_counts: FormalObservedStateCountsV0233 | None = None
    ending_counts: FormalObservedStateCountsV0233 | None = None
    new_incident_count: int | None = None
    new_diagnosis_count: int | None = None
    provider_calls: int | None = Field(default=None, ge=0)
    agent_writes: int | None = Field(default=None, ge=0)
    runbook_executions: int | None = Field(default=None, ge=0)
    fault_attempts: int | None = Field(default=None, ge=0)
    knowledge_loop_executions: int | None = Field(default=None, ge=0)
    observed_action_authority: Literal["NONE"] | None = None
    safe: bool
    observation_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_truthful_observation_and_seal(self) -> FormalSafetyObservationV0233:
        observed_values = (
            self.starting_counts,
            self.ending_counts,
            self.new_incident_count,
            self.new_diagnosis_count,
            self.provider_calls,
            self.agent_writes,
            self.runbook_executions,
            self.fault_attempts,
            self.knowledge_loop_executions,
            self.observed_action_authority,
        )
        complete = all(value is not None for value in observed_values) and (
            self.action_journal.observation_status == "COMPLETE"
        )
        exact = False
        if complete:
            assert self.starting_counts is not None
            assert self.ending_counts is not None
            assert self.new_incident_count is not None
            assert self.new_diagnosis_count is not None
            exact = (
                self.new_incident_count
                == self.ending_counts.incident_count
                - self.starting_counts.incident_count
                and self.new_diagnosis_count
                == self.ending_counts.diagnosis_job_count
                - self.starting_counts.diagnosis_job_count
            )
            expected_safe = (
                exact
                and self.new_incident_count >= 0
                and self.new_diagnosis_count >= 0
                and self.ending_counts.baseline_count
                == self.starting_counts.baseline_count
                and self.ending_counts.active_baseline_count
                == self.starting_counts.active_baseline_count
                and self.ending_counts.baseline_job_count
                == self.starting_counts.baseline_job_count
                and self.ending_counts.verify_job_count
                == self.starting_counts.verify_job_count
                and self.ending_counts.fault_family_count
                == self.starting_counts.fault_family_count
                and self.ending_counts.knowledge_artifact_count
                == self.starting_counts.knowledge_artifact_count
                and self.ending_counts.pending_job_count == 0
                and self.ending_counts.running_job_count == 0
                and self.provider_calls == 0
                and self.agent_writes == 0
                and self.runbook_executions == 0
                and self.fault_attempts == 0
                and self.knowledge_loop_executions == 0
                and self.fault_attempts == self.action_journal.fault_attempts
                and self.knowledge_loop_executions
                == self.action_journal.knowledge_loop_executions
                and self.observed_action_authority == "NONE"
            )
        else:
            expected_safe = False
        if (
            (self.observation_status == "OBSERVED") != complete
            or (complete and not exact)
            or self.safe != expected_safe
            or self.observation_sha256
            != semantic_sha256_v22(_sealed_body(self, "observation_sha256"))
        ):
            raise ValueError("Product v0.2.3.3 formal safety observation differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> FormalSafetyObservationV0233:
        body = {
            "schema_version": "ecomsre.product.formal-safety-observation.v0233",
            **payload,
        }
        return cls.model_validate(
            {**body, "observation_sha256": _semantic_json_sha256_v0233(body)}
        )


class FormalClosureProofV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-closure-proof.v0233"] = (
        "ecomsre.product.formal-closure-proof.v0233"
    )
    verdict: Literal["CLEAN"] = "CLEAN"
    queue_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    queue_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    outer_baseline_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    outer_baseline_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_selection_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_selection_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_database_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_database_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    product_cleanup: Literal["CLEAN"] = "CLEAN"
    demo_cleanup: Literal["CLEAN"] = "CLEAN"
    owned_host_processes: Literal[0] = 0
    owned_containers: Literal[0] = 0
    owned_networks: Literal[0] = 0
    owned_volumes: Literal[0] = 0
    formal_clone_database_owner_count: Literal[0] = 0
    non_owned_resources_changed: Literal[False] = False
    clone_baseline_binding_exact: Literal[True] = True
    frozen_semantic_surface_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    frozen_semantic_surface_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    safety_observation: FormalSafetyObservationV0233
    closure_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_clean_closure(self) -> FormalClosureProofV0233:
        unchanged = (
            self.queue_before_sha256 == self.queue_after_sha256
            and self.outer_baseline_before_sha256 == self.outer_baseline_after_sha256
            and self.source_selection_before_sha256
            == self.source_selection_after_sha256
            and self.source_database_before_sha256 == self.source_database_after_sha256
            and self.frozen_semantic_surface_before_sha256
            == self.frozen_semantic_surface_after_sha256
            and self.safety_observation.safe
        )
        if not unchanged or self.closure_sha256 != semantic_sha256_v22(
            _sealed_body(self, "closure_sha256")
        ):
            raise ValueError("Product v0.2.3.3 formal closure differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> FormalClosureProofV0233:
        body = {
            "schema_version": "ecomsre.product.formal-closure-proof.v0233",
            "verdict": "CLEAN",
            **payload,
        }
        return cls.model_validate(
            {**body, "closure_sha256": _semantic_json_sha256_v0233(body)}
        )


class FormalExecutionBlockerV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-execution-blocker.v0233"] = (
        "ecomsre.product.formal-execution-blocker.v0233"
    )
    terminal: FormalBlockerTerminalV0233
    failure_stage: str
    safe_error_code: str
    admission_sha256: str = Field(pattern=_SHA256_PATTERN)
    reservation_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_clone_count: Literal[0, 1]
    formal_clone_proof_status: Literal["NOT_CREATED", "OBSERVED", "UNAVAILABLE"]
    formal_clone_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    formal_execution_count: Literal[1] = 1
    new_incident_count: int | None
    new_diagnosis_count: int | None
    measured_result_count: Literal[0] = 0
    measured_terminal: None = None
    cleanup_proof_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    journal_tail_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    exception_fingerprint: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    private_failure_envelope_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    safety_observation: FormalSafetyObservationV0233
    action_authority: Literal["NONE"] = "NONE"
    blocker_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_blocker_shape_and_seal(self) -> FormalExecutionBlockerV0233:
        clone_exact = (
            self.formal_clone_count == 0
            and self.formal_clone_proof_status == "NOT_CREATED"
            and self.formal_clone_sha256 is None
            and self.cleanup_proof_sha256 is None
            and self.new_incident_count == 0
            and self.new_diagnosis_count == 0
        ) or (
            self.formal_clone_count == 1
            and (
                (
                    self.formal_clone_proof_status == "OBSERVED"
                    and self.formal_clone_sha256 is not None
                )
                or (
                    self.formal_clone_proof_status == "UNAVAILABLE"
                    and self.formal_clone_sha256 is None
                )
            )
        )
        counts_exact = (
            self.new_incident_count == self.safety_observation.new_incident_count
            and self.new_diagnosis_count == self.safety_observation.new_diagnosis_count
        )
        exact = (
            clone_exact
            and counts_exact
            and (
                self.terminal != "BLOCKED_ECOMSRE_PRODUCT_V0233_DIAGNOSIS_PIPELINE"
                or (
                    self.journal_tail_sha256 is not None
                    and self.exception_fingerprint is not None
                    and self.private_failure_envelope_sha256 is not None
                )
            )
        )
        if not exact or self.blocker_sha256 != semantic_sha256_v22(
            _sealed_body(self, "blocker_sha256")
        ):
            raise ValueError("Product v0.2.3.3 formal blocker differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> FormalExecutionBlockerV0233:
        body = {
            "schema_version": "ecomsre.product.formal-execution-blocker.v0233",
            "formal_clone_sha256": None,
            "formal_clone_proof_status": "NOT_CREATED",
            "cleanup_proof_sha256": None,
            "journal_tail_sha256": None,
            "exception_fingerprint": None,
            "private_failure_envelope_sha256": None,
            **payload,
            "measured_result_count": 0,
            "measured_terminal": None,
            "action_authority": "NONE",
        }
        return cls.model_validate(
            {**body, "blocker_sha256": _semantic_json_sha256_v0233(body)}
        )


__all__ = (
    "BASELINE_RESTART_PASS_V0233",
    "FORMAL_EXECUTION_ADMITTED_V0233",
    "FORMAL_HEALTHY_TRAFFIC_PASS_V0233",
    "FRESH_RUNTIME_SNAPSHOT_PASS_V0233",
    "RUNTIME_AUTHORITY_CONTINUITY_PASS_V0233",
    "BaselineRestartProofV0233",
    "FormalClosureProofV0233",
    "FormalExecutionAdmissionV0233",
    "FormalExecutionBlockerV0233",
    "FormalExecutionReservationV0233",
    "FormalActionJournalV0233",
    "FormalObservedStateCountsV0233",
    "FormalSafetyObservationV0233",
    "FormalTrafficResultV0233",
    "FreshRuntimeSnapshotProofV0233",
    "RuntimeAuthorityProofV0233",
)
