"""Product settings with a fail-closed network default."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr, model_validator


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _default_data_root() -> Path:
    return Path.home() / ".local" / "share" / "ecomsre-product"


class ProductSettingsV1(BaseModel):
    _admin_token_snapshot: str | None = PrivateAttr(default=None)

    data_root: Path = Field(default_factory=_default_data_root)
    sqlite_path: Path = Field(
        default_factory=lambda: _default_data_root() / "product.sqlite3"
    )
    object_store_root: Path = Field(
        default_factory=lambda: _default_data_root() / "objects"
    )
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8080, ge=1, le=65535)
    admin_token_env: str = "ECOMSRE_ADMIN_TOKEN"
    worker_poll_seconds: float = Field(default=1, gt=0, le=60)
    job_lease_seconds: int = Field(default=300, ge=1, le=3600)
    connector_timeout_seconds: float = Field(default=10, gt=0, le=60)
    maximum_candidate_services: int = Field(default=20, ge=1, le=20)
    maximum_evidence_records_per_source: int = Field(default=200, ge=1, le=200)
    default_baseline_lookback_seconds: int = Field(default=3600, ge=1, le=3600)
    default_baseline_window_count: int = Field(default=6, ge=1, le=60)
    fault_family_similarity_threshold: float = Field(default=0.65, ge=0, le=1)
    fault_family_review_min_occurrences: int = Field(default=2, ge=2)
    rule_miner_beam_width: int = Field(default=20, ge=1, le=100)
    rule_miner_max_clause_size: int = Field(default=3, ge=1, le=3)

    @model_validator(mode="before")
    @classmethod
    def derive_storage_paths(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        resolved = dict(value)
        data_root = Path(resolved.get("data_root", _default_data_root()))
        resolved.setdefault("sqlite_path", data_root / "product.sqlite3")
        resolved.setdefault("object_store_root", data_root / "objects")
        return resolved

    @model_validator(mode="after")
    def resolve_paths_and_network_boundary(self) -> "ProductSettingsV1":
        self.data_root = self.data_root.expanduser().resolve()
        self.sqlite_path = self.sqlite_path.expanduser().resolve()
        self.object_store_root = self.object_store_root.expanduser().resolve()
        token = os.environ.get(self.admin_token_env)
        self._admin_token_snapshot = token if token is not None and token.strip() else None
        if self._admin_token_snapshot is None and self.api_host not in _LOOPBACK_HOSTS:
            raise ValueError("non-loopback Product binding requires an admin token")
        return self

    def resolved_admin_token(self) -> str | None:
        return self._admin_token_snapshot

    @classmethod
    def from_environment(cls) -> "ProductSettingsV1":
        data_root = Path(os.environ.get("ECOMSRE_PRODUCT_DATA_ROOT", _default_data_root()))
        values: dict[str, object] = {"data_root": data_root}
        if path := os.environ.get("ECOMSRE_PRODUCT_SQLITE_PATH"):
            values["sqlite_path"] = Path(path)
        if path := os.environ.get("ECOMSRE_PRODUCT_OBJECT_STORE_ROOT"):
            values["object_store_root"] = Path(path)
        if host := os.environ.get("ECOMSRE_PRODUCT_API_HOST"):
            values["api_host"] = host
        if port := os.environ.get("ECOMSRE_PRODUCT_API_PORT"):
            values["api_port"] = int(port)
        if token_env := os.environ.get("ECOMSRE_PRODUCT_ADMIN_TOKEN_ENV"):
            values["admin_token_env"] = token_env
        return cls.model_validate(values)


__all__ = ("ProductSettingsV1",)
