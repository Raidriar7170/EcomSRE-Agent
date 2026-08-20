from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.controller_contracts import (
    build_belief_ledger_view_v22,
    build_hypothesis_catalog_v22,
    initialize_belief_ledger_v22,
)
from ecomsre.dta_v2.v22.controller_inputs import (
    ControllerArmV22,
    ControllerRuntimeContextV22,
    ControllerTurnInputV22,
)
from ecomsre.dta_v2.v22.simple_provider import (
    FUNCTION_NAME_V22,
    ProviderProtocolFailureV22,
    ProviderTransportErrorV22,
    SimpleProviderV22,
    build_provider_turn_request_v22,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


def _turn(arm: ControllerArmV22) -> ControllerTurnInputV22:
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=("checkout", "payment")
    )
    topology = StaticTopologyV22.build(
        services=("checkout", "payment"),
        edges=(("checkout", "payment"),),
    )
    actions = build_action_catalog_v22(
        candidate_services=("checkout", "payment"),
        topology=topology,
        capability_registry=build_default_tool_capability_registry_v22(),
        executed_action_ids=(),
        remaining_budget=3.0,
    )
    view = build_belief_ledger_view_v22(
        ledger=initialize_belief_ledger_v22(catalog=hypotheses),
        hypothesis_catalog=hypotheses,
    )
    return ControllerTurnInputV22.model_construct(
        schema_version="dta-v22.controller-turn-input.v1",
        arm=arm,
        runtime_context=ControllerRuntimeContextV22.build(
            run_id="4" * 32,
            turn_ordinal=1,
            controller_identity_sha256="5" * 64,
            remaining_evidence_budget=3.0,
            remaining_provider_turns=5,
            correction_remaining=True,
        ),
        bootstrap=SimpleNamespace(candidate_services=("checkout", "payment")),
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        salient_memory=SimpleNamespace(evidence_refs=(), salient_facts=()),
        evidence_support_policy=None,
        belief_ledger_view=(view if arm is ControllerArmV22.PLANNER_LITE else None),
        input_sha256="6" * 64,
    )


def _tool_response(
    *,
    hypothesis: str = "H00",
    action: str = "A00",
    usage: tuple[int, int, int] = (100, 20, 120),
) -> dict[str, object]:
    arguments = {
        "decision": "READ",
        "hypothesis": hypothesis,
        "action": action,
        "support": [],
        "contradict": [],
    }
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": FUNCTION_NAME_V22,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ]
                }
            }
        ],
        "usage": {
            "prompt_tokens": usage[0],
            "completion_tokens": usage[1],
            "total_tokens": usage[2],
        },
    }


class RecordingTransport:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
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
        selected = self.outcomes.pop(0)
        if isinstance(selected, Exception):
            raise selected
        assert isinstance(selected, Mapping)
        return selected


def _provider(
    *,
    transport: RecordingTransport,
    debug_root: Path,
    sleeper: Any = lambda _seconds: None,
) -> SimpleProviderV22:
    return SimpleProviderV22(
        config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="super-secret-provider-key",
            model="configured-model",
        ),
        transport=transport,
        sleeper=sleeper,
        minimum_request_interval_seconds=0,
        debug_root=debug_root,
    )


def test_flat_and_planner_provider_state_differs_only_by_compact_ledger() -> None:
    flat = build_provider_turn_request_v22(_turn(ControllerArmV22.FLAT_CANONICAL))
    planner = build_provider_turn_request_v22(_turn(ControllerArmV22.PLANNER_LITE))

    assert flat.system_prompt == planner.system_prompt
    assert "planner" not in flat.visible_state
    planner_without_ledger = dict(planner.visible_state)
    ledger = planner_without_ledger.pop("planner")
    assert planner_without_ledger == flat.visible_state
    assert isinstance(ledger, dict)
    assert planner.serialized_visible_state_bytes <= 16_000
    serialized = json.dumps(planner.visible_state, sort_keys=True)
    for forbidden in (
        "run_id",
        "sha256",
        "query_parameters",
        "write_authority",
        "runbook",
        "path",
        "url",
        "evidence_support_policy",
    ):
        assert forbidden not in serialized.casefold()


def test_one_semantic_repair_uses_only_safe_alias_frontier(tmp_path: Path) -> None:
    transport = RecordingTransport(
        [_tool_response(hypothesis="H99"), _tool_response()]
    )
    provider = _provider(transport=transport, debug_root=tmp_path)

    outcome = provider.complete_turn(
        turn_input=_turn(ControllerArmV22.FLAT_CANONICAL),
        run_id="a" * 32,
    )

    assert outcome.first_pass_protocol_success is False
    assert outcome.post_repair_protocol_success is True
    assert outcome.semantic_repair_used is True
    assert outcome.provider_calls == 2
    assert outcome.input_tokens == 200
    assert outcome.output_tokens == 40
    repair_message = transport.calls[1]["payload"]["messages"][1]["content"]
    repair = json.loads(repair_message)
    assert set(repair) == {"repair"}
    assert set(repair["repair"]) == {
        "safe_error_code",
        "allowed_hypotheses",
        "allowed_actions",
        "allowed_evidence",
        "required_shape",
    }
    debug = (tmp_path / ("a" * 32) / "provider-failure.json").read_text()
    assert "super-secret-provider-key" not in debug
    assert "Authorization" not in debug


def test_second_semantic_failure_is_terminal_without_a_third_call(tmp_path: Path) -> None:
    transport = RecordingTransport(
        [_tool_response(hypothesis="H99"), _tool_response(hypothesis="H99")]
    )
    provider = _provider(transport=transport, debug_root=tmp_path)

    with pytest.raises(ProviderProtocolFailureV22) as captured:
        provider.complete_turn(
            turn_input=_turn(ControllerArmV22.PLANNER_LITE),
            run_id="b" * 32,
        )
    assert captured.value.safe_code == "PROTOCOL_FAILED"
    assert len(transport.calls) == 2


def test_only_retryable_transport_failures_use_the_fixed_two_retries(
    tmp_path: Path,
) -> None:
    sleeps: list[float] = []
    transport = RecordingTransport(
        [
            ProviderTransportErrorV22("HTTP_429", status_code=429),
            ProviderTransportErrorV22("HTTP_503", status_code=503),
            _tool_response(),
        ]
    )
    provider = _provider(
        transport=transport,
        debug_root=tmp_path,
        sleeper=sleeps.append,
    )

    outcome = provider.complete_turn(
        turn_input=_turn(ControllerArmV22.FLAT_CANONICAL),
        run_id="c" * 32,
    )

    assert outcome.transport_retry_count == 2
    assert sleeps == [10.0, 30.0]
    assert len(transport.calls) == 3
    first_payload = transport.calls[0]["payload"]
    assert all(item["payload"] == first_payload for item in transport.calls)
    assert "response_format" not in first_payload
    assert first_payload["parallel_tool_calls"] is False
    tool = first_payload["tools"][0]["function"]
    assert tool["strict"] is False


def test_nonretryable_http_error_is_safe_and_does_not_log_credentials(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport(
        [ProviderTransportErrorV22("HTTP_400", status_code=400, raw_body="bad")]
    )
    provider = _provider(transport=transport, debug_root=tmp_path)

    with pytest.raises(ProviderProtocolFailureV22) as captured:
        provider.complete_turn(
            turn_input=_turn(ControllerArmV22.FLAT_CANONICAL),
            run_id="d" * 32,
        )
    assert captured.value.safe_code == "TRANSPORT_FAILED"
    assert len(transport.calls) == 1
    persisted = (tmp_path / ("d" * 32) / "provider-failure.json").read_text()
    assert "super-secret-provider-key" not in persisted
