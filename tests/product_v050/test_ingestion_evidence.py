"""Actual retained topology; sample matrices below are explicitly synthetic."""

from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path

import httpx
import pytest
from test_final_closure import closure  # noqa: F401
from scripts.product_v050 import ingestion_evidence as ie
from scripts.product_v050.validation_capture import capture, protocol_v2, verify_raw
from ecomsre.product.connectors._http import BoundedHttpTransportV1
from ecomsre.product.connectors.credentials import CredentialResolverV1

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/retained_otlp_topology.json").read_text()
)
SELECTOR = 'kafka_consumer_group_lag_ratio{group="fraud-detection",topic="orders"}'
QUERIES = {"prometheus:queue_lag:fraud-detection": "sum(" + SELECTOR + ")"}


def setup():
    binding = ie.topology(FIXTURE["collector"], FIXTURE["prometheus_command"], QUERIES)
    requirement = dict(
        query_key=next(iter(QUERIES)),
        query=next(iter(QUERIES.values())),
        start=1000,
        end=1300,
    )
    return binding, requirement


def body(times=range(1000, 1301, 10), **labels):
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {
                        "__name__": "kafka_consumer_group_lag_ratio",
                        "group": "fraud-detection",
                        "topic": "orders",
                    }
                    | labels,
                    "values": [[t, "4"] for t in times],
                }
            ],
        },
    }


def packet(binding, req, payload):
    _, _, params = ie.expected_reads(req["query"], req["start"], req["end"])[0]
    now = datetime.now(UTC).isoformat()
    entry = dict(
        method="GET",
        url="http://127.0.0.1:19090/api/v1/query",
        params=params,
        status_code=200,
        truncated=False,
        occurrence="new-fixture",
        requested_at=now,
        received_at=now,
        action_context=dict(
            ingestion_version=ie.VERSION,
            incident_id="i",
            binding_sha256=binding["sha256"],
        ),
        response_object_sha256="raw",
    )
    return [entry], lambda key: json.dumps(payload).encode()


def assess(payload, mutate=None):
    binding, req = setup()
    entries, reader = packet(binding, req, payload)
    if mutate:
        mutate(entries)
    return ie.verify(
        binding,
        entries=entries,
        occurrence="new-fixture",
        incident_id="i",
        queries=QUERIES,
        read_bytes=reader,
        collector=FIXTURE["collector"],
        command=FIXTURE["prometheus_command"],
        requirements=[req],
    )


def test_retained_push_topology_empty_targets_and_fresh_samples():
    assert not FIXTURE["targets_response"]["data"]["activeTargets"]
    result = assess(body())
    assert result["passed"] and result["mode"] == "OTLP_PUSH"
    assert result["business_health"] == "NOT_EVALUATED"
    assert result["exact_ingestion_latency"] == "UNKNOWN_NOT_EXPOSED"


@pytest.mark.parametrize(
    "payload,reason",
    [
        (body(range(1000, 1101, 10)), "STALE"),
        (
            {"status": "success", "data": {"resultType": "matrix", "result": []}},
            "EMPTY",
        ),
        (body(group="payment"), "WRONG_SERVICE_OR_SELECTOR"),
        (body([1000, 1300]), "WINDOW_INCOMPLETE"),
        (body([1301]), "SAMPLE_TIME_SCOPE_ERROR"),
    ],
)
def test_target_samples_fail_closed(payload, reason):
    result = assess(payload)
    assert not result["passed"]
    assert result["queries"][0]["selectors"][SELECTOR] == reason


def test_healthy_unrelated_targets_and_new_query_grid_do_not_prove_samples():
    binding, req = setup()
    entries, reader = packet(binding, req, body(range(1000, 1101, 10)))
    # A query_range matrix with fresh grid timestamps is not accepted as raw samples.
    entries[0]["url"] = "http://127.0.0.1:19090/api/v1/query_range"
    result = ie.verify(
        binding,
        entries=entries,
        occurrence="new-fixture",
        incident_id="i",
        queries=QUERIES,
        read_bytes=reader,
        collector=FIXTURE["collector"],
        command=FIXTURE["prometheus_command"],
        requirements=[req],
    )
    assert not result["passed"]
    assert result["queries"][0]["selectors"][SELECTOR] == "WRONG_SAMPLE_REQUEST"
    assert not assess(body(range(1000, 1101, 10)))["passed"]


def test_deployment_mode_and_config_mismatch():
    binding, _ = setup()
    collector = deepcopy(FIXTURE["collector"])
    collector["service"]["pipelines"]["metrics"]["processors"] = []
    with pytest.raises(ValueError, match="pipeline"):
        ie.validate_topology(binding, collector, FIXTURE["prometheus_command"], QUERIES)
    binding["mode"] = "SCRAPE"
    with pytest.raises(ValueError, match="mode/configuration"):
        ie.validate_topology(
            binding, FIXTURE["collector"], FIXTURE["prometheus_command"], QUERIES
        )


def test_sparse_error_absence_requires_same_source_companion_not_zero_filling():
    base = (
        'traces_span_metrics_calls_total{service_name="payment",service_name!="kafka"}'
    )
    errors = base[:-1] + ',status_code="STATUS_CODE_ERROR"}'
    assert ie.query_coverage("", {base: "FRESH_COVERED", errors: "EMPTY"}).startswith(
        "SUPPORTED"
    )
    assert (
        ie.query_coverage("", {base: "EMPTY", errors: "EMPTY"})
        == "UNKNOWN_NO_COMPLETE_SOURCE"
    )
    assert (
        ie.query_coverage("", {base: "STALE", errors: "EMPTY"})
        == "INVALID_SAMPLE_EVIDENCE"
    )


def test_raw_capture_and_sample_validator_share_wire_evidence(tmp_path):
    from types import SimpleNamespace
    import hashlib

    saved = {}

    def put(data, media_type):
        digest = hashlib.sha256(data).hexdigest()
        saved[digest] = data
        return SimpleNamespace(object_sha256=digest)

    objects = SimpleNamespace(put_bytes=put)
    binding, req = setup()
    with closing(
        BoundedHttpTransportV1(
            credential_resolver=CredentialResolverV1(),
            credential_refs={},
            timeout_seconds=1,
            maximum_response_bytes=10000,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body())),
        )
    ) as http:
        with capture(
            objects, tmp_path / "raw.jsonl", occurrence="new-fixture"
        ) as entries:
            ie.acquire(
                http,
                "http://127.0.0.1:19090",
                binding,
                [req],
                incident_id="i",
                monotonic=lambda: 0,
            )
    assert entries[0]["params"]["query"] == SELECTOR + "[301s]"
    assert "step" not in entries[0]["params"]
    result = ie.verify(
        binding,
        entries=entries,
        occurrence="new-fixture",
        incident_id="i",
        queries=QUERIES,
        read_bytes=saved.__getitem__,
        collector=FIXTURE["collector"],
        command=FIXTURE["prometheus_command"],
        requirements=[req],
    )
    assert result["passed"]


def test_bounded_prepare_does_not_dispatch_when_over_budget():
    binding, req = setup()
    binding["max_requests"] = 0
    with pytest.raises(ValueError, match="cap exceeded before dispatch"):
        ie.acquire(
            None,
            "http://127.0.0.1",
            binding,
            [req],
            incident_id=None,
            monotonic=lambda: 0,
        )


def test_v2_cannot_accept_a_legacy_scrape_receipt():
    plan = protocol_v2(
        QUERIES
        | {
            "prometheus:cpu:fraud-detection": 'rate(container_cpu_usage_nanoseconds_total{container_name="fraud-detection"}[5m])'
        },
        collector=FIXTURE["collector"],
        prometheus_command=FIXTURE["prometheus_command"],
        preparation_query_keys=list(QUERIES) + ["prometheus:cpu:fraud-detection"],
    )
    binding, req = setup()
    entries, reader = packet(binding, req, body())
    with pytest.raises(ValueError, match="receipt version"):
        verify_raw(
            entries,
            occurrence="new-fixture",
            incident_id="i",
            snapshots=[],
            queries=QUERIES,
            scrape={"scrape_recency_passed": True, "incident_id": "i"},
            ingestion=plan["ingestion"],
            read_bytes=reader,
        )


def test_v2_evidence_reaches_qualification_freeze_and_direct_promotion(closure):  # noqa: F811
    """Synthetic samples, actual normal fixture diagnoses, no validator stubs."""
    from datetime import timedelta
    from test_validation_batch import parent, plan
    from scripts.product_v050.validation_runner import ValidationRunner
    from ecomsre.product.knowledge import validation_batch_v050 as batch
    from ecomsre.product.knowledge.shadow_controls_v050 import CONTROL_VERSION
    from ecomsre.product.errors import ProductError

    c = closure
    original = parent(c)
    queries = {}
    for service in ("checkout", "payment"):
        for kind, metric in [
            ("error_rate", "kafka_request_failed_total"),
            ("latency", "kafka_produce_request_time_95p_milliseconds"),
            ("request_support", "kafka_request_count_total"),
            ("cpu", "container_cpu_usage_nanoseconds_total"),
            ("memory", "container_memory_usage_total_bytes"),
            ("queue_lag", "kafka_consumer_group_lag_ratio"),
        ]:
            label = (
                "container_name"
                if kind in ("cpu", "memory")
                else "group"
                if kind == "queue_lag"
                else "service_name"
            )
            selector = f'{metric}{{{label}="{service}"}}'
            queries[f"prometheus:{kind}:{service}"] = (
                f"sum(rate({selector}[5m]))"
                if kind == "request_support"
                else f"sum({selector})"
            )
    collection = protocol_v2(
        queries,
        collector=FIXTURE["collector"],
        prometheus_command=FIXTURE["prometheus_command"],
        preparation_query_keys=list(queries),
    )
    candidate = batch.install(
        c.evo,
        original.registration_id,
        plan=plan() | {"collection": collection},
        authorization_sha256="a" * 64,
        episode_ledger=c.runner.episode_ledger(),
    )
    runner = ValidationRunner(
        c.evo, original.environment_id, episode_root=c.runner.episode_root
    )
    objects = c.app.state.object_store
    collector_digest = objects.put_json(FIXTURE["collector"]).object_sha256
    command_digest = objects.put_json(FIXTURE["prometheus_command"]).object_sha256

    def evidence(requirements, iid, occurrence, normal=(), preparation=False):
        entries = list(normal)

        class Http:
            def request_json(self, method, url, params):
                selector = ie.SELECTOR.search(params["query"]).group()
                labels = {"__name__": selector.split("{")[0]} | {
                    k: v for k, op, v in ie.MATCHER.findall(selector) if op == "="
                }
                if url.endswith("/query"):
                    width = int(params["query"].rsplit("[", 1)[1][:-2])
                    end = params["time"]
                    start = end - width + 1
                else:
                    start, end = params["start"], params["end"]
                times = [start + 10 * n for n in range(int((end - start) // 10) + 1)]
                if times[-1] != end:
                    times.append(end)
                payload = {
                    "status": "success",
                    "data": {
                        "resultType": "matrix",
                        "result": [
                            {"metric": labels, "values": [[t, "2"] for t in times]}
                        ],
                    },
                }
                now = datetime.now(UTC).isoformat()
                entries.append(
                    dict(
                        method=method,
                        url=url,
                        params=params,
                        status_code=200,
                        truncated=False,
                        occurrence=occurrence,
                        requested_at=now,
                        received_at=now,
                        response_object_sha256=objects.put_json(payload).object_sha256,
                        action_context=dict(
                            ingestion_version=ie.VERSION,
                            incident_id=iid,
                            binding_sha256=collection["ingestion"]["sha256"],
                        ),
                    )
                )

        ie.acquire(
            Http(),
            "http://127.0.0.1:19090",
            collection["ingestion"],
            requirements,
            incident_id=iid,
            monotonic=lambda: 0,
            preparation=preparation,
        )
        assessment = ie.verify(
            collection["ingestion"],
            entries=entries,
            occurrence=occurrence,
            incident_id=iid,
            queries=queries,
            read_bytes=objects.read_bytes,
            collector=FIXTURE["collector"],
            command=FIXTURE["prometheus_command"],
            requirements=requirements,
        )
        assert assessment["passed"]
        return entries, dict(
            version=ie.VERSION,
            collector_object_sha256=collector_digest,
            prometheus_command_object_sha256=command_digest,
            requirements=requirements,
            assessment=assessment,
        )

    at = datetime.now(UTC)
    runner._keep(
        "ingestion-preparation-attempt",
        dict(
            at={"utc": at.isoformat()},
            incident_created=False,
            batch_sha256=runner.batch["sha256"],
        ),
    )
    end = at.timestamp()
    requirements = [
        dict(query_key=k, query=q, start=end - 300, end=end) for k, q in queries.items()
    ]
    entries, proof = evidence(
        requirements, None, "INGESTION_READINESS_NOT_EPISODE", preparation=True
    )
    proof["raw_index_sha256"] = objects.put_json(entries).object_sha256
    runner._keep("ingestion-preparation", proof)
    # Re-signed historical window cannot substitute for this attempt's anchor.
    key = batch.BATCH + ":ingestion-preparation"
    with c.evo.store.connect() as conn:
        saved_row = conn.execute(
            "SELECT payload_json FROM knowledge_closure_runner_v050 WHERE entry_key=?",
            (key,),
        ).fetchone()[0]
        altered = json.loads(saved_row)
        for r in altered["value"]["requirements"]:
            r["start"] -= 86400
            r["end"] -= 86400
        altered["sha256"] = batch.sha(altered["value"])
        conn.execute(
            "UPDATE knowledge_closure_runner_v050 SET payload_json=? WHERE entry_key=?",
            (json.dumps(altered), key),
        )
    with pytest.raises(ValueError, match="this batch attempt"):
        batch.verify_ingestion_preparation(c.evo, runner.batch)
    with c.evo.store.connect() as conn:
        conn.execute(
            "UPDATE knowledge_closure_runner_v050 SET payload_json=? WHERE entry_key=?",
            (saved_row, key),
        )
    cases = {}
    for slot, kind, stratum in [
        ("N4", "positive", "POSITIVE_INCIDENT"),
        ("N5", "healthy", "NO_INCIDENT"),
        ("N6", "core", "CONFUSABLE_CORE_KNOWN"),
    ]:
        now = datetime.now(UTC)
        runner._keep(
            "preparation:" + slot,
            dict(
                earliest_legal_observation=now.isoformat(),
                deadline=(now + timedelta(minutes=5)).isoformat(),
                mode="FIXTURE_ONLY",
            ),
        )
        runner.reserve_episode(slot)
        iid = c.new(runner.slots[slot], kind, supplement=True)
        runner.bind_episode(slot, iid)
        runner.finish_episode(slot, succeeded=True, reason="FIXTURE_ONLY")
        snapshots = [
            o.payload
            for o in c.evo.knowledge._evidence(
                iid, c.evo.knowledge._diagnosis(iid).diagnosis_id
            ).objects
            if "connector_result" in o.payload
        ]
        normal = []
        for snapshot in snapshots:
            action = snapshot["action"]
            result = snapshot["connector_result"]
            window = result["window"]
            source = result["source"]
            if source not in {"METRICS", "LOGS", "TRACES"}:
                continue
            start = datetime.fromisoformat(window["started_at"]).timestamp()
            end = datetime.fromisoformat(window["ended_at"]).timestamp()
            context = dict(
                incident_id=iid,
                action_id=action["action_id"],
                context={"window": window},
            )
            base = dict(
                occurrence=runner.slots[slot], truncated=False, action_context=context
            )
            if source == "METRICS":
                names = {
                    "ERROR_RATE": "error_rate",
                    "LATENCY_P95_MS": "latency",
                    "REQUEST_SUPPORT": "request_support",
                    "QUEUE_LAG": "queue_lag",
                }
                for service in action["target_services"]:
                    for metric in action["request"]["metric_kinds"]:
                        normal.append(
                            base
                            | dict(
                                url="http://127.0.0.1:19090/api/v1/query_range",
                                params=dict(
                                    query=queries[
                                        f"prometheus:{names[metric]}:{service}"
                                    ],
                                    start=start,
                                    end=end,
                                    step=10,
                                ),
                            )
                        )
            elif source == "TRACES":
                normal.append(
                    base
                    | dict(
                        url="http://127.0.0.1:19090/traces",
                        params=dict(start=int(start * 1000000), end=int(end * 1000000)),
                    )
                )
            else:
                normal.append(
                    base
                    | dict(
                        url="http://127.0.0.1:19090/logs",
                        json_body={
                            "query": {
                                "bool": {
                                    "filter": [
                                        {
                                            "range": {
                                                "timestamp": {
                                                    "gte": window["started_at"],
                                                    "lte": window["ended_at"],
                                                }
                                            }
                                        }
                                    ]
                                }
                            }
                        },
                    )
                )
        requirements = ie.event_requirements(normal, iid, queries)
        entries, proof = evidence(requirements, iid, runner.slots[slot], normal)
        runner.seal_collection(
            slot,
            iid,
            raw_index=dict(mode="FIXTURE_ONLY", entries=entries),
            ingestion_receipt=proof,
        )
        assert runner.qualify(candidate, slot)["qualified"]
        cases[iid] = stratum
    c.evo.freeze(
        candidate.registration_id, cases, derived_controls_version=CONTROL_VERSION
    )
    assert c.evo.evaluate(candidate.registration_id).gate_passed
    # Corruption of a referenced raw object is caught again at direct promotion.
    sample = next(
        e for e in entries if (e.get("action_context") or {}).get("ingestion_version")
    )
    path = objects._path_for(sample["response_object_sha256"])
    before = path.read_bytes()
    path.write_bytes(b"{}")
    with pytest.raises((ValueError, ProductError, RuntimeError)):
        c.evo.control_qualifications(candidate, cases)
    with pytest.raises((ValueError, ProductError, RuntimeError)):
        c.evo.freeze(
            candidate.registration_id, cases, derived_controls_version=CONTROL_VERSION
        )
    with pytest.raises((ValueError, ProductError, RuntimeError)):
        c.evo.promote(candidate.registration_id)
    path.write_bytes(before)
    c.evo.promote(candidate.registration_id)
    c.evo.revoke(candidate.registration_id)


def test_unrelated_scrape_target_cannot_rescue_stale_business_samples():
    def unrelated(entries):
        entries.append(
            dict(
                occurrence="new-fixture",
                truncated=False,
                action_context=None,
                url="http://127.0.0.1:19090/api/v1/targets",
                params={},
                status_code=200,
                response_object_sha256="unrelated-healthy-target-not-used",
            )
        )

    assert not assess(body(range(1000, 1101, 10)), mutate=unrelated)["passed"]


def test_no_implicit_alternative_for_non_or_expression():
    states = {
        'traces_span_metrics_calls_total{service_name="payment"}': "FRESH_COVERED",
        'kafka_request_count_total{service_name="payment"}': "EMPTY",
    }
    assert ie.query_coverage("a + b", states) == "UNKNOWN_NO_COMPLETE_SOURCE"
    assert ie.query_coverage("a or b", states).startswith("SUPPORTED")


@pytest.mark.parametrize("stale", [False, True])
def test_future_prepare_helper_is_single_attempt_no_incident(
    closure,  # noqa: F811
    monkeypatch,
    tmp_path,
    stale,  # noqa: F811
):  # noqa: F811
    from types import SimpleNamespace
    from scripts.product_v050 import validation_live as live

    c = closure
    query = 'sum(rate(kafka_request_count_total{service_name="payment"}[5m]))'
    queries = {"prometheus:request_support:payment": query}
    collection = protocol_v2(
        queries,
        collector=FIXTURE["collector"],
        prometheus_command=FIXTURE["prometheus_command"],
        preparation_query_keys=list(queries),
    )
    root = tmp_path / "preparation-only"
    root.mkdir()
    (root / "collector.json").write_text(json.dumps(FIXTURE["collector"]))
    (root / "compose.json").write_text(
        json.dumps(
            {"services": {"prometheus": {"command": FIXTURE["prometheus_command"]}}}
        )
    )
    monkeypatch.setattr(live, "ROOT", root)
    records = {}

    def keep(k, v):
        assert k not in records
        records[k] = v

    runner = SimpleNamespace(
        batch={"sha256": "b" * 64, "plan": {"collection": collection}},
        _get=records.get,
        _keep=keep,
    )
    called = []
    config = SimpleNamespace(
        kind=SimpleNamespace(value="PROMETHEUS"),
        endpoint="http://127.0.0.1:19090",
        credential_refs={},
        settings={"maximum_response_bytes": 10000},
    )
    campaign = SimpleNamespace(
        env="fixture",
        owner=SimpleNamespace(verify=lambda: None),
        app=SimpleNamespace(
            state=SimpleNamespace(
                object_store=c.app.state.object_store,
                environments=SimpleNamespace(
                    get=lambda _: SimpleNamespace(connector_configs=[config])
                ),
            )
        ),
    )
    transport = live.BoundedHttpTransportV1

    def reply(request):
        called.append(str(request.url))
        if request.url.path.endswith("/query"):
            end = float(request.url.params["time"])
            width = int(request.url.params["query"].rsplit("[", 1)[1][:-2])
            times = [
                end - width + 1 + n * 10 for n in range(int((width - 1) // 10) + 1)
            ]
            if stale:
                times = times[:-12]
            payload = {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {
                                "__name__": "kafka_request_count_total",
                                "service_name": "payment",
                            },
                            "values": [[t, "2"] for t in times],
                        }
                    ],
                },
            }
        else:
            payload = {
                "status": "success",
                "data": {"resultType": "matrix", "result": []},
            }
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        live,
        "BoundedHttpTransportV1",
        lambda **kw: transport(**kw, transport=httpx.MockTransport(reply)),
    )
    if stale:
        with pytest.raises(ValueError, match="INCOMPLETE_NO_RETRY"):
            live.prepare_ingestion(campaign, runner)
        assert "ingestion-preparation" not in records
    else:
        live.prepare_ingestion(campaign, runner)
        assert records["ingestion-preparation"]["assessment"]["passed"]
    assert len(called) == 2
    assert not records["ingestion-preparation-attempt"]["incident_created"]
    with pytest.raises(ValueError, match="already attempted"):
        live.prepare_ingestion(campaign, runner)
    assert len(called) == 2


def test_preparation_cannot_omit_required_metric_queries():
    queries = QUERIES | {
        "prometheus:cpu:fraud-detection": 'sum(rate(container_cpu_usage_nanoseconds_total{container_name="fraud-detection"}[5m]))'
    }
    with pytest.raises(ValueError, match="omits required"):
        protocol_v2(
            queries,
            collector=FIXTURE["collector"],
            prometheus_command=FIXTURE["prometheus_command"],
            preparation_query_keys=list(QUERIES),
            target_service="fraud-detection",
        )
