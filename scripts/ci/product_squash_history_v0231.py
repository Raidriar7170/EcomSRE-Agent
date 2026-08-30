"""Shared squash-import checks for the Product v0.2.x history stack."""

from __future__ import annotations

from pathlib import Path
import subprocess


PRODUCT_IMPORT_PR_V0231 = 79
PRODUCT_IMPORT_SQUASH_V0231 = "613f6203e4a174b4549b912cb16ca7998cf6238c"


def require_product_import_ancestry_v0231(root: Path) -> None:
    subprocess.run(
        (
            "git",
            "merge-base",
            "--is-ancestor",
            PRODUCT_IMPORT_SQUASH_V0231,
            "HEAD",
        ),
        cwd=root,
        check=True,
        capture_output=True,
    )


def require_product_import_bytes_v0231(
    root: Path,
    *,
    relative: str,
    expected: bytes,
) -> None:
    imported = subprocess.run(
        ("git", "show", f"{PRODUCT_IMPORT_SQUASH_V0231}:{relative}"),
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    if imported != expected:
        raise ValueError(f"Product squash-import byte drift: {relative}")


__all__ = (
    "PRODUCT_IMPORT_PR_V0231",
    "PRODUCT_IMPORT_SQUASH_V0231",
    "require_product_import_ancestry_v0231",
    "require_product_import_bytes_v0231",
)
