"""Deterministic 12-run synthetic protocol rehearsal with no Provider access."""

from __future__ import annotations

from collections import Counter
import hashlib
from itertools import permutations
from typing import Any

from ecomsre.phase5b.analysis import AnalysisRun, analyze_mock_population
from ecomsre.phase5b.contracts import VariantName
from ecomsre.phase5b.worker import run_mock_worker


_TEMPLATES = ("synthetic-template-a", "synthetic-template-b")
_SEEDS = ("synthetic-seed-00", "synthetic-seed-01")
_VARIANTS: tuple[VariantName, ...] = (
    "SINGLE_AGENT_V2",
    "FIXED_SPECIALIST_V2",
    "DYNAMIC_MULTI_AGENT_V2",
)
_PERMUTATIONS = tuple(permutations(_VARIANTS))
_MOCK_BALANCED_PERMUTATIONS = tuple(_PERMUTATIONS[index] for index in (0, 1, 2, 5))


def _identifier(*parts: str) -> str:
    return hashlib.sha256(b"\0".join(part.encode("utf-8") for part in parts)).hexdigest()[:32]


def build_mock_dry_run_report() -> dict[str, Any]:
    schedule: list[dict[str, object]] = []
    results: list[dict[str, object]] = []
    analysis_runs: list[AnalysisRun] = []
    balance: Counter[tuple[str, int]] = Counter()
    for pair_index, (template_id, seed_id) in enumerate(
        (template, seed) for template in _TEMPLATES for seed in _SEEDS
    ):
        instance_id = _identifier("phase5b.mock.v1", template_id, seed_id)
        expected = "RCA_CONFIRMED" if template_id.endswith("a") else "ABSTAIN"
        order = _MOCK_BALANCED_PERMUTATIONS[pair_index]
        for call_position, variant in enumerate(order, start=1):
            run_id = _identifier(instance_id, variant)
            visible: dict[str, str | bool] = {
                "synthetic_decision_signal": expected,
                "synthetic_observation": f"opaque-observation-{pair_index}",
            }
            if pair_index == 3 and variant == "SINGLE_AGENT_V2":
                visible["inject_terminal_failure"] = True
            request: dict[str, object] = {
                "instance_id": instance_id,
                "variant": variant,
                "agent_visible": visible,
            }
            worker_result = run_mock_worker(request)
            correct = worker_result.decision == expected
            schedule.append(
                {
                    "call_position": call_position,
                    "instance_id": instance_id,
                    "run_id": run_id,
                    "variant": variant,
                }
            )
            results.append(
                {
                    "decision_correct": correct and worker_result.failure_code is None,
                    "failure_code": worker_result.failure_code,
                    "instance_id": instance_id,
                    "run_id": run_id,
                    "terminal_status": worker_result.terminal_status,
                    "tool_calls": worker_result.tool_calls,
                    "variant": variant,
                }
            )
            analysis_runs.append(
                AnalysisRun(
                    run_id=run_id,
                    template_id=template_id,
                    seed_id=seed_id,
                    population="SYNTHETIC",
                    variant=variant,
                    decision_correct=correct,
                    tool_calls=worker_result.tool_calls,
                    failure_code=worker_result.failure_code,
                )
            )
            balance[(variant, call_position)] += 1
    return {
        "schema_version": "phase5b.mock-protocol-dry-run.v1",
        "evaluation_version": "phase5b.v1",
        "report_type": "MOCK_PROTOCOL_DRY_RUN",
        "evidence_class": "NOT_MODEL_EVIDENCE",
        "provider_call_count": 0,
        "actual_hidden_pack_used": False,
        "ground_truth_read": False,
        "template_count": 2,
        "seed_count_per_template": 2,
        "variant_count": 3,
        "run_count": 12,
        "failure_denominator_count": 1,
        "call_position_balance": {
            variant: [balance[(variant, position)] for position in (1, 2, 3)]
            for variant in _VARIANTS
        },
        "schedule": schedule,
        "results": results,
        "analysis_views": analyze_mock_population(tuple(analysis_runs)),
        "superiority_claim": False,
        "state_trace": [
            "PROTOCOL_FROZEN",
            "HIDDEN_PACK_SEALED",
            "EXECUTION_STARTED",
            "EXECUTION_COMPLETE",
            "UNBLINDED",
            "FINAL_REPORT_FROZEN",
        ],
    }
