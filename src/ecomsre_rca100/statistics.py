"""Frozen paired inference for the 103-case RCA100 endpoint."""

from __future__ import annotations

from math import comb
import random
from typing import Literal

from pydantic import Field, StrictFloat, StrictInt

from ecomsre_rca100.contracts import RCA100Model


BOOTSTRAP_REPLICATES: Literal[10000] = 10_000
BOOTSTRAP_SEED: Literal[20260810] = 20260810


class RCA100PairedInference(RCA100Model):
    schema_version: Literal["rca100.paired-inference.v1"] = (
        "rca100.paired-inference.v1"
    )
    denominator: StrictInt = Field(ge=1)
    initial_correct: StrictInt = Field(ge=0)
    final_correct: StrictInt = Field(ge=0)
    damage: StrictInt = Field(ge=0)
    damage_rate_denominator: StrictInt = Field(ge=0)
    damage_rate: StrictFloat = Field(ge=0.0, le=1.0)
    rescue: StrictInt = Field(ge=0)
    net_rescue: int
    point_difference: StrictFloat
    ci_lower: StrictFloat
    ci_upper: StrictFloat
    bootstrap_replicates: Literal[10000] = BOOTSTRAP_REPLICATES
    bootstrap_seed: Literal[20260810] = BOOTSTRAP_SEED
    mcnemar_exact_p_value: StrictFloat = Field(ge=0.0, le=1.0)
    classification: Literal[
        "RCA100_EXTERNAL_M3_SUPERIORITY_SUPPORTED",
        "RCA100_EXTERNAL_M3_POSITIVE_INCONCLUSIVE",
        "RCA100_EXTERNAL_M3_NOT_SUPPORTED",
    ]


def exact_mcnemar_p_value(damage: int, rescue: int) -> float:
    discordant = damage + rescue
    if discordant == 0:
        return 1.0
    smaller = min(damage, rescue)
    tail = sum(comb(discordant, index) for index in range(smaller + 1))
    return min(1.0, 2.0 * tail / (2**discordant))


def paired_inference(
    initial: tuple[bool, ...], final: tuple[bool, ...]
) -> RCA100PairedInference:
    if not initial or len(initial) != len(final):
        raise ValueError("paired inference requires equal nonempty vectors")
    denominator = len(initial)
    deltas = tuple(float(after) - float(before) for before, after in zip(initial, final))
    rng = random.Random(BOOTSTRAP_SEED)
    replicates = sorted(
        sum(deltas[rng.randrange(denominator)] for _ in range(denominator))
        / denominator
        for _ in range(BOOTSTRAP_REPLICATES)
    )
    lower = replicates[int(0.025 * BOOTSTRAP_REPLICATES)]
    upper = replicates[int(0.975 * BOOTSTRAP_REPLICATES) - 1]
    damage = sum(before and not after for before, after in zip(initial, final))
    rescue = sum(not before and after for before, after in zip(initial, final))
    point = sum(deltas) / denominator
    if point > 0 and lower > 0:
        classification: Literal[
            "RCA100_EXTERNAL_M3_SUPERIORITY_SUPPORTED",
            "RCA100_EXTERNAL_M3_POSITIVE_INCONCLUSIVE",
            "RCA100_EXTERNAL_M3_NOT_SUPPORTED",
        ] = "RCA100_EXTERNAL_M3_SUPERIORITY_SUPPORTED"
    elif point > 0:
        classification = "RCA100_EXTERNAL_M3_POSITIVE_INCONCLUSIVE"
    else:
        classification = "RCA100_EXTERNAL_M3_NOT_SUPPORTED"
    return RCA100PairedInference(
        denominator=denominator,
        initial_correct=sum(initial),
        final_correct=sum(final),
        damage=damage,
        damage_rate_denominator=sum(initial),
        damage_rate=(damage / sum(initial)) if any(initial) else 0.0,
        rescue=rescue,
        net_rescue=rescue - damage,
        point_difference=point,
        ci_lower=lower,
        ci_upper=upper,
        mcnemar_exact_p_value=exact_mcnemar_p_value(damage, rescue),
        classification=classification,
    )


__all__ = [
    "BOOTSTRAP_REPLICATES",
    "BOOTSTRAP_SEED",
    "RCA100PairedInference",
    "exact_mcnemar_p_value",
    "paired_inference",
]
