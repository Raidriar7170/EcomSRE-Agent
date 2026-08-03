"""Typed read-only logs search tool."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from ecomsre.backends.live_protocol import LogsObservationBatch
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


class LogsQuery(Phase1Model):
    schema_version: Literal["phase1.logs-query.v1"]
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


class LogsResult(ToolResultBase):
    schema_version: Literal["phase1.logs-result.v1"]
    tool_name: Literal["search_logs"] = "search_logs"


def search_logs(
    context: ToolContext,
    query: LogsQuery,
) -> LogsResult:
    execution = execute_read_only_tool(
        context,
        query,
        query_type=LogsQuery,
        source=EvidenceSource.LOGS,
        expected_batch_type=LogsObservationBatch,
        dispatch=lambda validated_query, timeout: context.backend.search_logs(
            validated_query,
            timeout_seconds=timeout,
        ),
    )
    return LogsResult(
        schema_version="phase1.logs-result.v1",
        status=execution.status,
        evidence_refs=execution.evidence_refs,
        budget_consumed=execution.budget_consumed,
        dispatched=execution.dispatched,
        error_code=execution.error_code,
    )
