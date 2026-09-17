"""Review counterexamples; all observations and Providers here are fixtures."""

from types import SimpleNamespace

import pytest

from ecomsre.product.investigation.contracts import (
    InvestigationConfig,
    HypothesisProposal,
)
from ecomsre.product.investigation.runtime import run_investigation
from test_expressions import observation
from test_investigation import decision

WINDOW = {"started_at": "2026-09-17T10:00:00Z", "ended_at": "2026-09-17T10:00:10Z"}


def hypothesis(*, support, threshold=80.0, **overrides):
    return HypothesisProposal.model_validate(
        dict(
            hypothesis_id=None,
            mechanism="Observable resource pattern, causality unresolved",
            target="payment",
            support=support,
            against=[],
            missing_observations=[],
            falsifiable_prediction="CPU maximum exceeds threshold in this window",
            claim_window=WINDOW,
            prediction_test=dict(
                field="cpu_percent",
                operator="max",
                comparator="gt",
                threshold=threshold,
                window_seconds=10,
                minimum_samples=3,
            ),
        )
        | overrides
    )


def run_conclusion(hypotheses, observations):
    class Repository:
        def get(self, key):
            return None

        def save(self, session, **kwargs):
            pass

    reads = SimpleNamespace(
        initial_observations=observations, catalog=lambda: [{"action_id": "unused"}]
    )
    return run_investigation(
        incident=SimpleNamespace(
            incident_id="incident",
            incident_sha256="i",
            environment_id="env",
            candidate_logical_services=["payment"],
        ),
        diagnosis=SimpleNamespace(
            diagnosis_id="diagnosis",
            result_sha256="d",
            terminal=SimpleNamespace(value="OPEN_WORLD"),
            capability_limitations=[],
        ),
        knowledge_snapshot="snapshot",
        reads=reads,
        repository=Repository(),
        config=InvestigationConfig(enabled=True),
        fence=None,
        renew_lease=lambda: None,
        provider=SimpleNamespace(
            complete=lambda **kwargs: decision(
                "CONCLUDE", hypotheses=hypotheses, result="PROVISIONAL_SUPPORTED"
            )
        ),
    )


def resource(**changes):
    return observation() | {"window": WINDOW, "targets": ["payment"]} | changes


def test_r1_cross_hypothesis_support_cannot_be_spliced():
    result = run_conclusion(
        [
            hypothesis(support=["r1"], threshold=100.0),
            hypothesis(support=[], threshold=80.0),
        ],
        [resource()],
    )
    assert result["status"] == "UNRESOLVED"
    assert result.get("supported_hypothesis_ids", []) == []


def test_r1_same_hypothesis_support_is_explicit_and_noncausal():
    result = run_conclusion([hypothesis(support=["r1"])], [resource()])
    assert result["status"] == "PROVISIONAL_SUPPORTED"
    assert result["supported_hypothesis_ids"] == [
        result["hypotheses"][0]["hypothesis_id"]
    ]
    assert result["checked_predictions"][0]["claim_kind"] == "OBSERVED_NUMERIC_TEST"
    assert result["unresolved_alternatives"]


@pytest.mark.parametrize(
    "change",
    [
        {"threshold": 100.0},
        {"target": "ad"},
        {
            "claim_window": {
                "started_at": "2026-09-17T10:01:00Z",
                "ended_at": "2026-09-17T10:01:10Z",
            }
        },
        {"prediction_test": None},
    ],
)
def test_r1_false_unknown_or_wrong_scope_cannot_support(change):
    result = run_conclusion([hypothesis(support=["r1"], **change)], [resource()])
    assert result["status"] == "UNRESOLVED"


def test_r2_supplemental_resource_dependency_reaches_development_and_normal_reuse(
    tmp_path, monkeypatch
):
    from dataclasses import replace
    from datetime import UTC, datetime
    from fastapi.testclient import TestClient
    from ecomsre.product.app import create_app
    from ecomsre.product.settings import ProductSettingsV1
    from ecomsre.product.jobs.worker import run_one_job
    from ecomsre.product.incidents.read_backend import ProductReadBackendV1
    from ecomsre.product.investigation.repository import InvestigationRepository
    from ecomsre.product.knowledge.evolution_v050 import KnowledgeEvolutionV050
    from ecomsre.product.knowledge.candidates_v050 import KnowledgeProposal
    from test_investigation import prepare

    original_acquire = ProductReadBackendV1.acquire

    def incomplete_initial(self, **kwargs):
        result = original_acquire(self, **kwargs)
        return replace(
            result,
            raw_outcomes=tuple(
                o for o in result.raw_outcomes if o.source.value != "RESOURCES"
            ),
            memory_outcomes=tuple(
                o for o in result.memory_outcomes if o.source.value != "RESOURCES"
            ),
            snapshots=tuple(
                s
                for s in result.snapshots
                if s["connector_result"]["source"] != "RESOURCES"
            ),
        )

    monkeypatch.setattr(ProductReadBackendV1, "acquire", incomplete_initial)

    class Supplement:
        calls = 0

        def complete(self, *, view, **kwargs):
            self.calls += 1
            if self.calls == 1:
                chosen = max(
                    (r for r in view["legal_reads"] if r["source"] == "RESOURCES"),
                    key=lambda r: r["window"]["ended_at"],
                )
                return decision("READ", action_id=chosen["action_id"], result=None)
            return decision()

    monkeypatch.setattr(
        "ecomsre.product.investigation.runtime.configured_provider",
        lambda _: Supplement(),
    )
    settings = ProductSettingsV1(data_root=tmp_path, investigation={"enabled": True})
    with TestClient(create_app(settings)) as client:
        first = prepare(client, settings)
        app = client.app
        incident = app.state.incidents.get(first)

        def new(key):
            data = incident.model_dump(
                mode="json",
                include={
                    "environment_id",
                    "alert_name",
                    "summary",
                    "candidate_service_ids",
                },
            )
            data.update(
                external_incident_key=key, started_at=datetime.now(UTC).isoformat()
            )
            iid = client.post("/v1/incidents", json=data).json()["incident_id"]
            client.post(f"/v1/incidents/{iid}/diagnosis-jobs")
            assert run_one_job(settings, worker_id=key)
            return iid

        second = new("discovery-2")
        for iid in (first, second):
            client.post(f"/v1/incidents/{iid}/investigation-jobs")
            assert run_one_job(settings, worker_id="investigate")
        evo = KnowledgeEvolutionV050(
            app.state.knowledge,
            InvestigationRepository(app.state.store, app.state.object_store),
        )
        view = evo.discovery_view(incident.environment_id, [first, second])
        resources = [
            o
            for s in view["sessions"]
            for o in s["observations"]
            if o["source"] == "RESOURCES"
        ]
        assert resources and all(
            o["evidence_ref"].startswith("investigation:") for o in resources
        )
        # The dependency is runtime-authored from the selected query, not a free URL.
        dependency = resources[0]["resource_dependency"]
        sample = resources[0]["records"][0]
        proposal = KnowledgeProposal(
            name="supplemental-fixture",
            kind="PATTERN_ONLY",
            target="payment",
            broad_domain="RESOURCE",
            member_incidents=[first, second],
            predicates=["core:RUNTIME_HEALTHY"],
            expression=dict(
                numerator=dict(field="cpu_percent", operator="mean"),
                comparator="gt",
                threshold=1.0,
                threshold_unit="PERCENT",
                threshold_provenance=view["snapshot_sha256"],
                window_seconds=sample["sampling_window_seconds"],
                minimum_samples=2,
            ),
            resource_dependency=dependency,
            supporting_refs=[o["evidence_ref"] for o in resources],
            counter_evidence_refs=[],
            confusable_patterns=["constant load"],
            prediction="observable pattern recurs",
            inapplicable_conditions=["missing samples"],
        )
        candidate = evo.add_candidate(
            environment_id=incident.environment_id,
            proposal=proposal,
            origin="FIXTURE_ONLY",
            source_request_key=None,
            discovery=view,
        )
        feedback = evo.check_development(candidate.registration_id, [first, second])
        assert {r["outcome"]["status"] for r in feedback["outcomes"]} == {"TRUE"}
        # Isolate normal matcher plumbing; this insertion is NOT a promotion claim.
        with app.state.store.connect() as c:
            c.execute(
                "INSERT INTO environment_extension_registrations VALUES (?,?,?,'ACTIVE',?,?)",
                (
                    candidate.registration_id,
                    incident.environment_id,
                    candidate.model_dump_json(),
                    datetime.now(UTC).isoformat(),
                    datetime.now(UTC).isoformat(),
                ),
            )
        recurrence = new("recurrence")
        result = client.get(f"/v1/incidents/{recurrence}/diagnosis").json()
        assert result["terminal"] == "EXTENSION_KNOWN", result
        assert result["provider_calls"] == 0
        assert any(
            r.startswith("investigation:") for r in result["supporting_evidence_refs"]
        )
        import json
        from ecomsre.product.knowledge.observations_v050 import load_observations, save_observation
        from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
        from ecomsre.product.connectors.base import ConnectorQueryResultV1, ConnectorWindowV1
        with app.state.store.connect() as c:
            original_digest = c.execute("SELECT object_sha256 FROM supplemental_observations_v050 WHERE incident_id=?", (first,)).fetchone()[0]
        envelope = json.loads(app.state.object_store.read_bytes(original_digest))
        action = EvidenceActionV22.model_validate_json(json.dumps(envelope["action"]))
        observed = ConnectorQueryResultV1.model_validate_json(json.dumps(envelope["result"]))
        window = ConnectorWindowV1.model_validate_json(json.dumps(envelope["window"]))
        arguments = dict(incident=incident, action=action, window=window, result=observed,
                         objects=app.state.object_store, capability_sha256=incident.source_capability_sha256)
        with pytest.raises(ValueError, match="QUERY_RESULT_MISMATCH"):
            save_observation(**(arguments | {"result": observed.model_copy(update={"source": type(observed.source)("LOGS")})}))
        wrong_parent = client.get(f"/v1/incidents/{second}/diagnosis").json()["diagnosis_id"]
        with pytest.raises(ValueError, match="PARENT_BINDING"):
            save_observation(**arguments, parent_diagnosis_id=wrong_parent)
        forged = dict(envelope, parent_diagnosis_id=wrong_parent)
        forged_digest = app.state.object_store.put_json(forged).object_sha256
        with app.state.store.connect() as c:
            c.execute("UPDATE supplemental_observations_v050 SET object_sha256=? WHERE incident_id=?", (forged_digest, first))
        with pytest.raises(ValueError, match="PARENT_BINDING"):
            load_observations(incident, app.state.object_store)
        with app.state.store.connect() as c:
            c.execute("UPDATE supplemental_observations_v050 SET object_sha256=? WHERE incident_id=?", (original_digest, first))
        assert load_observations(incident, app.state.object_store)
        with app.state.store.connect() as c:
            digest = c.execute("SELECT object_sha256 FROM supplemental_observations_v050 WHERE incident_id=?", (first,)).fetchone()[0]
            c.execute("UPDATE supplemental_observations_v050 SET object_sha256=? WHERE incident_id=?", (digest, recurrence))
        with pytest.raises(ValueError, match="EVENT_BINDING"):
            load_observations(app.state.incidents.get(recurrence), app.state.object_store)



def test_r3_numeric_counterexample_is_scoped_negative_evidence():
    h = hypothesis(support=[], against=["r1"], threshold=100.0)
    result = run_conclusion([h], [resource()])
    # The model cannot claim support, but a checked false prediction is retained.
    assert result["status"] == "UNRESOLVED"
    from ecomsre.product.investigation.runtime import check_predictions

    checked = check_predictions(
        [h.model_dump(mode="json") | {"hypothesis_id": "h1"}], [resource()]
    )
    assert checked[0]["status"] == "FALSE"
    assert checked[0]["evidence_refs"] == ["r1"]


@pytest.mark.parametrize(
    "mutation",
    [
        dict(status="SUCCESS_EMPTY", records=[]),
        dict(covered_services=[]),
        dict(status="FAILURE_TIMEOUT"),
        dict(truncated=True),
        dict(window={}),
    ],
)
def test_r3_empty_failed_or_wrong_scope_is_never_a_counterexample(mutation):
    from ecomsre.product.investigation.runtime import check_predictions

    h = hypothesis(support=[], against=["r1"]).model_dump(mode="json") | {
        "hypothesis_id": "h1"
    }
    assert check_predictions([h], [resource(**mutation)])[0]["status"] == "UNKNOWN"


def test_r4_cross_candidate_seen_incident_cannot_become_holdout(tmp_path, monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from ecomsre.product.app import create_app
    from ecomsre.product.settings import ProductSettingsV1
    from ecomsre.product.investigation.repository import InvestigationRepository
    from ecomsre.product.knowledge.evolution_v050 import KnowledgeEvolutionV050
    from ecomsre.product.knowledge.candidates_v050 import (
        KnowledgeProposal,
        CompiledKnowledge,
    )
    from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
    from test_investigation import prepare

    settings = ProductSettingsV1(data_root=tmp_path)
    with TestClient(create_app(settings)) as client:
        seen = prepare(client, settings)
        app = client.app
        incident = app.state.incidents.get(seen)
        evo = KnowledgeEvolutionV050(
            app.state.knowledge,
            InvestigationRepository(app.state.store, app.state.object_store),
        )
        for rid, ids in [("a", ["one", seen]), ("b", ["two", "three"])]:
            proposal = KnowledgeProposal(
                name="candidate-" + rid,
                kind="PATTERN_ONLY",
                target="payment",
                broad_domain="RESOURCE",
                member_incidents=ids,
                predicates=["core:RUNTIME_HEALTHY", "ga:RESOURCE_CPU_OUTLIER"],
                expression=None,
                supporting_refs=["fixture"],
                counter_evidence_refs=[],
                confusable_patterns=["other"],
                prediction="recurs",
                inapplicable_conditions=["missing"],
            )
            payload = dict(
                schema_version="ecomsre.product.compiled-knowledge.v050",
                registration_id=rid,
                environment_id=incident.environment_id,
                capability_sha256=incident.source_capability_sha256,
                origin="FIXTURE_ONLY",
                source_request_key=None,
                discovery_snapshot_sha256="0" * 64,
                discovery_incident_ids=ids,
                proposal=proposal.model_dump(mode="json"),
                action_authority="NONE",
            )
            candidate = CompiledKnowledge.model_validate(
                payload | {"compiled_sha256": semantic_sha256_v22(payload)}
            )
            with app.state.store.connect() as c:
                c.execute(
                    "INSERT INTO knowledge_candidate_pool_v050 VALUES (?,?,?,?,'DRAFT',NULL,NULL)",
                    (
                        rid,
                        incident.environment_id,
                        candidate.compiled_sha256,
                        candidate.model_dump_json(),
                    ),
                )
                c.execute(
                    "INSERT INTO knowledge_development_v050 VALUES (?,?)",
                    (rid, json.dumps({"state": "CHECKED", "incident_ids": []})),
                )
        with pytest.raises(ValueError, match="exposed"):
            evo.freeze("b", {seen: "POSITIVE_INCIDENT"})

        # A competing freeze writer must be excluded before the first check and
        # remain excluded until the development reservation is durable.
        import sqlite3
        from contextlib import contextmanager
        original_connect = app.state.store.connect
        with original_connect() as c:
            c.execute("DELETE FROM knowledge_development_v050 WHERE registration_id='a'")
        competing_writes = []

        class RacingConnection:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, sql, *args):
                if sql.startswith("SELECT freeze_json"):
                    with original_connect() as competing:
                        competing.execute("PRAGMA busy_timeout=0")
                        try:
                            competing.execute("BEGIN IMMEDIATE")
                        except sqlite3.OperationalError as exc:
                            assert "locked" in str(exc)
                            competing_writes.append("BLOCKED")
                        else:
                            competing.execute("UPDATE knowledge_candidate_pool_v050 SET freeze_json=? WHERE registration_id='b'", (json.dumps({"cases": [seen]}),))
                            competing.execute("COMMIT")
                            competing_writes.append("FROZEN")
                return self.connection.execute(sql, *args)

        @contextmanager
        def racing_connect():
            with original_connect() as c:
                yield RacingConnection(c)

        def interrupted(_):
            raise RuntimeError("evaluation interrupted after reservation")

        monkeypatch.setattr(app.state.store, "connect", racing_connect)
        monkeypatch.setattr(app.state.knowledge, "_shadow_runtime_material", interrupted)
        with pytest.raises(RuntimeError, match="evaluation interrupted"):
            evo.check_development("a", [seen])
        assert competing_writes == ["BLOCKED"]
        with original_connect() as c:
            saved = c.execute("SELECT payload_json FROM knowledge_development_v050 WHERE registration_id='a'").fetchone()
            assert json.loads(saved[0])["incident_ids"] == [seen]


def test_r4_episode_alias_failed_dispatch_and_restart_retain_exposure(
    tmp_path, monkeypatch
):
    from datetime import UTC, datetime
    from fastapi.testclient import TestClient
    from ecomsre.product.app import create_app
    from ecomsre.product.settings import ProductSettingsV1
    from ecomsre.product.investigation.repository import InvestigationRepository
    from ecomsre.product.knowledge.evolution_v050 import KnowledgeEvolutionV050
    from ecomsre.product.knowledge import split_v050
    from test_investigation import prepare

    settings = ProductSettingsV1(data_root=tmp_path)
    with TestClient(create_app(settings)) as client:
        first = prepare(client, settings)
        app = client.app
        incident = app.state.incidents.get(first)
        data = incident.model_dump(
            mode="json",
            include={
                "environment_id",
                "alert_name",
                "summary",
                "candidate_service_ids",
            },
        )
        data.update(
            external_incident_key="alias-window",
            started_at=datetime.now(UTC).isoformat(),
        )
        alias = client.post("/v1/incidents", json=data).json()["incident_id"]
        evo = KnowledgeEvolutionV050(
            app.state.knowledge,
            InvestigationRepository(app.state.store, app.state.object_store),
        )
        evo.freeze_split(
            incident.environment_id, {"episode-a": "DISCOVERY", "episode-b": "HOLDOUT"}
        )
        evo.bind_episode(first, "episode-a")
        evo.bind_episode(alias, "episode-a")
        monkeypatch.setattr(
            evo,
            "discovery_view",
            lambda *args: {"fixture": "failed-dispatch-exposure-test"},
        )

        def failure(**kwargs):
            raise ValueError("fixture Provider format failure after dispatch")

        with pytest.raises(ValueError, match="format failure"):
            evo.propose(
                environment_id=incident.environment_id,
                incident_ids=[first, alias],
                provider=SimpleNamespace(complete=failure),
                key="failed",
            )
        restarted = KnowledgeEvolutionV050(
            app.state.knowledge,
            InvestigationRepository(app.state.store, app.state.object_store),
        )
        with app.state.store.connect() as c:
            assert {first, alias} <= split_v050.exposed_incidents(c)
            with pytest.raises(ValueError, match="incompatible"):
                split_v050.require_roles(c, [alias], {"HOLDOUT"})
        with app.state.store.connect() as c:
            with pytest.raises(ValueError, match="denominator"):
                split_v050.require_independent(c, [first, alias])
        with pytest.raises(ValueError, match="immutable"):
            restarted.bind_episode(alias, "episode-b")
        with pytest.raises(ValueError, match="immutable"):
            restarted.freeze_split(incident.environment_id, {"episode-a": "HOLDOUT"})


def test_project_env_parser_never_executes_or_overrides(tmp_path, monkeypatch):
    from scripts.product_v050.project_environment import load_project_environment, KEYS

    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / "project.env"
    env.write_text("ECOMSRE_LLM_MODEL=$(touch forbidden)\n")
    with pytest.raises(ValueError, match="EXPANSION_FORBIDDEN"):
        load_project_environment(env)
    env.write_text("ECOMSRE_LLM_MODEL='explicit-model'\n")
    assert load_project_environment(env)["ECOMSRE_LLM_MODEL"]
    env.write_text("ECOMSRE_LLM_MODEL=other\n")
    with pytest.raises(ValueError, match="PROCESS_CONFLICT"):
        load_project_environment(env)


def test_r2_cache_selection_includes_target():
    from datetime import datetime
    from ecomsre.product.knowledge.observations_v050 import (
        ResourceDependency,
        select_dependency,
    )

    dep = ResourceDependency(
        window_offset_seconds=0, sampling_window_seconds=10, sample_count=3
    )
    window = {"started_at": "2026-09-17T10:00:00Z", "ended_at": "2026-09-17T10:00:30Z"}
    cached = resource(
        targets=["ad"],
        covered_services=["ad"],
        window=window,
        resource_dependency=dep.model_dump(mode="json"),
    )
    assert (
        select_dependency(
            dep,
            incident_end=datetime.fromisoformat(window["ended_at"]),
            observations=[cached],
            target="payment",
        )
        == []
    )


def test_r2_missing_required_supplement_is_unknown():
    from test_expressions import expression
    from ecomsre.product.knowledge.expressions import evaluate_expression

    assert (
        evaluate_expression(expression(), target="payment", observations=[]).status
        == "UNKNOWN"
    )


def test_r2_expired_worker_cannot_link_observation(tmp_path):
    from ecomsre.product.knowledge.observations_v050 import (
        save_observation,
        ensure_table,
    )
    from ecomsre.product.jobs.contracts import JobLeaseFenceV1
    from ecomsre.product.errors import ProductError
    from test_investigation import repository

    repo = repository(tmp_path)
    ensure_table(repo.store)
    window = SimpleNamespace(model_dump=lambda **kw: WINDOW)
    result = SimpleNamespace(
        source=SimpleNamespace(value="METRICS"), window=window, requested_services=("payment",), model_dump=lambda **kw: {}
    )
    action = SimpleNamespace(
        source=SimpleNamespace(value="METRICS"),
        target_services=("payment",),
        request_sha256="1" * 64,
        model_dump=lambda **kw: {},
    )
    incident = SimpleNamespace(
        incident_id="inc-" + "1" * 24,
        incident_sha256="2" * 64,
        environment_id="env-" + "1" * 24,
    )
    fence = JobLeaseFenceV1(
        job_id="job-" + "1" * 24, claimed_by="expired", attempt_count=1, checked_at=1
    )
    with pytest.raises(ProductError) as caught:
        save_observation(
            incident=incident,
            action=action,
            window=window,
            result=result,
            objects=repo.objects,
            capability_sha256="3" * 64,
            fence=fence,
        )
    assert caught.value.code == "JOB_LEASE_LOST"
    with repo.store.connect() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM supplemental_observations_v050").fetchone()[
                0
            ]
            == 0
        )
