"""Offline verifier for the qualification review package; grants no authority."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def verify_result(result: dict[str, Any]) -> None:
    if (
        result["formal_authority"] is not False
        or result["one_shot_allowance_consumed"] is not False
    ):
        raise ValueError("qualification cannot grant formal authority")
    counters = result["counters"]
    for key in (
        "formal_faults",
        "remediation_writes",
        "provider_calls",
        "formal_campaign_executions",
    ):
        if type(counters.get(key)) is not int or counters[key] != 0:
            raise ValueError("formal counters not proven zero")
    if result["status"] == "PASS_NO_FAULT_ONLY":
        if (
            result["first_divergence"] != "NONE"
            or result["completed_stage_count"] != result["expected_stage_count"]
            or not result["expected_stage_count"]
            or not result["active_baseline"]
            or not result["healthy_traffic"]
            or not result["copyup"]
            or not result["isolation"]
            or (result["no_incident"] or {}).get("terminal") != "NO_INCIDENT"
            or result["cleanup"]["nonowned_unchanged"] is not True
        ):
            raise ValueError("incomplete runtime success claim")
    elif (
        result["status"] != "BLOCKED_PRE_EXECUTION"
        or result["first_divergence"] != "LATCHED"
    ):
        raise ValueError("unsupported or unlatched terminal")


def verify(repository: Path) -> dict[str, Any]:
    root = repository / "docs/results/product-v040-qualification"
    package = json.loads((root / "runtime-results.json").read_bytes())
    for attempt in package["attempts"]:
        verify_result(attempt["result"])
    if not package["attempts"] or package["disposition"] != "Draft / REVIEW_REQUIRED":
        raise ValueError("qualification package missing or wrong disposition")
    review = json.loads((root / "final-review.json").read_bytes())
    if review["must_fix"] != 0 or review["claim_accuracy"] != "PASS":
        raise ValueError("independent review incomplete")
    if (
        review["future_formal_campaign"] not in {"ALLOW", "WITHHOLD"}
        or review["formal_authority"] is not False
    ):
        raise ValueError("review cannot authorize campaign")
    if any(a["result"]["status"] != "PASS_NO_FAULT_ONLY" for a in package["attempts"]):
        if review["future_formal_campaign"] != "WITHHOLD":
            raise ValueError("preserved blocker requires WITHHOLD")
    if (
        hashlib.sha256((root / "runtime-results.json").read_bytes()).hexdigest()
        != review["runtime_results_sha256"]
    ):
        raise ValueError("review result binding differs")
    return {
        "status": "VERIFIED_QUALIFICATION_EVIDENCE_ONLY",
        "attempts": len(package["attempts"]),
        "latest_terminal": package["attempts"][-1]["result"]["status"],
        "future_formal_campaign": review["future_formal_campaign"],
        "formal_authority": False,
    }


if __name__ == "__main__":
    print(
        json.dumps(
            verify(Path(__file__).resolve().parents[2]), indent=2, sort_keys=True
        )
    )
