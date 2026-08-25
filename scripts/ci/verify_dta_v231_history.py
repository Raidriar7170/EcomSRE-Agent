from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config/dta-v231/historical-results.v1.json"


def verify() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if payload["engineering_terminal"] != "DTA_V23_OPEN_WORLD_DISCOVERY_MVP_COMPLETE":
        raise ValueError("v2.3 engineering terminal binding differs")
    if payload["measured_result_terminal"] != "DTA_V23_OPEN_WORLD_DISCOVERY_NOT_OBSERVED":
        raise ValueError("v2.3 measured terminal binding differs")
    for binding in payload["bindings"]:
        path = ROOT / binding["path"]
        raw = path.read_bytes()
        if len(raw) != binding["size_bytes"]:
            raise ValueError(f"historical size differs: {binding['path']}")
        if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
            raise ValueError(f"historical SHA-256 differs: {binding['path']}")


if __name__ == "__main__":
    verify()
    print("DTA_V231_HISTORY_VERIFIED")
