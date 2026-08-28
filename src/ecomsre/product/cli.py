"""Repository Product command line surface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecomsre.product.cli")
    product = parser.add_subparsers(dest="product_version", required=True)
    v022 = product.add_parser("product-v022")
    commands = v022.add_subparsers(dest="command", required=True)
    commands.add_parser("history-verify")
    probe = commands.add_parser("opensearch-probe")
    probe.add_argument(
        "--config",
        type=Path,
        default=Path("config/product-v022/opensearch-probe/profile.json"),
    )
    probe.add_argument("--execute-live", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    root = Path.cwd().resolve(strict=True)
    if arguments.command == "history-verify":
        from scripts.ci.verify_product_v022_history import (
            verify_product_v022_history,
        )

        result = verify_product_v022_history(root)
    elif arguments.command == "opensearch-probe":
        from scripts.product_v022.run_opensearch_schema_probe import main as probe_main

        forwarded = ["--project-root", str(root), "--config", str(arguments.config)]
        if arguments.execute_live:
            forwarded.append("--execute-live")
        return probe_main(forwarded)
    else:
        raise ValueError("Product v0.2.2 command is unsupported")
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("main",)
