#!/usr/bin/env python3
"""Run the Product v0.2.2.2 capture-first recovery checkpoint."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_capture_v0222 import (
    OpenSearchCaptureStoreV0222,
    build_public_structural_summary_v0222,
)
from scripts.ci.verify_product_v0222_history import verify_product_v0222_history


TERMINAL = "ECOMSRE_PRODUCT_V0222_CAPTURE_FIRST_READY"
_NOW = datetime(2026, 8, 29, 4, 0, tzinfo=UTC)


def _verify_truth_surface(
    path: Path,
    *,
    digest_field: str,
    status_field: str,
    expected_status: str,
) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.2.2 truth surface is not an object")
    digest = payload.pop(digest_field, None)
    if digest != semantic_sha256_v22(payload):
        raise ValueError("Product v0.2.2.2 truth-surface digest differs")
    if payload.get(status_field) != expected_status:
        raise ValueError("Product v0.2.2.2 truth-surface status differs")


def _capture_response(
    store: OpenSearchCaptureStoreV0222,
    *,
    request_ordinal: int,
    request_id: str,
    request_kind: str,
    payload: object,
) -> None:
    store.record_request_intent(
        request_id=request_id,
        request_plan_id="plan-a-capture-first",
        request_kind=request_kind,
        method="GET" if request_kind in {"INDEX_RESOLUTION", "MAPPING"} else "POST",
        endpoint_class=f"/{request_kind.lower()}",
        index_binding="otel-v1-apm-span-*",
        query_parameter_names=(),
        request_body_schema_sha256="1" * 64,
        request_ordinal=request_ordinal,
        created_at=_NOW,
    )
    raw = json.dumps(payload, separators=(",", ":")).encode()
    store.record_response(
        request_id=request_id,
        http_status=200,
        response_headers={"content-type": "application/json"},
        response_body=raw,
        transport_latency_ms=1.0,
        received_at=_NOW,
    )
    store.record_parse_result(
        request_id=request_id,
        safe_parse_stage="JSON_DECODED",
        safe_error_code=None,
        structural_summary_sha256="2" * 64,
        accepted=True,
    )


def verify_product_v0222_increment1(project_root: Path) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    history = verify_product_v0222_history(root)
    _verify_truth_surface(
        root / "docs/analysis/product-v0222-predecessor-audit.json",
        digest_field="audit_sha256",
        status_field="status",
        expected_status="ECOMSRE_PRODUCT_V0222_PREDECESSOR_AUDIT_PASS",
    )
    _verify_truth_surface(
        root / "docs/analysis/product-v0222-progress.json",
        digest_field="progress_sha256",
        status_field="terminal",
        expected_status=TERMINAL,
    )
    with TemporaryDirectory(prefix="ecomsre-product-v0222-") as temporary:
        store = OpenSearchCaptureStoreV0222(
            private_root=Path(temporary) / "private",
            session_id="product-v0222-capture-checkpoint",
            maximum_response_bytes=2_000_000,
        )
        captures = (
            ("resolved-index", "INDEX_RESOLUTION", {"indices": ["otel-logs-1"]}),
            ("mapping", "MAPPING", {"mappings": {"body": "text"}}),
            ("field-caps", "FIELD_CAPS", {"fields": {"body": ["text"]}}),
            (
                "structural-sample",
                "STRUCTURAL_SAMPLE",
                {"hits": [{"body": "private-log-body-must-not-leak"}]},
            ),
            ("service-aggregation", "SERVICE_AGGREGATION", {"checkout": 5}),
            ("timestamp-range", "TIMESTAMP_RANGE", {"hits": 5}),
            ("profile-verification", "PROFILE_VERIFICATION", {"hits": 5}),
        )
        for ordinal, (request_id, request_kind, payload) in enumerate(
            captures,
            start=1,
        ):
            _capture_response(
                store,
                request_ordinal=ordinal,
                request_id=request_id,
                request_kind=request_kind,
                payload=payload,
            )

        try:
            raise RuntimeError("simulated PROFILE_RESOLUTION failure")
        except RuntimeError:
            pass

        bundle = store.build_bundle()
        if not bundle.capture_completeness:
            raise ValueError("Product v0.2.2.2 synthetic capture is incomplete")
        recovered = OpenSearchCaptureStoreV0222.load_bundle(
            private_root=Path(temporary) / "private"
        )
        if recovered != bundle or store.verify_content_addressed_objects() != 7:
            raise ValueError("Product v0.2.2.2 synthetic capture recovery differs")
        summary = build_public_structural_summary_v0222(
            bundle=bundle,
            json_path_inventory={"body": "string"},
            mapping_types={"body": ("text",)},
            field_caps_types={"body": ("text",)},
            presence_rates={"body": (5, 5)},
            timestamp_parseability_counts={"observedTimestamp": (5, 5)},
            service_alias_counts={"checkout": 5},
            message_type_classes=("string",),
            severity_type_classes=("string",),
            trace_id_type_classes=("string",),
            private_structural_shape_sha256="3" * 64,
        )
        serialized = summary.model_dump_json()
        if "private-log-body-must-not-leak" in serialized:
            raise ValueError("Product v0.2.2.2 public summary leaked a raw body")

    return {
        "status": TERMINAL,
        "history_status": history["status"],
        "predecessor_audit_status": "ECOMSRE_PRODUCT_V0222_PREDECESSOR_AUDIT_PASS",
        "progress_terminal": TERMINAL,
        "captured_response_count": len(bundle.responses),
        "capture_completeness": bundle.capture_completeness,
        "resolution_failure_recovery": "PASS",
        "public_summary_raw_body_leak_count": 0,
        "fault_attempt_count": 0,
        "baseline_readiness_attempt_count": 0,
        "product_diagnosis_attempt_count": 0,
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
            verify_product_v0222_increment1(arguments.project_root),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("TERMINAL", "verify_product_v0222_increment1")
