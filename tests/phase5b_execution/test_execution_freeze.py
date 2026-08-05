from __future__ import annotations

from pathlib import Path

from scripts.phase5b_execution.contracts import canonical_json_bytes
from scripts.phase5b_execution.freeze import (
    build_execution_freeze_manifest,
    harness_paths,
    verify_execution_freeze_manifest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXECUTION_BASE_COMMIT = "2cf6147b62394921727bde2f3094a72caa1563d9"


def test_execution_freeze_binds_all_control_plane_files_without_truth_paths(
    tmp_path: Path,
) -> None:
    manifest = build_execution_freeze_manifest(
        PROJECT_ROOT,
        execution_base_commit=EXECUTION_BASE_COMMIT,
    )
    manifest_path = tmp_path / "execution-freeze.v1.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest.model_dump(mode="json")))

    verified = verify_execution_freeze_manifest(PROJECT_ROOT, manifest_path)

    assert verified == manifest
    assert tuple(verified.harness_files) == harness_paths(PROJECT_ROOT)
    assert verified.protocol_commit == "790d2c79709ec88b94342b56feeb15d4c21c0d69"
    assert verified.execution_schedule_sha256 == (
        "a711696a2c12745e062d068fd507b74a4ce67e845505b05f458d7db5a97d37ec"
    )
    serialized = manifest_path.read_text(encoding="utf-8")
    assert "/Users/" not in serialized
    assert "ground_truth_root" not in serialized
    assert "expected_decision" not in serialized


def test_execution_freeze_uses_separate_config_root_to_preserve_protocol_paths() -> None:
    assert not (PROJECT_ROOT / "config/phase5b/execution-freeze.v1.json").exists()
    assert (
        Path("config/phase5b-execution/execution-freeze.v1.json")
        not in {Path(item) for item in harness_paths(PROJECT_ROOT)}
    )
