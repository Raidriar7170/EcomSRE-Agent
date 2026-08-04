"""Phase 5A wrapper around the existing isolated replay worker runner."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType


def _phase4_runner(project_root: Path) -> ModuleType:
    module_name = "_ecomsre_shared_isolated_worker_runner"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    source = project_root / "eval/phase4/runner.py"
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise ImportError("shared evaluator worker runner cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def worker_request(project_root: Path, request: dict[str, object]) -> object:
    root = Path(project_root).resolve(strict=True)
    return _phase4_runner(root).worker_request(
        root,
        request,
        worker_relative=Path("src/ecomsre/phase5a/replay_worker.py"),
        evaluator_relative=Path("eval/phase5a"),
    )
