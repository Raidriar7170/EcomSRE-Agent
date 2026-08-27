"""Stable Product error contracts."""

from __future__ import annotations

from typing import Any


class ProductError(Exception):
    """A safe error that can cross the Product API boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def not_found(code: str, message: str) -> ProductError:
    return ProductError(code, message, status_code=404)


__all__ = ("ProductError", "not_found")
