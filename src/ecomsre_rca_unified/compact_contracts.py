"""Runtime-safe contracts for compact evidence retrieval and strict root selection."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictFloat, field_validator, model_validator

from ecomsre_rcaeval_v2.contracts import V2Model
from ecomsre_rca_unified.contracts import CanonicalEntityLayer


EvidenceSource = Literal["METRICS", "LOGS", "TRACES", "EVENTS", "ALERTS"]
SourceStatus = Literal["AVAILABLE", "SOURCE_UNAVAILABLE"]
RetrievalReason = Literal[
    "DIRECT_EVIDENCE",
    "EVIDENCE_ANCESTOR",
    "UPSTREAM_DEPENDENCY",
    "EARLIEST_ANOMALY",
    "METRICS_TOPK",
    "ALERT_RELATED",
]
AllocationBucket = Literal["R1", "R2_R3", "R4", "R5", "R6"]
RelationToAlert = Literal[
    "SAME",
    "ANCESTOR",
    "UPSTREAM",
    "DOWNSTREAM",
    "SAME_COMPONENT",
    "UNRELATED",
    "UNKNOWN",
]
EdgeType = Literal[
    "PARENT",
    "DIRECTED_TOPOLOGY",
    "TRACE_PARENT_CHILD",
    "EXPLICIT_DEPENDENCY",
    "UNDIRECTED",
]


ROOT_ELIGIBLE_LAYERS = frozenset(
    {
        CanonicalEntityLayer.SERVICE,
        CanonicalEntityLayer.WORKLOAD,
        CanonicalEntityLayer.NODE,
        CanonicalEntityLayer.DATABASE,
        CanonicalEntityLayer.CACHE,
        CanonicalEntityLayer.MESSAGE_QUEUE,
        CanonicalEntityLayer.NETWORK_COMPONENT,
        CanonicalEntityLayer.CLUSTER,
        CanonicalEntityLayer.INFRASTRUCTURE,
    }
)


class CompactEntity(V2Model):
    entity_ref: str = Field(min_length=5, max_length=768)
    display_name: str = Field(min_length=1, max_length=512)
    layer: CanonicalEntityLayer
    service_ancestor_or_none: str | None = Field(default=None, max_length=768)
    parent_ref_or_none: str | None = Field(default=None, max_length=768)


class CompactEvidence(V2Model):
    evidence_ref: str = Field(pattern=r"^(metric|log|trace):[0-9]{4}$")
    source: Literal["METRICS", "LOGS", "TRACES"]
    entity_ref: str = Field(min_length=5, max_length=768)
    name: str = Field(min_length=1, max_length=512)
    started_at: float
    ended_at: float
    score: float
    summary: str = Field(min_length=1, max_length=2_000)


class CompactBaseContext(V2Model):
    schema_version: Literal["compact-retrieval.base-context.v1"] = (
        "compact-retrieval.base-context.v1"
    )
    alert_title: str = Field(min_length=1, max_length=1_000)
    prompt_text: str = Field(min_length=1, max_length=4_000)
    alert_entity_ref: str | None = Field(default=None, max_length=768)
    entities: tuple[CompactEntity, ...] = Field(min_length=1, max_length=256)
    evidence: tuple[CompactEvidence, ...] = Field(max_length=18)
    source_status: dict[Literal["METRICS", "LOGS", "TRACES"], SourceStatus]

    @model_validator(mode="after")
    def require_referential_integrity(self) -> CompactBaseContext:
        refs = tuple(item.entity_ref for item in self.entities)
        if len(refs) != len(set(refs)):
            raise ValueError("base context contains duplicate entity refs")
        evidence_refs = tuple(item.evidence_ref for item in self.evidence)
        if len(evidence_refs) != len(set(evidence_refs)):
            raise ValueError("base context contains duplicate evidence refs")
        if not {item.entity_ref for item in self.evidence}.issubset(refs):
            raise ValueError("base evidence references an invisible entity")
        if self.alert_entity_ref is not None and self.alert_entity_ref not in refs:
            raise ValueError("base alert entity is invisible")
        if set(self.source_status) != {"METRICS", "LOGS", "TRACES"}:
            raise ValueError("base source statuses are incomplete")
        return self


class CompactEdge(V2Model):
    source_entity_ref: str = Field(min_length=5, max_length=768)
    target_entity_ref: str = Field(min_length=5, max_length=768)
    edge_type: EdgeType


class CompactRetrievalSource(V2Model):
    """Label-free source projection consumed by the deterministic retriever."""

    schema_version: Literal["compact-retrieval.source.v1"] = (
        "compact-retrieval.source.v1"
    )
    entities: tuple[CompactEntity, ...] = Field(min_length=1)
    edges: tuple[CompactEdge, ...]
    source_visibility: dict[str, frozenset[EvidenceSource]]
    source_occurrences: dict[str, dict[EvidenceSource, int]]
    first_anomaly_time: dict[str, float]
    metrics_ranking: tuple[str, ...] = Field(max_length=6)
    metrics_scores: dict[str, float]
    alert_entities: tuple[str, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def require_graph_integrity(self) -> CompactRetrievalSource:
        refs = {item.entity_ref for item in self.entities}
        if len(refs) != len(self.entities):
            raise ValueError("retrieval source contains duplicate entities")
        linked = {
            ref
            for edge in self.edges
            for ref in (edge.source_entity_ref, edge.target_entity_ref)
        }
        if not linked.issubset(refs):
            raise ValueError("retrieval edge references an unknown entity")
        projected = (
            set(self.source_visibility)
            | set(self.source_occurrences)
            | set(self.first_anomaly_time)
            | set(self.metrics_ranking)
            | set(self.metrics_scores)
            | set(self.alert_entities)
        )
        if not projected.issubset(refs):
            raise ValueError("retrieval source metadata references an unknown entity")
        if len(self.metrics_ranking) != len(set(self.metrics_ranking)):
            raise ValueError("Metrics ranking contains duplicate entities")
        if set(self.metrics_scores) != set(self.metrics_ranking):
            raise ValueError("Metrics scores differ from the ranked entities")
        if len(self.alert_entities) != len(set(self.alert_entities)):
            raise ValueError("retrieval source contains duplicate alert entities")
        if any(
            type(count) is not int or count < 0
            for counts in self.source_occurrences.values()
            for count in counts.values()
        ):
            raise ValueError("source occurrence counts must be nonnegative integers")
        return self


class CompactCandidateCard(V2Model):
    candidate_id: str = Field(pattern=r"^C(?:0[1-9]|1[0-2])$")
    display_name: str = Field(min_length=1, max_length=512)
    entity_ref: str = Field(min_length=5, max_length=768)
    entity_layer: CanonicalEntityLayer
    service_ancestor_or_none: str | None = Field(default=None, max_length=768)
    retrieval_reasons: tuple[RetrievalReason, ...] = Field(min_length=1, max_length=6)
    allocation_bucket: AllocationBucket
    visible_sources: tuple[EvidenceSource, ...] = Field(max_length=5)
    metrics_rank_or_none: int | None = Field(default=None, ge=1, le=6)
    metrics_margin_or_none: float | None = None
    first_anomaly_offset_ms_or_none: int | None = None
    relation_to_alert: RelationToAlert
    topology_distance_or_none: int | None = Field(default=None, ge=0)
    evidence_refs: tuple[str, ...] = Field(max_length=3)

    @model_validator(mode="after")
    def require_unique_bounded_values(self) -> CompactCandidateCard:
        if len(self.retrieval_reasons) != len(set(self.retrieval_reasons)):
            raise ValueError("candidate reasons are duplicated")
        if len(self.visible_sources) != len(set(self.visible_sources)):
            raise ValueError("candidate visible sources are duplicated")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("candidate evidence refs are duplicated")
        return self

    def model_visible_dump(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"entity_ref", "allocation_bucket"})


class CompactCandidateContext(V2Model):
    schema_version: Literal["compact-evidence-retrieval.context.v1"] = (
        "compact-evidence-retrieval.context.v1"
    )
    candidates: tuple[CompactCandidateCard, ...] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def require_stable_ids_and_diversity(self) -> CompactCandidateContext:
        expected = tuple(
            f"C{index:02d}" for index in range(1, len(self.candidates) + 1)
        )
        observed = tuple(item.candidate_id for item in self.candidates)
        if observed != expected:
            raise ValueError("candidate IDs are not contiguous and stable")
        refs = tuple(item.entity_ref for item in self.candidates)
        if len(refs) != len(set(refs)):
            raise ValueError("compact context contains duplicate entities")
        layer_counts = {
            layer: sum(item.entity_layer is layer for item in self.candidates)
            for layer in CanonicalEntityLayer
        }
        if any(count > 6 for count in layer_counts.values()):
            raise ValueError("compact context exceeds the per-layer diversity cap")
        services = {
            value
            for item in self.candidates
            if (value := item.service_ancestor_or_none) is not None
        }
        if any(
            sum(item.service_ancestor_or_none == service for item in self.candidates)
            > 3
            for service in services
        ):
            raise ValueError("compact context exceeds the per-service diversity cap")
        return self

    def model_visible_dump(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "candidates": [item.model_visible_dump() for item in self.candidates],
        }


class CompactRootSelection(V2Model):
    root_candidate_id: str = Field(pattern=r"^C(?:0[1-9]|1[0-2])$")
    fault_type: str = Field(min_length=1, max_length=128)
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=4)
    summary: str = Field(min_length=1, max_length=400)

    @field_validator("fault_type", "summary")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("compact selection text must not be blank")
        return stripped

    @model_validator(mode="after")
    def require_unique_refs(self) -> CompactRootSelection:
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("compact selection evidence refs are duplicated")
        return self


class ResolvedCompactDiagnosis(V2Model):
    root_candidate_id: str = Field(pattern=r"^C(?:0[1-9]|1[0-2])$")
    root_cause_entity_ref: str = Field(min_length=5, max_length=768)
    selected_candidate_rank: int = Field(ge=1, le=12)
    selected_allocation_bucket: AllocationBucket
    fault_type: str = Field(min_length=1, max_length=128)
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=4)
    summary: str = Field(min_length=1, max_length=400)


def resolve_compact_selection(
    selection: CompactRootSelection,
    *,
    context: CompactCandidateContext,
    visible_evidence_refs: frozenset[str],
) -> ResolvedCompactDiagnosis:
    candidate = next(
        (
            item
            for item in context.candidates
            if item.candidate_id == selection.root_candidate_id
        ),
        None,
    )
    if candidate is None:
        raise ValueError("selected candidate ID is absent from this case")
    if not set(selection.evidence_refs).issubset(visible_evidence_refs):
        raise ValueError("compact selection cited non-visible evidence")
    return ResolvedCompactDiagnosis(
        root_candidate_id=selection.root_candidate_id,
        root_cause_entity_ref=candidate.entity_ref,
        selected_candidate_rank=int(selection.root_candidate_id[1:]),
        selected_allocation_bucket=candidate.allocation_bucket,
        fault_type=selection.fault_type,
        confidence=selection.confidence,
        evidence_refs=selection.evidence_refs,
        summary=selection.summary,
    )


__all__ = [
    "AllocationBucket",
    "CompactBaseContext",
    "CompactCandidateCard",
    "CompactCandidateContext",
    "CompactEdge",
    "CompactEntity",
    "CompactEvidence",
    "CompactRetrievalSource",
    "CompactRootSelection",
    "EdgeType",
    "EvidenceSource",
    "ROOT_ELIGIBLE_LAYERS",
    "ResolvedCompactDiagnosis",
    "RetrievalReason",
    "resolve_compact_selection",
]
