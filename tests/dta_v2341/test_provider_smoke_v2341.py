from __future__ import annotations

from pathlib import Path

from ecomsre.dta_v2.v23.provider_smoke_v2341 import (
    RegistrationSmokeModeV2341,
    RegistrationSmokeRoleV2341,
    build_smoke_data_v2341,
    load_smoke_manifest_v2341,
    load_smoke_tasks_v2341,
    load_smoke_truth_v2341,
    run_provider_smoke_v2341,
)


ROOT = Path(__file__).resolve().parents[2]


def test_fresh_smoke_data_is_exact_and_distinct_from_predecessor() -> None:
    generated_tasks, generated_truth = build_smoke_data_v2341(
        repository_root=ROOT
    )
    frozen_tasks = load_smoke_tasks_v2341(
        ROOT / "config/dta-v2341/smoke/tasks.json"
    )
    frozen_truth = load_smoke_truth_v2341(
        ROOT / "config/dta-v2341/smoke/truth.json"
    )

    assert generated_tasks == frozen_tasks
    assert generated_truth == frozen_truth
    assert len(frozen_tasks.tasks) == 8
    assert sum(item.provider_call_expected for item in frozen_tasks.tasks) == 6
    assert sum(
        item.role is RegistrationSmokeRoleV2341.HIDDEN_KNOWN_RECONSTRUCTION
        for item in frozen_tasks.tasks
    ) == 2
    assert sum(item.repair_path_fixture for item in frozen_tasks.tasks) >= 1
    assert sum(item.noncanonical_order_fixture for item in frozen_tasks.tasks) >= 1
    assert (
        (ROOT / "config/dta-v2341/smoke/tasks.json").read_bytes()
        != (ROOT / "config/dta-v234/evaluation/tasks.json").read_bytes()
    )


def test_deterministic_smoke_preflight_passes_all_eight_roles() -> None:
    tasks = load_smoke_tasks_v2341(
        ROOT / "config/dta-v2341/smoke/tasks.json"
    )
    truth = load_smoke_truth_v2341(
        ROOT / "config/dta-v2341/smoke/truth.json"
    )

    result = run_provider_smoke_v2341(
        repository_root=ROOT,
        task_set=tasks,
        truth_set=truth,
        mode=RegistrationSmokeModeV2341.DETERMINISTIC_FIXTURE,
    )

    assert result.terminal == "DTA_V2341_SMOKE_PREFLIGHT_PASS"
    assert result.execution_count == 0
    assert result.task_count == 8
    assert result.provider_called_task_count == 6
    assert result.zero_call_control_count == 2
    assert result.catalog_feasibility_pass_count == 6
    assert result.protocol_repair_count == 1
    assert result.canonical_order_failures == 0
    assert result.action_authority_violations == 0
    assert all(item.passed for item in result.tasks)
    assert all(
        item.provider_calls == 0
        for item in result.tasks
        if not item.provider_call_expected
    )


def test_smoke_manifest_starts_with_zero_real_executions() -> None:
    manifest = load_smoke_manifest_v2341(
        ROOT / "config/dta-v2341/smoke/manifest.json"
    )

    assert manifest.current_execution_count == 0
    assert manifest.fixed_evaluation_execution_count == 0
    assert manifest.planned_task_count == 8
    assert manifest.planned_provider_called_task_count == 6
    assert manifest.terminal == "DTA_V2341_SMOKE_SURFACE_FROZEN"
