"""Create-once evaluation authorization and Provider-ready checks for v2-dev.3."""

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
from ecomsre_rcaeval_v2.dev3_provider import FailureClass
from ecomsre_rcaeval_v2.dev3_schedule import PROTOCOL_ID
from ecomsre_rcaeval_v2.dev3_paths import require_pairwise_disjoint, tree_sha256


EVALUATION_LOCK_NAME = "evaluation-root-lock.json"
SOURCE_BASE_COMMIT = "ee755db525d2df1bca5c74b7b4c9336bc183443b"
DEV2_FAILURE_AUDIT_LOCK_NAME = "dev2-provider-failure-audit.json"
CONFIG_DIRECTORY = Path("config/rcaeval-re2-v2-dev3")
CONFIG_NAMES = (
    "protocol.json",
    "dataset-lock.json",
    "split-lock.json",
    "model-prompt-lock.json",
    "budget-lock.json",
    "indicator-lock.json",
    "schedule-generation.json",
    "evaluation-policy.json",
    "transport-retry-policy.json",
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
REQUIRED_CI_WORKFLOWS = {
    "Agent mainline",
    "RCAEval RE2 v2 development",
}


class CiCheckAuthorization(V2Model):
    workflow: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    state: Literal["SUCCESS"]
    bucket: Literal["pass"]
    link: str = Field(min_length=1, max_length=2048)


class EvaluationRootLock(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev3.evaluation-root-lock.v1"]
    protocol_id: Literal["rcaeval-re2-v2-dev.3"]
    implementation_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    draft_pr_number: int = Field(ge=1)
    draft_pr_url: str = Field(min_length=1, max_length=2048)
    required_ci_checks: tuple[CiCheckAuthorization, ...]
    source_tree_hashes: dict[str, Sha256]
    config_hashes: dict[str, Sha256]
    dataset_lock_sha256: Sha256
    split_lock_sha256: Sha256
    indicator_lock_sha256: Sha256
    model_prompt_lock_sha256: Sha256
    budget_lock_sha256: Sha256
    retry_policy_lock_sha256: Sha256
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
        raise ValueError("dev3 evaluation-root bound file is missing or invalid")
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


def _is_dev3_implementation_path(path: str) -> bool:
    return (
        path == ".github/workflows/rcaeval-v2-dev.yml"
        or path.startswith("config/rcaeval-re2-v2-dev3/")
        or path.startswith("docs/external-benchmarks/rcaeval-re2-v2-dev3-")
        or (
            path.startswith("scripts/rcaeval_v2/")
            and (
                "dev3" in Path(path).name
                or Path(path).name == "audit_dev2_provider_failures.py"
            )
        )
        or (
            path.startswith("src/ecomsre_rcaeval_v2/")
            and (
                Path(path).name.startswith("dev3_")
                or Path(path).name == "public_projection.py"
            )
        )
        or (
            path.startswith("tests/benchmarks/rcaeval_v2/")
            and Path(path).name.startswith("test_dev3_")
        )
    )


def _validate_implementation_diff(project_root: Path, implementation_commit: str) -> None:
    changed = tuple(
        item
        for item in _git(
            project_root,
            "diff",
            "--name-only",
            f"{SOURCE_BASE_COMMIT}..{implementation_commit}",
        ).splitlines()
        if item
    )
    rejected = tuple(path for path in changed if not _is_dev3_implementation_path(path))
    if rejected:
        raise ValueError(
            "dev3 implementation commit changed an unauthorized path: "
            + ", ".join(rejected)
        )
    if any("dev4" in path.casefold() for path in changed):
        raise ValueError("dev3 implementation commit contains forbidden dev4 work")


def _gh_json(project_root: Path, *args: str) -> object:
    completed = subprocess.run(
        ("gh", *args),
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if not completed.stdout.strip():
        raise ValueError("dev3 remote authorization query returned no JSON")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("dev3 remote authorization query returned invalid JSON") from error


def _remote_authorization(
    project_root: Path, implementation_commit: str
) -> dict[str, object]:
    branch = _git(project_root, "branch", "--show-current")
    if not branch:
        raise ValueError("dev3 evaluation root requires a pushed branch")
    pr = _gh_json(
        project_root,
        "pr",
        "view",
        branch,
        "--json",
        "number,state,isDraft,headRefOid,url",
    )
    if not isinstance(pr, dict):
        raise ValueError("dev3 Draft PR authorization is invalid")
    number = pr.get("number")
    url = pr.get("url")
    if (
        type(number) is not int
        or not isinstance(url, str)
        or pr.get("state") != "OPEN"
        or pr.get("isDraft") is not True
        or pr.get("headRefOid") != implementation_commit
    ):
        raise ValueError("dev3 evaluation root requires an open exact-head Draft PR")
    raw_checks = _gh_json(
        project_root,
        "pr",
        "checks",
        branch,
        "--json",
        "name,state,link,bucket,workflow",
    )
    if not isinstance(raw_checks, list):
        raise ValueError("dev3 CI authorization is invalid")
    selected: list[CiCheckAuthorization] = []
    observed_workflows: set[str] = set()
    for raw in raw_checks:
        if not isinstance(raw, dict) or raw.get("workflow") not in REQUIRED_CI_WORKFLOWS:
            continue
        check = CiCheckAuthorization.model_validate(raw)
        selected.append(check)
        observed_workflows.add(check.workflow)
    if observed_workflows != REQUIRED_CI_WORKFLOWS:
        raise ValueError("dev3 required CI workflows have not all passed")
    return {
        "draft_pr_number": number,
        "draft_pr_url": url,
        "required_ci_checks": tuple(
            sorted(selected, key=lambda item: (item.workflow, item.name, item.link))
        ),
    }


def _require_clean_commit(project_root: Path) -> str:
    if _git(project_root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("dev3 evaluation root requires a clean worktree")
    commit = _git(project_root, "rev-parse", "HEAD")
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ValueError("dev3 evaluation root requires a full implementation commit")
    tracked = set(_git(project_root, "ls-files").splitlines())
    required_files = {str(CONFIG_DIRECTORY / name) for name in CONFIG_NAMES} | {
        str(path) for path in SOURCE_SCOPES.values() if path.suffix
    }
    if not required_files.issubset(tracked):
        raise ValueError("implementation commit does not contain all dev3 files")
    for scope in SOURCE_SCOPES.values():
        if not scope.suffix and not any(
            item == str(scope) or item.startswith(f"{scope}/") for item in tracked
        ):
            raise ValueError("implementation commit has an empty dev3 source scope")
    _validate_implementation_diff(project_root, commit)
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
        raise ValueError("dev3 external roots must live outside Git")
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
        raise ValueError("dev3 evaluation-root source scope is empty")
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
        "retry_policy_lock_sha256": config_hashes["transport-retry-policy.json"],
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
        "v2_dev2_control",
        "v2_dev2_schedule",
        "v2_dev2_output",
        "v2_dev2_smoke",
        "v2_dev2_design",
    }:
        raise ValueError("dev3 evaluation root preserved roots are incomplete")
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
        raise FileExistsError("dev3 evaluation root lock already exists")
    if any(
        (control / "locks" / name).exists()
        for name in (DEV2_FAILURE_AUDIT_LOCK_NAME, ADMISSION_LOCK_NAME)
    ):
        raise ValueError("dev3 evaluation lock must precede audit and admission locks")
    if source_base_commit != SOURCE_BASE_COMMIT:
        raise ValueError("dev3 source base commit differs from immutable PR #16 head")
    implementation_commit = _require_clean_commit(repo)
    protocol = json.loads(
        (repo / CONFIG_DIRECTORY / "protocol.json").read_text(encoding="utf-8")
    )
    if protocol.get("source_base_commit") != SOURCE_BASE_COMMIT:
        raise ValueError("dev3 protocol source base binding drift")
    ancestry = subprocess.run(
        ("git", "merge-base", "--is-ancestor", SOURCE_BASE_COMMIT, implementation_commit),
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if ancestry.returncode != 0:
        raise ValueError("dev3 implementation commit does not descend from PR #16 head")
    remote_authorization = _remote_authorization(repo, implementation_commit)
    for root in (output, smoke, design):
        if root.exists() and any(root.iterdir()):
            raise ValueError("dev3 external output/journal roots must start empty")
    if (
        schedules.is_symlink()
        or not schedules.is_dir()
        or {path.name for path in schedules.iterdir()} != set(SCHEDULE_NAMES)
    ):
        raise ValueError("dev3 private schedule root is missing or not freshly frozen")
    if _run_attempt_count(smoke) != 0 or _run_attempt_count(design) != 0:
        raise ValueError("dev3 journal root already contains attempts")
    bindings = _current_bindings(repo, schedules, output, smoke, design)
    lock = EvaluationRootLock.model_validate(
        {
            "schema_version": "rcaeval-re2-v2-dev3.evaluation-root-lock.v1",
            "protocol_id": PROTOCOL_ID,
            "implementation_commit": implementation_commit,
            "source_base_commit": source_base_commit,
            **remote_authorization,
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
                    "schema_version": "rcaeval-re2-v2-dev3.output-root-authority.v1",
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
        raise ValueError("dev3 evaluation root lock is missing or invalid")
    try:
        lock = EvaluationRootLock.model_validate_json(lock_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError("dev3 evaluation root lock is invalid") from error
    if _require_clean_commit(repo) != lock.implementation_commit:
        raise ValueError("dev3 evaluation root implementation commit drift")
    if lock.source_base_commit != SOURCE_BASE_COMMIT:
        raise ValueError("dev3 evaluation root source base drift")
    for name, expected in _current_bindings(
        repo, schedules, output, smoke, design
    ).items():
        if getattr(lock, name) != expected:
            raise ValueError(f"dev3 evaluation root {name} drift")
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
            {".evaluation-root-authority.json", "v1-terminal-records", "v1-terminal-records.attempts", "v2-runs", "provider-sidecars"},
        ),
        (
            "DESIGN_JOURNAL",
            design,
            lock.design_journal_root_identity_sha256,
            {".evaluation-root-authority.json", "v1-terminal-records", "v1-terminal-records.attempts", "v2-runs", "provider-sidecars"},
        ),
    ):
        marker_path = root / ".evaluation-root-authority.json"
        if marker_path.is_symlink() or not marker_path.is_file():
            raise ValueError("dev3 external root is not authorized")
        expected_marker = {
            "schema_version": "rcaeval-re2-v2-dev3.output-root-authority.v1",
            "protocol_id": PROTOCOL_ID,
            "role": role,
            "evaluation_root_lock_sha256": _sha_file(lock_path),
            "root_identity_sha256": identity,
        }
        if json.loads(marker_path.read_text(encoding="utf-8")) != expected_marker:
            raise ValueError("dev3 external root authority drift")
        if {path.name for path in root.iterdir()} - allowed:
            raise ValueError("dev3 external root contains unauthorized entries")
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
    audit_path = control_root / "locks" / DEV2_FAILURE_AUDIT_LOCK_NAME
    if audit_path.is_symlink() or not audit_path.is_file():
        raise ValueError("dev3 Provider-ready failure audit lock is missing")
    audit = Dev2FailureAuditLock.model_validate_json(
        audit_path.read_text(encoding="utf-8")
    )
    if (
        audit.evaluation_root_lock_sha256 != _sha_file(
            control_root / "locks" / EVALUATION_LOCK_NAME
        )
        or
        audit.audit.failure_count != 5
        or audit.audit.retry_eligible_count != 0
        or audit.audit.failure_class_counts
        != {FailureClass.UNKNOWN_INSUFFICIENT_EVIDENCE: 5}
    ):
        raise ValueError("dev3 Provider-ready failure audit disposition drift")
    admission = load_admission_lock(control_root / "locks" / ADMISSION_LOCK_NAME)
    expected = {
        "implementation_commit": evaluation.implementation_commit,
        "split_lock_sha256": evaluation.split_lock_sha256,
        "dev2_failure_audit_lock_sha256": _sha_file(audit_path),
        "retry_policy_lock_sha256": evaluation.retry_policy_lock_sha256,
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
            raise ValueError(f"dev3 Provider-ready admission {field} drift")
    v1_schedule_binding = json.loads(
        (project_root / "config/rcaeval-re2-v1/schedule-generation.json").read_text(
            encoding="utf-8"
        )
    ).get("expected_schedule_sha256")
    if admission.v1_external_schedule_sha256 != v1_schedule_binding:
        raise ValueError("dev3 Provider-ready v1 external schedule binding drift")
    if admission.provider_objects_constructed != 0 or admission.provider_calls != 0 or admission.run_attempts_created != 0 or admission.operation_attempts_created != 0 or admission.provider_attempts_created != 0 or admission.dev_validation_metadata.values_accessed is not False:
        raise ValueError("dev3 Provider-ready zero-call admission invariant failed")
    expected_preserved_roots = {
        "v2_dev_v1",
        "v2_dev1_control",
        "v2_dev1_output",
        "v2_dev2_control",
        "v2_dev2_schedule",
        "v2_dev2_output",
        "v2_dev2_smoke",
        "v2_dev2_design",
    }
    if set(preserved_roots) != expected_preserved_roots or set(
        admission.preserved_root_identity_sha256
    ) != expected_preserved_roots:
        raise ValueError("dev3 Provider-ready preserved root bindings are incomplete")
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
            raise ValueError("dev3 Provider-ready preserved root identity drift")
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
        "v2_dev2_design": _sha_file(
            resolved_preserved_roots["v2_dev2_schedule"]
            / "design-schedule.json"
        ),
        "v2_dev2_validation": _sha_file(
            resolved_preserved_roots["v2_dev2_schedule"]
            / "dev-validation-schedule.json"
        ),
    }
    if admission.preserved_schedule_hashes != observed_schedule_hashes:
        raise ValueError("dev3 Provider-ready preserved schedule drift")
    observed_evidence_hashes = {
        f"{name}_tree": tree_sha256(root)
        for name, root in resolved_preserved_roots.items()
    }
    if admission.preserved_evidence_hashes != observed_evidence_hashes:
        raise ValueError("dev3 Provider-ready preserved terminal evidence drift")
    if (
        audit.dev2_smoke_gate_sha256
        != _sha_file(
            resolved_preserved_roots["v2_dev2_control"]
            / "evidence/provider-smoke-gate.json"
        )
        or audit.dev2_smoke_schedule_sha256
        != _sha_file(
            resolved_preserved_roots["v2_dev2_schedule"] / "smoke-schedule.json"
        )
        or audit.dev2_smoke_journal_tree_sha256
        != audit_tree_sha256(resolved_preserved_roots["v2_dev2_smoke"])
    ):
        raise ValueError("dev3 Provider-ready failure audit source drift")
    return evaluation, admission
