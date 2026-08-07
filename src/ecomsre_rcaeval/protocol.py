"""Frozen RCAEval configuration, schedule, and provider bindings."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ecomsre_rcaeval.adapter import INCIDENT_TEMPLATE
from ecomsre_rcaeval.artifacts import (
    canonical_json_bytes,
    read_json_object,
    schedule_payload,
    sha256_bytes,
)
from ecomsre_rcaeval.contracts import (
    CommanderDecision,
    Diagnosis,
    RCAEvalModel,
    ScheduledRun,
    SpecialistAssessment,
)
from ecomsre_rcaeval.provider import (
    COMMANDER_PROMPT,
    OpenAICompatibleRCAEvalProvider,
    SPECIALIST_PROMPT,
    SYSTEM_PROMPT,
)
from ecomsre_rcaeval.schedule import build_schedule
from ecomsre.model.gateway import OpenAICompatibleConfig


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PROJECT_ROOT / "config" / "rcaeval-re2-v1"


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _schema_sha256(contract: type[RCAEvalModel]) -> str:
    value = json.dumps(
        contract.model_json_schema(mode="validation"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256_bytes(value)


def diagnosis_schema_sha256() -> str:
    return _schema_sha256(Diagnosis)


def frozen_schedule() -> tuple[ScheduledRun, ...]:
    config = read_json_object(CONFIG_ROOT / "schedule-generation.json")
    seed = config.get("schedule_seed")
    if type(seed) is not int:
        raise ValueError("schedule seed is invalid")
    case_ids = tuple(f"tt-case-{index:04d}" for index in range(1, 91))
    schedule = build_schedule(case_ids, seed=seed)
    observed_sha = sha256_bytes(canonical_json_bytes(schedule_payload(schedule)))
    if observed_sha != config.get("expected_schedule_sha256"):
        raise ValueError("generated schedule differs from protocol lock")
    return schedule


def verify_prompt_lock() -> dict[str, object]:
    lock = read_json_object(CONFIG_ROOT / "prompt-lock.json")
    expected = {
        "incident_template_sha256": _text_sha256(INCIDENT_TEMPLATE),
        "system_prompt_sha256": _text_sha256(SYSTEM_PROMPT),
        "specialist_prompt_sha256": _text_sha256(SPECIALIST_PROMPT),
        "commander_prompt_sha256": _text_sha256(COMMANDER_PROMPT),
        "diagnosis_schema_sha256": diagnosis_schema_sha256(),
        "specialist_schema_sha256": _schema_sha256(SpecialistAssessment),
        "commander_schema_sha256": _schema_sha256(CommanderDecision),
    }
    if any(lock.get(key) != value for key, value in expected.items()):
        raise ValueError("prompt or output schema differs from protocol lock")
    return lock


def provider_from_lock() -> OpenAICompatibleRCAEvalProvider:
    lock = verify_prompt_lock()
    config = OpenAICompatibleConfig.from_environment(os.environ)
    if config is None:
        raise ValueError("OpenAI-compatible provider environment is not configured")
    model = lock.get("model")
    max_completion_tokens = lock.get("max_completion_tokens")
    budget = read_json_object(CONFIG_ROOT / "budget-lock.json")
    timeout_seconds = budget.get("model_call_timeout_seconds")
    if (
        not isinstance(model, str)
        or type(max_completion_tokens) is not int
        or not isinstance(timeout_seconds, (int, float))
    ):
        raise ValueError("provider or budget lock is invalid")
    return OpenAICompatibleRCAEvalProvider(
        config=config,
        expected_model=model,
        timeout_seconds=float(timeout_seconds),
        max_completion_tokens=max_completion_tokens,
    )
