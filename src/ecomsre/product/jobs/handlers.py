"""Incremental Product job handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.baselines import (
    BaselineJobCreateV1,
    HistoricalBaselineServiceV1,
)
from ecomsre.product.environment.capabilities import CapabilityMatrixRepositoryV1
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.environment.verification import EnvironmentVerificationServiceV1
from ecomsre.product.errors import ProductError
from ecomsre.product.incidents.diagnosis_bridge import ProductDiagnosisBridgeV1
from ecomsre.product.incidents.diagnosis_pipeline_v02322 import (
    DiagnosisAcquisitionArtifactV02322,
    DiagnosisBridgeArtifactV02322,
    DiagnosisPipelineContextV02322,
    DiagnosisPipelineStageV02322,
    DiagnosisPipelineV02322,
)
from ecomsre.product.incidents.contracts import IncidentRecordV1
from ecomsre.product.incidents.read_backend import ProductReadBackendV1
from ecomsre.product.incidents.repository import (
    DiagnosisRepositoryV1,
    IncidentRepositoryV1,
)
from ecomsre.product.jobs.contracts import JobLeaseFenceV1, ProductJobRecordV1


def handle_fixture_environment_verify(
    job: ProductJobRecordV1,
    environments: EnvironmentRepositoryV1,
) -> dict[str, Any]:
    if job.payload.get("fixture") is not True:
        raise ProductError(
            "CONNECTOR_UNAVAILABLE",
            "The Increment 1 worker only supports fixture verification.",
        )
    environment_id = str(job.payload.get("environment_id", ""))
    environment = environments.get(environment_id)
    if not any(connector.kind.value == "FIXTURE" for connector in environment.connector_configs):
        raise ProductError(
            "CONNECTOR_UNAVAILABLE",
            "The environment has no fixture connector.",
        )
    return {"environment_id": environment_id, "fixture_verified": True}


def handle_environment_verify(
    job: ProductJobRecordV1,
    environments: EnvironmentRepositoryV1,
    verification: EnvironmentVerificationServiceV1,
    *,
    fence: JobLeaseFenceV1 | None = None,
) -> dict[str, Any]:
    environment_id = str(job.payload.get("environment_id", ""))
    environment = environments.get(environment_id)
    result = verification.verify(environment, fence=fence)
    return result.model_dump(mode="json")


def handle_baseline_build(
    job: ProductJobRecordV1,
    environments: EnvironmentRepositoryV1,
    services: ServiceCatalogRepositoryV1,
    capabilities: CapabilityMatrixRepositoryV1,
    baselines: HistoricalBaselineServiceV1,
    *,
    fence: JobLeaseFenceV1 | None = None,
) -> dict[str, Any]:
    environment_id = str(job.payload.get("environment_id", ""))
    raw_request = job.payload.get("request")
    if not isinstance(raw_request, dict):
        raise ProductError(
            "INVALID_REQUEST",
            "The baseline job payload is invalid.",
        )
    request = BaselineJobCreateV1.model_validate(raw_request)
    result = baselines.build(
        environment=environments.get(environment_id),
        identity_map=services.get_map(environment_id),
        capability_matrix=capabilities.get(environment_id),
        request=request,
        baseline_id=f"base-{job.job_id.removeprefix('job-')}",
        built_at=datetime.fromtimestamp(job.created_at, UTC),
        fence=fence,
    )
    payload = result.model_dump(mode="json")
    readiness_audit = baselines.get_readiness_audit_v023_optional(
        result.baseline_id
    )
    if readiness_audit is not None:
        payload["readiness_audit_v023"] = readiness_audit.model_dump(mode="json")
    return payload


def handle_incident_diagnosis(
    job: ProductJobRecordV1,
    incidents: IncidentRepositoryV1,
    diagnoses: DiagnosisRepositoryV1,
    environments: EnvironmentRepositoryV1,
    services: ServiceCatalogRepositoryV1,
    capabilities: CapabilityMatrixRepositoryV1,
    baselines: Any,
    read_backend: ProductReadBackendV1,
    bridge: ProductDiagnosisBridgeV1,
    *,
    fence: JobLeaseFenceV1,
    stage_pipeline_v02322: DiagnosisPipelineV02322 | None = None,
    loaded_incident_v02322: IncidentRecordV1 | None = None,
) -> dict[str, Any]:
    def run_stage(stage, input_binding_sha256, operation):
        if stage_pipeline_v02322 is None:
            return operation()
        return stage_pipeline_v02322.run(
            stage,
            input_binding_sha256=input_binding_sha256,
            operation=operation,
        )

    incident_id = str(job.payload.get("incident_id", ""))
    existing = diagnoses.get_optional(incident_id)
    if existing is not None:
        diagnoses.evidence_index(incident_id)
        return existing.model_dump(mode="json")
    incident = loaded_incident_v02322 or run_stage(
        DiagnosisPipelineStageV02322.INCIDENT_LOAD_STARTED,
        semantic_sha256_v22(job.payload),
        lambda: incidents.get(incident_id),
    )
    if loaded_incident_v02322 is None:
        run_stage(
            DiagnosisPipelineStageV02322.INCIDENT_LOADED,
            incident.incident_sha256,
            lambda: incident,
        )
    baseline = run_stage(
        DiagnosisPipelineStageV02322.BASELINE_BINDING_STARTED,
        incident.baseline_sha256,
        lambda: baselines.get_optional(incident.baseline_id),
    )
    if baseline is None or baseline.baseline_sha256 != incident.baseline_sha256:
        raise ProductError(
            "INCIDENT_BASELINE_BINDING_INVALID",
            "The incident's frozen baseline binding is unavailable.",
        )
    run_stage(
        DiagnosisPipelineStageV02322.BASELINE_BOUND,
        baseline.baseline_sha256,
        lambda: baseline,
    )
    if stage_pipeline_v02322 is not None:
        stage_pipeline_v02322.bind_artifacts(
            baseline_sha256=baseline.baseline_sha256
        )
    identity_map = run_stage(
        DiagnosisPipelineStageV02322.SERVICE_IDENTITY_BINDING_STARTED,
        incident.service_identity_sha256,
        lambda: services.get_map(incident.environment_id),
    )
    if identity_map.identity_sha256 != incident.service_identity_sha256:
        raise ProductError(
            "INCIDENT_SERVICE_BINDING_INVALID",
            "The incident's frozen service identity binding has changed.",
        )
    run_stage(
        DiagnosisPipelineStageV02322.SERVICE_IDENTITY_BOUND,
        identity_map.identity_sha256,
        lambda: identity_map,
    )
    if stage_pipeline_v02322 is not None:
        stage_pipeline_v02322.bind_artifacts(
            identity_sha256=identity_map.identity_sha256
        )
    capability_matrix = run_stage(
        DiagnosisPipelineStageV02322.CAPABILITY_BINDING_STARTED,
        incident.source_capability_sha256,
        lambda: capabilities.get(incident.environment_id),
    )
    if capability_matrix.capability_sha256 != incident.source_capability_sha256:
        raise ProductError(
            "INCIDENT_CAPABILITY_BINDING_INVALID",
            "The incident's frozen capability binding has changed.",
        )
    run_stage(
        DiagnosisPipelineStageV02322.CAPABILITY_BOUND,
        capability_matrix.capability_sha256,
        lambda: capability_matrix,
    )
    if stage_pipeline_v02322 is not None:
        stage_pipeline_v02322.bind_artifacts(
            capability_sha256=capability_matrix.capability_sha256
        )
    environment = run_stage(
        DiagnosisPipelineStageV02322.ENVIRONMENT_LOAD_STARTED,
        incident.incident_sha256,
        lambda: environments.get(incident.environment_id),
    )
    run_stage(
        DiagnosisPipelineStageV02322.ENVIRONMENT_LOADED,
        semantic_sha256_v22(environment.model_dump(mode="json")),
        lambda: environment,
    )
    context_v02322 = DiagnosisPipelineContextV02322.build(
        incident_id=incident.incident_id,
        incident_sha256=incident.incident_sha256,
        baseline_sha256=baseline.baseline_sha256,
        identity_sha256=identity_map.identity_sha256,
        capability_sha256=capability_matrix.capability_sha256,
        environment_sha256=semantic_sha256_v22(environment.model_dump(mode="json")),
    )
    acquisition = run_stage(
        DiagnosisPipelineStageV02322.READ_ACQUISITION_STARTED,
        context_v02322.context_sha256,
        lambda: read_backend.acquire(
            incident=incident,
            environment=environment,
            identity_map=identity_map,
            capability_matrix=capability_matrix,
            topology_edges=tuple(
                (item.parent_service, item.child_service)
                for item in baseline.topology_edges
            ),
        ),
    )
    acquisition_artifact_v02322 = DiagnosisAcquisitionArtifactV02322.build(
        incident_id=incident.incident_id,
        raw_outcomes_sha256=semantic_sha256_v22(
            [item.model_dump(mode="json") for item in acquisition.raw_outcomes]
        ),
        memory_outcomes_sha256=semantic_sha256_v22(
            [item.model_dump(mode="json") for item in acquisition.memory_outcomes]
        ),
        read_snapshots_sha256=semantic_sha256_v22(list(acquisition.snapshots)),
        source_coverage_sha256=semantic_sha256_v22(
            {
                source.value: list(services)
                for source, services in sorted(
                    acquisition.covered_services_by_source.items(),
                    key=lambda item: item[0].value,
                )
            }
        ),
        capability_observations_sha256=semantic_sha256_v22(
            [
                item.model_dump(mode="json")
                for item in acquisition.capability_observations_v0232
            ]
        ),
        limitation_candidates_sha256=semantic_sha256_v22(
            [
                item.model_dump(mode="json")
                for item in acquisition.capability_limitation_candidates_v0232
            ]
        ),
    )
    acquisition_sha256 = acquisition_artifact_v02322.acquisition_sha256
    if stage_pipeline_v02322 is not None:
        stage_pipeline_v02322.bind_artifacts(
            read_acquisition_sha256=acquisition_sha256
        )
    run_stage(
        DiagnosisPipelineStageV02322.READ_ACQUISITION_COMPLETED,
        acquisition_sha256,
        lambda: acquisition,
    )
    diagnosed = run_stage(
        DiagnosisPipelineStageV02322.BRIDGE_DIAGNOSIS_STARTED,
        acquisition_sha256,
        lambda: bridge.diagnose(
            incident=incident,
            baseline=baseline,
            identity_map=identity_map,
            acquisition=acquisition,
            diagnosis_id=f"diag-{job.job_id.removeprefix('job-')}",
            created_at=datetime.fromtimestamp(job.created_at, UTC),
        ),
    )
    result, observations, decision_trace_v0232 = diagnosed
    bridge_artifact_v02322 = DiagnosisBridgeArtifactV02322.build(
        incident_id=incident.incident_id,
        diagnosis_id=result.diagnosis_id,
        result_sha256=result.result_sha256,
        observations_sha256=semantic_sha256_v22(list(observations)),
        decision_trace_sha256=decision_trace_v0232.trace_sha256,
    )
    if stage_pipeline_v02322 is not None:
        stage_pipeline_v02322.bind_artifacts(
            bridge_output_sha256=bridge_artifact_v02322.bridge_sha256
        )
    run_stage(
        DiagnosisPipelineStageV02322.BRIDGE_DIAGNOSIS_COMPLETED,
        bridge_artifact_v02322.bridge_sha256,
        lambda: bridge_artifact_v02322,
    )
    stored = diagnoses.put(
        result=result,
        observations=observations,
        fence=fence,
        decision_trace_v0232=decision_trace_v0232,
        limitation_candidates_v0232=(
            acquisition.capability_limitation_candidates_v0232
        ),
        bridge_artifact_v02322=bridge_artifact_v02322,
        stage_pipeline_v02322=stage_pipeline_v02322,
    )
    return stored.model_dump(mode="json")


__all__ = (
    "handle_baseline_build",
    "handle_environment_verify",
    "handle_fixture_environment_verify",
    "handle_incident_diagnosis",
)
