"""Label-free A2 shadow recommendations over the frozen A0 runtime."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
import math
from typing import TypeVar, cast

from ecomsre_rca_unified.contracts import (
    CanonicalEntityLayer,
    EntityHierarchyPath,
    EvidenceVisibilitySummary,
    FaultOntologyClass,
    FrontierCase,
    PropagationDisposition,
)
from ecomsre_rca_unified.frontier import _compatible_layers, _historical_m3
from ecomsre_rca_unified.runtime import (
    HierarchicalRCAResult,
    StrongSingleHierarchicalInput,
    execute_unified_hierarchical_rca,
)


class ApplicabilityGateId(str, Enum):
    G0_A2_REFERENCE = "G0_A2_REFERENCE"
    G1_EXACT_LAYER_A2 = "G1_EXACT_LAYER_A2"
    G2_ROOT_ELIGIBLE_LAYER_A2 = "G2_ROOT_ELIGIBLE_LAYER_A2"
    G3_CROSS_SOURCE_SUPPORTED_A2 = "G3_CROSS_SOURCE_SUPPORTED_A2"
    G4_EXACT_LAYER_CROSS_SOURCE_A2 = "G4_EXACT_LAYER_CROSS_SOURCE_A2"


class A2ShadowMode(str, Enum):
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"


class A2Action(str, Enum):
    WOULD_KEEP = "WOULD_KEEP"
    WOULD_OVERRIDE = "WOULD_OVERRIDE"
    KEEP_INITIAL = "KEEP_INITIAL"
    OVERRIDE_METRICS_TOP1 = "OVERRIDE_METRICS_TOP1"


class A2RootProvenance(str, Enum):
    MODEL_INITIAL = "MODEL_INITIAL"
    HIERARCHY_GUARDED_METRICS_SHADOW = "HIERARCHY_GUARDED_METRICS_SHADOW"
    CONDITIONAL_HIERARCHY_GUARDED_METRICS = "CONDITIONAL_HIERARCHY_GUARDED_METRICS"


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


@dataclass(frozen=True, slots=True)
class A2ShadowInput:
    """Runtime-observable projection shared by A0 and deterministic A2."""

    initial_entity: str
    initial_layer: CanonicalEntityLayer
    initial_hierarchy_path: EntityHierarchyPath
    initial_metrics_rank_or_none: int | None
    metrics_top1_entity: str | None
    metrics_top1_layer: CanonicalEntityLayer
    metrics_top1_service_ancestor: str | None
    metrics_margin: float | None
    metrics_top1_is_downstream: bool
    propagation_disposition: PropagationDisposition
    evidence_visibility: EvidenceVisibilitySummary
    fault_type_raw: str
    fault_ontology_class: FaultOntologyClass
    supporting_evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.initial_entity or not self.fault_type_raw:
            raise ValueError("A2 shadow input contains an empty required field")
        if self.initial_hierarchy_path.entity != self.initial_entity:
            raise ValueError("A2 shadow hierarchy path belongs to another root")
        if self.initial_metrics_rank_or_none is not None and not (
            1 <= self.initial_metrics_rank_or_none <= 6
        ):
            raise ValueError("A2 shadow Initial rank must be in the frozen Top-6")
        if self.metrics_margin is not None and not math.isfinite(self.metrics_margin):
            raise ValueError("A2 shadow Metrics margin must be finite")
        if self.metrics_top1_entity is None:
            if self.metrics_top1_layer is not CanonicalEntityLayer.UNKNOWN:
                raise ValueError("missing Metrics Top1 must use UNKNOWN layer")
            if self.metrics_top1_service_ancestor is not None:
                raise ValueError("missing Metrics Top1 cannot have a service ancestor")
        elif not self.metrics_top1_entity:
            raise ValueError("A2 shadow Metrics Top1 cannot be empty")
        if any(not item for item in self.supporting_evidence_refs):
            raise ValueError("A2 shadow evidence reference cannot be empty")


@dataclass(frozen=True, slots=True)
class _A2ReferenceProjection:
    """Only the label-free attributes read by PR #24's frozen A2 predicate."""

    initial_entity: str
    metrics_top1: str | None
    metrics_initial_rank: int | None
    metrics_margin: float | None


@dataclass(frozen=True, slots=True)
class A2ShadowDecision:
    mode: A2ShadowMode
    base_rule_passed: bool
    applicability_gate_id: ApplicabilityGateId
    applicability_gate_passed: bool
    action: A2Action
    initial_entity: str
    initial_layer: CanonicalEntityLayer
    initial_metrics_rank_or_none: int | None
    metrics_top1_entity: str | None
    metrics_top1_layer: CanonicalEntityLayer
    metrics_top1_service_ancestor: str | None
    metrics_margin: float | None
    layers_compatible: bool
    same_exact_layer: bool
    root_eligible_layer: bool
    top1_is_downstream: bool
    propagation_disposition: PropagationDisposition
    non_metrics_support_sources: tuple[str, ...]
    shadow_final_entity: str
    authoritative_final_entity: str
    decision_reasons: tuple[str, ...]
    root_provenance: A2RootProvenance
    fault_type_raw: str


@dataclass(frozen=True, slots=True)
class A2ShadowCaseResult:
    a0_authoritative: HierarchicalRCAResult
    shadow_decisions: tuple[A2ShadowDecision, ...]
    model_calls: int = 1
    specialist_calls: int = 0
    fusion_calls: int = 0


def _non_metrics_support_sources(runtime_input: A2ShadowInput) -> tuple[str, ...]:
    top1 = runtime_input.metrics_top1_entity
    if top1 is None:
        return ()
    source_sets = (
        ("LOGS", runtime_input.evidence_visibility.logs_entities),
        ("TRACES", runtime_input.evidence_visibility.traces_entities),
        ("EVENTS", runtime_input.evidence_visibility.events_entities),
        ("ALERTS", runtime_input.evidence_visibility.alerts_entities),
    )
    return tuple(name for name, entities in source_sets if top1 in entities)


def _frozen_a2_reference(runtime_input: A2ShadowInput) -> tuple[bool, bool]:
    projection = _A2ReferenceProjection(
        initial_entity=runtime_input.initial_entity,
        metrics_top1=runtime_input.metrics_top1_entity,
        metrics_initial_rank=runtime_input.initial_metrics_rank_or_none,
        metrics_margin=runtime_input.metrics_margin,
    )
    historical_passed = _historical_m3(cast(FrontierCase, projection))
    layers_compatible = _compatible_layers(
        runtime_input.initial_layer,
        runtime_input.metrics_top1_layer,
    )
    return (
        bool(
            historical_passed
            and layers_compatible
            and not runtime_input.metrics_top1_is_downstream
        ),
        layers_compatible,
    )


def _gate_passes(
    runtime_input: A2ShadowInput,
    gate: ApplicabilityGateId,
    *,
    base_rule_passed: bool,
    non_metrics_support_sources: tuple[str, ...],
) -> bool:
    if not base_rule_passed:
        return False
    same_exact_layer = (
        runtime_input.initial_layer is runtime_input.metrics_top1_layer
        and runtime_input.initial_layer
        not in {CanonicalEntityLayer.UNKNOWN, CanonicalEntityLayer.OPERATION}
    )
    root_eligible = (
        runtime_input.initial_layer in ROOT_ELIGIBLE_LAYERS
        and runtime_input.metrics_top1_layer in ROOT_ELIGIBLE_LAYERS
    )
    cross_source = bool(non_metrics_support_sources)
    if gate is ApplicabilityGateId.G0_A2_REFERENCE:
        return True
    if gate is ApplicabilityGateId.G1_EXACT_LAYER_A2:
        return same_exact_layer
    if gate is ApplicabilityGateId.G2_ROOT_ELIGIBLE_LAYER_A2:
        return root_eligible
    if gate is ApplicabilityGateId.G3_CROSS_SOURCE_SUPPORTED_A2:
        return cross_source
    if gate is ApplicabilityGateId.G4_EXACT_LAYER_CROSS_SOURCE_A2:
        return same_exact_layer and cross_source
    raise ValueError("A2 applicability gate is outside frozen G0-G4")


def _decision_reasons(
    runtime_input: A2ShadowInput,
    gate: ApplicabilityGateId,
    *,
    base_rule_passed: bool,
    gate_passed: bool,
    layers_compatible: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if base_rule_passed:
        reasons.append("A2_REFERENCE_PASSED")
    else:
        if runtime_input.metrics_top1_entity is None:
            reasons.append("A2_TOP1_MISSING")
        elif runtime_input.metrics_top1_entity == runtime_input.initial_entity:
            reasons.append("A2_TOP1_EQUALS_INITIAL")
        if (
            runtime_input.initial_metrics_rank_or_none is not None
            and runtime_input.initial_metrics_rank_or_none <= 2
        ):
            reasons.append("A2_INITIAL_RANK_NOT_ABSENT_OR_GT2")
        if runtime_input.metrics_margin is None or runtime_input.metrics_margin < 0.25:
            reasons.append("A2_MARGIN_BELOW_0_25")
        if not layers_compatible:
            reasons.append("A2_LAYERS_INCOMPATIBLE")
        if runtime_input.metrics_top1_is_downstream:
            reasons.append("A2_TOP1_IS_DOWNSTREAM")
    reasons.append(f"{gate.value}_{'PASSED' if gate_passed else 'FAILED'}")
    return tuple(reasons)


def evaluate_a2_shadow(
    runtime_input: A2ShadowInput,
    gate: ApplicabilityGateId,
    mode: A2ShadowMode,
) -> A2ShadowDecision:
    """Apply exactly one frozen G0-G4 gate without reading outcome labels."""

    base_rule_passed, layers_compatible = _frozen_a2_reference(runtime_input)
    support_sources = _non_metrics_support_sources(runtime_input)
    gate_passed = _gate_passes(
        runtime_input,
        gate,
        base_rule_passed=base_rule_passed,
        non_metrics_support_sources=support_sources,
    )
    same_exact_layer = (
        runtime_input.initial_layer is runtime_input.metrics_top1_layer
        and runtime_input.initial_layer
        not in {CanonicalEntityLayer.UNKNOWN, CanonicalEntityLayer.OPERATION}
    )
    root_eligible = (
        runtime_input.initial_layer in ROOT_ELIGIBLE_LAYERS
        and runtime_input.metrics_top1_layer in ROOT_ELIGIBLE_LAYERS
    )
    shadow_final = (
        cast(str, runtime_input.metrics_top1_entity)
        if gate_passed
        else runtime_input.initial_entity
    )
    authoritative_final = (
        runtime_input.initial_entity if mode is A2ShadowMode.SHADOW else shadow_final
    )
    if mode is A2ShadowMode.SHADOW:
        action = A2Action.WOULD_OVERRIDE if gate_passed else A2Action.WOULD_KEEP
        provenance = (
            A2RootProvenance.HIERARCHY_GUARDED_METRICS_SHADOW
            if gate_passed
            else A2RootProvenance.MODEL_INITIAL
        )
    else:
        action = (
            A2Action.OVERRIDE_METRICS_TOP1 if gate_passed else A2Action.KEEP_INITIAL
        )
        provenance = (
            A2RootProvenance.CONDITIONAL_HIERARCHY_GUARDED_METRICS
            if gate_passed
            else A2RootProvenance.MODEL_INITIAL
        )
    return A2ShadowDecision(
        mode=mode,
        base_rule_passed=base_rule_passed,
        applicability_gate_id=gate,
        applicability_gate_passed=gate_passed,
        action=action,
        initial_entity=runtime_input.initial_entity,
        initial_layer=runtime_input.initial_layer,
        initial_metrics_rank_or_none=runtime_input.initial_metrics_rank_or_none,
        metrics_top1_entity=runtime_input.metrics_top1_entity,
        metrics_top1_layer=runtime_input.metrics_top1_layer,
        metrics_top1_service_ancestor=runtime_input.metrics_top1_service_ancestor,
        metrics_margin=runtime_input.metrics_margin,
        layers_compatible=layers_compatible,
        same_exact_layer=same_exact_layer,
        root_eligible_layer=root_eligible,
        top1_is_downstream=runtime_input.metrics_top1_is_downstream,
        propagation_disposition=runtime_input.propagation_disposition,
        non_metrics_support_sources=support_sources,
        shadow_final_entity=shadow_final,
        authoritative_final_entity=authoritative_final,
        decision_reasons=_decision_reasons(
            runtime_input,
            gate,
            base_rule_passed=base_rule_passed,
            gate_passed=gate_passed,
            layers_compatible=layers_compatible,
        ),
        root_provenance=provenance,
        fault_type_raw=runtime_input.fault_type_raw,
    )


TModelInput = TypeVar("TModelInput")


def execute_a2_shadow_case(
    model_input: TModelInput,
    diagnose: Callable[[TModelInput], A2ShadowInput],
    *,
    gates: Sequence[ApplicabilityGateId],
) -> A2ShadowCaseResult:
    """Use one Strong Single observation for A0 and all shadow decisions."""

    if not gates or len(set(gates)) != len(gates):
        raise ValueError("A2 shadow case requires unique non-empty gates")
    observation = diagnose(model_input)
    a0 = execute_unified_hierarchical_rca(
        StrongSingleHierarchicalInput(
            initial_root=observation.initial_entity,
            initial_layer=observation.initial_layer,
            initial_hierarchy_path=observation.initial_hierarchy_path,
            fault_type_raw=observation.fault_type_raw,
            fault_ontology_class=observation.fault_ontology_class,
            evidence_visibility=observation.evidence_visibility,
            supporting_evidence_refs=observation.supporting_evidence_refs,
        )
    )
    decisions = tuple(
        evaluate_a2_shadow(observation, gate, A2ShadowMode.SHADOW) for gate in gates
    )
    if any(item.authoritative_final_entity != a0.final_root for item in decisions):
        raise ValueError("A2 shadow changed the authoritative A0 root")
    return A2ShadowCaseResult(a0_authoritative=a0, shadow_decisions=decisions)


__all__ = [
    "A2Action",
    "A2RootProvenance",
    "A2ShadowCaseResult",
    "A2ShadowDecision",
    "A2ShadowInput",
    "A2ShadowMode",
    "ApplicabilityGateId",
    "ROOT_ELIGIBLE_LAYERS",
    "evaluate_a2_shadow",
    "execute_a2_shadow_case",
]
