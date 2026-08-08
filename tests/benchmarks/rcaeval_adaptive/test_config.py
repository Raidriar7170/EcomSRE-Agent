from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "config/rcaeval-adaptive-v1"


def _load(name: str) -> dict:
    return json.loads((CONFIG / name).read_text(encoding="utf-8"))


def test_agent_config_locks_single_first_routes_and_exact_costs() -> None:
    agent = _load("agent.json")

    assert agent["evaluation_version"] == "single-first-adaptive-v1"
    assert agent["initial_sources"] == ["metrics", "logs"]
    assert agent["gate"] == {
        "direct_confidence_threshold": 0.75,
        "low_confidence_threshold": 0.55,
        "metrics_margin_threshold": 0.25,
    }
    assert agent["route_costs"] == {
        "DIRECT_RETURN": {"semantic_operations": 1, "tool_calls": 2},
        "ESCALATE_LOGS": {"semantic_operations": 3, "tool_calls": 2},
        "ESCALATE_TRACES": {"semantic_operations": 3, "tool_calls": 3},
        "ESCALATE_BOTH": {"semantic_operations": 4, "tool_calls": 3},
    }
    assert agent["ss_trace_policy"] == "UNAVAILABLE"


def test_evaluation_config_locks_candidate_design_and_validation_gates() -> None:
    evaluation = _load("evaluation.json")

    assert evaluation["candidate_limit"] == 3
    assert evaluation["smoke_cases"] == 12
    assert evaluation["design_cases"] == 60
    assert evaluation["dev_validation_cases"] == 120
    assert evaluation["design_minimum_gate"] == {
        "completion_min": 58,
        "root_service_correct_min": 50,
        "pair_correct_min": 28,
        "damage_max": 3,
        "rescue_strictly_greater_than_damage": True,
        "direct_return_min": 24,
        "mean_semantic_operations_max": 3.0,
        "privacy_schema_schedule_failure_max": 0,
    }
    assert evaluation["damage_rescue_endpoint"] == "root_cause_pair_ac_at_1"
    assert evaluation["phase_budgets"]["validation"]["semantic_operations"] == 600
    assert evaluation["phase_budgets"]["validation"]["provider_attempts"] == 1200


def test_model_lock_reuses_exact_dev3_provider_and_f0_contracts() -> None:
    model = _load("model-lock.json")

    assert model["model"] == "gpt-5.4-mini-2026-03-17"
    assert model["timeout_seconds"] == 30.0
    assert model["max_completion_tokens"] == 2048
    assert model["semantic_retry"] == "FORBIDDEN"
    assert model["schema_retry"] == "FORBIDDEN"
    assert model["fallback"] == "NO_FALLBACK"
    assert model["selected_indicator_formula"] == "F0"
    assert model["transport_retry_policy_sha256"] == (
        "7fd010103f83a1cb99b0c478ddafdf6e9fd0dc349a4297e7bb55c9b4157c202b"
    )
