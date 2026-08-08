"""Versioned Provider failure audit and retry primitives for RCAEval v2-dev.3."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
import hashlib
import http.client
import json
import os
from pathlib import Path
import ssl
from time import monotonic, sleep
from typing import Literal
import urllib.error

from pydantic import AwareDatetime, Field, ValidationError, model_validator

from ecomsre.model.gateway import OpenAICompatibleTransport, ProviderProtocolError
from ecomsre_rcaeval.normalization import UnresolvedServiceAlias
from ecomsre_rcaeval.provider import ProviderDiagnosisError
from ecomsre_rcaeval_v2.contracts import Sha256, V2Model
from ecomsre_rcaeval_v2.dev3_token_accounting import (
    AttemptBudget,
    AttemptBudgetExceeded,
    MalformedProviderUsage,
    ProviderAttemptJournal,
    ProviderAttemptRecord,
    ProviderAttemptStart,
    UsageDisposition,
)
from ecomsre_rcaeval_v2.provider import ProviderOutputValidationError


class FailureClass(str, Enum):
    ALLOWLISTED_TRANSPORT_TRANSIENT = "ALLOWLISTED_TRANSPORT_TRANSIENT"
    NON_RETRYABLE_SCHEMA = "NON_RETRYABLE_SCHEMA"
    NON_RETRYABLE_PROTOCOL = "NON_RETRYABLE_PROTOCOL"
    NON_RETRYABLE_LOCAL_CONTRACT = "NON_RETRYABLE_LOCAL_CONTRACT"
    UNKNOWN_INSUFFICIENT_EVIDENCE = "UNKNOWN_INSUFFICIENT_EVIDENCE"


SafeHttpStatusClass = Literal[
    "HTTP_429",
    "HTTP_5XX",
    "HTTP_4XX_NON_429",
    "HTTP_OTHER",
]


class Dev2FailureEvidence(V2Model):
    """Identity-free evidence retained from one immutable dev.2 failed run."""

    architecture_family: Literal["V1_REFERENCE", "V2"]
    variant: str = Field(min_length=1, max_length=64)
    operation_type: str = Field(min_length=1, max_length=64)
    operation_stage: str = Field(min_length=1, max_length=64)
    failure_code: str = Field(min_length=1, max_length=128)
    safe_http_status_class: SafeHttpStatusClass | None
    provider_attempt_index: int = Field(ge=1)
    provider_call_index: int = Field(ge=1)
    latency_bucket: str = Field(min_length=1, max_length=32)
    valid_response_received: bool
    parsed_tool_call_received: bool = False
    semantic_result_received: bool = False
    usage_object_received: bool | None
    token_usage_known: bool
    timestamp_bucket: str = Field(min_length=1, max_length=32)
    canonical_request_sha256: Sha256 | None


class Dev2FailureAuditGroup(Dev2FailureEvidence):
    failure_class: FailureClass
    retry_eligible: bool
    count: int = Field(ge=1)


class Dev2FailureAudit(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev3.failure-audit.v1"]
    failure_count: int = Field(ge=1)
    failure_class_counts: dict[FailureClass, int]
    retry_eligible_count: int = Field(ge=0)
    retry_ineligible_count: int = Field(ge=0)
    usage_known_count: int = Field(ge=0)
    usage_unknown_count: int = Field(ge=0)
    groups: tuple[Dev2FailureAuditGroup, ...]


_SCHEMA_FAILURES = {
    "PROVIDER_OUTPUT_INVALID_SCHEMA",
    "PROVIDER_OUTPUT_UNRESOLVED_SERVICE_ALIAS",
}
_PROTOCOL_FAILURES = {"PROVIDER_PROTOCOL_VIOLATION"}
_LOCAL_FAILURES = {
    "RUNTIME_CONTRACT_VIOLATION",
    "AGENT_VISIBLE_PRIVATE_PATH_REMAINED",
    "NO_INDICATOR_CANDIDATE",
}


def classify_failure(evidence: Dev2FailureEvidence) -> FailureClass:
    if evidence.failure_code in _SCHEMA_FAILURES:
        return FailureClass.NON_RETRYABLE_SCHEMA
    if evidence.failure_code in _PROTOCOL_FAILURES:
        return FailureClass.NON_RETRYABLE_PROTOCOL
    if evidence.failure_code in _LOCAL_FAILURES or evidence.operation_stage != "PROVIDER_CALL":
        return FailureClass.NON_RETRYABLE_LOCAL_CONTRACT
    if evidence.failure_code == "PROVIDER_TIMEOUT":
        if not evidence.valid_response_received:
            return FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT
        return FailureClass.NON_RETRYABLE_PROTOCOL
    if evidence.failure_code == "PROVIDER_TRANSPORT_FAILURE":
        if evidence.safe_http_status_class in {"HTTP_429", "HTTP_5XX"}:
            return FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT
        if evidence.safe_http_status_class in {"HTTP_4XX_NON_429", "HTTP_OTHER"}:
            return FailureClass.NON_RETRYABLE_PROTOCOL
        return FailureClass.UNKNOWN_INSUFFICIENT_EVIDENCE
    return FailureClass.UNKNOWN_INSUFFICIENT_EVIDENCE


def retry_eligible(evidence: Dev2FailureEvidence) -> bool:
    return (
        classify_failure(evidence)
        is FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT
        and not evidence.valid_response_received
        and not evidence.parsed_tool_call_received
        and not evidence.semantic_result_received
        and evidence.canonical_request_sha256 is not None
    )


def audit_dev2_failures(
    failures: tuple[Dev2FailureEvidence, ...],
) -> Dev2FailureAudit:
    if not failures:
        raise ValueError("dev.2 Provider failure audit requires at least one failure")
    grouped: Counter[tuple[object, ...]] = Counter()
    rows: dict[tuple[object, ...], tuple[Dev2FailureEvidence, FailureClass, bool]] = {}
    classes: Counter[FailureClass] = Counter()
    eligible = 0
    known = 0
    for evidence in failures:
        failure_class = classify_failure(evidence)
        is_eligible = retry_eligible(evidence)
        dumped = evidence.model_dump(mode="json")
        key = tuple((name, dumped[name]) for name in sorted(dumped))
        grouped[key] += 1
        rows[key] = (evidence, failure_class, is_eligible)
        classes[failure_class] += 1
        eligible += int(is_eligible)
        known += int(evidence.token_usage_known)
    groups = tuple(
        Dev2FailureAuditGroup(
            **evidence.model_dump(),
            failure_class=failure_class,
            retry_eligible=is_eligible,
            count=grouped[key],
        )
        for key, (evidence, failure_class, is_eligible) in sorted(
            rows.items(), key=lambda item: repr(item[0])
        )
    )
    return Dev2FailureAudit(
        schema_version="rcaeval-re2-v2-dev3.failure-audit.v1",
        failure_count=len(failures),
        failure_class_counts=dict(classes),
        retry_eligible_count=eligible,
        retry_ineligible_count=len(failures) - eligible,
        usage_known_count=known,
        usage_unknown_count=len(failures) - known,
        groups=groups,
    )


class RetryDecision(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev3.retry-decision.v1"]
    semantic_operation_index: int = Field(ge=1)
    first_provider_attempt_index: int = Field(ge=1)
    request_sha256: Sha256
    eligible_failure_code: str = Field(min_length=1, max_length=128)
    disposition: Literal[
        "RETRY_ISSUED",
        "RETRY_BUDGET_EXHAUSTED",
        "RETRY_BUDGET_EXCEEDED",
        "REQUEST_IDENTITY_MISMATCH",
    ]
    retry_wait_ms: int = Field(ge=0, le=10_000)
    created_at_utc: AwareDatetime


class SemanticOperationStart(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev3.semantic-operation-start.v1"]
    semantic_operation_index: int = Field(ge=1)
    operation_type: Literal[
        "METRICS_SPECIALIST",
        "LOGS_SPECIALIST",
        "TRACES_SPECIALIST",
        "COMMANDER",
        "FINAL_JUDGE",
    ]
    started_at_utc: AwareDatetime
    policy_lock_sha256: Sha256


class SemanticOperationRecord(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev3.semantic-operation.v1"]
    semantic_operation_index: int = Field(ge=1)
    operation_type: Literal[
        "METRICS_SPECIALIST",
        "LOGS_SPECIALIST",
        "TRACES_SPECIALIST",
        "COMMANDER",
        "FINAL_JUDGE",
    ]
    status: Literal["COMPLETED", "FAILED"]
    failure_class: FailureClass | None
    failure_code: str | None = Field(default=None, max_length=128)
    failure_stage: Literal[
        "INPUT_CONSTRUCTION", "PROVIDER_CALL", "OUTPUT_VALIDATION"
    ] | None
    started_at_utc: AwareDatetime
    ended_at_utc: AwareDatetime
    latency_ms: float = Field(ge=0)
    provider_attempt_indexes: tuple[int, ...] = Field(max_length=2)
    request_sha256s: tuple[Sha256, ...] = Field(max_length=2)
    attempt_usage_dispositions: tuple[str, ...] = Field(max_length=2)
    transport_recovered: bool
    first_attempt_failure_class: FailureClass | None
    first_attempt_failure_code: str | None = Field(default=None, max_length=128)
    retry_disposition: str | None = Field(default=None, max_length=64)
    policy_lock_sha256: Sha256

    @model_validator(mode="after")
    def require_semantic_consistency(self) -> SemanticOperationRecord:
        if self.ended_at_utc < self.started_at_utc:
            raise ValueError("semantic operation ended before it started")
        if not (
            len(self.provider_attempt_indexes)
            == len(self.request_sha256s)
            == len(self.attempt_usage_dispositions)
        ):
            raise ValueError("semantic operation attempt evidence is inconsistent")
        if len(set(self.provider_attempt_indexes)) != len(
            self.provider_attempt_indexes
        ):
            raise ValueError("semantic operation attempt indexes must be unique")
        if len(self.request_sha256s) == 2 and len(set(self.request_sha256s)) != 1:
            raise ValueError("transport retry request hashes differ")
        if self.status == "COMPLETED":
            if (
                self.failure_class is not None
                or self.failure_code is not None
                or self.failure_stage is not None
            ):
                raise ValueError("completed semantic operation cannot claim failure")
            if not self.provider_attempt_indexes:
                raise ValueError("completed semantic operation requires a Provider attempt")
            if self.attempt_usage_dispositions[-1] == "UNKNOWN_NO_VALID_RESPONSE":
                raise ValueError("completed semantic operation requires a valid final response")
        elif (
            self.failure_class is None
            or self.failure_code is None
            or self.failure_stage is None
        ):
            raise ValueError("failed semantic operation requires typed failure")
        if self.transport_recovered:
            if (
                self.status != "COMPLETED"
                or len(self.provider_attempt_indexes) != 2
                or self.first_attempt_failure_class
                is not FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT
                or self.retry_disposition != "RETRY_ISSUED"
            ):
                raise ValueError("transport recovery evidence is inconsistent")
        if len(self.provider_attempt_indexes) == 2 and (
            self.first_attempt_failure_class
            is not FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT
            or self.retry_disposition != "RETRY_ISSUED"
        ):
            raise ValueError("second Provider attempt requires an issued transport retry")
        if (
            len(self.provider_attempt_indexes) < 2
            and self.retry_disposition == "RETRY_ISSUED"
        ):
            raise ValueError("issued retry requires a durable second Provider attempt")
        return self


class Dev3RetrySuppressedError(ConnectionError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class _TransportFailure:
    failure_class: FailureClass
    failure_code: str
    safe_http_status_class: SafeHttpStatusClass | None
    retry_wait_seconds: float

    @property
    def retry_eligible(self) -> bool:
        return self.failure_class is FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT


def _exception_chain(error: BaseException) -> tuple[BaseException, ...]:
    found: list[BaseException] = []
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        found.append(current)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if isinstance(current, urllib.error.URLError) and isinstance(
            current.reason, BaseException
        ):
            pending.append(current.reason)
    return tuple(found)


def _retry_after_seconds(error: urllib.error.HTTPError) -> float:
    value = error.headers.get("Retry-After") if error.headers is not None else None
    if not isinstance(value, str) or not value.isascii():
        return 3.0
    normalized = value.strip()
    if normalized.isdigit():
        return float(min(int(normalized), 10))
    try:
        retry_at = parsedate_to_datetime(normalized)
    except (TypeError, ValueError, OverflowError):
        return 3.0
    if retry_at.tzinfo is None:
        return 3.0
    remaining = (retry_at.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
    if remaining < 0:
        return 0.0
    if remaining <= 10:
        return float(remaining)
    return 10.0


def _classify_transport_error(error: BaseException) -> _TransportFailure:
    chain = _exception_chain(error)
    http_error = next(
        (item for item in chain if isinstance(item, urllib.error.HTTPError)), None
    )
    if isinstance(http_error, urllib.error.HTTPError):
        if http_error.code == 429:
            return _TransportFailure(
                FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT,
                "HTTP_429",
                "HTTP_429",
                _retry_after_seconds(http_error),
            )
        if 500 <= http_error.code <= 599:
            return _TransportFailure(
                FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT,
                "HTTP_5XX",
                "HTTP_5XX",
                2.0,
            )
        if 400 <= http_error.code <= 499:
            return _TransportFailure(
                FailureClass.NON_RETRYABLE_PROTOCOL,
                "HTTP_4XX_NON_429",
                "HTTP_4XX_NON_429",
                0.0,
            )
        return _TransportFailure(
            FailureClass.NON_RETRYABLE_PROTOCOL,
            "HTTP_OTHER",
            "HTTP_OTHER",
            0.0,
        )
    if any(isinstance(item, TimeoutError) for item in chain):
        return _TransportFailure(
            FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT,
            "TIMEOUT_PRE_RESPONSE",
            None,
            2.0,
        )
    if any(isinstance(item, ssl.SSLCertVerificationError) for item in chain):
        return _TransportFailure(
            FailureClass.NON_RETRYABLE_PROTOCOL,
            "TLS_CERTIFICATE_FAILURE",
            None,
            0.0,
        )
    if any(
        isinstance(item, (ssl.SSLEOFError, ssl.SSLZeroReturnError)) for item in chain
    ):
        return _TransportFailure(
            FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT,
            "TLS_TRANSIENT",
            None,
            2.0,
        )
    if any(
        isinstance(
            item,
            (
                ConnectionResetError,
                ConnectionAbortedError,
                BrokenPipeError,
                http.client.RemoteDisconnected,
            ),
        )
        for item in chain
    ):
        return _TransportFailure(
            FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT,
            "CONNECTION_RESET_OR_DISCONNECT",
            None,
            2.0,
        )
    if isinstance(error, ProviderProtocolError):
        return _TransportFailure(
            FailureClass.NON_RETRYABLE_PROTOCOL,
            "PROVIDER_PROTOCOL_VIOLATION",
            None,
            0.0,
        )
    if isinstance(error, ConnectionError):
        return _TransportFailure(
            FailureClass.UNKNOWN_INSUFFICIENT_EVIDENCE,
            "UNKNOWN_TRANSPORT_FAILURE",
            None,
            0.0,
        )
    return _TransportFailure(
        FailureClass.NON_RETRYABLE_LOCAL_CONTRACT,
        "LOCAL_TRANSPORT_CONTRACT_FAILURE",
        None,
        0.0,
    )


def _wire_request_bytes(payload: Mapping[str, object]) -> bytes:
    """Mirror StdlibOpenAICompatibleTransport's exact request serialization."""

    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_retry_decision(run_root: Path, decision: RetryDecision) -> None:
    _write_safe_model(
        run_root / "retry-decisions" / f"{decision.semantic_operation_index:04d}.json",
        decision,
    )


def _write_safe_model(path: Path, value: V2Model) -> None:
    payload = (
        json.dumps(
            value.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def seal_interrupted_provider_sidecar(
    run_root: Path,
    *,
    policy_lock_sha256: str,
    expected_timeout_seconds: float,
    fallback_operation_type: Literal[
        "METRICS_SPECIALIST",
        "LOGS_SPECIALIST",
        "TRACES_SPECIALIST",
        "COMMANDER",
        "FINAL_JUDGE",
    ]
    | None = None,
) -> bool:
    """Seal durable starts after interruption without issuing any Provider call."""

    if run_root.is_symlink():
        raise ValueError("interrupted Provider sidecar root cannot be a symlink")
    if not run_root.exists() and fallback_operation_type is None:
        return False
    run_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    semantic_starts = {
        item.semantic_operation_index: item
        for path in sorted((run_root / "semantic-operation-starts").glob("*.json"))
        for item in (
            SemanticOperationStart.model_validate_json(
                path.read_text(encoding="utf-8")
            ),
        )
    }
    semantic_finals = {
        item.semantic_operation_index: item
        for path in sorted((run_root / "semantic-operations").glob("*.json"))
        for item in (
            SemanticOperationRecord.model_validate_json(
                path.read_text(encoding="utf-8")
            ),
        )
    }
    if set(semantic_finals) - set(semantic_starts):
        raise ValueError("semantic operation final is missing its start marker")
    changed = False
    if not semantic_starts and fallback_operation_type is not None:
        synthetic_start = SemanticOperationStart(
            schema_version="rcaeval-re2-v2-dev3.semantic-operation-start.v1",
            semantic_operation_index=1,
            operation_type=fallback_operation_type,
            started_at_utc=datetime.now(timezone.utc),
            policy_lock_sha256=policy_lock_sha256,
        )
        _write_safe_model(
            run_root / "semantic-operation-starts" / "0001.json", synthetic_start
        )
        semantic_starts[1] = synthetic_start
        changed = True
    attempt_starts = {
        item.provider_attempt_index: (path, item)
        for path in sorted((run_root / "provider-attempt-starts").glob("*.json"))
        for item in (
            ProviderAttemptStart.model_validate_json(path.read_text(encoding="utf-8")),
        )
    }
    attempt_finals = {
        item.provider_attempt_index: item
        for path in sorted((run_root / "provider-attempts").glob("*.json"))
        for item in (
            ProviderAttemptRecord.model_validate_json(path.read_text(encoding="utf-8")),
        )
    }
    if set(attempt_finals) - set(attempt_starts):
        raise ValueError("Provider attempt final is missing its start marker")
    for attempt_index, (start_path, attempt_start) in attempt_starts.items():
        if attempt_index in attempt_finals:
            continue
        if (
            attempt_start.policy_lock_sha256 != policy_lock_sha256
            or attempt_start.timeout_seconds != expected_timeout_seconds
        ):
            raise ValueError("interrupted Provider attempt differs from active policy")
        ended = datetime.now(timezone.utc)
        attempt_record = ProviderAttemptRecord(
            schema_version="rcaeval-re2-v2-dev3.provider-attempt.v1",
            semantic_operation_index=attempt_start.semantic_operation_index,
            provider_attempt_index=attempt_start.provider_attempt_index,
            retry_number=attempt_start.retry_number,
            failure_class=FailureClass.UNKNOWN_INSUFFICIENT_EVIDENCE.value,
            failure_code="INTERRUPTED_PROVIDER_ATTEMPT",
            safe_http_status_class=None,
            request_sha256=attempt_start.request_sha256,
            started_at_utc=attempt_start.started_at_utc,
            ended_at_utc=max(attempt_start.started_at_utc, ended),
            latency_ms=0.0,
            valid_response_received=False,
            usage_disposition=UsageDisposition.UNKNOWN_NO_VALID_RESPONSE,
            usage_tokens_if_known=None,
            retry_wait_ms=attempt_start.retry_wait_ms,
            timeout_seconds=attempt_start.timeout_seconds,
            attempt_token_reservation=attempt_start.attempt_token_reservation,
            policy_lock_sha256=attempt_start.policy_lock_sha256,
        )
        _write_safe_model(
            run_root / "provider-attempts" / start_path.name,
            attempt_record,
        )
        attempt_finals[attempt_index] = attempt_record
        changed = True
    decisions = {
        item.semantic_operation_index: item
        for path in sorted((run_root / "retry-decisions").glob("*.json"))
        for item in (
            RetryDecision.model_validate_json(path.read_text(encoding="utf-8")),
        )
    }
    attempts_by_semantic: dict[int, tuple[ProviderAttemptRecord, ...]] = {}
    for semantic_index in semantic_starts:
        attempts_by_semantic[semantic_index] = tuple(
            sorted(
                (
                    item
                    for item in attempt_finals.values()
                    if item.semantic_operation_index == semantic_index
                ),
                key=lambda item: item.retry_number,
            )
        )
    for semantic_index, attempts in attempts_by_semantic.items():
        if len(attempts) != 2 or semantic_index in decisions:
            continue
        first, second = attempts
        if (
            first.retry_number != 0
            or second.retry_number != 1
            or first.failure_class
            != FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT.value
            or first.request_sha256 != second.request_sha256
        ):
            raise ValueError("interrupted retry attempt cannot be safely reconstructed")
        recovery_decision = RetryDecision(
            schema_version="rcaeval-re2-v2-dev3.retry-decision.v1",
            semantic_operation_index=semantic_index,
            first_provider_attempt_index=first.provider_attempt_index,
            request_sha256=first.request_sha256,
            eligible_failure_code=first.failure_code or "UNKNOWN_TRANSPORT_FAILURE",
            disposition="RETRY_ISSUED",
            retry_wait_ms=second.retry_wait_ms,
            created_at_utc=datetime.now(timezone.utc),
        )
        _write_retry_decision(run_root, recovery_decision)
        decisions[semantic_index] = recovery_decision
        changed = True
    for semantic_index, semantic_start in semantic_starts.items():
        if semantic_index in semantic_finals:
            continue
        semantic_attempts = attempts_by_semantic[semantic_index]
        semantic_first = semantic_attempts[0] if semantic_attempts else None
        semantic_decision = decisions.get(semantic_index)
        ended = datetime.now(timezone.utc)
        semantic_record = SemanticOperationRecord(
            schema_version="rcaeval-re2-v2-dev3.semantic-operation.v1",
            semantic_operation_index=semantic_index,
            operation_type=semantic_start.operation_type,
            status="FAILED",
            failure_class=FailureClass.UNKNOWN_INSUFFICIENT_EVIDENCE,
            failure_code="INTERRUPTED_SEMANTIC_OPERATION",
            failure_stage=(
                "PROVIDER_CALL" if semantic_attempts else "INPUT_CONSTRUCTION"
            ),
            started_at_utc=semantic_start.started_at_utc,
            ended_at_utc=max(semantic_start.started_at_utc, ended),
            latency_ms=0.0,
            provider_attempt_indexes=tuple(
                item.provider_attempt_index for item in semantic_attempts
            ),
            request_sha256s=tuple(
                item.request_sha256 for item in semantic_attempts
            ),
            attempt_usage_dispositions=tuple(
                item.usage_disposition.value for item in semantic_attempts
            ),
            transport_recovered=False,
            first_attempt_failure_class=(
                None
                if semantic_first is None or semantic_first.failure_class is None
                else FailureClass(semantic_first.failure_class)
            ),
            first_attempt_failure_code=(
                None if semantic_first is None else semantic_first.failure_code
            ),
            retry_disposition=(
                None if semantic_decision is None else semantic_decision.disposition
            ),
            policy_lock_sha256=semantic_start.policy_lock_sha256,
        )
        _write_safe_model(
            run_root / "semantic-operations" / f"{semantic_index:04d}.json",
            semantic_record,
        )
        changed = True
    return changed


class Dev3RetryingTransport:
    """Retry transport attempts inside one Provider semantic operation."""

    def __init__(
        self,
        delegate: OpenAICompatibleTransport,
        *,
        run_root: Path,
        budget: AttemptBudget,
        policy_lock_sha256: str,
        expected_timeout_seconds: float,
        sleeper: Callable[[float], object] = sleep,
    ) -> None:
        if expected_timeout_seconds <= 0:
            raise ValueError("dev.3 Provider timeout must be positive")
        self._delegate = delegate
        self._run_root = run_root
        self._budget = budget
        self._policy_sha = policy_lock_sha256
        self._timeout = float(expected_timeout_seconds)
        self._sleeper = sleeper
        self._semantic_operations = 0

    def _decision(
        self,
        *,
        semantic_operation_index: int,
        first_provider_attempt_index: int,
        request_sha256: str,
        eligible_failure_code: str,
        disposition: str,
        retry_wait_ms: int,
    ) -> None:
        _write_retry_decision(
            self._run_root,
            RetryDecision.model_validate(
                {
                    "schema_version": "rcaeval-re2-v2-dev3.retry-decision.v1",
                    "semantic_operation_index": semantic_operation_index,
                    "first_provider_attempt_index": first_provider_attempt_index,
                    "request_sha256": request_sha256,
                    "eligible_failure_code": eligible_failure_code,
                    "disposition": disposition,
                    "retry_wait_ms": retry_wait_ms,
                    "created_at_utc": datetime.now(timezone.utc),
                }
            ),
        )

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        if float(timeout_seconds) != self._timeout:
            raise ValueError("dev.3 Provider timeout differs from retry policy lock")
        request_bytes = _wire_request_bytes(payload)
        request_sha = hashlib.sha256(request_bytes).hexdigest()
        self._semantic_operations += 1
        semantic_index = self._semantic_operations
        journal = ProviderAttemptJournal(
            self._run_root,
            budget=self._budget,
            policy_lock_sha256=self._policy_sha,
            timeout_seconds=self._timeout,
        )
        first_attempt_index: int | None = None
        last_failure: _TransportFailure | None = None
        for retry_number in (0, 1):
            retry_wait = 0.0
            retry_decision_args: dict[str, object] | None = None
            if retry_number == 1:
                if last_failure is None:
                    raise AssertionError("retry requires a typed first-attempt failure")
                observed = hashlib.sha256(_wire_request_bytes(payload)).hexdigest()
                if observed != request_sha:
                    assert first_attempt_index is not None
                    self._decision(
                        semantic_operation_index=semantic_index,
                        first_provider_attempt_index=first_attempt_index,
                        request_sha256=request_sha,
                        eligible_failure_code=last_failure.failure_code,
                        disposition="REQUEST_IDENTITY_MISMATCH",
                        retry_wait_ms=0,
                    )
                    raise Dev3RetrySuppressedError("REQUEST_IDENTITY_MISMATCH")
                try:
                    self._budget.ensure_can_reserve(retry_number=1)
                except AttemptBudgetExceeded as error:
                    assert first_attempt_index is not None
                    self._decision(
                        semantic_operation_index=semantic_index,
                        first_provider_attempt_index=first_attempt_index,
                        request_sha256=request_sha,
                        eligible_failure_code=last_failure.failure_code,
                        disposition=error.code,
                        retry_wait_ms=0,
                    )
                    raise Dev3RetrySuppressedError(error.code) from error
                retry_wait = last_failure.retry_wait_seconds
                assert first_attempt_index is not None
                self._sleeper(retry_wait)
                retry_decision_args = {
                    "semantic_operation_index": semantic_index,
                    "first_provider_attempt_index": first_attempt_index,
                    "request_sha256": request_sha,
                    "eligible_failure_code": last_failure.failure_code,
                    "disposition": "RETRY_ISSUED",
                    "retry_wait_ms": int(retry_wait * 1_000),
                }
            handle = journal.start_attempt(
                semantic_operation_index=semantic_index,
                retry_number=retry_number,
                request_sha256=request_sha,
                retry_wait_ms=int(retry_wait * 1_000),
            )
            if retry_decision_args is not None:
                self._decision(**retry_decision_args)  # type: ignore[arg-type]
            if first_attempt_index is None:
                first_attempt_index = handle.start.provider_attempt_index
            try:
                decoded = json.loads(request_bytes.decode("utf-8"))
                if not isinstance(decoded, dict):
                    raise ValueError("dev.3 request payload must remain an object")
                response = self._delegate.post_json(
                    url=url,
                    headers=dict(headers),
                    payload=decoded,
                    timeout_seconds=self._timeout,
                )
            except Exception as error:
                last_failure = _classify_transport_error(error)
                journal.finish_failure(
                    handle,
                    failure_class=last_failure.failure_class.value,
                    failure_code=last_failure.failure_code,
                    safe_http_status_class=last_failure.safe_http_status_class,
                )
                if retry_number == 0 and last_failure.retry_eligible:
                    continue
                raise
            try:
                journal.finish_response(handle, response)
            except MalformedProviderUsage as error:
                journal.finish_failure(
                    handle,
                    failure_class=FailureClass.NON_RETRYABLE_PROTOCOL.value,
                    failure_code="PROVIDER_MALFORMED_USAGE",
                    safe_http_status_class=None,
                )
                raise ProviderProtocolError("Provider usage object is malformed") from error
            return response
        raise AssertionError("dev.3 retry loop exhausted without terminal disposition")


def _semantic_failure(
    error: Exception, attempts: tuple[ProviderAttemptRecord, ...]
) -> tuple[
    FailureClass,
    str,
    Literal["INPUT_CONSTRUCTION", "PROVIDER_CALL", "OUTPUT_VALIDATION"],
]:
    if isinstance(error, Dev3RetrySuppressedError):
        failure_class = (
            FailureClass(attempts[0].failure_class)
            if attempts and attempts[0].failure_class is not None
            else FailureClass.NON_RETRYABLE_LOCAL_CONTRACT
        )
        return failure_class, error.code, "PROVIDER_CALL"
    if isinstance(
        error,
        (
            ProviderOutputValidationError,
            ProviderDiagnosisError,
            UnresolvedServiceAlias,
            ValidationError,
            TypeError,
        ),
    ):
        return (
            FailureClass.NON_RETRYABLE_SCHEMA,
            "PROVIDER_OUTPUT_INVALID_SCHEMA",
            "OUTPUT_VALIDATION" if attempts else "INPUT_CONSTRUCTION",
        )
    if isinstance(error, ProviderProtocolError):
        return (
            FailureClass.NON_RETRYABLE_PROTOCOL,
            "PROVIDER_PROTOCOL_VIOLATION",
            "OUTPUT_VALIDATION" if attempts else "INPUT_CONSTRUCTION",
        )
    if attempts and attempts[-1].failure_class is not None:
        return (
            FailureClass(attempts[-1].failure_class),
            attempts[-1].failure_code or "UNKNOWN_TRANSPORT_FAILURE",
            "PROVIDER_CALL",
        )
    return (
        FailureClass.NON_RETRYABLE_LOCAL_CONTRACT,
        "LOCAL_RUNTIME_CONTRACT_FAILURE",
        "INPUT_CONSTRUCTION",
    )


class Dev3ProviderProxy:
    """Common v1/v2 semantic-operation overlay without altering Agent semantics."""

    def __init__(
        self, inner: object, *, run_root: Path, policy_lock_sha256: str
    ) -> None:
        self._inner = inner
        self._run_root = run_root
        self._policy_sha = policy_lock_sha256
        self._semantic_operations = 0

    @property
    def calls(self) -> int:
        value = getattr(self._inner, "calls")
        if not isinstance(value, int):
            raise TypeError("v1 Provider calls counter must be an integer")
        return value

    @property
    def last_usage_tokens(self) -> int | None:
        value = getattr(self._inner, "last_usage_tokens")
        if value is not None and not isinstance(value, int):
            raise TypeError("v1 Provider token counter must be an integer or null")
        return value

    def usage_snapshot(self) -> object:
        return getattr(self._inner, "usage_snapshot")()

    def usage_delta_since(self, before: object) -> object:
        return getattr(self._inner, "usage_delta_since")(before)

    def _attempts_for(
        self, semantic_operation_index: int
    ) -> tuple[ProviderAttemptRecord, ...]:
        records = tuple(
            ProviderAttemptRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted((self._run_root / "provider-attempts").glob("*.json"))
        )
        selected = tuple(
            item
            for item in records
            if item.semantic_operation_index == semantic_operation_index
        )
        return tuple(sorted(selected, key=lambda item: item.retry_number))

    def _retry_decision_for(self, semantic_operation_index: int) -> RetryDecision | None:
        path = self._run_root / "retry-decisions" / f"{semantic_operation_index:04d}.json"
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise ValueError("retry decision artifact is invalid")
        return RetryDecision.model_validate_json(path.read_text(encoding="utf-8"))

    def _invoke(
        self,
        operation_type: Literal[
            "METRICS_SPECIALIST",
            "LOGS_SPECIALIST",
            "TRACES_SPECIALIST",
            "COMMANDER",
            "FINAL_JUDGE",
        ],
        action: Callable[[], object],
    ) -> object:
        self._semantic_operations += 1
        semantic_index = self._semantic_operations
        started = datetime.now(timezone.utc)
        monotonic_started = monotonic()
        start = SemanticOperationStart(
            schema_version="rcaeval-re2-v2-dev3.semantic-operation-start.v1",
            semantic_operation_index=semantic_index,
            operation_type=operation_type,
            started_at_utc=started,
            policy_lock_sha256=self._policy_sha,
        )
        _write_safe_model(
            self._run_root
            / "semantic-operation-starts"
            / f"{semantic_index:04d}.json",
            start,
        )
        error: Exception | None = None
        result: object | None = None
        try:
            result = action()
        except Exception as caught:
            error = caught
        attempts = self._attempts_for(semantic_index)
        decision = self._retry_decision_for(semantic_index)
        failure_class: FailureClass | None = None
        failure_code: str | None = None
        failure_stage: Literal[
            "INPUT_CONSTRUCTION", "PROVIDER_CALL", "OUTPUT_VALIDATION"
        ] | None = None
        if error is not None:
            failure_class, failure_code, failure_stage = _semantic_failure(error, attempts)
        first = attempts[0] if attempts else None
        transport_recovered = bool(
            error is None
            and len(attempts) == 2
            and attempts[0].failure_class
            == FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT.value
            and attempts[1].valid_response_received
        )
        record = SemanticOperationRecord(
            schema_version="rcaeval-re2-v2-dev3.semantic-operation.v1",
            semantic_operation_index=semantic_index,
            operation_type=operation_type,
            status="COMPLETED" if error is None else "FAILED",
            failure_class=failure_class,
            failure_code=failure_code,
            failure_stage=failure_stage,
            started_at_utc=started,
            ended_at_utc=datetime.now(timezone.utc),
            latency_ms=float(
                max(0.0, (monotonic() - monotonic_started) * 1_000)
            ),
            provider_attempt_indexes=tuple(
                item.provider_attempt_index for item in attempts
            ),
            request_sha256s=tuple(item.request_sha256 for item in attempts),
            attempt_usage_dispositions=tuple(
                item.usage_disposition.value for item in attempts
            ),
            transport_recovered=transport_recovered,
            first_attempt_failure_class=(
                None
                if first is None or first.failure_class is None
                else FailureClass(first.failure_class)
            ),
            first_attempt_failure_code=(
                None if first is None else first.failure_code
            ),
            retry_disposition=None if decision is None else decision.disposition,
            policy_lock_sha256=self._policy_sha,
        )
        _write_safe_model(
            self._run_root / "semantic-operations" / f"{semantic_index:04d}.json",
            record,
        )
        if error is not None:
            raise error
        return result

    def specialize(self, *args: object, **kwargs: object) -> object:
        source = args[2] if len(args) >= 3 else kwargs.get("source")
        operation_types = {
            "metrics": "METRICS_SPECIALIST",
            "logs": "LOGS_SPECIALIST",
            "traces": "TRACES_SPECIALIST",
        }
        operation_type = operation_types.get(source) if isinstance(source, str) else None
        if operation_type is None:
            raise ValueError("specialist source is invalid")
        return self._invoke(
            operation_type,  # type: ignore[arg-type]
            lambda: getattr(self._inner, "specialize")(*args, **kwargs),
        )

    def plan_followup(self, *args: object, **kwargs: object) -> object:
        return self._invoke(
            "COMMANDER",
            lambda: getattr(self._inner, "plan_followup")(*args, **kwargs),
        )

    def diagnose(self, *args: object, **kwargs: object) -> object:
        return self._invoke(
            "FINAL_JUDGE",
            lambda: getattr(self._inner, "diagnose")(*args, **kwargs),
        )

    def judge(self, *args: object, **kwargs: object) -> object:
        return self._invoke(
            "FINAL_JUDGE",
            lambda: getattr(self._inner, "judge")(*args, **kwargs),
        )


__all__ = [
    "Dev2FailureAudit",
    "Dev2FailureAuditGroup",
    "Dev2FailureEvidence",
    "Dev3RetrySuppressedError",
    "Dev3RetryingTransport",
    "Dev3ProviderProxy",
    "FailureClass",
    "RetryDecision",
    "SemanticOperationRecord",
    "SemanticOperationStart",
    "audit_dev2_failures",
    "classify_failure",
    "retry_eligible",
    "seal_interrupted_provider_sidecar",
]
