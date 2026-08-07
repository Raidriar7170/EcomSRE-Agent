from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval.artifacts import write_json_create_once
from ecomsre_rcaeval.dataset import DevSystem, audit_dev_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit development-visible RCAEval RE2 data")
    parser.add_argument("--ob-root", type=Path, required=True)
    parser.add_argument("--ss-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audits = (
        audit_dev_dataset(
            args.ob_root,
            DevSystem.RE2_OB,
            require_locked_distribution=True,
        ),
        audit_dev_dataset(
            args.ss_root,
            DevSystem.RE2_SS,
            require_locked_distribution=True,
        ),
    )
    write_json_create_once(
        args.output,
        {
            "schema_version": "rcaeval-re2.dev-dataset-audits.v1",
            "audits": [item.model_dump(mode="json") for item in audits],
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
