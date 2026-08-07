from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecomsre_rcaeval_v2.public_projection import (
    assert_public_payload,
    write_private_json_create_once,
    write_public_json_create_once,
)


@pytest.mark.parametrize(
    "payload",
    [
        {"case_id": "development-case"},
        {"run_id": "a" * 32},
        {"instance": "1"},
        {"value": "/Users/example/private"},
        {"value": "/home/example/private"},
        {"value": "/private/example"},
        {"Authorization": "redacted"},
        {"value": "Bearer secret"},
        {"api_key": "redacted"},
    ],
)
def test_public_projection_rejects_case_run_path_and_secret_markers(
    payload: object,
) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        assert_public_payload(payload)


def test_public_json_is_canonical_create_once_and_world_readable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "public" / "aggregate.json"
    payload = {
        "classification": ["DEVELOPMENT_VISIBLE"],
        "counts": {"design": 60},
    }

    first = write_public_json_create_once(path, payload)
    second = write_public_json_create_once(path, payload)

    assert first == second
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert path.stat().st_mode & 0o777 == 0o644
    with pytest.raises(ValueError, match="differs"):
        write_public_json_create_once(path, {"counts": {"design": 61}})


def test_private_json_is_create_once_and_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "private" / "case-outcomes.json"
    payload = {"case_identity_sha256": "a" * 64}

    write_private_json_create_once(path, payload)
    write_private_json_create_once(path, payload)

    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    with pytest.raises(ValueError, match="differs"):
        write_private_json_create_once(
            path, {"case_identity_sha256": "b" * 64}
        )
