#!/usr/bin/env python3
"""Execute and freeze the deterministic DTA v2.3.3 runtime preflight."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre.dta_v2.v23.runtime_preflight_v233 import (
    run_runtime_preflight_v233,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--evaluation-root",
        type=Path,
        default=Path("config/dta-v233/evaluation"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/analysis/dta-v233-runtime-preflight.json"),
    )
    args = parser.parse_args()
    root = args.repository_root.resolve()
    preflight = run_runtime_preflight_v233(
        repository_root=root,
        evaluation_root=(root / args.evaluation_root).resolve(),
    )
    output = (root / args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        preflight.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    print(preflight.terminal)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
