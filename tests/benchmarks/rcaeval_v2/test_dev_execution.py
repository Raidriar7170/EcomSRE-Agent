from __future__ import annotations

from pathlib import Path

from ecomsre_rcaeval.contracts import Architecture
from ecomsre_rcaeval_v2.dev_execution import (
    _v1_scheduled,
    freeze_private_schedules,
    load_private_schedule,
)
from ecomsre_rcaeval_v2.schedule import (
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
    assert len(load_private_schedule(tmp_path / "schedule" / "design-schedule.json")) == 360
    assert len(
        load_private_schedule(tmp_path / "schedule" / "dev-validation-schedule.json")
    ) == 480
    assert len(load_private_schedule(tmp_path / "schedule" / "smoke-schedule.json")) == 72
    assert all(path.stat().st_mode & 0o077 == 0 for path in (tmp_path / "schedule").iterdir())


def test_v1_reference_adapter_preserves_frozen_architecture_and_run_id() -> None:
    assignments = build_split_assignments(synthetic_identities(), seed=SPLIT_SEED)
    record = next(
        item
        for item in build_schedule(assignments, SplitName.DESIGN, seed=SPLIT_SEED)
        if item.variant is Variant.SINGLE_V1_REFERENCE
    )

    class Case:
        case_id = "re2-ob-case-0001"

    scheduled = _v1_scheduled(record, Case())  # type: ignore[arg-type]
    assert scheduled.run_id == record.run_id
    assert scheduled.case_id == "re2-ob-case-0001"
    assert scheduled.architecture is Architecture.SINGLE
