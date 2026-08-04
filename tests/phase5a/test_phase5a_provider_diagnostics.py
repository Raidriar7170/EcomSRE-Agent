from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
import urllib.error

from ecomsre.model.gateway import ProviderProtocolError
from ecomsre.phase2.token_policy import MODEL_SNAPSHOT
from ecomsre.phase5a import cli, provider
from ecomsre.phase5a.provider import (
    build_provider_request_shape_summary,
    run_provider_order_isolation,
    run_provider_pilot,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROVIDER_ENVIRONMENT = {
    "ECOMSRE_LLM_BASE_URL": "https://provider.invalid/v1",
    "ECOMSRE_LLM_API_KEY": "provider-test-secret",
    "ECOMSRE_LLM_MODEL": MODEL_SNAPSHOT,
}


def _typed_response(payload: dict[str, object], call_index: int) -> dict[str, object]:
    messages = payload["messages"]
    assert isinstance(messages, list)
    user_message = messages[1]
    assert isinstance(user_message, dict)
    envelope = json.loads(user_message["content"])
    diagnosis = {
        "schema_version": "phase5a.diagnosis-result.v2",
        "run_id": envelope["run_id"],
        "decision": "NEED_MORE_EVIDENCE",
        "root_service": None,
        "fault_mechanism": None,
        "causal_chain": [],
        "affected_sli": envelope["incident"]["affected_sli"],
        "supporting_evidence": [],
        "contradicting_evidence": [],
        "missing_evidence": ["One additional read-only source is required."],
        "confidence": 0.2,
        "decision_rationale": "The available evidence remains insufficient.",
        "recommended_next_action": (
            "Collect additional read-only telemetry evidence."
        ),
    }
    return {
        "id": f"completion-{call_index}",
        "model": MODEL_SNAPSHOT,
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"tool-{call_index}",
                            "type": "function",
                            "function": {
                                "name": "submit_phase5a_diagnosis",
                                "arguments": json.dumps(diagnosis),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        },
    }


def test_http_diagnostics_are_exact_allowlisted_and_body_free() -> None:
    statuses = [400, 413, 422, 429, 500, 502, 503, 504, 418]

    class HttpFailureTransport:
        def __init__(self) -> None:
            self.calls = 0

        def post_json(self, **_kwargs):
            status = statuses[self.calls]
            self.calls += 1
            cause = urllib.error.HTTPError(
                "https://raw-private-url.invalid",
                status,
                "raw-http-exception-marker",
                {"X-Raw-Secret": "raw-header-marker"},
                io.BytesIO(b"raw-response-body-marker"),
            )
            try:
                raise cause
            except Exception as error:
                raise ConnectionError("raw-outer-marker") from error

    report: Any = run_provider_pilot(
        PROJECT_ROOT,
        environment=PROVIDER_ENVIRONMENT,
        transport=HttpFailureTransport(),
    )

    assert [item["failure_code"] for item in report["case_results"]] == [
        "PROVIDER_HTTP_400",
        "PROVIDER_HTTP_413",
        "PROVIDER_HTTP_422",
        "PROVIDER_HTTP_429",
        "PROVIDER_HTTP_5XX",
        "PROVIDER_HTTP_5XX",
        "PROVIDER_HTTP_5XX",
        "PROVIDER_HTTP_5XX",
        "PROVIDER_HTTP_OTHER",
    ]
    assert [item["http_status"] for item in report["case_results"]] == [
        400,
        413,
        422,
        429,
        500,
        502,
        503,
        504,
        None,
    ]
    assert all(
        item["failure_stage"] == "HTTP_TRANSPORT"
        and item["call_index"] == index
        and type(item["request_bytes"]) is int
        and item["request_bytes"] > 0
        and type(item["elapsed_ms"]) is int
        and item["elapsed_ms"] >= 0
        for index, item in enumerate(report["case_results"], start=1)
    )
    serialized = json.dumps(report)
    for forbidden in (
        "raw-private-url",
        "raw-http-exception-marker",
        "raw-header-marker",
        "raw-response-body-marker",
        "raw-outer-marker",
        "provider-test-secret",
    ):
        assert forbidden not in serialized


def test_request_shape_summary_is_no_network_and_body_free() -> None:
    report: Any = build_provider_request_shape_summary(PROJECT_ROOT)

    assert report["status"] == "COMPLETED"
    assert report["entry_count"] == 9
    assert len(report["request_shapes"]) == 9
    required = {
        "requirement",
        "variant",
        "request_bytes",
        "conservative_reserved_tokens",
        "finding_count",
        "source_observation_count",
        "evidence_count",
        "offline_model_calls",
        "offline_tool_calls",
        "outer_budget_admitted",
    }
    assert all(set(item) == required for item in report["request_shapes"])
    assert all(
        type(item["request_bytes"]) is int
        and item["request_bytes"] > 0
        and item["conservative_reserved_tokens"]
        == item["request_bytes"] + 2048
        and item["outer_budget_admitted"] is True
        for item in report["request_shapes"]
    )
    forbidden_keys = {
        "api_key",
        "endpoint",
        "evidence",
        "headers",
        "incident",
        "input",
        "messages",
        "payload",
        "prompt",
        "raw_response",
        "secret",
        "url",
    }
    assert not forbidden_keys.intersection(_recursive_keys(report))


def test_unknown_provider_protocol_failure_stays_in_response_protocol_stage() -> None:
    class ProtocolFailureTransport:
        def post_json(self, **_kwargs):
            raise ProviderProtocolError("raw-provider-protocol-marker")

    report: Any = run_provider_pilot(
        PROJECT_ROOT,
        environment=PROVIDER_ENVIRONMENT,
        transport=ProtocolFailureTransport(),
    )

    assert all(
        item["failure_code"] == "PROVIDER_PROTOCOL_OTHER"
        and item["failure_stage"] == "RESPONSE_PROTOCOL"
        for item in report["case_results"]
    )
    assert "raw-provider-protocol-marker" not in json.dumps(report)


def test_all_variants_receive_one_complete_decision_semantics_contract() -> None:
    class CapturingTransport:
        def __init__(self) -> None:
            self.payloads: list[dict[str, object]] = []

        def post_json(self, **kwargs):
            payload = kwargs["payload"]
            self.payloads.append(payload)
            return _typed_response(payload, len(self.payloads))

    transport = CapturingTransport()
    report: Any = run_provider_pilot(
        PROJECT_ROOT,
        environment=PROVIDER_ENVIRONMENT,
        transport=transport,
    )

    assert report["status"] == "PASSED"
    prompts = {
        payload["messages"][0]["content"]  # type: ignore[index]
        for payload in transport.payloads
    }
    assert len(prompts) == 1
    prompt = prompts.pop()
    assert isinstance(prompt, str)
    for required in (
        "RCA_CONFIRMED requires non-null root_service and fault_mechanism",
        "NEED_MORE_EVIDENCE requires null root_service and fault_mechanism",
        "ABSTAIN requires null root_service and fault_mechanism",
        "supporting_evidence and missing_evidence empty",
    ):
        assert required in prompt


def test_order_isolation_uses_six_calls_no_retry_and_uniform_pacing() -> None:
    class TypedTransport:
        def __init__(self) -> None:
            self.payloads: list[dict[str, object]] = []

        def post_json(self, **kwargs):
            payload = kwargs["payload"]
            self.payloads.append(payload)
            return _typed_response(payload, len(self.payloads))

    waits: list[float] = []
    transport = TypedTransport()
    report: Any = run_provider_order_isolation(
        PROJECT_ROOT,
        environment=PROVIDER_ENVIRONMENT,
        transport=transport,
        sleeper=waits.append,
    )

    assert report["status"] == "COMPLETED"
    assert report["provider_call_count"] == 6
    assert report["hidden_retry"] is False
    assert report["scripted_fallback"] is False
    assert waits == [2.0] * 5
    assert [item["variant"] for item in report["case_results"]] == [
        "SINGLE_AGENT_V2",
        "FIXED_SPECIALIST_V2",
        "DYNAMIC_MULTI_AGENT_V2",
        "DYNAMIC_MULTI_AGENT_V2",
        "FIXED_SPECIALIST_V2",
        "SINGLE_AGENT_V2",
    ]
    assert [item["sequence"] for item in report["case_results"]] == [
        "A",
        "A",
        "A",
        "B",
        "B",
        "B",
    ]
    assert [item["call_index"] for item in report["case_results"]] == list(
        range(1, 7)
    )
    assert all(item["failure_stage"] == "COMPLETED" for item in report["case_results"])


def test_order_isolation_fails_closed_before_transport_without_waiting(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class ForbiddenTransport:
        def post_json(self, **_kwargs):
            raise AssertionError("budget rejection touched provider transport")

    waits: list[float] = []
    monkeypatch.setattr(provider, "_OUTER_MODEL_CALL_LIMIT", 0)
    report: Any = run_provider_order_isolation(
        PROJECT_ROOT,
        environment=PROVIDER_ENVIRONMENT,
        transport=ForbiddenTransport(),
        sleeper=waits.append,
    )

    assert report["status"] == "FAILED"
    assert report["provider_call_count"] == 0
    assert report["run_count"] == 1
    assert waits == []
    assert report["case_results"][0]["failure_stage"] == "BUDGET_ADMISSION"

    output = tmp_path / "order-isolation.json"
    monkeypatch.setattr(cli, "run_provider_order_isolation", lambda _root: report)
    assert cli._provider_order_isolation(output) == 1


def _recursive_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return {
            *(str(key) for key in value),
            *(
                nested
                for item in value.values()
                for nested in _recursive_keys(item)
            ),
        }
    if isinstance(value, list):
        return {nested for item in value for nested in _recursive_keys(item)}
    return set()
