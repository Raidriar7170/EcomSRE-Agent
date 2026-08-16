"""Trusted projection of freshly observed local Sandbox state."""

from __future__ import annotations

from ecomsre.dta_v2.authorization import MasterAuthorizationRecord
from ecomsre.dta_v2.operational_contracts import (
    CurrentStateSnapshot,
    DockerBoundary,
    OwnershipStatus,
)
from ecomsre.dta_v2.registry import RunbookRegistry


def require_trusted_live_current_state(
    *,
    snapshot: CurrentStateSnapshot,
    registry: RunbookRegistry,
    master_authorization: MasterAuthorizationRecord,
    expected_run_id: str,
    expected_attempt_id: str,
    authoritative_target: str,
) -> CurrentStateSnapshot:
    """Revalidate and admit only an owned local, exactly bound state snapshot."""

    snapshot = CurrentStateSnapshot.model_validate(snapshot.model_dump(mode="python"))
    registry = RunbookRegistry.model_validate(registry.model_dump(mode="python"))
    master_authorization = MasterAuthorizationRecord.model_validate(
        master_authorization.model_dump(mode="python")
    )
    if snapshot.docker_boundary is not DockerBoundary.LOCAL_UNIX:
        raise ValueError("live state requires the local Unix Docker boundary")
    if snapshot.ownership_status is not OwnershipStatus.PROVEN:
        raise ValueError("live state requires proven Sandbox ownership")
    if master_authorization.registry_sha256 != registry.registry_sha256:
        raise ValueError("live state registry differs from Master Authorization")
    if snapshot.sandbox_identity != master_authorization.sandbox_identity:
        raise ValueError("live state Sandbox identity differs from authorization")
    if (
        snapshot.run_id != expected_run_id
        or snapshot.attempt_id != expected_attempt_id
        or snapshot.target_logical_service != authoritative_target
    ):
        raise ValueError("live state run, attempt, or target binding differs")
    authorized_targets = {
        scope.target_service for scope in master_authorization.authorized_runbooks
    }
    if authoritative_target not in authorized_targets:
        raise ValueError("live state target is outside the authorized Registry")
    return snapshot
