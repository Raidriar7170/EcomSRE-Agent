"""Single-token Product mutation authentication."""

from __future__ import annotations

import hmac

from fastapi import Header, Request

from ecomsre.product.errors import ProductError


def require_mutation_auth(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    expected = request.app.state.settings.resolved_admin_token()
    if expected is None:
        return
    scheme, separator, supplied = (authorization or "").partition(" ")
    if (
        separator != " "
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(supplied, expected)
    ):
        raise ProductError(
            "AUTH_REQUIRED",
            "A valid bearer token is required.",
            status_code=401,
        )


__all__ = ("require_mutation_auth",)
