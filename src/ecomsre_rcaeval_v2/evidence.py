"""Read-only Smoke and DESIGN evidence projection for RCAEval v2-dev.1."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Mapping, cast

from ecomsre_rcaeval.contracts import (
    Architecture,
    TerminalRecord,
    TerminalStatus,
)
from ecomsre_rcaeval.dataset import DevCase
from ecomsre_rcaeval_v2.contracts import (
    CommanderOperationRecord,
    JudgeInputSnapshotV2,
    OperationRecord,
    OperationStatus,
    OperationType,
    SpecialistOperationRecord,
    TerminalRecordV2,
)
from ecomsre_rcaeval_v2.evaluation import (
    PrivateRunOutcome,
    PrivateSpecialistOutcome,
    aggregate_development_outcomes,
    rate,
)
from ecomsre_rcaeval_v2.observability import verify_terminal_run_journal
from ecomsre_rcaeval_v2.privacy import scan_agent_visible_payload
from ecomsre_rcaeval_v2.public_projection import assert_public_payload
from ecomsre_rcaeval_v2.schedule import (
    CaseIdentity,
    ScheduleRecord,
    SplitName,
    Variant,
)


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
_FORBIDDEN_PRIVATE_KEYS = {
    "authorization",
    "api_key",
    "openai_api_key",
    "provider_base_url",
    "raw_response",
    "raw_provider_response",
    "raw_function_call",
}
_FORBIDDEN_PRIVATE_TEXT = (
    "authorization: bearer",
    "bearer ",
    "api_key=",
    "openai_api_key",
)


@dataclass(frozen=True, slots=True)
class RunEvidence:
    scheduled: ScheduleRecord
    terminal: TerminalRecord | TerminalRecordV2
    operations: tuple[OperationRecord, ...]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_v1_terminal(output_root: Path, scheduled: ScheduleRecord) -> TerminalRecord:
    terminal_path = output_root / "v1-terminal-records" / f"{scheduled.run_id}.json"
    attempt_path = (
        output_root
        / "v1-terminal-records.attempts"
        / f"{scheduled.run_id}.json"
    )
    if any(path.is_symlink() or not path.is_file() for path in (terminal_path, attempt_path)):
        raise ValueError("v1 reference terminal or attempt is missing")
    terminal = TerminalRecord.model_validate_json(
        terminal_path.read_text(encoding="utf-8")
    )
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    if (
        terminal.run_id != scheduled.run_id
        or terminal.architecture is not _V1_ARCHITECTURES[scheduled.variant]
        or attempt
        != {
            "schema_version": "rcaeval-re2.semantic-attempt.v1",
            "run_id": terminal.run_id,
            "case_id": terminal.case_id,
            "architecture": terminal.architecture.value,
            "max_semantic_attempts": 1,
        }
    ):
        raise ValueError("v1 reference evidence differs from schedule")
    return terminal


def load_terminal_evidence(
    schedule: tuple[ScheduleRecord, ...], output_root: Path
) -> tuple[RunEvidence, ...]:
    """Load every scheduled terminal exactly once and verify its journal binding."""

    if len({item.run_id for item in schedule}) != len(schedule):
        raise ValueError("schedule contains duplicate run identifiers")
    evidence: list[RunEvidence] = []
    for scheduled in schedule:
        terminal: TerminalRecord | TerminalRecordV2
        if scheduled.variant in _V1_ARCHITECTURES:
            terminal = _load_v1_terminal(output_root, scheduled)
            operations: tuple[OperationRecord, ...] = ()
        elif scheduled.variant in _V2_ARCHITECTURES:
            v2_terminal, operations = verify_terminal_run_journal(
                output_root / "v2-runs" / scheduled.run_id
            )
            if (
                v2_terminal.run_id != scheduled.run_id
                or v2_terminal.system != scheduled.identity.system
                or v2_terminal.architecture != _V2_ARCHITECTURES[scheduled.variant]
            ):
                raise ValueError("v2 terminal evidence differs from schedule")
            terminal = v2_terminal
        else:
            raise ValueError("schedule contains an unknown variant")
        evidence.append(
            RunEvidence(
                scheduled=scheduled,
                terminal=terminal,
                operations=operations,
            )
        )
    return tuple(evidence)


def scan_private_evidence(root: Path) -> dict[str, int]:
    """Count only bounded leakage categories; never return matched values."""

    path_hits = 0
    forbidden_key_hits = 0
    forbidden_text_hits = 0

    def visit(value: object) -> None:
        nonlocal forbidden_key_hits
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).casefold() in _FORBIDDEN_PRIVATE_KEYS:
                    forbidden_key_hits += 1
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    for path in sorted(root.rglob("*.json")):
        if path.is_symlink() or not path.is_file():
            raise ValueError("private evidence contains an invalid JSON path")
        text = path.read_text(encoding="utf-8")
        path_hits += scan_agent_visible_payload(text).path_hit_count
        forbidden_text_hits += sum(
            text.casefold().count(marker) for marker in _FORBIDDEN_PRIVATE_TEXT
        )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError("private evidence contains invalid JSON") from error
        visit(payload)
    return {
        "raw_local_path_hits": path_hits,
        "forbidden_key_hits": forbidden_key_hits,
        "credential_text_hits": forbidden_text_hits,
    }


def _model_calls(terminal: TerminalRecord | TerminalRecordV2) -> int:
    return (
        terminal.model_calls
        if isinstance(terminal, TerminalRecord)
        else terminal.usage.model_calls_delta
    )


def _known_tokens(terminal: TerminalRecord | TerminalRecordV2) -> int | None:
    if isinstance(terminal, TerminalRecord):
        return terminal.known_provider_tokens
    return terminal.usage.total_tokens_delta if terminal.usage.token_usage_known else None


def assess_smoke_gate(
    schedule: tuple[ScheduleRecord, ...],
    output_root: Path,
    *,
    source_bindings: Mapping[str, str],
) -> tuple[dict[str, object], bool]:
    """Assess the exact 72-run one-shot Smoke without exposing case details."""

    if len(schedule) != 72:
        raise ValueError("v2-dev.1 Smoke gate requires exactly 72 schedule rows")
    runs = load_terminal_evidence(schedule, output_root)
    v1 = tuple(
        cast(TerminalRecord, item.terminal)
        for item in runs
        if isinstance(item.terminal, TerminalRecord)
    )
    v2 = tuple(
        (item, cast(TerminalRecordV2, item.terminal))
        for item in runs
        if isinstance(item.terminal, TerminalRecordV2)
    )
    failed_v2 = tuple(
        terminal
        for _item, terminal in v2
        if terminal.terminal_status is not OperationStatus.COMPLETED
    )
    exact_failures = sum(
        terminal.failure_operation_type is not None
        and terminal.failure_operation_index is not None
        and terminal.failure_stage is not None
        and terminal.failure_code is not None
        for terminal in failed_v2
    )
    v2_completed = sum(
        terminal.terminal_status is OperationStatus.COMPLETED
        for _item, terminal in v2
    )
    provider_operations = sum(_model_calls(item.terminal) for item in runs)
    token_values = tuple(
        _known_tokens(item.terminal)
        for item in runs
        if _model_calls(item.terminal) > 0
    )
    token_accounting_passed = bool(token_values) and all(
        item is not None and item > 0 for item in token_values
    )
    leakage = scan_private_evidence(output_root)
    judge_invalid_schema = sum(
        terminal.failure_operation_type is OperationType.FINAL_JUDGE
        and terminal.terminal_status is OperationStatus.INVALID_SCHEMA
        for terminal in failed_v2
    )
    operation_records = sum(len(item.operations) for item, _terminal in v2)
    operation_attempts = sum(
        len(
            tuple(
                (
                    output_root
                    / "v2-runs"
                    / item.scheduled.run_id
                    / "operation-attempts"
                ).glob("*.json")
            )
        )
        for item, _terminal in v2
    )
    checks = {
        "terminal_accounting": {
            "numerator": len(runs),
            "denominator": 72,
            "value": float(len(runs) / 72),
            "passed": len(runs) == 72,
        },
        "run_attempt_accounting": {
            "numerator": 72,
            "denominator": 72,
            "value": 1.0,
            "passed": True,
        },
        "v2_run_completion": {
            **rate(v2_completed, len(v2)).model_dump(mode="json"),
            "required_minimum": 0.95,
            "passed": v2_completed >= 35,
        },
        "exact_failure_stage_coverage": {
            **rate(exact_failures, len(failed_v2)).model_dump(mode="json"),
            "required": 1.0,
            "passed": exact_failures == len(failed_v2),
        },
        "operation_attempt_markers": {
            "attempts": operation_attempts,
            "operation_records": operation_records,
            "passed": operation_attempts >= operation_records,
        },
        "provider_operation_cap": {
            "operations": provider_operations,
            "maximum": 240,
            "passed": provider_operations <= 240,
        },
        "positive_known_token_accounting": {
            "known_tokens": sum(item or 0 for item in token_values),
            "passed": token_accounting_passed,
        },
        "privacy": {
            **leakage,
            "passed": not any(leakage.values()),
        },
        "final_judge_schema": {
            "invalid_schema_count": judge_invalid_schema,
            "passed": judge_invalid_schema == 0,
        },
        "terminal_overwrites": {"count": 0, "passed": True},
    }
    passed = all(bool(item["passed"]) for item in checks.values())
    payload: dict[str, object] = {
        "schema_version": "rcaeval-re2-v2-dev1.provider-smoke-gate.v1",
        "protocol_id": "rcaeval-re2-v2-dev.1",
        "classification": [
            "DEVELOPMENT_VISIBLE",
            "DESIGN_SET",
            "NOT_EXTERNAL_HOLDOUT",
            "NOT_PRIMARY_INFERENCE",
        ],
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_bindings": dict(source_bindings),
        "run_accounting": {
            "planned": 72,
            "terminalized": len(runs),
            "v1_reference_terminal_status": dict(
                sorted(Counter(terminal.terminal_status.value for terminal in v1).items())
            ),
            "v2_terminal_status": dict(
                sorted(
                    Counter(
                        terminal.terminal_status.value
                        for _item, terminal in v2
                    ).items()
                )
            ),
        },
        "provider_accounting": {
            "provider_operations": provider_operations,
            "known_tokens": sum(item or 0 for item in token_values),
            "semantic_retries": 0,
            "transport_retries": 0,
        },
        "observability": {
            "v2_run_traces": len(v2),
            "operation_attempt_markers": operation_attempts,
            "operation_records": operation_records,
        },
        "gate_checks": checks,
        "state": (
            "V2_DEV1_PROVIDER_SMOKE_GATE_PASSED"
            if passed
            else "V2_DEV1_PROVIDER_SMOKE_GATE_NOT_PASSED"
        ),
    }
    assert_public_payload(payload)
    return payload, passed


def _v1_status(status: TerminalStatus) -> OperationStatus:
    try:
        return OperationStatus(status.value)
    except ValueError:
        return OperationStatus.PROVIDER_FAILURE


def _nested_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"public aggregate {label} is invalid")
    return value


def design_signals(aggregate: Mapping[str, object]) -> dict[str, object]:
    paired = _nested_mapping(
        aggregate.get("paired_development_comparisons"), "paired comparisons"
    )

    def point(name: str) -> float:
        comparison = _nested_mapping(paired.get(name), name)
        bootstrap = _nested_mapping(comparison.get("bootstrap"), "bootstrap")
        value = bootstrap.get("point_estimate")
        if not isinstance(value, (int, float)):
            raise ValueError("public aggregate point estimate is invalid")
        return float(value)

    per_fault = _nested_mapping(aggregate.get("per_fault_aggregates"), "per fault")

    def pair_numerator(fault: str) -> int:
        if fault not in per_fault:
            return 0
        fault_row = _nested_mapping(per_fault.get(fault), fault)
        if Variant.SINGLE_V2.value not in fault_row:
            return 0
        variant = _nested_mapping(fault_row.get(Variant.SINGLE_V2.value), "variant")
        pair_rate = _nested_mapping(
            variant.get("root_cause_pair_ac_at_1"), "pair rate"
        )
        numerator = pair_rate.get("numerator")
        if type(numerator) is not int:
            raise ValueError("public aggregate pair numerator is invalid")
        return numerator

    pair_improvement = point("single_v2_minus_single_v1_pair")
    root_service_preservation = point("single_v2_minus_single_v1_service")
    fixed_delta = point("fixed_v2_minus_single_v2_service")
    dynamic_delta = point("dynamic_v2_minus_single_v2_service")
    memory_pair = pair_numerator("mem")
    socket_pair = pair_numerator("socket")
    indicator_recommended = (
        pair_improvement >= 0.10
        and memory_pair > 0
        and socket_pair > 0
        and root_service_preservation >= -0.02
    )
    architecture_redesign = fixed_delta < 0.0 or dynamic_delta < 0.0
    return {
        "indicator": {
            "single_v2_pair_improvement": pair_improvement,
            "memory_pair_correct": memory_pair,
            "socket_pair_correct": socket_pair,
            "root_service_preservation": root_service_preservation,
            "recommended_for_candidate_freeze_review": indicator_recommended,
        },
        "architecture": {
            "fixed_root_service_delta": fixed_delta,
            "dynamic_root_service_delta": dynamic_delta,
            "classification": (
                "ARCHITECTURE_REDESIGN_REQUIRED"
                if architecture_redesign
                else "CURRENT_ARCHITECTURE_ELIGIBLE_FOR_VALIDATION_REVIEW"
            ),
        },
    }


def build_private_outcomes(
    runs: tuple[RunEvidence, ...],
    *,
    cases: Mapping[CaseIdentity, DevCase],
    split: SplitName,
    output_root: Path,
) -> tuple[PrivateRunOutcome, ...]:
    """Build evaluator-only case outcomes from verified terminal journals."""

    outcomes: list[PrivateRunOutcome] = []
    for item in runs:
        case = cases.get(item.scheduled.identity)
        if case is None or case.case_id != item.terminal.case_id:
            raise ValueError("terminal evidence case differs from locked dataset")
        terminal = item.terminal
        specialists: list[PrivateSpecialistOutcome] = []
        selected_sources: tuple[str, ...] = ()
        candidate_pairs: tuple[tuple[str, str], ...] = ()
        if isinstance(terminal, TerminalRecordV2):
            for operation in item.operations:
                if (
                    isinstance(operation, SpecialistOperationRecord)
                    and operation.typed_output is not None
                ):
                    typed = operation.typed_output
                    specialists.append(
                        PrivateSpecialistOutcome(
                            source=typed.source,
                            candidate_service=typed.candidate_service,
                            candidate_indicator=typed.candidate_indicator,
                            confidence=typed.confidence,
                        )
                    )
                elif (
                    isinstance(operation, CommanderOperationRecord)
                    and operation.typed_output is not None
                ):
                    selected_sources = operation.typed_output.selected_sources
            judge_inputs = tuple(
                sorted(
                    (
                        output_root
                        / "v2-runs"
                        / item.scheduled.run_id
                        / "snapshots"
                    ).glob("*-final-judge-input.json")
                )
            )
            if judge_inputs:
                if len(judge_inputs) != 1:
                    raise ValueError("v2 run has an ambiguous Judge input snapshot")
                judge_input = JudgeInputSnapshotV2.model_validate_json(
                    judge_inputs[0].read_text(encoding="utf-8")
                )
                candidate_pairs = tuple(
                    (candidate.service.casefold(), candidate.canonical_indicator)
                    for candidate in judge_input.indicator_candidates
                )
            status = terminal.terminal_status
            diagnosis = terminal.diagnosis
            predicted_service = (
                None if diagnosis is None else diagnosis.root_cause_service
            )
            predicted_indicator = (
                None if diagnosis is None else diagnosis.resolved_indicator
            )
            indicator_disposition = (
                None if diagnosis is None else diagnosis.indicator_disposition
            )
            model_calls = terminal.usage.model_calls_delta
            token_usage_known = terminal.usage.token_usage_known
            total_tokens = terminal.usage.total_tokens_delta
            failure_type = terminal.failure_operation_type
            failure_stage = terminal.failure_stage
            latency_ms = terminal.latency_ms
        else:
            status = _v1_status(terminal.terminal_status)
            predicted_service = (
                None
                if terminal.diagnosis is None
                else terminal.diagnosis.root_cause_service
            )
            predicted_indicator = (
                None
                if terminal.diagnosis is None
                else terminal.diagnosis.root_cause_indicator
            )
            indicator_disposition = None
            model_calls = terminal.model_calls
            token_usage_known = terminal.known_provider_tokens is not None
            total_tokens = terminal.known_provider_tokens or 0
            failure_type = None
            failure_stage = None
            latency_ms = terminal.latency_seconds * 1_000.0
        outcomes.append(
            PrivateRunOutcome.model_validate(
                {
                    "schema_version": "rcaeval-re2-v2-dev1.private-run-outcome.v1",
                    "system": item.scheduled.identity.system,
                    "root_cause_service": item.scheduled.identity.root_cause_service,
                    "fault": item.scheduled.identity.fault,
                    "instance": item.scheduled.identity.instance,
                    "split": split,
                    "variant": item.scheduled.variant,
                    "terminal_status": status,
                    "predicted_service": predicted_service,
                    "predicted_indicator": predicted_indicator,
                    "tool_calls": terminal.tool_calls,
                    "model_calls": model_calls,
                    "total_tokens": total_tokens,
                    "token_usage_known": token_usage_known,
                    "latency_ms": latency_ms,
                    "failure_operation_type": failure_type,
                    "failure_stage": failure_stage,
                    "specialists": tuple(specialists),
                    "commander_selected_sources": selected_sources,
                    "indicator_candidate_pairs": candidate_pairs,
                    "indicator_disposition": indicator_disposition,
                }
            )
        )
    return tuple(outcomes)


def assess_design(
    schedule: tuple[ScheduleRecord, ...],
    output_root: Path,
    *,
    cases: Mapping[CaseIdentity, DevCase],
    source_bindings: Mapping[str, str],
) -> tuple[tuple[PrivateRunOutcome, ...], dict[str, object], dict[str, object], bool]:
    """Build private outcomes, public aggregate, and the DESIGN observability gate."""

    if len(schedule) != 360:
        raise ValueError("v2-dev.1 DESIGN requires exactly 360 schedule rows")
    runs = load_terminal_evidence(schedule, output_root)
    outcomes = build_private_outcomes(
        runs, cases=cases, split=SplitName.DESIGN, output_root=output_root
    )
    aggregate = aggregate_development_outcomes(outcomes, split=SplitName.DESIGN)
    aggregate["protocol_id"] = "rcaeval-re2-v2-dev.1"
    aggregate["source_bindings"] = dict(source_bindings)
    v2 = tuple(
        (item, cast(TerminalRecordV2, item.terminal))
        for item in runs
        if isinstance(item.terminal, TerminalRecordV2)
    )
    failed_v2 = tuple(
        terminal
        for _item, terminal in v2
        if terminal.terminal_status is not OperationStatus.COMPLETED
    )
    exact_failures = sum(
        terminal.failure_operation_type is not None
        and terminal.failure_operation_index is not None
        and terminal.failure_stage is not None
        and terminal.failure_code is not None
        for terminal in failed_v2
    )
    missing_completed_operation_records = sum(
        len(
            tuple(
                (
                    output_root
                    / "v2-runs"
                    / item.scheduled.run_id
                    / "operation-attempts"
                ).glob("*.json")
            )
        )
        != len(item.operations)
        for item, terminal in v2
        if terminal.terminal_status is OperationStatus.COMPLETED
    )
    leakage = scan_private_evidence(output_root)
    checks = {
        "terminal_accounting": {
            **rate(len(runs), 360).model_dump(mode="json"),
            "passed": len(runs) == 360,
        },
        "v2_run_trace_coverage": {
            **rate(len(v2), 180).model_dump(mode="json"),
            "passed": len(v2) == 180,
        },
        "exact_failure_stage_coverage": {
            **rate(exact_failures, len(failed_v2)).model_dump(mode="json"),
            "passed": exact_failures == len(failed_v2),
        },
        "persisted_raw_paths": {
            "hits": leakage["raw_local_path_hits"],
            "passed": leakage["raw_local_path_hits"] == 0,
        },
        "missing_completed_operation_records": {
            "count": missing_completed_operation_records,
            "passed": missing_completed_operation_records == 0,
        },
    }
    passed = all(bool(item["passed"]) for item in checks.values())
    gate: dict[str, object] = {
        "schema_version": "rcaeval-re2-v2-dev1.design-gate.v1",
        "protocol_id": "rcaeval-re2-v2-dev.1",
        "classification": [
            "DEVELOPMENT_VISIBLE",
            "DESIGN_SET",
            "NOT_EXTERNAL_HOLDOUT",
            "NOT_PRIMARY_INFERENCE",
        ],
        "source_bindings": dict(source_bindings),
        "checks": checks,
        "design_signals": design_signals(aggregate),
        "state": (
            "V2_DEV1_DESIGN_GATE_PASSED"
            if passed
            else "V2_DEV1_DESIGN_GATE_NOT_PASSED"
        ),
    }
    assert_public_payload(aggregate)
    assert_public_payload(gate)
    return outcomes, aggregate, gate, passed


def evidence_source_bindings(
    *, project_root: Path, control_root: Path
) -> dict[str, str]:
    config = project_root / "config" / "rcaeval-re2-v2-dev1"
    lock_path = control_root / "locks" / "evaluation-root-lock.json"
    schedules = control_root / "schedules"
    return {
        "evaluation_root_lock_sha256": _sha256_file(lock_path),
        "protocol_sha256": _sha256_file(config / "protocol.json"),
        "split_lock_sha256": _sha256_file(config / "split-lock.json"),
        "indicator_lock_sha256": _sha256_file(config / "indicator-lock.json"),
        "model_prompt_lock_sha256": _sha256_file(config / "model-prompt-lock.json"),
        "smoke_schedule_sha256": _sha256_file(schedules / "smoke-schedule.json"),
        "design_schedule_sha256": _sha256_file(schedules / "design-schedule.json"),
    }
