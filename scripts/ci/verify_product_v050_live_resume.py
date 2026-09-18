"""Verify current fixture checks and the retained prestart safety terminal."""

import hashlib
import json
from pathlib import Path
from scripts.ci.verify_product_v050 import require
from scripts.ci.verify_product_v050_provider_unblock import verify as verify_history
from scripts.product_v050.run_offline_checks import bound_sources

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "docs/results/product-v050/live-resume"


def verify_claims(r):
    require(
        r["terminal"] == "ECOMSRE_PRODUCT_V050_BLOCKED_SAFETY", "UNSUPPORTED_TERMINAL"
    )
    require(
        r["containers_created"] == 22
        and r["containers_started"]
        == r["fault_injections"]
        == r["live_episode_count"]
        == r["new_provider_requests"]
        == r["new_product_external_writes"]
        == 0,
        "UNOBSERVED_LIVE_CLAIM",
    )
    cleanup = r["owned_cleanup"]
    require(
        cleanup["remaining"] == {"container": 0, "network": 0, "volume": 0},
        "OWNED_RESOURCE_REMAINS",
    )
    counts = {
        kind: sum(x["kind"] == kind and x["removed"] for x in cleanup["receipts"])
        for kind in cleanup["remaining"]
    }
    require(counts == {"container": 22, "network": 1, "volume": 5}, "CLEANUP_RECEIPTS")
    require(
        not cleanup["clean"] and not cleanup["non_owned_unchanged"], "FALSE_CLEAN_CLAIM"
    )
    require(
        r["old_volume_count"] == 3 and r["old_volumes_unchanged"], "OLD_VOLUME_DRIFT"
    )
    networks = r["network_observations"]
    require(
        [x["stage"] for x in networks] == ["before", "prestart", "after_cleanup"],
        "NETWORK_STAGE_ORDER",
    )
    require(
        len({x["identity_sha256"] for x in networks}) == 3
        and len({x["configuration_sha256"] for x in networks}) == 1,
        "NETWORK_DRIFT_EVIDENCE",
    )
    require(
        r["daemon_identity_unchanged"] and r["network_recreation_cause"] == "UNKNOWN",
        "UNSUPPORTED_ROOT_CAUSE",
    )
    require(
        r["promotion"] == r["holdout"] == r["new_event_reuse"] == "NOT_ATTEMPTED"
        and r["reused_event_llm_calls"] is None,
        "UNOBSERVED_LEARNING",
    )
    require(
        r["provider"]["provider_request_count"] == 24
        and r["provider"]["committed_upper_microusd"] == 232264
        and r["provider"]["unknown_usage_requests"] == 4,
        "LEDGER_DRIFT",
    )
    require(r["actual_invoice_usd"] is None, "INVOICE_UNKNOWN")
    return {
        "verification": "PASS",
        "terminal": r["terminal"],
        "owned_remaining": cleanup["remaining"],
        "global_clean": False,
        "live_episodes": 0,
    }


def verify():
    verify_history()
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
    for path, expected in offline["source_sha256"].items():
        require(
            hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected,
            "SOURCE_DRIFT:" + path,
        )
    return verify_claims(json.loads((RESULT / "result.json").read_text()))


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
