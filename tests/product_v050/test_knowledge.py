import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.app import create_app
from ecomsre.product.jobs.worker import run_one_job
from ecomsre.product.knowledge.candidates_v050 import (
    CompiledKnowledge,
    KnowledgeProposal,
)
from ecomsre.product.settings import ProductSettingsV1
from test_investigation import prepare


def test_level_b_registry_normal_diagnosis_and_revocation(tmp_path):
    # FIXTURE_ONLY compiler/loader/runtime test. This direct fixture insertion is
    # not a governance promotion or LLM knowledge evidence.
    settings = ProductSettingsV1(data_root=tmp_path)
    with TestClient(create_app(settings)) as client:
        inc = prepare(client, settings)
        app = client.app
        incident = app.state.incidents.get(inc)
        evidence = app.state.diagnoses.evidence(inc)
        resource = next(
            x.payload["connector_result"]
            for x in evidence.objects
            if x.payload.get("connector_result", {}).get("source") == "RESOURCES"
        )
        sample = resource["records"][0]
        proposal = KnowledgeProposal(
            name="fixture-resource-pattern",
            kind="PATTERN_ONLY",
            target="payment",
            broad_domain="RESOURCE",
            member_incidents=["fixture-discovery-1", "fixture-discovery-2"],
            predicates=["core:RUNTIME_HEALTHY"],
            expression={
                "numerator": {"field": "cpu_percent", "operator": "mean"},
                "denominator": None,
                "comparator": "gt",
                "threshold": 1.0,
                "threshold_unit": "PERCENT",
                "threshold_provenance": "fixture-only",
                "window_seconds": sample["sampling_window_seconds"],
                "minimum_samples": 2,
            },
            supporting_refs=["fixture-ref"],
            counter_evidence_refs=[],
            confusable_patterns=["constant load"],
            prediction="Resource mean above frozen fixture limit",
            inapplicable_conditions=["missing samples"],
        )
        payload = {
            "schema_version": "ecomsre.product.compiled-knowledge.v050",
            "registration_id": "fixture-registration",
            "environment_id": incident.environment_id,
            "capability_sha256": incident.source_capability_sha256,
            "origin": "FIXTURE_ONLY",
            "source_request_key": None,
            "discovery_snapshot_sha256": "0" * 64,
            "discovery_incident_ids": ["fixture-discovery-1", "fixture-discovery-2"],
            "proposal": proposal.model_dump(mode="json"),
            "action_authority": "NONE",
        }
        candidate = CompiledKnowledge.model_validate(
            payload | {"compiled_sha256": semantic_sha256_v22(payload)}
        )
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
        assert app.state.knowledge.active_extensions(incident.environment_id) == ()
        assert (
            len(
                app.state.knowledge.active_investigation_extensions(
                    incident.environment_id
                )
            )
            == 1
        )

        def new_event(key):
            raw = incident.model_dump(
                mode="json",
                include={
                    "environment_id",
                    "alert_name",
                    "summary",
                    "candidate_service_ids",
                },
            )
            raw.update(
                external_incident_key=key, started_at=datetime.now(UTC).isoformat()
            )
            new = client.post("/v1/incidents", json=raw).json()["incident_id"]
            job = client.post(f"/v1/incidents/{new}/diagnosis-jobs").json()
            assert run_one_job(settings, worker_id=key)
            status = client.get("/v1/jobs/" + job["job_id"]).json()
            assert status["status"] == "SUCCEEDED", json.dumps(status)
            return client.get(f"/v1/incidents/{new}/diagnosis").json()

        result = new_event("fixture-recurrence")
        assert result["terminal"] == "EXTENSION_KNOWN", result
        assert result["provider_calls"] == 0
        with app.state.store.connect() as c:
            c.execute(
                "UPDATE environment_extension_registrations SET status='REVOKED' WHERE registration_id=?",
                (candidate.registration_id,),
            )
        assert new_event("fixture-revoked")["terminal"] == "OPEN_WORLD"
        from ecomsre.product.investigation.repository import InvestigationRepository
        from ecomsre.product.knowledge.evolution_v050 import KnowledgeEvolutionV050

        KnowledgeEvolutionV050(
            app.state.knowledge,
            InvestigationRepository(app.state.store, app.state.object_store),
        )
        with app.state.store.connect() as c:
            c.execute(
                "UPDATE environment_extension_registrations SET status='ACTIVE' WHERE registration_id=?",
                (candidate.registration_id,),
            )
            c.execute(
                "INSERT INTO knowledge_candidate_pool_v050 VALUES (?,?,?,?,'ACTIVE',NULL,NULL)",
                (
                    candidate.registration_id,
                    incident.environment_id,
                    candidate.compiled_sha256,
                    candidate.model_dump_json(),
                ),
            )
        legacy = client.post(
            "/v1/registrations/fixture-registration/revocations",
            json={
                "reviewer": "fixture",
                "note": "compatibility regression",
                "revoked_at": datetime.now(UTC).isoformat(),
            },
        )
        assert legacy.status_code == 409, legacy.text
        assert "SUCCESSOR_REVOCATION_REQUIRED" in legacy.text
        revoked = client.post(
            "/v1/knowledge-candidates/fixture-registration/revocations"
        )
        assert revoked.status_code == 200, revoked.text
        assert new_event("fixture-api-revoked")["terminal"] == "OPEN_WORLD"
        with app.state.store.connect() as c:
            assert (
                c.execute(
                    "SELECT status FROM environment_extension_registry_versions WHERE registration_id=?",
                    (candidate.registration_id,),
                ).fetchone()[0]
                == "REVOKED"
            )
        assert (
            client.post(
                "/v1/knowledge-candidates/fixture-registration/revocations"
            ).status_code
            == 409
        )


def test_holdout_excludes_all_discovery_inputs_and_cannot_reenter_proposer(tmp_path):
    import pytest
    from ecomsre.product.investigation.repository import InvestigationRepository
    from ecomsre.product.knowledge.evolution_v050 import KnowledgeEvolutionV050

    settings = ProductSettingsV1(data_root=tmp_path)
    with TestClient(create_app(settings)) as client:
        seen = prepare(client, settings)
        app = client.app
        incident = app.state.incidents.get(seen)
        evolution = KnowledgeEvolutionV050(
            app.state.knowledge,
            InvestigationRepository(app.state.store, app.state.object_store),
        )
        proposal = KnowledgeProposal(
            name="fixture-candidate",
            kind="PATTERN_ONLY",
            target="payment",
            broad_domain="RESOURCE",
            member_incidents=["member-a", "member-b"],
            predicates=["core:RUNTIME_HEALTHY", "ga:RESOURCE_CPU_OUTLIER"],
            expression=None,
            supporting_refs=["fixture"],
            counter_evidence_refs=[],
            confusable_patterns=["other"],
            prediction="Recurs",
            inapplicable_conditions=["missing"],
        )
        payload = {
            "schema_version": "ecomsre.product.compiled-knowledge.v050",
            "registration_id": "fixture-isolation",
            "environment_id": incident.environment_id,
            "capability_sha256": incident.source_capability_sha256,
            "origin": "FIXTURE_ONLY",
            "source_request_key": None,
            "discovery_snapshot_sha256": "0" * 64,
            "discovery_incident_ids": ["member-a", "member-b", seen],
            "proposal": proposal.model_dump(mode="json"),
            "action_authority": "NONE",
        }
        compiled = CompiledKnowledge.model_validate(
            payload | {"compiled_sha256": semantic_sha256_v22(payload)}
        )
        with app.state.store.connect() as c:
            c.execute(
                "INSERT INTO knowledge_candidate_pool_v050 VALUES (?,?,?,?,'DRAFT',NULL,NULL)",
                (
                    compiled.registration_id,
                    incident.environment_id,
                    compiled.compiled_sha256,
                    compiled.model_dump_json(),
                ),
            )
        with pytest.raises(ValueError, match="overlaps discovery"):
            evolution.freeze(compiled.registration_id, {seen: "POSITIVE_INCIDENT"})
        with app.state.store.connect() as c:
            c.execute(
                "UPDATE knowledge_candidate_pool_v050 SET freeze_json=?",
                (json.dumps({"cases": {seen: "POSITIVE_INCIDENT"}}),),
            )
        with pytest.raises(ValueError, match="cannot enter discovery"):
            evolution.discovery_view(incident.environment_id, [seen, "member-a"])


def test_existing_environment_cannot_gain_test_promotion_authority(tmp_path):
    import pytest
    from ecomsre.product.errors import ProductError
    from ecomsre.product.investigation.repository import InvestigationRepository
    from ecomsre.product.knowledge.evolution_v050 import KnowledgeEvolutionV050

    settings = ProductSettingsV1(data_root=tmp_path)
    with TestClient(create_app(settings)) as client:
        incident = prepare(client, settings)
        app = client.app
        evolution = KnowledgeEvolutionV050(
            app.state.knowledge,
            InvestigationRepository(app.state.store, app.state.object_store),
        )
        with pytest.raises(ProductError, match="unused test environment"):
            evolution.enroll_fresh_test_environment(
                app.state.incidents.get(incident).environment_id
            )
        with pytest.raises(ProductError, match="Independent validation"):
            evolution.promote("missing")


def test_candidate_from_persisted_discovery_is_frozen_consumed_and_rejected_without_controls(
    tmp_path, monkeypatch
):
    import pytest
    from ecomsre.product.errors import ProductError
    from ecomsre.product.investigation.repository import InvestigationRepository
    from ecomsre.product.knowledge.evolution_v050 import KnowledgeEvolutionV050
    from test_investigation import InvestigatingFixture

    settings = ProductSettingsV1(data_root=tmp_path, investigation={"enabled": True})
    monkeypatch.setattr(
        "ecomsre.product.investigation.runtime.configured_provider",
        lambda repository: InvestigatingFixture(),
    )
    with TestClient(create_app(settings)) as client:
        first = prepare(client, settings)
        app = client.app
        incident = app.state.incidents.get(first)

        def new_event(key):
            raw = incident.model_dump(
                mode="json",
                include={
                    "environment_id",
                    "alert_name",
                    "summary",
                    "candidate_service_ids",
                },
            )
            raw.update(
                external_incident_key=key, started_at=datetime.now(UTC).isoformat()
            )
            created = client.post("/v1/incidents", json=raw).json()["incident_id"]
            job = client.post(f"/v1/incidents/{created}/diagnosis-jobs").json()
            assert run_one_job(settings, worker_id=key)
            assert (
                client.get("/v1/jobs/" + job["job_id"]).json()["status"] == "SUCCEEDED"
            )
            return created

        second = new_event("second-discovery")
        heldout = new_event("separate-fixture-evaluation")
        for item in [first, second]:
            job = client.post(f"/v1/incidents/{item}/investigation-jobs").json()
            assert run_one_job(settings, worker_id="fixture-investigate")
            assert (
                client.get("/v1/jobs/" + job["job_id"]).json()["status"] == "SUCCEEDED"
            )
        evolution = KnowledgeEvolutionV050(
            app.state.knowledge,
            InvestigationRepository(app.state.store, app.state.object_store),
        )
        view = evolution.discovery_view(incident.environment_id, [first, second])
        from types import SimpleNamespace
        monkeypatch.setattr(app.state.knowledge, "get_family", lambda _:SimpleNamespace(
            family_id="fixture-family",environment_id=incident.environment_id,
            member_incident_ids=(first,second,heldout)))
        monkeypatch.setattr(app.state.knowledge, "_matrix_for_family", lambda _:pytest.fail("global matrix scans heldout"))
        original_fingerprint = app.state.knowledge.fingerprint_for
        seen_fingerprints = []
        def bounded_fingerprint(item):
            assert item in {first,second}, "miner read a non-discovery event"
            seen_fingerprints.append(item)
            return original_fingerprint(item)
        monkeypatch.setattr(app.state.knowledge, "fingerprint_for", bounded_fingerprint)
        assert evolution.add_miner_candidates(family_id="fixture-family",incident_ids=[first,second]) == ()
        assert set(seen_fingerprints) == {first,second}
        resource = next(
            o for o in view["sessions"][0]["observations"] if o["source"] == "RESOURCES"
        )
        proposal = KnowledgeProposal(
            name="fixture-only-governance",
            kind="PATTERN_ONLY",
            target="payment",
            broad_domain="RESOURCE",
            member_incidents=[first, second],
            predicates=["core:RUNTIME_HEALTHY"],
            expression={
                "numerator": {"field": "cpu_percent", "operator": "mean"},
                "comparator": "gt",
                "threshold": 1.0,
                "threshold_unit": "PERCENT",
                "threshold_provenance": view["snapshot_sha256"],
                "window_seconds": resource["records"][0]["sampling_window_seconds"],
                "minimum_samples": 2,
            },
            supporting_refs=[resource["evidence_ref"]],
            counter_evidence_refs=[],
            confusable_patterns=["constant load"],
            prediction="Fixture resource value persists",
            inapplicable_conditions=["missing samples"],
        )
        with pytest.raises(ValueError, match="completed model response"):
            evolution.add_candidate(
                environment_id=incident.environment_id,
                proposal=proposal,
                origin="LLM",
                source_request_key="fabricated",
                discovery=view,
            )
        candidate = evolution.add_candidate(
            environment_id=incident.environment_id,
            proposal=proposal,
            origin="FIXTURE_ONLY",
            source_request_key=None,
            discovery=view,
        )
        with pytest.raises(ProductError, match="equivalent candidate"):
            evolution.add_candidate(
                environment_id=incident.environment_id,
                proposal=proposal,
                origin="FIXTURE_ONLY",
                source_request_key=None,
                discovery=view,
            )
        development = new_event("development-fixture")
        feedback = evolution.check_development(candidate.registration_id, [development])
        assert feedback["claim"] == "DEVELOPMENT_ONLY_NOT_INDEPENDENT_VALIDATION"
        assert feedback["outcomes"][0]["outcome"]["status"] == "TRUE"
        with pytest.raises(ValueError, match="overlaps development"):
            evolution.freeze(
                candidate.registration_id, {development: "POSITIVE_INCIDENT"}
            )
        evolution.freeze(candidate.registration_id, {heldout: "POSITIVE_INCIDENT"})
        evaluation = evolution.evaluate(candidate.registration_id)
        assert not evaluation.gate_passed
        assert "CONFUSABLE_CORE_KNOWN_CONTROL_MISSING" in evaluation.reason_codes
        with pytest.raises(ValueError, match="unconsumed"):
            evolution.evaluate(candidate.registration_id)
        with pytest.raises(ProductError, match="Independent validation"):
            evolution.promote(candidate.registration_id)
        assert (
            app.state.knowledge.active_investigation_extensions(incident.environment_id)
            == ()
        )
