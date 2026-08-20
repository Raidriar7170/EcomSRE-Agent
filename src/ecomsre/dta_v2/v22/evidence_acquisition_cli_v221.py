"""Write-once local CLI for the gated check and single v2.2.1 study."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v22.evidence_acquisition_campaign_v221 import (
    EvidenceAcquisitionStudyArtifactV221,
    FINAL_STUDY_COMBINATIONS_V221,
    GATED_DEVELOPMENT_COMBINATIONS_V221,
    GatedDevelopmentArtifactV221,
    evaluate_gated_development_v221,
    run_evidence_acquisition_campaign_v221,
)
from ecomsre.dta_v2.v22.evidence_acquisition_manifest_v221 import (
    EvidenceAcquisitionStudyManifestV221,
    load_and_verify_study_manifest_v221,
    sha256_file_v221,
)
from ecomsre.dta_v2.v22.practical_runner import PracticalCaseRunV221
from ecomsre.dta_v2.v22.simple_provider import (
    SHARED_SYSTEM_PROMPT_V221,
    SimpleProviderV22,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


FINAL_RESULT_PATH_V221 = Path(
    "docs/results/dta-v22-1-evidence-acquisition-study.json"
)
FINAL_MANIFEST_PATH_V221 = Path(
    "config/dta-v22-1/evidence-acquisition-study-manifest.json"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DTA v2.2.1 gated development and single-study runner"
    )
    parser.add_argument("mode", choices=("development", "evaluation"))
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--case-set", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--development-iteration", type=int, choices=(1, 2), default=1)
    parser.add_argument("--minimum-request-interval", type=float, default=6.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser


def _write_once(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(
            value,
            handle,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        handle.write("\n")


def _require_repository_path(*, root: Path, supplied: Path, expected: Path) -> None:
    if supplied.resolve() != (root / expected).resolve():
        raise ValueError(f"study path differs from manifest binding: {expected}")


def _load_final_manifest(
    *,
    repository_root: Path,
    manifest_path: Path | None,
    configured_model: str,
    prompt_file: Path,
    case_set_path: Path,
    truth_path: Path,
    output_path: Path,
) -> EvidenceAcquisitionStudyManifestV221:
    if manifest_path is None:
        raise ValueError("evaluation requires --manifest")
    _require_repository_path(
        root=repository_root,
        supplied=manifest_path,
        expected=FINAL_MANIFEST_PATH_V221,
    )
    _require_repository_path(
        root=repository_root,
        supplied=output_path,
        expected=FINAL_RESULT_PATH_V221,
    )
    manifest = load_and_verify_study_manifest_v221(
        manifest_path=manifest_path,
        repository_root=repository_root,
        configured_model=configured_model,
    )
    for supplied, binding in (
        (prompt_file, manifest.prompt),
        (case_set_path, manifest.case_set),
        (truth_path, manifest.truth_set),
    ):
        _require_repository_path(
            root=repository_root,
            supplied=supplied,
            expected=Path(binding.path),
        )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = args.repository_root.resolve()
        values = load_private_provider_env(args.provider_env)
        model = values["ECOMSRE_LLM_MODEL"]
        prompt = args.prompt_file.read_text(encoding="utf-8").strip()
        if prompt != SHARED_SYSTEM_PROMPT_V221:
            raise ValueError("v2.2.1 prompt differs from the implemented prompt")
        manifest: EvidenceAcquisitionStudyManifestV221 | None = None
        if args.mode == "evaluation":
            manifest = _load_final_manifest(
                repository_root=root,
                manifest_path=args.manifest,
                configured_model=model,
                prompt_file=args.prompt_file,
                case_set_path=args.case_set,
                truth_path=args.truth,
                output_path=args.output,
            )
        elif args.manifest is not None:
            raise ValueError("development mode must not use the final manifest")

        config = OpenAICompatibleConfig(
            base_url=values["ECOMSRE_LLM_BASE_URL"],
            api_key=values["ECOMSRE_LLM_API_KEY"],
            model=model,
        )
        provider = SimpleProviderV22(
            config=config,
            minimum_request_interval_seconds=args.minimum_request_interval,
            timeout_seconds=args.timeout,
            debug_root=root / ".local/dta-v22-1-debug",
        )
        partial_path = args.output.with_suffix(args.output.suffix + ".partial.jsonl")
        partial_path.parent.mkdir(parents=True, exist_ok=True)
        partial_handle = partial_path.open("x", encoding="utf-8")

        def observe_run(run: PracticalCaseRunV221) -> None:
            partial_handle.write(run.model_dump_json() + "\n")
            partial_handle.flush()

        combinations = (
            GATED_DEVELOPMENT_COMBINATIONS_V221
            if args.mode == "development"
            else FINAL_STUDY_COMBINATIONS_V221
        )
        try:
            campaign = run_evidence_acquisition_campaign_v221(
                case_set_path=args.case_set,
                truth_path=args.truth,
                repository_root=root,
                provider=provider,
                combinations=combinations,
                system_prompt=prompt,
                run_observer=observe_run,
            )
        finally:
            partial_handle.close()

        if args.mode == "development":
            gate = evaluate_gated_development_v221(runs=campaign.case_runs)
            development_artifact = GatedDevelopmentArtifactV221(
                schema_version="dta-v22.1.gated-development-artifact.v1",
                development_iteration=args.development_iteration,
                provider_model=model,
                prompt_sha256=sha256_file_v221(args.prompt_file),
                case_set_sha256=sha256_file_v221(args.case_set),
                truth_set_sha256=sha256_file_v221(args.truth),
                campaign=campaign,
                gate=gate,
            )
            _write_once(
                args.output, development_artifact.model_dump(mode="json")
            )
            print(
                json.dumps(
                    {
                        "mode": args.mode,
                        "development_iteration": args.development_iteration,
                        "passed": gate.passed,
                        "arm_runs": gate.arm_runs,
                        "uncaught_exceptions": gate.uncaught_exceptions,
                        "agent_writes": gate.agent_writes,
                    },
                    sort_keys=True,
                )
            )
            return 0 if gate.passed else 1

        if manifest is None:
            raise AssertionError("evaluation manifest was not loaded")
        study_artifact = EvidenceAcquisitionStudyArtifactV221(
            schema_version="dta-v22.1.evidence-acquisition-study-artifact.v1",
            single_execution_rule="EXACTLY_ONE_FULL_STUDY_EXECUTION",
            execution_count=1,
            provider_model=model,
            manifest_sha256=sha256_file_v221(args.manifest),
            implementation_commit=manifest.implementation_commit,
            campaign=campaign,
        )
        _write_once(args.output, study_artifact.model_dump(mode="json"))
        interpretation = campaign.interpretation
        if interpretation is None:
            raise AssertionError("final interpretation is absent")
        print(
            json.dumps(
                {
                    "mode": args.mode,
                    "execution_count": 1,
                    "arm_policy_runs": len(campaign.case_runs),
                    "policy_terminal": interpretation.policy_terminal,
                    "uncaught_exceptions": sum(
                        item.uncaught_exceptions for item in campaign.case_runs
                    ),
                    "agent_writes": campaign.agent_writes,
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as error:
        validation_locations = (
            [
                {
                    "location": ".".join(str(item) for item in detail["loc"]),
                    "type": detail["type"],
                }
                for detail in error.errors(include_input=False, include_url=False)
            ]
            if isinstance(error, ValidationError)
            else []
        )
        print(
            json.dumps(
                {
                    "mode": args.mode,
                    "status": "FAILED",
                    "safe_error_code": type(error).__name__,
                    "validation_errors": validation_locations,
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
