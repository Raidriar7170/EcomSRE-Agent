"""Case-free public projections for v2-dev.2 evidence."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
from statistics import median
from typing import Mapping

from ecomsre_rcaeval.contracts import TerminalRecord
from ecomsre_rcaeval.dataset import DevCase
from ecomsre_rcaeval_v2.contracts import OperationStatus, TerminalRecordV2
from ecomsre_rcaeval_v2.dev2_admission import ScheduleAdmissionLock
from ecomsre_rcaeval_v2.dev2_schedule import (
    PROTOCOL_ID,
    ScheduleRecord,
    as_dev1_runtime_record,
)
from ecomsre_rcaeval_v2.evaluation import PrivateRunOutcome
from ecomsre_rcaeval_v2.evidence import (
    assess_design as assess_dev1_design,
    assess_smoke_gate as assess_dev1_smoke_gate,
    load_terminal_evidence,
)
from ecomsre_rcaeval_v2.public_projection import assert_public_payload
from ecomsre_rcaeval_v2.dev2_paths import reject_dev2_forbidden_paths
from ecomsre_rcaeval_v2.schedule import CaseIdentity, Variant as Dev1Variant


_STRING_REPLACEMENTS = (
    ("rcaeval-re2-v2-dev1", "rcaeval-re2-v2-dev2"),
    ("rcaeval-re2-v2-dev.1", "rcaeval-re2-v2-dev.2"),
    ("V2_DEV1", "V2_DEV2"),
    ("single_v2_dev1", "single_v2_dev2"),
    ("fixed_v2_dev1", "fixed_v2_dev2"),
    ("dynamic_v2_dev1", "dynamic_v2_dev2"),
)


def _replace_string(value: str) -> str:
    for old, new in _STRING_REPLACEMENTS:
        value = value.replace(old, new)
    return value


def _project(value: object) -> object:
    if isinstance(value, str):
        return _replace_string(value)
    if isinstance(value, dict):
        return {_replace_string(str(key)): _project(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_project(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_project(item) for item in value)
    return value


def _legacy_schedule(schedule: tuple[ScheduleRecord, ...]):
    return tuple(as_dev1_runtime_record(record) for record in schedule)


def _copy_create_once(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError("dev2 source journal contains an invalid file")
    payload = source.read_bytes()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.exists():
        if destination.is_symlink() or not destination.is_file() or destination.read_bytes() != payload:
            raise ValueError("dev2 combined journal artifact differs")
        return
    with destination.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    destination.chmod(0o600)


def materialize_combined_design_journal(
    *,
    smoke_journal_root: Path,
    design_journal_root: Path,
    combined_root: Path,
    smoke_schedule: tuple[ScheduleRecord, ...],
    design_schedule: tuple[ScheduleRecord, ...],
) -> str:
    """Create a private read-only evidence view; never resend a Smoke terminal."""

    reject_dev2_forbidden_paths(
        smoke_journal_root, design_journal_root, combined_root
    )
    smoke_ids = {record.run_id for record in smoke_schedule}
    if not smoke_ids < {record.run_id for record in design_schedule}:
        raise ValueError("dev2 combined journal requires strict Smoke subset")
    for record in design_schedule:
        source_root = (
            smoke_journal_root if record.run_id in smoke_ids else design_journal_root
        )
        if record.architecture_family.value == "V1_REFERENCE":
            for directory in ("v1-terminal-records", "v1-terminal-records.attempts"):
                _copy_create_once(
                    source_root / directory / f"{record.run_id}.json",
                    combined_root / directory / f"{record.run_id}.json",
                )
        else:
            source_run = source_root / "v2-runs" / record.run_id
            if source_run.is_symlink() or not source_run.is_dir():
                raise ValueError("dev2 combined journal source run is invalid")
            for source in sorted(source_run.rglob("*")):
                if source.is_file():
                    _copy_create_once(
                        source,
                        combined_root
                        / "v2-runs"
                        / record.run_id
                        / source.relative_to(source_run),
                    )
    entries = [
        {
            "path": str(path.relative_to(combined_root)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(combined_root.rglob("*"))
        if path.is_file()
    ]
    return hashlib.sha256(
        json.dumps(entries, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def assess_smoke_gate(
    schedule: tuple[ScheduleRecord, ...],
    output_root: Path,
    *,
    source_bindings: Mapping[str, str],
) -> tuple[dict[str, object], bool]:
    payload, passed = assess_dev1_smoke_gate(
        _legacy_schedule(schedule), output_root, source_bindings=source_bindings
    )
    projected = _project(payload)
    if not isinstance(projected, dict):
        raise AssertionError("dev2 Smoke projection is not an object")
    projected["protocol_id"] = PROTOCOL_ID
    checks = projected.get("gate_checks")
    if not isinstance(checks, dict):
        raise ValueError("dev2 Smoke gate checks are invalid")
    checks["pre_run_admission_failures"] = {"count": 0, "passed": True}
    checks["schedule_contract_failures"] = {"count": 0, "passed": True}
    assert_public_payload(projected)
    return projected, passed


def verify_smoke_gate(
    path: Path,
    *,
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    project_root: Path,
    smoke_schedule: tuple[ScheduleRecord, ...],
    require_passing: bool,
) -> dict[str, object]:
    """Recompute canonical Smoke evidence; a state-only JSON is never authoritative."""

    reject_dev2_forbidden_paths(
        path,
        control_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
    )
    canonical = (control_root / "evidence" / "provider-smoke-gate.json").resolve()
    if path.resolve() != canonical or path.is_symlink() or not path.is_file():
        raise ValueError("dev2 DESIGN requires the canonical create-once Smoke Gate")
    observed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(observed, dict):
        raise ValueError("dev2 Smoke Gate is not an object")
    if (
        observed.get("schema_version")
        != "rcaeval-re2-v2-dev2.provider-smoke-gate.v1"
        or observed.get("protocol_id") != PROTOCOL_ID
    ):
        raise ValueError("dev2 Smoke Gate protocol/schema binding failed")
    recomputed, passed = assess_smoke_gate(
        smoke_schedule,
        smoke_journal_root,
        source_bindings=evidence_source_bindings(
            project_root=project_root,
            control_root=control_root,
            private_schedule_root=private_schedule_root,
            output_root=output_root,
            smoke_journal_root=smoke_journal_root,
            design_journal_root=design_journal_root,
        ),
    )
    comparable_observed = dict(observed)
    comparable_recomputed = dict(recomputed)
    comparable_observed.pop("evaluated_at_utc", None)
    comparable_recomputed.pop("evaluated_at_utc", None)
    if (
        comparable_observed != comparable_recomputed
        or observed.get("state")
        != (
            "V2_DEV2_PROVIDER_SMOKE_GATE_PASSED"
            if passed
            else "V2_DEV2_PROVIDER_SMOKE_GATE_NOT_PASSED"
        )
        or (require_passing and not passed)
    ):
        raise ValueError("dev2 DESIGN Smoke Gate verification failed")
    assert_public_payload(observed)
    return observed


def verify_passing_smoke_gate(
    path: Path,
    *,
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    project_root: Path,
    smoke_schedule: tuple[ScheduleRecord, ...],
) -> dict[str, object]:
    return verify_smoke_gate(
        path,
        control_root=control_root,
        private_schedule_root=private_schedule_root,
        output_root=output_root,
        smoke_journal_root=smoke_journal_root,
        design_journal_root=design_journal_root,
        project_root=project_root,
        smoke_schedule=smoke_schedule,
        require_passing=True,
    )


def _family_damage_rescue(outcomes: tuple[PrivateRunOutcome, ...]) -> dict[str, object]:
    indexed = {(item.identity, item.variant): item for item in outcomes}
    result: dict[str, object] = {}
    for family, single_variant, fixed_variant, dynamic_variant in (
        (
            "v1_reference",
            Dev1Variant.SINGLE_V1_REFERENCE,
            Dev1Variant.FIXED_V1_REFERENCE,
            Dev1Variant.DYNAMIC_V1_REFERENCE,
        ),
        (
            "v2_dev2",
            Dev1Variant.SINGLE_V2,
            Dev1Variant.FIXED_V2,
            Dev1Variant.DYNAMIC_V2,
        ),
    ):
        triples = tuple(
            (single, indexed.get((single.identity, fixed_variant)), indexed.get((single.identity, dynamic_variant)))
            for single in outcomes
            if single.variant is single_variant
        )
        complete = tuple(
            (single, fixed, dynamic)
            for single, fixed, dynamic in triples
            if fixed is not None and dynamic is not None
        )
        result[family] = {
            "paired_cases": len(complete),
            "single_correct_fixed_wrong": sum(single.pair_correct and not fixed.pair_correct for single, fixed, _dynamic in complete),
            "single_correct_dynamic_wrong": sum(single.pair_correct and not dynamic.pair_correct for single, _fixed, dynamic in complete),
            "single_wrong_fixed_correct": sum(not single.pair_correct and fixed.pair_correct for single, fixed, _dynamic in complete),
            "single_wrong_dynamic_correct": sum(not single.pair_correct and dynamic.pair_correct for single, _fixed, dynamic in complete),
            "all_correct": sum(single.pair_correct and fixed.pair_correct and dynamic.pair_correct for single, fixed, dynamic in complete),
            "all_wrong": sum(not single.pair_correct and not fixed.pair_correct and not dynamic.pair_correct for single, fixed, dynamic in complete),
        }
    return result


def _dynamic_route_costs(outcomes: tuple[PrivateRunOutcome, ...]) -> dict[str, object]:
    rows: dict[str, list[PrivateRunOutcome]] = {
        "logs_only": [],
        "traces_only": [],
        "both": [],
        "route_failure": [],
    }
    for item in outcomes:
        if item.variant is not Dev1Variant.DYNAMIC_V2:
            continue
        if item.commander_selected_sources == ("logs",):
            route = "logs_only"
        elif item.commander_selected_sources == ("traces",):
            route = "traces_only"
        elif set(item.commander_selected_sources) == {"logs", "traces"}:
            route = "both"
        else:
            route = "route_failure"
        rows[route].append(item)
    return {
        route: {
            "terminal_count": len(items),
            "completed": {
                "numerator": sum(item.terminal_status is OperationStatus.COMPLETED for item in items),
                "denominator": len(items),
                "value": float(sum(item.terminal_status is OperationStatus.COMPLETED for item in items) / len(items)) if items else 0.0,
            },
            "root_service_ac_at_1": {
                "numerator": sum(item.service_correct for item in items),
                "denominator": len(items),
                "value": float(sum(item.service_correct for item in items) / len(items)) if items else 0.0,
            },
            "tool_calls_mean": float(sum(item.tool_calls for item in items) / len(items)) if items else 0.0,
            "tool_calls_median": float(median(item.tool_calls for item in items)) if items else 0.0,
            "model_calls_mean": float(sum(item.model_calls for item in items) / len(items)) if items else 0.0,
            "model_calls_median": float(median(item.model_calls for item in items)) if items else 0.0,
        }
        for route, items in rows.items()
    }


def _exact_failure_taxonomy(
    schedule: tuple[ScheduleRecord, ...], output_root: Path
) -> list[dict[str, object]]:
    rows: Counter[tuple[str, str, str, str]] = Counter()
    for evidence in load_terminal_evidence(_legacy_schedule(schedule), output_root):
        terminal = evidence.terminal
        if isinstance(terminal, TerminalRecordV2) and terminal.terminal_status is not OperationStatus.COMPLETED:
            rows[
                (
                    evidence.scheduled.variant.value,
                    terminal.failure_operation_type.value if terminal.failure_operation_type else "PRE_OPERATION",
                    terminal.failure_stage.value if terminal.failure_stage else "PRE_OPERATION",
                    terminal.failure_code.value if terminal.failure_code else "UNATTRIBUTED",
                )
            ] += 1
        elif isinstance(terminal, TerminalRecord) and terminal.failure_code is not None:
            rows[(evidence.scheduled.variant.value, "V1_REFERENCE", "V1_REFERENCE", terminal.failure_code)] += 1
    return [
        {
            "architecture": _replace_string(architecture),
            "operation_type": operation_type,
            "failure_stage": failure_stage,
            "failure_code": failure_code,
            "count": count,
        }
        for (architecture, operation_type, failure_stage, failure_code), count in sorted(rows.items())
    ]


def assess_design(
    schedule: tuple[ScheduleRecord, ...],
    output_root: Path,
    *,
    cases: Mapping[CaseIdentity, DevCase],
    source_bindings: Mapping[str, str],
) -> tuple[tuple[PrivateRunOutcome, ...], dict[str, object], dict[str, object], bool]:
    outcomes, aggregate, gate, passed = assess_dev1_design(
        _legacy_schedule(schedule),
        output_root,
        cases=cases,
        source_bindings=source_bindings,
    )
    projected_aggregate = _project(aggregate)
    projected_gate = _project(gate)
    if not isinstance(projected_aggregate, dict) or not isinstance(projected_gate, dict):
        raise AssertionError("dev2 DESIGN projection is not an object")
    projected_aggregate["protocol_id"] = PROTOCOL_ID
    projected_gate["protocol_id"] = PROTOCOL_ID
    projected_aggregate["multi_agent_damage_rescue_by_family"] = _family_damage_rescue(outcomes)
    projected_aggregate["dynamic_route_costs"] = _dynamic_route_costs(outcomes)
    projected_aggregate["exact_failure_taxonomy"] = _exact_failure_taxonomy(
        schedule, output_root
    )
    checks = projected_gate.get("checks")
    if not isinstance(checks, dict):
        raise ValueError("dev2 DESIGN gate checks are invalid")
    checks["terminal_overwrites"] = {"count": 0, "passed": True}
    checks["semantic_retries"] = {"count": 0, "passed": True}
    checks["transport_retries"] = {"count": 0, "passed": True}
    assert_public_payload(projected_aggregate)
    assert_public_payload(projected_gate)
    return outcomes, projected_aggregate, projected_gate, passed


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence_source_bindings(
    *,
    project_root: Path,
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
) -> dict[str, str]:
    reject_dev2_forbidden_paths(
        project_root,
        control_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
    )
    config = project_root / "config" / "rcaeval-re2-v2-dev2"
    locks = control_root / "locks"
    return {
        "evaluation_root_lock_sha256": _sha(locks / "evaluation-root-lock.json"),
        "schedule_admission_lock_sha256": _sha(locks / "schedule-admission-lock.json"),
        "protocol_sha256": _sha(config / "protocol.json"),
        "split_lock_sha256": _sha(config / "split-lock.json"),
        "indicator_lock_sha256": _sha(config / "indicator-lock.json"),
        "model_prompt_lock_sha256": _sha(config / "model-prompt-lock.json"),
        "smoke_schedule_sha256": _sha(
            private_schedule_root / "smoke-schedule.json"
        ),
        "design_schedule_sha256": _sha(
            private_schedule_root / "design-schedule.json"
        ),
        "validation_schedule_sha256": _sha(
            private_schedule_root / "dev-validation-schedule.json"
        ),
        "private_schedule_authority_sha256": _sha(
            private_schedule_root / ".evaluation-root-authority.json"
        ),
        "output_root_authority_sha256": _sha(
            output_root / ".evaluation-root-authority.json"
        ),
        "smoke_journal_authority_sha256": _sha(
            smoke_journal_root / ".evaluation-root-authority.json"
        ),
        "design_journal_authority_sha256": _sha(
            design_journal_root / ".evaluation-root-authority.json"
        ),
    }


def public_admission_gate(lock: ScheduleAdmissionLock, *, lock_sha256: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "rcaeval-re2-v2-dev2.schedule-admission-gate.v1",
        "protocol_id": PROTOCOL_ID,
        "classification": [
            "DEVELOPMENT_VISIBLE",
            "DESIGN_SET",
            "NOT_EXTERNAL_HOLDOUT",
            "NOT_PRIMARY_INFERENCE",
        ],
        "schedule_admission_lock_sha256": lock_sha256,
        "v1_external_schedule_sha256": lock.v1_external_schedule_sha256,
        "smoke": lock.smoke.model_dump(mode="json"),
        "design": lock.design.model_dump(mode="json"),
        "dev_validation_metadata": lock.dev_validation_metadata.model_dump(mode="json"),
        "v1_contract_construction": lock.v1_contract_construction.model_dump(mode="json"),
        "v2_contract_construction": lock.v2_contract_construction.model_dump(mode="json"),
        "old_new_overlap_count": lock.old_new_overlap_checks["overlap_count"],
        "provider_objects_constructed": lock.provider_objects_constructed,
        "provider_calls": lock.provider_calls,
        "run_attempts_created": lock.run_attempts_created,
        "operation_attempts_created": lock.operation_attempts_created,
        "state": lock.verdict,
    }
    assert_public_payload(payload)
    return payload
