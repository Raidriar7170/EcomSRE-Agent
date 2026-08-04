"""Generate and verify Phase 5A capability-parity artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ecomsre.phase4.cli import _report_bytes, _write_atomic
from ecomsre.phase5a.demo import build_phase5a_demo_report
from ecomsre.phase5a.evaluation import run_capability_parity_evaluation
from ecomsre.phase5a.provider import (
    build_provider_request_shape_summary,
    run_provider_order_isolation,
    run_provider_pilot,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _compare(output: Path) -> int:
    report = run_capability_parity_evaluation(PROJECT_ROOT)
    content = _report_bytes(report)
    _write_atomic(output, content)
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(output),
                "bytes": len(content),
                "semantic_sha256": report["deterministic_semantic_sha256"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if report["status"] == "COMPLETED" else 1


def _verify(report_path: Path) -> int:
    if report_path.is_symlink() or not report_path.is_file():
        raise ValueError("Phase 5A report must be an existing regular file")
    observed = report_path.read_bytes()
    expected_report = run_capability_parity_evaluation(PROJECT_ROOT)
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


def _demo(output: Path) -> int:
    report = build_phase5a_demo_report(PROJECT_ROOT)
    _write_atomic(output, _report_bytes(report))
    print(
        json.dumps(
            {
                "status": "COMPLETED",
                "report": str(output),
                "decision": report["judge_decision"],
                "workflow_failure": False,
                "live_mutation": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _provider_pilot(output: Path) -> int:
    report = run_provider_pilot(PROJECT_ROOT)
    _write_atomic(output, _report_bytes(report))
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(output),
                "configured": report["configured"],
                "run_count": report["run_count"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if report["status"] in {"PASSED", "SKIPPED_NOT_CONFIGURED"} else 1


def _provider_request_shapes(output: Path) -> int:
    report = build_provider_request_shape_summary(PROJECT_ROOT)
    content = _report_bytes(report)
    _write_atomic(output, content)
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(output),
                "bytes": len(content),
                "entry_count": report["entry_count"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _provider_order_isolation(output: Path) -> int:
    report = run_provider_order_isolation(PROJECT_ROOT)
    _write_atomic(output, _report_bytes(report))
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(output),
                "run_count": report["run_count"],
                "provider_call_count": report["provider_call_count"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if report["status"] == "COMPLETED" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    compare = subparsers.add_parser("compare")
    compare.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--report", type=Path, required=True)
    demo = subparsers.add_parser("demo")
    demo.add_argument("--output", type=Path, required=True)
    provider = subparsers.add_parser("provider-pilot")
    provider.add_argument("--output", type=Path, required=True)
    request_shapes = subparsers.add_parser("provider-request-shapes")
    request_shapes.add_argument("--output", type=Path, required=True)
    order_isolation = subparsers.add_parser("provider-order-isolation")
    order_isolation.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "compare":
        return _compare(arguments.output)
    if arguments.command == "verify":
        return _verify(arguments.report)
    if arguments.command == "demo":
        return _demo(arguments.output)
    if arguments.command == "provider-pilot":
        return _provider_pilot(arguments.output)
    if arguments.command == "provider-request-shapes":
        return _provider_request_shapes(arguments.output)
    return _provider_order_isolation(arguments.output)


if __name__ == "__main__":
    raise SystemExit(main())
