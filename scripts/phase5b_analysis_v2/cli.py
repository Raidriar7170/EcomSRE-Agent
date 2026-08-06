"""Review-gated command line for the Phase 5B v2 analysis-only repair."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from scripts.phase5b_analysis_v2.runner import (
    preflight_v2_analysis,
    reject_forbidden_environment,
    run_v2_analysis,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _ground_truth_root(environment: dict[str, str]) -> Path:
    selected = environment.get("PHASE5B_GROUND_TRUTH_ROOT")
    if not selected:
        raise ValueError("ground-truth root is required")
    return Path(selected)


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1-source-root", type=Path, required=True)
    parser.add_argument("--v1-execution-root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight")
    analyze = commands.add_parser("analyze")
    analyze.add_argument("--output-root", type=Path, required=True)
    arguments = parser.parse_args(argv)
    environment = dict(os.environ)
    reject_forbidden_environment(environment)
    truth_root = _ground_truth_root(environment)
    if arguments.command == "preflight":
        inputs = preflight_v2_analysis(
            project_root=PROJECT_ROOT,
            v1_source_root=arguments.v1_source_root,
            v1_execution_root=arguments.v1_execution_root,
            hidden_ground_truth_root=truth_root,
        )
        _print(
            {
                "status": "PHASE5B_V2_ANALYSIS_PREFLIGHT_VERIFIED",
                "main_runs": inputs.protocol.main_run_count,
                "ablation_runs": inputs.protocol.ablation_gap_count,
                "ground_truth_records_admitted": 30,
                "provider_calls": 0,
                "scoring_bundle_created": False,
                "final_report_created": False,
                "analysis_executed": False,
            }
        )
        return 0
    report, disposition = run_v2_analysis(
        project_root=PROJECT_ROOT,
        v1_source_root=arguments.v1_source_root,
        v1_execution_root=arguments.v1_execution_root,
        hidden_ground_truth_root=truth_root,
        output_root=arguments.output_root,
        environment=environment,
    )
    _print(
        {
            "status": disposition.status,
            "claim_classification": report.claim_classification,
            "main_runs": report.main_run_count,
            "ablation_runs": report.ablation_run_count,
            "provider_calls": 0,
            "scoring_bundle_created": True,
            "final_report_created": True,
            "analysis_executed": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
