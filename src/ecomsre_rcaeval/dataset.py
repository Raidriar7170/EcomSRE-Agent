"""Fail-closed discovery and audit for the two development-visible RE2 systems."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt

from ecomsre_rcaeval.contracts import FaultName, RCAEvalModel


class DevSystem(str, Enum):
    RE2_OB = "RE2-OB"
    RE2_SS = "RE2-SS"


@dataclass(frozen=True, slots=True)
class TelemetryCase:
    case_id: str
    system: str
    root: Path
    metrics_path: Path
    logs_path: Path
    traces_path: Path | None
    inject_time: int


@dataclass(frozen=True, slots=True)
class DevCase(TelemetryCase):
    root_cause_service: str
    fault: FaultName
    instance: str


class DatasetAudit(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.dataset-audit.v1"] = (
        "rcaeval-re2.dataset-audit.v1"
    )
    system: DevSystem
    case_count: StrictInt = Field(gt=0)
    service_count: StrictInt = Field(gt=0)
    fault_count: StrictInt = Field(gt=0)
    metrics_cases: StrictInt = Field(ge=0)
    logs_cases: StrictInt = Field(ge=0)
    traces_cases: StrictInt = Field(ge=0)
    timestamp_min: float
    timestamp_max: float
    extracted_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics_schema_variants: StrictInt = Field(gt=0)
    logs_schema_variants: StrictInt = Field(gt=0)
    traces_schema_variants: StrictInt = Field(ge=0)


def _regular(path: Path, label: str) -> Path:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"RCAEval case requires a regular {label}")
    return path


def _read_inject_time(case_root: Path) -> int:
    path = _regular(case_root / "inject_time.txt", "inject_time.txt")
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError("RCAEval inject time is invalid") from error
    if value < 0:
        raise ValueError("RCAEval inject time must be nonnegative")
    return value


def _metrics_path(case_root: Path) -> Path:
    candidates = tuple(
        path
        for path in (case_root / "simple_metrics.csv", case_root / "data.csv")
        if path.exists()
    )
    if len(candidates) != 1:
        raise ValueError("RCAEval case requires exactly one metrics CSV")
    return _regular(candidates[0], "metrics CSV")


def discover_dev_cases(root: Path, system: DevSystem) -> tuple[DevCase, ...]:
    if root.name != system.value:
        raise ValueError("RCAEval development dataset root name mismatch")
    if not root.is_dir() or root.is_symlink():
        raise ValueError("RCAEval development dataset root is invalid")
    groups = tuple(sorted(path for path in root.iterdir() if path.is_dir()))
    cases: list[DevCase] = []
    for group in groups:
        if group.is_symlink():
            raise ValueError("RCAEval case group must not be a symlink")
        parts = group.name.rsplit("_", 1)
        if len(parts) != 2:
            raise ValueError("RCAEval case group does not encode service and fault")
        service, fault_text = parts
        if fault_text not in {"cpu", "mem", "disk", "delay", "loss", "socket"}:
            raise ValueError("RCAEval case group has an unsupported fault")
        fault: FaultName = fault_text  # type: ignore[assignment]
        instance_roots = tuple(
            sorted(
                path
                for path in group.iterdir()
                if path.is_dir() and path.name in {"1", "2", "3"}
            )
        )
        for instance_root in instance_roots:
            if instance_root.is_symlink():
                raise ValueError("RCAEval case root must not be a symlink")
            metrics = _metrics_path(instance_root)
            logs = _regular(instance_root / "logs.csv", "logs.csv")
            traces_candidate = instance_root / "traces.csv"
            traces: Path | None
            if system is DevSystem.RE2_OB:
                traces = _regular(traces_candidate, "traces.csv")
            elif traces_candidate.exists():
                raise ValueError("RCAEval RE2-SS unexpectedly contains traces")
            else:
                traces = None
            cases.append(
                DevCase(
                    case_id=f"{system.value.lower()}-case-{len(cases) + 1:04d}",
                    system=system.value,
                    root=instance_root,
                    metrics_path=metrics,
                    logs_path=logs,
                    traces_path=traces,
                    inject_time=_read_inject_time(instance_root),
                    root_cause_service=service,
                    fault=fault,
                    instance=instance_root.name,
                )
            )
    if not cases:
        raise ValueError("RCAEval development dataset contains no cases")
    return tuple(cases)


def load_sanitized_cases(root: Path) -> tuple[TelemetryCase, ...]:
    """Load only opaque Agent-visible cases; never reads evaluator-only mapping."""

    from ecomsre_rcaeval.sanitize import verify_sanitized_holdout

    verify_sanitized_holdout(root, expected_cases=90)
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        manifest_cases = manifest["cases"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("sanitized manifest is invalid") from error
    if not isinstance(manifest_cases, list) or len(manifest_cases) != 90:
        raise ValueError("sanitized manifest case count is invalid")
    cases: list[TelemetryCase] = []
    for index in range(1, 91):
        case_id = f"tt-case-{index:04d}"
        case_root = root / case_id
        metrics_candidates = tuple(
            path
            for path in (case_root / "metrics.csv", case_root / "simple_metrics.csv")
            if path.is_file()
        )
        if len(metrics_candidates) != 1:
            raise ValueError("sanitized case requires exactly one metrics file")
        logs_path = _regular(case_root / "logs.csv", "sanitized logs.csv")
        traces_path = _regular(case_root / "traces.csv", "sanitized traces.csv")
        item = manifest_cases[index - 1]
        if not isinstance(item, dict):
            raise ValueError("sanitized manifest case is invalid")
        if item["case_id"] != case_id or type(item["inject_time"]) is not int:
            raise ValueError("sanitized manifest ordering is invalid")
        cases.append(
            TelemetryCase(
                case_id=case_id,
                system="RE2-TT",
                root=case_root,
                metrics_path=metrics_candidates[0],
                logs_path=logs_path,
                traces_path=traces_path,
                inject_time=item["inject_time"],
            )
        )
    return tuple(cases)


def _timestamp_bounds(path: Path) -> tuple[float, float]:
    minimum = math.inf
    maximum = -math.inf
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "time" not in reader.fieldnames:
            raise ValueError("RCAEval metrics CSV requires a time column")
        for row in reader:
            if row.get("time", "").strip() == "":
                continue
            try:
                value = float(row["time"])
            except (TypeError, ValueError) as error:
                raise ValueError("RCAEval metrics timestamp is invalid") from error
            if not math.isfinite(value):
                raise ValueError("RCAEval metrics timestamp must be finite")
            minimum = min(minimum, value)
            maximum = max(maximum, value)
    if not math.isfinite(minimum) or not math.isfinite(maximum):
        raise ValueError("RCAEval metrics CSV contains no rows")
    return minimum, maximum


def _manifest_sha256(cases: tuple[DevCase, ...]) -> str:
    digest = hashlib.sha256()
    for case in cases:
        paths = [case.metrics_path, case.logs_path, case.root / "inject_time.txt"]
        if case.traces_path is not None:
            paths.append(case.traces_path)
        for path in sorted(paths):
            digest.update(path.relative_to(case.root.parent.parent).as_posix().encode())
            digest.update(b"\0")
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            digest.update(b"\0")
    return digest.hexdigest()


def _schema_manifest(cases: tuple[DevCase, ...]) -> tuple[str, int, int, int]:
    variants: dict[str, set[tuple[str, ...]]] = {
        "metrics": set(),
        "logs": set(),
        "traces": set(),
    }
    for case in cases:
        paths = {
            "metrics": case.metrics_path,
            "logs": case.logs_path,
        }
        if case.traces_path is not None:
            paths["traces"] = case.traces_path
        for modality, path in paths.items():
            with path.open("r", encoding="utf-8", newline="") as handle:
                header = next(csv.reader(handle), None)
            if not header or len(header) != len(set(header)):
                raise ValueError("RCAEval telemetry schema header is invalid")
            variants[modality].add(tuple(header))
    payload = json.dumps(
        {
            modality: [list(header) for header in sorted(headers)]
            for modality, headers in sorted(variants.items())
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return (
        hashlib.sha256(payload).hexdigest(),
        len(variants["metrics"]),
        len(variants["logs"]),
        len(variants["traces"]),
    )


def audit_dev_dataset(
    root: Path,
    system: DevSystem,
    *,
    expected_cases: int = 90,
    require_locked_distribution: bool = False,
) -> DatasetAudit:
    cases = discover_dev_cases(root, system)
    if len(cases) != expected_cases:
        raise ValueError("RCAEval development dataset case count mismatch")
    services = {case.root_cause_service for case in cases}
    faults = {case.fault for case in cases}
    if require_locked_distribution:
        if len(services) != 5 or faults != {
            "cpu",
            "mem",
            "disk",
            "delay",
            "loss",
            "socket",
        }:
            raise ValueError("RCAEval development dataset distribution mismatch")
        counts = {
            (service, fault): sum(
                case.root_cause_service == service and case.fault == fault
                for case in cases
            )
            for service in services
            for fault in faults
        }
        if set(counts.values()) != {3}:
            raise ValueError("RCAEval service-fault strata require three instances")
    bounds = tuple(_timestamp_bounds(case.metrics_path) for case in cases)
    schema_sha, metrics_schemas, logs_schemas, traces_schemas = _schema_manifest(cases)
    return DatasetAudit(
        system=system,
        case_count=len(cases),
        service_count=len(services),
        fault_count=len(faults),
        metrics_cases=len(cases),
        logs_cases=sum(case.logs_path.is_file() for case in cases),
        traces_cases=sum(case.traces_path is not None for case in cases),
        timestamp_min=min(item[0] for item in bounds),
        timestamp_max=max(item[1] for item in bounds),
        extracted_manifest_sha256=_manifest_sha256(cases),
        schema_manifest_sha256=schema_sha,
        metrics_schema_variants=metrics_schemas,
        logs_schema_variants=logs_schemas,
        traces_schema_variants=traces_schemas,
    )
