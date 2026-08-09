from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ecomsre_rcaeval_v2.dev3_token_accounting import (
    AttemptBudget,
    AttemptBudgetExceeded,
    ProviderAttemptJournal,
    UsageDisposition,
    rebuild_attempt_accounting,
)
from ecomsre_rcaeval_v2.dev3_provider import SemanticOperationRecord


SHA = "a" * 64


def _budget() -> AttemptBudget:
    return AttemptBudget(
        max_provider_attempts=4,
        max_retry_attempts=1,
        prompt_token_reservation=29_952,
        max_completion_tokens=2_048,
        max_conservative_tokens=128_000,
    )


def test_attempt_journal_records_known_usage_and_releases_reservation(
    tmp_path: Path,
) -> None:
    budget = _budget()
    journal = ProviderAttemptJournal(
        tmp_path,
        budget=budget,
        policy_lock_sha256=SHA,
        timeout_seconds=30.0,
    )

    handle = journal.start_attempt(
        semantic_operation_index=1,
        retry_number=0,
        request_sha256=SHA,
        retry_wait_ms=0,
    )
    record = journal.finish_response(
        handle,
        {
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 2,
                "total_tokens": 7,
            }
        },
    )

    assert record.usage_disposition is UsageDisposition.KNOWN_POSITIVE
    assert record.usage_tokens_if_known is not None
    assert record.usage_tokens_if_known.total_tokens == 7
    assert budget.summary().known_token_lower_bound == 7
    assert budget.summary().unknown_attempt_count == 0
    assert budget.summary().conservative_token_upper_bound == 7
    assert len(tuple((tmp_path / "provider-attempt-starts").glob("*.json"))) == 1
    assert len(tuple((tmp_path / "provider-attempts").glob("*.json"))) == 1


def test_failed_and_orphan_attempts_reserve_unknown_tokens(tmp_path: Path) -> None:
    budget = _budget()
    journal = ProviderAttemptJournal(
        tmp_path,
        budget=budget,
        policy_lock_sha256=SHA,
        timeout_seconds=30.0,
    )
    failed = journal.start_attempt(
        semantic_operation_index=1,
        retry_number=0,
        request_sha256=SHA,
        retry_wait_ms=0,
    )
    record = journal.finish_failure(
        failed,
        failure_class="ALLOWLISTED_TRANSPORT_TRANSIENT",
        failure_code="HTTP_5XX",
        safe_http_status_class="HTTP_5XX",
    )
    journal.start_attempt(
        semantic_operation_index=2,
        retry_number=0,
        request_sha256="b" * 64,
        retry_wait_ms=0,
    )

    assert record.usage_disposition is UsageDisposition.UNKNOWN_NO_VALID_RESPONSE
    rebuilt = rebuild_attempt_accounting(
        (tmp_path,),
        prompt_token_reservation=29_952,
        max_completion_tokens=2_048,
    )
    assert rebuilt.known_token_lower_bound == 0
    assert rebuilt.unknown_attempt_count == 2
    assert rebuilt.unknown_reserved_tokens == 64_000
    assert rebuilt.conservative_token_upper_bound == 64_000
    assert rebuilt.orphan_attempt_count == 1


def test_valid_zero_usage_is_not_conflated_with_unknown(tmp_path: Path) -> None:
    budget = _budget()
    journal = ProviderAttemptJournal(
        tmp_path,
        budget=budget,
        policy_lock_sha256=SHA,
        timeout_seconds=30.0,
    )
    handle = journal.start_attempt(
        semantic_operation_index=1,
        retry_number=0,
        request_sha256=SHA,
        retry_wait_ms=0,
    )
    record = journal.finish_response(
        handle,
        {
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }
        },
    )

    assert record.usage_disposition is UsageDisposition.ZERO_CONFIRMED_NOT_BILLED
    assert budget.summary().unknown_attempt_count == 0
    assert budget.summary().conservative_token_upper_bound == 0


def test_completed_semantic_record_requires_a_valid_provider_attempt() -> None:
    now = datetime.now(timezone.utc)
    common = {
        "schema_version": "rcaeval-re2-v2-dev3.semantic-operation.v1",
        "semantic_operation_index": 1,
        "operation_type": "FINAL_JUDGE",
        "status": "COMPLETED",
        "failure_class": None,
        "failure_code": None,
        "failure_stage": None,
        "started_at_utc": now,
        "ended_at_utc": now,
        "latency_ms": 0.0,
        "transport_recovered": False,
        "first_attempt_failure_class": None,
        "first_attempt_failure_code": None,
        "retry_disposition": None,
        "policy_lock_sha256": SHA,
    }
    with pytest.raises(ValueError, match="requires a Provider attempt"):
        SemanticOperationRecord(
            **common,
            provider_attempt_indexes=(),
            request_sha256s=(),
            attempt_usage_dispositions=(),
        )
    with pytest.raises(ValueError, match="valid final response"):
        SemanticOperationRecord(
            **common,
            provider_attempt_indexes=(1,),
            request_sha256s=(SHA,),
            attempt_usage_dispositions=("UNKNOWN_NO_VALID_RESPONSE",),
        )


def test_batch_budget_rebuild_preserves_attempt_retry_and_token_state(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    original = AttemptBudget(
        max_provider_attempts=8,
        max_retry_attempts=1,
        prompt_token_reservation=29_952,
        max_completion_tokens=2_048,
        max_conservative_tokens=256_000,
    )
    journal = ProviderAttemptJournal(
        first_root,
        budget=original,
        policy_lock_sha256=SHA,
        timeout_seconds=30.0,
    )
    first = journal.start_attempt(
        semantic_operation_index=1,
        retry_number=0,
        request_sha256=SHA,
        retry_wait_ms=0,
    )
    journal.finish_failure(
        first,
        failure_class="ALLOWLISTED_TRANSPORT_TRANSIENT",
        failure_code="HTTP_5XX",
        safe_http_status_class="HTTP_5XX",
    )
    second = journal.start_attempt(
        semantic_operation_index=1,
        retry_number=1,
        request_sha256=SHA,
        retry_wait_ms=2_000,
    )
    journal.finish_response(
        second,
        {
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 2,
                "total_tokens": 7,
            }
        },
    )

    restored = AttemptBudget.restore(
        (first_root,),
        max_provider_attempts=8,
        max_retry_attempts=1,
        prompt_token_reservation=29_952,
        max_completion_tokens=2_048,
        max_conservative_tokens=256_000,
    )

    assert restored.summary() == original.summary()
    next_journal = ProviderAttemptJournal(
        tmp_path / "second",
        budget=restored,
        policy_lock_sha256=SHA,
        timeout_seconds=30.0,
    )
    next_attempt = next_journal.start_attempt(
        semantic_operation_index=1,
        retry_number=0,
        request_sha256="b" * 64,
        retry_wait_ms=0,
    )
    assert next_attempt.start.provider_attempt_index == 3
    with pytest.raises(AttemptBudgetExceeded, match="RETRY_BUDGET_EXHAUSTED"):
        restored.ensure_can_reserve(retry_number=1)
