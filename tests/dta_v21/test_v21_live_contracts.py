from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from pydantic_core import to_jsonable_python

from ecomsre.dta_v2.v21 import live_cli as live_cli_module
from ecomsre.dta_v2.read_tools import FakeReadBackend, InvestigationReadTools
from ecomsre.dta_v2.v21.agent import (
    AgentRunTerminalV21,
    DtaAgentRunResultV21,
    _build_result,
)
from ecomsre.dta_v2.v21.contracts import (
    ActionDispositionV21,
    ActionParameterV21,
    FaultDomainV21,
    FaultMechanismV21,
    RunbookIdV21,
    TerminalV21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.agent_contracts import (
    ActionSelectionDecisionV21,
    AgentArmV21,
    build_alert_context_v21,
    build_action_proposal_v21,
    build_candidate_action_view_v21,
)
from ecomsre.dta_v2.v21.candidate_filter import filter_runbook_candidates
from ecomsre.dta_v2.v21.live_contracts import (
    LIVE_CAMPAIGN_ORDER_V21,
    LiveAdBaselineWindowV21,
    LiveAttemptClosureV21,
    LiveBaselineEvidenceV21,
    LiveBusinessBaselineWindowV21,
    LiveCampaignClosureV21,
    LiveCurrentStateV21,
    LiveEnvironmentAdmissionV21,
    LiveFaultImpactEvidenceV21,
    LiveReadinessV21,
    LiveScenarioV21,
    ServiceRecoveryWindowV21,
    build_service_recovery_result_v21,
    build_service_recovery_window_v21,
    load_live_demo_config_v21,
)
from ecomsre.dta_v2.v21.live_execution import (
    FixedLiveControlsV21,
    LiveMasterAuthorizationV21,
    LiveDispatchIntentV21,
    LivePostWriteStateV21,
    LiveStepReceiptV21,
    admit_live_action_v21,
    deny_no_fault_live_action_v21,
    execute_fixed_live_step_v21,
)
from ecomsre.dta_v2.v21.live_cli import (
    _execution_scope_sha256,
    _pending_disposition_payload,
    run_closeout,
    run_finalize,
    run_verify,
)
from ecomsre.dta_v2.v21.live_protocol import (
    AD_CPU_RESOURCE_QUERY_ID_V1,
    build_ad_cpu_business_guardrail_result,
    build_ad_cpu_resource_recovery_result,
    build_ad_cpu_resource_window,
    load_ad_cpu_resource_recovery_protocol_v1,
)
from ecomsre.dta_v2.v21.live_reporting import (
    build_public_live_report_v21,
    render_public_final_summary_v21,
    render_public_human_brief_v21,
    render_public_interview_brief_v21,
    render_public_live_markdown_v21,
    verify_public_live_report_v21,
)
from ecomsre.dta_v2.v21.live_runner import _build_attempt_closure
from ecomsre.dta_v2.v21.identity import build_three_arm_identities_v21
from ecomsre.dta_v2.v21.registry import (
    load_default_runbook_registry,
    load_default_scenario_registries,
)
from ecomsre.dta_v2.v21.replay import build_replay_diagnosis


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config/dta-v21/live/live-demo.v1.json"


class _BoundProvider:
    def __init__(self) -> None:
        self.identity = next(
            item
            for item in build_three_arm_identities_v21(
                model_id="gpt-5.4-mini-2026-03-17", max_completion_tokens=1600
            )
            if item.arm is AgentArmV21.EVIDENCE_GUIDED_PLANNER
        )
        self.attempted_calls = 1


def _result(
    *,
    runbook_id: RunbookIdV21,
    target_service: str,
    parameters: tuple[ActionParameterV21, ...] = (),
) -> DtaAgentRunResultV21:
    registry = load_default_runbook_registry(REPO_ROOT)
    runbook = registry.require(runbook_id)
    domain, mechanism = {
        RunbookIdV21.MITIGATE_CPU_SATURATION: (
            FaultDomainV21.LOCAL_RESOURCE,
            FaultMechanismV21.CPU_SATURATION,
        ),
        RunbookIdV21.RESTORE_SERVICE_AVAILABILITY: (
            FaultDomainV21.SERVICE_RUNTIME,
            FaultMechanismV21.SERVICE_UNAVAILABLE,
        ),
    }[runbook_id]
    diagnosis, evidence = build_replay_diagnosis(
        run_id="a" * 32,
        terminal=TerminalV21.COMPLETED,
        root_service=target_service,
        fault_domain=domain,
        mechanism=mechanism,
        evidence_sources=tuple(
            item.value for item in runbook.required_evidence_for_target(target_service)
        ),
    )
    candidates = filter_runbook_candidates(
        diagnosis=diagnosis,
        diagnosis_evidence=evidence,
        registry=registry,
        exact_target=target_service,
    )
    decision = ActionSelectionDecisionV21(
        schema_version="dta-v21.action-selection-decision.v1",
        disposition=ActionDispositionV21.EXECUTE_RUNBOOK,
        runbook_id=runbook_id,
        target_service=target_service,
        parameters=parameters,
        supporting_evidence_refs=diagnosis.supporting_evidence_refs,
        rationale="Use the exact trusted live Runbook.",
    )
    proposal = build_action_proposal_v21(
        diagnosis=diagnosis,
        resolved_evidence=evidence,
        candidate_set=candidates,
        candidate_view=build_candidate_action_view_v21(candidates),
        registry=registry,
        decision=decision,
    )
    observer, _, _ = load_default_scenario_registries(REPO_ROOT)
    scenario_id = {
        "ad": "dta21-dev-001",
        "email": "dta21-dev-002",
        "product-catalog": "dta21-dev-003",
    }[target_service]
    scenario = next(
        item for item in observer.scenarios if item.scenario_id == scenario_id
    )
    started = datetime(2026, 8, 18, 2, 59, tzinfo=timezone.utc)
    context = build_alert_context_v21(
        scenario=scenario,
        run_id=diagnosis.run_id,
        started_at=started,
        ended_at=started + timedelta(minutes=1),
    )
    return _build_result(
        arm=AgentArmV21.EVIDENCE_GUIDED_PLANNER,
        context=context,
        provider=_BoundProvider(),  # type: ignore[arg-type]
        terminal=AgentRunTerminalV21.COMPLETED,
        failure_code=None,
        tools=InvestigationReadTools(
            run_id=diagnosis.run_id, backend=FakeReadBackend.healthy()
        ),
        turns=(),
        diagnosis=diagnosis,
        resolved_evidence=evidence,
        candidate_set=candidates,
        candidate_view=build_candidate_action_view_v21(candidates),
        action_proposal=proposal,
    )


def _no_fault_result() -> DtaAgentRunResultV21:
    registry = load_default_runbook_registry(REPO_ROOT)
    diagnosis, evidence = build_replay_diagnosis(
        run_id="a" * 32,
        terminal=TerminalV21.COMPLETED,
        root_service=None,
        fault_domain=None,
        mechanism=None,
        evidence_sources=("METRICS", "RUNTIME"),
    )
    candidates = filter_runbook_candidates(
        diagnosis=diagnosis,
        diagnosis_evidence=evidence,
        registry=registry,
        exact_target=None,
    )
    candidate_view = build_candidate_action_view_v21(candidates)
    proposal = build_action_proposal_v21(
        diagnosis=diagnosis,
        resolved_evidence=evidence,
        candidate_set=candidates,
        candidate_view=candidate_view,
        registry=registry,
        decision=ActionSelectionDecisionV21(
            schema_version="dta-v21.action-selection-decision.v1",
            disposition=ActionDispositionV21.NO_ACTION,
            runbook_id=None,
            target_service=None,
            parameters=(),
            supporting_evidence_refs=diagnosis.supporting_evidence_refs,
            rationale="No bounded write is supported.",
        ),
    )
    observer, _, _ = load_default_scenario_registries(REPO_ROOT)
    scenario = next(
        item for item in observer.scenarios if item.scenario_id == "dta21-dev-005"
    )
    started = datetime(2026, 8, 18, 2, 59, tzinfo=timezone.utc)
    return _build_result(
        arm=AgentArmV21.EVIDENCE_GUIDED_PLANNER,
        context=build_alert_context_v21(
            scenario=scenario,
            run_id=diagnosis.run_id,
            started_at=started,
            ended_at=started + timedelta(minutes=1),
        ),
        provider=_BoundProvider(),  # type: ignore[arg-type]
        terminal=AgentRunTerminalV21.COMPLETED,
        failure_code=None,
        tools=InvestigationReadTools(
            run_id=diagnosis.run_id, backend=FakeReadBackend.healthy()
        ),
        turns=(),
        diagnosis=diagnosis,
        resolved_evidence=evidence,
        candidate_set=candidates,
        candidate_view=candidate_view,
        action_proposal=proposal,
    )


def _state(
    scenario: LiveScenarioV21,
    *,
    target_service: str,
    attempt_id: str | None = None,
    ad_high_cpu_active: bool = False,
    target_runtime_stopped: bool = False,
) -> LiveCurrentStateV21:
    observed = datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc)
    return LiveCurrentStateV21.build(
        run_id="a" * 32,
        attempt_id=attempt_id or f"attempt-{scenario.value.casefold()}",
        scenario=scenario,
        target_service=target_service,
        owned_target_identity_sha256="e" * 64,
        daemon_identity_sha256="f" * 64,
        docker_boundary="LOCAL_UNIX_DOCKER",
        docker_context_sha256="3" * 64,
        ownership_scope_sha256="4" * 64,
        sandbox_identity_sha256="5" * 64,
        baseline_state_sha256="1" * 64,
        current_state_sha256="2" * 64,
        ad_high_cpu_active=ad_high_cpu_active,
        target_runtime_stopped=target_runtime_stopped,
        fault_operation_count=1,
        prior_forward_step_count=0,
        active_transaction_count=0,
        non_owned_changes=0,
        observation_started_at=observed,
        observed_at=observed,
    )


def _master() -> LiveMasterAuthorizationV21:
    return LiveMasterAuthorizationV21.build(
        issued_at=datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)
    )


def _readiness(
    *, code_head: str, master: LiveMasterAuthorizationV21
) -> LiveReadinessV21:
    config = load_live_demo_config_v21(CONFIG_PATH)
    protocol = load_ad_cpu_resource_recovery_protocol_v1(
        REPO_ROOT / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
    )
    return LiveReadinessV21.build(
        terminal="DTA_V21_PR_F_PRELIVE_READY",
        readiness_attempt_id="readiness-0001",
        code_head=code_head,
        exact_head_ci_success=True,
        exact_head_ci_run_id=123,
        exact_head_ci_run_url="https://github.com/example/repo/actions/runs/123",
        branch="codex/dta-v21-p0-pr-f-live-closeout",
        origin_main_is_ancestor=True,
        protocol_sha256=protocol.protocol_sha256,
        live_config_sha256=config.config_sha256,
        planner_identity_sha256=config.planner_identity_sha256,
        provider_model=config.provider_model,
        pr_e_claim="DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED",
        docker_boundary="LOCAL_UNIX_DOCKER",
        resolved_compose_sha256="1" * 64,
        baseline_flag_document_sha256="2" * 64,
        owned_resource_collisions=0,
        required_ports_available=True,
        cleanup_readiness="OWNED_SCOPE_ADMITTED",
        private_permissions="0700_DIRECTORIES_0600_FILES",
        master_authorization_sha256=master.authorization_sha256,
    )


def test_live_config_freezes_exact_four_slot_order_and_source_bindings() -> None:
    config = load_live_demo_config_v21(CONFIG_PATH)

    assert LIVE_CAMPAIGN_ORDER_V21 == (
        LiveScenarioV21.NO_FAULT,
        LiveScenarioV21.AD_CPU_SATURATION,
        LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
        LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE,
    )
    assert tuple(item.scenario for item in config.scenarios) == LIVE_CAMPAIGN_ORDER_V21
    assert config.planner_identity_sha256 == (
        "80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d"
    )
    assert config.provider_model == "gpt-5.4-mini-2026-03-17"
    assert config.protocol_sha256 == (
        "c983b9be95b532cdbb8fb5358af92055e633fd767693e9dc65743b3e80a77517"
    )


@pytest.mark.parametrize(
    ("scenario", "runbook_id", "target", "state_changes", "expected_step"),
    [
        (
            LiveScenarioV21.AD_CPU_SATURATION,
            RunbookIdV21.MITIGATE_CPU_SATURATION,
            "ad",
            {"ad_high_cpu_active": True},
            "DISABLE_AD_HIGH_CPU_FLAG",
        ),
        (
            LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
            RunbookIdV21.RESTORE_SERVICE_AVAILABILITY,
            "email",
            {"target_runtime_stopped": True},
            "START_OWNED_SERVICE",
        ),
        (
            LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE,
            RunbookIdV21.RESTORE_SERVICE_AVAILABILITY,
            "product-catalog",
            {"target_runtime_stopped": True},
            "START_OWNED_SERVICE",
        ),
    ],
)
def test_operational_admission_allows_only_the_exact_fixed_step(
    scenario: LiveScenarioV21,
    runbook_id: RunbookIdV21,
    target: str,
    state_changes: dict[str, bool],
    expected_step: str,
) -> None:
    registry = load_default_runbook_registry(REPO_ROOT)
    parameters: tuple[ActionParameterV21, ...] = ()
    if runbook_id is RunbookIdV21.RESTORE_SERVICE_AVAILABILITY:
        parameters = (ActionParameterV21(name="wait_for_health_seconds", value=30),)
    state = _state(
        scenario,
        target_service=target,
        ad_high_cpu_active=state_changes.get("ad_high_cpu_active", False),
        target_runtime_stopped=state_changes.get("target_runtime_stopped", False),
    )
    result = _result(
        runbook_id=runbook_id,
        target_service=target,
        parameters=parameters,
    )
    admission, authorization = admit_live_action_v21(
        scenario=scenario,
        agent_result=result,
        registry=registry,
        current_state=state,
        master_authorization=_master(),
        issued_at=datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc),
    )

    assert admission.verdict == "ALLOW"
    assert admission.admitted_step == expected_step
    assert admission.maximum_forward_steps == 1
    assert authorization.current_state_snapshot_sha256 == state.snapshot_sha256


def test_operational_admission_rejects_wrong_target_generic_material_and_state() -> (
    None
):
    registry = load_default_runbook_registry(REPO_ROOT)
    issued = datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc)
    result = _result(
        runbook_id=RunbookIdV21.MITIGATE_CPU_SATURATION,
        target_service="ad",
    )

    with pytest.raises(ValueError, match="scenario target"):
        admit_live_action_v21(
            scenario=LiveScenarioV21.AD_CPU_SATURATION,
            agent_result=result,
            registry=registry,
            current_state=_state(
                LiveScenarioV21.AD_CPU_SATURATION,
                target_service="email",
                ad_high_cpu_active=True,
            ),
            master_authorization=_master(),
            issued_at=issued,
        )

    with pytest.raises(ValueError, match="active"):
        admit_live_action_v21(
            scenario=LiveScenarioV21.AD_CPU_SATURATION,
            agent_result=result,
            registry=registry,
            current_state=_state(
                LiveScenarioV21.AD_CPU_SATURATION,
                target_service="ad",
            ),
            master_authorization=_master(),
            issued_at=issued,
        )


def test_no_fault_persists_a_hash_bound_zero_write_denial() -> None:
    result = _no_fault_result()
    admission = deny_no_fault_live_action_v21(
        agent_result=result,
        registry=load_default_runbook_registry(REPO_ROOT),
        attempt_id="attempt-no-fault",
        master_authorization=_master(),
    )

    assert admission.verdict == "DENY"
    assert admission.maximum_forward_steps == 0
    assert admission.agent_result_sha256 == result.result_sha256


def test_operational_admission_rejects_a_candidate_view_not_bound_to_result() -> None:
    result = _result(
        runbook_id=RunbookIdV21.MITIGATE_CPU_SATURATION,
        target_service="ad",
    )
    empty_view = _no_fault_result().candidate_view
    assert empty_view is not None
    draft = result.model_copy(
        update={"candidate_view": empty_view, "result_sha256": "0" * 64}
    )
    payload = draft.model_dump(mode="python", exclude={"result_sha256"})
    altered = DtaAgentRunResultV21.model_validate(
        {
            **payload,
            "result_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"result_sha256"})
            ),
        }
    )

    with pytest.raises(ValueError, match="CandidateActionView"):
        admit_live_action_v21(
            scenario=LiveScenarioV21.AD_CPU_SATURATION,
            agent_result=altered,
            registry=load_default_runbook_registry(REPO_ROOT),
            current_state=_state(
                LiveScenarioV21.AD_CPU_SATURATION,
                target_service="ad",
                ad_high_cpu_active=True,
            ),
            master_authorization=_master(),
            issued_at=datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc),
        )


class _Controls(FixedLiveControlsV21):
    def __init__(self, state: LiveCurrentStateV21) -> None:
        self.state = state
        self.calls: list[str] = []

    def revalidate(self) -> LiveCurrentStateV21:
        return self.state

    def disable_ad_high_cpu_flag(self) -> None:
        self.calls.append("DISABLE_AD_HIGH_CPU_FLAG")

    def start_owned_service(self, *, wait_for_health_seconds: int) -> None:
        self.calls.append(f"START_OWNED_SERVICE:{wait_for_health_seconds}")

    def observe_postcondition(self, *, step, observed_at) -> LivePostWriteStateV21:
        return LivePostWriteStateV21.build(
            run_id=self.state.run_id,
            attempt_id=self.state.attempt_id,
            scenario=self.state.scenario,
            target_service=self.state.target_service,
            ad_high_cpu_active=False,
            target_runtime_stopped=False,
            forward_step_count=1,
            non_owned_changes=0,
            observed_at=observed_at,
        )


class _Journal:
    def __init__(self) -> None:
        self.intents: list[LiveDispatchIntentV21] = []
        self.postconditions: list[LivePostWriteStateV21] = []
        self.receipts: list[LiveStepReceiptV21] = []

    def record_intent(self, intent: LiveDispatchIntentV21) -> None:
        self.intents.append(intent)

    def record_postcondition(self, state: LivePostWriteStateV21) -> None:
        self.postconditions.append(state)

    def append(self, receipt: LiveStepReceiptV21) -> None:
        self.receipts.append(receipt)


def test_fixed_executor_runs_one_registry_step_and_emits_a_bound_receipt() -> None:
    registry = load_default_runbook_registry(REPO_ROOT)
    issued = datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc)
    state = _state(
        LiveScenarioV21.AD_CPU_SATURATION,
        target_service="ad",
        ad_high_cpu_active=True,
    )
    result = _result(
        runbook_id=RunbookIdV21.MITIGATE_CPU_SATURATION,
        target_service="ad",
    )
    admission, authorization = admit_live_action_v21(
        scenario=LiveScenarioV21.AD_CPU_SATURATION,
        agent_result=result,
        registry=registry,
        current_state=state,
        master_authorization=_master(),
        issued_at=issued,
    )
    controls = _Controls(state)
    journal = _Journal()
    assert result.action_proposal is not None

    receipt = execute_fixed_live_step_v21(
        proposal=result.action_proposal,
        current_state=state,
        admission=admission,
        authorization=authorization,
        controls=controls,
        receipt_journal=journal,
        observed_at=issued + timedelta(seconds=1),
    )

    assert controls.calls == ["DISABLE_AD_HIGH_CPU_FLAG"]
    assert receipt.step_id == "DISABLE_AD_HIGH_CPU_FLAG"
    assert receipt.forward_step_ordinal == 1
    assert receipt.outcome == "APPLIED"
    assert len(journal.intents) == 1
    assert len(journal.postconditions) == 1
    assert receipt.dispatch_intent_sha256 == journal.intents[0].intent_sha256
    assert receipt.after_state_sha256 == journal.postconditions[0].state_sha256
    assert journal.receipts == [receipt]


@pytest.mark.parametrize("wait_seconds", (5, 120))
def test_service_executor_uses_the_exact_admitted_health_wait(
    wait_seconds: int,
) -> None:
    registry = load_default_runbook_registry(REPO_ROOT)
    issued = datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc)
    state = _state(
        LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
        target_service="email",
        target_runtime_stopped=True,
    )
    result = _result(
        runbook_id=RunbookIdV21.RESTORE_SERVICE_AVAILABILITY,
        target_service="email",
        parameters=(
            ActionParameterV21(name="wait_for_health_seconds", value=wait_seconds),
        ),
    )
    admission, authorization = admit_live_action_v21(
        scenario=LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
        agent_result=result,
        registry=registry,
        current_state=state,
        master_authorization=_master(),
        issued_at=issued,
    )
    controls = _Controls(state)
    assert result.action_proposal is not None
    execute_fixed_live_step_v21(
        proposal=result.action_proposal,
        current_state=state,
        admission=admission,
        authorization=authorization,
        controls=controls,
        receipt_journal=_Journal(),
        observed_at=issued + timedelta(seconds=1),
    )
    assert controls.calls == [f"START_OWNED_SERVICE:{wait_seconds}"]


def test_fixed_executor_does_not_mutate_when_dispatch_intent_cannot_persist() -> None:
    registry = load_default_runbook_registry(REPO_ROOT)
    issued = datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc)
    state = _state(
        LiveScenarioV21.AD_CPU_SATURATION,
        target_service="ad",
        ad_high_cpu_active=True,
    )
    result = _result(
        runbook_id=RunbookIdV21.MITIGATE_CPU_SATURATION,
        target_service="ad",
    )
    admission, authorization = admit_live_action_v21(
        scenario=LiveScenarioV21.AD_CPU_SATURATION,
        agent_result=result,
        registry=registry,
        current_state=state,
        master_authorization=_master(),
        issued_at=issued,
    )
    controls = _Controls(state)

    class FailingJournal(_Journal):
        def record_intent(self, intent: LiveDispatchIntentV21) -> None:
            del intent
            raise OSError("simulated durable journal failure")

    assert result.action_proposal is not None
    with pytest.raises(OSError, match="durable journal"):
        execute_fixed_live_step_v21(
            proposal=result.action_proposal,
            current_state=state,
            admission=admission,
            authorization=authorization,
            controls=controls,
            receipt_journal=FailingJournal(),
            observed_at=issued + timedelta(seconds=1),
        )
    assert controls.calls == []


def test_public_projection_reverifies_agent_identity_before_projecting(
    tmp_path: Path,
) -> None:
    config = load_live_demo_config_v21(CONFIG_PATH)
    registry = load_default_runbook_registry(REPO_ROOT)
    protocol = load_ad_cpu_resource_recovery_protocol_v1(
        REPO_ROOT / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
    )
    result = _no_fault_result()
    wrong_identity = next(
        item
        for item in build_three_arm_identities_v21(
            model_id="different-frozen-model", max_completion_tokens=1600
        )
        if item.arm is AgentArmV21.EVIDENCE_GUIDED_PLANNER
    )
    draft = result.model_copy(
        update={"identity": wrong_identity, "result_sha256": "0" * 64}
    )
    forged = DtaAgentRunResultV21.model_validate(
        {
            **draft.model_dump(mode="python", exclude={"result_sha256"}),
            "result_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"result_sha256"})
            ),
        }
    )
    code_head = "c" * 40
    master = _master()
    readiness = _readiness(code_head=code_head, master=master)
    cleanup = {
        "baseline_restored": True,
        "owned_containers": 0,
        "owned_networks": 0,
        "owned_volumes": 0,
        "non_owned_resources_changed": False,
        "verdict": "CLEAN",
    }
    closures = []
    for ordinal, scenario in enumerate(LIVE_CAMPAIGN_ORDER_V21, start=1):
        positive = scenario is not LiveScenarioV21.NO_FAULT
        closure = _build_attempt_closure(
            scenario=scenario,
            attempt_id=f"attempt-{ordinal}-{code_head[:12]}",
            run_id=forged.run_id,
            planner_identity_sha256=config.planner_identity_sha256,
            readiness_sha256=readiness.readiness_sha256,
            environment_admission_sha256=str(ordinal + 4) * 64,
            baseline_evidence_sha256=str(ordinal + 5) * 64,
            fault_impact_sha256=format(ordinal + 6, "x") * 64,
            agent_result_sha256=(
                forged.result_sha256 if not positive else str(ordinal) * 64
            ),
            provider_attempted_calls=1,
            operational_admission_sha256=str(ordinal + 4) * 64,
            run_authorization_sha256=(str(ordinal + 4) * 64 if positive else None),
            receipt_sha256=(str(ordinal + 4) * 64 if positive else None),
            recovery_result_sha256=(str(ordinal + 4) * 64 if positive else None),
            cleanup=cleanup,
        )
        closures.append(closure)
    campaign_payload = {
        "schema_version": "dta-v21.live-campaign-closure.v1",
        "terminal": "DTA_V21_PR_F_LIVE_PORTFOLIO_PASS",
        "code_head": code_head,
        "protocol_sha256": protocol.protocol_sha256,
        "live_config_sha256": config.config_sha256,
        "planner_identity_sha256": config.planner_identity_sha256,
        "readiness_sha256": readiness.readiness_sha256,
        "attempts": tuple(closures),
        "unsafe_proposal_attempts": 0,
        "arbitrary_shell_attempts": 0,
        "non_owned_changes": 0,
        "all_baselines_restored": True,
        "all_cleanup_clean": True,
    }
    campaign = LiveCampaignClosureV21.model_validate(
        {
            **campaign_payload,
            "campaign_sha256": semantic_sha256(to_jsonable_python(campaign_payload)),
        }
    )
    private = tmp_path / "pr-f"
    campaign_root = private / "campaigns" / code_head
    campaign_root.mkdir(parents=True)
    readiness_root = private / "readiness" / code_head
    readiness_root.mkdir(parents=True)
    (private / "master-authorization.json").write_text(
        master.model_dump_json(), encoding="utf-8"
    )
    (readiness_root / "readiness.json").write_text(
        readiness.model_dump_json(), encoding="utf-8"
    )
    (campaign_root / "campaign-closure.json").write_text(
        campaign.model_dump_json(), encoding="utf-8"
    )
    first = closures[0]
    first_root = private / "attempts" / first.attempt_id
    first_root.mkdir(parents=True)
    (first_root / "attempt-claim.json").write_text(
        json.dumps(
            {
                "schema_version": "dta-v21.live-attempt-claim.v1",
                "attempt_id": first.attempt_id,
                "scenario": first.scenario.value,
                "ordinal": 1,
                "code_head": code_head,
                "master_authorization_sha256": master.authorization_sha256,
                "protocol_sha256": protocol.protocol_sha256,
                "live_config_sha256": config.config_sha256,
                "readiness_sha256": readiness.readiness_sha256,
            }
        ),
        encoding="utf-8",
    )
    (first_root / "attempt-terminal.json").write_text(
        first.model_dump_json(), encoding="utf-8"
    )
    observed = datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc)
    environment = LiveEnvironmentAdmissionV21.build(
        run_id=forged.run_id,
        attempt_id=first.attempt_id,
        scenario=first.scenario,
        code_head=code_head,
        readiness_sha256=readiness.readiness_sha256,
        resolved_compose_sha256=readiness.resolved_compose_sha256,
        baseline_flag_document_sha256=readiness.baseline_flag_document_sha256,
        docker_boundary="LOCAL_UNIX_DOCKER",
        daemon_identity_sha256="3" * 64,
        docker_context_sha256="4" * 64,
        config_bundle_sha256="5" * 64,
        resolved_sandbox_sha256="6" * 64,
        resolved_endpoints_sha256="7" * 64,
        ownership_scope_sha256="8" * 64,
        read_authority_sha256="9" * 64,
        owned_inventory_sha256="a" * 64,
        non_owned_baseline_snapshot_sha256="b" * 64,
        owned_container_count=25,
        owned_network_count=1,
        owned_volume_count=3,
        admitted_at=observed,
    )
    baseline_windows = tuple(
        LiveBusinessBaselineWindowV21.build(
            ordinal=ordinal,
            window_started_at=observed + timedelta(seconds=20 * (ordinal - 1)),
            window_ended_at=observed + timedelta(seconds=20 * ordinal),
            business_anchor_service="payment",
            business_error_rate="0",
            request_support="1",
            first_error_span_count=None,
        )
        for ordinal in (1, 2)
    )
    baseline = LiveBaselineEvidenceV21.build(
        run_id=forged.run_id,
        attempt_id=first.attempt_id,
        scenario=first.scenario,
        environment_admission_sha256=environment.environment_admission_sha256,
        started_at=observed,
        baseline_state_sha256="c" * 64,
        windows=baseline_windows,
    )
    fault = LiveFaultImpactEvidenceV21.build(
        run_id=forged.run_id,
        attempt_id=first.attempt_id,
        scenario=first.scenario,
        environment_admission_sha256=environment.environment_admission_sha256,
        baseline_evidence_sha256=baseline.evidence_sha256,
        baseline_state_sha256=baseline.baseline_state_sha256,
        fault_impact_kind="NO_FAULT",
        fault_operation_count=0,
        logical_service=None,
        business_anchor_service=None,
        baseline_unchanged=True,
        cpu_p95_percent=None,
        capacity_ratio=None,
        sample_count=None,
        safe=None,
        measurable=None,
        resource_fault_observed=None,
        business_impact_required=None,
        target_runtime_stopped=None,
        business_error_rate=None,
        first_error_span_count=None,
        business_impact_observed=None,
        same_owned_target_identity=None,
    )
    first_payload = first.model_dump(mode="python", exclude={"closure_sha256"})
    first_payload.update(
        environment_admission_sha256=environment.environment_admission_sha256,
        baseline_evidence_sha256=baseline.evidence_sha256,
        fault_impact_sha256=fault.evidence_sha256,
    )
    first = type(first).model_validate(
        {
            **first_payload,
            "closure_sha256": semantic_sha256(to_jsonable_python(first_payload)),
        }
    )
    closures[0] = first
    campaign_payload["attempts"] = tuple(closures)
    campaign = LiveCampaignClosureV21.model_validate(
        {
            **campaign_payload,
            "campaign_sha256": semantic_sha256(to_jsonable_python(campaign_payload)),
        }
    )
    (campaign_root / "campaign-closure.json").write_text(
        campaign.model_dump_json(), encoding="utf-8"
    )
    (first_root / "attempt-terminal.json").write_text(
        first.model_dump_json(), encoding="utf-8"
    )
    (first_root / "environment-admission.json").write_text(
        environment.model_dump_json(), encoding="utf-8"
    )
    (first_root / "baseline-evidence.json").write_text(
        baseline.model_dump_json(), encoding="utf-8"
    )
    (first_root / "fault-impact.json").write_text(
        fault.model_dump_json(), encoding="utf-8"
    )
    (first_root / "agent-result.json").write_text(
        forged.model_dump_json(), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="identity differs"):
        build_public_live_report_v21(
            prf_private_root=private,
            protocol=protocol,
            config=config,
            registry=registry,
            execution_code_head=code_head,
            execution_scope_sha256="e" * 64,
            base_readme_sha256="f" * 64,
            base_master_progress_sha256="9" * 64,
        )


def _write_model(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.model_dump_json(), encoding="utf-8")  # type: ignore[attr-defined]


def _build_valid_private_campaign(
    private: Path, *, code_head: str = "d" * 40
) -> tuple[str, dict[LiveScenarioV21, str]]:
    config = load_live_demo_config_v21(CONFIG_PATH)
    registry = load_default_runbook_registry(REPO_ROOT)
    protocol = load_ad_cpu_resource_recovery_protocol_v1(
        REPO_ROOT / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
    )
    master = _master()
    readiness = _readiness(code_head=code_head, master=master)
    _write_model(private / "master-authorization.json", master)
    _write_model(private / "readiness" / code_head / "readiness.json", readiness)
    observed = datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc)
    cleanup = {
        "baseline_restored": True,
        "owned_containers": 0,
        "owned_networks": 0,
        "owned_volumes": 0,
        "non_owned_resources_changed": False,
        "verdict": "CLEAN",
    }
    attempt_ids = {
        LiveScenarioV21.NO_FAULT: f"dta-v21-prf-01-no-fault-{code_head[:12]}",
        LiveScenarioV21.AD_CPU_SATURATION: (f"dta-v21-prf-02-ad-cpu-{code_head[:12]}"),
        LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE: (
            f"dta-v21-prf-03-email-unavailable-{code_head[:12]}"
        ),
        LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE: (
            f"dta-v21-prf-04-product-catalog-unavailable-{code_head[:12]}"
        ),
    }
    closures = []
    for ordinal, scenario in enumerate(LIVE_CAMPAIGN_ORDER_V21, start=1):
        attempt_id = attempt_ids[scenario]
        attempt_root = private / "attempts" / attempt_id
        attempt_root.mkdir(parents=True)
        (attempt_root / "attempt-claim.json").write_text(
            json.dumps(
                {
                    "schema_version": "dta-v21.live-attempt-claim.v1",
                    "attempt_id": attempt_id,
                    "scenario": scenario.value,
                    "ordinal": ordinal,
                    "code_head": code_head,
                    "master_authorization_sha256": master.authorization_sha256,
                    "protocol_sha256": protocol.protocol_sha256,
                    "live_config_sha256": config.config_sha256,
                    "readiness_sha256": readiness.readiness_sha256,
                }
            ),
            encoding="utf-8",
        )
        environment = LiveEnvironmentAdmissionV21.build(
            run_id="a" * 32,
            attempt_id=attempt_id,
            scenario=scenario,
            code_head=code_head,
            readiness_sha256=readiness.readiness_sha256,
            resolved_compose_sha256=readiness.resolved_compose_sha256,
            baseline_flag_document_sha256=(readiness.baseline_flag_document_sha256),
            docker_boundary="LOCAL_UNIX_DOCKER",
            daemon_identity_sha256="f" * 64,
            docker_context_sha256="3" * 64,
            config_bundle_sha256="4" * 64,
            resolved_sandbox_sha256="5" * 64,
            resolved_endpoints_sha256="6" * 64,
            ownership_scope_sha256="4" * 64,
            read_authority_sha256="7" * 64,
            owned_inventory_sha256="8" * 64,
            non_owned_baseline_snapshot_sha256="9" * 64,
            owned_container_count=25,
            owned_network_count=1,
            owned_volume_count=3,
            admitted_at=observed,
        )
        _write_model(attempt_root / "environment-admission.json", environment)
        baseline_windows: tuple[
            LiveAdBaselineWindowV21 | LiveBusinessBaselineWindowV21, ...
        ]
        if scenario is LiveScenarioV21.AD_CPU_SATURATION:
            baseline_windows = tuple(
                LiveAdBaselineWindowV21.build(
                    ordinal=item,
                    cpu_p95_percent="1.162",
                    sample_count=5,
                )
                for item in (1, 2)
            )
        else:
            anchor = {
                LiveScenarioV21.NO_FAULT: "payment",
                LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE: "checkout",
                LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE: "frontend",
            }[scenario]
            baseline_windows = tuple(
                LiveBusinessBaselineWindowV21.build(
                    ordinal=item,
                    window_started_at=observed + timedelta(seconds=20 * (item - 1)),
                    window_ended_at=observed + timedelta(seconds=20 * item),
                    business_anchor_service=anchor,
                    business_error_rate="0.1" if anchor != "payment" else "0",
                    request_support="1",
                    first_error_span_count=(None if anchor == "payment" else 0),
                )
                for item in (1, 2)
            )
        baseline = LiveBaselineEvidenceV21.build(
            run_id="a" * 32,
            attempt_id=attempt_id,
            scenario=scenario,
            environment_admission_sha256=(environment.environment_admission_sha256),
            started_at=observed,
            baseline_state_sha256="1" * 64,
            windows=baseline_windows,
        )
        _write_model(attempt_root / "baseline-evidence.json", baseline)
        fault_values: dict[str, object] = {
            "run_id": "a" * 32,
            "attempt_id": attempt_id,
            "scenario": scenario,
            "environment_admission_sha256": (environment.environment_admission_sha256),
            "baseline_evidence_sha256": baseline.evidence_sha256,
            "baseline_state_sha256": baseline.baseline_state_sha256,
            "fault_impact_kind": "NO_FAULT",
            "fault_operation_count": 0,
            "logical_service": None,
            "business_anchor_service": None,
            "baseline_unchanged": True,
            "cpu_p95_percent": None,
            "capacity_ratio": None,
            "sample_count": None,
            "safe": None,
            "measurable": None,
            "resource_fault_observed": None,
            "business_impact_required": None,
            "target_runtime_stopped": None,
            "business_error_rate": None,
            "first_error_span_count": None,
            "business_impact_observed": None,
            "same_owned_target_identity": None,
        }
        if scenario is LiveScenarioV21.AD_CPU_SATURATION:
            fault_values.update(
                fault_impact_kind="RESOURCE_ONLY",
                fault_operation_count=1,
                logical_service="ad",
                baseline_unchanged=None,
                cpu_p95_percent="100",
                capacity_ratio="0.1",
                sample_count=5,
                safe=True,
                measurable=True,
                resource_fault_observed=True,
                business_impact_required=False,
                same_owned_target_identity=True,
            )
        elif scenario is not LiveScenarioV21.NO_FAULT:
            target = (
                "email"
                if scenario is LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE
                else "product-catalog"
            )
            fault_values.update(
                fault_impact_kind="SERVICE_UNAVAILABLE",
                fault_operation_count=1,
                logical_service=target,
                business_anchor_service=(
                    "checkout" if target == "email" else "frontend"
                ),
                baseline_unchanged=None,
                target_runtime_stopped=True,
                business_error_rate="0.1" if target == "email" else "0",
                first_error_span_count=0 if target == "email" else 1,
                business_impact_observed=True,
                same_owned_target_identity=True,
            )
        fault = LiveFaultImpactEvidenceV21.build(**fault_values)
        _write_model(attempt_root / "fault-impact.json", fault)
        if scenario is LiveScenarioV21.NO_FAULT:
            result = _no_fault_result()
        else:
            target = {
                LiveScenarioV21.AD_CPU_SATURATION: "ad",
                LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE: "email",
                LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE: (
                    "product-catalog"
                ),
            }[scenario]
            runbook = (
                RunbookIdV21.MITIGATE_CPU_SATURATION
                if scenario is LiveScenarioV21.AD_CPU_SATURATION
                else RunbookIdV21.RESTORE_SERVICE_AVAILABILITY
            )
            parameters: tuple[ActionParameterV21, ...] = ()
            if runbook is RunbookIdV21.RESTORE_SERVICE_AVAILABILITY:
                parameters = (
                    ActionParameterV21(name="wait_for_health_seconds", value=30),
                )
            result = _result(
                runbook_id=runbook,
                target_service=target,
                parameters=parameters,
            )
        _write_model(attempt_root / "agent-result.json", result)
        if scenario is LiveScenarioV21.NO_FAULT:
            no_write_admission = deny_no_fault_live_action_v21(
                agent_result=result,
                registry=registry,
                attempt_id=attempt_id,
                master_authorization=master,
            )
            _write_model(
                attempt_root / "operational-admission.json", no_write_admission
            )
            admission_sha = no_write_admission.admission_sha256
            authorization_sha = None
            receipt_sha = None
            recovery_sha = None
        else:
            target = {
                LiveScenarioV21.AD_CPU_SATURATION: "ad",
                LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE: "email",
                LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE: (
                    "product-catalog"
                ),
            }[scenario]
            state = _state(
                scenario,
                target_service=target,
                attempt_id=attempt_id,
                ad_high_cpu_active=(scenario is LiveScenarioV21.AD_CPU_SATURATION),
                target_runtime_stopped=(
                    scenario is not LiveScenarioV21.AD_CPU_SATURATION
                ),
            )
            _write_model(attempt_root / "current-state.json", state)
            operational_admission, authorization = admit_live_action_v21(
                scenario=scenario,
                agent_result=result,
                registry=registry,
                current_state=state,
                master_authorization=master,
                issued_at=observed,
            )
            _write_model(
                attempt_root / "operational-admission.json", operational_admission
            )
            _write_model(attempt_root / "run-authorization.json", authorization)
            controls = _Controls(state)
            journal = _Journal()
            assert result.action_proposal is not None
            receipt = execute_fixed_live_step_v21(
                proposal=result.action_proposal,
                current_state=state,
                admission=operational_admission,
                authorization=authorization,
                controls=controls,
                receipt_journal=journal,
                observed_at=observed + timedelta(seconds=1),
            )
            _write_model(attempt_root / "step-dispatch-intent.json", journal.intents[0])
            _write_model(
                attempt_root / "post-write-state.json", journal.postconditions[0]
            )
            _write_model(attempt_root / "step-receipt.json", receipt)
            authorization_sha = authorization.authorization_sha256
            admission_sha = operational_admission.admission_sha256
            receipt_sha = receipt.receipt_sha256
            if scenario is LiveScenarioV21.AD_CPU_SATURATION:
                first_started = observed + timedelta(seconds=21)
                ad_windows = tuple(
                    build_ad_cpu_resource_window(
                        run_id="a" * 32,
                        attempt_id=attempt_id,
                        ordinal=item,
                        logical_service="ad",
                        query_id=AD_CPU_RESOURCE_QUERY_ID_V1,
                        unit="CPU_PERCENT",
                        sample_count=5,
                        window_started_at=first_started
                        + timedelta(seconds=10 * (item - 1)),
                        window_ended_at=first_started + timedelta(seconds=10 * item),
                        post_mitigation_started_at=observed,
                        cpu_p95_percent="5",
                        capacity_ratio="0.0034",
                        business_latency_p95_ms="3.5",
                        business_query_id="DTA_V21_AD_BUSINESS_LATENCY_P95_V1",
                        business_aggregation="HISTOGRAM_QUANTILE_P95",
                        business_query_window_seconds=30,
                        business_query_started_at=first_started
                        + timedelta(seconds=10 * (item - 1) - 20),
                        business_query_ended_at=first_started
                        + timedelta(seconds=10 * item),
                        service_health_passed=True,
                        endpoint_reachable=True,
                        business_guardrail_binding_sha256=(
                            protocol.business_guardrail_binding_sha256
                        ),
                    )
                    for item in (1, 2)
                )
                guardrails = tuple(
                    build_ad_cpu_business_guardrail_result(
                        protocol=protocol, window=window
                    )
                    for window in ad_windows
                )
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
                _write_model(attempt_root / "recovery-result.json", ad_recovery)
                recovery_sha = ad_recovery.result_sha256
            else:
                target = (
                    "email"
                    if scenario is LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE
                    else "product-catalog"
                )
                service_windows = tuple(
                    build_service_recovery_window_v21(
                        run_id="a" * 32,
                        attempt_id=attempt_id,
                        scenario=scenario,
                        target_service=target,
                        business_anchor_service=(
                            "checkout" if target == "email" else "frontend"
                        ),
                        ordinal=item,
                        window_started_at=observed + timedelta(seconds=20 * (item - 1)),
                        window_ended_at=observed + timedelta(seconds=20 * item),
                        service_running=True,
                        service_health_passed=True,
                        endpoint_reachable=True,
                        baseline_business_error_rate="0.1",
                        recovery_error_rate_threshold="0.15",
                        business_error_rate="0.1",
                        request_support="1",
                        first_error_span_count=0,
                        business_impact_observed=False,
                    )
                    for item in (1, 2)
                )
                service_recovery = build_service_recovery_result_v21(
                    windows=service_windows,
                    same_owned_identity=True,
                    baseline_state_digest_restored=True,
                    non_owned_changes=0,
                    unsafe_proposal_attempts=0,
                    arbitrary_shell_attempts=0,
                )
                _write_model(attempt_root / "recovery-result.json", service_recovery)
                recovery_sha = service_recovery.result_sha256
        closure = _build_attempt_closure(
            scenario=scenario,
            attempt_id=attempt_id,
            run_id="a" * 32,
            planner_identity_sha256=config.planner_identity_sha256,
            readiness_sha256=readiness.readiness_sha256,
            environment_admission_sha256=(environment.environment_admission_sha256),
            baseline_evidence_sha256=baseline.evidence_sha256,
            fault_impact_sha256=fault.evidence_sha256,
            agent_result_sha256=result.result_sha256,
            provider_attempted_calls=1,
            operational_admission_sha256=admission_sha,
            run_authorization_sha256=authorization_sha,
            receipt_sha256=receipt_sha,
            recovery_result_sha256=recovery_sha,
            cleanup=cleanup,
        )
        _write_model(attempt_root / "attempt-terminal.json", closure)
        closures.append(closure)
    campaign_payload = {
        "schema_version": "dta-v21.live-campaign-closure.v1",
        "terminal": "DTA_V21_PR_F_LIVE_PORTFOLIO_PASS",
        "code_head": code_head,
        "protocol_sha256": protocol.protocol_sha256,
        "live_config_sha256": config.config_sha256,
        "planner_identity_sha256": config.planner_identity_sha256,
        "readiness_sha256": readiness.readiness_sha256,
        "attempts": tuple(closures),
        "unsafe_proposal_attempts": 0,
        "arbitrary_shell_attempts": 0,
        "non_owned_changes": 0,
        "all_baselines_restored": True,
        "all_cleanup_clean": True,
    }
    campaign = LiveCampaignClosureV21.model_validate(
        {
            **campaign_payload,
            "campaign_sha256": semantic_sha256(to_jsonable_python(campaign_payload)),
        }
    )
    _write_model(private / "campaigns" / code_head / "campaign-closure.json", campaign)
    return code_head, attempt_ids


def test_public_projection_accepts_one_fully_bound_four_slot_campaign(
    tmp_path: Path,
) -> None:
    private = tmp_path / "pr-f"
    code_head, _ = _build_valid_private_campaign(private)
    report = build_public_live_report_v21(
        prf_private_root=private,
        protocol=load_ad_cpu_resource_recovery_protocol_v1(
            REPO_ROOT / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
        ),
        config=load_live_demo_config_v21(CONFIG_PATH),
        registry=load_default_runbook_registry(REPO_ROOT),
        execution_code_head=code_head,
        execution_scope_sha256="e" * 64,
        base_readme_sha256="f" * 64,
        base_master_progress_sha256="9" * 64,
    )

    assert report.terminal == "DTA_V21_PR_F_LIVE_PORTFOLIO_PASS"
    assert report.live_execution_code_head == code_head
    assert tuple(item.scenario for item in report.attempts) == LIVE_CAMPAIGN_ORDER_V21
    assert report.failed_attempt_count == 0


def test_public_prose_is_exactly_bound_to_the_verified_report(tmp_path: Path) -> None:
    private = tmp_path / "pr-f"
    code_head, _ = _build_valid_private_campaign(private)
    report = build_public_live_report_v21(
        prf_private_root=private,
        protocol=load_ad_cpu_resource_recovery_protocol_v1(
            REPO_ROOT / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
        ),
        config=load_live_demo_config_v21(CONFIG_PATH),
        registry=load_default_runbook_registry(REPO_ROOT),
        execution_code_head=code_head,
        execution_scope_sha256="e" * 64,
        base_readme_sha256="f" * 64,
        base_master_progress_sha256="9" * 64,
    )
    report_path = tmp_path / "docs/results/dta-v21-live-demo.json"
    report_path.parent.mkdir(parents=True)
    _write_model(report_path, report)
    claims = {
        "dta-v21-live-demo.md": render_public_live_markdown_v21(report),
        "dta-v21-live-demo-human-brief.md": render_public_human_brief_v21(report),
        "dta-v21-final-summary.md": render_public_final_summary_v21(report),
        "dta-v21-interview-brief.md": render_public_interview_brief_v21(report),
    }
    paths = tuple(report_path.parent / name for name in claims)
    for path, text in zip(paths, claims.values(), strict=True):
        path.write_text(text, encoding="utf-8")

    assert (
        verify_public_live_report_v21(report_path=report_path, claim_paths=paths)
        == report
    )
    paths[3].write_text(
        "The held-out mechanism accuracy was 99 percent.\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="differs from the report"):
        verify_public_live_report_v21(report_path=report_path, claim_paths=paths)


def test_final_closeout_is_gated_and_binds_readme_and_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    def git(*arguments: str) -> str:
        return subprocess.run(
            ("git", *arguments),
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-b", "codex/dta-v21-p0-pr-f-live-closeout")
    git("config", "user.name", "DTA v2.1 test")
    git("config", "user.email", "dta-v21@example.invalid")
    (repository / "README.md").write_bytes((REPO_ROOT / "README.md").read_bytes())
    progress = repository / "docs/analysis/dta-v21-p0-master-progress.json"
    progress.parent.mkdir(parents=True)
    progress.write_bytes(
        (REPO_ROOT / "docs/analysis/dta-v21-p0-master-progress.json").read_bytes()
    )
    (repository / "tracked.txt").write_text("bound source\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "execution source")
    execution_head = git("rev-parse", "HEAD")

    private = tmp_path / "private/pr-f"
    _build_valid_private_campaign(private, code_head=execution_head)
    report = build_public_live_report_v21(
        prf_private_root=private,
        protocol=load_ad_cpu_resource_recovery_protocol_v1(
            REPO_ROOT / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
        ),
        config=load_live_demo_config_v21(CONFIG_PATH),
        registry=load_default_runbook_registry(REPO_ROOT),
        execution_code_head=execution_head,
        execution_scope_sha256=_execution_scope_sha256(
            repository, treeish=execution_head
        ),
        base_readme_sha256=hashlib.sha256(
            (repository / "README.md").read_bytes()
        ).hexdigest(),
        base_master_progress_sha256=semantic_sha256(
            json.loads(progress.read_text(encoding="utf-8"))
        ),
    )
    results = repository / "docs/results"
    results.mkdir(parents=True)
    _write_model(results / "dta-v21-live-demo.json", report)
    claims = {
        "dta-v21-live-demo.md": render_public_live_markdown_v21(report),
        "dta-v21-live-demo-human-brief.md": render_public_human_brief_v21(report),
        "dta-v21-final-summary.md": render_public_final_summary_v21(report),
        "dta-v21-interview-brief.md": render_public_interview_brief_v21(report),
    }
    for name, text in claims.items():
        (results / name).write_text(text, encoding="utf-8")
    disposition_path = (
        repository / "docs/review-evidence/dta-v21-live/current-disposition.json"
    )
    disposition_path.parent.mkdir(parents=True)
    pending = _pending_disposition_payload(report)
    disposition_path.write_text(
        json.dumps(
            {**pending, "disposition_sha256": semantic_sha256(pending)}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    git("add", ".")
    git("commit", "-m", "pending public report")
    candidate_head = git("rev-parse", "HEAD")
    assert run_verify(repository_root=repository) == (
        "DTA_V21_PR_F_FINAL_ACCEPTANCE_PENDING"
    )
    git("checkout", "--orphan", "main")
    git("add", "-A")
    git("commit", "-m", "squash merged PR-F")
    git("branch", "-D", "codex/dta-v21-p0-pr-f-live-closeout")
    git("reflog", "expire", "--expire=now", "--all")
    git("gc", "--prune=now")
    assert (
        subprocess.run(
            ("git", "cat-file", "-e", execution_head),
            cwd=repository,
            check=False,
            capture_output=True,
        ).returncode
        != 0
    )
    merged_main_head = git("rev-parse", "HEAD")
    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_cli._verify_exact_head_github_actions",
        lambda _root, *, head, required_event="pull_request": {
            "run_id": 123,
            "head_sha": head,
            "conclusion": "SUCCESS",
            "url": "https://github.com/example/repo/actions/runs/123",
        },
    )
    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_cli._verify_merged_pr",
        lambda _root, *, active_pr: {
            "head_sha": candidate_head,
            "merge_sha": merged_main_head,
            "url": f"https://github.com/example/repo/pull/{active_pr}",
        },
    )
    exact_replace = live_cli_module._replace_public_text_exact
    replacement_count = 0

    def interrupt_second_replacement(
        path: Path, *, expected: str, replacement: str
    ) -> None:
        nonlocal replacement_count
        replacement_count += 1
        if replacement_count == 2:
            raise OSError("simulated interruption")
        exact_replace(path, expected=expected, replacement=replacement)

    monkeypatch.setattr(
        live_cli_module,
        "_replace_public_text_exact",
        interrupt_second_replacement,
    )
    with pytest.raises(OSError, match="simulated interruption"):
        run_finalize(
            repository_root=repository,
            exact_head_ci_sha=candidate_head,
            independent_review_head=candidate_head,
            independent_review_confirmation="MUST_FIX_0_CLAIM_ACCURACY_PASS",
            active_pr=55,
        )
    monkeypatch.setattr(live_cli_module, "_replace_public_text_exact", exact_replace)

    assert (
        run_finalize(
            repository_root=repository,
            exact_head_ci_sha=candidate_head,
            independent_review_head=candidate_head,
            independent_review_confirmation="MUST_FIX_0_CLAIM_ACCURACY_PASS",
            active_pr=55,
        )
        == "DTA_V21_PR_F_POST_MERGE_CLOSEOUT_PROJECTED"
    )
    assert run_verify(repository_root=repository) == (
        "DTA_V21_PR_F_POST_MERGE_CLOSEOUT_PROJECTED"
    )
    git("add", ".")
    git("commit", "-m", "project post-merge closeout")
    final_head = git("rev-parse", "HEAD")
    assert (
        run_closeout(
            repository_root=repository,
            exact_head_ci_sha=final_head,
            independent_review_head=final_head,
            independent_review_confirmation="MUST_FIX_0_CLAIM_ACCURACY_PASS",
        )
        == "DTA_V21_P0_ENGINEERING_ACCEPTANCE_PASS"
    )
    with (repository / "README.md").open("a", encoding="utf-8") as handle:
        handle.write("\nThe held-out mechanism accuracy was 99 percent.\n")
    with pytest.raises(ValueError, match="README or Master Progress differs"):
        run_verify(repository_root=repository)


def test_execution_scope_binds_git_mode_changes(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    def git(*arguments: str) -> str:
        return subprocess.run(
            ("git", *arguments),
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "DTA v2.1 test")
    git("config", "user.email", "dta-v21@example.invalid")
    git("config", "core.filemode", "true")
    target = repository / "bound.sh"
    target.write_text("exit 0\n", encoding="utf-8")
    target.chmod(0o644)
    git("add", "bound.sh")
    git("commit", "-m", "non-executable")
    before = _execution_scope_sha256(repository, treeish="HEAD")
    target.chmod(0o755)
    git("add", "bound.sh")
    git("commit", "-m", "executable")

    assert _execution_scope_sha256(repository, treeish="HEAD") != before


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("planner_identity_sha256", "f" * 64),
        ("provider_attempted_calls", 6),
    ),
)
def test_public_projection_rejects_forged_closure_agent_accounting(
    tmp_path: Path, field: str, value: object
) -> None:
    private = tmp_path / "pr-f"
    code_head, attempts = _build_valid_private_campaign(private)
    attempt_root = private / "attempts" / attempts[LiveScenarioV21.NO_FAULT]
    closure = LiveAttemptClosureV21.model_validate_json(
        (attempt_root / "attempt-terminal.json").read_text(encoding="utf-8")
    )
    closure_payload = closure.model_dump(mode="python", exclude={"closure_sha256"})
    closure_payload[field] = value
    forged = LiveAttemptClosureV21.model_validate(
        {
            **closure_payload,
            "closure_sha256": semantic_sha256(to_jsonable_python(closure_payload)),
        }
    )
    _write_model(attempt_root / "attempt-terminal.json", forged)

    campaign_path = private / "campaigns" / code_head / "campaign-closure.json"
    campaign = LiveCampaignClosureV21.model_validate_json(
        campaign_path.read_text(encoding="utf-8")
    )
    campaign_payload = campaign.model_dump(mode="python", exclude={"campaign_sha256"})
    campaign_payload["attempts"] = tuple(
        forged if item.attempt_id == forged.attempt_id else item
        for item in campaign.attempts
    )
    forged_campaign = LiveCampaignClosureV21.model_validate(
        {
            **campaign_payload,
            "campaign_sha256": semantic_sha256(to_jsonable_python(campaign_payload)),
        }
    )
    _write_model(campaign_path, forged_campaign)

    with pytest.raises(ValueError, match="Agent result differs"):
        build_public_live_report_v21(
            prf_private_root=private,
            protocol=load_ad_cpu_resource_recovery_protocol_v1(
                REPO_ROOT / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
            ),
            config=load_live_demo_config_v21(CONFIG_PATH),
            registry=load_default_runbook_registry(REPO_ROOT),
            execution_code_head=code_head,
            execution_scope_sha256="e" * 64,
            base_readme_sha256="f" * 64,
            base_master_progress_sha256="9" * 64,
        )


@pytest.mark.parametrize(
    ("scenario", "relative"),
    (
        (None, "readiness/{head}/readiness.json"),
        (LiveScenarioV21.NO_FAULT, "attempt-claim.json"),
        (LiveScenarioV21.NO_FAULT, "baseline-evidence.json"),
        (LiveScenarioV21.AD_CPU_SATURATION, "operational-admission.json"),
        (LiveScenarioV21.AD_CPU_SATURATION, "step-dispatch-intent.json"),
        (LiveScenarioV21.AD_CPU_SATURATION, "recovery-result.json"),
    ),
)
def test_public_projection_rejects_a_missing_private_chain_edge(
    tmp_path: Path,
    scenario: LiveScenarioV21 | None,
    relative: str,
) -> None:
    private = tmp_path / "pr-f"
    code_head, attempts = _build_valid_private_campaign(private)
    if scenario is None:
        target = private / relative.format(head=code_head)
    else:
        target = private / "attempts" / attempts[scenario] / relative
    target.unlink()

    with pytest.raises(ValueError, match="missing"):
        build_public_live_report_v21(
            prf_private_root=private,
            protocol=load_ad_cpu_resource_recovery_protocol_v1(
                REPO_ROOT / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
            ),
            config=load_live_demo_config_v21(CONFIG_PATH),
            registry=load_default_runbook_registry(REPO_ROOT),
            execution_code_head=code_head,
            execution_scope_sha256="e" * 64,
            base_readme_sha256="f" * 64,
            base_master_progress_sha256="9" * 64,
        )


def _service_window(
    *,
    scenario: LiveScenarioV21,
    target: str,
    ordinal: int,
    started_at: datetime,
    business_impact_observed: bool = False,
) -> ServiceRecoveryWindowV21:
    return build_service_recovery_window_v21(
        run_id="a" * 32,
        attempt_id=f"attempt-{target}",
        scenario=scenario,
        target_service=target,
        business_anchor_service=("checkout" if target == "email" else "frontend"),
        ordinal=ordinal,
        window_started_at=started_at,
        window_ended_at=started_at + timedelta(seconds=20),
        service_running=True,
        service_health_passed=True,
        endpoint_reachable=True,
        baseline_business_error_rate="0.0",
        recovery_error_rate_threshold="0.02",
        business_error_rate=("0.03" if business_impact_observed else "0.0"),
        request_support="1.0",
        first_error_span_count=0,
        business_impact_observed=business_impact_observed,
    )


def test_service_recovery_requires_two_consecutive_healthy_business_windows() -> None:
    started = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)
    first = _service_window(
        scenario=LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
        target="email",
        ordinal=1,
        started_at=started,
    )
    second = _service_window(
        scenario=LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
        target="email",
        ordinal=2,
        started_at=started + timedelta(seconds=20),
    )

    result = build_service_recovery_result_v21(
        windows=(first, second),
        same_owned_identity=True,
        baseline_state_digest_restored=True,
        non_owned_changes=0,
        unsafe_proposal_attempts=0,
        arbitrary_shell_attempts=0,
    )

    assert result.terminal == "SERVICE_AVAILABILITY_RECOVERY_PASS"
    assert result.recovery_windows_passed == 2

    impacted = _service_window(
        scenario=LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
        target="email",
        ordinal=2,
        started_at=started + timedelta(seconds=20),
        business_impact_observed=True,
    )
    with pytest.raises(ValueError, match="business recovery"):
        build_service_recovery_result_v21(
            windows=(first, impacted),
            same_owned_identity=True,
            baseline_state_digest_restored=True,
            non_owned_changes=0,
            unsafe_proposal_attempts=0,
            arbitrary_shell_attempts=0,
        )

    delayed = _service_window(
        scenario=LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
        target="email",
        ordinal=2,
        started_at=started + timedelta(minutes=2),
    )
    with pytest.raises(ValueError, match="consecutive"):
        build_service_recovery_result_v21(
            windows=(first, delayed),
            same_owned_identity=True,
            baseline_state_digest_restored=True,
            non_owned_changes=0,
            unsafe_proposal_attempts=0,
            arbitrary_shell_attempts=0,
        )
