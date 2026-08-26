#!/usr/bin/env python3
"""Run all thirty-two DTA v2.3.4 paths with deterministic Provider fixtures."""

from __future__ import annotations

import argparse
from pathlib import Path
import tempfile

from ecomsre.dta_v2.v23.evaluation_v234 import run_runtime_preflight_v234


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    output = args.output or root / "docs/analysis/dta-v234-runtime-preflight.json"
    with tempfile.TemporaryDirectory(prefix="dta-v234-preflight-") as value:
        artifact = run_runtime_preflight_v234(
            repository_root=root,
            evaluation_root=root / "config/dta-v234/evaluation",
            local_root=Path(value),
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(artifact.terminal)
    print(f"preflight_sha256={artifact.preflight_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
