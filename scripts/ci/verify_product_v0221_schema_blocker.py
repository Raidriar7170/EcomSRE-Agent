#!/usr/bin/env python3
"""Verify the consumed Product v0.2.2.1 schema-ambiguity blocker."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_probe_session_v0221 import (
    load_schema_session_profile_v0221,
)
from scripts.ci.verify_product_v0221_increment2 import (
    verify_product_v0221_increment2,
)


SCHEMA_AMBIGUOUS_BLOCKER_V0221 = (
    "BLOCKED_ECOMSRE_PRODUCT_V0221_SCHEMA_AMBIGUOUS"
)


def _load_object_v0221(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.2.1 blocker artifact must be an object")
    return payload


def _verify_digest_v0221(payload: Mapping[str, Any], field: str) -> None:
    expected = semantic_sha256_v22(
        {key: value for key, value in payload.items() if key != field}
    )
    if payload.get(field) != expected:
        raise ValueError(f"Product v0.2.2.1 blocker {field} differs")


def _sha256_file_v0221(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_product_v0221_schema_blocker(
    project_root: Path,
) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    increment2 = verify_product_v0221_increment2(root)
    profile = load_schema_session_profile_v0221(
        root / "config/product-v0221/opensearch-probe/profile.json"
    )
    session = _load_object_v0221(root / profile.schema_session_json)
    _verify_digest_v0221(session, "report_sha256")
    if (
        session.get("schema_version")
        != "ecomsre.product.opensearch-schema-session-blocker.v0221"
        or session.get("terminal") != SCHEMA_AMBIGUOUS_BLOCKER_V0221
        or session.get("session_id") != profile.session_id
        or session.get("session_consumed") is not True
        or session.get("failure_stage") != "PROFILE_RESOLUTION"
        or session.get("failure_type")
        != "OpenSearchProbeProtocolBlockerV0221"
        or session.get("safe_message")
        != "OpenSearch Mapping and samples do not prove a unique profile"
        or session.get("request_protocol_terminal") != increment2["status"]
        or session.get("live_schema_discovery_session_count") != 1
        or session.get("request_plan_count") != 1
        or session.get("changed_request_plan_count") != 0
        or session.get("total_read_only_opensearch_request_count") != 6
        or session.get("transport_retry_count") != 0
        or session.get("mapping_status")
        != "SUCCEEDED_IN_MEMORY_NOT_RETAINED"
        or session.get("field_caps_status")
        != "AVAILABLE_IN_MEMORY_NOT_RETAINED"
        or session.get("bounded_sample_status")
        != "PRESENT_IN_MEMORY_NOT_RETAINED"
        or session.get("private_raw_capture_status")
        != "NOT_RETAINED_AFTER_PROFILE_RESOLUTION_BLOCKER"
        or session.get("normalization_profile_status") != "ABSENT"
        or session.get("sanitized_fixture_status") != "ABSENT"
        or session.get("offline_parser_status") != "NOT_STARTED"
        or session.get("connector_smoke_status") != "NOT_STARTED"
        or session.get("rerun_authority") != "NONE"
        or session.get("baseline_unchanged") is not True
        or session.get("cleanup") != "CLEAN"
        or session.get("fault_attempt_count") != 0
        or session.get("baseline_readiness_attempt_count") != 0
        or session.get("knowledge_loop_campaign_count") != 0
        or session.get("action_authority") != "NONE"
        or session.get("agent_writes") != 0
        or session.get("runbook_executions") != 0
        or session.get("predecessor_prs_unchanged") != [75, 76, 77]
    ):
        raise ValueError("Product v0.2.2.1 schema blocker differs")
    absent_outputs = (
        root / profile.normalization_profile_path,
        root / profile.sanitized_fixture_path,
        root / profile.offline_parser_report_path,
        root / "docs/analysis/product-v0221-connector-smoke.json",
        root / "config/product-v0221/handoff/opensearch-compatibility.json",
    )
    if any(path.exists() for path in absent_outputs):
        raise ValueError("Product v0.2.2.1 blocked successor has later output")
    markdown = (root / profile.schema_session_markdown).read_text(encoding="utf-8")
    if (
        SCHEMA_AMBIGUOUS_BLOCKER_V0221 not in markdown
        or "rerun authority: `NONE`" not in markdown
        or str(session["report_sha256"]) not in markdown
    ):
        raise ValueError("Product v0.2.2.1 schema blocker Markdown differs")
    progress = _load_object_v0221(
        root / "docs/analysis/product-v0221-progress.json"
    )
    _verify_digest_v0221(progress, "progress_sha256")
    if (
        progress.get("increment") != 3
        or progress.get("terminal") != session["terminal"]
        or progress.get("request_protocol_terminal") != increment2["status"]
        or progress.get("schema_discovery_terminal") != session["terminal"]
        or progress.get("offline_parser_terminal") != "NOT_STARTED"
        or progress.get("next_boundary") != "STOP_BLOCKED_SCHEMA_AMBIGUOUS"
        or progress.get("schema_session_sha256") != session["report_sha256"]
        or progress.get("live_schema_discovery_session_count") != 1
        or progress.get("request_plan_count") != 1
        or progress.get("changed_request_plan_count") != 0
        or progress.get("total_read_only_opensearch_request_count") != 6
        or progress.get("transport_retry_count") != 0
        or progress.get("offline_parser_changed_iteration_count") != 0
        or progress.get("connector_smoke_changed_attempt_count") != 0
        or progress.get("normalization_profile_status") != "ABSENT"
        or progress.get("sanitized_fixture_status") != "ABSENT"
        or progress.get("baseline_unchanged") is not True
        or progress.get("owned_demo_cleanup") != "CLEAN"
        or progress.get("fault_attempt_count") != 0
        or progress.get("baseline_readiness_attempt_count") != 0
        or progress.get("knowledge_loop_campaign_count") != 0
        or progress.get("action_authority") != "NONE"
        or progress.get("agent_writes") != 0
        or progress.get("runbook_executions") != 0
        or progress.get("rerun_authority") != "NONE"
    ):
        raise ValueError("Product v0.2.2.1 blocker progress differs")
    private_root = root / profile.private_root
    private_start = private_root / "schema-session-start.json"
    private_complete = private_root / "schema-session-complete.json"
    if private_start.exists() or private_complete.exists():
        if not private_start.exists() or not private_complete.exists():
            raise ValueError("Product v0.2.2.1 private sentinels are incomplete")
        if (
            _sha256_file_v0221(private_start)
            != session["private_start_file_sha256"]
            or _sha256_file_v0221(private_complete)
            != session["private_completion_file_sha256"]
        ):
            raise ValueError("Product v0.2.2.1 private sentinel bytes differ")
        completion = _load_object_v0221(private_complete)
        _verify_digest_v0221(completion, "report_sha256")
        if (
            completion.get("terminal") != session["terminal"]
            or completion.get("request_count") != 6
            or completion.get("changed_plan_count") != 0
            or completion.get("baseline_unchanged") is not True
            or completion.get("owned_demo_cleanup") != "CLEAN"
            or completion.get("report_sha256")
            != session["private_completion_report_sha256"]
        ):
            raise ValueError("Product v0.2.2.1 private completion differs")
    return {
        "status": SCHEMA_AMBIGUOUS_BLOCKER_V0221,
        "request_protocol_terminal": increment2["status"],
        "live_schema_discovery_session_count": 1,
        "request_plan_count": 1,
        "changed_request_plan_count": 0,
        "total_read_only_opensearch_request_count": 6,
        "transport_retry_count": 0,
        "normalization_profile_status": "ABSENT",
        "offline_parser_status": "NOT_STARTED",
        "connector_smoke_status": "NOT_STARTED",
        "rerun_authority": "NONE",
        "baseline_unchanged": True,
        "owned_demo_cleanup": "CLEAN",
        "fault_attempt_count": 0,
        "baseline_readiness_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "action_authority": "NONE",
        "agent_writes": 0,
        "runbook_executions": 0,
        "schema_session_sha256": session["report_sha256"],
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
            verify_product_v0221_schema_blocker(arguments.project_root),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "SCHEMA_AMBIGUOUS_BLOCKER_V0221",
    "verify_product_v0221_schema_blocker",
)
