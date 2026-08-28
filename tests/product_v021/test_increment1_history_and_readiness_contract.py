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
    assert (
        profile.connector_query_bindings["OPENSEARCH"]
        == "bounded-logs-required-fields-v2"
    )
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
    progress = json.loads(
        (ROOT / "docs/analysis/product-v021-progress.json").read_text()
    )

    assert result["status"] == "ECOMSRE_PRODUCT_V021_BASELINE_AUDIT_READY"
    assert (
        result["baseline_readiness_attempt_count"]
        == progress["baseline_readiness_attempt_count"]
    )
    assert result["baseline_readiness_run_count"] == progress[
        "baseline_readiness_run_count"
    ]
    assert result["fault_attempt_count"] == 0
    assert result["human_checkpoint_a"] == "UNFULFILLED"
    assert result["human_checkpoint_b"] == "UNFULFILLED"
    assert result["agent_writes"] == 0
    assert result["runbook_executions"] == 0


def test_increment1_verifier_accepts_bounded_monotonic_later_progress(
    monkeypatch,
) -> None:
    original_load = increment1_verifier._load
    readiness_artifacts_verified = False

    def load_with_later_progress(path: Path) -> dict[str, object]:
        payload = original_load(path)
        if path.name == "product-v021-progress.json":
            payload.update(
                {
                    "increment": 2,
                    "terminal": (
                        "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_REPAIR_REQUIRED"
                    ),
                    "baseline_readiness_attempt_count": 1,
                    "baseline_readiness_run_count": 1,
                    "infrastructure_replacement_count": 0,
                    "action_authority_violations": 0,
                }
            )
            payload.pop("progress_sha256", None)
            payload["progress_sha256"] = increment1_verifier._semantic_sha256(
                payload
            )
        return payload

    monkeypatch.setattr(increment1_verifier, "_load", load_with_later_progress)

    def verify_readiness_artifacts(
        _root: Path,
        *,
        progress: dict[str, object],
        profile: object,
    ) -> None:
        nonlocal readiness_artifacts_verified
        readiness_artifacts_verified = True
        assert progress["terminal"] == (
            "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_REPAIR_REQUIRED"
        )
        assert profile is not None

    monkeypatch.setattr(
        increment1_verifier,
        "_verify_readiness_terminal_artifacts_v021",
        verify_readiness_artifacts,
    )

    result = increment1_verifier.verify_product_v021_increment1(ROOT)
    assert result["status"] == "ECOMSRE_PRODUCT_V021_BASELINE_AUDIT_READY"
    assert result["baseline_readiness_attempt_count"] == 1
    assert result["baseline_readiness_run_count"] == 1
    assert result["infrastructure_replacement_count"] == 0
    assert readiness_artifacts_verified is True


def test_increment2_calibration_terminal_rechecks_readiness_artifacts(
    monkeypatch,
) -> None:
    original_load = increment1_verifier._load
    calibration_artifacts_verified = False
    readiness_artifacts_verified = False

    def load_with_calibration_progress(path: Path) -> dict[str, object]:
        payload = original_load(path)
        if path.name == "product-v021-progress.json":
            payload.update(
                {
                    "increment": 2,
                    "terminal": (
                        "BLOCKED_ECOMSRE_PRODUCT_V021_UNKNOWN_FAULT_PROFILE"
                    ),
                    "baseline_readiness_attempt_count": 1,
                    "baseline_readiness_run_count": 1,
                    "infrastructure_replacement_count": 0,
                    "profile_calibration_iteration_count": 1,
                    "profile_calibration_changed_iteration_count": 0,
                    "calibration_execution_count": 1,
                    "action_authority_violations": 0,
                }
            )
            payload.pop("progress_sha256", None)
            payload["progress_sha256"] = increment1_verifier._semantic_sha256(
                payload
            )
        return payload

    def verify_calibration_artifacts(
        _root: Path,
        *,
        progress: dict[str, object],
        profile: object,
    ) -> None:
        nonlocal calibration_artifacts_verified
        calibration_artifacts_verified = True
        assert progress["calibration_execution_count"] == 1
        assert profile is not None

    def verify_readiness_artifacts(
        _root: Path,
        *,
        progress: dict[str, object],
        profile: object,
    ) -> None:
        nonlocal readiness_artifacts_verified
        readiness_artifacts_verified = True
        assert progress["baseline_readiness_attempt_count"] == 1
        assert profile is not None

    monkeypatch.setattr(increment1_verifier, "_load", load_with_calibration_progress)
    monkeypatch.setattr(
        increment1_verifier,
        "_verify_queue_profile_state_v021",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        increment1_verifier,
        "_verify_calibration_terminal_artifacts_v021",
        verify_calibration_artifacts,
    )
    monkeypatch.setattr(
        increment1_verifier,
        "_verify_readiness_terminal_artifacts_v021",
        verify_readiness_artifacts,
    )

    increment1_verifier.verify_product_v021_increment1(ROOT)

    assert calibration_artifacts_verified is True
    assert readiness_artifacts_verified is True


@pytest.mark.parametrize("mutation", ("MISSING_DIGEST", "AUTHORITY_VIOLATION"))
def test_increment2_progress_requires_digest_and_zero_authority_violations(
    monkeypatch,
    mutation: str,
) -> None:
    original_load = increment1_verifier._load

    def load_with_invalid_progress(path: Path) -> dict[str, object]:
        payload = original_load(path)
        if path.name == "product-v021-progress.json":
            payload.update(
                {
                    "increment": 2,
                    "terminal": (
                        "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_REPAIR_REQUIRED"
                    ),
                    "baseline_readiness_attempt_count": 1,
                    "baseline_readiness_run_count": 1,
                    "infrastructure_replacement_count": 0,
                    "action_authority_violations": (
                        1 if mutation == "AUTHORITY_VIOLATION" else 0
                    ),
                }
            )
            payload.pop("progress_sha256", None)
            if mutation != "MISSING_DIGEST":
                payload["progress_sha256"] = increment1_verifier._semantic_sha256(
                    payload
                )
        return payload

    monkeypatch.setattr(
        increment1_verifier,
        "_load",
        load_with_invalid_progress,
    )

    with pytest.raises(ValueError, match="progress"):
        increment1_verifier.verify_product_v021_increment1(ROOT)


def test_increment1_verifier_rejects_later_progress_outside_goal_bounds(
    monkeypatch,
) -> None:
    original_load = increment1_verifier._load

    def load_with_drift(path: Path) -> dict[str, object]:
        payload = original_load(path)
        if path.name == "product-v021-progress.json":
            payload.update(
                {
                    "increment": 2,
                    "terminal": "DRIFTED",
                    "baseline_readiness_attempt_count": 3,
                    "action_authority_violations": 0,
                }
            )
        return payload

    monkeypatch.setattr(increment1_verifier, "_load", load_with_drift)
    with pytest.raises(ValueError, match="Increment 2 progress state differs"):
        increment1_verifier.verify_product_v021_increment1(ROOT)


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
