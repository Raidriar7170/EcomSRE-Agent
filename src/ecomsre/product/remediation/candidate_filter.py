"""Deterministic candidate projection from persisted Product evidence only."""

from __future__ import annotations

from typing import Any

from ecomsre.dta_v2.v22.memory import RuntimeReadOutcomeV22, build_memory_views_v22
from ecomsre.dta_v2.v22.predicates import build_default_evidence_support_policy_v22
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
from ecomsre.dta_v2.v23.known_admission import build_known_admission_state_v23
from ecomsre.dta_v2.v23.ontology_view import build_active_ontology_view_v23
from ecomsre.product.baselines import EnvironmentBaselineV1
from ecomsre.product.contracts import ServiceIdentityMapV1
from ecomsre.product.connectors.base import ConnectorQueryResultV1
from ecomsre.product.environment.capabilities import EnvironmentCapabilityMatrixV1
from ecomsre.product.incidents.anomaly_policy import extract_product_anomalies_v1
from ecomsre.product.incidents.contracts import (
    DiagnosisResultV1,
    EvidenceBundleV1,
    IncidentRecordV1,
)
from ecomsre.product.incidents.diagnosis_bridge import (
    ProductDiagnosisBridgeV1,
    _effective_admissions_v024,
)
from ecomsre.product.incidents.evidence_binding_v0232 import (
    CapabilityEvidenceObservationV0232,
    DiagnosisEvidenceIndexV0232,
)
from ecomsre.product.incidents.repository import _source_disposition
from ecomsre.product.incidents.queue_action import build_queue_lag_action_v030
from ecomsre.product.incidents.read_backend import (
    ProductReadAcquisitionV1,
    _project_capability_scope_v0232,
)
from ecomsre.product.remediation.contracts import (
    CandidateProjectionV1,
    CandidateReasonV1,
    RemediationCandidateV1,
    RemediationRegistryV1,
)
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1


class CandidateBindingError(ValueError):
    def __init__(self, reason: CandidateReasonV1) -> None:
        super().__init__(reason.value)
        self.reason = reason


def require(value: bool, reason: CandidateReasonV1) -> None:
    if not value:
        raise CandidateBindingError(reason)


def project_candidate(
    *,
    incident: IncidentRecordV1,
    diagnosis: DiagnosisResultV1,
    evidence: EvidenceBundleV1,
    index: DiagnosisEvidenceIndexV0232,
    baseline: EnvironmentBaselineV1,
    identity: ServiceIdentityMapV1,
    capability: EnvironmentCapabilityMatrixV1,
    registry: RemediationRegistryV1,
    expected_registry_sha256: str,
    objects: ContentAddressedObjectStoreV1,
) -> CandidateProjectionV1:
    """Project at most one non-executable candidate; never change a parent object.

    Callers load these inputs from Product repositories. Even direct callers must
    pass validation and CAS resolution; model_copy/model_construct cannot bypass
    digest checks. Recovery, approval and action execution are separate phases.
    """
    diagnosis = DiagnosisResultV1.model_validate_json(diagnosis.model_dump_json())
    candidates: tuple[RemediationCandidateV1, ...] = ()
    reasons: tuple[CandidateReasonV1, ...] = ()
    try:
        if diagnosis.terminal.value != "CORE_KNOWN":
            raise CandidateBindingError(CandidateReasonV1(diagnosis.terminal.value))
        require(len(diagnosis.root_service_ids) == 1, CandidateReasonV1.MULTIPLE_ROOTS)
        require(
            diagnosis.broad_domain == "CONFIGURATION", CandidateReasonV1.WRONG_DOMAIN
        )
        require(
            diagnosis.mechanism == "CONFIGURATION_ERROR",
            CandidateReasonV1.WRONG_MECHANISM,
        )
        # Reparse serialized models because older Product models are not frozen.
        incident = IncidentRecordV1.model_validate_json(incident.model_dump_json())
        baseline = EnvironmentBaselineV1.model_validate_json(baseline.model_dump_json())
        identity = ServiceIdentityMapV1.model_validate_json(identity.model_dump_json())
        capability = EnvironmentCapabilityMatrixV1.model_validate_json(
            capability.model_dump_json()
        )
        registry = RemediationRegistryV1.model_validate_json(registry.model_dump_json())
        index = DiagnosisEvidenceIndexV0232.model_validate_json(index.model_dump_json())
        require(
            registry.registry_sha256 == expected_registry_sha256,
            CandidateReasonV1.REGISTRY_MISMATCH,
        )
        require(
            diagnosis.incident_id == incident.incident_id,
            CandidateReasonV1.DIAGNOSIS_BINDING_MISMATCH,
        )
        require(
            baseline.environment_id == incident.environment_id
            and baseline.baseline_id == incident.baseline_id
            and baseline.baseline_sha256 == incident.baseline_sha256
            and baseline.active,
            CandidateReasonV1.BASELINE_MISMATCH,
        )
        require(
            identity.environment_id == incident.environment_id
            and identity.identity_sha256 == incident.service_identity_sha256,
            CandidateReasonV1.IDENTITY_MISMATCH,
        )
        roots = tuple(
            item.logical_service
            for item in identity.services
            if item.service_id in diagnosis.root_service_ids
        )
        require(
            roots == ("payment",) and "payment" in incident.candidate_logical_services,
            CandidateReasonV1.WRONG_ROOT,
        )
        require(
            capability.environment_id == incident.environment_id
            and capability.capability_sha256 == incident.source_capability_sha256
            and baseline.source_capability_sha256 == capability.capability_sha256,
            CandidateReasonV1.CAPABILITY_MISMATCH,
        )
        require(
            evidence.incident_id == index.incident_id == incident.incident_id
            and evidence.diagnosis_id == index.diagnosis_id == diagnosis.diagnosis_id
            and semantic_sha256_v22(evidence.model_dump(mode="json"))
            == index.evidence_bundle_sha256
            and evidence.supporting_evidence_refs
            == index.linked_support_refs
            == diagnosis.supporting_evidence_refs
            and evidence.contradicting_evidence_refs
            == index.linked_contradiction_refs
            == diagnosis.contradicting_evidence_refs
            and tuple(item.evidence_ref for item in evidence.objects)
            == index.all_object_refs
            and {item.evidence_ref: item.object_sha256 for item in evidence.objects}
            == index.all_object_sha256_by_ref,
            CandidateReasonV1.EVIDENCE_BINDING_MISMATCH,
        )
        snapshots: dict[str, dict[str, Any]] = {}
        capability_observations: list[CapabilityEvidenceObservationV0232] = []
        for item in evidence.objects:
            require(
                semantic_sha256_v22(item.payload) == item.object_sha256
                and objects.read_bytes(item.object_sha256)
                == _canonical_bytes(item.payload),
                CandidateReasonV1.EVIDENCE_BINDING_MISMATCH,
            )
            action = item.payload.get("action")
            if isinstance(action, dict):
                action_id = action.get("action_id")
                require(
                    action_id == item.action_id,
                    CandidateReasonV1.EVIDENCE_BINDING_MISMATCH,
                )
                prior = snapshots.setdefault(item.action_id, item.payload)
                require(
                    prior == item.payload, CandidateReasonV1.EVIDENCE_BINDING_MISMATCH
                )
            elif (
                item.payload.get("schema_version")
                == "ecomsre.product.capability-evidence-observation.v0232"
            ):
                capability_observations.append(
                    CapabilityEvidenceObservationV0232.model_validate_json(
                        _canonical_bytes(item.payload)
                    )
                )
            else:
                raise CandidateBindingError(CandidateReasonV1.EVIDENCE_BINDING_MISMATCH)
        for disposition, references in (
            ("SUCCESSFUL", index.successful_source_refs),
            ("FAILED", index.failed_source_refs),
        ):
            require(
                tuple(
                    sorted(
                        item.evidence_ref
                        for item in evidence.objects
                        if _source_disposition(item.payload) == disposition
                    )
                )
                == references,
                CandidateReasonV1.EVIDENCE_BINDING_MISMATCH,
            )
        require(
            tuple(item.limitation_code for item in index.capability_limitation_bindings)
            == diagnosis.capability_limitations,
            CandidateReasonV1.CAPABILITY_MISMATCH,
        )
        by_ref = {item.evidence_ref: item for item in evidence.objects}
        source_capabilities = {item.source: item for item in capability.sources}
        for observed in capability_observations:
            require(
                observed.source in source_capabilities,
                CandidateReasonV1.CAPABILITY_MISMATCH,
            )
            source_capability = source_capabilities[observed.source]
            scoped_status, scoped_services = _project_capability_scope_v0232(
                status=source_capability.status,
                covered_services=source_capability.covered_services,
                required_services=incident.candidate_logical_services,
            )
            require(
                observed.capability_matrix_sha256 == capability.capability_sha256
                and observed.required_services == incident.candidate_logical_services
                and observed.capability_status == scoped_status
                and observed.available_services == scoped_services,
                CandidateReasonV1.CAPABILITY_MISMATCH,
            )
        for binding in index.capability_limitation_bindings:
            observation = by_ref[binding.evidence_ref]
            require(
                observation.source == binding.source,
                CandidateReasonV1.CAPABILITY_MISMATCH,
            )
            if binding.connector_result_sha256 is not None:
                require(
                    observation.payload.get("connector_result", {}).get("result_sha256")
                    == binding.connector_result_sha256,
                    CandidateReasonV1.CAPABILITY_MISMATCH,
                )
            else:
                require(
                    observation.payload.get("observation_sha256")
                    == binding.capability_observation_sha256
                    and observation.payload.get("reason_code")
                    == binding.limitation_code,
                    CandidateReasonV1.CAPABILITY_MISMATCH,
                )
        queue_id = build_queue_lag_action_v030().action_id
        ordered = tuple(
            snapshots[key]
            for key in sorted(snapshots, key=lambda key: (key == queue_id, key))
        )
        raw: list[ReadOutcomeV22] = []
        memory_outcomes: list[ReadOutcomeV22 | RuntimeReadOutcomeV22] = []
        coverage: dict[EvidenceSourceV22, set[str]] = {}
        for snapshot in ordered:
            outcome = ReadOutcomeV22.model_validate_json(
                _canonical_bytes(snapshot["read_outcome"])
            )
            connector = ConnectorQueryResultV1.model_validate_json(
                _canonical_bytes(snapshot["connector_result"])
            )
            require(
                snapshot["action"]["action_id"] == outcome.action_id
                and snapshot["action"]["source"] == outcome.source.value
                and connector.source == outcome.source
                and connector.status == outcome.status
                and connector.records == outcome.records
                and connector.truncated == outcome.truncated,
                CandidateReasonV1.EVIDENCE_BINDING_MISMATCH,
            )
            raw.append(outcome)
            memory_payload = snapshot.get("memory_outcome")
            if memory_payload is not None:
                model = (
                    RuntimeReadOutcomeV22
                    if memory_payload.get("schema_version")
                    == "dta-v22.runtime-read-outcome.v1"
                    else ReadOutcomeV22
                )
                memory_outcome = model.model_validate_json(
                    _canonical_bytes(memory_payload)
                )
                require(
                    (
                        memory_outcome.source_outcome
                        if isinstance(memory_outcome, RuntimeReadOutcomeV22)
                        else memory_outcome
                    )
                    == outcome,
                    CandidateReasonV1.EVIDENCE_BINDING_MISMATCH,
                )
                memory_outcomes.append(memory_outcome)
            coverage.setdefault(outcome.source, set()).update(
                snapshot.get("connector_result", {}).get("covered_services", ())
            )
        require(
            bool(raw) and bool(memory_outcomes),
            CandidateReasonV1.EVIDENCE_BINDING_MISMATCH,
        )
        memory, _ = build_memory_views_v22(
            outcomes=tuple(memory_outcomes),
            baseline=baseline.v22_baseline_profile,
            observed_at=incident.diagnosis_observed_at,
            top_k=64,
        )
        require(
            memory.memory_sha256 == diagnosis.memory_sha256,
            CandidateReasonV1.DIAGNOSIS_BINDING_MISMATCH,
        )
        acquisition = ProductReadAcquisitionV1(
            raw_outcomes=tuple(raw),
            memory_outcomes=tuple(memory_outcomes),
            snapshots=ordered,
            covered_services_by_source={
                source: tuple(sorted(services)) for source, services in coverage.items()
            },
            capability_limitations=diagnosis.capability_limitations,
            capability_observations_v0232=tuple(capability_observations),
            capability_limitation_candidates_v0232=(),
        )
        reconstructed, generated_observations, trace = (
            ProductDiagnosisBridgeV1().diagnose(
                incident=incident,
                baseline=baseline,
                identity_map=identity,
                acquisition=acquisition,
                diagnosis_id=diagnosis.diagnosis_id,
                created_at=diagnosis.created_at,
            )
        )
        require(
            reconstructed == diagnosis, CandidateReasonV1.DIAGNOSIS_BINDING_MISMATCH
        )
        expected_observations = {
            str(item["evidence_ref"]): (
                str(item["source"]),
                str(item["action_id"]),
                semantic_sha256_v22(item["payload"]),
            )
            for item in generated_observations
        }
        actual_observations = {
            item.evidence_ref: (item.source.value, item.action_id, item.object_sha256)
            for item in evidence.objects
        }
        require(
            expected_observations == actual_observations,
            CandidateReasonV1.EVIDENCE_BINDING_MISMATCH,
        )
        require(
            trace.trace_sha256 == index.decision_trace_sha256,
            CandidateReasonV1.MISSING_DECISION_TRACE,
        )
        trace_bytes = _canonical_bytes(trace.model_dump(mode="json"))
        import hashlib

        require(
            objects.read_bytes(hashlib.sha256(trace_bytes).hexdigest()) == trace_bytes,
            CandidateReasonV1.MISSING_DECISION_TRACE,
        )
        require(
            trace.known_admission_status.value == "SINGLE_ADMISSION",
            CandidateReasonV1.MULTIPLE_CORE_ADMISSIONS,
        )
        topology = tuple(
            (edge.parent_service, edge.child_service)
            for edge in baseline.topology_edges
        )
        admission = build_known_admission_state_v23(
            view=build_active_ontology_view_v23(
                candidate_services=incident.candidate_logical_services
            ),
            memory=memory,
            topology_edges=topology,
            evidence_source_unavailable=any(
                item.status
                not in {
                    ReadSourceStatusV22.SUCCESS_EMPTY,
                    ReadSourceStatusV22.SUCCESS_NONEMPTY,
                }
                for item in raw
            ),
        )
        anomalies = extract_product_anomalies_v1(
            memory=memory,
            candidate_services=incident.candidate_logical_services,
            baseline_known_log_templates=tuple(
                (item.service, item.template) for item in baseline.normal_log_templates
            ),
            snapshots=ordered,
        )
        effective = _effective_admissions_v024(
            admission=admission, memory=memory, anomalies=anomalies
        )
        require(len(effective) == 1, CandidateReasonV1.MULTIPLE_CORE_ADMISSIONS)
        selected = effective[0]
        runbook = registry.entries[0]
        require(
            selected.matched_clause_id in runbook.allowed_diagnosis_clause_ids
            and selected.root_service == "payment"
            and selected.supporting_evidence_refs == diagnosis.supporting_evidence_refs
            and not diagnosis.contradicting_evidence_refs,
            CandidateReasonV1.SUPPORT_CLAUSE_MISMATCH,
        )
        clause = next(
            item
            for item in build_default_evidence_support_policy_v22().clauses
            if item.clause_id == selected.matched_clause_id
        )
        available_predicates = {
            predicate
            for source in capability.sources
            if source.status.value != "UNAVAILABLE"
            and "payment" in source.covered_services
            for predicate in source.observable_predicates
        }
        available_sources = {
            source.source
            for source in capability.sources
            if source.status.value != "UNAVAILABLE"
            and "payment" in source.covered_services
        }
        require(
            all(
                by_ref[ref].source in available_sources
                and ref in index.successful_source_refs
                for ref in selected.supporting_evidence_refs
            ),
            CandidateReasonV1.REQUIRED_SOURCE_UNAVAILABLE,
        )
        require(
            all(
                item.predicate_kind in available_predicates
                for item in clause.requirements
            ),
            CandidateReasonV1.REQUIRED_SOURCE_UNAVAILABLE,
        )
        require(
            not set(selected.supporting_evidence_refs).intersection(
                index.failed_source_refs
            ),
            CandidateReasonV1.REQUIRED_SOURCE_UNAVAILABLE,
        )
        payload: dict[str, Any] = dict(
            environment_id=incident.environment_id,
            incident_id=incident.incident_id,
            incident_sha256=incident.incident_sha256,
            diagnosis_id=diagnosis.diagnosis_id,
            diagnosis_sha256=diagnosis.result_sha256,
            diagnosis_decision_trace_sha256=trace.trace_sha256,
            evidence_bundle_sha256=index.evidence_bundle_sha256,
            evidence_index_sha256=index.index_sha256,
            admission_sha256=selected.diagnosis_sha256,
            memory_sha256=memory.memory_sha256,
            baseline_id=baseline.baseline_id,
            baseline_sha256=baseline.baseline_sha256,
            identity_map_sha256=identity.identity_sha256,
            capability_sha256=capability.capability_sha256,
            registry_sha256=registry.registry_sha256,
            runbook_sha256=runbook.runbook_sha256,
            parameters_sha256=semantic_sha256_v22([]),
            matched_clause_id=selected.matched_clause_id,
            created_at=diagnosis.created_at,
        )
        draft = RemediationCandidateV1.model_construct(
            **payload, candidate_id="cand-" + "0" * 24, candidate_sha256="0" * 64
        )
        identity_payload = draft.model_dump(
            mode="json", exclude={"candidate_id", "candidate_sha256"}
        )
        candidates = (
            RemediationCandidateV1.build(
                **payload,
                candidate_id="cand-" + semantic_sha256_v22(identity_payload)[:24],
            ),
        )
    except CandidateBindingError as error:
        reasons = (error.reason,)
    return CandidateProjectionV1.build(
        incident_id=diagnosis.incident_id,
        diagnosis_sha256=diagnosis.result_sha256,
        candidates=candidates,
        reason_codes=reasons,
        created_at=diagnosis.created_at,
    )


def _canonical_bytes(value: Any) -> bytes:
    import json

    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
