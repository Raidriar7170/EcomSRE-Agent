#!/usr/bin/env python3
"""Fail closed if the DTA v2.3.4 predecessor or its disposition drifts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import runpy
import subprocess
from typing import Any, Callable, cast


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config/dta-v2341/historical-results.v1.json"
PREDECESSOR_HEAD = "edb313655c4be64295012c383cfa19ed48ccb894"
PUBLIC_MAIN_BASE = "da423b9104ac532f0bf323f314d37b527671c679"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.relative_to(ROOT)}")
    return value


def _git(*args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def verify() -> None:
    manifest = _load_json(MANIFEST)
    if manifest.get("public_main_base") != PUBLIC_MAIN_BASE:
        raise ValueError("v2.3.4.1 public main binding differs")
    if manifest.get("frozen_predecessor_head") != PREDECESSOR_HEAD:
        raise ValueError("v2.3.4.1 predecessor-head binding differs")
    if manifest.get("frozen_predecessor_status") != "BLOCKED_DTA_V234_PROVIDER":
        raise ValueError("v2.3.4 predecessor terminal differs")
    if _git("merge-base", PREDECESSOR_HEAD, "HEAD") != PREDECESSOR_HEAD:
        raise ValueError("successor does not descend from the frozen predecessor head")
    if _git("merge-base", PUBLIC_MAIN_BASE, PREDECESSOR_HEAD) != PUBLIC_MAIN_BASE:
        raise ValueError("frozen predecessor does not descend from the bound main base")

    expected_classification = {
        "engineering_implementation": "PRESENT",
        "provider_smoke": "FAILED_CONSUMED",
        "fixed_evaluation": "NOT_STARTED",
        "measured_result": "ABSENT",
    }
    if manifest.get("predecessor_classification") != expected_classification:
        raise ValueError("v2.3.4 predecessor classification differs")
    counters = manifest.get("predecessor_counters")
    if counters != {
        "provider_smoke_execution_count": 1,
        "required_role_count": 7,
        "campaign_requests": 22,
        "campaign_responses": 22,
        "protocol_repair_requests": 12,
        "real_fixes": 2,
        "maximum_real_fixes": 2,
        "fixed_evaluation_execution_count": 0,
        "docker_calls": 0,
        "new_live_faults": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "remediation_registrations": 0,
        "action_authority_violations": 0,
    }:
        raise ValueError("v2.3.4 predecessor counters differ")

    roles: set[str] = set()
    for binding in manifest.get("bindings", []):
        path = ROOT / binding["path"]
        raw = path.read_bytes()
        if len(raw) != binding["size_bytes"]:
            raise ValueError(f"predecessor size differs: {binding['path']}")
        if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
            raise ValueError(f"predecessor SHA-256 differs: {binding['path']}")
        role = str(binding["role"])
        if role in roles:
            raise ValueError(f"duplicate predecessor binding role: {role}")
        roles.add(role)
    if roles != {
        "FROZEN_PROVIDER_BLOCKER",
        "FROZEN_PROVIDER_REPAIR_1",
        "FROZEN_PROVIDER_REPAIR_2",
        "FROZEN_ERROR_ANALYSIS",
        "FROZEN_INTERVIEW_BRIEF",
        "FROZEN_PRE_EXECUTION_REVIEW",
        "FROZEN_ACTIVE_MANIFEST",
    }:
        raise ValueError("v2.3.4 predecessor binding roles differ")

    blocker = _load_json(ROOT / "docs/analysis/dta-v234-provider-blocker.json")
    if blocker.get("status") != "BLOCKED_DTA_V234_PROVIDER":
        raise ValueError("v2.3.4 blocker artifact terminal differs")
    if blocker.get("provider_smoke", {}).get("execution_count") != 1:
        raise ValueError("v2.3.4 Provider smoke execution count differs")
    smoke = blocker.get("provider_smoke", {})
    if smoke.get("pass_artifact_present") is not False:
        raise ValueError("v2.3.4 unexpectedly has a Provider-smoke PASS artifact")
    if smoke.get("real_fix_count") != 2 or smoke.get("maximum_real_fixes") != 2:
        raise ValueError("v2.3.4 Provider-smoke repair count differs")
    if blocker.get("fixed_evaluation_execution_count") != 0:
        raise ValueError("v2.3.4 fixed evaluation count differs")
    if blocker.get("measured_result_terminal") is not None:
        raise ValueError("v2.3.4 unexpectedly has a measured result")
    if blocker.get("engineering_terminal") is not None:
        raise ValueError("v2.3.4 unexpectedly has an engineering terminal")
    if blocker.get("pr_ready_transition_authorized") is not False:
        raise ValueError("v2.3.4 predecessor Ready authorization differs")
    if blocker.get("squash_merge_authorized") is not False:
        raise ValueError("v2.3.4 predecessor merge authorization differs")
    repair_codes = tuple(
        item.get("fix_code") for item in blocker.get("repair_records", [])
    )
    if repair_codes != (
        "V234_PROTOCOL_FEEDBACK_AND_MODE_BINDING",
        "V234_SMOKE_RESUME_ISOLATION",
    ):
        raise ValueError("v2.3.4 predecessor repair codes differ")
    for forbidden in (
        ROOT / "docs/analysis/dta-v234-provider-smoke.json",
        ROOT / "docs/results/dta-v234-registration-assistance-evaluation.json",
        ROOT / "docs/results/dta-v234-registration-assistance-evaluation.md",
    ):
        if forbidden.exists():
            raise ValueError(f"unexpected v2.3.4 completion artifact: {forbidden.name}")

    verifier_namespace = runpy.run_path(
        str(ROOT / "scripts/ci/verify_dta_v234_history.py")
    )
    verify_v234 = cast(Callable[[], None], verifier_namespace["verify"])
    verify_v234()


if __name__ == "__main__":
    verify()
    print("DTA_V2341_PREDECESSOR_HISTORY_VERIFIED")
