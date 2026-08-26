#!/usr/bin/env python3
"""Create the fresh sixteen-task DTA v2.3.4 evaluation bytes."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre.dta_v2.v23.evaluation_v234 import (
    build_default_evaluation_data_v234,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--write-new",
        required=True,
        choices=(
            "DTA_V234_FRESH_EVALUATION_DATA",
            "DTA_V234_REPLACE_UNFROZEN_EVALUATION_DATA",
        ),
    )
    args = parser.parse_args()
    root = args.repository_root.resolve()
    output_root = root / "config/dta-v234/evaluation"
    output_root.mkdir(parents=True, exist_ok=True)
    task_set, truth_set, view_set = build_default_evaluation_data_v234(
        repository_root=root
    )
    outputs = {
        output_root / "tasks.json": task_set.model_dump_json(indent=2) + "\n",
        output_root / "truth.json": truth_set.model_dump_json(indent=2) + "\n",
        output_root
        / "core-schema-snapshot.json": view_set.model_dump_json(indent=2) + "\n",
    }
    replace = args.write_new == "DTA_V234_REPLACE_UNFROZEN_EVALUATION_DATA"
    if replace and (output_root / "manifest.json").exists():
        raise FileExistsError("v2.3.4 frozen evaluation data cannot be replaced")
    for path, content in outputs.items():
        if replace:
            path.write_text(content, encoding="utf-8")
        else:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(content)
    print("DTA_V234_FRESH_EVALUATION_DATA_WRITTEN")
    print(f"task_set_sha256={task_set.task_set_sha256}")
    print(f"truth_sha256={truth_set.truth_sha256}")
    print(f"view_set_sha256={view_set.view_set_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
