#!/usr/bin/env python3
"""Run the 32-path deterministic DTA v2.3.4.1 runtime preflight."""

from __future__ import annotations

import argparse
from pathlib import Path
import tempfile

from ecomsre.dta_v2.v23.evaluation_study_v2341 import (
    run_runtime_preflight_v2341,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--require",
        required=True,
        choices=("DTA_V2341_EVALUATION_DATA_PASS",),
    )
    args = parser.parse_args()
    root = args.repository_root.resolve()
    output = root / "docs/analysis/dta-v2341-runtime-preflight.json"
    if output.exists():
        raise FileExistsError("v2.3.4.1 runtime preflight already exists")
    admission = root / "docs/analysis/dta-v2341-evaluation-data-admission.json"
    if args.require not in admission.read_text(encoding="utf-8"):
        raise ValueError("v2.3.4.1 evaluation data gate is absent")
    private_root = root / ".local/dta-v2341"
    private_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="runtime-preflight-", dir=private_root
    ) as raw:
        artifact = run_runtime_preflight_v2341(
            repository_root=root,
            evaluation_root=root / "config/dta-v2341/evaluation",
            local_root=Path(raw),
        )
    with output.open("x", encoding="utf-8") as stream:
        stream.write(artifact.model_dump_json(indent=2) + "\n")
    print(artifact.terminal)
    print(f"preflight_sha256={artifact.preflight_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
