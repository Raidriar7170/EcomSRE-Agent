#!/usr/bin/env python3
"""Verify the consumed Product v0.2.2 schema-probe blocker record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from scripts.ci.verify_product_v022_history import verify_product_v022_history


BLOCKED_TERMINAL = "BLOCKED_ECOMSRE_PRODUCT_V022_SCHEMA_PROBE"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must be an object")
    return payload


def _verify_digest(payload: dict[str, Any], field: str) -> None:
    supplied = payload.get(field)
    body = {key: value for key, value in payload.items() if key != field}
    if supplied != semantic_sha256_v22(body):
        raise ValueError(f"Product v0.2.2 {field} differs")


def verify_product_v022_schema_probe_blocker(
    project_root: Path,
) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    history = verify_product_v022_history(root)
    blocker = _load(root / "docs/analysis/product-v022-schema-probe-blocker.json")
    progress = _load(root / "docs/analysis/product-v022-progress.json")
    expected_blocker = {
        "schema_version": "ecomsre.product.opensearch-schema-probe-blocker.v022",
        "goal_version": "ecomsre-product-v022-opensearch-baseline-compatibility-v1",
        "branch": "codex/product-v022-opensearch-baseline-compatibility",
        "campaign_id": "product-v022-schema-discovery-1",
        "terminal": BLOCKED_TERMINAL,
        "failure_stage": "FIELD_CAPS_QUERY",
        "failure_type": "RuntimeError",
        "safe_message": "OpenSearch schema probe HTTP status 400",
        "execution_count": 1,
        "request_count": 2,
        "maximum_request_count": 12,
        "sample_count": 0,
        "baseline_unchanged": True,
        "owned_demo_cleanup": "CLEAN",
        "retry_authority": "NONE",
        "offline_changed_iteration_count": 0,
        "connector_smoke_changed_attempt_count": 0,
        "baseline_readiness_campaign_count": 0,
        "nofault_acceptance_count": 0,
        "fault_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "action_authority": "NONE",
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    if {key: blocker.get(key) for key in expected_blocker} != expected_blocker:
        raise ValueError("Product v0.2.2 schema-probe blocker differs")
    digest_fields = (
        "probe_profile_sha256",
        "private_start_report_sha256",
        "private_completion_report_sha256",
        "private_start_file_sha256",
        "private_completion_file_sha256",
        "resolved_compose_file_sha256",
        "image_lock_file_sha256",
    )
    if any(
        not isinstance(blocker.get(field), str) or len(blocker[field]) != 64
        for field in digest_fields
    ):
        raise ValueError("Product v0.2.2 blocker digest binding differs")
    _verify_digest(blocker, "report_sha256")
    expected_progress = {
        "increment": 2,
        "terminal": BLOCKED_TERMINAL,
        "next_boundary": "STOPPED_CONSUMED_SCHEMA_PROBE",
        "schema_probe_execution_count": 1,
        "offline_changed_iteration_count": 0,
        "connector_smoke_changed_attempt_count": 0,
        "baseline_readiness_campaign_count": 0,
        "infrastructure_replacement_count": 0,
        "nofault_acceptance_count": 0,
        "fault_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "action_authority": "NONE",
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "profile_status": "UNDISCOVERED",
    }
    if {key: progress.get(key) for key in expected_progress} != expected_progress:
        raise ValueError("Product v0.2.2 blocker progress differs")
    _verify_digest(progress, "progress_sha256")
    forbidden = (
        root / "docs/analysis/product-v022-opensearch-schema-fingerprint.json",
        root / "docs/analysis/product-v022-opensearch-schema-fingerprint.md",
        root / "config/product-v022/opensearch-probe/normalization-profile.json",
    )
    if any(path.exists() for path in forbidden):
        raise ValueError("Product v0.2.2 blocker cannot publish a profile")
    return {
        "status": BLOCKED_TERMINAL,
        "history_status": history["status"],
        "execution_count": blocker["execution_count"],
        "request_count": blocker["request_count"],
        "sample_count": blocker["sample_count"],
        "baseline_unchanged": blocker["baseline_unchanged"],
        "owned_demo_cleanup": blocker["owned_demo_cleanup"],
        "fault_attempt_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    arguments = parser.parse_args(argv)
    print(
        json.dumps(
            verify_product_v022_schema_probe_blocker(arguments.project_root),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("BLOCKED_TERMINAL", "verify_product_v022_schema_probe_blocker")
