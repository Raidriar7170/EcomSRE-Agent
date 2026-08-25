#!/usr/bin/env python3
"""Build the deterministic zero-Provider v2.3.2 admission matrix."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre.dta_v2.v23.evaluation_data_v232 import (
    build_admission_matrix_v232,
    load_evaluation_cases_v232,
    load_evaluation_truths_v232,
    load_evaluation_views_v232,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--evaluation-root",
        type=Path,
        default=Path("config/dta-v232/evaluation"),
    )
    args = parser.parse_args()
    root = args.repository_root.resolve()
    evaluation_root = (
        args.evaluation_root
        if args.evaluation_root.is_absolute()
        else root / args.evaluation_root
    )
    matrix = build_admission_matrix_v232(
        repository_root=root,
        cases=load_evaluation_cases_v232(evaluation_root / "cases.json"),
        truths=load_evaluation_truths_v232(evaluation_root / "truth.json"),
        views=load_evaluation_views_v232(
            evaluation_root / "ontology-views.json"
        ),
    )
    output = evaluation_root / "admission-matrix.json"
    output.write_text(matrix.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(matrix.status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
