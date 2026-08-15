"""CLI for the staged live fault-to-A0 controlled-remediation E2E v2."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ecomsre_live_sandbox.contracts import canonical_json_bytes  # noqa: E402
from ecomsre_live_sandbox.e2e_v2 import (  # noqa: E402
    record_human_approval_for_invocation_b,
    run_canonical_invocation_a,
    run_diagnostic_preflight,
)
from ecomsre_live_sandbox.e2e_v2_contracts import (  # noqa: E402
    E2EV2Config,
    E2EV2PrivateRoots,
    load_e2e_v2_config,
)


def _private_root(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path == Path("/"):
        raise argparse.ArgumentTypeError("root path is forbidden")
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecomsre-live-fault-a0-e2e-v2")
    parser.add_argument("--private-root", type=_private_root, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("diagnostic-preflight")
    commands.add_parser("canonical-invocation-a")
    approve = commands.add_parser("approve")
    approve.add_argument("--approver", required=True)
    approve.add_argument("--phrase", required=True)
    commands.add_parser("invocation-b")
    commands.add_parser("verify-private-terminal")
    commands.add_parser("verify-public-result")
    return parser


def _verify_private_terminal(roots: E2EV2PrivateRoots) -> dict[str, object]:
    roots.verify()
    candidates = (
        roots.invocation_b / "terminal.json",
        roots.invocation_a / "terminal.json",
        roots.probe_root(2) / "terminal.json",
        roots.probe_root(1) / "terminal.json",
    )
    for path in candidates:
        if path.is_symlink():
            raise ValueError("private terminal is a symbolic link")
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("verdict"), str):
                raise ValueError("private terminal is malformed")
            return {
                "verdict": value["verdict"],
                "run_kind": value.get("run_kind"),
                "permissions": "VERIFIED",
            }
    raise RuntimeError("no private terminal exists")


def _verify_public_result(config: E2EV2Config) -> dict[str, object]:
    path = config.repository_root / config.reporting.public_result_json
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("public v2 result is unavailable")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("verdict"), str):
        raise ValueError("public v2 result is malformed")
    return {"verdict": value["verdict"], "public_result": "PRESENT"}


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    config = load_e2e_v2_config(
        REPOSITORY_ROOT / "config/live-fault-a0-controlled-remediation-e2e-v2"
    )
    roots = E2EV2PrivateRoots(arguments.private_root)
    try:
        if arguments.command == "diagnostic-preflight":
            result = run_diagnostic_preflight(config, roots)
        elif arguments.command == "canonical-invocation-a":
            result = run_canonical_invocation_a(config, roots)
        elif arguments.command == "approve":
            result = record_human_approval_for_invocation_b(
                config,
                roots,
                approver=arguments.approver,
                phrase=arguments.phrase,
            ).model_dump(mode="json")
        elif arguments.command == "invocation-b":
            from ecomsre_live_sandbox.e2e_v2 import run_invocation_b

            result = run_invocation_b(config, roots)
        elif arguments.command == "verify-private-terminal":
            result = _verify_private_terminal(roots)
        else:
            result = _verify_public_result(config)
    except Exception as error:
        result = {
            "verdict": "BLOCKED_E2E_V2_UNCLASSIFIED_RUNTIME_FAILURE",
            "error_type": type(error).__name__,
        }
        sys.stdout.buffer.write(canonical_json_bytes(result))
        return 1
    sys.stdout.buffer.write(canonical_json_bytes(result))
    verdict = result.get("verdict")
    success = {
        config.authority.diagnostic_success_terminal,
        config.authority.invocation_a_terminal,
        config.authority.invocation_b_success,
    }
    return 0 if arguments.command == "approve" or verdict in success else 1


if __name__ == "__main__":
    raise SystemExit(main())
