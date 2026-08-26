#!/usr/bin/env python3
"""Fail closed if any historical v2.3 through v2.3.2 result byte changes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config/dta-v233/historical-results.v1.json"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path.relative_to(ROOT)}")
    return payload


def verify() -> None:
    manifest = _load_json(MANIFEST)
    if manifest.get("merged_base_commit") != (
        "447e7a8ed4c8b9d592c16d181f5709bdfdc3d4cb"
    ):
        raise ValueError("v2.3.3 merged base binding differs")
    if manifest.get("historical_terminals") != {
        "v23": "DTA_V23_OPEN_WORLD_DISCOVERY_NOT_OBSERVED",
        "v231_evaluation": "BLOCKED_DTA_V231_EVALUATION_DATA",
        "v231_repository": "BLOCKED_DTA_V231_REPOSITORY_ACCEPTANCE",
        "v232_measured": "DTA_V232_CONFLICT_AWARE_DISCOVERY_MIXED_RESULT",
        "v232_engineering": "DTA_V232_CONFLICT_AWARE_SUCCESSOR_COMPLETE",
    }:
        raise ValueError("v2.3.3 historical terminal set differs")

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
        "V232_TRANSITIVE_HISTORY_LEDGER",
        "V232_TRANSITIVE_HISTORY_VERIFIER",
        "V232_FIXED_RESULT",
        "V232_FIXED_REPORT",
        "V232_ERROR_ANALYSIS",
        "V232_FINAL_REVIEW",
    }:
        raise ValueError("v2.3.3 historical role set differs")

    result = _load_json(
        ROOT / "docs/results/dta-v232-conflict-aware-evaluation.json"
    )
    if result.get("execution_count") != 1:
        raise ValueError("v2.3.2 historical execution count differs")
    if result.get("measured_result_terminal") != (
        "DTA_V232_CONFLICT_AWARE_DISCOVERY_MIXED_RESULT"
    ):
        raise ValueError("v2.3.2 historical measured terminal differs")
    if result.get("agent_writes") != 0 or result.get("runbook_executions") != 0:
        raise ValueError("v2.3.2 historical authority boundary differs")


if __name__ == "__main__":
    verify()
    print("DTA_V233_HISTORY_VERIFIED")
