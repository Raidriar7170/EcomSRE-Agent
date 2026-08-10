"""Synthetic non-case full-pipeline preflight for the frozen RCA100 runtime."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_rca100.lifecycle import (
    PrivateRoots,
    RCA100ScheduleRecord,
    create_once_json,
)
from ecomsre_rca100.projection import build_agent_context
from ecomsre_rca100.runner import RCA100TerminalRecord, execute_case
from ecomsre_rcaeval_adaptive.v2_runner import RequestPacer
from ecomsre_rcaeval_v2.dev3_token_accounting import AttemptBudget


class SyntheticStrongSingleTransport:
    def __init__(self, *, model: str) -> None:
        self._model = model

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        del url, headers, payload, timeout_seconds
        arguments = {
            "root_cause_entity_ref": "k8s|k8s.pod|synthetic-pod",
            "fault_type": "synthetic latency",
            "confidence": 0.8,
            "evidence_refs": ["metric:0002"],
            "reasoning_steps": [
                {
                    "claim": "Synthetic bounded evidence supports the initial entity.",
                    "entity_ref_or_none": "k8s|k8s.pod|synthetic-pod",
                    "evidence_refs": ["metric:0002"],
                }
            ],
            "summary": "Synthetic full-pipeline contract check.",
        }
        return {
            "model": self._model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "submit_rca100_initial_diagnosis",
                                    "arguments": json.dumps(arguments),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 25,
                "total_tokens": 125,
            },
        }


def write_synthetic_case(case_root: Path) -> None:
    case_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    create_once_json(
        case_root / "task.json",
        {
            "task_id": "synthetic-non-case",
            "task_version": "1.0",
            "alert_event_id": "synthetic-event",
            "alert_title": "Synthetic latency alert",
            "alert_trigger_time": 110.0,
            "alert_window": {"start": 100.0, "end": 120.0},
            "alert_entity": {
                "entity_id": "synthetic-pod",
                "entity_name": "synthetic-pod",
                "entity_type": "k8s.pod",
                "entity_domain": "k8s",
            },
            "prompt_text": "Diagnose this synthetic non-case incident.",
            "available_modalities": ["metrics", "topology"],
        },
    )
    create_once_json(
        case_root / "topology.json",
        {
            "case_id": "synthetic-non-case",
            "cluster_id": "synthetic",
            "source": "synthetic",
            "window": {},
            "stats": {},
            "entities": [
                {
                    "id": "synthetic-a",
                    "type": "apm.service",
                    "name": "synthetic-a",
                    "props": {},
                },
                {
                    "id": "synthetic-b",
                    "type": "apm.service",
                    "name": "synthetic-b",
                    "props": {},
                },
                {
                    "id": "synthetic-pod",
                    "type": "k8s.pod",
                    "name": "synthetic-pod",
                    "props": {},
                },
            ],
            "edges": [],
        },
    )
    rows: list[dict[str, object]] = []
    for timestamp, value_a, value_b in (
        (101_000_000, 1.0, 1.0),
        (102_000_000, 1.0, 1.0),
        (103_000_000, 1.0, 1.0),
        (111_000_000, 2.0, 5.0),
        (112_000_000, 2.0, 5.0),
        (113_000_000, 2.0, 5.0),
    ):
        rows.extend(
            (
                {
                    "time": timestamp,
                    "domain": "apm",
                    "entity_set": "apm.service.legacy",
                    "entity_id": "synthetic-a",
                    "entity_name": "synthetic-a",
                    "metric": "latency",
                    "value": value_a,
                    "metric_set_id": "synthetic",
                    "service": None,
                },
                {
                    "time": timestamp,
                    "domain": "apm",
                    "entity_set": "apm.service.legacy",
                    "entity_id": "synthetic-b",
                    "entity_name": "synthetic-b",
                    "metric": "latency",
                    "value": value_b,
                    "metric_set_id": "synthetic",
                    "service": None,
                },
            )
        )
    pq.write_table(pa.Table.from_pylist(rows), case_root / "metrics.parquet")


def run_synthetic_full_pipeline(
    roots: PrivateRoots,
    *,
    protocol_freeze_sha256: str,
    schedule_sha256: str,
    model: str,
    timeout_seconds: float,
    max_completion_tokens: int,
    prompt_token_reservation: int,
    attempt_token_reservation: int,
    retry_policy_sha256: str,
) -> RCA100TerminalRecord:
    preflight_root = roots.control / "preflight" / "synthetic-full-pipeline-v1"
    case_root = preflight_root / "cases" / "t001"
    write_synthetic_case(case_root)
    record = RCA100ScheduleRecord(
        position=1,
        source_task_id="t001",
        opaque_case_id="rca100-case-0001",
        run_id=hashlib.sha256(b"rca100-synthetic-full-pipeline-v1").hexdigest()[:32],
    )
    terminal = execute_case(
        record,
        cases_root=preflight_root / "cases",
        journal_root=preflight_root / "journal",
        output_root=preflight_root / "output",
        schedule_sha256=schedule_sha256,
        protocol_freeze_sha256=protocol_freeze_sha256,
        provider_config=OpenAICompatibleConfig(
            base_url="https://synthetic.invalid/v1",
            api_key="synthetic-preflight-only",
            model=model,
        ),
        expected_model=model,
        timeout_seconds=timeout_seconds,
        max_completion_tokens=max_completion_tokens,
        prompt_token_reservation=prompt_token_reservation,
        pacer=RequestPacer(5.0),
        budget=AttemptBudget(
            max_provider_attempts=1,
            max_retry_attempts=0,
            prompt_token_reservation=prompt_token_reservation,
            max_completion_tokens=max_completion_tokens,
            max_conservative_tokens=attempt_token_reservation,
        ),
        retry_policy_sha256=retry_policy_sha256,
        base_transport=SyntheticStrongSingleTransport(model=model),
        context_builder=build_agent_context,
    )
    if (
        terminal.status.value != "COMPLETED"
        or terminal.semantic_model_operations != 1
        or terminal.provider_attempts != 1
        or terminal.transport_retries != 0
        or terminal.m3_action is None
    ):
        raise ValueError("synthetic full-pipeline holdout preflight failed")
    return terminal


__all__ = [
    "SyntheticStrongSingleTransport",
    "run_synthetic_full_pipeline",
    "write_synthetic_case",
]
