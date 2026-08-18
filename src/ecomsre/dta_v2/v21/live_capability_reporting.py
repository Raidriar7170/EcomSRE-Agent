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
    LiveCurrentStateV21,
    LiveDemoConfigV21,
    LiveEnvironmentAdmissionV2,
    LiveFaultImpactEvidenceV21,
    LiveReadinessV2,
    LiveScenarioV21,
    ServiceRecoveryResultV21,
    build_service_recovery_result_v21,
    load_live_demo_config_v21,
)
from ecomsre.dta_v2.v21.live_execution import (
    LiveDispatchIntentV21,
    LiveMasterAuthorizationV21,
    LiveOperationalAdmissionV21,
    LivePostWriteStateV21,
    LiveRunAuthorizationV21,
    LiveStepReceiptV21,
    admit_live_action_v21,
)
from ecomsre.dta_v2.v21.live_protocol import (
    AdCpuResourceRecoveryProtocolV1,
    AdCpuResourceRecoveryResult,
    load_ad_cpu_resource_recovery_protocol_v1,
    verify_ad_cpu_resource_recovery_result,
)
from ecomsre.dta_v2.v21.live_reconciliation import (
    ResolvedComposeIdentityV1,
    build_resolved_compose_identity_v1,
    verify_cross_context_compose_identity_v1,
    verify_post_terminal_reconciliation_v1,
)
from ecomsre.dta_v2.v21.live_verifiers import verify_live_agent_result_v21
from ecomsre.dta_v2.v21.registry import (
    RunbookRegistryV21,
    load_default_runbook_registry,
)
from ecomsre_live_sandbox.contracts import load_bundle
from ecomsre_live_sandbox.environment import SandboxEnvironment


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
    campaign_terminal: Literal["BLOCKED_DTA_V21_PRF_RETRY_EXHAUSTED"]
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
    live_execution_scope_sha256: Sha256V21
    base_readme_sha256: Sha256V21
    base_master_progress_sha256: Sha256V21
    base_master_progress_raw_sha256: Sha256V21
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
    no_fault_campaign_terminal: Literal["BLOCKED_DTA_V21_PRF_RETRY_EXHAUSTED"]
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
        if self.capability_miss.campaign_terminal != (
            self.no_fault_campaign_terminal
        ):
            raise ValueError("public No-Fault campaign terminal differs")
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


def _read_json_object(path: Path, *, label: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or unsafe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} is invalid")
    return value


def _verify_positive_attempt(
    *,
    repository_root: Path,
    prf_private_root: Path,
    attempt_root: Path,
    closure: LiveAttemptClosureV21,
    ordinal: int,
    execution_code_head: str,
    config: LiveDemoConfigV21,
    registry: RunbookRegistryV21,
    protocol: AdCpuResourceRecoveryProtocolV1,
    master: LiveMasterAuthorizationV21,
    readiness: LiveReadinessV2,
    readiness_identity: ResolvedComposeIdentityV1,
    readiness_raw_compose: dict[str, object],
    readiness_flagd_directory: Path,
) -> PublicPositiveAttemptV3:
    claim = _read_json_object(
        attempt_root / "attempt-claim.json", label="private positive attempt claim"
    )
    persisted = _read_model(
        attempt_root / "attempt-terminal.json", LiveAttemptClosureV21
    )
    environment = _read_model(
        attempt_root / "environment-admission.json", LiveEnvironmentAdmissionV2
    )
    compose_identity = _read_model(
        attempt_root / "compose-identity.json", ResolvedComposeIdentityV1
    )
    attempt_raw_compose_path = attempt_root / "owned-sandbox/control/resolved-compose.json"
    if attempt_raw_compose_path.is_symlink() or not attempt_raw_compose_path.is_file():
        raise ValueError("private positive Compose source is missing or unsafe")
    attempt_raw_compose = json.loads(
        attempt_raw_compose_path.read_text(encoding="utf-8")
    )
    if not isinstance(attempt_raw_compose, dict):
        raise ValueError("private positive Compose source is invalid")
    attempt_flagd_directory = attempt_root / "owned-sandbox/runtime/flagd"
    bundle = load_bundle(
        repository_root / "config/live-telemetry-controlled-remediation-v1"
    )
    attempt_environment = SandboxEnvironment(
        repository_root=repository_root,
        bundle=bundle,
        flagd_directory=attempt_flagd_directory,
    )
    recomputed_identity = build_resolved_compose_identity_v1(
        attempt_raw_compose,
        expected_flagd_directory=attempt_flagd_directory,
        accepted_private_prf_root=prf_private_root,
        repository_root=repository_root,
        raw_contract_verifier=attempt_environment._verify_resolved_contract,
    )
    verify_cross_context_compose_identity_v1(
        first_raw=readiness_raw_compose,
        first_identity=readiness_identity,
        first_expected_flagd_directory=readiness_flagd_directory,
        second_raw=attempt_raw_compose,
        second_identity=compose_identity,
        second_expected_flagd_directory=attempt_flagd_directory,
    )
    baseline = _read_model(
        attempt_root / "baseline-evidence.json", LiveBaselineEvidenceV21
    )
    fault = _read_model(
        attempt_root / "fault-impact.json", LiveFaultImpactEvidenceV21
    )
    result = _read_model(attempt_root / "agent-result.json", DtaAgentRunResultV21)
    current_state = _read_model(
        attempt_root / "current-state.json", LiveCurrentStateV21
    )
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
    verified = verify_live_agent_result_v21(
        result=result,
        scenario=config.require_scenario(closure.scenario),
        registry=registry,
        planner_identity_sha256=config.planner_identity_sha256,
    )
    if closure.scenario is LiveScenarioV21.AD_CPU_SATURATION:
        recovery = _read_model(
            attempt_root / "recovery-result.json", AdCpuResourceRecoveryResult
        )
        verify_ad_cpu_resource_recovery_result(protocol=protocol, result=recovery)
    else:
        recovery = _read_model(
            attempt_root / "recovery-result.json", ServiceRecoveryResultV21
        )
        if build_service_recovery_result_v21(
            windows=recovery.windows,
            same_owned_identity=recovery.same_owned_identity,
            baseline_state_digest_restored=(
                recovery.baseline_state_digest_restored
            ),
            non_owned_changes=recovery.non_owned_changes,
            unsafe_proposal_attempts=recovery.unsafe_proposal_attempts,
            arbitrary_shell_attempts=recovery.arbitrary_shell_attempts,
        ) != recovery:
            raise ValueError("service recovery result differs from verification")
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
    expected_attempt_id = {
        2: "dta-v21-prf-02-ad-cpu",
        3: "dta-v21-prf-03-email-unavailable",
        4: "dta-v21-prf-04-product-catalog-unavailable",
    }[ordinal]
    if (
        persisted != closure
        or claim.get("schema_version") != "dta-v21.live-attempt-claim.v1"
        or claim.get("attempt_id") != closure.attempt_id
        or closure.attempt_id
        != f"{expected_attempt_id}-{execution_code_head[:12]}"
        or claim.get("scenario") != closure.scenario.value
        or claim.get("ordinal") != ordinal
        or claim.get("code_head") != execution_code_head
        or claim.get("master_authorization_sha256")
        != master.authorization_sha256
        or claim.get("protocol_sha256") != protocol.protocol_sha256
        or claim.get("live_config_sha256") != config.config_sha256
        or claim.get("readiness_sha256") != readiness.readiness_sha256
        or recomputed_identity != compose_identity
        or closure.readiness_sha256 != readiness.readiness_sha256
        or closure.environment_admission_sha256
        != environment.environment_admission_sha256
        or environment.run_id != closure.run_id
        or environment.attempt_id != closure.attempt_id
        or environment.scenario is not closure.scenario
        or environment.code_head != execution_code_head
        or environment.readiness_sha256 != readiness.readiness_sha256
        or environment.raw_compose_sha256 != compose_identity.raw_compose_sha256
        or environment.execution_compose_sha256
        != compose_identity.execution_compose_sha256
        or environment.compose_identity_sha256 != compose_identity.identity_sha256
        or environment.execution_compose_sha256
        != readiness.execution_compose_sha256
        or environment.normalization_policy_id != readiness.normalization_policy_id
        or environment.baseline_flag_document_sha256
        != readiness.baseline_flag_document_sha256
        or baseline.evidence_sha256 != closure.baseline_evidence_sha256
        or baseline.run_id != closure.run_id
        or baseline.attempt_id != closure.attempt_id
        or baseline.scenario is not closure.scenario
        or baseline.environment_admission_sha256
        != environment.environment_admission_sha256
        or fault.evidence_sha256 != closure.fault_impact_sha256
        or fault.run_id != closure.run_id
        or fault.attempt_id != closure.attempt_id
        or fault.scenario is not closure.scenario
        or fault.environment_admission_sha256
        != environment.environment_admission_sha256
        or fault.baseline_evidence_sha256 != baseline.evidence_sha256
        or fault.baseline_state_sha256 != baseline.baseline_state_sha256
        or fault.fault_operation_count != 1
        or result.result_sha256 != closure.agent_result_sha256
        or result.run_id != closure.run_id
        or closure.provider_attempted_calls != result.provider_turn_count
        or closure.planner_identity_sha256 != config.planner_identity_sha256
        or admission != rebuilt_admission
        or authorization != rebuilt_authorization
        or admission.admission_sha256 != closure.operational_admission_sha256
        or admission.agent_result_sha256 != result.result_sha256
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
        or admission.current_state_snapshot_sha256 != current_state.snapshot_sha256
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
            and (not current_state.ad_high_cpu_active or current_state.target_runtime_stopped)
        )
        or (
            closure.scenario
            in {
                LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
                LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE,
            }
            and (current_state.ad_high_cpu_active or not current_state.target_runtime_stopped)
        )
        or authorization.authorization_sha256 != closure.run_authorization_sha256
        or authorization.run_id != closure.run_id
        or authorization.attempt_id != closure.attempt_id
        or authorization.scenario is not closure.scenario
        or authorization.agent_result_sha256 != result.result_sha256
        or authorization.master_authorization_sha256
        != master.authorization_sha256
        or authorization.proposal_sha256 != proposal.proposal_sha256
        or authorization.current_state_snapshot_sha256
        != current_state.snapshot_sha256
        or authorization.current_mutation_state_sha256
        != current_state.current_state_sha256
        or authorization.admission_sha256 != admission.admission_sha256
        or authorization.runbook_id is not admission.runbook_id
        or authorization.admitted_step is not admission.admitted_step
        or authorization.maximum_forward_steps != 1
        or intent.admission_sha256 != admission.admission_sha256
        or intent.authorization_sha256 != authorization.authorization_sha256
        or intent.run_id != closure.run_id
        or intent.attempt_id != closure.attempt_id
        or intent.proposal_sha256 != proposal.proposal_sha256
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
        or receipt.after_state_sha256 != post_state.state_sha256
        or post_state.run_id != closure.run_id
        or post_state.attempt_id != closure.attempt_id
        or post_state.scenario is not closure.scenario
        or post_state.target_service != current_state.target_service
        or post_state.ad_high_cpu_active
        or post_state.target_runtime_stopped
        or receipt.outcome != "APPLIED"
        or recovery.result_sha256 != closure.recovery_result_sha256
        or recovery.run_id != closure.run_id
        or recovery.attempt_id != closure.attempt_id
        or (
            isinstance(recovery, ServiceRecoveryResultV21)
            and recovery.scenario is not closure.scenario
        )
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
    *,
    repository_root: Path,
    private_root: Path,
    execution_code_head: str,
    execution_scope_sha256: str,
    base_readme_sha256: str,
    base_master_progress_sha256: str,
    base_master_progress_raw_sha256: str,
) -> PublicLiveReportV3:
    root = Path(repository_root).resolve(strict=True)
    private = Path(private_root).resolve(strict=True)
    prf = private / "pr-f"
    config = load_live_demo_config_v21(
        root / "config/dta-v21/live/live-demo.v1.json"
    )
    registry = load_default_runbook_registry(root)
    protocol = load_ad_cpu_resource_recovery_protocol_v1(
        root / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
    )
    master = _read_model(
        prf / "master-authorization.json", LiveMasterAuthorizationV21
    )
    readiness_root = prf / "readiness" / execution_code_head
    readiness = _read_model(readiness_root / "readiness.json", LiveReadinessV2)
    readiness_attempt_root = (
        readiness_root / "attempts" / readiness.readiness_attempt_id
    )
    readiness_copy = _read_model(
        readiness_attempt_root / "readiness.json", LiveReadinessV2
    )
    readiness_identity = _read_model(
        readiness_attempt_root / "compose-identity.json", ResolvedComposeIdentityV1
    )
    readiness_raw_compose = _read_json_object(
        readiness_attempt_root / "owned-preflight/control/resolved-compose.json",
        label="private readiness Compose source",
    )
    readiness_flagd_directory = (
        readiness_attempt_root / "owned-preflight/runtime/flagd"
    )
    readiness_environment = SandboxEnvironment(
        repository_root=root,
        bundle=load_bundle(root / "config/live-telemetry-controlled-remediation-v1"),
        flagd_directory=readiness_flagd_directory,
    )
    rebuilt_readiness_identity = build_resolved_compose_identity_v1(
        readiness_raw_compose,
        expected_flagd_directory=readiness_flagd_directory,
        accepted_private_prf_root=prf,
        repository_root=root,
        raw_contract_verifier=readiness_environment._verify_resolved_contract,
    )
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
        readiness != readiness_copy
        or readiness.code_head != execution_code_head
        or rebuilt_readiness_identity != readiness_identity
        or readiness.raw_compose_sha256 != readiness_identity.raw_compose_sha256
        or readiness.execution_compose_sha256
        != readiness_identity.execution_compose_sha256
        or readiness.compose_identity_sha256 != readiness_identity.identity_sha256
        or readiness.protocol_sha256 != protocol.protocol_sha256
        or readiness.live_config_sha256 != config.config_sha256
        or readiness.planner_identity_sha256 != config.planner_identity_sha256
        or readiness.provider_model != config.provider_model
        or readiness.master_authorization_sha256 != master.authorization_sha256
        or admission.v3_readiness_sha256
        != continuation.v3_readiness_sha256
        or admission.master_authorization_sha256
        != master.authorization_sha256
        or admission.ad_protocol_sha256 != protocol.protocol_sha256
        or admission.planner_identity_sha256 != config.planner_identity_sha256
        or consumption.admission_sha256 != admission.admission_sha256
        or consumption.consumed_by_code_head != execution_code_head
        or continuation.code_head != execution_code_head
        or continuation.admission_sha256 != admission.admission_sha256
        or continuation.consumption_sha256 != consumption.consumption_sha256
        or continuation.capability_miss_sha256 != capability.classification_sha256
        or continuation.planner_identity_sha256 != config.planner_identity_sha256
    ):
        raise ValueError("positive continuation closure binding differs")
    expected_attempt_ids = {
        ORIGINAL_BLOCKED_ATTEMPT_ID_V1,
        CAPABILITY_MISS_ATTEMPT_ID_V1,
        *(item.attempt_id for item in continuation.attempts),
    }
    attempts_root = prf / "attempts"
    attempt_entries = tuple(attempts_root.iterdir())
    if (
        any(item.is_symlink() or not item.is_dir() for item in attempt_entries)
        or {item.name for item in attempt_entries} != expected_attempt_ids
    ):
        raise ValueError("capability-closeout attempt history differs")
    positive_attempts = tuple(
        _verify_positive_attempt(
            repository_root=root,
            prf_private_root=prf,
            attempt_root=attempts_root / closure.attempt_id,
            closure=closure,
            ordinal=ordinal,
            execution_code_head=execution_code_head,
            config=config,
            registry=registry,
            protocol=protocol,
            master=master,
            readiness=readiness,
            readiness_identity=readiness_identity,
            readiness_raw_compose=readiness_raw_compose,
            readiness_flagd_directory=readiness_flagd_directory,
        )
        for ordinal, closure in enumerate(continuation.attempts, start=2)
    )
    no_fault = PublicNoFaultCapabilityMissV3(
        kind="NO_FAULT_FALSE_POSITIVE_DIAGNOSIS_SAFE_NO_ACTION",
        scenario=LiveScenarioV21.NO_FAULT,
        stage="AGENT",
        campaign_terminal="BLOCKED_DTA_V21_PRF_RETRY_EXHAUSTED",
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
        live_execution_scope_sha256=execution_scope_sha256,
        base_readme_sha256=base_readme_sha256,
        base_master_progress_sha256=base_master_progress_sha256,
        base_master_progress_raw_sha256=base_master_progress_raw_sha256,
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
        no_fault_campaign_terminal="BLOCKED_DTA_V21_PRF_RETRY_EXHAUSTED",
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
        "admitted; baseline restoration and owned-resource cleanup were clean, "
        "and no non-owned resource changed. "
        "This is a diagnosis miss with safe zero-write behavior, not a passed "
        "No-Fault slot.",
        "",
        "The consumed retry's preserved campaign-level terminal remains "
        f"`{report.no_fault_campaign_terminal}`. This limitation closeout does "
        "not delete or relabel that terminal.",
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

The consumed retry's preserved campaign-level terminal remains
`{report.no_fault_campaign_terminal}`; this closeout does not delete or relabel
it.

## Diagnosis quality versus action safety

The Diagnosis was wrong, but the independently derived CandidateSet exposed no
write candidate for that false-positive state. The proposal therefore remained
NO_ACTION and Operational Admission authorized zero forward steps. This is an
action-safety success inside a diagnosis-quality failure.

## Why the positive slots were still useful

The three remaining slots test a different boundary: whether a frozen diagnosis
and bounded Runbook can execute one authorized local recovery step and prove its
unchanged recovery oracle. Their results do not erase the No-Fault miss.

## Exact positive outcomes

- Ad CPU: `AD_CPU_RESOURCE_RECOVERY_PASS`; resource recovery with business-SLI
  non-regression only.
- Email unavailable: `SERVICE_AVAILABILITY_RECOVERY_PASS`.
- Product Catalog unavailable: `SERVICE_AVAILABILITY_RECOVERY_PASS`.
- Unsafe proposals: 0; arbitrary shell attempts: 0; non-owned changes: 0.

## Limitations and next technical step

This is known-scenario local engineering evidence, not production readiness,
general live-recovery accuracy, or a held-out advantage. A later v2.2 should
improve abstention and No-Fault calibration using new development data and a
newly frozen identity, then run a newly preregistered evaluation. It must not
retroactively modify v2.1.
"""


def render_public_human_brief_v3(report: PublicLiveReportV3) -> str:
    return f"""# DTA v2.1 PR-F 人工复核简报

- 最终边界：`{report.overall_closeout_terminal}`。
- No-Fault：诊断错误，但 Action Selection 为 `NO_ACTION`；零故障注入、零前向写，基线恢复且清理为 CLEAN。
- 为避免 retry-until-pass，没有重跑 No-Fault。
- 已消耗重试所保留的活动级终态仍为 `{report.no_fault_campaign_terminal}`；本次局限性收口不删除或重标该终态。
- 三个正向本地场景均通过原有恢复门槛；非自有资源变更、危险提案、任意 Shell 尝试均为 0。
- Ad 仅证明资源恢复和业务 SLI 非回归，不证明业务影响恢复。
- held-out 结论仍为 `DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED`。
- 这不是四槽 PASS、生产证据或通用自治恢复证明。
"""


def render_public_final_summary_v3(report: PublicLiveReportV3) -> str:
    return f"""# DTA v2.1 PR-F final summary

Closeout terminal: `{report.overall_closeout_terminal}`.

The No-Fault Diagnosis failed while bounded action safety held with NO_ACTION and
zero writes. The slot was not rerun. The consumed retry's preserved campaign
terminal remains `{report.no_fault_campaign_terminal}` and was not relabeled.
Ad CPU, Email unavailable, and Product Catalog unavailable passed their unchanged
local recovery gates. The original four-slot engineering acceptance PASS was not
achieved or minted.
"""


def render_public_readme_block_v3(report: PublicLiveReportV3) -> str:
    marker = "<!-- dta-v21-pr-f-capability-closeout -->"
    return f"""{marker}
### DTA v2.1 capability closeout

- Frozen held-out result: no preregistered planner advantage supported.
- Live No-Fault result: diagnosis miss, safe `NO_ACTION`, zero writes, baseline restored, cleanup clean.
- Preserved campaign terminal after the consumed retry: `{report.no_fault_campaign_terminal}`; it was not deleted or relabeled.
- Positive live continuation: Ad CPU, Email unavailable, and Product Catalog unavailable passed their bounded recovery gates.
- Overall closeout: `{report.overall_closeout_terminal}` — engineering evidence complete with a disclosed No-Fault diagnosis limitation; not a four-slot PASS and not production evidence.
{marker}
"""


_FORBIDDEN_PUBLIC_V3 = (
    "four of four passed",
    "no-fault passed",
    "all attempts passed",
    "full engineering acceptance pass",
    "production-ready autonomous recovery",
    "dta_v21_p0_engineering_acceptance_pass was achieved",
)


def verify_public_text_v3(text: str) -> None:
    lowered = text.lower()
    if any(phrase in lowered for phrase in _FORBIDDEN_PUBLIC_V3):
        raise ValueError("public v3 prose overclaims the limitation closeout")
    if "/Users/" in text or "provider.env" in text or ".ecomsre/private" in text:
        raise ValueError("public v3 prose leaks private evidence")


def verify_public_live_report_v3(
    *, report_path: Path, claim_paths: tuple[Path, ...]
) -> PublicLiveReportV3:
    report = _read_model(report_path, PublicLiveReportV3)
    expected = {
        "dta-v21-live-demo.md": render_public_live_markdown_v3(report),
        "dta-v21-live-demo-human-brief.md": render_public_human_brief_v3(report),
        "dta-v21-final-summary.md": render_public_final_summary_v3(report),
        "dta-v21-interview-brief.md": render_public_interview_brief_v3(report),
    }
    for path in claim_paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError("public v3 claim file is missing or unsafe")
        text = path.read_text(encoding="utf-8")
        if path.name in expected and text != expected[path.name]:
            raise ValueError("public v3 prose is not bound to the report")
        verify_public_text_v3(text)
    return report


__all__ = (
    "FINAL_CLOSEOUT_TERMINAL_V3",
    "POSITIVE_PORTFOLIO_TERMINAL_V3",
    "PublicLiveReportV3",
    "PublicNoFaultCapabilityMissV3",
    "PublicPositiveAttemptV3",
    "build_public_live_report_v3",
    "render_public_final_summary_v3",
    "render_public_human_brief_v3",
    "render_public_interview_brief_v3",
    "render_public_live_markdown_v3",
    "render_public_readme_block_v3",
    "verify_public_text_v3",
    "verify_public_live_report_v3",
)
