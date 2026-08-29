from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from typing import Literal, Mapping, Sequence

from pydantic import Field, model_validator

from ecomsre.product.pilot.contracts_v02 import PilotModelV02, semantic_sha256_v02


class BaselineRecoveryResultV02(PilotModelV02):
    schema_version: Literal["ecomsre.product.pilot-baseline-recovery.v02"] = (
        "ecomsre.product.pilot-baseline-recovery.v02"
    )
    environment_id: str
    expected_baseline_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_flag_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_restored: bool
    traffic_stopped: bool
    connector_health: tuple[str, ...]
    owned_drift_refs: tuple[str, ...]
    status: Literal["PASS", "FAIL"]
    observed_at: datetime
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_result(self) -> "BaselineRecoveryResultV02":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("baseline recovery timestamp must be UTC")
        if self.connector_health != tuple(sorted(set(self.connector_health))):
            raise ValueError("connector health must be canonical")
        if self.owned_drift_refs != tuple(sorted(set(self.owned_drift_refs))):
            raise ValueError("owned drift refs must be canonical")
        passed = (
            self.baseline_restored
            and self.traffic_stopped
            and all(item.endswith(":HEALTHY") for item in self.connector_health)
            and not self.owned_drift_refs
        )
        if (self.status == "PASS") is not passed:
            raise ValueError("baseline recovery status differs from evidence")
        expected = semantic_sha256_v02(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("baseline recovery digest differs")
        return self


def verify_baseline_recovery_v02(
    *,
    environment_id: str,
    expected_baseline_sha256: str,
    current_flag_bytes: bytes,
    connector_health: Mapping[str, bool],
    traffic_active: bool,
    owned_drift_refs: Sequence[str],
    observed_at: datetime | None = None,
) -> BaselineRecoveryResultV02:
    observed_sha256 = hashlib.sha256(current_flag_bytes).hexdigest()
    normalized_health = tuple(
        sorted(
            f"{name}:{'HEALTHY' if healthy else 'UNHEALTHY'}"
            for name, healthy in connector_health.items()
        )
    )
    normalized_drift = tuple(sorted(set(owned_drift_refs)))
    observed_timestamp = observed_at or datetime.now(UTC)
    payload: dict[str, object] = {
        "schema_version": "ecomsre.product.pilot-baseline-recovery.v02",
        "environment_id": environment_id,
        "expected_baseline_sha256": expected_baseline_sha256,
        "observed_flag_sha256": observed_sha256,
        "baseline_restored": observed_sha256 == expected_baseline_sha256,
        "traffic_stopped": not traffic_active,
        "connector_health": normalized_health,
        "owned_drift_refs": normalized_drift,
        "status": (
            "PASS"
            if observed_sha256 == expected_baseline_sha256
            and not traffic_active
            and all(connector_health.values())
            and not normalized_drift
            else "FAIL"
        ),
        "observed_at": observed_timestamp,
    }
    digest_payload = {
        **payload,
        "connector_health": list(normalized_health),
        "owned_drift_refs": list(normalized_drift),
        "observed_at": observed_timestamp.isoformat().replace("+00:00", "Z"),
    }
    return BaselineRecoveryResultV02.model_validate(
        {
            **payload,
            "result_sha256": semantic_sha256_v02(digest_payload),
        }
    )


__all__ = ("BaselineRecoveryResultV02", "verify_baseline_recovery_v02")
