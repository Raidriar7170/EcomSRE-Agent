"""Offline-only integrity/preflight checks; never contact Docker or a Provider."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from scripts.product.v040_ownership import (
    BASE,
    COUNTERS,
    STAGES,
    sha,
    validate_mount_provenance,
)
from scripts.product.v040_ownership_provenance import kafka_overlay


def verify(repository: Path) -> dict[str, object]:
    root = repository / "config/product-v040/ownership-repair"
    manifest = json.loads((root / "environment-provenance.v1.json").read_bytes())
    digest = manifest.pop("manifest_sha256")
    if sha(manifest) != digest or manifest["historical_head"] != BASE:
        raise ValueError("manifest binding differs")
    validate_mount_provenance(manifest)
    overlay = json.loads((root / "compose.kafka-overlays.v1.json").read_bytes())
    if overlay != kafka_overlay() or sha(overlay) != manifest["kafka_overlay_sha256"]:
        raise ValueError("Kafka overlay differs")
    if manifest["stages"] != list(STAGES) or manifest["counts"] != COUNTERS:
        raise ValueError("stage or offline authority differs")
    runner = ast.parse((repository / "scripts/product/run_payment_v040.py").read_text())
    for name in ("main", "prepare", "campaign"):
        node = next(
            n for n in runner.body if isinstance(n, ast.FunctionDef) and n.name == name
        )
        first = node.body[0]
        if not (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Call)
            and isinstance(first.value.func, ast.Name)
            and first.value.func.id == "require_offline_only"
        ):
            raise ValueError("offline entrypoint lock is missing")
    return {
        "schema_version": "ecomsre.product.v040.offline-ownership-preflight.v1",
        "status": "PASS_OFFLINE_ONLY",
        "pr_disposition": "Draft / REVIEW_REQUIRED",
        "manifest_sha256": digest,
        "services": len(manifest["services"]),
        "mounts": len(manifest["mounts"]),
        "named_volumes": len(manifest["volumes"]),
        "stages": len(STAGES),
        "authority": "NONE",
        "counts": COUNTERS,
        "runtime_observations": 0,
        "formal_allowance_consumed": False,
        "claim_boundary": "Offline provenance and replay only; no startup/copy-up/network runtime claim",
    }


def main() -> None:
    print(
        json.dumps(
            verify(Path(__file__).resolve().parents[2]), indent=2, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
