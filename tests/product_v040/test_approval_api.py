"""Real SQLite/CAS API authorization and persistence regressions; no live adapter."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from ecomsre.product.app import create_app
from ecomsre.product.remediation.repository import RemediationRepositoryV1
from ecomsre.product.settings import ProductSettingsV1
from tests.product_v040.test_candidates import (
    NOW,
    material as material,
    persist_material,
)


TOKEN = "offline-test-token"
APPROVAL = {
    "approver": "LOCAL_OPERATOR",
    "authorization_source": "USER_EXPLICIT_OPERATOR_AUTHORIZATION",
    "decision": "APPROVE",
    "scope": {
        "runbook_id": "ROLLBACK_CONFIGURATION",
        "target_logical_service": "payment",
        "maximum_forward_steps": 1,
    },
    "ttl_seconds": 120,
}


def headers(key="request-1", *, token=TOKEN):
    return {"Authorization": "Bearer " + token, "Idempotency-Key": key}


@pytest.fixture
def api(material, monkeypatch, tmp_path):
    persist_material(material)
    monkeypatch.setenv("ECOMSRE_ADMIN_TOKEN", TOKEN)
    settings = ProductSettingsV1(data_root=tmp_path)
    app = create_app(settings)
    now = [NOW]
    app.state.remediation.clock = lambda: now[0]
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, app, material, now


def candidate(api, key="candidate-1"):
    client, _, source, _ = api
    response = client.post(
        f"/v1/incidents/{source['incident'].incident_id}/remediation-candidates",
        headers=headers(key),
    )
    assert response.status_code == 200, response.text
    return response.json()["candidates"][0]


def approve(api, item, key="approval-1", payload=None):
    return api[0].post(
        f"/v1/remediation-candidates/{item['candidate_id']}/approvals",
        json=payload or APPROVAL,
        headers=headers(key),
    )


def test_candidate_get_is_read_only_and_creation_survives_restart(api):
    client, app, source, _ = api
    uri = f"/v1/incidents/{source['incident'].incident_id}/remediation-candidates"
    with app.state.store.connect() as connection:
        before = connection.execute(
            "SELECT count(*) FROM remediation_candidates"
        ).fetchone()[0]
    projection = client.get(uri)
    assert projection.status_code == 200
    with app.state.store.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM remediation_candidates"
            ).fetchone()[0]
            == before
        )
    item = candidate(api)
    assert projection.json()["candidates"] == [item]
    restarted = RemediationRepositoryV1(app.state.store, app.state.object_store)
    assert restarted.get_candidate(item["candidate_id"]).model_dump(mode="json") == item
    assert (
        restarted.create_candidates(
            source["incident"].incident_id, "candidate-1"
        ).model_dump(mode="json")
        == projection.json()
    )
    assert (
        client.get(f"/v1/remediation-candidates/{item['candidate_id']}").json() == item
    )
    assert item["action_authority"] == "NONE" and item["executable"] is False


@pytest.mark.parametrize(
    "path,body",
    [
        ("/v1/incidents/inc-" + "5" * 24 + "/remediation-candidates", None),
        ("/v1/remediation-candidates/cand-" + "0" * 24 + "/approvals", APPROVAL),
        (
            "/v1/remediation-candidates/cand-" + "0" * 24 + "/revocations",
            {"approval_id": "appr-" + "0" * 24},
        ),
    ],
)
@pytest.mark.parametrize("configured", [True, False])
def test_all_mutations_require_token_even_loopback(
    api, monkeypatch, path, body, configured
):
    client, app, _, _ = api
    if not configured:
        monkeypatch.delenv("ECOMSRE_ADMIN_TOKEN")
        app.state.settings = ProductSettingsV1(data_root=app.state.settings.data_root)
    for auth in ({"Idempotency-Key": "unauth"}, headers("unauth", token="wrong")):
        response = client.post(path, json=body, headers=auth)
        assert response.status_code == (401 if configured else 403)
        assert response.json()["error"]["code"] == (
            "AUTH_REQUIRED" if configured else "REMEDIATION_ADMIN_REQUIRED"
        )
    with app.state.store.connect() as connection:
        for name in (
            "remediation_candidates",
            "remediation_approvals",
            "remediation_revocations",
            "remediation_idempotency_keys",
        ):
            assert connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0] == 0


def test_approval_idempotency_expiry_and_revocation_are_durable(api):
    client, app, _, now = api
    item = candidate(api)
    response = approve(api, item)
    assert response.status_code == 200, response.text
    approval = response.json()
    uri = "/v1/remediation-approvals/" + approval["approval_id"]
    assert client.get(uri).json()["status"] == "ACTIVE"
    now[0] += timedelta(seconds=120)
    assert client.get(uri).json()["status"] == "EXPIRED"
    assert approve(api, item).json() == approval
    assert (
        approve(api, item, payload={**APPROVAL, "ttl_seconds": 121}).status_code == 409
    )
    revocation_uri = f"/v1/remediation-candidates/{item['candidate_id']}/revocations"
    revoked = client.post(
        revocation_uri,
        json={"approval_id": approval["approval_id"]},
        headers=headers("revoke-1"),
    )
    assert revoked.status_code == 200, revoked.text
    assert (
        client.post(
            revocation_uri,
            json={"approval_id": approval["approval_id"]},
            headers=headers("revoke-1"),
        ).json()
        == revoked.json()
    )
    assert (
        client.post(
            revocation_uri,
            json={"approval_id": approval["approval_id"]},
            headers=headers("revoke-2"),
        ).json()
        == revoked.json()
    )
    restarted = RemediationRepositoryV1(
        app.state.store, app.state.object_store, clock=lambda: now[0]
    )
    status = restarted.approval_status(approval["approval_id"])
    assert status.status == "REVOKED"
    assert status.approval.model_dump(mode="json") == approval
    assert approval["revoked_at"] is None and approval["single_use"] is True
    assert approval["action_authority"] == "NONE"


def test_conflicting_key_across_candidates_and_unknown_binding(api):
    client, _, source, _ = api
    item = candidate(api)
    conflict = client.post(
        "/v1/incidents/inc-" + "0" * 24 + "/remediation-candidates",
        headers=headers("candidate-1"),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    response = approve(api, item)
    bad = {"candidate_id": "cand-" + "0" * 24}
    assert approve(api, bad).status_code == 409
    assert approve(api, bad, key="new").status_code == 404
    revoke = client.post(
        f"/v1/remediation-candidates/{bad['candidate_id']}/revocations",
        json={"approval_id": response.json()["approval_id"]},
        headers=headers("r"),
    )
    assert revoke.status_code == 409
    assert source["diagnosis"].agent_writes == 0


@pytest.mark.parametrize(
    "patch",
    [
        {"ttl_seconds": 0},
        {"ttl_seconds": 601},
        {"ttl_seconds": True},
        {"decision": "EXECUTE"},
        {"approver": "/private/token-secret"},
        {"command": "danger"},
        {"scope": {"target_logical_service": "checkout"}},
        {"scope": {"parameters_sha256": "0" * 64}},
    ],
)
def test_approval_closed_request_and_safe_errors(api, patch):
    item = candidate(api)
    response = approve(api, item, payload={**APPROVAL, **patch})
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": "The request does not satisfy the Product API contract.",
            "details": {},
        }
    }


def test_concurrent_same_key_creates_one_approval(api):
    from ecomsre.product.remediation.approval import ApprovalRequestV1

    _, app, _, _ = api
    item = candidate(api)
    request = ApprovalRequestV1.model_validate(APPROVAL)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(
                lambda _: app.state.remediation.approve(
                    item["candidate_id"], request, "concurrent"
                ),
                range(12),
            )
        )
    assert len({entry.approval_id for entry in results}) == 1
    with app.state.store.connect() as connection:
        assert (
            connection.execute("SELECT count(*) FROM remediation_approvals").fetchone()[
                0
            ]
            == 1
        )
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert (
            connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[
                0
            ]
            == 9
        )


def test_transaction_rollback_does_not_reserve_key(api):
    client, app, _, _ = api
    missing = {"candidate_id": "cand-" + "0" * 24}
    assert approve(api, missing, key="reusable-after-rollback").status_code == 404
    item = candidate(api)
    assert approve(api, item, key="reusable-after-rollback").status_code == 200
    with app.state.store.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO remediation_approvals VALUES ('bad','missing','hash','{}')"
            )


def test_migration_rejects_tampered_table_and_leaves_base_ready(api):
    _, app, _, _ = api
    with app.state.store.connect() as connection:
        connection.execute(
            "ALTER TABLE remediation_approvals ADD COLUMN arbitrary TEXT"
        )
    with pytest.raises(RuntimeError, match="table identity differs"):
        RemediationRepositoryV1(app.state.store, app.state.object_store)
    assert app.state.store.ready()


def test_public_response_contains_only_closed_projections_and_no_execution(api):
    client, app, _, _ = api
    item = candidate(api)
    approval = approve(api, item).json()
    status = client.get("/v1/remediation-approvals/" + approval["approval_id"]).json()
    serialized = json.dumps([item, approval, status])
    for forbidden in (
        TOKEN,
        str(app.state.settings.data_root),
        "http://",
        "docker",
        "traceback",
    ):
        assert forbidden not in serialized.lower()
    for route in (
        f"/v1/remediation-candidates/{item['candidate_id']}/attempts",
        "/execute-shell",
        "/execute-command",
        "/write-url",
    ):
        assert client.post(route, headers=headers()).status_code == 404


def test_active_gate_rejects_expired_revoked_future_and_mismatched(api):
    from ecomsre.product.errors import ProductError

    client, app, _, now = api
    item = candidate(api)
    approval = approve(api, item).json()
    aid, cid = approval["approval_id"], item["candidate_id"]
    with app.state.store.connect() as connection:
        with pytest.raises(ProductError, match="safely") as error:
            app.state.remediation.require_active_approval(connection, aid, cid)
        assert error.value.code == "REMEDIATION_TRANSACTION_REQUIRED"
        connection.execute("BEGIN IMMEDIATE")
        assert (
            app.state.remediation.require_active_approval(
                connection, aid, cid
            ).approval_id
            == aid
        )
        with pytest.raises(ProductError) as error:
            app.state.remediation.require_active_approval(
                connection, aid, "cand-" + "0" * 24
            )
        assert error.value.code == "REMEDIATION_APPROVAL_BINDING_MISMATCH"
        now[0] -= timedelta(seconds=1)
        with pytest.raises(ProductError) as error:
            app.state.remediation.require_active_approval(connection, aid, cid)
        assert error.value.code == "REMEDIATION_APPROVAL_NOT_YET_VALID"
        now[0] += timedelta(seconds=121)
        with pytest.raises(ProductError) as error:
            app.state.remediation.require_active_approval(connection, aid, cid)
        assert error.value.code == "REMEDIATION_APPROVAL_EXPIRED"
        connection.execute("ROLLBACK")
    client.post(
        f"/v1/remediation-candidates/{cid}/revocations",
        json={"approval_id": aid},
        headers=headers("revoke"),
    )
    with app.state.store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(ProductError) as error:
            app.state.remediation.require_active_approval(connection, aid, cid)
        assert error.value.code == "REMEDIATION_APPROVAL_REVOKED"


@pytest.mark.parametrize("second_ttl", [120, 300])
def test_swapped_valid_cached_approval_is_rejected(api, second_ttl):
    from hashlib import sha256
    from ecomsre.product.remediation.repository import canonical

    client, app, _, _ = api
    item = candidate(api)
    first = approve(api, item, key="first").json()
    second = approve(
        api, item, key="second", payload={**APPROVAL, "ttl_seconds": second_ttl}
    ).json()
    with app.state.store.connect() as connection:
        connection.execute(
            "UPDATE remediation_idempotency_keys SET response_json = ? WHERE operation = 'approval' AND key_sha256 = ?",
            (
                json.dumps(
                    second, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ),
                sha256(b"first").hexdigest(),
            ),
        )
    response = approve(api, item, key="first")
    assert response.status_code == 409
    assert (
        response.json()["error"]["code"] == "REMEDIATION_IDEMPOTENCY_BINDING_MISMATCH"
    )
    assert (
        client.get("/v1/remediation-approvals/" + first["approval_id"]).json()[
            "approval"
        ]
        == first
    )
    assert canonical(
        app.state.remediation.approval_status(first["approval_id"]).approval
    )


@pytest.mark.parametrize(
    "table,column,value",
    [
        ("remediation_candidates", "candidate_sha256", "0" * 64),
        ("remediation_candidates", "incident_id", "inc-" + "0" * 24),
        ("remediation_approvals", "approval_sha256", "0" * 64),
    ],
)
def test_persisted_index_and_payload_mismatch_is_rejected(api, table, column, value):
    client, app, _, _ = api
    item = candidate(api)
    approval = approve(api, item).json()
    with app.state.store.connect() as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(f"UPDATE {table} SET {column} = ?", (value,))
    response = client.get("/v1/remediation-approvals/" + approval["approval_id"])
    assert response.status_code == 409
    assert response.json()["error"]["details"] == {}


def test_existing_revocation_parent_is_checked_on_new_key(api):
    client, app, _, _ = api
    item = candidate(api)
    first = approve(api, item, key="first").json()
    second = approve(api, item, key="second").json()
    uri = f"/v1/remediation-candidates/{item['candidate_id']}/revocations"
    first_revocation = client.post(
        uri, json={"approval_id": first["approval_id"]}, headers=headers("r1")
    ).json()
    client.post(uri, json={"approval_id": second["approval_id"]}, headers=headers("r2"))
    with app.state.store.connect() as connection:
        connection.execute(
            "UPDATE remediation_revocations SET payload_json = ? WHERE approval_id = ?",
            (json.dumps(first_revocation), second["approval_id"]),
        )
    response = client.post(
        uri, json={"approval_id": second["approval_id"]}, headers=headers("r3")
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REMEDIATION_REVOCATION_BINDING_MISMATCH"
