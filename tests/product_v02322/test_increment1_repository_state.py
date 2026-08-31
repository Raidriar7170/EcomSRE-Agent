from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.repository_state_v02322 import (
    HISTORY_AND_BLOCKER_PASS_V02322,
    REPOSITORY_STATE_MODEL_PASS_V02322,
    ProductV02322RepositoryStateManifest,
    RepositoryPhaseV02322,
    verify_repository_state_v02322,
)
from scripts.ci.verify_product_v02322_history import verify_product_v02322_history


ROOT = Path(__file__).resolve().parents[2]


def test_repository_phase_contract_is_exact_and_mutually_exclusive() -> None:
    assert tuple(RepositoryPhaseV02322) == (
        RepositoryPhaseV02322.PRE_FORMAL,
        RepositoryPhaseV02322.FORMAL_RUNNING,
        RepositoryPhaseV02322.FORMAL_BLOCKED_DIAGNOSIS,
        RepositoryPhaseV02322.DIAGNOSIS_REPLAY_COMPLETE,
        RepositoryPhaseV02322.MEASURED_COMPLETE,
    )

    manifest = ProductV02322RepositoryStateManifest.load(
        ROOT / "config/product-v02322/repository-state-manifest.json"
    )
    assert manifest.phase is RepositoryPhaseV02322.FORMAL_BLOCKED_DIAGNOSIS
    with pytest.raises(ValueError, match="phase artifact contract differs"):
        ProductV02322RepositoryStateManifest.model_validate(
            {
                **manifest.model_dump(mode="json"),
                "phase": RepositoryPhaseV02322.PRE_FORMAL.value,
            }
        )


def test_current_tree_is_a_legal_formal_diagnosis_blocker_state() -> None:
    result = verify_repository_state_v02322(ROOT)

    assert result == {
        "terminal": REPOSITORY_STATE_MODEL_PASS_V02322,
        "phase": RepositoryPhaseV02322.FORMAL_BLOCKED_DIAGNOSIS.value,
        "formal_blocker_sha256": (
            "2f8f6fd26c7783091c00fb9cdcfaa29f145b4d29b31f16ec6ac1c8fb3e9999f1"
        ),
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
    }


def test_pre_formal_phase_remains_verifiable_in_an_isolated_fixture(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "pre-formal"
    for relative in (
        "docs/analysis/product-v02321-progress-pre-formal.json",
        "docs/analysis/product-v02321-formal-contract-freeze.json",
        "docs/external-reviews/product-v02321-pre-execution-review.md",
    ):
        target = fixture / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)

    template = ProductV02322RepositoryStateManifest.load(
        ROOT / "config/product-v02322/repository-state-manifest.json"
    )
    payload = template.model_dump(mode="json")
    payload.update(
        {
            "phase": RepositoryPhaseV02322.PRE_FORMAL.value,
            "formal_blocker_sha256": None,
            "allowed_artifacts": [
                item
                for item in payload["allowed_artifacts"]
                if item["role"]
                in {
                    "PRE_FORMAL_PROGRESS",
                    "FORMAL_CONTRACT_FREEZE",
                    "PRE_EXECUTION_REVIEW",
                }
            ],
            "forbidden_artifacts": [
                "docs/analysis/product-v02321-formal-blocker.json",
                "docs/analysis/product-v02321-knowledge-loop-handoff.json",
                "docs/analysis/product-v02322-diagnosis-replay.json",
                "docs/results/product-v02321-nofault-acceptance.json",
            ],
        }
    )
    payload.pop("manifest_sha256")
    payload["manifest_sha256"] = semantic_sha256_v22(payload)
    fixture_manifest = ProductV02322RepositoryStateManifest.model_validate(payload)

    result = verify_repository_state_v02322(fixture, manifest=fixture_manifest)
    assert result["phase"] == RepositoryPhaseV02322.PRE_FORMAL.value
    assert result["terminal"] == REPOSITORY_STATE_MODEL_PASS_V02322


def test_history_verifier_binds_pr82_and_pr83_without_rewriting_them() -> None:
    result = verify_product_v02322_history(ROOT)

    assert result == {
        "terminal": HISTORY_AND_BLOCKER_PASS_V02322,
        "pr82_terminal": "BLOCKED_ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT",
        "pr83_formal_terminal": (
            "BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE"
        ),
        "pr83_repository_terminal": (
            "BLOCKED_ECOMSRE_PRODUCT_V02321_REPOSITORY_ACCEPTANCE"
        ),
        "predecessor_head": "142dc1094926f18e789ece3668c34918f859b512",
        "formal_traffic_completed": 30,
        "successor_incident_count": 1,
        "successor_diagnosis_count": 0,
        "product_cleanup": "CLEAN",
        "demo_cleanup": "CLEAN",
    }


def test_history_verifier_rejects_resealed_frozen_artifact_substitution(
    tmp_path: Path,
) -> None:
    source = ROOT / "config/product-v02322/historical-results.v1.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["predecessors"]["pr83"]["formal_blocker_semantic_sha256"] = "0" * 64
    body = dict(payload)
    body.pop("manifest_sha256")
    payload["manifest_sha256"] = semantic_sha256_v22(body)
    changed = tmp_path / "historical-results.v1.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="historical manifest differs"):
        verify_product_v02322_history(ROOT, manifest_path=changed)
