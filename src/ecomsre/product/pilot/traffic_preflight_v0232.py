"""Live healthy-traffic preflight contracts for Product v0.2.3.2."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import Field, model_validator
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    HealthyTrafficExecutionV0232,
    HealthyTrafficProfileV0232,
    SourceFileBindingV0232,
)


TRAFFIC_PREFLIGHT_PASS_V0232 = "ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT_PASS"
TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0232 = (
    "ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT_ATTEMPT_PASS"
)
TRAFFIC_PREFLIGHT_ATTEMPT_FAIL_V0232 = (
    "ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT_ATTEMPT_FAIL"
)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class TrafficPreflightCleanupV0232(ProductModelV1):
    verdict: Literal["CLEAN", "BLOCKED"]
    owned_containers: int = Field(ge=0)
    owned_networks: int = Field(ge=0)
    owned_volumes: int = Field(ge=0)
    non_owned_resources_changed: bool

    @property
    def clean(self) -> bool:
        return (
            self.verdict == "CLEAN"
            and self.owned_containers == 0
            and self.owned_networks == 0
            and self.owned_volumes == 0
            and not self.non_owned_resources_changed
        )


class TrafficProductCleanupV0232(ProductModelV1):
    schema_version: Literal["ecomsre.product.host-process-cleanup.v023"]
    verdict: Literal["CLEAN"]
    owned_host_processes: Literal[0]
    product_api_port: Literal[18081]
    product_api_port_available: Literal[True]
    launches: tuple[object, ...] = ()
    non_owned_resources_changed: Literal[False]
    safe_error: None = None
    database_owner_count_before: Literal[0]
    database_owner_count_after: Literal[0]

    @model_validator(mode="after")
    def require_product_never_started(self) -> "TrafficProductCleanupV0232":
        if self.launches:
            raise ValueError("Product processes were launched during traffic preflight")
        return self


class TrafficFailureDiagnosticV0232(ProductModelV1):
    failure_stage: str = Field(min_length=1, max_length=80)
    safe_error_code: str = Field(min_length=1, max_length=120)
    http_status: int | None = Field(default=None, ge=100, le=599)
    response_content_type: str | None = Field(default=None, max_length=200)
    response_shape_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    response_shape_summary: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def require_bound_response_diagnostics(self) -> "TrafficFailureDiagnosticV0232":
        if (self.response_shape_sha256 is None) != (
            self.response_shape_summary is None
        ):
            raise ValueError("traffic failure response-shape binding differs")
        if self.http_status is None and any(
            item is not None
            for item in (
                self.response_content_type,
                self.response_shape_sha256,
                self.response_shape_summary,
            )
        ):
            raise ValueError("traffic failure HTTP diagnostics differ")
        return self


class TrafficPreflightAttemptV0232(ProductModelV1):
    schema_version: Literal["ecomsre.product.traffic-preflight-attempt.v0232"] = (
        "ecomsre.product.traffic-preflight-attempt.v0232"
    )
    terminal: Literal[
        "ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT_ATTEMPT_PASS",
        "ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT_ATTEMPT_FAIL",
    ]
    attempt_ordinal: Literal[1, 2]
    changed_parameter: Literal[
        "STABILIZATION_DURATION",
        "REQUEST_PATH_OR_METHOD",
        "REQUEST_PAYLOAD_SCHEMA",
        "RESPONSE_SUCCESS_VALIDATOR",
    ] | None = None
    changed_parameter_evidence_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    execution_sha256: str = Field(pattern=_SHA256_PATTERN)
    profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_file_bindings: tuple[SourceFileBindingV0232, ...]
    flagd_bind_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_continuity_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    resolved_compose_sha256: str = Field(pattern=_SHA256_PATTERN)
    read_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    pilot_runtime_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    checkout_state: Literal["RUNNING"]
    checkout_healthy: Literal[True]
    checkout_restart_count: Literal[0]
    planned_transactions: Literal[10]
    completed_transactions: Literal[10]
    successful_transactions: int = Field(ge=0, le=10)
    failed_transactions: int = Field(ge=0, le=10)
    stage_failure_counts: dict[str, int]
    transport_retry_count: Literal[0]
    first_failure: TrafficFailureDiagnosticV0232 | None
    queue_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    queue_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    queue_default_unchanged: bool
    outer_baseline_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    outer_baseline_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    outer_baseline_unchanged: bool
    source_state_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_state_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_state_unchanged: bool
    product_state_clone_sha256: str = Field(pattern=_SHA256_PATTERN)
    product_state_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    product_state_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    product_state_unchanged: bool
    incident_count_before: Literal[1]
    incident_count_after: Literal[1]
    diagnosis_count_before: Literal[1]
    diagnosis_count_after: Literal[1]
    demo_cleanup: TrafficPreflightCleanupV0232
    product_cleanup: TrafficProductCleanupV0232
    action_authority: Literal["NONE"] = "NONE"
    accepted_successor_incident_count: Literal[0] = 0
    successor_diagnosis_count: Literal[0] = 0
    fault_attempt_count: Literal[0] = 0
    agent_writes: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    provider_calls: Literal[0] = 0
    attempt_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_attempt(self) -> "TrafficPreflightAttemptV0232":
        if self.attempt_ordinal == 1:
            if (
                self.changed_parameter is not None
                or self.changed_parameter_evidence_sha256 is not None
            ):
                raise ValueError("Attempt 1 changed parameter differs")
        elif (
            self.changed_parameter is None
            or self.changed_parameter_evidence_sha256 is None
        ):
            raise ValueError("Attempt 2 changed parameter evidence differs")
        if (
            not self.source_file_bindings
            or tuple(item.path for item in self.source_file_bindings)
            != tuple(sorted({item.path for item in self.source_file_bindings}))
            or self.successful_transactions + self.failed_transactions != 10
            or sum(self.stage_failure_counts.values()) != self.failed_transactions
            or self.queue_default_unchanged
            != (self.queue_before_sha256 == self.queue_after_sha256)
            or self.outer_baseline_unchanged
            != (
                self.outer_baseline_before_sha256
                == self.outer_baseline_after_sha256
            )
            or self.source_state_unchanged
            != (self.source_state_before_sha256 == self.source_state_after_sha256)
            or self.product_state_unchanged
            != (self.product_state_before_sha256 == self.product_state_after_sha256)
            or self.incident_count_before != self.incident_count_after
            or self.diagnosis_count_before != self.diagnosis_count_after
        ):
            raise ValueError("traffic preflight attempt binding differs")
        passed = (
            self.successful_transactions == 10
            and self.failed_transactions == 0
            and not self.stage_failure_counts
            and self.transport_retry_count == 0
            and self.first_failure is None
            and self.queue_default_unchanged
            and self.outer_baseline_unchanged
            and self.source_state_unchanged
            and self.product_state_unchanged
            and self.demo_cleanup.clean
            and self.product_cleanup.verdict == "CLEAN"
        )
        expected_terminal = (
            TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0232
            if passed
            else TRAFFIC_PREFLIGHT_ATTEMPT_FAIL_V0232
        )
        if self.terminal != expected_terminal:
            raise ValueError("traffic preflight Attempt terminal differs")
        if (self.failed_transactions == 0) != (self.first_failure is None):
            raise ValueError("traffic preflight first failure differs")
        expected_sha256 = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"attempt_sha256"})
        )
        if self.attempt_sha256 != expected_sha256:
            raise ValueError("traffic preflight Attempt digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        attempt_ordinal: int,
        changed_parameter: str | None,
        changed_parameter_evidence_sha256: str | None,
        execution: HealthyTrafficExecutionV0232,
        source_file_bindings: tuple[SourceFileBindingV0232, ...],
        flagd_bind_descriptor_sha256: str,
        runtime_continuity_descriptor_sha256: str,
        resolved_compose_sha256: str,
        read_authority_sha256: str,
        pilot_runtime_authority_sha256: str,
        checkout_state: str,
        checkout_healthy: bool,
        checkout_restart_count: int,
        queue_before_sha256: str,
        queue_after_sha256: str,
        outer_baseline_before_sha256: str,
        outer_baseline_after_sha256: str,
        source_state_before_sha256: str,
        source_state_after_sha256: str,
        product_state_clone_sha256: str,
        product_state_before_sha256: str,
        product_state_after_sha256: str,
        incident_count_before: int,
        incident_count_after: int,
        diagnosis_count_before: int,
        diagnosis_count_after: int,
        demo_cleanup: Mapping[str, object],
        product_cleanup: Mapping[str, object],
    ) -> "TrafficPreflightAttemptV0232":
        run = execution.run
        if run.role != "PREFLIGHT" or run.planned_transactions != 10:
            raise ValueError("traffic preflight Attempt requires a 10-transaction run")
        if run.completed_transactions != 10:
            raise ValueError("traffic preflight Attempt must complete 10 transactions")
        failure = next(
            (item for item in execution.observations if not item.business_success),
            None,
        )
        first_failure: dict[str, object] | None = None
        if failure is not None:
            response_status: int | None = None
            response_content_type: str | None = None
            response_shape_sha256: str | None = None
            response_shape_summary: str | None = None
            if failure.checkout_status is not None:
                response_status = failure.checkout_status
                response_content_type = failure.checkout_response_content_type
                response_shape_sha256 = failure.checkout_response_shape_sha256
                response_shape_summary = failure.checkout_response_shape_summary
            elif failure.failure_stage in {
                "CART_HTTP",
                "CART_RESPONSE",
                "BUSINESS_SUCCESS",
            }:
                response_status = failure.cart_status
                response_content_type = failure.cart_response_content_type
                response_shape_sha256 = failure.cart_response_shape_sha256
                response_shape_summary = failure.cart_response_shape_summary
            first_failure = {
                "failure_stage": (
                    failure.failure_stage.value
                    if failure.failure_stage is not None
                    else "UNKNOWN"
                ),
                "safe_error_code": (
                    failure.safe_error_code.value
                    if failure.safe_error_code is not None
                    else "UNKNOWN"
                ),
                "http_status": response_status,
                "response_content_type": response_content_type,
                "response_shape_sha256": response_shape_sha256,
                "response_shape_summary": response_shape_summary,
            }
        cleanup_model = TrafficPreflightCleanupV0232.model_validate(demo_cleanup)
        product_cleanup_model = TrafficProductCleanupV0232.model_validate(
            product_cleanup
        )
        pass_conditions = (
            run.passed
            and checkout_state == "RUNNING"
            and checkout_healthy is True
            and checkout_restart_count == 0
            and queue_before_sha256 == queue_after_sha256
            and outer_baseline_before_sha256 == outer_baseline_after_sha256
            and source_state_before_sha256 == source_state_after_sha256
            and product_state_before_sha256 == product_state_after_sha256
            and incident_count_before == incident_count_after
            and diagnosis_count_before == diagnosis_count_after
            and cleanup_model.clean
            and product_cleanup_model.verdict == "CLEAN"
        )
        body: dict[str, Any] = {
            "schema_version": "ecomsre.product.traffic-preflight-attempt.v0232",
            "terminal": (
                TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0232
                if pass_conditions
                else TRAFFIC_PREFLIGHT_ATTEMPT_FAIL_V0232
            ),
            "attempt_ordinal": attempt_ordinal,
            "changed_parameter": changed_parameter,
            "changed_parameter_evidence_sha256": changed_parameter_evidence_sha256,
            "execution_sha256": execution.execution_sha256,
            "profile_sha256": run.profile_sha256,
            "contract_sha256": run.contract_sha256,
            "source_file_bindings": [
                item.model_dump(mode="json") for item in source_file_bindings
            ],
            "flagd_bind_descriptor_sha256": flagd_bind_descriptor_sha256,
            "runtime_continuity_descriptor_sha256": (
                runtime_continuity_descriptor_sha256
            ),
            "resolved_compose_sha256": resolved_compose_sha256,
            "read_authority_sha256": read_authority_sha256,
            "pilot_runtime_authority_sha256": pilot_runtime_authority_sha256,
            "checkout_state": checkout_state,
            "checkout_healthy": checkout_healthy,
            "checkout_restart_count": checkout_restart_count,
            "planned_transactions": run.planned_transactions,
            "completed_transactions": run.completed_transactions,
            "successful_transactions": run.successful_transactions,
            "failed_transactions": run.failed_transactions,
            "stage_failure_counts": run.stage_failure_counts,
            "transport_retry_count": run.transport_retry_count,
            "first_failure": first_failure,
            "queue_before_sha256": queue_before_sha256,
            "queue_after_sha256": queue_after_sha256,
            "queue_default_unchanged": queue_before_sha256 == queue_after_sha256,
            "outer_baseline_before_sha256": outer_baseline_before_sha256,
            "outer_baseline_after_sha256": outer_baseline_after_sha256,
            "outer_baseline_unchanged": (
                outer_baseline_before_sha256 == outer_baseline_after_sha256
            ),
            "source_state_before_sha256": source_state_before_sha256,
            "source_state_after_sha256": source_state_after_sha256,
            "source_state_unchanged": (
                source_state_before_sha256 == source_state_after_sha256
            ),
            "product_state_clone_sha256": product_state_clone_sha256,
            "product_state_before_sha256": product_state_before_sha256,
            "product_state_after_sha256": product_state_after_sha256,
            "product_state_unchanged": (
                product_state_before_sha256 == product_state_after_sha256
            ),
            "incident_count_before": incident_count_before,
            "incident_count_after": incident_count_after,
            "diagnosis_count_before": diagnosis_count_before,
            "diagnosis_count_after": diagnosis_count_after,
            "demo_cleanup": cleanup_model.model_dump(mode="json"),
            "product_cleanup": product_cleanup_model.model_dump(mode="json"),
            "action_authority": "NONE",
            "accepted_successor_incident_count": 0,
            "successor_diagnosis_count": 0,
            "fault_attempt_count": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
            "provider_calls": 0,
        }
        return cls.model_validate(
            {**body, "attempt_sha256": semantic_sha256_v22(body)}
        )


class TrafficCampaignV0232(ProductModelV1):
    schema_version: Literal["ecomsre.product.traffic-campaign.v0232"] = (
        "ecomsre.product.traffic-campaign.v0232"
    )
    goal_version: Literal[
        "ecomsre-product-v0232-healthy-traffic-evidence-nofault-v1"
    ]
    source_clone_count: Literal[1]
    offline_changed_iteration_limit: Literal[3]
    maximum_live_traffic_preflight_attempts: Literal[2]
    transactions_per_preflight_attempt: Literal[10]
    formal_healthy_traffic_execution_limit: Literal[1]
    formal_transaction_count: Literal[30]
    accepted_successor_incident_limit: Literal[1]
    successor_diagnosis_limit: Literal[1]
    preflight_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_state_sha256: str = Field(pattern=_SHA256_PATTERN)
    product_state_clone_sha256: str = Field(pattern=_SHA256_PATTERN)
    product_state_sha256: str = Field(pattern=_SHA256_PATTERN)
    flagd_bind_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_continuity_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    resolved_compose_sha256: str = Field(pattern=_SHA256_PATTERN)
    read_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    pilot_runtime_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    queue_default_bytes_sha256: str = Field(pattern=_SHA256_PATTERN)
    outer_baseline_document_sha256: str = Field(pattern=_SHA256_PATTERN)
    action_authority: Literal["NONE"]
    fault_attempt_limit: Literal[0]
    knowledge_loop_campaign_limit: Literal[0]
    agent_write_limit: Literal[0]
    runbook_execution_limit: Literal[0]
    provider_call_limit: Literal[0]
    campaign_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_campaign_seal(self) -> "TrafficCampaignV0232":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"campaign_sha256"})
        )
        if self.campaign_sha256 != expected:
            raise ValueError("Product v0.2.3.2 traffic campaign digest differs")
        return self


class TrafficPreflightEvidenceV0232(ProductModelV1):
    schema_version: Literal["ecomsre.product.traffic-preflight.v0232"] = (
        "ecomsre.product.traffic-preflight.v0232"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT_PASS"]
    winning_attempt_ordinal: Literal[1, 2]
    live_traffic_preflight_attempt_count: Literal[1, 2]
    attempt_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_sha256: str = Field(pattern=_SHA256_PATTERN)
    preflight_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    campaign_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_continuity_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    resolved_compose_sha256: str = Field(pattern=_SHA256_PATTERN)
    read_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    pilot_runtime_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    transaction_count: Literal[10]
    successful_transaction_count: Literal[10]
    failed_transaction_count: Literal[0]
    transport_retry_count: Literal[0]
    queue_default_unchanged: Literal[True]
    outer_baseline_unchanged: Literal[True]
    source_state_unchanged: Literal[True]
    product_state_unchanged: Literal[True]
    demo_cleanup: Literal["CLEAN"]
    product_cleanup: Literal["CLEAN"]
    action_authority: Literal["NONE"]
    accepted_successor_incident_count: Literal[0]
    successor_diagnosis_count: Literal[0]
    fault_attempt_count: Literal[0]
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    provider_calls: Literal[0]
    preflight_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_preflight_seal(self) -> "TrafficPreflightEvidenceV0232":
        if self.winning_attempt_ordinal != self.live_traffic_preflight_attempt_count:
            raise ValueError("traffic preflight attempt count differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"preflight_sha256"})
        )
        if self.preflight_sha256 != expected:
            raise ValueError("traffic preflight digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        attempt: TrafficPreflightAttemptV0232,
        formal_profile: HealthyTrafficProfileV0232,
        campaign: TrafficCampaignV0232,
    ) -> "TrafficPreflightEvidenceV0232":
        if attempt.terminal != TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0232:
            raise ValueError("traffic preflight requires a passing Attempt")
        if (
            formal_profile.transactions != 30
            or formal_profile.request_seed != 23083202
            or formal_profile.profile_sha256 != campaign.formal_profile_sha256
            or attempt.profile_sha256 != campaign.preflight_profile_sha256
            or attempt.contract_sha256 != campaign.traffic_contract_sha256
            or attempt.flagd_bind_descriptor_sha256
            != campaign.flagd_bind_descriptor_sha256
            or attempt.runtime_continuity_descriptor_sha256
            != campaign.runtime_continuity_descriptor_sha256
            or attempt.resolved_compose_sha256
            != campaign.resolved_compose_sha256
            or attempt.read_authority_sha256 != campaign.read_authority_sha256
            or attempt.pilot_runtime_authority_sha256
            != campaign.pilot_runtime_authority_sha256
            or attempt.queue_before_sha256
            != campaign.queue_default_bytes_sha256
            or attempt.queue_after_sha256
            != campaign.queue_default_bytes_sha256
            or attempt.outer_baseline_before_sha256
            != campaign.outer_baseline_document_sha256
            or attempt.outer_baseline_after_sha256
            != campaign.outer_baseline_document_sha256
            or attempt.source_state_before_sha256 != campaign.source_state_sha256
            or attempt.source_state_after_sha256 != campaign.source_state_sha256
            or attempt.product_state_clone_sha256
            != campaign.product_state_clone_sha256
            or attempt.product_state_before_sha256
            != campaign.product_state_sha256
            or attempt.product_state_after_sha256
            != campaign.product_state_sha256
        ):
            raise ValueError("traffic preflight frozen profile binding differs")
        body: dict[str, Any] = {
            "schema_version": "ecomsre.product.traffic-preflight.v0232",
            "terminal": TRAFFIC_PREFLIGHT_PASS_V0232,
            "winning_attempt_ordinal": attempt.attempt_ordinal,
            "live_traffic_preflight_attempt_count": attempt.attempt_ordinal,
            "attempt_sha256": attempt.attempt_sha256,
            "execution_sha256": attempt.execution_sha256,
            "preflight_profile_sha256": attempt.profile_sha256,
            "formal_profile_sha256": formal_profile.profile_sha256,
            "campaign_sha256": campaign.campaign_sha256,
            "contract_sha256": attempt.contract_sha256,
            "runtime_continuity_descriptor_sha256": (
                attempt.runtime_continuity_descriptor_sha256
            ),
            "resolved_compose_sha256": attempt.resolved_compose_sha256,
            "read_authority_sha256": attempt.read_authority_sha256,
            "pilot_runtime_authority_sha256": (
                attempt.pilot_runtime_authority_sha256
            ),
            "transaction_count": 10,
            "successful_transaction_count": 10,
            "failed_transaction_count": 0,
            "transport_retry_count": 0,
            "queue_default_unchanged": True,
            "outer_baseline_unchanged": True,
            "source_state_unchanged": True,
            "product_state_unchanged": True,
            "demo_cleanup": "CLEAN",
            "product_cleanup": "CLEAN",
            "action_authority": "NONE",
            "accepted_successor_incident_count": 0,
            "successor_diagnosis_count": 0,
            "fault_attempt_count": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
            "provider_calls": 0,
        }
        return cls.model_validate(
            {**body, "preflight_sha256": semantic_sha256_v22(body)}
        )


def load_traffic_profile_v0232(
    project_root: Path,
    *,
    role: Literal["PREFLIGHT", "FORMAL"],
) -> HealthyTrafficProfileV0232:
    name = "preflight-profile.json" if role == "PREFLIGHT" else "formal-profile.json"
    profile = HealthyTrafficProfileV0232.model_validate_json(
        (Path(project_root) / "config/product-v0232/traffic" / name).read_bytes()
    )
    expected = (
        (
            "product-v0232-preflight",
            10,
            1.0,
            23083201,
            0,
            30,
            0,
            0,
        )
        if role == "PREFLIGHT"
        else (
            "product-v0232-formal",
            30,
            1.0,
            23083202,
            0,
            0,
            300,
            0,
        )
    )
    observed = (
        profile.profile_id,
        profile.transactions,
        profile.requests_per_second,
        profile.request_seed,
        profile.maximum_failures,
        profile.stabilization_seconds,
        profile.minimum_full_episode_duration_seconds,
        profile.queue_fault_flag,
    )
    if observed != expected:
        raise ValueError(f"Product v0.2.3.2 {role.lower()} traffic profile differs")
    return profile


def load_traffic_campaign_v0232(project_root: Path) -> TrafficCampaignV0232:
    root = Path(project_root)
    campaign = TrafficCampaignV0232.model_validate_json(
        (root / "config/product-v0232/campaign.json").read_bytes()
    )
    preflight = load_traffic_profile_v0232(root, role="PREFLIGHT")
    formal = load_traffic_profile_v0232(root, role="FORMAL")
    if (
        campaign.preflight_profile_sha256 != preflight.profile_sha256
        or campaign.formal_profile_sha256 != formal.profile_sha256
    ):
        raise ValueError("Product v0.2.3.2 campaign profile binding differs")
    return campaign


__all__ = (
    "TRAFFIC_PREFLIGHT_ATTEMPT_FAIL_V0232",
    "TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0232",
    "TRAFFIC_PREFLIGHT_PASS_V0232",
    "TrafficCampaignV0232",
    "TrafficFailureDiagnosticV0232",
    "TrafficPreflightAttemptV0232",
    "TrafficPreflightCleanupV0232",
    "TrafficPreflightEvidenceV0232",
    "load_traffic_campaign_v0232",
    "load_traffic_profile_v0232",
)
