"""Separate v2.3 discovery runtime and Increment-1 offline demo."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import model_validator

from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    contrastive_resource_action_if_eligible_v225,
)
from ecomsre.dta_v2.v22.memory import SalientEvidenceMemoryV22, SignalStrengthV22, build_memory_views_v22
from ecomsre.dta_v2.v22.predicates import MechanismV22, evaluate_no_incident_v22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, EvidenceSourceV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.real_fault_action_backend_v225 import RealFaultActionReadBackendV225
from ecomsre.dta_v2.v22.real_fault_bootstrap_v226 import (
    build_real_fault_baseline_profile_v226,
    build_real_fault_canonical_bootstrap_v226,
    real_fault_run_id_v226,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import RealFaultOpaqueCaptureV1
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import build_replay_target_coverage_v225
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
from ecomsre.dta_v2.v23.novelty_gate import (
    NoveltyDispositionV23,
    NoveltyGateDecisionV23,
    evaluate_novelty_gate_v23,
)
from ecomsre.dta_v2.v23.ontology_view import (
    ActiveOntologyViewV23,
    build_active_ontology_view_v23,
)
from ecomsre.dta_v2.v23.residual_graph import (
    KnownTerminalCandidateV23,
    ResidualEvidenceGraphV23,
    build_known_terminal_candidates_v23,
    build_residual_evidence_graph_v23,
)


class CpuDevelopmentDemoV23(DtaModelV22):
    schema_version: Literal["dta-v23.cpu-development-demo.v1"]
    development_only: Literal[True]
    source_case_id: Literal["fault-map-a"]
    baseline_case_id: Literal["baseline-map-a"]
    hidden_mechanism: MechanismV22 | None
    active_ontology: ActiveOntologyViewV23
    closed_world_terminal: KnownTerminalCandidateV23 | None
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


def _load_capture(repository_root: Path, case_id: str) -> RealFaultOpaqueCaptureV1:
    path = repository_root / f"config/dta-v226-real-fault/captures/{case_id}.json"
    return RealFaultOpaqueCaptureV1.model_validate_json(path.read_text(encoding="utf-8"))


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
    capture, memory = build_cpu_development_memory_v23(
        repository_root=repository_root
    )
    hidden = (MechanismV22.CPU_SATURATION,) if hide_cpu else ()
    view = build_active_ontology_view_v23(
        candidate_services=capture.candidate_aliases,
        hidden_mechanisms=hidden,
    )
    known = build_known_terminal_candidates_v23(view=view, memory=memory)
    no_incident = evaluate_no_incident_v22(
        memory=memory,
        candidate_services=capture.candidate_aliases,
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
        no_incident_admissible=no_incident.accepted,
        remaining_budget_before_discovery=3.0,
    )
    report = (
        _build_demo_report(graph=graph, memory=memory)
        if novelty.disposition is NoveltyDispositionV23.UNREGISTERED_INCIDENT_SUSPECTED
        else None
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.cpu-development-demo.v1",
        "development_only": True,
        "source_case_id": "fault-map-a",
        "baseline_case_id": "baseline-map-a",
        "hidden_mechanism": MechanismV22.CPU_SATURATION if hide_cpu else None,
        "active_ontology": view,
        "closed_world_terminal": known[0] if len(known) == 1 else None,
        "no_incident_admissible": no_incident.accepted,
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


__all__ = (
    "CpuDevelopmentDemoV23",
    "build_cpu_development_memory_v23",
    "run_cpu_development_demo_v23",
)
