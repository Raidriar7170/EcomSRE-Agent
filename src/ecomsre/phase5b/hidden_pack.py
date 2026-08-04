"""Validate an external sealed hidden pack without exposing truth to workers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat

from ecomsre.backends.replay import ReplayCase, load_replay_case

from ecomsre.phase5b.contracts import (
    HiddenPackManifest,
    HiddenSeedManifest,
    HiddenTemplateManifest,
)
from ecomsre.phase5b.protocol import _freeze_json, _reject_constant, _strict_object


_TEMPLATES = tuple(f"hidden-{index:02d}" for index in range(1, 7))
_SEEDS = tuple(f"seed-{index:02d}" for index in range(5))
_VISIBLE_FILENAMES = (
    "changes.json",
    "incident.json",
    "logs.json",
    "manifest.json",
    "metrics.json",
    "traces.json",
)


def canonical_json_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def write_canonical_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("canonical JSON target must be a regular non-symlink file")
    path.write_bytes(canonical_json_bytes(payload))


def _load_canonical_object(path: Path) -> dict[str, object]:
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode):
        raise ValueError("hidden pack contains a symlink")
    if not stat.S_ISREG(details.st_mode):
        raise ValueError("hidden pack JSON entry must be a regular file")
    observed = path.read_bytes()
    payload = json.loads(
        observed,
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("hidden pack JSON entry must be an object")
    if observed != canonical_json_bytes(payload):
        raise ValueError("hidden pack JSON is not canonical")
    return payload


def validate_pack_roots(agent_visible_root: Path, ground_truth_root: Path) -> None:
    visible = agent_visible_root.resolve(strict=True)
    truth = ground_truth_root.resolve(strict=True)
    if visible == truth or visible in truth.parents or truth in visible.parents:
        raise ValueError("agent-visible and ground-truth roots must be distinct without overlap")
    for root in (agent_visible_root, ground_truth_root):
        if root.is_symlink() or not root.is_dir():
            raise ValueError("hidden pack roots must be regular non-symlink directories")


def _expected_paths() -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    visible = tuple(
        Path(template_id) / seed_id / filename
        for template_id in _TEMPLATES
        for seed_id in _SEEDS
        for filename in _VISIBLE_FILENAMES
    )
    truth = tuple(
        Path(template_id) / f"{seed_id}.json"
        for template_id in _TEMPLATES
        for seed_id in _SEEDS
    )
    return visible, truth


def _safe_files(root: Path, expected: tuple[Path, ...]) -> tuple[Path, ...]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("hidden pack root must be a regular non-symlink directory")
    observed: list[Path] = []
    for item in root.rglob("*"):
        details = item.lstat()
        if stat.S_ISLNK(details.st_mode):
            raise ValueError("hidden pack contains a symlink")
        if stat.S_ISREG(details.st_mode):
            observed.append(item.relative_to(root))
        elif not stat.S_ISDIR(details.st_mode):
            raise ValueError("hidden pack contains an unknown filesystem entry")
    if tuple(sorted(observed)) != tuple(sorted(expected)):
        raise ValueError("hidden pack contains an unknown or incomplete layout")
    return tuple(root / relative for relative in sorted(observed))


def _pack_hash(root: Path, paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        payload = _load_canonical_object(path)
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(canonical_json_bytes(payload))
        digest.update(b"\0")
    return digest.hexdigest()


def build_hidden_pack_manifest(
    pack_root: Path,
    *,
    pack_id: str,
    generator_version: str,
) -> HiddenPackManifest:
    visible_root = pack_root / "agent-visible"
    truth_root = pack_root / "ground-truth"
    validate_pack_roots(visible_root, truth_root)
    expected_visible, expected_truth = _expected_paths()
    visible_paths = _safe_files(visible_root, expected_visible)
    truth_paths = _safe_files(truth_root, expected_truth)
    templates: list[HiddenTemplateManifest] = []
    for template_id in _TEMPLATES:
        seeds: list[HiddenSeedManifest] = []
        template_digest = hashlib.sha256()
        for seed_id in _SEEDS:
            visible_seed_root = visible_root / template_id / seed_id
            truth_path = truth_root / template_id / f"{seed_id}.json"
            visible_sha = _pack_hash(
                visible_seed_root,
                tuple(visible_seed_root / filename for filename in _VISIBLE_FILENAMES),
            )
            truth_sha = hashlib.sha256(truth_path.read_bytes()).hexdigest()
            template_digest.update(seed_id.encode("utf-8"))
            template_digest.update(b"\0")
            template_digest.update(bytes.fromhex(visible_sha))
            template_digest.update(bytes.fromhex(truth_sha))
            seeds.append(
                HiddenSeedManifest(
                    seed_id=seed_id,
                    agent_visible_content_sha256=visible_sha,
                    ground_truth_content_sha256=truth_sha,
                )
            )
        templates.append(
            HiddenTemplateManifest(
                template_id=template_id,
                content_sha256=template_digest.hexdigest(),
                seeds=tuple(seeds),
            )
        )
    return HiddenPackManifest(
        schema_version="phase5b.hidden-pack-manifest.v1",
        evaluation_version="phase5b.v1",
        pack_id=pack_id,
        template_count=6,
        seed_count_per_template=5,
        agent_visible_pack_sha256=_pack_hash(visible_root, visible_paths),
        ground_truth_pack_sha256=_pack_hash(truth_root, truth_paths),
        generator_version=generator_version,
        sealed=True,
        unblinded=False,
        templates=tuple(templates),
    )


def validate_hidden_pack(
    pack_root: Path,
    manifest_path: Path,
) -> HiddenPackManifest:
    resolved_root = pack_root.resolve(strict=True)
    if pack_root.is_symlink() or not pack_root.is_dir():
        raise ValueError("hidden pack must be a regular non-symlink directory")
    if manifest_path.resolve(strict=True) != resolved_root / "manifest.json":
        raise ValueError("hidden pack manifest must remain at the pack root")
    payload = _load_canonical_object(manifest_path)
    manifest = HiddenPackManifest.model_validate(_freeze_json(payload))
    expected = build_hidden_pack_manifest(
        pack_root,
        pack_id=manifest.pack_id,
        generator_version=manifest.generator_version,
    )
    if manifest != expected:
        raise ValueError("hidden pack canonical hash verification failed")
    allowed_root_entries = {"agent-visible", "ground-truth", "manifest.json"}
    if {item.name for item in pack_root.iterdir()} != allowed_root_entries:
        raise ValueError("hidden pack contains an unknown root entry")
    return manifest


def load_agent_visible_instance(
    agent_visible_root: Path,
    template_id: str,
    seed_id: str,
) -> ReplayCase:
    if agent_visible_root.name != "agent-visible":
        raise ValueError("worker requires the agent-visible root")
    if not re.fullmatch(r"hidden-0[1-6]", template_id):
        raise ValueError("hidden template identifier is invalid")
    if not re.fullmatch(r"seed-0[0-4]", seed_id):
        raise ValueError("hidden seed identifier is invalid")
    root = agent_visible_root.resolve(strict=True)
    if agent_visible_root.is_symlink() or not agent_visible_root.is_dir():
        raise ValueError("agent-visible root must be a regular directory")
    template_root = agent_visible_root / template_id
    seed_root = template_root / seed_id
    if seed_root.resolve(strict=True).parent.parent != root:
        raise ValueError("agent-visible instance escapes its root")
    expected = tuple(Path(filename) for filename in _VISIBLE_FILENAMES)
    _safe_files(seed_root, expected)
    for filename in _VISIBLE_FILENAMES:
        _load_canonical_object(seed_root / filename)
    return load_replay_case(template_root, seed_id)
