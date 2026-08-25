"""Separate v2.3 discovery runtime and Increment-1 offline demo."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import model_validator

from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    ContrastiveResourceActionV225,
    contrastive_resource_action_if_eligible_v225,
)
from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22, StaticTopologyV22
from ecomsre.dta_v2.v22.diagnosis import AdmittedDiagnosisV22
from ecomsre.dta_v2.v22.memory import (
    SalientEvidenceMemoryV22,
    SignalStrengthV22,
    build_memory_views_v22,
)
from ecomsre.dta_v2.v22.practical_dataset import (
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.practical_runner import _baseline, _bootstrap
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.real_fault_action_backend_v225 import (
    RealFaultActionReadBackendV225,
)
from ecomsre.dta_v2.v22.real_fault_bootstrap_v226 import (
    build_real_fault_baseline_profile_v226,
    build_real_fault_canonical_bootstrap_v226,
    real_fault_run_id_v226,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import RealFaultOpaqueCaptureV1
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import (
    build_replay_target_coverage_v225,
)
from ecomsre.dta_v2.v22.replay import QuerySpecificReplayBackendV22, ReadOutcomeV22
from ecomsre.dta_v2.v23.contracts import (
    ProvisionalFaultDomainV23,
    ProvisionalIncidentReportV23,
    build_provisional_report_v23,
)
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    GenericAnomalyV23,
    extract_generic_anomalies_v23,
)
from ecomsre.dta_v2.v23.discovery_router import (
    DiscoveryReadOutcomeClassV23,
    MAX_DISCOVERY_READS_V23,
    NegativeCoverageLedgerV23,
    build_discovery_plan_v23,
    record_discovery_outcome_v23,
    resolve_discovery_action_v23,
)
from ecomsre.dta_v2.v23.novelty_gate import (
    NoveltyDispositionV23,
    NoveltyGateDecisionV23,
    evaluate_novelty_gate_v23,
)
from ecomsre.dta_v2.v23.known_admission import build_known_admission_state_v23
from ecomsre.dta_v2.v23.ontology_view import (
    ActiveOntologyViewV23,
    build_active_ontology_view_v23,
)
from ecomsre.dta_v2.v23.residual_graph import (
    ResidualEvidenceGraphV23,
    build_known_terminal_candidates_v23,
    build_residual_evidence_graph_v23,
)


class CpuDevelopmentDemoV23(DtaModelV22):
    schema_version: Literal["dta-v23.cpu-development-demo.v2"]
    development_only: Literal[True]
    source_case_id: Literal["fault-map-a"]
    baseline_case_id: Literal["baseline-map-a"]
    hidden_mechanism: MechanismV22 | None
    active_ontology: ActiveOntologyViewV23
    closed_world_terminal: AdmittedDiagnosisV22 | None
    no_incident_admissible: bool
    generic_anomalies: tuple[GenericAnomalyV23, ...]
    residual_graph: ResidualEvidenceGraphV23
    novelty: NoveltyGateDecisionV23
    provisional_report: ProvisionalIncidentReportV23 | None
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    demo_sha256: str

    @model_validator(mode="after")
    def require_demo(self) -> "CpuDevelopmentDemoV23":
        if self.hidden_mechanism is MechanismV22.CPU_SATURATION:
            if self.closed_world_terminal is not None:
                raise ValueError("hidden-CPU demo exposed a known terminal")
            if self.provisional_report is None:
                raise ValueError("hidden-CPU demo lacks a provisional report")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"demo_sha256"})
        )
        if self.demo_sha256 != expected:
            raise ValueError("CPU development demo digest differs")
        return self


class DevelopmentLeaveOneOutRunV23(DtaModelV22):
    schema_version: Literal["dta-v23.development-leave-one-out-run.v1"]
    development_only: Literal[True]
    case_id: str
    hidden_mechanism: MechanismV22 | None
    active_ontology: ActiveOntologyViewV23
    discovery_reads_used: int
    negative_coverage: NegativeCoverageLedgerV23
    final_disposition: NoveltyDispositionV23
    residual_graph: ResidualEvidenceGraphV23
    provisional_report: ProvisionalIncidentReportV23 | None
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    run_sha256: str

    @model_validator(mode="after")
    def require_run(self) -> "DevelopmentLeaveOneOutRunV23":
        if not 0 <= self.discovery_reads_used <= MAX_DISCOVERY_READS_V23:
            raise ValueError("development run exceeded discovery read cap")
        if (
            self.hidden_mechanism is not None
            and self.hidden_mechanism in self.active_ontology.enabled_mechanisms
        ):
            raise ValueError("development hidden mechanism remains active")
        if self.provisional_report is not None and self.final_disposition not in {
            NoveltyDispositionV23.UNREGISTERED_INCIDENT_SUSPECTED,
            NoveltyDispositionV23.KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY,
        }:
            raise ValueError("development report differs from final disposition")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"run_sha256"})
        )
        if self.run_sha256 != expected:
            raise ValueError("development run digest differs")
        return self


def _load_capture(repository_root: Path, case_id: str) -> RealFaultOpaqueCaptureV1:
    path = repository_root / f"config/dta-v226-real-fault/captures/{case_id}.json"
    return RealFaultOpaqueCaptureV1.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def build_cpu_development_memory_v23(
    *, repository_root: Path
) -> tuple[RealFaultOpaqueCaptureV1, SalientEvidenceMemoryV22]:
    """Replay the committed v2.2.6 capture without Docker or Provider calls."""

    capture = _load_capture(repository_root, "fault-map-a")
    baseline_capture = _load_capture(repository_root, "baseline-map-a")
    run_id = real_fault_run_id_v226(capture)
    backend = RealFaultActionReadBackendV225.snapshot(capture=capture, run_id=run_id)
    _bootstrap, bootstrap_outcomes = build_real_fault_canonical_bootstrap_v226(
        capture=capture,
        baseline_capture=baseline_capture,
        backend=backend,
    )
    baseline = build_real_fault_baseline_profile_v226(baseline_capture)
    target_coverage = build_replay_target_coverage_v225(
        source=EvidenceSourceV22.RESOURCES,
        candidate_services=capture.candidate_aliases,
        covered_target_services=tuple(
            sorted(item.service for item in capture.capture.resources)
        ),
    )
    action = contrastive_resource_action_if_eligible_v225(
        coverage=target_coverage,
        resources_enabled=True,
        unresolved_resource_hypotheses=len(capture.candidate_aliases),
        remaining_budget=3.0,
        bundle_mode=True,
    )
    if action is None:
        raise ValueError("v2.2.6 development capture lacks its Resources bundle")
    resource_outcome = backend.execute(action)
    memory, _full = build_memory_views_v22(
        outcomes=(*bootstrap_outcomes, resource_outcome),
        baseline=baseline,
        observed_at=capture.capture.captured_at,
        top_k=64,
    )
    return capture, memory


def _build_demo_report(
    *,
    graph: ResidualEvidenceGraphV23,
    memory: SalientEvidenceMemoryV22,
) -> ProvisionalIncidentReportV23:
    residual_ids = set(graph.residual_anomaly_ids)
    resource_anomalies = tuple(
        item
        for item in graph.generic_anomalies
        if item.anomaly_id in residual_ids
        and item.kind is GenericAnomalyKindV23.RESOURCE_CPU_OUTLIER
        and item.strength is SignalStrengthV22.STRONG
    )
    if len(resource_anomalies) != 1:
        raise ValueError("hidden-CPU demo lacks one unambiguous Resources outlier")
    primary = resource_anomalies[0]
    residual_refs = {
        item.anomaly_id: item.evidence_refs
        for item in graph.generic_anomalies
        if item.anomaly_id in residual_ids
    }
    return build_provisional_report_v23(
        terminal="UNREGISTERED_INCIDENT_SUSPECTED",
        candidate_services=graph.candidate_services,
        suspected_root_services=(primary.service,),
        affected_services=(primary.service,),
        broad_fault_domain=ProvisionalFaultDomainV23.RESOURCE,
        provisional_mechanism_label="sustained-compute-resource-pressure",
        mechanism_description=(
            "One opaque service has a strong target-complete compute-resource "
            "outlier while the compared runtime remains healthy."
        ),
        observed_symptoms=tuple(
            item.summary
            for item in graph.generic_anomalies
            if item.anomaly_id in residual_ids
        ),
        supporting_evidence_refs=primary.evidence_refs,
        contradicting_evidence_refs=(),
        unexplained_anomaly_ids=graph.residual_anomaly_ids,
        alternative_hypotheses=(
            "A transient demand spike could produce a similar resource surface.",
        ),
        recommended_next_observations=(
            "Compare another bounded resource window for the same opaque candidates.",
        ),
        confidence=0.90,
        memory=memory,
        residual_anomaly_refs=residual_refs,
    )


def run_cpu_development_demo_v23(
    *,
    repository_root: Path,
    hide_cpu: bool,
) -> CpuDevelopmentDemoV23:
    capture, memory = build_cpu_development_memory_v23(repository_root=repository_root)
    hidden = (MechanismV22.CPU_SATURATION,) if hide_cpu else ()
    view = build_active_ontology_view_v23(
        candidate_services=capture.candidate_aliases,
        hidden_mechanisms=hidden,
    )
    admission = build_known_admission_state_v23(
        view=view,
        memory=memory,
    )
    known = build_known_terminal_candidates_v23(
        admitted_diagnoses=admission.admitted_diagnoses
    )
    anomalies = extract_generic_anomalies_v23(
        memory=memory,
        candidate_services=capture.candidate_aliases,
    )
    graph = build_residual_evidence_graph_v23(
        candidate_services=capture.candidate_aliases,
        generic_anomalies=anomalies,
        known_terminal_candidates=known,
        memory=memory,
    )
    novelty = evaluate_novelty_gate_v23(
        graph=graph,
        no_incident_admissible=admission.no_incident_admissible,
        remaining_budget_before_discovery=3.0,
        conflicting_evidence=admission.conflicting_evidence,
    )
    report = (
        _build_demo_report(graph=graph, memory=memory)
        if novelty.disposition is NoveltyDispositionV23.UNREGISTERED_INCIDENT_SUSPECTED
        else None
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.cpu-development-demo.v2",
        "development_only": True,
        "source_case_id": "fault-map-a",
        "baseline_case_id": "baseline-map-a",
        "hidden_mechanism": MechanismV22.CPU_SATURATION if hide_cpu else None,
        "active_ontology": view,
        "closed_world_terminal": admission.admitted_diagnosis,
        "no_incident_admissible": admission.no_incident_admissible,
        "generic_anomalies": anomalies,
        "residual_graph": graph,
        "novelty": novelty,
        "provisional_report": report,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    draft = CpuDevelopmentDemoV23.model_construct(
        **payload,
        demo_sha256="0" * 64,
    )
    return CpuDevelopmentDemoV23.model_validate(
        {
            **payload,
            "demo_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"demo_sha256"})
            ),
        }
    )


def _build_read_outcome_v23(
    *,
    action: ContrastiveResourceActionV225,
    capture: object,
) -> ReadOutcomeV22:
    resources = tuple(getattr(capture, "resources"))
    by_service = {item.service: item for item in resources}
    records = tuple(
        by_service[target] for target in action.target_services if target in by_service
    )
    if not records:
        status = ReadSourceStatusV22.SUCCESS_EMPTY
    elif len(records) != len(action.target_services) or any(
        item.sampling_window_seconds != action.request.sampling_window_seconds
        or len(item.samples) != action.request.sample_count
        for item in records
    ):
        status = ReadSourceStatusV22.FAILURE_SCHEMA
        records = ()
    else:
        status = ReadSourceStatusV22.SUCCESS_NONEMPTY
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.read-outcome.v1",
        "action_id": action.action_id,
        "source": action.source,
        "request_sha256": action.request_sha256,
        "status": status,
        "records": records,
        "truncated": False,
    }
    draft = ReadOutcomeV22.model_construct(**payload, outcome_sha256="0" * 64)
    return ReadOutcomeV22.model_validate(
        {
            **payload,
            "outcome_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"outcome_sha256"})
            ),
        }
    )


_DOMAIN_BY_ANOMALY_KIND = {
    GenericAnomalyKindV23.RUNTIME_NOT_RUNNING: ProvisionalFaultDomainV23.RUNTIME,
    GenericAnomalyKindV23.RUNTIME_UNHEALTHY: ProvisionalFaultDomainV23.RUNTIME,
    GenericAnomalyKindV23.RUNTIME_RESTART_ANOMALY: ProvisionalFaultDomainV23.RUNTIME,
    GenericAnomalyKindV23.RESOURCE_CPU_OUTLIER: ProvisionalFaultDomainV23.RESOURCE,
    GenericAnomalyKindV23.RESOURCE_MEMORY_TREND: ProvisionalFaultDomainV23.RESOURCE,
    GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION: ProvisionalFaultDomainV23.DEPENDENCY,
    GenericAnomalyKindV23.TRACE_LATENCY_OUTLIER: ProvisionalFaultDomainV23.DEPENDENCY,
    GenericAnomalyKindV23.RECENT_CHANGE_CORRELATION: ProvisionalFaultDomainV23.CONFIGURATION,
}


_LABEL_BY_DOMAIN = {
    ProvisionalFaultDomainV23.CONFIGURATION: "opaque-change-correlated-divergence",
    ProvisionalFaultDomainV23.RUNTIME: "opaque-runtime-state-divergence",
    ProvisionalFaultDomainV23.RESOURCE: "persistent-resource-profile-divergence",
    ProvisionalFaultDomainV23.DEPENDENCY: "cross-service-request-path-degradation",
    ProvisionalFaultDomainV23.UNKNOWN: "unclassified-observable-divergence",
}


def _deterministic_development_report_v23(
    *,
    disposition: NoveltyDispositionV23,
    graph: ResidualEvidenceGraphV23,
    memory: SalientEvidenceMemoryV22,
) -> ProvisionalIncidentReportV23 | None:
    if disposition not in {
        NoveltyDispositionV23.UNREGISTERED_INCIDENT_SUSPECTED,
        NoveltyDispositionV23.KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY,
    }:
        return None
    residual = tuple(
        item
        for item in graph.generic_anomalies
        if item.anomaly_id in set(graph.residual_anomaly_ids)
    )
    if not residual:
        return None
    weights = {SignalStrengthV22.STRONG: 2, SignalStrengthV22.MODERATE: 1}
    service_scores = Counter(
        {
            service: sum(
                weights.get(item.strength, 0)
                for item in residual
                if item.service == service
            )
            for service in graph.candidate_services
        }
    )
    root = sorted(service_scores, key=lambda item: (-service_scores[item], item))[0]
    selected = tuple(item for item in residual if item.service == root)
    if not selected:
        selected = (residual[0],)
        root = selected[0].service
    domains = Counter(
        _DOMAIN_BY_ANOMALY_KIND.get(item.kind, ProvisionalFaultDomainV23.UNKNOWN)
        for item in selected
    )
    domain = sorted(domains, key=lambda item: (-domains[item], item.value))[0]
    residual_refs = {item.anomaly_id: item.evidence_refs for item in residual}
    terminal: Literal[
        "UNREGISTERED_INCIDENT_SUSPECTED",
        "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY",
    ] = (
        "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY"
        if disposition is NoveltyDispositionV23.KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY
        else "UNREGISTERED_INCIDENT_SUSPECTED"
    )
    return build_provisional_report_v23(
        terminal=terminal,
        candidate_services=graph.candidate_services,
        suspected_root_services=(root,),
        affected_services=(root,),
        broad_fault_domain=domain,
        provisional_mechanism_label=_LABEL_BY_DOMAIN.get(
            domain,
            _LABEL_BY_DOMAIN[ProvisionalFaultDomainV23.UNKNOWN],
        ),
        mechanism_description=(
            "Mechanism-independent anomalies remain unexplained by the active "
            "closed-world ontology for one opaque candidate service."
        ),
        observed_symptoms=tuple(item.summary for item in selected),
        supporting_evidence_refs=tuple(
            sorted({ref for item in selected for ref in item.evidence_refs})
        ),
        contradicting_evidence_refs=(),
        unexplained_anomaly_ids=tuple(sorted(item.anomaly_id for item in selected)),
        alternative_hypotheses=(),
        recommended_next_observations=(),
        confidence=0.50,
        memory=memory,
        residual_anomaly_refs=residual_refs,
    )


def _classify_discovery_outcome(
    *,
    outcome: ReadOutcomeV22,
    before_anomaly_ids: set[str],
    after_anomaly_ids: set[str],
) -> tuple[DiscoveryReadOutcomeClassV23, tuple[str, ...]]:
    new_ids = tuple(sorted(after_anomaly_ids - before_anomaly_ids))
    if outcome.status in {
        ReadSourceStatusV22.FAILURE_UNAVAILABLE,
        ReadSourceStatusV22.FAILURE_TIMEOUT,
        ReadSourceStatusV22.FAILURE_SCHEMA,
    }:
        return DiscoveryReadOutcomeClassV23.SOURCE_FAILURE, ()
    if new_ids:
        return DiscoveryReadOutcomeClassV23.ANOMALY_YIELD, new_ids
    if outcome.status is ReadSourceStatusV22.SUCCESS_EMPTY:
        return DiscoveryReadOutcomeClassV23.EMPTY_CAPTURED, ()
    return DiscoveryReadOutcomeClassV23.NONEMPTY_NO_NEW_ANOMALY, ()


def run_development_leave_one_out_v23(
    *,
    repository_root: Path,
    case_id: str,
    hidden_mechanism: MechanismV22 | None,
) -> DevelopmentLeaveOneOutRunV23:
    """Run committed replay evidence only; no Provider, Docker, Agent, or Runbook."""

    case_set = load_practical_case_set_v22(
        repository_root / "config/dta-v22-sprint/development/cases.json"
    )
    spec = next((item for item in case_set.cases if item.case_id == case_id), None)
    if spec is None:
        raise ValueError("development case is absent")
    case = materialize_practical_case_v22(spec=spec, repository_root=repository_root)
    topology = StaticTopologyV22.build(
        services=case.candidate_services,
        edges=case.topology_edges,
    )
    outcomes, _snapshot, _full, catalog = _bootstrap(
        case=case,
        topology=topology,
        run_id=semantic_sha256_v22(
            {"case_id": case.case_id, "lane": "dta-v23-development"}
        )[:32],
    )
    memory, _ = build_memory_views_v22(
        outcomes=outcomes,
        baseline=_baseline(case),
        observed_at=case.capture.captured_at,
        top_k=64,
    )
    view = build_active_ontology_view_v23(
        candidate_services=case.candidate_services,
        hidden_mechanisms=(hidden_mechanism,) if hidden_mechanism is not None else (),
    )
    backend = QuerySpecificReplayBackendV22(case.capture)
    negative = NegativeCoverageLedgerV23.empty()
    reads_used = 0
    remaining_budget = 3.0
    target_complete_resources = (
        tuple(sorted(item.service for item in case.capture.resources))
        == case.candidate_services
    )

    def state() -> tuple[ResidualEvidenceGraphV23, NoveltyGateDecisionV23]:
        admission = build_known_admission_state_v23(
            view=view,
            memory=memory,
            topology_edges=case.topology_edges,
        )
        known = build_known_terminal_candidates_v23(
            admitted_diagnoses=admission.admitted_diagnoses,
        )
        anomalies = extract_generic_anomalies_v23(
            memory=memory,
            candidate_services=case.candidate_services,
        )
        graph = build_residual_evidence_graph_v23(
            candidate_services=case.candidate_services,
            generic_anomalies=anomalies,
            known_terminal_candidates=known,
            memory=memory,
        )
        failures = tuple(
            sorted(
                {
                    item.source
                    for item in negative.entries
                    if item.outcome_class is DiscoveryReadOutcomeClassV23.SOURCE_FAILURE
                },
                key=lambda item: item.value,
            )
        )
        decision = evaluate_novelty_gate_v23(
            graph=graph,
            no_incident_admissible=admission.no_incident_admissible,
            remaining_budget_before_discovery=3.0,
            required_source_failures=failures,
            conflicting_evidence=admission.conflicting_evidence,
        )
        return graph, decision

    graph, decision = state()
    while decision.disposition is NoveltyDispositionV23.INSUFFICIENT_EVIDENCE:
        plan = build_discovery_plan_v23(
            catalog=catalog,
            graph=graph,
            negative_coverage=negative,
            reads_used=reads_used,
            remaining_weighted_budget=remaining_budget,
            target_complete_resource_coverage=target_complete_resources,
        )
        if plan is None:
            break
        action = resolve_discovery_action_v23(
            option=plan.selected_action,
            catalog=catalog,
            target_complete_resource_coverage=target_complete_resources,
        )
        before_ids = {item.anomaly_id for item in graph.generic_anomalies}
        if isinstance(action, ContrastiveResourceActionV225):
            outcome = _build_read_outcome_v23(action=action, capture=case.capture)
        else:
            if not isinstance(action, EvidenceActionV22):
                raise TypeError("resolved discovery action has an unsupported type")
            outcome = backend.execute(action)
        outcomes = (*outcomes, outcome)
        memory, _ = build_memory_views_v22(
            outcomes=outcomes,
            baseline=_baseline(case),
            observed_at=case.capture.captured_at,
            top_k=64,
        )
        after = extract_generic_anomalies_v23(
            memory=memory,
            candidate_services=case.candidate_services,
        )
        outcome_class, new_ids = _classify_discovery_outcome(
            outcome=outcome,
            before_anomaly_ids=before_ids,
            after_anomaly_ids={item.anomaly_id for item in after},
        )
        negative = record_discovery_outcome_v23(
            ledger=negative,
            action=plan.selected_action,
            outcome_class=outcome_class,
            new_anomaly_ids=new_ids,
        )
        reads_used += 1
        remaining_budget = max(
            0.0,
            remaining_budget - plan.selected_action.weighted_cost,
        )
        graph, decision = state()

    report = _deterministic_development_report_v23(
        disposition=decision.disposition,
        graph=graph,
        memory=memory,
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.development-leave-one-out-run.v1",
        "development_only": True,
        "case_id": case.case_id,
        "hidden_mechanism": hidden_mechanism,
        "active_ontology": view,
        "discovery_reads_used": reads_used,
        "negative_coverage": negative,
        "final_disposition": decision.disposition,
        "residual_graph": graph,
        "provisional_report": report,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    draft = DevelopmentLeaveOneOutRunV23.model_construct(
        **payload,
        run_sha256="0" * 64,
    )
    return DevelopmentLeaveOneOutRunV23.model_validate(
        {
            **payload,
            "run_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"run_sha256"})
            ),
        }
    )


def assert_v23_artifact_is_non_actionable(artifact: object) -> AdmittedDiagnosisV22:
    """The only v2.3-to-action bridge accepts an actual v2.2 admission."""

    if not isinstance(artifact, AdmittedDiagnosisV22):
        raise TypeError("v2.3 provisional and registry artifacts are non-actionable")
    return artifact


__all__ = (
    "CpuDevelopmentDemoV23",
    "DevelopmentLeaveOneOutRunV23",
    "assert_v23_artifact_is_non_actionable",
    "build_cpu_development_memory_v23",
    "run_development_leave_one_out_v23",
    "run_cpu_development_demo_v23",
)
