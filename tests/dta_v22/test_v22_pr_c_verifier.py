from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ci.verify_dta_v22_pr_c import (
    _verify_runtime_contracts,
    verify_pr_c_bindings,
    verify_pr_c_protocol,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pr_c_verifier_closes_memory_predicate_and_diagnosis_gates() -> None:
    result = verify_pr_c_protocol(REPO_ROOT)

    assert result == {
        "schema_version": "dta-v22-pr-c-verification.v1",
        "status": "PASS",
        "historical_bindings": "PASS",
        "pr_b_successor_gate": "PASS",
        "public_scan_mode": "PR_C_CLOSED_SURFACE",
        "secret_private_path_scan": "PASS",
        "truth_isolation": "PASS",
        "memory_contract": "PASS",
        "predicate_policy": "PASS",
        "diagnosis_candidate_filter": "PASS",
        "terminal": "DTA_V22_PR_C_MEMORY_PREDICATES_READY",
    }


def test_pr_c_binding_manifest_is_raw_and_artifact_hash_bound(tmp_path: Path) -> None:
    manifest = verify_pr_c_bindings(REPO_ROOT)
    assert manifest["terminal"] == "DTA_V22_PR_C_MEMORY_PREDICATES_READY"
    assert len(manifest["artifacts"]) == 6

    source = REPO_ROOT / "config/dta-v22/pr-c-memory-predicate-bindings.v1.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["memory_contract"]["salient_top_k_max"] = 512
    tampered = tmp_path / "manifest.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest raw SHA-256"):
        verify_pr_c_bindings(REPO_ROOT, manifest_path=tampered)


def test_pr_c_runtime_contract_markers_are_bound() -> None:
    _verify_runtime_contracts()
