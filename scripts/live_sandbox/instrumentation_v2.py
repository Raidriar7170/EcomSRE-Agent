"""CLI for typed no-fault live telemetry instrumentation v2."""

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
from ecomsre_live_sandbox.instrumentation_v2 import (  # noqa: E402
    SUCCESS_VERDICT,
    run_canonical_preflight,
    run_development_probe,
)


def _path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path == Path("/"):
        raise argparse.ArgumentTypeError("root path is forbidden")
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecomsre-live-telemetry-instrumentation-v2")
    parser.add_argument("--repository-root", type=_path, default=REPOSITORY_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("probe")
    probe.add_argument("--private-root", type=_path)
    canonical = commands.add_parser("canonical-preflight")
    canonical.add_argument("--private-root", type=_path)
    canonical.add_argument(
        "--implementation-ci-pass",
        action="store_true",
        help="bind admission to a separately verified exact-head offline CI pass",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "probe":
            result = run_development_probe(
                arguments.repository_root, arguments.private_root
            )
        elif arguments.command == "canonical-preflight":
            result = run_canonical_preflight(
                arguments.repository_root,
                arguments.private_root,
                implementation_ci_passed=arguments.implementation_ci_pass,
            )
        else:
            raise RuntimeError("unsupported instrumentation command")
    except Exception as error:
        verdict = (
            "BLOCKED_CANONICAL_INSTRUMENTATION_PREFLIGHT"
            if arguments.command == "canonical-preflight"
            else "BLOCKED_SOURCE_CONTRACT_UNRESOLVED"
        )
        print(
            json.dumps(
                {"verdict": verdict, "error_type": type(error).__name__},
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    sys.stdout.buffer.write(canonical_json_bytes(result))
    return 0 if result.get("verdict") in {"DEVELOPMENT_PROBE_AVAILABLE", SUCCESS_VERDICT} else 1


if __name__ == "__main__":
    raise SystemExit(main())
