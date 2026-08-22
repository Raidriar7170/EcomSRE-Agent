"""Fail-closed preflight for the frozen DTA v2.2.5 Provider execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import StrictInt

from ecomsre.dta_v2.v22.evaluation_manifest_v225 import (
    BASE_MAIN_COMMIT_V225,
    EvaluationManifestV225,
    GitQueryV225,
    PRE_EXECUTION_REVIEW_PATH_V225,
    build_schedule_v225,
    canonical_bindings_sha256_v225,
    schedule_sha256_v225,
    sha256_file_v225,
    source_tree_sha256_v225,
)
from ecomsre.dta_v2.v22.evaluation_strata_v225 import EvaluatorStrataV225
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22


class EvaluationPreflightReportV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.evaluation-preflight-report.v1"]
    status: Literal["DTA_V22_5_EVALUATION_PREFLIGHT_PASS"]
    manifest_sha256: str
    source_freeze_commit: str
    current_head: str
    agent_visible_sources_verified: Literal[16]
    implementation_sources_verified: StrictInt
    schedule_entries_verified: Literal[64]
    output_paths_absent: Literal[3]
    opaque_identity_lint_terminal: Literal["OPAQUE_PROVIDER_IDENTITY_LINT_PASS"]
    execution_state: Literal["NOT_STARTED"]
    agent_writes: Literal[0]


def verify_current_bindings_v225(
    *, manifest: EvaluationManifestV225, repository_root: Path
) -> None:
    bindings = (
        manifest.prompt,
        manifest.case_set,
        manifest.truth_set,
        manifest.target_coverage,
        manifest.utility_audit,
        manifest.evaluator_strata,
        manifest.opaque_identity_plan,
        manifest.opaque_lint_report,
        manifest.historical_results_manifest,
        manifest.predicate_yield_prior,
        manifest.development_result,
        *manifest.agent_visible_sources,
        *manifest.implementation_sources,
    )
    seen: set[str] = set()
    for binding in bindings:
        if binding.path in seen and binding not in manifest.agent_visible_sources:
            raise ValueError(f"duplicate v2.2.5 manifest binding: {binding.path}")
        seen.add(binding.path)
        path = repository_root / binding.path
        if not path.is_file():
            raise ValueError(f"missing v2.2.5 manifest path: {binding.path}")
        if sha256_file_v225(path) != binding.sha256:
            raise ValueError(f"v2.2.5 frozen binding differs: {binding.path}")
    if canonical_bindings_sha256_v225(manifest.implementation_sources) != (
        manifest.v22_runtime_tree_sha256
    ):
        raise ValueError("v2.2.5 runtime tree digest differs")


def verify_agent_visible_inventory_v225(
    *, manifest: EvaluationManifestV225, repository_root: Path
) -> None:
    expected = tuple(item.path for item in manifest.agent_visible_sources)
    actual = tuple(
        path.relative_to(repository_root).as_posix()
        for path in sorted(
            (repository_root / "config/dta-v22-5/evaluation/agent-visible").glob(
                "*.json"
            )
        )
    )
    if actual != expected:
        raise ValueError("v2.2.5 unlisted agent-visible evaluation file exists")
    case_set = json.loads((repository_root / manifest.case_set.path).read_bytes())
    cases = case_set.get("cases") if isinstance(case_set, dict) else None
    if not isinstance(cases, list) or len(cases) != 16:
        raise ValueError("v2.2.5 case-set composition differs")
    indexed: dict[str, tuple[str, str]] = {}
    for item in cases:
        if not isinstance(item, dict):
            raise ValueError("v2.2.5 case-set entry differs")
        case_id = item.get("case_id")
        source_path = item.get("source_path")
        source_sha256 = item.get("source_sha256")
        if not all(isinstance(value, str) for value in (case_id, source_path, source_sha256)):
            raise ValueError("v2.2.5 case source binding is incomplete")
        indexed[str(case_id)] = (str(source_path), str(source_sha256))
    if tuple(sorted(indexed)) != tuple(f"e{index:02d}" for index in range(1, 17)):
        raise ValueError("v2.2.5 case-set IDs differ")
    for case_id, (source_path, source_sha256) in indexed.items():
        expected_path = f"config/dta-v22-5/evaluation/agent-visible/{case_id}.json"
        if source_path != expected_path or sha256_file_v225(
            repository_root / source_path
        ) != source_sha256:
            raise ValueError(f"v2.2.5 case source hash differs: {case_id}")


def verify_strata_composition_v225(
    *, manifest: EvaluationManifestV225, repository_root: Path
) -> None:
    strata = EvaluatorStrataV225.model_validate_json(
        (repository_root / manifest.evaluator_strata.path).read_bytes()
    )
    truths = load_practical_truth_set_v22(
        repository_root / manifest.truth_set.path
    ).truths
    truth_by_case = {item.case_id: item for item in truths}
    if tuple(sorted(truth_by_case)) != strata.all_case_ids:
        raise ValueError("v2.2.5 evaluator strata do not cover the truth set")
    expected_mechanisms = {
        **{case_id: "CPU_SATURATION" for case_id in strata.cpu_incidents},
        **{case_id: "MEMORY_LEAK" for case_id in strata.memory_incidents},
        **{
            case_id: "CONFIGURATION_ERROR"
            for case_id in strata.configuration_incidents
        },
        **{
            case_id: "SERVICE_UNAVAILABLE"
            for case_id in strata.service_unavailable_incidents
        },
        **{
            case_id: "DEPENDENCY_LATENCY"
            for case_id in strata.dependency_incidents
        },
    }
    for case_id, expected in expected_mechanisms.items():
        if truth_by_case[case_id].expected_mechanism != expected:
            raise ValueError(f"v2.2.5 evaluator mechanism stratum differs: {case_id}")
    for case_id in strata.resource_normal_controls:
        if truth_by_case[case_id].expected_terminal != "NO_INCIDENT":
            raise ValueError(f"v2.2.5 normal-control stratum differs: {case_id}")
    for case_id in strata.abstention_controls:
        if truth_by_case[case_id].expected_terminal != "ABSTAIN":
            raise ValueError(f"v2.2.5 abstention-control stratum differs: {case_id}")


def verify_opaque_lint_report_v225(
    *, manifest: EvaluationManifestV225, repository_root: Path
) -> Literal["OPAQUE_PROVIDER_IDENTITY_LINT_PASS"]:
    raw = json.loads((repository_root / manifest.opaque_lint_report.path).read_bytes())
    if not isinstance(raw, dict):
        raise ValueError("v2.2.5 opaque identity lint report differs")
    if raw.get("terminal") != "OPAQUE_PROVIDER_IDENTITY_LINT_PASS":
        raise ValueError("v2.2.5 opaque identity lint did not pass")
    required_zero = (
        "forbidden_identity_value_count",
        "provider_case_id_count",
        "provider_evaluator_metadata_field_count",
    )
    if any(raw.get(name) != 0 for name in required_zero):
        raise ValueError("v2.2.5 opaque identity lint has nonzero violations")
    required_classes = {
        "bootstrap",
        "post-individual-read",
        "post-bundle-read",
        "terminal-only",
        "repair",
    }
    if set(raw.get("rendered_payload_classes", [])) != required_classes:
        raise ValueError("v2.2.5 rendered Provider payload classes are incomplete")
    if raw.get("evaluation_files_scanned") != 16:
        raise ValueError("v2.2.5 opaque lint evaluation file count differs")
    required_render_counts = {
        "evaluation_runs_rendered": 64,
        "runtime_payloads_rendered": 64,
        "synthetic_protocol_payloads_rendered": 2,
    }
    if any(raw.get(name) != expected for name, expected in required_render_counts.items()):
        raise ValueError("v2.2.5 opaque lint runtime render count differs")
    if not isinstance(raw.get("rendered_reports"), list) or len(raw["rendered_reports"]) != 66:
        raise ValueError("v2.2.5 opaque lint rendered report inventory differs")
    return "OPAQUE_PROVIDER_IDENTITY_LINT_PASS"


def verify_outputs_absent_v225(
    *, manifest: EvaluationManifestV225, repository_root: Path
) -> None:
    present = [path for path in manifest.expected_output_paths if (repository_root / path).exists()]
    if present:
        raise ValueError("v2.2.5 final output already exists: " + ", ".join(present))


def _verify_git_freeze_v225(
    *,
    manifest: EvaluationManifestV225,
    repository_root: Path,
    git_query: GitQueryV225,
) -> str:
    if git_query.text("status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("v2.2.5 execution checkout is not clean")
    head = git_query.text("rev-parse", "HEAD")
    for ancestor, descendant, label in (
        (BASE_MAIN_COMMIT_V225, manifest.source_freeze_commit, "base/source-freeze"),
        (manifest.source_freeze_commit, head, "source-freeze/current HEAD"),
    ):
        if not git_query.succeeds(
            "merge-base", "--is-ancestor", ancestor, descendant
        ):
            raise ValueError(f"v2.2.5 {label} ancestry differs")
    changed = set(
        filter(
            None,
            git_query.text(
                "diff",
                "--name-only",
                f"{manifest.source_freeze_commit}..{head}",
            ).splitlines(),
        )
    )
    allowed = set(manifest.allowed_post_freeze_paths)
    if not changed.issubset(allowed):
        raise ValueError(
            "v2.2.5 undeclared post-freeze path changed: "
            + ", ".join(sorted(changed - allowed))
        )
    required = {manifest.opaque_lint_report.path, "config/dta-v22-5/evaluation/manifest.json"}
    if not required.issubset(changed):
        raise ValueError("v2.2.5 manifest/lint commit is absent after source freeze")
    if source_tree_sha256_v225(
        git_query=git_query,
        commit=manifest.source_freeze_commit,
    ) != manifest.source_tree_sha256:
        raise ValueError("v2.2.5 source-freeze tree digest differs")
    return head


def _verify_pre_execution_review_v225(repository_root: Path) -> None:
    path = repository_root / PRE_EXECUTION_REVIEW_PATH_V225
    if not path.is_file():
        raise ValueError("v2.2.5 independent pre-execution review is absent")
    text = path.read_text(encoding="utf-8")
    required = (
        "Verdict: PASS",
        "Must Fix: 0",
        "Claim Accuracy: PASS",
    )
    if not all(marker in text for marker in required):
        raise ValueError("v2.2.5 independent pre-execution review did not pass")


def preflight_evaluation_v225(
    *,
    manifest_path: Path,
    repository_root: Path,
    configured_model: str,
    minimum_request_interval_seconds: float,
    timeout_seconds: float,
    case_set_path: Path,
    truth_path: Path,
    coverage_path: Path,
    strata_path: Path,
    predicate_yield_prior_path: Path,
    output_json_path: Path,
    output_markdown_path: Path,
    git_query: GitQueryV225,
) -> EvaluationPreflightReportV225:
    manifest = EvaluationManifestV225.model_validate_json(manifest_path.read_bytes())
    if manifest.provider_model != configured_model:
        raise ValueError("v2.2.5 configured Provider model differs")
    if manifest.minimum_request_interval_seconds != minimum_request_interval_seconds:
        raise ValueError("v2.2.5 configured Provider pacing differs")
    if manifest.timeout_seconds != timeout_seconds:
        raise ValueError("v2.2.5 configured Provider timeout differs")
    if manifest.execution_state != "NOT_STARTED":
        raise ValueError("v2.2.5 full-study execution is not NOT_STARTED")
    supplied = (
        (case_set_path, manifest.case_set.path),
        (truth_path, manifest.truth_set.path),
        (coverage_path, manifest.target_coverage.path),
        (strata_path, manifest.evaluator_strata.path),
        (predicate_yield_prior_path, manifest.predicate_yield_prior.path),
        (output_json_path, manifest.expected_output_paths[0]),
        (output_markdown_path, manifest.expected_output_paths[1]),
    )
    for path, relative in supplied:
        if path.resolve() != (repository_root / relative).resolve():
            raise ValueError(f"v2.2.5 supplied execution path differs: {relative}")
    head = _verify_git_freeze_v225(
        manifest=manifest,
        repository_root=repository_root,
        git_query=git_query,
    )
    verify_current_bindings_v225(manifest=manifest, repository_root=repository_root)
    current_runtime_paths = tuple(
        path.relative_to(repository_root).as_posix()
        for path in sorted((repository_root / "src/ecomsre/dta_v2/v22").rglob("*.py"))
    )
    if current_runtime_paths != tuple(item.path for item in manifest.implementation_sources):
        raise ValueError("v2.2.5 implementation source inventory differs")
    verify_agent_visible_inventory_v225(
        manifest=manifest, repository_root=repository_root
    )
    verify_strata_composition_v225(manifest=manifest, repository_root=repository_root)
    schedule = build_schedule_v225()
    if schedule != manifest.schedule or schedule_sha256_v225(schedule) != manifest.schedule_sha256:
        raise ValueError("v2.2.5 regenerated schedule differs")
    lint_terminal = verify_opaque_lint_report_v225(
        manifest=manifest, repository_root=repository_root
    )
    verify_outputs_absent_v225(manifest=manifest, repository_root=repository_root)
    _verify_pre_execution_review_v225(repository_root)
    return EvaluationPreflightReportV225(
        schema_version="dta-v22.5.evaluation-preflight-report.v1",
        status="DTA_V22_5_EVALUATION_PREFLIGHT_PASS",
        manifest_sha256=sha256_file_v225(manifest_path),
        source_freeze_commit=manifest.source_freeze_commit,
        current_head=head,
        agent_visible_sources_verified=16,
        implementation_sources_verified=len(manifest.implementation_sources),
        schedule_entries_verified=64,
        output_paths_absent=3,
        opaque_identity_lint_terminal=lint_terminal,
        execution_state="NOT_STARTED",
        agent_writes=0,
    )


__all__ = (
    "EvaluationPreflightReportV225",
    "preflight_evaluation_v225",
    "verify_agent_visible_inventory_v225",
    "verify_current_bindings_v225",
    "verify_opaque_lint_report_v225",
    "verify_outputs_absent_v225",
    "verify_strata_composition_v225",
)
