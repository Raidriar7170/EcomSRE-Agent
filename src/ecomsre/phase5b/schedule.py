"""Build the immutable 180-run Phase 5B paired execution schedule."""

from __future__ import annotations

from collections import Counter
import hashlib
from itertools import permutations

from ecomsre.phase5b.contracts import (
    ExecutionSchedule,
    ScheduledRun,
    SeedPolicy,
    SuiteRegistry,
    VariantName,
)
from ecomsre.phase5b.seeds import seed_material, variant_order_hash


_VARIANTS: tuple[VariantName, ...] = (
    "SINGLE_AGENT_V2",
    "FIXED_SPECIALIST_V2",
    "DYNAMIC_MULTI_AGENT_V2",
)
_PERMUTATIONS = tuple(permutations(_VARIANTS))


def _short_hash(*parts: str) -> str:
    return hashlib.sha256(b"\0".join(part.encode() for part in parts)).hexdigest()[:32]


def build_execution_schedule(
    suite: SuiteRegistry,
    seed_policy: SeedPolicy,
) -> ExecutionSchedule:
    if suite.evaluation_version != seed_policy.evaluation_version:
        raise ValueError("suite and seed policy evaluation versions differ")
    templates = tuple(
        item.template_id for item in suite.public_anchors + suite.hidden_slots
    )
    units = [
        (
            template_id,
            seed_id,
            variant_order_hash(suite.evaluation_version, template_id, seed_id),
        )
        for template_id in templates
        for seed_id in seed_policy.seed_ids
    ]
    permutation_by_unit = {
        (template_id, seed_id): _PERMUTATIONS[index % len(_PERMUTATIONS)]
        for index, (template_id, seed_id, _) in enumerate(
            sorted(units, key=lambda item: item[2])
        )
    }
    runs: list[ScheduledRun] = []
    for template_id, seed_id, order_sha256 in units:
        pairing_unit_id = _short_hash(
            suite.evaluation_version, template_id, seed_id, "pairing-unit"
        )
        instance_seed = seed_material(
            suite.evaluation_version, template_id, seed_id
        )
        order = permutation_by_unit[(template_id, seed_id)]
        for position, variant in enumerate(order, start=1):
            runs.append(
                ScheduledRun(
                    run_id=_short_hash(
                        suite.evaluation_version,
                        template_id,
                        seed_id,
                        variant,
                    ),
                    pairing_unit_id=pairing_unit_id,
                    template_id=template_id,
                    seed_id=seed_id,
                    seed_material_sha256=instance_seed,
                    variant_order_sha256=order_sha256,
                    variant=variant,
                    call_position=position,
                )
            )
    balance = Counter((item.variant, item.call_position) for item in runs)
    balance_payload: dict[str, tuple[int, int, int]] = {
        variant: (
            balance[(variant, 1)],
            balance[(variant, 2)],
            balance[(variant, 3)],
        )
        for variant in _VARIANTS
    }
    return ExecutionSchedule(
        schema_version="phase5b.execution-schedule.v1",
        evaluation_version=suite.evaluation_version,
        variant_order_method="ranked_hash_round_robin_six_permutations",
        pairing_unit_count=60,
        run_count=180,
        provider_pacing_seconds=2,
        hidden_retry=False,
        scripted_fallback=False,
        runs=tuple(runs),
        call_position_balance=balance_payload,
    )
