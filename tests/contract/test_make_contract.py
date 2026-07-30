import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"

CANONICAL_TARGETS = {
    "phase0-bootstrap",
    "phase0-preflight",
    "phase0-up",
    "phase0-health",
    "phase0-inject",
    "phase0-reset",
    "phase0-status",
    "phase0-accept",
    "phase0-stop",
}

FORBIDDEN_CLEANUP = {
    "docker system prune",
    "docker volume prune",
    "docker network prune",
    "docker container prune",
}


def test_makefile_exposes_every_canonical_phase0_target() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")

    for target in CANONICAL_TARGETS:
        assert f"{target}:" in text


def test_make_targets_are_thin_non_interactive_cli_adapters() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")

    assert "python -m ecomsre.cli phase0" in text
    for target in CANONICAL_TARGETS:
        command = target.removeprefix("phase0-")
        variable = "BOOTSTRAP_CLI" if command == "bootstrap" else "PHASE0_CLI"
        assert f"$({variable}) {command}" in text
    assert "--run-id $(RUN_ID)" not in text
    assert "export ECOMSRE_RUN_ID" in text
    assert "read " not in text
    assert "input(" not in text


def test_formal_make_targets_are_frozen_and_do_not_sync() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")

    assert "PHASE0_CLI := PYTHONPATH=$(PYTHONPATH) uv run --frozen --no-sync" in text
    assert "BOOTSTRAP_CLI := PYTHONPATH=$(PYTHONPATH) uv run python" in text
    assert "$(BOOTSTRAP_CLI) bootstrap" in text
    assert "$(PHASE0_CLI) bootstrap" not in text


def test_makefile_contains_no_broad_docker_cleanup() -> None:
    text = MAKEFILE.read_text(encoding="utf-8").lower()

    for forbidden in FORBIDDEN_CLEANUP:
        assert forbidden not in text


@pytest.mark.parametrize(
    "malicious_run_id",
    [
        "bad;touch make-injection-marker",
        "bad$(touch make-injection-marker)",
        "bad`touch make-injection-marker`",
        "bad\n;touch make-injection-marker",
    ],
)
def test_malicious_make_run_id_fails_closed_without_shell_execution(
    tmp_path: Path,
    malicious_run_id: str,
) -> None:
    marker = tmp_path / "make-injection-marker"
    payload = malicious_run_id.replace(
        "make-injection-marker",
        str(marker),
    )
    env = os.environ.copy()
    env["PATH"] = os.environ["PATH"]

    dry_run = subprocess.run(
        ["make", "-n", "phase0-up", f"RUN_ID={payload}"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    actual = subprocess.run(
        ["make", "phase0-up", f"RUN_ID={payload}"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert str(marker) not in dry_run.stdout
    assert actual.returncode != 0
    assert not marker.exists()
