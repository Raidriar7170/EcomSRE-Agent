#!/usr/bin/env python3
"""Execute the gated DTA v2.3.4 two-arm fixed study exactly once."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v23.evaluation_v234 import (
    RegistrationCaseComparisonV234,
    run_fixed_evaluation_once_v234,
)
from ecomsre.dta_v2.v23.registration_provider_v234 import (
    OpenAICompatibleRegistrationDraftTransportV234,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument(
        "--execute-once",
        required=True,
        choices=("DTA_V234_FINAL_EVALUATION_PREFLIGHT_PASS",),
    )
    parser.add_argument("--minimum-request-interval", type=float, default=6.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    values = load_private_provider_env(args.provider_env)
    provider = OpenAICompatibleRegistrationDraftTransportV234(
        config=OpenAICompatibleConfig(base_url=values["ECOMSRE_LLM_BASE_URL"], api_key=values["ECOMSRE_LLM_API_KEY"], model=values["ECOMSRE_LLM_MODEL"]),
        minimum_request_interval_seconds=args.minimum_request_interval,
        timeout_seconds=args.timeout,
        raw_artifact_dir=root / ".local/dta-v234/provider-raw/fixed-evaluation",
    )

    def observe(comparison: RegistrationCaseComparisonV234) -> None:
        treatment = comparison.runs[1]
        print(json.dumps({"task_id": comparison.task_id, "typed_disposition": treatment.typed_disposition.value if treatment.typed_disposition else None, "validation_status": treatment.validation_status.value if treatment.validation_status else None, "provider_calls": treatment.provider_calls, "provider_error_code": treatment.provider_error_code}, sort_keys=True), flush=True)

    artifact = run_fixed_evaluation_once_v234(
        repository_root=root,
        evaluation_root=root / "config/dta-v234/evaluation",
        manifest_path=root / "config/dta-v234/evaluation/manifest.json",
        independent_review_path=root / "docs/external-reviews/dta-v234-pre-execution-review.md",
        provider_smoke_path=root / "docs/analysis/dta-v234-provider-smoke.json",
        output_path=root / "docs/results/dta-v234-registration-assistance-evaluation.json",
        output_markdown_path=root / "docs/results/dta-v234-registration-assistance-evaluation.md",
        provider_transport=provider,
        expected_provider_model=provider.config.model,
        observer=observe,
    )
    print(json.dumps({"execution_count": artifact.execution_count, "task_count": artifact.task_count, "run_count": artifact.run_count, "measured_result_terminal": artifact.measured_result_terminal.value, "artifact_sha256": artifact.artifact_sha256}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
