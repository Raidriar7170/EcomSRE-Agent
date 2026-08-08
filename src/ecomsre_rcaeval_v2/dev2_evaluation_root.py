"""Create-once evaluation authorization and Provider-ready checks for v2-dev.2."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Literal, Mapping

from pydantic import Field

from ecomsre_rcaeval_v2.contracts import Sha256, V2Model
from ecomsre_rcaeval_v2.dev2_admission import (
    ADMISSION_LOCK_NAME,
    ScheduleAdmissionLock,
    load_admission_lock,
)
from ecomsre_rcaeval_v2.dev2_schedule import PROTOCOL_ID
from ecomsre_rcaeval_v2.dev2_paths import require_pairwise_disjoint, tree_sha256


EVALUATION_LOCK_NAME = "evaluation-root-lock.json"
SOURCE_BASE_COMMIT = "3b04ef340990e136312e1e1cbcf931a385cbe250"
CONFIG_DIRECTORY = Path("config/rcaeval-re2-v2-dev2")
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
    schema_version: Literal["rcaeval-re2-v2-dev2.evaluation-root-lock.v1"]
    protocol_id: Literal["rcaeval-re2-v2-dev.2"]
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
    private_schedule_root_identity_sha256: Sha256
    private_output_root_identity_sha256: Sha256
    smoke_journal_root_identity_sha256: Sha256
    design_journal_root_identity_sha256: Sha256
    created_at_utc: str
    provider_access_authorized_after_admission: Literal[True]
    provider_calls_before_lock: Literal[0]
    run_attempts_before_lock: Literal[0]


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("dev2 evaluation-root bound file is missing or invalid")
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
    return _sha_bytes(payload)


def _git(project_root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args),
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _require_clean_commit(project_root: Path) -> str:
    if _git(project_root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("dev2 evaluation root requires a clean worktree")
    commit = _git(project_root, "rev-parse", "HEAD")
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ValueError("dev2 evaluation root requires a full implementation commit")
    tracked = set(_git(project_root, "ls-files").splitlines())
    required_files = {str(CONFIG_DIRECTORY / name) for name in CONFIG_NAMES} | {
        str(path) for path in SOURCE_SCOPES.values() if path.suffix
    }
    if not required_files.issubset(tracked):
        raise ValueError("implementation commit does not contain all dev2 files")
    for scope in SOURCE_SCOPES.values():
        if not scope.suffix and not any(
            item == str(scope) or item.startswith(f"{scope}/") for item in tracked
        ):
            raise ValueError("implementation commit has an empty dev2 source scope")
    return commit


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_roots(
    project_root: Path,
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
) -> tuple[Path, Path, Path, Path, Path, Path]:
    repo, control, schedules, output, smoke, design = require_pairwise_disjoint(
        project_root,
        control_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
    )
    if any(
        _is_within(path, repo)
        for path in (control, schedules, output, smoke, design)
    ):
        raise ValueError("dev2 external roots must live outside Git")
    return repo, control, schedules, output, smoke, design


def _tracked_files_for_scope(project_root: Path, scope: Path) -> tuple[Path, ...]:
    if scope.suffix:
        return (project_root / scope,)
    output = _git(project_root, "ls-files", "--", str(scope))
    return tuple(project_root / item for item in output.splitlines() if item)


def _tree_hash(project_root: Path, scope: Path) -> str:
    entries = [
        {"path": str(path.relative_to(project_root)), "sha256": _sha_file(path)}
        for path in _tracked_files_for_scope(project_root, scope)
    ]
    if not entries:
        raise ValueError("dev2 evaluation-root source scope is empty")
    return _sha_bytes(
        json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    )


def _current_bindings(
    project_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
) -> dict[str, object]:
    config_root = project_root / CONFIG_DIRECTORY
    config_hashes = {name: _sha_file(config_root / name) for name in CONFIG_NAMES}
    schedule_hashes = {
        name: _sha_file(private_schedule_root / name) for name in SCHEDULE_NAMES
    }
    return {
        "source_tree_hashes": {
            name: _tree_hash(project_root, scope) for name, scope in SOURCE_SCOPES.items()
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
        "private_schedule_root_identity_sha256": _sha_bytes(
            str(private_schedule_root).encode()
        ),
        "private_output_root_identity_sha256": _sha_bytes(str(output_root).encode()),
        "smoke_journal_root_identity_sha256": _sha_bytes(
            str(smoke_journal_root).encode()
        ),
        "design_journal_root_identity_sha256": _sha_bytes(
            str(design_journal_root).encode()
        ),
    }


def _run_attempt_count(output_root: Path) -> int:
    return 0 if not output_root.exists() else sum(1 for _ in output_root.rglob("run-attempt.json"))


def prepare_evaluation_root(
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    *,
    project_root: Path,
    source_base_commit: str,
    preserved_roots: Mapping[str, Path],
) -> EvaluationRootLock:
    repo, control, schedules, output, smoke, design = _validate_roots(
        project_root,
        control_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
    )
    if set(preserved_roots) != {
        "v2_dev_v1",
        "v2_dev1_control",
        "v2_dev1_output",
    }:
        raise ValueError("dev2 evaluation root preserved roots are incomplete")
    require_pairwise_disjoint(
        repo,
        control,
        schedules,
        output,
        smoke,
        design,
        *preserved_roots.values(),
    )
    lock_path = control / "locks" / EVALUATION_LOCK_NAME
    if lock_path.exists():
        raise FileExistsError("dev2 evaluation root lock already exists")
    if source_base_commit != SOURCE_BASE_COMMIT:
        raise ValueError("dev2 source base commit differs from immutable PR #15 head")
    implementation_commit = _require_clean_commit(repo)
    protocol = json.loads(
        (repo / CONFIG_DIRECTORY / "protocol.json").read_text(encoding="utf-8")
    )
    if protocol.get("source_base_commit") != SOURCE_BASE_COMMIT:
        raise ValueError("dev2 protocol source base binding drift")
    ancestry = subprocess.run(
        ("git", "merge-base", "--is-ancestor", SOURCE_BASE_COMMIT, implementation_commit),
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if ancestry.returncode != 0:
        raise ValueError("dev2 implementation commit does not descend from PR #15 head")
    for root in (output, smoke, design):
        if root.exists() and any(root.iterdir()):
            raise ValueError("dev2 external output/journal roots must start empty")
    if (
        schedules.is_symlink()
        or not schedules.is_dir()
        or {path.name for path in schedules.iterdir()} != set(SCHEDULE_NAMES)
    ):
        raise ValueError("dev2 private schedule root is missing or not freshly frozen")
    if _run_attempt_count(smoke) != 0 or _run_attempt_count(design) != 0:
        raise ValueError("dev2 journal root already contains attempts")
    bindings = _current_bindings(repo, schedules, output, smoke, design)
    lock = EvaluationRootLock.model_validate(
        {
            "schema_version": "rcaeval-re2-v2-dev2.evaluation-root-lock.v1",
            "protocol_id": PROTOCOL_ID,
            "implementation_commit": implementation_commit,
            "source_base_commit": source_base_commit,
            **bindings,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "provider_access_authorized_after_admission": True,
            "provider_calls_before_lock": 0,
            "run_attempts_before_lock": 0,
        }
    )
    lock_sha = _durable_create(lock_path, _canonical_bytes(lock.model_dump(mode="json")))
    for role, root, identity in (
        (
            "PRIVATE_SCHEDULE",
            schedules,
            lock.private_schedule_root_identity_sha256,
        ),
        ("PRIVATE_OUTPUT", output, lock.private_output_root_identity_sha256),
        ("SMOKE_JOURNAL", smoke, lock.smoke_journal_root_identity_sha256),
        ("DESIGN_JOURNAL", design, lock.design_journal_root_identity_sha256),
    ):
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
        _durable_create(
            root / ".evaluation-root-authority.json",
            _canonical_bytes(
                {
                    "schema_version": "rcaeval-re2-v2-dev2.output-root-authority.v1",
                    "protocol_id": PROTOCOL_ID,
                    "role": role,
                    "evaluation_root_lock_sha256": lock_sha,
                    "root_identity_sha256": identity,
                }
            ),
        )
    return lock


def verify_evaluation_root(
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    *,
    project_root: Path,
) -> EvaluationRootLock:
    repo, control, schedules, output, smoke, design = _validate_roots(
        project_root,
        control_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
    )
    lock_path = control / "locks" / EVALUATION_LOCK_NAME
    if lock_path.is_symlink() or not lock_path.is_file():
        raise ValueError("dev2 evaluation root lock is missing or invalid")
    try:
        lock = EvaluationRootLock.model_validate_json(lock_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError("dev2 evaluation root lock is invalid") from error
    if _require_clean_commit(repo) != lock.implementation_commit:
        raise ValueError("dev2 evaluation root implementation commit drift")
    if lock.source_base_commit != SOURCE_BASE_COMMIT:
        raise ValueError("dev2 evaluation root source base drift")
    for name, expected in _current_bindings(
        repo, schedules, output, smoke, design
    ).items():
        if getattr(lock, name) != expected:
            raise ValueError(f"dev2 evaluation root {name} drift")
    for role, root, identity, allowed in (
        (
            "PRIVATE_SCHEDULE",
            schedules,
            lock.private_schedule_root_identity_sha256,
            {".evaluation-root-authority.json", *SCHEDULE_NAMES},
        ),
        (
            "PRIVATE_OUTPUT",
            output,
            lock.private_output_root_identity_sha256,
            {".evaluation-root-authority.json", "evidence"},
        ),
        (
            "SMOKE_JOURNAL",
            smoke,
            lock.smoke_journal_root_identity_sha256,
            {".evaluation-root-authority.json", "v1-terminal-records", "v1-terminal-records.attempts", "v2-runs"},
        ),
        (
            "DESIGN_JOURNAL",
            design,
            lock.design_journal_root_identity_sha256,
            {".evaluation-root-authority.json", "v1-terminal-records", "v1-terminal-records.attempts", "v2-runs"},
        ),
    ):
        marker_path = root / ".evaluation-root-authority.json"
        if marker_path.is_symlink() or not marker_path.is_file():
            raise ValueError("dev2 external root is not authorized")
        expected_marker = {
            "schema_version": "rcaeval-re2-v2-dev2.output-root-authority.v1",
            "protocol_id": PROTOCOL_ID,
            "role": role,
            "evaluation_root_lock_sha256": _sha_file(lock_path),
            "root_identity_sha256": identity,
        }
        if json.loads(marker_path.read_text(encoding="utf-8")) != expected_marker:
            raise ValueError("dev2 external root authority drift")
        if {path.name for path in root.iterdir()} - allowed:
            raise ValueError("dev2 external root contains unauthorized entries")
    return lock


def verify_provider_ready(
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    *,
    project_root: Path,
    preserved_roots: Mapping[str, Path],
) -> tuple[EvaluationRootLock, ScheduleAdmissionLock]:
    evaluation = verify_evaluation_root(
        control_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
        project_root=project_root,
    )
    admission = load_admission_lock(control_root / "locks" / ADMISSION_LOCK_NAME)
    expected = {
        "implementation_commit": evaluation.implementation_commit,
        "split_lock_sha256": evaluation.split_lock_sha256,
        "smoke_schedule_sha256": evaluation.smoke_schedule_sha256,
        "design_schedule_sha256": evaluation.design_schedule_sha256,
        "validation_schedule_sha256": evaluation.validation_schedule_sha256,
        "schedule_set_sha256": evaluation.schedule_set_sha256,
        "private_schedule_root_identity_sha256": evaluation.private_schedule_root_identity_sha256,
        "private_output_root_identity_sha256": evaluation.private_output_root_identity_sha256,
        "smoke_journal_root_identity_sha256": evaluation.smoke_journal_root_identity_sha256,
        "design_journal_root_identity_sha256": evaluation.design_journal_root_identity_sha256,
    }
    for field, value in expected.items():
        if getattr(admission, field) != value:
            raise ValueError(f"dev2 Provider-ready admission {field} drift")
    v1_schedule_binding = json.loads(
        (project_root / "config/rcaeval-re2-v1/schedule-generation.json").read_text(
            encoding="utf-8"
        )
    ).get("expected_schedule_sha256")
    if admission.v1_external_schedule_sha256 != v1_schedule_binding:
        raise ValueError("dev2 Provider-ready v1 external schedule binding drift")
    if admission.provider_objects_constructed != 0 or admission.provider_calls != 0 or admission.run_attempts_created != 0 or admission.operation_attempts_created != 0 or admission.dev_validation_metadata.values_accessed is not False:
        raise ValueError("dev2 Provider-ready zero-call admission invariant failed")
    expected_preserved_roots = {
        "v2_dev_v1",
        "v2_dev1_control",
        "v2_dev1_output",
    }
    if set(preserved_roots) != expected_preserved_roots or set(
        admission.preserved_root_identity_sha256
    ) != expected_preserved_roots:
        raise ValueError("dev2 Provider-ready preserved root bindings are incomplete")
    resolved_preserved_roots = {
        name: path.resolve() for name, path in preserved_roots.items()
    }
    require_pairwise_disjoint(
        control_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
        *resolved_preserved_roots.values(),
    )
    for name, root in resolved_preserved_roots.items():
        identity = _sha_bytes(str(root).encode())
        if admission.preserved_root_identity_sha256.get(name) != identity:
            raise ValueError("dev2 Provider-ready preserved root identity drift")
    observed_schedule_hashes = {
        "v2_dev_v1_design": _sha_file(
            resolved_preserved_roots["v2_dev_v1"]
            / "schedule/design-schedule.json"
        ),
        "v2_dev_v1_validation": _sha_file(
            resolved_preserved_roots["v2_dev_v1"]
            / "schedule/dev-validation-schedule.json"
        ),
        "v2_dev1_design": _sha_file(
            resolved_preserved_roots["v2_dev1_control"]
            / "schedules/design-schedule.json"
        ),
        "v2_dev1_validation": _sha_file(
            resolved_preserved_roots["v2_dev1_control"]
            / "schedules/dev-validation-schedule.json"
        ),
    }
    if admission.preserved_schedule_hashes != observed_schedule_hashes:
        raise ValueError("dev2 Provider-ready preserved schedule drift")
    observed_evidence_hashes = {
        "v2_dev_v1_terminal_tree": tree_sha256(
            resolved_preserved_roots["v2_dev_v1"] / "runs"
        ),
        "v2_dev1_terminal_tree": tree_sha256(
            resolved_preserved_roots["v2_dev1_output"]
        ),
    }
    if admission.preserved_evidence_hashes != observed_evidence_hashes:
        raise ValueError("dev2 Provider-ready preserved terminal evidence drift")
    return evaluation, admission
