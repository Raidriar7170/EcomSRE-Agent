"""Create-once, no-semantic-retry execution for RCAEval architecture arms."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from time import monotonic
from typing import Protocol

from pydantic import ValidationError

from ecomsre_rcaeval.adapter import (
    ArchitectureContext,
    ArchitectureContextBuilder,
    IncidentManifest,
    SOURCE_ORDER,
    SourceName,
    incident_for_case,
)
from ecomsre_rcaeval.artifacts import canonical_json_bytes, read_json_object
from ecomsre_rcaeval.contracts import (
    Architecture,
    CommanderDecision,
    Diagnosis,
    ScheduledRun,
    SpecialistAssessment,
    TerminalRecord,
    TerminalStatus,
)
from ecomsre_rcaeval.dataset import TelemetryCase
from ecomsre_rcaeval.normalization import UnresolvedServiceAlias
from ecomsre_rcaeval.provider import ProviderDiagnosisError
from ecomsre.model.gateway import ProviderProtocolError
from ecomsre.phase1.budgets import BudgetExceeded, RunBudget
from ecomsre.phase1.contracts import BudgetLimits


@dataclass(frozen=True, slots=True)
class RCAEvalRunLimits:
    max_tool_calls: int = 8
    max_model_calls: int = 8
    max_total_tokens: int = 32_000
    overall_run_timeout_seconds: float = 45.0

    def __post_init__(self) -> None:
        if (
            self.max_tool_calls < 0
            or self.max_model_calls < 0
            or self.max_total_tokens < 0
            or self.overall_run_timeout_seconds <= 0
        ):
            raise ValueError("RCAEval run limits are invalid")


class DiagnosisProvider(Protocol):
    def specialize(
        self,
        incident: IncidentManifest,
        context: ArchitectureContext,
        source: SourceName,
    ) -> SpecialistAssessment: ...

    def plan_followup(
        self,
        incident: IncidentManifest,
        context: ArchitectureContext,
        metrics_assessment: SpecialistAssessment,
    ) -> CommanderDecision: ...

    def diagnose(
        self,
        incident: IncidentManifest,
        context: ArchitectureContext,
        architecture: Architecture,
    ) -> Diagnosis: ...


def _known_provider_tokens(provider: DiagnosisProvider) -> int | None:
    value = getattr(provider, "last_usage_tokens", None)
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("provider usage projection is invalid")
    return value


def _failure_record(
    scheduled: ScheduledRun,
    *,
    status: TerminalStatus,
    failure_code: str,
    tool_calls: int,
    model_calls: int,
    known_provider_tokens: int | None = None,
    started: float,
) -> TerminalRecord:
    return TerminalRecord(
        run_id=scheduled.run_id,
        case_id=scheduled.case_id,
        architecture=scheduled.architecture,
        terminal_status=status,
        diagnosis=None,
        failure_code=failure_code,
        tool_calls=tool_calls,
        model_calls=model_calls,
        known_provider_tokens=known_provider_tokens,
        latency_seconds=float(max(0.0, monotonic() - started)),
    )


class _OverallRunTimeout(TimeoutError):
    pass


def execute_scheduled(
    scheduled: ScheduledRun,
    case: TelemetryCase,
    provider: DiagnosisProvider,
    *,
    limits: RCAEvalRunLimits | None = None,
) -> TerminalRecord:
    """Execute actual provider operations for one isolated architecture run."""

    if case.case_id != scheduled.case_id:
        raise ValueError("scheduled run and RCAEval case identifiers differ")
    started = monotonic()
    active_limits = limits or RCAEvalRunLimits()
    budget = RunBudget(
        BudgetLimits(
            max_model_calls=active_limits.max_model_calls,
            max_tool_calls=active_limits.max_tool_calls,
            max_total_tokens=active_limits.max_total_tokens,
        )
    )
    builder = ArchitectureContextBuilder(
        case,
        scheduled.architecture,
        run_id=scheduled.run_id,
    )
    incident = incident_for_case(case)
    tool_calls = 0
    model_calls = 0
    accounted_tokens = 0

    def require_time() -> None:
        if monotonic() - started > active_limits.overall_run_timeout_seconds:
            raise _OverallRunTimeout("overall RCAEval run timeout")

    def query(source: SourceName) -> None:
        nonlocal tool_calls
        require_time()
        budget.consume_tool_call()
        tool_calls += 1
        builder.query_source(source)
        require_time()

    def model_call(operation):
        nonlocal accounted_tokens, model_calls
        require_time()
        budget.consume_model_call()
        model_calls += 1
        result = operation()
        observed = _known_provider_tokens(provider)
        if observed is not None:
            if observed < accounted_tokens:
                raise ValueError("provider cumulative usage decreased")
            budget.consume_tokens(observed - accounted_tokens)
            accounted_tokens = observed
        require_time()
        return result

    def failed(error: Exception) -> TerminalRecord:
        try:
            known = _known_provider_tokens(provider)
        except ValueError:
            known = None
        if isinstance(error, BudgetExceeded):
            status, code = (
                TerminalStatus.PROTOCOL_VIOLATION,
                "RUN_BUDGET_EXCEEDED",
            )
        elif isinstance(error, _OverallRunTimeout):
            status, code = TerminalStatus.TIMEOUT, "OVERALL_RUN_TIMEOUT"
        elif isinstance(error, TimeoutError):
            status, code = TerminalStatus.TIMEOUT, "PROVIDER_TIMEOUT"
        elif isinstance(error, ConnectionError):
            status, code = (
                TerminalStatus.PROVIDER_FAILURE,
                "PROVIDER_TRANSPORT_FAILURE",
            )
        elif isinstance(error, UnresolvedServiceAlias):
            status, code = (
                TerminalStatus.UNRESOLVED_ALIAS,
                "PROVIDER_OUTPUT_UNRESOLVED_SERVICE_ALIAS",
            )
        elif isinstance(error, ProviderDiagnosisError):
            status, code = (
                TerminalStatus.INVALID_SCHEMA,
                "PROVIDER_OUTPUT_INVALID_SCHEMA",
            )
        elif isinstance(error, ProviderProtocolError):
            status, code = (
                TerminalStatus.PROTOCOL_VIOLATION,
                "PROVIDER_PROTOCOL_VIOLATION",
            )
        elif isinstance(error, (TypeError, ValidationError)):
            status, code = (
                TerminalStatus.INVALID_SCHEMA,
                "PROVIDER_OUTPUT_INVALID_SCHEMA",
            )
        elif isinstance(error, ValueError):
            status, code = (
                TerminalStatus.PROTOCOL_VIOLATION,
                "RUNTIME_CONTRACT_VIOLATION",
            )
        else:
            status, code = TerminalStatus.PROVIDER_FAILURE, "PROVIDER_FAILURE"
        return _failure_record(
            scheduled,
            status=status,
            failure_code=code,
            tool_calls=tool_calls,
            model_calls=model_calls,
            known_provider_tokens=known,
            started=started,
        )

    try:
        assessments: list[SpecialistAssessment] = []
        commander: CommanderDecision | None = None
        if scheduled.architecture is Architecture.SINGLE:
            for source in SOURCE_ORDER:
                query(source)
        elif scheduled.architecture is Architecture.FIXED:
            for source in SOURCE_ORDER:
                query(source)
            source_context = builder.snapshot()
            for source in SOURCE_ORDER:
                assessment = model_call(
                    lambda selected=source: provider.specialize(
                        incident, source_context, selected
                    )
                )
                if not isinstance(assessment, SpecialistAssessment):
                    raise TypeError("provider returned a non-SpecialistAssessment value")
                assessments.append(assessment)
        else:
            query("metrics")
            metrics_context = builder.snapshot()
            metrics_assessment = model_call(
                lambda: provider.specialize(incident, metrics_context, "metrics")
            )
            if not isinstance(metrics_assessment, SpecialistAssessment):
                raise TypeError("provider returned a non-SpecialistAssessment value")
            assessments.append(metrics_assessment)
            commander = model_call(
                lambda: provider.plan_followup(
                    incident,
                    metrics_context,
                    metrics_assessment,
                )
            )
            if not isinstance(commander, CommanderDecision):
                raise TypeError("provider returned a non-CommanderDecision value")
            for source in commander.selected_sources:
                query(source)
            followup_context = builder.snapshot(
                specialist_assessments=(metrics_assessment,),
                commander_decision=commander,
            )
            for source in commander.selected_sources:
                assessment = model_call(
                    lambda selected=source: provider.specialize(
                        incident, followup_context, selected
                    )
                )
                if not isinstance(assessment, SpecialistAssessment):
                    raise TypeError("provider returned a non-SpecialistAssessment value")
                assessments.append(assessment)

        final_context = builder.snapshot(
            specialist_assessments=tuple(assessments),
            commander_decision=commander,
        )
        diagnosis = model_call(
            lambda: provider.diagnose(
                incident,
                final_context,
                scheduled.architecture,
            )
        )
        if not isinstance(diagnosis, Diagnosis):
            raise TypeError("provider returned a non-Diagnosis value")
        known_provider_tokens = _known_provider_tokens(provider)
    except Exception as error:
        return failed(error)

    return TerminalRecord(
        run_id=scheduled.run_id,
        case_id=scheduled.case_id,
        architecture=scheduled.architecture,
        terminal_status=TerminalStatus.COMPLETED,
        diagnosis=diagnosis,
        failure_code=None,
        tool_calls=tool_calls,
        model_calls=model_calls,
        known_provider_tokens=known_provider_tokens,
        latency_seconds=float(max(0.0, monotonic() - started)),
    )


def _record_path(journal_root: Path, run_id: str) -> Path:
    return journal_root / f"{run_id}.json"


def _attempt_path(journal_root: Path, run_id: str) -> Path:
    return journal_root.parent / f"{journal_root.name}.attempts" / f"{run_id}.json"


def _load_record(path: Path) -> TerminalRecord:
    try:
        return TerminalRecord.model_validate_json(
            canonical_json_bytes(read_json_object(path))
        )
    except (ValueError, ValidationError) as error:
        raise ValueError("terminal journal record is invalid") from error


def _durable_create(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def _write_record(path: Path, record: TerminalRecord) -> None:
    payload = (
        json.dumps(
            record.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    _durable_create(path, payload)


def _attempt_payload(scheduled: ScheduledRun) -> dict[str, object]:
    return {
        "schema_version": "rcaeval-re2.semantic-attempt.v1",
        "run_id": scheduled.run_id,
        "case_id": scheduled.case_id,
        "architecture": scheduled.architecture.value,
        "max_semantic_attempts": 1,
    }


def _write_attempt(path: Path, scheduled: ScheduledRun) -> None:
    payload = (
        json.dumps(
            _attempt_payload(scheduled),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    _durable_create(path, payload)


def _validate_attempt(path: Path, scheduled: ScheduledRun) -> None:
    if read_json_object(path) != _attempt_payload(scheduled):
        raise ValueError("semantic attempt marker differs from schedule")


def execute_scheduled_once(
    scheduled: ScheduledRun,
    case: TelemetryCase,
    provider: DiagnosisProvider,
    journal_root: Path,
) -> TerminalRecord:
    journal_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = _record_path(journal_root, scheduled.run_id)
    if path.exists():
        record = _load_record(path)
        if (
            record.run_id != scheduled.run_id
            or record.case_id != scheduled.case_id
            or record.architecture is not scheduled.architecture
        ):
            raise ValueError("terminal journal record differs from schedule")
        return record

    attempt_path = _attempt_path(journal_root, scheduled.run_id)
    if attempt_path.exists():
        _validate_attempt(attempt_path, scheduled)
        recovered = TerminalRecord(
            run_id=scheduled.run_id,
            case_id=scheduled.case_id,
            architecture=scheduled.architecture,
            terminal_status=TerminalStatus.PROTOCOL_VIOLATION,
            diagnosis=None,
            failure_code="STARTED_ATTEMPT_WITHOUT_TERMINAL",
            tool_calls=0,
            model_calls=1,
            known_provider_tokens=None,
            latency_seconds=0.0,
        )
        try:
            _write_record(path, recovered)
        except FileExistsError:
            return _load_record(path)
        return recovered

    _write_attempt(attempt_path, scheduled)
    record = execute_scheduled(scheduled, case, provider)
    try:
        _write_record(path, record)
    except FileExistsError:
        return _load_record(path)
    return record
