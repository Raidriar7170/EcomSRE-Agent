from __future__ import annotations

import inspect
from pathlib import Path

from ecomsre.dta_v2.v23.evaluation_data_v232 import (
    AdmissionMatrixV232,
    load_evaluation_cases_v232,
    load_evaluation_truth_index_v232,
    load_evaluation_views_v232,
)
from ecomsre.dta_v2.v23.evaluation_study_v232 import (
    LazyTruthStoreV232,
    ProviderSmokeArtifactV232,
    run_fixed_evaluation_once_v232,
)
from ecomsre.dta_v2.v23.runtime_preflight_v232 import (
    RuntimeTotalityPreflightV232,
)


ROOT = Path(__file__).resolve().parents[2]
EVALUATION_ROOT = ROOT / "config/dta-v232/evaluation"


def test_new_fixed_set_and_admission_matrix_are_complete() -> None:
    cases = load_evaluation_cases_v232(EVALUATION_ROOT / "cases.json")
    views = load_evaluation_views_v232(EVALUATION_ROOT / "ontology-views.json")
    truth = load_evaluation_truth_index_v232(EVALUATION_ROOT / "truth.json")
    matrix = AdmissionMatrixV232.model_validate_json(
        (EVALUATION_ROOT / "admission-matrix.json").read_bytes()
    )

    expected_ids = tuple(f"vx-{ordinal:03d}" for ordinal in range(201, 225))
    assert tuple(item.case_id for item in cases.cases) == expected_ids
    assert tuple(item.case_id for item in views.views) == expected_ids
    assert tuple(item.case_id for item in truth.shards) == expected_ids
    assert tuple(item.case_id for item in matrix.entries) == expected_ids
    assert matrix.status == "DTA_V232_SUCCESSOR_EVALUATION_DATA_PASS"
    assert matrix.provider_calls == 0
    assert matrix.log_error_cluster_coverage == {
        "novelty": 2,
        "registered_known": 1,
        "irreconcilable": 1,
    }


def test_runtime_totality_preflight_covers_all_48_arms() -> None:
    artifact = RuntimeTotalityPreflightV232.model_validate_json(
        (
            ROOT / "docs/analysis/dta-v232-runtime-totality-preflight.json"
        ).read_bytes()
    )

    assert artifact.status == "DTA_V232_RUNTIME_TOTALITY_PREFLIGHT_PASS"
    assert artifact.arm_run_count == 48
    assert artifact.valid_terminal_or_boundary_count == 48
    assert artifact.runtime_exceptions == 0
    assert artifact.keyerrors == 0
    assert artifact.unmapped_anomaly_kinds == 0
    assert artifact.schema_failures == 0
    assert artifact.provider_calls == 0
    assert artifact.truth_access_before_both_arms == 0
    assert artifact.action_authority_violations == 0


def test_provider_smoke_passes_without_authority_or_protocol_failure() -> None:
    artifact = ProviderSmokeArtifactV232.model_validate_json(
        (ROOT / "docs/analysis/dta-v232-provider-smoke.json").read_bytes()
    )

    assert artifact.status == "DTA_V232_PROVIDER_SMOKE_PASS"
    assert artifact.case_count == 8
    assert artifact.arm_run_count == 8
    assert artifact.provider_output_parse_failures == 0
    assert artifact.protocol_failures == 0
    assert artifact.runner_failures == 0
    assert artifact.log_error_cluster_successful_paths >= 1
    assert artifact.action_authority_violations == 0


def test_truth_shards_load_only_after_both_arms() -> None:
    source = inspect.getsource(LazyTruthStoreV232)
    unlock = source.index("def load_case_after_both_arms")
    load = source.index("load_evaluation_truth_shard_v232(")

    assert unlock < load
    assert "strict.case_id != case_id" in source
    assert "treatment.case_id != case_id" in source


def test_final_runner_has_independent_write_once_boundary() -> None:
    source = inspect.getsource(run_fixed_evaluation_once_v232)

    assert '.local/dta-v232' in source
    assert "fixed-evaluation.started.json" in source
    assert "fixed-evaluation.partial.jsonl" in source
    assert 'with sentinel.open("x"' in source
    assert 'with partial.open("x"' in source
    assert 'with output_path.open("x"' in source
    assert "counterbalanced_arm_order_v232" in source
