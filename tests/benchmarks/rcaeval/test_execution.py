from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ecomsre_rcaeval.contracts import (
    Architecture,
    CommanderDecision,
    Diagnosis,
    SpecialistAssessment,
    TerminalStatus,
)
from ecomsre_rcaeval.dataset import DevSystem, discover_dev_cases
from ecomsre_rcaeval.execution import (
    load_terminal_records,
    run_schedule,
)
from ecomsre_rcaeval.schedule import build_schedule


def _telemetry_case(root: Path, case_id: str):
    case = root / "RE2-OB" / "checkoutservice_cpu" / "1"
    case.mkdir(parents=True, exist_ok=True)
    (case / "inject_time.txt").write_text("2\n", encoding="utf-8")
    (case / "simple_metrics.csv").write_text(
        "time,checkoutservice_cpu\n0,1\n1,1\n2,10\n3,10\n",
        encoding="utf-8",
    )
    (case / "logs.csv").write_text(
        "time,service,message\n2,checkoutservice,overload\n",
        encoding="utf-8",
    )
    (case / "traces.csv").write_text(
        "time,service,peer,duration,error\n"
        "1,checkoutservice,cartservice,1,0\n"
        "2,checkoutservice,cartservice,5,1\n",
        encoding="utf-8",
    )
    discovered = discover_dev_cases(root / "RE2-OB", DevSystem.RE2_OB)[0]
    return replace(discovered, case_id=case_id)


class StaticProvider:
    def specialize(self, incident, context, source):
        del incident
        prefix = {"metrics": "metric:", "logs": "log:", "traces": "trace:"}[
            source
        ]
        evidence = next(
            item for item in context.evidence if item.evidence_id.startswith(prefix)
        )
        return SpecialistAssessment(
            source=source,
            observation_status="AVAILABLE",
            candidate_service=evidence.service,
            candidate_indicator="cpu",
            confidence=0.8,
            evidence_refs=(evidence.evidence_id,),
            summary="Synthetic source assessment.",
        )

    def plan_followup(self, incident, context, metrics_assessment):
        del incident, context, metrics_assessment
        return CommanderDecision(
            selected_sources=("logs", "traces"),
            rationale="Exercise both synthetic follow-up sources.",
        )

    def diagnose(self, incident, context, architecture):
        del incident, architecture
        return Diagnosis(
            root_cause_service="checkoutservice",
            root_cause_indicator="cpu",
            evidence_refs=(context.evidence[0].evidence_id,),
            explanation="Synthetic bounded CPU evidence.",
        )


def test_schedule_execution_requires_fresh_provider_per_run(tmp_path: Path) -> None:
    case_ids = tuple(f"tt-case-{index:04d}" for index in range(1, 91))
    prototype = _telemetry_case(tmp_path, case_ids[0])
    cases = tuple(replace(prototype, case_id=case_id) for case_id in case_ids)
    schedule = build_schedule(case_ids, seed=20_260_806)
    providers: list[StaticProvider] = []

    def provider_factory(_scheduled):
        provider = StaticProvider()
        providers.append(provider)
        return provider

    records = run_schedule(
        cases,
        schedule,
        provider_factory,
        tmp_path / "journal",
    )

    assert len(records) == 270
    assert len(providers) == 270
    assert {record.architecture for record in records} == set(Architecture)
    assert all(record.terminal_status is TerminalStatus.COMPLETED for record in records)
    assert load_terminal_records(schedule, tmp_path / "journal") == records


def test_terminal_record_set_fails_closed_on_extra_file(tmp_path: Path) -> None:
    schedule = build_schedule(
        tuple(f"tt-case-{index:04d}" for index in range(1, 91)),
        seed=20_260_806,
    )
    journal = tmp_path / "journal"
    journal.mkdir()
    (journal / "unexpected.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="terminal journal file set"):
        load_terminal_records(schedule, journal)
