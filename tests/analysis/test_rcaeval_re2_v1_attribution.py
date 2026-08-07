from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from ecomsre_rcaeval.artifacts import canonical_json_bytes  # noqa: E402
from ecomsre_rcaeval.contracts import (  # noqa: E402
    Architecture,
    Diagnosis,
    GroundTruth,
    TerminalRecord,
    TerminalStatus,
)
from ecomsre_rcaeval.dataset import TelemetryCase  # noqa: E402
from scripts.analysis.rcaeval_re2_v1_attribution import (  # noqa: E402
    ALLOWED_PUBLIC_PATHS,
    CLASSIFICATION,
    _metric_indicator,
    assert_public_payload,
    build_architecture_decomposition,
    build_case_rows,
    build_pairwise_outcomes,
    build_tool_coverage,
    deterministic_projection,
    ratio,
    validate_allowed_paths,
)


AGGREGATE_PATH = (
    REPO_ROOT / "docs/results/rcaeval-re2-v1-attribution-aggregate.json"
)
SUMMARY_PATH = REPO_ROOT / "docs/results/rcaeval-re2-v1-attribution-summary.md"
DISPOSITION_PATH = (
    REPO_ROOT
    / "docs/review-evidence/rcaeval-re2-v1-attribution/current-disposition.json"
)


def _record(
    *,
    run: int,
    case_id: str,
    architecture: Architecture,
    predicted_service: str | None,
    status: TerminalStatus = TerminalStatus.COMPLETED,
    tool_calls: int = 3,
) -> TerminalRecord:
    diagnosis = None
    failure_code = "provider-operation-failed" if status is not TerminalStatus.COMPLETED else None
    if status is TerminalStatus.COMPLETED:
        assert predicted_service is not None
        diagnosis = Diagnosis(
            root_cause_service=predicted_service,
            root_cause_indicator="cpu",
            evidence_refs=("metric:0001",),
            explanation="Synthetic attribution unit-test diagnosis.",
        )
    return TerminalRecord(
        run_id=f"{run:032x}",
        case_id=case_id,
        architecture=architecture,
        terminal_status=status,
        diagnosis=diagnosis,
        failure_code=failure_code,
        tool_calls=tool_calls,
        model_calls=1 if architecture is Architecture.SINGLE else 4,
        known_provider_tokens=100,
        latency_seconds=1.0,
    )


def _synthetic_records() -> tuple[tuple[TerminalRecord, ...], dict[str, GroundTruth]]:
    truth = {
        case_id: GroundTruth(
            case_id=case_id,
            root_cause_service="checkout",
            fault="cpu",
            instance=str(index),
        )
        for index, case_id in enumerate(("case-a", "case-b"), start=1)
    }
    records = (
        _record(run=1, case_id="case-a", architecture=Architecture.SINGLE, predicted_service="checkout"),
        _record(run=2, case_id="case-a", architecture=Architecture.FIXED, predicted_service="frontend"),
        _record(run=3, case_id="case-a", architecture=Architecture.DYNAMIC, predicted_service="frontend", tool_calls=2),
        _record(run=4, case_id="case-b", architecture=Architecture.SINGLE, predicted_service="checkout"),
        _record(
            run=5,
            case_id="case-b",
            architecture=Architecture.FIXED,
            predicted_service=None,
            status=TerminalStatus.PROVIDER_FAILURE,
        ),
        _record(run=6, case_id="case-b", architecture=Architecture.DYNAMIC, predicted_service="checkout"),
    )
    return records, truth


def test_ratio_preserves_exact_accounting() -> None:
    assert ratio(1, 3) == {"numerator": 1, "denominator": 3, "value": 1 / 3}
    with pytest.raises(ValueError, match="denominator"):
        ratio(0, 0)


def test_architecture_decomposition_reconciles_gap() -> None:
    records, truth = _synthetic_records()
    result = build_architecture_decomposition(records, truth)

    assert result["single"]["completed_only_root_service_accuracy"] == ratio(2, 2)
    assert result["fixed"]["completed_only_root_service_accuracy"] == ratio(0, 1)
    assert result["dynamic"]["completed_only_root_service_accuracy"] == ratio(1, 2)
    assert result["fixed"]["vs_single"] == {
        "excess_terminal_failures": 1,
        "excess_completed_wrong": 1,
        "total_correct_gap": 2,
        "reconciled": True,
    }
    assert result["dynamic"]["vs_single"] == {
        "excess_terminal_failures": 0,
        "excess_completed_wrong": 1,
        "total_correct_gap": 1,
        "reconciled": True,
    }


def test_pairwise_outcomes_cover_all_eight_combinations() -> None:
    records, truth = _synthetic_records()
    result = build_pairwise_outcomes(records, truth)

    assert sum(result["eight_way"].values()) == 2
    assert result["eight_way"]["single_correct__fixed_wrong__dynamic_wrong"] == 1
    assert result["eight_way"]["single_correct__fixed_wrong__dynamic_correct"] == 1
    assert result["simplified"]["single_correct_fixed_wrong"] == 2
    assert result["simplified"]["single_correct_dynamic_wrong"] == 1
    assert result["simplified"]["single_wrong_fixed_correct"] == 0
    assert result["simplified"]["single_wrong_dynamic_correct"] == 0
    assert result["same_source_set_semantic_degradation"]["count"] == 1
    assert result["same_source_set_semantic_degradation"]["denominator"] == 2


def test_fixed_tool_attribution_separates_terminal_failures() -> None:
    records, truth = _synthetic_records()
    source = {
        "status": "AVAILABLE",
        "coverage_at_1": True,
        "coverage_at_3": True,
        "coverage_at_6": True,
        "truth_service_rank": 1,
        "top_1_service": "checkout",
        "top_1_top_2_score_margin": 1.0,
        "unique_service_count": 1,
        "evidence_count": 1,
        "evidence": [],
    }
    projections = {
        case_id: {
            "metrics": dict(source),
            "logs": dict(source),
            "traces": dict(source),
            "indicator": {"raw_present": True, "top6_present": True},
            "tool_calls": 3,
        }
        for case_id in truth
    }
    rows = build_case_rows(records, truth, projections)
    fixed_rows = {
        row["case_id"]: row for row in rows if row["architecture"] == "fixed"
    }
    assert fixed_rows["case-a"]["root_service_failure_attribution"] == (
        "REASONING_OR_FUSION_FAILURE"
    )
    assert fixed_rows["case-b"]["root_service_failure_attribution"] == (
        "TERMINAL_FAILURE"
    )
    result = build_tool_coverage(projections, rows)[
        "fixed_completed_wrong_attribution"
    ]
    assert result["all_root_service_incorrect_runs"] == 2
    assert result["completed_wrong_runs"] == 1
    assert result["terminal_failure_runs"] == 1
    assert result["truth_service_present_in_at_least_one"] == 1
    assert result["visible_but_wrong_rate"] == ratio(1, 1)


def _telemetry_case(tmp_path: Path) -> TelemetryCase:
    metrics = tmp_path / "metrics.csv"
    logs = tmp_path / "logs.csv"
    traces = tmp_path / "traces.csv"
    metrics.write_text(
        "time,checkout_cpu,frontend_mem\n"
        "90,1,2\n"
        "100,8,2\n"
        "110,9,2\n",
        encoding="utf-8",
    )
    logs.write_text(
        "timestamp,service,level,message\n"
        "100,checkout,error,failed request 1\n"
        "101,checkout,error,failed request 2\n"
        "102,frontend,info,normal request\n",
        encoding="utf-8",
    )
    traces.write_text(
        "startTimeMillis,service,duration,error\n"
        "90,checkout,1,0\n"
        "100,checkout,8,1\n"
        "90,frontend,2,0\n"
        "100,frontend,2,0\n",
        encoding="utf-8",
    )
    return TelemetryCase(
        case_id="synthetic-case",
        system="RE2-TT",
        root=tmp_path,
        metrics_path=metrics,
        logs_path=logs,
        traces_path=traces,
        inject_time=100,
    )


def test_deterministic_projection_is_canonical_byte_stable(tmp_path: Path) -> None:
    case = _telemetry_case(tmp_path)
    first = deterministic_projection(case, truth_service="checkout", truth_indicator="cpu")
    second = deterministic_projection(case, truth_service="checkout", truth_indicator="cpu")

    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert first["metrics"]["truth_service_rank"] == 1
    assert first["logs"]["truth_service_rank"] == 1
    assert first["traces"]["truth_service_rank"] == 1
    assert first["indicator"]["raw_present"] is True
    assert first["indicator"]["top6_present"] is True


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("checkout_cpu", "cpu"),
        ("checkout_mem", "mem"),
        ("checkout_diskio", "diskio"),
        ("checkout_latency", "latency"),
        ("checkout_socket", "socket"),
        ("checkout_requests", None),
    ],
)
def test_metric_indicator_uses_frozen_canonical_markers(
    name: str, expected: str | None
) -> None:
    assert _metric_indicator(name) == expected


@pytest.mark.parametrize(
    "forbidden",
    [
        "tt-case-0001",
        "case_id",
        "run_id",
        "instance",
        "scored_cases",
        "ground-truth",
        "evaluator-only",
        "/Users/example/private",
        "/home/example/private",
        "/private/example",
        "Bearer token",
        "Authorization",
        "api_key",
    ],
)
def test_public_payload_rejects_private_markers(forbidden: str) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        assert_public_payload({"value": forbidden})


def test_public_payload_accepts_aggregate_only_contract() -> None:
    assert_public_payload(
        {
            "classification": list(CLASSIFICATION),
            "architecture": {"single": {"correct": 84, "denominator": 90}},
        }
    )


def test_only_five_public_paths_are_allowed() -> None:
    validate_allowed_paths(ALLOWED_PUBLIC_PATHS)
    with pytest.raises(ValueError, match="frozen or undeclared"):
        validate_allowed_paths((*ALLOWED_PUBLIC_PATHS, "src/ecomsre_rcaeval/runner.py"))


def test_live_worktree_has_no_frozen_or_undeclared_changes() -> None:
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "-uall"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    paths = tuple(
        line[3:].split(" -> ")[-1]
        for line in completed.stdout.splitlines()
        if line
    )
    validate_allowed_paths(paths)


def test_analysis_has_no_provider_or_holdout_execution_entrypoint() -> None:
    source = (
        REPO_ROOT / "scripts/analysis/rcaeval_re2_v1_attribution.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "provider_from_lock",
        "execute_scheduled_once",
        "run_schedule(",
        "run_holdout",
        "provider_factory",
    ):
        assert forbidden not in source


def test_checked_aggregate_reconciles_frozen_result() -> None:
    aggregate = json.loads(AGGREGATE_PATH.read_text(encoding="utf-8"))
    assert aggregate["schema_version"] == "rcaeval-re2.post-hoc-attribution.v1"
    assert aggregate["classification"] == list(CLASSIFICATION)
    assert aggregate["source_bindings"] == {
        "implementation_commit": "3a03995037ce410488a4364f8a485b27c80f0ac0",
        "protocol_freeze_sha256": "cb5e31a0a20a3d7a4a2c10c6e2454ca19deb16d6faab01bf83e805b0840f1a2f",
        "terminal_records_lock_sha256": "4eaeb2a1b68413ea6bea86391d8663baf49228484b2a935d2f0256dece321ab0",
        "unblinding_lock_sha256": "19c52b02b07ed63c7592335062acd2cc638c025cd9346e491cbf30c9ee9cbe89",
        "final_report_sha256": "f40be2375ccd80b9cdd831577079043d4370a02924c19073b0ec8cf8b3232155",
    }
    assert aggregate["run_accounting"] == {
        "scheduled_runs": 270,
        "terminal_records": 270,
        "attempt_markers": 270,
        "cases": 90,
        "architecture_arms_per_case": 3,
        "duplicate_runs": 0,
        "provider_calls_during_attribution": 0,
    }
    frozen = aggregate["frozen_result_reconciliation"]
    assert frozen["primary_point_estimate"] == -0.18888888888888888
    assert frozen["primary_ci"] == {
        "lower": -0.28888888888888886,
        "upper": -0.08888888888888889,
    }
    assert frozen["primary_superiority_supported"] is False
    assert frozen["cost_quality_supported"] is False
    assert frozen["unchanged"] is True
    assert aggregate["architecture_decomposition"]["single"]["root_service_accuracy"] == ratio(84, 90)
    assert aggregate["architecture_decomposition"]["fixed"]["root_service_accuracy"] == ratio(67, 90)
    assert aggregate["architecture_decomposition"]["dynamic"]["root_service_accuracy"] == ratio(67, 90)
    assert aggregate["architecture_decomposition"]["single"]["root_cause_pair_accuracy"] == ratio(49, 90)
    assert aggregate["architecture_decomposition"]["fixed"]["root_cause_pair_accuracy"] == ratio(36, 90)
    assert aggregate["architecture_decomposition"]["dynamic"]["root_cause_pair_accuracy"] == ratio(37, 90)
    assert sum(aggregate["pairwise_outcomes"]["eight_way"].values()) == 90
    assert_public_payload(aggregate)


def test_public_report_answers_all_questions_and_declares_review_state() -> None:
    summary = SUMMARY_PATH.read_text(encoding="utf-8")
    disposition = json.loads(DISPOSITION_PATH.read_text(encoding="utf-8"))
    for marker in (
        "POST_HOC_EXPLORATORY",
        "NOT_PRIMARY_INFERENCE",
        "NO_HOLDOUT_RERUN",
        "Q1.",
        "Q2.",
        "Q3.",
        "Q4.",
        "Q5.",
        "Q6.",
        "H1",
        "H9",
        "P0",
        "P3",
        "UNOBSERVABLE_FROM_FROZEN_ARTIFACTS",
    ):
        assert marker in summary
    assert disposition["state"] == "POST_HOC_ATTRIBUTION_REPORT_READY_FOR_HUMAN_REVIEW"
    assert disposition["classification"] == list(CLASSIFICATION)
    assert disposition["merge_authorized"] is False
