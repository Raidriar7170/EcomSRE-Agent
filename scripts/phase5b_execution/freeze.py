"""Generate and verify the truth-free Phase 5B execution freeze manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
from typing import Any, cast

from scripts.phase5b_execution.contracts import (
    CheckpointPolicy,
    ExecutionLifecyclePolicy,
    ExecutionFreezeManifest,
    WorkerSandboxPolicy,
    canonical_json_bytes,
)


EXECUTION_FREEZE_RELATIVE = Path(
    "config/phase5b-execution/execution-freeze.v1.json"
)
_SEAL_RELATIVE = Path("config/phase5b-seal/hidden-pack-seal.v1.json")
_PROTOCOL_FREEZE_RELATIVE = Path("config/phase5b/freeze-manifest.v1.json")
_SCHEDULE_RELATIVE = Path("config/phase5b/execution-schedule.v1.json")
_ABLATION_RELATIVE = Path("config/phase5b/ablation-registry.v1.json")
_HARNESS_ROOTS = (
    Path("eval/phase5b_execution"),
    Path("scripts/phase5b_execution"),
    Path("tests/phase5b_execution"),
)


def sha256_regular_file(path: Path) -> str:
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError("execution freeze path must be a regular non-symlink file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate execution freeze JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite execution freeze JSON constant: {value}")


def _load_object(path: Path) -> dict[str, object]:
    payload = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("execution freeze input must be an object")
    return cast(dict[str, object], payload)


def harness_paths(project_root: Path) -> tuple[str, ...]:
    paths = {"Makefile"}
    for relative_root in _HARNESS_ROOTS:
        root = project_root / relative_root
        if not root.is_dir() or root.is_symlink():
            raise ValueError(f"execution harness root is absent: {relative_root}")
        paths.update(
            path.relative_to(project_root).as_posix()
            for path in root.rglob("*.py")
            if path.is_file()
            and not path.is_symlink()
            and "__pycache__" not in path.parts
        )
    return tuple(sorted(paths))


def build_execution_freeze_manifest(
    project_root: Path,
    *,
    execution_base_commit: str,
) -> ExecutionFreezeManifest:
    root = Path(project_root).resolve(strict=True)
    seal_path = root / _SEAL_RELATIVE
    seal = _load_object(seal_path)
    if not (
        seal.get("sealed") is True
        and seal.get("unblinded") is False
        and seal.get("execution_entered") is False
        and seal.get("provider_calls") == 0
        and seal.get("agent_runs") == 0
    ):
        raise ValueError("public hidden-pack seal is not execution-ready")
    bindings = {
        relative: sha256_regular_file(root / relative)
        for relative in harness_paths(root)
    }
    return ExecutionFreezeManifest(
        schema_version="phase5b.execution-freeze.v1",
        evaluation_version="phase5b.v1",
        protocol_commit=cast(str, seal["protocol_commit"]),
        execution_base_commit=execution_base_commit,
        protocol_freeze_manifest_sha256=sha256_regular_file(
            root / _PROTOCOL_FREEZE_RELATIVE
        ),
        hidden_pack_seal_record_sha256=sha256_regular_file(seal_path),
        hidden_pack_manifest_sha256=cast(
            str, seal["hidden_pack_manifest_sha256"]
        ),
        agent_visible_pack_sha256=cast(
            str, seal["agent_visible_pack_sha256"]
        ),
        ground_truth_pack_sha256=cast(
            str, seal["ground_truth_pack_sha256"]
        ),
        execution_schedule_sha256=sha256_regular_file(root / _SCHEDULE_RELATIVE),
        ablation_registry_sha256=sha256_regular_file(root / _ABLATION_RELATIVE),
        harness_files=bindings,
        provider="openai-compatible",
        model="gpt-5.4-mini-2026-03-17",
        temperature=0,
        max_model_calls=8,
        max_tool_calls=8,
        max_tokens=32000,
        max_completion_tokens=2048,
        provider_pacing_seconds=2,
        hidden_retry=False,
        scripted_fallback=False,
        main_run_count=180,
        ablation_run_count=38,
        worker_sandbox_policy=WorkerSandboxPolicy(
            schema_version="phase5b.worker-sandbox-policy.v1",
            request_fields=("run_id", "template_id", "seed_id", "variant"),
            environment_allowlist=(
                "ECOMSRE_LLM_API_KEY",
                "ECOMSRE_LLM_BASE_URL",
                "ECOMSRE_LLM_MODEL",
                "LANG",
                "LC_ALL",
                "PATH",
                "PHASE5B_AGENT_VISIBLE_ROOT",
                "SSL_CERT_DIR",
                "SSL_CERT_FILE",
                "TMPDIR",
            ),
            truth_environment_removed=True,
            repository_truth_roots_denied=True,
            external_ground_truth_component_denied=True,
            builder_source_and_logs_denied=True,
            evaluator_import_denied=True,
            nested_process_denied=True,
            provider_network_allowed=True,
        ),
        checkpoint_policy=CheckpointPolicy(
            schema_version="phase5b.checkpoint-policy.v1",
            attempt_marker_before_provider=True,
            create_once_terminal_record=True,
            interrupted_attempt_terminalized=True,
            retry=False,
            rerun_failed=False,
            overwrite=False,
        ),
        lifecycle_policy=ExecutionLifecyclePolicy(
            schema_version="phase5b.execution-lifecycle-policy.v1",
            merged_origin_main_required=True,
            required_results_branch="phase5b/v1-frozen-results",
            provider_canary_create_once=True,
            provider_canary_public_unscored=True,
            execution_started_create_once=True,
            execution_complete_create_once=True,
            unblinding_create_once=True,
            final_report_create_once=True,
            truth_environment_before_unblinding=False,
            source_read_only_after_execution_started=True,
        ),
        unblinding_contract=(
            "phase5b.unblinding-record.v1-execution-layer-superset"
        ),
        ablation_execution_policy=(
            "all_preregistered_v1_ablations_not_implemented_terminal_failure"
        ),
    )


def load_execution_freeze_manifest(path: Path) -> ExecutionFreezeManifest:
    observed = path.read_bytes()
    manifest = ExecutionFreezeManifest.model_validate_json(observed, strict=True)
    if observed != canonical_json_bytes(manifest.model_dump(mode="json")):
        raise ValueError("execution freeze manifest is not canonical")
    return manifest


def verify_execution_freeze_manifest(
    project_root: Path,
    manifest_path: Path | None = None,
) -> ExecutionFreezeManifest:
    root = Path(project_root).resolve(strict=True)
    path = manifest_path or root / EXECUTION_FREEZE_RELATIVE
    manifest = load_execution_freeze_manifest(path)
    expected = build_execution_freeze_manifest(
        root,
        execution_base_commit=manifest.execution_base_commit,
    )
    if manifest != expected:
        raise ValueError("execution freeze manifest differs from live bindings")
    return manifest
