"""Write-once CLI for the v2.2.3 development and final 2x2 studies."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal, Sequence

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v22.admission_dispatch_campaign_v223 import (
    AdmissionDispatchCampaignResultV223,
    AdmissionDispatchCaseRunV223,
    SHARED_SELECTION_SYSTEM_PROMPT_V223,
    load_frozen_predicate_yield_priors_v223,
    run_admission_dispatch_campaign_v223,
)
from ecomsre.dta_v2.v22.admission_dispatch_scorer_v223 import (
    AdmissionDispatchScoreBundleV223,
    score_admission_dispatch_study_v223,
)
from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import audit_case_set_v222
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22
from ecomsre.dta_v2.v22.selection_provider_v223 import SelectionProviderV223
from ecomsre.model.gateway import OpenAICompatibleConfig


class AdmissionDispatchStudyArtifactV223(DtaModelV22):
    schema_version: Literal["dta-v22.3.admission-dispatch-study-artifact.v1"]
    phase: Literal["DEVELOPMENT", "EVALUATION"]
    execution_count: StrictInt = Field(ge=0, le=1)
    development_iteration: StrictInt | None = Field(default=None, ge=1, le=2)
    provider_model: str
    prompt_sha256: str
    case_set_sha256: str
    truth_set_sha256: str
    predicate_yield_prior_sha256: str
    manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    campaign: AdmissionDispatchCampaignResultV223
    scores: AdmissionDispatchScoreBundleV223
    uncaught_exceptions: StrictInt = Field(ge=0)
    agent_writes: Literal[0]

    @model_validator(mode="after")
    def require_phase(self) -> "AdmissionDispatchStudyArtifactV223":
        final = self.phase == "EVALUATION"
        if final != (self.execution_count == 1 and self.manifest_sha256 is not None):
            raise ValueError("v2.2.3 study final execution binding differs")
        if final == (self.development_iteration is not None):
            raise ValueError("v2.2.3 study development iteration binding differs")
        if self.uncaught_exceptions != self.campaign.uncaught_exceptions:
            raise ValueError("v2.2.3 study exception accounting differs")
        return self


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_once(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)


def _markdown(artifact: AdmissionDispatchStudyArtifactV223) -> str:
    lines = [
        "# DTA v2.2.3 Evidence Closure and Deterministic Dispatch Study",
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
        "| Combination | Exact | Macro-F1 | Resource-silent | Premature NO_INCIDENT | Diagnosis after read | Control | Provider calls |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for score in artifact.scores.combinations:
        lines.append(
            "| "
            f"{score.combination.value} | {score.exact_completion_rate:.3f} | "
            f"{score.mechanism_macro_f1:.3f} | "
            f"{score.resource_silent_accuracy:.3f} | "
            f"{score.premature_no_incident_rate:.3f} | "
            f"{score.diagnosis_after_read_rate:.3f} | "
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
                f"- Oracle-path hit: {gate.oracle_path_action_hit_rate:.3f}",
                f"- Exact gain: {gate.exact_case_gain_over_model_legacy}",
                f"- Macro-F1 gain: {gate.mechanism_macro_f1_gain_over_model_legacy:.3f}",
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
    parser = argparse.ArgumentParser(description="DTA v2.2.3 2x2 study")
    parser.add_argument("mode", choices=("development", "evaluation"))
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--case-set", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--development-iteration", type=int, choices=(1, 2))
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--minimum-request-interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser


def _verify_final_manifest(
    *,
    manifest_path: Path,
    repository_root: Path,
    case_set: Path,
    truth: Path,
    prior: Path,
    provider_model: str,
    minimum_request_interval: float,
    output_json: Path,
    output_markdown: Path,
) -> str:
    raw = json.loads(manifest_path.read_bytes())
    if raw.get("schema_version") != "dta-v22.3.evaluation-manifest.v1":
        raise ValueError("v2.2.3 evaluation manifest schema differs")
    expected = {
        "case_set": case_set,
        "truth_set": truth,
        "predicate_yield_prior": prior,
    }
    for name, supplied in expected.items():
        item = raw.get(name)
        if not isinstance(item, dict):
            raise ValueError(f"v2.2.3 manifest lacks {name}")
        canonical = (repository_root / str(item.get("path"))).resolve()
        if canonical != supplied.resolve() or item.get("sha256") != _sha256(supplied):
            raise ValueError(f"v2.2.3 manifest {name} binding differs")
    if raw.get("provider_model") != provider_model:
        raise ValueError("v2.2.3 manifest Provider model differs")
    if raw.get("prompt_sha256") != _text_sha256(SHARED_SELECTION_SYSTEM_PROMPT_V223):
        raise ValueError("v2.2.3 manifest prompt binding differs")
    if raw.get("full_study_execution_count") != 1:
        raise ValueError("v2.2.3 final execution count differs")
    if raw.get("execution_state") != "NOT_STARTED":
        raise ValueError("v2.2.3 manifest execution state differs")
    if raw.get("minimum_request_interval_seconds") != minimum_request_interval:
        raise ValueError("v2.2.3 final Provider pacing differs")
    expected_outputs = (
        repository_root / "docs/results/dta-v22-3-admission-dispatch-evaluation.json",
        repository_root / "docs/results/dta-v22-3-admission-dispatch-evaluation.md",
    )
    if (output_json.resolve(), output_markdown.resolve()) != tuple(
        item.resolve() for item in expected_outputs
    ):
        raise ValueError("v2.2.3 final output paths differ")
    return _sha256(manifest_path)


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
    if phase == "EVALUATION":
        assert args.manifest is not None
        manifest_sha256 = _verify_final_manifest(
            manifest_path=args.manifest,
            repository_root=args.repository_root,
            case_set=args.case_set,
            truth=args.truth,
            prior=args.prior,
            provider_model=config.model,
            minimum_request_interval=args.minimum_request_interval,
            output_json=args.output_json,
            output_markdown=args.output_markdown,
        )
    provider = SelectionProviderV223(
        config=config,
        minimum_request_interval_seconds=args.minimum_request_interval,
        timeout_seconds=args.timeout,
        debug_root=args.repository_root / ".local/dta-v22-3-debug",
    )
    partial = args.output_json.with_suffix(args.output_json.suffix + ".partial.jsonl")
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial_handle = partial.open("x", encoding="utf-8")

    def observe(run: AdmissionDispatchCaseRunV223) -> None:
        partial_handle.write(run.model_dump_json() + "\n")
        partial_handle.flush()
        print(
            json.dumps(
                {
                    "case_id": run.case_id,
                    "combination": run.combination.value,
                    "terminal": run.terminal,
                    "reads": run.adaptive_reads,
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
        campaign = run_admission_dispatch_campaign_v223(
            repository_root=args.repository_root,
            case_set_path=args.case_set,
            truth_path=args.truth,
            provider=provider,
            predicate_yield_priors=priors,
            observer=observe,
        )
    finally:
        partial_handle.close()
    utility = audit_case_set_v222(
        repository_root=args.repository_root,
        case_set_path=args.case_set,
        truth_path=args.truth,
    )
    scores = score_admission_dispatch_study_v223(
        runs=campaign.runs,
        truths=campaign.truths,
        utility_audit=utility,
        include_development_gate=phase == "DEVELOPMENT",
        include_interpretation=phase == "EVALUATION",
    )
    artifact = AdmissionDispatchStudyArtifactV223(
        schema_version="dta-v22.3.admission-dispatch-study-artifact.v1",
        phase=phase,
        execution_count=int(phase == "EVALUATION"),
        development_iteration=args.development_iteration,
        provider_model=config.model,
        prompt_sha256=_text_sha256(SHARED_SELECTION_SYSTEM_PROMPT_V223),
        case_set_sha256=_sha256(args.case_set),
        truth_set_sha256=_sha256(args.truth),
        predicate_yield_prior_sha256=_sha256(args.prior),
        manifest_sha256=manifest_sha256,
        campaign=campaign,
        scores=scores,
        uncaught_exceptions=campaign.uncaught_exceptions,
        agent_writes=0,
    )
    _write_once(args.output_json, artifact.model_dump_json(indent=2) + "\n")
    _write_once(args.output_markdown, _markdown(artifact))
    partial.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("AdmissionDispatchStudyArtifactV223", "main")
