"""Check or execute Product v0.2.3 restart and one No-Fault acceptance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from ecomsre.product.pilot.live_nofault_acceptance_v023 import (
    run_live_nofault_acceptance_v023,
    verify_live_nofault_contract_v023,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts.product_v023.run_nofault_acceptance"
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--execute-live", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    root = arguments.project_root.resolve(strict=True)
    result = (
        run_live_nofault_acceptance_v023(repository_root=root)
        if arguments.execute_live
        else verify_live_nofault_contract_v023(root)
    )
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    terminal = str(result["terminal"])
    return (
        0
        if terminal.endswith(("_READY", "_SUPPORTED", "_LIMITED", "_VERIFIED"))
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
