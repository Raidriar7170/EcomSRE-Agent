from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_repository_contains_no_real_hidden_pack_or_truth() -> None:
    assert not (PROJECT_ROOT / "config/phase5b/agent-visible").exists()
    assert not (PROJECT_ROOT / "config/phase5b/ground-truth").exists()
    assert not (PROJECT_ROOT / "eval/phase5b/ground-truth").exists()


def test_phase5b_worker_has_no_provider_or_evaluator_import() -> None:
    source = (PROJECT_ROOT / "src/ecomsre/phase5b/worker.py").read_text(encoding="utf-8")
    assert "provider" not in source.lower()
    assert "ground_truth" not in source
    assert "template_id" not in source
    assert "seed_id" not in source
