"""Create-once execution for the three RCAEval RE2 v2 architecture arms."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Protocol, TypeVar

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
    OperationStatus,
    OperationType,
    ProviderUsageDelta,
    ResolverInputSnapshotV2,
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
    RunJournalV2,
    execute_run_once,
    write_private_snapshot_create_once,
)
from ecomsre_rcaeval_v2.provider import (
    ProviderCallDelta,
    ProviderCounterSnapshot,
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
    ) -> SpecialistAssessment: ...

    def plan_followup(
        self,
        incident: IncidentManifest,
        context: ArchitectureContext,
        metrics_assessment: SpecialistAssessment,
    ) -> CommanderDecision: ...

    def judge(
        self,
        judge_input: JudgeInputSnapshotV2,
        architecture: ArchitectureV2,
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


def _candidate_snapshots(
    case: TelemetryCase,
    *,
    case_identity_sha256: str,
    formula: FormulaId,
    config: LoadedIndicatorConfig,
) -> tuple[
    tuple[MetricIndicatorCandidate, ...],
    tuple[IndicatorCandidateSnapshotV2, ...],
]:
    ranked = build_runtime_metric_candidates(
        case,
        case_identity_sha256=case_identity_sha256,
        formula=formula,
        config=config,
    )
    selected = ranked[:6]
    snapshots = tuple(
        IndicatorCandidateSnapshotV2(
            service=item.service,
            canonical_indicator=item.canonical_indicator,
            metric_name=item.metric_name,
            score=item.score,
            evidence_ref=item.evidence_ref,
        )
        for item in selected
    )
    return selected, snapshots


def _terminal_failure(
    operation_type: OperationType,
    operation_index: int,
    status: OperationStatus,
    failure_code: OperationFailureCode,
    *,
    tool_calls: int,
) -> TerminalDispositionV2:
    return TerminalDispositionV2(
        terminal_status=status,
        failure_operation_type=operation_type,
        failure_operation_index=operation_index,
        failure_code=failure_code,
        diagnosis=None,
        tool_calls=tool_calls,
    )


def _provider_result(
    provider: ObservableDiagnosisProvider,
    action: Callable[[], OutputT],
    expected_type: type[OutputT],
) -> tuple[
    OutputT | None,
    ProviderCallDelta,
    OperationStatus,
    OperationFailureCode | None,
]:
    before = provider.usage_snapshot()
    try:
        output = action()
        if not isinstance(output, expected_type):
            raise TypeError("provider returned an unexpected typed output")
    except Exception as error:
        delta = provider.usage_delta_since(before)
        status, failure_code = _failure(error)
        return None, delta, status, failure_code
    delta = provider.usage_delta_since(before)
    if delta.usage.model_calls_delta != 1 or delta.provider_call_index is None:
        return (
            None,
            delta,
            OperationStatus.PROTOCOL_VIOLATION,
            OperationFailureCode.RUNTIME_CONTRACT_VIOLATION,
        )
    return output, delta, OperationStatus.COMPLETED, None


def _run_v2(
    journal: RunJournalV2,
    *,
    case: TelemetryCase,
    provider: ObservableDiagnosisProvider,
    v1_architecture: Architecture,
    v2_architecture: ArchitectureV2,
    ranked_candidates: tuple[MetricIndicatorCandidate, ...],
    indicator_candidates: tuple[IndicatorCandidateSnapshotV2, ...],
) -> TerminalDispositionV2:
    builder = ArchitectureContextBuilder(
        case, v1_architecture, run_id=journal.run_id
    )
    incident = incident_for_case(case)
    incident_snapshot = _incident_snapshot(incident)
    operation_index = 0
    assessments_v1: list[SpecialistAssessment] = []
    assessments_v2: list[SpecialistAssessmentV2] = []
    commander_v1: CommanderDecision | None = None
    commander_v2: CommanderDecisionV2 | None = None

    def next_index() -> int:
        nonlocal operation_index
        operation_index += 1
        return operation_index

    def run_specialist(
        source: SourceName, context: ArchitectureContext
    ) -> TerminalDispositionV2 | None:
        index = next_index()
        operation_type = _SOURCE_OPERATION[source]
        source_observation = next(
            item for item in _observations(context) if item.source == source
        )
        input_snapshot = SpecialistInputSnapshotV2(
            incident=incident_snapshot,
            architecture=v2_architecture,
            source=source,
            source_observation=source_observation,
            bounded_evidence=_bounded_evidence(context, source),
        )
        input_sha = write_private_snapshot_create_once(
            journal.run_root,
            _snapshot_stem(index, operation_type, "input"),
            input_snapshot,
        )
        started_at = datetime.now(timezone.utc)
        started = monotonic()
        raw_values: list[SpecialistAssessment] = []

        def callback() -> SpecialistOperationRecord:
            def call_and_convert() -> SpecialistAssessmentV2:
                raw_value = provider.specialize(incident, context, source)
                raw_values.append(raw_value)
                return _assessment_v2(raw_value)

            typed_output, delta, status, failure_code = _provider_result(
                provider,
                call_and_convert,
                SpecialistAssessmentV2,
            )
            output_sha = (
                None
                if typed_output is None
                else write_private_snapshot_create_once(
                    journal.run_root,
                    _snapshot_stem(index, operation_type, "output"),
                    typed_output,
                )
            )
            return SpecialistOperationRecord(
                schema_version="rcaeval-re2-v2.operation-record.v1",
                run_id=journal.run_id,
                case_id=journal.case_id,
                system=journal.system,
                architecture=journal.architecture,
                operation_index=index,
                operation_type=operation_type,
                source=source,
                started_at_utc=started_at,
                ended_at_utc=datetime.now(timezone.utc),
                latency_ms=float(max(0.0, (monotonic() - started) * 1_000)),
                status=status,
                failure_code=failure_code,
                provider_call_index=delta.provider_call_index,
                input_snapshot_sha256=input_sha,
                output_snapshot_sha256=output_sha,
                usage_delta=delta.usage,
                investigated_sources=context.investigated_sources,
                evidence_refs_visible_to_operation=tuple(
                    item.evidence_id for item in context.evidence
                ),
                selected_sources=(),
                typed_output=typed_output,
            )

        record = journal.record_operation(index, operation_type, callback)
        if record.status is not OperationStatus.COMPLETED:
            assert record.failure_code is not None
            return _terminal_failure(
                operation_type,
                index,
                record.status,
                record.failure_code,
                tool_calls=builder.tool_call_count,
            )
        assert record.typed_output is not None
        assert raw_values
        assessments_v2.append(record.typed_output)
        assessments_v1.append(raw_values[0])
        return None

    def run_commander(context: ArchitectureContext) -> TerminalDispositionV2 | None:
        nonlocal commander_v1, commander_v2
        index = next_index()
        operation_type = OperationType.COMMANDER
        metrics_v1 = assessments_v1[0]
        metrics_v2 = assessments_v2[0]
        input_snapshot = CommanderInputSnapshotV2(
            incident=incident_snapshot,
            metrics_assessment=metrics_v2,
        )
        input_sha = write_private_snapshot_create_once(
            journal.run_root,
            _snapshot_stem(index, operation_type, "input"),
            input_snapshot,
        )
        started_at = datetime.now(timezone.utc)
        started = monotonic()
        raw_values: list[CommanderDecision] = []

        def callback() -> CommanderOperationRecord:
            def call_and_convert() -> CommanderDecisionV2:
                raw_value = provider.plan_followup(incident, context, metrics_v1)
                raw_values.append(raw_value)
                return _commander_v2(raw_value)

            typed_output, delta, status, failure_code = _provider_result(
                provider,
                call_and_convert,
                CommanderDecisionV2,
            )
            output_sha = (
                None
                if typed_output is None
                else write_private_snapshot_create_once(
                    journal.run_root,
                    _snapshot_stem(index, operation_type, "output"),
                    typed_output,
                )
            )
            return CommanderOperationRecord(
                schema_version="rcaeval-re2-v2.operation-record.v1",
                run_id=journal.run_id,
                case_id=journal.case_id,
                system=journal.system,
                architecture=journal.architecture,
                operation_index=index,
                operation_type=operation_type,
                source=None,
                started_at_utc=started_at,
                ended_at_utc=datetime.now(timezone.utc),
                latency_ms=float(max(0.0, (monotonic() - started) * 1_000)),
                status=status,
                failure_code=failure_code,
                provider_call_index=delta.provider_call_index,
                input_snapshot_sha256=input_sha,
                output_snapshot_sha256=output_sha,
                usage_delta=delta.usage,
                investigated_sources=context.investigated_sources,
                evidence_refs_visible_to_operation=tuple(
                    item.evidence_id for item in context.evidence
                ),
                selected_sources=(
                    () if typed_output is None else typed_output.selected_sources
                ),
                typed_output=typed_output,
            )

        record = journal.record_operation(index, operation_type, callback)
        if record.status is not OperationStatus.COMPLETED:
            assert record.failure_code is not None
            return _terminal_failure(
                operation_type,
                index,
                record.status,
                record.failure_code,
                tool_calls=builder.tool_call_count,
            )
        assert record.typed_output is not None
        assert raw_values
        commander_v2 = record.typed_output
        commander_v1 = raw_values[0]
        return None

    if v1_architecture is Architecture.SINGLE:
        for source in SOURCE_ORDER:
            builder.query_source(source)
    elif v1_architecture is Architecture.FIXED:
        for source in SOURCE_ORDER:
            builder.query_source(source)
        fixed_context = builder.snapshot()
        for source in SOURCE_ORDER:
            failure = run_specialist(source, fixed_context)
            if failure is not None:
                return failure
    else:
        builder.query_source("metrics")
        metrics_context = builder.snapshot()
        failure = run_specialist("metrics", metrics_context)
        if failure is not None:
            return failure
        failure = run_commander(metrics_context)
        if failure is not None:
            return failure
        assert commander_v1 is not None
        for source in commander_v1.selected_sources:
            builder.query_source(source)
        followup_context = builder.snapshot(
            specialist_assessments=(assessments_v1[0],),
            commander_decision=commander_v1,
        )
        for source in commander_v1.selected_sources:
            failure = run_specialist(source, followup_context)
            if failure is not None:
                return failure

    final_context = builder.snapshot(
        specialist_assessments=tuple(assessments_v1),
        commander_decision=commander_v1,
    )
    judge_input = JudgeInputSnapshotV2(
        incident=incident_snapshot,
        source_observations=_observations(final_context),
        bounded_evidence=_bounded_evidence(final_context),
        specialist_assessments=tuple(assessments_v2),
        commander_decision=commander_v2,
        indicator_candidates=indicator_candidates,
    )
    judge_index = next_index()
    judge_type = OperationType.FINAL_JUDGE
    judge_input_sha = write_private_snapshot_create_once(
        journal.run_root,
        _snapshot_stem(judge_index, judge_type, "input"),
        judge_input,
    )
    judge_started_at = datetime.now(timezone.utc)
    judge_started = monotonic()

    def judge_callback() -> JudgeOperationRecord:
        output, delta, status, failure_code = _provider_result(
            provider,
            lambda: provider.judge(judge_input, v2_architecture),
            JudgeServiceDecisionV2,
        )
        output_sha = (
            None
            if output is None
            else write_private_snapshot_create_once(
                journal.run_root,
                _snapshot_stem(judge_index, judge_type, "output"),
                output,
            )
        )
        return JudgeOperationRecord(
            schema_version="rcaeval-re2-v2.operation-record.v1",
            run_id=journal.run_id,
            case_id=journal.case_id,
            system=journal.system,
            architecture=journal.architecture,
            operation_index=judge_index,
            operation_type=judge_type,
            source=None,
            started_at_utc=judge_started_at,
            ended_at_utc=datetime.now(timezone.utc),
            latency_ms=float(max(0.0, (monotonic() - judge_started) * 1_000)),
            status=status,
            failure_code=failure_code,
            provider_call_index=delta.provider_call_index,
            input_snapshot_sha256=judge_input_sha,
            output_snapshot_sha256=output_sha,
            usage_delta=delta.usage,
            investigated_sources=final_context.investigated_sources,
            evidence_refs_visible_to_operation=tuple(
                item.evidence_id for item in final_context.evidence
            ),
            selected_sources=(),
            typed_output=output,
        )

    judge_record = journal.record_operation(judge_index, judge_type, judge_callback)
    if judge_record.status is not OperationStatus.COMPLETED:
        assert judge_record.failure_code is not None
        return _terminal_failure(
            judge_type,
            judge_index,
            judge_record.status,
            judge_record.failure_code,
            tool_calls=builder.tool_call_count,
        )
    assert judge_record.typed_output is not None
    judge_decision = judge_record.typed_output

    resolver_index = next_index()
    resolver_type = OperationType.INDICATOR_RESOLVER
    resolver_input = ResolverInputSnapshotV2(
        selected_service=judge_decision.root_cause_service,
        indicator_candidates=indicator_candidates,
    )
    resolver_input_sha = write_private_snapshot_create_once(
        journal.run_root,
        _snapshot_stem(resolver_index, resolver_type, "input"),
        resolver_input,
    )
    resolver_started_at = datetime.now(timezone.utc)
    resolver_started = monotonic()

    def resolver_callback() -> IndicatorResolutionRecord:
        try:
            resolution = resolve_indicator(
                judge_decision.root_cause_service,
                ranked_candidates,
            )
            status = OperationStatus.COMPLETED
            failure_code = None
            output_sha = write_private_snapshot_create_once(
                journal.run_root,
                _snapshot_stem(resolver_index, resolver_type, "output"),
                resolution,
            )
        except Exception as error:
            resolution = None
            status, failure_code = _failure(error)
            output_sha = None
        return IndicatorResolutionRecord(
            schema_version="rcaeval-re2-v2.operation-record.v1",
            run_id=journal.run_id,
            case_id=journal.case_id,
            system=journal.system,
            architecture=journal.architecture,
            operation_index=resolver_index,
            operation_type=resolver_type,
            source=None,
            started_at_utc=resolver_started_at,
            ended_at_utc=datetime.now(timezone.utc),
            latency_ms=float(max(0.0, (monotonic() - resolver_started) * 1_000)),
            status=status,
            failure_code=failure_code,
            provider_call_index=None,
            input_snapshot_sha256=resolver_input_sha,
            output_snapshot_sha256=output_sha,
            usage_delta=_zero_usage(),
            investigated_sources=final_context.investigated_sources,
            evidence_refs_visible_to_operation=tuple(
                item.evidence_id for item in final_context.evidence
            ),
            selected_sources=(),
            typed_output=resolution,
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
        ranked_candidates, candidate_snapshots = _candidate_snapshots(
            case,
            case_identity_sha256=case_identity_sha256,
            formula=indicator_formula,
            config=indicator_config,
        )
        return _run_v2(
            journal,
            case=case,
            provider=provider,
            v1_architecture=v1_architecture,
            v2_architecture=v2_architecture,
            ranked_candidates=ranked_candidates,
            indicator_candidates=candidate_snapshots,
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
