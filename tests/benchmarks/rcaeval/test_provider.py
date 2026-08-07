from __future__ import annotations

from datetime import datetime, timezone
import json

from ecomsre_rcaeval.adapter import (
    ArchitectureContext,
    IncidentManifest,
    SourceObservation,
)
from ecomsre_rcaeval.contracts import Architecture
from ecomsre_rcaeval.provider import OpenAICompatibleRCAEvalProvider
from ecomsre_rcaeval.tools import (
    SourceStatus,
    ToolEvidence,
)
from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre.phase1.contracts import EvidenceSource
from ecomsre.phase1.evidence import EvidenceStore


class FakeTransport:
    def __init__(self, evidence_ref: str) -> None:
        self.calls = 0
        self.evidence_ref = evidence_ref

    def post_json(self, *, url, headers, payload, timeout_seconds):
        self.calls += 1
        assert url == "https://provider.invalid/v1/chat/completions"
        assert headers["Authorization"] == "Bearer secret"
        assert payload["temperature"] == 0.0
        assert payload["top_p"] == 1.0
        assert payload["n"] == 1
        assert payload["parallel_tool_calls"] is False
        assert timeout_seconds == 30.0
        return {
            "id": "response-1",
            "model": "frozen-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "submit_rcaeval_diagnosis",
                                    "arguments": json.dumps(
                                        {
                                            "schema_version": "rcaeval-re2.diagnosis.v1",
                                            "root_cause_service": " CHECKOUTSERVICE ",
                                            "root_cause_indicator": "cpu",
                                            "confidence": None,
                                            "evidence_refs": [self.evidence_ref],
                                            "explanation": "CPU is the strongest anomaly.",
                                        }
                                    ),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        }


class WorkflowTransport:
    def __init__(self) -> None:
        self.calls = 0

    def post_json(self, *, url, headers, payload, timeout_seconds):
        del url, headers, timeout_seconds
        self.calls += 1
        function_name = payload["tool_choice"]["function"]["name"]
        arguments = {
            "submit_rcaeval_specialist_assessment": {
                "schema_version": "rcaeval-re2.specialist-assessment.v1",
                "source": "metrics",
                "observation_status": "AVAILABLE",
                "candidate_service": "checkoutservice",
                "candidate_indicator": "cpu",
                "confidence": 0.8,
                "evidence_refs": ["metric:0001"],
                "summary": "Metrics isolate checkout CPU.",
            },
            "submit_rcaeval_commander_decision": {
                "schema_version": "rcaeval-re2.commander-decision.v1",
                "selected_sources": ["logs"],
                "rationale": "Inspect logs for corroboration.",
            },
            "submit_rcaeval_diagnosis": {
                "schema_version": "rcaeval-re2.diagnosis.v1",
                "root_cause_service": "checkoutservice",
                "root_cause_indicator": "cpu",
                "confidence": 0.9,
                "evidence_refs": ["metric:0001"],
                "explanation": "Metrics identify checkout CPU.",
            },
        }[function_name]
        return {
            "id": f"response-{self.calls}",
            "model": "frozen-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"call-{self.calls}",
                                "type": "function",
                                "function": {
                                    "name": function_name,
                                    "arguments": json.dumps(arguments),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        }


def test_provider_uses_one_frozen_tool_call_and_validates_evidence() -> None:
    evidence_store = EvidenceStore("2" * 32)
    canonical = evidence_store.add(
        source=EvidenceSource.METRICS,
        observation_type="rcaeval_bounded_observation",
        attributes={"external_evidence_id": "metric:0001"},
        raw_artifact_ref="metrics.json#1",
        raw_artifact_sha256="a" * 64,
        limitations=("Synthetic test projection.",),
        summary="Strong bounded CPU anomaly.",
        started_at=datetime.fromtimestamp(90, tz=timezone.utc),
        ended_at=datetime.fromtimestamp(110, tz=timezone.utc),
        service="checkoutservice",
    )
    transport = FakeTransport(canonical.evidence_ref)
    provider = OpenAICompatibleRCAEvalProvider(
        config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="secret",
            model="frozen-model",
        ),
        expected_model="frozen-model",
        timeout_seconds=30.0,
        max_completion_tokens=1_024,
        transport=transport,
    )
    incident = IncidentManifest(
        case_id="ob-case-0001",
        system="RE2-OB",
        anomaly_timestamp=100,
        modalities=("metrics", "logs", "traces"),
    )
    context = ArchitectureContext(
        context_id="1" * 32,
        run_id="2" * 32,
        case_id=incident.case_id,
        architecture=Architecture.DYNAMIC,
        evidence=(
            ToolEvidence(
                evidence_id="metric:0001",
                service="checkoutservice",
                name="checkoutservice_cpu",
                started_at=90.0,
                ended_at=110.0,
                summary="Strong bounded CPU anomaly.",
            ),
        ),
        canonical_evidence=(canonical,),
        specialist_assessments=(),
        source_observations=(
            SourceObservation(
                source="metrics",
                status=SourceStatus.AVAILABLE,
            ),
        ),
        investigated_sources=("metrics",),
        commander_stages=(),
        tool_call_count=1,
        targeted_refinement_used=True,
    )

    diagnosis = provider.diagnose(incident, context, Architecture.DYNAMIC)

    assert diagnosis.root_cause_service == "checkoutservice"
    assert diagnosis.evidence_refs == ("metric:0001",)
    assert provider.calls == 1
    assert provider.last_usage_tokens == 120
    assert transport.calls == 1


def test_provider_executes_specialist_commander_and_judge_as_real_calls() -> None:
    evidence_store = EvidenceStore("2" * 32)
    canonical = evidence_store.add(
        source=EvidenceSource.METRICS,
        observation_type="rcaeval_bounded_observation",
        attributes={"external_evidence_id": "metric:0001"},
        raw_artifact_ref="metrics.json#1",
        raw_artifact_sha256="a" * 64,
        limitations=("Synthetic test projection.",),
        summary="Strong bounded CPU anomaly.",
        started_at=datetime.fromtimestamp(90, tz=timezone.utc),
        ended_at=datetime.fromtimestamp(110, tz=timezone.utc),
        service="checkoutservice",
    )
    transport = WorkflowTransport()
    provider = OpenAICompatibleRCAEvalProvider(
        config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="secret",
            model="frozen-model",
        ),
        expected_model="frozen-model",
        timeout_seconds=30.0,
        max_completion_tokens=1_024,
        transport=transport,
    )
    incident = IncidentManifest(
        case_id="ob-case-0001",
        system="RE2-OB",
        anomaly_timestamp=100,
        modalities=("metrics", "logs", "traces"),
    )
    context = ArchitectureContext(
        context_id="1" * 32,
        run_id="2" * 32,
        case_id=incident.case_id,
        architecture=Architecture.DYNAMIC,
        evidence=(
            ToolEvidence(
                evidence_id="metric:0001",
                service="checkoutservice",
                name="checkoutservice_cpu",
                started_at=90.0,
                ended_at=110.0,
                summary="Strong bounded CPU anomaly.",
            ),
        ),
        canonical_evidence=(canonical,),
        specialist_assessments=(),
        source_observations=(
            SourceObservation(source="metrics", status=SourceStatus.AVAILABLE),
        ),
        investigated_sources=("metrics",),
        commander_stages=(),
        tool_call_count=1,
        targeted_refinement_used=False,
    )

    assessment = provider.specialize(incident, context, "metrics")
    decision = provider.plan_followup(incident, context, assessment)
    diagnosis = provider.diagnose(incident, context, Architecture.DYNAMIC)

    assert assessment.candidate_service == "checkoutservice"
    assert decision.selected_sources == ("logs",)
    assert diagnosis.root_cause_indicator == "cpu"
    assert provider.calls == 3
    assert provider.last_usage_tokens == 360
    assert transport.calls == 3
