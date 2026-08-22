"""Complete source-freeze bindings for the DTA v2.2.5 final study."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from pydantic import Field, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.ambiguity_bundle_campaign_v225 import (
    StudyCombinationV225,
    balanced_combination_order_v225,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22


BASE_MAIN_COMMIT_V225 = "9c601bd5d802fbe31990348c228e094985044a0b"
PROVIDER_MODEL_V225 = "gpt-5.4-mini-2026-03-17"
MINIMUM_REQUEST_INTERVAL_SECONDS_V225 = 4.0
TIMEOUT_SECONDS_V225 = 120.0
MAXIMUM_PROTOCOL_REPAIRS_PER_CASE_V225: Literal[2] = 2
MAXIMUM_TRANSPORT_RETRIES_PER_REQUEST_V225: Literal[3] = 3

PROMPT_PATH_V225 = "config/dta-v22-5/prompt.txt"
CASE_SET_PATH_V225 = "config/dta-v22-5/evaluation/cases.json"
TRUTH_SET_PATH_V225 = "config/dta-v22-5/evaluation/truth.json"
COVERAGE_PATH_V225 = "config/dta-v22-5/evaluation/coverage.json"
UTILITY_AUDIT_PATH_V225 = "config/dta-v22-5/evaluation/utility-audit.json"
STRATA_PATH_V225 = "config/dta-v22-5/evaluation/strata.json"
IDENTITY_PLAN_PATH_V225 = "config/dta-v22-5/evaluation/opaque-identity-plan.json"
LINT_REPORT_PATH_V225 = "config/dta-v22-5/evaluation/provider-payload-lint.json"
HISTORICAL_RESULTS_PATH_V225 = "config/dta-v22-5/historical-results.v1.json"
PREDICATE_YIELD_PRIOR_PATH_V225 = "config/dta-v22-3/development-predicate-yield-prior.json"
DEVELOPMENT_RESULT_PATH_V225 = (
    "docs/results/dta-v22-5-opaque-ambiguity-development.json"
)
MANIFEST_PATH_V225 = "config/dta-v22-5/evaluation/manifest.json"
PRE_EXECUTION_REVIEW_PATH_V225 = (
    "docs/external-reviews/dta-v22-5-pre-execution-review.md"
)
OUTPUT_JSON_PATH_V225 = (
    "docs/results/dta-v22-5-opaque-ambiguity-evaluation.json"
)
OUTPUT_MARKDOWN_PATH_V225 = (
    "docs/results/dta-v22-5-opaque-ambiguity-evaluation.md"
)
PARTIAL_JOURNAL_PATH_V225 = OUTPUT_JSON_PATH_V225 + ".partial.jsonl"


class GitQueryV225(Protocol):
    def bytes(self, *arguments: str, check: bool = True) -> bytes: ...

    def text(self, *arguments: str, check: bool = True) -> str: ...

    def succeeds(self, *arguments: str) -> bool: ...


class FrozenFileBindingV225(DtaModelV22):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_relative_path(self) -> "FrozenFileBindingV225":
        path = PurePosixPath(self.path)
        if path.is_absolute() or ".." in path.parts or str(path) != self.path:
            raise ValueError("v2.2.5 manifest path escapes the repository")
        return self


class StudyScheduleEntryManifestV225(DtaModelV22):
    ordinal: StrictInt = Field(ge=1, le=64)
    case_id: str = Field(pattern=r"^e[0-9]{2}$")
    execution_position: StrictInt = Field(ge=1, le=4)
    combination: StudyCombinationV225


class EvaluationManifestV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.evaluation-manifest.v1"]
    base_main_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_freeze_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_tree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_execution_id: str = Field(pattern=r"^dta-v225-[0-9a-f]{24}$")
    provider_model: str
    prompt: FrozenFileBindingV225
    minimum_request_interval_seconds: StrictFloat = Field(ge=0)
    timeout_seconds: StrictFloat = Field(gt=0)
    maximum_protocol_repairs_per_case: Literal[2]
    maximum_transport_retries_per_request: Literal[3]
    single_execution_rule: Literal["EXACTLY_ONE_FULL_STUDY_EXECUTION"]
    execution_state: Literal["NOT_STARTED"]
    case_set: FrozenFileBindingV225
    truth_set: FrozenFileBindingV225
    target_coverage: FrozenFileBindingV225
    utility_audit: FrozenFileBindingV225
    evaluator_strata: FrozenFileBindingV225
    opaque_identity_plan: FrozenFileBindingV225
    opaque_lint_report: FrozenFileBindingV225
    historical_results_manifest: FrozenFileBindingV225
    predicate_yield_prior: FrozenFileBindingV225
    development_result: FrozenFileBindingV225
    agent_visible_sources: tuple[FrozenFileBindingV225, ...] = Field(
        min_length=16, max_length=16
    )
    implementation_sources: tuple[FrozenFileBindingV225, ...] = Field(min_length=1)
    v22_runtime_tree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schedule: tuple[StudyScheduleEntryManifestV225, ...] = Field(
        min_length=64, max_length=64
    )
    schedule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_case_count: Literal[16]
    expected_run_count: Literal[64]
    expected_output_paths: tuple[str, str, str]
    expected_repair_retry_accounting: dict[str, int]
    allowed_post_freeze_paths: tuple[str, ...]
    agent_writes: Literal[0]
    docker_calls: Literal[0]
    runbook_calls: Literal[0]

    @model_validator(mode="after")
    def require_complete_manifest(self) -> "EvaluationManifestV225":
        expected_agent_paths = tuple(
            f"config/dta-v22-5/evaluation/agent-visible/e{index:02d}.json"
            for index in range(1, 17)
        )
        if tuple(item.path for item in self.agent_visible_sources) != expected_agent_paths:
            raise ValueError("v2.2.5 agent-visible source bindings differ")
        implementation_paths = tuple(item.path for item in self.implementation_sources)
        if implementation_paths != tuple(sorted(set(implementation_paths))):
            raise ValueError("v2.2.5 implementation sources are not canonical")
        if self.schedule != build_schedule_v225(expected_agent_paths):
            raise ValueError("v2.2.5 manifest schedule differs")
        if self.schedule_sha256 != schedule_sha256_v225(self.schedule):
            raise ValueError("v2.2.5 manifest schedule digest differs")
        if self.expected_output_paths != (
            OUTPUT_JSON_PATH_V225,
            OUTPUT_MARKDOWN_PATH_V225,
            PARTIAL_JOURNAL_PATH_V225,
        ):
            raise ValueError("v2.2.5 expected output paths differ")
        if self.expected_repair_retry_accounting != {
            "maximum_protocol_repairs_per_case": 2,
            "maximum_provider_calls_per_turn": 3,
            "maximum_transport_retries_per_request": 3,
        }:
            raise ValueError("v2.2.5 repair/retry accounting differs")
        if set(self.allowed_post_freeze_paths) != {
            LINT_REPORT_PATH_V225,
            MANIFEST_PATH_V225,
            PRE_EXECUTION_REVIEW_PATH_V225,
        }:
            raise ValueError("v2.2.5 post-freeze allowlist differs")
        return self


def sha256_file_v225(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_bindings_sha256_v225(
    bindings: tuple[FrozenFileBindingV225, ...],
) -> str:
    return semantic_sha256_v22([item.model_dump(mode="json") for item in bindings])


def source_tree_sha256_v225(*, git_query: GitQueryV225, commit: str) -> str:
    """Freshly SHA-256 every tracked blob in one exact Git tree."""

    records = tuple(
        record
        for record in git_query.bytes(
            "ls-tree", "-r", "-z", "--full-tree", commit
        ).split(b"\0")
        if record
    )
    digest = hashlib.sha256()
    for record in records:
        metadata, relative_bytes = record.split(b"\t", 1)
        _, object_type, object_id = metadata.split(b" ", 2)
        relative = relative_bytes.decode("utf-8")
        if object_type == b"blob":
            content = git_query.bytes("show", f"{commit}:{relative}")
        elif object_type == b"commit":
            content = b"gitlink\0" + object_id
        else:
            raise ValueError(f"v2.2.5 unsupported Git tree object: {object_type!r}")
        digest.update(relative_bytes)
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def build_schedule_v225(
    source_paths: tuple[str, ...] | None = None,
) -> tuple[StudyScheduleEntryManifestV225, ...]:
    paths = source_paths or tuple(
        f"config/dta-v22-5/evaluation/agent-visible/e{index:02d}.json"
        for index in range(1, 17)
    )
    entries: list[StudyScheduleEntryManifestV225] = []
    for case_index, path in enumerate(paths):
        case_id = PurePosixPath(path).stem
        for position, combination in enumerate(
            balanced_combination_order_v225(case_index), start=1
        ):
            entries.append(
                StudyScheduleEntryManifestV225(
                    ordinal=len(entries) + 1,
                    case_id=case_id,
                    execution_position=position,
                    combination=combination,
                )
            )
    return tuple(entries)


def schedule_sha256_v225(
    schedule: tuple[StudyScheduleEntryManifestV225, ...],
) -> str:
    return semantic_sha256_v22([item.model_dump(mode="json") for item in schedule])


def _binding(repository_root: Path, relative: str) -> FrozenFileBindingV225:
    return FrozenFileBindingV225(
        path=relative,
        sha256=sha256_file_v225(repository_root / relative),
    )


def _runtime_source_paths(repository_root: Path) -> tuple[str, ...]:
    root = repository_root / "src/ecomsre/dta_v2/v22"
    return tuple(
        path.relative_to(repository_root).as_posix()
        for path in sorted(root.rglob("*.py"))
    )


def build_evaluation_manifest_v225(
    *,
    repository_root: Path,
    source_freeze_commit: str,
    git_query: GitQueryV225,
) -> EvaluationManifestV225:
    commit = git_query.text("rev-parse", f"{source_freeze_commit}^{{commit}}")
    source_paths = tuple(
        f"config/dta-v22-5/evaluation/agent-visible/e{index:02d}.json"
        for index in range(1, 17)
    )
    implementation_paths = _runtime_source_paths(repository_root)
    implementation_sources = tuple(
        _binding(repository_root, relative) for relative in implementation_paths
    )
    schedule = build_schedule_v225(source_paths)
    schedule_sha256 = schedule_sha256_v225(schedule)
    execution_seed = semantic_sha256_v22(
        {
            "source_freeze_commit": commit,
            "schedule_sha256": schedule_sha256,
            "case_set_sha256": sha256_file_v225(repository_root / CASE_SET_PATH_V225),
        }
    )
    return EvaluationManifestV225(
        schema_version="dta-v22.5.evaluation-manifest.v1",
        base_main_commit=BASE_MAIN_COMMIT_V225,
        source_freeze_commit=commit,
        source_tree_sha256=source_tree_sha256_v225(
            git_query=git_query, commit=commit
        ),
        evaluation_execution_id=f"dta-v225-{execution_seed[:24]}",
        provider_model=PROVIDER_MODEL_V225,
        prompt=_binding(repository_root, PROMPT_PATH_V225),
        minimum_request_interval_seconds=MINIMUM_REQUEST_INTERVAL_SECONDS_V225,
        timeout_seconds=TIMEOUT_SECONDS_V225,
        maximum_protocol_repairs_per_case=MAXIMUM_PROTOCOL_REPAIRS_PER_CASE_V225,
        maximum_transport_retries_per_request=MAXIMUM_TRANSPORT_RETRIES_PER_REQUEST_V225,
        single_execution_rule="EXACTLY_ONE_FULL_STUDY_EXECUTION",
        execution_state="NOT_STARTED",
        case_set=_binding(repository_root, CASE_SET_PATH_V225),
        truth_set=_binding(repository_root, TRUTH_SET_PATH_V225),
        target_coverage=_binding(repository_root, COVERAGE_PATH_V225),
        utility_audit=_binding(repository_root, UTILITY_AUDIT_PATH_V225),
        evaluator_strata=_binding(repository_root, STRATA_PATH_V225),
        opaque_identity_plan=_binding(repository_root, IDENTITY_PLAN_PATH_V225),
        opaque_lint_report=_binding(repository_root, LINT_REPORT_PATH_V225),
        historical_results_manifest=_binding(
            repository_root, HISTORICAL_RESULTS_PATH_V225
        ),
        predicate_yield_prior=_binding(
            repository_root, PREDICATE_YIELD_PRIOR_PATH_V225
        ),
        development_result=_binding(repository_root, DEVELOPMENT_RESULT_PATH_V225),
        agent_visible_sources=tuple(
            _binding(repository_root, relative) for relative in source_paths
        ),
        implementation_sources=implementation_sources,
        v22_runtime_tree_sha256=canonical_bindings_sha256_v225(
            implementation_sources
        ),
        schedule=schedule,
        schedule_sha256=schedule_sha256,
        expected_case_count=16,
        expected_run_count=64,
        expected_output_paths=(
            OUTPUT_JSON_PATH_V225,
            OUTPUT_MARKDOWN_PATH_V225,
            PARTIAL_JOURNAL_PATH_V225,
        ),
        expected_repair_retry_accounting={
            "maximum_protocol_repairs_per_case": 2,
            "maximum_provider_calls_per_turn": 3,
            "maximum_transport_retries_per_request": 3,
        },
        allowed_post_freeze_paths=tuple(
            sorted(
                (
                    LINT_REPORT_PATH_V225,
                    MANIFEST_PATH_V225,
                    PRE_EXECUTION_REVIEW_PATH_V225,
                )
            )
        ),
        agent_writes=0,
        docker_calls=0,
        runbook_calls=0,
    )


def write_evaluation_manifest_v225(
    *,
    repository_root: Path,
    source_freeze_commit: str,
    output_path: Path,
    git_query: GitQueryV225,
) -> EvaluationManifestV225:
    manifest = build_evaluation_manifest_v225(
        repository_root=repository_root,
        source_freeze_commit=source_freeze_commit,
        git_query=git_query,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        json.dump(manifest.model_dump(mode="json"), handle, indent=2, sort_keys=True)
        handle.write("\n")
    return manifest


__all__ = (
    "EvaluationManifestV225",
    "FrozenFileBindingV225",
    "GitQueryV225",
    "StudyScheduleEntryManifestV225",
    "build_evaluation_manifest_v225",
    "build_schedule_v225",
    "canonical_bindings_sha256_v225",
    "schedule_sha256_v225",
    "sha256_file_v225",
    "source_tree_sha256_v225",
    "write_evaluation_manifest_v225",
)
