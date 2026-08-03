from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from ecomsre.phase1.cli import PROJECT_ROOT, main, run_provider_smoke


ENVIRONMENT = {
    "ECOMSRE_LLM_BASE_URL": "https://llm.example.test/v1",
    "ECOMSRE_LLM_API_KEY": "test-only-secret",
    "ECOMSRE_LLM_MODEL": "fixture-model",
}


def _envelope(
    sequence: int,
    name: str,
    arguments: Mapping[str, object],
) -> dict[str, object]:
    return {
        "id": f"provider-response-{sequence:04d}",
        "model": "fixture-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"provider-tool-{sequence:04d}",
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
            "prompt_tokens": 20,
            "completion_tokens": 8,
            "total_tokens": 28,
        },
    }


def _query(service: str | None) -> dict[str, object]:
    return {
        "started_at": "2026-07-31T08:00:00+00:00",
        "ended_at": "2026-07-31T08:05:00+00:00",
        "service": service,
    }


def _confirmed(run_id: str) -> dict[str, object]:
    return {
        "schema_version": "phase1.rca-result.v1",
        "decision": "RCA_CONFIRMED",
        "root_service": "ad",
        "fault_mechanism": "runtime_configuration_failure",
        "causal_chain": [
            "Ad configuration parsing failed after a configuration transition."
        ],
        "affected_sli": "ad request success rate",
        "supporting_evidence": [
            f"evidence://{run_id}/metrics/0001",
            f"evidence://{run_id}/traces/0001",
            f"evidence://{run_id}/logs/0001",
            f"evidence://{run_id}/changes/0001",
        ],
        "contradicting_evidence": [],
        "missing_evidence": [],
        "confidence": 0.9,
        "decision_rationale": (
            "Independent read-only sources confirm the bounded root cause."
        ),
        "recommended_next_action": "Review the bounded replay evidence.",
    }


def _abstain() -> dict[str, object]:
    return {
        "schema_version": "phase1.rca-result.v1",
        "decision": "ABSTAIN",
        "root_service": None,
        "fault_mechanism": None,
        "causal_chain": [],
        "affected_sli": "frontend request success rate",
        "supporting_evidence": [],
        "contradicting_evidence": [],
        "missing_evidence": [],
        "confidence": 0.15,
        "decision_rationale": (
            "The observations do not establish an incident."
        ),
        "recommended_next_action": "Continue monitoring the affected SLI.",
    }


class SequencedTransport:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
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
        return self.responses.pop(0)


def test_provider_smoke_skip_does_not_touch_transport() -> None:
    transport = SequencedTransport([])

    result = run_provider_smoke(
        project_root=PROJECT_ROOT,
        environment={},
        transport=transport,
    )

    assert result == {
        "schema_version": "phase1.provider-smoke-report.v1",
        "status": "SKIPPED_NOT_CONFIGURED",
        "provider": "openai-compatible",
        "model": None,
        "case_results": [],
        "requirements": {
            "validated_confirmed": False,
            "validated_non_confirmed": False,
        },
    }
    assert transport.calls == []


def test_provider_smoke_skip_does_not_construct_stdlib_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ecomsre.phase1.cli as cli_module

    def forbidden_transport() -> None:
        raise AssertionError("unconfigured provider smoke constructed transport")

    monkeypatch.setattr(
        cli_module,
        "StdlibOpenAICompatibleTransport",
        forbidden_transport,
    )

    result = run_provider_smoke(
        project_root=PROJECT_ROOT,
        environment={},
        transport=None,
    )

    assert result["status"] == "SKIPPED_NOT_CONFIGURED"


def test_provider_smoke_partial_or_blank_configuration_skips_without_transport() -> None:
    for environment in (
        {"ECOMSRE_LLM_BASE_URL": "https://llm.example.test/v1"},
        {
            "ECOMSRE_LLM_BASE_URL": "https://llm.example.test/v1",
            "ECOMSRE_LLM_API_KEY": "",
            "ECOMSRE_LLM_MODEL": "fixture-model",
        },
    ):
        transport = SequencedTransport([])
        result = run_provider_smoke(
            project_root=PROJECT_ROOT,
            environment=environment,
            transport=transport,
        )
        assert result["status"] == "SKIPPED_NOT_CONFIGURED"
        assert transport.calls == []


def test_configured_provider_smoke_requires_two_validated_decision_classes() -> None:
    from ecomsre.phase1.cli import stable_run_id

    confirmed_run = stable_run_id(
        "provider-smoke",
        "ad-partial-failure-complete",
    )
    responses = [
        _envelope(1, "query_metrics", _query(None)),
        _envelope(2, "search_traces", _query("ad")),
        _envelope(3, "search_logs", _query("ad")),
        _envelope(4, "list_changes", _query(None)),
        _envelope(5, "submit_rca", _confirmed(confirmed_run)),
        _envelope(6, "query_metrics", _query(None)),
        _envelope(7, "list_changes", _query(None)),
        _envelope(8, "submit_rca", _abstain()),
    ]
    transport = SequencedTransport(responses)

    result = run_provider_smoke(
        project_root=PROJECT_ROOT,
        environment=ENVIRONMENT,
        transport=transport,
    )

    assert result["status"] == "PASSED"
    assert result["requirements"] == {
        "validated_confirmed": True,
        "validated_non_confirmed": True,
    }
    assert [item["decision"] for item in result["case_results"]] == [
        "RCA_CONFIRMED",
        "ABSTAIN",
    ]
    for item in result["case_results"]:
        agent_report = item["agent_run_report"]
        assert agent_report["terminal_status"] == "COMPLETED"
        known_refs = {
            evidence["evidence_ref"]
            for evidence in agent_report["evidence_index"]
        }
        final_rca = agent_report["final_rca"]
        cited_refs = {
            *final_rca["supporting_evidence"],
            *final_rca["contradicting_evidence"],
        }
        assert cited_refs <= known_refs
        assert all(
            reference in known_refs
            for record in agent_report["tool_call_records"]
            for reference in record["evidence_refs"]
        )
    assert "test-only-secret" not in json.dumps(result)
    assert len(transport.calls) == 8
    assert transport.responses == []
    assert all(
        call["headers"]["Authorization"] == "Bearer test-only-secret"
        for call in transport.calls
    )


def test_unconfigured_provider_cli_writes_machine_report_and_exact_terminal(
    tmp_path: Path,
    capsys: Any,
) -> None:
    transport = SequencedTransport([])

    exit_code = main(
        ["provider-smoke"],
        project_root=PROJECT_ROOT,
        artifact_root=tmp_path,
        environment={},
        transport=transport,
    )

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert json.loads(lines[0])["status"] == "SKIPPED_NOT_CONFIGURED"
    assert lines[-1] == "SKIPPED_NOT_CONFIGURED"
    report_path = tmp_path / "provider-smoke/provider-smoke-report.json"
    assert json.loads(report_path.read_bytes())["status"] == (
        "SKIPPED_NOT_CONFIGURED"
    )
    assert transport.calls == []
