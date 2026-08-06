"""Isolated subprocess entrypoint for one truth-free Phase 5B run."""

from __future__ import annotations

import builtins
from functools import partial
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable, cast


_MAX_REQUEST_BYTES = 64 * 1024


class _CapabilityDenied(PermissionError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate worker request key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite worker request constant: {value}")


def _read_request() -> dict[str, object]:
    raw = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
    if len(raw) > _MAX_REQUEST_BYTES:
        raise ValueError("worker request exceeds size limit")
    payload = json.loads(
        raw.decode("utf-8", errors="strict"),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("worker request must be an object")
    return payload


def _candidate_path(candidate: object) -> Path | None:
    if isinstance(candidate, int):
        return None
    try:
        path = Path(os.fsdecode(candidate))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if path.is_absolute():
        return Path(os.path.abspath(os.fspath(path)))
    return Path(os.path.abspath(os.fspath(Path.cwd() / path)))


def _path_is_denied(candidate: object, project_root: Path) -> bool:
    resolved = _candidate_path(candidate)
    if resolved is None:
        return False
    try:
        resolved = resolved.resolve(strict=False)
    except OSError:
        return True
    denied_roots = (
        project_root / "eval/phase1/ground-truth",
        project_root / "eval/phase4/ground-truth",
    )
    if any(resolved == root or root in resolved.parents for root in denied_roots):
        return True
    folded_parts = tuple(part.casefold() for part in resolved.parts)
    return any(
        part == "ground-truth" or "phase5b-builder" in part
        for part in folded_parts
    )


def _install_isolation(project_root: Path) -> None:
    original_import = cast(Callable[..., Any], builtins.__import__)
    original_import_module = cast(Callable[..., Any], importlib.import_module)

    def guard_import_name(name: object) -> None:
        if isinstance(name, str) and (
            name == "eval"
            or name.startswith("eval.")
            or name.endswith(".evaluator")
        ):
            raise _CapabilityDenied("evaluator import capability denied")

    def guarded_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        guard_import_name(name)
        return original_import(name, globals, locals, fromlist, level)

    def guarded_import_module(name: str, package: str | None = None) -> object:
        guard_import_name(name)
        return original_import_module(name, package)

    def audit_guard(event: str, arguments: tuple[object, ...]) -> None:
        if event in {"open", "os.listdir", "os.scandir"} and arguments:
            if _path_is_denied(arguments[0], project_root):
                raise _CapabilityDenied("truth filesystem capability denied")
        elif event == "import" and arguments:
            guard_import_name(arguments[0])
        elif (
            event.startswith("ctypes.")
            or event == "subprocess.Popen"
            or event.startswith("os.exec")
            or event.startswith("os.spawn")
            or event.startswith("os.posix_spawn")
            or event in {"os.fork", "os.forkpty", "os.system"}
        ):
            raise _CapabilityDenied("nested runtime capability denied")

    sys.addaudithook(audit_guard)
    builtins.__import__ = guarded_import  # type: ignore[assignment]
    importlib.import_module = guarded_import_module  # type: ignore[assignment]


def _denied(operation: Callable[[], object]) -> str:
    try:
        resource = operation()
    except _CapabilityDenied:
        return "DENIED"
    if hasattr(resource, "close"):
        resource.close()  # type: ignore[union-attr]
    return "ALLOWED"


def _probe(project_root: Path) -> dict[str, object]:
    targets = {
        "phase1_ground_truth": (
            project_root / "eval/phase1/ground-truth/probe.json"
        ),
        "phase4_ground_truth": (
            project_root / "eval/phase4/ground-truth/probe.json"
        ),
        "external_ground_truth": (
            project_root.parent / "synthetic-pack/ground-truth/probe.json"
        ),
        "builder_source": (
            project_root / ".private/phase5b-builder/source.py"
        ),
        "builder_logs": (
            project_root / ".private/phase5b-builder/private.log"
        ),
    }
    denied = {
        key: _denied(partial(path.open, "rb"))
        for key, path in targets.items()
    }
    denied["eval_import"] = _denied(
        lambda: importlib.import_module("eval.phase1.run")
    )
    denied_markers = (
        "GROUND_TRUTH",
        "HIDDEN_PACK_ROOT",
        "EVALUATOR_TRUTH",
        "BUILDER",
    )
    return {
        "schema_version": "phase5b.worker-isolation-probe.v1",
        "provider_network_calls": 0,
        "truth_environment_present": any(
            any(marker in key.upper() for marker in denied_markers)
            for key in os.environ
        ),
        "isolated_request_fields": True,
        "denied": denied,
    }


def _run(
    project_root: Path,
    payload: dict[str, object],
    *,
    canary: bool,
) -> dict[str, object]:
    from ecomsre.model.gateway import OpenAICompatibleConfig
    from ecomsre.phase2.token_policy import MODEL_SNAPSHOT
    from ecomsre.phase5a.provider import OpenAICompatiblePhase5ABackend
    from ecomsre.phase5b.contracts import ExecutionSchedule
    from ecomsre.phase5b.protocol import load_strict_json

    from scripts.phase5b_execution.contracts import (
        PROVIDER_CANARY_RUN_ID,
        ScoredRunRequest,
    )
    from scripts.phase5b_execution.provider_adapter import execute_scored_run

    request = ScoredRunRequest.model_validate(
        {key: payload[key] for key in ("run_id", "template_id", "seed_id", "variant")},
        strict=True,
    )
    if canary:
        if (
            request.run_id != PROVIDER_CANARY_RUN_ID
            or request.template_id != "ad-partial-failure-complete"
            or request.seed_id != "seed-00"
            or request.variant != "SINGLE_AGENT_V2"
        ):
            raise ValueError("Provider canary request differs from the frozen public case")
    else:
        schedule = load_strict_json(
            project_root / "config/phase5b/execution-schedule.v1.json",
            ExecutionSchedule,
        )
        request.require_schedule_membership(schedule)
    config = OpenAICompatibleConfig.from_environment(os.environ)
    if config is None or config.model != MODEL_SNAPSHOT:
        raise ValueError("frozen Provider configuration is absent or mismatched")
    backend = OpenAICompatiblePhase5ABackend(
        config=config,
        timeout_seconds=120.0,
    )
    with tempfile.TemporaryDirectory(prefix="phase5b-public-seed-") as temporary:
        record = execute_scored_run(
            project_root=project_root,
            request=request,
            backend=backend,
            environment=os.environ,
            materialized_root=Path(temporary),
            evidence_class=(
                "UNSCORED_PROVIDER_CANARY" if canary else "ACTUAL_SCORED"
            ),
        )
    return record.model_dump(mode="json")


def main() -> int:
    project_root = Path(__file__).resolve(strict=True).parents[2]
    sys.path[:0] = [str(project_root / "src"), str(project_root)]
    _install_isolation(project_root)
    payload = _read_request()
    mode = payload.get("mode")
    if mode == "probe":
        if set(payload) != {"mode"}:
            raise ValueError("probe request fields are not exact")
        response = _probe(project_root)
    elif mode == "run":
        if set(payload) != {"mode", "run_id", "template_id", "seed_id", "variant"}:
            raise ValueError("run request fields are not exact")
        response = _run(project_root, payload, canary=False)
    elif mode == "canary":
        if set(payload) != {"mode", "run_id", "template_id", "seed_id", "variant"}:
            raise ValueError("canary request fields are not exact")
        response = _run(project_root, payload, canary=True)
    else:
        raise ValueError("worker mode is invalid")
    sys.stdout.write(
        json.dumps(
            response,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
