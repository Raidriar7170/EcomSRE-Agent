"""Strict CLI for the two-invocation live sandbox protocol."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

# This repository intentionally uses ``package = false``. Keep the documented
# ``python -m scripts.live_sandbox.cli`` entry point runnable without requiring
# callers to manufacture a PYTHONPATH.
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ecomsre.live_sandbox.contracts import (
    ApprovalRequest,
    canonical_json_bytes,
    write_private_json,
)
from ecomsre.live_sandbox.control import approve_plan
from ecomsre.live_sandbox.workflow import (
    ManualCleanupRequired,
    PrivateRoots,
    run_invocation_a,
    run_invocation_b,
)


def _path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path == Path("/"):
        raise argparse.ArgumentTypeError("root path is forbidden")
    return path


def _roots(arguments: argparse.Namespace) -> PrivateRoots:
    return PrivateRoots(
        control=arguments.control_root,
        runtime=arguments.runtime_root,
        telemetry=arguments.telemetry_root,
        report=arguments.report_root,
    )


def _add_roots(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--control-root", required=True, type=_path)
    parser.add_argument("--runtime-root", required=True, type=_path)
    parser.add_argument("--telemetry-root", required=True, type=_path)
    parser.add_argument("--report-root", required=True, type=_path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecomsre-live-sandbox")
    commands = parser.add_subparsers(dest="command", required=True)
    invocation_a = commands.add_parser("invocation-a")
    invocation_a.add_argument("--repository-root", required=True, type=_path)
    _add_roots(invocation_a)
    approve = commands.add_parser("approve")
    approve.add_argument("--request", required=True, type=_path)
    approve.add_argument("--approver", required=True)
    approve.add_argument("--phrase", required=True)
    invocation_b = commands.add_parser("invocation-b")
    invocation_b.add_argument("--repository-root", required=True, type=_path)
    _add_roots(invocation_b)
    return parser


def _approve(arguments: argparse.Namespace) -> dict[str, object]:
    request_path: Path = arguments.request
    if request_path.is_symlink() or not request_path.is_file():
        raise RuntimeError("approval request is not a regular file")
    request = ApprovalRequest.model_validate_json(
        request_path.read_text(encoding="utf-8")
    )
    expected_phrase = f"APPROVE {request.scenario_id} {request.plan_template_sha256}"
    if arguments.phrase != expected_phrase:
        raise PermissionError("approval phrase does not exactly match the frozen request")
    record = approve_plan(
        request,
        approver=arguments.approver,
        now=datetime.now(timezone.utc),
    )
    output = request_path.with_name("human-approval.json")
    record_sha256 = write_private_json(output, record, create_once=True)
    return {
        "verdict": "HUMAN_APPROVAL_RECORDED",
        "mode": "HUMAN",
        "approval_request_id": request.approval_request_id,
        "record_sha256": record_sha256,
        "expires_at": record.expires_at,
    }


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "approve":
            result = _approve(arguments)
        elif arguments.command == "invocation-a":
            result = run_invocation_a(arguments.repository_root, _roots(arguments))
        elif arguments.command == "invocation-b":
            result = run_invocation_b(arguments.repository_root, _roots(arguments))
        else:
            raise RuntimeError("unsupported live sandbox command")
    except ManualCleanupRequired as error:
        print(
            json.dumps(
                {
                    "verdict": "BLOCKED_ROLLBACK_FAILED_MANUAL_CLEANUP_REQUIRED",
                    "manual_cleanup_command": error.command,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except Exception as error:
        print(
            json.dumps(
                {"verdict": "BLOCKED", "error_type": type(error).__name__},
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    sys.stdout.buffer.write(canonical_json_bytes(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
