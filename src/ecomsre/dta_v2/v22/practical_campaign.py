"""Dual-arm practical replay campaign with post-execution truth loading."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.practical_dataset import (
    PracticalCaseSetV22,
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.practical_runner import (
    PracticalCaseRunV22,
    PracticalProviderV22,
    execute_practical_case_v22,
)
from ecomsre.dta_v2.v22.practical_scorer import (
    PracticalScoreReportV22,
    PracticalTruthV22,
    score_practical_runs_v22,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22
from ecomsre.dta_v2.v22.simple_provider import SHARED_SYSTEM_PROMPT_V22


class PracticalTruthSetV22(DtaModelV22):
    schema_version: Literal["dta-v22.practical-truth-set.v1"]
    truths: tuple[PracticalTruthV22, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_set(self) -> "PracticalTruthSetV22":
        ids = tuple(item.case_id for item in self.truths)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("practical truth set is not canonical and unique")
        return self


class PracticalCampaignResultV22(DtaModelV22):
    schema_version: Literal["dta-v22.practical-campaign-result.v1"]
    case_runs: tuple[PracticalCaseRunV22, ...]
    flat_score: PracticalScoreReportV22
    planner_score: PracticalScoreReportV22
    cases_materialized: StrictInt = Field(ge=1)
    same_case_bytes_both_arms: StrictBool
    truth_loaded_after_both_arms: StrictBool
    agent_writes: StrictInt = Field(ge=0, le=0)


TruthLoaderV22 = Callable[[Path], PracticalTruthSetV22]
RunObserverV22 = Callable[[PracticalCaseRunV22], None]


def load_practical_truth_set_v22(path: Path) -> PracticalTruthSetV22:
    return PracticalTruthSetV22.model_validate_json(path.read_bytes())


def run_practical_campaign_v22(
    *,
    case_set_path: Path,
    truth_path: Path,
    repository_root: Path,
    provider: PracticalProviderV22,
    system_prompt: str = SHARED_SYSTEM_PROMPT_V22,
    truth_loader: TruthLoaderV22 = load_practical_truth_set_v22,
    run_observer: RunObserverV22 | None = None,
) -> PracticalCampaignResultV22:
    """Execute both arms first, then load evaluator truth exactly once."""

    case_set: PracticalCaseSetV22 = load_practical_case_set_v22(case_set_path)
    runs: list[PracticalCaseRunV22] = []
    for index, spec in enumerate(case_set.cases):
        case = materialize_practical_case_v22(
            spec=spec,
            repository_root=repository_root,
        )
        arm_order = (
            (ControllerArmV22.FLAT_CANONICAL, ControllerArmV22.PLANNER_LITE)
            if index % 2 == 0
            else (ControllerArmV22.PLANNER_LITE, ControllerArmV22.FLAT_CANONICAL)
        )
        case_runs = tuple(
            execute_practical_case_v22(
                case=case,
                arm=arm,
                provider=provider,
                system_prompt=system_prompt,
            )
            for arm in arm_order
        )
        if len({item.case_bytes_sha256 for item in case_runs}) != 1:
            raise ValueError("controller arms received different case bytes")
        if run_observer is not None:
            for item in case_runs:
                run_observer(item)
        runs.extend(case_runs)
    truth_set = truth_loader(truth_path)
    expected_ids = tuple(item.case_id for item in case_set.cases)
    if tuple(item.case_id for item in truth_set.truths) != expected_ids:
        raise ValueError("campaign truth order differs from frozen cases")
    flat_runs = tuple(
        item for item in runs if item.arm is ControllerArmV22.FLAT_CANONICAL
    )
    planner_runs = tuple(
        item for item in runs if item.arm is ControllerArmV22.PLANNER_LITE
    )
    ordered_runs = tuple(
        sorted(runs, key=lambda item: (item.case_id, item.arm.value))
    )
    return PracticalCampaignResultV22(
        schema_version="dta-v22.practical-campaign-result.v1",
        case_runs=ordered_runs,
        flat_score=score_practical_runs_v22(
            runs=tuple(sorted(flat_runs, key=lambda item: item.case_id)),
            truths=truth_set.truths,
        ),
        planner_score=score_practical_runs_v22(
            runs=tuple(sorted(planner_runs, key=lambda item: item.case_id)),
            truths=truth_set.truths,
        ),
        cases_materialized=len(case_set.cases),
        same_case_bytes_both_arms=True,
        truth_loaded_after_both_arms=True,
        agent_writes=sum(item.agent_writes for item in runs),
    )


__all__ = (
    "PracticalCampaignResultV22",
    "PracticalTruthSetV22",
    "load_practical_truth_set_v22",
    "run_practical_campaign_v22",
)
