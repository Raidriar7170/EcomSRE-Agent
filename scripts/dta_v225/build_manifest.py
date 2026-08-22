"""Create the write-once DTA v2.2.5 evaluation preflight manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ecomsre.dta_v2.v22.evaluation_manifest_v225 import (
    MANIFEST_PATH_V225,
    write_evaluation_manifest_v225,
)
from scripts.dta_v225.git_readonly import ReadOnlyGitQueryV225


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-freeze", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    root = Path(__file__).resolve().parents[2]
    manifest = write_evaluation_manifest_v225(
        repository_root=root,
        source_freeze_commit=args.source_freeze,
        output_path=root / MANIFEST_PATH_V225,
        git_query=ReadOnlyGitQueryV225(root),
    )
    print(
        json.dumps(
            {
                "evaluation_execution_id": manifest.evaluation_execution_id,
                "source_freeze_commit": manifest.source_freeze_commit,
                "source_tree_sha256": manifest.source_tree_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
