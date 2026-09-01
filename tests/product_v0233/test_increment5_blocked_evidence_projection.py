from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.product_v0233.project_blocked_evidence import _project_exact_file
from scripts.ci.verify_product_v0233_terminal import (
    _REQUIRED_ABSENCES,
    _verify_required_absences,
    verify_product_v0233_terminal,
)


def _validate_object(payload: bytes) -> object:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("fixture is not a JSON object")
    return value


def test_exact_projection_creates_once_and_accepts_identical_bytes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "private.json"
    target = tmp_path / "public.json"
    payload = b'{"status":"BLOCKED"}'
    source.write_bytes(payload)
    expected_sha256 = hashlib.sha256(payload).hexdigest()

    first = _project_exact_file(
        source=source,
        target=target,
        expected_sha256=expected_sha256,
        validator=_validate_object,
    )
    second = _project_exact_file(
        source=source,
        target=target,
        expected_sha256=expected_sha256,
        validator=_validate_object,
    )

    assert first == second == expected_sha256
    assert target.read_bytes() == payload


def test_exact_projection_rejects_source_hash_drift(tmp_path: Path) -> None:
    source = tmp_path / "private.json"
    source.write_text('{"status":"CHANGED"}', encoding="utf-8")

    with pytest.raises(ValueError, match="source SHA-256 differs"):
        _project_exact_file(
            source=source,
            target=tmp_path / "public.json",
            expected_sha256="0" * 64,
            validator=_validate_object,
        )


def test_exact_projection_rejects_different_existing_target(tmp_path: Path) -> None:
    source = tmp_path / "private.json"
    target = tmp_path / "public.json"
    payload = b'{"status":"BLOCKED"}'
    source.write_bytes(payload)
    target.write_text('{"status":"DIFFERENT"}', encoding="utf-8")

    with pytest.raises(FileExistsError, match="public artifact differs"):
        _project_exact_file(
            source=source,
            target=target,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            validator=_validate_object,
        )


def test_exact_projection_rejects_existing_symlink(tmp_path: Path) -> None:
    source = tmp_path / "private.json"
    target = tmp_path / "public.json"
    elsewhere = tmp_path / "elsewhere.json"
    payload = b'{"status":"BLOCKED"}'
    source.write_bytes(payload)
    elsewhere.write_bytes(payload)
    target.symlink_to(elsewhere)

    with pytest.raises(FileExistsError, match="public artifact differs"):
        _project_exact_file(
            source=source,
            target=target,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            validator=_validate_object,
        )


def test_blocked_repository_terminal_is_publicly_verifiable() -> None:
    result = verify_product_v0233_terminal(Path(__file__).resolve().parents[2])

    assert result == {
        "terminal": "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS",
        "failure_stage": "FORMAL_TRAFFIC_PASS",
        "safe_error_code": "TypeError:FORMAL_TRAFFIC_PASS",
        "one_shot_consumed": True,
        "formal_clone_count": 1,
        "formal_execution_count": 1,
        "formal_transaction_count": 30,
        "new_incident_count": 0,
        "new_diagnosis_count": 0,
        "measured_result_count": 0,
        "measured_terminal": None,
        "formal_rerun_authorized": False,
        "diagnosis_retry_authorized": False,
        "action_authority": "NONE",
        "closure": "CLEAN",
    }


@pytest.mark.parametrize("relative_path", _REQUIRED_ABSENCES)
def test_blocked_repository_rejects_every_forbidden_success_artifact(
    tmp_path: Path,
    relative_path: str,
) -> None:
    forbidden = tmp_path / relative_path
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("fabricated\n", encoding="utf-8")

    with pytest.raises(ValueError, match="forbidden terminal artifact exists"):
        _verify_required_absences(tmp_path, list(_REQUIRED_ABSENCES))
