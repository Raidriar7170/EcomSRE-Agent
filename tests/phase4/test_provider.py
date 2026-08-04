from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from ecomsre.model.gateway import OpenAICompatibleConfig, ProviderProtocolError
from ecomsre.backends.replay import load_replay_case
from ecomsre.phase1.contracts import (
    EvidenceSource,
    RCADecision,
    RecommendedNextAction,
)
from ecomsre.phase2.contracts import ModelAllowedActions, Phase2Variant
from ecomsre.phase2.evidence_views import build_judge_request
from ecomsre.phase2.token_policy import MODEL_SNAPSHOT
from ecomsre.phase2.workflows import (
    execute_replay_specialists,
    prepare_specialist_execution,
)
from ecomsre.phase4.contracts import (
    DomainFaultMechanism,
    DomainRCAResult,
    DomainVariant,
)
from ecomsre.phase4.judge import invoke_provider_domain_judge
from ecomsre.phase4.provider import (
    DomainProviderCompletion,
    OpenAICompatibleDomainBackend,
    run_provider_smoke,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "1" * 32


class RecordingTransport:
    def __init__(self, response: Mapping[str, object]) -> None:
        self.response = response
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
        return self.response


def _result() -> DomainRCAResult:
    return DomainRCAResult(
        schema_version="phase4.domain-rca-result.v1",
        decision=RCADecision.RCA_CONFIRMED,
        root_service="feature",
        fault_mechanism=DomainFaultMechanism.FEATURE_FRESHNESS_LAG,
        causal_chain=("Feature freshness lag degraded the bounded business SLI.",),
        affected_sli="search relevance freshness",
        supporting_evidence=(
            f"evidence://{RUN_ID}/metrics/0000",
            f"evidence://{RUN_ID}/logs/0001",
        ),
        contradicting_evidence=(),
        missing_evidence=(),
        confidence=0.9,
        decision_rationale="Two current-run sources confirm one domain mechanism.",
        recommended_next_action=(
            RecommendedNextAction.REVIEW_BOUNDED_REPLAY_EVIDENCE
        ),
    )


def _response(result: DomainRCAResult) -> dict[str, object]:
    return {
        "id": "chatcmpl-phase4-001",
        "model": MODEL_SNAPSHOT,
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
                            "id": "call-phase4-001",
                            "type": "function",
                            "function": {
                                "name": "submit_phase4_domain_rca",
                                "arguments": result.model_dump_json(),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 1200,
            "completion_tokens": 180,
            "total_tokens": 1380,
        },
    }


def _config(model: str = MODEL_SNAPSHOT) -> OpenAICompatibleConfig:
    return OpenAICompatibleConfig(
        base_url="https://llm.example.test/v1",
        api_key="provider-secret-value",
        model=model,
    )


def test_unconfigured_provider_smoke_is_typed_offline_skip() -> None:
    report = run_provider_smoke(PROJECT_ROOT, environment={})
    assert report == {
        "schema_version": "phase4.provider-smoke-report.v1",
        "status": "SKIPPED_NOT_CONFIGURED",
        "configured": False,
        "provider": "openai-compatible",
        "model": None,
        "run_count": 0,
        "scripted_fallback": False,
        "case_results": [],
    }


def test_domain_backend_uses_one_typed_zero_temperature_call() -> None:
    result = _result()
    transport = RecordingTransport(_response(result))
    backend = OpenAICompatibleDomainBackend(
        config=_config(),
        timeout_seconds=17.0,
        transport=transport,
    )
    completion = backend.complete(
        envelope={"schema_version": "phase4.domain-judge-envelope.v1"},
        max_completion_tokens=512,
    )

    assert completion.result == result
    assert completion.provider_prompt_tokens == 1200
    assert completion.output_tokens == 180
    assert backend.calls == 1
    assert len(transport.calls) == 1
    payload = transport.calls[0]["payload"]
    assert payload["model"] == MODEL_SNAPSHOT
    assert payload["temperature"] == 0.0
    assert payload["n"] == 1
    assert payload["parallel_tool_calls"] is False
    assert payload["max_completion_tokens"] == 512
    assert payload["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_phase4_domain_rca"},
    }
    assert "provider-secret-value" not in repr(payload)
    assert json.loads(payload["messages"][1]["content"])["schema_version"] == (
        "phase4.domain-judge-envelope.v1"
    )


def test_domain_backend_rejects_model_drift_before_transport() -> None:
    transport = RecordingTransport(_response(_result()))
    with pytest.raises(ValueError, match="match Agent mainline"):
        OpenAICompatibleDomainBackend(
            config=_config("different-model"),
            timeout_seconds=17.0,
            transport=transport,
        )
    assert transport.calls == []


@pytest.mark.parametrize(
    "corruption",
    (
        "boolean-index",
        "extra-tool-call-field",
        "blank-tool-call-id",
        "legacy-function-call",
    ),
)
def test_domain_backend_rejects_non_exact_provider_envelope(
    corruption: str,
) -> None:
    response = copy.deepcopy(_response(_result()))
    choice = response["choices"][0]
    message = choice["message"]
    tool_call = message["tool_calls"][0]
    if corruption == "boolean-index":
        choice["index"] = False
    elif corruption == "extra-tool-call-field":
        tool_call["unexpected"] = "field"
    elif corruption == "blank-tool-call-id":
        tool_call["id"] = " "
    else:
        message["function_call"] = None

    backend = OpenAICompatibleDomainBackend(
        config=_config(),
        timeout_seconds=17.0,
        transport=RecordingTransport(response),
    )
    with pytest.raises(ProviderProtocolError):
        backend.complete(
            envelope={"schema_version": "phase4.domain-judge-envelope.v1"},
            max_completion_tokens=512,
        )


def test_provider_judge_rejects_decoy_supporting_evidence() -> None:
    case = load_replay_case(
        PROJECT_ROOT / "config/phase4/replay-cases/agent-visible",
        "search-ranking-configuration-frontend-decoy",
    )
    boundary = prepare_specialist_execution(
        project_root=PROJECT_ROOT,
        replay_case=case,
        variant=Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
        namespace="phase4-provider-decoy-test",
    )
    execute_replay_specialists(boundary)
    assert boundary.graph is not None
    assert boundary.judge_capacity_slot_id is not None
    request = build_judge_request(
        judge_request_id=f"phase4-domain-{boundary.judge_capacity_slot_id}",
        run_id=boundary.run_id,
        incident=case.incident,
        admitted_graph=boundary.graph,
        finding_ids=tuple(
            boundary.finding_id_by_node[node.node_id]
            for node in boundary.graph.initial_plan.nodes
        ),
        finding_store=boundary.finding_store,
        evidence_store=boundary.evidence_store,
        budget_snapshot=boundary.ledger.snapshot(),
        refinement_round=0,
        allowed_actions=ModelAllowedActions.FINAL_ONLY,
        conditional_refinement_bundle_id=None,
    )
    evidence = request.resolved_evidence_view.evidence
    business_sli = next(
        item
        for item in evidence
        if item.service == "search" and item.source is EvidenceSource.METRICS
    )
    frontend_decoy = next(
        item
        for item in evidence
        if item.service == "frontend" and item.source is EvidenceSource.CHANGES
    )
    malicious = DomainRCAResult(
        schema_version="phase4.domain-rca-result.v1",
        decision=RCADecision.RCA_CONFIRMED,
        root_service="ranking",
        fault_mechanism=DomainFaultMechanism.RANKING_CONFIGURATION_FAILURE,
        causal_chain=("A typed ranking failure degraded Search.",),
        affected_sli=case.incident.affected_sli,
        supporting_evidence=(
            business_sli.evidence_ref,
            frontend_decoy.evidence_ref,
        ),
        contradicting_evidence=(),
        missing_evidence=(),
        confidence=0.9,
        decision_rationale="Two visible source classes appear to support the claim.",
        recommended_next_action=(
            RecommendedNextAction.REVIEW_BOUNDED_REPLAY_EVIDENCE
        ),
    )

    class MaliciousBackend:
        provider_identity = "openai-compatible"
        model = MODEL_SNAPSHOT

        @staticmethod
        def complete(*, envelope, max_completion_tokens):
            del envelope, max_completion_tokens
            return DomainProviderCompletion(
                result=malicious,
                provider_prompt_tokens=100,
                output_tokens=10,
            )

    with pytest.raises(ProviderProtocolError, match="supporting evidence"):
        invoke_provider_domain_judge(
            request=request,
            ledger=boundary.ledger,
            authority=boundary.authority,
            judge_capacity_slot_id=boundary.judge_capacity_slot_id,
            variant=DomainVariant.FIXED_SPECIALIST_WORKFLOW,
            backend=MaliciousBackend(),  # type: ignore[arg-type]
        )
