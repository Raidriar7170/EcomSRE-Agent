from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from ecomsre_rcaeval_v2.dev2_execution import verify_model_prompt_lock


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG = PROJECT_ROOT / "config" / "rcaeval-re2-v2-dev2"
STARTING_COMMIT = "3b04ef340990e136312e1e1cbcf931a385cbe250"


def _load(name: str) -> dict[str, object]:
    value = json.loads((CONFIG / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_dev2_config_namespace_is_complete_and_protocol_bound() -> None:
    names = {
        "protocol.json",
        "dataset-lock.json",
        "split-lock.json",
        "model-prompt-lock.json",
        "budget-lock.json",
        "indicator-lock.json",
        "schedule-generation.json",
        "evaluation-policy.json",
    }
    assert {path.name for path in CONFIG.iterdir()} == names
    protocol_sha = hashlib.sha256((CONFIG / "protocol.json").read_bytes()).hexdigest()
    assert protocol_sha == "bdae9acab73e550a837807cfdb180015fde83a566eb745dae7be1887d5cad86f"
    for name in names - {"protocol.json"}:
        assert _load(name)["protocol_id"] == "rcaeval-re2-v2-dev.2"
        if name != "model-prompt-lock.json":
            assert _load(name)["protocol_sha256"] == protocol_sha
    schedule = _load("schedule-generation.json")
    assert schedule["schedule_domain"] == "rcaeval-re2-v2-dev2-schedule-v1"
    assert schedule["schedule_seed"] == 20260809
    assert _load("split-lock.json")["reallocation_performed"] is False


def test_dev2_model_prompt_lock_is_byte_equivalent_except_version_binding() -> None:
    observed = verify_model_prompt_lock()
    assert observed["protocol_id"] == "rcaeval-re2-v2-dev.2"
    assert observed["retry"] == {
        "fallback": "NO_FALLBACK",
        "semantic": "FORBIDDEN",
        "transport": "FORBIDDEN",
    }


def test_pr14_pr15_and_v1_tracked_evidence_remain_unchanged_from_start() -> None:
    protected = (
        "src/ecomsre_rcaeval",
        "scripts/rcaeval",
        "tests/benchmarks/rcaeval",
        "config/rcaeval-re2-v1",
        "config/rcaeval-re2-v2-dev",
        "config/rcaeval-re2-v2-dev1",
        "docs/review-evidence/rcaeval-re2-v2-dev",
        "docs/review-evidence/rcaeval-re2-v2-dev1",
        "docs/review-evidence/rcaeval-re2-v1-attribution",
        "docs/external-benchmarks/rcaeval-re2-v1-data-card.md",
        "docs/external-benchmarks/rcaeval-re2-v1-human-brief.md",
        "docs/external-benchmarks/rcaeval-re2-v1-protocol.md",
        "docs/external-benchmarks/rcaeval-re2-v2-dev-data-card.md",
        "docs/external-benchmarks/rcaeval-re2-v2-dev-protocol.md",
        "docs/external-benchmarks/rcaeval-re2-v2-dev1-data-card.md",
        "docs/external-benchmarks/rcaeval-re2-v2-dev1-protocol.md",
        "docs/results/rcaeval-re2-v1-attribution-aggregate.json",
        "docs/results/rcaeval-re2-v1-attribution-summary.md",
        "docs/results/rcaeval-re2-v2-dev-aggregate.json",
        "docs/results/rcaeval-re2-v2-dev-summary.md",
        "docs/results/rcaeval-re2-v2-dev1-design-aggregate.json",
        "docs/results/rcaeval-re2-v2-dev1-design-summary.md",
    )
    result = subprocess.run(
        ("git", "diff", "--quiet", STARTING_COMMIT, "--", *protected),
        cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0
