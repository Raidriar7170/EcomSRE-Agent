"""Verify the DTA v2.2 PR-B canonical action and query-semantics boundary."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from pathlib import Path
import stat
import subprocess
from typing import Any, Sequence

from ecomsre.dta_v2.v22.action_catalog import (
    ActionCoverageV22,
    ActionMaskReasonV22,
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
    resolve_canonical_request_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    RecentChangeRecordV22,
    semantic_sha256_v22,
)
from scripts.ci.verify_dta_v22_pr_a import (
    assert_no_public_leak,
    verify_pr_a_protocol,
)


PR_B_BASE = "9d53002c3d86208a67b73d271c5eaf6e2f45b8b7"
PR_B_SQUASH_MERGE = "8e42b6d212e24ddc94ac4097da7e3e3aae57da98"
PR_B_MANIFEST = Path("config/dta-v22/pr-b-action-catalog-bindings.v1.json")
PR_B_SUCCESSOR_ATTESTATION = Path(
    "config/dta-v22/pr-b-successor-attestation.v1.json"
)
EXPECTED_MANIFEST_SHA256 = (
    "546d4db79dbb169a9f8645eead864499f92f21b3e96326064a8c0ea2d6459788"
)
EXPECTED_PR_B_CHANGED_PATHS = (
    Path(".github/workflows/agent-mainline.yml"),
    PR_B_MANIFEST,
    Path("docs/analysis/dta-v22-p0-master-progress.json"),
    Path("docs/human-briefs/2026-08-19-dta-v22-pr-b-action-catalog.md"),
    Path("scripts/ci/verify_dta_v22_pr_b.py"),
    Path("src/ecomsre/dta_v2/v22/__init__.py"),
    Path("src/ecomsre/dta_v2/v22/action_catalog.py"),
    Path("src/ecomsre/dta_v2/v22/read_contracts.py"),
    Path("src/ecomsre/dta_v2/v22/replay.py"),
    Path("tests/dta_v22/test_v22_action_catalog.py"),
    Path("tests/dta_v22/test_v22_pr_a_protocol.py"),
    Path("tests/dta_v22/test_v22_pr_b_verifier.py"),
    Path("tests/dta_v22/test_v22_query_replay.py"),
)
PERSISTENT_PR_B_ARTIFACTS = (
    PR_B_MANIFEST,
    PR_B_SUCCESSOR_ATTESTATION,
    Path("docs/human-briefs/2026-08-19-dta-v22-pr-b-action-catalog.md"),
    Path("src/ecomsre/dta_v2/v22/action_catalog.py"),
    Path("src/ecomsre/dta_v2/v22/read_contracts.py"),
    Path("src/ecomsre/dta_v2/v22/replay.py"),
    Path("tests/dta_v22/test_v22_action_catalog.py"),
    Path("tests/dta_v22/test_v22_query_replay.py"),
)
EXPECTED_ARTIFACT_PATHS = (
    "src/ecomsre/dta_v2/v22/action_catalog.py",
    "src/ecomsre/dta_v2/v22/read_contracts.py",
    "src/ecomsre/dta_v2/v22/replay.py",
    "tests/dta_v22/test_v22_action_catalog.py",
    "tests/dta_v22/test_v22_query_replay.py",
)
EXPECTED_REQUEST_PROFILE = {
    "metrics": {
        "metric_kinds": ["ERROR_RATE", "LATENCY_P95_MS", "REQUEST_SUPPORT"],
        "lookback_seconds": 300,
        "max_results": 3,
    },
    "logs": {"lookback_seconds": 300, "max_records": 12},
    "traces": {
        "lookback_seconds": 300,
        "max_spans": 12,
        "neighborhood_hops": 1,
    },
    "runtime": {"max_results": "exact_target_count"},
    "resources": {"sampling_window_seconds": 10, "sample_count": 5},
    "changes": {"lookback_seconds": 3600, "max_records": 12},
}
EXPECTED_CHANGE_FIELDS = (
    "schema_version",
    "opaque_change_id",
    "service",
    "observed_at",
    "category",
    "rollout_state",
    "revision_digest",
)
EXPECTED_TRUTH_ISOLATION = {
    "catalog_reads_evaluator_truth": False,
    "catalog_reads_fixture_content": False,
    "catalog_reads_expected_mechanism": False,
    "catalog_reads_expected_source": False,
    "catalog_reads_fault_controller": False,
    "recent_changes_contains_fault_flag": False,
    "recent_changes_contains_injected_variant": False,
    "recent_changes_contains_expected_runbook": False,
}
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
    "provider_called",
    "docker_called",
    "held_out_executed",
    "scenario_executed",
    "fault_injected",
    "runbook_executed",
    "private_evidence_changed",
    "public_result_changed",
    "execution_report_rebound",
    "record_sha256",
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
EXPECTED_SUCCESSOR_ATTESTATION_CONTRACT = {
    "path": PR_B_SUCCESSOR_ATTESTATION.as_posix(),
    "schema_version": "dta-v22-pr-b-successor-attestation.v1",
    "required_fields": list(EXPECTED_SUCCESSOR_ATTESTATION_FIELDS),
    "decision_id": "DEC-055",
    "source_stage": "PR-B",
    "successor_stage": "PR-C",
    "pull_ref_namespace": "refs/remotes/dta-pr/{pr}",
    "git_proofs": [
        "source_candidate_matches_pull_ref",
        "source_candidate_tree_matches_single_parent_squash_tree",
        "source_candidate_is_not_merge_ancestor",
        "source_merge_is_current_ancestor_and_subject_binds_pr",
        "source_candidate_and_merge_trees_bind_manifest",
        "successor_head_tree_and_changed_paths_match_base_diff",
        "every_successor_changed_file_matches_raw_sha256",
        "attestation_commit_is_single_file_child",
        "successor_final_head_matches_pull_ref",
    ],
    "required_false_activity_fields": list(EXPECTED_FALSE_ACTIVITY_FIELDS),
}


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
        raise ValueError(f"PR-B artifact must be a regular file: {relative}")
    if not path.resolve(strict=True).is_relative_to(root):
        raise ValueError(f"PR-B artifact escapes repository: {relative}")
    return path


def _git_paths(root: Path, *args: str) -> set[Path]:
    completed = subprocess.run(
        ("git", "-C", str(root), *args),
        check=True,
        capture_output=True,
        text=True,
    )
    return {Path(item) for item in completed.stdout.splitlines() if item}


def _changed_text(root: Path, relative: Path) -> str:
    path = _regular_file(root, relative)
    baseline = subprocess.run(
        ("git", "-C", str(root), "cat-file", "-e", f"{PR_B_BASE}:{relative}"),
        check=False,
        capture_output=True,
    )
    if baseline.returncode != 0:
        return path.read_text(encoding="utf-8")
    diff = subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "diff",
            "--unified=0",
            PR_B_BASE,
            "--",
            relative.as_posix(),
        ),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )


def _verify_closed_changed_surface(root: Path) -> None:
    observed = _git_paths(root, "diff", "--name-only", PR_B_BASE, "--")
    observed.update(_git_paths(root, "ls-files", "--others", "--exclude-standard"))
    expected = set(EXPECTED_PR_B_CHANGED_PATHS)
    if observed != expected:
        raise ValueError(
            "PR-B changed surface differs: "
            f"undeclared={sorted(str(item) for item in observed - expected)}, "
            f"missing={sorted(str(item) for item in expected - observed)}"
        )


def _public_scan_plan(
    root: Path,
    progress: dict[str, Any],
) -> tuple[str, tuple[Path, ...]]:
    if progress.get("current_stage") == "PR-B":
        if progress.get("completed_stage") != "PR-A":
            raise ValueError("PR-B progress does not prove PR-A completion")
        return "PR_B_CLOSED_SURFACE", EXPECTED_PR_B_CHANGED_PATHS
    completed = progress.get("completed_stage")
    if completed not in {"PR-B", "PR-C", "PR-D", "PR-E", "PR-F"}:
        raise ValueError("successor progress does not prove PR-B completion")
    _require_pr_b_successor_progress(root, progress)
    return "SUCCESSOR_PERSISTENT_ARTIFACTS", PERSISTENT_PR_B_ARTIFACTS


def _is_sha40(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_sha64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _git_text(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(root), *args),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _require_pr_b_successor_progress(root: Path, progress: dict[str, Any]) -> None:
    stages = ("PR-A", "PR-B", "PR-C", "PR-D", "PR-E", "PR-F")
    current = progress.get("current_stage")
    if current == "COMPLETE":
        current_index = len(stages)
        if progress.get("completed_stage") != "PR-F":
            raise ValueError("terminal successor progress is not complete")
    else:
        if current not in stages or stages.index(current) < 2:
            raise ValueError("successor current stage is not after PR-B")
        current_index = stages.index(current)
        if progress.get("completed_stage") != stages[current_index - 1]:
            raise ValueError("successor progress is not monotonic")
    merged = progress.get("merged_prs")
    if not isinstance(merged, list) or len(merged) < 2:
        raise ValueError("successor progress lacks exact PR-B merge provenance")
    if len(merged) != current_index:
        raise ValueError("successor merged PR count is not monotonic")
    pr_b = merged[1]
    if (
        not isinstance(pr_b, dict)
        or set(pr_b) != {"stage", "pr", "head_sha", "merge_commit"}
        or pr_b.get("stage") != "PR-B"
        or not isinstance(pr_b.get("pr"), int)
        or pr_b["pr"] < 58
        or not _is_sha40(pr_b.get("head_sha"))
        or not _is_sha40(pr_b.get("merge_commit"))
    ):
        raise ValueError("successor progress lacks exact PR-B merge provenance")
    attestation_path = _regular_file(root, PR_B_SUCCESSOR_ATTESTATION)
    attestation_raw = attestation_path.read_text(encoding="utf-8")
    attestation = _load_json(attestation_path)
    if attestation_raw != json.dumps(
        attestation,
        indent=2,
        ensure_ascii=False,
    ) + "\n":
        raise ValueError("PR-B successor attestation is not canonical JSON")
    if tuple(attestation) != EXPECTED_SUCCESSOR_ATTESTATION_FIELDS:
        raise ValueError("PR-B successor attestation fields differ")
    record_sha256 = attestation.get("record_sha256")
    attestation_payload = dict(attestation)
    attestation_payload.pop("record_sha256")
    if (
        attestation.get("schema_version")
        != "dta-v22-pr-b-successor-attestation.v1"
        or attestation.get("goal_version") != "dta-v22-p0-master-v1"
        or attestation.get("decision_id") != "DEC-055"
        or attestation.get("repository") != "Raidriar7170/EcomSRE-Agent"
        or attestation.get("source_stage") != "PR-B"
        or attestation.get("source_pr") != pr_b["pr"]
        or attestation.get("source_candidate_head") != pr_b["head_sha"]
        or attestation.get("source_merge_commit") != pr_b["merge_commit"]
        or attestation.get("successor_stage") != "PR-C"
        or attestation.get("base_main_head") != pr_b["merge_commit"]
        or not isinstance(attestation.get("successor_pr"), int)
        or attestation["successor_pr"] <= pr_b["pr"]
        or attestation.get("successor_branch")
        != "codex/dta-v22-p0-pr-c-memory-predicates-diagnosis"
        or not _is_sha40(attestation.get("source_candidate_tree"))
        or not _is_sha40(attestation.get("source_merge_tree"))
        or not _is_sha40(attestation.get("successor_head"))
        or not _is_sha40(attestation.get("successor_tree"))
        or not _is_sha64(record_sha256)
        or record_sha256 != semantic_sha256_v22(attestation_payload)
        or any(
            attestation.get(field) is not False
            for field in EXPECTED_FALSE_ACTIVITY_FIELDS
        )
    ):
        raise ValueError("PR-B successor attestation differs")

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
            or item == PR_B_SUCCESSOR_ATTESTATION.as_posix()
            for item in changed_paths
        )
        or not isinstance(raw_hashes, dict)
        or list(raw_hashes) != changed_paths
        or any(not _is_sha64(value) for value in raw_hashes.values())
    ):
        raise ValueError("PR-B successor exact changed path or raw hash set differs")

    candidate = pr_b["head_sha"]
    merge = pr_b["merge_commit"]
    pull_ref = f"refs/remotes/dta-pr/{pr_b['pr']}"
    successor_pr = attestation["successor_pr"]
    successor_pull_ref = f"refs/remotes/dta-pr/{successor_pr}"
    try:
        if _git_text(root, "rev-parse", "--verify", pull_ref) != candidate:
            raise ValueError("PR-B candidate head does not match the pull ref")
        candidate_tree = _git_text(root, "rev-parse", f"{candidate}^{{tree}}")
        merge_tree = _git_text(root, "rev-parse", f"{merge}^{{tree}}")
        if candidate_tree != attestation["source_candidate_tree"]:
            raise ValueError("PR-B candidate tree differs")
        if merge_tree != attestation["source_merge_tree"]:
            raise ValueError("PR-B merge tree differs")
        if candidate_tree != merge_tree or candidate == merge:
            raise ValueError("PR-B squash tree identity differs")
        merge_parents = _git_text(root, "rev-list", "--parents", "-n", "1", merge)
        if len(merge_parents.split()) != 2:
            raise ValueError("PR-B squash merge is not single-parent")
        candidate_ancestor = subprocess.run(
            (
                "git",
                "-C",
                str(root),
                "merge-base",
                "--is-ancestor",
                candidate,
                merge,
            ),
            check=False,
            capture_output=True,
        )
        if candidate_ancestor.returncode == 0:
            raise ValueError("PR-B candidate is an ancestor of the claimed squash merge")
        if candidate_ancestor.returncode != 1:
            raise ValueError("PR-B candidate ancestry is unavailable")
        subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", merge, "HEAD"),
            check=True,
            capture_output=True,
        )
        subject = _git_text(root, "show", "-s", "--format=%s", merge)
        if not (
            subject.startswith("DTA v2.2 P0 PR-B:")
            and subject.endswith(f"(#{pr_b['pr']})")
        ):
            raise ValueError("PR-B merge subject does not bind the PR number")
        for commit in (candidate, merge):
            manifest = subprocess.run(
                (
                    "git",
                    "-C",
                    str(root),
                    "show",
                    f"{commit}:{PR_B_MANIFEST.as_posix()}",
                ),
                check=True,
                capture_output=True,
            ).stdout
            if hashlib.sha256(manifest).hexdigest() != EXPECTED_MANIFEST_SHA256:
                raise ValueError("PR-B Git tree does not bind the frozen manifest")

        if current == "PR-C":
            if (
                progress.get("active_pr") != successor_pr
                or progress.get("active_branch") != attestation["successor_branch"]
            ):
                raise ValueError("active PR-C identity differs from attestation")
            successor_final_head = _git_text(
                root,
                "rev-parse",
                "--verify",
                successor_pull_ref,
            )
            head = _git_text(root, "rev-parse", "HEAD")
            if head != successor_final_head:
                successor_tree = _git_text(
                    root,
                    "rev-parse",
                    f"{successor_final_head}^{{tree}}",
                )
                squash_candidates = tuple(
                    commit
                    for commit in _git_text(
                        root,
                        "rev-list",
                        "--ancestry-path",
                        f"{attestation['base_main_head']}..HEAD",
                    ).splitlines()
                    if _git_text(root, "rev-parse", f"{commit}^")
                    == attestation["base_main_head"]
                    and _git_text(root, "rev-parse", f"{commit}^{{tree}}")
                    == successor_tree
                    and _git_text(root, "show", "-s", "--format=%s", commit).endswith(
                        f"(#{successor_pr})"
                    )
                )
                if len(squash_candidates) != 1:
                    raise ValueError("PR-C squash merge tree is absent from HEAD ancestry")
        else:
            pr_c = merged[2]
            if (
                not isinstance(pr_c, dict)
                or set(pr_c) != {"stage", "pr", "head_sha", "merge_commit"}
                or pr_c.get("stage") != "PR-C"
                or pr_c.get("pr") != successor_pr
                or not _is_sha40(pr_c.get("head_sha"))
            ):
                raise ValueError("successor progress lacks exact PR-C provenance")
            successor_final_head = pr_c["head_sha"]
        if (
            _git_text(root, "rev-parse", "--verify", successor_pull_ref)
            != successor_final_head
        ):
            raise ValueError("PR-C final head does not match the pull ref")
        successor_head = attestation["successor_head"]
        if _git_text(root, "rev-parse", f"{successor_final_head}^") != successor_head:
            raise ValueError("PR-C attestation commit is not the final single-file child")
        if len(
            _git_text(
                root,
                "rev-list",
                "--parents",
                "-n",
                "1",
                successor_final_head,
            ).split()
        ) != 2:
            raise ValueError("PR-C attestation commit is not single-parent")
        if _git_text(root, "rev-parse", f"{successor_head}^{{tree}}") != attestation[
            "successor_tree"
        ]:
            raise ValueError("PR-C successor tree differs")
        subprocess.run(
            (
                "git",
                "-C",
                str(root),
                "merge-base",
                "--is-ancestor",
                merge,
                successor_head,
            ),
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
        observed_changed_paths: list[str] = []
        for line in changed.splitlines():
            status, relative = line.split("\t", 1)
            if status not in {"A", "M"}:
                raise ValueError("PR-C successor changed path kind differs")
            observed_changed_paths.append(relative)
        if sorted(observed_changed_paths) != changed_paths:
            raise ValueError("PR-C successor exact changed path set differs")
        for relative in changed_paths:
            blob = subprocess.run(
                ("git", "-C", str(root), "show", f"{successor_head}:{relative}"),
                check=True,
                capture_output=True,
            ).stdout
            if hashlib.sha256(blob).hexdigest() != raw_hashes[relative]:
                raise ValueError(f"PR-C successor raw SHA-256 differs: {relative}")
        attestation_delta = _git_text(
            root,
            "diff",
            "--name-status",
            "--no-renames",
            successor_head,
            successor_final_head,
            "--",
        )
        if attestation_delta != f"A\t{PR_B_SUCCESSOR_ATTESTATION.as_posix()}":
            raise ValueError("PR-C attestation commit changed more than its record")
        committed_attestation = subprocess.run(
            (
                "git",
                "-C",
                str(root),
                "show",
                f"{successor_final_head}:{PR_B_SUCCESSOR_ATTESTATION.as_posix()}",
            ),
            check=True,
            capture_output=True,
        ).stdout
        if committed_attestation != attestation_raw.encode("utf-8"):
            raise ValueError("PR-C attestation record differs from committed bytes")
        tree_entry = _git_text(
            root,
            "ls-tree",
            successor_final_head,
            "--",
            PR_B_SUCCESSOR_ATTESTATION.as_posix(),
        )
        if not tree_entry.startswith("100644 blob "):
            raise ValueError("PR-C attestation record is not a regular file")
    except subprocess.CalledProcessError as error:
        raise ValueError("PR-B Git provenance is unavailable") from error


def verify_pr_b_bindings(
    root: Path,
    *,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    relative = PR_B_MANIFEST if manifest_path is None else manifest_path
    path = root / relative if not relative.is_absolute() else relative
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_MANIFEST_SHA256:
        raise ValueError("PR-B manifest raw SHA-256 differs")
    manifest = _load_json(path)
    if manifest.get("schema_version") != "dta-v22-pr-b-action-catalog-bindings.v1":
        raise ValueError("PR-B manifest schema differs")
    if manifest.get("goal_version") != "dta-v22-p0-master-v1":
        raise ValueError("PR-B manifest goal differs")
    if manifest.get("stage") != "PR-B" or manifest.get("base_main") != PR_B_BASE:
        raise ValueError("PR-B manifest stage or base differs")
    if manifest.get("terminal") != "DTA_V22_PR_B_ACTION_CATALOG_READY":
        raise ValueError("PR-B terminal differs")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("PR-B artifact bindings are not a list")
    paths = tuple(item.get("path") for item in artifacts if isinstance(item, dict))
    if paths != EXPECTED_ARTIFACT_PATHS:
        raise ValueError("PR-B artifact path set differs")
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValueError("PR-B artifact binding shape differs")
        relative_path = Path(item["path"])
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("PR-B artifact path escapes the repository")
        artifact = subprocess.run(
            (
                "git",
                "-C",
                str(root),
                "show",
                f"{PR_B_SQUASH_MERGE}:{relative_path.as_posix()}",
            ),
            check=False,
            capture_output=True,
        )
        if artifact.returncode != 0:
            raise ValueError(f"PR-B artifact object is unavailable: {item['path']}")
        if hashlib.sha256(artifact.stdout).hexdigest() != item["sha256"]:
            raise ValueError(f"PR-B artifact digest differs: {item['path']}")
    if manifest.get("canonical_request_profile") != EXPECTED_REQUEST_PROFILE:
        raise ValueError("PR-B canonical request profile differs")
    if manifest.get("source_statuses") != [item.value for item in ReadSourceStatusV22]:
        raise ValueError("PR-B read source statuses differ")
    if manifest.get("mask_reasons") != [item.value for item in ActionMaskReasonV22]:
        raise ValueError("PR-B action mask reasons differ")
    if tuple(manifest.get("changes_public_fields", ())) != EXPECTED_CHANGE_FIELDS:
        raise ValueError("PR-B Changes public field set differs")
    if manifest.get("truth_isolation") != EXPECTED_TRUTH_ISOLATION:
        raise ValueError("PR-B truth-isolation declaration differs")
    if (
        manifest.get("successor_attestation_contract")
        != EXPECTED_SUCCESSOR_ATTESTATION_CONTRACT
    ):
        raise ValueError("PR-B successor attestation contract differs")
    return manifest


def _verify_progress(root: Path, progress: dict[str, Any]) -> None:
    if progress.get("current_stage") == "PR-B":
        if progress.get("completed_stage") != "PR-A":
            raise ValueError("PR-B progress predecessor differs")
        if progress.get("active_branch") != "codex/dta-v22-p0-pr-b-action-catalog":
            raise ValueError("PR-B active branch differs")
        active_pr = progress.get("active_pr")
        if active_pr is not None and (not isinstance(active_pr, int) or active_pr <= 0):
            raise ValueError("PR-B active PR is invalid")
    else:
        _require_pr_b_successor_progress(root, progress)
    merged = progress.get("merged_prs")
    if not isinstance(merged, list) or not merged:
        raise ValueError("Master Progress omits merged PR-A")
    if merged[0] != {
        "stage": "PR-A",
        "pr": 57,
        "head_sha": "57bc106ef181d4ca8bafb3eb00372c1084bd0c60",
        "merge_commit": PR_B_BASE,
    }:
        raise ValueError("Master Progress PR-A provenance differs")


def _verify_runtime_contracts() -> None:
    if tuple(ActionCoverageV22.model_fields) != (
        "schema_version",
        "executed_action_ids",
        "covered_capability_keys",
        "coverage_sha256",
    ):
        raise ValueError("ActionCoverageV22 contract differs")
    builder_fields = tuple(inspect.signature(build_action_catalog_v22).parameters)
    if builder_fields != (
        "candidate_services",
        "topology",
        "capability_registry",
        "executed_action_ids",
        "remaining_budget",
        "covered_capability_keys",
    ):
        raise ValueError("catalog builder input surface differs")
    if tuple(inspect.signature(resolve_canonical_request_v22).parameters) != (
        "catalog",
        "action_id",
    ):
        raise ValueError("canonical resolver accepts non-action parameters")
    topology = StaticTopologyV22.build(
        services=("checkout", "payment"),
        edges=(("checkout", "payment"),),
    )
    registry = build_default_tool_capability_registry_v22()
    first = build_action_catalog_v22(
        candidate_services=("checkout", "payment"),
        topology=topology,
        capability_registry=registry,
        executed_action_ids=(),
        remaining_budget=20.0,
    )
    second = build_action_catalog_v22(
        candidate_services=("checkout", "payment"),
        topology=topology,
        capability_registry=registry,
        executed_action_ids=(),
        remaining_budget=20.0,
    )
    if first != second:
        raise ValueError("canonical action catalog is nondeterministic")
    bindings = {item.action_id: item.request_sha256 for item in first.registry_actions}
    if len(bindings) != len(first.registry_actions):
        raise ValueError("canonical action IDs are not unique")
    for action in first.actions:
        request = resolve_canonical_request_v22(
            catalog=first,
            action_id=action.action_id,
        )
        if request.request_sha256 != bindings[action.action_id]:
            raise ValueError("action ID resolves to a different request")
    if tuple(RecentChangeRecordV22.model_fields) != EXPECTED_CHANGE_FIELDS:
        raise ValueError("Recent Changes contract exposes an undeclared field")
    if tuple(EvidenceSourceV22)[-1] is not EvidenceSourceV22.CHANGES:
        raise ValueError("Changes source is absent from canonical source registry")


def verify_pr_b_protocol(root: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    progress = _load_json(root / "docs/analysis/dta-v22-p0-master-progress.json")
    mode, scan_paths = _public_scan_plan(root, progress)
    if mode == "PR_B_CLOSED_SURFACE":
        _verify_closed_changed_surface(root)
    for relative in scan_paths:
        assert_no_public_leak(_changed_text(root, relative) if mode == "PR_B_CLOSED_SURFACE" else _regular_file(root, relative).read_text(encoding="utf-8"))
    pr_a = verify_pr_a_protocol(root)
    verify_pr_b_bindings(root)
    _verify_progress(root, progress)
    _verify_runtime_contracts()
    return {
        "schema_version": "dta-v22-pr-b-verification.v1",
        "status": "PASS",
        "historical_bindings": pr_a["historical_bindings"],
        "pr_a_successor_gate": "PASS",
        "public_scan_mode": mode,
        "secret_private_path_scan": "PASS",
        "truth_isolation": "PASS",
        "action_catalog": "PASS",
        "query_semantics": "PASS",
        "terminal": "DTA_V22_PR_B_ACTION_CATALOG_READY",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    print(json.dumps(verify_pr_b_protocol(args.root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
