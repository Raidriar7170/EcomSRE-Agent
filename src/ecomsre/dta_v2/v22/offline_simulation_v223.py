"""Evaluator-only D4 oracle and deterministic top-1 simulations."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.admission_dispatch_campaign_v223 import (
    AdmissionDispatchCaseRunV223,
    AdmissionDispatchRunStatusV223,
    StudyCombinationV223,
    execute_admission_dispatch_case_v223,
)
from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import (
    ShortestAdmissiblePathV222,
    audit_case_set_v222,
)
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_dataset import load_practical_case_set_v22
from ecomsre.dta_v2.v22.practical_scorer import PracticalTruthV22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.selection_provider_v222 import (
    SelectionDecisionV222,
    SelectionProviderOutcomeV222,
    SelectionTurnRequestV222,
)
from ecomsre.dta_v2.v22.gap_router_v223 import PredicateYieldPriorV223


_HYPOTHESIS_SUFFIX = {
    "CONFIGURATION_ERROR": "configuration-error",
    "SERVICE_UNAVAILABLE": "service-unavailable",
    "MEMORY_LEAK": "memory-leak",
    "CPU_SATURATION": "cpu-saturation",
    "DEPENDENCY_LATENCY": "dependency-latency",
}


class OfflineSimulationReportV223(DtaModelV22):
    schema_version: Literal["dta-v22.3.offline-simulation.v1"]
    oracle_feasible_incident_cases: StrictInt = Field(ge=1)
    oracle_exact_incident_cases: StrictInt = Field(ge=0)
    oracle_no_incident_controls: StrictInt = Field(ge=1)
    oracle_no_incident_correct: StrictInt = Field(ge=0)
    oracle_abstention_controls: StrictInt = Field(ge=1)
    oracle_abstention_correct: StrictInt = Field(ge=0)
    oracle_gate_passed: StrictBool
    top1_resource_silent_cases: StrictInt = Field(ge=1)
    top1_resource_silent_exact: StrictInt = Field(ge=0)
    top1_resource_silent_accuracy: StrictFloat = Field(ge=0, le=1)
    top1_resources_before_no_incident: StrictBool
    top1_premature_no_incident_cases: StrictInt = Field(ge=0)
    top1_control_cases: StrictInt = Field(ge=1)
    top1_control_correct: StrictInt = Field(ge=0)
    top1_control_accuracy: StrictFloat = Field(ge=0, le=1)
    uncaught_exceptions: StrictInt = Field(ge=0)
    agent_writes: Literal[0]
    implementation_repairs_used: Literal[1]
    top1_gate_passed: StrictBool
    oracle_visible_to_runtime: Literal[False]
    oracle_visible_to_provider_treatment: Literal[False]
    report_sha256: str

    @model_validator(mode="after")
    def require_report(self) -> "OfflineSimulationReportV223":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("v2.2.3 offline simulation digest differs")
        return self


def _expected_terminal_id(truth: PracticalTruthV22) -> str:
    if truth.expected_terminal == "NO_INCIDENT":
        return "terminal:no-incident"
    if truth.expected_terminal == "ABSTAIN":
        return "terminal:abstain"
    if truth.expected_root_service is None or truth.expected_mechanism is None:
        raise ValueError("diagnosis truth lacks root or mechanism")
    suffix = _HYPOTHESIS_SUFFIX[truth.expected_mechanism]
    return f"terminal:diagnosed:h:{truth.expected_root_service}:{suffix}"


def _expected_hypothesis_id(truth: PracticalTruthV22) -> str | None:
    if truth.expected_terminal != "DIAGNOSED":
        return None
    if truth.expected_root_service is None or truth.expected_mechanism is None:
        raise ValueError("diagnosis truth lacks root or mechanism")
    return (
        f"h:{truth.expected_root_service}:"
        f"{_HYPOTHESIS_SUFFIX[truth.expected_mechanism]}"
    )


class _EvaluatorSelectionProviderV223:
    """Constrained evaluator oracle; never used by a measured treatment."""

    def __init__(
        self,
        *,
        truth: PracticalTruthV22,
        oracle_action_ids: tuple[str, ...],
    ) -> None:
        self._truth = truth
        self._oracle_action_ids = oracle_action_ids

    def complete_turn(
        self,
        *,
        request: SelectionTurnRequestV222,
        run_id: str,
        max_protocol_repairs: int = 2,
    ) -> SelectionProviderOutcomeV222:
        del run_id, max_protocol_repairs
        if request.aliases.terminals:
            expected = _expected_terminal_id(self._truth)
            selected = next(
                (
                    item
                    for item in request.aliases.terminals
                    if item.canonical_id == expected
                ),
                request.aliases.terminals[0],
            )
            decision = SelectionDecisionV222(
                selection_alias=selected.alias,
                focus_alias="NONE",
                action_id=None,
                terminal_id=selected.canonical_id,
                focus_hypothesis_id=None,
            )
        else:
            selected = next(
                (
                    item
                    for action_id in self._oracle_action_ids
                    for item in request.aliases.actions
                    if item.canonical_id == action_id
                ),
                request.aliases.actions[0],
            )
            expected_focus = _expected_hypothesis_id(self._truth)
            focus = next(
                (
                    item
                    for item in request.aliases.hypotheses
                    if item.canonical_id == expected_focus
                ),
                request.aliases.hypotheses[0],
            )
            decision = SelectionDecisionV222(
                selection_alias=selected.alias,
                focus_alias=focus.alias,
                action_id=selected.canonical_id,
                terminal_id=None,
                focus_hypothesis_id=focus.canonical_id,
            )
        return SelectionProviderOutcomeV222(
            decision=decision,
            first_pass_protocol_success=True,
            post_repair_protocol_success=True,
            protocol_repairs=0,
            provider_calls=1,
            transport_retry_count=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            latency_ms=0.0,
        )


def _exact(run: AdmissionDispatchCaseRunV223, truth: PracticalTruthV22) -> bool:
    if run.status is not AdmissionDispatchRunStatusV223.VALID_TERMINAL:
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


def simulate_development_offline_v223(
    *,
    repository_root: Path,
    case_set_path: Path,
    truth_path: Path,
    predicate_yield_priors: tuple[PredicateYieldPriorV223, ...],
) -> OfflineSimulationReportV223:
    """Run D4 with evaluator truth isolated inside simulation-only selectors."""

    case_set = load_practical_case_set_v22(case_set_path)
    truths = {
        item.case_id: item for item in load_practical_truth_set_v22(truth_path).truths
    }
    utility = {
        item.case_id: item
        for item in audit_case_set_v222(
            repository_root=repository_root,
            case_set_path=case_set_path,
            truth_path=truth_path,
        ).cases
    }
    oracle_runs: list[AdmissionDispatchCaseRunV223] = []
    top1_runs: list[AdmissionDispatchCaseRunV223] = []
    for spec in case_set.cases:
        truth = truths[spec.case_id]
        action_ids = utility[spec.case_id].shortest_action_ids or ()
        oracle_runs.append(
            execute_admission_dispatch_case_v223(
                spec=spec,
                repository_root=repository_root,
                combination=StudyCombinationV223.MODEL_CLOSED,
                provider=_EvaluatorSelectionProviderV223(
                    truth=truth,
                    oracle_action_ids=action_ids,
                ),
                predicate_yield_priors=predicate_yield_priors,
            )
        )
        top1_runs.append(
            execute_admission_dispatch_case_v223(
                spec=spec,
                repository_root=repository_root,
                combination=StudyCombinationV223.AUTO_CLOSED,
                provider=_EvaluatorSelectionProviderV223(
                    truth=truth,
                    oracle_action_ids=(),
                ),
                predicate_yield_priors=predicate_yield_priors,
            )
        )

    feasible_incidents = tuple(
        run
        for run in oracle_runs
        if truths[run.case_id].expected_terminal == "DIAGNOSED"
        and utility[run.case_id].shortest_admissible_path
        is not ShortestAdmissiblePathV222.INFEASIBLE
    )
    no_incident = tuple(
        run
        for run in oracle_runs
        if truths[run.case_id].expected_terminal == "NO_INCIDENT"
    )
    abstention = tuple(
        run
        for run in oracle_runs
        if truths[run.case_id].expected_terminal == "ABSTAIN"
    )
    oracle_exact = sum(_exact(run, truths[run.case_id]) for run in feasible_incidents)
    no_incident_correct = sum(_exact(run, truths[run.case_id]) for run in no_incident)
    abstention_correct = sum(_exact(run, truths[run.case_id]) for run in abstention)
    oracle_gate = (
        oracle_exact == len(feasible_incidents)
        and no_incident_correct == len(no_incident)
        and abstention_correct == len(abstention)
    )

    resource_silent = tuple(
        run
        for run in top1_runs
        if truths[run.case_id].expected_mechanism
        in {"CPU_SATURATION", "MEMORY_LEAK"}
        and run.legacy_no_incident_exposed_turn_zero
    )
    resource_exact = sum(_exact(run, truths[run.case_id]) for run in resource_silent)
    resources_before = all(
        any(
            event.source == "RESOURCES"
            and (
                run.no_incident_first_open_turn is None
                or event.ordinal <= run.no_incident_first_open_turn
            )
            for event in run.adaptive_read_events
        )
        for run in resource_silent
    )
    premature = sum(run.terminal == "NO_INCIDENT" for run in resource_silent)
    controls = tuple(
        run
        for run in top1_runs
        if truths[run.case_id].expected_terminal in {"NO_INCIDENT", "ABSTAIN"}
    )
    controls_correct = sum(_exact(run, truths[run.case_id]) for run in controls)
    exceptions = sum(
        run.uncaught_exceptions for run in (*oracle_runs, *top1_runs)
    )
    resource_accuracy = resource_exact / len(resource_silent)
    control_accuracy = controls_correct / len(controls)
    top1_gate = (
        resource_accuracy >= 0.75
        and resources_before
        and premature == 0
        and control_accuracy >= 0.80
        and exceptions == 0
    )
    payload = {
        "schema_version": "dta-v22.3.offline-simulation.v1",
        "oracle_feasible_incident_cases": len(feasible_incidents),
        "oracle_exact_incident_cases": oracle_exact,
        "oracle_no_incident_controls": len(no_incident),
        "oracle_no_incident_correct": no_incident_correct,
        "oracle_abstention_controls": len(abstention),
        "oracle_abstention_correct": abstention_correct,
        "oracle_gate_passed": oracle_gate,
        "top1_resource_silent_cases": len(resource_silent),
        "top1_resource_silent_exact": resource_exact,
        "top1_resource_silent_accuracy": resource_accuracy,
        "top1_resources_before_no_incident": resources_before,
        "top1_premature_no_incident_cases": premature,
        "top1_control_cases": len(controls),
        "top1_control_correct": controls_correct,
        "top1_control_accuracy": control_accuracy,
        "uncaught_exceptions": exceptions,
        "agent_writes": 0,
        "implementation_repairs_used": 1,
        "top1_gate_passed": top1_gate,
        "oracle_visible_to_runtime": False,
        "oracle_visible_to_provider_treatment": False,
    }
    return OfflineSimulationReportV223.model_validate(
        {**payload, "report_sha256": semantic_sha256_v22(payload)}
    )


__all__ = ("OfflineSimulationReportV223", "simulate_development_offline_v223")
