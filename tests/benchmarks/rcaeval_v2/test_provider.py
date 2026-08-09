from __future__ import annotations

from collections.abc import Mapping
import json

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_rcaeval_v2.contracts import (
    BoundedEvidenceSnapshotV2,
    IncidentSnapshotV2,
    IndicatorCandidateSnapshotV2,
    JudgeInputSnapshotV2,
)
from ecomsre_rcaeval_v2.provider import (
    OpenAICompatibleRCAEvalV2Provider,
    UsageCapturingTransport,
)


class _SuccessfulTransport:
    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        del url, headers, payload, timeout_seconds
        return {
            "usage": {
                "prompt_tokens": 17,
                "completion_tokens": 5,
                "total_tokens": 22,
            },
            "opaque": "response-body-must-not-be-retained",
        }


class _FailingTransport:
    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        del url, headers, payload, timeout_seconds
        raise ConnectionError("synthetic transport failure")


def test_usage_transport_records_exact_delta_without_raw_response() -> None:
    transport = UsageCapturingTransport(_SuccessfulTransport())
    before = transport.snapshot()

    response = transport.post_json(
        url="https://provider.example/v1/chat/completions",
        headers={"Authorization": "Bearer secret"},
        payload={"model": "locked-model"},
        timeout_seconds=30.0,
    )
    delta = transport.delta_since(before)

    assert response["opaque"] == "response-body-must-not-be-retained"
    assert delta.provider_call_index == 1
    assert delta.usage.model_dump() == {
        "model_calls_delta": 1,
        "prompt_tokens_delta": 17,
        "completion_tokens_delta": 5,
        "total_tokens_delta": 22,
        "token_usage_known": True,
    }
    assert "response-body-must-not-be-retained" not in repr(transport)
    assert "secret" not in repr(transport)


def test_usage_transport_counts_failed_call_with_unknown_tokens() -> None:
    transport = UsageCapturingTransport(_FailingTransport())
    before = transport.snapshot()

    try:
        transport.post_json(
            url="https://provider.example/v1/chat/completions",
            headers={"Authorization": "Bearer secret"},
            payload={"model": "locked-model"},
            timeout_seconds=30.0,
        )
    except ConnectionError:
        pass
    delta = transport.delta_since(before)

    assert delta.provider_call_index == 1
    assert delta.usage.model_calls_delta == 1
    assert delta.usage.total_tokens_delta == 0
    assert delta.usage.token_usage_known is False


def test_usage_delta_without_transport_call_is_zero_and_has_no_index() -> None:
    transport = UsageCapturingTransport(_SuccessfulTransport())
    before = transport.snapshot()

    delta = transport.delta_since(before)

    assert delta.provider_call_index is None
    assert delta.usage.model_calls_delta == 0
    assert delta.usage.total_tokens_delta == 0


class _JudgeTransport:
    def post_json(self, **_kwargs):
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
                                    "arguments": json.dumps(
                                        {
                                            "root_cause_service": "checkoutservice",
                                            "model_proposed_indicator": "mem",
                                            "confidence": 0.8,
                                            "evidence_refs": ["metric:0001"],
                                            "explanation": "The bounded inputs support checkoutservice.",
                                        }
                                    ),
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


def test_judge_allows_service_visible_only_in_indicator_candidates() -> None:
    provider = OpenAICompatibleRCAEvalV2Provider(
        config=OpenAICompatibleConfig(
            base_url="https://provider.example/v1",
            api_key="secret",
            model="locked-model",
        ),
        expected_model="locked-model",
        timeout_seconds=30.0,
        max_completion_tokens=2048,
        transport=_JudgeTransport(),
    )
    judge_input = JudgeInputSnapshotV2(
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
                service="frontend",
                observation="Frontend latency shifted.",
            ),
        ),
        specialist_assessments=(),
        commander_decision=None,
        indicator_candidates=(
            IndicatorCandidateSnapshotV2(
                service="checkoutservice",
                canonical_indicator="mem",
                metric_name="checkoutservice_mem",
                score=4.0,
                evidence_ref="indicator:0001",
            ),
        ),
    )

    decision = provider.judge(judge_input, "single_v2")

    assert decision.root_cause_service == "checkoutservice"
