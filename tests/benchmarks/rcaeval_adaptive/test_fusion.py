from __future__ import annotations

from collections.abc import Mapping
import json

import pytest
from pydantic import ValidationError

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_rcaeval_adaptive.contracts import (
    CausalRole,
    FusionAction,
    FusionDecision,
    FusionFailureCode,
    InitialDiagnosis,
    ProviderFusionProposal,
    RankedHypothesis,
)
from ecomsre_rcaeval_adaptive.fusion import (
    FusionInput,
    FusionGuardrailConstructionError,
    build_fusion_request_payload,
    validate_fusion_decision,
)
from ecomsre_rcaeval_adaptive.specialists import (
    FusionOutputValidationError,
    OpenAICompatibleAdaptiveProvider,
)
from ecomsre_rcaeval_v2.contracts import BoundedEvidenceSnapshotV2


def _input() -> FusionInput:
    return FusionInput(
        schema_version="rcaeval-re2.fusion-input.v1",
        initial_diagnosis=InitialDiagnosis(
            root_cause_service="checkoutservice",
            model_proposed_indicator="cpu",
            confidence=0.7,
            evidence_refs=("metric:0001",),
            explanation="Initial bounded evidence.",
            uncertainty_flags=(),
        ),
        metrics_hypotheses=(
            RankedHypothesis(
                service="checkoutservice",
                indicator_or_none="cpu",
                score=1.0,
                causal_role=CausalRole.ROOT_CANDIDATE,
                supporting_evidence_refs=("metric:0001",),
                contradicting_evidence_refs=(),
                summary="Metrics anchor.",
                source="metrics",
            ),
        ),
        specialist_hypotheses=(),
        bounded_evidence=(
            BoundedEvidenceSnapshotV2(
                evidence_ref="metric:0001",
                source="metrics",
                service="checkoutservice",
                observation="Metric anomaly.",
            ),
        ),
        initial_service="checkoutservice",
        visible_services=("checkoutservice",),
        visible_evidence_refs=("metric:0001",),
        override_candidate_services=(),
    )


def _override_input() -> FusionInput:
    return FusionInput(
        initial_diagnosis=_input().initial_diagnosis,
        metrics_hypotheses=_input().metrics_hypotheses,
        specialist_hypotheses=(
            RankedHypothesis(
                service="frontend",
                indicator_or_none="latency",
                score=0.9,
                causal_role=CausalRole.ROOT_CANDIDATE,
                supporting_evidence_refs=("log:0001",),
                contradicting_evidence_refs=("metric:0001",),
                summary="Logs contradict the initial service.",
                source="logs",
            ),
        ),
        bounded_evidence=_input().bounded_evidence
        + (
            BoundedEvidenceSnapshotV2(
                evidence_ref="log:0001",
                source="logs",
                service="frontend",
                observation="Frontend emitted the first bounded error.",
            ),
        ),
        initial_service="checkoutservice",
        visible_services=("checkoutservice", "frontend"),
        visible_evidence_refs=("log:0001", "metric:0001"),
        override_candidate_services=("frontend",),
    )


class _FusionTransport:
    def __init__(self, arguments: Mapping[str, object] | str) -> None:
        self.arguments = arguments
        self.payload: Mapping[str, object] | None = None

    def post_json(self, **kwargs):
        self.payload = kwargs["payload"]
        function_name = self.payload["tool_choice"]["function"]["name"]
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
                                    "name": function_name,
                                    "arguments": (
                                        self.arguments
                                        if isinstance(self.arguments, str)
                                        else json.dumps(self.arguments)
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


def _provider(arguments: Mapping[str, object] | str):
    transport = _FusionTransport(arguments)
    provider = OpenAICompatibleAdaptiveProvider(
        config=OpenAICompatibleConfig(
            base_url="https://provider.example/v1",
            api_key="secret",
            model="locked-model",
        ),
        expected_model="locked-model",
        timeout_seconds=30.0,
        max_completion_tokens=2_048,
        transport=transport,
    )
    return provider, transport


def _valid_keep_arguments() -> dict[str, object]:
    return {
        "action": "KEEP_INITIAL",
        "final_root_service": "checkoutservice",
        "confidence": 1,
        "supporting_evidence_refs": ["metric:0001"],
        "reason_codes": ["DEFAULT_KEEP"],
    }


def test_fusion_input_has_one_explicit_authority() -> None:
    fusion_input = _input()

    assert fusion_input.initial_service == "checkoutservice"
    assert fusion_input.visible_services == ("checkoutservice",)
    assert fusion_input.visible_evidence_refs == ("metric:0001",)
    assert fusion_input.override_candidate_services == ()


def test_fusion_provider_uses_exact_input_and_normalizes_integer_confidence() -> None:
    provider, transport = _provider(_valid_keep_arguments())

    decision = provider.judge(_input())

    assert decision.action is FusionAction.KEEP_INITIAL
    assert decision.confidence == 1.0
    assert decision.contradicting_evidence_refs == ()
    assert provider.last_fusion_guardrail_applied is False
    assert provider.last_fusion_guardrail_reason is None
    assert provider.last_fusion_guardrail_overlap_count == 0
    assert transport.payload is not None
    messages = transport.payload["messages"]
    assert isinstance(messages, list)
    envelope = json.loads(messages[1]["content"])["fusion_input"]
    assert envelope["initial_service"] == "checkoutservice"
    assert envelope["visible_services"] == ["checkoutservice"]
    assert envelope["visible_evidence_refs"] == ["metric:0001"]
    assert envelope["override_candidate_services"] == []
    serialized = json.dumps(transport.payload).casefold()
    for forbidden in ("adaptive", "single", "dynamic", "variant"):
        assert forbidden not in serialized


def test_fusion_provider_preserves_valid_non_overlap_override() -> None:
    provider, _transport = _provider(
        {
            "action": "OVERRIDE_INITIAL",
            "final_root_service": " FRONTEND ",
            "confidence": 1,
            "supporting_evidence_refs": ["log:0001"],
            "contradicting_evidence_refs": ["metric:0001"],
            "reason_codes": ["STRONG_CAUSAL_CONTRADICTION"],
        }
    )

    decision = provider.judge(_override_input())

    assert decision.action is FusionAction.OVERRIDE_INITIAL
    assert decision.final_root_service == "frontend"
    assert decision.supporting_evidence_refs == ("log:0001",)
    assert decision.contradicting_evidence_refs == ("metric:0001",)
    assert provider.last_fusion_guardrail_applied is False


@pytest.mark.parametrize(
    ("fusion_input", "arguments", "expected_code", "raw_value"),
    (
        (
            _override_input(),
            {**_valid_keep_arguments(), "final_root_service": "frontend"},
            FusionFailureCode.FUSION_ACTION_SERVICE_INCONSISTENT,
            "raw-never-persisted",
        ),
        (
            _override_input(),
            {
                **_valid_keep_arguments(),
                "action": "OVERRIDE_INITIAL",
                "supporting_evidence_refs": ["log:0001"],
                "contradicting_evidence_refs": ["metric:0001"],
            },
            FusionFailureCode.FUSION_ACTION_SERVICE_INCONSISTENT,
            "raw-never-persisted",
        ),
        (
            _override_input(),
            {
                **_valid_keep_arguments(),
                "action": "OVERRIDE_INITIAL",
                "final_root_service": "invented-service",
                "supporting_evidence_refs": ["log:0001"],
                "contradicting_evidence_refs": ["metric:0001"],
            },
            FusionFailureCode.FUSION_SERVICE_NOT_SUPPORTED,
            "invented-service",
        ),
        (
            _input(),
            {
                **_valid_keep_arguments(),
                "supporting_evidence_refs": ["not-visible-ref"],
            },
            FusionFailureCode.FUSION_EVIDENCE_REF_NOT_VISIBLE,
            "not-visible-ref",
        ),
        (
            _override_input(),
            {
                **_valid_keep_arguments(),
                "action": "OVERRIDE_INITIAL",
                "final_root_service": "frontend",
                "supporting_evidence_refs": ["log:0001"],
            },
            FusionFailureCode.FUSION_OVERRIDE_LACKS_CONTRADICTION,
            "raw-never-persisted",
        ),
        (
            _input(),
            {**_valid_keep_arguments(), "reason_codes": ["not valid!"]},
            FusionFailureCode.FUSION_REASON_CODE_INVALID,
            "not valid!",
        ),
    ),
)
def test_fusion_exact_safe_failure_codes_do_not_persist_raw_values(
    fusion_input: FusionInput,
    arguments: Mapping[str, object],
    expected_code: FusionFailureCode,
    raw_value: str,
) -> None:
    provider, _transport = _provider(arguments)

    with pytest.raises(FusionOutputValidationError) as captured:
        provider.judge(fusion_input)

    assert captured.value.failure_code is expected_code
    safe = captured.value.safe_validation_error.model_dump_json()
    assert raw_value not in safe
    assert raw_value not in str(captured.value)


def test_fusion_stably_deduplicates_refs_and_reason_codes() -> None:
    provider, _transport = _provider(
        {
            **_valid_keep_arguments(),
            "supporting_evidence_refs": ["metric:0001", "metric:0001"],
            "reason_codes": ["DEFAULT_KEEP", "DEFAULT_KEEP"],
        }
    )

    decision = provider.judge(_input())

    assert decision.supporting_evidence_refs == ("metric:0001",)
    assert decision.reason_codes == ("DEFAULT_KEEP",)
    assert provider.last_fusion_guardrail_overlap_count == 0


def test_fusion_overlap_falls_back_to_deterministic_keep_without_second_call() -> None:
    provider, _transport = _provider(
        {
            "action": "OVERRIDE_INITIAL",
            "final_root_service": "frontend",
            "confidence": 0.95,
            "supporting_evidence_refs": ["log:0001", "metric:0001"],
            "contradicting_evidence_refs": ["metric:0001"],
            "reason_codes": ["STRONG_CAUSAL_CONTRADICTION"],
        }
    )

    decision = provider.judge(_override_input())

    assert provider.calls == 1
    assert decision.action is FusionAction.KEEP_INITIAL
    assert decision.final_root_service == "checkoutservice"
    assert decision.confidence == 0.7
    assert decision.supporting_evidence_refs == ("metric:0001",)
    assert decision.contradicting_evidence_refs == ()
    assert decision.reason_codes == (
        "STRONG_CAUSAL_CONTRADICTION",
        "OVERLAPPING_EVIDENCE_REJECTED_KEEP_INITIAL",
    )
    assert provider.last_fusion_guardrail_applied is True
    assert (
        provider.last_fusion_guardrail_reason
        == "OVERLAPPING_EVIDENCE_REJECTED_KEEP_INITIAL"
    )
    assert provider.last_fusion_guardrail_overlap_count == 1


def test_fusion_unknown_overlap_ref_is_not_masked_by_guardrail() -> None:
    provider, _transport = _provider(
        {
            **_valid_keep_arguments(),
            "supporting_evidence_refs": ["not-visible-ref"],
            "contradicting_evidence_refs": ["not-visible-ref"],
        }
    )

    with pytest.raises(FusionOutputValidationError) as captured:
        provider.judge(_input())

    assert (
        captured.value.failure_code
        is FusionFailureCode.FUSION_EVIDENCE_REF_NOT_VISIBLE
    )
    assert provider.calls == 1
    assert provider.last_fusion_guardrail_applied is False


def test_fusion_unsupported_service_overlap_is_not_masked_by_guardrail() -> None:
    provider, _transport = _provider(
        {
            **_valid_keep_arguments(),
            "final_root_service": "invented-service",
            "contradicting_evidence_refs": ["metric:0001"],
        }
    )

    with pytest.raises(FusionOutputValidationError) as captured:
        provider.judge(_input())

    assert (
        captured.value.failure_code
        is FusionFailureCode.FUSION_SERVICE_NOT_SUPPORTED
    )
    assert provider.calls == 1
    assert provider.last_fusion_guardrail_applied is False


def test_fusion_malformed_json_retains_exact_schema_failure() -> None:
    provider, _transport = _provider("{")

    with pytest.raises(FusionOutputValidationError) as captured:
        provider.judge(_input())

    assert (
        captured.value.failure_code
        is FusionFailureCode.FUSION_JSON_OR_SCHEMA_INVALID
    )
    assert provider.calls == 1


def test_fusion_unexpected_guardrail_construction_failure_is_local_and_single_call() -> None:
    provider, _transport = _provider(
        {
            **_valid_keep_arguments(),
            "contradicting_evidence_refs": ["metric:0001"],
            "reason_codes": [f"REASON_{index:02d}" for index in range(16)],
        }
    )

    with pytest.raises(FusionGuardrailConstructionError) as captured:
        provider.judge(_input())

    assert captured.value.failure_code == (
        "FUSION_RUNTIME_GUARDRAIL_CONSTRUCTION_FAILED"
    )
    assert provider.calls == 1


@pytest.mark.parametrize("missing_field", ("action", "final_root_service"))
def test_fusion_missing_required_field_has_exact_schema_code(
    missing_field: str,
) -> None:
    arguments = _valid_keep_arguments()
    del arguments[missing_field]
    provider, _transport = _provider(arguments)

    with pytest.raises(FusionOutputValidationError) as captured:
        provider.judge(_input())

    assert (
        captured.value.failure_code
        is FusionFailureCode.FUSION_JSON_OR_SCHEMA_INVALID
    )


def test_fusion_input_rejects_visible_authority_mismatch() -> None:
    payload = _input().model_dump(mode="python")
    payload["visible_evidence_refs"] = ("not-visible-ref",)

    with pytest.raises(ValidationError, match="visible refs"):
        FusionInput.model_validate(payload)


def test_fusion_payload_is_architecture_blind() -> None:
    payload = build_fusion_request_payload(
        model="locked-model", fusion_input=_input(), max_completion_tokens=512
    )
    serialized = json.dumps(payload).casefold()

    for forbidden in ("adaptive", "single", "dynamic", "variant"):
        assert forbidden not in serialized


def test_provider_proposal_allows_overlap_but_internal_decision_rejects_it() -> None:
    proposal = ProviderFusionProposal(
        action=FusionAction.KEEP_INITIAL,
        final_root_service="checkoutservice",
        confidence=0.8,
        supporting_evidence_refs=("metric:0001",),
        contradicting_evidence_refs=("metric:0001",),
        reason_codes=("AMBIGUOUS_EVIDENCE",),
    )

    assert proposal.supporting_evidence_refs == proposal.contradicting_evidence_refs
    with pytest.raises(ValidationError, match="unique and disjoint"):
        FusionDecision(**proposal.model_dump())


def test_default_keep_requires_initial_service() -> None:
    decision = FusionDecision(
        action=FusionAction.KEEP_INITIAL,
        final_root_service="frontend",
        confidence=0.8,
        supporting_evidence_refs=("metric:0001",),
        contradicting_evidence_refs=(),
        reason_codes=("WEAK_NEW_EVIDENCE",),
    )

    with pytest.raises(ValueError, match="KEEP_INITIAL"):
        validate_fusion_decision(decision, _input())


def test_strong_contradiction_can_override_to_supported_candidate() -> None:
    fusion_input = _override_input()
    decision = FusionDecision(
        action=FusionAction.OVERRIDE_INITIAL,
        final_root_service="frontend",
        confidence=0.9,
        supporting_evidence_refs=("log:0001",),
        contradicting_evidence_refs=("metric:0001",),
        reason_codes=("STRONG_CAUSAL_CONTRADICTION",),
    )

    assert validate_fusion_decision(decision, fusion_input) == decision


def test_override_rejects_weak_support_without_contradiction() -> None:
    fusion_input = FusionInput(
        initial_diagnosis=_input().initial_diagnosis,
        metrics_hypotheses=_input().metrics_hypotheses,
        specialist_hypotheses=(
            RankedHypothesis(
                service="frontend",
                indicator_or_none="latency",
                score=0.6,
                causal_role=CausalRole.PROPAGATED_SYMPTOM,
                supporting_evidence_refs=("log:0001",),
                contradicting_evidence_refs=(),
                summary="Weak symptom support only.",
                source="logs",
            ),
        ),
        bounded_evidence=_input().bounded_evidence
        + (
            BoundedEvidenceSnapshotV2(
                evidence_ref="log:0001",
                source="logs",
                service="frontend",
                observation="Frontend emitted an error log.",
            ),
        ),
        initial_service="checkoutservice",
        visible_services=("checkoutservice", "frontend"),
        visible_evidence_refs=("log:0001", "metric:0001"),
        override_candidate_services=(),
    )
    decision = FusionDecision(
        action=FusionAction.OVERRIDE_INITIAL,
        final_root_service="frontend",
        confidence=0.8,
        supporting_evidence_refs=("log:0001",),
        contradicting_evidence_refs=(),
        reason_codes=("ALTERNATIVE_SUPPORT",),
    )

    with pytest.raises(ValueError, match="contradicting root-candidate evidence"):
        validate_fusion_decision(decision, fusion_input)
