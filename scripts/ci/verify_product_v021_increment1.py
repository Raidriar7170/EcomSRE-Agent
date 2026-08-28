#!/usr/bin/env python3
"""Verify the Product v0.2.1 offline baseline-audit increment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from scripts.ci.verify_product_v021_history import verify_product_v021_history


TERMINAL = "ECOMSRE_PRODUCT_V021_BASELINE_AUDIT_READY"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"v0.2.1 Increment 1 artifact is not an object: {path}")
    return payload


def _semantic_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def verify_product_v021_increment1(project_root: Path) -> dict[str, object]:
    root = project_root.resolve(strict=True)
    history = verify_product_v021_history(root)
    profile = _load(root / "config/product-v021/baseline-readiness/profile.json")
    supplied_profile_sha = profile.pop("profile_sha256", None)
    if supplied_profile_sha != _semantic_sha256(profile):
        raise ValueError("v0.2.1 readiness profile digest differs")
    policy = profile.get("build_policy")
    traffic = profile.get("healthy_traffic_profile")
    if not isinstance(policy, dict) or not isinstance(traffic, dict):
        raise ValueError("v0.2.1 readiness profile shape differs")
    if (
        profile.get("candidate_services") != ["checkout"]
        or policy.get("mode") != "DEMO_ONLY"
        or policy.get("lookback_seconds") != 180
        or policy.get("window_count") != 5
        or policy.get("minimum_successful_windows") != 4
        or policy.get("warmup_seconds") != 180
        or profile.get("stabilization_seconds") != 60
        or profile.get("baseline_accumulation_seconds") != 360
        or profile.get("maximum_changed_attempts") != 2
        or traffic.get("request_seed") != 501
        or traffic.get("maximum_request_count") != 180
        or traffic.get("requests_per_second") != 0.5
        or traffic.get("error_budget") != 12
        or profile.get("public_root")
        != ".local/product-v021/baseline-readiness"
        or profile.get("private_root")
        != ".local/product-v021/private-baseline-readiness"
    ):
        raise ValueError("v0.2.1 readiness profile boundary differs")

    campaign = _load(root / "config/product-v021/live-pilot/campaign.json")
    negatives = _load(root / "config/product-v021/live-pilot/negative-controls.json")
    queue_profile = _load(root / "config/product-v021/live-pilot/profile.json")
    expected_episode_roles = {
        "N0": "LIVE_NO_FAULT_NEGATIVE",
        "P1": "FIT_POSITIVE",
        "P2": "FIT_POSITIVE",
        "P3": "SHADOW_POSITIVE",
        "H1": "FINAL_HELDOUT_RECURRENCE",
    }
    if (
        campaign.get("accepted_schedule") != ["N0", "P1", "P2", "P3"]
        or campaign.get("heldout_schedule") != ["H1"]
        or campaign.get("episode_roles") != expected_episode_roles
        or campaign.get("maximum_infrastructure_replacements_per_episode") != 1
        or campaign.get("maximum_changed_calibration_iterations") != 2
        or campaign.get("positive_episode_count") != 3
        or campaign.get("live_no_fault_negative_count") != 1
        or campaign.get("heldout_recurrence_maximum") != 1
        or campaign.get("human_checkpoint_a") != "UNFULFILLED"
        or campaign.get("human_checkpoint_b") != "UNFULFILLED"
        or campaign.get("private_root")
        != ".local/product-v021/private-live-control"
        or campaign.get("product_data_root") != ".local/product-v021/live-pilot"
        or campaign.get("action_authority") != "NONE"
        or campaign.get("runbook_authority") != "NONE"
    ):
        raise ValueError("v0.2.1 live campaign boundary differs")
    if (
        queue_profile.get("profile_name") != "CHECKOUT_KAFKA_QUEUE_OVERLOAD"
        or queue_profile.get("candidate_values") != [5, 10, 20]
        or queue_profile.get("expected_default_value") != 0
        or queue_profile.get("baseline_binding_required") is not True
        or queue_profile.get("maximum_calibration_changes") != 2
        or any(
            queue_profile.get(field) is not None
            for field in (
                "selected_value",
                "selected_root_service",
                "calibration_report_sha256",
                "calibration_contract_sha256",
                "calibration_runtime_binding_sha256",
                "calibrated_at",
                "profile_sha256",
            )
        )
    ):
        raise ValueError("v0.2.1 queue profile must remain uncalibrated")
    if (
        negatives.get("live_no_fault") != "N0"
        or negatives.get("known_core_negative", {}).get("fallback")
        != "VALIDATED_REAL_CAPTURE_NEGATIVE"
        or negatives.get("fit_strata")
        != [
            "LIVE_NO_FAULT",
            "KNOWN_CORE_NEGATIVE",
            "SAME_DOMAIN_REPLAY_CONTROL",
        ]
        or negatives.get("shadow_strata")
        != [
            "ADDITIONAL_NO_INCIDENT",
            "CONFUSABLE_CORE_KNOWN",
            "SOURCE_FAILURE",
            "MOVED_TARGET_COUNTERFACTUAL",
        ]
    ):
        raise ValueError("v0.2.1 negative-control boundary differs")

    predecessor_audit = _load(
        root / "docs/analysis/product-v021-predecessor-baseline-audit.json"
    )
    inferences = predecessor_audit.get("tracked_code_path_inferences")
    if (
        not isinstance(inferences, list)
        or len(inferences) != 1
        or inferences[0].get("classification") != "TRACKED_CODE_PATH_INFERENCE"
        or inferences[0].get("measured_predecessor_cause") is not False
        or predecessor_audit.get("tracked_artifact_facts", {}).get("fault_attempt_count")
        != 0
    ):
        raise ValueError("v0.2.1 predecessor audit claim boundary differs")

    progress = _load(root / "docs/analysis/product-v021-progress.json")
    if (
        progress.get("terminal") != TERMINAL
        or progress.get("increment") != 1
        or progress.get("baseline_readiness_attempt_count") != 0
        or progress.get("profile_calibration_iteration_count") != 0
        or progress.get("fault_attempt_count") != 0
        or progress.get("accepted_positive_episode_count") != 0
        or progress.get("heldout_recurrence_count") != 0
        or progress.get("current_human_gate") != "NOT_REACHED"
        or progress.get("action_authority") != "NONE"
        or progress.get("agent_writes") != 0
        or progress.get("runbook_executions") != 0
    ):
        raise ValueError("v0.2.1 Increment 1 progress differs")
    return {
        "status": TERMINAL,
        "history_status": history["status"],
        "baseline_readiness_attempt_count": 0,
        "fault_attempt_count": 0,
        "human_checkpoint_a": "UNFULFILLED",
        "human_checkpoint_b": "UNFULFILLED",
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
            verify_product_v021_increment1(arguments.project_root),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("TERMINAL", "verify_product_v021_increment1")
