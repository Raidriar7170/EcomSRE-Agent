"""Same-environment fixture successor; no real deployment equivalence claimed."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from ecomsre.product.app import create_app
from ecomsre.product.environment.capabilities import CapabilityMatrixRepositoryV1
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.jobs.worker import run_one_job
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.knowledge import capability_successor_v050 as successor
from test_investigation import prepare


@pytest.fixture
def deployment_pair(tmp_path):
    settings = ProductSettingsV1(data_root=tmp_path)
    with TestClient(create_app(settings)) as client:
        iid = prepare(client, settings)
        app = client.app
        incident = app.state.incidents.get(iid)
        caps = CapabilityMatrixRepositoryV1(app.state.store)
        old = caps.get(incident.environment_id)
        environment = EnvironmentRepositoryV1(app.state.store).get(
            incident.environment_id
        )
        client.post(f"/v1/environments/{incident.environment_id}/verify-jobs")
        assert run_one_job(settings, worker_id="fixture-reverify")
        new = caps.get(incident.environment_id)
        before = dict(
            mode="FIXTURE",
            deployment_id="fixture-old",
            environment=environment.model_dump(mode="json"),
            resource_births={"fixture": "old-birth"},
            runtime_binding={},
            images={"fixture": "capture-c2aa"},
            service_mapping={"payment": "payment"},
            actual_queries={"fixture": "capture-c2aa"},
            units={"cpu_percent": "PERCENT"},
            windows_and_sampling={"resource_window": 10, "sampling": 10},
            resource_limits={"maximum_samples": 5},
            trust_boundary={"scope": "fixture-only"},
            connector_semantics={"version": "fixture-v1"},
        )
        after = deepcopy(before)
        after.update(
            deployment_id="fixture-new", resource_births={"fixture": "new-birth"}
        )
        yield app, incident, old, new, before, after


def install(pair):
    app, _, old, new, before, after = pair
    return successor.install(
        app.state.store, old=old, new=new, old_deployment=before, new_deployment=after
    )


def test_timestamp_successor_preserves_raw_identity_and_historical_matrix(
    deployment_pair,
):
    app, incident, old, new, before, after = deployment_pair
    store = app.state.store
    args = dict(
        environment_id=old.environment_id,
        candidate_environment_id=old.environment_id,
        expected=old.capability_sha256,
        actual=new.capability_sha256,
    )
    assert not successor.admits(store, **args)
    digest = install(deployment_pair)
    assert install(deployment_pair) == digest
    assert successor.admits(store, **args)
    assert not successor.admits(store, **(args | {"actual": "0" * 64}))
    assert not successor.admits(store, **(args | {"environment_id": "env-" + "0" * 24}))
    assert app.state.knowledge._capability_matrix(incident) == old
    assert (
        app.state.incidents.get(incident.incident_id).source_capability_sha256
        == old.capability_sha256
    )
    with store.connect() as c:
        value = successor.load(c, old.environment_id)
        assert value["old"] == old.model_dump(mode="json")
        assert value["new"] == new.model_dump(mode="json")
        assert (
            value["old_deployment"]["resource_births"]
            != value["new_deployment"]["resource_births"]
        )
    changed = deepcopy(after)
    changed["deployment_id"] = "third"
    with pytest.raises(ValueError, match="immutable"):
        successor.install(
            store, old=old, new=new, old_deployment=before, new_deployment=changed
        )


@pytest.mark.parametrize(
    "field",
    [
        "images",
        "service_mapping",
        "actual_queries",
        "units",
        "windows_and_sampling",
        "resource_limits",
        "trust_boundary",
        "connector_semantics",
    ],
)
def test_changed_semantics_never_gain_timestamp_exemption(deployment_pair, field):
    _, _, old, new, before, after = deployment_pair
    after[field]["changed"] = "not-equivalent"
    with pytest.raises(ValueError, match="semantics differ"):
        successor.validate_pair(old, new, before, after)


def test_unknown_capability_and_later_reverify_do_not_use_mapping(deployment_pair):
    app, _, old, new, _, _ = deployment_pair
    install(deployment_pair)
    raw = new.model_dump(mode="json", exclude={"capability_sha256"})
    raw["no_incident_eligible"] = not raw["no_incident_eligible"]
    altered = {**raw, "capability_sha256": successor.sha(raw)}
    with pytest.raises(ValueError, match="beyond verified_at"):
        successor.validate_pair(old, altered, deployment_pair[4], deployment_pair[5])
    # A third verified identity is not transitively admitted.
    from datetime import timedelta

    raw = new.model_dump(mode="json", exclude={"capability_sha256"})
    raw["verified_at"] = new.model_copy(
        update={"verified_at": new.verified_at + timedelta(seconds=1)}
    ).model_dump(mode="json")["verified_at"]
    third = {**raw, "capability_sha256": successor.sha(raw)}
    from ecomsre.product.environment.capabilities import EnvironmentCapabilityMatrixV1

    CapabilityMatrixRepositoryV1(app.state.store).put(
        EnvironmentCapabilityMatrixV1.model_validate(third)
    )
    assert not successor.admits(
        app.state.store,
        environment_id=old.environment_id,
        candidate_environment_id=old.environment_id,
        expected=old.capability_sha256,
        actual=new.capability_sha256,
    )

    for digest in (old.capability_sha256, new.capability_sha256):
        assert not successor.admits(
            app.state.store,
            environment_id=old.environment_id,
            candidate_environment_id=old.environment_id,
            expected=digest,
            actual=digest,
        )


def test_same_digest_does_not_bypass_environment_drift(deployment_pair):
    app, _, old, new, _, _ = deployment_pair
    install(deployment_pair)
    with app.state.store.connect() as c:
        c.execute(
            "UPDATE environments SET description='changed' WHERE environment_id=?",
            (old.environment_id,),
        )
    for expected, actual in [
        (old.capability_sha256, old.capability_sha256),
        (new.capability_sha256, new.capability_sha256),
        (old.capability_sha256, new.capability_sha256),
    ]:
        assert not successor.admits(
            app.state.store,
            environment_id=old.environment_id,
            candidate_environment_id=old.environment_id,
            expected=expected,
            actual=actual,
        )
