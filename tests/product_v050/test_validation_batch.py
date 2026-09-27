from datetime import UTC, datetime, timedelta
import pytest
from test_final_closure import closure, admit  # noqa: F401
from ecomsre.product.knowledge import validation_batch_v050 as batch
from ecomsre.product.knowledge.shadow_controls_v050 import CONTROL_VERSION
from scripts.product_v050.validation_runner import require_recurrence
from scripts.product_v050.validation_capture import protocol, earliest


def parent(c):
    candidate, _ = admit(c)
    assert c.runner.develop(candidate)["passed"]
    c.runner.select(candidate)
    for slot, kind in [("N4", "positive"), ("N5", "healthy"), ("N6", "core")]:
        c.episode(slot, kind)
    assert c.runner.evaluate_and_promote(candidate).gate_passed
    c.evo.revoke(candidate.registration_id)
    return candidate


def plan():
    return dict(
        holdout_episodes={
            "v-target": "POSITIVE_INCIDENT",
            "v-healthy": "NO_INCIDENT",
            "v-core": "CONFUSABLE_CORE_KNOWN",
        },
        recurrence_episode="v-reuse",
        collection=protocol({"error": "rate(x[5m])"}),
        time_range={
            "start": datetime.now(UTC).isoformat(),
            "end": (datetime.now(UTC) + timedelta(hours=6)).isoformat(),
        },
        derived_controls_version=CONTROL_VERSION,
    )


def fixture_episode(c, runner, candidate, slot, kind):
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
    iid = c.new(runner.slots[slot], kind, supplement=slot != "N7")
    runner.bind_episode(slot, iid)
    runner.finish_episode(slot, succeeded=True, reason="FIXTURE_OBSERVED")
    runner.seal_collection(
        slot, iid, raw_index=dict(mode="FIXTURE_ONLY", incident_id=iid)
    )
    if slot != "N7":
        runner.qualify(candidate, slot)
    return iid


def test_append_validation_preserves_parent_and_promotes_only_new_registration(closure):  # noqa: F811
    c = closure
    p = parent(c)
    with c.evo.store.connect() as conn:
        before = batch.retained(conn, p.registration_id)
    candidate = batch.install(
        c.evo,
        p.registration_id,
        plan=plan(),
        authorization_sha256="a" * 64,
        episode_ledger=c.runner.episode_ledger(),
    )
    assert (
        candidate.proposal == p.proposal
        and candidate.source_request_key == p.source_request_key
    )
    assert candidate.registration_id != p.registration_id
    from scripts.product_v050.validation_runner import ValidationRunner

    runner = ValidationRunner(
        c.evo, p.environment_id, episode_root=c.runner.episode_root
    )
    cases = {}
    for slot, ep, kind, stratum in [
        ("N4", "v-target", "positive", "POSITIVE_INCIDENT"),
        ("N5", "v-healthy", "healthy", "NO_INCIDENT"),
        ("N6", "v-core", "core", "CONFUSABLE_CORE_KNOWN"),
    ]:
        iid = fixture_episode(c, runner, candidate, slot, kind)
        cases[iid] = stratum
    c.evo.freeze(
        candidate.registration_id, cases, derived_controls_version=CONTROL_VERSION
    )
    assert c.evo.evaluate(candidate.registration_id).gate_passed
    c.evo.promote(candidate.registration_id)
    iid = fixture_episode(c, runner, candidate, "N7", "positive")
    diagnosis = c.client.get(f"/v1/incidents/{iid}/diagnosis").json()
    evidence = c.client.get(f"/v1/incidents/{iid}/evidence").json()
    bindings = [
        b
        for o in evidence["objects"]
        for b in o["payload"].get("learned_match_bindings_v050", [])
    ]
    require_recurrence(
        diagnosis=diagnosis,
        bindings=bindings,
        candidate=candidate,
        incident_id=iid,
        before={},
        after={},
    )
    with pytest.raises(ValueError, match="RECURRENCE"):
        require_recurrence(
            diagnosis=diagnosis,
            bindings=[],
            candidate=candidate,
            incident_id=iid,
            before={},
            after={},
        )
    c.evo.revoke(candidate.registration_id)
    assert not c.evo.knowledge.active_investigation_extensions(candidate.environment_id)
    with c.evo.store.connect() as conn:
        assert batch.retained(conn, p.registration_id) == before
        batch.verify(conn, batch.load(conn))
    with pytest.raises(ValueError, match="one validation batch"):
        batch.install(
            c.evo,
            p.registration_id,
            plan=plan(),
            authorization_sha256="a" * 64,
            episode_ledger=c.runner.episode_ledger(),
        )


def test_fixed_effective_lookback_does_not_wait_for_expected_diagnosis():
    p = protocol({"error": "rate(x[5m])", "cpu": "rate(y[1m])"})
    assert p["metric_effective_seconds"] == 600
    assert (
        earliest(
            p,
            last_recovery="2026-09-27T01:00:00Z",
            last_change="2026-09-27T01:00:00Z",
            ready_at="2026-09-27T01:00:00Z",
        ).isoformat()
        == "2026-09-27T02:01:00+00:00"
    )
    with pytest.raises(ValueError):
        protocol({"error": "rate(x[10m])"})


def test_new_batch_unqualified_control_blocks_direct_promotion(closure, monkeypatch):  # noqa: F811
    from ecomsre.product.connectors.fixture import FixtureConnectorV1
    from ecomsre.product.errors import ProductError
    import json

    c = closure
    p = parent(c)
    candidate = batch.install(
        c.evo,
        p.registration_id,
        plan=plan(),
        authorization_sha256="a" * 64,
        episode_ledger=c.runner.episode_ledger(),
    )
    original = FixtureConnectorV1._records

    def anomalous(self, **kw):
        rows = original(self, **kw)
        if kw["current_observation"] and kw["source"].value == "METRICS":
            rows = tuple(
                r.model_copy(update={"value": 100000.0})
                if r.service == "payment" and r.metric_kind.value == "LATENCY_P95_MS"
                else r
                for r in rows
            )
        return rows

    from scripts.product_v050.validation_runner import ValidationRunner

    runner = ValidationRunner(
        c.evo, p.environment_id, episode_root=c.runner.episode_root
    )
    fixture_episode(c, runner, candidate, "N4", "positive")
    with monkeypatch.context() as patch:
        patch.setattr(FixtureConnectorV1, "_records", anomalous)
        fixture_episode(c, runner, candidate, "N5", "healthy")
    assert not runner._get("qualification:N5")["qualified"]
    with pytest.raises(ValueError, match="qualification failed"):
        fixture_episode(c, runner, candidate, "N6", "core")
    with c.evo.store.connect() as conn:
        conn.execute(
            "UPDATE knowledge_candidate_pool_v050 SET state='VALIDATED',evaluation_json=? WHERE registration_id=?",
            (json.dumps({"gate_passed": True}), candidate.registration_id),
        )
    with pytest.raises(ProductError):
        c.evo.promote(candidate.registration_id)
    with pytest.raises(ProductError, match="zero Provider"):
        c.evo.investigations.reserve("forbidden-new-request", {"payload": {}}, 1)
    with pytest.raises(ValueError, match="root"):
        ValidationRunner(
            c.evo, p.environment_id, episode_root=c.runner.episode_root / "other"
        )


def test_raw_http_capture_keeps_request_matrix_and_truncation_without_headers(
    closure,  # noqa: F811
    tmp_path,  # noqa: F811
):  # noqa: F811
    import httpx
    import json
    from scripts.product_v050.validation_capture import capture
    from ecomsre.product.connectors._http import (
        BoundedHttpTransportV1,
        ConnectorRequestError,
    )
    from ecomsre.product.connectors.credentials import CredentialResolverV1

    body = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"service_name": "payment"},
                    "values": [[100, "1"], [110, "2"]],
                }
            ],
        },
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    client = BoundedHttpTransportV1(
        credential_resolver=CredentialResolverV1(),
        credential_refs={},
        timeout_seconds=1,
        maximum_response_bytes=10000,
        transport=transport,
    )
    path = tmp_path / "raw.jsonl"
    with capture(
        closure.app.state.object_store, path, occurrence="fixture-event"
    ) as entries:
        client.request_json(
            "GET",
            "http://127.0.0.1:19090/api/v1/query_range",
            params={"query": "rate(x[5m])", "start": 100, "end": 110, "step": 10},
        )
    assert entries[0]["params"]["step"] == 10 and not entries[0]["truncated"]
    assert "headers" not in json.loads(path.read_text())
    raw = closure.app.state.object_store._path_for(
        entries[0]["response_object_sha256"]
    ).read_bytes()
    assert json.loads(raw) == body
    client._maximum_response_bytes = 10
    with capture(
        closure.app.state.object_store,
        tmp_path / "truncated.jsonl",
        occurrence="fixture-truncated",
    ) as entries:
        with pytest.raises(ConnectorRequestError):
            client.request_json("GET", "http://127.0.0.1:19090/api/v1/query_range")
    assert entries[0]["truncated"] and entries[0]["raw_bytes"] == 11
    client.close()


def test_validation_deployment_keeps_old_mapping_and_rejects_semantic_drift(closure):  # noqa: F811
    from copy import deepcopy
    from ecomsre.product.knowledge import capability_successor_v050 as mapping
    from ecomsre.product.environment.capabilities import CapabilityMatrixRepositoryV1
    from ecomsre.product.jobs.worker import run_one_job

    c = closure
    p = parent(c)
    batch.install(
        c.evo,
        p.registration_id,
        plan=plan(),
        authorization_sha256="a" * 64,
        episode_ledger=c.runner.episode_ledger(),
    )
    env = c.app.state.environments.get(p.environment_id)
    caps = CapabilityMatrixRepositoryV1(c.evo.store)
    old = caps.get(p.environment_id)
    before = dict(
        mode="FIXTURE",
        deployment_id="before",
        environment=env.model_dump(mode="json"),
        resource_births={"fixture": "old"},
        runtime_binding={},
        images={"fixture": "capture-c2aa"},
        service_mapping={"payment": "payment"},
        actual_queries={"fixture": "capture-c2aa"},
        units={"cpu": "percent"},
        windows_and_sampling={"seconds": 10},
        resource_limits={"samples": 5},
        trust_boundary={"scope": "fixture"},
        connector_semantics={"version": "fixture"},
    )
    # An existing predecessor is fixture setup, not a mutation of the real ledger.
    import json

    v = dict(
        old=old.model_dump(mode="json"),
        new=old.model_dump(mode="json"),
        old_deployment=before,
        new_deployment=before,
    )
    intermediate = old.model_copy(
        update={"verified_at": old.verified_at + timedelta(seconds=1)}
    ).model_dump(mode="json", exclude={"capability_sha256"})
    v["new"] = intermediate | {"capability_sha256": batch.sha(intermediate)}
    v["new_deployment"] = deepcopy(before)
    v["new_deployment"].update(
        deployment_id="intermediate", resource_births={"fixture": "intermediate"}
    )
    v["sha256"] = batch.sha(v)
    with c.evo.store.connect() as conn:
        conn.execute(
            "CREATE TABLE knowledge_capability_successor_v050 (environment_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO knowledge_capability_successor_v050 VALUES (?,?)",
            (p.environment_id, json.dumps(v)),
        )
    c.client.post(f"/v1/environments/{p.environment_id}/verify-jobs")
    assert run_one_job(c.settings, worker_id="reverify")
    from ecomsre.product.environment.capabilities import EnvironmentCapabilityMatrixV1

    raw = old.model_dump(mode="json", exclude={"capability_sha256"})
    raw["verified_at"] = old.model_copy(
        update={"verified_at": old.verified_at + timedelta(seconds=2)}
    ).model_dump(mode="json")["verified_at"]
    new = EnvironmentCapabilityMatrixV1.model_validate(
        raw | {"capability_sha256": batch.sha(raw)}
    )
    caps.put(new)
    after = deepcopy(before)
    after.update(deployment_id="after", resource_births={"fixture": "new"})
    altered = deepcopy(after)
    altered["actual_queries"]["fixture"] = "changed"
    with pytest.raises(ValueError, match="semantics differ"):
        batch.install_deployment(
            c.evo.store,
            new=new,
            new_deployment=mapping.DeploymentV050.model_validate(altered),
        )
    batch.install_deployment(
        c.evo.store,
        new=new,
        new_deployment=mapping.DeploymentV050.model_validate(after),
    )
    args = dict(
        environment_id=p.environment_id,
        candidate_environment_id=p.environment_id,
        expected=old.capability_sha256,
        actual=new.capability_sha256,
    )
    assert mapping.admits(c.evo.store, **args)
    assert not mapping.admits(c.evo.store, **(args | {"actual": "0" * 64}))
    with c.evo.store.connect() as conn:
        assert mapping.load(conn, p.environment_id) == v
    with pytest.raises(Exception):
        batch.install_deployment(
            c.evo.store,
            new=new,
            new_deployment=mapping.DeploymentV050.model_validate(after),
        )


@pytest.mark.parametrize("occurrence", ["N4", "N7"])
def test_raw_occurrence_window_and_required_query_are_bound(occurrence):
    from copy import deepcopy
    from scripts.product_v050.validation_capture import verify_raw

    window = {"started_at": "2026-09-27T00:00:00Z", "ended_at": "2026-09-27T00:05:00Z"}
    snap = dict(
        action=dict(
            action_id="a:metrics:payment",
            target_services=["payment"],
            request={"metric_kinds": ["ERROR_RATE"]},
        ),
        connector_result=dict(source="METRICS", window=window),
    )
    params = dict(
        query="rate(x[5m])",
        start=datetime.fromisoformat(window["started_at"]).timestamp(),
        end=datetime.fromisoformat(window["ended_at"]).timestamp(),
        step=10,
    )
    entry = dict(
        occurrence=occurrence,
        truncated=False,
        params=params,
        action_context=dict(
            incident_id="inc", action_id="a:metrics:payment", context={"window": window}
        ),
    )
    kw = dict(
        occurrence=occurrence,
        incident_id="inc",
        snapshots=[snap],
        queries={"prometheus:error_rate:payment": "rate(x[5m])"},
        scrape={"scrape_recency_passed": True, "incident_id": "inc"},
    )
    verify_raw([entry], **kw)
    for damage in ("occurrence", "window", "query"):
        changed = deepcopy(entry)
        if damage == "occurrence":
            changed["occurrence"] = "other"
        elif damage == "window":
            changed["params"]["start"] += 1
        else:
            changed["params"]["query"] = "unrelated"
        with pytest.raises(ValueError):
            verify_raw([changed], **kw)


def test_missing_receipts_cannot_freeze_new_batch(closure):  # noqa: F811
    c = closure
    p = parent(c)
    candidate = batch.install(
        c.evo,
        p.registration_id,
        plan=plan(),
        authorization_sha256="a" * 64,
        episode_ledger=c.runner.episode_ledger(),
    )
    cases = {}
    for ep, kind, stratum in [
        ("v-target", "positive", "POSITIVE_INCIDENT"),
        ("v-healthy", "healthy", "NO_INCIDENT"),
        ("v-core", "core", "CONFUSABLE_CORE_KNOWN"),
    ]:
        iid = c.new(ep, kind)
        c.evo.bind_episode(iid, ep)
        cases[iid] = stratum
    with pytest.raises(ValueError, match="receipt missing"):
        c.evo.freeze(
            candidate.registration_id, cases, derived_controls_version=CONTROL_VERSION
        )


def test_failed_owned_start_consumes_first_slot_and_stops(closure):  # noqa: F811
    from scripts.product_v050.validation_runner import ValidationRunner
    from scripts.product_v050.validation_live import failed_start

    c = closure
    p = parent(c)
    batch.install(
        c.evo,
        p.registration_id,
        plan=plan(),
        authorization_sha256="a" * 64,
        episode_ledger=c.runner.episode_ledger(),
    )
    r = ValidationRunner(c.evo, p.environment_id, episode_root=c.runner.episode_root)
    before = len(r.episode_ledger())
    failed_start(r, RuntimeError("fixture failed startup"))
    assert (
        len(r.episode_ledger()) == before + 1 and not r._get("terminal:N4")["succeeded"]
    )
    with c.evo.store.connect() as conn:
        with pytest.raises(ValueError, match="stopped"):
            batch.verify(conn, batch.load(conn))


def test_v1_raw_receipt_remains_readable_with_null_v2_field(closure, monkeypatch):  # noqa: F811
    import json
    from scripts.product_v050.validation_runner import ValidationRunner

    c = closure
    p = parent(c)
    candidate = batch.install(
        c.evo,
        p.registration_id,
        plan=plan(),
        authorization_sha256="a" * 64,
        episode_ledger=c.runner.episode_ledger(),
    )
    runner = ValidationRunner(
        c.evo, p.environment_id, episode_root=c.runner.episode_root
    )
    objects = c.app.state.object_store
    empty = objects.put_json({}).object_sha256
    cases = {}
    for slot, kind, label in [
        ("N4", "positive", "POSITIVE_INCIDENT"),
        ("N5", "healthy", "NO_INCIDENT"),
        ("N6", "core", "CONFUSABLE_CORE_KNOWN"),
    ]:
        iid = fixture_episode(c, runner, candidate, slot, kind)
        cases[iid] = label
        key = batch.BATCH + ":collection:" + slot
        with c.evo.store.connect() as conn:
            row = json.loads(
                conn.execute(
                    "SELECT payload_json FROM knowledge_closure_runner_v050 WHERE entry_key=?",
                    (key,),
                ).fetchone()[0]
            )
            row["value"]["raw_index_sha256"] = objects.put_json(
                dict(
                    mode="OWNED_LOCAL",
                    entries=[dict(truncated=False, response_object_sha256=empty)],
                    operation_artifacts={
                        k: empty
                        for k in [
                            "result.json",
                            "control-intent.json",
                            "restore-intent.json",
                            "incident-request.json",
                            "incident-response.json",
                        ]
                    },
                )
            ).object_sha256
            row["value"]["scrape_receipt_sha256"] = objects.put_json(
                dict(scrape_recency_passed=True, incident_id=iid, object_sha256=empty)
            ).object_sha256
            row["value"]["ingestion_receipt_sha256"] = None
            row["sha256"] = batch.sha(row["value"])
            conn.execute(
                "UPDATE knowledge_closure_runner_v050 SET payload_json=? WHERE entry_key=?",
                (json.dumps(row), key),
            )
    seen = []

    def check(*args, **kw):
        assert kw["ingestion"] is None and kw["scrape"]["scrape_recency_passed"]
        seen.append(kw["incident_id"])

    # Only stub the v1 raw validator to isolate protocol-field selection.
    monkeypatch.setattr("scripts.product_v050.validation_capture.verify_raw", check)
    batch.verify_collection_objects(c.evo, candidate, cases)
    assert set(seen) == set(cases)
