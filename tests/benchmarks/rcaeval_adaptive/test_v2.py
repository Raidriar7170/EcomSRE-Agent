from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from ecomsre_rcaeval.contracts import Diagnosis
from ecomsre_rcaeval.dataset import DevSystem, discover_dev_cases
from ecomsre_rcaeval_adaptive.contracts import (
    AdaptiveTerminalStatus,
    CausalRole,
    LogsPairwisePreference,
    LogsPairwiseVerification,
    RankedHypothesis,
)
from ecomsre_rcaeval_adaptive.v2 import (
    AdaptiveV2Route,
    DeterministicFusionPolicy,
    StrongSingleIndicatorAction,
    StrongSingleIndicatorPolicy,
    V2GateInputs,
    V2GatePolicy,
    decide_v2_gate,
    deterministic_fusion,
    deterministic_pairwise_fusion,
    expected_semantic_operations,
    paired_arm_order,
    resolve_strong_single_indicator,
    select_metrics_alternative,
)
from ecomsre_rcaeval_adaptive.v2_runner import (
    _operation_attempt_counts,
    AdaptiveV2CaseResult,
    AdaptiveV2TerminalRecord,
    execute_v2_case,
)
from ecomsre_rcaeval_v2.dev3_token_accounting import AttemptAccountingSummary
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
        ("emailservice", 0.2),
    ),
    logs_oppose: bool = False,
    propagation_conflict: bool = False,
    trace_available: bool = True,
    evidence_supports: bool = True,
    indicator_available: bool = True,
) -> V2GateInputs:
    return V2GateInputs(
        initial_diagnosis=initial,
        metrics_service_ranking=ranking,
        diagnosis_evidence_supports_service=evidence_supports,
        logs_explicitly_oppose_initial=logs_oppose,
        propagation_conflict=propagation_conflict,
        trace_available=trace_available,
        indicator_candidate_available=indicator_available,
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


def _candidate(indicator: str, score: float, rank: int) -> MetricIndicatorCandidate:
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


def test_low_confidence_is_a_route_authoritative_risk_signal() -> None:
    decision = decide_v2_gate(
        _gate_inputs(_diagnosis(confidence=0.8)),
        V2GatePolicy(metrics_margin_threshold=0.1, risk_signal_threshold=1),
    )

    assert decision.route is AdaptiveV2Route.VERIFY_LOGS
    assert decision.risk_signal_count == 1
    assert "LOW_CONFIDENCE" in decision.reason_codes


def test_low_metrics_margin_is_a_route_authoritative_risk_signal() -> None:
    decision = decide_v2_gate(
        _gate_inputs(
            _diagnosis(),
            ranking=(("checkoutservice", 1.0), ("emailservice", 0.8)),
        ),
        V2GatePolicy(metrics_margin_threshold=0.25, risk_signal_threshold=1),
    )

    assert decision.route is AdaptiveV2Route.VERIFY_LOGS
    assert decision.metrics_margin_risk is True
    assert "METRICS_MARGIN_RISK" in decision.reason_codes


def test_metrics_rank_risk_is_route_authoritative() -> None:
    decision = decide_v2_gate(
        _gate_inputs(
            _diagnosis(),
            ranking=(
                ("emailservice", 1.0),
                ("currencyservice", 0.5),
                ("checkoutservice", 0.1),
            ),
        ),
        V2GatePolicy(metrics_margin_threshold=0.1, risk_signal_threshold=1),
    )

    assert decision.route is AdaptiveV2Route.VERIFY_LOGS
    assert decision.metrics_rank_risk is True
    assert "METRICS_RANK_RISK" in decision.reason_codes


def test_two_weak_risks_select_logs_at_threshold_two() -> None:
    decision = decide_v2_gate(
        _gate_inputs(
            _diagnosis(confidence=0.8),
            evidence_supports=False,
        ),
        V2GatePolicy(metrics_margin_threshold=0.1, risk_signal_threshold=2),
    )

    assert decision.route is AdaptiveV2Route.VERIFY_LOGS
    assert decision.risk_signal_count == 2


def test_one_weak_risk_remains_direct_at_threshold_two_but_is_recorded() -> None:
    decision = decide_v2_gate(
        _gate_inputs(_diagnosis(confidence=0.8)),
        V2GatePolicy(metrics_margin_threshold=0.1, risk_signal_threshold=2),
    )

    assert decision.route is AdaptiveV2Route.DIRECT_RETURN
    assert decision.initial_unstable is True
    assert decision.risk_signal_count == 1
    assert "LOW_CONFIDENCE" in decision.reason_codes
    assert "RISK_COUNT_BELOW_ROUTE_THRESHOLD" in decision.reason_codes


def test_initial_unstable_is_not_silently_direct_at_threshold_one() -> None:
    decision = decide_v2_gate(
        _gate_inputs(_diagnosis(), indicator_available=False),
        V2GatePolicy(metrics_margin_threshold=0.1, risk_signal_threshold=1),
    )

    assert decision.initial_unstable is True
    assert decision.route is AdaptiveV2Route.VERIFY_LOGS
    assert "INDICATOR_MISSING" in decision.reason_codes


def test_deterministic_fusion_keeps_without_supported_contradiction() -> None:
    initial = _diagnosis()
    gate = decide_v2_gate(_gate_inputs(initial, logs_oppose=True), V2GatePolicy())

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
    gate = decide_v2_gate(_gate_inputs(initial, logs_oppose=True), V2GatePolicy())

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


def test_candidate4_gate_values_remain_frozen() -> None:
    agent_path = Path(__file__).parents[3] / "config/rcaeval-adaptive-v2/agent.json"
    policy = V2GatePolicy.model_validate(json.loads(agent_path.read_bytes())["gate"])

    assert policy.model_dump() == {
        "direct_confidence_threshold": 0.9,
        "low_confidence_threshold": 0.75,
        "metrics_conflict_rank": 3,
        "metrics_margin_threshold": 0.75,
        "risk_signal_threshold": 1,
    }


@pytest.mark.parametrize(
    ("initial_service", "ranking", "expected_service", "expected_rank"),
    (
        ("a", (("a", 4.0), ("b", 3.0)), "b", 2),
        ("b", (("a", 4.0), ("b", 3.0)), "a", 1),
        ("outside", (("a", 4.0), ("b", 3.0)), "a", 1),
    ),
)
def test_metrics_alternative_is_highest_ranked_non_initial_without_truth(
    initial_service: str,
    ranking: tuple[tuple[str, float], ...],
    expected_service: str,
    expected_rank: int,
) -> None:
    alternative = select_metrics_alternative(initial_service, ranking)

    assert alternative is not None
    assert alternative.alternative_service == expected_service
    assert alternative.alternative_rank == expected_rank
    assert "truth" not in str(select_metrics_alternative.__annotations__).casefold()


def test_metrics_alternative_is_absent_when_only_initial_is_ranked() -> None:
    assert select_metrics_alternative("a", (("a", 4.0),)) is None


def _pairwise(
    preference: LogsPairwisePreference,
    *,
    initial_role: CausalRole = CausalRole.PROPAGATED_SYMPTOM,
    alternative_role: CausalRole = CausalRole.ROOT_CANDIDATE,
    support: tuple[str, ...] = ("log:0002",),
    contradiction: tuple[str, ...] = ("log:0001",),
) -> LogsPairwiseVerification:
    return LogsPairwiseVerification(
        preference=preference,
        initial_role=initial_role,
        alternative_role=alternative_role,
        supporting_evidence_refs=support,
        contradicting_evidence_refs=contradiction,
        confidence=0.9,
        summary="Bounded Logs pairwise result.",
    )


def _pairwise_fusion(
    verification: LogsPairwiseVerification | None,
    *,
    route: AdaptiveV2Route = AdaptiveV2Route.VERIFY_LOGS,
    trace_hypotheses: tuple[RankedHypothesis, ...] = (),
):
    initial = _diagnosis()
    gate = decide_v2_gate(
        _gate_inputs(
            initial,
            ranking=(("emailservice", 1.0), ("checkoutservice", 0.8)),
            logs_oppose=True,
            propagation_conflict=route is AdaptiveV2Route.VERIFY_BOTH,
        ),
        V2GatePolicy(),
    )
    gate = gate.model_copy(update={"route": route})
    alternative = select_metrics_alternative(
        initial.root_cause_service,
        (("emailservice", 1.0), ("checkoutservice", 0.8)),
    )
    return deterministic_pairwise_fusion(
        initial=initial,
        gate=gate,
        metrics_alternative=alternative,
        metrics_service_ranking=(
            ("emailservice", 1.0),
            ("checkoutservice", 0.8),
        ),
        logs_pairwise_verification=verification,
        trace_hypotheses=trace_hypotheses,
        visible_logs_refs=("log:0001", "log:0002"),
        policy=DeterministicFusionPolicy(),
    )


def test_pairwise_alternative_root_and_initial_symptom_overrides() -> None:
    result = _pairwise_fusion(_pairwise(LogsPairwisePreference.ALTERNATIVE))

    assert result.action == "OVERRIDE_INITIAL"
    assert result.final_root_service == "emailservice"
    assert result.reason_codes == ("LOGS_PAIRWISE_ALTERNATIVE_OVERRIDE",)


@pytest.mark.parametrize(
    ("verification", "reason"),
    (
        (
            _pairwise(LogsPairwisePreference.ALTERNATIVE, support=()),
            "LOGS_PAIRWISE_ALT_LACKS_SUPPORT",
        ),
        (
            _pairwise(
                LogsPairwisePreference.ALTERNATIVE,
                initial_role=CausalRole.ROOT_CANDIDATE,
                contradiction=(),
            ),
            "LOGS_PAIRWISE_INITIAL_NOT_CONTRADICTED",
        ),
        (
            _pairwise(LogsPairwisePreference.INITIAL),
            "LOGS_PAIRWISE_INITIAL",
        ),
        (
            _pairwise(LogsPairwisePreference.INCONCLUSIVE),
            "LOGS_PAIRWISE_INCONCLUSIVE",
        ),
    ),
)
def test_pairwise_fusion_keeps_without_complete_authority(
    verification: LogsPairwiseVerification,
    reason: str,
) -> None:
    result = _pairwise_fusion(verification)

    assert result.action == "KEEP_INITIAL"
    assert result.reason_codes == (reason,)


def test_pairwise_both_sources_must_agree_on_metrics_alternative() -> None:
    agreeing_trace = _hypothesis(
        "emailservice", role=CausalRole.ROOT_CANDIDATE, source="traces"
    )
    agreeing = _pairwise_fusion(
        _pairwise(LogsPairwisePreference.ALTERNATIVE),
        route=AdaptiveV2Route.VERIFY_BOTH,
        trace_hypotheses=(agreeing_trace,),
    )
    disagreeing = _pairwise_fusion(
        _pairwise(LogsPairwisePreference.ALTERNATIVE),
        route=AdaptiveV2Route.VERIFY_BOTH,
        trace_hypotheses=(
            _hypothesis(
                "currencyservice", role=CausalRole.ROOT_CANDIDATE, source="traces"
            ),
        ),
    )

    assert agreeing.action == "OVERRIDE_INITIAL"
    assert agreeing.reason_codes == ("BOTH_SOURCES_AGREE_OVERRIDE",)
    assert disagreeing.action == "KEEP_INITIAL"
    assert disagreeing.reason_codes == ("BOTH_SOURCES_DO_NOT_AGREE",)


def test_pairwise_both_rejects_trace_support_below_frozen_threshold() -> None:
    weak_trace = _hypothesis(
        "emailservice",
        role=CausalRole.ROOT_CANDIDATE,
        source="traces",
        score=0.0,
    )

    result = _pairwise_fusion(
        _pairwise(LogsPairwisePreference.ALTERNATIVE),
        route=AdaptiveV2Route.VERIFY_BOTH,
        trace_hypotheses=(weak_trace,),
    )

    assert result.action == "KEEP_INITIAL"
    assert result.reason_codes == ("BOTH_SOURCES_DO_NOT_AGREE",)


def test_pairwise_fusion_keeps_when_gate_is_not_unstable() -> None:
    initial = _diagnosis()
    gate = decide_v2_gate(_gate_inputs(initial), V2GatePolicy()).model_copy(
        update={
            "route": AdaptiveV2Route.VERIFY_LOGS,
            "initial_unstable": False,
        }
    )
    alternative = select_metrics_alternative(
        initial.root_cause_service,
        (("emailservice", 1.0), ("checkoutservice", 0.8)),
    )

    result = deterministic_pairwise_fusion(
        initial=initial,
        gate=gate,
        metrics_alternative=alternative,
        metrics_service_ranking=(
            ("emailservice", 1.0),
            ("checkoutservice", 0.8),
        ),
        logs_pairwise_verification=_pairwise(
            LogsPairwisePreference.ALTERNATIVE
        ),
        trace_hypotheses=(),
        visible_logs_refs=("log:0001", "log:0002"),
        policy=DeterministicFusionPolicy(),
    )

    assert result.action == "KEEP_INITIAL"
    assert result.reason_codes == ("INITIAL_NOT_UNSTABLE",)


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
        from ecomsre_rcaeval_adaptive.contracts import (
            LogsPairwiseInput,
            RankedHypothesisBatch,
        )

        self.calls += 1
        if before_output_validation is not None:
            before_output_validation()
        if isinstance(specialist_input, LogsPairwiseInput):
            return LogsPairwiseVerification(
                preference=LogsPairwisePreference.ALTERNATIVE,
                initial_role=CausalRole.PROPAGATED_SYMPTOM,
                alternative_role=CausalRole.ROOT_CANDIDATE,
                supporting_evidence_refs=(
                    specialist_input.visible_evidence_refs[0],
                ),
                contradicting_evidence_refs=(),
                confidence=0.9,
                summary="Bounded Logs evidence favors the Metrics alternative.",
            )
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
    case = discover_dev_cases(tmp_path / "dataset" / "RE2-OB", DevSystem.RE2_OB)[0]
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
    assert tuple(
        item.source for item in provider.initial_context.source_observations
    ) == (
        "metrics",
        "logs",
        "traces",
    )
    assert result.diagnosis.initial_diagnosis == provider.initial
    assert result.diagnosis.gate_decision.route is AdaptiveV2Route.DIRECT_RETURN
    assert result.semantic_operations == provider.calls == 1
    assert result.tool_calls == 3


def test_candidate5_pairwise_path_replaces_free_logs_generation(
    tmp_path: Path,
) -> None:
    provider = _V2FakeProvider(_diagnosis(service="emailservice"))

    result = execute_v2_case(
        _telemetry_case(tmp_path),
        run_id="d" * 32,
        identity_sha256="e" * 64,
        provider=provider,
        indicator_formula=FormulaId.F0,
        indicator_config=_indicator_config(),
        gate_policy=V2GatePolicy(),
        fusion_policy=DeterministicFusionPolicy(),
        indicator_policy=StrongSingleIndicatorPolicy(),
        candidate_id="candidate-5",
    )

    diagnosis = result.diagnosis
    assert diagnosis.decision_basis == "METRICS_LOGS_PAIRWISE"
    assert diagnosis.gate_decision.route is AdaptiveV2Route.VERIFY_LOGS
    assert diagnosis.metrics_alternative is not None
    assert diagnosis.metrics_alternative.alternative_service == "checkoutservice"
    assert diagnosis.logs_pairwise_verification is not None
    assert diagnosis.specialist_hypotheses == ()
    assert diagnosis.fusion_decision.action == "OVERRIDE_INITIAL"
    assert diagnosis.fusion_decision.reason_codes == (
        "LOGS_PAIRWISE_ALTERNATIVE_OVERRIDE",
    )
    assert result.semantic_operations == provider.calls == 2


def test_candidate5_no_metrics_alternative_keeps_without_logs_call(
    tmp_path: Path,
) -> None:
    provider = _V2FakeProvider(_diagnosis(confidence=0.8))

    result = execute_v2_case(
        _telemetry_case(tmp_path),
        run_id="f" * 32,
        identity_sha256="1" * 64,
        provider=provider,
        indicator_formula=FormulaId.F0,
        indicator_config=_indicator_config(),
        gate_policy=V2GatePolicy(),
        fusion_policy=DeterministicFusionPolicy(),
        indicator_policy=StrongSingleIndicatorPolicy(),
        candidate_id="candidate-5",
    )

    assert result.diagnosis.gate_decision.route is AdaptiveV2Route.VERIFY_LOGS
    assert result.diagnosis.metrics_alternative is None
    assert result.diagnosis.logs_pairwise_verification is None
    assert result.diagnosis.fusion_decision.reason_codes == (
        "NO_METRICS_ALTERNATIVE",
    )
    assert result.semantic_operations == provider.calls == 1


def test_candidate5_attempt_counts_include_failed_pairwise_start(
    tmp_path: Path,
) -> None:
    root = tmp_path / "provider-sidecar" / "semantic-operation-starts"
    root.mkdir(parents=True)
    common = {
        "schema_version": "rcaeval-re2-v2-dev3.semantic-operation-start.v1",
        "started_at_utc": "2026-08-09T00:00:00Z",
        "policy_lock_sha256": "a" * 64,
    }
    (root / "0001.json").write_text(
        json.dumps(
            {
                **common,
                "semantic_operation_index": 1,
                "operation_type": "FINAL_JUDGE",
            }
        ),
        encoding="utf-8",
    )
    (root / "0002.json").write_text(
        json.dumps(
            {
                **common,
                "semantic_operation_index": 2,
                "operation_type": "LOGS_SPECIALIST",
            }
        ),
        encoding="utf-8",
    )

    assert _operation_attempt_counts(
        tmp_path / "provider-sidecar", "candidate-5"
    ) == (2, 1)


def _candidate5_terminal(
    result: AdaptiveV2CaseResult,
    *,
    pairwise_calls_attempted: int,
) -> AdaptiveV2TerminalRecord:
    now = datetime.now(timezone.utc)
    return AdaptiveV2TerminalRecord(
        schema_version="rcaeval-single-first-adaptive.terminal.v2",
        evaluation_version="single-first-adaptive-v2",
        candidate_id="candidate-5",
        split="TUNE_SET",
        run_id=result.run_id,
        case_id=result.case_id,
        system=result.system,
        status=AdaptiveTerminalStatus.COMPLETED,
        result=result,
        started_at_utc=now,
        ended_at_utc=now,
        latency_ms=1.0,
        attempt_accounting=AttemptAccountingSummary(
            provider_attempt_count=result.semantic_operations,
            retry_attempt_count=0,
            known_token_lower_bound=1,
            unknown_attempt_count=0,
            unknown_reserved_tokens=0,
            conservative_token_upper_bound=1,
            orphan_attempt_count=0,
            completed_attempt_usage_coverage_numerator=result.semantic_operations,
            completed_attempt_usage_coverage_denominator=result.semantic_operations,
            failed_attempt_disposition_coverage_numerator=0,
            failed_attempt_disposition_coverage_denominator=0,
        ),
        policy_lock_sha256="a" * 64,
        semantic_operations_attempted=result.semantic_operations,
        pairwise_calls_attempted=pairwise_calls_attempted,
    )


def test_candidate5_completed_terminal_binds_pairwise_attempt_to_diagnosis(
    tmp_path: Path,
) -> None:
    pairwise_result = execute_v2_case(
        _telemetry_case(tmp_path / "pairwise"),
        run_id="2" * 32,
        identity_sha256="3" * 64,
        provider=_V2FakeProvider(_diagnosis(service="emailservice")),
        indicator_formula=FormulaId.F0,
        indicator_config=_indicator_config(),
        gate_policy=V2GatePolicy(),
        fusion_policy=DeterministicFusionPolicy(),
        indicator_policy=StrongSingleIndicatorPolicy(),
        candidate_id="candidate-5",
    )
    no_pairwise_result = execute_v2_case(
        _telemetry_case(tmp_path / "no-pairwise"),
        run_id="4" * 32,
        identity_sha256="5" * 64,
        provider=_V2FakeProvider(_diagnosis(confidence=0.8)),
        indicator_formula=FormulaId.F0,
        indicator_config=_indicator_config(),
        gate_policy=V2GatePolicy(),
        fusion_policy=DeterministicFusionPolicy(),
        indicator_policy=StrongSingleIndicatorPolicy(),
        candidate_id="candidate-5",
    )

    with pytest.raises(ValueError, match="pairwise attempt count differs"):
        _candidate5_terminal(pairwise_result, pairwise_calls_attempted=0)
    with pytest.raises(ValueError, match="pairwise attempt count differs"):
        _candidate5_terminal(no_pairwise_result, pairwise_calls_attempted=1)
