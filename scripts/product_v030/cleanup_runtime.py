"""Restore and clean only the Goal runtime bound to its captured pre-start inventory."""

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path

import httpx

from ecomsre_live_sandbox.contracts import write_private_json
from ecomsre_live_sandbox.environment import DockerSnapshot
from ecomsre_live_sandbox.knowledge_v030 import (
    ProductV030Lifecycle,
    observe_queue_lag_v030,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    private = args.private_root.resolve()
    if not private.is_relative_to(root / ".local/product-v030"):
        raise ValueError("Goal root differs")
    lifecycle = ProductV030Lifecycle(
        repository_root=root,
        private_root=private,
        image_identities=root / ".local/product-v030/acquired-images.json",
    )
    lifecycle.admit()
    assert lifecycle.goal_controller is not None
    flags = lifecycle.goal_controller.read("BASELINE")
    with httpx.Client(timeout=10) as client:
        lag = observe_queue_lag_v030(client)
    if lag["lag"] >= 20:
        raise RuntimeError("cleanup requires drained queue")
    before = json.loads((private / "control/pre-start-inventory.json").read_text())
    if before["resolved_sha256"] != lifecycle.admitted_resolved_sha256:
        raise ValueError("cleanup pre-start authority differs")
    counts = lifecycle.environment.verify_owned_resources(require_complete=True)
    lifecycle.environment._baseline_snapshot = DockerSnapshot(
        containers=frozenset(before["containers"]),
        networks=frozenset(before["networks"]),
        volumes=frozenset(before["volumes"]),
    )
    result = lifecycle.cleanup_owned(baseline_unchanged=True)
    record = {
        "observed_at": datetime.now(UTC).isoformat(),
        "flags": flags,
        "lag": lag,
        "removed_owned_counts": counts,
        "cleanup": result.model_dump(mode="json"),
    }
    write_private_json(private / "control/cleanup.json", record, create_once=True)
    print(json.dumps(record), flush=True)
    if result.verdict != "CLEAN":
        raise RuntimeError("owned cleanup did not prove clean")


if __name__ == "__main__":
    main()
