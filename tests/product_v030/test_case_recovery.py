import importlib.util
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
