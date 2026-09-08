"""Verify the v4 contract and preserve the historical base; no Docker access."""

from pathlib import Path
import json
import subprocess
from scripts.product.preflight_v040_v4.common import (
    BASE,
    TREE,
    UPSTREAM,
    GOAL_PATH,
    GOAL_SHA,
    sha,
    require,
)


def verify(root: Path) -> dict[str, object]:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

    require(sha((root / GOAL_PATH).read_bytes()) == GOAL_SHA, "GOAL_HASH_DRIFT")
    require(git("rev-parse", BASE + "^{tree}") == TREE, "BASE_TREE_DRIFT")
    require(
        git("rev-parse", "HEAD:third_party/opentelemetry-demo") == UPSTREAM,
        "UPSTREAM_DRIFT",
    )
    contract = json.loads(
        (root / "config/product-v040/preflight-v4/goal-contract.json").read_text()
    )
    require(
        contract["goal_sha256"] == GOAL_SHA
        and contract["max_attempts"] == 5
        and contract["consecutive_complete_passes"] == 2
        and contract["formal_authority"] == "NONE",
        "CONTRACT_DRIFT",
    )
    paths = git("diff", "--name-only", BASE, "--").splitlines()
    allowed = (
        "scripts/product/preflight_v040_v4/",
        "tests/product_v040/preflight_v4/",
        "config/product-v040/preflight-v4/",
        "docs/results/product-v040-preflight-v4/",
        "docs/analysis/product-v040-preflight-v4-",
        "docs/external-reviews/product-v040-preflight-v4-",
    )
    exact = {
        GOAL_PATH,
        "docker-compose.product.preflight-v4.yml",
        "scripts/ci/verify_product_v040_preflight_v4.py",
        "Dockerfile.product",
        "docker-compose.product.yml",
        ".github/workflows/agent-mainline.yml",
    }
    require(
        all(p in exact or p.startswith(allowed) for p in paths),
        "UNDECLARED_TRACKED_PATH",
    )
    provenance = json.loads(
        (
            root / "config/product-v040/preflight-v4/kafka-image-provenance.json"
        ).read_text()
    )
    source = provenance["source"]
    raw_policy = subprocess.check_output(
        ["git", "show", source["commit"] + ":" + source["path"]], cwd=root
    )
    require(sha(raw_policy) == source["sha256"], "HISTORICAL_POLICY_HASH_DRIFT")
    old_policy = json.loads(raw_policy)
    require(
        old_policy["source_archive_sha256"] == source["archive_sha256"],
        "HISTORICAL_ARCHIVE_DRIFT",
    )
    image_source = old_policy["image_source"]
    for key, value in provenance["image"].items():
        if key in ("entries", "identity_files"):
            continue
        require(value == image_source[key], "HISTORICAL_IMAGE_PROJECTION_DRIFT:" + key)
    image_facts = json.loads(
        (root / "config/product-v040/preflight-v4/images.json").read_text()
    )
    historical_index = json.loads(
        subprocess.check_output(
            [
                "git",
                "show",
                image_facts["source_commit"]
                + ":docs/results/product-v040-qualification-v3/qualification-evidence-index.json",
            ],
            cwd=root,
        )
    )
    require(
        historical_index["files"][image_facts["source_record"]]
        == image_facts["source_record_sha256"],
        "HISTORICAL_IMAGE_EVIDENCE_DRIFT",
    )
    require(len(image_facts["images"]) == 28, "IMAGE_ROLE_COUNT")
    progress = json.loads(
        (root / "docs/analysis/product-v040-preflight-v4-progress.json").read_text()
    )
    require(0 <= progress["attempt_count"] <= 5, "ATTEMPT_BUDGET")
    return {
        "status": "PASS",
        "scope": "CONTRACT_TRACKED_SCOPE_AND_HISTORY_INTEGRITY",
        "tracked_delta_count": len(paths),
        "history_integrity": "PASS",
        "live_admission": progress["live_admission"],
        "attempt_count": progress["attempt_count"],
    }


if __name__ == "__main__":
    print(json.dumps(verify(Path(__file__).resolve().parents[2]), sort_keys=True))
