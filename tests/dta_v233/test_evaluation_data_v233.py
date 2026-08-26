from __future__ import annotations

import json
from pathlib import Path

from ecomsre.dta_v2.v23.evaluation_data_v233 import (
    EvaluationClassV233,
    load_admission_matrix_v233,
    load_evaluation_cases_v233,
    load_evaluation_strata_v233,
    load_evaluation_truth_v233,
    load_evaluation_views_v233,
)
from ecomsre.dta_v2.v23.runtime_preflight_v233 import (
    RuntimePreflightV233,
    run_runtime_preflight_v233,
)


ROOT = Path(__file__).resolve().parents[2]
EVALUATION_ROOT = ROOT / "config/dta-v233/evaluation"


def test_fixed_set_has_new_28_case_bytes_and_opaque_ids() -> None:
    cases = load_evaluation_cases_v233(EVALUATION_ROOT / "cases.json")
    current_hashes = {item.source_bytes_sha256 for item in cases.cases}
    historical_hashes: set[str] = set()
    for path in (
        ROOT / "config/dta-v231/evaluation/cases.json",
        ROOT / "config/dta-v231-successor/evaluation/cases.json",
        ROOT / "config/dta-v232/evaluation/cases.json",
    ):
        value = json.loads(path.read_text(encoding="utf-8"))
        historical_hashes.update(
            item["source_bytes_sha256"] for item in value["cases"]
        )

    assert len(cases.cases) == 28
    assert not current_hashes.intersection(historical_hashes)
    assert all(
        service.startswith("svc-") and len(service) == 14
        for case in cases.cases
        for service in case.candidate_services
    )


def test_truth_views_and_strata_have_exact_composition() -> None:
    truth = load_evaluation_truth_v233(EVALUATION_ROOT / "truth.json")
    views = load_evaluation_views_v233(EVALUATION_ROOT / "ontology-views.json")
    strata = load_evaluation_strata_v233(EVALUATION_ROOT / "strata.json")

    assert len(views.views) == 28
    assert len(strata.strata) == 12
    assert sum(
        item.evaluation_class is EvaluationClassV233.NOVELTY
        for item in truth.truths
    ) == 16
    assert sum(
        item.evaluation_class is EvaluationClassV233.REGISTERED_KNOWN
        for item in truth.truths
    ) == 4
    assert sum(
        item.evaluation_class is EvaluationClassV233.NO_INCIDENT
        for item in truth.truths
    ) == 3
    assert sum(
        item.evaluation_class is EvaluationClassV233.IRRECONCILABLE_CONTROL
        for item in truth.truths
    ) == 4
    assert sum(
        item.evaluation_class is EvaluationClassV233.INSUFFICIENT_EVIDENCE
        for item in truth.truths
    ) == 1


def test_admission_matrix_passes_every_required_stratum() -> None:
    matrix = load_admission_matrix_v233(
        EVALUATION_ROOT / "admission-matrix.json"
    )

    assert matrix.terminal == "DTA_V233_EVALUATION_DATA_PASS"
    assert matrix.novelty_passed == 16
    assert matrix.registered_known_passed == 4
    assert matrix.no_incident_passed == 3
    assert matrix.irreconcilable_passed == 4
    assert matrix.insufficient_passed == 1
    assert matrix.novelty_report_eligible >= 12
    assert matrix.novelty_multi_domain_candidates >= 8
    assert matrix.log_error_cluster_cases >= 6
    assert matrix.counterfactual_target_swaps >= 4
    assert all(item.admission_pass for item in matrix.entries)


def test_84_path_runtime_preflight_is_fresh_and_total() -> None:
    frozen = RuntimePreflightV233.model_validate_json(
        (ROOT / "docs/analysis/dta-v233-runtime-preflight.json").read_bytes()
    )
    fresh = run_runtime_preflight_v233(
        repository_root=ROOT,
        evaluation_root=EVALUATION_ROOT,
    )

    assert fresh.preflight_sha256 == frozen.preflight_sha256
    assert fresh.terminal == "DTA_V233_RUNTIME_PREFLIGHT_PASS"
    assert fresh.deterministic_path_count == 84
    assert fresh.fixed_evaluation_execution_count == 0
    assert fresh.runtime_exceptions == 0
    assert fresh.unmapped_anomaly_kinds == 0
    assert fresh.domain_projection_missing == 0
    assert fresh.provider_mechanical_field_drift == 0
    assert fresh.witness_contract_failures == 0
    assert fresh.premature_truth_access == 0
    assert fresh.action_authority_violations == 0
    assert fresh.agent_writes == 0
