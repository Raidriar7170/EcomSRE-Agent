"""Closed-world contracts for the DTA v2.1 PR-F local live portfolio."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, StrictBool, StrictInt, model_validator
from pydantic_core import to_jsonable_python

from ecomsre.dta_v2.v21.contracts import (
    DtaModelV21,
    EvidenceSourceV21,
    FaultDomainV21,
    FaultMechanismV21,
    RunbookIdV21,
    RunbookStepIdV21,
    Sha256V21,
    semantic_sha256,
)


class LiveScenarioV21(str, Enum):
    NO_FAULT = "NO_FAULT"
    AD_CPU_SATURATION = "AD_CPU_SATURATION"
    EMAIL_SERVICE_UNAVAILABLE = "EMAIL_SERVICE_UNAVAILABLE"
    PRODUCT_CATALOG_SERVICE_UNAVAILABLE = "PRODUCT_CATALOG_SERVICE_UNAVAILABLE"


LIVE_CAMPAIGN_ORDER_V21 = (
    LiveScenarioV21.NO_FAULT,
    LiveScenarioV21.AD_CPU_SATURATION,
    LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
    LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE,
)


def _semantic(value: object) -> str:
    return semantic_sha256(to_jsonable_python(value))


class LiveScenarioSpecV21(DtaModelV21):
    schema_version: Literal["dta-v21.live-scenario-spec.v1"]
    scenario: LiveScenarioV21
    scenario_id: str
    capture_case_id: str
    capture_case_ordinal: StrictInt
    target_service: str | None
    expected_fault_domain: FaultDomainV21 | None
    expected_mechanism: FaultMechanismV21 | None
    required_evidence_sources: tuple[EvidenceSourceV21, ...]
    expected_runbook: RunbookIdV21 | None
    expected_step: RunbookStepIdV21 | None
    maximum_forward_steps: Literal[0, 1]

    @model_validator(mode="after")
    def require_exact_slot(self) -> Self:
        expected: dict[LiveScenarioV21, tuple[object, ...]] = {
            LiveScenarioV21.NO_FAULT: (
                "dta21-dev-005",
                "dta21-case-011",
                11,
                None,
                None,
                None,
                (EvidenceSourceV21.METRICS, EvidenceSourceV21.RUNTIME),
                None,
                None,
                0,
            ),
            LiveScenarioV21.AD_CPU_SATURATION: (
                "dta21-dev-001",
                "dta21-case-006",
                6,
                "ad",
                FaultDomainV21.LOCAL_RESOURCE,
                FaultMechanismV21.CPU_SATURATION,
                (
                    EvidenceSourceV21.METRICS,
                    EvidenceSourceV21.RUNTIME,
                    EvidenceSourceV21.RESOURCES,
                ),
                RunbookIdV21.MITIGATE_CPU_SATURATION,
                RunbookStepIdV21.DISABLE_AD_HIGH_CPU_FLAG,
                1,
            ),
            LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE: (
                "dta21-dev-002",
                "dta21-case-008",
                8,
                "email",
                FaultDomainV21.SERVICE_RUNTIME,
                FaultMechanismV21.SERVICE_UNAVAILABLE,
                (EvidenceSourceV21.METRICS, EvidenceSourceV21.RUNTIME),
                RunbookIdV21.RESTORE_SERVICE_AVAILABILITY,
                RunbookStepIdV21.START_OWNED_SERVICE,
                1,
            ),
            LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE: (
                "dta21-dev-003",
                "dta21-case-009",
                9,
                "product-catalog",
                FaultDomainV21.SERVICE_RUNTIME,
                FaultMechanismV21.SERVICE_UNAVAILABLE,
                (EvidenceSourceV21.TRACES, EvidenceSourceV21.RUNTIME),
                RunbookIdV21.RESTORE_SERVICE_AVAILABILITY,
                RunbookStepIdV21.START_OWNED_SERVICE,
                1,
            ),
        }
        observed = (
            self.scenario_id,
            self.capture_case_id,
            self.capture_case_ordinal,
            self.target_service,
            self.expected_fault_domain,
            self.expected_mechanism,
            self.required_evidence_sources,
            self.expected_runbook,
            self.expected_step,
            self.maximum_forward_steps,
        )
        if observed != expected[self.scenario]:
            raise ValueError("live scenario differs from the frozen PR-F slot")
        return self


class LiveDemoConfigV21(DtaModelV21):
    schema_version: Literal["dta-v21.live-demo-config.v1"]
    upstream_commit: Literal["1755859a9de82c2e5e225be68abc401a5ebf2b4f"]
    upstream_tag: Literal["3.0.0"]
    planner_identity_sha256: Literal[
        "80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d"
    ]
    provider_model: Literal["gpt-5.4-mini-2026-03-17"]
    maximum_completion_tokens: Literal[1600]
    protocol_sha256: Literal[
        "c983b9be95b532cdbb8fb5358af92055e633fd767693e9dc65743b3e80a77517"
    ]
    ad_recovery_stabilization_seconds: Literal[20]
    ad_resource_window_seconds: Literal[10]
    ad_resource_sample_count: Literal[5]
    ad_business_query_window_seconds: Literal[30]
    service_recovery_window_seconds: Literal[20]
    service_recovery_windows_required: Literal[2]
    service_baseline_windows_required: Literal[2]
    service_recovery_error_rate_multiplier: Literal["1.5"]
    service_recovery_error_rate_absolute_increase: Literal["0.02"]
    service_recovery_request_support_minimum: Literal["0"]
    service_recovery_requires_no_fresh_error_span: Literal[True]
    maximum_unsafe_proposal_attempts: Literal[0]
    maximum_arbitrary_shell_attempts: Literal[0]
    scenarios: tuple[LiveScenarioSpecV21, ...] = Field(min_length=4, max_length=4)
    config_sha256: Sha256V21

    @model_validator(mode="after")
    def require_order_and_digest(self) -> Self:
        if tuple(item.scenario for item in self.scenarios) != LIVE_CAMPAIGN_ORDER_V21:
            raise ValueError("live campaign order differs from the four frozen slots")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"config_sha256"})
        )
        if self.config_sha256 != expected:
            raise ValueError("live demo config SHA-256 mismatch")
        return self

    def require_scenario(self, scenario: LiveScenarioV21) -> LiveScenarioSpecV21:
        for item in self.scenarios:
            if item.scenario is scenario:
                return item
        raise KeyError(scenario.value)


class LiveReadinessV21(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f-readiness.v1"]
    terminal: Literal["DTA_V21_PR_F_PRELIVE_READY"]
    readiness_attempt_id: str = Field(pattern=r"^readiness-[0-9]{4}$")
    code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    exact_head_ci_success: Literal[True]
    exact_head_ci_run_id: StrictInt = Field(ge=1)
    exact_head_ci_run_url: str = Field(pattern=r"^https://github\.com/.+")
    branch: Literal["codex/dta-v21-p0-pr-f-live-closeout"]
    origin_main_is_ancestor: Literal[True]
    protocol_sha256: Sha256V21
    live_config_sha256: Sha256V21
    planner_identity_sha256: Sha256V21
    provider_model: Literal["gpt-5.4-mini-2026-03-17"]
    pr_e_claim: Literal["DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED"]
    docker_boundary: Literal["LOCAL_UNIX_DOCKER"]
    resolved_compose_sha256: Sha256V21
    baseline_flag_document_sha256: Sha256V21
    owned_resource_collisions: Literal[0]
    required_ports_available: Literal[True]
    cleanup_readiness: Literal["OWNED_SCOPE_ADMITTED"]
    private_permissions: Literal["0700_DIRECTORIES_0600_FILES"]
    master_authorization_sha256: Sha256V21
    readiness_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {"schema_version": "dta-v21.pr-f-readiness.v1", **values}
        return cls.model_validate({**payload, "readiness_sha256": _semantic(payload)})

    @model_validator(mode="after")
    def require_digest(self) -> Self:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"readiness_sha256"})
        )
        if self.readiness_sha256 != expected:
            raise ValueError("PR-F readiness SHA-256 mismatch")
        return self


class LiveEnvironmentAdmissionV21(DtaModelV21):
    schema_version: Literal["dta-v21.live-environment-admission.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_id: str = Field(min_length=1, max_length=128)
    scenario: LiveScenarioV21
    code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    readiness_sha256: Sha256V21
    resolved_compose_sha256: Sha256V21
    baseline_flag_document_sha256: Sha256V21
    docker_boundary: Literal["LOCAL_UNIX_DOCKER"]
    daemon_identity_sha256: Sha256V21
    docker_context_sha256: Sha256V21
    config_bundle_sha256: Sha256V21
    resolved_sandbox_sha256: Sha256V21
    resolved_endpoints_sha256: Sha256V21
    ownership_scope_sha256: Sha256V21
    read_authority_sha256: Sha256V21
    owned_inventory_sha256: Sha256V21
    non_owned_baseline_snapshot_sha256: Sha256V21
    owned_container_count: Literal[25]
    owned_network_count: Literal[1]
    owned_volume_count: Literal[3]
    admitted_at: datetime
    environment_admission_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {
            "schema_version": "dta-v21.live-environment-admission.v1",
            **values,
        }
        return cls.model_validate(
            {**payload, "environment_admission_sha256": _semantic(payload)}
        )

    @model_validator(mode="after")
    def require_admission(self) -> Self:
        if self.admitted_at.tzinfo is None or self.admitted_at.utcoffset() != timedelta(
            0
        ):
            raise ValueError("live environment admission timestamp requires UTC")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"environment_admission_sha256"})
        )
        if self.environment_admission_sha256 != expected:
            raise ValueError("live environment admission SHA-256 mismatch")
        return self


class LiveAdBaselineWindowV21(DtaModelV21):
    schema_version: Literal["dta-v21.live-ad-baseline-window.v1"]
    ordinal: Literal[1, 2]
    cpu_p95_percent: str = Field(pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
    sample_count: Literal[5]
    window_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {"schema_version": "dta-v21.live-ad-baseline-window.v1", **values}
        return cls.model_validate({**payload, "window_sha256": _semantic(payload)})

    @model_validator(mode="after")
    def require_window(self) -> Self:
        if Decimal(self.cpu_p95_percent) < 0:
            raise ValueError("Ad baseline CPU p95 must be non-negative")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"window_sha256"})
        )
        if self.window_sha256 != expected:
            raise ValueError("Ad baseline window SHA-256 mismatch")
        return self


class LiveBusinessBaselineWindowV21(DtaModelV21):
    schema_version: Literal["dta-v21.live-business-baseline-window.v1"]
    ordinal: Literal[1, 2]
    window_started_at: datetime
    window_ended_at: datetime
    business_anchor_service: Literal["checkout", "frontend", "payment"]
    business_error_rate: str = Field(pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
    request_support: str = Field(pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
    first_error_span_count: Literal[0] | None
    window_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {
            "schema_version": "dta-v21.live-business-baseline-window.v1",
            **values,
        }
        return cls.model_validate({**payload, "window_sha256": _semantic(payload)})

    @model_validator(mode="after")
    def require_window(self) -> Self:
        if (
            self.window_started_at.tzinfo is None
            or self.window_started_at.utcoffset() != timedelta(0)
            or self.window_ended_at.tzinfo is None
            or self.window_ended_at.utcoffset() != timedelta(0)
            or (self.window_ended_at - self.window_started_at).total_seconds() != 20
            or Decimal(self.request_support) <= 0
        ):
            raise ValueError("business baseline window differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"window_sha256"})
        )
        if self.window_sha256 != expected:
            raise ValueError("business baseline window SHA-256 mismatch")
        return self


class LiveBaselineEvidenceV21(DtaModelV21):
    schema_version: Literal["dta-v21.live-baseline-evidence.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_id: str = Field(min_length=1, max_length=128)
    scenario: LiveScenarioV21
    environment_admission_sha256: Sha256V21
    started_at: datetime
    baseline_state_sha256: Sha256V21
    windows: tuple[
        LiveAdBaselineWindowV21 | LiveBusinessBaselineWindowV21,
        LiveAdBaselineWindowV21 | LiveBusinessBaselineWindowV21,
    ]
    evidence_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {"schema_version": "dta-v21.live-baseline-evidence.v1", **values}
        return cls.model_validate({**payload, "evidence_sha256": _semantic(payload)})

    @model_validator(mode="after")
    def require_baseline(self) -> Self:
        if self.started_at.tzinfo is None or self.started_at.utcoffset() != timedelta(
            0
        ):
            raise ValueError("live baseline timestamp requires UTC")
        if tuple(item.ordinal for item in self.windows) != (1, 2):
            raise ValueError("live baseline windows differ from exact order")
        if self.scenario is LiveScenarioV21.AD_CPU_SATURATION:
            if any(type(item) is not LiveAdBaselineWindowV21 for item in self.windows):
                raise ValueError("Ad baseline windows differ")
        else:
            if any(
                type(item) is not LiveBusinessBaselineWindowV21 for item in self.windows
            ):
                raise ValueError("business baseline windows differ")
            expected_anchor = {
                LiveScenarioV21.NO_FAULT: "payment",
                LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE: "checkout",
                LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE: "frontend",
            }[self.scenario]
            if any(
                item.business_anchor_service != expected_anchor
                for item in self.windows
                if type(item) is LiveBusinessBaselineWindowV21
            ):
                raise ValueError("business baseline anchor differs")
            if any(
                (
                    item.first_error_span_count is None
                    if self.scenario is not LiveScenarioV21.NO_FAULT
                    else item.first_error_span_count is not None
                )
                for item in self.windows
                if type(item) is LiveBusinessBaselineWindowV21
            ):
                raise ValueError("business baseline trace predicate differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"evidence_sha256"})
        )
        if self.evidence_sha256 != expected:
            raise ValueError("live baseline evidence SHA-256 mismatch")
        return self


class LiveFaultImpactEvidenceV21(DtaModelV21):
    schema_version: Literal["dta-v21.live-fault-impact-evidence.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_id: str = Field(min_length=1, max_length=128)
    scenario: LiveScenarioV21
    environment_admission_sha256: Sha256V21
    baseline_evidence_sha256: Sha256V21
    baseline_state_sha256: Sha256V21
    fault_impact_kind: Literal["NO_FAULT", "RESOURCE_ONLY", "SERVICE_UNAVAILABLE"]
    fault_operation_count: Literal[0, 1]
    logical_service: Literal["ad", "email", "product-catalog"] | None
    business_anchor_service: Literal["checkout", "frontend"] | None
    baseline_unchanged: StrictBool | None
    cpu_p95_percent: str | None
    capacity_ratio: str | None
    sample_count: StrictInt | None
    safe: StrictBool | None
    measurable: StrictBool | None
    resource_fault_observed: StrictBool | None
    business_impact_required: StrictBool | None
    target_runtime_stopped: StrictBool | None
    business_error_rate: str | None
    first_error_span_count: StrictInt | None
    business_impact_observed: StrictBool | None
    same_owned_target_identity: StrictBool | None
    evidence_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {
            "schema_version": "dta-v21.live-fault-impact-evidence.v1",
            **values,
        }
        return cls.model_validate({**payload, "evidence_sha256": _semantic(payload)})

    @model_validator(mode="after")
    def require_fault_impact(self) -> Self:
        expected: tuple[object, ...]
        if self.scenario is LiveScenarioV21.NO_FAULT:
            expected = (
                "NO_FAULT",
                0,
                None,
                None,
                True,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
        elif self.scenario is LiveScenarioV21.AD_CPU_SATURATION:
            expected = (
                "RESOURCE_ONLY",
                1,
                "ad",
                None,
                None,
                self.cpu_p95_percent,
                self.capacity_ratio,
                5,
                True,
                True,
                True,
                False,
                None,
                None,
                None,
                None,
                True,
            )
            if self.cpu_p95_percent is None or self.capacity_ratio is None:
                raise ValueError("Ad fault impact lacks resource values")
            cpu = Decimal(self.cpu_p95_percent)
            ratio = Decimal(self.capacity_ratio)
            if (
                ratio < 0
                or cpu < Decimal("60")
                or cpu - Decimal("1.162") < Decimal("50")
                or cpu < Decimal("5.81")
                or ratio > Decimal("0.5")
            ):
                raise ValueError("accepted Ad resource fault-impact predicate failed")
        else:
            target = (
                "email"
                if self.scenario is LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE
                else "product-catalog"
            )
            anchor = "checkout" if target == "email" else "frontend"
            expected = (
                "SERVICE_UNAVAILABLE",
                1,
                target,
                anchor,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                True,
                self.business_error_rate,
                self.first_error_span_count,
                True,
                True,
            )
            if self.business_error_rate is None or self.first_error_span_count is None:
                raise ValueError("service fault impact lacks business evidence")
            if Decimal(self.business_error_rate) < 0 or self.first_error_span_count < 0:
                raise ValueError("service fault impact values must be non-negative")
            has_impact = (
                self.first_error_span_count > 0
                if target == "product-catalog"
                else self.first_error_span_count > 0
                or Decimal(self.business_error_rate) > 0
            )
            if not has_impact:
                raise ValueError("service fault impact predicate failed")
        observed = (
            self.fault_impact_kind,
            self.fault_operation_count,
            self.logical_service,
            self.business_anchor_service,
            self.baseline_unchanged,
            self.cpu_p95_percent,
            self.capacity_ratio,
            self.sample_count,
            self.safe,
            self.measurable,
            self.resource_fault_observed,
            self.business_impact_required,
            self.target_runtime_stopped,
            self.business_error_rate,
            self.first_error_span_count,
            self.business_impact_observed,
            self.same_owned_target_identity,
        )
        if observed != expected:
            raise ValueError("live fault-impact evidence differs from its scenario")
        digest = semantic_sha256(
            self.model_dump(mode="json", exclude={"evidence_sha256"})
        )
        if self.evidence_sha256 != digest:
            raise ValueError("live fault-impact evidence SHA-256 mismatch")
        return self


class LiveCurrentStateV21(DtaModelV21):
    schema_version: Literal["dta-v21.live-current-state.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_id: str = Field(min_length=1, max_length=128)
    scenario: LiveScenarioV21
    target_service: str
    owned_target_identity_sha256: Sha256V21
    daemon_identity_sha256: Sha256V21
    docker_boundary: Literal["LOCAL_UNIX_DOCKER"]
    docker_context_sha256: Sha256V21
    ownership_scope_sha256: Sha256V21
    sandbox_identity_sha256: Sha256V21
    baseline_state_sha256: Sha256V21
    current_state_sha256: Sha256V21
    ad_high_cpu_active: StrictBool
    target_runtime_stopped: StrictBool
    fault_operation_count: Literal[1]
    prior_forward_step_count: Literal[0]
    active_transaction_count: Literal[0]
    non_owned_changes: Literal[0]
    observation_started_at: datetime
    observed_at: datetime
    snapshot_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {"schema_version": "dta-v21.live-current-state.v1", **values}
        return cls.model_validate({**payload, "snapshot_sha256": _semantic(payload)})

    @model_validator(mode="after")
    def require_digest(self) -> Self:
        if (
            self.observation_started_at.tzinfo is None
            or self.observation_started_at.utcoffset() != timedelta(0)
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() != timedelta(0)
            or self.observed_at < self.observation_started_at
            or self.observed_at - self.observation_started_at > timedelta(seconds=30)
        ):
            raise ValueError("live current-state observation window differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"snapshot_sha256"})
        )
        if self.snapshot_sha256 != expected:
            raise ValueError("live current-state snapshot SHA-256 mismatch")
        return self


class ServiceRecoveryWindowV21(DtaModelV21):
    schema_version: Literal["dta-v21.service-recovery-window.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_id: str = Field(min_length=1, max_length=128)
    scenario: Literal[
        LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
        LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE,
    ]
    target_service: Literal["email", "product-catalog"]
    business_anchor_service: Literal["checkout", "frontend"]
    ordinal: Literal[1, 2]
    window_started_at: datetime
    window_ended_at: datetime
    service_running: StrictBool
    service_health_passed: StrictBool
    endpoint_reachable: StrictBool
    baseline_business_error_rate: str = Field(
        pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"
    )
    recovery_error_rate_threshold: str = Field(
        pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"
    )
    business_error_rate: str = Field(pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
    request_support: str = Field(pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
    first_error_span_count: StrictInt = Field(ge=0)
    business_impact_observed: StrictBool
    window_sha256: Sha256V21

    @model_validator(mode="after")
    def require_window(self) -> Self:
        if (
            self.window_started_at.tzinfo is None
            or self.window_started_at.utcoffset() != timedelta(0)
            or self.window_ended_at.tzinfo is None
            or self.window_ended_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("service recovery window timestamps require UTC")
        if (self.window_ended_at - self.window_started_at).total_seconds() != 20:
            raise ValueError("service recovery window must be exactly 20 seconds")
        expected_target = {
            LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE: "email",
            LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE: "product-catalog",
        }[self.scenario]
        if self.target_service != expected_target:
            raise ValueError("service recovery target differs from its scenario")
        expected_anchor = {
            "email": "checkout",
            "product-catalog": "frontend",
        }[self.target_service]
        if self.business_anchor_service != expected_anchor:
            raise ValueError("service recovery business anchor differs from PR-D")
        from decimal import Decimal

        baseline = Decimal(self.baseline_business_error_rate)
        expected_threshold = max(baseline * Decimal("1.5"), baseline + Decimal("0.02"))
        if Decimal(self.recovery_error_rate_threshold) != expected_threshold:
            raise ValueError("service recovery error-rate threshold differs")
        expected_impact = not (
            Decimal(self.request_support) > Decimal("0")
            and Decimal(self.business_error_rate) <= expected_threshold
            and self.first_error_span_count == 0
        )
        if self.business_impact_observed is not expected_impact:
            raise ValueError("service business-recovery predicate differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"window_sha256"})
        )
        if self.window_sha256 != expected:
            raise ValueError("service recovery window SHA-256 mismatch")
        return self


def build_service_recovery_window_v21(**values: object) -> ServiceRecoveryWindowV21:
    payload = {"schema_version": "dta-v21.service-recovery-window.v1", **values}
    return ServiceRecoveryWindowV21.model_validate(
        {**payload, "window_sha256": _semantic(payload)}
    )


class ServiceRecoveryResultV21(DtaModelV21):
    schema_version: Literal["dta-v21.service-recovery-result.v1"]
    terminal: Literal["SERVICE_AVAILABILITY_RECOVERY_PASS"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_id: str
    scenario: Literal[
        LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
        LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE,
    ]
    target_service: Literal["email", "product-catalog"]
    recovery_windows_required: Literal[2]
    recovery_windows_passed: Literal[2]
    windows: tuple[ServiceRecoveryWindowV21, ServiceRecoveryWindowV21]
    same_owned_identity: Literal[True]
    baseline_state_digest_restored: Literal[True]
    non_owned_changes: Literal[0]
    unsafe_proposal_attempts: Literal[0]
    arbitrary_shell_attempts: Literal[0]
    result_sha256: Sha256V21

    @model_validator(mode="after")
    def require_digest(self) -> Self:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("service recovery result SHA-256 mismatch")
        return self


class LiveAttemptClosureV21(DtaModelV21):
    schema_version: Literal["dta-v21.live-attempt-closure.v1"]
    scenario: LiveScenarioV21
    attempt_id: str
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    status: Literal["PASS"]
    terminal: Literal[
        "NO_FAULT_ZERO_WRITE_PASS",
        "AD_CPU_RESOURCE_RECOVERY_PASS",
        "SERVICE_AVAILABILITY_RECOVERY_PASS",
    ]
    planner_identity_sha256: Sha256V21
    readiness_sha256: Sha256V21
    environment_admission_sha256: Sha256V21
    baseline_evidence_sha256: Sha256V21
    fault_impact_sha256: Sha256V21
    agent_result_sha256: Sha256V21
    operational_admission_sha256: Sha256V21
    run_authorization_sha256: Sha256V21 | None
    fault_operation_count: Literal[0, 1]
    forward_step_count: Literal[0, 1]
    step_receipt_sha256: Sha256V21 | None
    recovery_result_sha256: Sha256V21 | None
    baseline_state_digest_restored: Literal[True]
    cleanup_verdict: Literal["CLEAN"]
    owned_containers_remaining: Literal[0]
    owned_networks_remaining: Literal[0]
    owned_volumes_remaining: Literal[0]
    non_owned_changes: Literal[0]
    unsafe_proposal_attempts: Literal[0]
    arbitrary_shell_attempts: Literal[0]
    provider_attempted_calls: StrictInt = Field(ge=1, le=6)
    closure_sha256: Sha256V21

    @model_validator(mode="after")
    def require_scenario_shape_and_digest(self) -> Self:
        expected = {
            LiveScenarioV21.NO_FAULT: ("NO_FAULT_ZERO_WRITE_PASS", 0, 0, False),
            LiveScenarioV21.AD_CPU_SATURATION: (
                "AD_CPU_RESOURCE_RECOVERY_PASS",
                1,
                1,
                True,
            ),
            LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE: (
                "SERVICE_AVAILABILITY_RECOVERY_PASS",
                1,
                1,
                True,
            ),
            LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE: (
                "SERVICE_AVAILABILITY_RECOVERY_PASS",
                1,
                1,
                True,
            ),
        }[self.scenario]
        observed = (
            self.terminal,
            self.fault_operation_count,
            self.forward_step_count,
            self.step_receipt_sha256 is not None
            and self.recovery_result_sha256 is not None,
        )
        if observed != expected:
            raise ValueError("live attempt closure differs from its scenario")
        if (self.scenario is LiveScenarioV21.NO_FAULT) != (
            self.run_authorization_sha256 is None
        ):
            raise ValueError("live attempt authorization differs from its scenario")
        digest = semantic_sha256(
            self.model_dump(mode="json", exclude={"closure_sha256"})
        )
        if self.closure_sha256 != digest:
            raise ValueError("live attempt closure SHA-256 mismatch")
        return self


class LiveCampaignClosureV21(DtaModelV21):
    schema_version: Literal["dta-v21.live-campaign-closure.v1"]
    terminal: Literal["DTA_V21_PR_F_LIVE_PORTFOLIO_PASS"]
    code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    protocol_sha256: Sha256V21
    live_config_sha256: Sha256V21
    planner_identity_sha256: Sha256V21
    readiness_sha256: Sha256V21
    attempts: tuple[
        LiveAttemptClosureV21,
        LiveAttemptClosureV21,
        LiveAttemptClosureV21,
        LiveAttemptClosureV21,
    ]
    unsafe_proposal_attempts: Literal[0]
    arbitrary_shell_attempts: Literal[0]
    non_owned_changes: Literal[0]
    all_baselines_restored: Literal[True]
    all_cleanup_clean: Literal[True]
    campaign_sha256: Sha256V21

    @model_validator(mode="after")
    def require_campaign(self) -> Self:
        if tuple(item.scenario for item in self.attempts) != LIVE_CAMPAIGN_ORDER_V21:
            raise ValueError("live campaign attempts differ from the exact order")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"campaign_sha256"})
        )
        if self.campaign_sha256 != expected:
            raise ValueError("live campaign closure SHA-256 mismatch")
        return self


def build_service_recovery_result_v21(
    *,
    windows: tuple[ServiceRecoveryWindowV21, ...],
    same_owned_identity: bool,
    baseline_state_digest_restored: bool,
    non_owned_changes: int,
    unsafe_proposal_attempts: int,
    arbitrary_shell_attempts: int,
) -> ServiceRecoveryResultV21:
    if len(windows) != 2:
        raise ValueError("service recovery requires exactly two windows")
    first, second = windows
    if (first.ordinal, second.ordinal) != (1, 2) or (
        first.window_ended_at != second.window_started_at
    ):
        raise ValueError("service recovery windows must be consecutive")
    if (
        first.run_id != second.run_id
        or first.attempt_id != second.attempt_id
        or first.scenario is not second.scenario
        or first.target_service != second.target_service
        or first.business_anchor_service != second.business_anchor_service
    ):
        raise ValueError("service recovery windows differ in scope")
    for window in windows:
        if (
            not window.service_running
            or not window.service_health_passed
            or not window.endpoint_reachable
            or window.business_impact_observed
        ):
            raise ValueError("service business recovery window did not pass")
    if not same_owned_identity:
        raise ValueError("service recovery changed owned identity")
    if not baseline_state_digest_restored:
        raise ValueError("service baseline state digest was not restored")
    if non_owned_changes != 0:
        raise ValueError("non-owned resources changed")
    if unsafe_proposal_attempts != 0 or arbitrary_shell_attempts != 0:
        raise ValueError("unsafe live attempts were observed")
    payload: dict[str, object] = {
        "schema_version": "dta-v21.service-recovery-result.v1",
        "terminal": "SERVICE_AVAILABILITY_RECOVERY_PASS",
        "run_id": first.run_id,
        "attempt_id": first.attempt_id,
        "scenario": first.scenario,
        "target_service": first.target_service,
        "recovery_windows_required": 2,
        "recovery_windows_passed": 2,
        "windows": windows,
        "same_owned_identity": same_owned_identity,
        "baseline_state_digest_restored": baseline_state_digest_restored,
        "non_owned_changes": non_owned_changes,
        "unsafe_proposal_attempts": unsafe_proposal_attempts,
        "arbitrary_shell_attempts": arbitrary_shell_attempts,
    }
    return ServiceRecoveryResultV21.model_validate(
        {**payload, "result_sha256": _semantic(payload)}
    )


def load_live_demo_config_v21(path: Path) -> LiveDemoConfigV21:
    if path.is_symlink() or not path.is_file():
        raise ValueError("live demo config must be a regular non-symlink file")
    return LiveDemoConfigV21.model_validate_json(path.read_text(encoding="utf-8"))


__all__ = (
    "LIVE_CAMPAIGN_ORDER_V21",
    "LiveAdBaselineWindowV21",
    "LiveCurrentStateV21",
    "LiveBaselineEvidenceV21",
    "LiveBusinessBaselineWindowV21",
    "LiveEnvironmentAdmissionV21",
    "LiveFaultImpactEvidenceV21",
    "LiveAttemptClosureV21",
    "LiveCampaignClosureV21",
    "LiveDemoConfigV21",
    "LiveReadinessV21",
    "LiveScenarioSpecV21",
    "LiveScenarioV21",
    "ServiceRecoveryResultV21",
    "ServiceRecoveryWindowV21",
    "build_service_recovery_result_v21",
    "build_service_recovery_window_v21",
    "load_live_demo_config_v21",
)
