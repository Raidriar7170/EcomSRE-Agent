"""Frozen, explicit service alias normalization for RCAEval outputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


class UnresolvedServiceAlias(ValueError):
    pass


def _key(value: str) -> str:
    return value.strip().casefold()


@dataclass(frozen=True, slots=True)
class ServiceNormalizer:
    canonical_services: tuple[str, ...]
    aliases: Mapping[str, str]

    def __post_init__(self) -> None:
        canonical = {_key(value): value for value in self.canonical_services}
        if len(canonical) != len(self.canonical_services) or not canonical:
            raise ValueError("canonical service set must be unique and nonempty")
        if any(not value or value != _key(value) for value in self.canonical_services):
            raise ValueError("canonical service names must be normalized")
        for alias, target in self.aliases.items():
            if not alias.strip():
                raise ValueError("service alias must not be empty")
            if _key(target) not in canonical:
                raise ValueError("service alias canonical target is not locked")

    def normalize(self, value: str) -> str:
        key = _key(value)
        canonical = {_key(item): item for item in self.canonical_services}
        if key in canonical:
            return canonical[key]
        aliases = {_key(alias): target for alias, target in self.aliases.items()}
        if key in aliases:
            return aliases[key]
        raise UnresolvedServiceAlias(f"unresolved service alias: {value!r}")
