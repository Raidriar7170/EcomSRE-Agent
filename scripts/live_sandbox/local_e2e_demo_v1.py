from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ecomsre_live_sandbox.contracts import canonical_json_bytes  # noqa: E402
from ecomsre_live_sandbox.local_demo_contracts import (  # noqa: E402
    LOCAL_DEMO_CONFIG_RELATIVE,
    LocalDemoPrivateRoot,
    load_local_demo_config,
    validate_provider_env,
)
from ecomsre_live_sandbox.local_e2e_demo_v1 import (  # noqa: E402
    run_local_demo,
)


PROVIDER_ENV = Path.home() / ".config/ecomsre/provider.env"


def _private_root(value: str) -> Path:
    path = Path(value).expanduser()
    if path == Path("/") or not path.is_absolute():
        raise argparse.ArgumentTypeError("private root path is forbidden")
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecomsre-local-e2e-demo-v1")
    parser.add_argument("--private-root", required=True, type=_private_root)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    config = load_local_demo_config(ROOT / LOCAL_DEMO_CONFIG_RELATIVE)
    top = LocalDemoPrivateRoot(arguments.private_root)
    provider_environment, safe_metadata = validate_provider_env(PROVIDER_ENV)
    result = run_local_demo(
        config,
        top,
        provider_environment=provider_environment,
    )
    output = {**result, "provider_configuration": safe_metadata}
    sys.stdout.buffer.write(canonical_json_bytes(output))
    return 0 if result.get("verdict") == config.authority.invocation_b_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
