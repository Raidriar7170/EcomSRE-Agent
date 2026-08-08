"""Project the external Admission Lock to a case-free review artifact."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from ecomsre_rcaeval_v2.dev3_evidence import public_admission_gate
from ecomsre_rcaeval_v2.dev3_evaluation_root import verify_provider_ready
from ecomsre_rcaeval_v2.public_projection import write_public_json_create_once
from scripts.rcaeval_v2.dev3_cli import (
    add_preserved_root_arguments,
    preserved_roots_from_args,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--private-schedule-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--smoke-journal-root", required=True, type=Path)
    parser.add_argument("--design-journal-root", required=True, type=Path)
    add_preserved_root_arguments(parser)
    args = parser.parse_args(argv)
    _evaluation, lock = verify_provider_ready(
        args.control_root,
        args.private_schedule_root,
        args.output_root,
        args.smoke_journal_root,
        args.design_journal_root,
        project_root=PROJECT_ROOT,
        preserved_roots=preserved_roots_from_args(args),
    )
    admission_lock = args.control_root / "locks/schedule-admission-lock.json"
    payload = public_admission_gate(
        lock, lock_sha256=hashlib.sha256(admission_lock.read_bytes()).hexdigest()
    )
    write_public_json_create_once(
        args.control_root / "evidence/schedule-admission-gate.json", payload
    )
    print(payload["state"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
