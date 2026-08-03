"""Offline Phase 2 comparison generation and verification CLI."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORT = PROJECT_ROOT / "artifacts/phase2/comparison/comparison-report.json"


def _load_evaluator() -> ModuleType:
    module_name = "_ecomsre_phase2_cli_evaluator"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    source = PROJECT_ROOT / "eval/phase2/compare.py"
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise ImportError("Phase 2 evaluator spec cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _report_bytes(report: object) -> bytes:
    return (
        json.dumps(
            report,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_atomic(path: Path, content: bytes) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("comparison report target must be a regular file")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _compare(output: Path) -> int:
    report = _load_evaluator().run_comparison(PROJECT_ROOT)
    content = _report_bytes(report)
    _write_atomic(output, content)
    print(
        json.dumps(
            {
                "status": "COMPLETED",
                "report": str(output),
                "bytes": len(content),
                "semantic_sha256": report["deterministic_semantic_sha256"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _verify(report_path: Path) -> int:
    if report_path.is_symlink() or not report_path.is_file():
        raise ValueError("comparison report must be an existing regular file")
    observed = report_path.read_bytes()
    expected_report = _load_evaluator().run_comparison(PROJECT_ROOT)
    expected = _report_bytes(expected_report)
    status = "VERIFIED" if observed == expected else "MISMATCH"
    print(
        json.dumps(
            {
                "status": status,
                "report": str(report_path),
                "semantic_sha256": expected_report[
                    "deterministic_semantic_sha256"
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if status == "VERIFIED" else 1


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    verify = commands.add_parser("verify")
    verify.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.command == "compare":
            return _compare(args.output)
        return _verify(args.report)
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(
            json.dumps(
                {"status": "FAILED", "error": type(error).__name__},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
