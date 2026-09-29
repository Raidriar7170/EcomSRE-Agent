"""Full offline API/Worker rehearsal. Only external observations and clock are synthetic.

The controlled mapping and its retained public source bytes are unmodified. Docker
proof projections are simulated input (not live attestation); all credential,
raw acquisition, support, baseline and diagnosis validators execute normally.
"""

from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import time

import httpx
import pytest

from scripts.product_v050 import engineering_integration as run
from scripts.product_v050 import engineering_capture as ec, default_credentials as dc
from scripts.product_v050 import ingestion_evidence as ie
from scripts.product_v050.engineering_configuration import complete_process_projection
from ecomsre.product.connectors._http import BoundedHttpTransportV1
from ecomsre.product.connectors.registry import ConnectorRegistryV1
from ecomsre.product.jobs import worker
from test_ingestion_evidence import FIXTURE

ORIGIN = 1800000000.0


def proofs_for(obj, at, *, birth=ORIGIN, damage=None):
    """Simulated allowlisted Docker read projections, using the real reviewed identity."""
    m = dc.mapping()
    process = complete_process_projection(
        "/bin/java\0-javaagent:/tmp/opentelemetry-javaagent.jar\0-Dotel.jmx.target.system=kafka-broker\0"
        "OTEL_SERVICE_NAME=kafka\0OTEL_JMX_CONFIG=/etc/ecomsre/kafka-jmx.yml\0"
        "OTEL_INSTRUMENTATION_METHODS_INCLUDE=kafka.server.KafkaApis[handleProduceRequest]\0"
        "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative\0",
        container_id="a" * 64,
        observed_at=at + 1,
    )
    mounted = dict(
        container_id="b" * 64,
        observed_at=at + 3,
        destination="/etc/ecomsre/collector.json",
        read_only=True,
        source_identity_sha256="c" * 64,
        content=deepcopy(obj.collector),
    )
    services = {
        role: dict(
            service=role,
            image_id=m[key],
            container_id=cid * 64,
            started_at=birth,
            running=True,
            restart_count=0,
        )
        for role, key, cid in [
            ("kafka", "kafka_image_id", "a"),
            ("otel-collector", "collector_image_id", "b"),
        ]
    }
    services["otel-collector"].update(
        version=m["collector_version"],
        command=["--config=/etc/ecomsre/collector.json"],
        entrypoint=None,
        configuration_projection_complete=True,
        override_names=[],
        collector_sha256=ie.sha(obj.collector),
        config_mount_source_sha256="c" * 64,
        mounted_config_sha256=ie.sha(mounted),
    )
    runtime = dict(
        version=dc.OBSERVATION,
        deployment_id=obj.binding["deployment_id"],
        observed_at=at + 4,
        services=services,
    )
    jar = dict(
        container_id="a" * 64,
        observed_at=at + 2,
        process_configuration_sha256=ie.sha(process),
        image_id=m["kafka_image_id"],
        jar_sha256=m["jar_sha256"],
        manifest={"Implementation-Version": m["agent_version"]},
        metric_reader_classes=m["metric_reader_classes"],
    )
    if damage == "stale":
        process["observed_at"] = at - 500
        jar["process_configuration_sha256"] = ie.sha(process)
    if damage == "instance":
        jar["container_id"] = "d" * 64

    def put(value):
        return obj.objects.put_json(value).object_sha256

    refs = {
        k: put(v)
        for k, v in dict(
            runtime=runtime, process=process, jar=jar, mounted_config=mounted
        ).items()
    }
    refs["sources"] = [
        put(v)
        for v in json.loads(
            (
                Path(__file__).parent / "fixtures/reviewed_default_sources.json"
            ).read_text()
        )
    ]
    return refs


@pytest.fixture
def rehearsal(tmp_path, monkeypatch):
    clock = [ORIGIN]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(clock[0], tz)

    # Patch time only, after all application modules have been imported.
    for name, module in list(sys.modules.items()):
        if (
            name.startswith(("ecomsre.", "scripts.product_v050"))
            and getattr(module, "datetime", None) is datetime
        ):
            monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(time, "time", lambda: clock[0])
    monkeypatch.setattr(run, "ROOT", tmp_path)
    monkeypatch.setattr(ec, "ROOT", tmp_path)
    monkeypatch.setattr(ec, "CLEANING", False)
    ec.save(
        tmp_path / "authorization.json",
        dict(
            deadline=datetime.fromtimestamp(ORIGIN + 5400, UTC).isoformat(),
            caps=run.CAPS,
        ),
    )
    plan = {
        "services": {
            "checkout": {"container_name": "ecomsre-fixture-checkout"},
            "prometheus": {"command": FIXTURE["prometheus_command"]},
        }
    }
    ec.save(tmp_path / "compose.json", plan)
    ec.save(tmp_path / "collector.json", FIXTURE["collector"])
    ec.save(
        tmp_path / "manifest.json",
        dict(campaign="rehearsal", labels={"fixture": "yes"}),
    )
    ec.save(
        tmp_path / "precheck.json",
        dict(daemon="fixture", context="fixture", endpoint="unix:///fixture"),
    )
    # Use all actual business query templates, with the actual preparation subset.
    payload = run.environment_payload(plan, "a" * 64)
    templates = next(
        c["settings"]["query_templates"]
        for c in payload["connector_configs"]
        if c["kind"] == "PROMETHEUS"
    )
    queries = {
        f"prometheus:{metric}:{service}": query.replace("{service}", service)
        for metric, query in templates.items()
        for service in run.vc.SERVICES
        if metric != "queue_lag" or service == "fraud-detection"
    }
    prior = tmp_path / "prior"
    ec.save(
        prior / "validation-authorization.json",
        {
            "plan": {
                "collection": {
                    "actual_queries": queries,
                    "preparation_query_keys": list(queries),
                }
            }
        },
    )
    monkeypatch.setattr(ec, "PRIOR", prior)
    ec.save(
        tmp_path / "started.json",
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
                            "StartedAt": datetime.fromtimestamp(ORIGIN, UTC).isoformat()
                        },
                    }
                    for s, c in zip(run.vc.SERVICES, "cdae")
                ]
            }
        },
    )
    requests = []

    def respond(request):
        requests.append(request)
        path = request.url.path
        params = request.url.params
        if path.endswith("/values"):
            data = {"status": "success", "data": list(run.vc.SERVICES)}
        elif path.endswith("/query_range"):
            start, end = float(params["start"]), float(params["end"])
            q = params["query"]
            value = 0 if "STATUS_CODE_ERROR" in q or "queue" in q else 1
            data = {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {},
                            "values": [
                                [start + n * 10, str(value)]
                                for n in range(int((end - start) / 10) + 1)
                            ],
                        }
                    ],
                },
            }
        elif path.endswith("/query"):
            q = params["query"]
            end = float(params["time"])
            width = int(q.rsplit("[", 1)[1][:-2])
            selector = q.rsplit("[", 1)[0]
            labels = {"__name__": selector.split("{")[0]}
            labels.update(
                {k: v for k, op, v in ie.MATCHER.findall(selector) if op == "="}
            )
            period = (
                60
                if labels["__name__"].startswith(("kafka_request_", "kafka_produce_"))
                else 2
            )
            first = max(ORIGIN + period, end - width + 1)
            first = ORIGIN + int((first - ORIGIN + period - 1) // period) * period
            times = range(int(first), int(end) + 1, period)
            metrics = []
            bounds = (
                dc.mapping()["defaults"]["histogram_bounds_milliseconds"] + ["+Inf"]
                if labels["__name__"].endswith("_bucket")
                else [None]
            )
            for bound in bounds:
                metric = dict(labels)
                if bound is not None:
                    metric["le"] = str(bound)
                if labels["__name__"].startswith("traces_span_metrics_"):
                    metric.update(
                        span_name="fixture-normal",
                        status_code=labels.get("status_code", "STATUS_CODE_UNSET"),
                    )

                def value(t):
                    return (
                        0
                        if "STATUS_CODE_ERROR" in selector or "lag" in selector
                        else (t - ORIGIN) / period + 1
                    )

                metrics.append(
                    {"metric": metric, "values": [[t, str(value(t))] for t in times]}
                )
            if (
                labels["__name__"].startswith(("kafka_request_", "kafka_produce_"))
                and labels.get("service_name") != "kafka"
            ) or (
                labels["__name__"].startswith("traces_span_metrics_")
                and labels.get("service_name") == "kafka"
            ):
                metrics = []
            if (
                labels["__name__"] == "traces_span_metrics_calls_total"
                and "STATUS_CODE_ERROR" in selector
            ):
                metrics = []  # source total is present; error absence stays explicit
            data = {
                "status": "success",
                "data": {"resultType": "matrix", "result": metrics},
            }
        elif path.endswith("/api/services"):
            data = {"data": list(run.vc.SERVICES)}
        elif path.endswith("/api/traces"):
            data = {"data": []}
        elif path.endswith("/_search"):
            data = {
                "hits": {"total": {"value": 0}, "hits": []},
                "aggregations": {
                    "services": {"buckets": [{"key": s} for s in run.vc.SERVICES]}
                },
            }
        else:
            raise AssertionError(str(request.url))
        return httpx.Response(200, json=data)

    def registry(**kw):
        return ConnectorRegistryV1(
            **kw,
            transports={
                n: httpx.MockTransport(respond)
                for n in ("prometheus", "opensearch", "jaeger")
            },
        )

    monkeypatch.setattr(worker, "ConnectorRegistryV1", registry)
    # Keep the real transport implementation, replacing only its socket transport.
    original = BoundedHttpTransportV1.__init__

    def init(self, **kw):
        kw.setdefault("transport", httpx.MockTransport(respond))
        original(self, **kw)

    monkeypatch.setattr(BoundedHttpTransportV1, "__init__", init)
    obj = run.Integration()

    def observation():
        return dict(
            observed_at=clock[0],
            services={
                s: dict(state="RUNNING", healthy=True, restart_count=0)
                for s in run.vc.SERVICES
            },
        )

    try:
        yield obj, clock, observation, requests
    finally:
        obj.client.__exit__(None, None, None)


def test_complete_real_path_with_actual_deployment_clock(rehearsal):
    obj, clock, observation, requests = rehearsal
    clock[0] = ORIGIN + run.SCHEDULE["rounds"][0]
    proofs = proofs_for(obj, clock[0])
    obj.environment(observation(), run.ROOT / "round-1")
    assert ec.load(run.ROOT / "environment-verify/job.json")["status"] == "SUCCEEDED"
    clock[0] = ORIGIN + run.SCHEDULE["baseline_build"]
    obj.baseline(proofs)
    receipt = ec.load(run.ROOT / "baseline-support/receipt.json")
    assert receipt["configuration_status"] == "VERIFIED", receipt
    assert receipt["assessment"]["version"] == dc.VERSION
    assert receipt["assessment"]["passed"], receipt["assessment"]
    baseline = ec.load(run.ROOT / "baseline-frozen.json")
    clock[0] = ORIGIN + run.SCHEDULE["rounds"][1]
    end = clock[0]
    obj.put_runtime(observation(), run.ROOT / "round-2")
    iid = obj.incident(end, run.ROOT / "round-2")
    assert iid is not None
    proofs = proofs_for(obj, end)
    clock[0] += 5
    obj.samples(end - 300, end, "round-2", iid, proofs, run.ROOT / "round-2/samples")
    receipt = ec.load(run.ROOT / "round-2/samples/receipt.json")
    assert receipt["configuration_status"] == "VERIFIED", receipt
    assert receipt["assessment"]["passed"], receipt["assessment"]
    obj.diagnose(iid, run.ROOT / "round-2")
    assert ec.load(run.ROOT / "round-2/diagnosis-job/job.json")["status"] == "SUCCEEDED"
    assert (run.ROOT / "round-2/memory.json").exists()
    inc = obj.app.state.incidents.get(iid)
    assert inc.baseline_id == baseline["baseline_id"]
    assert len(obj.queries) == 21
    assert any(r.url.path.endswith("/query") for r in requests)


@pytest.mark.parametrize("damage", ["stale", "instance", "startup"])
def test_real_samples_reject_invalid_configuration_before_http(rehearsal, damage):
    obj, clock, _, requests = rehearsal
    end = ORIGIN + run.SCHEDULE["rounds"][1]
    clock[0] = end + 5
    proofs = proofs_for(
        obj, end, birth=end - 100 if damage == "startup" else ORIGIN, damage=damage
    )
    before = len(requests)
    obj.samples(end - 300, end, "negative", "i", proofs, run.ROOT / "negative")
    receipt = ec.load(run.ROOT / "negative/receipt.json")
    assert receipt["configuration_status"] == "UNKNOWN"
    assert receipt["configuration_error"]
    assert "assessment" not in receipt
    assert len(requests) == before


def test_real_api_rejects_wrong_pilot_and_unsorted_ids(rehearsal):
    obj, clock, observation, _ = rehearsal
    clock[0] = ORIGIN + 570
    payload = run.environment_payload(ec.load(run.ROOT / "compose.json"), "a" * 64)
    next(c for c in payload["connector_configs"] if c["kind"] == "PILOT_RUNTIME")[
        "settings"
    ]["snapshot_ref"] = "runtime-current.json"
    assert obj.client.post("/v1/environments", json=payload).status_code == 422
    obj.environment(observation(), run.ROOT / "round-1")
    clock[0] = ORIGIN + 720
    obj.baseline(proofs_for(obj, ORIGIN + 570))
    clock[0] = ORIGIN + 1380
    iid = obj.incident(clock[0], run.ROOT / "round-2")
    assert iid
    # Replay the actual incident creation request shape with deliberately reverse IDs.
    ids = sorted(
        s.service_id
        for s in obj.app.state.services.get_map(obj.env).services
        if s.logical_service in run.vc.SERVICES
    )
    response = obj.client.post(
        "/v1/incidents",
        json=dict(
            environment_id=obj.env,
            external_incident_key="unsorted",
            alert_name="engineering-observation",
            summary="fixture",
            labels={"fault": "none"},
            started_at=datetime.fromtimestamp(clock[0] - 300, UTC).isoformat(),
            ended_at=datetime.fromtimestamp(clock[0], UTC).isoformat(),
            candidate_service_ids=list(reversed(ids)),
        ),
    )
    assert response.status_code == 422


def test_check_window_cannot_overlap_fixed_baseline(rehearsal):
    obj, clock, observation, _ = rehearsal
    clock[0] = ORIGIN + 570
    obj.environment(observation(), run.ROOT / "round-1")
    clock[0] = ORIGIN + 720
    obj.baseline(proofs_for(obj, ORIGIN + 570))
    with pytest.raises(ValueError, match="BASELINE_NOT_FROZEN_BEFORE_CHECK_WINDOW"):
        obj.incident(ORIGIN + 1000, run.ROOT / "round-2")


def test_baseline_window_before_instance_start_is_not_qualified(rehearsal):
    obj, clock, observation, _ = rehearsal
    clock[0] = ORIGIN + 570
    obj.environment(observation(), run.ROOT / "round-1")
    # A too-early build would require raw samples from before the actual birth.
    # The Product builder can calculate synthetic query results, but v4 refuses
    # their evidence qualification; there is no artificial ancient StartedAt.
    clock[0] = ORIGIN + 600
    obj.baseline(proofs_for(obj, ORIGIN + 450))
    receipt = ec.load(run.ROOT / "baseline-support/receipt.json")
    assert receipt["configuration_status"] == "UNKNOWN"
    assert "lifetime" in receipt["configuration_error"]
