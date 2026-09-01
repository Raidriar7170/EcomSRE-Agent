"""Canonical JSON sealing helpers for Product v0.2.3.3 artifacts."""

from __future__ import annotations

from typing import Any

from pydantic_core import to_jsonable_python

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22


def canonical_jsonable_v0233(value: Any) -> Any:
    """Return the stable JSON-domain value persisted by Product v0.2.3.3."""

    return to_jsonable_python(value)


def semantic_json_sha256_v0233(value: Any) -> str:
    """Hash the same JSON-compatible representation used by Pydantic JSON dumps."""

    return semantic_sha256_v22(canonical_jsonable_v0233(value))


__all__ = ("canonical_jsonable_v0233", "semantic_json_sha256_v0233")
