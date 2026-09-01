#!/usr/bin/env python3
"""Freeze the post-attempt, pre-repair v0.2.3.3 harness surface."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.traffic_preflight_v0233 import (
    ALLOWED_REPAIR_SURFACES_V0233,
    TrafficRepairSurfaceSnapshotV0233,
)
from ecomsre_live_sandbox.contracts import canonical_json_bytes


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_create_once(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Product v0.2.3.3 artifact exists: {path.name}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(dict(payload)))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def freeze_surface(
    *, project_root: Path, attempt_ordinal: int, preserve_round_one_freeze: bool
) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    attempt_path = (
        root
        / "docs/analysis"
        / f"product-v0233-traffic-preflight-attempt-{attempt_ordinal}.json"
    )
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    attempt_sha256 = attempt.get("attempt_sha256")
    if not isinstance(attempt_sha256, str) or len(attempt_sha256) != 64:
        raise ValueError("Product v0.2.3.3 Attempt seal differs")
    source_sha256_by_path = {
        path: _sha256_file(root / path) for path in ALLOWED_REPAIR_SURFACES_V0233
    }
    body: dict[str, Any] = {
        "schema_version": "ecomsre.product.traffic-repair-surface.v0233",
        "phase": "POST_ATTEMPT_PRE_REPAIR",
        "attempt_ordinal": attempt_ordinal,
        "attempt_sha256": attempt_sha256,
        "allowed_surface_paths": list(ALLOWED_REPAIR_SURFACES_V0233),
        "source_sha256_by_path": source_sha256_by_path,
    }
    snapshot = TrafficRepairSurfaceSnapshotV0233.model_validate(
        {**body, "snapshot_sha256": semantic_sha256_v22(body)}
    ).model_dump(mode="json")
    _write_create_once(
        root
        / "docs/analysis"
        / f"product-v0233-traffic-repair-surface-attempt-{attempt_ordinal}.json",
        snapshot,
    )
    if preserve_round_one_freeze:
        source = root / "docs/analysis/product-v0233-formal-contract-freeze.json"
        target = (
            root
            / "docs/analysis/product-v0233-formal-contract-freeze-review-round-1.json"
        )
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Product v0.2.3.3 formal freeze differs")
        _write_create_once(target, payload)
    return snapshot


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--preserve-round-one-freeze", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(argv)
    result = freeze_surface(
        project_root=arguments.project_root,
        attempt_ordinal=arguments.attempt,
        preserve_round_one_freeze=arguments.preserve_round_one_freeze,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("ALLOWED_REPAIR_SURFACES_V0233", "freeze_surface")
