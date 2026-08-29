#!/usr/bin/env python3
"""Check or execute Product v0.2.1 unknown-fault profile calibration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_ROOT = _REPOSITORY_ROOT / "src"
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

from ecomsre.product.pilot.live_calibration_v021 import (  # noqa: E402
    CALIBRATION_PASS_V021,
    run_live_calibration_v021,
    verify_calibration_contract_v021,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=_REPOSITORY_ROOT,
    )
    parser.add_argument(
        "--execute-live",
        action="store_true",
        help=(
            "Start the owned local OTel Demo and consume the one-shot "
            "v0.2.1 calibration campaign."
        ),
    )
    parser.add_argument("--stabilization-seconds", type=int, default=30)
    parser.add_argument("--observation-seconds", type=int, default=30)
    arguments = parser.parse_args(argv)
    result = (
        run_live_calibration_v021(
            repository_root=arguments.repository_root,
            stabilization_seconds=arguments.stabilization_seconds,
            observation_seconds=arguments.observation_seconds,
        )
        if arguments.execute_live
        else verify_calibration_contract_v021(arguments.repository_root)
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    terminal = str(result.get("terminal", ""))
    return 0 if not arguments.execute_live or terminal == CALIBRATION_PASS_V021 else 2


if __name__ == "__main__":
    raise SystemExit(main())
