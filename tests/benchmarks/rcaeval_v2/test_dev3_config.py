from __future__ import annotations

import hashlib
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEV1 = PROJECT_ROOT / "config/rcaeval-re2-v2-dev1"
DEV3 = PROJECT_ROOT / "config/rcaeval-re2-v2-dev3"


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_dev3_protocol_hash_is_bound_by_every_dependent_config() -> None:
    expected = _sha(DEV3 / "protocol.json")
    assert expected == "3cbc02eec0bda6734bf777999200c3f5e6cdc6522fe8fb0baa22b126b402d8c4"
    for name in (
        "budget-lock.json",
        "dataset-lock.json",
        "evaluation-policy.json",
        "indicator-lock.json",
        "schedule-generation.json",
        "split-lock.json",
    ):
        assert _load(DEV3 / name)["protocol_sha256"] == expected


def test_dev3_model_prompt_semantics_equal_dev1_except_retry_identity() -> None:
    inherited = _load(DEV1 / "model-prompt-lock.json")
    observed = _load(DEV3 / "model-prompt-lock.json")
    assert observed.pop("schema_version") == (
        "rcaeval-re2-v2-dev3.model-prompt-lock.v1"
    )
    assert observed.pop("protocol_id") == "rcaeval-re2-v2-dev.3"
    retry = observed.pop("retry")
    inherited.pop("schema_version")
    inherited.pop("protocol_id")
    inherited_retry = inherited.pop("retry")
    assert observed == inherited
    assert retry == {
        "fallback": "NO_FALLBACK",
        "semantic": "FORBIDDEN",
        "transport": "ONE_ALLOWLISTED_SAME_REQUEST_RETRY",
    }
    assert inherited_retry == {
        "fallback": "NO_FALLBACK",
        "semantic": "FORBIDDEN",
        "transport": "FORBIDDEN",
    }


def test_dev3_retry_and_budget_locks_are_exact_and_finite() -> None:
    retry = _load(DEV3 / "transport-retry-policy.json")
    budget = _load(DEV3 / "budget-lock.json")
    assert retry["allowlist"] == [
        "CONNECTION_RESET_OR_DISCONNECT",
        "TLS_TRANSIENT",
        "TIMEOUT_PRE_RESPONSE",
        "HTTP_429",
        "HTTP_5XX",
    ]
    assert retry["max_transport_retries_per_semantic_operation"] == 1
    assert retry["jitter"] == "FORBIDDEN"
    assert retry["exponential_backoff"] == "FORBIDDEN"
    assert budget["provider_attempt_caps"] == {"design": 2400, "smoke": 480}
    assert budget["transport_retry_caps"] == {
        "design": 60,
        "per_semantic_operation": 1,
        "smoke": 12,
    }
    assert budget["attempt_token_reservation"] == {
        "max_completion_tokens": 2048,
        "prompt_tokens": 29952,
        "total_tokens": 32000,
    }
