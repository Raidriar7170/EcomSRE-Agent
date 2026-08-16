from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.agent_contracts import (
    ActionSelectionDecision,
    AgentIdentityManifest,
    build_alert_context,
    build_candidate_action_view,
)
from ecomsre.dta_v2.agent_provider import (
    ACTION_SELECTION_SYSTEM_PROMPT,
    ACTION_SELECTION_FUNCTION,
    DIAGNOSIS_FUNCTION,
    INVESTIGATION_SYSTEM_PROMPT,
    OpenAICompatibleDtaAgentProvider,
    ProviderProtocolError,
    build_provider_identity,
    diagnosis_definition,
)
from ecomsre.dta_v2.contracts import (
    DtaDiagnosis,
    EvidenceSource,
    FaultDomain,
    FaultMechanism,
    RunbookId,
    Terminal,
)
from ecomsre.dta_v2.registry import (
    load_runbook_registry,
    load_scenario_registry,
)
from ecomsre.dta_v2.candidate_filter import filter_runbook_candidates
from ecomsre.dta_v2.contracts import (
    ResolvedEvidence,
    build_resolved_diagnosis_evidence_view,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "b" * 32
START = datetime(2026, 8, 16, 5, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=5)
MODEL = "gpt-5.4-mini-2026-03-17"
FROZEN_IDENTITY = ROOT / "config/dta-v2/agent-identity.v1.json"


def _response(name: str, arguments: dict[str, object], *, index: int = 1):
    return {
        "id": f"response-{index}",
        "model": MODEL,
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
                            "id": f"call-{index}",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 30,
            "total_tokens": 130,
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }


class RecordingTransport:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.payloads: list[dict[str, object]] = []

    def post_json(self, **kwargs):
        self.payloads.append(kwargs["payload"])
        return deepcopy(self.responses.pop(0))


def _context():
    scenario = load_scenario_registry(
        ROOT / "config/dta-v2/scenarios/agent-visible"
    ).scenarios[0]
    return build_alert_context(
        scenario=scenario,
        run_id=RUN_ID,
        started_at=START,
        ended_at=END,
    )


def _provider(transport: RecordingTransport):
    return OpenAICompatibleDtaAgentProvider(
        config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="private-provider-test-secret",
            model=MODEL,
        ),
        timeout_seconds=1.0,
        max_completion_tokens=1024,
        transport=transport,
    )


def test_frozen_provider_identity_matches_runtime_build_exactly() -> None:
    frozen = AgentIdentityManifest.model_validate_json(
        FROZEN_IDENTITY.read_text(encoding="utf-8")
    )

    assert frozen == build_provider_identity(MODEL)


def test_investigation_read_call_injects_runtime_bindings() -> None:
    transport = RecordingTransport(
        [
            _response(
                "query_metrics",
                {
                    "service": "payment",
                    "metric_kinds": ["ERROR_RATE", "REQUEST_SUPPORT"],
                    "max_results": 6,
                },
            )
        ]
    )
    provider = _provider(transport)
    turn = provider.investigation_turn(
        context=_context(), transcript=(), read_tools_enabled=True
    )

    assert turn.function_name == "query_metrics"
    assert turn.read_request is not None
    assert turn.read_request.run_id == RUN_ID
    assert turn.read_request.started_at == START
    assert turn.read_request.ended_at == END
    assert turn.read_request.normalized_request_sha256 != "0" * 64
    assert turn.diagnosis is None
    assert turn.usage.total_tokens == 130
    assert turn.monotonic_latency_ms >= 0
    assert turn.raw_response_sha256
    query_schema = next(
        item["function"]["parameters"]
        for item in transport.payloads[0]["tools"]
        if item["function"]["name"] == "query_metrics"
    )
    assert set(query_schema["properties"]) == {
        "service",
        "metric_kinds",
        "max_results",
    }
    assert "run_id" not in json.dumps(query_schema)
    assert "normalized_request_sha256" not in json.dumps(query_schema)


def test_after_four_dispatches_provider_can_only_submit_diagnosis() -> None:
    diagnosis = {
        "schema_version": "dta-v2.diagnosis.v1",
        "run_id": RUN_ID,
        "terminal": "NEED_MORE_EVIDENCE",
        "root_service": None,
        "root_entity_ref": None,
        "fault_domain": None,
        "mechanism": None,
        "confidence": 0.2,
        "supporting_evidence_refs": [],
        "contradicting_evidence_refs": [],
        "evidence_source_types": [],
        "uncertainties": ["One additional independent source is required."],
        "summary": "The bounded evidence remains insufficient.",
    }
    transport = RecordingTransport([_response(DIAGNOSIS_FUNCTION, diagnosis)])
    turn = _provider(transport).investigation_turn(
        context=_context(), transcript=(), read_tools_enabled=False
    )
    assert turn.diagnosis is not None
    assert turn.diagnosis.terminal is Terminal.NEED_MORE_EVIDENCE
    assert [item["function"]["name"] for item in transport.payloads[0]["tools"]] == [
        DIAGNOSIS_FUNCTION
    ]


def test_diagnosis_prompt_and_schema_expose_cross_field_canonical_constraints() -> None:
    definition = diagnosis_definition()
    properties = definition["function"]["parameters"]["properties"]

    assert "service:<root_service>" in INVESTIGATION_SYSTEM_PROMPT
    assert "supporting and contradicting" in INVESTIGATION_SYSTEM_PROMPT
    assert "first_error_location=true" in INVESTIGATION_SYSTEM_PROMPT
    assert "record's service field" in INVESTIGATION_SYSTEM_PROMPT
    assert "word 'to' instead of the symbol '->'" in INVESTIGATION_SYSTEM_PROMPT
    assert "Never cite a FAILURE observation" in INVESTIGATION_SYSTEM_PROMPT
    assert "Order each evidence reference tuple" in INVESTIGATION_SYSTEM_PROMPT
    assert "RUNNING and HEALTHY" in INVESTIGATION_SYSTEM_PROMPT
    assert "CONFIGURATION_ERROR requires fault_domain CONFIGURATION" in (
        INVESTIGATION_SYSTEM_PROMPT
    )
    assert "local resource pressure" in INVESTIGATION_SYSTEM_PROMPT
    assert "ABSTAIN rather than NEED_MORE_EVIDENCE" in INVESTIGATION_SYSTEM_PROMPT
    assert "exactly 5 seconds and 3 samples" in INVESTIGATION_SYSTEM_PROMPT
    assert "exactly one service per runtime or resource call" in (
        INVESTIGATION_SYSTEM_PROMPT
    )
    assert "historical trace ERROR" in INVESTIGATION_SYSTEM_PROMPT
    assert "parameters=[]" in ACTION_SELECTION_SYSTEM_PROMPT
    assert "Do not use semicolons" in ACTION_SELECTION_SYSTEM_PROMPT
    assert "service:<root_service>" in properties["root_entity_ref"]["description"]
    assert "supporting and contradicting" in (
        properties["evidence_source_types"]["description"]
    )


def test_provider_canonicalizes_diagnosis_set_order_but_retains_raw_arguments() -> None:
    metrics_ref = f"evidence://{RUN_ID}/metrics/0002"
    runtime_ref = f"evidence://{RUN_ID}/runtime/0001"
    arguments = {
        "schema_version": "dta-v2.diagnosis.v1",
        "run_id": RUN_ID,
        "terminal": "COMPLETED",
        "root_service": "recommendation",
        "root_entity_ref": "service:recommendation",
        "fault_domain": "SERVICE_RUNTIME",
        "mechanism": "SERVICE_UNAVAILABLE",
        "confidence": 0.9,
        "supporting_evidence_refs": [runtime_ref, metrics_ref],
        "contradicting_evidence_refs": [],
        "evidence_source_types": ["RUNTIME", "METRICS"],
        "uncertainties": [],
        "summary": "Recommendation is unavailable in the bounded window.",
    }
    provider = _provider(
        RecordingTransport([_response(DIAGNOSIS_FUNCTION, arguments)])
    )

    turn = provider.investigation_turn(
        context=_context(), transcript=(), read_tools_enabled=False
    )

    assert turn.raw_arguments["supporting_evidence_refs"] == [
        runtime_ref,
        metrics_ref,
    ]
    assert turn.diagnosis is not None
    assert turn.diagnosis.supporting_evidence_refs == (metrics_ref, runtime_ref)
    assert turn.diagnosis.evidence_source_types == (
        EvidenceSource.METRICS,
        EvidenceSource.RUNTIME,
    )


def test_schema_rejected_safe_response_is_retained_for_private_failure_evidence() -> None:
    invalid = {
        "schema_version": "dta-v2.diagnosis.v1",
        "run_id": RUN_ID,
        "terminal": "COMPLETED",
    }
    raw = _response(DIAGNOSIS_FUNCTION, invalid)
    provider = _provider(RecordingTransport([raw]))
    with pytest.raises(ProviderProtocolError, match="diagnosis"):
        provider.investigation_turn(
            context=_context(), transcript=(), read_tools_enabled=False
        )
    assert provider.last_safe_raw_response == raw
    assert provider.attempted_calls == 1
    assert provider.accepted_calls == ()


def _action_inputs():
    diagnosis = DtaDiagnosis(
        schema_version="dta-v2.diagnosis.v1",
        run_id=RUN_ID,
        terminal=Terminal.COMPLETED,
        root_service="payment",
        root_entity_ref="service:payment",
        fault_domain=FaultDomain.CONFIGURATION,
        mechanism=FaultMechanism.CONFIGURATION_ERROR,
        confidence=0.9,
        supporting_evidence_refs=(
            f"evidence://{RUN_ID}/metrics/0001",
            f"evidence://{RUN_ID}/traces/0002",
        ),
        contradicting_evidence_refs=(),
        evidence_source_types=(EvidenceSource.METRICS, EvidenceSource.TRACES),
        uncertainties=(),
        summary="Metrics and traces localize one configuration failure.",
    )
    evidence = build_resolved_diagnosis_evidence_view(
        run_id=RUN_ID,
        evidence=tuple(
            ResolvedEvidence(
                evidence_ref=ref,
                source=source,
                artifact_sha256=str(index) * 64,
            )
            for index, (ref, source) in enumerate(
                zip(
                    diagnosis.supporting_evidence_refs,
                    (EvidenceSource.METRICS, EvidenceSource.TRACES),
                    strict=True,
                ),
                start=1,
            )
        ),
    )
    candidates = filter_runbook_candidates(
        diagnosis=diagnosis,
        registry=load_runbook_registry(ROOT / "config/dta-v2/runbooks"),
        diagnosis_evidence=evidence,
    )
    return diagnosis, build_candidate_action_view(candidates)


def test_action_selection_is_a_separate_safe_semantic_call() -> None:
    diagnosis, candidate_view = _action_inputs()
    decision = {
        "schema_version": "dta-v2.action-selection-decision.v1",
        "disposition": "EXECUTE_RUNBOOK",
        "runbook_id": "ROLLBACK_CONFIGURATION",
        "target_service": "payment",
        "parameters": [],
        "supporting_evidence_refs": list(diagnosis.supporting_evidence_refs),
        "rationale": "The exact bounded candidate covers the cited evidence.",
    }
    transport = RecordingTransport([_response(ACTION_SELECTION_FUNCTION, decision)])
    turn = _provider(transport).action_selection_turn(
        diagnosis=diagnosis, candidate_view=candidate_view
    )
    assert isinstance(turn.action_selection, ActionSelectionDecision)
    assert turn.action_selection.runbook_id is RunbookId.ROLLBACK_CONFIGURATION
    user_input = json.loads(transport.payloads[0]["messages"][1]["content"])
    payload_text = json.dumps(user_input["candidate_view"], sort_keys=True).casefold()
    for forbidden in (
        "runbook_sha256",
        "registry_sha256",
        "candidate_set_sha256",
        "preconditions",
        "forward_steps",
        "executor_id",
        "verifier_id",
    ):
        assert forbidden not in payload_text


@pytest.mark.parametrize(
    "mutator",
    (
        lambda value: value["choices"][0]["message"].update({"content": "prose"}),
        lambda value: value["choices"][0]["message"].update({"refusal": "no"}),
        lambda value: value["choices"][0]["message"]["tool_calls"].append(
            value["choices"][0]["message"]["tool_calls"][0]
        ),
        lambda value: value["choices"][0]["message"]["tool_calls"][0][
            "function"
        ].update({"name": "unknown_function"}),
        lambda value: value.update({"analysis": "private reasoning"}),
        lambda value: value.update({"model": "different-model"}),
        lambda value: value.update({"usage": {"prompt_tokens": 1}}),
    ),
)
def test_provider_response_fails_closed_on_unsafe_or_invalid_envelope(mutator) -> None:
    raw = _response(
        "inspect_service_runtime",
        {"services": ["payment"], "max_results": 1},
    )
    mutator(raw)
    provider = _provider(RecordingTransport([raw]))
    with pytest.raises(ProviderProtocolError):
        provider.investigation_turn(
            context=_context(), transcript=(), read_tools_enabled=True
        )


def test_provider_rejects_credential_echo_without_retaining_it() -> None:
    raw = _response(
        "inspect_service_runtime",
        {"services": ["payment"], "max_results": 1},
    )
    raw["echo"] = "private-provider-test-secret"
    provider = _provider(RecordingTransport([raw]))
    with pytest.raises(ProviderProtocolError):
        provider.investigation_turn(
            context=_context(), transcript=(), read_tools_enabled=True
        )
    assert provider.accepted_calls == ()


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("reasoning_content", "private reasoning"),
        ("reasoning_details", {"text": "private reasoning"}),
        ("chainOfThought", "private reasoning"),
    ),
)
def test_provider_rejects_normalized_private_reasoning_fields(
    key: str, value: object
) -> None:
    raw = _response(
        "inspect_service_runtime",
        {"services": ["payment"], "max_results": 1},
    )
    raw["choices"][0]["message"][key] = value
    provider = _provider(RecordingTransport([raw]))
    with pytest.raises(ProviderProtocolError, match="private reasoning"):
        provider.investigation_turn(
            context=_context(), transcript=(), read_tools_enabled=True
        )
    assert provider.accepted_calls == ()


def test_provider_rejects_credential_fragmented_across_ordered_string_leaves() -> None:
    raw = _response(
        "inspect_service_runtime",
        {"services": ["payment"], "max_results": 1},
    )
    raw["echo"] = ["private-provider-", "test-secret"]
    provider = _provider(RecordingTransport([raw]))
    with pytest.raises(ProviderProtocolError, match="credential"):
        provider.investigation_turn(
            context=_context(), transcript=(), read_tools_enabled=True
        )
    assert provider.accepted_calls == ()


def test_provider_rejects_credential_fragmented_across_object_key_and_value() -> None:
    raw = _response(
        "inspect_service_runtime",
        {"services": ["payment"], "max_results": 1},
    )
    raw["private-provider-"] = "test-secret"
    provider = _provider(RecordingTransport([raw]))
    with pytest.raises(ProviderProtocolError, match="credential"):
        provider.investigation_turn(
            context=_context(), transcript=(), read_tools_enabled=True
        )
    assert provider.last_safe_raw_response is None
    assert provider.accepted_calls == ()


@pytest.mark.parametrize(
    ("argument_fragment", "error_match"),
    (
        ({"private-provider-": "test-secret"}, "credential"),
        ({"reasoning_content": "hidden detail"}, "private reasoning"),
        ({"headers": {"Authorization": "redacted"}}, "private configuration"),
    ),
)
def test_provider_screens_decoded_arguments_before_safe_retention(
    argument_fragment: dict[str, object], error_match: str
) -> None:
    raw = _response(
        "inspect_service_runtime",
        {
            "services": ["payment"],
            "max_results": 1,
            **argument_fragment,
        },
    )
    provider = _provider(RecordingTransport([raw]))

    with pytest.raises(ProviderProtocolError, match=error_match):
        provider.investigation_turn(
            context=_context(), transcript=(), read_tools_enabled=True
        )

    assert provider.last_safe_raw_response is None
    assert provider.accepted_calls == ()


def test_provider_rejects_outer_private_configuration_before_safe_retention() -> None:
    raw = _response(
        "inspect_service_runtime",
        {"services": ["payment"], "max_results": 1},
    )
    raw["headers"] = {"Authorization": "redacted"}
    provider = _provider(RecordingTransport([raw]))

    with pytest.raises(ProviderProtocolError, match="private configuration"):
        provider.investigation_turn(
            context=_context(), transcript=(), read_tools_enabled=True
        )

    assert provider.last_safe_raw_response is None


def test_provider_rejects_valid_diagnosis_with_fragmented_credential_text() -> None:
    diagnosis = {
        "schema_version": "dta-v2.diagnosis.v1",
        "run_id": RUN_ID,
        "terminal": "NEED_MORE_EVIDENCE",
        "root_service": None,
        "root_entity_ref": None,
        "fault_domain": None,
        "mechanism": None,
        "confidence": 0.2,
        "supporting_evidence_refs": [],
        "contradicting_evidence_refs": [],
        "evidence_source_types": [],
        "uncertainties": ["private-provider-", "test-secret"],
        "summary": "The bounded evidence remains insufficient.",
    }
    provider = _provider(
        RecordingTransport([_response(DIAGNOSIS_FUNCTION, diagnosis)])
    )

    with pytest.raises(ProviderProtocolError, match="credential"):
        provider.investigation_turn(
            context=_context(), transcript=(), read_tools_enabled=False
        )

    assert provider.last_safe_raw_response is None


def test_provider_rejects_credential_fragmented_across_valid_diagnosis_fields() -> None:
    diagnosis = {
        "schema_version": "dta-v2.diagnosis.v1",
        "run_id": RUN_ID,
        "terminal": "NEED_MORE_EVIDENCE",
        "root_service": None,
        "root_entity_ref": None,
        "fault_domain": None,
        "mechanism": None,
        "confidence": 0.2,
        "supporting_evidence_refs": [],
        "contradicting_evidence_refs": [],
        "evidence_source_types": [],
        "uncertainties": ["private-provider-"],
        "summary": "test-secret",
    }
    provider = _provider(
        RecordingTransport([_response(DIAGNOSIS_FUNCTION, diagnosis)])
    )

    with pytest.raises(ProviderProtocolError, match="credential"):
        provider.investigation_turn(
            context=_context(), transcript=(), read_tools_enabled=False
        )

    assert provider.last_safe_raw_response is None
    assert provider.accepted_calls == ()
