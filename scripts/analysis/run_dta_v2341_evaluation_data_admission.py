#!/usr/bin/env python3
"""Run the zero-Provider DTA v2.3.4.1 final-data admission gate."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre.dta_v2.v23.evaluation_data_v2341 import (
    run_evaluation_data_admission_v2341,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--require",
        required=True,
        choices=("DTA_V2341_PROVIDER_SMOKE_PASS",),
    )
    args = parser.parse_args()
    root = args.repository_root.resolve()
    output = root / "docs/analysis/dta-v2341-evaluation-data-admission.json"
    smoke = root / "docs/analysis/dta-v2341-provider-smoke.json"
    if output.exists():
        raise FileExistsError("v2.3.4.1 evaluation admission already exists")
    if args.require not in smoke.read_text(encoding="utf-8"):
        raise ValueError("v2.3.4.1 Provider smoke gate is absent")
    artifact = run_evaluation_data_admission_v2341(
        repository_root=root,
        evaluation_root=root / "config/dta-v2341/evaluation",
    )
    with output.open("x", encoding="utf-8") as stream:
        stream.write(artifact.model_dump_json(indent=2) + "\n")
    print(artifact.terminal)
    print(f"admission_sha256={artifact.admission_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
