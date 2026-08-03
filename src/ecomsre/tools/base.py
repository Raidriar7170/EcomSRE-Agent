"""Shared fail-closed execution support for Phase 1 read-only tools."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, TypeVar, cast

from pydantic import Field, StrictBool, ValidationError, model_validator

from ecomsre.backends.live_protocol import (
    BackendStatus,
    ObservabilityBackend,
    _ObservationBatch,
)
from ecomsre.phase1.budgets import BudgetExceeded, RunBudget
from ecomsre.phase1.contracts import (
    MAX_EVIDENCE_REFS,
    EvidenceRef,
    EvidenceSource,
    Incident,
    Phase1Model,
    StableErrorCode,
)
from ecomsre.phase1.evidence import (
    EvidenceDraft,
    EvidenceStore,
    EvidenceStoreError,
)
from ecomsre.phase1.validator import (
    EvidenceValidationError,
    revalidate_phase1_model,
)


class ToolStatus(str, Enum):
    OK = "OK"
    ERROR = "ERROR"


class ToolResultBase(Phase1Model):
    """Common immutable success/error invariants for typed tool results."""

    status: ToolStatus
    evidence_refs: tuple[EvidenceRef, ...] = Field(
        max_length=MAX_EVIDENCE_REFS,
    )
    budget_consumed: StrictBool
    dispatched: StrictBool
    error_code: StableErrorCode | None = None

    @model_validator(mode="after")
    def require_consistent_result(self) -> ToolResultBase:
        if self.status is ToolStatus.OK and self.error_code is not None:
            raise ValueError("OK result cannot have an error code")
        if self.status is ToolStatus.ERROR:
            if self.error_code is None:
                raise ValueError("ERROR result requires a stable error code")
            if self.evidence_refs:
                raise ValueError("ERROR result cannot expose evidence refs")
        if self.dispatched and not self.budget_consumed:
            raise ValueError("dispatched result requires consumed budget")
        if self.status is ToolStatus.OK and (
            not self.budget_consumed or not self.dispatched
        ):
            raise ValueError("OK result requires budget and dispatch")
        return self


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Authenticated run-local dependencies available to a tool call."""

    incident: Incident
    evidence_store: EvidenceStore
    budget: RunBudget
    backend: ObservabilityBackend
    timeout_seconds: float

    def __post_init__(self) -> None:
        validated_incident = revalidate_phase1_model(
            self.incident,
            Incident,
        )
        object.__setattr__(self, "incident", validated_incident)
        if not isinstance(self.evidence_store, EvidenceStore):
            raise TypeError("evidence_store must be an EvidenceStore")
        if not isinstance(self.budget, RunBudget):
            raise TypeError("budget must be a RunBudget")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")


class WindowedQuery(Protocol):
    started_at: datetime
    ended_at: datetime
    service: str | None


@dataclass(frozen=True, slots=True)
class ToolExecution:
    status: ToolStatus
    evidence_refs: tuple[str, ...]
    error_code: StableErrorCode | None
    budget_consumed: bool
    dispatched: bool


BatchT = TypeVar("BatchT", bound=_ObservationBatch)
QueryT = TypeVar("QueryT", bound=Phase1Model)


def _error(
    code: StableErrorCode,
    *,
    budget_consumed: bool = False,
    dispatched: bool = False,
) -> ToolExecution:
    return ToolExecution(
        status=ToolStatus.ERROR,
        evidence_refs=(),
        error_code=code,
        budget_consumed=budget_consumed,
        dispatched=dispatched,
    )


def execute_read_only_tool(
    context: ToolContext,
    query: QueryT,
    *,
    query_type: type[QueryT],
    source: EvidenceSource,
    expected_batch_type: type[BatchT],
    dispatch: Callable[[QueryT, float], object],
) -> ToolExecution:
    """Validate, budget, dispatch, and atomically prevalidate one tool batch."""

    try:
        validated_query = revalidate_phase1_model(query, query_type)
    except EvidenceValidationError:
        return _error(StableErrorCode.INVALID_QUERY)
    windowed_query = cast(WindowedQuery, validated_query)

    if (
        windowed_query.started_at < context.incident.started_at
        or windowed_query.ended_at > context.incident.ended_at
    ):
        return _error(StableErrorCode.OUTSIDE_INCIDENT_WINDOW)

    try:
        context.budget.consume_tool_call()
    except BudgetExceeded:
        return _error(StableErrorCode.BUDGET_EXHAUSTED)

    try:
        raw_batch = dispatch(validated_query, context.timeout_seconds)
    except TimeoutError:
        return _error(
            StableErrorCode.TIMEOUT,
            budget_consumed=True,
            dispatched=True,
        )
    except ValueError:
        return _error(
            StableErrorCode.MALFORMED_REPLAY_ARTIFACT,
            budget_consumed=True,
            dispatched=True,
        )
    except Exception:
        return _error(
            StableErrorCode.INTERNAL_CONTRACT_VIOLATION,
            budget_consumed=True,
            dispatched=True,
        )

    if type(raw_batch) is not expected_batch_type:
        return _error(
            StableErrorCode.INTERNAL_CONTRACT_VIOLATION,
            budget_consumed=True,
            dispatched=True,
        )
    try:
        batch = revalidate_phase1_model(raw_batch, expected_batch_type)
    except EvidenceValidationError:
        return _error(
            StableErrorCode.MALFORMED_REPLAY_ARTIFACT,
            budget_consumed=True,
            dispatched=True,
        )

    if batch.status == BackendStatus.UNAVAILABLE:
        return _error(
            StableErrorCode.BACKEND_UNAVAILABLE,
            budget_consumed=True,
            dispatched=True,
        )
    if batch.status == BackendStatus.TIMEOUT:
        return _error(
            StableErrorCode.TIMEOUT,
            budget_consumed=True,
            dispatched=True,
        )
    if batch.status != BackendStatus.AVAILABLE:
        return _error(
            StableErrorCode.MALFORMED_REPLAY_ARTIFACT,
            budget_consumed=True,
            dispatched=True,
        )
    if len(batch.observations) > MAX_EVIDENCE_REFS:
        return _error(
            StableErrorCode.MALFORMED_REPLAY_ARTIFACT,
            budget_consumed=True,
            dispatched=True,
        )

    try:
        drafts = tuple(
            EvidenceDraft(
                source=source,
                observation_type=row.observation_type,
                attributes=row.attributes,
                raw_artifact_ref=(
                    f"{batch.raw_artifact_filename}#{raw_index}"
                ),
                raw_artifact_sha256=batch.raw_artifact_sha256,
                limitations=row.limitations,
                summary=(
                    f"{source.value.lower()} observation for "
                    f"{row.service}: {row.observation_type}."
                ),
                started_at=row.started_at,
                ended_at=row.ended_at,
                service=row.service,
            )
            for raw_index, row in zip(
                batch.raw_artifact_indices,
                batch.observations,
                strict=True,
            )
        )
    except (AttributeError, TypeError, ValueError, ValidationError):
        return _error(
            StableErrorCode.MALFORMED_REPLAY_ARTIFACT,
            budget_consumed=True,
            dispatched=True,
        )

    try:
        items = context.evidence_store.add_batch(drafts)
    except EvidenceStoreError:
        return _error(
            StableErrorCode.INTERNAL_CONTRACT_VIOLATION,
            budget_consumed=True,
            dispatched=True,
        )

    return ToolExecution(
        status=ToolStatus.OK,
        evidence_refs=tuple(item.evidence_ref for item in items),
        error_code=None,
        budget_consumed=True,
        dispatched=True,
    )
