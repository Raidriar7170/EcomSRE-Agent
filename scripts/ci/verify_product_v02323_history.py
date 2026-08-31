#!/usr/bin/env python3
"""Verify frozen PR #82, PR #83, and PR #84 Product history."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from scripts.ci.verify_product_v02322_history import verify_product_v02322_history


HISTORY_AND_BLOCKER_PASS_V02323 = "ECOMSRE_PRODUCT_V02323_HISTORY_AND_BLOCKER_PASS"
STARTING_MAIN_V02323 = "73fe478886a4f0875b4d60b07b3600e8aae02132"
PR82_HEAD_V02323 = "cc270e5624af573a12bc31f3df9ca8cacad8685d"
PR83_HEAD_V02323 = "142dc1094926f18e789ece3668c34918f859b512"
PR84_HEAD_V02323 = "0dfd9c93f7e1f8797aacfee198694b5b2380221c"
EXPECTED_SCHEMA8_RAW_SHA256_V02323 = (
    "25d0fae060c396e63f338de886da97885c21508d265a94f1e45b999b5bc206f6"
)
OBSERVED_SCHEMA9_RAW_SHA256_V02323 = (
    "c8dbe4a5c500c577988e433ef9921b31cc920983e39d458907e5481937561d37"
)
EXPECTED_PR83_EVIDENCE_SHA256_V02323 = {
    "formal_blocker_semantic_sha256": (
        "2f8f6fd26c7783091c00fb9cdcfaa29f145b4d29b31f16ec6ac1c8fb3e9999f1"
    ),
    "formal_state_clone_report_sha256": (
        "7073a69315430e72b73a1d4ad54b06d5b3cc400d11465e583252a2f75c38fbb5"
    ),
    "formal_state_clone_sha256": (
        "ebe5fce84300475cca3873bbbd6e3ec00cb9d5467789e7f210b564820bc68546"
    ),
    "formal_traffic_execution_sha256": (
        "930d5985c88aa8d797f0c1a268ae4b8ece26302480bff3797a61a0988899406e"
    ),
    "fresh_runtime_snapshot_proof_sha256": (
        "87397ce672d3b833a61d2e9b4105f407e30ab3c1eadcba4adf00adcc185187e3"
    ),
    "formal_blocker_evidence_manifest_sha256": (
        "6104953a87e3307ae826de6e3348d651d82fd7708f7dbf8341962666a0b93129"
    ),
}
EXPECTED_PR84_COMPLETED_TERMINALS_V02323 = [
    "ECOMSRE_PRODUCT_V02322_HISTORY_AND_BLOCKER_PASS",
    "ECOMSRE_PRODUCT_V02322_REPOSITORY_STATE_MODEL_PASS",
    "ECOMSRE_PRODUCT_V02322_DIAGNOSIS_STAGE_JOURNAL_PASS",
    "ECOMSRE_PRODUCT_V02322_PRIVATE_FAILURE_EVIDENCE_PASS",
]
EXPECTED_COUNTERS_V02323 = {
    "fault_attempts": 0,
    "new_baseline_attempts": 0,
    "new_business_traffic_executions": 0,
    "new_product_incidents": 0,
    "measured_nofault_terminals": 0,
    "knowledge_loop_campaigns": 0,
    "fault_family_rule_mining_promotions": 0,
    "provider_agent_runbook_docker_calls": 0,
    "diagnosis_persistence_replay_attempts": 0,
}
EXPECTED_AUTHORITY_V02323 = {
    "product_action_authority": "NONE",
    "source_state_mutation_authority": "NONE",
    "measured_nofault_authority": "NONE",
    "knowledge_loop_authority": "NONE",
}


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _git_bytes(root: Path, revision: str, relative: str) -> bytes:
    return subprocess.run(
        ("git", "show", f"{revision}:{relative}"),
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


def _require_commit(root: Path, revision: str) -> None:
    subprocess.run(
        ("git", "cat-file", "-e", f"{revision}^{{commit}}"),
        cwd=root,
        check=True,
        capture_output=True,
    )


def _require_ancestry(root: Path, ancestor: str, descendant: str) -> None:
    subprocess.run(
        ("git", "merge-base", "--is-ancestor", ancestor, descendant),
        cwd=root,
        check=True,
        capture_output=True,
    )


def _require_pr84_tracked_bytes(root: Path, tracked: object) -> None:
    if not isinstance(tracked, list) or not tracked:
        raise ValueError("Product v0.2.3.2.3 PR #84 bindings differ")
    paths: list[str] = []
    for item in tracked:
        if not isinstance(item, Mapping):
            raise ValueError("Product v0.2.3.2.3 PR #84 binding differs")
        relative = item.get("path")
        expected_sha256 = item.get("sha256")
        expected_size = item.get("size_bytes")
        if (
            not isinstance(relative, str)
            or item.get("revision") != PR84_HEAD_V02323
            or not isinstance(expected_sha256, str)
            or not isinstance(expected_size, int)
        ):
            raise ValueError("Product v0.2.3.2.3 PR #84 binding differs")
        paths.append(relative)
        local = root / relative
        predecessor = _git_bytes(root, PR84_HEAD_V02323, relative)
        if (
            local.is_symlink()
            or not local.is_file()
            or local.read_bytes() != predecessor
            or len(predecessor) != expected_size
            or hashlib.sha256(predecessor).hexdigest() != expected_sha256
        ):
            raise ValueError(f"Product v0.2.3.2.3 PR #84 bytes differ: {relative}")
    if paths != sorted(set(paths)):
        raise ValueError("Product v0.2.3.2.3 PR #84 path set differs")


def verify_product_v02323_history(
    root: Path,
    *,
    manifest_path: Path | None = None,
) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    manifest = _load_object(
        manifest_path
        or project / "config/product-v02323/historical-results.v1.json"
    )
    body = dict(manifest)
    supplied_sha256 = body.pop("manifest_sha256", None)
    if (
        manifest.get("schema_version")
        != "ecomsre.product-v02323.historical-results.v1"
        or manifest.get("goal_version")
        != "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
        or manifest.get("starting_main") != STARTING_MAIN_V02323
        or supplied_sha256 != semantic_sha256_v22(body)
    ):
        raise ValueError("Product v0.2.3.2.3 historical manifest differs")

    predecessors = manifest.get("predecessors")
    if not isinstance(predecessors, Mapping):
        raise ValueError("Product v0.2.3.2.3 predecessor history differs")
    pr82 = predecessors.get("pr82")
    pr83 = predecessors.get("pr83")
    pr84 = predecessors.get("pr84")
    if (
        not isinstance(pr82, Mapping)
        or not isinstance(pr83, Mapping)
        or not isinstance(pr84, Mapping)
        or pr82.get("pr") != 82
        or pr82.get("branch")
        != "codex/product-v0232-healthy-traffic-evidence-nofault"
        or pr82.get("head") != PR82_HEAD_V02323
        or pr82.get("terminal")
        != "BLOCKED_ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT"
        or pr82.get("safe_error_code") != "RUN_ID_SCHEMA_PATTERN_MISMATCH"
        or pr82.get("business_traffic_completed") != 0
        or pr82.get("formal_traffic_executions") != 0
        or pr83.get("pr") != 83
        or pr83.get("branch")
        != "codex/product-v02321-traffic-harness-repair-nofault"
        or pr83.get("head") != PR83_HEAD_V02323
        or pr83.get("formal_terminal")
        != "BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE"
        or pr83.get("repository_terminal")
        != "BLOCKED_ECOMSRE_PRODUCT_V02321_REPOSITORY_ACCEPTANCE"
        or pr83.get("formal_traffic_completed") != 30
        or pr83.get("formal_traffic_retries") != 0
        or pr83.get("successor_incident_count") != 1
        or pr83.get("successor_diagnosis_count") != 0
        or pr83.get("diagnosis_job_status") != "FAILED"
        or pr83.get("diagnosis_safe_error_code") != "INTERNAL_CONTRACT_FAILURE"
        or any(
            pr83.get(field) != expected
            for field, expected in EXPECTED_PR83_EVIDENCE_SHA256_V02323.items()
        )
        or pr83.get("product_cleanup") != "CLEAN"
        or pr83.get("demo_cleanup") != "CLEAN"
        or pr84.get("pr") != 84
        or pr84.get("branch")
        != "codex/product-v02322-diagnosis-forensics-replay"
        or pr84.get("head") != PR84_HEAD_V02323
        or pr84.get("terminal")
        != "BLOCKED_ECOMSRE_PRODUCT_V02322_PRIVATE_PRODUCT_STATE"
        or pr84.get("completed_increment") != 2
        or pr84.get("completed_terminals")
        != EXPECTED_PR84_COMPLETED_TERMINALS_V02323
        or pr84.get("diagnosis_persistence_replay_attempts") != 0
        or manifest.get("counters") != EXPECTED_COUNTERS_V02323
        or manifest.get("authority") != EXPECTED_AUTHORITY_V02323
    ):
        raise ValueError("Product v0.2.3.2.3 predecessor history differs")

    digest = manifest.get("lost_schema8_raw_digest_binding")
    if (
        not isinstance(digest, Mapping)
        or digest.get("expected_digest_full")
        != EXPECTED_SCHEMA8_RAW_SHA256_V02323
        or digest.get("observed_contaminated_digest_full")
        != OBSERVED_SCHEMA9_RAW_SHA256_V02323
        or digest.get("expected_digest_kind") != "RAW_SQLITE_FILE_SHA256"
        or digest.get("expected_digest_source_field")
        != "source_database_file_sha256"
        or digest.get("source_artifact_role")
        != "PRE_MIGRATION_RAW_SQLITE_SHASUM_EVENT"
        or digest.get("source_artifact_locator")
        != (
            ".local/product-v02323/forensics/digest-source/"
            "pre-migration-shasum-event.jsonl"
        )
        or digest.get("source_artifact_sha256")
        != "d76226b3517366e4a01354af0b77fcca9f365b6ea7980510b7d31996afa459c1"
        or digest.get("source_definition_commit") != PR83_HEAD_V02323
        or digest.get("source_definition_path")
        != "src/ecomsre/product/pilot/product_state_clone_v0232.py"
        or digest.get("source_definition_file_sha256")
        != "ad71f30160252836a7caf963ae6d512e9fc8875302c364472f5039740507498c"
        or digest.get("raw_digest_function_source_sha256")
        != "94abe846ace1677b0ab1db03054b9e124d9d47d6ca6538b10525944953590e78"
        or digest.get("logical_digest_function_source_sha256")
        != "354b348e4bb5f4f65ec0f3c9d989af3bd65bb9b2546b5f71aa00b0fd768faf62"
        or digest.get("state_digest_function_source_sha256")
        != "0b51a3b3f08a6bb8e2e61b6492e00e0935977b41b4ac4739fcfe57a1df26182b"
    ):
        raise ValueError("Product v0.2.3.2.3 lost digest binding differs")

    for revision in (
        STARTING_MAIN_V02323,
        PR82_HEAD_V02323,
        PR83_HEAD_V02323,
        PR84_HEAD_V02323,
    ):
        _require_commit(project, revision)
    _require_ancestry(project, STARTING_MAIN_V02323, PR82_HEAD_V02323)
    _require_ancestry(project, PR82_HEAD_V02323, PR83_HEAD_V02323)
    _require_ancestry(project, PR83_HEAD_V02323, PR84_HEAD_V02323)
    _require_ancestry(project, PR84_HEAD_V02323, "HEAD")
    _require_pr84_tracked_bytes(project, manifest.get("pr84_tracked_files"))

    prior = verify_product_v02322_history(project)
    if (
        prior["pr82_terminal"] != pr82["terminal"]
        or prior["pr83_formal_terminal"] != pr83["formal_terminal"]
        or prior["pr83_repository_terminal"] != pr83["repository_terminal"]
        or prior["predecessor_head"] != PR83_HEAD_V02323
    ):
        raise ValueError("Product v0.2.3.2.3 predecessor verifier differs")

    return {
        "terminal": HISTORY_AND_BLOCKER_PASS_V02323,
        "starting_main": STARTING_MAIN_V02323,
        "predecessor_head": PR84_HEAD_V02323,
        "pr82_terminal": pr82["terminal"],
        "pr83_formal_terminal": pr83["formal_terminal"],
        "pr83_repository_terminal": pr83["repository_terminal"],
        "pr84_terminal": pr84["terminal"],
        "diagnosis_persistence_replay_attempts": 0,
        "fault_attempts": 0,
        "new_business_traffic_executions": 0,
        "new_product_incidents": 0,
        "provider_agent_runbook_docker_calls": 0,
        "action_authority": "NONE",
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    arguments = parser.parse_args(argv)
    print(json.dumps(verify_product_v02323_history(arguments.root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_product_v02323_history",)
