"""No Docker or network; exercise budget and normal storage wiring boundaries."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import json

import httpx
import pytest
from scripts.product_v050 import (
    engineering_integration as run,
    engineering_capture as ec,
)
from scripts.product_v050 import default_credentials as dc, ingestion_evidence as ie
from scripts.product_v050.engineering_configuration import complete_process_projection
from ecomsre.product.connectors._http import BoundedHttpTransportV1
from ecomsre.product.connectors.credentials import CredentialResolverV1
from test_ingestion_evidence import FIXTURE, QUERIES, setup


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "ROOT", tmp_path)
    monkeypatch.setattr(ec, "ROOT", tmp_path)
    monkeypatch.setattr(ec, "CLEANING", False)
    ec.save(
        tmp_path / "authorization.json",
        dict(
            deadline=(datetime.now(UTC) + timedelta(minutes=90)).isoformat(),
            caps=run.CAPS,
        ),
    )
    return tmp_path


def test_guard_counts_physical_reads_and_denies_before_network(root):
    sent = []
    transport = httpx.MockTransport(
        lambda req: sent.append(req) or httpx.Response(200, json={})
    )
    http = BoundedHttpTransportV1(
        credential_resolver=CredentialResolverV1(environment={}),
        credential_refs={},
        timeout_seconds=1,
        maximum_response_bytes=1024,
        transport=transport,
    )
    with run.counted():
        http.request_json(
            "GET", "http://127.0.0.1:19090/api/v1/query", params={"query": "fixture"}
        )
        with pytest.raises(ValueError, match="ENDPOINT_DENIED"):
            http.request_json("POST", "https://example.invalid/write")
    http.close()
    assert len(sent) == 1
    assert len((root / "operations.jsonl").read_text().splitlines()) == 1


def test_current_runtime_updates_atomically_preserving_history(root):
    obj = object.__new__(run.Integration)
    obj.env = "env-" + "0" * 24
    obj.data = root / "product"
    obj.authority = lambda env: SimpleNamespace(connector_binding_sha256="a" * 64)
    for n in [1, 2]:
        observed = dict(
            observed_at=float(n),
            services={
                s: dict(state="RUNNING", healthy=True, restart_count=0)
                for s in run.vc.SERVICES
            },
        )
        obj.put_runtime(observed, root / f"round-{n}")
    assert (
        ec.load(root / "round-1/typed-runtime.json")["observed_at"]
        != ec.load(root / "round-2/typed-runtime.json")["observed_at"]
    )
    assert ec.load(obj.data / "pilot/runtime-current.json") == ec.load(
        root / "round-2/typed-runtime.json"
    )


def test_product_shutdown_failure_cannot_skip_cleanup(root, monkeypatch):
    ec.save(root / "declared-resources.json", {})
    calls = []
    monkeypatch.setattr(ec, "cleanup", lambda: calls.append("cleanup"))

    class Client:
        def __exit__(self, *args):
            raise RuntimeError("shutdown")

    with pytest.raises(RuntimeError, match="shutdown"):
        run.close_and_cleanup(SimpleNamespace(client=Client()))
    assert calls == ["cleanup"]
    assert (root / "product-close-failure.json").exists()


def test_unknown_known_option_values_are_not_saved():
    p = complete_process_projection(
        "/bin/java\0OTEL_JAVAAGENT_EXTENSIONS=/SECRET\0-Dotel.javaagent.configuration-file=/SECRET\0",
        container_id="a" * 64,
        observed_at=1,
    )
    assert "SECRET" not in json.dumps(p)
    assert p["allowed_environment"]["OTEL_JAVAAGENT_EXTENSIONS"] == "UNKNOWN_VALUE"


@pytest.mark.parametrize("damage", [None, "future", "before"])
def test_configuration_operations_keep_distinct_real_times(credential_factory, damage):
    import hashlib

    objects = {}

    def put(d):
        raw = json.dumps(d).encode()
        h = hashlib.sha256(raw).hexdigest()
        objects[h] = raw
        return h

    binding = ie.topology_v4(
        FIXTURE["collector"],
        FIXTURE["prometheus_command"],
        QUERIES,
        deployment_id="fixture",
    )
    _, req = setup()
    c, proofs = credential_factory(put, binding, [req], "fixture", "i")
    for k, offset in [
        ("runtime", 60),
        ("process", 10),
        ("jar", 20),
        ("mounted_config", 30),
    ]:
        proofs[k]["observed_at"] = req["end"] + offset
    if damage:
        proofs["process"]["observed_at"] = req["end"] + (
            121 if damage == "future" else -1
        )
    proofs["jar"]["process_configuration_sha256"] = ie.sha(proofs["process"])
    proofs["runtime"]["services"]["otel-collector"]["mounted_config_sha256"] = ie.sha(
        proofs["mounted_config"]
    )
    c["proofs"].update({k: put(v) for k, v in proofs.items()})

    def resolve():
        return dc.resolve(
            put(c),
            objects.__getitem__,
            collector=FIXTURE["collector"],
            binding=binding,
            occurrence="fixture",
            incident_id="i",
            requirements=[req],
        )

    if damage:
        with pytest.raises(ValueError, match="not current"):
            resolve()
    else:
        assert resolve()["kafka"]["period_seconds"] == 60


def test_new_docker_cap_keeps_cleanup_reserve(root):
    for _ in range(77):
        ec.reserve("docker_reads", {})
    with pytest.raises(ValueError, match="RESERVE"):
        ec.reserve("docker_reads", {})
    ec.CLEANING = True
    for _ in range(13):
        ec.reserve("docker_reads", {})
    with pytest.raises(ValueError, match="CAP"):
        ec.reserve("docker_reads", {})


def test_schedule_and_authority_are_bounded():
    assert run.CAPS["engineering_incidents"] == 2
    assert all(
        run.CAPS[k] == 0
        for k in ("provider", "faults", "recovery_writes", "holdout", "promotion")
    )
    assert run.SCHEDULE["baseline_build"] < run.SCHEDULE["rounds"][1] - 300
    assert sum(map(len, run.SCHEDULE["traffic"])) * 2 == 30
    assert run.SCHEDULE["rounds"] == [570, 1380, 1680]


def test_incident_sorts_ids_not_logical_service_order(root):
    obj = object.__new__(run.Integration)
    ec.save(
        root / "baseline-status.json",
        dict(status="BUILDER_ACCEPTED", frozen_at="2020-01-01T00:00:00+00:00"),
    )
    services = [
        SimpleNamespace(logical_service=s, service_id="svc-" + c * 24)
        for s, c in zip(run.vc.SERVICES, ["f", "a", "d", "b"])
    ]
    obj.app = SimpleNamespace(
        state=SimpleNamespace(
            services=SimpleNamespace(
                get_map=lambda env: SimpleNamespace(services=services)
            )
        )
    )
    obj.env = "env-" + "0" * 24
    captured = []

    def post(path, *, json):
        from ecomsre.product.incidents.contracts import IncidentCreateV1

        IncidentCreateV1.model_validate(json)
        captured.append(json)
        return SimpleNamespace(status_code=201, json=lambda: {"incident_id": "fixture"})

    obj.client = SimpleNamespace(post=post)
    assert obj.incident(1800000000, root / "round-2") == "fixture"
    assert captured[0]["candidate_service_ids"] == sorted(
        s.service_id for s in services
    )


def test_real_api_worker_wiring_uses_temporary_database_and_transport_fixtures(
    root, monkeypatch
):
    """Exercise the actual payload, baseline worker and diagnosis worker; no sockets."""
    import time
    from ecomsre.product.jobs import worker
    from ecomsre.product.connectors.registry import ConnectorRegistryV1

    plan = {
        "services": {
            "checkout": {"container_name": "ecomsre-fixture-checkout"},
            "prometheus": {"command": FIXTURE["prometheus_command"]},
        }
    }
    ec.save(root / "compose.json", plan)
    ec.save(root / "collector.json", FIXTURE["collector"])
    ec.save(
        root / "manifest.json", {"campaign": "fixture", "labels": {"fixture": "yes"}}
    )
    ec.save(
        root / "precheck.json",
        {"daemon": "fixture", "context": "fixture", "endpoint": "unix:///fixture"},
    )
    prior = root / "prior"
    ec.save(prior / "compose.json", plan)
    ec.save(
        prior / "validation-authorization.json",
        {
            "plan": {
                "collection": {
                    "actual_queries": QUERIES,
                    "preparation_query_keys": list(QUERIES),
                }
            }
        },
    )
    monkeypatch.setattr(ec, "PRIOR", prior)
    assert run.offline_preflight()["environment_api_status"] == 201
    now = time.time()
    ec.save(
        root / "started.json",
        {
            "resources": {
                "container": [
                    {
                        "Id": c * 64,
                        "Image": "sha256:" + c * 64,
                        "Config": {
                            "Labels": {
                                "fixture": "yes",
                                "com.docker.compose.service": s,
                            }
                        },
                        "State": {
                            "StartedAt": datetime.fromtimestamp(
                                now - 1500, UTC
                            ).isoformat()
                        },
                    }
                    for s, c in zip(run.vc.SERVICES, "abcd")
                ]
            }
        },
    )
    requests = []

    def respond(request):
        requests.append(request)
        path = request.url.path
        if path.endswith("/values"):
            return httpx.Response(
                200, json={"status": "success", "data": list(run.vc.SERVICES)}
            )
        if path.endswith("/query_range"):
            params = request.url.params
            start, end = float(params["start"]), float(params["end"])
            q = params["query"]
            value = 0 if "STATUS_CODE_ERROR" in q or "queue" in q else 1
            values = [
                [start + n * 10, str(value)] for n in range(int((end - start) / 10) + 1)
            ]
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "resultType": "matrix",
                        "result": [{"metric": {}, "values": values}],
                    },
                },
            )
        if path.endswith("/api/services"):
            return httpx.Response(200, json={"data": list(run.vc.SERVICES)})
        if path.endswith("/api/traces"):
            return httpx.Response(200, json={"data": []})
        if path.endswith("/_search"):
            return httpx.Response(
                200,
                json={
                    "hits": {"total": {"value": 0}, "hits": []},
                    "aggregations": {
                        "services": {"buckets": [{"key": s} for s in run.vc.SERVICES]}
                    },
                },
            )
        raise AssertionError(str(request.url))

    def registry(**kwargs):
        return ConnectorRegistryV1(
            **kwargs,
            transports={
                name: httpx.MockTransport(respond)
                for name in ("prometheus", "opensearch", "jaeger")
            },
        )

    monkeypatch.setattr(worker, "ConnectorRegistryV1", registry)
    integration = run.Integration()
    try:
        observation = dict(
            observed_at=now,
            services={
                s: dict(state="RUNNING", healthy=True, restart_count=0)
                for s in run.vc.SERVICES
            },
        )
        integration.environment(observation, root / "round-1")
        assert ec.load(root / "environment-create.json")["status"] == 201
        assert ec.load(root / "environment-verify/job.json")["status"] == "SUCCEEDED"
        assert all(
            x["status"] == 201 for x in ec.load(root / "deployment-changes.json")
        )
        assert integration.settings.sqlite_path.is_relative_to(root.resolve())
        # Raw v4 proofs have separate fixture tests; this test covers Product wiring.
        monkeypatch.setattr(integration, "samples", lambda *a, **k: None)
        integration.baseline({})
        assert ec.load(root / "baseline-status.json")["status"] == "BUILDER_ACCEPTED", (
            ec.load(root / "baseline/job.json")
        )
        end = time.time() + 600
        observation["observed_at"] = end - 1
        integration.put_runtime(observation, root / "round-2")
        iid = integration.incident(end, root / "round-2")
        assert iid is not None, ec.load(root / "round-2/incident-create.json")
        integration.diagnose(iid, root / "round-2")
        assert ec.load(root / "round-2/diagnosis-job/job.json")["status"] == "SUCCEEDED"
        assert (root / "round-2/memory.json").exists()
        assert requests and all(r.url.host == "127.0.0.1" for r in requests)
    finally:
        integration.client.__exit__(None, None, None)


def test_environment_api_rejects_missing_pilot_prefix_before_deployment(root):
    from copy import deepcopy
    from fastapi.testclient import TestClient
    from ecomsre.product.app import create_app
    from ecomsre.product.settings import ProductSettingsV1

    plan = {"services": {"checkout": {"container_name": "ecomsre-fixture-checkout"}}}
    payload = run.environment_payload(plan, "a" * 64)
    broken = deepcopy(payload)
    for c in broken["connector_configs"]:
        if c["kind"] == "PILOT_RUNTIME":
            c["settings"]["snapshot_ref"] = "runtime-current.json"
    with TestClient(
        create_app(ProductSettingsV1(data_root=root / "api-test"))
    ) as client:
        assert client.post("/v1/environments", json=broken).status_code == 422
        assert client.post("/v1/environments", json=payload).status_code == 201


def test_resumed_traffic_refuses_existing_next_output_before_request(root):
    import hashlib
    from scripts.product_v050.engineering_traffic import traffic

    directory = root / "traffic-1"
    directory.mkdir()
    for name in ("cart", "checkout"):
        (directory / f"1-{name}.body").write_bytes(b"fixture")
        ec.save(
            directory / f"1-{name}.json",
            dict(
                status=200,
                error=None,
                truncated=False,
                response_sha256=hashlib.sha256(b"fixture").hexdigest(),
            ),
        )
    (directory / "2-cart.body").write_bytes(b"already seen")
    with pytest.raises(ValueError, match="OUTPUT_EXISTS"):
        traffic(1, start_ordinal=2)
    assert (directory / "2-cart.body").read_bytes() == b"already seen"
