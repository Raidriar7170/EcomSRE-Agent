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
from ecomsre.dta_v2.v22.evidence_acquisition_v221 import (
    TerminalExplorationPolicyV221,
)
from ecomsre.dta_v2.v22.memory import (
    ChangeSalientPayloadV22,
    EvidenceRefV22,
    SalientFactV22,
    SignalStrengthV22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    ChangeCategoryV22,
    EvidenceSourceV22,
    RolloutStateV22,
)
from ecomsre.dta_v2.v22.simple_provider import (
    FUNCTION_NAME_V22,
    ProviderProtocolFailureV22,
    ProviderProtocolFailureV221,
    ProviderTransportErrorV22,
    SHARED_SYSTEM_PROMPT_V221,
    SimpleProviderV22,
    StdlibProviderTransportV22,
    build_provider_turn_request_v22,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


def _turn(
    arm: ControllerArmV22,
    *,
    salient_memory: object | None = None,
) -> ControllerTurnInputV22:
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
        salient_memory=(
            SimpleNamespace(evidence_refs=(), salient_facts=())
            if salient_memory is None
            else salient_memory
        ),
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


def test_v221_projection_adds_only_bounded_policy_state_for_both_arms() -> None:
    flat = build_provider_turn_request_v22(
        _turn(ControllerArmV22.FLAT_CANONICAL),
        terminal_exploration_policy=(
            TerminalExplorationPolicyV221.MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN
        ),
        adaptive_reads_so_far=0,
        policy_redirect_remaining=True,
    )
    planner = build_provider_turn_request_v22(
        _turn(ControllerArmV22.PLANNER_LITE),
        terminal_exploration_policy=(
            TerminalExplorationPolicyV221.MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN
        ),
        adaptive_reads_so_far=0,
        policy_redirect_remaining=True,
    )

    assert flat.visible_state["terminal_exploration_policy"] == (
        "MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN"
    )
    assert flat.visible_state["adaptive_reads_so_far"] == 0
    assert flat.visible_state["policy_redirect_remaining"] is True
    planner_without_ledger = dict(planner.visible_state)
    planner_without_ledger.pop("planner")
    assert planner_without_ledger == flat.visible_state
    assert "bootstrap_insufficient_expected" not in json.dumps(
        planner.visible_state, sort_keys=True
    )


def test_v221_prompt_file_matches_the_versioned_shared_prompt() -> None:
    prompt = Path("config/dta-v22-1/prompt.txt").read_text(encoding="utf-8").strip()

    assert prompt == SHARED_SYSTEM_PROMPT_V221


def test_policy_feedback_is_one_nonrepair_call_with_only_bounded_alias_state(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport([_tool_response()])
    provider = _provider(transport=transport, debug_root=tmp_path)

    result = provider.complete_policy_redirect_turn_v221(
        turn_input=_turn(ControllerArmV22.FLAT_CANONICAL),
        run_id="7" * 32,
        safe_error_code="PREMATURE_ABSTENTION",
        terminal_exploration_policy=(
            TerminalExplorationPolicyV221.MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN
        ),
        adaptive_reads_so_far=0,
        policy_redirect_remaining=False,
    )

    assert result.provider_calls == 1
    assert result.semantic_repair_used is False
    assert len(transport.calls) == 1
    payload = transport.calls[0]["payload"]
    assert isinstance(payload, dict)
    messages = payload["messages"]
    assert isinstance(messages, list)
    user = json.loads(messages[1]["content"])
    state = user["visible_state"]
    assert set(state) == {
        "safe_error_code",
        "current_hypothesis_aliases",
        "current_executable_action_aliases",
        "current_evidence_aliases",
        "remaining_evidence_budget",
        "instruction",
    }
    assert state["safe_error_code"] == "PREMATURE_ABSTENTION"
    assert state["remaining_evidence_budget"] == 3.0
    assert "bootstrap_insufficient_expected" not in json.dumps(state, sort_keys=True)


def test_policy_feedback_never_opens_a_nested_semantic_repair(tmp_path: Path) -> None:
    transport = RecordingTransport(
        [_tool_response(hypothesis="BAD"), _tool_response()]
    )
    provider = _provider(transport=transport, debug_root=tmp_path)

    with pytest.raises(ProviderProtocolFailureV22):
        provider.complete_policy_redirect_turn_v221(
            turn_input=_turn(ControllerArmV22.FLAT_CANONICAL),
            run_id="8" * 32,
            safe_error_code="PREMATURE_ABSTENTION",
            terminal_exploration_policy=(
                TerminalExplorationPolicyV221.MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN
            ),
            adaptive_reads_so_far=0,
            policy_redirect_remaining=False,
        )

    assert len(transport.calls) == 1


def test_policy_feedback_failure_preserves_call_token_and_latency_accounting(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport([_tool_response(hypothesis="BAD")])
    provider = _provider(transport=transport, debug_root=tmp_path)

    with pytest.raises(ProviderProtocolFailureV221) as captured:
        provider.complete_policy_redirect_turn_v221(
            turn_input=_turn(ControllerArmV22.FLAT_CANONICAL),
            run_id="9" * 32,
            safe_error_code="PREMATURE_ABSTENTION",
            terminal_exploration_policy=(
                TerminalExplorationPolicyV221.MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN
            ),
            adaptive_reads_so_far=0,
            policy_redirect_remaining=False,
        )

    assert captured.value.provider_calls == 1
    assert captured.value.input_tokens == 100
    assert captured.value.output_tokens == 20
    assert captured.value.total_tokens == 120
    assert captured.value.latency_ms >= 0


def test_change_fact_projection_removes_nested_revision_digest() -> None:
    revision_digest = "a" * 64
    evidence_ref = EvidenceRefV22.model_construct(
        schema_version="dta-v22.evidence-ref.v1",
        evidence_ref="e:a:changes:payment:0:111111111111",
        action_id="a:changes:payment",
        source=EvidenceSourceV22.CHANGES,
        outcome_sha256="2" * 64,
        record_index=0,
        record_sha256="1" * 64,
    )
    fact = SalientFactV22.model_construct(
        schema_version="dta-v22.salient-fact.v1",
        fact_id="f:changes:1111111111111111",
        source=EvidenceSourceV22.CHANGES,
        service="payment",
        evidence_refs=(evidence_ref.evidence_ref,),
        signal_strength=SignalStrengthV22.STRONG,
        payload=ChangeSalientPayloadV22(
            schema_version="dta-v22.salient-change.v1",
            category=ChangeCategoryV22.CONFIGURATION,
            relative_seconds=30,
            rollout_state=RolloutStateV22.COMPLETED,
            revision_digest=revision_digest,
        ),
        fact_sha256="3" * 64,
    )
    request = build_provider_turn_request_v22(
        _turn(
            ControllerArmV22.FLAT_CANONICAL,
            salient_memory=SimpleNamespace(
                evidence_refs=(evidence_ref,), salient_facts=(fact,)
            ),
        )
    )

    serialized = json.dumps(request.visible_state, sort_keys=True)
    assert revision_digest not in serialized
    assert "revision_digest" not in serialized
    assert "sha256" not in serialized.casefold()


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
    assert {item["role"] for item in repair["repair"]["allowed_hypotheses"]} == {
        "INCIDENT",
        "NO_INCIDENT",
        "UNRESOLVED",
    }
    debug = next(
        (tmp_path / ("a" * 32)).glob("provider-failure-*.json")
    ).read_text()
    assert "super-secret-provider-key" not in debug
    assert "Authorization" not in debug


def test_v221_semantic_repair_retains_only_bounded_policy_state(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport(
        [_tool_response(hypothesis="H99"), _tool_response()]
    )
    provider = _provider(transport=transport, debug_root=tmp_path)

    outcome = provider.complete_turn_v221(
        turn_input=_turn(ControllerArmV22.FLAT_CANONICAL),
        run_id="1" * 32,
        terminal_exploration_policy=(
            TerminalExplorationPolicyV221.MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN
        ),
        adaptive_reads_so_far=0,
        policy_redirect_remaining=True,
    )

    repair = json.loads(transport.calls[1]["payload"]["messages"][1]["content"])[
        "repair"
    ]
    assert outcome.semantic_repair_used is True
    assert repair["terminal_exploration_policy"] == (
        "MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN"
    )
    assert repair["adaptive_reads_so_far"] == 0
    assert repair["policy_redirect_remaining"] is True
    assert "bootstrap_insufficient_expected" not in json.dumps(repair, sort_keys=True)


def test_v221_controller_repair_retains_policy_state_and_accounting(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport([_tool_response()])
    provider = _provider(transport=transport, debug_root=tmp_path)

    outcome = provider.complete_repair_turn_v221(
        turn_input=_turn(ControllerArmV22.PLANNER_LITE),
        run_id="2" * 32,
        safe_error_code="UNKNOWN_E_ALIAS",
        terminal_exploration_policy=(
            TerminalExplorationPolicyV221.MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN
        ),
        adaptive_reads_so_far=1,
        policy_redirect_remaining=False,
    )

    repair = json.loads(transport.calls[0]["payload"]["messages"][1]["content"])[
        "repair"
    ]
    assert outcome.semantic_repair_used is True
    assert outcome.provider_calls == 1
    assert repair["adaptive_reads_so_far"] == 1
    assert repair["policy_redirect_remaining"] is False


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


def test_case_level_repair_budget_can_disable_a_later_automatic_repair(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport([_tool_response(hypothesis="H99")])
    provider = _provider(transport=transport, debug_root=tmp_path)

    with pytest.raises(ProviderProtocolFailureV22):
        provider.complete_turn(
            turn_input=_turn(ControllerArmV22.FLAT_CANONICAL),
            run_id="c" * 32,
            allow_semantic_repair=False,
        )

    assert len(transport.calls) == 1


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
    persisted_path = next(
        (tmp_path / ("d" * 32)).glob("provider-failure-*.json")
    )
    persisted = persisted_path.read_text()
    assert "super-secret-provider-key" not in persisted
    record = json.loads(persisted)
    assert record["http_status"] == 400
    assert record["raw_response_body"] == "bad"
    assert record["local_validation_error"] == "TRANSPORT_FAILURE"


def test_debug_record_omits_echoed_credentials_and_keeps_safe_metadata(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport(
        [
            ProviderTransportErrorV22(
                "HTTP_400",
                status_code=400,
                raw_body='{"Authorization":"Bearer stolen-credential"}',
            )
        ]
    )
    provider = _provider(transport=transport, debug_root=tmp_path)

    with pytest.raises(ProviderProtocolFailureV22):
        provider.complete_turn(
            turn_input=_turn(ControllerArmV22.FLAT_CANONICAL),
            run_id="e" * 32,
        )

    persisted = next(
        (tmp_path / ("e" * 32)).glob("provider-failure-*.json")
    ).read_text()
    assert "stolen-credential" not in persisted
    assert "Bearer" not in persisted
    assert "Authorization" not in persisted
    record = json.loads(persisted)
    assert record["http_status"] == 400
    assert record["raw_response_body"] is None
    assert record["safe_error_metadata"]["raw_response_body_omitted"] is True


def test_repair_transport_failure_gets_a_distinct_debug_record(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport(
        [
            _tool_response(hypothesis="H99"),
            ProviderTransportErrorV22(
                "HTTP_400", status_code=400, raw_body="repair rejected"
            ),
        ]
    )
    provider = _provider(transport=transport, debug_root=tmp_path)

    with pytest.raises(ProviderProtocolFailureV22) as captured:
        provider.complete_turn(
            turn_input=_turn(ControllerArmV22.PLANNER_LITE),
            run_id="f" * 32,
        )

    assert captured.value.safe_code == "TRANSPORT_FAILED"
    records = [
        json.loads(path.read_text())
        for path in sorted((tmp_path / ("f" * 32)).glob("provider-failure-*.json"))
    ]
    assert len(records) == 2
    assert {item["http_status"] for item in records} == {200, 400}
    assert {item["local_validation_error"] for item in records} == {
        "UNKNOWN_H_ALIAS",
        "REPAIR_TRANSPORT_FAILURE",
    }


def test_stdlib_transport_rejects_an_unbounded_success_body() -> None:
    class HugeResponse:
        status = 200

        def read(self, amount: int | None = None) -> bytes:
            assert amount == 65_537
            return b"x" * amount

    transport = StdlibProviderTransportV22(
        opener=lambda _request, timeout: HugeResponse()
    )

    with pytest.raises(ProviderTransportErrorV22) as captured:
        transport.post_json(
            url="https://provider.invalid/v1/chat/completions",
            headers={},
            payload={},
            timeout_seconds=1.0,
        )

    assert captured.value.safe_code == "HTTP_BODY_TOO_LARGE"
    assert captured.value.status_code == 200
