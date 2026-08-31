from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.repository_state_v02323 import (
    REPOSITORY_STATE_MODEL_PASS_V02323,
    ProductV02323RepositoryStateManifest,
    RepositoryPhaseV02323,
    verify_repository_state_v02323,
)
from scripts.ci.verify_product_v02323_increment5 import (
    verify_progress_transition_v02323,
    verify_product_v02323_increment5,
)
from scripts.product_v02323.build_increment5_closeout import (
    finalize_post_merge_closeout,
)


ROOT = Path(__file__).resolve().parents[2]
_POST_MERGE_INPUTS = (
    "config/product-v02323/repository-state-manifest.json",
    "docs/analysis/product-v02321-formal-blocker.json",
    "docs/analysis/product-v02322-private-failure-contract.json",
    "docs/analysis/product-v02322-stage-journal-contract.json",
    "docs/analysis/product-v02323-diagnosis-pipeline-replay.json",
    "docs/analysis/product-v02323-diagnosis-root-cause.json",
    "docs/analysis/product-v02323-digest-semantics.json",
    "docs/analysis/product-v02323-forensic-source-snapshot.json",
    "docs/analysis/product-v02323-progress.json",
    "docs/analysis/product-v02323-reconstruction-disposition.json",
    "docs/analysis/product-v02323-schema9-contamination-audit.json",
    "docs/analysis/product-v02323-targeted-repair.json",
    "docs/results/product-v02323-engineering-closeout.json",
)


def _reseal(payload: dict[str, object]) -> dict[str, object]:
    body = dict(payload)
    body.pop("manifest_sha256", None)
    body["manifest_sha256"] = semantic_sha256_v22(body)
    return body


def _pull_request_state_provider(
    merged_successor_commit: str,
    *,
    open_predecessor: int | None = None,
    successor_merged: bool = True,
):
    def provider(number: int) -> dict[str, object]:
        if number == 85:
            return {
                "number": 85,
                "state": "closed" if successor_merged else "open",
                "merged": successor_merged,
                "merged_at": ("2026-08-31T12:00:00Z" if successor_merged else None),
                "merge_commit_sha": merged_successor_commit,
                "head": {
                    "ref": "codex/product-v02323-schema8-reconstruction-replay",
                    "sha": "c" * 40,
                },
                "base": {"ref": "main"},
            }
        return {
            "number": number,
            "state": "open" if number == open_predecessor else "closed",
            "merged": False,
            "merged_at": None,
            "_superseded_disposition_comment_observed": True,
        }

    return provider


def test_repository_phase_contract_retains_history_and_adds_forensic_phases() -> None:
    assert tuple(RepositoryPhaseV02323) == (
        RepositoryPhaseV02323.PRE_FORMAL,
        RepositoryPhaseV02323.FORMAL_RUNNING,
        RepositoryPhaseV02323.FORMAL_BLOCKED_DIAGNOSIS,
        RepositoryPhaseV02323.FORENSIC_SOURCE_BLOCKED,
        RepositoryPhaseV02323.SCHEMA8_RECONSTRUCTION_COMPLETE,
        RepositoryPhaseV02323.DIAGNOSIS_REPLAY_COMPLETE,
        RepositoryPhaseV02323.MEASURED_COMPLETE,
    )

    manifest = ProductV02323RepositoryStateManifest.load(
        ROOT / "config/product-v02323/repository-state-manifest.json"
    )
    assert manifest.phase is RepositoryPhaseV02323.DIAGNOSIS_REPLAY_COMPLETE
    payload = manifest.model_dump(mode="json")
    payload["phase"] = RepositoryPhaseV02323.SCHEMA8_RECONSTRUCTION_COMPLETE.value
    with pytest.raises(ValueError, match="phase artifact contract differs"):
        ProductV02323RepositoryStateManifest.model_validate(_reseal(payload))


def test_current_tree_is_a_non_authorizing_replay_complete_state() -> None:
    result = verify_repository_state_v02323(ROOT)

    assert result == {
        "terminal": REPOSITORY_STATE_MODEL_PASS_V02323,
        "phase": RepositoryPhaseV02323.DIAGNOSIS_REPLAY_COMPLETE.value,
        "pr83_formal_blocker_sha256": (
            "2f8f6fd26c7783091c00fb9cdcfaa29f145b4d29b31f16ec6ac1c8fb3e9999f1"
        ),
        "pr84_private_state_contract_sha256": (
            "4aebb50acef0e21a964cdb812cd4d2b6aa8983d3ff106e3d05a8a4df7aa61812"
        ),
        "reconstruction_disposition_sha256": (
            "2484d82229338cf60ce7a477d630bf7faa3f416677e4a3f7293335731a3e4d01"
        ),
        "replay_result_sha256": (
            "20642e01c367f5cfc7ef481ae900d52587ee179676da0a188f6ca2519b962ea6"
        ),
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
    }


def test_formal_blocker_phase_remains_verifiable_in_an_isolated_fixture(
    tmp_path: Path,
) -> None:
    relative = "docs/analysis/product-v02321-formal-blocker.json"
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    shutil.copy2(ROOT / relative, target)
    template = ProductV02323RepositoryStateManifest.load(
        ROOT / "config/product-v02323/repository-state-manifest.json"
    ).model_dump(mode="json")
    template.update(
        {
            "phase": RepositoryPhaseV02323.FORMAL_BLOCKED_DIAGNOSIS.value,
            "pr84_private_state_contract_sha256": None,
            "forensic_source_snapshot_sha256": None,
            "reconstruction_disposition_sha256": None,
            "replay_result_sha256": None,
            "allowed_artifacts": [
                item
                for item in template["allowed_artifacts"]
                if item["path"] == relative
            ],
            "forbidden_artifacts": [],
        }
    )
    manifest = ProductV02323RepositoryStateManifest.model_validate(_reseal(template))

    result = verify_repository_state_v02323(tmp_path, manifest=manifest)
    assert result["phase"] == RepositoryPhaseV02323.FORMAL_BLOCKED_DIAGNOSIS.value
    assert result["terminal"] == REPOSITORY_STATE_MODEL_PASS_V02323


def test_current_manifest_rejects_resealed_artifact_tampering(tmp_path: Path) -> None:
    manifest = ProductV02323RepositoryStateManifest.load(
        ROOT / "config/product-v02323/repository-state-manifest.json"
    )
    first = manifest.allowed_artifacts[0]
    target = tmp_path / first.path
    target.parent.mkdir(parents=True)
    target.write_bytes((ROOT / first.path).read_bytes() + b" ")

    with pytest.raises(ValueError, match="repository artifact bytes differ"):
        verify_repository_state_v02323(tmp_path, manifest=manifest)


def test_test_migration_classifies_every_changed_test_without_deletion() -> None:
    payload = json.loads(
        (ROOT / "docs/analysis/product-v02323-test-migration.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["changed_tests"] == [
        {
            "category": "PATH_OR_SHA_REBINDING",
            "path": "tests/product/test_increment1_historical_contract.py",
        },
        {
            "category": "PHASE_FIXTURE_MIGRATION",
            "path": "tests/product_v023/test_increment3_live_baseline_runner.py",
        },
        {
            "category": "PHASE_FIXTURE_MIGRATION",
            "path": (
                "tests/product_v02321/test_increment3_state_clone_and_live_preflight.py"
            ),
        },
        {
            "category": "PHASE_FIXTURE_MIGRATION",
            "path": "tests/product_v02321/test_increment4_formal_nofault.py",
        },
        {
            "category": "NEW_FORENSIC_COVERAGE",
            "path": (
                "tests/product_v02323/test_increment1_forensic_snapshot_and_digest.py"
            ),
        },
        {
            "category": "NEW_FORENSIC_COVERAGE",
            "path": "tests/product_v02323/test_increment2_schema8_reconstruction.py",
        },
        {
            "category": "NEW_FORENSIC_COVERAGE",
            "path": "tests/product_v02323/test_increment3_diagnosis_forensics.py",
        },
        {
            "category": "NEW_FORENSIC_COVERAGE",
            "path": "tests/product_v02323/test_increment4_persistence_replay.py",
        },
        {
            "category": "NEW_FORENSIC_COVERAGE",
            "path": "tests/product_v02323/test_increment5_repository_acceptance.py",
        },
        {
            "category": "GENUINE_IMPLEMENTATION_REPAIR",
            "path": "tests/unit/test_command_runner.py",
        },
    ]
    assert payload["deleted_test_count"] == 0
    assert payload["skipped_or_xfailed_v02321_semantic_test_count"] == 0
    assert payload["terminal"] == "ECOMSRE_PRODUCT_V02323_TEST_MIGRATION_PASS"


def test_public_increment5_repository_acceptance_verifier_passes() -> None:
    result = verify_product_v02323_increment5(ROOT)

    assert result["terminal"] == "ECOMSRE_PRODUCT_V02323_REPOSITORY_ACCEPTANCE_PASS"
    assert result["phase"] == "DIAGNOSIS_REPLAY_COMPLETE"
    assert result["diagnosis_persistence_replay_attempt_count"] == 1
    assert result["provider_agent_runbook_docker_calls"] == 0
    assert result["measured_nofault_authority"] == "NONE"
    assert result["knowledge_loop_authority"] == "NONE"
    handoff_exists = (
        ROOT / "docs/analysis/product-v02323-fresh-formal-handoff.json"
    ).exists()
    assert result["engineering_terminal"] == (
        "ECOMSRE_PRODUCT_V02323_SCHEMA8_RECONSTRUCTION_DIAGNOSIS_REPLAY_COMPLETE"
        if handoff_exists
        else "PENDING_MERGE_AND_PREDECESSOR_CLOSEOUT"
    )


def test_post_merge_finalizer_binds_commit_and_preserves_authority_boundary(
    tmp_path: Path,
) -> None:
    for relative in _POST_MERGE_INPUTS:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)

    merged_successor_commit = "a" * 40
    result = finalize_post_merge_closeout(
        tmp_path,
        merged_successor_commit=merged_successor_commit,
        pull_request_state_provider=_pull_request_state_provider(
            merged_successor_commit
        ),
    )

    assert result["terminal"] == (
        "ECOMSRE_PRODUCT_V02323_SCHEMA8_RECONSTRUCTION_DIAGNOSIS_REPLAY_COMPLETE"
    )
    assert result["fresh_formal_handoff_terminal"] == (
        "ECOMSRE_PRODUCT_V02323_FRESH_FORMAL_NOFAULT_HANDOFF_READY"
    )
    handoff = json.loads(
        (tmp_path / "docs/analysis/product-v02323-fresh-formal-handoff.json").read_text(
            encoding="utf-8"
        )
    )
    assert handoff["merged_successor_commit"] == merged_successor_commit
    assert handoff["targeted_repair_sha256"] is None
    assert handoff["replay_is_live_nofault_result"] is False
    assert handoff["measured_nofault_authority"] == "NONE"
    assert handoff["knowledge_loop_authority"] == "NONE"
    closeout = json.loads(
        (tmp_path / "docs/results/product-v02323-engineering-closeout.json").read_text(
            encoding="utf-8"
        )
    )
    assert closeout["predecessor_closeout"] == [
        {
            "pull_request": number,
            "state": "CLOSED",
            "merged": False,
            "merged_at": None,
            "disposition": "SUPERSEDED_WITHOUT_MERGE",
            "superseded_comment_marker": (
                "ECOMSRE_PRODUCT_V02323_SUPERSEDED_BY_PR85_WITHOUT_MERGE"
            ),
        }
        for number in (82, 83, 84)
    ]
    assert closeout["successor_merge"]["merge_commit_sha"] == (merged_successor_commit)


def test_post_merge_finalizer_requires_all_frozen_predecessors(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="predecessor closeout differs"):
        finalize_post_merge_closeout(
            tmp_path,
            merged_successor_commit="b" * 40,
            pull_request_state_provider=_pull_request_state_provider(
                "b" * 40,
                open_predecessor=83,
            ),
        )


def test_post_merge_finalizer_rejects_unmerged_successor(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="successor merge state differs"):
        finalize_post_merge_closeout(
            tmp_path,
            merged_successor_commit="b" * 40,
            pull_request_state_provider=_pull_request_state_provider(
                "b" * 40,
                successor_merged=False,
            ),
        )


def test_premerge_progress_rejects_injected_final_terminal() -> None:
    progress = json.loads(
        (ROOT / "docs/analysis/product-v02323-progress.json").read_text(
            encoding="utf-8"
        )
    )
    progress["terminals"].append(
        "ECOMSRE_PRODUCT_V02323_SCHEMA8_RECONSTRUCTION_DIAGNOSIS_REPLAY_COMPLETE"
    )
    body = dict(progress)
    body.pop("progress_sha256")
    progress["progress_sha256"] = semantic_sha256_v22(body)
    manifest = json.loads(
        (ROOT / "config/product-v02323/repository-state-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    migration = json.loads(
        (ROOT / "docs/analysis/product-v02323-test-migration.json").read_text(
            encoding="utf-8"
        )
    )

    with pytest.raises(ValueError, match="exact progress transition differs"):
        verify_progress_transition_v02323(
            ROOT,
            progress,
            repository_state_manifest_sha256=manifest["manifest_sha256"],
            test_migration_sha256=migration["migration_sha256"],
            post_merge=False,
        )
