from __future__ import annotations

from pathlib import Path
import json
import hashlib
from types import SimpleNamespace

import pytest

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_rcaeval.contracts import TerminalRecord, TerminalStatus
from ecomsre_rcaeval.dataset import DevCase
import ecomsre_rcaeval_v2.dev2_execution as execution
from ecomsre_rcaeval_v2.dev2_schedule import SCHEDULE_SEED, build_schedule
from ecomsre_rcaeval_v2.schedule import (
    SPLIT_SEED,
    CaseIdentity,
    SplitName,
    build_split_assignments,
)


def _identities() -> tuple[CaseIdentity, ...]:
    services = {
        "RE2-OB": ("checkoutservice", "currencyservice", "emailservice", "productcatalogservice", "recommendationservice"),
        "RE2-SS": ("carts", "catalogue", "orders", "payment", "user"),
    }
    return tuple(
        CaseIdentity(system=system, root_cause_service=service, fault=fault, instance=str(instance))  # type: ignore[arg-type]
        for system, system_services in services.items()
        for service in system_services
        for fault in ("cpu", "mem", "disk", "delay", "loss", "socket")
        for instance in (1, 2, 3)
    )


def test_missing_admission_blocks_provider_construction_and_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assignments = build_split_assignments(_identities(), seed=SPLIT_SEED)
    scheduled = build_schedule(assignments, SplitName.DESIGN, seed=SCHEDULE_SEED)[0]
    provider_constructions = 0

    def fail_ready(*args: object, **kwargs: object) -> None:
        raise ValueError("schedule admission lock is missing")

    def forbidden_provider(*args: object, **kwargs: object) -> None:
        nonlocal provider_constructions
        provider_constructions += 1
        raise AssertionError("Provider construction must remain unreachable")

    monkeypatch.setattr(execution, "verify_provider_ready", fail_ready)
    monkeypatch.setattr(execution, "new_v1_reference_provider", forbidden_provider)
    monkeypatch.setattr(execution, "new_v2_provider", forbidden_provider)
    with pytest.raises(ValueError, match="admission"):
        execution.execute_development_schedule(
            (scheduled,),
            cases={},
            provider_config=OpenAICompatibleConfig(
                base_url="https://provider.invalid", api_key="unused", model="unused"
            ),
            control_root=tmp_path / "control",
            private_schedule_root=tmp_path / "private-schedules",
            output_root=tmp_path / "output",
            smoke_journal_root=tmp_path / "smoke-journal",
            design_journal_root=tmp_path / "design-journal",
            execution_phase="smoke",
            preserved_roots={},
        )
    assert provider_constructions == 0
    assert not list(tmp_path.rglob("run-attempt.json"))


def test_old_id_loader_fails_before_opening_tt_or_holdout_path(tmp_path: Path) -> None:
    forbidden = tmp_path / "RE2-TT" / "holdout-schedule.json"
    with pytest.raises(ValueError, match="forbidden"):
        execution.extract_run_ids(forbidden)


def test_design_reuses_existing_smoke_terminal_without_provider_object(
    tmp_path: Path,
) -> None:
    assignments = build_split_assignments(_identities(), seed=SPLIT_SEED)
    record = next(
        row
        for row in build_schedule(assignments, SplitName.DESIGN, seed=SCHEDULE_SEED)
        if row.architecture_family.value == "V1_REFERENCE"
    )
    case_root = tmp_path / record.identity.system / "case"
    case_root.mkdir(parents=True)
    metrics = case_root / "metrics.csv"
    logs = case_root / "logs.csv"
    traces = case_root / "traces.csv"
    for path in (metrics, logs, traces):
        path.write_text("time,value\n0,1\n", encoding="utf-8")
    case = DevCase(
        case_id=f"{record.identity.system.lower()}-case-0001",
        system=record.identity.system,
        root=case_root,
        metrics_path=metrics,
        logs_path=logs,
        traces_path=traces if record.identity.system == "RE2-OB" else None,
        inject_time=1,
        root_cause_service=record.identity.root_cause_service,
        fault=record.identity.fault,
        instance=record.identity.instance,
    )
    scheduled = execution.v1_scheduled_run(record, case)
    terminal = TerminalRecord(
        run_id=record.run_id,
        case_id=case.case_id,
        architecture=scheduled.architecture,
        terminal_status=TerminalStatus.PROVIDER_FAILURE,
        failure_code="PRESERVED_FAILURE",
        tool_calls=0,
        model_calls=1,
        known_provider_tokens=1,
        latency_seconds=0.1,
    )
    terminal_root = tmp_path / "output/v1-terminal-records"
    attempt_root = tmp_path / "output/v1-terminal-records.attempts"
    terminal_root.mkdir(parents=True)
    attempt_root.mkdir(parents=True)
    (terminal_root / f"{record.run_id}.json").write_text(
        terminal.model_dump_json(), encoding="utf-8"
    )
    (attempt_root / f"{record.run_id}.json").write_text(
        json.dumps(
            {
                "schema_version": "rcaeval-re2.semantic-attempt.v1",
                "run_id": record.run_id,
                "case_id": case.case_id,
                "architecture": scheduled.architecture.value,
                "max_semantic_attempts": 1,
            }
        ),
        encoding="utf-8",
    )
    assert execution._reuse_terminal_if_present(record, case, tmp_path / "output") == terminal


def test_locked_phase_loader_rejects_hash_drift(tmp_path: Path) -> None:
    assignments = build_split_assignments(_identities(), seed=SPLIT_SEED)
    schedule_root = tmp_path / "private-schedules"
    frozen = execution.freeze_private_schedules(
        assignments,
        schedule_root,
        control_root=tmp_path / "control",
        output_root=tmp_path / "output",
        smoke_journal_root=tmp_path / "smoke-journal",
        design_journal_root=tmp_path / "design-journal",
        preserved_roots={
            "v2_dev_v1": tmp_path / "old-dev-v1",
            "v2_dev1_control": tmp_path / "old-dev1-control",
            "v2_dev1_output": tmp_path / "old-dev1-output",
        },
        project_root=tmp_path / "repo",
    )
    evaluation = SimpleNamespace(
        smoke_schedule_sha256=frozen.smoke_schedule_sha256,
        design_schedule_sha256=frozen.design_schedule_sha256,
    )
    admission = SimpleNamespace(
        smoke_schedule_sha256=frozen.smoke_schedule_sha256,
        design_schedule_sha256=frozen.design_schedule_sha256,
    )
    observed = execution._load_locked_phase_schedule(
        schedule_root,
        "smoke",
        evaluation=evaluation,  # type: ignore[arg-type]
        admission=admission,  # type: ignore[arg-type]
    )
    assert len(observed) == 72
    smoke_path = schedule_root / "smoke-schedule.json"
    smoke_path.write_bytes(smoke_path.read_bytes() + b" ")
    assert hashlib.sha256(smoke_path.read_bytes()).hexdigest() != frozen.smoke_schedule_sha256
    with pytest.raises(ValueError, match="hash drift"):
        execution._load_locked_phase_schedule(
            schedule_root,
            "smoke",
            evaluation=evaluation,  # type: ignore[arg-type]
            admission=admission,  # type: ignore[arg-type]
        )


def test_schedule_freeze_rejects_preserved_root_nesting_before_writing(
    tmp_path: Path,
) -> None:
    assignments = build_split_assignments(_identities(), seed=SPLIT_SEED)
    old_v1 = tmp_path / "old-dev-v1"
    private_schedule = old_v1 / "nested-private-schedules"
    with pytest.raises(ValueError, match="pairwise disjoint"):
        execution.freeze_private_schedules(
            assignments,
            private_schedule,
            control_root=tmp_path / "control",
            output_root=tmp_path / "output",
            smoke_journal_root=tmp_path / "smoke-journal",
            design_journal_root=tmp_path / "design-journal",
            preserved_roots={
                "v2_dev_v1": old_v1,
                "v2_dev1_control": tmp_path / "old-dev1-control",
                "v2_dev1_output": tmp_path / "old-dev1-output",
            },
            project_root=tmp_path / "repo",
        )
    assert not private_schedule.exists()


def test_caller_supplied_rows_cannot_bypass_locked_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assignments = build_split_assignments(_identities(), seed=SPLIT_SEED)
    rows = build_schedule(assignments, SplitName.DESIGN, seed=SCHEDULE_SEED)
    monkeypatch.setattr(execution, "verify_provider_ready", lambda *args, **kwargs: (object(), object()))
    monkeypatch.setattr(
        execution,
        "_load_locked_phase_schedule",
        lambda *args, **kwargs: rows[:72],
    )
    with pytest.raises(ValueError, match="admitted locked schedule"):
        execution.execute_development_schedule(
            tuple(reversed(rows[:72])),
            cases={},
            provider_config=OpenAICompatibleConfig(
                base_url="https://provider.invalid", api_key="unused", model="unused"
            ),
            control_root=tmp_path / "control",
            private_schedule_root=tmp_path / "private-schedules",
            output_root=tmp_path / "output",
            smoke_journal_root=tmp_path / "smoke-journal",
            design_journal_root=tmp_path / "design-journal",
            execution_phase="smoke",
            preserved_roots={},
        )
