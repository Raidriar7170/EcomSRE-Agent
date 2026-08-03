"""Typed read-only deployment/change listing tool."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from ecomsre.backends.live_protocol import ChangesObservationBatch
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


class ChangesQuery(Phase1Model):
    schema_version: Literal["phase1.changes-query.v1"]
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


class ChangesResult(ToolResultBase):
    schema_version: Literal["phase1.changes-result.v1"]
    tool_name: Literal["list_changes"] = "list_changes"


def list_changes(
    context: ToolContext,
    query: ChangesQuery,
) -> ChangesResult:
    execution = execute_read_only_tool(
        context,
        query,
        query_type=ChangesQuery,
        source=EvidenceSource.CHANGES,
        expected_batch_type=ChangesObservationBatch,
        dispatch=lambda validated_query, timeout: context.backend.list_changes(
            validated_query,
            timeout_seconds=timeout,
        ),
    )
    return ChangesResult(
        schema_version="phase1.changes-result.v1",
        status=execution.status,
        evidence_refs=execution.evidence_refs,
        budget_consumed=execution.budget_consumed,
        dispatched=execution.dispatched,
        error_code=execution.error_code,
    )
