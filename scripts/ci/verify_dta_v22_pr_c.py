"""Verify the DTA v2.2 PR-C memory, predicate, and diagnosis boundary."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from pathlib import Path
import stat
import subprocess
from typing import Any, get_args, Sequence

from ecomsre.dta_v2.v22.diagnosis import (
    DiagnosisTerminalV22,
    filter_candidates_v22,
)
from ecomsre.dta_v2.v22.memory import (
    FullEvidenceMemoryV22,
    PredicateThresholdsV22,
    RuntimeSalientPayloadV22,
    SalientEvidenceMemoryV22,
    build_memory_views_v22,
)
from ecomsre.dta_v2.v22.memory_benchmark import (
    FixedTrajectoryMemoryBenchmarkV22,
    benchmark_fixed_trajectory_v22,
)
from ecomsre.dta_v2.v22.predicates import (
    PredicateExtractorV22,
    build_default_evidence_support_policy_v22,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from scripts.ci.verify_dta_v22_pr_a import assert_no_public_leak
from scripts.ci.verify_dta_v22_pr_b import verify_pr_b_protocol


PR_C_BASE = "8e42b6d212e24ddc94ac4097da7e3e3aae57da98"
PR_C_MANIFEST = Path("config/dta-v22/pr-c-memory-predicate-bindings.v1.json")
PR_C_SUCCESSOR_ATTESTATION = Path(
    "config/dta-v22/pr-c-successor-attestation.v1.json"
)
EXPECTED_MANIFEST_SHA256 = (
    "016a7361104d53326215c15c79a7442f8390dfbf5a7d367593e5222aa51432b1"
)
EXPECTED_PR_C_CHANGED_PATHS = (
    Path(".github/workflows/agent-mainline.yml"),
    Path("config/dta-v22/pr-b-successor-attestation.v1.json"),
    PR_C_MANIFEST,
    Path("docs/analysis/dta-v22-p0-master-progress.json"),
    Path("docs/human-briefs/2026-08-20-dta-v22-pr-c-memory-predicates.md"),
    Path("scripts/ci/verify_dta_v22_pr_c.py"),
    Path("src/ecomsre/dta_v2/v22/__init__.py"),
    Path("src/ecomsre/dta_v2/v22/diagnosis.py"),
    Path("src/ecomsre/dta_v2/v22/memory.py"),
    Path("src/ecomsre/dta_v2/v22/memory_benchmark.py"),
    Path("src/ecomsre/dta_v2/v22/predicates.py"),
    Path("tests/dta_v22/test_v22_memory_predicates_diagnosis.py"),
    Path("tests/dta_v22/test_v22_pr_c_verifier.py"),
)
PERSISTENT_PR_C_ARTIFACTS = (
    PR_C_MANIFEST,
    PR_C_SUCCESSOR_ATTESTATION,
    Path("docs/human-briefs/2026-08-20-dta-v22-pr-c-memory-predicates.md"),
    Path("src/ecomsre/dta_v2/v22/diagnosis.py"),
    Path("src/ecomsre/dta_v2/v22/memory.py"),
    Path("src/ecomsre/dta_v2/v22/memory_benchmark.py"),
    Path("src/ecomsre/dta_v2/v22/predicates.py"),
    Path("tests/dta_v22/test_v22_memory_predicates_diagnosis.py"),
)
EXPECTED_ARTIFACT_PATHS = (
    "src/ecomsre/dta_v2/v22/diagnosis.py",
    "src/ecomsre/dta_v2/v22/memory.py",
    "src/ecomsre/dta_v2/v22/memory_benchmark.py",
    "src/ecomsre/dta_v2/v22/predicates.py",
    "tests/dta_v22/test_v22_memory_predicates_diagnosis.py",
)
EXPECTED_FALSE_ACTIVITY_FIELDS = (
    "provider_called",
    "docker_called",
    "held_out_executed",
    "scenario_executed",
    "fault_injected",
    "runbook_executed",
    "private_evidence_changed",
    "public_result_changed",
    "execution_report_rebound",
)
EXPECTED_SUCCESSOR_ATTESTATION_FIELDS = (
    "schema_version",
    "goal_version",
    "decision_id",
    "repository",
    "source_stage",
    "source_pr",
    "source_candidate_head",
    "source_candidate_tree",
    "source_merge_commit",
    "source_merge_tree",
    "successor_stage",
    "successor_pr",
    "successor_branch",
    "base_main_head",
    "successor_head",
    "successor_tree",
    "changed_paths",
    "raw_sha256_by_path",
    *EXPECTED_FALSE_ACTIVITY_FIELDS,
    "record_sha256",
)
EXPECTED_GIT_PROOFS = [
    "source_candidate_matches_pull_ref",
    "source_candidate_tree_matches_single_parent_squash_tree",
    "source_candidate_is_not_merge_ancestor",
    "source_merge_is_current_ancestor_and_subject_binds_pr",
    "source_candidate_and_merge_trees_bind_manifest",
    "successor_head_tree_and_changed_paths_match_base_diff",
    "every_successor_changed_file_matches_raw_sha256",
    "attestation_commit_is_single_file_child",
    "successor_final_head_matches_pull_ref",
]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path.name}")
    return value


def _regular_file(root: Path, relative: Path) -> Path:
    path = root / relative
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError(f"PR-C artifact must be a regular file: {relative}")
    if not path.resolve(strict=True).is_relative_to(root):
        raise ValueError(f"PR-C artifact escapes repository: {relative}")
    return path


def _git_text(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(root), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_paths(root: Path, *args: str) -> set[Path]:
    return {Path(item) for item in _git_text(root, *args).splitlines() if item}


def _is_sha(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _changed_text(root: Path, relative: Path) -> str:
    path = _regular_file(root, relative)
    if subprocess.run(
        ("git", "-C", str(root), "cat-file", "-e", f"{PR_C_BASE}:{relative}"),
        check=False,
        capture_output=True,
    ).returncode != 0:
        return path.read_text(encoding="utf-8")
    diff = _git_text(
        root,
        "diff",
        "--unified=0",
        PR_C_BASE,
        "--",
        relative.as_posix(),
    )
    return "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )


def _verify_closed_changed_surface(root: Path) -> None:
    observed = _git_paths(root, "diff", "--name-only", PR_C_BASE, "--")
    observed.update(_git_paths(root, "ls-files", "--others", "--exclude-standard"))
    expected = set(EXPECTED_PR_C_CHANGED_PATHS)
    if observed != expected:
        raise ValueError(
            "PR-C changed surface differs: "
            f"undeclared={sorted(str(item) for item in observed - expected)}, "
            f"missing={sorted(str(item) for item in expected - observed)}"
        )


def _validate_stage_record(value: object, *, stage: str) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"stage", "pr", "head_sha", "merge_commit"}
        or value.get("stage") != stage
        or not isinstance(value.get("pr"), int)
        or not _is_sha(value.get("head_sha"), 40)
        or not _is_sha(value.get("merge_commit"), 40)
    ):
        raise ValueError(f"progress lacks exact {stage} merge provenance")
    return value


def _verify_progress(progress: dict[str, Any]) -> None:
    if progress.get("current_stage") != "PR-C":
        raise ValueError("PR-C verifier requires current PR-C progress")
    if progress.get("completed_stage") != "PR-B":
        raise ValueError("PR-C progress does not prove PR-B completion")
    if progress.get("active_branch") != "codex/dta-v22-p0-pr-c-memory-predicates-diagnosis":
        raise ValueError("PR-C active branch differs")
    if not isinstance(progress.get("active_pr"), int) or progress["active_pr"] <= 58:
        raise ValueError("PR-C active PR differs")
    merged = progress.get("merged_prs")
    if not isinstance(merged, list) or len(merged) != 2:
        raise ValueError("PR-C merged PR sequence differs")
    pr_a = _validate_stage_record(merged[0], stage="PR-A")
    pr_b = _validate_stage_record(merged[1], stage="PR-B")
    if (
        pr_a["pr"] != 57
        or pr_a["head_sha"] != "57bc106ef181d4ca8bafb3eb00372c1084bd0c60"
        or pr_a["merge_commit"] != "9d53002c3d86208a67b73d271c5eaf6e2f45b8b7"
        or pr_b["pr"] != 58
        or pr_b["head_sha"] != "1dc968cedd5f08d2a490256d658efd84c191c3a6"
        or pr_b["merge_commit"] != PR_C_BASE
    ):
        raise ValueError("PR-C prior merge provenance differs")


def _public_scan_plan(
    root: Path,
    progress: dict[str, Any],
) -> tuple[str, tuple[Path, ...]]:
    if progress.get("current_stage") == "PR-C":
        _verify_progress(progress)
        return "PR_C_CLOSED_SURFACE", EXPECTED_PR_C_CHANGED_PATHS
    _require_pr_c_successor_progress(root, progress)
    return "SUCCESSOR_PERSISTENT_ARTIFACTS", PERSISTENT_PR_C_ARTIFACTS


def _require_pr_c_successor_progress(root: Path, progress: dict[str, Any]) -> None:
    stages = ("PR-A", "PR-B", "PR-C", "PR-D", "PR-E", "PR-F")
    current = progress.get("current_stage")
    if current == "COMPLETE":
        current_index = len(stages)
        if progress.get("completed_stage") != "PR-F":
            raise ValueError("terminal successor progress is not complete")
    else:
        if current not in stages or stages.index(current) < 3:
            raise ValueError("successor current stage is not after PR-C")
        current_index = stages.index(current)
        if progress.get("completed_stage") != stages[current_index - 1]:
            raise ValueError("successor progress is not monotonic")
    merged = progress.get("merged_prs")
    if not isinstance(merged, list) or len(merged) != current_index:
        raise ValueError("successor merged PR sequence is not monotonic")
    pr_c = _validate_stage_record(merged[2], stage="PR-C")
    attestation_path = _regular_file(root, PR_C_SUCCESSOR_ATTESTATION)
    attestation_raw = attestation_path.read_text(encoding="utf-8")
    attestation = _load_json(attestation_path)
    if attestation_raw != json.dumps(attestation, indent=2, ensure_ascii=False) + "\n":
        raise ValueError("PR-C successor attestation is not canonical JSON")
    if tuple(attestation) != EXPECTED_SUCCESSOR_ATTESTATION_FIELDS:
        raise ValueError("PR-C successor attestation fields differ")
    payload = dict(attestation)
    record_sha256 = payload.pop("record_sha256")
    if (
        attestation.get("schema_version")
        != "dta-v22-pr-c-successor-attestation.v1"
        or attestation.get("goal_version") != "dta-v22-p0-master-v1"
        or attestation.get("decision_id") != "DEC-055"
        or attestation.get("repository") != "Raidriar7170/EcomSRE-Agent"
        or attestation.get("source_stage") != "PR-C"
        or attestation.get("source_pr") != pr_c["pr"]
        or attestation.get("source_candidate_head") != pr_c["head_sha"]
        or attestation.get("source_merge_commit") != pr_c["merge_commit"]
        or attestation.get("successor_stage") != "PR-D"
        or attestation.get("base_main_head") != pr_c["merge_commit"]
        or not isinstance(attestation.get("successor_pr"), int)
        or attestation["successor_pr"] <= pr_c["pr"]
        or attestation.get("successor_branch") != "codex/dta-v22-p0-pr-d-planner-lite"
        or not _is_sha(attestation.get("source_candidate_tree"), 40)
        or not _is_sha(attestation.get("source_merge_tree"), 40)
        or not _is_sha(attestation.get("successor_head"), 40)
        or not _is_sha(attestation.get("successor_tree"), 40)
        or not _is_sha(record_sha256, 64)
        or record_sha256 != semantic_sha256_v22(payload)
        or any(attestation.get(field) is not False for field in EXPECTED_FALSE_ACTIVITY_FIELDS)
    ):
        raise ValueError("PR-C successor attestation differs")
    changed_paths = attestation.get("changed_paths")
    raw_hashes = attestation.get("raw_sha256_by_path")
    if (
        not isinstance(changed_paths, list)
        or not changed_paths
        or any(not isinstance(item, str) for item in changed_paths)
        or changed_paths != sorted(set(changed_paths))
        or any(
            Path(item).is_absolute()
            or ".." in Path(item).parts
            or Path(item).as_posix() != item
            or item == PR_C_SUCCESSOR_ATTESTATION.as_posix()
            for item in changed_paths
        )
        or not isinstance(raw_hashes, dict)
        or list(raw_hashes) != changed_paths
        or any(not _is_sha(value, 64) for value in raw_hashes.values())
    ):
        raise ValueError("PR-C successor exact changed path or raw hash set differs")
    _verify_successor_git_provenance(
        root=root,
        progress=progress,
        current=current,
        merged=merged,
        source=pr_c,
        attestation=attestation,
        attestation_raw=attestation_raw,
        changed_paths=changed_paths,
        raw_hashes=raw_hashes,
    )


def _verify_successor_git_provenance(
    *,
    root: Path,
    progress: dict[str, Any],
    current: object,
    merged: list[object],
    source: dict[str, Any],
    attestation: dict[str, Any],
    attestation_raw: str,
    changed_paths: list[str],
    raw_hashes: dict[str, str],
) -> None:
    candidate = source["head_sha"]
    merge = source["merge_commit"]
    source_pull_ref = f"refs/remotes/dta-pr/{source['pr']}"
    successor_pr = attestation["successor_pr"]
    successor_pull_ref = f"refs/remotes/dta-pr/{successor_pr}"
    try:
        if _git_text(root, "rev-parse", "--verify", source_pull_ref) != candidate:
            raise ValueError("PR-C candidate head does not match the pull ref")
        candidate_tree = _git_text(root, "rev-parse", f"{candidate}^{{tree}}")
        merge_tree = _git_text(root, "rev-parse", f"{merge}^{{tree}}")
        if (
            candidate_tree != attestation["source_candidate_tree"]
            or merge_tree != attestation["source_merge_tree"]
            or candidate_tree != merge_tree
            or candidate == merge
        ):
            raise ValueError("PR-C squash tree identity differs")
        if len(_git_text(root, "rev-list", "--parents", "-n", "1", merge).split()) != 2:
            raise ValueError("PR-C squash merge is not single-parent")
        ancestor = subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", candidate, merge),
            check=False,
            capture_output=True,
        )
        if ancestor.returncode == 0:
            raise ValueError("PR-C candidate is an ancestor of the claimed squash merge")
        if ancestor.returncode != 1:
            raise ValueError("PR-C candidate ancestry is unavailable")
        subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", merge, "HEAD"),
            check=True,
            capture_output=True,
        )
        subject = _git_text(root, "show", "-s", "--format=%s", merge)
        if not (
            subject.startswith("DTA v2.2 P0 PR-C:")
            and subject.endswith(f"(#{source['pr']})")
        ):
            raise ValueError("PR-C merge subject does not bind the PR number")
        for commit in (candidate, merge):
            manifest = subprocess.run(
                ("git", "-C", str(root), "show", f"{commit}:{PR_C_MANIFEST}"),
                check=True,
                capture_output=True,
            ).stdout
            if hashlib.sha256(manifest).hexdigest() != EXPECTED_MANIFEST_SHA256:
                raise ValueError("PR-C Git tree does not bind the frozen manifest")
        if current == "PR-D":
            if (
                progress.get("active_pr") != successor_pr
                or progress.get("active_branch") != attestation["successor_branch"]
            ):
                raise ValueError("active PR-D identity differs from attestation")
            successor_final_head = _git_text(root, "rev-parse", "HEAD")
        else:
            pr_d = _validate_stage_record(merged[3], stage="PR-D")
            if pr_d["pr"] != successor_pr:
                raise ValueError("successor progress lacks exact PR-D provenance")
            successor_final_head = pr_d["head_sha"]
        if _git_text(root, "rev-parse", "--verify", successor_pull_ref) != successor_final_head:
            raise ValueError("PR-D final head does not match the pull ref")
        successor_head = attestation["successor_head"]
        if _git_text(root, "rev-parse", f"{successor_final_head}^") != successor_head:
            raise ValueError("PR-D attestation commit is not the final single-file child")
        if len(
            _git_text(root, "rev-list", "--parents", "-n", "1", successor_final_head).split()
        ) != 2:
            raise ValueError("PR-D attestation commit is not single-parent")
        if _git_text(root, "rev-parse", f"{successor_head}^{{tree}}") != attestation[
            "successor_tree"
        ]:
            raise ValueError("PR-D successor tree differs")
        subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", merge, successor_head),
            check=True,
            capture_output=True,
        )
        changed = _git_text(
            root,
            "diff",
            "--name-status",
            "--no-renames",
            merge,
            successor_head,
            "--",
        )
        observed: list[str] = []
        for line in changed.splitlines():
            status, relative = line.split("\t", 1)
            if status not in {"A", "M"}:
                raise ValueError("PR-D successor changed path kind differs")
            observed.append(relative)
        if sorted(observed) != changed_paths:
            raise ValueError("PR-D successor exact changed path set differs")
        for relative in changed_paths:
            blob = subprocess.run(
                ("git", "-C", str(root), "show", f"{successor_head}:{relative}"),
                check=True,
                capture_output=True,
            ).stdout
            if hashlib.sha256(blob).hexdigest() != raw_hashes[relative]:
                raise ValueError(f"PR-D successor raw SHA-256 differs: {relative}")
        delta = _git_text(
            root,
            "diff",
            "--name-status",
            "--no-renames",
            successor_head,
            successor_final_head,
            "--",
        )
        if delta != f"A\t{PR_C_SUCCESSOR_ATTESTATION}":
            raise ValueError("PR-D attestation commit changed more than its record")
        committed = subprocess.run(
            ("git", "-C", str(root), "show", f"{successor_final_head}:{PR_C_SUCCESSOR_ATTESTATION}"),
            check=True,
            capture_output=True,
        ).stdout
        if committed != attestation_raw.encode("utf-8"):
            raise ValueError("PR-D attestation record differs from committed bytes")
        tree_entry = _git_text(root, "ls-tree", successor_final_head, "--", str(PR_C_SUCCESSOR_ATTESTATION))
        if not tree_entry.startswith("100644 blob "):
            raise ValueError("PR-D attestation record is not a regular file")
    except subprocess.CalledProcessError as error:
        raise ValueError("PR-C Git provenance is unavailable") from error


def verify_pr_c_bindings(
    root: Path,
    *,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    path = manifest_path or (root / PR_C_MANIFEST)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_MANIFEST_SHA256:
        raise ValueError("PR-C manifest raw SHA-256 differs")
    manifest = _load_json(path)
    if raw.decode("utf-8") != json.dumps(manifest, indent=2, ensure_ascii=False) + "\n":
        raise ValueError("PR-C manifest is not canonical JSON")
    if (
        manifest.get("schema_version")
        != "dta-v22-pr-c-memory-predicate-bindings.v1"
        or manifest.get("goal_version") != "dta-v22-p0-master-v1"
        or manifest.get("stage") != "PR-C"
        or manifest.get("base_main") != PR_C_BASE
        or manifest.get("terminal") != "DTA_V22_PR_C_MEMORY_PREDICATES_READY"
    ):
        raise ValueError("PR-C manifest identity differs")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or tuple(
        item.get("path") if isinstance(item, dict) else None for item in artifacts
    ) != EXPECTED_ARTIFACT_PATHS:
        raise ValueError("PR-C artifact surface differs")
    for artifact in artifacts:
        if (
            not isinstance(artifact, dict)
            or set(artifact) != {"path", "sha256"}
            or not _is_sha(artifact.get("sha256"), 64)
        ):
            raise ValueError("PR-C artifact binding differs")
        source = _regular_file(root, Path(artifact["path"]))
        if hashlib.sha256(source.read_bytes()).hexdigest() != artifact["sha256"]:
            raise ValueError(f"PR-C artifact raw SHA-256 differs: {artifact['path']}")
    thresholds = PredicateThresholdsV22.frozen()
    if manifest.get("predicate_thresholds") != thresholds.model_dump(mode="json"):
        raise ValueError("PR-C frozen threshold binding differs")
    policy = build_default_evidence_support_policy_v22()
    if (
        manifest.get("support_policy_sha256") != policy.policy_sha256
        or manifest.get("support_clause_ids")
        != [item.clause_id for item in policy.clauses]
        or manifest.get("diagnosis_terminals")
        != [item.value for item in DiagnosisTerminalV22]
    ):
        raise ValueError("PR-C policy or terminal binding differs")
    contract = manifest.get("successor_attestation_contract")
    if not isinstance(contract, dict) or (
        contract.get("path") != PR_C_SUCCESSOR_ATTESTATION.as_posix()
        or contract.get("schema_version")
        != "dta-v22-pr-c-successor-attestation.v1"
        or contract.get("decision_id") != "DEC-055"
        or contract.get("source_stage") != "PR-C"
        or contract.get("successor_stage") != "PR-D"
        or contract.get("successor_branch") != "codex/dta-v22-p0-pr-d-planner-lite"
        or contract.get("required_fields") != list(EXPECTED_SUCCESSOR_ATTESTATION_FIELDS)
        or contract.get("git_proofs") != EXPECTED_GIT_PROOFS
        or contract.get("required_false_activity_fields")
        != list(EXPECTED_FALSE_ACTIVITY_FIELDS)
    ):
        raise ValueError("PR-C successor attestation contract differs")
    return manifest


def _verify_runtime_contracts() -> None:
    forbidden = {"truth", "fixture", "expected_mechanism", "case_id"}
    if not forbidden.isdisjoint(
        inspect.signature(PredicateExtractorV22.extract).parameters
    ):
        raise ValueError("predicate extractor exposes evaluator truth input")
    if not forbidden.isdisjoint(inspect.signature(build_memory_views_v22).parameters):
        raise ValueError("memory builder exposes evaluator truth input")
    if set(inspect.signature(filter_candidates_v22).parameters) != {
        "admission",
        "registry",
        "memory",
        "policy",
    }:
        raise ValueError("candidate filter lacks predicate bindings")
    if tuple(RuntimeSalientPayloadV22.model_fields) != (
        "schema_version",
        "state",
        "healthy",
        "endpoint",
        "restart_count",
        "exit_code",
    ):
        raise ValueError("runtime salient detail fields differ")
    if "full_observations" not in FullEvidenceMemoryV22.model_fields:
        raise ValueError("Full Memory lacks typed observations")
    if "full_observations" in SalientEvidenceMemoryV22.model_fields:
        raise ValueError("Salient Memory duplicates full observations")
    if tuple(DiagnosisTerminalV22) != (
        DiagnosisTerminalV22.DIAGNOSED,
        DiagnosisTerminalV22.NO_INCIDENT,
        DiagnosisTerminalV22.ABSTAIN,
        DiagnosisTerminalV22.FAILED,
    ):
        raise ValueError("Diagnosis terminals differ")
    if get_args(
        FixedTrajectoryMemoryBenchmarkV22.model_fields["provider_calls"].annotation
    ) != (0,):
        raise ValueError("memory benchmark Provider count differs")
    if "runtime_details" not in inspect.signature(benchmark_fixed_trajectory_v22).parameters:
        raise ValueError("memory benchmark omits runtime detail binding")


def verify_pr_c_protocol(root: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    progress = _load_json(root / "docs/analysis/dta-v22-p0-master-progress.json")
    mode, paths = _public_scan_plan(root, progress)
    if mode == "PR_C_CLOSED_SURFACE":
        _verify_closed_changed_surface(root)
    for relative in paths:
        text = (
            _changed_text(root, relative)
            if mode == "PR_C_CLOSED_SURFACE"
            else _regular_file(root, relative).read_text(encoding="utf-8")
        )
        assert_no_public_leak(text)
    prior = verify_pr_b_protocol(root)
    verify_pr_c_bindings(root)
    _verify_runtime_contracts()
    return {
        "schema_version": "dta-v22-pr-c-verification.v1",
        "status": "PASS",
        "historical_bindings": prior["historical_bindings"],
        "pr_b_successor_gate": "PASS",
        "public_scan_mode": mode,
        "secret_private_path_scan": "PASS",
        "truth_isolation": "PASS",
        "memory_contract": "PASS",
        "predicate_policy": "PASS",
        "diagnosis_candidate_filter": "PASS",
        "terminal": "DTA_V22_PR_C_MEMORY_PREDICATES_READY",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = verify_pr_c_protocol(args.root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
