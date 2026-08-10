from __future__ import annotations

import json
from typing import Mapping

import pytest

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_rca100.contracts import RCA100InitialDiagnosis
from ecomsre_rca100.prompt import (
    OpenAICompatibleRCA100Provider,
    build_request_payload,
)
from ecomsre_rca100.projection import (
    RCA100AgentContext,
    RCA100AgentTask,
    RCA100BoundedEvidence,
    RCA100MetricsProjection,
    RCA100SourceProjection,
)
from ecomsre_rca100.contracts import CanonicalRCA100Entity


def _context() -> RCA100AgentContext:
    entity = CanonicalRCA100Entity(
        entity_ref="apm|apm.service|svc-a",
        domain="apm",
        type="apm.service",
        entity_id="svc-a",
        entity_name="service-a",
        normalized_name="service-a",
    )
    return RCA100AgentContext(
        task=RCA100AgentTask(
            opaque_case_id="rca100-case-0001",
            alert_title="Latency alert",
            prompt_text="Diagnose this incident.",
            window_start_timestamp=100.0,
            anchor_timestamp=110.0,
            window_end_timestamp=120.0,
            anchor_source="TASK_ALERT_TRIGGER",
            alert_entity_ref=entity.entity_ref,
        ),
        visible_entities=(entity,),
        metrics=RCA100MetricsProjection(
            status="METRICS_PROJECTION_UNAVAILABLE",
            total_rows=0,
            window_rows=0,
            mapped_rows=0,
            unmapped_rows=0,
            valid_series=0,
            ranked_entities=0,
        ),
        logs=RCA100SourceProjection(
            source="logs",
            status="AVAILABLE",
            evidence=(
                RCA100BoundedEvidence(
                    evidence_ref="log:0001",
                    entity_ref=entity.entity_ref,
                    name="log-pattern",
                    started_at=109.0,
                    ended_at=111.0,
                    score=2.0,
                    summary="count=2 pattern sample: timeout",
                ),
            ),
            total_rows=2,
            window_rows=2,
            mapped_rows=2,
            unmapped_rows=0,
        ),
        traces=RCA100SourceProjection(
            source="traces",
            status="SOURCE_UNAVAILABLE",
            reason="SOURCE_FILE_UNAVAILABLE",
            total_rows=0,
            window_rows=0,
            mapped_rows=0,
            unmapped_rows=0,
        ),
    )


class _Transport:
    def __init__(self, arguments: Mapping[str, object]) -> None:
        self.arguments = arguments
        self.payloads: list[Mapping[str, object]] = []

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        del url, headers, timeout_seconds
        self.payloads.append(payload)
        return {
            "model": "strong-single-snapshot",
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
                                    "name": "submit_rca100_initial_diagnosis",
                                    "arguments": json.dumps(self.arguments),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 25,
                "total_tokens": 125,
            },
        }


def _diagnosis() -> dict[str, object]:
    return {
        "root_cause_entity_ref": "apm|apm.service|svc-a",
        "fault_type": "latency",
        "confidence": 0.8,
        "evidence_refs": ["log:0001"],
        "reasoning_steps": [
            {
                "claim": "The timeout pattern supports this entity.",
                "entity_ref_or_none": "apm|apm.service|svc-a",
                "evidence_refs": ["log:0001"],
            }
        ],
        "summary": "The bounded evidence indicates a latency fault.",
    }


def test_payload_is_one_strict_direct_diagnosis_without_m3_action() -> None:
    payload = build_request_payload(
        model="strong-single-snapshot",
        context=_context(),
        max_completion_tokens=1600,
    )
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["temperature"] == 0.0
    assert payload["top_p"] == 1.0
    assert payload["parallel_tool_calls"] is False
    assert len(payload["tools"]) == 1  # type: ignore[arg-type]
    assert "OVERRIDE_METRICS_TOP1" not in encoded
    assert "root_cause_entities" not in encoded


def test_provider_accepts_only_visible_entity_and_evidence() -> None:
    transport = _Transport(_diagnosis())
    provider = OpenAICompatibleRCA100Provider(
        config=OpenAICompatibleConfig(
            base_url="https://provider.example/v1",
            api_key="secret",
            model="strong-single-snapshot",
        ),
        expected_model="strong-single-snapshot",
        timeout_seconds=90.0,
        max_completion_tokens=1600,
        transport=transport,
    )

    diagnosis = provider.diagnose(_context())

    assert isinstance(diagnosis, RCA100InitialDiagnosis)
    assert provider.calls == 1
    assert provider.last_usage_tokens == 125
    assert provider.usage_known is True


def test_provider_rejects_unseen_entity_without_semantic_retry() -> None:
    invalid = _diagnosis()
    invalid["root_cause_entity_ref"] = "apm|apm.service|unseen"
    provider = OpenAICompatibleRCA100Provider(
        config=OpenAICompatibleConfig(
            base_url="https://provider.example/v1",
            api_key="secret",
            model="strong-single-snapshot",
        ),
        expected_model="strong-single-snapshot",
        timeout_seconds=90.0,
        max_completion_tokens=1600,
        transport=_Transport(invalid),
    )

    with pytest.raises(ValueError, match="visible entity"):
        provider.diagnose(_context())

    assert provider.calls == 1
