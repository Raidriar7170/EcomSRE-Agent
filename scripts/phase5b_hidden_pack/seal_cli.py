"""Out-of-band CLI for Phase 5B hidden-pack structural seal verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Sequence

from scripts.phase5b_hidden_pack.seal_record import (
    HiddenPackSealRecord,
    verify_external_hidden_pack,
    verify_public_seal_records,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git_worktree_roots(project_root: Path) -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "-C", str(project_root), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(
        Path(line.removeprefix("worktree "))
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    )


def _emit(payload: dict[str, object]) -> None:
    print(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _seal_summary(record: HiddenPackSealRecord) -> dict[str, object]:
    return {
        "status": "PHASE5B_HIDDEN_PACK_SEAL_VERIFIED",
        **record.model_dump(mode="json"),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify an external Phase 5B hidden pack without execution."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    verify_pack = commands.add_parser("verify-pack")
    verify_pack.add_argument("--pack-root", type=Path, required=True)
    commands.add_parser("verify-seal")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    project_root = _project_root()

    if args.command == "verify-seal":
        try:
            record = verify_public_seal_records(project_root)
        except (OSError, UnicodeError, ValueError):
            parser.error("public hidden-pack seal verification failed closed")
        _emit(_seal_summary(record))
        return 0

    try:
        record = verify_public_seal_records(project_root)
        result = verify_external_hidden_pack(
            args.pack_root,
            record,
            worktree_roots=_git_worktree_roots(project_root),
        )
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError):
        parser.error("external hidden-pack verification failed closed")
    _emit(result.model_dump(mode="json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
