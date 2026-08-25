#!/usr/bin/env python3
"""Execute the deterministic zero-Provider v2.3.2 totality preflight."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre.dta_v2.v23.evaluation_data_v232 import (
    load_evaluation_cases_v232,
    load_evaluation_views_v232,
)
from ecomsre.dta_v2.v23.runtime_preflight_v232 import (
    build_runtime_totality_preflight_v232,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--evaluation-root",
        type=Path,
        default=Path("config/dta-v232/evaluation"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/analysis/dta-v232-runtime-totality-preflight.json"),
    )
    args = parser.parse_args()
    root = args.repository_root.resolve()
    evaluation_root = (
        args.evaluation_root
        if args.evaluation_root.is_absolute()
        else root / args.evaluation_root
    )
    output = args.output if args.output.is_absolute() else root / args.output
    preflight = build_runtime_totality_preflight_v232(
        repository_root=root,
        cases=load_evaluation_cases_v232(evaluation_root / "cases.json"),
        views=load_evaluation_views_v232(
            evaluation_root / "ontology-views.json"
        ),
    )
    output.write_text(
        preflight.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    print(preflight.status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
