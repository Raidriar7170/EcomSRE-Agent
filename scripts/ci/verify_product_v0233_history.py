#!/usr/bin/env python3
"""Verify the merged v0.2.3.2.3 handoff without rewriting predecessor bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22


HISTORY_AND_HANDOFF_PASS_V0233 = (
    "ECOMSRE_PRODUCT_V0233_HISTORY_AND_HANDOFF_PASS"
)
_STARTING_MAIN = "6e07964e5595b4138decf0276189c76c3e278d87"
_PR85_MERGE = "d1a2f934620bf904d354e176732d3e66bfe6bbca"
_PR85_HEAD = "75ab277982c25be6d2b37e027db247526580a111"
_HANDOFF_SHA256 = "72d272951412d696d50fb6ee44c96bbc4a1a6e5ace63d574b0636297b848847f"
_CLOSEOUT_SHA256 = "87ef73ec6c0843d41865bf6406e9df8e46a90dedaf1c6cabdd3ab9c25a49e025"

_FROZEN_ARTIFACTS = (
    (
        "config/product-v02323/repository-state-manifest.json",
        "7a2ef6e2b6f67528ee586bf184c5a29babf4d7715716ec28f1d11c99f81f3b77",
        4632,
    ),
    (
        "docs/analysis/product-v02322-private-failure-contract.json",
        "8dae27ee1cc52fb18156579d4da6989b3eb37d739208e6a1ef64d03a81081861",
        2034,
    ),
    (
        "docs/analysis/product-v02322-stage-journal-contract.json",
        "af303d6aa5f763a72c203364b9b93ce8cf25a7a3840694d988d220fc0f02afb8",
        1980,
    ),
    (
        "docs/analysis/product-v02323-diagnosis-replay.json",
        "6caf2ebda423dcb166c1c6f846a71b1a913bcb13cee6de893f8e32ff85793d48",
        2721,
    ),
    (
        "docs/analysis/product-v02323-fresh-formal-handoff.json",
        "3eac006e08ae08692f9f3c29ffd0607dd62fa64a3c3409b93e6b8b49a6dc941f",
        3408,
    ),
    (
        "docs/analysis/product-v02323-reconstruction-disposition.json",
        "ada4321c104e9c9a57a8180fd3543c8ba16ede3dadf3aaaaee89c62ba2e2f27b",
        1354,
    ),
    (
        "docs/results/product-v02323-engineering-closeout.json",
        "d0a816a9a626bea255f931be50540730456cbc805263d89b9ce0ef9e005b603d",
        3742,
    ),
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git_bytes(root: Path, revision: str, relative: str) -> bytes:
    try:
        return subprocess.run(
            ("git", "-C", str(root), "show", f"{revision}:{relative}"),
            check=True,
            capture_output=True,
        ).stdout
    except subprocess.CalledProcessError as error:
        raise ValueError("Product v0.2.3.3 historical Git binding differs") from error


def _require_git_ancestry(root: Path) -> None:
    for ancestor in (_PR85_HEAD, _PR85_MERGE, _STARTING_MAIN):
        try:
            subprocess.run(
                ("git", "-C", str(root), "cat-file", "-e", f"{ancestor}^{{commit}}"),
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as error:
            raise ValueError("Product v0.2.3.3 historical commit is missing") from error
    head_tree = subprocess.run(
        ("git", "-C", str(root), "rev-parse", f"{_PR85_HEAD}^{{tree}}"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    merge_tree = subprocess.run(
        ("git", "-C", str(root), "rev-parse", f"{_PR85_MERGE}^{{tree}}"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if head_tree != merge_tree:
        raise ValueError("Product v0.2.3.3 squash-merge tree differs")
    checks = ((_PR85_MERGE, _STARTING_MAIN), (_STARTING_MAIN, "HEAD"))
    for ancestor, descendant in checks:
        result = subprocess.run(
            (
                "git",
                "-C",
                str(root),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ),
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            raise ValueError("Product v0.2.3.3 historical ancestry differs")


def _load_self_sealed(path: Path, seal_field: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    supplied = payload.get(seal_field)
    body = dict(payload)
    body.pop(seal_field, None)
    if supplied != semantic_sha256_v22(body):
        raise ValueError(f"Product v0.2.3.3 predecessor seal differs: {path.name}")
    return payload


def verify_product_v0233_history(
    root: Path,
    *,
    manifest_path: Path | None = None,
) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    manifest_file = manifest_path or project / "config/product-v0233/historical-results.v1.json"
    manifest = _load_self_sealed(manifest_file, "manifest_sha256")
    expected_core = {
        "schema_version": "ecomsre.product.historical-results.v0233",
        "goal_version": "ecomsre-product-v0233-fresh-formal-evidence-bound-nofault-v1",
        "terminal": HISTORY_AND_HANDOFF_PASS_V0233,
        "starting_main": _STARTING_MAIN,
        "branch": "codex/product-v0233-fresh-formal-nofault-acceptance",
        "predecessor": {
            "pull_request": 85,
            "merge_commit": _PR85_MERGE,
            "head_commit": _PR85_HEAD,
            "engineering_terminal": "ECOMSRE_PRODUCT_V02323_SCHEMA8_RECONSTRUCTION_DIAGNOSIS_REPLAY_COMPLETE",
            "engineering_closeout_sha256": _CLOSEOUT_SHA256,
            "repository_terminal": "ECOMSRE_PRODUCT_V02323_REPOSITORY_ACCEPTANCE_PASS",
            "handoff_terminal": "ECOMSRE_PRODUCT_V02323_FRESH_FORMAL_NOFAULT_HANDOFF_READY",
            "handoff_sha256": _HANDOFF_SHA256,
            "replay_classification": "STRUCTURAL_CONTRACT_REPLAY",
            "root_cause_disposition": "ECOMSRE_PRODUCT_V02323_ORIGINAL_ROOT_CAUSE_UNPROVEN",
            "measured_nofault_authority": "NONE",
            "knowledge_loop_authority": "NONE",
        },
        "safety_counters": {
            "fault_attempts": 0,
            "new_baseline_attempts": 0,
            "opensearch_profile_changes": 0,
            "knowledge_loop_campaigns": 0,
            "fault_family_rule_mining_promotion": 0,
            "provider_agent_runbook_calls": 0,
            "formal_product_state_clones": 0,
            "formal_healthy_traffic_executions": 0,
            "new_product_incidents": 0,
            "new_product_diagnoses": 0,
            "measured_semantic_results": 0,
            "action_authority": "NONE",
        },
    }
    for key, value in expected_core.items():
        if manifest.get(key) != value:
            raise ValueError("Product v0.2.3.3 historical manifest differs")
    expected_artifacts = [
        {"path": path, "file_sha256": digest, "size_bytes": size}
        for path, digest, size in _FROZEN_ARTIFACTS
    ]
    if manifest.get("frozen_artifacts") != expected_artifacts:
        raise ValueError("Product v0.2.3.3 frozen artifact manifest differs")

    _require_git_ancestry(project)
    for relative, expected_sha256, expected_size in _FROZEN_ARTIFACTS:
        current = project / relative
        current_bytes = current.read_bytes()
        starting_bytes = _git_bytes(project, _STARTING_MAIN, relative)
        if (
            current.is_symlink()
            or len(current_bytes) != expected_size
            or _sha256_bytes(current_bytes) != expected_sha256
            or current_bytes != starting_bytes
        ):
            raise ValueError(f"Product v0.2.3.3 predecessor bytes differ: {relative}")

    handoff = _load_self_sealed(
        project / "docs/analysis/product-v02323-fresh-formal-handoff.json",
        "handoff_sha256",
    )
    closeout = _load_self_sealed(
        project / "docs/results/product-v02323-engineering-closeout.json",
        "closeout_sha256",
    )
    if (
        handoff.get("terminal")
        != "ECOMSRE_PRODUCT_V02323_FRESH_FORMAL_NOFAULT_HANDOFF_READY"
        or handoff.get("handoff_sha256") != _HANDOFF_SHA256
        or handoff.get("replay_classification") != "STRUCTURAL_CONTRACT_REPLAY"
        or handoff.get("root_cause_disposition")
        != "ECOMSRE_PRODUCT_V02323_ORIGINAL_ROOT_CAUSE_UNPROVEN"
        or handoff.get("measured_nofault_authority") != "NONE"
        or handoff.get("knowledge_loop_authority") != "NONE"
        or handoff.get("successor_pull_request") != 85
        or handoff.get("merged_successor_commit") != _PR85_MERGE
        or closeout.get("closeout_sha256") != _CLOSEOUT_SHA256
        or closeout.get("repository_acceptance_terminal")
        != "ECOMSRE_PRODUCT_V02323_REPOSITORY_ACCEPTANCE_PASS"
    ):
        raise ValueError("Product v0.2.3.3 handoff binding differs")

    return {
        "terminal": HISTORY_AND_HANDOFF_PASS_V0233,
        "starting_main": _STARTING_MAIN,
        "merged_pull_request": 85,
        "merge_commit": _PR85_MERGE,
        "predecessor_head": _PR85_HEAD,
        "handoff_sha256": _HANDOFF_SHA256,
        "engineering_closeout_sha256": _CLOSEOUT_SHA256,
        "replay_classification": "STRUCTURAL_CONTRACT_REPLAY",
        "root_cause_disposition": "ECOMSRE_PRODUCT_V02323_ORIGINAL_ROOT_CAUSE_UNPROVEN",
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
        "action_authority": "NONE",
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(argv)
    print(json.dumps(verify_product_v0233_history(arguments.root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "HISTORY_AND_HANDOFF_PASS_V0233",
    "verify_product_v0233_history",
)
