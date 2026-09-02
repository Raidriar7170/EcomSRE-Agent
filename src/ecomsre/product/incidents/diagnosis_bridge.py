"""Product ordering bridge over the frozen core and open-world diagnosis contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ecomsre.dta_v2.v22.diagnosis import AdmittedDiagnosisV22
from ecomsre.dta_v2.v22.memory import (
    PredicateKindV22,
    SalientEvidenceMemoryV22,
    SignalStrengthV22,
    build_memory_views_v22,
)
from ecomsre.dta_v2.v22.predicates import (
    build_default_evidence_support_policy_v22,
    evaluate_no_incident_v22,
)
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, ReadSourceStatusV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts import (
    ProvisionalFaultDomainV23,
    build_provisional_report_v23,
)
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    GenericAnomalyV23,
    extract_generic_anomalies_v23,
)
from ecomsre.dta_v2.v23.known_admission import (
    KnownAdmissionStateV23,
    build_known_admission_state_v23,
)
from ecomsre.dta_v2.v23.novelty_gate import (
    NoveltyDispositionV23,
    evaluate_novelty_gate_v23,
)
from ecomsre.dta_v2.v23.ontology_view import build_active_ontology_view_v23
from ecomsre.dta_v2.v23.residual_graph import (
    build_known_terminal_candidates_v23,
    build_residual_evidence_graph_v23,
)
from ecomsre.product.baselines import EnvironmentBaselineV1
from ecomsre.product.contracts import ServiceIdentityMapV1
from ecomsre.product.ids import new_product_id
from ecomsre.product.incidents.contracts import (
    ActionAuthorityV1,
    DiagnosisLaneV1,
    DiagnosisResultV1,
    DiagnosisTerminalV1,
    IncidentRecordV1,
)
from ecomsre.product.incidents.extensions import ProductExtensionMatcherV1
from ecomsre.product.incidents.evidence_binding_v0232 import (
    DiagnosisDecisionTraceV0232,
)
from ecomsre.product.incidents.read_backend import ProductReadAcquisitionV1


_V024_ANOMALY_BY_PREDICATE = {
    PredicateKindV22.METRIC_LATENCY_STRONG: (
        GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER
    ),
    PredicateKindV22.RESOURCE_CPU_STRONG: GenericAnomalyKindV23.RESOURCE_CPU_OUTLIER,
    PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG: (
        GenericAnomalyKindV23.RESOURCE_MEMORY_TREND
    ),
    PredicateKindV22.LOG_CONFIGURATION_ERROR: GenericAnomalyKindV23.LOG_ERROR_CLUSTER,
    PredicateKindV22.LOG_DEPENDENCY_TIMEOUT: GenericAnomalyKindV23.LOG_ERROR_CLUSTER,
    PredicateKindV22.LOG_MEMORY_PRESSURE: GenericAnomalyKindV23.LOG_ERROR_CLUSTER,
}


def _effective_admissions_v024(
    *,
    admission: KnownAdmissionStateV23,
    memory: SalientEvidenceMemoryV22,
    anomalies: tuple[GenericAnomalyV23, ...],
) -> tuple[AdmittedDiagnosisV22, ...]:
    clauses = {
        item.clause_id: item
        for item in build_default_evidence_support_policy_v22().clauses
    }
    effective: list[AdmittedDiagnosisV22] = []
    for item in admission.admitted_diagnoses:
        clause = clauses[item.matched_clause_id]
        guarded_kinds = {
            requirement.predicate_kind
            for requirement in clause.requirements
            if requirement.predicate_kind in _V024_ANOMALY_BY_PREDICATE
        }
        valid = True
        for kind in guarded_kinds:
            predicates = tuple(
                predicate
                for predicate in memory.predicates
                if predicate.predicate_kind is kind
                and set(predicate.evidence_refs).issubset(
                    item.supporting_evidence_refs
                )
            )
            expected_anomaly = _V024_ANOMALY_BY_PREDICATE[kind]
            if not predicates or not any(
                anomaly.kind is expected_anomaly
                and anomaly.strength is SignalStrengthV22.STRONG
                and any(
                    set(predicate.evidence_refs).intersection(
                        anomaly.evidence_refs
                    )
                    for predicate in predicates
                )
                for anomaly in anomalies
            ):
                valid = False
                break
        if valid:
            effective.append(item)
    return tuple(effective)


def _domain_for_anomalies(anomalies: tuple[Any, ...]) -> ProvisionalFaultDomainV23:
    values = {item.kind.value for item in anomalies}
    if "METRIC_QUEUE_LAG_OUTLIER" in values:
        return ProvisionalFaultDomainV23.CONCURRENCY
    if any(value.startswith("RUNTIME_") for value in values):
        return ProvisionalFaultDomainV23.RUNTIME
    if any(value.startswith("RESOURCE_") for value in values):
        return ProvisionalFaultDomainV23.RESOURCE
    if any(value.startswith("TRACE_") or value.startswith("METRIC_LATENCY") for value in values):
        return ProvisionalFaultDomainV23.DEPENDENCY
    if "RECENT_CHANGE_CORRELATION" in values:
        return ProvisionalFaultDomainV23.CONFIGURATION
    return ProvisionalFaultDomainV23.UNKNOWN


class ProductDiagnosisBridgeV1:
    def __init__(
        self,
        extension_matcher: ProductExtensionMatcherV1 | None = None,
    ) -> None:
        self._extension_matcher = extension_matcher or ProductExtensionMatcherV1()

    def diagnose(
        self,
        *,
        incident: IncidentRecordV1,
        baseline: EnvironmentBaselineV1,
        identity_map: ServiceIdentityMapV1,
        acquisition: ProductReadAcquisitionV1,
        diagnosis_id: str | None,
        created_at: datetime,
    ) -> tuple[
        DiagnosisResultV1,
        tuple[dict[str, Any], ...],
        DiagnosisDecisionTraceV0232,
    ]:
        memory, _full = build_memory_views_v22(
            outcomes=acquisition.memory_outcomes,
            baseline=baseline.v22_baseline_profile,
            observed_at=incident.diagnosis_observed_at,
            top_k=64,
        )
        candidates = incident.candidate_logical_services
        view = build_active_ontology_view_v23(candidate_services=candidates)
        topology_edges = tuple(
            (item.parent_service, item.child_service) for item in baseline.topology_edges
        )
        anomalies = extract_generic_anomalies_v23(
            memory=memory,
            candidate_services=candidates,
            baseline_known_log_templates=tuple(
                (item.service, item.template) for item in baseline.normal_log_templates
            ),
            healthy_noise_guard_v024=True,
        )
        failed_sources = tuple(
            sorted(
                {
                    item.source
                    for item in acquisition.raw_outcomes
                    if item.status
                    not in {
                        ReadSourceStatusV22.SUCCESS_EMPTY,
                        ReadSourceStatusV22.SUCCESS_NONEMPTY,
                    }
                },
                key=lambda item: item.value,
            )
        )
        admission = build_known_admission_state_v23(
            view=view,
            memory=memory,
            topology_edges=topology_edges,
            evidence_source_unavailable=bool(failed_sources),
        )
        effective_admissions = _effective_admissions_v024(
            admission=admission,
            memory=memory,
            anomalies=anomalies,
        )
        effective_conflicting = len(effective_admissions) > 1
        legacy_no_incident = evaluate_no_incident_v22(
            memory=memory,
            candidate_services=candidates,
        )
        false_anomaly_only = (
            legacy_no_incident.denial_reasons == ("STRONG_ANOMALY_PRESENT",)
            and not anomalies
            and not effective_admissions
        )
        effective_no_incident_admissible = (
            admission.no_incident_admissible or false_anomaly_only
        )
        by_logical = {item.logical_service: item.service_id for item in identity_map.services}

        terminal: DiagnosisTerminalV1
        lane: DiagnosisLaneV1
        roots: tuple[str, ...] = ()
        mechanism: str | None = None
        broad_domain: str | None = None
        support: tuple[str, ...] = ()
        contradict: tuple[str, ...] = ()
        report_payload: dict[str, Any] | None = None
        limitations = set(acquisition.capability_limitations)
        algorithmic_reasons: set[str] = set()
        extension_match_count = 0
        required_coverage = False
        novelty_gate_disposition: NoveltyDispositionV23 | None = None
        novelty_gate_reason_codes: tuple[str, ...] = ()
        residual_anomaly_ids: tuple[str, ...] = ()

        if effective_conflicting:
            terminal = DiagnosisTerminalV1.CONFLICTING_EVIDENCE
            lane = DiagnosisLaneV1.ABSTAIN
            algorithmic_reasons.add("CORE_MULTIPLE_ADMISSIONS")
            support = tuple(
                sorted(
                    {
                        ref
                        for diagnosis in effective_admissions
                        for ref in diagnosis.supporting_evidence_refs
                    }
                )
            )
        elif effective_admissions:
            diagnosis = effective_admissions[0]
            terminal = DiagnosisTerminalV1.CORE_KNOWN
            lane = DiagnosisLaneV1.CORE
            roots = (by_logical[diagnosis.root_service],)
            mechanism = diagnosis.mechanism.value
            broad_domain = diagnosis.fault_domain.value
            support = diagnosis.supporting_evidence_refs
        else:
            extension_matches = self._extension_matcher.match(
                case_id=incident.incident_id,
                candidate_services=candidates,
                topology_edges=topology_edges,
                baseline=baseline.v22_baseline_profile,
                memory=memory,
                generic_anomalies=anomalies,
                raw_outcomes=acquisition.raw_outcomes,
            )
            extension_match_count = len(extension_matches)
            if len(extension_matches) > 1:
                terminal = DiagnosisTerminalV1.CONFLICTING_EVIDENCE
                lane = DiagnosisLaneV1.ABSTAIN
                support = tuple(
                    sorted(
                        {
                            ref
                            for match in extension_matches
                            for ref in match.supporting_evidence_refs
                        }
                    )
                )
                algorithmic_reasons.add("EXTENSION_MULTIPLE_ADMISSIONS")
            elif extension_matches:
                match = extension_matches[0]
                terminal = DiagnosisTerminalV1.EXTENSION_KNOWN
                lane = DiagnosisLaneV1.EXTENSION
                roots = (by_logical[match.root_service],)
                mechanism = match.mechanism_slug
                broad_domain = match.broad_fault_domain
                support = match.supporting_evidence_refs
            else:
                required_coverage = all(
                    set(acquisition.covered_services_by_source[source])
                    == set(candidates)
                    for source in (
                        EvidenceSourceV22.METRICS,
                        EvidenceSourceV22.RUNTIME,
                    )
                ) and "RUNTIME_DIAGNOSIS_UNAVAILABLE" not in limitations
                if (
                    effective_no_incident_admissible
                    and not anomalies
                    and required_coverage
                    and not failed_sources
                ):
                    terminal = DiagnosisTerminalV1.NO_INCIDENT
                    lane = DiagnosisLaneV1.NO_INCIDENT
                    first_ref_by_source = {
                        reference.source: reference.evidence_ref
                        for reference in reversed(memory.evidence_refs)
                    }
                    support = tuple(sorted(first_ref_by_source.values()))
                else:
                    graph = build_residual_evidence_graph_v23(
                        candidate_services=candidates,
                        generic_anomalies=anomalies,
                        known_terminal_candidates=build_known_terminal_candidates_v23(
                            admitted_diagnoses=()
                        ),
                        memory=memory,
                    )
                    gate = evaluate_novelty_gate_v23(
                        graph=graph,
                        no_incident_admissible=False,
                        remaining_budget_before_discovery=3.0,
                        required_source_failures=failed_sources,
                        conflicting_evidence=False,
                    )
                    novelty_gate_disposition = gate.disposition
                    novelty_gate_reason_codes = tuple(sorted(gate.reason_codes))
                    residual_anomaly_ids = tuple(sorted(graph.residual_anomaly_ids))
                    strong = tuple(
                        item
                        for item in anomalies
                        if item.anomaly_id in set(graph.residual_anomaly_ids)
                        and item.evidence_refs
                    )
                    if (
                        strong
                        and not failed_sources
                        and "RUNTIME_DIAGNOSIS_UNAVAILABLE" not in limitations
                        and gate.disposition
                        is NoveltyDispositionV23.UNREGISTERED_INCIDENT_SUSPECTED
                    ):
                        residual_refs = {
                            item.anomaly_id: item.evidence_refs for item in strong
                        }
                        support = tuple(
                            sorted({ref for item in strong for ref in item.evidence_refs})
                        )
                        root_logical = strong[0].service
                        domain = _domain_for_anomalies(strong)
                        report = build_provisional_report_v23(
                            terminal="UNREGISTERED_INCIDENT_SUSPECTED",
                            candidate_services=candidates,
                            suspected_root_services=(root_logical,),
                            affected_services=tuple(
                                sorted({item.service for item in strong})
                            ),
                            broad_fault_domain=domain,
                            provisional_mechanism_label="unregistered-observed-anomaly",
                            mechanism_description=(
                                "A strong observer-visible anomaly remains outside the active "
                                "core and environment extension registries."
                            ),
                            observed_symptoms=tuple(
                                sorted({item.summary for item in strong})
                            ),
                            supporting_evidence_refs=support,
                            contradicting_evidence_refs=(),
                            unexplained_anomaly_ids=tuple(sorted(residual_refs)),
                            alternative_hypotheses=(
                                "Another unregistered mechanism may explain the same observation.",
                            ),
                            recommended_next_observations=(
                                "Collect another bounded read-only observation for comparison.",
                            ),
                            confidence=0.55,
                            memory=memory,
                            residual_anomaly_refs=residual_refs,
                        )
                        terminal = DiagnosisTerminalV1.OPEN_WORLD
                        lane = DiagnosisLaneV1.OPEN_WORLD
                        roots = (by_logical[root_logical],)
                        broad_domain = domain.value
                        mechanism = "UNREGISTERED_OBSERVED_ANOMALY"
                        report_payload = report.model_dump(mode="json")
                    else:
                        terminal = (
                            DiagnosisTerminalV1.CONFLICTING_EVIDENCE
                            if gate.disposition
                            is NoveltyDispositionV23.CONFLICTING_EVIDENCE
                            else DiagnosisTerminalV1.INSUFFICIENT_EVIDENCE
                        )
                        lane = DiagnosisLaneV1.ABSTAIN
                        algorithmic_reasons.update(gate.reason_codes)

        result_payload: dict[str, Any] = {
            "schema_version": "ecomsre.product.diagnosis-result.v1",
            "diagnosis_id": diagnosis_id or new_product_id("diag"),
            "incident_id": incident.incident_id,
            "terminal": terminal,
            "core_or_extension_or_open_world": lane,
            "root_service_ids": tuple(sorted(roots)),
            "mechanism": mechanism,
            "broad_domain": broad_domain,
            "supporting_evidence_refs": tuple(sorted(set(support))),
            "contradicting_evidence_refs": tuple(sorted(set(contradict))),
            "capability_limitations": tuple(sorted(limitations)),
            "provisional_report": report_payload,
            "action_authority": ActionAuthorityV1.NONE,
            "agent_writes": 0,
            "runbook_executions": 0,
            "provider_calls": 0,
            "memory_sha256": memory.memory_sha256,
            "created_at": created_at,
        }
        result = DiagnosisResultV1.model_validate(
            {
                **result_payload,
                "result_sha256": semantic_sha256_v22(
                    DiagnosisResultV1.model_construct(
                        **result_payload,
                        result_sha256="0" * 64,
                    ).model_dump(mode="json", exclude={"result_sha256"})
                ),
            }
        )
        trace = DiagnosisDecisionTraceV0232.build(
            incident_id=incident.incident_id,
            diagnosis_id=result.diagnosis_id,
            known_admission_status=(
                "MULTIPLE_ADMISSIONS"
                if effective_conflicting
                else "SINGLE_ADMISSION"
                if effective_admissions
                else "NONE"
            ),
            extension_match_count=extension_match_count,
            no_incident_admissible=effective_no_incident_admissible,
            required_coverage_satisfied=required_coverage,
            failed_sources=failed_sources,
            novelty_gate_disposition=novelty_gate_disposition,
            novelty_gate_reason_codes=tuple(
                sorted(set(novelty_gate_reason_codes).union(algorithmic_reasons))
            ),
            residual_anomaly_ids=residual_anomaly_ids,
        )
        refs_by_action: dict[str, list[str]] = {}
        for reference in memory.evidence_refs:
            refs_by_action.setdefault(reference.action_id, []).append(reference.evidence_ref)
        observations: list[dict[str, Any]] = []
        for snapshot in acquisition.snapshots:
            action = snapshot["action"]
            action_id = str(action["action_id"])
            read_outcome = snapshot["read_outcome"]
            refs = refs_by_action.get(action_id) or [
                f"o:{action_id}:{str(read_outcome['outcome_sha256'])[:12]}"
            ]
            for evidence_ref in refs:
                observations.append(
                    {
                        "evidence_ref": evidence_ref,
                        "source": str(action["source"]),
                        "action_id": action_id,
                        "payload": snapshot,
                    }
                )
        observations.extend(
            {
                "evidence_ref": observation.evidence_ref,
                "source": observation.source.value,
                "action_id": f"capability:v0232:{observation.source.value.lower()}",
                "payload": observation.model_dump(mode="json"),
            }
            for observation in acquisition.capability_observations_v0232
        )
        return result, tuple(observations), trace


__all__ = ("ProductDiagnosisBridgeV1",)
