"""Goal harness only: real local telemetry through normal Product API/Worker."""

from datetime import UTC, datetime
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time

from fastapi.testclient import TestClient
import httpx

from scripts.product_v050.live_environment import (
    ROOT,
    REPO,
    load,
    save,
    digest,
    command,
)
from scripts.product_v050.project_environment import load_project_environment
from scripts.product_v050.preflight import inspect
from ecomsre.product.app import create_app
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.jobs.worker import run_one_job
from ecomsre.product.investigation.repository import InvestigationRepository
from ecomsre.product.knowledge.evolution_v050 import KnowledgeEvolutionV050
from ecomsre.product.pilot.runtime_authority_v02 import (
    PilotRuntimeAuthorityV02,
    write_pilot_runtime_authority_v02,
)
from ecomsre.product.connectors.pilot_runtime import PilotRuntimeSnapshotV02
from ecomsre.product.pilot.live_knowledge_evolution_v030 import (
    build_product_v030_environment_payload,
)
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
    HealthyTrafficProfileV021,
)
from ecomsre_live_sandbox.knowledge_v030 import (
    GoalFlagControllerV030,
    observe_queue_lag_v030,
    consumer_membership_healthy_v030,
)

SERVICES = ("checkout", "fraud-detection", "kafka", "payment")
DATA = REPO / ".local/product-v050"


def stamp():
    return {"utc": datetime.now(UTC).isoformat(), "monotonic_ns": time.monotonic_ns()}


class Campaign:
    def __init__(self, owner):
        self.owner = owner
        self.controller = GoalFlagControllerV030(
            endpoints=SimpleNamespace(
                flag_control="http://127.0.0.1:18081/api",
                flag_evaluation="http://127.0.0.1:18016",
            ),
            flag_file=ROOT / "control/demo.flagd.json",
            documents=load("documents.json"),
        )
        self.authority_path = ROOT / "runtime-authority.json"
        self.snapshot_path = DATA / "pilot/live-01-readiness.json"
        if not (DATA / "product.sqlite3").is_file():
            raise ValueError("ORIGINAL_LEDGER_MISSING")
        load_project_environment(Path.home() / ".config/ecomsre/provider.env")
        os.environ["ECOMSRE_PRODUCT_PROVIDER_API_STYLE"] = "responses"
        os.environ.setdefault(
            "ECOMSRE_PRODUCT_PROVIDER_PRICE_FILE",
            str(REPO / "config/product-v050/openai-gpt54-mini-prices-20260917.json"),
        )
        pre = inspect(DATA)
        if (
            pre["model"] != "gpt-5.4-mini-2026-03-17"
            or os.environ["ECOMSRE_LLM_BASE_URL"].rstrip("/")
            != "https://api.openai.com/v1"
        ):
            raise ValueError("PROVIDER_CONFIG_DRIFT")
        save(ROOT / "provider-preflight.json", pre)
        self.settings = ProductSettingsV1(
            data_root=DATA,
            pilot_runtime_authority_path=self.authority_path,
            connector_timeout_seconds=15,
            investigation={
                "enabled": True,
                "max_provider_calls": 10,
                "max_evidence_reads": 8,
            },
            knowledge_proposer_enabled=True,
        )
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()
        self.app = self.client.app
        self.repo = InvestigationRepository(
            self.app.state.store, self.app.state.object_store
        )
        self.evo = KnowledgeEvolutionV050(self.app.state.knowledge, self.repo)
        self.incidents = []

    def authority(self, env):
        return PilotRuntimeAuthorityV02.build(
            environment_id=env,
            allowed_logical_services=SERVICES,
            profile_sha256=digest(load("manifest.json")),
            daemon_identity_sha256=digest(self.owner.daemon),
            docker_context_sha256=digest(
                {"context": self.owner.context, "endpoint": self.owner.expected_context}
            ),
            config_bundle_sha256=digest(load("compose.json")),
            resolved_sandbox_sha256=digest(load("compose.json")),
            resolved_endpoints_sha256=digest(load("manifest.json")["ports"]),
            ownership_scope_sha256=digest(self.owner.labels),
        )

    def work(self, path, payload=None):
        self.owner.verify()
        r = (
            self.client.post(path, json=payload)
            if payload is not None
            else self.client.post(path)
        )
        if r.status_code != 202:
            raise ValueError("JOB_ENQUEUE:" + str(r.status_code))
        job = r.json()["job_id"]
        if not run_one_job(self.settings, worker_id="v050-live"):
            raise ValueError("WORKER_NO_JOB")
        result = self.client.get("/v1/jobs/" + job).json()
        save(ROOT / "jobs" / ("job-" + job + ".json"), result)
        if result["status"] != "SUCCEEDED":
            raise ValueError("JOB_FAILED:" + str(result.get("safe_error_code")))
        return result

    def runtime(self, label):
        self.owner.verify()
        states = {}
        proof = {}
        for name in SERVICES:
            row = self.owner.require_birth("container", self.owner.containers[name])
            st = row["State"]
            running = st["Running"] and not any(
                st.get(k) for k in ["Paused", "Restarting", "Dead", "OOMKilled"]
            )
            healthy = running and st.get("Health", {}).get("Status") == "healthy"
            if name == "fraud-detection":
                outputs = {}
                for option in ["state", "members"]:
                    outputs[option] = command(
                        "docker",
                        "exec",
                        "--env",
                        "KAFKA_HEAP_OPTS=-Xms32m -Xmx128m",
                        "--env",
                        "KAFKA_OPTS=",
                        "--env",
                        "JAVA_TOOL_OPTIONS=",
                        "--env",
                        "_JAVA_OPTIONS=",
                        self.owner.containers["kafka"],
                        "/opt/kafka/bin/kafka-consumer-groups.sh",
                        "--bootstrap-server",
                        "kafka:9092",
                        "--timeout",
                        "5000",
                        "--describe",
                        "--group",
                        "fraud-detection",
                        "--" + option,
                        *(["--verbose"] if option == "members" else []),
                    )
                ip = next(iter(row["NetworkSettings"]["Networks"].values()))[
                    "IPAddress"
                ]
                healthy = running and consumer_membership_healthy_v030(
                    state_output=outputs["state"],
                    members_output=outputs["members"],
                    container_ip=ip,
                )
                proof["consumer"] = outputs
            states[name] = {
                "state": "RUNNING" if running else "OTHER",
                "healthy": bool(healthy),
                "restart_count": row["RestartCount"],
            }
        save(
            ROOT / "runtime" / ("proof-" + label + ".json"),
            {"at": stamp(), "services": states, "proof": proof},
        )
        if not all(s["healthy"] and s["restart_count"] == 0 for s in states.values()):
            raise ValueError("RUNTIME_NOT_READY")
        if hasattr(self, "env"):
            snap = PilotRuntimeSnapshotV02.build(
                environment_id=self.env,
                authority_sha256=self.authority(self.env).connector_binding_sha256,
                observed_at=datetime.now(UTC),
                services=states,
            )
            save(
                ROOT / "runtime" / ("snapshot-" + label + ".json"),
                snap.model_dump(mode="json"),
            )
            self.snapshot_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temp = self.snapshot_path.with_suffix(".tmp")
            temp.write_text(snap.model_dump_json())
            temp.chmod(0o600)
            temp.replace(self.snapshot_path)
        return states

    def traffic(self, label, count, seed, rate=1):
        self.owner.verify()
        with httpx.Client() as c:
            r = BoundedHealthyCheckoutTrafficV021(client=c).run(
                endpoint="http://127.0.0.1:18080/api/checkout",
                profile=HealthyTrafficProfileV021(
                    request_seed=seed,
                    maximum_request_count=count,
                    requests_per_second=rate,
                    error_budget=1,
                ),
            )
        save(ROOT / "traffic" / (label + ".json"), r.model_dump(mode="json"))
        if r.failed or r.attempted != count:
            raise ValueError("BOUNDED_TRAFFIC_FAILED")
        return r

    def lag(self):
        self.owner.verify()
        with httpx.Client(timeout=10) as c:
            return observe_queue_lag_v030(c)

    def setup(self):
        self.owner.verify()
        self.controller.read("BASELINE")
        # First real healthy checkout and coordinator formation before baseline.
        self.traffic("initial", 3, 50001)
        time.sleep(30)
        self.runtime("initial")
        binding = self.authority("env-" + "0" * 24).connector_binding_sha256
        payload = build_product_v030_environment_payload(
            repository_root=REPO, runtime_authority_sha256=binding
        )
        payload.update(
            name="product-v050-live-01",
            description="Independent local Goal telemetry; no recovery authority",
        )
        for c in payload["connector_configs"]:
            if c["kind"] == "PROMETHEUS":
                for key, value in c["settings"]["query_templates"].items():
                    c["settings"]["query_templates"][key] = value.replace(
                        "ecomsre-live-sandbox-v1-", self.owner.nonce + "-"
                    )
            if c["kind"] == "JAEGER":
                c["endpoint"] = "http://127.0.0.1:16686/jaeger/ui"
            if c["kind"] == "PILOT_RUNTIME":
                c["settings"]["snapshot_ref"] = "pilot/live-01-readiness.json"
        r = self.client.post("/v1/environments", json=payload)
        if r.status_code != 201:
            raise ValueError("ENVIRONMENT_CREATE:" + str(r.status_code))
        self.env = r.json()["environment_id"]
        save(ROOT / "environment.json", r.json())
        self.evo.enroll_fresh_test_environment(self.env)
        self.evo.freeze_split(
            self.env,
            {
                self.owner.nonce + "-" + k: v
                for k, v in load("manifest.json")["episode_order"].items()
            },
        )
        write_pilot_runtime_authority_v02(self.authority_path, self.authority(self.env))
        self.runtime("environment")
        self.work("/v1/environments/" + self.env + "/verify-jobs")
        save(
            ROOT / "capabilities.json",
            self.app.state.capabilities.get(self.env).model_dump(mode="json"),
        )
        print("V050_ENVIRONMENT_VERIFIED", flush=True)
        self.traffic("baseline", 90, 50002, rate=0.5)
        self.runtime("baseline")
        if self.lag()["lag"] >= 20:
            raise ValueError("BASELINE_QUEUE_NOT_LOW")
        self.work(
            "/v1/environments/" + self.env + "/baseline-jobs",
            {
                "activate": True,
                "candidate_services": list(SERVICES),
                "build_policy": {
                    "mode": "DEMO_ONLY",
                    "lookback_seconds": 180,
                    "window_count": 5,
                    "minimum_successful_windows": 5,
                    "warmup_seconds": 180,
                },
            },
        )
        self.service_ids = {
            s.logical_service: s.service_id
            for s in self.app.state.services.get_map(self.env).services
        }
        save(
            ROOT / "baseline-ready.json",
            {
                "environment": self.env,
                "at": stamp(),
                "accounting": self.repo.accounting(),
            },
        )
        print("V050_BASELINE_READY", flush=True)

    def episode(self, ordinal):
        key = f"e{ordinal:02d}"
        root = ROOT / "episodes" / key
        count = len(list(DATA.glob("live-*/episodes/*/started.json")))
        if count >= 12:
            raise ValueError("LIVE_EPISODE_BUDGET")
        self.owner.verify()
        self.controller.read("BASELINE")
        self.runtime(key + "-before")
        if self.lag()["lag"] >= 20:
            raise ValueError("EPISODE_BASELINE_NOT_LOW")
        record = {
            "episode_id": self.owner.nonce + "-" + key,
            "start": stamp(),
            "status": "STARTED",
            "experiment_control_writes": 0,
            "product_recovery_writes": 0,
        }
        save(root / "started.json", record)
        try:
            self.owner.verify()
            record["experiment_control_writes"] += 1
            save(
                root / "fault-intent.json",
                {"at": stamp(), "operation": "FROZEN_QUEUE_DOCUMENT"},
            )
            record["activated"] = self.controller.apply("QUEUE")
            self.traffic(key, 3, 51000 + ordinal)
            samples = []
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                sample = self.lag()
                samples.append(sample)
                if len({x["source_timestamp"] for x in samples if x["lag"] >= 20}) >= 3:
                    break
                time.sleep(5)
            save(root / "lag-during.json", samples)
            if len({x["source_timestamp"] for x in samples if x["lag"] >= 20}) < 3:
                raise ValueError("NO_OBSERVED_QUEUE_EPISODE")
            self.runtime(key + "-during")
            r = self.client.post(
                "/v1/incidents",
                json={
                    "environment_id": self.env,
                    "external_incident_key": self.owner.nonce + "-" + key,
                    "alert_name": "service-observation",
                    "summary": "Investigate unexplained service observations using discriminating available evidence.",
                    "started_at": record["start"]["utc"],
                    "ended_at": datetime.now(UTC).isoformat(),
                    "candidate_service_ids": [
                        self.service_ids[n] for n in SERVICES[:3]
                    ],
                },
            )
            if r.status_code != 201:
                raise ValueError("INCIDENT_CREATE:" + str(r.status_code))
            iid = r.json()["incident_id"]
            record["incident_id"] = iid
            self.evo.bind_episode(iid, record["episode_id"])
            self.work("/v1/incidents/" + iid + "/diagnosis-jobs")
            d = self.client.get("/v1/incidents/" + iid + "/diagnosis").json()
            record["diagnosis"] = d
            print(
                json.dumps(
                    {
                        "episode": key,
                        "terminal": d["terminal"],
                        "root_count": len(d["root_service_ids"]),
                    }
                ),
                flush=True,
            )
            self.work("/v1/incidents/" + iid + "/investigation-jobs")
            record["investigation"] = self.client.get(
                "/v1/incidents/" + iid + "/investigation"
            ).json()
            record["status"] = "OBSERVED"
            self.incidents.append(iid)
            print(
                json.dumps(
                    {
                        "episode": key,
                        "investigation": record["investigation"]["status"],
                        "reads": record["investigation"]["read_count"],
                        "calls": record["investigation"]["provider_calls"],
                    }
                ),
                flush=True,
            )
        except Exception as exc:
            record["status"] = "FAILED"
            record["failure_type"] = type(exc).__name__
            record["failure"] = str(exc)[:240]
            raise
        finally:
            try:
                self.owner.verify()
                save(
                    root / "restore-intent.json",
                    {"at": stamp(), "operation": "FROZEN_BASELINE_DOCUMENT"},
                )
                record["restored"] = self.controller.apply("BASELINE")
                deadline = time.monotonic() + 360
                while True:
                    lag = self.lag()
                    if (
                        lag["lag"] < 20
                        and lag["source_timestamp"]
                        >= datetime.fromisoformat(
                            record["restored"]["observed_at"]
                        ).timestamp()
                    ):
                        break
                    if time.monotonic() > deadline:
                        raise ValueError("QUEUE_NOT_DRAINED")
                    time.sleep(5)
                record["lag_after"] = lag
                self.runtime(key + "-after")
                record["healthy_restored"] = True
            except Exception as exc:
                record["healthy_restored"] = False
                record["recovery_failure"] = type(exc).__name__
                raise
            finally:
                record["end"] = stamp()
                record["accounting"] = self.repo.accounting()
                save(root / "result.json", record)
        return iid
