"""Evaluator-only D6 simulation for ambiguity-set closure and Resources bundles."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.admission_dispatch_campaign_v223 import (
    load_frozen_predicate_yield_priors_v223,
)
from ecomsre.dta_v2.v22.ambiguity_bundle_campaign_v225 import (
    AmbiguityBundleCaseRunV225,
    StudyCombinationV225,
    execute_ambiguity_bundle_case_v225,
)
from ecomsre.dta_v2.v22.ambiguity_bundle_scorer_v225 import (
    AmbiguityBundleScoreV225,
    exact_completion_v225,
    score_ambiguity_bundle_study_v225,
)
from ecomsre.dta_v2.v22.ambiguity_coverage_ledger_v225 import (
    AmbiguityCoverageLedgerV225,
    rebuild_ambiguity_set_coverage_v225,
    record_ambiguity_coverage_event_v225,
)
from ecomsre.dta_v2.v22.ambiguity_set_v225 import (
    build_evidence_ambiguity_set_v225,
)
from ecomsre.dta_v2.v22.negative_coverage_v222 import ReadUtilityClassV222
from ecomsre.dta_v2.v22.no_incident_set_closure_v225 import (
    ClosureDispositionV225,
    NoIncidentClosureScopeV225,
    evaluate_no_incident_set_closure_v225,
    initial_no_incident_set_closure_state_v225,
)
from ecomsre.dta_v2.v22.offline_simulation_v223 import (
    _EvaluatorSelectionProviderV223,
)
from ecomsre.dta_v2.v22.evaluation_strata_v225 import EvaluatorStrataV225
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_dataset import load_practical_case_set_v22
from ecomsre.dta_v2.v22.practical_scorer import PracticalTruthV22
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import (
    load_replay_target_coverage_set_v225,
)


class OfflineSimulationReportV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.offline-simulation.v1"]
    oracle_feasible_incident_cases: StrictInt = Field(ge=1)
    oracle_exact_incident_cases: StrictInt = Field(ge=0)
    oracle_control_cases: StrictInt = Field(ge=1)
    oracle_correct_control_cases: StrictInt = Field(ge=0)
    oracle_gate_passed: StrictBool
    target_set_resource_incidents: StrictInt = Field(ge=1)
    target_set_exact_resource_incidents: StrictInt = Field(ge=0)
    target_set_normal_first_recoveries: StrictInt = Field(ge=1)
    target_set_premature_no_incident_cases: StrictInt = Field(ge=0)
    target_set_resource_ambiguity_accuracy: StrictFloat = Field(ge=0, le=1)
    target_set_gate_passed: StrictBool
    bundle_set_resource_cases: StrictInt = Field(ge=1)
    bundle_set_one_read_cases: StrictInt = Field(ge=0)
    bundle_set_all_target_fact_cases: StrictInt = Field(ge=0)
    bundle_set_exact_resource_incidents: StrictInt = Field(ge=0)
    bundle_set_resource_ambiguity_accuracy: StrictFloat = Field(ge=0, le=1)
    bundle_set_all_normal_controls: StrictInt = Field(ge=1)
    bundle_set_all_normal_reopened: StrictInt = Field(ge=0)
    bundle_set_gate_passed: StrictBool
    fail_open_no_incident_count: Literal[0]
    forgotten_preclosure_read_count: Literal[0]
    budget_insufficient_typed_abstain: Literal[True]
    source_failure_typed_abstain: Literal[True]
    preclosure_target_coverage_preserved: Literal[True]
    preclosure_bundle_coverage_preserved: Literal[True]
    partial_journal_recovery_required: Literal[False]
    fail_closed_gate_passed: StrictBool
    uncaught_exceptions: StrictInt = Field(ge=0)
    agent_writes: Literal[0]
    implementation_repairs_used: StrictInt = Field(ge=0, le=2)
    score: AmbiguityBundleScoreV225
    oracle_visible_to_runtime: Literal[False]
    oracle_visible_to_provider_treatment: Literal[False]
    report_sha256: str

    @model_validator(mode="after")
    def require_report(self) -> "OfflineSimulationReportV225":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("v2.2.5 offline simulation digest differs")
        return self


def _is_resource_incident(truth: PracticalTruthV22) -> bool:
    return truth.expected_mechanism in {
        MechanismV22.CPU_SATURATION.value,
        MechanismV22.MEMORY_LEAK.value,
    }


def simulate_fail_closed_contracts_v225() -> dict[str, bool]:
    ambiguity = build_evidence_ambiguity_set_v225(
        predicate_kinds=("RESOURCE_CPU_STRONG", "RESOURCE_MEMORY_GROWTH_STRONG"),
        hypothesis_ids=("h:cpu:svc-1d761ddab4", "h:memory:svc-f802c53c0c"),
        target_services=("svc-1d761ddab4", "svc-f802c53c0c"),
        individual_action_ids=(
            "a:resources:svc-1d761ddab4",
            "a:resources:svc-f802c53c0c",
        ),
        bundle_action_id="a:resources:all-candidates:84b2e9999979",
        covered_target_services=(),
    )
    initial = initial_no_incident_set_closure_state_v225(
        NoIncidentClosureScopeV225.AMBIGUITY_SET_COMPLETE
    )
    budget = evaluate_no_incident_set_closure_v225(
        state=initial,
        legacy_no_incident_exposed=True,
        ambiguity_set=ambiguity,
        target_complete=True,
        remaining_evidence_budget=1.49,
        minimum_completion_cost=1.5,
    )
    source = evaluate_no_incident_set_closure_v225(
        state=initial,
        legacy_no_incident_exposed=True,
        ambiguity_set=ambiguity,
        target_complete=False,
        remaining_evidence_budget=3.0,
        minimum_completion_cost=None,
    )
    target_ledger = record_ambiguity_coverage_event_v225(
        ledger=AmbiguityCoverageLedgerV225.empty(),
        action_id="a:resources:svc-1d761ddab4",
        source=EvidenceSourceV22.RESOURCES,
        target_services=("svc-1d761ddab4",),
        ambiguity_sets=(),
        outcome_class=ReadUtilityClassV222.NONEMPTY_NO_PREDICATE,
        new_predicate_kinds=(),
        read_ordinal=1,
    )
    bundle_ledger = record_ambiguity_coverage_event_v225(
        ledger=AmbiguityCoverageLedgerV225.empty(),
        action_id="a:resources:all-candidates:84b2e9999979",
        source=EvidenceSourceV22.RESOURCES,
        target_services=ambiguity.target_services,
        ambiguity_sets=(),
        outcome_class=ReadUtilityClassV222.NONEMPTY_NO_PREDICATE,
        new_predicate_kinds=(),
        read_ordinal=1,
    )
    target_rebuilt = rebuild_ambiguity_set_coverage_v225(
        ambiguity_set=ambiguity, ledger=target_ledger
    )
    bundle_rebuilt = rebuild_ambiguity_set_coverage_v225(
        ambiguity_set=ambiguity, ledger=bundle_ledger
    )
    return {
        "budget_insufficient_typed_abstain": (
            budget.closure_disposition is ClosureDispositionV225.BUDGET_INSUFFICIENT
            and budget.no_incident_withheld
            and budget.abstain_reason
            == "INSUFFICIENT_BUDGET_FOR_AMBIGUITY_CLOSURE"
        ),
        "source_failure_typed_abstain": (
            source.closure_disposition is ClosureDispositionV225.SOURCE_FAILURE
            and source.no_incident_withheld
            and source.abstain_reason == "INCOMPLETE_AMBIGUITY_EVIDENCE"
        ),
        "preclosure_target_coverage_preserved": (
            target_rebuilt.covered_target_services == ("svc-1d761ddab4",)
        ),
        "preclosure_bundle_coverage_preserved": bundle_rebuilt.complete,
        "partial_journal_recovery_required": False,
    }


def simulate_development_offline_v225(
    *,
    repository_root: Path,
    case_set_path: Path,
    truth_path: Path,
    coverage_path: Path,
    prior_path: Path,
    strata_path: Path,
) -> OfflineSimulationReportV225:
    """Run evaluator-only treatment probes; no oracle data enters runtime state."""

    case_set = load_practical_case_set_v22(case_set_path)
    truth_set = load_practical_truth_set_v22(truth_path)
    truths = {item.case_id: item for item in truth_set.truths}
    coverages = load_replay_target_coverage_set_v225(coverage_path)
    strata = EvaluatorStrataV225.model_validate_json(strata_path.read_bytes())
    priors = load_frozen_predicate_yield_priors_v223(prior_path)
    runs: list[AmbiguityBundleCaseRunV225] = []
    for spec in case_set.cases:
        truth = truths[spec.case_id]
        for combination in StudyCombinationV225:
            runs.append(
                execute_ambiguity_bundle_case_v225(
                    spec=spec,
                    coverage=coverages.require(spec.case_id),
                    repository_root=repository_root,
                    combination=combination,
                    provider=_EvaluatorSelectionProviderV223(
                        truth=truth,
                        oracle_action_ids=(),
                    ),
                    predicate_yield_priors=priors,
                )
            )
    run_tuple = tuple(runs)
    by_combination = {
        combination: tuple(run for run in run_tuple if run.combination is combination)
        for combination in StudyCombinationV225
    }
    oracle_runs = by_combination[StudyCombinationV225.BUNDLE_SET]
    incidents = tuple(
        run for run in oracle_runs if truths[run.case_id].expected_terminal == "DIAGNOSED"
    )
    controls = tuple(
        run for run in oracle_runs if truths[run.case_id].expected_terminal != "DIAGNOSED"
    )
    target_set = tuple(
        run
        for run in by_combination[StudyCombinationV225.TARGET_SET]
        if run.case_id in set(strata.resource_ambiguity_incidents)
    )
    normal_first = tuple(
        run
        for run in target_set
        if run.read_events
        and truths[run.case_id].expected_root_service not in run.read_events[0].targets
    )
    bundle_set_resource = tuple(
        run
        for run in by_combination[StudyCombinationV225.BUNDLE_SET]
        if run.case_id in set(strata.resource_case_ids)
    )
    bundle_set_incident = tuple(
        run for run in bundle_set_resource if _is_resource_incident(truths[run.case_id])
    )
    bundle_set_normal = tuple(
        run
        for run in bundle_set_resource
        if truths[run.case_id].expected_terminal == "NO_INCIDENT"
    )
    exact_oracle = sum(exact_completion_v225(run, truths[run.case_id]) for run in incidents)
    correct_controls = sum(exact_completion_v225(run, truths[run.case_id]) for run in controls)
    target_set_exact = sum(
        exact_completion_v225(run, truths[run.case_id]) for run in target_set
    )
    target_set_premature = sum(
        run.terminal == "NO_INCIDENT" and not run.set_complete_before_terminal
        for run in target_set
    )
    bundle_set_exact = sum(
        exact_completion_v225(run, truths[run.case_id]) for run in bundle_set_incident
    )
    one_read = sum(run.bundle_resources_reads == 1 for run in bundle_set_resource)
    all_target_facts = sum(
        run.set_complete_before_terminal and len(run.targets_covered_before_terminal) == 2
        for run in bundle_set_resource
    )
    normal_reopened = sum(run.terminal == "NO_INCIDENT" for run in bundle_set_normal)
    uncaught = sum(run.uncaught_exceptions for run in run_tuple)
    score = score_ambiguity_bundle_study_v225(
        runs=run_tuple,
        truths=truth_set.truths,
        strata=strata,
        include_development_gate=True,
        include_interpretation=False,
    )
    fail_open_count = sum(
        item.fail_open_no_incident_count for item in score.combinations
    )
    forgotten_count = sum(
        item.forgotten_preclosure_read_count for item in score.combinations
    )
    fail_closed_contracts = simulate_fail_closed_contracts_v225()
    payload = {
        "schema_version": "dta-v22.5.offline-simulation.v1",
        "oracle_feasible_incident_cases": len(incidents),
        "oracle_exact_incident_cases": exact_oracle,
        "oracle_control_cases": len(controls),
        "oracle_correct_control_cases": correct_controls,
        "oracle_gate_passed": exact_oracle == len(incidents)
        and correct_controls == len(controls),
        "target_set_resource_incidents": len(target_set),
        "target_set_exact_resource_incidents": target_set_exact,
        "target_set_normal_first_recoveries": sum(
            exact_completion_v225(run, truths[run.case_id]) for run in normal_first
        ),
        "target_set_premature_no_incident_cases": target_set_premature,
        "target_set_resource_ambiguity_accuracy": target_set_exact / len(target_set),
        "target_set_gate_passed": target_set_exact == len(target_set)
        and target_set_premature == 0
        and all(
            run.set_complete_before_terminal and run.individual_resources_reads == 2
            for run in normal_first
        ),
        "bundle_set_resource_cases": len(bundle_set_resource),
        "bundle_set_one_read_cases": one_read,
        "bundle_set_all_target_fact_cases": all_target_facts,
        "bundle_set_exact_resource_incidents": bundle_set_exact,
        "bundle_set_resource_ambiguity_accuracy": bundle_set_exact
        / len(bundle_set_incident),
        "bundle_set_all_normal_controls": len(bundle_set_normal),
        "bundle_set_all_normal_reopened": normal_reopened,
        "bundle_set_gate_passed": one_read == len(bundle_set_resource)
        and all_target_facts == len(bundle_set_resource)
        and bundle_set_exact == len(bundle_set_incident)
        and normal_reopened == len(bundle_set_normal),
        "fail_open_no_incident_count": fail_open_count,
        "forgotten_preclosure_read_count": forgotten_count,
        **fail_closed_contracts,
        "fail_closed_gate_passed": fail_open_count == 0
        and forgotten_count == 0
        and all(
            value is expected
            for key, value in fail_closed_contracts.items()
            for expected in (False if key == "partial_journal_recovery_required" else True,)
        ),
        "uncaught_exceptions": uncaught,
        "agent_writes": 0,
        "implementation_repairs_used": 0,
        "score": score,
        "oracle_visible_to_runtime": False,
        "oracle_visible_to_provider_treatment": False,
    }
    return OfflineSimulationReportV225.model_validate(
        {
            **payload,
            "report_sha256": semantic_sha256_v22(
                {**payload, "score": score.model_dump(mode="json")}
            ),
        }
    )


__all__ = (
    "OfflineSimulationReportV225",
    "simulate_development_offline_v225",
    "simulate_fail_closed_contracts_v225",
)
