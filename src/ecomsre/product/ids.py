"""Opaque Product identifiers."""

from __future__ import annotations

import secrets


def new_product_id(prefix: str) -> str:
    if not prefix.isalpha() or not prefix.islower():
        raise ValueError("product ID prefix must be lowercase ASCII letters")
    return f"{prefix}-{secrets.token_hex(12)}"


__all__ = ("new_product_id",)
