"""Product ordering bridge over the frozen core and open-world diagnosis contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ecomsre.dta_v2.v22.memory import build_memory_views_v22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, ReadSourceStatusV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts import (
    ProvisionalFaultDomainV23,
    build_provisional_report_v23,
)
from ecomsre.dta_v2.v23.generic_anomalies import extract_generic_anomalies_v23
from ecomsre.dta_v2.v23.known_admission import build_known_admission_state_v23
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
from ecomsre.product.incidents.read_backend import ProductReadAcquisitionV1


def _domain_for_anomalies(anomalies: tuple[Any, ...]) -> ProvisionalFaultDomainV23:
    values = {item.kind.value for item in anomalies}
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
    ) -> tuple[DiagnosisResultV1, tuple[dict[str, Any], ...]]:
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
        anomalies = extract_generic_anomalies_v23(
            memory=memory,
            candidate_services=candidates,
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

        if admission.conflicting_evidence:
            terminal = DiagnosisTerminalV1.CONFLICTING_EVIDENCE
            lane = DiagnosisLaneV1.ABSTAIN
            limitations.add("CORE_MULTIPLE_ADMISSIONS")
            support = tuple(
                sorted(
                    {
                        ref
                        for diagnosis in admission.admitted_diagnoses
                        for ref in diagnosis.supporting_evidence_refs
                    }
                )
            )
        elif admission.admitted_diagnosis is not None:
            diagnosis = admission.admitted_diagnosis
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
                limitations.add("EXTENSION_MULTIPLE_ADMISSIONS")
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
                    admission.no_incident_admissible
                    and not anomalies
                    and required_coverage
                ):
                    terminal = DiagnosisTerminalV1.NO_INCIDENT
                    lane = DiagnosisLaneV1.NO_INCIDENT
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
                        limitations.update(gate.reason_codes)

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
        return result, tuple(observations)


__all__ = ("ProductDiagnosisBridgeV1",)
