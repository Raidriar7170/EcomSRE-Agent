from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ci.verify_dta_v22_pr_d import (
    BLOCKED_PR_D_TERMINAL,
    EXPECTED_PR_E_ACTIVITY,
    PR_D_MANIFEST,
    PROVIDER_SUMMARY,
    _require_single_parent_commit,
    _verify_runtime_contracts,
    main,
    verify_blocked_provider_attempts,
    verify_pr_d_bindings,
    verify_pr_d_protocol,
    verify_provider_summary,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pr_d_verifier_binds_exact_provider_protocol_blocker() -> None:
    assert verify_pr_d_protocol(REPO_ROOT) == {
        "schema_version": "dta-v22-pr-d-verification.v1",
        "status": "BLOCKED",
        "historical_bindings": "PASS",
        "pr_c_successor_gate": "NOT_APPLICABLE_BLOCKED",
        "public_scan_mode": "PR_D_BLOCKED_SURFACE",
        "secret_private_path_scan": "PASS",
        "truth_isolation": "PASS",
        "shared_controller_schema": "PASS",
        "bounded_correction": "PASS",
        "identity_manifests": "NOT_FROZEN",
        "provider_protocol_gate": "BLOCKED",
        "terminal": BLOCKED_PR_D_TERMINAL,
    }


def test_pr_d_pass_artifacts_are_absent_while_blocked() -> None:
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


def test_pr_d_cli_fails_with_exact_blocker(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(RuntimeError, match=f"^{BLOCKED_PR_D_TERMINAL}$"):
        main(("--root", str(REPO_ROOT)))
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "BLOCKED"
    assert output["terminal"] == BLOCKED_PR_D_TERMINAL


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
