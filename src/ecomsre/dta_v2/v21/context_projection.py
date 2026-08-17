"""Deterministic bounded investigation-state projection for DTA v2.1."""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import Field, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.agent_contracts import (
    AgentVisibleObservation,
    build_agent_visible_observation,
)
from ecomsre.dta_v2.evidence_store import EvidenceStoreSnapshot
from ecomsre.dta_v2.tool_contracts import (
    DiagnosticLogRecord,
    InspectResourceUsageRequest,
    InspectServiceRuntimeRequest,
    MetricRecord,
    ObservationStatus,
    QueryMetricsRequest,
    ReadToolObservation,
    ResourceUsageRecord,
    RuntimeRecord,
    SearchLogsRequest,
    ToolName,
    TraceNeighborhoodRecord,
    TraceNeighborhoodRequest,
    revalidate_observation,
)
from ecomsre.dta_v2.v21.agent_contracts import AlertContextV21
from ecomsre.dta_v2.v21.contracts import (
    DtaModelV21,
    EvidenceSourceV21,
    IdentifierV21,
    Sha256V21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.planner_contracts import DiagnosticHypothesisV21


MAX_INVESTIGATION_STATE_BYTES = 24_000
_SOURCE_MAP = {
    "METRICS": EvidenceSourceV21.METRICS,
    "LOGS": EvidenceSourceV21.LOGS,
    "TRACES": EvidenceSourceV21.TRACES,
    "RUNTIME": EvidenceSourceV21.RUNTIME,
    "RESOURCES": EvidenceSourceV21.RESOURCES,
    "CHANGES": EvidenceSourceV21.CHANGES,
}
_SOURCE_ORDER = {source: index for index, source in enumerate(EvidenceSourceV21)}


class EvidenceIndexFactV21(DtaModelV21):
    """Exact deterministic projection of one or more typed records."""

    fact_kind: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9_]+$")
    service: IdentifierV21
    labels: tuple[str, ...] = Field(max_length=12)
    numeric_values: tuple[StrictFloat, ...] = Field(max_length=12)


class EvidenceIndexEntryV21(DtaModelV21):
    schema_version: Literal["dta-v21.evidence-index-entry.v1"]
    evidence_ref: str
    source: EvidenceSourceV21
    service_scope: tuple[IdentifierV21, ...] = Field(min_length=1, max_length=12)
    status: ObservationStatus
    error_code: str | None
    record_count: StrictInt = Field(ge=0, le=40)
    facts: tuple[EvidenceIndexFactV21, ...] = Field(max_length=40)
    truncated: bool
    artifact_sha256: Sha256V21

    @model_validator(mode="after")
    def require_entry_shape(self) -> EvidenceIndexEntryV21:
        if self.service_scope != tuple(sorted(set(self.service_scope))):
            raise ValueError("Evidence Index service scope is not canonical")
        if self.status is ObservationStatus.SUCCESS:
            if self.error_code is not None:
                raise ValueError("successful Evidence Index entry carries an error")
        elif self.error_code is None or self.facts or self.record_count:
            raise ValueError("failed Evidence Index entry is not fail-closed")
        return self


class EvidenceIndexV21(DtaModelV21):
    schema_version: Literal["dta-v21.evidence-index.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    entries: tuple[EvidenceIndexEntryV21, ...] = Field(max_length=4)
    evidence_index_sha256: Sha256V21

    @model_validator(mode="after")
    def require_index_digest(self) -> EvidenceIndexV21:
        keys = tuple(
            (_SOURCE_ORDER[item.source], item.evidence_ref) for item in self.entries
        )
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("Evidence Index entries are not canonical and unique")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"evidence_index_sha256"})
        )
        if self.evidence_index_sha256 != expected:
            raise ValueError("Evidence Index digest differs")
        return self


class InvestigationStateViewV21(DtaModelV21):
    schema_version: Literal["dta-v21.investigation-state-view.v1"]
    alert_context: AlertContextV21
    hypotheses: tuple[DiagnosticHypothesisV21, ...] = Field(max_length=3)
    evidence_index: EvidenceIndexV21
    newest_observation: AgentVisibleObservation | None
    prior_tools: tuple[ToolName, ...] = Field(max_length=4)
    prior_normalized_request_sha256: tuple[Sha256V21, ...] = Field(max_length=4)
    remaining_read_dispatches: StrictInt = Field(ge=0, le=4)
    remaining_provider_investigation_turns: StrictInt = Field(ge=0, le=5)
    next_turn_ordinal: StrictInt = Field(ge=1, le=5)
    serialized_size_bytes: StrictInt = Field(ge=1, le=MAX_INVESTIGATION_STATE_BYTES)
    state_sha256: Sha256V21

    @model_validator(mode="after")
    def require_state_binding(self) -> InvestigationStateViewV21:
        if self.alert_context.run_id != self.evidence_index.run_id:
            raise ValueError("investigation state contains another run")
        if len(self.prior_tools) != len(self.prior_normalized_request_sha256):
            raise ValueError("investigation request history is partial")
        if self.remaining_read_dispatches != 4 - len(self.prior_tools):
            raise ValueError("investigation read budget accounting differs")
        if self.next_turn_ordinal != 6 - self.remaining_provider_investigation_turns:
            raise ValueError("investigation next turn ordinal differs")
        if self.newest_observation is None:
            if self.evidence_index.entries:
                raise ValueError("nonempty Evidence Index lacks newest observation")
        elif (
            not self.evidence_index.entries
            or self.newest_observation.evidence_ref
            not in {item.evidence_ref for item in self.evidence_index.entries}
        ):
            raise ValueError("newest observation is outside the Evidence Index")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"state_sha256"})
        )
        if self.state_sha256 != expected:
            raise ValueError("investigation state digest differs")
        actual_size = len(self.model_dump_json().encode("utf-8"))
        if self.serialized_size_bytes != actual_size:
            raise ValueError("investigation state byte count differs")
        return self


def _service_scope(request: object, observation: ReadToolObservation) -> tuple[str, ...]:
    services: set[str] = set()
    if isinstance(request, (QueryMetricsRequest, SearchLogsRequest, TraceNeighborhoodRequest)):
        services.add(request.service)
    elif isinstance(request, (InspectServiceRuntimeRequest, InspectResourceUsageRequest)):
        services.update(request.services)
    for record in observation.results:
        for field in ("service", "logical_service", "anchor_service", "parent_service"):
            value = getattr(record, field, None)
            if isinstance(value, str):
                services.add(value)
        path = getattr(record, "service_path", ())
        services.update(item for item in path if isinstance(item, str))
    if not services:
        raise ValueError("Evidence Index entry lacks a request service scope")
    return tuple(sorted(services))


def _fact(record: object) -> EvidenceIndexFactV21:
    if isinstance(record, MetricRecord):
        return EvidenceIndexFactV21(
            fact_kind="METRIC",
            service=record.service,
            labels=(record.metric_kind.value, record.unit.value),
            numeric_values=(record.value, float(record.sample_count)),
        )
    if isinstance(record, DiagnosticLogRecord):
        return EvidenceIndexFactV21(
            fact_kind="LOG_EVENT",
            service=record.service,
            labels=(record.severity.value,),
            numeric_values=(),
        )
    if isinstance(record, TraceNeighborhoodRecord):
        return EvidenceIndexFactV21(
            fact_kind="TRACE_EDGE",
            service=record.service,
            labels=(
                record.relationship.value,
                record.status.value,
                "FIRST_ERROR" if record.first_error_location else "NON_ROOT_ERROR",
            ),
            numeric_values=(record.duration_ms,),
        )
    if isinstance(record, RuntimeRecord):
        return EvidenceIndexFactV21(
            fact_kind="RUNTIME_STATE",
            service=record.logical_service,
            labels=(record.state.value, record.health.value, record.endpoint_state.value),
            numeric_values=(
                float(record.restart_count),
                float(record.exit_code) if record.exit_code is not None else -1.0,
            ),
        )
    if isinstance(record, ResourceUsageRecord):
        return EvidenceIndexFactV21(
            fact_kind="RESOURCE_SERIES",
            service=record.logical_service,
            labels=("CPU_PERCENT", "MEMORY_BYTES", "MEMORY_SLOPE"),
            numeric_values=(
                max(item.cpu_percent for item in record.samples),
                float(record.samples[-1].memory_bytes),
                record.memory_slope_bytes_per_second,
                float(len(record.samples)),
            ),
        )
    raise TypeError("unsupported typed observation record")


def build_evidence_index_v21(snapshot: EvidenceStoreSnapshot) -> EvidenceIndexV21:
    snapshot = EvidenceStoreSnapshot.model_validate_json(snapshot.model_dump_json())
    requests = {
        item.request_sha256: item.resolve() for item in snapshot.request_envelopes
    }
    entries = tuple(
        sorted(
            (
                EvidenceIndexEntryV21(
                    schema_version="dta-v21.evidence-index-entry.v1",
                    evidence_ref=observation.evidence_ref,
                    source=_SOURCE_MAP[observation.source.value],
                    service_scope=_service_scope(
                        requests[observation.request_sha256], observation
                    ),
                    status=observation.status,
                    error_code=(
                        None
                        if observation.error_code is None
                        else observation.error_code.value
                    ),
                    record_count=observation.result_count,
                    facts=tuple(_fact(item) for item in observation.results),
                    truncated=observation.truncated,
                    artifact_sha256=observation.artifact_sha256,
                )
                for observation in snapshot.observations
            ),
            key=lambda item: (_SOURCE_ORDER[item.source], item.evidence_ref),
        )
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.evidence-index.v1",
        "run_id": snapshot.run_id,
        "entries": entries,
    }
    draft = cast(Any, EvidenceIndexV21).model_construct(
        **payload, evidence_index_sha256="0" * 64
    )
    return EvidenceIndexV21.model_validate(
        {
            **payload,
            "evidence_index_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"evidence_index_sha256"})
            ),
        }
    )


def build_investigation_state_view_v21(
    *,
    context: AlertContextV21,
    hypotheses: tuple[DiagnosticHypothesisV21, ...],
    evidence_store: EvidenceStoreSnapshot,
    newest_observation: ReadToolObservation | None,
    completed_provider_turns: int | None = None,
) -> InvestigationStateViewV21:
    context = AlertContextV21.model_validate(context.model_dump(mode="python"))
    snapshot = EvidenceStoreSnapshot.model_validate_json(evidence_store.model_dump_json())
    if context.run_id != snapshot.run_id:
        raise ValueError("context and Evidence Store runs differ")
    visible = None
    if newest_observation is not None:
        visible = build_agent_visible_observation(
            revalidate_observation(newest_observation)
        )
    turns = len(snapshot.observations) if completed_provider_turns is None else completed_provider_turns
    if turns < 0 or turns > 5:
        raise ValueError("completed Provider turns are outside the budget")
    payload: dict[str, object] = {
        "schema_version": "dta-v21.investigation-state-view.v1",
        "alert_context": context,
        "hypotheses": tuple(sorted(hypotheses, key=lambda item: item.hypothesis_id)),
        "evidence_index": build_evidence_index_v21(snapshot),
        "newest_observation": visible,
        "prior_tools": tuple(item.tool for item in snapshot.observations),
        "prior_normalized_request_sha256": tuple(
            item.request_sha256 for item in snapshot.observations
        ),
        "remaining_read_dispatches": 4 - snapshot.dispatch_count,
        "remaining_provider_investigation_turns": 5 - turns,
        "next_turn_ordinal": turns + 1,
    }
    size = 1
    for _ in range(8):
        draft = cast(Any, InvestigationStateViewV21).model_construct(
            **payload,
            serialized_size_bytes=size,
            state_sha256="0" * 64,
        )
        observed = len(draft.model_dump_json().encode("utf-8"))
        if observed == size:
            break
        size = observed
    if size > MAX_INVESTIGATION_STATE_BYTES:
        raise ValueError("investigation state exceeds the frozen byte ceiling")
    digest_draft = cast(Any, InvestigationStateViewV21).model_construct(
        **payload,
        serialized_size_bytes=size,
        state_sha256="0" * 64,
    )
    return InvestigationStateViewV21.model_validate(
        {
            **payload,
            "serialized_size_bytes": size,
            "state_sha256": semantic_sha256(
                digest_draft.model_dump(mode="json", exclude={"state_sha256"})
            ),
        }
    )


__all__ = (
    "EvidenceIndexEntryV21",
    "EvidenceIndexFactV21",
    "EvidenceIndexV21",
    "InvestigationStateViewV21",
    "MAX_INVESTIGATION_STATE_BYTES",
    "build_evidence_index_v21",
    "build_investigation_state_view_v21",
)
