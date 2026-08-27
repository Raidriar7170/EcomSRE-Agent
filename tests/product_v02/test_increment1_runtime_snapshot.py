from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
)
from ecomsre.product.connectors.base import ConnectorQueryContextV1, ConnectorWindowV1
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.pilot_runtime import (
    PilotRuntimeSnapshotV02,
    write_pilot_runtime_snapshot_v02,
)
from ecomsre.product.connectors.registry import ConnectorRegistryV1
from ecomsre.product.contracts import ConnectorConfigV1


NOW = datetime(2026, 8, 28, 1, 0, tzinfo=UTC)


def test_pilot_runtime_snapshot_is_authority_bound_and_observer_safe(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "product"
    snapshot = PilotRuntimeSnapshotV02.build(
        environment_id="env-" + "a" * 24,
        authority_sha256="1" * 64,
        observed_at=NOW,
        services={
            "checkout": {"state": "RUNNING", "healthy": True, "restart_count": 0},
            "fraud-detection": {"state": "RUNNING", "healthy": True, "restart_count": 0},
        },
    )
    path = data_root / "pilot/runtime-snapshot.json"
    write_pilot_runtime_snapshot_v02(path, snapshot)
    assert "kafkaQueueProblems" not in path.read_text(encoding="utf-8")

    config = ConnectorConfigV1.model_validate(
        {
            "name": "pilot-runtime",
            "kind": "PILOT_RUNTIME",
            "settings": {
                "snapshot_ref": "pilot/runtime-snapshot.json",
                "authority_sha256": "1" * 64,
                "maximum_age_seconds": 300,
            },
            "credential_refs": {},
        }
    )
    registry = ConnectorRegistryV1(
        credential_resolver=CredentialResolverV1(),
        timeout_seconds=1,
        data_root=data_root,
    )
    connector = registry.create(config)

    health = connector.verify()
    result = connector.query(
        ConnectorQueryContextV1(
            environment_id="env-" + "a" * 24,
            requested_services=("checkout", "fraud-detection"),
            window=ConnectorWindowV1(
                started_at=NOW - timedelta(seconds=60),
                ended_at=NOW + timedelta(seconds=1),
            ),
            maximum_records=2,
            requested_source=EvidenceSourceV22.RUNTIME,
        )
    )[0]

    assert health.status.value == "AVAILABLE"
    assert health.discovered_services == ("checkout", "fraud-detection")
    assert result.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert result.covered_services == ("checkout", "fraud-detection")
    assert all(item.healthy for item in result.records)

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["services"][0]["healthy"] = False
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert connector.verify().safe_error_code == "PILOT_RUNTIME_SNAPSHOT_INVALID"


def test_running_runtime_without_configured_health_does_not_claim_healthy() -> None:
    snapshot = PilotRuntimeSnapshotV02.build(
        environment_id="env-" + "b" * 24,
        authority_sha256="2" * 64,
        observed_at=NOW,
        services={
            "checkout": {
                "state": "RUNNING",
                "healthy": False,
                "restart_count": 0,
            }
        },
    )

    assert snapshot.services[0].state.value == "RUNNING"
    assert snapshot.services[0].healthy is False
