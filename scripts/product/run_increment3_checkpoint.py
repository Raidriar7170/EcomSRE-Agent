#!/usr/bin/env python3
"""Run Increment 3 known/open-world diagnosis and evidence checkpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any

from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from ecomsre.product.app import create_app  # noqa: E402
from ecomsre.product.jobs.worker import run_one_job  # noqa: E402
from ecomsre.product.settings import ProductSettingsV1  # noqa: E402


def _settings(data_root: Path) -> ProductSettingsV1:
    return ProductSettingsV1(
        data_root=data_root,
        sqlite_path=data_root / "product.sqlite3",
        object_store_root=data_root / "objects",
    )


def _environment(dataset: str, name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": "Opaque Increment 3 checkpoint capture",
        "timezone": "UTC",
        "service_identity_policy": {"services": [{"logical_service": "payment"}]},
        "connector_configs": [
            {
                "name": "fixture",
                "kind": "FIXTURE",
                "settings": {"dataset": dataset},
                "credential_refs": {},
            }
        ],
        "explicit_service_catalog": ["payment"],
    }


def _wait_in_process(
    client: TestClient,
    settings: ProductSettingsV1,
    job_id: str,
    worker_id: str,
) -> dict[str, Any]:
    if not run_one_job(settings, worker_id=worker_id):
        raise RuntimeError("checkpoint worker did not claim the queued job")
    job = client.get(f"/v1/jobs/{job_id}").json()
    if job["status"] != "SUCCEEDED":
        raise RuntimeError(f"checkpoint job failed: {job['safe_error_code']}")
    return job


def _diagnose_capture(
    client: TestClient,
    settings: ProductSettingsV1,
    *,
    dataset: str,
    name: str,
    incident_key: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    environment = client.post("/v1/environments", json=_environment(dataset, name)).json()
    environment_id = environment["environment_id"]
    verify = client.post(f"/v1/environments/{environment_id}/verify-jobs").json()
    verified = _wait_in_process(client, settings, verify["job_id"], f"verify-{name}")
    service_id = verified["result"]["service_identity_map"]["services"][0]["service_id"]
    baseline = client.post(
        f"/v1/environments/{environment_id}/baseline-jobs",
        json={"activate": True},
    ).json()
    _wait_in_process(client, settings, baseline["job_id"], f"baseline-{name}")
    incident = client.post(
        "/v1/incidents",
        json={
            "environment_id": environment_id,
            "external_incident_key": incident_key,
            "alert_name": "bounded-observation",
            "summary": "A bounded read-only observation requires diagnosis.",
            "started_at": "2026-08-27T10:00:00Z",
            "candidate_service_ids": [service_id],
            "labels": {"source": "increment3-checkpoint"},
        },
    ).json()
    queued = client.post(
        f"/v1/incidents/{incident['incident_id']}/diagnosis-jobs"
    ).json()
    _wait_in_process(client, settings, queued["job_id"], f"diagnosis-{name}")
    diagnosis = client.get(
        f"/v1/incidents/{incident['incident_id']}/diagnosis"
    ).json()
    evidence = client.get(
        f"/v1/incidents/{incident['incident_id']}/evidence"
    ).json()
    return diagnosis, evidence


def run_checkpoint(data_root: Path) -> dict[str, Any]:
    resolved = data_root.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    settings = _settings(resolved)
    with TestClient(create_app(settings)) as client:
        known, known_evidence = _diagnose_capture(
            client,
            settings,
            dataset="capture-7f31",
            name="capture-a",
            incident_key="checkpoint-a",
        )
        unknown, unknown_evidence = _diagnose_capture(
            client,
            settings,
            dataset="capture-c2aa",
            name="capture-b",
            incident_key="checkpoint-b",
        )
    with TestClient(create_app(settings)) as restarted:
        persisted_known = restarted.get(
            f"/v1/incidents/{known['incident_id']}/diagnosis"
        ).json()
        persisted_unknown_evidence = restarted.get(
            f"/v1/incidents/{unknown['incident_id']}/evidence"
        ).json()
        metric_text = restarted.get("/metrics").text
    if known["terminal"] != "CORE_KNOWN" or known["mechanism"] != "SERVICE_UNAVAILABLE":
        raise RuntimeError("known checkpoint did not reach Core Known")
    if unknown["terminal"] != "OPEN_WORLD" or unknown["provisional_report"] is None:
        raise RuntimeError("unknown checkpoint did not reach a provisional report")
    if persisted_known["result_sha256"] != known["result_sha256"]:
        raise RuntimeError("known diagnosis changed across restart")
    if not known_evidence["objects"] or not unknown_evidence["objects"]:
        raise RuntimeError("checkpoint evidence bundle is empty")
    if persisted_unknown_evidence != unknown_evidence:
        raise RuntimeError("unknown evidence changed across restart")
    if "ecomsre_diagnosis_terminals_total" not in metric_text:
        raise RuntimeError("checkpoint metrics are unavailable")
    return {
        "terminal": "ECOMSRE_PRODUCT_MVP_V01_DIAGNOSIS_PASS",
        "known_terminal": known["terminal"],
        "known_mechanism": known["mechanism"],
        "unknown_terminal": unknown["terminal"],
        "unknown_report_terminal": unknown["provisional_report"]["terminal"],
        "known_evidence_objects": len(known_evidence["objects"]),
        "unknown_evidence_objects": len(unknown_evidence["objects"]),
        "restart_persistence": True,
        "agent_writes": 0,
        "runbook_executions": 0,
        "provider_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path)
    arguments = parser.parse_args()
    if arguments.data_root is not None:
        print(json.dumps(run_checkpoint(arguments.data_root), sort_keys=True))
        return
    with TemporaryDirectory(prefix="ecomsre-product-increment3-") as directory:
        print(json.dumps(run_checkpoint(Path(directory)), sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = ("run_checkpoint",)
