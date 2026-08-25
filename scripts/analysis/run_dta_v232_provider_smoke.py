#!/usr/bin/env python3
"""Run the gated eight-case Provider smoke for DTA v2.3.2."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v23.evaluation_study_v232 import run_provider_smoke_v232
from ecomsre.dta_v2.v23.evaluation_v231 import (
    OpenAICompatibleDiscoveryTransportV231,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument(
        "--execute",
        required=True,
        choices=("MUST_FIX_0_CLAIM_ACCURACY_PASS",),
    )
    parser.add_argument("--minimum-request-interval", type=float, default=6.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    values = load_private_provider_env(args.provider_env)
    provider = OpenAICompatibleDiscoveryTransportV231(
        config=OpenAICompatibleConfig(
            base_url=values["ECOMSRE_LLM_BASE_URL"],
            api_key=values["ECOMSRE_LLM_API_KEY"],
            model=values["ECOMSRE_LLM_MODEL"],
        ),
        minimum_request_interval_seconds=args.minimum_request_interval,
        timeout_seconds=args.timeout,
    )
    evaluation_root = root / "config/dta-v232/evaluation"
    artifact = run_provider_smoke_v232(
        repository_root=root,
        cases_path=evaluation_root / "cases.json",
        ontology_views_path=evaluation_root / "ontology-views.json",
        manifest_path=evaluation_root / "manifest.json",
        independent_review_path=(
            root / "docs/external-reviews/dta-v232-pre-execution-review.md"
        ),
        output_path=root / "docs/analysis/dta-v232-provider-smoke.json",
        provider_transport=provider,
    )
    print(
        json.dumps(
            {
                "status": artifact.status,
                "execution_count": artifact.execution_count,
                "case_count": artifact.case_count,
                "provider_calls": sum(
                    item.provider_calls for item in artifact.runs
                ),
                "protocol_repairs": sum(
                    item.protocol_repairs for item in artifact.runs
                ),
                "transport_retries": sum(
                    item.transport_retries for item in artifact.runs
                ),
                "smoke_sha256": artifact.smoke_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
