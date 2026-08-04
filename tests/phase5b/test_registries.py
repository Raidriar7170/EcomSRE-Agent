from __future__ import annotations

from pathlib import Path

from ecomsre.phase5b.registry import (
    validate_ablation_registry,
    validate_hidden_pack_contract,
    validate_metrics_registry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PROJECT_ROOT / "config/phase5b"


def test_hidden_pack_contract_is_exact_and_fail_closed() -> None:
    contract = validate_hidden_pack_contract(CONFIG_ROOT / "hidden-pack-contract.v1.json")
    assert contract["template_count"] == 6
    assert contract["ground_truth_read_after_execution_complete_only"] is True
    assert contract["worker_allowed_root"] == "agent-visible"


def test_metrics_registry_preserves_replay_only_boundary() -> None:
    registry = validate_metrics_registry(CONFIG_ROOT / "metrics-registry.v1.json")
    assert registry["remediation_scope"] == "replay-only remediation; not production remediation"
    assert registry["wall_clock_latency_reported_separately"] is True


def test_ablation_registry_freezes_exact_38_non_primary_runs() -> None:
    registry = validate_ablation_registry(CONFIG_ROOT / "ablation-registry.v1.json")
    assert registry["diagnosis_run_count"] == 36
    assert registry["remediation_run_count"] == 2
    assert registry["ablation_run_count"] == 38
    assert all(item["primary_eligible"] is False for item in registry["ablations"])
