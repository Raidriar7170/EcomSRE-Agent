"""Provider-free fixed-trajectory Full versus Salient memory benchmark."""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any, Literal

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.v22.memory import (
    BaselineProfileV22,
    RuntimeObservationDetailV22,
    build_memory_views_v22,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, Sha256V22, semantic_sha256_v22
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22


class MemoryTrajectoryCostV22(DtaModelV22):
    schema_version: Literal["dta-v22.memory-trajectory-cost.v1"]
    representation: Literal["FULL_MEMORY", "SALIENT_MEMORY"]
    action_ids: tuple[str, ...]
    turn_count: StrictInt = Field(ge=1)
    serialized_bytes_by_turn: tuple[StrictInt, ...]
    estimated_tokens_by_turn: tuple[StrictInt, ...]
    cumulative_serialized_bytes: StrictInt = Field(ge=1)
    cumulative_estimated_tokens: StrictInt = Field(ge=1)
    cost_sha256: Sha256V22

    @model_validator(mode="after")
    def require_cost(self) -> MemoryTrajectoryCostV22:
        if not (
            self.turn_count
            == len(self.action_ids)
            == len(self.serialized_bytes_by_turn)
            == len(self.estimated_tokens_by_turn)
        ):
            raise ValueError("memory trajectory turn counts differ")
        if self.cumulative_serialized_bytes != sum(self.serialized_bytes_by_turn):
            raise ValueError("memory trajectory byte total differs")
        if self.cumulative_estimated_tokens != sum(self.estimated_tokens_by_turn):
            raise ValueError("memory trajectory token total differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"cost_sha256"})
        )
        if self.cost_sha256 != expected:
            raise ValueError("memory trajectory cost digest differs")
        return self


class FixedTrajectoryMemoryBenchmarkV22(DtaModelV22):
    schema_version: Literal["dta-v22.fixed-trajectory-memory-benchmark.v1"]
    provider_calls: Literal[0]
    baseline_sha256: Sha256V22
    full: MemoryTrajectoryCostV22
    salient: MemoryTrajectoryCostV22
    report_sha256: Sha256V22

    @model_validator(mode="after")
    def require_report(self) -> FixedTrajectoryMemoryBenchmarkV22:
        if self.full.action_ids != self.salient.action_ids:
            raise ValueError("fixed trajectory actions differ by representation")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("fixed trajectory benchmark digest differs")
        return self


def _cost(
    *,
    representation: Literal["FULL_MEMORY", "SALIENT_MEMORY"],
    action_ids: tuple[str, ...],
    byte_counts: tuple[int, ...],
) -> MemoryTrajectoryCostV22:
    token_counts = tuple(math.ceil(item / 4) for item in byte_counts)
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.memory-trajectory-cost.v1",
        "representation": representation,
        "action_ids": action_ids,
        "turn_count": len(action_ids),
        "serialized_bytes_by_turn": byte_counts,
        "estimated_tokens_by_turn": token_counts,
        "cumulative_serialized_bytes": sum(byte_counts),
        "cumulative_estimated_tokens": sum(token_counts),
    }
    draft = MemoryTrajectoryCostV22.model_construct(**payload, cost_sha256="0" * 64)
    return MemoryTrajectoryCostV22.model_validate(
        {
            **payload,
            "cost_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"cost_sha256"})
            ),
        }
    )


def benchmark_fixed_trajectory_v22(
    *,
    outcomes: tuple[ReadOutcomeV22, ...],
    runtime_details: tuple[RuntimeObservationDetailV22, ...],
    baseline: BaselineProfileV22,
    observed_at: datetime,
    top_k: int,
) -> FixedTrajectoryMemoryBenchmarkV22:
    """Serialize every fixed prefix twice without a Provider or action change."""

    if not outcomes:
        raise ValueError("fixed trajectory benchmark requires at least one outcome")
    action_ids = tuple(item.action_id for item in outcomes)
    full_bytes: list[int] = []
    salient_bytes: list[int] = []
    for turn in range(1, len(outcomes) + 1):
        prefix = outcomes[:turn]
        prefix_outcome_sha256 = {item.outcome_sha256 for item in prefix}
        salient, full = build_memory_views_v22(
            outcomes=prefix,
            runtime_details=tuple(
                item
                for item in runtime_details
                if item.outcome_sha256 in prefix_outcome_sha256
            ),
            baseline=baseline,
            observed_at=observed_at,
            top_k=top_k,
        )
        full_bytes.append(len(full.model_dump_json().encode("utf-8")))
        salient_bytes.append(len(salient.model_dump_json().encode("utf-8")))
    full_cost = _cost(
        representation="FULL_MEMORY",
        action_ids=action_ids,
        byte_counts=tuple(full_bytes),
    )
    salient_cost = _cost(
        representation="SALIENT_MEMORY",
        action_ids=action_ids,
        byte_counts=tuple(salient_bytes),
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.fixed-trajectory-memory-benchmark.v1",
        "provider_calls": 0,
        "baseline_sha256": baseline.baseline_sha256,
        "full": full_cost,
        "salient": salient_cost,
    }
    draft = FixedTrajectoryMemoryBenchmarkV22.model_construct(
        **payload,
        report_sha256="0" * 64,
    )
    return FixedTrajectoryMemoryBenchmarkV22.model_validate(
        {
            **payload,
            "report_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )


__all__ = (
    "FixedTrajectoryMemoryBenchmarkV22",
    "MemoryTrajectoryCostV22",
    "benchmark_fixed_trajectory_v22",
)
