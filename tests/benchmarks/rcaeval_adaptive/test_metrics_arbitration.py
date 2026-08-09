from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import sys
from typing import Any

import pytest

from ecomsre_rcaeval.contracts import Diagnosis
from ecomsre_rcaeval.dataset import DevSystem, discover_dev_cases
from ecomsre_rcaeval_adaptive.metrics_arbitration import (
    DiagnosisProvenance,
    MetricsArbitrationAction,
    MetricsArbitrationPolicy,
    MetricsServiceRank,
    arbitrate_diagnosis,
    decide_metrics_arbitration,
)
from ecomsre_rcaeval_adaptive.metrics_arbitration_runner import (
    MetricsArbitrationCaseResult,
    MetricsArbitrationTerminalRecord,
    aggregate_metrics_arbitration,
    evaluate_metrics_arbitration_gate,
    execute_metrics_arbitration_case,
    metrics_arbitration_run_id,
    execute_metrics_arbitration_batch,
)
from ecomsre_rcaeval_adaptive.contracts import AdaptiveTerminalStatus
from ecomsre_rcaeval_v2.adapter import dev_case_to_telemetry_case
from ecomsre_rcaeval_v2.indicator import FormulaId, load_indicator_config
from ecomsre_rcaeval_v2.schedule import CaseIdentity
from ecomsre_rcaeval_v2.dev3_token_accounting import AttemptAccountingSummary


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FORMULA_PATH = (
    PROJECT_ROOT / "config/rcaeval-re2-v2-dev/indicator-candidate-formulas.json"
)
CLI_PATH = (
    PROJECT_ROOT / "scripts/rcaeval_adaptive/run_metrics_arbitration.py"
)


def _initial(service: str = "initial-service") -> Diagnosis:
    return Diagnosis(
        root_cause_service=service,
        root_cause_indicator="cpu",
        confidence=0.91,
        evidence_refs=("log:0001",),
        explanation="Bounded Strong Single proposal.",
    )


def _ranking(
    *values: tuple[str, float],
) -> tuple[MetricsServiceRank, ...]:
    return tuple(
        MetricsServiceRank(
            service=service,
            rank=index,
            score=score,
            supporting_metrics_evidence_refs=(f"metric:{index:04d}",),
        )
        for index, (service, score) in enumerate(values, start=1)
    )


@pytest.mark.parametrize(
    ("initial", "ranking", "expected"),
    (
        (
            "absent-service",
            _ranking(("top-service", 1.0), ("second-service", 0.75)),
            MetricsArbitrationAction.OVERRIDE_METRICS_TOP1,
        ),
        (
            "initial-service",
            _ranking(
                ("top-service", 1.0),
                ("second-service", 0.75),
                ("initial-service", 0.5),
            ),
            MetricsArbitrationAction.OVERRIDE_METRICS_TOP1,
        ),
        (
            "initial-service",
            _ranking(("top-service", 1.0), ("initial-service", 0.1)),
            MetricsArbitrationAction.KEEP_INITIAL,
        ),
        (
            "initial-service",
            _ranking(("initial-service", 1.0), ("second-service", 0.0)),
            MetricsArbitrationAction.KEEP_INITIAL,
        ),
        (
            "absent-service",
            _ranking(("top-service", 1.0), ("second-service", 0.750001)),
            MetricsArbitrationAction.KEEP_INITIAL,
        ),
        (
            "absent-service",
            _ranking(("top-service", 1.0), ("second-service", 0.75)),
            MetricsArbitrationAction.OVERRIDE_METRICS_TOP1,
        ),
        (
            "initial-service",
            _ranking(("initial-service", 1.0),),
            MetricsArbitrationAction.KEEP_INITIAL,
        ),
        (
            "absent-service",
            _ranking(("top-service", 1.0),),
            MetricsArbitrationAction.OVERRIDE_METRICS_TOP1,
        ),
    ),
)
def test_m3_exact_boundaries(
    initial: str,
    ranking: tuple[MetricsServiceRank, ...],
    expected: MetricsArbitrationAction,
) -> None:
    decision = decide_metrics_arbitration(
        initial_root_service=initial,
        ranking=ranking,
        policy=MetricsArbitrationPolicy(),
    )

    assert decision.action is expected
    assert decision.normalized_margin == (
        1.0
        if len(ranking) == 1
        else (ranking[0].score - ranking[1].score) / abs(ranking[0].score)
    )


def test_keep_preserves_exact_initial_diagnosis_and_provenance() -> None:
    initial = _initial()
    result = arbitrate_diagnosis(
        initial,
        _ranking(("initial-service", 1.0), ("other-service", 0.1)),
        MetricsArbitrationPolicy(),
    )

    assert result.final_diagnosis == initial
    assert result.final_root_service == initial.root_cause_service
    assert result.final_indicator == initial.root_cause_indicator
    assert result.root_provenance is DiagnosisProvenance.MODEL_INITIAL
    assert result.indicator_provenance is DiagnosisProvenance.MODEL_INITIAL


def test_override_uses_only_metrics_evidence_and_deterministic_provenance() -> None:
    initial = _initial()
    result = arbitrate_diagnosis(
        initial,
        _ranking(("top-service", 1.0), ("second-service", 0.75)),
        MetricsArbitrationPolicy(),
    )

    assert result.final_root_service == "top-service"
    assert result.final_indicator == "cpu"
    assert result.root_provenance is DiagnosisProvenance.DETERMINISTIC_METRICS_M3
    assert result.indicator_provenance is DiagnosisProvenance.MODEL_INITIAL
    assert result.final_diagnosis.evidence_refs == ("metric:0001",)
    assert "log:0001" not in result.final_diagnosis.evidence_refs
    assert result.final_diagnosis.confidence is None
    assert "ground truth" not in result.arbitration_explanation.casefold()

    payload = result.arbitration_decision.model_dump(mode="python")
    payload["reason_codes"] = ("KEEP_INITIAL_TOP1_MATCH",)
    with pytest.raises(ValueError, match="reason codes"):
        type(result.arbitration_decision).model_validate(payload)


def test_m3_rejects_missing_or_malformed_top1_metrics_evidence() -> None:
    with pytest.raises(ValueError, match="Top-1 lacks"):
        decide_metrics_arbitration(
            initial_root_service="initial-service",
            ranking=(
                MetricsServiceRank(
                    service="top-service",
                    rank=1,
                    score=1.0,
                    supporting_metrics_evidence_refs=(),
                ),
            ),
            policy=MetricsArbitrationPolicy(),
        )
    with pytest.raises(ValueError, match="requires Metrics references"):
        MetricsServiceRank(
            service="top-service",
            rank=1,
            score=1.0,
            supporting_metrics_evidence_refs=("metric:not-a-sequence",),
        )


def test_runtime_batch_constructs_only_v1_reference_provider() -> None:
    source = inspect.getsource(execute_metrics_arbitration_batch)

    assert "new_v1_reference_provider" in source
    assert "OpenAICompatibleAdaptiveProvider" not in source
    assert "StrongSingleSpecialistProvider" not in source


class _OneCallProvider:
    def __init__(self, diagnosis: Diagnosis) -> None:
        self._diagnosis = diagnosis
        self.calls = 0
        self.context: Any = None

    def diagnose(self, _incident: object, context: object, _architecture: object) -> Diagnosis:
        self.calls += 1
        self.context = context
        return self._diagnosis


def _telemetry_case(tmp_path: Path):
    root = tmp_path / "dataset/RE2-OB/checkoutservice_cpu/1"
    root.mkdir(parents=True)
    (root / "inject_time.txt").write_text("1000\n", encoding="utf-8")
    (root / "simple_metrics.csv").write_text(
        "time,checkoutservice_cpu,emailservice_cpu\n"
        "400,1,1\n999,1,1\n1000,9,2\n1600,9,2\n",
        encoding="utf-8",
    )
    (root / "logs.csv").write_text(
        "time,service,message,level\n1000,checkoutservice,overload,ERROR\n",
        encoding="utf-8",
    )
    (root / "traces.csv").write_text(
        "time,service,peer,duration,error\n"
        "999,checkoutservice,emailservice,1,0\n"
        "1000,checkoutservice,emailservice,5,1\n",
        encoding="utf-8",
    )
    case = discover_dev_cases(tmp_path / "dataset/RE2-OB", DevSystem.RE2_OB)[0]
    return dev_case_to_telemetry_case(case)


def _indicator_config():
    return load_indicator_config(
        FORMULA_PATH,
        expected_sha256=hashlib.sha256(FORMULA_PATH.read_bytes()).hexdigest(),
    )


def test_runtime_makes_one_initial_call_and_no_specialist_or_fusion(
    tmp_path: Path,
) -> None:
    provider = _OneCallProvider(_initial("emailservice"))

    result = execute_metrics_arbitration_case(
        _telemetry_case(tmp_path),
        run_id="a" * 32,
        identity_sha256="b" * 64,
        provider=provider,
        indicator_formula=FormulaId.F0,
        indicator_config=_indicator_config(),
        policy=MetricsArbitrationPolicy(),
    )

    assert provider.calls == result.semantic_operations == 1
    assert result.tool_calls == 3
    assert len(result.operation_trace) == 1
    assert result.operation_trace[0].role == "INITIAL_DIAGNOSIS"
    assert tuple(item.source for item in provider.context.source_observations) == (
        "metrics",
        "logs",
        "traces",
    )
    tampered = result.model_dump(mode="python")
    tampered["diagnosis"]["arbitration_decision"]["metrics_top1_score"] += 1.0
    with pytest.raises(ValueError, match="ranking differs"):
        MetricsArbitrationCaseResult.model_validate(tampered)

    wrong_run = result.model_copy(update={"run_id": "c" * 32})
    accounting = AttemptAccountingSummary(
        provider_attempt_count=1,
        retry_attempt_count=0,
        known_token_lower_bound=1,
        unknown_attempt_count=0,
        unknown_reserved_tokens=0,
        conservative_token_upper_bound=1,
        orphan_attempt_count=0,
        completed_attempt_usage_coverage_numerator=1,
        completed_attempt_usage_coverage_denominator=1,
        failed_attempt_disposition_coverage_numerator=0,
        failed_attempt_disposition_coverage_denominator=0,
    )
    with pytest.raises(ValueError, match="terminal identity differs"):
        MetricsArbitrationTerminalRecord(
            split="TUNE_SET",
            run_id="a" * 32,
            case_id=result.case_id,
            system=result.system,
            status=AdaptiveTerminalStatus.COMPLETED,
            result=wrong_run,
            started_at_utc=datetime.now(timezone.utc),
            ended_at_utc=datetime.now(timezone.utc),
            latency_ms=1.0,
            attempt_accounting=accounting,
            policy_lock_sha256="d" * 64,
            semantic_operations_attempted=1,
        )


def test_run_id_namespace_is_independent_and_split_bound() -> None:
    identity = CaseIdentity(
        system="RE2-OB",
        root_cause_service="checkoutservice",
        fault="cpu",
        instance="1",
    )

    tune = metrics_arbitration_run_id("TUNE_SET", identity)
    regression = metrics_arbitration_run_id("REGRESSION_SET", identity)

    assert len(tune) == len(regression) == 32
    assert tune != regression


def test_aggregate_and_frozen_gates_cover_smoke_tune_and_regression() -> None:
    tune = {
        "scheduled": 60,
        "terminalized": 60,
        "completed": 58,
        "http_429_terminal_failures": 3,
        "disqualifying_failure_count": 0,
        "final_root_correct": 51,
        "final_pair_correct": 27,
        "same_run_root_damage": 2,
        "same_run_root_rescue": 3,
        "same_run_root_net_rescue": 1,
        "same_run_root_damage_rate": {"value": 2 / 58},
        "same_run_pair_damage": 1,
        "same_run_pair_rescue": 1,
        "same_run_pair_net_rescue": 0,
        "mean_semantic_operations": 1.0,
        "specialist_calls": 0,
        "fusion_model_calls": 0,
    }
    smoke = {
        **tune,
        "scheduled": 12,
        "terminalized": 12,
        "completed": 11,
        "http_429_terminal_failures": 1,
        "semantic_operations": 11,
    }
    regression = {
        **tune,
        "scheduled": 120,
        "terminalized": 120,
        "completed": 114,
        "http_429_terminal_failures": 6,
        "final_root_correct": 97,
        "final_pair_correct": 50,
        "same_run_root_damage": 5,
        "same_run_root_rescue": 5,
        "same_run_root_net_rescue": 0,
        "same_run_root_damage_rate": {"value": 0.05},
    }

    assert evaluate_metrics_arbitration_gate("smoke", smoke) is True
    assert evaluate_metrics_arbitration_gate("tune", tune) is True
    assert evaluate_metrics_arbitration_gate("regression", regression) is True
    assert evaluate_metrics_arbitration_gate(
        "tune", {**tune, "same_run_root_damage": 3}
    ) is False
    assert callable(aggregate_metrics_arbitration)


def test_config_locks_exact_m3_and_one_call() -> None:
    root = PROJECT_ROOT / "config/rcaeval-metrics-arbitration-v1"
    agent = json.loads((root / "agent.json").read_text(encoding="utf-8"))
    evaluation = json.loads((root / "evaluation.json").read_text(encoding="utf-8"))

    assert agent["evaluation_version"] == "metrics-arbitration-v1"
    assert agent["rule"] == "M3"
    assert agent["initial_rank_override_min_exclusive"] == 2
    assert agent["normalized_margin_min"] == 0.25
    assert agent["preserve_initial_indicator"] is True
    assert agent["semantic_model_calls"] == 1
    assert agent["specialists_enabled"] is False
    assert evaluation["tune_gate"]["pair_correct_min"] == 27
    assert evaluation["regression_gate"]["pair_correct_min"] == 50
    assert evaluation["schedule_sha256"] == {
        "smoke": "9ee6f72f0800750ab731d618faee3893d85b0f70475ac08676c233c70ee8206a",
        "tune": "f5bd027a40464d44051b686c32c3a07653e3516c9681b5a10becd4b13b82cd8d",
        "regression": "e5adae294869eceb0d8fdb323afbde2eb6778d771cc798dd36f4f6e8842bbe69",
    }
    cli = _load_cli()
    assert {
        "src/ecomsre",
        "src/ecomsre_rcaeval",
        "src/ecomsre_rcaeval_v2",
        "src/ecomsre_rcaeval_adaptive",
        "config/rcaeval-re2-v2-dev3",
        "docs/analysis/rcaeval-metrics-arbitration-m3-replay.json",
        "scripts/analysis/rcaeval_multiagent_communication_audit.py",
    }.issubset(cli.RUNTIME_SCOPES)


def _load_cli() -> Any:
    spec = importlib.util.spec_from_file_location("metrics_arbitration_cli", CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_fixture_replay_path_constructs_no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    cli = _load_cli()

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("fixture replay constructed a Provider")

    monkeypatch.setattr(cli, "new_v1_reference_provider", forbidden)
    rows = (
        {
            "candidate": "candidate-5",
            "completed": True,
            "private_case_key": "private-case",
            "truth_service": "top-service",
            "truth_indicator": "cpu",
            "initial_service": "initial-service",
            "initial_indicator": "cpu",
            "initial_confidence": 0.9,
            "initial_explanation": "Initial proposal.",
            "initial_evidence_refs": ("log:0001",),
            "metrics_ranking": (("top-service", 1.0), ("second-service", 0.75)),
            "metrics_evidence": (
                {
                    "service": "top-service",
                    "evidence_ref": "metric:0001",
                },
            ),
        },
    )

    aggregate, private_rows = cli.evaluate_fixture_rows(rows)

    assert aggregate["completed"] == 1
    assert aggregate["final_root_correct"] == 1
    assert aggregate["root_rescue"] == 1
    assert aggregate["root_damage"] == 0
    assert len(private_rows) == 1


def test_fixture_replay_artifact_and_private_output_fail_closed(
    tmp_path: Path,
) -> None:
    cli = _load_cli()
    source = PROJECT_ROOT / "docs/analysis/rcaeval-metrics-arbitration-m3-replay.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["provider_calls"] = 1
    tampered = tmp_path / "tampered-replay.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="M3_FIXTURE_REPLAY_MISMATCH"):
        cli._validate_fixture_replay(tampered, require_tracked_path=False)
    with pytest.raises(ValueError, match="outside Git"):
        cli._validate_private_output(
            PROJECT_ROOT / "docs/private-case-truth.jsonl",
            public_outputs=(source,),
        )

    private = tmp_path / "private.jsonl"
    cli._write_jsonl_create_once(private, ({"value": 1},))
    with pytest.raises(ValueError, match="private fixture replay differs"):
        cli._write_jsonl_create_once(private, ({"value": 2},))


def test_config_and_phase_result_tampering_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _load_cli()
    root = PROJECT_ROOT / "config/rcaeval-metrics-arbitration-v1"
    agent = json.loads((root / "agent.json").read_text(encoding="utf-8"))
    evaluation = json.loads((root / "evaluation.json").read_text(encoding="utf-8"))
    model = json.loads((root / "model-lock.json").read_text(encoding="utf-8"))
    drifted = json.loads(json.dumps(agent))
    drifted["pacing"]["minimum_interval_seconds"] = 0.0
    with pytest.raises(ValueError, match="agent lock differs"):
        cli._validate_config_values(drifted, evaluation, model)

    rows = [
        {
            "private_case_key": f"{index:064x}",
            "completed": True,
            "terminal_status": "COMPLETED",
            "failure_code": None,
            "disqualifying_failure": False,
            "initial_root_correct": True,
            "initial_pair_correct": True,
            "final_root_correct": True,
            "final_pair_correct": True,
            "action": "KEEP_INITIAL",
            "reason_codes": ["KEEP_INITIAL_TOP1_MATCH"],
            "rank_condition_passed": False,
            "margin_condition_passed": True,
            "both_conditions_passed": False,
            "initial_metrics_rank_or_none": 1,
            "normalized_margin": 0.5,
            "semantic_operations": 1,
            "provider_attempts": 1,
            "transport_retries": 0,
            "known_token_lower_bound": 1,
            "conservative_token_upper_bound": 1,
            "latency_ms": 1.0,
            "correct_override": False,
            "wrong_override": False,
        }
        for index in range(12)
    ]
    aggregate = cli._aggregate_outcome_rows(rows, scheduled=12)
    assert aggregate["initial_pair_correct"] == 12
    aggregate["gate_passed"] = True
    aggregate["gate_disposition"] = "PASSED"
    lock = cli._config_snapshot()
    monkeypatch.setattr(cli, "_git_runtime_unchanged", lambda *_args: True)
    monkeypatch.setattr(
        cli, "_validate_fixture_replay", lambda *_args, **_kwargs: "f" * 64
    )
    preflight = {
        "schema_version": "rcaeval-metrics-arbitration.preflight.v1",
        "status": "PROVIDER_CAPACITY_PREFLIGHT_PASSED",
        "classification": "SYNTHETIC_NON_CASE_PROVIDER_HEALTH_CALL",
        "implementation_git_sha": "b" * 40,
        "agent_config_sha256": lock["agent_config_sha256"],
        "evaluation_config_sha256": lock["evaluation_config_sha256"],
        "model_lock_sha256": lock["model_lock_sha256"],
        "fixture_replay_sha256": "f" * 64,
        "response_valid": True,
        "usage_known": True,
        "usage_tokens": 100,
        "http_429": 0,
        "schema_error": False,
        "provider_calls": 1,
        "provider_attempts": 1,
        "transport_retries": 0,
    }
    preflight_path = tmp_path / "preflight.json"
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")
    cli._load_bound_preflight(preflight_path, "b" * 40, lock)
    preflight["fixture_replay_sha256"] = "e" * 64
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")
    with pytest.raises(ValueError, match="BLOCKED_PROVIDER_CAPACITY_PREFLIGHT"):
        cli._load_bound_preflight(preflight_path, "b" * 40, lock)

    result = {
        "schema_version": "rcaeval-metrics-arbitration.development-result.v1",
        "evaluation_version": "metrics-arbitration-v1",
        "classification": [
            "CONSUMED_OBSS_DEVELOPMENT_RESULT",
            "NOT_EXTERNAL_VALIDATION",
            "NO_TT_ACCESS",
        ],
        "phase": "smoke",
        "implementation_git_sha": "a" * 40,
        "agent_config_sha256": lock["agent_config_sha256"],
        "evaluation_config_sha256": lock["evaluation_config_sha256"],
        "model_lock_sha256": lock["model_lock_sha256"],
        "fixture_replay_sha256": "f" * 64,
        "preflight_result_sha256": "p" * 64,
        "schedule_sha256": evaluation["schedule_sha256"]["smoke"],
        "aggregate": aggregate,
        "outcomes": rows,
    }
    path = tmp_path / "smoke.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    cli._validate_development_result(
        path,
        expected_phase="smoke",
        implementation_sha="b" * 40,
        lock=lock,
        preflight_result_sha256="p" * 64,
    )

    result["aggregate"]["gate_passed"] = False
    path.write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(ValueError, match="phase Gate differs"):
        cli._validate_development_result(
            path,
            expected_phase="smoke",
            implementation_sha="b" * 40,
            lock=lock,
            preflight_result_sha256="p" * 64,
        )
