"""Truth-surface guards for the public README."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_readme_exposes_the_demo_and_preserves_phase_boundaries() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    for required in (
        "make agent-demo",
        "PHASE1_SINGLE_AGENT_REPLAY_MVP_READY",
        "PHASE2_MULTI_AGENT_REPLAY_MVP_READY",
        "PHASE3_RESTRICTED_REMEDIATION_REPLAY_MVP_READY",
        "PHASE4_OFFLINE_ECOMMERCE_DOMAIN_REPLAY_MVP_READY",
        "Phase 5 | Not entered",
        "make phase4-demo",
        "canonical acceptance is **not complete**",
        "7 cases × 3 variants",
        "4 bounded Fixed/Dynamic positive/negative runs",
        "6 deterministic replay cases",
        "does not establish Multi-Agent superiority",
        "replay-only",
    ):
        assert required in readme

    lowered = readme.casefold()
    for forbidden in (
        "is a production autonomous sre",
        "production-ready",
        "phase 0: complete",
        "real-provider 7/7",
        "multi-agent outperforms",
        "live remediation is supported",
    ):
        assert forbidden not in lowered
