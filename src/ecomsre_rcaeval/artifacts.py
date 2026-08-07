"""Small canonical JSON and checksum helpers for benchmark artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ecomsre_rcaeval.contracts import ScheduledRun


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"checksum input is not a regular file: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_tree(
    root: Path,
    *,
    include_suffixes: tuple[str, ...] | None = None,
) -> str:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("checksum tree root is invalid")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and include_suffixes is not None:
            if path.suffix not in include_suffixes:
                continue
        if path.is_symlink() or not path.is_file():
            if path.is_dir() and not path.is_symlink():
                continue
            raise ValueError("checksum tree contains a non-regular path")
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        digest.update(b"\0")
    return digest.hexdigest()


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"JSON input is not a regular file: {path.name}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"JSON input is invalid: {path.name}") from error
    if not isinstance(value, dict):
        raise ValueError(f"JSON input must be an object: {path.name}")
    return value


def write_json_create_once(path: Path, value: object) -> str:
    payload = canonical_json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
        path.chmod(0o600)
    except FileExistsError as error:
        raise ValueError(f"JSON output already exists: {path.name}") from error
    return sha256_bytes(payload)


def schedule_payload(schedule: tuple[ScheduledRun, ...]) -> dict[str, object]:
    return {
        "schema_version": "rcaeval-re2.holdout-schedule.v1",
        "records": [item.model_dump(mode="json") for item in schedule],
    }


def load_schedule(path: Path) -> tuple[ScheduledRun, ...]:
    payload = read_json_object(path)
    if set(payload) != {"schema_version", "records"}:
        raise ValueError("holdout schedule has unexpected fields")
    if payload.get("schema_version") != "rcaeval-re2.holdout-schedule.v1":
        raise ValueError("holdout schedule schema version is invalid")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("holdout schedule records are invalid")
    try:
        return tuple(ScheduledRun.model_validate(item) for item in records)
    except ValidationError as error:
        raise ValueError("holdout schedule record is invalid") from error
