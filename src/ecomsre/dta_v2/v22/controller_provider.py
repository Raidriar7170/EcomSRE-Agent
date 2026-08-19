"""Fail-closed OpenAI-compatible Provider boundary for DTA v2.2 controllers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import time
from typing import Any, Literal, Protocol, cast
import urllib.error
import urllib.request

from pydantic import Field, InstanceOf, StrictInt, ValidationInfo, model_validator

from ecomsre.dta_v2.v22.controller_contracts import (
    ABSTAIN_HYPOTHESIS_ID_V22,
    ControllerDecisionKindV22,
    ControllerDecisionV22,
)
from ecomsre.dta_v2.v22.controller_modes import (
    PRIMARY_MODEL_V22,
    ControllerIdentityManifestV22,
    DeterministicRouterFinalInputV22,
    EvaluationArmV22,
    ProviderOutputModeV22,
    ProviderProbeStatusV22,
    controller_system_prompt_v22,
)
from ecomsre.dta_v2.v22.controller_inputs import (
    ControllerArmV22,
    ControllerTurnInputV22,
)
from ecomsre.dta_v2.v22.controller_runtime import PlanCorrectionV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    Sha256V22,
    semantic_sha256_v22,
)
from ecomsre.model.gateway import (
    MAX_PROVIDER_RESPONSE_BYTES,
    OpenAICompatibleConfig,
    RejectRedirectHandler,
    _contains_credential,
    _parse_usage,
    _reject_json_constant,
    _require_bounded_json,
    _strict_object,
)


_CONTROLLER_FUNCTION_V22 = "submit_dta_v22_controller_decision"
_CONTROLLER_SCHEMA_NAME_V22 = "dta_v22_controller_decision"
_PROBE_VISIBLE_STATE_V22: dict[str, object] = {
    "protocol_task": (
        "This is an output-mode capability probe with no evidence or actions. "
        "Return the protocol-safe unresolved terminal."
    ),
    "hypothesis_catalog": [ABSTAIN_HYPOTHESIS_ID_V22],
    "valid_action_ids": [],
    "known_evidence_refs": [],
}
_PROTOCOL_TASK_V22 = (
    "Choose one protocol-valid next ControllerDecisionV22 from the supplied state. "
    "READ only an available action; COMMIT only when cited predicates satisfy the "
    "selected hypothesis; choose NO_INCIDENT only with complete healthy runtime and "
    "supported core metrics for every candidate and no strong anomaly; choose ABSTAIN "
    "only for explicit exhausted budget, unavailable evidence, or conflict. The state "
    "does not contain an evaluator answer."
)


@dataclass(frozen=True, slots=True)
class ProviderHttpErrorV22(Exception):
    """Safe structured HTTP failure without retaining Provider-controlled text."""

    status: int
    code: str | None
    error_type: str | None
    param: str | None

    @property
    def strict_schema_unsupported(self) -> bool:
        return (
            self.status in {400, 404, 422}
            and self.code
            in {
                "invalid_parameter",
                "unsupported_parameter",
                "unsupported_value",
            }
            and self.error_type in {"invalid_request_error", "unsupported_error"}
            and self.param is not None
            and (
                self.param == "response_format"
                or self.param.startswith("response_format.")
            )
        )

    def __str__(self) -> str:
        return f"Provider HTTP request failed with status {self.status}"


class ControllerProviderTransportV22(Protocol):
    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]: ...


def _safe_error_field(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 80:
        return None
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for character in value):
        return None
    return value


def _http_error_v22(error: urllib.error.HTTPError) -> ProviderHttpErrorV22:
    code: str | None = None
    error_type: str | None = None
    param: str | None = None
    try:
        body = error.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
        if len(body) <= MAX_PROVIDER_RESPONSE_BYTES:
            decoded = json.loads(
                body.decode("utf-8", errors="strict"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
            _require_bounded_json(decoded)
            if isinstance(decoded, dict) and isinstance(decoded.get("error"), dict):
                detail = cast(dict[str, object], decoded["error"])
                code = _safe_error_field(detail.get("code"))
                error_type = _safe_error_field(detail.get("type"))
                param = _safe_error_field(detail.get("param"))
    except (UnicodeError, ValueError, json.JSONDecodeError, OSError):
        pass
    return ProviderHttpErrorV22(
        status=error.code,
        code=code,
        error_type=error_type,
        param=param,
    )


class StdlibControllerProviderTransportV22:
    """Bounded HTTPS transport that preserves safe structured HTTP status."""

    def __init__(self, opener: object | None = None) -> None:
        self._opener = opener or urllib.request.build_opener(RejectRedirectHandler())

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        request = urllib.request.Request(
            url=url,
            data=json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            with self._opener.open(  # type: ignore[attr-defined]
                request,
                timeout=timeout_seconds,
            ) as response:
                content = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            raise _http_error_v22(error) from None
        except TimeoutError:
            raise
        except (OSError, urllib.error.URLError) as error:
            raise ConnectionError("DTA v2.2 Provider request failed") from error
        if len(content) > MAX_PROVIDER_RESPONSE_BYTES:
            raise ValueError("Provider response exceeds size limit")
        try:
            decoded = json.loads(
                content.decode("utf-8", errors="strict"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
            _require_bounded_json(decoded)
        except (UnicodeError, ValueError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError("Provider response is not bounded strict JSON") from error
        if not isinstance(decoded, dict):
            raise ValueError("Provider response must be an object")
        return decoded


_PRIMARY_INPUT_ARM_BY_IDENTITY_ARM_V22 = {
    EvaluationArmV22.FLAT_CANONICAL_SALIENT: ControllerArmV22.FLAT_CANONICAL,
    EvaluationArmV22.PLANNER_LITE_SALIENT: ControllerArmV22.PLANNER_LITE,
}


class ProviderTurnRequestV22(DtaModelV22):
    """Exact typed controller input and declared identity sent to the Provider."""

    schema_version: Literal["dta-v22.provider-turn-request.v1"]
    execution_mode: Literal["CONTROLLER", "PROTOCOL_ONLY"]
    identity: InstanceOf[ControllerIdentityManifestV22]
    controller_input: InstanceOf[ControllerTurnInputV22]
    plan_correction: InstanceOf[PlanCorrectionV22] | None
    request_sha256: Sha256V22

    @model_validator(mode="after")
    def require_request(self) -> ProviderTurnRequestV22:
        self.identity.require_identity()  # type: ignore[operator]
        self.controller_input.runtime_context.require_context()  # type: ignore[operator]
        self.controller_input.require_input()  # type: ignore[operator]
        expected_arm = _PRIMARY_INPUT_ARM_BY_IDENTITY_ARM_V22.get(self.identity.arm)
        if expected_arm is None or self.controller_input.arm is not expected_arm:
            raise ValueError("Provider request identity and controller arm differ")
        runtime = self.controller_input.runtime_context
        if (
            runtime.controller_identity_sha256 != self.identity.identity_sha256
            or self.identity.prompt_sha256
            != semantic_sha256_v22(
                {"system_prompt": controller_system_prompt_v22(self.identity.arm)}
            )
            or (self.plan_correction is None) != runtime.correction_remaining
        ):
            raise ValueError("Provider request identity or correction binding differs")
        if self.plan_correction is not None:
            PlanCorrectionV22.model_validate(
                self.plan_correction.model_dump(mode="python"),
                context={"action_catalog": self.controller_input.action_catalog},
            )
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"request_sha256"})
        )
        if self.request_sha256 != expected:
            raise ValueError("Provider request digest differs")
        return self

    def visible_state(self) -> dict[str, object]:
        return {
            "schema_version": "dta-v22.provider-visible-controller-state.v1",
            "execution_mode": self.execution_mode,
            "protocol_task": _PROTOCOL_TASK_V22,
            "controller_input": self.controller_input.model_dump(mode="json"),
            "plan_correction": (
                None
                if self.plan_correction is None
                else self.plan_correction.model_dump(mode="json")
            ),
        }


def build_provider_turn_request_v22(
    *,
    execution_mode: Literal["CONTROLLER", "PROTOCOL_ONLY"],
    identity: ControllerIdentityManifestV22,
    controller_input: ControllerTurnInputV22,
    plan_correction: PlanCorrectionV22 | None,
) -> ProviderTurnRequestV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.provider-turn-request.v1",
        "execution_mode": execution_mode,
        "identity": identity,
        "controller_input": controller_input,
        "plan_correction": plan_correction,
    }
    draft = ProviderTurnRequestV22.model_construct(
        **payload,
        request_sha256="0" * 64,
    )
    return ProviderTurnRequestV22.model_validate(
        {
            **payload,
            "request_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"request_sha256"})
            ),
        }
    )


class RouterProviderTurnRequestV22(DtaModelV22):
    """Exact answer-free same-model request for router terminal reasoning."""

    schema_version: Literal["dta-v22.router-provider-turn-request.v1"]
    execution_mode: Literal["ROUTER_FINAL"]
    identity: InstanceOf[ControllerIdentityManifestV22]
    router_input: InstanceOf[DeterministicRouterFinalInputV22]
    request_sha256: Sha256V22

    @model_validator(mode="after")
    def require_request(self) -> RouterProviderTurnRequestV22:
        self.identity.require_identity()  # type: ignore[operator]
        self.router_input.require_input()  # type: ignore[operator]
        if self.identity.arm is not EvaluationArmV22.DETERMINISTIC_ROUTER_SALIENT:
            raise ValueError("router Provider request requires router identity")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"request_sha256"})
        )
        if self.request_sha256 != expected:
            raise ValueError("router Provider request digest differs")
        return self

    def visible_state(self) -> dict[str, object]:
        return {
            "schema_version": "dta-v22.provider-visible-router-final-state.v1",
            "execution_mode": self.execution_mode,
            "protocol_task": _PROTOCOL_TASK_V22,
            "router_final_input": self.router_input.model_dump(mode="json"),
        }


def build_router_provider_turn_request_v22(
    *,
    identity: ControllerIdentityManifestV22,
    router_input: DeterministicRouterFinalInputV22,
) -> RouterProviderTurnRequestV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.router-provider-turn-request.v1",
        "execution_mode": "ROUTER_FINAL",
        "identity": identity,
        "router_input": router_input,
    }
    draft = RouterProviderTurnRequestV22.model_construct(
        **payload,
        request_sha256="0" * 64,
    )
    return RouterProviderTurnRequestV22.model_validate(
        {
            **payload,
            "request_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"request_sha256"})
            ),
        }
    )


class ProviderControllerTurnV22(DtaModelV22):
    schema_version: Literal["dta-v22.provider-controller-turn.v1"]
    model: str
    mode: ProviderOutputModeV22
    controller_identity_sha256: Sha256V22
    prompt_sha256: Sha256V22
    visible_input_sha256: Sha256V22
    provider_request_sha256: Sha256V22
    request_payload_sha256: Sha256V22
    decision: ControllerDecisionV22 | None
    parse_error_code: Literal["INVALID_DECISION_SHAPE"] | None
    raw_decision_sha256: Sha256V22
    raw_response_sha256: Sha256V22
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    monotonic_latency_ms: StrictInt = Field(ge=0)
    turn_sha256: Sha256V22

    @model_validator(mode="after")
    def require_turn(self, info: ValidationInfo) -> ProviderControllerTurnV22:
        if self.model != PRIMARY_MODEL_V22:
            raise ValueError("Provider controller turn violates model continuity")
        if (
            self.total_tokens != self.input_tokens + self.output_tokens
            or (self.decision is None) != (self.parse_error_code is not None)
        ):
            raise ValueError("Provider controller token accounting differs")
        context = info.context if isinstance(info.context, dict) else None
        if context is not None and isinstance(
            context.get("request"),
            (ProviderTurnRequestV22, RouterProviderTurnRequestV22),
        ):
            request = context["request"]
            visible = request.visible_state()
            if (
                self.mode is not request.identity.provider_output_mode
                or self.controller_identity_sha256 != request.identity.identity_sha256
                or self.prompt_sha256 != request.identity.prompt_sha256
                or self.visible_input_sha256 != semantic_sha256_v22(visible)
                or self.provider_request_sha256 != request.request_sha256
            ):
                raise ValueError("Provider controller turn differs from typed request")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"turn_sha256"})
        )
        if self.turn_sha256 != expected:
            raise ValueError("Provider controller turn digest differs")
        return self


def _mapping_v22(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise ValueError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _one_v22(value: object, label: str) -> object:
    if not isinstance(value, list) or len(value) != 1:
        raise ValueError(f"{label} must contain exactly one item")
    return value[0]


def _decision_from_json_v22(value: object) -> ControllerDecisionV22:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 16_384:
        raise ValueError("Provider decision JSON is invalid")
    try:
        decoded = json.loads(
            value,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
        _require_bounded_json(decoded)
        if not isinstance(decoded, dict):
            raise ValueError("Provider decision must be an object")
        return ControllerDecisionV22.model_validate_json(value)
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("Provider decision violates ControllerDecisionV22") from error


def _controller_schema_v22() -> dict[str, object]:
    return ControllerDecisionV22.model_json_schema(mode="validation")


class OpenAICompatibleControllerProviderV22:
    """One exact-model Provider call returning one typed controller decision."""

    def __init__(
        self,
        *,
        config: OpenAICompatibleConfig,
        timeout_seconds: float,
        max_completion_tokens: int,
        min_request_interval_seconds: float = 0.0,
        throttle_monotonic_ns: Callable[[], int] = time.monotonic_ns,
        throttle_sleep: Callable[[float], None] = time.sleep,
        transport: ControllerProviderTransportV22 | None = None,
    ) -> None:
        if config.model != PRIMARY_MODEL_V22:
            raise RuntimeError("BLOCKED_DTA_V22_MODEL_CONTINUITY")
        if type(timeout_seconds) is not float or timeout_seconds <= 0:
            raise ValueError("Provider timeout must be a positive float")
        if type(max_completion_tokens) is not int or max_completion_tokens <= 0:
            raise ValueError("Provider completion limit must be positive")
        if (
            type(min_request_interval_seconds) is not float
            or min_request_interval_seconds < 0
        ):
            raise ValueError("Provider request interval must be a non-negative float")
        self._config = config
        self._timeout_seconds = timeout_seconds
        self._max_completion_tokens = max_completion_tokens
        self._min_request_interval_ns = int(
            min_request_interval_seconds * 1_000_000_000
        )
        self._throttle_monotonic_ns = throttle_monotonic_ns
        self._throttle_sleep = throttle_sleep
        self._last_request_started_ns: int | None = None
        self._transport = transport or StdlibControllerProviderTransportV22()
        self._attempted_calls = 0

    @property
    def attempted_calls(self) -> int:
        return self._attempted_calls

    def _wait_for_request_slot_v22(self) -> None:
        now = self._throttle_monotonic_ns()
        if self._last_request_started_ns is not None:
            wait_ns = (
                self._last_request_started_ns
                + self._min_request_interval_ns
                - now
            )
            if wait_ns > 0:
                self._throttle_sleep(wait_ns / 1_000_000_000)
                now = self._throttle_monotonic_ns()
        self._last_request_started_ns = now

    def probe_output_mode(
        self,
        model: str,
        mode: ProviderOutputModeV22,
        controller_schema_sha256: str,
    ) -> ProviderProbeStatusV22:
        if model != PRIMARY_MODEL_V22:
            raise RuntimeError("BLOCKED_DTA_V22_MODEL_CONTINUITY")
        if controller_schema_sha256 != semantic_sha256_v22(_controller_schema_v22()):
            return ProviderProbeStatusV22.FAILED
        try:
            prompt = controller_system_prompt_v22(
                EvaluationArmV22.FLAT_CANONICAL_SALIENT
            )
            visible_sha = semantic_sha256_v22(_PROBE_VISIBLE_STATE_V22)
            turn = self._complete_bound_v22(
                mode=mode,
                system_prompt=prompt,
                visible_state=_PROBE_VISIBLE_STATE_V22,
                controller_identity_sha256=semantic_sha256_v22(
                    {"provider_capability_probe": controller_schema_sha256}
                ),
                prompt_sha256=semantic_sha256_v22({"system_prompt": prompt}),
                visible_input_sha256=visible_sha,
                provider_request_sha256=semantic_sha256_v22(
                    {"probe_input": visible_sha, "mode": mode.value}
                ),
                request_context=None,
            )
        except ProviderHttpErrorV22 as error:
            if (
                mode is ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT
                and error.strict_schema_unsupported
            ):
                return ProviderProbeStatusV22.STRICT_SCHEMA_UNSUPPORTED
            return ProviderProbeStatusV22.FAILED
        except (ConnectionError, TimeoutError, TypeError, ValueError):
            return ProviderProbeStatusV22.FAILED
        if (
            turn.decision is None
            or turn.decision.decision is not ControllerDecisionKindV22.ABSTAIN
            or turn.decision.working_hypothesis_id != ABSTAIN_HYPOTHESIS_ID_V22
        ):
            return ProviderProbeStatusV22.FAILED
        return ProviderProbeStatusV22.SUPPORTED

    def complete_controller_turn(
        self,
        *,
        request: ProviderTurnRequestV22,
    ) -> ProviderControllerTurnV22:
        if not isinstance(request, ProviderTurnRequestV22):
            raise TypeError("Provider controller call requires a typed request")
        request.require_request()  # type: ignore[operator]
        expected_request_sha = semantic_sha256_v22(
            request.model_dump(mode="json", exclude={"request_sha256"})
        )
        if request.request_sha256 != expected_request_sha:
            raise ValueError("Provider request digest differs")
        prompt = controller_system_prompt_v22(request.identity.arm)
        visible_state = request.visible_state()
        return self._complete_bound_v22(
            mode=request.identity.provider_output_mode,
            system_prompt=prompt,
            visible_state=visible_state,
            controller_identity_sha256=request.identity.identity_sha256,
            prompt_sha256=request.identity.prompt_sha256,
            visible_input_sha256=semantic_sha256_v22(visible_state),
            provider_request_sha256=request.request_sha256,
            request_context=request,
        )

    def complete_router_final_turn(
        self,
        *,
        request: RouterProviderTurnRequestV22,
    ) -> ProviderControllerTurnV22:
        if not isinstance(request, RouterProviderTurnRequestV22):
            raise TypeError("router Provider call requires a typed request")
        request.require_request()  # type: ignore[operator]
        prompt = controller_system_prompt_v22(request.identity.arm)
        visible_state = request.visible_state()
        turn = self._complete_bound_v22(
            mode=request.identity.provider_output_mode,
            system_prompt=prompt,
            visible_state=visible_state,
            controller_identity_sha256=request.identity.identity_sha256,
            prompt_sha256=request.identity.prompt_sha256,
            visible_input_sha256=semantic_sha256_v22(visible_state),
            provider_request_sha256=request.request_sha256,
            request_context=request,
        )
        decision = turn.decision
        if decision is None or decision.decision is ControllerDecisionKindV22.READ:
            raise ValueError("router final Provider call did not return a terminal decision")
        request.router_input.hypothesis_catalog.require(
            decision.working_hypothesis_id
        )
        known_refs = {
            item.evidence_ref
            for item in request.router_input.salient_memory.evidence_refs
        }
        if not (
            set(decision.supporting_evidence_refs)
            | set(decision.contradicting_evidence_refs)
        ).issubset(known_refs):
            raise ValueError("router final Provider decision contains an unknown ref")
        return turn

    def _complete_bound_v22(
        self,
        *,
        mode: ProviderOutputModeV22,
        system_prompt: str,
        visible_state: Mapping[str, object],
        controller_identity_sha256: str,
        prompt_sha256: str,
        visible_input_sha256: str,
        provider_request_sha256: str,
        request_context: (
            ProviderTurnRequestV22 | RouterProviderTurnRequestV22 | None
        ),
    ) -> ProviderControllerTurnV22:
        if not isinstance(mode, ProviderOutputModeV22):
            raise TypeError("Provider output mode is invalid")
        _require_bounded_json(visible_state)
        if _contains_credential(visible_state, self._config.api_key):
            raise ValueError("Provider input contains credential material")
        payload = self._payload_v22(
            mode=mode,
            system_prompt=system_prompt,
            visible_state=visible_state,
        )
        self._wait_for_request_slot_v22()
        self._attempted_calls += 1
        started = time.monotonic_ns()
        response = self._transport.post_json(
            url=f"{self._config.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout_seconds=self._timeout_seconds,
        )
        latency = max(0, (time.monotonic_ns() - started) // 1_000_000)
        _require_bounded_json(response)
        if _contains_credential(response, self._config.api_key):
            raise ValueError("Provider response contains credential material")
        detached = cast(
            Mapping[str, object],
            json.loads(
                json.dumps(
                    response,
                    allow_nan=False,
                    ensure_ascii=True,
                    separators=(",", ":"),
                ),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            ),
        )
        if detached.get("model") != PRIMARY_MODEL_V22:
            raise ValueError("Provider response model differs")
        choice = _mapping_v22(
            _one_v22(detached.get("choices"), "Provider choices"),
            "Provider choice",
        )
        if choice.get("index") != 0:
            raise ValueError("Provider choice index differs")
        message = _mapping_v22(choice.get("message"), "Provider message")
        if message.get("role") != "assistant" or message.get("refusal") is not None:
            raise ValueError("Provider assistant message is invalid")
        if mode is ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT:
            if choice.get("finish_reason") != "stop" or "tool_calls" in message:
                raise ValueError("strict Provider choice metadata is invalid")
            raw_decision = message.get("content")
        else:
            if (
                choice.get("finish_reason") != "tool_calls"
                or message.get("content") is not None
            ):
                raise ValueError("local Provider choice metadata is invalid")
            tool_call = _mapping_v22(
                _one_v22(message.get("tool_calls"), "Provider tool calls"),
                "Provider tool call",
            )
            function = _mapping_v22(tool_call.get("function"), "Provider function")
            if (
                tool_call.get("type") != "function"
                or function.get("name") != _CONTROLLER_FUNCTION_V22
            ):
                raise ValueError("Provider function identity differs")
            raw_decision = function.get("arguments")
        raw_decision_sha256 = semantic_sha256_v22(
            {"raw_decision": raw_decision}
        )
        try:
            decision = _decision_from_json_v22(raw_decision)
        except ValueError:
            decision = None
            parse_error_code: Literal["INVALID_DECISION_SHAPE"] | None = (
                "INVALID_DECISION_SHAPE"
            )
        else:
            parse_error_code = None
        usage = _parse_usage(detached.get("usage"))
        if usage.output_tokens > self._max_completion_tokens:
            raise ValueError("Provider completion exceeds limit")
        turn_payload: dict[str, Any] = {
            "schema_version": "dta-v22.provider-controller-turn.v1",
            "model": PRIMARY_MODEL_V22,
            "mode": mode,
            "controller_identity_sha256": controller_identity_sha256,
            "prompt_sha256": prompt_sha256,
            "visible_input_sha256": visible_input_sha256,
            "provider_request_sha256": provider_request_sha256,
            "request_payload_sha256": semantic_sha256_v22(payload),
            "decision": decision,
            "parse_error_code": parse_error_code,
            "raw_decision_sha256": raw_decision_sha256,
            "raw_response_sha256": semantic_sha256_v22(detached),
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
            "monotonic_latency_ms": latency,
        }
        draft = ProviderControllerTurnV22.model_construct(
            **turn_payload,
            turn_sha256="0" * 64,
        )
        return ProviderControllerTurnV22.model_validate(
            {
                **turn_payload,
                "turn_sha256": semantic_sha256_v22(
                    draft.model_dump(mode="json", exclude={"turn_sha256"})
                ),
            },
            context=(
                None
                if request_context is None
                else {"request": request_context}
            ),
        )

    def _payload_v22(
        self,
        *,
        mode: ProviderOutputModeV22,
        system_prompt: str,
        visible_state: Mapping[str, object],
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": PRIMARY_MODEL_V22,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        visible_state,
                        allow_nan=False,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                },
            ],
            "temperature": 0.0,
            "n": 1,
            "max_completion_tokens": self._max_completion_tokens,
        }
        schema = _controller_schema_v22()
        if mode is ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": _CONTROLLER_SCHEMA_NAME_V22,
                    "strict": True,
                    "schema": schema,
                },
            }
        else:
            payload.update(
                {
                    "parallel_tool_calls": False,
                    "tool_choice": {
                        "type": "function",
                        "function": {"name": _CONTROLLER_FUNCTION_V22},
                    },
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": _CONTROLLER_FUNCTION_V22,
                                "description": "Submit exactly one controller decision.",
                                "strict": False,
                                "parameters": schema,
                            },
                        }
                    ],
                }
            )
        return payload


__all__ = (
    "ControllerProviderTransportV22",
    "OpenAICompatibleControllerProviderV22",
    "ProviderControllerTurnV22",
    "ProviderHttpErrorV22",
    "RouterProviderTurnRequestV22",
    "ProviderTurnRequestV22",
    "StdlibControllerProviderTransportV22",
    "build_provider_turn_request_v22",
    "build_router_provider_turn_request_v22",
)
