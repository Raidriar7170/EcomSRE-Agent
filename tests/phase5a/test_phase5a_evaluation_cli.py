from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
from typing import Any
import urllib.error

from ecomsre.phase2.token_policy import MODEL_SNAPSHOT
from ecomsre.phase5a import cli, evaluation
from ecomsre.phase5a.demo import build_phase5a_demo_report
from ecomsre.phase5a.provider import run_provider_pilot


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_worker_probe_denies_all_evaluator_and_external_access() -> None:
    probe = evaluation.run_worker_probe(PROJECT_ROOT)
    assert probe["isolated_sys_path"] is True
    assert probe["phase5a_evaluator_read"] == "DENIED"
    assert probe["socket_connect"] == "DENIED"
    assert probe["subprocess_run"] == "DENIED"
    assert probe["os_open"] == "DENIED"


def test_truth_is_loaded_only_after_each_isolated_trace_returns(monkeypatch) -> None:
    returned: list[tuple[str, str, str]] = []
    truth_reads: list[tuple[str, str, str]] = []
    original_runner = evaluation._run_workflow_trace
    original_loader = evaluation._load_ground_truth

    def tracked_runner(project_root, suite, case_id, variant):
        trace = original_runner(project_root, suite, case_id, variant)
        returned.append((variant.value, suite, case_id))
        return trace

    def tracked_loader(root, suite, case_id):
        current_variant = returned[-1][0]
        expected = (current_variant, suite, case_id)
        assert returned == [*truth_reads, expected]
        truth = original_loader(root, suite, case_id)
        truth_reads.append(expected)
        return truth

    monkeypatch.setattr(evaluation, "_run_workflow_trace", tracked_runner)
    monkeypatch.setattr(evaluation, "_load_ground_truth", tracked_loader)
    report: Any = evaluation.run_capability_parity_evaluation(PROJECT_ROOT)

    assert report["status"] == "COMPLETED"
    assert returned == truth_reads
    assert len(returned) == 36


def test_evaluation_retains_36_runs_and_required_quality_gates() -> None:
    report: Any = evaluation.run_capability_parity_evaluation(PROJECT_ROOT)

    assert report["schema_version"] == "phase5a.capability-parity-report.v2"
    assert report["evaluation_label"] == "VISIBLE DEVELOPMENT EVALUATION"
    assert report["claim_boundary"] == "NOT A SUPERIORITY CLAIM"
    assert report["run_count"] == 36
    assert len(report["run_results"]) == 36
    assert report["failure_denominator_policy"] == "all 36 runs are retained"
    assert report["runtime_gate"]["typed_terminal_results"] == 36
    assert report["runtime_gate"]["workflow_failures"] == 0
    assert report["runtime_gate"]["empty_evidence_failures"] == 0
    assert report["quality_gate"]["fixed_original_7"] > 2
    assert report["quality_gate"]["dynamic_original_7"] > 2
    assert report["efficiency_gate"]["dynamic_average_tool_calls"] <= (
        report["efficiency_gate"]["fixed_average_tool_calls"]
    )
    assert set(report["hard_subsets"]) == {
        "missing telemetry",
        "decoy change",
        "configuration",
        "cache/dependency",
        "Search/Recommendation domain",
        "negative/no-incident",
    }
    assert report["superiority_claim"] is False
    assert report["hidden_evaluation"] is False
    assert report["phase5b_entered"] is False


def test_compare_verify_demo_and_unconfigured_provider_are_deterministic(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "capability-parity-report.json"
    assert cli.main(["compare", "--output", str(report_path)]) == 0
    first = report_path.read_bytes()
    assert cli.main(["verify", "--report", str(report_path)]) == 0
    assert report_path.read_bytes() == first

    demo_path = tmp_path / "demo.json"
    assert cli.main(["demo", "--output", str(demo_path)]) == 0
    demo_first = demo_path.read_bytes()
    assert cli.main(["demo", "--output", str(demo_path)]) == 0
    assert demo_path.read_bytes() == demo_first
    assert json.loads(demo_first) == build_phase5a_demo_report(PROJECT_ROOT)

    provider_path = tmp_path / "provider.json"
    assert cli.main(["provider-pilot", "--output", str(provider_path)]) == 0
    provider = json.loads(provider_path.read_bytes())
    assert provider["status"] == "SKIPPED_NOT_CONFIGURED"
    assert provider["run_count"] == 0
    assert provider["scripted_fallback"] is False


def test_unconfigured_provider_pilot_never_uses_a_transport() -> None:
    class ForbiddenTransport:
        def post_json(self, **_kwargs):
            raise AssertionError("unconfigured pilot touched provider transport")

    report: Any = run_provider_pilot(
        project_root=PROJECT_ROOT,
        environment={},
        transport=ForbiddenTransport(),
    )
    assert report["status"] == "SKIPPED_NOT_CONFIGURED"
    assert report["configured"] is False
    assert report["run_count"] == 0
    assert report["scripted_fallback"] is False


def test_configured_provider_pilot_runs_exactly_nine_no_retry_calls() -> None:
    class TypedTransport:
        def __init__(self) -> None:
            self.payloads: list[dict[str, object]] = []

        def post_json(self, **kwargs):
            payload = kwargs["payload"]
            self.payloads.append(payload)
            messages = payload["messages"]
            envelope = json.loads(messages[1]["content"])
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
                "missing_evidence": [
                    "Provider pilot requests one additional read-only source."
                ],
                "confidence": 0.2,
                "decision_rationale": (
                    "The bounded provider pilot retains an unresolved evidence gap."
                ),
                "recommended_next_action": (
                    "Collect additional read-only telemetry evidence."
                ),
            }
            return {
                "id": f"completion-{len(self.payloads)}",
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
                                    "id": f"tool-{len(self.payloads)}",
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

    transport = TypedTransport()
    api_key = "provider-test-secret"
    report: Any = run_provider_pilot(
        project_root=PROJECT_ROOT,
        environment={
            "ECOMSRE_LLM_BASE_URL": "https://provider.invalid/v1",
            "ECOMSRE_LLM_API_KEY": api_key,
            "ECOMSRE_LLM_MODEL": MODEL_SNAPSHOT,
        },
        transport=transport,
    )

    assert report["status"] == "PASSED"
    assert report["run_count"] == 9
    assert report["provider_call_count"] == 9
    assert len(transport.payloads) == 9
    assert all(payload["temperature"] == 0.0 for payload in transport.payloads)
    assert all(
        payload["max_completion_tokens"] == 2048
        for payload in transport.payloads
    )
    assert report["scripted_fallback"] is False
    assert report["hidden_retry"] is False
    assert report["shared_outer_budget"]["model_calls"] == 8
    assert report["shared_outer_budget"]["tool_calls"] == 8
    assert report["shared_outer_budget"]["tokens"] == 32_000
    assert all(
        item["outer_budget"]["provider_attempted"] is True
        and item["outer_budget"]["provider_token_accounting"] == "ACTUAL"
        and item["outer_budget"]["within_budget"] is True
        and item["failure_code"] is None
        for item in report["case_results"]
    )
    assert api_key not in json.dumps(report)


def test_failed_provider_attempts_retain_reserved_outer_budget_usage() -> None:
    class TimeoutTransport:
        def post_json(self, **_kwargs):
            raise TimeoutError

    report: Any = run_provider_pilot(
        project_root=PROJECT_ROOT,
        environment={
            "ECOMSRE_LLM_BASE_URL": "https://provider.invalid/v1",
            "ECOMSRE_LLM_API_KEY": "provider-test-secret",
            "ECOMSRE_LLM_MODEL": MODEL_SNAPSHOT,
        },
        transport=TimeoutTransport(),
    )

    assert report["status"] == "FAILED"
    assert report["run_count"] == 9
    assert report["provider_call_count"] == 9
    assert all(
        item["status"] == "FAILED"
        and item["failure_code"] == "PROVIDER_TIMEOUT"
        and item["outer_budget"]["provider_attempted"] is True
        and item["outer_budget"]["provider_token_accounting"]
        == "RESERVED_UNKNOWN"
        and item["outer_budget"]["usage"]["model_calls"] >= 6
        for item in report["case_results"]
    )


def test_provider_failure_codes_are_allowlisted_and_never_copy_error_text() -> None:
    class DiagnosticTransport:
        def __init__(self) -> None:
            self.calls = 0

        def post_json(self, **_kwargs):
            self.calls += 1
            failure_kind = (self.calls - 1) % 3
            if failure_kind == 0:
                raise TimeoutError("raw-timeout-marker")
            if failure_kind == 1:
                raise OSError("raw-connection-marker")
            return {
                "id": f"completion-{self.calls}",
                "model": MODEL_SNAPSHOT,
                "choices": [{"index": 0, "finish_reason": "stop"}],
            }

    transport = DiagnosticTransport()
    api_key = "provider-test-secret"
    report: Any = run_provider_pilot(
        project_root=PROJECT_ROOT,
        environment={
            "ECOMSRE_LLM_BASE_URL": "https://provider.invalid/v1",
            "ECOMSRE_LLM_API_KEY": api_key,
            "ECOMSRE_LLM_MODEL": MODEL_SNAPSHOT,
        },
        transport=transport,
    )

    assert report["status"] == "FAILED"
    assert report["provider_call_count"] == 9
    assert [item["failure_code"] for item in report["case_results"]] == [
        "PROVIDER_TIMEOUT",
        "PROVIDER_CONNECTION",
        "CHOICE_METADATA_INVALID",
    ] * 3
    serialized = json.dumps(report)
    assert api_key not in serialized
    assert "raw-timeout-marker" not in serialized
    assert "raw-connection-marker" not in serialized


def test_provider_diagnosis_failure_codes_use_only_safe_validation_categories() -> None:
    class InvalidDiagnosisTransport:
        def __init__(self) -> None:
            self.calls = 0

        def post_json(self, **kwargs):
            self.calls += 1
            envelope = json.loads(kwargs["payload"]["messages"][1]["content"])
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
                "missing_evidence": ["One read-only source is still required."],
                "confidence": 0.2,
                "decision_rationale": "The available evidence is insufficient.",
                "recommended_next_action": (
                    "Collect additional read-only telemetry evidence."
                ),
            }
            failure_kind = (self.calls - 1) % 3
            if failure_kind == 0:
                del diagnosis["decision_rationale"]
            elif failure_kind == 1:
                diagnosis["decision"] = "raw-invalid-decision-marker"
            else:
                diagnosis["missing_evidence"] = []
            return {
                "id": f"completion-{self.calls}",
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
                                    "id": f"tool-{self.calls}",
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

    report: Any = run_provider_pilot(
        project_root=PROJECT_ROOT,
        environment={
            "ECOMSRE_LLM_BASE_URL": "https://provider.invalid/v1",
            "ECOMSRE_LLM_API_KEY": "provider-test-secret",
            "ECOMSRE_LLM_MODEL": MODEL_SNAPSHOT,
        },
        transport=InvalidDiagnosisTransport(),
    )

    assert [item["failure_code"] for item in report["case_results"]] == [
        "DIAGNOSIS_REQUIRED_FIELD_MISSING",
        "DIAGNOSIS_ENUM_INVALID",
        "DIAGNOSIS_DECISION_SEMANTICS_INVALID",
    ] * 3
    serialized = json.dumps(report)
    assert "raw-invalid-decision-marker" not in serialized
    assert "provider-test-secret" not in serialized


def test_provider_connection_codes_use_only_safe_exception_categories() -> None:
    class ClassifiedConnectionTransport:
        def __init__(self) -> None:
            self.calls = 0

        def post_json(self, **_kwargs):
            self.calls += 1
            failure_kind = (self.calls - 1) % 3
            if failure_kind == 0:
                cause: Exception = urllib.error.HTTPError(
                    "https://raw-private-url.invalid",
                    503,
                    "raw-http-marker",
                    {},
                    None,
                )
            elif failure_kind == 1:
                cause = urllib.error.URLError(
                    socket.gaierror("raw-dns-marker")
                )
            else:
                cause = urllib.error.URLError(ssl.SSLError("raw-tls-marker"))
            try:
                raise cause
            except Exception as error:
                raise ConnectionError("raw-outer-marker") from error

    report: Any = run_provider_pilot(
        project_root=PROJECT_ROOT,
        environment={
            "ECOMSRE_LLM_BASE_URL": "https://provider.invalid/v1",
            "ECOMSRE_LLM_API_KEY": "provider-test-secret",
            "ECOMSRE_LLM_MODEL": MODEL_SNAPSHOT,
        },
        transport=ClassifiedConnectionTransport(),
    )

    assert [item["failure_code"] for item in report["case_results"]] == [
        "PROVIDER_HTTP_5XX",
        "PROVIDER_DNS",
        "PROVIDER_TLS",
    ] * 3
    serialized = json.dumps(report)
    for forbidden in (
        "raw-private-url",
        "raw-http-marker",
        "raw-dns-marker",
        "raw-tls-marker",
        "raw-outer-marker",
        "provider-test-secret",
    ):
        assert forbidden not in serialized


def test_make_targets_and_ci_are_offline_wired(tmp_path: Path) -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    report_path = tmp_path / "report.json"
    rendered: dict[str, str] = {}
    for target in (
        "phase5a-test",
        "phase5a-compare",
        "phase5a-verify",
        "phase5a-demo",
        "phase5a-provider-pilot",
        "phase5a-provider-request-shapes",
        "phase5a-provider-order-isolation",
    ):
        completed = subprocess.run(
            ["make", "-n", target, f"PHASE5A_REPORT={report_path}"],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
        rendered[target] = completed.stdout

    assert "pytest tests/phase5a" in rendered["phase5a-test"]
    assert "ecomsre.phase5a.cli compare" in rendered["phase5a-compare"]
    assert "ecomsre.phase5a.cli verify" in rendered["phase5a-verify"]
    assert "ecomsre.phase5a.cli demo" in rendered["phase5a-demo"]
    assert "ecomsre.phase5a.cli provider-pilot" in rendered[
        "phase5a-provider-pilot"
    ]
    assert "ecomsre.phase5a.cli provider-request-shapes" in rendered[
        "phase5a-provider-request-shapes"
    ]
    assert "ecomsre.phase5a.cli provider-order-isolation" in rendered[
        "phase5a-provider-order-isolation"
    ]
    workflow = (PROJECT_ROOT / ".github/workflows/agent-mainline.yml").read_text()
    for target in (
        "phase5a-test",
        "phase5a-compare",
        "phase5a-verify",
        "phase5a-demo",
    ):
        assert f"make {target}" in workflow


def test_replay_worker_is_importable_only_via_isolated_cli() -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "-m", "ecomsre.phase5a.cli", "--help"],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
