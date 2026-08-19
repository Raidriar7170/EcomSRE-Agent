from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ci.verify_dta_v22_pr_a import (
    PUBLIC_PR_A_ARTIFACTS,
    _public_scan_plan,
    assert_no_public_leak,
    verify_pr_a_protocol,
    verify_v21_mypy_exception,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_v22_namespace_is_versioned_and_independent() -> None:
    import ecomsre.dta_v2.v22 as v22

    assert v22.SCHEMA_PREFIX == "dta-v22."
    assert v22.PUBLIC_RESULT_PREFIX == "dta-v22-"
    assert v22.GOAL_VERSION == "dta-v22-p0-master-v1"
    assert v22.PR_A_TERMINAL == "DTA_V22_PR_A_PROTOCOL_READY"


def test_master_progress_starts_at_exact_pr_a_boundary() -> None:
    progress = json.loads(
        (REPO_ROOT / "docs/analysis/dta-v22-p0-master-progress.json").read_text(
            encoding="utf-8"
        )
    )

    assert progress == {
        "schema_version": "dta-v22-p0-master-progress.v1",
        "goal_version": "dta-v22-p0-master-v1",
        "inspected_starting_main": "9da92d54a4fb470c5452cee36a731e81529d05a5",
        "actual_starting_main": "9da92d54a4fb470c5452cee36a731e81529d05a5",
        "completed_stage": None,
        "current_stage": "PR-A",
        "active_branch": "codex/dta-v22-p0-pr-a-protocol-audit",
        "active_pr": None,
        "merged_prs": [],
        "primary_model": "gpt-5.4-mini-2026-03-17",
        "provider_mode": None,
        "flat_identity_sha256": None,
        "planner_identity_sha256": None,
        "router_identity_sha256": None,
        "one_shot_identity_sha256": None,
        "development_report_sha256": None,
        "held_out_seal_sha256": None,
        "held_out_execution_id": None,
        "planner_claim": None,
        "memory_claim": None,
        "final_engineering_terminal": None,
    }


def test_pr_a_documents_freeze_the_protocol_without_claiming_results() -> None:
    decisions = (REPO_ROOT / "docs/DECISIONS.md").read_text(encoding="utf-8")
    for decision_id in range(49, 56):
        assert decisions.count(f"## DEC-{decision_id:03d} —") == 1

    design = (
        REPO_ROOT / "docs/design/diagnosis-to-action-v2.2-p0.md"
    ).read_text(encoding="utf-8")
    scoring = (
        REPO_ROOT / "docs/design/dta-v22-evaluation-metrics.md"
    ).read_text(encoding="utf-8")
    audit = (
        REPO_ROOT / "docs/analysis/dta-v21-forensic-audit-for-v22.md"
    ).read_text(encoding="utf-8")
    taxonomy = json.loads(
        (
            REPO_ROOT
            / "docs/analysis/dta-v21-private-failure-taxonomy-summary.json"
        ).read_text(encoding="utf-8")
    )

    assert "ControllerDecisionV22" in design
    assert "NO_INCIDENT" in design
    assert "ABSTAIN" in design
    assert "ORACLE_CONTEXT_UPPER_BOUND" in scoring
    assert "INFINITY / NOT_ESTIMABLE" in scoring
    assert "DTA_V22_NO_PREREGISTERED_ADVANTAGE_SUPPORTED" in scoring
    assert "src/ecomsre/dta_v2/v21/planner_contracts.py" in audit
    assert "src/ecomsre/dta_v2/v21/evaluation_replay.py" in audit
    assert taxonomy["planner_held_out_entries"] == 8
    assert taxonomy["protocol_accepted_entries"] == 2
    assert taxonomy["provider_protocol_failure_entries"] == 6
    assert taxonomy["held_out_seal_sha256"] == (
        "9a7c8e56400e99c693c8bddc26007b1dd26e0dcee2167b07cf3fba00fd22fbd7"
    )
    assert taxonomy["bounded_provider_failure_chain_counts"] == {
        "hypotheses:value_error": 1,
        "output:planner_abstain_output_shape": 5,
        "output:value_error": 5,
    }
    assert taxonomy["raw_provider_content_published"] is False
    assert taxonomy["private_case_mapping_published"] is False


def test_pr_a_protocol_verifier_passes_truth_and_publication_gates() -> None:
    result = verify_pr_a_protocol(REPO_ROOT)

    assert result["historical_bindings"] == "PASS"
    assert result["public_scan_mode"] == "PR_A_CLOSED_SURFACE"
    assert result["truth_isolation"] == "PASS"
    assert result["secret_private_path_scan"] == "PASS"


@pytest.mark.parametrize(
    "leak",
    (
        "private path /" + "Users/example/.ecomsre/run.json",
        "Authorization" + ": Bear" + "er secret-value",
        "ECOMSRE_LLM_" + "API_" + "KEY=secret-value",
        "sk-" + "example12345678",
    ),
)
def test_pr_a_publication_scanner_rejects_private_or_secret_material(
    leak: str,
) -> None:
    with pytest.raises(ValueError, match="public leakage"):
        assert_no_public_leak(leak)


def test_pr_a_gate_has_a_successor_safe_persistent_mode() -> None:
    mode, paths = _public_scan_plan(
        {"current_stage": "PR-B", "completed_stage": "PR-A"}
    )

    assert mode == "SUCCESSOR_PERSISTENT_ARTIFACTS"
    assert paths == PUBLIC_PR_A_ARTIFACTS


def test_frozen_v21_mypy_exception_is_exact_and_cannot_broaden(
    tmp_path: Path,
) -> None:
    source = REPO_ROOT / "mypy.ini"
    verify_v21_mypy_exception(source)

    broadened = tmp_path / "mypy.ini"
    broadened.write_text(
        source.read_text(encoding="utf-8").replace(
            "[mypy-ecomsre.dta_v2.v21.live_final_cli]\n"
            "disable_error_code = arg-type\n",
            "[mypy-ecomsre.dta_v2.v21.live_final_cli]\n"
            "disable_error_code = arg-type, assignment\n",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="frozen v2.1 mypy exception"):
        verify_v21_mypy_exception(broadened)

    wildcard = tmp_path / "wildcard.ini"
    wildcard.write_text(
        source.read_text(encoding="utf-8").replace(
            "[mypy-ecomsre.dta_v2.v21.live_final_cli]",
            "[mypy-ecomsre.dta_v2.v21.*]",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="frozen v2.1 mypy exception"):
        verify_v21_mypy_exception(wildcard)
