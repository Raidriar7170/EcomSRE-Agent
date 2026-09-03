import importlib.util
from pathlib import Path

import pytest


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
