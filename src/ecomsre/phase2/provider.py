"""No-retry OpenAI-compatible backend for typed Phase 2 model calls."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue, ValidationError

from ecomsre.model.gateway import (
    OpenAICompatibleConfig,
    OpenAICompatibleTransport,
    ProviderProtocolError,
    StdlibOpenAICompatibleTransport,
    _contains_credential,
    _parse_usage,
    _reject_json_constant,
    _require_bounded_json,
    _strict_object,
)
from ecomsre.phase2.comparison_adapter import ModelCompletion, ModelInvocation
from ecomsre.phase2.contracts import ModelInputEnvelope
from ecomsre.phase2.token_policy import canonical_json_bytes


PHASE2_PROVIDER_IDENTITY = "openai-compatible"


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProviderProtocolError(f"{label} must be an object")
    return value


def _require_one(value: object, label: str) -> object:
    if not isinstance(value, list) or len(value) != 1:
        raise ProviderProtocolError(f"{label} must contain exactly one item")
    return value[0]


def _parse_content(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, str) or not value.strip():
        raise ProviderProtocolError("assistant content must be nonempty JSON text")
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ProviderProtocolError(
            "assistant content is not strict JSON"
        ) from error
    _require_bounded_json(parsed)
    if not isinstance(parsed, dict):
        raise ProviderProtocolError("assistant content must be a JSON object")
    return cast(dict[str, JsonValue], parsed)


class OpenAICompatiblePhase2Backend:
    """Issue one structured Chat Completions request per admitted model lease."""

    def __init__(
        self,
        *,
        config: OpenAICompatibleConfig,
        timeout_seconds: float,
        transport: OpenAICompatibleTransport | None = None,
    ) -> None:
        if not isinstance(config, OpenAICompatibleConfig):
            raise TypeError("config must be OpenAICompatibleConfig")
        if type(timeout_seconds) is not float or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive float")
        if transport is not None and not callable(
            getattr(transport, "post_json", None)
        ):
            raise TypeError("transport must implement post_json")
        self._config = config
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._calls = 0
        self._provider_prompt_tokens: tuple[int, ...] = ()

    @property
    def provider_identity(self) -> str:
        return PHASE2_PROVIDER_IDENTITY

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def provider_prompt_tokens(self) -> tuple[int, ...]:
        return self._provider_prompt_tokens

    def __repr__(self) -> str:
        return (
            "OpenAICompatiblePhase2Backend("
            f"base_url={self._config.base_url!r}, "
            f"model={self._config.model!r}, "
            f"timeout_seconds={self._timeout_seconds!r})"
        )

    def complete(
        self,
        invocation: ModelInvocation,
        *,
        envelope: ModelInputEnvelope,
        exact_input_tokens: int,
        max_completion_tokens: int,
    ) -> ModelCompletion:
        """Return strict typed content with canonical local input accounting."""

        try:
            invocation = ModelInvocation.model_validate(invocation)
            envelope = ModelInputEnvelope.model_validate(envelope)
        except (TypeError, ValidationError, ValueError) as error:
            raise ProviderProtocolError(
                "Phase 2 invocation or envelope is invalid"
            ) from error
        if (
            invocation.operation is not envelope.operation
            or invocation.allowed_actions is not envelope.allowed_actions
            or invocation.provider_parameters.model_snapshot
            != envelope.model_snapshot
            or invocation.provider_parameters.provider_identity
            != PHASE2_PROVIDER_IDENTITY
            or self._config.model != envelope.model_snapshot
        ):
            raise ProviderProtocolError(
                "provider request conflicts with the frozen Phase 2 mapping"
            )
        if (
            type(exact_input_tokens) is not int
            or exact_input_tokens <= 0
            or type(max_completion_tokens) is not int
            or max_completion_tokens <= 0
        ):
            raise ProviderProtocolError("provider token limits are invalid")

        schema_envelope = envelope.response_schema
        schema = schema_envelope.get("schema")
        if (
            schema_envelope.get("dialect")
            != "https://json-schema.org/draft/2020-12/schema"
            or not isinstance(schema, dict)
        ):
            raise ProviderProtocolError(
                "response schema is not the frozen JSON Schema dialect"
            )
        visible_input = {
            "schema_version": envelope.schema_version,
            "operation": envelope.operation.value,
            "allowed_actions": envelope.allowed_actions.value,
            "model_snapshot": envelope.model_snapshot,
            "request": envelope.request,
        }
        payload: dict[str, object] = {
            "model": self._config.model,
            "messages": [
                {
                    "role": "system",
                    "content": envelope.system_instruction,
                },
                {
                    "role": "user",
                    "content": canonical_json_bytes(visible_input).decode("utf-8"),
                },
            ],
            "temperature": 0.0,
            "n": 1,
            "parallel_tool_calls": False,
            "max_completion_tokens": max_completion_tokens,
            "tool_choice": {
                "type": "function",
                "function": {"name": "submit_phase2_response"},
            },
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "submit_phase2_response",
                        "description": "Return the exact typed Phase 2 response.",
                        "strict": False,
                        "parameters": schema,
                    },
                },
            ],
        }
        effective_transport = (
            self._transport
            if self._transport is not None
            else StdlibOpenAICompatibleTransport()
        )
        try:
            raw = effective_transport.post_json(
                url=f"{self._config.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                },
                payload=payload,
                timeout_seconds=self._timeout_seconds,
            )
        except ProviderProtocolError:
            raise
        except TimeoutError:
            raise TimeoutError("Phase 2 provider request timed out") from None
        except Exception:
            raise ConnectionError("Phase 2 provider request failed") from None
        self._calls += 1
        envelope_response = _require_mapping(raw, "provider response")
        _require_bounded_json(envelope_response)
        if _contains_credential(envelope_response, self._config.api_key):
            raise ProviderProtocolError(
                "provider response contains forbidden credential material"
            )
        response_id = envelope_response.get("id")
        model = envelope_response.get("model")
        if not isinstance(response_id, str) or not response_id.strip():
            raise ProviderProtocolError("provider response id is invalid")
        if model != self._config.model:
            raise ProviderProtocolError("provider response model is not frozen")
        choice = _require_mapping(
            _require_one(envelope_response.get("choices"), "choices"),
            "choice",
        )
        if (
            type(choice.get("index")) is not int
            or choice.get("index") != 0
            or choice.get("finish_reason") != "tool_calls"
        ):
            raise ProviderProtocolError("provider choice metadata is invalid")
        message = _require_mapping(choice.get("message"), "message")
        if (
            message.get("role") != "assistant"
            or message.get("content") is not None
            or message.get("refusal") is not None
            or "tool_calls" not in message
            or "function_call" in message
        ):
            raise ProviderProtocolError("provider assistant message is invalid")
        tool_call = _require_mapping(
            _require_one(message.get("tool_calls"), "tool_calls"),
            "tool call",
        )
        if set(tool_call) != {"id", "type", "function"}:
            raise ProviderProtocolError("provider tool call fields are not exact")
        tool_call_id = tool_call.get("id")
        if (
            not isinstance(tool_call_id, str)
            or not tool_call_id.strip()
            or tool_call.get("type") != "function"
        ):
            raise ProviderProtocolError("provider tool call identity is invalid")
        function = _require_mapping(tool_call.get("function"), "function")
        if (
            set(function) != {"name", "arguments"}
            or function.get("name") != "submit_phase2_response"
        ):
            raise ProviderProtocolError("provider function fields are invalid")
        response = _parse_content(function.get("arguments"))
        usage = _parse_usage(envelope_response.get("usage"))
        if usage.output_tokens > max_completion_tokens:
            raise ProviderProtocolError(
                "provider completion exceeds the admitted token limit"
            )
        self._provider_prompt_tokens = (
            *self._provider_prompt_tokens,
            usage.input_tokens,
        )
        return ModelCompletion(
            schema_version="phase2.model-completion.v1",
            provider_identity=PHASE2_PROVIDER_IDENTITY,
            response=response,
            input_tokens=exact_input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=exact_input_tokens + usage.output_tokens,
            phase1_response=None,
        )
