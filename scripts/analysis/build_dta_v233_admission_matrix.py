#!/usr/bin/env python3
"""Build the deterministic, pre-Provider DTA v2.3.3 admission matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.dta_v2.v22.memory import LogCategoryV22, _log_category, _normalize_log
from ecomsre.dta_v2.v23.discovery_runtime_v233 import run_discovery_case_v233
from ecomsre.dta_v2.v23.evaluation import _build_common_context_v23
from ecomsre.dta_v2.v23.evaluation_data_v233 import (
    AdmissionMatrixEntryV233,
    AdmissionMatrixV233,
    EvaluationClassV233,
    load_evaluation_cases_v233,
    load_evaluation_truth_v233,
    load_evaluation_views_v233,
)
from ecomsre.dta_v2.v23.evaluation_v231 import materialize_evaluation_case_v231
from ecomsre.dta_v2.v23.irreconcilable_guard_v233 import (
    IrreconcilableGuardDispositionV233,
)


def _assert_fresh_bytes(root: Path, current_hashes: set[str]) -> None:
    historical_paths = (
        root / "config/dta-v23/evaluation/cases.json",
        root / "config/dta-v231/evaluation/cases.json",
        root / "config/dta-v231-successor/evaluation/cases.json",
        root / "config/dta-v232/evaluation/cases.json",
    )
    historical: set[str] = set()
    for path in historical_paths:
        value = json.loads(path.read_text(encoding="utf-8"))
        historical.update(
            item["source_bytes_sha256"]
            for item in value["cases"]
            if "source_bytes_sha256" in item
        )
    if current_hashes.intersection(historical):
        raise ValueError("v2.3.3 final observer bytes overlap historical sets")


def build(*, repository_root: Path, evaluation_root: Path) -> AdmissionMatrixV233:
    cases = load_evaluation_cases_v233(evaluation_root / "cases.json")
    views = load_evaluation_views_v233(evaluation_root / "ontology-views.json")
    _assert_fresh_bytes(
        repository_root,
        {item.source_bytes_sha256 for item in cases.cases},
    )

    states = {}
    admissions = {}
    for spec in cases.cases:
        view = views.require(spec.case_id)
        case = materialize_evaluation_case_v231(
            repository_root=repository_root,
            spec=spec,
        )
        admissions[spec.case_id] = _build_common_context_v23(
            case=case,
            hidden_mechanism=view.hidden_mechanism,
        ).admission
        states[spec.case_id] = run_discovery_case_v233(
            repository_root=repository_root,
            spec=spec,
            view_spec=view,
            provider_transport=None,
        )

    # Evaluator truth is opened only after every observer-only state is complete.
    truth = load_evaluation_truth_v233(evaluation_root / "truth.json")
    entries: list[AdmissionMatrixEntryV233] = []
    multi_domain = 0
    report_eligible = 0
    log_cluster_cases = sum(
        any(
            _log_category(_normalize_log(log.message)) is not LogCategoryV22.OTHER
            for log in spec.capture.logs
        )
        for spec in cases.cases
    )
    for spec in cases.cases:
        expected = truth.require(spec.case_id)
        state = states[spec.case_id]
        admission = admissions[spec.case_id]
        projection = state.domain_projection
        positive_domains = (
            0
            if projection is None
            else sum(item.score > 0.0 for item in projection.domain_scores)
        )
        domain_candidate_exists = positive_domains >= 1
        strong_witness_exists = bool(
            state.guard_decision is not None
            and state.guard_decision.blocking_witness_ids
        )
        report_support = bool(
            state.provisional_report is not None
            and state.provisional_report.supporting_evidence_refs
        )
        false_irreconcilable = bool(
            expected.evaluation_class is EvaluationClassV233.NOVELTY
            and state.guard_decision is not None
            and state.guard_decision.disposition
            is IrreconcilableGuardDispositionV233.IRRECONCILABLE
        )
        known_count = len(admission.admitted_diagnoses)
        incident_count = known_count + int(state.provisional_report is not None)
        no_incident = admission.no_incident_admissible
        if expected.evaluation_class is EvaluationClassV233.NOVELTY:
            admission_pass = (
                known_count == 0
                and not no_incident
                and domain_candidate_exists
                and not false_irreconcilable
            )
            multi_domain += positive_domains > 1
            report_eligible += report_support
        elif expected.evaluation_class is EvaluationClassV233.REGISTERED_KNOWN:
            admission_pass = known_count == 1
        elif expected.evaluation_class is EvaluationClassV233.NO_INCIDENT:
            admission_pass = no_incident and incident_count == 0
        elif (
            expected.evaluation_class
            is EvaluationClassV233.IRRECONCILABLE_CONTROL
        ):
            admission_pass = (
                known_count == 0 and not no_incident and strong_witness_exists
            )
        else:
            admission_pass = (
                known_count == 0
                and not report_support
                and not strong_witness_exists
            )
        entries.append(
            AdmissionMatrixEntryV233(
                case_id=spec.case_id,
                stratum=expected.stratum,
                known_terminal_count=known_count,
                no_incident=no_incident,
                incident_terminal_count=incident_count,
                domain_candidate_exists=domain_candidate_exists,
                strong_witness_exists=strong_witness_exists,
                strong_report_support=report_support,
                false_irreconcilable_witness=false_irreconcilable,
                admission_pass=admission_pass,
            )
        )
    canonical = tuple(sorted(entries, key=lambda item: item.case_id))
    counts = {
        category: sum(
            truth.require(item.case_id).evaluation_class is category
            and item.admission_pass
            for item in canonical
        )
        for category in EvaluationClassV233
    }
    payload: dict[str, Any] = {
        "schema_version": "dta-v233.admission-matrix.v1",
        "case_count": 28,
        "novelty_passed": counts[EvaluationClassV233.NOVELTY],
        "registered_known_passed": counts[EvaluationClassV233.REGISTERED_KNOWN],
        "no_incident_passed": counts[EvaluationClassV233.NO_INCIDENT],
        "irreconcilable_passed": counts[
            EvaluationClassV233.IRRECONCILABLE_CONTROL
        ],
        "insufficient_passed": counts[EvaluationClassV233.INSUFFICIENT_EVIDENCE],
        "novelty_multi_domain_candidates": multi_domain,
        "novelty_report_eligible": report_eligible,
        "log_error_cluster_cases": log_cluster_cases,
        "counterfactual_target_swaps": sum(
            item.counterfactual_target_role == "TARGET_HIGH"
            for item in truth.truths
        ),
        "entries": canonical,
        "terminal": "DTA_V233_EVALUATION_DATA_PASS",
    }
    draft = AdmissionMatrixV233.model_construct(
        **payload,
        matrix_sha256="0" * 64,
    )
    return AdmissionMatrixV233.model_validate(
        {
            **payload,
            "matrix_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"matrix_sha256"})
            ),
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--evaluation-root",
        type=Path,
        default=Path("config/dta-v233/evaluation"),
    )
    args = parser.parse_args()
    root = args.repository_root.resolve()
    evaluation_root = (root / args.evaluation_root).resolve()
    matrix = build(repository_root=root, evaluation_root=evaluation_root)
    output = evaluation_root / "admission-matrix.json"
    output.write_text(matrix.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(matrix.terminal)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
