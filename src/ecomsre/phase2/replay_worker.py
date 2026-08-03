"""Isolated subprocess entrypoint for one Phase 2 replay workflow.

Only standard-library modules are imported until the Phase 1 capability guards
have sanitized ``sys.path`` and denied evaluator, network, and process escape.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


_MAX_REQUEST_BYTES = 64 * 1024


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


def _project_root(payload: dict[str, object]) -> Path:
    value = payload.get("project_root")
    if not isinstance(value, str):
        raise ValueError("project_root must be a string")
    root = Path(value)
    if not root.is_absolute() or root.resolve(strict=True) != Path.cwd().resolve(
        strict=True
    ):
        raise ValueError("project_root must be the exact worker cwd")
    return root


def _install_isolation(project_root: Path) -> ModuleType:
    evaluator_root = (project_root / "eval").resolve(strict=True)
    src_root = (project_root / "src").resolve(strict=True)
    sys.path.insert(0, str(src_root))
    from ecomsre.phase1 import replay_worker as isolation

    isolation._sanitize_import_path(project_root)
    isolation._install_guards(project_root)

    def deny_all_evaluator_reads(
        event: str,
        arguments: tuple[object, ...],
    ) -> None:
        if event == "open" and arguments:
            isolation._guard_candidate(
                arguments[0],
                evaluator_root=evaluator_root,
            )
        elif event in {"os.listdir", "os.scandir"} and arguments:
            isolation._guard_candidate(
                arguments[0],
                evaluator_root=evaluator_root,
            )

    sys.addaudithook(deny_all_evaluator_reads)
    return isolation


def _run(
    project_root: Path,
    case_id: str,
    variant_value: str,
) -> dict[str, object]:
    from ecomsre.backends.replay import load_replay_case
    from ecomsre.phase2.contracts import Phase2Variant
    from ecomsre.phase2.workflows import run_replay_workflow

    variant = Phase2Variant(variant_value)
    replay_case = load_replay_case(
        project_root / "config/phase1/replay-cases/agent-visible",
        case_id,
    )
    result = run_replay_workflow(
        project_root=project_root,
        replay_case=replay_case,
        variant=variant,
        allow_refinement=variant is Phase2Variant.DYNAMIC_MULTI_AGENT,
    )
    return result.trace.model_dump(mode="json")


def main() -> int:
    payload = _read_request()
    project_root = _project_root(payload)
    isolation = _install_isolation(project_root)
    mode = payload.get("mode")
    if mode == "probe":
        if set(payload) != {"mode", "project_root"}:
            raise ValueError("probe request fields are not exact")
        response = isolation._probe(project_root)
        response["phase2_evaluator_read"] = isolation._denied(
            lambda: (project_root / "eval/phase2/compare.py").read_bytes()
        )
    elif mode == "run":
        if set(payload) != {"mode", "project_root", "case_id", "variant"}:
            raise ValueError("run request fields are not exact")
        case_id = payload.get("case_id")
        variant = payload.get("variant")
        if not isinstance(case_id, str) or not isinstance(variant, str):
            raise ValueError("case_id and variant must be strings")
        response = _run(project_root, case_id, variant)
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
