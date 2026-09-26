"""Fixture-only governance chain; no network, Docker, or real model evidence."""

from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from scripts.product_v050.final_closure import ClosureRunner
from ecomsre.product.app import create_app
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.jobs.worker import run_one_job
from ecomsre.product.investigation.repository import InvestigationRepository
from ecomsre.product.knowledge.evolution_v050 import KnowledgeEvolutionV050
from ecomsre.product.knowledge.candidates_v050 import KnowledgeProposal
from ecomsre.product.connectors.fixture import FixtureConnectorV1
from ecomsre.product.investigation.reads import InvestigationReads
from ecomsre.product.incidents.read_backend import ProductReadBackendV1
from ecomsre.product.connectors.registry import ConnectorRegistryV1
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.telemetry.metrics import ProductMetricsV1
from ecomsre.product.knowledge.observations_v050 import (
    dependency_for,
    load_observations,
)
from test_investigation import decision


@pytest.fixture
def closure(tmp_path, monkeypatch, request):
    state = {"kind": "positive"}
    original_records = FixtureConnectorV1._records

    def records(self, **kw):
        rows = original_records(self, **kw)
        if not kw["current_observation"]:
            return rows
        result = []
        for row in rows:
            if kw["source"].value == "LOGS" and (
                row.service != "payment" or state["kind"] != "positive"
            ):
                row = row.model_copy(
                    update={
                        "severity": "DIAGNOSTIC",
                        "message": "healthy fixture observation",
                    }
                )
            if (
                kw["source"].value == "RUNTIME"
                and row.service == "payment"
                and state["kind"] == "core"
            ):
                row = row.model_copy(
                    update={"state": type(row.state).EXITED, "healthy": False}
                )
            if kw["source"].value == "RESOURCES" and state["kind"] != "positive":
                row = row.model_copy(
                    update={
                        "samples": tuple(
                            s.model_copy(update={"cpu_percent": 0.1})
                            for s in row.samples
                        )
                    }
                )
            result.append(row)
        return tuple(result)

    monkeypatch.setattr(FixtureConnectorV1, "_records", records)
    # Investigation sessions are fixture-only. Fixed collection below is separately labelled.
    monkeypatch.setattr(
        "ecomsre.product.investigation.runtime.configured_provider",
        lambda _: SimpleNamespace(complete=lambda **_: decision()),
    )
    settings = ProductSettingsV1(data_root=tmp_path, investigation={"enabled": True})
    dependency = dict(
        template="RESOURCE_USAGE_SAMPLES",
        window_offset_seconds=30,
        query_window_seconds=30,
        sampling_window_seconds=10,
        sample_count=5,
    )
    with TestClient(create_app(settings)) as client:
        app = client.app
        env = client.post(
            "/v1/environments",
            json=dict(
                name="closure-fixture",
                description="Fixture only",
                timezone="UTC",
                service_identity_policy={
                    "services": [
                        {"logical_service": s} for s in ("checkout", "payment")
                    ]
                },
                connector_configs=[
                    dict(
                        name="fixture",
                        kind="FIXTURE",
                        settings={"dataset": "capture-c2aa"},
                        credential_refs={},
                    )
                ],
                explicit_service_catalog=["checkout", "payment"],
            ),
        ).json()["environment_id"]
        for route, body in (
            ("verify-jobs", None),
            ("baseline-jobs", {"activate": True}),
        ):
            response = client.post(f"/v1/environments/{env}/{route}", json=body)
            assert response.status_code == 202, response.text
            assert run_one_job(settings, worker_id="setup")
        evo = KnowledgeEvolutionV050(
            app.state.knowledge,
            InvestigationRepository(app.state.store, app.state.object_store),
        )
        evo.enroll_fresh_test_environment(env)
        evo.freeze_split(
            env,
            {
                **{
                    f"e{i:02}": "DISCOVERY" if i <= 3 else "DEVELOPMENT"
                    for i in range(1, 6)
                },
                "e06": "HOLDOUT",
                "e07": "REUSE",
            },
        )

        def new(key, kind="positive", supplement=True, investigate=False):
            state["kind"] = kind
            response = client.post(
                "/v1/incidents",
                json=dict(
                    environment_id=env,
                    external_incident_key=key,
                    alert_name="observation",
                    summary="Fixture observation",
                    started_at=datetime.now(UTC).isoformat(),
                    candidate_service_ids=sorted(
                        s.service_id for s in app.state.services.get_map(env).services
                    ),
                ),
            )
            assert response.status_code == 201, response.text
            iid = response.json()["incident_id"]
            job = client.post(f"/v1/incidents/{iid}/diagnosis-jobs").json()
            assert run_one_job(settings, worker_id="diagnose")
            status = client.get("/v1/jobs/" + job["job_id"]).json()
            assert status["status"] == "SUCCEEDED", status
            incident = app.state.incidents.get(iid)
            if supplement:
                backend = ProductReadBackendV1(
                    connectors=ConnectorRegistryV1(
                        credential_resolver=CredentialResolverV1(), timeout_seconds=1
                    ),
                    changes=app.state.changes,
                    metrics=ProductMetricsV1(app.state.store),
                )
                reads = InvestigationReads(
                    incident=incident,
                    environment=app.state.environments.get(env),
                    identities=app.state.services.get_map(env),
                    capabilities=app.state.capabilities.get(env),
                    backend=backend,
                    objects=app.state.object_store,
                )
                key = next(
                    k
                    for k, (a, w) in reads.entries.items()
                    if a.target_services == ("payment",)
                    and dependency_for(incident, a, w) == dependency
                )
                reads.read(key)
            if investigate:
                client.post(f"/v1/incidents/{iid}/investigation-jobs")
                assert run_one_job(settings, worker_id="fixture-investigation")
            return iid

        original = {}
        for i in range(1, 6):
            key = f"e{i:02}"
            iid = new(key, supplement=i != 4, investigate=True)
            evo.bind_episode(iid, key)
            original[key] = iid
            path = tmp_path / "live-original" / "episodes" / key / "started.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"episode_id": key, "status": "STARTED"}))
        history_bindings = {}
        if getattr(request, "param", {}).get("history"):
            from ecomsre.product.knowledge.drafts_v050 import (
                scoped_view,
                scoped_model_view,
                SCOPED_TASK,
            )
            from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22 as sha

            discovery = evo.discovery_view(env, list(original.values()))
            for n in range(2):
                key = "knowledge-draft-v050.2:proposal:" + str(n)
                binding = scoped_view(
                    discovery,
                    request_key=key,
                    target="payment",
                    members=list(original.values()),
                )
                digest = app.state.object_store.put_json(binding).object_sha256
                history_bindings[key] = digest
                evo.investigations.reserve(key, {"fixture_history": n}, 1)
                evo.investigations.settle(
                    key,
                    dict(
                        proposal=dict(
                            disposition="NO_CANDIDATE",
                            candidate=None,
                            reason="Retained fixture abstention",
                            binding_id=binding["binding_id"],
                        ),
                        task_view_sha256=sha(
                            dict(task=SCOPED_TASK, view=scoped_model_view(binding))
                        ),
                        prompt_version="knowledge-draft-v050.2",
                        evidence_mode="FIXTURE_ONLY",
                    ),
                    1,
                    "COMPLETED",
                )
        runner = ClosureRunner(evo, env, episode_root=tmp_path)
        plan = dict(
            original_incidents=original,
            slots={f"N{i}": f"future-{i}" for i in range(1, 8)},
            campaign="live-closure-fixture",
            historical_request_bindings=history_bindings,
            primary_level=getattr(request, "param", {}).get("primary", "B"),
            target="payment",
            dependency=dependency,
            numeric_bounds={"cpu_percent": [0, 100], "memory_bytes": [0, 10000]},
            collection={"fixture_only": True, "fixed_dependency": dependency},
            time_range={
                "start": datetime.now(UTC).isoformat(),
                "end": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
        runner.initialize(plan)

        def episode(slot, kind="positive", investigate=False, supplement=True):
            runner.reserve_episode(slot)
            iid = new(slot, kind, investigate=investigate, supplement=supplement)
            runner.bind_episode(slot, iid)
            runner.finish_episode(slot, succeeded=True, reason="fixture complete")
            return iid

        yield SimpleNamespace(
            runner=runner,
            evo=evo,
            client=client,
            app=app,
            settings=settings,
            episode=episode,
            new=new,
            plan=plan,
            original=original,
            state=state,
        )


def admit(c, *, level="B", threshold=1.0, complete_negative=True, collected=False):
    if not collected:
        c.episode("N1", investigate=True)
        c.episode("N2", "healthy", supplement=complete_negative)
        c.episode("N3", "core")
    cohort = c.runner.freeze_cohort()
    assert c.original["e04"] not in cohort["complete_positives"]
    discovery = c.evo.discovery_view(c.runner.environment_id, cohort["positives"])
    resources = [
        o
        for s in discovery["sessions"]
        for o in s["observations"]
        if o.get("resource_dependency") == c.plan["dependency"]
    ]
    proposal = KnowledgeProposal(
        name="fixture-only-derived",
        kind="PATTERN_ONLY",
        target="payment",
        broad_domain="RESOURCE",
        member_incidents=cohort["complete_positives"],
        predicates=["core:RUNTIME_HEALTHY"],
        expression=dict(
            numerator=dict(field="cpu_percent", operator="mean"),
            comparator="gt",
            threshold=threshold,
            threshold_unit="PERCENT",
            threshold_provenance=discovery["snapshot_sha256"],
            window_seconds=10,
            minimum_samples=5,
        ),
        resource_dependency=c.plan["dependency"],
        supporting_refs=[o["evidence_ref"] for o in resources],
        counter_evidence_refs=[],
        confusable_patterns=["healthy fixture"],
        prediction="Fixture recurrence",
        inapplicable_conditions=["missing source"],
    )
    if level == "A":
        raw = proposal.model_dump(mode="json")
        raw.update(
            expression=None,
            resource_dependency=None,
            member_incidents=cohort["positives"],
            predicates=["core:RUNTIME_HEALTHY", "ga:LOG_UNKNOWN_ERROR_PATTERN"],
            supporting_refs=[
                o["evidence_ref"]
                for session in discovery["sessions"]
                for o in session["observations"]
                if o["source"] in {"RUNTIME", "LOGS"}
                and "payment" in o["covered_services"]
            ],
        )
        proposal = KnowledgeProposal.model_validate(raw)
    candidate = c.evo.add_candidate(
        environment_id=c.runner.environment_id,
        proposal=proposal,
        origin="FIXTURE_ONLY",
        source_request_key=None,
        discovery=discovery,
    )
    return candidate, discovery


def test_full_fixture_governance_to_normal_worker_and_revoke(closure, monkeypatch):
    c = closure
    candidate, discovery = admit(c)
    gate = c.runner.develop(candidate)
    assert gate["passed"], gate
    statuses = {
        r["incident_id"]: r["outcome"]["status"] for r in gate["report"]["outcomes"]
    }
    assert statuses[c.original["e04"]] == "UNKNOWN"
    assert gate["expression_checks"]["status"] == "PASS"
    feedback = c.runner.feedback(discovery)
    assert len(feedback["event_facts"]) == 8
    assert feedback["previous_candidates"][0]["development"]["state"] == "CHECKED"
    lock = c.runner.select(candidate)
    for slot, kind in (("N4", "positive"), ("N5", "healthy"), ("N6", "core")):
        c.episode(slot, kind)
    result = c.runner.evaluate_and_promote(candidate)
    assert result.gate_passed, result.model_dump()
    assert lock["registration_id"] == candidate.registration_id
    before = c.evo.investigations.accounting()
    monkeypatch.setattr(
        "ecomsre.product.investigation.runtime.configured_provider",
        lambda _: pytest.fail("recurrence must never invoke Provider"),
    )
    # No fixed supplemental read: normal worker must acquire the registered dependency itself.
    iid = c.episode("N7", supplement=False)
    diagnosis = c.client.get(f"/v1/incidents/{iid}/diagnosis").json()
    assert diagnosis["terminal"] == "EXTENSION_KNOWN", diagnosis
    assert diagnosis["mechanism"] == candidate.proposal.name
    evidence = c.client.get(f"/v1/incidents/{iid}/evidence").json()
    bindings = [
        b
        for o in evidence["objects"]
        for b in o["payload"].get("learned_match_bindings_v050", [])
    ]
    assert bindings and {b["registration_id"] for b in bindings} == {
        candidate.registration_id
    }
    assert {b["incident_id"] for b in bindings} == {iid}
    assert {b["candidate_sha256"] for b in bindings} == {candidate.compiled_sha256}
    assert diagnosis["provider_calls"] == 0
    assert c.evo.investigations.accounting() == before
    assert load_observations(c.app.state.incidents.get(iid), c.app.state.object_store)
    from ecomsre.product.incidents.extensions import ProductExtensionMatcherV1

    material = c.evo.knowledge._shadow_runtime_material(iid)

    def replay():
        matcher = ProductExtensionMatcherV1(
            c.app.state.knowledge.active_extensions(c.runner.environment_id),
            derived_registrations=c.app.state.knowledge.active_investigation_extensions(
                c.runner.environment_id
            ),
            capability_sha256=material.incident.source_capability_sha256,
            compatibility_store=c.app.state.store,
            environment_id=c.runner.environment_id,
            supplemental_reads=SimpleNamespace(
                incident=material.incident, objects=c.app.state.object_store, entries={}
            ),
        )
        return matcher.match(
            case_id=iid,
            candidate_services=material.runtime_input.candidate_services,
            topology_edges=material.runtime_input.adjacent_services,
            baseline=material.runtime_input.baseline,
            memory=material.runtime_input.memory,
            generic_anomalies=material.runtime_input.generic_anomalies,
            raw_outcomes=material.raw_outcomes,
            snapshots=tuple(
                o["payload"]
                for o in evidence["objects"]
                if "connector_result" in o["payload"]
            ),
        )

    assert {m.registration_id for m in replay()} == {candidate.registration_id}
    c.evo.revoke(candidate.registration_id)
    assert replay() == ()  # Explicit retained-input replay; no eighth episode.
    assert c.client.get(f"/v1/incidents/{iid}/diagnosis").json() == diagnosis
    assert c.evo.investigations.accounting() == before
    assert len(c.runner.episode_ledger()) == 12
    assert not c.app.state.knowledge.active_investigation_extensions(
        c.runner.environment_id
    )


def test_episode_accounting_preserves_failed_start_and_cannot_reallocate(closure):
    c = closure
    assert c.runner._get("plan")["baseline_episodes"] == 5
    with pytest.raises(ValueError, match="selection must precede"):
        c.runner.reserve_episode("N4")
    c.runner.reserve_episode("N1")
    c.runner.finish_episode("N1", succeeded=False, reason="fixture acquisition failed")
    with pytest.raises(ValueError, match="already consumed"):
        c.runner.reserve_episode("N1")
    restarted = ClosureRunner(
        c.evo, c.runner.environment_id, episode_root=c.runner.episode_root
    )
    assert restarted.initialize(c.plan) == c.runner._get("plan")
    assert len(restarted.episode_ledger()) == 6
    changed_root = ClosureRunner(
        c.evo,
        c.runner.environment_id,
        episode_root=c.runner.episode_root / "other-root",
    )
    with pytest.raises(ValueError, match="initialized"):
        changed_root.reserve_episode("N2")
    # Even a copied baseline cannot become an alternative live ledger.
    import shutil

    shutil.copytree(
        c.runner.episode_root / "live-original",
        changed_root.episode_root / "live-original",
    )
    with pytest.raises(ValueError, match="cannot be reset"):
        changed_root.initialize(c.plan)
    with pytest.raises(ValueError, match="already consumed"):
        restarted.reserve_episode("N1")
    changed = dict(c.plan, original_incidents={**c.original, "e02": c.original["e01"]})
    with pytest.raises(ValueError, match="five distinct"):
        restarted.initialize(changed)
    # Unbound failed starts also consume the original global episode allowance.
    for n in range(6):
        p = c.runner.episode_root / "live-other" / "episodes" / str(n) / "started.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(dict(episode_id=f"failed-{n}")))
    with pytest.raises(ValueError, match="balance exhausted"):
        restarted.reserve_episode("N2")


def test_qualified_candidate_blocks_proposer_even_if_selection_write_fails(
    closure, monkeypatch
):
    from ecomsre.product.errors import ProductError
    from ecomsre.product.investigation.closure_budget import PROPOSAL_PREFIX

    c = closure
    candidate, _ = admit(c)
    assert c.runner.develop(candidate)["passed"]
    monkeypatch.setattr(
        "scripts.product_v050.final_closure.selection_lock_v050.seal",
        lambda *a, **kw: (_ for _ in ()).throw(
            ValueError("fixture lock write failure")
        ),
    )
    with pytest.raises(ValueError, match="write failure"):
        c.runner.select(candidate)
    with pytest.raises(ValueError, match="pending selection"):
        c.runner.propose_next(None)
    with pytest.raises(ProductError, match="No proposal"):
        c.evo.investigations.reserve(PROPOSAL_PREFIX + "0", {}, 1)
    assert c.evo.investigations.accounting()["request_count"] == 0


def test_unmet_control_gate_blocks_before_provider_and_preserves_episode(closure):
    c = closure
    c.episode("N1", investigate=True)
    c.episode("N2", "healthy")
    iid = c.episode("N3", "positive")
    diagnosis = c.client.get(f"/v1/incidents/{iid}/diagnosis").json()
    assert diagnosis["terminal"] not in {"CORE_KNOWN", "EXTENSION_KNOWN"}
    before = c.evo.investigations.accounting()
    with pytest.raises(ValueError, match="DEVELOPMENT_KNOWN_CONTROL_NOT_ESTABLISHED"):
        c.runner.propose_next(None)
    assert c.evo.investigations.accounting() == before
    assert c.runner._get("attempt:0") is None
    assert c.runner.incident("N3") == iid
    assert c.client.get(f"/v1/incidents/{iid}/diagnosis").json() == diagnosis
    with pytest.raises(ValueError):
        c.runner.reserve_episode("N3")


def test_real_proposer_wire_contains_full_feedback_and_bound_forward_members(closure):
    from ecomsre.model.gateway import OpenAICompatibleConfig
    from ecomsre.product.investigation.contracts import PriceSchedule
    from ecomsre.product.investigation.provider import StructuredProvider

    c = closure
    # Admit an intentionally failing fixture historical candidate. No handwritten
    # winning rule is supplied to the proposer under test.
    candidate, discovery = admit(c)
    c.evo.check_development(
        candidate.registration_id, [c.original["e04"], c.original["e05"]]
    )
    sent = []

    def post(**kw):
        payload = kw["payload"]
        view = json.loads(payload["input"][0]["content"])["view"]
        sent.append(view)
        feedback = view["feedback"]
        assert len(feedback["event_facts"]) == 8
        assert (
            sum(r["role"] == "DEVELOPMENT_CONTROL" for r in feedback["event_facts"])
            == 2
        )
        assert {
            r["outcome"]["status"]
            for r in feedback["previous_candidates"][0]["development"]["outcomes"]
        } == {"TRUE", "UNKNOWN"}
        assert feedback["minimum_distinct_sources"] == 2
        assert "RUNTIME" in feedback["source_categories"].values()
        assert "RESOURCES" in feedback["source_categories"].values()
        assert len(feedback["previous_candidates"][0]["recomputed_all_seen"]) == 8
        assert (
            len(view["scope"]["members"]) == 5
        )  # e04 still in feedback, never backfilled.
        assert set(view["scope"]["members"]) <= {
            r["event"] for r in feedback["event_facts"]
        }
        raw = dict(
            binding_id=view["binding_id"],
            disposition="NO_CANDIDATE",
            candidate=None,
            reason="Fixture abstention; no model learning claim.",
        )
        return dict(
            model="gpt-5.4-mini-2026-03-17",
            id="fixture",
            status="completed",
            output=[
                dict(
                    type="function_call",
                    status="completed",
                    name="submit_proposal",
                    arguments=json.dumps(raw),
                )
            ],
            usage=dict(input_tokens=100, output_tokens=20),
        )

    model = "gpt-5.4-mini-2026-03-17"
    provider = StructuredProvider(
        OpenAICompatibleConfig("https://api.openai.com/v1", "fixture-only", model),
        PriceSchedule(
            provider_profile="fixture",
            model=model,
            as_of="2026-09-17",
            source="fixture",
            input_usd_per_million=0.75,
            output_usd_per_million=4.5,
        ),
        c.evo.investigations,
        SimpleNamespace(post_json=post),
        api_style="responses",
    )
    outcome = c.runner.propose_next(provider)
    assert outcome["status"] == "NO_CANDIDATE", outcome
    assert len(sent) == 1
    assert c.evo.investigations.accounting()["request_count"] == 1
    with pytest.raises(ValueError, match="stopped"):
        c.runner.propose_next(provider)


@pytest.mark.parametrize("closure", [{"history": True}], indirect=True)
def test_all_historical_nonadmitted_drafts_are_required_and_request_verified(
    closure, monkeypatch
):
    c = closure
    _, discovery = admit(c)
    feedback = c.runner.feedback(discovery)
    assert len(feedback["previous_drafts"]) == 2
    assert all(
        d["output_available"] and d["draft"]["disposition"] == "NO_CANDIDATE"
        for d in feedback["previous_drafts"]
    )
    original_get = c.runner._get
    retained = original_get("plan")
    changed = dict(
        retained, plan=dict(retained["plan"], historical_request_bindings={})
    )
    monkeypatch.setattr(
        c.runner, "_get", lambda key: changed if key == "plan" else original_get(key)
    )
    with pytest.raises(
        ValueError, match="complete historical draft feedback binding missing"
    ):
        c.runner.feedback(discovery)
    assert c.evo.investigations.accounting()["request_count"] == 2


@pytest.mark.parametrize("closure", [{"primary": "A"}], indirect=True)
def test_level_a_gate_keeps_original_five_denominator(closure):
    candidate, _ = admit(closure, level="A")
    gate = closure.runner.develop(candidate)
    assert gate["passed"] and gate["level"] == "A", gate
    assert gate["expression_checks"]["status"] == "NOT_APPLICABLE_LEVEL_A"
    assert len(gate["report"]["outcomes"]) == 8
    assert (
        closure.runner.select(candidate)["registration_id"] == candidate.registration_id
    )


@pytest.mark.parametrize("complete_negative,threshold", [(False, 1.0), (True, -1.0)])
def test_incomplete_negative_or_constant_expression_never_passes(
    closure, complete_negative, threshold
):
    candidate, _ = admit(
        closure, complete_negative=complete_negative, threshold=threshold
    )
    gate = closure.runner.develop(candidate)
    assert not gate["passed"]
    assert not gate["controls_ok"]
    if threshold < 0:
        assert gate["expression_checks"]["status"] == "FAIL"
    with pytest.raises(ValueError, match="not passed"):
        closure.runner.select(candidate)


def test_failed_n1_preproposal_feasibility_preserves_plan_and_slots(closure):
    c = closure
    runner = c.runner
    original_plan = runner._get("plan")
    runner.reserve_episode("N1")
    runner.finish_episode("N1", succeeded=False, reason="FAILED")
    proof = dict(
        n1_result_sha256="a" * 64,
        cleanup_sha256="b" * 64,
        resource_window="NOT_COLLECTED_IRRECOVERABLE",
        incident_created=False,
        failure="INCIDENT_CREATE:422",
    )
    runner.declare_level_a_feasibility(proof)
    assert runner.primary_level == "A"
    assert runner.plan["primary_level"] == "B"
    assert runner._get("plan") == original_plan
    assert runner._get("terminal:N1") == dict(succeeded=False, reason="FAILED")
    with pytest.raises(ValueError, match="consumed"):
        runner.reserve_episode("N1")
    c.episode("N2", "healthy")
    c.episode("N3", "core")
    cohort = runner.freeze_cohort()
    assert cohort["n1"] is None
    assert len(cohort["all_ids"]) == 7
    candidate, _ = admit(c, level="A", collected=True)
    assert runner.develop(candidate)["passed"]
    runner.select(candidate)
    assert (
        runner._get("selection-pending")["registration_id"] == candidate.registration_id
    )
    for slot, kind in (("N4", "positive"), ("N5", "healthy"), ("N6", "core")):
        c.episode(slot, kind)
    assert runner.evaluate_and_promote(candidate).gate_passed
    with pytest.raises(ValueError, match="pending selection"):
        runner.propose_next(None)
    with pytest.raises(ValueError, match="precede cohort"):
        runner.declare_level_a_feasibility(proof)


def test_feasibility_revision_cannot_follow_new_provider_work(closure):
    c = closure
    c.runner.reserve_episode("N1")
    c.runner.finish_episode("N1", succeeded=False, reason="FAILED")
    c.evo.investigations.reserve("fixture-new", {"fixture": True}, 1)
    with pytest.raises(ValueError, match="precede new Provider"):
        c.runner.declare_level_a_feasibility({})
