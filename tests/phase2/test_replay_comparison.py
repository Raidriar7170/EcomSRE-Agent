"""Recomputation checks for the deterministic 7 x 3 comparison report."""

from pathlib import Path

from ecomsre.phase2.contracts import Phase2Variant
from .evaluator_loader import load_phase2_evaluator


comparison = load_phase2_evaluator()
EVALUATION_CASE_IDS = comparison.EVALUATION_CASE_IDS
FROZEN_SINGLE_AGENT_SEMANTIC_SHA256 = (
    comparison.FROZEN_SINGLE_AGENT_SEMANTIC_SHA256
)
run_comparison = comparison.run_comparison


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RATE_METRICS = {
    "Decision Accuracy",
    "Schema Valid Rate",
    "Root Service Accuracy",
    "Fault Mechanism Accuracy",
    "Evidence Reference Validity",
    "Abstention Accuracy",
    "Decoy Resistance",
}
COST_METRICS = {"Average Tool Calls", "Token Usage", "Wall-clock Latency"}
EXPECTED_CEILINGS = {
    Phase2Variant.SINGLE_AGENT.value: {"model_calls": 8, "tool_calls": 8},
    Phase2Variant.FIXED_SPECIALIST_WORKFLOW.value: {
        "model_calls": 5,
        "tool_calls": 4,
    },
    Phase2Variant.DYNAMIC_MULTI_AGENT.value: {
        "model_calls": 8,
        "tool_calls": 5,
    },
}


def test_report_preserves_all_cases_and_recomputable_raw_metrics() -> None:
    report = run_comparison(PROJECT_ROOT)

    assert report["schema_version"] == "phase2.comparison-report.v1"
    assert report["status"] == "COMPLETED"
    assert report["case_order"] == list(EVALUATION_CASE_IDS)
    assert report["baseline_verification"] == {
        "status": "VERIFIED",
        "expected_semantic_sha256": FROZEN_SINGLE_AGENT_SEMANTIC_SHA256,
        "observed_semantic_sha256": FROZEN_SINGLE_AGENT_SEMANTIC_SHA256,
    }
    assert [item["variant"] for item in report["variant_results"]] == [
        variant.value for variant in Phase2Variant
    ]

    for variant_result in report["variant_results"]:
        cases = variant_result["case_results"]
        metrics = variant_result["primary_metrics"]
        assert [item["case_id"] for item in cases] == list(EVALUATION_CASE_IDS)
        assert len(cases) == 7
        assert set(metrics) == RATE_METRICS | COST_METRICS
        assert variant_result["outer_caps"] == {
            "model_calls": 8,
            "tool_calls": 8,
            "total_tokens": 32_000,
        }
        assert variant_result["workflow_call_ceiling"] == EXPECTED_CEILINGS[
            variant_result["variant"]
        ]
        assert variant_result["failed_case_count"] == sum(
            item["status"] == "FAILED" for item in cases
        )

        for name in RATE_METRICS:
            metric = metrics[name]
            assert type(metric["numerator"]) is int
            assert type(metric["denominator"]) is int
            assert 0 <= metric["numerator"] <= metric["denominator"]
        assert metrics["Decision Accuracy"]["denominator"] == 7
        assert metrics["Schema Valid Rate"]["denominator"] == 7
        assert metrics["Evidence Reference Validity"]["denominator"] == 7
        assert metrics["Root Service Accuracy"]["denominator"] == 4
        assert metrics["Fault Mechanism Accuracy"]["denominator"] == 4
        assert metrics["Abstention Accuracy"]["denominator"] == 3
        assert metrics["Decoy Resistance"]["denominator"] == 1

        assert metrics["Decision Accuracy"]["numerator"] == sum(
            bool(item["decision_correct"]) for item in cases
        )
        rate_fields = {
            "Schema Valid Rate": "schema_valid",
            "Root Service Accuracy": "root_service_correct",
            "Fault Mechanism Accuracy": "fault_mechanism_correct",
            "Evidence Reference Validity": "evidence_references_valid",
            "Abstention Accuracy": "abstention_correct",
            "Decoy Resistance": "decoy_resistant",
        }
        for metric_name, field_name in rate_fields.items():
            applicable = [
                item[field_name]
                for item in cases
                if item[field_name] is not None
            ]
            assert metrics[metric_name]["numerator"] == sum(
                bool(value) for value in applicable
            )
            assert metrics[metric_name]["denominator"] == len(applicable)
        assert metrics["Average Tool Calls"]["total"] == sum(
            item["tool_calls"] for item in cases
        )
        assert metrics["Token Usage"]["total"] == sum(
            item["token_usage"] for item in cases
        )
        assert metrics["Wall-clock Latency"]["total_seconds"] == sum(
            item["monotonic_latency_seconds"] for item in cases
        )
        for name in COST_METRICS:
            assert metrics[name]["denominator"] == 7
        assert metrics["Average Tool Calls"]["per_case"] == {
            item["case_id"]: item["tool_calls"] for item in cases
        }
        assert metrics["Token Usage"]["per_case"] == {
            item["case_id"]: item["token_usage"] for item in cases
        }
        assert metrics["Wall-clock Latency"]["per_case"] == {
            item["case_id"]: item["monotonic_latency_seconds"] for item in cases
        }
        budget_metric = variant_result["diagnostic_metrics"]["Budget Compliance"]
        assert budget_metric["denominator"] == 7
        assert budget_metric["numerator"] == sum(
            bool(item["budget_compliant"]) for item in cases
        )
        if variant_result["variant"] == Phase2Variant.SINGLE_AGENT.value:
            assert budget_metric["numerator"] == 7
        else:
            assert all(
                item["tool_audit_count"] == item["tool_calls"]
                for item in cases
            )
            failed = [item for item in cases if item["status"] == "FAILED"]
            assert all(
                item["failure_code"]
                == "TOOL_DISPATCH_FAILED"
                for item in failed
            )


def test_dag_validity_retains_missing_graphs_in_the_denominator() -> None:
    metric = comparison._dag_validity(
        Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
        [{"graph_valid": True}, {"graph_valid": False}],
    )

    assert metric == {"numerator": 1, "denominator": 2, "rate": 0.5}
    assert comparison._dag_validity(
        Phase2Variant.SINGLE_AGENT,
        [{"graph_valid": False}],
    ) == {"numerator": 0, "denominator": 0, "rate": 0.0}


def test_two_comparison_reports_are_exactly_deterministic() -> None:
    first = run_comparison(PROJECT_ROOT)
    second = run_comparison(PROJECT_ROOT)

    assert first == second
    assert first["deterministic_semantic_sha256"] == second[
        "deterministic_semantic_sha256"
    ]
