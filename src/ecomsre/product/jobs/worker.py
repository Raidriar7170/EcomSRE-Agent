"""Separate SQLite-backed Product worker process."""

from __future__ import annotations

import os
import secrets
import time
import math

from ecomsre.product.baselines import BaselineRepositoryV1, HistoricalBaselineServiceV1
from ecomsre.product.changes import ChangeEventRepositoryV1
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.registry import ConnectorRegistryV1
from ecomsre.product.environment.capabilities import CapabilityMatrixRepositoryV1
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.environment.verification import EnvironmentVerificationServiceV1
from ecomsre.product.errors import ProductError
from ecomsre.product.incidents.diagnosis_bridge import ProductDiagnosisBridgeV1
from ecomsre.product.incidents.extensions import ProductExtensionMatcherV1
from ecomsre.product.incidents.read_backend import ProductReadBackendV1
from ecomsre.product.incidents.repository import (
    DiagnosisRepositoryV1,
    IncidentRepositoryV1,
)
from ecomsre.product.jobs.contracts import JobLeaseFenceV1, ProductJobTypeV1
from ecomsre.product.jobs.handlers import (
    handle_baseline_build,
    handle_environment_verify,
    handle_incident_diagnosis,
)
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.knowledge.repository import KnowledgeRepositoryV1
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.pilot.runtime_authority_v02 import (
    load_pilot_runtime_authority_v02,
)
from ecomsre.product.pilot.baseline_audit_v021 import (
    BaselineReadinessAuditRepositoryV021,
)
from ecomsre.product.pilot.baseline_readiness_v023 import (
    ProductBaselineReadinessAuditRepositoryV023,
)
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from ecomsre.product.telemetry.metrics import ProductMetricsV1


_LEGACY_FIXTURE_RESULT_DATASETS = frozenset({"increment-1", "product-increment-1"})


def should_ingest_open_world_v023(
    *,
    diagnosis_terminal: str,
    incident_labels: dict[str, str],
) -> bool:
    """Keep No-Fault false incidents without creating Knowledge artifacts."""

    return diagnosis_terminal == "OPEN_WORLD" and incident_labels.get("fault") != "none"


def run_one_job(
    settings: ProductSettingsV1,
    *,
    worker_id: str,
    now: float | None = None,
) -> bool:
    store = SqliteStoreV1(settings.sqlite_path)
    jobs = JobRepositoryV1(store)
    environments = EnvironmentRepositoryV1(store)
    services = ServiceCatalogRepositoryV1(store)
    capabilities = CapabilityMatrixRepositoryV1(store)
    baseline_repository = BaselineRepositoryV1(store)
    baseline_audit_repository = BaselineReadinessAuditRepositoryV021(store)
    baseline_audit_repository_v023 = ProductBaselineReadinessAuditRepositoryV023(
        store
    )
    change_repository = ChangeEventRepositoryV1(store)
    object_store = ContentAddressedObjectStoreV1(
        settings.object_store_root,
        metadata_store=store,
    )
    metrics = ProductMetricsV1(store)
    incidents = IncidentRepositoryV1(
        store,
        environments=environments,
        services=services,
        capabilities=capabilities,
        baselines=baseline_repository,
    )
    diagnoses = DiagnosisRepositoryV1(store, object_store)
    knowledge = KnowledgeRepositoryV1(store, object_store)
    job = jobs.claim_next(
        worker_id,
        lease_seconds=settings.job_lease_seconds,
        now=now,
    )
    if job is None:
        return False

    def renew_lease() -> None:
        jobs.renew_lease(
            job.job_id,
            worker_id,
            job.attempt_count,
            lease_seconds=settings.job_lease_seconds,
            now=now,
        )

    connector_registry = ConnectorRegistryV1(
        credential_resolver=CredentialResolverV1(),
        timeout_seconds=settings.connector_timeout_seconds,
        before_request=renew_lease,
        data_root=settings.data_root,
    )
    verification = EnvironmentVerificationServiceV1(
        services=services,
        capabilities=capabilities,
        connectors=connector_registry,
    )
    baselines = HistoricalBaselineServiceV1(
        connectors=connector_registry,
        repository=baseline_repository,
        maximum_records_per_source=settings.maximum_evidence_records_per_source,
        audit_repository=baseline_audit_repository,
        audit_repository_v023=baseline_audit_repository_v023,
    )
    pilot_runtime_authority = (
        None
        if settings.pilot_runtime_authority_path is None
        else load_pilot_runtime_authority_v02(settings.pilot_runtime_authority_path)
    )
    read_backend = ProductReadBackendV1(
        connectors=connector_registry,
        changes=change_repository,
        metrics=metrics,
        pilot_runtime_authority=pilot_runtime_authority,
    )
    fence = JobLeaseFenceV1(
        job_id=job.job_id,
        claimed_by=worker_id,
        attempt_count=job.attempt_count,
        checked_at=now,
    )
    try:
        if job.job_type is ProductJobTypeV1.ENVIRONMENT_VERIFY:
            environment = environments.get(str(job.payload.get("environment_id", "")))
            legacy_result_shape = bool(environment.connector_configs) and all(
                connector.kind.value == "FIXTURE"
                and str(connector.settings.get("dataset", ""))
                in _LEGACY_FIXTURE_RESULT_DATASETS
                for connector in environment.connector_configs
            )
            verified = handle_environment_verify(
                job,
                environments,
                verification,
                fence=fence,
            )
            result = (
                {
                    "environment_id": environment.environment_id,
                    "fixture_verified": True,
                }
                if legacy_result_shape
                else verified
            )
        elif job.job_type is ProductJobTypeV1.BASELINE_BUILD:
            result = handle_baseline_build(
                job,
                environments,
                services,
                capabilities,
                baselines,
                fence=fence,
            )
        elif job.job_type is ProductJobTypeV1.DIAGNOSIS:
            incident = incidents.get(str(job.payload.get("incident_id", "")))
            result = handle_incident_diagnosis(
                job,
                incidents,
                diagnoses,
                environments,
                services,
                capabilities,
                baseline_repository,
                read_backend,
                ProductDiagnosisBridgeV1(
                    ProductExtensionMatcherV1(
                        knowledge.active_extensions(incident.environment_id)
                    )
                ),
                fence=fence,
            )
            if should_ingest_open_world_v023(
                diagnosis_terminal=str(result.get("terminal")),
                incident_labels=incident.labels,
            ):
                family = knowledge.ingest_open_world(
                    incident.incident_id,
                    fence=fence,
                )
                metrics.increment(
                    "ecomsre_fault_families_total",
                    {
                        "environment_id": family.environment_id,
                        "status": family.status.value,
                    },
                )
        else:
            raise ProductError(
                "INTERNAL_CONTRACT_FAILURE",
                "No handler is registered for this job type.",
            )
        jobs.succeed(
            job.job_id,
            worker_id,
            job.attempt_count,
            result,
            now=now,
        )
        metrics.increment(
            "ecomsre_jobs_total",
            {"job_type": job.job_type.value, "status": "SUCCEEDED"},
        )
        metrics.increment(
            "ecomsre_job_duration_seconds",
            {"job_type": job.job_type.value, "status": "SUCCEEDED"},
            amount=max(
                0,
                math.ceil((time.time() if now is None else now) - job.created_at),
            ),
        )
        if job.job_type is ProductJobTypeV1.DIAGNOSIS:
            terminal = str(result.get("terminal", "UNKNOWN"))
            metrics.increment(
                "ecomsre_diagnosis_terminals_total",
                {"terminal": terminal},
            )
            if terminal == "OPEN_WORLD":
                metrics.increment(
                    "ecomsre_open_world_reports_total",
                    {"terminal": terminal},
                )
    except ProductError as exc:
        if exc.code == "JOB_LEASE_LOST":
            return True
        try:
            jobs.fail(
                job.job_id,
                worker_id,
                job.attempt_count,
                exc.code,
                now=now,
            )
            metrics.increment(
                "ecomsre_jobs_total",
                {"job_type": job.job_type.value, "status": "FAILED"},
            )
            metrics.increment(
                "ecomsre_job_duration_seconds",
                {"job_type": job.job_type.value, "status": "FAILED"},
                amount=max(
                    0,
                    math.ceil((time.time() if now is None else now) - job.created_at),
                ),
            )
        except ProductError as finish_error:
            if finish_error.code != "JOB_LEASE_LOST":
                raise
    except Exception:
        try:
            jobs.fail(
                job.job_id,
                worker_id,
                job.attempt_count,
                "INTERNAL_CONTRACT_FAILURE",
                now=now,
            )
        except ProductError as finish_error:
            if finish_error.code != "JOB_LEASE_LOST":
                raise
    return True


def run_worker(settings: ProductSettingsV1) -> None:
    worker_id = f"worker-{os.getpid()}-{secrets.token_hex(4)}"
    while True:
        worked = run_one_job(settings, worker_id=worker_id)
        if not worked:
            time.sleep(settings.worker_poll_seconds)


def main() -> None:
    run_worker(ProductSettingsV1.from_environment())


if __name__ == "__main__":
    main()


__all__ = ("main", "run_one_job", "run_worker")
