"""Offline generate/verify CLI for the Phase 3 minimum replay evaluation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from ecomsre.phase3.evaluation import run_minimum_evaluation


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
        raise ValueError("Phase 3 report target must be a regular file")
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


def _replay(output: Path) -> int:
    report = run_minimum_evaluation()
    content = _report_bytes(report)
    _write_atomic(output, content)
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(output),
                "semantic_sha256": report["deterministic_semantic_sha256"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if report["status"] == "PASSED" else 1


def _verify(report_path: Path) -> int:
    if report_path.is_symlink() or not report_path.is_file():
        raise ValueError("Phase 3 report must be an existing regular file")
    observed = report_path.read_bytes()
    expected_report = run_minimum_evaluation()
    expected = _report_bytes(expected_report)
    status = "VERIFIED" if observed == expected else "MISMATCH"
    print(
        json.dumps(
            {
                "status": status,
                "report": str(report_path),
                "semantic_sha256": expected_report["deterministic_semantic_sha256"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if status == "VERIFIED" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    replay = subparsers.add_parser("replay")
    replay.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "replay":
        return _replay(arguments.output)
    return _verify(arguments.report)


if __name__ == "__main__":
    raise SystemExit(main())
