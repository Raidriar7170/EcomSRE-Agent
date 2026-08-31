#!/usr/bin/env python3
"""Verify public Product v0.2.3.2.3 repository acceptance and closeout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.repository_state_v02323 import (
    RepositoryPhaseV02323,
    verify_repository_state_v02323,
)
from scripts.ci.verify_product_v02323_history import (
    PR84_HEAD_V02323,
    verify_product_v02323_history,
)
from scripts.product_v02323.build_increment5_closeout import (
    PullRequestStateProvider,
    load_github_pull_request_state,
    validate_live_pull_request_closeout,
)


REPOSITORY_ACCEPTANCE_PASS_V02323 = "ECOMSRE_PRODUCT_V02323_REPOSITORY_ACCEPTANCE_PASS"
ENGINEERING_COMPLETE_V02323 = (
    "ECOMSRE_PRODUCT_V02323_SCHEMA8_RECONSTRUCTION_DIAGNOSIS_REPLAY_COMPLETE"
)
FRESH_FORMAL_HANDOFF_READY_V02323 = (
    "ECOMSRE_PRODUCT_V02323_FRESH_FORMAL_NOFAULT_HANDOFF_READY"
)
INCREMENT4_HEAD_V02323 = "fff08372e851b51472e48d04f2d1882e35ce584d"
_PREMERGE_TERMINALS_V02323 = [
    "ECOMSRE_PRODUCT_V02323_HISTORY_AND_BLOCKER_PASS",
    "ECOMSRE_PRODUCT_V02323_FORENSIC_SOURCE_SNAPSHOT_PASS",
    "ECOMSRE_PRODUCT_V02323_DIGEST_SEMANTICS_PASS",
    "ECOMSRE_PRODUCT_V02323_SCHEMA9_CONTAMINATION_AUDIT_PASS",
    "ECOMSRE_PRODUCT_V02323_RECONSTRUCTION_DISPOSITION_FROZEN",
    "ECOMSRE_PRODUCT_V02323_REPLAY_INPUT_PASS",
    "ECOMSRE_PRODUCT_V02323_ROOT_CAUSE_DISPOSITION_FROZEN",
    "ECOMSRE_PRODUCT_V02323_DIAGNOSIS_PIPELINE_REPLAY_PASS",
    REPOSITORY_ACCEPTANCE_PASS_V02323,
]
_CHANGED_TESTS_V02323 = {
    "tests/product/test_increment1_historical_contract.py": (
        "M",
        "PATH_OR_SHA_REBINDING",
    ),
    "tests/product_v023/test_increment3_live_baseline_runner.py": (
        "M",
        "PHASE_FIXTURE_MIGRATION",
    ),
    "tests/product_v02321/test_increment3_state_clone_and_live_preflight.py": (
        "M",
        "PHASE_FIXTURE_MIGRATION",
    ),
    "tests/product_v02321/test_increment4_formal_nofault.py": (
        "M",
        "PHASE_FIXTURE_MIGRATION",
    ),
    "tests/product_v02323/test_increment1_forensic_snapshot_and_digest.py": (
        "A",
        "NEW_FORENSIC_COVERAGE",
    ),
    "tests/product_v02323/test_increment2_schema8_reconstruction.py": (
        "A",
        "NEW_FORENSIC_COVERAGE",
    ),
    "tests/product_v02323/test_increment3_diagnosis_forensics.py": (
        "A",
        "NEW_FORENSIC_COVERAGE",
    ),
    "tests/product_v02323/test_increment4_persistence_replay.py": (
        "A",
        "NEW_FORENSIC_COVERAGE",
    ),
    "tests/product_v02323/test_increment5_repository_acceptance.py": (
        "A",
        "NEW_FORENSIC_COVERAGE",
    ),
    "tests/unit/test_command_runner.py": (
        "M",
        "GENUINE_IMPLEMENTATION_REPAIR",
    ),
}
_SKIP_MARKERS = (
    "pytest.mark.skip",
    "pytest.mark.xfail",
    "pytest.skip(",
    "pytest.xfail(",
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _require_seal(payload: dict[str, Any], field: str) -> str:
    body = dict(payload)
    supplied = body.pop(field, None)
    if not isinstance(supplied, str) or supplied != semantic_sha256_v22(body):
        raise ValueError(f"Product v0.2.3.2.3 {field} differs")
    return supplied


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _verify_test_delta(root: Path, migration: dict[str, Any]) -> None:
    changed: dict[str, str] = {}
    output = _git(root, "diff", "--name-status", PR84_HEAD_V02323, "--", "tests")
    for line in output.splitlines():
        if not line:
            continue
        status, relative = line.split("\t", 1)
        changed[relative] = status
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "--", "tests")
    for relative in untracked.splitlines():
        if relative:
            changed[relative] = "A"
    expected = {
        path: status for path, (status, _category) in _CHANGED_TESTS_V02323.items()
    }
    if changed != expected:
        raise ValueError("Product v0.2.3.2.3 changed test set differs")

    expected_classification = [
        {"category": category, "path": path}
        for path, (_status, category) in _CHANGED_TESTS_V02323.items()
    ]
    if (
        migration.get("changed_tests") != expected_classification
        or migration.get("deleted_test_count") != 0
        or migration.get("skipped_or_xfailed_v02321_semantic_test_count") != 0
        or migration.get("terminal") != "ECOMSRE_PRODUCT_V02323_TEST_MIGRATION_PASS"
    ):
        raise ValueError("Product v0.2.3.2.3 test migration differs")
    for test_path in sorted((root / "tests/product_v02321").glob("test_*.py")):
        text = test_path.read_text(encoding="utf-8")
        if any(marker in text for marker in _SKIP_MARKERS):
            raise ValueError("Product v0.2.3.2.1 semantic test suppression differs")


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    process = subprocess.run(
        ("git", "merge-base", "--is-ancestor", ancestor, descendant),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode not in (0, 1):
        raise ValueError("Product v0.2.3.2.3 merged successor ancestry differs")
    return process.returncode == 0


def _load_git_json(root: Path, revision: str, relative_path: str) -> dict[str, Any]:
    value = json.loads(_git(root, "show", f"{revision}:{relative_path}"))
    if not isinstance(value, dict):
        raise ValueError("Product v0.2.3.2.3 committed JSON object differs")
    return value


def verify_progress_transition_v02323(
    root: Path,
    progress: dict[str, Any],
    *,
    repository_state_manifest_sha256: str,
    test_migration_sha256: str,
    post_merge: bool,
    handoff: dict[str, Any] | None = None,
    successor_merge: dict[str, object] | None = None,
    predecessor_closeout: list[dict[str, object]] | None = None,
) -> None:
    increment4_progress = _load_git_json(
        root,
        INCREMENT4_HEAD_V02323,
        "docs/analysis/product-v02323-progress.json",
    )
    _require_seal(increment4_progress, "progress_sha256")
    expected = dict(increment4_progress)
    expected.update(
        {
            "increment": 5,
            "phase": "DIAGNOSIS_REPLAY_COMPLETE",
            "repository_state_manifest_sha256": repository_state_manifest_sha256,
            "test_migration_sha256": test_migration_sha256,
            "repository_acceptance_terminal": REPOSITORY_ACCEPTANCE_PASS_V02323,
            "required_engineering_terminal": ENGINEERING_COMPLETE_V02323,
            "engineering_terminal": "PENDING_MERGE_AND_PREDECESSOR_CLOSEOUT",
            "next_gate": "FINAL_REVIEW_EXACT_HEAD_CI_AND_MERGE",
            "terminals": list(_PREMERGE_TERMINALS_V02323),
        }
    )
    if post_merge:
        if handoff is None or successor_merge is None or predecessor_closeout is None:
            raise ValueError("Product v0.2.3.2.3 post-merge progress input differs")
        expected.update(
            {
                "engineering_terminal": ENGINEERING_COMPLETE_V02323,
                "merged_successor_commit": handoff["merged_successor_commit"],
                "successor_pull_request": 85,
                "successor_merge": successor_merge,
                "predecessor_closeout": predecessor_closeout,
                "fresh_formal_handoff_terminal": (FRESH_FORMAL_HANDOFF_READY_V02323),
                "fresh_formal_handoff_sha256": handoff["handoff_sha256"],
                "next_gate": (
                    "PRODUCT_V0233_FRESH_FORMAL_EVIDENCE_BOUND_NOFAULT_ACCEPTANCE"
                ),
                "terminals": [
                    *_PREMERGE_TERMINALS_V02323,
                    FRESH_FORMAL_HANDOFF_READY_V02323,
                    ENGINEERING_COMPLETE_V02323,
                ],
            }
        )
    expected = {
        **{key: value for key, value in expected.items() if key != "progress_sha256"},
    }
    expected["progress_sha256"] = semantic_sha256_v22(expected)
    if progress != expected:
        raise ValueError("Product v0.2.3.2.3 exact progress transition differs")


def verify_product_v02323_increment5(
    root: Path,
    *,
    expected_head: str | None = None,
    require_clean_head: bool = False,
    pull_request_state_provider: PullRequestStateProvider = (
        load_github_pull_request_state
    ),
) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    head = _git(project, "rev-parse", "HEAD")
    if expected_head is not None and head != expected_head:
        raise ValueError("Product v0.2.3.2.3 exact CI head differs")
    if require_clean_head and _git(project, "status", "--porcelain"):
        raise ValueError("Product v0.2.3.2.3 exact CI head is dirty")

    history = verify_product_v02323_history(project)
    state = verify_repository_state_v02323(project)
    if (
        history.get("pr83_formal_terminal")
        != "BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE"
        or history.get("pr83_repository_terminal")
        != "BLOCKED_ECOMSRE_PRODUCT_V02321_REPOSITORY_ACCEPTANCE"
        or history.get("pr84_terminal")
        != "BLOCKED_ECOMSRE_PRODUCT_V02322_PRIVATE_PRODUCT_STATE"
        or state.get("phase") != RepositoryPhaseV02323.DIAGNOSIS_REPLAY_COMPLETE.value
        or state.get("measured_nofault_authority") != "NONE"
        or state.get("knowledge_loop_authority") != "NONE"
    ):
        raise ValueError("Product v0.2.3.2.3 repository state differs")

    replay_path = (
        project / "docs/analysis/product-v02323-diagnosis-pipeline-replay.json"
    )
    replay_alias_path = project / "docs/analysis/product-v02323-diagnosis-replay.json"
    replay = _load(replay_path)
    replay_alias = _load(replay_alias_path)
    attempts = _load(
        project / "docs/analysis/product-v02323-diagnosis-persistence-attempts.json"
    )
    recovery = _load(project / "config/product-v02323/replay/recovery-job.json")
    repair = _load(project / "docs/analysis/product-v02323-targeted-repair.json")
    migration = _load(project / "docs/analysis/product-v02323-test-migration.json")
    closeout = _load(project / "docs/results/product-v02323-engineering-closeout.json")
    progress = _load(project / "docs/analysis/product-v02323-progress.json")
    manifest = _load(project / "config/product-v02323/repository-state-manifest.json")
    handoff_path = project / "docs/analysis/product-v02323-fresh-formal-handoff.json"
    handoff_markdown_path = (
        project / "docs/analysis/product-v02323-fresh-formal-handoff.md"
    )
    if handoff_path.exists() != handoff_markdown_path.exists():
        raise ValueError("Product v0.2.3.2.3 fresh formal handoff pair differs")
    post_merge = handoff_path.exists()
    for payload, seal in (
        (replay, "result_sha256"),
        (attempts, "attempts_sha256"),
        (recovery, "record_sha256"),
        (repair, "report_sha256"),
        (migration, "migration_sha256"),
        (closeout, "closeout_sha256"),
        (progress, "progress_sha256"),
    ):
        _require_seal(payload, seal)
    if (
        replay_path.read_bytes() != replay_alias_path.read_bytes()
        or replay != replay_alias
    ):
        raise ValueError("Product v0.2.3.2.3 replay public alias differs")

    attempt_rows = attempts.get("attempts")
    if (
        replay.get("terminal")
        != "ECOMSRE_PRODUCT_V02323_DIAGNOSIS_PIPELINE_REPLAY_PASS"
        or replay.get("diagnosis_persistence_replay_attempt_count") != 1
        or replay.get("stage_journal_terminal") != "JOB_SUCCEEDED"
        or replay.get("stage_event_count") != 54
        or replay.get("original_failed_job_unchanged") is not True
        or replay.get("provider_agent_runbook_docker_calls") != 0
        or replay.get("measured_nofault_authority") != "NONE"
        or replay.get("knowledge_loop_authority") != "NONE"
        or attempts.get("attempt_count") != 1
        or attempts.get("diagnosis_persistence_replay_attempt_count") != 1
        or not isinstance(attempt_rows, list)
        or len(attempt_rows) != 1
        or attempt_rows[0].get("status") != "PASS"
        or attempt_rows[0].get("result_sha256") != replay.get("result_sha256")
    ):
        raise ValueError("Product v0.2.3.2.3 replay result differs")

    recovery_bindings = {
        "replay_id": "replay_id",
        "replay_of_job_id": "original_failed_job_id",
        "recovery_job_id": "recovery_job_id",
        "recovery_job_status": "recovery_job_status",
        "recovery_job_sha256": "recovery_job_sha256",
        "diagnosis_result_sha256": "diagnosis_result_sha256",
        "evidence_bundle_sha256": "evidence_bundle_sha256",
        "evidence_index_sha256": "evidence_index_sha256",
        "decision_trace_sha256": "decision_trace_sha256",
        "decision_trace_object_sha256": "decision_trace_object_sha256",
        "stage_event_count": "stage_event_count",
        "stage_journal_terminal": "stage_journal_terminal",
        "original_failed_job_unchanged": "original_failed_job_unchanged",
    }
    if (
        any(
            recovery.get(recovery_field) != replay.get(replay_field)
            for recovery_field, replay_field in recovery_bindings.items()
        )
        or recovery.get("terminal") != "ECOMSRE_PRODUCT_V02323_RECOVERY_JOB_FROZEN"
        or recovery.get("measured_nofault_authority") != "NONE"
        or recovery.get("knowledge_loop_authority") != "NONE"
    ):
        raise ValueError("Product v0.2.3.2.3 recovery job differs")
    if (
        repair.get("deterministic_structural_defect_identified") is not False
        or repair.get("targeted_repair") != "NOT_APPLICABLE"
        or repair.get("targeted_repair_sha256") is not None
        or repair.get("diagnosis_pipeline_replay_result_sha256")
        != replay.get("result_sha256")
        or repair.get("measured_nofault_authority") != "NONE"
        or repair.get("knowledge_loop_authority") != "NONE"
    ):
        raise ValueError("Product v0.2.3.2.3 targeted repair differs")
    _verify_test_delta(project, migration)

    expected_engineering_terminal = (
        ENGINEERING_COMPLETE_V02323
        if post_merge
        else "PENDING_MERGE_AND_PREDECESSOR_CLOSEOUT"
    )

    if (
        closeout.get("phase") != "DIAGNOSIS_REPLAY_COMPLETE"
        or closeout.get("repository_acceptance_terminal")
        != REPOSITORY_ACCEPTANCE_PASS_V02323
        or closeout.get("engineering_terminal") != expected_engineering_terminal
        or closeout.get("required_engineering_terminal") != ENGINEERING_COMPLETE_V02323
        or closeout.get("replay_result_sha256") != replay.get("result_sha256")
        or closeout.get("diagnosis_persistence_replay_attempt_count") != 1
        or closeout.get("provider_agent_runbook_docker_calls") != 0
        or closeout.get("new_business_traffic_executions") != 0
        or closeout.get("new_product_incidents") != 0
        or closeout.get("new_baseline_attempts") != 0
        or closeout.get("fault_attempts") != 0
        or closeout.get("measured_nofault_authority") != "NONE"
        or closeout.get("knowledge_loop_authority") != "NONE"
        or closeout.get("repository_state_manifest_sha256")
        != manifest.get("manifest_sha256")
        or closeout.get("test_migration_sha256") != migration.get("migration_sha256")
        or progress.get("increment") != 5
        or progress.get("phase") != "DIAGNOSIS_REPLAY_COMPLETE"
        or progress.get("repository_acceptance_terminal")
        != REPOSITORY_ACCEPTANCE_PASS_V02323
        or progress.get("engineering_terminal") != expected_engineering_terminal
        or progress.get("measured_nofault_authority") != "NONE"
        or progress.get("knowledge_loop_authority") != "NONE"
    ):
        raise ValueError("Product v0.2.3.2.3 closeout differs")

    handoff: dict[str, Any] | None = None
    successor_merge: dict[str, object] | None = None
    predecessor_closeout: list[dict[str, object]] | None = None
    if post_merge:
        handoff = _load(handoff_path)
        _require_seal(handoff, "handoff_sha256")
        merged_successor_commit = handoff.get("merged_successor_commit")
        if (
            not isinstance(merged_successor_commit, str)
            or len(merged_successor_commit) != 40
            or any(
                character not in "0123456789abcdef"
                for character in merged_successor_commit
            )
        ):
            raise ValueError("Product v0.2.3.2.3 merged successor commit differs")
        successor_merge, predecessor_closeout = validate_live_pull_request_closeout(
            merged_successor_commit,
            pull_request_state_provider=pull_request_state_provider,
        )
        reconstruction = _load(
            project / "docs/analysis/product-v02323-reconstruction-disposition.json"
        )
        root_cause = _load(
            project / "docs/analysis/product-v02323-diagnosis-root-cause.json"
        )
        snapshot = _load(
            project / "docs/analysis/product-v02323-forensic-source-snapshot.json"
        )
        digest = _load(project / "docs/analysis/product-v02323-digest-semantics.json")
        contamination = _load(
            project / "docs/analysis/product-v02323-schema9-contamination-audit.json"
        )
        journal_contract = _load(
            project / "docs/analysis/product-v02322-stage-journal-contract.json"
        )
        private_contract = _load(
            project / "docs/analysis/product-v02322-private-failure-contract.json"
        )
        blocker = _load(project / "docs/analysis/product-v02321-formal-blocker.json")
        if (
            not _is_ancestor(project, merged_successor_commit, head)
            or handoff.get("terminal") != FRESH_FORMAL_HANDOFF_READY_V02323
            or handoff.get("successor_pull_request") != 85
            or handoff.get("successor_merge") != successor_merge
            or handoff.get("predecessor_closeout") != predecessor_closeout
            or handoff.get("forensic_source_snapshot_sha256")
            != snapshot.get("snapshot_sha256")
            or handoff.get("digest_semantics_audit_sha256")
            != digest.get("audit_sha256")
            or handoff.get("schema9_contamination_audit_sha256")
            != contamination.get("audit_sha256")
            or handoff.get("reconstruction_disposition_sha256")
            != reconstruction.get("disposition_sha256")
            or handoff.get("sealed_schema8_reconstruction_sha256")
            != reconstruction.get("reconstruction_sha256")
            or handoff.get("replay_classification")
            != replay.get("replay_classification")
            or handoff.get("root_cause_disposition") != root_cause.get("disposition")
            or handoff.get("root_cause_disposition_sha256")
            != root_cause.get("disposition_sha256")
            or handoff.get("targeted_repair_sha256")
            != repair.get("targeted_repair_sha256")
            or handoff.get("diagnosis_replay_result_sha256")
            != replay.get("result_sha256")
            or handoff.get("stage_journal_contract_sha256")
            != journal_contract.get("contract_sha256")
            or handoff.get("private_failure_contract_sha256")
            != private_contract.get("contract_sha256")
            or handoff.get("repository_state_manifest_sha256")
            != manifest.get("manifest_sha256")
            or handoff.get("pr83_formal_blocker_sha256")
            != blocker.get("blocker_sha256")
            or handoff.get("replay_is_live_nofault_result") is not False
            or handoff.get("measured_nofault_authority") != "NONE"
            or handoff.get("knowledge_loop_authority") != "NONE"
            or closeout.get("merged_successor_commit") != merged_successor_commit
            or closeout.get("successor_merge") != successor_merge
            or closeout.get("predecessor_closeout") != predecessor_closeout
            or closeout.get("fresh_formal_handoff_sha256")
            != handoff.get("handoff_sha256")
            or progress.get("merged_successor_commit") != merged_successor_commit
            or progress.get("successor_merge") != successor_merge
            or progress.get("predecessor_closeout") != predecessor_closeout
            or progress.get("fresh_formal_handoff_sha256")
            != handoff.get("handoff_sha256")
        ):
            raise ValueError("Product v0.2.3.2.3 post-merge handoff differs")
        handoff_markdown = handoff_markdown_path.read_text(encoding="utf-8")
        if (
            not handoff_markdown.endswith("\n")
            or FRESH_FORMAL_HANDOFF_READY_V02323 not in handoff_markdown
            or merged_successor_commit not in handoff_markdown
            or "measured No-Fault" not in handoff_markdown
        ):
            raise ValueError("Product v0.2.3.2.3 handoff prose differs")

    verify_progress_transition_v02323(
        project,
        progress,
        repository_state_manifest_sha256=manifest["manifest_sha256"],
        test_migration_sha256=migration["migration_sha256"],
        post_merge=post_merge,
        handoff=handoff,
        successor_merge=successor_merge,
        predecessor_closeout=predecessor_closeout,
    )

    closeout_markdown = (
        project / "docs/results/product-v02323-engineering-closeout.md"
    ).read_text(encoding="utf-8")
    if (
        not closeout_markdown.endswith("\n")
        or "measured No-Fault" not in closeout_markdown
        or (ENGINEERING_COMPLETE_V02323 in closeout_markdown) is not post_merge
    ):
        raise ValueError("Product v0.2.3.2.3 engineering closeout prose differs")
    for relative in (
        "docs/results/product-v02323-limitations.md",
        "docs/results/product-v02323-interview-brief.md",
    ):
        text = (project / relative).read_text(encoding="utf-8")
        if (
            not text.endswith("\n")
            or "measured No-Fault" not in text
            or ENGINEERING_COMPLETE_V02323 in text
        ):
            raise ValueError(f"Product v0.2.3.2.3 closeout prose differs: {relative}")

    return {
        "terminal": REPOSITORY_ACCEPTANCE_PASS_V02323,
        "head": head,
        "phase": state["phase"],
        "history_terminal": history["terminal"],
        "replay_result_sha256": replay["result_sha256"],
        "repository_state_manifest_sha256": closeout[
            "repository_state_manifest_sha256"
        ],
        "diagnosis_persistence_replay_attempt_count": 1,
        "provider_agent_runbook_docker_calls": 0,
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
        "engineering_terminal": expected_engineering_terminal,
        "fresh_formal_handoff_terminal": (
            FRESH_FORMAL_HANDOFF_READY_V02323 if post_merge else None
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--expected-head")
    parser.add_argument("--require-clean-head", action="store_true")
    arguments = parser.parse_args(argv)
    print(
        json.dumps(
            verify_product_v02323_increment5(
                arguments.root,
                expected_head=arguments.expected_head,
                require_clean_head=arguments.require_clean_head,
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "verify_progress_transition_v02323",
    "verify_product_v02323_increment5",
)
