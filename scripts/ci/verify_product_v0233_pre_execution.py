#!/usr/bin/env python3
"""Verify the Product v0.2.3.3 pre-execution gate and frozen evidence graph."""

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
from ecomsre.product.pilot.healthy_traffic_v0232 import HealthyTrafficExecutionV0232
from ecomsre.product.pilot.repository_state_v0233 import (
    ProductV0233RepositoryStateManifest,
    RepositoryPhaseV0233,
)
from ecomsre.product.pilot.traffic_preflight_v0233 import (
    DiagnosisSemanticSourceManifestV0233,
    FormalClonePlanV0233,
    FormalContractFreezeV0233,
    TrafficPreflightAttemptV0233,
    TrafficPreflightLedgerV0233,
    TrafficPreflightPassV0233,
    TrafficRepairSurfaceSnapshotV0233,
)
from scripts.product_v0233.run_traffic_preflight import _diagnosis_source_manifest


_ATTEMPT_SHA256 = "14b2a91feee54bfdde75d3ddf37a514cbe2830ec18884b810dffd4917c3f509d"
_PREFLIGHT_SHA256 = "12c69bca49283698180a1ea9a741fb59c3a60949b80945c96a379eae929ad9a6"
_REVIEW_PATH = "docs/external-reviews/product-v0233-pre-execution-review.md"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Product v0.2.3.3 JSON object differs: {path.name}")
    return payload


def verify_product_v0233_pre_execution(
    project_root: Path, *, require_review: bool = True
) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    campaign = load_fresh_formal_campaign_v0233(root)
    attempt = TrafficPreflightAttemptV0233.model_validate_json(
        (
            root
            / "docs/analysis/product-v0233-traffic-preflight-attempt-1.json"
        ).read_bytes()
    )
    ledger = TrafficPreflightLedgerV0233.model_validate_json(
        (
            root / "docs/analysis/product-v0233-traffic-preflight-ledger.json"
        ).read_bytes()
    )
    preflight = TrafficPreflightPassV0233.model_validate_json(
        (root / "docs/analysis/product-v0233-traffic-preflight.json").read_bytes()
    )
    surface = TrafficRepairSurfaceSnapshotV0233.model_validate_json(
        (
            root
            / "docs/analysis/product-v0233-traffic-repair-surface-attempt-1.json"
        ).read_bytes()
    )
    clone_plan = FormalClonePlanV0233.model_validate_json(
        (root / "docs/analysis/product-v0233-formal-clone-plan.json").read_bytes()
    )
    sources = DiagnosisSemanticSourceManifestV0233.model_validate_json(
        (
            root / "docs/analysis/product-v0233-diagnosis-source-manifest.json"
        ).read_bytes()
    )
    freeze = FormalContractFreezeV0233.model_validate_json(
        (
            root / "docs/analysis/product-v0233-formal-contract-freeze.json"
        ).read_bytes()
    )
    manifest = ProductV0233RepositoryStateManifest.model_validate_json(
        (root / "config/product-v0233/repository-state-manifest.json").read_bytes()
    )
    progress = _object(root / "docs/analysis/product-v0233-progress.json")
    progress_sha256 = progress.pop("progress_sha256", None)
    private_execution = HealthyTrafficExecutionV0232.model_validate_json(
        (
            root
            / ".local/product-v0233/traffic-preflight"
            / attempt.attempt_id
            / "traffic-execution.json"
        ).read_bytes()
    )
    observed_source_sha256_by_path = {
        path: _sha256_file(root / path) for path in sources.source_sha256_by_path
    }
    regenerated_sources = _diagnosis_source_manifest(root)
    round_one_freeze_path = (
        root
        / "docs/analysis/product-v0233-formal-contract-freeze-review-round-1.json"
    )
    round_one_review_path = (
        root
        / "docs/external-reviews/product-v0233-pre-execution-review-round-1.md"
    )
    round_one_freeze = _object(round_one_freeze_path)
    round_one_review_lines = set(
        round_one_review_path.read_text(encoding="utf-8").splitlines()
    )
    round_two_review_path = (
        root
        / "docs/external-reviews/product-v0233-pre-execution-review-round-2.md"
    )
    round_two_review_lines = set(
        round_two_review_path.read_text(encoding="utf-8").splitlines()
    )
    zero_counters = (
        attempt.formal_clone_count,
        attempt.formal_execution_count,
        attempt.new_incident_count,
        attempt.new_diagnosis_count,
        attempt.measured_result_count,
        attempt.fault_attempt_count,
        attempt.provider_calls,
        attempt.agent_writes,
        attempt.runbook_executions,
    )
    if (
        attempt.attempt_sha256 != _ATTEMPT_SHA256
        or preflight.preflight_sha256 != _PREFLIGHT_SHA256
        or ledger.attempts != (attempt,)
        or preflight.attempt_sha256 != attempt.attempt_sha256
        or preflight.ledger_sha256 != ledger.ledger_sha256
        or private_execution != attempt.execution
        or not private_execution.run.passed
        or private_execution.run.successful_transactions != 10
        or private_execution.run.failed_transactions != 0
        or private_execution.run.transport_retry_count != 0
        or surface.attempt_sha256 != attempt.attempt_sha256
        or clone_plan.source_selection_sha256 != attempt.source_selection_sha256
        or clone_plan.plan_sha256 != freeze.formal_clone_plan_sha256
        or sources.manifest_sha256
        != freeze.diagnosis_semantic_source_manifest_sha256
        or regenerated_sources != sources
        or observed_source_sha256_by_path != sources.source_sha256_by_path
        or freeze.traffic_preflight_sha256 != preflight.preflight_sha256
        or freeze.campaign_sha256 != campaign.campaign_sha256
        or freeze.preflight_profile_file_sha256
        != _sha256_file(root / "config/product-v0233/traffic/preflight-profile.json")
        or freeze.formal_profile_file_sha256
        != _sha256_file(root / "config/product-v0233/traffic/formal-profile.json")
        or any(zero_counters)
        or attempt.action_authority != "NONE"
        or progress_sha256 != semantic_sha256_v22(progress)
        or progress.get("current_terminal")
        != "ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT_PASS"
        or progress.get("live_traffic_preflight_count") != 1
        or progress.get("formal_contract_freeze_sha256") != freeze.freeze_sha256
        or round_one_freeze.get("freeze_sha256")
        != "300a0b28ae296fb0e29f55774d707f6c13d79688c359e0fc47f2a6f6718a0919"
        or progress.get("formal_contract_freeze_round_1_sha256")
        != _sha256_file(round_one_freeze_path)
        or progress.get("pre_execution_review_round_1_sha256")
        != _sha256_file(round_one_review_path)
        or progress.get("pre_execution_review_round_2_sha256")
        != _sha256_file(round_two_review_path)
        or not {
            "- Verdict: Pass with fixes",
            "- Must Fix: 4",
            "- Claim Accuracy: FAIL",
        }.issubset(round_one_review_lines)
        or not {
            "- Verdict: Pass with fixes",
            "- Must Fix: 1",
            "- Claim Accuracy: FAIL",
        }.issubset(round_two_review_lines)
    ):
        raise ValueError("Product v0.2.3.3 pre-execution evidence graph differs")
    review_sha256: str | None = None
    if require_review:
        review_path = root / _REVIEW_PATH
        review_text = review_path.read_text(encoding="utf-8")
        review_lines = set(review_text.splitlines())
        review_sha256 = _sha256_file(review_path)
        if (
            manifest.phase is not RepositoryPhaseV0233.TRAFFIC_PREFLIGHT_PASS
            or manifest.traffic_preflight_sha256 != preflight.preflight_sha256
            or manifest.formal_contract_freeze_sha256 != freeze.freeze_sha256
            or manifest.pre_execution_review_sha256 != review_sha256
            or "- Verdict: Pass" not in review_lines
            or "- Must Fix: 0" not in review_lines
            or "- Claim Accuracy: PASS" not in review_lines
            or progress.get("repository_state_manifest_sha256")
            != manifest.manifest_sha256
            or progress.get("pre_execution_review_sha256") != review_sha256
            or progress.get("next_gate") != "PRODUCT_V0233_FORMAL_EXECUTION"
        ):
            raise ValueError("Product v0.2.3.3 pre-execution review gate differs")
    elif (
        manifest.phase is not RepositoryPhaseV0233.PREPARED
        or manifest.traffic_preflight_sha256 is not None
        or manifest.formal_contract_freeze_sha256 is not None
        or manifest.pre_execution_review_sha256 is not None
    ):
        raise ValueError("Product v0.2.3.3 review-pending manifest differs")
    return {
        "terminal": "ECOMSRE_PRODUCT_V0233_PRE_EXECUTION_GATE_PASS",
        "attempt_sha256": attempt.attempt_sha256,
        "traffic_preflight_sha256": preflight.preflight_sha256,
        "formal_contract_freeze_sha256": freeze.freeze_sha256,
        "formal_clone_plan_sha256": clone_plan.plan_sha256,
        "diagnosis_semantic_source_manifest_sha256": sources.manifest_sha256,
        "diagnosis_source_count": sources.source_count,
        "pre_execution_review_sha256": review_sha256,
        "formal_clone_count": manifest.formal_clone_count,
        "formal_execution_count": manifest.formal_execution_count,
        "new_incident_count": manifest.new_incident_count,
        "new_diagnosis_count": manifest.new_diagnosis_count,
        "measured_result_count": manifest.measured_result_count,
        "action_authority": manifest.action_authority,
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--allow-review-pending", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(argv)
    result = verify_product_v0233_pre_execution(
        arguments.project_root,
        require_review=not arguments.allow_review_pending,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_product_v0233_pre_execution",)
