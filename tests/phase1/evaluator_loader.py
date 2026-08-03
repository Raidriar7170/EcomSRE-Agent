"""Controlled test loader for the evaluator outside pytest's import roots."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_MODULE_NAME = "_ecomsre_phase1_evaluator"


def load_phase1_evaluator() -> ModuleType:
    existing = sys.modules.get(_MODULE_NAME)
    if existing is not None:
        return existing
    project_root = Path(__file__).resolve().parents[2]
    source = project_root / "eval/phase1/run.py"
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, source)
    if spec is None or spec.loader is None:
        raise ImportError("Phase 1 evaluator spec cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module
