"""Verify the v0.5 offline-only delivery; never infer live learning from fixtures."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "docs/results/product-v050"
HISTORICAL_HEAD = "cd086826b23b728c6527b8b6981aeffef489c4e7"
PREFIX = "ECOMSRE_PRODUCT_V050_"


def require(value: Any, reason: str) -> None:
    if not value:
        raise ValueError(reason)


def derive() -> dict[str, Any]:
    offline = json.loads((RESULT / "offline-checks.json").read_text())
    require(offline["evidence_mode"] == "FIXTURE_ONLY", "OFFLINE_MODE")
    require(offline["exit_code"] == 0 and len(offline["cases"]) >= 42, "OFFLINE_TESTS")
    require(all(c["status"] == "PASSED" for c in offline["cases"]), "OFFLINE_FAILURE")
    require(
        len({c["id"] for c in offline["cases"]}) == len(offline["cases"]),
        "DUPLICATE_TEST",
    )
    # This package is the preserved zero-call stage, not current source evidence.
    # Anchor every historical artifact to its published commit before reading it.
    for path in ["offline-checks.json", "preflight.json", "checks.json", "acceptance.json"]:
        relative = "docs/results/product-v050/" + path
        original = subprocess.check_output(["git", "show", HISTORICAL_HEAD + ":" + relative], cwd=ROOT)
        require((ROOT / relative).read_bytes() == original, "HISTORICAL_ARTIFACT_DRIFT:" + relative)
    for path, digest in offline["source_sha256"].items():
        require(not Path(path).is_absolute() and ".." not in Path(path).parts, "SOURCE_ESCAPE")
        original = subprocess.check_output(["git", "show", HISTORICAL_HEAD + ":" + path], cwd=ROOT)
        require(hashlib.sha256(original).hexdigest() == digest, "HISTORICAL_SOURCE_DRIFT:" + path)
    preflight = json.loads((RESULT / "preflight.json").read_text())
    require(
        preflight["provider_status"] == "NOT_CONFIGURED"
        and preflight["pricing_status"] == "NOT_CONFIGURED",
        "PREFLIGHT_MODE",
    )
    require(
        preflight["dispatches_in_this_command"] == preflight["docker_mutations"] == 0,
        "PREFLIGHT_EFFECT",
    )
    require(preflight["ledger_status"] == "NOT_CREATED", "UNEXPECTED_LEDGER")
    checks = json.loads((RESULT / "checks.json").read_text())
    review = checks["review"]
    engineering = (
        all(x["exit_code"] == 0 for x in checks["commands"])
        and {
            "focused",
            "full_repository",
            "ruff",
            "mypy",
            "historical_verifiers",
        }.issubset({x["name"] for x in checks["commands"]})
        and checks["historical_regression_status"] == "PASS"
        and review["must_fix_open"] == 0
        and review["status"] == "INDEPENDENT_REVIEW_COMPLETE"
    )
    plan = json.loads((ROOT / "config/product-v050/case-plan.json").read_text())
    require(plan["status"] == "PLANNED_NOT_FROZEN", "NO_EXECUTED_CASE_MANIFEST")
    result = {
        "schema_version": "ecomsre.product.acceptance.v050",
        "base_commit": "550a564d29954e6f3c2395790294e23800231ac4",
        "code_binding": "offline-checks.json:source_sha256",
        "configuration_binding": hashlib.sha256(
            (ROOT / "config/product-v050/case-plan.json").read_bytes()
        ).hexdigest(),
        "data_version": "FIXTURE_ONLY; no new live dataset or holdout was created",
        "engineering_status": "OFFLINE_COMPLETE" if engineering else "IN_PROGRESS",
        "provider_status": "NOT_CONFIGURED_NOT_ATTEMPTED",
        "telemetry_mode": "FIXTURE_ONLY",
        "investigation_acceptance": "OFFLINE_PROTOCOL_ONLY",
        "knowledge_proposal_acceptance": "NOT_ATTEMPTED_WITH_REAL_PROVIDER",
        "level_b_expression_acceptance": "OFFLINE_UNSEEN_VALUES_AND_NORMAL_MATCHER_VERIFIED",
        "holdout_result": "NOT_ATTEMPTED",
        "promotion_status": "NOT_ATTEMPTED",
        "new_event_reuse_status": "NOT_ATTEMPTED_WITH_LEARNED_KNOWLEDGE",
        "reused_event_llm_calls": None,
        "remediation_preview_status": "OFFLINE_PREVIEW_VERIFIED_NO_EXECUTION_AUTHORITY",
        "new_product_external_writes": 0,
        "historical_regression_status": checks["historical_regression_status"],
        "review_status": review["status"],
        "provider_request_count": 0,
        "usage_and_cost": {
            "requests": 0,
            "actual_usd": 0,
            "unknown_usage_requests": 0,
            "basis": "NO_REQUESTS",
        },
        "live_episode_count": 0,
        "owned_cleanup_status": "NOT_REQUIRED_NO_V050_RUNTIME_CREATED",
        "fixture_test_count": len(offline["cases"]),
        "limitations": [
            "Project Provider credentials and dated price schedule unavailable",
            "No real investigation trace, LLM candidate, live incidents or frozen holdout",
            "No validated/promoted LLM Level B knowledge or new-event reuse",
            "Case plan remains planned; no frozen live-data manifest exists",
            "Historical Provider-zero evidence remains unchanged",
            "Preview has no connection to execution authority",
        ],
        "terminal": PREFIX + "ENGINEERING_COMPLETE_WITH_LIMITATIONS"
        if engineering
        else "IN_PROGRESS",
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="Write the derived acceptance projection"
    )
    args = parser.parse_args()
    expected = derive()
    if args.write:
        raise ValueError("Historical acceptance is immutable; write continuation evidence separately")
    else:
        require(
            json.loads((RESULT / "acceptance.json").read_text()) == expected,
            "ACCEPTANCE_DRIFT",
        )
    print(
        json.dumps(
            {
                "verification": "PASS",
                "terminal": expected["terminal"],
                "scope": "HISTORICAL_OFFLINE_PACKAGE_NOT_CURRENT_OR_LIVE_ACCEPTANCE",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
