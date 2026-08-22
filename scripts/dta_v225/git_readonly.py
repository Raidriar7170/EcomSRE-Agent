"""Task-scoped allowlisted Git queries for DTA v2.2.5 freeze checks."""

from __future__ import annotations

from pathlib import Path
import subprocess


_READ_ONLY_SUBCOMMANDS = frozenset(
    {"diff", "ls-tree", "merge-base", "rev-parse", "show", "status"}
)


class ReadOnlyGitQueryV225:
    """Run byte-preserving local Git reads; reject every mutating subcommand."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = Path(repository_root).resolve()

    def bytes(self, *arguments: str, check: bool = True) -> bytes:
        if not arguments or arguments[0] not in _READ_ONLY_SUBCOMMANDS:
            raise ValueError("v2.2.5 Git query is not allowlisted as read-only")
        completed = subprocess.run(
            ("git", *arguments),
            cwd=self.repository_root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=30,
            shell=False,
        )
        if check and completed.returncode != 0:
            raise ValueError(
                completed.stderr.decode("utf-8", errors="replace").strip()
            )
        return completed.stdout

    def text(self, *arguments: str, check: bool = True) -> str:
        return self.bytes(*arguments, check=check).decode("utf-8").strip()

    def succeeds(self, *arguments: str) -> bool:
        try:
            self.bytes(*arguments)
        except ValueError:
            return False
        return True


__all__ = ("ReadOnlyGitQueryV225",)
