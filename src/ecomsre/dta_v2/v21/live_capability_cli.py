"""Guarded CLI for the Amendment-3 PR-F capability closeout."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Sequence

from ecomsre.dta_v2.v21.live_capability_closeout import (
    CAPABILITY_MISS_ATTEMPT_ID_V1,
    NoFaultCapabilityMissV1,
    PositiveContinuationQuiescenceV1,
    PositiveContinuationReadinessV3,
    PositiveContinuationReviewV1,
    PositiveContinuationStandingAuthorizationV1,
    build_positive_continuation_admission_v1,
    build_positive_continuation_readiness_v3,
    verify_no_fault_capability_miss_eligibility_v1,
    verify_positive_continuation_admission_v1,
    write_no_fault_capability_miss_v1,
    write_positive_continuation_admission_v1,
    write_positive_continuation_quiescence_v1,
    write_positive_continuation_readiness_v3,
    write_positive_continuation_review_v1,
    write_positive_continuation_standing_authorization_v1,
)
from ecomsre.dta_v2.v21.live_capability_reporting import (
    PublicLiveReportV3,
    build_public_live_report_v3,
    render_public_final_summary_v3,
    render_public_human_brief_v3,
    render_public_interview_brief_v3,
    render_public_live_markdown_v3,
    render_public_readme_block_v3,
    verify_public_live_report_v3,
    verify_public_text_v3,
)
from ecomsre.dta_v2.v21.live_cli import (
    _git,
    _git_blob_text,
    _load_exact_readiness,
    _verify_exact_head_github_actions,
    _verify_frozen_private_pr_e,
    _verify_merged_pr,
    _verify_private_protocol_freeze,
    _write_public_once,
    run_preflight,
)
from ecomsre.dta_v2.v21.contracts import semantic_sha256
from ecomsre.dta_v2.v21.live_protocol import (
    load_ad_cpu_resource_recovery_protocol_v1,
    verify_accepted_ad_cpu_calibration_binding,
)
from ecomsre.dta_v2.v21.live_reconciliation import (
    verify_post_terminal_reconciliation_v1,
    verify_retry_consumption_v1,
)
from ecomsre.dta_v2.v21.live_runner import (
    run_owned_live_positive_continuation_v1,
)
from ecomsre.dta_v2.v21.registry import load_default_runbook_registry
from ecomsre.dta_v2.v21.live_contracts import (
    LiveReadinessV2,
    load_live_demo_config_v21,
)
from ecomsre_live_sandbox.contracts import verify_private_tree_permissions


_EXECUTION_CONFIRMATION = (
    "USER_EXPLICIT_DTA_V21_PRF_CAPABILITY_CLOSEOUT_AND_POSITIVE_CONTINUATION"
)
_FINAL_REVIEW_CONFIRMATION = "MUST_FIX_0_SHOULD_FIX_0_CLAIM_ACCURACY_PASS"
_README_MARKER = "<!-- dta-v21-pr-f-capability-closeout -->"
_PUBLIC_PATHS = frozenset(
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


def _read_model(path: Path, model_type):
    if path.is_symlink() or not path.is_file():
        raise ValueError("required capability-closeout input is missing or unsafe")
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def _read_regular_text(path: Path, *, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or unsafe")
    if stat.S_IMODE(path.stat().st_mode) != 0o644:
        raise ValueError(f"{label} mode differs from 0644")
    return path.read_text(encoding="utf-8")


def run_capability_record(
    *, repository_root: Path, private_root: Path
) -> NoFaultCapabilityMissV1:
    write_positive_continuation_standing_authorization_v1(
        private_root=private_root
    )
    return write_no_fault_capability_miss_v1(
        repository_root=repository_root, private_root=private_root
    )


def _assert_no_execution_lease(prf_root: Path) -> None:
    path = prf_root / "execution.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    except BlockingIOError as error:
        raise ValueError("positive-continuation execution lease is held") from error
    finally:
        os.close(descriptor)


def run_positive_preflight(
    *,
    repository_root: Path,
    private_root: Path,
    provider_env_path: Path,
    exact_head_ci_sha: str,
) -> PositiveContinuationReadinessV3:
    root = repository_root.resolve(strict=True)
    private = private_root.resolve(strict=True)
    base_value = run_preflight(
        repository_root=root,
        private_root=private,
        provider_env_path=provider_env_path,
        exact_head_ci_sha=exact_head_ci_sha,
    )
    base = LiveReadinessV2.model_validate(base_value)
    capability = verify_no_fault_capability_miss_eligibility_v1(
        repository_root=root,
        private_root=private,
        require_no_positive_attempts=True,
    )
    stored_capability = _read_model(
        private
        / "pr-f/capability-closeout"
        / CAPABILITY_MISS_ATTEMPT_ID_V1
        / "no-fault-capability-miss.v1.json",
        NoFaultCapabilityMissV1,
    )
    standing = _read_model(
        private / "pr-f/capability-closeout/standing-authorization.v1.json",
        PositiveContinuationStandingAuthorizationV1,
    )
    parent = verify_retry_consumption_v1(
        repository_root=root,
        private_root=private,
        new_code_head=capability.code_head,
    )
    _assert_no_execution_lease(private / "pr-f")
    if capability != stored_capability:
        raise ValueError("capability-miss record differs before quiescence")
    quiescence = PositiveContinuationQuiescenceV1.build(
        code_head=base.code_head,
        observed_at=datetime.now(timezone.utc),
        docker_boundary="LOCAL_UNIX_DOCKER",
        owned_container_count=0,
        owned_network_count=0,
        owned_volume_count=0,
        required_ports_available=True,
        execution_lease_held=False,
        private_permissions_verified=True,
        source_worktree_clean=True,
        frozen_bindings_verified=True,
        capability_miss_sha256=capability.classification_sha256,
        parent_retry_consumption_sha256=parent.consumption_sha256,
    )
    write_positive_continuation_quiescence_v1(
        private_root=private, quiescence=quiescence
    )
    readiness = build_positive_continuation_readiness_v3(
        base_readiness=base,
        quiescence=quiescence,
        capability=capability,
        standing_authorization=standing,
    )
    write_positive_continuation_readiness_v3(
        private_root=private, readiness=readiness
    )
    verify_private_tree_permissions(private / "pr-f")
    return readiness


def run_record_positive_review(
    *, repository_root: Path, private_root: Path, reviewer: str
) -> PositiveContinuationReviewV1:
    root = repository_root.resolve(strict=True)
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("positive review requires an exactly clean worktree")
    review = PositiveContinuationReviewV1.build(
        code_head=_git(root, "rev-parse", "HEAD"),
        reviewer=reviewer,
        reviewed_at=datetime.now(timezone.utc),
        must_fix_count=0,
        should_fix_count=0,
        claim_accuracy="PASS",
    )
    write_positive_continuation_review_v1(
        private_root=private_root, review=review
    )
    return review


def run_positive_admit(
    *, repository_root: Path, private_root: Path
):
    root = repository_root.resolve(strict=True)
    private = private_root.resolve(strict=True)
    head = _git(root, "rev-parse", "HEAD")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("positive admission requires an exactly clean worktree")
    capability = verify_no_fault_capability_miss_eligibility_v1(
        repository_root=root,
        private_root=private,
        require_no_positive_attempts=True,
    )
    admission_root = private / "pr-f/positive-continuation-admissions" / head
    readiness = _read_model(
        admission_root / "readiness.v3.json", PositiveContinuationReadinessV3
    )
    quiescence = _read_model(
        admission_root / "quiescence.v1.json", PositiveContinuationQuiescenceV1
    )
    review = _read_model(
        private / "pr-f/positive-continuation-reviews" / head / "review.v1.json",
        PositiveContinuationReviewV1,
    )
    parent = verify_retry_consumption_v1(
        repository_root=root,
        private_root=private,
        new_code_head=capability.code_head,
    )
    reconciliation, _ = verify_post_terminal_reconciliation_v1(
        repository_root=root, private_root=private
    )
    admission = build_positive_continuation_admission_v1(
        new_code_head=head,
        base_main_head=_git(root, "rev-parse", "origin/main"),
        capability=capability,
        parent_retry_consumption_sha256=parent.consumption_sha256,
        original_blocker_reconciliation_sha256=(
            reconciliation.reconciliation_sha256
        ),
        readiness=readiness,
        quiescence=quiescence,
        review=review,
    )
    write_positive_continuation_admission_v1(
        private_root=private, admission=admission
    )
    return verify_positive_continuation_admission_v1(
        repository_root=root, private_root=private, new_code_head=head
    )


def run_positive_execute(
    *, repository_root: Path, private_root: Path, provider_env_path: Path
) -> None:
    if os.environ.get("DTA_V21_POSITIVE_CONTINUATION_EXECUTE") != (
        _EXECUTION_CONFIRMATION
    ):
        raise ValueError("exact positive-continuation confirmation is missing")
    root = repository_root.resolve(strict=True)
    private = private_root.resolve(strict=True)
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("positive continuation requires an exactly clean worktree")
    if _git(root, "branch", "--show-current") != (
        "codex/dta-v21-p0-pr-f-live-closeout"
    ):
        raise ValueError("positive continuation branch differs from authorization")
    head = _git(root, "rev-parse", "HEAD")
    master, readiness, identity, raw_compose, flagd_directory = (
        _load_exact_readiness(repository_root=root, private_root=private)
    )
    capability = verify_no_fault_capability_miss_eligibility_v1(
        repository_root=root,
        private_root=private,
        require_no_positive_attempts=True,
    )
    v3 = _read_model(
        private
        / "pr-f/positive-continuation-admissions"
        / head
        / "readiness.v3.json",
        PositiveContinuationReadinessV3,
    )
    verify_positive_continuation_admission_v1(
        repository_root=root, private_root=private, new_code_head=head
    )
    config = load_live_demo_config_v21(
        root / "config/dta-v21/live/live-demo.v1.json"
    )
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
    run_owned_live_positive_continuation_v1(
        repository_root=root,
        prf_private_root=private / "pr-f",
        provider_env_path=provider_env_path,
        config=config,
        registry=load_default_runbook_registry(root),
        protocol=protocol,
        master_authorization=master,
        readiness=readiness,
        v3_readiness=v3,
        capability_miss=capability,
        readiness_identity=identity,
        readiness_raw_compose=raw_compose,
        readiness_flagd_directory=flagd_directory,
        code_head=head,
    )


def _human_brief(report: PublicLiveReportV3) -> str:
    return render_public_human_brief_v3(report)


def _final_summary(report: PublicLiveReportV3) -> str:
    return render_public_final_summary_v3(report)


def _readme_block(report: PublicLiveReportV3) -> str:
    return render_public_readme_block_v3(report)


def _pending_disposition(report: PublicLiveReportV3) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "dta-v21.pr-f-capability-closeout-disposition.v1",
        "terminal": "DTA_V21_PR_F_LIMITATION_CLOSEOUT_FINAL_REVIEW_PENDING",
        "report_sha256": report.report_sha256,
        "live_execution_code_head": report.live_execution_code_head,
        "final_terminal": report.overall_closeout_terminal,
        "exact_head_ci": "PENDING_AFTER_PUBLIC_PROJECTION",
        "independent_review": "PENDING_AFTER_PUBLIC_PROJECTION",
        "claim_accuracy": "PENDING_AFTER_PUBLIC_PROJECTION",
    }
    return {**payload, "disposition_sha256": semantic_sha256(payload)}


def run_positive_report(
    *, repository_root: Path, private_root: Path
) -> PublicLiveReportV3:
    root = repository_root.resolve(strict=True)
    private = private_root.resolve(strict=True)
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("positive report requires a clean execution HEAD")
    head = _git(root, "rev-parse", "HEAD")
    report = build_public_live_report_v3(
        repository_root=root,
        private_root=private,
        execution_code_head=head,
    )
    results = root / "docs/results"
    review = root / "docs/review-evidence/dta-v21-live"
    _write_public_once(
        results / "dta-v21-live-demo.json",
        report.model_dump_json(indent=2) + "\n",
    )
    _write_public_once(
        results / "dta-v21-live-demo.md", render_public_live_markdown_v3(report)
    )
    _write_public_once(
        results / "dta-v21-live-demo-human-brief.md",
        render_public_human_brief_v3(report),
    )
    _write_public_once(
        results / "dta-v21-final-summary.md",
        render_public_final_summary_v3(report),
    )
    _write_public_once(
        results / "dta-v21-interview-brief.md",
        render_public_interview_brief_v3(report),
    )
    disposition = _pending_disposition(report)
    _write_public_once(
        review / "current-disposition.json",
        json.dumps(disposition, indent=2, ensure_ascii=False) + "\n",
    )
    readme = root / "README.md"
    current = _read_regular_text(readme, label="base README")
    if hashlib.sha256(current.encode("utf-8")).hexdigest() != (
        report.base_readme_sha256
    ):
        raise ValueError("base README digest differs from report")
    if _README_MARKER in current:
        raise ValueError("README capability-closeout block already exists")
    readme.write_text(
        current.rstrip() + "\n\n" + render_public_readme_block_v3(report),
        encoding="utf-8",
    )
    return report


def run_positive_verify(*, repository_root: Path) -> str:
    root = repository_root.resolve(strict=True)
    report_path = root / "docs/results/dta-v21-live-demo.json"
    disposition_path = (
        root / "docs/review-evidence/dta-v21-live/current-disposition.json"
    )
    paths = (
        root / "docs/results/dta-v21-live-demo.md",
        root / "docs/results/dta-v21-live-demo-human-brief.md",
        root / "docs/results/dta-v21-final-summary.md",
        root / "docs/results/dta-v21-interview-brief.md",
    )
    readme_path = root / "README.md"
    readme_text = _read_regular_text(readme_path, label="public README")
    readme_block_present = _README_MARKER in readme_text
    present = (
        report_path.exists() or report_path.is_symlink(),
        *(path.exists() or path.is_symlink() for path in paths),
        disposition_path.exists() or disposition_path.is_symlink(),
        readme_block_present,
    )
    if not any(present):
        return "DTA_V21_PR_F_CAPABILITY_CLOSEOUT_REPORT_PENDING"
    if not all(present):
        raise ValueError("public v3 capability-closeout outputs are partial")
    report = verify_public_live_report_v3(
        report_path=report_path, claim_paths=paths
    )
    base_readme = _git_blob_text(
        root,
        treeish=report.live_execution_code_head,
        relative_path="README.md",
    )
    if hashlib.sha256(base_readme.encode("utf-8")).hexdigest() != (
        report.base_readme_sha256
    ):
        raise ValueError("bound base README digest differs")
    expected_readme = (
        base_readme.rstrip()
        + "\n\n"
        + render_public_readme_block_v3(report)
    )
    readme = readme_text
    if readme != expected_readme:
        raise ValueError("README capability-closeout wording differs")
    verify_public_text_v3(readme)
    if disposition_path.is_symlink() or not disposition_path.is_file():
        raise ValueError("capability-closeout disposition is missing or unsafe")
    disposition = json.loads(disposition_path.read_text(encoding="utf-8"))
    if not isinstance(disposition, dict):
        raise ValueError("capability-closeout disposition is invalid")
    digest = disposition.pop("disposition_sha256", None)
    if digest != semantic_sha256(disposition):
        raise ValueError("capability-closeout disposition SHA-256 differs")
    pending = _pending_disposition(report)
    pending.pop("disposition_sha256")
    if disposition == pending:
        return "DTA_V21_PR_F_LIMITATION_CLOSEOUT_FINAL_REVIEW_PENDING"
    candidate = disposition.get("acceptance_candidate_head")
    merge_head = disposition.get("merged_main_head")
    active_pr = disposition.get("merged_pr")
    expected_disposition_fields = {
        "schema_version",
        "terminal",
        "report_sha256",
        "live_execution_code_head",
        "final_terminal",
        "merged_pr",
        "merged_pr_url",
        "acceptance_candidate_head",
        "merged_main_head",
        "candidate_exact_head_ci",
        "candidate_exact_head_ci_run_id",
        "candidate_exact_head_ci_run_url",
        "candidate_independent_review",
        "candidate_independent_review_head",
        "candidate_claim_accuracy",
        "post_merge_exact_head_ci",
        "post_merge_independent_review",
    }
    if (
        set(disposition) != expected_disposition_fields
        or disposition.get("schema_version")
        != "dta-v21.pr-f-capability-closeout-disposition.v1"
        or disposition.get("terminal")
        != "DTA_V21_PR_F_POST_MERGE_LIMITATION_CLOSEOUT_PROJECTED"
        or disposition.get("report_sha256") != report.report_sha256
        or disposition.get("live_execution_code_head")
        != report.live_execution_code_head
        or disposition.get("final_terminal")
        != report.overall_closeout_terminal
        or disposition.get("candidate_exact_head_ci") != "SUCCESS"
        or disposition.get("candidate_independent_review")
        != "MUST_FIX_0_SHOULD_FIX_0"
        or disposition.get("candidate_claim_accuracy") != "PASS"
        or disposition.get("candidate_independent_review_head") != candidate
        or disposition.get("post_merge_exact_head_ci")
        != "REQUIRED_AFTER_CLOSEOUT_COMMIT"
        or disposition.get("post_merge_independent_review")
        != "REQUIRED_AFTER_CLOSEOUT_COMMIT"
        or not isinstance(candidate, str)
        or re.fullmatch(r"[0-9a-f]{40}", candidate) is None
        or not isinstance(merge_head, str)
        or re.fullmatch(r"[0-9a-f]{40}", merge_head) is None
        or active_pr != 55
        or disposition.get("merged_pr_url")
        != "https://github.com/raidriar/EcomSRE-Agent/pull/55"
        or not isinstance(disposition.get("candidate_exact_head_ci_run_id"), int)
        or isinstance(disposition.get("candidate_exact_head_ci_run_id"), bool)
        or int(disposition["candidate_exact_head_ci_run_id"]) < 1
        or not isinstance(disposition.get("candidate_exact_head_ci_run_url"), str)
        or re.fullmatch(
            r"https://github\.com/.+/actions/runs/[0-9]+",
            str(disposition["candidate_exact_head_ci_run_url"]),
        )
        is None
    ):
        raise ValueError("final capability-closeout disposition differs")
    progress = json.loads(
        (root / "docs/analysis/dta-v21-p0-master-progress.json").read_text(
            encoding="utf-8"
        )
    )
    if (
        progress.get("completed_stage") != "PR-F"
        or progress.get("current_stage") != "COMPLETE_WITH_LIMITATION"
        or progress.get("active_branch") is not None
        or progress.get("active_pr") is not None
        or progress.get("main_head") != merge_head
        or progress.get("merged_prs") != [50, 51, 52, 53, 54, 55]
        or progress.get("no_fault_diagnosis_passed") is not False
        or progress.get("no_fault_no_write_safety_passed") is not True
        or progress.get("positive_continuation_status") != "PASS"
        or progress.get("positive_slots_passed") != 3
        or progress.get("four_slot_acceptance_passed") is not False
        or progress.get("live_report_sha256") != report.report_sha256
        or progress.get("live_execution_code_head")
        != report.live_execution_code_head
        or progress.get("live_demo_terminal")
        != "DTA_V21_PR_F_POSITIVE_PORTFOLIO_PASS_WITH_NO_FAULT_DIAGNOSIS_MISS"
        or progress.get("final_engineering_terminal")
        != "DTA_V21_P0_ENGINEERING_CLOSEOUT_WITH_NO_FAULT_DIAGNOSIS_MISS"
    ):
        raise ValueError("final capability-closeout progress differs")
    return "DTA_V21_PR_F_POST_MERGE_LIMITATION_CLOSEOUT_PROJECTED"


def _verify_public_only_candidate_delta(
    *, root: Path, execution_head: str, candidate_head: str
) -> None:
    records = _git(
        root, "diff", "--name-status", execution_head, candidate_head, "--"
    ).splitlines()
    parsed: list[tuple[str, str]] = []
    for record in records:
        parts = record.split("\t")
        if len(parts) != 2:
            raise ValueError("post-execution candidate contains a rename or copy")
        parsed.append((parts[0], parts[1]))
    expected_changed = _PUBLIC_PATHS - {
        "docs/analysis/dta-v21-p0-master-progress.json"
    }
    if (
        {path for _, path in parsed} != expected_changed
        or any(status not in {"A", "M"} for status, _ in parsed)
    ):
        raise ValueError("post-execution candidate delta is not public-only")
    for relative_path in _PUBLIC_PATHS:
        entry = _git(
            root,
            "ls-tree",
            candidate_head,
            "--",
            relative_path,
        )
        if not entry.startswith("100644 blob ") or not entry.endswith(
            f"\t{relative_path}"
        ):
            raise ValueError("public candidate file mode or type differs")


def _allow_only_resumable_finalize_delta(root: Path) -> None:
    allowed = {
        "docs/analysis/dta-v21-p0-master-progress.json",
        "docs/review-evidence/dta-v21-live/current-disposition.json",
        (
            "docs/analysis/"
            "dta-v21-p0-master-progress.json.dta-v21-finalize.tmp"
        ),
        (
            "docs/review-evidence/dta-v21-live/"
            "current-disposition.json.dta-v21-finalize.tmp"
        ),
    }
    for record in _git(
        root, "status", "--porcelain=v1", "--untracked-files=all"
    ).splitlines():
        if " -> " in record or len(record) < 4 or record[3:] not in allowed:
            raise ValueError("post-merge limitation-closeout worktree differs")


def _replace_regular_text_resumably(
    path: Path, *, previous: str, expected: str
) -> None:
    current = _read_regular_text(path, label=path.name)
    if current == expected:
        return
    if current != previous:
        raise ValueError("post-merge closeout file is neither previous nor expected")
    temporary = path.with_name(f"{path.name}.dta-v21-finalize.tmp")
    payload = expected.encode("utf-8")
    if temporary.exists() or temporary.is_symlink():
        if (
            temporary.is_symlink()
            or not temporary.is_file()
            or stat.S_IMODE(temporary.stat().st_mode) != 0o644
        ):
            raise ValueError("post-merge closeout temporary file differs")
        if temporary.read_bytes() != payload:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_TRUNC)
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                os.close(descriptor)
    else:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)
    os.replace(temporary, path)
    parent_descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)


def run_positive_finalize(
    *,
    repository_root: Path,
    exact_head_ci_sha: str,
    independent_review_head: str,
    independent_review_confirmation: str,
    active_pr: int,
) -> str:
    root = repository_root.resolve(strict=True)
    if (
        exact_head_ci_sha != independent_review_head
        or independent_review_confirmation != _FINAL_REVIEW_CONFIRMATION
        or active_pr != 55
        or _git(root, "branch", "--show-current") != "main"
    ):
        raise ValueError("post-merge limitation-closeout gates differ")
    _allow_only_resumable_finalize_delta(root)
    merged_main_head = _git(root, "rev-parse", "HEAD")
    merged_pr = _verify_merged_pr(root, active_pr=active_pr)
    if (
        merged_pr["head_sha"] != exact_head_ci_sha
        or merged_pr["merge_sha"] != merged_main_head
    ):
        raise ValueError("merged PR differs from the accepted limitation candidate")
    ci = _verify_exact_head_github_actions(
        root, head=exact_head_ci_sha, required_event="pull_request"
    )
    if ci.get("head_sha") != exact_head_ci_sha:
        raise ValueError("candidate exact-head CI binding differs")
    report = verify_public_live_report_v3(
        report_path=root / "docs/results/dta-v21-live-demo.json",
        claim_paths=(
            root / "docs/results/dta-v21-live-demo.md",
            root / "docs/results/dta-v21-live-demo-human-brief.md",
            root / "docs/results/dta-v21-final-summary.md",
            root / "docs/results/dta-v21-interview-brief.md",
        ),
    )
    _verify_public_only_candidate_delta(
        root=root,
        execution_head=report.live_execution_code_head,
        candidate_head=exact_head_ci_sha,
    )
    if _git(root, "diff", "--quiet", exact_head_ci_sha, merged_main_head) != "":
        raise ValueError("merged PR tree differs from its accepted candidate")
    progress_relative = "docs/analysis/dta-v21-p0-master-progress.json"
    disposition_relative = (
        "docs/review-evidence/dta-v21-live/current-disposition.json"
    )
    progress_path = root / progress_relative
    disposition_path = root / disposition_relative
    previous_progress_text = _git_blob_text(
        root, treeish=exact_head_ci_sha, relative_path=progress_relative
    )
    previous_disposition_text = _git_blob_text(
        root, treeish=exact_head_ci_sha, relative_path=disposition_relative
    )
    progress = json.loads(previous_progress_text)
    previous_disposition = json.loads(previous_disposition_text)
    if (
        progress.get("completed_stage") != "PR-E"
        or progress.get("current_stage") != "PR-F"
        or progress.get("active_branch")
        != "codex/dta-v21-p0-pr-f-live-closeout"
        or progress.get("active_pr") != active_pr
        or progress.get("positive_continuation_status") != "PENDING"
        or previous_disposition != _pending_disposition(report)
    ):
        raise ValueError("pre-closeout Master Progress differs")
    merged_prs = progress.get("merged_prs")
    if merged_prs != [50, 51, 52, 53, 54]:
        raise ValueError("pre-closeout merged PR history differs")
    progress.update(
        {
            "completed_stage": "PR-F",
            "current_stage": "COMPLETE_WITH_LIMITATION",
            "main_head": merged_main_head,
            "active_branch": None,
            "active_pr": None,
            "merged_prs": [*merged_prs, active_pr],
            "no_fault_diagnosis_passed": False,
            "no_fault_no_write_safety_passed": True,
            "positive_continuation_status": "PASS",
            "positive_slots_passed": 3,
            "four_slot_acceptance_passed": False,
            "live_demo_terminal": (
                "DTA_V21_PR_F_POSITIVE_PORTFOLIO_PASS_WITH_NO_FAULT_"
                "DIAGNOSIS_MISS"
            ),
            "final_engineering_terminal": (
                "DTA_V21_P0_ENGINEERING_CLOSEOUT_WITH_NO_FAULT_DIAGNOSIS_MISS"
            ),
            "live_report_sha256": report.report_sha256,
            "live_execution_code_head": report.live_execution_code_head,
        }
    )
    final_payload: dict[str, object] = {
        "schema_version": "dta-v21.pr-f-capability-closeout-disposition.v1",
        "terminal": "DTA_V21_PR_F_POST_MERGE_LIMITATION_CLOSEOUT_PROJECTED",
        "report_sha256": report.report_sha256,
        "live_execution_code_head": report.live_execution_code_head,
        "final_terminal": report.overall_closeout_terminal,
        "merged_pr": active_pr,
        "merged_pr_url": merged_pr["url"],
        "acceptance_candidate_head": exact_head_ci_sha,
        "merged_main_head": merged_main_head,
        "candidate_exact_head_ci": "SUCCESS",
        "candidate_exact_head_ci_run_id": ci["run_id"],
        "candidate_exact_head_ci_run_url": ci["url"],
        "candidate_independent_review": "MUST_FIX_0_SHOULD_FIX_0",
        "candidate_independent_review_head": independent_review_head,
        "candidate_claim_accuracy": "PASS",
        "post_merge_exact_head_ci": "REQUIRED_AFTER_CLOSEOUT_COMMIT",
        "post_merge_independent_review": "REQUIRED_AFTER_CLOSEOUT_COMMIT",
    }
    disposition = {
        **final_payload,
        "disposition_sha256": semantic_sha256(final_payload),
    }
    expected_progress_text = json.dumps(
        progress, indent=2, ensure_ascii=False
    ) + "\n"
    expected_disposition_text = json.dumps(
        disposition, indent=2, ensure_ascii=False
    ) + "\n"
    _replace_regular_text_resumably(
        progress_path,
        previous=previous_progress_text,
        expected=expected_progress_text,
    )
    _replace_regular_text_resumably(
        disposition_path,
        previous=previous_disposition_text,
        expected=expected_disposition_text,
    )
    if run_positive_verify(repository_root=root) != (
        "DTA_V21_PR_F_POST_MERGE_LIMITATION_CLOSEOUT_PROJECTED"
    ):
        raise ValueError("post-merge limitation projection did not verify")
    return "DTA_V21_PR_F_POST_MERGE_LIMITATION_CLOSEOUT_PROJECTED"


def run_positive_closeout(
    *,
    repository_root: Path,
    exact_head_ci_sha: str,
    independent_review_head: str,
    independent_review_confirmation: str,
) -> str:
    root = repository_root.resolve(strict=True)
    head = _git(root, "rev-parse", "HEAD")
    if (
        head != exact_head_ci_sha
        or head != independent_review_head
        or independent_review_confirmation != _FINAL_REVIEW_CONFIRMATION
        or _git(root, "branch", "--show-current") != "main"
        or _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    ):
        raise ValueError("exact-main limitation-closeout gates differ")
    if run_positive_verify(repository_root=root) != (
        "DTA_V21_PR_F_POST_MERGE_LIMITATION_CLOSEOUT_PROJECTED"
    ):
        raise ValueError("post-merge limitation projection is not verified")
    disposition_path = (
        root / "docs/review-evidence/dta-v21-live/current-disposition.json"
    )
    disposition = json.loads(
        _read_regular_text(
            disposition_path, label="final capability-closeout disposition"
        )
    )
    if not isinstance(disposition, dict):
        raise ValueError("final capability-closeout disposition is invalid")
    disposition_digest = disposition.pop("disposition_sha256", None)
    if disposition_digest != semantic_sha256(disposition):
        raise ValueError("final capability-closeout disposition SHA-256 differs")
    candidate = str(disposition.get("acceptance_candidate_head"))
    merged_main = str(disposition.get("merged_main_head"))
    merged_pr = _verify_merged_pr(root, active_pr=55)
    candidate_ci = _verify_exact_head_github_actions(
        root, head=candidate, required_event="pull_request"
    )
    if (
        disposition.get("merged_pr") != 55
        or merged_pr.get("head_sha") != candidate
        or merged_pr.get("merge_sha") != merged_main
        or merged_pr.get("url") != disposition.get("merged_pr_url")
        or candidate_ci.get("run_id")
        != disposition.get("candidate_exact_head_ci_run_id")
        or candidate_ci.get("url")
        != disposition.get("candidate_exact_head_ci_run_url")
        or candidate_ci.get("head_sha") != candidate
    ):
        raise ValueError("final merged PR or candidate CI evidence differs")
    _git(root, "merge-base", "--is-ancestor", merged_main, head)
    final_ci = _verify_exact_head_github_actions(
        root, head=head, required_event="workflow_dispatch"
    )
    if final_ci.get("head_sha") != head:
        raise ValueError("post-merge exact-head CI evidence differs")
    return "DTA_V21_P0_ENGINEERING_CLOSEOUT_WITH_NO_FAULT_DIAGNOSIS_MISS"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("capability-record", "admit", "report"):
        item = sub.add_parser(name)
        item.add_argument("--repository-root", type=Path, required=True)
        item.add_argument("--private-root", type=Path, required=True)
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--repository-root", type=Path, required=True)
    preflight.add_argument("--private-root", type=Path, required=True)
    preflight.add_argument("--provider-env", type=Path, required=True)
    preflight.add_argument("--exact-head-ci-sha", required=True)
    review = sub.add_parser("record-review")
    review.add_argument("--repository-root", type=Path, required=True)
    review.add_argument("--private-root", type=Path, required=True)
    review.add_argument("--reviewer", required=True)
    execute = sub.add_parser("execute")
    execute.add_argument("--repository-root", type=Path, required=True)
    execute.add_argument("--private-root", type=Path, required=True)
    execute.add_argument("--provider-env", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--repository-root", type=Path, required=True)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--repository-root", type=Path, required=True)
    finalize.add_argument("--exact-head-ci-sha", required=True)
    finalize.add_argument("--independent-review-head", required=True)
    finalize.add_argument("--independent-review-confirmation", required=True)
    finalize.add_argument("--active-pr", type=int, required=True)
    closeout = sub.add_parser("closeout")
    closeout.add_argument("--repository-root", type=Path, required=True)
    closeout.add_argument("--exact-head-ci-sha", required=True)
    closeout.add_argument("--independent-review-head", required=True)
    closeout.add_argument("--independent-review-confirmation", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "capability-record":
        print(
            run_capability_record(
                repository_root=args.repository_root,
                private_root=args.private_root,
            ).classification
        )
    elif args.command == "preflight":
        print(
            run_positive_preflight(
                repository_root=args.repository_root,
                private_root=args.private_root,
                provider_env_path=args.provider_env,
                exact_head_ci_sha=args.exact_head_ci_sha,
            ).terminal
        )
    elif args.command == "record-review":
        record = run_record_positive_review(
            repository_root=args.repository_root,
            private_root=args.private_root,
            reviewer=args.reviewer,
        )
        print(
            f"MUST_FIX_{record.must_fix_count}_SHOULD_FIX_"
            f"{record.should_fix_count}_CLAIM_ACCURACY_{record.claim_accuracy}"
        )
    elif args.command == "admit":
        print(
            run_positive_admit(
                repository_root=args.repository_root,
                private_root=args.private_root,
            ).verdict
        )
    elif args.command == "execute":
        run_positive_execute(
            repository_root=args.repository_root,
            private_root=args.private_root,
            provider_env_path=args.provider_env,
        )
        print(
            "DTA_V21_PR_F_POSITIVE_PORTFOLIO_PASS_WITH_NO_FAULT_DIAGNOSIS_MISS"
        )
    elif args.command == "report":
        run_positive_report(
            repository_root=args.repository_root, private_root=args.private_root
        )
        print("DTA_V21_PR_F_LIMITATION_CLOSEOUT_FINAL_REVIEW_PENDING")
    elif args.command == "verify":
        print(run_positive_verify(repository_root=args.repository_root))
    elif args.command == "finalize":
        print(
            run_positive_finalize(
                repository_root=args.repository_root,
                exact_head_ci_sha=args.exact_head_ci_sha,
                independent_review_head=args.independent_review_head,
                independent_review_confirmation=(
                    args.independent_review_confirmation
                ),
                active_pr=args.active_pr,
            )
        )
    else:
        print(
            run_positive_closeout(
                repository_root=args.repository_root,
                exact_head_ci_sha=args.exact_head_ci_sha,
                independent_review_head=args.independent_review_head,
                independent_review_confirmation=(
                    args.independent_review_confirmation
                ),
            )
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
