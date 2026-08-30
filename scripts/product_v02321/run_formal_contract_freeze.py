#!/usr/bin/env python3
"""Freeze Product v0.2.3.2.1 formal semantics after traffic preflight PASS."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile
from typing import Sequence

from ecomsre.product.pilot.formal_contract_v02321 import (
    FormalContractFreezeV02321,
    build_formal_contract_freeze_v02321,
    verify_formal_contract_freeze_v02321,
)
from ecomsre_live_sandbox.contracts import canonical_json_bytes


def write_formal_contract_freeze_v02321(
    root: Path,
) -> FormalContractFreezeV02321:
    project = Path(root).resolve(strict=True)
    freeze = build_formal_contract_freeze_v02321(project)
    output = project / "docs/analysis/product-v02321-formal-contract-freeze.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Product v0.2.3.2.1 formal contract freeze exists")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(freeze.model_dump(mode="json")))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return freeze


def replace_review_blocked_formal_contract_freeze_v02321(
    root: Path,
) -> FormalContractFreezeV02321:
    """Preserve the rejected candidate bytes, then replace the canonical freeze."""

    project = Path(root).resolve(strict=True)
    freeze = build_formal_contract_freeze_v02321(project)
    output = project / "docs/analysis/product-v02321-formal-contract-freeze.json"
    rejected = (
        project
        / "docs/analysis/"
        "product-v02321-formal-contract-freeze-review-blocked-1.json"
    )
    if output.is_symlink() or not output.is_file():
        raise FileNotFoundError(
            "Product v0.2.3.2.1 rejected formal contract freeze is missing"
        )
    rejected.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(rejected):
        if (
            rejected.is_symlink()
            or not rejected.is_file()
            or rejected.read_bytes() != output.read_bytes()
        ):
            raise FileExistsError(
                "Product v0.2.3.2.1 rejected freeze archive differs"
            )
    else:
        os.link(output, rejected)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(freeze.model_dump(mode="json")))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return freeze


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true")
    mode.add_argument(
        "--replace-review-blocked-candidate",
        action="store_true",
    )
    arguments = parser.parse_args(argv)
    if arguments.verify:
        freeze = verify_formal_contract_freeze_v02321(arguments.project_root)
    elif arguments.replace_review_blocked_candidate:
        freeze = replace_review_blocked_formal_contract_freeze_v02321(
            arguments.project_root
        )
    else:
        freeze = write_formal_contract_freeze_v02321(arguments.project_root)
    print(canonical_json_bytes(freeze.model_dump(mode="json")).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
