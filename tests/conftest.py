"""Controlled loaders for repository test-only support modules."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


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
_load_test_support_module(
    "telemetry_promotion_support",
    _TESTS_ROOT / "telemetry_promotion_support.py",
)
