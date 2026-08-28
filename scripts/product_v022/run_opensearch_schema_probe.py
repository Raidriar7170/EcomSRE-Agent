#!/usr/bin/env python3
"""Check or execute the single Product v0.2.2 OpenSearch schema probe."""

from __future__ import annotations

import argparse
from importlib import import_module
import json
from pathlib import Path
from typing import Sequence

from ecomsre.product.connectors.opensearch_probe_v022 import (
    load_schema_probe_profile_v022,
)
from scripts.ci.verify_product_v022_history import verify_product_v022_history


def verify_opensearch_schema_probe_contract_v022(
    repository_root: Path,
    config_path: Path | None = None,
) -> dict[str, object]:
    root = Path(repository_root).resolve(strict=True)
    history = verify_product_v022_history(root)
    profile = load_schema_probe_profile_v022(
        config_path or root / "config/product-v022/opensearch-probe/profile.json"
    )
    private_root = root / profile.private_root
    start = private_root / "schema-probe-start.json"
    complete = private_root / "schema-probe-complete.json"
    if complete.exists() and not start.exists():
        raise ValueError("OpenSearch schema-probe completion lacks start sentinel")
    execution_count = int(start.exists())
    if execution_count > 1:
        raise ValueError("OpenSearch schema-probe execution count exceeds one")
    return {
        "status": "ECOMSRE_PRODUCT_V022_SCHEMA_PROBE_CONTRACT_READY",
        "history_status": history["status"],
        "campaign_id": profile.campaign_id,
        "profile_sha256": profile.profile_sha256,
        "maximum_request_count": profile.maximum_request_count,
        "maximum_sample_documents": profile.maximum_sample_documents,
        "maximum_response_bytes": profile.maximum_response_bytes,
        "execution_count": execution_count,
        "completed": complete.exists(),
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
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/product-v022/opensearch-probe/profile.json"),
    )
    parser.add_argument("--execute-live", action="store_true")
    arguments = parser.parse_args(argv)
    config = (
        arguments.config
        if arguments.config.is_absolute()
        else arguments.project_root / arguments.config
    )
    if arguments.execute_live:
        live = import_module("ecomsre.product.pilot.live_schema_probe_v022")
        result = live.run_live_schema_probe_v022(arguments.project_root, config)
    else:
        result = verify_opensearch_schema_probe_contract_v022(
            arguments.project_root,
            config,
        )
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_opensearch_schema_probe_contract_v022",)
