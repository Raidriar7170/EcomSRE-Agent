"""Frozen bindings for the single DTA v2.2.2 final 2x2 study."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.v22.gap_study_campaign_v222 import StudyCombinationV222
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22


class FrozenFileBindingV222(DtaModelV22):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationManifestV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.gap-routing-evaluation-manifest.v1"]
    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    implementation_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    model: str
    prompt: FrozenFileBindingV222
    case_set: FrozenFileBindingV222
    truth_set: FrozenFileBindingV222
    utility_audit: FrozenFileBindingV222
    development_result: FrozenFileBindingV222
    policy_source: FrozenFileBindingV222
    router_source: FrozenFileBindingV222
    runner_source: FrozenFileBindingV222
    scorer_source: FrozenFileBindingV222
    selection_source: FrozenFileBindingV222
    historical_results_manifest: FrozenFileBindingV222
    agent_visible_sources: tuple[FrozenFileBindingV222, ...] = Field(min_length=16, max_length=16)
    combinations: tuple[StudyCombinationV222, ...]
    expected_cases: Literal[16]
    expected_runs: Literal[64]
    core_incident_path_1_or_2: Literal[10]
    counterfactual_pairs: Literal[4]
    tempting_empty_cases: StrictInt = Field(ge=4, le=16)
    non_byte_identical_to_previous: StrictInt = Field(ge=8, le=16)
    minimum_request_interval_seconds: Literal[4]
    maximum_protocol_repairs_per_turn: Literal[2]
    maximum_transport_retries_per_request: Literal[3]
    single_execution_rule: Literal["EXACTLY_ONE_FULL_STUDY_EXECUTION"]
    schedule_rule: Literal["DETERMINISTIC_BALANCED_ROTATION_INTERLEAVED_BY_CASE"]
    truth_isolation_rule: Literal["LOAD_ONLY_AFTER_ALL_FOUR_CASE_RUNS"]
    docker_calls: Literal[0]
    agent_writes: Literal[0]
    runbook_calls: Literal[0]

    @model_validator(mode="after")
    def require_manifest(self) -> "EvaluationManifestV222":
        if self.combinations != tuple(StudyCombinationV222):
            raise ValueError("evaluation manifest combinations differ")
        expected_paths = tuple(
            f"config/dta-v22-2/evaluation/agent-visible/e{index:02d}.json"
            for index in range(1, 17)
        )
        if tuple(item.path for item in self.agent_visible_sources) != expected_paths:
            raise ValueError("evaluation manifest source bindings differ")
        return self


_BINDING_PATHS = {
    "prompt": "config/dta-v22-2/prompt.txt",
    "case_set": "config/dta-v22-2/evaluation/cases.json",
    "truth_set": "config/dta-v22-2/evaluation/truth.json",
    "utility_audit": "config/dta-v22-2/evaluation/utility-audit.json",
    "development_result": "docs/results/dta-v22-2-gap-routing-development.json",
    "policy_source": "src/ecomsre/dta_v2/v22/effective_policy_v222.py",
    "router_source": "src/ecomsre/dta_v2/v22/gap_router_v222.py",
    "runner_source": "src/ecomsre/dta_v2/v22/gap_study_runner_v222.py",
    "scorer_source": "src/ecomsre/dta_v2/v22/gap_study_scorer_v222.py",
    "selection_source": "src/ecomsre/dta_v2/v22/selection_provider_v222.py",
    "historical_results_manifest": "config/dta-v22-2/historical-results.v1.json",
}


def sha256_file_v222(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _binding(*, repository_root: Path, relative: str) -> FrozenFileBindingV222:
    return FrozenFileBindingV222(
        path=relative,
        sha256=sha256_file_v222(repository_root / relative),
    )


def build_evaluation_manifest_v222(
    *, repository_root: Path, implementation_commit: str, model: str
) -> EvaluationManifestV222:
    bindings = {
        name: _binding(repository_root=repository_root, relative=relative)
        for name, relative in _BINDING_PATHS.items()
    }
    sources = tuple(
        _binding(
            repository_root=repository_root,
            relative=f"config/dta-v22-2/evaluation/agent-visible/e{index:02d}.json",
        )
        for index in range(1, 17)
    )
    return EvaluationManifestV222(
        schema_version="dta-v22.2.gap-routing-evaluation-manifest.v1",
        base_commit="b1418ff202831d809f85a2902e28b169a38e73d2",
        implementation_commit=implementation_commit,
        model=model,
        **bindings,
        agent_visible_sources=sources,
        combinations=tuple(StudyCombinationV222),
        expected_cases=16,
        expected_runs=64,
        core_incident_path_1_or_2=10,
        counterfactual_pairs=4,
        tempting_empty_cases=16,
        non_byte_identical_to_previous=16,
        minimum_request_interval_seconds=4,
        maximum_protocol_repairs_per_turn=2,
        maximum_transport_retries_per_request=3,
        single_execution_rule="EXACTLY_ONE_FULL_STUDY_EXECUTION",
        schedule_rule="DETERMINISTIC_BALANCED_ROTATION_INTERLEAVED_BY_CASE",
        truth_isolation_rule="LOAD_ONLY_AFTER_ALL_FOUR_CASE_RUNS",
        docker_calls=0,
        agent_writes=0,
        runbook_calls=0,
    )


def load_and_verify_evaluation_manifest_v222(
    *, manifest_path: Path, repository_root: Path, configured_model: str
) -> EvaluationManifestV222:
    manifest = EvaluationManifestV222.model_validate_json(manifest_path.read_bytes())
    if manifest.model != configured_model:
        raise ValueError("configured model differs from frozen evaluation manifest")
    bindings = (
        manifest.prompt,
        manifest.case_set,
        manifest.truth_set,
        manifest.utility_audit,
        manifest.development_result,
        manifest.policy_source,
        manifest.router_source,
        manifest.runner_source,
        manifest.scorer_source,
        manifest.selection_source,
        manifest.historical_results_manifest,
        *manifest.agent_visible_sources,
    )
    for binding in bindings:
        path = Path(binding.path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("evaluation manifest path escapes the repository")
        if sha256_file_v222(repository_root / path) != binding.sha256:
            raise ValueError(f"frozen evaluation binding differs: {binding.path}")
    return manifest


__all__ = (
    "EvaluationManifestV222",
    "FrozenFileBindingV222",
    "build_evaluation_manifest_v222",
    "load_and_verify_evaluation_manifest_v222",
    "sha256_file_v222",
)
