#!/usr/bin/env python3
"""Freeze the zero-call DTA v2.3.4.1 smoke catalog-feasibility audit."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre.dta_v2.v23.provider_smoke_v2341 import (
    audit_smoke_catalog_feasibility_v2341,
    load_smoke_tasks_v2341,
    load_smoke_truth_v2341,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.repository_root.resolve()
    smoke_root = root / "config/dta-v2341/smoke"
    output = root / "docs/analysis/dta-v2341-catalog-feasibility.json"
    artifact = audit_smoke_catalog_feasibility_v2341(
        repository_root=root,
        task_set=load_smoke_tasks_v2341(smoke_root / "tasks.json"),
        truth_set=load_smoke_truth_v2341(smoke_root / "truth.json"),
    )
    rendered = artifact.model_dump_json(indent=2) + "\n"
    if output.exists():
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            raise ValueError("v2.3.4.1 catalog-feasibility artifact already differs")
    else:
        with output.open("x", encoding="utf-8") as stream:
            stream.write(rendered)
    print(artifact.terminal)
    print(f"artifact_sha256={artifact.artifact_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
