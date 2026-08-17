from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.read_tools import FakeReadBackend, InvestigationReadTools
from ecomsre.dta_v2.v21.agent_contracts import (
    AgentArmV21,
    build_alert_context_v21,
    build_candidate_action_view_v21,
)
from ecomsre.dta_v2.v21.agent_provider import (
    ACTION_SELECTION_FUNCTION_V21,
    OpenAICompatibleDtaAgentProviderV21,
    PLANNER_FUNCTION_V21,
)
from ecomsre.dta_v2.v21.candidate_filter import filter_runbook_candidates
from ecomsre.dta_v2.v21.context_projection import build_investigation_state_view_v21
from ecomsre.dta_v2.v21.contracts import (
    FaultDomainV21,
    FaultMechanismV21,
    RunbookIdV21,
    TerminalV21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.prompts import (
    ACTION_SELECTION_SYSTEM_PROMPT_V21,
    FLAT_ADAPTIVE_SYSTEM_PROMPT_V21,
    ONE_SHOT_SYSTEM_PROMPT_V21,
    PLANNER_SYSTEM_PROMPT_V21,
)
from ecomsre.dta_v2.v21.provider_development_smoke import (
    ProviderSmokeAttemptManifestV21,
    _start_provider_smoke_attempt_v21,
)
from ecomsre.dta_v2.v21.registry import (
    load_default_runbook_registry,
    load_default_scenario_registries,
)
from ecomsre.dta_v2.v21.replay import build_replay_diagnosis
from ecomsre.model.gateway import OpenAICompatibleConfig


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "9" * 32
START = datetime(2026, 8, 17, 3, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=5)
MODEL = "gpt-5.4-mini-2026-03-17"


def _response(name: str, arguments: dict[str, object], index: int = 1):
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
    def __init__(self, responses):
        self.responses = list(responses)
        self.payloads = []

    def post_json(self, **kwargs):
        self.payloads.append(kwargs["payload"])
        return deepcopy(self.responses.pop(0))


def _context():
    scenarios, _, _ = load_default_scenario_registries(ROOT)
    return build_alert_context_v21(
        scenario=scenarios.scenarios[4],
        run_id=RUN_ID,
        started_at=START,
        ended_at=END,
    )


def _provider(transport):
    return OpenAICompatibleDtaAgentProviderV21(
        arm=AgentArmV21.EVIDENCE_GUIDED_PLANNER,
        config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="private-provider-test-secret",
            model=MODEL,
        ),
        timeout_seconds=1.0,
        max_completion_tokens=1600,
        transport=transport,
    )


def test_provider_injects_run_window_and_request_digest_into_planner_output() -> None:
    arguments = {
        "turn_ordinal": 1,
        "hypotheses": [
            {
                "hypothesis_id": "h1",
                "root_service": "payment",
                "fault_domain": "CONFIGURATION",
                "fault_mechanism": "CONFIGURATION_ERROR",
                "status": "ACTIVE",
                "supporting_evidence_refs": [],
                "contradicting_evidence_refs": [],
                "unresolved_evidence_sources": ["METRICS"],
            }
        ],
        "next_step": "REQUEST_EVIDENCE",
        "evidence_gap_sources": ["METRICS"],
        "read_request": {
            "tool": "query_metrics",
            "service": "payment",
            "metric_kinds": ["ERROR_RATE", "REQUEST_SUPPORT"],
            "max_results": 4,
        },
        "diagnosis": None,
        "bounded_rationale": "Metrics can close the active configuration evidence gap.",
    }
    transport = RecordingTransport([_response(PLANNER_FUNCTION_V21, arguments)])
    provider = _provider(transport)
    context = _context()
    tools = InvestigationReadTools(
        run_id=RUN_ID, backend=FakeReadBackend.healthy()
    )
    state = build_investigation_state_view_v21(
        context=context,
        hypotheses=(),
        evidence_store=tools.snapshot(),
        newest_observation=None,
    )

    turn = provider.investigation_turn(
        context=context, visible_state=state, read_tools_enabled=True
    )

    assert turn.plan_decision is not None
    assert turn.plan_decision.read_request is not None
    assert turn.plan_decision.read_request.run_id == RUN_ID
    assert turn.plan_decision.read_request.started_at == START
    assert turn.plan_decision.read_request.ended_at == END
    assert turn.plan_decision.read_request.normalized_request_sha256 != "0" * 64
    assert turn.raw_response_sha256
    parameters = transport.payloads[0]["tools"][0]["function"]["parameters"]
    assert transport.payloads[0]["tools"][0]["function"]["strict"] is False
    read_schema = json.dumps(parameters["properties"]["read_request"])
    assert "normalized_request_sha256" not in read_schema
    assert "started_at" not in read_schema
    assert "ended_at" not in read_schema


def test_provider_normalizes_derived_cross_field_shape_before_exact_validation() -> None:
    metrics_ref = f"evidence://{RUN_ID}/metrics/0001"
    traces_ref = f"evidence://{RUN_ID}/traces/0002"
    arguments = {
        "turn_ordinal": 3,
        "hypotheses": [
            {
                "hypothesis_id": "h1",
                "root_service": "payment",
                "fault_domain": "CONFIGURATION",
                "fault_mechanism": "CONFIGURATION_ERROR",
                "status": "REJECTED",
                "supporting_evidence_refs": [metrics_ref],
                "contradicting_evidence_refs": [traces_ref],
                "unresolved_evidence_sources": ["RUNTIME"],
            }
        ],
        "next_step": "SUBMIT_DIAGNOSIS",
        "evidence_gap_sources": [],
        "read_request": None,
        "diagnosis": {
            "schema_version": "dta-v21.diagnosis.v1",
            "run_id": RUN_ID,
            "terminal": "COMPLETED",
            "root_service": "payment",
            "root_entity_ref": "payment",
            "fault_domain": "CONFIGURATION",
            "mechanism": "CONFIGURATION_ERROR",
            "confidence": 0.9,
            "supporting_evidence_refs": [traces_ref, metrics_ref],
            "contradicting_evidence_refs": [],
            "evidence_source_types": ["TRACES", "METRICS"],
            "uncertainties": [],
            "summary": "The typed observations support a bounded configuration diagnosis.",
        },
        "bounded_rationale": "The cited observations are sufficient for early stopping.",
    }
    transport = RecordingTransport([_response(PLANNER_FUNCTION_V21, arguments)])
    provider = _provider(transport)
    context = _context()
    tools = InvestigationReadTools(
        run_id=RUN_ID, backend=FakeReadBackend.healthy()
    )
    state = build_investigation_state_view_v21(
        context=context,
        hypotheses=(),
        evidence_store=tools.snapshot(),
        newest_observation=None,
        completed_provider_turns=2,
    )

    turn = provider.investigation_turn(
        context=context, visible_state=state, read_tools_enabled=True
    )

    assert turn.plan_decision is not None
    assert turn.plan_decision.hypotheses[0].unresolved_evidence_sources == ()
    assert turn.plan_decision.diagnosis is not None
    assert turn.plan_decision.diagnosis.root_entity_ref == "service:payment"
    assert turn.plan_decision.diagnosis.supporting_evidence_refs == (
        metrics_ref,
        traces_ref,
    )


def test_action_selection_input_exposes_only_three_typed_views() -> None:
    diagnosis, evidence = build_replay_diagnosis(
        run_id=RUN_ID,
        terminal=TerminalV21.COMPLETED,
        root_service="payment",
        fault_domain=FaultDomainV21.CONFIGURATION,
        mechanism=FaultMechanismV21.CONFIGURATION_ERROR,
        evidence_sources=("METRICS", "TRACES"),
    )
    registry = load_default_runbook_registry(ROOT)
    candidates = filter_runbook_candidates(
        diagnosis=diagnosis,
        diagnosis_evidence=evidence,
        registry=registry,
        exact_target="payment",
    )
    response = _response(
        ACTION_SELECTION_FUNCTION_V21,
        {
            "schema_version": "dta-v21.action-selection-decision.v1",
            "disposition": "EXECUTE_RUNBOOK",
            "runbook_id": "ROLLBACK_CONFIGURATION",
            "target_service": "payment",
            "parameters": [],
            "supporting_evidence_refs": list(diagnosis.supporting_evidence_refs),
            "rationale": "The exact visible candidate matches the cited observations.",
        },
    )
    transport = RecordingTransport([response])
    provider = _provider(transport)

    turn = provider.action_selection_turn(
        diagnosis=diagnosis,
        resolved_evidence=evidence,
        candidate_view=build_candidate_action_view_v21(candidates),
    )

    assert turn.action_selection is not None
    assert turn.action_selection.runbook_id is RunbookIdV21.ROLLBACK_CONFIGURATION
    visible = json.loads(transport.payloads[0]["messages"][1]["content"])
    assert set(visible) == {"diagnosis", "resolved_evidence", "candidate_view"}
    serialized = json.dumps(visible).casefold()
    for forbidden in (
        "executor_id",
        "verifier_id",
        "docker",
        "flag_key",
        "raw_command",
        "authorization",
    ):
        assert forbidden not in serialized


def test_prompts_require_selective_grounded_reasoning_without_truth_mappings() -> None:
    combined = "\n".join(
        (
            PLANNER_SYSTEM_PROMPT_V21,
            FLAT_ADAPTIVE_SYSTEM_PROMPT_V21,
            ONE_SHOT_SYSTEM_PROMPT_V21,
            ACTION_SELECTION_SYSTEM_PROMPT_V21,
        )
    ).casefold()
    for required in (
        "root service from affected services",
        "service unavailability from memory leak and cpu saturation",
        "trace evidence for causal dependency",
        "resource evidence for resource hypotheses",
        "do not call every tool",
        "cite only exact observed evidence_ref",
        "abstain",
        "exact visible candidate",
    ):
        assert required in combined
    for forbidden in (
        "adhighcpu",
        "emailmemoryleak",
        "paymentfailure",
        "expected runbook",
        "answer key",
        "held-out family",
    ):
        assert forbidden not in combined


def test_rejected_provider_response_still_exposes_only_its_raw_hash() -> None:
    raw = _response("wrong_function", {}, index=7)
    transport = RecordingTransport([raw])
    provider = _provider(transport)
    context = _context()
    state = build_investigation_state_view_v21(
        context=context,
        hypotheses=(),
        evidence_store=InvestigationReadTools(
            run_id=RUN_ID, backend=FakeReadBackend.healthy()
        ).snapshot(),
        newest_observation=None,
    )

    with pytest.raises(Exception, match="required function"):
        provider.investigation_turn(
            context=context, visible_state=state, read_tools_enabled=True
        )

    assert provider.raw_response_sha256_by_attempt == (semantic_sha256(raw),)


def test_provider_smoke_manifest_is_predeclared_and_blocks_identical_rerun(
    tmp_path: Path,
) -> None:
    config = OpenAICompatibleConfig(
        base_url="https://provider.invalid/v1",
        api_key="private-provider-test-secret",
        model=MODEL,
    )
    identity = _provider(RecordingTransport([])).identity
    private_root = tmp_path / "private"

    manifest, attempt_root = _start_provider_smoke_attempt_v21(
        repository_root=ROOT,
        private_root=private_root,
        attempt_id="c" * 32,
        created_at=START,
        identity=identity,
        config=config,
        timeout_seconds=60.0,
        max_completion_tokens=1600,
    )

    manifest_path = attempt_root / "attempt-manifest.json"
    assert manifest_path.is_file()
    assert ProviderSmokeAttemptManifestV21.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    ) == manifest
    assert not (attempt_root / "agent-result.json").exists()
    with pytest.raises(ValueError, match="identical Provider Smoke rerun"):
        _start_provider_smoke_attempt_v21(
            repository_root=ROOT,
            private_root=private_root,
            attempt_id="d" * 32,
            created_at=START + timedelta(seconds=1),
            identity=identity,
            config=config,
            timeout_seconds=60.0,
            max_completion_tokens=1600,
        )
