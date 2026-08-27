from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ecomsre.product.app import create_app
from ecomsre.product.contracts import EnvironmentCreateV1
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.errors import ProductError
from ecomsre.product.jobs.contracts import ProductJobStatusV1, ProductJobTypeV1
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.jobs.worker import run_one_job
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.storage.object_store import (
    ContentAddressedObjectStoreV1,
    ObjectStoreIntegrityError,
)
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


def _settings(tmp_path: Path) -> ProductSettingsV1:
    return ProductSettingsV1(
        data_root=tmp_path,
        sqlite_path=tmp_path / "product.sqlite3",
        object_store_root=tmp_path / "objects",
    )


def _environment_payload(name: str = "fixture-dev") -> dict[str, object]:
    return {
        "name": name,
        "description": "fixture-backed development environment",
        "timezone": "UTC",
        "service_identity_policy": {"canonical_field": "service.name"},
        "connector_configs": [
            {
                "name": "fixture",
                "kind": "FIXTURE",
                "endpoint": None,
                "settings": {"dataset": "product-increment-1"},
                "credential_refs": {},
            }
        ],
        "explicit_service_catalog": ["frontend", "payment"],
    }


def test_settings_default_to_loopback_without_admin_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECOMSRE_ADMIN_TOKEN", raising=False)

    settings = ProductSettingsV1()

    assert settings.api_host == "127.0.0.1"
    assert settings.resolved_admin_token() is None
    with pytest.raises(ValidationError, match="non-loopback"):
        ProductSettingsV1(api_host="0.0.0.0")


def test_connector_settings_reject_secret_bearing_fields() -> None:
    payload = _environment_payload()
    payload["connector_configs"][0]["settings"] = {"api_token": "raw-secret"}

    with pytest.raises(ValidationError):
        EnvironmentCreateV1.model_validate(payload)

    for settings in (
        {"auth": {"bearer": "raw-secret"}},
        {"headers": {"X-Custom-Auth": "raw-secret"}},
    ):
        payload = _environment_payload()
        payload["connector_configs"][0]["settings"] = settings
        with pytest.raises(ValidationError):
            EnvironmentCreateV1.model_validate(payload)

    payload = _environment_payload()
    payload["connector_configs"][0]["endpoint"] = "https://user:secret@example.test"
    with pytest.raises(ValidationError, match="userinfo"):
        EnvironmentCreateV1.model_validate(payload)

    payload = _environment_payload()
    payload["connector_configs"][0]["endpoint"] = (
        "https://example.test/api?access_token=raw-secret"
    )
    with pytest.raises(ValidationError, match="secret query"):
        EnvironmentCreateV1.model_validate(payload)

    payload = _environment_payload()
    payload["connector_configs"][0]["credential_refs"] = {"token": "env:../bad"}
    with pytest.raises(ValidationError, match="environment credential"):
        EnvironmentCreateV1.model_validate(payload)


def test_auth_snapshot_cannot_fail_open_after_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECOMSRE_ADMIN_TOKEN", "startup-secret")
    settings = ProductSettingsV1(
        data_root=tmp_path,
        api_host="0.0.0.0",
    )
    app = create_app(settings)
    monkeypatch.delenv("ECOMSRE_ADMIN_TOKEN")

    with TestClient(app) as client:
        response = client.post("/v1/environments", json=_environment_payload())

    assert response.status_code == 401
    assert "startup-secret" not in response.text


def test_api_validation_and_internal_errors_use_safe_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECOMSRE_ADMIN_TOKEN", "test-secret")
    app = create_app(_settings(tmp_path))
    headers = {"Authorization": "Bearer test-secret"}
    invalid = _environment_payload()
    invalid["connector_configs"][0]["credential_refs"] = {
        "token": "raw-secret"
    }
    with TestClient(app, raise_server_exceptions=False) as client:
        validation = client.post(
            "/v1/environments",
            headers=headers,
            json=invalid,
        )
        assert validation.status_code == 422
        assert validation.json() == {
            "error": {
                "code": "INVALID_REQUEST",
                "message": "The request does not satisfy the Product API contract.",
                "details": {},
            }
        }
        assert "raw-secret" not in validation.text

        app.state.environments.create = lambda _payload: (_ for _ in ()).throw(
            RuntimeError("internal raw-secret")
        )
        internal = client.post(
            "/v1/environments",
            headers=headers,
            json=_environment_payload(),
        )
        assert internal.status_code == 500
        assert internal.json()["error"]["code"] == "INTERNAL_CONTRACT_FAILURE"
        assert "raw-secret" not in internal.text


def test_api_auth_environment_restart_persistence_and_openapi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECOMSRE_ADMIN_TOKEN", "test-secret")
    settings = _settings(tmp_path)

    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ready"}
        unauthenticated = client.post("/v1/environments", json=_environment_payload())
        assert unauthenticated.status_code == 401
        assert unauthenticated.json() == {
            "error": {
                "code": "AUTH_REQUIRED",
                "message": "A valid bearer token is required.",
                "details": {},
            }
        }
        created = client.post(
            "/v1/environments",
            headers={"Authorization": "Bearer test-secret"},
            json=_environment_payload(),
        )
        assert created.status_code == 201
        environment = created.json()
        environment_id = environment["environment_id"]
        assert environment_id.startswith("env-")
        assert environment["connector_configs"][0]["kind"] == "FIXTURE"
        assert "test-secret" not in json.dumps(environment)
        assert "/v1/environments" in client.get("/openapi.json").json()["paths"]

    with TestClient(create_app(settings)) as restarted:
        persisted = restarted.get(f"/v1/environments/{environment_id}")
        assert persisted.status_code == 200
        assert persisted.json() == environment
        assert restarted.get("/v1/environments").json()["items"] == [environment]


def test_sqlite_migration_wal_and_required_tables(tmp_path: Path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")

    with store.connect() as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert journal_mode.lower() == "wal"
    assert foreign_keys == 1
    assert {
        "schema_migrations",
        "environments",
        "connector_configs",
        "services",
        "baseline_versions",
        "incidents",
        "diagnosis_jobs",
        "job_events",
        "diagnosis_results",
        "evidence_objects",
        "incident_fingerprints",
        "fault_families",
        "fault_family_members",
        "human_reviews",
        "registration_drafts",
        "shadow_evaluations",
        "environment_extension_registrations",
        "promotion_records",
        "revocation_records",
    }.issubset(table_names)


def test_sqlite_rejects_newer_migration_and_readiness_checks_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "product.sqlite3"
    store = SqliteStoreV1(path)
    with store.connect() as connection:
        connection.execute("DROP TABLE incidents")
    assert store.ready() is False

    with store.connect() as connection:
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (999, ?, ?)",
            ("future", "2026-01-01T00:00:00+00:00"),
        )
    with pytest.raises(RuntimeError, match="newer schema version"):
        SqliteStoreV1(path)


def test_object_store_is_create_once_and_detects_corruption(tmp_path: Path) -> None:
    metadata_store = SqliteStoreV1(tmp_path / "product.sqlite3")
    object_store = ContentAddressedObjectStoreV1(
        tmp_path / "objects",
        metadata_store=metadata_store,
    )

    first = object_store.put_json({"service": "payment", "healthy": True})
    repeated = object_store.put_json({"healthy": True, "service": "payment"})

    assert first == repeated
    with metadata_store.connect() as connection:
        metadata = connection.execute(
            "SELECT object_sha256, byte_size, media_type, created_at "
            "FROM evidence_objects WHERE object_sha256 = ?",
            (first.object_sha256,),
        ).fetchone()
    assert metadata is not None
    assert tuple(metadata)[:3] == (
        first.object_sha256,
        first.byte_size,
        first.media_type,
    )
    assert metadata["created_at"]
    assert object_store.read_bytes(first.object_sha256) == first.path.read_bytes()
    first.path.write_bytes(b"corrupt")
    with pytest.raises(ObjectStoreIntegrityError, match="existing object bytes differ"):
        object_store.put_json({"service": "payment", "healthy": True})


def test_object_store_concurrent_identical_writers_publish_complete_bytes(
    tmp_path: Path,
) -> None:
    object_store = ContentAddressedObjectStoreV1(
        tmp_path / "objects",
        metadata_store=SqliteStoreV1(tmp_path / "product.sqlite3"),
    )
    payload = b"x" * 8_000_000
    barrier = Barrier(8)

    def write() -> str:
        barrier.wait()
        return object_store.put_bytes(
            payload,
            media_type="application/json",
        ).object_sha256

    with ThreadPoolExecutor(max_workers=8) as executor:
        digests = tuple(executor.map(lambda _index: write(), range(8)))

    assert len(set(digests)) == 1
    assert object_store.read_bytes(digests[0]) == payload
    assert not tuple(object_store.sha_root.rglob(".tmp-*"))


def test_object_store_fails_closed_on_conflicting_metadata_and_repairs_orphans(
    tmp_path: Path,
) -> None:
    metadata_store = SqliteStoreV1(tmp_path / "product.sqlite3")
    object_store = ContentAddressedObjectStoreV1(
        tmp_path / "objects",
        metadata_store=metadata_store,
    )
    payload = b'{"status":"ok"}'
    stored = object_store.put_bytes(payload, media_type="application/json")

    with pytest.raises(ObjectStoreIntegrityError, match="metadata differs"):
        object_store.put_bytes(payload, media_type="text/plain")

    with metadata_store.connect() as connection:
        connection.execute(
            "DELETE FROM evidence_objects WHERE object_sha256 = ?",
            (stored.object_sha256,),
        )
    with pytest.raises(ObjectStoreIntegrityError, match="metadata is missing"):
        object_store.read_bytes(stored.object_sha256)
    object_store.put_bytes(payload, media_type="application/json")
    assert object_store.read_bytes(stored.object_sha256) == payload

    stored.path.unlink()
    with pytest.raises(ObjectStoreIntegrityError, match="bytes are missing"):
        object_store.read_bytes(stored.object_sha256)
    repaired = object_store.put_bytes(payload, media_type="application/json")
    assert repaired == stored
    assert object_store.read_bytes(stored.object_sha256) == payload

    with metadata_store.connect() as connection:
        connection.execute(
            "UPDATE evidence_objects SET byte_size = byte_size + 1 "
            "WHERE object_sha256 = ?",
            (stored.object_sha256,),
        )
    with pytest.raises(ObjectStoreIntegrityError, match="metadata differs"):
        object_store.read_bytes(stored.object_sha256)


def test_job_lease_reclaim_and_idempotency(tmp_path: Path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    jobs = JobRepositoryV1(store)

    first = jobs.enqueue(
        ProductJobTypeV1.ENVIRONMENT_VERIFY,
        {"environment_id": "env-fixture", "fixture": True},
        idempotency_key="verify:env-fixture",
        now=100.0,
    )
    repeated = jobs.enqueue(
        ProductJobTypeV1.ENVIRONMENT_VERIFY,
        {"environment_id": "env-fixture", "fixture": True},
        idempotency_key="verify:env-fixture",
        now=101.0,
    )
    assert repeated.job_id == first.job_id

    claimed = jobs.claim_next("worker-a", lease_seconds=10, now=100.0)
    assert claimed is not None
    assert claimed.attempt_count == 1
    assert jobs.claim_next("worker-b", lease_seconds=10, now=105.0) is None

    reclaimed = jobs.claim_next("worker-b", lease_seconds=10, now=111.0)
    assert reclaimed is not None
    assert reclaimed.job_id == first.job_id
    assert reclaimed.attempt_count == 2
    jobs.succeed(
        reclaimed.job_id,
        "worker-b",
        reclaimed.attempt_count,
        {"verified": True},
        now=112.0,
    )

    completed = jobs.get(first.job_id)
    assert completed.status is ProductJobStatusV1.SUCCEEDED
    assert completed.result == {"verified": True}


def test_expired_worker_cannot_finish_without_reclaiming_lease(tmp_path: Path) -> None:
    jobs = JobRepositoryV1(SqliteStoreV1(tmp_path / "product.sqlite3"))
    job = jobs.enqueue(
        ProductJobTypeV1.ENVIRONMENT_VERIFY,
        {"environment_id": "env-fixture", "fixture": True},
        now=100.0,
    )
    assert jobs.claim_next("worker-a", lease_seconds=10, now=100.0) is not None

    with pytest.raises(ProductError, match="no longer owns"):
        jobs.succeed(job.job_id, "worker-a", 1, {"verified": True}, now=111.0)

    reclaimed = jobs.claim_next("worker-b", lease_seconds=10, now=111.0)
    assert reclaimed is not None
    jobs.succeed(
        job.job_id,
        "worker-b",
        reclaimed.attempt_count,
        {"verified": True},
        now=112.0,
    )


def test_job_idempotency_key_rejects_different_payload(tmp_path: Path) -> None:
    jobs = JobRepositoryV1(SqliteStoreV1(tmp_path / "product.sqlite3"))
    jobs.enqueue(
        ProductJobTypeV1.ENVIRONMENT_VERIFY,
        {"environment_id": "env-one", "fixture": True},
        idempotency_key="caller-request-1",
    )

    with pytest.raises(ProductError, match="different payload"):
        jobs.enqueue(
            ProductJobTypeV1.ENVIRONMENT_VERIFY,
            {"environment_id": "env-two", "fixture": True},
            idempotency_key="caller-request-1",
        )


def test_fixture_job_completes_and_survives_repository_restart(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = SqliteStoreV1(settings.sqlite_path)
    environments = EnvironmentRepositoryV1(store)
    environment = environments.create(_environment_payload(), now=100.0)
    jobs = JobRepositoryV1(store)
    job = jobs.enqueue(
        ProductJobTypeV1.ENVIRONMENT_VERIFY,
        {"environment_id": environment.environment_id, "fixture": True},
        idempotency_key=f"fixture:{environment.environment_id}",
        now=101.0,
    )

    assert run_one_job(settings, worker_id="fixture-worker", now=102.0)

    restarted_jobs = JobRepositoryV1(SqliteStoreV1(settings.sqlite_path))
    completed = restarted_jobs.get(job.job_id)
    assert completed.status is ProductJobStatusV1.SUCCEEDED
    assert completed.result == {
        "environment_id": environment.environment_id,
        "fixture_verified": True,
    }


def test_api_enqueues_worker_completes_and_api_polls_fixture_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECOMSRE_ADMIN_TOKEN", "test-secret")
    settings = _settings(tmp_path)
    headers = {"Authorization": "Bearer test-secret"}
    with TestClient(create_app(settings)) as client:
        environment = client.post(
            "/v1/environments",
            headers=headers,
            json=_environment_payload("fixture-worker-flow"),
        ).json()
        environment_id = environment["environment_id"]
        enqueued = client.post(
            f"/v1/environments/{environment_id}/verify-jobs",
            headers=headers,
        )
        assert enqueued.status_code == 202
        job_id = enqueued.json()["job_id"]
        assert enqueued.json()["status"] == "PENDING"

        assert run_one_job(settings, worker_id="api-fixture-worker", now=200.0)

        completed = client.get(f"/v1/jobs/{job_id}")
        assert completed.status_code == 200
        assert completed.json()["status"] == "SUCCEEDED"
        assert completed.json()["result"] == {
            "environment_id": environment_id,
            "fixture_verified": True,
        }

        fresh = client.post(
            f"/v1/environments/{environment_id}/verify-jobs",
            headers=headers,
        )
        assert fresh.status_code == 202
        assert fresh.json()["job_id"] != job_id

        idempotent_headers = {**headers, "Idempotency-Key": "explicit-request-1"}
        first = client.post(
            f"/v1/environments/{environment_id}/verify-jobs",
            headers=idempotent_headers,
        )
        repeated = client.post(
            f"/v1/environments/{environment_id}/verify-jobs",
            headers=idempotent_headers,
        )
        assert repeated.json()["job_id"] == first.json()["job_id"]


def test_readyz_returns_503_when_schema_is_not_ready(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    app.state.store.ready = lambda: False

    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "not-ready"}
