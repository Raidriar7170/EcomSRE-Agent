"""Narrow private Provider environment parsing for the DTA v2 live CLI."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat


_PROVIDER_KEYS = frozenset(
    {
        "ECOMSRE_LLM_BASE_URL",
        "ECOMSRE_LLM_API_KEY",
        "ECOMSRE_LLM_MODEL",
    }
)
_SAFE_VALUE = re.compile(r"^[\x21-\x7e]{1,4096}$")
_SHELL_TOKENS = ("$", "`", "'", '"', "\\", ";", "|", "&", "<", ">")


def load_private_provider_env(path: Path) -> dict[str, str]:
    """Read exactly three non-shell variables from an owned 0600 file."""

    target = Path(path)
    try:
        descriptor = os.open(
            target,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError:
        raise ValueError("Provider env must be a regular non-symlink file") from None
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise ValueError("Provider env must be a regular non-symlink file")
        if stat.S_IMODE(details.st_mode) != 0o600:
            raise ValueError("Provider env mode must be exactly 0600")
        if details.st_uid != os.getuid():
            raise ValueError("Provider env owner differs from the current user")
        if details.st_size > 16_384:
            raise ValueError("Provider env exceeds the bounded file size")
        raw = os.read(descriptor, 16_385)
        if len(raw) > 16_384:
            raise ValueError("Provider env exceeds the bounded file size")
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Provider env is not strict UTF-8") from None
    finally:
        os.close(descriptor)

    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export ") or "=" not in line:
            raise ValueError("Provider env contains unsupported shell syntax")
        key, value = line.split("=", 1)
        if (
            key not in _PROVIDER_KEYS
            or key in values
            or _SAFE_VALUE.fullmatch(value) is None
            or any(token in value for token in _SHELL_TOKENS)
        ):
            raise ValueError("Provider env contains an unknown, duplicate, or unsafe value")
        values[key] = value
    if set(values) != _PROVIDER_KEYS:
        raise ValueError("Provider env does not contain exactly three variables")
    return values


__all__ = ["load_private_provider_env"]
