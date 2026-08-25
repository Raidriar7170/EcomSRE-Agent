#!/usr/bin/env python3
"""Fail closed if any v2.3 or blocked v2.3.1 evidence byte changes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config/dta-v232/historical-results.v1.json"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path.relative_to(ROOT)}")
    return payload


def verify() -> None:
    manifest = _load_json(MANIFEST)
    if manifest.get("merged_base_commit") != (
        "7fe2bff7186cca1cedd2513f7984709057fc19e5"
    ):
        raise ValueError("merged v2.3 base binding differs")
    if manifest.get("merged_v23") != {
        "engineering_terminal": "DTA_V23_OPEN_WORLD_DISCOVERY_MVP_COMPLETE",
        "measured_result_terminal": "DTA_V23_OPEN_WORLD_DISCOVERY_NOT_OBSERVED",
        "disposition": "VALID_NEGATIVE",
    }:
        raise ValueError("merged v2.3 disposition differs")
    if manifest.get("blocked_attempts") != [
        {
            "attempt": "A",
            "terminal": "BLOCKED_DTA_V231_EVALUATION_DATA",
            "disposition": "INVALID_FOR_EFFECT / BLOCKED_DATA",
            "measured_result": False,
            "may_continue": False,
            "may_rerun": False,
        },
        {
            "attempt": "B",
            "terminal": "BLOCKED_DTA_V231_REPOSITORY_ACCEPTANCE",
            "disposition": "INCOMPLETE / BLOCKED_RUNTIME",
            "measured_result": False,
            "may_continue": False,
            "may_rerun": False,
            "completed_case_pairs": 12,
            "completed_arm_runs": 24,
        },
    ]:
        raise ValueError("blocked v2.3.1 dispositions differ")

    roles: set[str] = set()
    for binding in manifest.get("bindings", []):
        path = ROOT / binding["path"]
        raw = path.read_bytes()
        if len(raw) != binding["size_bytes"]:
            raise ValueError(f"historical size differs: {binding['path']}")
        if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
            raise ValueError(f"historical SHA-256 differs: {binding['path']}")
        role = binding["role"]
        if role in roles:
            raise ValueError(f"duplicate historical role: {role}")
        roles.add(role)

    required_roles = {
        "MERGED_V23_VALID_NEGATIVE_RESULT",
        "MERGED_V23_COMPLETION_REVIEW",
        "ATTEMPT_A_STARTED_SENTINEL",
        "ATTEMPT_A_PARTIAL_JOURNAL",
        "ATTEMPT_A_MANIFEST",
        "ATTEMPT_A_BLOCKER_STATUS",
        "ATTEMPT_A_RESULT",
        "ATTEMPT_A_BLOCKER_REVIEW",
        "ATTEMPT_B_STARTED_SENTINEL",
        "ATTEMPT_B_PARTIAL_JOURNAL",
        "ATTEMPT_B_PREDECESSOR_FREEZE",
        "ATTEMPT_B_ADMISSION_PASS",
        "ATTEMPT_B_MANIFEST",
        "ATTEMPT_B_PRE_EXECUTION_REVIEW",
        "ATTEMPT_B_BLOCKER_REVIEW",
        "ATTEMPT_B_BLOCKER_RESULT",
        "ATTEMPT_B_BLOCKER_REPORT",
        "ATTEMPT_B_BLOCKER_AUDIT",
    }
    if roles != required_roles:
        raise ValueError("historical role set differs")

    attempt_a_started = _load_json(
        ROOT / ".local/dta-v231/fixed-evaluation.started.json"
    )
    attempt_b_started = _load_json(
        ROOT / ".local/dta-v231-successor/successor-evaluation.started.json"
    )
    if attempt_a_started.get("status") != "COMPLETE":
        raise ValueError("Attempt A consumed-study sentinel differs")
    if attempt_b_started.get("status") != "STARTED":
        raise ValueError("Attempt B STARTED sentinel differs")
    if attempt_b_started.get("planned_execution_count") != 1:
        raise ValueError("Attempt B execution budget differs")

    attempt_a_lines = (
        ROOT / ".local/dta-v231/fixed-evaluation.partial.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    attempt_b_lines = (
        ROOT / ".local/dta-v231-successor/successor-evaluation.partial.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    if len(attempt_a_lines) != 24:
        raise ValueError("Attempt A partial journal denominator differs")
    if len(attempt_b_lines) != 12:
        raise ValueError("Attempt B partial journal denominator differs")

    admission = _load_json(
        ROOT / "config/dta-v231-successor/evaluation/admission-matrix.json"
    )
    if admission.get("status") != "DTA_V231_SUCCESSOR_EVALUATION_DATA_PASS":
        raise ValueError("Attempt B admission terminal differs")

    blocked_result = _load_json(
        ROOT / "docs/results/dta-v231-successor-evaluation-blocked.json"
    )
    if blocked_result.get("terminal") != "BLOCKED_DTA_V231_REPOSITORY_ACCEPTANCE":
        raise ValueError("Attempt B blocker terminal differs")
    if blocked_result.get("completed_final_metrics") is not None:
        raise ValueError("Attempt B must not contain completed final metrics")
    if blocked_result.get("measured_result_terminal") is not None:
        raise ValueError("Attempt B must not contain a measured terminal")


if __name__ == "__main__":
    verify()
    print("DTA_V232_HISTORY_VERIFIED")
