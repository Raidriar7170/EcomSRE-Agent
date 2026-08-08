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
        "cross_source_conflict_blocks_direct": False,
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

    assert evaluation["evaluation_version"] == "single-first-adaptive-v1"
    assert evaluation["run_domain"] == (
        "single-first-adaptive-v1-fusion-guardrail-r1"
    )
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


def test_historical_smoke_aggregate_hashes_remain_preserved() -> None:
    public_result = json.loads(
        (ROOT / "docs/results/rcaeval-single-first-adaptive-v1.json").read_text(
            encoding="utf-8"
        )
    )

    pre_fix = public_result["pre_fix_initial_interface_failure"]
    assert pre_fix["terminal_aggregate_sha256"] == [
        "d54425e492166e633703c3b84f5907c9f57ce6269ecc51f1603bcfe86aadde86",
        "3640482694a51dc95e003d77892b8d0892b82d5726eb001480f20ac216bdc1ed",
        "a81fec9cbfd47d282ac3b3093964a286b0665d8b5ae2999e4993da448b1d0b4d",
    ]
    assert pre_fix["sidecar_aggregate_sha256"] == [
        "eb35189e5a67ea07ccc965422d45d4bb2f8127c30ca9053f54353f72b1c4b86d",
        "c4eca62b25c4773698c0e4b1faebb19047813dcc27dafa0470a43f39a53ed949",
        "a3e94ef1e7113236c0cec51f2f38324f372e9b31432be468463716389a1808cb",
    ]
    initial_repair = public_result["initial_interface_fix_r1_downstream_failure"]
    assert initial_repair["terminal_aggregate_sha256"] == (
        "75745728f1678683e55a3d7b0b2183fbb6c5bc503d6db3ab7d6945b0152cfaa9"
    )
    assert initial_repair["sidecar_aggregate_sha256"] == (
        "0db58fa1a153f58329bcdb633c21d3ea0826331f32e99d67910a81a9ec5e2307"
    )
    downstream_rounds = public_result["downstream_interface_repair"]["rounds"]
    assert [item["terminal_aggregate_sha256"] for item in downstream_rounds] == [
        "f1689af57f3b514d86b640a0644233eb3560b5850404197bba13c53d401cbc44",
        "ec6765cd89c71a325642bcf1d7613b6b6703d18efb666b19ee683f45f895f44b",
    ]
    assert [item["sidecar_aggregate_sha256"] for item in downstream_rounds] == [
        "1422b45fedb6b135e077ac9ffe56b7c5bd8d69c98f462fde216d76e30e8863fe",
        "901977efef759d747eca49a5d789bdb8923380a1615a7d145b90f34c452ae645",
    ]
