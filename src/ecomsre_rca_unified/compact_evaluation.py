"""Frozen admissibility and paired-development aggregate helpers for C1."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import random
import re
from statistics import median

from ecomsre_rca100.statistics import exact_mcnemar_p_value


@dataclass(frozen=True, slots=True)
class AdmissibilityCase:
    source: str
    candidate_count: int
    exact_gt_rank: int | None
    service_gt_rank: int | None
    estimated_b0_input: int
    estimated_c1_input: int
    duplicate_candidate_ids: int
    invalid_refs: int
    allocation_buckets: tuple[str, ...]
    visible_sources: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.source not in {"RCA100", "OBSS"}:
            raise ValueError("admissibility case source is invalid")
        if not 1 <= self.candidate_count <= 12:
            raise ValueError("admissibility candidate count is invalid")
        if self.estimated_b0_input <= 0 or self.estimated_c1_input <= 0:
            raise ValueError("admissibility token estimate is invalid")
        if self.duplicate_candidate_ids < 0 or self.invalid_refs < 0:
            raise ValueError("admissibility integrity count is invalid")


def admissibility_aggregate(
    rows: Sequence[AdmissibilityCase], *, legacy_exact_visible: int
) -> dict[str, object]:
    rca = tuple(item for item in rows if item.source == "RCA100")
    obss = tuple(item for item in rows if item.source == "OBSS")
    if len(rca) != 103 or len(obss) != 60 or legacy_exact_visible < 0:
        raise ValueError("admissibility denominators differ from the frozen contract")
    exact_ranks = tuple(
        item.exact_gt_rank for item in rca if item.exact_gt_rank is not None
    )
    service_ranks = tuple(
        item.service_gt_rank for item in rca if item.service_gt_rank is not None
    )
    obss_ranks = tuple(
        item.service_gt_rank for item in obss if item.service_gt_rank is not None
    )
    b0_mean = sum(item.estimated_b0_input for item in rows) / len(rows)
    c1_mean = sum(item.estimated_c1_input for item in rows) / len(rows)
    ratios = tuple(item.estimated_c1_input / item.estimated_b0_input for item in rows)
    bucket_counts = Counter(
        bucket for item in rows for bucket in item.allocation_buckets
    )
    source_counts = Counter(source for item in rows for source in item.visible_sources)
    checks = {
        "rca100_exact_recall_at_least_60": len(exact_ranks) >= 60,
        "rca100_exact_recall_legacy_plus_15": (
            len(exact_ranks) >= legacy_exact_visible + 15
        ),
        "rca100_service_recall_at_least_80": len(service_ranks) >= 80,
        "obss_root_service_recall_60_of_60": len(obss_ranks) == 60,
        "mean_candidates_at_most_12": (
            sum(item.candidate_count for item in rows) / len(rows) <= 12
        ),
        "max_candidates_at_most_12": max(item.candidate_count for item in rows) <= 12,
        "invalid_refs_zero": sum(item.invalid_refs for item in rows) == 0,
        "duplicate_candidate_ids_zero": (
            sum(item.duplicate_candidate_ids for item in rows) == 0
        ),
        "mean_estimated_token_ratio_at_most_1_15": c1_mean / b0_mean <= 1.15,
    }
    passed = all(checks.values())
    return {
        "schema_version": "compact-retrieval.admissibility.v1",
        "classification": [
            "CONSUMED_DEVELOPMENT_EVALUATION",
            "NO_PROVIDER_RETRIEVAL_AUDIT",
            "ONE_ARCHITECTURE_CANDIDATE",
            "NOT_EXTERNAL_VALIDATION",
        ],
        "rca100": {
            "denominator": 103,
            "exact_gt_recall_at_12": len(exact_ranks),
            "legacy_model_visible_exact": legacy_exact_visible,
            "improvement_over_legacy": len(exact_ranks) - legacy_exact_visible,
            "service_ancestor_recall_at_12": len(service_ranks),
            "exact_gt_median_rank": None if not exact_ranks else median(exact_ranks),
            "service_gt_median_rank": (
                None if not service_ranks else median(service_ranks)
            ),
            "exact_gt_missing": 103 - len(exact_ranks),
        },
        "obss": {
            "denominator": 60,
            "root_service_recall_at_12": len(obss_ranks),
            "median_rank": None if not obss_ranks else median(obss_ranks),
            "missing": 60 - len(obss_ranks),
        },
        "context": {
            "candidate_count": {
                "minimum": min(item.candidate_count for item in rows),
                "maximum": max(item.candidate_count for item in rows),
                "mean": sum(item.candidate_count for item in rows) / len(rows),
            },
            "estimated_input": {
                "b0_mean": b0_mean,
                "c1_mean": c1_mean,
                "c1_to_b0_mean_ratio": c1_mean / b0_mean,
                "maximum_paired_ratio": max(ratios),
                "basis": "CEIL_CANONICAL_REQUEST_UTF8_BYTES_DIV_3",
            },
            "duplicate_candidate_ids": sum(
                item.duplicate_candidate_ids for item in rows
            ),
            "invalid_refs": sum(item.invalid_refs for item in rows),
        },
        "composition": {
            "allocation_bucket_counts": dict(sorted(bucket_counts.items())),
            "visible_source_counts": dict(sorted(source_counts.items())),
        },
        "gate": {
            "checks": checks,
            "passed": passed,
            "verdict": (
                "COMPACT_RETRIEVAL_ADMISSIBILITY_PASSED"
                if passed
                else "COMPACT_RETRIEVAL_ADMISSIBILITY_NOT_PASSED_KEEP_A0"
            ),
        },
    }


@dataclass(frozen=True, slots=True)
class CaseScore:
    opaque_case_id: str
    dataset: str
    b0_root: bool
    c1_root: bool
    b0_service: bool
    c1_service: bool
    b0_fault: bool
    c1_fault: bool
    b0_pair: bool
    c1_pair: bool
    b0_layer: bool
    c1_layer: bool
    b0_ancestor: bool
    c1_ancestor: bool
    b0_descendant: bool
    c1_descendant: bool
    b0_downstream: bool
    c1_downstream: bool

    def __post_init__(self) -> None:
        if not self.opaque_case_id or self.dataset not in {"RCA100", "OBSS"}:
            raise ValueError("case score identity is invalid")


def aggregate_paired_scores(rows: Sequence[CaseScore]) -> dict[str, int]:
    if not rows or len({row.opaque_case_id for row in rows}) != len(rows):
        raise ValueError("paired score rows must be unique and nonempty")
    root_rescue = sum(not row.b0_root and row.c1_root for row in rows)
    root_damage = sum(row.b0_root and not row.c1_root for row in rows)
    pair_rescue = sum(not row.b0_pair and row.c1_pair for row in rows)
    pair_damage = sum(row.b0_pair and not row.c1_pair for row in rows)
    service_rescue = sum(not row.b0_service and row.c1_service for row in rows)
    service_damage = sum(row.b0_service and not row.c1_service for row in rows)
    return {
        "denominator": len(rows),
        "b0_root_correct": sum(row.b0_root for row in rows),
        "c1_root_correct": sum(row.c1_root for row in rows),
        "root_rescue": root_rescue,
        "root_damage": root_damage,
        "root_net_rescue": root_rescue - root_damage,
        "b0_service_root_correct": sum(row.b0_service for row in rows),
        "c1_service_root_correct": sum(row.c1_service for row in rows),
        "service_root_rescue": service_rescue,
        "service_root_damage": service_damage,
        "service_root_net_rescue": service_rescue - service_damage,
        "b0_fault_correct": sum(row.b0_fault for row in rows),
        "c1_fault_correct": sum(row.c1_fault for row in rows),
        "b0_pair_correct": sum(row.b0_pair for row in rows),
        "c1_pair_correct": sum(row.c1_pair for row in rows),
        "pair_rescue": pair_rescue,
        "pair_damage": pair_damage,
        "pair_net_rescue": pair_rescue - pair_damage,
        "entity_layer_mismatch_delta": sum(not row.c1_layer for row in rows)
        - sum(not row.b0_layer for row in rows),
        "b0_ancestor_error": sum(row.b0_ancestor for row in rows),
        "c1_ancestor_error": sum(row.c1_ancestor for row in rows),
        "ancestor_error_delta": sum(row.c1_ancestor for row in rows)
        - sum(row.b0_ancestor for row in rows),
        "b0_descendant_error": sum(row.b0_descendant for row in rows),
        "c1_descendant_error": sum(row.c1_descendant for row in rows),
        "descendant_error_delta": sum(row.c1_descendant for row in rows)
        - sum(row.b0_descendant for row in rows),
        "b0_downstream_symptom_selection": sum(row.b0_downstream for row in rows),
        "c1_downstream_symptom_selection": sum(row.c1_downstream for row in rows),
        "downstream_symptom_selection_delta": sum(row.c1_downstream for row in rows)
        - sum(row.b0_downstream for row in rows),
    }


def paired_development_inference(
    rows: Sequence[CaseScore], *, seed: int, replicates: int = 10_000
) -> dict[str, object]:
    if not rows or replicates != 10_000:
        raise ValueError("development inference requires 10,000 paired replicates")
    deltas = tuple(float(row.c1_root) - float(row.b0_root) for row in rows)
    rng = random.Random(seed)
    denominator = len(rows)
    samples = sorted(
        sum(deltas[rng.randrange(denominator)] for _ in range(denominator))
        / denominator
        for _ in range(replicates)
    )
    damage = sum(row.b0_root and not row.c1_root for row in rows)
    rescue = sum(not row.b0_root and row.c1_root for row in rows)
    return {
        "bootstrap_replicates": replicates,
        "bootstrap_seed": seed,
        "ci_lower": samples[int(0.025 * replicates)],
        "ci_upper": samples[int(0.975 * replicates) - 1],
        "denominator": denominator,
        "mcnemar_exact_p_value": exact_mcnemar_p_value(damage, rescue),
        "point_difference": sum(deltas) / denominator,
    }


def live_tune_gate(
    *,
    rca100: Mapping[str, int],
    obss: Mapping[str, int],
    combined: Mapping[str, int],
    execution: Mapping[str, int],
    input_token_ratio: float | None,
    latency_ratio: float | None,
) -> dict[str, object]:
    checks = {
        "c1_terminalized_163": execution["c1_terminalized"] == 163,
        "c1_completed_at_least_160": execution["c1_completed"] >= 160,
        "c1_invalid_schema_at_most_2": execution["c1_invalid_schema"] <= 2,
        "http_429_zero": execution["http_429"] == 0,
        "privacy_schedule_failure_zero": execution["privacy_schedule_failure"] == 0,
        "rca100_rescue_gt_damage": rca100["root_rescue"] > rca100["root_damage"],
        "rca100_root_net_at_least_three": rca100["root_net_rescue"] >= 3,
        "rca100_root_damage_at_most_two": rca100["root_damage"] <= 2,
        "rca100_c1_root_gt_b0": rca100["c1_root_correct"] > rca100["b0_root_correct"],
        "rca100_service_net_nonnegative": rca100["service_root_net_rescue"] >= 0,
        "rca100_downstream_not_increased": rca100["downstream_symptom_selection_delta"]
        <= 0,
        "rca100_layer_mismatch_not_increased": rca100["entity_layer_mismatch_delta"]
        <= 0,
        "obss_root_net_nonnegative": obss["root_net_rescue"] >= 0,
        "obss_root_damage_at_most_two": obss["root_damage"] <= 2,
        "obss_c1_root_not_lower": obss["c1_root_correct"] >= obss["b0_root_correct"],
        "obss_pair_net_nonnegative": obss["pair_net_rescue"] >= 0,
        "combined_root_net_positive": combined["root_net_rescue"] > 0,
        "invalid_candidate_ids_zero": execution["invalid_candidate_ids"] == 0,
        "mean_model_calls_exactly_one": execution["semantic_model_operations"] == 326,
        "specialist_and_fusion_zero": (
            execution["specialist_calls"] == 0 and execution["fusion_calls"] == 0
        ),
        "input_token_ratio_at_most_1_20": (
            input_token_ratio is not None and input_token_ratio <= 1.20
        ),
        "latency_ratio_at_most_1_30": (
            latency_ratio is not None and latency_ratio <= 1.30
        ),
    }
    passed = all(checks.values())
    return {
        "checks": checks,
        "passed": passed,
        "verdict": (
            "COMPACT_EVIDENCE_RETRIEVAL_LIVE_DEV_PASSED_READY_FOR_MERGE_REVIEW"
            if passed
            else "COMPACT_EVIDENCE_RETRIEVAL_LIVE_DEV_NOT_PASSED_KEEP_A0"
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
        "candidate_mapping",
        "root_cause_entity_ref",
        "raw_provider",
    )
    for path, payload in outputs.items():
        text = payload.decode("utf-8").casefold()
        if any(marker in text for marker in forbidden_literals):
            raise ValueError(f"public leakage marker detected in {path.name}")
        if (
            re.search(r"\bt[0-9]{3}\b", text)
            or re.search(r"\bcase-[0-9a-f]{8,}\b", text)
            or re.search(r"(?:apm|k8s)\|[a-z0-9._-]+\|", text)
        ):
            raise ValueError(f"public case/entity identity detected in {path.name}")


__all__ = [
    "AdmissibilityCase",
    "CaseScore",
    "admissibility_aggregate",
    "aggregate_paired_scores",
    "live_tune_gate",
    "paired_development_inference",
    "scan_public_payloads",
]
