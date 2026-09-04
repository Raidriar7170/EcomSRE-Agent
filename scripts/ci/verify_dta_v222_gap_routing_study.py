#!/usr/bin/env python3
"""Verify the frozen DTA v2.2.2 development and single-study evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from ecomsre.dta_v2.v22.evaluation_manifest_v222 import (
    EvaluationManifestV222,
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
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_dataset import (
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path("config/dta-v22-2/evaluation/manifest.json")
DEVELOPMENT = Path("docs/results/dta-v22-2-gap-routing-development.json")
STUDY = Path("docs/results/dta-v22-2-gap-routing-evaluation.json")
PARTIAL = Path("docs/results/dta-v22-2-gap-routing-evaluation.json.partial.jsonl")
STUDY_MARKDOWN = Path("docs/results/dta-v22-2-gap-routing-evaluation.md")
ERROR_ANALYSIS = Path("docs/results/dta-v22-2-gap-routing-error-analysis.md")
INTERVIEW_BRIEF = Path("docs/results/dta-v22-2-gap-routing-interview-brief.md")
PROGRESS = Path("docs/analysis/dta-v22-2-gap-routing-progress.json")
V222_SQUASH_MERGE = "bb85500fd4aa1777e2ac186f04b4b887c3a1023b"


def _json(path: Path) -> dict[str, object]:
    value = json.loads((REPOSITORY_ROOT / path).read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _git_bytes(commit: str, relative: str) -> bytes:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"cannot read frozen implementation object: {relative}")
    return completed.stdout


def _load_frozen_manifest() -> EvaluationManifestV222:
    manifest = EvaluationManifestV222.model_validate_json(
        (REPOSITORY_ROOT / MANIFEST).read_bytes()
    )
    # PR #63 was squash-merged, so its feature implementation commit is not an
    # ancestor of mainline. Verify both sides against the common base, then
    # require the published squash merge itself to remain in HEAD's ancestry.
    for older, newer, label in (
        (manifest.base_commit, manifest.implementation_commit, "base to implementation"),
        (manifest.base_commit, V222_SQUASH_MERGE, "base to squash merge"),
        (V222_SQUASH_MERGE, "HEAD", "squash merge to HEAD"),
    ):
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", older, newer],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
        )
        if ancestry.returncode != 0:
            raise ValueError(f"frozen commit ancestry differs: {label}")

    implementation_bindings = (
        manifest.policy_source,
        manifest.router_source,
        manifest.runner_source,
        manifest.scorer_source,
        manifest.selection_source,
    )
    for binding in implementation_bindings:
        implementation_sha256 = hashlib.sha256(
            _git_bytes(manifest.implementation_commit, binding.path)
        ).hexdigest()
        if implementation_sha256 != binding.sha256:
            raise ValueError(f"frozen implementation binding differs: {binding.path}")

    current_bindings = (
        manifest.prompt,
        manifest.case_set,
        manifest.truth_set,
        manifest.utility_audit,
        manifest.development_result,
        manifest.historical_results_manifest,
        *manifest.agent_visible_sources,
    )
    for binding in current_bindings:
        path = Path(binding.path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("evaluation manifest path escapes the repository")
        if sha256_file_v222(REPOSITORY_ROOT / path) != binding.sha256:
            raise ValueError(f"frozen evaluation binding differs: {binding.path}")
    return manifest


def verify_gap_routing_study_v222() -> dict[str, object]:
    manifest_path = REPOSITORY_ROOT / MANIFEST
    manifest_raw = _json(MANIFEST)
    model = manifest_raw.get("model")
    if not isinstance(model, str):
        raise ValueError("evaluation manifest model is absent")
    manifest = _load_frozen_manifest()
    if manifest.model != model:
        raise ValueError("configured model differs from frozen evaluation manifest")
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
        or artifact.prompt_sha256 != manifest.prompt.sha256
        or artifact.case_set_sha256 != manifest.case_set.sha256
        or artifact.truth_set_sha256 != manifest.truth_set.sha256
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
    case_set = load_practical_case_set_v22(REPOSITORY_ROOT / manifest.case_set.path)
    case_hashes = {
        spec.case_id: semantic_sha256_v22(
            materialize_practical_case_v22(
                spec=spec,
                repository_root=REPOSITORY_ROOT,
            ).model_dump(mode="json")
        )
        for spec in case_set.cases
    }
    if any(
        run.case_bytes_sha256 != case_hashes.get(run.case_id)
        for run in artifact.campaign.runs
    ):
        raise ValueError("measured run case bytes differ from frozen materialized cases")
    frozen_truths = load_practical_truth_set_v22(
        REPOSITORY_ROOT / manifest.truth_set.path
    ).truths
    if artifact.campaign.truths != frozen_truths:
        raise ValueError("measured truths differ from the frozen truth set")
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
    terminal = rescored.interpretation.measured_result_terminal
    if terminal != "DTA_V22_2_GAP_ROUTING_QUALITY_EFFECT_OBSERVED":
        raise ValueError("frozen measured result terminal differs")

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
        or final.get("measured_result_terminal") != terminal
        or final.get("artifact_sha256")
        != sha256_file_v222(REPOSITORY_ROOT / STUDY)
        or final.get("partial_jsonl_sha256")
        != sha256_file_v222(REPOSITORY_ROOT / PARTIAL)
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
    # The public closeout moves historical terminals out of the root README.
    for path in (
        STUDY_MARKDOWN,
        ERROR_ANALYSIS,
        INTERVIEW_BRIEF,
        Path("docs/history/PROJECT_EVOLUTION.md"),
    ):
        if terminal not in (REPOSITORY_ROOT / path).read_text(encoding="utf-8"):
            raise ValueError(f"measured result terminal absent from {path}")
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
