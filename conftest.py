"""Route frozen pre-execution-only assertions on typed DTA successor states."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


_V4_PRE_EXECUTION_ONLY_NODES = frozenset(
    {
        "tests/dta_v22/test_v22_pr_d_v4_verifier.py::test_v4_pre_execution_admission_binds_manifest_history_and_progress",
        "tests/dta_v22/test_v22_pr_d_v4_verifier.py::test_v4_hosted_admission_does_not_require_private_v3_files",
        "tests/dta_v22/test_v22_pr_d_v4_verifier.py::test_v4_public_results_are_absent_before_formal_campaign",
    }
)
_V5_STATES = frozenset(
    {"V5_PRE_EXECUTION_READY", "V5_COMPLETE_PASS", "V5_COMPLETE_BLOCKED"}
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    progress_path = (
        Path(__file__).resolve().parent
        / "docs/analysis/dta-v22-p0-master-progress.json"
    )
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    state = progress.get("provider_compatibility_v5_state")
    if state is None:
        return
    if state not in _V5_STATES:
        raise pytest.UsageError("DTA v2.2 v5 progress state is not typed")
    marker = pytest.mark.skip(
        reason=(
            "frozen v4 assertion is pre-execution-only; v5 verifies the immutable "
            "v4 post state and retains a separate empty-fixture absence oracle"
        )
    )
    for item in items:
        if item.nodeid in _V4_PRE_EXECUTION_ONLY_NODES:
            item.add_marker(marker)
