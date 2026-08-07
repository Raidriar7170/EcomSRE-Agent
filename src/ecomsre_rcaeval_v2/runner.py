"""Create-once execution for the three RCAEval RE2 v2 architecture arms."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Literal, Protocol, TypeVar

from pydantic import ValidationError

from ecomsre.model.gateway import ProviderProtocolError
from ecomsre_rcaeval.adapter import (
    ArchitectureContext,
    ArchitectureContextBuilder,
    IncidentManifest,
    SOURCE_ORDER,
    SourceName,
    incident_for_case,
)
from ecomsre_rcaeval.contracts import (
    Architecture,
    CommanderDecision,
    SpecialistAssessment,
)
from ecomsre_rcaeval.dataset import TelemetryCase
from ecomsre_rcaeval.normalization import UnresolvedServiceAlias
from ecomsre_rcaeval.provider import ProviderDiagnosisError
from ecomsre_rcaeval_v2.contracts import (
    ArchitectureV2,
    BoundedEvidenceSnapshotV2,
    CommanderDecisionV2,
    CommanderInputSnapshotV2,
    CommanderOperationRecord,
    DiagnosisV2,
    IncidentSnapshotV2,
    IndicatorCandidateSnapshotV2,
    IndicatorResolutionRecord,
    JudgeInputSnapshotV2,
    JudgeOperationRecord,
    JudgeServiceDecisionV2,
    OperationFailureCode,
    OperationStage,
    OperationStatus,
    OperationType,
    ProviderUsageDelta,
    ResolverInputSnapshotV2,
    SafeValidationError,
    SourceObservationSnapshotV2,
    SpecialistAssessmentV2,
    SpecialistInputSnapshotV2,
    SpecialistOperationRecord,
    TerminalDispositionV2,
    TerminalRecordV2,
)
from ecomsre_rcaeval_v2.indicator import (
    FormulaId,
    LoadedIndicatorConfig,
    MetricIndicatorCandidate,
    resolve_indicator,
)
from ecomsre_rcaeval_v2.indicator_evaluation import (
    build_runtime_metric_candidates,
)
from ecomsre_rcaeval_v2.observability import (
    OperationTransaction,
    RunJournalV2,
    execute_run_once,
    write_private_snapshot_create_once,
)
from ecomsre_rcaeval_v2.privacy import (
    sanitize_agent_visible_text,
    scan_agent_visible_payload,
)
from ecomsre_rcaeval_v2.provider import (
    ProviderOutputValidationError,
    ProviderCallDelta,
    ProviderCounterSnapshot,
    safe_validation_error_from_exception,
)
from ecomsre_rcaeval_v2.schedule import ScheduleRecord, Variant


_ARCHITECTURES: dict[Variant, tuple[Architecture, ArchitectureV2]] = {
    Variant.SINGLE_V2: (Architecture.SINGLE, "single_v2"),
    Variant.FIXED_V2: (Architecture.FIXED, "fixed_v2"),
    Variant.DYNAMIC_V2: (Architecture.DYNAMIC, "dynamic_v2"),
}
_SOURCE_OPERATION = {
    "metrics": OperationType.METRICS_SPECIALIST,
    "logs": OperationType.LOGS_SPECIALIST,
    "traces": OperationType.TRACES_SPECIALIST,
}
_SOURCE_PREFIX = {
    "metric": "metrics",
    "log": "logs",
    "trace": "traces",
}


class ObservableDiagnosisProvider(Protocol):
    def usage_snapshot(self) -> ProviderCounterSnapshot: ...

    def usage_delta_since(
        self, before: ProviderCounterSnapshot
    ) -> ProviderCallDelta: ...

    def specialize(
        self,
        incident: IncidentManifest,
        context: ArchitectureContext,
        source: SourceName,
        *,
        before_output_validation: Callable[[], None] | None = None,
    ) -> SpecialistAssessment: ...

    def plan_followup(
        self,
        incident: IncidentManifest,
        context: ArchitectureContext,
        metrics_assessment: SpecialistAssessment,
        *,
        before_output_validation: Callable[[], None] | None = None,
    ) -> CommanderDecision: ...

    def judge(
        self,
        judge_input: JudgeInputSnapshotV2,
        architecture: ArchitectureV2,
        *,
        before_output_validation: Callable[[], None] | None = None,
    ) -> JudgeServiceDecisionV2: ...


OutputT = TypeVar("OutputT")


def _snapshot_stem(index: int, operation_type: OperationType, suffix: str) -> str:
    return f"{index:04d}-{operation_type.value.casefold().replace('_', '-')}-{suffix}"


def _zero_usage() -> ProviderUsageDelta:
    return ProviderUsageDelta(
        model_calls_delta=0,
        prompt_tokens_delta=0,
        completion_tokens_delta=0,
        total_tokens_delta=0,
    )


def _failure(error: Exception) -> tuple[OperationStatus, OperationFailureCode]:
    if isinstance(error, TimeoutError):
        return OperationStatus.TIMEOUT, OperationFailureCode.PROVIDER_TIMEOUT
    if isinstance(error, ConnectionError):
        return (
            OperationStatus.PROVIDER_FAILURE,
            OperationFailureCode.PROVIDER_TRANSPORT_FAILURE,
        )
    if isinstance(error, UnresolvedServiceAlias):
        return (
            OperationStatus.INVALID_SCHEMA,
            OperationFailureCode.PROVIDER_OUTPUT_UNRESOLVED_SERVICE_ALIAS,
        )
    if isinstance(error, ProviderDiagnosisError):
        return (
            OperationStatus.INVALID_SCHEMA,
            OperationFailureCode.PROVIDER_OUTPUT_INVALID_SCHEMA,
        )
    if isinstance(error, ProviderProtocolError):
        return (
            OperationStatus.PROTOCOL_VIOLATION,
            OperationFailureCode.PROVIDER_PROTOCOL_VIOLATION,
        )
    if isinstance(error, (TypeError, ValidationError)):
        return (
            OperationStatus.INVALID_SCHEMA,
            OperationFailureCode.PROVIDER_OUTPUT_INVALID_SCHEMA,
        )
    return (
        OperationStatus.PROTOCOL_VIOLATION,
        OperationFailureCode.RUNTIME_CONTRACT_VIOLATION,
    )


def _incident_snapshot(incident: IncidentManifest) -> IncidentSnapshotV2:
    return IncidentSnapshotV2(
        incident_id=incident.case_id,
        system=incident.system,  # type: ignore[arg-type]
        anomaly_timestamp=incident.anomaly_timestamp,
        modalities=incident.modalities,
        summary=incident.incident,
    )


def _source_for_evidence(reference: str) -> SourceName:
    prefix, separator, _sequence = reference.partition(":")
    if separator != ":" or prefix not in _SOURCE_PREFIX:
        raise ValueError("bounded evidence reference has an invalid source")
    return _SOURCE_PREFIX[prefix]  # type: ignore[return-value]


def _sanitize_context(context: ArchitectureContext) -> ArchitectureContext:
    def clean(value: str) -> str:
        return sanitize_agent_visible_text(value).value

    return context.model_copy(
        update={
            "evidence": tuple(
                item.model_copy(update={"summary": clean(item.summary)})
                for item in context.evidence
            ),
            "canonical_evidence": tuple(
                item.model_copy(update={"summary": clean(item.summary)})
                for item in context.canonical_evidence
            ),
            "specialist_assessments": tuple(
                item.model_copy(update={"summary": clean(item.summary)})
                for item in context.specialist_assessments
            ),
            "source_observations": tuple(
                item.model_copy(
                    update={
                        "reason": (None if item.reason is None else clean(item.reason))
                    }
                )
                for item in context.source_observations
            ),
            "commander_stages": tuple(
                item.model_copy(update={"rationale": clean(item.rationale)})
                for item in context.commander_stages
            ),
        }
    )


def _assert_sanitized(value: object) -> None:
    scan = scan_agent_visible_payload(value)
    if scan.path_hit_count:
        raise ValueError("sanitized Agent-visible payload retained a local path")


def _observations(
    context: ArchitectureContext,
) -> tuple[SourceObservationSnapshotV2, ...]:
    result: list[SourceObservationSnapshotV2] = []
    for observation in context.source_observations:
        references = tuple(
            item.evidence_id
            for item in context.evidence
            if _source_for_evidence(item.evidence_id) == observation.source
        )
        summary = observation.reason or (
            f"Bounded {observation.source} telemetry is available."
        )
        result.append(
            SourceObservationSnapshotV2(
                source=observation.source,
                status=observation.status.value,
                summary=summary,
                evidence_refs=references,
            )
        )
    return tuple(result)


def _bounded_evidence(
    context: ArchitectureContext,
    source: SourceName | None = None,
) -> tuple[BoundedEvidenceSnapshotV2, ...]:
    return tuple(
        BoundedEvidenceSnapshotV2(
            evidence_ref=item.evidence_id,
            source=_source_for_evidence(item.evidence_id),
            service=item.service,
            observation=item.summary,
        )
        for item in context.evidence
        if source is None or _source_for_evidence(item.evidence_id) == source
    )


def _assessment_v2(value: SpecialistAssessment) -> SpecialistAssessmentV2:
    return SpecialistAssessmentV2(
        source=value.source,
        candidate_service=value.candidate_service,
        candidate_indicator=value.candidate_indicator,
        confidence=value.confidence,
        supporting_evidence_refs=value.evidence_refs,
        contradicting_evidence_refs=(),
        summary=value.summary,
    )


def _commander_v2(value: CommanderDecision) -> CommanderDecisionV2:
    return CommanderDecisionV2(
        selected_sources=value.selected_sources,
        rationale=value.rationale,
    )


def _ranked_candidates(
    case: TelemetryCase,
    *,
    case_identity_sha256: str,
    formula: FormulaId,
    config: LoadedIndicatorConfig,
) -> tuple[MetricIndicatorCandidate, ...]:
    ranked = build_runtime_metric_candidates(
        case,
        case_identity_sha256=case_identity_sha256,
        formula=formula,
        config=config,
    )
    return ranked[:6]


def _candidate_snapshots(
    ranked: tuple[MetricIndicatorCandidate, ...],
) -> tuple[IndicatorCandidateSnapshotV2, ...]:
    return tuple(
        IndicatorCandidateSnapshotV2(
            service=item.service,
            canonical_indicator=item.canonical_indicator,
            metric_name=item.metric_name,
            score=item.score,
            evidence_ref=item.evidence_ref,
        )
        for item in ranked
    )


def _terminal_failure(
    operation_type: OperationType,
    operation_index: int,
    status: OperationStatus,
    failure_code: OperationFailureCode,
    failure_stage: OperationStage,
    *,
    tool_calls: int,
) -> TerminalDispositionV2:
    return TerminalDispositionV2(
        terminal_status=status,
        failure_operation_type=operation_type,
        failure_operation_index=operation_index,
        failure_code=failure_code,
        failure_stage=failure_stage,
        diagnosis=None,
        tool_calls=tool_calls,
    )


def _provider_result(
    provider: ObservableDiagnosisProvider,
    action: Callable[[Callable[[], None]], OutputT],
    convert: Callable[[OutputT], object],
    transaction: OperationTransaction,
) -> tuple[
    object | None,
    ProviderCallDelta,
    OperationStatus,
    OperationFailureCode | None,
    SafeValidationError | None,
    OperationStage | None,
]:
    before = provider.usage_snapshot()
    validation_started = False

    def begin_output_validation() -> None:
        nonlocal validation_started
        if not validation_started:
            transaction.start_stage(OperationStage.OUTPUT_VALIDATION)
            validation_started = True

    try:
        raw_output = action(begin_output_validation)
        begin_output_validation()
        output = convert(raw_output)
    except Exception as error:
        if (
            isinstance(
                error,
                (
                    ProviderDiagnosisError,
                    ProviderOutputValidationError,
                    ProviderProtocolError,
                    UnresolvedServiceAlias,
                    TypeError,
                    ValidationError,
                    ValueError,
                ),
            )
            and not validation_started
        ):
            begin_output_validation()
        delta = provider.usage_delta_since(before)
        status, failure_code = _failure(error)
        safe_error = (
            error.safe_validation_error
            if isinstance(error, ProviderOutputValidationError)
            else (
                safe_validation_error_from_exception(error)
                if validation_started
                else None
            )
        )
        return (
            None,
            delta,
            status,
            failure_code,
            safe_error,
            transaction.current_stage,
        )
    delta = provider.usage_delta_since(before)
    if delta.usage.model_calls_delta != 1 or delta.provider_call_index is None:
        return (
            None,
            delta,
            OperationStatus.PROTOCOL_VIOLATION,
            OperationFailureCode.RUNTIME_CONTRACT_VIOLATION,
            None,
            transaction.current_stage,
        )
    return output, delta, OperationStatus.COMPLETED, None, None, None


def _run_v2(
    journal: RunJournalV2,
    *,
    case: TelemetryCase,
    provider: ObservableDiagnosisProvider,
    v1_architecture: Architecture,
    v2_architecture: ArchitectureV2,
    case_identity_sha256: str,
    indicator_formula: FormulaId,
    indicator_config: LoadedIndicatorConfig,
) -> TerminalDispositionV2:
    builder = ArchitectureContextBuilder(case, v1_architecture, run_id=journal.run_id)
    operation_index = 0
    assessments_v1: list[SpecialistAssessment] = []
    assessments_v2: list[SpecialistAssessmentV2] = []
    commander_v1: CommanderDecision | None = None
    commander_v2: CommanderDecisionV2 | None = None
    ranked_candidates: tuple[MetricIndicatorCandidate, ...] | None = None
    indicator_candidates: tuple[IndicatorCandidateSnapshotV2, ...] | None = None

    def next_index() -> int:
        nonlocal operation_index
        operation_index += 1
        return operation_index

    def safe_error(
        error: Exception, stage: OperationStage
    ) -> SafeValidationError | None:
        if isinstance(error, ProviderOutputValidationError):
            return error.safe_validation_error
        if stage in {
            OperationStage.INPUT_SANITIZATION,
            OperationStage.INPUT_CONSTRUCTION,
            OperationStage.OUTPUT_VALIDATION,
        }:
            if (
                stage is OperationStage.INPUT_SANITIZATION
                and "retained a local path" in str(error)
            ):
                return SafeValidationError(
                    error_class="LeakageScanError",
                    field_paths=("agent_visible_payload",),
                    constraint_types=("agent_visible_private_path",),
                    error_count=1,
                )
            return safe_validation_error_from_exception(error)
        return None

    def status_code(
        error: Exception, stage: OperationStage
    ) -> tuple[OperationStatus, OperationFailureCode]:
        if (
            stage is OperationStage.INPUT_SANITIZATION
            and "retained a local path" in str(error)
        ):
            return (
                OperationStatus.PROTOCOL_VIOLATION,
                OperationFailureCode.AGENT_VISIBLE_PRIVATE_PATH_REMAINED,
            )
        return _failure(error)

    def common_fields(
        *,
        index: int,
        operation_type: OperationType,
        source: SourceName | None,
        started_at: datetime,
        started: float,
        transaction: OperationTransaction,
        status: OperationStatus,
        failure_code: OperationFailureCode | None,
        failure_stage: OperationStage | None,
        validation_error: SafeValidationError | None,
        input_sha: str | None,
        output_sha: str | None,
        usage: ProviderUsageDelta,
        provider_call_index: int | None,
        context: ArchitectureContext | None,
        selected_sources: tuple[Literal["logs", "traces"], ...] = (),
    ) -> dict[str, object]:
        return {
            "schema_version": "rcaeval-re2-v2.operation-record.v1",
            "run_id": journal.run_id,
            "case_id": journal.case_id,
            "system": journal.system,
            "architecture": journal.architecture,
            "operation_index": index,
            "operation_type": operation_type,
            "source": source,
            "started_at_utc": started_at,
            "ended_at_utc": datetime.now(timezone.utc),
            "latency_ms": float(max(0.0, (monotonic() - started) * 1_000)),
            "status": status,
            "failure_code": failure_code,
            "failure_stage": failure_stage,
            "last_completed_stage": (
                OperationStage.OUTPUT_PERSISTENCE
                if status is OperationStatus.COMPLETED
                else transaction.last_completed_stage
            ),
            "stage_trace_sha256": transaction.stage_trace_sha256(),
            "safe_validation_error": validation_error,
            "provider_call_index": provider_call_index,
            "input_snapshot_sha256": input_sha,
            "output_snapshot_sha256": output_sha,
            "usage_delta": usage,
            "investigated_sources": (
                () if context is None else context.investigated_sources
            ),
            "evidence_refs_visible_to_operation": (
                ()
                if context is None
                else tuple(item.evidence_id for item in context.evidence)
            ),
            "selected_sources": selected_sources,
        }

    def run_specialist(
        source: SourceName,
        context_factory: Callable[[], ArchitectureContext],
    ) -> TerminalDispositionV2 | None:
        index = next_index()
        operation_type = _SOURCE_OPERATION[source]
        raw_values: list[SpecialistAssessment] = []

        def callback(
            transaction: OperationTransaction,
        ) -> SpecialistOperationRecord:
            started_at = datetime.now(timezone.utc)
            started = monotonic()
            context: ArchitectureContext | None = None
            input_sha: str | None = None
            failure_code: OperationFailureCode | None
            try:
                transaction.start_stage(OperationStage.INPUT_SANITIZATION)
                incident = incident_for_case(case)
                context = _sanitize_context(context_factory())
                _assert_sanitized(context.model_dump(mode="json"))
                transaction.start_stage(OperationStage.INPUT_CONSTRUCTION)
                source_observation = next(
                    item for item in _observations(context) if item.source == source
                )
                input_snapshot = SpecialistInputSnapshotV2(
                    incident=_incident_snapshot(incident),
                    architecture=v2_architecture,
                    source=source,
                    source_observation=source_observation,
                    bounded_evidence=_bounded_evidence(context, source),
                )
                transaction.start_stage(OperationStage.INPUT_PERSISTENCE)
                input_sha = write_private_snapshot_create_once(
                    journal.run_root,
                    _snapshot_stem(index, operation_type, "input"),
                    input_snapshot,
                )
            except Exception as error:
                stage = transaction.current_stage or OperationStage.INPUT_SANITIZATION
                status, failure_code = status_code(error, stage)
                return SpecialistOperationRecord.model_validate(
                    {
                        **common_fields(
                            index=index,
                            operation_type=operation_type,
                            source=source,
                            started_at=started_at,
                            started=started,
                            transaction=transaction,
                            status=status,
                            failure_code=failure_code,
                            failure_stage=stage,
                            validation_error=safe_error(error, stage),
                            input_sha=input_sha,
                            output_sha=None,
                            usage=_zero_usage(),
                            provider_call_index=None,
                            context=context,
                        ),
                        "typed_output": None,
                    }
                )
            assert context is not None
            transaction.start_stage(OperationStage.PROVIDER_CALL)

            def call_and_convert(
                begin_output_validation: Callable[[], None],
            ) -> SpecialistAssessment:
                raw_value = provider.specialize(
                    incident,
                    context,
                    source,
                    before_output_validation=begin_output_validation,
                )
                raw_values.append(raw_value)
                return raw_value

            result, delta, status, failure_code, validation_error, failure_stage = (
                _provider_result(
                    provider,
                    call_and_convert,
                    _assessment_v2,
                    transaction,
                )
            )
            typed_output = (
                result if isinstance(result, SpecialistAssessmentV2) else None
            )
            output_sha: str | None = None
            if typed_output is not None:
                try:
                    transaction.start_stage(OperationStage.OUTPUT_PERSISTENCE)
                    output_sha = write_private_snapshot_create_once(
                        journal.run_root,
                        _snapshot_stem(index, operation_type, "output"),
                        typed_output,
                    )
                except Exception as error:
                    status, failure_code = status_code(
                        error, OperationStage.OUTPUT_PERSISTENCE
                    )
                    failure_stage = OperationStage.OUTPUT_PERSISTENCE
                    validation_error = None
                    typed_output = None
            return SpecialistOperationRecord.model_validate(
                {
                    **common_fields(
                        index=index,
                        operation_type=operation_type,
                        source=source,
                        started_at=started_at,
                        started=started,
                        transaction=transaction,
                        status=status,
                        failure_code=failure_code,
                        failure_stage=failure_stage,
                        validation_error=validation_error,
                        input_sha=input_sha,
                        output_sha=output_sha,
                        usage=delta.usage,
                        provider_call_index=delta.provider_call_index,
                        context=context,
                    ),
                    "typed_output": typed_output,
                }
            )

        record = journal.record_operation(index, operation_type, callback)
        if record.status is not OperationStatus.COMPLETED:
            assert record.failure_code is not None
            return _terminal_failure(
                operation_type,
                index,
                record.status,
                record.failure_code,
                record.failure_stage or OperationStage.INPUT_SANITIZATION,
                tool_calls=builder.tool_call_count,
            )
        assert record.typed_output is not None
        assert raw_values
        assessments_v2.append(record.typed_output)
        assessments_v1.append(raw_values[0])
        return None

    def run_commander(
        context_factory: Callable[[], ArchitectureContext],
    ) -> TerminalDispositionV2 | None:
        nonlocal commander_v1, commander_v2
        index = next_index()
        operation_type = OperationType.COMMANDER
        raw_values: list[CommanderDecision] = []

        def callback(transaction: OperationTransaction) -> CommanderOperationRecord:
            started_at = datetime.now(timezone.utc)
            started = monotonic()
            context: ArchitectureContext | None = None
            input_sha: str | None = None
            failure_code: OperationFailureCode | None
            try:
                transaction.start_stage(OperationStage.INPUT_SANITIZATION)
                incident = incident_for_case(case)
                context = _sanitize_context(context_factory())
                _assert_sanitized(context.model_dump(mode="json"))
                metrics_v1 = assessments_v1[0]
                metrics_v2 = assessments_v2[0]
                transaction.start_stage(OperationStage.INPUT_CONSTRUCTION)
                input_snapshot = CommanderInputSnapshotV2(
                    incident=_incident_snapshot(incident),
                    metrics_assessment=metrics_v2,
                )
                transaction.start_stage(OperationStage.INPUT_PERSISTENCE)
                input_sha = write_private_snapshot_create_once(
                    journal.run_root,
                    _snapshot_stem(index, operation_type, "input"),
                    input_snapshot,
                )
            except Exception as error:
                stage = transaction.current_stage or OperationStage.INPUT_SANITIZATION
                status, failure_code = status_code(error, stage)
                return CommanderOperationRecord.model_validate(
                    {
                        **common_fields(
                            index=index,
                            operation_type=operation_type,
                            source=None,
                            started_at=started_at,
                            started=started,
                            transaction=transaction,
                            status=status,
                            failure_code=failure_code,
                            failure_stage=stage,
                            validation_error=safe_error(error, stage),
                            input_sha=input_sha,
                            output_sha=None,
                            usage=_zero_usage(),
                            provider_call_index=None,
                            context=context,
                        ),
                        "typed_output": None,
                    }
                )
            assert context is not None
            transaction.start_stage(OperationStage.PROVIDER_CALL)

            def call_and_convert(
                begin_output_validation: Callable[[], None],
            ) -> CommanderDecision:
                raw_value = provider.plan_followup(
                    incident,
                    context,
                    metrics_v1,
                    before_output_validation=begin_output_validation,
                )
                raw_values.append(raw_value)
                return raw_value

            result, delta, status, failure_code, validation_error, failure_stage = (
                _provider_result(
                    provider,
                    call_and_convert,
                    _commander_v2,
                    transaction,
                )
            )
            typed_output = result if isinstance(result, CommanderDecisionV2) else None
            output_sha: str | None = None
            if typed_output is not None:
                try:
                    transaction.start_stage(OperationStage.OUTPUT_PERSISTENCE)
                    output_sha = write_private_snapshot_create_once(
                        journal.run_root,
                        _snapshot_stem(index, operation_type, "output"),
                        typed_output,
                    )
                except Exception as error:
                    status, failure_code = status_code(
                        error, OperationStage.OUTPUT_PERSISTENCE
                    )
                    failure_stage = OperationStage.OUTPUT_PERSISTENCE
                    validation_error = None
                    typed_output = None
            return CommanderOperationRecord.model_validate(
                {
                    **common_fields(
                        index=index,
                        operation_type=operation_type,
                        source=None,
                        started_at=started_at,
                        started=started,
                        transaction=transaction,
                        status=status,
                        failure_code=failure_code,
                        failure_stage=failure_stage,
                        validation_error=validation_error,
                        input_sha=input_sha,
                        output_sha=output_sha,
                        usage=delta.usage,
                        provider_call_index=delta.provider_call_index,
                        context=context,
                        selected_sources=(
                            ()
                            if typed_output is None
                            else typed_output.selected_sources
                        ),
                    ),
                    "typed_output": typed_output,
                }
            )

        record = journal.record_operation(index, operation_type, callback)
        if record.status is not OperationStatus.COMPLETED:
            assert record.failure_code is not None
            return _terminal_failure(
                operation_type,
                index,
                record.status,
                record.failure_code,
                record.failure_stage or OperationStage.INPUT_SANITIZATION,
                tool_calls=builder.tool_call_count,
            )
        assert record.typed_output is not None
        assert raw_values
        commander_v2 = record.typed_output
        commander_v1 = raw_values[0]
        return None

    fixed_context: ArchitectureContext | None = None
    metrics_context: ArchitectureContext | None = None
    followup_context: ArchitectureContext | None = None

    def single_context_factory() -> ArchitectureContext:
        nonlocal fixed_context
        if fixed_context is None:
            for source in SOURCE_ORDER:
                builder.query_source(source)
            fixed_context = builder.snapshot()
        return fixed_context

    def fixed_context_factory() -> ArchitectureContext:
        return single_context_factory()

    def metrics_context_factory() -> ArchitectureContext:
        nonlocal metrics_context
        if metrics_context is None:
            builder.query_source("metrics")
            metrics_context = builder.snapshot()
        return metrics_context

    def followup_context_factory() -> ArchitectureContext:
        nonlocal followup_context
        if followup_context is None:
            assert commander_v1 is not None
            for selected_source in commander_v1.selected_sources:
                builder.query_source(selected_source)
            followup_context = builder.snapshot(
                specialist_assessments=(assessments_v1[0],),
                commander_decision=commander_v1,
            )
        return followup_context

    if v1_architecture is Architecture.FIXED:
        for source in SOURCE_ORDER:
            failure = run_specialist(source, fixed_context_factory)
            if failure is not None:
                return failure
    elif v1_architecture is Architecture.DYNAMIC:
        failure = run_specialist("metrics", metrics_context_factory)
        if failure is not None:
            return failure
        failure = run_commander(metrics_context_factory)
        if failure is not None:
            return failure
        assert commander_v1 is not None
        for source in commander_v1.selected_sources:
            failure = run_specialist(source, followup_context_factory)
            if failure is not None:
                return failure

    judge_index = next_index()
    judge_type = OperationType.FINAL_JUDGE
    final_context: ArchitectureContext | None = None

    def final_context_factory() -> ArchitectureContext:
        nonlocal final_context
        if final_context is None:
            if v1_architecture is Architecture.SINGLE:
                base = single_context_factory()
            else:
                base = builder.snapshot(
                    specialist_assessments=tuple(assessments_v1),
                    commander_decision=commander_v1,
                )
            final_context = base
        return final_context

    def judge_callback(transaction: OperationTransaction) -> JudgeOperationRecord:
        nonlocal ranked_candidates, indicator_candidates
        started_at = datetime.now(timezone.utc)
        started = monotonic()
        context: ArchitectureContext | None = None
        input_sha: str | None = None
        failure_code: OperationFailureCode | None
        try:
            transaction.start_stage(OperationStage.INPUT_SANITIZATION)
            context = _sanitize_context(final_context_factory())
            _assert_sanitized(context.model_dump(mode="json"))
            transaction.start_stage(OperationStage.INPUT_CONSTRUCTION)
            incident = incident_for_case(case)
            ranked_candidates = _ranked_candidates(
                case,
                case_identity_sha256=case_identity_sha256,
                formula=indicator_formula,
                config=indicator_config,
            )
            indicator_candidates = _candidate_snapshots(ranked_candidates)
            judge_input = JudgeInputSnapshotV2(
                incident=_incident_snapshot(incident),
                source_observations=_observations(context),
                bounded_evidence=_bounded_evidence(context),
                specialist_assessments=tuple(assessments_v2),
                commander_decision=commander_v2,
                indicator_candidates=indicator_candidates,
            )
            transaction.start_stage(OperationStage.INPUT_PERSISTENCE)
            input_sha = write_private_snapshot_create_once(
                journal.run_root,
                _snapshot_stem(judge_index, judge_type, "input"),
                judge_input,
            )
        except Exception as error:
            stage = transaction.current_stage or OperationStage.INPUT_SANITIZATION
            status, failure_code = status_code(error, stage)
            return JudgeOperationRecord.model_validate(
                {
                    **common_fields(
                        index=judge_index,
                        operation_type=judge_type,
                        source=None,
                        started_at=started_at,
                        started=started,
                        transaction=transaction,
                        status=status,
                        failure_code=failure_code,
                        failure_stage=stage,
                        validation_error=safe_error(error, stage),
                        input_sha=input_sha,
                        output_sha=None,
                        usage=_zero_usage(),
                        provider_call_index=None,
                        context=context,
                    ),
                    "typed_output": None,
                }
            )
        assert context is not None
        transaction.start_stage(OperationStage.PROVIDER_CALL)
        result, delta, status, failure_code, validation_error, failure_stage = (
            _provider_result(
                provider,
                lambda begin: provider.judge(
                    judge_input,
                    v2_architecture,
                    before_output_validation=begin,
                ),
                lambda value: value,
                transaction,
            )
        )
        output = result if isinstance(result, JudgeServiceDecisionV2) else None
        output_sha: str | None = None
        if output is not None:
            try:
                transaction.start_stage(OperationStage.OUTPUT_PERSISTENCE)
                output_sha = write_private_snapshot_create_once(
                    journal.run_root,
                    _snapshot_stem(judge_index, judge_type, "output"),
                    output,
                )
            except Exception as error:
                status, failure_code = status_code(
                    error, OperationStage.OUTPUT_PERSISTENCE
                )
                failure_stage = OperationStage.OUTPUT_PERSISTENCE
                validation_error = None
                output = None
        return JudgeOperationRecord.model_validate(
            {
                **common_fields(
                    index=judge_index,
                    operation_type=judge_type,
                    source=None,
                    started_at=started_at,
                    started=started,
                    transaction=transaction,
                    status=status,
                    failure_code=failure_code,
                    failure_stage=failure_stage,
                    validation_error=validation_error,
                    input_sha=input_sha,
                    output_sha=output_sha,
                    usage=delta.usage,
                    provider_call_index=delta.provider_call_index,
                    context=context,
                ),
                "typed_output": output,
            }
        )

    judge_record = journal.record_operation(judge_index, judge_type, judge_callback)
    if judge_record.status is not OperationStatus.COMPLETED:
        assert judge_record.failure_code is not None
        return _terminal_failure(
            judge_type,
            judge_index,
            judge_record.status,
            judge_record.failure_code,
            judge_record.failure_stage or OperationStage.INPUT_SANITIZATION,
            tool_calls=builder.tool_call_count,
        )
    assert judge_record.typed_output is not None
    judge_decision = judge_record.typed_output

    resolver_index = next_index()
    resolver_type = OperationType.INDICATOR_RESOLVER

    def resolver_callback(
        transaction: OperationTransaction,
    ) -> IndicatorResolutionRecord:
        started_at = datetime.now(timezone.utc)
        started = monotonic()
        input_sha: str | None = None
        resolution = None
        validation_error: SafeValidationError | None = None
        failure_code: OperationFailureCode | None = None
        try:
            transaction.start_stage(OperationStage.INPUT_SANITIZATION)
            if ranked_candidates is None or indicator_candidates is None:
                raise ValueError("Judge candidate state is unavailable")
            _assert_sanitized(
                [item.model_dump(mode="json") for item in indicator_candidates]
            )
            transaction.start_stage(OperationStage.INPUT_CONSTRUCTION)
            resolver_input = ResolverInputSnapshotV2(
                selected_service=judge_decision.root_cause_service,
                indicator_candidates=indicator_candidates,
            )
            transaction.start_stage(OperationStage.INPUT_PERSISTENCE)
            input_sha = write_private_snapshot_create_once(
                journal.run_root,
                _snapshot_stem(resolver_index, resolver_type, "input"),
                resolver_input,
            )
            transaction.start_stage(OperationStage.OUTPUT_VALIDATION)
            resolution = resolve_indicator(
                judge_decision.root_cause_service,
                ranked_candidates,
            )
            transaction.start_stage(OperationStage.OUTPUT_PERSISTENCE)
            status = OperationStatus.COMPLETED
            failure_code = None
            output_sha = write_private_snapshot_create_once(
                journal.run_root,
                _snapshot_stem(resolver_index, resolver_type, "output"),
                resolution,
            )
        except Exception as error:
            stage = transaction.current_stage or OperationStage.INPUT_SANITIZATION
            status, failure_code = status_code(error, stage)
            failure_stage: OperationStage | None = stage
            validation_error = safe_error(error, stage)
            output_sha = None
        else:
            failure_stage = None
        return IndicatorResolutionRecord.model_validate(
            {
                **common_fields(
                    index=resolver_index,
                    operation_type=resolver_type,
                    source=None,
                    started_at=started_at,
                    started=started,
                    transaction=transaction,
                    status=status,
                    failure_code=failure_code,
                    failure_stage=failure_stage,
                    validation_error=validation_error,
                    input_sha=input_sha,
                    output_sha=output_sha,
                    usage=_zero_usage(),
                    provider_call_index=None,
                    context=final_context,
                ),
                "typed_output": resolution,
            }
        )

    resolver_record = journal.record_operation(
        resolver_index, resolver_type, resolver_callback
    )
    if resolver_record.status is not OperationStatus.COMPLETED:
        assert resolver_record.failure_code is not None
        return _terminal_failure(
            resolver_type,
            resolver_index,
            resolver_record.status,
            resolver_record.failure_code,
            resolver_record.failure_stage or OperationStage.INPUT_SANITIZATION,
            tool_calls=builder.tool_call_count,
        )
    assert resolver_record.typed_output is not None
    resolution = resolver_record.typed_output
    diagnosis = DiagnosisV2(
        root_cause_service=judge_decision.root_cause_service,
        model_proposed_indicator=judge_decision.model_proposed_indicator,
        resolved_indicator=resolution.resolved_indicator,
        indicator_disposition=resolution.disposition,
        judge_evidence_refs=judge_decision.evidence_refs,
        indicator_evidence_ref=resolution.evidence_ref,
        confidence=judge_decision.confidence,
        explanation=judge_decision.explanation,
    )
    return TerminalDispositionV2(
        terminal_status=OperationStatus.COMPLETED,
        failure_operation_type=None,
        failure_operation_index=None,
        failure_code=None,
        failure_stage=None,
        diagnosis=diagnosis,
        tool_calls=builder.tool_call_count,
    )


def execute_v2_scheduled_once(
    scheduled: ScheduleRecord,
    case: TelemetryCase,
    *,
    case_identity_sha256: str,
    provider: ObservableDiagnosisProvider,
    indicator_formula: FormulaId,
    indicator_config: LoadedIndicatorConfig,
    run_root: Path,
) -> TerminalRecordV2:
    """Execute one v2 schedule row with no semantic retry or fallback."""

    if scheduled.variant not in _ARCHITECTURES:
        raise ValueError("v2 runner requires a v2 schedule variant")
    if scheduled.system != case.system:
        raise ValueError("scheduled run and telemetry system differ")
    v1_architecture, v2_architecture = _ARCHITECTURES[scheduled.variant]

    def run_callback(journal: RunJournalV2) -> TerminalDispositionV2:
        return _run_v2(
            journal,
            case=case,
            provider=provider,
            v1_architecture=v1_architecture,
            v2_architecture=v2_architecture,
            case_identity_sha256=case_identity_sha256,
            indicator_formula=indicator_formula,
            indicator_config=indicator_config,
        )

    return execute_run_once(
        run_root,
        run_id=scheduled.run_id,
        case_id=case.case_id,
        system=scheduled.identity.system,
        architecture=v2_architecture,
        started_at_utc=datetime.now(timezone.utc),
        callback=run_callback,
    )
    (SafeValidationError,)
