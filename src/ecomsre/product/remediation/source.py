"""Read-only Product repository integration for remediation candidate projection."""

from ecomsre.product.baselines import BaselineRepositoryV1
from ecomsre.product.environment.capabilities import CapabilityMatrixRepositoryV1
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.incidents.repository import (
    DiagnosisRepositoryV1,
    IncidentRepositoryV1,
)
from ecomsre.product.remediation.candidate_filter import project_candidate
from ecomsre.product.remediation.contracts import (
    CandidateProjectionV1,
    RemediationRegistryV1,
)
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


def project_for_incident(
    incident_id: str,
    *,
    store: SqliteStoreV1,
    objects: ContentAddressedObjectStoreV1,
    registry: RemediationRegistryV1,
    expected_registry_sha256: str,
) -> CandidateProjectionV1:
    baselines = BaselineRepositoryV1(store)
    environments = EnvironmentRepositoryV1(store)
    services = ServiceCatalogRepositoryV1(store)
    capabilities = CapabilityMatrixRepositoryV1(store)
    incidents = IncidentRepositoryV1(
        store,
        environments=environments,
        services=services,
        capabilities=capabilities,
        baselines=baselines,
    )
    diagnoses = DiagnosisRepositoryV1(store, objects)
    incident = incidents.get(incident_id)
    environments.get(incident.environment_id)
    return project_candidate(
        incident=incident,
        diagnosis=diagnoses.get(incident_id),
        evidence=diagnoses.evidence(incident_id),
        index=diagnoses.evidence_index(incident_id),
        baseline=baselines.get_active(incident.environment_id),
        identity=services.get_map(incident.environment_id),
        capability=capabilities.get(incident.environment_id),
        registry=registry,
        expected_registry_sha256=expected_registry_sha256,
        objects=objects,
    )
