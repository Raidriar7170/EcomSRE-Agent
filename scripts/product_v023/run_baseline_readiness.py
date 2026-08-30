"""Check or execute Product v0.2.3 fresh Baseline readiness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from ecomsre.product.pilot.baseline_attempts_v023 import BaselineChangedParameterV023
from ecomsre.product.pilot.live_baseline_readiness_v023 import (
    run_live_baseline_readiness_v023,
    verify_live_baseline_readiness_contract_v023,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts.product_v023.run_baseline_readiness")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument(
        "--changed-parameter",
        choices=(
            BaselineChangedParameterV023.CONNECTOR_QUERY_BINDING_SHA256.value,
            BaselineChangedParameterV023.SERVICE_ALIAS_BINDING_SHA256.value,
            BaselineChangedParameterV023.IMPLEMENTATION_REVISION_SHA256.value,
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    root = arguments.project_root.resolve(strict=True)
    result = (
        run_live_baseline_readiness_v023(
            repository_root=root,
            changed_parameter=(
                None
                if arguments.changed_parameter is None
                else BaselineChangedParameterV023(arguments.changed_parameter)
            ),
        )
        if arguments.execute_live
        else verify_live_baseline_readiness_contract_v023(root)
    )
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0 if str(result["terminal"]).endswith(("_PASS", "_READY")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
