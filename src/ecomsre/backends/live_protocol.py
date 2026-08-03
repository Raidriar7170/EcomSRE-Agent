"""Immutable contracts for read-only observability backends."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Literal, Protocol, cast

from pydantic import (
    Field,
    StrictInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

from ecomsre.phase1.contracts import (
    MAX_EVIDENCE_ATTRIBUTES,
    MAX_EVIDENCE_LIMITATIONS,
    MAX_EVIDENCE_OBSERVATION_TYPE_LENGTH,
    MAX_SERVICE_LENGTH,
    MAX_TEXT_ENTRY_LENGTH,
    EvidenceAttribute,
    EvidenceScalar,
    EvidenceSource,
    Phase1Model,
)

if TYPE_CHECKING:
    from ecomsre.tools.changes import ChangesQuery
    from ecomsre.tools.logs import LogsQuery
    from ecomsre.tools.metrics import MetricsQuery
    from ecomsre.tools.traces import TracesQuery

MAX_BACKEND_OBSERVATIONS = 1024
RawArtifactIndex = Annotated[
    StrictInt,
    Field(ge=0, lt=MAX_BACKEND_OBSERVATIONS),
]


class BackendStatus(str, Enum):
    """Availability state supplied by a read-only backend."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    TIMEOUT = "TIMEOUT"


class BackendObservation(Phase1Model):
    """One bounded backend observation, independent of any filesystem."""

    service: str = Field(min_length=1, max_length=MAX_SERVICE_LENGTH)
    started_at: datetime
    ended_at: datetime
    observation_type: str = Field(
        min_length=1,
        max_length=MAX_EVIDENCE_OBSERVATION_TYPE_LENGTH,
    )
    attributes: tuple[EvidenceAttribute, ...] = Field(
        max_length=MAX_EVIDENCE_ATTRIBUTES,
    )
    limitations: tuple[str, ...] = Field(
        max_length=MAX_EVIDENCE_LIMITATIONS,
    )

    @field_validator("service", "observation_type", mode="before")
    @classmethod
    def require_trimmed_text(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> str:
        if not isinstance(value, str):
            # Pydantic v2 lets TypeError escape field validators.
            raise ValueError(  # noqa: TRY004
                f"{info.field_name} must be a string"
            )
        trimmed = value.strip()
        if not trimmed:
            raise ValueError(f"{info.field_name} must not be empty")
        return trimmed

    @field_validator("attributes", mode="before")
    @classmethod
    def canonicalize_attribute_mapping(
        cls,
        value: object,
    ) -> object:
        if not isinstance(value, Mapping):
            return value
        if any(not isinstance(name, str) for name in value):
            raise ValueError("attribute mapping keys must be strings")
        return tuple(
            EvidenceAttribute(
                name=name,
                value=cast(EvidenceScalar, attribute_value),
            )
            for name, attribute_value in sorted(value.items())
        )

    @field_validator("attributes")
    @classmethod
    def require_canonical_attributes(
        cls,
        values: tuple[EvidenceAttribute, ...],
    ) -> tuple[EvidenceAttribute, ...]:
        names = tuple(attribute.name for attribute in values)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("attributes must have unique names in canonical order")
        return values

    @field_validator("limitations")
    @classmethod
    def require_bounded_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            if not isinstance(value, str):
                # Pydantic v2 lets TypeError escape field validators.
                raise ValueError(  # noqa: TRY004
                    "limitations entries must be strings"
                )
            trimmed = value.strip()
            if not trimmed or len(trimmed) > MAX_TEXT_ENTRY_LENGTH:
                raise ValueError("limitations entry is empty or too long")
            normalized.append(trimmed)
        return tuple(normalized)


class _ObservationBatch(Phase1Model):
    status: BackendStatus
    observations: tuple[BackendObservation, ...] = Field(
        max_length=MAX_BACKEND_OBSERVATIONS,
    )
    raw_artifact_indices: tuple[RawArtifactIndex, ...] = Field(
        max_length=MAX_BACKEND_OBSERVATIONS,
    )
    raw_artifact_filename: str = Field(
        pattern=r"^(?:metrics|logs|traces|changes)\.json$",
    )
    raw_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_status_observation_consistency(self) -> _ObservationBatch:
        if len(self.raw_artifact_indices) != len(self.observations):
            raise ValueError(
                "raw_artifact_indices must align with observations"
            )
        if self.raw_artifact_indices != tuple(
            sorted(set(self.raw_artifact_indices))
        ):
            raise ValueError(
                "raw_artifact_indices must be unique and ascending"
            )
        if self.status != BackendStatus.AVAILABLE and self.observations:
            raise ValueError(
                "unavailable or timed-out batch must have no observations"
            )
        return self


class MetricsObservationBatch(_ObservationBatch):
    source: Literal[EvidenceSource.METRICS] = EvidenceSource.METRICS
    raw_artifact_filename: Literal["metrics.json"] = "metrics.json"


class LogsObservationBatch(_ObservationBatch):
    source: Literal[EvidenceSource.LOGS] = EvidenceSource.LOGS
    raw_artifact_filename: Literal["logs.json"] = "logs.json"


class TracesObservationBatch(_ObservationBatch):
    source: Literal[EvidenceSource.TRACES] = EvidenceSource.TRACES
    raw_artifact_filename: Literal["traces.json"] = "traces.json"


class ChangesObservationBatch(_ObservationBatch):
    source: Literal[EvidenceSource.CHANGES] = EvidenceSource.CHANGES
    raw_artifact_filename: Literal["changes.json"] = "changes.json"


class ObservabilityBackend(Protocol):
    """Read-only backend interface used by the four Phase 1 tools."""

    def query_metrics(
        self,
        query: MetricsQuery,
        *,
        timeout_seconds: float,
    ) -> MetricsObservationBatch: ...

    def search_logs(
        self,
        query: LogsQuery,
        *,
        timeout_seconds: float,
    ) -> LogsObservationBatch: ...

    def search_traces(
        self,
        query: TracesQuery,
        *,
        timeout_seconds: float,
    ) -> TracesObservationBatch: ...

    def list_changes(
        self,
        query: ChangesQuery,
        *,
        timeout_seconds: float,
    ) -> ChangesObservationBatch: ...
