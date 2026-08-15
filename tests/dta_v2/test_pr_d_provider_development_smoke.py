from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.agent_provider import (
    ACTION_SELECTION_FUNCTION,
    DIAGNOSIS_FUNCTION,
    INVESTIGATION_SYSTEM_PROMPT,
    OpenAICompatibleDtaAgentProvider,
)
from ecomsre.dta_v2.contracts import semantic_sha256
from ecomsre.dta_v2.provider_development_smoke import (
    DevelopmentSmokeTerminal,
    ProviderDevelopmentSmokeReport,
    _PaymentDevelopmentReplayBackend,
    run_provider_development_smoke,
)
from ecomsre.dta_v2.read_tools import InvestigationReadTools
from ecomsre.dta_v2.tool_contracts import (
    ObservationStatus,
    TraceNeighborhoodRecord,
    build_trace_neighborhood_request,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


ROOT = Path(__file__).resolve().parents[2]
MODEL = "gpt-5.4-mini-2026-03-17"
SMOKE_ID = "8" * 32


def _response(name: str, arguments: dict[str, object], index: int):
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
            "completion_tokens": 40,
            "total_tokens": 140,
        },
    }


class ReplayTransport:
    def __init__(self) -> None:
        run_id = SMOKE_ID
        self.responses = [
            _response(
                "query_metrics",
                {
                    "service": "payment",
                    "metric_kinds": ["ERROR_RATE", "REQUEST_SUPPORT"],
                    "max_results": 6,
                },
                1,
            ),
            _response(
                "query_trace_neighborhood",
                {"service": "payment", "max_spans": 10},
                2,
            ),
            _response(
                DIAGNOSIS_FUNCTION,
                {
                    "schema_version": "dta-v2.diagnosis.v1",
                    "run_id": run_id,
                    "terminal": "COMPLETED",
                    "root_service": "payment",
                    "root_entity_ref": "service:payment",
                    "fault_domain": "CONFIGURATION",
                    "mechanism": "CONFIGURATION_ERROR",
                    "confidence": 0.9,
                    "supporting_evidence_refs": [
                        f"evidence://{run_id}/metrics/0001",
                        f"evidence://{run_id}/traces/0002",
                    ],
                    "contradicting_evidence_refs": [],
                    "evidence_source_types": ["METRICS", "TRACES"],
                    "uncertainties": [],
                    "summary": "Metrics and traces localize a configuration failure.",
                },
                3,
            ),
            _response(
                ACTION_SELECTION_FUNCTION,
                {
                    "schema_version": "dta-v2.action-selection-decision.v1",
                    "disposition": "EXECUTE_RUNBOOK",
                    "runbook_id": "ROLLBACK_CONFIGURATION",
                    "target_service": "payment",
                    "parameters": [],
                    "supporting_evidence_refs": [
                        f"evidence://{run_id}/metrics/0001",
                        f"evidence://{run_id}/traces/0002",
                    ],
                    "rationale": "The bounded candidate covers both cited sources.",
                },
                4,
            ),
        ]

    def post_json(self, **_kwargs):
        return deepcopy(self.responses.pop(0))


class InvalidDiagnosisTransport:
    def __init__(self) -> None:
        self.response = _response(
            DIAGNOSIS_FUNCTION,
            {
                "schema_version": "dta-v2.diagnosis.v1",
                "run_id": SMOKE_ID,
                "terminal": "COMPLETED",
            },
            1,
        )

    def post_json(self, **_kwargs):
        return deepcopy(self.response)


class SingleResponseTransport:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response

    def post_json(self, **_kwargs):
        return deepcopy(self.response)


def test_provider_development_smoke_is_private_replay_only_and_zero_write(
    tmp_path: Path,
) -> None:
    config = OpenAICompatibleConfig(
        base_url="https://provider.invalid/v1",
        api_key="provider-test-secret",
        model=MODEL,
    )
    provider = OpenAICompatibleDtaAgentProvider(
        config=config,
        timeout_seconds=1.0,
        max_completion_tokens=1024,
        transport=ReplayTransport(),
    )
    report = run_provider_development_smoke(
        repository_root=ROOT,
        private_root=tmp_path / "private",
        smoke_id=SMOKE_ID,
        config=config,
        provider=provider,
    )

    assert report.terminal is DevelopmentSmokeTerminal.PASS
    assert report.model_id == MODEL
    assert report.provider_turn_count == 4
    assert report.read_tool_dispatch_count == 2
    assert report.selected_runbook == "ROLLBACK_CONFIGURATION"
    assert report.prohibited_action_counters.model_dump() == {
        "docker_calls": 0,
        "fault_injections": 0,
        "runbook_executions": 0,
        "executor_calls": 0,
        "verifier_calls": 0,
        "forward_writes": 0,
        "configuration_mutations": 0,
        "service_mutations": 0,
        "public_writes": 0,
    }
    serialized = "".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "private").rglob("*.json")
    )
    assert "provider-test-secret" not in serialized


def test_payment_development_replay_localizes_checkout_trace_to_payment() -> None:
    ended_at = datetime.now(timezone.utc)
    request = build_trace_neighborhood_request(
        run_id=SMOKE_ID,
        service="checkout",
        started_at=ended_at - timedelta(minutes=5),
        ended_at=ended_at,
        max_spans=10,
    )
    tools = InvestigationReadTools(
        run_id=SMOKE_ID, backend=_PaymentDevelopmentReplayBackend()
    )

    observation = tools.dispatch(request)

    assert observation.status is ObservationStatus.SUCCESS
    assert len(observation.results) == 1
    record = observation.results[0]
    assert isinstance(record, TraceNeighborhoodRecord)
    assert record.anchor_service == "checkout"
    assert record.service_path == ("checkout", "payment")
    assert record.service == "payment"
    assert record.parent_service == "checkout"
    assert record.operation == "configuration-lookup"
    assert record.first_error_location is True
    assert "Never repeat an identical normalized read request" in (
        INVESTIGATION_SYSTEM_PROMPT
    )


def test_schema_rejected_provider_response_is_persisted_only_as_private_failure(
    tmp_path: Path,
) -> None:
    config = OpenAICompatibleConfig(
        base_url="https://provider.invalid/v1",
        api_key="provider-test-secret",
        model=MODEL,
    )
    transport = InvalidDiagnosisTransport()
    provider = OpenAICompatibleDtaAgentProvider(
        config=config,
        timeout_seconds=1.0,
        max_completion_tokens=1024,
        transport=transport,
    )
    private_root = tmp_path / "private"

    report = run_provider_development_smoke(
        repository_root=ROOT,
        private_root=private_root,
        smoke_id=SMOKE_ID,
        config=config,
        provider=provider,
    )

    rejected_path = private_root / "rejected-provider-response.json"
    assert report.terminal is DevelopmentSmokeTerminal.FAIL
    assert report.rejected_provider_response_file_sha256 == hashlib.sha256(
        rejected_path.read_bytes()
    ).hexdigest()
    assert json.loads(rejected_path.read_text(encoding="utf-8")) == transport.response
    assert report.prohibited_action_counters.model_dump() == {
        "docker_calls": 0,
        "fault_injections": 0,
        "runbook_executions": 0,
        "executor_calls": 0,
        "verifier_calls": 0,
        "forward_writes": 0,
        "configuration_mutations": 0,
        "service_mutations": 0,
        "public_writes": 0,
    }


def test_v1_smoke_report_loads_legacy_absent_rejected_response_field() -> None:
    legacy_payload: dict[str, object] = {
        "schema_version": "dta-v2.provider-development-smoke.v1",
        "smoke_id": SMOKE_ID,
        "terminal": "FAIL",
        "model_id": MODEL,
        "identity_sha256": "1" * 64,
        "prompt_sha256": "2" * 64,
        "tool_schema_sha256": "3" * 64,
        "diagnosis_schema_sha256": "4" * 64,
        "action_selection_schema_sha256": "5" * 64,
        "action_proposal_schema_sha256": "6" * 64,
        "provider_adapter_version": "dta-v2.openai-compatible-agent.v1",
        "temperature": 0.0,
        "provider_turn_count": 1,
        "read_tool_dispatch_count": 0,
        "agent_terminal": "FAILED",
        "failure_code": "PROVIDER_PROTOCOL_FAILURE",
        "diagnosis_terminal": None,
        "proposal_disposition": None,
        "selected_runbook": None,
        "agent_result_sha256": "7" * 64,
        "evidence_manifest_sha256": "8" * 64,
        "prohibited_action_counters": {
            "docker_calls": 0,
            "fault_injections": 0,
            "runbook_executions": 0,
            "executor_calls": 0,
            "verifier_calls": 0,
            "forward_writes": 0,
            "configuration_mutations": 0,
            "service_mutations": 0,
            "public_writes": 0,
        },
    }

    report = ProviderDevelopmentSmokeReport.model_validate_json(
        json.dumps(
            {
            **legacy_payload,
            "report_sha256": semantic_sha256(legacy_payload),
            }
        )
    )

    assert report.rejected_provider_response_file_sha256 is None
    assert ProviderDevelopmentSmokeReport.model_validate_json(
        report.model_dump_json()
    ) == report


@pytest.mark.parametrize(
    "unsafe_kind",
    ("decoded_credential", "decoded_cot", "outer_configuration"),
)
def test_unsafe_provider_response_is_never_persisted_as_rejected_evidence(
    tmp_path: Path, unsafe_kind: str
) -> None:
    arguments: dict[str, object] = {
        "services": ["payment"],
        "max_results": 1,
    }
    if unsafe_kind == "decoded_credential":
        arguments["provider-test-"] = "secret"
    elif unsafe_kind == "decoded_cot":
        arguments["reasoning_content"] = "hidden detail"
    raw = _response("inspect_service_runtime", arguments, 1)
    if unsafe_kind == "outer_configuration":
        raw["headers"] = {"Authorization": "redacted"}
    config = OpenAICompatibleConfig(
        base_url="https://provider.invalid/v1",
        api_key="provider-test-secret",
        model=MODEL,
    )
    provider = OpenAICompatibleDtaAgentProvider(
        config=config,
        timeout_seconds=1.0,
        max_completion_tokens=1024,
        transport=SingleResponseTransport(raw),
    )
    private_root = tmp_path / unsafe_kind

    report = run_provider_development_smoke(
        repository_root=ROOT,
        private_root=private_root,
        smoke_id=SMOKE_ID,
        config=config,
        provider=provider,
    )

    assert report.terminal is DevelopmentSmokeTerminal.FAIL
    assert report.rejected_provider_response_file_sha256 is None
    assert not (private_root / "rejected-provider-response.json").exists()
    assert provider.last_safe_raw_response is None
