"""Case-interleaved four-combination campaign for DTA v2.2.2."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.gap_router_v222 import GapRouterModeV222
from ecomsre.dta_v2.v22.gap_study_runner_v222 import (
    GapStudyCaseRunV222,
    SelectionProviderProtocolV222,
    execute_gap_study_case_v222,
)
from ecomsre.dta_v2.v22.practical_campaign import (
    PracticalTruthSetV22,
    load_practical_truth_set_v22,
)
from ecomsre.dta_v2.v22.practical_dataset import load_practical_case_set_v22
from ecomsre.dta_v2.v22.practical_scorer import PracticalTruthV22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22


class StudyCombinationV222(str, Enum):
    FLAT_BROAD = "FLAT_BROAD"
    FLAT_GAP = "FLAT_GAP"
    PLANNER_BROAD = "PLANNER_BROAD"
    PLANNER_GAP = "PLANNER_GAP"

    @property
    def arm(self) -> ControllerArmV22:
        return (
            ControllerArmV22.FLAT_CANONICAL
            if self in {self.FLAT_BROAD, self.FLAT_GAP}
            else ControllerArmV22.PLANNER_LITE
        )

    @property
    def router_mode(self) -> GapRouterModeV222:
        return (
            GapRouterModeV222.BROAD_CATALOG
            if self in {self.FLAT_BROAD, self.PLANNER_BROAD}
            else GapRouterModeV222.GAP_RANKED_TOP_K
        )


def combination_for_run_v222(run: GapStudyCaseRunV222) -> StudyCombinationV222:
    for combination in StudyCombinationV222:
        if combination.arm is run.arm and combination.router_mode is run.router_mode:
            return combination
    raise AssertionError("run combination is outside the study factorial")


def balanced_combination_order_v222(case_index: int) -> tuple[StudyCombinationV222, ...]:
    if case_index < 0:
        raise ValueError("case index must be nonnegative")
    base = tuple(StudyCombinationV222)
    offset = case_index % len(base)
    return (*base[offset:], *base[:offset])


class StudyScheduleEntryV222(DtaModelV22):
    case_id: str
    execution_position: StrictInt = Field(ge=1, le=4)
    combination: StudyCombinationV222


class GapStudyCampaignResultV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.gap-study-campaign.v1"]
    schedule: tuple[StudyScheduleEntryV222, ...]
    runs: tuple[GapStudyCaseRunV222, ...]
    truths: tuple[PracticalTruthV22, ...]
    cases_materialized: StrictInt = Field(ge=1)
    combinations_per_case: Literal[4]
    same_case_bytes_all_combinations: StrictBool
    truth_loaded_after_all_four_runs_per_case: StrictBool
    truth_load_count: Literal[1]
    uncaught_exceptions: StrictInt = Field(ge=0)
    agent_writes: Literal[0]

    @model_validator(mode="after")
    def require_campaign(self) -> "GapStudyCampaignResultV222":
        expected = {
            (truth.case_id, combination)
            for truth in self.truths
            for combination in StudyCombinationV222
        }
        actual = {
            (run.case_id, combination_for_run_v222(run)) for run in self.runs
        }
        if actual != expected or len(self.runs) != len(expected):
            raise ValueError("campaign does not contain the full four-combination grid")
        return self


TruthLoaderV222 = Callable[[Path], PracticalTruthSetV22]
RunObserverV222 = Callable[[GapStudyCaseRunV222], None]


def run_gap_study_campaign_v222(
    *,
    repository_root: Path,
    case_set_path: Path,
    truth_path: Path,
    provider: SelectionProviderProtocolV222,
    truth_loader: TruthLoaderV222 = load_practical_truth_set_v22,
    run_observer: RunObserverV222 | None = None,
) -> GapStudyCampaignResultV222:
    """Execute all four case-local runs before loading any evaluator truth."""

    case_set = load_practical_case_set_v22(case_set_path)
    schedule: list[StudyScheduleEntryV222] = []
    runs: list[GapStudyCaseRunV222] = []
    for case_index, spec in enumerate(case_set.cases):
        case_runs: list[GapStudyCaseRunV222] = []
        for position, combination in enumerate(
            balanced_combination_order_v222(case_index),
            start=1,
        ):
            schedule.append(
                StudyScheduleEntryV222(
                    case_id=spec.case_id,
                    execution_position=position,
                    combination=combination,
                )
            )
            run = execute_gap_study_case_v222(
                spec=spec,
                repository_root=repository_root,
                arm=combination.arm,
                router_mode=combination.router_mode,
                provider=provider,
            )
            case_runs.append(run)
            runs.append(run)
            if run_observer is not None:
                run_observer(run)
        if len({item.case_bytes_sha256 for item in case_runs}) != 1:
            raise ValueError("four combinations received different case bytes")
    truth_set = truth_loader(truth_path)
    if tuple(item.case_id for item in truth_set.truths) != tuple(
        item.case_id for item in case_set.cases
    ):
        raise ValueError("campaign truth order differs from frozen cases")
    return GapStudyCampaignResultV222(
        schema_version="dta-v22.2.gap-study-campaign.v1",
        schedule=tuple(schedule),
        runs=tuple(runs),
        truths=truth_set.truths,
        cases_materialized=len(case_set.cases),
        combinations_per_case=4,
        same_case_bytes_all_combinations=True,
        truth_loaded_after_all_four_runs_per_case=True,
        truth_load_count=1,
        uncaught_exceptions=sum(item.uncaught_exceptions for item in runs),
        agent_writes=0,
    )


__all__ = (
    "GapStudyCampaignResultV222",
    "StudyCombinationV222",
    "StudyScheduleEntryV222",
    "balanced_combination_order_v222",
    "combination_for_run_v222",
    "run_gap_study_campaign_v222",
)
