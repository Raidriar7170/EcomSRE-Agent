from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.diagnosis import filter_candidates_v22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.conflict_model_v231 import (
    ConflictTypeV231,
    assess_conflict_v231,
)
from ecomsre.dta_v2.v23.contracts_v231 import (
    ConfidenceBandV231,
    ReportUncertaintyModeV231,
    build_competing_hypothesis_set_v231,
    build_competing_report_v231,
)
from ecomsre.dta_v2.v23.discovery_router import (
    DiscoveryReadOutcomeClassV23,
    NegativeCoverageLedgerV23,
    record_discovery_outcome_v23,
)
from ecomsre.dta_v2.v23.discriminating_router_v231 import (
    _option,
    _separated_pairs_v231,
    build_discriminating_plan_v231,
)
from ecomsre.dta_v2.v23.evaluation import FixedEvaluationArtifactV23
from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyKindV23
from ecomsre.dta_v2.v23.novelty_gate_v231 import (
    NoveltyDispositionV231,
    evaluate_novelty_gate_v231,
)
from ecomsre.dta_v2.v23.residual_graph import (
    ResidualEvidenceGraphV23,
    SourceCoverageV23,
)


ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "docs/results/dta-v23-open-world-evaluation.json"


@pytest.fixture(scope="module")
def artifact() -> FixedEvaluationArtifactV23:
    return FixedEvaluationArtifactV23.model_validate_json(RESULT.read_bytes())


def _pair(artifact: FixedEvaluationArtifactV23, case_id: str):
    return next(item for item in artifact.pairs if item.case_id == case_id)


def _catalog(services: tuple[str, ...], edges: tuple[tuple[str, str], ...]):
    return build_action_catalog_v22(
        candidate_services=services,
        topology=StaticTopologyV22.build(services=services, edges=edges),
        capability_registry=build_default_tool_capability_registry_v22(),
        executed_action_ids=(),
        remaining_budget=3.0,
    )


def _pre_discovery_metric_competition(
    graph: ResidualEvidenceGraphV23,
) -> ResidualEvidenceGraphV23:
    anomalies = tuple(
        item
        for item in graph.generic_anomalies
        if item.kind
        in {
            GenericAnomalyKindV23.METRIC_ERROR_OUTLIER,
            GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER,
        }
    )
    payload = {
        field: getattr(graph, field)
        for field in type(graph).model_fields
        if field != "graph_sha256"
    }
    payload.update(
        generic_anomalies=anomalies,
        explained_anomaly_ids=(),
        residual_anomaly_ids=tuple(item.anomaly_id for item in anomalies),
        contradicted_anomaly_ids=(),
        source_coverage=tuple(
            SourceCoverageV23(
                source=source,
                queried=source
                in {
                    EvidenceSourceV22.LOGS,
                    EvidenceSourceV22.METRICS,
                    EvidenceSourceV22.RUNTIME,
                },
                covered_services=(
                    graph.candidate_services
                    if source in {EvidenceSourceV22.METRICS, EvidenceSourceV22.RUNTIME}
                    else ()
                ),
                successful_observations=(
                    1
                    if source
                    in {
                        EvidenceSourceV22.LOGS,
                        EvidenceSourceV22.METRICS,
                        EvidenceSourceV22.RUNTIME,
                    }
                    else 0
                ),
                failed_observations=0,
            )
            for source in EvidenceSourceV22
        ),
        explanation_coverage=0.0,
        contrastive_target_present=False,
    )
    draft = ResidualEvidenceGraphV23.model_construct(
        **payload,
        graph_sha256="0" * 64,
    )
    return ResidualEvidenceGraphV23.model_validate(
        {
            **payload,
            "graph_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"graph_sha256"})
            ),
        }
    )


def test_resolvable_competition_exposes_one_discriminating_action(
    artifact: FixedEvaluationArtifactV23,
) -> None:
    pair = _pair(artifact, "ow-011")
    observed_graph = pair.open_world.residual_graph
    assert observed_graph is not None
    graph = _pre_discovery_metric_competition(observed_graph)
    edges = (("svc-46c27b44e9", "svc-90a131dcc4"),)
    assessment = assess_conflict_v231(
        graph=graph,
        topology_edges=edges,
        legal_sources=tuple(
            sorted(
                (
                    EvidenceSourceV22.LOGS,
                    EvidenceSourceV22.RESOURCES,
                    EvidenceSourceV22.TRACES,
                ),
                key=lambda item: item.value,
            )
        ),
        remaining_reads=2,
    )
    plan = build_discriminating_plan_v231(
        catalog=_catalog(graph.candidate_services, edges),
        graph=graph,
        assessment=assessment,
        negative_coverage=NegativeCoverageLedgerV23.empty(),
        reads_used=1,
        remaining_weighted_budget=2.0,
    )
    decision = evaluate_novelty_gate_v231(
        graph=graph,
        no_incident_admissible=False,
        assessment=assessment,
        discriminating_plan=plan,
    )

    assert assessment.conflict_type is ConflictTypeV231.RESOLVABLE_CONFLICT
    assert plan is not None
    assert plan.selected_action == plan.ranked_actions[0].action
    assert plan.selected_action.source in set(assessment.discriminating_sources)
    pair_counts = tuple(
        item.separated_hypothesis_pairs for item in plan.ranked_actions
    )
    assert pair_counts[0] == max(pair_counts)
    assert all(value >= 1 for value in pair_counts)
    options = tuple(
        _option(item)
        for item in _catalog(graph.candidate_services, edges).registry_actions
    )
    separated = {
        (option.source, option.target_services): len(
            _separated_pairs_v231(option=option, assessment=assessment)
        )
        for option in options
        if option.source in set(assessment.discriminating_sources)
    }
    assert max(separated.values()) > min(separated.values())
    assert decision.disposition is NoveltyDispositionV231.DISCOVERY_READ_REQUIRED


def test_no_useful_action_plus_strong_competition_produces_evidence_backed_report(
    artifact: FixedEvaluationArtifactV23,
) -> None:
    pair = _pair(artifact, "ow-011")
    observed_graph = pair.open_world.residual_graph
    assert observed_graph is not None
    graph = _pre_discovery_metric_competition(observed_graph)
    assessment = assess_conflict_v231(
        graph=graph,
        topology_edges=(("svc-46c27b44e9", "svc-90a131dcc4"),),
        legal_sources=(),
        remaining_reads=0,
    )
    decision = evaluate_novelty_gate_v231(
        graph=graph,
        no_incident_admissible=False,
        assessment=assessment,
        discriminating_plan=None,
    )
    hypotheses = build_competing_hypothesis_set_v231(
        graph=graph,
        assessment=assessment,
    )
    report = build_competing_report_v231(
        graph=graph,
        assessment=assessment,
        hypothesis_set=hypotheses,
    )

    assert decision.disposition is (
        NoveltyDispositionV231.UNREGISTERED_INCIDENT_WITH_COMPETING_HYPOTHESES
    )
    assert 2 <= len(report.competing_hypotheses) <= 4
    assert report.uncertainty_mode is ReportUncertaintyModeV231.COMPETING_HYPOTHESES
    assert report.preferred_hypothesis_id == hypotheses.leading_hypothesis_id
    assert len(report.alternative_hypotheses) >= 2
    assert report.unresolved_questions
    assert report.confidence <= 0.65
    assert report.confidence_band in {ConfidenceBandV231.LOW, ConfidenceBandV231.MEDIUM}
    assert report.action_authority == "NONE"
    assert all(item.supporting_evidence_refs for item in report.competing_hypotheses)


def test_discriminating_read_uses_shared_cap_and_negative_coverage_blocks_repeat(
    artifact: FixedEvaluationArtifactV23,
) -> None:
    pair = _pair(artifact, "ow-011")
    observed_graph = pair.open_world.residual_graph
    assert observed_graph is not None
    graph = _pre_discovery_metric_competition(observed_graph)
    edges = (("svc-46c27b44e9", "svc-90a131dcc4"),)
    assessment = assess_conflict_v231(
        graph=graph,
        topology_edges=edges,
        legal_sources=(EvidenceSourceV22.LOGS, EvidenceSourceV22.TRACES),
        remaining_reads=2,
    )
    catalog = _catalog(graph.candidate_services, edges)
    first = build_discriminating_plan_v231(
        catalog=catalog,
        graph=graph,
        assessment=assessment,
        negative_coverage=NegativeCoverageLedgerV23.empty(),
        reads_used=1,
        remaining_weighted_budget=2.0,
    )
    assert first is not None
    ledger = record_discovery_outcome_v23(
        ledger=NegativeCoverageLedgerV23.empty(),
        action=first.selected_action,
        outcome_class=DiscoveryReadOutcomeClassV23.NONEMPTY_NO_NEW_ANOMALY,
        new_anomaly_ids=(),
    )
    second = build_discriminating_plan_v231(
        catalog=catalog,
        graph=graph,
        assessment=assessment,
        negative_coverage=ledger,
        reads_used=2,
        remaining_weighted_budget=1.0,
    )

    assert second is None or second.selected_action != first.selected_action
    assert (
        build_discriminating_plan_v231(
            catalog=catalog,
            graph=graph,
            assessment=assessment,
            negative_coverage=ledger,
            reads_used=3,
            remaining_weighted_budget=1.0,
        )
        is None
    )


def test_known_no_incident_and_weak_evidence_remain_prioritized(
    artifact: FixedEvaluationArtifactV23,
) -> None:
    known_pair = _pair(artifact, "ow-015")
    no_pair = _pair(artifact, "ow-020")
    weak_pair = _pair(artifact, "ow-019")
    known_graph = known_pair.open_world.residual_graph
    no_graph = no_pair.open_world.residual_graph
    weak_graph = weak_pair.open_world.residual_graph
    assert known_graph is not None and no_graph is not None and weak_graph is not None

    def decision(graph, *, no_incident: bool):
        assessment = assess_conflict_v231(
            graph=graph,
            legal_sources=(),
            remaining_reads=0,
        )
        return evaluate_novelty_gate_v231(
            graph=graph,
            no_incident_admissible=no_incident,
            assessment=assessment,
            discriminating_plan=None,
        )

    assert decision(known_graph, no_incident=False).disposition is (
        NoveltyDispositionV231.KNOWN_INCIDENT
    )
    assert decision(no_graph, no_incident=True).disposition is (
        NoveltyDispositionV231.NO_INCIDENT
    )
    assert decision(weak_graph, no_incident=False).disposition is (
        NoveltyDispositionV231.INSUFFICIENT_EVIDENCE
    )


def test_v231_report_type_cannot_enter_the_v22_candidate_filter(
    artifact: FixedEvaluationArtifactV23,
) -> None:
    pair = _pair(artifact, "ow-011")
    graph = pair.open_world.residual_graph
    assert graph is not None
    assessment = assess_conflict_v231(
        graph=graph,
        legal_sources=(),
        remaining_reads=0,
    )
    report = build_competing_report_v231(
        graph=graph,
        assessment=assessment,
        hypothesis_set=build_competing_hypothesis_set_v231(
            graph=graph,
            assessment=assessment,
        ),
    )

    signature = inspect.signature(filter_candidates_v22)
    assert "ProvisionalIncidentReportV231" not in str(signature)
    assert all(
        annotation is not type(report) for annotation in filter_candidates_v22.__annotations__.values()
    )
