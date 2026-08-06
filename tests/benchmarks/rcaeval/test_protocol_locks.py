from __future__ import annotations

from collections import Counter

from ecomsre_rcaeval.artifacts import read_json_object
from ecomsre_rcaeval.contracts import Architecture
from ecomsre_rcaeval.protocol import (
    CONFIG_ROOT,
    frozen_schedule,
    verify_prompt_lock,
)
from ecomsre_rcaeval.runner import RCAEvalRunLimits
from ecomsre_rcaeval.tools import RCAEvalToolConfig


def test_prompt_model_budget_and_schedule_locks_are_self_consistent() -> None:
    prompt = verify_prompt_lock()
    budget = read_json_object(CONFIG_ROOT / "budget-lock.json")
    holdout_policy = read_json_object(CONFIG_ROOT / "holdout-policy.json")
    protocol = read_json_object(CONFIG_ROOT / "protocol.json")
    scorer = read_json_object(CONFIG_ROOT / "scorer-lock.json")
    statistics = read_json_object(CONFIG_ROOT / "statistics-lock.json")
    schedule = frozen_schedule()

    assert prompt["model"] == "gpt-5.4-mini-2026-03-17"
    assert prompt["temperature"] == 0.0
    assert budget == {
        "schema_version": "rcaeval-re2.budget-lock.v1",
        "model_call_accounting": "actual_provider_model_calls",
        "max_tool_calls": 8,
        "max_model_calls": 8,
        "max_total_tokens": 32_000,
        "model_call_timeout_seconds": 30.0,
        "overall_run_timeout_seconds": 45.0,
        "max_targeted_refinement": 0,
    }
    assert holdout_policy[
        "raw_holdout_access_without_separate_post_b1_authorization"
    ] == "forbidden"
    assert protocol["primary_metric"] == "root_service_ac1"
    assert protocol["main_comparison"] == "dynamic_minus_single_root_service_ac1"
    assert protocol["baseline_policy"] == {
        "baro": {
            "future_disposition": "separate_secondary_analysis_only",
            "included_in_main_experiment": False,
            "primary_inference_eligible": False,
        }
    }
    implementation_freeze = protocol["implementation_freeze"]
    assert implementation_freeze["base_commit"] == (
        "bad8f25ccfbec0ba5d61a40187b40742cb5eec26"
    )
    assert implementation_freeze["expected_scoped_file_count"] == 54
    scoped_paths = implementation_freeze["scoped_paths"]
    assert len(scoped_paths) == 54
    assert scoped_paths == sorted(set(scoped_paths))
    assert "config/rcaeval-re2-v1/protocol.json" in scoped_paths
    assert "src/ecomsre_rcaeval/freeze.py" in scoped_paths
    assert all("phase5b" not in path.lower() for path in scoped_paths)
    assert protocol["bootstrap"]["replicates"] == 10_000
    assert scorer["indicator_mapping"] == protocol["indicator_mapping"]
    assert statistics["primary_metric"] == protocol["primary_metric"]
    assert statistics["main_comparison"] == protocol["main_comparison"]
    assert statistics["external_baseline_policy"] == {
        "baro": "excluded_from_main_experiment_separate_secondary_analysis_only"
    }
    assert statistics["bootstrap"]["replicates"] == protocol["bootstrap"][
        "replicates"
    ]
    limits = RCAEvalRunLimits()
    assert RCAEvalToolConfig().window_seconds == protocol["incident_window_seconds"]
    assert limits.max_tool_calls == budget["max_tool_calls"]
    assert limits.max_model_calls == budget["max_model_calls"]
    assert limits.max_total_tokens == budget["max_total_tokens"]
    assert (
        limits.overall_run_timeout_seconds
        == budget["overall_run_timeout_seconds"]
    )
    assert len(schedule) == 270
    balance = Counter((item.architecture, item.call_position) for item in schedule)
    assert {
        (architecture, position): 30
        for architecture in Architecture
        for position in (1, 2, 3)
    } == balance
