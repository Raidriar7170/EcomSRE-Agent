import importlib.util
import json
from pathlib import Path

import pytest


def test_case_rejects_a_different_active_baseline():
    path = Path(__file__).resolve().parents[2] / "scripts/product_v030/run_live_case.py"
    spec = importlib.util.spec_from_file_location("live_case_baseline_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    baseline = {"baseline_id": "base-one", "baseline_sha256": "a" * 64}
    module.require_case_baseline(baseline, baseline)
    for field in baseline:
        with pytest.raises(ValueError, match="case Baseline binding differs"):
            module.require_case_baseline({**baseline, field: "different"}, baseline)


def test_control_traffic_covers_frozen_metrics_window_without_changing_positives():
    path = Path(__file__).resolve().parents[2] / "scripts/product_v030/run_live_case.py"
    spec = importlib.util.spec_from_file_location("live_case_window_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payment, duration = module.case_traffic_profile("C1")
    assert payment.request_seed == 30003
    assert payment.maximum_request_count == 10
    assert payment.requests_per_second == 1 / 30
    assert duration == 300
    for name, seed in (("N0-A", 30001), ("N0-B", 30002)):
        profile, duration = module.case_traffic_profile(name)
        assert (profile.request_seed, profile.maximum_request_count) == (seed, 30)
        assert profile.requests_per_second == 1
        assert duration == 60
    for name, seed in (("P1", 31001), ("P2", 31002), ("P3", 31003), ("H1", 32001)):
        profile, duration = module.case_traffic_profile(name)
        assert (profile.request_seed, profile.maximum_request_count) == (seed, 3)
        assert profile.requests_per_second == 1
        assert duration == 60


def test_fault_write_then_readback_failure_still_restores_baseline():
    path = Path(__file__).resolve().parents[2] / "scripts/product_v030/run_live_case.py"
    spec = importlib.util.spec_from_file_location("live_case_recovery_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    class Controller:
        state = "BASELINE"

        def apply(self, state):
            calls.append(state)
            self.state = state
            if state == "QUEUE":
                raise RuntimeError("readback failed after write")
            return {"state": self.state}

        def read(self, state):
            raise AssertionError("mutation-possible recovery must not be read-only")

    controller = Controller()
    result = {"fault_write_attempt_count": 0, "fault_enable_count": 0}
    with pytest.raises(RuntimeError, match="after write"):
        try:
            module.apply_case_fault(controller, "QUEUE", result)
        finally:
            module.restore_case_flags(controller, result)
    assert calls == ["QUEUE", "BASELINE"]
    assert controller.state == "BASELINE"
    assert result == {"fault_write_attempt_count": 1, "fault_enable_count": 0}


def test_current_control_leakage_gate_is_bound_to_fresh_run(tmp_path):
    path = (
        Path(__file__).resolve().parents[2]
        / "scripts/product_v030/audit_existing_acquisition.py"
    )
    spec = importlib.util.spec_from_file_location("control_leakage_gate_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    private = tmp_path / "live-005"
    (private / "cases/N0-A").mkdir(parents=True)
    (private / "cases/N0-B").mkdir(parents=True)
    baseline_sha = "a" * 64
    (private / "baseline-result.json").write_text(
        json.dumps(
            {
                "status": "PRODUCT_V030_FRESH_BASELINE_READY",
                "environment": {"environment_id": "env-current"},
                "baseline": {
                    "baseline_id": "base-current",
                    "baseline_sha256": baseline_sha,
                },
            }
        )
    )
    expected_sources = {
        "CHANGES",
        "LOGS",
        "METRICS",
        "RESOURCES",
        "RUNTIME",
        "TRACES",
    }
    for case in ("N0-A", "N0-B"):
        incident_id = f"inc-{case.lower()}"
        (private / f"cases/{case}/result.json").write_text(
            json.dumps(
                {
                    "case": case,
                    "status": "PASS",
                    "leaked_tokens": [],
                    "supporting_refs_resolve": True,
                    "incident": {
                        "incident_id": incident_id,
                        "environment_id": "env-current",
                        "baseline_id": "base-current",
                        "baseline_sha256": baseline_sha,
                        "started_at": "2026-09-03T00:00:00Z",
                        "ended_at": "2026-09-03T00:01:00Z",
                    },
                    "diagnosis": {
                        "diagnosis_id": f"diag-{case.lower()}",
                        "terminal": "NO_INCIDENT",
                        "capability_limitations": [],
                        "supporting_evidence_refs": [],
                        "contradicting_evidence_refs": [],
                        "action_authority": "NONE",
                        "provider_calls": 0,
                        "agent_writes": 0,
                        "runbook_executions": 0,
                    },
                }
            )
        )
        (private / f"cases/{case}/evidence.json").write_text(
            json.dumps(
                {
                    "incident_id": incident_id,
                    "diagnosis_id": f"diag-{case.lower()}",
                    "supporting_evidence_refs": [],
                    "contradicting_evidence_refs": [],
                    "objects": [
                        {
                            "source": source,
                            "evidence_ref": f"e:{source.lower()}",
                            "payload": {"summary": "observer-safe telemetry"},
                        }
                        for source in sorted(expected_sources)
                    ],
                }
            )
        )
    historical = tmp_path / "historical.json"
    historical.write_text(
        json.dumps(
            {
                "status": "PASS",
                "leaked_tokens": [],
                "capability_limitations": ["SOURCE_TRACES_PARTIAL"],
            }
        )
    )

    result = module.build_current_control_leakage_gate(private, historical)

    assert result["status"] == "PASS"
    assert result["environment_id"] == "env-current"
    assert result["baseline_id"] == "base-current"
    assert result["capability_limitations"] == []
    assert [item["case"] for item in result["current_control_checks"]] == [
        "N0-A",
        "N0-B",
    ]
    assert all(
        set(item["sources"]) == expected_sources
        for item in result["current_control_checks"]
    )
    assert result["historical_enabled_fault_audit"]["status"] == "PASS"

    n0a_result_path = private / "cases/N0-A/result.json"
    n0a_evidence_path = private / "cases/N0-A/evidence.json"
    original_result = n0a_result_path.read_text()
    original_evidence = n0a_evidence_path.read_text()
    bad_result = json.loads(original_result)
    bad_result["incident"]["environment_id"] = "env-other"
    n0a_result_path.write_text(json.dumps(bad_result))
    with pytest.raises(ValueError, match="environment or Baseline"):
        module.build_current_control_leakage_gate(private, historical)
    n0a_result_path.write_text(original_result)

    bad_result = json.loads(original_result)
    bad_result["diagnosis"]["capability_limitations"] = ["SOURCE_LOGS_COVERAGE_GAP"]
    n0a_result_path.write_text(json.dumps(bad_result))
    with pytest.raises(ValueError, match="current control did not pass"):
        module.build_current_control_leakage_gate(private, historical)
    n0a_result_path.write_text(original_result)

    bad_evidence = json.loads(original_evidence)
    bad_evidence["objects"][0]["payload"]["summary"] = "feature flag leaked"
    n0a_evidence_path.write_text(json.dumps(bad_evidence))
    with pytest.raises(ValueError, match="leakage"):
        module.build_current_control_leakage_gate(private, historical)
    n0a_evidence_path.write_text(original_evidence)

    bad_result = json.loads(original_result)
    bad_result["diagnosis"]["supporting_evidence_refs"] = ["e:missing"]
    n0a_result_path.write_text(json.dumps(bad_result))
    with pytest.raises(ValueError, match="references"):
        module.build_current_control_leakage_gate(private, historical)
    n0a_result_path.write_text(original_result)

    bad_evidence = json.loads(original_evidence)
    bad_evidence["diagnosis_id"] = "diag-other"
    n0a_evidence_path.write_text(json.dumps(bad_evidence))
    with pytest.raises(ValueError, match="Evidence Diagnosis differs"):
        module.build_current_control_leakage_gate(private, historical)
    n0a_evidence_path.write_text(original_evidence)

    bad_evidence = json.loads(original_evidence)
    bad_evidence["objects"] = [
        item for item in bad_evidence["objects"] if item["source"] != "TRACES"
    ]
    n0a_evidence_path.write_text(json.dumps(bad_evidence))
    with pytest.raises(ValueError, match="source coverage"):
        module.build_current_control_leakage_gate(private, historical)
    n0a_evidence_path.write_text(original_evidence)

    with pytest.raises(ValueError, match="historical enabled-fault audit"):
        module.build_current_control_leakage_gate(
            private, historical, expected_historical_sha256="b" * 64
        )


def test_queue_case_root_must_equal_the_unique_queue_owner():
    path = Path(__file__).resolve().parents[2] / "scripts/product_v030/run_live_case.py"
    spec = importlib.util.spec_from_file_location("queue_root_gate_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    by_logical = {"checkout": "svc-checkout", "fraud-detection": "svc-fraud"}
    anomaly = type("Anomaly", (), {"service": "fraud-detection"})()
    assert module.queue_case_root_matches_unique_owner(
        {"root_service_ids": ["svc-fraud"]},
        [anomaly],
        by_logical,
        expected_owner="fraud-detection",
    )
    assert not module.queue_case_root_matches_unique_owner(
        {"root_service_ids": ["svc-checkout"]},
        [anomaly],
        by_logical,
        expected_owner="fraud-detection",
    )
    assert not module.queue_case_root_matches_unique_owner(
        {"root_service_ids": ["svc-fraud"]},
        [anomaly, type("Anomaly", (), {"service": "checkout"})()],
        by_logical,
        expected_owner="fraud-detection",
    )


def test_baseline_recovery_accepts_only_the_exact_prebaseline_failure():
    path = (
        Path(__file__).resolve().parents[2]
        / "scripts/product_v030/build_live_baseline.py"
    )
    spec = importlib.util.spec_from_file_location("baseline_recovery_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous = {
        "status": "PRODUCT_V030_BASELINE_PREPARATION_FAILED",
        "failure": {
            "type": "RuntimeError",
            "message": "fresh baseline healthy traffic did not complete",
        },
        "traffic_started_at": "2026-09-03T00:00:00Z",
        "traffic": {
            "attempted": 1,
            "succeeded": 0,
            "failed": 1,
            "stopped_on_error_budget": True,
        },
        "environment": {"environment_id": "env-current"},
        "verification": {"environment_id": "env-current"},
    }
    module.require_prebaseline_traffic_recovery(previous)
    for replacement in (
        {"status": "PRODUCT_V030_FRESH_BASELINE_READY"},
        {"baseline": {}},
        {"baseline_job_id": "job-existing"},
        {"traffic": {**previous["traffic"], "attempted": 2}},
        {"failure": {"type": "RuntimeError", "message": "different"}},
    ):
        with pytest.raises(ValueError, match="pre-Baseline traffic failure"):
            module.require_prebaseline_traffic_recovery(
                {**previous, **replacement}
            )
