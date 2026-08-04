"""Evaluator-only subprocess boundary for Phase 4 replay workers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


_MAX_WORKER_RESPONSE_BYTES = 8 * 1024 * 1024
_WORKER_TIMEOUT_SECONDS = 60.0


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate worker response key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite worker response constant: {value}")


def _sandbox_profile(project_root: Path, evaluator_relative: Path) -> str:
    evaluator_root = (project_root / evaluator_relative).resolve(strict=True)
    literal = '"' + str(evaluator_root).replace("\\", "\\\\").replace(
        '"', '\\"'
    ) + '"'
    return "\n".join(
        (
            "(version 1)",
            "(allow default)",
            f"(deny file-read* (subpath {literal}))",
            f"(deny file-write* (subpath {literal}))",
            "(deny network*)",
        )
    )


def worker_request(
    project_root: Path,
    request: dict[str, object],
    *,
    worker_relative: Path = Path("src/ecomsre/phase4/replay_worker.py"),
    evaluator_relative: Path = Path("eval/phase4"),
) -> object:
    """Run one isolated worker outside the production subprocess surface."""

    root = Path(project_root).resolve(strict=True)
    sandbox = Path("/usr/bin/sandbox-exec")
    if not sandbox.is_file():
        raise RuntimeError("sandbox-exec is required for Phase 4 evaluation")
    worker = (root / worker_relative).resolve(strict=True)
    worker.relative_to(root)
    environment = dict(os.environ)
    for name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "ECOMSRE_LLM_BASE_URL",
        "ECOMSRE_LLM_API_KEY",
        "ECOMSRE_LLM_MODEL",
    ):
        environment.pop(name, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [
            str(sandbox),
            "-p",
            _sandbox_profile(root, evaluator_relative),
            sys.executable,
            "-I",
            str(worker),
        ],
        input=(_canonical_json(request) + "\n").encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=root,
        env=environment,
        timeout=_WORKER_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(f"isolated Phase 4 worker failed: {detail}")
    if len(completed.stdout) > _MAX_WORKER_RESPONSE_BYTES:
        raise ValueError("isolated Phase 4 worker response exceeds size limit")
    return json.loads(
        completed.stdout.decode("utf-8", errors="strict"),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )
