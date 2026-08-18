"""Verify the DTA v2.1 PR-F Ad CPU resource-recovery protocol."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
from typing import Sequence

from ecomsre.dta_v2.v21.live_protocol import (
    AdCpuResourceRecoveryProtocolV1,
    load_ad_cpu_resource_recovery_protocol_v1,
    verify_accepted_ad_cpu_calibration_binding,
    verify_public_ad_cpu_claim_text,
)
from ecomsre.dta_v2.v21.contracts import semantic_sha256
from ecomsre.dta_v2.v21.live_capability_reporting import PublicLiveReportV3
from scripts.ci.verify_dta_v21_evaluation_freeze import verify_public_evaluation
from scripts.ci.verify_dta_v21_held_out import verify_public_held_out_report_v21
from scripts.ci.verify_dta_v2_historical_bindings import verify_historical_bindings


PROTOCOL_RELATIVE = Path("config/dta-v21/live/ad-cpu-resource-recovery.v1.json")
PROGRESS_RELATIVE = Path("docs/analysis/dta-v21-p0-master-progress.json")
DECISIONS_RELATIVE = Path("docs/DECISIONS.md")
HISTORICAL_BINDINGS_RELATIVE = Path("config/dta-v21/historical-v2-bindings.v1.json")
RECONCILIATION_SOURCE_RELATIVE = Path(
    "src/ecomsre/dta_v2/v21/live_reconciliation.py"
)
CAPABILITY_SOURCE_RELATIVE = Path(
    "src/ecomsre/dta_v2/v21/live_capability_closeout.py"
)
CAPABILITY_BASE_HEAD = "a167285a6a1d691709f229b26d167a7cd7c10fa0"


def _verify_pr_f_targets_do_not_reach_held_out_execution(makefile: str) -> None:
    lines: list[str] = []
    logical_pending = ""
    for physical in makefile.splitlines():
        if logical_pending:
            logical_pending += physical.lstrip()
        else:
            logical_pending = physical
        if logical_pending.rstrip().endswith("\\"):
            logical_pending = logical_pending.rstrip()[:-1] + " "
            continue
        lines.append(logical_pending)
        logical_pending = ""
    if logical_pending:
        lines.append(logical_pending)
    prerequisites: dict[str, set[str]] = {}
    recipes: dict[str, list[str]] = {}
    current_targets: tuple[str, ...] = ()
    for line in lines:
        target_match = re.match(r"^([^#\t ][^:=]*):(?:\s*)(.*)$", line)
        if target_match is not None:
            current_targets = tuple(target_match.group(1).split())
            raw_prerequisites = target_match.group(2).split("|", 1)[0].split()
            for target in current_targets:
                prerequisites.setdefault(target, set()).update(raw_prerequisites)
                recipes.setdefault(target, [])
            continue
        if line.startswith("\t") and current_targets:
            for target in current_targets:
                recipes[target].append(line)
        elif line and not line.startswith((" ", "#")):
            current_targets = ()

    roots = tuple(
        target
        for target in prerequisites
        if target.startswith(("dta-v21-pr-f-", "dta-v21-live-"))
        or target in {"dta-v21-demo", "dta-v21-verify"}
    )
    if not roots:
        raise ValueError("no PR-F Make target is defined")
    forbidden_target = re.compile(r"dta-v21-held-out-(?:execute|score)", re.I)
    forbidden_recipe = re.compile(
        r"(?:dta[_.-]v21[_.-]held[_.-]out(?:[_.-]cli)?|"
        r"held[_.-]out(?:[_.-]cli)?)"
        r"[^\n]*(?:execute|score)",
        re.I,
    )
    for root in roots:
        pending_targets = [root]
        visited: set[str] = set()
        while pending_targets:
            target = pending_targets.pop()
            if target in visited:
                continue
            visited.add(target)
            recipe = re.sub(r"\\\n[ \t]*", " ", "\n".join(recipes.get(target, ())))
            if forbidden_target.search(target) or forbidden_recipe.search(recipe):
                raise ValueError("a PR-F target reaches held-out execution or scoring")
            dependencies = prerequisites.get(target, ())
            if any(forbidden_target.search(item) for item in dependencies):
                raise ValueError("a PR-F target reaches held-out execution or scoring")
            pending_targets.extend(
                dependency for dependency in dependencies if dependency in prerequisites
            )


def verify_pr_f_protocol(
    project_root: Path, *, private_root: Path | None = None
) -> AdCpuResourceRecoveryProtocolV1:
    root = project_root.resolve(strict=True)
    protocol = load_ad_cpu_resource_recovery_protocol_v1(root / PROTOCOL_RELATIVE)

    verify_historical_bindings(root, root / HISTORICAL_BINDINGS_RELATIVE)
    verify_public_evaluation(root, require_freeze=True)

    progress_path = root / PROGRESS_RELATIVE
    if progress_path.is_symlink() or not progress_path.is_file():
        raise ValueError("DTA v2.1 master progress must be a regular file")
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    required_progress = {
        "active_amendment_version": "dta-v21-p0-prf-capability-closeout-v1",
        "active_amendment_sha256": (
            "24cc236c1892c9992b6d36da377608c34fb22c2bc270f99349e5e8a4e0a0498a"
        ),
        "active_decision_id": "DEC-046",
        "held_out_seal_sha256": (
            "9a7c8e56400e99c693c8bddc26007b1dd26e0dcee2167b07cf3fba00fd22fbd7"
        ),
        "held_out_execution_id": "53615cdd78b348b68496f64102c0b4de",
        "held_out_claim": ("DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED"),
        "ad_cpu_resource_recovery_protocol_sha256": protocol.protocol_sha256,
        "historical_blocked_attempt_id": (
            "dta-v21-prf-01-no-fault-422f015451fd"
        ),
        "historical_blocked_attempt_terminal": "BLOCKED_DTA_V21_PRF_SAFETY",
        "historical_blocked_attempt_baseline_restored": False,
        "historical_blocked_attempt_cleanup": "BLOCKED",
        "no_fault_capability_attempt_id": (
            "dta-v21-prf-01-no-fault-a167285a6a1d"
        ),
        "no_fault_capability_classification": (
            "NO_FAULT_FALSE_POSITIVE_DIAGNOSIS_SAFE_NO_ACTION"
        ),
        "no_fault_diagnosis_passed": False,
        "no_fault_no_write_safety_passed": True,
        "four_slot_acceptance_passed": False,
    }
    for field, expected in required_progress.items():
        if progress.get(field) != expected:
            raise ValueError(
                f"master progress field {field} differs from PR-F protocol"
            )
    stage = (progress.get("completed_stage"), progress.get("current_stage"))
    if stage == ("PR-E", "PR-F"):
        if (
            progress.get("live_demo_terminal") is not None
            or progress.get("final_engineering_terminal") is not None
            or progress.get("active_branch")
            != "codex/dta-v21-p0-pr-f-live-closeout"
            or progress.get("active_pr") != 55
            or progress.get("positive_continuation_status") != "PENDING"
            or progress.get("positive_slots_passed") != 0
        ):
            raise ValueError("open PR-F progress carries a final terminal")
    elif stage == ("PR-F", "COMPLETE_WITH_LIMITATION"):
        merged_prs = progress.get("merged_prs")
        report_path = root / "docs/results/dta-v21-live-demo.json"
        disposition_path = (
            root / "docs/review-evidence/dta-v21-live/current-disposition.json"
        )
        if (
            report_path.is_symlink()
            or not report_path.is_file()
            or disposition_path.is_symlink()
            or not disposition_path.is_file()
        ):
            raise ValueError("closed PR-F public evidence is missing or unsafe")
        report = PublicLiveReportV3.model_validate_json(
            report_path.read_text(encoding="utf-8")
        )
        disposition = json.loads(disposition_path.read_text(encoding="utf-8"))
        if not isinstance(disposition, dict):
            raise ValueError("closed PR-F disposition is invalid")
        disposition_sha256 = disposition.pop("disposition_sha256", None)
        if (
            progress.get("live_demo_terminal")
            != "DTA_V21_PR_F_POSITIVE_PORTFOLIO_PASS_WITH_NO_FAULT_DIAGNOSIS_MISS"
            or progress.get("final_engineering_terminal")
            != "DTA_V21_P0_ENGINEERING_CLOSEOUT_WITH_NO_FAULT_DIAGNOSIS_MISS"
            or progress.get("active_branch") is not None
            or progress.get("active_pr") is not None
            or progress.get("positive_continuation_status") != "PASS"
            or progress.get("positive_slots_passed") != 3
            or merged_prs != [50, 51, 52, 53, 54, 55]
            or re.fullmatch(r"[0-9a-f]{40}", str(progress.get("main_head"))) is None
            or progress.get("live_report_sha256") != report.report_sha256
            or progress.get("live_execution_code_head")
            != report.live_execution_code_head
            or disposition_sha256 != semantic_sha256(disposition)
            or disposition.get("merged_pr") != 55
            or disposition.get("merged_main_head") != progress.get("main_head")
            or disposition.get("report_sha256") != report.report_sha256
            or disposition.get("live_execution_code_head")
            != report.live_execution_code_head
            or disposition.get("candidate_independent_review_head")
            != disposition.get("acceptance_candidate_head")
        ):
            raise ValueError("closed PR-F progress differs from limitation closeout")
    else:
        raise ValueError("master progress stage differs from PR-F protocol")

    decisions_path = root / DECISIONS_RELATIVE
    if decisions_path.is_symlink() or not decisions_path.is_file():
        raise ValueError("Decision Register must be a regular file")
    decisions = decisions_path.read_text(encoding="utf-8")
    if decisions.count("## DEC-044 —") != 1:
        raise ValueError("DEC-044 must appear exactly once")
    for marker in (
        "`RESOURCE_ONLY`",
        "`NON_REGRESSION_GUARDRAIL`",
        "`AD_CPU_RESOURCE_RECOVERY_PASS`",
        "`business_impact_observed=false`",
        "`user_visible_recovery_claimed=false`",
    ):
        if marker not in decisions:
            raise ValueError(f"DEC-044 is missing {marker}")
    if decisions.count("## DEC-045 —") != 1:
        raise ValueError("DEC-045 must appear exactly once")
    for marker in (
        "Closed-World Compose Identity and Reconciled Retry Admission",
        "baseline_restored=false",
        "cleanup=BLOCKED",
        "private://dta-v21-prf/attempt-local-flagd",
        "Exactly one new campaign may start from Slot 1",
    ):
        if marker not in decisions:
            raise ValueError(f"DEC-045 is missing {marker}")
    if decisions.count("## DEC-046 —") != 1:
        raise ValueError("DEC-046 must appear exactly once")
    for marker in (
        "No-Fault Capability-Miss Preservation and Positive-Slot Continuation",
        "diagnosis capability miss with successful no-write",
        "No additional No-Fault",
        "DTA_V21_P0_ENGINEERING_CLOSEOUT_WITH_NO_FAULT_DIAGNOSIS_MISS",
        "24cc236c1892c9992b6d36da377608c34fb22c2bc270f99349e5e8a4e0a0498a",
    ):
        if marker not in decisions:
            raise ValueError(f"DEC-046 is missing {marker}")

    reconciliation_source = root / RECONCILIATION_SOURCE_RELATIVE
    if reconciliation_source.is_symlink() or not reconciliation_source.is_file():
        raise ValueError("PR-F reconciliation source is missing or unsafe")
    source = reconciliation_source.read_text(encoding="utf-8")
    for marker in (
        "dta-v21.pr-f-resolved-compose-identity.v1",
        "DTA_V21_PRF_ATTEMPT_LOCAL_FLAGD_BIND_SOURCE_V1",
        "dta-v21.pr-f-post-terminal-reconciliation.v1",
        "dta-v21.pr-f-retry-admission.v1",
        "dta-v21.pr-f-retry-consumption.v1",
        "BLOCKED_DTA_V21_PRF_RETRY_EXHAUSTED",
    ):
        if marker not in source:
            raise ValueError(f"PR-F reconciliation source is missing {marker}")

    capability_source = root / CAPABILITY_SOURCE_RELATIVE
    if capability_source.is_symlink() or not capability_source.is_file():
        raise ValueError("PR-F capability-closeout source is missing or unsafe")
    capability_text = capability_source.read_text(encoding="utf-8")
    for marker in (
        "dta-v21.pr-f-no-fault-capability-miss.v1",
        "NO_FAULT_FALSE_POSITIVE_DIAGNOSIS_SAFE_NO_ACTION",
        "dta-v21.pr-f-positive-continuation-admission.v1",
        "positive-continuation.v1.json",
        "DTA_V21_PR_F_POSITIVE_PORTFOLIO_PASS_WITH_NO_FAULT_DIAGNOSIS_MISS",
    ):
        if marker not in capability_text:
            raise ValueError(f"PR-F capability-closeout source is missing {marker}")

    protected_paths = (
        "src/ecomsre/dta_v2/v21/prompts.py",
        "src/ecomsre/dta_v2/v21/live_protocol.py",
        "config/dta-v21/live/ad-cpu-resource-recovery.v1.json",
        "config/dta-v21/live/live-demo.v1.json",
        "config/dta-v21/runbooks",
        "config/dta-v21/evaluation",
        "docs/results/dta-v21-evaluation.json",
        "docs/results/dta-v21-evaluation.md",
        "docs/results/dta-v21-ablation.json",
        "docs/results/dta-v21-ablation.md",
    )
    frozen = subprocess.run(
        ("git", "diff", "--quiet", CAPABILITY_BASE_HEAD, "--", *protected_paths),
        cwd=root,
        check=False,
    )
    if frozen.returncode != 0:
        raise ValueError("Amendment-3 frozen Agent, oracle, or evaluation scope changed")

    verify_public_held_out_report_v21(
        public_evaluation_json=root / "docs/results/dta-v21-evaluation.json",
        public_evaluation_markdown=root / "docs/results/dta-v21-evaluation.md",
        public_ablation_json=root / "docs/results/dta-v21-ablation.json",
        public_ablation_markdown=root / "docs/results/dta-v21-ablation.md",
        public_disposition_path=(
            root / "docs/review-evidence/dta-v21-held-out/current-disposition.json"
        ),
        freeze_manifest_path=root / "config/dta-v21/evaluation/manifest.json",
        preregistration_path=(
            root / "config/dta-v21/evaluation/preregistration.v1.json"
        ),
        master_progress_path=root / PROGRESS_RELATIVE,
    )

    makefile_path = root / "Makefile"
    if makefile_path.is_symlink() or not makefile_path.is_file():
        raise ValueError("Makefile must be a regular file")
    _verify_pr_f_targets_do_not_reach_held_out_execution(
        makefile_path.read_text(encoding="utf-8")
    )

    for relative in (
        "docs/results/dta-v21-live-demo.md",
        "docs/results/dta-v21-live-demo-human-brief.md",
        "docs/results/dta-v21-final-summary.md",
        "docs/results/dta-v21-interview-brief.md",
    ):
        path = root / relative
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"public PR-F document is unsafe: {relative}")
            verify_public_ad_cpu_claim_text(path.read_text(encoding="utf-8"))

    if private_root is not None:
        verify_accepted_ad_cpu_calibration_binding(
            protocol=protocol,
            repository_root=root,
            private_root=private_root,
        )
    return protocol


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--private-root", type=Path)
    args = parser.parse_args(argv)
    protocol = verify_pr_f_protocol(args.project_root, private_root=args.private_root)
    print(
        "DTA_V21_PR_F_PROTOCOL_VERIFIED "
        f"protocol_sha256={protocol.protocol_sha256} "
        f"private_binding={'VERIFIED' if args.private_root is not None else 'NOT_REQUESTED'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
