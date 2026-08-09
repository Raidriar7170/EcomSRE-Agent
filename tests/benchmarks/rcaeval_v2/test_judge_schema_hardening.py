from __future__ import annotations

from collections.abc import Mapping
import json

import pytest

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_rcaeval_v2.contracts import (
    BoundedEvidenceSnapshotV2,
    IncidentSnapshotV2,
    JudgeInputSnapshotV2,
)
from ecomsre_rcaeval_v2.provider import (
    OpenAICompatibleRCAEvalV2Provider,
    ProviderOutputValidationError,
    build_judge_request_payload,
)


def _judge_input() -> JudgeInputSnapshotV2:
    return JudgeInputSnapshotV2(
        incident=IncidentSnapshotV2(
            incident_id="re2-ob-case-0001",
            system="RE2-OB",
            anomaly_timestamp=1_000,
            modalities=("metrics", "logs", "traces"),
            summary="A service anomaly was detected around T0.",
        ),
        source_observations=(),
        bounded_evidence=(
            BoundedEvidenceSnapshotV2(
                evidence_ref="metric:0001",
                source="metrics",
                service="checkoutservice",
                observation="Checkoutservice CPU shifted.",
            ),
        ),
        specialist_assessments=(),
        commander_decision=None,
        indicator_candidates=(),
    )


class _ArgumentsTransport:
    def __init__(self, arguments: str, events: list[str] | None = None) -> None:
        self.arguments = arguments
        self.events = events

    def post_json(self, **_kwargs) -> Mapping[str, object]:
        if self.events is not None:
            self.events.append("PROVIDER_CALL")
        return {
            "model": "locked-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "submit_rcaeval_v2_service_decision",
                                    "arguments": self.arguments,
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }


def _provider(arguments: str, events: list[str] | None = None):
    return OpenAICompatibleRCAEvalV2Provider(
        config=OpenAICompatibleConfig(
            base_url="https://provider.example/v1",
            api_key="secret",
            model="locked-model",
        ),
        expected_model="locked-model",
        timeout_seconds=30.0,
        max_completion_tokens=2048,
        transport=_ArgumentsTransport(arguments, events),
    )


def _valid(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "root_cause_service": "checkoutservice",
        "model_proposed_indicator": "cpu",
        "confidence": 0.8,
        "evidence_refs": ["metric:0001"],
        "explanation": "The bounded inputs support checkoutservice.",
    }
    value.update(updates)
    return value


def test_judge_normalizes_service_and_deduplicates_evidence_preserving_order() -> None:
    provider = _provider(
        json.dumps(
            _valid(
                root_cause_service="  CHECKOUTSERVICE  ",
                evidence_refs=["metric:0001", "metric:0001"],
            )
        )
    )

    decision = provider.judge(
        _judge_input(), "single_v2", before_output_validation=lambda: None
    )

    assert decision.root_cause_service == "checkoutservice"
    assert decision.evidence_refs == ("metric:0001",)


@pytest.mark.parametrize(
    ("arguments", "constraint"),
    [
        (
            json.dumps(
                {key: value for key, value in _valid().items() if key != "confidence"}
            ),
            "missing",
        ),
        (json.dumps(_valid(unexpected="forbidden")), "extra_forbidden"),
        (
            json.dumps(_valid(root_cause_service="not/a/service")),
            "string_pattern_mismatch",
        ),
        (json.dumps(_valid(evidence_refs=["metric:9999"])), "visible_evidence_ref"),
        (json.dumps(_valid(evidence_refs=[])), "too_short"),
        (json.dumps(_valid(confidence=1.5)), "less_than_equal"),
        ("{malformed", "json_invalid"),
    ],
)
def test_judge_failures_emit_only_safe_generic_diagnostics(
    arguments: str, constraint: str
) -> None:
    events: list[str] = []
    provider = _provider(arguments, events)

    with pytest.raises(ProviderOutputValidationError) as captured:
        provider.judge(
            _judge_input(),
            "single_v2",
            before_output_validation=lambda: events.append("OUTPUT_VALIDATION"),
        )

    assert events == ["PROVIDER_CALL", "OUTPUT_VALIDATION"]
    diagnostics = captured.value.safe_validation_error
    assert constraint in diagnostics.constraint_types
    serialized = diagnostics.model_dump_json()
    assert arguments not in serialized
    assert "checkoutservice" not in serialized
    assert "metric:9999" not in serialized
    assert "input" not in serialized.casefold()
    assert "ctx" not in serialized.casefold()


def test_judge_prompt_states_exact_generic_contract_without_case_answer() -> None:
    payload = build_judge_request_payload(
        model="locked-model",
        judge_input=_judge_input(),
        architecture="single_v2",
        max_completion_tokens=2048,
    )
    prompt = payload["messages"][0]["content"]  # type: ignore[index]
    function = payload["tools"][0]["function"]  # type: ignore[index]

    assert "exactly one JudgeServiceDecisionV2" in prompt
    assert "Agent-visible service set" in prompt
    assert "non-empty" in prompt
    assert "no additional fields" in prompt
    assert function["strict"] is False
