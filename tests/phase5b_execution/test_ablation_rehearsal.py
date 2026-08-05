from __future__ import annotations

from pathlib import Path
import pytest

from scripts.phase5b_execution.ablation import (
    _AblationStore,
    UnsupportedFrozenAblationExecutor,
    build_ablation_schedule,
    run_ablation_schedule,
    run_mock_ablation_rehearsal,
)
from scripts.phase5b_execution.contracts import TerminalStatus


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = PROJECT_ROOT / "config/phase5b/ablation-registry.v1.json"


def test_frozen_ablation_schedule_is_exact_and_deterministic() -> None:
    first = build_ablation_schedule(REGISTRY)
    second = build_ablation_schedule(REGISTRY)

    assert first == second
    assert len(first) == 38
    assert len({item.ablation_run_id for item in first}) == 38
    assert sum(item.run_kind == "DIAGNOSIS" for item in first) == 36
    assert sum(item.run_kind == "REMEDIATION" for item in first) == 2
    assert all(item.primary_eligible is False for item in first)


def test_38_run_mock_ablation_rehearsal_is_primary_ineligible(
    tmp_path: Path,
) -> None:
    waits: list[float] = []
    report = run_mock_ablation_rehearsal(
        registry_path=REGISTRY,
        output_root=tmp_path,
        sleeper=waits.append,
    )

    assert waits == [2.0] * 37
    assert report["schema_version"] == "phase5b.mock-ablation-rehearsal.v1"
    assert report["ablation_run_count"] == 38
    assert report["unique_terminal_records"] == 38
    assert report["diagnosis_run_count"] == 36
    assert report["remediation_run_count"] == 2
    assert report["primary_eligible"] is False
    assert report["primary_disposition"] == "PRIMARY_INELIGIBLE"
    assert report["executor_calls_this_process"] == 38
    assert "mock_transport_calls" not in report
    assert report["provider_network_calls"] == 0
    assert report["ground_truth_reads"] == 0
    assert report["all_checkpoints_closed"] is True
    assert len(list((tmp_path / "ablation-raw").glob("*.json"))) == 38
    assert list((tmp_path / "ablation-attempts").glob("*.json")) == []

    with pytest.raises(ValueError, match="evidence class"):
        run_ablation_schedule(
            registry_path=REGISTRY,
            output_root=tmp_path,
            executor=UnsupportedFrozenAblationExecutor(),
            sleeper=lambda _seconds: None,
            evidence_class="ACTUAL_SCORED",
        )


def test_ablation_resume_seals_interrupted_attempt_without_retry(
    tmp_path: Path,
) -> None:
    first = build_ablation_schedule(REGISTRY)[0]
    _AblationStore(tmp_path).start(
        first,
        evidence_class="MOCK_EXECUTION_REHEARSAL",
    )
    waits: list[float] = []

    report = run_mock_ablation_rehearsal(
        registry_path=REGISTRY,
        output_root=tmp_path,
        sleeper=waits.append,
    )

    recovered = _AblationStore(tmp_path).load(first.ablation_run_id)
    assert recovered is not None
    assert recovered.terminal_status is TerminalStatus.PROVIDER_TRANSPORT_FAILURE
    assert recovered.failure_code == "INTERRUPTED_AFTER_ATTEMPT"
    assert report["executor_calls_this_process"] == 37
    assert waits == [2.0] * 37


def test_unimplemented_frozen_ablations_fail_terminal_without_provider(
    tmp_path: Path,
) -> None:
    report = run_ablation_schedule(
        registry_path=REGISTRY,
        output_root=tmp_path,
        executor=UnsupportedFrozenAblationExecutor(),
        sleeper=lambda _seconds: None,
        evidence_class="ACTUAL_SCORED",
    )

    assert report["schema_version"] == "phase5b.ablation-execution-progress.v1"
    assert report["unique_terminal_records"] == 38
    assert report["provider_network_calls"] == 0
    for path in (tmp_path / "ablation-raw").glob("*.json"):
        record = _AblationStore(tmp_path).load(path.stem)
        assert record is not None
        assert record.terminal_status is TerminalStatus.WORKFLOW_FAILURE
        assert record.failure_code == "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"


def test_actual_ablation_interruption_retains_not_implemented_zero_call_policy(
    tmp_path: Path,
) -> None:
    first = build_ablation_schedule(REGISTRY)[0]
    store = _AblationStore(tmp_path)
    store.start(first, evidence_class="ACTUAL_SCORED")

    recovered = store.recover_interrupted(first)

    assert recovered is not None
    assert recovered.terminal_status is TerminalStatus.WORKFLOW_FAILURE
    assert recovered.failure_code == "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
    assert recovered.failure_stage == "ABLATION_IMPLEMENTATION"
    assert recovered.provider_attempted is False
    assert recovered.usage.provider_network_calls == 0


def test_actual_ablation_executor_exception_retains_zero_call_policy(
    tmp_path: Path,
) -> None:
    class RaisingExecutor:
        def __call__(self, _request):
            raise RuntimeError("synthetic executor failure")

    report = run_ablation_schedule(
        registry_path=REGISTRY,
        output_root=tmp_path,
        executor=RaisingExecutor(),
        sleeper=lambda _seconds: None,
        evidence_class="ACTUAL_SCORED",
    )

    assert report["provider_network_calls"] == 0
    store = _AblationStore(tmp_path)
    for request in build_ablation_schedule(REGISTRY):
        record = store.load(request.ablation_run_id)
        assert record is not None
        assert record.terminal_status is TerminalStatus.WORKFLOW_FAILURE
        assert record.failure_code == "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
        assert record.failure_stage == "ABLATION_IMPLEMENTATION"
        assert record.provider_attempted is False
        assert record.usage.model_calls == 0
        assert record.usage.tool_calls == 0
        assert record.usage.combined_tokens == 0
