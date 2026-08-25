from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.diagnosis import AdmittedDiagnosisV22
from ecomsre.dta_v2.v22.memory import (
    LogCategoryV22,
    LogSalientPayloadV22,
    SalientEvidenceMemoryV22,
    SalientFactV22,
    SignalStrengthV22,
)
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts import (
    ProvisionalFaultDomainV23,
    build_provisional_report_v23,
)
from ecomsre.dta_v2.v23.discovery_runtime import (
    build_cpu_development_memory_v23,
    run_cpu_development_demo_v23,
)
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    extract_generic_anomalies_v23,
)
from ecomsre.dta_v2.v23.novelty_gate import (
    NoveltyDispositionV23,
    evaluate_novelty_gate_v23,
)
from ecomsre.dta_v2.v23.ontology_view import (
    REGISTERED_MECHANISMS_V23,
    build_active_ontology_view_v23,
    lint_provider_ontology_payload_v23,
    provider_ontology_payload_v23,
)


ROOT = Path(__file__).resolve().parents[2]


def test_default_ontology_contains_every_registered_mechanism() -> None:
    view = build_active_ontology_view_v23(candidate_services=("svc-a", "svc-b"))

    assert view.enabled_mechanisms == REGISTERED_MECHANISMS_V23
    assert view.hidden_mechanisms == ()


def test_leave_one_out_ontology_removes_hidden_mechanism_from_provider_view() -> None:
    view = build_active_ontology_view_v23(
        candidate_services=("svc-a", "svc-b"),
        hidden_mechanisms=(MechanismV22.CPU_SATURATION,),
    )

    assert MechanismV22.CPU_SATURATION not in view.enabled_mechanisms
    assert view.hidden_mechanisms == (MechanismV22.CPU_SATURATION,)
    assert all(
        item.mechanism is not MechanismV22.CPU_SATURATION
        for item in view.active_hypotheses
    )
    assert all(
        item.mechanism is not MechanismV22.CPU_SATURATION
        for item in view.active_support_clauses
    )

    payload = provider_ontology_payload_v23(view)
    lint_provider_ontology_payload_v23(
        payload=payload,
        hidden_mechanisms=view.hidden_mechanisms,
    )
    rendered = json.dumps(payload, sort_keys=True).casefold()
    assert "cpu_saturation" not in rendered
    assert "cpu-saturation" not in rendered
    assert "cpu saturation" not in rendered


def test_hidden_cpu_real_capture_runs_the_open_world_vertical_slice() -> None:
    result = run_cpu_development_demo_v23(repository_root=ROOT, hide_cpu=True)

    assert result.development_only is True
    assert result.source_case_id == "fault-map-a"
    assert result.closed_world_terminal is None
    assert result.no_incident_admissible is False
    assert result.novelty.disposition is (
        NoveltyDispositionV23.UNREGISTERED_INCIDENT_SUSPECTED
    )
    assert result.provisional_report is not None
    report = result.provisional_report
    assert report.suspected_root_services == ("svc-20e1bc90a8",)
    assert report.broad_fault_domain is ProvisionalFaultDomainV23.RESOURCE
    assert report.action_authority == "NONE"
    assert report.supporting_evidence_refs
    assert all(
        ref.startswith("e:a:resources:all-candidates:")
        for ref in report.supporting_evidence_refs
    )
    assert result.agent_writes == 0
    assert result.runbook_executions == 0


def test_full_ontology_preserves_the_known_cpu_terminal() -> None:
    result = run_cpu_development_demo_v23(repository_root=ROOT, hide_cpu=False)

    assert isinstance(result.closed_world_terminal, AdmittedDiagnosisV22)
    assert result.closed_world_terminal.mechanism is MechanismV22.CPU_SATURATION
    assert result.closed_world_terminal.root_service == "svc-20e1bc90a8"
    assert result.novelty.disposition is NoveltyDispositionV23.KNOWN_INCIDENT
    assert result.provisional_report is None


def test_other_error_log_still_creates_unknown_pattern_anomaly() -> None:
    log_payload = LogSalientPayloadV22(
        schema_version="dta-v22.salient-log.v1",
        severity="ERROR",
        normalized_template="worker pool wait exceeded capacity",
        category=LogCategoryV22.OTHER,
        downstream_service=None,
        count=3,
    )
    evidence_refs = ("e:a:logs:svc-a:0:123456789abc",)
    identity = semantic_sha256_v22(
        {
            "source": EvidenceSourceV22.LOGS.value,
            "service": "svc-a",
            "evidence_refs": evidence_refs,
            "payload": log_payload.model_dump(mode="json"),
        }
    )
    fact_payload = {
        "schema_version": "dta-v22.salient-fact.v1",
        "fact_id": f"f:logs:{identity[:16]}",
        "source": EvidenceSourceV22.LOGS,
        "service": "svc-a",
        "evidence_refs": evidence_refs,
        "signal_strength": SignalStrengthV22.NONE,
        "payload": log_payload,
    }
    draft = SalientFactV22.model_construct(**fact_payload, fact_sha256="0" * 64)
    fact = SalientFactV22.model_validate(
        {
            **fact_payload,
            "fact_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"fact_sha256"})
            ),
        }
    )
    memory = SalientEvidenceMemoryV22.model_construct(
        salient_facts=(fact,),
        predicates=(),
    )

    anomalies = extract_generic_anomalies_v23(
        memory=memory,
        candidate_services=("svc-a",),
    )

    assert len(anomalies) == 1
    assert anomalies[0].kind is GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN
    assert anomalies[0].strength is SignalStrengthV22.STRONG


def test_novelty_gate_does_not_turn_budget_or_conflict_into_discovery() -> None:
    result = run_cpu_development_demo_v23(repository_root=ROOT, hide_cpu=True)

    insufficient = evaluate_novelty_gate_v23(
        graph=result.residual_graph,
        no_incident_admissible=False,
        remaining_budget_before_discovery=0.0,
    )
    conflicting = evaluate_novelty_gate_v23(
        graph=result.residual_graph,
        no_incident_admissible=False,
        remaining_budget_before_discovery=3.0,
        conflicting_evidence=True,
    )

    assert insufficient.disposition is NoveltyDispositionV23.INSUFFICIENT_EVIDENCE
    assert conflicting.disposition is NoveltyDispositionV23.CONFLICTING_EVIDENCE


def test_single_strong_source_needs_healthy_runtime_or_contrastive_target() -> None:
    result = run_cpu_development_demo_v23(repository_root=ROOT, hide_cpu=True)
    payload = {
        name: getattr(result.residual_graph, name)
        for name in type(result.residual_graph).model_fields
        if name != "graph_sha256"
    }
    strong = next(
        item
        for item in result.residual_graph.generic_anomalies
        if item.kind is GenericAnomalyKindV23.RESOURCE_CPU_OUTLIER
    )
    payload["generic_anomalies"] = (strong,)
    payload["explained_anomaly_ids"] = ()
    payload["residual_anomaly_ids"] = (strong.anomaly_id,)
    payload["contradicted_anomaly_ids"] = ()
    payload["explanation_coverage"] = 0.0
    payload["contrastive_target_present"] = False
    draft = type(result.residual_graph).model_construct(
        **payload, graph_sha256="0" * 64
    )
    graph = type(result.residual_graph).model_validate(
        {
            **payload,
            "graph_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"graph_sha256"})
            ),
        }
    )

    decision = evaluate_novelty_gate_v23(
        graph=graph,
        no_incident_admissible=False,
        remaining_budget_before_discovery=3.0,
    )

    assert decision.disposition is (
        NoveltyDispositionV23.UNREGISTERED_INCIDENT_SUSPECTED
    )


def test_provisional_report_rejects_an_unknown_evidence_ref() -> None:
    _capture, memory = build_cpu_development_memory_v23(repository_root=ROOT)
    result = run_cpu_development_demo_v23(repository_root=ROOT, hide_cpu=True)
    residual_refs = {
        item.anomaly_id: item.evidence_refs
        for item in result.generic_anomalies
        if item.anomaly_id in set(result.residual_graph.residual_anomaly_ids)
    }

    with pytest.raises(ValueError, match="unknown evidence ref"):
        build_provisional_report_v23(
            terminal="UNREGISTERED_INCIDENT_SUSPECTED",
            candidate_services=result.residual_graph.candidate_services,
            suspected_root_services=("svc-20e1bc90a8",),
            affected_services=("svc-20e1bc90a8",),
            broad_fault_domain=ProvisionalFaultDomainV23.RESOURCE,
            provisional_mechanism_label="resource-pressure",
            mechanism_description="Visible resource pressure remains unexplained.",
            observed_symptoms=("resource outlier",),
            supporting_evidence_refs=("e:a:unknown:0:000000000000",),
            contradicting_evidence_refs=(),
            unexplained_anomaly_ids=result.residual_graph.residual_anomaly_ids,
            alternative_hypotheses=(),
            recommended_next_observations=(),
            confidence=0.5,
            memory=memory,
            residual_anomaly_refs=residual_refs,
        )
