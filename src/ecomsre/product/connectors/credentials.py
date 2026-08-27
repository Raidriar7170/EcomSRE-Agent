"""Ephemeral resolution of connector credential references."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
from typing import Mapping


_REFERENCE_PATTERN = re.compile(r"^(?:env:[A-Z_][A-Z0-9_]*|file:/[^\x00]*)$")
_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_BLOCKED_STATIC_HEADERS = {
    "authorization",
    "connection",
    "content-length",
    "cookie",
    "host",
    "proxy-authorization",
    "set-cookie",
    "transfer-encoding",
}
_MAX_SECRET_BYTES = 65_536


class ConnectorCredentialError(RuntimeError):
    """Safe credential resolution failure without reference details."""


@dataclass(frozen=True, repr=False)
class ResolvedHttpHeadersV1:
    _items: tuple[tuple[str, str], ...]

    def as_dict(self) -> dict[str, str]:
        return dict(self._items)

    def __repr__(self) -> str:
        return "<ResolvedHttpHeadersV1 redacted>"


class CredentialResolverV1:
    def __init__(self, *, environment: Mapping[str, str] | None = None) -> None:
        self._environment = os.environ if environment is None else environment

    def resolve_http_headers(
        self,
        references: Mapping[str, str],
    ) -> ResolvedHttpHeadersV1:
        names = set(references)
        allowed_names = {"bearer", "basic_username", "basic_password"}
        unknown = {
            name
            for name in names
            if name not in allowed_names and not name.startswith("header.")
        }
        header_names = tuple(sorted(name for name in names if name.startswith("header.")))
        if unknown or len(header_names) > 16:
            raise ConnectorCredentialError(
                "connector credential configuration is invalid"
            )
        has_bearer = "bearer" in names
        basic_names = names.intersection({"basic_username", "basic_password"})
        if has_bearer and basic_names:
            raise ConnectorCredentialError(
                "connector credential configuration is invalid"
            )
        if basic_names and basic_names != {"basic_username", "basic_password"}:
            raise ConnectorCredentialError(
                "connector credential configuration is invalid"
            )

        headers: dict[str, str] = {}
        if has_bearer:
            headers["Authorization"] = f"Bearer {self._resolve(references['bearer'])}"
        elif basic_names:
            username = self._resolve(references["basic_username"])
            password = self._resolve(references["basic_password"])
            if ":" in username:
                raise ConnectorCredentialError(
                    "connector credential configuration is invalid"
                )
            encoded = base64.b64encode(f"{username}:{password}".encode("utf-8"))
            headers["Authorization"] = f"Basic {encoded.decode('ascii')}"

        for reference_name in header_names:
            header_name = reference_name.removeprefix("header.")
            if (
                not _HEADER_NAME_PATTERN.fullmatch(header_name)
                or header_name.casefold() in _BLOCKED_STATIC_HEADERS
            ):
                raise ConnectorCredentialError(
                    "connector credential configuration is invalid"
                )
            headers[header_name] = self._resolve(references[reference_name])
        return ResolvedHttpHeadersV1(tuple(sorted(headers.items())))

    def _resolve(self, reference: str) -> str:
        if not _REFERENCE_PATTERN.fullmatch(reference):
            raise ConnectorCredentialError(
                "connector credential reference is invalid"
            )
        if reference.startswith("env:"):
            raw_value = self._environment.get(reference.removeprefix("env:"))
            if raw_value is None:
                raise ConnectorCredentialError("connector credential is unavailable")
            value = raw_value
        else:
            path = Path(reference.removeprefix("file:"))
            nofollow = getattr(os, "O_NOFOLLOW", None)
            if not isinstance(nofollow, int):
                raise ConnectorCredentialError(
                    "connector credential file is unavailable"
                )
            flags = os.O_RDONLY | nofollow
            close_on_exec = getattr(os, "O_CLOEXEC", None)
            if isinstance(close_on_exec, int):
                flags |= close_on_exec
            try:
                descriptor = os.open(path, flags)
            except OSError as error:
                raise ConnectorCredentialError(
                    "connector credential file is unavailable"
                ) from error
            try:
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode):
                    raise ConnectorCredentialError(
                        "connector credential file is unavailable"
                    )
                if before.st_size > _MAX_SECRET_BYTES:
                    raise ConnectorCredentialError(
                        "connector credential value is invalid"
                    )
                chunks: list[bytes] = []
                remaining = _MAX_SECRET_BYTES + 1
                while remaining > 0:
                    chunk = os.read(descriptor, min(16_384, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                data = b"".join(chunks)
                after = os.fstat(descriptor)
                before_signature = (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                after_signature = (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                )
                if before_signature != after_signature:
                    raise ConnectorCredentialError(
                        "connector credential file is unavailable"
                    )
            except OSError as error:
                raise ConnectorCredentialError(
                    "connector credential file is unavailable"
                ) from error
            finally:
                os.close(descriptor)
            if len(data) > _MAX_SECRET_BYTES:
                raise ConnectorCredentialError(
                    "connector credential value is invalid"
                )
            try:
                value = data.decode("utf-8").rstrip("\r\n")
            except UnicodeDecodeError as error:
                raise ConnectorCredentialError(
                    "connector credential value is invalid"
                ) from error
        if (
            not value
            or len(value.encode("utf-8")) > _MAX_SECRET_BYTES
            or any(character in value for character in "\r\n\x00")
        ):
            raise ConnectorCredentialError("connector credential value is invalid")
        return value


__all__ = (
    "ConnectorCredentialError",
    "CredentialResolverV1",
    "ResolvedHttpHeadersV1",
)
