#!/usr/bin/env python3
"""Verify Product v0.2.2.1 request-protocol Increment 1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_http_v0221 import (
    build_opensearch_http_error_envelope_v0221,
)
from ecomsre.product.connectors.opensearch_probe_protocol_v0221 import (
    OpenSearchProbeEndpointKindV0221,
    OpenSearchProbePlanVariantV0221,
    select_next_request_plan_variant_v0221,
)
from scripts.ci.verify_product_v0221_history import verify_product_v0221_history


READY_TERMINAL = "ECOMSRE_PRODUCT_V0221_REQUEST_PROTOCOL_READY"


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.2.1 fixture must be an object")
    return payload


def _verify_digest(payload: dict[str, Any], field: str) -> None:
    supplied = payload.get(field)
    body = {key: value for key, value in payload.items() if key != field}
    if supplied != semantic_sha256_v22(body):
        raise ValueError(f"Product v0.2.2.1 {field} differs")


def verify_product_v0221_increment1(project_root: Path) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    history = verify_product_v0221_history(root)
    audit = _load_object(
        root / "docs/analysis/product-v0221-predecessor-audit.json"
    )
    progress = _load_object(root / "docs/analysis/product-v0221-progress.json")
    _verify_digest(audit, "audit_sha256")
    _verify_digest(progress, "progress_sha256")
    progress_increment = progress.get("increment")
    if (
        audit.get("status") != "ECOMSRE_PRODUCT_V0221_PREDECESSOR_AUDIT_PASS"
        or audit.get("bound_file_count") != history["bound_file_count"]
        or audit.get("v022_execution_count") != 1
        or audit.get("v022_request_count") != 2
        or audit.get("v022_sample_count") != 0
        or audit.get("v022_retry_authority") != "NONE"
        or audit.get("baseline_unchanged") is not True
        or audit.get("owned_demo_cleanup") != "CLEAN"
        or not isinstance(progress_increment, int)
        or not 1 <= progress_increment <= 4
        or progress.get("schema_version") != "ecomsre.product.v0221.progress.v1"
        or progress.get("goal_version")
        != "ecomsre-product-v0221-opensearch-probe-protocol-v1"
        or progress.get("branch")
        != "codex/product-v0221-opensearch-probe-protocol"
        or progress.get("history_status") != history["status"]
        or progress.get("predecessor_audit_status") != audit["status"]
        or not isinstance(progress.get("live_schema_discovery_session_count"), int)
        or not 0 <= progress["live_schema_discovery_session_count"] <= 1
        or not isinstance(progress.get("changed_request_plan_count"), int)
        or not 0 <= progress["changed_request_plan_count"] <= 3
        or not isinstance(progress.get("total_read_only_opensearch_request_count"), int)
        or not 0 <= progress["total_read_only_opensearch_request_count"] <= 16
        or not isinstance(progress.get("transport_retry_count"), int)
        or not 0 <= progress["transport_retry_count"] <= 2
        or progress.get("fault_attempt_count") != 0
        or progress.get("baseline_readiness_attempt_count") != 0
        or progress.get("knowledge_loop_campaign_count") != 0
        or progress.get("agent_writes") != 0
        or progress.get("runbook_executions") != 0
        or progress.get("action_authority") != "NONE"
    ):
        raise ValueError("Product v0.2.2.1 Increment 1 audit or progress differs")
    if progress_increment == 1 and (
        progress.get("terminal") != READY_TERMINAL
        or progress.get("live_schema_discovery_session_count") != 0
        or progress.get("changed_request_plan_count") != 0
        or progress.get("total_read_only_opensearch_request_count") != 0
        or progress.get("transport_retry_count") != 0
    ):
        raise ValueError("Product v0.2.2.1 Increment 1 checkpoint differs")
    fixture = _load_object(
        root
        / "tests/fixtures/product_v0221/opensearch_field_caps_body_400.safe.json"
    )
    request = fixture.get("request")
    response = fixture.get("response")
    if (
        fixture.get("provenance")
        != "SAFE_REPRODUCTION_NOT_HISTORICAL_RAW_RESPONSE"
        or not isinstance(request, dict)
        or not isinstance(response, dict)
        or request.get("method") != "POST"
        or request.get("path_template") != "/{index}/_field_caps"
        or request.get("query_parameters") != {}
        or not isinstance(request.get("body"), dict)
        or set(request["body"]) != {"fields"}
        or response.get("status") != 400
    ):
        raise ValueError("Product v0.2.2.1 predecessor reproduction fixture differs")
    response_bytes = json.dumps(response, separators=(",", ":")).encode("utf-8")
    envelope = build_opensearch_http_error_envelope_v0221(
        http_status=400,
        response_body=response_bytes,
        method="POST",
        endpoint_kind=OpenSearchProbeEndpointKindV0221.FIELD_CAPS,
        path_template="/{index}/_field_caps",
        query_parameters={},
        request_body=request["body"],
    )
    variant = select_next_request_plan_variant_v0221(envelope)
    if variant is not OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY:
        raise ValueError("Product v0.2.2.1 next request-plan selection differs")
    return {
        "status": READY_TERMINAL,
        "history_status": history["status"],
        "predecessor_audit_status": audit["status"],
        "fixture_provenance": fixture["provenance"],
        "http_status": envelope.http_status,
        "error_type": envelope.error_type,
        "error_reason": envelope.error_reason,
        "root_cause_count": len(envelope.root_causes),
        "method": envelope.method,
        "path_template": envelope.path_template,
        "query_parameter_names": envelope.query_parameter_names,
        "request_body_schema_sha256": envelope.request_body_schema_sha256,
        "safe_error_code": envelope.safe_error_code.value,
        "next_plan_variant": variant.value,
        "fault_attempt_count": 0,
        "baseline_readiness_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority": "NONE",
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
            verify_product_v0221_increment1(arguments.project_root),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("READY_TERMINAL", "verify_product_v0221_increment1")
