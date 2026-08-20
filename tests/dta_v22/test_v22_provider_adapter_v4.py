from __future__ import annotations

import json
from typing import Mapping

import pytest

from ecomsre.dta_v2.v22.controller_modes import (
    PRIMARY_MODEL_V22,
    ProviderOutputModeV22,
)
from ecomsre.dta_v2.v22.controller_provider import ProviderHttpErrorV22
from ecomsre.dta_v2.v22.provider_boundary_v4 import (
    ProviderDecisionAliasV4,
    build_provider_probe_request_v4,
    materialize_protocol_requests_v4,
)
from ecomsre.dta_v2.v22.provider_protocol_v4 import (
    OpenAICompatibleProviderBoundaryV4,
    ProviderBoundaryFailureCodeV4,
    ProviderBoundaryProbeReportV4,
    ProviderBoundaryTurnV4,
    ProviderResponseProtocolErrorV4,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


class _Transport:
    def __init__(self, decision: ProviderDecisionAliasV4) -> None:
        self.decision = decision
        self.payloads: list[Mapping[str, object]] = []

    def post_json(self, **kwargs):
        self.payloads.append(kwargs["payload"])
        return {
            "model": PRIMARY_MODEL_V22,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "refusal": None,
                        "content": self.decision.model_dump_json(),
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 111,
                "completion_tokens": 12,
                "total_tokens": 123,
            },
        }


class _SequenceTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.payloads: list[Mapping[str, object]] = []

    def post_json(self, **kwargs):
        self.payloads.append(kwargs["payload"])
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _response(
    decision: ProviderDecisionAliasV4,
    *,
    mode: ProviderOutputModeV22,
    model: str = PRIMARY_MODEL_V22,
) -> dict[str, object]:
    if mode is ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT:
        finish_reason = "stop"
        message: dict[str, object] = {
            "role": "assistant",
            "refusal": None,
            "content": decision.model_dump_json(),
        }
    else:
        finish_reason = "tool_calls"
        message = {
            "role": "assistant",
            "refusal": None,
            "content": None,
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "submit_dta_v22_provider_alias_decision_v4",
                        "arguments": decision.model_dump_json(),
                    },
                }
            ],
        }
    return {
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": message,
            }
        ],
        "usage": {
            "prompt_tokens": 111,
            "completion_tokens": 12,
            "total_tokens": 123,
        },
    }


def _provider(decision: ProviderDecisionAliasV4, transport: _Transport):
    return OpenAICompatibleProviderBoundaryV4(
        config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="fixture-key",
            model=PRIMARY_MODEL_V22,
        ),
        timeout_seconds=60.0,
        max_completion_tokens=256,
        min_request_interval_seconds=12.0,
        transport=transport,
        throttle_monotonic_ns=lambda: 0,
        throttle_sleep=lambda _seconds: None,
    )


def test_provider_uses_exact_dynamic_schema_and_minimal_projection() -> None:
    request = materialize_protocol_requests_v4(replicate_id="A")[0].request
    decision = ProviderDecisionAliasV4(
        decision="READ",
        hypothesis_alias=request.alias_binding.hypotheses[0].alias,
        action_alias=next(
            item.alias for item in request.alias_binding.actions if item.available
        ),
        support_aliases=(),
        contradict_aliases=(),
    )
    transport = _Transport(decision)
    provider = _provider(decision, transport)
    turn = provider.complete(
        request=request,
        mode=ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT,
    )
    assert isinstance(turn, ProviderBoundaryTurnV4)
    assert turn.alias_decision == decision
    assert turn.canonical_decision is not None
    assert provider.attempted_calls == 1
    payload = transport.payloads[0]
    response_format = payload["response_format"]
    assert isinstance(response_format, Mapping)
    json_schema = response_format["json_schema"]
    assert isinstance(json_schema, Mapping)
    assert json_schema["schema"] == request.dynamic_schema
    messages = payload["messages"]
    assert isinstance(messages, list)
    message = messages[1]
    assert isinstance(message, Mapping)
    content = message["content"]
    assert isinstance(content, str)
    visible = json.loads(content)
    assert visible == request.visible_state()
    assert "controller_input" not in visible


def test_actual_alias_schema_probe_is_one_provider_call() -> None:
    request = build_provider_probe_request_v4()
    abstain = next(
        item.alias
        for item in request.alias_binding.hypotheses
        if item.canonical_id == "h:none:unresolved"
    )
    decision = ProviderDecisionAliasV4(
        decision="ABSTAIN",
        hypothesis_alias=abstain,
        action_alias="NONE",
        support_aliases=(),
        contradict_aliases=(),
    )
    transport = _Transport(decision)
    provider = _provider(decision, transport)
    report = provider.probe(request=request)
    assert report.supported is True
    assert report.provider_calls == 1
    assert provider.attempted_calls == 1
    assert report.schema_sha256 == request.schema_sha256


def test_unknown_alias_is_typed_and_never_constructs_canonical_decision() -> None:
    request = materialize_protocol_requests_v4(replicate_id="A")[0].request
    decision = ProviderDecisionAliasV4(
        decision="READ",
        hypothesis_alias="H99",
        action_alias=next(
            item.alias for item in request.alias_binding.actions if item.available
        ),
        support_aliases=(),
        contradict_aliases=(),
    )
    transport = _Transport(decision)
    turn = _provider(decision, transport).complete(
        request=request,
        mode=ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT,
    )
    assert turn.alias_decision == decision
    assert turn.canonical_decision is None
    assert turn.failure_code == "UNKNOWN_ALIAS"


def test_probe_falls_back_to_exact_local_schema_only_for_strict_unsupported() -> None:
    request = build_provider_probe_request_v4()
    abstain = next(
        item.alias
        for item in request.alias_binding.hypotheses
        if item.canonical_id == "h:none:unresolved"
    )
    decision = ProviderDecisionAliasV4(
        decision="ABSTAIN",
        hypothesis_alias=abstain,
        action_alias="NONE",
        support_aliases=(),
        contradict_aliases=(),
    )
    transport = _SequenceTransport(
        [
            ProviderHttpErrorV22(
                status=400,
                code="unsupported_value",
                error_type="invalid_request_error",
                param="response_format",
            ),
            _response(
                decision,
                mode=ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON,
            ),
        ]
    )
    provider = _provider(decision, transport)  # type: ignore[arg-type]
    report = provider.probe(request=request)
    assert isinstance(report, ProviderBoundaryProbeReportV4)
    assert report.supported is True
    assert report.selected_mode is ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON
    assert report.provider_calls == 2
    assert [attempt.status.value for attempt in report.attempts] == [
        "STRICT_SCHEMA_UNSUPPORTED",
        "SUPPORTED",
    ]


def test_local_mode_rejects_decision_outside_exact_dynamic_schema() -> None:
    spec = next(
        item
        for item in materialize_protocol_requests_v4(replicate_id="A")
        if item.protocol_intent == "READ"
    )
    request = spec.request
    decision = ProviderDecisionAliasV4(
        decision="COMMIT",
        hypothesis_alias=request.alias_binding.hypotheses[0].alias,
        action_alias="NONE",
        support_aliases=(),
        contradict_aliases=(),
    )
    transport = _SequenceTransport(
        [
            _response(
                decision,
                mode=ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON,
            )
        ]
    )
    turn = _provider(decision, transport).complete(  # type: ignore[arg-type]
        request=request,
        mode=ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON,
    )
    assert turn.alias_decision == decision
    assert turn.canonical_decision is None
    assert (
        turn.failure_code is ProviderBoundaryFailureCodeV4.LOCAL_DYNAMIC_SCHEMA_REJECTED
    )


def test_bounded_bad_provider_envelope_is_typed_response_protocol_failure() -> None:
    request = build_provider_probe_request_v4()
    decision = ProviderDecisionAliasV4(
        decision="ABSTAIN",
        hypothesis_alias=request.alias_binding.hypotheses[-1].alias,
        action_alias="NONE",
        support_aliases=(),
        contradict_aliases=(),
    )
    transport = _SequenceTransport(
        [
            _response(
                decision,
                mode=ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT,
                model="gpt-silent-successor",
            )
        ]
    )
    with pytest.raises(ProviderResponseProtocolErrorV4) as raised:
        _provider(decision, transport).complete(  # type: ignore[arg-type]
            request=request,
            mode=ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT,
        )
    assert raised.value.provider_request_sha256 == request.request_sha256
    assert raised.value.safe_failure_code == "RESPONSE_MODEL_MISMATCH"
    assert raised.value.raw_response_sha256 is not None
    assert raised.value.input_tokens is None
    assert raised.value.output_tokens is None
