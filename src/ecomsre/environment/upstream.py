"""Frozen OpenTelemetry Demo checkout verification."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, model_validator

from ecomsre.environment.manifests import UPSTREAM_COMMIT, UPSTREAM_TAG
from ecomsre.environment.preflight import CommandResult
from ecomsre.phase0.models import Outcome


UPSTREAM_URL = "https://github.com/open-telemetry/opentelemetry-demo.git"
UPSTREAM_RELATIVE_PATH = Path("third_party/opentelemetry-demo")
ALLOWED_COMPOSE_FILES = (
    "compose.yaml",
    "compose.observability.yaml",
)


class UpstreamRunner(Protocol):
    def run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
        environment: dict[str, str] | None = None,
    ) -> CommandResult: ...


class UpstreamVerification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Outcome
    exit_code: int
    reason_codes: tuple[str, ...]
    observed_commit: str | None
    observed_tag: str | None
    worktree_clean: bool

    @model_validator(mode="after")
    def require_consistent_result(self) -> "UpstreamVerification":
        if self.exit_code != self.outcome.exit_code:
            raise ValueError("upstream exit code conflicts with outcome")
        if self.outcome is Outcome.SUCCESS and self.reason_codes:
            raise ValueError("successful upstream verification has reasons")
        if self.outcome is not Outcome.SUCCESS and not self.reason_codes:
            raise ValueError("blocked upstream verification requires reasons")
        return self


def bootstrap_frozen_upstream(
    project_root: Path,
    runner: UpstreamRunner,
) -> UpstreamVerification:
    """Initialize only the declared submodule, then verify its exact checkout."""
    root = Path(project_root).resolve()
    checkout = root / UPSTREAM_RELATIVE_PATH
    if not (checkout / ".git").is_file():
        arguments = (
            "git",
            "-C",
            str(root),
            "submodule",
            "update",
            "--init",
            "--checkout",
            "--depth",
            "1",
            "--",
            str(UPSTREAM_RELATIVE_PATH),
        )
        result = runner.run(
            arguments,
            timeout_seconds=300,
            environment=None,
        )
        if result.exit_code != 0:
            return _verification(
                Outcome.BLOCKED_UPSTREAM,
                ("UPSTREAM_FETCH_UNAVAILABLE",),
            )
    return verify_frozen_upstream(root, runner)


def verify_frozen_upstream(
    project_root: Path,
    runner: UpstreamRunner,
) -> UpstreamVerification:
    """Verify commit, release tag, and a clean read-only upstream worktree."""
    checkout = Path(project_root).resolve() / UPSTREAM_RELATIVE_PATH
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
    commit_result = runner.run(
        commit_command,
        timeout_seconds=10,
        environment=None,
    )
    tag_result = runner.run(
        tag_command,
        timeout_seconds=10,
        environment=None,
    )
    status_result = runner.run(
        status_command,
        timeout_seconds=10,
        environment=None,
    )

    observed_commit = (
        commit_result.stdout.strip() if commit_result.exit_code == 0 else None
    )
    observed_tag = tag_result.stdout.strip() if tag_result.exit_code == 0 else None
    clean = status_result.exit_code == 0 and not status_result.stdout.strip()
    reasons: list[str] = []
    if observed_commit != UPSTREAM_COMMIT:
        reasons.append("UPSTREAM_COMMIT_MISMATCH")
    if observed_tag != UPSTREAM_TAG:
        reasons.append("UPSTREAM_TAG_MISMATCH")
    if not clean:
        reasons.append("UPSTREAM_WORKTREE_DIRTY")
    if reasons:
        return _verification(
            Outcome.BLOCKED_UPSTREAM,
            tuple(reasons),
            observed_commit=observed_commit,
            observed_tag=observed_tag,
            worktree_clean=clean,
        )
    return _verification(
        Outcome.SUCCESS,
        (),
        observed_commit=observed_commit,
        observed_tag=observed_tag,
        worktree_clean=True,
    )


def _verification(
    outcome: Outcome,
    reason_codes: tuple[str, ...],
    *,
    observed_commit: str | None = None,
    observed_tag: str | None = None,
    worktree_clean: bool = False,
) -> UpstreamVerification:
    return UpstreamVerification(
        outcome=outcome,
        exit_code=outcome.exit_code,
        reason_codes=reason_codes,
        observed_commit=observed_commit,
        observed_tag=observed_tag,
        worktree_clean=worktree_clean,
    )
