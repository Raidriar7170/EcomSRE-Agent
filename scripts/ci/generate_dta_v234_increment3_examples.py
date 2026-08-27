#!/usr/bin/env python3
"""Generate deterministic DTA v2.3.4 Increment-3 example artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile

from ecomsre.dta_v2.v23.registration_development_v234 import (
    run_increment3_development_demo_v234,
)


EXAMPLE_TIME_V234 = datetime(2026, 8, 26, 7, 0, tzinfo=timezone.utc)


def render_increment3_examples_v234(repository_root: Path) -> dict[str, bytes]:
    with tempfile.TemporaryDirectory(prefix="dta-v234-increment3-") as temporary:
        result = run_increment3_development_demo_v234(
            repository_root=repository_root,
            local_root=Path(temporary) / ".local" / "dta-v234",
            run_at=EXAMPLE_TIME_V234,
        )
    return {
        "config/dta-v234/examples/shadow-evaluation.json": (
            result.shadow_evaluation.model_dump_json(indent=2) + "\n"
        ).encode("utf-8"),
        "config/dta-v234/examples/promotion-record.json": (
            result.promotion_record.model_dump_json(indent=2) + "\n"
        ).encode("utf-8"),
        "config/dta-v234/examples/extension-registry.json": (
            result.extension_registry.model_dump_json(indent=2) + "\n"
        ).encode("utf-8"),
    }


def main() -> int:
    repository_root = Path(__file__).resolve().parents[2]
    for relative_path, content in render_increment3_examples_v234(
        repository_root
    ).items():
        path = repository_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
