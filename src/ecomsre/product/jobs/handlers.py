"""Incremental Product job handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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
) -> dict[str, Any]:
    incident_id = str(job.payload.get("incident_id", ""))
    existing = diagnoses.get_optional(incident_id)
    if existing is not None:
        diagnoses.evidence_index(incident_id)
        return existing.model_dump(mode="json")
    incident = incidents.get(incident_id)
    baseline = baselines.get_optional(incident.baseline_id)
    if baseline is None or baseline.baseline_sha256 != incident.baseline_sha256:
        raise ProductError(
            "INCIDENT_BASELINE_BINDING_INVALID",
            "The incident's frozen baseline binding is unavailable.",
        )
    identity_map = services.get_map(incident.environment_id)
    capability_matrix = capabilities.get(incident.environment_id)
    if identity_map.identity_sha256 != incident.service_identity_sha256:
        raise ProductError(
            "INCIDENT_SERVICE_BINDING_INVALID",
            "The incident's frozen service identity binding has changed.",
        )
    if capability_matrix.capability_sha256 != incident.source_capability_sha256:
        raise ProductError(
            "INCIDENT_CAPABILITY_BINDING_INVALID",
            "The incident's frozen capability binding has changed.",
        )
    acquisition = read_backend.acquire(
        incident=incident,
        environment=environments.get(incident.environment_id),
        identity_map=identity_map,
        capability_matrix=capability_matrix,
        topology_edges=tuple(
            (item.parent_service, item.child_service) for item in baseline.topology_edges
        ),
    )
    result, observations, decision_trace_v0232 = bridge.diagnose(
        incident=incident,
        baseline=baseline,
        identity_map=identity_map,
        acquisition=acquisition,
        diagnosis_id=f"diag-{job.job_id.removeprefix('job-')}",
        created_at=datetime.fromtimestamp(job.created_at, UTC),
    )
    stored = diagnoses.put(
        result=result,
        observations=observations,
        fence=fence,
        decision_trace_v0232=decision_trace_v0232,
        limitation_candidates_v0232=(
            acquisition.capability_limitation_candidates_v0232
        ),
    )
    return stored.model_dump(mode="json")


__all__ = (
    "handle_baseline_build",
    "handle_environment_verify",
    "handle_fixture_environment_verify",
    "handle_incident_diagnosis",
)
