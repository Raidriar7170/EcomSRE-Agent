from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre_rcaeval.contracts import Architecture
from ecomsre.model.gateway import OpenAICompatibleConfig
import ecomsre_rcaeval_v2.dev_execution as dev_execution
from ecomsre_rcaeval_v2.dev_execution import (
    _v1_scheduled,
    discover_case_index,
    execute_development_schedule,
    freeze_private_schedules,
    load_private_schedule,
)
from ecomsre_rcaeval_v2.schedule import (
    SCHEDULE_SEED,
    SPLIT_SEED,
    CaseIdentity,
    SplitName,
    Variant,
    build_schedule,
    build_split_assignments,
)


def synthetic_identities() -> tuple[CaseIdentity, ...]:
    services = {
        "RE2-OB": (
            "checkoutservice",
            "currencyservice",
            "emailservice",
            "productcatalogservice",
            "recommendationservice",
        ),
        "RE2-SS": ("carts", "catalogue", "orders", "payment", "user"),
    }
    return tuple(
        CaseIdentity(
            system=system,  # type: ignore[arg-type]
            root_cause_service=service,
            fault=fault,  # type: ignore[arg-type]
            instance=str(instance),
        )
        for system, system_services in services.items()
        for service in system_services
        for fault in ("cpu", "mem", "disk", "delay", "loss", "socket")
        for instance in (1, 2, 3)
    )


def test_private_schedule_freeze_is_exact_and_create_once(tmp_path: Path) -> None:
    assignments = build_split_assignments(synthetic_identities(), seed=SPLIT_SEED)
    first = freeze_private_schedules(assignments, tmp_path / "schedule")
    second = freeze_private_schedules(assignments, tmp_path / "schedule")

    assert first == second
    assert (
        len(load_private_schedule(tmp_path / "schedule" / "design-schedule.json"))
        == 360
    )
    assert (
        len(
            load_private_schedule(
                tmp_path / "schedule" / "dev-validation-schedule.json"
            )
        )
        == 480
    )
    assert (
        len(load_private_schedule(tmp_path / "schedule" / "smoke-schedule.json")) == 72
    )
    assert all(
        path.stat().st_mode & 0o077 == 0 for path in (tmp_path / "schedule").iterdir()
    )


def test_v1_reference_adapter_preserves_frozen_architecture_and_run_id() -> None:
    assignments = build_split_assignments(synthetic_identities(), seed=SPLIT_SEED)
    record = next(
        item
        for item in build_schedule(assignments, SplitName.DESIGN, seed=SCHEDULE_SEED)
        if item.variant is Variant.SINGLE_V1_REFERENCE
    )

    class Case:
        case_id = "re2-ob-case-0001"

    scheduled = _v1_scheduled(record, Case())  # type: ignore[arg-type]
    assert scheduled.run_id == record.run_id
    assert scheduled.case_id == "re2-ob-case-0001"
    assert scheduled.architecture is Architecture.SINGLE


def test_case_discovery_opens_only_schedule_selected_design_identity(
    tmp_path: Path,
) -> None:
    ob_root = tmp_path / "RE2-OB"
    ss_root = tmp_path / "RE2-SS"
    selected = CaseIdentity(
        system="RE2-OB",
        root_cause_service="checkoutservice",
        fault="mem",
        instance="1",
    )
    case_root = ob_root / "checkoutservice_mem" / "1"
    case_root.mkdir(parents=True)
    ss_root.mkdir()
    for name in ("simple_metrics.csv", "logs.csv", "traces.csv"):
        (case_root / name).write_text("time,value\n0,1\n", encoding="utf-8")
    (case_root / "inject_time.txt").write_text("1\n", encoding="utf-8")

    cases = discover_case_index(ob_root, ss_root, {selected})

    assert set(cases) == {selected}
    assert cases[selected].root == case_root


def test_missing_evaluation_root_blocks_provider_construction_and_run_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assignments = build_split_assignments(synthetic_identities(), seed=SPLIT_SEED)
    scheduled = build_schedule(assignments, SplitName.DESIGN, seed=SCHEDULE_SEED)[0]
    provider_constructions = 0

    def fail_verification(*args: object, **kwargs: object) -> None:
        raise ValueError("evaluation root lock is missing or invalid")

    def forbidden_provider(*args: object, **kwargs: object) -> None:
        nonlocal provider_constructions
        provider_constructions += 1
        raise AssertionError("provider construction must remain unreachable")

    monkeypatch.setattr(dev_execution, "verify_evaluation_root", fail_verification)
    monkeypatch.setattr(dev_execution, "new_v1_reference_provider", forbidden_provider)
    monkeypatch.setattr(dev_execution, "new_v2_provider", forbidden_provider)

    with pytest.raises(ValueError, match="evaluation root lock"):
        execute_development_schedule(
            (scheduled,),
            cases={},
            provider_config=OpenAICompatibleConfig(
                base_url="https://provider.invalid",
                api_key="unused",
                model="unused",
            ),
            control_root=tmp_path / "control",
            private_run_root=tmp_path / "output",
            execution_phase="smoke",
        )

    assert provider_constructions == 0
    assert not list(tmp_path.rglob("run-attempt.json"))
