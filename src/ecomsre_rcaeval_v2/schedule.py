"""Deterministic development split and rotated schedule generation."""

from __future__ import annotations

from collections import Counter
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from ecomsre_rcaeval_v2.contracts import DevSystem, ServiceName, Sha256, V2Model


SPLIT_SEED = 20260807
SPLIT_DOMAIN: Literal["rcaeval-re2-v2-dev-split-v1"] = (
    "rcaeval-re2-v2-dev-split-v1"
)
CASE_ORDER_DOMAIN = "rcaeval-re2-v2-dev-schedule-case-v1"
PROTOCOL_ID: Literal["rcaeval-re2-v2-dev-v1"] = "rcaeval-re2-v2-dev-v1"
_ROOT_SERVICES = {
    "RE2-OB": (
        "checkoutservice",
        "currencyservice",
        "emailservice",
        "productcatalogservice",
        "recommendationservice",
    ),
    "RE2-SS": ("carts", "catalogue", "orders", "payment", "user"),
}
_FAULTS = ("cpu", "mem", "disk", "delay", "loss", "socket")
_FORBIDDEN_PATH_MARKERS = (
    "re2-tt",
    "tt-case-",
    "holdout-sanitized",
    "evaluator-only",
    "terminal-journal",
    "ground-truth.json",
    "scored_cases",
    "/attribution/",
)


class SplitName(str, Enum):
    DESIGN = "DESIGN"
    DEV_VALIDATION = "DEV_VALIDATION"


class Variant(str, Enum):
    SINGLE_V1_REFERENCE = "single_v1_reference"
    FIXED_V1_REFERENCE = "fixed_v1_reference"
    DYNAMIC_V1_REFERENCE = "dynamic_v1_reference"
    SINGLE_V2 = "single_v2"
    FIXED_V2 = "fixed_v2"
    DYNAMIC_V2 = "dynamic_v2"


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


class CaseIdentity(V2Model):
    system: DevSystem
    root_cause_service: ServiceName
    fault: Literal["cpu", "mem", "disk", "delay", "loss", "socket"]
    instance: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


class SplitAssignment(V2Model):
    identity: CaseIdentity
    split: SplitName
    selection_digest_sha256: Sha256


class SplitAssignmentManifest(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev.split-assignment-manifest.v1"]
    seed: Literal[20260807]
    domain: Literal["rcaeval-re2-v2-dev-split-v1"]
    identity_encoding: Literal["utf8_nul_joined"]
    assignments: tuple[SplitAssignment, ...]


class PublicSplitLock(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev.split-lock.v1"]
    protocol_id: Literal["rcaeval-re2-v2-dev-v1"]
    classification: tuple[
        Literal["DEVELOPMENT_VISIBLE"],
        Literal["NOT_EXTERNAL_HOLDOUT"],
        Literal["NOT_PRIMARY_INFERENCE"],
    ]
    seed: Literal[20260807]
    identity_fields: tuple[
        Literal["system"],
        Literal["root_cause_service"],
        Literal["fault"],
        Literal["instance"],
    ]
    algorithm: dict[str, str]
    counts: dict[str, StrictInt]
    assignment_manifest_sha256: Sha256
    protocol_sha256: Sha256
    dataset_lock_sha256: Sha256


class ScheduleRecord(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev.scheduled-run.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    split: SplitName
    identity: CaseIdentity
    variant: Variant
    arm_position: StrictInt = Field(ge=1, le=6)
    case_order_digest_sha256: Sha256

    @property
    def system(self) -> str:
        return self.identity.system

    @property
    def fault(self) -> str:
        return self.identity.fault


class ScheduleBudgetSummary(V2Model):
    design_max_provider_operations: Literal[1200]
    dev_validation_max_provider_operations: Literal[1320]
    worst_case_provider_operations: Literal[2520]
    hard_max_provider_operations: Literal[2600]
    provider_operation_headroom: Literal[80]
    smoke_additional_operations: Literal[0]

    @model_validator(mode="after")
    def require_budget_consistency(self) -> ScheduleBudgetSummary:
        if (
            self.design_max_provider_operations
            + self.dev_validation_max_provider_operations
            != self.worst_case_provider_operations
            or self.hard_max_provider_operations
            - self.worst_case_provider_operations
            != self.provider_operation_headroom
        ):
            raise ValueError("schedule budget summary is inconsistent")
        return self


def case_identity_bytes(identity: CaseIdentity) -> bytes:
    return b"\0".join(
        value.encode("utf-8")
        for value in (
            identity.system,
            identity.root_cause_service,
            identity.fault,
            identity.instance,
        )
    )


def _domain_digest(domain: str, seed: int, identity: CaseIdentity) -> str:
    payload = (
        domain.encode("utf-8")
        + b"\0"
        + str(seed).encode("ascii")
        + b"\0"
        + case_identity_bytes(identity)
    )
    return hashlib.sha256(payload).hexdigest()


def _validate_identity_set(identities: tuple[CaseIdentity, ...]) -> None:
    if len(identities) != 180 or len(set(identities)) != 180:
        raise ValueError("development split requires 180 unique identities")
    strata: dict[tuple[str, str, str], set[str]] = {}
    for identity in identities:
        if identity.root_cause_service not in _ROOT_SERVICES[identity.system]:
            raise ValueError("identity service is outside the root-service allowlist")
        key = (identity.system, identity.root_cause_service, identity.fault)
        strata.setdefault(key, set()).add(identity.instance)
    if len(strata) != 60 or any(len(instances) != 3 for instances in strata.values()):
        raise ValueError("development split requires 60 complete three-instance strata")
    for system, services in _ROOT_SERVICES.items():
        expected = {
            (system, service, fault) for service in services for fault in _FAULTS
        }
        if expected != {key for key in strata if key[0] == system}:
            raise ValueError("development split strata differ from the frozen design")


def build_split_assignments(
    identities: tuple[CaseIdentity, ...], *, seed: int
) -> tuple[SplitAssignment, ...]:
    if type(seed) is not int or seed != SPLIT_SEED:
        raise ValueError("development split seed differs from pre-registration")
    _validate_identity_set(identities)
    grouped: dict[tuple[str, str, str], list[CaseIdentity]] = {}
    for identity in identities:
        key = (identity.system, identity.root_cause_service, identity.fault)
        grouped.setdefault(key, []).append(identity)
    assignments: list[SplitAssignment] = []
    for stratum_key in sorted(grouped):
        ordered = sorted(
            grouped[stratum_key],
            key=lambda item: (
                _domain_digest(SPLIT_DOMAIN, seed, item),
                item.instance.encode("utf-8"),
            ),
        )
        for index, identity in enumerate(ordered):
            assignments.append(
                SplitAssignment(
                    identity=identity,
                    split=(
                        SplitName.DESIGN
                        if index == 0
                        else SplitName.DEV_VALIDATION
                    ),
                    selection_digest_sha256=_domain_digest(
                        SPLIT_DOMAIN, seed, identity
                    ),
                )
            )
    return tuple(
        sorted(assignments, key=lambda item: case_identity_bytes(item.identity))
    )


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _ensure_directory(path: Path, *, private: bool) -> None:
    path.mkdir(mode=0o700 if private else 0o755, parents=True, exist_ok=True)
    if private:
        path.chmod(0o700)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _create_once_or_verify(path: Path, payload: bytes, *, private: bool) -> None:
    _ensure_directory(path.parent, private=private)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ValueError("existing split artifact differs from deterministic payload")
        return
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)
    _fsync_directory(path.parent)


def _reject_forbidden_paths(*paths: Path) -> None:
    for path in paths:
        normalized = str(path).casefold()
        if any(marker in normalized for marker in _FORBIDDEN_PATH_MARKERS):
            raise ValueError("split artifact path contains a forbidden TT/private marker")


def write_split_artifacts(
    assignments: tuple[SplitAssignment, ...],
    *,
    private_root: Path,
    split_lock_output: Path,
    protocol_sha256: str,
    dataset_lock_sha256: str,
    seed: int,
) -> PublicSplitLock:
    _reject_forbidden_paths(private_root, split_lock_output)
    if type(seed) is not int or seed != SPLIT_SEED:
        raise ValueError("development split seed differs from pre-registration")
    canonical_assignments = tuple(
        sorted(assignments, key=lambda item: case_identity_bytes(item.identity))
    )
    if len(canonical_assignments) != 180:
        raise ValueError("split assignment manifest requires 180 entries")
    manifest = SplitAssignmentManifest(
        schema_version="rcaeval-re2-v2-dev.split-assignment-manifest.v1",
        seed=20260807,
        domain=SPLIT_DOMAIN,
        identity_encoding="utf8_nul_joined",
        assignments=canonical_assignments,
    )
    manifest_payload = _canonical_bytes(manifest.model_dump(mode="json"))
    manifest_path = private_root / "split-assignment-manifest.json"
    _create_once_or_verify(manifest_path, manifest_payload, private=True)
    manifest_sha256 = hashlib.sha256(manifest_payload).hexdigest()
    counts = Counter(item.split for item in canonical_assignments)
    counts_by_system_split = Counter(
        (item.identity.system, item.split) for item in canonical_assignments
    )
    public = PublicSplitLock(
        schema_version="rcaeval-re2-v2-dev.split-lock.v1",
        protocol_id=PROTOCOL_ID,
        classification=(
            "DEVELOPMENT_VISIBLE",
            "NOT_EXTERNAL_HOLDOUT",
            "NOT_PRIMARY_INFERENCE",
        ),
        seed=20260807,
        identity_fields=("system", "root_cause_service", "fault", "instance"),
        algorithm={
            "domain": SPLIT_DOMAIN,
            "identity_encoding": "utf8_nul_joined",
            "selection_order": "sha256_digest_then_instance_bytes",
        },
        counts={
            "total": len(canonical_assignments),
            "strata": 60,
            "design": counts[SplitName.DESIGN],
            "design_re2_ob": counts_by_system_split[
                ("RE2-OB", SplitName.DESIGN)
            ],
            "design_re2_ss": counts_by_system_split[
                ("RE2-SS", SplitName.DESIGN)
            ],
            "dev_validation": counts[SplitName.DEV_VALIDATION],
            "dev_validation_re2_ob": counts_by_system_split[
                ("RE2-OB", SplitName.DEV_VALIDATION)
            ],
            "dev_validation_re2_ss": counts_by_system_split[
                ("RE2-SS", SplitName.DEV_VALIDATION)
            ],
        },
        assignment_manifest_sha256=manifest_sha256,
        protocol_sha256=protocol_sha256,
        dataset_lock_sha256=dataset_lock_sha256,
    )
    public_payload = _canonical_bytes(public.model_dump(mode="json"))
    _create_once_or_verify(split_lock_output, public_payload, private=False)
    return public


def _case_order_digest(identity: CaseIdentity, split: SplitName, seed: int) -> str:
    domain = f"{CASE_ORDER_DOMAIN}\0{split.value}"
    return _domain_digest(domain, seed, identity)


def _run_id(
    identity: CaseIdentity, split: SplitName, variant: Variant, seed: int
) -> str:
    fields = (
        PROTOCOL_ID.encode("utf-8"),
        split.value.encode("utf-8"),
        str(seed).encode("ascii"),
        case_identity_bytes(identity),
        variant.value.encode("utf-8"),
    )
    payload = b"\0".join(fields)
    return hashlib.sha256(payload).hexdigest()[:32]


def build_schedule(
    assignments: tuple[SplitAssignment, ...],
    split: SplitName,
    *,
    seed: int,
) -> tuple[ScheduleRecord, ...]:
    if type(seed) is not int or seed != SPLIT_SEED:
        raise ValueError("development schedule seed differs from pre-registration")
    if not isinstance(split, SplitName):
        raise TypeError("development schedule split must be typed")
    selected = tuple(item for item in assignments if item.split is split)
    expected_cases = 60 if split is SplitName.DESIGN else 120
    if len(selected) != expected_cases or len({item.identity for item in selected}) != expected_cases:
        raise ValueError("development schedule case count differs from frozen split")
    variants = (
        DESIGN_VARIANTS if split is SplitName.DESIGN else DEV_VALIDATION_VARIANTS
    )
    ordered_cases = tuple(
        sorted(
            selected,
            key=lambda item: (
                _case_order_digest(item.identity, split, seed),
                case_identity_bytes(item.identity),
            ),
        )
    )
    records: list[ScheduleRecord] = []
    for case_index, assignment in enumerate(ordered_cases):
        rotation = case_index % len(variants)
        rotated = variants[rotation:] + variants[:rotation]
        case_digest = _case_order_digest(assignment.identity, split, seed)
        for position, variant in enumerate(rotated, 1):
            records.append(
                ScheduleRecord(
                    schema_version="rcaeval-re2-v2-dev.scheduled-run.v1",
                    run_id=_run_id(assignment.identity, split, variant, seed),
                    split=split,
                    identity=assignment.identity,
                    variant=variant,
                    arm_position=position,
                    case_order_digest_sha256=case_digest,
                )
            )
    return tuple(records)


def build_smoke_schedule(
    assignments: tuple[SplitAssignment, ...],
    design_schedule: tuple[ScheduleRecord, ...],
    *,
    seed: int,
) -> tuple[ScheduleRecord, ...]:
    if type(seed) is not int or seed != SPLIT_SEED:
        raise ValueError("development smoke seed differs from pre-registration")
    design = tuple(item for item in assignments if item.split is SplitName.DESIGN)
    grouped: dict[tuple[str, str], list[SplitAssignment]] = {}
    for item in design:
        grouped.setdefault((item.identity.system, item.identity.fault), []).append(item)
    expected_groups = {
        (system, fault) for system in _ROOT_SERVICES for fault in _FAULTS
    }
    if set(grouped) != expected_groups:
        raise ValueError("development smoke strata differ from frozen design")
    selected_identities = {
        min(
            group,
            key=lambda item: (
                _case_order_digest(item.identity, SplitName.DESIGN, seed),
                case_identity_bytes(item.identity),
            ),
        ).identity
        for group in grouped.values()
    }
    smoke = tuple(
        record
        for record in design_schedule
        if record.identity in selected_identities
    )
    if len(smoke) != 72 or len({item.identity for item in smoke}) != 12:
        raise ValueError("development smoke is not an exact 12-case design subset")
    return smoke


def schedule_budget_summary() -> ScheduleBudgetSummary:
    return ScheduleBudgetSummary(
        design_max_provider_operations=1200,
        dev_validation_max_provider_operations=1320,
        worst_case_provider_operations=2520,
        hard_max_provider_operations=2600,
        provider_operation_headroom=80,
        smoke_additional_operations=0,
    )
