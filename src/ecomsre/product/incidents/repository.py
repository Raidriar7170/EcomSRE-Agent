"""Transactional incident and diagnosis persistence."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.baselines import BaselineRepositoryV1
from ecomsre.product.environment.capabilities import CapabilityMatrixRepositoryV1
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.errors import ProductError, not_found
from ecomsre.product.ids import new_product_id
from ecomsre.product.incidents.contracts import (
    DiagnosisResultV1,
    EvidenceBundleV1,
    EvidenceObjectV1,
    IncidentCreateV1,
    IncidentRecordV1,
)
from ecomsre.product.jobs.contracts import JobLeaseFenceV1
from ecomsre.product.jobs.fencing import require_live_job_fence
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class IncidentRepositoryV1:
    def __init__(
        self,
        store: SqliteStoreV1,
        *,
        environments: EnvironmentRepositoryV1,
        services: ServiceCatalogRepositoryV1,
        capabilities: CapabilityMatrixRepositoryV1,
        baselines: BaselineRepositoryV1,
    ) -> None:
        self.store = store
        self.environments = environments
        self.services = services
        self.capabilities = capabilities
        self.baselines = baselines

    def create(
        self,
        request: IncidentCreateV1,
        *,
        now: float | None = None,
    ) -> IncidentRecordV1:
        self.environments.get(request.environment_id)
        baseline = self.baselines.get_active(request.environment_id)
        identity = self.services.get_map(request.environment_id)
        capability = self.capabilities.get(request.environment_id)
        identity_by_id = {item.service_id: item for item in identity.services}
        if not set(request.candidate_service_ids).issubset(identity_by_id):
            raise ProductError(
                "SERVICE_NOT_FOUND",
                "One or more incident candidate services do not exist.",
                status_code=404,
            )
        created_at = datetime.now(UTC) if now is None else datetime.fromtimestamp(now, UTC)
        diagnosis_observed_at = request.ended_at or created_at
        incident_id = new_product_id("inc")
        payload: dict[str, Any] = {
            **request.model_dump(mode="python"),
            "schema_version": "ecomsre.product.incident.v1",
            "incident_id": incident_id,
            "baseline_id": baseline.baseline_id,
            "baseline_sha256": baseline.baseline_sha256,
            "service_identity_sha256": identity.identity_sha256,
            "source_capability_sha256": capability.capability_sha256,
            "candidate_logical_services": tuple(
                sorted(identity_by_id[item].logical_service for item in request.candidate_service_ids)
            ),
            "diagnosis_observed_at": diagnosis_observed_at,
            "created_at": created_at,
        }
        draft = IncidentRecordV1.model_construct(
            **payload,
            incident_sha256="0" * 64,
        )
        record = IncidentRecordV1.model_validate(
            {
                **payload,
                "incident_sha256": semantic_sha256_v22(
                    draft.model_dump(mode="json", exclude={"incident_sha256"})
                ),
            }
        )
        serialized = _json(record.model_dump(mode="json"))
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT payload_json FROM incidents "
                    "WHERE environment_id = ? AND external_incident_key = ?",
                    (request.environment_id, request.external_incident_key),
                ).fetchone()
                if existing is not None:
                    prior = IncidentRecordV1.model_validate_json(existing["payload_json"])
                    prior_request = IncidentCreateV1.model_validate(
                        prior.model_dump(
                            mode="python",
                            include=set(IncidentCreateV1.model_fields),
                        )
                    )
                    if prior_request != request:
                        raise ProductError(
                            "INCIDENT_IDEMPOTENCY_CONFLICT",
                            "The external incident key is bound to a different payload.",
                            status_code=409,
                        )
                    connection.execute("COMMIT")
                    return prior
                connection.execute(
                    "INSERT INTO incidents(incident_id, environment_id, "
                    "external_incident_key, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        record.incident_id,
                        record.environment_id,
                        record.external_incident_key,
                        serialized,
                        record.created_at.isoformat(),
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return record

    def get(self, incident_id: str) -> IncidentRecordV1:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM incidents WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
        if row is None:
            raise not_found("INCIDENT_NOT_FOUND", "The requested incident does not exist.")
        return IncidentRecordV1.model_validate_json(row["payload_json"])


class DiagnosisRepositoryV1:
    def __init__(
        self,
        store: SqliteStoreV1,
        object_store: ContentAddressedObjectStoreV1,
    ) -> None:
        self.store = store
        self.object_store = object_store

    def get_optional(self, incident_id: str) -> DiagnosisResultV1 | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM diagnosis_results WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
        return None if row is None else DiagnosisResultV1.model_validate_json(row["payload_json"])

    def get(self, incident_id: str) -> DiagnosisResultV1:
        result = self.get_optional(incident_id)
        if result is None:
            raise not_found(
                "DIAGNOSIS_NOT_FOUND",
                "The incident has no completed diagnosis.",
            )
        return result

    def put(
        self,
        *,
        result: DiagnosisResultV1,
        observations: tuple[dict[str, Any], ...],
        fence: JobLeaseFenceV1,
    ) -> DiagnosisResultV1:
        stored = tuple(
            (
                observation,
                self.object_store.prepare_json(observation["payload"]),
            )
            for observation in observations
        )
        serialized = _json(result.model_dump(mode="json"))
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                require_live_job_fence(connection, fence)
                existing = connection.execute(
                    "SELECT payload_json FROM diagnosis_results WHERE incident_id = ?",
                    (result.incident_id,),
                ).fetchone()
                if existing is not None:
                    prior = DiagnosisResultV1.model_validate_json(existing["payload_json"])
                    if prior.result_sha256 != result.result_sha256:
                        raise ProductError(
                            "DIAGNOSIS_IMMUTABLE_CONFLICT",
                            "The incident diagnosis already exists with different content.",
                            status_code=409,
                        )
                    connection.execute("COMMIT")
                    return prior
                for _observation, stored_object in stored:
                    self.object_store.bind_prepared(
                        connection,
                        stored_object,
                        created_at=result.created_at,
                    )
                connection.execute(
                    "INSERT INTO diagnosis_results(diagnosis_id, incident_id, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        result.diagnosis_id,
                        result.incident_id,
                        serialized,
                        result.created_at.isoformat(),
                    ),
                )
                for observation, stored_object in stored:
                    connection.execute(
                        "INSERT INTO diagnosis_evidence_links(diagnosis_id, incident_id, "
                        "object_sha256, evidence_ref, source, action_id, role, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, 'OBSERVATION', ?)",
                        (
                            result.diagnosis_id,
                            result.incident_id,
                            stored_object.object_sha256,
                            observation["evidence_ref"],
                            observation["source"],
                            observation["action_id"],
                            result.created_at.isoformat(),
                        ),
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return result

    def evidence(self, incident_id: str) -> EvidenceBundleV1:
        result = self.get(incident_id)
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT object_sha256, evidence_ref, source, action_id, role "
                "FROM diagnosis_evidence_links WHERE diagnosis_id = ? "
                "ORDER BY evidence_ref",
                (result.diagnosis_id,),
            ).fetchall()
        objects = tuple(
            EvidenceObjectV1(
                evidence_ref=row["evidence_ref"],
                source=row["source"],
                action_id=row["action_id"],
                object_sha256=row["object_sha256"],
                role=row["role"],
                payload=json.loads(
                    self.object_store.read_bytes(row["object_sha256"]).decode("utf-8")
                ),
            )
            for row in rows
        )
        return EvidenceBundleV1(
            incident_id=incident_id,
            diagnosis_id=result.diagnosis_id,
            objects=objects,
            supporting_evidence_refs=result.supporting_evidence_refs,
            contradicting_evidence_refs=result.contradicting_evidence_refs,
        )


__all__ = ("DiagnosisRepositoryV1", "IncidentRepositoryV1")
