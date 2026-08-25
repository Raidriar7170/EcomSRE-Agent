from __future__ import annotations

from pathlib import Path

from ecomsre.dta_v2.v22.real_fault_capture_v225 import RealFaultOpaqueCaptureV1
from ecomsre.dta_v2.v22.real_fault_comparison_contracts_v226 import (
    RealFaultStudyArmV226,
)
from ecomsre.dta_v2.v22.real_fault_live_shadow_v226 import (
    RealFaultLiveShadowRunV226,
)
from ecomsre.dta_v2.v22.real_fault_scorer_v226 import (
    RealFaultComparisonDispositionV226,
    RealFaultTransferTerminalV226,
    score_real_fault_study_v226,
)
from ecomsre.dta_v2.v22.real_fault_selection_v226 import (
    RealFaultSelectionDecisionV226,
    RealFaultSelectionOutcomeV226,
)
from ecomsre.dta_v2.v22.real_fault_selection_provider_v226 import (
    RealFaultSelectionProtocolFailureV226,
)
from ecomsre.dta_v2.v22.real_fault_study_v226 import (
    RealFaultCaseTruthV226,
    build_real_fault_schedule_v226,
    execute_real_fault_study_v226,
)


ROOT = Path(__file__).resolve().parents[2]


def _capture(case_id: str) -> RealFaultOpaqueCaptureV1:
    return RealFaultOpaqueCaptureV1.model_validate_json(
        (ROOT / f"config/dta-v225-real-fault/captures/{case_id}.json").read_bytes()
    )


CAPTURES = {
    case_id: _capture(case_id)
    for case_id in (
        "fault-map-a",
        "fault-map-b",
        "baseline-map-a",
        "baseline-map-b",
    )
}


class _ExactProvider:
    def complete_selection(self, *, request, run_id, max_protocol_repairs=2):
        del run_id, max_protocol_repairs
        if request.terminals:
            selected = next(
                (
                    item
                    for item in request.terminals
                    if item.terminal_kind == "CPU_SATURATION"
                ),
                None,
            ) or next(
                item
                for item in request.terminals
                if item.terminal_kind == "NO_INCIDENT"
            )
            decision = RealFaultSelectionDecisionV226(
                selection=selected.alias,
                focus="NONE",
            )
        else:
            selected = next(
                item
                for item in request.actions
                if item.source.value == "RESOURCES"
                and len(item.target_aliases) == 2
            )
            focus = next(
                item
                for item in request.focuses
                if item.mechanism == "CPU_SATURATION"
            )
            decision = RealFaultSelectionDecisionV226(
                selection=selected.alias,
                focus=focus.alias,
            )
        return RealFaultSelectionOutcomeV226(
            decision=decision,
            first_pass_protocol_success=True,
            post_repair_protocol_success=True,
            protocol_repairs=0,
            provider_calls=1,
            transport_retry_count=0,
            input_tokens=20,
            output_tokens=4,
            total_tokens=24,
            latency_ms=1.0,
        )


def _truth(case_id: str) -> RealFaultCaseTruthV226:
    capture = CAPTURES[case_id]
    fault = case_id.startswith("fault-")
    root = (
        next(
            item.service
            for item in capture.capture.resources
            if max(sample.cpu_percent for sample in item.samples) >= 80.0
        )
        if fault
        else None
    )
    return RealFaultCaseTruthV226(
        schema_version="dta-v226-real-fault.case-truth.v1",
        case_id=case_id,
        case_kind="AD_CPU_FAULT" if fault else "BASELINE",
        expected_root_alias=root,
        expected_fault_domain="LOCAL_RESOURCE" if fault else None,
        expected_mechanism="CPU_SATURATION" if fault else None,
    )


def _execution():
    truth_loads: list[str] = []

    def load_truth(case_id: str) -> RealFaultCaseTruthV226:
        truth_loads.append(case_id)
        return _truth(case_id)

    execution, truths = execute_real_fault_study_v226(
        execution_id="exec-v226-0123456789abcdef",
        captures=CAPTURES,
        model_id="deterministic-v226",
        provider_factory=_ExactProvider,
        truth_loader=load_truth,
    )
    return execution, truths, truth_loads


def test_v226_final_schedule_is_exact_truth_late_and_single_execution() -> None:
    assert tuple(
        (item.ordinal, item.case_id, item.case_local_position, item.arm.value)
        for item in build_real_fault_schedule_v226()
    ) == (
        (1, "fault-map-a", 1, "MODEL_DIRECTED_RETRIEVAL"),
        (2, "fault-map-a", 2, "CURRENT_RUNTIME_BUNDLE"),
        (3, "fault-map-b", 1, "CURRENT_RUNTIME_BUNDLE"),
        (4, "fault-map-b", 2, "MODEL_DIRECTED_RETRIEVAL"),
        (5, "baseline-map-a", 1, "MODEL_DIRECTED_RETRIEVAL"),
        (6, "baseline-map-a", 2, "CURRENT_RUNTIME_BUNDLE"),
        (7, "baseline-map-b", 1, "CURRENT_RUNTIME_BUNDLE"),
        (8, "baseline-map-b", 2, "MODEL_DIRECTED_RETRIEVAL"),
    )

    execution, truths, truth_loads = _execution()

    assert execution.execution_count == 1
    assert execution.arm_run_count == 8
    assert execution.truth_load_after_run_ordinals == (2, 4, 6, 8)
    assert execution.run_attempts_per_ordinal == (1, 1, 1, 1, 1, 1, 1, 1)
    assert execution.score_driven_retries == 0
    assert execution.no_retry_after_valid_terminal is True
    assert truth_loads == [
        "fault-map-a",
        "fault-map-b",
        "baseline-map-a",
        "baseline-map-b",
    ]
    assert tuple(item.case_id for item in truths) == tuple(truth_loads)


def _live_shadow(execution, case_id: str) -> RealFaultLiveShadowRunV226:
    run = next(
        item
        for item in execution.runs
        if item.case_id == case_id
        and item.arm is RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE
    )
    return RealFaultLiveShadowRunV226(
        schema_version="dta-v226-real-fault.live-shadow-run.v1",
        backend="LocalSandboxReadBackend",
        case_kind="AD_CPU_FAULT" if case_id.startswith("fault-") else "BASELINE",
        arm_run=run,
        backend_identity_verified=True,
        resource_request_target_count=2,
        physical_multi_target=True,
        opaque_remap_complete=True,
        live_read_only=True,
        agent_writes=0,
        action_proposals=0,
        runbook_executions=0,
    )


def test_v226_shared_scorer_applies_transfer_and_acquisition_rules() -> None:
    execution, truths, _truth_loads = _execution()

    score = score_real_fault_study_v226(
        execution=execution,
        truths=truths,
        live_fault=_live_shadow(execution, "fault-map-a"),
        live_baseline=_live_shadow(execution, "baseline-map-a"),
        baseline_restored=True,
        cleanup="CLEAN",
        non_owned_changes=0,
    )

    assert score.all_snapshot_runs_valid is True
    assert score.transfer_terminal is RealFaultTransferTerminalV226.SUPPORTED
    assert (
        score.comparison_disposition
        is RealFaultComparisonDispositionV226.CURRENT_ADVANTAGE
    )
    assert score.comparison_admissible is True
    assert score.current_snapshot_exact_count == 4
    assert score.current_live_fault_exact is True
    assert score.current_live_baseline_exact is True
    assert score.premature_no_incident_count == 0
    assert score.false_positive_fault_on_baseline_count == 0


class _FailedProvider:
    def complete_selection(self, **_kwargs):
        raise RealFaultSelectionProtocolFailureV226(
            "UNKNOWN_ALIAS_KIND",
            provider_calls=3,
            protocol_repairs=2,
            transport_retry_count=0,
            input_tokens=30,
            output_tokens=6,
            latency_ms=2.0,
            transport_failure=False,
        )


def test_v226_failed_arm_forbids_cost_based_comparison_award() -> None:
    provider_ordinal = 0

    def provider_factory():
        nonlocal provider_ordinal
        provider_ordinal += 1
        return _FailedProvider() if provider_ordinal == 1 else _ExactProvider()

    execution, truths = execute_real_fault_study_v226(
        execution_id="exec-v226-fedcba9876543210",
        captures=CAPTURES,
        model_id="deterministic-v226",
        provider_factory=provider_factory,
        truth_loader=_truth,
    )
    score = score_real_fault_study_v226(
        execution=execution,
        truths=truths,
        live_fault=_live_shadow(execution, "fault-map-a"),
        live_baseline=_live_shadow(execution, "baseline-map-a"),
        baseline_restored=True,
        cleanup="CLEAN",
        non_owned_changes=0,
    )

    assert provider_ordinal == 8
    assert execution.arm_run_count == 8
    assert execution.score_driven_retries == 0
    assert score.all_snapshot_runs_valid is False
    assert score.comparison_admissible is False
    assert score.comparison_disposition is None
