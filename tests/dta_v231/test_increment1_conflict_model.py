from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ecomsre.dta_v2.v22.memory import SignalStrengthV22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.conflict_model_v231 import (
    ConflictTypeV231,
    assess_conflict_v231,
    audit_historical_conflicts_v231,
)
from ecomsre.dta_v2.v23.evaluation import FixedEvaluationArtifactV23
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    _build_anomaly,
)
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "docs/results/dta-v23-open-world-evaluation.json"


def _artifact() -> FixedEvaluationArtifactV23:
    return FixedEvaluationArtifactV23.model_validate_json(RESULT.read_bytes())


def _graph(case_id: str) -> ResidualEvidenceGraphV23:
    pair = next(item for item in _artifact().pairs if item.case_id == case_id)
    assert pair.open_world.residual_graph is not None
    return pair.open_world.residual_graph


def test_v23_history_manifest_binds_the_valid_negative_result() -> None:
    manifest = json.loads(
        (ROOT / "config/dta-v231/historical-results.v1.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["engineering_terminal"] == "DTA_V23_OPEN_WORLD_DISCOVERY_MVP_COMPLETE"
    assert manifest["measured_result_terminal"] == "DTA_V23_OPEN_WORLD_DISCOVERY_NOT_OBSERVED"
    for binding in manifest["bindings"]:
        path = ROOT / binding["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == binding["sha256"]
        assert path.stat().st_size == binding["size_bytes"]


def test_historical_audit_reproduces_all_eight_novelty_conflict_misses() -> None:
    audit = audit_historical_conflicts_v231(RESULT)

    assert audit.strict_conflict_miss_count == 8
    assert tuple(item.case_id for item in audit.entries) == (
        "ow-001",
        "ow-002",
        "ow-009",
        "ow-010",
        "ow-011",
        "ow-012",
        "ow-013",
        "ow-014",
    )
    assert all(item.strict_disposition == "CONFLICTING_EVIDENCE" for item in audit.entries)
    assert sum(
        item.conflict_assessment.conflict_type is not ConflictTypeV231.IRRECONCILABLE_CONFLICT
        for item in audit.entries
    ) >= 6


def test_multi_service_evidence_alone_is_not_a_contradiction() -> None:
    assessment = assess_conflict_v231(
        graph=_graph("ow-009"),
        topology_edges=(("svc-4a67a8cd51", "svc-d6cd766de9"),),
        legal_sources=(),
        remaining_reads=0,
    )

    assert not any(
        cluster.contradiction_edges for cluster in assessment.interpretation_clusters
    )
    assert assessment.conflict_type is not ConflictTypeV231.IRRECONCILABLE_CONFLICT
    assert any(
        set(cluster.candidate_root_services)
        == {"svc-4a67a8cd51", "svc-d6cd766de9"}
        and cluster.coherence_edges
        for cluster in assessment.interpretation_clusters
    )


def test_multi_domain_evidence_alone_is_coherent_competition() -> None:
    assessment = assess_conflict_v231(
        graph=_graph("ow-011"),
        topology_edges=(("svc-46c27b44e9", "svc-90a131dcc4"),),
        legal_sources=(),
        remaining_reads=0,
    )

    assert assessment.conflict_type is ConflictTypeV231.COHERENT_COMPETITION
    assert any(
        len(cluster.broad_domains) >= 2
        for cluster in assessment.interpretation_clusters
    )
    assert not any(
        cluster.contradiction_edges for cluster in assessment.interpretation_clusters
    )


def test_same_root_cross_domain_evidence_forms_one_multi_domain_cluster() -> None:
    base = _graph("ow-011")
    service = base.candidate_services[0]
    anomalies = (
        _build_anomaly(
            kind=GenericAnomalyKindV23.METRIC_ERROR_OUTLIER,
            source=EvidenceSourceV22.METRICS,
            service=service,
            related_services=(),
            strength=SignalStrengthV22.STRONG,
            summary="opaque error outlier",
            evidence_refs=(f"e:a:metrics:{service}:0:aaaaaaaaaaaa",),
            observed_values={"value": 0.3},
        ),
        _build_anomaly(
            kind=GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN,
            source=EvidenceSourceV22.LOGS,
            service=service,
            related_services=(),
            strength=SignalStrengthV22.STRONG,
            summary="opaque worker saturation",
            evidence_refs=(f"e:a:logs:{service}:0:bbbbbbbbbbbb",),
            observed_values={"template": "worker slots unavailable"},
        ),
    )
    payload = {
        field: getattr(base, field)
        for field in type(base).model_fields
        if field != "graph_sha256"
    }
    payload.update(
        generic_anomalies=tuple(sorted(anomalies, key=lambda item: item.anomaly_id)),
        explained_anomaly_ids=(),
        residual_anomaly_ids=tuple(sorted(item.anomaly_id for item in anomalies)),
        contradicted_anomaly_ids=(),
        explanation_coverage=0.0,
    )
    draft = ResidualEvidenceGraphV23.model_construct(
        **payload,
        graph_sha256="0" * 64,
    )
    graph = ResidualEvidenceGraphV23.model_validate(
        {
            **payload,
            "graph_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"graph_sha256"})
            ),
        }
    )

    assessment = assess_conflict_v231(
        graph=graph,
        topology_edges=(),
        legal_sources=(),
        remaining_reads=0,
    )

    assert len(assessment.interpretation_clusters) == 1
    assert {item.value for item in assessment.interpretation_clusters[0].broad_domains} == {
        "CONCURRENCY",
        "RUNTIME",
    }
    assert assessment.conflict_type is ConflictTypeV231.COHERENT_COMPETITION


def test_complete_normal_resource_observation_contradicts_local_resource_failure() -> None:
    base = _graph("ow-011")
    service = base.candidate_services[0]
    anomaly = _build_anomaly(
        kind=GenericAnomalyKindV23.RESOURCE_CPU_OUTLIER,
        source=EvidenceSourceV22.RESOURCES,
        service=service,
        related_services=(),
        strength=SignalStrengthV22.STRONG,
        summary="opaque local resource failure",
        evidence_refs=(f"e:a:resources:{service}:0:cccccccccccc",),
        observed_values={"cpu_p95_percent": 95.0},
    )
    payload = {
        field: getattr(base, field)
        for field in type(base).model_fields
        if field != "graph_sha256"
    }
    payload.update(
        generic_anomalies=(anomaly,),
        explained_anomaly_ids=(),
        residual_anomaly_ids=(anomaly.anomaly_id,),
        contradicted_anomaly_ids=(),
        explanation_coverage=0.0,
    )
    draft = ResidualEvidenceGraphV23.model_construct(
        **payload,
        graph_sha256="0" * 64,
    )
    graph = ResidualEvidenceGraphV23.model_validate(
        {
            **payload,
            "graph_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"graph_sha256"})
            ),
        }
    )

    assessment = assess_conflict_v231(
        graph=graph,
        topology_edges=(),
        legal_sources=(),
        remaining_reads=0,
        normal_resource_services=(service,),
    )

    assert assessment.conflict_type is ConflictTypeV231.IRRECONCILABLE_CONFLICT
    assert any(
        edge.reason_code == "RESOURCE_STATE_EXPLICITLY_INCOMPATIBLE"
        for cluster in assessment.interpretation_clusters
        for edge in cluster.contradiction_edges
    )


def test_explicit_runtime_healthy_incompatibility_is_a_contradiction_edge() -> None:
    base = _graph("ow-011")
    service = "svc-46c27b44e9"
    anomaly = _build_anomaly(
        kind=GenericAnomalyKindV23.RUNTIME_NOT_RUNNING,
        source=EvidenceSourceV22.RUNTIME,
        service=service,
        related_services=(),
        strength=SignalStrengthV22.STRONG,
        summary=f"{service} is not running",
        evidence_refs=("e:a:runtime:svc-46c27b44e9:0:aaaaaaaaaaaa",),
        observed_values={"state": "STOPPED"},
    )
    payload = {
        field: getattr(base, field)
        for field in type(base).model_fields
        if field != "graph_sha256"
    }
    payload.update(
        generic_anomalies=(anomaly,),
        explained_anomaly_ids=(),
        residual_anomaly_ids=(anomaly.anomaly_id,),
        contradicted_anomaly_ids=(),
        explanation_coverage=0.0,
        healthy_runtime_services=(service,),
    )
    draft = ResidualEvidenceGraphV23.model_construct(
        **payload,
        graph_sha256="0" * 64,
    )
    graph = ResidualEvidenceGraphV23.model_validate(
        {
            **payload,
            "graph_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"graph_sha256"})
            ),
        }
    )

    assessment = assess_conflict_v231(
        graph=graph,
        topology_edges=(),
        legal_sources=(),
        remaining_reads=0,
    )

    assert assessment.conflict_type is ConflictTypeV231.IRRECONCILABLE_CONFLICT
    assert any(
        edge.reason_code == "RUNTIME_STATE_EXPLICITLY_INCOMPATIBLE"
        for cluster in assessment.interpretation_clusters
        for edge in cluster.contradiction_edges
    )


def test_incompatible_first_error_localizations_are_a_contradiction_edge() -> None:
    base = _graph("ow-011")
    left, right = base.candidate_services
    anomalies = tuple(
        _build_anomaly(
            kind=GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION,
            source=EvidenceSourceV22.TRACES,
            service=service,
            related_services=(other,),
            strength=SignalStrengthV22.STRONG,
            summary=f"first error localized at {service}",
            evidence_refs=(f"e:a:traces:{service}:0:aaaaaaaaaaaa",),
            observed_values={"first_error_location": True},
        )
        for service, other in ((left, right), (right, left))
    )
    payload = {
        field: getattr(base, field)
        for field in type(base).model_fields
        if field != "graph_sha256"
    }
    payload.update(
        generic_anomalies=anomalies,
        explained_anomaly_ids=(),
        residual_anomaly_ids=tuple(sorted(item.anomaly_id for item in anomalies)),
        contradicted_anomaly_ids=(),
        explanation_coverage=0.0,
    )
    draft = ResidualEvidenceGraphV23.model_construct(
        **payload,
        graph_sha256="0" * 64,
    )
    graph = ResidualEvidenceGraphV23.model_validate(
        {
            **payload,
            "graph_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"graph_sha256"})
            ),
        }
    )

    assessment = assess_conflict_v231(
        graph=graph,
        topology_edges=((left, right),),
        legal_sources=(),
        remaining_reads=0,
    )

    assert assessment.conflict_type is ConflictTypeV231.IRRECONCILABLE_CONFLICT
    assert any(
        edge.reason_code == "MUTUALLY_EXCLUSIVE_FIRST_ERROR_LOCALIZATIONS"
        for cluster in assessment.interpretation_clusters
        for edge in cluster.contradiction_edges
    )
