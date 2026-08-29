#!/usr/bin/env python3
"""Verify the Product v0.2.3 ACTIVE-profile Connector checkpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    PROFILE_BINDING_PASS_V023,
    OpenSearchConnectorDiagnosticsV023,
    build_profile_bound_opensearch_config_v023,
    build_product_v023_environment_payload,
    load_product_v023_profile_binding,
)
from ecomsre.product.contracts import (
    ConnectorConfigV1,
    ConnectorKindV1,
    OpenSearchConnectorSettingsV1,
)
from scripts.ci.verify_product_v023_history import verify_product_v023_history


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.3 Increment 1 artifact must be an object")
    return payload


def verify_product_v023_increment1(root: Path) -> dict[str, object]:
    repository = Path(root).resolve(strict=True)
    history = verify_product_v023_history(repository)
    binding = load_product_v023_profile_binding(
        active_profile_path=(
            repository
            / "config/product-v0222/opensearch/normalization-profile.json"
        ),
        handoff_path=(
            repository / "docs/analysis/product-v0222-baseline-handoff.json"
        ),
    )
    profile_config = build_profile_bound_opensearch_config_v023(
        active_profile_path=(
            repository
            / "config/product-v0222/opensearch/normalization-profile.json"
        ),
        handoff_path=(
            repository / "docs/analysis/product-v0222-baseline-handoff.json"
        ),
        endpoint="http://127.0.0.1:19200",
        name="opensearch",
    )
    legacy_config = ConnectorConfigV1(
        name="legacy-opensearch",
        kind=ConnectorKindV1.OPENSEARCH,
        endpoint="http://127.0.0.1:19200",
        settings={
            "index_pattern": "legacy-logs-*",
            "timestamp_field": "observedTimestamp",
            "service_field": "resource.service.name",
            "service_query_field": "resource.service.name.keyword",
            "severity_field": "severity.text",
            "message_field": "body",
            "trace_id_field": "trace_id",
        },
    )
    legacy_settings = OpenSearchConnectorSettingsV1.model_validate(
        legacy_config.settings
    )
    environment = build_product_v023_environment_payload(
        repository_root=repository,
        runtime_authority_sha256="1" * 64,
    )
    opensearch_configs = tuple(
        item
        for item in environment["connector_configs"]
        if isinstance(item, dict) and item.get("kind") == "OPENSEARCH"
    )
    profile_settings = OpenSearchConnectorSettingsV1.model_validate(
        opensearch_configs[0]["settings"] if len(opensearch_configs) == 1 else {}
    )
    profile_snapshot = profile_settings.profile_binding
    diagnostics = OpenSearchConnectorDiagnosticsV023.build(binding)
    report = _load_object(
        repository / "docs/analysis/product-v023-profile-binding.json"
    )
    report_body = {
        key: value for key, value in report.items() if key != "report_sha256"
    }
    expected_report = {
        "terminal": PROFILE_BINDING_PASS_V023,
        "history_status": history["status"],
        "settings_mode": "PROFILE_BOUND",
        "legacy_settings_mode": "LEGACY_EXPLICIT_FIELDS",
        "active_profile_sha256": binding.profile_sha256,
        "profile_binding_sha256": binding.binding_sha256,
        "candidate_set_sha256": binding.candidate_set_sha256,
        "operator_decision_sha256": binding.operator_decision_sha256,
        "baseline_handoff_sha256": binding.baseline_handoff_sha256,
        "selected_candidate_alias": binding.selected_candidate_alias,
        "index_pattern": binding.index_pattern,
        "timestamp_query_field": binding.timestamp_query_field,
        "service_source_field": binding.service_source_field,
        "service_query_field": binding.service_query_field,
        "severity_field": binding.severity_field,
        "message_field": binding.message_field,
        "trace_id_field": binding.trace_id_field,
        "maximum_record_rejection_fraction": (
            binding.maximum_record_rejection_fraction
        ),
        "connector_config_sha256": semantic_sha256_v22(
            profile_config.model_dump(mode="json")
        ),
        "connector_diagnostics_sha256": diagnostics.diagnostics_sha256,
        "profile_snapshot_persisted_in_environment": True,
        "repository_path_required_after_environment_creation": False,
        "baseline_readiness_attempt_count": 0,
        "product_diagnosis_attempt_count": 0,
        "fault_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority": "NONE",
    }
    if (
        report.get("schema_version")
        != "ecomsre.product.v023.profile-binding-report.v1"
        or report.get("goal_version")
        != "ecomsre-product-v023-fresh-baseline-nofault-v1"
        or report.get("starting_main") != history["starting_main"]
        or any(report.get(key) != value for key, value in expected_report.items())
        or report.get("report_sha256") != semantic_sha256_v22(report_body)
        or "mode" in legacy_config.settings
        or legacy_settings.mode.value != "LEGACY_EXPLICIT_FIELDS"
        or len(opensearch_configs) != 1
        or profile_snapshot is None
        or profile_snapshot.profile_sha256 != binding.profile_sha256
    ):
        raise ValueError("Product v0.2.3 Increment 1 profile binding differs")
    return {
        "status": PROFILE_BINDING_PASS_V023,
        "history_status": history["status"],
        "settings_modes": ("LEGACY_EXPLICIT_FIELDS", "PROFILE_BOUND"),
        "active_profile_sha256": binding.profile_sha256,
        "profile_binding_sha256": binding.binding_sha256,
        "selected_candidate_alias": binding.selected_candidate_alias,
        "timestamp_query_field": binding.timestamp_query_field,
        "severity_field": binding.severity_field,
        "trace_id_field": binding.trace_id_field,
        "profile_snapshot_persisted_in_environment": True,
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
            verify_product_v023_increment1(arguments.project_root),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_product_v023_increment1",)
