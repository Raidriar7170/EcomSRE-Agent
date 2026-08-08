"""Create the external v2-dev.2 evaluation-root lock."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval_v2.dev2_evaluation_root import prepare_evaluation_root
from ecomsre_rcaeval_v2.dev2_paths import preserved_evidence_roots


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--private-schedule-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--smoke-journal-root", required=True, type=Path)
    parser.add_argument("--design-journal-root", required=True, type=Path)
    parser.add_argument("--source-base-commit", required=True)
    parser.add_argument("--v2-dev-v1-root", required=True, type=Path)
    parser.add_argument("--v2-dev1-control-root", required=True, type=Path)
    parser.add_argument("--v2-dev1-output-root", required=True, type=Path)
    args = parser.parse_args(argv)
    lock = prepare_evaluation_root(
        args.control_root,
        args.private_schedule_root,
        args.output_root,
        args.smoke_journal_root,
        args.design_journal_root,
        project_root=PROJECT_ROOT,
        source_base_commit=args.source_base_commit,
        preserved_roots=preserved_evidence_roots(
            args.v2_dev_v1_root,
            args.v2_dev1_control_root,
            args.v2_dev1_output_root,
        ),
    )
    print(lock.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
