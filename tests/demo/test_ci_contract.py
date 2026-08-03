"""The public CI surface stays offline, reproducible, and complete."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "agent-mainline.yml"


def test_agent_mainline_ci_runs_the_locked_offline_validation_surface() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    for required in (
        "runs-on: macos-14",
        "submodules: recursive",
        "uv sync --frozen --python 3.11 --group ci",
        "make phase1-test",
        "make phase2-test",
        "make phase2-compare",
        "make phase2-verify",
        "make phase3-test",
        "make phase3-replay",
        "make phase3-verify",
        "make agent-demo",
        "uv run --frozen --no-sync pytest -q",
        "uv run --frozen --no-sync ruff check .",
        "uv run --frozen --no-sync mypy",
    ):
        assert required in source

    lowered = source.casefold()
    for forbidden in (
        "provider-smoke",
        "phase0-",
        "docker ",
        "ecomsre_llm_api_key",
        "authorization:",
        "bearer ",
        "printenv",
        "env |",
        "enable-cache",
    ):
        assert forbidden not in lowered
