from __future__ import annotations

from collections import Counter
import hashlib

from ecomsre_rcaeval_v2.schedule import (
    DESIGN_VARIANTS,
    DEV_VALIDATION_VARIANTS,
    CaseIdentity,
    SplitName,
    Variant,
    build_schedule,
    build_smoke_schedule,
    build_split_assignments,
    schedule_budget_summary,
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
            system=system,
            root_cause_service=service,
            fault=fault,
            instance=str(instance),
        )
        for system, system_services in services.items()
        for service in system_services
        for fault in ("cpu", "mem", "disk", "delay", "loss", "socket")
        for instance in (1, 2, 3)
    )


def test_design_and_validation_schedules_are_balanced_and_unique() -> None:
    assignments = build_split_assignments(synthetic_identities(), seed=20260807)
    design = build_schedule(assignments, SplitName.DESIGN, seed=20260807)
    validation = build_schedule(
        assignments, SplitName.DEV_VALIDATION, seed=20260807
    )

    assert len(design) == 360
    assert len(validation) == 480
    assert {item.variant for item in design} == set(DESIGN_VARIANTS)
    assert {item.variant for item in validation} == set(DEV_VALIDATION_VARIANTS)
    assert len({item.run_id for item in design + validation}) == 840
    assert Counter(item.arm_position for item in design) == {
        position: 60 for position in range(1, 7)
    }
    assert Counter(item.arm_position for item in validation) == {
        position: 120 for position in range(1, 5)
    }
    assert Counter(
        (item.variant, item.arm_position) for item in design
    ).most_common(1)[0][1] == 10
    assert set(Counter((item.variant, item.arm_position) for item in design).values()) == {
        10
    }
    assert set(
        Counter((item.variant, item.arm_position) for item in validation).values()
    ) == {30}


def test_schedule_is_input_order_invariant_and_rotates_base_arms() -> None:
    assignments = build_split_assignments(synthetic_identities(), seed=20260807)
    forward = build_schedule(assignments, SplitName.DESIGN, seed=20260807)
    reverse = build_schedule(
        tuple(reversed(assignments)), SplitName.DESIGN, seed=20260807
    )
    assert forward == reverse
    assert tuple(item.variant for item in forward[:6]) == DESIGN_VARIANTS
    assert tuple(item.variant for item in forward[6:12]) == (
        DESIGN_VARIANTS[1:] + DESIGN_VARIANTS[:1]
    )


def test_run_id_matches_the_preregistered_exact_formula() -> None:
    assignments = build_split_assignments(synthetic_identities(), seed=20260807)
    record = build_schedule(assignments, SplitName.DESIGN, seed=20260807)[0]
    identity = record.identity
    identity_bytes = b"\0".join(
        value.encode("utf-8")
        for value in (
            identity.system,
            identity.root_cause_service,
            identity.fault,
            identity.instance,
        )
    )
    expected = hashlib.sha256(
        b"\0".join(
            (
                b"rcaeval-re2-v2-dev-v1",
                b"DESIGN",
                b"20260807",
                identity_bytes,
                record.variant.value.encode("utf-8"),
            )
        )
    ).hexdigest()[:32]

    assert record.run_id == expected


def test_smoke_is_exact_twelve_case_subset_of_design_with_no_extra_budget() -> None:
    assignments = build_split_assignments(synthetic_identities(), seed=20260807)
    design = build_schedule(assignments, SplitName.DESIGN, seed=20260807)
    smoke = build_smoke_schedule(assignments, design, seed=20260807)

    assert len(smoke) == 72
    assert set(smoke).issubset(set(design))
    smoke_cases = {item.identity for item in smoke}
    assert len(smoke_cases) == 12
    assert Counter((item.system, item.fault) for item in smoke_cases) == {
        (system, fault): 1
        for system in ("RE2-OB", "RE2-SS")
        for fault in ("cpu", "mem", "disk", "delay", "loss", "socket")
    }
    assert all(
        {item.variant for item in smoke if item.identity == identity}
        == set(DESIGN_VARIANTS)
        for identity in smoke_cases
    )
    for system in ("RE2-OB", "RE2-SS"):
        for fault in ("cpu", "mem", "disk", "delay", "loss", "socket"):
            candidates = {
                item.identity: item.case_order_digest_sha256
                for item in design
                if item.system == system and item.fault == fault
            }
            expected = min(candidates, key=lambda identity: candidates[identity])
            assert expected in smoke_cases


def test_schedule_budget_is_exact_and_below_hard_cap() -> None:
    summary = schedule_budget_summary()
    assert summary.design_max_provider_operations == 1200
    assert summary.dev_validation_max_provider_operations == 1320
    assert summary.worst_case_provider_operations == 2520
    assert summary.hard_max_provider_operations == 2600
    assert summary.provider_operation_headroom == 80
    assert summary.smoke_additional_operations == 0
    assert summary.worst_case_provider_operations <= summary.hard_max_provider_operations


def test_validation_excludes_fixed_and_dynamic_v1_reference() -> None:
    assert Variant.FIXED_V1_REFERENCE not in DEV_VALIDATION_VARIANTS
    assert Variant.DYNAMIC_V1_REFERENCE not in DEV_VALIDATION_VARIANTS
