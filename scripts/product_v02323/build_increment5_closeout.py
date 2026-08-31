#!/usr/bin/env python3
"""Build deterministic Product v0.2.3.2.3 repository-closeout artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.repository_state_v02323 import sha256_file_v02323


GOAL_VERSION = "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
REPOSITORY_ACCEPTANCE_PASS = "ECOMSRE_PRODUCT_V02323_REPOSITORY_ACCEPTANCE_PASS"
ENGINEERING_COMPLETE = (
    "ECOMSRE_PRODUCT_V02323_SCHEMA8_RECONSTRUCTION_DIAGNOSIS_REPLAY_COMPLETE"
)
FRESH_FORMAL_HANDOFF_READY = "ECOMSRE_PRODUCT_V02323_FRESH_FORMAL_NOFAULT_HANDOFF_READY"
SUCCESSOR_PULL_REQUEST = 85
FROZEN_PREDECESSOR_PULL_REQUESTS = (82, 83, 84)
PullRequestStateProvider = Callable[[int], dict[str, Any]]
SUPERSEDED_COMMENT_MARKER = "ECOMSRE_PRODUCT_V02323_SUPERSEDED_BY_PR85_WITHOUT_MERGE"
_CHANGED_TESTS = (
    (
        "tests/product/test_increment1_historical_contract.py",
        "PATH_OR_SHA_REBINDING",
    ),
    (
        "tests/product_v023/test_increment3_live_baseline_runner.py",
        "PHASE_FIXTURE_MIGRATION",
    ),
    (
        "tests/product_v02321/test_increment3_state_clone_and_live_preflight.py",
        "PHASE_FIXTURE_MIGRATION",
    ),
    (
        "tests/product_v02321/test_increment4_formal_nofault.py",
        "PHASE_FIXTURE_MIGRATION",
    ),
    (
        "tests/product_v02323/test_increment1_forensic_snapshot_and_digest.py",
        "NEW_FORENSIC_COVERAGE",
    ),
    (
        "tests/product_v02323/test_increment2_schema8_reconstruction.py",
        "NEW_FORENSIC_COVERAGE",
    ),
    (
        "tests/product_v02323/test_increment3_diagnosis_forensics.py",
        "NEW_FORENSIC_COVERAGE",
    ),
    (
        "tests/product_v02323/test_increment4_persistence_replay.py",
        "NEW_FORENSIC_COVERAGE",
    ),
    (
        "tests/product_v02323/test_increment5_repository_acceptance.py",
        "NEW_FORENSIC_COVERAGE",
    ),
    (
        "tests/unit/test_command_runner.py",
        "GENUINE_IMPLEMENTATION_REPAIR",
    ),
)
_ALLOWED_ARTIFACTS = {
    "config/product-v02323/replay/recovery-job.json": "RECOVERY_JOB",
    "docs/analysis/product-v02321-formal-blocker.json": "PR83_FORMAL_BLOCKER",
    "docs/analysis/product-v02322-private-failure-contract.json": (
        "PR84_PRIVATE_FAILURE_CONTRACT"
    ),
    "docs/analysis/product-v02322-stage-journal-contract.json": (
        "PR84_STAGE_JOURNAL_CONTRACT"
    ),
    "docs/analysis/product-v02323-diagnosis-pipeline-replay.json": (
        "DIAGNOSIS_PIPELINE_REPLAY"
    ),
    "docs/analysis/product-v02323-diagnosis-replay.json": "DIAGNOSIS_REPLAY",
    "docs/analysis/product-v02323-diagnosis-root-cause.json": (
        "ROOT_CAUSE_DISPOSITION"
    ),
    "docs/analysis/product-v02323-digest-semantics.json": "DIGEST_SEMANTICS",
    "docs/analysis/product-v02323-forensic-source-snapshot.json": (
        "FORENSIC_SOURCE_SNAPSHOT"
    ),
    "docs/analysis/product-v02323-reconstruction-disposition.json": (
        "RECONSTRUCTION_DISPOSITION"
    ),
    "docs/analysis/product-v02323-schema8-reconstruction.json": (
        "SCHEMA8_RECONSTRUCTION"
    ),
    "docs/analysis/product-v02323-schema9-contamination-audit.json": (
        "SCHEMA9_CONTAMINATION_AUDIT"
    ),
    "docs/analysis/product-v02323-targeted-repair.json": "TARGETED_REPAIR",
    "docs/analysis/product-v02323-test-migration.json": "TEST_MIGRATION",
    "docs/external-reviews/product-v02323-final-review.md": "FINAL_REVIEW",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sealed(payload: dict[str, Any], field: str) -> dict[str, Any]:
    body = dict(payload)
    body.pop(field, None)
    return {**body, field: semantic_sha256_v22(body)}


def _require_seal(payload: dict[str, Any], field: str) -> None:
    body = dict(payload)
    supplied = body.pop(field, None)
    if not isinstance(supplied, str) or supplied != semantic_sha256_v22(body):
        raise ValueError(f"Product v0.2.3.2.3 {field} differs")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_markdown(path: Path, lines: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_github_json(endpoint: str) -> Any:
    try:
        process = subprocess.run(
            ("gh", "api", endpoint),
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(
            "Product v0.2.3.2.3 GitHub pull request read failed"
        ) from error
    if process.returncode != 0:
        raise ValueError("Product v0.2.3.2.3 GitHub pull request read failed")
    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(
            "Product v0.2.3.2.3 GitHub pull request response differs"
        ) from error


def load_github_pull_request_state(number: int) -> dict[str, Any]:
    """Read one fixed-repository pull request through the GitHub CLI."""

    if number not in (*FROZEN_PREDECESSOR_PULL_REQUESTS, SUCCESSOR_PULL_REQUEST):
        raise ValueError("Product v0.2.3.2.3 pull request number differs")
    value = _load_github_json(f"repos/Raidriar7170/EcomSRE-Agent/pulls/{number}")
    if not isinstance(value, dict):
        raise ValueError("Product v0.2.3.2.3 GitHub pull request response differs")
    if number in FROZEN_PREDECESSOR_PULL_REQUESTS:
        comments = _load_github_json(
            f"repos/Raidriar7170/EcomSRE-Agent/issues/{number}/comments?per_page=100"
        )
        if not isinstance(comments, list):
            raise ValueError("Product v0.2.3.2.3 GitHub comments response differs")
        value["_superseded_disposition_comment_observed"] = any(
            isinstance(comment, dict)
            and isinstance(comment.get("body"), str)
            and SUPERSEDED_COMMENT_MARKER in comment["body"]
            for comment in comments
        )
    return value


def validate_live_pull_request_closeout(
    merged_successor_commit: str,
    *,
    pull_request_state_provider: PullRequestStateProvider,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Fail closed unless the successor is merged and all predecessors are unmerged."""

    successor = pull_request_state_provider(SUCCESSOR_PULL_REQUEST)
    successor_head = successor.get("head")
    successor_base = successor.get("base")
    if (
        successor.get("number") != SUCCESSOR_PULL_REQUEST
        or successor.get("state") != "closed"
        or successor.get("merged") is not True
        or not isinstance(successor.get("merged_at"), str)
        or successor.get("merge_commit_sha") != merged_successor_commit
        or not isinstance(successor_head, dict)
        or successor_head.get("ref")
        != "codex/product-v02323-schema8-reconstruction-replay"
        or not isinstance(successor_head.get("sha"), str)
        or not isinstance(successor_base, dict)
        or successor_base.get("ref") != "main"
    ):
        raise ValueError("Product v0.2.3.2.3 successor merge state differs")
    successor_evidence: dict[str, object] = {
        "pull_request": SUCCESSOR_PULL_REQUEST,
        "state": "CLOSED",
        "merged": True,
        "merged_at": successor["merged_at"],
        "merge_commit_sha": merged_successor_commit,
        "head_ref": successor_head["ref"],
        "head_sha": successor_head["sha"],
        "base_ref": successor_base["ref"],
    }

    predecessor_evidence: list[dict[str, object]] = []
    for number in FROZEN_PREDECESSOR_PULL_REQUESTS:
        predecessor = pull_request_state_provider(number)
        if (
            predecessor.get("number") != number
            or predecessor.get("state") != "closed"
            or predecessor.get("merged") is not False
            or predecessor.get("merged_at") is not None
            or predecessor.get("_superseded_disposition_comment_observed") is not True
        ):
            raise ValueError("Product v0.2.3.2.3 predecessor closeout differs")
        predecessor_evidence.append(
            {
                "pull_request": number,
                "state": "CLOSED",
                "merged": False,
                "merged_at": None,
                "disposition": "SUPERSEDED_WITHOUT_MERGE",
                "superseded_comment_marker": SUPERSEDED_COMMENT_MARKER,
            }
        )
    return successor_evidence, predecessor_evidence


def build_increment5_closeout(root: Path) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    replay = _load(
        project / "docs/analysis/product-v02323-diagnosis-pipeline-replay.json"
    )
    root_cause = _load(
        project / "docs/analysis/product-v02323-diagnosis-root-cause.json"
    )
    reconstruction = _load(
        project / "docs/analysis/product-v02323-reconstruction-disposition.json"
    )
    snapshot = _load(
        project / "docs/analysis/product-v02323-forensic-source-snapshot.json"
    )
    private_contract = _load(
        project / "docs/analysis/product-v02322-private-failure-contract.json"
    )
    journal_contract = _load(
        project / "docs/analysis/product-v02322-stage-journal-contract.json"
    )
    blocker = _load(project / "docs/analysis/product-v02321-formal-blocker.json")

    if (
        replay.get("terminal")
        != "ECOMSRE_PRODUCT_V02323_DIAGNOSIS_PIPELINE_REPLAY_PASS"
        or replay.get("diagnosis_persistence_replay_attempt_count") != 1
        or replay.get("provider_agent_runbook_docker_calls") != 0
        or replay.get("measured_nofault_authority") != "NONE"
        or replay.get("knowledge_loop_authority") != "NONE"
        or replay.get("targeted_repair_sha256") is not None
        or root_cause.get("deterministic_structural_defect_identified") is not False
        or root_cause.get("targeted_repair") != "NOT_APPLICABLE"
    ):
        raise ValueError("Product v0.2.3.2.3 replay closeout input differs")

    targeted_repair = _sealed(
        {
            "schema_version": "ecomsre.product.targeted-repair.v02323",
            "goal_version": GOAL_VERSION,
            "root_cause_disposition": root_cause["disposition"],
            "root_cause_disposition_sha256": root_cause["disposition_sha256"],
            "deterministic_structural_defect_identified": False,
            "targeted_repair": "NOT_APPLICABLE",
            "targeted_repair_sha256": None,
            "diagnosis_pipeline_replay_result_sha256": replay["result_sha256"],
            "measured_nofault_authority": "NONE",
            "knowledge_loop_authority": "NONE",
            "terminal": "ECOMSRE_PRODUCT_V02323_NO_TARGETED_REPAIR_REQUIRED",
        },
        "report_sha256",
    )
    _write_json(
        project / "docs/analysis/product-v02323-targeted-repair.json",
        targeted_repair,
    )
    (project / "docs/analysis/product-v02323-diagnosis-replay.json").write_bytes(
        (
            project / "docs/analysis/product-v02323-diagnosis-pipeline-replay.json"
        ).read_bytes()
    )

    recovery_job = _sealed(
        {
            "schema_version": "ecomsre.product.diagnosis-recovery-job.v02323",
            "goal_version": GOAL_VERSION,
            "replay_id": replay["replay_id"],
            "replay_of_job_id": replay["original_failed_job_id"],
            "recovery_job_id": replay["recovery_job_id"],
            "recovery_job_status": replay["recovery_job_status"],
            "recovery_job_sha256": replay["recovery_job_sha256"],
            "diagnosis_result_sha256": replay["diagnosis_result_sha256"],
            "evidence_bundle_sha256": replay["evidence_bundle_sha256"],
            "evidence_index_sha256": replay["evidence_index_sha256"],
            "decision_trace_sha256": replay["decision_trace_sha256"],
            "decision_trace_object_sha256": replay["decision_trace_object_sha256"],
            "stage_event_count": replay["stage_event_count"],
            "stage_journal_terminal": replay["stage_journal_terminal"],
            "original_failed_job_unchanged": replay["original_failed_job_unchanged"],
            "measured_nofault_authority": "NONE",
            "knowledge_loop_authority": "NONE",
            "terminal": "ECOMSRE_PRODUCT_V02323_RECOVERY_JOB_FROZEN",
        },
        "record_sha256",
    )
    _write_json(
        project / "config/product-v02323/replay/recovery-job.json", recovery_job
    )

    test_migration = _sealed(
        {
            "schema_version": "ecomsre.product.test-migration.v02323",
            "goal_version": GOAL_VERSION,
            "changed_tests": [
                {"category": category, "path": path}
                for path, category in _CHANGED_TESTS
            ],
            "retained_test_roots": [
                "tests/product_v0231",
                "tests/product_v02321",
                "tests/product_v02322",
                "tests/product_v02323",
            ],
            "deleted_test_count": 0,
            "skipped_or_xfailed_v02321_semantic_test_count": 0,
            "categories": [
                "PHASE_FIXTURE_MIGRATION",
                "PATH_OR_SHA_REBINDING",
                "GENUINE_IMPLEMENTATION_REPAIR",
                "NEW_FORENSIC_COVERAGE",
            ],
            "terminal": "ECOMSRE_PRODUCT_V02323_TEST_MIGRATION_PASS",
        },
        "migration_sha256",
    )
    _write_json(
        project / "docs/analysis/product-v02323-test-migration.json",
        test_migration,
    )

    allowed_artifacts = []
    for relative, role in sorted(_ALLOWED_ARTIFACTS.items()):
        file_sha256, size_bytes = sha256_file_v02323(project / relative)
        allowed_artifacts.append(
            {
                "role": role,
                "path": relative,
                "file_sha256": file_sha256,
                "size_bytes": size_bytes,
            }
        )
    manifest = _sealed(
        {
            "schema_version": "ecomsre.product.repository-state.v02323",
            "goal_version": GOAL_VERSION,
            "phase": "DIAGNOSIS_REPLAY_COMPLETE",
            "pr83_formal_blocker_sha256": blocker["blocker_sha256"],
            "pr84_private_state_contract_sha256": private_contract["contract_sha256"],
            "forensic_source_snapshot_sha256": snapshot["snapshot_sha256"],
            "reconstruction_disposition_sha256": reconstruction["disposition_sha256"],
            "replay_result_sha256": replay["result_sha256"],
            "measured_nofault_result_sha256": None,
            "allowed_artifacts": allowed_artifacts,
            "forbidden_artifacts": [
                "docs/analysis/product-v02323-knowledge-loop-handoff.json",
                "docs/results/product-v02323-measured-nofault.json",
                "docs/results/product-v02323-nofault-acceptance.json",
            ],
        },
        "manifest_sha256",
    )
    _write_json(
        project / "config/product-v02323/repository-state-manifest.json", manifest
    )

    progress = _load(project / "docs/analysis/product-v02323-progress.json")
    progress.update(
        {
            "increment": 5,
            "phase": "DIAGNOSIS_REPLAY_COMPLETE",
            "repository_state_manifest_sha256": manifest["manifest_sha256"],
            "test_migration_sha256": test_migration["migration_sha256"],
            "repository_acceptance_terminal": REPOSITORY_ACCEPTANCE_PASS,
            "required_engineering_terminal": ENGINEERING_COMPLETE,
            "engineering_terminal": "PENDING_MERGE_AND_PREDECESSOR_CLOSEOUT",
            "next_gate": "FINAL_REVIEW_EXACT_HEAD_CI_AND_MERGE",
        }
    )
    terminals = list(progress["terminals"])
    if REPOSITORY_ACCEPTANCE_PASS not in terminals:
        terminals.append(REPOSITORY_ACCEPTANCE_PASS)
    progress["terminals"] = terminals
    progress = _sealed(progress, "progress_sha256")
    _write_json(project / "docs/analysis/product-v02323-progress.json", progress)

    closeout = _sealed(
        {
            "schema_version": "ecomsre.product.engineering-closeout.v02323",
            "goal_version": GOAL_VERSION,
            "phase": "DIAGNOSIS_REPLAY_COMPLETE",
            "repository_acceptance_terminal": REPOSITORY_ACCEPTANCE_PASS,
            "engineering_terminal": "PENDING_MERGE_AND_PREDECESSOR_CLOSEOUT",
            "required_engineering_terminal": ENGINEERING_COMPLETE,
            "reconstruction_disposition": reconstruction["disposition"],
            "reconstruction_disposition_sha256": reconstruction["disposition_sha256"],
            "historical_raw_byte_authority": reconstruction[
                "historical_raw_byte_authority"
            ],
            "replay_classification": replay["replay_classification"],
            "replay_result_sha256": replay["result_sha256"],
            "root_cause_disposition": root_cause["disposition"],
            "targeted_repair_sha256": None,
            "diagnosis_persistence_replay_attempt_count": 1,
            "original_failed_job_unchanged": True,
            "provider_agent_runbook_docker_calls": 0,
            "new_business_traffic_executions": 0,
            "new_product_incidents": 0,
            "new_baseline_attempts": 0,
            "fault_attempts": 0,
            "measured_nofault_authority": "NONE",
            "knowledge_loop_authority": "NONE",
            "repository_state_manifest_sha256": manifest["manifest_sha256"],
            "test_migration_sha256": test_migration["migration_sha256"],
            "stage_journal_contract_sha256": journal_contract["contract_sha256"],
            "private_failure_contract_sha256": private_contract["contract_sha256"],
            "pr83_formal_blocker_sha256": blocker["blocker_sha256"],
            "limitations": [
                "Original schema-8 SQLite bytes remain unavailable.",
                "The replay is structural and cannot reproduce the lost acquisition.",
                "The original root cause remains unproven.",
                "No measured No-Fault or Knowledge-Loop authority is granted.",
                "Fresh formal v0.2.3.3 execution remains a separate campaign.",
            ],
        },
        "closeout_sha256",
    )
    _write_json(
        project / "docs/results/product-v02323-engineering-closeout.json",
        closeout,
    )
    _write_markdown(
        project / "docs/results/product-v02323-engineering-closeout.md",
        (
            "# Product v0.2.3.2.3 Engineering Closeout",
            "",
            f"Repository acceptance: `{REPOSITORY_ACCEPTANCE_PASS}`.",
            "",
            "Current repository phase: `DIAGNOSIS_REPLAY_COMPLETE`.",
            "The final engineering terminal remains pending until the successor is",
            "squash merged and PR #82, PR #83, and PR #84 are closed as superseded.",
            "",
            "The one authorized structural Diagnosis persistence replay succeeded.",
            "It did not run traffic, create a Product Incident or Baseline, call a",
            "Provider, Agent, Runbook, or Docker, or mint measured No-Fault authority.",
        ),
    )
    _write_markdown(
        project / "docs/results/product-v02323-limitations.md",
        (
            "# Product v0.2.3.2.3 Limitations",
            "",
            "- The original schema-8 SQLite bytes and exact acquisition are lost.",
            "- The reconstruction proves logical state, not raw-byte identity.",
            "- The replay classification is `STRUCTURAL_CONTRACT_REPLAY`.",
            "- The historical `INTERNAL_CONTRACT_FAILURE` root cause is unproven.",
            "- The successful replay is not a live or measured No-Fault result.",
            "- Measured No-Fault and Knowledge-Loop authority remain `NONE`.",
        ),
    )
    _write_markdown(
        project / "docs/results/product-v02323-interview-brief.md",
        (
            "# Product v0.2.3.2.3 Interview Brief",
            "",
            "Recovered the strongest provable schema-8 logical state from a pristine",
            "base plus the frozen formal delta, while preserving the contaminated",
            "schema-9 source and all predecessor blockers.",
            "",
            "A single fresh-clone structural replay persisted one recovery Diagnosis,",
            "Evidence Bundle, Evidence Index, Decision Trace, and a 54-event Stage",
            "Journal ending at `JOB_SUCCEEDED`. The original failed job stayed failed.",
            "",
            "Claim boundary: this closes the engineering persistence path only. It is",
            "not exact historical reproduction, live or measured No-Fault evidence,",
            "or permission",
            "to promote a Knowledge Loop.",
        ),
    )

    return {
        "terminal": "ECOMSRE_PRODUCT_V02323_INCREMENT5_ARTIFACTS_BUILT",
        "repository_state_manifest_sha256": manifest["manifest_sha256"],
        "test_migration_sha256": test_migration["migration_sha256"],
        "closeout_sha256": closeout["closeout_sha256"],
        "replay_result_sha256": replay["result_sha256"],
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
    }


def finalize_post_merge_closeout(
    root: Path,
    *,
    merged_successor_commit: str,
    pull_request_state_provider: PullRequestStateProvider = (
        load_github_pull_request_state
    ),
) -> dict[str, object]:
    """Create the post-merge handoff and mint the final engineering terminal."""

    if re.fullmatch(r"[0-9a-f]{40}", merged_successor_commit) is None:
        raise ValueError("Product v0.2.3.2.3 merged successor commit differs")
    successor_merge, predecessor_closeout = validate_live_pull_request_closeout(
        merged_successor_commit,
        pull_request_state_provider=pull_request_state_provider,
    )

    project = Path(root).resolve(strict=True)
    closeout = _load(project / "docs/results/product-v02323-engineering-closeout.json")
    progress = _load(project / "docs/analysis/product-v02323-progress.json")
    replay = _load(
        project / "docs/analysis/product-v02323-diagnosis-pipeline-replay.json"
    )
    snapshot = _load(
        project / "docs/analysis/product-v02323-forensic-source-snapshot.json"
    )
    digest = _load(project / "docs/analysis/product-v02323-digest-semantics.json")
    contamination = _load(
        project / "docs/analysis/product-v02323-schema9-contamination-audit.json"
    )
    reconstruction = _load(
        project / "docs/analysis/product-v02323-reconstruction-disposition.json"
    )
    root_cause = _load(
        project / "docs/analysis/product-v02323-diagnosis-root-cause.json"
    )
    repair = _load(project / "docs/analysis/product-v02323-targeted-repair.json")
    journal_contract = _load(
        project / "docs/analysis/product-v02322-stage-journal-contract.json"
    )
    private_contract = _load(
        project / "docs/analysis/product-v02322-private-failure-contract.json"
    )
    blocker = _load(project / "docs/analysis/product-v02321-formal-blocker.json")
    manifest = _load(project / "config/product-v02323/repository-state-manifest.json")

    for payload, field in (
        (closeout, "closeout_sha256"),
        (progress, "progress_sha256"),
        (replay, "result_sha256"),
        (snapshot, "snapshot_sha256"),
        (digest, "audit_sha256"),
        (contamination, "audit_sha256"),
        (reconstruction, "disposition_sha256"),
        (root_cause, "disposition_sha256"),
        (repair, "report_sha256"),
        (journal_contract, "contract_sha256"),
        (private_contract, "contract_sha256"),
        (blocker, "blocker_sha256"),
        (manifest, "manifest_sha256"),
    ):
        _require_seal(payload, field)

    if (
        closeout.get("engineering_terminal") != "PENDING_MERGE_AND_PREDECESSOR_CLOSEOUT"
        or closeout.get("required_engineering_terminal") != ENGINEERING_COMPLETE
        or progress.get("engineering_terminal")
        != "PENDING_MERGE_AND_PREDECESSOR_CLOSEOUT"
        or replay.get("diagnosis_persistence_replay_attempt_count") != 1
        or replay.get("provider_agent_runbook_docker_calls") != 0
        or replay.get("measured_nofault_authority") != "NONE"
        or replay.get("knowledge_loop_authority") != "NONE"
    ):
        raise ValueError("Product v0.2.3.2.3 pre-merge closeout differs")

    handoff = _sealed(
        {
            "schema_version": "ecomsre.product.fresh-formal-handoff.v02323",
            "goal_version": GOAL_VERSION,
            "terminal": FRESH_FORMAL_HANDOFF_READY,
            "merged_successor_commit": merged_successor_commit,
            "successor_pull_request": SUCCESSOR_PULL_REQUEST,
            "successor_merge": successor_merge,
            "predecessor_closeout": predecessor_closeout,
            "forensic_source_snapshot_sha256": snapshot["snapshot_sha256"],
            "digest_semantics_audit_sha256": digest["audit_sha256"],
            "schema9_contamination_audit_sha256": contamination["audit_sha256"],
            "reconstruction_disposition": reconstruction["disposition"],
            "reconstruction_disposition_sha256": reconstruction["disposition_sha256"],
            "sealed_schema8_reconstruction_sha256": reconstruction[
                "reconstruction_sha256"
            ],
            "replay_classification": replay["replay_classification"],
            "root_cause_disposition": root_cause["disposition"],
            "root_cause_disposition_sha256": root_cause["disposition_sha256"],
            "targeted_repair_sha256": repair["targeted_repair_sha256"],
            "diagnosis_replay_result_sha256": replay["result_sha256"],
            "stage_journal_contract_sha256": journal_contract["contract_sha256"],
            "private_failure_contract_sha256": private_contract["contract_sha256"],
            "repository_state_manifest_sha256": manifest["manifest_sha256"],
            "pr83_formal_blocker_sha256": blocker["blocker_sha256"],
            "next_milestone": {
                "version": "Product v0.2.3.3",
                "name": "Fresh Formal Evidence-Bound No-Fault Acceptance",
                "requirements": [
                    "NEW_PRODUCT_STATE_CLONE",
                    "NEW_HEALTHY_FORMAL_WORKLOAD",
                    "NEW_PRODUCT_INCIDENT",
                    "NEW_PRODUCT_DIAGNOSIS",
                    "ONE_MEASURED_TERMINAL",
                ],
            },
            "replay_is_live_nofault_result": False,
            "measured_nofault_authority": "NONE",
            "knowledge_loop_authority": "NONE",
        },
        "handoff_sha256",
    )
    _write_json(
        project / "docs/analysis/product-v02323-fresh-formal-handoff.json",
        handoff,
    )
    _write_markdown(
        project / "docs/analysis/product-v02323-fresh-formal-handoff.md",
        (
            "# Product v0.2.3.2.3 Fresh Formal Handoff",
            "",
            f"Terminal: `{FRESH_FORMAL_HANDOFF_READY}`.",
            f"Merged successor commit: `{merged_successor_commit}`.",
            "",
            "The recommended next milestone is Product v0.2.3.3, Fresh Formal",
            "Evidence-Bound No-Fault Acceptance. It requires a new Product-state",
            "clone, healthy formal workload, Product Incident, Product Diagnosis,",
            "and exactly one measured terminal.",
            "",
            "This structural replay is not a live or measured No-Fault result.",
            "Measured No-Fault and Knowledge-Loop authority remain `NONE`.",
        ),
    )

    closeout.update(
        {
            "engineering_terminal": ENGINEERING_COMPLETE,
            "merged_successor_commit": merged_successor_commit,
            "successor_pull_request": SUCCESSOR_PULL_REQUEST,
            "successor_merge": successor_merge,
            "predecessor_closeout": predecessor_closeout,
            "fresh_formal_handoff_terminal": FRESH_FORMAL_HANDOFF_READY,
            "fresh_formal_handoff_sha256": handoff["handoff_sha256"],
        }
    )
    closeout = _sealed(closeout, "closeout_sha256")
    _write_json(
        project / "docs/results/product-v02323-engineering-closeout.json",
        closeout,
    )
    _write_markdown(
        project / "docs/results/product-v02323-engineering-closeout.md",
        (
            "# Product v0.2.3.2.3 Engineering Closeout",
            "",
            f"Repository acceptance: `{REPOSITORY_ACCEPTANCE_PASS}`.",
            f"Engineering terminal: `{ENGINEERING_COMPLETE}`.",
            f"Merged successor commit: `{merged_successor_commit}`.",
            "",
            "PR #82, PR #83, and PR #84 are closed as superseded without merge.",
            "The one authorized structural Diagnosis persistence replay succeeded.",
            "It did not run traffic, create a Product Incident or Baseline, call a",
            "Provider, Agent, Runbook, or Docker, or mint measured No-Fault authority.",
        ),
    )

    progress.update(
        {
            "engineering_terminal": ENGINEERING_COMPLETE,
            "merged_successor_commit": merged_successor_commit,
            "successor_pull_request": SUCCESSOR_PULL_REQUEST,
            "successor_merge": successor_merge,
            "predecessor_closeout": predecessor_closeout,
            "fresh_formal_handoff_terminal": FRESH_FORMAL_HANDOFF_READY,
            "fresh_formal_handoff_sha256": handoff["handoff_sha256"],
            "next_gate": "PRODUCT_V0233_FRESH_FORMAL_EVIDENCE_BOUND_NOFAULT_ACCEPTANCE",
        }
    )
    terminals = list(progress["terminals"])
    for terminal in (FRESH_FORMAL_HANDOFF_READY, ENGINEERING_COMPLETE):
        if terminal not in terminals:
            terminals.append(terminal)
    progress["terminals"] = terminals
    progress = _sealed(progress, "progress_sha256")
    _write_json(project / "docs/analysis/product-v02323-progress.json", progress)

    return {
        "terminal": ENGINEERING_COMPLETE,
        "fresh_formal_handoff_terminal": FRESH_FORMAL_HANDOFF_READY,
        "merged_successor_commit": merged_successor_commit,
        "predecessor_pull_requests_closed_without_merge": list(
            FROZEN_PREDECESSOR_PULL_REQUESTS
        ),
        "handoff_sha256": handoff["handoff_sha256"],
        "closeout_sha256": closeout["closeout_sha256"],
        "progress_sha256": progress["progress_sha256"],
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--finalize-after-merge")
    arguments = parser.parse_args(argv)
    if arguments.finalize_after_merge is None:
        result = build_increment5_closeout(arguments.root)
    else:
        result = finalize_post_merge_closeout(
            arguments.root,
            merged_successor_commit=arguments.finalize_after_merge,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "build_increment5_closeout",
    "finalize_post_merge_closeout",
    "load_github_pull_request_state",
    "validate_live_pull_request_closeout",
)
