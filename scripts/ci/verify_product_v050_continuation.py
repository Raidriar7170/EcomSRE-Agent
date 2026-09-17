"""Verify the appended continuation; failed real calls never prove learning."""

import hashlib
import json
from pathlib import Path
from scripts.product_v050.run_offline_checks import bound_sources
from scripts.ci.verify_product_v050 import derive, require

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "docs/results/product-v050/continuation-01"


def verify():
    derive()  # Preserved zero-call history anchored to its exact published commit.
    offline = json.loads((RESULT / "offline-checks.json").read_text())
    require(
        offline["exit_code"] == 0
        and all(c["status"] == "PASSED" for c in offline["cases"]),
        "OFFLINE_FAILURE",
    )
    require(
        set(offline["source_sha256"])
        == {str(p.relative_to(ROOT)) for p in bound_sources(ROOT)},
        "SOURCE_SCOPE",
    )
    for path, digest in offline["source_sha256"].items():
        require(
            hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest,
            "SOURCE_DRIFT:" + path,
        )
    result = json.loads((RESULT / "acceptance.json").read_text())
    calls = json.loads((RESULT / "provider-attempts.json").read_text())
    require(len(calls) == result["provider_request_count"] <= 200, "REQUEST_COUNT")
    require(
        sum(c["committed_upper_microusd"] for c in calls)
        == result["usage_and_cost"]["committed_upper_microusd"]
        <= 20_000_000,
        "COST_BOUND",
    )
    require(result["usage_and_cost"]["actual_usd"] is None, "UNKNOWN_IS_NOT_ZERO")
    require(all(c["usage_status"] == "UNKNOWN" for c in calls), "UNEXPECTED_USAGE")
    require(
        result["live_episode_count"] == 0
        and result["new_product_external_writes"] == 0,
        "LIVE_OR_WRITE_CLAIM",
    )
    require(
        result["knowledge_proposal_acceptance"] == "NOT_ATTEMPTED_SMOKE_FAILED",
        "KNOWLEDGE_CLAIM",
    )
    require(
        result["holdout_result"] == result["promotion_status"] == "NOT_ATTEMPTED",
        "LEARNING_CLAIM",
    )
    require(result["reused_event_llm_calls"] is None, "REUSE_CLAIM")
    require(
        result["terminal"]
        in {
            "IN_PROGRESS",
            "ECOMSRE_PRODUCT_V050_ENGINEERING_COMPLETE_WITH_LIMITATIONS",
        },
        "UNSUPPORTED_TERMINAL",
    )
    if result["terminal"] != "IN_PROGRESS":
        checks = json.loads((RESULT / "checks.json").read_text())
        require(
            checks["must_fix_open"] == 0
            and checks["full_repository"]["exit_code"] == 0,
            "CLOSURE_INCOMPLETE",
        )
    return {
        "verification": "PASS",
        "scope": "CONTINUATION_OFFLINE_AND_RETAINED_FAILED_PROVIDER_ATTEMPTS",
        "terminal": result["terminal"],
    }


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
