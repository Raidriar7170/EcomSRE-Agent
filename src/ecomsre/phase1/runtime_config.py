"""Strict shared loader for the Phase 1 replay Agent runtime settings."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ecomsre.phase1.contracts import BudgetLimits

_AGENT_CONFIG = Path("config/phase1/agent.json")
_AGENT_CONFIG_KEYS = {
    "schema_version",
    "temperature",
    "max_model_calls",
    "max_tool_calls",
    "max_total_tokens",
    "model_timeout_seconds",
    "tool_timeout_seconds",
}


@dataclass(frozen=True, slots=True)
class AgentSettings:
    budgets: BudgetLimits
    model_timeout_seconds: float
    tool_timeout_seconds: float


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_agent_settings(project_root: Path) -> AgentSettings:
    path = Path(project_root) / _AGENT_CONFIG
    try:
        content = path.read_bytes()
        payload = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("Phase 1 agent configuration is invalid") from error
    if not isinstance(payload, dict) or set(payload) != _AGENT_CONFIG_KEYS:
        raise ValueError("Phase 1 agent configuration fields are not exact")
    if payload["schema_version"] != "phase1.agent-config.v1":
        raise ValueError("Phase 1 agent configuration version is invalid")
    temperature = payload["temperature"]
    if (
        type(temperature) not in {int, float}
        or not math.isfinite(float(temperature))
        or float(temperature) != 0.0
    ):
        raise ValueError("Phase 1 scripted temperature must be finite and zero")
    for field in ("model_timeout_seconds", "tool_timeout_seconds"):
        value = payload[field]
        if (
            type(value) not in {int, float}
            or not math.isfinite(float(value))
            or float(value) <= 0
        ):
            raise ValueError(f"{field} must be finite and positive")
    try:
        budgets = BudgetLimits(
            max_model_calls=payload["max_model_calls"],
            max_tool_calls=payload["max_tool_calls"],
            max_total_tokens=payload["max_total_tokens"],
        )
    except Exception as error:
        raise ValueError("Phase 1 agent budgets are invalid") from error
    return AgentSettings(
        budgets=budgets,
        model_timeout_seconds=float(payload["model_timeout_seconds"]),
        tool_timeout_seconds=float(payload["tool_timeout_seconds"]),
    )
