from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval.artifacts import (
    sha256_file,
    sha256_tree,
    write_json_create_once,
)
from ecomsre_rcaeval.freeze import verify_source_bound_snapshot
from ecomsre_rcaeval.lifecycle import advance_state, current_state
from ecomsre_rcaeval.sanitize import seal_holdout
from ecomsre_rcaeval.state import HoldoutState


def main() -> int:
    parser = argparse.ArgumentParser(description="Seal raw RCAEval holdout behind opaque IDs")
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--sanitized-root", type=Path, required=True)
    parser.add_argument("--evaluator-root", type=Path, required=True)
    parser.add_argument("--opaque-seed", required=True)
    args = parser.parse_args()
    journal = args.control_root / "state-journal"
    if current_state(journal) is not HoldoutState.PROTOCOL_FROZEN:
        raise ValueError("holdout sealing requires PROTOCOL_FROZEN state")
    verify_source_bound_snapshot(args.control_root)
    result = seal_holdout(
        args.raw_root,
        args.sanitized_root,
        args.evaluator_root,
        expected_cases=90,
        opaque_seed=args.opaque_seed,
    )
    evidence_sha = write_json_create_once(
        args.control_root / "locks" / "holdout-seal.json",
        {
            "schema_version": "rcaeval-re2.holdout-seal-lock.v1",
            "result": result.model_dump(mode="json"),
            "sanitized_manifest_sha256": sha256_file(
                args.sanitized_root / "manifest.json"
            ),
            "sanitized_tree_sha256": sha256_tree(
                args.sanitized_root,
                include_suffixes=(".csv", ".json"),
            ),
            "ground_truth_sha256": sha256_file(
                args.evaluator_root / "ground-truth.json"
            ),
        },
    )
    advance_state(
        journal,
        HoldoutState.HOLDOUT_SEALED,
        evidence_sha256=evidence_sha,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
