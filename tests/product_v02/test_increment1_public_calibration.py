from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

from ecomsre.product.pilot.live_calibration_v02 import _write_public_calibration
from ecomsre.product.pilot.contracts_v02 import semantic_sha256_v02


ROOT = Path(__file__).resolve().parents[2]


def test_zero_attempt_calibration_cannot_claim_baseline_restoration(
    tmp_path: Path,
) -> None:
    _write_public_calibration(
        repository_root=tmp_path,
        terminal="BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE",
        observed_at=datetime.now(UTC),
        attempt_results=(),
        selected_root_service=None,
        selected_profile_sha256=None,
        private_report_sha256="1" * 64,
        demo_cleanup="CLEAN",
        outer_baseline_restored=True,
    )

    report = json.loads(
        (
            tmp_path / "docs/analysis/product-v02-profile-calibration.json"
        ).read_text(encoding="utf-8")
    )
    assert report["live_attempt_count"] == 0
    assert report["baseline_restoration"] is False
    assert report["outer_baseline_restored"] is True


def test_consumed_calibration_public_artifacts_are_hash_bound_and_non_leaking() -> None:
    calibration = json.loads(
        (
            ROOT / "docs/analysis/product-v02-profile-calibration.json"
        ).read_text(encoding="utf-8")
    )
    calibration_sha256 = calibration.pop("report_sha256")
    assert calibration_sha256 == semantic_sha256_v02(calibration)
    assert calibration["terminal"] == (
        "BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE"
    )
    assert calibration["live_attempt_count"] == 0
    assert calibration["baseline_restoration"] is False
    assert calibration["outer_baseline_restored"] is True
    assert calibration["demo_cleanup"] == "CLEAN"

    result = json.loads(
        (ROOT / "docs/results/product-v02-live-knowledge-loop.json").read_text(
            encoding="utf-8"
        )
    )
    result_sha256 = result.pop("result_sha256")
    assert result_sha256 == semantic_sha256_v02(result)
    assert result["live_calibration_attempt_count"] == 0
    assert result["positive_episode_count"] == 0
    assert result["heldout_recurrence_count"] == 0
    assert result["outer_baseline_restored"] is True
    assert result["owned_demo_cleanup"] == "CLEAN"

    public_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "docs/analysis/product-v02-profile-calibration.json",
            ROOT / "docs/analysis/product-v02-profile-calibration.md",
            ROOT / "docs/results/product-v02-live-knowledge-loop.json",
            ROOT / "docs/results/product-v02-live-knowledge-loop.md",
            ROOT / "docs/results/product-v02-live-knowledge-loop-limitations.md",
            ROOT / "docs/results/product-v02-live-knowledge-loop-interview-brief.md",
        )
    ).casefold()
    assert "kafkaqueueproblems" not in public_text
    assert "injected_value" not in public_text
    assert "private-control" not in public_text
