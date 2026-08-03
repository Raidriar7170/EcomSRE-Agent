"""Strict real-provider boundary tests for Phase 2 typed completions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from pydantic import JsonValue

import ecomsre.phase2.provider as provider_module
from ecomsre.model.gateway import OpenAICompatibleConfig, ProviderProtocolError
from ecomsre.phase2.comparison_adapter import ModelInvocation, ProviderParameters
from ecomsre.phase2.contracts import (
    CommanderRequest,
    ModelAllowedActions,
    ModelInputEnvelope,
    ModelOperation,
    Phase2Variant,
)
from ecomsre.phase2.provider import (
    PHASE2_PROVIDER_IDENTITY,
    OpenAICompatiblePhase2Backend,
)
from ecomsre.phase2.token_policy import (
    MODEL_SNAPSHOT,
    build_model_input_envelope,
    load_token_authority,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class RecordingTransport:
    def __init__(self, response: Mapping[str, object]) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "payload": dict(payload),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.response


def _commander_call() -> tuple[
    ModelInvocation,
    ModelInputEnvelope,
    int,
    dict[str, JsonValue],
]:
    authority = load_token_authority(PROJECT_ROOT)
    operation = ModelOperation.COMMANDER_MODEL
    actions = ModelAllowedActions.PLAN_ONLY
    request = CommanderRequest.model_validate(
        authority.minimal_requests[(operation, actions)]
    )
    envelope = build_model_input_envelope(
        authority.core,
        operation,
        actions,
        request,
    )
    invocation = ModelInvocation(
        schema_version="phase2.model-invocation.v1",
        invocation_id="provider-invocation-001",
        run_id=request.run_id,
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
        case_id=request.budget_snapshot.case_id,
        operation=operation,
        allowed_actions=actions,
        request=request,
        provider_parameters=ProviderParameters(
            model_snapshot=MODEL_SNAPSHOT,
            provider_identity=PHASE2_PROVIDER_IDENTITY,
            temperature=0.0,
            n=1,
            parallel_tool_calls=False,
        ),
        token_policy_core_sha256=authority.core_sha256,
        response_schema_sha256=authority.golden(
            operation,
            actions,
        ).response_schema_sha256,
        expected_snapshot_sequence=request.budget_snapshot.sequence,
        source_record_id="commander-slot-001",
    )
    exact_input_tokens = authority.exact_input_tokens(envelope)
    response = authority.minimal_responses[(operation, actions)]
    return invocation, envelope, exact_input_tokens, response


def _provider_response(content: str) -> dict[str, object]:
    return {
        "id": "chatcmpl-phase2-001",
        "model": MODEL_SNAPSHOT,
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "refusal": None,
                    "tool_calls": [
                        {
                            "id": "call-phase2-001",
                            "type": "function",
                            "function": {
                                "name": "submit_phase2_response",
                                "arguments": content,
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 2_000,
            "completion_tokens": 205,
            "total_tokens": 2_205,
        },
    }


def _backend(transport: RecordingTransport) -> OpenAICompatiblePhase2Backend:
    return OpenAICompatiblePhase2Backend(
        config=OpenAICompatibleConfig(
            base_url="https://llm.example.test/v1",
            api_key="provider-secret-value",
            model=MODEL_SNAPSHOT,
        ),
        transport=transport,
        timeout_seconds=17.0,
    )


def test_backend_sends_frozen_structured_request_and_normalizes_usage() -> None:
    invocation, envelope, exact_input_tokens, response = _commander_call()

    transport = RecordingTransport(
        _provider_response(json.dumps(response, separators=(",", ":")))
    )
    backend = _backend(transport)

    completion = backend.complete(
        invocation,
        envelope=envelope,
        exact_input_tokens=exact_input_tokens,
        max_completion_tokens=512,
    )

    assert completion.provider_identity == PHASE2_PROVIDER_IDENTITY
    assert completion.response == response
    assert completion.input_tokens == exact_input_tokens
    assert completion.output_tokens == 205
    assert completion.total_tokens == exact_input_tokens + 205
    assert backend.calls == 1
    assert backend.provider_prompt_tokens == (2_000,)

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == "https://llm.example.test/v1/chat/completions"
    assert call["timeout_seconds"] == 17.0
    payload = call["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == MODEL_SNAPSHOT
    assert payload["temperature"] == 0.0
    assert payload["n"] == 1
    assert payload["parallel_tool_calls"] is False
    assert payload["max_completion_tokens"] == 512
    assert payload["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_phase2_response"},
    }
    assert payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "submit_phase2_response",
                "description": "Return the exact typed Phase 2 response.",
                "strict": False,
                "parameters": envelope.response_schema["schema"],
            },
        }
    ]
    assert "response_format" not in payload
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert messages[0] == {
        "role": "system",
        "content": envelope.system_instruction,
    }
    assert "provider-secret-value" not in repr(backend)
    assert "provider-secret-value" not in repr(payload)


def test_backend_rejects_model_drift_before_transport() -> None:
    invocation, envelope, exact_input_tokens, response = _commander_call()

    transport = RecordingTransport(_provider_response(json.dumps(response)))
    backend = OpenAICompatiblePhase2Backend(
        config=OpenAICompatibleConfig(
            base_url="https://llm.example.test/v1",
            api_key="provider-secret-value",
            model="different-model",
        ),
        transport=transport,
        timeout_seconds=17.0,
    )

    with pytest.raises(ProviderProtocolError):
        backend.complete(
            invocation,
            envelope=envelope,
            exact_input_tokens=exact_input_tokens,
            max_completion_tokens=512,
        )

    assert transport.calls == []


@pytest.mark.parametrize(
    "content",
    (
        '{"schema_version":"one","schema_version":"two"}',
        "[]",
    ),
)
def test_backend_rejects_non_strict_object_content(content: str) -> None:
    invocation, envelope, exact_input_tokens, _response = _commander_call()
    backend = _backend(RecordingTransport(_provider_response(content)))

    with pytest.raises(ProviderProtocolError):
        backend.complete(
            invocation,
            envelope=envelope,
            exact_input_tokens=exact_input_tokens,
            max_completion_tokens=512,
        )


def test_default_transport_is_fresh_per_provider_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation, envelope, exact_input_tokens, response = _commander_call()

    transports: list[RecordingTransport] = []

    def transport_factory() -> RecordingTransport:
        transport = RecordingTransport(
            _provider_response(json.dumps(response, separators=(",", ":")))
        )
        transports.append(transport)
        return transport

    monkeypatch.setattr(
        provider_module,
        "StdlibOpenAICompatibleTransport",
        transport_factory,
    )
    backend = OpenAICompatiblePhase2Backend(
        config=OpenAICompatibleConfig(
            base_url="https://llm.example.test/v1",
            api_key="provider-secret-value",
            model=MODEL_SNAPSHOT,
        ),
        timeout_seconds=17.0,
    )

    for _ in range(2):
        backend.complete(
            invocation,
            envelope=envelope,
            exact_input_tokens=exact_input_tokens,
            max_completion_tokens=512,
        )

    assert len(transports) == 2
    assert all(len(transport.calls) == 1 for transport in transports)
