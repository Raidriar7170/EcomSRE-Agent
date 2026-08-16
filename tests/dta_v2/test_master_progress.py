from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PROGRESS_PATH = REPO_ROOT / "docs/analysis/dta-v2-master-progress.json"


def test_master_progress_records_completed_post_merge_state() -> None:
    progress = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))

    assert progress["schema_version"] == "dta-v2-master-progress.v1"
    assert progress["goal_version"] == "dta-v2-master-v1"
    assert progress["completed_stage"] == "PR-F"
    assert progress["current_stage"] == "COMPLETE"
    assert progress["main_head"] == (
        "9906f63df0e4f7cf65b4061ac24ea0061c14680a"
    )
    assert progress["active_branch"] is None
    assert progress["active_pr"] is None
    assert progress["merged_prs"] == [43, 44, 45, 46, 47, 48]
    assert (
        progress["live_demo_terminal"]
        == "DTA_V2_LIVE_DEMO_ACCEPTANCE_PASS"
    )
