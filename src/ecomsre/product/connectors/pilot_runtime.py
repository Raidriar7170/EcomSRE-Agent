from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import stat
import time
from typing import Literal, Mapping

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    RuntimeRecordV22,
    RuntimeStateV22,
)
from ecomsre.product.connectors.base import (
    ConnectorAvailabilityV1,
    ConnectorCapabilityV1,
    ConnectorHealthResultV1,
    ConnectorQueryContextV1,
    ConnectorQueryResultV1,
)
from ecomsre.product.contracts import (
    ConnectorConfigV1,
    ConnectorKindV1,
    PilotRuntimeConnectorSettingsV02,
    ProductModelV1,
)
from ecomsre.product.pilot.contracts_v02 import semantic_sha256_v02


class PilotRuntimeServiceV02(ProductModelV1):
    logical_service: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    state: RuntimeStateV22
    healthy: bool
    restart_count: int = Field(ge=0, le=1000)


class PilotRuntimeSnapshotV02(ProductModelV1):
    schema_version: Literal["ecomsre.product.pilot.runtime-snapshot.v02"]
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    services: tuple[PilotRuntimeServiceV02, ...] = Field(min_length=1, max_length=4)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_snapshot(self) -> "PilotRuntimeSnapshotV02":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("pilot Runtime snapshot timestamp must be UTC")
        names = tuple(item.logical_service for item in self.services)
        if names != tuple(sorted(set(names))):
            raise ValueError("pilot Runtime snapshot services must be canonical")
        if any(
            item.state is not RuntimeStateV22.RUNNING and item.healthy
            for item in self.services
        ):
            raise ValueError("non-running pilot Runtime service cannot be healthy")
        expected = semantic_sha256_v02(
            self.model_dump(mode="json", exclude={"snapshot_sha256"})
        )
        if self.snapshot_sha256 != expected:
            raise ValueError("pilot Runtime snapshot digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        environment_id: str,
        authority_sha256: str,
        observed_at: datetime,
        services: Mapping[str, Mapping[str, object]],
    ) -> "PilotRuntimeSnapshotV02":
        normalized_services = tuple(
            PilotRuntimeServiceV02.model_validate(
                {"logical_service": name, **details}
            )
            for name, details in sorted(services.items())
        )
        payload: dict[str, object] = {
            "schema_version": "ecomsre.product.pilot.runtime-snapshot.v02",
            "environment_id": environment_id,
            "authority_sha256": authority_sha256,
            "observed_at": observed_at,
            "services": normalized_services,
        }
        draft = cls.model_construct(
            **payload,  # type: ignore[arg-type]
            snapshot_sha256="0" * 64,
        )
        return cls.model_validate(
            {
                **payload,
                "snapshot_sha256": semantic_sha256_v02(
                    draft.model_dump(mode="json", exclude={"snapshot_sha256"})
                ),
            }
        )


def _read_snapshot(path: Path) -> PilotRuntimeSnapshotV02:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("pilot Runtime snapshot is not a regular file")
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            payload = handle.read()
    finally:
        os.close(descriptor)
    return PilotRuntimeSnapshotV02.model_validate_json(payload)


def write_pilot_runtime_snapshot_v02(
    path: Path,
    snapshot: PilotRuntimeSnapshotV02,
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            json.dump(
                snapshot.model_dump(mode="json"),
                handle,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


class PilotRuntimeConnectorV02:
    def __init__(self, config: ConnectorConfigV1, *, data_root: Path) -> None:
        if config.kind is not ConnectorKindV1.PILOT_RUNTIME:
            raise ValueError("pilot Runtime connector configuration is invalid")
        self.config = config
        self._settings = PilotRuntimeConnectorSettingsV02.model_validate(config.settings)
        self._data_root = Path(data_root).resolve()
        self._snapshot_path = (self._data_root / self._settings.snapshot_ref).resolve()
        if not self._snapshot_path.is_relative_to(self._data_root):
            raise ValueError("pilot Runtime snapshot escapes Product data root")

    def capabilities(self) -> tuple[ConnectorCapabilityV1, ...]:
        return (
            ConnectorCapabilityV1(
                source=EvidenceSourceV22.RUNTIME,
                supports_historical_range=False,
                supports_multi_target=True,
                supports_service_discovery=True,
                supports_baseline=False,
                supports_target_complete_coverage=True,
                maximum_window_seconds=600,
            ),
        )

    def _snapshot(self) -> PilotRuntimeSnapshotV02:
        snapshot = _read_snapshot(self._snapshot_path)
        if snapshot.authority_sha256 != self._settings.authority_sha256:
            raise ValueError("pilot Runtime snapshot authority differs")
        return snapshot

    def verify(self) -> ConnectorHealthResultV1:
        started = time.monotonic()
        try:
            snapshot = self._snapshot()
            services = tuple(item.logical_service for item in snapshot.services)
            status = ConnectorAvailabilityV1.AVAILABLE
            error = None
        except (OSError, ValueError):
            services = ()
            status = ConnectorAvailabilityV1.UNAVAILABLE
            error = "PILOT_RUNTIME_SNAPSHOT_INVALID"
        return ConnectorHealthResultV1(
            connector_name=self.config.name,
            kind=self.config.kind,
            status=status,
            capabilities=self.capabilities(),
            discovered_services=services,
            safe_error_code=error,
            latency_ms=max(0.0, (time.monotonic() - started) * 1000),
        )

    def query(
        self,
        context: ConnectorQueryContextV1,
    ) -> tuple[ConnectorQueryResultV1, ...]:
        if context.requested_source not in {None, EvidenceSourceV22.RUNTIME}:
            return ()
        started = time.monotonic()
        try:
            snapshot = self._snapshot()
            if (
                snapshot.environment_id != context.environment_id
                or snapshot.observed_at > context.window.ended_at
                or context.window.ended_at - snapshot.observed_at
                > timedelta(seconds=self._settings.maximum_age_seconds)
            ):
                raise ValueError("pilot Runtime snapshot scope or freshness differs")
            by_service = {item.logical_service: item for item in snapshot.services}
            if not set(context.requested_services).issubset(by_service):
                raise ValueError("pilot Runtime snapshot target coverage differs")
            records = tuple(
                RuntimeRecordV22(
                    schema_version="dta-v22.runtime-record.v1",
                    service=service,
                    state=by_service[service].state,
                    healthy=by_service[service].healthy,
                    restart_count=by_service[service].restart_count,
                )
                for service in context.requested_services
            )
            result = ConnectorQueryResultV1.build(
                source=EvidenceSourceV22.RUNTIME,
                status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
                requested_services=context.requested_services,
                covered_services=context.requested_services,
                window=context.window,
                records=records,
                truncated=False,
                safe_error_code=None,
                latency_ms=max(0.0, (time.monotonic() - started) * 1000),
            )
        except (OSError, ValueError):
            result = ConnectorQueryResultV1.build(
                source=EvidenceSourceV22.RUNTIME,
                status=ReadSourceStatusV22.FAILURE_SCHEMA,
                requested_services=context.requested_services,
                covered_services=(),
                window=context.window,
                records=(),
                truncated=False,
                safe_error_code="PILOT_RUNTIME_SNAPSHOT_INVALID",
                latency_ms=max(0.0, (time.monotonic() - started) * 1000),
            )
        return (result,)

    def close(self) -> None:
        return None


__all__ = (
    "PilotRuntimeConnectorV02",
    "PilotRuntimeSnapshotV02",
    "write_pilot_runtime_snapshot_v02",
)
