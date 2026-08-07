from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ecomsre_rcaeval.contracts import CommanderDecision, SpecialistAssessment
from ecomsre_rcaeval.dataset import DevSystem, discover_dev_cases
from ecomsre_rcaeval_v2.adapter import dev_case_to_telemetry_case
from ecomsre_rcaeval_v2.contracts import (
    JudgeServiceDecisionV2,
    OperationStatus,
    OperationType,
)
from ecomsre_rcaeval_v2.indicator import FormulaId, load_indicator_config
from ecomsre_rcaeval_v2.provider import (
    ProviderCallDelta,
    ProviderCounterSnapshot,
    UsageCapturingTransport,
)
from ecomsre_rcaeval_v2.runner import execute_v2_scheduled_once
from ecomsre_rcaeval_v2.schedule import (
    CaseIdentity,
    ScheduleRecord,
    SplitName,
    Variant,
)


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


class _StaticObservableProvider:
    def __init__(self, *, fail_operation: OperationType | None = None) -> None:
        self.usage = UsageCapturingTransport(_UsageTransport())
        self.fail_operation = fail_operation

    @property
    def calls(self) -> int:
        return self.usage.snapshot().call_count

    def usage_snapshot(self) -> ProviderCounterSnapshot:
        return self.usage.snapshot()

    def usage_delta_since(
        self, before: ProviderCounterSnapshot
    ) -> ProviderCallDelta:
        return self.usage.delta_since(before)

    def _charge(self, operation_type: OperationType) -> None:
        self.usage.post_json(
            url="https://provider.example/v1/chat/completions",
            headers={"Authorization": "Bearer secret"},
            payload={"model": "locked-model"},
            timeout_seconds=30.0,
        )
        if self.fail_operation is operation_type:
            raise ConnectionError("synthetic provider failure")

    def specialize(self, incident, context, source):
        del incident
        operation_type = {
            "metrics": OperationType.METRICS_SPECIALIST,
            "logs": OperationType.LOGS_SPECIALIST,
            "traces": OperationType.TRACES_SPECIALIST,
        }[source]
        self._charge(operation_type)
        evidence = next(
            item
            for item in context.evidence
            if item.evidence_id.startswith(
                {"metrics": "metric:", "logs": "log:", "traces": "trace:"}[
                    source
                ]
            )
        )
        return SpecialistAssessment(
            source=source,
            observation_status="AVAILABLE",
            candidate_service=evidence.service,
            candidate_indicator="cpu",
            confidence=0.8,
            evidence_refs=(evidence.evidence_id,),
            summary="Synthetic source-isolated assessment.",
        )

    def plan_followup(self, incident, context, metrics_assessment):
        del incident, context, metrics_assessment
        self._charge(OperationType.COMMANDER)
        return CommanderDecision(
            selected_sources=("logs",),
            rationale="Inspect Logs after the Metrics assessment.",
        )

    def judge(self, judge_input, architecture):
        del architecture
        self._charge(OperationType.FINAL_JUDGE)
        return JudgeServiceDecisionV2(
            root_cause_service="checkoutservice",
            model_proposed_indicator="mem",
            confidence=0.9,
            evidence_refs=(judge_input.bounded_evidence[0].evidence_ref,),
            explanation="Synthetic bounded evidence selects checkoutservice.",
        )


def _config():
    digest = hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()
    return load_indicator_config(CONFIG_PATH, expected_sha256=digest)


def _case(tmp_path: Path):
    root = tmp_path / "dataset" / "RE2-OB" / "checkoutservice_cpu" / "1"
    root.mkdir(parents=True, exist_ok=True)
    (root / "inject_time.txt").write_text("1000\n", encoding="utf-8")
    (root / "simple_metrics.csv").write_text(
        "time,checkoutservice_cpu,checkoutservice_mem\n"
        "400,1,1\n999,1,1\n1000,9,1\n1600,9,1\n",
        encoding="utf-8",
    )
    (root / "logs.csv").write_text(
        "time,service,message,level\n"
        "1000,checkoutservice,overload,ERROR\n",
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
    return case, dev_case_to_telemetry_case(case)


def _scheduled(case, variant: Variant) -> ScheduleRecord:
    return ScheduleRecord(
        schema_version="rcaeval-re2-v2-dev.scheduled-run.v1",
        run_id=hashlib.sha256(variant.value.encode("utf-8")).hexdigest()[:32],
        split=SplitName.DESIGN,
        identity=CaseIdentity(
            system="RE2-OB",
            root_cause_service=case.root_cause_service,
            fault=case.fault,
            instance=case.instance,
        ),
        variant=variant,
        arm_position=1,
        case_order_digest_sha256="d" * 64,
    )


def _run(tmp_path: Path, variant: Variant, provider):
    dev_case, visible = _case(tmp_path)
    return execute_v2_scheduled_once(
        _scheduled(dev_case, variant),
        visible,
        case_identity_sha256="c" * 64,
        provider=provider,
        indicator_formula=FormulaId.F0,
        indicator_config=_config(),
        run_root=tmp_path / "runs" / variant.value,
    )


def test_single_v2_persists_exact_judge_input_and_resolved_diagnosis(
    tmp_path: Path,
) -> None:
    provider = _StaticObservableProvider()
    terminal = _run(tmp_path, Variant.SINGLE_V2, provider)
    run_root = tmp_path / "runs" / Variant.SINGLE_V2.value

    assert terminal.terminal_status is OperationStatus.COMPLETED
    assert terminal.tool_calls == 3
    assert terminal.usage.model_calls_delta == 1
    assert terminal.diagnosis is not None
    assert terminal.diagnosis.model_proposed_indicator == "mem"
    assert terminal.diagnosis.resolved_indicator == "cpu"
    assert [path.name for path in sorted((run_root / "operations").iterdir())] == [
        "0001-FINAL_JUDGE.json",
        "0002-INDICATOR_RESOLVER.json",
    ]
    judge_input = json.loads(
        (run_root / "snapshots" / "0001-final-judge-input.json").read_text()
    )
    assert judge_input["indicator_candidates"]
    assert judge_input["specialist_assessments"] == []
    assert "/Users/" not in json.dumps(judge_input)


def test_fixed_v2_persists_three_specialists_then_judge_and_resolver(
    tmp_path: Path,
) -> None:
    terminal = _run(tmp_path, Variant.FIXED_V2, _StaticObservableProvider())
    run_root = tmp_path / "runs" / Variant.FIXED_V2.value

    assert terminal.terminal_status is OperationStatus.COMPLETED
    assert terminal.usage.model_calls_delta == 4
    assert terminal.tool_calls == 3
    assert [path.name for path in sorted((run_root / "operations").iterdir())] == [
        "0001-METRICS_SPECIALIST.json",
        "0002-LOGS_SPECIALIST.json",
        "0003-TRACES_SPECIALIST.json",
        "0004-FINAL_JUDGE.json",
        "0005-INDICATOR_RESOLVER.json",
    ]


def test_dynamic_v2_persists_commander_selection_and_selected_specialist(
    tmp_path: Path,
) -> None:
    terminal = _run(tmp_path, Variant.DYNAMIC_V2, _StaticObservableProvider())
    run_root = tmp_path / "runs" / Variant.DYNAMIC_V2.value

    assert terminal.terminal_status is OperationStatus.COMPLETED
    assert terminal.usage.model_calls_delta == 4
    assert terminal.tool_calls == 2
    assert [path.name for path in sorted((run_root / "operations").iterdir())] == [
        "0001-METRICS_SPECIALIST.json",
        "0002-COMMANDER.json",
        "0003-LOGS_SPECIALIST.json",
        "0004-FINAL_JUDGE.json",
        "0005-INDICATOR_RESOLVER.json",
    ]
    commander = json.loads(
        (run_root / "operations" / "0002-COMMANDER.json").read_text()
    )
    assert commander["selected_sources"] == ["logs"]


def test_provider_failure_has_exact_stage_and_second_call_is_read_only(
    tmp_path: Path,
) -> None:
    provider = _StaticObservableProvider(fail_operation=OperationType.FINAL_JUDGE)
    first = _run(tmp_path, Variant.SINGLE_V2, provider)
    calls = provider.calls
    second_provider = _StaticObservableProvider()
    second = _run(tmp_path, Variant.SINGLE_V2, second_provider)

    assert first == second
    assert first.terminal_status is OperationStatus.PROVIDER_FAILURE
    assert first.failure_operation_type is OperationType.FINAL_JUDGE
    assert first.failure_operation_index == 1
    assert first.diagnosis is None
    assert calls == 1
    assert second_provider.calls == 0
