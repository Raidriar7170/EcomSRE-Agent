"""Zero-Provider admission rehearsal for the complete v2-dev.3 schedule set."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Literal, Mapping

from pydantic import Field, StrictBool, StrictInt

from ecomsre_rcaeval.contracts import Architecture, ScheduledRun
from ecomsre_rcaeval.dataset import DevCase, TelemetryCase
from ecomsre_rcaeval_v2.adapter import dev_case_to_telemetry_case
from ecomsre_rcaeval_v2.contracts import RunId, Sha256, V2Model
from ecomsre_rcaeval_v2.dev3_schedule import (
    DESIGN_VARIANTS,
    DEV_VALIDATION_VARIANTS,
    PROTOCOL_ID,
    SCHEDULE_SEED,
    ArchitectureFamily,
    ScheduleRecord,
    Variant,
    expected_run_id,
    schedule_budget_summary,
)
from ecomsre_rcaeval_v2.schedule import CaseIdentity, SplitAssignment, SplitName
from ecomsre_rcaeval_v2.dev3_paths import (
    attempt_path_for,
    journal_root_for,
    require_pairwise_disjoint,
    terminal_path_for,
)


ADMISSION_LOCK_NAME = "schedule-admission-lock.json"
_V1_ARCHITECTURES = {
    Variant.SINGLE_V1_REFERENCE: Architecture.SINGLE,
    Variant.FIXED_V1_REFERENCE: Architecture.FIXED,
    Variant.DYNAMIC_V1_REFERENCE: Architecture.DYNAMIC,
}
_V2_ARCHITECTURES: dict[
    Variant, Literal["single_v2", "fixed_v2", "dynamic_v2"]
] = {
    Variant.SINGLE_V2: "single_v2",
    Variant.FIXED_V2: "fixed_v2",
    Variant.DYNAMIC_V2: "dynamic_v2",
}
_MAX_PROVIDER_OPERATIONS = {
    Variant.SINGLE_V1_REFERENCE: 1,
    Variant.FIXED_V1_REFERENCE: 4,
    Variant.DYNAMIC_V1_REFERENCE: 5,
    Variant.SINGLE_V2: 1,
    Variant.FIXED_V2: 4,
    Variant.DYNAMIC_V2: 5,
}


class Dev3V2RunContract(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev3.v2-run-admission.v1"]
    run_id: RunId
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    system: Literal["RE2-OB", "RE2-SS"]
    architecture: Literal["single_v2", "fixed_v2", "dynamic_v2"]
    global_arm_position: StrictInt = Field(ge=1, le=6)
    family_call_position: StrictInt = Field(ge=1, le=3)
    schedule_seed: Literal[20260810]


class AdmissionCount(V2Model):
    admitted: StrictInt = Field(ge=0)
    rejected: Literal[0]


class ValidationAdmission(V2Model):
    admitted: Literal[480]
    rejected: Literal[0]
    values_accessed: Literal[False]


class V1ContractAdmission(V2Model):
    admitted: StrictInt = Field(ge=1)
    call_position_min: Literal[1]
    call_position_max: Literal[3]


class V2ContractAdmission(V2Model):
    admitted: StrictInt = Field(ge=1)
    family_position_min: Literal[1]
    family_position_max: Literal[3]


class ScheduleAdmissionLock(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev3.schedule-admission-lock.v1"]
    protocol_id: Literal["rcaeval-re2-v2-dev.3"]
    implementation_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    split_lock_sha256: Sha256
    dev2_failure_audit_lock_sha256: Sha256
    retry_policy_lock_sha256: Sha256
    smoke_schedule_sha256: Sha256
    design_schedule_sha256: Sha256
    validation_schedule_sha256: Sha256
    schedule_set_sha256: Sha256
    v1_external_schedule_sha256: Sha256
    private_schedule_root_identity_sha256: Sha256
    private_output_root_identity_sha256: Sha256
    smoke_journal_root_identity_sha256: Sha256
    design_journal_root_identity_sha256: Sha256
    preserved_schedule_hashes: dict[str, Sha256]
    preserved_root_identity_sha256: dict[str, Sha256]
    preserved_evidence_hashes: dict[str, Sha256]
    smoke: AdmissionCount
    design: AdmissionCount
    dev_validation_metadata: ValidationAdmission
    v1_contract_construction: V1ContractAdmission
    v2_contract_construction: V2ContractAdmission
    run_id_checks: dict[str, StrictBool]
    old_new_overlap_checks: dict[str, StrictInt]
    budget_checks: dict[str, StrictInt | StrictBool]
    provider_objects_constructed: Literal[0]
    provider_calls: Literal[0]
    run_attempts_created: Literal[0]
    operation_attempts_created: Literal[0]
    provider_attempts_created: Literal[0]
    verdict: Literal["V2_DEV3_ADMISSION_REHEARSAL_PASSED"]


def v1_scheduled_run(record: ScheduleRecord, case: DevCase) -> ScheduledRun:
    architecture = _V1_ARCHITECTURES.get(record.variant)
    if architecture is None or record.architecture_family is not ArchitectureFamily.V1_REFERENCE:
        raise ValueError("dev3 v1 adapter received a non-v1 schedule row")
    if record.family_call_position not in {1, 2, 3}:
        raise ValueError("dev3 v1 family call position is outside the frozen contract")
    return ScheduledRun(
        run_id=record.run_id,
        case_id=case.case_id,
        architecture=architecture,
        call_position=record.family_call_position,
        schedule_seed=SCHEDULE_SEED,
    )


def v2_run_contract(record: ScheduleRecord, case: TelemetryCase) -> Dev3V2RunContract:
    architecture = _V2_ARCHITECTURES.get(record.variant)
    if architecture is None or record.architecture_family is not ArchitectureFamily.V2_DEV3:
        raise ValueError("dev3 v2 adapter received a non-v2 schedule row")
    return Dev3V2RunContract(
        schema_version="rcaeval-re2-v2-dev3.v2-run-admission.v1",
        run_id=record.run_id,
        case_id=case.case_id,
        system=record.identity.system,
        architecture=architecture,
        global_arm_position=record.global_arm_position,
        family_call_position=record.family_call_position,
        schedule_seed=20260810,
    )


def _validate_positions(records: tuple[ScheduleRecord, ...], *, expected_cases: int) -> None:
    grouped: dict[CaseIdentity, list[ScheduleRecord]] = {}
    for record in records:
        grouped.setdefault(record.identity, []).append(record)
    if len(grouped) != expected_cases:
        raise ValueError("admission schedule case count is invalid")
    for rows in grouped.values():
        expected_variants = DESIGN_VARIANTS if rows[0].split is SplitName.DESIGN else DEV_VALIDATION_VARIANTS
        if {row.variant for row in rows} != set(expected_variants):
            raise ValueError("admission schedule variants are invalid")
        if [row.global_arm_position for row in rows] != list(range(1, len(rows) + 1)):
            raise ValueError("admission global positions are invalid")
        for family in {row.architecture_family for row in rows}:
            family_rows = [row for row in rows if row.architecture_family is family]
            if [row.family_call_position for row in family_rows] != list(
                range(1, len(family_rows) + 1)
            ):
                raise ValueError("admission family positions are not global-order derived")


def _validate_run_ids(records: tuple[ScheduleRecord, ...]) -> None:
    if len({record.run_id for record in records}) != len(records):
        raise ValueError("admission schedule contains duplicate run IDs")
    for record in records:
        if record.run_id != expected_run_id(
            record.identity, record.split, record.variant, SCHEDULE_SEED
        ):
            raise ValueError("admission run ID differs from dev3 schedule domain")


def _validate_output_identity(journal_root: Path, record: ScheduleRecord) -> None:
    root = journal_root.resolve()
    terminal = terminal_path_for(record, root).resolve()
    attempt = attempt_path_for(record, root).resolve()
    for candidate in (terminal, attempt):
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise ValueError("admission journal path escapes its locked root") from error
    if record.run_id not in terminal.parts and record.run_id not in terminal.name:
        raise ValueError("admission terminal identity differs from run ID")
    if record.run_id not in attempt.parts and record.run_id not in attempt.name:
        raise ValueError("admission attempt identity differs from run ID")


def _require_pristine_runtime_roots(*roots: Path) -> None:
    for root in roots:
        if not root.exists():
            continue
        if root.is_symlink() or not root.is_dir():
            raise ValueError("admission runtime root is invalid")
        entries = tuple(root.iterdir())
        if any(path.name != ".evaluation-root-authority.json" for path in entries):
            raise ValueError("admission requires zero pre-existing runtime artifacts")
        if any(
            path.name in {
                "run-attempt.json",
                "operation-attempts",
                "provider-attempt-starts",
                "provider-attempts",
                "semantic-operation-starts",
                "semantic-operations",
            }
            for path in root.rglob("*")
        ):
            raise ValueError("admission observed a pre-existing attempt artifact")


def rehearse_schedule_admission(
    *,
    assignments: tuple[SplitAssignment, ...],
    smoke_schedule: tuple[ScheduleRecord, ...],
    design_schedule: tuple[ScheduleRecord, ...],
    validation_schedule: tuple[ScheduleRecord, ...],
    design_cases: Mapping[CaseIdentity, DevCase],
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    implementation_commit: str,
    split_lock_sha256: str,
    dev2_failure_audit_lock_sha256: str,
    retry_policy_lock_sha256: str,
    schedule_hashes: Mapping[str, str],
    old_run_ids: set[str],
    v1_external_schedule_sha256: str,
    preserved_schedule_hashes: Mapping[str, str],
    preserved_roots: Mapping[str, Path],
    preserved_evidence_hashes: Mapping[str, str],
) -> ScheduleAdmissionLock:
    """Rehearse every runtime contract without a Provider or attempt API."""

    require_pairwise_disjoint(
        control_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
        *preserved_roots.values(),
    )
    _require_pristine_runtime_roots(
        output_root, smoke_journal_root, design_journal_root
    )
    if set(preserved_schedule_hashes) != {
        "v2_dev_v1_design",
        "v2_dev_v1_validation",
        "v2_dev1_design",
        "v2_dev1_validation",
        "v2_dev2_design",
        "v2_dev2_validation",
    }:
        raise ValueError("admission preserved schedule bindings are incomplete")
    if set(preserved_roots) != {
        "v2_dev_v1",
        "v2_dev1_control",
        "v2_dev1_output",
        "v2_dev2_control",
        "v2_dev2_schedule",
        "v2_dev2_output",
        "v2_dev2_smoke",
        "v2_dev2_design",
    }:
        raise ValueError("admission preserved roots are incomplete")
    expected_evidence_hashes = {
        f"{name}_tree"
        for name in {
            "v2_dev_v1",
            "v2_dev1_control",
            "v2_dev1_output",
            "v2_dev2_control",
            "v2_dev2_schedule",
            "v2_dev2_output",
            "v2_dev2_smoke",
            "v2_dev2_design",
        }
    }
    if set(preserved_evidence_hashes) != expected_evidence_hashes:
        raise ValueError("admission preserved evidence bindings are incomplete")
    if len(smoke_schedule) != 72 or len(design_schedule) != 360 or len(validation_schedule) != 480:
        raise ValueError("admission requires exact 72/360/480 schedules")
    _validate_positions(smoke_schedule, expected_cases=12)
    _validate_positions(design_schedule, expected_cases=60)
    _validate_positions(validation_schedule, expected_cases=120)
    _validate_run_ids(design_schedule)
    _validate_run_ids(validation_schedule)
    if not set(smoke_schedule) < set(design_schedule):
        raise ValueError("Smoke schedule is not a strict DESIGN subset")
    design_ids = {record.run_id for record in design_schedule}
    validation_ids = {record.run_id for record in validation_schedule}
    new_ids = design_ids | validation_ids
    if design_ids & validation_ids:
        raise ValueError("DESIGN and validation run IDs overlap")
    overlap = new_ids & old_run_ids
    if overlap:
        raise ValueError("dev3 run IDs overlap preserved evidence")
    assignment_map = {item.identity: item.split for item in assignments}
    if len(assignment_map) != 180:
        raise ValueError("admission split assignment metadata is incomplete")

    v1_positions: list[int] = []
    v2_positions: list[int] = []
    smoke_ids = {record.run_id for record in smoke_schedule}
    for records in (smoke_schedule, design_schedule):
        for record in records:
            if assignment_map.get(record.identity) is not SplitName.DESIGN:
                raise ValueError("admission DESIGN row differs from split assignment")
            case = design_cases.get(record.identity)
            if case is None:
                raise ValueError("admission DESIGN case resolution is incomplete")
            telemetry = dev_case_to_telemetry_case(case)
            journal_root = journal_root_for(
                record,
                phase=("smoke" if records is smoke_schedule else "design"),
                smoke_run_ids=smoke_ids,
                smoke_journal_root=smoke_journal_root,
                design_journal_root=design_journal_root,
            )
            _validate_output_identity(journal_root, record)
            if record.architecture_family is ArchitectureFamily.V1_REFERENCE:
                v1_positions.append(v1_scheduled_run(record, case).call_position)
            else:
                v2_positions.append(v2_run_contract(record, telemetry).family_call_position)

    for record in validation_schedule:
        if assignment_map.get(record.identity) is not SplitName.DEV_VALIDATION:
            raise ValueError("validation metadata row differs from split assignment")

    budget = schedule_budget_summary()
    smoke_cap = sum(_MAX_PROVIDER_OPERATIONS[row.variant] for row in smoke_schedule)
    design_cap = sum(_MAX_PROVIDER_OPERATIONS[row.variant] for row in design_schedule)
    validation_cap = sum(_MAX_PROVIDER_OPERATIONS[row.variant] for row in validation_schedule)
    if smoke_cap > budget.smoke_max_provider_operations or design_cap > budget.design_max_provider_operations or validation_cap > budget.dev_validation_max_provider_operations:
        raise ValueError("admission schedule exceeds locked Provider operation budget")
    smoke_attempt_cap = smoke_cap * 2
    design_attempt_cap = design_cap * 2
    attempt_token_reservation = 32_000
    if smoke_attempt_cap > 480 or design_attempt_cap > 2_400:
        raise ValueError("admission schedule exceeds locked Provider attempt budget")
    if (
        smoke_attempt_cap * attempt_token_reservation > 15_360_000
        or design_attempt_cap * attempt_token_reservation > 76_800_000
    ):
        raise ValueError("admission schedule exceeds conservative token budget")
    required_hashes = {"smoke", "design", "validation", "set"}
    if set(schedule_hashes) != required_hashes:
        raise ValueError("admission schedule hashes are incomplete")

    if min(v1_positions) != 1 or max(v1_positions) != 3:
        raise ValueError("admitted v1 call-position range is not 1..3")
    if min(v2_positions) != 1 or max(v2_positions) != 3:
        raise ValueError("admitted v2 family-position range is not 1..3")
    return ScheduleAdmissionLock(
        schema_version="rcaeval-re2-v2-dev3.schedule-admission-lock.v1",
        protocol_id=PROTOCOL_ID,
        implementation_commit=implementation_commit,
        split_lock_sha256=split_lock_sha256,
        dev2_failure_audit_lock_sha256=dev2_failure_audit_lock_sha256,
        retry_policy_lock_sha256=retry_policy_lock_sha256,
        smoke_schedule_sha256=schedule_hashes["smoke"],
        design_schedule_sha256=schedule_hashes["design"],
        validation_schedule_sha256=schedule_hashes["validation"],
        schedule_set_sha256=schedule_hashes["set"],
        v1_external_schedule_sha256=v1_external_schedule_sha256,
        private_schedule_root_identity_sha256=hashlib.sha256(
            str(private_schedule_root.resolve()).encode()
        ).hexdigest(),
        private_output_root_identity_sha256=hashlib.sha256(
            str(output_root.resolve()).encode()
        ).hexdigest(),
        smoke_journal_root_identity_sha256=hashlib.sha256(
            str(smoke_journal_root.resolve()).encode()
        ).hexdigest(),
        design_journal_root_identity_sha256=hashlib.sha256(
            str(design_journal_root.resolve()).encode()
        ).hexdigest(),
        preserved_schedule_hashes=dict(preserved_schedule_hashes),
        preserved_root_identity_sha256={
            name: hashlib.sha256(str(path.resolve()).encode()).hexdigest()
            for name, path in preserved_roots.items()
        },
        preserved_evidence_hashes=dict(preserved_evidence_hashes),
        smoke=AdmissionCount(admitted=72, rejected=0),
        design=AdmissionCount(admitted=360, rejected=0),
        dev_validation_metadata=ValidationAdmission(
            admitted=480, rejected=0, values_accessed=False
        ),
        v1_contract_construction=V1ContractAdmission(
            admitted=len(v1_positions),
            call_position_min=1,
            call_position_max=3,
        ),
        v2_contract_construction=V2ContractAdmission(
            admitted=len(v2_positions),
            family_position_min=1,
            family_position_max=3,
        ),
        run_id_checks={
            "smoke_strict_subset_of_design": True,
            "design_validation_disjoint": True,
            "unique_within_schedules": True,
            "dev3_domain_recomputed": True,
            "v1_external_namespace_separated": True,
        },
        old_new_overlap_checks={
            "old_run_id_count": len(old_run_ids),
            "new_run_id_count": len(new_ids),
            "overlap_count": 0,
        },
        budget_checks={
            "smoke_operation_ceiling": smoke_cap,
            "design_operation_ceiling": design_cap,
            "validation_operation_ceiling": validation_cap,
            "within_locked_caps": True,
            "smoke_provider_attempt_ceiling": smoke_attempt_cap,
            "design_provider_attempt_ceiling": design_attempt_cap,
            "smoke_retry_cap": 12,
            "design_retry_cap": 60,
            "attempt_token_reservation": attempt_token_reservation,
            "smoke_conservative_token_budget": 15_360_000,
            "design_conservative_token_budget": 76_800_000,
            "retry_and_token_reservations_validated": True,
        },
        provider_objects_constructed=0,
        provider_calls=0,
        run_attempts_created=0,
        operation_attempts_created=0,
        provider_attempts_created=0,
        verdict="V2_DEV3_ADMISSION_REHEARSAL_PASSED",
    )


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def write_admission_lock(path: Path, lock: ScheduleAdmissionLock) -> str:
    payload = _canonical_bytes(lock.model_dump(mode="json"))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ValueError("existing schedule admission lock differs")
        return hashlib.sha256(payload).hexdigest()
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)
    return hashlib.sha256(payload).hexdigest()


def load_admission_lock(path: Path) -> ScheduleAdmissionLock:
    if path.is_symlink() or not path.is_file():
        raise ValueError("schedule admission lock is missing or invalid")
    return ScheduleAdmissionLock.model_validate_json(path.read_text(encoding="utf-8"))
