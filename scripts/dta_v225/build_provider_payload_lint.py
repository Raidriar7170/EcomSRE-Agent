"""Create the write-once DTA v2.2.5 opaque Provider-payload lint report."""

from __future__ import annotations

import json
from pathlib import Path

from ecomsre.dta_v2.v22.provider_payload_lint_report_v225 import (
    write_provider_payload_lint_report_v225,
)


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    report = write_provider_payload_lint_report_v225(
        repository_root=root,
        output_path=root
        / "config/dta-v22-5/evaluation/provider-payload-lint.json",
    )
    print(
        json.dumps(
            {
                "evaluation_files_scanned": report.evaluation_files_scanned,
                "rendered_payload_classes": report.rendered_payload_classes,
                "terminal": report.terminal,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
