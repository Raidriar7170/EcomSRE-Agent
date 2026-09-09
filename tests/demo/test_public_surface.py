"""Truth-surface guards for the public README."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_readme_exposes_current_product_and_links_preserved_history() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    for required in (
        "1 · 证据驱动诊断",
        "2 · 可部署 Product",
        "3 · 知识演化",
        "4 · 受限恢复",
        "v0.4.1",
        "docs/product/QUICKSTART.md",
        "docs/history/PROJECT_EVOLUTION.md",
        "docs/product/STATUS.md",
        "docs/product/LIMITATIONS.md",
        "docs/results/product-v024-nofault-acceptance-final.json",
        "docs/analysis/product-v030-family-and-rule-summary.json",
        "docs/results/product-v040-minimal-payment/live-result.json",
        "docs/results/product-v041-live-safety/README.md",
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

    # Keep the current state and live evidence visible before the long explanation.
    opening = "\n".join(readme.splitlines()[:20])
    assert "v0.4.1" in opening and "Payment" in opening
    assert "Live Safety Matrix" in opening
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
