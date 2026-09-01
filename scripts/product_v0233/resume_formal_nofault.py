#!/usr/bin/env python3
"""Verify and inspect a resumable Product v0.2.3.3 formal attempt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from ecomsre.product.pilot.formal_recovery_v0233 import (
    FormalCheckpointRepositoryV0233,
    determine_earliest_safe_resume_state_v0233,
    verify_checkpoint_artifacts_v0233,
)
from ecomsre.product.pilot.serialization_v0233 import semantic_json_sha256_v0233
from scripts.product_v0233.run_formal_nofault import _formal_surfaces_v0233


def inspect_formal_resume_v0233(
    *,
    project_root: Path,
    attempt_id: str,
) -> dict[str, Any]:
    root = Path(project_root).resolve(strict=True)
    attempt_root = root / ".local/product-v0233/attempts" / attempt_id
    repository = FormalCheckpointRepositoryV0233(attempt_root)
    chain = repository.load_chain()
    if not chain:
        raise ValueError("Product v0.2.3.3 attempt has no resumable checkpoint")
    latest = chain[-1]
    semantic, operational = _formal_surfaces_v0233(
        root,
        semantic_generation=latest.semantic_generation,
    )
    if semantic.semantic_surface_sha256 != latest.semantic_surface_sha256:
        raise ValueError("Product v0.2.3.3 resume semantic surface differs")
    verify_checkpoint_artifacts_v0233(root, latest)
    resume_state = determine_earliest_safe_resume_state_v0233(latest)
    body = {
        "schema_version": "ecomsre.product.formal-resume-decision.v0233",
        "campaign_id": latest.campaign_id,
        "semantic_generation": latest.semantic_generation,
        "attempt_id": latest.attempt_id,
        "latest_checkpoint_sha256": latest.checkpoint_sha256,
        "resume_state": resume_state.value,
        "semantic_surface_sha256": semantic.semantic_surface_sha256,
        "checkpoint_operational_surface_sha256": (
            latest.operational_surface_sha256
        ),
        "current_operational_surface_sha256": operational.operational_surface_sha256,
        "operational_surface_changed": (
            operational.operational_surface_sha256
            != latest.operational_surface_sha256
        ),
        "referenced_artifacts_verified": True,
    }
    return {**body, "decision_sha256": semantic_json_sha256_v0233(body)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", required=True)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    arguments = parser.parse_args(argv)
    decision = inspect_formal_resume_v0233(
        project_root=arguments.project_root,
        attempt_id=arguments.attempt,
    )
    print(json.dumps(decision, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("inspect_formal_resume_v0233",)
