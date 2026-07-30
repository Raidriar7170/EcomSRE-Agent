import json
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

DIAGNOSTIC_TARGETS = {"phase0-smoke"}

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

    assert (
        'PHASE0_CLI := env PYTHONPATH="$(PYTHONPATH)" '
        "uv run --frozen --no-sync"
    ) in text
    assert (
        'BOOTSTRAP_CLI := env PYTHONPATH="$(PYTHONPATH)" '
        "uv run --frozen --no-sync"
        in text
    )
    assert "$(BOOTSTRAP_CLI) bootstrap" in text
    assert "$(PHASE0_CLI) bootstrap" not in text


def test_makefile_binds_uv_and_temp_state_inside_project() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "UV_CACHE_DIR := $(PROJECT_ROOT)/.ecomsre-cache/uv" in text
    assert "TMPDIR := $(PROJECT_ROOT)/.ecomsre-tmp" in text
    assert "export UV_CACHE_DIR TMPDIR" in text
    assert "uv run --frozen --no-sync" in text
    assert "uvx" not in text
    for ignored in (
        ".ecomsre-cache/",
        ".ecomsre-tmp/",
        ".env",
        ".env.*",
        "!.env.example",
    ):
        assert ignored in gitignore


def test_every_phase0_target_prepares_safe_repo_local_uv_and_temp_directories() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")

    assert "UV_CACHE_ROOT := $(PROJECT_ROOT)/.ecomsre-cache" in text
    assert "phase0-prerequisites:" in text
    assert 'test ! -L "$$path"' in text
    assert 'test -O "$$path"' in text
    assert 'mkdir -p "$(UV_CACHE_ROOT)" "$(UV_CACHE_DIR)" "$(TMPDIR)"' in text
    assert 'chmod 700 "$(UV_CACHE_ROOT)" "$(UV_CACHE_DIR)" "$(TMPDIR)"' in text
    for target in CANONICAL_TARGETS | DIAGNOSTIC_TARGETS:
        assert f"{target}: phase0-prerequisites" in text


def test_makefile_exposes_noncanonical_one_cycle_smoke_target() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")

    for target in DIAGNOSTIC_TARGETS:
        assert f"{target}:" in text
    assert "$(PHASE0_CLI) smoke" in text


def test_makefile_contains_no_broad_docker_cleanup() -> None:
    text = MAKEFILE.read_text(encoding="utf-8").lower()

    for forbidden in FORBIDDEN_CLEANUP:
        assert forbidden not in text


@pytest.mark.parametrize(
    ("target", "command"),
    [
        ("phase0-bootstrap", "bootstrap"),
        ("phase0-preflight", "preflight"),
    ],
)
def test_make_cli_env_survives_project_root_with_spaces_without_docker(
    tmp_path: Path,
    target: str,
    command: str,
) -> None:
    stub_dir = tmp_path / "stub bin"
    stub_dir.mkdir()
    capture = tmp_path / f"{command}-capture.json"
    uv_stub = stub_dir / "uv"
    uv_stub.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

Path(os.environ["UV_STUB_CAPTURE"]).write_text(
    json.dumps(
        {
            "argv": sys.argv[1:],
            "pythonpath": os.environ.get("PYTHONPATH"),
            "uv_cache_dir": os.environ.get("UV_CACHE_DIR"),
            "tmpdir": os.environ.get("TMPDIR"),
            "run_id": os.environ.get("ECOMSRE_RUN_ID"),
        }
    ),
    encoding="utf-8",
)
""",
        encoding="utf-8",
    )
    uv_stub.chmod(0o700)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{stub_dir}{os.pathsep}{env['PATH']}",
            "UV_STUB_CAPTURE": str(capture),
        }
    )
    run_id = "a" * 32

    completed = subprocess.run(
        ["make", target, f"RUN_ID={run_id}"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    observed = json.loads(capture.read_text(encoding="utf-8"))
    assert observed == {
        "argv": [
            "run",
            "--frozen",
            "--no-sync",
            "python",
            "-m",
            "ecomsre.cli",
            "phase0",
            command,
        ],
        "pythonpath": str(ROOT / "src"),
        "uv_cache_dir": str(ROOT / ".ecomsre-cache" / "uv"),
        "tmpdir": str(ROOT / ".ecomsre-tmp"),
        "run_id": run_id,
    }


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
