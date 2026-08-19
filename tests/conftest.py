"""Controlled loaders for repository test-only support modules."""

from __future__ import annotations

import importlib.util
import subprocess
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
    from scripts.ci.verify_phase5b_execution_historical_bindings import (
        verify_historical_execution_bindings,
    )
    from scripts.phase5b_execution import cli as execution_cli

    monkeypatch.setattr(
        execution_cli,
        "verify_freeze_manifest",
        verify_historical_bindings,
    )
    monkeypatch.setattr(
        execution_cli,
        "verify_execution_freeze_manifest",
        verify_historical_execution_bindings,
    )


@pytest.fixture(autouse=True)
def _run_frozen_v21_pr_f_verifier_at_its_attested_head(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Evaluate the PR #56 scope at PR #56, not at an unrelated successor."""

    if request.node.nodeid != (
        "tests/dta_v21/test_v21_pr_f_protocol_verifier.py::"
        "test_public_pr_f_protocol_verifier_passes_without_private_evidence"
    ):
        return
    historical_root = tmp_path / "dta-v21-pr56-historical-repository"
    subprocess.run(
        (
            "git",
            "clone",
            "--shared",
            "--no-checkout",
            str(_PROJECT_ROOT),
            str(historical_root),
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        (
            "git",
            "checkout",
            "-q",
            "--detach",
            "9da92d54a4fb470c5452cee36a731e81529d05a5",
        ),
        cwd=historical_root,
        check=True,
        capture_output=True,
        text=True,
    )
    test_module = request.node.module
    verifier = getattr(test_module, "verify_pr_f_protocol")
    monkeypatch.setattr(
        test_module,
        "verify_pr_f_protocol",
        lambda _current_root: verifier(historical_root),
    )
