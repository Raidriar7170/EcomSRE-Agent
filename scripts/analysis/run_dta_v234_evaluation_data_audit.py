#!/usr/bin/env python3
"""Run the DTA v2.3.4 pre-Provider evaluation-data gate."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre.dta_v2.v23.evaluation_v234 import (
    run_evaluation_data_audit_v234,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    output = args.output or root / "docs/analysis/dta-v234-registration-audit.json"
    artifact = run_evaluation_data_audit_v234(
        repository_root=root,
        evaluation_root=root / "config/dta-v234/evaluation",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(artifact.terminal)
    print(f"audit_sha256={artifact.audit_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
