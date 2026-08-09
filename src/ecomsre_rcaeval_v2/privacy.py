"""Deterministic redaction for local paths in Agent-visible free text."""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SANITIZER_VERSION: Literal["rcaeval-re2-v2-dev1.local-path-sanitizer.v1"] = (
    "rcaeval-re2-v2-dev1.local-path-sanitizer.v1"
)
PathKind = Literal[
    "FILE_URI",
    "WINDOWS_ABSOLUTE",
    "HOME_RELATIVE",
    "POSIX_ABSOLUTE",
]

_SEGMENT = r"[A-Za-z0-9._~@%+()\-]+"
_LOCAL_PATH = re.compile(
    rf"(?P<FILE_URI>file:///(?:{_SEGMENT}/)*{_SEGMENT})"
    rf"|(?P<WINDOWS_ABSOLUTE>(?<![A-Za-z0-9])[A-Za-z]:\\(?:{_SEGMENT}\\)*{_SEGMENT})"
    rf"|(?P<HOME_RELATIVE>(?<![A-Za-z0-9])~/(?:{_SEGMENT}/)*{_SEGMENT})"
    rf"|(?P<POSIX_ABSOLUTE>(?<![A-Za-z0-9:/])/(?:Users|private|home|tmp|var|opt|Volumes)/(?:{_SEGMENT}/)*{_SEGMENT})"
)


class _PrivacyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SanitizedText(_PrivacyModel):
    value: str
    replacement_count: int = Field(ge=0)
    replacement_kinds: tuple[PathKind, ...]
    sanitizer_version: Literal["rcaeval-re2-v2-dev1.local-path-sanitizer.v1"]
    semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LeakageScanResult(_PrivacyModel):
    path_hit_count: int = Field(ge=0)
    path_kinds: tuple[PathKind, ...]
    scanner_version: Literal["rcaeval-re2-v2-dev1.local-path-sanitizer.v1"]


def _kind(match: re.Match[str]) -> PathKind:
    group = match.lastgroup
    if group not in {
        "FILE_URI",
        "WINDOWS_ABSOLUTE",
        "HOME_RELATIVE",
        "POSIX_ABSOLUTE",
    }:
        raise AssertionError("local-path matcher returned an unknown group")
    return group  # type: ignore[return-value]


def _ordered_unique(values: list[PathKind]) -> tuple[PathKind, ...]:
    return tuple(dict.fromkeys(values))


def sanitize_agent_visible_text(text: str) -> SanitizedText:
    """Replace supported local paths with stable, non-reversible tokens."""

    if not isinstance(text, str):
        raise TypeError("Agent-visible text must be a string")
    kinds: list[PathKind] = []
    replacement_count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal replacement_count
        raw_path = match.group(0)
        kinds.append(_kind(match))
        replacement_count += 1
        digest = hashlib.sha256(raw_path.encode("utf-8")).hexdigest()[:12]
        return f"<LOCAL_PATH:{digest}>"

    sanitized = _LOCAL_PATH.sub(replace, text)
    return SanitizedText(
        value=sanitized,
        replacement_count=replacement_count,
        replacement_kinds=_ordered_unique(kinds),
        sanitizer_version=SANITIZER_VERSION,
        semantic_sha256=hashlib.sha256(sanitized.encode("utf-8")).hexdigest(),
    )


def _scan_strings(value: object, kinds: list[PathKind]) -> int:
    if isinstance(value, str):
        matches = tuple(_LOCAL_PATH.finditer(value))
        kinds.extend(_kind(match) for match in matches)
        return len(matches)
    if isinstance(value, dict):
        return sum(
            _scan_strings(key, kinds) + _scan_strings(item, kinds)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return sum(_scan_strings(item, kinds) for item in value)
    return 0


def scan_agent_visible_payload(value: object) -> LeakageScanResult:
    """Return only safe local-path counts and kinds for a nested payload."""

    kinds: list[PathKind] = []
    count = _scan_strings(value, kinds)
    return LeakageScanResult(
        path_hit_count=count,
        path_kinds=_ordered_unique(kinds),
        scanner_version=SANITIZER_VERSION,
    )
