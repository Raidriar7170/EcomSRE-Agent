"""Evaluator-only D6 simulation for ambiguity-set closure and Resources bundles."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.admission_dispatch_campaign_v223 import (
    load_frozen_predicate_yield_priors_v223,
)
from ecomsre.dta_v2.v22.ambiguity_bundle_campaign_v224 import (
    AmbiguityBundleCaseRunV224,
    StudyCombinationV224,
    execute_ambiguity_bundle_case_v224,
)
from ecomsre.dta_v2.v22.ambiguity_bundle_scorer_v224 import (
    AmbiguityBundleScoreV224,
    exact_completion_v224,
    score_ambiguity_bundle_study_v224,
)
from ecomsre.dta_v2.v22.offline_simulation_v223 import (
    _EvaluatorSelectionProviderV223,
)
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_dataset import load_practical_case_set_v22
from ecomsre.dta_v2.v22.practical_scorer import PracticalTruthV22
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.replay_target_coverage_v224 import (
    load_replay_target_coverage_set_v224,
)


class OfflineSimulationReportV224(DtaModelV22):
    schema_version: Literal["dta-v22.4.offline-simulation.v1"]
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
    uncaught_exceptions: StrictInt = Field(ge=0)
    agent_writes: Literal[0]
    implementation_repairs_used: StrictInt = Field(ge=0, le=2)
    score: AmbiguityBundleScoreV224
    oracle_visible_to_runtime: Literal[False]
    oracle_visible_to_provider_treatment: Literal[False]
    report_sha256: str

    @model_validator(mode="after")
    def require_report(self) -> "OfflineSimulationReportV224":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("v2.2.4 offline simulation digest differs")
        return self


def _is_resource_incident(truth: PracticalTruthV22) -> bool:
    return truth.expected_mechanism in {
        MechanismV22.CPU_SATURATION.value,
        MechanismV22.MEMORY_LEAK.value,
    }


def simulate_development_offline_v224(
    *,
    repository_root: Path,
    case_set_path: Path,
    truth_path: Path,
    coverage_path: Path,
    prior_path: Path,
) -> OfflineSimulationReportV224:
    """Run evaluator-only treatment probes; no oracle data enters runtime state."""

    case_set = load_practical_case_set_v22(case_set_path)
    truth_set = load_practical_truth_set_v22(truth_path)
    truths = {item.case_id: item for item in truth_set.truths}
    coverages = load_replay_target_coverage_set_v224(coverage_path)
    priors = load_frozen_predicate_yield_priors_v223(prior_path)
    runs: list[AmbiguityBundleCaseRunV224] = []
    for spec in case_set.cases:
        truth = truths[spec.case_id]
        for combination in StudyCombinationV224:
            runs.append(
                execute_ambiguity_bundle_case_v224(
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
        for combination in StudyCombinationV224
    }
    oracle_runs = by_combination[StudyCombinationV224.BUNDLE_SET]
    incidents = tuple(
        run for run in oracle_runs if truths[run.case_id].expected_terminal == "DIAGNOSED"
    )
    controls = tuple(
        run for run in oracle_runs if truths[run.case_id].expected_terminal != "DIAGNOSED"
    )
    target_set = tuple(
        run
        for run in by_combination[StudyCombinationV224.TARGET_SET]
        if _is_resource_incident(truths[run.case_id])
    )
    normal_first = tuple(
        run
        for run in target_set
        if run.read_events
        and truths[run.case_id].expected_root_service not in run.read_events[0].targets
    )
    bundle_set_resource = tuple(
        run
        for run in by_combination[StudyCombinationV224.BUNDLE_SET]
        if run.resource_target_complete and run.ambiguity_set_count > 0
    )
    bundle_set_incident = tuple(
        run for run in bundle_set_resource if _is_resource_incident(truths[run.case_id])
    )
    bundle_set_normal = tuple(
        run
        for run in bundle_set_resource
        if truths[run.case_id].expected_terminal == "NO_INCIDENT"
    )
    exact_oracle = sum(exact_completion_v224(run, truths[run.case_id]) for run in incidents)
    correct_controls = sum(exact_completion_v224(run, truths[run.case_id]) for run in controls)
    target_set_exact = sum(
        exact_completion_v224(run, truths[run.case_id]) for run in target_set
    )
    target_set_premature = sum(
        run.terminal == "NO_INCIDENT" and not run.set_complete_before_terminal
        for run in target_set
    )
    bundle_set_exact = sum(
        exact_completion_v224(run, truths[run.case_id]) for run in bundle_set_incident
    )
    one_read = sum(run.bundle_resources_reads == 1 for run in bundle_set_resource)
    all_target_facts = sum(
        run.set_complete_before_terminal and len(run.targets_covered_before_terminal) == 2
        for run in bundle_set_resource
    )
    normal_reopened = sum(run.terminal == "NO_INCIDENT" for run in bundle_set_normal)
    uncaught = sum(run.uncaught_exceptions for run in run_tuple)
    score = score_ambiguity_bundle_study_v224(
        runs=run_tuple,
        truths=truth_set.truths,
        include_development_gate=True,
        include_interpretation=False,
    )
    payload = {
        "schema_version": "dta-v22.4.offline-simulation.v1",
        "oracle_feasible_incident_cases": len(incidents),
        "oracle_exact_incident_cases": exact_oracle,
        "oracle_control_cases": len(controls),
        "oracle_correct_control_cases": correct_controls,
        "oracle_gate_passed": exact_oracle == len(incidents)
        and correct_controls == len(controls),
        "target_set_resource_incidents": len(target_set),
        "target_set_exact_resource_incidents": target_set_exact,
        "target_set_normal_first_recoveries": sum(
            exact_completion_v224(run, truths[run.case_id]) for run in normal_first
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
        "uncaught_exceptions": uncaught,
        "agent_writes": 0,
        "implementation_repairs_used": 0,
        "score": score,
        "oracle_visible_to_runtime": False,
        "oracle_visible_to_provider_treatment": False,
    }
    return OfflineSimulationReportV224(
        **payload,
        report_sha256=semantic_sha256_v22(
            {**payload, "score": score.model_dump(mode="json")}
        ),
    )


__all__ = ("OfflineSimulationReportV224", "simulate_development_offline_v224")
