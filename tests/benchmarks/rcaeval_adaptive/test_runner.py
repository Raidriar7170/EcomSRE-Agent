from __future__ import annotations

import hashlib
from pathlib import Path

from ecomsre_rcaeval.dataset import DevSystem, discover_dev_cases
from ecomsre_rcaeval_v2.adapter import dev_case_to_telemetry_case
from ecomsre_rcaeval_v2.indicator import FormulaId, load_indicator_config
from ecomsre_rcaeval_v2.provider import UsageCapturingTransport

from ecomsre_rcaeval_adaptive.contracts import (
    CausalRole,
    EscalationRoute,
    FusionAction,
    FusionDecision,
    InitialDiagnosis,
    RankedHypothesis,
    RankedHypothesisBatch,
    UncertaintyFlag,
)
from ecomsre_rcaeval_adaptive.gate import GatePolicy
from ecomsre_rcaeval_adaptive.indicator import IndicatorPolicy
from ecomsre_rcaeval_adaptive.runner import execute_adaptive_case
from ecomsre_rcaeval_adaptive.runner import execute_adaptive_scheduled_once


CONFIG_PATH = (
    Path(__file__).parents[3]
    / "config"
    / "rcaeval-re2-v2-dev"
    / "indicator-candidate-formulas.json"
)


class _UsageTransport:
    def post_json(self, **_kwargs):
        return {
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            }
        }


class _FakeProvider:
    def __init__(
        self,
        *,
        confidence: float,
        indicator: str = "cpu",
        flags: tuple[UncertaintyFlag, ...] = (),
        cite_candidate: bool = False,
    ) -> None:
        self.usage = UsageCapturingTransport(_UsageTransport())
        self.confidence = confidence
        self.indicator = indicator
        self.flags = flags
        self.cite_candidate = cite_candidate

    @property
    def calls(self) -> int:
        return self.usage.snapshot().call_count

    def usage_snapshot(self):
        return self.usage.snapshot()

    def usage_delta_since(self, before):
        return self.usage.delta_since(before)

    def _charge(self, before_output_validation) -> None:
        self.usage.post_json(
            url="https://provider.example/chat/completions",
            headers={"Authorization": "Bearer secret"},
            payload={"model": "locked-model"},
            timeout_seconds=30.0,
        )
        if before_output_validation is not None:
            before_output_validation()

    def diagnose(
        self,
        incident,
        context,
        indicator_candidates,
        *,
        before_output_validation=None,
    ):
        del incident
        self._charge(before_output_validation)
        evidence_ref = (
            indicator_candidates[0].evidence_ref
            if self.cite_candidate
            else context.evidence[0].evidence_id
        )
        return InitialDiagnosis(
            root_cause_service="checkoutservice",
            model_proposed_indicator=self.indicator,
            confidence=self.confidence,
            evidence_refs=(evidence_ref,),
            explanation="Synthetic initial diagnosis.",
            uncertainty_flags=self.flags,
        )

    def specialize(
        self,
        incident,
        context,
        source,
        initial_diagnosis,
        *,
        before_output_validation=None,
    ):
        del incident, initial_diagnosis
        self._charge(before_output_validation)
        prefix = {"logs": "log:", "traces": "trace:"}[source]
        evidence = next(
            item for item in context.evidence if item.evidence_id.startswith(prefix)
        )
        return RankedHypothesisBatch(
            source=source,
            hypotheses=(
                RankedHypothesis(
                    service="checkoutservice",
                    indicator_or_none=self.indicator,
                    score=0.8,
                    causal_role=CausalRole.ROOT_CANDIDATE,
                    supporting_evidence_refs=(evidence.evidence_id,),
                    contradicting_evidence_refs=(),
                    summary="Synthetic specialist hypothesis.",
                    source=source,
                ),
            ),
        )

    def judge(self, fusion_input, *, before_output_validation=None):
        self._charge(before_output_validation)
        return FusionDecision(
            action=FusionAction.KEEP_INITIAL,
            final_root_service=fusion_input.initial_diagnosis.root_cause_service,
            confidence=0.8,
            supporting_evidence_refs=(
                fusion_input.initial_diagnosis.evidence_refs[0],
            ),
            contradicting_evidence_refs=(),
            reason_codes=("DEFAULT_KEEP",),
        )


def _config():
    return load_indicator_config(
        CONFIG_PATH, expected_sha256=hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()
    )


def _case(tmp_path: Path):
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


def _run(tmp_path: Path, provider: _FakeProvider):
    return execute_adaptive_case(
        _case(tmp_path),
        run_id="a" * 32,
        case_identity_sha256="b" * 64,
        provider=provider,
        indicator_formula=FormulaId.F0,
        indicator_config=_config(),
        gate_policy=GatePolicy(),
        indicator_policy=IndicatorPolicy(),
    )


def test_direct_path_uses_one_model_call_and_two_tools(tmp_path: Path) -> None:
    provider = _FakeProvider(confidence=0.9)
    result = _run(tmp_path, provider)

    assert result.diagnosis.escalation_decision.route is EscalationRoute.DIRECT_RETURN
    assert result.tool_calls == 2
    assert result.semantic_operations == 1
    assert provider.calls == 1


def test_metric_candidate_reference_counts_as_initial_service_support(
    tmp_path: Path,
) -> None:
    provider = _FakeProvider(confidence=0.9, cite_candidate=True)

    result = _run(tmp_path, provider)

    assert result.diagnosis.escalation_decision.route is EscalationRoute.DIRECT_RETURN
    assert result.semantic_operations == 1


def test_logs_path_uses_three_model_calls_and_two_tools(tmp_path: Path) -> None:
    provider = _FakeProvider(confidence=0.7)
    result = _run(tmp_path, provider)

    assert result.diagnosis.escalation_decision.route is EscalationRoute.ESCALATE_LOGS
    assert result.tool_calls == 2
    assert result.semantic_operations == 3
    assert provider.calls == 3


def test_trace_path_uses_three_model_calls_and_three_tools(tmp_path: Path) -> None:
    provider = _FakeProvider(
        confidence=0.7,
        indicator="latency",
        flags=(UncertaintyFlag.NETWORK_OR_TRACE_AMBIGUITY,),
    )
    result = _run(tmp_path, provider)

    assert result.diagnosis.escalation_decision.route is EscalationRoute.ESCALATE_TRACES
    assert result.tool_calls == 3
    assert result.semantic_operations == 3
    assert provider.calls == 3


def test_both_path_uses_four_model_calls_and_three_tools(tmp_path: Path) -> None:
    provider = _FakeProvider(confidence=0.3)
    result = _run(tmp_path, provider)

    assert result.diagnosis.escalation_decision.route is EscalationRoute.ESCALATE_BOTH
    assert result.tool_calls == 3
    assert result.semantic_operations == 4
    assert provider.calls == 4


def test_scheduled_execution_reuses_create_once_terminal_without_provider_replay(
    tmp_path: Path,
) -> None:
    provider = _FakeProvider(confidence=0.9)
    kwargs = {
        "case": _case(tmp_path),
        "run_id": "c" * 32,
        "case_identity_sha256": "d" * 64,
        "candidate_id": "candidate-1",
        "split": "DESIGN",
        "provider": provider,
        "indicator_formula": FormulaId.F0,
        "indicator_config": _config(),
        "gate_policy": GatePolicy(),
        "indicator_policy": IndicatorPolicy(),
        "terminal_root": tmp_path / "terminals",
        "sidecar_root": tmp_path / "sidecar",
        "policy_lock_sha256": "e" * 64,
    }

    first = execute_adaptive_scheduled_once(**kwargs)
    calls_after_first = provider.calls
    second = execute_adaptive_scheduled_once(**kwargs)

    assert first == second
    assert second.status == "COMPLETED"
    assert provider.calls == calls_after_first == 1
    assert len(list((tmp_path / "terminals").glob("*.json"))) == 1
