#!/usr/bin/env python3
"""Build the zero-Provider admission proof for the v2.3.1 successor set."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre.dta_v2.v23.evaluation_successor_v231 import (
    build_admission_matrix_v231_successor,
    load_successor_case_set_v231,
    load_successor_truth_set_v231,
    load_successor_views_v231,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--evaluation-root",
        type=Path,
        default=Path("config/dta-v231-successor/evaluation"),
    )
    args = parser.parse_args()
    repository_root = args.repository_root.resolve()
    evaluation_root = (
        args.evaluation_root
        if args.evaluation_root.is_absolute()
        else repository_root / args.evaluation_root
    )
    matrix = build_admission_matrix_v231_successor(
        repository_root=repository_root,
        cases=load_successor_case_set_v231(evaluation_root / "cases.json"),
        truths=load_successor_truth_set_v231(evaluation_root / "truth-index.json"),
        views=load_successor_views_v231(evaluation_root / "ontology-views.json"),
    )
    output = evaluation_root / "admission-matrix.json"
    output.write_text(matrix.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(matrix.status)
    print(f"matrix_sha256={matrix.matrix_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
