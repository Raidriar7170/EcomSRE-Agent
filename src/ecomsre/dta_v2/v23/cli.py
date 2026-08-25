"""Offline CLI for the DTA v2.3 discovery lane."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from ecomsre.dta_v2.v23.discovery_runtime import run_cpu_development_demo_v23


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecomsre-dta-v23")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo = subparsers.add_parser("demo")
    demo.add_argument("demo_name", choices=("hidden-cpu",))
    demo.add_argument("--repository-root", type=Path, default=Path.cwd())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo" and args.demo_name == "hidden-cpu":
        result = run_cpu_development_demo_v23(
            repository_root=args.repository_root.resolve(),
            hide_cpu=True,
        )
        print(result.model_dump_json(indent=2))
        return 0
    raise AssertionError("unreachable v2.3 CLI command")


if __name__ == "__main__":
    raise SystemExit(main())
