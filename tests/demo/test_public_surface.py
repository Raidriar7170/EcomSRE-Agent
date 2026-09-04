"""Truth-surface guards for the public README."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_readme_exposes_current_product_and_links_preserved_history() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    for required in (
        "证据驱动的可靠诊断",
        "可部署的只读 Product MVP",
        "开放世界发现与人引导知识演化",
        "scripts.product.run_product_mvp_demo",
        "docs/history/PROJECT_EVOLUTION.md",
        "docs/product/STATUS.md",
        "docs/product/LIMITATIONS.md",
        "docs/results/product-v024-nofault-acceptance-final.json",
        "docs/results/product-v030-live-knowledge-evolution.json",
        "NO_INCIDENT",
        "EXTENSION_KNOWN",
        "action_authority = NONE",
        "OTHER_EXTENSION",
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

    assert 180 <= len(readme.splitlines()) <= 300
    history = (PROJECT_ROOT / "docs/history/PROJECT_EVOLUTION.md").read_text(
        encoding="utf-8"
    )
    for required in (
        "phase5b-v2-final-summary.md",
        "dta-v226-real-fault-comparison.md",
        "dta-v2341-registration-assistance-error-analysis.md",
        "ORIGINAL_ROOT_CAUSE_UNPROVEN",
    ):
        assert required in history
