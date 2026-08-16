"""Dependency-injected PR-F lifecycle runner; live effects stay behind one protocol."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from ecomsre.dta_v2.agent import AgentRunTerminal, DtaAgentRunResult
from ecomsre.dta_v2.authorization import (
    MasterAuthorizationRecord,
    derive_attempt_authorization,
)
from ecomsre.dta_v2.contracts import ActionDisposition, semantic_sha256
from ecomsre.dta_v2.live_contracts import (
    BaselineEvidence,
    CleanupTerminal,
    ForwardExecution,
    ForwardExecutionTerminal,
    LiveAttemptClosure,
    LiveAttemptCounters,
    LiveAttemptEvent,
    LiveAttemptMode,
    LiveAttemptStage,
    LiveAttemptTerminal,
    LiveDemoConfig,
    LiveFailureCode,
    LiveScenario,
    LiveScenarioSpec,
    LiveStageStatus,
    PreLiveFreeze,
    RecoveryWindow,
    TerminalNonwriteAdmission,
    build_live_attempt_event,
)
from ecomsre.dta_v2.live_execution import (
    LiveControls,
    PartialExecutionError,
    ReceiptPersistenceError,
    execute_live_forward_steps,
)
from ecomsre.dta_v2.live_capability import OwnedLiveExecutionGrant
from ecomsre.dta_v2.live_reporting import PrivateLiveAttemptJournal
from ecomsre.dta_v2.live_state import require_trusted_live_current_state
from ecomsre.dta_v2.live_verifiers import finalize_live_execution
from ecomsre.dta_v2.operational_contracts import (
    AdmissionReasonCode,
    AdmissionVerdict,
    CurrentStateSnapshot,
    ExecutionTerminal,
    ExecutionTransaction,
    OperationalAdmission,
)
from ecomsre.dta_v2.policy import (
    evaluate_nonwrite_operational_admission,
    evaluate_operational_admission,
    evaluate_terminal_nonwrite_admission,
)
from ecomsre.dta_v2.read_only_smoke import CleanupObservation
from ecomsre.dta_v2.registry import RunbookRegistry


class LiveAttemptLifecycle(Protocol):
    @property
    def mode(self) -> LiveAttemptMode: ...

    def verify_pre_live(self, freeze: PreLiveFreeze) -> None: ...

    def admit_environment(self) -> None: ...

    def start(self) -> None: ...

    def wait_ready(self) -> None: ...

    def capture_baseline(self) -> BaselineEvidence: ...

    def revalidate_before_fault(self, scenario: LiveScenarioSpec) -> None: ...

    def inject_fault(self, scenario: LiveScenarioSpec) -> None: ...

    @property
    def fault_applied(self) -> bool: ...

    def verify_fault_impact(self, scenario: LiveScenarioSpec) -> bool: ...

    def run_agent(self, scenario: LiveScenarioSpec) -> DtaAgentRunResult: ...

    def current_state(
        self,
        *,
        scenario: LiveScenarioSpec,
        agent_result: DtaAgentRunResult,
        attempt_id: str,
    ) -> CurrentStateSnapshot: ...

    def controls(self, current_state: CurrentStateSnapshot) -> LiveControls: ...

    def capture_recovery_windows(
        self,
        forward_execution: ForwardExecution,
    ) -> tuple[RecoveryWindow, RecoveryWindow]: ...

    def email_leak_flag_off(self) -> bool | None: ...

    @property
    def restoration_write_count(self) -> int: ...

    def restore_baseline(self, baseline: BaselineEvidence | None) -> bool: ...

    def revalidate_before_cleanup(self) -> None: ...

    def cleanup_owned(self, *, baseline_restored: bool) -> CleanupObservation: ...


def _with_digest(model_type, payload: dict[str, object], digest_field: str):
    draft = model_type.model_construct(**payload, **{digest_field: "0" * 64})
    return model_type.model_validate(
        {
            **payload,
            digest_field: semantic_sha256(
                draft.model_dump(mode="json", exclude={digest_field})
            ),
        }
    )


def _require_executable_agent_result(
    result: DtaAgentRunResult,
    *,
    freeze: PreLiveFreeze,
) -> None:
    if (
        result.terminal is not AgentRunTerminal.COMPLETED
        or result.diagnosis is None
        or result.action_proposal is None
        or result.candidate_set is None
        or result.resolved_evidence is None
        or result.action_proposal.disposition is not ActionDisposition.EXECUTE_RUNBOOK
        or result.identity.identity_sha256 != freeze.agent_identity_sha256
        or result.identity.model_id != freeze.model_id
    ):
        raise ValueError("Agent result is not an executable, frozen-identity result")


def _require_no_fault_agent_result(
    result: DtaAgentRunResult,
    *,
    freeze: PreLiveFreeze,
) -> None:
    identity_matches = (
        result.identity.identity_sha256 == freeze.agent_identity_sha256
        and result.identity.model_id == freeze.model_id
    )
    no_action = (
        result.terminal is AgentRunTerminal.COMPLETED
        and result.action_proposal is not None
        and result.action_proposal.disposition is ActionDisposition.NO_ACTION
    )
    abstain = (
        result.terminal is AgentRunTerminal.ABSTAIN
        and result.action_proposal is None
    )
    if not identity_matches or not (no_action or abstain):
        raise ValueError("no-fault Agent result is not a non-write terminal")


def run_live_attempt(
    *,
    private_root: Path,
    attempt_id: str,
    config: LiveDemoConfig,
    scenario: LiveScenarioSpec,
    freeze: PreLiveFreeze,
    registry: RunbookRegistry,
    master_authorization: MasterAuthorizationRecord,
    lifecycle: LiveAttemptLifecycle,
    as_of: datetime,
    utc_now: Callable[[], datetime],
    forbidden_secrets: tuple[str, ...] = (),
    _owned_execution_grant: OwnedLiveExecutionGrant | None = None,
) -> LiveAttemptClosure:
    """Run one bounded attempt and always seal cleanup/terminal evidence."""

    config = LiveDemoConfig.model_validate(config.model_dump(mode="python"))
    scenario = LiveScenarioSpec.model_validate(scenario.model_dump(mode="python"))
    freeze = PreLiveFreeze.model_validate(freeze.model_dump(mode="python"))
    registry = RunbookRegistry.model_validate(registry.model_dump(mode="python"))
    master = MasterAuthorizationRecord.model_validate(
        master_authorization.model_dump(mode="python")
    )
    owned_claim = None
    if lifecycle.mode is LiveAttemptMode.OWNED_LOCAL:
        if not isinstance(_owned_execution_grant, OwnedLiveExecutionGrant):
            raise TypeError("OWNED_LOCAL attempts require a campaign-issued grant")
        owned_claim = _owned_execution_grant.consume(
            lifecycle=lifecycle,
            attempt_id=attempt_id,
        )
        if owned_claim.scenario is not scenario.scenario:
            raise ValueError("owned campaign claim differs from attempt scenario")
    elif _owned_execution_grant is not None:
        raise TypeError("fake/replay attempts cannot consume owned live grants")
    effective_mode = (
        LiveAttemptMode.OWNED_LOCAL
        if owned_claim is not None
        else LiveAttemptMode.FAKE_REPLAY
    )
    if scenario not in config.scenarios:
        raise ValueError("attempt scenario is outside the frozen live config")
    if freeze.live_config_sha256 != config.config_sha256:
        raise ValueError("pre-live freeze differs from live config")
    if freeze.registry_sha256 != registry.registry_sha256:
        raise ValueError("pre-live freeze differs from trusted Registry")
    if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
        raise ValueError("live attempt time must be UTC")

    def fresh_utc() -> datetime:
        observed = utc_now()
        if observed.tzinfo is None or observed.utcoffset() != timedelta(0):
            raise ValueError("live lifecycle clock must return UTC")
        return observed

    journal = PrivateLiveAttemptJournal(
        private_root,
        forbidden_secrets=forbidden_secrets,
    )
    events: list[LiveAttemptEvent] = []
    evidence_persistence_failed = False
    event_journal_open = True
    primary_failure: LiveFailureCode | None = None

    def record(
        stage: LiveAttemptStage,
        *,
        status: LiveStageStatus = LiveStageStatus.PASS,
        failure_code: LiveFailureCode | None = None,
    ) -> None:
        nonlocal evidence_persistence_failed, event_journal_open, primary_failure
        event = build_live_attempt_event(
            ordinal=len(events) + 1,
            stage=stage,
            status=status,
            failure_code=failure_code,
        )
        if stage is LiveAttemptStage.CLOSED:
            events.append(event)
            return
        if not event_journal_open:
            events.append(event)
            return
        try:
            journal.append_event(event)
        except Exception:
            evidence_persistence_failed = True
            event_journal_open = False
            primary_failure = LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
            event = build_live_attempt_event(
                ordinal=event.ordinal,
                stage=stage,
                status=LiveStageStatus.FAIL,
                failure_code=LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED,
            )
        events.append(event)

    def persist_artifact(relative_path: str, value: object) -> None:
        nonlocal evidence_persistence_failed
        try:
            journal.persist_artifact(relative_path, value)
        except Exception:
            evidence_persistence_failed = True
            raise

    def persist_agent(result: DtaAgentRunResult) -> None:
        nonlocal evidence_persistence_failed
        try:
            journal.persist_agent(result)
        except Exception:
            evidence_persistence_failed = True
            raise

    record(LiveAttemptStage.CREATED)
    cleanup_failure: LiveFailureCode | None = None
    start_requested = False
    baseline: BaselineEvidence | None = None
    baseline_restored: bool | None = None
    cleanup_attempted = False
    cleanup = CleanupObservation.unknown_blocked()
    agent_result: DtaAgentRunResult | None = None
    admission: OperationalAdmission | TerminalNonwriteAdmission | None = None
    authorization_sha256: str | None = None
    transaction: ExecutionTransaction | None = None
    forward: ForwardExecution | None = None
    windows: tuple[RecoveryWindow, ...] = ()
    live_controls: LiveControls | None = None
    fault_injection_count = 0
    fault_injection_applied_count = 0
    recovery_window_count = 0

    def call_stage(
        stage: LiveAttemptStage,
        failure_code: LiveFailureCode,
        operation: Callable[[], object],
    ) -> object | None:
        nonlocal primary_failure
        if primary_failure is not None:
            return None
        try:
            value = operation()
        except Exception:
            primary_failure = failure_code
            record(stage, status=LiveStageStatus.FAIL, failure_code=failure_code)
            return None
        record(stage)
        if evidence_persistence_failed:
            primary_failure = LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
        return value

    call_stage(
        LiveAttemptStage.PRELIVE_FREEZE_VERIFIED,
        LiveFailureCode.FREEZE_MISMATCH,
        lambda: lifecycle.verify_pre_live(freeze),
    )
    call_stage(
        LiveAttemptStage.ENVIRONMENT_ADMITTED,
        LiveFailureCode.ENVIRONMENT_ADMISSION_FAILED,
        lifecycle.admit_environment,
    )
    if primary_failure is None:
        start_requested = True
    call_stage(
        LiveAttemptStage.START_REQUESTED,
        LiveFailureCode.START_FAILED,
        lifecycle.start,
    )
    call_stage(
        LiveAttemptStage.READY,
        LiveFailureCode.READINESS_FAILED,
        lifecycle.wait_ready,
    )
    if primary_failure is None:
        try:
            baseline_value = lifecycle.capture_baseline()
            if not isinstance(baseline_value, BaselineEvidence):
                raise TypeError("baseline lifecycle returned the wrong boundary type")
            baseline = BaselineEvidence.model_validate(
                baseline_value.model_dump(mode="python")
            )
            persist_artifact("baseline.json", baseline)
            record(LiveAttemptStage.BASELINE_CAPTURED)
        except Exception:
            primary_failure = (
                LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
                if evidence_persistence_failed
                else LiveFailureCode.BASELINE_FAILED
            )
            record(
                LiveAttemptStage.BASELINE_CAPTURED,
                status=LiveStageStatus.FAIL,
                failure_code=primary_failure,
            )

    if primary_failure is None and scenario.scenario is LiveScenario.NO_FAULT:
        record(
            LiveAttemptStage.FAULT_INJECTED,
            status=LiveStageStatus.NOT_APPLICABLE,
        )
        record(
            LiveAttemptStage.FAULT_IMPACT_VERIFIED,
            status=LiveStageStatus.NOT_APPLICABLE,
        )
    elif primary_failure is None:
        try:
            lifecycle.revalidate_before_fault(scenario)
            fault_injection_count = 1
            lifecycle.inject_fault(scenario)
            fault_injection_applied_count = 1 if lifecycle.fault_applied else 0
            record(LiveAttemptStage.FAULT_INJECTED)
        except Exception:
            fault_injection_applied_count = 1 if lifecycle.fault_applied else 0
            primary_failure = LiveFailureCode.FAULT_INJECTION_FAILED
            record(
                LiveAttemptStage.FAULT_INJECTED,
                status=LiveStageStatus.FAIL,
                failure_code=primary_failure,
            )
        if primary_failure is None:
            try:
                if lifecycle.verify_fault_impact(scenario) is not True:
                    raise ValueError("fault impact was not proven")
                record(LiveAttemptStage.FAULT_IMPACT_VERIFIED)
            except Exception:
                primary_failure = LiveFailureCode.FAULT_IMPACT_FAILED
                record(
                    LiveAttemptStage.FAULT_IMPACT_VERIFIED,
                    status=LiveStageStatus.FAIL,
                    failure_code=primary_failure,
                )

    if primary_failure is None:
        try:
            agent_value = lifecycle.run_agent(scenario)
            if not isinstance(agent_value, DtaAgentRunResult):
                raise TypeError("Agent lifecycle returned the wrong boundary type")
            agent_result = DtaAgentRunResult.model_validate(
                agent_value.model_dump(mode="python")
            )
            if owned_claim is not None and agent_result.run_id != owned_claim.run_id:
                raise ValueError("Agent run differs from owned campaign claim")
            persist_agent(agent_result)
            if scenario.scenario is LiveScenario.NO_FAULT:
                _require_no_fault_agent_result(agent_result, freeze=freeze)
            else:
                _require_executable_agent_result(
                    agent_result,
                    freeze=freeze,
                )
            record(LiveAttemptStage.AGENT_COMPLETED)
        except Exception:
            primary_failure = (
                LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
                if evidence_persistence_failed
                else LiveFailureCode.AGENT_FAILED
            )
            record(
                LiveAttemptStage.AGENT_COMPLETED,
                status=LiveStageStatus.FAIL,
                failure_code=primary_failure,
            )

    current_state: CurrentStateSnapshot | None = None
    if primary_failure is None and agent_result is not None:
        try:
            state = lifecycle.current_state(
                scenario=scenario,
                agent_result=agent_result,
                attempt_id=attempt_id,
            )
            proposal_target = (
                agent_result.action_proposal.target_service
                if agent_result.action_proposal is not None
                else None
            )
            observed_target = (
                proposal_target
                or (
                    agent_result.diagnosis.root_service
                    if agent_result.diagnosis is not None
                    else None
                )
                or state.target_logical_service
            )
            current_state = require_trusted_live_current_state(
                snapshot=state,
                registry=registry,
                master_authorization=master,
                expected_run_id=agent_result.run_id,
                expected_attempt_id=attempt_id,
                authoritative_target=observed_target,
            )
            persist_artifact("current-state.json", current_state)
        except Exception:
            primary_failure = (
                LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
                if evidence_persistence_failed
                else LiveFailureCode.STATE_ADMISSION_FAILED
            )

    if (
        primary_failure is None
        and agent_result is not None
        and current_state is not None
        and scenario.scenario is LiveScenario.NO_FAULT
    ):
        try:
            admission_as_of = fresh_utc()
            if agent_result.action_proposal is None:
                admission = evaluate_terminal_nonwrite_admission(
                    registry=registry,
                    agent_result=agent_result,
                    current_state=current_state,
                    master_authorization=master,
                    as_of=admission_as_of,
                )
            else:
                assert agent_result.candidate_set is not None
                assert agent_result.diagnosis is not None
                assert agent_result.resolved_evidence is not None
                admission = evaluate_nonwrite_operational_admission(
                    registry=registry,
                    candidate_set=agent_result.candidate_set,
                    diagnosis=agent_result.diagnosis,
                    diagnosis_evidence=agent_result.resolved_evidence,
                    proposal=agent_result.action_proposal,
                    current_state=current_state,
                    master_authorization=master,
                    as_of=admission_as_of,
                )
            persist_artifact("operational-admission.json", admission)
            record(LiveAttemptStage.ADMISSION_COMPLETED)
            record(
                LiveAttemptStage.AUTHORIZATION_COMPLETED,
                status=LiveStageStatus.NOT_APPLICABLE,
            )
            for stage in (
                LiveAttemptStage.FORWARD_EXECUTION_COMPLETED,
                LiveAttemptStage.RECOVERY_WINDOWS_CAPTURED,
                LiveAttemptStage.VERIFICATION_COMPLETED,
            ):
                record(stage, status=LiveStageStatus.NOT_APPLICABLE)
        except Exception:
            primary_failure = (
                LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
                if evidence_persistence_failed
                else LiveFailureCode.OPERATIONAL_ADMISSION_DENIED
            )

    if (
        primary_failure is None
        and agent_result is not None
        and current_state is not None
        and scenario.scenario is not LiveScenario.NO_FAULT
    ):
        candidate_set = agent_result.candidate_set
        diagnosis_evidence = agent_result.resolved_evidence
        diagnosis_record = agent_result.diagnosis
        proposal_record = agent_result.action_proposal
        try:
            if (
                candidate_set is None
                or diagnosis_evidence is None
                or diagnosis_record is None
                or proposal_record is None
            ):
                raise ValueError("positive Agent artifacts are incomplete")
            authorization_issued_at = fresh_utc()
            child = derive_attempt_authorization(
                master=master,
                scenario_id=scenario.scenario_id,
                registry=registry,
                candidate_set=candidate_set,
                diagnosis=diagnosis_record,
                diagnosis_evidence=diagnosis_evidence,
                proposal=proposal_record,
                current_state=current_state,
                issued_at=authorization_issued_at,
                expires_at=authorization_issued_at + timedelta(hours=1),
            )
            authorization_sha256 = child.authorization_sha256
            persist_artifact("attempt-authorization.json", child)
            record(LiveAttemptStage.AUTHORIZATION_COMPLETED)
        except Exception:
            primary_failure = (
                LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
                if evidence_persistence_failed
                else LiveFailureCode.AUTHORIZATION_FAILED
            )
        if primary_failure is None:
            try:
                if (
                    candidate_set is None
                    or diagnosis_evidence is None
                    or diagnosis_record is None
                    or proposal_record is None
                ):
                    raise ValueError("positive Agent artifacts are incomplete")
                write_admission = evaluate_operational_admission(
                    registry=registry,
                    candidate_set=candidate_set,
                    diagnosis=diagnosis_record,
                    diagnosis_evidence=diagnosis_evidence,
                    proposal=proposal_record,
                    current_state=current_state,
                    master_authorization=master,
                    attempt_authorization=child,
                    as_of=fresh_utc(),
                )
                admission = write_admission
                persist_artifact("operational-admission.json", write_admission)
                if write_admission.verdict is not AdmissionVerdict.ALLOW:
                    raise ValueError("Operational Admission denied the Runbook")
                record(LiveAttemptStage.ADMISSION_COMPLETED)
            except Exception:
                primary_failure = (
                    LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
                    if evidence_persistence_failed
                    else LiveFailureCode.OPERATIONAL_ADMISSION_DENIED
                )
        if primary_failure is None:
            try:
                if proposal_record is None:
                    raise ValueError("positive Agent proposal is incomplete")
                live_controls = lifecycle.controls(current_state)
                forward = execute_live_forward_steps(
                    registry=registry,
                    proposal=proposal_record,
                    current_state=current_state,
                    admission=write_admission,
                    authorization=child,
                    controls=live_controls,
                    receipt_journal=journal,
                    utc_now=utc_now,
                )
                persist_artifact("forward-execution.json", forward)
                if forward.terminal is ForwardExecutionTerminal.PARTIALLY_APPLIED:
                    primary_failure = LiveFailureCode.PARTIALLY_APPLIED
                    record(
                        LiveAttemptStage.FORWARD_EXECUTION_COMPLETED,
                        status=LiveStageStatus.FAIL,
                        failure_code=primary_failure,
                    )
                    transaction = finalize_live_execution(
                        registry=registry,
                        forward_execution=forward,
                        recovery_windows=(),
                        email_leak_flag_off=lifecycle.email_leak_flag_off(),
                        maximum_email_memory_slope_bytes_per_second=(
                            config.maximum_email_recovery_slope_bytes_per_second
                        ),
                    )
                elif forward.terminal is ForwardExecutionTerminal.EXECUTION_FAILED:
                    primary_failure = LiveFailureCode.EXECUTION_FAILED
                    record(
                        LiveAttemptStage.FORWARD_EXECUTION_COMPLETED,
                        status=LiveStageStatus.FAIL,
                        failure_code=primary_failure,
                    )
                    transaction = finalize_live_execution(
                        registry=registry,
                        forward_execution=forward,
                        recovery_windows=(),
                        email_leak_flag_off=lifecycle.email_leak_flag_off(),
                        maximum_email_memory_slope_bytes_per_second=(
                            config.maximum_email_recovery_slope_bytes_per_second
                        ),
                    )
                else:
                    record(LiveAttemptStage.FORWARD_EXECUTION_COMPLETED)
            except ReceiptPersistenceError as error:
                forward = error.forward_execution
                try:
                    persist_artifact(
                        "receipt-persistence-fallback.json", forward
                    )
                except Exception:
                    evidence_persistence_failed = True
                primary_failure = LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
                record(
                    LiveAttemptStage.FORWARD_EXECUTION_COMPLETED,
                    status=LiveStageStatus.FAIL,
                    failure_code=primary_failure,
                )
            except PartialExecutionError as error:
                forward = error.forward_execution
                try:
                    persist_artifact("forward-execution.json", forward)
                    primary_failure = LiveFailureCode.PARTIALLY_APPLIED
                    record(
                        LiveAttemptStage.FORWARD_EXECUTION_COMPLETED,
                        status=LiveStageStatus.FAIL,
                        failure_code=primary_failure,
                    )
                    transaction = finalize_live_execution(
                        registry=registry,
                        forward_execution=forward,
                        recovery_windows=(),
                        email_leak_flag_off=lifecycle.email_leak_flag_off(),
                        maximum_email_memory_slope_bytes_per_second=(
                            config.maximum_email_recovery_slope_bytes_per_second
                        ),
                    )
                except Exception:
                    primary_failure = LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
                    record(
                        LiveAttemptStage.FORWARD_EXECUTION_COMPLETED,
                        status=LiveStageStatus.FAIL,
                        failure_code=primary_failure,
                    )
            except Exception:
                if primary_failure is None:
                    primary_failure = (
                        LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
                        if evidence_persistence_failed
                        else LiveFailureCode.EXECUTION_FAILED
                    )
                record(
                    LiveAttemptStage.FORWARD_EXECUTION_COMPLETED,
                    status=LiveStageStatus.FAIL,
                    failure_code=primary_failure,
                )
        if primary_failure is None and forward is not None:
            try:
                raw_windows = lifecycle.capture_recovery_windows(forward)
                if not isinstance(raw_windows, tuple) or len(raw_windows) != 2:
                    raise TypeError("recovery lifecycle returned the wrong boundary")
                windows = tuple(
                    RecoveryWindow.model_validate(item.model_dump(mode="python"))
                    for item in raw_windows
                )
                if (
                    tuple(item.ordinal for item in windows) != (1, 2)
                    or windows[1].started_at < windows[0].ended_at
                ):
                    raise ValueError("recovery windows are not canonical")
                recovery_window_count = len(windows)
                persist_artifact(
                    "recovery-windows.json",
                    [item.model_dump(mode="json") for item in windows],
                )
                record(LiveAttemptStage.RECOVERY_WINDOWS_CAPTURED)
            except Exception:
                primary_failure = (
                    LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
                    if evidence_persistence_failed
                    else LiveFailureCode.RECOVERY_CAPTURE_FAILED
                )
                record(
                    LiveAttemptStage.RECOVERY_WINDOWS_CAPTURED,
                    status=LiveStageStatus.FAIL,
                    failure_code=primary_failure,
                )
        if primary_failure is None and forward is not None:
            try:
                transaction = finalize_live_execution(
                    registry=registry,
                    forward_execution=forward,
                    recovery_windows=windows,
                    email_leak_flag_off=lifecycle.email_leak_flag_off(),
                    maximum_email_memory_slope_bytes_per_second=(
                        config.maximum_email_recovery_slope_bytes_per_second
                    ),
                )
                persist_artifact("execution-transaction.json", transaction)
                if transaction.terminal is not ExecutionTerminal.RECOVERED:
                    raise ValueError("Runbook-specific verification failed")
                record(LiveAttemptStage.VERIFICATION_COMPLETED)
            except Exception:
                primary_failure = (
                    LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
                    if evidence_persistence_failed
                    else LiveFailureCode.VERIFICATION_FAILED
                )
                record(
                    LiveAttemptStage.VERIFICATION_COMPLETED,
                    status=LiveStageStatus.FAIL,
                    failure_code=primary_failure,
                )
        elif transaction is not None:
            try:
                persist_artifact("execution-transaction.json", transaction)
            except Exception:
                if primary_failure is None:
                    primary_failure = LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED

    if start_requested:
        try:
            baseline_restored = lifecycle.restore_baseline(baseline)
            if baseline_restored is not True:
                raise ValueError("baseline restoration was not proven")
            record(LiveAttemptStage.BASELINE_RESTORED)
        except Exception:
            baseline_restored = False
            if primary_failure is None:
                primary_failure = LiveFailureCode.BASELINE_RESTORATION_FAILED
            record(
                LiveAttemptStage.BASELINE_RESTORED,
                status=LiveStageStatus.FAIL,
                failure_code=LiveFailureCode.BASELINE_RESTORATION_FAILED,
            )

    if start_requested:
        cleanup_attempted = True
        try:
            lifecycle.revalidate_before_cleanup()
            cleanup = CleanupObservation.model_validate(
                lifecycle.cleanup_owned(
                    baseline_restored=baseline_restored is True
                ).model_dump(mode="python")
            )
            if cleanup.verdict != "CLEAN":
                cleanup_failure = LiveFailureCode.CLEANUP_BLOCKED
                record(
                    LiveAttemptStage.CLEANUP_ATTEMPTED,
                    status=LiveStageStatus.FAIL,
                    failure_code=cleanup_failure,
                )
            else:
                record(LiveAttemptStage.CLEANUP_ATTEMPTED)
        except Exception:
            cleanup_failure = LiveFailureCode.CLEANUP_FAILED
            cleanup = CleanupObservation.unknown_blocked()
            record(
                LiveAttemptStage.CLEANUP_ATTEMPTED,
                status=LiveStageStatus.FAIL,
                failure_code=cleanup_failure,
            )

    if evidence_persistence_failed and primary_failure is None:
        primary_failure = LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
    failure = cleanup_failure or primary_failure
    record(
        LiveAttemptStage.CLOSED,
        status=LiveStageStatus.FAIL if failure is not None else LiveStageStatus.PASS,
        failure_code=failure,
    )

    diagnosis = None if agent_result is None else agent_result.diagnosis
    proposal = None if agent_result is None else agent_result.action_proposal
    counters = LiveAttemptCounters(
        fault_injection_count=fault_injection_count,
        fault_injection_applied_count=fault_injection_applied_count,
        agent_investigation_count=1 if agent_result is not None else 0,
        provider_turn_count=(0 if agent_result is None else agent_result.provider_turn_count),
        read_tool_dispatch_count=(
            0 if agent_result is None else agent_result.read_tool_dispatch_count
        ),
        diagnosis_count=(
            1 if agent_result is not None and agent_result.diagnosis is not None else 0
        ),
        runbook_proposal_count=(
            1
            if proposal is not None
            and proposal.disposition is ActionDisposition.EXECUTE_RUNBOOK
            else 0
        ),
        admitted_runbook_count=(
            1
            if admission is not None and admission.verdict is AdmissionVerdict.ALLOW
            else 0
        ),
        forward_step_count=(
            transaction.forward_step_count
            if transaction is not None
            else (
                0 if live_controls is None else live_controls.forward_write_count
            )
        ),
        restoration_write_count=lifecycle.restoration_write_count,
        recovery_window_count=recovery_window_count,
        rollback_or_compensation_count=0,
        unsafe_write_attempt_count=0,
        arbitrary_shell_attempt_count=0,
    )
    terminal = (
        LiveAttemptTerminal.FAIL
        if failure is not None
        else (
            LiveAttemptTerminal.OFFLINE_PASS
            if effective_mode is LiveAttemptMode.FAKE_REPLAY
            else LiveAttemptTerminal.LIVE_PASS
        )
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v2.live-attempt-closure.v2",
        "attempt_id": attempt_id,
        "run_id": (
            owned_claim.run_id
            if owned_claim is not None
            else (agent_result.run_id if agent_result is not None else "0" * 32)
        ),
        "mode": effective_mode,
        "scenario": scenario.scenario,
        "fault_operation": scenario.fault_operation,
        "terminal": terminal,
        "failure_code": failure,
        "primary_failure_code": primary_failure,
        "cleanup_failure_code": cleanup_failure,
        "pre_live_freeze_sha256": freeze.freeze_sha256,
        "live_config_sha256": config.config_sha256,
        "agent_result_sha256": (
            None if agent_result is None else agent_result.result_sha256
        ),
        "agent_terminal": (
            None if agent_result is None else agent_result.terminal.value
        ),
        "tool_call_sequence": (
            ()
            if agent_result is None
            else tuple(turn.function_name for turn in agent_result.provider_turns)
        ),
        "diagnosis_sha256": (
            None if diagnosis is None else semantic_sha256(diagnosis.model_dump(mode="json"))
        ),
        "root_service": None if diagnosis is None else diagnosis.root_service,
        "fault_domain": None if diagnosis is None else diagnosis.fault_domain,
        "mechanism": None if diagnosis is None else diagnosis.mechanism,
        "evidence_source_types": (
            () if diagnosis is None else diagnosis.evidence_source_types
        ),
        "evidence_refs": (
            () if diagnosis is None else diagnosis.supporting_evidence_refs
        ),
        "candidates": (
            ()
            if agent_result is None or agent_result.candidate_set is None
            else agent_result.candidate_set.write_candidates
        ),
        "proposal_disposition": (
            None if proposal is None else proposal.disposition.value
        ),
        "runbook_id": None if proposal is None else proposal.runbook_id,
        "proposal_sha256": None if proposal is None else proposal.proposal_sha256,
        "proposal_target_service": (
            None if proposal is None else proposal.target_service
        ),
        "proposal_parameters": () if proposal is None else proposal.parameters,
        "admission_verdict": None if admission is None else admission.verdict,
        "admission_reason_codes": (
            admission.reason_codes
            if isinstance(admission, OperationalAdmission)
            else (
                (AdmissionReasonCode.NONWRITE_AGENT_TERMINAL,)
                if isinstance(admission, TerminalNonwriteAdmission)
                else ()
            )
        ),
        "admission_sha256": None if admission is None else admission.admission_sha256,
        "authorization_sha256": authorization_sha256,
        "transaction_terminal": (
            None if transaction is None else transaction.terminal
        ),
        "transaction_sha256": (
            None if transaction is None else transaction.transaction_sha256
        ),
        "receipts": (
            transaction.receipts
            if transaction is not None
            else (() if forward is None else forward.receipts)
        ),
        "recovery_windows": windows,
        "verification": (
            None if transaction is None else transaction.verification
        ),
        "baseline_restored": baseline_restored,
        "cleanup_attempted": cleanup_attempted,
        "cleanup_terminal": (
            CleanupTerminal.CLEAN if cleanup.verdict == "CLEAN" else CleanupTerminal.BLOCKED
        ) if cleanup_attempted else None,
        "owned_containers_after": cleanup.owned_containers,
        "owned_networks_after": cleanup.owned_networks,
        "owned_volumes_after": cleanup.owned_volumes,
        "non_owned_resources_changed": cleanup.non_owned_resources_changed,
        "counters": counters,
        "journal": tuple(events),
    }
    closure = LiveAttemptClosure.model_validate(
        _with_digest(LiveAttemptClosure, payload, "closure_sha256")
    )
    try:
        journal.persist_closure(closure)
    except Exception:
        created = journal.recover_created_primary_closure(closure)
        if created is not None:
            return created
        primary_failure = LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
        failure = cleanup_failure or primary_failure
        fallback_event = build_live_attempt_event(
            ordinal=events[-1].ordinal,
            stage=LiveAttemptStage.CLOSED,
            status=LiveStageStatus.FAIL,
            failure_code=failure,
        )
        events[-1] = fallback_event
        fallback_payload = {
            **payload,
            "terminal": LiveAttemptTerminal.FAIL,
            "failure_code": failure,
            "primary_failure_code": primary_failure,
            "journal": tuple(events),
        }
        closure = LiveAttemptClosure.model_validate(
            _with_digest(
                LiveAttemptClosure,
                fallback_payload,
                "closure_sha256",
            )
        )
        journal.persist_closure_fallback(closure)
    return closure


__all__ = ["LiveAttemptLifecycle", "run_live_attempt"]
