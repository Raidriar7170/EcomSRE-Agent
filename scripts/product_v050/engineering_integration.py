"""One authorized no-fault engineering deployment, isolated Product API/Worker.

No old Campaign, Provider, fault controller, registration or promotion entrypoint.
The fixed schedule is independent of observed diagnoses.
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import time

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from scripts.product_v050 import engineering_capture as ec, engineering_context
from scripts.product_v050 import engineering_v4_capture as vc, default_credentials as dc
from scripts.product_v050 import ingestion_evidence as ie
from scripts.product_v050.validation_capture import capture
from scripts.product.minimal_payment_acceptance_v040.owned import semantic
from ecomsre.product.contracts import EnvironmentCreateV1
from ecomsre.product.app import create_app
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.jobs.worker import run_one_job
from ecomsre.product.connectors._http import (
    BoundedHttpTransportV1,
    ConnectorRequestError,
    raw_request_guard,
)
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.pilot_runtime import PilotRuntimeSnapshotV02
from ecomsre.product.pilot.runtime_authority_v02 import (
    PilotRuntimeAuthorityV02,
    write_pilot_runtime_authority_v02,
)
from ecomsre.product.pilot.live_knowledge_evolution_v030 import (
    build_product_v030_environment_payload,
)
from ecomsre.dta_v2.v22.memory import MemoryReadOutcomeV22, build_memory_views_v22
from ecomsre.dta_v2.v22.predicates import evaluate_no_incident_v22
from ecomsre.product.incidents.queue_action import build_queue_lag_action_v030

ROOT = ec.REPO / ".local/engineering-calibration/live-02"
SCHEDULE = {
    "rounds": [570, 1380, 1680],
    "baseline_build": 720,
    "traffic": [
        [30, 150, 270, 390, 480],
        [1080, 1140, 1200, 1260, 1320],
        [1440, 1480, 1520, 1560, 1600],
    ],
    "maximum_lateness_seconds": 120,
}
CAPS = dict(
    deployment_starts=1,
    rounds=3,
    http_reads=600,
    docker_reads=90,
    normal_traffic=30,
    repairs=1,
    engineering_incidents=2,
    provider=0,
    faults=0,
    recovery_writes=0,
    holdout=0,
    promotion=0,
)


def bind():
    ec.ROOT = ROOT
    ec.CLEANING = False


def precheck():
    """Fresh daemon and inventory; do not assume old resources are still paused."""

    def read(args):
        seq = ec.reserve("docker_reads", dict(argv=args))
        r = subprocess.run(args, capture_output=True, text=True, timeout=20)
        ec.save(
            ROOT / "receipts" / f"{seq:04}.json",
            dict(
                returncode=r.returncode,
                received_at=datetime.now(UTC).isoformat(),
                stdout_sha256=hashlib.sha256(r.stdout.encode()).hexdigest(),
            ),
        )
        if r.returncode:
            raise ValueError("PREFLIGHT_DOCKER_READ_FAILED")
        return r.stdout.strip()

    context = read(["docker", "context", "show"])
    ctx = json.loads(read(["docker", "context", "inspect", context]))[0]
    endpoint = ctx["Endpoints"]["docker"]["Host"]
    if not endpoint.startswith("unix://"):
        raise ValueError("LOCAL_UNIX_DOCKER_REQUIRED")
    base = ["docker", "--host", endpoint]
    info = json.loads(read(base + ["info", "--format", "{{json .}}"]))
    if info["OSType"] != "linux" or info["Architecture"] not in {"arm64", "aarch64"}:
        raise ValueError("DAEMON_PLATFORM_DIFFERS")
    inventory = {}
    for kind, listing in [
        ("container", ["ps", "-aq", "--no-trunc"]),
        ("network", ["network", "ls", "-q", "--no-trunc"]),
        ("volume", ["volume", "ls", "-q"]),
    ]:
        ids = read(base + listing).split()
        rows = json.loads(read(base + [kind, "inspect", *ids])) if ids else []
        inventory[kind] = {
            r.get("Id", r.get("Name")): hashlib.sha256(
                json.dumps(semantic(kind, r), sort_keys=True).encode()
            ).hexdigest()
            for r in rows
        }
    prior = ec.load(ec.REPO / ".local/engineering-calibration/live-01/precheck.json")
    # Existing accepted layout had no containers, and only untouched daemon
    # networks/unattached volumes. A changed resource requires separate review.
    if inventory != prior["inventory"] or inventory["container"]:
        raise ValueError("NON_OWNED_RESOURCE_STATE_REQUIRES_REVIEW")
    ec.save(
        ROOT / "precheck.json",
        dict(
            context=context,
            endpoint=endpoint,
            daemon=info["ID"],
            inventory=inventory,
            observed_at=datetime.now(UTC).isoformat(),
        ),
    )


def guard(request):
    allowed = {
        ("GET", "http://127.0.0.1:19090/api/v1/query"),
        ("GET", "http://127.0.0.1:19090/api/v1/query_range"),
        ("GET", "http://127.0.0.1:19090/api/v1/series"),
        ("GET", "http://127.0.0.1:19090/api/v1/label/service_name/values"),
        ("GET", "http://127.0.0.1:16686/jaeger/ui/api/services"),
        ("GET", "http://127.0.0.1:16686/jaeger/ui/api/traces"),
        ("GET", "http://127.0.0.1:19200/otel-logs-*/_search"),
        ("POST", "http://127.0.0.1:19200/otel-logs-*/_search"),
    }
    if (request["method"], request["url"]) not in allowed:
        raise ValueError("ENGINEERING_READ_ENDPOINT_DENIED")
    ec.reserve("http_reads", request)


@contextmanager
def counted():
    token = raw_request_guard.set(guard)
    try:
        yield
    finally:
        raw_request_guard.reset(token)


def wait_until(anchor, offset):
    target = anchor + offset
    if time.monotonic() > target + SCHEDULE["maximum_lateness_seconds"]:
        raise ValueError("FIXED_SCHEDULE_MISSED")
    while time.monotonic() < target:
        ec.ensure_capacity(0, 0)
        time.sleep(min(1, target - time.monotonic()))


def environment_payload(plan, authority_sha256):
    payload = build_product_v030_environment_payload(
        repository_root=ec.REPO,
        runtime_authority_sha256=authority_sha256,
    )
    payload.update(
        name="engineering-v4-no-fault",
        description="Seen engineering observations; no promotion authority",
    )
    prefix = plan["services"]["checkout"]["container_name"].removesuffix("checkout")
    for c in payload["connector_configs"]:
        if c["kind"] == "PROMETHEUS":
            c["settings"]["query_templates"] = {
                k: v.replace("ecomsre-live-sandbox-v1-", prefix)
                for k, v in c["settings"]["query_templates"].items()
            }
        if c["kind"] == "JAEGER":
            c["endpoint"] = "http://127.0.0.1:16686/jaeger/ui"
        if c["kind"] == "PILOT_RUNTIME":
            c["settings"]["snapshot_ref"] = "pilot/runtime-current.json"
    EnvironmentCreateV1.model_validate(payload)
    return payload


def offline_preflight():
    """Exercise the real environment API in a disposable DB before any Docker work."""
    payload = environment_payload(ec.load(ec.PRIOR / "compose.json"), "a" * 64)
    with TemporaryDirectory(prefix="ecomsre-integration-preflight-") as directory:
        settings = ProductSettingsV1(
            data_root=Path(directory),
            investigation={"enabled": False},
            knowledge_proposer_enabled=False,
            admin_token_env="ECOMSRE_ENGINEERING_NO_ADMIN_TOKEN",
        )
        with TestClient(create_app(settings)) as client:
            response = client.post("/v1/environments", json=payload)
            if response.status_code != 201:
                raise ValueError("OFFLINE_ENVIRONMENT_API_CONTRACT_FAILED")
    return dict(
        environment_api_status=201,
        payload_sha256=ec.digest(payload),
        network_requests=0,
        fixture_only=True,
    )


class Integration:
    def __init__(self, *, resume=False):
        self.data = ROOT / "product"
        self.settings = ProductSettingsV1(
            data_root=self.data,
            pilot_runtime_authority_path=self.data / "runtime-authority.json",
            investigation={"enabled": False},
            knowledge_proposer_enabled=False,
            admin_token_env="ECOMSRE_ENGINEERING_NO_ADMIN_TOKEN",
        )
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()
        self.app = self.client.app
        self.objects = self.app.state.object_store
        self.queries = ec.load(ec.PRIOR / "validation-authorization.json")["plan"][
            "collection"
        ]["actual_queries"]
        keys = ec.load(ec.PRIOR / "validation-authorization.json")["plan"][
            "collection"
        ]["preparation_query_keys"]
        self.queries = {k: self.queries[k] for k in keys}
        self.collector = ec.load(ROOT / "collector.json")
        self.command = ec.load(ROOT / "compose.json")["services"]["prometheus"][
            "command"
        ]
        self.binding = ie.topology_v4(
            self.collector,
            self.command,
            self.queries,
            deployment_id=ec.load(ROOT / "manifest.json")["campaign"],
        )
        if resume:
            if (
                not (ROOT / "repair.json").exists()
                or ec.load(ROOT / "v4-binding.json") != self.binding
            ):
                raise ValueError("REPAIR_BINDING_DIFFERS")
        else:
            ec.save(ROOT / "v4-binding.json", self.binding)

    def authority(self, env):
        pre = ec.load(ROOT / "precheck.json")
        plan = ec.load(ROOT / "compose.json")
        manifest = ec.load(ROOT / "manifest.json")
        return PilotRuntimeAuthorityV02.build(
            environment_id=env,
            allowed_logical_services=vc.SERVICES,
            profile_sha256=ec.digest(manifest),
            daemon_identity_sha256=ec.digest(pre["daemon"]),
            docker_context_sha256=ec.digest(
                {"context": pre["context"], "endpoint": pre["endpoint"]}
            ),
            config_bundle_sha256=ec.digest(plan),
            resolved_sandbox_sha256=ec.digest(plan),
            resolved_endpoints_sha256=ec.digest(ec.PORTS),
            ownership_scope_sha256=ec.digest(manifest["labels"]),
        )

    def put_runtime(self, observation, directory):
        snap = PilotRuntimeSnapshotV02.build(
            environment_id=self.env,
            authority_sha256=self.authority(self.env).connector_binding_sha256,
            observed_at=datetime.fromtimestamp(observation["observed_at"], UTC),
            services=observation["services"],
        )
        ec.save(directory / "typed-runtime.json", snap.model_dump(mode="json"))
        temp = self.data / "pilot/runtime-current.tmp.json"
        ec.save(temp, snap.model_dump(mode="json"))
        temp.replace(self.data / "pilot/runtime-current.json")

    def work(self, path, directory, payload=None):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        r = (
            self.client.post(path, json=payload)
            if payload is not None
            else self.client.post(path)
        )
        ec.save(
            directory / "enqueue.json",
            dict(status=r.status_code, body=r.json(), at=datetime.now(UTC).isoformat()),
        )
        if r.status_code != 202:
            return None
        job = r.json()["job_id"]
        with (
            counted(),
            capture(self.objects, directory / "raw.jsonl", occurrence=directory.name),
        ):
            run_one_job(self.settings, worker_id="engineering-no-fault")
        result = self.client.get("/v1/jobs/" + job).json()
        ec.save(directory / "job.json", result)
        return result

    def environment(self, observation, directory):
        payload = environment_payload(
            ec.load(ROOT / "compose.json"),
            self.authority("env-" + "0" * 24).connector_binding_sha256,
        )
        r = self.client.post("/v1/environments", json=payload)
        ec.save(
            ROOT / "environment-create.json",
            dict(status=r.status_code, body=r.json(), payload=payload),
        )
        if r.status_code != 201:
            raise ValueError("ENGINEERING_ENVIRONMENT_CREATE_FAILED")
        self.env = r.json()["environment_id"]
        write_pilot_runtime_authority_v02(
            self.settings.pilot_runtime_authority_path, self.authority(self.env)
        )
        self.put_runtime(observation, directory)
        self.work(
            "/v1/environments/" + self.env + "/verify-jobs", ROOT / "environment-verify"
        )
        # Record the actual deployment, not a fabricated configuration fault.
        rows = vc.by_service(ec.load(ROOT / "started.json")["resources"])
        changes = []
        for service in self.app.state.services.get_map(self.env).services:
            if service.logical_service not in vc.SERVICES:
                continue
            row = rows[service.logical_service]
            response = self.client.post(
                "/v1/environments/" + self.env + "/changes",
                json=dict(
                    service_id=service.service_id,
                    category="DEPLOYMENT",
                    occurred_at=row["State"]["StartedAt"],
                    revision=row["Image"],
                    summary="Owned engineering deployment startup",
                    external_change_id="startup:" + row["Id"],
                ),
            )
            changes.append(dict(status=response.status_code, body=response.json()))
        ec.save(ROOT / "deployment-changes.json", changes)
        ec.save(
            ROOT / "capabilities.json",
            self.app.state.capabilities.get(self.env).model_dump(mode="json"),
        )

    def samples(
        self, start, end, occurrence, iid, proofs, directory, *, preparation=True
    ):
        requirements = [
            dict(query_key=k, query=q, start=start, end=end)
            for k, q in self.queries.items()
        ]
        credential = dc.build_credential(
            binding=self.binding,
            requirements=requirements,
            occurrence=occurrence,
            incident_id=iid,
            proofs=proofs,
        )
        ref = self.objects.put_json(credential).object_sha256
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        receipt = dict(
            version=dc.VERSION,
            default_credential_sha256=ref,
            collector_object_sha256=self.objects.put_json(self.collector).object_sha256,
            prometheus_command_object_sha256=self.objects.put_json(
                self.command
            ).object_sha256,
            requirements=requirements,
        )
        # Configuration validity is reported independently of sample sufficiency.
        try:
            dc.resolve(
                ref,
                self.objects.read_bytes,
                collector=self.collector,
                binding=self.binding,
                occurrence=occurrence,
                incident_id=iid,
                requirements=requirements,
            )
            receipt["configuration_status"] = "VERIFIED"
        except ValueError as exc:
            receipt.update(configuration_status="UNKNOWN", configuration_error=str(exc))
            ec.save(directory / "receipt.json", receipt)
            return
        http = BoundedHttpTransportV1(
            credential_resolver=CredentialResolverV1(environment={}),
            credential_refs={},
            timeout_seconds=10,
            maximum_response_bytes=2 * 1024 * 1024,
        )
        try:
            with (
                counted(),
                capture(
                    self.objects, directory / "raw.jsonl", occurrence=occurrence
                ) as entries,
            ):
                ie.acquire(
                    http,
                    "http://127.0.0.1:19090",
                    self.binding,
                    requirements,
                    incident_id=iid,
                    monotonic=time.monotonic,
                    preparation=preparation,
                )
            receipt["assessment"] = ie.verify(
                self.binding,
                entries=entries,
                occurrence=occurrence,
                incident_id=iid,
                queries=self.queries,
                read_bytes=self.objects.read_bytes,
                collector=self.collector,
                command=self.command,
                requirements=requirements,
                default_credential_sha256=ref,
            )
        except ConnectorRequestError as exc:
            receipt.update(
                collection_status="INSUFFICIENT_EVIDENCE",
                collection_error=exc.safe_error_code,
            )
        finally:
            http.close()
            ec.save(directory / "receipt.json", receipt)

    def baseline(self, proofs):
        directory = ROOT / "baseline"
        result = self.work(
            "/v1/environments/" + self.env + "/baseline-jobs",
            directory,
            dict(
                activate=True,
                candidate_services=list(vc.SERVICES),
                build_policy=dict(
                    mode="DEMO_ONLY",
                    lookback_seconds=180,
                    window_count=5,
                    minimum_successful_windows=5,
                    warmup_seconds=180,
                ),
            ),
        )
        if not result or result["status"] != "SUCCEEDED":
            ec.save(
                ROOT / "baseline-status.json", dict(status="NOT_AVAILABLE", job=result)
            )
            return
        baseline = self.app.state.baselines.get_active(self.env)
        ec.save(ROOT / "baseline-frozen.json", baseline.model_dump(mode="json"))
        ec.save(
            ROOT / "baseline-status.json",
            dict(
                status="BUILDER_ACCEPTED",
                frozen_at=datetime.now(UTC).isoformat(),
                baseline_sha256=baseline.baseline_sha256,
            ),
        )
        entries = [
            json.loads(x) for x in (directory / "raw.jsonl").read_text().splitlines()
        ]
        metrics = [
            e["params"] for e in entries if e["url"].endswith("/api/v1/query_range")
        ]
        self.samples(
            min(p["start"] for p in metrics),
            max(p["end"] for p in metrics),
            "baseline-support",
            "baseline:" + baseline.baseline_id,
            proofs,
            ROOT / "baseline-support",
            preparation=False,
        )

    def incident(self, end, directory):
        ec.reserve("engineering_incidents", dict(window_end=end, formal_holdout=False))
        status = ec.load(ROOT / "baseline-status.json")
        if status["status"] != "BUILDER_ACCEPTED":
            ec.save(
                directory / "incident-unavailable.json",
                dict(reason="NO_ACCEPTED_BASELINE"),
            )
            return None
        if datetime.fromisoformat(status["frozen_at"]).timestamp() >= end - 300:
            raise ValueError("BASELINE_NOT_FROZEN_BEFORE_CHECK_WINDOW")
        ids = [
            s.service_id
            for s in self.app.state.services.get_map(self.env).services
            if s.logical_service in vc.SERVICES
        ]
        r = self.client.post(
            "/v1/incidents",
            json=dict(
                environment_id=self.env,
                external_incident_key=directory.name,
                alert_name="engineering-service-observation",
                summary="Observe current services with normal diagnostics",
                labels={"fault": "none"},
                started_at=datetime.fromtimestamp(end - 300, UTC).isoformat(),
                ended_at=datetime.fromtimestamp(end, UTC).isoformat(),
                candidate_service_ids=sorted(ids),
            ),
        )
        ec.save(
            directory / "incident-create.json",
            dict(status=r.status_code, body=r.json()),
        )
        return r.json()["incident_id"] if r.status_code == 201 else None

    def diagnose(self, iid, directory):
        result = self.work(
            "/v1/incidents/" + iid + "/diagnosis-jobs", directory / "diagnosis-job"
        )
        if not result or result["status"] != "SUCCEEDED":
            return
        diagnosis = self.app.state.diagnoses.get(iid)
        bundle = self.app.state.diagnoses.evidence(iid)
        ec.save(directory / "diagnosis.json", diagnosis.model_dump(mode="json"))
        ec.save(directory / "evidence.json", bundle.model_dump(mode="json"))
        ec.save(
            directory / "evidence-index.json",
            self.app.state.diagnoses.evidence_index(iid).model_dump(mode="json"),
        )
        snapshots = {
            o.action_id: o.payload
            for o in bundle.objects
            if o.payload.get("memory_outcome")
        }
        queue_id = build_queue_lag_action_v030().action_id
        outcomes = tuple(
            TypeAdapter(MemoryReadOutcomeV22).validate_json(
                json.dumps(p["memory_outcome"])
            )
            for p in (
                snapshots[k]
                for k in sorted(snapshots, key=lambda k: (k == queue_id, k))
            )
        )
        inc = self.app.state.incidents.get(iid)
        base = self.app.state.baselines.get_optional(inc.baseline_id)
        memory, _ = build_memory_views_v22(
            outcomes=outcomes,
            baseline=base.v22_baseline_profile,
            observed_at=inc.diagnosis_observed_at,
            top_k=64,
        )
        if memory.memory_sha256 != diagnosis.memory_sha256:
            raise ValueError("DIAGNOSIS_MEMORY_REPLAY_DIFFERS")
        ec.save(directory / "memory.json", memory.model_dump(mode="json"))
        ec.save(
            directory / "health-predicate.json",
            evaluate_no_incident_v22(
                memory=memory, candidate_services=inc.candidate_logical_services
            ).model_dump(mode="json"),
        )

    def round(self, number):
        ec.ensure_capacity(12, 180)
        directory = ROOT / f"round-{number}"
        directory.mkdir(mode=0o700)
        ec.reserve(
            "rounds", dict(round=number, planned_offset=SCHEDULE["rounds"][number - 1])
        )
        rows = ec.snapshot("round-" + str(number))
        ec.validate(rows)
        observation = vc.runtime(rows, directory)
        # Configuration capture is independent of Product API acceptance.
        # For diagnosis windows keep the evidence after the fixed end time.
        end = time.time()
        proofs = vc.configuration(rows, directory, self.objects)
        if number == 1:
            self.environment(observation, directory)
        else:
            self.put_runtime(observation, directory)
        iid = self.incident(end, directory) if number > 1 else None
        if number > 1:
            self.samples(
                end - 300,
                end,
                directory.name,
                iid or directory.name,
                proofs,
                directory / "samples",
            )
        if iid:
            self.diagnose(iid, directory)
        engineering_context.collect_context(number)
        ec.save(
            directory / "round.json",
            dict(end=end, incident_id=iid, formal_holdout=False, provider_calls=0),
        )
        return proofs


def close_and_cleanup(integration):
    try:
        if integration is not None:
            integration.client.__exit__(None, None, None)
    except Exception as exc:
        ec.save(
            ROOT / "product-close-failure.json",
            dict(type=type(exc).__name__, at=datetime.now(UTC).isoformat()),
        )
        raise
    finally:
        if (ROOT / "declared-resources.json").exists():
            ec.cleanup()


def run():
    bind()
    preflight = offline_preflight()
    ROOT.mkdir(mode=0o700)  # create-once; no restart of an ended calibration
    ec.save(ROOT / "offline-preflight.json", preflight)
    started = datetime.now(UTC)
    ec.save(
        ROOT / "authorization.json",
        dict(
            mode="SEEN_ENGINEERING_INTEGRATION",
            authorized_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            started_at=started.isoformat(),
            deadline=(started + timedelta(minutes=90)).isoformat(),
            start_monotonic=time.monotonic(),
            caps=CAPS,
            schedule=SCHEDULE,
            source="current explicit user authorization",
            old_batch_authority=False,
        ),
    )
    ec.save(
        ROOT / "source-version.json",
        dict(
            head=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            diff_sha256=hashlib.sha256(
                subprocess.check_output(["git", "diff", "HEAD"])
            ).hexdigest(),
            sources={
                str(p.relative_to(ec.REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
                for folder in (
                    "scripts/product_v050",
                    "src/ecomsre/product",
                    "config/product-v050",
                )
                for p in sorted((ec.REPO / folder).rglob("*"))
                if p.is_file() and p.suffix in {".py", ".json"}
            },
            at=datetime.now(UTC).isoformat(),
        ),
    )
    integration = None
    process_names = subprocess.check_output(["ps", "-axo", "pid=,comm="], text=True)
    active_clients = [
        line.split()[0]
        for line in process_names.splitlines()
        if line.split() and Path(line.split()[-1]).name in {"docker", "docker-compose"}
    ]
    ec.save(
        ROOT / "docker-client-check.json",
        dict(active_client_pids=active_clients, at=datetime.now(UTC).isoformat()),
    )
    if active_clients:
        raise ValueError("OTHER_DOCKER_CLIENT_ACTIVE")
    try:
        precheck()
        ec.prepare()
        ec.start()
        anchor = time.monotonic()
        ec.save(
            ROOT / "schedule-anchor.json",
            dict(
                utc=datetime.now(UTC).isoformat(), monotonic=anchor, schedule=SCHEDULE
            ),
        )
        integration = Integration()
        from scripts.product_v050.engineering_traffic import traffic

        traffic(1, anchor=anchor, pair_offsets=SCHEDULE["traffic"][0])
        wait_until(anchor, SCHEDULE["rounds"][0])
        proofs = integration.round(1)
        wait_until(anchor, SCHEDULE["baseline_build"])
        integration.baseline(proofs)
        traffic(2, anchor=anchor, pair_offsets=SCHEDULE["traffic"][1])
        wait_until(anchor, SCHEDULE["rounds"][1])
        integration.round(2)
        traffic(3, anchor=anchor, pair_offsets=SCHEDULE["traffic"][2])
        wait_until(anchor, SCHEDULE["rounds"][2])
        integration.round(3)
    except Exception as exc:
        ec.save(
            ROOT / "failure.json",
            dict(
                type=type(exc).__name__,
                message=str(exc)[:300],
                at=datetime.now(UTC).isoformat(),
            ),
        )
        raise
    finally:
        close_and_cleanup(integration)
        ec.save(
            ROOT / "finished.json",
            dict(
                at=datetime.now(UTC).isoformat(),
                elapsed_seconds=(datetime.now(UTC) - started).total_seconds(),
            ),
        )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation", choices=["run", "cleanup"])
    a = p.parse_args()
    bind()
    if a.operation == "run":
        run()
    else:
        ec.cleanup()
