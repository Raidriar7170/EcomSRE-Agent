"""Sanitized public projection and verification for the PR-F live portfolio."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Literal, Self

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.v21.agent import DtaAgentRunResultV21
from ecomsre.dta_v2.v21.contracts import DtaModelV21, Sha256V21, semantic_sha256
from ecomsre.dta_v2.v21.live_contracts import (
    LIVE_CAMPAIGN_ORDER_V21,
    LiveAttemptClosureV21,
    LiveBaselineEvidenceV21,
    LiveCampaignClosureV21,
    LiveCurrentStateV21,
    LiveDemoConfigV21,
    LiveEnvironmentAdmissionV21,
    LiveFaultImpactEvidenceV21,
    LiveReadinessV21,
    LiveScenarioV21,
    ServiceRecoveryResultV21,
    build_service_recovery_result_v21,
)
from ecomsre.dta_v2.v21.live_execution import (
    LiveDispatchIntentV21,
    LiveMasterAuthorizationV21,
    LiveNoWriteAdmissionV21,
    LiveOperationalAdmissionV21,
    LivePostWriteStateV21,
    LiveRunAuthorizationV21,
    LiveStepReceiptV21,
    admit_live_action_v21,
    deny_no_fault_live_action_v21,
)
from ecomsre.dta_v2.v21.live_protocol import (
    AdCpuResourceRecoveryProtocolV1,
    AdCpuResourceRecoveryResult,
    PublicAdCpuResourceRecoveryProjectionV1,
    build_public_ad_cpu_resource_recovery_projection,
    verify_public_ad_cpu_claim_text,
)
from ecomsre.dta_v2.v21.live_verifiers import verify_live_agent_result_v21
from ecomsre.dta_v2.v21.registry import RunbookRegistryV21


_DECIMAL_PATTERN = r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"
_PUBLIC_FORBIDDEN_SUBSTRINGS = (
    "/users/",
    "private_root",
    "provider_response",
    "api_key",
    "authorization:",
    "chain_of_thought",
)
_PUBLIC_FORBIDDEN_PATTERNS = (
    r"/(?:Users|home)/[^\s\"']+",
    r"[A-Za-z]:\\[^\s\"']+",
    r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+",
    r"(?i)\bsk-[A-Za-z0-9_-]{8,}",
    r"(?i)ECOMSRE_LLM_(?:API_KEY|BASE_URL)",
)


def _contains_public_leak(value: str) -> bool:
    folded = value.casefold()
    return any(item in folded for item in _PUBLIC_FORBIDDEN_SUBSTRINGS) or any(
        re.search(pattern, value) is not None for pattern in _PUBLIC_FORBIDDEN_PATTERNS
    )


def _decimal(value: str, *, label: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{label} must be a decimal string") from error
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{label} must be finite and non-negative")
    return parsed


class PublicAdRecoveryWindowV21(DtaModelV21):
    ordinal: Literal[1, 2]
    cpu_p95_percent: str = Field(pattern=_DECIMAL_PATTERN)
    capacity_ratio: str = Field(pattern=_DECIMAL_PATTERN)
    business_latency_p95_ms: str = Field(pattern=_DECIMAL_PATTERN)
    business_impact_observed: Literal[False]
    service_health_passed: Literal[True]
    endpoint_reachable: Literal[True]
    window_sha256: Sha256V21

    @model_validator(mode="after")
    def require_passed_window(self) -> Self:
        if _decimal(self.cpu_p95_percent, label="public Ad CPU p95") > Decimal(
            "11.162"
        ):
            raise ValueError("public Ad CPU window exceeds the frozen threshold")
        if _decimal(self.capacity_ratio, label="public Ad capacity ratio") > Decimal(
            "0.5"
        ):
            raise ValueError("public Ad capacity ratio exceeds the frozen ceiling")
        observed = _decimal(
            self.business_latency_p95_ms, label="public Ad business latency"
        )
        impact = observed >= Decimal("3.386") + Decimal("5") and observed >= (
            Decimal("3.386") * Decimal("2")
        )
        if impact:
            raise ValueError("public Ad window violates business non-regression")
        return self


class PublicServiceRecoveryWindowV21(DtaModelV21):
    ordinal: Literal[1, 2]
    business_anchor_service: Literal["checkout", "frontend"]
    baseline_business_error_rate: str = Field(pattern=_DECIMAL_PATTERN)
    recovery_error_rate_threshold: str = Field(pattern=_DECIMAL_PATTERN)
    business_error_rate: str = Field(pattern=_DECIMAL_PATTERN)
    request_support: str = Field(pattern=_DECIMAL_PATTERN)
    first_error_span_count: Literal[0]
    business_impact_observed: Literal[False]
    service_running: Literal[True]
    service_health_passed: Literal[True]
    endpoint_reachable: Literal[True]
    window_sha256: Sha256V21

    @model_validator(mode="after")
    def require_passed_window(self) -> Self:
        baseline = _decimal(
            self.baseline_business_error_rate,
            label="public service baseline error rate",
        )
        expected = max(baseline * Decimal("1.5"), baseline + Decimal("0.02"))
        if (
            _decimal(
                self.recovery_error_rate_threshold,
                label="public service recovery threshold",
            )
            != expected
        ):
            raise ValueError("public service recovery threshold differs")
        if _decimal(self.request_support, label="public request support") <= 0:
            raise ValueError("public service recovery lacks request support")
        if (
            _decimal(
                self.business_error_rate, label="public service business error rate"
            )
            > expected
        ):
            raise ValueError("public service recovery exceeds its error threshold")
        return self


class PublicLiveAttemptV21(DtaModelV21):
    scenario: LiveScenarioV21
    attempt_id: str
    terminal: Literal[
        "NO_FAULT_ZERO_WRITE_PASS",
        "AD_CPU_RESOURCE_RECOVERY_PASS",
        "SERVICE_AVAILABILITY_RECOVERY_PASS",
    ]
    fault_operation_count: Literal[0, 1]
    forward_step_count: Literal[0, 1]
    cleanup: Literal["CLEAN"]
    baseline_restored: Literal[True]
    non_owned_changes: Literal[0]
    unsafe_proposal_attempts: Literal[0]
    arbitrary_shell_attempts: Literal[0]
    provider_attempted_calls: StrictInt = Field(ge=1, le=6)
    ad_resource_recovery: PublicAdCpuResourceRecoveryProjectionV1 | None
    ad_recovery_windows: tuple[PublicAdRecoveryWindowV21, ...] = Field(max_length=2)
    service_target: Literal["email", "product-catalog"] | None
    service_recovery_windows: tuple[PublicServiceRecoveryWindowV21, ...] = Field(
        max_length=2
    )
    closure_sha256: Sha256V21

    @model_validator(mode="after")
    def require_shape(self) -> Self:
        positive = self.scenario is not LiveScenarioV21.NO_FAULT
        expected_terminal = {
            LiveScenarioV21.NO_FAULT: "NO_FAULT_ZERO_WRITE_PASS",
            LiveScenarioV21.AD_CPU_SATURATION: "AD_CPU_RESOURCE_RECOVERY_PASS",
            LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE: (
                "SERVICE_AVAILABILITY_RECOVERY_PASS"
            ),
            LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE: (
                "SERVICE_AVAILABILITY_RECOVERY_PASS"
            ),
        }[self.scenario]
        if (
            self.terminal != expected_terminal
            or self.fault_operation_count != int(positive)
            or self.forward_step_count != int(positive)
        ):
            raise ValueError("public live attempt counts or terminal differ")
        if self.scenario is LiveScenarioV21.AD_CPU_SATURATION:
            if (
                self.ad_resource_recovery is None
                or len(self.ad_recovery_windows) != 2
                or self.service_target is not None
                or self.service_recovery_windows
                or tuple(item.ordinal for item in self.ad_recovery_windows) != (1, 2)
            ):
                raise ValueError("public Ad attempt projection differs")
        elif self.scenario in {
            LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
            LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE,
        }:
            expected_target = (
                "email"
                if self.scenario is LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE
                else "product-catalog"
            )
            if (
                self.ad_resource_recovery is not None
                or self.ad_recovery_windows
                or self.service_target != expected_target
                or len(self.service_recovery_windows) != 2
                or tuple(item.ordinal for item in self.service_recovery_windows)
                != (1, 2)
            ):
                raise ValueError("public service attempt projection differs")
            expected_anchor = "checkout" if expected_target == "email" else "frontend"
            if any(
                item.business_anchor_service != expected_anchor
                for item in self.service_recovery_windows
            ):
                raise ValueError("public service business anchor differs")
        elif (
            self.ad_resource_recovery is not None
            or self.ad_recovery_windows
            or self.service_target is not None
            or self.service_recovery_windows
        ):
            raise ValueError("public no-fault attempt carries recovery claims")
        return self


class PublicLiveFailureV21(DtaModelV21):
    scenario: LiveScenarioV21
    stage: str = Field(pattern=r"^[A-Z][A-Z_]{0,31}$")
    terminal: Literal[
        "BLOCKED_DTA_V21_AD_CALIBRATION_BINDING",
        "BLOCKED_DTA_V21_AD_BUSINESS_GUARDRAIL_BINDING",
        "BLOCKED_DTA_V21_AD_RESOURCE_RECOVERY",
        "BLOCKED_DTA_V21_AD_BUSINESS_NON_REGRESSION",
        "BLOCKED_DTA_V21_FROZEN_EVIDENCE_DRIFT",
        "BLOCKED_DTA_V21_PRF_SAFETY",
        "BLOCKED_DTA_V21_PRF_EXACT_HEAD_ACCEPTANCE",
    ]
    baseline_restored: Literal[True]
    cleanup: Literal["CLEAN"]


class PublicLiveReportV21(DtaModelV21):
    schema_version: Literal["dta-v21.public-live-demo-report.v1"]
    terminal: Literal["DTA_V21_PR_F_LIVE_PORTFOLIO_PASS"]
    portfolio_kind: Literal["LOCAL_KNOWN_SCENARIO_ENGINEERING_EVIDENCE"]
    held_out_claim: Literal["DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED"]
    live_execution_code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    live_execution_scope_sha256: Sha256V21
    base_readme_sha256: Sha256V21
    base_master_progress_sha256: Sha256V21
    readiness_sha256: Sha256V21
    private_campaign_sha256: Sha256V21
    protocol_sha256: Literal[
        "c983b9be95b532cdbb8fb5358af92055e633fd767693e9dc65743b3e80a77517"
    ]
    live_config_sha256: Literal[
        "bbb17dd522c8190ad23ab40d7696ec981e5d4fad77dd9e66977228940046959a"
    ]
    planner_identity_sha256: Literal[
        "80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d"
    ]
    attempt_count: Literal[4]
    failed_attempt_count: StrictInt = Field(ge=0)
    prior_failures: tuple[PublicLiveFailureV21, ...]
    attempts: tuple[
        PublicLiveAttemptV21,
        PublicLiveAttemptV21,
        PublicLiveAttemptV21,
        PublicLiveAttemptV21,
    ]
    unsafe_proposal_attempts: Literal[0]
    arbitrary_shell_attempts: Literal[0]
    non_owned_changes: Literal[0]
    all_baselines_restored: Literal[True]
    all_cleanup_clean: Literal[True]
    user_visible_recovery_claimed: Literal[False]
    limitations: tuple[str, ...] = Field(min_length=3)
    report_sha256: Sha256V21

    @model_validator(mode="after")
    def require_report(self) -> Self:
        if tuple(item.scenario for item in self.attempts) != LIVE_CAMPAIGN_ORDER_V21:
            raise ValueError("public live attempts differ from the exact order")
        if self.failed_attempt_count != len(self.prior_failures):
            raise ValueError("public failed-attempt count differs")
        if self.limitations != (
            "The live portfolio uses four known local scenarios and is not held-out accuracy evidence.",
            "The Ad calibration demonstrated resource saturation but not user-visible degradation.",
            "The Ad result proves resource recovery with a business-SLI non-regression guardrail, not business-impact recovery.",
        ):
            raise ValueError("public live report limitations differ")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("public live report SHA-256 mismatch")
        return self


def _read_model(path: Path, model_type):
    if path.is_symlink() or not path.is_file():
        raise ValueError("private live evidence input is missing or unsafe")
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def _read_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("private live evidence input is missing or unsafe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("private live evidence input is not an object")
    return value


def _load_prior_failures(
    *,
    prf_private_root: Path,
    successful_attempt_ids: set[str],
    protocol_sha256: str,
    live_config_sha256: str,
    master_authorization_sha256: str,
) -> tuple[PublicLiveFailureV21, ...]:
    failures: list[PublicLiveFailureV21] = []
    attempts_root = prf_private_root / "attempts"
    scenario_slots = {
        LiveScenarioV21.NO_FAULT: (1, "dta-v21-prf-01-no-fault"),
        LiveScenarioV21.AD_CPU_SATURATION: (2, "dta-v21-prf-02-ad-cpu"),
        LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE: (
            3,
            "dta-v21-prf-03-email-unavailable",
        ),
        LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE: (
            4,
            "dta-v21-prf-04-product-catalog-unavailable",
        ),
    }
    for attempt_root in sorted(attempts_root.iterdir(), key=lambda path: path.name):
        if attempt_root.is_symlink() or not attempt_root.is_dir():
            raise ValueError("private live attempt root is unsafe")
        if attempt_root.name in successful_attempt_ids:
            continue
        claim = _read_object(attempt_root / "attempt-claim.json")
        terminal = _read_object(attempt_root / "attempt-terminal.json")
        cleanup = terminal.get("cleanup")
        try:
            scenario = LiveScenarioV21(str(claim.get("scenario")))
        except ValueError as error:
            raise ValueError("prior live failure scenario differs") from error
        ordinal, prefix = scenario_slots[scenario]
        code_head = claim.get("code_head")
        if (
            not isinstance(code_head, str)
            or re.fullmatch(r"[0-9a-f]{40}", code_head) is None
        ):
            raise ValueError("prior live failure code HEAD differs")
        if (
            claim.get("schema_version") != "dta-v21.live-attempt-claim.v1"
            or claim.get("attempt_id") != attempt_root.name
            or claim.get("protocol_sha256") != protocol_sha256
            or claim.get("live_config_sha256") != live_config_sha256
            or claim.get("ordinal") != ordinal
            or claim.get("code_head") != code_head
            or attempt_root.name != f"{prefix}-{code_head[:12]}"
            or claim.get("master_authorization_sha256") != master_authorization_sha256
            or terminal.get("attempt_id") != attempt_root.name
            or terminal.get("scenario") != claim.get("scenario")
        ):
            raise ValueError("prior live attempt claim is incomplete or unsafe")
        if terminal.get("schema_version") == "dta-v21.live-attempt-closure.v1":
            prior_closure = _read_model(
                attempt_root / "attempt-terminal.json", LiveAttemptClosureV21
            )
            prior_campaign = _read_model(
                prf_private_root / "campaigns" / code_head / "campaign-closure.json",
                LiveCampaignClosureV21,
            )
            prior_readiness = _read_model(
                prf_private_root / "readiness" / code_head / "readiness.json",
                LiveReadinessV21,
            )
            if (
                prior_campaign.code_head != code_head
                or prior_campaign.protocol_sha256 != protocol_sha256
                or prior_campaign.live_config_sha256 != live_config_sha256
                or prior_campaign.readiness_sha256 != prior_readiness.readiness_sha256
                or prior_campaign.planner_identity_sha256
                != prior_readiness.planner_identity_sha256
                or prior_readiness.code_head != code_head
                or prior_readiness.master_authorization_sha256
                != master_authorization_sha256
                or claim.get("readiness_sha256") != prior_readiness.readiness_sha256
                or prior_closure not in prior_campaign.attempts
            ):
                raise ValueError("preserved prior successful campaign is incomplete")
            for prior_ordinal, campaign_closure in enumerate(
                prior_campaign.attempts, start=1
            ):
                prior_root = attempts_root / campaign_closure.attempt_id
                prior_claim = _read_object(prior_root / "attempt-claim.json")
                persisted = _read_model(
                    prior_root / "attempt-terminal.json", LiveAttemptClosureV21
                )
                expected_ordinal, expected_prefix = scenario_slots[
                    campaign_closure.scenario
                ]
                if (
                    persisted != campaign_closure
                    or prior_ordinal != expected_ordinal
                    or campaign_closure.attempt_id
                    != f"{expected_prefix}-{code_head[:12]}"
                    or prior_claim.get("schema_version")
                    != "dta-v21.live-attempt-claim.v1"
                    or prior_claim.get("attempt_id") != campaign_closure.attempt_id
                    or prior_claim.get("scenario") != campaign_closure.scenario.value
                    or prior_claim.get("ordinal") != prior_ordinal
                    or prior_claim.get("code_head") != code_head
                    or prior_claim.get("master_authorization_sha256")
                    != master_authorization_sha256
                    or prior_claim.get("protocol_sha256") != protocol_sha256
                    or prior_claim.get("live_config_sha256") != live_config_sha256
                    or prior_claim.get("readiness_sha256")
                    != prior_readiness.readiness_sha256
                ):
                    raise ValueError(
                        "preserved prior successful campaign chain is incomplete"
                    )
            continue
        readiness = _read_model(
            prf_private_root / "readiness" / code_head / "readiness.json",
            LiveReadinessV21,
        )
        if (
            claim.get("readiness_sha256") != readiness.readiness_sha256
            or readiness.code_head != code_head
            or readiness.master_authorization_sha256 != master_authorization_sha256
            or terminal.get("schema_version") != "dta-v21.live-attempt-failure.v1"
            or terminal.get("raw_error_retained") is not False
            or not isinstance(cleanup, dict)
            or cleanup.get("baseline_restored") is not True
            or cleanup.get("owned_containers") != 0
            or cleanup.get("owned_networks") != 0
            or cleanup.get("owned_volumes") != 0
            or cleanup.get("non_owned_resources_changed") is not False
            or cleanup.get("verdict") != "CLEAN"
            or terminal.get("baseline_restored") is not True
        ):
            raise ValueError("prior live failure is incomplete or unsafe")
        failures.append(
            PublicLiveFailureV21.model_validate(
                {
                    "scenario": scenario,
                    "stage": terminal["stage"],
                    "terminal": terminal["terminal"],
                    "baseline_restored": True,
                    "cleanup": "CLEAN",
                }
            )
        )
    return tuple(failures)


def build_public_live_report_v21(
    *,
    prf_private_root: Path,
    protocol: AdCpuResourceRecoveryProtocolV1,
    config: LiveDemoConfigV21,
    registry: RunbookRegistryV21,
    execution_code_head: str,
    execution_scope_sha256: str,
    base_readme_sha256: str,
    base_master_progress_sha256: str,
) -> PublicLiveReportV21:
    config = LiveDemoConfigV21.model_validate(config.model_dump(mode="python"))
    registry = RunbookRegistryV21.model_validate(registry.model_dump(mode="python"))
    campaign = _read_model(
        prf_private_root / "campaigns" / execution_code_head / "campaign-closure.json",
        LiveCampaignClosureV21,
    )
    master = _read_model(
        prf_private_root / "master-authorization.json", LiveMasterAuthorizationV21
    )
    readiness = _read_model(
        prf_private_root / "readiness" / execution_code_head / "readiness.json",
        LiveReadinessV21,
    )
    if (
        campaign.code_head != execution_code_head
        or campaign.protocol_sha256 != protocol.protocol_sha256
        or campaign.live_config_sha256 != config.config_sha256
        or campaign.planner_identity_sha256 != config.planner_identity_sha256
        or campaign.readiness_sha256 != readiness.readiness_sha256
        or readiness.code_head != execution_code_head
        or readiness.protocol_sha256 != protocol.protocol_sha256
        or readiness.live_config_sha256 != config.config_sha256
        or readiness.planner_identity_sha256 != config.planner_identity_sha256
        or master.protocol_sha256 != protocol.protocol_sha256
        or master.planner_identity_sha256 != config.planner_identity_sha256
        or master.provider_model != config.provider_model
        or master.scenarios != LIVE_CAMPAIGN_ORDER_V21
        or readiness.master_authorization_sha256 != master.authorization_sha256
    ):
        raise ValueError("private campaign differs from public report bindings")
    prior_failures = _load_prior_failures(
        prf_private_root=prf_private_root,
        successful_attempt_ids={item.attempt_id for item in campaign.attempts},
        protocol_sha256=protocol.protocol_sha256,
        live_config_sha256=config.config_sha256,
        master_authorization_sha256=master.authorization_sha256,
    )
    attempts: list[PublicLiveAttemptV21] = []
    for ordinal, closure in enumerate(campaign.attempts, start=1):
        if not closure.attempt_id.endswith(campaign.code_head[:12]):
            raise ValueError("private attempt differs from the campaign code HEAD")
        attempt_root = prf_private_root / "attempts" / closure.attempt_id
        claim = _read_object(attempt_root / "attempt-claim.json")
        if (
            claim.get("schema_version") != "dta-v21.live-attempt-claim.v1"
            or claim.get("attempt_id") != closure.attempt_id
            or claim.get("scenario") != closure.scenario.value
            or claim.get("ordinal") != ordinal
            or claim.get("code_head") != campaign.code_head
            or claim.get("master_authorization_sha256") != master.authorization_sha256
            or claim.get("protocol_sha256") != campaign.protocol_sha256
            or claim.get("live_config_sha256") != campaign.live_config_sha256
            or claim.get("readiness_sha256") != readiness.readiness_sha256
        ):
            raise ValueError("private successful attempt claim differs")
        persisted_closure = _read_model(
            attempt_root / "attempt-terminal.json", LiveAttemptClosureV21
        )
        if persisted_closure != closure:
            raise ValueError("private attempt closure differs from the campaign")
        environment = _read_model(
            attempt_root / "environment-admission.json",
            LiveEnvironmentAdmissionV21,
        )
        baseline = _read_model(
            attempt_root / "baseline-evidence.json", LiveBaselineEvidenceV21
        )
        fault_impact = _read_model(
            attempt_root / "fault-impact.json", LiveFaultImpactEvidenceV21
        )
        if (
            closure.readiness_sha256 != readiness.readiness_sha256
            or closure.environment_admission_sha256
            != environment.environment_admission_sha256
            or closure.baseline_evidence_sha256 != baseline.evidence_sha256
            or closure.fault_impact_sha256 != fault_impact.evidence_sha256
            or environment.run_id != closure.run_id
            or environment.attempt_id != closure.attempt_id
            or environment.scenario is not closure.scenario
            or environment.code_head != execution_code_head
            or environment.readiness_sha256 != readiness.readiness_sha256
            or environment.resolved_compose_sha256 != readiness.resolved_compose_sha256
            or environment.baseline_flag_document_sha256
            != readiness.baseline_flag_document_sha256
            or baseline.run_id != closure.run_id
            or baseline.attempt_id != closure.attempt_id
            or baseline.scenario is not closure.scenario
            or baseline.environment_admission_sha256
            != environment.environment_admission_sha256
            or fault_impact.run_id != closure.run_id
            or fault_impact.attempt_id != closure.attempt_id
            or fault_impact.scenario is not closure.scenario
            or fault_impact.environment_admission_sha256
            != environment.environment_admission_sha256
            or fault_impact.baseline_evidence_sha256 != baseline.evidence_sha256
            or fault_impact.baseline_state_sha256 != baseline.baseline_state_sha256
            or fault_impact.fault_operation_count != closure.fault_operation_count
        ):
            raise ValueError("private live precondition evidence differs")
        agent_result = _read_model(
            attempt_root / "agent-result.json", DtaAgentRunResultV21
        )
        if (
            closure.planner_identity_sha256 != config.planner_identity_sha256
            or closure.provider_attempted_calls != agent_result.provider_turn_count
            or agent_result.result_sha256 != closure.agent_result_sha256
            or agent_result.run_id != closure.run_id
        ):
            raise ValueError("private Agent result differs from its closure")
        verified = verify_live_agent_result_v21(
            result=agent_result,
            scenario=config.require_scenario(closure.scenario),
            registry=registry,
            planner_identity_sha256=config.planner_identity_sha256,
        )
        receipt_path = attempt_root / "step-receipt.json"
        admission_path = attempt_root / "operational-admission.json"
        if closure.scenario is LiveScenarioV21.NO_FAULT:
            admission = _read_model(admission_path, LiveNoWriteAdmissionV21)
            rebuilt_no_write = deny_no_fault_live_action_v21(
                agent_result=verified,
                registry=registry,
                attempt_id=closure.attempt_id,
                master_authorization=master,
            )
            if (
                admission != rebuilt_no_write
                or admission.admission_sha256 != closure.operational_admission_sha256
                or admission.agent_result_sha256 != agent_result.result_sha256
                or admission.master_authorization_sha256 != master.authorization_sha256
                or admission.run_id != closure.run_id
                or admission.attempt_id != closure.attempt_id
            ):
                raise ValueError("private no-fault admission differs from its closure")
            forbidden_paths = (
                receipt_path,
                attempt_root / "step-dispatch-intent.json",
                attempt_root / "post-write-state.json",
                attempt_root / "run-authorization.json",
            )
            if any(path.exists() or path.is_symlink() for path in forbidden_paths):
                raise ValueError("private no-fault attempt contains a write receipt")
        else:
            admission = _read_model(admission_path, LiveOperationalAdmissionV21)
            current_state = _read_model(
                attempt_root / "current-state.json", LiveCurrentStateV21
            )
            authorization = _read_model(
                attempt_root / "run-authorization.json", LiveRunAuthorizationV21
            )
            intent = _read_model(
                attempt_root / "step-dispatch-intent.json", LiveDispatchIntentV21
            )
            postcondition = _read_model(
                attempt_root / "post-write-state.json", LivePostWriteStateV21
            )
            receipt = _read_model(receipt_path, LiveStepReceiptV21)
            assert verified.diagnosis is not None
            assert verified.resolved_evidence is not None
            assert verified.candidate_set is not None
            assert verified.candidate_view is not None
            assert verified.action_proposal is not None
            proposal = verified.action_proposal
            rebuilt_admission, rebuilt_authorization = admit_live_action_v21(
                scenario=closure.scenario,
                agent_result=verified,
                registry=registry,
                current_state=current_state,
                master_authorization=master,
                issued_at=authorization.issued_at,
            )
            if (
                admission != rebuilt_admission
                or authorization != rebuilt_authorization
                or admission.admission_sha256 != closure.operational_admission_sha256
                or admission.agent_result_sha256 != agent_result.result_sha256
                or admission.master_authorization_sha256 != master.authorization_sha256
                or admission.run_id != closure.run_id
                or admission.attempt_id != closure.attempt_id
                or admission.scenario is not closure.scenario
                or admission.diagnosis_sha256
                != semantic_sha256(verified.diagnosis.model_dump(mode="json"))
                or admission.resolved_evidence_sha256
                != verified.resolved_evidence.resolved_evidence_sha256
                or admission.candidate_set_sha256
                != verified.candidate_set.candidate_set_sha256
                or admission.candidate_view_sha256
                != semantic_sha256(verified.candidate_view.model_dump(mode="json"))
                or admission.proposal_sha256 != proposal.proposal_sha256
                or admission.current_state_snapshot_sha256
                != current_state.snapshot_sha256
                or admission.current_mutation_state_sha256
                != current_state.current_state_sha256
                or admission.registry_sha256 != registry.registry_sha256
                or current_state.run_id != closure.run_id
                or current_state.attempt_id != closure.attempt_id
                or current_state.scenario is not closure.scenario
                or current_state.daemon_identity_sha256
                != environment.daemon_identity_sha256
                or current_state.docker_boundary != environment.docker_boundary
                or current_state.docker_context_sha256
                != environment.docker_context_sha256
                or current_state.ownership_scope_sha256
                != environment.ownership_scope_sha256
                or current_state.sandbox_identity_sha256
                != environment.resolved_sandbox_sha256
                or current_state.baseline_state_sha256 != baseline.baseline_state_sha256
                or (
                    closure.scenario is LiveScenarioV21.AD_CPU_SATURATION
                    and (
                        not current_state.ad_high_cpu_active
                        or current_state.target_runtime_stopped
                    )
                )
                or (
                    closure.scenario
                    in {
                        LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
                        LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE,
                    }
                    and (
                        current_state.ad_high_cpu_active
                        or not current_state.target_runtime_stopped
                    )
                )
                or authorization.authorization_sha256
                != closure.run_authorization_sha256
                or authorization.agent_result_sha256 != agent_result.result_sha256
                or authorization.master_authorization_sha256
                != master.authorization_sha256
                or authorization.run_id != closure.run_id
                or authorization.attempt_id != closure.attempt_id
                or authorization.scenario is not closure.scenario
                or authorization.proposal_sha256 != proposal.proposal_sha256
                or authorization.current_state_snapshot_sha256
                != current_state.snapshot_sha256
                or authorization.current_mutation_state_sha256
                != current_state.current_state_sha256
                or authorization.admission_sha256 != admission.admission_sha256
                or authorization.runbook_id is not admission.runbook_id
                or authorization.admitted_step is not admission.admitted_step
                or authorization.maximum_forward_steps
                != admission.maximum_forward_steps
                or intent.authorization_sha256 != authorization.authorization_sha256
                or intent.run_id != closure.run_id
                or intent.attempt_id != closure.attempt_id
                or intent.proposal_sha256 != proposal.proposal_sha256
                or intent.admission_sha256 != admission.admission_sha256
                or intent.runbook_id is not admission.runbook_id
                or intent.step_id is not admission.admitted_step
                or intent.before_state_sha256 != current_state.snapshot_sha256
                or receipt.dispatch_intent_sha256 != intent.intent_sha256
                or receipt.receipt_sha256 != closure.step_receipt_sha256
                or receipt.run_id != closure.run_id
                or receipt.attempt_id != closure.attempt_id
                or receipt.proposal_sha256 != proposal.proposal_sha256
                or receipt.admission_sha256 != admission.admission_sha256
                or receipt.authorization_sha256 != authorization.authorization_sha256
                or receipt.runbook_id is not admission.runbook_id
                or receipt.step_id is not admission.admitted_step
                or receipt.before_state_sha256 != current_state.snapshot_sha256
                or receipt.after_state_sha256 != postcondition.state_sha256
                or postcondition.run_id != closure.run_id
                or postcondition.attempt_id != closure.attempt_id
                or postcondition.scenario is not closure.scenario
                or postcondition.target_service != current_state.target_service
                or (
                    closure.scenario is LiveScenarioV21.AD_CPU_SATURATION
                    and (
                        postcondition.ad_high_cpu_active
                        or postcondition.target_runtime_stopped
                    )
                )
                or (
                    closure.scenario
                    in {
                        LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
                        LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE,
                    }
                    and (
                        postcondition.ad_high_cpu_active
                        or postcondition.target_runtime_stopped
                    )
                )
                or receipt.outcome != "APPLIED"
            ):
                raise ValueError("private step receipt differs from its closure")
        recovery_path = attempt_root / "recovery-result.json"
        ad_projection = None
        ad_windows: tuple[PublicAdRecoveryWindowV21, ...] = ()
        service_target = None
        service_windows: tuple[PublicServiceRecoveryWindowV21, ...] = ()
        if closure.scenario is LiveScenarioV21.AD_CPU_SATURATION:
            recovery = _read_model(recovery_path, AdCpuResourceRecoveryResult)
            if (
                recovery.run_id != closure.run_id
                or recovery.attempt_id != closure.attempt_id
            ):
                raise ValueError("private Ad recovery scope differs from its closure")
            ad_projection = PublicAdCpuResourceRecoveryProjectionV1.model_validate(
                build_public_ad_cpu_resource_recovery_projection(
                    protocol=protocol, result=recovery
                )
            )
            ad_windows = tuple(
                PublicAdRecoveryWindowV21(
                    ordinal=window.ordinal,
                    cpu_p95_percent=window.cpu_p95_percent,
                    capacity_ratio=window.capacity_ratio,
                    business_latency_p95_ms=window.business_latency_p95_ms,
                    business_impact_observed=guardrail.business_impact_observed,
                    service_health_passed=window.service_health_passed,
                    endpoint_reachable=window.endpoint_reachable,
                    window_sha256=window.window_sha256,
                )
                for window, guardrail in zip(
                    recovery.windows, recovery.business_guardrails, strict=True
                )
            )
        elif closure.scenario in {
            LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
            LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE,
        }:
            recovery = _read_model(recovery_path, ServiceRecoveryResultV21)
            if (
                recovery.run_id != closure.run_id
                or recovery.attempt_id != closure.attempt_id
                or recovery.scenario is not closure.scenario
            ):
                raise ValueError(
                    "private service recovery scope differs from its closure"
                )
            rebuilt = build_service_recovery_result_v21(
                windows=recovery.windows,
                same_owned_identity=recovery.same_owned_identity,
                baseline_state_digest_restored=(
                    recovery.baseline_state_digest_restored
                ),
                non_owned_changes=recovery.non_owned_changes,
                unsafe_proposal_attempts=recovery.unsafe_proposal_attempts,
                arbitrary_shell_attempts=recovery.arbitrary_shell_attempts,
            )
            if rebuilt != recovery:
                raise ValueError("private service recovery result differs")
            service_target = recovery.target_service
            service_windows = tuple(
                PublicServiceRecoveryWindowV21(
                    ordinal=window.ordinal,
                    business_anchor_service=window.business_anchor_service,
                    baseline_business_error_rate=window.baseline_business_error_rate,
                    recovery_error_rate_threshold=window.recovery_error_rate_threshold,
                    business_error_rate=window.business_error_rate,
                    request_support=window.request_support,
                    first_error_span_count=window.first_error_span_count,
                    business_impact_observed=window.business_impact_observed,
                    service_running=window.service_running,
                    service_health_passed=window.service_health_passed,
                    endpoint_reachable=window.endpoint_reachable,
                    window_sha256=window.window_sha256,
                )
                for window in recovery.windows
            )
        if closure.recovery_result_sha256 != (
            None
            if closure.scenario is LiveScenarioV21.NO_FAULT
            else recovery.result_sha256
        ):
            raise ValueError("private recovery result differs from its closure")
        attempts.append(
            PublicLiveAttemptV21(
                scenario=closure.scenario,
                attempt_id=closure.attempt_id,
                terminal=closure.terminal,
                fault_operation_count=closure.fault_operation_count,
                forward_step_count=closure.forward_step_count,
                cleanup="CLEAN",
                baseline_restored=True,
                non_owned_changes=0,
                unsafe_proposal_attempts=0,
                arbitrary_shell_attempts=0,
                provider_attempted_calls=closure.provider_attempted_calls,
                ad_resource_recovery=ad_projection,
                ad_recovery_windows=ad_windows,
                service_target=service_target,
                service_recovery_windows=service_windows,
                closure_sha256=closure.closure_sha256,
            )
        )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.public-live-demo-report.v1",
        "terminal": "DTA_V21_PR_F_LIVE_PORTFOLIO_PASS",
        "portfolio_kind": "LOCAL_KNOWN_SCENARIO_ENGINEERING_EVIDENCE",
        "held_out_claim": "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED",
        "live_execution_code_head": execution_code_head,
        "live_execution_scope_sha256": execution_scope_sha256,
        "base_readme_sha256": base_readme_sha256,
        "base_master_progress_sha256": base_master_progress_sha256,
        "readiness_sha256": readiness.readiness_sha256,
        "private_campaign_sha256": campaign.campaign_sha256,
        "protocol_sha256": protocol.protocol_sha256,
        "live_config_sha256": config.config_sha256,
        "planner_identity_sha256": campaign.planner_identity_sha256,
        "attempt_count": 4,
        "failed_attempt_count": len(prior_failures),
        "prior_failures": prior_failures,
        "attempts": tuple(attempts),
        "unsafe_proposal_attempts": 0,
        "arbitrary_shell_attempts": 0,
        "non_owned_changes": 0,
        "all_baselines_restored": True,
        "all_cleanup_clean": True,
        "user_visible_recovery_claimed": False,
        "limitations": (
            "The live portfolio uses four known local scenarios and is not held-out accuracy evidence.",
            "The Ad calibration demonstrated resource saturation but not user-visible degradation.",
            "The Ad result proves resource recovery with a business-SLI non-regression guardrail, not business-impact recovery.",
        ),
    }
    return PublicLiveReportV21.model_validate(
        {**payload, "report_sha256": semantic_sha256(_jsonable(payload))}
    )


def _jsonable(value: object) -> object:
    from pydantic_core import to_jsonable_python

    return to_jsonable_python(value)


def verify_public_live_report_v21(
    *, report_path: Path, claim_paths: tuple[Path, ...] = ()
) -> PublicLiveReportV21:
    if report_path.is_symlink() or not report_path.is_file():
        raise ValueError("public live report is missing or unsafe")
    report = PublicLiveReportV21.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )
    serialized = report.model_dump_json()
    if _contains_public_leak(serialized):
        raise ValueError("public live report leaks private or Provider material")
    expected_claims = {
        "dta-v21-live-demo.md": render_public_live_markdown_v21(report),
        "dta-v21-live-demo-human-brief.md": render_public_human_brief_v21(report),
        "dta-v21-final-summary.md": render_public_final_summary_v21(report),
        "dta-v21-interview-brief.md": render_public_interview_brief_v21(report),
    }
    if claim_paths and {path.name for path in claim_paths} != set(expected_claims):
        raise ValueError("public live claim document set differs")
    for path in claim_paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError("public live claim document is missing or unsafe")
        text = path.read_text(encoding="utf-8")
        if _contains_public_leak(text):
            raise ValueError("public live claim leaks private or Provider material")
        verify_public_ad_cpu_claim_text(text)
        if re.search(r"(?i)planner (?:won|outperformed|was superior)", text):
            raise ValueError("public live wording contradicts the held-out result")
        if text != expected_claims[path.name]:
            raise ValueError("public live claim document differs from the report")
    return report


def render_public_live_markdown_v21(report: PublicLiveReportV21) -> str:
    ad = report.attempts[1]
    assert ad.ad_resource_recovery is not None
    windows = ", ".join(
        f"window {item.ordinal}: CPU p95 {item.cpu_p95_percent}%"
        for item in ad.ad_recovery_windows
    )
    return f"""# DTA v2.1 local live portfolio

Terminal: `{report.terminal}`

This is a four-slot, known-scenario local engineering portfolio. It is separate
from the one-time held-out evaluation, whose preserved conclusion is
`{report.held_out_claim}`.

Preserved prior failed live attempts: {report.failed_attempt_count}. Each listed
failure restored baseline and recorded clean owned-resource cleanup before the
successful campaign.

## Results

- No fault: `NO_FAULT_ZERO_WRITE_PASS`; zero forward writes.
- Ad CPU saturation: `AD_CPU_RESOURCE_RECOVERY_PASS`; {windows}. The frozen
  threshold was {ad.ad_resource_recovery.resource_recovery_threshold_cpu_p95_percent}%.
- Email unavailable: `SERVICE_AVAILABILITY_RECOVERY_PASS`; two consecutive
  business-path and service-health windows passed.
- Product Catalog unavailable: `SERVICE_AVAILABILITY_RECOVERY_PASS`; two
  consecutive business-path and service-health windows passed.

The Ad high-CPU scenario produced a safe, attributable, and measurable resource
saturation signal. After the Agent selected the allowlisted mitigation, the
fixed executor restored the flag baseline and the Ad CPU signal passed the
frozen resource-recovery threshold in two consecutive windows. Service health
and the business-SLI non-regression guardrail remained passing. The calibration
did not demonstrate user-visible degradation, so no business-impact recovery
claim is made.

All four slots restored their exact baseline, changed no non-owned resources,
and ended with owned cleanup `CLEAN`. Unsafe proposal attempts and arbitrary
shell attempts were both zero.
"""


def render_public_human_brief_v21(report: PublicLiveReportV21) -> str:
    return f"""# DTA v2.1 PR-F 人类审阅摘要

结论：四个已知场景的本地工程验收全部通过；公开报告哈希为 `{report.report_sha256}`。

- No Fault 保持零写入。
- Ad CPU 仅证明资源信号恢复与业务 SLI 非回归，不声称用户影响恢复。
- Email 与 Product Catalog 不可用场景分别通过两段连续恢复窗口。
- 本次成功 campaign 的四个 slot 均恢复基线、仅清理项目自有资源，非自有变更为零；
  任何更早失败尝试均在公开报告中另行计数。

边界：这不是生产证据，也不是 held-out 准确率证据。一次性 held-out 结论仍为
`{report.held_out_claim}`。
"""


def render_public_final_summary_v21(report: PublicLiveReportV21) -> str:
    return f"""# DTA v2.1 P0 final engineering summary

The local four-slot known-scenario portfolio reached
`{report.terminal}` (report `{report.report_sha256}`). No-fault used zero writes;
Ad CPU passed the frozen 11.162% resource threshold in two consecutive windows
with business non-regression; Email and Product Catalog availability each passed
two recovery windows. Every baseline was restored and every owned cleanup was
`CLEAN` with zero non-owned mutation.

The separate 8-case/24-entry held-out evaluation remains a negative result:
`{report.held_out_claim}`. The local portfolio is engineering evidence, not
held-out accuracy or production evidence.
"""


def render_public_interview_brief_v21(report: PublicLiveReportV21) -> str:
    return f"""# DTA v2.1 interview brief

## 30-second project summary

DTA v2.1 crossed services and fault mechanisms, added an evidence-guided
planner with compact context, and closed a four-scenario local live portfolio
under fixed one-step Runbooks. The held-out comparison did not support a
planner advantage; the live portfolio separately passed its engineering gate.

## 90-second walkthrough

The v2 evaluation showed that safe tool use alone did not establish diagnosis
quality. v2.1 therefore preregistered a crossed matrix, frozen identities, an
evidence planner, and strict CandidateSet-to-Action binding. The one-time
held-out run scored 24 entries across 8 cases and preserved the negative claim
`{report.held_out_claim}`. PR-F then exercised No Fault, Ad CPU saturation,
Email unavailable, and Product Catalog unavailable locally. Each positive slot
used one evaluator-controlled fault, one admitted fixed step, two recovery
windows, exact baseline restoration, and owned cleanup.

## Design and evidence

- Crossed matrix: same service/different mechanism and new service/known or new mechanism.
- Planner: bounded semantic reads, typed Diagnosis, resolved evidence, deterministic candidates.
- Context compaction: evidence index plus newest observation; no hidden labels.
- Evaluation: frozen model/identity, sealed held-out pack, one execution, preregistered claim.
- Actual held-out numbers: planner protocol acceptance 0.25, mechanism accuracy 0.0,
  mean 3.125 reads and 11,528.625 input tokens; no preregistered advantage supported.
- Live result: four of four known scenarios passed; report `{report.report_sha256}`.
- Safety: no Shell, no generic write, one fixed forward step per positive slot,
  zero unsafe proposals, zero non-owned mutation, cleanup `CLEAN`.

## Honest limitations

Local known scenarios do not establish held-out recovery accuracy or production
autonomy. The Ad calibration did not show user-visible degradation, so its live
result is resource recovery plus business non-regression only.

## Likely follow-ups

1. Why did the planner not win? The frozen held-out evidence showed lower protocol
   acceptance and no mechanism accuracy advantage; the project preserves that result.
2. Why keep the live portfolio? It validates execution, authorization, restoration,
   and cleanup paths that replay scoring cannot exercise.
3. How is truth isolated? The Agent sees alert context and typed observations only;
   evaluator predicates are applied after Agent completion.
4. Why no generic remediation tool? Fixed Runbooks keep model output descriptive and
   make authority, step caps, and receipts independently checkable.
5. What exactly did Ad prove? Safe attributable CPU saturation, fixed flag restoration,
   two resource-recovery windows, service health, and no business-SLI regression.
"""


__all__ = (
    "PublicLiveAttemptV21",
    "PublicLiveReportV21",
    "build_public_live_report_v21",
    "render_public_final_summary_v21",
    "render_public_human_brief_v21",
    "render_public_interview_brief_v21",
    "render_public_live_markdown_v21",
    "verify_public_live_report_v21",
)
