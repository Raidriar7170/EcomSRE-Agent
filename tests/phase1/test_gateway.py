from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import ecomsre.model.gateway as gateway_module
from ecomsre.model.gateway import (
    OpenAICompatibleConfig,
    OpenAICompatibleGateway,
    ProviderProtocolError,
)
from ecomsre.phase1.contracts import (
    FinalAction,
    Incident,
    MetricsAction,
    ModelFunctionName,
    ModelRequest,
    RemainingBudgets,
    Severity,
)
from ecomsre.phase1.validator import EvidenceValidationError

START = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
END = START + timedelta(minutes=5)
API_KEY = "phase1-super-secret-key"


def model_request(**overrides: object) -> ModelRequest:
    payload: dict[str, object] = {
        "schema_version": "phase1.model-request.v1",
        "request_id": "request-001",
        "run_id": "a" * 32,
        "agent_id": "single-agent",
        "incident_id": "incident-001",
        "task_id": "root-cause-analysis",
        "model_name": "fixture-model",
        "incident": Incident(
            schema_version="phase1.incident.v1",
            incident_id="incident-001",
            alert_source_service="frontend",
            summary="Request success rate is below the objective.",
            started_at=START,
            ended_at=END,
            affected_sli="request success rate",
            severity=Severity.SEV2,
        ),
        "transcript": (),
        "evidence": (),
        "remaining_budgets": RemainingBudgets(
            model_calls=7,
            tool_calls=8,
            total_tokens=12_000,
        ),
        "allowed_actions": tuple(ModelFunctionName),
        "temperature": 0.0,
        "timeout_seconds": 1.25,
    }
    payload.update(overrides)
    return ModelRequest.model_validate(payload)


def provider_response(
    *,
    function_name: str = "query_metrics",
    arguments: dict[str, object] | str | None = None,
) -> dict[str, object]:
    if arguments is None:
        arguments = {
            "started_at": START.isoformat(),
            "ended_at": END.isoformat(),
            "service": None,
        }
    encoded_arguments = (
        arguments if isinstance(arguments, str) else json.dumps(arguments)
    )
    return {
        "id": "chatcmpl-fixture",
        "model": "fixture-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-fixture",
                            "type": "function",
                            "function": {
                                "name": function_name,
                                "arguments": encoded_arguments,
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 8,
            "total_tokens": 28,
        },
    }


class FakeTransport:
    def __init__(
        self,
        response: dict[str, object] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.response = response or provider_response()
        self.error = error
        self.calls: list[dict[str, object]] = []

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.error is not None:
            raise self.error
        return self.response


class FakeHTTPResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.content


class FakeOpener:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls: list[tuple[object, float]] = []

    def open(self, request: object, *, timeout: float) -> FakeHTTPResponse:
        self.calls.append((request, timeout))
        return FakeHTTPResponse(self.content)


class ScriptedOpener:
    def __init__(self, outcomes: list[BaseException | bytes]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def open(self, request: object, *, timeout: float) -> FakeHTTPResponse:
        del request, timeout
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeHTTPResponse(outcome)


def gateway(
    transport: FakeTransport,
    *,
    base_url: str = "https://llm.example.test/v1",
) -> OpenAICompatibleGateway:
    return OpenAICompatibleGateway(
        config=OpenAICompatibleConfig(
            base_url=base_url,
            api_key=API_KEY,
            model="fixture-model",
        ),
        transport=transport,
    )


def test_environment_configuration_is_all_or_none_and_https() -> None:
    assert OpenAICompatibleConfig.from_environment({}) is None
    configured = OpenAICompatibleConfig.from_environment(
        {
            "ECOMSRE_LLM_BASE_URL": "https://llm.example.test/v1",
            "ECOMSRE_LLM_API_KEY": API_KEY,
            "ECOMSRE_LLM_MODEL": "fixture-model",
        }
    )

    assert configured is not None
    assert configured.model == "fixture-model"
    with pytest.raises(ValueError, match="partial"):
        OpenAICompatibleConfig.from_environment(
            {"ECOMSRE_LLM_BASE_URL": "https://llm.example.test/v1"}
        )
    for present_value in (None, "", 123):
        with pytest.raises(ValueError, match="configuration"):
            OpenAICompatibleConfig.from_environment(
                {
                    "ECOMSRE_LLM_BASE_URL": present_value,
                    "ECOMSRE_LLM_API_KEY": API_KEY,
                    "ECOMSRE_LLM_MODEL": "fixture-model",
                }
            )
    with pytest.raises(ValueError, match="configuration"):
        OpenAICompatibleConfig.from_environment(
            {
                "ECOMSRE_LLM_BASE_URL": None,
                "ECOMSRE_LLM_API_KEY": None,
                "ECOMSRE_LLM_MODEL": None,
            }
        )
    with pytest.raises(ValueError, match="configuration"):
        OpenAICompatibleConfig.from_environment(
            {
                "ECOMSRE_LLM_BASE_URL": "",
                "ECOMSRE_LLM_API_KEY": "",
                "ECOMSRE_LLM_MODEL": "",
            }
        )
    with pytest.raises(ValueError, match="HTTPS"):
        OpenAICompatibleConfig(
            base_url="http://llm.example.test/v1",
            api_key=API_KEY,
            model="fixture-model",
        )


@pytest.mark.parametrize(
    "redirect_target",
    (
        "http://llm.example.test/v1/chat/completions",
        "https://attacker.example.test/steal",
        "https://llm.example.test/other",
    ),
)
def test_redirect_handler_rejects_every_redirect_without_forwarding_bearer(
    redirect_target: str,
) -> None:
    original = urllib.request.Request(
        "https://llm.example.test/v1/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    handler_type = getattr(gateway_module, "RejectRedirectHandler")
    handler = handler_type()

    with pytest.raises(ProviderProtocolError, match="redirect"):
        handler.redirect_request(
            original,
            None,
            302,
            "Found",
            {},
            redirect_target,
        )

    assert original.get_header("Authorization") == f"Bearer {API_KEY}"


@pytest.mark.parametrize(
    "content",
    (
        (
            '{"nested":'
            * 70
            + "null"
            + "}"
            * 70
        ).encode(),
        (b"[" * 2000) + b"0" + (b"]" * 2000),
    ),
)
def test_stdlib_transport_rejects_deep_or_recursive_json_deterministically(
    content: bytes,
) -> None:
    opener = FakeOpener(content)
    transport_type = gateway_module.StdlibOpenAICompatibleTransport
    transport = transport_type(opener=opener)

    with pytest.raises(ProviderProtocolError, match="JSON|depth"):
        transport.post_json(
            url="https://llm.example.test/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            payload={"bounded": True},
            timeout_seconds=0.5,
        )

    assert len(opener.calls) == 1


def test_stdlib_transport_retries_one_tls_eof_without_new_semantic_call() -> None:
    opener = ScriptedOpener(
        [
            urllib.error.URLError(ssl.SSLEOFError(8, "handshake eof")),
            b'{"ok":true}',
        ]
    )
    waits: list[float] = []
    transport = gateway_module.StdlibOpenAICompatibleTransport(
        opener=opener,
        maximum_tls_transient_retries=1,
        sleeper=waits.append,
    )

    result = transport.post_json(
        url="https://llm.example.test/v1/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        payload={"bounded": True},
        timeout_seconds=0.5,
    )

    assert result == {"ok": True}
    assert opener.calls == 2
    assert waits == [2.0]
    assert transport.last_retry_count == 1


def test_stdlib_transport_never_retries_certificate_failure() -> None:
    opener = ScriptedOpener(
        [
            urllib.error.URLError(
                ssl.SSLCertVerificationError(1, "certificate verify failed")
            )
        ]
    )
    waits: list[float] = []
    transport = gateway_module.StdlibOpenAICompatibleTransport(
        opener=opener,
        maximum_tls_transient_retries=1,
        sleeper=waits.append,
    )

    with pytest.raises(ConnectionError, match="request failed"):
        transport.post_json(
            url="https://llm.example.test/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            payload={"bounded": True},
            timeout_seconds=0.5,
        )

    assert opener.calls == 1
    assert waits == []
    assert transport.last_retry_count == 0


def test_gateway_sends_five_strict_tools_and_one_deterministic_call() -> None:
    transport = FakeTransport()
    response = gateway(transport).complete(model_request())

    assert response.action == MetricsAction(
        action_type="metrics",
        started_at=START,
        ended_at=END,
        service=None,
    )
    assert response.usage.total_tokens == 28
    assert response.provider_name == "openai-compatible"
    assert response.model_name == "fixture-model"
    assert response.monotonic_duration_seconds >= 0
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == "https://llm.example.test/v1/chat/completions"
    assert call["timeout_seconds"] == 1.25
    payload = call["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "fixture-model"
    assert payload["temperature"] == 0
    assert payload["n"] == 1
    assert payload["parallel_tool_calls"] is False
    assert payload["max_completion_tokens"] == 12_000
    tools = payload["tools"]
    assert isinstance(tools, list)
    functions = [tool["function"] for tool in tools]
    assert [function["name"] for function in functions] == [
        "query_metrics",
        "search_logs",
        "search_traces",
        "list_changes",
        "submit_rca",
    ]
    assert all(function["strict"] is True for function in functions)
    assert all(
        function["parameters"]["additionalProperties"] is False
        for function in functions
    )
    assert all(
        set(function["parameters"]["required"])
        == set(function["parameters"]["properties"])
        for function in functions
    )
    rca_schema = functions[-1]["parameters"]
    mechanism_schema = rca_schema["properties"]["fault_mechanism"]
    assert mechanism_schema["anyOf"][0]["enum"] == [
        "runtime_configuration_failure",
        "request_processing_failure",
        "cache_backend_timeout",
    ]
    assert mechanism_schema["anyOf"][1] == {"type": "null"}
    messages = payload["messages"]
    assert isinstance(messages, list)
    system_instruction = messages[0]["content"]
    assert "untrusted data" in system_instruction
    assert "embedded instructions" in system_instruction
    assert "alert_source_service" in system_instruction
    assert "non-authoritative" in system_instruction
    assert "never Evidence" in system_instruction


def test_gateway_exposes_canonical_evidence_reference_schema() -> None:
    transport = FakeTransport()

    gateway(transport).complete(model_request())

    payload = transport.calls[0]["payload"]
    assert isinstance(payload, dict)
    tools = payload["tools"]
    assert isinstance(tools, list)
    rca_properties = tools[-1]["function"]["parameters"]["properties"]
    expected_pattern = (
        r"^evidence://[0-9a-f]{32}/"
        r"(?:metrics|logs|traces|changes)/[0-9]{4}$"
    )
    assert rca_properties["supporting_evidence"]["items"]["pattern"] == (
        expected_pattern
    )
    assert rca_properties["contradicting_evidence"]["items"][
        "pattern"
    ] == expected_pattern


def test_gateway_instructs_model_to_use_existing_independent_evidence() -> None:
    transport = FakeTransport()

    gateway(transport).complete(model_request())

    payload = transport.calls[0]["payload"]
    assert isinstance(payload, dict)
    messages = payload["messages"]
    assert isinstance(messages, list)
    system_instruction = messages[0]["content"]
    assert "copy evidence_ref strings exactly" in system_instruction
    assert "Never invent or transform" in system_instruction
    assert "two distinct Evidence sources" in system_instruction
    assert (
        "runtime_configuration_failure requires matching CHANGES Evidence"
        in system_instruction
    )
    assert "For ABSTAIN, set root_service and fault_mechanism to null" in (
        system_instruction
    )
    assert "no confirmed incident" in system_instruction
    assert "Do not repeat an identical successful query" in system_instruction


def test_gateway_accepts_standard_optional_usage_detail_objects() -> None:
    envelope = provider_response()
    envelope["usage"].update(
        {
            "prompt_tokens_details": {
                "cached_tokens": 0,
                "audio_tokens": 0,
            },
            "completion_tokens_details": {
                "reasoning_tokens": 0,
                "audio_tokens": 0,
                "accepted_prediction_tokens": 0,
                "rejected_prediction_tokens": 0,
            },
        }
    )

    response = gateway(FakeTransport(envelope)).complete(model_request())

    assert response.usage.input_tokens == 20
    assert response.usage.output_tokens == 8
    assert response.usage.total_tokens == 28


@pytest.mark.parametrize(
    "usage_patch",
    (
        {"prompt_tokens_details": []},
        {"completion_tokens_details": "not-an-object"},
        {"vendor_usage_extension": {}},
    ),
)
def test_gateway_rejects_non_object_usage_details_and_unknown_fields(
    usage_patch: dict[str, object],
) -> None:
    envelope = provider_response()
    envelope["usage"].update(usage_patch)

    with pytest.raises(ProviderProtocolError, match="object|exact"):
        gateway(FakeTransport(envelope)).complete(model_request())


def test_gateway_parses_submit_rca_into_the_internal_final_action() -> None:
    result = {
        "schema_version": "phase1.rca-result.v1",
        "decision": "ABSTAIN",
        "root_service": None,
        "fault_mechanism": None,
        "causal_chain": [],
        "affected_sli": "request success rate",
        "supporting_evidence": [],
        "contradicting_evidence": [],
        "missing_evidence": [],
        "confidence": 0.1,
        "decision_rationale": (
            "The observations do not establish an incident."
        ),
        "recommended_next_action": (
            "Continue monitoring the affected SLI."
        ),
    }
    transport = FakeTransport(
        provider_response(function_name="submit_rca", arguments=result)
    )

    response = gateway(transport).complete(model_request())
    action = response.action

    assert isinstance(action, FinalAction)
    assert action.result.decision.value == "ABSTAIN"


@pytest.mark.parametrize(
    "mutate",
    (
        lambda envelope: envelope.update(choices=[]),
        lambda envelope: envelope["choices"].append(envelope["choices"][0]),
        lambda envelope: envelope["choices"][0]["message"].update(tool_calls=[]),
        lambda envelope: envelope["choices"][0]["message"]["tool_calls"].append(
            envelope["choices"][0]["message"]["tool_calls"][0]
        ),
        lambda envelope: envelope["choices"][0]["message"]["tool_calls"][0][
            "function"
        ].update(name="run_shell"),
        lambda envelope: envelope["choices"][0]["message"]["tool_calls"][0][
            "function"
        ].update(arguments="{"),
        lambda envelope: envelope["usage"].update(total_tokens=29),
        lambda envelope: envelope.update(usage={"prompt_tokens": True}),
        lambda envelope: envelope["choices"][0].update(index=False),
        lambda envelope: envelope["choices"][0]["message"].update(
            content="unexpected"
        ),
        lambda envelope: envelope["choices"][0]["message"]["tool_calls"][0].pop(
            "id"
        ),
        lambda envelope: envelope["choices"][0]["message"].update(
            function_call=None
        ),
    ),
)
def test_gateway_rejects_malformed_provider_envelopes(
    mutate: Any,
) -> None:
    envelope = provider_response()
    mutate(envelope)

    with pytest.raises(ProviderProtocolError):
        gateway(FakeTransport(envelope)).complete(model_request())


@pytest.mark.parametrize(
    "arguments",
    (
        {
            "started_at": START.isoformat(),
            "ended_at": END.isoformat(),
            "service": None,
            "extra": "forbidden",
        },
        '{"started_at":"2026-07-31T08:00:00+00:00",'
        '"started_at":"2026-07-31T08:01:00+00:00",'
        '"ended_at":"2026-07-31T08:05:00+00:00"}',
        {
            "started_at": START.isoformat(),
            "ended_at": END.isoformat(),
        },
    ),
)
def test_gateway_rejects_non_strict_function_arguments(
    arguments: dict[str, object] | str,
) -> None:
    transport = FakeTransport(provider_response(arguments=arguments))

    with pytest.raises(ProviderProtocolError):
        gateway(transport).complete(model_request())


def test_gateway_propagates_timeout_once_without_retry() -> None:
    transport = FakeTransport(error=TimeoutError("provider timed out"))

    with pytest.raises(TimeoutError, match="timed out"):
        gateway(transport).complete(model_request(timeout_seconds=0.75))

    assert len(transport.calls) == 1
    assert transport.calls[0]["timeout_seconds"] == 0.75


def test_gateway_preserves_known_provider_protocol_classification() -> None:
    transport = FakeTransport(
        error=ProviderProtocolError("redirects are forbidden")
    )

    with pytest.raises(ProviderProtocolError, match="redirect"):
        gateway(transport).complete(model_request())

    assert len(transport.calls) == 1


def test_gateway_rejects_cyclic_or_deep_injected_transport_mapping() -> None:
    cyclic = provider_response()
    cyclic["metadata"] = cyclic
    with pytest.raises(ProviderProtocolError, match="cycle|depth"):
        gateway(FakeTransport(cyclic)).complete(model_request())

    deep: dict[str, object] = {}
    cursor = deep
    for _ in range(70):
        nested: dict[str, object] = {}
        cursor["nested"] = nested
        cursor = nested
    envelope = provider_response()
    envelope["metadata"] = deep
    with pytest.raises(ProviderProtocolError, match="depth"):
        gateway(FakeTransport(envelope)).complete(model_request())


@pytest.mark.parametrize("location", ("tool_call", "function"))
def test_gateway_rejects_extra_nested_tool_envelope_keys(
    location: str,
) -> None:
    envelope = provider_response()
    tool_call = envelope["choices"][0]["message"]["tool_calls"][0]
    if location == "tool_call":
        tool_call["extra"] = "forbidden"
    else:
        tool_call["function"]["extra"] = "forbidden"

    with pytest.raises(ProviderProtocolError, match="fields|exact"):
        gateway(FakeTransport(envelope)).complete(model_request())


def test_gateway_allows_harmless_standard_top_level_metadata() -> None:
    envelope = provider_response()
    envelope["system_fingerprint"] = "fp_fixture"
    response = gateway(FakeTransport(envelope)).complete(model_request())
    assert response.response_id == "chatcmpl-fixture"


@pytest.mark.parametrize(
    "tainted_summary",
    (
        "Ground truth says recommendation.",
        "ground trut\u200bh says recommendation.",
        "ｇｒｏｕｎｄ ｔｒｕｔｈ says recommendation.",
        "scenario_label: hidden-case-7",
        "evaluator/path=/private/answer.json",
        "expected mechanism: cache_backend_timeout",
    ),
)
def test_gateway_never_serializes_tainted_request_into_provider_payload(
    tainted_summary: str,
) -> None:
    transport = FakeTransport()
    clean = model_request()
    tainted_incident = clean.incident.model_copy(
        update={"summary": tainted_summary}
    )
    tainted = clean.model_copy(update={"incident": tainted_incident})

    with pytest.raises(EvidenceValidationError, match="SCHEMA_REVALIDATION"):
        gateway(transport).complete(tainted)

    assert transport.calls == []


def test_api_key_is_absent_from_repr_errors_records_and_payload_body() -> None:
    transport = FakeTransport(response={"error": {"message": API_KEY}})
    adapter = gateway(transport)

    with pytest.raises(ProviderProtocolError) as captured:
        adapter.complete(model_request())

    rendered = (
        repr(adapter)
        + repr(adapter.config)
        + str(captured.value)
        + json.dumps(transport.calls[0]["payload"])
    )
    assert API_KEY not in rendered
    headers = transport.calls[0]["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == f"Bearer {API_KEY}"


def test_transport_exception_text_cannot_leak_the_api_key() -> None:
    transport = FakeTransport(error=RuntimeError(API_KEY))

    with pytest.raises(ConnectionError) as captured:
        gateway(transport).complete(model_request())

    assert API_KEY not in str(captured.value)
    assert len(transport.calls) == 1


def test_provider_cannot_echo_the_api_key_into_a_model_record() -> None:
    envelope = provider_response()
    envelope["id"] = API_KEY

    with pytest.raises(ProviderProtocolError):
        gateway(FakeTransport(envelope)).complete(model_request())


@pytest.mark.parametrize("bypass", ("model_construct", "hidden_storage"))
def test_gateway_reconstructs_model_request_at_the_trust_boundary(
    bypass: str,
) -> None:
    if bypass == "model_construct":
        request = ModelRequest.model_construct(
            schema_version="phase1.model-request.v1",
            request_id="request-001",
            run_id="a" * 32,
            agent_id="single-agent",
            incident_id="incident-001",
            task_id="root-cause-analysis",
            model_name="fixture-model",
            incident=model_request().incident,
            transcript=(),
            evidence=(),
            remaining_budgets=RemainingBudgets(
                model_calls=7,
                tool_calls=8,
                total_tokens=12_000,
            ),
            allowed_actions=(),
            temperature=0.0,
            timeout_seconds=1.0,
        )
    else:
        request = model_request()
        object.__setattr__(request, "__dict__", request.__dict__ | {"secret": API_KEY})

    with pytest.raises(EvidenceValidationError):
        gateway(FakeTransport()).complete(request)
