from __future__ import annotations

from ecomsre.dta_v2.v22.real_fault_comparison_contracts_v225 import (
    RealFaultCaseTruthV1,
    RealFaultArmStatus,
    RealFaultLiveShadowRun,
    RealFaultShadowPrediction,
    RealFaultStudyArm,
    build_real_fault_arm_run_v225,
    build_real_fault_schedule_v225,
    build_real_fault_study_execution_v225,
)
from ecomsre.dta_v2.v22.real_fault_shadow_scorer_v225 import (
    RealFaultComparisonDisposition,
    RealFaultTransferTerminal,
    score_real_fault_study_v225,
)


ALIASES = {"a": "svc-1111111111", "b": "svc-2222222222"}


def _truth(case_id: str) -> RealFaultCaseTruthV1:
    fault = case_id.startswith("fault-")
    map_name = case_id[-1]
    return RealFaultCaseTruthV1(
        schema_version="dta-v225-real-fault.case-truth.v1",
        case_id=case_id,
        case_kind="AD_CPU_FAULT" if fault else "BASELINE",
        expected_root_alias=ALIASES[map_name] if fault else None,
        expected_fault_domain="LOCAL_RESOURCE" if fault else None,
        expected_mechanism="CPU_SATURATION" if fault else None,
    )


def _run(case_id: str, arm: RealFaultStudyArm):
    fault = case_id.startswith("fault-")
    prediction = RealFaultShadowPrediction(
        schema_version="dta-v225-real-fault.shadow-prediction.v1",
        terminal="DIAGNOSED" if fault else "NO_INCIDENT",
        root_service_alias=ALIASES[case_id[-1]] if fault else None,
        fault_domain="LOCAL_RESOURCE" if fault else None,
        mechanism="CPU_SATURATION" if fault else None,
        supporting_evidence_refs=("e:resource:1",) if fault else (),
        evidence_clause_valid=True,
    )
    current = arm is RealFaultStudyArm.CURRENT_RUNTIME_BUNDLE
    return build_real_fault_arm_run_v225(
        case_id=case_id,
        arm=arm,
        case_bytes_sha256=("a" if case_id.endswith("a") else "b") * 64,
        model_id="gpt-test",
        status=RealFaultArmStatus.VALID_TERMINAL,
        prediction=prediction,
        first_useful_evidence_ordinal=1,
        resources_requested=True,
        resource_read_shape="MULTI_TARGET",
        all_candidates_covered=True,
        semantic_evidence_actions=1,
        target_equivalent_reads=2,
        provider_turns=1 if current else 2,
        provider_calls=1 if current else 2,
        input_tokens=10 if current else 20,
        output_tokens=2 if current else 4,
        total_tokens=12 if current else 24,
        latency_ms=1.0 if current else 2.0,
        protocol_failures=0,
        transport_retries=0,
        duplicate_read_attempts=0,
        empty_read_count=0,
        predicate_yield_count=int(fault),
        bundle_resources_reads=int(current),
    )


def test_truth_late_execution_and_scorer_mint_supported_current_advantage() -> None:
    schedule = build_real_fault_schedule_v225()
    runs = tuple(_run(item.case_id, item.arm) for item in schedule)
    execution = build_real_fault_study_execution_v225(runs=runs)
    truths = tuple(_truth(case_id) for case_id in sorted({item.case_id for item in runs}))
    live_fault = RealFaultLiveShadowRun(
        schema_version="dta-v225-real-fault.live-shadow-run.v1",
        backend="LocalSandboxReadBackend",
        case_kind="AD_CPU_FAULT",
        arm_run=_run("fault-map-a", RealFaultStudyArm.CURRENT_RUNTIME_BUNDLE),
        live_read_only=True,
        agent_writes=0,
        action_proposals=0,
        runbook_executions=0,
    )

    score = score_real_fault_study_v225(
        execution=execution,
        truths=truths,
        live_fault=live_fault,
        live_baseline=None,
        live_baseline_omission_reason="Optional baseline shadow omitted by protocol.",
        baseline_restored=True,
        cleanup="CLEAN",
        non_owned_changes=0,
    )

    assert execution.execution_count == 1
    assert execution.truth_load_after_run_ordinals == (2, 4, 6, 8)
    assert score.transfer_terminal is RealFaultTransferTerminal.SUPPORTED
    assert (
        score.comparison_disposition
        is RealFaultComparisonDisposition.CURRENT_ADVANTAGE
    )
    assert score.statistical_significance_testing_performed is False


def test_transfer_fails_closed_when_cleanup_is_not_clean() -> None:
    schedule = build_real_fault_schedule_v225()
    runs = tuple(_run(item.case_id, item.arm) for item in schedule)
    execution = build_real_fault_study_execution_v225(runs=runs)
    truths = tuple(_truth(case_id) for case_id in sorted({item.case_id for item in runs}))
    live_fault = RealFaultLiveShadowRun(
        schema_version="dta-v225-real-fault.live-shadow-run.v1",
        backend="LocalSandboxReadBackend",
        case_kind="AD_CPU_FAULT",
        arm_run=_run("fault-map-a", RealFaultStudyArm.CURRENT_RUNTIME_BUNDLE),
        live_read_only=True,
        agent_writes=0,
        action_proposals=0,
        runbook_executions=0,
    )

    score = score_real_fault_study_v225(
        execution=execution,
        truths=truths,
        live_fault=live_fault,
        live_baseline=None,
        live_baseline_omission_reason="Optional baseline shadow omitted by protocol.",
        baseline_restored=True,
        cleanup="NOT_CLEAN",
        non_owned_changes=0,
    )

    assert score.transfer_terminal is RealFaultTransferTerminal.NOT_SUPPORTED
