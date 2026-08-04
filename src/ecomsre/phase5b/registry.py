"""Fail-closed validation for non-model Phase 5B protocol registries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ecomsre.phase5b.protocol import load_protocol_object


_DIAGNOSIS_ABLATIONS = {
    "NO_COMMANDER",
    "NO_RCA_JUDGE",
    "PARALLEL_TO_SEQUENTIAL",
    "SHARED_CONTEXT",
    "NO_CONTRADICTION_CHECK",
    "FULL_CHANGE_VISIBILITY",
}
_REMEDIATION_ABLATION = "NO_INDEPENDENT_VERIFIER"


def validate_hidden_pack_contract(path: Path) -> dict[str, Any]:
    payload = load_protocol_object(path)
    expected_keys = {
        "schema_version",
        "evaluation_version",
        "default_external_pack_path",
        "agent_visible_root",
        "ground_truth_root",
        "worker_allowed_root",
        "template_count",
        "hidden_template_ids",
        "seed_ids",
        "agent_visible_layout",
        "manifest_public_fields",
        "canonical_json",
        "hash_algorithm",
        "symlinks_rejected",
        "traversal_rejected",
        "unknown_files_rejected",
        "overlapping_roots_rejected",
        "ground_truth_read_after_execution_complete_only",
    }
    if set(payload) != expected_keys:
        raise ValueError("hidden-pack contract fields are not exact")
    if payload["schema_version"] != "phase5b.hidden-pack-contract.v1":
        raise ValueError("hidden-pack contract schema version mismatch")
    if payload["evaluation_version"] != "phase5b.v1":
        raise ValueError("hidden-pack contract evaluation version mismatch")
    if payload["template_count"] != 6:
        raise ValueError("hidden-pack contract template count mismatch")
    if payload["hidden_template_ids"] != [f"hidden-{index:02d}" for index in range(1, 7)]:
        raise ValueError("hidden-pack contract opaque IDs mismatch")
    if payload["seed_ids"] != [f"seed-{index:02d}" for index in range(5)]:
        raise ValueError("hidden-pack contract seed IDs mismatch")
    if payload["agent_visible_layout"] != [
        "manifest.json",
        "incident.json",
        "metrics.json",
        "logs.json",
        "traces.json",
        "changes.json",
    ]:
        raise ValueError("hidden-pack visible layout mismatch")
    for field in (
        "canonical_json",
        "symlinks_rejected",
        "traversal_rejected",
        "unknown_files_rejected",
        "overlapping_roots_rejected",
        "ground_truth_read_after_execution_complete_only",
    ):
        if payload[field] is not True:
            raise ValueError(f"hidden-pack safety flag is not frozen: {field}")
    return payload


def validate_metrics_registry(path: Path) -> dict[str, Any]:
    payload = load_protocol_object(path)
    if payload.get("schema_version") != "phase5b.metrics-registry.v1":
        raise ValueError("metrics registry schema version mismatch")
    if payload.get("evaluation_version") != "phase5b.v1":
        raise ValueError("metrics registry evaluation version mismatch")
    if payload.get("remediation_scope") != "replay-only remediation; not production remediation":
        raise ValueError("metrics registry remediation truth boundary mismatch")
    quality = payload.get("quality_metrics")
    cost = payload.get("cost_metrics")
    if not isinstance(quality, list) or not isinstance(cost, list):
        raise ValueError("metrics registry collections are invalid")
    if len(quality) != 9 or len(cost) != 7:
        raise ValueError("metrics registry metric counts mismatch")
    return payload


def validate_ablation_registry(path: Path) -> dict[str, Any]:
    payload = load_protocol_object(path)
    if payload.get("schema_version") != "phase5b.ablation-registry.v1":
        raise ValueError("ablation registry schema version mismatch")
    if payload.get("evaluation_version") != "phase5b.v1":
        raise ValueError("ablation registry evaluation version mismatch")
    ablations = payload.get("ablations")
    diagnosis_pairs = payload.get("diagnosis_pairing_units")
    remediation_pairs = payload.get("remediation_pairing_units")
    if not isinstance(ablations, list) or not isinstance(diagnosis_pairs, list) or not isinstance(remediation_pairs, list):
        raise ValueError("ablation registry collections are invalid")
    identifiers = {item.get("ablation_id") for item in ablations if isinstance(item, dict)}
    if identifiers != _DIAGNOSIS_ABLATIONS | {_REMEDIATION_ABLATION}:
        raise ValueError("ablation registry must contain the frozen seven ablations")
    if len(diagnosis_pairs) != 6 or len(remediation_pairs) != 2:
        raise ValueError("ablation registry frozen pairing units mismatch")
    for item in ablations:
        if not isinstance(item, dict) or item.get("base_arm") != "DYNAMIC_MULTI_AGENT_V2" or item.get("primary_eligible") is not False:
            raise ValueError("ablation registry arm or primary eligibility mismatch")
        expected_count = 2 if item.get("ablation_id") == _REMEDIATION_ABLATION else 6
        if item.get("pairing_unit_count") != expected_count:
            raise ValueError("ablation pairing-unit count mismatch")
    if payload.get("diagnosis_run_count") != 36 or payload.get("remediation_run_count") != 2 or payload.get("ablation_run_count") != 38:
        raise ValueError("ablation registry run counts mismatch")
    return payload
