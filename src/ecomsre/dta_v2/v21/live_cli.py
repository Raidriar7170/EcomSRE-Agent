"""Explicit, guarded CLI for DTA v2.1 PR-F preflight, execution, and reports."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Sequence, TypeVar

from pydantic import BaseModel

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v21.agent_contracts import AgentArmV21
from ecomsre.dta_v2.v21.agent_provider import OpenAICompatibleDtaAgentProviderV21
from ecomsre.dta_v2.v21.contracts import semantic_sha256
from ecomsre.dta_v2.v21.live_contracts import (
    LiveReadinessV2,
    load_live_demo_config_v21,
)
from ecomsre.dta_v2.v21.live_execution import LiveMasterAuthorizationV21
from ecomsre.dta_v2.v21.live_protocol import (
    load_ad_cpu_resource_recovery_protocol_v1,
    verify_accepted_ad_cpu_calibration_binding,
)
from ecomsre.dta_v2.v21.live_reconciliation import (
    BLOCKED_ATTEMPT_ID_V1,
    BLOCKED_CODE_HEAD_V1,
    CurrentResourceQuiescenceV1,
    IndependentRetryReviewV1,
    ResolvedComposeIdentityV1,
    build_resolved_compose_identity_v1,
    verify_post_terminal_reconciliation_v1,
    verify_retry_admission_v1,
    write_independent_retry_review_v1,
    write_post_terminal_reconciliation_v1,
    write_retry_admission_v1,
)
from ecomsre.dta_v2.v21.live_reporting import (
    PublicLiveReportV21,
    build_public_live_report_v21,
    render_public_final_summary_v21,
    render_public_human_brief_v21,
    render_public_interview_brief_v21,
    render_public_live_markdown_v21,
    verify_public_live_report_v21,
)
from ecomsre.dta_v2.v21.live_runner import run_owned_live_campaign_v21
from ecomsre.dta_v2.v21.owned_capture import OwnedCaptureLifecycleV21
from ecomsre.dta_v2.v21.registry import load_default_runbook_registry
from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_live_sandbox.contracts import (
    ensure_private_directory,
    load_bundle,
    verify_private_tree_permissions,
    write_private_json,
)
from ecomsre_live_sandbox.environment import ExactCommandRunner, SandboxEnvironment


_EXECUTION_CONFIRMATION = "USER_EXPLICIT_DTA_V21_PRF_RESOURCE_RECOVERY_AMENDMENT"
_RETRY_EXECUTION_CONFIRMATION = (
    "USER_EXPLICIT_DTA_V21_PRF_APPEND_ONLY_RECONCILIATION_AND_ONE_RETRY"
)
_FINAL_REVIEW_CONFIRMATION = "MUST_FIX_0_CLAIM_ACCURACY_PASS"
_COMMAND_RUNNER = ExactCommandRunner()
_ModelT = TypeVar("_ModelT", bound=BaseModel)
_README_LIVE_MARKER = "<!-- dta-v21-pr-f-live-closeout -->"
_PUBLIC_CLOSEOUT_PATHS = frozenset(
    {
        "README.md",
        "docs/analysis/dta-v21-p0-master-progress.json",
        "docs/results/dta-v21-live-demo.json",
        "docs/results/dta-v21-live-demo.md",
        "docs/results/dta-v21-live-demo-human-brief.md",
        "docs/results/dta-v21-final-summary.md",
        "docs/results/dta-v21-interview-brief.md",
        "docs/review-evidence/dta-v21-live/current-disposition.json",
    }
)
_PUBLIC_REPORT_PATHS = frozenset(
    path
    for path in _PUBLIC_CLOSEOUT_PATHS
    if path not in {"README.md", "docs/analysis/dta-v21-p0-master-progress.json"}
)
_FROZEN_PRIVATE_PR_E_RAW_SHA256 = {
    "pr-e/execution/execution-manifest.json": (
        "d5504f2d07de791d3fbadde071cb4befb7cf3cbe405f09799b9bac68d205d87e"
    ),
    "pr-e/execution/execution-seal.json": (
        "4f34b83b4b5a2bc2957562013dbde552631f3c82012be8c92a8a444c5c61b865"
    ),
    "pr-e/one-time-claims/"
    "9a7c8e56400e99c693c8bddc26007b1dd26e0dcee2167b07cf3fba00fd22fbd7.json": (
        "bc58d133a6ca7807c265567ab0023820fb9d6211544514474ca7a14cd971b9e4"
    ),
    "pr-e/unblinding/held-out-evaluation-report.json": (
        "13df28e58f85474a8fe58e76773eff6d7d8c7015ec8c721666c30fde6645c132"
    ),
    "pr-e/unblinding/unblinding-receipt.json": (
        "9818b38b3f6934944dd3484e0092657aed7fc5227b73f2ffaab0909817b6fcf9"
    ),
}


def _git(root: Path, *arguments: str) -> str:
    try:
        result = _COMMAND_RUNNER.run(("git", *arguments), cwd=root, timeout_seconds=30)
    except RuntimeError as error:
        raise ValueError("required Git verification failed") from error
    return result.stdout.strip()


def _verify_exact_head_github_actions(
    root: Path, *, head: str, required_event: str = "pull_request"
) -> dict[str, object]:
    try:
        result = _COMMAND_RUNNER.run(
            (
                "gh",
                "run",
                "list",
                "--commit",
                head,
                "--workflow",
                "agent-mainline.yml",
                "--json",
                "databaseId,headSha,status,conclusion,url,event",
                "--limit",
                "20",
            ),
            cwd=root,
            timeout_seconds=30,
        )
    except RuntimeError as error:
        raise ValueError("exact-head GitHub Actions verification failed") from error
    value = json.loads(result.stdout)
    if not isinstance(value, list):
        raise ValueError("exact-head GitHub Actions response is invalid")
    successful = tuple(
        item
        for item in value
        if isinstance(item, dict)
        and item.get("headSha") == head
        and item.get("status") == "completed"
        and item.get("conclusion") == "success"
        and item.get("event") == required_event
        and isinstance(item.get("databaseId"), int)
        and isinstance(item.get("url"), str)
        and str(item["url"]).startswith("https://github.com/")
    )
    if not successful:
        raise ValueError("exact-head GitHub Actions has no successful PR run")
    selected = max(successful, key=lambda item: int(item["databaseId"]))
    return {
        "run_id": selected["databaseId"],
        "head_sha": selected["headSha"],
        "conclusion": "SUCCESS",
        "url": selected["url"],
    }


def _verify_merged_pr(root: Path, *, active_pr: int) -> dict[str, str]:
    try:
        result = _COMMAND_RUNNER.run(
            (
                "gh",
                "pr",
                "view",
                str(active_pr),
                "--json",
                "number,state,isDraft,baseRefName,headRefOid,mergeCommit,url",
            ),
            cwd=root,
            timeout_seconds=30,
        )
        value = json.loads(result.stdout)
    except (RuntimeError, json.JSONDecodeError) as error:
        raise ValueError("merged PR evidence is unavailable") from error
    merge_commit = value.get("mergeCommit") if isinstance(value, dict) else None
    merge_oid = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("number") != active_pr
        or value.get("state") != "MERGED"
        or value.get("isDraft") is not False
        or value.get("baseRefName") != "main"
        or not isinstance(value.get("headRefOid"), str)
        or not isinstance(merge_oid, str)
        or not isinstance(value.get("url"), str)
        or not str(value["url"]).startswith("https://github.com/")
    ):
        raise ValueError("merged PR evidence differs")
    return {
        "head_sha": str(value["headRefOid"]),
        "merge_sha": merge_oid,
        "url": str(value["url"]),
    }


def _read_json(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("required JSON input is missing or unsafe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("required JSON input is not an object")
    return value


def _read_model(path: Path, model_type: type[_ModelT]) -> _ModelT:
    if path.is_symlink() or not path.is_file():
        raise ValueError("required typed JSON input is missing or unsafe")
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def _write_public_once(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise FileExistsError(f"public closure output is unsafe: {path.name}")
    if path.is_file() and path.read_text(encoding="utf-8") == value:
        return
    if path.exists():
        raise FileExistsError(f"public closure output already exists: {path.name}")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        raise


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("frozen private evidence is missing or unsafe")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _execution_scope_sha256(root: Path, *, treeish: str) -> str:
    try:
        result = _COMMAND_RUNNER.run(
            ("git", "ls-tree", "-r", "--full-tree", treeish),
            cwd=root,
            timeout_seconds=30,
        )
    except RuntimeError as error:
        raise ValueError("execution-scope Git tree is unavailable") from error
    entries: list[tuple[str, str, str, str]] = []
    for line in result.stdout.splitlines():
        try:
            metadata, path = line.split("\t", 1)
            mode, object_type, object_sha = metadata.split(" ", 2)
        except ValueError as error:
            raise ValueError("execution-scope Git tree is invalid") from error
        if path in _PUBLIC_CLOSEOUT_PATHS:
            continue
        entries.append((mode, object_type, path, object_sha))
    if not entries:
        raise ValueError("execution-scope Git tree is empty")
    return semantic_sha256(tuple(entries))


def _verify_public_only_delta(root: Path, *, execution_scope_sha256: str) -> None:
    if _execution_scope_sha256(root, treeish="HEAD") != execution_scope_sha256:
        raise ValueError("live execution source scope changed after the campaign")
    status_paths: set[str] = set()
    try:
        status = _COMMAND_RUNNER.run(
            ("git", "status", "--porcelain=v1", "--untracked-files=all"),
            cwd=root,
            timeout_seconds=30,
        ).stdout
    except RuntimeError as error:
        raise ValueError("public closeout Git status is unavailable") from error
    for line in status.splitlines():
        if " -> " in line:
            raise ValueError("public closeout scope contains a rename")
        if len(line) < 4:
            raise ValueError("public closeout Git status is invalid")
        status_paths.add(line[3:])
    if not status_paths.issubset(_PUBLIC_CLOSEOUT_PATHS):
        raise ValueError("live execution code changed after the bound campaign")


def _verify_frozen_private_pr_e(private_root: Path) -> None:
    for relative, expected in _FROZEN_PRIVATE_PR_E_RAW_SHA256.items():
        if _sha256_file(private_root / relative) != expected:
            raise ValueError("frozen private PR-E evidence drifted")


def _verify_private_protocol_freeze(
    *, private_root: Path, protocol_sha256: str
) -> None:
    freeze = _read_json(private_root / "pr-f/protocol-freeze.json")
    freeze_sha = freeze.pop("record_sha256", None)
    if (
        freeze_sha != semantic_sha256(freeze)
        or freeze.get("protocol_commit") != "d20eef2dd644269b975fff22d9a8c03d437878ba"
        or freeze.get("protocol_sha256") != protocol_sha256
        or freeze.get("pr_e_claim")
        != "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED"
    ):
        raise ValueError("private F0 protocol freeze binding differs")


def _claim_readiness_attempt(head_root: Path) -> tuple[str, Path]:
    if (head_root / "readiness.json").exists() or (
        head_root / "readiness.json"
    ).is_symlink():
        raise ValueError("exact-head readiness evidence already exists")
    attempts_root = head_root / "attempts"
    ensure_private_directory(attempts_root)
    for ordinal in range(1, 10_000):
        attempt_id = f"readiness-{ordinal:04d}"
        attempt_root = attempts_root / attempt_id
        try:
            attempt_root.mkdir(mode=0o700, exist_ok=False)
        except FileExistsError:
            continue
        return attempt_id, attempt_root
    raise ValueError("exact-head readiness attempt space is exhausted")


def _verify_execution_lease_is_free(prf_root: Path) -> None:
    path = prf_root / "execution.lock"
    if path.is_symlink() or not path.is_file():
        raise ValueError("PR-F execution lease file is missing or unsafe")
    descriptor = os.open(path, os.O_RDWR)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("PR-F execution lease is held") from error
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def run_reconcile(*, repository_root: Path, private_root: Path) -> dict[str, object]:
    """Create the one append-only reconciliation after fresh read-only checks."""

    root = repository_root.resolve(strict=True)
    private = private_root.resolve(strict=True)
    prf = private / "pr-f"
    if private.is_relative_to(root):
        raise ValueError("PR-F private evidence must remain outside the repository")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("PR-F reconciliation requires an exactly clean worktree")
    verify_private_tree_permissions(prf)
    protocol = load_ad_cpu_resource_recovery_protocol_v1(
        root / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
    )
    verify_accepted_ad_cpu_calibration_binding(
        protocol=protocol, repository_root=root, private_root=private
    )
    _verify_private_protocol_freeze(
        private_root=private, protocol_sha256=protocol.protocol_sha256
    )
    _verify_frozen_private_pr_e(private)
    _verify_execution_lease_is_free(prf)

    probe_root = prf / "reconciliations" / BLOCKED_ATTEMPT_ID_V1 / "quiescence-probe"
    flagd_directory = probe_root / "flagd"
    ensure_private_directory(flagd_directory)
    environment = SandboxEnvironment(
        repository_root=root,
        bundle=load_bundle(
            root / "config/live-telemetry-controlled-remediation-v1"
        ),
        flagd_directory=flagd_directory,
    )
    environment.verify_local_docker()
    environment.verify_upstream()
    environment.resolve()
    counts = environment.verify_owned_resources(require_complete=False)
    if counts != {"container": 0, "network": 0, "volume": 0}:
        raise ValueError("BLOCKED_DTA_V21_PRF_RECONCILIATION_QUIESCENCE")
    environment.verify_ports_available()
    quiescence = CurrentResourceQuiescenceV1.build(
        observed_at=datetime.now(timezone.utc),
        docker_boundary="LOCAL_UNIX_DOCKER",
        owned_container_count=0,
        owned_network_count=0,
        owned_volume_count=0,
        required_ports_available=True,
        execution_lease_held=False,
        private_permissions_verified=True,
        source_worktree_clean=True,
        pr_d_frozen_bindings_verified=True,
        pr_e_frozen_bindings_verified=True,
    )
    record = write_post_terminal_reconciliation_v1(
        repository_root=root, private_root=private, quiescence=quiescence
    )
    verify_private_tree_permissions(prf)
    return record.model_dump(mode="json")


def run_record_retry_review(
    *, repository_root: Path, private_root: Path, reviewer: str
) -> dict[str, object]:
    root = repository_root.resolve(strict=True)
    private = private_root.resolve(strict=True)
    head = _git(root, "rev-parse", "HEAD")
    if head == BLOCKED_CODE_HEAD_V1:
        raise ValueError("independent retry review requires a corrected code HEAD")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("independent retry review recording requires a clean worktree")
    review = IndependentRetryReviewV1.build(
        code_head=head,
        reviewer=reviewer,
        reviewed_at=datetime.now(timezone.utc),
        must_fix_count=0,
        should_fix_count=0,
        claim_accuracy="PASS",
    )
    write_independent_retry_review_v1(private_root=private, review=review)
    verify_private_tree_permissions(private / "pr-f")
    return review.model_dump(mode="json")


def run_retry_admit(
    *, repository_root: Path, private_root: Path
) -> dict[str, object]:
    root = repository_root.resolve(strict=True)
    private = private_root.resolve(strict=True)
    head = _git(root, "rev-parse", "HEAD")
    if head == BLOCKED_CODE_HEAD_V1:
        raise ValueError("retry admission requires a new code HEAD")
    _git(root, "merge-base", "--is-ancestor", BLOCKED_CODE_HEAD_V1, head)
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("retry admission requires an exactly clean worktree")
    _load_exact_readiness(repository_root=root, private_root=private)
    verify_post_terminal_reconciliation_v1(
        repository_root=root, private_root=private
    )
    admission = write_retry_admission_v1(
        repository_root=root,
        private_root=private,
        new_code_head=head,
    )
    verify_private_tree_permissions(private / "pr-f")
    return admission.model_dump(mode="json")


def run_verify_reconciliation(
    *, repository_root: Path, private_root: Path
) -> dict[str, object]:
    record, _quiescence = verify_post_terminal_reconciliation_v1(
        repository_root=repository_root.resolve(strict=True),
        private_root=private_root.resolve(strict=True),
    )
    return record.model_dump(mode="json")


def run_preflight(
    *,
    repository_root: Path,
    private_root: Path,
    provider_env_path: Path,
    exact_head_ci_sha: str,
) -> dict[str, object]:
    root = repository_root.resolve(strict=True)
    private = private_root.resolve(strict=True)
    if private.is_relative_to(root):
        raise ValueError("PR-F private evidence must remain outside the repository")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("PR-F preflight requires an exactly clean worktree")
    head = _git(root, "rev-parse", "HEAD")
    if head != exact_head_ci_sha or len(head) != 40:
        raise ValueError("exact-head CI SHA differs from current HEAD")
    ci_receipt = _verify_exact_head_github_actions(root, head=head)
    if _git(root, "branch", "--show-current") != (
        "codex/dta-v21-p0-pr-f-live-closeout"
    ):
        raise ValueError("PR-F preflight branch differs")
    _git(root, "merge-base", "--is-ancestor", "origin/main", "HEAD")
    _git(root, "merge-base", "--is-ancestor", BLOCKED_CODE_HEAD_V1, "HEAD")
    protocol = load_ad_cpu_resource_recovery_protocol_v1(
        root / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
    )
    config = load_live_demo_config_v21(root / "config/dta-v21/live/live-demo.v1.json")
    if config.protocol_sha256 != protocol.protocol_sha256:
        raise ValueError("live config differs from the frozen Ad protocol")
    verify_accepted_ad_cpu_calibration_binding(
        protocol=protocol, repository_root=root, private_root=private
    )
    _verify_frozen_private_pr_e(private)
    _verify_private_protocol_freeze(
        private_root=private, protocol_sha256=protocol.protocol_sha256
    )
    provider_values = load_private_provider_env(provider_env_path)
    if provider_values["ECOMSRE_LLM_MODEL"] != config.provider_model:
        raise ValueError("Provider model differs from the frozen live config")
    provider_config = OpenAICompatibleConfig.from_environment(provider_values)
    if provider_config is None:
        raise ValueError("Provider configuration is unavailable")
    provider = OpenAICompatibleDtaAgentProviderV21(
        arm=AgentArmV21.EVIDENCE_GUIDED_PLANNER,
        config=provider_config,
        timeout_seconds=90.0,
        max_completion_tokens=config.maximum_completion_tokens,
    )
    if provider.identity.identity_sha256 != config.planner_identity_sha256:
        raise ValueError("Provider identity differs from the frozen live config")
    progress = _read_json(root / "docs/analysis/dta-v21-p0-master-progress.json")
    if (
        progress.get("held_out_claim")
        != "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED"
        or progress.get("held_out_execution_id") != "53615cdd78b348b68496f64102c0b4de"
        or progress.get("held_out_seal_sha256")
        != "9a7c8e56400e99c693c8bddc26007b1dd26e0dcee2167b07cf3fba00fd22fbd7"
    ):
        raise ValueError("PR-E frozen progress fields differ")
    readiness_root = private / "pr-f/readiness" / head
    ensure_private_directory(readiness_root)
    readiness_attempt_id, readiness_attempt_root = _claim_readiness_attempt(
        readiness_root
    )
    try:
        capture = OwnedCaptureLifecycleV21(
            repository_root=root,
            private_root=readiness_attempt_root / "owned-preflight",
            plan=config,  # type: ignore[arg-type]
            stabilization_seconds=30,
        )
        capture.admit()
        environment = capture._environment()
        if any(environment.verify_owned_resources(require_complete=False).values()):
            raise ValueError("owned PR-F resources already exist before live execution")
        environment.verify_ports_available()
        admitted_compose_path = (
            readiness_attempt_root / "owned-preflight/control/resolved-compose.json"
        )
        admitted_raw = _read_json(admitted_compose_path)
        admitted_identity = build_resolved_compose_identity_v1(
            admitted_raw,
            expected_flagd_directory=environment.flagd_directory,
            accepted_private_prf_root=private / "pr-f",
            repository_root=root,
            raw_contract_verifier=environment._verify_resolved_contract,
        )
        _resolved, fresh_raw = environment.resolve()
        fresh_identity = build_resolved_compose_identity_v1(
            fresh_raw,
            expected_flagd_directory=environment.flagd_directory,
            accepted_private_prf_root=private / "pr-f",
            repository_root=root,
            raw_contract_verifier=environment._verify_resolved_contract,
        )
        if admitted_identity != fresh_identity:
            raise ValueError("same-context preflight Compose identities differ")
        baseline_sha = semantic_sha256(capture._baseline())
        master_path = private / "pr-f/master-authorization.json"
        if master_path.exists() or master_path.is_symlink():
            master = _read_model(master_path, LiveMasterAuthorizationV21)
        else:
            master = LiveMasterAuthorizationV21.build(
                issued_at=datetime.now(timezone.utc)
            )
            write_private_json(master_path, master, create_once=True)
        record = LiveReadinessV2.build(
            terminal="DTA_V21_PR_F_PRELIVE_READY",
            readiness_attempt_id=readiness_attempt_id,
            code_head=head,
            exact_head_ci_success=True,
            exact_head_ci_run_id=ci_receipt["run_id"],
            exact_head_ci_run_url=ci_receipt["url"],
            branch="codex/dta-v21-p0-pr-f-live-closeout",
            origin_main_is_ancestor=True,
            protocol_sha256=protocol.protocol_sha256,
            live_config_sha256=config.config_sha256,
            planner_identity_sha256=config.planner_identity_sha256,
            provider_model=config.provider_model,
            pr_e_claim="DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED",
            docker_boundary="LOCAL_UNIX_DOCKER",
            raw_compose_sha256=admitted_identity.raw_compose_sha256,
            execution_compose_sha256=admitted_identity.execution_compose_sha256,
            compose_identity_sha256=admitted_identity.identity_sha256,
            normalization_policy_id=admitted_identity.normalization_policy_id,
            baseline_flag_document_sha256=baseline_sha,
            owned_resource_collisions=0,
            required_ports_available=True,
            cleanup_readiness="OWNED_SCOPE_ADMITTED",
            private_permissions="0700_DIRECTORIES_0600_FILES",
            master_authorization_sha256=master.authorization_sha256,
        )
        write_private_json(
            readiness_attempt_root / "readiness.json", record, create_once=True
        )
        write_private_json(
            readiness_attempt_root / "compose-identity.json",
            admitted_identity,
            create_once=True,
        )
        write_private_json(readiness_root / "readiness.json", record, create_once=True)
        verify_private_tree_permissions(private / "pr-f")
        return record.model_dump(mode="json")
    except Exception as error:
        failure = {
            "schema_version": "dta-v21.pr-f-readiness-failure.v1",
            "terminal": "BLOCKED_DTA_V21_PRF_SAFETY",
            "readiness_attempt_id": readiness_attempt_id,
            "code_head": head,
            "failure_type": type(error).__name__,
            "raw_error_retained": False,
        }
        write_private_json(
            readiness_attempt_root / "readiness-terminal.json",
            failure,
            create_once=True,
        )
        verify_private_tree_permissions(private / "pr-f")
        raise


def _load_exact_readiness(
    *, repository_root: Path, private_root: Path
) -> tuple[
    LiveMasterAuthorizationV21,
    LiveReadinessV2,
    ResolvedComposeIdentityV1,
    dict[str, object],
    Path,
]:
    head = _git(repository_root, "rev-parse", "HEAD")
    config = load_live_demo_config_v21(
        repository_root / "config/dta-v21/live/live-demo.v1.json"
    )
    protocol = load_ad_cpu_resource_recovery_protocol_v1(
        repository_root / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
    )
    readiness_root = private_root / "pr-f/readiness" / head
    readiness = _read_model(readiness_root / "readiness.json", LiveReadinessV2)
    attempt_root = readiness_root / "attempts" / readiness.readiness_attempt_id
    attempt_copy = _read_model(
        attempt_root / "readiness.json",
        LiveReadinessV2,
    )
    identity = _read_model(
        attempt_root / "compose-identity.json", ResolvedComposeIdentityV1
    )
    raw_compose = _read_json(
        attempt_root / "owned-preflight/control/resolved-compose.json"
    )
    flagd_directory = attempt_root / "owned-preflight/runtime/flagd"
    master = _read_model(
        private_root / "pr-f/master-authorization.json",
        LiveMasterAuthorizationV21,
    )
    ci_receipt = _verify_exact_head_github_actions(repository_root, head=head)
    if (
        readiness != attempt_copy
        or readiness.code_head != head
        or readiness.exact_head_ci_run_id != ci_receipt["run_id"]
        or readiness.exact_head_ci_run_url != ci_receipt["url"]
        or readiness.protocol_sha256 != protocol.protocol_sha256
        or readiness.live_config_sha256 != config.config_sha256
        or readiness.planner_identity_sha256 != config.planner_identity_sha256
        or readiness.provider_model != config.provider_model
        or readiness.master_authorization_sha256 != master.authorization_sha256
        or readiness.raw_compose_sha256 != identity.raw_compose_sha256
        or readiness.execution_compose_sha256 != identity.execution_compose_sha256
        or readiness.compose_identity_sha256 != identity.identity_sha256
        or semantic_sha256(raw_compose) != readiness.raw_compose_sha256
    ):
        raise ValueError("PR-F readiness record differs from exact HEAD")
    return master, readiness, identity, raw_compose, flagd_directory


def run_execute(
    *, repository_root: Path, private_root: Path, provider_env_path: Path
) -> None:
    root = repository_root.resolve(strict=True)
    private = private_root.resolve(strict=True)
    if os.environ.get("DTA_V21_LIVE_EXECUTE") != _EXECUTION_CONFIRMATION:
        raise ValueError("DTA v2.1 live execution confirmation is missing")
    if os.environ.get("DTA_V21_RETRY_EXECUTE") != _RETRY_EXECUTION_CONFIRMATION:
        raise ValueError("DTA v2.1 one-retry execution confirmation is missing")
    if private.is_relative_to(root):
        raise ValueError("PR-F private evidence must remain outside the repository")
    verify_private_tree_permissions(private / "pr-f")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("PR-F live execution requires an exactly clean worktree")
    if _git(root, "branch", "--show-current") != (
        "codex/dta-v21-p0-pr-f-live-closeout"
    ):
        raise ValueError("PR-F live execution branch differs")
    _git(root, "merge-base", "--is-ancestor", "origin/main", "HEAD")
    _git(root, "merge-base", "--is-ancestor", BLOCKED_CODE_HEAD_V1, "HEAD")
    master, readiness, readiness_identity, readiness_raw, readiness_flagd = (
        _load_exact_readiness(
        repository_root=root, private_root=private
        )
    )
    config = load_live_demo_config_v21(root / "config/dta-v21/live/live-demo.v1.json")
    protocol = load_ad_cpu_resource_recovery_protocol_v1(
        root / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
    )
    if config.protocol_sha256 != protocol.protocol_sha256:
        raise ValueError("live config differs from the frozen Ad protocol")
    verify_accepted_ad_cpu_calibration_binding(
        protocol=protocol, repository_root=root, private_root=private
    )
    _verify_private_protocol_freeze(
        private_root=private, protocol_sha256=protocol.protocol_sha256
    )
    _verify_frozen_private_pr_e(private)
    head = _git(root, "rev-parse", "HEAD")
    verify_retry_admission_v1(
        repository_root=root,
        private_root=private,
        new_code_head=head,
    )
    run_owned_live_campaign_v21(
        repository_root=root,
        prf_private_root=private / "pr-f",
        provider_env_path=provider_env_path,
        config=config,
        registry=load_default_runbook_registry(root),
        protocol=protocol,
        master_authorization=master,
        readiness=readiness,
        readiness_identity=readiness_identity,
        readiness_raw_compose=readiness_raw,
        readiness_flagd_directory=readiness_flagd,
        code_head=head,
    )


def _git_blob_text(root: Path, *, treeish: str, relative_path: str) -> str:
    try:
        result = _COMMAND_RUNNER.run(
            ("git", "show", f"{treeish}:{relative_path}"),
            cwd=root,
            timeout_seconds=30,
        )
    except RuntimeError as error:
        raise ValueError("bound public source file is unavailable") from error
    return result.stdout


def _render_final_readme_section_v21(report: PublicLiveReportV21) -> str:
    return f"""{_README_LIVE_MARKER}
## DTA v2.1 evaluation and local live portfolio

- Held-out evaluation: the frozen 8-case/24-entry result remains
  `{report.held_out_claim}`; it does not support a planner-advantage claim.
- Local live portfolio: four known local scenarios reached
  `{report.terminal}` as engineering evidence, not held-out or production evidence.
- Ad CPU result: resource recovery passed with a business-SLI non-regression
  guardrail. The calibration did not show user-visible degradation, so this is
  not a business-impact or user-impact recovery claim.
- Final engineering gate: `DTA_V21_P0_ENGINEERING_ACCEPTANCE_PASS`.

See the [final summary](docs/results/dta-v21-final-summary.md),
[live report](docs/results/dta-v21-live-demo.md), and
[Human Brief](docs/results/dta-v21-live-demo-human-brief.md).

"""


def _render_final_readme_v21(*, base_readme: str, report: PublicLiveReportV21) -> str:
    anchor = "## One-command offline demo"
    if _README_LIVE_MARKER in base_readme or base_readme.count(anchor) != 1:
        raise ValueError("README live-closeout insertion boundary differs")
    section = _render_final_readme_section_v21(report)
    return base_readme.replace(anchor, section + anchor, 1)


def _render_final_progress_v21(
    *,
    base_progress: str,
    report: PublicLiveReportV21,
    active_pr: int,
    merged_main_head: str,
) -> str:
    value = json.loads(base_progress)
    if not isinstance(value, dict):
        raise ValueError("Master Progress source is not an object")
    if (
        value.get("schema_version") != "dta-v21-p0-master-progress.v1"
        or value.get("completed_stage") != "PR-E"
        or value.get("current_stage") != "PR-F"
        or value.get("main_head") != "1c763eb815764e971855a5d6730981b9a2e5858a"
        or value.get("active_branch") != "codex/dta-v21-p0-pr-f-live-closeout"
        or value.get("active_pr") != 55
        or value.get("active_amendment_version")
        != "dta-v21-p0-prf-compose-identity-reconciliation-v1"
        or value.get("active_amendment_sha256")
        != "ea6740bce0ba63e093cda2807aea886d4ca48907702a2bf41ad1eedd0e2ab164"
        or value.get("active_decision_id") != "DEC-045"
        or value.get("merged_prs") != [50, 51, 52, 53, 54]
        or value.get("held_out_claim") != report.held_out_claim
        or value.get("ad_cpu_resource_recovery_protocol_sha256")
        != report.protocol_sha256
        or value.get("live_demo_terminal") is not None
        or value.get("final_engineering_terminal") is not None
    ):
        raise ValueError("Master Progress source differs from the PR-F boundary")
    value.update(
        {
            "completed_stage": "PR-F",
            "current_stage": "COMPLETE",
            "main_head": merged_main_head,
            "active_branch": None,
            "active_pr": None,
            "merged_prs": [50, 51, 52, 53, 54, active_pr],
            "live_report_sha256": report.report_sha256,
            "live_execution_code_head": report.live_execution_code_head,
            "live_execution_scope_sha256": report.live_execution_scope_sha256,
            "live_demo_terminal": "DTA_V21_P0_ENGINEERING_ACCEPTANCE_PASS",
            "final_engineering_terminal": ("DTA_V21_P0_ENGINEERING_ACCEPTANCE_PASS"),
        }
    )
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _base_disposition_payload(report: PublicLiveReportV21) -> dict[str, object]:
    return {
        "schema_version": "dta-v21.live-disposition.v2",
        "report_sha256": report.report_sha256,
        "protocol_sha256": report.protocol_sha256,
        "held_out_claim": report.held_out_claim,
        "live_execution_code_head": report.live_execution_code_head,
        "live_execution_scope_sha256": report.live_execution_scope_sha256,
        "readiness_sha256": report.readiness_sha256,
        "private_campaign_sha256": report.private_campaign_sha256,
        "reconciliation_sha256": report.reconciliation_sha256,
        "retry_admission_sha256": report.retry_admission_sha256,
        "retry_consumption_sha256": report.retry_consumption_sha256,
        "successful_campaign_attempt_count": 4,
        "successful_campaign_all_baselines_restored": True,
        "successful_campaign_all_cleanup_clean": True,
        "successful_campaign_non_owned_changes": 0,
        "historical_blocked_attempt_count": 1,
        "historical_all_cleanup_clean": False,
        "unsafe_proposal_attempts": 0,
        "arbitrary_shell_attempts": 0,
    }


def _pending_disposition_payload(report: PublicLiveReportV21) -> dict[str, object]:
    return {
        **_base_disposition_payload(report),
        "terminal": "DTA_V21_PR_F_FINAL_ACCEPTANCE_PENDING",
        "exact_head_ci": "PENDING",
        "independent_final_review": "PENDING",
        "claim_accuracy": "PENDING",
    }


def _replace_public_text_exact(path: Path, *, expected: str, replacement: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("public closeout source is missing or unsafe")
    current = path.read_text(encoding="utf-8")
    if current == replacement:
        return
    if current != expected:
        raise ValueError("public closeout source changed before finalization")
    temporary = path.with_name(f".{path.name}.dta-v21-finalize-{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(replacement)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()
        raise


def _recover_base_closeout_text(
    *, root: Path, report: PublicLiveReportV21, active_pr: int
) -> tuple[str, str]:
    readme = (root / "README.md").read_text(encoding="utf-8")
    if hashlib.sha256(readme.encode("utf-8")).hexdigest() == (
        report.base_readme_sha256
    ):
        base_readme = readme
    else:
        section = _render_final_readme_section_v21(report)
        if readme.count(section) != 1:
            raise ValueError("README cannot be recovered to the bound base")
        base_readme = readme.replace(section, "", 1)
        if hashlib.sha256(base_readme.encode("utf-8")).hexdigest() != (
            report.base_readme_sha256
        ):
            raise ValueError("README cannot be recovered to the bound base")
    progress = (root / "docs/analysis/dta-v21-p0-master-progress.json").read_text(
        encoding="utf-8"
    )
    progress_value = json.loads(progress)
    if not isinstance(progress_value, dict):
        raise ValueError("Master Progress is not an object")
    if semantic_sha256(progress_value) == report.base_master_progress_sha256:
        return base_readme, progress
    base_progress_value = dict(progress_value)
    for field in (
        "live_report_sha256",
        "live_execution_code_head",
        "live_execution_scope_sha256",
    ):
        base_progress_value.pop(field, None)
    base_progress_value.update(
        {
            "completed_stage": "PR-E",
            "current_stage": "PR-F",
            "main_head": "1c763eb815764e971855a5d6730981b9a2e5858a",
            "active_branch": "codex/dta-v21-p0-pr-f-live-closeout",
            "active_pr": 55,
            "merged_prs": [50, 51, 52, 53, 54],
            "live_demo_terminal": None,
            "final_engineering_terminal": None,
        }
    )
    if (
        active_pr in {50, 51, 52, 53, 54}
        or semantic_sha256(base_progress_value) != report.base_master_progress_sha256
    ):
        raise ValueError("Master Progress cannot be recovered to the bound base")
    return (
        base_readme,
        json.dumps(base_progress_value, indent=2, ensure_ascii=False) + "\n",
    )


def _verify_closeout_surfaces(
    *,
    root: Path,
    report: PublicLiveReportV21,
    final: bool,
    active_pr: int | None = None,
    merged_main_head: str | None = None,
) -> None:
    progress_relative = "docs/analysis/dta-v21-p0-master-progress.json"
    readme_path = root / "README.md"
    progress_path = root / progress_relative
    if (
        readme_path.is_symlink()
        or not readme_path.is_file()
        or progress_path.is_symlink()
        or not progress_path.is_file()
    ):
        raise ValueError("README or Master Progress is missing or unsafe")
    readme = readme_path.read_text(encoding="utf-8")
    progress = progress_path.read_text(encoding="utf-8")
    progress_value = json.loads(progress)
    if not isinstance(progress_value, dict):
        raise ValueError("Master Progress is not an object")
    if not final:
        if (
            hashlib.sha256(readme.encode("utf-8")).hexdigest()
            != report.base_readme_sha256
            or semantic_sha256(progress_value) != report.base_master_progress_sha256
        ):
            raise ValueError("README or Master Progress differs from the bound report")
        return
    if (
        not isinstance(active_pr, int)
        or isinstance(active_pr, bool)
        or not isinstance(merged_main_head, str)
        or len(merged_main_head) != 40
    ):
        raise ValueError("final public closeout lacks an exact PR number")
    section = _render_final_readme_section_v21(report)
    if readme.count(section) != 1:
        raise ValueError("README final live section differs from the bound report")
    base_readme = readme.replace(section, "", 1)
    base_progress_value = dict(progress_value)
    observed_final = {
        "completed_stage": base_progress_value.get("completed_stage"),
        "current_stage": base_progress_value.get("current_stage"),
        "main_head": base_progress_value.get("main_head"),
        "active_branch": base_progress_value.get("active_branch"),
        "active_pr": base_progress_value.get("active_pr"),
        "merged_prs": base_progress_value.get("merged_prs"),
        "live_report_sha256": base_progress_value.pop("live_report_sha256", None),
        "live_execution_code_head": base_progress_value.pop(
            "live_execution_code_head", None
        ),
        "live_execution_scope_sha256": base_progress_value.pop(
            "live_execution_scope_sha256", None
        ),
        "live_demo_terminal": base_progress_value.get("live_demo_terminal"),
        "final_engineering_terminal": base_progress_value.get(
            "final_engineering_terminal"
        ),
    }
    expected_final = {
        "completed_stage": "PR-F",
        "current_stage": "COMPLETE",
        "main_head": merged_main_head,
        "active_branch": None,
        "active_pr": None,
        "merged_prs": [50, 51, 52, 53, 54, active_pr],
        "live_report_sha256": report.report_sha256,
        "live_execution_code_head": report.live_execution_code_head,
        "live_execution_scope_sha256": report.live_execution_scope_sha256,
        "live_demo_terminal": "DTA_V21_P0_ENGINEERING_ACCEPTANCE_PASS",
        "final_engineering_terminal": "DTA_V21_P0_ENGINEERING_ACCEPTANCE_PASS",
    }
    base_progress_value.update(
        {
            "completed_stage": "PR-E",
            "current_stage": "PR-F",
            "main_head": "1c763eb815764e971855a5d6730981b9a2e5858a",
            "active_branch": "codex/dta-v21-p0-pr-f-live-closeout",
            "active_pr": 55,
            "merged_prs": [50, 51, 52, 53, 54],
            "live_demo_terminal": None,
            "final_engineering_terminal": None,
        }
    )
    if (
        observed_final != expected_final
        or hashlib.sha256(base_readme.encode("utf-8")).hexdigest()
        != report.base_readme_sha256
        or semantic_sha256(base_progress_value) != report.base_master_progress_sha256
        or readme != _render_final_readme_v21(base_readme=base_readme, report=report)
        or progress
        != _render_final_progress_v21(
            base_progress=json.dumps(base_progress_value, indent=2, ensure_ascii=False)
            + "\n",
            report=report,
            active_pr=active_pr,
            merged_main_head=merged_main_head,
        )
    ):
        raise ValueError("README or Master Progress differs from the bound report")


def run_report(*, repository_root: Path, private_root: Path) -> None:
    root = repository_root.resolve(strict=True)
    private = private_root.resolve(strict=True)
    verify_private_tree_permissions(private / "pr-f")
    try:
        status = _COMMAND_RUNNER.run(
            ("git", "status", "--porcelain=v1", "--untracked-files=all"),
            cwd=root,
            timeout_seconds=30,
        ).stdout
    except RuntimeError as error:
        raise ValueError("PR-F report Git status is unavailable") from error
    status_paths = {line[3:] for line in status.splitlines() if len(line) >= 4}
    if " -> " in status or not status_paths.issubset(_PUBLIC_REPORT_PATHS):
        raise ValueError("PR-F report projection has non-report worktree changes")
    execution_code_head = _git(root, "rev-parse", "HEAD")
    protocol = load_ad_cpu_resource_recovery_protocol_v1(
        root / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
    )
    config = load_live_demo_config_v21(root / "config/dta-v21/live/live-demo.v1.json")
    base_readme = _git_blob_text(
        root, treeish=execution_code_head, relative_path="README.md"
    )
    base_progress = _git_blob_text(
        root,
        treeish=execution_code_head,
        relative_path="docs/analysis/dta-v21-p0-master-progress.json",
    )
    base_progress_value = json.loads(base_progress)
    if not isinstance(base_progress_value, dict):
        raise ValueError("Master Progress source is not an object")
    report = build_public_live_report_v21(
        repository_root=root,
        prf_private_root=private / "pr-f",
        protocol=protocol,
        config=config,
        registry=load_default_runbook_registry(root),
        execution_code_head=execution_code_head,
        execution_scope_sha256=_execution_scope_sha256(
            root, treeish=execution_code_head
        ),
        base_readme_sha256=hashlib.sha256(base_readme.encode("utf-8")).hexdigest(),
        base_master_progress_sha256=semantic_sha256(base_progress_value),
    )
    markdown = render_public_live_markdown_v21(report)
    result_root = root / "docs/results"
    review_root = root / "docs/review-evidence/dta-v21-live"
    _write_public_once(
        result_root / "dta-v21-live-demo.json",
        json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
    )
    _write_public_once(result_root / "dta-v21-live-demo.md", markdown)
    _write_public_once(
        result_root / "dta-v21-live-demo-human-brief.md",
        render_public_human_brief_v21(report),
    )
    _write_public_once(
        result_root / "dta-v21-final-summary.md",
        render_public_final_summary_v21(report),
    )
    _write_public_once(
        result_root / "dta-v21-interview-brief.md",
        render_public_interview_brief_v21(report),
    )
    disposition_payload = _pending_disposition_payload(report)
    disposition = {
        **disposition_payload,
        "disposition_sha256": semantic_sha256(disposition_payload),
    }
    _write_public_once(
        review_root / "current-disposition.json",
        json.dumps(disposition, indent=2, ensure_ascii=False) + "\n",
    )


def run_verify(*, repository_root: Path) -> str:
    root = repository_root.resolve(strict=True)
    report_path = root / "docs/results/dta-v21-live-demo.json"
    public_paths = (
        report_path,
        root / "docs/results/dta-v21-live-demo.md",
        root / "docs/results/dta-v21-live-demo-human-brief.md",
        root / "docs/results/dta-v21-final-summary.md",
        root / "docs/results/dta-v21-interview-brief.md",
        root / "docs/review-evidence/dta-v21-live/current-disposition.json",
    )
    present = tuple(path.exists() or path.is_symlink() for path in public_paths)
    if not any(present):
        load_live_demo_config_v21(root / "config/dta-v21/live/live-demo.v1.json")
        return "DTA_V21_PR_F_LIVE_REPORT_PENDING"
    if not all(present):
        raise ValueError("public live closure outputs are partial")
    paths = public_paths[1:5]
    report = verify_public_live_report_v21(report_path=report_path, claim_paths=paths)
    _verify_public_only_delta(
        root, execution_scope_sha256=report.live_execution_scope_sha256
    )
    disposition = _read_json(
        root / "docs/review-evidence/dta-v21-live/current-disposition.json"
    )
    digest = disposition.pop("disposition_sha256", None)
    if digest != semantic_sha256(disposition):
        raise ValueError("public live disposition differs from the report")
    pending = _pending_disposition_payload(report)
    if disposition == pending:
        _verify_closeout_surfaces(root=root, report=report, final=False)
        return "DTA_V21_PR_F_FINAL_ACCEPTANCE_PENDING"
    candidate_head = disposition.get("acceptance_candidate_head")
    ci_run_id = disposition.get("exact_head_ci_run_id")
    ci_run_url = disposition.get("exact_head_ci_run_url")
    active_pr = disposition.get("active_pr")
    merged_main_head = disposition.get("merged_main_head")
    merged_pr_url = disposition.get("merged_pr_url")
    final_expected: dict[str, object] = {
        **_base_disposition_payload(report),
        "terminal": "DTA_V21_PR_F_POST_MERGE_CLOSEOUT_PROJECTED",
        "active_pr": active_pr,
        "acceptance_candidate_head": candidate_head,
        "merged_main_head": merged_main_head,
        "merged_pr_url": merged_pr_url,
        "candidate_exact_head_ci": "SUCCESS",
        "exact_head_ci_head": candidate_head,
        "exact_head_ci_run_id": ci_run_id,
        "exact_head_ci_run_url": ci_run_url,
        "candidate_independent_review": "MUST_FIX_0",
        "candidate_independent_review_head": candidate_head,
        "candidate_claim_accuracy": "PASS",
        "post_merge_exact_head_ci": "REQUIRED_AFTER_CLOSEOUT_COMMIT",
        "post_merge_independent_review": "REQUIRED_AFTER_CLOSEOUT_COMMIT",
    }
    if (
        disposition != final_expected
        or not isinstance(candidate_head, str)
        or len(candidate_head) != 40
        or any(character not in "0123456789abcdef" for character in candidate_head)
        or not isinstance(ci_run_id, int)
        or isinstance(ci_run_id, bool)
        or ci_run_id < 1
        or not isinstance(ci_run_url, str)
        or not ci_run_url.startswith("https://github.com/")
        or not isinstance(merged_main_head, str)
        or len(merged_main_head) != 40
        or not isinstance(merged_pr_url, str)
        or not merged_pr_url.startswith("https://github.com/")
        or not isinstance(active_pr, int)
        or isinstance(active_pr, bool)
        or active_pr < 1
    ):
        raise ValueError("public final acceptance disposition differs")
    _verify_closeout_surfaces(
        root=root,
        report=report,
        final=True,
        active_pr=active_pr,
        merged_main_head=merged_main_head,
    )
    return "DTA_V21_PR_F_POST_MERGE_CLOSEOUT_PROJECTED"


def run_finalize(
    *,
    repository_root: Path,
    exact_head_ci_sha: str,
    independent_review_head: str,
    independent_review_confirmation: str,
    active_pr: int,
) -> str:
    root = repository_root.resolve(strict=True)
    progress = _read_json(root / "docs/analysis/dta-v21-p0-master-progress.json")
    if progress.get("active_decision_id") == "DEC-046":
        raise ValueError("legacy four-slot finalization is superseded by DEC-046")
    merged_main_head = _git(root, "rev-parse", "HEAD")
    if (
        exact_head_ci_sha != independent_review_head
        or independent_review_confirmation != _FINAL_REVIEW_CONFIRMATION
        or active_pr < 1
    ):
        raise ValueError("PR-F final acceptance candidate gates differ")
    if _git(root, "branch", "--show-current") != "main":
        raise ValueError("PR-F post-merge finalization requires main")
    merged_pr = _verify_merged_pr(root, active_pr=active_pr)
    if (
        merged_pr["head_sha"] != exact_head_ci_sha
        or merged_pr["merge_sha"] != merged_main_head
    ):
        raise ValueError("PR-F merged PR differs from the accepted candidate")
    ci_receipt = _verify_exact_head_github_actions(
        root, head=exact_head_ci_sha, required_event="pull_request"
    )
    report = verify_public_live_report_v21(
        report_path=root / "docs/results/dta-v21-live-demo.json",
        claim_paths=(
            root / "docs/results/dta-v21-live-demo.md",
            root / "docs/results/dta-v21-live-demo-human-brief.md",
            root / "docs/results/dta-v21-final-summary.md",
            root / "docs/results/dta-v21-interview-brief.md",
        ),
    )
    _verify_public_only_delta(
        root, execution_scope_sha256=report.live_execution_scope_sha256
    )
    progress_relative = "docs/analysis/dta-v21-p0-master-progress.json"
    base_readme, base_progress = _recover_base_closeout_text(
        root=root, report=report, active_pr=active_pr
    )
    disposition_path = (
        root / "docs/review-evidence/dta-v21-live/current-disposition.json"
    )
    pending_payload = _pending_disposition_payload(report)
    pending_document = (
        json.dumps(
            {
                **pending_payload,
                "disposition_sha256": semantic_sha256(pending_payload),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    final_payload: dict[str, object] = {
        **_base_disposition_payload(report),
        "terminal": "DTA_V21_PR_F_POST_MERGE_CLOSEOUT_PROJECTED",
        "active_pr": active_pr,
        "acceptance_candidate_head": exact_head_ci_sha,
        "merged_main_head": merged_main_head,
        "merged_pr_url": merged_pr["url"],
        "candidate_exact_head_ci": "SUCCESS",
        "exact_head_ci_head": exact_head_ci_sha,
        "exact_head_ci_run_id": ci_receipt["run_id"],
        "exact_head_ci_run_url": ci_receipt["url"],
        "candidate_independent_review": "MUST_FIX_0",
        "candidate_independent_review_head": exact_head_ci_sha,
        "candidate_claim_accuracy": "PASS",
        "post_merge_exact_head_ci": "REQUIRED_AFTER_CLOSEOUT_COMMIT",
        "post_merge_independent_review": "REQUIRED_AFTER_CLOSEOUT_COMMIT",
    }
    final_document = (
        json.dumps(
            {
                **final_payload,
                "disposition_sha256": semantic_sha256(final_payload),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    _replace_public_text_exact(
        root / "README.md",
        expected=base_readme,
        replacement=_render_final_readme_v21(base_readme=base_readme, report=report),
    )
    _replace_public_text_exact(
        root / progress_relative,
        expected=base_progress,
        replacement=_render_final_progress_v21(
            base_progress=base_progress,
            report=report,
            active_pr=active_pr,
            merged_main_head=merged_main_head,
        ),
    )
    _replace_public_text_exact(
        disposition_path,
        expected=pending_document,
        replacement=final_document,
    )
    if run_verify(repository_root=root) != (
        "DTA_V21_PR_F_POST_MERGE_CLOSEOUT_PROJECTED"
    ):
        raise ValueError("PR-F final acceptance projection did not verify")
    return "DTA_V21_PR_F_POST_MERGE_CLOSEOUT_PROJECTED"


def run_closeout(
    *,
    repository_root: Path,
    exact_head_ci_sha: str,
    independent_review_head: str,
    independent_review_confirmation: str,
) -> str:
    root = repository_root.resolve(strict=True)
    progress = _read_json(root / "docs/analysis/dta-v21-p0-master-progress.json")
    if progress.get("active_decision_id") == "DEC-046":
        raise ValueError("legacy four-slot closeout is superseded by DEC-046")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("PR-F final closeout requires an exactly clean main HEAD")
    head = _git(root, "rev-parse", "HEAD")
    if (
        head != exact_head_ci_sha
        or head != independent_review_head
        or independent_review_confirmation != _FINAL_REVIEW_CONFIRMATION
        or _git(root, "branch", "--show-current") != "main"
    ):
        raise ValueError("PR-F exact-main closeout gates differ")
    if run_verify(repository_root=root) != (
        "DTA_V21_PR_F_POST_MERGE_CLOSEOUT_PROJECTED"
    ):
        raise ValueError("PR-F post-merge projection is not verified")
    disposition = _read_json(
        root / "docs/review-evidence/dta-v21-live/current-disposition.json"
    )
    active_pr = disposition.get("active_pr")
    if not isinstance(active_pr, int) or isinstance(active_pr, bool):
        raise ValueError("PR-F closeout lacks the merged PR number")
    merged_pr = _verify_merged_pr(root, active_pr=active_pr)
    if merged_pr["head_sha"] != disposition.get(
        "acceptance_candidate_head"
    ) or merged_pr["merge_sha"] != disposition.get("merged_main_head"):
        raise ValueError("PR-F closeout merged PR binding differs")
    candidate_ci = _verify_exact_head_github_actions(
        root, head=merged_pr["head_sha"], required_event="pull_request"
    )
    if candidate_ci["run_id"] != disposition.get(
        "exact_head_ci_run_id"
    ) or candidate_ci["url"] != disposition.get("exact_head_ci_run_url"):
        raise ValueError("PR-F closeout candidate CI binding differs")
    _git(root, "merge-base", "--is-ancestor", merged_pr["merge_sha"], head)
    _verify_exact_head_github_actions(
        root, head=head, required_event="workflow_dispatch"
    )
    return "DTA_V21_P0_ENGINEERING_ACCEPTANCE_PASS"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "execute"):
        command = subparsers.add_parser(name)
        command.add_argument("--repository-root", type=Path, required=True)
        command.add_argument("--private-root", type=Path, required=True)
        command.add_argument("--provider-env", type=Path, required=True)
        if name == "preflight":
            command.add_argument("--exact-head-ci-sha", required=True)
    reconcile = subparsers.add_parser("reconcile")
    reconcile.add_argument("--repository-root", type=Path, required=True)
    reconcile.add_argument("--private-root", type=Path, required=True)
    review = subparsers.add_parser("record-retry-review")
    review.add_argument("--repository-root", type=Path, required=True)
    review.add_argument("--private-root", type=Path, required=True)
    review.add_argument("--reviewer", required=True)
    retry_admit = subparsers.add_parser("retry-admit")
    retry_admit.add_argument("--repository-root", type=Path, required=True)
    retry_admit.add_argument("--private-root", type=Path, required=True)
    verify_reconciliation = subparsers.add_parser("verify-reconciliation")
    verify_reconciliation.add_argument("--repository-root", type=Path, required=True)
    verify_reconciliation.add_argument("--private-root", type=Path, required=True)
    report = subparsers.add_parser("report")
    report.add_argument("--repository-root", type=Path, required=True)
    report.add_argument("--private-root", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--repository-root", type=Path, required=True)
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--repository-root", type=Path, required=True)
    finalize.add_argument("--exact-head-ci-sha", required=True)
    finalize.add_argument("--independent-review-head", required=True)
    finalize.add_argument("--independent-review-confirmation", required=True)
    finalize.add_argument("--active-pr", type=int, required=True)
    closeout = subparsers.add_parser("closeout")
    closeout.add_argument("--repository-root", type=Path, required=True)
    closeout.add_argument("--exact-head-ci-sha", required=True)
    closeout.add_argument("--independent-review-head", required=True)
    closeout.add_argument("--independent-review-confirmation", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "preflight":
        record = run_preflight(
            repository_root=args.repository_root,
            private_root=args.private_root,
            provider_env_path=args.provider_env,
            exact_head_ci_sha=args.exact_head_ci_sha,
        )
        print(record["terminal"])
    elif args.command == "execute":
        run_execute(
            repository_root=args.repository_root,
            private_root=args.private_root,
            provider_env_path=args.provider_env,
        )
        print("DTA_V21_PR_F_LIVE_PORTFOLIO_PASS")
    elif args.command == "reconcile":
        record = run_reconcile(
            repository_root=args.repository_root,
            private_root=args.private_root,
        )
        print(record["classification"])
    elif args.command == "record-retry-review":
        record = run_record_retry_review(
            repository_root=args.repository_root,
            private_root=args.private_root,
            reviewer=args.reviewer,
        )
        print(f"MUST_FIX_{record['must_fix_count']}_CLAIM_ACCURACY_{record['claim_accuracy']}")
    elif args.command == "retry-admit":
        record = run_retry_admit(
            repository_root=args.repository_root,
            private_root=args.private_root,
        )
        print(record["verdict"])
    elif args.command == "verify-reconciliation":
        record = run_verify_reconciliation(
            repository_root=args.repository_root,
            private_root=args.private_root,
        )
        print(record["reconciliation_sha256"])
    elif args.command == "report":
        run_report(repository_root=args.repository_root, private_root=args.private_root)
        print("DTA_V21_PR_F_FINAL_ACCEPTANCE_PENDING")
    elif args.command == "verify":
        print(run_verify(repository_root=args.repository_root))
    elif args.command == "finalize":
        print(
            run_finalize(
                repository_root=args.repository_root,
                exact_head_ci_sha=args.exact_head_ci_sha,
                independent_review_head=args.independent_review_head,
                independent_review_confirmation=(args.independent_review_confirmation),
                active_pr=args.active_pr,
            )
        )
    else:
        print(
            run_closeout(
                repository_root=args.repository_root,
                exact_head_ci_sha=args.exact_head_ci_sha,
                independent_review_head=args.independent_review_head,
                independent_review_confirmation=(args.independent_review_confirmation),
            )
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
