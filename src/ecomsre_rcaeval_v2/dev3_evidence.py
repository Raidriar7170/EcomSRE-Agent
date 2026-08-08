"""Case-free public projections for v2-dev.3 evidence."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
from statistics import median
from typing import Mapping

from ecomsre_rcaeval.contracts import TerminalRecord
from ecomsre_rcaeval.dataset import DevCase
from ecomsre_rcaeval_v2.contracts import OperationStatus, TerminalRecordV2
from ecomsre_rcaeval_v2.dev3_admission import ScheduleAdmissionLock
from ecomsre_rcaeval_v2.dev3_provider import (
    FailureClass,
    RetryDecision,
    SemanticOperationRecord,
    SemanticOperationStart,
)
from ecomsre_rcaeval_v2.dev3_token_accounting import (
    ProviderAttemptRecord,
    ProviderAttemptStart,
    rebuild_attempt_accounting,
)
from ecomsre_rcaeval_v2.dev3_schedule import (
    PROTOCOL_ID,
    ScheduleRecord,
    as_dev1_runtime_record,
)
from ecomsre_rcaeval_v2.evaluation import PrivateRunOutcome
from ecomsre_rcaeval_v2.evidence import (
    assess_design as assess_dev1_design,
    assess_smoke_gate as assess_dev1_smoke_gate,
    load_terminal_evidence,
)
from ecomsre_rcaeval_v2.public_projection import assert_public_payload
from ecomsre_rcaeval_v2.dev3_paths import reject_dev3_forbidden_paths
from ecomsre_rcaeval_v2.locks import PROJECT_ROOT
from ecomsre_rcaeval_v2.schedule import CaseIdentity, Variant as Dev1Variant


_STRING_REPLACEMENTS = (
    ("rcaeval-re2-v2-dev1", "rcaeval-re2-v2-dev3"),
    ("rcaeval-re2-v2-dev.1", "rcaeval-re2-v2-dev.3"),
    ("V2_DEV1", "V2_DEV3"),
    ("single_v2_dev1", "single_v2_dev3"),
    ("fixed_v2_dev1", "fixed_v2_dev3"),
    ("dynamic_v2_dev1", "dynamic_v2_dev3"),
)


def _json_model_files(root: Path, directory: str) -> tuple[Path, ...]:
    target = root / directory
    if not target.exists():
        return ()
    if target.is_symlink() or not target.is_dir():
        raise ValueError("dev3 Provider sidecar directory is invalid")
    entries = tuple(sorted(target.iterdir()))
    if any(path.is_symlink() or not path.is_file() or path.suffix != ".json" for path in entries):
        raise ValueError("dev3 Provider sidecar contains an invalid entry")
    return entries


def verify_provider_sidecar(
    run_root: Path,
    *,
    expected_semantic_operations: int,
    expected_policy_lock_sha256: str,
    expected_timeout_seconds: float = 30.0,
    prompt_token_reservation: int = 29_952,
    max_completion_tokens: int = 2_048,
) -> tuple[
    tuple[SemanticOperationRecord, ...], tuple[ProviderAttemptRecord, ...]
]:
    """Bind every sidecar record to starts, retry policy, and token reservations."""

    if run_root.is_symlink() or not run_root.is_dir():
        raise ValueError("dev3 Provider sidecar root is missing")
    allowed_directories = {
        "semantic-operation-starts",
        "semantic-operations",
        "provider-attempt-starts",
        "provider-attempts",
        "retry-decisions",
    }
    if any(
        path.name not in allowed_directories
        or path.is_symlink()
        or not path.is_dir()
        for path in run_root.iterdir()
    ):
        raise ValueError("dev3 Provider sidecar contains an undeclared artifact")
    semantic_start_paths = _json_model_files(run_root, "semantic-operation-starts")
    semantic_paths = _json_model_files(run_root, "semantic-operations")
    attempt_start_paths = _json_model_files(run_root, "provider-attempt-starts")
    attempt_paths = _json_model_files(run_root, "provider-attempts")
    decision_paths = _json_model_files(run_root, "retry-decisions")
    if len(semantic_start_paths) != expected_semantic_operations or len(
        semantic_paths
    ) != expected_semantic_operations:
        raise ValueError("dev3 semantic-operation accounting differs from terminal")
    semantic_starts = tuple(
        SemanticOperationStart.model_validate_json(path.read_text(encoding="utf-8"))
        for path in semantic_start_paths
    )
    semantics = tuple(
        SemanticOperationRecord.model_validate_json(path.read_text(encoding="utf-8"))
        for path in semantic_paths
    )
    attempt_starts = tuple(
        ProviderAttemptStart.model_validate_json(path.read_text(encoding="utf-8"))
        for path in attempt_start_paths
    )
    attempts = tuple(
        ProviderAttemptRecord.model_validate_json(path.read_text(encoding="utf-8"))
        for path in attempt_paths
    )
    decisions = tuple(
        RetryDecision.model_validate_json(path.read_text(encoding="utf-8"))
        for path in decision_paths
    )
    semantic_indexes = list(range(1, expected_semantic_operations + 1))
    if [item.semantic_operation_index for item in semantic_starts] != semantic_indexes:
        raise ValueError("dev3 semantic-operation start indexes are not contiguous")
    if [item.semantic_operation_index for item in semantics] != semantic_indexes:
        raise ValueError("dev3 semantic-operation indexes are not contiguous")
    if any(
        path.name != f"{item.semantic_operation_index:04d}.json"
        for path, item in zip(semantic_start_paths, semantic_starts, strict=True)
    ) or any(
        path.name != f"{item.semantic_operation_index:04d}.json"
        for path, item in zip(semantic_paths, semantics, strict=True)
    ):
        raise ValueError("dev3 semantic-operation filename binding failed")
    if len({item.provider_attempt_index for item in attempt_starts}) != len(
        attempt_starts
    ) or len({item.provider_attempt_index for item in attempts}) != len(attempts):
        raise ValueError("dev3 Provider attempt index is duplicated")
    by_start = {item.provider_attempt_index: item for item in attempt_starts}
    by_attempt = {item.provider_attempt_index: item for item in attempts}
    if set(by_start) != set(by_attempt) or len(attempt_start_paths) != len(attempt_paths):
        raise ValueError("dev3 Provider sidecar contains an orphan attempt")
    reservation = prompt_token_reservation + max_completion_tokens
    for start_path, attempt_start in zip(
        attempt_start_paths, attempt_starts, strict=True
    ):
        stem = (
            f"{attempt_start.semantic_operation_index:04d}-"
            f"{attempt_start.provider_attempt_index:04d}-"
            f"{attempt_start.retry_number}.json"
        )
        if start_path.name != stem:
            raise ValueError("dev3 Provider attempt start filename binding failed")
        final = by_attempt[attempt_start.provider_attempt_index]
        final_path = attempt_paths[attempts.index(final)]
        if final_path.name != stem:
            raise ValueError("dev3 Provider attempt final filename binding failed")
        if (
            attempt_start.policy_lock_sha256 != expected_policy_lock_sha256
            or final.policy_lock_sha256 != expected_policy_lock_sha256
            or attempt_start.timeout_seconds != expected_timeout_seconds
            or final.timeout_seconds != expected_timeout_seconds
            or attempt_start.prompt_token_reservation != prompt_token_reservation
            or attempt_start.max_completion_tokens != max_completion_tokens
            or attempt_start.attempt_token_reservation != reservation
            or final.attempt_token_reservation != reservation
        ):
            raise ValueError("dev3 Provider attempt differs from active policy")
        if (
            final.semantic_operation_index
            != attempt_start.semantic_operation_index
            or final.retry_number != attempt_start.retry_number
            or final.request_sha256 != attempt_start.request_sha256
            or final.started_at_utc != attempt_start.started_at_utc
            or final.retry_wait_ms != attempt_start.retry_wait_ms
        ):
            raise ValueError("dev3 Provider attempt final differs from start marker")
    decision_map = {item.semantic_operation_index: item for item in decisions}
    if len(decision_map) != len(decisions) or any(
        path.name != f"{item.semantic_operation_index:04d}.json"
        for path, item in zip(decision_paths, decisions, strict=True)
    ):
        raise ValueError("dev3 retry decision binding failed")
    bound_attempts: list[int] = []
    for semantic_start, semantic in zip(semantic_starts, semantics, strict=True):
        if (
            semantic_start.operation_type != semantic.operation_type
            or semantic_start.started_at_utc != semantic.started_at_utc
            or semantic_start.policy_lock_sha256 != expected_policy_lock_sha256
            or semantic.policy_lock_sha256 != expected_policy_lock_sha256
        ):
            raise ValueError("dev3 semantic operation differs from active policy/start")
        try:
            bound = tuple(
                by_attempt[index] for index in semantic.provider_attempt_indexes
            )
        except KeyError as error:
            raise ValueError("dev3 semantic operation references a missing attempt") from error
        bound_attempts.extend(semantic.provider_attempt_indexes)
        if tuple(item.semantic_operation_index for item in bound) != (
            semantic.semantic_operation_index,
        ) * len(bound):
            raise ValueError("dev3 semantic operation references another operation")
        if tuple(item.retry_number for item in bound) != tuple(range(len(bound))):
            raise ValueError("dev3 semantic operation retry ordering is invalid")
        if tuple(item.request_sha256 for item in bound) != semantic.request_sha256s:
            raise ValueError("dev3 semantic operation request binding drift")
        if tuple(item.usage_disposition.value for item in bound) != (
            semantic.attempt_usage_dispositions
        ):
            raise ValueError("dev3 semantic operation usage binding drift")
        first = bound[0] if bound else None
        expected_first_class = (
            None
            if first is None or first.failure_class is None
            else FailureClass(first.failure_class)
        )
        if (
            semantic.first_attempt_failure_class is not expected_first_class
            or semantic.first_attempt_failure_code
            != (None if first is None else first.failure_code)
        ):
            raise ValueError("dev3 semantic first-attempt attribution drift")
        decision = decision_map.get(semantic.semantic_operation_index)
        if len(bound) == 2:
            assert first is not None
            second = bound[1]
            if (
                first.valid_response_received
                or first.failure_class
                != FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT.value
                or decision is None
                or decision.disposition != "RETRY_ISSUED"
                or decision.first_provider_attempt_index
                != first.provider_attempt_index
                or decision.request_sha256 != first.request_sha256
                or decision.eligible_failure_code != first.failure_code
                or decision.retry_wait_ms != second.retry_wait_ms
            ):
                raise ValueError("dev3 issued retry evidence is inconsistent")
            fixed_wait_codes = {
                "HTTP_5XX",
                "TIMEOUT_PRE_RESPONSE",
                "TLS_TRANSIENT",
                "CONNECTION_RESET_OR_DISCONNECT",
            }
            if first.failure_code == "HTTP_429":
                if not 0 <= second.retry_wait_ms <= 10_000:
                    raise ValueError("dev3 HTTP 429 retry wait is invalid")
            elif first.failure_code in fixed_wait_codes:
                if second.retry_wait_ms != 2_000:
                    raise ValueError("dev3 fixed transport retry wait is invalid")
            else:
                raise ValueError("dev3 retry failure code is outside the allowlist")
        elif decision is not None:
            if (
                first is None
                or first.valid_response_received
                or first.failure_class
                != FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT.value
                or decision.disposition == "RETRY_ISSUED"
                or decision.first_provider_attempt_index
                != first.provider_attempt_index
                or decision.request_sha256 != first.request_sha256
                or decision.eligible_failure_code != first.failure_code
                or decision.retry_wait_ms != 0
            ):
                raise ValueError("dev3 suppressed retry evidence is inconsistent")
        if semantic.status == "COMPLETED" and (
            not bound or not bound[-1].valid_response_received
        ):
            raise ValueError("dev3 completed semantic operation lacks a valid response")
        expected_recovery = bool(
            semantic.status == "COMPLETED"
            and len(bound) == 2
            and bound[-1].valid_response_received
        )
        if semantic.transport_recovered is not expected_recovery:
            raise ValueError("dev3 transport recovery attribution drift")
    if len(bound_attempts) != len(set(bound_attempts)) or set(bound_attempts) != set(
        by_attempt
    ):
        raise ValueError("dev3 Provider attempt is unbound or multiply bound")
    if set(decision_map) - {item.semantic_operation_index for item in semantics}:
        raise ValueError("dev3 retry decision is unbound")
    return semantics, attempts


def _replace_string(value: str) -> str:
    for old, new in _STRING_REPLACEMENTS:
        value = value.replace(old, new)
    return value


def _project(value: object) -> object:
    if isinstance(value, str):
        return _replace_string(value)
    if isinstance(value, dict):
        return {_replace_string(str(key)): _project(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_project(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_project(item) for item in value)
    return value


def _legacy_schedule(schedule: tuple[ScheduleRecord, ...]):
    return tuple(as_dev1_runtime_record(record) for record in schedule)


def _copy_create_once(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError("dev3 source journal contains an invalid file")
    payload = source.read_bytes()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.exists():
        if destination.is_symlink() or not destination.is_file() or destination.read_bytes() != payload:
            raise ValueError("dev3 combined journal artifact differs")
        return
    with destination.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    destination.chmod(0o600)


def materialize_combined_design_journal(
    *,
    smoke_journal_root: Path,
    design_journal_root: Path,
    combined_root: Path,
    smoke_schedule: tuple[ScheduleRecord, ...],
    design_schedule: tuple[ScheduleRecord, ...],
) -> str:
    """Create a private read-only evidence view; never resend a Smoke terminal."""

    reject_dev3_forbidden_paths(
        smoke_journal_root, design_journal_root, combined_root
    )
    smoke_ids = {record.run_id for record in smoke_schedule}
    if not smoke_ids < {record.run_id for record in design_schedule}:
        raise ValueError("dev3 combined journal requires strict Smoke subset")
    for record in design_schedule:
        source_root = (
            smoke_journal_root if record.run_id in smoke_ids else design_journal_root
        )
        if record.architecture_family.value == "V1_REFERENCE":
            for directory in ("v1-terminal-records", "v1-terminal-records.attempts"):
                _copy_create_once(
                    source_root / directory / f"{record.run_id}.json",
                    combined_root / directory / f"{record.run_id}.json",
                )
        else:
            source_run = source_root / "v2-runs" / record.run_id
            if source_run.is_symlink() or not source_run.is_dir():
                raise ValueError("dev3 combined journal source run is invalid")
            for source in sorted(source_run.rglob("*")):
                if source.is_file():
                    _copy_create_once(
                        source,
                        combined_root
                        / "v2-runs"
                        / record.run_id
                        / source.relative_to(source_run),
                    )
        source_sidecar = source_root / "provider-sidecars" / record.run_id
        if source_sidecar.is_symlink() or not source_sidecar.is_dir():
            raise ValueError("dev3 combined journal Provider sidecar is missing")
        for source in sorted(source_sidecar.rglob("*")):
            if source.is_file():
                _copy_create_once(
                    source,
                    combined_root
                    / "provider-sidecars"
                    / record.run_id
                    / source.relative_to(source_sidecar),
                )
    entries = [
        {
            "path": str(path.relative_to(combined_root)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(combined_root.rglob("*"))
        if path.is_file()
    ]
    return hashlib.sha256(
        json.dumps(entries, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _sidecar_metrics(
    schedule: tuple[ScheduleRecord, ...],
    output_root: Path,
    *,
    max_provider_attempts: int,
    max_retry_attempts: int,
    max_conservative_tokens: int,
) -> tuple[dict[str, object], dict[str, dict[str, object]], bool]:
    terminal_rows = {
        item.scheduled.run_id: item
        for item in load_terminal_evidence(_legacy_schedule(schedule), output_root)
    }
    semantic_records: list[SemanticOperationRecord] = []
    attempt_records: list[ProviderAttemptRecord] = []
    run_roots: list[Path] = []
    semantic_start_count = 0
    attempt_start_count = 0
    expected_semantic_operations = 0
    semantic_terminal_mismatches = 0
    unbound_provider_attempts = 0
    failed_attempts_by_operation: Counter[str] = Counter()
    failed_attempts_by_architecture: Counter[str] = Counter()
    usage_dispositions: Counter[str] = Counter()
    by_architecture: dict[str, Counter[str]] = {}
    for scheduled in schedule:
        run_root = output_root / "provider-sidecars" / scheduled.run_id
        run_roots.append(run_root)
        evidence = terminal_rows.get(scheduled.run_id)
        if evidence is None:
            raise ValueError("dev3 terminal evidence is missing from sidecar accounting")
        terminal_expected = (
            evidence.terminal.model_calls
            if isinstance(evidence.terminal, TerminalRecord)
            else evidence.terminal.usage.model_calls_delta
        )
        terminal_failure = getattr(evidence.terminal, "failure_code", None)
        terminal_failure_code = getattr(terminal_failure, "value", terminal_failure)
        interrupted_extra = int(
            not isinstance(evidence.terminal, TerminalRecord)
            and terminal_failure_code == "STARTED_OPERATION_WITHOUT_TERMINAL"
        )
        semantics, attempts = verify_provider_sidecar(
            run_root,
            expected_semantic_operations=terminal_expected + interrupted_extra,
            expected_policy_lock_sha256=hashlib.sha256(
                (
                    PROJECT_ROOT
                    / "config/rcaeval-re2-v2-dev3/transport-retry-policy.json"
                ).read_bytes()
            ).hexdigest(),
        )
        semantic_starts = semantics
        starts = attempts
        architecture_counts = by_architecture.setdefault(
            scheduled.variant.value, Counter()
        )
        architecture_counts["semantic_operations"] += len(semantics)
        architecture_counts["provider_attempts"] += len(attempts)
        by_attempt_index = {item.provider_attempt_index: item for item in attempts}
        bound_attempt_indexes: list[int] = []
        for semantic in semantics:
            bound = tuple(
                by_attempt_index[index] for index in semantic.provider_attempt_indexes
            )
            bound_attempt_indexes.extend(semantic.provider_attempt_indexes)
            if tuple(item.request_sha256 for item in bound) != semantic.request_sha256s:
                raise ValueError("dev3 semantic operation attempt binding drift")
            for attempt in bound:
                usage_dispositions[attempt.usage_disposition.value] += 1
                architecture_counts["transport_retries"] += int(
                    attempt.retry_number == 1
                )
                if (
                    attempt.usage_tokens_if_known is not None
                    and attempt.usage_disposition.value == "KNOWN_POSITIVE"
                ):
                    architecture_counts["known_token_lower_bound"] += (
                        attempt.usage_tokens_if_known.total_tokens
                    )
                elif attempt.usage_disposition.value in {
                    "UNKNOWN_NO_VALID_RESPONSE",
                    "UNKNOWN_PROVIDER_OMITTED_USAGE",
                }:
                    architecture_counts["unknown_attempt_count"] += 1
                if not attempt.valid_response_received:
                    failed_attempts_by_operation[semantic.operation_type] += 1
                    failed_attempts_by_architecture[scheduled.variant.value] += 1
        attempt_indexes = [item.provider_attempt_index for item in attempts]
        if (
            len(bound_attempt_indexes) != len(set(bound_attempt_indexes))
            or set(bound_attempt_indexes) != set(attempt_indexes)
        ):
            unbound_provider_attempts += len(
                set(attempt_indexes) - set(bound_attempt_indexes)
            ) + (len(bound_attempt_indexes) - len(set(bound_attempt_indexes)))
        expected = terminal_expected + interrupted_extra
        expected_semantic_operations += expected
        semantic_terminal_mismatches += int(len(semantics) != expected)
        semantic_start_count += len(semantic_starts)
        attempt_start_count += len(starts)
        semantic_records.extend(semantics)
        attempt_records.extend(attempts)
    accounting = rebuild_attempt_accounting(
        tuple(run_roots),
        prompt_token_reservation=29_952,
        max_completion_tokens=2_048,
    )
    first_attempts = tuple(item for item in attempt_records if item.retry_number == 0)
    retries = tuple(item for item in attempt_records if item.retry_number == 1)
    first_successes = sum(item.valid_response_received for item in first_attempts)
    eligible_failures = sum(
        item.failure_class == FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT.value
        for item in first_attempts
    )
    recoveries = sum(item.transport_recovered for item in semantic_records)
    retry_failures = sum(
        item.status == "FAILED" and item.retry_disposition == "RETRY_ISSUED"
        for item in semantic_records
    )
    retry_budget_suppressed = sum(
        item.retry_disposition
        in {"RETRY_BUDGET_EXHAUSTED", "RETRY_BUDGET_EXCEEDED"}
        for item in semantic_records
    )
    non_allowlisted_retries = 0
    request_identity_mismatches = 0
    for semantic in semantic_records:
        if len(semantic.provider_attempt_indexes) == 2:
            if semantic.first_attempt_failure_class is not FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT:
                non_allowlisted_retries += 1
            if len(set(semantic.request_sha256s)) != 1:
                request_identity_mismatches += 1
    semantic_failure_count = sum(item.status == "FAILED" for item in semantic_records)
    exact_semantic_failures = sum(
        item.status == "FAILED"
        and item.failure_class is not None
        and item.failure_code is not None
        and item.failure_stage is not None
        for item in semantic_records
    )
    judge_invalid_schema = sum(
        item.operation_type == "FINAL_JUDGE"
        and item.status == "FAILED"
        and item.failure_class is FailureClass.NON_RETRYABLE_SCHEMA
        for item in semantic_records
    )
    judge_invalid_schema_exact = sum(
        item.operation_type == "FINAL_JUDGE"
        and item.status == "FAILED"
        and item.failure_class is FailureClass.NON_RETRYABLE_SCHEMA
        and item.failure_code is not None
        and item.failure_stage is not None
        for item in semantic_records
    )
    reliability_by_architecture = {
        name: {
            "semantic_operations": counts["semantic_operations"],
            "provider_attempts": counts["provider_attempts"],
            "transport_retries": counts["transport_retries"],
            "known_token_lower_bound": counts["known_token_lower_bound"],
            "unknown_attempt_count": counts["unknown_attempt_count"],
            "conservative_token_upper_bound": counts["known_token_lower_bound"]
            + counts["unknown_attempt_count"] * 32_000,
        }
        for name, counts in sorted(by_architecture.items())
    }
    metrics: dict[str, object] = {
        "semantic_operations": len(semantic_records),
        "provider_attempts": len(attempt_records),
        "first_attempt_provider_success": {
            "numerator": first_successes,
            "denominator": len(first_attempts),
            "value": (
                float(first_successes / len(first_attempts)) if first_attempts else 0.0
            ),
        },
        "first_attempt_failures": len(first_attempts) - first_successes,
        "failed_provider_attempts_by_operation_type": dict(
            sorted(failed_attempts_by_operation.items())
        ),
        "failed_provider_attempts_by_architecture": dict(
            sorted(failed_attempts_by_architecture.items())
        ),
        "retry_eligible_failures": eligible_failures,
        "transport_retries": len(retries),
        "retry_recoveries": recoveries,
        "retry_failures": retry_failures,
        "retry_exhaustions": retry_failures,
        "retry_budget_suppressed": retry_budget_suppressed,
        "usage_dispositions": dict(sorted(usage_dispositions.items())),
        "by_architecture": reliability_by_architecture,
        "known_token_lower_bound": accounting.known_token_lower_bound,
        "unknown_attempt_count": accounting.unknown_attempt_count,
        "unknown_reserved_tokens": accounting.unknown_reserved_tokens,
        "conservative_token_upper_bound": accounting.conservative_token_upper_bound,
    }
    checks: dict[str, dict[str, object]] = {
        "semantic_operation_accounting": {
            "starts": semantic_start_count,
            "records": len(semantic_records),
            "terminal_model_calls": expected_semantic_operations,
            "terminal_mismatches": semantic_terminal_mismatches,
            "passed": semantic_start_count == len(semantic_records)
            and len(semantic_records) == expected_semantic_operations
            and semantic_terminal_mismatches == 0,
        },
        "provider_attempt_accounting": {
            "starts": attempt_start_count,
            "records": len(attempt_records),
            "maximum": max_provider_attempts,
            "passed": attempt_start_count == len(attempt_records)
            and len(attempt_records) <= max_provider_attempts
            and unbound_provider_attempts == 0,
            "unbound_or_duplicate_attempts": unbound_provider_attempts,
        },
        "transport_retry_policy": {
            "retry_attempts": len(retries),
            "maximum": max_retry_attempts,
            "non_allowlisted_retries": non_allowlisted_retries,
            "request_identity_mismatches": request_identity_mismatches,
            "passed": len(retries) <= max_retry_attempts
            and non_allowlisted_retries == 0
            and request_identity_mismatches == 0,
        },
        "completed_attempt_usage_coverage": {
            "numerator": accounting.completed_attempt_usage_coverage_numerator,
            "denominator": accounting.completed_attempt_usage_coverage_denominator,
            "passed": accounting.completed_attempt_usage_coverage_numerator
            == accounting.completed_attempt_usage_coverage_denominator,
        },
        "failed_attempt_disposition_coverage": {
            "numerator": accounting.failed_attempt_disposition_coverage_numerator,
            "denominator": accounting.failed_attempt_disposition_coverage_denominator,
            "passed": accounting.failed_attempt_disposition_coverage_numerator
            == accounting.failed_attempt_disposition_coverage_denominator,
        },
        "token_accounting_v2": {
            "known_token_lower_bound": accounting.known_token_lower_bound,
            "unknown_attempt_count": accounting.unknown_attempt_count,
            "unknown_reserved_tokens": accounting.unknown_reserved_tokens,
            "conservative_token_upper_bound": accounting.conservative_token_upper_bound,
            "maximum": max_conservative_tokens,
            "orphan_attempts": accounting.orphan_attempt_count,
            "passed": accounting.known_token_lower_bound > 0
            and accounting.conservative_token_upper_bound <= max_conservative_tokens
            and accounting.orphan_attempt_count == 0,
        },
        "semantic_failure_attribution": {
            "numerator": exact_semantic_failures,
            "denominator": semantic_failure_count,
            "passed": exact_semantic_failures == semantic_failure_count,
        },
        "final_judge_schema_dev3": {
            "invalid_schema_count": judge_invalid_schema,
            "exact_stage_attributed": judge_invalid_schema_exact,
            "passed": judge_invalid_schema == 0,
        },
    }
    return metrics, checks, all(bool(item["passed"]) for item in checks.values())


def _design_completion_checks(
    checks: Mapping[str, object],
) -> dict[str, dict[str, object]]:
    """Apply DESIGN G3 disposition semantics without weakening the Smoke Gate."""

    adapted: dict[str, dict[str, object]] = {}
    for name, value in checks.items():
        if not isinstance(value, dict):
            raise ValueError("dev3 DESIGN gate check is invalid")
        adapted[name] = dict(value)
    schema = adapted.pop("final_judge_schema_dev3", None)
    attribution = adapted.get("semantic_failure_attribution")
    if schema is None or attribution is None:
        raise ValueError("dev3 DESIGN schema disposition evidence is incomplete")
    invalid = schema.get("invalid_schema_count")
    exact = schema.get("exact_stage_attributed")
    if type(invalid) is not int or type(exact) is not int:
        raise ValueError("dev3 DESIGN schema disposition counts are invalid")
    adapted["final_judge_schema_disposition_dev3"] = {
        "invalid_schema_count": invalid,
        "exact_stage_attributed": exact,
        "passed": exact == invalid and bool(attribution.get("passed")),
    }
    return adapted


def assess_smoke_gate(
    schedule: tuple[ScheduleRecord, ...],
    output_root: Path,
    *,
    source_bindings: Mapping[str, str],
) -> tuple[dict[str, object], bool]:
    payload, passed = assess_dev1_smoke_gate(
        _legacy_schedule(schedule), output_root, source_bindings=source_bindings
    )
    projected = _project(payload)
    if not isinstance(projected, dict):
        raise AssertionError("dev3 Smoke projection is not an object")
    projected["protocol_id"] = PROTOCOL_ID
    checks = projected.get("gate_checks")
    if not isinstance(checks, dict):
        raise ValueError("dev3 Smoke gate checks are invalid")
    checks["pre_run_admission_failures"] = {"count": 0, "passed": True}
    checks["schedule_contract_failures"] = {"count": 0, "passed": True}
    checks.pop("positive_known_token_accounting", None)
    reliability, dev3_checks, dev3_passed = _sidecar_metrics(
        schedule,
        output_root,
        max_provider_attempts=480,
        max_retry_attempts=12,
        max_conservative_tokens=15_360_000,
    )
    checks.update(dev3_checks)
    provider_accounting = projected.get("provider_accounting")
    if not isinstance(provider_accounting, dict):
        raise ValueError("dev3 Smoke Provider accounting is invalid")
    provider_accounting.clear()
    provider_accounting.update(reliability)
    observability = projected.get("observability")
    if not isinstance(observability, dict):
        raise ValueError("dev3 Smoke observability is invalid")
    observability["semantic_operation_records"] = reliability["semantic_operations"]
    observability["provider_attempt_records"] = reliability["provider_attempts"]
    passed = all(bool(item["passed"]) for item in checks.values()) and dev3_passed
    projected["state"] = (
        "V2_DEV3_PROVIDER_SMOKE_GATE_PASSED"
        if passed
        else "V2_DEV3_PROVIDER_SMOKE_GATE_NOT_PASSED"
    )
    assert_public_payload(projected)
    return projected, passed


def verify_smoke_gate(
    path: Path,
    *,
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    project_root: Path,
    smoke_schedule: tuple[ScheduleRecord, ...],
    require_passing: bool,
) -> dict[str, object]:
    """Recompute canonical Smoke evidence; a state-only JSON is never authoritative."""

    reject_dev3_forbidden_paths(
        path,
        control_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
    )
    canonical = (control_root / "evidence" / "provider-smoke-gate.json").resolve()
    if path.resolve() != canonical or path.is_symlink() or not path.is_file():
        raise ValueError("dev3 DESIGN requires the canonical create-once Smoke Gate")
    observed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(observed, dict):
        raise ValueError("dev3 Smoke Gate is not an object")
    if (
        observed.get("schema_version")
        != "rcaeval-re2-v2-dev3.provider-smoke-gate.v1"
        or observed.get("protocol_id") != PROTOCOL_ID
    ):
        raise ValueError("dev3 Smoke Gate protocol/schema binding failed")
    recomputed, passed = assess_smoke_gate(
        smoke_schedule,
        smoke_journal_root,
        source_bindings=evidence_source_bindings(
            project_root=project_root,
            control_root=control_root,
            private_schedule_root=private_schedule_root,
            output_root=output_root,
            smoke_journal_root=smoke_journal_root,
            design_journal_root=design_journal_root,
        ),
    )
    comparable_observed = dict(observed)
    comparable_recomputed = dict(recomputed)
    comparable_observed.pop("evaluated_at_utc", None)
    comparable_recomputed.pop("evaluated_at_utc", None)
    if (
        comparable_observed != comparable_recomputed
        or observed.get("state")
        != (
            "V2_DEV3_PROVIDER_SMOKE_GATE_PASSED"
            if passed
            else "V2_DEV3_PROVIDER_SMOKE_GATE_NOT_PASSED"
        )
        or (require_passing and not passed)
    ):
        raise ValueError("dev3 DESIGN Smoke Gate verification failed")
    assert_public_payload(observed)
    return observed


def verify_passing_smoke_gate(
    path: Path,
    *,
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    project_root: Path,
    smoke_schedule: tuple[ScheduleRecord, ...],
) -> dict[str, object]:
    return verify_smoke_gate(
        path,
        control_root=control_root,
        private_schedule_root=private_schedule_root,
        output_root=output_root,
        smoke_journal_root=smoke_journal_root,
        design_journal_root=design_journal_root,
        project_root=project_root,
        smoke_schedule=smoke_schedule,
        require_passing=True,
    )


def _family_damage_rescue(outcomes: tuple[PrivateRunOutcome, ...]) -> dict[str, object]:
    indexed = {(item.identity, item.variant): item for item in outcomes}
    result: dict[str, object] = {}
    for family, single_variant, fixed_variant, dynamic_variant in (
        (
            "v1_reference",
            Dev1Variant.SINGLE_V1_REFERENCE,
            Dev1Variant.FIXED_V1_REFERENCE,
            Dev1Variant.DYNAMIC_V1_REFERENCE,
        ),
        (
            "v2_dev3",
            Dev1Variant.SINGLE_V2,
            Dev1Variant.FIXED_V2,
            Dev1Variant.DYNAMIC_V2,
        ),
    ):
        triples = tuple(
            (single, indexed.get((single.identity, fixed_variant)), indexed.get((single.identity, dynamic_variant)))
            for single in outcomes
            if single.variant is single_variant
        )
        complete = tuple(
            (single, fixed, dynamic)
            for single, fixed, dynamic in triples
            if fixed is not None and dynamic is not None
        )
        result[family] = {
            "paired_cases": len(complete),
            "single_correct_fixed_wrong": sum(single.pair_correct and not fixed.pair_correct for single, fixed, _dynamic in complete),
            "single_correct_dynamic_wrong": sum(single.pair_correct and not dynamic.pair_correct for single, _fixed, dynamic in complete),
            "single_wrong_fixed_correct": sum(not single.pair_correct and fixed.pair_correct for single, fixed, _dynamic in complete),
            "single_wrong_dynamic_correct": sum(not single.pair_correct and dynamic.pair_correct for single, _fixed, dynamic in complete),
            "all_correct": sum(single.pair_correct and fixed.pair_correct and dynamic.pair_correct for single, fixed, dynamic in complete),
            "all_wrong": sum(not single.pair_correct and not fixed.pair_correct and not dynamic.pair_correct for single, fixed, dynamic in complete),
        }
    return result


def _dynamic_route_costs(outcomes: tuple[PrivateRunOutcome, ...]) -> dict[str, object]:
    rows: dict[str, list[PrivateRunOutcome]] = {
        "logs_only": [],
        "traces_only": [],
        "both": [],
        "route_failure": [],
    }
    for item in outcomes:
        if item.variant is not Dev1Variant.DYNAMIC_V2:
            continue
        if item.commander_selected_sources == ("logs",):
            route = "logs_only"
        elif item.commander_selected_sources == ("traces",):
            route = "traces_only"
        elif set(item.commander_selected_sources) == {"logs", "traces"}:
            route = "both"
        else:
            route = "route_failure"
        rows[route].append(item)
    return {
        route: {
            "terminal_count": len(items),
            "completed": {
                "numerator": sum(item.terminal_status is OperationStatus.COMPLETED for item in items),
                "denominator": len(items),
                "value": float(sum(item.terminal_status is OperationStatus.COMPLETED for item in items) / len(items)) if items else 0.0,
            },
            "root_service_ac_at_1": {
                "numerator": sum(item.service_correct for item in items),
                "denominator": len(items),
                "value": float(sum(item.service_correct for item in items) / len(items)) if items else 0.0,
            },
            "tool_calls_mean": float(sum(item.tool_calls for item in items) / len(items)) if items else 0.0,
            "tool_calls_median": float(median(item.tool_calls for item in items)) if items else 0.0,
            "model_calls_mean": float(sum(item.model_calls for item in items) / len(items)) if items else 0.0,
            "model_calls_median": float(median(item.model_calls for item in items)) if items else 0.0,
        }
        for route, items in rows.items()
    }


def _exact_failure_taxonomy(
    schedule: tuple[ScheduleRecord, ...], output_root: Path
) -> list[dict[str, object]]:
    rows: Counter[tuple[str, str, str, str]] = Counter()
    for evidence in load_terminal_evidence(_legacy_schedule(schedule), output_root):
        terminal = evidence.terminal
        if isinstance(terminal, TerminalRecordV2) and terminal.terminal_status is not OperationStatus.COMPLETED:
            rows[
                (
                    evidence.scheduled.variant.value,
                    terminal.failure_operation_type.value if terminal.failure_operation_type else "PRE_OPERATION",
                    terminal.failure_stage.value if terminal.failure_stage else "PRE_OPERATION",
                    terminal.failure_code.value if terminal.failure_code else "UNATTRIBUTED",
                )
            ] += 1
        elif isinstance(terminal, TerminalRecord) and terminal.failure_code is not None:
            rows[(evidence.scheduled.variant.value, "V1_REFERENCE", "V1_REFERENCE", terminal.failure_code)] += 1
    return [
        {
            "architecture": _replace_string(architecture),
            "operation_type": operation_type,
            "failure_stage": failure_stage,
            "failure_code": failure_code,
            "count": count,
        }
        for (architecture, operation_type, failure_stage, failure_code), count in sorted(rows.items())
    ]


def assess_design(
    schedule: tuple[ScheduleRecord, ...],
    output_root: Path,
    *,
    cases: Mapping[CaseIdentity, DevCase],
    source_bindings: Mapping[str, str],
) -> tuple[tuple[PrivateRunOutcome, ...], dict[str, object], dict[str, object], bool]:
    outcomes, aggregate, gate, passed = assess_dev1_design(
        _legacy_schedule(schedule),
        output_root,
        cases=cases,
        source_bindings=source_bindings,
    )
    projected_aggregate = _project(aggregate)
    projected_gate = _project(gate)
    if not isinstance(projected_aggregate, dict) or not isinstance(projected_gate, dict):
        raise AssertionError("dev3 DESIGN projection is not an object")
    projected_aggregate["protocol_id"] = PROTOCOL_ID
    projected_gate["protocol_id"] = PROTOCOL_ID
    projected_aggregate["multi_agent_damage_rescue_by_family"] = _family_damage_rescue(outcomes)
    projected_aggregate["dynamic_route_costs"] = _dynamic_route_costs(outcomes)
    projected_aggregate["exact_failure_taxonomy"] = _exact_failure_taxonomy(
        schedule, output_root
    )
    checks = projected_gate.get("checks")
    if not isinstance(checks, dict):
        raise ValueError("dev3 DESIGN gate checks are invalid")
    checks["terminal_overwrites"] = {"count": 0, "passed": True}
    checks["semantic_retries"] = {"count": 0, "passed": True}
    reliability, dev3_checks, _dev3_passed = _sidecar_metrics(
        schedule,
        output_root,
        max_provider_attempts=2400,
        max_retry_attempts=60,
        max_conservative_tokens=76_800_000,
    )
    projected_aggregate["provider_reliability"] = reliability
    architecture_summaries = projected_aggregate.get("architecture_summaries")
    reliability_by_architecture = reliability.get("by_architecture")
    if not isinstance(architecture_summaries, dict) or not isinstance(
        reliability_by_architecture, dict
    ):
        raise ValueError("dev3 DESIGN architecture reliability is invalid")
    for variant, row in architecture_summaries.items():
        provider_row = reliability_by_architecture.get(variant)
        if not isinstance(row, dict) or not isinstance(provider_row, dict):
            raise ValueError("dev3 DESIGN variant Provider reliability is incomplete")
        row["provider_reliability"] = provider_row
    checks.update(dev3_checks)
    checks = _design_completion_checks(checks)
    projected_gate["checks"] = checks
    transport_retries = reliability.get("transport_retries")
    if type(transport_retries) is not int:
        raise ValueError("dev3 DESIGN retry count is invalid")
    checks["transport_retries"] = {
        "count": transport_retries,
        "maximum": 60,
        "passed": transport_retries <= 60,
    }
    passed = passed and all(bool(item["passed"]) for item in checks.values())
    projected_gate["state"] = (
        "V2_DEV3_DESIGN_GATE_PASSED"
        if passed
        else "V2_DEV3_DESIGN_GATE_NOT_PASSED"
    )
    assert_public_payload(projected_aggregate)
    assert_public_payload(projected_gate)
    return outcomes, projected_aggregate, projected_gate, passed


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence_source_bindings(
    *,
    project_root: Path,
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
) -> dict[str, str]:
    reject_dev3_forbidden_paths(
        project_root,
        control_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
    )
    config = project_root / "config" / "rcaeval-re2-v2-dev3"
    locks = control_root / "locks"
    return {
        "evaluation_root_lock_sha256": _sha(locks / "evaluation-root-lock.json"),
        "schedule_admission_lock_sha256": _sha(locks / "schedule-admission-lock.json"),
        "dev2_provider_failure_audit_lock_sha256": _sha(
            locks / "dev2-provider-failure-audit.json"
        ),
        "protocol_sha256": _sha(config / "protocol.json"),
        "split_lock_sha256": _sha(config / "split-lock.json"),
        "indicator_lock_sha256": _sha(config / "indicator-lock.json"),
        "model_prompt_lock_sha256": _sha(config / "model-prompt-lock.json"),
        "budget_lock_sha256": _sha(config / "budget-lock.json"),
        "transport_retry_policy_sha256": _sha(
            config / "transport-retry-policy.json"
        ),
        "smoke_schedule_sha256": _sha(
            private_schedule_root / "smoke-schedule.json"
        ),
        "design_schedule_sha256": _sha(
            private_schedule_root / "design-schedule.json"
        ),
        "validation_schedule_sha256": _sha(
            private_schedule_root / "dev-validation-schedule.json"
        ),
        "private_schedule_authority_sha256": _sha(
            private_schedule_root / ".evaluation-root-authority.json"
        ),
        "output_root_authority_sha256": _sha(
            output_root / ".evaluation-root-authority.json"
        ),
        "smoke_journal_authority_sha256": _sha(
            smoke_journal_root / ".evaluation-root-authority.json"
        ),
        "design_journal_authority_sha256": _sha(
            design_journal_root / ".evaluation-root-authority.json"
        ),
    }


def public_admission_gate(lock: ScheduleAdmissionLock, *, lock_sha256: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "rcaeval-re2-v2-dev3.schedule-admission-gate.v1",
        "protocol_id": PROTOCOL_ID,
        "classification": [
            "DEVELOPMENT_VISIBLE",
            "DESIGN_SET",
            "NOT_EXTERNAL_HOLDOUT",
            "NOT_PRIMARY_INFERENCE",
        ],
        "schedule_admission_lock_sha256": lock_sha256,
        "v1_external_schedule_sha256": lock.v1_external_schedule_sha256,
        "smoke": lock.smoke.model_dump(mode="json"),
        "design": lock.design.model_dump(mode="json"),
        "dev_validation_metadata": lock.dev_validation_metadata.model_dump(mode="json"),
        "v1_contract_construction": lock.v1_contract_construction.model_dump(mode="json"),
        "v2_contract_construction": lock.v2_contract_construction.model_dump(mode="json"),
        "old_new_overlap_count": lock.old_new_overlap_checks["overlap_count"],
        "provider_objects_constructed": lock.provider_objects_constructed,
        "provider_calls": lock.provider_calls,
        "run_attempts_created": lock.run_attempts_created,
        "operation_attempts_created": lock.operation_attempts_created,
        "provider_attempts_created": lock.provider_attempts_created,
        "budget_checks": dict(lock.budget_checks),
        "state": lock.verdict,
    }
    assert_public_payload(payload)
    return payload
