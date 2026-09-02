#!/usr/bin/env python3
"""Apply the bounded offline fixes from pre-execution review round 1."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.fresh_formal_acceptance_v0233 import (
    load_fresh_formal_campaign_v0233,
)
from ecomsre.product.pilot.repository_state_v0233 import (
    ProductV0233RepositoryStateManifest,
    RepositoryPhaseV0233,
)
from ecomsre.product.pilot.traffic_preflight_v0233 import (
    TrafficPreflightAttemptV0233,
    TrafficPreflightLedgerV0233,
    TrafficPreflightPassV0233,
    TrafficRepairSurfaceSnapshotV0233,
)
from scripts.product_v0233.run_traffic_preflight import (
    _formal_freeze,
    _load_object,
    _replace_public,
    _write_public_create_once,
)


_ATTEMPT_SHA256 = "14b2a91feee54bfdde75d3ddf37a514cbe2830ec18884b810dffd4917c3f509d"
_PREFLIGHT_SHA256 = "12c69bca49283698180a1ea9a741fb59c3a60949b80945c96a379eae929ad9a6"


def repair_pre_execution_freeze(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    attempt_path = root / "docs/analysis/product-v0233-traffic-preflight-attempt-1.json"
    ledger_path = root / "docs/analysis/product-v0233-traffic-preflight-ledger.json"
    preflight_path = root / "docs/analysis/product-v0233-traffic-preflight.json"
    freeze_path = root / "docs/analysis/product-v0233-formal-contract-freeze.json"
    round_one_path = (
        root
        / "docs/analysis/product-v0233-formal-contract-freeze-review-round-1.json"
    )
    attempt_bytes = attempt_path.read_bytes()
    ledger_bytes = ledger_path.read_bytes()
    preflight_bytes = preflight_path.read_bytes()
    attempt = TrafficPreflightAttemptV0233.model_validate_json(attempt_bytes)
    ledger = TrafficPreflightLedgerV0233.model_validate_json(ledger_bytes)
    preflight = TrafficPreflightPassV0233.model_validate_json(preflight_bytes)
    snapshot = TrafficRepairSurfaceSnapshotV0233.model_validate_json(
        (
            root
            / "docs/analysis/product-v0233-traffic-repair-surface-attempt-1.json"
        ).read_bytes()
    )
    prepared = ProductV0233RepositoryStateManifest.model_validate_json(
        (root / "config/product-v0233/repository-state-manifest.json").read_bytes()
    )
    if (
        attempt.attempt_sha256 != _ATTEMPT_SHA256
        or preflight.preflight_sha256 != _PREFLIGHT_SHA256
        or ledger.attempts != (attempt,)
        or preflight.attempt_sha256 != attempt.attempt_sha256
        or snapshot.attempt_sha256 != attempt.attempt_sha256
        or prepared.phase is not RepositoryPhaseV0233.PREPARED
    ):
        raise ValueError("Product v0.2.3.3 review-fix boundary differs")
    campaign = load_fresh_formal_campaign_v0233(root)
    clone_plan, source_manifest, freeze = _formal_freeze(
        root,
        preflight=preflight,
        prepared_manifest=prepared,
        campaign=campaign,
        attempt=attempt,
    )
    clone_plan_path = root / "docs/analysis/product-v0233-formal-clone-plan.json"
    source_manifest_path = (
        root / "docs/analysis/product-v0233-diagnosis-source-manifest.json"
    )
    if clone_plan_path.exists():
        if _load_object(clone_plan_path) != clone_plan.model_dump(mode="json"):
            raise ValueError("Product v0.2.3.3 formal clone plan drifted")
    else:
        _write_public_create_once(
            clone_plan_path,
            clone_plan.model_dump(mode="json"),
        )
    if source_manifest_path.exists():
        if _load_object(source_manifest_path) != source_manifest.model_dump(mode="json"):
            raise ValueError("Product v0.2.3.3 Diagnosis source manifest drifted")
    else:
        _write_public_create_once(
            source_manifest_path,
            source_manifest.model_dump(mode="json"),
        )
    current_freeze = _load_object(freeze_path)
    if current_freeze != freeze.model_dump(mode="json"):
        if freeze_path.read_bytes() != round_one_path.read_bytes():
            raise ValueError("Product v0.2.3.3 formal freeze repair history differs")
        _replace_public(freeze_path, freeze.model_dump(mode="json"))
    progress_path = root / "docs/analysis/product-v0233-progress.json"
    round_one_review_path = (
        root
        / "docs/external-reviews/product-v0233-pre-execution-review-round-1.md"
    )
    round_two_review_path = (
        root
        / "docs/external-reviews/product-v0233-pre-execution-review-round-2.md"
    )
    prior_progress = _load_object(progress_path)
    body = {
        **{
            key: value
            for key, value in prior_progress.items()
            if key != "progress_sha256"
        },
        "phase": "INCREMENT_3_TRAFFIC_PREFLIGHT_PASS",
        "current_terminal": "ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT_PASS",
        "live_traffic_preflight_count": ledger.attempt_count,
        "traffic_preflight_attempt_sha256": attempt.attempt_sha256,
        "traffic_preflight_sha256": preflight.preflight_sha256,
        "formal_contract_freeze_sha256": freeze.freeze_sha256,
        "pre_execution_review_round_1": "PASS_WITH_FIXES_CLAIM_ACCURACY_FAIL",
        "pre_execution_review_round_1_sha256": hashlib.sha256(
            round_one_review_path.read_bytes()
        ).hexdigest(),
        "pre_execution_review_round_2": "PASS_WITH_FIXES_CLAIM_ACCURACY_FAIL",
        "pre_execution_review_round_2_sha256": hashlib.sha256(
            round_two_review_path.read_bytes()
        ).hexdigest(),
        "formal_contract_freeze_round_1_sha256": hashlib.sha256(
            round_one_path.read_bytes()
        ).hexdigest(),
        "traffic_repair_surface_snapshot_sha256": snapshot.snapshot_sha256,
        "formal_clone_plan_sha256": clone_plan.plan_sha256,
        "diagnosis_semantic_source_manifest_sha256": (
            source_manifest.manifest_sha256
        ),
        "next_gate": (
            "EXPLICIT_AUTHORIZATION_FOR_PRODUCT_V0233_"
            "PRE_EXECUTION_REVIEW_ROUND_3"
        ),
    }
    _replace_public(
        progress_path,
        {**body, "progress_sha256": semantic_sha256_v22(body)},
    )
    if (
        attempt_path.read_bytes() != attempt_bytes
        or ledger_path.read_bytes() != ledger_bytes
        or preflight_path.read_bytes() != preflight_bytes
    ):
        raise RuntimeError("Product v0.2.3.3 live preflight bytes changed")
    return {
        "terminal": "ECOMSRE_PRODUCT_V0233_PRE_EXECUTION_REVIEW_FIXES_APPLIED",
        "attempt_sha256": attempt.attempt_sha256,
        "preflight_sha256": preflight.preflight_sha256,
        "formal_clone_plan_sha256": clone_plan.plan_sha256,
        "diagnosis_source_count": source_manifest.source_count,
        "diagnosis_semantic_source_manifest_sha256": (
            source_manifest.manifest_sha256
        ),
        "formal_contract_freeze_sha256": freeze.freeze_sha256,
        "formal_clone_count": 0,
        "formal_execution_count": 0,
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(argv)
    result = repair_pre_execution_freeze(arguments.project_root)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("repair_pre_execution_freeze",)
