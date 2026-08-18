from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import json
import subprocess
from types import SimpleNamespace

import pytest

from ecomsre.dta_v2.v21.contracts import semantic_sha256
from ecomsre.dta_v2.v21.live_capability_closeout import (
    AMENDMENT3_RAW_SHA256_V1,
    CAPABILITY_MISS_ATTEMPT_ID_V1,
    CAPABILITY_MISS_CODE_HEAD_V1,
    LivePositiveContinuationClosureV1,
    POSITIVE_CONTINUATION_ORDER_V1,
    NoFaultCapabilityMissV1,
    PositiveContinuationAdmissionV1,
    PositiveContinuationConsumptionV1,
    PositiveContinuationQuiescenceV1,
    PositiveContinuationReadinessV3,
    PositiveContinuationReviewV1,
    PositiveContinuationStandingAuthorizationV1,
    build_positive_continuation_admission_v1,
    consume_positive_continuation_v1,
    verify_no_fault_capability_miss_eligibility_v1,
)
from ecomsre.dta_v2.v21.live_contracts import (
    LiveAttemptClosureV21,
    LiveScenarioV21,
)
from ecomsre.dta_v2.v21.live_capability_reporting import (
    PublicHistoricalReadyBlockerV3,
    PublicLiveReportV3,
    PublicNoFaultCapabilityMissV3,
    PublicPositiveAttemptV3,
    render_public_interview_brief_v3,
    render_public_live_markdown_v3,
    verify_public_live_report_v3,
)
from ecomsre.dta_v2.v21.live_capability_cli import (
    _pending_disposition,
    _readme_block,
    run_positive_closeout,
    run_positive_finalize,
    run_positive_verify,
)
from ecomsre.dta_v2.v21.live_runner import (
    LiveCampaignBlockedV21,
    run_owned_live_positive_continuation_v1,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _capability() -> NoFaultCapabilityMissV1:
    return NoFaultCapabilityMissV1.build(
        amendment_sha256=AMENDMENT3_RAW_SHA256_V1,
        decision_id="DEC-046",
        classification="NO_FAULT_FALSE_POSITIVE_DIAGNOSIS_SAFE_NO_ACTION",
        code_head=CAPABILITY_MISS_CODE_HEAD_V1,
        attempt_id=CAPABILITY_MISS_ATTEMPT_ID_V1,
        scenario=LiveScenarioV21.NO_FAULT,
        stage="AGENT",
        attempt_terminal="BLOCKED_DTA_V21_PRF_SAFETY",
        agent_terminal="COMPLETED",
        diagnosis_root_service="checkout",
        diagnosis_root_entity_ref="service:checkout",
        diagnosis_fault_domain="APPLICATION",
        diagnosis_mechanism="UNKNOWN",
        action_disposition="NO_ACTION",
        capability_passed=False,
        diagnosis_correct=False,
        no_write_safety_passed=True,
        fault_injected=False,
        write_admitted=False,
        forward_action_observed=False,
        baseline_restored=True,
        cleanup_clean=True,
        non_owned_change_observed=False,
        fault_operation_count=0,
        forward_step_count=0,
        unsafe_proposal_attempts=0,
        arbitrary_shell_attempts=0,
        planner_identity_sha256="80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d",
        provider_model="gpt-5.4-mini-2026-03-17",
        held_out_execution_id="53615cdd78b348b68496f64102c0b4de",
        held_out_seal_sha256="9a7c8e56400e99c693c8bddc26007b1dd26e0dcee2167b07cf3fba00fd22fbd7",
        held_out_claim="DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED",
        attempt_claim_raw_sha256="1" * 64,
        attempt_claim_semantic_sha256="2" * 64,
        readiness_raw_sha256="3" * 64,
        readiness_semantic_sha256="4" * 64,
        readiness_sha256="5" * 64,
        readiness_compose_identity_sha256="6" * 64,
        environment_admission_raw_sha256="5" * 64,
        environment_admission_semantic_sha256="6" * 64,
        environment_admission_sha256="7" * 64,
        attempt_compose_identity_sha256="8" * 64,
        baseline_evidence_raw_sha256="7" * 64,
        baseline_evidence_semantic_sha256="8" * 64,
        baseline_evidence_sha256="9" * 64,
        fault_impact_raw_sha256="9" * 64,
        fault_impact_semantic_sha256="a" * 64,
        fault_impact_sha256="b" * 64,
        agent_result_raw_sha256="b" * 64,
        agent_result_semantic_sha256="c" * 64,
        agent_result_sha256="d" * 64,
        diagnosis_sha256="d" * 64,
        candidate_set_sha256="e" * 64,
        candidate_view_sha256="f" * 64,
        action_proposal_sha256="0" * 64,
        attempt_terminal_raw_sha256="1" * 64,
        attempt_terminal_semantic_sha256="2" * 64,
        parent_retry_admission_sha256="3" * 64,
        parent_retry_consumption_sha256="4" * 64,
        original_blocker_reconciliation_sha256="5" * 64,
        master_authorization_sha256="6" * 64,
        protocol_freeze_sha256="7" * 64,
    )


def _review(head: str) -> PositiveContinuationReviewV1:
    return PositiveContinuationReviewV1.build(
        code_head=head,
        reviewer="independent-reviewer",
        reviewed_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
        must_fix_count=0,
        should_fix_count=0,
        claim_accuracy="PASS",
    )


def _quiescence(head: str, capability: NoFaultCapabilityMissV1) -> PositiveContinuationQuiescenceV1:
    return PositiveContinuationQuiescenceV1.build(
        code_head=head,
        observed_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
        docker_boundary="LOCAL_UNIX_DOCKER",
        owned_container_count=0,
        owned_network_count=0,
        owned_volume_count=0,
        required_ports_available=True,
        execution_lease_held=False,
        private_permissions_verified=True,
        source_worktree_clean=True,
        frozen_bindings_verified=True,
        capability_miss_sha256=capability.classification_sha256,
        parent_retry_consumption_sha256="4" * 64,
    )


def _readiness(head: str, capability: NoFaultCapabilityMissV1) -> PositiveContinuationReadinessV3:
    return PositiveContinuationReadinessV3.build(
        terminal="DTA_V21_PR_F_POSITIVE_CONTINUATION_READY",
        code_head=head,
        base_v2_readiness_sha256="1" * 64,
        current_quiescence_sha256=_quiescence(head, capability).observation_sha256,
        capability_miss_sha256=capability.classification_sha256,
        parent_retry_consumption_sha256="4" * 64,
        amendment_sha256=AMENDMENT3_RAW_SHA256_V1,
        decision_id="DEC-046",
        exact_head_ci_run_id=123,
        exact_head_ci_run_url="https://github.com/example/repo/actions/runs/123",
        master_authorization_sha256="6" * 64,
        standing_authorization_sha256="7" * 64,
        planner_identity_sha256="80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d",
        provider_model="gpt-5.4-mini-2026-03-17",
        ad_protocol_sha256="c983b9be95b532cdbb8fb5358af92055e633fd767693e9dc65743b3e80a77517",
    )


def test_capability_miss_is_failure_plus_safe_no_action_not_no_fault_pass() -> None:
    record = _capability()

    assert record.diagnosis_correct is False
    assert record.no_write_safety_passed is True
    assert record.action_disposition == "NO_ACTION"
    assert record.fault_operation_count == 0
    assert record.forward_step_count == 0

    payload = record.model_dump(mode="json", exclude={"classification_sha256"})
    payload["diagnosis_root_service"] = None
    with pytest.raises(ValueError):
        NoFaultCapabilityMissV1.model_validate(
            {**payload, "classification_sha256": semantic_sha256(payload)}
        )


def test_standing_authorization_excludes_no_fault_and_self_approval() -> None:
    authorization = PositiveContinuationStandingAuthorizationV1.build()

    assert authorization.positive_scenarios == POSITIVE_CONTINUATION_ORDER_V1
    assert LiveScenarioV21.NO_FAULT not in authorization.positive_scenarios
    assert authorization.codex_autonomous_self_approval is False
    assert authorization.additional_human_confirmation_required is False


def _positive_attempt(scenario: LiveScenarioV21, ordinal: int) -> LiveAttemptClosureV21:
    terminal = (
        "AD_CPU_RESOURCE_RECOVERY_PASS"
        if scenario is LiveScenarioV21.AD_CPU_SATURATION
        else "SERVICE_AVAILABILITY_RECOVERY_PASS"
    )
    payload = {
        "schema_version": "dta-v21.live-attempt-closure.v1",
        "scenario": scenario,
        "attempt_id": f"attempt-{ordinal}",
        "run_id": f"{ordinal:032x}",
        "status": "PASS",
        "terminal": terminal,
        "planner_identity_sha256": "1" * 64,
        "readiness_sha256": "2" * 64,
        "environment_admission_sha256": "3" * 64,
        "baseline_evidence_sha256": "4" * 64,
        "fault_impact_sha256": "5" * 64,
        "agent_result_sha256": "6" * 64,
        "operational_admission_sha256": "7" * 64,
        "run_authorization_sha256": "8" * 64,
        "fault_operation_count": 1,
        "forward_step_count": 1,
        "step_receipt_sha256": "9" * 64,
        "recovery_result_sha256": "a" * 64,
        "baseline_state_digest_restored": True,
        "cleanup_verdict": "CLEAN",
        "owned_containers_remaining": 0,
        "owned_networks_remaining": 0,
        "owned_volumes_remaining": 0,
        "non_owned_changes": 0,
        "unsafe_proposal_attempts": 0,
        "arbitrary_shell_attempts": 0,
        "provider_attempted_calls": 1,
    }
    return LiveAttemptClosureV21.model_validate(
        {**payload, "closure_sha256": semantic_sha256(payload)}
    )


def test_positive_closure_requires_exact_three_positive_slots() -> None:
    attempts = tuple(
        _positive_attempt(scenario, ordinal)
        for ordinal, scenario in enumerate(POSITIVE_CONTINUATION_ORDER_V1, 2)
    )
    closure = LivePositiveContinuationClosureV1.build(
        terminal=(
            "DTA_V21_PR_F_POSITIVE_PORTFOLIO_PASS_WITH_NO_FAULT_DIAGNOSIS_MISS"
        ),
        code_head="a" * 40,
        admission_sha256="1" * 64,
        consumption_sha256="2" * 64,
        v3_readiness_sha256="3" * 64,
        capability_miss_sha256="4" * 64,
        planner_identity_sha256=(
            "80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d"
        ),
        attempts=attempts,
        positive_continuation_attempt_count=3,
        positive_continuation_attempts_passed=3,
        all_baselines_restored=True,
        all_cleanup_clean=True,
        non_owned_changes=0,
        unsafe_proposal_attempts=0,
        arbitrary_shell_attempts=0,
    )
    assert tuple(item.scenario for item in closure.attempts) == (
        POSITIVE_CONTINUATION_ORDER_V1
    )

    forged = closure.model_dump(mode="json", exclude={"closure_sha256"})
    forged["attempts"] = list(reversed(forged["attempts"]))
    with pytest.raises(ValueError):
        LivePositiveContinuationClosureV1.model_validate(
            {**forged, "closure_sha256": semantic_sha256(forged)}
        )


def test_positive_admission_is_exactly_slots_two_through_four() -> None:
    head = "a" * 40
    capability = _capability()
    readiness = _readiness(head, capability)
    quiescence = _quiescence(head, capability)
    admission = build_positive_continuation_admission_v1(
        new_code_head=head,
        base_main_head="b" * 40,
        capability=capability,
        parent_retry_consumption_sha256="4" * 64,
        original_blocker_reconciliation_sha256="5" * 64,
        readiness=readiness,
        quiescence=quiescence,
        review=_review(head),
    )

    assert admission.continuation_scenarios == POSITIVE_CONTINUATION_ORDER_V1
    assert LiveScenarioV21.NO_FAULT not in admission.continuation_scenarios
    assert admission.no_fault_retry_authorized is False
    assert admission.maximum_new_positive_continuations == 1
    assert admission.maximum_continuations_after_consumption == 0

    forged = admission.model_dump(mode="json", exclude={"admission_sha256"})
    forged["continuation_scenarios"] = [
        LiveScenarioV21.NO_FAULT.value,
        LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE.value,
        LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE.value,
    ]
    with pytest.raises(ValueError):
        PositiveContinuationAdmissionV1.model_validate(
            {**forged, "admission_sha256": semantic_sha256(forged)}
        )


def test_positive_consumption_is_one_global_append_only_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = "a" * 40
    capability = _capability()
    readiness = _readiness(head, capability)
    quiescence = _quiescence(head, capability)
    admission = build_positive_continuation_admission_v1(
        new_code_head=head,
        base_main_head="b" * 40,
        capability=capability,
        parent_retry_consumption_sha256="4" * 64,
        original_blocker_reconciliation_sha256="5" * 64,
        readiness=readiness,
        quiescence=quiescence,
        review=_review(head),
    )
    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_capability_closeout.verify_positive_continuation_admission_v1",
        lambda **_values: admission,
    )
    attempts = tmp_path / "pr-f/attempts"
    (attempts / "dta-v21-prf-01-no-fault-422f015451fd").mkdir(parents=True)
    (attempts / CAPABILITY_MISS_ATTEMPT_ID_V1).mkdir()

    first = consume_positive_continuation_v1(
        repository_root=tmp_path,
        private_root=tmp_path,
        new_code_head=head,
        consumed_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    assert isinstance(first, PositiveContinuationConsumptionV1)
    assert first.consumed_for_scenarios == POSITIVE_CONTINUATION_ORDER_V1
    assert first.no_fault_rerun is False
    assert first.maximum_additional_positive_continuations == 0

    with pytest.raises(
        RuntimeError, match="BLOCKED_DTA_V21_PRF_POSITIVE_CONTINUATION_EXHAUSTED"
    ):
        consume_positive_continuation_v1(
            repository_root=tmp_path,
            private_root=tmp_path,
            new_code_head=head,
            consumed_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
        )


def test_positive_runner_dispatches_only_exact_positive_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = "a" * 40
    capability = _capability()
    readiness_v3 = _readiness(head, capability)
    quiescence = _quiescence(head, capability)
    admission = build_positive_continuation_admission_v1(
        new_code_head=head,
        base_main_head="b" * 40,
        capability=capability,
        parent_retry_consumption_sha256="4" * 64,
        original_blocker_reconciliation_sha256="5" * 64,
        readiness=readiness_v3,
        quiescence=quiescence,
        review=_review(head),
    )
    consumption = PositiveContinuationConsumptionV1.build(
        status="CONSUMED",
        admission_sha256=admission.admission_sha256,
        consumed_by_code_head=head,
        consumed_for_scenarios=POSITIVE_CONTINUATION_ORDER_V1,
        first_scenario=LiveScenarioV21.AD_CPU_SATURATION,
        no_fault_rerun=False,
        maximum_additional_positive_continuations=0,
        consumed_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    observed: list[LiveScenarioV21] = []

    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_runner.verify_positive_continuation_admission_v1",
        lambda **_values: admission,
    )
    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_runner.consume_positive_continuation_v1",
        lambda **_values: consumption,
    )

    def _run_attempt(**values: object) -> LiveAttemptClosureV21:
        scenario = values["scenario"]
        assert isinstance(scenario, LiveScenarioV21)
        observed.append(scenario)
        return _positive_attempt(scenario, len(observed) + 1)

    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_runner.run_owned_live_attempt_v21",
        _run_attempt,
    )
    prf_root = tmp_path / "private/pr-f"
    prf_root.mkdir(parents=True)
    closure = run_owned_live_positive_continuation_v1(
        repository_root=tmp_path,
        prf_private_root=prf_root,
        provider_env_path=tmp_path / "provider.env",
        config=SimpleNamespace(planner_identity_sha256=(
            "80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d"
        )),
        registry=SimpleNamespace(),
        protocol=SimpleNamespace(),
        master_authorization=SimpleNamespace(authorization_sha256="6" * 64),
        readiness=SimpleNamespace(readiness_sha256="1" * 64),
        v3_readiness=readiness_v3,
        capability_miss=capability,
        readiness_identity=SimpleNamespace(),
        readiness_raw_compose={},
        readiness_flagd_directory=tmp_path / "flagd",
        code_head=head,
    )

    assert observed == list(POSITIVE_CONTINUATION_ORDER_V1)
    assert closure.positive_continuation_attempts_passed == 3
    assert LiveScenarioV21.NO_FAULT not in observed


def test_positive_runner_stops_after_first_failed_positive_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = "a" * 40
    capability = _capability()
    readiness_v3 = _readiness(head, capability)
    quiescence = _quiescence(head, capability)
    admission = build_positive_continuation_admission_v1(
        new_code_head=head,
        base_main_head="b" * 40,
        capability=capability,
        parent_retry_consumption_sha256="4" * 64,
        original_blocker_reconciliation_sha256="5" * 64,
        readiness=readiness_v3,
        quiescence=quiescence,
        review=_review(head),
    )
    consumption = PositiveContinuationConsumptionV1.build(
        status="CONSUMED",
        admission_sha256=admission.admission_sha256,
        consumed_by_code_head=head,
        consumed_for_scenarios=POSITIVE_CONTINUATION_ORDER_V1,
        first_scenario=LiveScenarioV21.AD_CPU_SATURATION,
        no_fault_rerun=False,
        maximum_additional_positive_continuations=0,
        consumed_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    observed: list[LiveScenarioV21] = []
    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_runner.verify_positive_continuation_admission_v1",
        lambda **_values: admission,
    )
    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_runner.consume_positive_continuation_v1",
        lambda **_values: consumption,
    )

    def _fail(**values: object) -> LiveAttemptClosureV21:
        scenario = values["scenario"]
        assert isinstance(scenario, LiveScenarioV21)
        observed.append(scenario)
        raise LiveCampaignBlockedV21("BLOCKED_DTA_V21_AD_RESOURCE_RECOVERY")

    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_runner.run_owned_live_attempt_v21", _fail
    )
    prf_root = tmp_path / "private/pr-f"
    prf_root.mkdir(parents=True)
    with pytest.raises(LiveCampaignBlockedV21):
        run_owned_live_positive_continuation_v1(
            repository_root=tmp_path,
            prf_private_root=prf_root,
            provider_env_path=tmp_path / "provider.env",
            config=SimpleNamespace(planner_identity_sha256="8" * 64),
            registry=SimpleNamespace(),
            protocol=SimpleNamespace(),
            master_authorization=SimpleNamespace(authorization_sha256="6" * 64),
            readiness=SimpleNamespace(readiness_sha256="1" * 64),
            v3_readiness=readiness_v3,
            capability_miss=capability,
            readiness_identity=SimpleNamespace(),
            readiness_raw_compose={},
            readiness_flagd_directory=tmp_path / "flagd",
            code_head=head,
        )

    assert observed == [LiveScenarioV21.AD_CPU_SATURATION]


def test_exact_private_no_fault_miss_is_eligible_without_relabeling() -> None:
    configured = os.environ.get("DTA_V21_ACCEPTED_PRIVATE_ROOT")
    if configured is None:
        pytest.skip("DTA_V21_ACCEPTED_PRIVATE_ROOT is not configured")

    record = verify_no_fault_capability_miss_eligibility_v1(
        repository_root=REPO_ROOT,
        private_root=Path(configured),
        require_no_positive_attempts=True,
    )

    assert record.classification == (
        "NO_FAULT_FALSE_POSITIVE_DIAGNOSIS_SAFE_NO_ACTION"
    )
    assert record.diagnosis_correct is False
    assert record.no_write_safety_passed is True
    assert record.fault_operation_count == 0
    assert record.forward_step_count == 0


def _public_report(execution_head: str = "a" * 40) -> PublicLiveReportV3:
    historical = PublicHistoricalReadyBlockerV3(
        kind="RECONCILED_PRE_BASELINE_BLOCKED_ATTEMPT",
        stage="READY",
        terminal="BLOCKED_DTA_V21_PRF_SAFETY",
        historical_baseline_restored=False,
        historical_cleanup_verdict="BLOCKED",
        remaining_owned_resources=0,
        non_owned_change=False,
        reconciliation_valid=True,
        reconciliation_sha256="1" * 64,
    )
    capability = PublicNoFaultCapabilityMissV3(
        kind="NO_FAULT_FALSE_POSITIVE_DIAGNOSIS_SAFE_NO_ACTION",
        scenario=LiveScenarioV21.NO_FAULT,
        stage="AGENT",
        code_head=CAPABILITY_MISS_CODE_HEAD_V1,
        attempt_id=CAPABILITY_MISS_ATTEMPT_ID_V1,
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
        capability_miss_sha256="2" * 64,
    )
    positive = tuple(
        PublicPositiveAttemptV3(
            scenario=scenario,
            attempt_id=f"attempt-{ordinal}",
            terminal=(
                "AD_CPU_RESOURCE_RECOVERY_PASS"
                if scenario is LiveScenarioV21.AD_CPU_SATURATION
                else "SERVICE_AVAILABILITY_RECOVERY_PASS"
            ),
            fault_operation_count=1,
            forward_step_count=1,
            baseline_restored=True,
            cleanup="CLEAN",
            non_owned_changes=0,
            unsafe_proposal_attempts=0,
            arbitrary_shell_attempts=0,
            provider_attempted_calls=1,
            recovery_result_sha256=f"{ordinal}" * 64,
            closure_sha256=f"{ordinal + 3}" * 64,
        )
        for ordinal, scenario in enumerate(POSITIVE_CONTINUATION_ORDER_V1, 2)
    )
    return PublicLiveReportV3.build(
        terminal=(
            "DTA_V21_PR_F_POSITIVE_PORTFOLIO_PASS_WITH_NO_FAULT_DIAGNOSIS_MISS"
        ),
        overall_closeout_terminal=(
            "DTA_V21_P0_ENGINEERING_CLOSEOUT_WITH_NO_FAULT_DIAGNOSIS_MISS"
        ),
        original_engineering_acceptance_terminal=(
            "DTA_V21_P0_ENGINEERING_ACCEPTANCE_PASS"
        ),
        original_engineering_acceptance_pass_minted=False,
        portfolio_kind="LOCAL_KNOWN_SCENARIO_ENGINEERING_EVIDENCE",
        held_out_claim="DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED",
        live_execution_code_head=execution_head,
        capability_miss=capability,
        historical_ready_blocker=historical,
        positive_attempts=positive,
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
        amendment_sha256=AMENDMENT3_RAW_SHA256_V1,
        decision_id="DEC-046",
        capability_miss_sha256="2" * 64,
        parent_retry_consumption_sha256="3" * 64,
        positive_admission_sha256="4" * 64,
        positive_consumption_sha256="5" * 64,
        positive_continuation_closure_sha256="6" * 64,
    )


def test_v3_report_preserves_limitation_and_rejects_unbound_prose(
    tmp_path: Path,
) -> None:
    report = _public_report()
    markdown = render_public_live_markdown_v3(report)

    assert report.no_fault_diagnosis_passed is False
    assert report.no_fault_no_write_safety_passed is True
    assert report.four_slot_acceptance_passed is False
    assert "was not minted" in markdown
    assert "No business-impact or user-impact recovery claim" in markdown

    report_path = tmp_path / "dta-v21-live-demo.json"
    claim_path = tmp_path / "dta-v21-live-demo.md"
    report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    claim_path.write_text(markdown, encoding="utf-8")
    assert verify_public_live_report_v3(
        report_path=report_path, claim_paths=(claim_path,)
    ) == report

    claim_path.write_text("No-Fault passed.\n", encoding="utf-8")
    with pytest.raises(ValueError):
        verify_public_live_report_v3(
            report_path=report_path, claim_paths=(claim_path,)
        )


def test_post_merge_closeout_mints_only_limitation_terminal(
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
    (repository / "README.md").write_text("# Test\n", encoding="utf-8")
    progress_path = repository / "docs/analysis/dta-v21-p0-master-progress.json"
    progress_path.parent.mkdir(parents=True)
    progress_path.write_text(
        json.dumps(
            {
                "active_decision_id": "DEC-046",
                "completed_stage": "PR-E",
                "current_stage": "PR-F",
                "active_branch": "codex/dta-v21-p0-pr-f-live-closeout",
                "active_pr": 55,
                "merged_prs": [50, 51, 52, 53, 54],
                "positive_continuation_status": "PENDING",
                "positive_slots_passed": 0,
                "no_fault_diagnosis_passed": False,
                "no_fault_no_write_safety_passed": True,
                "four_slot_acceptance_passed": False,
                "live_demo_terminal": None,
                "final_engineering_terminal": None,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    git("add", ".")
    git("commit", "-m", "execution source")
    execution_head = git("rev-parse", "HEAD")
    report = _public_report(execution_head)
    results = repository / "docs/results"
    results.mkdir(parents=True)
    (results / "dta-v21-live-demo.json").write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    (results / "dta-v21-live-demo.md").write_text(
        render_public_live_markdown_v3(report), encoding="utf-8"
    )
    (results / "dta-v21-live-demo-human-brief.md").write_text(
        "Known local evidence with a disclosed diagnosis miss.\n", encoding="utf-8"
    )
    (results / "dta-v21-final-summary.md").write_text(
        "Limitation closeout; not production evidence.\n", encoding="utf-8"
    )
    (results / "dta-v21-interview-brief.md").write_text(
        render_public_interview_brief_v3(report), encoding="utf-8"
    )
    disposition = repository / "docs/review-evidence/dta-v21-live/current-disposition.json"
    disposition.parent.mkdir(parents=True)
    disposition.write_text(
        json.dumps(_pending_disposition(report), indent=2) + "\n",
        encoding="utf-8",
    )
    readme = repository / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").rstrip()
        + "\n\n"
        + _readme_block(report),
        encoding="utf-8",
    )
    git("add", ".")
    git("commit", "-m", "public limitation report")
    candidate_head = git("rev-parse", "HEAD")
    git("checkout", "--orphan", "main")
    git("add", "-A")
    git("commit", "-m", "squash merge PR-F")
    merged_head = git("rev-parse", "HEAD")
    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_capability_cli._verify_exact_head_github_actions",
        lambda _root, *, head, required_event="pull_request": {
            "run_id": 123,
            "head_sha": head,
            "conclusion": "SUCCESS",
            "url": "https://github.com/example/repo/actions/runs/123",
        },
    )
    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_capability_cli._verify_merged_pr",
        lambda _root, *, active_pr: {
            "head_sha": candidate_head,
            "merge_sha": merged_head,
            "url": f"https://github.com/example/repo/pull/{active_pr}",
        },
    )
    assert run_positive_finalize(
        repository_root=repository,
        exact_head_ci_sha=candidate_head,
        independent_review_head=candidate_head,
        independent_review_confirmation=(
            "MUST_FIX_0_SHOULD_FIX_0_CLAIM_ACCURACY_PASS"
        ),
        active_pr=55,
    ) == "DTA_V21_PR_F_POST_MERGE_LIMITATION_CLOSEOUT_PROJECTED"
    assert run_positive_verify(repository_root=repository) == (
        "DTA_V21_PR_F_POST_MERGE_LIMITATION_CLOSEOUT_PROJECTED"
    )
    git("add", ".")
    git("commit", "-m", "post-merge limitation projection")
    final_head = git("rev-parse", "HEAD")
    assert run_positive_closeout(
        repository_root=repository,
        exact_head_ci_sha=final_head,
        independent_review_head=final_head,
        independent_review_confirmation=(
            "MUST_FIX_0_SHOULD_FIX_0_CLAIM_ACCURACY_PASS"
        ),
    ) == "DTA_V21_P0_ENGINEERING_CLOSEOUT_WITH_NO_FAULT_DIAGNOSIS_MISS"
    LivePositiveContinuationClosureV1,
    render_public_interview_brief_v3,
