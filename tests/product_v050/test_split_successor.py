"""Fixture-only forward split; never rewrites real episodes or evaluates holdout."""

from datetime import UTC, datetime
import json

from fastapi.testclient import TestClient
import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22 as sha
from ecomsre.product.app import create_app
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.investigation.repository import InvestigationRepository
from ecomsre.product.knowledge.evolution_v050 import KnowledgeEvolutionV050
from ecomsre.product.knowledge import split_v050 as split
from test_investigation import prepare


@pytest.fixture
def cohort(tmp_path):
    settings = ProductSettingsV1(data_root=tmp_path)
    with TestClient(create_app(settings)) as client:
        first = prepare(client, settings)
        app = client.app
        incident = app.state.incidents.get(first)
        evo = KnowledgeEvolutionV050(
            app.state.knowledge,
            InvestigationRepository(app.state.store, app.state.object_store),
        )
        original = {"e01": "DISCOVERY", "e06": "HOLDOUT", "e07": "REUSE"}
        evo.freeze_split(incident.environment_id, original)
        evo.bind_episode(first, "e01")

        def create(key):
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
            response = client.post("/v1/incidents", json=raw)
            assert response.status_code == 201, response.text
            return app.state.incidents.get(response.json()["incident_id"])

        yield app.state.store, evo, incident, original, create


def test_forward_split_preserves_history_exposure_and_old_unused_slots(cohort):
    store, evo, old, original, create = cohort
    alias = create("old-alias")
    evo.bind_episode(alias.incident_id, "e01")
    with store.connect() as c:
        split.expose(c, [old.incident_id], "DISCOVERY_VIEW")
        original_bytes = c.execute(
            "SELECT manifest_json FROM knowledge_split_v050"
        ).fetchone()[0]
        assert split.split_digest(c, old.environment_id) == sha(original)
    additions = {"n1": "DEVELOPMENT", "n4": "HOLDOUT", "n7": "REUSE"}
    digest = split.append_final_closure_split(
        store, old.environment_id, additions, parent_sha256=sha(original)
    )
    # Restart/idempotent replay preserves the exact original reservation.
    split.initialize(store)
    assert (
        split.append_final_closure_split(
            store, old.environment_id, additions, parent_sha256=sha(original)
        )
        == digest
    )
    dev, heldout = create("future-dev"), create("future-holdout")
    evo.bind_episode(dev.incident_id, "n1")
    evo.bind_episode(heldout.incident_id, "n4")
    with store.connect() as c:
        assert (
            c.execute("SELECT manifest_json FROM knowledge_split_v050").fetchone()[0]
            == original_bytes
        )
        assert split.effective_manifest(c, old.environment_id) == original | additions
        assert split.split_digest(c, old.environment_id) == sha(original | additions)
        assert {old.incident_id, alias.incident_id} <= split.exposed_incidents(c)
        assert heldout.incident_id not in split.exposed_incidents(c)
        split.require_roles(c, [dev.incident_id], {"DEVELOPMENT"})
        split.require_roles(c, [heldout.incident_id], {"HOLDOUT"})
        with pytest.raises(ValueError, match="incompatible"):
            split.require_roles(c, [old.incident_id], {"HOLDOUT"})
        with pytest.raises(ValueError, match="denominator"):
            split.require_independent(c, [old.incident_id, alias.incident_id])
    with pytest.raises(ValueError, match="immutable"):
        evo.bind_episode(alias.incident_id, "n4")
    with pytest.raises(ValueError, match="immutable"):
        split.append_final_closure_split(
            store, old.environment_id, {"n9": "HOLDOUT"}, parent_sha256=sha(original)
        )


def test_existing_unbound_incident_cannot_be_rebranded_as_future(cohort):
    store, evo, old, original, create = cohort
    unbound = create("already-collected")
    split.append_final_closure_split(
        store, old.environment_id, {"n4": "HOLDOUT"}, parent_sha256=sha(original)
    )
    with pytest.raises(ValueError, match="new incident"):
        evo.bind_episode(unbound.incident_id, "n4")


@pytest.mark.parametrize(
    "additions,parent,error",
    [
        ({"e06": "DEVELOPMENT"}, None, "immutable"),
        ({"e06": "HOLDOUT"}, None, "immutable"),
        ({"n1": "DISCOVERY"}, None, "allocation"),
        ({f"n{i}": "DEVELOPMENT" for i in range(4)}, None, "allocation"),
        ({"n1": "HOLDOUT"}, "0" * 64, "parent"),
    ],
)
def test_split_rejects_old_identity_budget_or_parent_changes(
    cohort, additions, parent, error
):
    store, _, old, original, _ = cohort
    with pytest.raises(ValueError, match=error):
        split.append_final_closure_split(
            store, old.environment_id, additions, parent_sha256=parent or sha(original)
        )
    with store.connect() as c:
        assert split.effective_manifest(c, old.environment_id) == original
        assert (
            c.execute("SELECT count(*) FROM knowledge_split_successor_v050").fetchone()[
                0
            ]
            == 0
        )


def test_no_successor_after_frozen_evaluation_and_no_digest_tampering(cohort):
    store, _, old, original, _ = cohort
    with store.connect() as c:
        c.execute(
            "INSERT INTO knowledge_candidate_pool_v050 VALUES (?,?,?,?,'FROZEN',?,NULL)",
            ("fixture-only", old.environment_id, "fixture", "{}", "{}"),
        )
    with pytest.raises(ValueError, match="frozen evaluation"):
        split.append_final_closure_split(
            store,
            old.environment_id,
            {"n1": "DEVELOPMENT"},
            parent_sha256=sha(original),
        )
    with store.connect() as c:
        c.execute(
            "DELETE FROM knowledge_candidate_pool_v050 WHERE registration_id='fixture-only'"
        )
    split.append_final_closure_split(
        store, old.environment_id, {"n1": "DEVELOPMENT"}, parent_sha256=sha(original)
    )
    with store.connect() as c:
        value = json.loads(
            c.execute(
                "SELECT payload_json FROM knowledge_split_successor_v050"
            ).fetchone()[0]
        )
        value["additions"]["n1"] = "HOLDOUT"
        c.execute(
            "UPDATE knowledge_split_successor_v050 SET payload_json=?",
            (json.dumps(value),),
        )
        with pytest.raises(ValueError, match="digest"):
            split.split_digest(c, old.environment_id)
