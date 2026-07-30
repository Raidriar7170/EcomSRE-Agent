import importlib
import subprocess
from pathlib import Path

import pytest

from ecomsre.environment.preflight import CommandResult
from ecomsre.phase0.models import Outcome


ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = ROOT / "third_party" / "opentelemetry-demo"
EXPECTED_URL = "https://github.com/open-telemetry/opentelemetry-demo.git"
EXPECTED_TAG = "3.0.0"
EXPECTED_COMMIT = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"


class FixtureRunner:
    def __init__(self, results: dict[tuple[str, ...], CommandResult]) -> None:
        self.results = results
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
        environment: dict[str, str] | None = None,
    ) -> CommandResult:
        assert timeout_seconds > 0
        assert environment in (None, {})
        self.calls.append(arguments)
        return self.results[arguments]


def _upstream_module():
    try:
        return importlib.import_module("ecomsre.environment.upstream")
    except ModuleNotFoundError:
        pytest.fail("frozen upstream verifier is not implemented")


def test_submodule_metadata_and_live_checkout_match_the_frozen_decision() -> None:
    metadata = (ROOT / ".gitmodules").read_text(encoding="utf-8")

    assert "path = third_party/opentelemetry-demo" in metadata
    assert f"url = {EXPECTED_URL}" in metadata
    assert (UPSTREAM / ".git").is_file()
    assert _git("rev-parse", "HEAD") == EXPECTED_COMMIT
    assert _git("describe", "--tags", "--exact-match", "HEAD") == EXPECTED_TAG
    assert _git("status", "--porcelain", "--untracked-files=all") == ""


def test_upstream_contract_allows_only_core_and_observability_compose() -> None:
    upstream = _upstream_module()

    assert upstream.UPSTREAM_URL == EXPECTED_URL
    assert upstream.UPSTREAM_TAG == EXPECTED_TAG
    assert upstream.UPSTREAM_COMMIT == EXPECTED_COMMIT
    assert upstream.ALLOWED_COMPOSE_FILES == (
        "compose.yaml",
        "compose.observability.yaml",
    )
    assert {
        "compose.full.yaml",
        "compose.extras.yaml",
        "compose.profiling.yaml",
        "compose.agent.yaml",
        "compose.tests.yaml",
    }.isdisjoint(upstream.ALLOWED_COMPOSE_FILES)


def test_fixture_verification_fails_closed_on_dirty_or_mismatched_checkout() -> None:
    upstream = _upstream_module()
    root = Path("/repo")
    checkout = root / "third_party" / "opentelemetry-demo"
    commit_command = ("git", "-C", str(checkout), "rev-parse", "HEAD")
    tag_command = (
        "git",
        "-C",
        str(checkout),
        "describe",
        "--tags",
        "--exact-match",
        "HEAD",
    )
    status_command = (
        "git",
        "-C",
        str(checkout),
        "status",
        "--porcelain",
        "--untracked-files=all",
    )
    runner = FixtureRunner(
        {
            commit_command: _result(commit_command, stdout="0" * 40 + "\n"),
            tag_command: _result(tag_command, stdout=EXPECTED_TAG + "\n"),
            status_command: _result(status_command, stdout=" M compose.yaml\n"),
        }
    )

    verification = upstream.verify_frozen_upstream(root, runner)

    assert verification.outcome is Outcome.BLOCKED_UPSTREAM
    assert verification.exit_code == 21
    assert set(verification.reason_codes) == {
        "UPSTREAM_COMMIT_MISMATCH",
        "UPSTREAM_WORKTREE_DIRTY",
    }
    assert runner.calls == [commit_command, tag_command, status_command]


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(UPSTREAM), *arguments],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return completed.stdout.strip()


def _result(
    arguments: tuple[str, ...],
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> CommandResult:
    return CommandResult(
        arguments=arguments,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )
