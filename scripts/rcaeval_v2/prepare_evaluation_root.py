"""Create the external v2-dev.1 Provider authorization lock exactly once."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval_v2.evaluation_root import prepare_evaluation_root


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_BASE_COMMIT = "95e835460c83b631e5860e98276b3fc55c7f77aa"


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args(argv)
    lock = prepare_evaluation_root(
        args.control_root,
        args.output_root,
        project_root=PROJECT_ROOT,
        source_base_commit=SOURCE_BASE_COMMIT,
    )
    print(lock.implementation_commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
