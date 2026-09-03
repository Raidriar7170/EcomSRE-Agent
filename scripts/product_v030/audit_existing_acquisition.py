"""Re-read the captured Phase A window through full acquisition, without an Incident."""

from datetime import UTC, datetime
import json
from pathlib import Path
import re

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.app import create_app
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.pilot_runtime import (
    PilotRuntimeSnapshotV02,
    write_pilot_runtime_snapshot_v02,
)
from ecomsre.product.connectors.registry import ConnectorRegistryV1
from ecomsre.product.incidents.contracts import IncidentRecordV1
from ecomsre.product.incidents.read_backend import ProductReadBackendV1
from ecomsre.product.pilot.runtime_authority_v02 import load_pilot_runtime_authority_v02
from ecomsre.product.settings import ProductSettingsV1
from ecomsre_live_sandbox.contracts import write_private_json


def main():
    root = Path(__file__).resolve().parents[2]
    private = root / ".local/product-v030/live-001"
    setup = json.loads(
        (private / "baseline-setup-resumed/baseline-result.json").read_text()
    )
    original = json.loads((private / "phase-a-resumed.json").read_text())
    data = private / "product-formal"
    app = create_app(ProductSettingsV1(data_root=data))
    env_id = setup["environment"]["environment_id"]
    environment = app.state.environments.get(env_id)
    identity = app.state.services.get_map(env_id)
    capabilities = app.state.capabilities.get(env_id)
    baseline = app.state.baselines.get_active(env_id)
    authority = load_pilot_runtime_authority_v02(data / "pilot/runtime-authority.json")
    captured = private / "phase-a-full-acquisition"
    runtime = original["runtime_during"]
    snapshot = PilotRuntimeSnapshotV02.build(
        environment_id=env_id,
        authority_sha256=authority.connector_binding_sha256,
        observed_at=datetime.fromisoformat(runtime["observed_at"]),
        services=runtime["services"],
    )
    write_pilot_runtime_snapshot_v02(
        captured / "pilot/runtime-readiness.json", snapshot
    )
    window = original["product_metrics"]["window"]
    candidates = ("checkout", "fraud-detection", "kafka")
    payload = {
        "schema_version": "ecomsre.product.incident.v1",
        "incident_id": "inc-" + "0" * 24,
        "environment_id": env_id,
        "baseline_id": baseline.baseline_id,
        "baseline_sha256": baseline.baseline_sha256,
        "service_identity_sha256": identity.identity_sha256,
        "source_capability_sha256": capabilities.capability_sha256,
        "external_incident_key": "ephemeral-read-preflight-not-persisted",
        "alert_name": "bounded-telemetry-read",
        "summary": "Re-read captured telemetry only; no diagnosis or Incident persistence.",
        "started_at": datetime.fromisoformat(window["started_at"]),
        "ended_at": datetime.fromisoformat(window["ended_at"]),
        "diagnosis_observed_at": datetime.fromisoformat(window["ended_at"]),
        "created_at": datetime.now(UTC),
        "candidate_service_ids": tuple(
            sorted(
                item.service_id
                for item in identity.services
                if item.logical_service in candidates
            )
        ),
        "candidate_logical_services": candidates,
        "labels": {"fault": "synthetic-unknown"},
    }
    draft = IncidentRecordV1.model_validate(
        {
            **payload,
            "incident_sha256": semantic_sha256_v22(
                IncidentRecordV1.model_construct(
                    **payload, incident_sha256="0" * 64
                ).model_dump(mode="json", exclude={"incident_sha256"})
            ),
        }
    )
    backend = ProductReadBackendV1(
        connectors=ConnectorRegistryV1(
            credential_resolver=CredentialResolverV1(),
            timeout_seconds=15,
            data_root=captured,
        ),
        changes=app.state.changes,
        metrics=app.state.metrics,
        pilot_runtime_authority=authority,
    )
    acquisition = backend.acquire(
        incident=draft,
        environment=environment,
        identity_map=identity,
        capability_matrix=capabilities,
        topology_edges=(),
    )
    visible = {
        "raw_outcomes": [
            item.model_dump(mode="json") for item in acquisition.raw_outcomes
        ],
        "memory_outcomes": [
            item.model_dump(mode="json") for item in acquisition.memory_outcomes
        ],
        "snapshots": list(acquisition.snapshots),
        "capability_observations": [
            item.model_dump(mode="json")
            for item in acquisition.capability_observations_v0232
        ],
    }
    leaked = sorted(
        set(
            re.findall(
                r"kafkaQueueProblems|paymentFailure|defaultVariant|feature\s*flag|\.flagd\.json|\.local/product-v030|overload simulation",
                json.dumps(visible),
                re.I,
            )
        )
    )
    result = {
        "status": "PASS" if not leaked else "FAIL",
        "leaked_tokens": leaked,
        "incident_count_created": 0,
        "diagnosis_count_created": 0,
        "new_fault_count": 0,
        "window": window,
        "runtime_basis": "CAPTURED_ACTUAL_PHASE_A_OBSERVATION",
        "capability_limitations": list(acquisition.capability_limitations),
        "evidence": visible,
    }
    write_private_json(
        private / "pre-p1-acquisition-leakage.json", result, create_once=True
    )
    print(
        json.dumps({key: value for key, value in result.items() if key != "evidence"})
    )


if __name__ == "__main__":
    main()
