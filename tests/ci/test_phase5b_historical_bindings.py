from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ecomsre.phase5b.contracts import FrozenEvaluationManifest
from scripts.ci.verify_phase5b_historical_bindings import (
    HISTORICAL_MANIFEST_SHA256,
    _verify_declared_historical_bindings,
    main,
    verify_historical_bindings,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
HISTORICAL_MANIFEST = PROJECT_ROOT / "config/phase5b/freeze-manifest.v1.json"
AGENT_MAINLINE_WORKFLOW = PROJECT_ROOT / ".github/workflows/agent-mainline.yml"
MAKEFILE = PROJECT_ROOT / "Makefile"
BOUND_RELATIVE = "src/ecomsre/phase5b/freeze.py"


def _historical_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    payload = json.loads(HISTORICAL_MANIFEST.read_text(encoding="utf-8"))
    bound = tmp_path / BOUND_RELATIVE
    bound.parent.mkdir(parents=True)
    bound.write_bytes(b"historical Phase 5B runtime\n")
    payload["frozen_files"] = {
        BOUND_RELATIVE: hashlib.sha256(bound.read_bytes()).hexdigest()
    }
    manifest = tmp_path / "config/phase5b/freeze-manifest.v1.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return manifest, bound, payload


def _verify_fixture(
    project_root: Path,
    payload: dict[str, object],
) -> FrozenEvaluationManifest:
    manifest = FrozenEvaluationManifest.model_validate(payload)
    return _verify_declared_historical_bindings(project_root, manifest)


def test_current_historical_manifest_bytes_are_unchanged() -> None:
    assert hashlib.sha256(HISTORICAL_MANIFEST.read_bytes()).hexdigest() == (
        HISTORICAL_MANIFEST_SHA256
    )


def test_historical_binding_rejects_manifest_byte_drift(tmp_path: Path) -> None:
    tampered_manifest = tmp_path / "freeze-manifest.v1.json"
    tampered_manifest.write_bytes(HISTORICAL_MANIFEST.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="manifest bytes changed"):
        verify_historical_bindings(PROJECT_ROOT, tampered_manifest)


def test_historical_manifest_never_absorbs_dta_v2() -> None:
    payload = json.loads(HISTORICAL_MANIFEST.read_text(encoding="utf-8"))

    assert not any(
        path.startswith("src/ecomsre/dta_v2/")
        for path in payload["frozen_files"]
    )


def test_successor_namespace_does_not_change_historical_bindings(
    tmp_path: Path,
) -> None:
    manifest, _bound, _payload = _historical_fixture(tmp_path)
    successor = tmp_path / "src/ecomsre/dta_v2/module.py"
    successor.parent.mkdir(parents=True)
    successor.write_text("SUCCESSOR = True\n", encoding="utf-8")

    verified = _verify_fixture(
        tmp_path,
        json.loads(manifest.read_text(encoding="utf-8")),
    )

    assert tuple(verified.frozen_files) == (BOUND_RELATIVE,)


def test_historical_binding_rejects_modified_bound_file(tmp_path: Path) -> None:
    manifest, bound, _payload = _historical_fixture(tmp_path)
    bound.write_bytes(b"modified historical runtime\n")

    with pytest.raises(ValueError, match="frozen path drift"):
        _verify_fixture(
            tmp_path,
            json.loads(manifest.read_text(encoding="utf-8")),
        )


def test_historical_binding_rejects_deleted_bound_file(tmp_path: Path) -> None:
    manifest, bound, _payload = _historical_fixture(tmp_path)
    bound.unlink()

    with pytest.raises(ValueError, match="frozen path is missing"):
        _verify_fixture(
            tmp_path,
            json.loads(manifest.read_text(encoding="utf-8")),
        )


def test_historical_binding_rejects_manifest_hash_drift(tmp_path: Path) -> None:
    manifest, _bound, payload = _historical_fixture(tmp_path)
    payload["frozen_files"] = {BOUND_RELATIVE: "0" * 64}
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="frozen path drift"):
        _verify_fixture(tmp_path, payload)


def test_historical_binding_rejects_symlinked_bound_file(tmp_path: Path) -> None:
    manifest, bound, _payload = _historical_fixture(tmp_path)
    target = tmp_path / "historical-target.py"
    target.write_bytes(bound.read_bytes())
    bound.unlink()
    bound.symlink_to(target)

    with pytest.raises(ValueError, match="regular non-symlink"):
        _verify_fixture(
            tmp_path,
            json.loads(manifest.read_text(encoding="utf-8")),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("base_main_commit", "0" * 40, "base main commit"),
        ("evaluation_version", "phase5b.v2", "evaluation_version|literal"),
    ],
)
def test_historical_binding_rejects_identity_drift(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    manifest, _bound, payload = _historical_fixture(tmp_path)
    payload[field] = value
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        _verify_fixture(tmp_path, payload)


def test_cli_reports_verified_historical_bindings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(
        [
            "--project-root",
            str(PROJECT_ROOT),
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["evaluation_version"] == "phase5b.v1"
    assert output["frozen_file_count"] > 1
    assert output["status"] == "PHASE5B_HISTORICAL_BINDINGS_VERIFIED"


def test_agent_mainline_uses_successor_safe_historical_binding_gate() -> None:
    workflow = AGENT_MAINLINE_WORKFLOW.read_text(encoding="utf-8")

    assert "Phase 5B historical bindings" in workflow
    assert "scripts.ci.verify_phase5b_historical_bindings" in workflow
    assert "make phase5b-preflight" not in workflow
    assert "src/ecomsre/dta_v2" in workflow
    assert "scripts/ci" in workflow

    makefile = MAKEFILE.read_text(encoding="utf-8")
    assert "phase5b-preflight:" in makefile
    assert "$(PHASE5B_CLI) preflight" in makefile
