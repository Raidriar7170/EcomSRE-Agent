#!/usr/bin/env python3
"""Run one bounded eight-arm real-Provider gate over the PR #67 captures."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
from typing import cast

from ecomsre.dta_v2.provider_env import load_private_provider_env
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
from ecomsre.dta_v2.v22.real_fault_selection_provider_v226 import (
    RealFaultSelectionProviderAdapterV226,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


SCHEDULE = (
    ("fault-map-a", RealFaultStudyArmV226.MODEL_DIRECTED_RETRIEVAL),
    ("fault-map-a", RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE),
    ("fault-map-b", RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE),
    ("fault-map-b", RealFaultStudyArmV226.MODEL_DIRECTED_RETRIEVAL),
    ("baseline-map-a", RealFaultStudyArmV226.MODEL_DIRECTED_RETRIEVAL),
    ("baseline-map-a", RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE),
    ("baseline-map-b", RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE),
    ("baseline-map-b", RealFaultStudyArmV226.MODEL_DIRECTED_RETRIEVAL),
)


def _capture(root: Path, case_id: str) -> RealFaultOpaqueCaptureV1:
    return RealFaultOpaqueCaptureV1.model_validate_json(
        (root / f"config/dta-v225-real-fault/captures/{case_id}.json").read_bytes()
    )


def build_provider_gate(
    *,
    root: Path,
    provider_env_path: Path,
) -> dict[str, object]:
    values = load_private_provider_env(provider_env_path)
    config = OpenAICompatibleConfig(
        base_url=values["ECOMSRE_LLM_BASE_URL"],
        api_key=values["ECOMSRE_LLM_API_KEY"],
        model=values["ECOMSRE_LLM_MODEL"],
    )
    provider = RealFaultSelectionProviderAdapterV226(config=config)
    captures = {case_id: _capture(root, case_id) for case_id, _arm in SCHEDULE}
    runs: list[RealFaultArmRunV226] = []
    for case_id, arm in SCHEDULE:
        capture = captures[case_id]
        baseline = captures[f"baseline-{case_id.split('-', 1)[1]}"]
        runner = (
            run_model_directed_retrieval_v226
            if arm is RealFaultStudyArmV226.MODEL_DIRECTED_RETRIEVAL
            else run_current_runtime_bundle_v226
        )
        runs.append(
            runner(
                capture=capture,
                baseline_capture=baseline,
                model_id=config.model,
                provider=provider,
            )
        )
    current = tuple(
        run for run in runs if run.arm is RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE
    )
    model = tuple(
        run
        for run in runs
        if run.arm is RealFaultStudyArmV226.MODEL_DIRECTED_RETRIEVAL
    )
    valid = sum(
        run.status is RealFaultArmStatusV226.VALID_TERMINAL for run in runs
    )
    protocol_failures = sum(run.protocol_failures for run in runs)
    runner_failures = sum(run.runner_failures for run in runs)
    transport_failures = sum(run.transport_failures for run in runs)
    passed = (
        len(runs) == 8
        and valid == 8
        and protocol_failures == 0
        and runner_failures == 0
        and transport_failures == 0
        and len(current) == 4
        and all(
            run.bundle_dispatched
            and run.bundle_target_count == 2
            and run.provider_turns == 1
            and run.prediction.terminal != "FAILED"
            for run in current
        )
        and len(model) == 4
        and all(run.provider_turns >= 1 for run in model)
        and all(
            run.agent_writes == 0
            and run.action_proposals == 0
            and run.runbook_executions == 0
            for run in runs
        )
    )
    return {
        "schema_version": "dta-v226-real-fault.provider-old-capture-gate.v1",
        "development_only": True,
        "execution_count": 1,
        "arm_run_count": len(runs),
        "model_id": config.model,
        "schedule": [
            {"ordinal": index, "case_id": case_id, "arm": arm.value}
            for index, (case_id, arm) in enumerate(SCHEDULE, start=1)
        ],
        "runs": [run.model_dump(mode="json") for run in runs],
        "summary": {
            "valid_terminals": valid,
            "protocol_failures": protocol_failures,
            "runner_failures": runner_failures,
            "transport_failures": transport_failures,
            "current_bundle_dispatches": sum(run.bundle_dispatched for run in current),
            "current_provider_terminal_selections": sum(
                run.provider_turns == 1 and run.prediction.terminal != "FAILED"
                for run in current
            ),
            "model_provider_outputs_parsed": sum(
                run.status is RealFaultArmStatusV226.VALID_TERMINAL for run in model
            ),
            "provider_calls": sum(run.provider_calls for run in runs),
            "protocol_repairs": sum(run.protocol_repairs for run in runs),
            "transport_retries": sum(run.transport_retries for run in runs),
            "agent_writes": sum(run.agent_writes for run in runs),
            "action_proposals": sum(run.action_proposals for run in runs),
            "runbook_executions": sum(run.runbook_executions for run in runs),
        },
        "physical_service_names_in_provider_payloads": 0,
        "docker_calls": 0,
        "status": (
            "DTA_V226_REAL_PROVIDER_OLD_CAPTURE_GATE_PASS"
            if passed
            else "DTA_V226_REAL_PROVIDER_OLD_CAPTURE_GATE_FAILED"
        ),
    }


def _write_private_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    details = path.parent.stat()
    if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) & 0o077:
        raise ValueError("private Provider evidence directory is not owned 0700")
    data = (
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.write(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    report = build_provider_gate(
        root=root,
        provider_env_path=cast(Path, args.provider_env),
    )
    _write_private_report(cast(Path, args.output), report)
    summary = cast(dict[str, object], report["summary"])
    print(
        json.dumps(
            {
                "arm_run_count": report["arm_run_count"],
                "provider_calls": summary["provider_calls"],
                "protocol_repairs": summary["protocol_repairs"],
                "status": report["status"],
                "transport_retries": summary["transport_retries"],
                "valid_terminals": summary["valid_terminals"],
            },
            sort_keys=True,
        )
    )
    print(report["status"])
    return int(report["status"] != "DTA_V226_REAL_PROVIDER_OLD_CAPTURE_GATE_PASS")


if __name__ == "__main__":
    raise SystemExit(main())
