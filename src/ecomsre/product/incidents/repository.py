"""Transactional incident and diagnosis persistence."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
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
from ecomsre.product.incidents.evidence_binding_v0232 import (
    CapabilityLimitationBindingV0232,
    CapabilityLimitationCandidateV0232,
    DiagnosisDecisionTraceV0232,
    DiagnosisEvidenceIndexV0232,
)
from ecomsre.product.incidents.diagnosis_pipeline_v02322 import (
    DiagnosisBridgeArtifactV02322,
    DiagnosisPersistencePlanV02322,
    DiagnosisPipelineStageV02322,
    DiagnosisPipelineV02322,
)
from ecomsre.product.jobs.contracts import JobLeaseFenceV1
from ecomsre.product.jobs.fencing import require_live_job_fence
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_SUCCESS_STATUSES = {"SUCCESS_EMPTY", "SUCCESS_NONEMPTY"}


def _coverage_status(candidate: CapabilityLimitationCandidateV0232) -> str:
    observed = set(candidate.coverage_observed_services)
    if not observed:
        return "NONE"
    if observed == set(candidate.coverage_required_services):
        return "COMPLETE"
    return "PARTIAL"


def _source_disposition(payload: dict[str, Any]) -> str | None:
    if payload.get("schema_version") == (
        "ecomsre.product.capability-evidence-observation.v0232"
    ):
        return "FAILED"
    connector_result = payload.get("connector_result")
    if not isinstance(connector_result, dict):
        return None
    status = connector_result.get("status")
    if not isinstance(status, str):
        return None
    return "SUCCESSFUL" if status in _SUCCESS_STATUSES else "FAILED"


def _specialized_binding_refs(
    observations: tuple[dict[str, Any], ...],
    *,
    binding_kind: str,
) -> tuple[str, ...]:
    refs: set[str] = set()
    for observation in observations:
        payload = observation["payload"]
        for item in payload.get("connector_bindings_v0232", ()):
            connector_binding = item.get("connector_binding", {})
            if (
                connector_binding.get("binding_kind") == binding_kind
                and item.get("binding_payload") is not None
            ):
                refs.add(str(observation["evidence_ref"]))
    return tuple(sorted(refs))


def _build_limitation_bindings(
    *,
    result: DiagnosisResultV1,
    observations: tuple[dict[str, Any], ...],
    candidates: tuple[CapabilityLimitationCandidateV0232, ...],
) -> tuple[CapabilityLimitationBindingV0232, ...]:
    candidates_by_code = {item.limitation_code: item for item in candidates}
    if len(candidates_by_code) != len(candidates) or set(candidates_by_code) != set(
        result.capability_limitations
    ):
        raise ProductError(
            "DIAGNOSIS_CAPABILITY_BINDING_INVALID",
            "Diagnosis capability limitations do not have exact typed candidates.",
            status_code=409,
        )
    bindings: list[CapabilityLimitationBindingV0232] = []
    for limitation_code in result.capability_limitations:
        candidate = candidates_by_code[limitation_code]
        matches: list[tuple[str, str | None]] = []
        for observation in observations:
            payload = observation["payload"]
            if candidate.connector_result_sha256 is not None:
                connector_result = payload.get("connector_result")
                if (
                    isinstance(connector_result, dict)
                    and connector_result.get("result_sha256")
                    == candidate.connector_result_sha256
                ):
                    matches.append((str(observation["evidence_ref"]), None))
            elif (
                payload.get("schema_version")
                == "ecomsre.product.capability-evidence-observation.v0232"
                and payload.get("source") == candidate.source.value
                and payload.get("reason_code") == candidate.limitation_code
            ):
                matches.append(
                    (
                        str(observation["evidence_ref"]),
                        str(payload.get("observation_sha256")),
                    )
                )
        if not matches:
            raise ProductError(
                "DIAGNOSIS_CAPABILITY_BINDING_INVALID",
                "A diagnosis capability limitation has no persisted Evidence object.",
                status_code=409,
            )
        evidence_ref, observation_sha256 = sorted(matches)[0]
        bindings.append(
            CapabilityLimitationBindingV0232.build(
                limitation_code=candidate.limitation_code,
                category=candidate.category,
                source=candidate.source,
                evidence_ref=evidence_ref,
                connector_result_sha256=candidate.connector_result_sha256,
                capability_observation_sha256=observation_sha256,
                safe_error_code=candidate.safe_error_code,
                coverage_status=_coverage_status(candidate),
            )
        )
    return tuple(bindings)


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
        decision_trace_v0232: DiagnosisDecisionTraceV0232 | None = None,
        limitation_candidates_v0232: tuple[
            CapabilityLimitationCandidateV0232,
            ...,
        ]
        | None = None,
        bridge_artifact_v02322: DiagnosisBridgeArtifactV02322 | None = None,
        stage_pipeline_v02322: DiagnosisPipelineV02322 | None = None,
    ) -> DiagnosisResultV1:
        def run_stage(stage, input_binding_sha256, operation):
            if stage_pipeline_v02322 is None:
                return operation()
            return stage_pipeline_v02322.run(
                stage,
                input_binding_sha256=input_binding_sha256,
                operation=operation,
            )

        def validate_prepare_inputs() -> tuple[dict[str, Any], ...]:
            if (decision_trace_v0232 is None) != (
                limitation_candidates_v0232 is None
            ):
                raise ValueError(
                    "v0.2.3.2 diagnosis bindings must be supplied together"
                )
            if len({item["evidence_ref"] for item in observations}) != len(
                observations
            ):
                raise ProductError(
                    "DIAGNOSIS_EVIDENCE_INDEX_INVALID",
                    "Diagnosis Evidence object references are not unique.",
                    status_code=409,
                )
            return observations

        prepared_observations = run_stage(
            DiagnosisPipelineStageV02322.EVIDENCE_PREPARE_STARTED,
            result.result_sha256,
            validate_prepare_inputs,
        )
        observation_payload_sha256 = {
            str(observation["evidence_ref"]): semantic_sha256_v22(
                observation["payload"]
            )
            for observation in prepared_observations
        }
        run_stage(
            DiagnosisPipelineStageV02322.EVIDENCE_OBJECTS_PREPARED,
            semantic_sha256_v22(observation_payload_sha256),
            lambda: observation_payload_sha256,
        )
        index_v0232: DiagnosisEvidenceIndexV0232 | None = None
        limitation_bindings: tuple[CapabilityLimitationBindingV0232, ...] = ()
        if decision_trace_v0232 is not None and limitation_candidates_v0232 is not None:
            limitation_bindings = run_stage(
                DiagnosisPipelineStageV02322.LIMITATION_BINDING_STARTED,
                result.result_sha256,
                lambda: _build_limitation_bindings(
                    result=result,
                    observations=prepared_observations,
                    candidates=limitation_candidates_v0232,
                ),
            )
            run_stage(
                DiagnosisPipelineStageV02322.LIMITATION_BINDING_COMPLETED,
                semantic_sha256_v22(
                    [item.model_dump(mode="json") for item in limitation_bindings]
                ),
                lambda: limitation_bindings,
            )

        def build_index() -> DiagnosisEvidenceIndexV0232 | None:
            if decision_trace_v0232 is None:
                return None
            if (
                decision_trace_v0232.incident_id != result.incident_id
                or decision_trace_v0232.diagnosis_id != result.diagnosis_id
            ):
                raise ProductError(
                    "DIAGNOSIS_EVIDENCE_INDEX_INVALID",
                    "Diagnosis decision trace identity differs from the diagnosis.",
                    status_code=409,
                )
            objects = tuple(
                sorted(
                    (
                        EvidenceObjectV1(
                            evidence_ref=str(observation["evidence_ref"]),
                            source=observation["source"],
                            action_id=str(observation["action_id"]),
                            object_sha256=hashlib.sha256(
                                _json(observation["payload"]).encode("utf-8")
                            ).hexdigest(),
                            payload=observation["payload"],
                        )
                        for observation in prepared_observations
                    ),
                    key=lambda item: item.evidence_ref,
                )
            )
            bundle = EvidenceBundleV1(
                incident_id=result.incident_id,
                diagnosis_id=result.diagnosis_id,
                objects=objects,
                supporting_evidence_refs=result.supporting_evidence_refs,
                contradicting_evidence_refs=result.contradicting_evidence_refs,
            )
            dispositions = {
                str(observation["evidence_ref"]): _source_disposition(
                    observation["payload"]
                )
                for observation in prepared_observations
            }
            opensearch_refs = _specialized_binding_refs(
                prepared_observations,
                binding_kind="OPENSEARCH_PROFILE",
            )
            runtime_refs = _specialized_binding_refs(
                prepared_observations,
                binding_kind="RUNTIME_SNAPSHOT",
            )
            return DiagnosisEvidenceIndexV0232.build(
                incident_id=result.incident_id,
                diagnosis_id=result.diagnosis_id,
                evidence_bundle_sha256=semantic_sha256_v22(
                    bundle.model_dump(mode="json")
                ),
                all_object_refs=tuple(item.evidence_ref for item in objects),
                all_object_sha256_by_ref={
                    item.evidence_ref: item.object_sha256 for item in objects
                },
                linked_support_refs=result.supporting_evidence_refs,
                linked_contradiction_refs=result.contradicting_evidence_refs,
                successful_source_refs=tuple(
                    sorted(
                        reference
                        for reference, disposition in dispositions.items()
                        if disposition == "SUCCESSFUL"
                    )
                ),
                failed_source_refs=tuple(
                    sorted(
                        reference
                        for reference, disposition in dispositions.items()
                        if disposition == "FAILED"
                    )
                ),
                open_search_profile_binding_ref=(
                    opensearch_refs[0] if opensearch_refs else None
                ),
                runtime_snapshot_binding_ref=(runtime_refs[0] if runtime_refs else None),
                capability_limitation_bindings=limitation_bindings,
                decision_trace_sha256=decision_trace_v0232.trace_sha256,
            )
        index_v0232 = run_stage(
            DiagnosisPipelineStageV02322.EVIDENCE_INDEX_STARTED,
            result.result_sha256,
            build_index,
        )
        run_stage(
            DiagnosisPipelineStageV02322.EVIDENCE_INDEX_VALIDATED,
            (
                result.result_sha256
                if index_v0232 is None
                else index_v0232.index_sha256
            ),
            lambda: index_v0232,
        )
        if bridge_artifact_v02322 is not None:
            if (
                bridge_artifact_v02322.incident_id != result.incident_id
                or bridge_artifact_v02322.diagnosis_id != result.diagnosis_id
                or bridge_artifact_v02322.result_sha256 != result.result_sha256
            ):
                raise ProductError(
                    "DIAGNOSIS_PERSISTENCE_PLAN_INVALID",
                    "Diagnosis bridge binding differs from the persistence input.",
                    status_code=409,
                )
            persistence_plan_v02322 = DiagnosisPersistencePlanV02322.build(
                incident_id=result.incident_id,
                diagnosis_id=result.diagnosis_id,
                bridge_sha256=bridge_artifact_v02322.bridge_sha256,
                evidence_object_sha256_by_ref=dict(
                    sorted(observation_payload_sha256.items())
                ),
                limitation_bindings_sha256=semantic_sha256_v22(
                    [item.model_dump(mode="json") for item in limitation_bindings]
                ),
                evidence_bundle_sha256=(
                    None
                    if index_v0232 is None
                    else index_v0232.evidence_bundle_sha256
                ),
                evidence_index_sha256=(
                    None if index_v0232 is None else index_v0232.index_sha256
                ),
                decision_trace_sha256=(
                    None
                    if decision_trace_v0232 is None
                    else decision_trace_v0232.trace_sha256
                ),
            )
            if stage_pipeline_v02322 is not None:
                stage_pipeline_v02322.bind_artifacts(
                    prepared_evidence_sha256=(
                        persistence_plan_v02322.persistence_plan_sha256
                    )
                )

        def prepare_object_store():
            stored_objects = tuple(
                (
                    observation,
                    self.object_store.prepare_json(observation["payload"]),
                )
                for observation in prepared_observations
            )
            trace = (
                None
                if decision_trace_v0232 is None
                else self.object_store.prepare_json(
                    decision_trace_v0232.model_dump(mode="json")
                )
            )
            return stored_objects, trace

        stored, stored_trace = run_stage(
            DiagnosisPipelineStageV02322.OBJECT_STORE_PREPARE_STARTED,
            (
                result.result_sha256
                if index_v0232 is None
                else index_v0232.index_sha256
            ),
            prepare_object_store,
        )
        run_stage(
            DiagnosisPipelineStageV02322.OBJECT_STORE_PREPARED,
            semantic_sha256_v22(
                {
                    str(observation["evidence_ref"]): stored_object.object_sha256
                    for observation, stored_object in stored
                }
            ),
            lambda: stored,
        )
        serialized = _json(result.model_dump(mode="json"))

        def persist_transaction() -> DiagnosisResultV1:
            with self.store.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    require_live_job_fence(connection, fence)
                    existing = connection.execute(
                        "SELECT payload_json FROM diagnosis_results "
                        "WHERE incident_id = ?",
                        (result.incident_id,),
                    ).fetchone()
                    if existing is not None:
                        prior = DiagnosisResultV1.model_validate_json(
                            existing["payload_json"]
                        )
                        if prior.result_sha256 != result.result_sha256:
                            raise ProductError(
                                "DIAGNOSIS_IMMUTABLE_CONFLICT",
                                "The incident diagnosis already exists with "
                                "different content.",
                                status_code=409,
                            )
                        if index_v0232 is not None:
                            index_row = connection.execute(
                                "SELECT payload_json FROM "
                                "diagnosis_evidence_indexes WHERE incident_id = ?",
                                (result.incident_id,),
                            ).fetchone()
                            if index_row is None:
                                raise ProductError(
                                    "DIAGNOSIS_EVIDENCE_INDEX_MISSING",
                                    "The existing diagnosis has no v0.2.3.2 "
                                    "Evidence Index.",
                                    status_code=409,
                                )
                            prior_index = (
                                DiagnosisEvidenceIndexV0232.model_validate_json(
                                    index_row["payload_json"]
                                )
                            )
                            if prior_index.index_sha256 != index_v0232.index_sha256:
                                raise ProductError(
                                    "DIAGNOSIS_EVIDENCE_INDEX_IMMUTABLE_CONFLICT",
                                    "The existing diagnosis Evidence Index differs.",
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
                    if stored_trace is not None:
                        self.object_store.bind_prepared(
                            connection,
                            stored_trace,
                            created_at=result.created_at,
                        )
                    connection.execute(
                        "INSERT INTO diagnosis_results("
                        "diagnosis_id, incident_id, payload_json, created_at"
                        ") VALUES (?, ?, ?, ?)",
                        (
                            result.diagnosis_id,
                            result.incident_id,
                            serialized,
                            result.created_at.isoformat(),
                        ),
                    )
                    for observation, stored_object in stored:
                        connection.execute(
                            "INSERT INTO diagnosis_evidence_links("
                            "diagnosis_id, incident_id, object_sha256, evidence_ref, "
                            "source, action_id, role, created_at) "
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
                    if index_v0232 is not None:
                        connection.execute(
                            "INSERT INTO diagnosis_evidence_indexes("
                            "diagnosis_id, incident_id, payload_json, index_sha256, "
                            "created_at) VALUES (?, ?, ?, ?, ?)",
                            (
                                result.diagnosis_id,
                                result.incident_id,
                                _json(index_v0232.model_dump(mode="json")),
                                index_v0232.index_sha256,
                                result.created_at.isoformat(),
                            ),
                        )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
            return result

        stored_result = run_stage(
            DiagnosisPipelineStageV02322.SQL_TRANSACTION_STARTED,
            result.result_sha256,
            persist_transaction,
        )
        return run_stage(
            DiagnosisPipelineStageV02322.DIAGNOSIS_PERSISTED,
            stored_result.result_sha256,
            lambda: stored_result,
        )

    def evidence_index(self, incident_id: str) -> DiagnosisEvidenceIndexV0232:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM diagnosis_evidence_indexes "
                "WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
        if row is None:
            raise not_found(
                "DIAGNOSIS_EVIDENCE_INDEX_NOT_FOUND",
                "The incident has no completed v0.2.3.2 Evidence Index.",
            )
        return DiagnosisEvidenceIndexV0232.model_validate_json(row["payload_json"])

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
