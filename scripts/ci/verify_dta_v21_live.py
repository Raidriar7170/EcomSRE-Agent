"""Verify the checked-in DTA v2.1 PR-F live report when present."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from ecomsre.dta_v2.v21.live_cli import run_verify


def verify_public_live_v21(project_root: Path) -> str:
    return run_verify(repository_root=project_root)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args(argv)
    print(verify_public_live_v21(args.project_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
