"""Write-once CLI for the v2.2.5 development and final 2x2 studies."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal, Sequence

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v22.admission_dispatch_campaign_v223 import (
    load_frozen_predicate_yield_priors_v223,
)
from ecomsre.dta_v2.v22.ambiguity_bundle_campaign_v225 import (
    AmbiguityBundleCampaignResultV225,
    AmbiguityBundleCaseRunV225,
    SHARED_SELECTION_SYSTEM_PROMPT_V225,
    run_ambiguity_bundle_campaign_v225,
)
from ecomsre.dta_v2.v22.ambiguity_bundle_scorer_v225 import (
    AmbiguityBundleScoreV225,
    score_ambiguity_bundle_study_v225,
)
from ecomsre.dta_v2.v22.evaluation_strata_v225 import EvaluatorStrataV225
from ecomsre.dta_v2.v22.evaluation_manifest_v225 import sha256_file_v225
from ecomsre.dta_v2.v22.evaluation_preflight_v225 import preflight_evaluation_v225
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22
from ecomsre.dta_v2.v22.selection_provider_v225 import SelectionProviderV225
from ecomsre.model.gateway import OpenAICompatibleConfig


class AmbiguityBundleStudyArtifactV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.ambiguity-bundle-study-artifact.v1"]
    phase: Literal["DEVELOPMENT", "EVALUATION"]
    execution_count: StrictInt = Field(ge=0, le=1)
    development_iteration: StrictInt | None = Field(default=None, ge=1, le=2)
    provider_model: str
    prompt_sha256: str
    case_set_sha256: str
    truth_set_sha256: str
    coverage_sha256: str
    strata_sha256: str
    predicate_yield_prior_sha256: str
    manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    preflight_status: Literal["DTA_V22_5_EVALUATION_PREFLIGHT_PASS"] | None = None
    partial_journal_sha256: str
    partial_journal_line_count: StrictInt = Field(ge=1)
    campaign: AmbiguityBundleCampaignResultV225
    scores: AmbiguityBundleScoreV225
    uncaught_exceptions: StrictInt = Field(ge=0)
    agent_writes: Literal[0]

    @model_validator(mode="after")
    def require_phase(self) -> "AmbiguityBundleStudyArtifactV225":
        final = self.phase == "EVALUATION"
        if final != (self.execution_count == 1 and self.manifest_sha256 is not None):
            raise ValueError("v2.2.5 study final execution binding differs")
        if final != (self.preflight_status == "DTA_V22_5_EVALUATION_PREFLIGHT_PASS"):
            raise ValueError("v2.2.5 study preflight binding differs")
        if final == (self.development_iteration is not None):
            raise ValueError("v2.2.5 study development iteration binding differs")
        if self.uncaught_exceptions != self.campaign.uncaught_exceptions:
            raise ValueError("v2.2.5 study exception accounting differs")
        if self.agent_writes != self.campaign.agent_writes:
            raise ValueError("v2.2.5 study Agent-write accounting differs")
        if self.partial_journal_line_count != len(self.campaign.runs):
            raise ValueError("v2.2.5 partial journal line count differs from represented runs")
        return self


def _sha256(path: Path) -> str:
    return sha256_file_v225(path)


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_once(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)


def _markdown(artifact: AmbiguityBundleStudyArtifactV225) -> str:
    lines = [
        "# DTA v2.2.5 Opaque Ambiguity and Fail-Closed Set Closure Study",
        "",
        f"- Phase: `{artifact.phase}`",
        f"- Provider model: `{artifact.provider_model}`",
        f"- Cases: {artifact.campaign.cases_materialized}",
        f"- Runs: {len(artifact.campaign.runs)}",
        f"- Full-study execution count: {artifact.execution_count}",
        f"- Uncaught exceptions: {artifact.uncaught_exceptions}",
        f"- Agent writes: {artifact.agent_writes}",
        "",
        "## Four-combination metrics",
        "",
        "| Combination | Exact | Macro-F1 | Resource ambiguity | Premature NO_INCIDENT | Resources reads/case | Control | Provider calls |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for score in artifact.scores.combinations:
        lines.append(
            "| "
            f"{score.combination.value} | {score.exact_completion_rate:.3f} | "
            f"{score.mechanism_macro_f1:.3f} | "
            f"{score.resource_ambiguity_exact_accuracy:.3f} | "
            f"{score.premature_no_incident_partial_rate:.3f} | "
            f"{score.mean_resources_reads_per_resource_case:.3f} | "
            f"{score.combined_control_accuracy:.3f} | {score.provider_calls} |"
        )
    if artifact.scores.development_gate is not None:
        gate = artifact.scores.development_gate
        lines.extend(
            [
                "",
                "## Development gate",
                "",
                f"- Gate passed: `{str(gate.gate_passed).lower()}`",
                f"- Exact gain: {gate.exact_case_gain}",
                f"- Macro-F1 gain: {gate.mechanism_macro_f1_gain:.3f}",
            ]
        )
    if artifact.scores.interpretation is not None:
        lines.extend(
            [
                "",
                "## Measured result terminal",
                "",
                f"`{artifact.scores.interpretation.measured_result_terminal}`",
            ]
        )
    return "\n".join((*lines, ""))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DTA v2.2.5 2x2 study")
    parser.add_argument("mode", choices=("development", "evaluation"))
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--case-set", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--strata", type=Path, required=True)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--development-iteration", type=int, choices=(1, 2))
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--minimum-request-interval", type=float, default=4.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    phase: Literal["DEVELOPMENT", "EVALUATION"] = (
        "DEVELOPMENT" if args.mode == "development" else "EVALUATION"
    )
    if phase == "DEVELOPMENT" and args.development_iteration is None:
        raise ValueError("development phase requires an iteration")
    if phase == "EVALUATION" and args.development_iteration is not None:
        raise ValueError("evaluation phase forbids a development iteration")
    if phase == "DEVELOPMENT" and args.manifest is not None:
        raise ValueError("development phase forbids the final manifest")
    if phase == "EVALUATION" and args.manifest is None:
        raise ValueError("evaluation phase requires the final manifest")
    for output in (args.output_json, args.output_markdown):
        if output.exists():
            raise FileExistsError(f"write-once output already exists: {output}")

    values = load_private_provider_env(args.provider_env)
    config = OpenAICompatibleConfig(
        base_url=values["ECOMSRE_LLM_BASE_URL"],
        api_key=values["ECOMSRE_LLM_API_KEY"],
        model=values["ECOMSRE_LLM_MODEL"],
    )
    manifest_sha256 = None
    preflight_status = None
    if phase == "EVALUATION":
        assert args.manifest is not None
        preflight = preflight_evaluation_v225(
            manifest_path=args.manifest,
            repository_root=args.repository_root,
            configured_model=config.model,
            minimum_request_interval_seconds=args.minimum_request_interval,
            timeout_seconds=args.timeout,
            case_set_path=args.case_set,
            truth_path=args.truth,
            coverage_path=args.coverage,
            strata_path=args.strata,
            predicate_yield_prior_path=args.prior,
            output_json_path=args.output_json,
            output_markdown_path=args.output_markdown,
        )
        manifest_sha256 = preflight.manifest_sha256
        preflight_status = preflight.status
    provider = SelectionProviderV225(
        config=config,
        minimum_request_interval_seconds=args.minimum_request_interval,
        timeout_seconds=args.timeout,
        debug_root=args.repository_root / ".local/dta-v22-5-debug",
    )
    partial = args.output_json.with_suffix(args.output_json.suffix + ".partial.jsonl")
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial_handle = partial.open("x", encoding="utf-8")

    def observe(run: AmbiguityBundleCaseRunV225) -> None:
        partial_handle.write(run.model_dump_json() + "\n")
        partial_handle.flush()
        print(
            json.dumps(
                {
                    "case_id": run.case_id,
                    "combination": run.combination.value,
                    "terminal": run.terminal,
                    "resources_reads": run.individual_resources_reads
                    + run.bundle_resources_reads,
                    "provider_calls": run.provider_calls,
                    "repairs": run.protocol_repairs,
                    "transport_retries": run.transport_retry_count,
                    "status": run.status.value,
                },
                sort_keys=True,
            ),
            flush=True,
        )

    try:
        priors = load_frozen_predicate_yield_priors_v223(args.prior)
        campaign = run_ambiguity_bundle_campaign_v225(
            repository_root=args.repository_root,
            case_set_path=args.case_set,
            truth_path=args.truth,
            coverage_path=args.coverage,
            provider=provider,
            predicate_yield_priors=priors,
            observer=observe,
        )
    finally:
        partial_handle.close()
    scores = score_ambiguity_bundle_study_v225(
        runs=campaign.runs,
        truths=campaign.truths,
        strata=EvaluatorStrataV225.model_validate_json(args.strata.read_bytes()),
        include_development_gate=phase == "DEVELOPMENT",
        include_interpretation=phase == "EVALUATION",
    )
    artifact = AmbiguityBundleStudyArtifactV225(
        schema_version="dta-v22.5.ambiguity-bundle-study-artifact.v1",
        phase=phase,
        execution_count=int(phase == "EVALUATION"),
        development_iteration=args.development_iteration,
        provider_model=config.model,
        prompt_sha256=_text_sha256(SHARED_SELECTION_SYSTEM_PROMPT_V225),
        case_set_sha256=_sha256(args.case_set),
        truth_set_sha256=_sha256(args.truth),
        coverage_sha256=_sha256(args.coverage),
        strata_sha256=_sha256(args.strata),
        predicate_yield_prior_sha256=_sha256(args.prior),
        manifest_sha256=manifest_sha256,
        preflight_status=preflight_status,
        partial_journal_sha256=_sha256(partial),
        partial_journal_line_count=sum(1 for _ in partial.open("rb")),
        campaign=campaign,
        scores=scores,
        uncaught_exceptions=campaign.uncaught_exceptions,
        agent_writes=0,
    )
    _write_once(args.output_json, artifact.model_dump_json(indent=2) + "\n")
    _write_once(args.output_markdown, _markdown(artifact))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("AmbiguityBundleStudyArtifactV225", "main")
