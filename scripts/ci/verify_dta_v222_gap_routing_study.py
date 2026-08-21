#!/usr/bin/env python3
"""Verify the frozen DTA v2.2.2 development and single-study evidence."""

from __future__ import annotations

import json
from pathlib import Path

from ecomsre.dta_v2.v22.evaluation_manifest_v222 import (
    load_and_verify_evaluation_manifest_v222,
    sha256_file_v222,
)
from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import (
    EvidenceUtilityAuditReportV222,
    evaluate_development_routing_gate_v222,
)
from ecomsre.dta_v2.v22.gap_study_campaign_v222 import StudyCombinationV222
from ecomsre.dta_v2.v22.gap_study_cli_v222 import GapStudyArtifactV222
from ecomsre.dta_v2.v22.gap_study_runner_v222 import GapStudyCaseRunV222
from ecomsre.dta_v2.v22.gap_study_scorer_v222 import score_gap_study_v222


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path("config/dta-v22-2/evaluation/manifest.json")
DEVELOPMENT = Path("docs/results/dta-v22-2-gap-routing-development.json")
STUDY = Path("docs/results/dta-v22-2-gap-routing-evaluation.json")
PARTIAL = Path("docs/results/dta-v22-2-gap-routing-evaluation.json.partial.jsonl")
STUDY_MARKDOWN = Path("docs/results/dta-v22-2-gap-routing-evaluation.md")
ERROR_ANALYSIS = Path("docs/results/dta-v22-2-gap-routing-error-analysis.md")
INTERVIEW_BRIEF = Path("docs/results/dta-v22-2-gap-routing-interview-brief.md")
PROGRESS = Path("docs/analysis/dta-v22-2-gap-routing-progress.json")


def _json(path: Path) -> dict[str, object]:
    value = json.loads((REPOSITORY_ROOT / path).read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def verify_gap_routing_study_v222() -> dict[str, object]:
    manifest_path = REPOSITORY_ROOT / MANIFEST
    manifest_raw = _json(MANIFEST)
    model = manifest_raw.get("model")
    if not isinstance(model, str):
        raise ValueError("evaluation manifest model is absent")
    manifest = load_and_verify_evaluation_manifest_v222(
        manifest_path=manifest_path,
        repository_root=REPOSITORY_ROOT,
        configured_model=model,
    )
    development = _json(DEVELOPMENT)
    development_gate = development.get("scores")
    if not isinstance(development_gate, dict):
        raise ValueError("development scores are absent")
    gate = development_gate.get("development_gate")
    if (
        not isinstance(gate, dict)
        or gate.get("gate_passed") is not True
        or development.get("phase") != "DEVELOPMENT"
        or development.get("development_iteration") != 2
        or development.get("execution_count") != 0
        or development.get("uncaught_exceptions") != 0
        or development.get("agent_writes") != 0
    ):
        raise ValueError("frozen development gate did not pass")

    artifact = GapStudyArtifactV222.model_validate_json(
        (REPOSITORY_ROOT / STUDY).read_bytes()
    )
    manifest_sha256 = sha256_file_v222(manifest_path)
    if (
        artifact.phase != "EVALUATION"
        or artifact.execution_count != 1
        or artifact.development_iteration is not None
        or artifact.manifest_sha256 != manifest_sha256
        or artifact.provider_model != manifest.model
        or artifact.campaign.cases_materialized != 16
        or len(artifact.campaign.runs) != 64
        or artifact.campaign.truth_load_count != 1
        or not artifact.campaign.truth_loaded_after_all_four_runs_per_case
        or not artifact.campaign.same_case_bytes_all_combinations
        or artifact.uncaught_exceptions != 0
        or artifact.agent_writes != 0
    ):
        raise ValueError("single final study invariants differ")

    grid = {
        (run.case_id, run.arm.value, run.router_mode.value)
        for run in artifact.campaign.runs
    }
    if len(grid) != 64:
        raise ValueError("final study factorial grid is incomplete")
    for case_id in (f"e{index:02d}" for index in range(1, 17)):
        schedule = tuple(
            item
            for item in artifact.campaign.schedule
            if item.case_id == case_id
        )
        if (
            tuple(item.execution_position for item in schedule) != (1, 2, 3, 4)
            or set(item.combination for item in schedule) != set(StudyCombinationV222)
        ):
            raise ValueError("final study schedule rotation is invalid")

    partial_runs = tuple(
        GapStudyCaseRunV222.model_validate_json(line)
        for line in (REPOSITORY_ROOT / PARTIAL).read_text(encoding="utf-8").splitlines()
    )
    if partial_runs != artifact.campaign.runs:
        raise ValueError("partial JSONL differs from final campaign runs")

    audit = EvidenceUtilityAuditReportV222.model_validate_json(
        (REPOSITORY_ROOT / manifest.utility_audit.path).read_bytes()
    )
    routing_gate = evaluate_development_routing_gate_v222(
        repository_root=REPOSITORY_ROOT,
        case_set_path=REPOSITORY_ROOT / manifest.case_set.path,
        truth_path=REPOSITORY_ROOT / manifest.truth_set.path,
    )
    rescored = score_gap_study_v222(
        runs=artifact.campaign.runs,
        truths=artifact.campaign.truths,
        utility_audit=audit,
        routing_gate=routing_gate,
        include_interpretation=True,
    )
    if rescored != artifact.scores or rescored.interpretation is None:
        raise ValueError("frozen final scores do not reproduce")
    terminal = rescored.interpretation.engineering_terminal
    if terminal != "DTA_V22_2_GAP_ROUTING_QUALITY_EFFECT_OBSERVED":
        raise ValueError("frozen engineering terminal differs")

    progress = _json(PROGRESS)
    final = progress.get("final_study")
    safety = progress.get("safety")
    reported = progress.get("reported_evidence")
    if (
        progress.get("status") not in {
            "MEASURED_RESULTS_FROZEN",
            "DTA_V22_2_GAP_ROUTING_STUDY_COMPLETE",
        }
        or not isinstance(final, dict)
        or final.get("execution_count") != 1
        or final.get("represented_runs") != 64
        or final.get("engineering_terminal") != terminal
        or not isinstance(safety, dict)
        or safety
        != {"agent_writes": 0, "docker_calls": 0, "runbook_executions": 0}
        or not isinstance(reported, dict)
    ):
        raise ValueError("progress report differs from final study")
    expected_hashes = {
        "study_markdown_sha256": sha256_file_v222(REPOSITORY_ROOT / STUDY_MARKDOWN),
        "error_analysis_sha256": sha256_file_v222(REPOSITORY_ROOT / ERROR_ANALYSIS),
        "interview_brief_sha256": sha256_file_v222(REPOSITORY_ROOT / INTERVIEW_BRIEF),
    }
    if reported != expected_hashes:
        raise ValueError("reported evidence hashes differ")
    for path in (STUDY_MARKDOWN, ERROR_ANALYSIS, INTERVIEW_BRIEF, Path("README.md")):
        if terminal not in (REPOSITORY_ROOT / path).read_text(encoding="utf-8"):
            raise ValueError(f"engineering terminal absent from {path}")
    return {
        "status": "DTA_V22_2_GAP_ROUTING_STUDY_VERIFIED",
        "execution_count": 1,
        "runs": 64,
        "terminal": terminal,
        "uncaught_exceptions": artifact.uncaught_exceptions,
        "agent_writes": artifact.agent_writes,
    }


if __name__ == "__main__":
    print(json.dumps(verify_gap_routing_study_v222(), sort_keys=True))
