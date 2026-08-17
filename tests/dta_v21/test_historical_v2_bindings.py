from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from scripts.ci.verify_dta_v2_historical_bindings import (
    load_binding_manifest,
    verify_historical_bindings,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "config/dta-v21/historical-v2-bindings.v1.json"
EXPECTED_PATHS = {
    "config/dta-v2/agent-identity.v1.json",
    "config/dta-v2/live-demo.v1.json",
    "config/dta-v2/live-demo.v2.json",
    "config/dta-v2/evaluation/manifest.json",
    "docs/analysis/dta-v2-master-progress.json",
    "docs/design/diagnosis-to-action-v2.md",
    "docs/results/dta-v2-evaluation.json",
    "docs/results/dta-v2-evaluation.md",
    "docs/results/dta-v2-live-demo.json",
    "docs/results/dta-v2-live-demo.md",
    "docs/results/dta-v2-live-demo-human-brief.md",
}


def _copy_bound_tree(tmp_path: Path) -> tuple[dict[str, object], Path]:
    manifest = load_binding_manifest(MANIFEST_PATH)
    copied_manifest = tmp_path / "config/dta-v21/historical-v2-bindings.v1.json"
    copied_manifest.parent.mkdir(parents=True)
    shutil.copy2(MANIFEST_PATH, copied_manifest)
    for item in manifest["files"]:
        source = REPO_ROOT / item["path"]
        destination = tmp_path / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return manifest, copied_manifest


def test_manifest_binds_exact_historical_dta_v2_surface() -> None:
    manifest = load_binding_manifest(MANIFEST_PATH)

    assert manifest["schema_version"] == "dta-v21.historical-v2-bindings.v1"
    assert manifest["base_commit"] == (
        "925d23994888d1b83e57fc1bbdd1944e57a1bfff"
    )
    assert manifest["expected_historical_terminal"] == (
        "DTA_V2_LIVE_DEMO_ACCEPTANCE_PASS"
    )
    assert manifest["expected_evaluation_result"] == (
        "COMPLETED_HELD_OUT_NEGATIVE"
    )
    assert manifest["expected_agent_identity_sha256"] == (
        "6efc26c6e5fab6190be9e63c0bec318c6e94fa29196e6693eb63b2845c6ad0a4"
    )
    assert manifest["expected_held_out_seal_sha256"] == (
        "0f944e79f0958f285006c3bdc3cf8f82b8a71731d8d96d02b474f254a54e247a"
    )
    assert {item["path"] for item in manifest["files"]} == EXPECTED_PATHS

    summary = verify_historical_bindings(REPO_ROOT, MANIFEST_PATH)
    assert summary == {
        "base_commit": "925d23994888d1b83e57fc1bbdd1944e57a1bfff",
        "file_count": 11,
        "status": "DTA_V2_HISTORICAL_BINDINGS_VERIFIED",
    }


def test_verifier_fails_closed_on_historical_file_drift(tmp_path: Path) -> None:
    _, copied_manifest = _copy_bound_tree(tmp_path)

    changed = tmp_path / "docs/results/dta-v2-evaluation.md"
    changed.write_text(changed.read_text(encoding="utf-8") + "\ndrift\n")

    with pytest.raises(ValueError, match="historical DTA v2 path drift"):
        verify_historical_bindings(tmp_path, copied_manifest)


def test_verifier_rejects_manifest_byte_drift(tmp_path: Path) -> None:
    _, copied_manifest = _copy_bound_tree(tmp_path)
    copied_manifest.write_text(
        copied_manifest.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="binding manifest bytes changed"):
        verify_historical_bindings(tmp_path, copied_manifest)


def test_verifier_rejects_historical_symlink(tmp_path: Path) -> None:
    _, copied_manifest = _copy_bound_tree(tmp_path)
    changed = tmp_path / "docs/results/dta-v2-evaluation.md"
    changed.unlink()
    changed.symlink_to(tmp_path / "docs/results/dta-v2-live-demo.md")

    with pytest.raises(ValueError, match="regular non-symlink"):
        verify_historical_bindings(tmp_path, copied_manifest)


def test_v21_namespace_is_independent_from_frozen_v2() -> None:
    import ecomsre.dta_v2.v21 as v21

    assert v21.SCHEMA_PREFIX == "dta-v21."
    assert v21.PUBLIC_RESULT_PREFIX == "dta-v21-"
    assert json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["files"]
