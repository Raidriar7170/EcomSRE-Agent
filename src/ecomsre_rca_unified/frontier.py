"""Frozen A0-A5 counterfactual rules, robustness folds, and selection gates."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from ecomsre_rca_unified.contracts import (
    ArchitectureOption,
    CanonicalEntityLayer,
    FaultOntologyClass,
    FrontierCase,
    FrontierOutcome,
    PropagationDisposition,
    RobustnessFold,
    RootProvenance,
)


@dataclass(frozen=True, slots=True)
class OptionDefinition:
    option: ArchitectureOption
    name: str
    selectable: bool
    strategy: str
    config: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class FrozenFrontier:
    schema_version: str
    evaluation_version: str
    classification: tuple[str, ...]
    options: Mapping[str, OptionDefinition]
    selection: Mapping[str, object]


def load_frontier(path: Path) -> FrozenFrontier:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {
        "classification",
        "evaluation_version",
        "frontier_schema_version",
        "options",
        "selection",
    }:
        raise ValueError("frontier schema differs from the frozen contract")
    raw_options = value["options"]
    if not isinstance(raw_options, dict) or tuple(raw_options) != (
        "A0",
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
    ):
        raise ValueError("frontier must contain exactly ordered A0-A5")
    options: dict[str, OptionDefinition] = {}
    for key, raw in raw_options.items():
        if not isinstance(raw, dict):
            raise ValueError("frontier option must be an object")
        name = raw.get("name")
        strategy = raw.get("strategy")
        selectable = raw.get("selectable")
        if not isinstance(name, str) or not isinstance(strategy, str) or type(selectable) is not bool:
            raise ValueError("frontier option identity is invalid")
        options[key] = OptionDefinition(
            option=ArchitectureOption(key),
            name=name,
            selectable=selectable,
            strategy=strategy,
            config=dict(raw),
        )
    classification = value["classification"]
    if not isinstance(classification, list) or any(not isinstance(item, str) for item in classification):
        raise ValueError("frontier classification is invalid")
    selection = value["selection"]
    if not isinstance(selection, dict):
        raise ValueError("frontier selection contract is invalid")
    return FrozenFrontier(
        schema_version=str(value["frontier_schema_version"]),
        evaluation_version=str(value["evaluation_version"]),
        classification=tuple(classification),
        options=options,
        selection=selection,
    )


def _historical_m3(case: FrontierCase) -> bool:
    return bool(
        case.metrics_top1 is not None
        and case.metrics_top1 != case.initial_entity
        and (case.metrics_initial_rank is None or case.metrics_initial_rank > 2)
        and case.metrics_margin is not None
        and case.metrics_margin >= 0.25
    )


_COMPATIBLE_LAYER_GROUPS = (
    frozenset(
        {
            CanonicalEntityLayer.SERVICE,
            CanonicalEntityLayer.WORKLOAD,
            CanonicalEntityLayer.POD,
            CanonicalEntityLayer.CONTAINER,
        }
    ),
    frozenset(
        {
            CanonicalEntityLayer.NODE,
            CanonicalEntityLayer.CLUSTER,
            CanonicalEntityLayer.INFRASTRUCTURE,
        }
    ),
    frozenset(
        {
            CanonicalEntityLayer.DATABASE,
            CanonicalEntityLayer.CACHE,
            CanonicalEntityLayer.MESSAGE_QUEUE,
            CanonicalEntityLayer.NETWORK_COMPONENT,
        }
    ),
    frozenset({CanonicalEntityLayer.OPERATION}),
)


def _compatible_layers(left: CanonicalEntityLayer, right: CanonicalEntityLayer) -> bool:
    if CanonicalEntityLayer.UNKNOWN in {left, right}:
        return False
    return any(left in group and right in group for group in _COMPATIBLE_LAYER_GROUPS)


def _a2(case: FrontierCase) -> bool:
    return bool(
        _historical_m3(case)
        and _compatible_layers(case.initial_layer, case.metrics_top1_layer)
        and not case.metrics_top1_is_downstream
    )


def _a3(case: FrontierCase) -> bool:
    return bool(
        _a2(case)
        and case.fault_regime is FaultOntologyClass.LOCAL_RESOURCE
        and case.metric_family in {"CPU", "MEMORY", "DISK", "LOCAL_NODE_RESOURCE"}
        and case.propagation_disposition is PropagationDisposition.ABSENT
    )


def _causal_selection(case: FrontierCase, frontier: FrozenFrontier) -> str | None:
    config = frontier.options["A4"].config
    minimum_support_value = config.get("causal_min_source_support")
    if type(minimum_support_value) is not int:
        raise ValueError("A4 causal support threshold must be an integer")
    minimum_support = minimum_support_value
    eligible = [
        item
        for item in case.causal_candidates
        if item.source_support >= minimum_support
        and item.first_anomaly_time is not None
        and item.relation_to_symptom in {"ROOT", "UPSTREAM"}
    ]
    if not eligible:
        return None
    def order_key(item: Any) -> tuple[float, int, int, str]:
        if item.first_anomaly_time is None:
            raise ValueError("eligible causal candidate lacks anomaly time")
        return (
            item.first_anomaly_time,
            -item.source_support,
            item.metrics_rank,
            item.entity,
        )

    ordered = sorted(
        eligible,
        key=order_key,
    )
    return ordered[0].entity


def causal_selection(case: FrontierCase, frontier: FrozenFrontier) -> str | None:
    """Expose the frozen deterministic A4 candidate choice for replay/oracle audit."""

    return _causal_selection(case, frontier)


def apply_option(
    case: FrontierCase,
    option: ArchitectureOption,
    frontier: FrozenFrontier,
) -> FrontierOutcome:
    if option.value not in frontier.options:
        raise ValueError("architecture option is outside the frozen frontier")
    if case.terminal_failure:
        return FrontierOutcome(
            private_case_key=case.private_case_key,
            option=option,
            initial_entity=case.initial_entity,
            final_entity=case.initial_entity,
            fault_type=case.initial_fault_type,
            root_provenance=RootProvenance.MODEL_INITIAL,
            decision_reason="TERMINAL_FAILURE_KEEP",
            override=False,
            initial_exact_correct=False,
            final_exact_correct=False,
            initial_service_correct=False,
            final_service_correct=False,
            initial_pair_correct=False,
            final_pair_correct=False,
        )
    final = case.initial_entity
    provenance = RootProvenance.MODEL_INITIAL
    reason = "KEEP_INITIAL"
    if option is ArchitectureOption.A1 and _historical_m3(case):
        assert case.metrics_top1 is not None
        final = case.metrics_top1
        provenance = RootProvenance.DETERMINISTIC_METRICS_M3
        reason = "HISTORICAL_M3_OVERRIDE"
    elif option is ArchitectureOption.A2 and _a2(case):
        assert case.metrics_top1 is not None
        final = case.metrics_top1
        provenance = RootProvenance.HIERARCHY_GUARDED_METRICS
        reason = "HIERARCHY_GUARDED_M3_OVERRIDE"
    elif option is ArchitectureOption.A3 and _a3(case):
        assert case.metrics_top1 is not None
        final = case.metrics_top1
        provenance = RootProvenance.HIERARCHY_GUARDED_METRICS
        reason = "LOCAL_RESOURCE_HIERARCHY_GUARDED_OVERRIDE"
    elif option is ArchitectureOption.A4:
        if _a3(case):
            assert case.metrics_top1 is not None
            final = case.metrics_top1
            provenance = RootProvenance.HIERARCHY_GUARDED_METRICS
            reason = "LOCAL_RESOURCE_HIERARCHY_GUARDED_OVERRIDE"
        elif case.fault_regime in {
            FaultOntologyClass.PROPAGATION,
            FaultOntologyClass.NETWORK,
            FaultOntologyClass.DEPENDENCY,
            FaultOntologyClass.APPLICATION,
        }:
            selected = _causal_selection(case, frontier)
            if selected is not None and selected != case.initial_entity:
                final = selected
                provenance = RootProvenance.DETERMINISTIC_CAUSAL_RANKING
                reason = "DETERMINISTIC_CAUSAL_EARLIEST_SUPPORTED"
    elif option is ArchitectureOption.A5:
        if _a3(case):
            assert case.metrics_top1 is not None
            final = case.metrics_top1
            provenance = RootProvenance.HIERARCHY_GUARDED_METRICS
            reason = "LOCAL_RESOURCE_HIERARCHY_GUARDED_OVERRIDE"
        else:
            reason = "SELECTIVE_CAUSAL_AGENT_ORACLE_ONLY_KEEP"

    initial_exact = case.initial_entity in case.ground_truth_equivalent_entities
    final_exact = final in case.ground_truth_equivalent_entities
    initial_service = bool(
        case.ground_truth_service is not None
        and case.initial_service == case.ground_truth_service
    )
    if final == case.initial_entity:
        final_service_value = case.initial_service
    elif final == case.metrics_top1:
        final_service_value = case.metrics_top1_service
    else:
        causal_match = next(
            (item for item in case.causal_candidates if item.entity == final), None
        )
        final_service_value = (
            None if causal_match is None else causal_match.service_ancestor
        )
    final_service = bool(
        case.ground_truth_service is not None
        and final_service_value == case.ground_truth_service
    )
    return FrontierOutcome(
        private_case_key=case.private_case_key,
        option=option,
        initial_entity=case.initial_entity,
        final_entity=final,
        fault_type=case.initial_fault_type,
        root_provenance=provenance,
        decision_reason=reason,
        override=final != case.initial_entity,
        initial_exact_correct=initial_exact,
        final_exact_correct=final_exact,
        initial_service_correct=initial_service,
        final_service_correct=final_service,
        initial_pair_correct=case.initial_pair_correct,
        final_pair_correct=final_exact and case.initial_fault_correct,
    )


def aggregate_outcomes(outcomes: Sequence[FrontierOutcome]) -> dict[str, int | float]:
    denominator = len(outcomes)
    initial_correct = sum(item.initial_exact_correct for item in outcomes)
    rescue = sum(item.root_rescue for item in outcomes)
    damage = sum(item.root_damage for item in outcomes)
    pair_rescue = sum(not item.initial_pair_correct and item.final_pair_correct for item in outcomes)
    pair_damage = sum(item.initial_pair_correct and not item.final_pair_correct for item in outcomes)
    return {
        "denominator": denominator,
        "initial_exact_correct": initial_correct,
        "final_exact_correct": sum(item.final_exact_correct for item in outcomes),
        "initial_service_correct": sum(item.initial_service_correct for item in outcomes),
        "final_service_correct": sum(item.final_service_correct for item in outcomes),
        "root_rescue": rescue,
        "root_damage": damage,
        "root_net_rescue": rescue - damage,
        "root_damage_rate": 0.0 if initial_correct == 0 else damage / initial_correct,
        "pair_rescue": pair_rescue,
        "pair_damage": pair_damage,
        "pair_net_rescue": pair_rescue - pair_damage,
        "override_count": sum(item.override for item in outcomes),
        "correct_override": sum(item.override and item.final_exact_correct for item in outcomes),
        "wrong_override": sum(item.override and not item.final_exact_correct for item in outcomes),
    }


def grouped_robustness(
    cases: Sequence[FrontierCase],
    outcomes: Mapping[ArchitectureOption, Sequence[FrontierOutcome]],
) -> tuple[RobustnessFold, ...]:
    if any(len(values) != len(cases) for values in outcomes.values()):
        raise ValueError("robustness outcomes do not align with cases")
    axes: tuple[tuple[str, Any], ...] = (
        ("LEAVE_ONE_FAULT_FAMILY_OUT", lambda item: item.fault_family),
        ("LEAVE_ONE_ENTITY_LAYER_OUT", lambda item: item.initial_layer.value),
        ("LEAVE_ONE_SYSTEM_OUT", lambda item: item.system),
    )
    folds: list[RobustnessFold] = []
    for option, values in sorted(outcomes.items(), key=lambda item: item[0].value):
        by_key = {item.private_case_key: item for item in values}
        if len(by_key) != len(values):
            raise ValueError("robustness outcomes contain duplicate case keys")
        for axis, group_key in axes:
            groups = sorted({str(group_key(case)) for case in cases})
            for group in groups:
                included = [
                    by_key[case.private_case_key]
                    for case in cases
                    if str(group_key(case)) != group
                ]
                folds.append(
                    RobustnessFold(
                        option=option,
                        axis=axis,  # type: ignore[arg-type]
                        held_out_group=group,
                        denominator=len(included),
                        rescue=sum(item.root_rescue for item in included),
                        damage=sum(item.root_damage for item in included),
                    )
                )
    return tuple(folds)


def _fold_fraction_nonnegative(
    option: ArchitectureOption, axis: str, folds: Sequence[RobustnessFold]
) -> float:
    selected = [item for item in folds if item.option is option and item.axis == axis]
    if not selected:
        return 0.0
    return sum(item.net_rescue >= 0 for item in selected) / len(selected)


def _deterministic_passes(
    option: ArchitectureOption,
    option_aggregate: Mapping[str, int | float],
    fixture_aggregates: Mapping[str, Mapping[str, Mapping[str, int | float]]],
    robustness: Sequence[RobustnessFold],
) -> bool:
    net = int(option_aggregate.get("rca100_net_rescue", option_aggregate.get("root_net_rescue", -1)))
    damage = int(option_aggregate.get("rca100_damage", option_aggregate.get("root_damage", 999)))
    initial = int(option_aggregate.get("rca100_initial", option_aggregate.get("initial_exact_correct", 0)))
    final = int(option_aggregate.get("rca100_final", option_aggregate.get("final_exact_correct", -1)))
    damage_rate = 0.0 if initial == 0 else damage / initial
    if not (net > 0 and damage_rate <= 0.10 and final >= initial):
        return False
    per_fixture = fixture_aggregates.get(option.value, {})
    if not per_fixture:
        return False
    if any(
        int(value.get("root_net_rescue", -999)) < 0
        or int(value.get("root_damage", 999)) > 2
        for value in per_fixture.values()
    ):
        return False
    if sum(int(value.get("root_net_rescue", 0)) for value in per_fixture.values()) <= 0:
        return False
    return bool(
        _fold_fraction_nonnegative(option, "LEAVE_ONE_FAULT_FAMILY_OUT", robustness) >= 0.8
        and _fold_fraction_nonnegative(option, "LEAVE_ONE_ENTITY_LAYER_OUT", robustness) >= 0.8
    )


def select_architecture(
    *,
    option_aggregates: Mapping[str, Mapping[str, int | float]],
    fixture_aggregates: Mapping[str, Mapping[str, Mapping[str, int | float]]],
    robustness: Sequence[RobustnessFold],
    causal_agent: Mapping[str, int | float | bool] | None,
    frontier: FrozenFrontier,
) -> ArchitectureOption:
    deterministic = [
        option
        for option in (ArchitectureOption.A2, ArchitectureOption.A3, ArchitectureOption.A4)
        if _deterministic_passes(
            option,
            option_aggregates.get(option.value, {}),
            fixture_aggregates,
            robustness,
        )
    ]
    if deterministic:
        complexity = {ArchitectureOption.A2: 2, ArchitectureOption.A3: 3, ArchitectureOption.A4: 4}

        def priority(option: ArchitectureOption) -> tuple[float, float, int, int, int]:
            aggregate = option_aggregates[option.value]
            folds = [item.net_rescue for item in robustness if item.option is option]
            return (
                -float(aggregate.get("rca100_net_rescue", aggregate.get("root_net_rescue", 0))),
                -float(min(folds) if folds else -999),
                int(aggregate.get("root_damage", aggregate.get("rca100_damage", 999))),
                -int(aggregate.get("final_service_correct", 0)),
                complexity[option],
            )

        return sorted(deterministic, key=priority)[0]
    if causal_agent is not None:
        if (
            float(causal_agent.get("eligible_initial_wrong_coverage", 0.0)) >= 0.2
            and int(causal_agent.get("oracle_rca100_net_rescue", -999)) >= 5
            and int(causal_agent.get("oracle_damage", 999)) <= 2
            and bool(causal_agent.get("obss_expected_non_degradation", False))
            and bool(causal_agent.get("message_contract_nonredundant", False))
            and bool(
                causal_agent.get("source_evidence_distinguishes_root_symptom", False)
            )
            and float(causal_agent.get("mean_model_calls", 999.0)) <= 1.5
        ):
            return ArchitectureOption.A5
    if frontier.selection.get("fallback") != "A0":
        raise ValueError("frontier fallback differs from A0")
    return ArchitectureOption.A0


def distribution(values: Sequence[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


__all__ = [
    "FrozenFrontier",
    "OptionDefinition",
    "aggregate_outcomes",
    "apply_option",
    "causal_selection",
    "distribution",
    "grouped_robustness",
    "load_frontier",
    "select_architecture",
]
