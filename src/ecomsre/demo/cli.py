"""Command line entry point for the offline Agent Mainline V1 demo."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from ecomsre.demo.runtime import (
    AgentMainlineReport,
    canonical_report_bytes,
    run_agent_mainline_demo,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _write_atomic(path: Path, content: bytes) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("demo report target must be a regular file")
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


def _print_summary(report: AgentMainlineReport) -> None:
    print("EcomSRE-Agent Mainline V1 (offline replay)")
    print(f"Case: {report.case}")
    print(f"Diagnosis variant: {report.diagnosis.variant.value}")
    print(f"Diagnosis backend: {report.diagnosis.backend}")
    print(f"Decision: {report.diagnosis.decision.value}")
    print(f"Root service: {report.diagnosis.root_service}")
    print(f"Fault mechanism: {report.diagnosis.fault_mechanism.value}")
    print(f"Supporting evidence count: {report.diagnosis.supporting_evidence_count}")
    print(f"Selected remediation action: {report.remediation.selected_action.value}")
    print(f"Policy decision: {report.remediation.policy_decision.value}")
    print(f"Approval mode: {report.remediation.approval_mode.value}")
    print(f"Forward mutation count: {report.remediation.forward_mutation_count}")
    print(f"Verification result: {report.remediation.verification_result.value}")
    print(f"Rollback count: {report.remediation.rollback_count}")
    print(f"Terminal status: {report.remediation.terminal_status.value}")
    print(
        "Model/tool/token usage: "
        f"{report.usage.model_calls}/{report.usage.tool_calls}/"
        f"{report.usage.total_tokens}"
    )
    print(f"Semantic SHA-256: {report.semantic_sha256}")
    print("Provider called: false")
    print("Docker called: false")
    print("Live execution: false")


def _run(output: Path) -> int:
    report = run_agent_mainline_demo(PROJECT_ROOT)
    _write_atomic(output, canonical_report_bytes(report))
    _print_summary(report)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "artifacts/demo/agent-mainline-v1-report.json",
    )
    arguments = parser.parse_args(argv)
    if arguments.command == "run":
        return _run(arguments.output)
    raise AssertionError("unreachable demo command")


if __name__ == "__main__":
    raise SystemExit(main())
