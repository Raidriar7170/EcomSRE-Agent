"""Canonical verification and create-once freeze of the public RCA100 report."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Mapping

from ecomsre.evidence.hashes import canonical_json_bytes, sha256_file
from ecomsre_rca100.evaluation_integrity import load_frozen_evaluation_inputs
from ecomsre_rca100.evaluator import evaluate_terminals
from ecomsre_rca100.lifecycle import (
    PrivateRoots,
    advance_state,
    current_state,
    load_strict_json,
)
from ecomsre_rca100.prompt import output_schema_sha256, prompt_sha256
from ecomsre_rca100.public_projection import scan_public_artifacts
from scripts.rca100.build_report import (
    HUMAN_REVIEW_CHECKLIST,
    _current_disposition,
    _execution_integrity_public,
    _execution_summary,
    _human_brief,
    _markdown,
    _public_report,
    _source_lock_public,
)


PROTOCOL_ID = "rca100-metrics-arbitration-v1"
SOURCE_COMMIT = "fd92cae17e6e14fa3ed0f3963c31838151fbdaa7"
INPUT_TREE_SHA256 = "8ab512ce9ad041ed1ffd89226c2df77d3bb741fed08990854f481794c98585bb"
FRESH_INPUT_TREE_SHA256 = "aca130e350330000e0d9bc575606e3a5378178b6d7e0c2afb5cf13910596fea9"
SCHEDULE_SHA256 = "00604fa3157edde3597a7ef6758637be06a099051181d921cc35a7f305c4459e"
MODEL = "gpt-5.4-mini-2026-03-17"
_PROVIDER_CREDENTIALS = (
    "ECOMSRE_LLM_API_KEY",
    "ECOMSRE_LLM_BASE_URL",
    "ECOMSRE_LLM_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
)


def _mapping(path: Path, *, label: str) -> Mapping[str, object]:
    value = load_strict_json(path)
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is invalid")
    return value


def _written_text(value: str) -> str:
    return value if value.endswith("\n") else f"{value}\n"


def main() -> None:
    repository = Path(__file__).resolve().parents[2]
    roots = PrivateRoots.from_environment(os.environ)
    roots.validate(repository_root=repository, create=False)
    if current_state(roots.control) != "UNBLINDED":
        raise ValueError("report verification requires UNBLINDED")
    if any(name in os.environ for name in _PROVIDER_CREDENTIALS):
        raise ValueError("Provider credentials remained during report verification")
    inputs = load_frozen_evaluation_inputs(
        roots=roots,
        repository_root=repository,
        protocol_id=PROTOCOL_ID,
        expected_source_commit=SOURCE_COMMIT,
        expected_input_tree_sha256=INPUT_TREE_SHA256,
        expected_fresh_input_tree_sha256=FRESH_INPUT_TREE_SHA256,
        expected_input_file_count=721,
        expected_schedule_sha256=SCHEDULE_SHA256,
        expected_model=MODEL,
        expected_prompt_sha256=prompt_sha256(),
        expected_output_schema_sha256=output_schema_sha256(),
    )
    recomputed, scores = evaluate_terminals(
        schedule=inputs.schedule,
        terminals=inputs.terminals,
        truths=inputs.truths,
        catalogs=inputs.catalogs,
        alert_entity_types=inputs.alert_entity_types,
    )
    saved_aggregate = _mapping(
        roots.evaluator / "results" / "aggregate.json",
        label="saved evaluator aggregate",
    )
    if canonical_json_bytes(saved_aggregate) != canonical_json_bytes(recomputed):
        raise ValueError("saved evaluator aggregate differs from canonical recomputation")
    saved_scores = _mapping(
        roots.evaluator / "results" / "case-scores.json",
        label="saved private case scores",
    )
    expected_scores = {
        "schema_version": "rca100.private-case-scores.v1",
        "records": [item.model_dump(mode="json") for item in scores],
    }
    if canonical_json_bytes(saved_scores) != canonical_json_bytes(expected_scores):
        raise ValueError("saved private case scores differ from canonical recomputation")
    audit = _mapping(
        roots.control / "audit" / "no-label-schema-audit.json",
        label="adapter audit",
    )
    expected_report = _public_report(
        recomputed,
        _execution_summary(inputs.terminals),
        audit,
        inputs.terminal_lock,
        inputs.answer_lock,
        inputs.protocol_lock,
    )
    report_path = (
        repository
        / "docs"
        / "results"
        / "rca100-metrics-arbitration-v1-final.json"
    )
    report = _mapping(report_path, label="public report")
    if canonical_json_bytes(report) != canonical_json_bytes(expected_report):
        raise ValueError("public report differs from canonical frozen recomputation")
    final_markdown = report_path.with_suffix(".md")
    human_brief = report_path.with_name(
        "rca100-metrics-arbitration-v1-human-brief.md"
    )
    if final_markdown.read_text(encoding="utf-8") != _written_text(
        _markdown(expected_report)
    ):
        raise ValueError("public Markdown differs from canonical frozen projection")
    if human_brief.read_text(encoding="utf-8") != _written_text(
        _human_brief(expected_report)
    ):
        raise ValueError("Human Brief differs from canonical frozen projection")
    review = (
        repository
        / "docs"
        / "review-evidence"
        / "rca100-metrics-arbitration-v1"
    )
    integrity = _mapping(
        review / "execution-integrity.json",
        label="public execution integrity",
    )
    expected_integrity = _execution_integrity_public(
        protocol_lock=inputs.protocol_lock,
        terminal_lock=inputs.terminal_lock,
        answer_lock=inputs.answer_lock,
    )
    if canonical_json_bytes(integrity) != canonical_json_bytes(expected_integrity):
        raise ValueError("public execution integrity differs from frozen locks")
    source_public = _mapping(
        review / "source-lock-public.json",
        label="public source lock",
    )
    if canonical_json_bytes(source_public) != canonical_json_bytes(
        _source_lock_public()
    ):
        raise ValueError("public source lock differs from frozen source")
    disposition = _mapping(
        review / "current-disposition.json",
        label="public disposition",
    )
    if canonical_json_bytes(disposition) != canonical_json_bytes(
        _current_disposition(expected_report)
    ):
        raise ValueError("public disposition differs from canonical result")
    checklist = (review / "human-review-checklist.md").read_text(encoding="utf-8")
    if checklist != _written_text(HUMAN_REVIEW_CHECKLIST):
        raise ValueError("human review checklist differs from frozen projection")
    paths = tuple(
        (repository / "docs" / "results").glob(
            "rca100-metrics-arbitration-v1*"
        )
    ) + tuple(review.glob("*"))
    findings = scan_public_artifacts(paths)
    if findings:
        raise ValueError(f"RCA100 public leakage scan failed: {findings}")
    final_report_sha256 = sha256_file(report_path)
    advance_state(
        roots.control,
        "FINAL_REPORT_FROZEN",
        bindings={
            "final_report_sha256": final_report_sha256,
            "classification": recomputed["root"]["classification"],  # type: ignore[index]
            "canonical_verification": "PASS",
            "public_leakage_scan": "PASS",
            "source_and_ground_truth_drift": "NONE",
            "frozen_at_utc": datetime.now(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
        },
    )
    print(
        json.dumps(
            {
                "canonical_verification": "PASS",
                "all_denominators_exact": True,
                "paired_statistics_reproduced": True,
                "source_and_ground_truth_drift": "NONE",
                "public_leakage_scan": "PASS",
                "final_report_sha256": final_report_sha256,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
