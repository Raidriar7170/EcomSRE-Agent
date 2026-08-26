from pathlib import Path

import pytest

from ecomsre.dta_v2.v23.evaluation_study_v233 import (
    MeasuredResultTerminalV233,
    ProviderSmokeArtifactV233,
    _verify_smoke_manifest_bridge_v233,
    counterbalanced_arm_order_v233,
    load_evaluation_manifest_v233,
    run_deterministic_study_v233,
)
from ecomsre.dta_v2.v23.evaluation_v233 import EvaluationPolicyV233


ROOT = Path(__file__).resolve().parents[2]
EVALUATION_ROOT = ROOT / "config/dta-v233/evaluation"


def test_three_arm_schedule_is_exact_and_counterbalanced() -> None:
    schedules = tuple(counterbalanced_arm_order_v233(index) for index in range(3))

    assert all(set(item) == set(EvaluationPolicyV233) for item in schedules)
    assert tuple(item[0] for item in schedules) == tuple(EvaluationPolicyV233)


def test_deterministic_study_scores_all_28_cases_after_three_arms() -> None:
    artifact = run_deterministic_study_v233(
        repository_root=ROOT,
        evaluation_root=EVALUATION_ROOT,
    )

    assert artifact.case_count == 28
    assert artifact.run_count == 84
    assert len(artifact.comparisons) == 28
    assert artifact.truth_load_count == 28
    assert artifact.metrics.require(
        EvaluationPolicyV233.V233_DOMAIN_BOUND_WITNESS_GUARD
    ).irreconcilable_control_accuracy == 1.0
    assert artifact.metrics.require(
        EvaluationPolicyV233.V233_DOMAIN_BOUND_WITNESS_GUARD
    ).novelty_cases_blocked_by_guard == 0
    assert artifact.measured_result_terminal is MeasuredResultTerminalV233.MIXED_RESULT


def test_provider_smoke_artifact_rejects_wrong_role_denominator() -> None:
    with pytest.raises(ValueError, match="smoke (denominator|role composition)"):
        ProviderSmokeArtifactV233.model_validate(
            {
                "schema_version": "dta-v233.provider-smoke.v1",
                "execution_count": 1,
                "case_count": 12,
                "arm_run_count": 12,
                "manifest_sha256": "0" * 64,
                "runs": (),
                "provider_output_parse_failures": 0,
                "protocol_failures": 0,
                "root_domain_evidence_drift": 0,
                "irreconcilable_provider_calls": 0,
                "action_authority_violations": 0,
                "status": "DTA_V233_PROVIDER_SMOKE_PASS",
                "smoke_sha256": "0" * 64,
            }
        )


def test_zero_provider_addendum_bridges_smoke_to_active_manifest() -> None:
    manifest = load_evaluation_manifest_v233(EVALUATION_ROOT / "manifest.json")
    smoke_path = ROOT / "docs/analysis/dta-v233-provider-smoke.json"
    smoke = ProviderSmokeArtifactV233.model_validate_json(smoke_path.read_bytes())

    bridge_sha = _verify_smoke_manifest_bridge_v233(
        repository_root=ROOT,
        manifest=manifest,
        smoke=smoke,
        provider_smoke_path=smoke_path,
    )

    assert bridge_sha == (
        "684af0819e9d1f926165b68643811408876dbb2ae1af1a421e707a937783f154"
    )
