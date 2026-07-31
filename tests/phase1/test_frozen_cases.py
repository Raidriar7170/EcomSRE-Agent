from __future__ import annotations

import ast
import builtins
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ecomsre.backends.replay import (
    ReplayObservabilityBackend,
    load_replay_case,
)
from ecomsre.model.scripted import ScriptedModelGateway
from ecomsre.phase1.agent import SingleAgent
from ecomsre.phase1.contracts import (
    BudgetLimits,
    EvidenceSource,
    InvestigationRequest,
    ModelConfiguration,
    RCADecision,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VISIBLE_ROOT = PROJECT_ROOT / "config/phase1/replay-cases/agent-visible"
EVAL_PHASE1_ROOT = PROJECT_ROOT / "eval/phase1"
GROUND_TRUTH_ROOT = EVAL_PHASE1_ROOT / "ground-truth"

EXPECTED_DECISIONS = {
    "ad-partial-failure-complete": "RCA_CONFIRMED",
    "ad-partial-failure-without-logs": "RCA_CONFIRMED",
    "ad-partial-failure-frontend-decoy": "RCA_CONFIRMED",
    "ad-change-with-normal-sli": "ABSTAIN",
    "telemetry-insufficient": "NEED_MORE_EVIDENCE",
    "no-real-incident": "ABSTAIN",
    "recommendation-cache-failure": "RCA_CONFIRMED",
}
EXPECTED_ROOTS = {
    "ad-partial-failure-complete": "ad",
    "ad-partial-failure-without-logs": "ad",
    "ad-partial-failure-frontend-decoy": "ad",
    "ad-change-with-normal-sli": None,
    "telemetry-insufficient": None,
    "no-real-incident": None,
    "recommendation-cache-failure": "recommendation",
}
EXPECTED_MECHANISMS = {
    "ad-partial-failure-complete": "runtime_configuration_failure",
    "ad-partial-failure-without-logs": "request_processing_failure",
    "ad-partial-failure-frontend-decoy": "request_processing_failure",
    "ad-change-with-normal-sli": None,
    "telemetry-insufficient": None,
    "no-real-incident": None,
    "recommendation-cache-failure": "cache_backend_timeout",
}
EXPECTED_REPLAY_FILES = {
    "manifest.json",
    "incident.json",
    "metrics.json",
    "logs.json",
    "traces.json",
    "changes.json",
}
DATA_FILES = EXPECTED_REPLAY_FILES - {"manifest.json"}
OBSERVATION_FILES = {
    "metrics.json",
    "logs.json",
    "traces.json",
    "changes.json",
}
INCIDENT_KEYS = {
    "schema_version",
    "incident_id",
    "alert_source_service",
    "summary",
    "started_at",
    "ended_at",
    "affected_sli",
    "severity",
}
OBSERVATION_KEYS = {
    "service",
    "started_at",
    "ended_at",
    "observation_type",
    "attributes",
    "limitations",
}
GROUND_TRUTH_KEYS = {
    "schema_version",
    "case_id",
    "expected_decision",
    "expected_root_service",
    "expected_fault_mechanism",
    "decoys",
}
DECOY_KEYS = {"source", "service", "observation_type"}
DECOY_RESISTANCE_DENOMINATOR = 1
START = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
END = datetime(2026, 7, 31, 8, 5, tzinfo=UTC)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _assert_canonical_json(path: Path, payload: object) -> None:
    expected = (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode()
    assert path.read_bytes() == expected


def _parse_utc(value: object) -> datetime:
    assert type(value) is str
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == UTC.utcoffset(parsed)
    return parsed


def _observation_documents(case_id: str) -> dict[str, dict[str, Any]]:
    case_dir = VISIBLE_ROOT / case_id
    return {filename: _read_json(case_dir / filename) for filename in OBSERVATION_FILES}


def _native_signal_sources(
    case_id: str,
    *,
    service: str,
    mechanism: str,
) -> set[str]:
    sources: set[str] = set()
    for filename, document in _observation_documents(case_id).items():
        for observation in document["observations"]:
            if observation["service"] != service:
                continue
            source = filename.removesuffix(".json").upper()
            attributes = observation["attributes"]
            observation_type = observation["observation_type"]
            if source == "METRICS" and attributes.get("anomaly") is True:
                if (
                    mechanism == "request_processing_failure"
                    and observation_type == "request_handler_failure_rate"
                    and attributes.get("component_role") == "request_handler"
                    and attributes.get("outcome") == "failure"
                ):
                    sources.add(source)
                elif (
                    mechanism == "cache_backend_timeout"
                    and observation_type == "cache_timeout_rate"
                    and attributes.get("dependency_role") == "cache"
                    and attributes.get("outcome") == "timeout"
                ):
                    sources.add(source)
            elif mechanism == "runtime_configuration_failure" and (
                (
                    source in {"TRACES", "LOGS"}
                    and attributes.get("diagnostic_kind")
                    == "configuration_parse_failure"
                )
                or (
                    source == "CHANGES"
                    and observation_type == "configuration_transition"
                    and attributes.get("transition") == "valid_to_invalid"
                )
            ):
                sources.add(source)
            elif mechanism == "request_processing_failure" and (
                (
                    source in {"TRACES", "LOGS"}
                    and attributes.get("component_role") == "request_handler"
                    and attributes.get("outcome") == "failure"
                )
                or (
                    source == "CHANGES"
                    and attributes.get("release_scope") == "request_path"
                    and attributes.get("risk_signal") == "request_handler_regression"
                )
            ):
                sources.add(source)
            elif mechanism == "cache_backend_timeout" and (
                source in {"TRACES", "LOGS"}
                and attributes.get("dependency_role") == "cache"
                and attributes.get("outcome") == "timeout"
            ):
                sources.add(source)
    return sources


def _reject_dynamic_imports(tree: ast.AST, source: Path) -> None:
    importlib_aliases = {"importlib"}
    import_module_aliases: set[str] = set()
    builtins_aliases = {"builtins"}
    builtin_import_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_aliases.add(alias.asname or alias.name)
                if alias.name == "builtins":
                    builtins_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            import_module_aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "import_module"
            )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            is_builtin_reference = (
                isinstance(value, ast.Name) and value.id == "__import__"
            ) or (
                isinstance(value, ast.Attribute)
                and value.attr == "__import__"
                and isinstance(value.value, ast.Name)
                and value.value.id in builtins_aliases
            )
            if is_builtin_reference:
                builtin_import_aliases.update(
                    target.id for target in targets if isinstance(target, ast.Name)
                )

    for node in ast.walk(tree):
        is_builtin_reference = (
            isinstance(node, ast.Name) and node.id == "__import__"
        ) or (
            isinstance(node, ast.Attribute)
            and node.attr == "__import__"
            and isinstance(node.value, ast.Name)
            and node.value.id in builtins_aliases
        )
        is_dynamic_call = isinstance(node, ast.Call) and (
            (
                isinstance(node.func, ast.Name)
                and node.func.id
                in {*import_module_aliases, *builtin_import_aliases, "__import__"}
            )
            or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in importlib_aliases
            )
        )
        if is_builtin_reference or is_dynamic_call:
            raise AssertionError(f"dynamic runtime import is not allowed: {source}")


def _reject_forbidden_runtime_capabilities(tree: ast.AST, source: Path) -> None:
    forbidden_import_roots = {
        "_ctypes",
        "cffi",
        "ctypes",
        "multiprocessing",
        "socket",
        "subprocess",
    }
    forbidden_os_calls = {
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "fork",
        "forkpty",
        "posix_spawn",
        "posix_spawnp",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "system",
    }
    os_aliases = {"os"}
    direct_os_capabilities: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_name = alias.name.partition(".")[0]
                if root_name in forbidden_import_roots:
                    raise AssertionError(
                        f"forbidden runtime import {root_name}: {source}"
                    )
                if alias.name == "os":
                    os_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            root_name = (node.module or "").partition(".")[0]
            if root_name in forbidden_import_roots:
                raise AssertionError(
                    f"forbidden runtime import {root_name}: {source}"
                )
            if node.module == "os":
                direct_os_capabilities.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name in forbidden_os_calls
                )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        forbidden_call = (
            isinstance(node.func, ast.Name)
            and node.func.id in direct_os_capabilities
        ) or (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in forbidden_os_calls
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in os_aliases
        )
        if forbidden_call:
            raise AssertionError(f"forbidden process capability: {source}")


def _loaded_ecomsre_runtime_sources() -> set[Path]:
    package_root = (PROJECT_ROOT / "src/ecomsre").resolve()
    pending = [
        "ecomsre.phase1.agent",
        "ecomsre.backends.replay",
        "ecomsre.model.scripted",
    ]
    visited: set[str] = set()
    sources: set[Path] = set()
    while pending:
        module_name = pending.pop()
        if module_name in visited:
            continue
        visited.add(module_name)
        module = sys.modules.get(module_name)
        if module is None:
            raise AssertionError(f"runtime module is not loaded: {module_name}")
        module_file = getattr(module, "__file__", None)
        if not isinstance(module_file, str):
            continue
        source = Path(module_file).resolve()
        if source.suffix != ".py" or not source.is_relative_to(package_root):
            continue
        sources.add(source)

        parts = module_name.split(".")
        pending.extend(".".join(parts[:width]) for width in range(1, len(parts)))
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        _reject_dynamic_imports(tree, source)
        _reject_forbidden_runtime_capabilities(tree, source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                pending.extend(
                    alias.name
                    for alias in node.names
                    if alias.name == "ecomsre" or alias.name.startswith("ecomsre.")
                )
            elif isinstance(node, ast.ImportFrom):
                imported_module = node.module or ""
                if node.level:
                    package_name = (
                        module_name
                        if source.name == "__init__.py"
                        else module_name.rpartition(".")[0]
                    )
                    imported_module = importlib.util.resolve_name(
                        "." * node.level + imported_module,
                        package_name,
                    )
                if imported_module == "ecomsre" or imported_module.startswith(
                    "ecomsre."
                ):
                    pending.append(imported_module)
                    pending.extend(
                        candidate
                        for alias in node.names
                        if (candidate := f"{imported_module}.{alias.name}")
                        in sys.modules
                    )
    return sources


@pytest.mark.parametrize(
    "source_text",
    (
        'import importlib\nimportlib.import_module("eval.phase1")',
        'import importlib as il\nil.import_module("eval.phase1")',
        'from importlib import import_module\nimport_module("eval.phase1")',
        'from importlib import import_module as loader\nloader("eval.phase1")',
        '__import__("eval.phase1")',
        'loader = __import__\nloader("eval.phase1")',
        'import builtins as b\nloader = b.__import__\nloader("eval.phase1")',
        'reference = __import__',
    ),
)
def test_runtime_import_guard_rejects_dynamic_import_aliases(
    source_text: str,
) -> None:
    tree = ast.parse(source_text)
    with pytest.raises(AssertionError, match="dynamic runtime import"):
        _reject_dynamic_imports(tree, Path("runtime.py"))


def test_runtime_import_guard_allows_static_imports() -> None:
    tree = ast.parse("from ecomsre.phase1 import contracts")
    _reject_dynamic_imports(tree, Path("runtime.py"))


@pytest.mark.parametrize(
    "source_text",
    (
        "import ctypes",
        "from subprocess import run",
        "import socket as network",
        "import os\nos.system('true')",
        "import os as platform\nplatform.posix_spawn('/bin/true', [], {})",
        "from os import fork as clone\nclone()",
    ),
)
def test_runtime_capability_guard_rejects_native_process_and_network_paths(
    source_text: str,
) -> None:
    with pytest.raises(AssertionError, match="forbidden"):
        _reject_forbidden_runtime_capabilities(
            ast.parse(source_text),
            Path("runtime.py"),
        )


def _path_for_directory_fd(directory_fd: int) -> Path:
    for descriptor_root in ("/proc/self/fd",):
        descriptor = Path(descriptor_root) / str(directory_fd)
        try:
            target = os.readlink(descriptor)
        except OSError:
            continue
        return Path(target).resolve()
    try:
        raw_path = fcntl.fcntl(directory_fd, 50, b"\0" * 1024)
    except (OSError, TypeError, ValueError) as error:
        raise AssertionError(
            "Agent used an unresolvable directory file descriptor"
        ) from error
    if not isinstance(raw_path, bytes):
        raise AssertionError("Agent used an unresolvable directory file descriptor")
    decoded = os.fsdecode(raw_path.split(b"\0", 1)[0])
    if not decoded:
        raise AssertionError("Agent used an unresolvable directory file descriptor")
    return Path(decoded).resolve()


def _assert_outside_evaluator(
    candidate: object,
    *,
    dir_fd: int | None = None,
) -> None:
    if isinstance(candidate, int):
        return
    if isinstance(candidate, bytes):
        candidate = os.fsdecode(candidate)
    try:
        candidate_path = Path(candidate)  # type: ignore[arg-type]
        if dir_fd is not None and not candidate_path.is_absolute():
            candidate_path = _path_for_directory_fd(dir_fd) / candidate_path
        resolved = candidate_path.resolve()
    except (OSError, TypeError, ValueError):
        return
    evaluator_root = EVAL_PHASE1_ROOT.resolve()
    if resolved == evaluator_root or evaluator_root in resolved.parents:
        raise AssertionError(f"Agent attempted evaluator file access: {resolved}")


@contextmanager
def _deny_evaluator_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    original_builtin_open = builtins.open
    original_io_open = io.open
    original_os_open = os.open
    original_path_open = Path.open
    original_path_read_text = Path.read_text
    original_path_read_bytes = Path.read_bytes

    def guarded_builtin_open(
        file: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        _assert_outside_evaluator(file)
        return original_builtin_open(file, *args, **kwargs)

    def guarded_io_open(
        file: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        _assert_outside_evaluator(file)
        return original_io_open(file, *args, **kwargs)

    def guarded_os_open(
        path: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        _assert_outside_evaluator(path, dir_fd=dir_fd)
        return original_os_open(path, flags, mode, dir_fd=dir_fd)

    def guarded_path_open(
        path: Path,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        _assert_outside_evaluator(path)
        return original_path_open(path, *args, **kwargs)

    def guarded_path_read_text(
        path: Path,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        _assert_outside_evaluator(path)
        return original_path_read_text(path, *args, **kwargs)

    def guarded_path_read_bytes(
        path: Path,
    ) -> bytes:
        _assert_outside_evaluator(path)
        return original_path_read_bytes(path)

    with monkeypatch.context() as scoped:
        scoped.setattr(builtins, "open", guarded_builtin_open)
        scoped.setattr(io, "open", guarded_io_open)
        scoped.setattr(os, "open", guarded_os_open)
        scoped.setattr(Path, "open", guarded_path_open)
        scoped.setattr(Path, "read_text", guarded_path_read_text)
        scoped.setattr(Path, "read_bytes", guarded_path_read_bytes)
        yield


def _assert_strict_scalar(value: object) -> None:
    assert type(value) in {str, int, float, bool, type(None)}


def test_exact_frozen_case_pairs_exist() -> None:
    assert not VISIBLE_ROOT.is_symlink()
    assert not GROUND_TRUTH_ROOT.is_symlink()
    visible_cases = {path.name for path in VISIBLE_ROOT.iterdir() if path.is_dir()}
    ground_truth_files = {path.name for path in GROUND_TRUTH_ROOT.iterdir()}

    assert visible_cases == set(EXPECTED_DECISIONS)
    assert {path.name for path in VISIBLE_ROOT.iterdir()} == visible_cases
    assert ground_truth_files == {f"{case_id}.json" for case_id in EXPECTED_DECISIONS}
    for case_id in EXPECTED_DECISIONS:
        case_dir = VISIBLE_ROOT / case_id
        assert not case_dir.is_symlink()
        assert {path.name for path in case_dir.iterdir()} == EXPECTED_REPLAY_FILES
        assert all(
            path.is_file() and not path.is_symlink() for path in case_dir.iterdir()
        )
        truth_path = GROUND_TRUTH_ROOT / f"{case_id}.json"
        assert truth_path.is_file()
        assert not truth_path.is_symlink()


def test_agent_visible_files_have_strict_canonical_hash_bound_contracts() -> None:
    null_hints = 0
    observed_hints: dict[str, object] = {}
    for case_id in EXPECTED_DECISIONS:
        case_dir = VISIBLE_ROOT / case_id
        payloads = {
            filename: _read_json(case_dir / filename)
            for filename in EXPECTED_REPLAY_FILES
        }
        for filename, payload in payloads.items():
            _assert_canonical_json(case_dir / filename, payload)

        manifest = payloads["manifest.json"]
        assert type(manifest) is dict
        assert set(manifest) == {"schema_version", "case_id", "files"}
        assert manifest["schema_version"] == "phase1.replay-manifest.v1"
        assert manifest["case_id"] == case_id
        assert type(manifest["files"]) is dict
        assert set(manifest["files"]) == DATA_FILES
        for filename in DATA_FILES:
            expected_sha256 = hashlib.sha256(
                (case_dir / filename).read_bytes()
            ).hexdigest()
            assert manifest["files"][filename] == expected_sha256

        incident = payloads["incident.json"]
        assert type(incident) is dict
        assert set(incident) == INCIDENT_KEYS
        assert incident["schema_version"] == "phase1.incident.v1"
        assert type(incident["incident_id"]) is str
        assert type(incident["summary"]) is str
        assert type(incident["affected_sli"]) is str
        assert incident["severity"] in {"SEV1", "SEV2", "SEV3"}
        assert _parse_utc(incident["started_at"]) == START
        assert _parse_utc(incident["ended_at"]) == END
        hint = incident["alert_source_service"]
        assert hint is None or type(hint) is str
        observed_hints[case_id] = hint
        null_hints += hint is None

        for filename in OBSERVATION_FILES:
            document = payloads[filename]
            assert type(document) is dict
            assert set(document) == {
                "schema_version",
                "status",
                "observations",
            }
            assert document["schema_version"] == "phase1.replay-observations.v1"
            assert document["status"] in {
                "AVAILABLE",
                "UNAVAILABLE",
                "TIMEOUT",
            }
            assert type(document["observations"]) is list
            if document["status"] != "AVAILABLE":
                assert document["observations"] == []
            for observation in document["observations"]:
                assert type(observation) is dict
                assert set(observation) == OBSERVATION_KEYS
                assert type(observation["service"]) is str
                assert type(observation["observation_type"]) is str
                started_at = _parse_utc(observation["started_at"])
                ended_at = _parse_utc(observation["ended_at"])
                assert START <= started_at <= ended_at <= END
                assert type(observation["attributes"]) is dict
                assert all(type(name) is str for name in observation["attributes"])
                for value in observation["attributes"].values():
                    _assert_strict_scalar(value)
                assert type(observation["limitations"]) is list
                assert all(type(item) is str for item in observation["limitations"])

    assert null_hints >= 5
    assert observed_hints == {
        case_id: (
            "frontend" if case_id == "ad-partial-failure-frontend-decoy" else None
        )
        for case_id in EXPECTED_DECISIONS
    }


def test_agent_visible_json_has_no_evaluator_answer_or_path_leakage() -> None:
    forbidden_keys = {
        "expected_decision",
        "expected_root_service",
        "expected_fault_mechanism",
        "ground_truth",
        "scenario_truth",
        "scenario_label",
        "evaluator_path",
        "answer_key",
    }
    forbidden_text = {
        "eval/phase1",
        "eval\\phase1",
        "ground-truth",
        "ground_truth",
        "scenario_truth",
        "scenario-label",
        "answer-key",
        "answer_key",
        "evaluator-only",
    }

    def visit(value: object) -> None:
        if type(value) is dict:
            assert forbidden_keys.isdisjoint(value)
            for key, nested in value.items():
                assert type(key) is str
                lowered = key.casefold()
                assert all(marker not in lowered for marker in forbidden_text)
                visit(nested)
        elif type(value) is list:
            for nested in value:
                visit(nested)
        elif type(value) is str:
            lowered = value.casefold()
            assert all(marker not in lowered for marker in forbidden_text)

    for path in VISIBLE_ROOT.glob("*/*.json"):
        visit(_read_json(path))


def test_evaluator_ground_truth_has_exact_bounded_schema() -> None:
    expected_decoy = {
        "source": "CHANGES",
        "service": "frontend",
        "observation_type": "deployment",
    }
    decoy_cases = 0
    for case_id, expected_decision in EXPECTED_DECISIONS.items():
        path = GROUND_TRUTH_ROOT / f"{case_id}.json"
        truth = _read_json(path)
        _assert_canonical_json(path, truth)
        assert type(truth) is dict
        assert set(truth) == GROUND_TRUTH_KEYS
        assert truth["schema_version"] == "phase1.ground-truth.v1"
        assert truth["case_id"] == case_id
        assert truth["expected_decision"] == expected_decision
        assert truth["expected_root_service"] == EXPECTED_ROOTS[case_id]
        assert truth["expected_fault_mechanism"] == EXPECTED_MECHANISMS[case_id]
        assert type(truth["decoys"]) is list
        assert all(
            type(decoy) is dict and set(decoy) == DECOY_KEYS
            for decoy in truth["decoys"]
        )
        if case_id == "ad-partial-failure-frontend-decoy":
            assert truth["decoys"] == [expected_decoy]
        else:
            assert truth["decoys"] == []
        decoy_cases += bool(truth["decoys"])

        if expected_decision != "RCA_CONFIRMED":
            assert truth["expected_root_service"] is None
            assert truth["expected_fault_mechanism"] is None

    # Decoy Resistance is currently measured on exactly one frozen case.
    assert decoy_cases == DECOY_RESISTANCE_DENOMINATOR == 1


def test_ground_truth_granularity_is_backed_by_visible_sources() -> None:
    for case_id, decision in EXPECTED_DECISIONS.items():
        if decision != "RCA_CONFIRMED":
            continue
        root = EXPECTED_ROOTS[case_id]
        mechanism = EXPECTED_MECHANISMS[case_id]
        assert root is not None
        assert mechanism is not None
        matching_sources = _native_signal_sources(
            case_id,
            service=root,
            mechanism=mechanism,
        )
        assert len(matching_sources) >= 2
        if mechanism == "runtime_configuration_failure":
            assert "CHANGES" in matching_sources

    decoy_changes = _read_json(
        VISIBLE_ROOT / "ad-partial-failure-frontend-decoy" / "changes.json"
    )
    assert decoy_changes["observations"] == [
        {
            "service": "frontend",
            "started_at": "2026-07-31T08:03:00Z",
            "ended_at": "2026-07-31T08:03:30Z",
            "observation_type": "deployment",
            "attributes": {
                "change_id": "frontend-ui-20260731",
                "release_scope": "request_path",
                "risk_signal": "request_handler_regression",
                "status": "completed",
            },
            "limitations": ["Timing alone does not establish causality."],
        }
    ]
    assert _native_signal_sources(
        "ad-partial-failure-frontend-decoy",
        service="frontend",
        mechanism="request_processing_failure",
    ) == {"CHANGES"}


def test_frozen_case_visible_scenario_matrix_is_exact() -> None:
    for case_id in EXPECTED_DECISIONS:
        for document in _observation_documents(case_id).values():
            for observation in document["observations"]:
                assert "fault_mechanism" not in observation["attributes"]

    assert _native_signal_sources(
        "ad-partial-failure-complete",
        service="ad",
        mechanism="runtime_configuration_failure",
    ) == {"LOGS", "TRACES", "CHANGES"}

    without_logs = _observation_documents("ad-partial-failure-without-logs")
    assert _native_signal_sources(
        "ad-partial-failure-without-logs",
        service="ad",
        mechanism="request_processing_failure",
    ) == {"METRICS", "TRACES"}
    assert without_logs["logs.json"] == {
        "schema_version": "phase1.replay-observations.v1",
        "status": "UNAVAILABLE",
        "observations": [],
    }

    assert _native_signal_sources(
        "ad-partial-failure-frontend-decoy",
        service="ad",
        mechanism="request_processing_failure",
    ) == {"METRICS", "TRACES"}

    normal_change = _observation_documents("ad-change-with-normal-sli")
    assert (
        normal_change["metrics.json"]["observations"][0]["attributes"]["anomaly"]
        is False
    )
    assert len(normal_change["changes.json"]["observations"]) == 1
    assert (
        "fault_mechanism"
        not in normal_change["changes.json"]["observations"][0]["attributes"]
    )

    insufficient = _observation_documents("telemetry-insufficient")
    insufficient_metric = insufficient["metrics.json"]["observations"][0]
    assert insufficient_metric["attributes"]["anomaly"] is True
    assert "fault_mechanism" not in insufficient_metric["attributes"]
    assert all(
        insufficient[f"{source}.json"]["observations"] == []
        for source in ("logs", "traces", "changes")
    )

    no_incident = _observation_documents("no-real-incident")
    assert (
        no_incident["metrics.json"]["observations"][0]["attributes"]["anomaly"] is False
    )
    assert no_incident["changes.json"]["observations"] == []
    assert all(
        "fault_mechanism" not in observation["attributes"]
        for document in no_incident.values()
        for observation in document["observations"]
    )

    recommendation = _observation_documents("recommendation-cache-failure")
    assert _native_signal_sources(
        "recommendation-cache-failure",
        service="recommendation",
        mechanism="cache_backend_timeout",
    ) == {"METRICS", "LOGS", "TRACES"}
    assert recommendation["changes.json"]["observations"] == []


def test_replay_loader_loads_every_frozen_case() -> None:
    for case_id in EXPECTED_DECISIONS:
        replay_case = load_replay_case(VISIBLE_ROOT, case_id)
        assert replay_case.case_id == case_id
        assert replay_case.incident.started_at == START
        assert replay_case.incident.ended_at == END


def test_loaded_agent_runtime_closure_has_no_evaluator_import_or_path() -> None:
    runtime_sources = _loaded_ecomsre_runtime_sources()
    required_package_initializers = {
        PROJECT_ROOT / "src/ecomsre/__init__.py",
        PROJECT_ROOT / "src/ecomsre/backends/__init__.py",
        PROJECT_ROOT / "src/ecomsre/model/__init__.py",
        PROJECT_ROOT / "src/ecomsre/phase1/__init__.py",
        PROJECT_ROOT / "src/ecomsre/tools/__init__.py",
    }
    assert required_package_initializers <= runtime_sources
    assert PROJECT_ROOT / "src/ecomsre/phase1/agent.py" in runtime_sources
    assert PROJECT_ROOT / "src/ecomsre/model/scripted.py" in runtime_sources
    assert PROJECT_ROOT / "src/ecomsre/backends/replay.py" in runtime_sources

    for path in runtime_sources:
        source = path.read_text(encoding="utf-8")
        lowered = source.casefold()
        assert "eval/phase1" not in lowered
        assert "ground-truth" not in lowered
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("eval") for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("eval")


def test_evaluator_read_guard_covers_supported_filesystem_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth_path = GROUND_TRUTH_ROOT / "ad-partial-failure-complete.json"
    visible_path = VISIBLE_ROOT / "ad-partial-failure-complete" / "incident.json"
    original_entries = (
        builtins.open,
        io.open,
        os.open,
        Path.open,
        Path.read_text,
        Path.read_bytes,
    )
    attempts: tuple[Callable[[], object], ...] = (
        lambda: builtins.open(truth_path, "rb"),
        lambda: io.open(truth_path, "rb"),
        lambda: os.close(os.open(truth_path, os.O_RDONLY)),
        lambda: truth_path.open("rb"),
        lambda: truth_path.read_text(encoding="utf-8"),
        truth_path.read_bytes,
    )
    evaluator_dir_fd = os.open(GROUND_TRUTH_ROOT, os.O_RDONLY)
    visible_dir_fd = os.open(visible_path.parent, os.O_RDONLY)

    try:
        with _deny_evaluator_reads(monkeypatch):
            for attempt in attempts:
                with pytest.raises(AssertionError, match="evaluator file access"):
                    attempt()

            with pytest.raises(AssertionError, match="evaluator file access"):
                os.close(
                    os.open(
                        truth_path.name,
                        os.O_RDONLY,
                        dir_fd=evaluator_dir_fd,
                    )
                )

            with builtins.open(visible_path, "rb") as stream:
                assert stream.read(1) == b"{"
            with io.open(visible_path, "rb") as stream:
                assert stream.read(1) == b"{"
            file_descriptor = os.open(visible_path, os.O_RDONLY)
            try:
                assert os.read(file_descriptor, 1) == b"{"
            finally:
                os.close(file_descriptor)
            relative_descriptor = os.open(
                visible_path.name,
                os.O_RDONLY,
                dir_fd=visible_dir_fd,
            )
            try:
                assert os.read(relative_descriptor, 1) == b"{"
            finally:
                os.close(relative_descriptor)
            with visible_path.open("rb") as stream:
                assert stream.read(1) == b"{"
            assert visible_path.read_text(encoding="utf-8").startswith("{")
            assert visible_path.read_bytes().startswith(b"{")
    finally:
        os.close(evaluator_dir_fd)
        os.close(visible_dir_fd)

    assert _read_json(truth_path)["case_id"] == "ad-partial-failure-complete"
    assert (
        builtins.open,
        io.open,
        os.open,
        Path.open,
        Path.read_text,
        Path.read_bytes,
    ) == original_entries


def test_runtime_imports_occur_under_evaluator_read_guard_in_clean_process() -> None:
    source_root = PROJECT_ROOT / "src"
    script = f"""
import os
import sys
from pathlib import Path

source_root = Path({str(source_root)!r}).resolve()
evaluator_root = Path({str(EVAL_PHASE1_ROOT)!r}).resolve()
sys.path.insert(0, str(source_root))

def audit(event, arguments):
    if event != "open" or not arguments:
        return
    candidate = arguments[0]
    if isinstance(candidate, int):
        return
    if isinstance(candidate, bytes):
        candidate = os.fsdecode(candidate)
    try:
        resolved = Path(candidate).resolve()
    except (OSError, TypeError, ValueError):
        return
    if resolved == evaluator_root or evaluator_root in resolved.parents:
        raise AssertionError(f"evaluator import-time read: {{resolved}}")

sys.addaudithook(audit)
from ecomsre.backends.replay import ReplayObservabilityBackend
from ecomsre.model.scripted import ScriptedModelGateway
from ecomsre.phase1.agent import SingleAgent

assert ReplayObservabilityBackend
assert ScriptedModelGateway
assert SingleAgent
print("IMPORT_GUARD_OK")
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "IMPORT_GUARD_OK\n"
    assert completed.stderr == ""


def test_scripted_single_agent_runs_all_frozen_cases_without_answer_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action_sequences: dict[str, tuple[str, ...]] = {}
    for index, case_id in enumerate(EXPECTED_DECISIONS, start=1):
        replay_case = load_replay_case(VISIBLE_ROOT, case_id)
        agent = SingleAgent(
            gateway=ScriptedModelGateway(),
            backend=ReplayObservabilityBackend(replay_case),
            model_configuration=ModelConfiguration(
                model_name="scripted-replay-v1",
                temperature=0.0,
                model_timeout_seconds=1.0,
            ),
            tool_timeout_seconds=0.5,
        )
        with _deny_evaluator_reads(monkeypatch):
            report = agent.run(
                InvestigationRequest(
                    schema_version="phase1.investigation-request.v1",
                    request_id=f"frozen-investigation-{index:02d}",
                    run_id=f"{index:032x}",
                    agent_id="single-agent",
                    task_id="root-cause-analysis",
                    incident=replay_case.incident,
                    budgets=BudgetLimits(
                        max_model_calls=8,
                        max_tool_calls=8,
                        max_total_tokens=12_000,
                    ),
                )
            )

        assert report.terminal_status == "COMPLETED"
        assert report.final_rca is not None
        evidence_by_ref = {
            evidence.evidence_ref: evidence for evidence in report.evidence_index
        }
        cited_refs = {
            *report.final_rca.supporting_evidence,
            *report.final_rca.contradicting_evidence,
        }
        assert cited_refs <= evidence_by_ref.keys()
        assert all(
            set(record.evidence_refs) <= evidence_by_ref.keys()
            for record in report.tool_call_records
        )
        action_sequences[case_id] = tuple(
            record.action.action_type for record in report.tool_call_records
        )

        # Evaluator-only data is deliberately opened after the validated report.
        truth = _read_json(GROUND_TRUTH_ROOT / f"{case_id}.json")
        assert report.final_rca.decision.value == truth["expected_decision"]
        if report.final_rca.decision is RCADecision.RCA_CONFIRMED:
            assert report.final_rca.root_service == truth["expected_root_service"]
            assert report.final_rca.fault_mechanism is not None
            assert (
                report.final_rca.fault_mechanism.value
                == truth["expected_fault_mechanism"]
            )
        elif report.final_rca.decision is RCADecision.ABSTAIN:
            assert report.final_rca.root_service is None
            assert report.final_rca.fault_mechanism is None
        else:
            assert report.final_rca.missing_evidence

        if case_id == "ad-partial-failure-frontend-decoy":
            decoy = next(
                evidence
                for evidence in report.evidence_index
                if evidence.source is EvidenceSource.CHANGES
                and evidence.service == "frontend"
                and evidence.observation_type == "deployment"
            )
            assert decoy.evidence_ref not in (report.final_rca.supporting_evidence)
            assert decoy.evidence_ref in report.tool_call_records[-1].evidence_refs

    assert len(set(action_sequences.values())) >= 4
    assert action_sequences["recommendation-cache-failure"] == (
        "metrics",
        "traces",
    )
    assert "logs" not in action_sequences["recommendation-cache-failure"]
    assert "changes" not in action_sequences["recommendation-cache-failure"]
