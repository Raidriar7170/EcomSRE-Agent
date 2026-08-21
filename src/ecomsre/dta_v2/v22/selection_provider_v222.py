"""Short A/T selection Provider with bounded protocol and transport repair."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from pathlib import Path
import time
from typing import Literal, cast

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22
from ecomsre.dta_v2.v22.simple_provider import (
    ProviderTransportErrorV22,
    ProviderTransportV22,
    StdlibProviderTransportV22,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


FUNCTION_NAME_V222 = "submit_dta_selection"
TRANSPORT_RETRY_BACKOFF_SECONDS_V222 = (5.0, 15.0, 30.0)
DEFAULT_MINIMUM_REQUEST_INTERVAL_SECONDS_V222 = 4.0
_MAX_VISIBLE_BYTES = 16_000


class SelectionProviderSemanticErrorV222(ValueError):
    def __init__(self, safe_code: str) -> None:
        self.safe_code = safe_code
        super().__init__(safe_code)


class SelectionProviderProtocolFailureV222(RuntimeError):
    def __init__(
        self,
        safe_code: str,
        *,
        provider_calls: int,
        protocol_repairs: int,
        transport_retry_count: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        latency_ms: float,
    ) -> None:
        self.safe_code = safe_code
        self.provider_calls = provider_calls
        self.protocol_repairs = protocol_repairs
        self.transport_retry_count = transport_retry_count
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens
        self.latency_ms = latency_ms
        super().__init__(safe_code)


class SelectionAliasBindingV222(DtaModelV22):
    alias: str = Field(pattern=r"^[HATE][0-9]{2}$")
    canonical_id: str


class SelectionAliasTableV222(DtaModelV22):
    hypotheses: tuple[SelectionAliasBindingV222, ...]
    actions: tuple[SelectionAliasBindingV222, ...]
    terminals: tuple[SelectionAliasBindingV222, ...]
    evidence: tuple[SelectionAliasBindingV222, ...]

    @classmethod
    def build(
        cls,
        *,
        hypothesis_ids: tuple[str, ...],
        action_ids: tuple[str, ...],
        terminal_ids: tuple[str, ...],
        evidence_refs: tuple[str, ...],
    ) -> "SelectionAliasTableV222":
        values = (
            ("H", hypothesis_ids),
            ("A", action_ids),
            ("T", terminal_ids),
            ("E", evidence_refs),
        )
        if not hypothesis_ids:
            raise ValueError("selection aliases require hypotheses")
        if any(len(items) != len(set(items)) or len(items) > 100 for _, items in values):
            raise ValueError("selection aliases require bounded unique inputs")
        return cls(
            hypotheses=tuple(
                SelectionAliasBindingV222(alias=f"H{index:02d}", canonical_id=item)
                for index, item in enumerate(hypothesis_ids)
            ),
            actions=tuple(
                SelectionAliasBindingV222(alias=f"A{index:02d}", canonical_id=item)
                for index, item in enumerate(action_ids)
            ),
            terminals=tuple(
                SelectionAliasBindingV222(alias=f"T{index:02d}", canonical_id=item)
                for index, item in enumerate(terminal_ids)
            ),
            evidence=tuple(
                SelectionAliasBindingV222(alias=f"E{index:02d}", canonical_id=item)
                for index, item in enumerate(evidence_refs)
            ),
        )

    @model_validator(mode="after")
    def require_aliases(self) -> "SelectionAliasTableV222":
        for prefix, values in (
            ("H", self.hypotheses),
            ("A", self.actions),
            ("T", self.terminals),
            ("E", self.evidence),
        ):
            if tuple(item.alias for item in values) != tuple(
                f"{prefix}{index:02d}" for index in range(len(values))
            ):
                raise ValueError("selection aliases are not contiguous")
        return self

    @staticmethod
    def _resolve(
        bindings: tuple[SelectionAliasBindingV222, ...], alias: str
    ) -> str | None:
        item = next((item for item in bindings if item.alias == alias), None)
        return None if item is None else item.canonical_id

    def resolve_hypothesis(self, alias: str) -> str | None:
        return self._resolve(self.hypotheses, alias)

    def resolve_action(self, alias: str) -> str | None:
        return self._resolve(self.actions, alias)

    def resolve_terminal(self, alias: str) -> str | None:
        return self._resolve(self.terminals, alias)


class SelectionTurnRequestV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.selection-turn-request.v1"]
    system_prompt: str
    aliases: SelectionAliasTableV222
    visible_state: dict[str, object]
    serialized_visible_state_bytes: StrictInt = Field(ge=1, le=_MAX_VISIBLE_BYTES)

    @classmethod
    def build(
        cls,
        *,
        system_prompt: str,
        aliases: SelectionAliasTableV222,
        visible_state: dict[str, object],
    ) -> "SelectionTurnRequestV222":
        serialized = json.dumps(
            visible_state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return cls(
            schema_version="dta-v22.2.selection-turn-request.v1",
            system_prompt=system_prompt,
            aliases=aliases,
            visible_state=visible_state,
            serialized_visible_state_bytes=len(serialized),
        )


class SelectionDecisionV222(DtaModelV22):
    selection_alias: str
    focus_alias: str
    action_id: str | None
    terminal_id: str | None
    focus_hypothesis_id: str | None

    @model_validator(mode="after")
    def require_shape(self) -> "SelectionDecisionV222":
        action = self.action_id is not None
        if action != (self.terminal_id is None and self.focus_hypothesis_id is not None):
            raise ValueError("selection decision kind differs from focus")
        if (not action) != (self.focus_alias == "NONE"):
            raise ValueError("terminal selection must use NONE focus")
        return self


class SelectionProviderOutcomeV222(DtaModelV22):
    decision: SelectionDecisionV222
    first_pass_protocol_success: StrictBool
    post_repair_protocol_success: StrictBool
    protocol_repairs: StrictInt = Field(ge=0, le=2)
    provider_calls: StrictInt = Field(ge=1, le=3)
    transport_retry_count: StrictInt = Field(ge=0, le=9)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    latency_ms: StrictFloat = Field(ge=0)


_STATIC_TOOL_V222 = {
    "type": "function",
    "function": {
        "name": FUNCTION_NAME_V222,
        "description": "Select one current evidence action or runtime-admissible terminal.",
        "strict": False,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selection": {"type": "string"},
                "focus": {"type": "string"},
            },
            "required": ["focus", "selection"],
        },
    },
}


def _provider_object(response: Mapping[str, object]) -> Mapping[str, object]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise SelectionProviderSemanticErrorV222("INVALID_PROVIDER_ENVELOPE")
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise SelectionProviderSemanticErrorV222("INVALID_PROVIDER_ENVELOPE")
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise SelectionProviderSemanticErrorV222("INVALID_PROVIDER_ENVELOPE")
    tool_calls = message.get("tool_calls")
    raw: object
    if tool_calls is not None:
        if not isinstance(tool_calls, list) or len(tool_calls) != 1:
            raise SelectionProviderSemanticErrorV222("INVALID_TOOL_CALL_COUNT")
        call = tool_calls[0]
        if not isinstance(call, Mapping) or not isinstance(call.get("function"), Mapping):
            raise SelectionProviderSemanticErrorV222("INVALID_TOOL_CALL")
        function = cast(Mapping[str, object], call["function"])
        if function.get("name") != FUNCTION_NAME_V222:
            raise SelectionProviderSemanticErrorV222("INVALID_TOOL_NAME")
        raw = function.get("arguments")
    else:
        raw = message.get("content")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as error:
            raise SelectionProviderSemanticErrorV222("INVALID_JSON") from error
    if not isinstance(raw, Mapping):
        raise SelectionProviderSemanticErrorV222("INVALID_JSON_OBJECT")
    return cast(Mapping[str, object], raw)


def parse_selection_response_v222(
    response: Mapping[str, object], *, aliases: SelectionAliasTableV222
) -> SelectionDecisionV222:
    raw = _provider_object(response)
    if set(raw) != {"selection", "focus"}:
        raise SelectionProviderSemanticErrorV222("INVALID_DECISION_SHAPE")
    selection = raw.get("selection")
    focus = raw.get("focus")
    if not isinstance(selection, str) or not isinstance(focus, str):
        raise SelectionProviderSemanticErrorV222("INVALID_DECISION_SHAPE")
    if selection.startswith("A"):
        action = aliases.resolve_action(selection)
        hypothesis = aliases.resolve_hypothesis(focus)
        if action is None:
            raise SelectionProviderSemanticErrorV222("UNKNOWN_ACTION_ALIAS")
        if hypothesis is None:
            raise SelectionProviderSemanticErrorV222("SELECTION_FOCUS_MISMATCH")
        return SelectionDecisionV222(
            selection_alias=selection,
            focus_alias=focus,
            action_id=action,
            terminal_id=None,
            focus_hypothesis_id=hypothesis,
        )
    if selection.startswith("T"):
        terminal = aliases.resolve_terminal(selection)
        if terminal is None:
            raise SelectionProviderSemanticErrorV222("UNKNOWN_TERMINAL_ALIAS")
        if focus != "NONE":
            raise SelectionProviderSemanticErrorV222("SELECTION_FOCUS_MISMATCH")
        return SelectionDecisionV222(
            selection_alias=selection,
            focus_alias=focus,
            action_id=None,
            terminal_id=terminal,
            focus_hypothesis_id=None,
        )
    raise SelectionProviderSemanticErrorV222("UNKNOWN_ALIAS_KIND")


def _usage(response: Mapping[str, object]) -> tuple[int, int, int]:
    usage = response.get("usage")
    if not isinstance(usage, Mapping):
        return (0, 0, 0)
    values = (
        usage.get("prompt_tokens", usage.get("input_tokens", 0)),
        usage.get("completion_tokens", usage.get("output_tokens", 0)),
        usage.get("total_tokens", 0),
    )
    return cast(
        tuple[int, int, int],
        tuple(value if type(value) is int and value >= 0 else 0 for value in values),
    )


class SelectionProviderV222:
    def __init__(
        self,
        *,
        config: OpenAICompatibleConfig,
        transport: ProviderTransportV22 | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        minimum_request_interval_seconds: float = DEFAULT_MINIMUM_REQUEST_INTERVAL_SECONDS_V222,
        timeout_seconds: float = 120.0,
        max_completion_tokens: int = 120,
        debug_root: Path = Path(".local/dta-v22-2-debug"),
    ) -> None:
        if minimum_request_interval_seconds < 0 or timeout_seconds <= 0:
            raise ValueError("selection Provider timing limits are invalid")
        self.config = config
        self.transport = transport or StdlibProviderTransportV22()
        self.sleeper = sleeper
        self.clock = clock
        self.minimum_request_interval_seconds = minimum_request_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.max_completion_tokens = max_completion_tokens
        self.debug_root = debug_root
        self._last_started: float | None = None

    def _pace(self) -> None:
        now = self.clock()
        if self._last_started is not None:
            remaining = self.minimum_request_interval_seconds - (now - self._last_started)
            if remaining > 0:
                self.sleeper(remaining)
                now = self.clock()
        self._last_started = now

    def _post(
        self, payload: Mapping[str, object]
    ) -> tuple[Mapping[str, object], int, float]:
        retries = 0
        latency = 0.0
        while True:
            self._pace()
            started = self.clock()
            try:
                response = self.transport.post_json(
                    url=f"{self.config.base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json",
                    },
                    payload=payload,
                    timeout_seconds=self.timeout_seconds,
                )
            except ProviderTransportErrorV22 as error:
                latency += max(0.0, (self.clock() - started) * 1000)
                retryable = error.retryable or error.safe_code in {
                    "CONNECTION_ERROR",
                    "TEMPORARY_CONNECTION_ERROR",
                }
                if not retryable or retries >= len(TRANSPORT_RETRY_BACKOFF_SECONDS_V222):
                    raise ProviderTransportErrorV22(
                        safe_code=error.safe_code,
                        status_code=error.status_code,
                        retry_count=retries,
                        latency_ms=latency,
                    ) from error
                self.sleeper(TRANSPORT_RETRY_BACKOFF_SECONDS_V222[retries])
                retries += 1
                continue
            latency += max(0.0, (self.clock() - started) * 1000)
            return response, retries, latency

    def _payload(
        self, *, request: SelectionTurnRequestV222, repair_code: str | None
    ) -> dict[str, object]:
        if repair_code is None:
            user = {
                "visible_state": request.visible_state,
                "required_shape": {"selection": "Axx or Txx", "focus": "Hxx or NONE"},
            }
        else:
            user = {
                "repair": {
                    "safe_error_code": repair_code,
                    "valid_A_aliases": [item.alias for item in request.aliases.actions],
                    "valid_T_aliases": [item.alias for item in request.aliases.terminals],
                    "valid_H_aliases": [item.alias for item in request.aliases.hypotheses],
                    "required_shape": {
                        "selection": "Axx or Txx",
                        "focus": "Hxx for A, NONE for T",
                    },
                }
            }
        return {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        user,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "tools": [_STATIC_TOOL_V222],
            "tool_choice": {
                "type": "function",
                "function": {"name": FUNCTION_NAME_V222},
            },
            "parallel_tool_calls": False,
            "temperature": 0,
            "max_completion_tokens": self.max_completion_tokens,
        }

    def complete_turn(
        self, *, request: SelectionTurnRequestV222, run_id: str
    ) -> SelectionProviderOutcomeV222:
        return self._complete(
            request=request,
            run_id=run_id,
            initial_repair_code=None,
        )

    def complete_repair_turn(
        self,
        *,
        request: SelectionTurnRequestV222,
        run_id: str,
        safe_error_code: str,
    ) -> SelectionProviderOutcomeV222:
        """Exercise one current alias frontier after a runtime protocol rejection."""

        return self._complete(
            request=request,
            run_id=run_id,
            initial_repair_code=safe_error_code,
        )

    def _complete(
        self,
        *,
        request: SelectionTurnRequestV222,
        run_id: str,
        initial_repair_code: str | None,
    ) -> SelectionProviderOutcomeV222:
        del run_id
        provider_calls = retries = 0
        repairs = int(initial_repair_code is not None)
        latency = 0.0
        usages: list[tuple[int, int, int]] = []
        repair_code = initial_repair_code
        while provider_calls < 3:
            payload = self._payload(request=request, repair_code=repair_code)
            provider_calls += 1
            try:
                response, request_retries, request_latency = self._post(payload)
            except ProviderTransportErrorV22 as error:
                retries += error.retry_count
                latency += error.latency_ms
                raise SelectionProviderProtocolFailureV222(
                    "TRANSPORT_FAILED",
                    provider_calls=provider_calls,
                    protocol_repairs=repairs,
                    transport_retry_count=retries,
                    input_tokens=sum(item[0] for item in usages),
                    output_tokens=sum(item[1] for item in usages),
                    total_tokens=sum(item[2] for item in usages),
                    latency_ms=latency,
                ) from error
            retries += request_retries
            latency += request_latency
            usages.append(_usage(response))
            try:
                decision = parse_selection_response_v222(
                    response,
                    aliases=request.aliases,
                )
            except SelectionProviderSemanticErrorV222 as error:
                repair_code = error.safe_code
                if repairs >= 2:
                    break
                repairs += 1
                continue
            input_tokens = sum(item[0] for item in usages)
            output_tokens = sum(item[1] for item in usages)
            reported_total = sum(item[2] for item in usages)
            return SelectionProviderOutcomeV222(
                decision=decision,
                first_pass_protocol_success=repairs == 0,
                post_repair_protocol_success=True,
                protocol_repairs=repairs,
                provider_calls=provider_calls,
                transport_retry_count=retries,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=reported_total or input_tokens + output_tokens,
                latency_ms=latency,
            )
        input_tokens = sum(item[0] for item in usages)
        output_tokens = sum(item[1] for item in usages)
        reported_total = sum(item[2] for item in usages)
        raise SelectionProviderProtocolFailureV222(
            repair_code or "PROTOCOL_FAILED",
            provider_calls=provider_calls,
            protocol_repairs=repairs,
            transport_retry_count=retries,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=reported_total or input_tokens + output_tokens,
            latency_ms=latency,
        )


__all__ = (
    "FUNCTION_NAME_V222",
    "SelectionAliasTableV222",
    "SelectionDecisionV222",
    "SelectionProviderOutcomeV222",
    "SelectionProviderProtocolFailureV222",
    "SelectionProviderV222",
    "SelectionTurnRequestV222",
    "parse_selection_response_v222",
)
