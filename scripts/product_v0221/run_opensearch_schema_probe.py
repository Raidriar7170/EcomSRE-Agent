#!/usr/bin/env python3
"""Check or execute the Product v0.2.2.1 OpenSearch schema session."""

from __future__ import annotations

import argparse
from importlib import import_module
import json
from pathlib import Path
from typing import Any, Sequence

from ecomsre.product.connectors.opensearch_probe_session_v0221 import (
    load_schema_session_profile_v0221,
)
from scripts.ci.verify_product_v0221_increment2 import (
    verify_product_v0221_increment2,
)


SESSION_READY_V0221 = "ECOMSRE_PRODUCT_V0221_SCHEMA_SESSION_READY"
REQUEST_PROTOCOL_BLOCKED_V0221 = (
    "BLOCKED_ECOMSRE_PRODUCT_V0221_REQUEST_PROTOCOL"
)


def _load_object_v0221(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.2.1 session artifact must be an object")
    return payload


def verify_schema_session_contract_v0221(
    repository_root: Path,
    config_path: Path | None = None,
) -> dict[str, object]:
    root = Path(repository_root).resolve(strict=True)
    increment2 = verify_product_v0221_increment2(root)
    profile = load_schema_session_profile_v0221(
        config_path
        or root / "config/product-v0221/opensearch-probe/profile.json"
    )
    private_root = root / profile.private_root
    start = private_root / "schema-session-start.json"
    complete = private_root / "schema-session-complete.json"
    if complete.exists() and not start.exists():
        raise ValueError("OpenSearch schema-session completion lacks start sentinel")
    execution_count = int(start.exists())
    if execution_count > 1:
        raise ValueError("OpenSearch schema-session execution count exceeds one")
    public_session_path = root / profile.schema_session_json
    if public_session_path.exists():
        public_session = _load_object_v0221(public_session_path)
        if public_session.get("terminal") == (
            "BLOCKED_ECOMSRE_PRODUCT_V0221_SCHEMA_AMBIGUOUS"
        ):
            completion_sha256 = public_session[
                "private_completion_report_sha256"
            ]
            if complete.exists():
                completion_sha256 = _load_object_v0221(complete)[
                    "report_sha256"
                ]
            return {
                "status": public_session["terminal"],
                "request_protocol_terminal": increment2["status"],
                "session_id": profile.session_id,
                "profile_sha256": profile.profile_sha256,
                "live_schema_discovery_session_count": 1,
                "request_plan_count": public_session["request_plan_count"],
                "changed_request_plan_count": public_session[
                    "changed_request_plan_count"
                ],
                "total_read_only_opensearch_request_count": public_session[
                    "total_read_only_opensearch_request_count"
                ],
                "transport_retry_count": public_session[
                    "transport_retry_count"
                ],
                "offline_parser_changed_iteration_count": 0,
                "completed": True,
                "completion_sha256": completion_sha256,
                "baseline_unchanged": public_session["baseline_unchanged"],
                "owned_demo_cleanup": public_session["cleanup"],
                "rerun_authority": "NONE",
                "fault_attempt_count": 0,
                "baseline_readiness_attempt_count": 0,
                "knowledge_loop_campaign_count": 0,
                "action_authority": "NONE",
                "agent_writes": 0,
                "runbook_executions": 0,
            }
    if complete.exists():
        completion = _load_object_v0221(complete)
        session = _load_object_v0221(root / profile.schema_session_json)
        offline = _load_object_v0221(root / profile.offline_parser_report_path)
        return {
            "status": offline["terminal"],
            "schema_discovery_terminal": session["terminal"],
            "request_protocol_terminal": increment2["status"],
            "session_id": profile.session_id,
            "profile_sha256": profile.profile_sha256,
            "live_schema_discovery_session_count": execution_count,
            "changed_request_plan_count": session["changed_request_plan_count"],
            "total_read_only_opensearch_request_count": session[
                "total_read_only_opensearch_request_count"
            ],
            "transport_retry_count": session["transport_retry_count"],
            "offline_parser_changed_iteration_count": offline[
                "changed_iteration_count"
            ],
            "completed": True,
            "completion_sha256": completion["report_sha256"],
            "fault_attempt_count": 0,
            "baseline_readiness_attempt_count": 0,
            "knowledge_loop_campaign_count": 0,
            "action_authority": "NONE",
            "agent_writes": 0,
            "runbook_executions": 0,
        }
    status = SESSION_READY_V0221
    if start.exists():
        status = REQUEST_PROTOCOL_BLOCKED_V0221
    return {
        "status": status,
        "request_protocol_terminal": increment2["status"],
        "session_id": profile.session_id,
        "profile_sha256": profile.profile_sha256,
        "live_schema_discovery_session_count": execution_count,
        "changed_request_plan_count": 0,
        "total_read_only_opensearch_request_count": 0,
        "transport_retry_count": 0,
        "offline_parser_changed_iteration_count": 0,
        "completed": False,
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
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/product-v0221/opensearch-probe/profile.json"),
    )
    parser.add_argument("--execute-live", action="store_true")
    arguments = parser.parse_args(argv)
    config = (
        arguments.config
        if arguments.config.is_absolute()
        else arguments.project_root / arguments.config
    )
    current = verify_schema_session_contract_v0221(
        arguments.project_root,
        config,
    )
    if arguments.execute_live and current["live_schema_discovery_session_count"] == 0:
        live = import_module("ecomsre.product.pilot.live_schema_probe_v0221")
        result = live.run_live_schema_probe_v0221(arguments.project_root, config)
    else:
        result = current
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "REQUEST_PROTOCOL_BLOCKED_V0221",
    "SESSION_READY_V0221",
    "verify_schema_session_contract_v0221",
)
