"""Paired aggregate helpers for the consumed-development live comparison."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import random
import re

from ecomsre_rca100.statistics import exact_mcnemar_p_value


@dataclass(frozen=True, slots=True)
class CaseScore:
    opaque_case_id: str
    dataset: str
    b0_root: bool
    h1_root: bool
    b0_service: bool
    h1_service: bool
    b0_fault: bool
    h1_fault: bool
    b0_pair: bool
    h1_pair: bool
    b0_layer: bool
    h1_layer: bool
    b0_ancestor: bool
    h1_ancestor: bool
    b0_descendant: bool
    h1_descendant: bool
    b0_downstream: bool
    h1_downstream: bool

    def __post_init__(self) -> None:
        if not self.opaque_case_id or self.dataset not in {"RCA100", "OBSS"}:
            raise ValueError("case score identity is invalid")


def aggregate_paired_scores(rows: Sequence[CaseScore]) -> dict[str, int]:
    if not rows or len({row.opaque_case_id for row in rows}) != len(rows):
        raise ValueError("paired score rows must be unique and nonempty")
    root_rescue = sum(not row.b0_root and row.h1_root for row in rows)
    root_damage = sum(row.b0_root and not row.h1_root for row in rows)
    pair_rescue = sum(not row.b0_pair and row.h1_pair for row in rows)
    pair_damage = sum(row.b0_pair and not row.h1_pair for row in rows)
    service_rescue = sum(not row.b0_service and row.h1_service for row in rows)
    service_damage = sum(row.b0_service and not row.h1_service for row in rows)
    return {
        "denominator": len(rows),
        "b0_root_correct": sum(row.b0_root for row in rows),
        "h1_root_correct": sum(row.h1_root for row in rows),
        "root_rescue": root_rescue,
        "root_damage": root_damage,
        "root_net_rescue": root_rescue - root_damage,
        "b0_service_root_correct": sum(row.b0_service for row in rows),
        "h1_service_root_correct": sum(row.h1_service for row in rows),
        "service_root_rescue": service_rescue,
        "service_root_damage": service_damage,
        "service_root_net_rescue": service_rescue - service_damage,
        "b0_fault_correct": sum(row.b0_fault for row in rows),
        "h1_fault_correct": sum(row.h1_fault for row in rows),
        "b0_pair_correct": sum(row.b0_pair for row in rows),
        "h1_pair_correct": sum(row.h1_pair for row in rows),
        "pair_rescue": pair_rescue,
        "pair_damage": pair_damage,
        "pair_net_rescue": pair_rescue - pair_damage,
        "entity_layer_mismatch_delta": sum(not row.h1_layer for row in rows)
        - sum(not row.b0_layer for row in rows),
        "b0_ancestor_error": sum(row.b0_ancestor for row in rows),
        "h1_ancestor_error": sum(row.h1_ancestor for row in rows),
        "ancestor_error_delta": sum(row.h1_ancestor for row in rows)
        - sum(row.b0_ancestor for row in rows),
        "b0_descendant_error": sum(row.b0_descendant for row in rows),
        "h1_descendant_error": sum(row.h1_descendant for row in rows),
        "descendant_error_delta": sum(row.h1_descendant for row in rows)
        - sum(row.b0_descendant for row in rows),
        "b0_downstream_symptom_selection": sum(row.b0_downstream for row in rows),
        "h1_downstream_symptom_selection": sum(row.h1_downstream for row in rows),
        "downstream_symptom_selection_delta": sum(row.h1_downstream for row in rows)
        - sum(row.b0_downstream for row in rows),
    }


def paired_development_inference(
    rows: Sequence[CaseScore], *, seed: int, replicates: int = 10_000
) -> dict[str, object]:
    if not rows or replicates != 10_000:
        raise ValueError("development inference requires 10,000 paired replicates")
    deltas = tuple(float(row.h1_root) - float(row.b0_root) for row in rows)
    rng = random.Random(seed)
    denominator = len(rows)
    samples = sorted(
        sum(deltas[rng.randrange(denominator)] for _ in range(denominator))
        / denominator
        for _ in range(replicates)
    )
    damage = sum(row.b0_root and not row.h1_root for row in rows)
    rescue = sum(not row.b0_root and row.h1_root for row in rows)
    return {
        "bootstrap_replicates": replicates,
        "bootstrap_seed": seed,
        "ci_lower": samples[int(0.025 * replicates)],
        "ci_upper": samples[int(0.975 * replicates) - 1],
        "denominator": denominator,
        "mcnemar_exact_p_value": exact_mcnemar_p_value(damage, rescue),
        "point_difference": sum(deltas) / denominator,
    }


def tune_gate(
    *,
    rca100: dict[str, int],
    obss: dict[str, int],
    combined: dict[str, int],
    execution: dict[str, int],
    h1_input_token_ratio: float | None,
    h1_latency_ratio: float | None,
) -> dict[str, object]:
    checks = {
        "rca100_completed_b0": execution["rca100_completed_b0"] >= 98,
        "rca100_completed_h1": execution["rca100_completed_h1"] >= 98,
        "obss_completed_b0": execution["obss_completed_b0"] >= 58,
        "obss_completed_h1": execution["obss_completed_h1"] >= 58,
        "http_429_zero": execution["http_429"] == 0,
        "schema_privacy_schedule_failure_zero": (
            execution["schema_privacy_schedule_failure"] == 0
        ),
        "rca100_rescue_gt_damage": (
            rca100["root_rescue"] > rca100["root_damage"]
        ),
        "rca100_root_net_at_least_three": rca100["root_net_rescue"] >= 3,
        "rca100_root_damage_at_most_two": rca100["root_damage"] <= 2,
        "rca100_h1_root_not_lower": (
            rca100["h1_root_correct"] >= rca100["b0_root_correct"]
        ),
        "rca100_service_net_nonnegative": (
            rca100["service_root_net_rescue"] >= 0
        ),
        "rca100_downstream_not_increased": (
            rca100["downstream_symptom_selection_delta"] <= 0
        ),
        "rca100_layer_mismatch_not_increased": (
            rca100["entity_layer_mismatch_delta"] <= 0
        ),
        "obss_root_net_nonnegative": obss["root_net_rescue"] >= 0,
        "obss_root_damage_at_most_two": obss["root_damage"] <= 2,
        "obss_pair_net_nonnegative": obss["pair_net_rescue"] >= 0,
        "combined_root_net_positive": combined["root_net_rescue"] > 0,
        "mean_model_calls_exactly_one": execution["semantic_model_operations"]
        == execution["terminal_count"],
        "specialist_and_fusion_zero": (
            execution["specialist_calls"] == 0 and execution["fusion_calls"] == 0
        ),
        "input_token_ratio": (
            h1_input_token_ratio is not None and h1_input_token_ratio <= 1.35
        ),
        "latency_ratio": (
            h1_latency_ratio is not None and h1_latency_ratio <= 1.40
        ),
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "verdict": (
            "TUNE_GATE_PASSED"
            if all(checks.values())
            else "HIERARCHICAL_STRONG_SINGLE_LIVE_TUNE_NOT_PASSED"
        ),
    }


def regression_gate(
    *,
    aggregate: dict[str, int],
    execution: dict[str, int],
    h1_input_token_ratio: float | None,
    h1_latency_ratio: float | None,
) -> dict[str, object]:
    checks = {
        "completed_b0": execution["completed_b0"] >= 114,
        "completed_h1": execution["completed_h1"] >= 114,
        "http_429_at_most_two": execution["http_429"] <= 2,
        "schema_privacy_schedule_failure_zero": (
            execution["schema_privacy_schedule_failure"] == 0
        ),
        "h1_root_not_lower": (
            aggregate["h1_root_correct"] >= aggregate["b0_root_correct"]
        ),
        "root_rescue_not_lower_than_damage": (
            aggregate["root_rescue"] >= aggregate["root_damage"]
        ),
        "root_net_nonnegative": aggregate["root_net_rescue"] >= 0,
        "root_damage_at_most_two": aggregate["root_damage"] <= 2,
        "h1_pair_not_lower": (
            aggregate["h1_pair_correct"] >= aggregate["b0_pair_correct"]
        ),
        "pair_rescue_not_lower_than_damage": (
            aggregate["pair_rescue"] >= aggregate["pair_damage"]
        ),
        "pair_net_nonnegative": aggregate["pair_net_rescue"] >= 0,
        "mean_model_calls_exactly_one": execution["semantic_model_operations"]
        == execution["admitted_arms"],
        "specialist_and_fusion_zero": (
            execution["specialist_calls"] == 0 and execution["fusion_calls"] == 0
        ),
        "input_token_ratio": (
            h1_input_token_ratio is not None and h1_input_token_ratio <= 1.35
        ),
        "latency_ratio": (
            h1_latency_ratio is not None and h1_latency_ratio <= 1.40
        ),
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "verdict": (
            "HIERARCHICAL_STRONG_SINGLE_LIVE_DEV_PASSED_READY_FOR_EXTERNAL_PLANNING"
            if all(checks.values())
            else "HIERARCHICAL_STRONG_SINGLE_REGRESSION_NOT_PASSED"
        ),
    }


def scan_public_payloads(outputs: Mapping[Path, bytes]) -> None:
    forbidden_literals = (
        "source_key",
        "source_task_id",
        "opaque_case_id",
        "run_id",
        "answer_root",
        "private_root",
        "/users/",
        "ecomsre_llm_",
        "api_key",
        "base_url",
    )
    for path, payload in outputs.items():
        text = payload.decode("utf-8").casefold()
        if any(marker in text for marker in forbidden_literals):
            raise ValueError(f"public leakage marker detected in {path.name}")
        if re.search(r"\bt[0-9]{3}\b", text) or re.search(
            r"\bcase-[0-9a-f]{8,}\b", text
        ):
            raise ValueError(f"public case identity detected in {path.name}")


__all__ = [
    "CaseScore",
    "aggregate_paired_scores",
    "paired_development_inference",
    "regression_gate",
    "scan_public_payloads",
    "tune_gate",
]
