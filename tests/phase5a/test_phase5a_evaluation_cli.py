from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

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
    report = evaluation.run_capability_parity_evaluation(PROJECT_ROOT)

    assert report["status"] == "COMPLETED"
    assert returned == truth_reads
    assert len(returned) == 36


def test_evaluation_retains_36_runs_and_required_quality_gates() -> None:
    report = evaluation.run_capability_parity_evaluation(PROJECT_ROOT)

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

    report = run_provider_pilot(
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
    report = run_provider_pilot(
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
        for item in report["case_results"]
    )
    assert api_key not in json.dumps(report)


def test_failed_provider_attempts_retain_reserved_outer_budget_usage() -> None:
    class TimeoutTransport:
        def post_json(self, **_kwargs):
            raise TimeoutError

    report = run_provider_pilot(
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
        and item["outer_budget"]["provider_attempted"] is True
        and item["outer_budget"]["provider_token_accounting"]
        == "RESERVED_UNKNOWN"
        and item["outer_budget"]["usage"]["model_calls"] >= 6
        for item in report["case_results"]
    )


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
