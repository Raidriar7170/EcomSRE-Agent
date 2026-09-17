"""Bounded real Provider smoke through Product API/Worker, fixture replay telemetry.

Uses the original Product v0.5 data root and ledger. No Docker or recovery writes.
Run once; terminal sessions, failed jobs and all paid reservations remain retained.
"""

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient
from ecomsre.product.app import create_app
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.jobs.worker import run_one_job
from ecomsre.product.investigation.repository import InvestigationRepository
from ecomsre.product.knowledge.evolution_v050 import KnowledgeEvolutionV050
from scripts.product_v050.project_environment import load_project_environment
from scripts.product_v050.preflight import inspect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--attempt", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument(
        "--api-style",
        choices=("chat_completions", "responses"),
        default="chat_completions",
    )
    args = parser.parse_args()
    os.environ["ECOMSRE_PRODUCT_PROVIDER_API_STYLE"] = args.api_style
    os.umask(0o077)
    root = Path(".local/product-v050").resolve()
    loaded = load_project_environment(Path.home() / ".config/ecomsre/provider.env")
    if not os.environ.get("ECOMSRE_PRODUCT_PROVIDER_PRICE_FILE"):
        os.environ["ECOMSRE_PRODUCT_PROVIDER_PRICE_FILE"] = str(
            Path("config/product-v050/openai-gpt54-mini-prices-20260917.json").resolve()
        )
    # This dated profile is specifically direct OpenAI standard, not a gateway.
    if (
        os.environ.get("ECOMSRE_LLM_BASE_URL", "").rstrip("/")
        != "https://api.openai.com/v1"
    ):
        raise ValueError("SMOKE_REQUIRES_VERIFIED_DIRECT_PRICE_PROFILE")
    settings = ProductSettingsV1(
        data_root=root,
        investigation={"enabled": True, "max_provider_calls": 3},
        knowledge_proposer_enabled=True,
    )
    preflight = inspect(root) | {
        "project_file_exists": True,
        "project_file_readable": True,
        "project_file_variables": loaded,
        "loaded_provider_variables": {k: bool(os.environ.get(k)) for k in loaded},
        "api_worker_same_process_environment": True,
        "investigation_enabled": True,
        "knowledge_proposer_enabled": True,
    }
    print(json.dumps(preflight), flush=True)
    if not args.execute:
        return
    if preflight["pricing_status"] != "CONFIGURED_NOT_PROVIDER_VERIFIED":
        raise ValueError("SMOKE_PREFLIGHT_FAILED")
    out = root / "continuation-01"
    out.mkdir(parents=True, exist_ok=True)
    marker = out / (
        "smoke-started.json"
        if args.attempt == 1
        else f"smoke-{args.attempt:02d}-started.json"
    )
    with marker.open("x") as f:
        json.dump(
            {
                "started_at": datetime.now(UTC).isoformat(),
                "mode": "LIVE_PROVIDER_REPLAY_TELEMETRY",
            },
            f,
        )
    report = {
        "telemetry_mode": "LIVE_PROVIDER_REPLAY_TELEMETRY",
        "live_episodes": 0,
        "independent_live_incidents": 0,
        "new_product_external_writes": 0,
        "incidents": [],
        "preflight": preflight,
    }
    with TestClient(create_app(settings)) as client:
        app = client.app
        with app.state.store.connect() as c:
            if c.execute(
                "SELECT 1 FROM diagnosis_jobs WHERE status IN ('PENDING','RUNNING') LIMIT 1"
            ).fetchone():
                raise ValueError("SMOKE_SHARED_QUEUE_NOT_QUIESCENT")
        repo = InvestigationRepository(app.state.store, app.state.object_store)
        report["accounting_before"] = repo.accounting()
        evo = KnowledgeEvolutionV050(app.state.knowledge, repo)
        env = client.post(
            "/v1/environments",
            json={
                "name": "v050-continuation-replay",
                "description": "Real Provider; frozen fixture connector replay, not live incidents",
                "timezone": "UTC",
                "service_identity_policy": {
                    "services": [{"logical_service": "payment"}]
                },
                "connector_configs": [
                    {
                        "name": "replay",
                        "kind": "FIXTURE",
                        "settings": {"dataset": "capture-c2aa"},
                        "credential_refs": {},
                    }
                ],
                "explicit_service_catalog": ["payment"],
            },
        ).json()["environment_id"]
        report["environment_id"] = env
        evo.enroll_fresh_test_environment(env)
        evo.freeze_split(env, {"replay-a": "DISCOVERY", "replay-b": "DISCOVERY"})

        def work(response):
            if response.status_code != 202:
                raise ValueError("SMOKE_JOB_ENQUEUE_FAILED")
            job = response.json()["job_id"]
            if not run_one_job(settings, worker_id="v050-smoke"):
                raise ValueError("SMOKE_WORKER_NO_JOB")
            result = client.get("/v1/jobs/" + job).json()
            if result["status"] not in {"SUCCEEDED", "FAILED"}:
                raise ValueError("SMOKE_TARGET_JOB_NOT_TERMINAL")
            return result

        for response in [client.post(f"/v1/environments/{env}/verify-jobs")]:
            assert work(response)["status"] == "SUCCEEDED"
        assert (
            work(
                client.post(
                    f"/v1/environments/{env}/baseline-jobs", json={"activate": True}
                )
            )["status"]
            == "SUCCEEDED"
        )
        service = app.state.services.get_map(env).services[0].service_id
        try:
            for ordinal in range(2):
                incident = client.post(
                    "/v1/incidents",
                    json={
                        "environment_id": env,
                        "external_incident_key": "v050-replay-" + str(ordinal),
                        "alert_name": "service observation",
                        "summary": "Investigate the unexplained observation; use available reads when they distinguish alternatives.",
                        "started_at": datetime.now(UTC).isoformat(),
                        "candidate_service_ids": [service],
                    },
                ).json()["incident_id"]
                evo.bind_episode(incident, ["replay-a", "replay-b"][ordinal])
                assert (
                    work(client.post(f"/v1/incidents/{incident}/diagnosis-jobs"))[
                        "status"
                    ]
                    == "SUCCEEDED"
                )
                job = work(client.post(f"/v1/incidents/{incident}/investigation-jobs"))
                session = client.get(f"/v1/incidents/{incident}/investigation").json()
                report["incidents"].append(
                    {
                        "incident_id": incident,
                        "job_status": job["status"],
                        "investigation": session,
                    }
                )
                print(
                    json.dumps(
                        {
                            "incident_ordinal": ordinal,
                            "status": session.get("status"),
                            "reads": session.get("read_count"),
                            "calls": session.get("provider_calls"),
                        }
                    ),
                    flush=True,
                )
                if session.get("status") == "PROVIDER_FAILED":
                    break
            if len(report["incidents"]) == 2 and all(
                i["investigation"].get("status") != "PROVIDER_FAILED"
                for i in report["incidents"]
            ):
                job = work(
                    client.post(
                        f"/v1/environments/{env}/knowledge-proposal-jobs",
                        json=[i["incident_id"] for i in report["incidents"]],
                    )
                )
                report["knowledge_job"] = job
                print(
                    json.dumps(
                        {
                            "knowledge_job_status": job["status"],
                            "error_code": job.get("error_code"),
                        }
                    ),
                    flush=True,
                )
        finally:
            report["accounting_after"] = repo.accounting()
            with app.state.store.connect() as c:
                report["provider_calls"] = [
                    dict(r)
                    for r in c.execute(
                        "SELECT call_key,state,reserved_microusd,charged_microusd,payload_json FROM investigation_provider_calls_v050"
                    )
                ]
            with (
                out
                / (
                    "smoke-private.json"
                    if args.attempt == 1
                    else f"smoke-{args.attempt:02d}-private.json"
                )
            ).open("x") as f:
                json.dump(report, f, indent=2)
            print(json.dumps({"accounting": report["accounting_after"]}), flush=True)


if __name__ == "__main__":
    main()
