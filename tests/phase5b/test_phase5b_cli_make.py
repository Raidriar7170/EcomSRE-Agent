from __future__ import annotations

from pathlib import Path

from ecomsre.phase5b.cli import verify_dry_run_report, write_dry_run_report


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_makefile_exposes_only_offline_phase5b_targets() -> None:
    makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")
    for target in (
        "phase5b-test",
        "phase5b-preflight",
        "phase5b-protocol-verify",
        "phase5b-schedule",
        "phase5b-dry-run",
        "phase5b-dry-run-verify",
    ):
        assert f"{target}:" in makefile
    assert "phase5b-run-provider" not in makefile
    assert "phase5b-unblind-real" not in makefile


def test_cli_writes_and_verifies_deterministic_mock_report(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    write_dry_run_report(report_path)
    first = verify_dry_run_report(report_path)
    write_dry_run_report(report_path)
    assert verify_dry_run_report(report_path) == first
