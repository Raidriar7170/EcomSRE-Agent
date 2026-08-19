from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from scripts.ci.verify_dta_v22_historical_bindings import (
    REQUIRED_HISTORICAL_PATHS,
    load_binding_manifest,
    verify_declared_files,
    verify_historical_bindings,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "config/dta-v22/historical-bindings.v1.json"


def _copy_bound_files(tmp_path: Path) -> tuple[dict[str, object], Path]:
    manifest = load_binding_manifest(MANIFEST_PATH)
    copied_manifest = tmp_path / "config/dta-v22/historical-bindings.v1.json"
    copied_manifest.parent.mkdir(parents=True)
    shutil.copy2(MANIFEST_PATH, copied_manifest)
    for item in manifest["files"]:
        source = REPO_ROOT / item["path"]
        destination = tmp_path / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return manifest, copied_manifest


def test_manifest_binds_exact_v2_and_v21_historical_surface() -> None:
    manifest = load_binding_manifest(MANIFEST_PATH)

    assert manifest["schema_version"] == "dta-v22.historical-bindings.v1"
    assert manifest["inspected_starting_main"] == (
        "9da92d54a4fb470c5452cee36a731e81529d05a5"
    )
    assert manifest["v21_capability_merge_commit"] == (
        "4442dda6cf7d54e163b34355dad2e8235d3957c1"
    )
    assert manifest["v21_capability_tree_sha1"] == (
        "b6b5e5df5ba0cdd45bc97d1990bbe1abe83c2675"
    )
    assert manifest["v21_administrative_merge_commit"] == (
        "9da92d54a4fb470c5452cee36a731e81529d05a5"
    )
    assert manifest["v21_administrative_tree_sha1"] == (
        "65877cf9061bab3e30c6f127fdbe1da59b3b95a6"
    )
    assert tuple(item["path"] for item in manifest["files"]) == (
        REQUIRED_HISTORICAL_PATHS
    )
    assert manifest["files"][-1] == {
        "path": "src/ecomsre/dta_v2/v21/live_final_cli.py",
        "raw_sha256": (
            "0140114f0754aa7fb4d5482805d4ecdb8715a996c57e41111c85c2ad5586f031"
        ),
        "semantic_sha256": None,
    }

    summary = verify_historical_bindings(REPO_ROOT, MANIFEST_PATH)
    assert summary == {
        "base_commit": "9da92d54a4fb470c5452cee36a731e81529d05a5",
        "file_count": len(REQUIRED_HISTORICAL_PATHS),
        "status": "DTA_V22_HISTORICAL_BINDINGS_VERIFIED",
    }


def test_verifier_fails_closed_on_bound_file_drift(tmp_path: Path) -> None:
    manifest, _ = _copy_bound_files(tmp_path)
    changed = tmp_path / "docs/results/dta-v21-evaluation.md"
    changed.write_text(changed.read_text(encoding="utf-8") + "\ndrift\n")

    with pytest.raises(ValueError, match="historical path drift"):
        verify_declared_files(tmp_path, manifest)


def test_verifier_rejects_historical_symlink(tmp_path: Path) -> None:
    manifest, _ = _copy_bound_files(tmp_path)
    changed = tmp_path / "docs/results/dta-v21-evaluation.md"
    changed.unlink()
    changed.symlink_to(tmp_path / "docs/results/dta-v21-live-capability-closeout.md")

    with pytest.raises(ValueError, match="regular non-symlink"):
        verify_declared_files(tmp_path, manifest)


def test_manifest_contains_no_private_path_or_secret_material() -> None:
    text = MANIFEST_PATH.read_text(encoding="utf-8")
    payload = json.loads(text)

    assert "/" + "Users/" not in text
    assert ".ecomsre/private" not in text
    assert "provider" + "_response" not in text
    assert payload["expected_v21_planner_identity_sha256"] == (
        "80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d"
    )
