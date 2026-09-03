from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyKindV23
import ecomsre.dta_v2.v23.runtime_preflight_v232 as preflight_module
from ecomsre.dta_v2.v23.evaluation_data_v232 import (
    AdmissionMatrixV232,
    load_evaluation_cases_v232,
    load_evaluation_truth_index_v232,
    load_evaluation_views_v232,
)
from ecomsre.dta_v2.v23.evaluation_study_v232 import (
    FixedEvaluationArtifactV232,
    LazyTruthStoreV232,
    MeasuredResultTerminalV232,
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


def test_historical_runtime_totality_preflight_covers_all_48_arms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = (ROOT / "docs/analysis/dta-v232-runtime-totality-preflight.json").read_bytes()
    # The frozen 13-kind result is historical evidence, not a new totality
    # claim for the Product queue successor. Bind its exact unchanged bytes.
    assert hashlib.sha256(raw).hexdigest() == (
        "ce83430b3ea0808f7b3976f86ec920dde8dd126c5d6c5654c1b29ef675a12cac"
    )
    frozen_kinds = tuple(
        GenericAnomalyKindV23(value)
        for value in json.loads(raw)["registered_anomaly_kinds"]
    )
    assert set(GenericAnomalyKindV23) - set(frozen_kinds) == {
        GenericAnomalyKindV23.METRIC_QUEUE_LAG_OUTLIER
    }
    # The current, unmodified runtime gate must reject the historical closed
    # surface. Only this local test context validates its original enum scope;
    # schedule, typed traces, zero-authority checks and digest remain active.
    with pytest.raises(ValueError, match="preflight registry is not enum-total"):
        RuntimeTotalityPreflightV232.model_validate_json(raw)
    with monkeypatch.context() as historical:
        historical.setattr(preflight_module, "GenericAnomalyKindV23", frozen_kinds)
        artifact = RuntimeTotalityPreflightV232.model_validate_json(raw)
        for invalid_kinds in (
            frozen_kinds[1:],
            (*frozen_kinds, GenericAnomalyKindV23.METRIC_QUEUE_LAG_OUTLIER),
        ):
            changed = artifact.model_dump(mode="python")
            changed["registered_anomaly_kinds"] = invalid_kinds
            with pytest.raises(ValueError, match="preflight registry is not enum-total"):
                RuntimeTotalityPreflightV232.model_validate(changed)
    with pytest.raises(ValueError, match="preflight registry is not enum-total"):
        RuntimeTotalityPreflightV232.model_validate_json(raw)

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


def test_fixed_result_is_the_single_mixed_successor_terminal() -> None:
    artifact = FixedEvaluationArtifactV232.model_validate_json(
        (
            ROOT / "docs/results/dta-v232-conflict-aware-evaluation.json"
        ).read_bytes()
    )

    assert artifact.execution_count == 1
    assert artifact.case_count == 24
    assert artifact.run_count == 48
    assert artifact.measured_result_terminal is MeasuredResultTerminalV232.MIXED_RESULT
    assert artifact.runtime_exceptions == 0
    assert artifact.unmapped_anomaly_count == 0
    assert artifact.metrics.action_authority_violations == 0
    assert artifact.agent_writes == 0
    assert artifact.runbook_executions == 0
    assert artifact.docker_calls == 0
    assert artifact.new_live_faults == 0
