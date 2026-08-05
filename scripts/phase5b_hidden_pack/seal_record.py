"""Out-of-band safe aggregate record for a sealed Phase 5B hidden pack."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, Strict, StringConstraints

from ecomsre.phase5b.hidden_pack import validate_hidden_pack


Sha256 = Annotated[
    str,
    Strict(),
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]
CommitSha = Annotated[
    str,
    Strict(),
    StringConstraints(pattern=r"^[0-9a-f]{40}$"),
]


class HiddenPackSealRecord(BaseModel):
    """Public, answer-free binding from the frozen protocol to one pack."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        strict=True,
    )

    schema_version: Literal["phase5b.hidden-pack-seal.v1"]
    evaluation_version: Literal["phase5b.v1"]
    protocol_commit: CommitSha
    freeze_manifest_sha256: Sha256
    pack_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    generator_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    builder_source_sha256: Sha256
    validator_source_sha256: Sha256
    private_validation_report_sha256: Sha256
    hidden_pack_manifest_sha256: Sha256
    agent_visible_pack_sha256: Sha256
    ground_truth_pack_sha256: Sha256
    template_count: Literal[6]
    seed_count_per_template: Literal[5]
    instance_count: Literal[30]
    sealed: Literal[True]
    unblinded: Literal[False]
    agent_runs: Literal[0]
    provider_calls: Literal[0]
    execution_entered: Literal[False]


class HiddenPackVerification(BaseModel):
    """Path-free verification result safe for terminal and CI output."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        strict=True,
    )

    status: Literal["PHASE5B_HIDDEN_PACK_VERIFIED"]
    evaluation_version: Literal["phase5b.v1"]
    pack_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    generator_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    hidden_pack_manifest_sha256: Sha256
    agent_visible_pack_sha256: Sha256
    ground_truth_pack_sha256: Sha256
    template_count: Literal[6]
    seed_count_per_template: Literal[5]
    instance_count: Literal[30]
    sealed: Literal[True]
    unblinded: Literal[False]
    agent_runs: Literal[0]
    provider_calls: Literal[0]
    execution_entered: Literal[False]


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _load_strict_object(path: Path) -> dict[str, object]:
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError("seal record must be a regular non-symlink file")
    payload = json.loads(
        path.read_bytes().decode("utf-8", errors="strict"),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("seal record must be a JSON object")
    return payload


def load_hidden_pack_seal_record(path: Path) -> HiddenPackSealRecord:
    return HiddenPackSealRecord.model_validate(_load_strict_object(path))


def verify_public_seal_records(project_root: Path) -> HiddenPackSealRecord:
    """Verify the duplicated public seal and its frozen protocol binding."""

    config_record = load_hidden_pack_seal_record(
        project_root / "config/phase5b-seal/hidden-pack-seal.v1.json"
    )
    disposition_record = load_hidden_pack_seal_record(
        project_root
        / "docs/review-evidence/phase5b-hidden-pack/current-disposition.json"
    )
    if config_record != disposition_record:
        raise ValueError("public seal records differ")

    freeze_path = project_root / "config/phase5b/freeze-manifest.v1.json"
    freeze_sha256 = hashlib.sha256(freeze_path.read_bytes()).hexdigest()
    if freeze_sha256 != config_record.freeze_manifest_sha256:
        raise ValueError("freeze manifest SHA does not match public seal")

    protocol = _load_strict_object(
        project_root
        / "docs/review-evidence/phase5b-protocol/current-disposition.json"
    )
    if protocol.get("status") != "PHASE5B_PROTOCOL_FREEZE_READY":
        raise ValueError("protocol disposition is not frozen and ready")
    if protocol.get("evaluation_version") != config_record.evaluation_version:
        raise ValueError("protocol evaluation version does not match public seal")
    if protocol.get("protocol_commit") != config_record.protocol_commit:
        raise ValueError("protocol commit does not match public seal")
    return config_record


def _require_external_location(
    pack_root: Path,
    worktree_roots: tuple[Path, ...],
) -> None:
    resolved_pack = pack_root.resolve(strict=True)
    for worktree_root in worktree_roots:
        resolved_worktree = worktree_root.resolve(strict=True)
        if resolved_pack == resolved_worktree or resolved_worktree in resolved_pack.parents:
            raise ValueError("hidden pack must remain outside every Git worktree")


def verify_external_hidden_pack(
    pack_root: Path,
    seal_record: HiddenPackSealRecord,
    *,
    worktree_roots: tuple[Path, ...],
) -> HiddenPackVerification:
    """Verify structural bytes and their public aggregate binding."""

    _require_external_location(pack_root, worktree_roots)
    manifest_path = pack_root / "manifest.json"
    manifest = validate_hidden_pack(pack_root, manifest_path)
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    if manifest_sha256 != seal_record.hidden_pack_manifest_sha256:
        raise ValueError("hidden-pack manifest SHA does not match public seal")
    if manifest.pack_id != seal_record.pack_id:
        raise ValueError("hidden-pack ID does not match public seal")
    if manifest.generator_version != seal_record.generator_version:
        raise ValueError("hidden-pack generator version does not match public seal")
    if manifest.agent_visible_pack_sha256 != seal_record.agent_visible_pack_sha256:
        raise ValueError("agent-visible pack SHA does not match public seal")
    if manifest.ground_truth_pack_sha256 != seal_record.ground_truth_pack_sha256:
        raise ValueError("ground-truth pack SHA does not match public seal")
    if (
        manifest.template_count != seal_record.template_count
        or manifest.seed_count_per_template != seal_record.seed_count_per_template
        or manifest.template_count * manifest.seed_count_per_template
        != seal_record.instance_count
    ):
        raise ValueError("hidden-pack counts do not match public seal")

    return HiddenPackVerification(
        status="PHASE5B_HIDDEN_PACK_VERIFIED",
        evaluation_version=manifest.evaluation_version,
        pack_id=manifest.pack_id,
        generator_version=manifest.generator_version,
        hidden_pack_manifest_sha256=manifest_sha256,
        agent_visible_pack_sha256=manifest.agent_visible_pack_sha256,
        ground_truth_pack_sha256=manifest.ground_truth_pack_sha256,
        template_count=manifest.template_count,
        seed_count_per_template=manifest.seed_count_per_template,
        instance_count=seal_record.instance_count,
        sealed=manifest.sealed,
        unblinded=manifest.unblinded,
        agent_runs=seal_record.agent_runs,
        provider_calls=seal_record.provider_calls,
        execution_entered=seal_record.execution_entered,
    )
