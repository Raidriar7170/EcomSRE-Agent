from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ci.verify_dta_v21_evaluation_freeze import (
    _require_entry_projection_bindings,
    _require_exact_directory,
    verify_development_report_files,
    verify_public_evaluation,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_checked_in_development_evaluation_is_bound_and_safe() -> None:
    result = verify_public_evaluation(REPO_ROOT, require_freeze=False)

    assert result["development_report_sha256"] == (
        "ed624890b655f10598310daefb574eaea0ca74085183ba70cbc31cb05a812a43"
    )
    assert result["development_entry_count"] == 40
    assert result["held_out_case_count"] == 8
    assert result["truth_isolation"] == "PASS"
    assert result["unsafe_writes"] == 0


def test_development_report_verifier_rejects_unsafe_write_drift(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "report.json"
    disposition_path = tmp_path / "disposition.json"
    report = json.loads(
        (REPO_ROOT / "docs/results/dta-v21-development-evaluation.json").read_text(
            encoding="utf-8"
        )
    )
    report["unsafe_writes"] = 1
    report_path.write_text(json.dumps(report), encoding="utf-8")
    disposition_path.write_bytes(
        (
            REPO_ROOT
            / "docs/review-evidence/dta-v21-evaluation-freeze/current-disposition.json"
        ).read_bytes()
    )

    with pytest.raises(ValueError):
        verify_development_report_files(report_path, disposition_path)


def test_private_attempt_tree_rejects_undeclared_files_and_symlinks(
    tmp_path: Path,
) -> None:
    (tmp_path / "expected.json").write_text("{}", encoding="utf-8")
    (tmp_path / "entries").mkdir()
    _require_exact_directory(
        tmp_path,
        files={"expected.json"},
        directories={"entries"},
        description="test tree",
    )

    (tmp_path / "undeclared.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="undeclared"):
        _require_exact_directory(
            tmp_path,
            files={"expected.json"},
            directories={"entries"},
            description="test tree",
        )
    (tmp_path / "undeclared.json").unlink()
    (tmp_path / "entries").rmdir()
    (tmp_path / "entries").symlink_to(tmp_path / "expected.json")
    with pytest.raises(ValueError, match="non-symlink"):
        _require_exact_directory(
            tmp_path,
            files={"expected.json"},
            directories={"entries"},
            description="test tree",
        )


def test_private_entry_projection_rejects_standalone_score_tamper() -> None:
    marker = object()
    entry = type("Entry", (), {"prediction": marker, "score": marker})()

    with pytest.raises(ValueError, match="standalone prediction or score"):
        _require_entry_projection_bindings(
            entry,
            prediction=marker,  # type: ignore[arg-type]
            score=object(),  # type: ignore[arg-type]
        )


def test_public_evaluation_requires_freeze_manifest_when_requested() -> None:
    manifest = REPO_ROOT / "config/dta-v21/evaluation/manifest.json"
    if manifest.exists():
        result = verify_public_evaluation(REPO_ROOT, require_freeze=True)
        assert result["evaluation_frozen"] is True
    else:
        with pytest.raises(ValueError, match="freeze manifest is missing"):
            verify_public_evaluation(REPO_ROOT, require_freeze=True)


def test_make_and_ci_expose_offline_evaluation_verification() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github/workflows/agent-mainline.yml").read_text(
        encoding="utf-8"
    )

    assert "dta-v21-development-eval:" in makefile
    assert "dta-v21-development-verify:" in makefile
    assert "dta-v21-replay-verify:" in makefile
    assert "make dta-v21-development-verify" in workflow
    assert "make dta-v21-replay-verify" in workflow
