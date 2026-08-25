"""v2.3.1 conflict policy over the shared total v2.3.2 interpretation layer."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ecomsre.dta_v2.v22.memory import SalientEvidenceMemoryV22, SignalStrengthV22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.anomaly_interpretation_v232 import (
    AnomalyInterpretationRegistryV232,
    DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232,
    InterpretationSourceV232,
)
from ecomsre.dta_v2.v23.conflict_model_v231 import (
    ConflictAssessmentV231,
    ConflictTypeV231,
    InterpretationClusterV231,
    InterpretationEdgeV231,
    _STRENGTH_WEIGHT,
    _candidate_discriminating_sources,
    _coherence_reason,
    _components,
    _edge,
)
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
)
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


def build_interpretation_clusters_v232(
    *,
    graph: ResidualEvidenceGraphV23,
    memory: SalientEvidenceMemoryV22,
    topology_edges: tuple[tuple[str, str], ...] = (),
    normal_resource_services: tuple[str, ...] = (),
    registry: AnomalyInterpretationRegistryV232 = (
        DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232
    ),
) -> tuple[InterpretationClusterV231, ...]:
    if normal_resource_services != tuple(sorted(set(normal_resource_services))):
        raise ValueError("normal resource services are not canonical")
    residual_ids = set(graph.residual_anomaly_ids)
    interpretations = {
        item.anomaly_id: registry.interpret(anomaly=item, memory=memory)
        for item in graph.generic_anomalies
        if item.anomaly_id in residual_ids
    }
    anomalies = tuple(
        item
        for item in graph.generic_anomalies
        if item.anomaly_id in residual_ids
        and item.strength in {SignalStrengthV22.MODERATE, SignalStrengthV22.STRONG}
        and interpretations[item.anomaly_id].interpretation_source
        is not InterpretationSourceV232.COVERAGE_STATE
    )
    topology = {frozenset(edge) for edge in topology_edges}
    contradictions: dict[str, list[InterpretationEdgeV231]] = defaultdict(list)
    runtime_failure_kinds = {
        GenericAnomalyKindV23.RUNTIME_NOT_RUNNING,
        GenericAnomalyKindV23.RUNTIME_UNHEALTHY,
    }
    resource_failure_kinds = {
        GenericAnomalyKindV23.RESOURCE_CPU_OUTLIER,
        GenericAnomalyKindV23.RESOURCE_MEMORY_TREND,
    }
    for anomaly in anomalies:
        if (
            anomaly.kind in runtime_failure_kinds
            and anomaly.service in set(graph.healthy_runtime_services)
        ):
            contradictions[anomaly.anomaly_id].append(
                _edge(
                    anomaly.anomaly_id,
                    f"obs:runtime-healthy:{anomaly.service}",
                    "RUNTIME_STATE_EXPLICITLY_INCOMPATIBLE",
                )
            )
        if (
            anomaly.kind in resource_failure_kinds
            and anomaly.service in set(normal_resource_services)
        ):
            contradictions[anomaly.anomaly_id].append(
                _edge(
                    anomaly.anomaly_id,
                    f"obs:resources-normal:{anomaly.service}",
                    "RESOURCE_STATE_EXPLICITLY_INCOMPATIBLE",
                )
            )
    trace_first_errors = tuple(
        item
        for item in anomalies
        if item.kind is GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION
    )
    for index, left in enumerate(trace_first_errors):
        for right in trace_first_errors[index + 1 :]:
            left_surface = {left.service, *left.related_services}
            right_surface = {right.service, *right.related_services}
            if left.service == right.service or left_surface != right_surface:
                continue
            edge = _edge(
                left.anomaly_id,
                right.anomaly_id,
                "MUTUALLY_EXCLUSIVE_FIRST_ERROR_LOCALIZATIONS",
            )
            contradictions[left.anomaly_id].append(edge)
    exclusive_log_claims = tuple(
        item
        for item in anomalies
        if item.kind is GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN
        and "exclusive causal origin"
        in {value.key: value.value for value in item.observed_values}.get(
            "template",
            "",
        )
    )
    for index, left in enumerate(exclusive_log_claims):
        for right in exclusive_log_claims[index + 1 :]:
            if (
                left.service == right.service
                or frozenset((left.service, right.service)) not in topology
            ):
                continue
            contradictions[left.anomaly_id].append(
                _edge(
                    left.anomaly_id,
                    right.anomaly_id,
                    "MUTUALLY_EXCLUSIVE_CAUSAL_ORIGIN_CLAIMS",
                )
            )

    clusters: list[InterpretationClusterV231] = []
    for component in _components(anomalies, topology):
        component_ids = {item.anomaly_id for item in component}
        coherence = tuple(
            sorted(
                (
                    _edge(left.anomaly_id, right.anomaly_id, reason)
                    for index, left in enumerate(component)
                    for right in component[index + 1 :]
                    if (
                        reason := _coherence_reason(left, right, topology)
                    )
                    is not None
                ),
                key=lambda item: (item.left_id, item.right_id, item.reason_code),
            )
        )
        contradiction = tuple(
            sorted(
                (
                    edge
                    for anomaly_id in component_ids
                    for edge in contradictions[anomaly_id]
                ),
                key=lambda item: (item.left_id, item.right_id, item.reason_code),
            )
        )
        roots = tuple(sorted({item.service for item in component}))
        domains = tuple(
            sorted(
                {
                    domain
                    for item in component
                    for domain in interpretations[item.anomaly_id].candidate_domains
                },
                key=lambda item: item.value,
            )
        )
        anomaly_ids = tuple(sorted(component_ids))
        identity = {
            "candidate_root_services": roots,
            "broad_domains": domains,
            "anomaly_ids": anomaly_ids,
        }
        clusters.append(
            InterpretationClusterV231(
                schema_version="dta-v231.interpretation-cluster.v1",
                cluster_id=f"ic-v231-{semantic_sha256_v22(identity)[:16]}",
                candidate_root_services=roots,
                broad_domains=domains,
                anomaly_ids=anomaly_ids,
                evidence_refs=tuple(
                    sorted({ref for item in component for ref in item.evidence_refs})
                ),
                related_services=tuple(
                    sorted(
                        {
                            service
                            for item in component
                            for service in item.related_services
                        }
                    )
                ),
                coherence_edges=coherence,
                contradiction_edges=contradiction,
                cluster_strength=sum(
                    _STRENGTH_WEIGHT[item.strength] for item in component
                ),
            )
        )
    return tuple(sorted(clusters, key=lambda item: item.cluster_id))


def assess_conflict_v232(
    *,
    graph: ResidualEvidenceGraphV23,
    memory: SalientEvidenceMemoryV22,
    topology_edges: tuple[tuple[str, str], ...] = (),
    legal_sources: tuple[EvidenceSourceV22, ...] = (),
    remaining_reads: int,
    normal_resource_services: tuple[str, ...] = (),
) -> ConflictAssessmentV231:
    """Preserve v2.3.1 conflict policy with only interpretation made total."""

    if not 0 <= remaining_reads <= 3:
        raise ValueError("conflict assessment remaining reads are outside the bound")
    if legal_sources != tuple(
        sorted(set(legal_sources), key=lambda item: item.value)
    ):
        raise ValueError("conflict assessment legal sources are not canonical")
    clusters = build_interpretation_clusters_v232(
        graph=graph,
        memory=memory,
        topology_edges=topology_edges,
        normal_resource_services=normal_resource_services,
    )
    leading = (
        None
        if not clusters
        else min(
            clusters,
            key=lambda item: (-item.cluster_strength, item.cluster_id),
        ).cluster_id
    )
    candidate_sources = _candidate_discriminating_sources(clusters)
    sources = tuple(item for item in candidate_sources if item in set(legal_sources))
    has_explicit_contradiction = any(
        cluster.contradiction_edges for cluster in clusters
    )
    interpretation_count = sum(
        max(len(cluster.candidate_root_services), len(cluster.broad_domains))
        for cluster in clusters
    )
    material_competition = interpretation_count >= 2
    useful_read = bool(sources) and remaining_reads > 0
    if has_explicit_contradiction:
        conflict_type = (
            ConflictTypeV231.RESOLVABLE_CONFLICT
            if useful_read
            else ConflictTypeV231.IRRECONCILABLE_CONFLICT
        )
    elif material_competition:
        conflict_type = (
            ConflictTypeV231.RESOLVABLE_CONFLICT
            if useful_read
            else ConflictTypeV231.COHERENT_COMPETITION
        )
    else:
        conflict_type = ConflictTypeV231.NO_CONFLICT
    reasons: list[str] = []
    if has_explicit_contradiction:
        reasons.append("EXPLICIT_INCOMPATIBLE_OBSERVATIONS")
    if material_competition:
        reasons.append("MULTIPLE_MATERIAL_INTERPRETATIONS")
    if useful_read:
        reasons.append("USEFUL_BOUNDED_DISCRIMINATING_READ_AVAILABLE")
    elif material_competition and not has_explicit_contradiction:
        reasons.append("COMPETITION_CAN_BE_REPRESENTED_PROVISIONALLY")
    if not clusters:
        reasons.append("NO_MATERIAL_INTERPRETATION")
    unresolved: set[str] = set()
    if len({domain for item in clusters for domain in item.broad_domains}) > 1:
        unresolved.add("BROAD_FAULT_DOMAIN")
    if len(
        {root for item in clusters for root in item.candidate_root_services}
    ) > 1:
        unresolved.add("ROOT_SERVICE")
    if interpretation_count > 1:
        unresolved.add("CAUSAL_MECHANISM")
    payload: dict[str, Any] = {
        "schema_version": "dta-v231.conflict-assessment.v1",
        "conflict_type": conflict_type,
        "interpretation_clusters": clusters,
        "leading_cluster_id": leading,
        "alternative_cluster_ids": tuple(
            item.cluster_id for item in clusters if item.cluster_id != leading
        ),
        "unresolved_dimensions": tuple(sorted(unresolved)),
        "discriminating_sources": sources,
        "minimum_additional_reads": int(useful_read),
        "reason_codes": tuple(sorted(reasons)),
    }
    draft = ConflictAssessmentV231.model_construct(
        **payload,
        assessment_sha256="0" * 64,
    )
    return ConflictAssessmentV231.model_validate(
        {
            **payload,
            "assessment_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"assessment_sha256"})
            ),
        }
    )


__all__ = (
    "assess_conflict_v232",
    "build_interpretation_clusters_v232",
)
