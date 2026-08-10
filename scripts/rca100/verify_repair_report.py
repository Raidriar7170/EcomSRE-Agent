"""Independently recompute and freeze the RCA100 evaluator-repair report."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Mapping

from ecomsre.evidence.hashes import canonical_json_bytes, sha256_file
from ecomsre_rca100.evaluator import RCA100CaseScore, evaluate_terminals
from ecomsre_rca100.lifecycle import create_once_json, load_strict_json
from ecomsre_rca100.public_projection import scan_public_artifacts
from scripts.rca100.build_report import _execution_summary
from scripts.rca100.build_repair_report import (
    HUMAN_REVIEW_CHECKLIST,
    disposition,
    execution_integrity,
    human_brief,
    markdown,
    public_report,
)
from scripts.rca100.evaluator_repair import (
    ORIGINAL_TERMINAL_LOCK_SHA256,
    REPAIR_PROTOCOL_ID,
    RepairEnvironment,
    advance_repair_state,
    case_score_vector_sha256,
    current_repair_state,
    load_repair_evaluation_inputs,
)


def _mapping(path: Path, *, label: str) -> Mapping[str, object]:
    value = load_strict_json(path)
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is invalid")
    return value


def _written_text(value: str) -> str:
    return value if value.endswith("\n") else f"{value}\n"


def _score_records(scores: tuple[RCA100CaseScore, ...]) -> dict[str, object]:
    return {
        "schema_version": "rca100.evaluator-repair-private-case-scores.v1",
        "records": [score.model_dump(mode="json") for score in scores],
    }


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> None:
    repository = _repository_root()
    repair = RepairEnvironment.from_environment(
        os.environ, repository_root=repository
    )
    if current_repair_state(repair.repair_control) != "REPAIR_SCORED":
        raise ValueError("repair report verification requires REPAIR_SCORED")
    inputs = load_repair_evaluation_inputs(repair)
    recomputed, scores = evaluate_terminals(
        schedule=inputs.original.schedule,
        terminals=inputs.original.terminals,
        truths=inputs.truths,
        catalogs=inputs.catalogs,
        alert_entity_types=inputs.alert_entity_types,
    )
    aggregate_path = repair.repair_control / "results" / "aggregate.json"
    case_scores_path = repair.repair_control / "results" / "case-scores.json"
    saved_aggregate = _mapping(
        aggregate_path,
        label="saved repair aggregate",
    )
    if canonical_json_bytes(saved_aggregate) != canonical_json_bytes(recomputed):
        raise ValueError("BLOCKED_CANONICAL_RECOMPUTE_MISMATCH")
    saved_scores = _mapping(
        case_scores_path,
        label="saved repair case scores",
    )
    expected_scores = _score_records(scores)
    if canonical_json_bytes(saved_scores) != canonical_json_bytes(expected_scores):
        raise ValueError("BLOCKED_CANONICAL_RECOMPUTE_MISMATCH")
    vector_sha = case_score_vector_sha256(scores)
    scoring_path = repair.repair_control / "locks" / "scoring-result-lock.json"
    scoring_lock = _mapping(scoring_path, label="repair scoring result lock")
    scoring_state = _mapping(
        repair.repair_control / "state" / "REPAIR_SCORED.json",
        label="repair scored state",
    )
    if (
        scoring_state.get("scoring_result_lock_sha256")
        != sha256_file(scoring_path)
        or scoring_lock.get("aggregate_file_sha256")
        != sha256_file(aggregate_path)
        or scoring_lock.get("case_scores_file_sha256")
        != sha256_file(case_scores_path)
        or scoring_lock.get("answer_key_lock_sha256")
        != sha256_file(repair.repair_control / "locks" / "answer-key-lock.json")
        or scoring_lock.get("repair_implementation_commit")
        != inputs.implementation_lock.get("repair_implementation_commit")
        or scoring_lock.get("original_terminal_lock_sha256")
        != ORIGINAL_TERMINAL_LOCK_SHA256
        or scoring_lock.get("case_score_vector_sha256") != vector_sha
        or scoring_lock.get("fixed_denominator") != 103
        or scoring_lock.get("terminals_scored") != 103
        or scoring_lock.get("provider_calls") != 0
        or scoring_lock.get("prediction_reruns") != 0
        or scoring_lock.get("case_replacements") != 0
    ):
        raise ValueError("BLOCKED_CANONICAL_RECOMPUTE_MISMATCH")
    audit = _mapping(
        repair.roots.control / "audit" / "no-label-schema-audit.json",
        label="original label-blind audit",
    )
    expected_report = public_report(
        aggregate=recomputed,
        execution=_execution_summary(inputs.original.terminals),
        audit=audit,
        terminal_lock=inputs.original.terminal_lock,
        answer_lock=inputs.answer_lock,
        implementation_lock=inputs.implementation_lock,
        scoring_lock=scoring_lock,
    )
    report_path = repository / "docs" / "results" / "rca100-metrics-arbitration-v1-final.json"
    final_markdown = report_path.with_suffix(".md")
    brief = report_path.with_name("rca100-metrics-arbitration-v1-human-brief.md")
    report = _mapping(report_path, label="public repair report")
    if canonical_json_bytes(report) != canonical_json_bytes(expected_report):
        raise ValueError("public repair JSON differs from canonical recomputation")
    if final_markdown.read_text(encoding="utf-8") != _written_text(
        markdown(expected_report)
    ):
        raise ValueError("public repair Markdown differs from canonical projection")
    if brief.read_text(encoding="utf-8") != _written_text(
        human_brief(expected_report)
    ):
        raise ValueError("repair Human Brief differs from canonical projection")
    review = repository / "docs" / "review-evidence" / "rca100-metrics-arbitration-v1"
    expected_disposition = disposition(expected_report)
    observed_disposition = _mapping(
        review / "evaluator-repair-disposition.json",
        label="repair disposition",
    )
    if canonical_json_bytes(observed_disposition) != canonical_json_bytes(
        expected_disposition
    ):
        raise ValueError("repair disposition differs from canonical projection")
    expected_integrity = execution_integrity(
        terminal_lock=inputs.original.terminal_lock,
        answer_lock=inputs.answer_lock,
        implementation_lock=inputs.implementation_lock,
        scoring_lock=scoring_lock,
    )
    observed_integrity = _mapping(
        review / "execution-integrity.json",
        label="repair execution integrity",
    )
    if canonical_json_bytes(observed_integrity) != canonical_json_bytes(
        expected_integrity
    ):
        raise ValueError("repair execution integrity differs from canonical locks")
    checklist_path = review / "human-review-checklist.md"
    if checklist_path.read_text(encoding="utf-8") != _written_text(
        HUMAN_REVIEW_CHECKLIST
    ):
        raise ValueError("repair review checklist differs from canonical projection")
    root = recomputed.get("root")
    subgroups = recomputed.get("descriptive_subgroups")
    if not isinstance(root, Mapping) or not isinstance(subgroups, Mapping):
        raise ValueError("recomputed repair aggregate is invalid")
    verification = {
        "schema_version": "rca100.evaluator-repair-final-verification.v1",
        "canonical_recomputation": "PASS",
        "independent_case_vector_match": True,
        "headline_counts_match": True,
        "bootstrap_match": True,
        "mcnemar_match": True,
        "subgroups_match": True,
        "fixed_denominator": 103,
        "case_score_vector_sha256": vector_sha,
        "classification": root["classification"],
        "bootstrap_replicates": root["bootstrap_replicates"],
        "bootstrap_seed": root["bootstrap_seed"],
        "mcnemar_exact_p_value": root["mcnemar_exact_p_value"],
        "ground_truth_drift": "NONE",
        "terminal_drift": "NONE",
        "public_leakage_scan": "PASS",
        "provider_calls": 0,
        "prediction_reruns": 0,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
    }
    verification_path = review / "final-report-verification.json"
    verification_sha = create_once_json(verification_path, verification)
    public_paths = (
        report_path,
        final_markdown,
        brief,
        review / "evaluator-repair-disposition.json",
        review / "execution-integrity.json",
        verification_path,
        checklist_path,
        repository
        / "docs"
        / "external-benchmarks"
        / "rca100-metrics-arbitration-v1-evaluator-repair-protocol.md",
    )
    findings = scan_public_artifacts(public_paths)
    if findings:
        raise ValueError(f"RCA100 repair public leakage scan failed: {findings}")
    report_hashes = {
        "final_json_sha256": sha256_file(report_path),
        "summary_sha256": sha256_file(final_markdown),
        "human_brief_sha256": sha256_file(brief),
        "disposition_sha256": sha256_file(
            review / "evaluator-repair-disposition.json"
        ),
        "execution_integrity_sha256": sha256_file(
            review / "execution-integrity.json"
        ),
        "final_verification_sha256": verification_sha,
        "human_review_checklist_sha256": sha256_file(checklist_path),
    }
    final_lock = {
        "schema_version": "rca100.evaluator-repair-final-report-lock.v1",
        "repair_protocol_id": REPAIR_PROTOCOL_ID,
        "original_blocked_disposition": "BLOCKED_PROTOCOL_DRIFT",
        "original_terminal_lock_sha256": ORIGINAL_TERMINAL_LOCK_SHA256,
        "implementation_lock_sha256": sha256_file(
            repair.repair_control
            / "locks"
            / "evaluator-repair-implementation-lock.json"
        ),
        "answer_key_lock_sha256": sha256_file(
            repair.repair_control / "locks" / "answer-key-lock.json"
        ),
        "scoring_result_lock_sha256": sha256_file(scoring_path),
        "case_score_vector_sha256": vector_sha,
        "aggregate_sha256": sha256_file(
            repair.repair_control / "results" / "aggregate.json"
        ),
        "paired_inference": dict(root),
        "public_report_sha256": report_hashes,
        "canonical_verification": "PASS",
        "public_leakage_scan": "PASS",
        "provider_calls": 0,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
    }
    final_lock_path = repair.repair_control / "locks" / "final-report-lock.json"
    final_lock_sha = create_once_json(final_lock_path, final_lock)
    advance_repair_state(
        repair.repair_control,
        "REPAIR_FINAL_REPORT_FROZEN",
        bindings={
            "final_report_lock_sha256": final_lock_sha,
            "classification": root["classification"],
            "evaluation_method_status": "POST_LOCK_EVALUATOR_REPAIR_DISCLOSED",
            "canonical_verification": "PASS",
            "public_leakage_scan": "PASS",
            "provider_calls": 0,
        },
    )
    print(
        json.dumps(
            {
                "verdict": (
                    "RCA100_EVALUATOR_REPAIR_FINAL_REPORT_FROZEN_READY_FOR_"
                    "PUBLICATION_REVIEW"
                ),
                "classification": root["classification"],
                "evaluation_method_status": (
                    "POST_LOCK_EVALUATOR_REPAIR_DISCLOSED"
                ),
                "case_score_vector_sha256": vector_sha,
                "final_report_lock_sha256": final_lock_sha,
                **report_hashes,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
