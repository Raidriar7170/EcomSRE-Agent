#!/usr/bin/env python3
"""Verify the frozen, consumed Product v0.2 blocked calibration result."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence


BLOCKED_TERMINAL = "BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE"
VERIFIED_TERMINAL = "ECOMSRE_PRODUCT_V02_BLOCKED_RESULT_VERIFIED"


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"v0.2 blocked artifact is not an object: {path}")
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


def _verify_self_hash(payload: dict[str, Any], field: str) -> str:
    supplied = payload.pop(field, None)
    if not isinstance(supplied, str) or supplied != _semantic_sha256(payload):
        raise ValueError(f"v0.2 blocked artifact {field} differs")
    return supplied


def verify_product_v02_blocked_result(project_root: Path) -> dict[str, object]:
    root = project_root.resolve(strict=True)
    marker = _load_object(
        root / "config/product-v02/live-pilot/calibration-consumed.json"
    )
    calibration = _load_object(
        root / "docs/analysis/product-v02-profile-calibration.json"
    )
    cleanup = _load_object(root / "docs/analysis/product-v02-cleanup-closure.json")
    progress = _load_object(root / "docs/analysis/product-v02-progress.json")
    result = _load_object(
        root / "docs/results/product-v02-live-knowledge-loop.json"
    )
    profile = _load_object(root / "config/product-v02/live-pilot/profile.json")

    marker_sha256 = _verify_self_hash(marker, "consumed_sha256")
    calibration_sha256 = _verify_self_hash(calibration, "report_sha256")
    cleanup_sha256 = _verify_self_hash(cleanup, "closure_sha256")
    progress_sha256 = _verify_self_hash(progress, "progress_sha256")
    result_sha256 = _verify_self_hash(result, "result_sha256")

    if (
        marker.get("schema_version")
        != "ecomsre.product.v02.calibration-consumed.v1"
        or marker.get("campaign_consumed") is not True
        or marker.get("terminal") != BLOCKED_TERMINAL
        or marker.get("live_attempt_count") != 0
        or marker.get("public_calibration_report_sha256")
        != calibration_sha256
        or marker.get("public_result_sha256") != result_sha256
        or marker.get("calibration_start_sha256")
        != result.get("calibration_start_sha256")
        or marker.get("private_report_sha256")
        != result.get("private_calibration_report_sha256")
    ):
        raise ValueError("tracked v0.2 calibration consumption binding differs")
    if (
        calibration.get("terminal") != BLOCKED_TERMINAL
        or calibration.get("live_attempt_count") != 0
        or calibration.get("attempts") != []
        or calibration.get("baseline_restoration") is not False
        or calibration.get("outer_baseline_restored") is not True
        or calibration.get("demo_cleanup") != "CLEAN"
        or calibration.get("action_authority") != "NONE"
        or calibration.get("agent_writes") != 0
        or calibration.get("runbook_executions") != 0
    ):
        raise ValueError("public v0.2 calibration blocker semantics differ")
    if (
        progress.get("terminal") != BLOCKED_TERMINAL
        or progress.get("live_attempt_count") != 0
        or progress.get("accepted_positive_episode_count") != 0
        or progress.get("heldout_recurrence_count") != 0
        or progress.get("next_boundary") != "STOPPED_UNKNOWN_FAULT_PROFILE"
    ):
        raise ValueError("public v0.2 progress blocker semantics differ")
    if (
        result.get("engineering_terminal") != BLOCKED_TERMINAL
        or result.get("pilot_outcome") != "NOT_REACHED"
        or result.get("blocker_code") != "BASELINE_INSUFFICIENT_WINDOWS"
        or result.get("live_calibration_attempt_count") != 0
        or result.get("positive_episode_count") != 0
        or result.get("heldout_recurrence_count") != 0
        or result.get("outer_baseline_restored") is not True
        or result.get("owned_demo_cleanup") != "CLEAN"
        or result.get("action_authority") != "NONE"
        or result.get("agent_writes") != 0
        or result.get("runbook_executions") != 0
        or result.get("cleanup_closure_sha256") != cleanup_sha256
    ):
        raise ValueError("public v0.2 result blocker semantics differ")
    if (
        cleanup.get("cleanup_contract_verdict") != "CLEAN"
        or cleanup.get("cleanup_contract_owned_containers_after") != 0
        or cleanup.get("cleanup_contract_owned_networks_after") != 0
        or cleanup.get("cleanup_contract_owned_volumes_after") != 0
        or cleanup.get("cleanup_contract_non_owned_resources_changed") is not False
        or cleanup.get("postrun_owned_containers") != 0
        or cleanup.get("postrun_owned_networks") != 0
        or cleanup.get("postrun_owned_volumes") != 0
        or cleanup.get("postrun_reserved_port_listener_count") != 0
        or cleanup.get("source_private_report_sha256")
        != result.get("private_calibration_report_sha256")
    ):
        raise ValueError("v0.2 cleanup closure semantics differ")
    selection_fields = (
        "selected_value",
        "selected_root_service",
        "calibration_report_sha256",
        "calibration_contract_sha256",
        "calibration_runtime_binding_sha256",
        "calibrated_at",
        "profile_sha256",
    )
    if any(profile.get(field) is not None for field in selection_fields):
        raise ValueError("blocked v0.2 profile must remain unfrozen")

    public_paths = (
        root / "docs/analysis/product-v02-profile-calibration.json",
        root / "docs/analysis/product-v02-profile-calibration.md",
        root / "docs/analysis/product-v02-cleanup-closure.json",
        root / "docs/analysis/product-v02-progress.json",
        root / "docs/results/product-v02-live-knowledge-loop.json",
        root / "docs/results/product-v02-live-knowledge-loop.md",
        root / "docs/results/product-v02-live-knowledge-loop-limitations.md",
        root / "docs/results/product-v02-live-knowledge-loop-interview-brief.md",
    )
    public_text = "\n".join(
        path.read_text(encoding="utf-8") for path in public_paths
    ).casefold()
    forbidden = ("kafkaqueueproblems", "injected_value", "private-control")
    if any(token in public_text for token in forbidden):
        raise ValueError("public v0.2 blocked artifact leaks evaluator control")
    if "ecomsre_product_v02_live_knowledge_loop_supported" in public_text:
        raise ValueError("blocked v0.2 artifacts contain an unsupported success marker")

    return {
        "status": VERIFIED_TERMINAL,
        "marker_sha256": marker_sha256,
        "calibration_report_sha256": calibration_sha256,
        "cleanup_closure_sha256": cleanup_sha256,
        "progress_sha256": progress_sha256,
        "result_sha256": result_sha256,
        "live_attempt_count": 0,
        "outer_baseline_restored": True,
        "owned_demo_cleanup": "CLEAN",
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
            verify_product_v02_blocked_result(arguments.project_root),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "BLOCKED_TERMINAL",
    "VERIFIED_TERMINAL",
    "verify_product_v02_blocked_result",
)
