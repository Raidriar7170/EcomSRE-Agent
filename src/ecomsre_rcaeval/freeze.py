"""Immutable protocol binding verification used at every holdout boundary."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

from ecomsre_rcaeval.artifacts import (
    canonical_json_bytes,
    read_json_object,
    sha256_bytes,
    sha256_file,
    sha256_tree,
)
from ecomsre_rcaeval.lifecycle import evidence_for_state
from ecomsre_rcaeval.protocol import CONFIG_ROOT, PROJECT_ROOT
from ecomsre_rcaeval.state import HoldoutState


REQUIRED_CONFIG_NAMES = frozenset(
    {
        "budget-lock.json",
        "dataset-lock.json",
        "holdout-policy.json",
        "prompt-lock.json",
        "protocol.json",
        "schedule-generation.json",
        "scorer-lock.json",
        "service-normalization.json",
        "statistics-lock.json",
    }
)


def current_runtime_bindings() -> dict[str, dict[str, str]]:
    observed_names = {path.name for path in CONFIG_ROOT.iterdir()}
    if observed_names != REQUIRED_CONFIG_NAMES:
        raise ValueError("protocol config file set is incomplete or unexpected")
    return {
        "config_files": {
            path.name: sha256_file(path) for path in sorted(CONFIG_ROOT.iterdir())
        },
        "source_trees": {
            "adapter": sha256_tree(
                PROJECT_ROOT / "src" / "ecomsre_rcaeval",
                include_suffixes=(".py",),
            ),
            "control_plane": sha256_tree(
                PROJECT_ROOT / "scripts" / "rcaeval",
                include_suffixes=(".py",),
            ),
        },
    }


def repository_base_commit() -> str:
    completed = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip()
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("repository base commit is invalid")
    return value


def _git_bytes(*arguments: str) -> bytes:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    return completed.stdout


def _implementation_freeze_policy() -> tuple[str, int, tuple[str, ...]]:
    protocol = read_json_object(CONFIG_ROOT / "protocol.json")
    implementation_freeze = protocol.get("implementation_freeze")
    if not isinstance(implementation_freeze, dict):
        raise ValueError("implementation freeze policy is missing")
    base_commit = implementation_freeze.get("base_commit")
    expected_count = implementation_freeze.get("expected_scoped_file_count")
    raw_paths = implementation_freeze.get("scoped_paths")
    if (
        not isinstance(base_commit, str)
        or len(base_commit) != 40
        or any(character not in "0123456789abcdef" for character in base_commit)
        or type(expected_count) is not int
        or expected_count <= 0
        or not isinstance(raw_paths, list)
        or any(not isinstance(item, str) for item in raw_paths)
    ):
        raise ValueError("implementation freeze policy is invalid")
    scoped_paths = tuple(raw_paths)
    if (
        len(scoped_paths) != expected_count
        or tuple(sorted(set(scoped_paths))) != scoped_paths
        or any(
            Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not relative
            for relative in scoped_paths
        )
    ):
        raise ValueError("implementation scoped path allowlist is invalid")
    return base_commit, expected_count, scoped_paths


def require_external_control_root(control_root: Path) -> Path:
    """Require the private control tree to be disjoint from the repository."""

    resolved_control = control_root.resolve(strict=False)
    resolved_project = PROJECT_ROOT.resolve(strict=True)
    if (
        resolved_control == resolved_project
        or resolved_project in resolved_control.parents
        or resolved_control in resolved_project.parents
    ):
        raise ValueError("control root must be external to the repository")
    return resolved_control


def require_clean_repository() -> None:
    """Fail closed on every staged, unstaged, or untracked worktree delta."""

    if _git_bytes("status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise ValueError("repository must be clean before source-bound freezing")


def implementation_snapshot(*, require_complete: bool = False) -> dict[str, object]:
    """Hash the exact committed B1 delta without embedding the freeze record."""

    implementation_commit = repository_base_commit()
    base_commit, expected_count, scoped_paths = _implementation_freeze_policy()
    subprocess.run(
        ("git", "merge-base", "--is-ancestor", base_commit, implementation_commit),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    raw_status = _git_bytes(
        "diff",
        "--name-status",
        "--no-renames",
        "-z",
        base_commit,
        implementation_commit,
    )
    fields = raw_status.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise ValueError("implementation commit has an invalid scoped delta")

    files: dict[str, dict[str, object]] = {}
    for offset in range(0, len(fields), 2):
        status = fields[offset].decode("ascii", errors="strict")
        relative = fields[offset + 1].decode("utf-8", errors="strict")
        path = Path(relative)
        if (
            status not in {"A", "M"}
            or path.is_absolute()
            or ".." in path.parts
            or relative in files
        ):
            raise ValueError("implementation scope contains an unsupported delta")
        tree = _git_bytes("ls-tree", "-z", implementation_commit, "--", relative)
        entries = tree.split(b"\0")
        if entries and entries[-1] == b"":
            entries.pop()
        if len(entries) != 1 or b"\t" not in entries[0]:
            raise ValueError("implementation scope path is not one committed blob")
        metadata, observed_path = entries[0].split(b"\t", 1)
        mode, kind, _git_object = metadata.split()
        if (
            observed_path.decode("utf-8", errors="strict") != relative
            or kind != b"blob"
            or mode not in {b"100644", b"100755"}
        ):
            raise ValueError("implementation scope contains a non-regular file")
        content = _git_bytes("show", f"{implementation_commit}:{relative}")
        files[relative] = {
            "git_status": status,
            "mode": mode.decode("ascii"),
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    if require_complete and tuple(sorted(files)) != scoped_paths:
        raise ValueError("implementation delta differs from scoped path allowlist")

    tracked_diff = _git_bytes(
        "diff",
        "--binary",
        "--full-index",
        base_commit,
        implementation_commit,
    )
    core: dict[str, object] = {
        "base_commit": base_commit,
        "implementation_commit": implementation_commit,
        "expected_scoped_file_count": expected_count,
        "scoped_file_count": len(files),
        "scoped_paths_sha256": sha256_bytes(canonical_json_bytes(scoped_paths)),
        "files": files,
        "tracked_diff_sha256": hashlib.sha256(tracked_diff).hexdigest(),
    }
    return {
        "schema_version": "rcaeval-re2.implementation-snapshot.v1",
        **core,
        "scoped_closure_sha256": sha256_bytes(canonical_json_bytes(core)),
    }


def verify_worktree_matches_snapshot(snapshot: dict[str, object]) -> None:
    """Prove that every scoped worktree file is the frozen committed blob."""

    files = snapshot.get("files")
    if not isinstance(files, dict):
        raise ValueError("implementation snapshot files are invalid")
    _base_commit, _expected_count, scoped_paths = _implementation_freeze_policy()
    if tuple(sorted(files)) != scoped_paths:
        raise ValueError("implementation snapshot differs from scoped path allowlist")
    for relative in scoped_paths:
        metadata = files.get(relative)
        path = PROJECT_ROOT / relative
        if (
            not isinstance(metadata, dict)
            or not isinstance(metadata.get("sha256"), str)
            or path.is_symlink()
            or not path.is_file()
            or sha256_file(path) != metadata["sha256"]
        ):
            raise ValueError("worktree differs from committed implementation snapshot")


def verify_protocol_freeze(control_root: Path) -> dict[str, object]:
    control_root = require_external_control_root(control_root)
    freeze_path = control_root / "locks" / "protocol-freeze.json"
    freeze = read_json_object(freeze_path)
    if set(freeze) != {
        "schema_version",
        "repository_base_commit",
        "config_files",
        "source_trees",
        "holdout_schedule_sha256",
        "development_evidence",
        "implementation_snapshot",
    } or freeze.get("schema_version") != "rcaeval-re2.protocol-freeze.v2":
        raise ValueError("protocol freeze record shape is invalid")
    if freeze.get("repository_base_commit") != repository_base_commit():
        raise ValueError("repository base commit differs from protocol freeze")
    if freeze.get("implementation_snapshot") != implementation_snapshot(
        require_complete=True
    ):
        raise ValueError("committed implementation differs from protocol freeze")
    current = current_runtime_bindings()
    if freeze.get("config_files") != current["config_files"]:
        raise ValueError("frozen RCAEval config drift detected")
    if freeze.get("source_trees") != current["source_trees"]:
        raise ValueError("frozen RCAEval source drift detected")
    schedule_path = control_root / "locks" / "holdout-schedule.json"
    if freeze.get("holdout_schedule_sha256") != sha256_file(schedule_path):
        raise ValueError("frozen RCAEval schedule drift detected")
    expected_freeze_sha = evidence_for_state(
        control_root / "state-journal",
        HoldoutState.PROTOCOL_FROZEN,
    )
    if sha256_file(freeze_path) != expected_freeze_sha:
        raise ValueError("protocol freeze record differs from state binding")
    return freeze


def verify_source_bound_snapshot(control_root: Path) -> dict[str, object]:
    """Verify an external freeze against a clean, byte-identical worktree."""

    require_clean_repository()
    freeze = verify_protocol_freeze(control_root)
    snapshot = freeze.get("implementation_snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("implementation snapshot is missing")
    verify_worktree_matches_snapshot(snapshot)
    return freeze


def verify_state_artifact(
    control_root: Path,
    state: HoldoutState,
    relative_path: str,
) -> dict[str, object]:
    path = control_root / relative_path
    expected = evidence_for_state(control_root / "state-journal", state)
    if sha256_file(path) != expected:
        raise ValueError(f"{state.value} artifact differs from state binding")
    return read_json_object(path)


def verify_ground_truth_binding(
    control_root: Path,
    truth_path: Path,
) -> str:
    preflight = verify_state_artifact(
        control_root,
        HoldoutState.HOLDOUT_PREFLIGHT_PASSED,
        "locks/holdout-preflight.json",
    )
    seal = verify_state_artifact(
        control_root,
        HoldoutState.HOLDOUT_SEALED,
        "locks/holdout-seal.json",
    )
    current_sha = sha256_file(truth_path)
    if (
        preflight.get("ground_truth_sha256") != current_sha
        or seal.get("ground_truth_sha256") != current_sha
    ):
        raise ValueError("Ground Truth drifted after holdout sealing or preflight")
    return current_sha
