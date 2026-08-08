"""Typed per-attempt accounting for the RCAEval v2-dev.3 Provider boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
from time import monotonic
from typing import Literal, Mapping

from pydantic import AwareDatetime, Field, model_validator

from ecomsre_rcaeval_v2.contracts import Sha256, V2Model


class UsageDisposition(str, Enum):
    KNOWN_POSITIVE = "KNOWN_POSITIVE"
    UNKNOWN_NO_VALID_RESPONSE = "UNKNOWN_NO_VALID_RESPONSE"
    UNKNOWN_PROVIDER_OMITTED_USAGE = "UNKNOWN_PROVIDER_OMITTED_USAGE"
    ZERO_CONFIRMED_NOT_BILLED = "ZERO_CONFIRMED_NOT_BILLED"


class AttemptUsageTokens(V2Model):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def require_exact_total(self) -> AttemptUsageTokens:
        if self.prompt_tokens + self.completion_tokens != self.total_tokens:
            raise ValueError("Provider attempt usage total is inconsistent")
        return self


class ProviderAttemptStart(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev3.provider-attempt-start.v1"]
    semantic_operation_index: int = Field(ge=1)
    provider_attempt_index: int = Field(ge=1)
    retry_number: int = Field(ge=0, le=1)
    request_sha256: Sha256
    started_at_utc: AwareDatetime
    timeout_seconds: float = Field(gt=0)
    retry_wait_ms: int = Field(ge=0, le=10_000)
    prompt_token_reservation: int = Field(ge=1)
    max_completion_tokens: int = Field(ge=1)
    attempt_token_reservation: int = Field(ge=1)
    policy_lock_sha256: Sha256

    @model_validator(mode="after")
    def require_exact_reservation(self) -> ProviderAttemptStart:
        if self.attempt_token_reservation != (
            self.prompt_token_reservation + self.max_completion_tokens
        ):
            raise ValueError("Provider attempt token reservation is inconsistent")
        return self


class ProviderAttemptRecord(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev3.provider-attempt.v1"]
    semantic_operation_index: int = Field(ge=1)
    provider_attempt_index: int = Field(ge=1)
    retry_number: int = Field(ge=0, le=1)
    failure_class: str | None = Field(default=None, max_length=128)
    failure_code: str | None = Field(default=None, max_length=128)
    safe_http_status_class: str | None = Field(default=None, max_length=64)
    request_sha256: Sha256
    started_at_utc: AwareDatetime
    ended_at_utc: AwareDatetime
    latency_ms: float = Field(ge=0)
    valid_response_received: bool
    usage_disposition: UsageDisposition
    usage_tokens_if_known: AttemptUsageTokens | None
    retry_wait_ms: int = Field(ge=0, le=10_000)
    timeout_seconds: float = Field(gt=0)
    attempt_token_reservation: int = Field(ge=1)
    policy_lock_sha256: Sha256

    @model_validator(mode="after")
    def require_attempt_consistency(self) -> ProviderAttemptRecord:
        if self.ended_at_utc < self.started_at_utc:
            raise ValueError("Provider attempt ended before it started")
        if self.valid_response_received:
            if self.failure_class is not None or self.failure_code is not None:
                raise ValueError("valid Provider response cannot claim transport failure")
            if self.usage_disposition is UsageDisposition.UNKNOWN_NO_VALID_RESPONSE:
                raise ValueError("valid response cannot use no-response disposition")
        elif self.usage_disposition is not UsageDisposition.UNKNOWN_NO_VALID_RESPONSE:
            raise ValueError("failed Provider attempt must retain unknown no-response usage")
        known = self.usage_disposition in {
            UsageDisposition.KNOWN_POSITIVE,
            UsageDisposition.ZERO_CONFIRMED_NOT_BILLED,
        }
        if known != (self.usage_tokens_if_known is not None):
            raise ValueError("Provider attempt usage payload differs from disposition")
        if (
            self.usage_disposition is UsageDisposition.KNOWN_POSITIVE
            and self.usage_tokens_if_known is not None
            and self.usage_tokens_if_known.total_tokens <= 0
        ):
            raise ValueError("known-positive Provider usage must be positive")
        if (
            self.usage_disposition is UsageDisposition.ZERO_CONFIRMED_NOT_BILLED
            and self.usage_tokens_if_known is not None
            and self.usage_tokens_if_known.total_tokens != 0
        ):
            raise ValueError("confirmed-zero Provider usage must be zero")
        return self


class AttemptAccountingSummary(V2Model):
    provider_attempt_count: int = Field(ge=0)
    retry_attempt_count: int = Field(ge=0)
    known_token_lower_bound: int = Field(ge=0)
    unknown_attempt_count: int = Field(ge=0)
    unknown_reserved_tokens: int = Field(ge=0)
    conservative_token_upper_bound: int = Field(ge=0)
    orphan_attempt_count: int = Field(ge=0)
    completed_attempt_usage_coverage_numerator: int = Field(ge=0)
    completed_attempt_usage_coverage_denominator: int = Field(ge=0)
    failed_attempt_disposition_coverage_numerator: int = Field(ge=0)
    failed_attempt_disposition_coverage_denominator: int = Field(ge=0)


class AttemptBudgetExceeded(ConnectionError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class MalformedProviderUsage(ValueError):
    """A response contained a usage object that violated the locked schema."""


class AttemptBudget:
    """Sequential phase ledger; every attempt reserves before network I/O."""

    def __init__(
        self,
        *,
        max_provider_attempts: int,
        max_retry_attempts: int,
        prompt_token_reservation: int,
        max_completion_tokens: int,
        max_conservative_tokens: int,
    ) -> None:
        values = (
            max_provider_attempts,
            prompt_token_reservation,
            max_completion_tokens,
            max_conservative_tokens,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("Provider attempt budget values must be positive integers")
        if type(max_retry_attempts) is not int or max_retry_attempts < 0:
            raise ValueError("Provider retry budget must be nonnegative")
        self.max_provider_attempts = max_provider_attempts
        self.max_retry_attempts = max_retry_attempts
        self.prompt_token_reservation = prompt_token_reservation
        self.max_completion_tokens = max_completion_tokens
        self.attempt_token_reservation = (
            prompt_token_reservation + max_completion_tokens
        )
        self.max_conservative_tokens = max_conservative_tokens
        self._attempts = 0
        self._retries = 0
        self._known_tokens = 0
        self._unknown_attempts = 0
        self._zero_attempts = 0
        self._completed_attempts = 0
        self._completed_known_attempts = 0
        self._failed_attempts = 0
        self._open: set[int] = set()

    @classmethod
    def restore(
        cls,
        run_roots: tuple[Path, ...],
        *,
        max_provider_attempts: int,
        max_retry_attempts: int,
        prompt_token_reservation: int,
        max_completion_tokens: int,
        max_conservative_tokens: int,
    ) -> AttemptBudget:
        summary = rebuild_attempt_accounting(
            run_roots,
            prompt_token_reservation=prompt_token_reservation,
            max_completion_tokens=max_completion_tokens,
        )
        if summary.provider_attempt_count > max_provider_attempts:
            raise ValueError("persisted Provider attempts exceed frozen batch cap")
        if summary.retry_attempt_count > max_retry_attempts:
            raise ValueError("persisted Provider retries exceed frozen batch cap")
        if summary.conservative_token_upper_bound > max_conservative_tokens:
            raise ValueError("persisted Provider usage exceeds frozen token budget")
        starts: dict[tuple[str, str], ProviderAttemptStart] = {}
        finals: set[tuple[str, str]] = set()
        for root in run_roots:
            root_key = str(root.resolve(strict=False))
            for path in sorted((root / "provider-attempt-starts").glob("*.json")):
                key = (root_key, path.name)
                starts[key] = ProviderAttemptStart.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            for path in sorted((root / "provider-attempts").glob("*.json")):
                finals.add((root_key, path.name))
        indexes = sorted(item.provider_attempt_index for item in starts.values())
        if indexes != list(range(1, len(indexes) + 1)):
            raise ValueError("persisted Provider attempt indexes are not contiguous")
        restored = cls(
            max_provider_attempts=max_provider_attempts,
            max_retry_attempts=max_retry_attempts,
            prompt_token_reservation=prompt_token_reservation,
            max_completion_tokens=max_completion_tokens,
            max_conservative_tokens=max_conservative_tokens,
        )
        restored._attempts = summary.provider_attempt_count
        restored._retries = summary.retry_attempt_count
        restored._known_tokens = summary.known_token_lower_bound
        restored._unknown_attempts = (
            summary.unknown_attempt_count - summary.orphan_attempt_count
        )
        restored._completed_attempts = (
            summary.completed_attempt_usage_coverage_denominator
        )
        restored._completed_known_attempts = (
            summary.completed_attempt_usage_coverage_numerator
        )
        restored._failed_attempts = (
            summary.failed_attempt_disposition_coverage_denominator
        )
        restored._open = {
            start.provider_attempt_index
            for key, start in starts.items()
            if key not in finals
        }
        return restored

    def ensure_can_reserve(self, *, retry_number: int) -> None:
        if retry_number not in {0, 1}:
            raise ValueError("Provider retry number must be zero or one")
        if self._attempts >= self.max_provider_attempts:
            raise AttemptBudgetExceeded("PROVIDER_ATTEMPT_BUDGET_EXCEEDED")
        if retry_number == 1 and self._retries >= self.max_retry_attempts:
            raise AttemptBudgetExceeded("RETRY_BUDGET_EXHAUSTED")
        projected = (
            self._known_tokens
            + (self._unknown_attempts + len(self._open) + 1)
            * self.attempt_token_reservation
        )
        if projected > self.max_conservative_tokens:
            raise AttemptBudgetExceeded("RETRY_BUDGET_EXCEEDED")

    def reserve(self, *, retry_number: int) -> int:
        self.ensure_can_reserve(retry_number=retry_number)
        self._attempts += 1
        if retry_number == 1:
            self._retries += 1
        index = self._attempts
        self._open.add(index)
        return index

    def rollback_unstarted(self, attempt_index: int, *, retry_number: int) -> None:
        if attempt_index != self._attempts or attempt_index not in self._open:
            raise ValueError("only the newest unstarted Provider reservation can roll back")
        self._open.remove(attempt_index)
        self._attempts -= 1
        if retry_number == 1:
            self._retries -= 1

    def finalize(
        self,
        attempt_index: int,
        disposition: UsageDisposition,
        usage: AttemptUsageTokens | None,
        *,
        valid_response_received: bool,
    ) -> None:
        if attempt_index not in self._open:
            raise ValueError("Provider attempt reservation is not open")
        if usage is not None and usage.total_tokens > self.attempt_token_reservation:
            raise ValueError("Provider usage exceeds frozen attempt reservation")
        self._open.remove(attempt_index)
        if disposition is UsageDisposition.KNOWN_POSITIVE:
            assert usage is not None
            self._known_tokens += usage.total_tokens
        elif disposition in {
            UsageDisposition.UNKNOWN_NO_VALID_RESPONSE,
            UsageDisposition.UNKNOWN_PROVIDER_OMITTED_USAGE,
        }:
            self._unknown_attempts += 1
        else:
            self._zero_attempts += 1
        if valid_response_received:
            self._completed_attempts += 1
            if disposition is UsageDisposition.KNOWN_POSITIVE:
                self._completed_known_attempts += 1
        else:
            self._failed_attempts += 1

    def summary(self) -> AttemptAccountingSummary:
        unknown = self._unknown_attempts + len(self._open)
        reserved = unknown * self.attempt_token_reservation
        return AttemptAccountingSummary(
            provider_attempt_count=self._attempts,
            retry_attempt_count=self._retries,
            known_token_lower_bound=self._known_tokens,
            unknown_attempt_count=unknown,
            unknown_reserved_tokens=reserved,
            conservative_token_upper_bound=self._known_tokens + reserved,
            orphan_attempt_count=len(self._open),
            completed_attempt_usage_coverage_numerator=self._completed_known_attempts,
            completed_attempt_usage_coverage_denominator=self._completed_attempts,
            failed_attempt_disposition_coverage_numerator=self._failed_attempts,
            failed_attempt_disposition_coverage_denominator=self._failed_attempts,
        )


@dataclass(frozen=True, slots=True)
class AttemptHandle:
    start: ProviderAttemptStart
    monotonic_started: float


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _durable_create(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def _write_model(path: Path, value: V2Model) -> None:
    _durable_create(path, _canonical_bytes(value.model_dump(mode="json")))


def _usage_from_response(
    response: Mapping[str, object],
) -> tuple[UsageDisposition, AttemptUsageTokens | None]:
    if "usage" not in response or response.get("usage") is None:
        return UsageDisposition.UNKNOWN_PROVIDER_OMITTED_USAGE, None
    raw = response.get("usage")
    if not isinstance(raw, Mapping):
        raise MalformedProviderUsage("Provider usage must be an object when present")
    values = (
        raw.get("prompt_tokens"),
        raw.get("completion_tokens"),
        raw.get("total_tokens"),
    )
    if not all(type(value) is int and value >= 0 for value in values):
        raise MalformedProviderUsage("Provider usage token fields are malformed")
    prompt, completion, total = values
    assert isinstance(prompt, int)
    assert isinstance(completion, int)
    assert isinstance(total, int)
    if prompt + completion != total:
        raise MalformedProviderUsage("Provider usage token total is inconsistent")
    usage = AttemptUsageTokens(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
    )
    if total == 0:
        return UsageDisposition.ZERO_CONFIRMED_NOT_BILLED, usage
    return UsageDisposition.KNOWN_POSITIVE, usage


class ProviderAttemptJournal:
    def __init__(
        self,
        run_root: Path,
        *,
        budget: AttemptBudget,
        policy_lock_sha256: str,
        timeout_seconds: float,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Provider timeout must be positive")
        self._run_root = run_root
        self._budget = budget
        self._policy_sha = policy_lock_sha256
        self._timeout = float(timeout_seconds)

    def _stem(self, start: ProviderAttemptStart) -> str:
        return (
            f"{start.semantic_operation_index:04d}-"
            f"{start.provider_attempt_index:04d}-{start.retry_number}"
        )

    def start_attempt(
        self,
        *,
        semantic_operation_index: int,
        retry_number: int,
        request_sha256: str,
        retry_wait_ms: int,
    ) -> AttemptHandle:
        attempt_index = self._budget.reserve(retry_number=retry_number)
        start = ProviderAttemptStart(
            schema_version="rcaeval-re2-v2-dev3.provider-attempt-start.v1",
            semantic_operation_index=semantic_operation_index,
            provider_attempt_index=attempt_index,
            retry_number=retry_number,
            request_sha256=request_sha256,
            started_at_utc=datetime.now(timezone.utc),
            timeout_seconds=self._timeout,
            retry_wait_ms=retry_wait_ms,
            prompt_token_reservation=self._budget.prompt_token_reservation,
            max_completion_tokens=self._budget.max_completion_tokens,
            attempt_token_reservation=self._budget.attempt_token_reservation,
            policy_lock_sha256=self._policy_sha,
        )
        try:
            _write_model(
                self._run_root
                / "provider-attempt-starts"
                / f"{self._stem(start)}.json",
                start,
            )
        except Exception:
            self._budget.rollback_unstarted(
                attempt_index, retry_number=retry_number
            )
            raise
        return AttemptHandle(start=start, monotonic_started=monotonic())

    def _finish(
        self,
        handle: AttemptHandle,
        *,
        valid_response_received: bool,
        disposition: UsageDisposition,
        usage: AttemptUsageTokens | None,
        failure_class: str | None,
        failure_code: str | None,
        safe_http_status_class: str | None,
    ) -> ProviderAttemptRecord:
        ended = datetime.now(timezone.utc)
        record = ProviderAttemptRecord(
            schema_version="rcaeval-re2-v2-dev3.provider-attempt.v1",
            semantic_operation_index=handle.start.semantic_operation_index,
            provider_attempt_index=handle.start.provider_attempt_index,
            retry_number=handle.start.retry_number,
            failure_class=failure_class,
            failure_code=failure_code,
            safe_http_status_class=safe_http_status_class,
            request_sha256=handle.start.request_sha256,
            started_at_utc=handle.start.started_at_utc,
            ended_at_utc=ended,
            latency_ms=float(max(0.0, (monotonic() - handle.monotonic_started) * 1_000)),
            valid_response_received=valid_response_received,
            usage_disposition=disposition,
            usage_tokens_if_known=usage,
            retry_wait_ms=handle.start.retry_wait_ms,
            timeout_seconds=handle.start.timeout_seconds,
            attempt_token_reservation=handle.start.attempt_token_reservation,
            policy_lock_sha256=handle.start.policy_lock_sha256,
        )
        if usage is not None and usage.total_tokens > handle.start.attempt_token_reservation:
            raise ValueError("Provider usage exceeds frozen attempt reservation")
        _write_model(
            self._run_root / "provider-attempts" / f"{self._stem(handle.start)}.json",
            record,
        )
        self._budget.finalize(
            handle.start.provider_attempt_index,
            disposition,
            usage,
            valid_response_received=valid_response_received,
        )
        return record

    def finish_response(
        self, handle: AttemptHandle, response: Mapping[str, object]
    ) -> ProviderAttemptRecord:
        disposition, usage = _usage_from_response(response)
        return self._finish(
            handle,
            valid_response_received=True,
            disposition=disposition,
            usage=usage,
            failure_class=None,
            failure_code=None,
            safe_http_status_class=None,
        )

    def finish_failure(
        self,
        handle: AttemptHandle,
        *,
        failure_class: str,
        failure_code: str,
        safe_http_status_class: str | None,
    ) -> ProviderAttemptRecord:
        return self._finish(
            handle,
            valid_response_received=False,
            disposition=UsageDisposition.UNKNOWN_NO_VALID_RESPONSE,
            usage=None,
            failure_class=failure_class,
            failure_code=failure_code,
            safe_http_status_class=safe_http_status_class,
        )


def rebuild_attempt_accounting(
    run_roots: tuple[Path, ...],
    *,
    prompt_token_reservation: int,
    max_completion_tokens: int,
) -> AttemptAccountingSummary:
    reservation = prompt_token_reservation + max_completion_tokens
    starts: dict[tuple[str, str], ProviderAttemptStart] = {}
    finals: dict[tuple[str, str], ProviderAttemptRecord] = {}
    for root in run_roots:
        root_key = str(root.resolve(strict=False))
        for path in sorted((root / "provider-attempt-starts").glob("*.json")):
            key = (root_key, path.name)
            if key in starts:
                raise ValueError("duplicate Provider attempt start")
            starts[key] = ProviderAttemptStart.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        for path in sorted((root / "provider-attempts").glob("*.json")):
            key = (root_key, path.name)
            if key in finals:
                raise ValueError("duplicate Provider attempt final")
            finals[key] = ProviderAttemptRecord.model_validate_json(
                path.read_text(encoding="utf-8")
            )
    if set(finals) - set(starts):
        raise ValueError("Provider attempt final is missing its start marker")
    known = 0
    unknown = 0
    completed = 0
    completed_known = 0
    failed = 0
    retries = 0
    for key, start in starts.items():
        if start.attempt_token_reservation != reservation:
            raise ValueError("Provider attempt reservation differs from frozen budget")
        retries += int(start.retry_number == 1)
        final = finals.get(key)
        if final is None:
            unknown += 1
            continue
        if (
            final.semantic_operation_index != start.semantic_operation_index
            or final.provider_attempt_index != start.provider_attempt_index
            or final.retry_number != start.retry_number
            or final.request_sha256 != start.request_sha256
            or final.policy_lock_sha256 != start.policy_lock_sha256
        ):
            raise ValueError("Provider attempt final differs from start marker")
        if final.valid_response_received:
            completed += 1
            if final.usage_disposition is UsageDisposition.KNOWN_POSITIVE:
                assert final.usage_tokens_if_known is not None
                known += final.usage_tokens_if_known.total_tokens
                completed_known += 1
            elif final.usage_disposition is UsageDisposition.ZERO_CONFIRMED_NOT_BILLED:
                pass
            else:
                unknown += 1
        else:
            failed += 1
            unknown += 1
    orphan = len(set(starts) - set(finals))
    reserved = unknown * reservation
    return AttemptAccountingSummary(
        provider_attempt_count=len(starts),
        retry_attempt_count=retries,
        known_token_lower_bound=known,
        unknown_attempt_count=unknown,
        unknown_reserved_tokens=reserved,
        conservative_token_upper_bound=known + reserved,
        orphan_attempt_count=orphan,
        completed_attempt_usage_coverage_numerator=completed_known,
        completed_attempt_usage_coverage_denominator=completed,
        failed_attempt_disposition_coverage_numerator=failed,
        failed_attempt_disposition_coverage_denominator=failed,
    )


__all__ = [
    "AttemptAccountingSummary",
    "AttemptBudget",
    "AttemptBudgetExceeded",
    "AttemptHandle",
    "AttemptUsageTokens",
    "MalformedProviderUsage",
    "ProviderAttemptJournal",
    "ProviderAttemptRecord",
    "ProviderAttemptStart",
    "UsageDisposition",
    "rebuild_attempt_accounting",
]
