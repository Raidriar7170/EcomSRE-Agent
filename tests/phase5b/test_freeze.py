from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecomsre.phase5b.freeze import build_freeze_manifest, verify_freeze_manifest
from ecomsre.phase5b.hidden_pack import canonical_json_bytes


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_freeze_builder_binds_required_runtime_and_public_anchors() -> None:
    manifest = build_freeze_manifest(PROJECT_ROOT)
    assert manifest.base_main_commit == "30c202adb74d5f2e9224098e4f51eb19f214f275"
    assert manifest.model_snapshot == "gpt-5.4-mini-2026-03-17"
    assert manifest.max_model_calls == manifest.max_tool_calls == 8
    assert "src/ecomsre/phase5a/provider.py" in manifest.frozen_files
    assert "src/ecomsre/phase5b/analysis.py" in manifest.frozen_files
    assert "src/ecomsre/backends/live_protocol.py" in manifest.frozen_files
    assert "src/ecomsre/backends/replay.py" in manifest.frozen_files
    assert "src/ecomsre/phase1/runtime_config.py" in manifest.frozen_files
    assert "src/ecomsre/phase2/tool_isolation.py" in manifest.frozen_files
    assert "src/ecomsre/tools/metrics.py" in manifest.frozen_files
    assert "src/ecomsre/__init__.py" in manifest.frozen_files
    assert "src/ecomsre/phase0/models.py" in manifest.frozen_files
    assert "config/phase1/agent.json" in manifest.frozen_files
    assert "config/phase2/tokenizers/o200k_base.tiktoken" in manifest.frozen_files
    assert "eval/phase1/ground-truth/ad-partial-failure-complete.json" in manifest.frozen_files
    assert not any("__pycache__" in path or path.endswith(".pyc") for path in manifest.frozen_files)


def test_freeze_preflight_rejects_content_model_and_budget_drift(tmp_path: Path) -> None:
    manifest = build_freeze_manifest(PROJECT_ROOT)
    manifest_path = tmp_path / "freeze.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest.model_dump(mode="json")))
    verify_freeze_manifest(PROJECT_ROOT, manifest_path)

    payload = json.loads(manifest_path.read_text())
    payload["model_snapshot"] = "gpt-5.4-mini-unfrozen"
    manifest_path.write_bytes(canonical_json_bytes(payload))
    with pytest.raises(ValueError, match="model_snapshot|literal"):
        verify_freeze_manifest(PROJECT_ROOT, manifest_path)

    payload = manifest.model_dump(mode="json")
    payload["max_tool_calls"] = 9
    manifest_path.write_bytes(canonical_json_bytes(payload))
    with pytest.raises(ValueError, match="max_tool_calls|literal"):
        verify_freeze_manifest(PROJECT_ROOT, manifest_path)
