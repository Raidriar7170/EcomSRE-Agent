"""Render the deterministic v2 model/prompt/schema lock for review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ecomsre_rcaeval_v2.locks import expected_model_prompt_lock


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    payload = (
        json.dumps(
            expected_model_prompt_lock(),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if args.output is None:
        print(payload, end="")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        handle.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
