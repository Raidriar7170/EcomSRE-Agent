from __future__ import annotations

from pathlib import Path

from ecomsre.dta_v2.v21.capture_campaign import build_default_capture_plan_v21
from ecomsre.dta_v2.v21.evaluation_scenarios import (
    build_evaluation_scenario_registry_v21,
)


ROOT = Path(__file__).resolve().parents[2]


def test_evaluation_scenarios_cover_every_case_without_truth_fields() -> None:
    registry = build_evaluation_scenario_registry_v21(ROOT)
    plan = build_default_capture_plan_v21(base_head="a" * 40)

    assert {item.scenario_id for item in plan.cases}.issubset(
        {item.scenario_id for item in registry.scenarios}
    )
    recommendation = registry.require("dta21-legacy-recommendation")
    assert recommendation.candidate_services == ("frontend", "recommendation")
    serialized = registry.model_dump_json().casefold()
    for forbidden in (
        "expected_root",
        "fault_mechanism",
        "expected_runbook",
        "held_out",
        "fault_variant",
    ):
        assert forbidden not in serialized
