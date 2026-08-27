from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_ROOT = _REPOSITORY_ROOT / "src"
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

from ecomsre.product.pilot.contracts_v02 import (  # noqa: E402
    QueueProfileV02,
    TrafficProfileV02,
)
from ecomsre.product.pilot.live_calibration_v02 import (  # noqa: E402
    run_live_calibration_v02,
)
from scripts.ci.verify_product_v02_history import (  # noqa: E402
    verify_product_v02_history,
)


PINNED_UPSTREAM = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
STARTING_MAIN = "8398a063de048064f160a7ffed236fbb3327b701"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"configuration must be an object: {path}")
    return payload


def verify_calibration_contract(repository_root: Path) -> dict[str, object]:
    root = repository_root.resolve(strict=True)
    profile = QueueProfileV02.model_validate(
        _load(root / "config/product-v02/live-pilot/profile.json")
    )
    campaign = _load(root / "config/product-v02/live-pilot/campaign.json")
    negatives = _load(root / "config/product-v02/live-pilot/negative-controls.json")
    if campaign.get("schema_version") != "ecomsre.product.pilot.campaign.v02":
        raise ValueError("pilot campaign schema differs")
    if campaign.get("accepted_schedule") != ["N0", "P1", "P2", "P3"]:
        raise ValueError("pilot accepted schedule differs")
    if campaign.get("heldout_schedule") != ["H1"]:
        raise ValueError("pilot held-out schedule differs")
    if campaign.get("maximum_infrastructure_replacements_per_episode") != 1:
        raise ValueError("pilot replacement limit differs")
    traffic_raw = campaign.get("traffic_profiles")
    if not isinstance(traffic_raw, dict) or set(traffic_raw) != {
        "CALIBRATION",
        "N0",
        "P1",
        "P2",
        "P3",
        "H1",
    }:
        raise ValueError("pilot traffic profile set differs")
    traffic = {
        key: TrafficProfileV02.model_validate(value)
        for key, value in traffic_raw.items()
    }
    positive_seeds = tuple(traffic[key].request_seed for key in ("P1", "P2", "P3"))
    positive_rates = tuple(traffic[key].requests_per_second for key in ("P1", "P2", "P3"))
    if len(set(positive_seeds)) != 3 or len(set(positive_rates)) < 2:
        raise ValueError("positive episode independence contract differs")
    if negatives.get("live_no_fault") != "N0":
        raise ValueError("live no-fault control differs")
    upstream = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=root / "third_party/opentelemetry-demo",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if upstream != PINNED_UPSTREAM:
        raise ValueError("pinned OTel Demo commit differs")
    flag_source = _load(
        root / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
    )
    target = flag_source.get("flags", {}).get("kafkaQueueProblems", {})
    if target.get("variants", {}).get("off") != 0 or target.get("state") != "ENABLED":
        raise ValueError("pinned queue flag contract differs")
    checkout_source = (
        root / "third_party/opentelemetry-demo/src/checkout/main.go"
    ).read_text(encoding="utf-8")
    if "KafkaQueueProblems" not in checkout_source or "overloading queue" not in checkout_source:
        raise ValueError("pinned checkout queue behavior differs")
    history = verify_product_v02_history(root)
    return {
        "terminal": "ECOMSRE_PRODUCT_V02_CALIBRATION_CONTRACT_PASS",
        "starting_main": STARTING_MAIN,
        "pinned_upstream": upstream,
        "candidate_values": profile.candidate_values,
        "traffic_profile_count": len(traffic),
        "history_status": history["status"],
        "campaign_consumed": (
            root / "config/product-v02/live-pilot/calibration-consumed.json"
        ).is_file(),
        "live_attempt_count": 0,
        "action_authority": "NONE",
        "runbook_executions": 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--stabilization-seconds", type=int, default=30)
    parser.add_argument("--baseline-accumulation-seconds", type=int, default=360)
    parser.add_argument("--observation-seconds", type=int, default=30)
    arguments = parser.parse_args(argv)
    result = (
        verify_calibration_contract(arguments.repository_root)
        if arguments.check_only
        else run_live_calibration_v02(
            repository_root=arguments.repository_root,
            stabilization_seconds=arguments.stabilization_seconds,
            baseline_accumulation_seconds=arguments.baseline_accumulation_seconds,
            observation_seconds=arguments.observation_seconds,
        )
    )
    print(
        json.dumps(
            result,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if not str(result.get("terminal", "")).startswith("BLOCKED_") else 2


if __name__ == "__main__":
    raise SystemExit(main())
