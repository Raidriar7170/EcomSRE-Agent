from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from scripts.ci.verify_dta_v22_pr_b import (
    PERSISTENT_PR_B_ARTIFACTS,
    PR_B_SUCCESSOR_ATTESTATION,
    _public_scan_plan,
    verify_pr_b_bindings,
    verify_pr_b_protocol,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pr_b_verifier_closes_catalog_query_and_truth_gates() -> None:
    result = verify_pr_b_protocol(REPO_ROOT)

    assert result == {
        "schema_version": "dta-v22-pr-b-verification.v1",
        "status": "PASS",
        "historical_bindings": "PASS",
        "pr_a_successor_gate": "PASS",
        "public_scan_mode": "PR_B_CLOSED_SURFACE",
        "secret_private_path_scan": "PASS",
        "truth_isolation": "PASS",
        "action_catalog": "PASS",
        "query_semantics": "PASS",
        "terminal": "DTA_V22_PR_B_ACTION_CATALOG_READY",
    }


def test_pr_b_binding_manifest_is_raw_and_artifact_hash_bound(tmp_path: Path) -> None:
    manifest = verify_pr_b_bindings(REPO_ROOT)
    assert manifest["terminal"] == "DTA_V22_PR_B_ACTION_CATALOG_READY"
    assert len(manifest["artifacts"]) == 5

    source = REPO_ROOT / "config/dta-v22/pr-b-action-catalog-bindings.v1.json"
    tampered = tmp_path / "manifest.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["canonical_request_profile"]["logs"]["max_records"] = 20
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest raw SHA-256"):
        verify_pr_b_bindings(REPO_ROOT, manifest_path=tampered)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(root), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _successor_repo(
    tmp_path: Path,
) -> tuple[
    Path,
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    root = tmp_path / "successor"
    root.mkdir(parents=True)
    _git(root, "init")
    _git(root, "config", "user.email", "ci@example.invalid")
    _git(root, "config", "user.name", "CI")
    base_marker = root / "README.md"
    base_marker.write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "PR-A merge base")
    base = _git(root, "rev-parse", "HEAD")

    _git(root, "checkout", "-b", "pr-b-candidate")
    manifest = root / "config/dta-v22/pr-b-action-catalog-bindings.v1.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(
        (REPO_ROOT / "config/dta-v22/pr-b-action-catalog-bindings.v1.json").read_bytes()
    )
    _git(root, "add", manifest.relative_to(root).as_posix())
    _git(root, "commit", "-m", "PR-B candidate")
    candidate = _git(root, "rev-parse", "HEAD")
    candidate_tree = _git(root, "rev-parse", "HEAD^{tree}")
    _git(root, "update-ref", "refs/remotes/dta-pr/58", candidate)

    _git(root, "checkout", "--detach", base)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_bytes(
        (REPO_ROOT / "config/dta-v22/pr-b-action-catalog-bindings.v1.json").read_bytes()
    )
    _git(root, "add", manifest.relative_to(root).as_posix())
    _git(
        root,
        "commit",
        "-m",
        "DTA v2.2 P0 PR-B: action catalog and query semantics (#58)",
    )
    merge = _git(root, "rev-parse", "HEAD")
    merge_tree = _git(root, "rev-parse", "HEAD^{tree}")
    assert candidate_tree == merge_tree

    successor_file = root / "src/ecomsre/dta_v2/v22/pr_c_marker.txt"
    successor_file.parent.mkdir(parents=True)
    successor_file.write_text("pr-c\n", encoding="utf-8")
    successor_relative = successor_file.relative_to(root).as_posix()
    _git(root, "add", successor_relative)
    _git(root, "commit", "-m", "PR-C pre-attestation candidate")
    successor_head = _git(root, "rev-parse", "HEAD")
    successor_tree = _git(root, "rev-parse", "HEAD^{tree}")

    pr_a = {
        "stage": "PR-A",
        "pr": 57,
        "head_sha": "57bc106ef181d4ca8bafb3eb00372c1084bd0c60",
        "merge_commit": "9d53002c3d86208a67b73d271c5eaf6e2f45b8b7",
    }
    pr_b: dict[str, object] = {
        "stage": "PR-B",
        "pr": 58,
        "head_sha": candidate,
        "merge_commit": merge,
    }
    payload: dict[str, object] = {
        "schema_version": "dta-v22-pr-b-successor-attestation.v1",
        "goal_version": "dta-v22-p0-master-v1",
        "decision_id": "DEC-055",
        "repository": "Raidriar7170/EcomSRE-Agent",
        "source_stage": "PR-B",
        "source_pr": 58,
        "source_candidate_head": candidate,
        "source_candidate_tree": candidate_tree,
        "source_merge_commit": merge,
        "source_merge_tree": merge_tree,
        "successor_stage": "PR-C",
        "successor_pr": 59,
        "successor_branch": "codex/dta-v22-p0-pr-c-memory-predicates-diagnosis",
        "base_main_head": merge,
        "successor_head": successor_head,
        "successor_tree": successor_tree,
        "changed_paths": [successor_relative],
        "raw_sha256_by_path": {
            successor_relative: hashlib.sha256(successor_file.read_bytes()).hexdigest()
        },
        "provider_called": False,
        "docker_called": False,
        "held_out_executed": False,
        "scenario_executed": False,
        "fault_injected": False,
        "runbook_executed": False,
        "private_evidence_changed": False,
        "public_result_changed": False,
        "execution_report_rebound": False,
    }
    attestation = {**payload, "record_sha256": semantic_sha256_v22(payload)}
    attestation_path = root / PR_B_SUCCESSOR_ATTESTATION
    attestation_path.write_text(
        json.dumps(attestation, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _git(root, "add", PR_B_SUCCESSOR_ATTESTATION.as_posix())
    _git(root, "commit", "-m", "Seal PR-B administrative successor")
    pr_c_head = _git(root, "rev-parse", "HEAD")
    _git(root, "update-ref", "refs/remotes/dta-pr/59", pr_c_head)
    pr_c: dict[str, object] = {
        "stage": "PR-C",
        "pr": 59,
        "head_sha": pr_c_head,
        "merge_commit": "5" * 40,
    }
    return root, pr_a, pr_b, pr_c


def _rewrite_attestation(root: Path, **updates: object) -> None:
    attestation_path = root / PR_B_SUCCESSOR_ATTESTATION
    value = json.loads(attestation_path.read_text(encoding="utf-8"))
    value.pop("record_sha256")
    value.update(updates)
    value["record_sha256"] = semantic_sha256_v22(value)
    attestation_path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def test_pr_b_gate_has_a_successor_safe_persistent_mode(tmp_path: Path) -> None:
    root, pr_a, pr_b, _pr_c = _successor_repo(tmp_path)
    mode, paths = _public_scan_plan(
        root,
        {
            "current_stage": "PR-C",
            "completed_stage": "PR-B",
            "active_branch": "codex/dta-v22-p0-pr-c-memory-predicates-diagnosis",
            "active_pr": 59,
            "merged_prs": [pr_a, pr_b],
        }
    )

    assert mode == "SUCCESSOR_PERSISTENT_ARTIFACTS"
    assert paths == PERSISTENT_PR_B_ARTIFACTS

    with pytest.raises(ValueError, match="PR-B merge provenance"):
        _public_scan_plan(
            root,
            {
                "current_stage": "PR-C",
                "completed_stage": "PR-B",
                "active_branch": "codex/dta-v22-p0-pr-c-memory-predicates-diagnosis",
                "active_pr": 59,
                "merged_prs": [pr_a],
            }
        )


def test_pr_b_successor_gate_accepts_pr_c_squash_merge_tree(tmp_path: Path) -> None:
    root, pr_a, pr_b, pr_c = _successor_repo(tmp_path)
    tree = _git(root, "rev-parse", f"{pr_c['head_sha']}^{{tree}}")
    squash = _git(
        root,
        "commit-tree",
        tree,
        "-p",
        str(pr_b["merge_commit"]),
        "-m",
        "DTA v2.2 P0 PR-C: memory predicates and diagnosis (#59)",
    )
    _git(root, "checkout", "--detach", squash)

    mode, paths = _public_scan_plan(
        root,
        {
            "current_stage": "PR-C",
            "completed_stage": "PR-B",
            "active_branch": "codex/dta-v22-p0-pr-c-memory-predicates-diagnosis",
            "active_pr": 59,
            "merged_prs": [pr_a, pr_b],
        },
    )

    assert mode == "SUCCESSOR_PERSISTENT_ARTIFACTS"
    assert paths == PERSISTENT_PR_B_ARTIFACTS


def test_pr_b_successor_gate_rejects_fake_shas(tmp_path: Path) -> None:
    root, pr_a, pr_b, _pr_c = _successor_repo(tmp_path)
    pr_b["head_sha"] = "1" * 40
    _rewrite_attestation(root, source_candidate_head="1" * 40)
    with pytest.raises(ValueError, match="pull ref"):
        _public_scan_plan(
            root,
            {
                "current_stage": "PR-C",
                "completed_stage": "PR-B",
                "active_branch": "codex/dta-v22-p0-pr-c-memory-predicates-diagnosis",
                "active_pr": 59,
                "merged_prs": [pr_a, pr_b],
            },
        )


def test_pr_b_successor_gate_accepts_complete_terminal(tmp_path: Path) -> None:
    root, pr_a, pr_b, pr_c = _successor_repo(tmp_path)
    later = [
        {"stage": stage, "pr": number, "head_sha": "3" * 40, "merge_commit": "4" * 40}
        for number, stage in enumerate(("PR-D", "PR-E", "PR-F"), start=60)
    ]
    mode, paths = _public_scan_plan(
        root,
        {
            "current_stage": "COMPLETE",
            "completed_stage": "PR-F",
            "merged_prs": [pr_a, pr_b, pr_c, *later],
        },
    )
    assert mode == "SUCCESSOR_PERSISTENT_ARTIFACTS"
    assert paths == PERSISTENT_PR_B_ARTIFACTS


def test_pr_b_successor_gate_binds_exact_paths_hashes_and_activity(
    tmp_path: Path,
) -> None:
    root, pr_a, pr_b, _pr_c = _successor_repo(tmp_path)
    progress = {
        "current_stage": "PR-C",
        "completed_stage": "PR-B",
        "active_branch": "codex/dta-v22-p0-pr-c-memory-predicates-diagnosis",
        "active_pr": 59,
        "merged_prs": [pr_a, pr_b],
    }
    _rewrite_attestation(root, provider_called=True)
    with pytest.raises(ValueError, match="attestation differs"):
        _public_scan_plan(root, progress)

    root, pr_a, pr_b, _pr_c = _successor_repo(tmp_path / "hash-case")
    progress["merged_prs"] = [pr_a, pr_b]
    attestation = json.loads(
        (root / PR_B_SUCCESSOR_ATTESTATION).read_text(encoding="utf-8")
    )
    hashes = dict(attestation["raw_sha256_by_path"])
    hashes[attestation["changed_paths"][0]] = "f" * 64
    _rewrite_attestation(root, raw_sha256_by_path=hashes)
    with pytest.raises(ValueError, match="raw SHA-256"):
        _public_scan_plan(root, progress)
