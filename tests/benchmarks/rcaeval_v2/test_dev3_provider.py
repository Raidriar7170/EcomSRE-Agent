from __future__ import annotations

from collections.abc import Mapping
from email.message import Message
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import ssl
import urllib.error

import pytest

from ecomsre.model.gateway import ProviderProtocolError
from ecomsre_rcaeval_v2.dev3_provider import (
    Dev2FailureEvidence,
    Dev3ProviderProxy,
    Dev3RetryingTransport,
    FailureClass,
    audit_dev2_failures,
)
from ecomsre_rcaeval_v2.dev3_token_accounting import AttemptBudget


def test_legacy_transport_failure_without_safe_status_is_not_retry_eligible() -> None:
    audit = audit_dev2_failures(
        (
            Dev2FailureEvidence(
                architecture_family="V2",
                variant="fixed_v2",
                operation_type="METRICS_SPECIALIST",
                operation_stage="PROVIDER_CALL",
                failure_code="PROVIDER_TRANSPORT_FAILURE",
                safe_http_status_class=None,
                provider_attempt_index=1,
                provider_call_index=1,
                latency_bucket="60-120s",
                valid_response_received=False,
                usage_object_received=False,
                token_usage_known=False,
                timestamp_bucket="2026-08-09T19Z",
                canonical_request_sha256=None,
            ),
        )
    )

    assert audit.failure_count == 1
    assert audit.failure_class_counts == {
        FailureClass.UNKNOWN_INSUFFICIENT_EVIDENCE: 1
    }
    assert audit.retry_eligible_count == 0
    assert audit.retry_ineligible_count == 1
    assert audit.groups[0].failure_class is FailureClass.UNKNOWN_INSUFFICIENT_EVIDENCE
    assert audit.groups[0].count == 1
    assert "run_id" not in audit.model_dump_json()
    assert "case_id" not in audit.model_dump_json()


def test_dev2_audit_groups_safe_rows_without_reintroducing_identity() -> None:
    row = Dev2FailureEvidence(
        architecture_family="V1_REFERENCE",
        variant="dynamic_v1_reference",
        operation_type="UNKNOWN_SPECIALIST",
        operation_stage="PROVIDER_CALL",
        failure_code="PROVIDER_TRANSPORT_FAILURE",
        safe_http_status_class=None,
        provider_attempt_index=1,
        provider_call_index=3,
        latency_bucket="120s+",
        valid_response_received=False,
        usage_object_received=None,
        token_usage_known=False,
        timestamp_bucket="UNKNOWN",
        canonical_request_sha256=None,
    )

    audit = audit_dev2_failures((row, row))

    assert audit.failure_count == 2
    assert len(audit.groups) == 1
    assert audit.groups[0].count == 2
    assert audit.usage_known_count == 0
    assert audit.usage_unknown_count == 2


class _ScriptedTransport:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        del url, headers, payload, timeout_seconds
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, BaseException):
            if isinstance(outcome, urllib.error.HTTPError):
                try:
                    raise outcome
                except urllib.error.HTTPError as cause:
                    raise ConnectionError("sanitized transport failure") from cause
            raise outcome
        assert isinstance(outcome, Mapping)
        return outcome


def _attempt_budget(*, retries: int = 1) -> AttemptBudget:
    return AttemptBudget(
        max_provider_attempts=4,
        max_retry_attempts=retries,
        prompt_token_reservation=29_952,
        max_completion_tokens=2_048,
        max_conservative_tokens=128_000,
    )


def _valid_response() -> dict[str, object]:
    return {
        "usage": {
            "prompt_tokens": 5,
            "completion_tokens": 2,
            "total_tokens": 7,
        }
    }


def test_transport_retries_one_5xx_with_same_wire_request_and_fixed_wait(
    tmp_path: Path,
) -> None:
    headers = Message()
    delegate = _ScriptedTransport(
        [
            urllib.error.HTTPError(
                "https://provider.invalid", 503, "unavailable", headers, None
            ),
            _valid_response(),
        ]
    )
    waits: list[float] = []
    transport = Dev3RetryingTransport(
        delegate,
        run_root=tmp_path,
        budget=_attempt_budget(),
        policy_lock_sha256="a" * 64,
        expected_timeout_seconds=30.0,
        sleeper=waits.append,
    )
    payload = {"model": "locked", "messages": [{"role": "user", "content": "x"}]}

    assert transport.post_json(
        url="https://provider.invalid/chat/completions",
        headers={"Authorization": "redacted"},
        payload=payload,
        timeout_seconds=30.0,
    ) == _valid_response()

    assert delegate.calls == 2
    assert waits == [2.0]
    records = sorted((tmp_path / "provider-attempts").glob("*.json"))
    assert len(records) == 2
    values = [json.loads(path.read_text(encoding="utf-8")) for path in records]
    expected_sha = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert {value["request_sha256"] for value in values} == {expected_sha}
    assert [value["retry_number"] for value in values] == [0, 1]
    assert values[0]["failure_class"] == "ALLOWLISTED_TRANSPORT_TRANSIENT"
    assert values[1]["usage_disposition"] == "KNOWN_POSITIVE"


def test_retry_allowlist_waits_for_429_timeout_reset_and_transient_tls(
    tmp_path: Path,
) -> None:
    future = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )
    seconds_headers = Message()
    seconds_headers["Retry-After"] = "7"
    date_headers = Message()
    date_headers["Retry-After"] = future
    scenarios: tuple[tuple[BaseException, float], ...] = (
        (
            urllib.error.HTTPError(
                "https://provider.invalid",
                429,
                "limited",
                seconds_headers,
                None,
            ),
            7.0,
        ),
        (
            urllib.error.HTTPError(
                "https://provider.invalid",
                429,
                "limited",
                date_headers,
                None,
            ),
            10.0,
        ),
        (TimeoutError("timed out"), 2.0),
        (ConnectionResetError("reset"), 2.0),
        (ssl.SSLEOFError(8, "eof"), 2.0),
    )
    for index, (error, expected_wait) in enumerate(scenarios):
        delegate = _ScriptedTransport([error, _valid_response()])
        waits: list[float] = []
        transport = Dev3RetryingTransport(
            delegate,
            run_root=tmp_path / str(index),
            budget=_attempt_budget(),
            policy_lock_sha256="a" * 64,
            expected_timeout_seconds=30.0,
            sleeper=waits.append,
        )
        assert transport.post_json(
            url="https://provider.invalid/chat/completions",
            headers={},
            payload={"model": "locked"},
            timeout_seconds=30.0,
        ) == _valid_response()
        assert len(waits) == 1
        assert abs(waits[0] - expected_wait) < 0.01
        assert delegate.calls == 2


def test_certificate_verification_failure_is_never_retried(tmp_path: Path) -> None:
    delegate = _ScriptedTransport(
        [ssl.SSLCertVerificationError(1, "certificate verify failed")]
    )
    transport = Dev3RetryingTransport(
        delegate,
        run_root=tmp_path,
        budget=_attempt_budget(),
        policy_lock_sha256="a" * 64,
        expected_timeout_seconds=30.0,
        sleeper=lambda _: (_ for _ in ()).throw(
            AssertionError("certificate failure must not wait")
        ),
    )
    try:
        transport.post_json(
            url="https://provider.invalid/chat/completions",
            headers={},
            payload={"model": "locked"},
            timeout_seconds=30.0,
        )
    except ssl.SSLCertVerificationError:
        pass
    else:
        raise AssertionError("certificate failure must propagate")
    assert delegate.calls == 1


def test_non_429_4xx_and_provider_protocol_errors_never_retry(tmp_path: Path) -> None:
    headers = Message()
    for index, error in enumerate(
        (
            urllib.error.HTTPError(
                "https://provider.invalid", 401, "unauthorized", headers, None
            ),
            ProviderProtocolError("invalid provider envelope"),
        ),
        1,
    ):
        delegate = _ScriptedTransport([error])
        transport = Dev3RetryingTransport(
            delegate,
            run_root=tmp_path / str(index),
            budget=_attempt_budget(),
            policy_lock_sha256="a" * 64,
            expected_timeout_seconds=30.0,
            sleeper=lambda _: (_ for _ in ()).throw(
                AssertionError("nonretryable failure must not wait")
            ),
        )
        try:
            transport.post_json(
                url="https://provider.invalid/chat/completions",
                headers={},
                payload={"model": "locked"},
                timeout_seconds=30.0,
            )
        except (ConnectionError, ProviderProtocolError):
            pass
        else:
            raise AssertionError("scripted Provider failure must propagate")
        assert delegate.calls == 1


def test_retry_budget_exhaustion_does_not_issue_attempt_two(tmp_path: Path) -> None:
    headers = Message()
    delegate = _ScriptedTransport(
        [urllib.error.HTTPError("https://provider.invalid", 503, "down", headers, None)]
    )
    transport = Dev3RetryingTransport(
        delegate,
        run_root=tmp_path,
        budget=_attempt_budget(retries=0),
        policy_lock_sha256="a" * 64,
        expected_timeout_seconds=30.0,
        sleeper=lambda _: (_ for _ in ()).throw(
            AssertionError("budget exhaustion must not wait")
        ),
    )

    try:
        transport.post_json(
            url="https://provider.invalid/chat/completions",
            headers={},
            payload={"model": "locked"},
            timeout_seconds=30.0,
        )
    except ConnectionError as error:
        assert getattr(error, "code", None) == "RETRY_BUDGET_EXHAUSTED"
    else:
        raise AssertionError("retry suppression must terminalize the operation")
    assert delegate.calls == 1
    decisions = tuple((tmp_path / "retry-decisions").glob("*.json"))
    assert len(decisions) == 1
    assert json.loads(decisions[0].read_text(encoding="utf-8"))["disposition"] == (
        "RETRY_BUDGET_EXHAUSTED"
    )


def test_interruption_during_retry_wait_never_records_retry_issued(
    tmp_path: Path,
) -> None:
    headers = Message()
    delegate = _ScriptedTransport(
        [urllib.error.HTTPError("https://provider.invalid", 503, "down", headers, None)]
    )

    def interrupt(_: float) -> None:
        raise KeyboardInterrupt

    transport = Dev3RetryingTransport(
        delegate,
        run_root=tmp_path,
        budget=_attempt_budget(),
        policy_lock_sha256="a" * 64,
        expected_timeout_seconds=30.0,
        sleeper=interrupt,
    )
    with pytest.raises(KeyboardInterrupt):
        transport.post_json(
            url="https://provider.invalid/chat/completions",
            headers={},
            payload={"model": "locked"},
            timeout_seconds=30.0,
        )

    assert not (tmp_path / "retry-decisions").exists()
    assert len(tuple((tmp_path / "provider-attempt-starts").glob("*.json"))) == 1


def test_malformed_present_usage_is_a_nonretryable_protocol_failure(
    tmp_path: Path,
) -> None:
    delegate = _ScriptedTransport(
        [{"usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 8}}]
    )
    transport = Dev3RetryingTransport(
        delegate,
        run_root=tmp_path,
        budget=_attempt_budget(),
        policy_lock_sha256="a" * 64,
        expected_timeout_seconds=30.0,
        sleeper=lambda _: None,
    )
    with pytest.raises(ProviderProtocolError, match="usage object"):
        transport.post_json(
            url="https://provider.invalid/chat/completions",
            headers={},
            payload={"model": "locked"},
            timeout_seconds=30.0,
        )

    attempt = json.loads(
        next((tmp_path / "provider-attempts").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert delegate.calls == 1
    assert attempt["valid_response_received"] is False
    assert attempt["usage_disposition"] == "UNKNOWN_NO_VALID_RESPONSE"
    assert attempt["failure_class"] == "NON_RETRYABLE_PROTOCOL"
    assert attempt["failure_code"] == "PROVIDER_MALFORMED_USAGE"
    assert not (tmp_path / "retry-decisions").exists()


def test_semantic_overlay_binds_two_transport_attempts_as_one_recovered_operation(
    tmp_path: Path,
) -> None:
    headers = Message()
    delegate = _ScriptedTransport(
        [
            urllib.error.HTTPError(
                "https://provider.invalid", 503, "down", headers, None
            ),
            _valid_response(),
        ]
    )
    transport = Dev3RetryingTransport(
        delegate,
        run_root=tmp_path,
        budget=_attempt_budget(),
        policy_lock_sha256="a" * 64,
        expected_timeout_seconds=30.0,
        sleeper=lambda _: None,
    )

    class _Inner:
        calls = 0
        last_usage_tokens = 7

        def specialize(self, *args: object, **kwargs: object) -> str:
            del args, kwargs
            self.calls += 1
            transport.post_json(
                url="https://provider.invalid/chat/completions",
                headers={},
                payload={"model": "locked"},
                timeout_seconds=30.0,
            )
            return "typed-result"

    proxy = Dev3ProviderProxy(
        _Inner(), run_root=tmp_path, policy_lock_sha256="a" * 64
    )

    assert proxy.specialize(object(), object(), "metrics") == "typed-result"
    assert proxy.calls == 1
    record_path = next((tmp_path / "semantic-operations").glob("*.json"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["status"] == "COMPLETED"
    assert record["transport_recovered"] is True
    assert len(record["provider_attempt_indexes"]) == 2
    assert record["first_attempt_failure_code"] == "HTTP_5XX"
    assert record["retry_disposition"] == "RETRY_ISSUED"


def test_semantic_overlay_records_post_response_protocol_failure_without_retry(
    tmp_path: Path,
) -> None:
    delegate = _ScriptedTransport([_valid_response()])
    transport = Dev3RetryingTransport(
        delegate,
        run_root=tmp_path,
        budget=_attempt_budget(),
        policy_lock_sha256="a" * 64,
        expected_timeout_seconds=30.0,
        sleeper=lambda _: None,
    )

    class _Inner:
        def judge(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            transport.post_json(
                url="https://provider.invalid/chat/completions",
                headers={},
                payload={"model": "locked"},
                timeout_seconds=30.0,
            )
            raise ProviderProtocolError("post-response protocol failure")

    proxy = Dev3ProviderProxy(
        _Inner(), run_root=tmp_path, policy_lock_sha256="a" * 64
    )
    try:
        proxy.judge(object(), "single_v2")
    except ProviderProtocolError:
        pass
    else:
        raise AssertionError("semantic Provider protocol failure must propagate")

    record = json.loads(
        next((tmp_path / "semantic-operations").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert record["status"] == "FAILED"
    assert record["failure_class"] == "NON_RETRYABLE_PROTOCOL"
    assert record["failure_code"] == "PROVIDER_PROTOCOL_VIOLATION"
    assert record["provider_attempt_indexes"] == [1]
    assert delegate.calls == 1
