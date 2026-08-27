#!/usr/bin/env python3
"""Run the seven-role DTA v2.3.4 Provider smoke exactly once."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v23.evaluation_v234 import run_provider_smoke_v234
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
        choices=("DTA_V234_RUNTIME_PREFLIGHT_PASS",),
    )
    parser.add_argument("--minimum-request-interval", type=float, default=6.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--resume-after-fix", type=int, choices=(1, 2))
    parser.add_argument("--repair-record", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    values = load_private_provider_env(args.provider_env)
    provider = OpenAICompatibleRegistrationDraftTransportV234(
        config=OpenAICompatibleConfig(
            base_url=values["ECOMSRE_LLM_BASE_URL"],
            api_key=values["ECOMSRE_LLM_API_KEY"],
            model=values["ECOMSRE_LLM_MODEL"],
        ),
        minimum_request_interval_seconds=args.minimum_request_interval,
        timeout_seconds=args.timeout,
        raw_artifact_dir=root / ".local/dta-v234/provider-raw/smoke",
    )
    artifact = run_provider_smoke_v234(
        repository_root=root,
        evaluation_root=root / "config/dta-v234/evaluation",
        manifest_path=root / "config/dta-v234/evaluation/manifest.json",
        output_path=root / "docs/analysis/dta-v234-provider-smoke.json",
        provider_transport=provider,
        expected_provider_model=provider.config.model,
        repair_record_path=args.repair_record,
        resume_after_fix=args.resume_after_fix or 0,
    )
    print(json.dumps({"status": artifact.status, "execution_count": artifact.execution_count, "role_count": artifact.role_count, "provider_calls": sum(item.provider_calls for item in artifact.runs), "protocol_repairs": sum(item.protocol_repairs for item in artifact.runs), "transport_retries": sum(item.transport_retries for item in artifact.runs), "real_fixes": artifact.real_fixes, "smoke_sha256": artifact.smoke_sha256}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
