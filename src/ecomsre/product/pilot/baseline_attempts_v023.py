"""Fresh, at-most-two-attempt Product v0.2.3 baseline ledger."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import os
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.baselines import BaselineJobCreateV1, EnvironmentBaselineV1
from ecomsre.product.connectors.base import ConnectorWindowV1
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    ACTIVE_PROFILE_SHA256_V023,
)
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.jobs.contracts import (
    ProductJobRecordV1,
    ProductJobStatusV1,
    ProductJobTypeV1,
)
from ecomsre.product.pilot.baseline_readiness_v023 import (
    ProductBaselineReadinessAuditV023,
    ProductBaselineReadinessProfileV023,
)


BASELINE_READINESS_PASS_V023 = "ECOMSRE_PRODUCT_V023_BASELINE_READINESS_PASS"
BASELINE_REPAIR_REQUIRED_V023 = (
    "ECOMSRE_PRODUCT_V023_BASELINE_REPAIR_REQUIRED"
)
BASELINE_READINESS_BLOCKED_V023 = (
    "BLOCKED_ECOMSRE_PRODUCT_V023_BASELINE_READINESS"
)


class BaselineChangedParameterV023(str, Enum):
    INITIAL = "INITIAL"
    HEALTHY_TRAFFIC_REQUEST_COUNT = "healthy_traffic_request_count"
    HEALTHY_TRAFFIC_REQUESTS_PER_SECOND = "healthy_traffic_requests_per_second"
    MAXIMUM_ERROR_FRACTION = "maximum_error_fraction"
    WARMUP_SECONDS = "warmup_seconds"
    BASELINE_ACCUMULATION_SECONDS = "baseline_accumulation_seconds"
    MINIMUM_ACCEPTED_WINDOWS = "minimum_accepted_windows"
    CONNECTOR_QUERY_BINDING_SHA256 = "connector_query_binding_sha256"
    SERVICE_ALIAS_BINDING_SHA256 = "service_alias_binding_sha256"
    IMPLEMENTATION_REVISION_SHA256 = "implementation_revision_sha256"


class BaselineAttemptFailureKindV023(str, Enum):
    HEALTHY_TRAFFIC = "HEALTHY_TRAFFIC"
    CONNECTOR_QUERY_BINDING = "CONNECTOR_QUERY_BINDING"
    SERVICE_ALIAS_BINDING = "SERVICE_ALIAS_BINDING"
    BUILDER = "BUILDER"
    PERSISTENCE = "PERSISTENCE"
    IMPLEMENTATION = "IMPLEMENTATION"


_ALLOWED_REPAIR_PARAMETER_BY_FAILURE_V023 = {
    BaselineAttemptFailureKindV023.HEALTHY_TRAFFIC: frozenset(
        {BaselineChangedParameterV023.IMPLEMENTATION_REVISION_SHA256}
    ),
    BaselineAttemptFailureKindV023.CONNECTOR_QUERY_BINDING: frozenset(
        {
            BaselineChangedParameterV023.CONNECTOR_QUERY_BINDING_SHA256,
            BaselineChangedParameterV023.IMPLEMENTATION_REVISION_SHA256,
        }
    ),
    BaselineAttemptFailureKindV023.SERVICE_ALIAS_BINDING: frozenset(
        {
            BaselineChangedParameterV023.SERVICE_ALIAS_BINDING_SHA256,
            BaselineChangedParameterV023.IMPLEMENTATION_REVISION_SHA256,
        }
    ),
    BaselineAttemptFailureKindV023.BUILDER: frozenset(
        {BaselineChangedParameterV023.IMPLEMENTATION_REVISION_SHA256}
    ),
    BaselineAttemptFailureKindV023.PERSISTENCE: frozenset(
        {BaselineChangedParameterV023.IMPLEMENTATION_REVISION_SHA256}
    ),
    BaselineAttemptFailureKindV023.IMPLEMENTATION: frozenset(
        {BaselineChangedParameterV023.IMPLEMENTATION_REVISION_SHA256}
    ),
}


def baseline_builder_job_evidence_sha256_v023(
    job: ProductJobRecordV1,
) -> str:
    return semantic_sha256_v22(job.model_dump(mode="json"))


class BaselineTrafficResultV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.baseline-traffic-result.v023"
    ] = "ecomsre.product.baseline-traffic-result.v023"
    planned_request_count: int = Field(ge=1)
    completed_request_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    requests_per_second: float = Field(gt=0, allow_inf_nan=False)
    maximum_error_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    queue_fault_flag: Literal[0]
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantics_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    passed: bool
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_result(self) -> "BaselineTrafficResultV023":
        if self.completed_request_count > self.planned_request_count:
            raise ValueError("baseline traffic completed count exceeds its plan")
        if self.error_count > self.completed_request_count:
            raise ValueError("baseline traffic error count exceeds completion")
        measured_error_fraction = (
            1.0
            if self.completed_request_count == 0
            else self.error_count / self.completed_request_count
        )
        measured_pass = (
            self.completed_request_count == self.planned_request_count
            and measured_error_fraction <= self.maximum_error_fraction
        )
        if self.passed != measured_pass:
            raise ValueError("baseline traffic disposition differs from measured counts")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("baseline traffic result digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "BaselineTrafficResultV023":
        body = {
            "schema_version": "ecomsre.product.baseline-traffic-result.v023",
            **payload,
        }
        return cls.model_validate(
            {**body, "result_sha256": semantic_sha256_v22(body)}
        )


class BaselineAttemptStartV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.baseline-attempt-start.v023"
    ] = "ecomsre.product.baseline-attempt-start.v023"
    attempt_ordinal: int = Field(ge=1, le=2)
    changed_parameter: BaselineChangedParameterV023
    prior_completion_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    product_data_root: str = Field(min_length=2, max_length=1024)
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    readiness_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    planned_windows: tuple[ConnectorWindowV1, ...]
    semantic_inputs: dict[str, Any]
    semantics_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: datetime
    maximum_changed_attempts: Literal[2] = 2
    action_authority: Literal["NONE"] = "NONE"
    start_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_start(self) -> "BaselineAttemptStartV023":
        product_root = Path(self.product_data_root)
        if (
            not product_root.is_absolute()
            or os.path.normpath(self.product_data_root) != self.product_data_root
        ):
            raise ValueError("baseline attempt data root must be absolute")
        readiness = ProductBaselineReadinessProfileV023.default()
        frozen_semantics: dict[str, object] = {
            "healthy_traffic_request_count": readiness.healthy_traffic_request_count,
            "healthy_traffic_requests_per_second": (
                readiness.healthy_traffic_requests_per_second
            ),
            "maximum_error_fraction": readiness.maximum_error_fraction,
            "warmup_seconds": readiness.warmup_seconds,
            "baseline_accumulation_seconds": (
                readiness.baseline_accumulation_seconds
            ),
            "minimum_accepted_windows": readiness.minimum_accepted_windows,
            "queue_fault_flag": readiness.queue_fault_flag,
        }
        mutable_semantics = {
            "connector_query_binding_sha256",
            "service_alias_binding_sha256",
            "implementation_revision_sha256",
        }
        if (
            self.profile_sha256 != ACTIVE_PROFILE_SHA256_V023
            or self.readiness_profile_sha256 != readiness.profile_sha256
            or set(self.semantic_inputs) != set(frozen_semantics).union(mutable_semantics)
            or any(
                self.semantic_inputs.get(name) != value
                for name, value in frozen_semantics.items()
            )
            or any(
                not isinstance(self.semantic_inputs.get(name), str)
                or re.fullmatch(r"[0-9a-f]{64}", self.semantic_inputs[name]) is None
                for name in mutable_semantics
            )
        ):
            raise ValueError("baseline attempt differs from frozen v0.2.3 profiles")
        if len(self.planned_windows) != 5:
            raise ValueError("baseline attempt must plan all five windows")
        if any(
            left.ended_at > right.started_at
            for left, right in zip(self.planned_windows, self.planned_windows[1:])
        ) or self.planned_windows[0].started_at < self.started_at:
            raise ValueError("baseline attempt windows are not a future canonical schedule")
        if self.started_at.tzinfo is None or self.started_at.utcoffset() != timedelta(0):
            raise ValueError("baseline attempt start time must be UTC")
        if self.semantics_sha256 != semantic_sha256_v22(self.semantic_inputs):
            raise ValueError("baseline attempt semantic-input digest differs")
        if self.attempt_ordinal == 1:
            if (
                self.changed_parameter is not BaselineChangedParameterV023.INITIAL
                or self.prior_completion_sha256 is not None
            ):
                raise ValueError("first baseline attempt must use INITIAL inputs")
        elif (
            self.changed_parameter is BaselineChangedParameterV023.INITIAL
            or self.prior_completion_sha256 is None
        ):
            raise ValueError("second baseline attempt lacks prior evidence")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"start_sha256"})
        )
        if self.start_sha256 != expected:
            raise ValueError("baseline attempt start digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "BaselineAttemptStartV023":
        readiness = ProductBaselineReadinessProfileV023.default()
        semantic_inputs = {
            "healthy_traffic_request_count": readiness.healthy_traffic_request_count,
            "healthy_traffic_requests_per_second": (
                readiness.healthy_traffic_requests_per_second
            ),
            "maximum_error_fraction": readiness.maximum_error_fraction,
            "warmup_seconds": readiness.warmup_seconds,
            "baseline_accumulation_seconds": readiness.baseline_accumulation_seconds,
            "minimum_accepted_windows": readiness.minimum_accepted_windows,
            "queue_fault_flag": readiness.queue_fault_flag,
            **dict(payload["semantic_inputs"]),
        }
        body = {
            "schema_version": "ecomsre.product.baseline-attempt-start.v023",
            **payload,
            "readiness_profile_sha256": readiness.profile_sha256,
            "semantic_inputs": semantic_inputs,
            "semantics_sha256": semantic_sha256_v22(semantic_inputs),
            "maximum_changed_attempts": 2,
            "action_authority": "NONE",
        }
        draft = cls.model_construct(**body, start_sha256="0" * 64)
        normalized = draft.model_dump(mode="json", exclude={"start_sha256"})
        return cls.model_validate(
            {**normalized, "start_sha256": semantic_sha256_v22(normalized)}
        )


class BaselineAttemptCompletionV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.baseline-attempt-completion.v023"
    ] = "ecomsre.product.baseline-attempt-completion.v023"
    attempt_ordinal: int = Field(ge=1, le=2)
    start_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    traffic_result: BaselineTrafficResultV023 | None
    per_window_audit: ProductBaselineReadinessAuditV023 | None = None
    per_window_audit_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    builder_job_id: str | None = Field(default=None, pattern=r"^job-[0-9a-f]{24}$")
    builder_job_record: ProductJobRecordV1 | None = None
    builder_job_evidence_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    builder_job_disposition: Literal["SUCCEEDED", "FAILED", "NOT_SUBMITTED"]
    active_baseline_id: str | None = Field(
        default=None, pattern=r"^base-[0-9a-f]{24}$"
    )
    active_baseline_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    cleanup: Literal["CLEAN", "BLOCKED", "NOT_REQUIRED"]
    failure_kind: BaselineAttemptFailureKindV023 | None = None
    failure_code: str | None = Field(
        default=None,
        pattern=r"^[A-Z][A-Z0-9_]{2,119}$",
    )
    failure_evidence_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    terminal: Literal[
        "ECOMSRE_PRODUCT_V023_BASELINE_READINESS_PASS",
        "ECOMSRE_PRODUCT_V023_BASELINE_REPAIR_REQUIRED",
        "BLOCKED_ECOMSRE_PRODUCT_V023_BASELINE_READINESS",
    ]
    completed_at: datetime
    action_authority: Literal["NONE"] = "NONE"
    agent_writes: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    completion_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_completion(self) -> "BaselineAttemptCompletionV023":
        if self.completed_at.tzinfo is None or self.completed_at.utcoffset() != timedelta(0):
            raise ValueError("baseline attempt completion time must be UTC")
        has_baseline = self.active_baseline_id is not None
        if has_baseline != (self.active_baseline_sha256 is not None):
            raise ValueError("baseline attempt active baseline binding differs")
        has_audit = self.per_window_audit is not None
        if has_audit != (self.per_window_audit_sha256 is not None):
            raise ValueError("baseline attempt readiness audit evidence differs")
        if (
            self.per_window_audit is not None
            and self.per_window_audit.audit_sha256
            != self.per_window_audit_sha256
        ):
            raise ValueError("baseline attempt readiness audit SHA differs")
        has_builder_job = self.builder_job_id is not None
        has_builder_record = self.builder_job_record is not None
        has_builder_evidence = self.builder_job_evidence_sha256 is not None
        if not (has_builder_job == has_builder_record == has_builder_evidence):
            raise ValueError("baseline attempt builder job evidence differs")
        if self.builder_job_disposition == "NOT_SUBMITTED":
            if has_builder_job or has_baseline:
                raise ValueError("unsubmitted baseline builder has job or baseline")
        elif self.builder_job_disposition == "FAILED":
            if not has_builder_job or has_baseline:
                raise ValueError("failed baseline builder state is contradictory")
        elif not has_builder_job:
            raise ValueError("successful baseline builder lacks job evidence")
        if has_baseline and self.builder_job_disposition != "SUCCEEDED":
            raise ValueError("active baseline lacks successful builder disposition")
        if has_builder_job:
            if (
                self.builder_job_id is None
                or self.builder_job_record is None
                or self.builder_job_disposition == "NOT_SUBMITTED"
                or self.per_window_audit is None
                or self.per_window_audit_sha256 is None
            ):
                raise ValueError("submitted baseline builder lacks window audit")
            job = self.builder_job_record
            audit = self.per_window_audit
            try:
                request = BaselineJobCreateV1.model_validate(
                    job.payload.get("request")
                )
            except (TypeError, ValueError) as error:
                raise ValueError("baseline builder request binding differs") from error
            expected_status = (
                ProductJobStatusV1.SUCCEEDED
                if self.builder_job_disposition == "SUCCEEDED"
                else ProductJobStatusV1.FAILED
            )
            if (
                job.job_id != self.builder_job_id
                or job.job_type is not ProductJobTypeV1.BASELINE_BUILD
                or job.status is not expected_status
                or job.payload.get("environment_id") != audit.environment_id
                or not request.activate
                or request.candidate_services != ("checkout",)
                or request.build_policy.model_dump(mode="json") != audit.build_policy
                or audit.service_ids != ("checkout",)
            ):
                raise ValueError("baseline builder JobRecord binding differs")
            expected_job_evidence = baseline_builder_job_evidence_sha256_v023(job)
            if self.builder_job_evidence_sha256 != expected_job_evidence:
                raise ValueError("baseline builder job evidence is not bound")
            if self.builder_job_disposition == "SUCCEEDED":
                if job.safe_error_code is not None or not isinstance(job.result, dict):
                    raise ValueError("successful baseline JobRecord lacks result")
                raw_audit = job.result.get("readiness_audit_v023")
                if not isinstance(raw_audit, dict):
                    raise ValueError("successful baseline JobRecord lacks readiness audit")
                result_audit = ProductBaselineReadinessAuditV023.model_validate(
                    raw_audit
                )
                if result_audit != audit:
                    raise ValueError("Builder JobRecord readiness audit differs")
                raw_baseline = {
                    key: value
                    for key, value in job.result.items()
                    if key != "readiness_audit_v023"
                }
                try:
                    result_baseline = EnvironmentBaselineV1.model_validate(
                        raw_baseline,
                        strict=False,
                    )
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        "successful baseline JobRecord result differs"
                    ) from error
                result_baseline_id = result_baseline.baseline_id
                result_baseline_sha256 = result_baseline.baseline_sha256
                if (
                    not result_baseline.active
                    or result_baseline.environment_id != audit.environment_id
                    or result_baseline.service_ids
                    != audit.baseline_entity_service_ids
                    or result_baseline.source_capability_sha256
                    != audit.capability_sha256
                    or result_baseline.build_policy.model_dump(mode="json")
                    != audit.build_policy
                ):
                    raise ValueError("successful baseline JobRecord binding differs")
                if has_baseline and (
                    result_baseline_id != self.active_baseline_id
                    or result_baseline_sha256 != self.active_baseline_sha256
                ):
                    raise ValueError("active baseline differs from Builder JobRecord")
                if (
                    result_baseline_id != audit.baseline_id
                    or result_baseline_sha256 != audit.baseline_sha256
                ):
                    raise ValueError("Builder JobRecord differs from readiness audit")
            elif job.safe_error_code is None or job.result is not None:
                raise ValueError("failed baseline JobRecord lacks safe error")
        if self.terminal == BASELINE_READINESS_PASS_V023:
            if (
                self.traffic_result is None
                or not self.traffic_result.passed
                or self.per_window_audit_sha256 is None
                or self.builder_job_id is None
                or self.builder_job_disposition != "SUCCEEDED"
                or not has_baseline
                or self.failure_kind is not None
                or self.failure_code is not None
                or self.failure_evidence_sha256 is not None
                or self.cleanup != "NOT_REQUIRED"
            ):
                raise ValueError("passing baseline attempt is incomplete")
        else:
            if (
                self.failure_kind is None
                or self.failure_code is None
                or self.failure_evidence_sha256 is None
            ):
                raise ValueError("failed baseline attempt lacks exact failure evidence")
            failure_evidence = self._expected_failure_evidence_sha256()
            if self.failure_evidence_sha256 != failure_evidence:
                raise ValueError("baseline attempt failure evidence is not bound")
            if (
                self.terminal == BASELINE_REPAIR_REQUIRED_V023
                and self.attempt_ordinal >= 2
            ):
                raise ValueError("baseline repair terminal is not eligible")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"completion_sha256"})
        )
        if self.completion_sha256 != expected:
            raise ValueError("baseline attempt completion digest differs")
        return self

    def _expected_failure_evidence_sha256(self) -> str:
        traffic = self.traffic_result
        audit = self.per_window_audit_sha256
        job_evidence = self.builder_job_evidence_sha256
        failure_kind = self.failure_kind
        if failure_kind is BaselineAttemptFailureKindV023.HEALTHY_TRAFFIC:
            if (
                traffic is None
                or traffic.passed
                or self.builder_job_disposition != "NOT_SUBMITTED"
            ):
                raise ValueError("baseline traffic failure state is contradictory")
            if self.failure_code != "HEALTHY_TRAFFIC_INCOMPLETE":
                raise ValueError("baseline traffic failure code differs")
            return traffic.result_sha256
        if failure_kind in {
            BaselineAttemptFailureKindV023.CONNECTOR_QUERY_BINDING,
            BaselineAttemptFailureKindV023.SERVICE_ALIAS_BINDING,
        }:
            if (
                traffic is None
                or not traffic.passed
                or audit is None
                or self.builder_job_disposition != "NOT_SUBMITTED"
            ):
                raise ValueError("baseline connector failure state is contradictory")
            expected_code = (
                "CONNECTOR_QUERY_BINDING_INVALID"
                if failure_kind
                is BaselineAttemptFailureKindV023.CONNECTOR_QUERY_BINDING
                else "SERVICE_ALIAS_BINDING_INVALID"
            )
            if self.failure_code != expected_code:
                raise ValueError("baseline connector failure code differs")
            return audit
        if failure_kind in {
            BaselineAttemptFailureKindV023.BUILDER,
            BaselineAttemptFailureKindV023.IMPLEMENTATION,
        }:
            if (
                traffic is None
                or not traffic.passed
                or audit is None
                or self.builder_job_disposition != "FAILED"
                or job_evidence is None
                or self.builder_job_record is None
            ):
                raise ValueError("baseline builder failure state is contradictory")
            if self.failure_code != self.builder_job_record.safe_error_code:
                raise ValueError("baseline builder failure code differs")
            return job_evidence
        if failure_kind is BaselineAttemptFailureKindV023.PERSISTENCE:
            if (
                traffic is None
                or not traffic.passed
                or audit is None
                or self.builder_job_disposition != "SUCCEEDED"
                or job_evidence is None
                or self.active_baseline_id is not None
            ):
                raise ValueError("baseline persistence failure state is contradictory")
            if self.failure_code != "BASELINE_ACTIVATION_MISSING":
                raise ValueError("baseline persistence failure code differs")
            return job_evidence
        raise ValueError("baseline attempt failure kind is absent")

    @classmethod
    def build(cls, **payload: Any) -> "BaselineAttemptCompletionV023":
        traffic_result = payload.get("traffic_result")
        if isinstance(traffic_result, dict):
            traffic_result = BaselineTrafficResultV023.model_validate(traffic_result)
        builder_job_record = payload.get("builder_job_record")
        if isinstance(builder_job_record, dict):
            builder_job_record = ProductJobRecordV1.model_validate(builder_job_record)
        per_window_audit = payload.get("per_window_audit")
        if isinstance(per_window_audit, dict):
            per_window_audit = ProductBaselineReadinessAuditV023.model_validate(
                per_window_audit
            )
        body = {
            "schema_version": "ecomsre.product.baseline-attempt-completion.v023",
            **payload,
            "traffic_result": traffic_result,
            "per_window_audit": per_window_audit,
            "builder_job_record": builder_job_record,
            "action_authority": "NONE",
            "agent_writes": 0,
            "runbook_executions": 0,
        }
        draft = cls.model_construct(**body, completion_sha256="0" * 64)
        normalized = draft.model_dump(mode="json", exclude={"completion_sha256"})
        return cls.model_validate(
            {
                **normalized,
                "completion_sha256": semantic_sha256_v22(normalized),
            }
        )


class BaselineAttemptV023(ProductModelV1):
    start: BaselineAttemptStartV023
    completion: BaselineAttemptCompletionV023

    @model_validator(mode="after")
    def require_pair(self) -> "BaselineAttemptV023":
        if (
            self.start.attempt_ordinal != self.completion.attempt_ordinal
            or self.start.start_sha256 != self.completion.start_sha256
        ):
            raise ValueError("baseline attempt start/completion binding differs")
        if self.completion.completed_at < max(
            window.ended_at for window in self.start.planned_windows
        ):
            raise ValueError("baseline attempt completed before its planned windows")
        traffic = self.completion.traffic_result
        if traffic is not None:
            semantic_inputs = self.start.semantic_inputs
            expected = {
                "planned_request_count": semantic_inputs.get(
                    "healthy_traffic_request_count"
                ),
                "requests_per_second": semantic_inputs.get(
                    "healthy_traffic_requests_per_second"
                ),
                "maximum_error_fraction": semantic_inputs.get(
                    "maximum_error_fraction"
                ),
                "queue_fault_flag": semantic_inputs.get("queue_fault_flag", 0),
                "profile_sha256": self.start.profile_sha256,
                "semantics_sha256": self.start.semantics_sha256,
            }
            if any(getattr(traffic, name) != value for name, value in expected.items()):
                raise ValueError(
                    "baseline traffic result differs from frozen attempt inputs"
                )
        audit = self.completion.per_window_audit
        if audit is not None and (
            audit.environment_id != self.start.environment_id
            or audit.profile_sha256 != self.start.readiness_profile_sha256
            or audit.active_opensearch_profile_sha256 != self.start.profile_sha256
        ):
            raise ValueError("baseline attempt readiness audit differs from start")
        return self


class BaselineAttemptLedgerV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.baseline-attempt-ledger.v023"
    ] = "ecomsre.product.baseline-attempt-ledger.v023"
    attempts: tuple[BaselineAttemptV023, ...]
    maximum_changed_attempts: Literal[2] = 2
    ledger_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_closed_ledger(self) -> "BaselineAttemptLedgerV023":
        if not self.attempts or len(self.attempts) > 2:
            raise ValueError("baseline attempt ledger size differs")
        if tuple(item.start.attempt_ordinal for item in self.attempts) != tuple(
            range(1, len(self.attempts) + 1)
        ):
            raise ValueError("baseline attempt ledger sequence differs")
        if len(self.attempts) == 2:
            prior, current = self.attempts
            if (
                prior.completion.terminal != BASELINE_REPAIR_REQUIRED_V023
                or prior.completion.cleanup != "CLEAN"
                or current.start.prior_completion_sha256
                != prior.completion.completion_sha256
            ):
                raise ValueError("second baseline attempt lacks eligible prior evidence")
            if (
                prior.start.environment_id == current.start.environment_id
                or Path(prior.start.product_data_root).resolve()
                == Path(current.start.product_data_root).resolve()
            ):
                raise ValueError(
                    "second baseline attempt must use a new environment and data root"
                )
            if (
                prior.start.profile_sha256 != current.start.profile_sha256
                or prior.start.readiness_profile_sha256
                != current.start.readiness_profile_sha256
            ):
                raise ValueError("second baseline attempt changed frozen P01 bytes")
            prior_windows = prior.start.planned_windows
            current_windows = current.start.planned_windows
            prior_shape = tuple(
                (
                    item.ended_at - item.started_at,
                    None
                    if index == 0
                    else item.started_at - prior_windows[index - 1].ended_at,
                )
                for index, item in enumerate(prior_windows)
            )
            current_shape = tuple(
                (
                    item.ended_at - item.started_at,
                    None
                    if index == 0
                    else item.started_at - current_windows[index - 1].ended_at,
                )
                for index, item in enumerate(current_windows)
            )
            if (
                prior_shape != current_shape
                or current_windows[0].started_at <= prior_windows[-1].ended_at
                or current_windows[0].started_at < current.start.started_at
                or current.start.started_at < prior.completion.completed_at
            ):
                raise ValueError(
                    "second baseline attempt planned-window schedule is not a fresh equivalent"
                )
            left = prior.start.semantic_inputs
            right = current.start.semantic_inputs
            changed = tuple(
                sorted(
                    key
                    for key in set(left).union(right)
                    if left.get(key) != right.get(key)
                )
            )
            if changed != (current.start.changed_parameter.value,):
                raise ValueError("second baseline attempt changed more than one parameter")
            failure_kind = prior.completion.failure_kind
            if (
                failure_kind is None
                or current.start.changed_parameter
                not in _ALLOWED_REPAIR_PARAMETER_BY_FAILURE_V023[failure_kind]
            ):
                raise ValueError(
                    "second baseline attempt change does not match prior failure"
                )
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"ledger_sha256"})
        )
        if self.ledger_sha256 != expected:
            raise ValueError("baseline attempt ledger digest differs")
        return self

    @classmethod
    def build(
        cls,
        attempts: tuple[BaselineAttemptV023, ...],
    ) -> "BaselineAttemptLedgerV023":
        body = {
            "schema_version": "ecomsre.product.baseline-attempt-ledger.v023",
            "attempts": tuple(item.model_dump(mode="json") for item in attempts),
            "maximum_changed_attempts": 2,
        }
        return cls.model_validate(
            {**body, "ledger_sha256": semantic_sha256_v22(body)}
        )


__all__ = (
    "BASELINE_READINESS_BLOCKED_V023",
    "BASELINE_READINESS_PASS_V023",
    "BASELINE_REPAIR_REQUIRED_V023",
    "BaselineAttemptCompletionV023",
    "BaselineAttemptFailureKindV023",
    "BaselineAttemptLedgerV023",
    "BaselineAttemptStartV023",
    "BaselineAttemptV023",
    "BaselineChangedParameterV023",
    "BaselineTrafficResultV023",
    "baseline_builder_job_evidence_sha256_v023",
)
