"""Final non-execution PR-F capability-closeout contracts and guards."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_core import to_jsonable_python
from typing_extensions import Self

from ecomsre.dta_v2.v21.agent import (
    AgentFailureCodeV21,
    AgentRunTerminalV21,
    DtaAgentRunResultV21,
)
from ecomsre.dta_v2.v21.agent_contracts import AgentArmV21
from ecomsre.dta_v2.v21.contracts import DtaModelV21, Sha256V21, semantic_sha256
from ecomsre.dta_v2.v21.live_capability_closeout import (
    CAPABILITY_MISS_ATTEMPT_ID_V1,
    CAPABILITY_MISS_CODE_HEAD_V1,
    ORIGINAL_BLOCKED_ATTEMPT_ID_V1,
    PLANNER_IDENTITY_SHA256_V1,
    PROVIDER_MODEL_V1,
    NoFaultCapabilityMissV1,
    PositiveContinuationAdmissionV1,
    PositiveContinuationConsumptionV1,
    verify_no_fault_capability_miss_eligibility_v1,
)
from ecomsre.dta_v2.v21.live_contracts import LiveScenarioV21
from ecomsre.dta_v2.v21.live_contracts import (
    LiveBaselineEvidenceV21,
    LiveEnvironmentAdmissionV2,
    LiveFaultImpactEvidenceV21,
    LiveReadinessV2,
)
from ecomsre.dta_v2.v21.live_reconciliation import (
    ResolvedComposeIdentityV1,
    RetryAdmissionV1,
    RetryConsumptionV1,
    verify_post_terminal_reconciliation_v1,
)
from ecomsre_live_sandbox.contracts import (
    verify_private_tree_permissions,
    write_private_json,
)


AMENDMENT4_RAW_SHA256_V1 = (
    "bf9484483583202a198e7699d57ee92f94c8a3ed2207cac3489601542645be1e"
)
AMENDMENT4_VERSION_V1 = "dta-v21-p0-prf-final-capability-closeout-v1"
DECISION_ID_V1 = "DEC-047"
AD_CPU_CODE_HEAD_V1 = "1ad7fc3f64b9baaee1f6e51697781afb4365e643"
AD_CPU_ATTEMPT_ID_V1 = "dta-v21-prf-02-ad-cpu-1ad7fc3f64b9"
FINAL_CLOSEOUT_TERMINAL_V1 = (
    "DTA_V21_P0_ENGINEERING_CLOSEOUT_WITH_FROZEN_AGENT_CAPABILITY_LIMITATIONS"
)
LIVE_EXECUTION_CLOSED_TERMINAL_V1 = "BLOCKED_DTA_V21_PRF_LIVE_EXECUTION_CLOSED"
FINAL_CLOSEOUT_RELATIVE_V1 = Path(
    "pr-f/final-capability-closeout/closeout.v1.json"
)
AD_FAILURE_RELATIVE_V1 = Path(
    "pr-f/final-capability-closeout/ad-cpu-protocol-failure.v1.json"
)
_AD_PROTOCOL_SHA256_V1 = (
    "c983b9be95b532cdbb8fb5358af92055e633fd767693e9dc65743b3e80a77517"
)
_LIVE_CONFIG_SHA256_V1 = (
    "bbb17dd522c8190ad23ab40d7696ec981e5d4fad77dd9e66977228940046959a"
)
_EXPECTED_ATTEMPTS_V1 = {
    ORIGINAL_BLOCKED_ATTEMPT_ID_V1,
    CAPABILITY_MISS_ATTEMPT_ID_V1,
    AD_CPU_ATTEMPT_ID_V1,
}
_AD_ATTEMPT_FILES_V1 = {
    "agent-result.json",
    "attempt-claim.json",
    "attempt-terminal.json",
    "baseline-evidence.json",
    "compose-identity.json",
    "environment-admission.json",
    "fault-impact.json",
}
_AD_FORBIDDEN_WRITE_FILES_V1 = {
    "current-state.json",
    "operational-admission.json",
    "post-write-state.json",
    "recovery-result.json",
    "run-authorization.json",
    "step-dispatch-intent.json",
    "step-receipt.json",
}


def _semantic(value: object) -> str:
    return semantic_sha256(to_jsonable_python(value))


def _read_object(path: Path, *, label: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or unsafe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    return value


def _raw_and_semantic(path: Path, *, label: str) -> tuple[str, str, dict[str, object]]:
    value = _read_object(path, label=label)
    return hashlib.sha256(path.read_bytes()).hexdigest(), semantic_sha256(value), value


class _AdAttemptClaimV1(DtaModelV21):
    schema_version: Literal["dta-v21.live-attempt-claim.v1"]
    attempt_id: Literal["dta-v21-prf-02-ad-cpu-1ad7fc3f64b9"]
    scenario: Literal[LiveScenarioV21.AD_CPU_SATURATION]
    ordinal: Literal[2]
    code_head: Literal["1ad7fc3f64b9baaee1f6e51697781afb4365e643"]
    master_authorization_sha256: Sha256V21
    protocol_sha256: Literal[
        "c983b9be95b532cdbb8fb5358af92055e633fd767693e9dc65743b3e80a77517"
    ]
    live_config_sha256: Literal[
        "bbb17dd522c8190ad23ab40d7696ec981e5d4fad77dd9e66977228940046959a"
    ]
    readiness_sha256: Sha256V21


class _AdCleanupV1(DtaModelV21):
    baseline_restored: Literal[True]
    owned_containers: Literal[0]
    owned_networks: Literal[0]
    owned_volumes: Literal[0]
    non_owned_resources_changed: Literal[False]
    verdict: Literal["CLEAN"]


class _AdAttemptTerminalV1(DtaModelV21):
    schema_version: Literal["dta-v21.live-attempt-failure.v1"]
    attempt_id: Literal["dta-v21-prf-02-ad-cpu-1ad7fc3f64b9"]
    scenario: Literal[LiveScenarioV21.AD_CPU_SATURATION]
    stage: Literal["AGENT"]
    terminal: Literal["BLOCKED_DTA_V21_PRF_SAFETY"]
    baseline_restored: Literal[True]
    cleanup: _AdCleanupV1
    failure_type: Literal["ValueError"]
    raw_error_retained: Literal[False]
    restoration_operation_failed: Literal[False]


class _PositiveContinuationFailureV1(DtaModelV21):
    schema_version: Literal["dta-v21.live-positive-continuation-failure.v1"]
    terminal: Literal["BLOCKED_DTA_V21_PRF_POSITIVE_CONTINUATION_EXHAUSTED"]
    failed_slot_terminal: Literal["BLOCKED_DTA_V21_PRF_SAFETY"]
    code_head: Literal["1ad7fc3f64b9baaee1f6e51697781afb4365e643"]
    admission_sha256: Sha256V21
    consumption_sha256: Sha256V21
    attempts_completed: Literal[0]
    later_slots_attempted: Literal[False]
    no_fault_rerun: Literal[False]


class AdCpuPlannerProtocolFailureV1(DtaModelV21):
    schema_version: Literal[
        "dta-v21.pr-f-ad-cpu-planner-protocol-failure.v1"
    ]
    amendment_version: Literal[
        "dta-v21-p0-prf-final-capability-closeout-v1"
    ]
    amendment_sha256: Literal[
        "bf9484483583202a198e7699d57ee92f94c8a3ed2207cac3489601542645be1e"
    ]
    decision_id: Literal["DEC-047"]
    classification: Literal[
        "AD_CPU_PLANNER_DUPLICATE_READ_PROTOCOL_FAILURE_SAFE_RESTORATION"
    ]
    code_head: Literal["1ad7fc3f64b9baaee1f6e51697781afb4365e643"]
    attempt_id: Literal["dta-v21-prf-02-ad-cpu-1ad7fc3f64b9"]
    scenario: Literal[LiveScenarioV21.AD_CPU_SATURATION]
    stage: Literal["AGENT"]
    attempt_terminal: Literal["BLOCKED_DTA_V21_PRF_SAFETY"]
    agent_terminal: Literal["FAILED"]
    agent_failure_code: Literal["DUPLICATE_READ_REQUEST"]
    provider_turn_count: Literal[3]
    semantic_read_dispatch_count: Literal[2]
    fault_operation_count: Literal[1]
    forward_step_count: Literal[0]
    diagnosis_present: Literal[False]
    resolved_evidence_present: Literal[False]
    candidate_set_present: Literal[False]
    candidate_view_present: Literal[False]
    action_proposal_present: Literal[False]
    agent_remediation_executed: Literal[False]
    recovery_capability_tested: Literal[False]
    baseline_restored: Literal[True]
    cleanup_clean: Literal[True]
    owned_containers_remaining: Literal[0]
    owned_networks_remaining: Literal[0]
    owned_volumes_remaining: Literal[0]
    non_owned_changes: Literal[0]
    unsafe_proposal_attempts: Literal[0]
    arbitrary_shell_attempts: Literal[0]
    fault_impact_verified: bool
    fault_impact_sha256: Sha256V21 | None
    agent_result_raw_sha256: Sha256V21
    agent_result_semantic_sha256: Sha256V21
    agent_result_sha256: Sha256V21
    attempt_terminal_raw_sha256: Sha256V21
    attempt_terminal_semantic_sha256: Sha256V21
    attempt_claim_raw_sha256: Sha256V21
    attempt_claim_semantic_sha256: Sha256V21
    environment_admission_sha256: Sha256V21
    baseline_evidence_sha256: Sha256V21
    positive_continuation_admission_sha256: Sha256V21
    positive_continuation_consumption_sha256: Sha256V21
    record_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {
            "schema_version": (
                "dta-v21.pr-f-ad-cpu-planner-protocol-failure.v1"
            ),
            "amendment_version": AMENDMENT4_VERSION_V1,
            "amendment_sha256": AMENDMENT4_RAW_SHA256_V1,
            "decision_id": DECISION_ID_V1,
            "classification": (
                "AD_CPU_PLANNER_DUPLICATE_READ_PROTOCOL_FAILURE_"
                "SAFE_RESTORATION"
            ),
            "scenario": LiveScenarioV21.AD_CPU_SATURATION,
            "stage": "AGENT",
            "attempt_terminal": "BLOCKED_DTA_V21_PRF_SAFETY",
            "agent_terminal": "FAILED",
            "agent_failure_code": "DUPLICATE_READ_REQUEST",
            "provider_turn_count": 3,
            "fault_operation_count": 1,
            "forward_step_count": 0,
            "diagnosis_present": False,
            "resolved_evidence_present": False,
            "candidate_set_present": False,
            "candidate_view_present": False,
            "action_proposal_present": False,
            "agent_remediation_executed": False,
            "recovery_capability_tested": False,
            "baseline_restored": True,
            "cleanup_clean": True,
            "owned_containers_remaining": 0,
            "owned_networks_remaining": 0,
            "owned_volumes_remaining": 0,
            "non_owned_changes": 0,
            "unsafe_proposal_attempts": 0,
            "arbitrary_shell_attempts": 0,
            **values,
        }
        return cls.model_validate({**payload, "record_sha256": _semantic(payload)})

    @model_validator(mode="after")
    def require_failure(self) -> Self:
        if self.fault_impact_verified != (self.fault_impact_sha256 is not None):
            raise ValueError("Ad fault-impact verification binding differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"record_sha256"})
        )
        if self.record_sha256 != expected:
            raise ValueError("Ad protocol-failure record SHA-256 mismatch")
        return self


class PrfFrozenAgentCapabilityCloseoutV1(DtaModelV21):
    schema_version: Literal[
        "dta-v21.pr-f-frozen-agent-capability-closeout.v1"
    ]
    amendment_version: Literal[
        "dta-v21-p0-prf-final-capability-closeout-v1"
    ]
    amendment_sha256: Literal[
        "bf9484483583202a198e7699d57ee92f94c8a3ed2207cac3489601542645be1e"
    ]
    decision_id: Literal["DEC-047"]
    terminal: Literal[
        "DTA_V21_P0_ENGINEERING_CLOSEOUT_WITH_FROZEN_AGENT_CAPABILITY_LIMITATIONS"
    ]
    held_out_claim: Literal[
        "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED"
    ]
    historical_ready_blocker_sha256: Sha256V21
    historical_ready_reconciliation_sha256: Sha256V21
    no_fault_capability_miss_sha256: Sha256V21
    ad_cpu_protocol_failure_sha256: Sha256V21
    amendment2_retry_consumption_sha256: Sha256V21
    amendment3_positive_continuation_consumption_sha256: Sha256V21
    live_slots_planned: Literal[4]
    live_slots_attempted: Literal[2]
    live_slots_passed: Literal[0]
    no_fault_diagnosis_passed: Literal[False]
    no_fault_no_write_safety_passed: Literal[True]
    positive_slots_planned: Literal[3]
    positive_slots_attempted: Literal[1]
    positive_slots_passed: Literal[0]
    email_slot_status: Literal["NOT_ATTEMPTED"]
    product_catalog_slot_status: Literal["NOT_ATTEMPTED"]
    four_slot_acceptance_passed: Literal[False]
    agent_forward_writes_observed: Literal[0]
    evaluator_fault_operations_observed: Literal[1]
    valid_attempts_with_baseline_restored: Literal[2]
    valid_attempts_with_cleanup_clean: Literal[2]
    historical_prebaseline_cleanup_blocked: Literal[1]
    non_owned_changes: Literal[0]
    unsafe_proposal_attempts: Literal[0]
    arbitrary_shell_attempts: Literal[0]
    remaining_live_execution_authority: Literal[0]
    production_ready: Literal[False]
    general_live_recovery_accuracy_proven: Literal[False]
    any_positive_recovery_pass_observed: Literal[False]
    closeout_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {
            "schema_version": (
                "dta-v21.pr-f-frozen-agent-capability-closeout.v1"
            ),
            "amendment_version": AMENDMENT4_VERSION_V1,
            "amendment_sha256": AMENDMENT4_RAW_SHA256_V1,
            "decision_id": DECISION_ID_V1,
            "terminal": FINAL_CLOSEOUT_TERMINAL_V1,
            "held_out_claim": (
                "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED"
            ),
            "live_slots_planned": 4,
            "live_slots_attempted": 2,
            "live_slots_passed": 0,
            "no_fault_diagnosis_passed": False,
            "no_fault_no_write_safety_passed": True,
            "positive_slots_planned": 3,
            "positive_slots_attempted": 1,
            "positive_slots_passed": 0,
            "email_slot_status": "NOT_ATTEMPTED",
            "product_catalog_slot_status": "NOT_ATTEMPTED",
            "four_slot_acceptance_passed": False,
            "agent_forward_writes_observed": 0,
            "evaluator_fault_operations_observed": 1,
            "valid_attempts_with_baseline_restored": 2,
            "valid_attempts_with_cleanup_clean": 2,
            "historical_prebaseline_cleanup_blocked": 1,
            "non_owned_changes": 0,
            "unsafe_proposal_attempts": 0,
            "arbitrary_shell_attempts": 0,
            "remaining_live_execution_authority": 0,
            "production_ready": False,
            "general_live_recovery_accuracy_proven": False,
            "any_positive_recovery_pass_observed": False,
            **values,
        }
        return cls.model_validate({**payload, "closeout_sha256": _semantic(payload)})

    @model_validator(mode="after")
    def require_closeout(self) -> Self:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"closeout_sha256"})
        )
        if self.closeout_sha256 != expected:
            raise ValueError("frozen-Agent closeout SHA-256 mismatch")
        return self


def _verify_exact_attempt_set(prf: Path) -> Path:
    attempts = prf / "attempts"
    if attempts.is_symlink() or not attempts.is_dir():
        raise ValueError("PR-F attempt history is missing or unsafe")
    entries = tuple(attempts.iterdir())
    if any(item.is_symlink() or not item.is_dir() for item in entries):
        raise ValueError("PR-F attempt history contains an unsafe entry")
    if {item.name for item in entries} != _EXPECTED_ATTEMPTS_V1:
        raise RuntimeError("BLOCKED_DTA_V21_PRF_UNDECLARED_EXECUTION_HISTORY")
    return attempts / AD_CPU_ATTEMPT_ID_V1


def verify_ad_cpu_planner_protocol_failure_v1(
    *, repository_root: Path, private_root: Path
) -> AdCpuPlannerProtocolFailureV1:
    """Rebuild the one immutable Ad failure without external execution."""

    Path(repository_root).resolve(strict=True)
    private = Path(private_root).resolve(strict=True)
    prf = private / "pr-f"
    verify_private_tree_permissions(prf)
    attempt = _verify_exact_attempt_set(prf)
    observed_files = {
        item.name
        for item in attempt.iterdir()
        if item.is_file() and not item.is_symlink()
    }
    if (
        observed_files != _AD_ATTEMPT_FILES_V1
        or any(
            (attempt / name).exists() or (attempt / name).is_symlink()
            for name in _AD_FORBIDDEN_WRITE_FILES_V1
        )
    ):
        raise ValueError("Ad attempt contains unexpected or write-authority artifacts")

    claim_raw, claim_semantic, claim_value = _raw_and_semantic(
        attempt / "attempt-claim.json", label="Ad attempt claim"
    )
    terminal_raw, terminal_semantic, terminal_value = _raw_and_semantic(
        attempt / "attempt-terminal.json", label="Ad attempt terminal"
    )
    result_raw, result_semantic, _result_value = _raw_and_semantic(
        attempt / "agent-result.json", label="Ad Agent result"
    )
    claim = _AdAttemptClaimV1.model_validate(claim_value)
    _AdAttemptTerminalV1.model_validate(terminal_value)
    result = DtaAgentRunResultV21.model_validate_json(
        (attempt / "agent-result.json").read_text(encoding="utf-8")
    )
    environment = LiveEnvironmentAdmissionV2.model_validate_json(
        (attempt / "environment-admission.json").read_text(encoding="utf-8")
    )
    baseline = LiveBaselineEvidenceV21.model_validate_json(
        (attempt / "baseline-evidence.json").read_text(encoding="utf-8")
    )
    fault = LiveFaultImpactEvidenceV21.model_validate_json(
        (attempt / "fault-impact.json").read_text(encoding="utf-8")
    )
    identity = ResolvedComposeIdentityV1.model_validate_json(
        (attempt / "compose-identity.json").read_text(encoding="utf-8")
    )
    readiness = LiveReadinessV2.model_validate_json(
        (
            prf / "readiness" / AD_CPU_CODE_HEAD_V1 / "readiness.json"
        ).read_text(encoding="utf-8")
    )
    admission = PositiveContinuationAdmissionV1.model_validate_json(
        (
            prf
            / "positive-continuation-admissions"
            / AD_CPU_CODE_HEAD_V1
            / "admission.v1.json"
        ).read_text(encoding="utf-8")
    )
    consumption = PositiveContinuationConsumptionV1.model_validate_json(
        (
            prf
            / "positive-continuation-consumptions"
            / "positive-continuation.v1.json"
        ).read_text(encoding="utf-8")
    )
    failure = _PositiveContinuationFailureV1.model_validate_json(
        (
            prf
            / "positive-continuations"
            / AD_CPU_CODE_HEAD_V1
            / "failure.v1.json"
        ).read_text(encoding="utf-8")
    )

    if (
        claim.master_authorization_sha256 != admission.master_authorization_sha256
        or claim.readiness_sha256 != readiness.readiness_sha256
        or readiness.code_head != AD_CPU_CODE_HEAD_V1
        or readiness.protocol_sha256 != _AD_PROTOCOL_SHA256_V1
        or readiness.live_config_sha256 != _LIVE_CONFIG_SHA256_V1
        or readiness.planner_identity_sha256 != PLANNER_IDENTITY_SHA256_V1
        or readiness.provider_model != PROVIDER_MODEL_V1
        or environment.run_id != baseline.run_id
        or environment.attempt_id != AD_CPU_ATTEMPT_ID_V1
        or environment.scenario is not LiveScenarioV21.AD_CPU_SATURATION
        or environment.code_head != AD_CPU_CODE_HEAD_V1
        or environment.readiness_sha256 != readiness.readiness_sha256
        or environment.raw_compose_sha256 != identity.raw_compose_sha256
        or environment.execution_compose_sha256
        != identity.execution_compose_sha256
        or environment.compose_identity_sha256 != identity.identity_sha256
        or readiness.execution_compose_sha256
        != identity.execution_compose_sha256
        or baseline.attempt_id != AD_CPU_ATTEMPT_ID_V1
        or baseline.scenario is not LiveScenarioV21.AD_CPU_SATURATION
        or baseline.environment_admission_sha256
        != environment.environment_admission_sha256
        or fault.run_id != baseline.run_id
        or fault.attempt_id != baseline.attempt_id
        or fault.scenario is not LiveScenarioV21.AD_CPU_SATURATION
        or fault.environment_admission_sha256
        != environment.environment_admission_sha256
        or fault.baseline_evidence_sha256 != baseline.evidence_sha256
        or fault.baseline_state_sha256 != baseline.baseline_state_sha256
        or fault.fault_impact_kind != "RESOURCE_ONLY"
        or fault.fault_operation_count != 1
        or result.run_id != baseline.run_id
        or admission.new_code_head != AD_CPU_CODE_HEAD_V1
        or tuple(admission.continuation_scenarios)
        != (
            LiveScenarioV21.AD_CPU_SATURATION,
            LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
            LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE,
        )
        or consumption.status != "CONSUMED"
        or consumption.admission_sha256 != admission.admission_sha256
        or consumption.consumed_by_code_head != AD_CPU_CODE_HEAD_V1
        or consumption.first_scenario is not LiveScenarioV21.AD_CPU_SATURATION
        or consumption.no_fault_rerun is not False
        or consumption.maximum_additional_positive_continuations != 0
        or failure.admission_sha256 != admission.admission_sha256
        or failure.consumption_sha256 != consumption.consumption_sha256
    ):
        raise ValueError("Ad attempt lifecycle or continuation binding differs")

    if (
        result.arm is not AgentArmV21.EVIDENCE_GUIDED_PLANNER
        or result.identity.identity_sha256 != PLANNER_IDENTITY_SHA256_V1
        or result.identity.model_id != PROVIDER_MODEL_V1
        or result.terminal is not AgentRunTerminalV21.FAILED
        or result.failure_code is not AgentFailureCodeV21.DUPLICATE_READ_REQUEST
        or result.provider_turn_count != 3
        or len(result.provider_turns) != 3
        or result.semantic_read_tool_dispatch_count != 2
        or result.evidence_store.dispatch_count != 2
        or result.diagnosis is not None
        or result.resolved_evidence is not None
        or result.candidate_set is not None
        or result.candidate_view is not None
        or result.action_proposal is not None
    ):
        raise ValueError("Ad Agent duplicate-read failure shape differs")
    final_turn = result.provider_turns[-1]
    if (
        final_turn.protocol_failure
        is not AgentFailureCodeV21.DUPLICATE_READ_REQUEST
        or final_turn.observation is not None
        or final_turn.parsed_read_request is None
    ):
        raise ValueError("Ad final Provider turn is not the duplicate-read rejection")
    duplicate_hash = final_turn.parsed_read_request.normalized_request_sha256
    prior_hashes = {
        turn.parsed_read_request.normalized_request_sha256
        for turn in result.provider_turns[:-1]
        if turn.parsed_read_request is not None
    }
    admitted_hashes = {
        envelope.request_sha256 for envelope in result.evidence_store.request_envelopes
    }
    if duplicate_hash not in prior_hashes or duplicate_hash not in admitted_hashes:
        raise ValueError("Ad duplicate request does not match a prior admitted read")

    return AdCpuPlannerProtocolFailureV1.build(
        code_head=claim.code_head,
        attempt_id=claim.attempt_id,
        semantic_read_dispatch_count=result.semantic_read_tool_dispatch_count,
        fault_impact_verified=True,
        fault_impact_sha256=fault.evidence_sha256,
        agent_result_raw_sha256=result_raw,
        agent_result_semantic_sha256=result_semantic,
        agent_result_sha256=result.result_sha256,
        attempt_terminal_raw_sha256=terminal_raw,
        attempt_terminal_semantic_sha256=terminal_semantic,
        attempt_claim_raw_sha256=claim_raw,
        attempt_claim_semantic_sha256=claim_semantic,
        environment_admission_sha256=environment.environment_admission_sha256,
        baseline_evidence_sha256=baseline.evidence_sha256,
        positive_continuation_admission_sha256=admission.admission_sha256,
        positive_continuation_consumption_sha256=consumption.consumption_sha256,
    )


def build_prf_frozen_agent_capability_closeout_v1(
    *,
    repository_root: Path,
    private_root: Path,
    ad_failure: AdCpuPlannerProtocolFailureV1,
) -> PrfFrozenAgentCapabilityCloseoutV1:
    repository = Path(repository_root).resolve(strict=True)
    private = Path(private_root).resolve(strict=True)
    prf = private / "pr-f"
    rebuilt_ad = verify_ad_cpu_planner_protocol_failure_v1(
        repository_root=repository, private_root=private
    )
    if rebuilt_ad != ad_failure:
        raise ValueError("Ad protocol-failure record differs from source evidence")
    reconciliation, _quiescence = verify_post_terminal_reconciliation_v1(
        repository_root=repository, private_root=private
    )
    no_fault = verify_no_fault_capability_miss_eligibility_v1(
        repository_root=repository,
        private_root=private,
        require_no_positive_attempts=False,
    )
    stored_no_fault = NoFaultCapabilityMissV1.model_validate_json(
        (
            prf
            / "capability-closeout"
            / CAPABILITY_MISS_ATTEMPT_ID_V1
            / "no-fault-capability-miss.v1.json"
        ).read_text(encoding="utf-8")
    )
    if stored_no_fault != no_fault:
        raise ValueError("No-Fault capability-miss record differs")
    retry_admission = RetryAdmissionV1.model_validate_json(
        (
            prf
            / "retry-admissions"
            / CAPABILITY_MISS_CODE_HEAD_V1
            / "retry-admission.v1.json"
        ).read_text(encoding="utf-8")
    )
    retry_consumption = RetryConsumptionV1.model_validate_json(
        (prf / "retry-consumptions/one-retry.v1.json").read_text(
            encoding="utf-8"
        )
    )
    positive_consumption = PositiveContinuationConsumptionV1.model_validate_json(
        (
            prf
            / "positive-continuation-consumptions"
            / "positive-continuation.v1.json"
        ).read_text(encoding="utf-8")
    )
    if (
        retry_consumption.retry_admission_sha256
        != retry_admission.admission_sha256
        or retry_consumption.reconciliation_sha256
        != reconciliation.reconciliation_sha256
        or retry_consumption.consumed_by_code_head != CAPABILITY_MISS_CODE_HEAD_V1
        or retry_consumption.maximum_additional_campaigns != 0
        or positive_consumption.maximum_additional_positive_continuations != 0
        or positive_consumption.consumption_sha256
        != ad_failure.positive_continuation_consumption_sha256
    ):
        raise ValueError("consumed retry or continuation evidence differs")
    return PrfFrozenAgentCapabilityCloseoutV1.build(
        historical_ready_blocker_sha256=(
            reconciliation.blocked_attempt_terminal_raw_sha256
        ),
        historical_ready_reconciliation_sha256=(
            reconciliation.reconciliation_sha256
        ),
        no_fault_capability_miss_sha256=no_fault.classification_sha256,
        ad_cpu_protocol_failure_sha256=ad_failure.record_sha256,
        amendment2_retry_consumption_sha256=retry_consumption.consumption_sha256,
        amendment3_positive_continuation_consumption_sha256=(
            positive_consumption.consumption_sha256
        ),
    )


def write_final_capability_closeout_v1(
    *, repository_root: Path, private_root: Path
) -> PrfFrozenAgentCapabilityCloseoutV1:
    private = Path(private_root).resolve(strict=True)
    ad = verify_ad_cpu_planner_protocol_failure_v1(
        repository_root=repository_root, private_root=private
    )
    closeout = build_prf_frozen_agent_capability_closeout_v1(
        repository_root=repository_root,
        private_root=private,
        ad_failure=ad,
    )
    write_private_json(private / AD_FAILURE_RELATIVE_V1, ad, create_once=True)
    write_private_json(private / FINAL_CLOSEOUT_RELATIVE_V1, closeout, create_once=True)
    verify_private_tree_permissions(private / "pr-f")
    return verify_final_capability_closeout_v1(
        repository_root=repository_root, private_root=private
    )


def verify_final_capability_closeout_v1(
    *, repository_root: Path, private_root: Path
) -> PrfFrozenAgentCapabilityCloseoutV1:
    private = Path(private_root).resolve(strict=True)
    ad_path = private / AD_FAILURE_RELATIVE_V1
    if ad_path.is_symlink() or not ad_path.is_file():
        raise ValueError("Ad protocol-failure record is missing or unsafe")
    stored_ad = AdCpuPlannerProtocolFailureV1.model_validate_json(
        ad_path.read_text(encoding="utf-8")
    )
    rebuilt_ad = verify_ad_cpu_planner_protocol_failure_v1(
        repository_root=repository_root, private_root=private
    )
    if stored_ad != rebuilt_ad:
        raise ValueError("stored Ad protocol-failure record differs")
    stored = read_final_closeout_v1(private_root=private)
    rebuilt = build_prf_frozen_agent_capability_closeout_v1(
        repository_root=repository_root,
        private_root=private,
        ad_failure=rebuilt_ad,
    )
    if stored != rebuilt:
        raise ValueError("stored frozen-Agent closeout record differs")
    return stored


def read_final_closeout_v1(*, private_root: Path) -> PrfFrozenAgentCapabilityCloseoutV1:
    path = Path(private_root) / FINAL_CLOSEOUT_RELATIVE_V1
    if path.is_symlink() or not path.is_file():
        raise ValueError("final capability-closeout record is missing or unsafe")
    return PrfFrozenAgentCapabilityCloseoutV1.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def assert_prf_live_execution_open_v1(*, private_root: Path) -> None:
    """Fail before any Provider or Docker path once the final record exists."""

    path = Path(private_root) / FINAL_CLOSEOUT_RELATIVE_V1
    if not path.exists() and not path.is_symlink():
        return
    try:
        read_final_closeout_v1(private_root=private_root)
    except (OSError, ValueError):
        pass
    raise RuntimeError(LIVE_EXECUTION_CLOSED_TERMINAL_V1)


__all__ = (
    "AD_CPU_ATTEMPT_ID_V1",
    "AD_CPU_CODE_HEAD_V1",
    "AD_FAILURE_RELATIVE_V1",
    "AMENDMENT4_RAW_SHA256_V1",
    "AMENDMENT4_VERSION_V1",
    "DECISION_ID_V1",
    "FINAL_CLOSEOUT_RELATIVE_V1",
    "FINAL_CLOSEOUT_TERMINAL_V1",
    "LIVE_EXECUTION_CLOSED_TERMINAL_V1",
    "AdCpuPlannerProtocolFailureV1",
    "PrfFrozenAgentCapabilityCloseoutV1",
    "assert_prf_live_execution_open_v1",
    "build_prf_frozen_agent_capability_closeout_v1",
    "read_final_closeout_v1",
    "verify_ad_cpu_planner_protocol_failure_v1",
    "verify_final_capability_closeout_v1",
    "write_final_capability_closeout_v1",
)
