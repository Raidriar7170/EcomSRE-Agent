"""Callable exact PR-F owned campaign entrypoint.

This module intentionally has no single-attempt or generic lifecycle mode.  One
invocation claims and runs the frozen NO_FAULT, PAYMENT, RECOMMENDATION, EMAIL
schedule through :class:`OwnedLiveCampaign`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre.dta_v2.authorization import load_master_authorization
from ecomsre.dta_v2.live_contracts import load_live_demo_config
from ecomsre.dta_v2.live_owned import OwnedLiveCampaign, run_owned_live_campaign
from ecomsre.dta_v2.live_reporting import (
    build_public_live_campaign_report,
    write_public_live_campaign_artifacts,
)
from ecomsre.dta_v2.registry import load_runbook_registry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the exact four-attempt DTA v2 owned local campaign once."
    )
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--master-authorization", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--stabilization-seconds", type=int, default=90)
    parser.add_argument(
        "--public-result-root",
        type=Path,
        help="Existing docs/results directory; written only after exact LIVE acceptance.",
    )
    return parser


def main(argv: tuple[str, ...] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    repository_root = arguments.repository_root.resolve()
    config = load_live_demo_config(
        repository_root / "config/dta-v2/live-demo.v1.json"
    )
    registry = load_runbook_registry(repository_root / "config/dta-v2/runbooks")
    master = load_master_authorization(arguments.master_authorization)
    campaign = OwnedLiveCampaign(
        repository_root=repository_root,
        private_root=arguments.private_root,
        campaign_id=arguments.campaign_id,
        provider_env_path=arguments.provider_env,
        config=config,
        registry=registry,
        master_authorization=master,
        stabilization_seconds=arguments.stabilization_seconds,
    )
    closures = run_owned_live_campaign(campaign)
    report = build_public_live_campaign_report(closures)
    if arguments.public_result_root is not None:
        write_public_live_campaign_artifacts(
            result_root=arguments.public_result_root,
            report=report,
        )
    return 0 if report.terminal == "DTA_V2_LIVE_DEMO_ACCEPTANCE_PASS" else 2


if __name__ == "__main__":  # pragma: no cover - exercised by the module CLI
    raise SystemExit(main())
