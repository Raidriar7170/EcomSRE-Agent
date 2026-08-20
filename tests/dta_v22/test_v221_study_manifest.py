from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.evidence_acquisition_manifest_v221 import (
    load_and_verify_study_manifest_v221,
)
from ecomsre.dta_v2.v22.evidence_acquisition_v221 import StudyCombinationV221
from scripts.ci.verify_dta_v221_study_manifest import (
    verify_dta_v221_study_manifest,
)


ROOT = Path(__file__).resolve().parents[2]


def _binding(root: Path, relative: str) -> dict[str, str]:
    payload = (root / relative).read_bytes()
    return {"path": relative, "sha256": hashlib.sha256(payload).hexdigest()}


def _manifest(root: Path) -> dict[str, object]:
    for relative in (
        "prompt.txt",
        "cases.json",
        "truth.json",
        "policy.py",
        "scorer.py",
        "historical.json",
    ):
        (root / relative).write_text(relative, encoding="utf-8")
    return {
        "schema_version": "dta-v22.1.evidence-acquisition-study-manifest.v1",
        "base_commit": "fceadc924d4909ca1457b35f268429f0272427ce",
        "implementation_commit": "1" * 40,
        "model": "gpt-test",
        "prompt": _binding(root, "prompt.txt"),
        "case_set": _binding(root, "cases.json"),
        "truth_set": _binding(root, "truth.json"),
        "policy_source": _binding(root, "policy.py"),
        "scorer_source": _binding(root, "scorer.py"),
        "historical_results_manifest": _binding(root, "historical.json"),
        "combinations": [item.value for item in StudyCombinationV221],
        "expected_cases": 12,
        "expected_arm_policy_runs": 48,
        "single_execution_rule": "EXACTLY_ONE_FULL_STUDY_EXECUTION",
        "schedule_rule": "DETERMINISTIC_BALANCED_ROTATION_INTERLEAVED_BY_CASE",
        "truth_isolation_rule": "LOAD_ONLY_AFTER_ALL_ARM_POLICY_EXECUTIONS",
    }


def test_study_manifest_binds_every_preregistered_input(tmp_path: Path) -> None:
    payload = _manifest(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = load_and_verify_study_manifest_v221(
        manifest_path=manifest_path,
        repository_root=tmp_path,
        configured_model="gpt-test",
    )

    assert manifest.expected_arm_policy_runs == 48
    assert manifest.combinations == tuple(StudyCombinationV221)


def test_study_manifest_fails_closed_on_model_or_source_drift(tmp_path: Path) -> None:
    payload = _manifest(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="configured Provider model"):
        load_and_verify_study_manifest_v221(
            manifest_path=manifest_path,
            repository_root=tmp_path,
            configured_model="different-model",
        )

    (tmp_path / "scorer.py").write_text("drift", encoding="utf-8")
    with pytest.raises(ValueError, match="study binding drift"):
        load_and_verify_study_manifest_v221(
            manifest_path=manifest_path,
            repository_root=tmp_path,
            configured_model="gpt-test",
        )


def test_study_manifest_rejects_a_second_execution_shape(tmp_path: Path) -> None:
    payload = _manifest(tmp_path)
    payload["single_execution_rule"] = "ALLOW_RERUN"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_and_verify_study_manifest_v221(
            manifest_path=manifest_path,
            repository_root=tmp_path,
            configured_model="gpt-test",
        )


def test_frozen_repository_study_manifest_is_provider_free_and_commit_bound() -> None:
    verified = verify_dta_v221_study_manifest(repository_root=ROOT)

    assert verified["implementation_commit"] == (
        "6988a730763fc08506c8c70c76518e47f90b05e2"
    )
    assert verified["expected_arm_policy_runs"] == 48
