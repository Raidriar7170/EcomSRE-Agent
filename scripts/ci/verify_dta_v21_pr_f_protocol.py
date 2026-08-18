"""Verify the DTA v2.1 PR-F Ad CPU resource-recovery protocol."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Sequence

from ecomsre.dta_v2.v21.live_protocol import (
    AdCpuResourceRecoveryProtocolV1,
    load_ad_cpu_resource_recovery_protocol_v1,
    verify_accepted_ad_cpu_calibration_binding,
    verify_public_ad_cpu_claim_text,
)
from scripts.ci.verify_dta_v21_evaluation_freeze import verify_public_evaluation
from scripts.ci.verify_dta_v21_held_out import verify_public_held_out_report_v21
from scripts.ci.verify_dta_v2_historical_bindings import verify_historical_bindings


PROTOCOL_RELATIVE = Path("config/dta-v21/live/ad-cpu-resource-recovery.v1.json")
PROGRESS_RELATIVE = Path("docs/analysis/dta-v21-p0-master-progress.json")
DECISIONS_RELATIVE = Path("docs/DECISIONS.md")
HISTORICAL_BINDINGS_RELATIVE = Path(
    "config/dta-v21/historical-v2-bindings.v1.json"
)


def _verify_pr_f_targets_do_not_reach_held_out_execution(makefile: str) -> None:
    lines = makefile.splitlines()
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
        target for target in prerequisites if target.startswith("dta-v21-pr-f-")
    )
    if not roots:
        raise ValueError("no PR-F Make target is defined")
    forbidden_target = re.compile(r"dta-v21-held-out-(?:execute|score)", re.I)
    forbidden_recipe = re.compile(
        r"(?:dta[_-]v21[_-]held[_-]out[_-]cli|held[_-]out[_-]cli)"
        r"[^\n]*(?:execute|score)",
        re.I,
    )
    for root in roots:
        pending = [root]
        visited: set[str] = set()
        while pending:
            target = pending.pop()
            if target in visited:
                continue
            visited.add(target)
            recipe = re.sub(
                r"\\\n[ \t]*", " ", "\n".join(recipes.get(target, ()))
            )
            if forbidden_target.search(target) or forbidden_recipe.search(recipe):
                raise ValueError("a PR-F target reaches held-out execution or scoring")
            dependencies = prerequisites.get(target, ())
            if any(forbidden_target.search(item) for item in dependencies):
                raise ValueError("a PR-F target reaches held-out execution or scoring")
            pending.extend(
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
        "active_amendment_version": protocol.amendment_version,
        "completed_stage": "PR-E",
        "current_stage": "PR-F",
        "held_out_seal_sha256": (
            "9a7c8e56400e99c693c8bddc26007b1dd26e0dcee2167b07cf3fba00fd22fbd7"
        ),
        "held_out_execution_id": "53615cdd78b348b68496f64102c0b4de",
        "held_out_claim": (
            "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED"
        ),
        "ad_cpu_resource_recovery_protocol_sha256": protocol.protocol_sha256,
    }
    for field, expected in required_progress.items():
        if progress.get(field) != expected:
            raise ValueError(f"master progress field {field} differs from PR-F protocol")

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
    protocol = verify_pr_f_protocol(
        args.project_root, private_root=args.private_root
    )
    print(
        "DTA_V21_PR_F_PROTOCOL_VERIFIED "
        f"protocol_sha256={protocol.protocol_sha256} "
        f"private_binding={'VERIFIED' if args.private_root is not None else 'NOT_REQUESTED'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
