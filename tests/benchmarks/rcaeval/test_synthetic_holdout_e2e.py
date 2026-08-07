from __future__ import annotations

from pathlib import Path

from ecomsre_rcaeval.artifacts import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    sha256_tree,
    write_json_create_once,
)
from ecomsre_rcaeval.contracts import (
    CommanderDecision,
    Diagnosis,
    SpecialistAssessment,
    TerminalStatus,
)
from ecomsre_rcaeval.dataset import load_sanitized_cases
from ecomsre_rcaeval.execution import load_terminal_records, run_schedule
from ecomsre_rcaeval.lifecycle import advance_state, current_state
from ecomsre_rcaeval.reporting import (
    build_final_report,
    load_ground_truth,
    verify_final_report,
)
from ecomsre_rcaeval.sanitize import seal_holdout
from ecomsre_rcaeval.schedule import build_schedule
from ecomsre_rcaeval.state import HoldoutState


def _raw_case(root: Path, service: str, fault: str, instance: str) -> None:
    case = root / f"{service}_{fault}" / instance
    case.mkdir(parents=True)
    indicator = {
        "cpu": "cpu",
        "mem": "mem",
        "disk": "diskio",
        "delay": "latency",
        "loss": "latency",
        "socket": "socket",
    }[fault]
    (case / "inject_time.txt").write_text("2\n", encoding="utf-8")
    (case / "simple_metrics.csv").write_text(
        f"time,{service}_{indicator}\n0,1\n1,1\n2,10\n3,10\n",
        encoding="utf-8",
    )
    (case / "logs.csv").write_text(
        f"time,service,message\n2,{service},synthetic anomaly\n",
        encoding="utf-8",
    )
    (case / "traces.csv").write_text(
        f"time,service,peer,duration,error\n"
        f"1,{service},dependency,1,0\n"
        f"2,{service},dependency,5,1\n",
        encoding="utf-8",
    )


class EvidenceProvider:
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
        evidence = context.evidence[0]
        indicator = evidence.name.rsplit("_", 1)[1]
        return Diagnosis(
            root_cause_service=evidence.service,
            root_cause_indicator=indicator,
            evidence_refs=(evidence.evidence_id,),
            explanation="Synthetic end-to-end evidence diagnosis.",
        )


def test_synthetic_holdout_runs_full_fail_closed_state_machine(tmp_path: Path) -> None:
    raw = tmp_path / "synthetic-raw"
    for service_index in range(1, 6):
        for fault in ("cpu", "mem", "disk", "delay", "loss", "socket"):
            for instance in ("1", "2", "3"):
                _raw_case(raw, f"service-{service_index}", fault, instance)
    control = tmp_path / "control"
    state_journal = control / "state-journal"
    advance_state(
        state_journal,
        HoldoutState.PROTOCOL_FROZEN,
        evidence_sha256="a" * 64,
    )
    sanitized = tmp_path / "sanitized"
    evaluator = tmp_path / "evaluator-only"
    seal = seal_holdout(
        raw,
        sanitized,
        evaluator,
        expected_cases=90,
        opaque_seed="synthetic-e2e-seed",
    )
    advance_state(
        state_journal,
        HoldoutState.HOLDOUT_SEALED,
        evidence_sha256=sha256_bytes(canonical_json_bytes(seal.model_dump(mode="json"))),
    )
    advance_state(
        state_journal,
        HoldoutState.HOLDOUT_PREFLIGHT_PASSED,
        evidence_sha256=sha256_file(sanitized / "manifest.json"),
    )
    cases = load_sanitized_cases(sanitized)
    schedule = build_schedule(tuple(case.case_id for case in cases), seed=20_260_806)
    terminal_root = tmp_path / "terminal-records"
    run_schedule(
        cases,
        schedule,
        lambda _scheduled: EvidenceProvider(),
        terminal_root,
    )
    terminal_sha = sha256_tree(terminal_root, include_suffixes=(".json",))
    advance_state(
        state_journal,
        HoldoutState.HOLDOUT_EXECUTED,
        evidence_sha256=terminal_sha,
    )
    records = load_terminal_records(schedule, terminal_root)
    assert all(record.terminal_status is TerminalStatus.COMPLETED for record in records)
    advance_state(
        state_journal,
        HoldoutState.TERMINAL_RECORDS_LOCKED,
        evidence_sha256=terminal_sha,
    )
    truth_path = evaluator / "ground-truth.json"
    truth = load_ground_truth(truth_path)
    advance_state(
        state_journal,
        HoldoutState.UNBLINDED,
        evidence_sha256=sha256_file(truth_path),
    )
    report_path = tmp_path / "final-report.json"
    report_sha = write_json_create_once(
        report_path,
        build_final_report(records, truth, bootstrap_replicates=50),
    )
    advance_state(
        state_journal,
        HoldoutState.FINAL_REPORT_FROZEN,
        evidence_sha256=report_sha,
    )

    verify_final_report(
        report_path,
        records,
        truth,
        bootstrap_replicates=50,
    )
    assert current_state(state_journal) is HoldoutState.FINAL_REPORT_FROZEN
    assert not (sanitized / "ground-truth.json").exists()
