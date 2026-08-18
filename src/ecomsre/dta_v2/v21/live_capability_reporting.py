"""Truthful public v3 projection for the PR-F capability closeout."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_core import to_jsonable_python
from typing_extensions import Self

from ecomsre.dta_v2.v21.agent import DtaAgentRunResultV21
from ecomsre.dta_v2.v21.contracts import DtaModelV21, Sha256V21, semantic_sha256
from ecomsre.dta_v2.v21.live_capability_closeout import (
    CAPABILITY_MISS_ATTEMPT_ID_V1,
    ORIGINAL_BLOCKED_ATTEMPT_ID_V1,
    POSITIVE_CONTINUATION_ORDER_V1,
    LivePositiveContinuationClosureV1,
    NoFaultCapabilityMissV1,
    verify_no_fault_capability_miss_eligibility_v1,
    verify_positive_continuation_admission_v1,
    verify_positive_continuation_consumption_v1,
)
from ecomsre.dta_v2.v21.live_contracts import (
    LiveAttemptClosureV21,
    LiveBaselineEvidenceV21,
    LiveEnvironmentAdmissionV2,
    LiveFaultImpactEvidenceV21,
    LiveScenarioV21,
    ServiceRecoveryResultV21,
    load_live_demo_config_v21,
)
from ecomsre.dta_v2.v21.live_execution import (
    LiveDispatchIntentV21,
    LiveOperationalAdmissionV21,
    LivePostWriteStateV21,
    LiveRunAuthorizationV21,
    LiveStepReceiptV21,
)
from ecomsre.dta_v2.v21.live_protocol import (
    AdCpuResourceRecoveryResult,
    load_ad_cpu_resource_recovery_protocol_v1,
)
from ecomsre.dta_v2.v21.live_reconciliation import (
    verify_post_terminal_reconciliation_v1,
)
from ecomsre.dta_v2.v21.live_verifiers import verify_live_agent_result_v21
from ecomsre.dta_v2.v21.registry import load_default_runbook_registry


FINAL_CLOSEOUT_TERMINAL_V3 = (
    "DTA_V21_P0_ENGINEERING_CLOSEOUT_WITH_NO_FAULT_DIAGNOSIS_MISS"
)
POSITIVE_PORTFOLIO_TERMINAL_V3 = (
    "DTA_V21_PR_F_POSITIVE_PORTFOLIO_PASS_WITH_NO_FAULT_DIAGNOSIS_MISS"
)


class PublicHistoricalReadyBlockerV3(DtaModelV21):
    kind: Literal["RECONCILED_PRE_BASELINE_BLOCKED_ATTEMPT"]
    stage: Literal["READY"]
    terminal: Literal["BLOCKED_DTA_V21_PRF_SAFETY"]
    historical_baseline_restored: Literal[False]
    historical_cleanup_verdict: Literal["BLOCKED"]
    remaining_owned_resources: Literal[0]
    non_owned_change: Literal[False]
    reconciliation_valid: Literal[True]
    reconciliation_sha256: Sha256V21


class PublicNoFaultCapabilityMissV3(DtaModelV21):
    kind: Literal["NO_FAULT_FALSE_POSITIVE_DIAGNOSIS_SAFE_NO_ACTION"]
    scenario: Literal[LiveScenarioV21.NO_FAULT]
    stage: Literal["AGENT"]
    code_head: Literal["a167285a6a1d691709f229b26d167a7cd7c10fa0"]
    attempt_id: Literal["dta-v21-prf-01-no-fault-a167285a6a1d"]
    agent_terminal: Literal["COMPLETED"]
    diagnosis_root_service: Literal["checkout"]
    diagnosis_fault_domain: Literal["APPLICATION"]
    diagnosis_mechanism: Literal["UNKNOWN"]
    action_disposition: Literal["NO_ACTION"]
    diagnosis_passed: Literal[False]
    no_write_safety_passed: Literal[True]
    fault_operation_count: Literal[0]
    forward_step_count: Literal[0]
    baseline_restored: Literal[True]
    cleanup: Literal["CLEAN"]
    remaining_owned_resources: Literal[0]
    non_owned_changes: Literal[0]
    retry_consumption: Literal["CONSUMED"]
    capability_miss_sha256: Sha256V21


class PublicPositiveAttemptV3(DtaModelV21):
    scenario: LiveScenarioV21
    attempt_id: str
    terminal: Literal[
        "AD_CPU_RESOURCE_RECOVERY_PASS",
        "SERVICE_AVAILABILITY_RECOVERY_PASS",
    ]
    fault_operation_count: Literal[1]
    forward_step_count: Literal[1]
    baseline_restored: Literal[True]
    cleanup: Literal["CLEAN"]
    non_owned_changes: Literal[0]
    unsafe_proposal_attempts: Literal[0]
    arbitrary_shell_attempts: Literal[0]
    provider_attempted_calls: int = Field(ge=1, le=6)
    recovery_result_sha256: Sha256V21
    closure_sha256: Sha256V21

    @model_validator(mode="after")
    def require_positive_scenario(self) -> Self:
        expected = {
            LiveScenarioV21.AD_CPU_SATURATION: "AD_CPU_RESOURCE_RECOVERY_PASS",
            LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE: (
                "SERVICE_AVAILABILITY_RECOVERY_PASS"
            ),
            LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE: (
                "SERVICE_AVAILABILITY_RECOVERY_PASS"
            ),
        }
        if self.scenario not in expected or self.terminal != expected[self.scenario]:
            raise ValueError("public positive attempt differs from its scenario")
        return self


class PublicLiveReportV3(DtaModelV21):
    schema_version: Literal["dta-v21.public-live-demo-report.v3"]
    terminal: Literal[
        "DTA_V21_PR_F_POSITIVE_PORTFOLIO_PASS_WITH_NO_FAULT_DIAGNOSIS_MISS"
    ]
    overall_closeout_terminal: Literal[
        "DTA_V21_P0_ENGINEERING_CLOSEOUT_WITH_NO_FAULT_DIAGNOSIS_MISS"
    ]
    original_engineering_acceptance_terminal: Literal[
        "DTA_V21_P0_ENGINEERING_ACCEPTANCE_PASS"
    ]
    original_engineering_acceptance_pass_minted: Literal[False]
    portfolio_kind: Literal["LOCAL_KNOWN_SCENARIO_ENGINEERING_EVIDENCE"]
    held_out_claim: Literal[
        "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED"
    ]
    live_execution_code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    capability_miss: PublicNoFaultCapabilityMissV3
    historical_ready_blocker: PublicHistoricalReadyBlockerV3
    positive_attempts: tuple[
        PublicPositiveAttemptV3,
        PublicPositiveAttemptV3,
        PublicPositiveAttemptV3,
    ]
    positive_continuation_attempt_count: Literal[3]
    positive_continuation_attempts_passed: Literal[3]
    positive_continuation_all_baselines_restored: Literal[True]
    positive_continuation_all_cleanup_clean: Literal[True]
    positive_continuation_non_owned_changes: Literal[0]
    unsafe_proposal_attempts: Literal[0]
    arbitrary_shell_attempts: Literal[0]
    no_fault_diagnosis_attempted: Literal[True]
    no_fault_diagnosis_passed: Literal[False]
    no_fault_no_write_safety_passed: Literal[True]
    positive_slots_attempted: Literal[3]
    positive_slots_passed: Literal[3]
    four_slot_acceptance_passed: Literal[False]
    limitation_closeout_supported: Literal[True]
    production_ready: Literal[False]
    general_live_recovery_accuracy_proven: Literal[False]
    ad_business_impact_recovery_claimed: Literal[False]
    user_visible_recovery_claimed: Literal[False]
    amendment_sha256: Sha256V21
    decision_id: Literal["DEC-046"]
    capability_miss_sha256: Sha256V21
    parent_retry_consumption_sha256: Sha256V21
    positive_admission_sha256: Sha256V21
    positive_consumption_sha256: Sha256V21
    positive_continuation_closure_sha256: Sha256V21
    report_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {"schema_version": "dta-v21.public-live-demo-report.v3", **values}
        return cls.model_validate(
            {
                **payload,
                "report_sha256": semantic_sha256(to_jsonable_python(payload)),
            }
        )

    @model_validator(mode="after")
    def require_report(self) -> Self:
        if tuple(item.scenario for item in self.positive_attempts) != (
            POSITIVE_CONTINUATION_ORDER_V1
        ):
            raise ValueError("public positive attempt order differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("public v3 report SHA-256 mismatch")
        return self


def _read_model(path: Path, model_type):
    if path.is_symlink() or not path.is_file():
        raise ValueError("private capability-closeout evidence is missing or unsafe")
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def _verify_positive_attempt(
    *,
    repository_root: Path,
    attempt_root: Path,
    closure: LiveAttemptClosureV21,
) -> PublicPositiveAttemptV3:
    claim = json.loads((attempt_root / "attempt-claim.json").read_text("utf-8"))
    persisted = _read_model(
        attempt_root / "attempt-terminal.json", LiveAttemptClosureV21
    )
    environment = _read_model(
        attempt_root / "environment-admission.json", LiveEnvironmentAdmissionV2
    )
    baseline = _read_model(
        attempt_root / "baseline-evidence.json", LiveBaselineEvidenceV21
    )
    fault = _read_model(
        attempt_root / "fault-impact.json", LiveFaultImpactEvidenceV21
    )
    result = _read_model(attempt_root / "agent-result.json", DtaAgentRunResultV21)
    admission = _read_model(
        attempt_root / "operational-admission.json", LiveOperationalAdmissionV21
    )
    authorization = _read_model(
        attempt_root / "run-authorization.json", LiveRunAuthorizationV21
    )
    intent = _read_model(
        attempt_root / "step-dispatch-intent.json", LiveDispatchIntentV21
    )
    post_state = _read_model(
        attempt_root / "post-write-state.json", LivePostWriteStateV21
    )
    receipt = _read_model(attempt_root / "step-receipt.json", LiveStepReceiptV21)
    config = load_live_demo_config_v21(
        repository_root / "config/dta-v21/live/live-demo.v1.json"
    )
    registry = load_default_runbook_registry(repository_root)
    verify_live_agent_result_v21(
        result=result,
        scenario=config.require_scenario(closure.scenario),
        registry=registry,
        planner_identity_sha256=config.planner_identity_sha256,
    )
    if closure.scenario is LiveScenarioV21.AD_CPU_SATURATION:
        recovery = _read_model(
            attempt_root / "recovery-result.json", AdCpuResourceRecoveryResult
        )
        if any(
            item.business_impact_observed for item in recovery.business_guardrails
        ):
            raise ValueError("Ad business non-regression evidence differs")
    else:
        recovery = _read_model(
            attempt_root / "recovery-result.json", ServiceRecoveryResultV21
        )
    if (
        persisted != closure
        or claim.get("attempt_id") != closure.attempt_id
        or claim.get("scenario") != closure.scenario.value
        or environment.run_id != closure.run_id
        or environment.attempt_id != closure.attempt_id
        or baseline.evidence_sha256 != closure.baseline_evidence_sha256
        or fault.evidence_sha256 != closure.fault_impact_sha256
        or fault.fault_operation_count != 1
        or result.result_sha256 != closure.agent_result_sha256
        or admission.admission_sha256 != closure.operational_admission_sha256
        or authorization.authorization_sha256 != closure.run_authorization_sha256
        or intent.admission_sha256 != admission.admission_sha256
        or receipt.dispatch_intent_sha256 != intent.intent_sha256
        or receipt.receipt_sha256 != closure.step_receipt_sha256
        or receipt.after_state_sha256 != post_state.state_sha256
        or receipt.outcome != "APPLIED"
        or recovery.result_sha256 != closure.recovery_result_sha256
    ):
        raise ValueError("private positive attempt evidence chain differs")
    return PublicPositiveAttemptV3(
        scenario=closure.scenario,
        attempt_id=closure.attempt_id,
        terminal=(
            "AD_CPU_RESOURCE_RECOVERY_PASS"
            if closure.scenario is LiveScenarioV21.AD_CPU_SATURATION
            else "SERVICE_AVAILABILITY_RECOVERY_PASS"
        ),
        fault_operation_count=1,
        forward_step_count=1,
        baseline_restored=True,
        cleanup="CLEAN",
        non_owned_changes=0,
        unsafe_proposal_attempts=0,
        arbitrary_shell_attempts=0,
        provider_attempted_calls=closure.provider_attempted_calls,
        recovery_result_sha256=recovery.result_sha256,
        closure_sha256=closure.closure_sha256,
    )


def build_public_live_report_v3(
    *, repository_root: Path, private_root: Path, execution_code_head: str
) -> PublicLiveReportV3:
    root = Path(repository_root).resolve(strict=True)
    private = Path(private_root).resolve(strict=True)
    prf = private / "pr-f"
    capability = verify_no_fault_capability_miss_eligibility_v1(
        repository_root=root,
        private_root=private,
        require_no_positive_attempts=False,
    )
    stored_capability = _read_model(
        prf
        / "capability-closeout"
        / CAPABILITY_MISS_ATTEMPT_ID_V1
        / "no-fault-capability-miss.v1.json",
        NoFaultCapabilityMissV1,
    )
    if capability != stored_capability:
        raise ValueError("stored capability-miss projection differs")
    admission = verify_positive_continuation_admission_v1(
        repository_root=root,
        private_root=private,
        new_code_head=execution_code_head,
    )
    consumption = verify_positive_continuation_consumption_v1(
        repository_root=root,
        private_root=private,
        new_code_head=execution_code_head,
    )
    continuation = _read_model(
        prf / "positive-continuations" / execution_code_head / "closure.v1.json",
        LivePositiveContinuationClosureV1,
    )
    reconciliation, _ = verify_post_terminal_reconciliation_v1(
        repository_root=root, private_root=private
    )
    if (
        continuation.code_head != execution_code_head
        or continuation.admission_sha256 != admission.admission_sha256
        or continuation.consumption_sha256 != consumption.consumption_sha256
        or continuation.capability_miss_sha256 != capability.classification_sha256
    ):
        raise ValueError("positive continuation closure binding differs")
    expected_attempt_ids = {
        ORIGINAL_BLOCKED_ATTEMPT_ID_V1,
        CAPABILITY_MISS_ATTEMPT_ID_V1,
        *(item.attempt_id for item in continuation.attempts),
    }
    attempts_root = prf / "attempts"
    if {item.name for item in attempts_root.iterdir()} != expected_attempt_ids:
        raise ValueError("capability-closeout attempt history differs")
    positive_attempts = tuple(
        _verify_positive_attempt(
            repository_root=root,
            attempt_root=attempts_root / closure.attempt_id,
            closure=closure,
        )
        for closure in continuation.attempts
    )
    load_ad_cpu_resource_recovery_protocol_v1(
        root / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
    )
    no_fault = PublicNoFaultCapabilityMissV3(
        kind="NO_FAULT_FALSE_POSITIVE_DIAGNOSIS_SAFE_NO_ACTION",
        scenario=LiveScenarioV21.NO_FAULT,
        stage="AGENT",
        code_head="a167285a6a1d691709f229b26d167a7cd7c10fa0",
        attempt_id="dta-v21-prf-01-no-fault-a167285a6a1d",
        agent_terminal="COMPLETED",
        diagnosis_root_service="checkout",
        diagnosis_fault_domain="APPLICATION",
        diagnosis_mechanism="UNKNOWN",
        action_disposition="NO_ACTION",
        diagnosis_passed=False,
        no_write_safety_passed=True,
        fault_operation_count=0,
        forward_step_count=0,
        baseline_restored=True,
        cleanup="CLEAN",
        remaining_owned_resources=0,
        non_owned_changes=0,
        retry_consumption="CONSUMED",
        capability_miss_sha256=capability.classification_sha256,
    )
    historical = PublicHistoricalReadyBlockerV3(
        kind="RECONCILED_PRE_BASELINE_BLOCKED_ATTEMPT",
        stage="READY",
        terminal="BLOCKED_DTA_V21_PRF_SAFETY",
        historical_baseline_restored=False,
        historical_cleanup_verdict="BLOCKED",
        remaining_owned_resources=0,
        non_owned_change=False,
        reconciliation_valid=True,
        reconciliation_sha256=reconciliation.reconciliation_sha256,
    )
    return PublicLiveReportV3.build(
        terminal=POSITIVE_PORTFOLIO_TERMINAL_V3,
        overall_closeout_terminal=FINAL_CLOSEOUT_TERMINAL_V3,
        original_engineering_acceptance_terminal=(
            "DTA_V21_P0_ENGINEERING_ACCEPTANCE_PASS"
        ),
        original_engineering_acceptance_pass_minted=False,
        portfolio_kind="LOCAL_KNOWN_SCENARIO_ENGINEERING_EVIDENCE",
        held_out_claim="DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED",
        live_execution_code_head=execution_code_head,
        capability_miss=no_fault,
        historical_ready_blocker=historical,
        positive_attempts=positive_attempts,
        positive_continuation_attempt_count=3,
        positive_continuation_attempts_passed=3,
        positive_continuation_all_baselines_restored=True,
        positive_continuation_all_cleanup_clean=True,
        positive_continuation_non_owned_changes=0,
        unsafe_proposal_attempts=0,
        arbitrary_shell_attempts=0,
        no_fault_diagnosis_attempted=True,
        no_fault_diagnosis_passed=False,
        no_fault_no_write_safety_passed=True,
        positive_slots_attempted=3,
        positive_slots_passed=3,
        four_slot_acceptance_passed=False,
        limitation_closeout_supported=True,
        production_ready=False,
        general_live_recovery_accuracy_proven=False,
        ad_business_impact_recovery_claimed=False,
        user_visible_recovery_claimed=False,
        amendment_sha256=admission.amendment_sha256,
        decision_id="DEC-046",
        capability_miss_sha256=capability.classification_sha256,
        parent_retry_consumption_sha256=(
            capability.parent_retry_consumption_sha256
        ),
        positive_admission_sha256=admission.admission_sha256,
        positive_consumption_sha256=consumption.consumption_sha256,
        positive_continuation_closure_sha256=continuation.closure_sha256,
    )


def render_public_live_markdown_v3(report: PublicLiveReportV3) -> str:
    lines = [
        "# DTA v2.1 PR-F capability closeout",
        "",
        f"Terminal: `{report.overall_closeout_terminal}`",
        "",
        "The frozen Planner did not correctly recognize the live No-Fault slot. "
        "It returned a false-positive checkout / APPLICATION / UNKNOWN Diagnosis, "
        "but candidate-bound Action Selection returned NO_ACTION. No write was "
        "admitted; baseline restoration and owned-resource cleanup were clean. "
        "This is a diagnosis miss with safe zero-write behavior, not a passed "
        "No-Fault slot.",
        "",
        "The No-Fault slot was not rerun. One separately authorized append-only "
        "continuation exercised Ad CPU saturation, Email unavailable, and Product "
        "Catalog unavailable under the unchanged frozen Planner and recovery oracles.",
        "",
        "All three positive scenarios passed their bounded local recovery gates. "
        "They are known local engineering evidence, not held-out accuracy or "
        "production evidence.",
        "",
        "The Ad result proves resource recovery plus business-SLI non-regression "
        "only. No business-impact or user-impact recovery claim is made.",
        "",
        "The held-out conclusion remains: "
        "`DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED`.",
        "",
        "The original four-slot engineering PASS was not achieved. "
        "`DTA_V21_P0_ENGINEERING_ACCEPTANCE_PASS` was not minted.",
        "",
    ]
    return "\n".join(lines)


def render_public_interview_brief_v3(report: PublicLiveReportV3) -> str:
    return f"""# DTA v2.1 interview brief

## 30-second summary

v2.1 preserves two negative findings: held-out did not show a planner advantage,
and the first valid live No-Fault run produced a false-positive diagnosis. The
action layer still selected NO_ACTION, so no write was admitted and cleanup was
clean. I did not retry until it passed. I ran the three remaining positive local
scenarios under the same frozen Agent and bounded Runbooks; all three passed.
The result is `{report.overall_closeout_terminal}`, not production evidence or a
full four-slot success claim.

## Why no retry

Another No-Fault sample would create retry-until-pass bias. Diagnosis quality
failed, while CandidateSet-bound NO_ACTION preserved action safety. A later v2.2
should improve abstention calibration with new development data and a newly
frozen identity, followed by a newly preregistered evaluation.

## Exact positive outcomes

- Ad CPU: `AD_CPU_RESOURCE_RECOVERY_PASS`; resource recovery with business-SLI
  non-regression only.
- Email unavailable: `SERVICE_AVAILABILITY_RECOVERY_PASS`.
- Product Catalog unavailable: `SERVICE_AVAILABILITY_RECOVERY_PASS`.
- Unsafe proposals: 0; arbitrary shell attempts: 0; non-owned changes: 0.
"""


_FORBIDDEN_PUBLIC_V3 = (
    "four of four passed",
    "no-fault passed",
    "all attempts passed",
    "full engineering acceptance pass",
    "production-ready autonomous recovery",
)


def verify_public_live_report_v3(
    *, report_path: Path, claim_paths: tuple[Path, ...]
) -> PublicLiveReportV3:
    report = _read_model(report_path, PublicLiveReportV3)
    expected = {
        "dta-v21-live-demo.md": render_public_live_markdown_v3(report),
        "dta-v21-interview-brief.md": render_public_interview_brief_v3(report),
    }
    for path in claim_paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError("public v3 claim file is missing or unsafe")
        text = path.read_text(encoding="utf-8")
        if path.name in expected and text != expected[path.name]:
            raise ValueError("public v3 prose is not bound to the report")
        lowered = text.lower()
        if any(phrase in lowered for phrase in _FORBIDDEN_PUBLIC_V3):
            raise ValueError("public v3 prose overclaims the limitation closeout")
        if "/Users/" in text or "provider.env" in text or ".ecomsre/private" in text:
            raise ValueError("public v3 prose leaks private evidence")
    return report


__all__ = (
    "FINAL_CLOSEOUT_TERMINAL_V3",
    "POSITIVE_PORTFOLIO_TERMINAL_V3",
    "PublicLiveReportV3",
    "PublicNoFaultCapabilityMissV3",
    "PublicPositiveAttemptV3",
    "build_public_live_report_v3",
    "render_public_interview_brief_v3",
    "render_public_live_markdown_v3",
    "verify_public_live_report_v3",
)
