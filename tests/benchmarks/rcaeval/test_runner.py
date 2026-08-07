from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ecomsre_rcaeval.adapter import prepare_architecture_context
from ecomsre_rcaeval.contracts import (
    Architecture,
    CommanderDecision,
    Diagnosis,
    ScheduledRun,
    SpecialistAssessment,
    TerminalStatus,
)
from ecomsre_rcaeval.dataset import DevSystem, discover_dev_cases
from ecomsre_rcaeval.normalization import UnresolvedServiceAlias
from ecomsre_rcaeval.provider import ProviderDiagnosisError
from ecomsre_rcaeval.runner import execute_scheduled_once
from ecomsre.model.gateway import ProviderProtocolError


def _case(root: Path):
    case = root / "RE2-OB" / "checkoutservice_cpu" / "1"
    case.mkdir(parents=True)
    (case / "inject_time.txt").write_text("2\n", encoding="utf-8")
    (case / "simple_metrics.csv").write_text(
        "time,checkoutservice_cpu\n0,1\n1,1\n2,10\n3,10\n",
        encoding="utf-8",
    )
    (case / "logs.csv").write_text(
        "time,service,message,level\n2,checkoutservice,overload,ERROR\n",
        encoding="utf-8",
    )
    (case / "traces.csv").write_text(
        "time,service,peer,duration,error\n"
        "1,checkoutservice,cartservice,1,0\n"
        "2,checkoutservice,cartservice,5,1\n",
        encoding="utf-8",
    )
    discovered = discover_dev_cases(root / "RE2-OB", DevSystem.RE2_OB)[0]
    return replace(discovered, case_id="tt-case-0001")


class CountingProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def diagnose(self, incident, context, architecture):
        self.calls += 1
        if self.fail:
            raise ConnectionError("synthetic provider failure")
        assert "root_cause_service" not in incident.model_dump(mode="json")
        return Diagnosis(
            root_cause_service="checkoutservice",
            root_cause_indicator="cpu",
            confidence=None,
            evidence_refs=(context.evidence[0].evidence_id,),
            explanation="The strongest bounded anomaly is checkout CPU.",
        )

    def specialize(self, incident, context, source):
        self.calls += 1
        del incident
        if self.fail:
            raise ConnectionError("synthetic provider failure")
        evidence = next(
            (
                item
                for item in context.evidence
                if item.evidence_id.startswith(
                    {"metrics": "metric:", "logs": "log:", "traces": "trace:"}[
                        source
                    ]
                )
            ),
            None,
        )
        return SpecialistAssessment(
            source=source,
            observation_status=(
                "AVAILABLE" if evidence is not None else "SOURCE_UNAVAILABLE"
            ),
            candidate_service=(evidence.service if evidence is not None else None),
            candidate_indicator=("cpu" if evidence is not None else None),
            confidence=(0.8 if evidence is not None else 0.0),
            evidence_refs=(evidence.evidence_id,) if evidence is not None else (),
            summary="Synthetic source-isolated assessment.",
        )

    def plan_followup(self, incident, context, metrics_assessment):
        self.calls += 1
        del incident, context, metrics_assessment
        if self.fail:
            raise ConnectionError("synthetic provider failure")
        return CommanderDecision(
            selected_sources=("logs",),
            rationale="Use logs for deterministic synthetic follow-up.",
        )


class OverBudgetProvider(CountingProvider):
    last_usage_tokens = 32_001


class InterruptingProvider(CountingProvider):
    def diagnose(self, incident, context, architecture):
        del incident, context, architecture
        raise KeyboardInterrupt


class ClassifiedFailureProvider(CountingProvider):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def diagnose(self, incident, context, architecture):
        del incident, context, architecture
        raise self.error


def test_architecture_contexts_use_fresh_run_local_evidence_ids(tmp_path: Path) -> None:
    case = _case(tmp_path)

    single = prepare_architecture_context(case, Architecture.SINGLE)
    fixed = prepare_architecture_context(case, Architecture.FIXED)
    dynamic = prepare_architecture_context(case, Architecture.DYNAMIC)

    assert single.evidence[0].evidence_id == "metric:0001"
    assert fixed.evidence[0].evidence_id == "metric:0001"
    assert dynamic.evidence[0].evidence_id == "metric:0001"
    assert single.context_id != fixed.context_id != dynamic.context_id
    assert single.tool_call_count == 3
    assert fixed.tool_call_count == 3
    assert dynamic.tool_call_count == 1
    assert single.specialist_assessments == ()
    assert fixed.specialist_assessments == ()
    assert dynamic.specialist_assessments == ()
    assert all(item.run_id == single.run_id for item in single.canonical_evidence)
    assert dynamic.commander_stages[0].selected_sources == ("metrics",)
    assert dynamic.targeted_refinement_used is False


def test_agent_context_rejects_raw_path_leakage_from_log_payload(tmp_path: Path) -> None:
    case = _case(tmp_path)
    case.logs_path.write_text(
        f"time,service,message,level\n2,checkoutservice,{case.root},ERROR\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="raw telemetry path"):
        prepare_architecture_context(case, Architecture.SINGLE)


def test_create_once_terminal_journal_prevents_scored_retry(tmp_path: Path) -> None:
    case = _case(tmp_path)
    scheduled = ScheduledRun(
        run_id="1" * 32,
        case_id=case.case_id,
        architecture=Architecture.DYNAMIC,
        call_position=1,
        schedule_seed=20_260_806,
    )
    provider = CountingProvider()
    journal = tmp_path / "journal"

    first = execute_scheduled_once(scheduled, case, provider, journal)
    second = execute_scheduled_once(scheduled, case, provider, journal)

    assert first == second
    assert first.terminal_status is TerminalStatus.COMPLETED
    assert provider.calls == 4
    assert first.model_calls == 4
    assert first.tool_calls == 2
    assert len(tuple(journal.iterdir())) == 1


def test_provider_failure_is_terminal_and_retained_without_retry(tmp_path: Path) -> None:
    case = _case(tmp_path)
    scheduled = ScheduledRun(
        run_id="2" * 32,
        case_id=case.case_id,
        architecture=Architecture.SINGLE,
        call_position=1,
        schedule_seed=20_260_806,
    )
    provider = CountingProvider(fail=True)

    record = execute_scheduled_once(
        scheduled,
        case,
        provider,
        tmp_path / "journal",
    )

    assert record.terminal_status is TerminalStatus.PROVIDER_FAILURE
    assert record.diagnosis is None
    assert record.failure_code == "PROVIDER_TRANSPORT_FAILURE"
    assert provider.calls == 1


def test_started_attempt_without_terminal_is_never_reissued(tmp_path: Path) -> None:
    case = _case(tmp_path)
    scheduled = ScheduledRun(
        run_id="a" * 32,
        case_id=case.case_id,
        architecture=Architecture.SINGLE,
        call_position=1,
        schedule_seed=20_260_806,
    )
    journal = tmp_path / "journal"

    with pytest.raises(KeyboardInterrupt):
        execute_scheduled_once(scheduled, case, InterruptingProvider(), journal)
    replacement = CountingProvider()
    record = execute_scheduled_once(scheduled, case, replacement, journal)

    assert replacement.calls == 0
    assert record.terminal_status is TerminalStatus.PROTOCOL_VIOLATION
    assert record.failure_code == "STARTED_ATTEMPT_WITHOUT_TERMINAL"


def test_known_provider_tokens_are_enforced_by_shared_run_budget(tmp_path: Path) -> None:
    case = _case(tmp_path)
    scheduled = ScheduledRun(
        run_id="3" * 32,
        case_id=case.case_id,
        architecture=Architecture.SINGLE,
        call_position=1,
        schedule_seed=20_260_806,
    )

    record = execute_scheduled_once(
        scheduled,
        case,
        OverBudgetProvider(),
        tmp_path / "journal",
    )

    assert record.terminal_status is TerminalStatus.PROTOCOL_VIOLATION
    assert record.failure_code == "RUN_BUDGET_EXCEEDED"
    assert record.model_calls == 1
    assert record.known_provider_tokens == 32_001


@pytest.mark.parametrize(
    ("error", "status", "failure_code"),
    (
        (TimeoutError(), TerminalStatus.TIMEOUT, "PROVIDER_TIMEOUT"),
        (
            ProviderDiagnosisError("synthetic invalid output"),
            TerminalStatus.INVALID_SCHEMA,
            "PROVIDER_OUTPUT_INVALID_SCHEMA",
        ),
        (
            UnresolvedServiceAlias("synthetic unresolved alias"),
            TerminalStatus.UNRESOLVED_ALIAS,
            "PROVIDER_OUTPUT_UNRESOLVED_SERVICE_ALIAS",
        ),
        (
            ProviderProtocolError("synthetic provider envelope"),
            TerminalStatus.PROTOCOL_VIOLATION,
            "PROVIDER_PROTOCOL_VIOLATION",
        ),
    ),
)
def test_provider_failures_have_stable_terminal_classification(
    tmp_path: Path,
    error: Exception,
    status: TerminalStatus,
    failure_code: str,
) -> None:
    case = _case(tmp_path)
    scheduled = ScheduledRun(
        run_id="4" * 32,
        case_id=case.case_id,
        architecture=Architecture.SINGLE,
        call_position=1,
        schedule_seed=20_260_806,
    )

    record = execute_scheduled_once(
        scheduled,
        case,
        ClassifiedFailureProvider(error),
        tmp_path / "journal",
    )

    assert record.terminal_status is status
    assert record.failure_code == failure_code
