from __future__ import annotations

from collections.abc import Mapping
import json
import pytest

from ecomsre.dta_v2.v22.controller_contracts import (
    ABSTAIN_HYPOTHESIS_ID_V22,
    NO_ACTION_ID_V22,
    ControllerDecisionKindV22,
    build_hypothesis_catalog_v22,
)
from ecomsre.dta_v2.v22.controller_modes import (
    PRIMARY_MODEL_V22,
    EvaluationArmV22,
    ProviderOutputModeV22,
    ProviderProbeStatusV22,
    build_controller_identity_manifests_v22,
    build_deterministic_router_final_input_v22,
    probe_provider_output_mode_v22,
)
from ecomsre.dta_v2.v22.controller_provider import (
    OpenAICompatibleControllerProviderV22,
    ProviderHttpErrorV22,
    ProviderTurnRequestV22,
    build_router_provider_turn_request_v22,
)
from ecomsre.dta_v2.v22.protocol_suite import (
    SyntheticTransitionCategoryV22,
    _actions_v22,
    _memory_v22,
    _setup_transition_v22,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.model.gateway import OpenAICompatibleConfig


def _decision() -> dict[str, object]:
    return {
        "decision": "ABSTAIN",
        "working_hypothesis_id": ABSTAIN_HYPOTHESIS_ID_V22,
        "action_id": NO_ACTION_ID_V22,
        "supporting_evidence_refs": [],
        "contradicting_evidence_refs": [],
    }


def _strict_response(*, api_key: str, model: str = PRIMARY_MODEL_V22) -> dict[str, object]:
    del api_key
    return {
        "id": "response-v22-strict",
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": (
                        '{"decision":"ABSTAIN","working_hypothesis_id":'
                        '"h:none:unresolved","action_id":"NONE",'
                        '"supporting_evidence_refs":[],"contradicting_evidence_refs":[]}'
                    ),
                    "refusal": None,
                },
            }
        ],
        "usage": {
            "prompt_tokens": 101,
            "completion_tokens": 17,
            "total_tokens": 118,
        },
    }


def _local_response(*, api_key: str) -> dict[str, object]:
    del api_key
    return {
        "id": "response-v22-local",
        "model": PRIMARY_MODEL_V22,
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
                            "id": "call-v22-local",
                            "type": "function",
                            "function": {
                                "name": "submit_dta_v22_controller_decision",
                                "arguments": (
                                    '{"decision":"ABSTAIN",'
                                    '"working_hypothesis_id":"h:none:unresolved",'
                                    '"action_id":"NONE",'
                                    '"supporting_evidence_refs":[],'
                                    '"contradicting_evidence_refs":[]}'
                                ),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 99,
            "completion_tokens": 19,
            "total_tokens": 118,
        },
    }


class RecordingTransport:
    def __init__(self, responses: list[Mapping[str, object] | Exception]) -> None:
        self.responses = list(responses)
        self.payloads: list[Mapping[str, object]] = []

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        assert url == "https://provider.example/v1/chat/completions"
        assert headers["Authorization"].startswith("Bearer ")
        assert timeout_seconds == 30.0
        self.payloads.append(payload)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _provider(
    *,
    transport: RecordingTransport,
    model: str = PRIMARY_MODEL_V22,
) -> OpenAICompatibleControllerProviderV22:
    return OpenAICompatibleControllerProviderV22(
        config=OpenAICompatibleConfig(
            base_url="https://provider.example/v1",
            api_key="private-provider-test-value",
            model=model,
        ),
        timeout_seconds=30.0,
        max_completion_tokens=256,
        transport=transport,
    )


def _probe(mode: ProviderOutputModeV22):
    return probe_provider_output_mode_v22(
        probe=lambda _model, candidate, _schema: (
            ProviderProbeStatusV22.SUPPORTED
            if candidate is mode
            else ProviderProbeStatusV22.STRICT_SCHEMA_UNSUPPORTED
        )
    )


def _request(mode: ProviderOutputModeV22) -> ProviderTurnRequestV22:
    return _setup_transition_v22(
        ordinal=25,
        category=SyntheticTransitionCategoryV22.VALID_ABSTAIN,
        probe=_probe(mode),
    ).request


def _router_request(mode: ProviderOutputModeV22):
    probe = _probe(mode)
    identity = next(
        item
        for item in build_controller_identity_manifests_v22(provider_probe=probe)
        if item.arm is EvaluationArmV22.DETERMINISTIC_ROUTER_SALIENT
    )
    initial = _actions_v22()
    exhausted = _actions_v22(
        executed=tuple(item.action_id for item in initial.registry_actions),
        remaining_budget=0.0,
    )
    memory, _ = _memory_v22(anomaly=True)
    router_input = build_deterministic_router_final_input_v22(
        action_catalog=exhausted,
        hypothesis_catalog=build_hypothesis_catalog_v22(
            candidate_services=("checkout", "payment")
        ),
        salient_memory=memory,
    )
    return build_router_provider_turn_request_v22(
        identity=identity,
        router_input=router_input,
    )


def test_strict_provider_turn_uses_exact_schema_and_retains_only_safe_evidence() -> None:
    transport = RecordingTransport(
        [_strict_response(api_key="private-provider-test-value")]
    )
    request = _request(ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT)
    turn = _provider(transport=transport).complete_controller_turn(
        request=request,
    )
    assert turn.decision is not None
    assert turn.decision.decision is ControllerDecisionKindV22.ABSTAIN
    assert turn.model == PRIMARY_MODEL_V22
    assert turn.mode is ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT
    assert turn.input_tokens == 101
    assert turn.output_tokens == 17
    assert turn.total_tokens == 118
    assert len(turn.raw_response_sha256) == 64
    assert "response-v22" not in turn.model_dump_json()
    assert turn.provider_request_sha256 == request.request_sha256
    assert turn.controller_identity_sha256 == request.identity.identity_sha256

    payload = transport.payloads[0]
    assert payload["model"] == PRIMARY_MODEL_V22
    response_format = payload["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert isinstance(json_schema, dict)
    assert json_schema["name"] == "dta_v22_controller_decision"
    assert json_schema["strict"] is True
    assert isinstance(json_schema["schema"], dict)
    assert set(json_schema["schema"]["properties"]) == {
        "decision",
        "working_hypothesis_id",
        "action_id",
        "supporting_evidence_refs",
        "contradicting_evidence_refs",
    }
    messages = payload["messages"]
    assert isinstance(messages, list)
    user_content = messages[1]["content"]
    assert isinstance(user_content, str)
    visible = json.loads(user_content)
    assert "required_decision" not in user_content
    assert "expected_decision" not in user_content
    assert visible["controller_input"]["input_sha256"] == (
        request.controller_input.input_sha256
    )


def test_local_fail_closed_mode_uses_same_schema_and_exact_tool_call() -> None:
    transport = RecordingTransport(
        [_local_response(api_key="private-provider-test-value")]
    )
    turn = _provider(transport=transport).complete_controller_turn(
        request=_request(ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON),
    )
    assert turn.decision is not None
    assert turn.decision.decision is ControllerDecisionKindV22.ABSTAIN
    payload = transport.payloads[0]
    tools = payload["tools"]
    assert isinstance(tools, list)
    function = tools[0]["function"]
    assert function["strict"] is False
    assert isinstance(function["parameters"], dict)
    assert set(function["parameters"]["properties"]) == {
        "decision",
        "working_hypothesis_id",
        "action_id",
        "supporting_evidence_refs",
        "contradicting_evidence_refs",
    }


def test_probe_falls_back_only_on_structured_unsupported_response() -> None:
    transport = RecordingTransport(
        [
            ProviderHttpErrorV22(
                status=400,
                code="unsupported_value",
                error_type="invalid_request_error",
                param="response_format",
            ),
            _local_response(api_key="private-provider-test-value"),
        ]
    )
    provider = _provider(transport=transport)
    report = probe_provider_output_mode_v22(probe=provider.probe_output_mode)
    assert report.selected_mode is ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON
    assert [item.status for item in report.attempts] == [
        ProviderProbeStatusV22.STRICT_SCHEMA_UNSUPPORTED,
        ProviderProbeStatusV22.SUPPORTED,
    ]

    ambiguous = _provider(
        transport=RecordingTransport(
            [
                ProviderHttpErrorV22(
                    status=503,
                    code="unavailable",
                    error_type="server_error",
                    param=None,
                )
            ]
        )
    )
    with pytest.raises(RuntimeError, match="BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"):
        probe_provider_output_mode_v22(probe=ambiguous.probe_output_mode)


def test_model_continuity_and_provider_envelope_fail_closed() -> None:
    with pytest.raises(RuntimeError, match="BLOCKED_DTA_V22_MODEL_CONTINUITY"):
        _provider(
            transport=RecordingTransport([]),
            model="gpt-silent-successor",
        )

    wrong_model = _provider(
        transport=RecordingTransport(
            [
                _strict_response(
                    api_key="private-provider-test-value",
                    model="gpt-silent-successor",
                )
            ]
        )
    )
    with pytest.raises(ValueError, match="model differs"):
        wrong_model.complete_controller_turn(
            request=_request(ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT),
        )

    leaked = _strict_response(api_key="private-provider-test-value")
    leaked["leak"] = "private-provider-test-value"
    provider = _provider(transport=RecordingTransport([leaked]))
    with pytest.raises(ValueError, match="credential material"):
        provider.complete_controller_turn(
            request=_request(ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT),
        )


def test_provider_rejects_untyped_and_rehashed_cross_arm_requests() -> None:
    provider = _provider(transport=RecordingTransport([]))
    with pytest.raises(TypeError, match="typed request"):
        provider.complete_controller_turn(request={})  # type: ignore[arg-type]

    request = _request(ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT)
    identities = build_controller_identity_manifests_v22(
        provider_probe=_probe(ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT)
    )
    other = next(
        item
        for item in identities
        if item.arm is EvaluationArmV22.PLANNER_LITE_SALIENT
    )
    payload = {
        "schema_version": request.schema_version,
        "execution_mode": request.execution_mode,
        "identity": other,
        "controller_input": request.controller_input,
        "plan_correction": request.plan_correction,
    }
    draft = ProviderTurnRequestV22.model_construct(
        **payload,
        request_sha256="0" * 64,
    )
    forged = ProviderTurnRequestV22.model_construct(
        **payload,
        request_sha256=semantic_sha256_v22(
            draft.model_dump(mode="json", exclude={"request_sha256"})
        ),
    )
    with pytest.raises(ValueError, match="identity and controller arm differ"):
        provider.complete_controller_turn(request=forged)


def test_router_final_turn_uses_exact_router_identity_and_same_model() -> None:
    request = _router_request(ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT)
    transport = RecordingTransport(
        [_strict_response(api_key="private-provider-test-value")]
    )
    turn = _provider(transport=transport).complete_router_final_turn(
        request=request
    )
    assert turn.decision is not None
    assert turn.decision.decision is ControllerDecisionKindV22.ABSTAIN
    assert turn.model == request.identity.model == PRIMARY_MODEL_V22
    assert turn.controller_identity_sha256 == request.identity.identity_sha256
    user_content = transport.payloads[0]["messages"][1]["content"]
    assert isinstance(user_content, str)
    assert "required_decision" not in user_content
    assert "expected_decision" not in user_content


def test_router_final_turn_rejects_another_read() -> None:
    response = _strict_response(api_key="private-provider-test-value")
    response["choices"][0]["message"]["content"] = (
        '{"decision":"READ","working_hypothesis_id":'
        '"h:payment:configuration-error","action_id":"a:logs:payment",'
        '"supporting_evidence_refs":[],"contradicting_evidence_refs":[]}'
    )
    with pytest.raises(ValueError, match="terminal decision"):
        _provider(transport=RecordingTransport([response])).complete_router_final_turn(
            request=_router_request(
                ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT
            )
        )


def test_provider_applies_fixed_start_interval_without_http_retry() -> None:
    clock_ns = [0]
    sleeps: list[float] = []

    def monotonic_ns() -> int:
        return clock_ns[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock_ns[0] += int(seconds * 1_000_000_000)

    transport = RecordingTransport(
        [
            _strict_response(api_key="private-provider-test-value"),
            _strict_response(api_key="private-provider-test-value"),
        ]
    )
    provider = OpenAICompatibleControllerProviderV22(
        config=OpenAICompatibleConfig(
            base_url="https://provider.example/v1",
            api_key="private-provider-test-value",
            model=PRIMARY_MODEL_V22,
        ),
        timeout_seconds=30.0,
        max_completion_tokens=256,
        min_request_interval_seconds=1.5,
        throttle_monotonic_ns=monotonic_ns,
        throttle_sleep=sleep,
        transport=transport,
    )
    request = _request(ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT)
    provider.complete_controller_turn(request=request)
    provider.complete_controller_turn(request=request)
    assert sleeps == [1.5]
    assert provider.attempted_calls == 2
