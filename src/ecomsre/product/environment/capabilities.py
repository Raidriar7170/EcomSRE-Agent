"""Environment source and mechanism capability derivation."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import json
from typing import Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.effective_policy_v222 import (
    build_effective_support_policy_v222,
)
from ecomsre.dta_v2.v22.gap_router_v222 import (
    SOURCE_PREDICATE_CAPABILITIES_V222,
)
from ecomsre.dta_v2.v22.memory import PredicateKindV22
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.ontology_view import REGISTERED_MECHANISMS_V23
from ecomsre.product.connectors.base import (
    ConnectorAvailabilityV1,
    ConnectorHealthResultV1,
)
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.errors import not_found
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


class SourceCapabilityStatusV1(str, Enum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class MechanismCapabilityStatusV1(str, Enum):
    DIAGNOSABLE = "DIAGNOSABLE"
    PARTIALLY_OBSERVABLE = "PARTIALLY_OBSERVABLE"
    UNAVAILABLE = "UNAVAILABLE"


class SourceCapabilityV1(ProductModelV1):
    source: EvidenceSourceV22
    status: SourceCapabilityStatusV1
    connector_names: tuple[str, ...]
    covered_services: tuple[str, ...]
    target_complete_coverage: bool
    observable_predicates: tuple[PredicateKindV22, ...]


class MechanismCapabilityV1(ProductModelV1):
    mechanism: MechanismV22
    status: MechanismCapabilityStatusV1
    observable_sources: tuple[EvidenceSourceV22, ...]


class EnvironmentCapabilityMatrixV1(ProductModelV1):
    schema_version: Literal["ecomsre.product.environment-capability-matrix.v1"] = (
        "ecomsre.product.environment-capability-matrix.v1"
    )
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    logical_services: tuple[str, ...]
    sources: tuple[SourceCapabilityV1, ...]
    mechanisms: tuple[MechanismCapabilityV1, ...]
    no_incident_eligible: bool
    effective_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verified_at: datetime
    capability_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_canonical_bound_matrix(self) -> "EnvironmentCapabilityMatrixV1":
        if self.verified_at.tzinfo is None or self.verified_at.utcoffset() != timedelta(0):
            raise ValueError("capability verification time must be UTC")
        if self.logical_services != tuple(sorted(set(self.logical_services))):
            raise ValueError("capability services are not canonical")
        source_values = tuple(item.source for item in self.sources)
        if source_values != tuple(sorted(set(source_values), key=lambda item: item.value)):
            raise ValueError("source capabilities are not canonical")
        mechanisms = tuple(item.mechanism for item in self.mechanisms)
        if mechanisms != tuple(sorted(set(mechanisms), key=lambda item: item.value)):
            raise ValueError("mechanism capabilities are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"capability_sha256"})
        )
        if self.capability_sha256 != expected:
            raise ValueError("capability matrix digest differs")
        return self


_HTTP_HEALTH_RUNTIME_PREDICATES = frozenset(
    {
        PredicateKindV22.RUNTIME_HEALTHY,
        PredicateKindV22.RUNTIME_UNHEALTHY,
    }
)


def _observable_predicates(
    source: EvidenceSourceV22,
    health_rows: tuple[ConnectorHealthResultV1, ...],
) -> frozenset[PredicateKindV22]:
    if source is EvidenceSourceV22.RUNTIME and all(
        item.kind.value == "HTTP_HEALTH" for item in health_rows
    ):
        return _HTTP_HEALTH_RUNTIME_PREDICATES
    return SOURCE_PREDICATE_CAPABILITIES_V222[source]


def build_environment_capability_matrix(
    *,
    environment_id: str,
    logical_services: tuple[str, ...],
    connector_health: tuple[ConnectorHealthResultV1, ...],
    changes_available: bool,
    verified_at: datetime,
) -> EnvironmentCapabilityMatrixV1:
    statuses: dict[EvidenceSourceV22, SourceCapabilityStatusV1] = {}
    available_predicates: set[PredicateKindV22] = set()
    partial_predicates: set[PredicateKindV22] = set()
    source_rows: list[SourceCapabilityV1] = []
    for source in sorted(EvidenceSourceV22, key=lambda item: item.value):
        relevant = tuple(
            health
            for health in connector_health
            if any(capability.source is source for capability in health.capabilities)
        )
        covered = tuple(
            sorted(
                {
                    service
                    for item in relevant
                    for service in item.discovered_services
                }
            )
        )
        target_complete_without_discovery = any(
            item.status is ConnectorAvailabilityV1.AVAILABLE
            and any(
                capability.source is source
                and capability.supports_target_complete_coverage
                and not capability.supports_service_discovery
                for capability in item.capabilities
            )
            for item in relevant
        )
        if target_complete_without_discovery:
            covered = tuple(sorted(set(logical_services)))
        if source is EvidenceSourceV22.CHANGES and changes_available:
            status = SourceCapabilityStatusV1.AVAILABLE
            covered = tuple(sorted(set(logical_services)))
            target_complete = True
            observable = SOURCE_PREDICATE_CAPABILITIES_V222[source]
        elif any(item.status is ConnectorAvailabilityV1.AVAILABLE for item in relevant):
            status = (
                SourceCapabilityStatusV1.AVAILABLE
                if set(logical_services).issubset(covered)
                else SourceCapabilityStatusV1.PARTIAL
            )
            target_complete = set(logical_services).issubset(covered) and any(
                item.status is ConnectorAvailabilityV1.AVAILABLE
                and any(
                    capability.source is source
                    and capability.supports_target_complete_coverage
                    for capability in item.capabilities
                )
                for item in relevant
            )
            observable = _observable_predicates(source, relevant)
        elif any(item.status is ConnectorAvailabilityV1.PARTIAL for item in relevant):
            status = SourceCapabilityStatusV1.PARTIAL
            target_complete = False
            observable = _observable_predicates(source, relevant)
        else:
            status = SourceCapabilityStatusV1.UNAVAILABLE
            target_complete = False
            observable = frozenset()
        statuses[source] = status
        if status is SourceCapabilityStatusV1.AVAILABLE:
            available_predicates.update(observable)
        elif status is SourceCapabilityStatusV1.PARTIAL:
            partial_predicates.update(observable)
        source_rows.append(
            SourceCapabilityV1(
                source=source,
                status=status,
                connector_names=tuple(sorted(item.connector_name for item in relevant)),
                covered_services=covered,
                target_complete_coverage=target_complete,
                observable_predicates=tuple(
                    sorted(observable, key=lambda item: item.value)
                ),
            )
        )

    source_by_predicate = {
        predicate: tuple(
            source
            for source, predicates in SOURCE_PREDICATE_CAPABILITIES_V222.items()
            if predicate in predicates
        )
        for predicates in SOURCE_PREDICATE_CAPABILITIES_V222.values()
        for predicate in predicates
    }
    policy = build_effective_support_policy_v222()
    mechanism_rows: list[MechanismCapabilityV1] = []
    for mechanism in REGISTERED_MECHANISMS_V23:
        clauses = tuple(item for item in policy.clauses if item.mechanism is mechanism)
        diagnosable = False
        any_observable = False
        observable_sources: set[EvidenceSourceV22] = set()
        for clause in clauses:
            clause_complete = True
            for requirement in clause.requirements:
                candidates = source_by_predicate[requirement.predicate_kind]
                available = tuple(
                    source
                    for source in candidates
                    if requirement.predicate_kind in available_predicates
                    and statuses[source] is SourceCapabilityStatusV1.AVAILABLE
                    and requirement.predicate_kind
                    in SOURCE_PREDICATE_CAPABILITIES_V222[source]
                )
                partial = tuple(
                    source
                    for source in candidates
                    if requirement.predicate_kind in partial_predicates
                    and statuses[source] is SourceCapabilityStatusV1.PARTIAL
                    and requirement.predicate_kind
                    in SOURCE_PREDICATE_CAPABILITIES_V222[source]
                )
                observable_sources.update((*available, *partial))
                any_observable = any_observable or bool(available or partial)
                clause_complete = clause_complete and bool(available)
            diagnosable = diagnosable or clause_complete
        mechanism_rows.append(
            MechanismCapabilityV1(
                mechanism=mechanism,
                status=(
                    MechanismCapabilityStatusV1.DIAGNOSABLE
                    if diagnosable
                    else MechanismCapabilityStatusV1.PARTIALLY_OBSERVABLE
                    if any_observable
                    else MechanismCapabilityStatusV1.UNAVAILABLE
                ),
                observable_sources=tuple(
                    sorted(observable_sources, key=lambda item: item.value)
                ),
            )
        )
    draft = EnvironmentCapabilityMatrixV1.model_construct(
        schema_version="ecomsre.product.environment-capability-matrix.v1",
        environment_id=environment_id,
        logical_services=tuple(sorted(set(logical_services))),
        sources=tuple(source_rows),
        mechanisms=tuple(mechanism_rows),
        no_incident_eligible=False,
        effective_policy_sha256=policy.policy_sha256,
        verified_at=verified_at,
        capability_sha256="0" * 64,
    )
    payload = draft.model_dump(mode="json", exclude={"capability_sha256"})
    return EnvironmentCapabilityMatrixV1.model_validate(
        {
            **payload,
            "capability_sha256": semantic_sha256_v22(payload),
        }
    )


class CapabilityMatrixRepositoryV1:
    def __init__(self, store: SqliteStoreV1) -> None:
        self.store = store

    def put(self, matrix: EnvironmentCapabilityMatrixV1) -> None:
        payload = json.dumps(
            matrix.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.store.connect() as connection:
            connection.execute(
                """INSERT INTO environment_capability_matrices(
                    environment_id, payload_json, created_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(environment_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at""",
                (matrix.environment_id, payload, matrix.verified_at.isoformat()),
            )

    def get(self, environment_id: str) -> EnvironmentCapabilityMatrixV1:
        with self.store.connect() as connection:
            row = connection.execute(
                """SELECT payload_json FROM environment_capability_matrices
                   WHERE environment_id = ?""",
                (environment_id,),
            ).fetchone()
        if row is None:
            raise not_found(
                "CAPABILITY_MATRIX_NOT_FOUND",
                "The environment has not completed connector verification.",
            )
        return EnvironmentCapabilityMatrixV1.model_validate(
            json.loads(row["payload_json"])
        )


__all__ = (
    "CapabilityMatrixRepositoryV1",
    "EnvironmentCapabilityMatrixV1",
    "MechanismCapabilityStatusV1",
    "SourceCapabilityStatusV1",
    "build_environment_capability_matrix",
)
