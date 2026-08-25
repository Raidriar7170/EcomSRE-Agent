#!/usr/bin/env python3
"""Execute the admitted independent DTA v2.3.1 successor exactly once."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v23.evaluation_successor_v231 import (
    run_successor_evaluation_once_v231,
)
from ecomsre.dta_v2.v23.evaluation_v231 import (
    EvaluationCasePairV231,
    OpenAICompatibleDiscoveryTransportV231,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument(
        "--execute-once",
        required=True,
        choices=("DTA_V231_SUCCESSOR_EVALUATION_DATA_PASS",),
    )
    parser.add_argument("--minimum-request-interval", type=float, default=6.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    repository_root = args.repository_root.resolve()
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

    def observe(pair: EvaluationCasePairV231) -> None:
        print(
            json.dumps(
                {
                    "case_id": pair.case_id,
                    "strict": pair.strict.final_disposition,
                    "treatment": pair.treatment.final_disposition,
                    "strict_reads": pair.strict.discovery_read_count,
                    "treatment_reads": pair.treatment.discovery_read_count,
                    "provider_calls": (
                        pair.strict.provider_cost.provider_calls
                        + pair.treatment.provider_cost.provider_calls
                    ),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    evaluation_root = repository_root / "config/dta-v231-successor/evaluation"
    artifact = run_successor_evaluation_once_v231(
        repository_root=repository_root,
        cases_path=evaluation_root / "cases.json",
        truth_index_path=evaluation_root / "truth-index.json",
        ontology_views_path=evaluation_root / "ontology-views.json",
        admission_matrix_path=evaluation_root / "admission-matrix.json",
        manifest_path=evaluation_root / "manifest.json",
        independent_review_path=repository_root
        / "docs/external-reviews/dta-v231-successor-pre-execution-review.json",
        output_path=repository_root
        / "docs/results/dta-v231-successor-conflict-aware-evaluation.json",
        output_markdown_path=repository_root
        / "docs/results/dta-v231-successor-conflict-aware-evaluation.md",
        provider_transport=provider,
        observer=observe,
    )
    print(
        json.dumps(
            {
                "execution_count": artifact.execution_count,
                "case_count": artifact.case_count,
                "run_count": artifact.run_count,
                "study_relation": artifact.study_relation,
                "measured_result_terminal": (
                    artifact.measured_result_terminal.value
                ),
                "artifact_sha256": artifact.artifact_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
