#!/usr/bin/env python3
"""Fail-closed offline preflight for the one Product v0.2.2.1 live session."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Sequence

from ecomsre.product.connectors.opensearch_probe_session_v0221 import (
    load_schema_session_profile_v0221,
)
from ecomsre.product.pilot.live_schema_probe_v0221 import PINNED_UPSTREAM_V0221
from scripts.product_v0221.run_opensearch_schema_probe import (
    SESSION_READY_V0221,
    verify_schema_session_contract_v0221,
)


PREFLIGHT_READY_V0221 = "ECOMSRE_PRODUCT_V0221_LIVE_PREFLIGHT_READY"


def verify_product_v0221_live_preflight(
    project_root: Path,
) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    profile_path = root / "config/product-v0221/opensearch-probe/profile.json"
    profile = load_schema_session_profile_v0221(profile_path)
    contract = verify_schema_session_contract_v0221(root, profile_path)
    upstream = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=root / "third_party/opentelemetry-demo",
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    private_root = root / profile.private_root
    protected_absent = (
        private_root / "schema-session-start.json",
        private_root / "schema-session-complete.json",
        root / profile.schema_session_json,
        root / profile.schema_session_markdown,
        root / profile.normalization_profile_path,
        root / profile.sanitized_fixture_path,
        root / profile.offline_parser_report_path,
    )
    if (
        contract.get("status") != SESSION_READY_V0221
        or contract.get("live_schema_discovery_session_count") != 0
        or contract.get("total_read_only_opensearch_request_count") != 0
        or upstream != PINNED_UPSTREAM_V0221
        or any(path.exists() for path in protected_absent)
    ):
        raise ValueError("Product v0.2.2.1 live preflight differs")
    return {
        "status": PREFLIGHT_READY_V0221,
        "request_protocol_terminal": contract["request_protocol_terminal"],
        "session_id": profile.session_id,
        "session_profile_sha256": profile.profile_sha256,
        "upstream_commit": upstream,
        "live_schema_discovery_session_count": 0,
        "changed_request_plan_count": 0,
        "total_read_only_opensearch_request_count": 0,
        "transport_retry_count": 0,
        "fault_attempt_count": 0,
        "baseline_readiness_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "action_authority": "NONE",
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
            verify_product_v0221_live_preflight(arguments.project_root),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "PREFLIGHT_READY_V0221",
    "verify_product_v0221_live_preflight",
)
