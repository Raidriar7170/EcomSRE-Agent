"""Fail-closed source, seal, visible-pack, and Provider admission gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import subprocess
from typing import Any, Mapping, cast

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre.phase2.token_policy import MODEL_SNAPSHOT
from ecomsre.phase5b.contracts import HiddenPackManifest
from ecomsre.phase5b.freeze import verify_freeze_manifest

from scripts.phase5b_execution.contracts import canonical_json_bytes
from scripts.phase5b_execution.freeze import (
    EXECUTION_FREEZE_RELATIVE,
    sha256_regular_file,
    verify_execution_freeze_manifest,
)


_EXPECTED_RESULTS_BRANCH = "phase5b/v1-frozen-results"
_EXECUTION_AUTHORIZATION = "AUTHORIZE_PHASE5B_V1_SCORED_EXECUTION"
_VISIBLE_FILENAMES = (
    "changes.json",
    "incident.json",
    "logs.json",
    "manifest.json",
    "metrics.json",
    "traces.json",
)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key at execution admission")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant at execution admission: {value}")


def _load_canonical_object(path: Path) -> dict[str, object]:
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError("execution admission input must be a regular file")
    observed = path.read_bytes()
    payload = json.loads(
        observed,
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("execution admission input must be a JSON object")
    if observed != canonical_json_bytes(payload):
        raise ValueError("execution admission input must be canonical JSON")
    return cast(dict[str, object], payload)


def provider_configuration_preflight(
    environment: Mapping[str, str],
) -> dict[str, object]:
    """Return only safe Provider configuration facts; never the key or URL."""

    config = OpenAICompatibleConfig.from_environment(environment)
    model = environment.get("ECOMSRE_LLM_MODEL", "")
    return {
        "base_url_configured": bool(environment.get("ECOMSRE_LLM_BASE_URL", "").strip()),
        "api_key_configured": bool(environment.get("ECOMSRE_LLM_API_KEY", "").strip()),
        "model": model,
        "complete": config is not None,
        "frozen_model": config is not None and config.model == MODEL_SNAPSHOT,
    }


def require_scored_execution_authorization(
    environment: Mapping[str, str],
) -> None:
    if environment.get("PHASE5B_EXECUTION_AUTHORIZATION") != _EXECUTION_AUTHORIZATION:
        raise PermissionError("exact Phase 5B scored execution authorization is absent")


def require_provider_configuration(
    environment: Mapping[str, str],
) -> OpenAICompatibleConfig:
    config = OpenAICompatibleConfig.from_environment(environment)
    if config is None or config.model != MODEL_SNAPSHOT:
        raise ValueError("frozen Provider configuration is absent or mismatched")
    return config


def provider_configuration_fingerprint(
    config: OpenAICompatibleConfig,
) -> str:
    """Bind endpoint, model, and credential without recording plaintext values."""

    digest = hashlib.sha256()
    for value in (
        "phase5b.provider-config.v1",
        config.base_url,
        config.model,
        config.api_key,
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def verify_public_execution_seal(project_root: Path) -> dict[str, object]:
    seal = _load_canonical_object(
        project_root / "config/phase5b-seal/hidden-pack-seal.v1.json"
    )
    required = {
        "evaluation_version": "phase5b.v1",
        "sealed": True,
        "unblinded": False,
        "execution_entered": False,
        "provider_calls": 0,
        "agent_runs": 0,
    }
    if any(seal.get(key) != value for key, value in required.items()):
        raise ValueError("public hidden-pack seal is not execution-ready")
    return seal


def _git(project_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _git_bytes(project_root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ("git", *arguments),
        cwd=project_root,
        check=True,
        capture_output=True,
    ).stdout


def require_merged_execution_source(project_root: Path) -> tuple[str, str]:
    """Require the post-merge result worktree before canary or state entry."""

    verify_execution_freeze_manifest(project_root)
    verify_freeze_manifest(
        project_root,
        project_root / "config/phase5b/freeze-manifest.v1.json",
    )
    verify_public_execution_seal(project_root)
    head = _git(project_root, "rev-parse", "HEAD")
    origin_main = _git(project_root, "rev-parse", "origin/main")
    branch = _git(project_root, "branch", "--show-current")
    if head != origin_main:
        raise ValueError("execution source HEAD is not the current origin/main")
    if branch != _EXPECTED_RESULTS_BRANCH:
        raise ValueError("execution must use the frozen-results branch")
    if _git(project_root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("execution source contains changes or untracked files")
    relative = EXECUTION_FREEZE_RELATIVE.as_posix()
    _git(project_root, "ls-files", "--error-unmatch", relative)
    committed = _git_bytes(project_root, "show", f"origin/main:{relative}")
    if committed != (project_root / EXECUTION_FREEZE_RELATIVE).read_bytes():
        raise ValueError("execution freeze manifest is not merged in origin/main")
    return head, origin_main


def require_frozen_runtime_source(
    project_root: Path,
    *,
    expected_execution_freeze_sha256: str,
    expected_source_commit: str,
) -> None:
    verify_execution_freeze_manifest(project_root)
    verify_freeze_manifest(
        project_root,
        project_root / "config/phase5b/freeze-manifest.v1.json",
    )
    observed = sha256_regular_file(project_root / EXECUTION_FREEZE_RELATIVE)
    if observed != expected_execution_freeze_sha256:
        raise ValueError("execution freeze changed after execution started")
    if _git(project_root, "branch", "--show-current") != _EXPECTED_RESULTS_BRANCH:
        raise ValueError("frozen execution moved off the results branch")
    if _git(project_root, "rev-parse", "HEAD") != expected_source_commit:
        raise ValueError("execution source HEAD changed after execution started")
    if _git(project_root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("execution source contains changes or untracked files")
    source_paths = (
        "Makefile",
        "config/phase5b",
        "config/phase5b-execution",
        "config/phase5b-seal",
        "eval/phase5b_execution",
        "scripts/phase5b_execution",
        "src",
    )
    unchanged = subprocess.run(
        ("git", "diff", "--quiet", expected_source_commit, "--", *source_paths),
        cwd=project_root,
        check=False,
    )
    if unchanged.returncode != 0:
        raise ValueError("source tree changed after execution started")


def _visible_expected_paths() -> tuple[Path, ...]:
    return tuple(
        Path(f"hidden-{template:02d}") / f"seed-{seed:02d}" / filename
        for template in range(1, 7)
        for seed in range(5)
        for filename in _VISIBLE_FILENAMES
    )


def _canonical_visible_file(path: Path) -> bytes:
    payload = _load_canonical_object(path)
    return canonical_json_bytes(payload)


def verify_agent_visible_inputs(
    *,
    agent_visible_root: Path,
    hidden_pack_manifest_path: Path,
    expected_manifest_sha256: str,
    expected_agent_visible_pack_sha256: str,
) -> HiddenPackManifest:
    """Verify only the manifest and agent-visible half; never derive a truth path."""

    if sha256_regular_file(hidden_pack_manifest_path) != expected_manifest_sha256:
        raise ValueError("external hidden-pack manifest hash mismatch")
    manifest_bytes = hidden_pack_manifest_path.read_bytes()
    manifest = HiddenPackManifest.model_validate_json(manifest_bytes, strict=True)
    if manifest_bytes != canonical_json_bytes(manifest.model_dump(mode="json")):
        raise ValueError("external hidden-pack manifest is not canonical")
    if manifest.agent_visible_pack_sha256 != expected_agent_visible_pack_sha256:
        raise ValueError("hidden-pack manifest visible hash mismatch")
    root_details = agent_visible_root.lstat()
    if stat.S_ISLNK(root_details.st_mode) or not stat.S_ISDIR(root_details.st_mode):
        raise ValueError("agent-visible root must be a real directory")
    expected = _visible_expected_paths()
    observed: list[Path] = []
    for item in agent_visible_root.rglob("*"):
        details = item.lstat()
        if stat.S_ISLNK(details.st_mode):
            raise ValueError("agent-visible root contains a symlink")
        if stat.S_ISREG(details.st_mode):
            observed.append(item.relative_to(agent_visible_root))
        elif not stat.S_ISDIR(details.st_mode):
            raise ValueError("agent-visible root contains an unknown entry")
    if tuple(sorted(observed)) != tuple(sorted(expected)):
        raise ValueError("agent-visible root layout is incomplete or unknown")
    digest = hashlib.sha256()
    for relative in sorted(expected):
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_canonical_visible_file(agent_visible_root / relative))
        digest.update(b"\0")
    if digest.hexdigest() != expected_agent_visible_pack_sha256:
        raise ValueError("agent-visible pack hash mismatch")
    return manifest


def safe_execution_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Drop every truth or Builder locator before an execution worker is built."""

    denied_markers = ("GROUND_TRUTH", "HIDDEN_PACK_ROOT", "EVALUATOR", "BUILDER")
    return {
        key: value
        for key, value in environment.items()
        if not any(marker in key.upper() for marker in denied_markers)
    }
