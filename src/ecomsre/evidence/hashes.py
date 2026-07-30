"""Deterministic SHA-256 helpers for immutable evidence."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any


class StrictJSONError(ValueError):
    """A value cannot cross the canonical evidence JSON boundary."""


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    _validate_strict_json(value, active_containers=set())
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as error:
        raise StrictJSONError("STRICT_JSON_INVALID") from error


def canonical_json_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_strict_json(
    value: Any,
    *,
    active_containers: set[int],
) -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StrictJSONError("NON_FINITE_JSON_VALUE")
        return
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, dict):
        identity = id(value)
        if identity in active_containers:
            raise StrictJSONError("STRICT_JSON_CYCLE")
        if any(not isinstance(key, str) for key in value):
            raise StrictJSONError("STRICT_JSON_KEY_NOT_STRING")
        active_containers.add(identity)
        try:
            for nested in value.values():
                _validate_strict_json(
                    nested,
                    active_containers=active_containers,
                )
        finally:
            active_containers.remove(identity)
        return
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active_containers:
            raise StrictJSONError("STRICT_JSON_CYCLE")
        active_containers.add(identity)
        try:
            for nested in value:
                _validate_strict_json(
                    nested,
                    active_containers=active_containers,
                )
        finally:
            active_containers.remove(identity)
        return
    raise StrictJSONError(f"STRICT_JSON_INVALID_TYPE:{type(value).__name__}")
