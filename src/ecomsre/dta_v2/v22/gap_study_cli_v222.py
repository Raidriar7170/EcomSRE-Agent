"""Write-once CLI for the DTA v2.2.2 development and final factorials."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal, Sequence

from pydantic import Field, StrictInt, ValidationError, model_validator

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v22.gap_study_campaign_v222 import (
    GapStudyCampaignResultV222,
    combination_for_run_v222,
    run_gap_study_campaign_v222,
)
from ecomsre.dta_v2.v22.evaluation_manifest_v222 import (
    load_and_verify_evaluation_manifest_v222,
    sha256_file_v222,
)
from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import (
    audit_case_set_v222,
    evaluate_development_routing_gate_v222,
)
from ecomsre.dta_v2.v22.gap_study_runner_v222 import (
    GapStudyCaseRunV222,
    SHARED_SELECTION_SYSTEM_PROMPT_V222,
)
from ecomsre.dta_v2.v22.gap_study_scorer_v222 import (
    GapStudyScoreBundleV222,
    score_gap_study_v222,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22
from ecomsre.dta_v2.v22.selection_provider_v222 import SelectionProviderV222
from ecomsre.model.gateway import OpenAICompatibleConfig


class GapStudyArtifactV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.gap-study-artifact.v1"]
    phase: Literal["DEVELOPMENT", "EVALUATION"]
    execution_count: StrictInt = Field(ge=0, le=1)
    development_iteration: StrictInt | None = Field(default=None, ge=1, le=3)
    provider_model: str
    manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    prompt_sha256: str
    case_set_sha256: str
    truth_set_sha256: str
    campaign: GapStudyCampaignResultV222
    scores: GapStudyScoreBundleV222
    uncaught_exceptions: StrictInt = Field(ge=0)
    agent_writes: Literal[0]

    @model_validator(mode="after")
    def require_phase_bindings(self) -> "GapStudyArtifactV222":
        evaluation = self.phase == "EVALUATION"
        if evaluation != (self.execution_count == 1 and self.manifest_sha256 is not None):
            raise ValueError("study artifact final execution binding differs")
        if evaluation == (self.development_iteration is not None):
            raise ValueError("study artifact development iteration differs")
        return self


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DTA v2.2.2 gap-routing study runner")
    parser.add_argument("mode", choices=("development", "evaluation"))
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--case-set", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--development-iteration", type=int, choices=(1, 2, 3))
    parser.add_argument("--minimum-request-interval", type=float, default=4.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_once(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)


def _markdown(artifact: GapStudyArtifactV222) -> str:
    gate = artifact.scores.development_gate
    lines = [
        "# DTA v2.2.2 Gap-Aware Routing Study",
        "",
        f"- Phase: `{artifact.phase}`",
        f"- Provider model: `{artifact.provider_model}`",
        f"- Cases: {artifact.campaign.cases_materialized}",
        f"- Runs: {len(artifact.campaign.runs)}",
        f"- Execution count: {artifact.execution_count}",
        f"- Uncaught exceptions: {artifact.uncaught_exceptions}",
        f"- Agent writes: {artifact.agent_writes}",
        "",
        "## Four-combination metrics",
        "",
        "| Combination | Exact | Macro-F1 | Diagnosis after read | Control accuracy | Protocol failure |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for score in artifact.scores.combinations:
        lines.append(
            "| "
            f"{score.combination.value} | {score.end_to_end_exact_completion:.3f} | "
            f"{score.mechanism_macro_f1:.3f} | {score.diagnosis_after_read_rate:.3f} | "
            f"{score.combined_no_incident_abstention_accuracy:.3f} | "
            f"{score.protocol_failure_rate:.3f} |"
        )
    lines.extend(
        [
            "",
            "## Development utility gate",
            "",
            f"- Predicate-yield read rate: {gate.predicate_yield_read_rate:.3f}",
            "- Nonempty-or-predicate-yield read rate: "
            f"{gate.nonempty_or_predicate_yield_read_rate:.3f}",
            f"- Read-bearing diagnosed runs: {gate.read_bearing_diagnosed_runs}",
            f"- Protocol failure rate: {gate.protocol_failure_rate:.3f}",
            f"- Gate passed: `{str(gate.gate_passed).lower()}`",
            "",
        ]
    )
    if artifact.scores.interpretation is not None:
        lines.extend(
            [
                "## Measured result terminal",
                "",
                f"`{artifact.scores.interpretation.measured_result_terminal}`",
                "",
                "- Planner interaction observed: "
                f"`{str(artifact.scores.interpretation.planner_interaction_observed).lower()}`",
                "",
            ]
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        phase: Literal["DEVELOPMENT", "EVALUATION"] = (
            "DEVELOPMENT" if args.mode == "development" else "EVALUATION"
        )
        if phase == "DEVELOPMENT" and args.development_iteration is None:
            raise ValueError("development mode requires --development-iteration")
        if phase == "EVALUATION" and args.development_iteration is not None:
            raise ValueError("evaluation mode forbids --development-iteration")
        if phase == "DEVELOPMENT" and args.manifest is not None:
            raise ValueError("development mode forbids the final manifest")
        if phase == "EVALUATION" and args.manifest is None:
            raise ValueError("evaluation mode requires --manifest")
        values = load_private_provider_env(args.provider_env)
        prompt = args.prompt_file.read_text(encoding="utf-8").strip()
        if prompt != SHARED_SELECTION_SYSTEM_PROMPT_V222:
            raise ValueError("v2.2.2 prompt differs from implemented short prompt")
        config = OpenAICompatibleConfig(
            base_url=values["ECOMSRE_LLM_BASE_URL"],
            api_key=values["ECOMSRE_LLM_API_KEY"],
            model=values["ECOMSRE_LLM_MODEL"],
        )
        manifest_sha256: str | None = None
        if phase == "EVALUATION":
            manifest = load_and_verify_evaluation_manifest_v222(
                manifest_path=args.manifest,
                repository_root=args.repository_root,
                configured_model=config.model,
            )
            manifest_sha256 = sha256_file_v222(args.manifest)
            supplied = {
                args.prompt_file.resolve(): manifest.prompt.path,
                args.case_set.resolve(): manifest.case_set.path,
                args.truth.resolve(): manifest.truth_set.path,
            }
            for path, expected in supplied.items():
                if path != (args.repository_root / expected).resolve():
                    raise ValueError("evaluation path differs from frozen manifest")
            expected_json = (
                args.repository_root
                / "docs/results/dta-v22-2-gap-routing-evaluation.json"
            ).resolve()
            expected_markdown = (
                args.repository_root
                / "docs/results/dta-v22-2-gap-routing-evaluation.md"
            ).resolve()
            if (
                args.output_json.resolve() != expected_json
                or args.output_markdown.resolve() != expected_markdown
                or args.minimum_request_interval
                != manifest.minimum_request_interval_seconds
            ):
                raise ValueError("evaluation outputs or pacing differ from manifest")
        provider = SelectionProviderV222(
            config=config,
            minimum_request_interval_seconds=args.minimum_request_interval,
            timeout_seconds=args.timeout,
            debug_root=args.repository_root / ".local/dta-v22-2-debug",
        )
        partial_path = args.output_json.with_suffix(
            args.output_json.suffix + ".partial.jsonl"
        )
        partial_path.parent.mkdir(parents=True, exist_ok=True)
        partial_handle = partial_path.open("x", encoding="utf-8")

        def observe_run(run: GapStudyCaseRunV222) -> None:
            partial_handle.write(run.model_dump_json() + "\n")
            partial_handle.flush()
            print(
                json.dumps(
                    {
                        "case_id": run.case_id,
                        "combination": combination_for_run_v222(run).value,
                        "terminal": run.terminal,
                        "adaptive_reads": run.adaptive_reads,
                        "provider_calls": run.provider_calls,
                        "protocol_repairs": run.protocol_repairs,
                        "transport_retries": run.transport_retry_count,
                        "uncaught_exceptions": run.uncaught_exceptions,
                        "agent_writes": run.agent_writes,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

        try:
            campaign = run_gap_study_campaign_v222(
                repository_root=args.repository_root,
                case_set_path=args.case_set,
                truth_path=args.truth,
                provider=provider,
                run_observer=observe_run,
            )
        finally:
            partial_handle.close()
        utility_audit = audit_case_set_v222(
            repository_root=args.repository_root,
            case_set_path=args.case_set,
            truth_path=args.truth,
        )
        routing_gate = evaluate_development_routing_gate_v222(
            repository_root=args.repository_root,
            case_set_path=args.case_set,
            truth_path=args.truth,
        )
        scores = score_gap_study_v222(
            runs=campaign.runs,
            truths=campaign.truths,
            utility_audit=utility_audit,
            routing_gate=routing_gate,
            include_interpretation=phase == "EVALUATION",
        )
        artifact = GapStudyArtifactV222(
            schema_version="dta-v22.2.gap-study-artifact.v1",
            phase=phase,
            execution_count=1 if phase == "EVALUATION" else 0,
            development_iteration=args.development_iteration,
            provider_model=config.model,
            manifest_sha256=manifest_sha256,
            prompt_sha256=_sha256(args.prompt_file),
            case_set_sha256=_sha256(args.case_set),
            truth_set_sha256=_sha256(args.truth),
            campaign=campaign,
            scores=scores,
            uncaught_exceptions=campaign.uncaught_exceptions,
            agent_writes=0,
        )
        _write_once(
            args.output_json,
            json.dumps(
                artifact.model_dump(mode="json"),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
        )
        _write_once(args.output_markdown, _markdown(artifact))
        print(
            json.dumps(
                {
                    "mode": args.mode,
                    "cases": campaign.cases_materialized,
                    "runs": len(campaign.runs),
                    "development_gate_passed": scores.development_gate.gate_passed,
                    "execution_count": artifact.execution_count,
                    "measured_result_terminal": (
                        None
                        if scores.interpretation is None
                        else scores.interpretation.measured_result_terminal
                    ),
                    "uncaught_exceptions": artifact.uncaught_exceptions,
                    "agent_writes": artifact.agent_writes,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0 if phase == "EVALUATION" or scores.development_gate.gate_passed else 1
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


__all__ = ("GapStudyArtifactV222", "main")
