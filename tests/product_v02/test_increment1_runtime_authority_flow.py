from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from ecomsre.product.app import create_app
from ecomsre.product.connectors.pilot_runtime import (
    PilotRuntimeSnapshotV02,
    write_pilot_runtime_snapshot_v02,
)
from ecomsre.product.jobs.worker import run_one_job
from ecomsre.product.contracts import EnvironmentRecordV1
from ecomsre.product.incidents.read_backend import ProductReadBackendV1
from ecomsre.product.pilot.runtime_authority_v02 import (
    PilotRuntimeAuthorityV02,
    write_pilot_runtime_authority_v02,
)
from ecomsre.product.settings import ProductSettingsV1


def test_runtime_authority_rejects_mismatched_environment_binding() -> None:
    authority = PilotRuntimeAuthorityV02.build(
        environment_id="env-" + "a" * 24,
        allowed_logical_services=("payment",),
        profile_sha256="1" * 64,
        daemon_identity_sha256="2" * 64,
        docker_context_sha256="3" * 64,
        config_bundle_sha256="4" * 64,
        resolved_sandbox_sha256="5" * 64,
        resolved_endpoints_sha256="6" * 64,
        ownership_scope_sha256="7" * 64,
    )
    backend = ProductReadBackendV1.__new__(ProductReadBackendV1)
    backend._pilot_runtime_authority = authority
    environment = EnvironmentRecordV1.model_validate(
        {
            "environment_id": authority.environment_id,
            "name": "mismatched-runtime-binding",
            "created_at": "2026-08-28T00:00:00Z",
            "updated_at": "2026-08-28T00:00:00Z",
            "connector_configs": [
                {
                    "name": "pilot-runtime",
                    "kind": "PILOT_RUNTIME",
                    "settings": {
                        "snapshot_ref": "pilot/runtime-snapshot.json",
                        "authority_sha256": "f" * 64,
                    },
                    "credential_refs": {},
                }
            ],
            "explicit_service_catalog": ["payment"],
        }
    )

    assert (
        backend._pilot_runtime_admitted(
            environment=environment,
            services=("payment",),
        )
        is False
    )


def test_v02_owned_runtime_authority_enables_open_world_without_weakening_default(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "product"
    authority_path = data_root / "pilot/runtime-authority.json"
    authority_inputs = {
        "allowed_logical_services": ("payment",),
        "profile_sha256": "1" * 64,
        "daemon_identity_sha256": "2" * 64,
        "docker_context_sha256": "3" * 64,
        "config_bundle_sha256": "4" * 64,
        "resolved_sandbox_sha256": "5" * 64,
        "resolved_endpoints_sha256": "6" * 64,
        "ownership_scope_sha256": "7" * 64,
    }
    prebound = PilotRuntimeAuthorityV02.build(
        environment_id="env-" + "0" * 24,
        **authority_inputs,
    )
    runtime_authority_sha256 = prebound.connector_binding_sha256
    settings = ProductSettingsV1(
        data_root=data_root,
        pilot_runtime_authority_path=authority_path,
    )
    with TestClient(create_app(settings)) as client:
        environment = client.post(
            "/v1/environments",
            json={
                "name": "v02-owned-runtime",
                "description": "Fixture anomalies plus an authority-bound Runtime snapshot.",
                "timezone": "UTC",
                "service_identity_policy": {
                    "services": [{"logical_service": "payment"}]
                },
                "connector_configs": [
                    {
                        "name": "fixture",
                        "kind": "FIXTURE",
                        "settings": {"dataset": "capture-c2aa"},
                        "credential_refs": {},
                    },
                    {
                        "name": "pilot-runtime",
                        "kind": "PILOT_RUNTIME",
                        "settings": {
                            "snapshot_ref": "pilot/runtime-snapshot.json",
                            "authority_sha256": runtime_authority_sha256,
                            "maximum_age_seconds": 300,
                        },
                        "credential_refs": {},
                    },
                ],
                "explicit_service_catalog": ["payment"],
            },
        )
        assert environment.status_code == 201, environment.text
        environment_id = environment.json()["environment_id"]
        authority = PilotRuntimeAuthorityV02.build(
            environment_id=environment_id,
            **authority_inputs,
        )
        assert authority.connector_binding_sha256 == runtime_authority_sha256
        write_pilot_runtime_authority_v02(authority_path, authority)
        snapshot = PilotRuntimeSnapshotV02.build(
            environment_id=environment_id,
            authority_sha256=runtime_authority_sha256,
            observed_at=datetime.now(UTC),
            services={
                "payment": {
                    "state": "RUNNING",
                    "healthy": True,
                    "restart_count": 0,
                }
            },
        )
        snapshot_path = data_root / "pilot/runtime-snapshot.json"
        write_pilot_runtime_snapshot_v02(snapshot_path, snapshot)

        verify = client.post(f"/v1/environments/{environment_id}/verify-jobs")
        assert verify.status_code == 202
        assert run_one_job(settings, worker_id="v02-runtime-verify") is True
        verified = client.get(f"/v1/jobs/{verify.json()['job_id']}").json()
        assert verified["status"] == "SUCCEEDED", verified
        service = next(
            item
            for item in verified["result"]["service_identity_map"]["services"]
            if item["logical_service"] == "payment"
        )

        baseline = client.post(
            f"/v1/environments/{environment_id}/baseline-jobs",
            json={"activate": True},
        )
        assert baseline.status_code == 202
        assert run_one_job(settings, worker_id="v02-runtime-baseline") is True
        baseline_job = client.get(f"/v1/jobs/{baseline.json()['job_id']}").json()
        assert baseline_job["status"] == "SUCCEEDED", baseline_job

        incident = client.post(
            "/v1/incidents",
            json={
                "environment_id": environment_id,
                "external_incident_key": "v02-authorized-runtime-open-world",
                "alert_name": "payment-observation",
                "summary": "Bounded v0.2 observation.",
                "started_at": datetime.now(UTC).isoformat(),
                "candidate_service_ids": [service["service_id"]],
                "labels": {"source": "product-v02-test"},
            },
        )
        assert incident.status_code == 201, incident.text
        queued = client.post(
            f"/v1/incidents/{incident.json()['incident_id']}/diagnosis-jobs"
        )
        assert queued.status_code == 202
        assert run_one_job(settings, worker_id="v02-runtime-diagnosis") is True
        diagnosis = client.get(
            f"/v1/incidents/{incident.json()['incident_id']}/diagnosis"
        )
        assert diagnosis.status_code == 200, diagnosis.text
        result = diagnosis.json()
        assert result["terminal"] == "OPEN_WORLD"
        assert "RUNTIME_MEMORY_AUTHORITY_UNAVAILABLE" not in result["capability_limitations"]
        assert "RUNTIME_DIAGNOSIS_UNAVAILABLE" not in result["capability_limitations"]
        assert result["action_authority"] == "NONE"
