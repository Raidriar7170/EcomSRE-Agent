from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ecomsre_live_sandbox.contracts import canonical_json_bytes  # noqa: E402
from ecomsre_live_sandbox.e2e_v6 import (  # noqa: E402
    record_human_approval_for_invocation_b,
    run_canonical_invocation_a,
    run_development_probe,
    run_invocation_b,
)
from ecomsre_live_sandbox.e2e_v6_contracts import (  # noqa: E402
    E2EV6PrivateRoots,
    load_e2e_v6_config,
)


CONFIG = ROOT / "config/live-fault-a0-controlled-remediation-e2e-v6"


def _private_root(value: str) -> Path:
    path = Path(value).expanduser()
    if path == Path("/") or not path.is_absolute():
        raise argparse.ArgumentTypeError("root path is forbidden")
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecomsre-live-fault-a0-e2e-v6")
    parser.add_argument("--private-root", required=True, type=_private_root)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("development-probe")
    commands.add_parser("canonical-invocation-a")
    approve = commands.add_parser("approve")
    approve.add_argument("--approver", required=True)
    approve.add_argument("--phrase", required=True)
    commands.add_parser("invocation-b")
    return parser


def _verify_private_terminal(roots: E2EV6PrivateRoots) -> dict[str, object]:
    candidates = [
        roots.invocation_b / "terminal.json",
        roots.invocation_a / "terminal.json",
        *(
            path / "terminal.json"
            for path in sorted(roots.development.glob("run-*"), reverse=True)
        ),
    ]
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value
    raise RuntimeError("v6 terminal was not created")


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    config = load_e2e_v6_config(CONFIG)
    roots = E2EV6PrivateRoots(arguments.private_root)
    try:
        if arguments.command == "development-probe":
            result = run_development_probe(config, roots)
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
            result = run_invocation_b(config, roots)
        else:  # pragma: no cover
            raise RuntimeError("unreachable command")
    except Exception:
        result = _verify_private_terminal(roots)
        sys.stdout.buffer.write(canonical_json_bytes(result))
        return 1
    sys.stdout.buffer.write(canonical_json_bytes(result))
    verdict = result.get("verdict") if isinstance(result, dict) else None
    success = {
        config.authority.development_success_terminal,
        config.authority.invocation_a_terminal,
        config.authority.invocation_b_success,
    }
    return 0 if arguments.command == "approve" or verdict in success else 1


if __name__ == "__main__":
    raise SystemExit(main())
