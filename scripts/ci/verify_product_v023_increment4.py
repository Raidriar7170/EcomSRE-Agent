"""Verify frozen Product v0.2.3 restart and No-Fault acceptance outputs."""

from __future__ import annotations

import json
from pathlib import Path

from ecomsre.product.pilot.live_nofault_acceptance_v023 import (
    verify_frozen_nofault_acceptance_v023,
)


def main() -> int:
    result = verify_frozen_nofault_acceptance_v023(Path.cwd())
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
