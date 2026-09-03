"""Start the exact admitted Goal runtime and retain its pre-start inventory."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path

from ecomsre_live_sandbox.contracts import write_private_json
from ecomsre_live_sandbox.knowledge_v030 import ProductV030Lifecycle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    private = args.private_root.resolve()
    if not private.is_relative_to(root / ".local/product-v030"):
        raise ValueError("runtime must remain inside the private v0.3 Goal directory")
    lifecycle = ProductV030Lifecycle(
        repository_root=root,
        private_root=private,
        image_identities=root / ".local/product-v030/acquired-images.json",
    )
    lifecycle.admit()
    print("stage=ADMITTED_28_SERVICES", flush=True)
    before = lifecycle.environment.snapshot_all_resources()
    write_private_json(
        private / "control/pre-start-inventory.json",
        {
            "observed_at": datetime.now(UTC).isoformat(),
            "resolved_sha256": lifecycle.admitted_resolved_sha256,
            "containers": sorted(before.containers),
            "networks": sorted(before.networks),
            "volumes": sorted(before.volumes),
        },
        create_once=True,
    )
    try:
        lifecycle.start()
        print("stage=STARTED_28_SERVICES", flush=True)
        lifecycle.wait_ready()
        backend = lifecycle.authorize_reads()
        del backend
        if lifecycle.goal_controller is None:
            raise RuntimeError("Goal controller is absent")
        baseline = lifecycle.goal_controller.read("BASELINE")
        actual_before = lifecycle.environment._baseline_snapshot
        if actual_before != before:
            raise RuntimeError("resource inventory changed during start admission")
        result = {
            "status": "RUNTIME_READY",
            "observed_at": datetime.now(UTC).isoformat(),
            "services": lifecycle.environment.service_health(),
            "baseline": baseline,
            "resolved_sha256": lifecycle.admitted_resolved_sha256,
            "owned_counts": lifecycle.environment.verify_owned_resources(
                require_complete=True
            ),
        }
        write_private_json(
            private / "control/runtime-ready.json", result, create_once=True
        )
        print(
            json.dumps(
                {"status": result["status"], "owned_counts": result["owned_counts"]}
            ),
            flush=True,
        )
    except Exception as error:
        baseline_verified = False
        if lifecycle.goal_controller is not None:
            try:
                lifecycle.goal_controller.read("BASELINE")
                baseline_verified = True
            except Exception:
                pass
        cleanup = lifecycle.cleanup_owned(baseline_unchanged=baseline_verified)
        write_private_json(
            private / "control/start-failure.json",
            {
                "exception_type": type(error).__name__,
                "message": str(error),
                "cleanup": cleanup.model_dump(mode="json"),
            },
            create_once=True,
        )
        raise


if __name__ == "__main__":
    main()
