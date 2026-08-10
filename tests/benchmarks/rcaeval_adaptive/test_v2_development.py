from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from ecomsre_rcaeval_adaptive.contracts import AdaptiveTerminalStatus
from ecomsre_rcaeval_adaptive.evaluation import BaselineOutcome
from ecomsre_rcaeval_adaptive.v2 import AdaptiveV2Route, StrongSingleIndicatorAction
from ecomsre_rcaeval_adaptive.v2_runner import (
    AdaptiveV2TerminalRecord,
    adaptive_v2_run_id,
)
from ecomsre_rcaeval_v2.dev3_token_accounting import AttemptAccountingSummary
from ecomsre_rcaeval_v2.schedule import CaseIdentity

_SCRIPT_PATH = (
    Path(__file__).parents[3] / "scripts/rcaeval_adaptive/run_v2_development.py"
)
_SPEC = importlib.util.spec_from_file_location("test_run_v2_development", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_DIAGNOSIS_SCRIPT_PATH = (
    Path(__file__).parents[3] / "scripts/analysis/rcaeval_adaptive_v2_gate_diagnosis.py"
)
_DIAGNOSIS_SPEC = importlib.util.spec_from_file_location(
    "test_adaptive_v2_gate_diagnosis", _DIAGNOSIS_SCRIPT_PATH
)
assert _DIAGNOSIS_SPEC is not None and _DIAGNOSIS_SPEC.loader is not None
_DIAGNOSIS_MODULE = importlib.util.module_from_spec(_DIAGNOSIS_SPEC)
_DIAGNOSIS_SPEC.loader.exec_module(_DIAGNOSIS_MODULE)
PROJECT_ROOT = _MODULE.PROJECT_ROOT
_aggregate = _MODULE._aggregate
_gate_passed = _MODULE._gate_passed
_gate_disposition = _MODULE._gate_disposition
_validate_private_run_root = _MODULE._validate_private_run_root
_validate_regression_authorization = _MODULE._validate_regression_authorization
_validate_tune_lineage = _MODULE._validate_tune_lineage
main = _MODULE.main


def test_gate_diagnosis_bins_and_policy_simulation_are_provider_free() -> None:
    rows = []
    for ordinal in range(1, 61):
        risk_count = 2 if ordinal <= 3 else 1 if ordinal <= 20 else 0
        rows.append(
            {
                "initial_root_correct": ordinal > 10,
                "initial_pair_correct": ordinal > 30,
                "confidence": 0.95,
                "metrics_service_rank": 3 if risk_count else 1,
                "metrics_margin": 0.1 if risk_count else 0.9,
                "initial_service_is_metrics_top1": not risk_count,
                "diagnosis_evidence_supports_service": True,
                "logs_oppose": False,
                "propagation_conflict": False,
                "trace_semantics": False,
                "indicator_candidate_available": True,
                "below_direct": False,
                "below_low": False,
                "metrics_conflict": bool(risk_count),
                "metrics_rank_risk": bool(risk_count),
                "metrics_margin_risk": bool(risk_count),
                "evidence_weak": False,
                "indicator_missing": False,
                "risk_count": risk_count,
                "initial_unstable": bool(risk_count),
                "stored_route": "DIRECT_RETURN",
                "stored_gate_reason_codes": ("CONSERVATIVE_DIRECT_DEFAULT",),
            }
        )

    report = _DIAGNOSIS_MODULE.build_diagnosis(
        rows,
        direct_confidence_threshold=0.9,
        low_confidence_threshold=0.75,
        metrics_conflict_rank=3,
        metrics_margin_threshold=0.75,
    )

    assert report["provider_calls"] == 0
    assert report["scope"]["completed_records"] == 60
    assert sum(item["case_count"] for item in report["confidence_bins"]) == 60
    assert sum(item["case_count"] for item in report["metrics_margin_bins"]) == 60
    simulations = report["offline_policy_simulations"]
    assert simulations["policy_a_risk_count_at_least_2"]["escalation_count"] == 3
    assert simulations["policy_b_risk_count_at_least_1"]["escalation_count"] == 20


def test_gate_diagnosis_uses_tracked_policy_and_rejects_public_identifiers(
    tmp_path: Path,
) -> None:
    policy, config_sha256 = _DIAGNOSIS_MODULE._tracked_gate_policy()

    assert policy.metrics_margin_threshold == 0.75
    assert policy.metrics_conflict_rank == 3
    assert len(config_sha256) == 64
    assert _DIAGNOSIS_MODULE._metrics_rank_risk(2, 3) is False
    assert _DIAGNOSIS_MODULE._metrics_rank_risk(3, 3) is True
    alternate = tmp_path / "agent.json"
    alternate.write_text(
        (_DIAGNOSIS_MODULE.AGENT_CONFIG_PATH).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="tracked production"):
        _DIAGNOSIS_MODULE._tracked_gate_policy(alternate)
    with pytest.raises(ValueError, match="forbidden key"):
        _DIAGNOSIS_MODULE.assert_public_payload({"case_id": "private"})


def test_candidate4_metrics_alternative_analysis_is_aggregate_only() -> None:
    rows = []
    for ordinal in range(1, 60):
        wrong = ordinal <= 8
        rows.append(
            {
                "pair_ordinal": ordinal,
                "initial_root_correct": not wrong,
                "initial_service": "initial",
                "true_root_service": "truth" if wrong else "initial",
                "metrics_service_ranking": (
                    (("truth", 4.0), ("initial", 3.0))
                    if wrong
                    else (("initial", 4.0), ("other", 3.0))
                ),
                "logs_visible_services": ("initial",),
                "metrics_margin": 0.25,
                "stored_route": "VERIFY_LOGS" if wrong else "DIRECT_RETURN",
            }
        )

    report, private_rows = _DIAGNOSIS_MODULE.build_metrics_alternative_analysis(rows)

    assert report["provider_calls"] == 0
    assert report["scope"]["wrong_initial_cases"] == 8
    assert report["opportunity_summary"]["alternative_truth_match_count"] == 8
    assert report["decision_support"]["condition_met"] is True
    assert len(private_rows) == 8
    _DIAGNOSIS_MODULE.assert_public_payload(report)
    rows[0]["stored_route"] = None
    with pytest.raises(ValueError, match="invalid route"):
        _DIAGNOSIS_MODULE.build_metrics_alternative_analysis(rows)


def _identity(instance: str, *, fault: str = "cpu") -> CaseIdentity:
    return CaseIdentity.model_validate(
        {
            "system": "RE2-OB",
            "root_cause_service": "checkoutservice",
            "fault": fault,
            "instance": instance,
        }
    )


def _failed_terminal(
    ordinal: int,
    failure_code: str,
    *,
    candidate_id: str = "candidate-1",
    semantic_operations_attempted: int = 0,
    pairwise_calls_attempted: int = 0,
) -> AdaptiveV2TerminalRecord:
    now = datetime.now(timezone.utc)
    return AdaptiveV2TerminalRecord(
        schema_version="rcaeval-single-first-adaptive.terminal.v2",
        evaluation_version="single-first-adaptive-v2",
        candidate_id=candidate_id,
        split="TUNE_SET",
        run_id=f"{ordinal:032x}",
        case_id=f"case-{ordinal}",
        system="RE2-OB",
        status=AdaptiveTerminalStatus.PROVIDER_FAILURE,
        result=None,
        failure_class="ALLOWLISTED_TRANSPORT_TRANSIENT",
        failure_code=failure_code,
        failure_stage="PROVIDER_CALL",
        started_at_utc=now,
        ended_at_utc=now,
        latency_ms=10.0,
        attempt_accounting=AttemptAccountingSummary(
            provider_attempt_count=2,
            retry_attempt_count=1,
            known_token_lower_bound=0,
            unknown_attempt_count=2,
            unknown_reserved_tokens=64000,
            conservative_token_upper_bound=64000,
            orphan_attempt_count=0,
            completed_attempt_usage_coverage_numerator=0,
            completed_attempt_usage_coverage_denominator=0,
            failed_attempt_disposition_coverage_numerator=2,
            failed_attempt_disposition_coverage_denominator=2,
        ),
        policy_lock_sha256="a" * 64,
        semantic_operations_attempted=semantic_operations_attempted,
        pairwise_calls_attempted=pairwise_calls_attempted,
    )


def _completed_terminal(
    ordinal: int,
    *,
    initial_service: str,
    final_service: str,
    initial_indicator: str,
    final_indicator: str,
    route: AdaptiveV2Route,
    fusion_action: str,
    alternative_service: str | None = None,
    pairwise_preference: str | None = None,
    fusion_reason: str = "LEGACY_FUSION",
):
    semantic_operations = {
        AdaptiveV2Route.DIRECT_RETURN: 1,
        AdaptiveV2Route.VERIFY_LOGS: 2,
        AdaptiveV2Route.VERIFY_TRACES: 2,
        AdaptiveV2Route.VERIFY_BOTH: 3,
    }[route]
    diagnosis = SimpleNamespace(
        decision_basis=(
            "METRICS_LOGS_PAIRWISE"
            if alternative_service is not None
            else "LEGACY_RANKED_HYPOTHESES"
        ),
        initial_diagnosis=SimpleNamespace(
            root_cause_service=initial_service,
            root_cause_indicator=initial_indicator,
        ),
        final_root_service=final_service,
        final_indicator=final_indicator,
        gate_decision=SimpleNamespace(route=route),
        fusion_decision=SimpleNamespace(
            action=fusion_action, reason_codes=(fusion_reason,)
        ),
        indicator_resolution=SimpleNamespace(
            action=StrongSingleIndicatorAction.KEEP_STRONG_SINGLE_INDICATOR
        ),
        specialist_hypotheses=(),
        metrics_alternative=(
            None
            if alternative_service is None
            else SimpleNamespace(
                alternative_service=alternative_service,
                alternative_rank=1,
            )
        ),
        logs_pairwise_verification=(
            None
            if pairwise_preference is None
            else SimpleNamespace(
                preference=SimpleNamespace(value=pairwise_preference),
                initial_role=SimpleNamespace(value="PROPAGATED_SYMPTOM"),
                alternative_role=SimpleNamespace(value="ROOT_CANDIDATE"),
            )
        ),
    )
    return SimpleNamespace(
        status=AdaptiveTerminalStatus.COMPLETED,
        result=SimpleNamespace(
            diagnosis=diagnosis,
            semantic_operations=semantic_operations,
        ),
        failure_code=None,
        attempt_accounting=SimpleNamespace(
            provider_attempt_count=semantic_operations,
            retry_attempt_count=0,
            known_token_lower_bound=100,
            conservative_token_upper_bound=100,
        ),
        latency_ms=10.0,
        semantic_operations_attempted=semantic_operations,
        pairwise_calls_attempted=int(pairwise_preference is not None),
    )


def test_candidate5_aggregate_records_pairwise_outcomes_without_service_names() -> None:
    identities = (_identity("1"), _identity("2"))
    baseline = {
        identity: BaselineOutcome(
            identity=identity,
            root_correct=False,
            pair_correct=False,
        )
        for identity in identities
    }
    terminals = (
        _completed_terminal(
            1,
            initial_service="emailservice",
            final_service="checkoutservice",
            initial_indicator="cpu",
            final_indicator="cpu",
            route=AdaptiveV2Route.VERIFY_LOGS,
            fusion_action="OVERRIDE_INITIAL",
            alternative_service="checkoutservice",
            pairwise_preference="ALTERNATIVE",
            fusion_reason="LOGS_PAIRWISE_ALTERNATIVE_OVERRIDE",
        ),
        _completed_terminal(
            2,
            initial_service="checkoutservice",
            final_service="checkoutservice",
            initial_indicator="cpu",
            final_indicator="cpu",
            route=AdaptiveV2Route.VERIFY_LOGS,
            fusion_action="KEEP_INITIAL",
            alternative_service="emailservice",
            pairwise_preference="INITIAL",
            fusion_reason="LOGS_PAIRWISE_INITIAL",
        ),
    )

    aggregate, rows = _aggregate(identities, terminals, baseline)

    assert aggregate["pairwise_calls"] == 2
    assert aggregate["pairwise_preference_distribution"] == {
        "INITIAL": 1,
        "ALTERNATIVE": 1,
        "INCONCLUSIVE": 0,
    }
    assert aggregate["alternative_preference_when_alternative_true_root"] == 1
    assert aggregate["alternative_preference_when_alternative_wrong"] == 0
    assert aggregate["metrics_alternative_rank_distribution"] == {1: 2}
    assert rows[0]["pairwise_initial_role"] == "PROPAGATED_SYMPTOM"
    assert rows[0]["pairwise_alternative_role"] == "ROOT_CANDIDATE"


def test_same_run_root_pair_damage_rescue_and_escalation_are_authoritative() -> None:
    identities = tuple(_identity(str(index)) for index in range(1, 5))
    baseline = {
        identity: BaselineOutcome(
            identity=identity,
            root_correct=index % 2 == 0,
            pair_correct=index == 2,
        )
        for index, identity in enumerate(identities, start=1)
    }
    terminals = (
        _completed_terminal(
            1,
            initial_service="checkoutservice",
            final_service="emailservice",
            initial_indicator="cpu",
            final_indicator="cpu",
            route=AdaptiveV2Route.VERIFY_LOGS,
            fusion_action="OVERRIDE_INITIAL",
        ),
        _completed_terminal(
            2,
            initial_service="emailservice",
            final_service="checkoutservice",
            initial_indicator="cpu",
            final_indicator="cpu",
            route=AdaptiveV2Route.VERIFY_LOGS,
            fusion_action="OVERRIDE_INITIAL",
        ),
        _completed_terminal(
            3,
            initial_service="checkoutservice",
            final_service="checkoutservice",
            initial_indicator="mem",
            final_indicator="cpu",
            route=AdaptiveV2Route.DIRECT_RETURN,
            fusion_action="KEEP_INITIAL",
        ),
        _completed_terminal(
            4,
            initial_service="checkoutservice",
            final_service="checkoutservice",
            initial_indicator="cpu",
            final_indicator="mem",
            route=AdaptiveV2Route.DIRECT_RETURN,
            fusion_action="KEEP_INITIAL",
        ),
    )

    aggregate, rows = _aggregate(identities, terminals, baseline)

    assert aggregate["initial_root_correct"] == 3
    assert aggregate["final_root_correct"] == 3
    assert aggregate["same_run_root_damage"] == 1
    assert aggregate["same_run_root_rescue"] == 1
    assert aggregate["same_run_root_net_rescue"] == 0
    assert aggregate["initial_pair_correct"] == 2
    assert aggregate["final_pair_correct"] == 2
    assert aggregate["same_run_pair_damage"] == 2
    assert aggregate["same_run_pair_rescue"] == 2
    assert aggregate["same_run_pair_net_rescue"] == 0
    assert aggregate["escalation_precision"] == {
        "numerator": 1,
        "denominator": 2,
        "value": 0.5,
    }
    assert aggregate["escalation_recall"] == {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0,
    }
    assert aggregate["initial_correct_escalated"] == 1
    assert aggregate["initial_wrong_direct"] == 0
    assert aggregate["historical_cross_run_comparison"]["classification"] == [
        "CROSS_RUN_CONTEXTUAL_COMPARISON",
        "MODEL_RUN_VARIABILITY_CONFOUNDED",
    ]
    assert rows[0]["initial_pair_correct"] is True
    assert rows[1]["initial_pair_correct"] is False


def test_revised_tune_and_regression_gates_have_synthetic_pass_and_fail() -> None:
    evaluation = _MODULE._load(_MODULE.CONFIG_ROOT / "evaluation.json")
    tune = {
        "scheduled": 60,
        "completed": 60,
        "http_429_terminal_failures": 0,
        "disqualifying_failure_count": 0,
        "final_root_correct": 51,
        "final_pair_correct": 29,
        "same_run_root_damage": 1,
        "same_run_root_rescue": 2,
        "same_run_root_net_rescue": 1,
        "same_run_root_damage_rate": {"value": 1 / 50},
        "same_run_pair_damage": 1,
        "same_run_pair_rescue": 1,
        "same_run_pair_net_rescue": 0,
        "direct_return": 36,
        "mean_semantic_operations": 1.8,
        "trace_routes": 12,
        "wrong_overrides": 1,
        "correct_overrides": 1,
    }
    assert _gate_passed("tune", tune, evaluation) is True
    assert _gate_passed("tune", {**tune, "direct_return": 49}, evaluation) is False
    assert (
        _gate_passed("tune", {**tune, "same_run_root_damage": 3}, evaluation) is False
    )

    regression = {
        **tune,
        "scheduled": 120,
        "completed": 114,
        "final_root_correct": 97,
        "final_pair_correct": 53,
        "direct_return": 72,
        "trace_routes": 24,
        "same_run_root_rescue": 1,
        "same_run_root_damage": 1,
        "same_run_root_net_rescue": 0,
        "same_run_pair_rescue": 1,
        "same_run_pair_damage": 1,
        "same_run_pair_net_rescue": 0,
        "same_run_root_damage_rate": {"value": 0.05},
    }
    assert _gate_passed("regression", regression, evaluation) is True
    assert (
        _gate_passed(
            "regression",
            {**regression, "same_run_root_damage_rate": {"value": 0.051}},
            evaluation,
        )
        is False
    )


def test_evaluation_config_locks_candidate_budget_and_same_run_gates() -> None:
    evaluation = _MODULE._load(_MODULE.CONFIG_ROOT / "evaluation.json")

    assert evaluation["candidate_budget"] == {
        "algorithm_candidate_ids": ["candidate-3", "candidate-4", "candidate-5"],
        "algorithm_candidate_limit": 3,
        "capacity_record_ids": ["candidate-1", "candidate-2"],
        "record_limit": 5,
    }
    assert evaluation["tune_gate"]["direct_return_max"] == 48
    assert evaluation["tune_gate"]["same_run_root_damage_max"] == 2
    assert evaluation["tune_gate"]["same_run_root_net_rescue_min"] == 1
    assert evaluation["regression_gate"]["same_run_root_damage_rate_max"] == 0.05
    assert "src/ecomsre_rcaeval_adaptive/contracts.py" in _MODULE._RUNTIME_SCOPES
    assert "src/ecomsre_rcaeval_adaptive/specialists.py" in _MODULE._RUNTIME_SCOPES
    for required_path in (
        "docs/analysis/rcaeval-adaptive-v2-candidate4-metrics-alternative-analysis.json",
        "docs/analysis/rcaeval-adaptive-v2-candidate4-metrics-alternative-analysis.md",
        "docs/design/rcaeval-adaptive-v2-candidate-5-decision.md",
        "scripts/analysis/rcaeval_adaptive_v2_gate_diagnosis.py",
        "tests/benchmarks/rcaeval_adaptive/test_specialists.py",
        "tests/benchmarks/rcaeval_adaptive/test_v2.py",
        "tests/benchmarks/rcaeval_adaptive/test_v2_development.py",
    ):
        assert required_path in _MODULE._RUNTIME_SCOPES


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_capacity_failures_are_separate_from_algorithm_quality() -> None:
    identities = (_identity("1"), _identity("2", fault="mem"))
    baseline = {
        identities[0]: BaselineOutcome(
            identity=identities[0], root_correct=True, pair_correct=True
        ),
        identities[1]: BaselineOutcome(
            identity=identities[1], root_correct=False, pair_correct=False
        ),
    }

    aggregate, _ = _aggregate(
        identities,
        (_failed_terminal(1, "HTTP_429"), _failed_terminal(2, "TLS_TRANSIENT")),
        baseline,
    )

    assert aggregate["completion_coverage"] == {
        "numerator": 0,
        "denominator": 2,
        "value": 0.0,
    }
    assert aggregate["provider_failure_count"] == 2
    assert aggregate["provider_failure_code_distribution"] == {
        "HTTP_429": 1,
        "TLS_TRANSIENT": 1,
    }
    assert aggregate["algorithm_quality_evaluable"] is False
    assert aggregate["completed_only_root_service_accuracy"]["value"] is None
    assert aggregate["completed_only_pair_accuracy"]["value"] is None
    assert aggregate["mean_semantic_operations_completed_only"] is None
    assert _gate_disposition("tune", aggregate) == "PROVIDER_CAPACITY_BLOCKED"


def test_failed_pairwise_attempt_is_included_in_call_and_cost_aggregates() -> None:
    identity = _identity("1")
    baseline = {
        identity: BaselineOutcome(
            identity=identity,
            root_correct=False,
            pair_correct=False,
        )
    }
    terminal = _failed_terminal(
        1,
        "PROVIDER_OUTPUT_INVALID_SCHEMA",
        candidate_id="candidate-5",
        semantic_operations_attempted=2,
        pairwise_calls_attempted=1,
    )

    aggregate, rows = _aggregate((identity,), (terminal,), baseline)

    assert aggregate["pairwise_calls"] == 1
    assert aggregate["pairwise_completed_verifications"] == 0
    assert aggregate["mean_semantic_operations"] == 2.0
    assert rows[0]["semantic_operations"] == 2
    assert rows[0]["pairwise_call_attempts"] == 1


def test_private_run_root_rejects_git_and_symlink_targets(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside Git"):
        _validate_private_run_root(PROJECT_ROOT / "private-output")

    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        _validate_private_run_root(linked)

    assert (
        _validate_private_run_root(tmp_path / "private-output")
        == (tmp_path / "private-output").resolve()
    )


def test_failed_tune_cannot_reach_regression_schedule_or_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tune_root = tmp_path / "tune"
    tune_result = tune_root / "development-result.json"
    agent_path = PROJECT_ROOT / "config/rcaeval-adaptive-v2/agent.json"
    model_path = PROJECT_ROOT / "config/rcaeval-adaptive-v2/model-lock.json"
    evaluation_path = PROJECT_ROOT / "config/rcaeval-adaptive-v2/evaluation.json"
    implementation_sha = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(
        _MODULE, "_clean_implementation_sha", lambda: implementation_sha
    )
    _write_json(
        tune_root / "candidate-lock.json",
        {
            "schema_version": "rcaeval-single-first-adaptive.candidate-lock.v2",
            "candidate_id": "candidate-1",
            "implementation_git_sha": implementation_sha,
            "agent_config_sha256": hashlib.sha256(agent_path.read_bytes()).hexdigest(),
            "model_lock_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
            "evaluation_config_sha256": hashlib.sha256(
                evaluation_path.read_bytes()
            ).hexdigest(),
            "phase": "TUNE_SET",
        },
    )
    _write_json(
        tune_result,
        {
            "schema_version": "rcaeval-single-first-adaptive.development-result.v2",
            "candidate_id": "candidate-1",
            "phase": "TUNE_SET",
            "aggregate": {
                "gate_passed": False,
                "gate_disposition": "PROVIDER_CAPACITY_BLOCKED",
                "algorithm_quality_evaluable": False,
            },
        },
    )

    with pytest.raises(ValueError, match="passed TUNE result"):
        main(
            (
                "--phase",
                "regression",
                "--candidate-id",
                "candidate-1",
                "--ob-root",
                str(tmp_path / "missing-ob"),
                "--ss-root",
                str(tmp_path / "missing-ss"),
                "--schedule",
                str(tmp_path / "missing-schedule.json"),
                "--env-file",
                str(tmp_path / "missing.env"),
                "--run-root",
                str(tmp_path / "regression"),
                "--reference-terminal-root",
                str(tmp_path / "missing-reference"),
                "--tune-result",
                str(tune_result),
            )
        )


def test_tune_lineage_is_bounded_and_ordered(tmp_path: Path) -> None:
    first = tmp_path / "candidate-1" / "development-result.json"
    _write_json(
        first,
        {
            "schema_version": "rcaeval-single-first-adaptive.development-result.v2",
            "candidate_id": "candidate-1",
            "phase": "TUNE_SET",
            "aggregate": {"scheduled": 60, "gate_passed": False},
        },
    )

    assert _validate_tune_lineage("candidate-2", (first,)) == ("candidate-1",)
    with pytest.raises(ValueError, match="lineage"):
        _validate_tune_lineage("candidate-2", ())
    with pytest.raises(ValueError, match="lineage"):
        _validate_tune_lineage("candidate-1", (first,))
    with pytest.raises(ValueError, match="lineage"):
        _validate_tune_lineage("1", ())

    passed = tmp_path / "passed" / "development-result.json"
    _write_json(
        passed,
        {
            "schema_version": "rcaeval-single-first-adaptive.development-result.v2",
            "candidate_id": "candidate-1",
            "phase": "TUNE_SET",
            "aggregate": {"scheduled": 60, "gate_passed": True},
        },
    )
    with pytest.raises(ValueError, match="already passed"):
        _validate_tune_lineage("candidate-2", (passed,))

    prior_results = [first]
    for ordinal in (2, 3, 4):
        path = tmp_path / f"candidate-{ordinal}" / "development-result.json"
        _write_json(
            path,
            {
                "schema_version": "rcaeval-single-first-adaptive.development-result.v2",
                "candidate_id": f"candidate-{ordinal}",
                "phase": "TUNE_SET",
                "aggregate": {"scheduled": 60, "gate_passed": False},
            },
        )
        prior_results.append(path)
    assert _validate_tune_lineage("candidate-4", tuple(prior_results[:3])) == (
        "candidate-1",
        "candidate-2",
        "candidate-3",
    )
    assert _validate_tune_lineage("candidate-5", tuple(prior_results)) == (
        "candidate-1",
        "candidate-2",
        "candidate-3",
        "candidate-4",
    )
    with pytest.raises(ValueError, match="lineage"):
        _validate_tune_lineage("candidate-6", tuple(prior_results))


def test_candidate_metadata_separates_capacity_and_algorithm_budget() -> None:
    candidate_metadata = getattr(_MODULE, "_candidate_metadata", None)
    assert candidate_metadata is not None
    assert candidate_metadata("candidate-1") == {
        "candidate_kind": "CAPACITY_RECORD",
        "algorithm_candidate_ordinal": None,
        "algorithm_candidate_limit": 3,
    }
    assert candidate_metadata("candidate-2")["candidate_kind"] == "CAPACITY_RECORD"
    assert candidate_metadata("candidate-3")["algorithm_candidate_ordinal"] == 1
    assert candidate_metadata("candidate-4")["algorithm_candidate_ordinal"] == 2
    assert candidate_metadata("candidate-5")["algorithm_candidate_ordinal"] == 3
    with pytest.raises(ValueError, match="candidate"):
        candidate_metadata("candidate-6")


def test_candidate_run_ids_allow_four_and_five_but_reject_six() -> None:
    identity = _identity("1")
    assert len(adaptive_v2_run_id("candidate-4", "TUNE_SET", identity)) == 32
    assert len(adaptive_v2_run_id("candidate-5", "REGRESSION_SET", identity)) == 32
    with pytest.raises(ValueError, match="bounded search"):
        adaptive_v2_run_id("candidate-6", "TUNE_SET", identity)


def test_passed_tune_binding_authorizes_same_runtime(tmp_path: Path) -> None:
    tune_root = tmp_path / "tune"
    tune_result = tune_root / "development-result.json"
    agent_path = PROJECT_ROOT / "config/rcaeval-adaptive-v2/agent.json"
    model_path = PROJECT_ROOT / "config/rcaeval-adaptive-v2/model-lock.json"
    evaluation_path = PROJECT_ROOT / "config/rcaeval-adaptive-v2/evaluation.json"
    implementation_sha = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _write_json(
        tune_root / "candidate-lock.json",
        {
            "schema_version": "rcaeval-single-first-adaptive.candidate-lock.v2",
            "candidate_id": "candidate-1",
            "implementation_git_sha": implementation_sha,
            "agent_config_sha256": hashlib.sha256(agent_path.read_bytes()).hexdigest(),
            "model_lock_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
            "evaluation_config_sha256": hashlib.sha256(
                evaluation_path.read_bytes()
            ).hexdigest(),
            "phase": "TUNE_SET",
        },
    )
    _write_json(
        tune_result,
        {
            "schema_version": "rcaeval-single-first-adaptive.development-result.v2",
            "candidate_id": "candidate-1",
            "phase": "TUNE_SET",
            "aggregate": {
                "scheduled": 60,
                "completed": 60,
                "algorithm_quality_evaluable": True,
                "http_429_terminal_failures": 0,
                "root_service_correct": 51,
                "pair_correct": 29,
                "final_root_correct": 51,
                "final_pair_correct": 29,
                "same_run_root_damage": 0,
                "same_run_root_damage_rate": {
                    "numerator": 0,
                    "denominator": 50,
                    "value": 0.0,
                },
                "same_run_root_rescue": 1,
                "same_run_root_net_rescue": 1,
                "same_run_pair_damage": 0,
                "same_run_pair_rescue": 0,
                "same_run_pair_net_rescue": 0,
                "damage": 0,
                "damage_rate": {"numerator": 0, "denominator": 29, "value": 0.0},
                "rescue": 0,
                "wrong_overrides": 0,
                "correct_overrides": 0,
                "disqualifying_failure_count": 0,
                "direct_return": 36,
                "mean_semantic_operations": 1.8,
                "trace_routes": 12,
                "gate_passed": True,
                "gate_disposition": "PASSED",
            },
        },
    )

    _validate_regression_authorization(
        candidate_id="candidate-1",
        tune_result_path=tune_result,
        current_implementation_sha=implementation_sha,
        agent_config_sha256=hashlib.sha256(agent_path.read_bytes()).hexdigest(),
        model_lock_sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(),
        evaluation_config_sha256=hashlib.sha256(
            evaluation_path.read_bytes()
        ).hexdigest(),
        evaluation=_MODULE._load(evaluation_path),
    )


def test_missing_evaluation_lock_blocks_before_provider_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tune_root = tmp_path / "tune"
    tune_result = tune_root / "development-result.json"
    agent_path = PROJECT_ROOT / "config/rcaeval-adaptive-v2/agent.json"
    model_path = PROJECT_ROOT / "config/rcaeval-adaptive-v2/model-lock.json"
    implementation_sha = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(
        _MODULE, "_clean_implementation_sha", lambda: implementation_sha
    )
    provider_configured = False

    def forbidden_provider_config(_path: Path) -> None:
        nonlocal provider_configured
        provider_configured = True
        raise AssertionError("Provider configuration must remain unreachable")

    monkeypatch.setattr(
        _MODULE, "provider_config_from_env_file", forbidden_provider_config
    )
    _write_json(
        tune_root / "candidate-lock.json",
        {
            "schema_version": "rcaeval-single-first-adaptive.candidate-lock.v2",
            "candidate_id": "candidate-1",
            "implementation_git_sha": implementation_sha,
            "agent_config_sha256": hashlib.sha256(agent_path.read_bytes()).hexdigest(),
            "model_lock_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
            "phase": "TUNE_SET",
        },
    )
    passing = {
        "scheduled": 60,
        "completed": 60,
        "algorithm_quality_evaluable": True,
        "http_429_terminal_failures": 0,
        "final_root_correct": 51,
        "final_pair_correct": 29,
        "same_run_root_damage": 0,
        "same_run_root_damage_rate": {"value": 0.0},
        "same_run_root_rescue": 1,
        "same_run_root_net_rescue": 1,
        "same_run_pair_damage": 0,
        "same_run_pair_rescue": 0,
        "same_run_pair_net_rescue": 0,
        "wrong_overrides": 0,
        "correct_overrides": 0,
        "disqualifying_failure_count": 0,
        "direct_return": 36,
        "mean_semantic_operations": 1.8,
        "trace_routes": 12,
        "gate_passed": True,
        "gate_disposition": "PASSED",
    }
    _write_json(
        tune_result,
        {
            "schema_version": "rcaeval-single-first-adaptive.development-result.v2",
            "candidate_id": "candidate-1",
            "phase": "TUNE_SET",
            "aggregate": passing,
        },
    )

    with pytest.raises(ValueError, match="binding differs"):
        main(
            (
                "--phase",
                "regression",
                "--candidate-id",
                "candidate-1",
                "--ob-root",
                str(tmp_path / "missing-ob"),
                "--ss-root",
                str(tmp_path / "missing-ss"),
                "--schedule",
                str(tmp_path / "missing-schedule.json"),
                "--env-file",
                str(tmp_path / "missing.env"),
                "--run-root",
                str(tmp_path / "regression"),
                "--reference-terminal-root",
                str(tmp_path / "missing-reference"),
                "--tune-result",
                str(tune_result),
            )
        )
    assert provider_configured is False
