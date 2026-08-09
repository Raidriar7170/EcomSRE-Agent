from __future__ import annotations

from collections import Counter, defaultdict
import hashlib

from ecomsre_rcaeval_v2.dev3_schedule import (
    DESIGN_VARIANTS,
    DEV_VALIDATION_VARIANTS,
    PROTOCOL_ID,
    SCHEDULE_DOMAIN,
    SCHEDULE_SEED,
    ArchitectureFamily,
    Variant,
    build_schedule,
    build_smoke_schedule,
)
from ecomsre_rcaeval_v2.dev2_schedule import (
    SCHEDULE_SEED as DEV2_SCHEDULE_SEED,
    build_schedule as build_dev2_schedule,
)
from ecomsre_rcaeval_v2.schedule import (
    SPLIT_SEED,
    CaseIdentity,
    SplitName,
    build_split_assignments,
)


def _identities() -> tuple[CaseIdentity, ...]:
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


def _schedules():
    assignments = build_split_assignments(_identities(), seed=SPLIT_SEED)
    design = build_schedule(assignments, SplitName.DESIGN, seed=SCHEDULE_SEED)
    validation = build_schedule(
        assignments, SplitName.DEV_VALIDATION, seed=SCHEDULE_SEED
    )
    smoke = build_smoke_schedule(assignments, design, seed=SCHEDULE_SEED)
    return assignments, smoke, design, validation


def test_dev3_schedule_has_global_and_family_local_positions_for_all_rotations() -> None:
    _assignments, _smoke, design, validation = _schedules()
    cases: dict[CaseIdentity, list] = defaultdict(list)
    for record in design:
        cases[record.identity].append(record)

    assert len(cases) == 60
    assert {
        tuple(record.variant for record in records) for records in cases.values()
    } == {
        DESIGN_VARIANTS[offset:] + DESIGN_VARIANTS[:offset]
        for offset in range(6)
    }
    for records in cases.values():
        assert {record.global_arm_position for record in records} == set(range(1, 7))
        assert all(record.arm_position == record.global_arm_position for record in records)
        for family in ArchitectureFamily:
            family_records = [
                record for record in records if record.architecture_family is family
            ]
            assert [record.family_call_position for record in family_records] == [1, 2, 3]
            assert [record.global_arm_position for record in family_records] == sorted(
                record.global_arm_position for record in family_records
            )

    assert Counter(record.global_arm_position for record in validation) == {
        position: 120 for position in range(1, 5)
    }
    assert all(1 <= record.family_call_position <= 3 for record in validation)


def test_known_global_six_v1_record_maps_to_family_three() -> None:
    _assignments, _smoke, design, _validation = _schedules()
    record = next(
        item
        for item in design
        if item.global_arm_position == 6
        and item.architecture_family is ArchitectureFamily.V1_REFERENCE
    )
    assert record.family_call_position == 3


def test_dev3_schedule_counts_subset_disjointness_and_new_id_domain() -> None:
    assignments, smoke, design, validation = _schedules()
    smoke_ids = {record.run_id for record in smoke}
    design_ids = {record.run_id for record in design}
    validation_ids = {record.run_id for record in validation}

    assert PROTOCOL_ID == "rcaeval-re2-v2-dev.3"
    assert SCHEDULE_DOMAIN == "rcaeval-re2-v2-dev3-schedule-v1"
    assert SCHEDULE_SEED == 20260810
    assert len(smoke_ids) == 72
    assert len(design_ids) == 360
    assert len(validation_ids) == 480
    assert smoke_ids < design_ids
    assert not design_ids & validation_ids
    assert {item.variant for item in design} == set(DESIGN_VARIANTS)
    assert {item.variant for item in validation} == set(DEV_VALIDATION_VARIANTS)

    dev1_ids = {
        hashlib.sha256(
            b"\0".join(
                (
                    b"rcaeval-re2-v2-dev.1",
                    record.split.value.encode(),
                    b"20260808",
                    b"\0".join(
                        value.encode()
                        for value in (
                            record.identity.system,
                            record.identity.root_cause_service,
                            record.identity.fault,
                            record.identity.instance,
                        )
                    ),
                    record.variant.value.replace("_dev3", "_dev1").encode(),
                )
            )
        ).hexdigest()[:32]
        for record in design + validation
    }
    assert not (design_ids | validation_ids) & dev1_ids
    dev2_ids = {
        record.run_id
        for record in (
            build_dev2_schedule(
                assignments, SplitName.DESIGN, seed=DEV2_SCHEDULE_SEED
            )
            + build_dev2_schedule(
                assignments, SplitName.DEV_VALIDATION, seed=DEV2_SCHEDULE_SEED
            )
        )
    }
    assert not (design_ids | validation_ids) & dev2_ids


def test_dev3_variant_names_are_versioned_without_changing_reference_names() -> None:
    assert Variant.SINGLE_V2.value == "single_v2_dev3"
    assert Variant.FIXED_V2.value == "fixed_v2_dev3"
    assert Variant.DYNAMIC_V2.value == "dynamic_v2_dev3"
    assert Variant.SINGLE_V1_REFERENCE.value == "single_v1_reference"
