from __future__ import annotations

from pathlib import Path

from ecomsre.dta_v2.v23.provider_smoke_v2341 import (
    ReplayThenLiveRegistrationAliasTransportV2341,
    RegistrationSmokeRepairDiagnosticV2341,
    RegistrationSmokeRepairRecordV2341,
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


def test_smoke_manifest_preserves_one_execution_and_binds_fix_one() -> None:
    manifest = load_smoke_manifest_v2341(
        ROOT / "config/dta-v2341/smoke/manifest.json"
    )

    assert manifest.current_execution_count == 1
    assert manifest.fixed_evaluation_execution_count == 0
    assert manifest.real_fix_count == 1
    assert manifest.repair_record_path == (
        "docs/analysis/dta-v2341-provider-smoke-repair-1.json"
    )
    assert manifest.repair_record_sha256 == (
        "ee52efd2e997a4d756c03ab056d3ea3c1fb5094577e13a892a3df7150a1622f5"
    )
    assert manifest.prior_manifest_sha256 == (
        "0e8d7e0215a854d69d6841552c29fbff3ac5377e70c286136b4e15f3a1f48811"
    )
    assert manifest.planned_task_count == 8
    assert manifest.planned_provider_called_task_count == 6
    assert manifest.terminal == "DTA_V2341_SMOKE_SURFACE_FROZEN"


def test_smoke_fix_one_records_bind_the_consumed_campaign() -> None:
    diagnostic = RegistrationSmokeRepairDiagnosticV2341.model_validate_json(
        (
            ROOT
            / "docs/analysis/dta-v2341-provider-smoke-fix1-diagnostic.json"
        ).read_bytes()
    )
    repair = RegistrationSmokeRepairRecordV2341.model_validate_json(
        (
            ROOT / "docs/analysis/dta-v2341-provider-smoke-repair-1.json"
        ).read_bytes()
    )

    assert diagnostic.execution_count == repair.execution_count == 1
    assert diagnostic.fixed_evaluation_execution_count == 0
    assert repair.fixed_evaluation_execution_count == 0
    assert len(diagnostic.raw_bindings) == 14
    assert diagnostic.original_provider_call_count == 7
    assert diagnostic.diagnostic_sha256 == repair.diagnostic_sha256
    assert diagnostic.raw_bindings_sha256 == repair.raw_bindings_sha256
    assert diagnostic.blocker_sha256 == repair.blocker_sha256


def test_replay_then_live_transport_does_not_repeat_completed_network_calls() -> None:
    live_bodies: list[str] = []

    def live(body: str) -> str:
        live_bodies.append(body)
        return "live"

    transport = ReplayThenLiveRegistrationAliasTransportV2341(
        replayed_responses=("prior-a", "prior-b"),
        live_transport=live,
    )

    assert transport("ignored-a") == "prior-a"
    assert transport("ignored-b") == "prior-b"
    assert transport("new") == "live"
    assert transport.replayed_call_count == 2
    assert transport.live_call_count == 1
    assert live_bodies == ["new"]
