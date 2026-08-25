"""Conflict-aware interpretation accounting for the DTA v2.3.1 discovery lane."""

from __future__ import annotations

from collections import defaultdict
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.memory import SignalStrengthV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.evaluation import (
    EvaluationCategoryV23,
    FixedEvaluationArtifactV23,
)
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    GenericAnomalyV23,
)
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


class ConflictTypeV231(str, Enum):
    NO_CONFLICT = "NO_CONFLICT"
    COHERENT_COMPETITION = "COHERENT_COMPETITION"
    RESOLVABLE_CONFLICT = "RESOLVABLE_CONFLICT"
    IRRECONCILABLE_CONFLICT = "IRRECONCILABLE_CONFLICT"


class InterpretationEdgeV231(DtaModelV22):
    left_id: str
    right_id: str
    reason_code: str

    @model_validator(mode="after")
    def require_edge(self) -> "InterpretationEdgeV231":
        if self.left_id >= self.right_id:
            raise ValueError("interpretation edge endpoints are not canonical")
        return self


class InterpretationClusterV231(DtaModelV22):
    schema_version: Literal["dta-v231.interpretation-cluster.v1"]
    cluster_id: str = Field(pattern=r"^ic-v231-[0-9a-f]{16}$")
    candidate_root_services: tuple[str, ...] = Field(min_length=1)
    broad_domains: tuple[ProvisionalFaultDomainV23, ...] = Field(min_length=1)
    anomaly_ids: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    related_services: tuple[str, ...]
    coherence_edges: tuple[InterpretationEdgeV231, ...]
    contradiction_edges: tuple[InterpretationEdgeV231, ...]
    cluster_strength: StrictFloat = Field(ge=0.0)

    @model_validator(mode="after")
    def require_cluster(self) -> "InterpretationClusterV231":
        for values, label in (
            (self.candidate_root_services, "candidate roots"),
            (self.anomaly_ids, "anomaly IDs"),
            (self.evidence_refs, "evidence refs"),
            (self.related_services, "related services"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"interpretation cluster {label} are not canonical")
        if self.broad_domains != tuple(
            sorted(set(self.broad_domains), key=lambda item: item.value)
        ):
            raise ValueError("interpretation cluster domains are not canonical")
        identity = {
            "candidate_root_services": self.candidate_root_services,
            "broad_domains": self.broad_domains,
            "anomaly_ids": self.anomaly_ids,
        }
        if self.cluster_id != f"ic-v231-{semantic_sha256_v22(identity)[:16]}":
            raise ValueError("interpretation cluster identity differs")
        return self


class ConflictAssessmentV231(DtaModelV22):
    schema_version: Literal["dta-v231.conflict-assessment.v1"]
    conflict_type: ConflictTypeV231
    interpretation_clusters: tuple[InterpretationClusterV231, ...]
    leading_cluster_id: str | None
    alternative_cluster_ids: tuple[str, ...]
    unresolved_dimensions: tuple[str, ...]
    discriminating_sources: tuple[EvidenceSourceV22, ...]
    minimum_additional_reads: StrictInt = Field(ge=0, le=1)
    reason_codes: tuple[str, ...]
    assessment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_assessment(self) -> "ConflictAssessmentV231":
        cluster_ids = tuple(item.cluster_id for item in self.interpretation_clusters)
        if cluster_ids != tuple(sorted(set(cluster_ids))):
            raise ValueError("conflict assessment clusters are not canonical")
        if self.leading_cluster_id is None:
            if cluster_ids or self.alternative_cluster_ids:
                raise ValueError("conflict assessment leading cluster is absent")
        elif self.leading_cluster_id not in set(cluster_ids):
            raise ValueError("conflict assessment leading cluster is unknown")
        if self.alternative_cluster_ids != tuple(
            item for item in cluster_ids if item != self.leading_cluster_id
        ):
            raise ValueError("conflict assessment alternatives differ")
        if self.unresolved_dimensions != tuple(sorted(set(self.unresolved_dimensions))):
            raise ValueError("conflict unresolved dimensions are not canonical")
        if self.discriminating_sources != tuple(
            sorted(set(self.discriminating_sources), key=lambda item: item.value)
        ):
            raise ValueError("conflict discriminating sources are not canonical")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("conflict reason codes are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"assessment_sha256"})
        )
        if self.assessment_sha256 != expected:
            raise ValueError("conflict assessment digest differs")
        return self


class HistoricalConflictAuditEntryV231(DtaModelV22):
    case_id: str
    strict_disposition: Literal["CONFLICTING_EVIDENCE"]
    conflict_assessment: ConflictAssessmentV231


class HistoricalConflictAuditV231(DtaModelV22):
    schema_version: Literal["dta-v231.historical-conflict-audit.v1"]
    source_result_sha256: str
    strict_conflict_miss_count: Literal[8]
    entries: tuple[HistoricalConflictAuditEntryV231, ...] = Field(
        min_length=8,
        max_length=8,
    )
    audit_sha256: str

    @model_validator(mode="after")
    def require_audit(self) -> "HistoricalConflictAuditV231":
        ids = tuple(item.case_id for item in self.entries)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("historical conflict audit cases are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"audit_sha256"})
        )
        if self.audit_sha256 != expected:
            raise ValueError("historical conflict audit digest differs")
        return self


_DOMAIN_BY_KIND = {
    GenericAnomalyKindV23.METRIC_ERROR_OUTLIER: ProvisionalFaultDomainV23.RUNTIME,
    GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER: ProvisionalFaultDomainV23.DEPENDENCY,
    GenericAnomalyKindV23.RUNTIME_NOT_RUNNING: ProvisionalFaultDomainV23.RUNTIME,
    GenericAnomalyKindV23.RUNTIME_UNHEALTHY: ProvisionalFaultDomainV23.RUNTIME,
    GenericAnomalyKindV23.RUNTIME_RESTART_ANOMALY: ProvisionalFaultDomainV23.RUNTIME,
    GenericAnomalyKindV23.RESOURCE_CPU_OUTLIER: ProvisionalFaultDomainV23.RESOURCE,
    GenericAnomalyKindV23.RESOURCE_MEMORY_TREND: ProvisionalFaultDomainV23.RESOURCE,
    GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION: ProvisionalFaultDomainV23.DEPENDENCY,
    GenericAnomalyKindV23.TRACE_LATENCY_OUTLIER: ProvisionalFaultDomainV23.DEPENDENCY,
    GenericAnomalyKindV23.LOG_ERROR_CLUSTER: ProvisionalFaultDomainV23.UNKNOWN,
    GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN: ProvisionalFaultDomainV23.CONCURRENCY,
    GenericAnomalyKindV23.RECENT_CHANGE_CORRELATION: ProvisionalFaultDomainV23.CONFIGURATION,
    GenericAnomalyKindV23.SOURCE_COVERAGE_GAP: ProvisionalFaultDomainV23.UNKNOWN,
}

_STRENGTH_WEIGHT = {
    SignalStrengthV22.STRONG: 2.0,
    SignalStrengthV22.MODERATE: 1.0,
    SignalStrengthV22.WEAK: 0.25,
    SignalStrengthV22.NONE: 0.0,
}


def _edge(left: str, right: str, reason: str) -> InterpretationEdgeV231:
    first, second = sorted((left, right))
    return InterpretationEdgeV231(left_id=first, right_id=second, reason_code=reason)


def _coherence_reason(
    left: GenericAnomalyV23,
    right: GenericAnomalyV23,
    topology: set[frozenset[str]],
) -> str | None:
    if left.service == right.service:
        return "SAME_SUSPECTED_ROOT_SERVICE"
    if set(left.evidence_refs).intersection(right.evidence_refs):
        return "SHARED_EVIDENCE_REF"
    left_surface = {left.service, *left.related_services}
    right_surface = {right.service, *right.related_services}
    if left_surface.intersection(right_surface):
        return "SHARED_TRACE_OR_PROPAGATION_PATH"
    if frozenset((left.service, right.service)) in topology:
        return "DIRECT_TOPOLOGY_ADJACENCY"
    if left.service in set(right.related_services) or right.service in set(
        left.related_services
    ):
        return "UPSTREAM_DOWNSTREAM_CONSEQUENCE"
    return None


def _components(
    anomalies: tuple[GenericAnomalyV23, ...],
    topology: set[frozenset[str]],
) -> tuple[tuple[GenericAnomalyV23, ...], ...]:
    by_id = {item.anomaly_id: item for item in anomalies}
    adjacency: dict[str, set[str]] = defaultdict(set)
    for index, left in enumerate(anomalies):
        for right in anomalies[index + 1 :]:
            if _coherence_reason(left, right, topology) is not None:
                adjacency[left.anomaly_id].add(right.anomaly_id)
                adjacency[right.anomaly_id].add(left.anomaly_id)
    result: list[tuple[GenericAnomalyV23, ...]] = []
    unseen = set(by_id)
    while unseen:
        frontier = [min(unseen)]
        component: set[str] = set()
        while frontier:
            current = frontier.pop()
            if current in component:
                continue
            component.add(current)
            frontier.extend(sorted(adjacency[current] - component, reverse=True))
        unseen.difference_update(component)
        result.append(tuple(by_id[item] for item in sorted(component)))
    return tuple(result)


def build_interpretation_clusters_v231(
    *,
    graph: ResidualEvidenceGraphV23,
    topology_edges: tuple[tuple[str, str], ...] = (),
    normal_resource_services: tuple[str, ...] = (),
) -> tuple[InterpretationClusterV231, ...]:
    if normal_resource_services != tuple(sorted(set(normal_resource_services))):
        raise ValueError("normal resource services are not canonical")
    residual_ids = set(graph.residual_anomaly_ids)
    anomalies = tuple(
        item
        for item in graph.generic_anomalies
        if item.anomaly_id in residual_ids
        and item.strength in {SignalStrengthV22.MODERATE, SignalStrengthV22.STRONG}
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
        and "exclusive causal origin" in {
            value.key: value.value for value in item.observed_values
        }.get("template", "")
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
                    if (reason := _coherence_reason(left, right, topology)) is not None
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
            sorted({_DOMAIN_BY_KIND[item.kind] for item in component}, key=lambda item: item.value)
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
                    sorted({service for item in component for service in item.related_services})
                ),
                coherence_edges=coherence,
                contradiction_edges=contradiction,
                cluster_strength=sum(_STRENGTH_WEIGHT[item.strength] for item in component),
            )
        )
    return tuple(sorted(clusters, key=lambda item: item.cluster_id))


def _candidate_discriminating_sources(
    clusters: tuple[InterpretationClusterV231, ...],
) -> tuple[EvidenceSourceV22, ...]:
    domains = {domain for cluster in clusters for domain in cluster.broad_domains}
    sources: set[EvidenceSourceV22] = set()
    sources_by_domain = {
        ProvisionalFaultDomainV23.CONFIGURATION: {
            EvidenceSourceV22.CHANGES,
            EvidenceSourceV22.LOGS,
        },
        ProvisionalFaultDomainV23.DEPENDENCY: {
            EvidenceSourceV22.TRACES,
            EvidenceSourceV22.LOGS,
        },
        ProvisionalFaultDomainV23.CONCURRENCY: {
            EvidenceSourceV22.LOGS,
            EvidenceSourceV22.TRACES,
        },
        ProvisionalFaultDomainV23.RUNTIME: {EvidenceSourceV22.RUNTIME},
        ProvisionalFaultDomainV23.RESOURCE: {EvidenceSourceV22.RESOURCES},
        ProvisionalFaultDomainV23.UNKNOWN: {
            EvidenceSourceV22.LOGS,
            EvidenceSourceV22.TRACES,
        },
    }
    if len(domains) > 1:
        for domain in domains:
            sources.update(sources_by_domain[domain])
    if {ProvisionalFaultDomainV23.CONFIGURATION, ProvisionalFaultDomainV23.DEPENDENCY}.issubset(domains):
        sources.update({EvidenceSourceV22.CHANGES, EvidenceSourceV22.TRACES, EvidenceSourceV22.LOGS})
    if {ProvisionalFaultDomainV23.DEPENDENCY, ProvisionalFaultDomainV23.CONCURRENCY}.issubset(domains):
        sources.update({EvidenceSourceV22.TRACES, EvidenceSourceV22.LOGS})
    if {ProvisionalFaultDomainV23.RUNTIME, ProvisionalFaultDomainV23.RESOURCE}.issubset(domains):
        sources.update({EvidenceSourceV22.RUNTIME, EvidenceSourceV22.RESOURCES})
    root_sets = {cluster.candidate_root_services for cluster in clusters}
    if len(root_sets) > 1:
        sources.update({EvidenceSourceV22.TRACES, EvidenceSourceV22.LOGS, EvidenceSourceV22.RESOURCES})
    if len(clusters) > 1 and not sources:
        sources.update({EvidenceSourceV22.LOGS, EvidenceSourceV22.TRACES})
    if any(cluster.contradiction_edges for cluster in clusters) and not sources:
        sources.update({EvidenceSourceV22.RUNTIME, EvidenceSourceV22.RESOURCES})
    return tuple(sorted(sources, key=lambda item: item.value))


def assess_conflict_v231(
    *,
    graph: ResidualEvidenceGraphV23,
    topology_edges: tuple[tuple[str, str], ...] = (),
    legal_sources: tuple[EvidenceSourceV22, ...] = (),
    remaining_reads: int,
    normal_resource_services: tuple[str, ...] = (),
) -> ConflictAssessmentV231:
    if not 0 <= remaining_reads <= 3:
        raise ValueError("conflict assessment remaining reads are outside the bound")
    if legal_sources != tuple(sorted(set(legal_sources), key=lambda item: item.value)):
        raise ValueError("conflict assessment legal sources are not canonical")
    clusters = build_interpretation_clusters_v231(
        graph=graph,
        topology_edges=topology_edges,
        normal_resource_services=normal_resource_services,
    )
    leading = (
        None
        if not clusters
        else min(clusters, key=lambda item: (-item.cluster_strength, item.cluster_id)).cluster_id
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
    if len({root for item in clusters for root in item.candidate_root_services}) > 1:
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


def audit_historical_conflicts_v231(result_path: Path) -> HistoricalConflictAuditV231:
    raw = result_path.read_bytes()
    artifact = FixedEvaluationArtifactV23.model_validate_json(raw)
    novelty_categories = {
        EvaluationCategoryV23.NOVEL_HIDDEN,
        EvaluationCategoryV23.NOVEL_UNREGISTERED,
    }
    entries: list[HistoricalConflictAuditEntryV231] = []
    for pair in artifact.pairs:
        if (
            pair.evaluator_truth.category not in novelty_categories
            or pair.open_world.final_disposition != "CONFLICTING_EVIDENCE"
        ):
            continue
        graph = pair.open_world.residual_graph
        if graph is None:
            raise ValueError("historical conflict miss lacks its residual graph")
        topology_edges = tuple(
            sorted(
                {
                    (
                        (item.service, related)
                        if item.service < related
                        else (related, item.service)
                    )
                    for item in graph.generic_anomalies
                    for related in item.related_services
                    if related in set(graph.candidate_services)
                }
            )
        )
        entries.append(
            HistoricalConflictAuditEntryV231(
                case_id=pair.case_id,
                strict_disposition="CONFLICTING_EVIDENCE",
                conflict_assessment=assess_conflict_v231(
                    graph=graph,
                    topology_edges=topology_edges,
                    legal_sources=(),
                    remaining_reads=0,
                ),
            )
        )
    payload: dict[str, Any] = {
        "schema_version": "dta-v231.historical-conflict-audit.v1",
        "source_result_sha256": __import__("hashlib").sha256(raw).hexdigest(),
        "strict_conflict_miss_count": len(entries),
        "entries": tuple(entries),
    }
    draft = HistoricalConflictAuditV231.model_construct(
        **payload,
        audit_sha256="0" * 64,
    )
    return HistoricalConflictAuditV231.model_validate(
        {
            **payload,
            "audit_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"audit_sha256"})
            ),
        }
    )


__all__ = (
    "ConflictAssessmentV231",
    "ConflictTypeV231",
    "HistoricalConflictAuditV231",
    "InterpretationClusterV231",
    "InterpretationEdgeV231",
    "assess_conflict_v231",
    "audit_historical_conflicts_v231",
    "build_interpretation_clusters_v231",
)
