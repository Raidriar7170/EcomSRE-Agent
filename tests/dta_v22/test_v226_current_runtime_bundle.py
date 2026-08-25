from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.current_runtime_bundle_v226 import (
    run_current_runtime_bundle_v226,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import RealFaultOpaqueCaptureV1
from ecomsre.dta_v2.v22.real_fault_comparison_contracts_v226 import (
    RealFaultArmStatusV226,
)
from ecomsre.dta_v2.v22.real_fault_selection_v226 import (
    RealFaultSelectionDecisionV226,
    RealFaultSelectionOutcomeV226,
)
from ecomsre.dta_v2.v22.real_fault_stage_trace_v226 import RealFaultStageV226


ROOT = Path(__file__).resolve().parents[2]
CASE_IDS = (
    "fault-map-a",
    "fault-map-b",
    "baseline-map-a",
    "baseline-map-b",
)


def _capture(case_id: str) -> RealFaultOpaqueCaptureV1:
    path = ROOT / f"config/dta-v225-real-fault/captures/{case_id}.json"
    return RealFaultOpaqueCaptureV1.model_validate_json(path.read_text())


class _TerminalSelectingProvider:
    def __init__(self) -> None:
        self.requests = []

    def complete_selection(
        self,
        *,
        request,
        run_id: str,
        max_protocol_repairs: int = 2,
    ) -> RealFaultSelectionOutcomeV226:
        assert len(run_id) == 32
        assert max_protocol_repairs == 2
        assert request.actions == ()
        self.requests.append(request)
        selected = next(
            (
                item
                for item in request.terminals
                if item.terminal_kind == "CPU_SATURATION"
            ),
            None,
        )
        if selected is None:
            selected = next(
                item
                for item in request.terminals
                if item.terminal_kind == "NO_INCIDENT"
            )
        return RealFaultSelectionOutcomeV226(
            decision=RealFaultSelectionDecisionV226(
                selection=selected.alias,
                focus="NONE",
            ),
            first_pass_protocol_success=True,
            post_repair_protocol_success=True,
            protocol_repairs=0,
            provider_calls=1,
            transport_retry_count=0,
            input_tokens=100,
            output_tokens=8,
            total_tokens=108,
            latency_ms=5.0,
        )


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_current_executes_one_target_complete_bundle_on_old_capture(
    case_id: str,
) -> None:
    capture = _capture(case_id)
    baseline_case = (
        f"baseline-{case_id.split('-', 1)[1]}"
        if case_id.startswith("fault-")
        else case_id
    )
    provider = _TerminalSelectingProvider()

    run = run_current_runtime_bundle_v226(
        capture=capture,
        baseline_capture=_capture(baseline_case),
        model_id="deterministic-v226",
        provider=provider,
    )

    assert run.status is RealFaultArmStatusV226.VALID_TERMINAL
    assert run.trace.last_completed_stage is RealFaultStageV226.COMPLETE
    assert run.trace.failure_stage is None
    assert run.strictly_ambiguous is False
    assert run.comparison_set_size == 2
    assert run.bundle_eligible is True
    assert run.bundle_dispatched is True
    assert run.bundle_target_count == 2
    assert run.resource_read_shape == "MULTI_TARGET"
    assert run.resources_selected is True
    assert run.all_candidates_covered is True
    assert run.semantic_evidence_actions == 1
    assert run.target_equivalent_reads == 2
    assert run.provider_turns == 1
    assert run.provider_calls == 1
    assert run.protocol_repairs == 0
    assert run.agent_writes == 0
    assert run.action_proposals == 0
    assert run.runbook_executions == 0
    assert run.run_sha256 == run.recompute_sha256()
    if case_id.startswith("fault-"):
        expected_root = next(
            item.service
            for item in capture.capture.resources
            if max(sample.cpu_percent for sample in item.samples) >= 80.0
        )
        assert run.prediction.terminal == "DIAGNOSED"
        assert run.prediction.root_service_alias == expected_root
        assert run.prediction.mechanism == "CPU_SATURATION"
    else:
        assert run.prediction.terminal == "NO_INCIDENT"
        assert run.prediction.root_service_alias is None


def test_current_stage_trace_contains_every_required_transition() -> None:
    provider = _TerminalSelectingProvider()
    run = run_current_runtime_bundle_v226(
        capture=_capture("fault-map-a"),
        baseline_capture=_capture("baseline-map-a"),
        model_id="deterministic-v226",
        provider=provider,
    )

    assert tuple(event.stage for event in run.trace.stage_events) == (
        RealFaultStageV226.INPUT_VALIDATION,
        RealFaultStageV226.BOOTSTRAP_ACTION_BUILD,
        RealFaultStageV226.BOOTSTRAP_DISPATCH,
        RealFaultStageV226.BOOTSTRAP_MEMORY_BUILD,
        RealFaultStageV226.BASELINE_PROFILE_BUILD,
        RealFaultStageV226.HYPOTHESIS_CATALOG_BUILD,
        RealFaultStageV226.GAP_GRAPH_BUILD,
        RealFaultStageV226.ACTION_CATALOG_BUILD,
        RealFaultStageV226.RESOURCE_COMPARISON_SET_BUILD,
        RealFaultStageV226.BUNDLE_BUILD,
        RealFaultStageV226.BUNDLE_DISPATCH,
        RealFaultStageV226.POST_READ_MEMORY_BUILD,
        RealFaultStageV226.POST_READ_GAP_BUILD,
        RealFaultStageV226.TERMINAL_CATALOG_BUILD,
        RealFaultStageV226.PROVIDER_TERMINAL_SELECTION,
        RealFaultStageV226.TERMINAL_BIND,
        RealFaultStageV226.COMPLETE,
    )
