"""Evaluator-only conversion of labeled raw cases into opaque telemetry roots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Literal

from pydantic import Field, StrictInt

from ecomsre_rcaeval.artifacts import read_json_object
from ecomsre_rcaeval.contracts import (
    FaultName,
    GroundTruth,
    RCAEvalModel,
)


_TELEMETRY_FILES = frozenset(
    {
        "metrics.csv",
        "simple_metrics.csv",
        "logs.csv",
        "traces.csv",
    }
)
_FORBIDDEN_PAYLOAD_MARKERS = (
    b"root_cause_service",
    b"ground_truth",
    b"evaluator-only",
)


class HoldoutSealError(ValueError):
    pass


class HoldoutSealResult(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.holdout-seal-result.v1"] = (
        "rcaeval-re2.holdout-seal-result.v1"
    )
    case_count: StrictInt = Field(gt=0)
    agent_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ground_truth_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


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


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _raw_truth(group_dir: Path, case_dir: Path, case_id: str) -> GroundTruth:
    parts = group_dir.name.rsplit("_", 1)
    if len(parts) != 2:
        raise HoldoutSealError("raw case group does not encode service and fault")
    service, fault_text = parts
    if fault_text not in {"cpu", "mem", "disk", "delay", "loss", "socket"}:
        raise HoldoutSealError("raw case Ground Truth fault is invalid")
    fault: FaultName = fault_text  # type: ignore[assignment]
    try:
        return GroundTruth(
            case_id=case_id,
            root_cause_service=service,
            fault=fault,
            instance=case_dir.name,
        )
    except ValueError as error:
        raise HoldoutSealError("raw case Ground Truth is invalid") from error


def _inject_time(case_dir: Path) -> int:
    path = case_dir / "inject_time.txt"
    if not path.is_file() or path.is_symlink():
        raise HoldoutSealError("raw case requires a regular inject_time.txt")
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeError, ValueError) as error:
        raise HoldoutSealError("raw case inject time is invalid") from error
    if value < 0:
        raise HoldoutSealError("raw case inject time must be nonnegative")
    return value


def _reject_source_links(case_dir: Path) -> None:
    for path in case_dir.rglob("*"):
        if path.is_symlink():
            raise HoldoutSealError("raw case contains a symlink")
        if path.is_dir():
            raise HoldoutSealError("nested raw telemetry directories are forbidden")


def _copy_telemetry(case_dir: Path, target: Path) -> dict[str, str]:
    _reject_source_links(case_dir)
    simple_metrics = case_dir / "simple_metrics.csv"
    metrics = case_dir / "metrics.csv"
    selected_metrics = simple_metrics if simple_metrics.is_file() else metrics
    if not selected_metrics.is_file():
        raise HoldoutSealError("raw holdout case has no metrics telemetry")
    for required_name in ("logs.csv", "traces.csv"):
        if not (case_dir / required_name).is_file():
            raise HoldoutSealError(
                f"raw holdout case is missing required {required_name}"
            )
    files = tuple(
        sorted(
            path
            for path in (
                selected_metrics,
                case_dir / "logs.csv",
                case_dir / "traces.csv",
            )
            if path.is_file()
        )
    )
    if not files:
        raise HoldoutSealError("raw case has no allowlisted telemetry")
    target.mkdir(mode=0o700)
    checksums: dict[str, str] = {}
    for source in files:
        destination = target / source.name
        shutil.copyfile(source, destination)
        destination.chmod(0o600)
        checksums[source.name] = _sha256_file(destination)
    return checksums


def _modalities(checksums: dict[str, str]) -> list[str]:
    values: list[str] = []
    if "metrics.csv" in checksums or "simple_metrics.csv" in checksums:
        values.append("metrics")
    if "logs.csv" in checksums:
        values.append("logs")
    if "traces.csv" in checksums:
        values.append("traces")
    return values


def _write_create_once(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(payload)
        path.chmod(0o600)
    except FileExistsError as error:
        raise HoldoutSealError("holdout seal output already exists") from error


def _raw_cases(raw_root: Path) -> tuple[tuple[Path, Path], ...]:
    cases: list[tuple[Path, Path]] = []
    for group_dir in sorted(raw_root.iterdir()):
        if group_dir.is_symlink() or not group_dir.is_dir():
            raise HoldoutSealError("raw holdout root must contain only case groups")
        parts = group_dir.name.rsplit("_", 1)
        if len(parts) != 2 or parts[1] not in {
            "cpu",
            "mem",
            "disk",
            "delay",
            "loss",
            "socket",
        }:
            raise HoldoutSealError("raw holdout case group is invalid")
        instances = tuple(sorted(group_dir.iterdir()))
        if not instances:
            raise HoldoutSealError("raw holdout case group is empty")
        for case_dir in instances:
            if case_dir.is_symlink() or not case_dir.is_dir():
                raise HoldoutSealError("raw case instance must be a regular directory")
            cases.append((group_dir, case_dir))
    return tuple(cases)


def _validate_locked_distribution(
    raw_cases: tuple[tuple[Path, Path], ...],
) -> None:
    grouped: dict[str, set[str]] = {}
    services: set[str] = set()
    faults: set[str] = set()
    for group, instance in raw_cases:
        service, fault = group.name.rsplit("_", 1)
        services.add(service)
        faults.add(fault)
        grouped.setdefault(group.name, set()).add(instance.name)
    if (
        len(grouped) != 30
        or len(services) != 5
        or faults != {"cpu", "mem", "disk", "delay", "loss", "socket"}
        or any(len(instances) != 3 for instances in grouped.values())
    ):
        raise HoldoutSealError(
            "raw holdout requires exactly 30 service-fault strata by three instances"
        )


def seal_holdout(
    raw_root: Path,
    sanitized_root: Path,
    evaluator_root: Path,
    *,
    expected_cases: int,
    opaque_seed: str,
) -> HoldoutSealResult:
    if not opaque_seed:
        raise HoldoutSealError("opaque seed must not be empty")
    if sanitized_root.exists() or evaluator_root.exists():
        raise HoldoutSealError("holdout seal roots must not already exist")
    if not raw_root.is_dir() or raw_root.is_symlink():
        raise HoldoutSealError("raw holdout root must be a regular directory")
    resolved = tuple(
        path.resolve() for path in (raw_root, sanitized_root, evaluator_root)
    )
    if any(
        left == right or left in right.parents or right in left.parents
        for index, left in enumerate(resolved)
        for right in resolved[index + 1 :]
    ):
        raise HoldoutSealError("raw, sanitized, and evaluator roots must be disjoint")
    raw_cases = _raw_cases(raw_root)
    if len(raw_cases) != expected_cases:
        raise HoldoutSealError("raw holdout case count mismatch")
    if expected_cases == 90:
        _validate_locked_distribution(raw_cases)
    ranked = sorted(
        raw_cases,
        key=lambda item: hashlib.sha256(
            b"\0".join(
                (
                    opaque_seed.encode("utf-8"),
                    item[0].name.encode("utf-8"),
                    item[1].name.encode("utf-8"),
                )
            )
        ).hexdigest(),
    )
    sanitized_root.mkdir(mode=0o700, parents=True)
    evaluator_root.mkdir(mode=0o700, parents=True)
    agent_cases: list[dict[str, object]] = []
    private_cases: dict[str, object] = {}
    raw_markers = tuple(
        marker.encode("utf-8")
        for marker in (
            str(raw_root.resolve()),
            *(str(case_dir.resolve()) for _, case_dir in raw_cases),
        )
    )
    for index, (group_dir, case_dir) in enumerate(ranked, start=1):
        case_id = f"tt-case-{index:04d}"
        checksums = _copy_telemetry(case_dir, sanitized_root / case_id)
        agent_cases.append(
            {
                "case_id": case_id,
                "inject_time": _inject_time(case_dir),
                "modalities": _modalities(checksums),
                "telemetry_checksums": checksums,
            }
        )
        private_cases[case_id] = _raw_truth(group_dir, case_dir, case_id).model_dump(
            mode="json", exclude={"schema_version", "case_id"}
        )
    manifest_payload = _canonical_bytes(
        {
            "schema_version": "rcaeval-re2.agent-manifest.v1",
            "cases": agent_cases,
        }
    )
    truth_payload = _canonical_bytes(
        {
            "schema_version": "rcaeval-re2.ground-truth-mapping.v1",
            "cases": private_cases,
        }
    )
    _write_create_once(sanitized_root / "manifest.json", manifest_payload)
    _write_create_once(evaluator_root / "ground-truth.json", truth_payload)
    verify_sanitized_holdout(
        sanitized_root,
        expected_cases=expected_cases,
        extra_forbidden_markers=raw_markers,
    )
    return HoldoutSealResult(
        case_count=expected_cases,
        agent_manifest_sha256=_sha256_bytes(manifest_payload),
        ground_truth_sha256=_sha256_bytes(truth_payload),
    )


def verify_sanitized_holdout(
    sanitized_root: Path,
    *,
    expected_cases: int,
    extra_forbidden_markers: tuple[bytes, ...] = (),
) -> None:
    if not sanitized_root.is_dir() or sanitized_root.is_symlink():
        raise HoldoutSealError("sanitized root must be a regular directory")
    manifest_path = sanitized_root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise HoldoutSealError("sanitized manifest is missing")
    try:
        manifest = read_json_object(manifest_path)
    except ValueError as error:
        raise HoldoutSealError("sanitized manifest is invalid") from error
    if set(manifest) != {"schema_version", "cases"}:
        raise HoldoutSealError("sanitized manifest has unexpected fields")
    if manifest.get("schema_version") != "rcaeval-re2.agent-manifest.v1":
        raise HoldoutSealError("sanitized manifest schema version is invalid")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or len(cases) != expected_cases:
        raise HoldoutSealError("sanitized manifest case count mismatch")
    expected_ids = {f"tt-case-{index:04d}" for index in range(1, expected_cases + 1)}
    observed_ids: set[str] = set()
    forbidden = _FORBIDDEN_PAYLOAD_MARKERS + extra_forbidden_markers
    manifest_bytes = _canonical_bytes(manifest)
    if any(marker.lower() in manifest_bytes.lower() for marker in forbidden):
        raise HoldoutSealError("forbidden evaluator marker in sanitized manifest")
    for item in cases:
        if not isinstance(item, dict) or set(item) != {
            "case_id",
            "inject_time",
            "modalities",
            "telemetry_checksums",
        }:
            raise HoldoutSealError("sanitized manifest case has unexpected fields")
        case_id = item.get("case_id")
        if not isinstance(case_id, str):
            raise HoldoutSealError("sanitized case identifier is invalid")
        inject_time = item.get("inject_time")
        if type(inject_time) is not int or inject_time < 0:
            raise HoldoutSealError("sanitized case inject time is invalid")
        observed_ids.add(case_id)
        case_root = sanitized_root / case_id
        if not case_root.is_dir() or case_root.is_symlink():
            raise HoldoutSealError("sanitized case root is invalid")
        checksums = item.get("telemetry_checksums")
        if not isinstance(checksums, dict) or not checksums:
            raise HoldoutSealError("sanitized telemetry checksums are invalid")
        modalities = item.get("modalities")
        if modalities != _modalities(checksums):
            raise HoldoutSealError("sanitized telemetry modalities are invalid")
        if set(checksums) != {path.name for path in case_root.iterdir()}:
            raise HoldoutSealError("sanitized telemetry file set mismatch")
        for path in case_root.iterdir():
            if path.is_symlink() or not path.is_file():
                raise HoldoutSealError("sanitized telemetry must be regular files")
            if path.name not in _TELEMETRY_FILES:
                raise HoldoutSealError("sanitized telemetry filename is not allowlisted")
            data = path.read_bytes()
            if any(marker.lower() in data.lower() for marker in forbidden):
                raise HoldoutSealError("forbidden evaluator marker in sanitized payload")
            expected_sha = checksums.get(path.name)
            if not isinstance(expected_sha, str) or _sha256_bytes(data) != expected_sha:
                raise HoldoutSealError("sanitized telemetry checksum mismatch")
    if observed_ids != expected_ids:
        raise HoldoutSealError("sanitized opaque case set mismatch")
    top_level = {path.name for path in sanitized_root.iterdir()}
    if top_level != expected_ids | {"manifest.json"}:
        raise HoldoutSealError("sanitized root contains an unexpected path")
