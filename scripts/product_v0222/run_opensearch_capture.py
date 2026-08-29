#!/usr/bin/env python3
"""Preflight or execute the one Product v0.2.2.2 capture session."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from ecomsre.product.pilot.live_capture_v0222 import (
    resume_frozen_capture_analysis_v0222,
    run_live_capture_v0222,
    verify_live_capture_preflight_v0222,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--resume-frozen-analysis", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.execute_live and arguments.resume_frozen_analysis:
        parser.error("live capture and frozen analysis are mutually exclusive")
    if arguments.execute_live:
        result = run_live_capture_v0222(arguments.project_root)
    elif arguments.resume_frozen_analysis:
        result = resume_frozen_capture_analysis_v0222(arguments.project_root)
    else:
        result = verify_live_capture_preflight_v0222(arguments.project_root)
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("main",)
