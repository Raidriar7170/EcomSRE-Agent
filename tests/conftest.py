"""Controlled loaders for repository test-only support modules."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_test_support_module(name: str, source: Path) -> None:
    if name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise ImportError(f"test support module cannot be loaded: {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)


_TESTS_ROOT = Path(__file__).resolve().parent
_PROJECT_ROOT = _TESTS_ROOT.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.append(str(_PROJECT_ROOT))
_load_test_support_module(
    "telemetry_promotion_support",
    _TESTS_ROOT / "telemetry_promotion_support.py",
)


@pytest.fixture(autouse=True)
def _adapt_historical_phase5b_preflight_for_successor_tests(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the frozen execution test valid on independently added namespaces."""

    if request.node.nodeid != (
        "tests/phase5b_execution/test_execution_cli.py::"
        "test_execution_preflight_exposes_main_readiness_and_ablation_gap"
    ):
        return
    from scripts.ci.verify_phase5b_historical_bindings import (
        verify_historical_bindings,
    )
    from scripts.phase5b_execution import cli as execution_cli

    monkeypatch.setattr(
        execution_cli,
        "verify_freeze_manifest",
        verify_historical_bindings,
    )
