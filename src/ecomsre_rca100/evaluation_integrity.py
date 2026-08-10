"""Evaluator-only loading from freshly verified frozen RCA100 evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Mapping

from ecomsre.evidence.hashes import sha256_file
from ecomsre_rca100.entity import EntityCatalog, load_entity_catalog
from ecomsre_rca100.evaluator import RCA100GroundTruth, load_answer_key
from ecomsre_rca100.lifecycle import (
    PrivateRoots,
    RCA100Schedule,
    load_strict_json,
    schedule_sha256,
    tree_sha256,
    verify_tree_binding,
)
from ecomsre_rca100.runner import RCA100TerminalRecord


_ALLOWED_GENERATED_PUBLIC_PATHS = frozenset(
    {
        "docs/results/rca100-metrics-arbitration-v1-final.json",
        "docs/results/rca100-metrics-arbitration-v1-final.md",
        "docs/results/rca100-metrics-arbitration-v1-human-brief.md",
        "docs/review-evidence/rca100-metrics-arbitration-v1/current-disposition.json",
        "docs/review-evidence/rca100-metrics-arbitration-v1/source-lock-public.json",
        "docs/review-evidence/rca100-metrics-arbitration-v1/execution-integrity.json",
        "docs/review-evidence/rca100-metrics-arbitration-v1/human-review-checklist.md",
    }
)


@dataclass(frozen=True, slots=True)
class FrozenEvaluationInputs:
    schedule: RCA100Schedule
    terminals: dict[str, RCA100TerminalRecord]
    truths: dict[str, RCA100GroundTruth]
    catalogs: dict[str, EntityCatalog]
    alert_entity_types: dict[str, str]
    protocol_lock: Mapping[str, object]
    terminal_lock: Mapping[str, object]
    answer_lock: Mapping[str, object]


def _mapping(path: Path, *, label: str) -> Mapping[str, object]:
    value = load_strict_json(path)
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is invalid")
    return value


def _git_head(path: Path) -> str:
    return subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def verify_frozen_repository(
    repository_root: Path,
    *,
    implementation_commit: str,
) -> None:
    if _git_head(repository_root) != implementation_commit:
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: implementation HEAD differs")
    status = subprocess.run(
        ("git", "status", "--porcelain=v1", "-uall"),
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    dirty_paths = {
        line[3:].split(" -> ")[-1]
        for line in status.splitlines()
        if line
    }
    unexpected = dirty_paths - _ALLOWED_GENERATED_PUBLIC_PATHS
    if unexpected:
        raise ValueError(
            "BLOCKED_PROTOCOL_DRIFT: frozen repository paths changed: "
            f"{sorted(unexpected)}"
        )


def _integer(value: object, *, label: str) -> int:
    if not isinstance(value, int):
        raise ValueError(f"{label} is not an integer")
    return value


def _require_state_lock_binding(
    control_root: Path,
    *,
    state: str,
    field: str,
    lock_path: Path,
) -> None:
    record = _mapping(control_root / "state" / f"{state}.json", label=state)
    if record.get(field) != sha256_file(lock_path):
        raise ValueError(f"{state} does not bind its create-once lock")


def _alert_entity_type(task_path: Path) -> str:
    task = _mapping(task_path, label="RCA100 agent task")
    alert = task.get("alert_entity")
    if not isinstance(alert, Mapping):
        return "UNAVAILABLE"
    value = alert.get("entity_type")
    return value.strip() if isinstance(value, str) and value.strip() else "UNAVAILABLE"


def load_frozen_evaluation_inputs(
    *,
    roots: PrivateRoots,
    repository_root: Path,
    protocol_id: str,
    expected_source_commit: str,
    expected_input_tree_sha256: str,
    expected_fresh_input_tree_sha256: str,
    expected_input_file_count: int,
    expected_schedule_sha256: str,
    expected_model: str,
    expected_prompt_sha256: str,
    expected_output_schema_sha256: str,
) -> FrozenEvaluationInputs:
    protocol_path = roots.control / "locks" / "protocol-freeze.json"
    terminal_path = roots.control / "locks" / "terminal-records-lock.json"
    answer_path = roots.evaluator / "locks" / "answer-key-lock.json"
    protocol_lock = _mapping(protocol_path, label="protocol freeze")
    terminal_lock = _mapping(terminal_path, label="terminal records lock")
    answer_lock = _mapping(answer_path, label="answer-key lock")
    _require_state_lock_binding(
        roots.control,
        state="PROTOCOL_FROZEN",
        field="protocol_freeze_sha256",
        lock_path=protocol_path,
    )
    _require_state_lock_binding(
        roots.control,
        state="TERMINAL_RECORDS_LOCKED",
        field="terminal_records_lock_sha256",
        lock_path=terminal_path,
    )
    _require_state_lock_binding(
        roots.control,
        state="ANSWER_KEY_ACQUIRED",
        field="answer_key_lock_sha256",
        lock_path=answer_path,
    )
    expected_protocol = {
        "source_commit": expected_source_commit,
        "input_tree_sha256": expected_input_tree_sha256,
        "fresh_content_tree_sha256": expected_fresh_input_tree_sha256,
        "schedule_sha256": expected_schedule_sha256,
        "fixed_denominator": 103,
        "model": expected_model,
        "prompt_sha256": expected_prompt_sha256,
        "output_schema_sha256": expected_output_schema_sha256,
    }
    if any(protocol_lock.get(key) != value for key, value in expected_protocol.items()):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: frozen protocol binding differs")
    implementation_commit = protocol_lock.get("implementation_commit")
    if not isinstance(implementation_commit, str):
        raise ValueError("frozen protocol lacks an implementation commit")
    verify_frozen_repository(
        repository_root,
        implementation_commit=implementation_commit,
    )
    config_sha256, _ = tree_sha256(repository_root / "config" / protocol_id)
    if protocol_lock.get("config_tree_sha256") != config_sha256:
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: frozen config tree differs")
    if _git_head(roots.input_source) != expected_source_commit:
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: input source commit differs")
    if _git_head(roots.evaluator_source) != expected_source_commit:
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: evaluator source commit differs")
    if (roots.input_source / "RCA100" / "answer_key").exists():
        raise ValueError("BLOCKED_GROUND_TRUTH_LEAKAGE: runtime source gained labels")
    verify_tree_binding(
        roots.input_source / "RCA100" / "cases",
        expected_sha256=expected_fresh_input_tree_sha256,
        expected_file_count=expected_input_file_count,
        label="RCA100 label-blind input",
    )
    source_lock = _mapping(
        roots.control / "source" / "input-source-lock.json",
        label="input source lock",
    )
    if (
        source_lock.get("source_commit") != expected_source_commit
        or source_lock.get("input_tree_sha256") != expected_input_tree_sha256
        or source_lock.get("file_count") != expected_input_file_count
        or source_lock.get("answer_key_materialized") is not False
    ):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: input source lock differs")
    terminal_sha = terminal_lock.get("terminal_tree_sha256")
    attempt_sha = terminal_lock.get("attempt_tree_sha256")
    provider_sha = terminal_lock.get("provider_attempt_tree_sha256")
    answer_sha = answer_lock.get("answer_key_tree_sha256")
    counts = (
        terminal_lock.get("terminal_records"),
        terminal_lock.get("run_attempts"),
        terminal_lock.get("provider_sidecar_records"),
        answer_lock.get("answer_key_files"),
    )
    if not all(isinstance(item, str) for item in (terminal_sha, attempt_sha, provider_sha, answer_sha)):
        raise ValueError("frozen evidence lock lacks a tree hash")
    if not all(isinstance(item, int) for item in counts):
        raise ValueError("frozen evidence lock lacks a file count")
    verify_tree_binding(
        roots.output / "terminals",
        expected_sha256=str(terminal_sha),
        expected_file_count=_integer(counts[0], label="terminal record count"),
        label="terminal record",
    )
    verify_tree_binding(
        roots.journal / "run-attempts",
        expected_sha256=str(attempt_sha),
        expected_file_count=_integer(counts[1], label="run attempt count"),
        label="run attempt",
    )
    verify_tree_binding(
        roots.journal / "runs",
        expected_sha256=str(provider_sha),
        expected_file_count=_integer(counts[2], label="Provider sidecar count"),
        label="Provider attempt",
    )
    answer_root = roots.evaluator_source / "RCA100" / "answer_key"
    verify_tree_binding(
        answer_root,
        expected_sha256=str(answer_sha),
        expected_file_count=_integer(counts[3], label="answer-key file count"),
        label="answer key",
    )
    schedule = RCA100Schedule.model_validate_json(
        (roots.schedule / "schedule.json").read_text(encoding="utf-8")
    )
    if schedule_sha256(schedule) != expected_schedule_sha256:
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: private schedule differs")
    terminals = {
        terminal.opaque_case_id: terminal
        for path in sorted((roots.output / "terminals").glob("*.json"))
        if (
            terminal := RCA100TerminalRecord.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        )
    }
    truths = load_answer_key(answer_root)
    catalogs: dict[str, EntityCatalog] = {}
    alert_entity_types: dict[str, str] = {}
    for record in schedule.records:
        case_root = roots.input_source / "RCA100" / "cases" / record.source_task_id
        catalogs[record.source_task_id] = load_entity_catalog(
            case_root / "topology.json"
        )
        alert_entity_types[record.source_task_id] = _alert_entity_type(
            case_root / "task.json"
        )
    return FrozenEvaluationInputs(
        schedule=schedule,
        terminals=terminals,
        truths=truths,
        catalogs=catalogs,
        alert_entity_types=alert_entity_types,
        protocol_lock=protocol_lock,
        terminal_lock=terminal_lock,
        answer_lock=answer_lock,
    )


__all__ = [
    "FrozenEvaluationInputs",
    "load_frozen_evaluation_inputs",
    "verify_frozen_repository",
]
