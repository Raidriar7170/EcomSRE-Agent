"""Versioned RCAEval RE2 v2-dev.3 schedule contracts and generation."""

from __future__ import annotations

from collections import Counter
from enum import Enum
import hashlib
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from ecomsre_rcaeval_v2.contracts import Sha256, V2Model
from ecomsre_rcaeval_v2.schedule import (
    LEGACY_CASE_ORDER_DOMAIN,
    LEGACY_SCHEDULE_SEED,
    SPLIT_DOMAIN,
    SPLIT_SEED,
    CaseIdentity,
    SplitAssignment,
    SplitName,
    ScheduleRecord as Dev1ScheduleRecord,
    Variant as Dev1Variant,
    _domain_digest,
    case_identity_bytes,
)


PROTOCOL_ID: Literal["rcaeval-re2-v2-dev.3"] = "rcaeval-re2-v2-dev.3"
SCHEDULE_DOMAIN: Literal["rcaeval-re2-v2-dev3-schedule-v1"] = (
    "rcaeval-re2-v2-dev3-schedule-v1"
)
CASE_ORDER_DOMAIN = f"{SCHEDULE_DOMAIN}-case"
SCHEDULE_SEED: Literal[20260810] = 20260810
_FAULTS = ("cpu", "mem", "disk", "delay", "loss", "socket")
_SYSTEMS = ("RE2-OB", "RE2-SS")


class ArchitectureFamily(str, Enum):
    V1_REFERENCE = "V1_REFERENCE"
    V2_DEV3 = "V2_DEV3"


class Variant(str, Enum):
    SINGLE_V1_REFERENCE = "single_v1_reference"
    FIXED_V1_REFERENCE = "fixed_v1_reference"
    DYNAMIC_V1_REFERENCE = "dynamic_v1_reference"
    SINGLE_V2 = "single_v2_dev3"
    FIXED_V2 = "fixed_v2_dev3"
    DYNAMIC_V2 = "dynamic_v2_dev3"


DESIGN_VARIANTS = (
    Variant.SINGLE_V1_REFERENCE,
    Variant.FIXED_V1_REFERENCE,
    Variant.DYNAMIC_V1_REFERENCE,
    Variant.SINGLE_V2,
    Variant.FIXED_V2,
    Variant.DYNAMIC_V2,
)
DEV_VALIDATION_VARIANTS = (
    Variant.SINGLE_V1_REFERENCE,
    Variant.SINGLE_V2,
    Variant.FIXED_V2,
    Variant.DYNAMIC_V2,
)
_FAMILY_BY_VARIANT = {
    Variant.SINGLE_V1_REFERENCE: ArchitectureFamily.V1_REFERENCE,
    Variant.FIXED_V1_REFERENCE: ArchitectureFamily.V1_REFERENCE,
    Variant.DYNAMIC_V1_REFERENCE: ArchitectureFamily.V1_REFERENCE,
    Variant.SINGLE_V2: ArchitectureFamily.V2_DEV3,
    Variant.FIXED_V2: ArchitectureFamily.V2_DEV3,
    Variant.DYNAMIC_V2: ArchitectureFamily.V2_DEV3,
}
_DEV1_VARIANT = {
    Variant.SINGLE_V1_REFERENCE: Dev1Variant.SINGLE_V1_REFERENCE,
    Variant.FIXED_V1_REFERENCE: Dev1Variant.FIXED_V1_REFERENCE,
    Variant.DYNAMIC_V1_REFERENCE: Dev1Variant.DYNAMIC_V1_REFERENCE,
    Variant.SINGLE_V2: Dev1Variant.SINGLE_V2,
    Variant.FIXED_V2: Dev1Variant.FIXED_V2,
    Variant.DYNAMIC_V2: Dev1Variant.DYNAMIC_V2,
}


class ScheduleRecord(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev3.scheduled-run.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    split: SplitName
    identity: CaseIdentity
    variant: Variant
    global_arm_position: StrictInt = Field(ge=1, le=6)
    architecture_family: ArchitectureFamily
    family_call_position: StrictInt = Field(ge=1, le=3)
    case_order_digest_sha256: Sha256

    @model_validator(mode="after")
    def require_family_matches_variant(self) -> ScheduleRecord:
        if self.architecture_family is not _FAMILY_BY_VARIANT[self.variant]:
            raise ValueError("schedule architecture family differs from variant")
        return self

    @property
    def arm_position(self) -> int:
        """Read-only public alias; runtime adapters must not use this property."""

        return self.global_arm_position

    @property
    def system(self) -> str:
        return self.identity.system

    @property
    def fault(self) -> str:
        return self.identity.fault


class ScheduleBudgetSummary(V2Model):
    smoke_max_provider_operations: Literal[240]
    design_max_provider_operations: Literal[1200]
    dev_validation_max_provider_operations: Literal[1320]
    worst_case_provider_operations: Literal[2520]
    hard_max_provider_operations: Literal[2600]
    provider_operation_headroom: Literal[80]
    smoke_additional_operations: Literal[0]

    @model_validator(mode="after")
    def require_consistency(self) -> ScheduleBudgetSummary:
        if (
            self.design_max_provider_operations
            + self.dev_validation_max_provider_operations
            != self.worst_case_provider_operations
            or self.hard_max_provider_operations - self.worst_case_provider_operations
            != self.provider_operation_headroom
        ):
            raise ValueError("dev3 schedule budget summary is inconsistent")
        return self


def _case_order_digest(identity: CaseIdentity, split: SplitName, seed: int) -> str:
    return _domain_digest(f"{CASE_ORDER_DOMAIN}\0{split.value}", seed, identity)


def _legacy_smoke_order_digest(identity: CaseIdentity) -> str:
    return _domain_digest(
        f"{LEGACY_CASE_ORDER_DOMAIN}\0{SplitName.DESIGN.value}",
        LEGACY_SCHEDULE_SEED,
        identity,
    )


def expected_run_id(
    identity: CaseIdentity, split: SplitName, variant: Variant, seed: int
) -> str:
    payload = b"\0".join(
        (
            PROTOCOL_ID.encode(),
            SCHEDULE_DOMAIN.encode(),
            split.value.encode(),
            str(seed).encode("ascii"),
            case_identity_bytes(identity),
            variant.value.encode(),
        )
    )
    return hashlib.sha256(payload).hexdigest()[:32]


def _rotated_records(
    assignment: SplitAssignment,
    split: SplitName,
    variants: tuple[Variant, ...],
    rotation: int,
    seed: int,
) -> tuple[ScheduleRecord, ...]:
    rotated = variants[rotation:] + variants[:rotation]
    family_positions: Counter[ArchitectureFamily] = Counter()
    records: list[ScheduleRecord] = []
    for global_position, variant in enumerate(rotated, 1):
        family = _FAMILY_BY_VARIANT[variant]
        family_positions[family] += 1
        records.append(
            ScheduleRecord(
                schema_version="rcaeval-re2-v2-dev3.scheduled-run.v1",
                run_id=expected_run_id(assignment.identity, split, variant, seed),
                split=split,
                identity=assignment.identity,
                variant=variant,
                global_arm_position=global_position,
                architecture_family=family,
                family_call_position=family_positions[family],
                case_order_digest_sha256=_case_order_digest(
                    assignment.identity, split, seed
                ),
            )
        )
    return tuple(records)


def build_schedule(
    assignments: tuple[SplitAssignment, ...],
    split: SplitName,
    *,
    seed: int,
) -> tuple[ScheduleRecord, ...]:
    if type(seed) is not int or seed != SCHEDULE_SEED:
        raise ValueError("dev3 schedule seed differs from pre-registration")
    if not isinstance(split, SplitName):
        raise TypeError("dev3 schedule split must be typed")
    selected = tuple(item for item in assignments if item.split is split)
    expected_cases = 60 if split is SplitName.DESIGN else 120
    if len(selected) != expected_cases or len({item.identity for item in selected}) != expected_cases:
        raise ValueError("dev3 schedule case count differs from inherited split")
    variants = DESIGN_VARIANTS if split is SplitName.DESIGN else DEV_VALIDATION_VARIANTS
    ordered = tuple(
        sorted(
            selected,
            key=lambda item: (
                _case_order_digest(item.identity, split, seed),
                case_identity_bytes(item.identity),
            ),
        )
    )
    return tuple(
        record
        for case_index, assignment in enumerate(ordered)
        for record in _rotated_records(
            assignment,
            split,
            variants,
            case_index % len(variants),
            seed,
        )
    )


def build_smoke_schedule(
    assignments: tuple[SplitAssignment, ...],
    design_schedule: tuple[ScheduleRecord, ...],
    *,
    seed: int,
) -> tuple[ScheduleRecord, ...]:
    if type(seed) is not int or seed != SCHEDULE_SEED:
        raise ValueError("dev3 smoke seed differs from pre-registration")
    design = tuple(item for item in assignments if item.split is SplitName.DESIGN)
    grouped: dict[tuple[str, str], list[SplitAssignment]] = {}
    for item in design:
        grouped.setdefault((item.identity.system, item.identity.fault), []).append(item)
    if set(grouped) != {(system, fault) for system in _SYSTEMS for fault in _FAULTS}:
        raise ValueError("dev3 smoke strata differ from inherited design")
    selected = {
        min(
            group,
            key=lambda item: (
                _legacy_smoke_order_digest(item.identity),
                case_identity_bytes(item.identity),
            ),
        ).identity
        for group in grouped.values()
    }
    smoke = tuple(record for record in design_schedule if record.identity in selected)
    if len(smoke) != 72 or len({record.identity for record in smoke}) != 12:
        raise ValueError("dev3 smoke is not the exact 12-case DESIGN subset")
    return smoke


def schedule_budget_summary() -> ScheduleBudgetSummary:
    return ScheduleBudgetSummary(
        smoke_max_provider_operations=240,
        design_max_provider_operations=1200,
        dev_validation_max_provider_operations=1320,
        worst_case_provider_operations=2520,
        hard_max_provider_operations=2600,
        provider_operation_headroom=80,
        smoke_additional_operations=0,
    )


def as_dev1_runtime_record(record: ScheduleRecord) -> Dev1ScheduleRecord:
    """Adapt only the generic v2 runner/evidence surface, never v1 call position."""

    return Dev1ScheduleRecord(
        schema_version="rcaeval-re2-v2-dev1.scheduled-run.v1",
        run_id=record.run_id,
        split=record.split,
        identity=record.identity,
        variant=_DEV1_VARIANT[record.variant],
        arm_position=record.global_arm_position,
        case_order_digest_sha256=record.case_order_digest_sha256,
    )


__all__ = [
    "ArchitectureFamily",
    "CASE_ORDER_DOMAIN",
    "DESIGN_VARIANTS",
    "DEV_VALIDATION_VARIANTS",
    "PROTOCOL_ID",
    "SCHEDULE_DOMAIN",
    "SCHEDULE_SEED",
    "SPLIT_DOMAIN",
    "SPLIT_SEED",
    "ScheduleRecord",
    "Variant",
    "build_schedule",
    "build_smoke_schedule",
    "as_dev1_runtime_record",
    "expected_run_id",
    "schedule_budget_summary",
]
