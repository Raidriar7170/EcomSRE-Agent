"""Verify bounded public and optional private DTA v2.1 held-out evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ecomsre.dta_v2.v21.evaluation_campaign import (
    EvaluationFreezeManifestV21,
    EvaluationPreregistrationV21,
    EvaluationScheduleV21,
)
from ecomsre.dta_v2.v21.evaluation_contracts import EvaluationArmV21
from ecomsre.dta_v2.v21.evaluation_seal import HeldOutPackSealV21
from ecomsre.dta_v2.v21.held_out_cli import (
    DevelopmentAblationReportV21,
    HeldOutEvaluationDispositionV21,
    HeldOutPublicEvaluationReportV21,
    verify_private_held_out_evaluation_v21,
)


def _read_regular(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("held-out verification input is missing or unsafe")
    return path.read_text(encoding="utf-8")


def verify_public_held_out_report_v21(
    *,
    public_evaluation_json: Path,
    public_evaluation_markdown: Path,
    public_disposition_path: Path,
    freeze_manifest_path: Path,
    preregistration_path: Path,
    master_progress_path: Path,
    public_ablation_json: Path | None = None,
    public_ablation_markdown: Path | None = None,
) -> dict[str, object]:
    report = HeldOutPublicEvaluationReportV21.model_validate_json(
        _read_regular(public_evaluation_json)
    )
    disposition = HeldOutEvaluationDispositionV21.model_validate_json(
        _read_regular(public_disposition_path)
    )
    freeze = EvaluationFreezeManifestV21.model_validate_json(
        _read_regular(freeze_manifest_path)
    )
    preregistration = EvaluationPreregistrationV21.model_validate_json(
        _read_regular(preregistration_path)
    )
    progress = json.loads(_read_regular(master_progress_path))
    if (
        report.model_id != freeze.model_id
        or report.identity_sha256s
        != tuple(item.identity_sha256 for item in freeze.agent_identities)
        or report.preregistered_thresholds != preregistration.thresholds
        or report.held_out_pack_seal_sha256 != progress.get("held_out_seal_sha256")
        or report.execution_id != progress.get("held_out_execution_id")
        or report.exact_claim != progress.get("held_out_claim")
        or disposition.execution_id != report.execution_id
        or disposition.claim != report.exact_claim
        or disposition.report_sha256 != report.report_sha256
        or disposition.execution_seal_sha256 != report.execution_seal_sha256
        or disposition.unblinding_receipt_sha256 != report.unblinding_receipt_sha256
    ):
        raise ValueError("public held-out evidence bindings differ")
    aggregates = report.evaluation.aggregates
    by_arm = {item.group_value: item for item in aggregates if item.group_type == "ARM"}
    expected_arms = {
        EvaluationArmV21.ONE_SHOT_FULL_CONTEXT.value,
        EvaluationArmV21.FLAT_ADAPTIVE.value,
        EvaluationArmV21.EVIDENCE_GUIDED_PLANNER.value,
    }
    overall = [item for item in aggregates if item.group_type == "OVERALL"]
    split = [item for item in aggregates if item.group_type == "SPLIT"]
    if (
        set(by_arm) != expected_arms
        or any(item.scored_entries != 8 for item in by_arm.values())
        or len(overall) != 1
        or overall[0].scored_entries != 24
        or len(split) != 1
        or split[0].group_value != "HELD_OUT"
        or split[0].scored_entries != 24
        or sum(
            item.scored_entries for item in aggregates if item.group_type == "MECHANISM"
        )
        != 24
        or sum(
            item.scored_entries
            for item in aggregates
            if item.group_type == "GENERALIZATION_SLICE"
        )
        != 24
        or sum(item.unsafe_proposal_attempts for item in by_arm.values()) != 0
        or sum(item.arbitrary_shell_attempts for item in by_arm.values()) != 0
        or sum(item.non_owned_mutations for item in by_arm.values()) != 0
    ):
        raise ValueError("public held-out metric coverage differs")
    markdown = _read_regular(public_evaluation_markdown)
    if (
        report.terminal not in markdown
        or report.exact_claim not in markdown
        or report.execution_id not in markdown
        or report.held_out_pack_seal_sha256 not in markdown
        or report.execution_seal_sha256 not in markdown
        or "Preregistered threshold table" not in markdown
    ):
        raise ValueError("public held-out Markdown differs")
    ablation_sha256 = None
    if (public_ablation_json is None) != (public_ablation_markdown is None):
        raise ValueError("public ablation verification paths are incomplete")
    if public_ablation_json is not None and public_ablation_markdown is not None:
        ablation = DevelopmentAblationReportV21.model_validate_json(
            _read_regular(public_ablation_json)
        )
        if ablation.development_report_sha256 != progress.get(
            "development_report_sha256"
        ) or str(len(ablation.matched_case_ids)) not in _read_regular(
            public_ablation_markdown
        ):
            raise ValueError("public development ablation differs")
        ablation_sha256 = ablation.report_sha256
    forbidden = ("/Users/", "ECOMSRE_LLM_API_KEY", "sk-")
    for path in (
        public_evaluation_json,
        public_evaluation_markdown,
        public_disposition_path,
        public_ablation_json,
        public_ablation_markdown,
    ):
        if path is not None and any(
            token in _read_regular(path) for token in forbidden
        ):
            raise ValueError("public held-out evidence exposes private content")
    return {
        "status": "DTA_V21_HELD_OUT_REPORT_VERIFIED",
        "terminal": report.terminal,
        "claim": report.exact_claim,
        "execution_id": report.execution_id,
        "held_out_case_count": report.held_out_case_count,
        "scored_entry_count": report.scored_entry_count,
        "execution_seal_sha256": report.execution_seal_sha256,
        "report_sha256": report.report_sha256,
        "ablation_report_sha256": ablation_sha256,
        "truth_isolation": disposition.truth_isolation,
        "scorer_verification": disposition.scorer_verification,
        "unsafe_writes": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--public-evaluation-json", type=Path)
    parser.add_argument("--public-evaluation-markdown", type=Path)
    parser.add_argument("--public-ablation-json", type=Path)
    parser.add_argument("--public-ablation-markdown", type=Path)
    parser.add_argument("--public-disposition", type=Path)
    parser.add_argument("--freeze-manifest", type=Path)
    parser.add_argument("--preregistration", type=Path)
    parser.add_argument("--master-progress", type=Path)
    parser.add_argument("--held-out-pack-root", type=Path)
    parser.add_argument("--held-out-pack-seal", type=Path)
    parser.add_argument("--private-execution-root", type=Path)
    parser.add_argument("--private-unblinding-root", type=Path)
    parser.add_argument("--schedule", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.project_root.resolve()
    evaluation_json = (
        args.public_evaluation_json or root / "docs/results/dta-v21-evaluation.json"
    ).resolve()
    report = verify_public_held_out_report_v21(
        public_evaluation_json=evaluation_json,
        public_evaluation_markdown=(
            args.public_evaluation_markdown
            or root / "docs/results/dta-v21-evaluation.md"
        ).resolve(),
        public_ablation_json=(
            args.public_ablation_json or root / "docs/results/dta-v21-ablation.json"
        ).resolve(),
        public_ablation_markdown=(
            args.public_ablation_markdown or root / "docs/results/dta-v21-ablation.md"
        ).resolve(),
        public_disposition_path=(
            args.public_disposition
            or root / "docs/review-evidence/dta-v21-held-out/current-disposition.json"
        ).resolve(),
        freeze_manifest_path=(
            args.freeze_manifest or root / "config/dta-v21/evaluation/manifest.json"
        ).resolve(),
        preregistration_path=(
            args.preregistration
            or root / "config/dta-v21/evaluation/preregistration.v1.json"
        ).resolve(),
        master_progress_path=(
            args.master_progress
            or root / "docs/analysis/dta-v21-p0-master-progress.json"
        ).resolve(),
    )
    private_args = (
        args.held_out_pack_root,
        args.held_out_pack_seal,
        args.private_execution_root,
        args.private_unblinding_root,
        args.schedule,
    )
    if any(item is not None for item in private_args):
        if any(item is None for item in private_args):
            raise ValueError("private held-out verification arguments are incomplete")
        assert args.held_out_pack_root is not None
        assert args.held_out_pack_seal is not None
        assert args.private_execution_root is not None
        assert args.private_unblinding_root is not None
        assert args.schedule is not None
        verify_private_held_out_evaluation_v21(
            repository_root=root,
            held_out_pack_root=args.held_out_pack_root.resolve(),
            private_execution_root=args.private_execution_root.resolve(),
            private_unblinding_root=args.private_unblinding_root.resolve(),
            freeze_manifest=EvaluationFreezeManifestV21.model_validate_json(
                _read_regular(
                    args.freeze_manifest.resolve()
                    if args.freeze_manifest is not None
                    else root / "config/dta-v21/evaluation/manifest.json"
                )
            ),
            schedule=EvaluationScheduleV21.model_validate_json(
                _read_regular(args.schedule.resolve())
            ),
            preregistration=EvaluationPreregistrationV21.model_validate_json(
                _read_regular(
                    args.preregistration.resolve()
                    if args.preregistration is not None
                    else root / "config/dta-v21/evaluation/preregistration.v1.json"
                )
            ),
            held_out_pack_seal=HeldOutPackSealV21.model_validate_json(
                _read_regular(args.held_out_pack_seal.resolve())
            ),
            public_report=HeldOutPublicEvaluationReportV21.model_validate_json(
                _read_regular(evaluation_json)
            ),
        )
        report["private_execution_verified"] = True
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_public_held_out_report_v21",)
