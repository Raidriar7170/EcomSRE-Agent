"""Verify the public DTA v2.1 development evaluation and optional freeze."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess
from typing import Any, Sequence

from ecomsre.dta_v2.v21.evaluation_campaign import (
    DevelopmentEvaluationReportV21,
    EvaluationFreezeManifestV21,
    EvaluationPreregistrationV21,
    EvaluationScheduleV21,
    build_development_report_v21,
)
from ecomsre.dta_v2.v21.evaluation_cli import (
    DevelopmentAttemptManifestV21,
    DevelopmentAttemptReceiptV21,
    DevelopmentEvaluationDispositionV21,
)
from ecomsre.dta_v2.v21.agent import DtaAgentRunResultV21
from ecomsre.dta_v2.v21.evaluation_agents import EvaluationEntryResultV21
from ecomsre.dta_v2.v21.evaluation_contracts import (
    AgentVisibleReplayCaseV21,
    EvaluationSplitV21,
    EvaluatorCaseTruthV21,
    PublicEvaluationManifestV21,
)
from ecomsre.dta_v2.v21.evaluation_seal import (
    HeldOutPackSealV21,
    verify_held_out_pack_seal_v21,
)
from ecomsre.dta_v2.v21.identity import build_three_arm_identities_v21


EVALUATION_ROOT = Path("config/dta-v21/evaluation")
PUBLIC_MANIFEST_RELATIVE = EVALUATION_ROOT / "public-case-bindings.v1.json"
SCHEDULE_RELATIVE = EVALUATION_ROOT / "schedule.v1.json"
PREREGISTRATION_RELATIVE = EVALUATION_ROOT / "preregistration.v1.json"
FREEZE_MANIFEST_RELATIVE = EVALUATION_ROOT / "manifest.json"
DEVELOPMENT_REPORT_RELATIVE = Path(
    "docs/results/dta-v21-development-evaluation.json"
)
DEVELOPMENT_DISPOSITION_RELATIVE = Path(
    "docs/review-evidence/dta-v21-evaluation-freeze/current-disposition.json"
)
PROGRESS_RELATIVE = Path("docs/analysis/dta-v21-p0-master-progress.json")


def _regular_file(path: Path, *, description: str) -> Path:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise ValueError(f"{description} is missing") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError(f"{description} must be a regular non-symlink file")
    return path


def _read_text(path: Path, *, description: str) -> str:
    return _regular_file(path, description=description).read_text(encoding="utf-8")


def _sha256(path: Path, *, description: str) -> str:
    return hashlib.sha256(
        _regular_file(path, description=description).read_bytes()
    ).hexdigest()


def verify_development_report_files(
    report_path: Path,
    disposition_path: Path,
) -> tuple[DevelopmentEvaluationReportV21, DevelopmentEvaluationDispositionV21]:
    report = DevelopmentEvaluationReportV21.model_validate_json(
        _read_text(report_path, description="development evaluation report")
    )
    disposition = DevelopmentEvaluationDispositionV21.model_validate_json(
        _read_text(disposition_path, description="development evaluation disposition")
    )
    if (
        report.primary_entry_count != 36
        or report.ablation_entry_count != 4
        or report.truth_isolation != "PASS"
        or report.scorer_self_tests != "PASS"
        or report.unsafe_writes != 0
    ):
        raise ValueError("development evaluation safety or completeness gate failed")
    if (
        disposition.report_sha256 != report.report_sha256
        or disposition.model_id != report.model_id
        or disposition.primary_entry_count != report.primary_entry_count
        or disposition.ablation_entry_count != report.ablation_entry_count
        or disposition.truth_isolation != report.truth_isolation
        or disposition.scorer_self_tests != report.scorer_self_tests
        or disposition.unsafe_writes != report.unsafe_writes
        or disposition.held_out_executed is not False
    ):
        raise ValueError("development disposition differs from report")
    return report, disposition


def _verify_public_dataset(
    root: Path,
) -> tuple[
    PublicEvaluationManifestV21,
    EvaluationScheduleV21,
    EvaluationPreregistrationV21,
]:
    evaluation_root = root / EVALUATION_ROOT
    if evaluation_root.is_symlink() or not evaluation_root.is_dir():
        raise ValueError("public evaluation root is missing or unsafe")
    public = PublicEvaluationManifestV21.model_validate_json(
        _read_text(root / PUBLIC_MANIFEST_RELATIVE, description="public case manifest")
    )
    schedule = EvaluationScheduleV21.model_validate_json(
        _read_text(root / SCHEDULE_RELATIVE, description="evaluation schedule")
    )
    preregistration = EvaluationPreregistrationV21.model_validate_json(
        _read_text(
            root / PREREGISTRATION_RELATIVE,
            description="evaluation preregistration",
        )
    )
    if schedule.schedule_sha256 != preregistration.schedule_sha256:
        raise ValueError("evaluation schedule differs from preregistration")

    development_ids = tuple(f"dta21-case-{index:03d}" for index in range(1, 13))
    held_out_ids = tuple(f"dta21-case-{index:03d}" for index in range(13, 21))
    if tuple(item.case_id for item in public.development_cases) != development_ids:
        raise ValueError("public development binding set differs")
    if tuple(item.case_id for item in public.held_out_cases) != held_out_ids:
        raise ValueError("public held-out binding set differs")

    for binding in public.development_cases:
        visible_path = (
            evaluation_root
            / "development/agent-visible"
            / f"{binding.case_id}.json"
        )
        truth_path = (
            evaluation_root
            / "development/evaluator-truth"
            / f"{binding.case_id}.json"
        )
        visible = AgentVisibleReplayCaseV21.model_validate_json(
            _read_text(visible_path, description="development Agent-visible case")
        )
        truth = EvaluatorCaseTruthV21.model_validate_json(
            _read_text(truth_path, description="development evaluator truth")
        )
        if (
            visible.case_id != binding.case_id
            or truth.case_id != binding.case_id
            or truth.split is not EvaluationSplitV21.DEVELOPMENT
            or visible.case_sha256 != binding.case_sha256
            or truth.truth_sha256 != binding.truth_sha256
        ):
            raise ValueError("development bytes differ from public binding")

    allowed = {
        PUBLIC_MANIFEST_RELATIVE.relative_to(EVALUATION_ROOT),
        SCHEDULE_RELATIVE.relative_to(EVALUATION_ROOT),
        PREREGISTRATION_RELATIVE.relative_to(EVALUATION_ROOT),
        FREEZE_MANIFEST_RELATIVE.relative_to(EVALUATION_ROOT),
    }
    allowed.update(
        Path("development/agent-visible") / f"{case_id}.json"
        for case_id in development_ids
    )
    allowed.update(
        Path("development/evaluator-truth") / f"{case_id}.json"
        for case_id in development_ids
    )
    observed = {
        path.relative_to(evaluation_root)
        for path in evaluation_root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if observed - allowed:
        raise ValueError("public evaluation tree contains an undeclared file")
    return public, schedule, preregistration


def _require_git_commit(root: Path, commit: str) -> None:
    completed = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError("freeze base code head is not a repository commit")


def _git_blob_sha256(root: Path, commit: str, relative: str) -> str:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"frozen source is absent from base code head: {relative}")
    return hashlib.sha256(completed.stdout).hexdigest()


def _verify_freeze_manifest(
    root: Path,
    *,
    public: PublicEvaluationManifestV21,
    schedule: EvaluationScheduleV21,
    preregistration: EvaluationPreregistrationV21,
) -> EvaluationFreezeManifestV21:
    manifest = EvaluationFreezeManifestV21.model_validate_json(
        _read_text(root / FREEZE_MANIFEST_RELATIVE, description="freeze manifest")
    )
    _require_git_commit(root, manifest.base_code_head)
    identities = build_three_arm_identities_v21(
        model_id=preregistration.model_id,
        max_completion_tokens=preregistration.max_completion_tokens,
    )
    if (
        manifest.public_case_manifest != public
        or manifest.schedule_sha256 != schedule.schedule_sha256
        or manifest.preregistration_sha256 != preregistration.preregistration_sha256
        or manifest.agent_identities != identities
        or manifest.held_out_executed is not False
    ):
        raise ValueError("freeze manifest differs from preregistered evaluation")
    source_root = root / "src/ecomsre/dta_v2/v21"
    for binding in manifest.source_bindings:
        relative = f"src/ecomsre/dta_v2/v21/{binding.name}"
        if _sha256(
            source_root / binding.name,
            description=f"frozen evaluation source {binding.name}",
        ) != binding.source_sha256:
            raise ValueError(f"frozen evaluation source changed: {binding.name}")
        if _git_blob_sha256(root, manifest.base_code_head, relative) != (
            binding.source_sha256
        ):
            raise ValueError(
                f"frozen evaluation source differs from base code head: {binding.name}"
            )
    if _sha256(
        root / "config/dta-v21/historical-v2-bindings.v1.json",
        description="historical DTA v2 binding manifest",
    ) != manifest.historical_v2_bindings_sha256:
        raise ValueError("historical DTA v2 binding differs from freeze")
    return manifest


def _load_progress(root: Path) -> dict[str, Any]:
    payload = json.loads(
        _read_text(root / PROGRESS_RELATIVE, description="DTA v2.1 Master Progress")
    )
    if not isinstance(payload, dict):
        raise ValueError("DTA v2.1 Master Progress must be an object")
    return payload


def verify_private_held_out_seal(
    root: Path,
    held_out_pack_root: Path,
) -> HeldOutPackSealV21:
    public, schedule, preregistration = _verify_public_dataset(root)
    manifest = _verify_freeze_manifest(
        root,
        public=public,
        schedule=schedule,
        preregistration=preregistration,
    )
    seal = HeldOutPackSealV21.model_validate_json(
        _read_text(
            held_out_pack_root / "held-out-seal.v1.json",
            description="private held-out seal",
        )
    )
    verify_held_out_pack_seal_v21(held_out_pack_root=held_out_pack_root, seal=seal)
    if (
        seal.base_code_head != manifest.base_code_head
        or seal.freeze_manifest_sha256 != manifest.manifest_sha256
        or seal.public_case_manifest_sha256 != public.manifest_sha256
        or seal.schedule_sha256 != schedule.schedule_sha256
        or seal.preregistration_sha256 != preregistration.preregistration_sha256
        or seal.held_out_executed is not False
    ):
        raise ValueError("private held-out seal differs from public freeze")
    progress = _load_progress(root)
    if progress.get("held_out_seal_sha256") != seal.seal_sha256:
        raise ValueError("Master Progress held-out seal differs")
    return seal


def verify_private_development_attempt(
    root: Path,
    *,
    attempt_root: Path,
    development_root: Path,
) -> DevelopmentAttemptReceiptV21:
    manifest = DevelopmentAttemptManifestV21.model_validate_json(
        _read_text(
            attempt_root / "attempt-manifest.json",
            description="private development attempt manifest",
        )
    )
    receipt = DevelopmentAttemptReceiptV21.model_validate_json(
        _read_text(
            attempt_root / "attempt-receipt.json",
            description="private development attempt receipt",
        )
    )
    report = DevelopmentEvaluationReportV21.model_validate_json(
        _read_text(
            attempt_root / "development-report.json",
            description="private development report",
        )
    )
    scorer_sha256 = _sha256(
        root / "src/ecomsre/dta_v2/v21/evaluation_contracts.py",
        description="development scorer source",
    )
    reporting_sha256 = _sha256(
        root / "src/ecomsre/dta_v2/v21/evaluation_campaign.py",
        description="development reporting source",
    )
    if (
        receipt.attempt_manifest_sha256 != manifest.manifest_sha256
        or manifest.scorer_source_sha256 != scorer_sha256
        or manifest.reporting_source_sha256 != reporting_sha256
        or report.scorer_source_sha256 != scorer_sha256
        or report.reporting_source_sha256 != reporting_sha256
    ):
        raise ValueError("private development scorer/manifest binding differs")

    public, schedule, preregistration = _verify_public_dataset(root)
    if (
        manifest.model_id != preregistration.model_id
        or manifest.public_case_manifest_sha256 != public.manifest_sha256
        or manifest.schedule_sha256 != schedule.schedule_sha256
        or manifest.preregistration_sha256 != preregistration.preregistration_sha256
    ):
        raise ValueError("private development attempt differs from public protocol")
    scheduled = tuple(item for item in schedule.entries if item.split.value == "DEVELOPMENT")
    entry_roots = sorted(
        path
        for path in (attempt_root / "entries").iterdir()
        if path.is_dir() and not path.is_symlink()
    )
    if len(entry_roots) != 40 or len(scheduled) != 40:
        raise ValueError("private development attempt cardinality differs")
    entries: list[EvaluationEntryResultV21] = []
    truths: dict[str, EvaluatorCaseTruthV21] = {}
    for index, (scheduled_entry, entry_root) in enumerate(
        zip(scheduled, entry_roots, strict=True)
    ):
        entry = EvaluationEntryResultV21.model_validate_json(
            _read_text(
                entry_root / "entry-result.json",
                description="private development entry result",
            )
        )
        agent_result = DtaAgentRunResultV21.model_validate_json(
            _read_text(
                entry_root / "agent-result.json",
                description="private development Agent result",
            )
        )
        truth = EvaluatorCaseTruthV21.model_validate_json(
            _read_text(
                development_root
                / "evaluator-truth"
                / f"{scheduled_entry.case_id}.json",
                description="private development evaluator truth",
            )
        )
        if (
            entry.prediction.case_id != scheduled_entry.case_id
            or entry.arm is not scheduled_entry.arm
            or entry.truth_sha256 != truth.truth_sha256
            or entry.agent_result_sha256 != agent_result.result_sha256
            or receipt.entry_sha256s[index] != entry.entry_sha256
        ):
            raise ValueError("private development entry binding differs")
        entries.append(entry)
        truths[truth.case_id] = truth
    identities = build_three_arm_identities_v21(
        model_id=preregistration.model_id,
        max_completion_tokens=preregistration.max_completion_tokens,
    )
    rebuilt = build_development_report_v21(
        entries=tuple(entries), truths=truths, identities=identities
    )
    public_report = DevelopmentEvaluationReportV21.model_validate_json(
        _read_text(
            root / DEVELOPMENT_REPORT_RELATIVE,
            description="public development report",
        )
    )
    if (
        rebuilt != report
        or public_report != report
        or receipt.report_sha256 != report.report_sha256
    ):
        raise ValueError("private development report rebuild differs")
    return receipt


def verify_public_evaluation(
    project_root: Path,
    *,
    require_freeze: bool,
) -> dict[str, object]:
    root = project_root.resolve(strict=True)
    public, schedule, preregistration = _verify_public_dataset(root)
    report, _ = verify_development_report_files(
        root / DEVELOPMENT_REPORT_RELATIVE,
        root / DEVELOPMENT_DISPOSITION_RELATIVE,
    )
    expected_identities = build_three_arm_identities_v21(
        model_id=preregistration.model_id,
        max_completion_tokens=preregistration.max_completion_tokens,
    )
    if (
        report.model_id != preregistration.model_id
        or report.identity_sha256s
        != tuple(item.identity_sha256 for item in expected_identities)
    ):
        raise ValueError("development report identity differs from preregistration")
    scorer_source = root / "src/ecomsre/dta_v2/v21/evaluation_contracts.py"
    reporting_source = root / "src/ecomsre/dta_v2/v21/evaluation_campaign.py"
    if (
        report.scorer_source_sha256
        != _sha256(scorer_source, description="development scorer source")
        or report.reporting_source_sha256
        != _sha256(reporting_source, description="development reporting source")
        or report.primary_aggregate_entry_count != 36
    ):
        raise ValueError("development report lacks exact scorer/source bindings")

    manifest_path = root / FREEZE_MANIFEST_RELATIVE
    manifest: EvaluationFreezeManifestV21 | None = None
    if manifest_path.exists() or manifest_path.is_symlink():
        manifest = _verify_freeze_manifest(
            root,
            public=public,
            schedule=schedule,
            preregistration=preregistration,
        )
        progress = _load_progress(root)
        if progress.get("development_report_sha256") != report.report_sha256:
            raise ValueError("Master Progress development report differs")
        seal_sha256 = progress.get("held_out_seal_sha256")
        if not isinstance(seal_sha256, str) or len(seal_sha256) != 64:
            raise ValueError("Master Progress held-out seal is absent")
    elif require_freeze:
        raise ValueError("freeze manifest is missing")

    return {
        "development_entry_count": (
            report.primary_entry_count + report.ablation_entry_count
        ),
        "development_report_sha256": report.report_sha256,
        "evaluation_frozen": manifest is not None,
        "held_out_case_count": len(public.held_out_cases),
        "scorer_self_tests": report.scorer_self_tests,
        "status": "DTA_V21_PUBLIC_EVALUATION_VERIFIED",
        "truth_isolation": report.truth_isolation,
        "unsafe_writes": report.unsafe_writes,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify DTA v2.1 public development and freeze artifacts."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--require-freeze", action="store_true")
    parser.add_argument("--held-out-pack-root", type=Path)
    parser.add_argument("--private-development-attempt-root", type=Path)
    parser.add_argument("--development-root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.project_root.resolve(strict=True)
    result = verify_public_evaluation(root, require_freeze=args.require_freeze)
    if (args.private_development_attempt_root is None) != (
        args.development_root is None
    ):
        raise ValueError(
            "private development attempt and development root are required together"
        )
    if args.private_development_attempt_root is not None:
        assert args.development_root is not None
        receipt = verify_private_development_attempt(
            root,
            attempt_root=args.private_development_attempt_root.resolve(strict=True),
            development_root=args.development_root.resolve(strict=True),
        )
        result["development_attempt_id"] = receipt.attempt_id
        result["private_development_verified"] = True
    if args.held_out_pack_root is not None:
        seal = verify_private_held_out_seal(
            root,
            args.held_out_pack_root.resolve(strict=True),
        )
        result["held_out_seal_sha256"] = seal.seal_sha256
        result["private_seal_verified"] = True
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "verify_development_report_files",
    "verify_private_development_attempt",
    "verify_private_held_out_seal",
    "verify_public_evaluation",
)
