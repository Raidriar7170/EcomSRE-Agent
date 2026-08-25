"""Opaque A/T Provider adapter for the DTA v2.2.6 real-fault study."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from pathlib import Path
import re
import time
from typing import cast

from ecomsre.dta_v2.v22.real_fault_capture_v225 import (
    require_provider_payload_opaque_v225,
)
from ecomsre.dta_v2.v22.real_fault_selection_v226 import (
    REAL_FAULT_SELECTION_SYSTEM_PROMPT_V226,
    RealFaultSelectionDecisionV226,
    RealFaultSelectionOutcomeV226,
    RealFaultSelectionRequestV226,
)
from ecomsre.dta_v2.v22.selection_provider_v222 import (
    SelectionProviderV222,
    _usage,
)
from ecomsre.dta_v2.v22.simple_provider import (
    ProviderTransportErrorV22,
    ProviderTransportV22,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


REAL_FAULT_SELECTION_FUNCTION_V226 = "submit_real_fault_selection"
_RUN_ID = re.compile(r"^[0-9a-f]{32}$")

_STATIC_SELECTION_TOOL_V226: dict[str, object] = {
    "type": "function",
    "function": {
        "name": REAL_FAULT_SELECTION_FUNCTION_V226,
        "description": "Select one current opaque read action or admitted terminal.",
        "strict": False,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selection": {"type": "string"},
                "focus": {"type": "string"},
            },
            "required": ["selection", "focus"],
        },
    },
}


class _RealFaultSelectionSemanticErrorV226(ValueError):
    def __init__(self, safe_code: str) -> None:
        self.safe_code = safe_code
        super().__init__(safe_code)


class RealFaultSelectionProtocolFailureV226(RuntimeError):
    """Safe Provider failure retaining complete bounded cost accounting."""

    def __init__(
        self,
        safe_code: str,
        *,
        provider_calls: int,
        protocol_repairs: int,
        transport_retry_count: int,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        transport_failure: bool,
    ) -> None:
        self.safe_code = safe_code
        self.provider_calls = provider_calls
        self.protocol_repairs = protocol_repairs
        self.transport_retry_count = transport_retry_count
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = input_tokens + output_tokens
        self.latency_ms = latency_ms
        self.transport_failure = transport_failure
        super().__init__(safe_code)


def _provider_object_v226(
    response: Mapping[str, object],
) -> Mapping[str, object]:
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise _RealFaultSelectionSemanticErrorV226("INVALID_PROVIDER_ENVELOPE")
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise _RealFaultSelectionSemanticErrorV226("INVALID_PROVIDER_ENVELOPE")
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise _RealFaultSelectionSemanticErrorV226("INVALID_PROVIDER_ENVELOPE")
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or len(tool_calls) != 1:
        raise _RealFaultSelectionSemanticErrorV226("INVALID_TOOL_CALL_COUNT")
    call = tool_calls[0]
    if not isinstance(call, Mapping):
        raise _RealFaultSelectionSemanticErrorV226("INVALID_TOOL_CALL")
    function = call.get("function")
    if not isinstance(function, Mapping):
        raise _RealFaultSelectionSemanticErrorV226("INVALID_TOOL_CALL")
    if function.get("name") != REAL_FAULT_SELECTION_FUNCTION_V226:
        raise _RealFaultSelectionSemanticErrorV226("INVALID_TOOL_NAME")
    raw = function.get("arguments")
    if not isinstance(raw, str):
        raise _RealFaultSelectionSemanticErrorV226("INVALID_JSON")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise _RealFaultSelectionSemanticErrorV226("INVALID_JSON") from error
    if not isinstance(parsed, Mapping):
        raise _RealFaultSelectionSemanticErrorV226("INVALID_JSON_OBJECT")
    return cast(Mapping[str, object], parsed)


def _parse_selection_v226(
    response: Mapping[str, object],
    *,
    request: RealFaultSelectionRequestV226,
) -> RealFaultSelectionDecisionV226:
    raw = _provider_object_v226(response)
    if set(raw) != {"selection", "focus"}:
        raise _RealFaultSelectionSemanticErrorV226("INVALID_DECISION_SHAPE")
    selection = raw.get("selection")
    focus = raw.get("focus")
    if not isinstance(selection, str) or not isinstance(focus, str):
        raise _RealFaultSelectionSemanticErrorV226("INVALID_DECISION_SHAPE")
    action_aliases = {item.alias for item in request.actions}
    terminal_aliases = {item.alias for item in request.terminals}
    focus_aliases = {item.alias for item in request.focuses}
    if selection.startswith("A"):
        if selection not in action_aliases:
            raise _RealFaultSelectionSemanticErrorV226("UNKNOWN_ACTION_ALIAS")
        if focus not in focus_aliases:
            raise _RealFaultSelectionSemanticErrorV226(
                "SELECTION_FOCUS_MISMATCH"
            )
    elif selection.startswith("T"):
        if selection not in terminal_aliases:
            raise _RealFaultSelectionSemanticErrorV226("UNKNOWN_TERMINAL_ALIAS")
        if focus != "NONE":
            raise _RealFaultSelectionSemanticErrorV226(
                "SELECTION_FOCUS_MISMATCH"
            )
    else:
        raise _RealFaultSelectionSemanticErrorV226("UNKNOWN_ALIAS_KIND")
    return RealFaultSelectionDecisionV226(selection=selection, focus=focus)


class RealFaultSelectionProviderAdapterV226(SelectionProviderV222):
    """Use one forced opaque function with bounded protocol and transport repair."""

    def __init__(
        self,
        *,
        config: OpenAICompatibleConfig,
        transport: ProviderTransportV22 | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        minimum_request_interval_seconds: float = 4.0,
        timeout_seconds: float = 120.0,
        max_completion_tokens: int = 120,
        debug_root: Path = Path(".local/dta-v226-real-fault-debug"),
    ) -> None:
        super().__init__(
            config=config,
            transport=transport,
            sleeper=sleeper,
            clock=clock,
            minimum_request_interval_seconds=minimum_request_interval_seconds,
            timeout_seconds=timeout_seconds,
            max_completion_tokens=max_completion_tokens,
            debug_root=debug_root,
        )

    def _selection_payload(
        self,
        *,
        request: RealFaultSelectionRequestV226,
        repair_code: str | None,
    ) -> dict[str, object]:
        if repair_code is None:
            user: dict[str, object] = {
                "selection_surface": request.model_dump(mode="json"),
                "required_shape": {
                    "selection": "one supplied Axx or Txx alias",
                    "focus": "one supplied Hxx for A, NONE for T",
                },
            }
        else:
            user = {
                "repair": {
                    "safe_error_code": repair_code,
                    "valid_A_aliases": [item.alias for item in request.actions],
                    "valid_T_aliases": [item.alias for item in request.terminals],
                    "valid_H_aliases": [item.alias for item in request.focuses],
                    "required_shape": {
                        "selection": "one supplied Axx or Txx alias",
                        "focus": "one supplied Hxx for A, NONE for T",
                    },
                }
            }
        require_provider_payload_opaque_v225(user)
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": REAL_FAULT_SELECTION_SYSTEM_PROMPT_V226,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        user,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                },
            ],
            "tools": [_STATIC_SELECTION_TOOL_V226],
            "tool_choice": {
                "type": "function",
                "function": {"name": REAL_FAULT_SELECTION_FUNCTION_V226},
            },
            "parallel_tool_calls": False,
            "temperature": 0,
            "max_completion_tokens": self.max_completion_tokens,
        }
        require_provider_payload_opaque_v225(payload)
        return payload

    def complete_selection(
        self,
        *,
        request: RealFaultSelectionRequestV226,
        run_id: str,
        max_protocol_repairs: int = 2,
    ) -> RealFaultSelectionOutcomeV226:
        if _RUN_ID.fullmatch(run_id) is None:
            raise ValueError("real-fault selection run ID is invalid")
        if not 0 <= max_protocol_repairs <= 2:
            raise ValueError("real-fault protocol repair budget is invalid")
        provider_calls = retries = repairs = 0
        latency_ms = 0.0
        usages: list[tuple[int, int, int]] = []
        repair_code: str | None = None
        while provider_calls < max_protocol_repairs + 1:
            payload = self._selection_payload(
                request=request,
                repair_code=repair_code,
            )
            provider_calls += 1
            try:
                response, request_retries, request_latency = self._post(payload)
            except ProviderTransportErrorV22 as error:
                retries += error.retry_count
                latency_ms += error.latency_ms
                raise RealFaultSelectionProtocolFailureV226(
                    "TRANSPORT_FAILED",
                    provider_calls=provider_calls,
                    protocol_repairs=repairs,
                    transport_retry_count=retries,
                    input_tokens=sum(item[0] for item in usages),
                    output_tokens=sum(item[1] for item in usages),
                    latency_ms=latency_ms,
                    transport_failure=True,
                ) from error
            retries += request_retries
            latency_ms += request_latency
            usages.append(_usage(response))
            try:
                decision = _parse_selection_v226(response, request=request)
            except _RealFaultSelectionSemanticErrorV226 as error:
                repair_code = error.safe_code
                if repairs >= max_protocol_repairs:
                    break
                repairs += 1
                continue
            input_tokens = sum(item[0] for item in usages)
            output_tokens = sum(item[1] for item in usages)
            return RealFaultSelectionOutcomeV226(
                decision=decision,
                first_pass_protocol_success=repairs == 0,
                post_repair_protocol_success=True,
                protocol_repairs=repairs,
                provider_calls=provider_calls,
                transport_retry_count=retries,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                latency_ms=latency_ms,
            )
        input_tokens = sum(item[0] for item in usages)
        output_tokens = sum(item[1] for item in usages)
        raise RealFaultSelectionProtocolFailureV226(
            repair_code or "PROTOCOL_FAILED",
            provider_calls=provider_calls,
            protocol_repairs=repairs,
            transport_retry_count=retries,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            transport_failure=False,
        )


__all__ = (
    "REAL_FAULT_SELECTION_FUNCTION_V226",
    "RealFaultSelectionProtocolFailureV226",
    "RealFaultSelectionProviderAdapterV226",
)
