from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from ecomsre.dta_v2.read_only_smoke import (
    CleanupObservation,
    SmokeFailureCode,
    SmokeTerminal,
    run_owned_read_only_smoke_attempt,
)
from ecomsre.dta_v2.read_tools import FakeReadBackend


@dataclass
class FakeLifecycle:
    fail_stage: str | None = None
    baseline_before: str = "a" * 64
    baseline_after: str = "a" * 64
    cleanup: CleanupObservation = CleanupObservation.clean()
    cleanup_calls: int = 0

    def admit(self) -> None:
        if self.fail_stage == "admit":
            raise RuntimeError("admit")

    def start(self) -> None:
        if self.fail_stage == "start":
            raise RuntimeError("partial start")

    def wait_ready(self) -> None:
        if self.fail_stage == "ready":
            raise RuntimeError("ready")

    def authorize_reads(self):
        if self.fail_stage == "authority":
            raise RuntimeError("authority")
        return FakeReadBackend.healthy()

    def read_baseline_sha256(self) -> str:
        if self.fail_stage == "baseline":
            raise RuntimeError("baseline")
        value = self.baseline_before
        self.baseline_before = self.baseline_after
        return value

    def cleanup_owned(self, *, baseline_unchanged: bool) -> CleanupObservation:
        del baseline_unchanged
        self.cleanup_calls += 1
        if self.fail_stage == "cleanup":
            raise RuntimeError("cleanup")
        return self.cleanup


@pytest.mark.parametrize(
    ("stage", "code", "cleanup_calls"),
    (
        ("admit", SmokeFailureCode.ENVIRONMENT_ADMISSION_FAILED, 0),
        ("start", SmokeFailureCode.START_FAILED, 1),
        ("ready", SmokeFailureCode.READINESS_FAILED, 1),
        ("authority", SmokeFailureCode.AUTHORITY_ADMISSION_FAILED, 1),
        ("baseline", SmokeFailureCode.BASELINE_READ_FAILED, 1),
        ("cleanup", SmokeFailureCode.CLEANUP_FAILED, 1),
    ),
)
def test_every_lifecycle_failure_persists_typed_closure(
    tmp_path: Path, stage: str, code: SmokeFailureCode, cleanup_calls: int
) -> None:
    lifecycle = FakeLifecycle(fail_stage=stage)
    closure = run_owned_read_only_smoke_attempt(
        smoke_id="6" * 32,
        service="payment",
        private_root=tmp_path / stage,
        lifecycle=lifecycle,
    )
    assert closure.terminal is SmokeTerminal.FAIL
    assert closure.failure_code is code
    assert lifecycle.cleanup_calls == cleanup_calls
    assert (tmp_path / stage / "owned-read-only-smoke-closure.json").is_file()
    assert closure.fault_injection_count == 0
    assert closure.agent_call_count == 0
    assert closure.provider_call_count == 0
    assert closure.runbook_execution_count == 0
    assert closure.forward_mutation_count == 0
    assert closure.configuration_mutation_count == 0
    assert closure.service_mutation_count == 0


def test_post_read_baseline_drift_and_non_owned_cleanup_drift_fail_closed(
    tmp_path: Path,
) -> None:
    baseline = FakeLifecycle(baseline_after="b" * 64)
    first = run_owned_read_only_smoke_attempt(
        smoke_id="7" * 32,
        service="payment",
        private_root=tmp_path / "baseline-drift",
        lifecycle=baseline,
    )
    assert first.failure_code is SmokeFailureCode.POST_READ_BASELINE_MISMATCH

    cleanup_drift = FakeLifecycle(cleanup=CleanupObservation.non_owned_drift())
    second = run_owned_read_only_smoke_attempt(
        smoke_id="8" * 32,
        service="payment",
        private_root=tmp_path / "cleanup-drift",
        lifecycle=cleanup_drift,
    )
    assert second.failure_code is SmokeFailureCode.CLEANUP_BLOCKED


def test_evidence_persistence_failure_still_closes_attempt(tmp_path: Path) -> None:
    def fail_read(**kwargs):
        del kwargs
        raise PermissionError("evidence persistence")

    closure = run_owned_read_only_smoke_attempt(
        smoke_id="9" * 32,
        service="payment",
        private_root=tmp_path / "persist-fail",
        lifecycle=FakeLifecycle(),
        read_runner=fail_read,
    )
    assert closure.failure_code is SmokeFailureCode.EVIDENCE_PERSISTENCE_FAILED
    assert closure.cleanup_verdict == "CLEAN"
    assert (tmp_path / "persist-fail" / "owned-read-only-smoke-closure.json").is_file()


def test_read_exception_still_closes_attempt_as_typed_read_failure(
    tmp_path: Path,
) -> None:
    def fail_read(**kwargs):
        del kwargs
        raise RuntimeError("read source failed")

    lifecycle = FakeLifecycle()
    closure = run_owned_read_only_smoke_attempt(
        smoke_id="a" * 32,
        service="payment",
        private_root=tmp_path / "read-fail",
        lifecycle=lifecycle,
        read_runner=fail_read,
    )
    assert closure.failure_code is SmokeFailureCode.READ_TOOL_FAILED
    assert lifecycle.cleanup_calls == 1
    assert closure.journal[-1].stage.value == "CLOSED"
