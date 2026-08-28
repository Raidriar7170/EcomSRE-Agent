#!/usr/bin/env python3
"""Verify Product v0.2.2.1 live schema and offline-parser checkpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_probe_protocol_v0221 import (
    OpenSearchProbeRequestAttemptV0221,
    OpenSearchProbeRequestPlanV0221,
)
from ecomsre.product.connectors.opensearch_probe_resolution_v0221 import (
    OpenSearchNormalizationProfileV0221,
)
from ecomsre.product.connectors.opensearch_probe_session_v0221 import (
    OFFLINE_PARSER_PASS_V0221,
    SCHEMA_DISCOVERY_PASS_V0221,
    OpenSearchOfflineParserReportV0221,
    OpenSearchSanitizedFixtureV0221,
    evaluate_offline_parser_v0221,
    load_schema_session_profile_v0221,
)
from scripts.ci.verify_product_v0221_increment2 import (
    verify_product_v0221_increment2,
)


def _load_object_v0221(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.2.1 artifact must be an object")
    return payload


def _verify_digest_v0221(payload: Mapping[str, Any], field: str) -> None:
    expected = semantic_sha256_v22(
        {key: value for key, value in payload.items() if key != field}
    )
    if payload.get(field) != expected:
        raise ValueError(f"Product v0.2.2.1 {field} differs")


def verify_product_v0221_increment3(project_root: Path) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    increment2 = verify_product_v0221_increment2(root)
    session_profile = load_schema_session_profile_v0221(
        root / "config/product-v0221/opensearch-probe/profile.json"
    )
    normalization_profile = OpenSearchNormalizationProfileV0221.model_validate(
        _load_object_v0221(root / session_profile.normalization_profile_path)
    )
    fixture = OpenSearchSanitizedFixtureV0221.model_validate(
        _load_object_v0221(root / session_profile.sanitized_fixture_path)
    )
    offline = OpenSearchOfflineParserReportV0221.model_validate(
        _load_object_v0221(root / session_profile.offline_parser_report_path)
    )
    expected_offline = evaluate_offline_parser_v0221(
        fixture=fixture,
        profile=normalization_profile,
        changed_iteration_count=offline.changed_iteration_count,
    )
    if offline != expected_offline or offline.terminal != OFFLINE_PARSER_PASS_V0221:
        raise ValueError("Product v0.2.2.1 offline parser replay differs")
    session = _load_object_v0221(root / session_profile.schema_session_json)
    _verify_digest_v0221(session, "report_sha256")
    plans_raw = session.get("plans")
    attempts_raw = session.get("attempts")
    if not isinstance(plans_raw, list) or not isinstance(attempts_raw, list):
        raise ValueError("Product v0.2.2.1 schema-session ledger is invalid")
    plans = tuple(OpenSearchProbeRequestPlanV0221.model_validate(item) for item in plans_raw)
    attempts = tuple(
        OpenSearchProbeRequestAttemptV0221.model_validate(item)
        for item in attempts_raw
    )
    plan_ids = tuple(plan.plan_id for plan in plans)
    if (
        not 1 <= len(plans) <= 3
        or len({plan.semantic_plan_sha256 for plan in plans}) != len(plans)
        or not 1 <= len(attempts) <= 16
        or tuple(attempt.ordinal for attempt in attempts)
        != tuple(range(1, len(attempts) + 1))
        or any(attempt.plan_id not in plan_ids for attempt in attempts)
    ):
        raise ValueError("Product v0.2.2.1 schema-session bounds differ")
    safe_errors = session.get("safe_http_errors")
    live_validation = session.get("live_sample_validation")
    if not isinstance(safe_errors, list) or not isinstance(live_validation, Mapping):
        raise ValueError("Product v0.2.2.1 safe session projection differs")
    if (
        session.get("terminal") != SCHEMA_DISCOVERY_PASS_V0221
        or session.get("session_id") != session_profile.session_id
        or session.get("session_profile_sha256") != session_profile.profile_sha256
        or session.get("live_schema_discovery_session_count") != 1
        or session.get("changed_request_plan_count") != len(plans)
        or session.get("total_read_only_opensearch_request_count") != len(attempts)
        or not isinstance(session.get("transport_retry_count"), int)
        or not 0 <= session["transport_retry_count"] <= 2
        or session.get("normalization_profile_sha256")
        != normalization_profile.profile_sha256
        or session.get("sanitized_fixture_sha256") != fixture.fixture_sha256
        or session.get("offline_parser_terminal") != offline.terminal
        or session.get("offline_parser_report_sha256") != offline.report_sha256
        or session.get("private_sample_shape_sha256")
        != fixture.private_sample_shape_sha256
        or live_validation.get("accepted_record_count", 0) < 1
        or live_validation.get("batch_status") != "SUCCESS_NONEMPTY"
        or session.get("baseline_unchanged") is not True
        or session.get("cleanup") != "CLEAN"
        or session.get("fault_attempt_count") != 0
        or session.get("baseline_readiness_attempt_count") != 0
        or session.get("knowledge_loop_campaign_count") != 0
        or session.get("action_authority") != "NONE"
        or session.get("agent_writes") != 0
        or session.get("runbook_executions") != 0
    ):
        raise ValueError("Product v0.2.2.1 schema-session acceptance differs")
    progress = _load_object_v0221(
        root / "docs/analysis/product-v0221-progress.json"
    )
    _verify_digest_v0221(progress, "progress_sha256")
    if (
        progress.get("increment") != 3
        or progress.get("terminal") != OFFLINE_PARSER_PASS_V0221
        or progress.get("request_protocol_terminal") != increment2["status"]
        or progress.get("schema_discovery_terminal") != session["terminal"]
        or progress.get("offline_parser_terminal") != offline.terminal
        or progress.get("schema_session_sha256") != session["report_sha256"]
        or progress.get("normalization_profile_sha256")
        != normalization_profile.profile_sha256
        or progress.get("sanitized_fixture_sha256") != fixture.fixture_sha256
        or progress.get("offline_parser_report_sha256") != offline.report_sha256
        or progress.get("live_schema_discovery_session_count") != 1
        or progress.get("changed_request_plan_count") != len(plans)
        or progress.get("total_read_only_opensearch_request_count") != len(attempts)
        or progress.get("offline_parser_changed_iteration_count")
        != offline.changed_iteration_count
        or progress.get("connector_smoke_changed_attempt_count") != 0
        or progress.get("next_boundary") != "INCREMENT_4_LIVE_CONNECTOR_SMOKE"
        or progress.get("baseline_unchanged") is not True
        or progress.get("cleanup") != "CLEAN"
        or progress.get("fault_attempt_count") != 0
        or progress.get("baseline_readiness_attempt_count") != 0
        or progress.get("knowledge_loop_campaign_count") != 0
        or progress.get("action_authority") != "NONE"
        or progress.get("agent_writes") != 0
        or progress.get("runbook_executions") != 0
    ):
        raise ValueError("Product v0.2.2.1 Increment 3 progress differs")
    return {
        "status": OFFLINE_PARSER_PASS_V0221,
        "schema_discovery_terminal": SCHEMA_DISCOVERY_PASS_V0221,
        "request_protocol_terminal": increment2["status"],
        "live_schema_discovery_session_count": 1,
        "changed_request_plan_count": len(plans),
        "total_read_only_opensearch_request_count": len(attempts),
        "transport_retry_count": session["transport_retry_count"],
        "offline_parser_changed_iteration_count": offline.changed_iteration_count,
        "normalization_profile_sha256": normalization_profile.profile_sha256,
        "sanitized_fixture_sha256": fixture.fixture_sha256,
        "schema_session_sha256": session["report_sha256"],
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
            verify_product_v0221_increment3(arguments.project_root),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_product_v0221_increment3",)
