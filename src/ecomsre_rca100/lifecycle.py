"""Create-once private lifecycle, schedule, and integrity locks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
from typing import Any, Literal, Mapping

from pydantic import Field, StrictInt, model_validator

from ecomsre.evidence.hashes import canonical_json_bytes, sha256_file
from ecomsre_rca100.contracts import RCA100Model


STATE_CHAIN = (
    "SOURCE_LOCKED",
    "INPUTS_ACQUIRED",
    "ADAPTER_VALIDATED_NO_GT",
    "PROTOCOL_FROZEN",
    "HOLDOUT_PREFLIGHT_PASSED",
    "HOLDOUT_EXECUTED",
    "TERMINAL_RECORDS_LOCKED",
    "ANSWER_KEY_ACQUIRED",
    "UNBLINDED",
    "FINAL_REPORT_FROZEN",
)
StateName = Literal[
    "SOURCE_LOCKED",
    "INPUTS_ACQUIRED",
    "ADAPTER_VALIDATED_NO_GT",
    "PROTOCOL_FROZEN",
    "HOLDOUT_PREFLIGHT_PASSED",
    "HOLDOUT_EXECUTED",
    "TERMINAL_RECORDS_LOCKED",
    "ANSWER_KEY_ACQUIRED",
    "UNBLINDED",
    "FINAL_REPORT_FROZEN",
]


@dataclass(frozen=True, slots=True)
class PrivateRoots:
    input_source: Path
    control: Path
    schedule: Path
    journal: Path
    output: Path
    evaluator_source: Path
    evaluator: Path

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> PrivateRoots:
        names = {
            "input_source": "RCA100_INPUT_SOURCE_ROOT",
            "control": "RCA100_CONTROL_ROOT",
            "schedule": "RCA100_PRIVATE_SCHEDULE_ROOT",
            "journal": "RCA100_JOURNAL_ROOT",
            "output": "RCA100_OUTPUT_ROOT",
            "evaluator_source": "RCA100_EVALUATOR_SOURCE_ROOT",
            "evaluator": "RCA100_EVALUATOR_ROOT",
        }
        values: dict[str, Path] = {}
        for key, name in names.items():
            value = environment.get(name)
            if not value:
                raise ValueError(f"missing private root: {name}")
            path = Path(value)
            if not path.is_absolute():
                raise ValueError(f"private root must be absolute: {name}")
            values[key] = path
        return cls(**values)

    def validate(self, *, repository_root: Path, create: bool = True) -> None:
        repo = repository_root.resolve()
        roots = tuple(self.__dict__.values()) if hasattr(self, "__dict__") else (
            self.input_source,
            self.control,
            self.schedule,
            self.journal,
            self.output,
            self.evaluator_source,
            self.evaluator,
        )
        resolved: list[Path] = []
        for root in roots:
            if root.is_symlink():
                raise ValueError("private root must not be a symlink")
            if create:
                root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not root.is_dir():
                raise ValueError("private root is not a directory")
            candidate = root.resolve()
            if candidate == repo or repo in candidate.parents:
                raise ValueError("private root is inside the Git repository")
            resolved.append(candidate)
        for index, left in enumerate(resolved):
            for right in resolved[index + 1 :]:
                if left == right or left in right.parents or right in left.parents:
                    raise ValueError("private roots overlap")


class RCA100ScheduleRecord(RCA100Model):
    schema_version: Literal["rca100.schedule-record.v1"] = (
        "rca100.schedule-record.v1"
    )
    position: StrictInt = Field(ge=1, le=103)
    source_task_id: str = Field(pattern=r"^t[0-9]{3}$")
    opaque_case_id: str = Field(pattern=r"^rca100-case-[0-9]{4}$")
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class RCA100Schedule(RCA100Model):
    schema_version: Literal["rca100.private-schedule.v1"] = (
        "rca100.private-schedule.v1"
    )
    seed: Literal[20260810] = 20260810
    records: tuple[RCA100ScheduleRecord, ...] = Field(min_length=103, max_length=103)

    @model_validator(mode="after")
    def require_complete_schedule(self) -> RCA100Schedule:
        if tuple(item.position for item in self.records) != tuple(range(1, 104)):
            raise ValueError("RCA100 schedule positions are incomplete")
        if len({item.source_task_id for item in self.records}) != 103:
            raise ValueError("RCA100 schedule source tasks are not unique")
        if len({item.opaque_case_id for item in self.records}) != 103:
            raise ValueError("RCA100 schedule opaque cases are not unique")
        if len({item.run_id for item in self.records}) != 103:
            raise ValueError("RCA100 schedule run IDs are not unique")
        return self


def create_once_json(path: Path, value: object) -> str:
    content = canonical_json_bytes(value) + b"\n"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    return hashlib.sha256(content).hexdigest()


def load_strict_json(path: Path) -> object:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=lambda pairs: _strict_object(pairs),
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"invalid constant: {value}")
        ),
    )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate JSON key")
        output[key] = value
    return output


def current_state(control_root: Path) -> StateName | None:
    found = [state for state in STATE_CHAIN if (control_root / "state" / f"{state}.json").is_file()]
    if not found:
        return None
    expected = list(STATE_CHAIN[: len(found)])
    if found != expected:
        raise ValueError("RCA100 lifecycle state chain is non-contiguous")
    return found[-1]  # type: ignore[return-value]


def advance_state(
    control_root: Path,
    state: StateName,
    *,
    bindings: Mapping[str, object],
) -> str:
    index = STATE_CHAIN.index(state)
    expected_previous = None if index == 0 else STATE_CHAIN[index - 1]
    if current_state(control_root) != expected_previous:
        raise ValueError("RCA100 lifecycle transition is out of order")
    previous_sha = (
        None
        if expected_previous is None
        else sha256_file(control_root / "state" / f"{expected_previous}.json")
    )
    return create_once_json(
        control_root / "state" / f"{state}.json",
        {
            "schema_version": "rca100.state.v1",
            "state": state,
            "previous_state": expected_previous,
            "previous_state_record_sha256": previous_sha,
            "created_at_utc": datetime.now(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
            **dict(bindings),
        },
    )


def build_schedule(source_task_ids: tuple[str, ...]) -> RCA100Schedule:
    if tuple(sorted(source_task_ids)) != tuple(f"t{index:03d}" for index in range(1, 104)):
        raise ValueError("RCA100 schedule source manifest differs from t001..t103")
    shuffled = list(source_task_ids)
    random.Random(20260810).shuffle(shuffled)
    records = tuple(
        RCA100ScheduleRecord(
            position=position,
            source_task_id=source_id,
            opaque_case_id=f"rca100-case-{position:04d}",
            run_id=hashlib.sha256(
                b"\0".join(
                    (
                        b"rca100-external-run-v1",
                        str(position).encode("ascii"),
                        source_id.encode("ascii"),
                    )
                )
            ).hexdigest()[:32],
        )
        for position, source_id in enumerate(shuffled, 1)
    )
    return RCA100Schedule(records=records)


def schedule_sha256(schedule: RCA100Schedule) -> str:
    return hashlib.sha256(
        canonical_json_bytes(schedule.model_dump(mode="json"))
    ).hexdigest()


def tree_sha256(root: Path, pattern: str = "**/*") -> tuple[str, int]:
    records: list[bytes] = []
    count = 0
    for path in sorted(root.glob(pattern)):
        if path.is_symlink():
            raise ValueError("integrity tree contains a symlink")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        records.append(f"{relative}\0{sha256_file(path)}\n".encode("utf-8"))
        count += 1
    return hashlib.sha256(b"".join(records)).hexdigest(), count


__all__ = [
    "PrivateRoots",
    "RCA100Schedule",
    "RCA100ScheduleRecord",
    "STATE_CHAIN",
    "advance_state",
    "build_schedule",
    "create_once_json",
    "current_state",
    "load_strict_json",
    "schedule_sha256",
    "tree_sha256",
]
