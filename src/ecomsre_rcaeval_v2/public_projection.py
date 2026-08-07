"""Leakage guards and durable create-once public/private projections."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re


_FORBIDDEN_KEYS = {
    "authorization",
    "api_key",
    "openai_api_key",
    "provider_base_url",
    "raw_response",
    "raw_provider_response",
    "raw_function_call",
    "case_id",
    "run_id",
    "instance",
}
_FORBIDDEN_TEXT = (
    "authorization",
    "bearer ",
    "api_key",
    "openai_api_key",
    "tt-case-",
    "scored_cases",
    "ground-truth.json",
    "evaluator-only",
)
_PRIVATE_PATH = re.compile(r"(?:^|[\s='\"])/(?:users|home|private)/", re.IGNORECASE)


def _canonical_json_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def assert_public_payload(payload: object) -> None:
    """Reject identifiers, secrets, raw outputs, and private absolute paths."""

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).casefold()
                if normalized in _FORBIDDEN_KEYS:
                    raise ValueError("public payload contains a forbidden key")
                visit(item)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                visit(item)
            return
        if isinstance(value, str):
            normalized = value.casefold()
            if any(marker in normalized for marker in _FORBIDDEN_TEXT):
                raise ValueError("public payload contains forbidden text")
            if _PRIVATE_PATH.search(value):
                raise ValueError("public payload contains a forbidden private path")

    visit(payload)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _create_once(path: Path, payload: bytes, *, private: bool) -> str:
    directory_mode = 0o700 if private else 0o755
    file_mode = 0o600 if private else 0o644
    path.parent.mkdir(mode=directory_mode, parents=True, exist_ok=True)
    if private:
        path.parent.chmod(directory_mode)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ValueError("existing artifact differs from deterministic payload")
        if path.stat().st_mode & 0o777 != file_mode:
            raise ValueError("existing artifact has an invalid mode")
        return hashlib.sha256(payload).hexdigest()
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(file_mode)
    _fsync_directory(path.parent)
    return hashlib.sha256(payload).hexdigest()


def write_public_json_create_once(path: Path, payload: object) -> str:
    assert_public_payload(payload)
    return _create_once(path, _canonical_json_bytes(payload), private=False)


def write_private_json_create_once(path: Path, payload: object) -> str:
    return _create_once(path, _canonical_json_bytes(payload), private=True)


def write_public_text_create_once(path: Path, payload: str) -> str:
    assert_public_payload(payload)
    return _create_once(path, payload.encode("utf-8"), private=False)
