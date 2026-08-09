from __future__ import annotations

from collections.abc import Mapping
import json

import pytest
from pydantic import ValidationError

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_rcaeval.adapter import IncidentManifest
from ecomsre_rcaeval_adaptive.contracts import (
    CausalRole,
    InitialDiagnosis,
    InitialDiagnosisInput,
    InitialFailureCode,
    RankedHypothesis,
    RankedHypothesisBatch,
    SpecialistFailureCode,
    SpecialistInitialDiagnosisContext,
    SpecialistInput,
)
from ecomsre_rcaeval_adaptive.specialists import (
    LOGS_PROMPT,
    TRACES_PROMPT,
    InitialOutputValidationError,
    OpenAICompatibleAdaptiveProvider,
    SpecialistOutputValidationError,
    validate_hypothesis_batch,
)
from ecomsre_rcaeval_v2.contracts import (
    BoundedEvidenceSnapshotV2,
    IndicatorCandidateSnapshotV2,
)


class _InitialTransport:
    def __init__(self, arguments: Mapping[str, object]) -> None:
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
                                    "arguments": json.dumps(self.arguments),
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


def _initial_input() -> InitialDiagnosisInput:
    return InitialDiagnosisInput(
        schema_version="rcaeval-single-first-adaptive.initial-input.v1",
        incident=IncidentManifest(
            case_id="re2-ob-case-0001",
            system="RE2-OB",
            anomaly_timestamp=1_000,
            modalities=("metrics", "logs", "traces"),
        ),
        bounded_evidence=(
            BoundedEvidenceSnapshotV2(
                evidence_ref="metric:0001",
                source="metrics",
                service="frontend",
                observation="Frontend latency shifted.",
            ),
            BoundedEvidenceSnapshotV2(
                evidence_ref="log:0001",
                source="logs",
                service="checkoutservice",
                observation="Checkout emitted an overload error.",
            ),
        ),
        indicator_candidates=(
            IndicatorCandidateSnapshotV2(
                service="checkoutservice",
                canonical_indicator="cpu",
                metric_name="checkoutservice_cpu",
                score=4.0,
                evidence_ref="indicator:0001",
            ),
        ),
        visible_services=("checkoutservice", "frontend"),
        visible_evidence_refs=("indicator:0001", "log:0001", "metric:0001"),
    )


def _specialist_input() -> SpecialistInput:
    return SpecialistInput(
        source="logs",
        incident=_initial_input().incident,
        initial_diagnosis=SpecialistInitialDiagnosisContext(
            root_cause_service="checkoutservice",
            model_proposed_indicator="cpu",
            confidence=0.8,
            explanation="The bounded evidence supports this service.",
            uncertainty_flags=(),
        ),
        source_evidence=(
            BoundedEvidenceSnapshotV2(
                evidence_ref="log:0001",
                source="logs",
                service="checkoutservice",
                observation="Checkout emitted an overload error.",
            ),
        ),
        visible_services=("checkoutservice",),
        visible_evidence_refs=("log:0001",),
    )


def _provider(arguments: Mapping[str, object]):
    transport = _InitialTransport(arguments)
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


def _valid_arguments() -> dict[str, object]:
    return {
        "root_cause_service": "checkoutservice",
        "model_proposed_indicator": "cpu",
        "confidence": 0.9,
        "evidence_refs": ["log:0001", "indicator:0001"],
        "explanation": "The bounded evidence supports this service.",
        "uncertainty_flags": [],
    }


def _valid_specialist_arguments() -> dict[str, object]:
    return {
        "hypotheses": [
            {
                "service": "checkoutservice",
                "indicator_or_none": "cpu",
                "score": 1,
                "causal_role": "ROOT_CANDIDATE",
                "supporting_evidence_refs": ["log:0001"],
                "contradicting_evidence_refs": [],
                "summary": "The source supports a root-candidate hypothesis.",
            }
        ]
    }


def _hypothesis(*, source: str = "logs", evidence_ref: str = "log:0001"):
    return RankedHypothesis.model_validate(
        {
            "service": "checkoutservice",
            "indicator_or_none": "cpu",
            "score": 0.8,
            "causal_role": CausalRole.ROOT_CANDIDATE,
            "supporting_evidence_refs": (evidence_ref,),
            "contradicting_evidence_refs": (),
            "summary": "The source supports a root-candidate hypothesis.",
            "source": source,
        }
    )


@pytest.mark.parametrize("prompt", (LOGS_PROMPT, TRACES_PROMPT))
def test_specialist_prompts_prohibit_overlapping_evidence_roles(prompt: str) -> None:
    assert "must not overlap" in prompt
    assert "exactly one evidence role" in prompt


def test_specialist_returns_one_to_three_hypotheses() -> None:
    with pytest.raises(ValidationError):
        RankedHypothesisBatch.model_validate(
            {
                "source": "logs",
                "hypotheses": tuple(_hypothesis() for _ in range(4)),
            }
        )


def test_specialist_input_has_one_exact_source_authority() -> None:
    specialist_input = _specialist_input()

    assert specialist_input.source == "logs"
    assert specialist_input.visible_services == ("checkoutservice",)
    assert specialist_input.visible_evidence_refs == ("log:0001",)
    serialized = specialist_input.model_dump_json()
    initial_context = specialist_input.model_dump(mode="json")["initial_diagnosis"]
    assert "evidence_refs" not in initial_context
    assert "metric:0001" not in serialized
    assert "trace:0001" not in serialized
    assert "canonical_evidence" not in serialized
    assert "ArchitectureContext" not in serialized


def test_specialist_initial_context_preserves_explanation_bound() -> None:
    source_schema = InitialDiagnosis.model_json_schema(mode="validation")
    projected_schema = SpecialistInitialDiagnosisContext.model_json_schema(
        mode="validation"
    )

    assert source_schema["properties"]["explanation"]["maxLength"] == 2_000
    assert (
        projected_schema["properties"]["explanation"]["maxLength"]
        == source_schema["properties"]["explanation"]["maxLength"]
    )


def test_specialist_provider_uses_exact_input_and_runtime_adds_source() -> None:
    arguments = _valid_specialist_arguments()
    hypothesis = arguments["hypotheses"][0]
    assert isinstance(hypothesis, dict)
    hypothesis["service"] = " CheckoutService "
    hypothesis["indicator_or_none"] = " CPU "
    provider, transport = _provider(arguments)

    batch = provider.specialize(_specialist_input())

    assert batch.source == "logs"
    assert batch.hypotheses[0].source == "logs"
    assert batch.hypotheses[0].service == "checkoutservice"
    assert batch.hypotheses[0].indicator_or_none == "cpu"
    assert batch.hypotheses[0].score == 1.0
    assert transport.payload is not None
    messages = transport.payload["messages"]
    assert isinstance(messages, list)
    envelope = json.loads(messages[1]["content"])
    assert set(envelope) == {
        "schema_version",
        "source",
        "incident",
        "initial_diagnosis",
        "source_evidence",
        "visible_services",
        "visible_evidence_refs",
    }
    assert envelope["visible_services"] == ["checkoutservice"]
    assert envelope["visible_evidence_refs"] == ["log:0001"]
    assert "evidence_refs" not in envelope["initial_diagnosis"]
    serialized = json.dumps(transport.payload, sort_keys=True)
    assert "canonical_evidence" not in serialized
    assert "bounded_context" not in serialized
    parameters = transport.payload["tools"][0]["function"]["parameters"]
    assert "source" not in parameters["$defs"]["ProviderRankedHypothesis"][
        "properties"
    ]


@pytest.mark.parametrize(
    ("arguments", "expected_code", "raw_value"),
    (
        (
            {
                "hypotheses": [
                    {
                        **_valid_specialist_arguments()["hypotheses"][0],
                        "service": "invented-service",
                    }
                ]
            },
            SpecialistFailureCode.SPECIALIST_SERVICE_NOT_VISIBLE,
            "invented-service",
        ),
        (
            {
                "hypotheses": [
                    {
                        **_valid_specialist_arguments()["hypotheses"][0],
                        "supporting_evidence_refs": ["not-visible-ref"],
                    }
                ]
            },
            SpecialistFailureCode.SPECIALIST_EVIDENCE_REF_NOT_VISIBLE,
            "not-visible-ref",
        ),
        (
            {
                "hypotheses": [
                    {
                        **_valid_specialist_arguments()["hypotheses"][0],
                        "supporting_evidence_refs": ["log:0001", "log:0001"],
                    }
                ]
            },
            SpecialistFailureCode.SPECIALIST_DUPLICATE_EVIDENCE_REF,
            "log:0001",
        ),
        (
            {
                "hypotheses": [
                    {
                        **_valid_specialist_arguments()["hypotheses"][0],
                        "contradicting_evidence_refs": ["log:0001"],
                    }
                ]
            },
            SpecialistFailureCode.SPECIALIST_OVERLAPPING_EVIDENCE_REF,
            "log:0001",
        ),
        (
            {**_valid_specialist_arguments(), "source": "traces"},
            SpecialistFailureCode.SPECIALIST_BATCH_SOURCE_MISMATCH,
            "traces",
        ),
        (
            {
                "hypotheses": [
                    {
                        **_valid_specialist_arguments()["hypotheses"][0],
                        "score": "not-a-score",
                    }
                ]
            },
            SpecialistFailureCode.SPECIALIST_SCORE_INVALID,
            "not-a-score",
        ),
        (
            {
                "hypotheses": [
                    {
                        **_valid_specialist_arguments()["hypotheses"][0],
                        "causal_role": "NOT_A_ROLE",
                    }
                ]
            },
            SpecialistFailureCode.SPECIALIST_CAUSAL_ROLE_INVALID,
            "NOT_A_ROLE",
        ),
        (
            {"hypotheses": []},
            SpecialistFailureCode.SPECIALIST_HYPOTHESIS_COUNT_INVALID,
            "raw-never-persisted",
        ),
        (
            {
                "hypotheses": _valid_specialist_arguments()["hypotheses"] * 4,
            },
            SpecialistFailureCode.SPECIALIST_HYPOTHESIS_COUNT_INVALID,
            "raw-never-persisted",
        ),
    ),
)
def test_specialist_exact_safe_failure_codes_do_not_persist_raw_values(
    arguments: Mapping[str, object],
    expected_code: SpecialistFailureCode,
    raw_value: str,
) -> None:
    provider, _transport = _provider(arguments)

    with pytest.raises(SpecialistOutputValidationError) as captured:
        provider.specialize(_specialist_input())

    assert captured.value.failure_code is expected_code
    safe = captured.value.safe_validation_error.model_dump_json()
    assert raw_value not in safe
    assert raw_value not in str(captured.value)


def test_specialist_missing_required_field_has_exact_schema_code() -> None:
    arguments = _valid_specialist_arguments()
    hypothesis = arguments["hypotheses"][0]
    assert isinstance(hypothesis, dict)
    del hypothesis["service"]
    provider, _transport = _provider(arguments)

    with pytest.raises(SpecialistOutputValidationError) as captured:
        provider.specialize(_specialist_input())

    assert (
        captured.value.failure_code
        is SpecialistFailureCode.SPECIALIST_JSON_OR_SCHEMA_INVALID
    )


def test_specialist_has_no_final_diagnosis_field() -> None:
    schema = RankedHypothesisBatch.model_json_schema(mode="validation")

    assert "final_root_service" not in str(schema)
    assert "root_cause_service" not in str(schema)


def test_unknown_or_cross_source_evidence_is_rejected() -> None:
    batch = RankedHypothesisBatch(
        source="logs",
        hypotheses=(_hypothesis(evidence_ref="trace:0001"),),
    )

    with pytest.raises(ValueError, match="unknown source evidence"):
        validate_hypothesis_batch(
            batch,
            _specialist_input(),
        )


def test_initial_diagnosis_defaults_absent_optional_outputs_to_none_and_empty() -> None:
    diagnosis = InitialDiagnosis.model_validate(
        {
            "root_cause_service": "checkoutservice",
            "confidence": 0.8,
            "evidence_refs": ("metric:0001",),
            "explanation": "The bounded evidence supports this service.",
        }
    )

    assert diagnosis.model_proposed_indicator is None
    assert diagnosis.uncertainty_flags == ()


def test_initial_payload_has_one_external_reference_authority() -> None:
    provider, transport = _provider(_valid_arguments())

    diagnosis = provider.diagnose(_initial_input())

    assert diagnosis.root_cause_service == "checkoutservice"
    assert transport.payload is not None
    messages = transport.payload["messages"]
    assert isinstance(messages, list)
    envelope = json.loads(messages[1]["content"])
    assert set(envelope) == {
        "schema_version",
        "incident",
        "bounded_evidence",
        "indicator_candidates",
        "visible_services",
        "visible_evidence_refs",
    }
    serialized = json.dumps(transport.payload, sort_keys=True)
    for forbidden in (
        "bounded_context",
        "canonical_evidence",
        "artifact_ref",
        "architecture-context",
        "/Users/",
        "evidence://",
    ):
        assert forbidden not in serialized
    assert envelope["visible_services"] == ["checkoutservice", "frontend"]
    assert envelope["visible_evidence_refs"] == [
        "indicator:0001",
        "log:0001",
        "metric:0001",
    ]


@pytest.mark.parametrize(
    ("updates", "expected_code", "raw_value"),
    (
        (
            {"root_cause_service": "not-visible-service"},
            InitialFailureCode.INITIAL_SERVICE_NOT_VISIBLE,
            "not-visible-service",
        ),
        (
            {"evidence_refs": ["not-visible-ref"]},
            InitialFailureCode.INITIAL_EVIDENCE_REF_NOT_VISIBLE,
            "not-visible-ref",
        ),
        (
            {"evidence_refs": ["evidence://internal-canonical"]},
            InitialFailureCode.INITIAL_EVIDENCE_REF_NOT_VISIBLE,
            "evidence://internal-canonical",
        ),
        (
            {"evidence_refs": ["metric:0001", "metric:0001"]},
            InitialFailureCode.INITIAL_DUPLICATE_EVIDENCE_REF,
            "metric:0001",
        ),
        (
            {"uncertainty_flags": ["NOT_A_FLAG"]},
            InitialFailureCode.INITIAL_UNCERTAINTY_FLAG_INVALID,
            "NOT_A_FLAG",
        ),
    ),
)
def test_initial_validation_returns_safe_field_code_without_raw_value(
    updates: Mapping[str, object],
    expected_code: InitialFailureCode,
    raw_value: str,
) -> None:
    arguments = _valid_arguments()
    arguments.update(updates)
    provider, _transport = _provider(arguments)

    with pytest.raises(InitialOutputValidationError) as captured:
        provider.diagnose(_initial_input())

    assert captured.value.failure_code is expected_code
    safe = captured.value.safe_validation_error.model_dump_json()
    assert raw_value not in safe
    assert raw_value not in str(captured.value)


def test_initial_schema_failure_has_exact_safe_code() -> None:
    arguments = _valid_arguments()
    del arguments["root_cause_service"]
    provider, _transport = _provider(arguments)

    with pytest.raises(InitialOutputValidationError) as captured:
        provider.diagnose(_initial_input())

    assert (
        captured.value.failure_code
        is InitialFailureCode.INITIAL_JSON_OR_SCHEMA_INVALID
    )
