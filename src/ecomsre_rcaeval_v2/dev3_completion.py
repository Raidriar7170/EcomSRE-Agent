"""Append-only correction authority for the dev.3 DESIGN completion gate."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Literal, Mapping

from pydantic import Field

from ecomsre_rcaeval_v2.contracts import Sha256, V2Model
from ecomsre_rcaeval_v2.dev3_admission import ScheduleAdmissionLock
from ecomsre_rcaeval_v2.dev3_evaluation_root import (
    EVALUATION_LOCK_NAME,
    SOURCE_SCOPES,
    CiCheckAuthorization,
    EvaluationRootLock,
    _current_bindings,
    _remote_authorization,
)
from ecomsre_rcaeval_v2.dev3_postrun import (
    POSTRUN_LOCK_NAME,
    PostRunEvaluationLock,
    _durable_create,
    _fixed_evidence_bindings,
    _git,
    _parent_root_snapshot,
    _sha_bytes,
    _sha_file,
    _verify_preserved_roots,
    load_postrun_phase_schedules,
)
from ecomsre_rcaeval_v2.dev3_schedule import PROTOCOL_ID, ScheduleRecord
from ecomsre_rcaeval_v2.public_projection import assert_public_payload


COMPLETION_AMENDMENT_LOCK_NAME = "design-completion-amendment-lock.json"
COMPLETION_GATE_NAME = "design-completion-gate.json"
_ALLOWED_AMENDMENT_PATHS = {
    "src/ecomsre_rcaeval_v2/dev3_completion.py",
    "src/ecomsre_rcaeval_v2/dev3_evidence.py",
    "scripts/rcaeval_v2/correct_dev3_design_gate.py",
    "scripts/rcaeval_v2/prepare_dev3_completion_amendment.py",
    "scripts/rcaeval_v2/publish_dev3_results.py",
    "tests/benchmarks/rcaeval_v2/test_dev3_postrun.py",
    "tests/benchmarks/rcaeval_v2/test_dev3_provider_gates.py",
}


class DesignCompletionAmendmentLock(V2Model):
    """Bind an append-only G3 correction to frozen original DESIGN outputs."""

    schema_version: Literal[
        "rcaeval-re2-v2-dev3.design-completion-amendment-lock.v1"
    ]
    protocol_id: Literal["rcaeval-re2-v2-dev.3"]
    postrun_evaluation_lock_sha256: Sha256
    postrun_evaluation_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    amendment_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    draft_pr_number: int = Field(ge=1)
    draft_pr_url: str = Field(min_length=1, max_length=2048)
    required_ci_checks: tuple[CiCheckAuthorization, ...]
    changed_paths: tuple[str, ...]
    amendment_diff_sha256: Sha256
    source_tree_hashes: dict[str, Sha256]
    config_hashes: dict[str, Sha256]
    frozen_evidence_hashes: dict[str, Sha256]
    original_design_output_hashes: dict[str, Sha256]
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


def _validate_amendment_paths(paths: tuple[str, ...]) -> None:
    rejected = tuple(path for path in paths if path not in _ALLOWED_AMENDMENT_PATHS)
    if rejected:
        raise ValueError(
            "dev3 DESIGN completion amendment changed an unauthorized path: "
            + ", ".join(rejected)
        )
    required = {
        "src/ecomsre_rcaeval_v2/dev3_evidence.py",
        "src/ecomsre_rcaeval_v2/dev3_completion.py",
    }
    if not required.issubset(paths):
        raise ValueError("dev3 DESIGN completion amendment lacks its contract repair")
    if any("dev4" in path.casefold() for path in paths):
        raise ValueError("dev3 DESIGN completion amendment contains forbidden dev4 work")


def _commit_diff(
    project_root: Path, base_commit: str
) -> tuple[str, tuple[str, ...], str]:
    if _git(project_root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("dev3 DESIGN completion amendment requires a clean worktree")
    commit = _git(project_root, "rev-parse", "HEAD")
    ancestry = subprocess.run(
        ("git", "merge-base", "--is-ancestor", base_commit, commit),
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if ancestry.returncode != 0 or commit == base_commit:
        raise ValueError("dev3 DESIGN completion amendment must descend from post-run")
    paths = tuple(
        item
        for item in _git(
            project_root, "diff", "--name-only", f"{base_commit}..{commit}"
        ).splitlines()
        if item
    )
    _validate_amendment_paths(paths)
    diff = subprocess.run(
        ("git", "diff", "--binary", f"{base_commit}..{commit}"),
        cwd=project_root,
        check=True,
        capture_output=True,
    ).stdout
    return commit, paths, _sha_bytes(diff)


def _tree_hash_at_commit(project_root: Path, commit: str, scope: Path) -> str:
    names = tuple(
        item
        for item in _git(
            project_root,
            "ls-tree",
            "-r",
            "--name-only",
            commit,
            "--",
            str(scope),
        ).splitlines()
        if item
    )
    if not names:
        raise ValueError("dev3 post-run committed source scope is empty")
    entries = []
    for name in names:
        payload = subprocess.run(
            ("git", "show", f"{commit}:{name}"),
            cwd=project_root,
            check=True,
            capture_output=True,
        ).stdout
        entries.append({"path": name, "sha256": _sha_bytes(payload)})
    return _sha_bytes(
        json.dumps(
            entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    )


def _source_hashes_at_commit(project_root: Path, commit: str) -> dict[str, str]:
    return {
        name: _tree_hash_at_commit(project_root, commit, scope)
        for name, scope in SOURCE_SCOPES.items()
    }


def _source_hashes(project_root: Path) -> dict[str, str]:
    commit = _git(project_root, "rev-parse", "HEAD")
    return _source_hashes_at_commit(project_root, commit)


def _original_output_hashes(control_root: Path, output_root: Path) -> dict[str, str]:
    return {
        "design_aggregate_sha256": _sha_file(
            control_root / "evidence/design-aggregate.json"
        ),
        "design_gate_sha256": _sha_file(control_root / "evidence/design-gate.json"),
        "design_outcomes_sha256": _sha_file(
            output_root / "evidence/design-outcomes.json"
        ),
    }


def _validate_original_inconsistent_gate(
    control_root: Path, *, postrun_lock_sha256: str
) -> None:
    gate = json.loads(
        (control_root / "evidence/design-gate.json").read_text(encoding="utf-8")
    )
    aggregate = json.loads(
        (control_root / "evidence/design-aggregate.json").read_text(encoding="utf-8")
    )
    assert_public_payload(gate)
    assert_public_payload(aggregate)
    if not isinstance(gate, dict) or not isinstance(aggregate, dict):
        raise ValueError("dev3 original DESIGN public output is invalid")
    checks = gate.get("checks")
    bindings = gate.get("source_bindings")
    aggregate_bindings = aggregate.get("source_bindings")
    if not all(
        isinstance(value, dict)
        for value in (checks, bindings, aggregate_bindings)
    ):
        raise ValueError("dev3 original DESIGN gate binding is invalid")
    assert isinstance(checks, dict)
    assert isinstance(bindings, dict)
    assert isinstance(aggregate_bindings, dict)
    failed = tuple(
        name
        for name, value in checks.items()
        if not isinstance(value, dict) or not bool(value.get("passed"))
    )
    schema = checks.get("final_judge_schema_dev3")
    taxonomy = aggregate.get("exact_failure_taxonomy")
    if (
        gate.get("state") != "V2_DEV3_DESIGN_GATE_PASSED"
        or failed != ("final_judge_schema_dev3",)
        or not isinstance(schema, dict)
        or type(schema.get("invalid_schema_count")) is not int
        or not isinstance(taxonomy, list)
        or bindings.get("postrun_evaluation_lock_sha256")
        != postrun_lock_sha256
        or aggregate_bindings.get("postrun_evaluation_lock_sha256")
        != postrun_lock_sha256
    ):
        raise ValueError("dev3 original DESIGN gate is not the bounded G3 mismatch")
    invalid = schema["invalid_schema_count"]
    exact_schema_failures = sum(
        item.get("count", 0)
        for item in taxonomy
        if isinstance(item, dict)
        and item.get("operation_type") == "FINAL_JUDGE"
        and item.get("failure_stage") == "OUTPUT_VALIDATION"
        and item.get("failure_code") == "PROVIDER_OUTPUT_INVALID_SCHEMA"
    )
    if invalid <= 0 or exact_schema_failures != invalid:
        raise ValueError("dev3 original DESIGN schema terminals lack exact attribution")


def _snapshot(
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    *,
    project_root: Path,
    preserved_roots: Mapping[str, Path],
) -> tuple[
    PostRunEvaluationLock,
    EvaluationRootLock,
    ScheduleAdmissionLock,
    dict[str, str],
    dict[str, object],
]:
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
    postrun_path = control_root / "locks" / POSTRUN_LOCK_NAME
    postrun_sha = _sha_file(postrun_path)
    postrun = PostRunEvaluationLock.model_validate_json(
        postrun_path.read_text(encoding="utf-8")
    )
    if (
        postrun.parent_evaluation_root_lock_sha256
        != _sha_file(control_root / "locks" / EVALUATION_LOCK_NAME)
        or postrun.parent_implementation_commit != parent.implementation_commit
        or postrun.provider_access_authorized
        or postrun.provider_calls_authorized != 0
        or postrun.source_tree_hashes
        != _source_hashes_at_commit(project_root, postrun.evaluation_commit)
    ):
        raise ValueError("dev3 post-run predecessor lock drift")
    current = _current_bindings(
        project_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
    )
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
        if getattr(postrun, name) != current[name]:
            raise ValueError(f"dev3 post-run predecessor {name} drift")
    combined_root = output_root / "evidence/combined-design-journal"
    frozen = _fixed_evidence_bindings(
        control_root=control_root,
        output_root=output_root,
        smoke_journal_root=smoke_journal_root,
        design_journal_root=design_journal_root,
        combined_root=combined_root,
    )
    for name, expected in frozen.items():
        if getattr(postrun, name) != expected:
            raise ValueError(f"dev3 post-run predecessor {name} drift")
    _validate_original_inconsistent_gate(
        control_root, postrun_lock_sha256=postrun_sha
    )
    return postrun, parent, admission, frozen, current


def prepare_design_completion_amendment(
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    *,
    project_root: Path,
    preserved_roots: Mapping[str, Path],
) -> DesignCompletionAmendmentLock:
    postrun, _parent, _admission, frozen, current = _snapshot(
        control_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
        project_root=project_root,
        preserved_roots=preserved_roots,
    )
    commit, changed_paths, diff_sha = _commit_diff(
        project_root, postrun.evaluation_commit
    )
    remote = _remote_authorization(project_root, commit)
    lock_path = control_root / "locks" / COMPLETION_AMENDMENT_LOCK_NAME
    completion_gate = control_root / "evidence" / COMPLETION_GATE_NAME
    if lock_path.exists() or completion_gate.exists():
        raise FileExistsError("dev3 DESIGN completion amendment already exists")
    lock = DesignCompletionAmendmentLock.model_validate(
        {
            "schema_version": (
                "rcaeval-re2-v2-dev3.design-completion-amendment-lock.v1"
            ),
            "protocol_id": PROTOCOL_ID,
            "postrun_evaluation_lock_sha256": _sha_file(
                control_root / "locks" / POSTRUN_LOCK_NAME
            ),
            "postrun_evaluation_commit": postrun.evaluation_commit,
            "amendment_commit": commit,
            **remote,
            "changed_paths": changed_paths,
            "amendment_diff_sha256": diff_sha,
            "source_tree_hashes": _source_hashes(project_root),
            "config_hashes": current["config_hashes"],
            "frozen_evidence_hashes": frozen,
            "original_design_output_hashes": _original_output_hashes(
                control_root, output_root
            ),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "provider_access_authorized": False,
            "provider_calls_authorized": 0,
        }
    )
    _durable_create(lock_path, _canonical_bytes(lock.model_dump(mode="json")))
    return lock


def verify_design_completion_amendment_ready(
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    *,
    project_root: Path,
    preserved_roots: Mapping[str, Path],
) -> tuple[
    DesignCompletionAmendmentLock,
    PostRunEvaluationLock,
    EvaluationRootLock,
    ScheduleAdmissionLock,
]:
    postrun, parent, admission, frozen, current = _snapshot(
        control_root,
        private_schedule_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
        project_root=project_root,
        preserved_roots=preserved_roots,
    )
    lock_path = control_root / "locks" / COMPLETION_AMENDMENT_LOCK_NAME
    lock = DesignCompletionAmendmentLock.model_validate_json(
        lock_path.read_text(encoding="utf-8")
    )
    commit, changed_paths, diff_sha = _commit_diff(
        project_root, postrun.evaluation_commit
    )
    remote = _remote_authorization(project_root, commit)
    remote_checks = remote["required_ci_checks"]
    if not isinstance(remote_checks, tuple):
        raise ValueError("dev3 DESIGN completion CI authorization is invalid")
    expected_checks = tuple(
        CiCheckAuthorization.model_validate(item) for item in remote_checks
    )
    if (
        lock.postrun_evaluation_lock_sha256
        != _sha_file(control_root / "locks" / POSTRUN_LOCK_NAME)
        or lock.postrun_evaluation_commit != postrun.evaluation_commit
        or lock.amendment_commit != commit
        or lock.changed_paths != changed_paths
        or lock.amendment_diff_sha256 != diff_sha
        or lock.draft_pr_number != remote["draft_pr_number"]
        or lock.draft_pr_url != remote["draft_pr_url"]
        or lock.required_ci_checks != expected_checks
        or lock.source_tree_hashes != _source_hashes(project_root)
        or lock.config_hashes != current["config_hashes"]
        or lock.frozen_evidence_hashes != frozen
        or lock.original_design_output_hashes
        != _original_output_hashes(control_root, output_root)
        or lock.provider_access_authorized
        or lock.provider_calls_authorized != 0
    ):
        raise ValueError("dev3 DESIGN completion amendment binding drift")
    return lock, postrun, parent, admission


def load_completion_phase_schedules(
    private_schedule_root: Path,
    *,
    parent: EvaluationRootLock,
    admission: ScheduleAdmissionLock,
) -> tuple[tuple[ScheduleRecord, ...], tuple[ScheduleRecord, ...]]:
    return load_postrun_phase_schedules(
        private_schedule_root, parent=parent, admission=admission
    )
