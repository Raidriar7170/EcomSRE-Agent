from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecomsre.product.pilot.baseline_readiness_v021 import (
    PilotBaselineReadinessProfileV021,
)
import scripts.ci.verify_product_v021_increment1 as increment1_verifier
from scripts.ci.verify_product_v021_history import verify_product_v021_history
from scripts.ci.verify_product_v021_increment1 import verify_product_v021_increment1


ROOT = Path(__file__).resolve().parents[2]


def test_product_v021_history_preserves_blocked_predecessor() -> None:
    result = verify_product_v021_history(ROOT)

    assert result == {
        "status": "ECOMSRE_PRODUCT_V021_HISTORY_VERIFIED",
        "predecessor_head": "a439f8882cd2fcdd3767f6bcfd5d955219fa1e15",
        "predecessor_terminal": "BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE",
        "bound_file_count": 8,
        "live_attempt_count": 0,
        "pilot_outcome": "NOT_REACHED",
        "private_content_audit_claimed": False,
    }


def test_product_v021_history_rejects_classification_drift(tmp_path) -> None:
    manifest = json.loads(
        (ROOT / "config/product-v021/historical-results.v1.json").read_text()
    )
    manifest["classification"]["fault_attempt"] = "STARTED"
    drifted = tmp_path / "history.json"
    drifted.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="classification differs"):
        verify_product_v021_history(ROOT, drifted)


def test_product_v021_history_rejects_path_role_remap(tmp_path) -> None:
    manifest = json.loads(
        (ROOT / "config/product-v021/historical-results.v1.json").read_text()
    )
    manifest["files"][0]["role"], manifest["files"][1]["role"] = (
        manifest["files"][1]["role"],
        manifest["files"][0]["role"],
    )
    drifted = tmp_path / "history-role-remap.json"
    drifted.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="bindings differ"):
        verify_product_v021_history(ROOT, drifted)


def test_readiness_profile_is_fresh_strict_and_hash_bound() -> None:
    payload = json.loads(
        (ROOT / "config/product-v021/baseline-readiness/profile.json").read_text()
    )
    profile = PilotBaselineReadinessProfileV021.model_validate(payload)

    assert profile.candidate_services == ("checkout",)
    assert profile.build_policy.window_count == 5
    assert profile.build_policy.minimum_successful_windows == 4
    assert profile.healthy_traffic_profile.maximum_request_count == 180
    assert profile.maximum_changed_attempts == 2
    assert profile.public_root != ".local/product-v02/live-pilot"
    assert profile.private_root != ".local/product-v02/private-live-control"


def test_readiness_profile_rejects_threshold_weakening() -> None:
    payload = json.loads(
        (ROOT / "config/product-v021/baseline-readiness/profile.json").read_text()
    )
    payload["build_policy"]["minimum_successful_windows"] = 3

    with pytest.raises(ValueError, match="build policy differs"):
        PilotBaselineReadinessProfileV021.model_validate(payload)


def test_increment1_verifier_mints_only_the_offline_audit_terminal() -> None:
    result = verify_product_v021_increment1(ROOT)

    assert result["status"] == "ECOMSRE_PRODUCT_V021_BASELINE_AUDIT_READY"
    assert result["baseline_readiness_attempt_count"] == 0
    assert result["fault_attempt_count"] == 0
    assert result["human_checkpoint_a"] == "UNFULFILLED"
    assert result["human_checkpoint_b"] == "UNFULFILLED"
    assert result["agent_writes"] == 0
    assert result["runbook_executions"] == 0


@pytest.mark.parametrize(
    ("field", "drifted_value"),
    [
        (
            "episode_roles",
            {
                "N0": "FIT_POSITIVE",
                "P1": "LIVE_NO_FAULT_NEGATIVE",
                "P2": "FIT_POSITIVE",
                "P3": "SHADOW_POSITIVE",
                "H1": "FINAL_HELDOUT_RECURRENCE",
            },
        ),
        ("maximum_infrastructure_replacements_per_episode", 99),
        ("positive_episode_count", 2),
        ("live_no_fault_negative_count", 0),
        ("heldout_recurrence_maximum", 2),
    ],
)
def test_increment1_verifier_rejects_campaign_schedule_drift(
    monkeypatch,
    field: str,
    drifted_value: object,
) -> None:
    original_load = increment1_verifier._load

    def load_with_drift(path: Path) -> dict[str, object]:
        payload = original_load(path)
        if path.name == "campaign.json":
            payload[field] = drifted_value
        return payload

    monkeypatch.setattr(increment1_verifier, "_load", load_with_drift)
    with pytest.raises(ValueError, match="live campaign boundary differs"):
        increment1_verifier.verify_product_v021_increment1(ROOT)


@pytest.mark.parametrize("field", ["fit_strata", "shadow_strata"])
def test_increment1_verifier_rejects_negative_control_strata_drift(
    monkeypatch,
    field: str,
) -> None:
    original_load = increment1_verifier._load

    def load_with_drift(path: Path) -> dict[str, object]:
        payload = original_load(path)
        if path.name == "negative-controls.json":
            payload[field] = []
        return payload

    monkeypatch.setattr(increment1_verifier, "_load", load_with_drift)
    with pytest.raises(ValueError, match="negative-control boundary differs"):
        increment1_verifier.verify_product_v021_increment1(ROOT)
