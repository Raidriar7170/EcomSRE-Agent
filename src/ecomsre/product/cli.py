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
    v0221 = product.add_parser("product-v0221")
    commands_v0221 = v0221.add_subparsers(dest="command", required=True)
    probe_v0221 = commands_v0221.add_parser("opensearch-probe")
    probe_v0221.add_argument(
        "--config",
        type=Path,
        default=Path("config/product-v0221/opensearch-probe/profile.json"),
    )
    probe_v0221.add_argument("--execute-live", action="store_true")
    report_v0221 = commands_v0221.add_parser("opensearch-probe-report")
    report_v0221.add_argument("--session", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    root = Path.cwd().resolve(strict=True)
    if arguments.product_version == "product-v022" and arguments.command == "history-verify":
        from scripts.ci.verify_product_v022_history import (
            verify_product_v022_history,
        )

        result = verify_product_v022_history(root)
    elif arguments.product_version == "product-v022" and arguments.command == "opensearch-probe":
        from scripts.product_v022.run_opensearch_schema_probe import main as probe_main

        forwarded = ["--project-root", str(root), "--config", str(arguments.config)]
        if arguments.execute_live:
            forwarded.append("--execute-live")
        return probe_main(forwarded)
    elif arguments.product_version == "product-v0221" and arguments.command == "opensearch-probe":
        from scripts.product_v0221.run_opensearch_schema_probe import (
            main as probe_v0221_main,
        )

        forwarded = ["--project-root", str(root), "--config", str(arguments.config)]
        if arguments.execute_live:
            forwarded.append("--execute-live")
        return probe_v0221_main(forwarded)
    elif arguments.product_version == "product-v0221" and arguments.command == "opensearch-probe-report":
        if arguments.session != "product-v0221-schema-discovery-1":
            raise ValueError("Product v0.2.2.1 schema session differs")
        report_path = root / "docs/analysis/product-v0221-schema-session.json"
        if not report_path.exists():
            raise ValueError("Product v0.2.2.1 schema report is unavailable")
        result = json.loads(report_path.read_text(encoding="utf-8"))
    else:
        raise ValueError("Product command is unsupported")
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("main",)
