"""Live traffic-preflight evidence contracts for Product v0.2.3.2.1."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    HealthyTrafficExecutionV0232,
)
from ecomsre.product.pilot.product_state_clone_v02321 import (
    PreflightStateCloneReportV02321,
)
from ecomsre.product.pilot.traffic_harness_closure_v02321 import (
    InfrastructureSessionStartV02321,
    TrafficPreflightAttemptCompletionV02321,
    TrafficPreflightAttemptStartV02321,
    TrafficHarnessClosureV02321,
    TrafficPreflightLedgerV02321,
)
from ecomsre_live_sandbox.contracts import canonical_json_bytes


TRAFFIC_PREFLIGHT_PASS_V02321 = "ECOMSRE_PRODUCT_V02321_TRAFFIC_PREFLIGHT_PASS"
TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V02321 = (
    "ECOMSRE_PRODUCT_V02321_TRAFFIC_PREFLIGHT_ATTEMPT_PASS"
)
TRAFFIC_PREFLIGHT_ATTEMPT_FAIL_V02321 = (
    "ECOMSRE_PRODUCT_V02321_TRAFFIC_PREFLIGHT_ATTEMPT_FAIL"
)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ATTEMPT_ID_PATTERN = r"^attempt-[0-9a-f]{32}$"


class LiveTrafficPreflightAttemptV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.live-traffic-preflight-attempt.v02321"] = (
        "ecomsre.product.live-traffic-preflight-attempt.v02321"
    )
    terminal: Literal[
        "ECOMSRE_PRODUCT_V02321_TRAFFIC_PREFLIGHT_ATTEMPT_PASS",
        "ECOMSRE_PRODUCT_V02321_TRAFFIC_PREFLIGHT_ATTEMPT_FAIL",
    ]
    attempt_id: str = Field(pattern=_ATTEMPT_ID_PATTERN)
    attempt_ordinal: int = Field(ge=1)
    typed_request_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    product_state_clone_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    product_state_clone_report: PreflightStateCloneReportV02321
    product_state_clone_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_continuity_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_execution: HealthyTrafficExecutionV0232
    closure_sha256: str = Field(pattern=_SHA256_PATTERN)
    ledger: TrafficPreflightLedgerV02321
    source_state_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_state_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    product_state_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    product_state_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    incident_count_before: Literal[1]
    incident_count_after: Literal[1]
    diagnosis_count_before: Literal[1]
    diagnosis_count_after: Literal[1]
    infrastructure_session_count_after: int = Field(ge=1)
    traffic_attempt_count_after: int = Field(ge=1)
    formal_healthy_traffic_execution_count: Literal[0]
    accepted_successor_incident_count: Literal[0]
    successor_diagnosis_count: Literal[0]
    fault_attempt_count: Literal[0]
    knowledge_loop_campaign_count: Literal[0]
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    provider_calls: Literal[0]
    action_authority: Literal["NONE"]
    attempt_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_attempt_evidence(self) -> "LiveTrafficPreflightAttemptV02321":
        closures = tuple(
            event
            for event in self.ledger.events
            if isinstance(event, TrafficHarnessClosureV02321)
            and event.attempt_id == self.attempt_id
        )
        if len(closures) != 1:
            raise ValueError("live traffic preflight closure coverage differs")
        closure = closures[0]
        starts = tuple(
            event
            for event in self.ledger.events
            if isinstance(event, TrafficPreflightAttemptStartV02321)
            and event.attempt_id == self.attempt_id
        )
        completions = tuple(
            event
            for event in self.ledger.events
            if isinstance(event, TrafficPreflightAttemptCompletionV02321)
            and event.attempt_id == self.attempt_id
        )
        sessions = tuple(
            event
            for event in self.ledger.events
            if isinstance(event, InfrastructureSessionStartV02321)
            and starts
            and event.session_id == starts[0].session_id
        )
        if len(starts) != 1 or len(completions) != 1 or len(sessions) != 1:
            raise ValueError("live traffic preflight attempt coverage differs")
        start = starts[0]
        completion = completions[0]
        session = sessions[0]
        execution = self.traffic_execution
        if (
            self.product_state_clone_report.report_sha256
            != self.product_state_clone_report_sha256
            or self.product_state_clone_report.clone.clone_sha256
            != self.product_state_clone_sha256
            or session.state_clone_sha256 != self.product_state_clone_sha256
            or self.source_state_before_sha256
            != self.product_state_clone_report.source_state.source_sha256
            or self.source_state_after_sha256
            != self.product_state_clone_report.source_state.source_sha256
            or self.product_state_before_sha256
            != self.product_state_clone_report.destination_state.source_sha256
            or self.product_state_after_sha256
            != self.product_state_clone_report.destination_state.source_sha256
            or self.closure_sha256 != closure.closure_sha256
            or self.ledger.infrastructure_session_count
            != self.infrastructure_session_count_after
            or self.ledger.traffic_attempt_count
            != self.traffic_attempt_count_after
            or self.attempt_ordinal != self.traffic_attempt_count_after
            or start.attempt_ordinal != self.attempt_ordinal
            or start.request_plan_sha256 != self.typed_request_plan_sha256
            or start.traffic_contract_sha256 != self.traffic_contract_sha256
            or start.profile_sha256 != self.traffic_profile_sha256
            or start.runtime_authority_sha256
            != self.runtime_continuity_descriptor_sha256
            or execution.run.contract_sha256 != self.traffic_contract_sha256
            or execution.run.profile_sha256 != self.traffic_profile_sha256
            or completion.traffic_execution_sha256
            != execution.execution_sha256
            or completion.completed_transactions
            != execution.run.completed_transactions
            or completion.successful_transactions
            != execution.run.successful_transactions
            or completion.failed_transactions
            != execution.run.failed_transactions
            or closure.traffic_execution_sha256
            != execution.execution_sha256
            or closure.traffic_dispatch_failure_sha256 is not None
        ):
            raise ValueError("live traffic preflight evidence binding differs")
        passed = (
            execution.run.role == "PREFLIGHT"
            and execution.run.planned_transactions == 10
            and execution.run.completed_transactions == 10
            and execution.run.successful_transactions == 10
            and execution.run.failed_transactions == 0
            and execution.run.transport_retry_count == 0
            and execution.run.passed
            and closure.closure_terminal == "CLEAN_POST_TRAFFIC"
            and closure.failure_stage is None
            and closure.safe_error_code is None
            and completion.terminal == "ATTEMPT_PASS"
            and completion.safe_error_code is None
            and self.source_state_before_sha256
            == self.source_state_after_sha256
            and self.product_state_before_sha256
            == self.product_state_after_sha256
            and self.incident_count_before == self.incident_count_after
            and self.diagnosis_count_before == self.diagnosis_count_after
        )
        if (self.terminal == TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V02321) != passed:
            raise ValueError("live traffic preflight Attempt terminal differs")
        body = self.model_dump(mode="json", exclude={"attempt_sha256"})
        if self.attempt_sha256 != semantic_sha256_v22(body):
            raise ValueError("live traffic preflight Attempt digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "LiveTrafficPreflightAttemptV02321":
        execution = HealthyTrafficExecutionV0232.model_validate(
            payload["traffic_execution"]
        )
        ledger = TrafficPreflightLedgerV02321.model_validate(payload["ledger"])
        clone_report = PreflightStateCloneReportV02321.model_validate(
            payload["product_state_clone_report"]
        )
        closures = tuple(
            event
            for event in ledger.events
            if isinstance(event, TrafficHarnessClosureV02321)
            and event.attempt_id == payload["attempt_id"]
        )
        completions = tuple(
            event
            for event in ledger.events
            if isinstance(event, TrafficPreflightAttemptCompletionV02321)
            and event.attempt_id == payload["attempt_id"]
        )
        operational_pass = (
            len(closures) == 1
            and len(completions) == 1
            and execution.run.role == "PREFLIGHT"
            and execution.run.planned_transactions == 10
            and execution.run.completed_transactions == 10
            and execution.run.successful_transactions == 10
            and execution.run.failed_transactions == 0
            and execution.run.transport_retry_count == 0
            and execution.run.passed
            and closures[0].closure_terminal == "CLEAN_POST_TRAFFIC"
            and closures[0].failure_stage is None
            and closures[0].safe_error_code is None
            and completions[0].terminal == "ATTEMPT_PASS"
            and completions[0].safe_error_code is None
            and payload["source_state_before_sha256"]
            == payload["source_state_after_sha256"]
            and payload["product_state_before_sha256"]
            == payload["product_state_after_sha256"]
            and payload["incident_count_before"] == payload["incident_count_after"]
            and payload["diagnosis_count_before"]
            == payload["diagnosis_count_after"]
        )
        terminal = (
            TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V02321
            if operational_pass
            else TRAFFIC_PREFLIGHT_ATTEMPT_FAIL_V02321
        )
        serializable_payload = dict(payload)
        serializable_payload["traffic_execution"] = execution.model_dump(mode="json")
        serializable_payload["ledger"] = ledger.model_dump(mode="json")
        serializable_payload["product_state_clone_report"] = (
            clone_report.model_dump(mode="json")
        )
        normalized_payload = json.loads(canonical_json_bytes(serializable_payload))
        body = {
            "schema_version": (
                "ecomsre.product.live-traffic-preflight-attempt.v02321"
            ),
            "terminal": terminal,
            **normalized_payload,
        }
        return cls.model_validate(
            {**body, "attempt_sha256": semantic_sha256_v22(body)}
        )


class LiveTrafficPreflightPassV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.live-traffic-preflight.v02321"] = (
        "ecomsre.product.live-traffic-preflight.v02321"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V02321_TRAFFIC_PREFLIGHT_PASS"] = (
        "ECOMSRE_PRODUCT_V02321_TRAFFIC_PREFLIGHT_PASS"
    )
    attempt: LiveTrafficPreflightAttemptV02321
    frozen_traffic_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    frozen_preflight_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    frozen_formal_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    typed_request_plan_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    closure_contract_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    live_traffic_preflight_attempt_count: int = Field(ge=1)
    infrastructure_session_count: int = Field(ge=1)
    traffic_attempt_count: int = Field(ge=1)
    formal_healthy_traffic_execution_count: Literal[0]
    accepted_successor_incident_count: Literal[0]
    successor_diagnosis_count: Literal[0]
    action_authority: Literal["NONE"]
    preflight_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_pass(self) -> "LiveTrafficPreflightPassV02321":
        if (
            self.attempt.terminal != TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V02321
            or self.frozen_traffic_contract_sha256
            != self.attempt.traffic_contract_sha256
            or self.frozen_preflight_profile_sha256
            != self.attempt.traffic_profile_sha256
            or self.frozen_formal_profile_sha256
            != self.attempt.formal_profile_sha256
            or self.live_traffic_preflight_attempt_count
            != self.attempt.attempt_ordinal
            or self.infrastructure_session_count
            != self.attempt.infrastructure_session_count_after
            or self.traffic_attempt_count
            != self.attempt.traffic_attempt_count_after
        ):
            raise ValueError("live traffic preflight PASS binding differs")
        body = self.model_dump(mode="json", exclude={"preflight_sha256"})
        if self.preflight_sha256 != semantic_sha256_v22(body):
            raise ValueError("live traffic preflight PASS digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "LiveTrafficPreflightPassV02321":
        attempt = LiveTrafficPreflightAttemptV02321.model_validate(
            payload["attempt"]
        )
        serializable_payload = dict(payload)
        serializable_payload["attempt"] = attempt.model_dump(mode="json")
        normalized_payload = json.loads(canonical_json_bytes(serializable_payload))
        body = {
            "schema_version": "ecomsre.product.live-traffic-preflight.v02321",
            "terminal": TRAFFIC_PREFLIGHT_PASS_V02321,
            **normalized_payload,
        }
        return cls.model_validate(
            {**body, "preflight_sha256": semantic_sha256_v22(body)}
        )


__all__ = (
    "LiveTrafficPreflightAttemptV02321",
    "LiveTrafficPreflightPassV02321",
    "TRAFFIC_PREFLIGHT_ATTEMPT_FAIL_V02321",
    "TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V02321",
    "TRAFFIC_PREFLIGHT_PASS_V02321",
)
