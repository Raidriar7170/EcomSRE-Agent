from pathlib import Path
import hashlib
import subprocess

import pytest

from scripts.ci.verify_dta_v22_pr_b import verify_pr_b_bindings
from scripts.ci.verify_dta_v22_pr_c import verify_pr_c_bindings
from scripts.ci.product_v030_source_successor import (
    GOAL_BASE_V030,
    QUEUE_SIGNAL_SOURCE_SHA256_V030,
    matches_queue_signal_successor_v030,
)


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("verify", [verify_pr_b_bindings, verify_pr_c_bindings])
def test_queue_signal_preserves_historical_bindings(verify):
    assert verify(ROOT)["stage"] in {"PR-B", "PR-C"}


@pytest.mark.parametrize("relative", tuple(QUEUE_SIGNAL_SOURCE_SHA256_V030))
def test_successor_rejects_any_other_source_or_historical_drift(relative, tmp_path):
    current = (ROOT / relative).read_bytes()
    historical = subprocess.run(
        ("git", "show", f"{GOAL_BASE_V030}:{relative}"),
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    historical_sha = hashlib.sha256(historical).hexdigest()
    assert matches_queue_signal_successor_v030(ROOT, relative, current, historical_sha)
    assert not matches_queue_signal_successor_v030(
        ROOT, relative, current + b"\n", historical_sha
    )
    assert not matches_queue_signal_successor_v030(ROOT, relative, current, "0" * 64)
    assert not matches_queue_signal_successor_v030(
        ROOT, "src/ecomsre/dta_v2/v22/action_catalog.py", current, historical_sha
    )
    assert not matches_queue_signal_successor_v030(
        tmp_path, relative, current, historical_sha
    )
