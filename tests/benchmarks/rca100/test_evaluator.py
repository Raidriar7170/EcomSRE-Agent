from __future__ import annotations

from ecomsre_rca100.entity import normalize_entity_name
from ecomsre_rca100.evaluator import fault_correct, parse_ground_truth
from ecomsre_rca100.statistics import exact_mcnemar_p_value, paired_inference


def test_ground_truth_parser_prefers_structured_outcome_ids() -> None:
    truth = parse_ground_truth(
        "t001",
        "F1-latency.synthetic",
        {
            "root_cause_entities": ["fallback-name"],
            "root_cause_types": ["Network Latency"],
            "raw_ground_truth": {
                "outcome": {
                    "target_entity_ids": ["svc-a"],
                    "target_entities": [
                        {"entity_id": "svc-b", "entity_name": "Service B"}
                    ],
                }
            },
        },
    )

    assert truth.target_entity_ids == ("svc-a", "svc-b")
    assert truth.target_entity_names == ("Service B",)
    assert fault_correct(" network latency ", truth)
    assert normalize_entity_name("  SÉRVICE   A ") == "sérvice a"


def test_paired_inference_is_deterministic_and_uses_exact_mcnemar() -> None:
    initial = (True, False, False, True)
    final = (True, True, False, False)

    first = paired_inference(initial, final)
    second = paired_inference(initial, final)

    assert first == second
    assert first.damage == 1
    assert first.rescue == 1
    assert first.net_rescue == 0
    assert first.point_difference == 0.0
    assert first.mcnemar_exact_p_value == 1.0
    assert exact_mcnemar_p_value(0, 3) == 0.25
    assert first.classification == "RCA100_EXTERNAL_M3_NOT_SUPPORTED"
