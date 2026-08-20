from __future__ import annotations

import json
from pathlib import Path

from scripts.ci import verify_dta_v22_pr_d_v4 as verifier_v4
from scripts.ci import verify_dta_v22_pr_d_v5 as verifier_v5


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_v4_pre_execution_absence_oracle_uses_an_empty_fixture(
    tmp_path: Path,
) -> None:
    assert verifier_v4.verify_public_results_v4(tmp_path) is None


def test_current_v4_public_result_remains_the_exact_blocked_campaign() -> None:
    verifier_v5._require_raw_bindings(
        REPO_ROOT, verifier_v5.HISTORICAL_PUBLIC_RAW_V5
    )
    campaign = json.loads(
        (
            REPO_ROOT
            / "docs/analysis/dta-v22-pr-d-provider-boundary-v4-campaign.json"
        ).read_text(encoding="utf-8")
    )
    assert campaign["campaign_sha256"] == (
        "1837365119ac0cf1fcd2ddbd50199387c47bdf6dfd88cf3f0e4b87382453fc3a"
    )
    assert campaign["terminal"] == "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"
    assert campaign["merge_ready"] is False
