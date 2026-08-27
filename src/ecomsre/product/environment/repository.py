"""SQLite-backed environment CRUD."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any, Mapping

from ecomsre.product.contracts import (
    ConnectorConfigV1,
    EnvironmentCreateV1,
    EnvironmentRecordV1,
    ServiceIdentityV1,
    ServiceSourceAliasesV1,
)
from ecomsre.product.errors import not_found
from ecomsre.product.ids import new_product_id
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


def _timestamp(now: float | None) -> str:
    if now is None:
        return datetime.now(UTC).isoformat()
    return datetime.fromtimestamp(now, UTC).isoformat()


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class EnvironmentRepositoryV1:
    def __init__(self, store: SqliteStoreV1) -> None:
        self.store = store

    def create(
        self,
        value: EnvironmentCreateV1 | Mapping[str, Any],
        *,
        now: float | None = None,
    ) -> EnvironmentRecordV1:
        request = (
            value
            if isinstance(value, EnvironmentCreateV1)
            else EnvironmentCreateV1.model_validate(value)
        )
        environment_id = new_product_id("env")
        created_at = _timestamp(now)
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """INSERT INTO environments(
                        environment_id, name, description, timezone,
                        service_identity_policy_json, explicit_service_catalog_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        environment_id,
                        request.name,
                        request.description,
                        request.timezone,
                        _json(request.service_identity_policy.model_dump(mode="json")),
                        _json(request.explicit_service_catalog),
                        created_at,
                        created_at,
                    ),
                )
                for connector in request.connector_configs:
                    connection.execute(
                        """INSERT INTO connector_configs(
                            connector_config_id, environment_id, name, kind, endpoint,
                            settings_json, credential_refs_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            new_product_id("conn"),
                            environment_id,
                            connector.name,
                            connector.kind.value,
                            connector.endpoint,
                            _json(connector.settings),
                            _json(connector.credential_refs),
                            created_at,
                        ),
                    )
                rules = {
                    item.logical_service: item
                    for item in request.service_identity_policy.services
                }
                logical_services = tuple(
                    sorted(set(request.explicit_service_catalog).union(rules))
                )
                for logical_service in logical_services:
                    identity = ServiceIdentityV1(
                        service_id=new_product_id("svc"),
                        logical_service=logical_service,
                        aliases=(
                            rules[logical_service].aliases
                            if logical_service in rules
                            else ServiceSourceAliasesV1()
                        ),
                    )
                    connection.execute(
                        """INSERT INTO services(
                            service_id, environment_id, payload_json, created_at,
                            logical_service
                        ) VALUES (?, ?, ?, ?, ?)""",
                        (
                            identity.service_id,
                            environment_id,
                            _json(identity.model_dump(mode="json")),
                            created_at,
                            logical_service,
                        ),
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self.get(environment_id)

    def get(self, environment_id: str) -> EnvironmentRecordV1:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM environments WHERE environment_id = ?",
                (environment_id,),
            ).fetchone()
            if row is None:
                raise not_found(
                    "ENVIRONMENT_NOT_FOUND",
                    "The requested environment does not exist.",
                )
            connector_rows = connection.execute(
                """SELECT name, kind, endpoint, settings_json, credential_refs_json
                   FROM connector_configs WHERE environment_id = ? ORDER BY name""",
                (environment_id,),
            ).fetchall()
        connectors = tuple(
            ConnectorConfigV1.model_validate(
                {
                    "name": connector["name"],
                    "kind": connector["kind"],
                    "endpoint": connector["endpoint"],
                    "settings": json.loads(connector["settings_json"]),
                    "credential_refs": json.loads(connector["credential_refs_json"]),
                }
            )
            for connector in connector_rows
        )
        return EnvironmentRecordV1(
            environment_id=row["environment_id"],
            name=row["name"],
            description=row["description"],
            timezone=row["timezone"],
            service_identity_policy=json.loads(row["service_identity_policy_json"]),
            connector_configs=connectors,
            explicit_service_catalog=tuple(
                json.loads(row["explicit_service_catalog_json"])
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list(self) -> tuple[EnvironmentRecordV1, ...]:
        with self.store.connect() as connection:
            ids = [
                row[0]
                for row in connection.execute(
                    "SELECT environment_id FROM environments "
                    "ORDER BY created_at, environment_id"
                ).fetchall()
            ]
        return tuple(self.get(environment_id) for environment_id in ids)


__all__ = ("EnvironmentRepositoryV1",)
