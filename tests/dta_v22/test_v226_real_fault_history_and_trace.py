from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.real_fault_stage_trace_v226 import (
    RealFaultSafeFailureCodeV226,
    RealFaultStageV226,
    build_failed_real_fault_trace_v226,
    build_successful_real_fault_trace_v226,
)
from scripts.ci.verify_dta_v226_real_fault_history import (
    verify_dta_v226_real_fault_history,
)


ROOT = Path(__file__).resolve().parents[2]

PR67_FILES = {
    "docs/external-reviews/dta-v225-real-fault-final-review.md": (
        "b8648c01ee15399deabd232fc8f042dc91a8e1a485417e6e6015e3bb8e84849a"
    ),
    "docs/results/dta-v225-real-fault-shadow-comparison.json": (
        "3dde33b920da8cd55e4081e1b2e959a384a04e8279d127a4b7277dcef6856337"
    ),
    "docs/results/dta-v225-real-fault-shadow-comparison.md": (
        "6170bb6f2b7161568e31b2372d2a30cd1eea5ce8bc8cceda87d8d4abdc53335b"
    ),
    "docs/results/dta-v225-real-fault-shadow-error-analysis.md": (
        "16cffa748f118a5a9acfe279885388f215afa9c7f3b031b057d66cc72d1c9cf7"
    ),
}


def test_v226_history_binds_exact_pr67_public_bytes() -> None:
    manifest_path = ROOT / "config/dta-v226-real-fault/historical-results.v1.json"

    assert verify_dta_v226_real_fault_history() == 4
    manifest = json.loads(manifest_path.read_bytes())
    assert manifest["base_commit"] == "1c6520d706481f37b63a5b14c1fe8554b52d530b"
    assert {item["path"]: item["sha256"] for item in manifest["files"]} == PR67_FILES
    for relative, expected in PR67_FILES.items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected


def test_failed_trace_preserves_exact_stage_and_safe_code() -> None:
    trace = build_failed_real_fault_trace_v226(
        arm="CURRENT_RUNTIME_BUNDLE",
        completed_stages=(
            RealFaultStageV226.INPUT_VALIDATION,
            RealFaultStageV226.BOOTSTRAP_ACTION_BUILD,
        ),
        failure_stage=RealFaultStageV226.BOOTSTRAP_DISPATCH,
        safe_error_code=RealFaultSafeFailureCodeV226.BOOTSTRAP_READ_FAILED,
        local_exception_class="MemoryReadDispatchError",
        safe_validation_codes=("SOURCE_UNAVAILABLE",),
    )

    assert trace.last_completed_stage is RealFaultStageV226.BOOTSTRAP_ACTION_BUILD
    assert trace.failure_stage is RealFaultStageV226.BOOTSTRAP_DISPATCH
    assert trace.safe_error_code is RealFaultSafeFailureCodeV226.BOOTSTRAP_READ_FAILED
    assert trace.stage_events[-1].outcome == "FAILED"
    assert trace.stage_events[-1].stage is RealFaultStageV226.BOOTSTRAP_DISPATCH
    assert trace.stage_events[-1].safe_error_code is trace.safe_error_code
    assert trace.trace_sha256 == trace.recompute_sha256()
    serialized = json.dumps(trace.model_dump(mode="json"), sort_keys=True).lower()
    assert "chain_of_thought" not in serialized
    assert "reasoning" not in serialized


def test_success_trace_reaches_complete_without_failure_identity() -> None:
    trace = build_successful_real_fault_trace_v226(
        arm="MODEL_DIRECTED_RETRIEVAL",
        completed_stages=(
            RealFaultStageV226.INPUT_VALIDATION,
            RealFaultStageV226.BOOTSTRAP_BUILD,
            RealFaultStageV226.ACTION_SURFACE_BUILD,
            RealFaultStageV226.COMPLETE,
        ),
    )

    assert trace.last_completed_stage is RealFaultStageV226.COMPLETE
    assert trace.failure_stage is None
    assert trace.safe_error_code is None
    assert all(event.outcome == "COMPLETED" for event in trace.stage_events)


def test_failed_trace_rejects_failure_stage_already_marked_complete() -> None:
    with pytest.raises(ValueError, match="failure stage was already completed"):
        build_failed_real_fault_trace_v226(
            arm="CURRENT_RUNTIME_BUNDLE",
            completed_stages=(
                RealFaultStageV226.INPUT_VALIDATION,
                RealFaultStageV226.BOOTSTRAP_ACTION_BUILD,
            ),
            failure_stage=RealFaultStageV226.BOOTSTRAP_ACTION_BUILD,
            safe_error_code=RealFaultSafeFailureCodeV226.INTERNAL_CONTRACT_FAILURE,
        )
