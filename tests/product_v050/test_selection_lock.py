"""Fixture-only selection identity barriers; not development-gate acceptance."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json

import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22 as sha
from ecomsre.product.errors import ProductError
from ecomsre.product.knowledge.candidates_v050 import (
    CompiledKnowledge,
    KnowledgeProposal,
)
from ecomsre.product.knowledge import (
    selection_lock_v050 as selection,
    split_v050 as split,
)
from ecomsre.product.knowledge.shadow_controls_v050 import CONTROL_VERSION
from test_split_successor import cohort  # noqa: F401


@pytest.fixture
def prepared(request):
    store, evo, incident, original, create = request.getfixturevalue("cohort")
    proposal = KnowledgeProposal(
        name="fixture-only-selection",
        kind="PATTERN_ONLY",
        target="payment",
        broad_domain="RESOURCE",
        member_incidents=[incident.incident_id, "fixture-peer"],
        predicates=["core:RUNTIME_HEALTHY", "core:RESOURCE_MEMORY_GROWTH_STRONG"],
        expression=None,
        supporting_refs=["fixture-ref"],
        counter_evidence_refs=[],
        confusable_patterns=["fixture"],
        prediction="fixture only",
        inapplicable_conditions=["fixture missing source"],
    )
    payload = dict(
        registration_id="fixture-lock",
        environment_id=incident.environment_id,
        capability_sha256=incident.source_capability_sha256,
        origin="FIXTURE_ONLY",
        source_request_key=None,
        discovery_snapshot_sha256="fixture",
        discovery_incident_ids=[incident.incident_id],
        proposal=proposal.model_dump(mode="json"),
        action_authority="NONE",
        schema_version="ecomsre.product.compiled-knowledge.v050",
    )
    candidate = CompiledKnowledge.model_validate(
        {**payload, "compiled_sha256": sha(payload)}
    )
    with store.connect() as c:
        c.execute(
            "INSERT INTO knowledge_candidate_pool_v050 VALUES (?,?,?,?,'DRAFT',NULL,NULL)",
            (
                candidate.registration_id,
                incident.environment_id,
                candidate.compiled_sha256,
                candidate.model_dump_json(),
            ),
        )
    evo.check_development(candidate.registration_id, [incident.incident_id])
    split.append_final_closure_split(
        store,
        incident.environment_id,
        {"n4": "HOLDOUT", "n5": "HOLDOUT", "n6": "HOLDOUT", "n7": "REUSE"},
        parent_sha256=sha(original),
    )
    plan = dict(
        holdout_episodes={
            "n4": "POSITIVE_INCIDENT",
            "n5": "NO_INCIDENT",
            "n6": "CONFUSABLE_CORE_KNOWN",
        },
        recurrence_episode="n7",
        collection={"fixture_only": True},
        time_range={
            "start": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "end": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
        development_gate_report={"status": "FIXTURE_IDENTITY_TEST_NOT_GATE_PASS"},
        compatibility_binding={"mode": "EXACT_CAPABILITY_ONLY"},
        expression_checks={"status": "NOT_APPLICABLE_LEVEL_A"},
        derived_controls_version=CONTROL_VERSION,
    )
    return store, evo, incident, create, candidate, plan


def test_selection_locks_identity_and_exact_future_cohort(prepared):
    store, evo, incident, create, candidate, plan = prepared
    lock = selection.seal(store, candidate.registration_id, plan=plan)
    assert selection.seal(store, candidate.registration_id, plan=plan) == lock
    assert lock["bindings"]["development"]["state"] == "CHECKED"
    assert lock["claim"] == "IDENTITY_LOCK_ONLY_NOT_DEVELOPMENT_GATE_ACCEPTANCE"
    future = {}
    for episode, label in plan["holdout_episodes"].items():
        new = create(episode)
        evo.bind_episode(new.incident_id, episode)
        future[new.incident_id] = label
    with store.connect() as c:
        assert (
            selection.require_frozen_identity(c, candidate, future, CONTROL_VERSION)
            == lock["sha256"]
        )
        with pytest.raises(ValueError, match="cohort"):
            selection.require_frozen_identity(
                c, candidate, dict(list(future.items())[:2]), CONTROL_VERSION
            )
    changed = deepcopy(plan)
    changed["collection"]["extra"] = True
    with pytest.raises(ValueError, match="immutable"):
        selection.seal(store, candidate.registration_id, plan=changed)
    with pytest.raises(ProductError) as error:
        evo.investigations.reserve(
            "concealed-proposal",
            {
                "payload": {
                    "input": [
                        {
                            "role": "user",
                            "content": json.dumps(
                                {"task": "propose_detection_knowledge", "view": {}}
                            ),
                        }
                    ]
                }
            },
            1,
        )
    assert error.value.code == "CANDIDATE_SELECTION_LOCKED"
    # Ordinary diagnosis reads are not proposals or paid model calls.
    assert evo.investigations.accounting()["request_count"] == 0


def test_preexisting_incident_cannot_be_late_bound_as_holdout(prepared):
    store, evo, _, create, candidate, plan = prepared
    old = create("observed-before-selection")
    selection.seal(store, candidate.registration_id, plan=plan)
    with pytest.raises(ValueError, match="new incident"):
        evo.bind_episode(old.incident_id, "n4")
    fresh = create("wrong-planned-slot")
    with pytest.raises(ValueError, match="planned episode"):
        evo.bind_episode(fresh.incident_id, "e06")


def test_selection_refuses_already_collected_holdout(prepared):
    store, evo, _, create, candidate, plan = prepared
    heldout = create("too-early")
    evo.bind_episode(heldout.incident_id, "n4")
    with pytest.raises(ValueError, match="precede holdout"):
        selection.seal(store, candidate.registration_id, plan=plan)


def test_freeze_requires_lock_and_detects_development_drift(prepared):
    store, evo, incident, _, candidate, plan = prepared
    with pytest.raises(ValueError, match="selection lock"):
        evo.freeze(
            candidate.registration_id,
            {incident.incident_id: "POSITIVE_INCIDENT"},
            derived_controls_version=CONTROL_VERSION,
        )
    selection.seal(store, candidate.registration_id, plan=plan)
    with store.connect() as c:
        row = c.execute(
            "SELECT payload_json FROM knowledge_development_v050"
        ).fetchone()
        report = json.loads(row[0])
        report["claim"] = "changed"
        c.execute(
            "UPDATE knowledge_development_v050 SET payload_json=?",
            (json.dumps(report),),
        )
    with pytest.raises(ValueError, match="identity changed"):
        evo.freeze(
            candidate.registration_id, {}, derived_controls_version=CONTROL_VERSION
        )


@pytest.mark.parametrize("episode", ["n4", "n7"])
def test_planned_episode_cannot_supply_multiple_selectable_incidents(prepared, episode):
    store, evo, _, create, candidate, plan = prepared
    selection.seal(store, candidate.registration_id, plan=plan)
    first, replacement = create("first"), create("replacement")
    evo.bind_episode(first.incident_id, episode)
    evo.bind_episode(first.incident_id, episode)  # exact idempotent retry
    with pytest.raises(ValueError, match="one immutable incident"):
        evo.bind_episode(replacement.incident_id, episode)


def test_global_lock_rejects_other_environment_candidate(prepared):
    store, _, _, _, candidate, plan = prepared
    selection.seal(store, candidate.registration_id, plan=plan)
    raw = candidate.model_dump(mode="json", exclude={"compiled_sha256"})
    raw.update(
        environment_id="another-environment", registration_id="another-candidate"
    )
    other = CompiledKnowledge.model_validate({**raw, "compiled_sha256": sha(raw)})
    with store.connect() as c:
        with pytest.raises(ValueError, match="globally selected"):
            selection.require_frozen_identity(c, other, {}, CONTROL_VERSION)


def test_holdout_cannot_backdate_collection_and_expired_window_rejected(prepared):
    store, _, _, create, candidate, plan = prepared
    expired = deepcopy(plan)
    expired["time_range"] = {
        "start": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        "end": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
    }
    with pytest.raises(ValueError, match="already ended"):
        selection.seal(store, candidate.registration_id, plan=expired)
    selection.seal(store, candidate.registration_id, plan=plan)
    fresh = create("backdated-observation")
    backdated = fresh.model_copy(
        update={"started_at": datetime.now(UTC) - timedelta(hours=1)}
    )
    with pytest.raises(ValueError, match="time range"):
        split.bind_episode(store, backdated, "n4")


def test_evaluator_change_invalidates_lock(prepared, monkeypatch):
    from ecomsre.product.knowledge import evolution_v050

    store, _, _, _, candidate, plan = prepared
    selection.seal(store, candidate.registration_id, plan=plan)
    monkeypatch.setattr(
        evolution_v050, "evaluation_bindings", lambda: {"changed": "digest"}
    )
    with store.connect() as c:
        with pytest.raises(ValueError, match="identity changed"):
            selection.require_frozen_identity(c, candidate, {}, CONTROL_VERSION)
