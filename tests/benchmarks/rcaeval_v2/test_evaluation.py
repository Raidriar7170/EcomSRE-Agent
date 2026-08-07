from __future__ import annotations

import json

from ecomsre_rcaeval_v2.contracts import (
    OperationStage,
    OperationStatus,
    OperationType,
)
from ecomsre_rcaeval_v2.evaluation import (
    PrivateRunOutcome,
    PrivateSpecialistOutcome,
    aggregate_development_outcomes,
)
from ecomsre_rcaeval_v2.evidence import design_signals
from ecomsre_rcaeval_v2.schedule import SplitName, Variant
from ecomsre_rcaeval_v2.statistics import (
    PairedObservation,
    hierarchical_paired_bootstrap,
)


def _outcome(
    *,
    variant: Variant,
    instance: str,
    predicted_indicator: str,
) -> PrivateRunOutcome:
    specialists = ()
    route = ()
    if variant in {Variant.FIXED_V2, Variant.DYNAMIC_V2}:
        specialists = (
            PrivateSpecialistOutcome(
                source="metrics",
                candidate_service="checkoutservice",
                candidate_indicator="mem",
                confidence=0.8,
            ),
        )
    if variant is Variant.DYNAMIC_V2:
        route = ("logs",)
    return PrivateRunOutcome(
        schema_version="rcaeval-re2-v2-dev1.private-run-outcome.v1",
        system="RE2-OB",
        root_cause_service="checkoutservice",
        fault="mem",
        instance=instance,
        split=SplitName.DEV_VALIDATION,
        variant=variant,
        terminal_status=OperationStatus.COMPLETED,
        predicted_service="checkoutservice",
        predicted_indicator=predicted_indicator,
        tool_calls=3,
        model_calls=1,
        total_tokens=15,
        token_usage_known=True,
        latency_ms=10.0,
        failure_operation_type=None,
        failure_stage=None,
        specialists=specialists,
        commander_selected_sources=route,
        indicator_candidate_pairs=(("checkoutservice", "mem"),),
        indicator_disposition=(
            "RESOLVED" if variant is not Variant.SINGLE_V1_REFERENCE else None
        ),
    )


def test_hierarchical_paired_bootstrap_is_deterministic_and_paired() -> None:
    observations = tuple(
        PairedObservation(
            system=system,
            service="service",
            fault="mem",
            baseline=0.0,
            candidate=1.0,
        )
        for system in ("RE2-OB", "RE2-SS")
        for _ in range(2)
    )
    first = hierarchical_paired_bootstrap(observations, iterations=200, seed=7)
    second = hierarchical_paired_bootstrap(observations, iterations=200, seed=7)

    assert first == second
    assert first.point_estimate == 1.0
    assert first.lower_95 == 1.0
    assert first.upper_95 == 1.0


def test_public_aggregate_contains_rates_and_no_private_identifiers() -> None:
    outcomes = tuple(
        _outcome(
            variant=variant,
            instance=instance,
            predicted_indicator=(
                "cpu" if variant is Variant.SINGLE_V1_REFERENCE else "mem"
            ),
        )
        for instance in ("1", "2")
        for variant in (
            Variant.SINGLE_V1_REFERENCE,
            Variant.SINGLE_V2,
            Variant.FIXED_V2,
            Variant.DYNAMIC_V2,
        )
    )
    aggregate = aggregate_development_outcomes(
        outcomes, split=SplitName.DEV_VALIDATION
    )
    encoded = json.dumps(aggregate, sort_keys=True)

    comparison = aggregate["paired_development_comparisons"][
        "single_v2_minus_single_v1_pair"
    ]
    assert comparison["bootstrap"]["point_estimate"] == 1.0
    pair_rate = aggregate["architecture_summaries"]["single_v2_dev1"][
        "root_cause_pair_ac_at_1"
    ]
    assert pair_rate == {"numerator": 2, "denominator": 2, "value": 1.0}
    assert aggregate["classification"] == [
        "DEVELOPMENT_VISIBLE",
        "DESIGN_SET",
        "NOT_EXTERNAL_HOLDOUT",
        "NOT_PRIMARY_INFERENCE",
    ]
    assert aggregate["architecture_summaries"]["single_v2_dev1"]["cost"][
        "tool_calls_median"
    ] == 3.0
    assert aggregate["multi_agent_damage_rescue"]["fixed"] == {
        "paired_cases": 2,
        "single_correct_candidate_wrong": 0,
        "single_wrong_candidate_correct": 0,
    }
    signals = design_signals(aggregate)
    assert signals["indicator"]["single_v2_pair_improvement"] == 1.0
    assert signals["indicator"]["recommended_for_candidate_freeze_review"] is False
    assert "instance" not in encoded
    assert "case_id" not in encoded
    assert "run_id" not in encoded


def test_failure_taxonomy_crosses_operation_and_exact_stage() -> None:
    failure = _outcome(
        variant=Variant.SINGLE_V2,
        instance="1",
        predicted_indicator="mem",
    ).model_copy(
        update={
            "terminal_status": OperationStatus.INVALID_SCHEMA,
            "predicted_service": None,
            "predicted_indicator": None,
            "failure_operation_type": OperationType.FINAL_JUDGE,
            "failure_stage": OperationStage.OUTPUT_VALIDATION,
        }
    )

    aggregate = aggregate_development_outcomes(
        (failure,), split=SplitName.DEV_VALIDATION
    )

    assert aggregate["failure_stage_taxonomy"] == {
        "FINAL_JUDGE": {"OUTPUT_VALIDATION": 1}
    }
    assert aggregate["unattributed_terminal_failures"] == 0
