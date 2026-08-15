"""Create-once private evidence persistence for DTA v2 Agent runs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.agent import DtaAgentRunResult
from ecomsre.dta_v2.agent_provider import (
    _contains_forbidden_reasoning,
    _contains_forbidden_raw_key,
    _contains_secret_material,
)
from ecomsre.dta_v2.contracts import DtaModel, RunId, Sha256, semantic_sha256


class AgentEvidenceManifest(DtaModel):
    schema_version: Literal["dta-v2.agent-evidence-manifest.v1"]
    run_id: RunId
    result_sha256: Sha256
    identity_sha256: Sha256
    artifacts: dict[str, Sha256] = Field(min_length=1)
    missing_artifacts: tuple[str, ...]
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def require_manifest(self) -> AgentEvidenceManifest:
        for path in (*self.artifacts, *self.missing_artifacts):
            candidate = Path(path)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError("Agent evidence manifest path is unsafe")
        if set(self.artifacts).intersection(self.missing_artifacts):
            raise ValueError("Agent evidence cannot be present and missing")
        if self.missing_artifacts != tuple(sorted(self.missing_artifacts)):
            raise ValueError("missing Agent artifacts are not canonical")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected:
            raise ValueError("Agent evidence manifest digest differs")
        return self


def _canonical_bytes(value: object) -> bytes:
    if isinstance(value, DtaModel):
        value = value.model_dump(mode="json")
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _ensure_private_directory(path: Path) -> None:
    path = Path(path)
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        if cursor.is_symlink():
            raise ValueError("private Agent evidence path contains a symbolic link")
        missing.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    if cursor.is_symlink():
        raise ValueError("private Agent evidence path contains a symbolic link")
    if not cursor.is_dir():
        raise ValueError("private Agent evidence ancestor is not a directory")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        os.chmod(directory, 0o700)
    if path.is_symlink():
        raise ValueError("private Agent evidence path contains a symbolic link")
    if not path.is_dir():
        raise ValueError("private Agent evidence root is not a directory")
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise PermissionError("private Agent evidence directory mode must be 0700")


def _write_private_json(path: Path, value: object) -> str:
    _ensure_private_directory(path.parent)
    if path.is_symlink():
        raise ValueError("private Agent evidence target is a symbolic link")
    payload = _canonical_bytes(value)
    if path.exists():
        if not path.is_file():
            raise ValueError("private Agent evidence target is not a file")
        if path.read_bytes() != payload:
            raise FileExistsError("create-once private Agent evidence differs")
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise PermissionError("private Agent evidence file mode must be 0600")
        return hashlib.sha256(payload).hexdigest()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(path, 0o600)
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise PermissionError("private Agent evidence file mode must be 0600")
    return hashlib.sha256(payload).hexdigest()


def _artifact_values(result: DtaAgentRunResult) -> tuple[dict[str, object], tuple[str, ...]]:
    artifacts: dict[str, object] = {
        "identity.json": result.identity,
        "final/evidence-store.json": result.evidence_store,
        "agent-run-result.json": result,
    }
    for turn in result.provider_turns:
        prefix = f"turns/{turn.turn_ordinal:04d}"
        artifacts[f"{prefix}/raw-response.json"] = turn.raw_response
        artifacts[f"{prefix}/tool-call-arguments.json"] = turn.raw_arguments
        artifacts[f"{prefix}/usage-latency.json"] = {
            "schema_version": "dta-v2.provider-usage-latency.v1",
            "usage": turn.usage.model_dump(mode="json"),
            "monotonic_latency_ms": turn.monotonic_latency_ms,
        }
        if turn.parsed_read_request is not None:
            artifacts[f"{prefix}/parsed-read-request.json"] = (
                turn.parsed_read_request
            )
            assert turn.observation is not None
            artifacts[f"{prefix}/tool-observation.json"] = turn.observation
        elif turn.parsed_diagnosis is not None:
            artifacts[f"{prefix}/parsed-diagnosis.json"] = turn.parsed_diagnosis
        else:
            assert turn.parsed_action_selection is not None
            artifacts[f"{prefix}/parsed-action-selection.json"] = (
                turn.parsed_action_selection
            )

    optional = {
        "final/diagnosis.json": result.diagnosis,
        "final/resolved-evidence.json": result.resolved_evidence,
        "final/candidate-set.json": result.candidate_set,
        "final/candidate-view.json": result.candidate_view,
        "final/action-proposal.json": result.action_proposal,
    }
    missing: list[str] = []
    for name, value in optional.items():
        if value is None:
            missing.append(name)
        else:
            artifacts[name] = value
    return artifacts, tuple(sorted(missing))


def persist_agent_run(
    root: Path,
    result: DtaAgentRunResult,
    *,
    forbidden_secrets: tuple[str, ...] = (),
) -> AgentEvidenceManifest:
    """Persist every accepted turn and final artifact under one private root."""

    result = DtaAgentRunResult.model_validate_json(result.model_dump_json())
    for turn in result.provider_turns:
        for value in (turn.raw_response, turn.raw_arguments):
            if _contains_forbidden_reasoning(value):
                raise ValueError("raw Provider output contains private reasoning")
            if _contains_forbidden_raw_key(value):
                raise ValueError("raw Provider output contains private configuration")
    dumped_result = result.model_dump(mode="json")
    for secret in forbidden_secrets:
        if not isinstance(secret, str) or not secret:
            raise ValueError("forbidden secret markers must be nonempty strings")
        if _contains_secret_material(dumped_result, secret):
            raise ValueError("private Agent evidence contains a forbidden secret")

    private_root = Path(root)
    if private_root.is_symlink():
        raise ValueError("private Agent evidence root is a symbolic link")
    _ensure_private_directory(private_root)
    values, missing = _artifact_values(result)
    hashes: dict[str, str] = {}
    for relative_path in sorted(values):
        hashes[relative_path] = _write_private_json(
            private_root / relative_path, values[relative_path]
        )
    payload: dict[str, object] = {
        "schema_version": "dta-v2.agent-evidence-manifest.v1",
        "run_id": result.run_id,
        "result_sha256": result.result_sha256,
        "identity_sha256": result.identity.identity_sha256,
        "artifacts": hashes,
        "missing_artifacts": missing,
    }
    manifest = AgentEvidenceManifest.model_validate(
        {**payload, "manifest_sha256": semantic_sha256(payload)}
    )
    _write_private_json(private_root / "manifest.json", manifest)
    return manifest


def load_agent_run(root: Path) -> DtaAgentRunResult:
    """Load and revalidate one exact private result and its artifact manifest."""

    private_root = Path(root)
    if private_root.is_symlink() or not private_root.is_dir():
        raise ValueError("private Agent evidence root is not a regular directory")
    for path in (private_root, *sorted(private_root.rglob("*"))):
        if path.is_symlink():
            raise ValueError("private Agent evidence tree contains a symbolic link")
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir() and mode != 0o700:
            raise PermissionError("private Agent evidence directory mode must be 0700")
        if path.is_file() and mode != 0o600:
            raise PermissionError("private Agent evidence file mode must be 0600")
    manifest_path = private_root / "manifest.json"
    manifest = AgentEvidenceManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    for relative_path, expected in manifest.artifacts.items():
        path = private_root / relative_path
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("private Agent evidence artifact digest differs")
    result = DtaAgentRunResult.model_validate_json(
        (private_root / "agent-run-result.json").read_text(encoding="utf-8")
    )
    if result.result_sha256 != manifest.result_sha256:
        raise ValueError("private Agent result differs from manifest")
    return result


__all__ = [
    "AgentEvidenceManifest",
    "load_agent_run",
    "persist_agent_run",
]
