"""Independent evidence checks, reusable offline and after exact cleanup."""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .core import (
    Authority,
    BASE,
    GOAL_PATH,
    GOAL_SHA,
    KINDS,
    PROBE,
    digest,
    nonowned,
    require,
    sha,
    validate_pair,
)

PREFIXES = (
    "scripts/product/cleanup_v040_v3_retained/",
    "tests/product_v040/cleanup_v3_retained/",
    "docs/results/product-v040-v3-retained-cleanup/",
    "docs/external-reviews/product-v040-v3-retained-cleanup-",
)


def scope(paths: list[str]) -> None:
    require(
        all(p == GOAL_PATH or p.startswith(PREFIXES) for p in paths),
        "HISTORICAL_OR_UNDECLARED_CHANGE",
    )


def verify(root: Path, a: Authority) -> dict[str, Any]:
    index = json.loads((root / "private-evidence-index.json").read_bytes())
    require(digest(index["files"]) == index["files_sha256"], "INDEX_DIGEST")
    actual = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}
    require(
        actual == set(index["files"]) | {"private-evidence-index.json"},
        "INDEX_COVERAGE",
    )
    for name, expected in index["files"].items():
        require(
            ".." not in Path(name).parts and not Path(name).is_absolute(), "INDEX_PATH"
        )
        p = root / name
        require(
            sha(p.read_bytes()) == expected and p.stat().st_mode & 0o777 == 0o600,
            "INDEX_FILE",
        )
    result = json.loads((root / "final-cleanup-result.json").read_bytes())
    require(
        result["goal_sha256"] == GOAL_SHA and result["starting_head"] == BASE,
        "RESULT_BINDING",
    )
    intents = sorted((root / "mutation-intents").glob("*.json"))
    receipts = sorted((root / "mutation-receipts").glob("*.json"))
    require(len(intents) == len(receipts), "UNRESOLVED_MUTATION_INTENT")
    previous = None
    seen = set()
    counts = {"stop": 0, "container_rm": 0, "network_rm": 0, "volume_rm": 0}
    removed: dict[str, list[str]] = {k: [] for k in KINDS}
    for number, (ip, rp) in enumerate(zip(intents, receipts)):
        require(ip.stem == rp.stem == f"{number:03d}", "RECEIPT_SEQUENCE")
        intent, receipt = json.loads(ip.read_bytes()), json.loads(rp.read_bytes())
        content = dict(receipt)
        claimed = content.pop("receipt_sha256")
        require(
            digest(content) == claimed
            and receipt["previous_receipt_sha256"] == previous,
            "RECEIPT_CHAIN",
        )
        require(receipt["intent_sha256"] == sha(ip.read_bytes()), "INTENT_BINDING")
        require(all(receipt[k] == v for k, v in intent.items()), "INTENT_RECEIPT_DRIFT")
        kind, rid, argv = (
            intent["resource_kind"],
            intent["retained_identity"],
            intent["command"],
        )
        op = argv[2]
        require(
            argv == a.argv(kind, rid, op) and (kind, rid, op) not in seen,
            "COMMAND_AUTHORITY",
        )
        require(
            intent["retained_record_digest"] == digest(a.records[kind][rid]),
            "RETAINED_RECORD",
        )
        require(
            receipt["started_at"]["monotonic_ns"]
            <= receipt["ended_at"]["monotonic_ns"],
            "TIME_ORDER",
        )
        seen.add((kind, rid, op))
        counts["stop" if op == "stop" else kind[:-1] + "_rm"] += 1
        if receipt["outcome"] == "VERIFIED" and op == "rm":
            removed[kind].append(rid)
        if receipt["outcome"] != "VERIFIED":
            require(number == len(receipts) - 1, "CONTINUED_AFTER_FAILURE")
        previous = sha(rp.read_bytes())
    require(
        counts["stop"] <= 29
        and counts["container_rm"] <= 29
        and counts["network_rm"] <= 3
        and counts["volume_rm"] <= 6,
        "CARDINALITY",
    )
    if result.get("runtime_checks") == "PASS":
        first = json.loads((root / "post-cleanup-inventory-1.json").read_bytes())
        final = json.loads((root / "post-cleanup-inventory-2.json").read_bytes())
        validate_pair(first, final)
        require(
            all(
                x == "ALREADY_ABSENT"
                for rows in a.validate(final).values()
                for x in rows.values()
            ),
            "FINAL_ABSENCE",
        )
        baseline = json.loads((root / "pre-cleanup-inventory-2.json").read_bytes())
        require(baseline["binding"] == final["binding"], "DAEMON_IDENTITY_DRIFT")
        require(
            nonowned(baseline, a, PROBE in removed["containers"])
            == nonowned(final, a, False),
            "NONOWNED_DRIFT",
        )
        require(
            result["removed"] == removed and result["counts"] == counts,
            "RESULT_COUNTERS",
        )
        for kind in KINDS:
            for rid in removed[kind]:
                require(
                    result["initial_disposition"][kind][rid] == "PRESENT_MATCHING",
                    "ABSENCE_ATTRIBUTION",
                )
    return {
        "status": "PASS",
        "files_verified": len(index["files"]),
        "intent_receipt_pairs": len(intents),
        "counts": counts,
        "removed": removed,
        "history_unchanged_required_separately": True,
    }
