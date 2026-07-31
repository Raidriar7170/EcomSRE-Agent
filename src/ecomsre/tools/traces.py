"""Typed read-only traces search tool."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from ecomsre.backends.live_protocol import TracesObservationBatch
from ecomsre.phase1.contracts import (
    MAX_SERVICE_LENGTH,
    EvidenceSource,
    Phase1Model,
)
from ecomsre.tools.base import (
    ToolContext,
    ToolResultBase,
    execute_read_only_tool,
)


class TracesQuery(Phase1Model):
    schema_version: Literal["phase1.traces-query.v1"]
    started_at: datetime
    ended_at: datetime
    service: str | None = Field(default=None, max_length=MAX_SERVICE_LENGTH)

    @field_validator("service", mode="before")
    @classmethod
    def normalize_service(cls, value: object | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("service must be a nonempty string")
        return value.strip()


class TracesResult(ToolResultBase):
    schema_version: Literal["phase1.traces-result.v1"]
    tool_name: Literal["search_traces"] = "search_traces"


def search_traces(
    context: ToolContext,
    query: TracesQuery,
) -> TracesResult:
    execution = execute_read_only_tool(
        context,
        query,
        query_type=TracesQuery,
        source=EvidenceSource.TRACES,
        expected_batch_type=TracesObservationBatch,
        dispatch=lambda validated_query, timeout: context.backend.search_traces(
            validated_query,
            timeout_seconds=timeout,
        ),
    )
    return TracesResult(
        schema_version="phase1.traces-result.v1",
        status=execution.status,
        evidence_refs=execution.evidence_refs,
        budget_consumed=execution.budget_consumed,
        dispatched=execution.dispatched,
        error_code=execution.error_code,
    )
