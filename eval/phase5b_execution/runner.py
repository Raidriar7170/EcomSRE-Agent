"""Evaluator-side launcher for the isolated Phase 5B execution worker."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


_MAX_WORKER_RESPONSE_BYTES = 8 * 1024 * 1024
_WORKER_TIMEOUT_SECONDS = 180.0
_PASSTHROUGH_ENVIRONMENT = frozenset(
    {
        "ECOMSRE_LLM_API_KEY",
        "ECOMSRE_LLM_BASE_URL",
        "ECOMSRE_LLM_MODEL",
        "LANG",
        "LC_ALL",
        "PATH",
        "PHASE5B_AGENT_VISIBLE_ROOT",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TMPDIR",
    }
)
_DENIED_ENVIRONMENT_MARKERS = (
    "GROUND_TRUTH",
    "HIDDEN_PACK_ROOT",
    "EVALUATOR_TRUTH",
    "BUILDER",
)


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


def sanitized_subprocess_environment(
    environment: Mapping[str, str],
) -> dict[str, str]:
    sanitized = {
        key: value
        for key, value in environment.items()
        if key in _PASSTHROUGH_ENVIRONMENT
        and not any(marker in key.upper() for marker in _DENIED_ENVIRONMENT_MARKERS)
    }
    sanitized["PYTHONDONTWRITEBYTECODE"] = "1"
    return sanitized


def _sandbox_profile(project_root: Path) -> str:
    denied_roots = (
        project_root / "eval/phase1/ground-truth",
        project_root / "eval/phase4/ground-truth",
    )
    clauses = ["(version 1)", "(allow default)"]
    for root in denied_roots:
        literal = '"' + str(root.resolve(strict=True)).replace(
            "\\", "\\\\"
        ).replace('"', '\\"') + '"'
        clauses.append(f"(deny file-read* (subpath {literal}))")
        clauses.append(f"(deny file-write* (subpath {literal}))")
    return "\n".join(clauses)


def worker_request(
    project_root: Path,
    request: dict[str, object],
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    worker = (root / "scripts/phase5b_execution/worker_entrypoint.py").resolve(
        strict=True
    )
    worker.relative_to(root)
    command = [sys.executable, "-I", str(worker)]
    sandbox = Path("/usr/bin/sandbox-exec")
    if sandbox.is_file():
        command = [
            str(sandbox),
            "-p",
            _sandbox_profile(root),
            *command,
        ]
    completed = subprocess.run(
        command,
        input=(_canonical_json(request) + "\n").encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=root,
        env=sanitized_subprocess_environment(
            os.environ if environment is None else environment
        ),
        timeout=_WORKER_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(f"isolated Phase 5B worker failed: {detail}")
    if len(completed.stdout) > _MAX_WORKER_RESPONSE_BYTES:
        raise ValueError("isolated Phase 5B worker response exceeds size limit")
    payload = json.loads(
        completed.stdout.decode("utf-8", errors="strict"),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("isolated Phase 5B worker response must be an object")
    return payload
