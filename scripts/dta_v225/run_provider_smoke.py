"""Run the explicit write-once DTA v2.2.5 Provider smoke."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ecomsre.dta_v2.v22.provider_smoke_v225 import write_provider_smoke_v225


def main() -> int:
    parser = argparse.ArgumentParser(description="DTA v2.2.5 Provider smoke")
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = write_provider_smoke_v225(
        repository_root=args.repository_root,
        provider_env_path=args.provider_env,
        output_path=args.output,
    )
    print(
        json.dumps(
            {
                "gate_passed": report.gate_passed,
                "post_repair_protocol_success_rate": report.post_repair_protocol_success_rate,
                "provider_calls": report.provider_calls,
                "real_runs": report.real_runs,
            },
            sort_keys=True,
        )
    )
    return 0 if report.gate_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
