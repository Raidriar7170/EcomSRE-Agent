from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ecomsre_rcaeval_v2.contracts import (
    CommanderDecisionV2,
    CommanderOperationRecord,
    DiagnosisV2,
    IndicatorResolutionRecord,
    IndicatorResolutionV2,
    IndicatorCandidateSnapshotV2,
    IncidentSnapshotV2,
    JudgeInputSnapshotV2,
    JudgeOperationRecord,
    JudgeServiceDecisionV2,
    OperationFailureCode,
    OperationStatus,
    OperationType,
    ProviderUsageDelta,
    BoundedEvidenceSnapshotV2,
    SourceObservationSnapshotV2,
    SpecialistAssessmentV2,
    SpecialistOperationRecord,
    TerminalRecordV2,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
NOW = datetime(2026, 8, 7, tzinfo=timezone.utc)


def _common(operation_type: OperationType, *, source: str | None) -> dict[str, object]:
    return {
        "schema_version": "rcaeval-re2-v2.operation-record.v1",
        "run_id": "1" * 32,
        "case_id": "re2-ob-case-0001",
        "system": "RE2-OB",
        "architecture": "single_v2",
        "operation_index": 1,
        "operation_type": operation_type,
        "source": source,
        "started_at_utc": NOW,
        "ended_at_utc": NOW,
        "latency_ms": 0.0,
        "status": OperationStatus.COMPLETED,
        "failure_code": None,
        "provider_call_index": (
            None if operation_type is OperationType.INDICATOR_RESOLVER else 1
        ),
        "input_snapshot_sha256": SHA_A,
        "output_snapshot_sha256": SHA_B,
        "usage_delta": ProviderUsageDelta(
            model_calls_delta=1,
            prompt_tokens_delta=10,
            completion_tokens_delta=5,
            total_tokens_delta=15,
        ),
        "investigated_sources": (() if source is None else (source,)),
        "evidence_refs_visible_to_operation": ("metric:0001",),
        "selected_sources": (),
    }


def test_all_required_operation_contracts_preserve_typed_outputs() -> None:
    specialist = SpecialistOperationRecord(
        **_common(OperationType.METRICS_SPECIALIST, source="metrics"),
        typed_output=SpecialistAssessmentV2(
            source="metrics",
            candidate_service="cartservice",
            candidate_indicator="mem",
            confidence=0.8,
            supporting_evidence_refs=("metric:0001",),
            contradicting_evidence_refs=(),
            summary="Memory pressure is localized to cartservice.",
        ),
    )
    commander_fields = _common(OperationType.COMMANDER, source=None)
    commander_fields["selected_sources"] = ("logs", "traces")
    commander = CommanderOperationRecord(
        **commander_fields,
        typed_output=CommanderDecisionV2(
            selected_sources=("logs", "traces"), rationale="Inspect both sources."
        ),
    )
    judge = JudgeOperationRecord(
        **_common(OperationType.FINAL_JUDGE, source=None),
        typed_output=JudgeServiceDecisionV2(
            root_cause_service="cartservice",
            model_proposed_indicator="mem",
            confidence=0.9,
            evidence_refs=("metric:0001",),
            explanation="The bounded evidence supports cartservice.",
        ),
    )
    resolver_fields = _common(OperationType.INDICATOR_RESOLVER, source=None)
    resolver_fields["usage_delta"] = ProviderUsageDelta(
        model_calls_delta=0,
        prompt_tokens_delta=0,
        completion_tokens_delta=0,
        total_tokens_delta=0,
    )
    resolver = IndicatorResolutionRecord(
        **resolver_fields,
        typed_output=IndicatorResolutionV2(
            selected_service="cartservice",
            disposition="RESOLVED",
            resolved_indicator="mem",
            selected_metric="cartservice_memory_rss",
            evidence_ref="metric:0001",
        ),
    )

    assert specialist.typed_output.candidate_service == "cartservice"
    assert commander.typed_output.selected_sources == ("logs", "traces")
    assert judge.typed_output.root_cause_service == "cartservice"
    assert resolver.typed_output.resolved_indicator == "mem"


def test_operation_records_require_every_observability_field_and_strict_types() -> None:
    required = {
        "schema_version",
        "run_id",
        "case_id",
        "system",
        "architecture",
        "operation_index",
        "operation_type",
        "source",
        "started_at_utc",
        "ended_at_utc",
        "latency_ms",
        "status",
        "failure_code",
        "provider_call_index",
        "input_snapshot_sha256",
        "output_snapshot_sha256",
        "usage_delta",
        "investigated_sources",
        "evidence_refs_visible_to_operation",
        "selected_sources",
        "typed_output",
    }
    assert {
        name
        for name, field in SpecialistOperationRecord.model_fields.items()
        if field.is_required()
    } == required

    fields = _common(OperationType.METRICS_SPECIALIST, source="metrics")
    fields["operation_index"] = 1.0
    with pytest.raises(ValidationError):
        SpecialistOperationRecord(
            **fields,
            typed_output=SpecialistAssessmentV2(
                source="metrics",
                candidate_service=None,
                candidate_indicator=None,
                confidence=0.0,
                supporting_evidence_refs=(),
                contradicting_evidence_refs=(),
                summary="No candidate.",
            ),
        )


@pytest.mark.parametrize(
    "forbidden",
    [
        "Authorization: Bearer secret",
        "api_key=secret",
        "OPENAI_API_KEY=secret",
        "https://provider.example/v1",
        "/Users/example/private-run",
        "/home/example/private-run",
        "/private/tmp/private-run",
        "/tmp/provider-private/run.json",
        "/var/folders/provider-private/run.json",
        "raw HTTP response body",
        "raw function-call text",
    ],
)
def test_contracts_reject_secret_raw_provider_and_absolute_path_text(
    forbidden: str,
) -> None:
    with pytest.raises(ValidationError, match="serialization-safe"):
        SpecialistAssessmentV2(
            source="metrics",
            candidate_service=None,
            candidate_indicator=None,
            confidence=0.0,
            supporting_evidence_refs=(),
            contradicting_evidence_refs=(),
            summary=forbidden,
        )


def test_status_failure_code_and_usage_totals_are_consistent() -> None:
    with pytest.raises(ValidationError):
        ProviderUsageDelta(
            model_calls_delta=1,
            prompt_tokens_delta=2,
            completion_tokens_delta=3,
            total_tokens_delta=99,
        )
    unknown = ProviderUsageDelta(
        model_calls_delta=1,
        prompt_tokens_delta=0,
        completion_tokens_delta=0,
        total_tokens_delta=0,
        token_usage_known=False,
    )
    assert unknown.token_usage_known is False
    with pytest.raises(ValidationError):
        ProviderUsageDelta(
            model_calls_delta=1,
            prompt_tokens_delta=1,
            completion_tokens_delta=0,
            total_tokens_delta=1,
            token_usage_known=False,
        )

    fields = _common(OperationType.METRICS_SPECIALIST, source="metrics")
    fields.update(
        status=OperationStatus.PROVIDER_FAILURE,
        failure_code=OperationFailureCode.PROVIDER_TRANSPORT_FAILURE,
        output_snapshot_sha256=None,
    )
    record = SpecialistOperationRecord(**fields, typed_output=None)
    assert record.failure_code is OperationFailureCode.PROVIDER_TRANSPORT_FAILURE

    fields["failure_code"] = None
    with pytest.raises(ValidationError):
        SpecialistOperationRecord(**fields, typed_output=None)


def test_terminal_record_has_direct_exact_failure_stage_and_trace_binding() -> None:
    terminal = TerminalRecordV2(
        schema_version="rcaeval-re2-v2.terminal-record.v1",
        run_id="1" * 32,
        case_id="re2-ob-case-0001",
        system="RE2-OB",
        architecture="single_v2",
        terminal_status=OperationStatus.PROVIDER_FAILURE,
        failure_operation_type=OperationType.FINAL_JUDGE,
        failure_operation_index=2,
        failure_code=OperationFailureCode.PROVIDER_TRANSPORT_FAILURE,
        diagnosis=None,
        tool_calls=3,
        run_trace_sha256=SHA_A,
        operation_tree_sha256=SHA_B,
        usage=ProviderUsageDelta(
            model_calls_delta=2,
            prompt_tokens_delta=20,
            completion_tokens_delta=10,
            total_tokens_delta=30,
        ),
        started_at_utc=NOW,
        ended_at_utc=NOW,
        latency_ms=0.0,
    )
    assert terminal.failure_operation_type is OperationType.FINAL_JUDGE
    assert terminal.run_trace_sha256 == SHA_A


def test_completed_terminal_requires_resolved_diagnosis() -> None:
    diagnosis = DiagnosisV2(
        root_cause_service="cartservice",
        model_proposed_indicator="cpu",
        resolved_indicator="mem",
        indicator_disposition="RESOLVED",
        judge_evidence_refs=("metric:0001",),
        indicator_evidence_ref="indicator:0001",
        confidence=0.9,
        explanation="The Judge selected cartservice and the resolver selected memory.",
    )
    terminal = TerminalRecordV2(
        schema_version="rcaeval-re2-v2.terminal-record.v1",
        run_id="1" * 32,
        case_id="re2-ob-case-0001",
        system="RE2-OB",
        architecture="single_v2",
        terminal_status=OperationStatus.COMPLETED,
        failure_operation_type=None,
        failure_operation_index=None,
        failure_code=None,
        diagnosis=diagnosis,
        tool_calls=3,
        run_trace_sha256=SHA_A,
        operation_tree_sha256=SHA_B,
        usage=ProviderUsageDelta(
            model_calls_delta=1,
            prompt_tokens_delta=20,
            completion_tokens_delta=10,
            total_tokens_delta=30,
        ),
        started_at_utc=NOW,
        ended_at_utc=NOW,
        latency_ms=0.0,
    )
    assert terminal.diagnosis.resolved_indicator == "mem"


def test_judge_input_snapshot_has_exact_typed_bounded_inputs() -> None:
    snapshot = JudgeInputSnapshotV2(
        incident=IncidentSnapshotV2(
            incident_id="incident-0001",
            system="RE2-OB",
            anomaly_timestamp=1_000,
            modalities=("metrics", "logs", "traces"),
            summary="Checkout latency increased after the injection window.",
        ),
        source_observations=(
            SourceObservationSnapshotV2(
                source="metrics",
                status="AVAILABLE",
                summary="Cartservice memory shifted.",
                evidence_refs=("metric:0001",),
            ),
        ),
        bounded_evidence=(
            BoundedEvidenceSnapshotV2(
                evidence_ref="metric:0001",
                source="metrics",
                service="cartservice",
                observation="Cartservice RSS increased persistently.",
            ),
        ),
        specialist_assessments=(
            SpecialistAssessmentV2(
                source="metrics",
                candidate_service="cartservice",
                candidate_indicator="mem",
                confidence=0.8,
                supporting_evidence_refs=("metric:0001",),
                contradicting_evidence_refs=(),
                summary="Metrics support cartservice.",
            ),
        ),
        commander_decision=None,
        indicator_candidates=(
            IndicatorCandidateSnapshotV2(
                service="cartservice",
                canonical_indicator="mem",
                metric_name="cartservice_memory_rss",
                score=4.0,
                evidence_ref="metric:0001",
            ),
        ),
    )
    assert set(snapshot.model_dump()) == {
        "incident",
        "source_observations",
        "bounded_evidence",
        "specialist_assessments",
        "commander_decision",
        "indicator_candidates",
    }
    with pytest.raises(ValidationError):
        JudgeInputSnapshotV2(**snapshot.model_dump(), raw_response="forbidden")
