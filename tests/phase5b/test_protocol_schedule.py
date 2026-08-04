from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

import pytest

from ecomsre.phase5b.contracts import FrozenEvaluationManifest, ScheduledRun

from ecomsre.phase5b.protocol import load_seed_policy, load_suite_registry
from ecomsre.phase5b.schedule import build_execution_schedule
from ecomsre.phase5b.seeds import seed_material


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PROJECT_ROOT / "config/phase5b"

PUBLIC_ANCHORS = (
    "ad-partial-failure-complete",
    "ad-partial-failure-without-logs",
    "ad-partial-failure-frontend-decoy",
    "recommendation-cache-failure",
    "recommendation-feature-evidence-insufficient",
    "ranking-change-with-normal-search-sli",
)
HIDDEN_SLOTS = tuple(f"hidden-{index:02d}" for index in range(1, 7))
VARIANTS = (
    "SINGLE_AGENT_V2",
    "FIXED_SPECIALIST_V2",
    "DYNAMIC_MULTI_AGENT_V2",
)
SEEDS = tuple(f"seed-{index:02d}" for index in range(5))


def test_suite_and_seed_policy_are_exactly_frozen() -> None:
    suite = load_suite_registry(CONFIG_ROOT / "suite-registry.v1.json")
    policy = load_seed_policy(CONFIG_ROOT / "seed-policy.v1.json")

    assert suite.evaluation_version == "phase5b.v1"
    assert suite.template_count == 12
    assert suite.public_anchor_count == 6
    assert suite.hidden_template_count == 6
    assert suite.hidden_share == 0.5
    assert tuple(item.template_id for item in suite.public_anchors) == PUBLIC_ANCHORS
    assert tuple(item.template_id for item in suite.hidden_slots) == HIDDEN_SLOTS
    assert all(item.actual_content_created is False for item in suite.hidden_slots)
    assert all(item.actual_ground_truth_created is False for item in suite.hidden_slots)

    assert policy.evaluation_version == "phase5b.v1"
    assert policy.seed_ids == SEEDS
    assert policy.seed_count_per_template == 5
    assert policy.temperature == 0
    assert "ground_truth_decision" in policy.forbidden_transformations
    assert "evidence_declaration_order" in policy.allowed_transformations


def test_seed_material_is_deterministic_and_variant_independent() -> None:
    first = seed_material("phase5b.v1", "hidden-01", "seed-00")
    second = seed_material("phase5b.v1", "hidden-01", "seed-00")
    changed = seed_material("phase5b.v1", "hidden-01", "seed-01")

    assert first == second
    assert first != changed
    assert len(first) == 64
    assert set(first) <= set("0123456789abcdef")


def test_main_schedule_is_paired_unique_retry_free_and_position_balanced() -> None:
    suite = load_suite_registry(CONFIG_ROOT / "suite-registry.v1.json")
    policy = load_seed_policy(CONFIG_ROOT / "seed-policy.v1.json")
    schedule = build_execution_schedule(suite, policy)

    assert schedule.evaluation_version == "phase5b.v1"
    assert schedule.pairing_unit_count == 60
    assert schedule.run_count == 180
    assert schedule.hidden_retry is False
    assert schedule.scripted_fallback is False
    assert len({item.run_id for item in schedule.runs}) == 180

    by_pair: dict[tuple[str, str], list[ScheduledRun]] = defaultdict(list)
    for item in schedule.runs:
        by_pair[(item.template_id, item.seed_id)].append(item)
        assert "retry" not in item.model_dump()
    assert len(by_pair) == 60
    for items in by_pair.values():
        assert {item.variant for item in items} == set(VARIANTS)
        assert {item.call_position for item in items} == {1, 2, 3}
        assert len({item.seed_material_sha256 for item in items}) == 1
        assert len({item.pairing_unit_id for item in items}) == 1

    balance = Counter((item.variant, item.call_position) for item in schedule.runs)
    assert set(balance.values()) == {20}


def test_suite_and_seed_contracts_reject_semantic_drift(tmp_path: Path) -> None:
    suite_payload = (CONFIG_ROOT / "suite-registry.v1.json").read_text()
    changed_suite = tmp_path / "suite.json"
    changed_suite.write_text(
        suite_payload.replace("causal_chain_reconstruction", "root_disambiguation", 1),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="frozen hidden coverage slots"):
        load_suite_registry(changed_suite)

    seed_payload = (CONFIG_ROOT / "seed-policy.v1.json").read_text()
    changed_seed = tmp_path / "seed.json"
    changed_seed.write_text(
        seed_payload.replace("safe_lexical_variant_index", "arbitrary_prompt_variant"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="frozen allowed transformations"):
        load_seed_policy(changed_seed)


@pytest.mark.parametrize("path", ("/absolute.py", "../escape.py", "bad\\path.py"))
def test_freeze_manifest_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError, match="path"):
        FrozenEvaluationManifest(
            schema_version="phase5b.freeze-manifest.v1",
            evaluation_version="phase5b.v1",
            base_main_commit="30c202adb74d5f2e9224098e4f51eb19f214f275",
            provider="openai-compatible",
            model_snapshot="gpt-5.4-mini-2026-03-17",
            temperature=0,
            max_model_calls=8,
            max_tool_calls=8,
            max_tokens=32000,
            max_completion_tokens=2048,
            provider_pacing_seconds=2,
            hidden_retry=False,
            scripted_fallback=False,
            frozen_files={path: "a" * 64},
        )
