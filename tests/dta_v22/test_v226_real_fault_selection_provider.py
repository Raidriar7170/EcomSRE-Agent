from __future__ import annotations

from collections.abc import Mapping
import json

import pytest

from ecomsre.dta_v2.v22.real_fault_selection_provider_v226 import (
    REAL_FAULT_SELECTION_FUNCTION_V226,
    RealFaultSelectionProviderAdapterV226,
    RealFaultSelectionProtocolFailureV226,
)
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22
from ecomsre.dta_v2.v22.real_fault_selection_v226 import (
    RealFaultSelectionRequestV226,
    RealFaultVisibleActionV226,
    RealFaultVisibleFocusV226,
    RealFaultVisibleTerminalV226,
)
from ecomsre.dta_v2.v22.simple_provider import ProviderTransportErrorV22
from ecomsre.model.gateway import OpenAICompatibleConfig


class _RecordingTransport:
    def __init__(self, outcomes: list[Mapping[str, object] | Exception]) -> None:
        self.outcomes = outcomes
        self.payloads: list[Mapping[str, object]] = []

    def post_json(self, *, url, headers, payload, timeout_seconds):
        del url, headers, timeout_seconds
        self.payloads.append(payload)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _response(selection: str, focus: str) -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": REAL_FAULT_SELECTION_FUNCTION_V226,
                                "arguments": json.dumps(
                                    {"selection": selection, "focus": focus}
                                ),
                            }
                        }
                    ]
                }
            }
        ],
        "usage": {
            "prompt_tokens": 21,
            "completion_tokens": 4,
            "total_tokens": 25,
        },
    }


def _request(*, terminal_only: bool = False) -> RealFaultSelectionRequestV226:
    return RealFaultSelectionRequestV226(
        schema_version="dta-v226-real-fault.selection-request.v1",
        output_shape='{"selection":"A00 or T00","focus":"H00 or NONE"}',
        actions=()
        if terminal_only
        else (
            RealFaultVisibleActionV226(
                alias="A00",
                source=EvidenceSourceV22.RESOURCES,
                target_aliases=("svc-0000000001", "svc-0000000002"),
                weighted_cost=1.0,
            ),
        ),
        terminals=(
            RealFaultVisibleTerminalV226(
                alias="T00",
                terminal_kind="NO_INCIDENT",
                root_service_alias=None,
                mechanism=None,
            ),
        )
        if terminal_only
        else (),
        focuses=(
            RealFaultVisibleFocusV226(
                alias="H00",
                target_alias="svc-0000000001",
                mechanism="CPU_SATURATION",
            ),
        ),
        remaining_semantic_actions=0 if terminal_only else 4,
        remaining_target_equivalent_reads=0 if terminal_only else 4,
    )


def _provider(transport, sleeps: list[float] | None = None):
    return RealFaultSelectionProviderAdapterV226(
        config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="test-secret",
            model="test-model",
        ),
        transport=transport,
        sleeper=(lambda _: None) if sleeps is None else sleeps.append,
        minimum_request_interval_seconds=0,
    )


def test_v226_selection_provider_forces_exact_opaque_shape() -> None:
    transport = _RecordingTransport([_response("A00", "H00")])

    outcome = _provider(transport).complete_selection(
        request=_request(), run_id="1" * 32
    )

    assert outcome.decision.selection == "A00"
    assert outcome.decision.focus == "H00"
    assert outcome.provider_calls == 1
    assert outcome.protocol_repairs == 0
    payload = transport.payloads[0]
    assert payload["temperature"] == 0
    assert payload["parallel_tool_calls"] is False
    assert payload["tool_choice"] == {
        "type": "function",
        "function": {"name": REAL_FAULT_SELECTION_FUNCTION_V226},
    }
    rendered = payload["messages"][1]["content"].casefold()
    assert "recommendation" not in rendered
    assert '"ad"' not in rendered
    assert "query" not in rendered
    assert "evidence_ref" not in rendered


def test_v226_selection_provider_repairs_only_protocol_errors() -> None:
    repaired = _RecordingTransport(
        [_response("BAD", "H00"), _response("A00", "H00")]
    )

    outcome = _provider(repaired).complete_selection(
        request=_request(), run_id="2" * 32
    )

    assert outcome.provider_calls == 2
    assert outcome.protocol_repairs == 1
    assert outcome.first_pass_protocol_success is False
    repair_user = json.loads(repaired.payloads[1]["messages"][1]["content"])
    assert repair_user["repair"]["safe_error_code"] == "UNKNOWN_ALIAS_KIND"
    assert set(repair_user["repair"]) == {
        "safe_error_code",
        "valid_A_aliases",
        "valid_T_aliases",
        "valid_H_aliases",
        "required_shape",
    }

    valid_but_semantically_weak = _RecordingTransport([_response("T00", "NONE")])
    weak = _provider(valid_but_semantically_weak).complete_selection(
        request=_request(terminal_only=True), run_id="3" * 32
    )
    assert weak.provider_calls == 1
    assert weak.protocol_repairs == 0


def test_v226_selection_provider_bounds_repairs_and_exact_transport_retries() -> None:
    exhausted = _RecordingTransport([_response("BAD", "H00")] * 3)
    with pytest.raises(RealFaultSelectionProtocolFailureV226) as captured:
        _provider(exhausted).complete_selection(
            request=_request(), run_id="4" * 32
        )
    assert captured.value.provider_calls == 3
    assert captured.value.protocol_repairs == 2
    assert captured.value.safe_code == "UNKNOWN_ALIAS_KIND"

    sleeps: list[float] = []
    retried = _RecordingTransport(
        [
            ProviderTransportErrorV22("HTTP_429", status_code=429),
            ProviderTransportErrorV22("HTTP_503", status_code=503),
            ProviderTransportErrorV22("CONNECTION_ERROR"),
            _response("A00", "H00"),
        ]
    )
    outcome = _provider(retried, sleeps).complete_selection(
        request=_request(), run_id="5" * 32
    )
    assert outcome.transport_retry_count == 3
    assert sleeps == [5.0, 15.0, 30.0]
    assert all(payload == retried.payloads[0] for payload in retried.payloads)


def test_v226_selection_provider_rejects_surface_mismatches() -> None:
    wrong_focus = _RecordingTransport([_response("A00", "NONE")] * 3)
    with pytest.raises(RealFaultSelectionProtocolFailureV226) as captured:
        _provider(wrong_focus).complete_selection(
            request=_request(), run_id="6" * 32
        )
    assert captured.value.safe_code == "SELECTION_FOCUS_MISMATCH"
