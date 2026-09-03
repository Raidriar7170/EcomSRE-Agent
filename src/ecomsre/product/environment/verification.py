"""Connector health, service normalization, and capability verification."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import re
from typing import Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.base import ConnectorHealthResultV1
from ecomsre.product.connectors.registry import ConnectorRegistryV1
from ecomsre.product.contracts import (
    ConnectorKindV1,
    EnvironmentRecordV1,
    ProductModelV1,
    ServiceIdentityMapV1,
    ServiceIdentityPolicyV1,
    ServiceIdentityV1,
    ServiceSourceAliasesV1,
)
from ecomsre.product.environment.capabilities import (
    CapabilityMatrixRepositoryV1,
    EnvironmentCapabilityMatrixV1,
    build_environment_capability_matrix,
)
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.errors import ProductError
from ecomsre.product.ids import new_product_id
from ecomsre.product.jobs.contracts import JobLeaseFenceV1
from ecomsre.product.jobs.fencing import require_live_job_fence


_LOGICAL_SERVICE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_ALIAS_FIELD_BY_KIND = {
    ConnectorKindV1.PROMETHEUS: "prometheus",
    ConnectorKindV1.OPENSEARCH: "opensearch",
    ConnectorKindV1.JAEGER: "jaeger",
    ConnectorKindV1.HTTP_HEALTH: "http_health",
}


def _require_bounded_alias_sets(alias_sets: dict[str, dict[str, set[str]]]) -> None:
    if len(alias_sets) > 20:
        raise ProductError(
            "SERVICE_CATALOG_TOO_LARGE",
            "The verified service catalog exceeds the Product candidate bound.",
        )
    for source in ServiceSourceAliasesV1.model_fields:
        if sum(len(values[source]) for values in alias_sets.values()) > 20:
            raise ProductError(
                "SERVICE_CATALOG_TOO_LARGE",
                "A verified source alias set exceeds the Product fanout bound.",
            )


class EnvironmentVerificationResultV1(ProductModelV1):
    schema_version: Literal["ecomsre.product.environment-verification-result.v1"] = (
        "ecomsre.product.environment-verification-result.v1"
    )
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    connector_health: tuple[ConnectorHealthResultV1, ...]
    service_identity_map: ServiceIdentityMapV1
    capability_matrix: EnvironmentCapabilityMatrixV1
    verified_at: datetime
    verification_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_result(self) -> "EnvironmentVerificationResultV1":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"verification_sha256"})
        )
        if self.verification_sha256 != expected:
            raise ValueError("environment verification digest differs")
        return self


def normalize_service_identities(
    *,
    environment: EnvironmentRecordV1,
    existing: ServiceIdentityMapV1,
    connector_health: tuple[ConnectorHealthResultV1, ...],
) -> tuple[ServiceIdentityMapV1, tuple[ConnectorHealthResultV1, ...]]:
    policy = ServiceIdentityPolicyV1.model_validate(
        environment.service_identity_policy.model_dump()
    )
    rules = {item.logical_service: item for item in policy.services}
    alias_owner: dict[tuple[str, str], str] = {}
    alias_sets: dict[str, dict[str, set[str]]] = {}
    for logical, rule in rules.items():
        alias_sets[logical] = {
            key: set(values)
            for key, values in rule.aliases.model_dump(mode="python").items()
        }
        for source, aliases in alias_sets[logical].items():
            for alias in aliases:
                alias_owner[(source, alias)] = logical
    for identity in existing.services:
        alias_sets.setdefault(
            identity.logical_service,
            {
                key: set(values)
                for key, values in identity.aliases.model_dump(mode="python").items()
            },
        )

    _require_bounded_alias_sets(alias_sets)
    declared_services = frozenset(alias_sets)
    if policy.discovery_mode == "DECLARED_ONLY" and not declared_services:
        raise ProductError(
            "SERVICE_CATALOG_EMPTY",
            "Declared-only discovery requires a nonempty service catalog.",
        )

    normalized_health: list[ConnectorHealthResultV1] = []
    for health in connector_health:
        field = _ALIAS_FIELD_BY_KIND.get(health.kind)
        normalized: set[str] = set()
        for alias in health.discovered_services:
            resolved_logical = (
                alias_owner.get((field, alias)) if field is not None else None
            )
            if resolved_logical is None and _LOGICAL_SERVICE.fullmatch(alias):
                resolved_logical = alias
            if (
                policy.discovery_mode == "DECLARED_ONLY"
                and resolved_logical not in declared_services
            ):
                continue
            if resolved_logical is None:
                raise ProductError(
                    "SERVICE_IDENTITY_UNRESOLVED",
                    "A discovered service alias has no approved canonical mapping.",
                )
            normalized.add(resolved_logical)
            alias_sets.setdefault(
                resolved_logical,
                {
                    "prometheus": set(),
                    "opensearch": set(),
                    "jaeger": set(),
                    "http_health": set(),
                },
            )
            if field is not None:
                alias_sets[resolved_logical][field].add(alias)
        normalized_health.append(
            ConnectorHealthResultV1(
                **{
                    **health.model_dump(mode="python"),
                    "discovered_services": tuple(sorted(normalized)),
                }
            )
        )
    _require_bounded_alias_sets(alias_sets)

    existing_by_logical = {item.logical_service: item for item in existing.services}
    identities: list[ServiceIdentityV1] = []
    for logical in sorted(alias_sets):
        current_rule = rules.get(logical)
        if current_rule is None or not current_rule.approved_many_to_one:
            if any(len(values) > 1 for values in alias_sets[logical].values()):
                raise ProductError(
                    "SERVICE_IDENTITY_AMBIGUOUS",
                    "A many-to-one service mapping is not explicitly approved.",
                )
        prior = existing_by_logical.get(logical)
        identities.append(
            ServiceIdentityV1(
                service_id=(
                    prior.service_id if prior is not None else new_product_id("svc")
                ),
                logical_service=logical,
                aliases=ServiceSourceAliasesV1.model_validate(
                    {
                        key: tuple(sorted(values))
                        for key, values in alias_sets[logical].items()
                    }
                ),
            )
        )
    return (
        ServiceIdentityMapV1.build(
            environment_id=environment.environment_id,
            services=tuple(identities),
        ),
        tuple(normalized_health),
    )


class EnvironmentVerificationServiceV1:
    def __init__(
        self,
        *,
        services: ServiceCatalogRepositoryV1,
        capabilities: CapabilityMatrixRepositoryV1,
        connectors: ConnectorRegistryV1,
    ) -> None:
        self._services = services
        self._capabilities = capabilities
        self._connectors = connectors

    def verify(
        self,
        environment: EnvironmentRecordV1,
        *,
        verified_at: datetime | None = None,
        fence: JobLeaseFenceV1 | None = None,
    ) -> EnvironmentVerificationResultV1:
        timestamp = verified_at or datetime.now(UTC)
        health: list[ConnectorHealthResultV1] = []
        for config in environment.connector_configs:
            connector = self._connectors.create(config)
            try:
                health.append(connector.verify())
            finally:
                connector.close()
        identity_map, normalized_health = normalize_service_identities(
            environment=environment,
            existing=self._services.get_map(environment.environment_id),
            connector_health=tuple(health),
        )
        matrix = build_environment_capability_matrix(
            environment_id=environment.environment_id,
            logical_services=tuple(item.logical_service for item in identity_map.services),
            connector_health=normalized_health,
            changes_available=True,
            verified_at=timestamp,
        )
        self._persist_verified_state(
            identity_map,
            matrix,
            timestamp=timestamp,
            fence=fence,
        )
        draft = EnvironmentVerificationResultV1.model_construct(
            environment_id=environment.environment_id,
            connector_health=normalized_health,
            service_identity_map=identity_map,
            capability_matrix=matrix,
            verified_at=timestamp,
            verification_sha256="0" * 64,
        )
        payload = draft.model_dump(mode="json", exclude={"verification_sha256"})
        return EnvironmentVerificationResultV1.model_validate(
            {**payload, "verification_sha256": semantic_sha256_v22(payload)}
        )

    def _persist_verified_state(
        self,
        identity_map: ServiceIdentityMapV1,
        matrix: EnvironmentCapabilityMatrixV1,
        *,
        timestamp: datetime,
        fence: JobLeaseFenceV1 | None,
    ) -> None:
        if self._services.store.path != self._capabilities.store.path:
            raise RuntimeError("verification repositories do not share one SQLite store")
        matrix_payload = json.dumps(
            matrix.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._services.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                require_live_job_fence(connection, fence)
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
                    identity_payload = json.dumps(
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
                            identity_payload,
                            timestamp.isoformat(),
                            identity.logical_service,
                        ),
                    )
                connection.execute(
                    """INSERT INTO environment_capability_matrices(
                        environment_id, payload_json, created_at
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(environment_id) DO UPDATE SET
                        payload_json = excluded.payload_json,
                        created_at = excluded.created_at""",
                    (
                        matrix.environment_id,
                        matrix_payload,
                        timestamp.isoformat(),
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise


__all__ = (
    "EnvironmentVerificationResultV1",
    "EnvironmentVerificationServiceV1",
    "normalize_service_identities",
)
