"""Sequential one-shot RCA100 execution and terminal accounting."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
import time
from pathlib import Path
from typing import Literal
import urllib.error

from pydantic import AwareDatetime, Field, StrictFloat, StrictInt, model_validator

from ecomsre.model.gateway import (
    OpenAICompatibleConfig,
    OpenAICompatibleTransport,
    ProviderProtocolError,
    StdlibOpenAICompatibleTransport,
)
from ecomsre_rca100.contracts import (
    RCA100DiagnosisProvenance,
    RCA100MetricsArbitrationAction,
    RCA100Model,
    arbitrate_rca100_diagnosis,
)
from ecomsre_rca100.lifecycle import (
    RCA100Schedule,
    RCA100ScheduleRecord,
    create_once_json,
)
from ecomsre_rca100.projection import build_agent_context
from ecomsre_rca100.prompt import OpenAICompatibleRCA100Provider
from ecomsre_rcaeval_adaptive.v2_runner import PacedTransport, RequestPacer
from ecomsre_rcaeval_v2.dev3_provider import Dev3RetryingTransport
from ecomsre_rcaeval_v2.dev3_token_accounting import (
    AttemptBudget,
    rebuild_attempt_accounting,
)


class RCA100TerminalStatus(str, Enum):
    COMPLETED = "COMPLETED"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    PROTOCOL_VIOLATION = "PROTOCOL_VIOLATION"
    TIMEOUT = "TIMEOUT"
    METRICS_PROJECTION_FAILURE = "METRICS_PROJECTION_FAILURE"
    ENTITY_NORMALIZATION_FAILURE = "ENTITY_NORMALIZATION_FAILURE"


class RCA100RunAttempt(RCA100Model):
    schema_version: Literal["rca100.run-attempt.v1"] = "rca100.run-attempt.v1"
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    opaque_case_id: str = Field(pattern=r"^rca100-case-[0-9]{4}$")
    schedule_position: StrictInt = Field(ge=1, le=103)
    schedule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_freeze_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at_utc: AwareDatetime


class RCA100TerminalRecord(RCA100Model):
    schema_version: Literal["rca100.terminal-record.v1"] = (
        "rca100.terminal-record.v1"
    )
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    opaque_case_id: str = Field(pattern=r"^rca100-case-[0-9]{4}$")
    schedule_position: StrictInt = Field(ge=1, le=103)
    status: RCA100TerminalStatus
    failure_code: str | None = Field(default=None, max_length=128)
    initial_root_entity_ref: str | None = Field(default=None, max_length=768)
    final_root_entity_ref: str | None = Field(default=None, max_length=768)
    initial_fault_type: str | None = Field(default=None, max_length=256)
    final_fault_type: str | None = Field(default=None, max_length=256)
    m3_action: RCA100MetricsArbitrationAction | None = None
    m3_reason_codes: tuple[str, ...] = Field(default=(), max_length=2)
    initial_metrics_rank_or_none: StrictInt | None = Field(default=None, ge=1, le=6)
    metrics_top1_entity_ref: str | None = Field(default=None, max_length=768)
    metrics_top2_entity_ref_or_none: str | None = Field(default=None, max_length=768)
    normalized_margin: StrictFloat | None = None
    root_provenance: RCA100DiagnosisProvenance | None = None
    fault_type_provenance: Literal["MODEL_INITIAL"] | None = None
    initial_evidence_refs: tuple[str, ...] = Field(default=(), max_length=18)
    final_evidence_refs: tuple[str, ...] = Field(default=(), max_length=18)
    metrics_projection_status: Literal[
        "AVAILABLE", "METRICS_PROJECTION_UNAVAILABLE"
    ] | None = None
    semantic_model_operations: StrictInt = Field(ge=0, le=1)
    specialist_calls: Literal[0] = 0
    fusion_model_calls: Literal[0] = 0
    provider_attempts: StrictInt = Field(ge=0, le=2)
    transport_retries: StrictInt = Field(ge=0, le=1)
    known_token_lower_bound: StrictInt = Field(ge=0)
    conservative_token_upper_bound: StrictInt = Field(ge=0)
    request_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    latency_seconds: StrictFloat = Field(ge=0)
    started_at_utc: AwareDatetime
    ended_at_utc: AwareDatetime

    @model_validator(mode="after")
    def require_terminal_disposition(self) -> RCA100TerminalRecord:
        if self.ended_at_utc < self.started_at_utc:
            raise ValueError("RCA100 terminal ended before it started")
        if self.transport_retries > self.provider_attempts - self.semantic_model_operations:
            raise ValueError("RCA100 terminal retry accounting is inconsistent")
        if self.status is RCA100TerminalStatus.COMPLETED:
            required = (
                self.initial_root_entity_ref,
                self.final_root_entity_ref,
                self.initial_fault_type,
                self.final_fault_type,
                self.m3_action,
                self.root_provenance,
                self.fault_type_provenance,
                self.metrics_projection_status,
                self.request_sha256,
            )
            if any(item is None for item in required) or self.failure_code is not None:
                raise ValueError("completed RCA100 terminal lacks diagnosis fields")
            if self.semantic_model_operations != 1 or not self.initial_evidence_refs:
                raise ValueError("completed RCA100 terminal has invalid model accounting")
            if self.initial_fault_type != self.final_fault_type:
                raise ValueError("RCA100 M3 changed the fault type")
        elif self.failure_code is None:
            raise ValueError("failed RCA100 terminal lacks a failure code")
        return self


def _failure(error: Exception) -> tuple[RCA100TerminalStatus, str]:
    if isinstance(error, urllib.error.HTTPError):
        return (
            RCA100TerminalStatus.PROVIDER_FAILURE,
            "HTTP_429" if error.code == 429 else f"HTTP_{error.code}",
        )
    if isinstance(error, TimeoutError):
        return RCA100TerminalStatus.TIMEOUT, "PROVIDER_TIMEOUT"
    if isinstance(error, ProviderProtocolError):
        return RCA100TerminalStatus.PROTOCOL_VIOLATION, "PROVIDER_PROTOCOL_VIOLATION"
    if isinstance(error, (ConnectionError, OSError)):
        return RCA100TerminalStatus.PROVIDER_FAILURE, "PROVIDER_TRANSPORT_FAILURE"
    return RCA100TerminalStatus.INVALID_SCHEMA, "PROVIDER_OUTPUT_INVALID_SCHEMA"


def execute_case(
    record: RCA100ScheduleRecord,
    *,
    cases_root: Path,
    journal_root: Path,
    output_root: Path,
    schedule_sha256: str,
    protocol_freeze_sha256: str,
    provider_config: OpenAICompatibleConfig,
    expected_model: str,
    timeout_seconds: float,
    max_completion_tokens: int,
    prompt_token_reservation: int,
    pacer: RequestPacer,
    budget: AttemptBudget,
    retry_policy_sha256: str,
    base_transport: OpenAICompatibleTransport | None = None,
    context_builder: Callable[..., object] = build_agent_context,
) -> RCA100TerminalRecord:
    started_at = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    run_root = journal_root / "runs" / record.run_id
    create_once_json(
        journal_root / "run-attempts" / f"{record.opaque_case_id}.json",
        RCA100RunAttempt(
            run_id=record.run_id,
            opaque_case_id=record.opaque_case_id,
            schedule_position=record.position,
            schedule_sha256=schedule_sha256,
            protocol_freeze_sha256=protocol_freeze_sha256,
            started_at_utc=started_at,
        ).model_dump(mode="json"),
    )
    context = None
    provider: OpenAICompatibleRCA100Provider | None = None
    diagnosis = None
    try:
        context = context_builder(
            cases_root / record.source_task_id,
            opaque_case_id=record.opaque_case_id,
        )
    except Exception as error:
        terminal = RCA100TerminalRecord(
            run_id=record.run_id,
            opaque_case_id=record.opaque_case_id,
            schedule_position=record.position,
            status=RCA100TerminalStatus.METRICS_PROJECTION_FAILURE,
            failure_code="SOURCE_PROJECTION_FAILURE",
            semantic_model_operations=0,
            provider_attempts=0,
            transport_retries=0,
            known_token_lower_bound=0,
            conservative_token_upper_bound=0,
            latency_seconds=time.monotonic() - started_monotonic,
            started_at_utc=started_at,
            ended_at_utc=datetime.now(timezone.utc),
        )
        create_once_json(
            output_root / "terminals" / f"{record.opaque_case_id}.json",
            terminal.model_dump(mode="json"),
        )
        del error
        return terminal

    from ecomsre_rca100.projection import RCA100AgentContext

    if not isinstance(context, RCA100AgentContext):
        raise TypeError("RCA100 context builder returned an invalid context")
    retry_transport = Dev3RetryingTransport(
        PacedTransport(base_transport or StdlibOpenAICompatibleTransport(), pacer),
        run_root=run_root,
        budget=budget,
        policy_lock_sha256=retry_policy_sha256,
        expected_timeout_seconds=timeout_seconds,
    )
    provider = OpenAICompatibleRCA100Provider(
        config=provider_config,
        expected_model=expected_model,
        timeout_seconds=timeout_seconds,
        max_completion_tokens=max_completion_tokens,
        transport=retry_transport,
    )
    status = RCA100TerminalStatus.COMPLETED
    failure_code: str | None = None
    try:
        initial_result = provider.diagnose(context)
        diagnosis = arbitrate_rca100_diagnosis(
            initial_result, context.metrics.ranking
        )
        create_once_json(
            output_root / "diagnoses" / record.run_id / "initial.json",
            diagnosis.initial_diagnosis.model_dump(mode="json"),
        )
        create_once_json(
            output_root / "diagnoses" / record.run_id / "m3-decision.json",
            diagnosis.arbitration_decision.model_dump(mode="json"),
        )
        create_once_json(
            output_root / "diagnoses" / record.run_id / "final.json",
            diagnosis.final_diagnosis.model_dump(mode="json"),
        )
    except Exception as error:
        status, failure_code = _failure(error)
    accounting = rebuild_attempt_accounting(
        (run_root,),
        prompt_token_reservation=prompt_token_reservation,
        max_completion_tokens=max_completion_tokens,
    )
    decision = None if diagnosis is None else diagnosis.arbitration_decision
    terminal_initial = None if diagnosis is None else diagnosis.initial_diagnosis
    terminal_final = None if diagnosis is None else diagnosis.final_diagnosis
    terminal = RCA100TerminalRecord(
        run_id=record.run_id,
        opaque_case_id=record.opaque_case_id,
        schedule_position=record.position,
        status=status,
        failure_code=failure_code,
        initial_root_entity_ref=(
            None if terminal_initial is None else terminal_initial.root_cause_entity_ref
        ),
        final_root_entity_ref=(
            None if terminal_final is None else terminal_final.root_cause_entity_ref
        ),
        initial_fault_type=(
            None if terminal_initial is None else terminal_initial.fault_type
        ),
        final_fault_type=(
            None if terminal_final is None else terminal_final.fault_type
        ),
        m3_action=None if decision is None else decision.action,
        m3_reason_codes=() if decision is None else decision.reason_codes,
        initial_metrics_rank_or_none=(
            None if decision is None else decision.initial_metrics_rank_or_none
        ),
        metrics_top1_entity_ref=(
            None if decision is None else decision.metrics_top1_entity_ref
        ),
        metrics_top2_entity_ref_or_none=(
            None if decision is None else decision.metrics_top2_entity_ref_or_none
        ),
        normalized_margin=None if decision is None else decision.normalized_margin,
        root_provenance=None if diagnosis is None else diagnosis.root_provenance,
        fault_type_provenance=(
            None if diagnosis is None else diagnosis.fault_type_provenance
        ),
        initial_evidence_refs=(
            () if terminal_initial is None else terminal_initial.evidence_refs
        ),
        final_evidence_refs=(
            () if terminal_final is None else terminal_final.evidence_refs
        ),
        metrics_projection_status=context.metrics.status,
        semantic_model_operations=1,
        provider_attempts=accounting.provider_attempt_count,
        transport_retries=accounting.retry_attempt_count,
        known_token_lower_bound=accounting.known_token_lower_bound,
        conservative_token_upper_bound=accounting.conservative_token_upper_bound,
        request_sha256=provider.last_request_sha256,
        latency_seconds=time.monotonic() - started_monotonic,
        started_at_utc=started_at,
        ended_at_utc=datetime.now(timezone.utc),
    )
    create_once_json(
        output_root / "terminals" / f"{record.opaque_case_id}.json",
        terminal.model_dump(mode="json"),
    )
    return terminal


def execute_schedule(
    schedule: RCA100Schedule,
    *,
    execute: Callable[[RCA100ScheduleRecord], RCA100TerminalRecord],
) -> tuple[RCA100TerminalRecord, ...]:
    terminals: list[RCA100TerminalRecord] = []
    for record in schedule.records:
        terminal = execute(record)
        terminals.append(terminal)
        if terminal.failure_code == "HTTP_429":
            break
    return tuple(terminals)


__all__ = [
    "RCA100RunAttempt",
    "RCA100TerminalRecord",
    "RCA100TerminalStatus",
    "execute_case",
    "execute_schedule",
]
