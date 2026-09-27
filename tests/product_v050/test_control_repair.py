"""Narrow amendment invariants; fixtures are never evidence of live success."""

from datetime import UTC, datetime
import json
from types import SimpleNamespace

import pytest

from test_final_closure import closure as closure, admit
from ecomsre.product.knowledge import control_repair_v050 as repair, split_v050
from ecomsre.product.connectors.fixture import FixtureConnectorV1


def prepare(c, monkeypatch):
    c.runner.reserve_episode("N1")
    c.runner.finish_episode("N1", succeeded=False, reason="FAILED")
    c.runner.declare_level_a_feasibility(
        dict(
            n1_result_sha256="a" * 64,
            cleanup_sha256="b" * 64,
            resource_window="NOT_COLLECTED_IRRECOVERABLE",
            incident_created=False,
            failure="INCIDENT_CREATE:422",
        )
    )
    c.episode("N2", "healthy")
    previous = FixtureConnectorV1._records

    def records(self, **kw):
        rows = previous(self, **kw)
        if kw["current_observation"] and kw["source"].value == "RUNTIME":
            return ()
        return rows

    with monkeypatch.context() as m:
        m.setattr(FixtureConnectorV1, "_records", records)
        iid = c.episode("N3", "healthy")
    assert c.evo.knowledge._diagnosis(iid).terminal.value == "INSUFFICIENT_EVIDENCE"
    return repair.inputs()


def test_amended_chain_preserves_failed_parent_and_uses_new_control(
    closure, monkeypatch, tmp_path
):
    c = closure
    args = prepare(c, monkeypatch)
    parent = c.runner.freeze_cohort()
    ledger = c.runner.episode_ledger()
    contract = repair.install(c.runner, **args)
    assert repair.install(c.runner, **args) == contract
    assert c.runner.episode_ledger() == ledger
    assert c.runner._get("cohort") == parent
    assert contract["cumulative_live_limit"] == 13 and contract["round_live_limit"] == 8
    with c.app.state.store.connect() as connection:
        manifest = split_v050.effective_manifest(connection, c.runner.environment_id)
    assert manifest[contract["episode_id"]] == "DEVELOPMENT"
    with pytest.raises(ValueError, match="immutable"):
        c.evo.bind_episode(c.runner.incident("N3"), contract["episode_id"])
    c.episode(repair.SLOT, "core")
    cohort = c.runner.freeze_cohort()
    assert cohort["controls"] == [
        c.runner.incident("N2"),
        c.runner.incident(repair.SLOT),
    ]
    assert cohort["insufficient_controls"] == [c.runner.incident("N3")]
    assert len(cohort["all_ids"]) == 8 and c.runner._get("cohort") == parent
    candidate, discovery = admit(c, level="A", collected=True)
    feedback = c.runner.feedback(discovery)
    assert (
        sum(r["role"] == "INSUFFICIENT_CONTROL" for r in feedback["event_facts"]) == 1
    )
    gate = c.runner.develop(candidate)
    assert gate["passed"] and gate["insufficient_control_safe"]
    lock = c.runner.select(candidate)
    assert lock["plan"]["collection"]["control_repair"] == contract
    assert lock["plan"]["time_range"] == contract["time_range"]
    assert c.runner.plan == c.plan
    for slot, kind in (("N4", "positive"), ("N5", "healthy"), ("N6", "core")):
        c.episode(slot, kind)
    assert c.runner.evaluate_and_promote(candidate).gate_passed
    from scripts.product_v050 import final_closure_live as live

    monkeypatch.setattr(live, "ROOT", tmp_path)
    monkeypatch.setattr(
        live,
        "collect",
        lambda campaign, runner, slot: c.episode(slot, supplement=False),
    )
    live.recurrence(
        SimpleNamespace(
            app=c.app,
            env=c.runner.environment_id,
            evo=c.evo,
            repo=c.evo.investigations,
            client=c.client,
        ),
        c.runner,
    )
    proof = json.loads((tmp_path / "recurrence-proof.json").read_text())
    assert (
        proof["passed"]
        and proof["revocation_check"]
        and proof["before"] == proof["after"]
    )
    assert len(c.runner.episode_ledger()) == 13
    with pytest.raises(ValueError, match="consumed"):
        c.runner.reserve_episode(repair.SLOT)
    with pytest.raises(ValueError, match="identity differs"):
        repair.install(c.runner, **(args | {"collection_sha256": "d" * 64}))


def test_change_audit_projects_actual_completed_fact_and_restore(tmp_path, monkeypatch):
    from scripts.product_v050 import change_audit
    from ecomsre_live_sandbox.contracts import canonical_sha256

    state = {"name": "BASELINE"}
    documents = {
        "BASELINE": {"flags": {"private": 0}},
        "PAYMENT": {"flags": {"private": 1}},
    }
    path = tmp_path / "private.json"
    path.write_text(json.dumps(documents[state["name"]]))

    def read(name):
        assert name == state["name"]
        return dict(
            state=name,
            document_sha256=canonical_sha256(documents[name]),
            observed_at=datetime.now(UTC).isoformat(),
            evaluations={"private": documents[name]["flags"]["private"]},
        )

    def apply(name):
        state["name"] = name
        path.write_text(json.dumps(documents[name]))
        return read(name)

    sent = []

    def post(url, json):
        sent.append(json)
        return SimpleNamespace(status_code=201, json=lambda: {"accepted": True})

    monkeypatch.setattr(
        change_audit, "_local_json", lambda url: documents[state["name"]]
    )
    campaign = SimpleNamespace(
        controller=SimpleNamespace(
            read=read,
            apply=apply,
            flag_file=path,
            endpoints=SimpleNamespace(flag_control="http://fixture"),
        ),
        client=SimpleNamespace(post=post),
        env="env-" + "1" * 24,
        service_ids={"payment": "svc-" + "2" * 24},
    )
    for before, after, phase in (
        ("BASELINE", "PAYMENT", "activation"),
        ("PAYMENT", "BASELINE", "restoration"),
    ):
        change_audit.apply(
            campaign, before_state=before, after_state=after, root=tmp_path, phase=phase
        )
    assert len(sent) == 2 and sent[0]["revision"] != sent[1]["revision"]
    assert all(
        "private" not in json.dumps(v) and "PAYMENT" not in json.dumps(v) for v in sent
    )
    assert (
        json.loads((tmp_path / "activation-configuration-audit.json").read_text())[
            "after"
        ]["document"]
        == documents["PAYMENT"]
    )
    with pytest.raises(ValueError, match="ALREADY_ATTEMPTED"):
        change_audit.apply(
            campaign,
            before_state="BASELINE",
            after_state="PAYMENT",
            root=tmp_path,
            phase="activation",
        )
    assert len(sent) == 2


def test_consumed_deployment_repair_preserves_all_three_identities(
    closure, monkeypatch
):
    from copy import deepcopy
    from ecomsre.product.knowledge import capability_successor_v050 as successor
    from ecomsre.product.jobs.worker import run_one_job

    c = closure
    app, env = c.app, c.runner.environment_id
    old = app.state.capabilities.get(env)
    environment = app.state.environments.get(env).model_dump(mode="json")
    before = dict(
        mode="FIXTURE",
        deployment_id="original",
        environment=environment,
        resource_births={"fixture": "original"},
        runtime_binding={},
        images={"fixture": "capture-c2aa"},
        service_mapping={"payment": "payment"},
        actual_queries={"query": "fixed"},
        units={"cpu": "percent"},
        windows_and_sampling={"samples": 5},
        resource_limits={"max": 5},
        trust_boundary={"fixture": True},
        connector_semantics={"fixture": "capture-c2aa"},
    )
    c.client.post(f"/v1/environments/{env}/verify-jobs")
    assert run_one_job(c.settings, worker_id="reverify")
    consumed = app.state.capabilities.get(env)
    second = dict(
        before, deployment_id="consumed", resource_births={"fixture": "consumed"}
    )
    first_sha = successor.install(
        app.state.store,
        old=old,
        new=consumed,
        old_deployment=before,
        new_deployment=second,
    )
    args = prepare(c, monkeypatch)
    repair.install(c.runner, **args)
    c.client.post(f"/v1/environments/{env}/verify-jobs")
    assert run_one_job(c.settings, worker_id="reverify-again")
    latest = app.state.capabilities.get(env)
    third = dict(before, deployment_id="repair", resource_births={"fixture": "repair"})
    kwargs = dict(
        old=old,
        new=latest,
        old_deployment=before,
        new_deployment=third,
        repair_predecessor_sha256=first_sha,
    )
    for field in (
        "actual_queries",
        "windows_and_sampling",
        "resource_limits",
        "trust_boundary",
    ):
        bad = deepcopy(third)
        bad[field]["drift"] = "changed"
        with pytest.raises(ValueError, match="semantics differ"):
            successor.install(app.state.store, **(kwargs | {"new_deployment": bad}))
    final = successor.install(app.state.store, **kwargs)
    assert successor.install(app.state.store, **kwargs) == final
    for matrix in (old, consumed, latest):
        assert successor.admits(
            app.state.store,
            environment_id=env,
            candidate_environment_id=env,
            expected=matrix.capability_sha256,
            actual=latest.capability_sha256,
        )
        assert (
            successor.historical_matrix(app.state.store, env, matrix.capability_sha256)
            == matrix
        )
    assert not successor.admits(
        app.state.store,
        environment_id=env,
        candidate_environment_id=env,
        expected="0" * 64,
        actual=latest.capability_sha256,
    )
    with pytest.raises(ValueError, match="predecessor"):
        successor.install(
            app.state.store, **(kwargs | {"repair_predecessor_sha256": "0" * 64})
        )
    c.episode(repair.SLOT, "core")
    request = c.runner._keep("request:0", dict(binding="retained fixture request"))
    parent = c.runner._get(repair.KEY)
    resume = repair.install_execution_resume(
        c.runner,
        prior_sources={p: (repair.REPO / p).read_text() for p in repair.SOURCES},
        cleanup={
            "result": {
                "clean": True,
                "remaining": {"container": 0, "network": 0, "volume": 0},
            }
        },
        predecessor_sha256=final,
        rejection=dict(
            error="PROVIDER_INPUT_TOO_LARGE",
            dispatch_occurred=False,
            unchanged_limit_bytes=192000,
            full_wire_payload_bytes_after=1000,
        ),
    )
    assert c.runner._get("request:0") == request and c.runner._get(repair.KEY) == parent
    repeat = dict(
        prior_sources=resume["prior_sources"],
        cleanup=resume["cleanup"],
        predecessor_sha256=final,
        rejection=resume["predispatch_rejection"],
    )
    assert repair.install_execution_resume(c.runner, **repeat) == resume
    with pytest.raises(ValueError, match="immutable"):
        repair.install_execution_resume(
            c.runner, **(repeat | {"predecessor_sha256": "0" * 64})
        )
    c.client.post(f"/v1/environments/{env}/verify-jobs")
    assert run_one_job(c.settings, worker_id="execution-resume")
    fourth = app.state.capabilities.get(env)
    deployment = dict(
        before, deployment_id="resumed", resource_births={"fixture": "resumed"}
    )
    resumed = dict(
        kwargs, new=fourth, new_deployment=deployment, repair_predecessor_sha256=final
    )
    for field in (
        "actual_queries",
        "windows_and_sampling",
        "resource_limits",
        "trust_boundary",
    ):
        bad = deepcopy(deployment)
        bad[field]["drift"] = "changed"
        with pytest.raises(ValueError, match="semantics differ"):
            successor.install(app.state.store, **(resumed | {"new_deployment": bad}))
    digest = successor.install(app.state.store, **resumed)
    assert successor.install(app.state.store, **resumed) == digest
    for matrix in (old, consumed, latest, fourth):
        assert successor.admits(
            app.state.store,
            environment_id=env,
            candidate_environment_id=env,
            expected=matrix.capability_sha256,
            actual=fourth.capability_sha256,
        )
        assert (
            successor.historical_matrix(app.state.store, env, matrix.capability_sha256)
            == matrix
        )
    with app.state.store.connect() as connection:
        assert repair.read_execution_resume(connection, env) == resume

    # Explicit user repair after two failed model calls: original identities remain.
    from ecomsre.product.investigation import closure_budget
    for ordinal in range(2):
        key = closure_budget.PROPOSAL_PREFIX + str(ordinal)
        c.evo.investigations.reserve(key, {"fixture": ordinal}, 1)
        c.evo.investigations.settle(key, {"failure": "TWO_SOURCES_REQUIRED"}, 1, "COMPLETED")
        c.runner._keep("attempt:" + str(ordinal), dict(error="TWO_SOURCES_REQUIRED", ordinal=ordinal))
    c.runner._keep("stop", dict(reason="REPEATED_ERROR_WITHOUT_NEW_OBSERVATIONS", ordinal=1))
    continued = c.runner.authorize_development_resume(authority_sha256="a"*64, verification_sha256="b"*64)
    assert continued["predecessor_sha256"] == digest
    c.client.post(f"/v1/environments/{env}/verify-jobs")
    assert run_one_job(c.settings, worker_id="development-repair")
    fifth = app.state.capabilities.get(env)
    fifth_deployment = dict(before, deployment_id="development-resumed", resource_births={"fixture":"development-resumed"})
    final_kwargs = dict(kwargs, new=fifth, new_deployment=fifth_deployment, repair_predecessor_sha256=digest)
    final_digest = successor.install(app.state.store, **final_kwargs)
    assert successor.install(app.state.store, **final_kwargs) == final_digest
    for matrix in (old, consumed, latest, fourth, fifth):
        assert successor.admits(app.state.store, environment_id=env, candidate_environment_id=env,
            expected=matrix.capability_sha256, actual=fifth.capability_sha256)
        assert successor.historical_matrix(app.state.store, env, matrix.capability_sha256) == matrix
    for field in ("actual_queries", "windows_and_sampling", "resource_limits", "trust_boundary"):
        bad = deepcopy(fifth_deployment)
        bad[field]["drift"] = "changed"
        with pytest.raises(ValueError, match="semantics differ"):
            successor.install(app.state.store, **(final_kwargs | {"new_deployment":bad}))


@pytest.mark.parametrize("barrier", ["provider", "selection", "holdout"])
def test_execution_resume_rejects_consumed_frontier(closure, monkeypatch, barrier):
    c = closure
    repair.install(c.runner, **prepare(c, monkeypatch))
    c.episode(repair.SLOT, "core")
    c.runner._keep("request:0", dict(binding="retained fixture request"))
    if barrier == "provider":
        monkeypatch.setattr(repair.closure_budget, "ledger", lambda c: {"different": 1})
    elif barrier == "selection":
        c.runner._keep("selection-pending", dict(registration_id="fixture"))
    else:
        c.runner._keep("episode:N4", dict(episode_id=c.plan["slots"]["N4"]))
    with pytest.raises(
        ValueError, match="zero new Provider|precede selection|unexposed holdout"
    ):
        repair.install_execution_resume(
            c.runner,
            prior_sources={p: (repair.REPO / p).read_text() for p in repair.SOURCES},
            cleanup={"result": {"clean": True, "remaining": {"container": 0}}},
            predecessor_sha256="0" * 64,
            rejection=dict(
                error="PROVIDER_INPUT_TOO_LARGE",
                dispatch_occurred=False,
                unchanged_limit_bytes=192000,
                full_wire_payload_bytes_after=1000,
            ),
        )
    assert c.runner._get(repair.RESUME_KEY) is None


def test_execution_resume_rejects_changed_collection_globals(
    closure, monkeypatch, tmp_path
):
    c = closure
    repair.install(c.runner, **prepare(c, monkeypatch))
    c.episode(repair.SLOT, "core")
    c.runner._keep("request:0", dict(binding="retained fixture request"))
    sources = {p: (repair.REPO / p).read_text() for p in repair.SOURCES}
    for path in (*repair.SOURCES, repair.AUTHORITY, repair.COLLECTION):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((repair.REPO / path).read_bytes())
    live = tmp_path / "scripts/product_v050/final_closure_live.py"
    live.write_text(
        live.read_text().replace("window_offset_seconds=30", "window_offset_seconds=31")
    )
    monkeypatch.setattr(repair, "REPO", tmp_path)
    with pytest.raises(ValueError, match="deployment path change|module changed"):
        repair.install_execution_resume(
            c.runner,
            prior_sources=sources,
            cleanup={"result": {"clean": True, "remaining": {"container": 0}}},
            predecessor_sha256="0" * 64,
            rejection=dict(
                error="PROVIDER_INPUT_TOO_LARGE",
                dispatch_occurred=False,
                unchanged_limit_bytes=192000,
                full_wire_payload_bytes_after=1000,
            ),
        )


def test_normal_changes_repository_supports_existing_core_clause(closure, monkeypatch):
    """Fixture telemetry plus real ingestion/read adapter; no live claim."""
    from ecomsre.product.incidents.read_backend import ProductReadBackendV1

    c = closure
    original_execute = ProductReadBackendV1._execute
    original_records = FixtureConnectorV1._records
    service = next(
        s.service_id
        for s in c.app.state.services.get_map(c.runner.environment_id).services
        if s.logical_service == "payment"
    )
    response = c.client.post(
        f"/v1/environments/{c.runner.environment_id}/changes",
        json=dict(
            service_id=service,
            category="CONFIGURATION",
            occurred_at=datetime.now(UTC).isoformat(),
            revision="opaque-fixture-revision",
            external_change_id="opaque-fixture-event",
            summary="Observed local configuration rollout",
        ),
    )
    assert response.status_code == 201

    def execute(self, **kw):
        if kw["action"].source.value == "CHANGES":
            # Only route fixture CHANGES through the real local repository branch.
            kw["environment"] = kw["environment"].model_copy(
                update={"connector_configs": ()}
            )
        return original_execute(self, **kw)

    def records(self, **kw):
        rows = original_records(self, **kw)
        if kw["current_observation"] and kw["source"].value == "METRICS":
            return tuple(
                r.model_copy(update={"value": 0.5})
                if r.service == "payment" and r.metric_kind.value == "ERROR_RATE"
                else r
                for r in rows
            )
        return rows

    monkeypatch.setattr(ProductReadBackendV1, "_execute", execute)
    monkeypatch.setattr(FixtureConnectorV1, "_records", records)
    iid = c.new("change-evidence", "healthy")
    diagnosis = c.evo.knowledge._diagnosis(iid)
    assert diagnosis.terminal.value == "CORE_KNOWN", diagnosis
    assert diagnosis.root_service_ids == (service,)
    assert diagnosis.mechanism == "CONFIGURATION_ERROR"
    material, observations = c.runner.observations(iid)
    assert any(
        o["source"] == "CHANGES"
        and o["status"] == "SUCCESS_NONEMPTY"
        and "payment" in o["covered_services"]
        for o in observations
    )
    # The separate service's runtime/metrics observations remain available; a
    # known payment result does not stand in for its coverage.
    assert all(
        any(
            o["source"] == source
            and "checkout" in o["covered_services"]
            and not o["truncated"]
            for o in observations
        )
        for source in ("METRICS", "RUNTIME")
    )


def test_development_repair_resume_preserves_stop_and_original_six_slots(closure, monkeypatch):
    from ecomsre.product.investigation import closure_budget
    from ecomsre.product.errors import ProductError
    c=closure
    args=prepare(c,monkeypatch)
    repair.install(c.runner,**args)
    c.episode(repair.SLOT,'core')
    c.runner.freeze_cohort()
    monkeypatch.setattr(c.runner,'_resume_predecessor',lambda:'c'*64)
    for n in range(2):
        key=closure_budget.PROPOSAL_PREFIX+str(n)
        c.evo.investigations.reserve(key,dict(fixture=n),1)
        c.evo.investigations.settle(key,dict(proposal='retained failure'),1,'COMPLETED')
        c.runner._keep('attempt:'+str(n),dict(ordinal=n,error='TWO_SOURCES_REQUIRED'))
    stopped=dict(reason='REPEATED_ERROR_WITHOUT_NEW_OBSERVATIONS',ordinal=1)
    c.runner._keep('stop',stopped)
    with pytest.raises(ProductError,match='unresumed stop'):
        c.evo.investigations.reserve(closure_budget.PROPOSAL_PREFIX+'2',dict(fixture=2),1)
    resume=c.runner.authorize_development_resume(authority_sha256='a'*64,verification_sha256='b'*64)
    assert resume['maximum_additional_semantic_attempts']==4
    assert c.runner._get('stop')==stopped and c.runner.active_stop() is None
    assert c.runner.authorize_development_resume(authority_sha256='a'*64,verification_sha256='b'*64)==resume
    assert c.runner.verify_development_resume()==resume
    with pytest.raises(ValueError,match='authorization differs'):
        c.runner.authorize_development_resume(authority_sha256='f'*64,verification_sha256='b'*64)
    for n in range(2,6):
        key=closure_budget.PROPOSAL_PREFIX+str(n)
        c.evo.investigations.reserve(key,dict(prompt_version='knowledge-draft-v050.3'),1)
        c.evo.investigations.settle(key,dict(fixture=n),1,'FAILED')
    with pytest.raises(ProductError,match='semantic slots'):
        c.evo.investigations.reserve(closure_budget.PROPOSAL_PREFIX+'6',dict(prompt_version='knowledge-draft-v050.3'),1)
    c.runner._keep('stop:development-repair-resume',dict(reason='new stop',ordinal=5))
    assert c.runner.active_stop()['reason']=='new stop'
    assert c.runner._get('stop')==stopped


def test_effective_a_pass_blocks_dispatch_before_pending_marker(closure, monkeypatch):
    from ecomsre.product.investigation import closure_budget
    from ecomsre.product.errors import ProductError
    c=closure
    repair.install(c.runner,**prepare(c,monkeypatch))
    assert c.runner.plan['primary_level']=='B' and c.runner.primary_level=='A'
    c.runner._keep('development:fixture-crash',dict(passed=True,level='A'))
    assert c.runner._get('selection-pending') is None
    with pytest.raises(ProductError,match='No proposal dispatch after selection'):
        c.evo.investigations.reserve(closure_budget.PROPOSAL_PREFIX+'0',{'fixture':True},1)


@pytest.mark.parametrize('direct_holdout',[False,True])
def test_repaired_wire_admission_development_lock_and_freeze(closure, monkeypatch, direct_holdout):
    from ecomsre.product.investigation import closure_budget
    from ecomsre.product.investigation.provider import StructuredProvider
    from ecomsre.product.investigation.contracts import PriceSchedule
    from ecomsre.model.gateway import OpenAICompatibleConfig
    c=closure
    repair.install(c.runner,**prepare(c,monkeypatch))
    c.episode(repair.SLOT,'core')
    c.runner.freeze_cohort()
    monkeypatch.setattr(c.runner,'_resume_predecessor',lambda:'c'*64)
    for n in range(2):
        key=closure_budget.PROPOSAL_PREFIX+str(n)
        c.evo.investigations.reserve(key,{'fixture':n},1)
        c.evo.investigations.settle(key,{'fixture':n},1,'COMPLETED')
        c.runner._keep('attempt:'+str(n),dict(ordinal=n,error='TWO_SOURCES_REQUIRED'))
    c.runner._keep('stop',dict(reason='REPEATED_ERROR_WITHOUT_NEW_OBSERVATIONS',ordinal=1))
    if direct_holdout:
        iid=c.new('bypass-runner','healthy')
        c.evo.bind_episode(iid,c.plan['slots']['N4'])
        with pytest.raises(ValueError,match='outside runner'):
            c.runner.authorize_development_resume(authority_sha256='a'*64,verification_sha256='b'*64)
        return
    c.runner.authorize_development_resume(authority_sha256='a'*64,verification_sha256='b'*64)
    def post(**kw):
        payload=kw['payload']
        wire=json.loads(payload['input'][0]['content'])['view']
        raw=dict(disposition='CANDIDATE',reason='Fixture only',binding_id=wire['binding_id'],
            candidate=dict(name='fixture-source-repair',target='payment',broad_domain='UNKNOWN',
                member_incidents=wire['scope']['members'],predicates=dict(first='core:RUNTIME_HEALTHY',second='ga:LOG_UNKNOWN_ERROR_PATTERN',third=None),
                expression=None,target_support=[a for a,o in wire['target_evidence'].items() if o['source'] in {'RUNTIME','LOGS'}][:24],
                target_counterevidence=[],comparison_context=[],confusable_patterns=['fixture'],prediction='fixture',inapplicable_conditions=['missing']))
        return dict(model='gpt-5.4-mini-2026-03-17',status='completed',id='fixture',output=[dict(type='function_call',status='completed',name='submit_proposal',arguments=json.dumps(raw))],usage=dict(input_tokens=100,output_tokens=100))
    provider=StructuredProvider(OpenAICompatibleConfig('https://api.openai.com/v1','fixture','gpt-5.4-mini-2026-03-17'),
        PriceSchedule(provider_profile='fixture',model='gpt-5.4-mini-2026-03-17',as_of='2026-09-17',source='fixture',input_usd_per_million=.75,output_usd_per_million=4.5),c.evo.investigations,SimpleNamespace(post_json=post),api_style='responses')
    # Synthetic provenance flag exists only in this isolated fixture DB, to exercise the guard.
    monkeypatch.setattr(provider, 'evidence_mode', 'LIVE_PROVIDER')
    result=c.runner.propose_next(provider)
    assert result['status']=='SELECTED',result
    assert c.runner._get('stop')['ordinal']==1
    with pytest.raises(ValueError,match='selection only'):
        c.runner.propose_next(provider)
    for slot,kind in (('N4','positive'),('N5','healthy'),('N6','core')):
        c.episode(slot,kind)
    from ecomsre.product.knowledge.candidates_v050 import CompiledKnowledge
    with c.evo.store.connect() as conn:
        row=conn.execute('SELECT payload_json FROM knowledge_candidate_pool_v050 WHERE registration_id=?',(result['registration_id'],)).fetchone()
    candidate=CompiledKnowledge.model_validate_json(row[0])
    assert c.runner.evaluate_and_promote(candidate).gate_passed
    assert candidate.source_request_key==closure_budget.PROPOSAL_PREFIX+'2'
    assert candidate.proposal.predicates==['core:RUNTIME_HEALTHY','ga:LOG_UNKNOWN_ERROR_PATTERN']
