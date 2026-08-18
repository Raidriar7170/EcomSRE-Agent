"""Offline-only CLI for the final DTA v2.1 PR-F capability closeout."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Sequence

from ecomsre.dta_v2.v21.contracts import semantic_sha256
from ecomsre.dta_v2.v21.live_cli import _write_public_once
from ecomsre.dta_v2.v21.live_capability_cli import (
    _FINAL_REVIEW_CONFIRMATION,
    _OPEN_PROGRESS_KEYS_V3,
    _OPEN_PROGRESS_REQUIRED_V3,
    _allow_only_resumable_finalize_delta,
    _parse_open_progress_v3,
    _replace_regular_text_resumably,
)
from ecomsre.dta_v2.v21.live_cli import (
    _verify_exact_head_github_actions,
    _verify_merged_pr,
)
from ecomsre.dta_v2.v21.live_final_closeout import (
    assert_prf_live_execution_open_v1,
    verify_final_capability_closeout_v1,
    write_final_capability_closeout_v1,
)
from ecomsre.dta_v2.v21.live_final_reporting import (
    PublicLiveCapabilityCloseoutReportV4,
    build_public_live_capability_closeout_report_v4,
    render_public_final_summary_v4,
    render_public_human_brief_v4,
    render_public_interview_brief_v4,
    render_public_live_markdown_v4,
    render_public_readme_block_v4,
    verify_public_text_v4,
)
from ecomsre_live_sandbox.environment import ExactCommandRunner


_COMMAND_RUNNER = ExactCommandRunner()
_README_MARKER = "<!-- dta-v21-pr-f-final-capability-closeout -->"
_PUBLIC_PATHS = frozenset(
    {
        "README.md",
        "docs/analysis/dta-v21-p0-master-progress.json",
        "docs/results/dta-v21-live-capability-closeout.json",
        "docs/results/dta-v21-live-capability-closeout.md",
        "docs/results/dta-v21-final-summary.md",
        "docs/results/dta-v21-interview-brief.md",
        "docs/results/dta-v21-live-demo-human-brief.md",
        "docs/review-evidence/dta-v21-live/current-disposition.json",
    }
)
_GENERATED_PATHS = _PUBLIC_PATHS - {
    "README.md",
    "docs/analysis/dta-v21-p0-master-progress.json",
}
_V4_PROGRESS_ADDED_KEYS = frozenset(
    {
        "ad_cpu_agent_terminal",
        "ad_cpu_agent_failure_code",
        "ad_cpu_recovery_tested",
        "positive_slots_attempted",
        "email_slot_status",
        "product_catalog_slot_status",
        "agent_forward_writes_observed",
        "remaining_live_execution_authority",
        "live_slots_attempted",
        "live_slots_passed",
        "capability_closeout_report_sha256",
        "private_capability_closeout_sha256",
        "capability_closeout_source_code_head",
        "capability_closeout_candidate_scope_sha256",
    }
)
_V4_OPEN_PROGRESS_KEYS = _OPEN_PROGRESS_KEYS_V3 | _V4_PROGRESS_ADDED_KEYS
_FINAL_PROJECTED_TERMINAL = (
    "DTA_V21_PR_F_POST_MERGE_FINAL_CAPABILITY_CLOSEOUT_PROJECTED"
)


def _git(root: Path, *arguments: str) -> str:
    try:
        result = _COMMAND_RUNNER.run(
            ("git", *arguments), cwd=root, timeout_seconds=30
        )
    except RuntimeError as error:
        raise ValueError("required final-closeout Git verification failed") from error
    return result.stdout.strip()


def _git_blob_text(root: Path, *, treeish: str, relative: str) -> str:
    try:
        result = _COMMAND_RUNNER.run(
            ("git", "show", f"{treeish}:{relative}"),
            cwd=root,
            timeout_seconds=30,
        )
    except RuntimeError as error:
        raise ValueError("bound final-closeout source is unavailable") from error
    return result.stdout


def _candidate_scope_sha256(root: Path, *, treeish: str) -> str:
    try:
        result = _COMMAND_RUNNER.run(
            ("git", "ls-tree", "-r", "--full-tree", treeish),
            cwd=root,
            timeout_seconds=30,
        )
    except RuntimeError as error:
        raise ValueError("final-closeout Git tree is unavailable") from error
    entries: list[tuple[str, str, str, str]] = []
    for line in result.stdout.splitlines():
        try:
            metadata, path = line.split("\t", 1)
            mode, object_type, object_sha = metadata.split(" ", 2)
        except ValueError as error:
            raise ValueError("final-closeout Git tree is invalid") from error
        if path in _PUBLIC_PATHS:
            continue
        entries.append((mode, object_type, path, object_sha))
    if not entries:
        raise ValueError("final-closeout Git scope is empty")
    return semantic_sha256(tuple(entries))


def _verify_exact_head_workflow(
    root: Path, *, head: str, workflow: str, required_event: str
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
                workflow,
                "--json",
                "databaseId,headSha,status,conclusion,url,event",
                "--limit",
                "20",
            ),
            cwd=root,
            timeout_seconds=30,
        )
        value = json.loads(result.stdout)
    except (RuntimeError, json.JSONDecodeError) as error:
        raise ValueError("exact-head closeout workflow evidence is unavailable") from error
    if not isinstance(value, list):
        raise ValueError("exact-head closeout workflow response is invalid")
    successful = tuple(
        item
        for item in value
        if isinstance(item, dict)
        and item.get("headSha") == head
        and item.get("status") == "completed"
        and item.get("conclusion") == "success"
        and item.get("event") == required_event
        and isinstance(item.get("databaseId"), int)
        and not isinstance(item.get("databaseId"), bool)
        and isinstance(item.get("url"), str)
        and str(item["url"]).startswith("https://github.com/")
    )
    if not successful:
        raise ValueError(f"exact-head {workflow} has no successful run")
    selected = max(successful, key=lambda item: int(item["databaseId"]))
    return {
        "run_id": selected["databaseId"],
        "head_sha": selected["headSha"],
        "conclusion": "SUCCESS",
        "url": selected["url"],
    }


def _read_regular(path: Path, *, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or unsafe")
    if path.stat().st_mode & 0o777 != 0o644:
        raise ValueError(f"{label} has an unsafe mode")
    return path.read_text(encoding="utf-8")


def _allow_only_report_delta(root: Path) -> None:
    try:
        status = _COMMAND_RUNNER.run(
            ("git", "status", "--porcelain=v1", "--untracked-files=all"),
            cwd=root,
            timeout_seconds=30,
        ).stdout
    except RuntimeError as error:
        raise ValueError("final report Git status is unavailable") from error
    for record in status.splitlines():
        if " -> " in record or len(record) < 4 or record[3:] not in _PUBLIC_PATHS:
            raise ValueError("final report worktree contains an unrelated change")


def _replace_exact(path: Path, *, previous: str, expected: str) -> None:
    current = _read_regular(path, label=path.name)
    if current == expected:
        return
    if current != previous:
        raise ValueError(f"{path.name} differs from the resumable report boundary")
    path.write_text(expected, encoding="utf-8")


def _render_readme(*, base: str, report: PublicLiveCapabilityCloseoutReportV4) -> str:
    anchor = "## One-command offline demo"
    if _README_MARKER in base or base.count(anchor) != 1:
        raise ValueError("README final-closeout insertion boundary differs")
    return base.replace(anchor, render_public_readme_block_v4(report) + "\n" + anchor, 1)


def _open_progress(
    *, base_text: str, report: PublicLiveCapabilityCloseoutReportV4
) -> str:
    value = _parse_open_progress_v3(base_text)
    value.update(
        {
            "active_amendment_version": (
                "dta-v21-p0-prf-final-capability-closeout-v1"
            ),
            "active_amendment_sha256": report.amendment_sha256,
            "active_decision_id": "DEC-047",
            "positive_continuation_status": "CONSUMED_FAILED",
            "ad_cpu_agent_terminal": "FAILED",
            "ad_cpu_agent_failure_code": "DUPLICATE_READ_REQUEST",
            "ad_cpu_recovery_tested": False,
            "positive_slots_attempted": 1,
            "positive_slots_passed": 0,
            "email_slot_status": "NOT_ATTEMPTED",
            "product_catalog_slot_status": "NOT_ATTEMPTED",
            "agent_forward_writes_observed": 0,
            "remaining_live_execution_authority": 0,
            "live_slots_attempted": 2,
            "live_slots_passed": 0,
            "capability_closeout_report_sha256": report.report_sha256,
            "private_capability_closeout_sha256": (
                report.private_closeout_sha256
            ),
            "capability_closeout_source_code_head": (
                report.closeout_source_code_head
            ),
            "capability_closeout_candidate_scope_sha256": (
                report.candidate_scope_sha256
            ),
        }
    )
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _recover_base_progress(
    *, text: str, report: PublicLiveCapabilityCloseoutReportV4
) -> dict[str, object]:
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != _V4_OPEN_PROGRESS_KEYS:
        raise ValueError("open Master Progress fields differ")
    base = dict(value)
    for field in _V4_PROGRESS_ADDED_KEYS:
        base.pop(field)
    base.update(_OPEN_PROGRESS_REQUIRED_V3)
    base = _parse_open_progress_v3(
        json.dumps(base, indent=2, ensure_ascii=False) + "\n"
    )
    if semantic_sha256(base) != report.base_progress_semantic_sha256:
        raise ValueError("open Master Progress base digest differs")
    expected = _open_progress(
        base_text=json.dumps(base, indent=2, ensure_ascii=False) + "\n",
        report=report,
    )
    if text != expected:
        raise ValueError("open Master Progress differs from v4 report")
    return base


def _render_final_progress(
    *,
    open_progress_text: str,
    report: PublicLiveCapabilityCloseoutReportV4,
    merged_main_head: str,
) -> str:
    _recover_base_progress(text=open_progress_text, report=report)
    value = json.loads(open_progress_text)
    value.update(
        {
            "completed_stage": "PR-F",
            "current_stage": "COMPLETE_WITH_CAPABILITY_LIMITATIONS",
            "main_head": merged_main_head,
            "active_branch": None,
            "active_pr": None,
            "merged_prs": [50, 51, 52, 53, 54, 55],
            "final_engineering_terminal": report.terminal,
        }
    )
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _recover_final_progress(
    *,
    text: str,
    report: PublicLiveCapabilityCloseoutReportV4,
    merged_main_head: str,
) -> None:
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != _V4_OPEN_PROGRESS_KEYS:
        raise ValueError("final Master Progress fields differ")
    expected_final = {
        "completed_stage": "PR-F",
        "current_stage": "COMPLETE_WITH_CAPABILITY_LIMITATIONS",
        "main_head": merged_main_head,
        "active_branch": None,
        "active_pr": None,
        "merged_prs": [50, 51, 52, 53, 54, 55],
        "final_engineering_terminal": report.terminal,
        "live_demo_terminal": None,
    }
    if any(value.get(key) != expected for key, expected in expected_final.items()):
        raise ValueError("final Master Progress differs")
    open_value = dict(value)
    open_value.update(
        {
            "completed_stage": "PR-E",
            "current_stage": "PR-F",
            "main_head": _OPEN_PROGRESS_REQUIRED_V3["main_head"],
            "active_branch": _OPEN_PROGRESS_REQUIRED_V3["active_branch"],
            "active_pr": _OPEN_PROGRESS_REQUIRED_V3["active_pr"],
            "merged_prs": _OPEN_PROGRESS_REQUIRED_V3["merged_prs"],
            "final_engineering_terminal": None,
        }
    )
    open_text = json.dumps(open_value, indent=2, ensure_ascii=False) + "\n"
    _recover_base_progress(text=open_text, report=report)
    if text != _render_final_progress(
        open_progress_text=open_text,
        report=report,
        merged_main_head=merged_main_head,
    ):
        raise ValueError("final Master Progress transform differs")


def _pending_disposition(
    report: PublicLiveCapabilityCloseoutReportV4,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": (
            "dta-v21.pr-f-final-capability-closeout-disposition.v1"
        ),
        "terminal": "DTA_V21_PR_F_FINAL_CAPABILITY_CLOSEOUT_REVIEW_PENDING",
        "final_terminal": report.terminal,
        "report_sha256": report.report_sha256,
        "private_closeout_sha256": report.private_closeout_sha256,
        "closeout_source_code_head": report.closeout_source_code_head,
        "candidate_scope_sha256": report.candidate_scope_sha256,
        "exact_head_ci": "PENDING",
        "independent_review": "PENDING",
        "claim_accuracy": "PENDING",
    }
    return {**payload, "disposition_sha256": semantic_sha256(payload)}


def _final_disposition(
    *,
    report: PublicLiveCapabilityCloseoutReportV4,
    merged_main_head: str,
    merged_pr_url: str,
    acceptance_candidate_head: str,
    agent_ci: dict[str, object],
    rcaeval_ci: dict[str, object],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": (
            "dta-v21.pr-f-final-capability-closeout-disposition.v1"
        ),
        "terminal": _FINAL_PROJECTED_TERMINAL,
        "final_terminal": report.terminal,
        "report_sha256": report.report_sha256,
        "private_closeout_sha256": report.private_closeout_sha256,
        "closeout_source_code_head": report.closeout_source_code_head,
        "candidate_scope_sha256": report.candidate_scope_sha256,
        "merged_pr": 55,
        "merged_pr_url": merged_pr_url,
        "acceptance_candidate_head": acceptance_candidate_head,
        "merged_main_head": merged_main_head,
        "candidate_exact_head_ci": "SUCCESS",
        "candidate_agent_mainline_run_id": agent_ci["run_id"],
        "candidate_agent_mainline_run_url": agent_ci["url"],
        "candidate_rcaeval_run_id": rcaeval_ci["run_id"],
        "candidate_rcaeval_run_url": rcaeval_ci["url"],
        "candidate_independent_review": "MUST_FIX_0_SHOULD_FIX_0",
        "candidate_independent_review_head": acceptance_candidate_head,
        "candidate_claim_accuracy": "PASS",
        "post_merge_metadata_update": "REQUIRED",
    }
    return {**payload, "disposition_sha256": semantic_sha256(payload)}


def run_final_record(*, repository_root: Path, private_root: Path) -> str:
    record = write_final_capability_closeout_v1(
        repository_root=repository_root, private_root=private_root
    )
    return record.terminal


def run_final_report(
    *, repository_root: Path, private_root: Path
) -> PublicLiveCapabilityCloseoutReportV4:
    root = Path(repository_root).resolve(strict=True)
    private = Path(private_root).resolve(strict=True)
    verify_final_capability_closeout_v1(
        repository_root=root, private_root=private
    )
    _allow_only_report_delta(root)
    head = _git(root, "rev-parse", "HEAD")
    base_readme = _git_blob_text(root, treeish=head, relative="README.md")
    base_progress = _git_blob_text(
        root,
        treeish=head,
        relative="docs/analysis/dta-v21-p0-master-progress.json",
    )
    progress_value = json.loads(base_progress)
    if not isinstance(progress_value, dict):
        raise ValueError("base Master Progress is invalid")
    report = build_public_live_capability_closeout_report_v4(
        repository_root=root,
        private_root=private,
        closeout_source_code_head=head,
        candidate_scope_sha256=_candidate_scope_sha256(root, treeish=head),
        base_readme_sha256=hashlib.sha256(base_readme.encode()).hexdigest(),
        base_progress_raw_sha256=hashlib.sha256(base_progress.encode()).hexdigest(),
        base_progress_semantic_sha256=semantic_sha256(progress_value),
    )
    results = root / "docs/results"
    review = root / "docs/review-evidence/dta-v21-live"
    _write_public_once(
        results / "dta-v21-live-capability-closeout.json",
        report.model_dump_json(indent=2) + "\n",
    )
    _write_public_once(
        results / "dta-v21-live-capability-closeout.md",
        render_public_live_markdown_v4(report),
    )
    _write_public_once(
        results / "dta-v21-final-summary.md",
        render_public_final_summary_v4(report),
    )
    _write_public_once(
        results / "dta-v21-interview-brief.md",
        render_public_interview_brief_v4(report),
    )
    _write_public_once(
        results / "dta-v21-live-demo-human-brief.md",
        render_public_human_brief_v4(report),
    )
    _write_public_once(
        review / "current-disposition.json",
        json.dumps(_pending_disposition(report), indent=2, ensure_ascii=False) + "\n",
    )
    _replace_exact(
        root / "README.md",
        previous=base_readme,
        expected=_render_readme(base=base_readme, report=report),
    )
    _replace_exact(
        root / "docs/analysis/dta-v21-p0-master-progress.json",
        previous=base_progress,
        expected=_open_progress(base_text=base_progress, report=report),
    )
    return report


def _verify_report_file_set(root: Path) -> tuple[Path, tuple[Path, ...], Path, Path, Path]:
    report = root / "docs/results/dta-v21-live-capability-closeout.json"
    claims = (
        root / "docs/results/dta-v21-live-capability-closeout.md",
        root / "docs/results/dta-v21-final-summary.md",
        root / "docs/results/dta-v21-interview-brief.md",
        root / "docs/results/dta-v21-live-demo-human-brief.md",
    )
    disposition = root / "docs/review-evidence/dta-v21-live/current-disposition.json"
    readme = root / "README.md"
    progress = root / "docs/analysis/dta-v21-p0-master-progress.json"
    all_paths = (report, *claims, disposition, readme, progress)
    if any(path.is_symlink() or not path.is_file() for path in all_paths):
        raise ValueError("public v4 closeout file set is missing or unsafe")
    return report, claims, disposition, readme, progress


def _recover_base_readme(
    *, current: str, report: PublicLiveCapabilityCloseoutReportV4
) -> str:
    block = render_public_readme_block_v4(report) + "\n"
    if current.count(block) != 1 or current.count(_README_MARKER) != 1:
        raise ValueError("README v4 projection differs")
    base = current.replace(block, "", 1)
    if hashlib.sha256(base.encode()).hexdigest() != report.base_readme_sha256:
        raise ValueError("README base digest differs")
    return base


def _verify_readme_projection(
    *, current: str, report: PublicLiveCapabilityCloseoutReportV4
) -> None:
    _recover_base_readme(current=current, report=report)
    verify_public_text_v4(render_public_readme_block_v4(report))


def _verify_open_progress(
    *, text: str, report: PublicLiveCapabilityCloseoutReportV4
) -> None:
    _recover_base_progress(text=text, report=report)


def _read_disposition(path: Path) -> dict[str, object]:
    value = json.loads(_read_regular(path, label="disposition"))
    if not isinstance(value, dict):
        raise ValueError("v4 disposition is invalid")
    digest = value.pop("disposition_sha256", None)
    if digest != semantic_sha256(value):
        raise ValueError("v4 disposition SHA-256 differs")
    return value


def _verify_final_disposition(
    *,
    value: dict[str, object],
    report: PublicLiveCapabilityCloseoutReportV4,
) -> tuple[str, str, int, str, int, str]:
    merged_main_head = value.get("merged_main_head")
    acceptance_candidate_head = value.get("acceptance_candidate_head")
    merged_pr_url = value.get("merged_pr_url")
    agent_run_id = value.get("candidate_agent_mainline_run_id")
    agent_run_url = value.get("candidate_agent_mainline_run_url")
    rcaeval_run_id = value.get("candidate_rcaeval_run_id")
    rcaeval_run_url = value.get("candidate_rcaeval_run_url")
    if (
        not isinstance(merged_main_head, str)
        or re.fullmatch(r"[0-9a-f]{40}", merged_main_head) is None
        or not isinstance(acceptance_candidate_head, str)
        or re.fullmatch(r"[0-9a-f]{40}", acceptance_candidate_head) is None
        or merged_pr_url != "https://github.com/Raidriar7170/EcomSRE-Agent/pull/55"
        or not isinstance(agent_run_id, int)
        or isinstance(agent_run_id, bool)
        or agent_run_id < 1
        or not isinstance(rcaeval_run_id, int)
        or isinstance(rcaeval_run_id, bool)
        or rcaeval_run_id < 1
        or not isinstance(agent_run_url, str)
        or re.fullmatch(
            r"https://github\.com/.+/actions/runs/[0-9]+", agent_run_url
        )
        is None
        or not isinstance(rcaeval_run_url, str)
        or re.fullmatch(
            r"https://github\.com/.+/actions/runs/[0-9]+", rcaeval_run_url
        )
        is None
    ):
        raise ValueError("final v4 disposition evidence shape differs")
    expected = _final_disposition(
        report=report,
        merged_main_head=merged_main_head,
        merged_pr_url=merged_pr_url,
        acceptance_candidate_head=acceptance_candidate_head,
        agent_ci={"run_id": agent_run_id, "url": agent_run_url},
        rcaeval_ci={"run_id": rcaeval_run_id, "url": rcaeval_run_url},
    )
    expected.pop("disposition_sha256")
    if value != expected:
        raise ValueError("final v4 disposition differs")
    return (
        merged_main_head,
        acceptance_candidate_head,
        agent_run_id,
        agent_run_url,
        rcaeval_run_id,
        rcaeval_run_url,
    )


def _load_verified_public_projection(
    root: Path,
) -> tuple[
    PublicLiveCapabilityCloseoutReportV4,
    str,
    dict[str, object],
    Path,
    Path,
]:
    report_path, claims, disposition_path, readme_path, progress_path = (
        _verify_report_file_set(root)
    )
    report = PublicLiveCapabilityCloseoutReportV4.model_validate_json(
        _read_regular(report_path, label="v4 closeout report")
    )
    expected_claims = (
        render_public_live_markdown_v4(report),
        render_public_final_summary_v4(report),
        render_public_interview_brief_v4(report),
        render_public_human_brief_v4(report),
    )
    for path, expected in zip(claims, expected_claims, strict=True):
        actual = _read_regular(path, label=path.name)
        if actual != expected:
            raise ValueError(f"public v4 claim differs: {path.name}")
        verify_public_text_v4(actual)
    readme = _read_regular(readme_path, label="README")
    _verify_readme_projection(current=readme, report=report)
    if _candidate_scope_sha256(root, treeish="HEAD") != report.candidate_scope_sha256:
        raise ValueError("candidate non-public source scope differs")
    progress_text = _read_regular(progress_path, label="Master Progress")
    disposition = _read_disposition(disposition_path)
    return report, progress_text, disposition, disposition_path, progress_path


def _verify_finalize_state(
    *,
    progress_text: str,
    disposition: dict[str, object],
    report: PublicLiveCapabilityCloseoutReportV4,
    merged_main_head: str,
) -> str:
    expected_disposition = _pending_disposition(report)
    expected_disposition.pop("disposition_sha256")
    if disposition == expected_disposition:
        try:
            _verify_open_progress(text=progress_text, report=report)
        except ValueError:
            try:
                _recover_final_progress(
                    text=progress_text,
                    report=report,
                    merged_main_head=merged_main_head,
                )
            except ValueError as final_error:
                raise ValueError(
                    "v4 finalization progress/disposition state differs"
                ) from final_error
            return "FINAL_PROGRESS_PENDING_DISPOSITION"
        return "OPEN_PROGRESS_PENDING_DISPOSITION"
    disposition_merged_head, *_ = _verify_final_disposition(
        value=disposition, report=report
    )
    if disposition_merged_head != merged_main_head:
        raise ValueError("v4 finalization merged-main binding differs")
    _recover_final_progress(
        text=progress_text,
        report=report,
        merged_main_head=merged_main_head,
    )
    return "FINAL_PROGRESS_FINAL_DISPOSITION"


def run_final_verify(*, repository_root: Path) -> str:
    root = Path(repository_root).resolve(strict=True)
    report, progress_text, disposition, _disposition_path, _progress_path = (
        _load_verified_public_projection(root)
    )
    expected_disposition = _pending_disposition(report)
    expected_disposition.pop("disposition_sha256")
    if disposition == expected_disposition:
        _verify_open_progress(text=progress_text, report=report)
        _allow_only_report_delta(root)
        return "DTA_V21_PR_F_FINAL_CAPABILITY_CLOSEOUT_REVIEW_PENDING"
    merged_main_head, *_ = _verify_final_disposition(
        value=disposition, report=report
    )
    _recover_final_progress(
        text=progress_text,
        report=report,
        merged_main_head=merged_main_head,
    )
    _allow_only_report_delta(root)
    return _FINAL_PROJECTED_TERMINAL


def run_final_finalize(
    *,
    repository_root: Path,
    exact_head_ci_sha: str,
    independent_review_head: str,
    independent_review_confirmation: str,
    active_pr: int,
) -> str:
    root = Path(repository_root).resolve(strict=True)
    if (
        exact_head_ci_sha != independent_review_head
        or independent_review_confirmation != _FINAL_REVIEW_CONFIRMATION
        or active_pr != 55
        or _git(root, "branch", "--show-current") != "main"
    ):
        raise ValueError("final capability-closeout acceptance gates differ")
    _allow_only_resumable_finalize_delta(root)
    merged_main_head = _git(root, "rev-parse", "HEAD")
    merged_pr = _verify_merged_pr(root, active_pr=active_pr)
    if (
        merged_pr["head_sha"] != exact_head_ci_sha
        or merged_pr["merge_sha"] != merged_main_head
    ):
        raise ValueError("merged PR differs from the accepted v4 candidate")
    agent_ci = _verify_exact_head_workflow(
        root,
        head=exact_head_ci_sha,
        workflow="agent-mainline.yml",
        required_event="pull_request",
    )
    rcaeval_ci = _verify_exact_head_workflow(
        root,
        head=exact_head_ci_sha,
        workflow="rcaeval-v2-dev.yml",
        required_event="pull_request",
    )
    (
        report,
        previous_progress,
        previous_disposition_value,
        disposition_path,
        progress_path,
    ) = _load_verified_public_projection(root)
    state = _verify_finalize_state(
        progress_text=previous_progress,
        disposition=previous_disposition_value,
        report=report,
        merged_main_head=merged_main_head,
    )
    if _candidate_scope_sha256(root, treeish="HEAD") != report.candidate_scope_sha256:
        raise ValueError("accepted candidate non-public source scope differs")
    previous_disposition = _read_regular(disposition_path, label="disposition")
    if state == "OPEN_PROGRESS_PENDING_DISPOSITION":
        expected_progress = _render_final_progress(
            open_progress_text=previous_progress,
            report=report,
            merged_main_head=merged_main_head,
        )
    else:
        _recover_final_progress(
            text=previous_progress,
            report=report,
            merged_main_head=merged_main_head,
        )
        expected_progress = previous_progress
    expected_disposition = json.dumps(
        _final_disposition(
            report=report,
            merged_main_head=merged_main_head,
            merged_pr_url=merged_pr["url"],
            acceptance_candidate_head=exact_head_ci_sha,
            agent_ci=agent_ci,
            rcaeval_ci=rcaeval_ci,
        ),
        indent=2,
        ensure_ascii=False,
    ) + "\n"
    pending_disposition = json.dumps(
        _pending_disposition(report), indent=2, ensure_ascii=False
    ) + "\n"
    if previous_disposition not in {pending_disposition, expected_disposition}:
        raise ValueError("pre-final v4 disposition differs")
    _replace_regular_text_resumably(
        progress_path,
        previous=previous_progress,
        expected=expected_progress,
    )
    _replace_regular_text_resumably(
        disposition_path,
        previous=pending_disposition,
        expected=expected_disposition,
    )
    if run_final_verify(repository_root=root) != _FINAL_PROJECTED_TERMINAL:
        raise ValueError("post-merge v4 projection did not verify")
    return _FINAL_PROJECTED_TERMINAL


def run_final_closeout(*, repository_root: Path, exact_main_ci_sha: str) -> str:
    root = Path(repository_root).resolve(strict=True)
    head = _git(root, "rev-parse", "HEAD")
    if (
        head != exact_main_ci_sha
        or _git(root, "branch", "--show-current") != "main"
        or _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    ):
        raise ValueError("exact-main final capability-closeout gates differ")
    if run_final_verify(repository_root=root) != _FINAL_PROJECTED_TERMINAL:
        raise ValueError("post-merge v4 projection is not verified")
    report_path, _claims, disposition_path, _readme, _progress = (
        _verify_report_file_set(root)
    )
    report = PublicLiveCapabilityCloseoutReportV4.model_validate_json(
        _read_regular(report_path, label="v4 closeout report")
    )
    disposition = _read_disposition(disposition_path)
    (
        merged_main_head,
        acceptance_candidate_head,
        agent_run_id,
        agent_run_url,
        rcaeval_run_id,
        rcaeval_run_url,
    ) = _verify_final_disposition(value=disposition, report=report)
    merged_pr = _verify_merged_pr(root, active_pr=55)
    if (
        merged_pr["head_sha"] != acceptance_candidate_head
        or merged_pr["merge_sha"] != merged_main_head
        or merged_pr["url"] != disposition.get("merged_pr_url")
    ):
        raise ValueError("final merged PR evidence differs")
    candidate_agent = _verify_exact_head_workflow(
        root,
        head=acceptance_candidate_head,
        workflow="agent-mainline.yml",
        required_event="pull_request",
    )
    candidate_rcaeval = _verify_exact_head_workflow(
        root,
        head=acceptance_candidate_head,
        workflow="rcaeval-v2-dev.yml",
        required_event="pull_request",
    )
    if (
        candidate_agent["run_id"] != agent_run_id
        or candidate_agent["url"] != agent_run_url
        or candidate_rcaeval["run_id"] != rcaeval_run_id
        or candidate_rcaeval["url"] != rcaeval_run_url
    ):
        raise ValueError("final candidate CI evidence differs")
    _git(root, "merge-base", "--is-ancestor", merged_main_head, head)
    final_ci = _verify_exact_head_github_actions(
        root, head=head, required_event="workflow_dispatch"
    )
    if final_ci["head_sha"] != head:
        raise ValueError("post-merge exact-main CI evidence differs")
    return report.terminal


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("record", "report"):
        item = sub.add_parser(name)
        item.add_argument("--repository-root", type=Path, required=True)
        item.add_argument("--private-root", type=Path, required=True)
    guard = sub.add_parser("guard-live-execution")
    guard.add_argument("--private-root", type=Path, required=True)
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
    closeout.add_argument("--exact-main-ci-sha", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "record":
            print(
                run_final_record(
                    repository_root=args.repository_root,
                    private_root=args.private_root,
                )
            )
        elif args.command == "report":
            run_final_report(
                repository_root=args.repository_root,
                private_root=args.private_root,
            )
            print("DTA_V21_PR_F_FINAL_CAPABILITY_CLOSEOUT_REVIEW_PENDING")
        elif args.command == "guard-live-execution":
            assert_prf_live_execution_open_v1(private_root=args.private_root)
            print("DTA_V21_PR_F_LIVE_EXECUTION_OPEN")
        elif args.command == "verify":
            print(run_final_verify(repository_root=args.repository_root))
        elif args.command == "finalize":
            print(
                run_final_finalize(
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
                run_final_closeout(
                    repository_root=args.repository_root,
                    exact_main_ci_sha=args.exact_main_ci_sha,
                )
            )
    except RuntimeError as error:
        print(str(error))
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
