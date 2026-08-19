from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ci.verify_dta_v22_pr_d import (
    EXPECTED_PR_E_ACTIVITY,
    PR_C_SUCCESSOR_ATTESTATION,
    _require_single_parent_commit,
    _verify_runtime_contracts,
    verify_pr_d_bindings,
    verify_pr_d_protocol,
    verify_provider_summary,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pr_d_verifier_closes_controller_and_provider_protocol_gates() -> None:
    if not (REPO_ROOT / PR_C_SUCCESSOR_ATTESTATION).exists():
        pytest.skip("PR-C successor attestation is added only at final exact head")
    assert verify_pr_d_protocol(REPO_ROOT) == {
        "schema_version": "dta-v22-pr-d-verification.v1",
        "status": "PASS",
        "historical_bindings": "PASS",
        "pr_c_successor_gate": "PASS",
        "public_scan_mode": "PR_D_CLOSED_SURFACE",
        "secret_private_path_scan": "PASS",
        "truth_isolation": "PASS",
        "shared_controller_schema": "PASS",
        "bounded_correction": "PASS",
        "identity_manifests": "PASS",
        "provider_protocol_gate": "PASS",
        "terminal": "DTA_V22_PR_D_CONTROLLER_READY",
    }


def test_pr_d_manifest_is_raw_and_artifact_hash_bound(tmp_path: Path) -> None:
    manifest = verify_pr_d_bindings(REPO_ROOT)
    assert manifest["terminal"] == "DTA_V22_PR_D_CONTROLLER_READY"
    assert len(manifest["artifacts"]) == 15

    source = REPO_ROOT / "config/dta-v22/pr-d-controller-bindings.v1.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["provider_protocol_gate"]["invalid_dispatches"] = 1
    tampered = tmp_path / "manifest.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest raw SHA-256"):
        verify_pr_d_bindings(REPO_ROOT, manifest_path=tampered)


def test_pr_d_public_provider_summary_is_exactly_bound(tmp_path: Path) -> None:
    summary = verify_provider_summary(REPO_ROOT)
    assert summary["transition_count"] == 50
    assert summary["provider_protocol_calls"] == 52
    assert summary["raw_provider_content_published"] is False

    source = REPO_ROOT / "docs/analysis/dta-v22-pr-d-provider-protocol-summary.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["first_pass_protocol_acceptance"] = 1.0
    tampered = tmp_path / "summary.json"
    tampered.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="summary digest"):
        verify_provider_summary(REPO_ROOT, summary_path=tampered)


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
