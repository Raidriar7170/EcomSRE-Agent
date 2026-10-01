"""Read-only v0.5 Provider configuration and campaign-ledger inspection.

Does not dispatch Provider requests or start/stop a runtime. Never prints keys,
Provider URLs, raw responses, or environment contents.
"""

import argparse
import json
import os
from pathlib import Path
import sqlite3

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre.product.investigation.contracts import PriceSchedule


def inspect(data_root: Path) -> dict:
    result = {
        "schema_version": "ecomsre.product.preflight.v050",
        "provider_status": "NOT_CONFIGURED",
        "pricing_status": "NOT_CONFIGURED",
        "dispatches_in_this_command": 0,
        "docker_mutations": 0,
        "request_cap": 200,
        "cost_cap_usd": 20,
        "live_episode_cap": 12,
        "ledger_status": "NOT_CREATED",
    }
    try:
        config = OpenAICompatibleConfig.from_environment()
        if config is not None:
            result.update(provider_status="CONFIGURED_NOT_TESTED", model=config.model)
        path = os.environ.get("ECOMSRE_PRODUCT_PROVIDER_PRICE_FILE")
        if path:
            prices = PriceSchedule.model_validate_json(Path(path).read_bytes())
            from datetime import UTC, datetime

            result["pricing_status"] = (
                "CONFIGURED_NOT_PROVIDER_VERIFIED"
                if config is not None
                and prices.model == config.model
                and prices.as_of <= datetime.now(UTC).date()
                else "INVALID"
            )
    except (ValueError, OSError):
        result["provider_status"] = "CONFIGURATION_INVALID"
    db = data_root / "product.sqlite3"
    if db.exists():
        with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as c:
            if c.execute(
                "SELECT 1 FROM sqlite_master WHERE name='investigation_provider_calls_v050'"
            ).fetchone():
                row = c.execute(
                    "SELECT COUNT(*),COALESCE(SUM(charged_microusd),0),"
                    "COUNT(*)-COUNT(charged_microusd),COALESCE(SUM(COALESCE(charged_microusd,reserved_microusd)),0) "
                    "FROM investigation_provider_calls_v050"
                ).fetchone()
                result.update(
                    ledger_status="READ",
                    provider_request_count=row[0],
                    reported_cost_microusd=row[1],
                    unknown_usage_requests=row[2],
                    committed_upper_microusd=row[3],
                )
            else:
                result["ledger_status"] = "NO_V050_LEDGER"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inspect(args.data_root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
