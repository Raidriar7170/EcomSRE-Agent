#!/usr/bin/env python3
"""Eight-run deterministic development gate over the frozen PR #67 captures."""

from __future__ import annotations

import json
from pathlib import Path

from ecomsre.dta_v2.v22.current_runtime_bundle_v226 import (
    run_current_runtime_bundle_v226,
)
from ecomsre.dta_v2.v22.model_directed_retrieval_v226 import (
    run_model_directed_retrieval_v226,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import RealFaultOpaqueCaptureV1
from ecomsre.dta_v2.v22.real_fault_comparison_contracts_v226 import (
    RealFaultArmRunV226,
    RealFaultArmStatusV226,
    RealFaultStudyArmV226,
)
from ecomsre.dta_v2.v22.real_fault_selection_v226 import (
    RealFaultSelectionDecisionV226,
    RealFaultSelectionOutcomeV226,
)


CASE_IDS = (
    "fault-map-a",
    "fault-map-b",
    "baseline-map-a",
    "baseline-map-b",
)


class DeterministicOldCaptureProviderV226:
    """Choose target-complete Resources, then an admitted exact-kind terminal."""

    def complete_selection(
        self,
        *,
        request: object,
        run_id: str,
        max_protocol_repairs: int = 2,
    ) -> RealFaultSelectionOutcomeV226:
        del run_id
        if max_protocol_repairs != 2:
            raise ValueError("deterministic gate repair bound differs")
        visible = request
        terminals = visible.terminals  # type: ignore[attr-defined]
        if terminals:
            selected = next(
                (
                    item
                    for item in terminals
                    if item.terminal_kind == "CPU_SATURATION"
                ),
                None,
            ) or next(
                item for item in terminals if item.terminal_kind == "NO_INCIDENT"
            )
            decision = RealFaultSelectionDecisionV226(
                selection=selected.alias,
                focus="NONE",
            )
        else:
            selected = next(
                item
                for item in visible.actions  # type: ignore[attr-defined]
                if item.source.value == "RESOURCES"
                and len(item.target_aliases) == 2
            )
            focus = next(
                item
                for item in visible.focuses  # type: ignore[attr-defined]
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
            input_tokens=100,
            output_tokens=8,
            total_tokens=108,
            latency_ms=5.0,
        )


def _capture(root: Path, case_id: str) -> RealFaultOpaqueCaptureV1:
    return RealFaultOpaqueCaptureV1.model_validate_json(
        (root / f"config/dta-v225-real-fault/captures/{case_id}.json").read_bytes()
    )


def _exact(run: RealFaultArmRunV226, truth: dict[str, object]) -> bool:
    if run.status is not RealFaultArmStatusV226.VALID_TERMINAL:
        return False
    if truth["case_kind"] == "BASELINE":
        return (
            run.prediction.terminal == "NO_INCIDENT"
            and run.prediction.root_service_alias is None
            and run.prediction.fault_domain is None
            and run.prediction.mechanism is None
            and run.prediction.evidence_clause_valid
        )
    return (
        run.prediction.terminal == "DIAGNOSED"
        and run.prediction.root_service_alias == truth["expected_root_alias"]
        and run.prediction.fault_domain == "LOCAL_RESOURCE"
        and run.prediction.mechanism == "CPU_SATURATION"
        and run.prediction.evidence_clause_valid
    )


def build_gate(root: Path) -> dict[str, object]:
    runs: list[RealFaultArmRunV226] = []
    for case_id in CASE_IDS:
        capture = _capture(root, case_id)
        baseline = _capture(root, f"baseline-{case_id.split('-', 1)[1]}")
        runs.extend(
            (
                run_model_directed_retrieval_v226(
                    capture=capture,
                    baseline_capture=baseline,
                    model_id="deterministic-v226",
                    provider=DeterministicOldCaptureProviderV226(),
                ),
                run_current_runtime_bundle_v226(
                    capture=capture,
                    baseline_capture=baseline,
                    model_id="deterministic-v226",
                    provider=DeterministicOldCaptureProviderV226(),
                ),
            )
        )
    truth_raw = json.loads(
        (root / "config/dta-v225-real-fault/truth.json").read_bytes()
    )
    truth_by_case = {item["case_id"]: item for item in truth_raw["truths"]}
    rows = tuple(
        {
            "case_id": run.case_id,
            "arm": run.arm.value,
            "status": run.status.value,
            "terminal": run.prediction.terminal,
            "exact": _exact(run, truth_by_case[run.case_id]),
            "semantic_evidence_actions": run.semantic_evidence_actions,
            "target_equivalent_reads": run.target_equivalent_reads,
            "all_candidates_covered": run.all_candidates_covered,
            "bundle_dispatched": run.bundle_dispatched,
            "provider_calls": run.provider_calls,
            "protocol_failures": run.protocol_failures,
        }
        for run in runs
    )
    arms: dict[str, object] = {}
    for arm in RealFaultStudyArmV226:
        selected = tuple(
            (run, row)
            for run, row in zip(runs, rows, strict=True)
            if run.arm is arm
        )
        arms[arm.value] = {
            "valid_terminals": sum(
                run.status is RealFaultArmStatusV226.VALID_TERMINAL
                for run, _ in selected
            ),
            "exact": sum(bool(row["exact"]) for _, row in selected),
            "fault_exact": sum(
                bool(row["exact"]) and run.case_id.startswith("fault-")
                for run, row in selected
            ),
            "baseline_exact": sum(
                bool(row["exact"]) and run.case_id.startswith("baseline-")
                for run, row in selected
            ),
        }
    current = tuple(
        run for run in runs if run.arm is RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE
    )
    model = tuple(
        run
        for run in runs
        if run.arm is RealFaultStudyArmV226.MODEL_DIRECTED_RETRIEVAL
    )
    passed = (
        len(runs) == 8
        and all(
            value
            == {
                "valid_terminals": 4,
                "exact": 4,
                "fault_exact": 2,
                "baseline_exact": 2,
            }
            for value in arms.values()
        )
        and all(
            run.bundle_dispatched
            and run.semantic_evidence_actions == 1
            and run.target_equivalent_reads == 2
            and run.all_candidates_covered
            for run in current
        )
        and all(
            run.resources_selected
            and run.status is RealFaultArmStatusV226.VALID_TERMINAL
            and not run.protocol_failures
            for run in model
        )
    )
    return {
        "schema_version": "dta-v226-real-fault.deterministic-old-capture-gate.v1",
        "development_fixture_only": True,
        "execution_count": 1,
        "arm_run_count": len(runs),
        "arms": arms,
        "runs": rows,
        "docker_calls": 0,
        "provider_network_calls": 0,
        "agent_writes": 0,
        "action_proposals": 0,
        "runbook_executions": 0,
        "status": (
            "DTA_V226_DETERMINISTIC_OLD_CAPTURE_GATE_PASS"
            if passed
            else "DTA_V226_DETERMINISTIC_OLD_CAPTURE_GATE_FAILED"
        ),
    }


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    gate = build_gate(root)
    print(json.dumps(gate, sort_keys=True))
    if gate["status"] != "DTA_V226_DETERMINISTIC_OLD_CAPTURE_GATE_PASS":
        return 1
    print(gate["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
