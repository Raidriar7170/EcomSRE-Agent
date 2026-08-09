"""External, create-once authorization root for RCAEval v2-dev.1 Provider runs."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Literal

from pydantic import Field

from ecomsre_rcaeval_v2.contracts import Sha256, V2Model


PROTOCOL_ID: Literal["rcaeval-re2-v2-dev.1"] = "rcaeval-re2-v2-dev.1"
EVALUATION_LOCK_NAME = "evaluation-root-lock.json"
CONFIG_DIRECTORY = Path("config/rcaeval-re2-v2-dev1")
CONFIG_NAMES = (
    "protocol.json",
    "dataset-lock.json",
    "split-lock.json",
    "model-prompt-lock.json",
    "budget-lock.json",
    "indicator-lock.json",
    "schedule-generation.json",
    "evaluation-policy.json",
)
SCHEDULE_NAMES = (
    "smoke-schedule.json",
    "design-schedule.json",
    "dev-validation-schedule.json",
    "schedule-set-lock.json",
)
SOURCE_SCOPES = {
    "runtime": Path("src/ecomsre_rcaeval_v2"),
    "scripts": Path("scripts/rcaeval_v2"),
    "tests": Path("tests/benchmarks/rcaeval_v2"),
    "ci": Path(".github/workflows/rcaeval-v2-dev.yml"),
}


class EvaluationRootLock(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev1.evaluation-root-lock.v1"]
    protocol_id: Literal["rcaeval-re2-v2-dev.1"]
    implementation_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_tree_hashes: dict[str, Sha256]
    config_hashes: dict[str, Sha256]
    dataset_lock_sha256: Sha256
    split_lock_sha256: Sha256
    indicator_lock_sha256: Sha256
    model_prompt_lock_sha256: Sha256
    budget_lock_sha256: Sha256
    smoke_schedule_sha256: Sha256
    design_schedule_sha256: Sha256
    validation_schedule_sha256: Sha256
    schedule_set_sha256: Sha256
    private_output_root_identity_sha256: Sha256
    created_at_utc: str
    provider_access_authorized: Literal[True]
    provider_calls_before_lock: Literal[0]
    run_attempts_before_lock: Literal[0]


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


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("evaluation-root bound file is missing or invalid")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _durable_create(path: Path, payload: bytes) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return _sha_bytes(payload)


def _git(project_root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _require_clean_commit(project_root: Path) -> str:
    status = _git(project_root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise ValueError("evaluation root requires a clean worktree")
    commit = _git(project_root, "rev-parse", "HEAD")
    if len(commit) != 40 or any(
        character not in "0123456789abcdef" for character in commit
    ):
        raise ValueError("evaluation root requires a full implementation commit")
    tracked = set(_git(project_root, "ls-files").splitlines())
    required_files = {str(CONFIG_DIRECTORY / name) for name in CONFIG_NAMES} | {
        str(path) for path in SOURCE_SCOPES.values() if path.suffix
    }
    if not required_files.issubset(tracked):
        raise ValueError("implementation commit does not contain all v2-dev.1 files")
    for path in SOURCE_SCOPES.values():
        if not path.suffix and not any(
            item == str(path) or item.startswith(f"{path}/") for item in tracked
        ):
            raise ValueError("implementation commit has an empty v2-dev.1 scope")
    return commit


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_roots(
    project_root: Path, control_root: Path, output_root: Path
) -> tuple[Path, Path, Path]:
    repo = project_root.resolve()
    control = control_root.resolve()
    output = output_root.resolve()
    if control == output or _is_within(control, output) or _is_within(output, control):
        raise ValueError("control and output roots must be disjoint")
    if _is_within(control, repo) or _is_within(output, repo):
        raise ValueError("evaluation control and output roots must live outside Git")
    return repo, control, output


def _tracked_files_for_scope(project_root: Path, scope: Path) -> tuple[Path, ...]:
    if scope.suffix:
        return (project_root / scope,)
    output = _git(project_root, "ls-files", "--", str(scope))
    return tuple(project_root / item for item in output.splitlines() if item)


def _tree_hash(project_root: Path, scope: Path) -> str:
    entries = [
        {
            "path": str(path.relative_to(project_root)),
            "sha256": _sha_file(path),
        }
        for path in _tracked_files_for_scope(project_root, scope)
    ]
    if not entries:
        raise ValueError("evaluation-root source scope is empty")
    return _sha_bytes(
        json.dumps(
            entries,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def _current_bindings(
    project_root: Path, control_root: Path, output_root: Path
) -> dict[str, object]:
    config_root = project_root / CONFIG_DIRECTORY
    config_hashes = {name: _sha_file(config_root / name) for name in CONFIG_NAMES}
    schedules_root = control_root / "schedules"
    schedule_hashes = {
        name: _sha_file(schedules_root / name) for name in SCHEDULE_NAMES
    }
    return {
        "source_tree_hashes": {
            name: _tree_hash(project_root, path) for name, path in SOURCE_SCOPES.items()
        },
        "config_hashes": config_hashes,
        "dataset_lock_sha256": config_hashes["dataset-lock.json"],
        "split_lock_sha256": config_hashes["split-lock.json"],
        "indicator_lock_sha256": config_hashes["indicator-lock.json"],
        "model_prompt_lock_sha256": config_hashes["model-prompt-lock.json"],
        "budget_lock_sha256": config_hashes["budget-lock.json"],
        "smoke_schedule_sha256": schedule_hashes["smoke-schedule.json"],
        "design_schedule_sha256": schedule_hashes["design-schedule.json"],
        "validation_schedule_sha256": schedule_hashes["dev-validation-schedule.json"],
        "schedule_set_sha256": schedule_hashes["schedule-set-lock.json"],
        "private_output_root_identity_sha256": _sha_bytes(
            str(output_root).encode("utf-8")
        ),
    }


def _run_attempt_count(output_root: Path) -> int:
    return (
        0
        if not output_root.exists()
        else sum(1 for _path in output_root.rglob("run-attempt.json"))
    )


def prepare_evaluation_root(
    control_root: Path,
    output_root: Path,
    *,
    project_root: Path,
    source_base_commit: str,
) -> EvaluationRootLock:
    """Create the external lock only from a clean committed implementation."""

    repo, control, output = _validate_roots(project_root, control_root, output_root)
    lock_path = control / "locks" / EVALUATION_LOCK_NAME
    if lock_path.exists():
        raise FileExistsError("evaluation root lock already exists")
    if len(source_base_commit) != 40 or any(
        character not in "0123456789abcdef" for character in source_base_commit
    ):
        raise ValueError("source base commit must be a full commit id")
    implementation_commit = _require_clean_commit(repo)
    if output.exists() and any(output.iterdir()):
        raise ValueError("evaluation output root must be empty before authorization")
    if _run_attempt_count(output) != 0:
        raise ValueError("evaluation output root already contains run attempts")
    bindings = _current_bindings(repo, control, output)
    lock = EvaluationRootLock.model_validate(
        {
            "schema_version": "rcaeval-re2-v2-dev1.evaluation-root-lock.v1",
            "protocol_id": PROTOCOL_ID,
            "implementation_commit": implementation_commit,
            "source_base_commit": source_base_commit,
            **bindings,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "provider_access_authorized": True,
            "provider_calls_before_lock": 0,
            "run_attempts_before_lock": 0,
        }
    )
    preflight = {
        "schema_version": "rcaeval-re2-v2-dev1.pre-provider-preflight.v1",
        "protocol_id": PROTOCOL_ID,
        "implementation_commit": implementation_commit,
        "provider_calls_before_lock": 0,
        "run_attempts_before_lock": 0,
    }
    _durable_create(
        control / "preflight" / "evaluation-root-authorization.json",
        _canonical_bytes(preflight),
    )
    lock_sha = _durable_create(
        lock_path, _canonical_bytes(lock.model_dump(mode="json"))
    )
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.chmod(0o700)
    marker = {
        "schema_version": "rcaeval-re2-v2-dev1.output-root-authority.v1",
        "protocol_id": PROTOCOL_ID,
        "evaluation_root_lock_sha256": lock_sha,
        "private_output_root_identity_sha256": (
            lock.private_output_root_identity_sha256
        ),
    }
    _durable_create(
        output / ".evaluation-root-authority.json", _canonical_bytes(marker)
    )
    return lock


def verify_evaluation_root(
    control_root: Path,
    output_root: Path,
    *,
    project_root: Path,
) -> EvaluationRootLock:
    """Fail closed on commit, config, schedule, or output-root authorization drift."""

    repo, control, output = _validate_roots(project_root, control_root, output_root)
    lock_path = control / "locks" / EVALUATION_LOCK_NAME
    if lock_path.is_symlink() or not lock_path.is_file():
        raise ValueError("evaluation root lock is missing or invalid")
    try:
        lock = EvaluationRootLock.model_validate_json(
            lock_path.read_text(encoding="utf-8")
        )
    except Exception as error:
        raise ValueError("evaluation root lock is invalid") from error
    commit = _require_clean_commit(repo)
    if commit != lock.implementation_commit:
        raise ValueError("evaluation root implementation commit drift")
    bindings = _current_bindings(repo, control, output)
    for name, expected in bindings.items():
        if getattr(lock, name) != expected:
            raise ValueError(f"evaluation root {name} drift")
    marker_path = output / ".evaluation-root-authority.json"
    if marker_path.is_symlink() or not marker_path.is_file():
        raise ValueError("evaluation output root is not authorized")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("evaluation output root authority is invalid") from error
    expected_marker = {
        "schema_version": "rcaeval-re2-v2-dev1.output-root-authority.v1",
        "protocol_id": PROTOCOL_ID,
        "evaluation_root_lock_sha256": _sha_file(lock_path),
        "private_output_root_identity_sha256": (
            lock.private_output_root_identity_sha256
        ),
    }
    if marker != expected_marker:
        raise ValueError("evaluation output root authority drift")
    allowed_entries = {
        ".evaluation-root-authority.json",
        "v1-terminal-records",
        "v1-terminal-records.attempts",
        "v2-runs",
    }
    unexpected = {path.name for path in output.iterdir()} - allowed_entries
    if unexpected:
        raise ValueError("evaluation output root contains unauthorized entries")
    return lock
