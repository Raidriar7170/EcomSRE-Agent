"""Bounded loop for the one Phase 1 read-only RCA Agent."""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from typing import Literal, cast

from ecomsre.backends.live_protocol import (
    ChangesObservationBatch,
    LogsObservationBatch,
    MetricsObservationBatch,
    ObservabilityBackend,
    TracesObservationBatch,
)
from ecomsre.model.gateway import ModelGateway
from ecomsre.phase1.budgets import RunBudget
from ecomsre.phase1.contracts import (
    AgentTerminalReason,
    AgentTerminalStatus,
    AgentRunReport,
    ChangesAction,
    Evidence,
    FinalAction,
    InvestigationRequest,
    LogsAction,
    MODEL_TIMING_TOLERANCE_SECONDS,
    MetricsAction,
    ModelCallRecord,
    ModelConfiguration,
    ModelFunctionName,
    ModelRequest,
    ModelResponse,
    RCAResult,
    ReadOnlyToolName,
    RemainingBudgets,
    StableErrorCode,
    ToolAction,
    ToolCallRecord,
    TracesAction,
    TranscriptEntry,
)
from ecomsre.phase1.evidence import EvidenceStore
from ecomsre.phase1.validator import (
    EvidenceValidationError,
    revalidate_phase1_model,
    validate_agent_report,
    validate_rca_result,
)
from ecomsre.tools.base import ToolContext, ToolResultBase, ToolStatus
from ecomsre.tools.changes import ChangesQuery, list_changes
from ecomsre.tools.logs import LogsQuery, search_logs
from ecomsre.tools.metrics import MetricsQuery, query_metrics
from ecomsre.tools.traces import TracesQuery, search_traces


class _DispatchObserver:
    """Count actual backend method entry independently of tool declarations."""

    def __init__(self, delegate: ObservabilityBackend) -> None:
        self._delegate = delegate
        self.calls = 0

    def query_metrics(
        self,
        query: MetricsQuery,
        *,
        timeout_seconds: float,
    ) -> MetricsObservationBatch:
        self.calls += 1
        return self._delegate.query_metrics(
            query,
            timeout_seconds=timeout_seconds,
        )

    def search_logs(
        self,
        query: LogsQuery,
        *,
        timeout_seconds: float,
    ) -> LogsObservationBatch:
        self.calls += 1
        return self._delegate.search_logs(
            query,
            timeout_seconds=timeout_seconds,
        )

    def search_traces(
        self,
        query: TracesQuery,
        *,
        timeout_seconds: float,
    ) -> TracesObservationBatch:
        self.calls += 1
        return self._delegate.search_traces(
            query,
            timeout_seconds=timeout_seconds,
        )

    def list_changes(
        self,
        query: ChangesQuery,
        *,
        timeout_seconds: float,
    ) -> ChangesObservationBatch:
        self.calls += 1
        return self._delegate.list_changes(
            query,
            timeout_seconds=timeout_seconds,
        )


def _remaining(budget: RunBudget) -> RemainingBudgets:
    return RemainingBudgets(
        model_calls=budget.remaining_model_calls,
        tool_calls=budget.remaining_tool_calls,
        total_tokens=budget.remaining_tokens,
    )


def _transcript(
    records: list[ToolCallRecord],
) -> tuple[TranscriptEntry, ...]:
    return tuple(
        TranscriptEntry(
            sequence=index,
            action=record.action,
            tool_name=record.tool_name,
            status=record.status,
            error_code=record.error_code,
            evidence_refs=record.evidence_refs,
        )
        for index, record in enumerate(records, start=1)
    )


def _record_model_error(
    request: ModelRequest,
    *,
    call_id: str,
    started_at: datetime,
    monotonic_start: float,
    error_code: StableErrorCode,
) -> ModelCallRecord:
    return ModelCallRecord(
        schema_version="phase1.model-call-record.v1",
        call_id=call_id,
        run_id=request.run_id,
        agent_id=request.agent_id,
        incident_id=request.incident_id,
        task_id=request.task_id,
        request=request,
        response=None,
        started_at=started_at,
        ended_at=datetime.now(UTC),
        monotonic_duration_seconds=time.monotonic() - monotonic_start,
        model_call_consumed=True,
        charged_tokens=0,
        status="ERROR",
        error_code=error_code,
    )


def _tool_name(action: ToolAction) -> ReadOnlyToolName:
    return {
        "metrics": ReadOnlyToolName.QUERY_METRICS,
        "logs": ReadOnlyToolName.SEARCH_LOGS,
        "traces": ReadOnlyToolName.SEARCH_TRACES,
        "changes": ReadOnlyToolName.LIST_CHANGES,
    }[action.action_type]


def _dispatch_tool(
    context: ToolContext,
    action: ToolAction,
) -> ToolResultBase:
    if type(action) is MetricsAction:
        metrics = action
        return query_metrics(
            context,
            MetricsQuery(
                schema_version="phase1.metrics-query.v1",
                started_at=metrics.started_at,
                ended_at=metrics.ended_at,
                service=metrics.service,
            ),
        )
    if type(action) is LogsAction:
        logs = action
        return search_logs(
            context,
            LogsQuery(
                schema_version="phase1.logs-query.v1",
                started_at=logs.started_at,
                ended_at=logs.ended_at,
                service=logs.service,
            ),
        )
    if type(action) is TracesAction:
        traces = action
        return search_traces(
            context,
            TracesQuery(
                schema_version="phase1.traces-query.v1",
                started_at=traces.started_at,
                ended_at=traces.ended_at,
                service=traces.service,
            ),
        )
    if type(action) is ChangesAction:
        changes = action
        return list_changes(
            context,
            ChangesQuery(
                schema_version="phase1.changes-query.v1",
                started_at=changes.started_at,
                ended_at=changes.ended_at,
                service=changes.service,
            ),
        )
    raise TypeError("unsupported tool action")


class SingleAgent:
    """One product Agent with exactly four read-only tools and submit_rca."""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        backend: ObservabilityBackend,
        model_configuration: ModelConfiguration,
        tool_timeout_seconds: float,
    ) -> None:
        self._gateway = gateway
        self._backend = backend
        self._model_configuration = revalidate_phase1_model(
            model_configuration,
            ModelConfiguration,
        )
        if self._model_configuration.temperature != 0:
            raise ValueError("SingleAgent requires temperature zero")
        if (
            isinstance(tool_timeout_seconds, bool)
            or not isinstance(tool_timeout_seconds, (int, float))
            or not math.isfinite(tool_timeout_seconds)
            or tool_timeout_seconds <= 0
        ):
            raise ValueError(
                "tool_timeout_seconds must be finite and positive"
            )
        self._tool_timeout_seconds = float(tool_timeout_seconds)

    def run(self, request: InvestigationRequest) -> AgentRunReport:
        validated_request = revalidate_phase1_model(
            request,
            InvestigationRequest,
        )
        run_started_at = datetime.now(UTC)
        run_monotonic_start = time.monotonic()
        budget = RunBudget(validated_request.budgets)
        store = EvidenceStore(validated_request.run_id)
        model_records: list[ModelCallRecord] = []
        tool_records: list[ToolCallRecord] = []
        final_rca: RCAResult | None = None
        terminal_status = AgentTerminalStatus.TERMINATED
        terminal_reason = AgentTerminalReason.MODEL_RESPONSE_INVALID
        terminal_error_code: StableErrorCode | None = (
            StableErrorCode.MODEL_PROTOCOL_VIOLATION
        )
        model_sequence = 0
        tool_sequence = 0

        while final_rca is None:
            if budget.remaining_model_calls <= 0:
                terminal_reason = (
                    AgentTerminalReason.MODEL_CALL_BUDGET_EXHAUSTED
                )
                terminal_error_code = StableErrorCode.BUDGET_EXHAUSTED
                break
            if budget.remaining_tokens <= 0:
                terminal_reason = AgentTerminalReason.TOKEN_BUDGET_EXHAUSTED
                terminal_error_code = StableErrorCode.BUDGET_EXHAUSTED
                break
            budget.consume_model_call()
            model_sequence += 1
            model_call_id = f"model-call-{model_sequence:04d}"
            model_request = ModelRequest(
                schema_version="phase1.model-request.v1",
                request_id=f"model-request-{model_sequence:04d}",
                run_id=validated_request.run_id,
                agent_id=validated_request.agent_id,
                incident_id=validated_request.incident.incident_id,
                task_id=validated_request.task_id,
                model_name=self._model_configuration.model_name,
                incident=validated_request.incident,
                transcript=_transcript(tool_records),
                evidence=store.snapshot(),
                remaining_budgets=_remaining(budget),
                allowed_actions=tuple(ModelFunctionName),
                temperature=self._model_configuration.temperature,
                timeout_seconds=(
                    self._model_configuration.model_timeout_seconds
                ),
            )
            model_started_at = datetime.now(UTC)
            model_monotonic_start = time.monotonic()
            try:
                raw_response = self._gateway.complete(model_request)
                model_ended_at = datetime.now(UTC)
                model_elapsed = time.monotonic() - model_monotonic_start
            except TimeoutError:
                code = StableErrorCode.TIMEOUT
                model_records.append(
                    _record_model_error(
                        model_request,
                        call_id=model_call_id,
                        started_at=model_started_at,
                        monotonic_start=model_monotonic_start,
                        error_code=code,
                    )
                )
                terminal_reason = AgentTerminalReason.MODEL_CALL_TIMED_OUT
                terminal_error_code = code
                break
            except Exception:
                code = StableErrorCode.MODEL_PROTOCOL_VIOLATION
                model_records.append(
                    _record_model_error(
                        model_request,
                        call_id=model_call_id,
                        started_at=model_started_at,
                        monotonic_start=model_monotonic_start,
                        error_code=code,
                    )
                )
                terminal_reason = AgentTerminalReason.MODEL_GATEWAY_FAILED
                terminal_error_code = code
                break

            try:
                response = revalidate_phase1_model(
                    raw_response,
                    ModelResponse,
                )
                if (
                    response.request_id != model_request.request_id
                    or response.run_id != model_request.run_id
                    or response.agent_id != model_request.agent_id
                    or response.incident_id != model_request.incident_id
                    or response.task_id != model_request.task_id
                    or response.model_name != model_request.model_name
                    or response.error_code is not None
                ):
                    raise ValueError("model response identity mismatch")
            except Exception:
                code = StableErrorCode.MODEL_PROTOCOL_VIOLATION
                model_records.append(
                    _record_model_error(
                        model_request,
                        call_id=model_call_id,
                        started_at=model_started_at,
                        monotonic_start=model_monotonic_start,
                        error_code=code,
                    )
                )
                terminal_reason = AgentTerminalReason.MODEL_RESPONSE_INVALID
                terminal_error_code = code
                break

            if (
                response.started_at < model_started_at
                or response.ended_at > model_ended_at
                or response.monotonic_duration_seconds
                > model_elapsed + MODEL_TIMING_TOLERANCE_SECONDS
            ):
                code = StableErrorCode.MODEL_PROTOCOL_VIOLATION
                sanitized_response = ModelResponse(
                    schema_version="phase1.model-response.v1",
                    request_id=response.request_id,
                    response_id=response.response_id,
                    run_id=response.run_id,
                    agent_id=response.agent_id,
                    incident_id=response.incident_id,
                    task_id=response.task_id,
                    provider_name=response.provider_name,
                    model_name=response.model_name,
                    action=response.action,
                    usage=response.usage,
                    started_at=model_started_at,
                    ended_at=model_ended_at,
                    monotonic_duration_seconds=model_elapsed,
                    error_code=code,
                )
                model_records.append(
                    ModelCallRecord(
                        schema_version="phase1.model-call-record.v1",
                        call_id=model_call_id,
                        run_id=validated_request.run_id,
                        agent_id=validated_request.agent_id,
                        incident_id=(
                            validated_request.incident.incident_id
                        ),
                        task_id=validated_request.task_id,
                        request=model_request,
                        response=sanitized_response,
                        started_at=model_started_at,
                        ended_at=model_ended_at,
                        monotonic_duration_seconds=model_elapsed,
                        model_call_consumed=True,
                        charged_tokens=0,
                        status="ERROR",
                        error_code=code,
                    )
                )
                terminal_reason = AgentTerminalReason.MODEL_RESPONSE_INVALID
                terminal_error_code = code
                break

            if response.usage.total_tokens > budget.remaining_tokens:
                code = StableErrorCode.BUDGET_EXHAUSTED
                model_records.append(
                    ModelCallRecord(
                        schema_version="phase1.model-call-record.v1",
                        call_id=model_call_id,
                        run_id=validated_request.run_id,
                        agent_id=validated_request.agent_id,
                        incident_id=(
                            validated_request.incident.incident_id
                        ),
                        task_id=validated_request.task_id,
                        request=model_request,
                        response=response,
                        started_at=model_started_at,
                        ended_at=model_ended_at,
                        monotonic_duration_seconds=model_elapsed,
                        model_call_consumed=True,
                        charged_tokens=0,
                        status="ERROR",
                        error_code=code,
                    )
                )
                terminal_reason = AgentTerminalReason.TOKEN_BUDGET_EXHAUSTED
                terminal_error_code = code
                break
            budget.consume_tokens(response.usage.total_tokens)
            model_records.append(
                ModelCallRecord(
                    schema_version="phase1.model-call-record.v1",
                    call_id=model_call_id,
                    run_id=validated_request.run_id,
                    agent_id=validated_request.agent_id,
                    incident_id=validated_request.incident.incident_id,
                    task_id=validated_request.task_id,
                    request=model_request,
                    response=response,
                    started_at=model_started_at,
                    ended_at=model_ended_at,
                    monotonic_duration_seconds=model_elapsed,
                    model_call_consumed=True,
                    charged_tokens=response.usage.total_tokens,
                    status="OK",
                    error_code=None,
                )
            )

            action = response.action
            if type(action) is FinalAction:
                try:
                    candidate = revalidate_phase1_model(
                        action.result,
                        RCAResult,
                    )
                    final_rca = validate_rca_result(
                        candidate,
                        store,
                        validated_request.incident,
                    )
                except EvidenceValidationError:
                    terminal_reason = AgentTerminalReason.FINAL_RCA_INVALID
                    terminal_error_code = (
                        StableErrorCode.MODEL_PROTOCOL_VIOLATION
                    )
                    break
                terminal_status = AgentTerminalStatus.COMPLETED
                terminal_reason = AgentTerminalReason.FINAL_RCA_ACCEPTED
                terminal_error_code = None
                continue

            if budget.remaining_tool_calls <= 0:
                terminal_reason = (
                    AgentTerminalReason.TOOL_CALL_BUDGET_EXHAUSTED
                )
                terminal_error_code = StableErrorCode.BUDGET_EXHAUSTED
                break
            tool_action = cast(ToolAction, action)
            tool_sequence += 1
            tool_started_at = datetime.now(UTC)
            tool_monotonic_start = time.monotonic()
            evidence_before = len(store.snapshot())
            tool_calls_before = budget.snapshot().tool_calls
            dispatch_observer = _DispatchObserver(self._backend)
            tool_context = ToolContext(
                incident=validated_request.incident,
                evidence_store=store,
                budget=budget,
                backend=dispatch_observer,
                timeout_seconds=self._tool_timeout_seconds,
            )
            result = _dispatch_tool(tool_context, tool_action)
            tool_ended_at = datetime.now(UTC)
            tool_elapsed = time.monotonic() - tool_monotonic_start
            evidence_after = store.snapshot()
            new_evidence: tuple[Evidence, ...] = evidence_after[evidence_before:]
            tool_call_delta = (
                budget.snapshot().tool_calls - tool_calls_before
            )
            actual_budget_consumed = tool_call_delta == 1
            observed_dispatch = dispatch_observer.calls == 1
            confirmed_dispatched = (
                actual_budget_consumed and observed_dispatch
            )
            actual_refs = tuple(
                item.evidence_ref for item in new_evidence
            )
            status: Literal["OK", "ERROR"] = (
                "OK" if result.status is ToolStatus.OK else "ERROR"
            )
            declaration_mismatch = (
                tool_call_delta not in {0, 1}
                or dispatch_observer.calls not in {0, 1}
                or result.budget_consumed != actual_budget_consumed
                or result.dispatched != confirmed_dispatched
                or (
                    status == "OK"
                    and result.evidence_refs != actual_refs
                )
                or (status == "ERROR" and bool(result.evidence_refs))
            )
            if declaration_mismatch:
                tool_records.append(
                    ToolCallRecord(
                        schema_version="phase1.tool-call-record.v1",
                        call_id=f"tool-call-{tool_sequence:04d}",
                        run_id=validated_request.run_id,
                        agent_id=validated_request.agent_id,
                        incident_id=validated_request.incident.incident_id,
                        task_id=validated_request.task_id,
                        tool_name=_tool_name(tool_action),
                        action=tool_action,
                        evidence=new_evidence,
                        evidence_refs=actual_refs,
                        started_at=tool_started_at,
                        ended_at=tool_ended_at,
                        monotonic_duration_seconds=tool_elapsed,
                        budget_consumed=actual_budget_consumed,
                        dispatched=confirmed_dispatched,
                        evidence_quarantined=True,
                        usable=False,
                        status="ERROR",
                        error_code=(
                            StableErrorCode.INTERNAL_CONTRACT_VIOLATION
                        ),
                    )
                )
                terminal_reason = (
                    AgentTerminalReason.TOOL_EVIDENCE_ALLOCATION_INVALID
                )
                terminal_error_code = (
                    StableErrorCode.INTERNAL_CONTRACT_VIOLATION
                )
                break
            if status == "ERROR" and new_evidence:
                tool_records.append(
                    ToolCallRecord(
                        schema_version="phase1.tool-call-record.v1",
                        call_id=f"tool-call-{tool_sequence:04d}",
                        run_id=validated_request.run_id,
                        agent_id=validated_request.agent_id,
                        incident_id=validated_request.incident.incident_id,
                        task_id=validated_request.task_id,
                        tool_name=_tool_name(tool_action),
                        action=tool_action,
                        evidence=new_evidence,
                        evidence_refs=actual_refs,
                        started_at=tool_started_at,
                        ended_at=tool_ended_at,
                        monotonic_duration_seconds=tool_elapsed,
                        budget_consumed=actual_budget_consumed,
                        dispatched=confirmed_dispatched,
                        evidence_quarantined=True,
                        usable=False,
                        status="ERROR",
                        error_code=(
                            StableErrorCode.INTERNAL_CONTRACT_VIOLATION
                        ),
                    )
                )
                terminal_reason = (
                    AgentTerminalReason.FAILED_TOOL_PERSISTED_EVIDENCE
                )
                terminal_error_code = (
                    StableErrorCode.INTERNAL_CONTRACT_VIOLATION
                )
                break
            tool_records.append(
                ToolCallRecord(
                    schema_version="phase1.tool-call-record.v1",
                    call_id=f"tool-call-{tool_sequence:04d}",
                    run_id=validated_request.run_id,
                    agent_id=validated_request.agent_id,
                    incident_id=validated_request.incident.incident_id,
                    task_id=validated_request.task_id,
                    tool_name=_tool_name(tool_action),
                    action=tool_action,
                    evidence=new_evidence,
                    evidence_refs=result.evidence_refs,
                    started_at=tool_started_at,
                    ended_at=tool_ended_at,
                    monotonic_duration_seconds=tool_elapsed,
                    budget_consumed=actual_budget_consumed,
                    dispatched=confirmed_dispatched,
                    evidence_quarantined=False,
                    usable=status == "OK",
                    status=status,
                    error_code=result.error_code,
                )
            )

        if final_rca is not None:
            final_rca = validate_rca_result(
                final_rca,
                store,
                validated_request.incident,
            )
        report = AgentRunReport(
            schema_version="phase1.agent-run-report.v1",
            run_id=validated_request.run_id,
            request=validated_request,
            model_configuration=self._model_configuration,
            final_rca=final_rca,
            model_call_records=tuple(model_records),
            tool_call_records=tuple(tool_records),
            evidence_index=store.snapshot(),
            budget_limits=validated_request.budgets,
            budget_snapshot=budget.snapshot(),
            started_at=run_started_at,
            ended_at=datetime.now(UTC),
            monotonic_duration_seconds=time.monotonic() - run_monotonic_start,
            terminal_status=terminal_status,
            terminal_reason=terminal_reason,
            terminal_error_code=terminal_error_code,
            schema_valid=True,
            evidence_references_valid=True,
        )
        return validate_agent_report(
            report,
            store,
            validated_request.incident,
        )
