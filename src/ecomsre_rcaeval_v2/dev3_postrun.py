"""Read-only successor authorization for post-Provider dev.3 evaluation."""

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
from ecomsre_rcaeval_v2.dev3_admission import (
    ADMISSION_LOCK_NAME,
    ScheduleAdmissionLock,
    load_admission_lock,
)
from ecomsre_rcaeval_v2.dev3_audit import Dev2FailureAuditLock, audit_tree_sha256
from ecomsre_rcaeval_v2.dev3_evaluation_root import (
    DEV2_FAILURE_AUDIT_LOCK_NAME,
    EVALUATION_LOCK_NAME,
    SOURCE_SCOPES,
    CiCheckAuthorization,
    EvaluationRootLock,
    _current_bindings,
    _remote_authorization,
    _tree_hash,
    _validate_roots,
)
from ecomsre_rcaeval_v2.dev3_evidence import (
    materialize_combined_design_journal,
    verify_passing_smoke_gate,
)
from ecomsre_rcaeval_v2.dev3_execution import _load_locked_phase_schedule
from ecomsre_rcaeval_v2.dev3_paths import require_pairwise_disjoint, tree_sha256
from ecomsre_rcaeval_v2.dev3_schedule import PROTOCOL_ID, ScheduleRecord


POSTRUN_LOCK_NAME = "postrun-evaluation-successor-lock.json"
_ALLOWED_POSTRUN_PATHS = {
    "src/ecomsre_rcaeval_v2/evaluation.py",
    "src/ecomsre_rcaeval_v2/dev3_postrun.py",
    "scripts/rcaeval_v2/evaluate_dev3_design.py",
    "scripts/rcaeval_v2/prepare_dev3_postrun_evaluation.py",
    "scripts/rcaeval_v2/publish_dev3_results.py",
    "tests/benchmarks/rcaeval_v2/test_evaluation.py",
    "tests/benchmarks/rcaeval_v2/test_dev3_postrun.py",
    "tests/benchmarks/rcaeval_v2/test_dev3_provider_gates.py",
}
_PRESERVED_ROOT_NAMES = {
    "v2_dev_v1",
    "v2_dev1_control",
    "v2_dev1_output",
    "v2_dev2_control",
    "v2_dev2_schedule",
    "v2_dev2_output",
    "v2_dev2_smoke",
    "v2_dev2_design",
}


class PostRunEvaluationLock(V2Model):
    """Bind frozen Provider evidence to a CI-approved read-only evaluator."""

    schema_version: Literal[
        "rcaeval-re2-v2-dev3.postrun-evaluation-successor-lock.v1"
    ]
    protocol_id: Literal["rcaeval-re2-v2-dev.3"]
    parent_evaluation_root_lock_sha256: Sha256
    parent_implementation_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    evaluation_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    draft_pr_number: int = Field(ge=1)
    draft_pr_url: str = Field(min_length=1, max_length=2048)
    required_ci_checks: tuple[CiCheckAuthorization, ...]
    changed_paths: tuple[str, ...]
    postrun_diff_sha256: Sha256
    source_tree_hashes: dict[str, Sha256]
    config_hashes: dict[str, Sha256]
    smoke_schedule_sha256: Sha256
    design_schedule_sha256: Sha256
    validation_schedule_sha256: Sha256
    schedule_set_sha256: Sha256
    private_schedule_root_identity_sha256: Sha256
    private_output_root_identity_sha256: Sha256
    smoke_journal_root_identity_sha256: Sha256
    design_journal_root_identity_sha256: Sha256
    dev2_failure_audit_lock_sha256: Sha256
    schedule_admission_lock_sha256: Sha256
    f0_public_sha256: Sha256
    f0_private_sha256: Sha256
    schedule_admission_gate_sha256: Sha256
    provider_smoke_gate_sha256: Sha256
    smoke_journal_tree_sha256: Sha256
    design_journal_tree_sha256: Sha256
    combined_design_journal_tree_sha256: Sha256
    created_at_utc: str
    provider_access_authorized: Literal[False]
    provider_calls_authorized: Literal[0]


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
        raise ValueError("dev3 post-run bound file is missing or invalid")
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


def _validate_postrun_paths(paths: tuple[str, ...]) -> None:
    rejected = tuple(path for path in paths if path not in _ALLOWED_POSTRUN_PATHS)
    if rejected:
        raise ValueError(
            "dev3 post-run evaluator changed an unauthorized path: "
            + ", ".join(rejected)
        )
    if "src/ecomsre_rcaeval_v2/evaluation.py" not in paths:
        raise ValueError("dev3 post-run evaluator lacks the aggregate projection repair")
    if any("dev4" in path.casefold() for path in paths):
        raise ValueError("dev3 post-run evaluator contains forbidden dev4 work")


def _postrun_commit(
    project_root: Path, parent_implementation_commit: str
) -> tuple[str, tuple[str, ...], str]:
    if _git(project_root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("dev3 post-run evaluation requires a clean worktree")
    commit = _git(project_root, "rev-parse", "HEAD")
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ValueError("dev3 post-run evaluation requires a full commit")
    ancestry = subprocess.run(
        ("git", "merge-base", "--is-ancestor", parent_implementation_commit, commit),
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if ancestry.returncode != 0 or commit == parent_implementation_commit:
        raise ValueError("dev3 post-run commit must descend from the Provider implementation")
    paths = tuple(
        item
        for item in _git(
            project_root,
            "diff",
            "--name-only",
            f"{parent_implementation_commit}..{commit}",
        ).splitlines()
        if item
    )
    _validate_postrun_paths(paths)
    diff = subprocess.run(
        ("git", "diff", "--binary", f"{parent_implementation_commit}..{commit}"),
        cwd=project_root,
        check=True,
        capture_output=True,
    ).stdout
    return commit, paths, _sha_bytes(diff)


def _parent_root_snapshot(
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    *,
    project_root: Path,
) -> tuple[EvaluationRootLock, ScheduleAdmissionLock, Dev2FailureAuditLock]:
    repo, control, schedules, output, smoke, design = _validate_roots(
        project_root,
        control_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
    )
    lock_path = control / "locks" / EVALUATION_LOCK_NAME
    parent = EvaluationRootLock.model_validate_json(lock_path.read_text(encoding="utf-8"))
    current = _current_bindings(repo, schedules, output, smoke, design)
    for name, expected in current.items():
        if name == "source_tree_hashes":
            continue
        if getattr(parent, name) != expected:
            raise ValueError(f"dev3 frozen parent evaluation {name} drift")
    parent_sha = _sha_file(lock_path)
    for role, root, identity in (
        ("PRIVATE_SCHEDULE", schedules, parent.private_schedule_root_identity_sha256),
        ("PRIVATE_OUTPUT", output, parent.private_output_root_identity_sha256),
        ("SMOKE_JOURNAL", smoke, parent.smoke_journal_root_identity_sha256),
        ("DESIGN_JOURNAL", design, parent.design_journal_root_identity_sha256),
    ):
        marker = root / ".evaluation-root-authority.json"
        expected_marker = {
            "schema_version": "rcaeval-re2-v2-dev3.output-root-authority.v1",
            "protocol_id": PROTOCOL_ID,
            "role": role,
            "evaluation_root_lock_sha256": parent_sha,
            "root_identity_sha256": identity,
        }
        if marker.is_symlink() or not marker.is_file() or json.loads(
            marker.read_text(encoding="utf-8")
        ) != expected_marker:
            raise ValueError("dev3 frozen parent root authority drift")
    audit_path = control / "locks" / DEV2_FAILURE_AUDIT_LOCK_NAME
    audit = Dev2FailureAuditLock.model_validate_json(
        audit_path.read_text(encoding="utf-8")
    )
    admission_path = control / "locks" / ADMISSION_LOCK_NAME
    admission = load_admission_lock(admission_path)
    if audit.evaluation_root_lock_sha256 != parent_sha:
        raise ValueError("dev3 frozen parent audit binding drift")
    expected_admission = {
        "implementation_commit": parent.implementation_commit,
        "split_lock_sha256": parent.split_lock_sha256,
        "dev2_failure_audit_lock_sha256": _sha_file(audit_path),
        "retry_policy_lock_sha256": parent.retry_policy_lock_sha256,
        "smoke_schedule_sha256": parent.smoke_schedule_sha256,
        "design_schedule_sha256": parent.design_schedule_sha256,
        "validation_schedule_sha256": parent.validation_schedule_sha256,
        "schedule_set_sha256": parent.schedule_set_sha256,
        "private_schedule_root_identity_sha256": (
            parent.private_schedule_root_identity_sha256
        ),
        "private_output_root_identity_sha256": (
            parent.private_output_root_identity_sha256
        ),
        "smoke_journal_root_identity_sha256": (
            parent.smoke_journal_root_identity_sha256
        ),
        "design_journal_root_identity_sha256": (
            parent.design_journal_root_identity_sha256
        ),
    }
    for name, expected in expected_admission.items():
        if getattr(admission, name) != expected:
            raise ValueError(f"dev3 frozen parent admission {name} drift")
    if any(
        value != 0
        for value in (
            admission.provider_objects_constructed,
            admission.provider_calls,
            admission.run_attempts_created,
            admission.operation_attempts_created,
            admission.provider_attempts_created,
        )
    ) or admission.dev_validation_metadata.values_accessed:
        raise ValueError("dev3 frozen parent zero-Provider admission drift")
    return parent, admission, audit


def _verify_preserved_roots(
    preserved_roots: Mapping[str, Path],
    *,
    admission: ScheduleAdmissionLock,
    audit: Dev2FailureAuditLock,
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
) -> None:
    if set(preserved_roots) != _PRESERVED_ROOT_NAMES:
        raise ValueError("dev3 post-run preserved roots are incomplete")
    roots = {name: path.resolve() for name, path in preserved_roots.items()}
    require_pairwise_disjoint(
        control_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
        *roots.values(),
    )
    identities = {
        name: _sha_bytes(str(path).encode()) for name, path in roots.items()
    }
    if admission.preserved_root_identity_sha256 != identities:
        raise ValueError("dev3 post-run preserved root identity drift")
    schedules = {
        "v2_dev_v1_design": roots["v2_dev_v1"] / "schedule/design-schedule.json",
        "v2_dev_v1_validation": (
            roots["v2_dev_v1"] / "schedule/dev-validation-schedule.json"
        ),
        "v2_dev1_design": (
            roots["v2_dev1_control"] / "schedules/design-schedule.json"
        ),
        "v2_dev1_validation": (
            roots["v2_dev1_control"] / "schedules/dev-validation-schedule.json"
        ),
        "v2_dev2_design": roots["v2_dev2_schedule"] / "design-schedule.json",
        "v2_dev2_validation": (
            roots["v2_dev2_schedule"] / "dev-validation-schedule.json"
        ),
    }
    if admission.preserved_schedule_hashes != {
        name: _sha_file(path) for name, path in schedules.items()
    }:
        raise ValueError("dev3 post-run preserved schedule drift")
    if admission.preserved_evidence_hashes != {
        f"{name}_tree": tree_sha256(path) for name, path in roots.items()
    }:
        raise ValueError("dev3 post-run preserved terminal evidence drift")
    if (
        audit.dev2_smoke_gate_sha256
        != _sha_file(
            roots["v2_dev2_control"] / "evidence/provider-smoke-gate.json"
        )
        or audit.dev2_smoke_schedule_sha256
        != _sha_file(roots["v2_dev2_schedule"] / "smoke-schedule.json")
        or audit.dev2_smoke_journal_tree_sha256
        != audit_tree_sha256(roots["v2_dev2_smoke"])
    ):
        raise ValueError("dev3 post-run failure audit source drift")


def _source_tree_hashes(project_root: Path) -> dict[str, str]:
    return {name: _tree_hash(project_root, scope) for name, scope in SOURCE_SCOPES.items()}


def _fixed_evidence_bindings(
    *,
    control_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    combined_root: Path,
) -> dict[str, str]:
    return {
        "dev2_failure_audit_lock_sha256": _sha_file(
            control_root / "locks" / DEV2_FAILURE_AUDIT_LOCK_NAME
        ),
        "schedule_admission_lock_sha256": _sha_file(
            control_root / "locks" / ADMISSION_LOCK_NAME
        ),
        "f0_public_sha256": _sha_file(control_root / "evidence/f0-public.json"),
        "f0_private_sha256": _sha_file(output_root / "evidence/f0-private.json"),
        "schedule_admission_gate_sha256": _sha_file(
            control_root / "evidence/schedule-admission-gate.json"
        ),
        "provider_smoke_gate_sha256": _sha_file(
            control_root / "evidence/provider-smoke-gate.json"
        ),
        "smoke_journal_tree_sha256": tree_sha256(smoke_journal_root),
        "design_journal_tree_sha256": tree_sha256(design_journal_root),
        "combined_design_journal_tree_sha256": tree_sha256(combined_root),
    }


def _schedules(
    private_schedule_root: Path,
    *,
    parent: EvaluationRootLock,
    admission: ScheduleAdmissionLock,
) -> tuple[tuple[ScheduleRecord, ...], tuple[ScheduleRecord, ...]]:
    smoke = _load_locked_phase_schedule(
        private_schedule_root, "smoke", evaluation=parent, admission=admission
    )
    design = _load_locked_phase_schedule(
        private_schedule_root, "design", evaluation=parent, admission=admission
    )
    return smoke, design


def prepare_postrun_evaluation(
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    *,
    project_root: Path,
    preserved_roots: Mapping[str, Path],
) -> PostRunEvaluationLock:
    parent, admission, audit = _parent_root_snapshot(
        control_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
        project_root=project_root,
    )
    _verify_preserved_roots(
        preserved_roots,
        admission=admission,
        audit=audit,
        control_root=control_root,
        private_schedule_root=private_schedule_root,
        output_root=output_root,
        smoke_journal_root=smoke_journal_root,
        design_journal_root=design_journal_root,
    )
    evaluation_commit, changed_paths, diff_sha = _postrun_commit(
        project_root, parent.implementation_commit
    )
    remote = _remote_authorization(project_root, evaluation_commit)
    lock_path = control_root / "locks" / POSTRUN_LOCK_NAME
    if lock_path.exists():
        raise FileExistsError("dev3 post-run evaluation lock already exists")
    for path in (
        control_root / "evidence/design-aggregate.json",
        control_root / "evidence/design-gate.json",
        output_root / "evidence/design-outcomes.json",
    ):
        if path.exists():
            raise ValueError("dev3 post-run lock must precede DESIGN evaluation outputs")
    smoke_schedule, design_schedule = _schedules(
        private_schedule_root, parent=parent, admission=admission
    )
    verify_passing_smoke_gate(
        control_root / "evidence/provider-smoke-gate.json",
        control_root=control_root,
        private_schedule_root=private_schedule_root,
        output_root=output_root,
        smoke_journal_root=smoke_journal_root,
        design_journal_root=design_journal_root,
        project_root=project_root,
        smoke_schedule=smoke_schedule,
    )
    combined_root = output_root / "evidence/combined-design-journal"
    combined_sha = materialize_combined_design_journal(
        smoke_journal_root=smoke_journal_root,
        design_journal_root=design_journal_root,
        combined_root=combined_root,
        smoke_schedule=smoke_schedule,
        design_schedule=design_schedule,
    )
    if combined_sha != tree_sha256(combined_root):
        raise ValueError("dev3 post-run combined DESIGN journal hash drift")
    parent_bindings = _current_bindings(
        project_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
    )
    lock = PostRunEvaluationLock.model_validate(
        {
            "schema_version": (
                "rcaeval-re2-v2-dev3.postrun-evaluation-successor-lock.v1"
            ),
            "protocol_id": PROTOCOL_ID,
            "parent_evaluation_root_lock_sha256": _sha_file(
                control_root / "locks" / EVALUATION_LOCK_NAME
            ),
            "parent_implementation_commit": parent.implementation_commit,
            "evaluation_commit": evaluation_commit,
            **remote,
            "changed_paths": changed_paths,
            "postrun_diff_sha256": diff_sha,
            "source_tree_hashes": _source_tree_hashes(project_root),
            "config_hashes": parent_bindings["config_hashes"],
            "smoke_schedule_sha256": parent.smoke_schedule_sha256,
            "design_schedule_sha256": parent.design_schedule_sha256,
            "validation_schedule_sha256": parent.validation_schedule_sha256,
            "schedule_set_sha256": parent.schedule_set_sha256,
            "private_schedule_root_identity_sha256": (
                parent.private_schedule_root_identity_sha256
            ),
            "private_output_root_identity_sha256": (
                parent.private_output_root_identity_sha256
            ),
            "smoke_journal_root_identity_sha256": (
                parent.smoke_journal_root_identity_sha256
            ),
            "design_journal_root_identity_sha256": (
                parent.design_journal_root_identity_sha256
            ),
            **_fixed_evidence_bindings(
                control_root=control_root,
                output_root=output_root,
                smoke_journal_root=smoke_journal_root,
                design_journal_root=design_journal_root,
                combined_root=combined_root,
            ),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "provider_access_authorized": False,
            "provider_calls_authorized": 0,
        }
    )
    _durable_create(lock_path, _canonical_bytes(lock.model_dump(mode="json")))
    return lock


def verify_postrun_evaluation_ready(
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    *,
    project_root: Path,
    preserved_roots: Mapping[str, Path],
) -> tuple[PostRunEvaluationLock, EvaluationRootLock, ScheduleAdmissionLock]:
    parent, admission, audit = _parent_root_snapshot(
        control_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
        project_root=project_root,
    )
    _verify_preserved_roots(
        preserved_roots,
        admission=admission,
        audit=audit,
        control_root=control_root,
        private_schedule_root=private_schedule_root,
        output_root=output_root,
        smoke_journal_root=smoke_journal_root,
        design_journal_root=design_journal_root,
    )
    lock_path = control_root / "locks" / POSTRUN_LOCK_NAME
    lock = PostRunEvaluationLock.model_validate_json(
        lock_path.read_text(encoding="utf-8")
    )
    commit, changed_paths, diff_sha = _postrun_commit(
        project_root, parent.implementation_commit
    )
    if (
        lock.parent_evaluation_root_lock_sha256
        != _sha_file(control_root / "locks" / EVALUATION_LOCK_NAME)
        or lock.parent_implementation_commit != parent.implementation_commit
        or lock.evaluation_commit != commit
        or lock.changed_paths != changed_paths
        or lock.postrun_diff_sha256 != diff_sha
    ):
        raise ValueError("dev3 post-run evaluation commit binding drift")
    remote = _remote_authorization(project_root, commit)
    remote_checks = remote["required_ci_checks"]
    if not isinstance(remote_checks, tuple):
        raise ValueError("dev3 post-run CI authorization is invalid")
    expected_checks = tuple(
        CiCheckAuthorization.model_validate(item) for item in remote_checks
    )
    if (
        lock.draft_pr_number != remote["draft_pr_number"]
        or lock.draft_pr_url != remote["draft_pr_url"]
        or lock.required_ci_checks != expected_checks
    ):
        raise ValueError("dev3 post-run Draft PR or CI authorization drift")
    current = _current_bindings(
        project_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
    )
    if lock.source_tree_hashes != _source_tree_hashes(project_root):
        raise ValueError("dev3 post-run source tree drift")
    for name in (
        "config_hashes",
        "smoke_schedule_sha256",
        "design_schedule_sha256",
        "validation_schedule_sha256",
        "schedule_set_sha256",
        "private_schedule_root_identity_sha256",
        "private_output_root_identity_sha256",
        "smoke_journal_root_identity_sha256",
        "design_journal_root_identity_sha256",
    ):
        if getattr(lock, name) != current[name]:
            raise ValueError(f"dev3 post-run {name} drift")
    combined_root = output_root / "evidence/combined-design-journal"
    fixed = _fixed_evidence_bindings(
        control_root=control_root,
        output_root=output_root,
        smoke_journal_root=smoke_journal_root,
        design_journal_root=design_journal_root,
        combined_root=combined_root,
    )
    for name, expected in fixed.items():
        if getattr(lock, name) != expected:
            raise ValueError(f"dev3 post-run {name} drift")
    if lock.provider_access_authorized or lock.provider_calls_authorized != 0:
        raise ValueError("dev3 post-run lock cannot authorize Provider access")
    return lock, parent, admission


def load_postrun_phase_schedules(
    private_schedule_root: Path,
    *,
    parent: EvaluationRootLock,
    admission: ScheduleAdmissionLock,
) -> tuple[tuple[ScheduleRecord, ...], tuple[ScheduleRecord, ...]]:
    return _schedules(private_schedule_root, parent=parent, admission=admission)
