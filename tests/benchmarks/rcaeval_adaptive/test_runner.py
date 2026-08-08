from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

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
    InitialDiagnosisInput,
    InitialFailureCode,
    RankedHypothesis,
    RankedHypothesisBatch,
    UncertaintyFlag,
)
from ecomsre_rcaeval_adaptive.gate import GatePolicy
from ecomsre_rcaeval_adaptive.indicator import IndicatorPolicy
from ecomsre_rcaeval_adaptive.runner import (
    adaptive_run_id,
    execute_adaptive_case,
    execute_adaptive_scheduled_once,
    write_candidate_config_create_once,
)
from ecomsre_rcaeval_adaptive.specialists import InitialOutputValidationError
from ecomsre_rcaeval_v2.contracts import SafeValidationError
from ecomsre_rcaeval_v2.dev3_provider import Dev3ProviderProxy
from ecomsre_rcaeval_v2.schedule import CaseIdentity, case_identity_bytes


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
        self.initial_input: InitialDiagnosisInput | None = None

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
        initial_input,
        *,
        before_output_validation=None,
    ):
        assert isinstance(initial_input, InitialDiagnosisInput)
        self.initial_input = initial_input
        self._charge(before_output_validation)
        evidence_ref = (
            initial_input.indicator_candidates[0].evidence_ref
            if self.cite_candidate
            else initial_input.bounded_evidence[0].evidence_ref
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


class _InitialFailureProvider(_FakeProvider):
    def __init__(self) -> None:
        super().__init__(confidence=0.9)
        self._safe_error = SafeValidationError(
            error_class="ValueError",
            field_paths=("evidence_refs",),
            constraint_types=("visible_evidence_ref",),
            error_count=1,
        )

    @property
    def last_safe_validation_error(self) -> SafeValidationError:
        return self._safe_error

    def diagnose(self, initial_input, *, before_output_validation=None):
        del initial_input, before_output_validation
        raise InitialOutputValidationError(
            InitialFailureCode.INITIAL_EVIDENCE_REF_NOT_VISIBLE,
            self._safe_error,
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
    assert provider.initial_input is not None
    assert provider.initial_input.visible_services == tuple(
        sorted(
            {
                *(item.service for item in provider.initial_input.bounded_evidence),
                *(item.service for item in provider.initial_input.indicator_candidates),
            }
        )
    )
    assert provider.initial_input.visible_evidence_refs == tuple(
        sorted(
            {
                *(item.evidence_ref for item in provider.initial_input.bounded_evidence),
                *(
                    item.evidence_ref
                    for item in provider.initial_input.indicator_candidates
                ),
            }
        )
    )


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


def test_initial_failure_code_reaches_semantic_and_terminal_records(
    tmp_path: Path,
) -> None:
    sidecar = tmp_path / "sidecar"
    provider = Dev3ProviderProxy(
        _InitialFailureProvider(),
        run_root=sidecar,
        policy_lock_sha256="e" * 64,
    )

    terminal = execute_adaptive_scheduled_once(
        case=_case(tmp_path),
        run_id="f" * 32,
        case_identity_sha256="d" * 64,
        candidate_id="candidate-1",
        split="DESIGN",
        provider=provider,  # type: ignore[arg-type]
        indicator_formula=FormulaId.F0,
        indicator_config=_config(),
        gate_policy=GatePolicy(),
        indicator_policy=IndicatorPolicy(),
        terminal_root=tmp_path / "terminals",
        sidecar_root=sidecar,
        policy_lock_sha256="e" * 64,
    )
    semantic = json.loads(
        (sidecar / "semantic-operations/0001.json").read_text(encoding="utf-8")
    )

    assert terminal.status == "INVALID_SCHEMA"
    assert terminal.failure_code == "INITIAL_EVIDENCE_REF_NOT_VISIBLE"
    assert terminal.safe_validation_error == provider.last_safe_validation_error
    assert semantic["failure_code"] == "INITIAL_EVIDENCE_REF_NOT_VISIBLE"
    assert semantic["failure_class"] == "NON_RETRYABLE_SCHEMA"


def test_run_domain_separates_old_smoke_and_validation_ids() -> None:
    identity = CaseIdentity(
        system="RE2-OB",
        root_cause_service="checkoutservice",
        fault="cpu",
        instance="1",
    )
    legacy = hashlib.sha256(
        b"\0".join(
            (
                b"single-first-adaptive-v1",
                b"candidate-1",
                b"DESIGN",
                case_identity_bytes(identity),
            )
        )
    ).hexdigest()[:32]
    domain = "single-first-adaptive-v1-interface-fix-r1"
    design = adaptive_run_id(domain, "candidate-1", "DESIGN", identity)

    assert design != legacy
    assert design == adaptive_run_id(domain, "candidate-1", "DESIGN", identity)
    assert design != adaptive_run_id(
        domain, "candidate-1", "DEV_VALIDATION", identity
    )
    assert design != adaptive_run_id(
        "single-first-adaptive-v1-interface-fix-r2",
        "candidate-1",
        "DESIGN",
        identity,
    )


def test_candidate_config_is_create_once_and_binds_prompts(tmp_path: Path) -> None:
    kwargs = {
        "run_root": tmp_path,
        "candidate_id": "candidate-1",
        "run_domain": "single-first-adaptive-v1-interface-fix-r1",
        "agent_config": {"gate": {"direct_confidence_threshold": 0.75}},
        "indicator_policy": IndicatorPolicy(),
        "implementation_git_sha": "a" * 40,
    }

    first = write_candidate_config_create_once(**kwargs)
    second = write_candidate_config_create_once(**kwargs)

    assert first == second == tmp_path / "candidate-config.json"
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert payload["evaluation_version"] == "single-first-adaptive-v1"
    assert payload["candidate_id"] == "candidate-1"
    assert payload["run_domain"] == "single-first-adaptive-v1-interface-fix-r1"
    assert payload["implementation_git_sha"] == "a" * 40
    assert set(payload["prompt_sha256"]) == {
        "fusion",
        "initial",
        "logs_specialist",
        "traces_specialist",
    }
    assert payload["indicator_policy"] == {
        "deterministic_margin_threshold": 0.6
    }

    with pytest.raises(ValueError, match="candidate config differs"):
        write_candidate_config_create_once(
            **{**kwargs, "agent_config": {"gate": {"direct_confidence_threshold": 0.9}}}
        )


def test_shared_smoke_rejects_non_candidate_one_before_reading_inputs(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        (
            sys.executable,
            "-m",
            "scripts.rcaeval_adaptive.run_adaptive_smoke",
                "--candidate-id",
                "candidate-2",
                "--ob-root",
                str(tmp_path / "missing-ob"),
                "--ss-root",
                str(tmp_path / "missing-ss"),
                "--smoke-schedule",
                str(tmp_path / "missing-schedule.json"),
                "--env-file",
                str(tmp_path / "missing.env"),
                "--run-root",
                str(tmp_path / "missing-run-root"),
        ),
        cwd=project_root,
        env={**os.environ, "PYTHONPATH": "src:."},
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "requires candidate-1" in completed.stderr
    assert "missing-schedule" not in completed.stderr
