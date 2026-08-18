"""Fail-closed four-slot runner for the DTA v2.1 PR-F local portfolio."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import re

from pydantic_core import to_jsonable_python

from ecomsre.dta_v2.v21.agent import AgentRunTerminalV21
from ecomsre.dta_v2.v21.live_contracts import (
    LIVE_CAMPAIGN_ORDER_V21,
    LiveAttemptClosureV21,
    LiveBaselineEvidenceV21,
    LiveCampaignClosureV21,
    LiveDemoConfigV21,
    LiveEnvironmentAdmissionV21,
    LiveFaultImpactEvidenceV21,
    LiveReadinessV21,
    LiveScenarioV21,
    build_service_recovery_result_v21,
)
from ecomsre.dta_v2.v21.live_execution import (
    LiveDispatchIntentV21,
    LiveMasterAuthorizationV21,
    LivePostWriteStateV21,
    LiveReceiptJournalV21,
    LiveStepReceiptV21,
    admit_live_action_v21,
    deny_no_fault_live_action_v21,
    execute_fixed_live_step_v21,
)
from ecomsre.dta_v2.v21.live_owned import OwnedLiveAttemptV21
from ecomsre.dta_v2.v21.live_protocol import (
    AdCpuResourceRecoveryProtocolV1,
    build_ad_cpu_resource_recovery_result,
)
from ecomsre.dta_v2.v21.live_verifiers import verify_live_agent_result_v21
from ecomsre.dta_v2.v21.registry import RunbookRegistryV21
from ecomsre_live_sandbox.contracts import (
    ensure_private_directory,
    verify_private_tree_permissions,
    write_private_json,
)


_ATTEMPT_ID_SUFFIXES = {
    LiveScenarioV21.NO_FAULT: "dta-v21-prf-01-no-fault",
    LiveScenarioV21.AD_CPU_SATURATION: "dta-v21-prf-02-ad-cpu",
    LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE: "dta-v21-prf-03-email-unavailable",
    LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE: (
        "dta-v21-prf-04-product-catalog-unavailable"
    ),
}


def _attempt_id(*, scenario: LiveScenarioV21, code_head: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", code_head) is None:
        raise ValueError("live campaign code HEAD is invalid")
    return f"{_ATTEMPT_ID_SUFFIXES[scenario]}-{code_head[:12]}"


class LiveCampaignBlockedV21(RuntimeError):
    def __init__(self, terminal: str) -> None:
        super().__init__(terminal)
        self.terminal = terminal


class LiveExecutionLeaseV21:
    """Process-scoped exclusive lease for the single fixed local Sandbox."""

    def __init__(self, prf_private_root: Path) -> None:
        self.path = prf_private_root / "execution.lock"
        self._descriptor: int | None = None

    def __enter__(self) -> LiveExecutionLeaseV21:
        ensure_private_directory(self.path.parent)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(descriptor)
            raise LiveCampaignBlockedV21("BLOCKED_DTA_V21_PRF_SAFETY") from error
        self._descriptor = descriptor
        return self

    def assert_exclusive(self) -> None:
        if self._descriptor is None:
            raise RuntimeError("live execution lease is not held")

    def __exit__(self, *_errors: object) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


class PrivateLiveReceiptJournalV21(LiveReceiptJournalV21):
    def __init__(self, attempt_root: Path) -> None:
        self.path = attempt_root / "step-receipt.json"
        self.intent_path = attempt_root / "step-dispatch-intent.json"
        self.postcondition_path = attempt_root / "post-write-state.json"

    def record_intent(self, intent: LiveDispatchIntentV21) -> None:
        write_private_json(self.intent_path, intent, create_once=True)

    def record_postcondition(self, state: LivePostWriteStateV21) -> None:
        write_private_json(self.postcondition_path, state, create_once=True)

    def append(self, receipt: LiveStepReceiptV21) -> None:
        write_private_json(self.path, receipt, create_once=True)


def _safe_failure_terminal(
    *, scenario: LiveScenarioV21, stage: str, provider_failed: bool
) -> str:
    if provider_failed:
        return "BLOCKED_DTA_V21_PRF_SAFETY"
    if scenario is LiveScenarioV21.AD_CPU_SATURATION and stage == "RECOVERY":
        return "BLOCKED_DTA_V21_AD_RESOURCE_RECOVERY"
    if scenario is LiveScenarioV21.AD_CPU_SATURATION and stage == "GUARDRAIL":
        return "BLOCKED_DTA_V21_AD_BUSINESS_NON_REGRESSION"
    return "BLOCKED_DTA_V21_PRF_SAFETY"


def _build_attempt_closure(
    *,
    scenario: LiveScenarioV21,
    attempt_id: str,
    run_id: str,
    planner_identity_sha256: str,
    readiness_sha256: str,
    environment_admission_sha256: str,
    baseline_evidence_sha256: str,
    fault_impact_sha256: str,
    agent_result_sha256: str,
    provider_attempted_calls: int,
    operational_admission_sha256: str,
    run_authorization_sha256: str | None,
    receipt_sha256: str | None,
    recovery_result_sha256: str | None,
    cleanup: dict[str, object],
) -> LiveAttemptClosureV21:
    terminal = {
        LiveScenarioV21.NO_FAULT: "NO_FAULT_ZERO_WRITE_PASS",
        LiveScenarioV21.AD_CPU_SATURATION: "AD_CPU_RESOURCE_RECOVERY_PASS",
        LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE: (
            "SERVICE_AVAILABILITY_RECOVERY_PASS"
        ),
        LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE: (
            "SERVICE_AVAILABILITY_RECOVERY_PASS"
        ),
    }[scenario]
    positive = scenario is not LiveScenarioV21.NO_FAULT
    if cleanup != {
        "baseline_restored": True,
        "owned_containers": 0,
        "owned_networks": 0,
        "owned_volumes": 0,
        "non_owned_resources_changed": False,
        "verdict": "CLEAN",
    }:
        raise ValueError("live cleanup result is not the exact CLEAN terminal")
    payload: dict[str, object] = {
        "schema_version": "dta-v21.live-attempt-closure.v1",
        "scenario": scenario,
        "attempt_id": attempt_id,
        "run_id": run_id,
        "status": "PASS",
        "terminal": terminal,
        "planner_identity_sha256": planner_identity_sha256,
        "readiness_sha256": readiness_sha256,
        "environment_admission_sha256": environment_admission_sha256,
        "baseline_evidence_sha256": baseline_evidence_sha256,
        "fault_impact_sha256": fault_impact_sha256,
        "agent_result_sha256": agent_result_sha256,
        "operational_admission_sha256": operational_admission_sha256,
        "run_authorization_sha256": run_authorization_sha256,
        "fault_operation_count": 1 if positive else 0,
        "forward_step_count": 1 if positive else 0,
        "step_receipt_sha256": receipt_sha256,
        "recovery_result_sha256": recovery_result_sha256,
        "baseline_state_digest_restored": True,
        "cleanup_verdict": "CLEAN",
        "owned_containers_remaining": 0,
        "owned_networks_remaining": 0,
        "owned_volumes_remaining": 0,
        "non_owned_changes": 0,
        "unsafe_proposal_attempts": 0,
        "arbitrary_shell_attempts": 0,
        "provider_attempted_calls": provider_attempted_calls,
    }
    from ecomsre.dta_v2.v21.contracts import semantic_sha256

    return LiveAttemptClosureV21.model_validate(
        {**payload, "closure_sha256": semantic_sha256(to_jsonable_python(payload))}
    )


def run_owned_live_attempt_v21(
    *,
    repository_root: Path,
    prf_private_root: Path,
    provider_env_path: Path,
    config: LiveDemoConfigV21,
    scenario: LiveScenarioV21,
    registry: RunbookRegistryV21,
    protocol: AdCpuResourceRecoveryProtocolV1,
    master_authorization: LiveMasterAuthorizationV21,
    readiness: LiveReadinessV21,
    code_head: str,
    execution_lease: LiveExecutionLeaseV21,
) -> LiveAttemptClosureV21:
    """Run one exact slot and always attempt idempotent restoration and cleanup."""

    spec = config.require_scenario(scenario)
    execution_lease.assert_exclusive()
    attempt_id = _attempt_id(scenario=scenario, code_head=code_head)
    attempts_root = prf_private_root / "attempts"
    ensure_private_directory(attempts_root)
    attempt_root = prf_private_root / "attempts" / attempt_id
    try:
        attempt_root.mkdir(mode=0o700, parents=False, exist_ok=False)
    except FileExistsError:
        raise LiveCampaignBlockedV21("BLOCKED_DTA_V21_PRF_SAFETY")
    except OSError as error:
        raise LiveCampaignBlockedV21("BLOCKED_DTA_V21_PRF_SAFETY") from error
    claim = {
        "schema_version": "dta-v21.live-attempt-claim.v1",
        "attempt_id": attempt_id,
        "scenario": scenario.value,
        "ordinal": LIVE_CAMPAIGN_ORDER_V21.index(scenario) + 1,
        "code_head": code_head,
        "master_authorization_sha256": master_authorization.authorization_sha256,
        "protocol_sha256": protocol.protocol_sha256,
        "live_config_sha256": config.config_sha256,
        "readiness_sha256": readiness.readiness_sha256,
    }
    write_private_json(attempt_root / "attempt-claim.json", claim, create_once=True)
    owned: OwnedLiveAttemptV21 | None = None
    stage = "ENVIRONMENT"
    start_requested = False
    provider_failed = False
    baseline_restored = False
    cleanup: dict[str, object] | None = None
    agent_result_sha256: str | None = None
    receipt: LiveStepReceiptV21 | None = None
    recovery_result_sha256: str | None = None
    operational_admission_sha256: str | None = None
    run_authorization_sha256: str | None = None
    environment_admission: LiveEnvironmentAdmissionV21 | None = None
    baseline: LiveBaselineEvidenceV21 | None = None
    fault_impact: LiveFaultImpactEvidenceV21 | None = None
    restoration_operation_failed = False
    try:
        owned = OwnedLiveAttemptV21(
            repository_root=repository_root,
            private_root=attempt_root / "owned-sandbox",
            attempt_id=attempt_id,
            config=config,
            scenario=spec,
            registry=registry,
            protocol=protocol,
            provider_env_path=provider_env_path,
            concurrency_guard=execution_lease.assert_exclusive,
        )
        owned.admit_environment()
        stage = "START"
        start_requested = True
        owned.start()
        stage = "READY"
        owned.wait_ready()
        environment_admission = owned.environment_admission(readiness=readiness)
        write_private_json(
            attempt_root / "environment-admission.json",
            environment_admission,
            create_once=True,
        )
        stage = "BASELINE"
        baseline = owned.capture_baseline(environment_admission=environment_admission)
        write_private_json(
            attempt_root / "baseline-evidence.json", baseline, create_once=True
        )
        stage = "FAULT"
        owned.inject_fault()
        fault_impact = owned.verify_fault_impact(
            environment_admission=environment_admission,
            baseline=baseline,
        )
        write_private_json(
            attempt_root / "fault-impact.json", fault_impact, create_once=True
        )
        stage = "AGENT"
        result = owned.run_agent()
        agent_result_sha256 = result.result_sha256
        write_private_json(attempt_root / "agent-result.json", result, create_once=True)
        if result.terminal is AgentRunTerminalV21.FAILED:
            provider_failed = result.failure_code is not None and (
                "PROVIDER" in result.failure_code.value
            )
        verified = verify_live_agent_result_v21(
            result=result,
            scenario=spec,
            registry=registry,
            planner_identity_sha256=config.planner_identity_sha256,
        )
        if scenario is LiveScenarioV21.NO_FAULT:
            stage = "ADMISSION"
            no_write_admission = deny_no_fault_live_action_v21(
                agent_result=verified,
                registry=registry,
                attempt_id=attempt_id,
                master_authorization=master_authorization,
            )
            operational_admission_sha256 = no_write_admission.admission_sha256
            write_private_json(
                attempt_root / "operational-admission.json",
                no_write_admission,
                create_once=True,
            )
            stage = "RESTORE"
            baseline_restored = owned.restore_baseline_idempotently()
            if not baseline_restored:
                raise RuntimeError("no-fault baseline restoration failed")
            owned.assert_no_unrelated_owned_drift()
        else:
            assert verified.diagnosis is not None
            assert verified.resolved_evidence is not None
            assert verified.candidate_set is not None
            assert verified.action_proposal is not None
            stage = "ADMISSION"
            current_state = owned.current_state()
            write_private_json(
                attempt_root / "current-state.json", current_state, create_once=True
            )
            admission, authorization = admit_live_action_v21(
                scenario=scenario,
                agent_result=verified,
                registry=registry,
                current_state=current_state,
                master_authorization=master_authorization,
                issued_at=datetime.now(timezone.utc),
            )
            operational_admission_sha256 = admission.admission_sha256
            run_authorization_sha256 = authorization.authorization_sha256
            write_private_json(
                attempt_root / "operational-admission.json",
                admission,
                create_once=True,
            )
            write_private_json(
                attempt_root / "run-authorization.json",
                authorization,
                create_once=True,
            )
            stage = "EXECUTION"
            receipt = execute_fixed_live_step_v21(
                proposal=verified.action_proposal,
                current_state=current_state,
                admission=admission,
                authorization=authorization,
                controls=owned,
                receipt_journal=PrivateLiveReceiptJournalV21(attempt_root),
                observed_at=datetime.now(timezone.utc),
            )
            stage = "RECOVERY"
            if scenario is LiveScenarioV21.AD_CPU_SATURATION:
                ad_windows, guardrails = owned.capture_ad_recovery_windows()
                if any(
                    item.business_impact_observed
                    or not item.service_health_passed
                    or not item.endpoint_reachable
                    for item in guardrails
                ):
                    stage = "GUARDRAIL"
                    raise RuntimeError("Ad business non-regression guardrail failed")
                stage = "RESTORE"
                baseline_restored = owned.restore_baseline_idempotently()
                if not baseline_restored:
                    raise RuntimeError("Ad baseline restoration failed")
                owned.assert_no_unrelated_owned_drift()
                ad_recovery = build_ad_cpu_resource_recovery_result(
                    protocol=protocol,
                    windows=ad_windows,
                    guardrails=guardrails,
                    baseline_flag_restored=True,
                    baseline_state_digest_restored=True,
                    non_owned_changes=0,
                    unsafe_proposal_attempts=0,
                    arbitrary_shell_attempts=0,
                )
                recovery_result_sha256 = ad_recovery.result_sha256
                write_private_json(
                    attempt_root / "recovery-result.json",
                    ad_recovery,
                    create_once=True,
                )
            else:
                service_windows = owned.capture_service_recovery_windows()
                stage = "RESTORE"
                baseline_restored = owned.restore_baseline_idempotently()
                if not baseline_restored:
                    raise RuntimeError("service baseline restoration failed")
                owned.assert_no_unrelated_owned_drift()
                service_recovery = build_service_recovery_result_v21(
                    windows=service_windows,
                    same_owned_identity=True,
                    baseline_state_digest_restored=True,
                    non_owned_changes=0,
                    unsafe_proposal_attempts=0,
                    arbitrary_shell_attempts=0,
                )
                recovery_result_sha256 = service_recovery.result_sha256
                write_private_json(
                    attempt_root / "recovery-result.json",
                    service_recovery,
                    create_once=True,
                )
    except Exception as error:
        if owned is None:
            cleanup = {
                "schema_version": "dta-v21.live-cleanup-terminal.v1",
                "disposition": "NO_MUTATION_ATTEMPTED_RESOURCE_STATE_UNMEASURED",
                "baseline_restored": False,
                "owned_containers": None,
                "owned_networks": None,
                "owned_volumes": None,
                "non_owned_resources_changed": None,
                "verdict": "BLOCKED",
            }
        elif not start_requested:
            try:
                cleanup = owned.cleanup_not_started()
                baseline_restored = (
                    cleanup.get("baseline_restored") is True
                    and cleanup.get("verdict") == "CLEAN"
                )
            except Exception:
                cleanup = {
                    "schema_version": "dta-v21.live-cleanup-terminal.v1",
                    "disposition": "NOT_STARTED_CLEANUP_PROOF_FAILED",
                    "baseline_restored": False,
                    "owned_containers": -1,
                    "owned_networks": -1,
                    "owned_volumes": -1,
                    "non_owned_resources_changed": False,
                    "verdict": "BLOCKED",
                }
        if owned is not None and start_requested and not baseline_restored:
            try:
                baseline_restored = owned.restore_baseline_idempotently()
            except Exception:
                restoration_operation_failed = True
                baseline_restored = False
        if owned is not None and start_requested:
            try:
                cleanup = owned.cleanup(baseline_restored=baseline_restored)
            except Exception:
                cleanup = {
                    "schema_version": "dta-v21.live-cleanup-terminal.v1",
                    "disposition": "CLEANUP_OPERATION_FAILED",
                    "baseline_restored": baseline_restored,
                    "owned_containers": None,
                    "owned_networks": None,
                    "owned_volumes": None,
                    "non_owned_resources_changed": None,
                    "verdict": "BLOCKED",
                }
        terminal = _safe_failure_terminal(
            scenario=scenario, stage=stage, provider_failed=provider_failed
        )
        failure = {
            "schema_version": "dta-v21.live-attempt-failure.v1",
            "attempt_id": attempt_id,
            "scenario": scenario.value,
            "stage": stage,
            "terminal": terminal,
            "baseline_restored": baseline_restored,
            "restoration_operation_failed": restoration_operation_failed,
            "cleanup": cleanup,
            "failure_type": type(error).__name__,
            "raw_error_retained": False,
        }
        write_private_json(
            attempt_root / "attempt-terminal.json", failure, create_once=True
        )
        verify_private_tree_permissions(prf_private_root)
        raise LiveCampaignBlockedV21(terminal) from error
    if (
        owned is None
        or not start_requested
        or not baseline_restored
        or agent_result_sha256 is None
        or operational_admission_sha256 is None
        or environment_admission is None
        or baseline is None
        or fault_impact is None
    ):
        raise LiveCampaignBlockedV21("BLOCKED_DTA_V21_PRF_SAFETY")
    try:
        assert owned is not None
        cleanup = owned.cleanup(baseline_restored=True)
        closure = _build_attempt_closure(
            scenario=scenario,
            attempt_id=attempt_id,
            run_id=owned.run_id,
            planner_identity_sha256=config.planner_identity_sha256,
            readiness_sha256=readiness.readiness_sha256,
            environment_admission_sha256=(
                environment_admission.environment_admission_sha256
            ),
            baseline_evidence_sha256=baseline.evidence_sha256,
            fault_impact_sha256=fault_impact.evidence_sha256,
            agent_result_sha256=agent_result_sha256,
            provider_attempted_calls=owned.provider.attempted_calls,
            operational_admission_sha256=operational_admission_sha256,
            run_authorization_sha256=run_authorization_sha256,
            receipt_sha256=(None if receipt is None else receipt.receipt_sha256),
            recovery_result_sha256=recovery_result_sha256,
            cleanup=cleanup,
        )
    except Exception as error:
        if cleanup is None:
            cleanup = {
                "schema_version": "dta-v21.live-cleanup-terminal.v1",
                "disposition": "CLEANUP_OPERATION_FAILED",
                "baseline_restored": baseline_restored,
                "owned_containers": None,
                "owned_networks": None,
                "owned_volumes": None,
                "non_owned_resources_changed": None,
                "verdict": "BLOCKED",
            }
        failure = {
            "schema_version": "dta-v21.live-attempt-failure.v1",
            "attempt_id": attempt_id,
            "scenario": scenario.value,
            "stage": "CLEANUP",
            "terminal": "BLOCKED_DTA_V21_PRF_SAFETY",
            "baseline_restored": baseline_restored,
            "cleanup": cleanup,
            "failure_type": type(error).__name__,
            "raw_error_retained": False,
        }
        write_private_json(
            attempt_root / "attempt-terminal.json", failure, create_once=True
        )
        verify_private_tree_permissions(prf_private_root)
        raise LiveCampaignBlockedV21("BLOCKED_DTA_V21_PRF_SAFETY") from error
    write_private_json(
        attempt_root / "attempt-terminal.json", closure, create_once=True
    )
    verify_private_tree_permissions(prf_private_root)
    return closure


def run_owned_live_campaign_v21(
    *,
    repository_root: Path,
    prf_private_root: Path,
    provider_env_path: Path,
    config: LiveDemoConfigV21,
    registry: RunbookRegistryV21,
    protocol: AdCpuResourceRecoveryProtocolV1,
    master_authorization: LiveMasterAuthorizationV21,
    readiness: LiveReadinessV21,
    code_head: str,
) -> LiveCampaignClosureV21:
    """Execute the exact four slots once, stopping after any failed cleanup."""

    ensure_private_directory(prf_private_root / "attempts")
    write_private_json(
        prf_private_root / "master-authorization.json",
        master_authorization,
        create_once=True,
    )
    if (
        readiness.code_head != code_head
        or readiness.protocol_sha256 != protocol.protocol_sha256
        or readiness.live_config_sha256 != config.config_sha256
        or readiness.planner_identity_sha256 != config.planner_identity_sha256
        or readiness.master_authorization_sha256
        != master_authorization.authorization_sha256
    ):
        raise LiveCampaignBlockedV21("BLOCKED_DTA_V21_PRF_EXACT_HEAD_ACCEPTANCE")
    with LiveExecutionLeaseV21(prf_private_root) as execution_lease:
        attempts = tuple(
            run_owned_live_attempt_v21(
                repository_root=repository_root,
                prf_private_root=prf_private_root,
                provider_env_path=provider_env_path,
                config=config,
                scenario=scenario,
                registry=registry,
                protocol=protocol,
                master_authorization=master_authorization,
                readiness=readiness,
                code_head=code_head,
                execution_lease=execution_lease,
            )
            for scenario in LIVE_CAMPAIGN_ORDER_V21
        )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.live-campaign-closure.v1",
        "terminal": "DTA_V21_PR_F_LIVE_PORTFOLIO_PASS",
        "code_head": code_head,
        "protocol_sha256": protocol.protocol_sha256,
        "live_config_sha256": config.config_sha256,
        "planner_identity_sha256": config.planner_identity_sha256,
        "readiness_sha256": readiness.readiness_sha256,
        "attempts": attempts,
        "unsafe_proposal_attempts": 0,
        "arbitrary_shell_attempts": 0,
        "non_owned_changes": 0,
        "all_baselines_restored": True,
        "all_cleanup_clean": True,
    }
    from ecomsre.dta_v2.v21.contracts import semantic_sha256

    campaign = LiveCampaignClosureV21.model_validate(
        {**payload, "campaign_sha256": semantic_sha256(to_jsonable_python(payload))}
    )
    campaign_root = prf_private_root / "campaigns" / code_head
    ensure_private_directory(campaign_root)
    write_private_json(
        campaign_root / "campaign-closure.json", campaign, create_once=True
    )
    verify_private_tree_permissions(prf_private_root)
    return campaign


__all__ = (
    "LiveCampaignBlockedV21",
    "LiveExecutionLeaseV21",
    "PrivateLiveReceiptJournalV21",
    "run_owned_live_attempt_v21",
    "run_owned_live_campaign_v21",
)
