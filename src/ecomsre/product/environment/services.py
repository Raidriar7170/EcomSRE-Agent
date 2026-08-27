"""Persistent opaque-to-logical Product service identities."""

from __future__ import annotations

import json

from ecomsre.product.contracts import ServiceIdentityMapV1, ServiceIdentityV1
from ecomsre.product.errors import not_found
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


class ServiceCatalogRepositoryV1:
    def __init__(self, store: SqliteStoreV1) -> None:
        self.store = store

    def get_map(self, environment_id: str) -> ServiceIdentityMapV1:
        with self.store.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM environments WHERE environment_id = ?",
                (environment_id,),
            ).fetchone()
            if exists is None:
                raise not_found(
                    "ENVIRONMENT_NOT_FOUND",
                    "The requested environment does not exist.",
                )
            rows = connection.execute(
                """SELECT payload_json FROM services
                   WHERE environment_id = ? ORDER BY logical_service""",
                (environment_id,),
            ).fetchall()
        services = tuple(
            ServiceIdentityV1.model_validate(json.loads(row["payload_json"]))
            for row in rows
        )
        return ServiceIdentityMapV1.build(
            environment_id=environment_id,
            services=services,
        )

    def put_map(self, identity_map: ServiceIdentityMapV1, *, created_at: str) -> None:
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = {
                    row["logical_service"]: row["service_id"]
                    for row in connection.execute(
                        """SELECT service_id, logical_service FROM services
                           WHERE environment_id = ?""",
                        (identity_map.environment_id,),
                    ).fetchall()
                }
                for identity in identity_map.services:
                    prior_id = existing.get(identity.logical_service)
                    if prior_id is not None and prior_id != identity.service_id:
                        raise RuntimeError("service identity is not stable")
                    payload = json.dumps(
                        identity.model_dump(mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    connection.execute(
                        """INSERT INTO services(
                            service_id, environment_id, payload_json, created_at,
                            logical_service
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(service_id) DO UPDATE SET
                            payload_json = excluded.payload_json,
                            logical_service = excluded.logical_service""",
                        (
                            identity.service_id,
                            identity_map.environment_id,
                            payload,
                            created_at,
                            identity.logical_service,
                        ),
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise


__all__ = ("ServiceCatalogRepositoryV1",)
