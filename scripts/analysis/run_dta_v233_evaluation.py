#!/usr/bin/env python3
"""Execute the gated DTA v2.3.3 three-arm fixed study exactly once."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v23.discovery_provider_v233 import (
    OpenAICompatibleDiscoveryTransportV233,
)
from ecomsre.dta_v2.v23.evaluation_study_v233 import (
    EvaluationCaseComparisonV233,
    run_fixed_evaluation_once_v233,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument(
        "--execute-once",
        required=True,
        choices=("DTA_V233_FINAL_EVALUATION_PREFLIGHT_PASS",),
    )
    parser.add_argument("--minimum-request-interval", type=float, default=6.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    values = load_private_provider_env(args.provider_env)
    provider = OpenAICompatibleDiscoveryTransportV233(
        config=OpenAICompatibleConfig(
            base_url=values["ECOMSRE_LLM_BASE_URL"],
            api_key=values["ECOMSRE_LLM_API_KEY"],
            model=values["ECOMSRE_LLM_MODEL"],
        ),
        minimum_request_interval_seconds=args.minimum_request_interval,
        timeout_seconds=args.timeout,
    )

    def observe(comparison: EvaluationCaseComparisonV233) -> None:
        print(
            json.dumps(
                {
                    "case_id": comparison.case_id,
                    "arm_order": [item.value for item in comparison.arm_order],
                    "dispositions": {
                        item.policy.value: item.final_disposition
                        for item in comparison.runs
                    },
                    "provider_calls": sum(
                        item.provider_cost.provider_calls for item in comparison.runs
                    ),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    artifact = run_fixed_evaluation_once_v233(
        repository_root=root,
        evaluation_root=root / "config/dta-v233/evaluation",
        manifest_path=root / "config/dta-v233/evaluation/manifest.json",
        independent_review_path=root
        / "docs/external-reviews/dta-v233-pre-execution-review.md",
        provider_smoke_path=root / "docs/analysis/dta-v233-provider-smoke.json",
        output_path=root / "docs/results/dta-v233-domain-guard-evaluation.json",
        output_markdown_path=root
        / "docs/results/dta-v233-domain-guard-evaluation.md",
        provider_transport=provider,
        observer=observe,
    )
    print(
        json.dumps(
            {
                "execution_count": artifact.execution_count,
                "case_count": artifact.case_count,
                "run_count": artifact.run_count,
                "measured_result_terminal": artifact.measured_result_terminal.value,
                "artifact_sha256": artifact.artifact_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
