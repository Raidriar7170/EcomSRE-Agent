"""Promote one clean owned capture into visible development and private held-out packs."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import model_validator

from ecomsre.dta_v2.v21.capture_campaign import (
    CaptureCampaignClosureV21,
    CaptureCampaignPlanV21,
    CaptureTerminalV21,
    build_default_capture_plan_v21,
)
from ecomsre.dta_v2.v21.contracts import DtaModelV21, Sha256V21, semantic_sha256
from ecomsre.dta_v2.v21.evaluation_contracts import (
    AgentVisibleReplayCaseV21,
    EvaluationSplitV21,
    EvaluatorCaseTruthV21,
    PublicCaseBindingV21,
    PublicEvaluationManifestV21,
)
from ecomsre_live_sandbox.contracts import write_private_json


class EvaluationDatasetPromotionV21(DtaModelV21):
    schema_version: Literal["dta-v21.evaluation-dataset-promotion.v1"]
    capture_plan_sha256: Sha256V21
    capture_closure_sha256: Sha256V21
    development_case_count: Literal[12]
    held_out_case_count: Literal[8]
    public_manifest: PublicEvaluationManifestV21
    promotion_sha256: Sha256V21

    @model_validator(mode="after")
    def require_promotion(self) -> EvaluationDatasetPromotionV21:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"promotion_sha256"})
        )
        if self.promotion_sha256 != expected:
            raise ValueError("evaluation dataset promotion digest differs")
        return self


def promote_capture_dataset_v21(
    *,
    plan: CaptureCampaignPlanV21,
    closure: CaptureCampaignClosureV21,
    capture_attempt_root: Path,
    development_root: Path,
    held_out_pack_root: Path,
    public_binding_path: Path,
) -> EvaluationDatasetPromotionV21:
    """Validate all captured bytes, then create each destination exactly once."""

    plan = CaptureCampaignPlanV21.model_validate(plan.model_dump(mode="python"))
    closure = CaptureCampaignClosureV21.model_validate(
        closure.model_dump(mode="python")
    )
    if closure.terminal is not CaptureTerminalV21.PASS:
        raise ValueError("capture closure is not PASS")
    if closure.plan_sha256 != plan.plan_sha256:
        raise ValueError("capture closure belongs to another plan")
    receipts = {item.case_id: item for item in closure.case_receipts}
    if set(receipts) != {item.case_id for item in plan.cases}:
        raise ValueError("capture closure receipts are incomplete")

    development_bindings: list[PublicCaseBindingV21] = []
    held_out_bindings: list[PublicCaseBindingV21] = []
    for case_plan in plan.cases:
        source = capture_attempt_root / "cases" / case_plan.case_id
        visible = AgentVisibleReplayCaseV21.model_validate_json(
            _read_regular(source / "agent-visible.json")
        )
        truth = EvaluatorCaseTruthV21.model_validate_json(
            _read_regular(source / "evaluator-truth.json")
        )
        receipt = receipts[case_plan.case_id]
        if (
            visible.case_id != case_plan.case_id
            or truth.case_id != case_plan.case_id
            or truth.split is not case_plan.split
            or visible.case_sha256 != receipt.case_sha256
            or truth.truth_sha256 != receipt.truth_sha256
        ):
            raise ValueError("capture bytes differ from the closure receipt")
        binding = PublicCaseBindingV21(
            case_id=case_plan.case_id,
            case_sha256=visible.case_sha256,
            truth_sha256=truth.truth_sha256,
            split_sha256=semantic_sha256(case_plan.split.value),
        )
        if case_plan.split is EvaluationSplitV21.DEVELOPMENT:
            write_private_json(
                development_root / "agent-visible" / f"{case_plan.case_id}.json",
                visible,
                create_once=True,
            )
            write_private_json(
                development_root / "evaluator-truth" / f"{case_plan.case_id}.json",
                truth,
                create_once=True,
            )
            development_bindings.append(binding)
        else:
            destination = held_out_pack_root / "cases" / case_plan.case_id
            write_private_json(
                destination / "agent-visible.json", visible, create_once=True
            )
            write_private_json(
                destination / "evaluator-truth.json", truth, create_once=True
            )
            held_out_bindings.append(binding)

    manifest_payload: dict[str, object] = {
        "schema_version": "dta-v21.public-evaluation-manifest.v1",
        "case_schema_version": "dta-v21.agent-visible-replay-case.v1",
        "truth_schema_version": "dta-v21.evaluator-case-truth.v1",
        "development_cases": tuple(development_bindings),
        "held_out_cases": tuple(held_out_bindings),
    }
    manifest_draft = cast(Any, PublicEvaluationManifestV21).model_construct(
        **manifest_payload, manifest_sha256="0" * 64
    )
    manifest = PublicEvaluationManifestV21.model_validate(
        {
            **manifest_payload,
            "manifest_sha256": semantic_sha256(
                manifest_draft.model_dump(mode="json", exclude={"manifest_sha256"})
            ),
        }
    )
    write_private_json(public_binding_path, manifest, create_once=True)
    payload: dict[str, object] = {
        "schema_version": "dta-v21.evaluation-dataset-promotion.v1",
        "capture_plan_sha256": plan.plan_sha256,
        "capture_closure_sha256": closure.closure_sha256,
        "development_case_count": 12,
        "held_out_case_count": 8,
        "public_manifest": manifest,
    }
    draft = cast(Any, EvaluationDatasetPromotionV21).model_construct(
        **payload, promotion_sha256="0" * 64
    )
    promotion = EvaluationDatasetPromotionV21.model_validate(
        {
            **payload,
            "promotion_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"promotion_sha256"})
            ),
        }
    )
    write_private_json(
        held_out_pack_root / "dataset-promotion.json",
        promotion,
        create_once=True,
    )
    return promotion


def publish_development_dataset_v21(
    *,
    development_private_root: Path,
    public_manifest: PublicEvaluationManifestV21,
    evaluation_config_root: Path,
) -> None:
    """Publish only visible development bytes plus answer-free hash bindings."""

    public_manifest = PublicEvaluationManifestV21.model_validate(
        public_manifest.model_dump(mode="python")
    )
    for binding in public_manifest.development_cases:
        visible = AgentVisibleReplayCaseV21.model_validate_json(
            _read_regular(
                development_private_root / "agent-visible" / f"{binding.case_id}.json"
            )
        )
        truth = EvaluatorCaseTruthV21.model_validate_json(
            _read_regular(
                development_private_root / "evaluator-truth" / f"{binding.case_id}.json"
            )
        )
        if (
            visible.case_sha256 != binding.case_sha256
            or truth.truth_sha256 != binding.truth_sha256
            or truth.split is not EvaluationSplitV21.DEVELOPMENT
        ):
            raise ValueError("development publication differs from public binding")
        write_public_model_create_once_v21(
            evaluation_config_root
            / "development"
            / "agent-visible"
            / f"{binding.case_id}.json",
            visible,
        )
        write_public_model_create_once_v21(
            evaluation_config_root
            / "development"
            / "evaluator-truth"
            / f"{binding.case_id}.json",
            truth,
        )
    write_public_model_create_once_v21(
        evaluation_config_root / "public-case-bindings.v1.json", public_manifest
    )


def write_public_model_create_once_v21(path: Path, model: DtaModelV21) -> None:
    parent = path.parent
    missing: list[Path] = []
    cursor = parent
    while not cursor.exists():
        if cursor.is_symlink():
            raise ValueError("public evaluation destination is unsafe")
        missing.append(cursor)
        cursor = cursor.parent
    if cursor.is_symlink() or not cursor.is_dir():
        raise ValueError("public evaluation destination is unsafe")
    for directory in reversed(missing):
        directory.mkdir(mode=0o755, exist_ok=False)
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    data = (model.model_dump_json(indent=2) + "\n").encode("utf-8")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("public evaluation write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_regular(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("evaluation dataset source is missing or unsafe")
    return path.read_text(encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Promote one PASS capture into the frozen PR-D dataset."
    )
    parser.add_argument("--capture-attempt-root", type=Path, required=True)
    parser.add_argument("--base-head", required=True)
    parser.add_argument("--development-private-root", type=Path, required=True)
    parser.add_argument("--held-out-pack-root", type=Path, required=True)
    parser.add_argument("--public-binding-private-path", type=Path, required=True)
    parser.add_argument("--evaluation-config-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    capture_root = args.capture_attempt_root.resolve()
    plan = build_default_capture_plan_v21(base_head=args.base_head)
    closure = CaptureCampaignClosureV21.model_validate_json(
        _read_regular(capture_root / "capture-campaign-closure.json")
    )
    promotion = promote_capture_dataset_v21(
        plan=plan,
        closure=closure,
        capture_attempt_root=capture_root,
        development_root=args.development_private_root.resolve(),
        held_out_pack_root=args.held_out_pack_root.resolve(),
        public_binding_path=args.public_binding_private_path.resolve(),
    )
    publish_development_dataset_v21(
        development_private_root=args.development_private_root.resolve(),
        public_manifest=promotion.public_manifest,
        evaluation_config_root=args.evaluation_config_root.resolve(),
    )
    print(promotion.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "EvaluationDatasetPromotionV21",
    "promote_capture_dataset_v21",
    "publish_development_dataset_v21",
    "write_public_model_create_once_v21",
)
