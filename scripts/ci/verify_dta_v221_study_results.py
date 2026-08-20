"""Recompute and verify the single DTA v2.2.1 study without a Provider."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import cast

from ecomsre.dta_v2.v22.evidence_acquisition_campaign_v221 import (
    EvidenceAcquisitionStudyArtifactV221,
    FINAL_STUDY_COMBINATIONS_V221,
    load_practical_truth_set_v221,
)
from ecomsre.dta_v2.v22.evidence_acquisition_scorer_v221 import (
    compute_control_cost_metrics_v221,
    score_evidence_acquisition_runs_v221,
    summarize_study_interpretation_v221,
)
from ecomsre.dta_v2.v22.evidence_acquisition_v221 import StudyCombinationV221
from ecomsre.dta_v2.v22.practical_dataset import load_practical_case_set_v22
from ecomsre.dta_v2.v22.practical_runner import PracticalCaseRunV221
from scripts.ci.verify_dta_v221_study_manifest import (
    verify_dta_v221_study_manifest,
)


ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "docs/results/dta-v22-1-evidence-acquisition-study.json"
PARTIAL = RESULT.with_suffix(RESULT.suffix + ".partial.jsonl")
STUDY_REPORT = ROOT / "docs/results/dta-v22-1-evidence-acquisition-study.md"
ERROR_ANALYSIS = (
    ROOT / "docs/results/dta-v22-1-evidence-acquisition-error-analysis.md"
)
INTERVIEW_BRIEF = (
    ROOT / "docs/results/dta-v22-1-evidence-acquisition-interview-brief.md"
)
PROGRESS = ROOT / "docs/analysis/dta-v22-1-evidence-acquisition-progress.json"
CASES = ROOT / "config/dta-v22-sprint/evaluation/cases.json"
TRUTH = ROOT / "config/dta-v22-sprint/evaluation/truth.json"
EXPECTED_RESULT_SHA256 = (
    "047cab366a9f431a0eb097e79b0c48cdff1c143f63e88676601f9ba9e1f47a39"
)
EXPECTED_TERMINAL = "DTA_V22_1_NO_EVIDENCE_ACQUISITION_EFFECT_OBSERVED"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_dta_v221_study_results(
    *, repository_root: Path = ROOT
) -> dict[str, object]:
    if repository_root.resolve() != ROOT.resolve():
        raise ValueError("DTA v2.2.1 result verifier repository root differs")
    manifest_result = verify_dta_v221_study_manifest(
        repository_root=repository_root
    )
    if _sha256(RESULT) != EXPECTED_RESULT_SHA256:
        raise ValueError("DTA v2.2.1 study result bytes differ")
    artifact = EvidenceAcquisitionStudyArtifactV221.model_validate_json(
        RESULT.read_bytes()
    )
    campaign = artifact.campaign
    if (
        artifact.execution_count != 1
        or len(campaign.case_runs) != 48
        or campaign.combinations != FINAL_STUDY_COMBINATIONS_V221
        or campaign.agent_writes != 0
        or sum(item.uncaught_exceptions for item in campaign.case_runs) != 0
        or campaign.interpretation is None
        or campaign.interpretation.policy_terminal != EXPECTED_TERMINAL
    ):
        raise ValueError("DTA v2.2.1 final study closure differs")

    partial_runs = tuple(
        PracticalCaseRunV221.model_validate_json(line)
        for line in PARTIAL.read_text(encoding="utf-8").splitlines()
    )
    if partial_runs != campaign.case_runs:
        raise ValueError("DTA v2.2.1 partial execution order differs")
    by_case: defaultdict[str, list[PracticalCaseRunV221]] = defaultdict(list)
    for run in campaign.case_runs:
        by_case[run.case_id].append(run)
    if len(by_case) != 12 or any(
        len(runs) != 4
        or len({item.case_bytes_sha256 for item in runs}) != 1
        or len({item.normalized_case_sha256 for item in runs}) != 1
        or {
            next(
                combination
                for combination in FINAL_STUDY_COMBINATIONS_V221
                if item.arm is combination.arm
                and item.terminal_exploration_policy is combination.policy
            )
            for item in runs
        }
        != set(FINAL_STUDY_COMBINATIONS_V221)
        for runs in by_case.values()
    ):
        raise ValueError("DTA v2.2.1 same-case combination binding differs")
    for position in range(1, 5):
        counts = Counter(
            item.combination
            for item in campaign.schedule
            if item.case_position == position
        )
        if counts != Counter({item: 3 for item in FINAL_STUDY_COMBINATIONS_V221}):
            raise ValueError("DTA v2.2.1 schedule position balance differs")

    case_set = load_practical_case_set_v22(CASES)
    truth_set = load_practical_truth_set_v221(TRUTH)
    bootstrap_ids = tuple(
        item.case_id for item in case_set.cases if item.bootstrap_insufficient_expected
    )
    score_by_combination = {
        item.combination: item.score for item in campaign.combination_scores
    }
    recomputed_scores = {}
    runs_by_combination = {}
    for combination in FINAL_STUDY_COMBINATIONS_V221:
        runs = tuple(
            sorted(
                (
                    item
                    for item in campaign.case_runs
                    if item.arm is combination.arm
                    and item.terminal_exploration_policy is combination.policy
                ),
                key=lambda item: item.case_id,
            )
        )
        runs_by_combination[combination] = runs
        recomputed = score_evidence_acquisition_runs_v221(
            combination=combination,
            runs=runs,
            truths=truth_set.truths,
            bootstrap_insufficient_case_ids=bootstrap_ids,
        )
        if recomputed != score_by_combination[combination]:
            raise ValueError(f"DTA v2.2.1 score drift: {combination.value}")
        recomputed_scores[combination] = recomputed
    control_costs = (
        compute_control_cost_metrics_v221(
            arm=StudyCombinationV221.FLAT_LEGACY.arm,
            legacy_runs=runs_by_combination[StudyCombinationV221.FLAT_LEGACY],
            gate_runs=runs_by_combination[StudyCombinationV221.FLAT_GATE],
            truths=truth_set.truths,
        ),
        compute_control_cost_metrics_v221(
            arm=StudyCombinationV221.PLANNER_LEGACY.arm,
            legacy_runs=runs_by_combination[StudyCombinationV221.PLANNER_LEGACY],
            gate_runs=runs_by_combination[StudyCombinationV221.PLANNER_GATE],
            truths=truth_set.truths,
        ),
    )
    if control_costs != campaign.control_costs:
        raise ValueError("DTA v2.2.1 control-cost score drift")
    interpretation = summarize_study_interpretation_v221(
        scores=tuple(recomputed_scores[item] for item in FINAL_STUDY_COMBINATIONS_V221),
        control_costs=control_costs,
    )
    if interpretation != campaign.interpretation:
        raise ValueError("DTA v2.2.1 interpretation drift")

    progress = cast(
        dict[str, object], json.loads(PROGRESS.read_text(encoding="utf-8"))
    )
    evidence = cast(dict[str, str], progress["committed_evidence"])
    expected_report_hashes = {
        "partial_jsonl_sha256": _sha256(PARTIAL),
        "study_report_sha256": _sha256(STUDY_REPORT),
        "error_analysis_sha256": _sha256(ERROR_ANALYSIS),
        "interview_brief_sha256": _sha256(INTERVIEW_BRIEF),
    }
    if evidence != expected_report_hashes:
        raise ValueError("DTA v2.2.1 committed report binding differs")
    required_text = {
        STUDY_REPORT: (
            EXPECTED_TERMINAL,
            "48/48",
            "Flat Gate added 7 Provider calls",
        ),
        ERROR_ANALYSIS: (
            EXPECTED_TERMINAL,
            "READ followed by ABSTAIN | 15",
            "Protocol failure | 18",
        ),
        INTERVIEW_BRIEF: (
            EXPECTED_TERMINAL,
            "Diagnosis after read | 0 | 0 | 0 | 0",
            "Agent writes | 0 | 0 | 0 | 0",
        ),
    }
    for path, markers in required_text.items():
        text = path.read_text(encoding="utf-8")
        if any(marker not in text for marker in markers):
            raise ValueError(f"DTA v2.2.1 report marker differs: {path.name}")
    return {
        "execution_count": artifact.execution_count,
        "arm_policy_runs": len(campaign.case_runs),
        "policy_terminal": interpretation.policy_terminal,
        "agent_writes": campaign.agent_writes,
        "historical_files_verified": manifest_result[
            "historical_files_verified"
        ],
    }


def main() -> int:
    result = verify_dta_v221_study_results()
    print(json.dumps({"status": "DTA_V22_1_STUDY_RESULTS_VERIFIED", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
