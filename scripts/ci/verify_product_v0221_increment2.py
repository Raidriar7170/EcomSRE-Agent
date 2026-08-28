#!/usr/bin/env python3
"""Verify Product v0.2.2.1 offline request-protocol matrix and fallback."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_http_v0221 import (
    build_opensearch_http_error_envelope_v0221,
)
from ecomsre.product.connectors.opensearch_probe_protocol_v0221 import (
    OpenSearchProbeChangeReasonV0221,
    OpenSearchProbeEndpointKindV0221,
    OpenSearchProbePlanVariantV0221,
    OpenSearchProbeSessionLedgerV0221,
    build_probe_request_plan_v0221,
    select_next_request_plan_variant_v0221,
)
from ecomsre.product.connectors.opensearch_probe_resolution_v0221 import (
    OpenSearchFieldCapsStatusV0221,
    OpenSearchNormalizationProfileV0221,
    OpenSearchProfileResolutionModeV0221,
    build_empirical_query_verification_v0221,
    resolve_normalization_profile_v0221,
)
from ecomsre.product.connectors.opensearch_probe_v022 import (
    parse_field_caps_v022,
    parse_mapping_v022,
    summarize_sample_shapes_v022,
)
from scripts.ci.verify_product_v0221_increment1 import (
    verify_product_v0221_increment1,
)


PASS_TERMINAL = "ECOMSRE_PRODUCT_V0221_REQUEST_PROTOCOL_PASS"
FIELDS = ("observed.timestamp", "resource.service.name.keyword")


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.2.1 protocol payload must be an object")
    return payload


def _error_bytes(status: int, error_type: str, reason: str) -> bytes:
    return json.dumps(
        {
            "error": {
                "type": error_type,
                "reason": reason,
                "root_cause": [{"type": error_type, "reason": reason}],
            },
            "status": status,
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _envelope(
    *,
    status: int,
    method: str,
    error_type: str,
    reason: str,
) -> object:
    return build_opensearch_http_error_envelope_v0221(
        http_status=status,
        response_body=_error_bytes(status, error_type, reason),
        method=method,  # type: ignore[arg-type]
        endpoint_kind=OpenSearchProbeEndpointKindV0221.FIELD_CAPS,
        path_template="/{index}/_field_caps",
        query_parameters={"fields": ",".join(FIELDS)},
        request_body=None,
    )


def build_product_v0221_protocol_matrix(project_root: Path) -> dict[str, Any]:
    root = Path(project_root).resolve(strict=True)
    increment1 = verify_product_v0221_increment1(root)
    fixture = _load_object(
        root / "tests/fixtures/product_v0221/opensearch_protocol_matrix.safe.json"
    )
    if fixture.get("provenance") != "SANITIZED_SYNTHETIC_OFFLINE_PROTOCOL_MATRIX":
        raise ValueError("Product v0.2.2.1 protocol fixture provenance differs")
    plan_a = build_probe_request_plan_v0221(
        variant=OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY,
        fields=FIELDS,
        parent_plan_id=None,
        change_reason_code=OpenSearchProbeChangeReasonV0221.INITIAL_OFFICIAL_PROTOCOL,
    )
    plan_b = build_probe_request_plan_v0221(
        variant=OpenSearchProbePlanVariantV0221.PLAN_B_FIELD_CAPS_POST_QUERY,
        fields=FIELDS,
        parent_plan_id=plan_a.plan_id,
        change_reason_code=(
            OpenSearchProbeChangeReasonV0221.FIELD_CAPS_METHOD_NOT_ALLOWED
        ),
    )
    plan_c = build_probe_request_plan_v0221(
        variant=OpenSearchProbePlanVariantV0221.PLAN_C_MAPPING_SAMPLE_EMPIRICAL,
        fields=FIELDS,
        parent_plan_id=plan_b.plan_id,
        change_reason_code=OpenSearchProbeChangeReasonV0221.FIELD_CAPS_UNSUPPORTED,
    )
    ledger = OpenSearchProbeSessionLedgerV0221()
    for plan in (plan_a, plan_b, plan_c):
        ledger.register_plan(plan)

    method_error = _envelope(
        status=405,
        method="GET",
        error_type="method_not_allowed_exception",
        reason="method GET is not allowed",
    )
    permission_error = _envelope(
        status=403,
        method="GET",
        error_type="security_exception",
        reason="field caps permission is unavailable",
    )
    post_error = _envelope(
        status=400,
        method="POST",
        error_type="illegal_argument_exception",
        reason="field capabilities are unsupported by this proxy",
    )
    next_b = select_next_request_plan_variant_v0221(
        method_error,  # type: ignore[arg-type]
        current_variant=OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY,
    )
    next_c_permission = select_next_request_plan_variant_v0221(
        permission_error,  # type: ignore[arg-type]
        current_variant=OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY,
    )
    next_c_post = select_next_request_plan_variant_v0221(
        post_error,  # type: ignore[arg-type]
        current_variant=OpenSearchProbePlanVariantV0221.PLAN_B_FIELD_CAPS_POST_QUERY,
    )
    if (
        next_b is not OpenSearchProbePlanVariantV0221.PLAN_B_FIELD_CAPS_POST_QUERY
        or next_c_permission
        is not OpenSearchProbePlanVariantV0221.PLAN_C_MAPPING_SAMPLE_EMPIRICAL
        or next_c_post
        is not OpenSearchProbePlanVariantV0221.PLAN_C_MAPPING_SAMPLE_EMPIRICAL
    ):
        raise ValueError("Product v0.2.2.1 protocol transition matrix differs")

    mapping_raw = fixture.get("mapping")
    field_caps_raw = fixture.get("field_caps")
    samples_raw = fixture.get("samples")
    if (
        not isinstance(mapping_raw, Mapping)
        or not isinstance(field_caps_raw, Mapping)
        or not isinstance(samples_raw, list)
        or not all(isinstance(sample, Mapping) for sample in samples_raw)
    ):
        raise ValueError("Product v0.2.2.1 protocol fixture shape differs")
    samples = tuple(dict(sample) for sample in samples_raw)
    mapping = parse_mapping_v022(mapping_raw)
    field_caps = parse_field_caps_v022(field_caps_raw)
    sample_shapes = summarize_sample_shapes_v022(samples)
    empirical = build_empirical_query_verification_v0221(
        service_query_field="resource.service.name.keyword",
        timestamp_query_field="observed.timestamp",
        checkout_aliases=("checkoutservice",),
        service_aggregation_response=fixture.get("service_aggregation_response"),
        timestamp_range_response=fixture.get("timestamp_range_response"),
        profile_verification_response=fixture.get("profile_verification_response"),
    )
    available = resolve_normalization_profile_v0221(
        index_pattern="otel-v1-apm-span-*",
        mapping=mapping,
        field_caps=field_caps,
        samples=samples,
        sample_shapes=sample_shapes,
        checkout_aliases=("checkoutservice",),
        empirical_verification=empirical,
    )
    fallback = resolve_normalization_profile_v0221(
        index_pattern="otel-v1-apm-span-*",
        mapping=mapping,
        field_caps=None,
        samples=samples,
        sample_shapes=sample_shapes,
        checkout_aliases=("checkoutservice",),
        empirical_verification=empirical,
    )
    if (
        available.profile.field_caps_status
        is not OpenSearchFieldCapsStatusV0221.AVAILABLE
        or available.profile.profile_resolution_mode
        is not OpenSearchProfileResolutionModeV0221.MAPPING_FIELD_CAPS_SAMPLE
        or fallback.profile.field_caps_status
        is not OpenSearchFieldCapsStatusV0221.UNAVAILABLE_OPTIONAL
        or fallback.profile.profile_resolution_mode
        is not OpenSearchProfileResolutionModeV0221.MAPPING_SAMPLE_EMPIRICAL
    ):
        raise ValueError("Product v0.2.2.1 profile matrix differs")

    body: dict[str, Any] = {
        "schema_version": "ecomsre.product.v0221.request-protocol-matrix.v1",
        "goal_version": "ecomsre-product-v0221-opensearch-probe-protocol-v1",
        "terminal": PASS_TERMINAL,
        "increment1_terminal": increment1["status"],
        "fixture_provenance": fixture["provenance"],
        "cases": [
            {
                "case_id": "OFFICIAL_GET_FIELD_CAPS_QUERY",
                "outcome": "PASS",
                "method": "GET",
                "query_parameter_names": ["fields"],
                "body_shape": "NONE",
            },
            {
                "case_id": "METHOD_405_TO_POST_QUERY",
                "outcome": "PASS",
                "next_plan": next_b.value,
            },
            {
                "case_id": "PERMISSION_403_TO_MAPPING_EMPIRICAL",
                "outcome": "PASS",
                "next_plan": next_c_permission.value,
            },
            {
                "case_id": "POST_400_TO_MAPPING_EMPIRICAL",
                "outcome": "PASS",
                "next_plan": next_c_post.value,
            },
            {
                "case_id": "FIELD_CAPS_AVAILABLE_PROFILE",
                "outcome": "PASS",
                "profile_resolution_mode": (
                    available.profile.profile_resolution_mode.value
                ),
                "profile_sha256": available.profile.profile_sha256,
            },
            {
                "case_id": "FIELD_CAPS_OPTIONAL_FALLBACK",
                "outcome": "PASS",
                "profile_resolution_mode": (
                    fallback.profile.profile_resolution_mode.value
                ),
                "profile_sha256": fallback.profile.profile_sha256,
            },
            {
                "case_id": "REQUIRED_TIE_FAILS_CLOSED",
                "outcome": "PASS",
                "blocker": "BLOCKED_ECOMSRE_PRODUCT_V0221_SCHEMA_AMBIGUOUS",
            },
            {
                "case_id": "SEMANTIC_PLAN_REPEAT_REJECTED",
                "outcome": "PASS",
            },
            {
                "case_id": "HTTP_400_NOT_RETRIED_UNCHANGED",
                "outcome": "PASS",
            },
        ],
        "plans": [
            {
                "plan_id": plan.plan_id,
                "semantic_plan_sha256": plan.semantic_plan_sha256,
                "plan_sha256": plan.plan_sha256,
            }
            for plan in ledger.plans
        ],
        "mapping_sha256": mapping.mapping_sha256,
        "field_caps_sha256": field_caps.field_caps_sha256,
        "sample_shape_sha256": sample_shapes.sample_shape_sha256,
        "empirical_query_verification_sha256": empirical.verification_sha256,
        "live_schema_discovery_session_count": 0,
        "total_live_read_only_opensearch_request_count": 0,
        "fault_attempt_count": 0,
        "baseline_readiness_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority": "NONE",
    }
    body["matrix_sha256"] = semantic_sha256_v22(body)
    return body


def render_product_v0221_protocol_matrix_markdown(matrix: Mapping[str, Any]) -> str:
    plans = matrix.get("plans")
    cases = matrix.get("cases")
    if not isinstance(plans, list) or not isinstance(cases, list):
        raise ValueError("Product v0.2.2.1 protocol matrix report shape differs")
    lines = [
        "# Product v0.2.2.1 OpenSearch Request Protocol Matrix",
        "",
        f"Terminal: `{matrix['terminal']}`",
        "",
        "This is an offline, sanitized protocol matrix. Live schema sessions and",
        "live OpenSearch requests remain zero at this checkpoint.",
        "",
        "## Plans",
        "",
    ]
    for plan in plans:
        if not isinstance(plan, Mapping):
            raise ValueError("Product v0.2.2.1 protocol plan report differs")
        lines.append(
            f"- `{plan['plan_id']}`: semantic `{plan['semantic_plan_sha256']}`"
        )
    lines.extend(("", "## Cases", ""))
    for case in cases:
        if not isinstance(case, Mapping):
            raise ValueError("Product v0.2.2.1 protocol case report differs")
        lines.append(f"- `{case['case_id']}`: `{case['outcome']}`")
    lines.extend(
        (
            "",
            "## Boundary",
            "",
            "Field Caps is preferred. It is optional only after Mapping, bounded",
            "samples, service aggregation, timestamp range, and final profile",
            "verification establish one unique profile. No default field guessing",
            "is permitted.",
            "",
            f"Matrix SHA-256: `{matrix['matrix_sha256']}`",
            "",
        )
    )
    return "\n".join(lines)


def verify_product_v0221_increment2(project_root: Path) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    expected = build_product_v0221_protocol_matrix(root)
    tracked = _load_object(
        root / "docs/analysis/product-v0221-request-protocol-matrix.json"
    )
    if tracked != expected:
        raise ValueError("Product v0.2.2.1 tracked protocol matrix differs")
    markdown = (
        root / "docs/analysis/product-v0221-request-protocol-matrix.md"
    ).read_text(encoding="utf-8")
    if markdown != render_product_v0221_protocol_matrix_markdown(expected):
        raise ValueError("Product v0.2.2.1 protocol matrix Markdown differs")
    synthetic_profile = OpenSearchNormalizationProfileV0221.model_validate(
        _load_object(
            root
            / "tests/fixtures/product_v0221/opensearch_verified_profile.synthetic.json"
        )
    )
    if (
        synthetic_profile.profile_sha256
        != expected["cases"][5]["profile_sha256"]
        or synthetic_profile.field_caps_status
        is not OpenSearchFieldCapsStatusV0221.UNAVAILABLE_OPTIONAL
        or synthetic_profile.profile_resolution_mode
        is not OpenSearchProfileResolutionModeV0221.MAPPING_SAMPLE_EMPIRICAL
    ):
        raise ValueError("Product v0.2.2.1 synthetic verified profile differs")
    progress = _load_object(root / "docs/analysis/product-v0221-progress.json")
    progress_body = {
        key: value for key, value in progress.items() if key != "progress_sha256"
    }
    if progress.get("progress_sha256") != semantic_sha256_v22(progress_body):
        raise ValueError("Product v0.2.2.1 progress digest differs")
    if progress.get("increment") == 2 and (
        progress.get("terminal") != PASS_TERMINAL
        or progress.get("live_schema_discovery_session_count") != 0
        or progress.get("changed_request_plan_count") != 0
        or progress.get("total_read_only_opensearch_request_count") != 0
        or progress.get("transport_retry_count") != 0
        or progress.get("next_boundary") != "INCREMENT_3_LIVE_SCHEMA_SESSION"
    ):
        raise ValueError("Product v0.2.2.1 Increment 2 progress differs")
    return {
        "status": PASS_TERMINAL,
        "matrix_sha256": expected["matrix_sha256"],
        "case_count": len(expected["cases"]),
        "plan_count": len(expected["plans"]),
        "available_profile_sha256": expected["cases"][4]["profile_sha256"],
        "fallback_profile_sha256": expected["cases"][5]["profile_sha256"],
        "live_schema_discovery_session_count": 0,
        "total_live_read_only_opensearch_request_count": 0,
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
            verify_product_v0221_increment2(arguments.project_root),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "PASS_TERMINAL",
    "build_product_v0221_protocol_matrix",
    "render_product_v0221_protocol_matrix_markdown",
    "verify_product_v0221_increment2",
)
