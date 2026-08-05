from __future__ import annotations

import json
from pathlib import Path

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre.phase2.token_policy import MODEL_SNAPSHOT
from ecomsre.phase5a.provider import OpenAICompatiblePhase5ABackend
from ecomsre.phase5b.contracts import ExecutionSchedule
from ecomsre.phase5b.protocol import load_strict_json

from scripts.phase5b_execution.contracts import (
    ScoredRunRequest,
    TerminalStatus,
)
from scripts.phase5b_execution.provider_adapter import execute_scored_run


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _request() -> ScoredRunRequest:
    schedule = load_strict_json(
        PROJECT_ROOT / "config/phase5b/execution-schedule.v1.json",
        ExecutionSchedule,
    )
    scheduled = next(
        item
        for item in schedule.runs
        if item.template_id == "ad-partial-failure-complete"
        and item.seed_id == "seed-00"
        and item.variant == "SINGLE_AGENT_V2"
    )
    return ScoredRunRequest.from_scheduled_run(scheduled)


def _response(payload: dict[str, object], call_index: int) -> dict[str, object]:
    messages = payload["messages"]
    assert isinstance(messages, list)
    user_message = messages[1]
    assert isinstance(user_message, dict)
    envelope = json.loads(user_message["content"])
    diagnosis = {
        "schema_version": "phase5a.diagnosis-result.v2",
        "run_id": envelope["run_id"],
        "decision": "NEED_MORE_EVIDENCE",
        "root_service": None,
        "fault_mechanism": None,
        "causal_chain": [],
        "affected_sli": envelope["incident"]["affected_sli"],
        "supporting_evidence": [],
        "contradicting_evidence": [],
        "missing_evidence": ["One additional read-only source is required."],
        "confidence": 0.2,
        "decision_rationale": "The available evidence remains insufficient.",
        "recommended_next_action": (
            "Collect additional read-only telemetry evidence."
        ),
    }
    return {
        "id": f"completion-{call_index}",
        "model": MODEL_SNAPSHOT,
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"tool-{call_index}",
                            "type": "function",
                            "function": {
                                "name": "submit_phase5a_diagnosis",
                                "arguments": json.dumps(diagnosis),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        },
    }


def _backend(transport: object) -> OpenAICompatiblePhase5ABackend:
    return OpenAICompatiblePhase5ABackend(
        config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="provider-test-secret",
            model=MODEL_SNAPSHOT,
        ),
        timeout_seconds=1.0,
        transport=transport,  # type: ignore[arg-type]
    )


def test_adapter_maps_outer_run_id_and_calls_mock_transport_once(
    tmp_path: Path,
) -> None:
    class TypedTransport:
        def __init__(self) -> None:
            self.payloads: list[dict[str, object]] = []

        def post_json(self, **kwargs):
            payload = kwargs["payload"]
            self.payloads.append(payload)
            return _response(payload, len(self.payloads))

    transport = TypedTransport()
    backend = _backend(transport)
    request = _request()
    record = execute_scored_run(
        project_root=PROJECT_ROOT,
        request=request,
        backend=backend,
        environment={},
        materialized_root=tmp_path / "instances",
        evidence_class="MOCK_EXECUTION_REHEARSAL",
    )

    assert len(transport.payloads) == 1
    assert backend.calls == 1
    assert record.terminal_status is TerminalStatus.COMPLETED
    assert record.run_id == request.run_id
    assert record.observed_diagnosis is not None
    assert record.observed_diagnosis.run_id == request.run_id
    assert record.usage.provider_network_calls == 0
    assert record.usage.provider_usage_known is True
    assert record.usage.combined_tokens == (
        record.usage.workflow_tokens + record.usage.total_tokens
    )
    envelope = json.loads(transport.payloads[0]["messages"][1]["content"])  # type: ignore[index]
    assert envelope["run_id"] == request.run_id
    serialized = record.canonical_bytes().decode()
    for forbidden in (
        "provider-test-secret",
        "provider.invalid",
        "Authorization",
        "raw_response",
    ):
        assert forbidden not in serialized


def test_transport_failure_is_terminal_sanitized_and_not_retried(
    tmp_path: Path,
) -> None:
    class FailingTransport:
        def __init__(self) -> None:
            self.calls = 0

        def post_json(self, **_kwargs):
            self.calls += 1
            raise ConnectionError("raw transport secret marker")

    transport = FailingTransport()
    record = execute_scored_run(
        project_root=PROJECT_ROOT,
        request=_request(),
        backend=_backend(transport),
        environment={},
        materialized_root=tmp_path / "instances",
        evidence_class="MOCK_EXECUTION_REHEARSAL",
    )

    assert transport.calls == 1
    assert record.terminal_status is TerminalStatus.PROVIDER_TRANSPORT_FAILURE
    assert record.failure_code == "PROVIDER_TRANSPORT_FAILURE"
    assert record.observed_diagnosis is None
    assert "raw transport secret marker" not in record.canonical_bytes().decode()


def test_budget_rejection_is_terminal_before_transport(tmp_path: Path) -> None:
    class BudgetRejectingBackend:
        calls = 0

        def request_bytes(self, **_kwargs):
            return 40_000

        def complete(self, **_kwargs):
            self.calls += 1
            raise AssertionError("budget rejection called transport")

    backend = BudgetRejectingBackend()
    record = execute_scored_run(
        project_root=PROJECT_ROOT,
        request=_request(),
        backend=backend,  # type: ignore[arg-type]
        environment={},
        materialized_root=tmp_path / "instances",
        evidence_class="MOCK_EXECUTION_REHEARSAL",
    )

    assert backend.calls == 0
    assert record.terminal_status is TerminalStatus.BUDGET_FAILURE
    assert record.usage.provider_network_calls == 0
    assert record.failure_stage == "BUDGET_ADMISSION"


def test_actual_usage_overrun_is_retained_as_typed_budget_failure(
    tmp_path: Path,
) -> None:
    class OverBudgetTransport:
        def post_json(self, **kwargs):
            response = _response(kwargs["payload"], 1)
            response["usage"] = {
                "prompt_tokens": 31_000,
                "completion_tokens": 2_048,
                "total_tokens": 33_048,
            }
            return response

    record = execute_scored_run(
        project_root=PROJECT_ROOT,
        request=_request(),
        backend=_backend(OverBudgetTransport()),
        environment={},
        materialized_root=tmp_path / "instances",
        evidence_class="MOCK_EXECUTION_REHEARSAL",
    )

    assert record.terminal_status is TerminalStatus.BUDGET_FAILURE
    assert record.failure_stage == "BUDGET_RECONCILIATION"
    assert record.usage.combined_tokens > record.usage.max_tokens
    assert record.usage.within_budget is False


def test_provider_result_with_wrong_run_id_is_terminal_semantic_failure(
    tmp_path: Path,
) -> None:
    class WrongRunTransport:
        def post_json(self, **kwargs):
            response = _response(kwargs["payload"], 1)
            arguments = response["choices"][0]["message"]["tool_calls"][0][  # type: ignore[index]
                "function"
            ]["arguments"]
            diagnosis = json.loads(arguments)
            diagnosis["run_id"] = "f" * 32
            response["choices"][0]["message"]["tool_calls"][0]["function"][  # type: ignore[index]
                "arguments"
            ] = json.dumps(diagnosis)
            return response

    record = execute_scored_run(
        project_root=PROJECT_ROOT,
        request=_request(),
        backend=_backend(WrongRunTransport()),
        environment={},
        materialized_root=tmp_path / "instances",
        evidence_class="MOCK_EXECUTION_REHEARSAL",
    )

    assert record.terminal_status is TerminalStatus.SEMANTIC_FAILURE
    assert record.failure_code == "RUN_ID_MISMATCH"
    assert record.observed_diagnosis is None
