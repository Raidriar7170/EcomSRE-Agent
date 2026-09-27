"""Fixture-only promotion eligibility; real historical rows are never rewritten."""

import json

import pytest

from test_final_closure import closure, admit  # noqa: F401
from ecomsre.product.connectors.fixture import FixtureConnectorV1
from ecomsre.product.errors import ProductError
from ecomsre.product.knowledge.control_qualification_v050 import VERSION, qualify


def prepared(c):
    candidate, _ = admit(c)
    c.runner.develop(candidate)
    c.runner.select(candidate)
    return candidate


def test_n5_false_with_open_world_cannot_validate_or_promote(closure, monkeypatch):  # noqa: F811
    c = closure
    candidate = prepared(c)
    c.episode("N4", "positive")
    original = FixtureConnectorV1._records

    def residual(self, **kw):
        rows = original(self, **kw)
        if kw["current_observation"] and kw["source"].value == "METRICS":
            rows = tuple(
                r.model_copy(update={"value": 100000.0})
                if r.service == "payment" and r.metric_kind.value == "LATENCY_P95_MS"
                else r
                for r in rows
            )
        return rows

    with monkeypatch.context() as patch:
        patch.setattr(FixtureConnectorV1, "_records", residual)
        n5 = c.episode("N5", "healthy")
    diagnosis = c.evo.knowledge._diagnosis(n5)
    assert diagnosis.terminal.value == "OPEN_WORLD"
    c.episode("N6", "core")
    result = c.runner.evaluate_and_promote(candidate)
    assert not result.gate_passed
    assert "ORIGINAL_CONTROL_UNQUALIFIED" in result.reason_codes
    with c.evo.store.connect() as conn:
        row = dict(
            conn.execute(
                "SELECT * FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
                (candidate.registration_id,),
            ).fetchone()
        )
        audit = json.loads(
            conn.execute(
                "SELECT payload_json FROM knowledge_shadow_details_v050 WHERE registration_id=?",
                (candidate.registration_id,),
            ).fetchone()[0]
        )
    assert row["state"] == "REJECTED"
    assert audit["mechanical_shadow"]["gate_passed"] is True
    assert (
        next(x for x in audit["raw_details"] if x["case_id"] == n5)["raw_result"][
            "status"
        ]
        == "FALSE"
    )
    q = audit["control_qualifications"][n5]
    assert not q["qualified"] and q["normal_terminal"] == "OPEN_WORLD"
    assert q["planned_stratum"] == "NO_INCIDENT" and not q["candidate_match_used"]
    assert q["bindings"]["diagnosis_sha256"] == diagnosis.result_sha256
    assert q["strong_anomalies"]
    assert c.evo.knowledge._diagnosis(n5) == diagnosis
    # Reproduce the old bug's only-promote-state-and-numeric-gate inputs in this
    # temporary fixture DB. The public entry must still reject the actual role.
    with c.evo.store.connect() as conn:
        conn.execute(
            "UPDATE knowledge_candidate_pool_v050 SET state='VALIDATED',evaluation_json=? WHERE registration_id=?",
            (json.dumps(audit["mechanical_shadow"]), candidate.registration_id),
        )
    with pytest.raises(ProductError, match="qualification"):
        c.evo.promote(candidate.registration_id)
    with c.evo.store.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM environment_extension_registry_versions"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "damage",
    ["missing_protocol", "missing_audit", "changed_qualification", "changed_candidate"],
)
def test_direct_promotion_rechecks_frozen_eligibility(closure, damage):  # noqa: F811
    c = closure
    candidate = prepared(c)
    cases = {
        c.episode(n, k): s
        for n, k, s in (
            ("N4", "positive", "POSITIVE_INCIDENT"),
            ("N5", "healthy", "NO_INCIDENT"),
            ("N6", "core", "CONFUSABLE_CORE_KNOWN"),
        )
    }
    from ecomsre.product.knowledge.shadow_controls_v050 import CONTROL_VERSION

    c.evo.freeze(
        candidate.registration_id, cases, derived_controls_version=CONTROL_VERSION
    )
    assert c.evo.evaluate(candidate.registration_id).gate_passed
    with c.evo.store.connect() as conn:
        row = conn.execute(
            "SELECT freeze_json FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
            (candidate.registration_id,),
        ).fetchone()
        manifest = json.loads(row[0])
        assert manifest["control_qualification_version"] == VERSION
        if damage == "missing_protocol":
            del manifest["control_qualification_version"]
        elif damage == "missing_audit":
            conn.execute(
                "DELETE FROM knowledge_shadow_details_v050 WHERE registration_id=?",
                (candidate.registration_id,),
            )
        elif damage == "changed_candidate":
            manifest["candidate_sha256"] = "0" * 64
        else:
            manifest["control_qualifications"][next(iter(cases))]["bindings"][
                "runtime_input_sha256"
            ] = "0" * 64
        conn.execute(
            "UPDATE knowledge_candidate_pool_v050 SET freeze_json=? WHERE registration_id=?",
            (json.dumps(manifest), candidate.registration_id),
        )
    with pytest.raises(ProductError, match="qualification"):
        c.evo.promote(candidate.registration_id)
    assert not c.evo.knowledge.active_investigation_extensions(c.runner.environment_id)


def test_pure_qualification_rejects_wrong_roles_binding_and_missing_coverage(closure):  # noqa: F811
    c = closure
    candidate = prepared(c)
    iid = c.episode("N5", "healthy")
    m = c.evo.knowledge._shadow_runtime_material(iid)
    d = c.evo.knowledge._diagnosis(iid)
    ev = c.evo.knowledge._evidence(iid, d.diagnosis_id)
    from ecomsre.product.knowledge.candidates_v050 import snapshot_observations

    snapshots = {
        o.action_id: o.payload for o in ev.objects if "connector_result" in o.payload
    }
    obs = snapshot_observations(snapshots.values(), m.runtime_input.memory)
    kw = dict(
        target=candidate.proposal.target,
        incident=m.incident,
        diagnosis=d,
        runtime=m.runtime_input,
        observations=obs,
        evidence_objects=[o.object_sha256 for o in ev.objects],
    )
    healthy = qualify(stratum="NO_INCIDENT", **kw)
    assert healthy["qualified"]
    for stratum in ("CONFUSABLE_CORE_KNOWN", "POSITIVE_INCIDENT"):
        assert not qualify(stratum=stratum, **kw)["qualified"]
    assert not qualify(
        stratum="NO_INCIDENT",
        **(kw | {"observations": [o for o in obs if o["source"] != "METRICS"]}),
    )["qualified"]
    with pytest.raises(ValueError, match="incident binding"):
        qualify(
            stratum="NO_INCIDENT",
            **(
                kw
                | {"diagnosis": d.model_copy(update={"incident_id": "another-event"})}
            ),
        )


def test_legacy_frozen_evaluation_preserves_entire_row(closure):  # noqa: F811
    c = closure
    candidate = prepared(c)
    cases = {
        c.episode(n, k): s
        for n, k, s in (
            ("N4", "positive", "POSITIVE_INCIDENT"),
            ("N5", "healthy", "NO_INCIDENT"),
            ("N6", "core", "CONFUSABLE_CORE_KNOWN"),
        )
    }
    from ecomsre.product.knowledge.shadow_controls_v050 import CONTROL_VERSION

    c.evo.freeze(
        candidate.registration_id, cases, derived_controls_version=CONTROL_VERSION
    )
    with c.evo.store.connect() as conn:
        manifest = json.loads(
            conn.execute(
                "SELECT freeze_json FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
                (candidate.registration_id,),
            ).fetchone()[0]
        )
        del manifest["control_qualification_version"]
        conn.execute(
            "UPDATE knowledge_candidate_pool_v050 SET freeze_json=? WHERE registration_id=?",
            (json.dumps(manifest), candidate.registration_id),
        )
        before = dict(
            conn.execute(
                "SELECT * FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
                (candidate.registration_id,),
            ).fetchone()
        )
    with pytest.raises(ValueError, match="qualification protocol"):
        c.evo.evaluate(candidate.registration_id)
    with c.evo.store.connect() as conn:
        after = dict(
            conn.execute(
                "SELECT * FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
                (candidate.registration_id,),
            ).fetchone()
        )
    assert after == before
