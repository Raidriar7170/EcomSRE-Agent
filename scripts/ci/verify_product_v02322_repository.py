#!/usr/bin/env python3
"""Run Product v0.2.3.2.2 history and repository-state gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from ecomsre.product.pilot.repository_state_v02322 import (
    verify_repository_state_v02322,
)
from scripts.ci.verify_product_v02322_history import verify_product_v02322_history


def verify_product_v02322_repository(root: Path) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    return {
        "history": verify_product_v02322_history(project),
        "repository_state": verify_repository_state_v02322(project),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    arguments = parser.parse_args(argv)
    print(json.dumps(verify_product_v02322_repository(arguments.root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_product_v02322_repository",)
