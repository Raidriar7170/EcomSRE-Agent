"""Transactional environment-scoped family, review, and registry persistence."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any

from ecomsre.dta_v2.v22.memory import (
    PredicateKindV22,
    RuntimeReadOutcomeV22,
    build_memory_views_v22,
)
from ecomsre.dta_v2.v22.predicates import (
    MechanismV22,
    build_default_evidence_support_policy_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
from ecomsre.dta_v2.v23.extension_runtime_v234 import (
    ExtensionRuntimeInputV234,
    ExtensionSourceCoverageV234,
    ExtensionSupportPolicyV234,
)
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    extract_generic_anomalies_v23,
)
from ecomsre.dta_v2.v23.registration_compiler_v234 import CompiledFaultRegistrationV234
from ecomsre.dta_v2.v23.registration_contracts_v234 import hashed_model_v234
from ecomsre.product.baselines import EnvironmentBaselineV1
from ecomsre.product.contracts import EnvironmentRecordV1
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.capabilities import (
    CapabilityMatrixRepositoryV1,
    EnvironmentCapabilityMatrixV1,
    SourceCapabilityStatusV1,
)
from ecomsre.product.errors import ProductError, not_found
from ecomsre.product.ids import new_product_id
from ecomsre.product.incidents.contracts import (
    DiagnosisResultV1,
    DiagnosisTerminalV1,
    EvidenceBundleV1,
    EvidenceObjectV1,
    IncidentRecordV1,
)
from ecomsre.product.incidents.extensions import (
    ProductExtensionRegistrationV1,
    build_product_extension_runtime_input_v1,
)
from ecomsre.product.incidents.queue_action import build_queue_lag_action_v030
from ecomsre.product.incidents.anomaly_policy import extract_product_anomalies_v1
from ecomsre.product.jobs.contracts import JobLeaseFenceV1
from ecomsre.product.jobs.fencing import require_live_job_fence
from ecomsre.product.knowledge.compiler import (
    build_product_shadow_candidate_v1,
    compile_product_registration_v1,
)
from ecomsre.product.knowledge.metric_coverage import complete_queue_aware_metrics_v1
from ecomsre.product.knowledge.contracts import (
    EnvironmentExtensionRegistryEntryV1,
    FamilyRegistrationDraftV1,
    FaultFamilyListV1,
    FaultFamilyMergeV1,
    FaultFamilyStatusV1,
    FaultFamilyV1,
    FingerprintObservationV1,
    HumanReviewCreateV1,
    HumanReviewV1,
    IncidentFingerprintV1,
    PredicateCellStateV1,
    PredicateMatrixCellV1,
    PredicateMatrixRowKindV1,
    PredicateMatrixRowV1,
    PredicateMatrixV1,
    PromotionCreateV1,
    PromotionRecordV1,
    RegistrationDraftCreateV1,
    RegistrationImplementationModeV1,
    RevocationCreateV1,
    RevocationRecordV1,
    ReviewDecisionV1,
    ShadowEvaluationV1,
    ShadowCaseOriginV1,
    ShadowCaseOutcomeV1,
    ShadowEvaluationStratumV1,
)
from ecomsre.product.knowledge.runtime import (
    CLUSTER_ASSIGNMENT_THRESHOLD_V1,
    build_incident_fingerprint_v1,
    build_predicate_matrix_v1,
    cluster_similarity_v1,
    evaluate_shadow_gate_v1,
    mine_candidate_clauses_v1,
)
from ecomsre.product.pilot.leakage_guard_v02 import normalized_observed_log_tokens_v02
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


_SOURCE_BY_ANOMALY = {
    kind.value: (
        "METRICS"
        if kind.value.startswith("METRIC_")
        else "RUNTIME"
        if kind.value.startswith("RUNTIME_")
        else "RESOURCES"
        if kind.value.startswith("RESOURCE_")
        else "TRACES"
        if kind.value.startswith("TRACE_")
        else "LOGS"
        if kind.value.startswith("LOG_")
        else "CHANGES"
        if kind.value.startswith("RECENT_CHANGE")
        else "RUNTIME"
    )
    for kind in GenericAnomalyKindV23
}
_SOURCE_BY_CORE = {
    PredicateKindV22.RUNTIME_HEALTHY.value: "RUNTIME",
    PredicateKindV22.RUNTIME_NOT_RUNNING.value: "RUNTIME",
    PredicateKindV22.RUNTIME_UNHEALTHY.value: "RUNTIME",
    PredicateKindV22.RUNTIME_RESTART_PRESSURE.value: "RUNTIME",
}
_CONFUSABLE_CORE_BY_DOMAIN = {
    "CONFIGURATION": frozenset({MechanismV22.CONFIGURATION_ERROR.value}),
    "RUNTIME": frozenset({MechanismV22.SERVICE_UNAVAILABLE.value}),
    "RESOURCE": frozenset(
        {MechanismV22.CPU_SATURATION.value, MechanismV22.MEMORY_LEAK.value}
    ),
    "DEPENDENCY": frozenset({MechanismV22.DEPENDENCY_LATENCY.value}),
}


@dataclass(frozen=True)
class _ShadowRuntimeMaterialV1:
    incident: IncidentRecordV1
    baseline: EnvironmentBaselineV1
    raw_outcomes: tuple[ReadOutcomeV22, ...]
    memory_outcomes: tuple[ReadOutcomeV22 | RuntimeReadOutcomeV22, ...]
    runtime_input: ExtensionRuntimeInputV234
    complete_sources: tuple[str, ...] = ()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _hashed(model: type[Any], payload: dict[str, Any], field: str):
    draft = model.model_construct(**payload, **{field: "0" * 64})
    return model.model_validate(
        {
            **payload,
            field: semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={field})
            ),
        }
    )


def _records(bundle: EvidenceBundleV1) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    for item in bundle.objects:
        outcome = item.payload.get("read_outcome", {})
        if isinstance(outcome, dict):
            values = outcome.get("records", ())
            if isinstance(values, list):
                records.extend(value for value in values if isinstance(value, dict))
    return tuple(records)


def _anomaly_kinds(result: DiagnosisResultV1) -> tuple[str, ...]:
    report = result.provisional_report or {}
    identifiers = report.get("unexplained_anomaly_ids", ())
    found = []
    for kind in GenericAnomalyKindV23:
        token = kind.value.casefold().replace("_", "-")
        if any(token in str(identifier) for identifier in identifiers):
            found.append(kind.value)
    return tuple(sorted(found))


def _complete_source_coverage_v1(
    *,
    incident: IncidentRecordV1,
    evidence: EvidenceBundleV1,
    capability_matrix: EnvironmentCapabilityMatrixV1,
    environment: EnvironmentRecordV1 | None = None,
    baseline: EnvironmentBaselineV1 | None = None,
) -> tuple[str, ...]:
    if (
        capability_matrix.environment_id != incident.environment_id
        or capability_matrix.capability_sha256 != incident.source_capability_sha256
    ):
        return ()
    candidates = set(incident.candidate_logical_services)
    target_complete_sources = {
        item.source.value
        for item in capability_matrix.sources
        if item.status is SourceCapabilityStatusV1.AVAILABLE
        and item.target_complete_coverage
        and candidates.issubset(item.covered_services)
    }
    if any(
        source.source is EvidenceSourceV22.METRICS
        and source.status is SourceCapabilityStatusV1.AVAILABLE
        and candidates.issubset(source.covered_services)
        for source in capability_matrix.sources
    ) and complete_queue_aware_metrics_v1(
        incident=incident, evidence=evidence, environment=environment, baseline=baseline
    ):
        target_complete_sources.add("METRICS")
    requested_by_source: dict[str, set[str]] = {}
    invalid_sources: set[str] = set()
    for item in evidence.objects:
        action = item.payload.get("action", {})
        result = item.payload.get("connector_result", {})
        outcome = item.payload.get("read_outcome", {})
        if not all(isinstance(value, dict) for value in (action, result, outcome)):
            invalid_sources.add(item.source.value)
            continue
        source = item.source.value
        action_targets = set(action.get("target_services", ()))
        requested = set(result.get("requested_services", ()))
        covered = set(result.get("covered_services", ()))
        valid = (
            str(outcome.get("status", "")).startswith("SUCCESS_")
            and result.get("truncated") is False
            and action_targets == requested
            and requested.issubset(covered)
            and bool(requested)
            and requested.issubset(candidates)
        )
        if not valid:
            invalid_sources.add(source)
            continue
        requested_by_source.setdefault(source, set()).update(requested)
    return tuple(
        sorted(
            source
            for source, requested in requested_by_source.items()
            if source not in invalid_sources
            and source in target_complete_sources
            and requested == candidates
        )
    )


def build_product_fingerprint_observation_v1(
    *,
    incident: IncidentRecordV1,
    result: DiagnosisResultV1,
    evidence: EvidenceBundleV1,
    baseline: EnvironmentBaselineV1,
    capability_matrix: EnvironmentCapabilityMatrixV1,
    environment: EnvironmentRecordV1 | None = None,
) -> FingerprintObservationV1:
    records = _records(evidence)
    evidence_sources = tuple(sorted({item.source.value for item in evidence.objects}))
    coverage = _complete_source_coverage_v1(
        incident=incident,
        evidence=evidence,
        capability_matrix=capability_matrix,
        environment=environment,
        baseline=baseline,
    )
    anomaly_kinds = set(_anomaly_kinds(result))
    metric_outcomes = complete_queue_aware_metrics_v1(
        incident=incident, evidence=evidence, environment=environment, baseline=baseline
    )
    if metric_outcomes:
        metric_memory, _ = build_memory_views_v22(
            outcomes=metric_outcomes, baseline=baseline.v22_baseline_profile,
            observed_at=incident.diagnosis_observed_at, top_k=64,
        )
        # Known/no-incident routing can omit a provisional report. Its omission
        # is not proof of absence: derive observable metric symptoms from reads.
        anomaly_kinds.update(a.kind.value for a in extract_generic_anomalies_v23(
            memory=metric_memory, candidate_services=incident.candidate_logical_services,
            healthy_noise_guard_v024=True,
        ))
    runtime: list[str] = []
    resources: list[str] = []
    log_tokens: list[str] = []
    trace_roles: list[str] = []
    for record in records:
        schema = str(record.get("schema_version", ""))
        service = str(record.get("service", "unknown"))
        if "runtime-record" in schema:
            runtime.append(
                f"{service}:{record.get('state', 'UNKNOWN')}:{record.get('healthy', 'UNKNOWN')}"
            )
        elif "resource-usage" in schema:
            samples = record.get("samples", ())
            cpu_values = [
                float(sample["cpu_percent"])
                for sample in samples
                if isinstance(sample, dict) and sample.get("cpu_percent") is not None
            ]
            bucket = "CPU_HIGH" if cpu_values and max(cpu_values) >= 90.0 else "CPU_NORMAL"
            resources.append(f"{service}:{bucket}")
        elif "log-record" in schema:
            message = str(record.get("normalized_template") or record.get("message") or "")
            log_tokens.extend(normalized_observed_log_tokens_v02(message))
        elif "trace-span" in schema and record.get("first_error_location") is True:
            trace_roles.append(f"{service}:TARGET")
    return FingerprintObservationV1(
        environment_id=incident.environment_id,
        incident_id=incident.incident_id,
        root_service_ids=result.root_service_ids,
        broad_domain=result.broad_domain or "UNKNOWN",
        generic_anomaly_kinds=tuple(sorted(anomaly_kinds)),
        evidence_sources=evidence_sources,
        topology_edges=tuple(
            sorted(
                (edge.parent_service, edge.child_service)
                for edge in baseline.topology_edges
                if {
                    edge.parent_service,
                    edge.child_service,
                }.issubset(set(incident.candidate_logical_services))
            )
        ),
        runtime_state_signature=tuple(sorted(set(runtime))),
        resource_state_signature=tuple(sorted(set(resources))),
        normalized_log_tokens=tuple(sorted(set(log_tokens))),
        trace_first_error_roles=tuple(sorted(set(trace_roles))),
        source_coverage=tuple(sorted(set(coverage))),
    )


def _present_predicates(fingerprint: IncidentFingerprintV1) -> set[str]:
    values = {f"ga:{kind}" for kind in fingerprint.generic_anomaly_kinds}
    for item in fingerprint.runtime_state_signature:
        if ":RUNNING:True" in item:
            values.add("core:RUNTIME_HEALTHY")
        if ":EXITED:" in item:
            values.add("core:RUNTIME_NOT_RUNNING")
    return values


def _predicate_source(predicate_id: str) -> str:
    namespace, value = predicate_id.split(":", 1)
    if namespace == "ga":
        return _SOURCE_BY_ANOMALY[value]
    if namespace == "core":
        return _SOURCE_BY_CORE[value]
    raise ValueError("unknown Product predicate column")


class KnowledgeRepositoryV1:
    def __init__(
        self,
        store: SqliteStoreV1,
        object_store: ContentAddressedObjectStoreV1,
    ) -> None:
        self.store = store
        self.object_store = object_store

    def _evidence(self, incident_id: str, diagnosis_id: str) -> EvidenceBundleV1:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT object_sha256, evidence_ref, source, action_id, role "
                "FROM diagnosis_evidence_links WHERE diagnosis_id = ? ORDER BY evidence_ref",
                (diagnosis_id,),
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
        result = self._diagnosis(incident_id)
        return EvidenceBundleV1(
            incident_id=incident_id,
            diagnosis_id=diagnosis_id,
            objects=objects,
            supporting_evidence_refs=result.supporting_evidence_refs,
            contradicting_evidence_refs=result.contradicting_evidence_refs,
        )

    def _incident(self, incident_id: str) -> IncidentRecordV1:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
        if row is None:
            raise not_found("INCIDENT_NOT_FOUND", "The requested incident does not exist.")
        return IncidentRecordV1.model_validate_json(row["payload_json"])

    def _diagnosis(self, incident_id: str) -> DiagnosisResultV1:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM diagnosis_results WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
        if row is None:
            raise not_found("DIAGNOSIS_NOT_FOUND", "The incident has no diagnosis result.")
        return DiagnosisResultV1.model_validate_json(row["payload_json"])

    def _baseline(self, baseline_id: str) -> EnvironmentBaselineV1:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json, active FROM baseline_versions WHERE baseline_id = ?",
                (baseline_id,),
            ).fetchone()
        if row is None:
            raise not_found("BASELINE_NOT_FOUND", "The incident baseline does not exist.")
        payload = json.loads(row["payload_json"])
        payload["active"] = bool(row["active"])
        return EnvironmentBaselineV1.model_validate_json(_json(payload))

    def fingerprint_for(self, incident_id: str) -> IncidentFingerprintV1:
        incident = self._incident(incident_id)
        result = self._diagnosis(incident_id)
        evidence = self._evidence(incident_id, result.diagnosis_id)
        return build_incident_fingerprint_v1(
            build_product_fingerprint_observation_v1(
                incident=incident,
                result=result,
                evidence=evidence,
                baseline=self._baseline(incident.baseline_id),
                capability_matrix=self._capability_matrix(incident),
                environment=EnvironmentRepositoryV1(self.store).get(incident.environment_id),
            )
        )

    def _capability_matrix(
        self,
        incident: IncidentRecordV1,
    ) -> EnvironmentCapabilityMatrixV1:
        matrix = CapabilityMatrixRepositoryV1(self.store).get(
            incident.environment_id
        )
        if matrix.capability_sha256 != incident.source_capability_sha256:
            raise ProductError(
                "INCIDENT_CAPABILITY_BINDING_MISMATCH",
                "The incident capability binding is no longer available.",
            )
        return matrix

    def _family_from_rows(
        self,
        *,
        family_id: str,
        environment_id: str,
        status: FaultFamilyStatusV1,
        merged_into_family_id: str | None,
        created_at: datetime,
        updated_at: datetime,
        fingerprints: tuple[IncidentFingerprintV1, ...],
    ) -> FaultFamilyV1:
        incidents = tuple(self._incident(item.incident_id) for item in fingerprints)
        windows = {
            (
                item.started_at.isoformat(),
                (item.ended_at or item.diagnosis_observed_at).isoformat(),
            )
            for item in incidents
        }
        root_counts = Counter(root for item in fingerprints for root in item.root_service_ids)
        root_consistency = (
            max(root_counts.values()) / len(fingerprints)
            if fingerprints and root_counts
            else 0.0
        )
        payload = {
            "schema_version": "ecomsre.product.fault-family.v1",
            "family_id": family_id,
            "environment_id": environment_id,
            "status": status,
            "member_incident_ids": tuple(sorted(item.incident_id for item in fingerprints)),
            "member_fingerprint_sha256s": tuple(
                sorted(item.fingerprint_sha256 for item in fingerprints)
            ),
            "distinct_incident_windows": len(windows),
            "root_consistency": round(root_consistency, 12),
            "evidence_source_diversity": len(
                {source for item in fingerprints for source in item.evidence_sources}
            ),
            "merged_into_family_id": merged_into_family_id,
            "created_at": created_at,
            "updated_at": updated_at,
        }
        return _hashed(FaultFamilyV1, payload, "family_sha256")

    def _family_fingerprints(self, family_id: str) -> tuple[IncidentFingerprintV1, ...]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT f.payload_json FROM incident_fingerprints f "
                "JOIN fault_family_members m ON m.fingerprint_id = f.fingerprint_id "
                "WHERE m.family_id = ? ORDER BY f.fingerprint_id",
                (family_id,),
            ).fetchall()
        return tuple(IncidentFingerprintV1.model_validate_json(row["payload_json"]) for row in rows)

    def ingest_open_world(
        self,
        incident_id: str,
        *,
        fence: JobLeaseFenceV1 | None = None,
    ) -> FaultFamilyV1:
        result = self._diagnosis(incident_id)
        if result.terminal is not DiagnosisTerminalV1.OPEN_WORLD:
            raise ProductError(
                "OPEN_WORLD_REQUIRED",
                "Only an OpenWorld incident may enter family clustering.",
            )
        fingerprint = self.fingerprint_for(incident_id)
        fingerprint_id = f"fp-{fingerprint.fingerprint_sha256[:24]}"
        with self.store.connect() as connection:
            existing = connection.execute(
                "SELECT m.family_id FROM incident_fingerprints f "
                "JOIN fault_family_members m ON m.fingerprint_id = f.fingerprint_id "
                "WHERE f.incident_id = ? ORDER BY m.family_id LIMIT 1",
                (incident_id,),
            ).fetchone()
        if existing is not None:
            family = self.get_family(existing["family_id"])
            if family.status in {
                FaultFamilyStatusV1.ACCUMULATING,
                FaultFamilyStatusV1.REVIEW_READY,
            }:
                members = self._family_fingerprints(family.family_id)
                refreshed = self._family_from_rows(
                    family_id=family.family_id,
                    environment_id=family.environment_id,
                    status=family.status,
                    merged_into_family_id=None,
                    created_at=family.created_at,
                    updated_at=_utc_now(),
                    fingerprints=members,
                )
                refreshed_status = (
                    FaultFamilyStatusV1.REVIEW_READY
                    if len(members) >= 2
                    and refreshed.distinct_incident_windows >= 2
                    and refreshed.root_consistency >= 0.50
                    and refreshed.evidence_source_diversity >= 2
                    else FaultFamilyStatusV1.ACCUMULATING
                )
                refreshed = _hashed(
                    FaultFamilyV1,
                    {
                        **refreshed.model_dump(
                            mode="python",
                            exclude={"family_sha256", "status"},
                        ),
                        "status": refreshed_status,
                    },
                    "family_sha256",
                )
                self._put_family(refreshed, fence=fence)
                return refreshed
            return family
        best: tuple[float, FaultFamilyV1] | None = None
        for family in self.list_families(fingerprint.environment_id).items:
            if family.status in {FaultFamilyStatusV1.MERGED, FaultFamilyStatusV1.REJECTED}:
                continue
            score = max(
                (
                    cluster_similarity_v1(fingerprint, member) or 0.0
                    for member in self._family_fingerprints(family.family_id)
                ),
                default=0.0,
            )
            if score >= CLUSTER_ASSIGNMENT_THRESHOLD_V1 and (
                best is None or (score, family.family_id) > (best[0], best[1].family_id)
            ):
                best = (score, family)
        now = _utc_now()
        family_id = best[1].family_id if best else new_product_id("family")
        created_at = best[1].created_at if best else now
        prior_status = best[1].status if best else FaultFamilyStatusV1.ACCUMULATING
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if fence is not None:
                    require_live_job_fence(connection, fence)
                connection.execute(
                    "INSERT INTO incident_fingerprints(fingerprint_id, incident_id, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (fingerprint_id, incident_id, _json(fingerprint.model_dump(mode="json")), now.isoformat()),
                )
                if best is None:
                    placeholder = self._family_from_rows(
                        family_id=family_id,
                        environment_id=fingerprint.environment_id,
                        status=FaultFamilyStatusV1.ACCUMULATING,
                        merged_into_family_id=None,
                        created_at=created_at,
                        updated_at=now,
                        fingerprints=(fingerprint,),
                    )
                    connection.execute(
                        "INSERT INTO fault_families(family_id, environment_id, payload_json, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (family_id, fingerprint.environment_id, _json(placeholder.model_dump(mode="json")), created_at.isoformat(), now.isoformat()),
                    )
                connection.execute(
                    "INSERT INTO fault_family_members(family_id, fingerprint_id, created_at) VALUES (?, ?, ?)",
                    (family_id, fingerprint_id, now.isoformat()),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        members = self._family_fingerprints(family_id)
        readiness = self._family_from_rows(
            family_id=family_id,
            environment_id=fingerprint.environment_id,
            status=prior_status,
            merged_into_family_id=None,
            created_at=created_at,
            updated_at=now,
            fingerprints=members,
        )
        if prior_status in {FaultFamilyStatusV1.ACCUMULATING, FaultFamilyStatusV1.REVIEW_READY}:
            status = (
                FaultFamilyStatusV1.REVIEW_READY
                if len(members) >= 2
                and readiness.distinct_incident_windows >= 2
                and readiness.root_consistency >= 0.50
                and readiness.evidence_source_diversity >= 2
                else FaultFamilyStatusV1.ACCUMULATING
            )
            readiness = readiness.model_copy(update={"status": status})
            readiness = _hashed(
                FaultFamilyV1,
                readiness.model_dump(mode="python", exclude={"family_sha256"}),
                "family_sha256",
            )
        self._put_family(readiness, fence=fence)
        return readiness

    def _put_family(
        self,
        family: FaultFamilyV1,
        *,
        fence: JobLeaseFenceV1 | None = None,
    ) -> None:
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if fence is not None:
                    require_live_job_fence(connection, fence)
                connection.execute(
                    "UPDATE fault_families SET payload_json = ?, updated_at = ? WHERE family_id = ?",
                    (
                        _json(family.model_dump(mode="json")),
                        family.updated_at.isoformat(),
                        family.family_id,
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def get_family(self, family_id: str) -> FaultFamilyV1:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM fault_families WHERE family_id = ?", (family_id,)
            ).fetchone()
        if row is None:
            raise not_found("FAULT_FAMILY_NOT_FOUND", "The fault family does not exist.")
        return FaultFamilyV1.model_validate_json(row["payload_json"])

    def list_families(self, environment_id: str) -> FaultFamilyListV1:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM fault_families WHERE environment_id = ? ORDER BY family_id",
                (environment_id,),
            ).fetchall()
        return FaultFamilyListV1(
            items=tuple(FaultFamilyV1.model_validate_json(row["payload_json"]) for row in rows)
        )

    def review(self, family_id: str, request: HumanReviewCreateV1) -> HumanReviewV1:
        family = self.get_family(family_id)
        if request.decision is ReviewDecisionV1.MERGE_WITH_EXISTING:
            self.merge(
                family_id,
                FaultFamilyMergeV1(
                    target_family_id=str(request.merge_target_family_id),
                    reviewer=request.reviewer,
                    note=request.note,
                    merged_at=request.reviewed_at,
                ),
            )
        payload = {
            "schema_version": "ecomsre.product.human-review.v1",
            "review_id": new_product_id("review"),
            "family_id": family_id,
            **request.model_dump(mode="python"),
        }
        review = _hashed(HumanReviewV1, payload, "review_sha256")
        if request.decision in {
            ReviewDecisionV1.ACCEPT_AS_NEW,
            ReviewDecisionV1.SAVE_AS_INCIDENT_FAMILY,
        }:
            status = FaultFamilyStatusV1.ACCEPTED_SHADOW
        elif request.decision is ReviewDecisionV1.REJECT_AS_NOISE:
            status = FaultFamilyStatusV1.REJECTED
        elif request.decision is ReviewDecisionV1.MERGE_WITH_EXISTING:
            status = FaultFamilyStatusV1.MERGED
        else:
            status = FaultFamilyStatusV1.ACCUMULATING
        updated = _hashed(
            FaultFamilyV1,
            {
                **family.model_dump(mode="python", exclude={"family_sha256", "status", "updated_at"}),
                "status": status,
                "updated_at": request.reviewed_at,
                "merged_into_family_id": request.merge_target_family_id,
            },
            "family_sha256",
        )
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO human_reviews(review_id, family_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
                    (review.review_id, family_id, _json(review.model_dump(mode="json")), request.reviewed_at.isoformat()),
                )
                connection.execute(
                    "UPDATE fault_families SET payload_json = ?, updated_at = ? WHERE family_id = ?",
                    (_json(updated.model_dump(mode="json")), request.reviewed_at.isoformat(), family_id),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return review

    def merge(self, family_id: str, request: FaultFamilyMergeV1) -> FaultFamilyV1:
        source = self.get_family(family_id)
        target = self.get_family(request.target_family_id)
        if source.environment_id != target.environment_id or source.family_id == target.family_id:
            raise ProductError("INVALID_FAMILY_MERGE", "Fault-family merge must remain within one environment.")
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT OR IGNORE INTO fault_family_members(family_id, fingerprint_id, created_at) "
                    "SELECT ?, fingerprint_id, ? FROM fault_family_members WHERE family_id = ?",
                    (target.family_id, request.merged_at.isoformat(), source.family_id),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        target_updated = self._family_from_rows(
            family_id=target.family_id,
            environment_id=target.environment_id,
            status=target.status,
            merged_into_family_id=None,
            created_at=target.created_at,
            updated_at=request.merged_at,
            fingerprints=self._family_fingerprints(target.family_id),
        )
        source_updated = _hashed(
            FaultFamilyV1,
            {
                **source.model_dump(mode="python", exclude={"family_sha256", "status", "updated_at", "merged_into_family_id"}),
                "status": FaultFamilyStatusV1.MERGED,
                "updated_at": request.merged_at,
                "merged_into_family_id": target.family_id,
            },
            "family_sha256",
        )
        self._put_family(target_updated)
        self._put_family(source_updated)
        return source_updated

    def _review(self, review_id: str) -> HumanReviewV1:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM human_reviews WHERE review_id = ?", (review_id,)
            ).fetchone()
        if row is None:
            raise not_found("HUMAN_REVIEW_NOT_FOUND", "The human review does not exist.")
        return HumanReviewV1.model_validate_json(row["payload_json"])

    def _existing_clause_predicates(
        self,
        environment_id: str,
    ) -> tuple[tuple[str, ...], ...]:
        core_clauses = tuple(
            tuple(
                sorted(
                    f"core:{requirement.predicate_kind.value}"
                    for requirement in clause.requirements
                )
            )
            for clause in build_default_evidence_support_policy_v22().clauses
        )
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT d.payload_json FROM environment_extension_registrations r "
                "JOIN registration_drafts d ON d.registration_id = r.registration_id "
                "WHERE r.environment_id = ? AND r.status = 'ACTIVE' "
                "ORDER BY r.registration_id",
                (environment_id,),
            ).fetchall()
        clauses = list(core_clauses)
        for row in rows:
            draft = FamilyRegistrationDraftV1.model_validate_json(row["payload_json"])
            selected = next(
                (
                    item.predicate_ids
                    for item in draft.candidate_clauses
                    if item.candidate_id == draft.selected_candidate_id
                ),
                None,
            )
            if selected is not None:
                clauses.append(tuple(sorted(selected)))
        return tuple(sorted(set(clauses)))

    def _eligible_negative_control(
        self,
        *,
        positive_fingerprints: tuple[IncidentFingerprintV1, ...],
        positive_incidents: tuple[IncidentRecordV1, ...],
        control_fingerprint: IncidentFingerprintV1,
        control_incident: IncidentRecordV1,
        result: DiagnosisResultV1,
    ) -> bool:
        if result.terminal is DiagnosisTerminalV1.NO_INCIDENT:
            return True
        same_domain = (
            control_fingerprint.broad_domain != "UNKNOWN"
            and any(
                item.broad_domain == control_fingerprint.broad_domain
                for item in positive_fingerprints
            )
        )
        same_candidates = any(
            set(item.candidate_logical_services).intersection(
                control_incident.candidate_logical_services
            )
            for item in positive_incidents
        )
        same_topology = any(
            set(item.topology_edges).intersection(control_fingerprint.topology_edges)
            for item in positive_fingerprints
        )
        same_sources = any(
            set(item.evidence_sources).intersection(
                control_fingerprint.evidence_sources
            )
            for item in positive_fingerprints
        )
        mechanism = (
            result.mechanism.value
            if isinstance(result.mechanism, MechanismV22)
            else str(result.mechanism or "")
        )
        known_confusable = any(
            mechanism in _CONFUSABLE_CORE_BY_DOMAIN.get(item.broad_domain, ())
            for item in positive_fingerprints
        )
        return known_confusable or (
            same_sources and (same_domain or same_candidates or same_topology)
        )

    def _matrix_for_family(self, family: FaultFamilyV1) -> PredicateMatrixV1:
        positives = self._family_fingerprints(family.family_id)
        positive_incidents = tuple(self._incident(item.incident_id) for item in positives)
        predicate_ids = tuple(
            sorted({predicate for item in positives for predicate in _present_predicates(item)})
        )
        rows: list[PredicateMatrixRowV1] = []

        def row_for(
            fingerprint: IncidentFingerprintV1,
            kind: PredicateMatrixRowKindV1,
        ) -> PredicateMatrixRowV1:
            present = _present_predicates(fingerprint)
            cells = []
            for predicate_id in predicate_ids:
                source = _predicate_source(predicate_id)
                if predicate_id in present:
                    state = PredicateCellStateV1.PRESENT
                elif source in fingerprint.source_coverage:
                    state = PredicateCellStateV1.ABSENT_WITH_COMPLETE_COVERAGE
                elif source in fingerprint.evidence_sources:
                    state = PredicateCellStateV1.SOURCE_FAILED
                else:
                    state = PredicateCellStateV1.UNKNOWN
                cells.append(
                    PredicateMatrixCellV1(
                        predicate_id=predicate_id,
                        source=source,
                        state=state,
                    )
                )
            return PredicateMatrixRowV1(
                row_id=f"{kind.value.casefold()}:{fingerprint.incident_id}",
                incident_id=fingerprint.incident_id,
                row_kind=kind,
                cells=tuple(sorted(cells, key=lambda item: (item.predicate_id, item.source))),
            )

        rows.extend(row_for(item, PredicateMatrixRowKindV1.POSITIVE_FAMILY) for item in positives)
        with self.store.connect() as connection:
            control_rows = connection.execute(
                "SELECT i.incident_id, d.payload_json FROM incidents i "
                "JOIN diagnosis_results d ON d.incident_id = i.incident_id "
                "WHERE i.environment_id = ? ORDER BY i.incident_id",
                (family.environment_id,),
            ).fetchall()
        positive_ids = {item.incident_id for item in positives}
        kind_by_terminal = {
            DiagnosisTerminalV1.CORE_KNOWN: PredicateMatrixRowKindV1.CORE_KNOWN_CONTROL,
            DiagnosisTerminalV1.NO_INCIDENT: PredicateMatrixRowKindV1.NO_INCIDENT_CONTROL,
            DiagnosisTerminalV1.EXTENSION_KNOWN: PredicateMatrixRowKindV1.OTHER_ACCEPTED_FAMILY,
            DiagnosisTerminalV1.INSUFFICIENT_EVIDENCE: PredicateMatrixRowKindV1.INSUFFICIENT_OR_CONFLICT_CONTROL,
            DiagnosisTerminalV1.CONFLICTING_EVIDENCE: PredicateMatrixRowKindV1.INSUFFICIENT_OR_CONFLICT_CONTROL,
        }
        for row in control_rows:
            if row["incident_id"] in positive_ids:
                continue
            result = DiagnosisResultV1.model_validate_json(row["payload_json"])
            kind = kind_by_terminal.get(result.terminal)
            if kind is None:
                continue
            control_fingerprint = self.fingerprint_for(row["incident_id"])
            control_incident = self._incident(row["incident_id"])
            if not self._eligible_negative_control(
                positive_fingerprints=positives,
                positive_incidents=positive_incidents,
                control_fingerprint=control_fingerprint,
                control_incident=control_incident,
                result=result,
            ):
                continue
            rows.append(row_for(control_fingerprint, kind))
        return build_predicate_matrix_v1(
            environment_id=family.environment_id,
            family_id=family.family_id,
            rows=tuple(rows),
        )

    def create_registration_draft(
        self,
        family_id: str,
        request: RegistrationDraftCreateV1,
    ) -> FamilyRegistrationDraftV1:
        family = self.get_family(family_id)
        review = self._review(request.human_review_id)
        if review.family_id != family_id or review.decision not in {
            ReviewDecisionV1.ACCEPT_AS_NEW,
            ReviewDecisionV1.SAVE_AS_INCIDENT_FAMILY,
        }:
            raise ProductError("ACCEPTED_REVIEW_REQUIRED", "Registration drafting requires an accepted bound review.")
        matrix = self._matrix_for_family(family)
        existing_clauses = self._existing_clause_predicates(family.environment_id)
        unfiltered_mining = mine_candidate_clauses_v1(matrix)
        mining = mine_candidate_clauses_v1(
            matrix,
            existing_clause_predicates=existing_clauses,
        )
        mode_by_status = {
            "NEEDS_MORE_INCIDENTS": RegistrationImplementationModeV1.NEEDS_MORE_INCIDENTS,
            "NEEDS_MORE_NEGATIVES": RegistrationImplementationModeV1.NEEDS_MORE_NEGATIVES,
            "NO_ACCEPTABLE_CANDIDATE": RegistrationImplementationModeV1.ENGINEERING_REQUIRED,
            "CANDIDATES_READY": RegistrationImplementationModeV1.DECLARATIVE_READY,
        }
        duplicate_existing = (
            not mining.candidates
            and any(
                candidate.predicate_ids in set(existing_clauses)
                for candidate in unfiltered_mining.candidates
            )
        )
        implementation_mode = (
            RegistrationImplementationModeV1.DUPLICATE_EXISTING
            if duplicate_existing
            else mode_by_status[mining.status]
        )
        selected = (
            mining.candidates[0].candidate_id
            if implementation_mode is RegistrationImplementationModeV1.DECLARATIVE_READY
            else None
        )
        positive_ids = tuple(sorted(row.incident_id for row in matrix.rows if row.row_kind is PredicateMatrixRowKindV1.POSITIVE_FAMILY))
        negative_ids = tuple(sorted(row.incident_id for row in matrix.rows if row.row_kind is not PredicateMatrixRowKindV1.POSITIVE_FAMILY))
        fingerprints = self._family_fingerprints(family_id)
        domains = Counter(item.broad_domain for item in fingerprints)
        now = _utc_now()
        payload = {
            "schema_version": "ecomsre.product.family-registration-draft.v1",
            "registration_id": new_product_id("registration"),
            "environment_id": family.environment_id,
            "family_id": family_id,
            "human_review_id": request.human_review_id,
            "human_canonical_label": request.human_canonical_label,
            "broad_domain": domains.most_common(1)[0][0] if domains else "UNKNOWN",
            "positive_incident_ids": positive_ids,
            "negative_incident_ids": negative_ids,
            "predicate_matrix_sha256": matrix.predicate_matrix_sha256,
            "candidate_clauses": mining.candidates,
            "selected_candidate_id": selected,
            "llm_explanation": request.llm_explanation,
            "unresolved_gaps": tuple(sorted(set(request.unresolved_gaps))),
            "implementation_mode": implementation_mode,
            "remediation_registration": "NOT_INCLUDED",
            "action_authority": "NONE",
            "provider_calls": 0,
            "created_at": now,
        }
        draft = _hashed(FamilyRegistrationDraftV1, payload, "draft_sha256")
        updated = _hashed(
            FaultFamilyV1,
            {
                **family.model_dump(mode="python", exclude={"family_sha256", "status", "updated_at"}),
                "status": FaultFamilyStatusV1.REGISTRATION_DRAFTED,
                "updated_at": now,
            },
            "family_sha256",
        )
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO predicate_matrices(predicate_matrix_sha256, family_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
                    (matrix.predicate_matrix_sha256, family_id, _json(matrix.model_dump(mode="json")), now.isoformat()),
                )
                connection.execute(
                    "INSERT INTO registration_drafts(registration_id, family_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
                    (draft.registration_id, family_id, _json(draft.model_dump(mode="json")), now.isoformat()),
                )
                connection.execute(
                    "UPDATE fault_families SET payload_json = ?, updated_at = ? WHERE family_id = ?",
                    (_json(updated.model_dump(mode="json")), now.isoformat(), family_id),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return draft

    def get_registration(self, registration_id: str) -> FamilyRegistrationDraftV1:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM registration_drafts WHERE registration_id = ?",
                (registration_id,),
            ).fetchone()
        if row is None:
            raise not_found("REGISTRATION_NOT_FOUND", "The registration draft does not exist.")
        return FamilyRegistrationDraftV1.model_validate_json(row["payload_json"])

    def _matrix(self, digest: str) -> PredicateMatrixV1:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM predicate_matrices WHERE predicate_matrix_sha256 = ?",
                (digest,),
            ).fetchone()
        if row is None:
            raise ProductError("PREDICATE_MATRIX_MISSING", "The registration predicate matrix is unavailable.")
        return PredicateMatrixV1.model_validate_json(row["payload_json"])

    def _shadow_runtime_material(
        self,
        incident_id: str,
    ) -> _ShadowRuntimeMaterialV1:
        incident = self._incident(incident_id)
        result = self._diagnosis(incident_id)
        evidence = self._evidence(incident_id, result.diagnosis_id)
        baseline = self._baseline(incident.baseline_id)
        snapshots = {
            str(item.payload.get("action", {}).get("action_id")): item.payload
            for item in evidence.objects
            if isinstance(item.payload.get("action"), dict)
            and item.payload.get("action", {}).get("action_id")
        }
        raw_outcomes: list[ReadOutcomeV22] = []
        memory_outcomes: list[ReadOutcomeV22 | RuntimeReadOutcomeV22] = []
        # Product acquires the frozen, sorted Core catalog first, then appends
        # its optional queue action. Memory summaries bind that original order.
        queue_action_id = build_queue_lag_action_v030().action_id
        for action_id in sorted(
            snapshots, key=lambda value: (value == queue_action_id, value)
        ):
            snapshot = snapshots[action_id]
            read_payload = snapshot.get("read_outcome")
            if not isinstance(read_payload, dict):
                raise ProductError(
                    "SHADOW_EVIDENCE_INVALID",
                    "A persisted read snapshot lacks its typed read outcome.",
                )
            raw_outcomes.append(ReadOutcomeV22.model_validate_json(_json(read_payload)))
            memory_payload = snapshot.get("memory_outcome")
            if memory_payload is None:
                continue
            if not isinstance(memory_payload, dict):
                raise ProductError(
                    "SHADOW_EVIDENCE_INVALID",
                    "A persisted read snapshot has an invalid memory outcome.",
                )
            if memory_payload.get("schema_version") == "dta-v22.runtime-read-outcome.v1":
                memory_outcomes.append(
                    RuntimeReadOutcomeV22.model_validate_json(_json(memory_payload))
                )
            else:
                memory_outcomes.append(
                    ReadOutcomeV22.model_validate_json(_json(memory_payload))
                )
        if not raw_outcomes or not memory_outcomes:
            raise ProductError(
                "SHADOW_EVIDENCE_INCOMPLETE",
                "The persisted incident cannot reconstruct a bounded runtime input.",
            )
        memory, _full = build_memory_views_v22(
            outcomes=tuple(memory_outcomes),
            baseline=baseline.v22_baseline_profile,
            observed_at=incident.diagnosis_observed_at,
            top_k=64,
        )
        if result.memory_sha256 is not None and memory.memory_sha256 != result.memory_sha256:
            raise ProductError(
                "SHADOW_MEMORY_BINDING_MISMATCH",
                "The reconstructed shadow memory differs from the diagnosis binding.",
            )
        runtime_input = build_product_extension_runtime_input_v1(
            case_id=incident.incident_id,
            candidate_services=incident.candidate_logical_services,
            topology_edges=tuple(
                (item.parent_service, item.child_service)
                for item in baseline.topology_edges
            ),
            baseline=baseline.v22_baseline_profile,
            memory=memory,
            generic_anomalies=extract_product_anomalies_v1(
                memory=memory,
                candidate_services=incident.candidate_logical_services,
                baseline_known_log_templates=tuple(
                    (item.service, item.template)
                    for item in baseline.normal_log_templates
                ),
                snapshots=tuple(snapshots.values()),
            ),
            raw_outcomes=tuple(raw_outcomes),
        )
        complete_sources = _complete_source_coverage_v1(
            incident=incident,
            evidence=evidence,
            capability_matrix=self._capability_matrix(incident),
            environment=EnvironmentRepositoryV1(self.store).get(incident.environment_id),
            baseline=baseline,
        )
        # Reachability describes returned statuses, not target completeness.
        # Keep that typed fact intact; gate selected-source completeness separately.
        return _ShadowRuntimeMaterialV1(
            incident=incident,
            baseline=baseline,
            raw_outcomes=tuple(raw_outcomes),
            memory_outcomes=tuple(memory_outcomes),
            runtime_input=runtime_input,
            complete_sources=complete_sources,
        )

    @staticmethod
    def _target_counterfactual_runtime_input(
        *,
        material: _ShadowRuntimeMaterialV1,
        counterfactual_target: str,
    ) -> ExtensionRuntimeInputV234:
        runtime_input = material.runtime_input
        return hashed_model_v234(
            ExtensionRuntimeInputV234,
            {
                "schema_version": runtime_input.schema_version,
                "case_id": (
                    f"{material.incident.incident_id}:target:{counterfactual_target}"
                ),
                "candidate_services": tuple(
                    sorted(
                        set(runtime_input.candidate_services)
                        | {counterfactual_target}
                    )
                ),
                "adjacent_services": runtime_input.adjacent_services,
                "baseline": runtime_input.baseline,
                "memory": runtime_input.memory,
                "generic_anomalies": runtime_input.generic_anomalies,
                "source_coverage": runtime_input.source_coverage,
            },
            "runtime_input_sha256",
        )

    @staticmethod
    def _source_failure_runtime_input(
        *,
        runtime_input: ExtensionRuntimeInputV234,
        failed_source: EvidenceSourceV22,
    ) -> ExtensionRuntimeInputV234:
        coverage = [
            item
            for item in runtime_input.source_coverage
            if item.source is not failed_source
        ]
        coverage.append(
            ExtensionSourceCoverageV234(
                source=failed_source,
                statuses=(ReadSourceStatusV22.FAILURE_UNAVAILABLE,),
                reachable=False,
            )
        )
        return hashed_model_v234(
            ExtensionRuntimeInputV234,
            {
                "schema_version": runtime_input.schema_version,
                "case_id": f"{runtime_input.case_id}:failed:{failed_source.value}",
                "candidate_services": runtime_input.candidate_services,
                "adjacent_services": runtime_input.adjacent_services,
                "baseline": runtime_input.baseline,
                "memory": runtime_input.memory,
                "generic_anomalies": runtime_input.generic_anomalies,
                "source_coverage": tuple(
                    sorted(coverage, key=lambda item: item.source.value)
                ),
            },
            "runtime_input_sha256",
        )

    @staticmethod
    def _shadow_case_outcome(
        *,
        case_id: str,
        incident_id: str | None,
        stratum: ShadowEvaluationStratumV1,
        origin: ShadowCaseOriginV1,
        runtime_input: ExtensionRuntimeInputV234,
        compiled: CompiledFaultRegistrationV234,
        expected_match: bool,
        target_services: tuple[str, ...] | None = None,
    ) -> ShadowCaseOutcomeV1:
        required_sources = tuple(
            sorted({item.evidence_source.value for item in compiled.predicates})
        )
        decisions = ExtensionSupportPolicyV234().evaluate(
            registration=compiled,
            runtime_input=runtime_input,
            target_services=target_services,
        )
        evaluated_targets = (
            runtime_input.candidate_services
            if target_services is None
            else tuple(sorted(set(target_services)))
        )
        selected = next((item for item in decisions if item.admitted), None)
        available_refs = tuple(
            sorted(item.evidence_ref for item in runtime_input.memory.evidence_refs)
        )
        authority_violations = sum(
            (
                compiled.action_authority != "NONE",
                compiled.repository_write_authority != "NONE",
                compiled.remediation_registration != "NOT_INCLUDED",
            )
        )
        if authority_violations:
            raise ProductError(
                "SHADOW_ACTION_AUTHORITY_VIOLATION",
                "The shadow candidate carries forbidden authority.",
            )
        payload = {
            "schema_version": "ecomsre.product.shadow-case-outcome.v1",
            "case_id": case_id,
            "incident_id": incident_id,
            "stratum": stratum,
            "origin": origin,
            "runtime_input_sha256": runtime_input.runtime_input_sha256,
            "expected_match": expected_match,
            "matched": selected is not None,
            "evaluated_target_services": evaluated_targets,
            "supporting_evidence_refs": (
                () if selected is None else selected.supporting_evidence_refs
            ),
            "available_evidence_refs": available_refs,
            "required_sources": required_sources,
            "source_reachable": all(
                runtime_input.source_is_reachable(EvidenceSourceV22(source))
                for source in required_sources
            ),
            "action_authority_violations": 0,
            "reason_code": None,
        }
        return _hashed(ShadowCaseOutcomeV1, payload, "outcome_sha256")

    @staticmethod
    def _unavailable_shadow_case(
        *,
        case_id: str,
        stratum: ShadowEvaluationStratumV1,
        reason_code: str,
        required_sources: tuple[str, ...],
    ) -> ShadowCaseOutcomeV1:
        payload = {
            "schema_version": "ecomsre.product.shadow-case-outcome.v1",
            "case_id": case_id,
            "incident_id": None,
            "stratum": stratum,
            "origin": ShadowCaseOriginV1.NOT_AVAILABLE,
            "runtime_input_sha256": None,
            "expected_match": None,
            "matched": None,
            "evaluated_target_services": (),
            "supporting_evidence_refs": (),
            "available_evidence_refs": (),
            "required_sources": required_sources,
            "source_reachable": None,
            "action_authority_violations": 0,
            "reason_code": reason_code,
        }
        return _hashed(ShadowCaseOutcomeV1, payload, "outcome_sha256")

    def create_shadow_evaluation(self, registration_id: str) -> ShadowEvaluationV1:
        draft = self.get_registration(registration_id)
        if draft.implementation_mode is not RegistrationImplementationModeV1.DECLARATIVE_READY:
            raise ProductError("DECLARATIVE_REGISTRATION_REQUIRED", "Only a declarative-ready draft can enter shadow evaluation.")
        selected = next(item for item in draft.candidate_clauses if item.candidate_id == draft.selected_candidate_id)
        matrix = self._matrix(draft.predicate_matrix_sha256)
        compiled = build_product_shadow_candidate_v1(
            draft=draft,
            selected=selected,
        )
        required_sources = tuple(
            sorted({item.evidence_source.value for item in compiled.predicates})
        )
        stratum_by_kind = {
            PredicateMatrixRowKindV1.POSITIVE_FAMILY: ShadowEvaluationStratumV1.POSITIVE_INCIDENT,
            PredicateMatrixRowKindV1.CORE_KNOWN_CONTROL: ShadowEvaluationStratumV1.CONFUSABLE_CORE_KNOWN,
            PredicateMatrixRowKindV1.NO_INCIDENT_CONTROL: ShadowEvaluationStratumV1.NO_INCIDENT,
            PredicateMatrixRowKindV1.OTHER_ACCEPTED_FAMILY: ShadowEvaluationStratumV1.OTHER_EXTENSION,
            PredicateMatrixRowKindV1.INSUFFICIENT_OR_CONFLICT_CONTROL: ShadowEvaluationStratumV1.INSUFFICIENT_OR_CONFLICT,
        }
        outcomes: list[ShadowCaseOutcomeV1] = []
        materials: dict[str, _ShadowRuntimeMaterialV1] = {}
        for row in matrix.rows:
            stratum = stratum_by_kind[row.row_kind]
            material = materials.setdefault(
                row.incident_id,
                self._shadow_runtime_material(row.incident_id),
            )
            if stratum is not ShadowEvaluationStratumV1.INSUFFICIENT_OR_CONFLICT:
                cells = {cell.predicate_id: cell.state for cell in row.cells}
                if not set(required_sources).issubset(material.complete_sources) or any(
                    cells.get(predicate) not in {
                        PredicateCellStateV1.PRESENT,
                        PredicateCellStateV1.ABSENT_WITH_COMPLETE_COVERAGE,
                    }
                    for predicate in selected.predicate_ids
                ):
                    raise ProductError(
                        "SHADOW_SELECTED_EVIDENCE_INCOMPLETE",
                        "A persisted shadow case lacks conclusive selected-predicate evidence.",
                    )
            outcomes.append(
                self._shadow_case_outcome(
                    case_id=f"shadow:{stratum.value.casefold()}:{row.incident_id}",
                    incident_id=row.incident_id,
                    stratum=stratum,
                    origin=ShadowCaseOriginV1.PERSISTED_INCIDENT,
                    runtime_input=material.runtime_input,
                    compiled=compiled,
                    expected_match=(
                        stratum is ShadowEvaluationStratumV1.POSITIVE_INCIDENT
                    ),
                )
            )
        positive_materials = tuple(
            materials[row.incident_id]
            for row in matrix.rows
            if row.row_kind is PredicateMatrixRowKindV1.POSITIVE_FAMILY
        )
        if not positive_materials:
            raise ProductError(
                "SHADOW_POSITIVE_CASES_MISSING",
                "Shadow evaluation requires persisted positive incidents.",
            )
        counterfactual_base = positive_materials[0]
        counterfactual_target = "counterfactual-target"
        for index, material in enumerate(positive_materials, start=1):
            counterfactual = self._target_counterfactual_runtime_input(
                material=material,
                counterfactual_target=counterfactual_target,
            )
            outcomes.append(
                self._shadow_case_outcome(
                    case_id=f"shadow:target-counterfactual:{index}",
                    incident_id=material.incident.incident_id,
                    stratum=ShadowEvaluationStratumV1.TARGET_COUNTERFACTUAL,
                    origin=ShadowCaseOriginV1.DERIVED_COUNTERFACTUAL,
                    runtime_input=counterfactual,
                    compiled=compiled,
                    expected_match=False,
                    target_services=(counterfactual_target,),
                )
            )
        source_failure_inputs: list[tuple[EvidenceSourceV22, ExtensionRuntimeInputV234]] = []
        for source_value in required_sources:
            source = EvidenceSourceV22(source_value)
            failed = self._source_failure_runtime_input(
                runtime_input=counterfactual_base.runtime_input,
                failed_source=source,
            )
            source_failure_inputs.append((source, failed))
            outcomes.append(
                self._shadow_case_outcome(
                    case_id=f"shadow:source-failure:{source.value}",
                    incident_id=counterfactual_base.incident.incident_id,
                    stratum=ShadowEvaluationStratumV1.SOURCE_FAILURE,
                    origin=ShadowCaseOriginV1.DERIVED_SOURCE_FAILURE,
                    runtime_input=failed,
                    compiled=compiled,
                    expected_match=False,
                )
            )
        present_strata = {item.stratum for item in outcomes}
        if ShadowEvaluationStratumV1.INSUFFICIENT_OR_CONFLICT not in present_strata:
            for source, failed in source_failure_inputs:
                outcomes.append(
                    self._shadow_case_outcome(
                        case_id=f"shadow:insufficient-control:{source.value}",
                        incident_id=counterfactual_base.incident.incident_id,
                        stratum=ShadowEvaluationStratumV1.INSUFFICIENT_OR_CONFLICT,
                        origin=ShadowCaseOriginV1.DERIVED_SOURCE_FAILURE,
                        runtime_input=failed,
                        compiled=compiled,
                        expected_match=False,
                    )
                )
        present_strata = {item.stratum for item in outcomes}
        optional_reasons = {
            ShadowEvaluationStratumV1.CONFUSABLE_CORE_KNOWN: "NO_CONFUSABLE_CORE_CONTROL_AVAILABLE",
            ShadowEvaluationStratumV1.NO_INCIDENT: "NO_NO_INCIDENT_CONTROL_AVAILABLE",
            ShadowEvaluationStratumV1.OTHER_EXTENSION: "NO_ACTIVE_OTHER_EXTENSION_CONTROL_AVAILABLE",
        }
        for stratum, reason in optional_reasons.items():
            if stratum not in present_strata:
                outcomes.append(
                    self._unavailable_shadow_case(
                        case_id=f"shadow:{stratum.value.casefold()}:not-available",
                        stratum=stratum,
                        reason_code=reason,
                        required_sources=required_sources,
                    )
                )
        shadow = evaluate_shadow_gate_v1(
            registration_id=registration_id,
            outcomes=tuple(outcomes),
        )
        with self.store.connect() as connection:
            connection.execute(
                "INSERT INTO shadow_evaluations(evaluation_id, registration_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (shadow.evaluation_id, registration_id, _json(shadow.model_dump(mode="json")), _utc_now().isoformat()),
            )
        return shadow

    def _shadow(self, evaluation_id: str) -> ShadowEvaluationV1:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM shadow_evaluations WHERE evaluation_id = ?",
                (evaluation_id,),
            ).fetchone()
        if row is None:
            raise not_found("SHADOW_EVALUATION_NOT_FOUND", "The shadow evaluation does not exist.")
        return ShadowEvaluationV1.model_validate_json(row["payload_json"])

    def promote(self, registration_id: str, request: PromotionCreateV1) -> PromotionRecordV1:
        draft = self.get_registration(registration_id)
        shadow = self._shadow(request.shadow_evaluation_id)
        selected = next(item for item in draft.candidate_clauses if item.candidate_id == draft.selected_candidate_id)
        compiled, _validation = compile_product_registration_v1(
            draft=draft,
            selected=selected,
            shadow=shadow,
        )
        human_review = self._review(draft.human_review_id)
        family = self.get_family(draft.family_id)
        updated = _hashed(
            FaultFamilyV1,
            {
                **family.model_dump(mode="python", exclude={"family_sha256", "status", "updated_at"}),
                "status": FaultFamilyStatusV1.PROMOTED,
                "updated_at": request.promoted_at,
            },
            "family_sha256",
        )
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                version = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(registry_version), 0) + 1 "
                        "FROM environment_extension_registry_versions "
                        "WHERE environment_id = ?",
                        (draft.environment_id,),
                    ).fetchone()[0]
                )
                promotion_payload = {
                    "schema_version": "ecomsre.product.registration-promotion.v1",
                    "promotion_id": new_product_id("promotion"),
                    "registration_id": registration_id,
                    "environment_id": draft.environment_id,
                    "registry_version": version,
                    "status": "ACTIVE",
                    "action_authority": "NONE",
                    **request.model_dump(mode="python"),
                }
                promotion = _hashed(
                    PromotionRecordV1,
                    promotion_payload,
                    "promotion_sha256",
                )
                registry = _hashed(
                    EnvironmentExtensionRegistryEntryV1,
                    {
                    "schema_version": "ecomsre.product.environment-extension-registry-entry.v1",
                    "registration_id": registration_id,
                    "compiled_registration_id": compiled.registration_id,
                    "environment_id": draft.environment_id,
                    "family_id": draft.family_id,
                    "mechanism_enum_name": compiled.mechanism.mechanism_enum_name,
                    "mechanism_slug": compiled.mechanism.mechanism_slug,
                    "mechanism_display_name": compiled.mechanism.display_name,
                    "human_canonical_label": draft.human_canonical_label,
                    "broad_domain": draft.broad_domain,
                    "compiled_predicates": compiled.predicates,
                    "compiled_dnf_clauses": compiled.support_clauses,
                    "compiled_registration": compiled,
                    "source_draft_sha256": draft.draft_sha256,
                    "source_human_review_sha256": human_review.review_sha256,
                    "shadow_evaluation_sha256": shadow.evaluation_sha256,
                    "promotion_review": promotion,
                    "revocation_review": None,
                    "registry_version": version,
                    "status": "ACTIVE",
                    "action_authority": "NONE",
                    "remediation_authority": "NONE",
                    "created_at": request.promoted_at,
                    "updated_at": request.promoted_at,
                    },
                    "entry_sha256",
                )
                connection.execute(
                    "INSERT INTO environment_extension_registrations(registration_id, environment_id, payload_json, status, created_at, updated_at) VALUES (?, ?, ?, 'ACTIVE', ?, ?)",
                    (registration_id, draft.environment_id, _json(registry.model_dump(mode="json")), request.promoted_at.isoformat(), request.promoted_at.isoformat()),
                )
                connection.execute(
                    "INSERT INTO environment_extension_registry_versions(environment_id, registry_version, registration_id, status, payload_json, created_at) "
                    "VALUES (?, ?, ?, 'ACTIVE', ?, ?)",
                    (
                        draft.environment_id,
                        version,
                        registration_id,
                        _json(registry.model_dump(mode="json")),
                        request.promoted_at.isoformat(),
                    ),
                )
                connection.execute(
                    "INSERT INTO promotion_records(promotion_id, registration_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
                    (promotion.promotion_id, registration_id, _json(promotion.model_dump(mode="json")), request.promoted_at.isoformat()),
                )
                connection.execute(
                    "UPDATE fault_families SET payload_json = ?, updated_at = ? WHERE family_id = ?",
                    (_json(updated.model_dump(mode="json")), request.promoted_at.isoformat(), draft.family_id),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return promotion

    def revoke(self, registration_id: str, request: RevocationCreateV1) -> RevocationRecordV1:
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT environment_id, payload_json, status "
                    "FROM environment_extension_registrations "
                    "WHERE registration_id = ?",
                    (registration_id,),
                ).fetchone()
                if row is None:
                    raise not_found(
                        "REGISTRATION_NOT_FOUND",
                        "The active registration does not exist.",
                    )
                if row["status"] != "ACTIVE":
                    raise ProductError(
                        "REGISTRATION_NOT_ACTIVE",
                        "The registration is not active.",
                        status_code=409,
                    )
                registry = EnvironmentExtensionRegistryEntryV1.model_validate_json(
                    row["payload_json"]
                )
                next_version = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(registry_version), 0) + 1 "
                        "FROM environment_extension_registry_versions "
                        "WHERE environment_id = ?",
                        (row["environment_id"],),
                    ).fetchone()[0]
                )
                payload = {
                    "schema_version": "ecomsre.product.registration-revocation.v1",
                    "revocation_id": new_product_id("revocation"),
                    "registration_id": registration_id,
                    "environment_id": row["environment_id"],
                    "prior_registry_version": registry.registry_version,
                    "status": "REVOKED",
                    "action_authority": "NONE",
                    **request.model_dump(mode="python"),
                }
                revocation = _hashed(
                    RevocationRecordV1,
                    payload,
                    "revocation_sha256",
                )
                revoked_registry = _hashed(
                    EnvironmentExtensionRegistryEntryV1,
                    {
                        **{
                            field: getattr(registry, field)
                            for field in EnvironmentExtensionRegistryEntryV1.model_fields
                            if field
                            not in {
                                "entry_sha256",
                                "registry_version",
                                "status",
                                "updated_at",
                                "revocation_review",
                            }
                        },
                        "registry_version": next_version,
                        "status": "REVOKED",
                        "updated_at": request.revoked_at,
                        "revocation_review": revocation,
                    },
                    "entry_sha256",
                )
                connection.execute(
                    "UPDATE environment_extension_registrations SET payload_json = ?, status = 'REVOKED', updated_at = ? WHERE registration_id = ?",
                    (_json(revoked_registry.model_dump(mode="json")), request.revoked_at.isoformat(), registration_id),
                )
                connection.execute(
                    "INSERT INTO environment_extension_registry_versions(environment_id, registry_version, registration_id, status, payload_json, created_at) "
                    "VALUES (?, ?, ?, 'REVOKED', ?, ?)",
                    (
                        row["environment_id"],
                        next_version,
                        registration_id,
                        _json(revoked_registry.model_dump(mode="json")),
                        request.revoked_at.isoformat(),
                    ),
                )
                connection.execute(
                    "INSERT INTO revocation_records(revocation_id, registration_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
                    (revocation.revocation_id, registration_id, _json(revocation.model_dump(mode="json")), request.revoked_at.isoformat()),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return revocation

    def active_extensions(self, environment_id: str) -> tuple[ProductExtensionRegistrationV1, ...]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM environment_extension_registrations "
                "WHERE environment_id = ? AND status = 'ACTIVE' ORDER BY registration_id",
                (environment_id,),
            ).fetchall()
        values = []
        for row in rows:
            registry = EnvironmentExtensionRegistryEntryV1.model_validate_json(
                row["payload_json"]
            )
            compiled = registry.compiled_registration
            values.append(
                ProductExtensionRegistrationV1(
                    registration_id=compiled.registration_id,
                    mechanism_slug=compiled.mechanism.mechanism_slug,
                    broad_fault_domain=registry.broad_domain,
                    compiled_registration=compiled,
                )
            )
        return tuple(values)


__all__ = (
    "KnowledgeRepositoryV1",
    "build_product_fingerprint_observation_v1",
)
