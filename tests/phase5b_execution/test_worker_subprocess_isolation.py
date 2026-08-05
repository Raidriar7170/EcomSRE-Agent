from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from scripts.phase5b_execution.worker_entrypoint import _path_is_denied


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _runner() -> ModuleType:
    source = PROJECT_ROOT / "eval/phase5b_execution/runner.py"
    spec = importlib.util.spec_from_file_location(
        "_phase5b_execution_isolation_test_runner",
        source,
    )
    if spec is None or spec.loader is None:
        raise ImportError("Phase 5B execution runner cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_subprocess_probe_denies_truth_builder_and_evaluator_capabilities() -> None:
    runner = _runner()
    response = runner.worker_request(
        PROJECT_ROOT,
        {"mode": "probe"},
        environment={
            "PATH": "/usr/bin:/bin",
            "PHASE5B_AGENT_VISIBLE_ROOT": "/safe/agent-visible",
            "PHASE5B_GROUND_TRUTH_ROOT": "/private/truth",
            "PHASE5B_HIDDEN_PACK_ROOT": "/private/pack",
            "PHASE5B_EVALUATOR_TRUTH_ROOT": "/private/evaluator",
            "PHASE5B_BUILDER_ROOT": "/private/builder",
        },
    )

    assert response["schema_version"] == "phase5b.worker-isolation-probe.v1"
    assert response["provider_network_calls"] == 0
    assert response["truth_environment_present"] is False
    assert response["isolated_request_fields"] is True
    assert response["denied"] == {
        "builder_logs": "DENIED",
        "builder_source": "DENIED",
        "eval_import": "DENIED",
        "external_ground_truth": "DENIED",
        "phase1_ground_truth": "DENIED",
        "phase4_ground_truth": "DENIED",
    }


def test_subprocess_rejects_extra_worker_request_fields() -> None:
    with pytest.raises(RuntimeError, match="isolated Phase 5B worker failed"):
        _runner().worker_request(
            PROJECT_ROOT,
            {"mode": "probe", "ground_truth_root": "/forbidden"},
            environment={"PATH": "/usr/bin:/bin"},
        )


def test_python_guard_denies_symlink_alias_to_external_ground_truth(
    tmp_path: Path,
) -> None:
    truth = tmp_path / "external-pack/ground-truth"
    truth.mkdir(parents=True)
    alias = tmp_path / "safe-looking-alias"
    alias.symlink_to(truth, target_is_directory=True)

    assert _path_is_denied(alias / "hidden.json", PROJECT_ROOT) is True
