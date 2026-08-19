"""Verify DTA v2.2 PR-A history, truth isolation, and public safety gates."""

from __future__ import annotations

import argparse
import configparser
import json
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Sequence

from scripts.ci.verify_dta_v22_historical_bindings import (
    verify_historical_bindings,
)


HISTORICAL_MANIFEST = Path("config/dta-v22/historical-bindings.v1.json")
EXPECTED_PR_A_CHANGED_PATHS = (
    Path(".github/workflows/agent-mainline.yml"),
    Path("config/dta-v22/historical-bindings.v1.json"),
    Path("docs/DECISIONS.md"),
    Path("docs/SAFETY_BOUNDARIES.md"),
    Path("docs/analysis/dta-v21-forensic-audit-for-v22.md"),
    Path("docs/analysis/dta-v21-private-failure-taxonomy-summary.json"),
    Path("docs/analysis/dta-v22-p0-master-progress.json"),
    Path("docs/design/diagnosis-to-action-v2.2-p0.md"),
    Path("docs/design/dta-v22-evaluation-metrics.md"),
    Path("docs/human-briefs/2026-08-19-dta-v22-pr-a-protocol-audit.md"),
    Path("mypy.ini"),
    Path("scripts/ci/verify_dta_v22_historical_bindings.py"),
    Path("scripts/ci/verify_dta_v22_pr_a.py"),
    Path("src/ecomsre/dta_v2/v22/__init__.py"),
    Path("tests/conftest.py"),
    Path("tests/dta_v22/test_historical_bindings.py"),
    Path("tests/dta_v22/test_v22_pr_a_protocol.py"),
)
PUBLIC_PR_A_ARTIFACTS = (
    Path("config/dta-v22/historical-bindings.v1.json"),
    Path("docs/analysis/dta-v21-forensic-audit-for-v22.md"),
    Path("docs/analysis/dta-v21-private-failure-taxonomy-summary.json"),
    Path("docs/analysis/dta-v22-p0-master-progress.json"),
    Path("docs/design/diagnosis-to-action-v2.2-p0.md"),
    Path("docs/design/dta-v22-evaluation-metrics.md"),
    Path("docs/human-briefs/2026-08-19-dta-v22-pr-a-protocol-audit.md"),
)
_FORBIDDEN_PUBLIC_SUBSTRINGS = (
    "/" + "users/",
    "private" + "_root",
    "provider" + "_response",
    "api" + "_key",
    "authorization" + ":",
    "chain" + "_of_thought",
)
_FORBIDDEN_PUBLIC_PATTERNS = (
    re.compile(r"/(?:Users|home)/[^\s\"']+"),
    re.compile(r"[A-Za-z]:\\[^\s\"']+"),
    re.compile(r"bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}", re.IGNORECASE),
    re.compile(r"ECOMSRE_LLM_(?:API_" + r"KEY|BASE_URL)", re.IGNORECASE),
)
_FORBIDDEN_TAXONOMY_KEYS = frozenset(
    {
        "case_id",
        "case_mapping",
        "chain" + "_of_thought",
        "expected_action",
        "expected_mechanism",
        "expected_root_cause",
        "fault_controller_state",
        "injected_fault",
        "private_path",
        "provider" + "_response",
        "rationale",
        "raw_provider_content",
    }
)
_TAXONOMY_FIELDS = frozenset(
    {
        "schema_version",
        "source_generation",
        "held_out_execution_id",
        "held_out_seal_sha256",
        "planner_identity_sha256",
        "planner_held_out_entries",
        "protocol_accepted_entries",
        "provider_protocol_failure_entries",
        "failure_code_counts",
        "bounded_provider_failure_chain_counts",
        "raw_provider_content_published",
        "private_case_mapping_published",
        "private_paths_published",
        "historical_execution_rerun",
        "interpretation",
    }
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


def _regular_public_file(root: Path, relative: Path) -> Path:
    candidate = root / relative
    details = candidate.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError(f"public PR-A artifact must be a regular file: {relative}")
    if not candidate.resolve(strict=True).is_relative_to(root):
        raise ValueError(f"public PR-A artifact escapes repository: {relative}")
    return candidate


def _git_paths(root: Path, *args: str) -> set[Path]:
    completed = subprocess.run(
        ("git", "-C", str(root), *args),
        check=True,
        capture_output=True,
        text=True,
    )
    return {Path(item) for item in completed.stdout.splitlines() if item}


def _changed_text(root: Path, relative: Path) -> str:
    baseline_object = subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "cat-file",
            "-e",
            f"9da92d54a4fb470c5452cee36a731e81529d05a5:{relative.as_posix()}",
        ),
        check=False,
        capture_output=True,
    )
    path = _regular_public_file(root, relative)
    if baseline_object.returncode != 0:
        return path.read_text(encoding="utf-8")
    diff = subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "diff",
            "--unified=0",
            "9da92d54a4fb470c5452cee36a731e81529d05a5",
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
    observed = _git_paths(root, "diff", "--name-only", "9da92d54a4fb470c5452cee36a731e81529d05a5", "--")
    observed.update(_git_paths(root, "ls-files", "--others", "--exclude-standard"))
    expected = set(EXPECTED_PR_A_CHANGED_PATHS)
    if observed != expected:
        undeclared = sorted(str(item) for item in observed - expected)
        missing = sorted(str(item) for item in expected - observed)
        raise ValueError(
            f"PR-A changed surface differs: undeclared={undeclared}, missing={missing}"
        )


def _public_scan_plan(progress: dict[str, Any]) -> tuple[str, tuple[Path, ...]]:
    if progress.get("current_stage") == "PR-A":
        if progress.get("completed_stage") is not None:
            raise ValueError("PR-A progress has an invalid completed stage")
        return "PR_A_CLOSED_SURFACE", EXPECTED_PR_A_CHANGED_PATHS
    completed = progress.get("completed_stage")
    if completed not in {"PR-A", "PR-B", "PR-C", "PR-D", "PR-E", "PR-F"}:
        raise ValueError("successor progress does not prove PR-A completion")
    return "SUCCESSOR_PERSISTENT_ARTIFACTS", PUBLIC_PR_A_ARTIFACTS


def assert_no_public_leak(text: str) -> None:
    """Reject private absolute paths and common credential shapes."""

    folded = text.casefold()
    if any(item in folded for item in _FORBIDDEN_PUBLIC_SUBSTRINGS) or any(
        pattern.search(text) is not None for pattern in _FORBIDDEN_PUBLIC_PATTERNS
    ):
        raise ValueError("public leakage detected")


def _assert_no_forbidden_taxonomy_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in _FORBIDDEN_TAXONOMY_KEYS:
                raise ValueError(f"private taxonomy field is forbidden: {key}")
            _assert_no_forbidden_taxonomy_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_taxonomy_keys(child)


def _verify_taxonomy(root: Path) -> None:
    taxonomy = _load_json(
        root / "docs/analysis/dta-v21-private-failure-taxonomy-summary.json"
    )
    if frozenset(taxonomy) != _TAXONOMY_FIELDS:
        raise ValueError("private taxonomy summary fields changed")
    expected_scalars = {
        "schema_version": "dta-v22.v21-private-failure-taxonomy-summary.v1",
        "source_generation": "DTA_V21_FROZEN_HELD_OUT_PRIVATE_AGGREGATION",
        "held_out_execution_id": "53615cdd78b348b68496f64102c0b4de",
        "held_out_seal_sha256": (
            "9a7c8e56400e99c693c8bddc26007b1dd26e0dcee2167b07cf3fba00fd22fbd7"
        ),
        "planner_identity_sha256": (
            "80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d"
        ),
        "planner_held_out_entries": 8,
        "protocol_accepted_entries": 2,
        "provider_protocol_failure_entries": 6,
        "failure_code_counts": {"PROVIDER_PROTOCOL_FAILURE": 6},
    }
    if any(taxonomy[field] != expected for field, expected in expected_scalars.items()):
        raise ValueError("private taxonomy provenance or top-level counts changed")
    if taxonomy["bounded_provider_failure_chain_counts"] != {
        "hypotheses:value_error": 1,
        "output:planner_abstain_output_shape": 5,
        "output:value_error": 5,
    }:
        raise ValueError("private taxonomy bounded counts changed")
    if any(
        taxonomy[field] is not False
        for field in (
            "raw_provider_content_published",
            "private_case_mapping_published",
            "private_paths_published",
            "historical_execution_rerun",
        )
    ):
        raise ValueError("private taxonomy publication boundary changed")
    _assert_no_forbidden_taxonomy_keys(taxonomy)


def _verify_protocol_markers(root: Path) -> None:
    design = (root / "docs/design/diagnosis-to-action-v2.2-p0.md").read_text(
        encoding="utf-8"
    )
    scoring = (root / "docs/design/dta-v22-evaluation-metrics.md").read_text(
        encoding="utf-8"
    )
    normalized_design = " ".join(design.split())
    normalized_scoring = " ".join(scoring.split())
    required_design = (
        "BLOCKED_DTA_V22_MODEL_CONTINUITY",
        "no silent model swap",
        "architecture + model joint successor",
        "Evaluator truth, fixtures, expected sources/mechanisms, and fault controllers are forbidden inputs.",
        "Live Agent write authority: `0`",
    )
    required_scoring = (
        "final Diagnosis given the evidence actually selected by the controller",
        "false-positive rate = fault Diagnosis on `NO_INCIDENT` truth",
        "true-negative rate = correct `NO_INCIDENT` / all `NO_INCIDENT` cases",
        "false-negative incident rate = `NO_INCIDENT` on fault truth / all fault cases",
        "at least two preregistered Provider-stability measurements",
    )
    if any(marker not in normalized_design for marker in required_design):
        raise ValueError("DTA v2.2 design protocol marker is missing")
    if any(marker not in normalized_scoring for marker in required_scoring):
        raise ValueError("DTA v2.2 scoring protocol marker is missing")


def verify_v21_mypy_exception(path: Path) -> None:
    """Bind the sole frozen-v2.1 mypy exception without a wildcard escape."""

    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError("frozen v2.1 mypy exception config is unsafe")
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(path.read_text(encoding="utf-8"))
    except (configparser.Error, UnicodeDecodeError) as error:
        raise ValueError("frozen v2.1 mypy exception config is invalid") from error
    target = "mypy-ecomsre.dta_v2.v21.live_final_cli"
    v21_sections = tuple(
        section
        for section in parser.sections()
        if section.startswith("mypy-ecomsre.dta_v2.v21")
    )
    if v21_sections != (target,) or dict(parser[target]) != {
        "disable_error_code": "arg-type"
    }:
        raise ValueError("frozen v2.1 mypy exception was broadened")
    unsafe_global_options = {"disable_error_code", "ignore_errors"}
    global_options = parser["mypy"]
    if unsafe_global_options.intersection(global_options):
        raise ValueError("frozen v2.1 mypy exception has a global bypass")
    if global_options.get("follow_imports", "normal") == "skip":
        raise ValueError("frozen v2.1 mypy exception has a global import bypass")


def verify_pr_a_protocol(project_root: Path) -> dict[str, str]:
    """Run all deterministic PR-A gates without Provider or Docker access."""

    root = project_root.resolve(strict=True)
    verify_historical_bindings(root, root / HISTORICAL_MANIFEST)
    progress = _load_json(root / "docs/analysis/dta-v22-p0-master-progress.json")
    scan_mode, scan_paths = _public_scan_plan(progress)
    if scan_mode == "PR_A_CLOSED_SURFACE":
        _verify_closed_changed_surface(root)
    for relative in scan_paths:
        text = (
            _changed_text(root, relative)
            if scan_mode == "PR_A_CLOSED_SURFACE"
            else _regular_public_file(root, relative).read_text(encoding="utf-8")
        )
        assert_no_public_leak(text)
    _verify_taxonomy(root)
    _verify_protocol_markers(root)
    verify_v21_mypy_exception(root / "mypy.ini")
    return {
        "historical_bindings": "PASS",
        "public_scan_mode": scan_mode,
        "mypy_frozen_exception": "PASS",
        "secret_private_path_scan": "PASS",
        "status": "DTA_V22_PR_A_PROTOCOL_GATES_PASS",
        "truth_isolation": "PASS",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify DTA v2.2 PR-A gates.")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    print(
        json.dumps(
            verify_pr_a_protocol(args.project_root),
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
