from __future__ import annotations

import json
import hashlib
from typing import Mapping

import pytest

from ecomsre.dta_v2.v22.controller_modes import PRIMARY_MODEL_V22, ProviderOutputModeV22
from ecomsre.dta_v2.v22.controller_provider import ProviderHttpErrorV22
from ecomsre.dta_v2.v22.provider_compatibility_v5 import (
    ProviderDecisionAliasV5,
    build_provider_probe_request_v5,
    materialize_protocol_requests_v5,
)
from ecomsre.dta_v2.v22.provider_protocol_v5 import (
    OpenAICompatibleProviderBoundaryV5,
    ProviderBoundaryFailureCodeV5,
    ProviderBoundaryProbeAttemptV5,
    ProviderBoundaryProbeReportV5,
    ProviderHttpFailureClassV5,
    ProviderResponseProtocolErrorV5,
    StdlibProviderBoundaryTransportV5,
    safe_provider_failure_v5,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.model.gateway import OpenAICompatibleConfig


class _SequenceTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.payloads: list[Mapping[str, object]] = []

    def post_json(self, **kwargs):
        self.payloads.append(kwargs["payload"])
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def _response(decision: ProviderDecisionAliasV5) -> dict[str, object]:
    return {
        "model": PRIMARY_MODEL_V22,
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "refusal": None,
                    "content": None,
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "submit_dta_v22_provider_alias_decision_v5",
                                "arguments": decision.model_dump_json(),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 111,
            "completion_tokens": 12,
            "total_tokens": 123,
        },
    }


def _provider(transport: _SequenceTransport) -> OpenAICompatibleProviderBoundaryV5:
    return OpenAICompatibleProviderBoundaryV5(
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


def test_payload_is_one_static_local_fail_closed_function_call() -> None:
    request = materialize_protocol_requests_v5(replicate_id="A")[0].request
    decision = ProviderDecisionAliasV5(
        decision="READ",
        hypothesis_alias=request.alias_binding.hypotheses[0].alias,
        action_alias=next(item.alias for item in request.alias_binding.actions if item.available),
        support_aliases=(),
        contradict_aliases=(),
    )
    transport = _SequenceTransport([_response(decision)])
    turn = _provider(transport).complete(request=request)
    assert turn.mode is ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON
    assert turn.canonical_decision is not None
    payload = transport.payloads[0]
    assert "response_format" not in payload
    assert payload["parallel_tool_calls"] is False
    assert payload["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_dta_v22_provider_alias_decision_v5"},
    }
    tools = payload["tools"]
    assert isinstance(tools, list) and len(tools) == 1
    function = tools[0]["function"]
    assert function["strict"] is False
    assert function["parameters"] == request.static_schema


def test_probe_is_exactly_one_local_mode_call_and_requires_abstain_sentinel() -> None:
    request = build_provider_probe_request_v5()
    abstain = next(
        item.alias
        for item in request.alias_binding.hypotheses
        if item.canonical_id == "h:none:unresolved"
    )
    decision = ProviderDecisionAliasV5(
        decision="ABSTAIN",
        hypothesis_alias=abstain,
        action_alias="NONE",
        support_aliases=(),
        contradict_aliases=(),
    )
    provider = _provider(_SequenceTransport([_response(decision)]))
    report = provider.probe(request=request)
    assert report.supported is True
    assert report.provider_calls == 1
    assert report.selected_mode is ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON
    assert provider.attempted_calls == 1


def test_probe_report_rejects_rehashed_supported_non_abstain_turn() -> None:
    probe_request = build_provider_probe_request_v5()
    abstain = next(
        item.alias
        for item in probe_request.alias_binding.hypotheses
        if item.canonical_id == "h:none:unresolved"
    )
    probe_decision = ProviderDecisionAliasV5(
        decision="ABSTAIN",
        hypothesis_alias=abstain,
        action_alias="NONE",
        support_aliases=(),
        contradict_aliases=(),
    )
    report = _provider(_SequenceTransport([_response(probe_decision)])).probe(
        request=probe_request
    )

    read_request = next(
        item.request
        for item in materialize_protocol_requests_v5(replicate_id="A")
        if item.protocol_intent == "READ"
    )
    read_decision = ProviderDecisionAliasV5(
        decision="READ",
        hypothesis_alias=read_request.alias_binding.hypotheses[0].alias,
        action_alias=next(
            item.alias for item in read_request.alias_binding.actions if item.available
        ),
        support_aliases=(),
        contradict_aliases=(),
    )
    read_turn = _provider(_SequenceTransport([_response(read_decision)])).complete(
        request=read_request
    )
    attempt_payload = report.attempts[0].model_dump(
        mode="python", exclude={"attempt_sha256"}
    )
    attempt_payload.update(
        {
            "provider_request_sha256": read_request.request_sha256,
            "turn_sha256": read_turn.turn_sha256,
        }
    )
    attempt = ProviderBoundaryProbeAttemptV5.model_validate(
        {
            **attempt_payload,
            "attempt_sha256": semantic_sha256_v22(attempt_payload),
        }
    )
    report_payload = report.model_dump(mode="json", exclude={"report_sha256"})
    report_payload.update(
        {
            "provider_request_sha256": read_request.request_sha256,
            "static_schema_sha256": read_request.static_schema_sha256,
            "prompt_sha256": read_turn.prompt_sha256,
            "attempts": (attempt.model_dump(mode="json"),),
            "turn": read_turn.model_dump(mode="json"),
        }
    )
    with pytest.raises(ValueError, match="probe"):
        ProviderBoundaryProbeReportV5.model_validate_json(
            json.dumps(
                {
                    **report_payload,
                    "report_sha256": semantic_sha256_v22(report_payload),
                }
            )
        )


def test_http_4xx_is_safe_request_rejection_not_generic_transport_abort() -> None:
    request = build_provider_probe_request_v5()
    error = ProviderHttpErrorV22(
        status=400,
        code="invalid_parameter",
        error_type="invalid_request_error",
        param="tools.0.function.parameters",
    )
    failure = safe_provider_failure_v5(
        error=error,
        failure_stage="PROBE",
        request_payload_sha256=request.request_sha256,
    )
    assert failure.failure_class is ProviderHttpFailureClassV5.PROVIDER_REQUEST_REJECTED
    assert failure.status == 400
    assert failure.safe_code == "invalid_parameter"
    assert failure.safe_type == "invalid_request_error"
    assert failure.safe_param == "tools.0.function.parameters"
    encoded = failure.model_dump_json()
    assert "message" not in encoded
    assert "base_url" not in encoded
    assert "/Users/" not in encoded


def test_provider_controlled_http_atoms_are_dropped_instead_of_breaking_persistence() -> None:
    failure = safe_provider_failure_v5(
        error=ProviderHttpErrorV22(
            status=400,
            code="invalid value /Users/alice/private.txt",
            error_type="invalid request with spaces",
            param="Bearer secret-value",
        ),
        failure_stage="PROBE",
        request_payload_sha256="f" * 64,
    )
    assert failure.failure_class is ProviderHttpFailureClassV5.PROVIDER_REQUEST_REJECTED
    assert failure.status == 400
    assert failure.safe_code is None
    assert failure.safe_type is None
    assert failure.safe_param is None


def test_non_object_provider_envelope_is_a_typed_persistable_protocol_failure() -> None:
    request = build_provider_probe_request_v5()
    with pytest.raises(ProviderResponseProtocolErrorV5) as raised:
        _provider(_SequenceTransport([["not", "an", "object"]])).complete(
            request=request
        )
    assert raised.value.safe_failure_code == "RESPONSE_ENVELOPE_INVALID"
    assert len(raised.value.raw_response_sha256) == 64


def test_stdlib_malformed_2xx_body_preserves_raw_hash_as_response_protocol_failure() -> None:
    content = b"{malformed-json"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit: int) -> bytes:
            return content

    class Opener:
        def open(self, *_args, **_kwargs):
            return Response()

    provider = OpenAICompatibleProviderBoundaryV5(
        config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="fixture-key",
            model=PRIMARY_MODEL_V22,
        ),
        timeout_seconds=60.0,
        max_completion_tokens=256,
        min_request_interval_seconds=12.0,
        transport=StdlibProviderBoundaryTransportV5(opener=Opener()),
        throttle_monotonic_ns=lambda: 0,
        throttle_sleep=lambda _seconds: None,
    )
    with pytest.raises(ProviderResponseProtocolErrorV5) as raised:
        provider.complete(request=build_provider_probe_request_v5())
    assert raised.value.safe_failure_code == "RESPONSE_ENVELOPE_INVALID"
    assert raised.value.raw_response_sha256 == hashlib.sha256(content).hexdigest()


def test_usage_failure_preserves_completed_alias_processing_stage() -> None:
    request = next(
        item.request
        for item in materialize_protocol_requests_v5(replicate_id="A")
        if item.protocol_intent == "READ"
        and item.transition_kind == "ORDINARY"
    )
    decision = ProviderDecisionAliasV5(
        decision="READ",
        hypothesis_alias=request.alias_binding.hypotheses[0].alias,
        action_alias=next(
            item.alias for item in request.alias_binding.actions if item.available
        ),
        support_aliases=(),
        contradict_aliases=(),
    )
    response = _response(decision)
    response["usage"] = {
        "prompt_tokens": "not-an-integer",
        "completion_tokens": 12,
        "total_tokens": 12,
    }
    with pytest.raises(ProviderResponseProtocolErrorV5) as raised:
        _provider(_SequenceTransport([response])).complete(request=request)
    error = raised.value
    assert error.safe_failure_code == "RESPONSE_USAGE_INVALID"
    assert error.parsed_alias is True
    assert error.alias_resolved is True
    assert error.intent_conformant is True
    assert error.raw_alias_decision_sha256 is not None
    assert error.resolved_canonical_decision_sha256 is not None
    assert error.alias_binding_sha256 == request.alias_binding.binding_sha256


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (
            ProviderHttpErrorV22(status=429, code=None, error_type=None, param=None),
            ProviderHttpFailureClassV5.PROVIDER_RATE_LIMITED,
        ),
        (
            ProviderHttpErrorV22(status=503, code=None, error_type=None, param=None),
            ProviderHttpFailureClassV5.PROVIDER_SERVER_ERROR,
        ),
        (TimeoutError(), ProviderHttpFailureClassV5.PROVIDER_TIMEOUT),
        (ConnectionError(), ProviderHttpFailureClassV5.PROVIDER_CONNECTION_ERROR),
    ),
)
def test_safe_failure_taxonomy_is_mutually_exclusive(
    error: BaseException,
    expected: ProviderHttpFailureClassV5,
) -> None:
    failure = safe_provider_failure_v5(
        error=error,
        failure_stage="TRANSITION",
        request_payload_sha256="f" * 64,
    )
    assert failure.failure_class is expected


def test_safe_http_failure_class_is_bound_to_status_range() -> None:
    valid = safe_provider_failure_v5(
        error=ProviderHttpErrorV22(
            status=500,
            code=None,
            error_type=None,
            param=None,
        ),
        failure_stage="TRANSITION",
        request_payload_sha256="f" * 64,
    )
    forged = valid.model_dump(mode="python", exclude={"failure_sha256"})
    forged["failure_class"] = ProviderHttpFailureClassV5.PROVIDER_REQUEST_REJECTED
    with pytest.raises(ValueError, match="status"):
        type(valid).model_validate(
            {**forged, "failure_sha256": semantic_sha256_v22(forged)}
        )


@pytest.mark.parametrize("status", (302, 307))
def test_rejected_redirect_is_a_typed_response_protocol_http_failure(
    status: int,
) -> None:
    failure = safe_provider_failure_v5(
        error=ProviderHttpErrorV22(
            status=status,
            code=None,
            error_type=None,
            param=None,
        ),
        failure_stage="TRANSITION",
        request_payload_sha256="f" * 64,
    )
    assert (
        failure.failure_class
        is ProviderHttpFailureClassV5.PROVIDER_RESPONSE_PROTOCOL_FAILURE
    )
    assert failure.status == status
    assert failure.safe_code == "HTTP_REDIRECT_REJECTED"


def test_unknown_alias_never_constructs_or_dispatches_a_canonical_decision() -> None:
    request = materialize_protocol_requests_v5(replicate_id="A")[0].request
    decision = ProviderDecisionAliasV5(
        decision="READ",
        hypothesis_alias="H99",
        action_alias=next(item.alias for item in request.alias_binding.actions if item.available),
        support_aliases=(),
        contradict_aliases=(),
    )
    turn = _provider(_SequenceTransport([_response(decision)])).complete(request=request)
    assert turn.alias_decision == decision
    assert turn.canonical_decision is None
    assert turn.failure_code is ProviderBoundaryFailureCodeV5.UNKNOWN_ALIAS
