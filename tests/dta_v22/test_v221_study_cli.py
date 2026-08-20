from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.evidence_acquisition_cli_v221 import _parser, _write_once


def test_study_cli_exposes_only_development_and_single_evaluation_modes() -> None:
    parser = _parser()

    development = parser.parse_args(
        [
            "development",
            "--provider-env",
            "provider.env",
            "--prompt-file",
            "prompt.txt",
            "--case-set",
            "cases.json",
            "--truth",
            "truth.json",
            "--output",
            "development.json",
        ]
    )

    assert development.mode == "development"
    assert development.development_iteration == 1
    with pytest.raises(SystemExit):
        parser.parse_args(["smoke"])


def test_study_cli_output_is_write_once(tmp_path: Path) -> None:
    target = tmp_path / "study.json"

    _write_once(target, {"execution_count": 1})

    assert json.loads(target.read_text(encoding="utf-8"))["execution_count"] == 1
    with pytest.raises(FileExistsError):
        _write_once(target, {"execution_count": 2})
