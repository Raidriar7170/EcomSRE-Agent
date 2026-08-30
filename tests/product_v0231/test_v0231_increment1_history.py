from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from scripts.ci import verify_product_v0231_history as verifier
from scripts.ci.verify_product_v0231_history import (
    verify_product_v0231_history,
    verify_product_v0231_squash_history,
    verify_product_v0231_written_reports,
)


ROOT = Path(__file__).resolve().parents[2]


def test_v0231_squash_history_accepts_imported_product_stack() -> None:
    result = verify_product_v0231_squash_history(ROOT)

    assert result == {
        "terminal": "ECOMSRE_PRODUCT_V0231_SQUASH_HISTORY_PASS",
        "binding_count": 1,
        "direct_bound_file_count": 8,
        "import_pr": 79,
        "import_squash_merge_commit": ("613f6203e4a174b4549b912cb16ca7998cf6238c"),
        "legacy_verifiers": {
            "v021": "ECOMSRE_PRODUCT_V021_HISTORY_VERIFIED",
            "v022": "ECOMSRE_PRODUCT_V022_HISTORY_VERIFIED",
            "v0221": "ECOMSRE_PRODUCT_V0221_HISTORY_VERIFIED",
            "v0222": "ECOMSRE_PRODUCT_V0222_HISTORY_VERIFIED",
            "v023": "ECOMSRE_PRODUCT_V023_HISTORY_VERIFIED",
        },
    }


def test_v0231_history_rejects_predecessor_counter_drift(tmp_path: Path) -> None:
    manifest = json.loads(
        (ROOT / "config/product-v0231/historical-results.v1.json").read_text(
            encoding="utf-8"
        )
    )
    manifest["predecessor"]["accepted_incident_count"] = 1
    drifted = tmp_path / "historical-results.v1.json"
    drifted.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="predecessor identity differs"):
        verify_product_v0231_history(
            ROOT,
            predecessor_root=tmp_path / "unavailable-private-state",
            manifest_path=drifted,
        )


def test_v0231_history_requires_direct_predecessor_head_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def require_lineage(_root: Path, ancestor: str, descendant: str) -> None:
        calls.append((ancestor, descendant))
        if ancestor == verifier.PREDECESSOR_HEAD_V0231 and descendant == "HEAD":
            raise ValueError("missing direct predecessor lineage")

    monkeypatch.setattr(verifier, "_require_ancestry", require_lineage)
    with pytest.raises(ValueError, match="missing direct predecessor lineage"):
        verify_product_v0231_history(
            ROOT,
            predecessor_root=tmp_path / "must-not-be-reached",
        )

    assert (verifier.PREDECESSOR_HEAD_V0231, "HEAD") in calls


def test_v0231_written_reports_are_self_sealed_and_cross_bound() -> None:
    result = verify_product_v0231_written_reports(ROOT)

    assert result["terminal"] == "ECOMSRE_PRODUCT_V0231_HISTORY_AND_BASELINE_PASS"
    assert result["squash_terminal"] == "ECOMSRE_PRODUCT_V0231_SQUASH_HISTORY_PASS"


def test_v0231_written_reports_reject_audit_drift(tmp_path: Path) -> None:
    audit = json.loads(
        (ROOT / "docs/analysis/product-v0231-predecessor-audit.json").read_text(
            encoding="utf-8"
        )
    )
    audit["accepted_incident_count"] = 1
    drifted = tmp_path / "product-v0231-predecessor-audit.json"
    drifted.write_text(json.dumps(audit), encoding="utf-8")

    with pytest.raises(ValueError, match="written report binding differs"):
        verify_product_v0231_written_reports(
            ROOT,
            predecessor_audit_path=drifted,
        )


def test_v0231_written_reports_reject_resealed_semantic_tampering(
    tmp_path: Path,
) -> None:
    audit = json.loads(
        (ROOT / "docs/analysis/product-v0231-predecessor-audit.json").read_text(
            encoding="utf-8"
        )
    )
    audit.update(
        {
            "active_baseline_id": "base-" + "0" * 24,
            "product_cleanup": "DIRTY",
            "demo_cleanup": "DIRTY",
            "tracked_file_count": 0,
            "private_binding_count": 0,
        }
    )
    audit["audit_sha256"] = semantic_sha256_v22(
        {key: value for key, value in audit.items() if key != "audit_sha256"}
    )
    audit_path = tmp_path / "product-v0231-predecessor-audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")

    squash = json.loads(
        (
            ROOT / "docs/analysis/product-v0231-squash-history-verification.json"
        ).read_text(encoding="utf-8")
    )
    squash["legacy_verifiers"] = {}
    squash["verification_sha256"] = semantic_sha256_v22(
        {
            key: value
            for key, value in squash.items()
            if key != "verification_sha256"
        }
    )
    squash_path = tmp_path / "product-v0231-squash-history-verification.json"
    squash_path.write_text(json.dumps(squash), encoding="utf-8")

    with pytest.raises(ValueError, match="written report binding differs"):
        verify_product_v0231_written_reports(
            ROOT,
            predecessor_audit_path=audit_path,
            squash_report_path=squash_path,
        )
