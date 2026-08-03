from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ecomsre.backends.replay import load_replay_case
from ecomsre.phase1.contracts import RCADecision
from ecomsre.phase4.contracts import DomainFaultMechanism, DomainGroundTruth


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VISIBLE_ROOT = PROJECT_ROOT / "config/phase4/replay-cases/agent-visible"
GROUND_TRUTH_ROOT = PROJECT_ROOT / "eval/phase4/ground-truth"
PHASE1_VISIBLE_ROOT = PROJECT_ROOT / "config/phase1/replay-cases/agent-visible"
EXPECTED_CASES = (
    "ranking-change-with-normal-search-sli",
    "recommendation-feature-evidence-insufficient",
    "recommendation-model-feature-schema-mismatch",
    "search-feature-freshness-lag-complete",
    "search-ranking-configuration-frontend-decoy",
)
EXPECTED_PHASE1_TREE = "6737e487c27acaf6ebfa3794d6753a5e914c3c3d"


def _canonical_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _git_tree(path: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", f"HEAD:{path}"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_phase4_has_exactly_five_isolated_replay_case_pairs() -> None:
    visible = tuple(sorted(path.name for path in VISIBLE_ROOT.iterdir()))
    truths = tuple(sorted(path.stem for path in GROUND_TRUTH_ROOT.glob("*.json")))
    assert visible == EXPECTED_CASES
    assert truths == EXPECTED_CASES
    assert tuple(sorted(path.name for path in PHASE1_VISIBLE_ROOT.iterdir())) == (
        "ad-change-with-normal-sli",
        "ad-partial-failure-complete",
        "ad-partial-failure-frontend-decoy",
        "ad-partial-failure-without-logs",
        "no-real-incident",
        "recommendation-cache-failure",
        "telemetry-insufficient",
    )
    assert _git_tree("config/phase1/replay-cases") == EXPECTED_PHASE1_TREE


def test_phase4_cases_are_canonical_hash_bound_and_loadable() -> None:
    for case_id in EXPECTED_CASES:
        case_root = VISIBLE_ROOT / case_id
        assert not case_root.is_symlink()
        entries = {path.name for path in case_root.iterdir()}
        assert entries == {
            "changes.json",
            "incident.json",
            "logs.json",
            "manifest.json",
            "metrics.json",
            "traces.json",
        }
        manifest = json.loads((case_root / "manifest.json").read_bytes())
        assert (case_root / "manifest.json").read_bytes() == _canonical_bytes(manifest)
        assert manifest["case_id"] == case_id
        for filename, expected_sha in manifest["files"].items():
            path = case_root / filename
            assert not path.is_symlink()
            payload = json.loads(path.read_bytes())
            assert path.read_bytes() == _canonical_bytes(payload)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha
        loaded = load_replay_case(VISIBLE_ROOT, case_id)
        assert loaded.case_id == case_id


def test_phase4_ground_truth_distribution_and_decoy_are_frozen() -> None:
    truths: dict[str, DomainGroundTruth] = {}
    for case_id in EXPECTED_CASES:
        path = GROUND_TRUTH_ROOT / f"{case_id}.json"
        payload = json.loads(path.read_bytes())
        assert path.read_bytes() == _canonical_bytes(payload)
        truths[case_id] = DomainGroundTruth.model_validate(payload)

    assert [truth.expected_decision for truth in truths.values()].count(
        RCADecision.RCA_CONFIRMED
    ) == 3
    assert [truth.expected_decision for truth in truths.values()].count(
        RCADecision.NEED_MORE_EVIDENCE
    ) == 1
    assert [truth.expected_decision for truth in truths.values()].count(
        RCADecision.ABSTAIN
    ) == 1
    assert truths[
        "search-feature-freshness-lag-complete"
    ].expected_fault_mechanism is DomainFaultMechanism.FEATURE_FRESHNESS_LAG
    assert truths[
        "recommendation-model-feature-schema-mismatch"
    ].expected_fault_mechanism is (
        DomainFaultMechanism.MODEL_FEATURE_SCHEMA_MISMATCH
    )
    assert truths[
        "search-ranking-configuration-frontend-decoy"
    ].expected_fault_mechanism is (
        DomainFaultMechanism.RANKING_CONFIGURATION_FAILURE
    )
    assert truths[
        "search-ranking-configuration-frontend-decoy"
    ].decoy_evidence == (
        "CHANGES:frontend:frontend_rollout",
    )


def test_agent_visible_case_files_contain_no_evaluator_markers() -> None:
    forbidden = (
        "expected_decision",
        "expected_root_service",
        "expected_fault_mechanism",
        "ground_truth",
        "evaluator_only",
        "answer_key",
    )
    for case_id in EXPECTED_CASES:
        for path in (VISIBLE_ROOT / case_id).iterdir():
            text = path.read_text(encoding="utf-8").casefold()
            assert not any(marker in text for marker in forbidden)
