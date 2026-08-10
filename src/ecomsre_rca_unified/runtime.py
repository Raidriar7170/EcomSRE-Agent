"""Selected Provider-free Strong Single hierarchical RCA runtime."""

from __future__ import annotations

from dataclasses import dataclass

from ecomsre_rca_unified.contracts import (
    CanonicalEntityLayer,
    EntityHierarchyPath,
    EvidenceVisibilitySummary,
    FaultOntologyClass,
    RootProvenance,
)


EVALUATION_VERSION = "unified-hierarchical-rca-v1"
SELECTED_OPTION = "A0"
DECISION_NAME = "STRONG_SINGLE_HIERARCHICAL"


@dataclass(frozen=True, slots=True)
class StrongSingleHierarchicalInput:
    """Label-free runtime boundary projected from the Strong Single terminal."""

    initial_root: str
    initial_layer: CanonicalEntityLayer
    initial_hierarchy_path: EntityHierarchyPath
    fault_type_raw: str
    fault_ontology_class: FaultOntologyClass
    evidence_visibility: EvidenceVisibilitySummary
    supporting_evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.initial_root or not self.fault_type_raw:
            raise ValueError("Strong Single runtime input contains an empty field")
        if self.initial_hierarchy_path.entity != self.initial_root:
            raise ValueError("Strong Single hierarchy path belongs to another root")


@dataclass(frozen=True, slots=True)
class HierarchicalRCAResult:
    evaluation_version: str
    initial_root: str
    final_root: str
    initial_layer: CanonicalEntityLayer
    final_layer: CanonicalEntityLayer
    root_provenance: RootProvenance
    fault_type_raw: str
    fault_ontology_class: FaultOntologyClass
    decision_reason: str
    supporting_evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.evaluation_version != EVALUATION_VERSION:
            raise ValueError("hierarchical RCA evaluation version differs")
        if not self.initial_root or not self.final_root or not self.fault_type_raw:
            raise ValueError("hierarchical RCA output contains an empty required field")


def execute_unified_hierarchical_rca(
    runtime_input: StrongSingleHierarchicalInput,
) -> HierarchicalRCAResult:
    """Apply the frozen A0 decision without a Metrics or Agent override."""

    return HierarchicalRCAResult(
        evaluation_version=EVALUATION_VERSION,
        initial_root=runtime_input.initial_root,
        final_root=runtime_input.initial_root,
        initial_layer=runtime_input.initial_layer,
        final_layer=runtime_input.initial_layer,
        root_provenance=RootProvenance.MODEL_INITIAL,
        fault_type_raw=runtime_input.fault_type_raw,
        fault_ontology_class=runtime_input.fault_ontology_class,
        decision_reason="STRONG_SINGLE_HIERARCHICAL_KEEP_INITIAL",
        supporting_evidence_refs=runtime_input.supporting_evidence_refs,
    )


__all__ = [
    "DECISION_NAME",
    "EVALUATION_VERSION",
    "HierarchicalRCAResult",
    "SELECTED_OPTION",
    "StrongSingleHierarchicalInput",
    "execute_unified_hierarchical_rca",
]
