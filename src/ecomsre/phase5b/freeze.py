"""Flat SHA-256 freeze manifest generation and fail-closed preflight."""

from __future__ import annotations

import hashlib
from pathlib import Path
import stat

from ecomsre.phase5b.contracts import FrozenEvaluationManifest
from ecomsre.phase5b.protocol import load_strict_json


BASE_MAIN_COMMIT = "30c202adb74d5f2e9224098e4f51eb19f214f275"
_PUBLIC_ANCHORS = (
    ("config/phase1/replay-cases/agent-visible", "eval/phase1/ground-truth", "ad-partial-failure-complete"),
    ("config/phase1/replay-cases/agent-visible", "eval/phase1/ground-truth", "ad-partial-failure-without-logs"),
    ("config/phase1/replay-cases/agent-visible", "eval/phase1/ground-truth", "ad-partial-failure-frontend-decoy"),
    ("config/phase1/replay-cases/agent-visible", "eval/phase1/ground-truth", "recommendation-cache-failure"),
    ("config/phase4/replay-cases/agent-visible", "eval/phase4/ground-truth", "recommendation-feature-evidence-insufficient"),
    ("config/phase4/replay-cases/agent-visible", "eval/phase4/ground-truth", "ranking-change-with-normal-search-sli"),
)
_VISIBLE_FILES = (
    "changes.json",
    "incident.json",
    "logs.json",
    "manifest.json",
    "metrics.json",
    "traces.json",
)
_FIXED_RUNTIME_PATHS = (
    "pyproject.toml",
    "uv.lock",
    "config/phase1/agent.json",
)
_RUNTIME_SOURCE_ROOTS = (
    "src/ecomsre",
)


def sha256_regular_file(path: Path) -> str:
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError("frozen path must be a regular non-symlink file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def required_frozen_paths(project_root: Path) -> tuple[str, ...]:
    paths = set(_FIXED_RUNTIME_PATHS)
    for relative in (*_RUNTIME_SOURCE_ROOTS, "eval/phase5b"):
        root = project_root / relative
        if not root.is_dir() or root.is_symlink():
            raise ValueError(f"required Phase 5B root is absent: {relative}")
        for path in root.rglob("*"):
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix == ".py"
                and path.name != "freeze-manifest.v1.json"
                and "__pycache__" not in path.parts
            ):
                paths.add(path.relative_to(project_root).as_posix())
    for relative in ("config/phase2", "config/phase5b"):
        root = project_root / relative
        if not root.is_dir() or root.is_symlink():
            raise ValueError(f"required Phase 5B config root is absent: {relative}")
        for path in root.rglob("*"):
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix in {".json", ".tiktoken"}
                and path.name != "freeze-manifest.v1.json"
            ):
                paths.add(path.relative_to(project_root).as_posix())
    for visible_root, truth_root, template_id in _PUBLIC_ANCHORS:
        paths.update(f"{visible_root}/{template_id}/{name}" for name in _VISIBLE_FILES)
        paths.add(f"{truth_root}/{template_id}.json")
    return tuple(sorted(paths))


def build_freeze_manifest(project_root: Path) -> FrozenEvaluationManifest:
    bindings = {
        relative: sha256_regular_file(project_root / relative)
        for relative in required_frozen_paths(project_root)
    }
    return FrozenEvaluationManifest(
        schema_version="phase5b.freeze-manifest.v1",
        evaluation_version="phase5b.v1",
        base_main_commit=BASE_MAIN_COMMIT,
        provider="openai-compatible",
        model_snapshot="gpt-5.4-mini-2026-03-17",
        temperature=0,
        max_model_calls=8,
        max_tool_calls=8,
        max_tokens=32000,
        max_completion_tokens=2048,
        provider_pacing_seconds=2,
        hidden_retry=False,
        scripted_fallback=False,
        frozen_files=bindings,
    )


def verify_freeze_manifest(project_root: Path, manifest_path: Path) -> FrozenEvaluationManifest:
    manifest = load_strict_json(manifest_path, FrozenEvaluationManifest)
    if manifest.base_main_commit != BASE_MAIN_COMMIT:
        raise ValueError("freeze manifest base main commit mismatch")
    expected_paths = required_frozen_paths(project_root)
    if tuple(manifest.frozen_files) != expected_paths:
        raise ValueError("freeze manifest path set differs from the required frozen runtime")
    for relative, expected in manifest.frozen_files.items():
        if sha256_regular_file(project_root / relative) != expected:
            raise ValueError(f"frozen path drift detected: {relative}")
    return manifest
