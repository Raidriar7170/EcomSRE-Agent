from __future__ import annotations

import re


_TOKEN_V02 = re.compile(r"[a-z][a-z0-9-]{2,}")
_STOP_TOKENS_V02 = frozenset(
    {"and", "are", "bounded", "detected", "for", "from", "the", "this", "with"}
)
_PRIVATE_TOKENS_V02 = frozenset(
    {
        "calibration",
        "expected-family",
        "fit",
        "fit-negative",
        "fit-positive",
        "heldout-recurrence",
        "kafkaqueueproblems",
        "live-known-negative",
        "live-no-fault-negative",
        "positive-fit",
        "positive-shadow",
        "positive",
        "private-control",
        "review-decision",
        "shadow-negative",
        "shadow-positive",
        "shadow",
    }
)


def normalized_observed_log_tokens_v02(message: str) -> tuple[str, ...]:
    """Return only stable symptom tokens suitable for fingerprinting or Provider input."""

    tokens = {
        token
        for token in _TOKEN_V02.findall(message.casefold())
        if token not in _STOP_TOKENS_V02
        and token not in _PRIVATE_TOKENS_V02
        and not any(character.isdigit() for character in token)
    }
    return tuple(sorted(tokens))


__all__ = ("normalized_observed_log_tokens_v02",)
