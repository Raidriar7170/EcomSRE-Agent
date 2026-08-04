"""Strict loaders for public Phase 5B protocol configuration."""

from __future__ import annotations

import json
from pathlib import Path
import stat
from typing import Any, TypeVar

from pydantic import BaseModel

from ecomsre.phase5b.contracts import SeedPolicy, SuiteRegistry


ModelT = TypeVar("ModelT", bound=BaseModel)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate protocol JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite protocol JSON constant: {value}")


def _freeze_json(value: object) -> object:
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, dict):
        return {key: _freeze_json(item) for key, item in value.items()}
    return value


def load_strict_json(path: Path, model: type[ModelT]) -> ModelT:
    payload = load_protocol_object(path)
    return model.model_validate(_freeze_json(payload))


def load_protocol_object(path: Path) -> dict[str, object]:
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError("protocol config must be a regular non-symlink file")
    if details.st_size > 1024 * 1024:
        raise ValueError("protocol config exceeds the size limit")
    payload = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("protocol config must be a JSON object")
    return payload


def load_suite_registry(path: Path) -> SuiteRegistry:
    return load_strict_json(path, SuiteRegistry)


def load_seed_policy(path: Path) -> SeedPolicy:
    return load_strict_json(path, SeedPolicy)


def load_analysis_plan(path: Path) -> dict[str, object]:
    payload = load_protocol_object(path)
    required = {
        "schema_version",
        "evaluation_version",
        "primary_population",
        "primary_comparison",
        "primary_metric",
        "bootstrap_method",
        "bootstrap_rng_engine",
        "bootstrap_draw_algorithm",
        "bootstrap_replicates",
        "bootstrap_rng_seed",
        "confidence_interval",
        "percentile_method",
        "superiority_rule",
        "accuracy_noninferiority_margin",
        "minimum_mean_tool_call_reduction",
        "secondary_comparisons",
        "failure_denominator",
        "difficult_subsets",
    }
    if set(payload) != required:
        raise ValueError("analysis plan fields are not exact")
    return payload
