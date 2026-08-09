from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ecomsre_rcaeval.contracts import (
    Architecture,
    Diagnosis,
    TerminalRecord,
    TerminalStatus,
)
from ecomsre_rcaeval_v2.contracts import (
    DiagnosisV2,
    OperationFailureCode,
    OperationStage,
    OperationStatus,
    OperationType,
    ProviderUsageDelta,
    TerminalRecordV2,
)
import ecomsre_rcaeval_v2.evidence as evidence_module
from ecomsre_rcaeval_v2.evidence import RunEvidence, assess_smoke_gate
from ecomsre_rcaeval_v2.schedule import (
    CaseIdentity,
    ScheduleRecord,
    SplitName,
    Variant,
)


_HASH = "a" * 64
_V1_ARCHITECTURES = {
    Variant.SINGLE_V1_REFERENCE: Architecture.SINGLE,
    Variant.FIXED_V1_REFERENCE: Architecture.FIXED,
    Variant.DYNAMIC_V1_REFERENCE: Architecture.DYNAMIC,
}
_V2_ARCHITECTURES = {
    Variant.SINGLE_V2: "single_v2",
    Variant.FIXED_V2: "fixed_v2",
    Variant.DYNAMIC_V2: "dynamic_v2",
}


def _schedule() -> tuple[ScheduleRecord, ...]:
    identity = CaseIdentity(
        system="RE2-OB",
        root_cause_service="checkoutservice",
        fault="mem",
        instance="1",
    )
    variants = tuple(Variant)
    return tuple(
        ScheduleRecord(
            schema_version="rcaeval-re2-v2-dev1.scheduled-run.v1",
            run_id=f"{index:032x}",
            split=SplitName.DESIGN,
            identity=identity,
            variant=variant,
            arm_position=variants.index(variant) + 1,
            case_order_digest_sha256=_HASH,
        )
        for index in range(72)
        for variant in (variants[index % len(variants)],)
    )


def _runs(
    schedule: tuple[ScheduleRecord, ...],
) -> tuple[RunEvidence, ...]:
    now = datetime.now(timezone.utc)
    result: list[RunEvidence] = []
    for scheduled in schedule:
        if scheduled.variant in _V1_ARCHITECTURES:
            terminal: TerminalRecord | TerminalRecordV2 = TerminalRecord(
                run_id=scheduled.run_id,
                case_id="re2-ob-case-0001",
                architecture=_V1_ARCHITECTURES[scheduled.variant],
                terminal_status=TerminalStatus.COMPLETED,
                diagnosis=Diagnosis(
                    root_cause_service="checkoutservice",
                    root_cause_indicator="mem",
                    evidence_refs=("metric:0001",),
                    explanation="bounded diagnosis",
                ),
                failure_code=None,
                tool_calls=3,
                model_calls=1,
                known_provider_tokens=10,
                latency_seconds=0.1,
            )
        else:
            terminal = TerminalRecordV2(
                schema_version="rcaeval-re2-v2.terminal-record.v1",
                run_id=scheduled.run_id,
                case_id="re2-ob-case-0001",
                system="RE2-OB",
                architecture=_V2_ARCHITECTURES[scheduled.variant],  # type: ignore[arg-type]
                terminal_status=OperationStatus.COMPLETED,
                failure_operation_type=None,
                failure_operation_index=None,
                failure_code=None,
                failure_stage=None,
                diagnosis=DiagnosisV2(
                    root_cause_service="checkoutservice",
                    model_proposed_indicator="mem",
                    resolved_indicator="mem",
                    indicator_disposition="RESOLVED",
                    judge_evidence_refs=("metric:0001",),
                    indicator_evidence_ref="indicator:0001",
                    confidence=0.8,
                    explanation="bounded diagnosis",
                ),
                tool_calls=3,
                run_trace_sha256=_HASH,
                operation_tree_sha256=_HASH,
                usage=ProviderUsageDelta(
                    model_calls_delta=1,
                    prompt_tokens_delta=8,
                    completion_tokens_delta=2,
                    total_tokens_delta=10,
                ),
                started_at_utc=now,
                ended_at_utc=now,
                latency_ms=10.0,
            )
        result.append(RunEvidence(scheduled, terminal, ()))
    return tuple(result)


def test_smoke_gate_passes_only_complete_bounded_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule = _schedule()
    runs = _runs(schedule)
    monkeypatch.setattr(
        evidence_module,
        "load_terminal_evidence",
        lambda _schedule, _root: runs,
    )
    monkeypatch.setattr(
        evidence_module,
        "scan_private_evidence",
        lambda _root: {
            "raw_local_path_hits": 0,
            "forbidden_key_hits": 0,
            "credential_text_hits": 0,
        },
    )

    gate, passed = assess_smoke_gate(
        schedule, tmp_path, source_bindings={"protocol_sha256": _HASH}
    )

    assert passed is True
    assert gate["state"] == "V2_DEV1_PROVIDER_SMOKE_GATE_PASSED"
    assert gate["gate_checks"]["v2_run_completion"]["numerator"] == 36
    assert gate["provider_accounting"]["provider_operations"] == 72


def test_final_judge_invalid_schema_fails_without_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule = _schedule()
    runs = list(_runs(schedule))
    index = next(
        offset
        for offset, item in enumerate(runs)
        if isinstance(item.terminal, TerminalRecordV2)
    )
    terminal = runs[index].terminal
    assert isinstance(terminal, TerminalRecordV2)
    runs[index] = RunEvidence(
        runs[index].scheduled,
        terminal.model_copy(
            update={
                "terminal_status": OperationStatus.INVALID_SCHEMA,
                "failure_operation_type": OperationType.FINAL_JUDGE,
                "failure_operation_index": 1,
                "failure_code": OperationFailureCode.PROVIDER_OUTPUT_INVALID_SCHEMA,
                "failure_stage": OperationStage.OUTPUT_VALIDATION,
                "diagnosis": None,
            }
        ),
        (),
    )
    monkeypatch.setattr(
        evidence_module,
        "load_terminal_evidence",
        lambda _schedule, _root: tuple(runs),
    )
    monkeypatch.setattr(
        evidence_module,
        "scan_private_evidence",
        lambda _root: {
            "raw_local_path_hits": 0,
            "forbidden_key_hits": 0,
            "credential_text_hits": 0,
        },
    )

    gate, passed = assess_smoke_gate(
        schedule, tmp_path, source_bindings={"protocol_sha256": _HASH}
    )

    assert passed is False
    assert gate["state"] == "V2_DEV1_PROVIDER_SMOKE_GATE_NOT_PASSED"
    assert gate["gate_checks"]["final_judge_schema"] == {
        "invalid_schema_count": 1,
        "passed": False,
    }
    assert gate["gate_checks"]["exact_failure_stage_coverage"]["passed"] is True
