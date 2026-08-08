from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecomsre_rcaeval.dataset import DevCase
from ecomsre_rcaeval_v2.dev3_admission import (
    _require_pristine_runtime_roots,
    rehearse_schedule_admission,
    v1_scheduled_run,
)
from ecomsre_rcaeval_v2.dev3_evidence import public_admission_gate
from ecomsre_rcaeval_v2.dev3_schedule import (
    SCHEDULE_SEED,
    build_schedule,
    build_smoke_schedule,
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


def _case(identity: CaseIdentity, root: Path) -> DevCase:
    case_root = root / identity.system / identity.root_cause_service / identity.fault / identity.instance
    case_root.mkdir(parents=True)
    paths = [case_root / "metrics.csv", case_root / "logs.csv"]
    if identity.system == "RE2-OB":
        paths.append(case_root / "traces.csv")
    for path in paths:
        path.write_text("time,value\n0,1\n", encoding="utf-8")
    return DevCase(
        case_id=f"{identity.system.lower()}-case-{identity.instance}",
        system=identity.system,
        root=case_root,
        metrics_path=paths[0],
        logs_path=paths[1],
        traces_path=paths[2] if len(paths) == 3 else None,
        inject_time=1,
        root_cause_service=identity.root_cause_service,
        fault=identity.fault,
        instance=identity.instance,
    )


def test_admission_zero_counts_are_observed_from_runtime_roots(tmp_path: Path) -> None:
    smoke = tmp_path / "smoke"
    smoke.mkdir()
    (smoke / ".evaluation-root-authority.json").write_text("{}\n", encoding="utf-8")
    _require_pristine_runtime_roots(smoke, tmp_path / "output", tmp_path / "design")
    (smoke / "provider-attempt-starts").mkdir()
    with pytest.raises(ValueError, match="zero pre-existing runtime artifacts"):
        _require_pristine_runtime_roots(smoke)


def test_full_admission_rehearses_72_360_480_without_provider_or_attempts(
    tmp_path: Path,
) -> None:
    assignments = build_split_assignments(_identities(), seed=SPLIT_SEED)
    design = build_schedule(assignments, SplitName.DESIGN, seed=SCHEDULE_SEED)
    validation = build_schedule(assignments, SplitName.DEV_VALIDATION, seed=SCHEDULE_SEED)
    smoke = build_smoke_schedule(assignments, design, seed=SCHEDULE_SEED)
    cases = {
        identity: _case(identity, tmp_path / "cases")
        for identity in {record.identity for record in design}
    }

    lock = rehearse_schedule_admission(
        assignments=assignments,
        smoke_schedule=smoke,
        design_schedule=design,
        validation_schedule=validation,
        design_cases=cases,
        control_root=tmp_path / "control",
        private_schedule_root=tmp_path / "private-schedules",
        output_root=tmp_path / "private-output",
        smoke_journal_root=tmp_path / "smoke-journal",
        design_journal_root=tmp_path / "design-journal",
        implementation_commit="a" * 40,
        split_lock_sha256="b" * 64,
        dev2_failure_audit_lock_sha256="d" * 64,
        retry_policy_lock_sha256="e" * 64,
        schedule_hashes={
            "smoke": "c" * 64,
            "design": "d" * 64,
            "validation": "e" * 64,
            "set": "f" * 64,
        },
        old_run_ids=set(),
        v1_external_schedule_sha256="1" * 64,
        preserved_schedule_hashes={
            "v2_dev_v1_design": "2" * 64,
            "v2_dev_v1_validation": "3" * 64,
            "v2_dev1_design": "4" * 64,
            "v2_dev1_validation": "5" * 64,
            "v2_dev2_design": "8" * 64,
            "v2_dev2_validation": "9" * 64,
        },
        preserved_roots={
            "v2_dev_v1": tmp_path / "old-dev-v1",
            "v2_dev1_control": tmp_path / "old-dev1-control",
            "v2_dev1_output": tmp_path / "old-dev1-output",
            "v2_dev2_control": tmp_path / "old-dev2-control",
            "v2_dev2_schedule": tmp_path / "old-dev2-schedule",
            "v2_dev2_output": tmp_path / "old-dev2-output",
            "v2_dev2_smoke": tmp_path / "old-dev2-smoke",
            "v2_dev2_design": tmp_path / "old-dev2-design",
        },
        preserved_evidence_hashes={
            "v2_dev_v1_tree": "1" * 64,
            "v2_dev1_control_tree": "2" * 64,
            "v2_dev1_output_tree": "3" * 64,
            "v2_dev2_control_tree": "4" * 64,
            "v2_dev2_schedule_tree": "5" * 64,
            "v2_dev2_output_tree": "6" * 64,
            "v2_dev2_smoke_tree": "7" * 64,
            "v2_dev2_design_tree": "8" * 64,
        },
    )

    assert lock.smoke.admitted == 72 and lock.smoke.rejected == 0
    assert lock.design.admitted == 360 and lock.design.rejected == 0
    assert lock.dev_validation_metadata.admitted == 480
    assert lock.dev_validation_metadata.values_accessed is False
    assert lock.v1_contract_construction.call_position_min == 1
    assert lock.v1_contract_construction.call_position_max == 3
    assert lock.v2_contract_construction.family_position_min == 1
    assert lock.v2_contract_construction.family_position_max == 3
    assert lock.provider_objects_constructed == 0
    assert lock.provider_calls == 0
    assert lock.run_attempts_created == 0
    assert lock.operation_attempts_created == 0
    assert lock.provider_attempts_created == 0
    assert not list(tmp_path.rglob("run-attempt.json"))


def test_known_global_six_v1_row_instantiates_frozen_contract_at_family_three(
    tmp_path: Path,
) -> None:
    assignments = build_split_assignments(_identities(), seed=SPLIT_SEED)
    design = build_schedule(assignments, SplitName.DESIGN, seed=SCHEDULE_SEED)
    record = next(
        row
        for row in design
        if row.global_arm_position == 6 and row.architecture_family.value == "V1_REFERENCE"
    )
    scheduled = v1_scheduled_run(record, _case(record.identity, tmp_path / "cases"))
    assert scheduled.call_position == 3
    assert record.global_arm_position == 6


def test_admission_rejects_control_root_nested_under_preserved_evidence(
    tmp_path: Path,
) -> None:
    assignments = build_split_assignments(_identities(), seed=SPLIT_SEED)
    design = build_schedule(assignments, SplitName.DESIGN, seed=SCHEDULE_SEED)
    validation = build_schedule(assignments, SplitName.DEV_VALIDATION, seed=SCHEDULE_SEED)
    smoke = build_smoke_schedule(assignments, design, seed=SCHEDULE_SEED)
    old_v1 = tmp_path / "old-dev-v1"
    with pytest.raises(ValueError, match="pairwise disjoint"):
        rehearse_schedule_admission(
            assignments=assignments,
            smoke_schedule=smoke,
            design_schedule=design,
            validation_schedule=validation,
            design_cases={},
            control_root=old_v1 / "nested-dev3-control",
            private_schedule_root=tmp_path / "private-schedules",
            output_root=tmp_path / "output",
            smoke_journal_root=tmp_path / "smoke-journal",
            design_journal_root=tmp_path / "design-journal",
            implementation_commit="a" * 40,
            split_lock_sha256="b" * 64,
            dev2_failure_audit_lock_sha256="d" * 64,
            retry_policy_lock_sha256="e" * 64,
            schedule_hashes={
                "smoke": "c" * 64,
                "design": "d" * 64,
                "validation": "e" * 64,
                "set": "f" * 64,
            },
            old_run_ids=set(),
            v1_external_schedule_sha256="1" * 64,
            preserved_schedule_hashes={
                "v2_dev_v1_design": "2" * 64,
                "v2_dev_v1_validation": "3" * 64,
                "v2_dev1_design": "4" * 64,
                "v2_dev1_validation": "5" * 64,
                "v2_dev2_design": "8" * 64,
                "v2_dev2_validation": "9" * 64,
            },
            preserved_roots={
                "v2_dev_v1": old_v1,
                "v2_dev1_control": tmp_path / "old-dev1-control",
                "v2_dev1_output": tmp_path / "old-dev1-output",
                "v2_dev2_control": tmp_path / "old-dev2-control",
                "v2_dev2_schedule": tmp_path / "old-dev2-schedule",
                "v2_dev2_output": tmp_path / "old-dev2-output",
                "v2_dev2_smoke": tmp_path / "old-dev2-smoke",
                "v2_dev2_design": tmp_path / "old-dev2-design",
            },
            preserved_evidence_hashes={
                "v2_dev_v1_tree": "1" * 64,
                "v2_dev1_control_tree": "2" * 64,
                "v2_dev1_output_tree": "3" * 64,
                "v2_dev2_control_tree": "4" * 64,
                "v2_dev2_schedule_tree": "5" * 64,
                "v2_dev2_output_tree": "6" * 64,
                "v2_dev2_smoke_tree": "7" * 64,
                "v2_dev2_design_tree": "8" * 64,
            },
        )


def test_admission_lock_json_has_no_private_paths(tmp_path: Path) -> None:
    assignments = build_split_assignments(_identities(), seed=SPLIT_SEED)
    design = build_schedule(assignments, SplitName.DESIGN, seed=SCHEDULE_SEED)
    validation = build_schedule(assignments, SplitName.DEV_VALIDATION, seed=SCHEDULE_SEED)
    smoke = build_smoke_schedule(assignments, design, seed=SCHEDULE_SEED)
    cases = {
        identity: _case(identity, tmp_path / "cases")
        for identity in {record.identity for record in design}
    }
    lock = rehearse_schedule_admission(
        assignments=assignments,
        smoke_schedule=smoke,
        design_schedule=design,
        validation_schedule=validation,
        design_cases=cases,
        control_root=tmp_path / "control",
        private_schedule_root=tmp_path / "private-schedules",
        output_root=tmp_path / "output",
        smoke_journal_root=tmp_path / "smoke-journal",
        design_journal_root=tmp_path / "design-journal",
        implementation_commit="a" * 40,
        split_lock_sha256="b" * 64,
        dev2_failure_audit_lock_sha256="d" * 64,
        retry_policy_lock_sha256="e" * 64,
        schedule_hashes={"smoke": "c" * 64, "design": "d" * 64, "validation": "e" * 64, "set": "f" * 64},
        old_run_ids=set(),
        v1_external_schedule_sha256="1" * 64,
        preserved_schedule_hashes={
            "v2_dev_v1_design": "2" * 64,
            "v2_dev_v1_validation": "3" * 64,
            "v2_dev1_design": "4" * 64,
            "v2_dev1_validation": "5" * 64,
            "v2_dev2_design": "8" * 64,
            "v2_dev2_validation": "9" * 64,
        },
        preserved_roots={
            "v2_dev_v1": tmp_path / "old-dev-v1",
            "v2_dev1_control": tmp_path / "old-dev1-control",
            "v2_dev1_output": tmp_path / "old-dev1-output",
            "v2_dev2_control": tmp_path / "old-dev2-control",
            "v2_dev2_schedule": tmp_path / "old-dev2-schedule",
            "v2_dev2_output": tmp_path / "old-dev2-output",
            "v2_dev2_smoke": tmp_path / "old-dev2-smoke",
            "v2_dev2_design": tmp_path / "old-dev2-design",
        },
        preserved_evidence_hashes={
            "v2_dev_v1_tree": "1" * 64,
            "v2_dev1_control_tree": "2" * 64,
            "v2_dev1_output_tree": "3" * 64,
            "v2_dev2_control_tree": "4" * 64,
            "v2_dev2_schedule_tree": "5" * 64,
            "v2_dev2_output_tree": "6" * 64,
            "v2_dev2_smoke_tree": "7" * 64,
            "v2_dev2_design_tree": "8" * 64,
        },
    )
    text = json.dumps(
        public_admission_gate(lock, lock_sha256="8" * 64), sort_keys=True
    )
    assert str(tmp_path) not in text
    assert all(record.run_id not in text for record in design + validation)
