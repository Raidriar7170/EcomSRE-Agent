"""Fixed-stratum factorial quality, ambiguity, cost, and reliability metrics."""

from __future__ import annotations

from collections import Counter
from statistics import fmean
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt

from ecomsre.dta_v2.v22.ambiguity_bundle_campaign_v225 import (
    AmbiguityBundleCaseRunV225,
    AmbiguityBundleRunStatusV225,
    StudyCombinationV225,
)
from ecomsre.dta_v2.v22.negative_coverage_v222 import ReadUtilityClassV222
from ecomsre.dta_v2.v22.evaluation_strata_v225 import EvaluatorStrataV225
from ecomsre.dta_v2.v22.no_incident_set_closure_v225 import (
    ClosureDispositionV225,
    NoIncidentClosureScopeV225,
)
from ecomsre.dta_v2.v22.practical_scorer import PracticalTruthV22
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    ReadSourceStatusV22,
)


MeasuredResultTerminalV225 = Literal[
    "DTA_V22_5_COMBINED_AMBIGUITY_EFFECT_OBSERVED",
    "DTA_V22_5_PARTIAL_AMBIGUITY_EFFECT_OBSERVED",
    "DTA_V22_5_NO_AMBIGUITY_EFFECT_OBSERVED",
]


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def _resource_incident(truth: PracticalTruthV22) -> bool:
    return truth.expected_mechanism in {
        MechanismV22.CPU_SATURATION.value,
        MechanismV22.MEMORY_LEAK.value,
    }


def exact_completion_v225(
    run: AmbiguityBundleCaseRunV225,
    truth: PracticalTruthV22,
) -> bool:
    if run.status is not AmbiguityBundleRunStatusV225.VALID_TERMINAL:
        return False
    if run.terminal != truth.expected_terminal:
        return False
    if truth.expected_terminal != "DIAGNOSED":
        return True
    return (
        run.root_service == truth.expected_root_service
        and run.mechanism == truth.expected_mechanism
        and bool(run.supporting_evidence_refs)
        and run.matched_clause_id is not None
    )


def _macro_f1(
    *,
    runs: tuple[AmbiguityBundleCaseRunV225, ...],
    truths: dict[str, PracticalTruthV22],
) -> float:
    labels = tuple(
        item.value
        for item in MechanismV22
        if item not in {MechanismV22.NO_INCIDENT, MechanismV22.UNKNOWN}
    )
    scores: list[float] = []
    for label in labels:
        tp = sum(
            truths[run.case_id].expected_mechanism == label and run.mechanism == label
            for run in runs
        )
        fp = sum(
            truths[run.case_id].expected_mechanism != label and run.mechanism == label
            for run in runs
        )
        fn = sum(
            truths[run.case_id].expected_mechanism == label and run.mechanism != label
            for run in runs
        )
        scores.append(_ratio(2 * tp, 2 * tp + fp + fn))
    return fmean(scores)


class CombinationMetricsV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.combination-metrics.v1"]
    combination: StudyCombinationV225
    total_runs: StrictInt = Field(ge=1)
    incident_denominator: StrictInt = Field(ge=0)
    resource_ambiguity_denominator: StrictInt = Field(ge=0)
    resource_case_denominator: StrictInt = Field(ge=0)
    no_incident_denominator: StrictInt = Field(ge=0)
    abstention_denominator: StrictInt = Field(ge=0)
    exact_completion_cases: StrictInt = Field(ge=0)
    exact_completion_rate: StrictFloat = Field(ge=0, le=1)
    incident_root_accuracy: StrictFloat = Field(ge=0, le=1)
    mechanism_accuracy: StrictFloat = Field(ge=0, le=1)
    mechanism_macro_f1: StrictFloat = Field(ge=0, le=1)
    evidence_ref_validity: StrictFloat = Field(ge=0, le=1)
    semantic_clause_validity: StrictFloat = Field(ge=0, le=1)
    no_incident_accuracy: StrictFloat = Field(ge=0, le=1)
    abstention_accuracy: StrictFloat = Field(ge=0, le=1)
    combined_control_accuracy: StrictFloat = Field(ge=0, le=1)
    ambiguity_set_count: StrictInt = Field(ge=0)
    mean_ambiguity_set_size: StrictFloat = Field(ge=0)
    set_completion_rate: StrictFloat = Field(ge=0, le=1)
    wrong_target_first_rate: StrictFloat = Field(ge=0, le=1)
    premature_no_incident_partial_rate: StrictFloat = Field(ge=0, le=1)
    premature_no_incident_complete_rate: StrictFloat = Field(ge=0, le=1)
    resource_ambiguity_exact_accuracy: StrictFloat = Field(ge=0, le=1)
    bundle_eligibility_rate: StrictFloat = Field(ge=0, le=1)
    bundle_dispatch_count: StrictInt = Field(ge=0)
    bundle_predicate_yield_rate: StrictFloat = Field(ge=0, le=1)
    bundle_all_normal_rate: StrictFloat = Field(ge=0, le=1)
    bundle_schema_failure_rate: StrictFloat = Field(ge=0, le=1)
    individual_resources_reads: StrictInt = Field(ge=0)
    bundle_resources_reads: StrictInt = Field(ge=0)
    mean_resources_reads_per_resource_case: StrictFloat = Field(ge=0)
    provider_calls: StrictInt = Field(ge=0)
    automatic_dispatches: StrictInt = Field(ge=0)
    protocol_repairs: StrictInt = Field(ge=0)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    tokens_per_correct_case: StrictFloat = Field(ge=0)
    latency_ms: StrictFloat = Field(ge=0)
    transport_retries: StrictInt = Field(ge=0)
    protocol_failure_rate: StrictFloat = Field(ge=0, le=1)
    first_pass_protocol_success_rate: StrictFloat = Field(ge=0, le=1)
    post_repair_protocol_success_rate: StrictFloat = Field(ge=0, le=1)
    configuration_accuracy: StrictFloat = Field(ge=0, le=1)
    service_unavailable_accuracy: StrictFloat = Field(ge=0, le=1)
    dependency_accuracy: StrictFloat = Field(ge=0, le=1)
    nonresource_regression_count: StrictInt = Field(ge=0)
    budget_insufficient_closure_states: StrictInt = Field(ge=0)
    budget_insufficient_abstain_accuracy: StrictFloat = Field(ge=0, le=1)
    source_failure_closure_states: StrictInt = Field(ge=0)
    source_failure_abstain_accuracy: StrictFloat = Field(ge=0, le=1)
    fail_open_no_incident_count: StrictInt = Field(ge=0)
    forgotten_preclosure_read_count: StrictInt = Field(ge=0)
    outcome_distribution: dict[str, int]
    uncaught_exceptions: StrictInt = Field(ge=0)
    agent_writes: Literal[0]


class DevelopmentGateV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.development-gate.v1"]
    target_set_resource_ambiguity_accuracy: StrictFloat
    bundle_set_resource_ambiguity_accuracy: StrictFloat
    bundle_set_premature_no_incident_rate: StrictFloat
    bundle_set_mean_resources_reads: StrictFloat
    target_set_mean_resources_reads: StrictFloat
    bundle_set_control_accuracy: StrictFloat
    protocol_failure_rate: StrictFloat
    exact_case_gain: StrictInt
    mechanism_macro_f1_gain: StrictFloat
    fail_open_no_incident_count: StrictInt
    forgotten_preclosure_read_count: StrictInt
    uncaught_exceptions: StrictInt
    agent_writes: Literal[0]
    gate_passed: StrictBool


class ClosureMainEffectV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.closure-main-effect.v1"]
    resource_ambiguity_accuracy_improvement: StrictFloat
    premature_no_incident_decrease: StrictFloat
    target_set_completion_improvement: StrictFloat
    mean_resources_read_change: StrictFloat
    control_accuracy_change: StrictFloat


class BundleMainEffectV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.bundle-main-effect.v1"]
    resource_ambiguity_accuracy_change: StrictFloat
    mean_resources_read_decrease: StrictFloat
    provider_call_fraction_decrease: StrictFloat
    token_fraction_decrease: StrictFloat
    latency_fraction_decrease: StrictFloat
    bundle_schema_failures: StrictInt


class FactorialInterpretationV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.factorial-interpretation.v1"]
    closure_main_effect: ClosureMainEffectV225
    bundle_main_effect: BundleMainEffectV225
    interaction_exact_rate: StrictFloat
    measured_result_terminal: MeasuredResultTerminalV225
    combined_threshold_passed: StrictBool
    closure_threshold_passed: StrictBool
    bundle_threshold_passed: StrictBool


class AmbiguityBundleScoreV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.ambiguity-bundle-score.v1"]
    combinations: tuple[CombinationMetricsV225, ...]
    development_gate: DevelopmentGateV225 | None
    interpretation: FactorialInterpretationV225 | None


def _mechanism_accuracy(
    mechanism: str,
    *,
    runs: tuple[AmbiguityBundleCaseRunV225, ...],
    truths: dict[str, PracticalTruthV22],
) -> float:
    subset = tuple(
        run for run in runs if truths[run.case_id].expected_mechanism == mechanism
    )
    return _ratio(
        sum(exact_completion_v225(run, truths[run.case_id]) for run in subset),
        len(subset),
    )


def _score_combination(
    *,
    combination: StudyCombinationV225,
    runs: tuple[AmbiguityBundleCaseRunV225, ...],
    truths: dict[str, PracticalTruthV22],
    strata: EvaluatorStrataV225,
) -> CombinationMetricsV225:
    incident_ids = {
        *strata.resource_ambiguity_incidents,
        *strata.configuration_incidents,
        *strata.service_unavailable_incidents,
        *strata.dependency_incidents,
    }
    resource_incident_ids = set(strata.resource_ambiguity_incidents)
    resource_case_ids = set(strata.resource_case_ids)
    no_incident_ids = set(strata.resource_normal_controls)
    abstention_ids = set(strata.abstention_controls)
    incident = tuple(run for run in runs if run.case_id in incident_ids)
    resource_incident = tuple(
        run for run in runs if run.case_id in resource_incident_ids
    )
    resource_cases = tuple(run for run in runs if run.case_id in resource_case_ids)
    no_incident = tuple(run for run in runs if run.case_id in no_incident_ids)
    abstention = tuple(run for run in runs if run.case_id in abstention_ids)
    controls = (*no_incident, *abstention)
    exact = sum(exact_completion_v225(run, truths[run.case_id]) for run in runs)
    bundle_events = tuple(event for run in runs for event in run.read_events if event.bundle)
    first_resource_events = {
        run.case_id: next(
            (event for event in run.read_events if event.source is EvidenceSourceV22.RESOURCES),
            None,
        )
        for run in resource_incident
    }
    provider_calls = sum(run.provider_calls for run in runs)
    budget_insufficient = tuple(
        run
        for run in runs
        if run.closure_state.closure_disposition
        is ClosureDispositionV225.BUDGET_INSUFFICIENT
    )
    source_failure = tuple(
        run
        for run in runs
        if run.closure_state.closure_disposition
        is ClosureDispositionV225.SOURCE_FAILURE
    )
    fail_open = sum(
        run.terminal == "NO_INCIDENT"
        and run.closure_scope is NoIncidentClosureScopeV225.AMBIGUITY_SET_COMPLETE
        and (
            run.closure_state.closure_disposition
            in {
                ClosureDispositionV225.BUDGET_INSUFFICIENT,
                ClosureDispositionV225.SOURCE_FAILURE,
            }
            or (
                run.case_id in resource_incident_ids
                and not run.set_complete_before_terminal
            )
        )
        for run in runs
    )
    return CombinationMetricsV225(
        schema_version="dta-v22.5.combination-metrics.v1",
        combination=combination,
        total_runs=len(runs),
        incident_denominator=len(incident),
        resource_ambiguity_denominator=len(resource_incident),
        resource_case_denominator=len(resource_cases),
        no_incident_denominator=len(no_incident),
        abstention_denominator=len(abstention),
        exact_completion_cases=exact,
        exact_completion_rate=_ratio(exact, len(runs)),
        incident_root_accuracy=_ratio(
            sum(run.root_service == truths[run.case_id].expected_root_service for run in incident),
            len(incident),
        ),
        mechanism_accuracy=_ratio(
            sum(run.mechanism == truths[run.case_id].expected_mechanism for run in incident),
            len(incident),
        ),
        mechanism_macro_f1=_macro_f1(runs=runs, truths=truths),
        evidence_ref_validity=_ratio(
            sum(bool(run.supporting_evidence_refs) for run in incident), len(incident)
        ),
        semantic_clause_validity=_ratio(
            sum(run.matched_clause_id is not None for run in incident), len(incident)
        ),
        no_incident_accuracy=_ratio(
            sum(exact_completion_v225(run, truths[run.case_id]) for run in no_incident),
            len(no_incident),
        ),
        abstention_accuracy=_ratio(
            sum(exact_completion_v225(run, truths[run.case_id]) for run in abstention),
            len(abstention),
        ),
        combined_control_accuracy=_ratio(
            sum(exact_completion_v225(run, truths[run.case_id]) for run in controls),
            len(controls),
        ),
        ambiguity_set_count=sum(run.ambiguity_set_count for run in runs),
        mean_ambiguity_set_size=(
            0.0 if not resource_cases else fmean(run.ambiguity_set_size for run in resource_cases)
        ),
        set_completion_rate=_ratio(
            sum(run.set_complete_before_terminal for run in resource_cases),
            len(resource_cases),
        ),
        wrong_target_first_rate=_ratio(
            sum(
                event is not None
                and truths[run.case_id].expected_root_service not in event.targets
                for run in resource_incident
                for event in (first_resource_events[run.case_id],)
            ),
            len(resource_incident),
        ),
        premature_no_incident_partial_rate=_ratio(
            sum(run.terminal == "NO_INCIDENT" and not run.set_complete_before_terminal for run in resource_incident),
            len(resource_incident),
        ),
        premature_no_incident_complete_rate=_ratio(
            sum(run.terminal == "NO_INCIDENT" and run.set_complete_before_terminal for run in resource_incident),
            len(resource_incident),
        ),
        resource_ambiguity_exact_accuracy=_ratio(
            sum(exact_completion_v225(run, truths[run.case_id]) for run in resource_incident),
            len(resource_incident),
        ),
        bundle_eligibility_rate=_ratio(
            sum(run.bundle_eligible for run in resource_cases), len(resource_cases)
        ),
        bundle_dispatch_count=len(bundle_events),
        bundle_predicate_yield_rate=_ratio(
            sum(event.outcome_class is ReadUtilityClassV222.PREDICATE_YIELD for event in bundle_events),
            len(bundle_events),
        ),
        bundle_all_normal_rate=_ratio(
            sum(event.outcome_class is ReadUtilityClassV222.NONEMPTY_NO_PREDICATE for event in bundle_events),
            len(bundle_events),
        ),
        bundle_schema_failure_rate=_ratio(
            sum(event.status is ReadSourceStatusV22.FAILURE_SCHEMA for event in bundle_events),
            len(bundle_events),
        ),
        individual_resources_reads=sum(run.individual_resources_reads for run in runs),
        bundle_resources_reads=sum(run.bundle_resources_reads for run in runs),
        mean_resources_reads_per_resource_case=_ratio(
            sum(run.individual_resources_reads + run.bundle_resources_reads for run in resource_cases),
            len(resource_cases),
        ),
        provider_calls=provider_calls,
        automatic_dispatches=sum(run.automatic_dispatches for run in runs),
        protocol_repairs=sum(run.protocol_repairs for run in runs),
        input_tokens=sum(run.input_tokens for run in runs),
        output_tokens=sum(run.output_tokens for run in runs),
        total_tokens=sum(run.total_tokens for run in runs),
        tokens_per_correct_case=_ratio(sum(run.total_tokens for run in runs), exact),
        latency_ms=sum(run.latency_ms for run in runs),
        transport_retries=sum(run.transport_retry_count for run in runs),
        protocol_failure_rate=_ratio(
            sum(run.status is AmbiguityBundleRunStatusV225.PROTOCOL_FAILED for run in runs),
            len(runs),
        ),
        first_pass_protocol_success_rate=_ratio(
            sum(run.first_pass_protocol_successes for run in runs), provider_calls
        ),
        post_repair_protocol_success_rate=_ratio(
            sum(run.post_repair_protocol_successes for run in runs), provider_calls
        ),
        configuration_accuracy=_mechanism_accuracy(
            MechanismV22.CONFIGURATION_ERROR.value, runs=runs, truths=truths
        ),
        service_unavailable_accuracy=_mechanism_accuracy(
            MechanismV22.SERVICE_UNAVAILABLE.value, runs=runs, truths=truths
        ),
        dependency_accuracy=_mechanism_accuracy(
            MechanismV22.DEPENDENCY_LATENCY.value, runs=runs, truths=truths
        ),
        nonresource_regression_count=sum(
            not exact_completion_v225(run, truths[run.case_id])
            for run in incident
            if not _resource_incident(truths[run.case_id])
        ),
        budget_insufficient_closure_states=len(budget_insufficient),
        budget_insufficient_abstain_accuracy=_ratio(
            sum(run.terminal == "ABSTAIN" for run in budget_insufficient),
            len(budget_insufficient),
        ),
        source_failure_closure_states=len(source_failure),
        source_failure_abstain_accuracy=_ratio(
            sum(run.terminal == "ABSTAIN" for run in source_failure),
            len(source_failure),
        ),
        fail_open_no_incident_count=fail_open,
        forgotten_preclosure_read_count=sum(
            run.forgotten_preclosure_read_count for run in runs
        ),
        outcome_distribution=dict(
            sorted(Counter(event.outcome_class.value for run in runs for event in run.read_events).items())
        ),
        uncaught_exceptions=sum(run.uncaught_exceptions for run in runs),
        agent_writes=0,
    )


def _pooled(
    metrics: tuple[CombinationMetricsV225, ...],
    names: set[StudyCombinationV225],
    field: str,
) -> float:
    return fmean(getattr(item, field) for item in metrics if item.combination in names)


def _fraction_decrease(baseline: float, treatment: float) -> float:
    return 0.0 if baseline == 0 else (baseline - treatment) / baseline


def score_ambiguity_bundle_study_v225(
    *,
    runs: tuple[AmbiguityBundleCaseRunV225, ...],
    truths: tuple[PracticalTruthV22, ...],
    strata: EvaluatorStrataV225,
    include_development_gate: bool,
    include_interpretation: bool,
) -> AmbiguityBundleScoreV225:
    truth_by_id = {item.case_id: item for item in truths}
    if set(truth_by_id) != set(strata.all_case_ids):
        raise ValueError("v2.2.5 scorer truth set differs from frozen evaluator strata")
    if (
        {item.case_id for item in truths if _resource_incident(item)}
        != set(strata.resource_ambiguity_incidents)
        or {
            item.case_id
            for item in truths
            if item.expected_mechanism == MechanismV22.CPU_SATURATION.value
        }
        != set(strata.cpu_incidents)
        or {
            item.case_id
            for item in truths
            if item.expected_mechanism == MechanismV22.MEMORY_LEAK.value
        }
        != set(strata.memory_incidents)
    ):
        raise ValueError("v2.2.5 scorer mechanism strata differ from frozen truth")
    expected = {
        (truth.case_id, combination)
        for truth in truths
        for combination in StudyCombinationV225
    }
    if {(run.case_id, run.combination) for run in runs} != expected or len(runs) != len(expected):
        raise ValueError("v2.2.5 scorer factorial grid differs")
    metrics = tuple(
        _score_combination(
            combination=combination,
            runs=tuple(run for run in runs if run.combination is combination),
            truths=truth_by_id,
            strata=strata,
        )
        for combination in StudyCombinationV225
    )
    by_name = {item.combination: item for item in metrics}
    target_one = by_name[StudyCombinationV225.TARGET_ONE]
    target_set = by_name[StudyCombinationV225.TARGET_SET]
    bundle_set = by_name[StudyCombinationV225.BUNDLE_SET]
    development: DevelopmentGateV225 | None = None
    if include_development_gate:
        protocol_failure = max(item.protocol_failure_rate for item in metrics)
        exceptions = sum(item.uncaught_exceptions for item in metrics)
        exact_gain = bundle_set.exact_completion_cases - target_one.exact_completion_cases
        macro_gain = bundle_set.mechanism_macro_f1 - target_one.mechanism_macro_f1
        fail_open_count = sum(item.fail_open_no_incident_count for item in metrics)
        forgotten_count = sum(item.forgotten_preclosure_read_count for item in metrics)
        gate = (
            target_set.resource_ambiguity_exact_accuracy >= 0.75
            and bundle_set.resource_ambiguity_exact_accuracy >= 0.75
            and bundle_set.premature_no_incident_partial_rate <= 0.25
            and bundle_set.mean_resources_reads_per_resource_case <= 1.25
            and target_set.mean_resources_reads_per_resource_case <= 2.0
            and bundle_set.combined_control_accuracy >= 0.80
            and protocol_failure <= 0.10
            and exceptions == 0
            and fail_open_count == 0
            and forgotten_count == 0
            and all(item.agent_writes == 0 for item in metrics)
            and (exact_gain >= 2 or macro_gain >= 0.15)
        )
        development = DevelopmentGateV225(
            schema_version="dta-v22.5.development-gate.v1",
            target_set_resource_ambiguity_accuracy=target_set.resource_ambiguity_exact_accuracy,
            bundle_set_resource_ambiguity_accuracy=bundle_set.resource_ambiguity_exact_accuracy,
            bundle_set_premature_no_incident_rate=bundle_set.premature_no_incident_partial_rate,
            bundle_set_mean_resources_reads=bundle_set.mean_resources_reads_per_resource_case,
            target_set_mean_resources_reads=target_set.mean_resources_reads_per_resource_case,
            bundle_set_control_accuracy=bundle_set.combined_control_accuracy,
            protocol_failure_rate=protocol_failure,
            exact_case_gain=exact_gain,
            mechanism_macro_f1_gain=macro_gain,
            fail_open_no_incident_count=fail_open_count,
            forgotten_preclosure_read_count=forgotten_count,
            uncaught_exceptions=exceptions,
            agent_writes=0,
            gate_passed=gate,
        )
    interpretation: FactorialInterpretationV225 | None = None
    if include_interpretation:
        set_names = {StudyCombinationV225.TARGET_SET, StudyCombinationV225.BUNDLE_SET}
        one_names = {StudyCombinationV225.TARGET_ONE, StudyCombinationV225.BUNDLE_ONE}
        bundle_names = {StudyCombinationV225.BUNDLE_ONE, StudyCombinationV225.BUNDLE_SET}
        target_names = {StudyCombinationV225.TARGET_ONE, StudyCombinationV225.TARGET_SET}
        closure = ClosureMainEffectV225(
            schema_version="dta-v22.5.closure-main-effect.v1",
            resource_ambiguity_accuracy_improvement=(
                _pooled(metrics, set_names, "resource_ambiguity_exact_accuracy")
                - _pooled(metrics, one_names, "resource_ambiguity_exact_accuracy")
            ),
            premature_no_incident_decrease=(
                _pooled(metrics, one_names, "premature_no_incident_partial_rate")
                - _pooled(metrics, set_names, "premature_no_incident_partial_rate")
            ),
            target_set_completion_improvement=(
                _pooled(metrics, set_names, "set_completion_rate")
                - _pooled(metrics, one_names, "set_completion_rate")
            ),
            mean_resources_read_change=(
                _pooled(metrics, set_names, "mean_resources_reads_per_resource_case")
                - _pooled(metrics, one_names, "mean_resources_reads_per_resource_case")
            ),
            control_accuracy_change=(
                _pooled(metrics, set_names, "combined_control_accuracy")
                - _pooled(metrics, one_names, "combined_control_accuracy")
            ),
        )
        target_calls = _pooled(metrics, target_names, "provider_calls")
        bundle_calls = _pooled(metrics, bundle_names, "provider_calls")
        target_tokens = _pooled(metrics, target_names, "total_tokens")
        bundle_tokens = _pooled(metrics, bundle_names, "total_tokens")
        target_latency = _pooled(metrics, target_names, "latency_ms")
        bundle_latency = _pooled(metrics, bundle_names, "latency_ms")
        bundle = BundleMainEffectV225(
            schema_version="dta-v22.5.bundle-main-effect.v1",
            resource_ambiguity_accuracy_change=(
                _pooled(metrics, bundle_names, "resource_ambiguity_exact_accuracy")
                - _pooled(metrics, target_names, "resource_ambiguity_exact_accuracy")
            ),
            mean_resources_read_decrease=(
                _pooled(metrics, target_names, "mean_resources_reads_per_resource_case")
                - _pooled(metrics, bundle_names, "mean_resources_reads_per_resource_case")
            ),
            provider_call_fraction_decrease=_fraction_decrease(target_calls, bundle_calls),
            token_fraction_decrease=_fraction_decrease(target_tokens, bundle_tokens),
            latency_fraction_decrease=_fraction_decrease(target_latency, bundle_latency),
            bundle_schema_failures=sum(
                int(item.bundle_schema_failure_rate * item.bundle_dispatch_count)
                for item in metrics
                if item.combination in bundle_names
            ),
        )
        combined = (
            bundle_set.resource_ambiguity_exact_accuracy >= 0.75
            and bundle_set.resource_ambiguity_exact_accuracy
            >= target_one.resource_ambiguity_exact_accuracy + 0.50
            and bundle_set.premature_no_incident_partial_rate <= 0.125
            and (
                bundle_set.exact_completion_cases >= target_one.exact_completion_cases + 3
                or bundle_set.mechanism_macro_f1 >= target_one.mechanism_macro_f1 + 0.20
            )
            and bundle_set.combined_control_accuracy
            >= target_one.combined_control_accuracy - 0.25
            and bundle_set.mean_resources_reads_per_resource_case
            <= target_set.mean_resources_reads_per_resource_case - 0.50
            and bundle_set.agent_writes == 0
            and bundle_set.fail_open_no_incident_count == 0
            and bundle_set.forgotten_preclosure_read_count == 0
        )
        closure_pass = (
            closure.resource_ambiguity_accuracy_improvement >= 0.25
            and closure.premature_no_incident_decrease >= 0.50
            and closure.control_accuracy_change >= -0.25
            and sum(item.fail_open_no_incident_count for item in metrics) == 0
        )
        bundle_pass = (
            bundle.resource_ambiguity_accuracy_change >= 0.0
            and bundle.mean_resources_read_decrease >= 0.50
            and (
                bundle.provider_call_fraction_decrease >= 0.15
                or bundle.token_fraction_decrease >= 0.15
            )
            and bundle.bundle_schema_failures == 0
        )
        terminal: MeasuredResultTerminalV225 = (
            "DTA_V22_5_COMBINED_AMBIGUITY_EFFECT_OBSERVED"
            if combined
            else "DTA_V22_5_PARTIAL_AMBIGUITY_EFFECT_OBSERVED"
            if closure_pass or bundle_pass
            else "DTA_V22_5_NO_AMBIGUITY_EFFECT_OBSERVED"
        )
        target_set_effect = target_set.exact_completion_rate - target_one.exact_completion_rate
        bundle_one_effect = (
            by_name[StudyCombinationV225.BUNDLE_ONE].exact_completion_rate
            - target_one.exact_completion_rate
        )
        interpretation = FactorialInterpretationV225(
            schema_version="dta-v22.5.factorial-interpretation.v1",
            closure_main_effect=closure,
            bundle_main_effect=bundle,
            interaction_exact_rate=(
                bundle_set.exact_completion_rate
                - target_one.exact_completion_rate
                - target_set_effect
                - bundle_one_effect
            ),
            measured_result_terminal=terminal,
            combined_threshold_passed=combined,
            closure_threshold_passed=closure_pass,
            bundle_threshold_passed=bundle_pass,
        )
    return AmbiguityBundleScoreV225(
        schema_version="dta-v22.5.ambiguity-bundle-score.v1",
        combinations=metrics,
        development_gate=development,
        interpretation=interpretation,
    )


__all__ = (
    "AmbiguityBundleScoreV225",
    "CombinationMetricsV225",
    "DevelopmentGateV225",
    "FactorialInterpretationV225",
    "exact_completion_v225",
    "score_ambiguity_bundle_study_v225",
)
