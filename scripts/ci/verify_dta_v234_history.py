#!/usr/bin/env python3
"""Fail closed if any historical v2.3 through v2.3.3 result byte changes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import runpy
from typing import Any, Callable, cast


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config/dta-v234/historical-results.v1.json"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path.relative_to(ROOT)}")
    return payload


def verify() -> None:
    manifest = _load_json(MANIFEST)
    if manifest.get("merged_base_commit") != (
        "da423b9104ac532f0bf323f314d37b527671c679"
    ):
        raise ValueError("v2.3.4 merged base binding differs")
    if manifest.get("historical_terminals") != {
        "v23": "DTA_V23_OPEN_WORLD_DISCOVERY_NOT_OBSERVED",
        "v231_evaluation": "BLOCKED_DTA_V231_EVALUATION_DATA",
        "v231_repository": "BLOCKED_DTA_V231_REPOSITORY_ACCEPTANCE",
        "v232": "DTA_V232_CONFLICT_AWARE_DISCOVERY_MIXED_RESULT",
        "v233": "DTA_V233_DOMAIN_AND_GUARD_MIXED_RESULT",
    }:
        raise ValueError("v2.3.4 historical terminal set differs")

    roles: set[str] = set()
    for binding in manifest.get("bindings", []):
        path = ROOT / binding["path"]
        raw = path.read_bytes()
        if len(raw) != binding["size_bytes"]:
            raise ValueError(f"historical size differs: {binding['path']}")
        if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
            raise ValueError(f"historical SHA-256 differs: {binding['path']}")
        role = str(binding["role"])
        if role in roles:
            raise ValueError(f"duplicate historical role: {role}")
        roles.add(role)
    if roles != {
        "V233_TRANSITIVE_HISTORY_LEDGER",
        "V233_TRANSITIVE_HISTORY_VERIFIER",
        "V233_FIXED_RESULT",
        "V233_FIXED_REPORT",
        "V233_ERROR_ANALYSIS",
        "V233_FINAL_REVIEW",
    }:
        raise ValueError("v2.3.4 historical role set differs")

    verifier_v232_namespace = runpy.run_path(
        str(ROOT / "scripts/ci/verify_dta_v232_history.py")
    )
    verify_v232 = cast(Callable[[], None], verifier_v232_namespace["verify"])
    verify_v232()

    verifier_v233_namespace = runpy.run_path(
        str(ROOT / "scripts/ci/verify_dta_v233_history.py")
    )
    verify_v233 = cast(Callable[[], None], verifier_v233_namespace["verify"])
    verify_v233()

    result = _load_json(
        ROOT / "docs/results/dta-v233-domain-guard-evaluation.json"
    )
    if result.get("execution_count") != 1:
        raise ValueError("v2.3.3 historical execution count differs")
    if result.get("measured_result_terminal") != (
        "DTA_V233_DOMAIN_AND_GUARD_MIXED_RESULT"
    ):
        raise ValueError("v2.3.3 historical measured terminal differs")
    if any(
        result.get(field) != 0
        for field in (
            "agent_writes",
            "runbook_executions",
            "docker_calls",
            "new_live_faults",
            "action_authority_violations",
        )
    ):
        raise ValueError("v2.3.3 historical authority boundary differs")

    final_review = (
        ROOT / "docs/external-reviews/dta-v233-final-review.md"
    ).read_text(encoding="utf-8")
    if "Must Fix: 0" not in final_review or "Claim Accuracy: PASS" not in final_review:
        raise ValueError("v2.3.3 historical final review differs")


if __name__ == "__main__":
    verify()
    print("DTA_V234_HISTORY_VERIFIED")
