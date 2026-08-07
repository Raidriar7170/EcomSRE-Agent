"""Fail closed when v2-dev.1 public result artifacts contain private material."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval_v2.public_projection import scan_public_paths


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    print(scan_public_paths(tuple(args.paths), allow_missing=args.allow_missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
