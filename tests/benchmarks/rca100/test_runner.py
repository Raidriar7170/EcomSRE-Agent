from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ecomsre_rca100.lifecycle import PrivateRoots, build_schedule
from ecomsre_rca100.preflight import run_synthetic_full_pipeline
from ecomsre_rca100.runner import (
    RCA100TerminalRecord,
    RCA100TerminalStatus,
    execute_schedule,
)


def _roots(tmp_path: Path) -> PrivateRoots:
    return PrivateRoots(
        input_source=tmp_path / "input",
        control=tmp_path / "control",
        schedule=tmp_path / "schedule",
        journal=tmp_path / "journal",
        output=tmp_path / "output",
        evaluator_source=tmp_path / "evaluator-source",
        evaluator=tmp_path / "evaluator",
    )


def test_synthetic_preflight_runs_projection_provider_m3_and_terminal_once(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)

    terminal = run_synthetic_full_pipeline(
        roots,
        protocol_freeze_sha256="a" * 64,
        schedule_sha256="b" * 64,
        model="synthetic-model",
        timeout_seconds=30.0,
        max_completion_tokens=2048,
        prompt_token_reservation=29952,
        attempt_token_reservation=32000,
        retry_policy_sha256="c" * 64,
    )

    assert terminal.status is RCA100TerminalStatus.COMPLETED
    assert terminal.provider_attempts == 1
    assert terminal.transport_retries == 0
    assert terminal.semantic_model_operations == 1
    assert terminal.m3_action == "OVERRIDE_METRICS_TOP1"
    assert terminal.initial_root_entity_ref == "k8s|k8s.pod|synthetic-pod"
    assert terminal.final_root_entity_ref == "apm|apm.service|synthetic-b"
    assert (
        roots.control
        / "preflight"
        / "synthetic-full-pipeline-v1"
        / "output"
        / "terminals"
        / "rca100-case-0001.json"
    ).is_file()

    with pytest.raises(FileExistsError):
        run_synthetic_full_pipeline(
            roots,
            protocol_freeze_sha256="a" * 64,
            schedule_sha256="b" * 64,
            model="synthetic-model",
            timeout_seconds=30.0,
            max_completion_tokens=2048,
            prompt_token_reservation=29952,
            attempt_token_reservation=32000,
            retry_policy_sha256="c" * 64,
        )


def _failure_terminal(
    *,
    position: int,
    opaque_case_id: str,
    run_id: str,
    failure_code: str,
) -> RCA100TerminalRecord:
    now = datetime.now(timezone.utc)
    return RCA100TerminalRecord(
        run_id=run_id,
        opaque_case_id=opaque_case_id,
        schedule_position=position,
        status=RCA100TerminalStatus.PROVIDER_FAILURE,
        failure_code=failure_code,
        semantic_model_operations=0,
        provider_attempts=1,
        transport_retries=0,
        known_token_lower_bound=0,
        conservative_token_upper_bound=32_000,
        latency_seconds=0.1,
        started_at_utc=now,
        ended_at_utc=now,
    )


def test_schedule_stops_admission_immediately_after_terminal_http_429() -> None:
    schedule = build_schedule(tuple(f"t{index:03d}" for index in range(1, 104)))
    admitted: list[int] = []

    def execute(record: object) -> RCA100TerminalRecord:
        assert hasattr(record, "position")
        admitted.append(record.position)  # type: ignore[attr-defined]
        return _failure_terminal(
            position=record.position,  # type: ignore[attr-defined]
            opaque_case_id=record.opaque_case_id,  # type: ignore[attr-defined]
            run_id=record.run_id,  # type: ignore[attr-defined]
            failure_code="HTTP_429" if record.position == 2 else "HTTP_500",  # type: ignore[attr-defined]
        )

    terminals = execute_schedule(schedule, execute=execute)  # type: ignore[arg-type]

    assert len(terminals) == 2
    assert admitted == [1, 2]
    assert terminals[-1].failure_code == "HTTP_429"
