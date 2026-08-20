"""Truth-isolated gated development and balanced four-combination study."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Callable, Literal

from pydantic import Field, StrictBool, StrictInt, StringConstraints, model_validator

from ecomsre.dta_v2.v22.evidence_acquisition_scorer_v221 import (
    ControlCostMetricsV221,
    EvidenceAcquisitionScoreReportV221,
    StudyInterpretationV221,
    compute_control_cost_metrics_v221,
    score_evidence_acquisition_runs_v221,
    summarize_study_interpretation_v221,
)
from ecomsre.dta_v2.v22.evidence_acquisition_v221 import StudyCombinationV221
from ecomsre.dta_v2.v22.practical_dataset import (
    PracticalCaseSetV22,
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.practical_runner import (
    PracticalCaseRunV221,
    PracticalProviderV221,
    PracticalRunStatusV22,
    execute_practical_case_v221,
)
from ecomsre.dta_v2.v22.practical_scorer import PracticalTruthV22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, Sha256V22
from ecomsre.dta_v2.v22.simple_provider import SHARED_SYSTEM_PROMPT_V221


FINAL_STUDY_COMBINATIONS_V221 = tuple(StudyCombinationV221)
GATED_DEVELOPMENT_COMBINATIONS_V221 = (
    StudyCombinationV221.FLAT_GATE,
    StudyCombinationV221.PLANNER_GATE,
)


class PracticalTruthSetV221(DtaModelV22):
    schema_version: Literal[
        "dta-v22.practical-truth-set.v1",
        "dta-v22.1.practical-truth-set.v1",
    ]
    truths: tuple[PracticalTruthV22, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_set(self) -> "PracticalTruthSetV221":
        ids = tuple(item.case_id for item in self.truths)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("v2.2.1 truth set is not canonical and unique")
        return self


class StudyScheduleEntryV221(DtaModelV22):
    schema_version: Literal["dta-v22.1.study-schedule-entry.v1"]
    execution_ordinal: StrictInt = Field(ge=1, le=48)
    case_id: str
    case_position: StrictInt = Field(ge=1, le=4)
    combination: StudyCombinationV221


class CombinationScoreV221(DtaModelV22):
    schema_version: Literal["dta-v22.1.combination-score.v1"]
    combination: StudyCombinationV221
    score: EvidenceAcquisitionScoreReportV221


class EvidenceAcquisitionCampaignResultV221(DtaModelV22):
    schema_version: Literal["dta-v22.1.evidence-acquisition-campaign-result.v1"]
    case_runs: tuple[PracticalCaseRunV221, ...]
    combination_scores: tuple[CombinationScoreV221, ...]
    schedule: tuple[StudyScheduleEntryV221, ...]
    combinations: tuple[StudyCombinationV221, ...]
    cases_materialized: StrictInt = Field(ge=1)
    same_case_bytes_all_combinations: StrictBool
    same_normalized_case_all_combinations: StrictBool
    truth_loaded_after_all_combinations: StrictBool
    agent_writes: StrictInt = Field(ge=0, le=0)
    control_costs: tuple[ControlCostMetricsV221, ...]
    interpretation: StudyInterpretationV221 | None


class GatedDevelopmentResultV221(DtaModelV22):
    schema_version: Literal["dta-v22.1.gated-development-result.v1"]
    passed: StrictBool
    cases: StrictInt = Field(ge=0)
    arm_runs: StrictInt = Field(ge=0)
    all_16_arm_runs_represented: StrictBool
    uncaught_exceptions: StrictInt = Field(ge=0)
    transport_failures: StrictInt = Field(ge=0)
    agent_writes: StrictInt = Field(ge=0, le=0)
    policy_redirects_bounded: StrictBool
    repeated_abstention_cannot_loop: StrictBool
    at_least_one_gated_adaptive_read: StrictBool


CommitShaV221 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]


class GatedDevelopmentArtifactV221(DtaModelV22):
    schema_version: Literal["dta-v22.1.gated-development-artifact.v1"]
    development_iteration: StrictInt = Field(ge=1, le=2)
    provider_model: str = Field(min_length=1, max_length=160)
    prompt_sha256: Sha256V22
    case_set_sha256: Sha256V22
    truth_set_sha256: Sha256V22
    campaign: EvidenceAcquisitionCampaignResultV221
    gate: GatedDevelopmentResultV221

    @model_validator(mode="after")
    def require_development_shape(self) -> "GatedDevelopmentArtifactV221":
        if self.campaign.combinations != GATED_DEVELOPMENT_COMBINATIONS_V221:
            raise ValueError("development artifact combinations differ")
        if len(self.campaign.case_runs) != 16:
            raise ValueError("development artifact run count differs")
        if self.gate.arm_runs != len(self.campaign.case_runs):
            raise ValueError("development gate differs from campaign")
        return self


class EvidenceAcquisitionStudyArtifactV221(DtaModelV22):
    schema_version: Literal["dta-v22.1.evidence-acquisition-study-artifact.v1"]
    single_execution_rule: Literal["EXACTLY_ONE_FULL_STUDY_EXECUTION"]
    execution_count: Literal[1]
    provider_model: str = Field(min_length=1, max_length=160)
    manifest_sha256: Sha256V22
    implementation_commit: CommitShaV221
    campaign: EvidenceAcquisitionCampaignResultV221

    @model_validator(mode="after")
    def require_final_study_shape(self) -> "EvidenceAcquisitionStudyArtifactV221":
        if (
            self.campaign.combinations != FINAL_STUDY_COMBINATIONS_V221
            or self.campaign.cases_materialized != 12
            or len(self.campaign.case_runs) != 48
            or self.campaign.agent_writes != 0
            or not self.campaign.same_case_bytes_all_combinations
            or not self.campaign.same_normalized_case_all_combinations
            or not self.campaign.truth_loaded_after_all_combinations
            or self.campaign.interpretation is None
        ):
            raise ValueError("final study artifact closure shape differs")
        return self


TruthLoaderV221 = Callable[[Path], PracticalTruthSetV221]
RunObserverV221 = Callable[[PracticalCaseRunV221], None]


def load_practical_truth_set_v221(path: Path) -> PracticalTruthSetV221:
    return PracticalTruthSetV221.model_validate_json(path.read_bytes())


def balanced_combination_order_v221(
    case_index: int,
    combinations: tuple[StudyCombinationV221, ...],
) -> tuple[StudyCombinationV221, ...]:
    if case_index < 0 or not combinations or len(set(combinations)) != len(combinations):
        raise ValueError("study combination rotation input is invalid")
    offset = case_index % len(combinations)
    return combinations[offset:] + combinations[:offset]


def evaluate_gated_development_v221(
    *, runs: tuple[PracticalCaseRunV221, ...]
) -> GatedDevelopmentResultV221:
    case_ids = tuple(sorted({item.case_id for item in runs}))
    expected_pairs = {
        (case_id, combination)
        for case_id in case_ids
        for combination in GATED_DEVELOPMENT_COMBINATIONS_V221
    }
    observed_pairs = {
        (
            item.case_id,
            StudyCombinationV221.FLAT_GATE
            if item.arm.value == "FLAT_CANONICAL"
            else StudyCombinationV221.PLANNER_GATE,
        )
        for item in runs
        if item.terminal_exploration_policy.value
        == "MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN"
    }
    represented = (
        len(runs) == 16
        and len(case_ids) == 8
        and observed_pairs == expected_pairs
        and len(observed_pairs) == len(runs)
    )
    uncaught = sum(item.uncaught_exceptions for item in runs)
    transport = sum(
        item.status is PracticalRunStatusV22.TRANSPORT_FAILED for item in runs
    )
    writes = sum(item.agent_writes for item in runs)
    redirects_bounded = all(item.policy_redirects <= 1 for item in runs)
    repeated_bounded = all(
        item.repeated_premature_abstentions <= 1
        and (
            item.repeated_premature_abstentions == 0
            or (
                item.status is PracticalRunStatusV22.PROTOCOL_FAILED
                and item.safe_error_code == "PREMATURE_ABSTENTION_REPEATED"
            )
        )
        for item in runs
    )
    has_read = any(item.adaptive_reads > 0 for item in runs)
    passed = all(
        (
            represented,
            uncaught == 0,
            transport == 0,
            writes == 0,
            redirects_bounded,
            repeated_bounded,
            has_read,
        )
    )
    return GatedDevelopmentResultV221(
        schema_version="dta-v22.1.gated-development-result.v1",
        passed=passed,
        cases=len(case_ids),
        arm_runs=len(runs),
        all_16_arm_runs_represented=represented,
        uncaught_exceptions=uncaught,
        transport_failures=transport,
        agent_writes=writes,
        policy_redirects_bounded=redirects_bounded,
        repeated_abstention_cannot_loop=repeated_bounded,
        at_least_one_gated_adaptive_read=has_read,
    )


def run_evidence_acquisition_campaign_v221(
    *,
    case_set_path: Path,
    truth_path: Path,
    repository_root: Path,
    provider: PracticalProviderV221,
    combinations: tuple[StudyCombinationV221, ...],
    system_prompt: str = SHARED_SYSTEM_PROMPT_V221,
    truth_loader: TruthLoaderV221 = load_practical_truth_set_v221,
    run_observer: RunObserverV221 | None = None,
) -> EvidenceAcquisitionCampaignResultV221:
    """Execute every case combination before opening evaluator truth."""

    if combinations not in {
        FINAL_STUDY_COMBINATIONS_V221,
        GATED_DEVELOPMENT_COMBINATIONS_V221,
    }:
        raise ValueError("campaign combinations are not a preregistered schedule")
    case_set: PracticalCaseSetV22 = load_practical_case_set_v22(case_set_path)
    runs: list[PracticalCaseRunV221] = []
    schedule: list[StudyScheduleEntryV221] = []
    for case_index, spec in enumerate(case_set.cases):
        case = materialize_practical_case_v22(
            spec=spec,
            repository_root=repository_root,
        )
        order = balanced_combination_order_v221(case_index, combinations)
        case_runs: list[PracticalCaseRunV221] = []
        for position, combination in enumerate(order, start=1):
            run = execute_practical_case_v221(
                case=case,
                arm=combination.arm,
                provider=provider,
                terminal_exploration_policy=combination.policy,
                system_prompt=system_prompt,
            )
            case_runs.append(run)
            runs.append(run)
            schedule.append(
                StudyScheduleEntryV221(
                    schema_version="dta-v22.1.study-schedule-entry.v1",
                    execution_ordinal=len(runs),
                    case_id=case.case_id,
                    case_position=position,
                    combination=combination,
                )
            )
            if run_observer is not None:
                run_observer(run)
        if len({item.case_bytes_sha256 for item in case_runs}) != 1:
            raise ValueError("study combinations received different case bindings")
        if len({item.normalized_case_sha256 for item in case_runs}) != 1:
            raise ValueError("study combinations received different normalized cases")
    truth_set = truth_loader(truth_path)
    expected_ids = tuple(item.case_id for item in case_set.cases)
    if tuple(item.case_id for item in truth_set.truths) != expected_ids:
        raise ValueError("v2.2.1 campaign truth order differs from frozen cases")
    bootstrap_ids = tuple(
        item.case_id for item in case_set.cases if item.bootstrap_insufficient_expected
    )
    combination_scores = tuple(
        CombinationScoreV221(
            schema_version="dta-v22.1.combination-score.v1",
            combination=combination,
            score=score_evidence_acquisition_runs_v221(
                combination=combination,
                runs=tuple(
                    sorted(
                        (
                            item
                            for item in runs
                            if item.arm is combination.arm
                            and item.terminal_exploration_policy is combination.policy
                        ),
                        key=lambda item: item.case_id,
                    )
                ),
                truths=truth_set.truths,
                bootstrap_insufficient_case_ids=bootstrap_ids,
            ),
        )
        for combination in combinations
    )
    scores_by_combination = {
        item.combination: item.score for item in combination_scores
    }
    runs_by_combination = {
        combination: tuple(
            item
            for item in runs
            if item.arm is combination.arm
            and item.terminal_exploration_policy is combination.policy
        )
        for combination in combinations
    }
    control_costs: tuple[ControlCostMetricsV221, ...]
    interpretation: StudyInterpretationV221 | None
    if combinations == FINAL_STUDY_COMBINATIONS_V221:
        control_costs = (
            compute_control_cost_metrics_v221(
                arm=StudyCombinationV221.FLAT_LEGACY.arm,
                legacy_runs=runs_by_combination[StudyCombinationV221.FLAT_LEGACY],
                gate_runs=runs_by_combination[StudyCombinationV221.FLAT_GATE],
                truths=truth_set.truths,
            ),
            compute_control_cost_metrics_v221(
                arm=StudyCombinationV221.PLANNER_LEGACY.arm,
                legacy_runs=runs_by_combination[StudyCombinationV221.PLANNER_LEGACY],
                gate_runs=runs_by_combination[StudyCombinationV221.PLANNER_GATE],
                truths=truth_set.truths,
            ),
        )
        interpretation = summarize_study_interpretation_v221(
            scores=tuple(
                scores_by_combination[item]
                for item in FINAL_STUDY_COMBINATIONS_V221
            ),
            control_costs=control_costs,
        )
    else:
        control_costs = ()
        interpretation = None
    return EvidenceAcquisitionCampaignResultV221(
        schema_version="dta-v22.1.evidence-acquisition-campaign-result.v1",
        case_runs=tuple(runs),
        combination_scores=combination_scores,
        schedule=tuple(schedule),
        combinations=combinations,
        cases_materialized=len(case_set.cases),
        same_case_bytes_all_combinations=True,
        same_normalized_case_all_combinations=True,
        truth_loaded_after_all_combinations=True,
        agent_writes=sum(item.agent_writes for item in runs),
        control_costs=control_costs,
        interpretation=interpretation,
    )


__all__ = (
    "CombinationScoreV221",
    "EvidenceAcquisitionStudyArtifactV221",
    "EvidenceAcquisitionCampaignResultV221",
    "FINAL_STUDY_COMBINATIONS_V221",
    "GATED_DEVELOPMENT_COMBINATIONS_V221",
    "GatedDevelopmentResultV221",
    "GatedDevelopmentArtifactV221",
    "PracticalTruthSetV221",
    "StudyScheduleEntryV221",
    "balanced_combination_order_v221",
    "evaluate_gated_development_v221",
    "load_practical_truth_set_v221",
    "run_evidence_acquisition_campaign_v221",
)
