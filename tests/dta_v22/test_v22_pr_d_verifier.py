from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ci.verify_dta_v22_pr_d import (
    BLOCKED_PR_D_TERMINAL,
    EXPECTED_PR_E_ACTIVITY,
    PR_D_MANIFEST,
    PROVIDER_SUMMARY,
    PROVIDER_V3_CAMPAIGN_SUMMARY,
    PROVIDER_V3_PREREGISTRATION,
    PROVIDER_V3_REPLICATE_SUMMARIES,
    _require_single_parent_commit,
    _verify_runtime_contracts,
    main,
    verify_blocked_provider_attempts,
    verify_pr_d_bindings,
    verify_provider_summary,
    verify_provider_v3_campaign_results,
    verify_provider_v3_preregistration,
    verify_pr_c_stage_aware_gate,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pr_d_verifier_binds_exact_provider_protocol_v3_blocked_campaign() -> None:
    preregistration = verify_provider_v3_preregistration(REPO_ROOT)
    results = verify_provider_v3_campaign_results(REPO_ROOT)
    assert preregistration["preregistration_sha256"] == (
        "3ef35bc80a151e90c4bc21f27f061e496819a916e12020ff20dcd65719d03a8f"
    )
    assert results["terminal"] == BLOCKED_PR_D_TERMINAL
    assert results["campaign_sha256"] == (
        "b23184d23ad5d6fc801e85efca268d5c7e7ad951ee004b8221fe2a5889211170"
    )


def test_pr_d_negative_result_artifacts_are_exactly_bound() -> None:
    assert (REPO_ROOT / PROVIDER_V3_PREREGISTRATION).is_file()
    assert all(
        (REPO_ROOT / relative).is_file()
        for relative in (*PROVIDER_V3_REPLICATE_SUMMARIES, PROVIDER_V3_CAMPAIGN_SUMMARY)
    )
    results = verify_provider_v3_campaign_results(REPO_ROOT)
    assert results["replicate_provider_calls"] == [18, 3]
    assert results["observed_provider_calls"] == 22
    assert results["both_replicates_independently_passed"] is False
    assert results["terminal"] == BLOCKED_PR_D_TERMINAL
    assert not (REPO_ROOT / PR_D_MANIFEST).exists()
    assert not (REPO_ROOT / PROVIDER_SUMMARY).exists()
    with pytest.raises(FileNotFoundError):
        verify_pr_d_bindings(REPO_ROOT)
    with pytest.raises(FileNotFoundError):
        verify_provider_summary(REPO_ROOT)


def test_pr_d_blocked_attempts_are_exactly_bound(tmp_path: Path) -> None:
    attempts = verify_blocked_provider_attempts(REPO_ROOT)
    assert tuple(item["attempt_ordinal"] for item in attempts) == (1, 2, 3, 4, 5)
    assert attempts[-1]["blocker"] == BLOCKED_PR_D_TERMINAL

    source = (
        REPO_ROOT
        / "docs/analysis/dta-v22-pr-d-provider-protocol-attempt-5-gate-blocked.json"
    )
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["provider_gate_eligible"] = True
    tampered = tmp_path / source.name
    tampered.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="attempt 5 contract differs"):
        verify_blocked_provider_attempts(
            REPO_ROOT,
            attempt_paths=(*(
                REPO_ROOT
                / f"docs/analysis/dta-v22-pr-d-provider-protocol-attempt-{ordinal}-{suffix}.json"
                for ordinal, suffix in (
                    (1, "invalid-location"),
                    (2, "validator-abort"),
                    (3, "rate-limited"),
                    (4, "rate-limited"),
                )
            ), tampered),
        )


def test_pr_c_stage_gate_does_not_require_successor_attestation_before_pr_d_merge(
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected = {
        "schema_version": "dta-v22-pr-c-stage-aware-verification.v1",
        "status": "PASS",
        "mode": "PR_D_STAGE_FROZEN_BINDINGS",
        "successor_attestation": "NOT_APPLICABLE_UNMERGED_PR_D",
    }
    assert verify_pr_c_stage_aware_gate(REPO_ROOT) == expected
    assert main(("--root", str(REPO_ROOT), "--stage-aware-pr-c")) == 0
    assert json.loads(capsys.readouterr().out) == expected


def test_pr_c_stage_gate_restores_full_successor_verifier_after_pr_d(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.ci.verify_dta_v22_pr_d._load_json",
        lambda _path: {"current_stage": "PR-E"},
    )
    monkeypatch.setattr(
        "scripts.ci.verify_dta_v22_pr_d.verify_pr_c_protocol",
        lambda _root: {"status": "PASS", "mode": "FULL_SUCCESSOR_PROVENANCE"},
    )
    assert verify_pr_c_stage_aware_gate(REPO_ROOT) == {
        "status": "PASS",
        "mode": "FULL_SUCCESSOR_PROVENANCE",
    }


def test_pr_d_runtime_contract_markers_are_bound() -> None:
    _verify_runtime_contracts()


def test_pr_e_successor_activity_contract_is_stage_specific() -> None:
    assert EXPECTED_PR_E_ACTIVITY == {
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


def test_successor_final_attestation_commit_must_be_single_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.ci.verify_dta_v22_pr_d._git_text",
        lambda _root, *_args: "head parent-a parent-b",
    )
    with pytest.raises(ValueError, match="PR-E final attestation commit"):
        _require_single_parent_commit(
            REPO_ROOT,
            "f" * 40,
            label="PR-E final attestation commit",
        )
