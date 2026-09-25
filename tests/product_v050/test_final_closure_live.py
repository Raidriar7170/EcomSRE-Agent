"""No Docker/Provider: exact Runtime identity rebinding persists or fails closed."""

from contextlib import contextmanager
import json
import sqlite3
from types import SimpleNamespace

import pytest

from test_final_closure import closure as closure, admit

from scripts.product_v050.final_closure_live import rebind_runtime


def test_runtime_rebinding_commits_only_two_fields_and_compare_swap(tmp_path):
    path = tmp_path / "test.sqlite3"
    old = {
        "snapshot_ref": "old.json",
        "authority_sha256": "old",
        "retained_policy": "exact",
    }
    new = dict(old, snapshot_ref="new.json", authority_sha256="new")
    with sqlite3.connect(path) as c:
        c.execute(
            "CREATE TABLE connector_configs(connector_config_id TEXT, environment_id TEXT, kind TEXT, settings_json TEXT)"
        )
        c.execute(
            "INSERT INTO connector_configs VALUES (?,?,?,?)",
            ("runtime", "env", "PILOT_RUNTIME", json.dumps(old)),
        )

    @contextmanager
    def connect():
        c = sqlite3.connect(path, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    store = SimpleNamespace(connect=connect)
    rebind_runtime(store, "env", old, new)
    with connect() as c:
        assert (
            json.loads(
                c.execute("SELECT settings_json FROM connector_configs").fetchone()[0]
            )
            == new
        )
    with pytest.raises(ValueError, match="COMPARE_AND_SWAP"):
        rebind_runtime(store, "env", old, new)
    with pytest.raises(ValueError, match="ONLY_IDENTITY"):
        rebind_runtime(store, "env", new, dict(new, retained_policy="loosened"))
    with connect() as c:
        assert (
            json.loads(
                c.execute("SELECT settings_json FROM connector_configs").fetchone()[0]
            )
            == new
        )


def test_ulimit_comparison_retains_every_named_limit_and_value():
    from scripts.product_v050.final_closure_live import resource_limits

    limits = [
        {"Name": "nofile", "Soft": 65536, "Hard": 65536},
        {"Name": "memlock", "Soft": -1, "Hard": -1},
    ]
    old = {"Memory": 123, "Ulimits": limits}
    new = {"Memory": 123, "Ulimits": list(reversed(limits))}
    assert resource_limits(old, old) == resource_limits(new, new)
    assert old["Ulimits"][0]["Name"] == "nofile"
    new["Ulimits"][0] = dict(new["Ulimits"][0], Hard=1)
    assert resource_limits(old, old) != resource_limits(new, new)
    with pytest.raises(ValueError, match="DUPLICATE_ULIMIT"):
        resource_limits({"Ulimits": [limits[0], limits[0]]}, ["Ulimits"])


@pytest.mark.parametrize("closure", [{"primary": "A"}, {"primary": "B"}], indirect=True)
def test_live_harness_recurrence_uses_governed_fixture_and_actual_matcher(
    closure, tmp_path, monkeypatch
):
    from scripts.product_v050 import final_closure_live as live

    c = closure
    candidate, _ = admit(c, level=c.plan["primary_level"])
    assert c.runner.develop(candidate)["passed"]
    c.runner.select(candidate)
    for slot, kind in (("N4", "positive"), ("N5", "healthy"), ("N6", "core")):
        c.episode(slot, kind)
    assert c.runner.evaluate_and_promote(candidate).gate_passed
    monkeypatch.setattr(live, "ROOT", tmp_path)
    monkeypatch.setattr(
        live,
        "collect",
        lambda campaign, runner, slot: c.episode(slot, supplement=False),
    )
    campaign = SimpleNamespace(
        app=c.app,
        env=c.runner.environment_id,
        evo=c.evo,
        repo=c.evo.investigations,
        client=c.client,
    )
    live.recurrence(campaign, c.runner)
    proof = json.loads((tmp_path / "recurrence-proof.json").read_text())
    assert proof["passed"] and proof["revocation_check"]
    assert proof["before"] == proof["after"]
    assert proof["capability_mismatch_check"] and proof["environment_mismatch_check"]
    assert proof["matched_bindings"][0]["candidate_sha256"] == candidate.compiled_sha256


def test_oom_kill_default_is_not_disabled_and_true_still_differs():
    from scripts.product_v050.live_environment import resource_limit_values

    assert resource_limit_values({"OomKillDisable": None}) == resource_limit_values(
        {"OomKillDisable": False}
    )
    assert resource_limit_values({"OomKillDisable": True}) != resource_limit_values(
        {"OomKillDisable": False}
    )


def test_incident_payload_prevalidates_canonical_ids_before_live_collection():
    from scripts.product_v050.final_closure_live import incident_payload
    from ecomsre.product.incidents.contracts import IncidentCreateV1
    from datetime import UTC, datetime

    campaign = SimpleNamespace(
        env="env-" + "1" * 24,
        service_ids={
            "checkout": "svc-" + "4" * 24,
            "fraud-detection": "svc-" + "2" * 24,
            "kafka": "svc-" + "3" * 24,
            "payment": "svc-" + "1" * 24,
        },
    )
    now = datetime.now(UTC).isoformat()
    payload = incident_payload(campaign, "opaque-episode", now, now)
    assert payload["candidate_service_ids"] == sorted(campaign.service_ids.values())
    IncidentCreateV1.model_validate(payload)
    payload["candidate_service_ids"].reverse()
    with pytest.raises(ValueError, match="not canonical"):
        IncidentCreateV1.model_validate(payload)


def test_proposer_stage_resumes_only_pending_selection(closure, monkeypatch):
    from scripts.product_v050 import final_closure_live as live
    from ecomsre.product.knowledge.selection_lock_v050 import load as selection

    c = closure
    candidate, _ = admit(c)
    assert c.runner.develop(candidate)["passed"]
    with c.app.state.store.connect() as connection:
        assert selection(connection) is None
    campaign = SimpleNamespace(
        app=c.app,
        repo=c.evo.investigations,
        client=SimpleNamespace(__exit__=lambda *_: None),
    )
    monkeypatch.setattr(live, "attach", lambda **_: campaign)
    monkeypatch.setattr(live, "runner_for", lambda _: c.runner)

    def forbidden(*args, **kwargs):
        raise AssertionError("no new proposal after development pass")

    monkeypatch.setattr(c.runner, "propose_next", forbidden)
    live.run_stage("propose")
    with c.app.state.store.connect() as connection:
        assert selection(connection)["registration_id"] == candidate.registration_id
