#!/usr/bin/env python3
"""Create the fresh sixteen-task DTA v2.3.4.1 evaluation bytes."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre.dta_v2.v23.evaluation_data_v2341 import build_evaluation_data_v2341


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--write-new",
        required=True,
        choices=("DTA_V2341_FRESH_EVALUATION_DATA",),
    )
    args = parser.parse_args()
    root = args.repository_root.resolve()
    output_root = root / "config/dta-v2341/evaluation"
    output_root.mkdir(parents=True, exist_ok=True)
    tasks, truths, views = build_evaluation_data_v2341(repository_root=root)
    outputs = {
        output_root / "tasks.json": tasks.model_dump_json(indent=2) + "\n",
        output_root / "truth.json": truths.model_dump_json(indent=2) + "\n",
        output_root / "core-schema-snapshot.json": (
            views.model_dump_json(indent=2) + "\n"
        ),
    }
    for path, content in outputs.items():
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
    print("DTA_V2341_FRESH_EVALUATION_DATA_WRITTEN")
    print(f"task_set_sha256={tasks.task_set_sha256}")
    print(f"truth_sha256={truths.truth_sha256}")
    print(f"view_set_sha256={views.view_set_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
