"""CLI boundary for the one human-approved live fault-to-A0 successor."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ecomsre_live_sandbox.contracts import canonical_json_bytes  # noqa: E402
from ecomsre_live_sandbox.e2e_contracts import (  # noqa: E402
    E2EPrivateRoots,
    load_e2e_config,
)
from ecomsre_live_sandbox.e2e_v1 import (  # noqa: E402
    record_human_approval_for_invocation_b,
    run_invocation_a,
    run_invocation_b,
)


def _private_root(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path == Path("/"):
        raise argparse.ArgumentTypeError("root path is forbidden")
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecomsre-live-fault-a0-e2e-v1")
    parser.add_argument("--private-root", type=_private_root, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("invocation-a")
    approve = commands.add_parser("approve")
    approve.add_argument("--approver", required=True)
    approve.add_argument("--phrase", required=True)
    commands.add_parser("invocation-b")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    config = load_e2e_config(REPOSITORY_ROOT / "config/live-fault-a0-controlled-remediation-e2e-v1")
    roots = E2EPrivateRoots(arguments.private_root)
    try:
        if arguments.command == "invocation-a":
            result = run_invocation_a(config, roots)
        elif arguments.command == "approve":
            result = record_human_approval_for_invocation_b(
                config,
                roots,
                approver=arguments.approver,
                phrase=arguments.phrase,
            ).model_dump(mode="json")
        else:
            result = run_invocation_b(config, roots)
    except Exception as error:
        result = {"verdict": "LIVE_E2E_TERMINAL_FAILURE", "error_type": type(error).__name__}
        sys.stdout.buffer.write(canonical_json_bytes(result))
        return 1
    sys.stdout.buffer.write(canonical_json_bytes(result))
    return 0 if result.get("verdict") in {
        config.authority.invocation_a_terminal,
        config.authority.invocation_b_success,
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
