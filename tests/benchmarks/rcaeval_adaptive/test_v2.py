from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ecomsre_rcaeval.contracts import Diagnosis
from ecomsre_rcaeval.dataset import DevSystem, discover_dev_cases
from ecomsre_rcaeval_adaptive.contracts import CausalRole, RankedHypothesis
from ecomsre_rcaeval_adaptive.v2 import (
    AdaptiveV2Route,
    DeterministicFusionPolicy,
    StrongSingleIndicatorAction,
    StrongSingleIndicatorPolicy,
    V2GateInputs,
    V2GatePolicy,
    decide_v2_gate,
    deterministic_fusion,
    expected_semantic_operations,
    paired_arm_order,
    resolve_strong_single_indicator,
)
from ecomsre_rcaeval_adaptive.v2_runner import execute_v2_case
from ecomsre_rcaeval_v2.adapter import dev_case_to_telemetry_case
from ecomsre_rcaeval_v2.indicator import (
    FormulaId,
    MetricIndicatorCandidate,
    load_indicator_config,
)


CONFIG_PATH = (
    Path(__file__).parents[3]
    / "config"
    / "rcaeval-re2-v2-dev"
    / "indicator-candidate-formulas.json"
)


def _diagnosis(
    *,
    service: str = "checkoutservice",
    indicator: str = "cpu",
    confidence: float = 0.96,
) -> Diagnosis:
    return Diagnosis.model_validate(
        {
            "root_cause_service": service,
            "root_cause_indicator": indicator,
            "confidence": confidence,
            "evidence_refs": ("metric:0001", "log:0001"),
            "explanation": "Bounded Strong Single diagnosis.",
        }
    )


def _gate_inputs(
    initial: Diagnosis,
    *,
    ranking: tuple[tuple[str, float], ...] = (
        ("checkoutservice", 1.0),
        ("emailservice", 0.7),
    ),
    logs_oppose: bool = False,
    propagation_conflict: bool = False,
    trace_available: bool = True,
) -> V2GateInputs:
    return V2GateInputs(
        initial_diagnosis=initial,
        metrics_service_ranking=ranking,
        diagnosis_evidence_supports_service=True,
        logs_explicitly_oppose_initial=logs_oppose,
        propagation_conflict=propagation_conflict,
        trace_available=trace_available,
        indicator_candidate_available=True,
    )


def _hypothesis(
    service: str,
    *,
    role: CausalRole,
    source: str = "logs",
    score: float = 0.95,
) -> RankedHypothesis:
    return RankedHypothesis.model_validate(
        {
            "service": service,
            "indicator_or_none": "cpu",
            "score": score,
            "causal_role": role,
            "supporting_evidence_refs": ("log:0002",),
            "contradicting_evidence_refs": (),
            "summary": "Bounded verifier result.",
            "source": source,
        }
    )


def _candidate(
    indicator: str, score: float, rank: int
) -> MetricIndicatorCandidate:
    return MetricIndicatorCandidate.model_validate(
        {
            "service": "checkoutservice",
            "canonical_indicator": indicator,
            "metric_name": f"checkoutservice_{indicator}",
            "formula": FormulaId.F0,
            "score": score,
            "score_method": "F0",
            "rank_within_service": rank,
            "rank_global": rank,
            "pre_count": 10,
            "post_count": 10,
            "pre_location": 1.0,
            "post_location": 2.0,
            "pre_scale": 1.0,
            "absolute_shift": 1.0,
            "relative_shift": 1.0,
            "robust_shift": 1.0,
            "persistence": 1.0,
            "evidence_ref": f"indicator:{rank:04d}",
            "config_sha256": "a" * 64,
        }
    )


def test_stable_strong_single_diagnosis_returns_directly() -> None:
    decision = decide_v2_gate(_gate_inputs(_diagnosis()), V2GatePolicy())

    assert decision.route is AdaptiveV2Route.DIRECT_RETURN


def test_metrics_and_logs_conflict_selects_logs_verifier() -> None:
    decision = decide_v2_gate(
        _gate_inputs(
            _diagnosis(),
            ranking=(("emailservice", 1.0), ("currencyservice", 0.8)),
            logs_oppose=True,
        ),
        V2GatePolicy(),
    )

    assert decision.route is AdaptiveV2Route.VERIFY_LOGS


def test_latency_propagation_conflict_selects_trace_verifier() -> None:
    decision = decide_v2_gate(
        _gate_inputs(_diagnosis(indicator="latency"), propagation_conflict=True),
        V2GatePolicy(),
    )

    assert decision.route is AdaptiveV2Route.VERIFY_TRACES


def test_low_confidence_multi_source_conflict_selects_both() -> None:
    decision = decide_v2_gate(
        _gate_inputs(
            _diagnosis(indicator="socket", confidence=0.5),
            ranking=(("emailservice", 1.0), ("currencyservice", 0.8)),
            logs_oppose=True,
            propagation_conflict=True,
        ),
        V2GatePolicy(),
    )

    assert decision.route is AdaptiveV2Route.VERIFY_BOTH


def test_cpu_conflict_never_uses_trace_without_trace_semantics() -> None:
    decision = decide_v2_gate(
        _gate_inputs(
            _diagnosis(indicator="cpu"),
            ranking=(("emailservice", 1.0), ("currencyservice", 0.8)),
            logs_oppose=True,
            propagation_conflict=True,
        ),
        V2GatePolicy(),
    )

    assert decision.route is AdaptiveV2Route.VERIFY_LOGS


def test_deterministic_fusion_keeps_without_supported_contradiction() -> None:
    initial = _diagnosis()
    gate = decide_v2_gate(
        _gate_inputs(initial, logs_oppose=True), V2GatePolicy()
    )

    result = deterministic_fusion(
        initial=initial,
        gate=gate,
        metrics_service_ranking=(("checkoutservice", 1.0), ("emailservice", 0.9)),
        specialist_hypotheses=(
            _hypothesis("emailservice", role=CausalRole.ROOT_CANDIDATE),
        ),
        policy=DeterministicFusionPolicy(),
    )

    assert result.action == "KEEP_INITIAL"
    assert result.final_root_service == "checkoutservice"


def test_deterministic_fusion_allows_one_strong_supported_override() -> None:
    initial = _diagnosis()
    gate = decide_v2_gate(
        _gate_inputs(initial, logs_oppose=True), V2GatePolicy()
    )

    result = deterministic_fusion(
        initial=initial,
        gate=gate,
        metrics_service_ranking=(("checkoutservice", 1.0), ("emailservice", 0.9)),
        specialist_hypotheses=(
            _hypothesis("emailservice", role=CausalRole.ROOT_CANDIDATE),
            _hypothesis("checkoutservice", role=CausalRole.PROPAGATED_SYMPTOM),
        ),
        policy=DeterministicFusionPolicy(),
    )

    assert result.action == "OVERRIDE_INITIAL"
    assert result.final_root_service == "emailservice"


def test_indicator_keeps_strong_single_unless_margin_is_strong() -> None:
    result = resolve_strong_single_indicator(
        final_root_service="checkoutservice",
        initial=_diagnosis(indicator="socket"),
        candidates=(_candidate("cpu", 1.0, 1), _candidate("mem", 0.8, 2)),
        policy=StrongSingleIndicatorPolicy(deterministic_override_margin=0.5),
    )

    assert result.action is StrongSingleIndicatorAction.KEEP_WITH_UNCERTAINTY
    assert result.final_indicator == "socket"


def test_cost_and_future_pair_order_have_no_fusion_call() -> None:
    assert expected_semantic_operations(AdaptiveV2Route.DIRECT_RETURN) == 1
    assert expected_semantic_operations(AdaptiveV2Route.VERIFY_LOGS) == 2
    assert expected_semantic_operations(AdaptiveV2Route.VERIFY_TRACES) == 2
    assert expected_semantic_operations(AdaptiveV2Route.VERIFY_BOTH) == 3
    assert paired_arm_order(4) == (
        ("STRONG_SINGLE", "ADAPTIVE_V2"),
        ("ADAPTIVE_V2", "STRONG_SINGLE"),
        ("STRONG_SINGLE", "ADAPTIVE_V2"),
        ("ADAPTIVE_V2", "STRONG_SINGLE"),
    )


def test_tracked_v2_agent_config_is_strictly_loadable() -> None:
    agent = json.loads(
        (Path(__file__).parents[3] / "config/rcaeval-adaptive-v2/agent.json").read_text(
            encoding="utf-8"
        )
    )

    V2GatePolicy.model_validate(agent["gate"])
    DeterministicFusionPolicy.model_validate(agent["fusion"])
    StrongSingleIndicatorPolicy.model_validate(agent["indicator"])


class _V2FakeProvider:
    def __init__(self, initial: Diagnosis) -> None:
        self.initial = initial
        self.calls = 0
        self.initial_architecture = None
        self.initial_context = None

    def diagnose(self, incident, context, architecture):
        del incident
        self.calls += 1
        self.initial_architecture = architecture
        self.initial_context = context
        return self.initial

    def specialize(self, specialist_input, *, before_output_validation=None):
        from ecomsre_rcaeval_adaptive.contracts import RankedHypothesisBatch

        self.calls += 1
        if before_output_validation is not None:
            before_output_validation()
        source = specialist_input.source
        reference = specialist_input.source_evidence[0].evidence_ref
        return RankedHypothesisBatch(
            source=source,
            hypotheses=(
                RankedHypothesis(
                    service="checkoutservice",
                    indicator_or_none="cpu",
                    score=0.95,
                    causal_role=CausalRole.ROOT_CANDIDATE,
                    supporting_evidence_refs=(reference,),
                    contradicting_evidence_refs=(),
                    summary="Synthetic supported alternative.",
                    source=source,
                ),
                RankedHypothesis(
                    service=self.initial.root_cause_service,
                    indicator_or_none=self.initial.root_cause_indicator,
                    score=0.5,
                    causal_role=CausalRole.PROPAGATED_SYMPTOM,
                    supporting_evidence_refs=(reference,),
                    contradicting_evidence_refs=(),
                    summary="Synthetic propagated symptom.",
                    source=source,
                ),
            ),
        )


def _telemetry_case(tmp_path: Path):
    root = tmp_path / "dataset" / "RE2-OB" / "checkoutservice_cpu" / "1"
    root.mkdir(parents=True)
    (root / "inject_time.txt").write_text("1000\n", encoding="utf-8")
    (root / "simple_metrics.csv").write_text(
        "time,checkoutservice_cpu,checkoutservice_mem\n"
        "400,1,1\n999,1,1\n1000,9,1\n1600,9,1\n",
        encoding="utf-8",
    )
    (root / "logs.csv").write_text(
        "time,service,message,level\n1000,checkoutservice,overload,ERROR\n",
        encoding="utf-8",
    )
    (root / "traces.csv").write_text(
        "time,service,peer,duration,error\n"
        "999,checkoutservice,cartservice,1,0\n"
        "1000,checkoutservice,cartservice,5,1\n",
        encoding="utf-8",
    )
    case = discover_dev_cases(
        tmp_path / "dataset" / "RE2-OB", DevSystem.RE2_OB
    )[0]
    return dev_case_to_telemetry_case(case)


def _indicator_config():
    return load_indicator_config(
        CONFIG_PATH,
        expected_sha256=hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest(),
    )


def test_v2_initial_is_exact_strong_single_call_and_direct_cost(tmp_path: Path) -> None:
    provider = _V2FakeProvider(_diagnosis())

    result = execute_v2_case(
        _telemetry_case(tmp_path),
        run_id="b" * 32,
        identity_sha256="c" * 64,
        provider=provider,
        indicator_formula=FormulaId.F0,
        indicator_config=_indicator_config(),
        gate_policy=V2GatePolicy(),
        fusion_policy=DeterministicFusionPolicy(),
        indicator_policy=StrongSingleIndicatorPolicy(),
    )

    assert provider.initial_architecture.value == "single"
    assert provider.initial_context.architecture.value == "single"
    assert tuple(item.source for item in provider.initial_context.source_observations) == (
        "metrics",
        "logs",
        "traces",
    )
    assert result.diagnosis.initial_diagnosis == provider.initial
    assert result.diagnosis.gate_decision.route is AdaptiveV2Route.DIRECT_RETURN
    assert result.semantic_operations == provider.calls == 1
    assert result.tool_calls == 3
