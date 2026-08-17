"""Create-once preregistration, freeze manifest, and private held-out seal."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre.dta_v2.v21.contracts import semantic_sha256
from ecomsre.dta_v2.v21.evaluation_campaign import (
    EvaluationFreezeManifestV21,
    EvaluationPreregistrationV21,
    EvaluationScheduleV21,
    build_evaluation_freeze_manifest_v21,
    build_evaluation_preregistration_v21,
    build_evaluation_schedule_v21,
)
from ecomsre.dta_v2.v21.evaluation_contracts import PublicEvaluationManifestV21
from ecomsre.dta_v2.v21.evaluation_dataset import (
    write_public_model_create_once_v21,
)
from ecomsre.dta_v2.v21.evaluation_seal import seal_held_out_pack_v21


SCHEDULE_SEED_V21 = semantic_sha256("dta-v21-p0-master-v1-evaluation-schedule-v1")


def preregister_evaluation_v21(
    *,
    evaluation_config_root: Path,
    model_id: str,
    max_completion_tokens: int,
) -> tuple[EvaluationScheduleV21, EvaluationPreregistrationV21]:
    schedule = build_evaluation_schedule_v21(seed_sha256=SCHEDULE_SEED_V21)
    preregistration = build_evaluation_preregistration_v21(
        model_id=model_id,
        max_completion_tokens=max_completion_tokens,
        schedule_sha256=schedule.schedule_sha256,
    )
    write_public_model_create_once_v21(
        evaluation_config_root / "schedule.v1.json", schedule
    )
    write_public_model_create_once_v21(
        evaluation_config_root / "preregistration.v1.json", preregistration
    )
    return schedule, preregistration


def freeze_evaluation_v21(
    *,
    repository_root: Path,
    base_code_head: str,
    model_id: str,
    max_completion_tokens: int,
    public_manifest_path: Path,
    schedule_path: Path,
    preregistration_path: Path,
    output_path: Path,
) -> EvaluationFreezeManifestV21:
    public = PublicEvaluationManifestV21.model_validate_json(
        _read_regular(public_manifest_path)
    )
    schedule = EvaluationScheduleV21.model_validate_json(_read_regular(schedule_path))
    preregistration = EvaluationPreregistrationV21.model_validate_json(
        _read_regular(preregistration_path)
    )
    manifest = build_evaluation_freeze_manifest_v21(
        repository_root=repository_root,
        base_code_head=base_code_head,
        model_id=model_id,
        max_completion_tokens=max_completion_tokens,
        public_case_manifest=public,
        schedule=schedule,
        preregistration=preregistration,
    )
    write_public_model_create_once_v21(output_path, manifest)
    return manifest


def _read_regular(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("evaluation freeze input is missing or unsafe")
    return path.read_text(encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    preregister = subparsers.add_parser("preregister")
    preregister.add_argument("--evaluation-config-root", type=Path, required=True)
    preregister.add_argument("--model-id", required=True)
    preregister.add_argument("--max-completion-tokens", type=int, default=1600)

    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--repository-root", type=Path, required=True)
    freeze.add_argument("--base-code-head", required=True)
    freeze.add_argument("--model-id", required=True)
    freeze.add_argument("--max-completion-tokens", type=int, default=1600)
    freeze.add_argument("--public-manifest", type=Path, required=True)
    freeze.add_argument("--schedule", type=Path, required=True)
    freeze.add_argument("--preregistration", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)

    seal = subparsers.add_parser("seal")
    seal.add_argument("--held-out-pack-root", type=Path, required=True)
    seal.add_argument("--freeze-manifest", type=Path, required=True)
    seal.add_argument("--schedule", type=Path, required=True)
    seal.add_argument("--preregistration", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "preregister":
        schedule, preregistration = preregister_evaluation_v21(
            evaluation_config_root=args.evaluation_config_root.resolve(),
            model_id=args.model_id,
            max_completion_tokens=args.max_completion_tokens,
        )
        print(schedule.model_dump_json(indent=2))
        print(preregistration.model_dump_json(indent=2))
        return 0
    if args.command == "freeze":
        manifest = freeze_evaluation_v21(
            repository_root=args.repository_root.resolve(),
            base_code_head=args.base_code_head,
            model_id=args.model_id,
            max_completion_tokens=args.max_completion_tokens,
            public_manifest_path=args.public_manifest.resolve(),
            schedule_path=args.schedule.resolve(),
            preregistration_path=args.preregistration.resolve(),
            output_path=args.output.resolve(),
        )
        print(manifest.model_dump_json(indent=2))
        return 0
    seal = seal_held_out_pack_v21(
        held_out_pack_root=args.held_out_pack_root.resolve(),
        freeze_manifest=EvaluationFreezeManifestV21.model_validate_json(
            _read_regular(args.freeze_manifest.resolve())
        ),
        schedule=EvaluationScheduleV21.model_validate_json(
            _read_regular(args.schedule.resolve())
        ),
        preregistration=EvaluationPreregistrationV21.model_validate_json(
            _read_regular(args.preregistration.resolve())
        ),
    )
    print(seal.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "SCHEDULE_SEED_V21",
    "freeze_evaluation_v21",
    "preregister_evaluation_v21",
)
