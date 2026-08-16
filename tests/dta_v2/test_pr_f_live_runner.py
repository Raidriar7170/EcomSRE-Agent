from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ecomsre.dta_v2.agent import DtaAgentRunResult, run_tool_using_agent
from ecomsre.dta_v2.agent_contracts import ActionSelectionDecision
from ecomsre.dta_v2.agent_provider import build_provider_identity
from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    DtaDiagnosis,
    EvidenceSource,
    FaultDomain,
    FaultMechanism,
    RunbookId,
    Terminal,
    semantic_sha256,
)
from ecomsre.dta_v2.live_contracts import (
    LiveAttemptClosure,
    LiveAttemptMode,
    LiveAttemptStage,
    LiveAttemptTerminal,
    LiveFailureCode,
    LiveScenario,
    build_live_campaign_attempt_claim,
    build_baseline_evidence,
    build_pre_live_freeze,
    build_recovery_window,
    load_live_demo_config,
)
from ecomsre.dta_v2.live_capability import (
    _OWNED_CAMPAIGN_TOKEN,
    issue_owned_live_execution_grant,
)
from ecomsre.dta_v2.live_reporting import (
    PrivateLiveAttemptJournal,
    build_public_live_attempt_report,
    build_public_live_campaign_report,
    render_public_live_demo_human_brief,
    render_public_live_demo_markdown,
)
from ecomsre.dta_v2.live_runner import run_live_attempt
from ecomsre.dta_v2.operational_contracts import (
    DockerBoundary,
    OwnershipStatus,
    PreconditionObservation,
    ServiceRuntimeState,
    build_current_state_snapshot,
)
from ecomsre.dta_v2.read_only_smoke import CleanupObservation
from ecomsre.dta_v2.read_tools import FakeReadBackend
from ecomsre.dta_v2.registry import load_runbook_registry

from test_admission_policy import NOW, RUNBOOK_ROOT, master_authorization
from test_pr_d_agent_loop import (
    ScriptedProvider,
    _context,
    _decision,
    _diagnosis,
    _metrics,
    _resource,
    _runtime,
    _trace,
)


ROOT = Path(__file__).resolve().parents[2]
FROZEN_MODEL = "gpt-5.4-2026-03-05"


_CASE = {
    LiveScenario.PAYMENT: (
        0,
        "1" * 32,
        "payment",
        FaultDomain.CONFIGURATION,
        FaultMechanism.CONFIGURATION_ERROR,
        (EvidenceSource.METRICS, EvidenceSource.TRACES),
        (_metrics, _trace),
        RunbookId.ROLLBACK_CONFIGURATION,
    ),
    LiveScenario.RECOMMENDATION: (
        1,
        "2" * 32,
        "recommendation",
        FaultDomain.SERVICE_RUNTIME,
        FaultMechanism.SERVICE_UNAVAILABLE,
        (EvidenceSource.METRICS, EvidenceSource.RUNTIME),
        (_metrics, _runtime),
        RunbookId.RESTART_SERVICE,
    ),
    LiveScenario.EMAIL: (
        2,
        "3" * 32,
        "email",
        FaultDomain.LOCAL_RESOURCE,
        FaultMechanism.MEMORY_LEAK,
        (EvidenceSource.METRICS, EvidenceSource.RUNTIME, EvidenceSource.RESOURCES),
        (_metrics, _runtime, _resource),
        RunbookId.MITIGATE_MEMORY_LEAK,
    ),
}


def _positive_agent(scenario: LiveScenario) -> DtaAgentRunResult:
    index, run_id, service, domain, mechanism, sources, requests, runbook = _CASE[
        scenario
    ]
    diagnosis = _diagnosis(
        run_id=run_id,
        service=service,
        domain=domain,
        mechanism=mechanism,
        sources=sources,
    )
    provider = ScriptedProvider(
        [*(builder(run_id, service) for builder in requests), diagnosis],
        _decision(
            run_id=run_id,
            runbook=runbook,
            service=service,
            sources=sources,
        ),
    )
    provider.identity = build_provider_identity(FROZEN_MODEL)
    return run_tool_using_agent(
        context=_context(run_id, index),
        backend=FakeReadBackend.healthy(),
        registry=load_runbook_registry(RUNBOOK_ROOT),
        provider=provider,
    )


def _no_fault_agent() -> DtaAgentRunResult:
    run_id = "4" * 32
    diagnosis = _diagnosis(
        run_id=run_id,
        service="payment",
        domain=FaultDomain.UNKNOWN,
        mechanism=FaultMechanism.UNKNOWN,
        sources=(EvidenceSource.METRICS,),
    )
    decision = ActionSelectionDecision(
        schema_version="dta-v2.action-selection-decision.v1",
        disposition=ActionDisposition.NO_ACTION,
        runbook_id=None,
        target_service=None,
        parameters=(),
        supporting_evidence_refs=diagnosis.supporting_evidence_refs,
        rationale="The healthy control requires no write.",
    )
    provider = ScriptedProvider([_metrics(run_id, "payment"), diagnosis], decision)
    provider.identity = build_provider_identity(FROZEN_MODEL)
    return run_tool_using_agent(
        context=_context(run_id, 0),
        backend=FakeReadBackend.healthy(),
        registry=load_runbook_registry(RUNBOOK_ROOT),
        provider=provider,
    )


def _no_fault_abstain_agent() -> DtaAgentRunResult:
    run_id = "5" * 32
    diagnosis = DtaDiagnosis(
        schema_version="dta-v2.diagnosis.v1",
        run_id=run_id,
        terminal=Terminal.ABSTAIN,
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
        evidence_source_types=(),
        uncertainties=("No actionable incident is present in the control window.",),
        summary="The healthy control does not justify a write.",
    )
    provider = ScriptedProvider([diagnosis], None)
    provider.identity = build_provider_identity(FROZEN_MODEL)
    return run_tool_using_agent(
        context=_context(run_id, 0),
        backend=FakeReadBackend.healthy(),
        registry=load_runbook_registry(RUNBOOK_ROOT),
        provider=provider,
    )


def _freeze(config, registry):
    identity = build_provider_identity(FROZEN_MODEL)
    return build_pre_live_freeze(
        code_head="b" * 40,
        agent_identity_sha256=identity.identity_sha256,
        model_id=identity.model_id,
        prompt_sha256=identity.prompt_sha256,
        tool_schema_sha256=identity.tool_schema_sha256,
        diagnosis_schema_sha256=identity.diagnosis_schema_sha256,
        action_selection_schema_sha256=identity.action_selection_schema_sha256,
        action_proposal_schema_sha256=identity.action_proposal_schema_sha256,
        registry_sha256=registry.registry_sha256,
        candidate_filter_source_sha256="1" * 64,
        admission_source_sha256="2" * 64,
        authorization_source_sha256="3" * 64,
        executor_source_sha256="4" * 64,
        verifier_source_sha256="5" * 64,
        runner_source_sha256="6" * 64,
        reporting_schema_sha256="7" * 64,
        upstream_commit=config.upstream_commit,
        upstream_tag=config.upstream_tag,
        resolved_compose_sha256="8" * 64,
        image_authority_sha256="9" * 64,
        live_config=config,
    )


def _windows(*, slope: float | None = None):
    start = datetime(2026, 8, 16, 10, 0, tzinfo=timezone.utc)
    return tuple(
        build_recovery_window(
            ordinal=ordinal,
            started_at=start + timedelta(minutes=ordinal - 1),
            ended_at=start + timedelta(minutes=ordinal),
            infrastructure_passed=True,
            business_sli_passed=True,
            endpoint_passed=True,
            configuration_restored=True,
            memory_slope_bytes_per_second=slope,
        )
        for ordinal in (1, 2)
    )


class Controls:
    def __init__(self, snapshot, lifecycle) -> None:
        self.source_snapshot_sha256 = snapshot.snapshot_sha256
        self.run_id = snapshot.run_id
        self.attempt_id = snapshot.attempt_id
        self.target = snapshot.target_logical_service
        self.ownership_digest = snapshot.ownership_digest
        self.forward_write_count = snapshot.prior_forward_step_count
        self.transaction_started = False
        self.version = 0
        self.flag_off = False
        self.lifecycle = lifecycle
        self.initial_state_digest = self.state_digest()

    def state_digest(self):
        return semantic_sha256(
            {"target": self.target, "version": self.version, "flag_off": self.flag_off}
        )

    def revalidate_before_write(self, authorization, observed_at):
        assert authorization.run_id == self.run_id
        assert authorization.attempt_id == self.attempt_id
        assert authorization.issued_at <= observed_at < authorization.expires_at
        if (
            self.lifecycle.fail_stage == "reauth_second_write"
            and self.forward_write_count == 1
        ):
            raise RuntimeError("secret second-write authority detail")
        if (
            self.lifecycle.fail_stage == "drift_second_write"
            and self.forward_write_count == 1
        ):
            self.version += 1

    def _apply(self, operation):
        self.lifecycle.calls.append(operation)
        self.forward_write_count += 1
        if self.lifecycle.fail_stage == operation:
            raise RuntimeError("secret execution detail")
        if operation == "disable_email_leak":
            self.flag_off = True
            self.lifecycle.flag_off = True
        self.version += 1

    def restore_payment_configuration(self):
        self._apply("restore_payment")

    def start_recommendation_service(self):
        self._apply("start_recommendation")

    def disable_email_leak_flag(self):
        self._apply("disable_email_leak")

    def restart_email_service(self):
        self._apply("restart_email")


class FakeLifecycle:
    mode = LiveAttemptMode.FAKE_REPLAY

    def __init__(self, scenario, result, *, fail_stage=None) -> None:
        self.scenario = scenario
        self.result = result
        self.fail_stage = fail_stage
        self.calls: list[str] = []
        self.flag_off = False
        self._restoration_write_count = 0
        self._fault_applied = False

    def _stage(self, name):
        self.calls.append(name)
        if self.fail_stage == name:
            raise RuntimeError(f"secret {name} detail")

    def verify_pre_live(self, freeze):
        assert freeze.model_id == FROZEN_MODEL
        self._stage("freeze")

    def admit_environment(self):
        self._stage("admit")

    def start(self):
        self._stage("start")

    def wait_ready(self):
        self._stage("ready")

    def capture_baseline(self):
        self._stage("baseline")
        windows = _windows(slope=0.0 if self.scenario is LiveScenario.EMAIL else None)
        return build_baseline_evidence(
            baseline_sha256="a" * 64,
            windows=windows,
        )

    def inject_fault(self, scenario):
        assert scenario.scenario is self.scenario
        self._stage("fault")
        self._fault_applied = True

    @property
    def fault_applied(self):
        return self._fault_applied

    def revalidate_before_fault(self, scenario):
        assert scenario.scenario is self.scenario
        self._stage("reauth_fault")

    def verify_fault_impact(self, scenario):
        assert scenario.scenario is self.scenario
        self._stage("impact")
        if self.fail_stage == "impact_false":
            return False
        return True

    def run_agent(self, scenario):
        assert scenario.scenario is self.scenario
        self._stage("agent")
        return self.result

    def current_state(self, *, scenario, agent_result, attempt_id):
        self._stage("state")
        target = scenario.target_service or "payment"
        runbook_id = scenario.runbook_id or RunbookId.ROLLBACK_CONFIGURATION
        registry = load_runbook_registry(RUNBOOK_ROOT)
        runbook = registry.require(runbook_id)
        runtime = (
            ServiceRuntimeState.STOPPED
            if runbook_id is RunbookId.RESTART_SERVICE
            else ServiceRuntimeState.RUNNING_HEALTHY
        )
        preconditions = tuple(
            PreconditionObservation(precondition=item, satisfied=True)
            for item in runbook.preconditions
        )
        return build_current_state_snapshot(
            run_id=agent_result.run_id,
            attempt_id=attempt_id,
            docker_boundary=DockerBoundary.LOCAL_UNIX,
            docker_context_identity="1" * 64,
            daemon_identity="2" * 64,
            sandbox_identity="ecomsre-live-sandbox-v1",
            ownership_digest="3" * 64,
            ownership_status=OwnershipStatus.PROVEN,
            target_logical_service=target,
            service_runtime_state=runtime,
            configuration_state_digest=(
                "4" * 64 if runbook_id is RunbookId.ROLLBACK_CONFIGURATION else None
            ),
            baseline_digest="5" * 64,
            active_transaction_count=0,
            prior_forward_step_count=0,
            preconditions=preconditions,
            observed_at_start=NOW,
            observed_at_end=NOW + timedelta(seconds=1),
            observation_monotonic_duration_ms=1000,
        )

    def controls(self, current_state):
        self._stage("controls")
        return Controls(current_state, self)

    def capture_recovery_windows(self, forward_execution):
        del forward_execution
        self._stage("recovery")
        return _windows(slope=0.0 if self.scenario is LiveScenario.EMAIL else None)

    def email_leak_flag_off(self):
        return self.flag_off if self.scenario is LiveScenario.EMAIL else None

    def restore_baseline(self, baseline):
        del baseline
        self._stage("restore")
        return True

    @property
    def restoration_write_count(self):
        return self._restoration_write_count

    def revalidate_before_cleanup(self):
        self._stage("reauth_cleanup")

    def cleanup_owned(self, *, baseline_restored):
        del baseline_restored
        self._stage("cleanup")
        if self.fail_stage == "non_owned_drift":
            return CleanupObservation.non_owned_drift()
        return CleanupObservation.clean()


def _run(tmp_path, scenario, lifecycle, *, owned_claim=None):
    config = load_live_demo_config(ROOT / "config/dta-v2/live-demo.v1.json")
    spec = next(item for item in config.scenarios if item.scenario is scenario)
    registry = load_runbook_registry(RUNBOOK_ROOT)
    clock_tick = 0

    def controlled_utc_now():
        nonlocal clock_tick
        observed = NOW + timedelta(minutes=1, milliseconds=clock_tick)
        clock_tick += 1
        return observed

    attempt_id = (
        f"attempt-{scenario.value.casefold()}"
        if owned_claim is None
        else owned_claim.attempt_id
    )
    grant = (
        None
        if owned_claim is None
        else issue_owned_live_execution_grant(
            claim=owned_claim,
            lifecycle=lifecycle,
            _token=_OWNED_CAMPAIGN_TOKEN,
        )
    )
    return run_live_attempt(
        private_root=tmp_path / scenario.value.casefold(),
        attempt_id=attempt_id,
        config=config,
        scenario=spec,
        freeze=_freeze(config, registry),
        registry=registry,
        master_authorization=master_authorization(registry),
        lifecycle=lifecycle,
        as_of=NOW + timedelta(minutes=1),
        utc_now=controlled_utc_now,
        forbidden_secrets=("provider-secret",),
        _owned_execution_grant=grant,
    )


@pytest.mark.parametrize(
    "scenario",
    (LiveScenario.PAYMENT, LiveScenario.RECOMMENDATION, LiveScenario.EMAIL),
)
def test_three_positive_fake_lifecycles_are_offline_pass(
    tmp_path: Path,
    scenario: LiveScenario,
) -> None:
    closure = _run(tmp_path, scenario, FakeLifecycle(scenario, _positive_agent(scenario)))

    assert closure.terminal is LiveAttemptTerminal.OFFLINE_PASS
    assert closure.mode is LiveAttemptMode.FAKE_REPLAY
    assert closure.counters.fault_injection_count == 1
    assert closure.counters.recovery_window_count == 2
    assert closure.counters.forward_step_count == (
        2 if scenario is LiveScenario.EMAIL else 1
    )
    assert closure.counters.unsafe_write_attempt_count == 0
    assert closure.counters.arbitrary_shell_attempt_count == 0
    assert closure.baseline_restored is True
    assert closure.journal[-1].stage is LiveAttemptStage.CLOSED


def test_no_fault_fake_lifecycle_is_typed_zero_write_offline_pass(
    tmp_path: Path,
) -> None:
    lifecycle = FakeLifecycle(LiveScenario.NO_FAULT, _no_fault_agent())
    closure = _run(tmp_path, LiveScenario.NO_FAULT, lifecycle)

    assert closure.terminal is LiveAttemptTerminal.OFFLINE_PASS
    assert closure.admission_verdict.value == "DENY"
    assert closure.authorization_sha256 is None
    assert closure.transaction_sha256 is None
    assert closure.counters.fault_injection_count == 0
    assert closure.counters.forward_step_count == 0
    assert not any(call.startswith("restore_") for call in lifecycle.calls[:-2])

    public = build_public_live_attempt_report(closure)
    payload = public.model_dump_json()
    assert closure.run_id not in payload
    assert closure.closure_sha256 not in payload
    assert "/" not in payload
    assert "container" not in payload.casefold()
    assert "provider-secret" not in payload


def test_stage14_safe_aggregate_contains_complete_bounded_attempt_surfaces(
    tmp_path: Path,
) -> None:
    closures = tuple(
        _run(
            tmp_path / f"case-{index}",
            scenario,
            FakeLifecycle(
                scenario,
                _no_fault_agent()
                if scenario is LiveScenario.NO_FAULT
                else _positive_agent(scenario),
            ),
        )
        for index, scenario in enumerate(
            (
                LiveScenario.NO_FAULT,
                LiveScenario.PAYMENT,
                LiveScenario.RECOMMENDATION,
                LiveScenario.EMAIL,
            )
        )
    )

    aggregate = build_public_live_campaign_report(closures)
    payload = aggregate.model_dump_json()

    assert aggregate.terminal == "DTA_V2_LIVE_DEMO_REVIEW_REQUIRED"
    assert tuple(item.scenario for item in aggregate.attempts) == (
        LiveScenario.NO_FAULT,
        LiveScenario.PAYMENT,
        LiveScenario.RECOMMENDATION,
        LiveScenario.EMAIL,
    )
    assert aggregate.attempts[-1].step_receipts
    assert aggregate.attempts[-1].recovery_windows
    assert aggregate.attempts[-1].verifier is not None
    assert aggregate.attempts[-1].diagnosis is not None
    assert aggregate.attempts[-1].candidate_set
    assert aggregate.attempts[-1].operational_admission is not None
    assert aggregate.attempts[-1].authorization_present is True
    assert aggregate.attempts[-1].authorization is not None
    assert "evidence://" not in payload
    assert not any(closure.run_id in payload for closure in closures)
    assert "container_id" not in payload.casefold()
    assert "DTA v2 Local Live Demo" in render_public_live_demo_markdown(aggregate)
    assert "本地演示" in render_public_live_demo_human_brief(aggregate)


def test_generic_owned_mode_protocol_cannot_mint_live_pass(tmp_path: Path) -> None:
    class FabricatedOwnedLifecycle(FakeLifecycle):
        mode = LiveAttemptMode.OWNED_LOCAL

    with pytest.raises(TypeError, match="campaign-issued"):
        _run(
            tmp_path,
            LiveScenario.NO_FAULT,
            FabricatedOwnedLifecycle(LiveScenario.NO_FAULT, _no_fault_agent()),
        )


def test_no_fault_abstain_seals_typed_deny_without_proposal_or_child_authority(
    tmp_path: Path,
) -> None:
    lifecycle = FakeLifecycle(LiveScenario.NO_FAULT, _no_fault_abstain_agent())

    closure = _run(tmp_path, LiveScenario.NO_FAULT, lifecycle)

    assert closure.terminal is LiveAttemptTerminal.OFFLINE_PASS
    assert closure.agent_terminal == Terminal.ABSTAIN.value
    assert closure.proposal_sha256 is None
    assert closure.admission_verdict.value == "DENY"
    assert closure.authorization_sha256 is None
    assert closure.transaction_sha256 is None
    assert closure.counters.runbook_proposal_count == 0
    assert closure.counters.admitted_runbook_count == 0
    assert closure.counters.forward_step_count == 0


def test_email_partial_failure_has_two_receipts_no_third_or_compensation(
    tmp_path: Path,
) -> None:
    lifecycle = FakeLifecycle(
        LiveScenario.EMAIL,
        _positive_agent(LiveScenario.EMAIL),
        fail_stage="restart_email",
    )
    closure = _run(tmp_path, LiveScenario.EMAIL, lifecycle)

    assert closure.terminal is LiveAttemptTerminal.FAIL
    assert closure.primary_failure_code is LiveFailureCode.PARTIALLY_APPLIED
    assert closure.counters.forward_step_count == 2
    assert closure.counters.rollback_or_compensation_count == 0
    assert lifecycle.flag_off is True
    assert lifecycle.calls.count("restart_email") == 1
    assert "restart_email" == lifecycle.calls[lifecycle.calls.index("restart_email")]


@pytest.mark.parametrize(
    "fail_stage",
    ("reauth_second_write", "drift_second_write"),
)
def test_email_second_write_revalidation_failure_seals_partial_receipt_and_cleanup(
    tmp_path: Path,
    fail_stage: str,
) -> None:
    lifecycle = FakeLifecycle(
        LiveScenario.EMAIL,
        _positive_agent(LiveScenario.EMAIL),
        fail_stage=fail_stage,
    )

    closure = _run(tmp_path, LiveScenario.EMAIL, lifecycle)

    assert closure.terminal is LiveAttemptTerminal.FAIL
    assert closure.primary_failure_code is LiveFailureCode.PARTIALLY_APPLIED
    assert closure.transaction_terminal.value == "PARTIALLY_APPLIED"
    assert closure.counters.forward_step_count == 1
    assert len(closure.receipts) == 1
    assert closure.receipts[0].step_id.value == "DISABLE_LEAK_FLAG"
    assert lifecycle.flag_off is True
    assert lifecycle.calls.count("disable_email_leak") == 1
    assert "restart_email" not in lifecycle.calls
    assert closure.baseline_restored is True
    assert closure.cleanup_terminal.value == "CLEAN"


def test_false_fault_impact_records_fail_not_pass(tmp_path: Path) -> None:
    lifecycle = FakeLifecycle(
        LiveScenario.PAYMENT,
        _positive_agent(LiveScenario.PAYMENT),
        fail_stage="impact_false",
    )

    closure = _run(tmp_path, LiveScenario.PAYMENT, lifecycle)

    impact = next(
        event
        for event in closure.journal
        if event.stage is LiveAttemptStage.FAULT_IMPACT_VERIFIED
    )
    assert closure.primary_failure_code is LiveFailureCode.FAULT_IMPACT_FAILED
    assert impact.status.value == "FAIL"
    assert closure.counters.fault_injection_count == 1
    assert closure.counters.fault_injection_applied_count == 1


def test_receipt_journal_failure_seals_evidence_failure_before_second_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = FakeLifecycle(
        LiveScenario.EMAIL,
        _positive_agent(LiveScenario.EMAIL),
    )
    monkeypatch.setattr(
        PrivateLiveAttemptJournal,
        "append",
        lambda self, receipt: (_ for _ in ()).throw(OSError("private detail")),
    )

    closure = _run(tmp_path, LiveScenario.EMAIL, lifecycle)

    assert closure.primary_failure_code is LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
    assert closure.counters.forward_step_count == 1
    assert lifecycle.calls.count("disable_email_leak") == 1
    assert "restart_email" not in lifecycle.calls
    assert (
        tmp_path
        / "email"
        / "artifacts"
        / "receipt-persistence-fallback.json"
    ).is_file()


def test_event_persistence_failure_after_fault_halts_agent_and_forward_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = FakeLifecycle(
        LiveScenario.PAYMENT,
        _positive_agent(LiveScenario.PAYMENT),
    )
    original = PrivateLiveAttemptJournal.append_event
    failed = False

    def fail_fault_event_once(self, event):
        nonlocal failed
        if not failed and event.stage is LiveAttemptStage.FAULT_INJECTED:
            failed = True
            raise OSError("private event persistence detail")
        return original(self, event)

    monkeypatch.setattr(PrivateLiveAttemptJournal, "append_event", fail_fault_event_once)

    closure = _run(tmp_path, LiveScenario.PAYMENT, lifecycle)

    assert failed is True
    assert closure.terminal is LiveAttemptTerminal.FAIL
    assert closure.primary_failure_code is LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
    assert "agent" not in lifecycle.calls
    assert "restore_payment" not in lifecycle.calls
    assert closure.counters.forward_step_count == 0
    assert closure.baseline_restored is True
    assert closure.cleanup_terminal.value == "CLEAN"


def test_primary_closure_persistence_failure_retains_typed_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        PrivateLiveAttemptJournal,
        "persist_closure",
        lambda self, closure: (_ for _ in ()).throw(OSError("private detail")),
    )

    closure = _run(
        tmp_path,
        LiveScenario.NO_FAULT,
        FakeLifecycle(LiveScenario.NO_FAULT, _no_fault_agent()),
    )

    assert closure.terminal is LiveAttemptTerminal.FAIL
    assert closure.primary_failure_code is LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
    closed = [
        event for event in closure.journal if event.stage is LiveAttemptStage.CLOSED
    ]
    assert len(closed) == 1
    assert closed[0].status.value == "FAIL"
    assert closed[0].failure_code is LiveFailureCode.EVIDENCE_PERSISTENCE_FAILED
    assert (tmp_path / "no_fault/live-attempt-closure-fallback.json").is_file()


def test_post_write_closure_exception_keeps_only_exact_primary_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = PrivateLiveAttemptJournal.persist_closure

    def write_then_raise(self, closure):
        original(self, closure)
        raise OSError("post-write private detail")

    monkeypatch.setattr(
        PrivateLiveAttemptJournal,
        "persist_closure",
        write_then_raise,
    )

    closure = _run(
        tmp_path,
        LiveScenario.NO_FAULT,
        FakeLifecycle(LiveScenario.NO_FAULT, _no_fault_agent()),
    )
    root = tmp_path / "no_fault"

    assert closure.terminal is LiveAttemptTerminal.OFFLINE_PASS
    assert closure.failure_code is None
    assert (root / "live-attempt-closure.json").is_file()
    assert not (root / "live-attempt-closure-fallback.json").exists()
    assert LiveAttemptClosure.model_validate_json(
        (root / "live-attempt-closure.json").read_text(encoding="utf-8")
    ) == closure


@pytest.mark.parametrize(
    ("ordinal", "scenario", "fail_stage"),
    (
        (1, LiveScenario.NO_FAULT, "freeze"),
        (2, LiveScenario.PAYMENT, "agent"),
    ),
)
def test_owned_failure_closure_uses_authenticated_claim_identity_before_agent(
    tmp_path: Path,
    ordinal: int,
    scenario: LiveScenario,
    fail_stage: str,
) -> None:
    class OwnedFailureLifecycle(FakeLifecycle):
        mode = LiveAttemptMode.OWNED_LOCAL

    claim = build_live_campaign_attempt_claim(
        campaign_id="owned-failure-lineage",
        ordinal=ordinal,
        change_sha256="c" * 64,
    )
    lifecycle = OwnedFailureLifecycle(
        scenario,
        _no_fault_agent() if scenario is LiveScenario.NO_FAULT else _positive_agent(scenario),
        fail_stage=fail_stage,
    )

    closure = _run(tmp_path, scenario, lifecycle, owned_claim=claim)

    assert closure.mode is LiveAttemptMode.OWNED_LOCAL
    assert closure.terminal is LiveAttemptTerminal.FAIL
    assert closure.attempt_id == claim.attempt_id
    assert closure.run_id == claim.run_id
    assert closure.scenario is claim.scenario
    if fail_stage == "agent":
        assert closure.baseline_restored is True
        assert closure.cleanup_terminal.value == "CLEAN"


@pytest.mark.parametrize(
    ("fail_stage", "expected"),
    [
        ("freeze", LiveFailureCode.FREEZE_MISMATCH),
        ("admit", LiveFailureCode.ENVIRONMENT_ADMISSION_FAILED),
        ("start", LiveFailureCode.START_FAILED),
        ("ready", LiveFailureCode.READINESS_FAILED),
        ("baseline", LiveFailureCode.BASELINE_FAILED),
        ("fault", LiveFailureCode.FAULT_INJECTION_FAILED),
        ("impact", LiveFailureCode.FAULT_IMPACT_FAILED),
        ("agent", LiveFailureCode.AGENT_FAILED),
        ("state", LiveFailureCode.STATE_ADMISSION_FAILED),
        ("restore_payment", LiveFailureCode.EXECUTION_FAILED),
        ("recovery", LiveFailureCode.RECOVERY_CAPTURE_FAILED),
        ("restore", LiveFailureCode.BASELINE_RESTORATION_FAILED),
        ("cleanup", LiveFailureCode.CLEANUP_FAILED),
        ("non_owned_drift", LiveFailureCode.CLEANUP_BLOCKED),
    ],
)
def test_failure_matrix_always_closes_and_cleans_started_attempts(
    tmp_path: Path,
    fail_stage: str,
    expected: LiveFailureCode,
) -> None:
    lifecycle = FakeLifecycle(
        LiveScenario.PAYMENT,
        _positive_agent(LiveScenario.PAYMENT),
        fail_stage=fail_stage,
    )
    closure = _run(tmp_path, LiveScenario.PAYMENT, lifecycle)

    assert closure.terminal is LiveAttemptTerminal.FAIL
    assert closure.failure_code is expected
    assert closure.journal[-1].stage is LiveAttemptStage.CLOSED
    assert (tmp_path / "payment/live-attempt-closure.json").is_file()
    if fail_stage not in {"freeze", "admit"}:
        assert closure.cleanup_attempted is True
    if fail_stage == "baseline":
        assert "restore" in lifecycle.calls
        assert lifecycle.calls.index("restore") < lifecycle.calls.index("cleanup")


def test_pr_e_frozen_bytes_are_unchanged() -> None:
    expected = {
        ROOT / "config/dta-v2/agent-identity.v1.json": (
            "e5608f6dc2f40e8026b42468ed437daa562661abde02cbaf0a880fd00a456a6e"
        ),
        ROOT / "config/dta-v2/evaluation/manifest.json": (
            "f6ff2f222a725377f664b1ae70c7a300c6c2c4f9bfacc8420e3c5861ddcf697e"
        ),
    }
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in expected
    } == expected
    manifest = json.loads(
        (ROOT / "config/dta-v2/evaluation/manifest.json").read_text(encoding="utf-8")
    )
    assert len(manifest["held_out_case_sha256s"]) == 3
    assert len(manifest["held_out_truth_sha256s"]) == 3
    assert manifest["manifest_sha256"] == (
        "9ae255ea385a8bfc486032bdb26c6bf76b8bcc9cfa2a06417282cb2287542d92"
    )
