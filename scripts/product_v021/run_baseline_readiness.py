#!/usr/bin/env python3
"""Check or execute the bounded Product v0.2.1 baseline-readiness campaign."""

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

from ecomsre.product.pilot.live_baseline_readiness_v021 import (  # noqa: E402
    READINESS_PASS_V021,
    run_live_baseline_readiness_v021,
    verify_baseline_readiness_contract_v021,
)
from ecomsre.product.pilot.baseline_readiness_v021 import (  # noqa: E402
    ReadinessChangeParameterV021,
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
        help="Start the owned local OTel Demo and consume one changed readiness attempt.",
    )
    parser.add_argument(
        "--change-parameter",
        choices=[
            item.value
            for item in ReadinessChangeParameterV021
            if item is not ReadinessChangeParameterV021.INITIAL
        ],
        help="Evidence-backed semantic parameter changed after a usable readiness audit.",
    )
    parser.add_argument(
        "--infrastructure-replacement-for",
        help="Exact prior run ID eligible for the one identical infrastructure replacement.",
    )
    arguments = parser.parse_args(argv)
    result = (
        run_live_baseline_readiness_v021(
            repository_root=arguments.repository_root,
            changed_parameter=(
                None
                if arguments.change_parameter is None
                else ReadinessChangeParameterV021(arguments.change_parameter)
            ),
            infrastructure_replacement_for_run_id=(
                arguments.infrastructure_replacement_for
            ),
        )
        if arguments.execute_live
        else verify_baseline_readiness_contract_v021(arguments.repository_root)
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    terminal = str(result.get("terminal", ""))
    return 0 if not arguments.execute_live or terminal == READINESS_PASS_V021 else 2


if __name__ == "__main__":
    raise SystemExit(main())
