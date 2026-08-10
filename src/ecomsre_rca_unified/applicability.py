"""Evaluator-only G0-G4 counterfactuals and frozen Gate selection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from ecomsre_rca_unified.a2_shadow import (
    A2ShadowInput,
    A2ShadowMode,
    ApplicabilityGateId,
    evaluate_a2_shadow,
)
from ecomsre_rca_unified.contracts import (
    ArchitectureOption,
    CanonicalEntityLayer,
    FrontierCase,
    FrontierOutcome,
)
from ecomsre_rca_unified.frontier import (
    FrozenFrontier,
    aggregate_outcomes,
    apply_option,
)


DESIGN_FIXTURES = ("RCA100", "candidate-3", "candidate-4", "candidate-5")
OBSS_FIXTURES = ("candidate-3", "candidate-4", "candidate-5")


_REFERENCE_ROOT_ELIGIBLE_LAYERS = frozenset(
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
class ApplicabilityCase:
    """Evaluator-only labels paired with one label-free runtime projection."""

    fixture: str
    frontier_case: FrontierCase
    runtime_input: A2ShadowInput

    def __post_init__(self) -> None:
        case = self.frontier_case
        runtime = self.runtime_input
        top1 = case.metrics_top1
        expected = (
            case.initial_entity,
            case.initial_layer,
            case.metrics_initial_rank,
            top1,
            case.metrics_top1_layer,
            case.metrics_top1_service,
            case.metrics_margin,
            case.metrics_top1_is_downstream,
            case.propagation_disposition,
            case.initial_fault_type,
            case.fault_regime,
        )
        actual = (
            runtime.initial_entity,
            runtime.initial_layer,
            runtime.initial_metrics_rank_or_none,
            runtime.metrics_top1_entity,
            runtime.metrics_top1_layer,
            runtime.metrics_top1_service_ancestor,
            runtime.metrics_margin,
            runtime.metrics_top1_is_downstream,
            runtime.propagation_disposition,
            runtime.fault_type_raw,
            runtime.fault_ontology_class,
        )
        if not self.fixture or expected != actual:
            raise ValueError("applicability labels and runtime projection differ")


@dataclass(frozen=True, slots=True)
class ApplicabilityFold:
    gate: ApplicabilityGateId
    axis: Literal[
        "LEAVE_ONE_FAULT_FAMILY_OUT",
        "LEAVE_ONE_ENTITY_LAYER_OUT",
        "LEAVE_ONE_SYSTEM_OUT",
    ]
    held_out_group: str
    denominator: int
    rescue: int
    damage: int

    @property
    def net_rescue(self) -> int:
        return self.rescue - self.damage


@dataclass(frozen=True, slots=True)
class GateEvaluation:
    gate: ApplicabilityGateId
    rca100: Mapping[str, int | float]
    fixtures: Mapping[str, Mapping[str, int | float]]
    obss_aggregate: Mapping[str, int | float]
    obss_net_retained_fraction: float
    folds: tuple[ApplicabilityFold, ...]
    fault_family_fold_pass_fraction: float
    entity_layer_fold_pass_fraction: float
    accepted: bool
    rejection_reasons: tuple[str, ...]
    model_calls: int = 1


@dataclass(frozen=True, slots=True)
class GateFrontierResult:
    selected_gate: ApplicabilityGateId | None
    evaluations: Mapping[ApplicabilityGateId, GateEvaluation]


def _reference_non_metrics_support(runtime_input: A2ShadowInput) -> bool:
    top1 = runtime_input.metrics_top1_entity
    return bool(
        top1 is not None
        and any(
            top1 in entities
            for entities in (
                runtime_input.evidence_visibility.logs_entities,
                runtime_input.evidence_visibility.traces_entities,
                runtime_input.evidence_visibility.events_entities,
                runtime_input.evidence_visibility.alerts_entities,
            )
        )
    )


def _reference_gate_passes(
    case: ApplicabilityCase,
    gate: ApplicabilityGateId,
    frontier: FrozenFrontier,
) -> bool:
    raw = case.frontier_case
    base_rule_passed = apply_option(raw, ArchitectureOption.A2, frontier).override
    if not base_rule_passed:
        return False
    same_exact_layer = (
        raw.initial_layer is raw.metrics_top1_layer
        and raw.initial_layer
        not in {CanonicalEntityLayer.UNKNOWN, CanonicalEntityLayer.OPERATION}
    )
    root_eligible = (
        raw.initial_layer in _REFERENCE_ROOT_ELIGIBLE_LAYERS
        and raw.metrics_top1_layer in _REFERENCE_ROOT_ELIGIBLE_LAYERS
    )
    cross_source = _reference_non_metrics_support(case.runtime_input)
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
    raise ValueError("applicability Gate must be exactly G0-G4")


def evaluate_reference_case(
    case: ApplicabilityCase,
    gate: ApplicabilityGateId,
    frontier: FrozenFrontier,
) -> FrontierOutcome:
    """Evaluate the Phase 9 reference without calling production Shadow code."""

    selected = (
        ArchitectureOption.A2
        if _reference_gate_passes(case, gate, frontier)
        else ArchitectureOption.A0
    )
    return apply_option(case.frontier_case, selected, frontier)


def evaluate_production_case(
    case: ApplicabilityCase,
    gate: ApplicabilityGateId,
    frontier: FrozenFrontier,
) -> FrontierOutcome:
    """Score one production Shadow decision in the evaluator-only boundary."""

    decision = evaluate_a2_shadow(case.runtime_input, gate, A2ShadowMode.SHADOW)
    selected = (
        ArchitectureOption.A2
        if decision.applicability_gate_passed
        else ArchitectureOption.A0
    )
    outcome = apply_option(case.frontier_case, selected, frontier)
    if outcome.final_entity != decision.shadow_final_entity:
        raise ValueError("production Shadow outcome differs from frozen A2 scoring")
    if outcome.fault_type != decision.fault_type_raw:
        raise ValueError("production Shadow changed the Initial fault type")
    return outcome


def _folds(
    cases: Sequence[ApplicabilityCase],
    outcomes: Sequence[FrontierOutcome],
    gate: ApplicabilityGateId,
) -> tuple[ApplicabilityFold, ...]:
    if len(cases) != len(outcomes):
        raise ValueError("applicability fold inputs do not align")
    by_key = {item.private_case_key: item for item in outcomes}
    if len(by_key) != len(cases):
        raise ValueError("applicability cases contain duplicate private keys")
    axes = (
        (
            "LEAVE_ONE_FAULT_FAMILY_OUT",
            lambda item: item.frontier_case.fault_family,
        ),
        (
            "LEAVE_ONE_ENTITY_LAYER_OUT",
            lambda item: item.frontier_case.initial_layer.value,
        ),
        (
            "LEAVE_ONE_SYSTEM_OUT",
            lambda item: item.frontier_case.system,
        ),
    )
    result: list[ApplicabilityFold] = []
    for axis, group_key in axes:
        groups = sorted({str(group_key(item)) for item in cases})
        for held_out in groups:
            included = [
                by_key[item.frontier_case.private_case_key]
                for item in cases
                if str(group_key(item)) != held_out
            ]
            result.append(
                ApplicabilityFold(
                    gate=gate,
                    axis=axis,  # type: ignore[arg-type]
                    held_out_group=held_out,
                    denominator=len(included),
                    rescue=sum(item.root_rescue for item in included),
                    damage=sum(item.root_damage for item in included),
                )
            )
    return tuple(result)


def _fold_pass_fraction(
    folds: Sequence[ApplicabilityFold],
    axis: str,
) -> float:
    selected = [item for item in folds if item.axis == axis]
    if not selected:
        raise ValueError("required applicability robustness axis is empty")
    return sum(item.net_rescue >= 0 for item in selected) / len(selected)


def _rejection_reasons(
    rca100: Mapping[str, int | float],
    fixtures: Mapping[str, Mapping[str, int | float]],
    obss: Mapping[str, int | float],
    retained: float,
    fault_fold_pass: float,
    layer_fold_pass: float,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if int(rca100["root_net_rescue"]) < 0:
        reasons.append("RCA100_ROOT_NET_RESCUE_BELOW_ZERO")
    if int(rca100["final_exact_correct"]) < int(rca100["initial_exact_correct"]):
        reasons.append("RCA100_FINAL_EXACT_BELOW_INITIAL")
    if float(rca100["root_damage_rate"]) > 0.10:
        reasons.append("RCA100_ROOT_DAMAGE_RATE_ABOVE_0_10")
    if int(rca100["root_damage"]) > 0 and not (
        int(rca100["root_rescue"]) > int(rca100["root_damage"])
    ):
        reasons.append("RCA100_RESCUE_NOT_GREATER_THAN_DAMAGE")
    for fixture in OBSS_FIXTURES:
        aggregate = fixtures[fixture]
        if int(aggregate["root_net_rescue"]) < 0:
            reasons.append(f"{fixture.upper()}_ROOT_NET_RESCUE_BELOW_ZERO")
        if int(aggregate["root_damage"]) > 2:
            reasons.append(f"{fixture.upper()}_ROOT_DAMAGE_ABOVE_2")
    if int(obss["root_net_rescue"]) <= 0:
        reasons.append("OBSS_AGGREGATE_ROOT_NET_RESCUE_NOT_POSITIVE")
    if retained < 0.50:
        reasons.append("OBSS_G0_NET_RETAINED_BELOW_0_50")
    if fault_fold_pass < 0.80:
        reasons.append("FAULT_FAMILY_FOLD_PASS_BELOW_0_80")
    if layer_fold_pass < 0.80:
        reasons.append("ENTITY_LAYER_FOLD_PASS_BELOW_0_80")
    return tuple(reasons)


def evaluate_applicability_frontier(
    cases: Sequence[ApplicabilityCase],
    frontier: FrozenFrontier,
) -> GateFrontierResult:
    if not cases:
        raise ValueError("applicability frontier requires design cases")
    fixture_names = {item.fixture for item in cases}
    if fixture_names != set(DESIGN_FIXTURES):
        raise ValueError(
            "applicability design fixtures must be RCA100 and Candidate-3/4/5"
        )
    keys = [item.frontier_case.private_case_key for item in cases]
    if len(set(keys)) != len(keys):
        raise ValueError("applicability design cases contain duplicate keys")
    reference: dict[ApplicabilityGateId, tuple[FrontierOutcome, ...]] = {
        gate: tuple(evaluate_reference_case(item, gate, frontier) for item in cases)
        for gate in ApplicabilityGateId
    }
    g0_obss = aggregate_outcomes(
        tuple(
            outcome
            for case, outcome in zip(
                cases,
                reference[ApplicabilityGateId.G0_A2_REFERENCE],
                strict=True,
            )
            if case.fixture in OBSS_FIXTURES
        )
    )
    g0_net = int(g0_obss["root_net_rescue"])
    evaluations: dict[ApplicabilityGateId, GateEvaluation] = {}
    for gate, outcomes in reference.items():
        rca100 = aggregate_outcomes(
            tuple(
                outcome
                for case, outcome in zip(cases, outcomes, strict=True)
                if case.fixture == "RCA100"
            )
        )
        fixture_aggregates = {
            fixture: aggregate_outcomes(
                tuple(
                    outcome
                    for case, outcome in zip(cases, outcomes, strict=True)
                    if case.fixture == fixture
                )
            )
            for fixture in OBSS_FIXTURES
        }
        obss = aggregate_outcomes(
            tuple(
                outcome
                for case, outcome in zip(cases, outcomes, strict=True)
                if case.fixture in OBSS_FIXTURES
            )
        )
        retained = 0.0 if g0_net <= 0 else int(obss["root_net_rescue"]) / g0_net
        gate_folds = _folds(cases, outcomes, gate)
        fault_pass = _fold_pass_fraction(gate_folds, "LEAVE_ONE_FAULT_FAMILY_OUT")
        layer_pass = _fold_pass_fraction(gate_folds, "LEAVE_ONE_ENTITY_LAYER_OUT")
        rejections = _rejection_reasons(
            rca100,
            fixture_aggregates,
            obss,
            retained,
            fault_pass,
            layer_pass,
        )
        evaluations[gate] = GateEvaluation(
            gate=gate,
            rca100=rca100,
            fixtures=fixture_aggregates,
            obss_aggregate=obss,
            obss_net_retained_fraction=retained,
            folds=gate_folds,
            fault_family_fold_pass_fraction=fault_pass,
            entity_layer_fold_pass_fraction=layer_pass,
            accepted=not rejections,
            rejection_reasons=rejections,
        )
    complexity = {
        ApplicabilityGateId.G0_A2_REFERENCE: 0,
        ApplicabilityGateId.G1_EXACT_LAYER_A2: 1,
        ApplicabilityGateId.G2_ROOT_ELIGIBLE_LAYER_A2: 1,
        ApplicabilityGateId.G3_CROSS_SOURCE_SUPPORTED_A2: 1,
        ApplicabilityGateId.G4_EXACT_LAYER_CROSS_SOURCE_A2: 2,
    }

    def priority(gate: ApplicabilityGateId) -> tuple[int, int, int, int, int, int, str]:
        item = evaluations[gate]
        minimum_fold = min(fold.net_rescue for fold in item.folds)
        total_damage = int(item.rca100["root_damage"]) + int(
            item.obss_aggregate["root_damage"]
        )
        total_override = int(item.rca100["override_count"]) + int(
            item.obss_aggregate["override_count"]
        )
        return (
            -int(item.rca100["root_net_rescue"]),
            -int(item.obss_aggregate["root_net_rescue"]),
            total_damage,
            -minimum_fold,
            total_override,
            complexity[gate],
            gate.value,
        )

    accepted = [gate for gate, item in evaluations.items() if item.accepted]
    selected = None if not accepted else sorted(accepted, key=priority)[0]
    return GateFrontierResult(selected_gate=selected, evaluations=evaluations)


__all__ = [
    "ApplicabilityCase",
    "ApplicabilityFold",
    "DESIGN_FIXTURES",
    "GateEvaluation",
    "GateFrontierResult",
    "OBSS_FIXTURES",
    "evaluate_applicability_frontier",
    "evaluate_production_case",
    "evaluate_reference_case",
]
