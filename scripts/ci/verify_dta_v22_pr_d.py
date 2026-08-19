"""Verify the DTA v2.2 PR-D controller and Provider protocol boundary."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Sequence

from ecomsre.dta_v2.v22 import PR_D_TERMINAL
from ecomsre.dta_v2.v22.controller_contracts import (
    ControllerDecisionV22,
    HypothesisCatalogV22,
)
from ecomsre.dta_v2.v22.controller_inputs import (
    ControllerTurnInputV22,
    build_common_triage_snapshot_v22,
    build_controller_turn_input_v22,
)
from ecomsre.dta_v2.v22.controller_modes import (
    PRIMARY_MODEL_V22,
    EvaluationArmV22,
    ProviderOutputModeV22,
    ProviderProbeStatusV22,
    build_controller_identity_manifests_v22,
    build_one_shot_oracle_context_v22,
    probe_provider_output_mode_v22,
    select_deterministic_router_decision_v22,
)
from ecomsre.dta_v2.v22.controller_provider import (
    ProviderControllerTurnV22,
    _controller_schema_v22,
)
from ecomsre.dta_v2.v22.controller_runtime import (
    PlanCorrectionV22,
    process_controller_decision_v22,
)
from ecomsre.dta_v2.v22.protocol_suite import (
    run_local_protocol_capability_suite_v22,
    run_provider_protocol_capability_suite_v22,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from scripts.ci.verify_dta_v22_pr_c import verify_pr_c_protocol


PR_D_BASE = "145d152c2c2d1367e7dac2f0229e2b369fbe55dc"
PR_D_PR = 60
PR_D_BRANCH = "codex/dta-v22-p0-pr-d-planner-lite"
PR_D_MANIFEST = Path("config/dta-v22/pr-d-controller-bindings.v1.json")
PR_D_SUCCESSOR_ATTESTATION = Path(
    "config/dta-v22/pr-d-successor-attestation.v1.json"
)
PR_C_SUCCESSOR_ATTESTATION = Path(
    "config/dta-v22/pr-c-successor-attestation.v2.json"
)
PROVIDER_SUMMARY = Path(
    "docs/analysis/dta-v22-pr-d-provider-protocol-summary.json"
)
EXPECTED_MANIFEST_SHA256 = (
    "4a8ad04967009af871d6f8ed51d68464218f36e3a64895a47387fdc0193cf7bb"
)
EXPECTED_SUMMARY_SHA256 = (
    "8911b46de9c0cbd2c063e5af94ca42c153c92d9de4888bf5da0220b573416bf1"
)
EXPECTED_REPORT_SHA256 = (
    "088b9febb807af46fd0708cd1a0a6cdb2ed7b943a1f44ec615173a53302bbdb8"
)
EXPECTED_IMPLEMENTATION_COMMIT = "b60f164df1409422110e7a72cff682ac59cf66f0"
EXPECTED_IMPLEMENTATION_TREE = "5715e24e52c27dc25cae0100164b407213ac2994"
EXPECTED_IDENTITY_SHA256S = (
    "f1c69ba0cd2d2d107748ab2bd66833a7ac0293ddaa1857b37227a1681e9b1ce8",
    "f9236c3939866d0c41c93b67fa0811b4f1c3cb6364a68e7b7e17c4ff88eb5176",
    "754e649a792919baf6cb2827f7787411c47e9b9af08324210db20b5e335e37cc",
    "bafbbbed13fa98e1bfaef754ecd1fcbff3ffb485d3840f6bbdfca72c906b4b37",
)
EXPECTED_PR_D_CHANGED_PATHS = (
    Path(".github/workflows/agent-mainline.yml"),
    PR_C_SUCCESSOR_ATTESTATION,
    PR_D_MANIFEST,
    Path("docs/DECISIONS.md"),
    Path("docs/analysis/dta-v22-p0-master-progress.json"),
    PROVIDER_SUMMARY,
    Path("docs/human-briefs/2026-08-20-dta-v22-pr-d-controller-protocol.md"),
    Path("scripts/ci/verify_dta_v22_pr_c.py"),
    Path("scripts/ci/verify_dta_v22_pr_d.py"),
    Path("scripts/dta_v22/run_pr_d_provider_protocol.py"),
    Path("src/ecomsre/dta_v2/v22/__init__.py"),
    Path("src/ecomsre/dta_v2/v22/controller_contracts.py"),
    Path("src/ecomsre/dta_v2/v22/controller_inputs.py"),
    Path("src/ecomsre/dta_v2/v22/controller_modes.py"),
    Path("src/ecomsre/dta_v2/v22/controller_provider.py"),
    Path("src/ecomsre/dta_v2/v22/controller_runtime.py"),
    Path("src/ecomsre/dta_v2/v22/protocol_suite.py"),
    Path("tests/dta_v22/test_v22_controller_contracts.py"),
    Path("tests/dta_v22/test_v22_controller_modes.py"),
    Path("tests/dta_v22/test_v22_controller_provider.py"),
    Path("tests/dta_v22/test_v22_controller_runtime.py"),
    Path("tests/dta_v22/test_v22_pr_c_verifier.py"),
    Path("tests/dta_v22/test_v22_pr_d_provider_execution.py"),
    Path("tests/dta_v22/test_v22_pr_d_verifier.py"),
    Path("tests/dta_v22/test_v22_protocol_suite.py"),
    Path("tests/dta_v22/test_v22_provider_protocol_suite.py"),
)
PERSISTENT_PR_D_ARTIFACTS = (
    PR_D_MANIFEST,
    PR_D_SUCCESSOR_ATTESTATION,
    PROVIDER_SUMMARY,
    Path("docs/human-briefs/2026-08-20-dta-v22-pr-d-controller-protocol.md"),
    Path("scripts/ci/verify_dta_v22_pr_d.py"),
    Path("scripts/dta_v22/run_pr_d_provider_protocol.py"),
    Path("src/ecomsre/dta_v2/v22/controller_contracts.py"),
    Path("src/ecomsre/dta_v2/v22/controller_inputs.py"),
    Path("src/ecomsre/dta_v2/v22/controller_modes.py"),
    Path("src/ecomsre/dta_v2/v22/controller_provider.py"),
    Path("src/ecomsre/dta_v2/v22/controller_runtime.py"),
    Path("src/ecomsre/dta_v2/v22/protocol_suite.py"),
)
EXPECTED_ARTIFACT_PATHS = (
    "docs/analysis/dta-v22-pr-d-provider-protocol-summary.json",
    "scripts/dta_v22/run_pr_d_provider_protocol.py",
    "src/ecomsre/dta_v2/v22/controller_contracts.py",
    "src/ecomsre/dta_v2/v22/controller_inputs.py",
    "src/ecomsre/dta_v2/v22/controller_modes.py",
    "src/ecomsre/dta_v2/v22/controller_provider.py",
    "src/ecomsre/dta_v2/v22/controller_runtime.py",
    "src/ecomsre/dta_v2/v22/protocol_suite.py",
    "tests/dta_v22/test_v22_controller_contracts.py",
    "tests/dta_v22/test_v22_controller_modes.py",
    "tests/dta_v22/test_v22_controller_provider.py",
    "tests/dta_v22/test_v22_controller_runtime.py",
    "tests/dta_v22/test_v22_pr_d_provider_execution.py",
    "tests/dta_v22/test_v22_protocol_suite.py",
    "tests/dta_v22/test_v22_provider_protocol_suite.py",
)
EXPECTED_ACTIVITY_FIELDS = (
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
EXPECTED_PR_E_ACTIVITY = {
    "provider_called": False,
    "docker_called": True,
    "held_out_executed": False,
    "scenario_executed": True,
    "fault_injected": True,
    "runbook_executed": False,
    "private_evidence_changed": True,
    "public_result_changed": True,
    "execution_report_rebound": False,
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
    *EXPECTED_ACTIVITY_FIELDS,
    "record_sha256",
)
_FORBIDDEN_PUBLIC_PATTERNS = (
    re.compile(r"/(?:Users|home)/[^\s\"']+"),
    re.compile(r"[A-Za-z]:\\[^\s\"']+"),
    re.compile(r"bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}", re.IGNORECASE),
)


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
        raise ValueError(f"PR-D artifact must be a regular file: {relative}")
    if not path.resolve(strict=True).is_relative_to(root):
        raise ValueError(f"PR-D artifact escapes repository: {relative}")
    return path


def _git_text(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(root), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _require_single_parent_commit(
    root: Path,
    commit: str,
    *,
    label: str,
) -> None:
    parents = _git_text(
        root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        commit,
    ).split()
    if len(parents) != 2:
        raise ValueError(f"{label} is not single-parent")


def _git_paths(root: Path, *args: str) -> set[Path]:
    return {Path(item) for item in _git_text(root, *args).splitlines() if item}


def _is_sha(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _assert_no_pr_d_public_leak(text: str) -> None:
    if any(pattern.search(text) is not None for pattern in _FORBIDDEN_PUBLIC_PATTERNS):
        raise ValueError("PR-D public leakage detected")


def _changed_text(root: Path, relative: Path) -> str:
    path = _regular_file(root, relative)
    if subprocess.run(
        ("git", "-C", str(root), "cat-file", "-e", f"{PR_D_BASE}:{relative}"),
        check=False,
        capture_output=True,
    ).returncode != 0:
        return path.read_text(encoding="utf-8")
    diff = _git_text(root, "diff", "--unified=0", PR_D_BASE, "--", relative.as_posix())
    return "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )


def _verify_closed_changed_surface(root: Path) -> None:
    observed = _git_paths(root, "diff", "--name-only", PR_D_BASE, "--")
    observed.update(_git_paths(root, "ls-files", "--others", "--exclude-standard"))
    expected = set(EXPECTED_PR_D_CHANGED_PATHS)
    if observed != expected:
        raise ValueError(
            "PR-D changed surface differs: "
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


def _require_pr_d_progress(progress: dict[str, Any]) -> None:
    if (
        progress.get("current_stage") != "PR-D"
        or progress.get("completed_stage") != "PR-C"
        or progress.get("active_branch") != PR_D_BRANCH
        or progress.get("active_pr") != PR_D_PR
        or progress.get("primary_model") != PRIMARY_MODEL_V22
        or progress.get("provider_mode") != "STRICT_STRUCTURED_OUTPUT"
        or tuple(
            progress.get(field)
            for field in (
                "flat_identity_sha256",
                "planner_identity_sha256",
                "router_identity_sha256",
                "one_shot_identity_sha256",
            )
        )
        != EXPECTED_IDENTITY_SHA256S
    ):
        raise ValueError("PR-D progress identity differs")
    merged = progress.get("merged_prs")
    if not isinstance(merged, list) or len(merged) != 3:
        raise ValueError("PR-D merged sequence differs")
    pr_c = _validate_stage_record(merged[2], stage="PR-C")
    if (
        pr_c["pr"] != 59
        or pr_c["head_sha"] != "de0f0b39bdd51e75925d75580401bab15a04ec66"
        or pr_c["merge_commit"] != PR_D_BASE
    ):
        raise ValueError("PR-D base provenance differs")


def _require_pr_d_successor_progress(root: Path, progress: dict[str, Any]) -> None:
    stages = ("PR-A", "PR-B", "PR-C", "PR-D", "PR-E", "PR-F")
    current = progress.get("current_stage")
    if current == "COMPLETE":
        current_index = len(stages)
        if progress.get("completed_stage") != "PR-F":
            raise ValueError("terminal successor progress is not complete")
    else:
        if current not in stages or stages.index(current) < 4:
            raise ValueError("successor current stage is not after PR-D")
        current_index = stages.index(current)
        if progress.get("completed_stage") != stages[current_index - 1]:
            raise ValueError("successor progress is not monotonic")
    merged = progress.get("merged_prs")
    if not isinstance(merged, list) or len(merged) != current_index:
        raise ValueError("successor merged PR sequence is not monotonic")
    pr_d = _validate_stage_record(merged[3], stage="PR-D")
    path = _regular_file(root, PR_D_SUCCESSOR_ATTESTATION)
    raw = path.read_text(encoding="utf-8")
    attestation = _load_json(path)
    if raw != json.dumps(attestation, indent=2, ensure_ascii=False) + "\n":
        raise ValueError("PR-D successor attestation is not canonical JSON")
    if tuple(attestation) != EXPECTED_SUCCESSOR_ATTESTATION_FIELDS:
        raise ValueError("PR-D successor attestation fields differ")
    payload = dict(attestation)
    record_sha = payload.pop("record_sha256")
    if (
        attestation.get("schema_version")
        != "dta-v22-pr-d-successor-attestation.v1"
        or attestation.get("goal_version") != "dta-v22-p0-master-v1"
        or attestation.get("decision_id") != "DEC-055"
        or attestation.get("repository") != "Raidriar7170/EcomSRE-Agent"
        or attestation.get("source_stage") != "PR-D"
        or attestation.get("source_pr") != pr_d["pr"]
        or attestation.get("source_candidate_head") != pr_d["head_sha"]
        or attestation.get("source_merge_commit") != pr_d["merge_commit"]
        or attestation.get("successor_stage") != "PR-E"
        or attestation.get("base_main_head") != pr_d["merge_commit"]
        or not isinstance(attestation.get("successor_pr"), int)
        or attestation["successor_pr"] <= pr_d["pr"]
        or attestation.get("successor_branch") != "codex/dta-v22-p0-pr-e-capture-freeze"
        or not _is_sha(attestation.get("source_candidate_tree"), 40)
        or not _is_sha(attestation.get("source_merge_tree"), 40)
        or not _is_sha(attestation.get("successor_head"), 40)
        or not _is_sha(attestation.get("successor_tree"), 40)
        or record_sha != semantic_sha256_v22(payload)
        or any(
            attestation.get(field) is not expected
            for field, expected in EXPECTED_PR_E_ACTIVITY.items()
        )
    ):
        raise ValueError("PR-D successor attestation differs")
    changed_paths = attestation.get("changed_paths")
    raw_hashes = attestation.get("raw_sha256_by_path")
    if (
        not isinstance(changed_paths, list)
        or not changed_paths
        or changed_paths != sorted(set(changed_paths))
        or any(not isinstance(item, str) for item in changed_paths)
        or any(
            Path(item).is_absolute()
            or ".." in Path(item).parts
            or Path(item).as_posix() != item
            or item == PR_D_SUCCESSOR_ATTESTATION.as_posix()
            for item in changed_paths
        )
        or not isinstance(raw_hashes, dict)
        or list(raw_hashes) != changed_paths
        or any(not _is_sha(value, 64) for value in raw_hashes.values())
    ):
        raise ValueError("PR-D successor exact changed path or raw hash set differs")
    _verify_successor_git_provenance(
        root=root,
        progress=progress,
        current=current,
        merged=merged,
        source=pr_d,
        attestation=attestation,
        attestation_raw=raw,
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
    source_ref = f"refs/remotes/dta-pr/{source['pr']}"
    successor_pr = attestation["successor_pr"]
    successor_ref = f"refs/remotes/dta-pr/{successor_pr}"
    try:
        if _git_text(root, "rev-parse", "--verify", source_ref) != candidate:
            raise ValueError("PR-D candidate head does not match pull ref")
        candidate_tree = _git_text(root, "rev-parse", f"{candidate}^{{tree}}")
        merge_tree = _git_text(root, "rev-parse", f"{merge}^{{tree}}")
        if (
            candidate_tree != attestation["source_candidate_tree"]
            or merge_tree != attestation["source_merge_tree"]
            or candidate_tree != merge_tree
            or candidate == merge
        ):
            raise ValueError("PR-D squash tree identity differs")
        _require_single_parent_commit(
            root,
            merge,
            label="PR-D squash merge",
        )
        ancestor = subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", candidate, merge),
            check=False,
            capture_output=True,
        )
        if ancestor.returncode != 1:
            raise ValueError("PR-D squash candidate ancestry differs")
        subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", merge, "HEAD"),
            check=True,
            capture_output=True,
        )
        subject = _git_text(root, "show", "-s", "--format=%s", merge)
        if not (
            subject.startswith("DTA v2.2 P0 PR-D:")
            and subject.endswith(f"(#{source['pr']})")
        ):
            raise ValueError("PR-D merge subject does not bind PR number")
        for commit in (candidate, merge):
            manifest = subprocess.run(
                ("git", "-C", str(root), "show", f"{commit}:{PR_D_MANIFEST}"),
                check=True,
                capture_output=True,
            ).stdout
            if hashlib.sha256(manifest).hexdigest() != EXPECTED_MANIFEST_SHA256:
                raise ValueError("PR-D Git tree does not bind frozen manifest")
        if current == "PR-E":
            if (
                progress.get("active_pr") != successor_pr
                or progress.get("active_branch") != attestation["successor_branch"]
            ):
                raise ValueError("active PR-E identity differs from attestation")
            successor_final_head = _git_text(root, "rev-parse", "HEAD")
        else:
            pr_e = _validate_stage_record(merged[4], stage="PR-E")
            if pr_e["pr"] != successor_pr:
                raise ValueError("successor progress lacks exact PR-E provenance")
            successor_final_head = pr_e["head_sha"]
        if _git_text(root, "rev-parse", "--verify", successor_ref) != successor_final_head:
            raise ValueError("PR-E final head does not match pull ref")
        _require_single_parent_commit(
            root,
            successor_final_head,
            label="PR-E final attestation commit",
        )
        successor_head = attestation["successor_head"]
        if _git_text(root, "rev-parse", f"{successor_final_head}^") != successor_head:
            raise ValueError("PR-E attestation commit is not final single-file child")
        if _git_text(root, "rev-parse", f"{successor_head}^{{tree}}") != attestation[
            "successor_tree"
        ]:
            raise ValueError("PR-E successor tree differs")
        subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", merge, successor_head),
            check=True,
            capture_output=True,
        )
        changed = _git_text(
            root, "diff", "--name-status", "--no-renames", merge, successor_head, "--"
        )
        observed: list[str] = []
        for line in changed.splitlines():
            status, relative = line.split("\t", 1)
            if status not in {"A", "M"}:
                raise ValueError("PR-E successor changed path kind differs")
            observed.append(relative)
        if sorted(observed) != changed_paths:
            raise ValueError("PR-E successor exact changed path set differs")
        for relative in changed_paths:
            blob = subprocess.run(
                ("git", "-C", str(root), "show", f"{successor_head}:{relative}"),
                check=True,
                capture_output=True,
            ).stdout
            if hashlib.sha256(blob).hexdigest() != raw_hashes[relative]:
                raise ValueError(f"PR-E successor raw SHA-256 differs: {relative}")
        delta = _git_text(
            root,
            "diff",
            "--name-status",
            "--no-renames",
            successor_head,
            successor_final_head,
            "--",
        )
        if delta != f"A\t{PR_D_SUCCESSOR_ATTESTATION}":
            raise ValueError("PR-E attestation commit changed more than its record")
        committed = subprocess.run(
            (
                "git",
                "-C",
                str(root),
                "show",
                f"{successor_final_head}:{PR_D_SUCCESSOR_ATTESTATION}",
            ),
            check=True,
            capture_output=True,
        ).stdout
        if committed != attestation_raw.encode("utf-8"):
            raise ValueError("PR-E attestation record differs from committed bytes")
    except subprocess.CalledProcessError as error:
        raise ValueError("PR-D Git provenance is unavailable") from error


def _public_scan_plan(
    root: Path,
    progress: dict[str, Any],
) -> tuple[str, tuple[Path, ...]]:
    if progress.get("current_stage") == "PR-D":
        _require_pr_d_progress(progress)
        return "PR_D_CLOSED_SURFACE", EXPECTED_PR_D_CHANGED_PATHS
    _require_pr_d_successor_progress(root, progress)
    return "SUCCESSOR_PERSISTENT_ARTIFACTS", PERSISTENT_PR_D_ARTIFACTS


def verify_provider_summary(
    root: Path,
    *,
    summary_path: Path | None = None,
) -> dict[str, Any]:
    path = summary_path or _regular_file(root, PROVIDER_SUMMARY)
    raw = path.read_text(encoding="utf-8")
    summary = _load_json(path)
    if raw != json.dumps(summary, indent=2, ensure_ascii=False) + "\n":
        raise ValueError("PR-D Provider summary is not canonical JSON")
    payload = dict(summary)
    summary_sha = payload.pop("summary_sha256", None)
    if summary_sha != EXPECTED_SUMMARY_SHA256 or summary_sha != semantic_sha256_v22(
        payload
    ):
        raise ValueError("PR-D Provider summary digest differs")
    expected_categories = {
        "BUDGET_EXHAUSTION": 6,
        "EMPTY_SOURCE": 5,
        "INVALID_REF_CORRECTION": 1,
        "STALE_ACTION_CORRECTION": 1,
        "UNAVAILABLE_SOURCE": 5,
        "VALID_ABSTAIN": 8,
        "VALID_COMMIT": 8,
        "VALID_NO_INCIDENT": 8,
        "VALID_READ": 8,
    }
    if (
        summary.get("schema_version")
        != "dta-v22-pr-d-provider-protocol-summary.v1"
        or summary.get("goal_version") != "dta-v22-p0-master-v1"
        or summary.get("implementation_commit") != EXPECTED_IMPLEMENTATION_COMMIT
        or summary.get("implementation_tree") != EXPECTED_IMPLEMENTATION_TREE
        or summary.get("model") != PRIMARY_MODEL_V22
        or summary.get("selected_mode") != "STRICT_STRUCTURED_OUTPUT"
        or summary.get("controller_schema_sha256")
        != semantic_sha256_v22(_controller_schema_v22())
        or summary.get("provider_protocol_report_sha256") != EXPECTED_REPORT_SHA256
        or tuple(summary.get("controller_identity_sha256s", ()))
        != EXPECTED_IDENTITY_SHA256S
        or summary.get("transition_count") != 50
        or summary.get("transition_category_counts") != expected_categories
        or summary.get("controller_arm_counts")
        != {"FLAT_CANONICAL": 25, "PLANNER_LITE": 25}
        or summary.get("first_pass_accepted_count") != 48
        or summary.get("first_pass_protocol_acceptance") != 0.96
        or summary.get("post_correction_accepted_count") != 50
        or summary.get("post_correction_protocol_acceptance") != 1.0
        or summary.get("correction_count") != 2
        or summary.get("correction_rate") != 0.04
        or summary.get("invalid_dispatches") != 0
        or summary.get("provider_probe_calls") != 1
        or summary.get("provider_protocol_calls") != 52
        or summary.get("total_tokens")
        != summary.get("input_tokens", -1) + summary.get("output_tokens", -2)
        or summary.get("provider_gate_eligible") is not True
        or summary.get("terminal") != "PROVIDER_PROTOCOL_GATE_PASS"
        or summary.get("raw_provider_content_published") is not False
        or any(
            summary.get(field) != 0
            for field in (
                "agent_read_dispatches_executed",
                "agent_write_calls",
                "runbook_executions",
                "docker_calls",
            )
        )
        or not _is_sha(summary.get("response_digest_set_sha256"), 64)
    ):
        raise ValueError("PR-D Provider summary contract differs")
    if (
        _git_text(root, "rev-parse", f"{EXPECTED_IMPLEMENTATION_COMMIT}^{{tree}}")
        != EXPECTED_IMPLEMENTATION_TREE
    ):
        raise ValueError("PR-D Provider implementation tree differs")
    subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "merge-base",
            "--is-ancestor",
            EXPECTED_IMPLEMENTATION_COMMIT,
            "HEAD",
        ),
        check=True,
        capture_output=True,
    )
    _assert_no_pr_d_public_leak(raw)
    return summary


def verify_pr_d_bindings(
    root: Path,
    *,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    path = manifest_path or (root / PR_D_MANIFEST)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_MANIFEST_SHA256:
        raise ValueError("PR-D manifest raw SHA-256 differs")
    manifest = _load_json(path)
    if raw.decode("utf-8") != json.dumps(manifest, indent=2, ensure_ascii=False) + "\n":
        raise ValueError("PR-D manifest is not canonical JSON")
    if (
        manifest.get("schema_version") != "dta-v22-pr-d-controller-bindings.v1"
        or manifest.get("goal_version") != "dta-v22-p0-master-v1"
        or manifest.get("stage") != "PR-D"
        or manifest.get("base_main") != PR_D_BASE
        or manifest.get("terminal") != PR_D_TERMINAL
    ):
        raise ValueError("PR-D manifest identity differs")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or tuple(
        item.get("path") if isinstance(item, dict) else None for item in artifacts
    ) != EXPECTED_ARTIFACT_PATHS:
        raise ValueError("PR-D artifact surface differs")
    for artifact in artifacts:
        if (
            not isinstance(artifact, dict)
            or set(artifact) != {"path", "sha256"}
            or not _is_sha(artifact.get("sha256"), 64)
        ):
            raise ValueError("PR-D artifact binding differs")
        source = _regular_file(root, Path(artifact["path"]))
        if hashlib.sha256(source.read_bytes()).hexdigest() != artifact["sha256"]:
            raise ValueError(f"PR-D artifact raw SHA-256 differs: {artifact['path']}")
    provider_gate = manifest.get("provider_protocol_gate")
    if not isinstance(provider_gate, dict) or (
        provider_gate.get("public_summary_sha256") != EXPECTED_SUMMARY_SHA256
        or provider_gate.get("private_report_sha256") != EXPECTED_REPORT_SHA256
        or provider_gate.get("model") != PRIMARY_MODEL_V22
        or provider_gate.get("selected_mode") != "STRICT_STRUCTURED_OUTPUT"
        or provider_gate.get("transition_count") != 50
        or provider_gate.get("first_pass_protocol_acceptance") != 0.96
        or provider_gate.get("post_correction_protocol_acceptance") != 1.0
        or provider_gate.get("invalid_dispatches") != 0
        or provider_gate.get("provider_protocol_calls") != 52
        or provider_gate.get("terminal") != "PROVIDER_PROTOCOL_GATE_PASS"
    ):
        raise ValueError("PR-D Provider gate binding differs")
    identities = manifest.get("controller_identities")
    if not isinstance(identities, dict) or tuple(identities.values()) != (
        "STRICT_STRUCTURED_OUTPUT",
        *EXPECTED_IDENTITY_SHA256S,
    ):
        raise ValueError("PR-D controller identity binding differs")
    correction = manifest.get("bounded_correction")
    if correction != {
        "maximum_per_run": 1,
        "consumes_provider_turn_and_tokens": True,
        "read_dispatches": 0,
        "write_authority": 0,
        "second_invalid_terminal": "FAILED",
    }:
        raise ValueError("PR-D correction contract differs")
    safety = manifest.get("safety_activity")
    if safety != {
        "provider_called": True,
        "private_evidence_changed": True,
        "public_result_changed": True,
        "agent_read_dispatches_executed": 0,
        "agent_write_calls": 0,
        "docker_called": False,
        "held_out_executed": False,
        "scenario_executed": False,
        "fault_injected": False,
        "runbook_executed": False,
    }:
        raise ValueError("PR-D safety activity differs")
    successor = manifest.get("successor_attestation_contract")
    if not isinstance(successor, dict) or (
        successor.get("path") != PR_D_SUCCESSOR_ATTESTATION.as_posix()
        or successor.get("schema_version")
        != "dta-v22-pr-d-successor-attestation.v1"
        or successor.get("decision_id") != "DEC-055"
        or successor.get("source_stage") != "PR-D"
        or successor.get("successor_stage") != "PR-E"
        or successor.get("successor_branch") != "codex/dta-v22-p0-pr-e-capture-freeze"
        or successor.get("required_fields")
        != list(EXPECTED_SUCCESSOR_ATTESTATION_FIELDS)
        or successor.get("activity_expectations") != EXPECTED_PR_E_ACTIVITY
    ):
        raise ValueError("PR-D successor attestation contract differs")
    return manifest


def _verify_runtime_contracts() -> None:
    if tuple(ControllerDecisionV22.model_fields) != (
        "decision",
        "working_hypothesis_id",
        "action_id",
        "supporting_evidence_refs",
        "contradicting_evidence_refs",
    ):
        raise ValueError("shared ControllerDecision schema differs")
    if tuple(PlanCorrectionV22.model_fields) != (
        "schema_version",
        "safe_error_code",
        "current_valid_action_ids",
        "remaining_evidence_budget",
        "read_dispatches",
        "write_authority",
        "correction_sha256",
    ):
        raise ValueError("bounded correction schema differs")
    if PRIMARY_MODEL_V22 != "gpt-5.4-mini-2026-03-17":
        raise ValueError("PR-D model continuity differs")
    probe = probe_provider_output_mode_v22(
        probe=lambda _model, _mode, _schema: ProviderProbeStatusV22.SUPPORTED
    )
    identities = build_controller_identity_manifests_v22(provider_probe=probe)
    if (
        probe.selected_mode is not ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT
        or probe.report_sha256
        != "3979244217b486dcdd9f28a1e26361939272cea3ab5e14c4eed4759fe395a6e0"
        or tuple(item.arm for item in identities) != tuple(EvaluationArmV22)
        or tuple(item.identity_sha256 for item in identities)
        != EXPECTED_IDENTITY_SHA256S
        or sum(item.receives_persistent_belief_ledger for item in identities) != 1
        or next(
            item
            for item in identities
            if item.receives_persistent_belief_ledger
        ).arm
        is not EvaluationArmV22.PLANNER_LITE_SALIENT
    ):
        raise ValueError("PR-D identity reconstruction differs")
    local = run_local_protocol_capability_suite_v22(provider_probe=probe)
    if (
        local.transition_count != 50
        or local.first_pass_protocol_acceptance != 0.96
        or local.post_correction_protocol_acceptance != 1.0
        or local.invalid_dispatches != 0
        or local.provider_calls != 0
        or local.provider_gate_eligible is not False
    ):
        raise ValueError("PR-D deterministic protocol harness differs")
    forbidden = {"truth", "fixture", "expected_mechanism", "case_id"}
    for function in (
        build_common_triage_snapshot_v22,
        build_controller_turn_input_v22,
        select_deterministic_router_decision_v22,
        build_one_shot_oracle_context_v22,
        run_provider_protocol_capability_suite_v22,
        process_controller_decision_v22,
    ):
        if not forbidden.isdisjoint(inspect.signature(function).parameters):
            raise ValueError("PR-D controller exposes evaluator truth input")
    if (
        "belief_ledger_view" not in ControllerTurnInputV22.model_fields
        or "truth" in HypothesisCatalogV22.model_fields
        or "raw_response" in ProviderControllerTurnV22.model_fields
    ):
        raise ValueError("PR-D typed privacy or truth boundary differs")


def verify_pr_d_protocol(root: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    progress = _load_json(root / "docs/analysis/dta-v22-p0-master-progress.json")
    mode, paths = _public_scan_plan(root, progress)
    if mode == "PR_D_CLOSED_SURFACE":
        _verify_closed_changed_surface(root)
    for relative in paths:
        text = (
            _changed_text(root, relative)
            if mode == "PR_D_CLOSED_SURFACE"
            else _regular_file(root, relative).read_text(encoding="utf-8")
        )
        _assert_no_pr_d_public_leak(text)
    prior = verify_pr_c_protocol(root)
    verify_provider_summary(root)
    verify_pr_d_bindings(root)
    _verify_runtime_contracts()
    return {
        "schema_version": "dta-v22-pr-d-verification.v1",
        "status": "PASS",
        "historical_bindings": prior["historical_bindings"],
        "pr_c_successor_gate": "PASS",
        "public_scan_mode": mode,
        "secret_private_path_scan": "PASS",
        "truth_isolation": "PASS",
        "shared_controller_schema": "PASS",
        "bounded_correction": "PASS",
        "identity_manifests": "PASS",
        "provider_protocol_gate": "PASS",
        "terminal": PR_D_TERMINAL,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    print(json.dumps(verify_pr_d_protocol(args.root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
