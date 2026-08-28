#!/usr/bin/env python3
"""Verify Product v0.2.2 schema instrumentation without live execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_schema_v022 import (
    OPENSEARCH_ERROR_STAGE_V022,
    OpenSearchSchemaErrorCodeV022,
)
from scripts.ci.verify_product_v022_history import verify_product_v022_history
from scripts.product_v022.run_opensearch_schema_probe import (
    verify_opensearch_schema_probe_contract_v022,
)


READY_TERMINAL = "ECOMSRE_PRODUCT_V022_SCHEMA_INSTRUMENTATION_READY"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.2 progress must be an object")
    return payload


def verify_product_v022_increment1(project_root: Path) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    history = verify_product_v022_history(root)
    probe = verify_opensearch_schema_probe_contract_v022(root)
    progress = _load(root / "docs/analysis/product-v022-progress.json")
    expected = {
        "schema_version": "ecomsre.product.v022.progress.v1",
        "goal_version": (
            "ecomsre-product-v022-opensearch-baseline-compatibility-v1"
        ),
        "branch": "codex/product-v022-opensearch-baseline-compatibility",
        "increment": 1,
        "terminal": READY_TERMINAL,
        "v02_terminal": "BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE",
        "v021_terminal": "BLOCKED_ECOMSRE_PRODUCT_V021_BASELINE_READINESS",
        "next_boundary": "ONE_LIVE_SCHEMA_DISCOVERY_CAMPAIGN",
        "schema_probe_execution_count": 0,
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
    if {key: progress.get(key) for key in expected} != expected:
        raise ValueError("Product v0.2.2 Increment 1 progress differs")
    digest = progress.get("progress_sha256")
    if digest != semantic_sha256_v22(
        {key: value for key, value in progress.items() if key != "progress_sha256"}
    ):
        raise ValueError("Product v0.2.2 progress digest differs")
    if set(OPENSEARCH_ERROR_STAGE_V022) != set(OpenSearchSchemaErrorCodeV022):
        raise ValueError("Product v0.2.2 OpenSearch taxonomy is incomplete")
    attempt_paths = tuple(
        sorted(
            (root / "docs/analysis").glob(
                "product-v021-baseline-readiness-attempt-*.json"
            )
        )
    )
    if tuple(path.name for path in attempt_paths) != (
        "product-v021-baseline-readiness-attempt-1.json",
        "product-v021-baseline-readiness-attempt-2.json",
    ):
        raise ValueError("Product v0.2.1 readiness attempt set differs")
    return {
        "status": READY_TERMINAL,
        "history_status": history["status"],
        "schema_probe_contract_status": probe["status"],
        "typed_error_code_count": len(OpenSearchSchemaErrorCodeV022),
        "schema_probe_execution_count": probe["execution_count"],
        "v021_readiness_attempt_count": 2,
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
            verify_product_v022_increment1(arguments.project_root),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("READY_TERMINAL", "verify_product_v022_increment1")
