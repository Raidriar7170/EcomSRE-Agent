"""Official-semantics Top-1 scoring with failure retention."""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt

from ecomsre_rcaeval.contracts import (
    Architecture,
    CanonicalIndicator,
    FaultName,
    GroundTruth,
    RCAEvalModel,
    TerminalRecord,
    TerminalStatus,
)


_INDICATOR_BY_FAULT: dict[FaultName, CanonicalIndicator] = {
    "cpu": "cpu",
    "mem": "mem",
    "disk": "diskio",
    "delay": "latency",
    "loss": "latency",
    "socket": "socket",
}


def normalize_indicator(fault: FaultName) -> CanonicalIndicator:
    return _INDICATOR_BY_FAULT[fault]


class ScoredCase(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.scored-case.v1"] = (
        "rcaeval-re2.scored-case.v1"
    )
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    case_id: str
    architecture: Architecture
    terminal_status: TerminalStatus
    root_service_correct: StrictBool
    root_cause_pair_correct: StrictBool
    tool_calls: StrictInt = Field(ge=0)


class ArchitectureSummary(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.architecture-summary.v1"] = (
        "rcaeval-re2.architecture-summary.v1"
    )
    architecture: Architecture
    denominator: StrictInt = Field(ge=0)
    root_service_correct: StrictInt = Field(ge=0)
    root_cause_pair_correct: StrictInt = Field(ge=0)
    terminal_failures: StrictInt = Field(ge=0)
    root_service_ac1: StrictFloat = Field(ge=0.0, le=1.0)
    root_cause_pair_ac1: StrictFloat = Field(ge=0.0, le=1.0)


def score_terminal_records(
    records: tuple[TerminalRecord, ...],
    truth_by_case: dict[str, GroundTruth],
) -> tuple[tuple[ScoredCase, ...], dict[Architecture, ArchitectureSummary]]:
    if len({item.run_id for item in records}) != len(records):
        raise ValueError("duplicate terminal run identifier")
    unknown = {item.case_id for item in records} - set(truth_by_case)
    if unknown:
        raise ValueError("terminal record has no evaluator-only Ground Truth")
    scored: list[ScoredCase] = []
    grouped: dict[Architecture, list[ScoredCase]] = defaultdict(list)
    for record in records:
        truth = truth_by_case[record.case_id]
        service_correct = False
        pair_correct = False
        if record.terminal_status is TerminalStatus.COMPLETED:
            assert record.diagnosis is not None
            service_correct = (
                record.diagnosis.root_cause_service == truth.root_cause_service
            )
            pair_correct = service_correct and (
                record.diagnosis.root_cause_indicator
                == normalize_indicator(truth.fault)
            )
        item = ScoredCase(
            run_id=record.run_id,
            case_id=record.case_id,
            architecture=record.architecture,
            terminal_status=record.terminal_status,
            root_service_correct=service_correct,
            root_cause_pair_correct=pair_correct,
            tool_calls=record.tool_calls,
        )
        scored.append(item)
        grouped[item.architecture].append(item)
    summaries: dict[Architecture, ArchitectureSummary] = {}
    for architecture, items in grouped.items():
        denominator = len(items)
        service_correct_count = sum(item.root_service_correct for item in items)
        pair_correct_count = sum(item.root_cause_pair_correct for item in items)
        failures = sum(
            item.terminal_status is not TerminalStatus.COMPLETED for item in items
        )
        summaries[architecture] = ArchitectureSummary(
            architecture=architecture,
            denominator=denominator,
            root_service_correct=service_correct_count,
            root_cause_pair_correct=pair_correct_count,
            terminal_failures=failures,
            root_service_ac1=service_correct_count / denominator,
            root_cause_pair_ac1=pair_correct_count / denominator,
        )
    return tuple(scored), summaries
