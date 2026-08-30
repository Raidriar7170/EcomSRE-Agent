from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path, PurePath
import secrets
import sqlite3
import stat
import subprocess
import time
from typing import Any, Sequence, cast

import httpx

from ecomsre.product.pilot.baseline_readiness_v023 import (
    ProductBaselineReadinessAuditV023,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.base import ConnectorWindowV1
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    ACTIVE_PROFILE_SHA256_V023,
)
from ecomsre.product.connectors.pilot_runtime import PilotRuntimeSnapshotV02
from ecomsre.product.incidents.contracts import (
    DiagnosisResultV1,
    EvidenceBundleV1,
    IncidentRecordV1,
)
from ecomsre.product.jobs.contracts import ProductJobStatusV1
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
    HealthyTrafficProfileV021,
    verify_queue_default_v021,
)
from ecomsre.product.pilot.baseline_restart_v023 import BaselineRestartProofV023
from ecomsre.product.pilot.live_baseline_readiness_v023 import (
    _ProductHostProcessesV023,
    _queue_counts,
    _request_json,
    _require_clean_head,
    _restart_snapshot,
    _wait_job,
)
from ecomsre.product.pilot.live_nofault_acceptance_v023 import (
    _attempt_context,
    _database_counts,
    _limitation_evidence_refs,
    _load_persisted_bindings,
    _successful_runtime_ref,
    _runtime_snapshot,
)
from ecomsre.product.pilot.nofault_acceptance_v0231 import (
    NOFAULT_ACCEPTANCE_COMPLETE_V0231,
    NOFAULT_FULLY_SUPPORTED_V0231,
    NoFaultAcceptanceResultV0231,
    NoFaultCampaignV0231,
    NoFaultProfileBindingV0231,
)
from ecomsre.product.pilot.runtime_continuity_v0231 import (
    AuthorityContinuousSandboxLifecycleV0231,
    ProductBaselineContinuationContextV0231,
    ProductV023PrivateStateBindingV0231,
    RuntimeAuthorityContinuityDescriptorV0231,
    admit_product_baseline_continuation_context_v0231,
    load_preserved_runtime_inputs_v0231,
)
from ecomsre.product.pilot.runtime_session_v0231 import (
    BaselineRestartProofV0231,
    RuntimeAuthorityContinuityProofV0231,
    RuntimeContinuationSessionCompletionV0231,
    RuntimeContinuationSessionLedgerV0231,
    RuntimeContinuationSessionStartV0231,
)
from ecomsre.product.pilot.nofault_acceptance_v023 import (
    NoFaultAcceptanceResultV023,
    NoFaultCapabilityAssessmentV023,
    NoFaultExecutionProfileV023,
    NoFaultQueueSnapshotV023,
    NoFaultTrafficResultV023,
    _successful_evidence_sources,
    score_nofault_v023,
)
from ecomsre_live_sandbox.contracts import (
    canonical_json_bytes,
    load_bundle,
    write_private_json,
)
from scripts.ci.verify_product_v0231_history import verify_product_v0231_history

import ecomsre


RUNTIME_PASS = "ECOMSRE_PRODUCT_V0231_RUNTIME_AUTHORITY_CONTINUITY_PASS"
RESTART_PASS = "ECOMSRE_PRODUCT_V0231_BASELINE_RESTART_PASS"
KNOWLEDGE_READY = "ECOMSRE_PRODUCT_V0231_KNOWLEDGE_LOOP_HANDOFF_READY"
KNOWLEDGE_NOT_AUTHORIZED = "ECOMSRE_PRODUCT_V0231_KNOWLEDGE_LOOP_HANDOFF_NOT_AUTHORIZED"
_EPISODE_MINIMUM_SECONDS = 300
_PUBLICATION_OUTPUTS = (
    "docs/analysis/product-v0231-baseline-restart.json",
    "docs/analysis/product-v0231-continuation-session-1.json",
    "docs/analysis/product-v0231-knowledge-loop-handoff.json",
    "docs/analysis/product-v0231-knowledge-loop-handoff.md",
    "docs/analysis/product-v0231-progress.json",
    "docs/results/product-v0231-interview-brief.md",
    "docs/results/product-v0231-limitations.md",
    "docs/results/product-v0231-nofault-acceptance.json",
    "docs/results/product-v0231-nofault-acceptance.md",
)


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Product v0.2.3.1 JSON object differs: {path.name}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_only_database_counts(data_root: Path, environment_id: str) -> dict[str, int]:
    database = (data_root / "product.sqlite3").resolve(strict=True)
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        counts = {
            "incident_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM incidents WHERE environment_id = ?",
                    (environment_id,),
                ).fetchone()[0]
            ),
            "diagnosis_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM diagnosis_results d "
                    "JOIN incidents i ON i.incident_id = d.incident_id "
                    "WHERE i.environment_id = ?",
                    (environment_id,),
                ).fetchone()[0]
            ),
            "fault_family_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM fault_families WHERE environment_id = ?",
                    (environment_id,),
                ).fetchone()[0]
            ),
            "baseline_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM baseline_versions WHERE environment_id = ?",
                    (environment_id,),
                ).fetchone()[0]
            ),
            "baseline_job_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM diagnosis_jobs "
                    "WHERE job_type = 'BASELINE_BUILD'"
                ).fetchone()[0]
            ),
            "verify_job_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM diagnosis_jobs "
                    "WHERE job_type = 'ENVIRONMENT_VERIFY'"
                ).fetchone()[0]
            ),
            "pending_job_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM diagnosis_jobs WHERE status = 'PENDING'"
                ).fetchone()[0]
            ),
            "running_job_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM diagnosis_jobs WHERE status = 'RUNNING'"
                ).fetchone()[0]
            ),
            "failed_job_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM diagnosis_jobs WHERE status = 'FAILED'"
                ).fetchone()[0]
            ),
        }
        counts["knowledge_artifact_count"] = sum(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "fault_families",
                "registration_drafts",
                "shadow_evaluations",
                "promotion_records",
            )
        )
        return counts
    finally:
        connection.close()


def _atomic_create_once(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.product-v0231-atomic.tmp"

    def owned_regular_temporary_exists() -> bool:
        try:
            metadata = temporary.lstat()
        except FileNotFoundError:
            return False
        if (
            temporary.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
        ):
            raise FileExistsError(
                f"Product v0.2.3.1 atomic temporary differs: {path.name}"
            )
        return True

    if path.exists():
        if path.is_symlink() or path.read_bytes() != payload:
            raise FileExistsError(f"Product v0.2.3.1 output differs: {path.name}")
        if owned_regular_temporary_exists():
            temporary.unlink()
        return
    descriptor = -1
    try:
        if owned_regular_temporary_exists():
            if temporary.read_bytes() != payload:
                temporary.unlink()
        if not temporary.exists():
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                mode,
            )
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != payload:
                raise
        parent_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


def _write_once(path: Path, payload: bytes) -> None:
    _atomic_create_once(path, payload, mode=0o644)


def _markdown_bytes(lines: Sequence[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def _freeze_publication_bundle(
    *,
    private_root: Path,
    execution_head: str,
    private_files: dict[str, bytes],
    public_files: dict[str, bytes],
) -> dict[str, Any]:
    if tuple(sorted(public_files)) != _PUBLICATION_OUTPUTS:
        raise ValueError("Product v0.2.3.1 publication output set differs")
    if set(private_files) != {"acceptance.json", "session-completion.json"}:
        raise ValueError("Product v0.2.3.1 private publication set differs")

    def entries(files: dict[str, bytes]) -> list[dict[str, Any]]:
        return [
            {
                "path": path,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
                "content_utf8": payload.decode("utf-8"),
            }
            for path, payload in sorted(files.items())
        ]

    body: dict[str, Any] = {
        "schema_version": "ecomsre.product.publication-bundle.v0231",
        "execution_head": execution_head,
        "private_files": entries(private_files),
        "public_files": entries(public_files),
        "terminal": NOFAULT_ACCEPTANCE_COMPLETE_V0231,
    }
    bundle = {**body, "bundle_sha256": semantic_sha256_v22(body)}
    _atomic_create_once(
        private_root / "publication-bundle.json",
        canonical_json_bytes(bundle),
        mode=0o600,
    )
    return bundle


def _publication_entry_bytes(entry: dict[str, Any]) -> bytes:
    payload = str(entry["content_utf8"]).encode("utf-8")
    if len(payload) != entry.get("size_bytes") or hashlib.sha256(
        payload
    ).hexdigest() != entry.get("sha256"):
        raise ValueError("Product v0.2.3.1 publication entry differs")
    return payload


def recover_publication_v0231(*, project_root: Path) -> dict[str, Any]:
    root = Path(project_root).resolve(strict=True)
    private_root = root / ".local/product-v0231/continuation-sessions/session-1"
    bundle_path = private_root / "publication-bundle.json"
    bundle = _object(bundle_path)
    digest = bundle.pop("bundle_sha256", None)
    if (
        digest != semantic_sha256_v22(bundle)
        or bundle.get("schema_version") != "ecomsre.product.publication-bundle.v0231"
        or bundle.get("terminal") != NOFAULT_ACCEPTANCE_COMPLETE_V0231
    ):
        raise ValueError("Product v0.2.3.1 publication bundle differs")
    execution_head = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if execution_head != bundle.get("execution_head"):
        raise ValueError("Product v0.2.3.1 publication execution HEAD differs")
    private_entries = bundle.get("private_files")
    public_entries = bundle.get("public_files")
    if not isinstance(private_entries, list) or not isinstance(public_entries, list):
        raise ValueError("Product v0.2.3.1 publication entries are absent")
    if {entry.get("path") for entry in private_entries} != {
        "acceptance.json",
        "session-completion.json",
    }:
        raise ValueError("Product v0.2.3.1 private publication paths differ")
    if tuple(sorted(str(entry.get("path")) for entry in public_entries)) != (
        _PUBLICATION_OUTPUTS
    ):
        raise ValueError("Product v0.2.3.1 public publication paths differ")
    for raw in private_entries:
        entry = cast(dict[str, Any], raw)
        path = PurePath(str(entry["path"]))
        if path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
            raise ValueError("Product v0.2.3.1 private publication locator differs")
        _atomic_create_once(
            private_root / path,
            _publication_entry_bytes(entry),
            mode=0o600,
        )
    for raw in public_entries:
        entry = cast(dict[str, Any], raw)
        locator = str(entry["path"])
        if locator not in _PUBLICATION_OUTPUTS:
            raise ValueError("Product v0.2.3.1 public publication locator differs")
        _atomic_create_once(
            root / locator,
            _publication_entry_bytes(entry),
            mode=0o644,
        )
    completion = {
        "schema_version": "ecomsre.product.publication-completion.v0231",
        "bundle_sha256": digest,
        "execution_head": execution_head,
        "published_output_count": len(public_entries),
        "terminal": "ECOMSRE_PRODUCT_V0231_PUBLICATION_RECOVERY_PASS",
    }
    completion["completion_sha256"] = semantic_sha256_v22(completion)
    _atomic_create_once(
        private_root / "publication-completion.json",
        canonical_json_bytes(completion),
        mode=0o600,
    )
    return completion


def _rotate_runtime_snapshot_v0231(
    *,
    data_root: Path,
    path: Path,
    snapshot: PilotRuntimeSnapshotV02,
    private_root: Path,
    ordinal: int,
) -> dict[str, object]:
    allowed = Path(data_root).resolve(strict=True)
    target = Path(path)
    if (
        target.is_symlink()
        or target.parent.resolve(strict=True) != target.parent.absolute()
        or not target.resolve(strict=True).is_relative_to(allowed)
    ):
        raise ValueError("Product v0.2.3.1 Runtime snapshot path differs")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Product v0.2.3.1 Runtime snapshot is not regular")
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            before = PilotRuntimeSnapshotV02.model_validate_json(handle.read())
    finally:
        os.close(descriptor)
    if (
        before.environment_id != snapshot.environment_id
        or before.authority_sha256 != snapshot.authority_sha256
    ):
        raise ValueError("Product v0.2.3.1 Runtime snapshot rotation differs")
    encoded = (snapshot.model_dump_json() + "\n").encode("utf-8")
    temporary = target.parent / (
        f".{target.name}.product-v0231-{secrets.token_hex(8)}.tmp"
    )
    output = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(output, "wb") as handle:
            output = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if output >= 0:
            os.close(output)
        if temporary.exists():
            temporary.unlink()
    readback = os.open(target, flags)
    try:
        with os.fdopen(os.dup(readback), "rb") as handle:
            if handle.read() != encoded:
                raise RuntimeError("Product v0.2.3.1 Runtime snapshot readback differs")
    finally:
        os.close(readback)
    evidence: dict[str, object] = {
        "schema_version": "ecomsre.product.runtime-snapshot-rotation.v0231",
        "environment_id": snapshot.environment_id,
        "authority_sha256": snapshot.authority_sha256,
        "before_snapshot_sha256": before.snapshot_sha256,
        "after_snapshot_sha256": snapshot.snapshot_sha256,
        "rotated_at": datetime.now(UTC).isoformat(),
    }
    write_private_json(
        private_root / f"runtime-snapshot-rotation-{ordinal}.json",
        evidence,
        create_once=True,
    )
    return evidence


def _authority_proof(
    *,
    descriptor: RuntimeAuthorityContinuityDescriptorV0231,
    authority: Any,
    backend: Any,
    rotation: dict[str, object],
) -> RuntimeAuthorityContinuityProofV0231:
    observed = {
        "daemon_identity_sha256": backend.authority.daemon_identity_sha256,
        "docker_context_sha256": backend.authority.docker_context_sha256,
        "config_bundle_sha256": backend.authority.config_bundle_sha256,
        "resolved_sandbox_sha256": backend.authority.resolved_sandbox_sha256,
        "resolved_endpoints_sha256": backend.authority.resolved_endpoints_sha256,
        "ownership_scope_sha256": backend.authority.ownership_scope_sha256,
        "read_authority_sha256": backend.authority.authority_sha256,
        "pilot_runtime_authority_sha256": authority.pilot_authority_sha256,
        "connector_binding_sha256": authority.connector_binding_sha256,
    }
    components = {
        name: {
            "expected": str(getattr(descriptor, name)),
            "observed": str(value),
            "equal": getattr(descriptor, name) == value,
        }
        for name, value in observed.items()
    }
    return RuntimeAuthorityContinuityProofV0231.build(
        continuity_descriptor_sha256=descriptor.descriptor_sha256,
        components=components,
        runtime_snapshot_before_sha256=rotation["before_snapshot_sha256"],
        runtime_snapshot_after_sha256=rotation["after_snapshot_sha256"],
        runtime_snapshot_authority_sha256=rotation["authority_sha256"],
        terminal=RUNTIME_PASS,
    )


def _require_successor_import_origin(root: Path) -> None:
    package_file = Path(cast(str, ecomsre.__file__)).resolve(strict=True)
    expected = (root / "src/ecomsre").resolve(strict=True)
    python_path = os.environ.get("PYTHONPATH", "")
    first_entry = python_path.split(os.pathsep, 1)[0]
    first_root = Path(first_entry)
    if not first_root.is_absolute():
        first_root = root / first_root
    if not package_file.is_relative_to(expected) or first_root.resolve(strict=True) != (
        root / "src"
    ).resolve(strict=True):
        raise ValueError("Product v0.2.3.1 successor import origin differs")


def _failure_class(stage: str, error: BaseException) -> str:
    message = f"{type(error).__name__}: {error}".upper()
    if "FLAGD" in message or "QUEUE BASELINE" in message:
        return "FLAGD_BIND_MISMATCH"
    if "COMPOSE" in message:
        return "RESOLVED_COMPOSE_MISMATCH"
    if "AUTHORITY" in message:
        return "RUNTIME_AUTHORITY_MISMATCH"
    if "BASELINE" in message or "BINDING" in message or "COUNTS" in message:
        return "BASELINE_BINDING_MISMATCH"
    if stage == "COMPOSE_VERIFIED":
        return "SANDBOX_START_TRANSIENT"
    if stage == "SANDBOX_STARTED":
        return "SANDBOX_READINESS_TRANSIENT"
    if stage == "AUTHORITY_VERIFIED":
        return "PRODUCT_START_TRANSIENT"
    if stage in {"PRODUCT_STARTED", "PRODUCT_RESTARTED"}:
        return "PRODUCT_RESTART_TRANSIENT"
    return "UNCLEAN_CLOSURE"


def run_live_authority_restart_v0231(
    *,
    project_root: Path,
    predecessor_root: Path,
) -> dict[str, Any]:
    root = Path(project_root).resolve(strict=True)
    predecessor = Path(predecessor_root).resolve(strict=True)
    _require_successor_import_origin(root)
    execution_head = _require_clean_head(root)
    verify_product_v0231_history(
        root,
        predecessor_root=predecessor,
        write_reports=False,
    )
    session_path = root / "docs/analysis/product-v0231-continuation-session-1.json"
    restart_path = root / "docs/analysis/product-v0231-baseline-restart.json"
    result_path = root / "docs/results/product-v0231-nofault-acceptance.json"
    handoff_path = root / "docs/analysis/product-v0231-knowledge-loop-handoff.json"
    progress_path = root / "docs/analysis/product-v0231-progress.json"
    if any(
        path.exists()
        for path in (
            session_path,
            restart_path,
            result_path,
            handoff_path,
            progress_path,
        )
    ):
        raise FileExistsError("Product v0.2.3.1 Session 1 output already exists")

    manifest = _object(root / "config/product-v0231/historical-results.v1.json")
    binding = ProductV023PrivateStateBindingV0231.model_validate(
        manifest["private_state"]
    )
    context = ProductBaselineContinuationContextV0231.model_validate(
        _object(root / "docs/analysis/product-v0231-baseline-continuation-context.json")
    )
    tracked_runtime = RuntimeAuthorityContinuityDescriptorV0231.model_validate(
        _object(root / "docs/analysis/product-v0231-runtime-authority-descriptor.json")
    )
    profile_binding = NoFaultProfileBindingV0231.model_validate(
        _object(root / "config/product-v0231/continuity/nofault-profile-binding.json")
    )
    campaign = NoFaultCampaignV0231.model_validate(
        _object(root / "config/product-v0231/continuity/campaign.json")
    )
    bundle = load_bundle(
        predecessor / "config/live-telemetry-controlled-remediation-v1"
    )
    authority, resolved_compose = load_preserved_runtime_inputs_v0231(
        predecessor_root=predecessor,
        binding=binding,
    )
    _attempt, audit, product_data_root = _attempt_context(predecessor)
    if (
        audit.environment_id != context.environment_id
        or product_data_root != predecessor / binding.product_data_root_locator
    ):
        raise ValueError("Product v0.2.3.1 preserved Product root differs")
    expected_identity_sha256 = context.service_identity_sha256
    expected_capability_sha256 = context.capability_sha256
    candidate_sha256 = audit.service_identity_sha256
    before_counts = _read_only_database_counts(product_data_root, audit.environment_id)
    if any(
        before_counts[name]
        for name in (
            "incident_count",
            "diagnosis_count",
            "fault_family_count",
            "knowledge_artifact_count",
        )
    ):
        raise ValueError("Product v0.2.3.1 pre-Incident counts differ")
    if (
        before_counts["baseline_count"] != 1
        or before_counts["baseline_job_count"] != 1
        or before_counts["verify_job_count"] != 1
        or any(
            before_counts[name]
            for name in (
                "pending_job_count",
                "running_job_count",
                "failed_job_count",
            )
        )
    ):
        raise ValueError("Product v0.2.3.1 prestart Product queue differs")

    private_locator = ".local/product-v0231/continuation-sessions/session-1"
    private_root = root / private_locator
    if private_root.exists():
        raise FileExistsError("Product v0.2.3.1 private Session 1 already exists")
    pre_session_root = root / ".local/product-v0231/live-preflight" / execution_head
    if pre_session_root.exists():
        raise FileExistsError("Product v0.2.3.1 live preflight already exists")
    lifecycle = AuthorityContinuousSandboxLifecycleV0231(
        predecessor_root=predecessor,
        private_root=pre_session_root,
        binding=binding,
        context=context,
        bundle=bundle,
        preserved_authority=authority,
        preserved_resolved_compose=resolved_compose,
    )
    lifecycle.admit_prestart()
    if lifecycle.runtime_descriptor != tracked_runtime:
        raise ValueError("Product v0.2.3.1 tracked Runtime descriptor differs")
    nofault_profile = NoFaultExecutionProfileV023.load(
        root / "config/product-v023/nofault/profile.json"
    )
    if (
        _sha256(root / profile_binding.source_profile_locator)
        != profile_binding.source_profile_file_sha256
        or nofault_profile.profile_sha256 != profile_binding.source_profile_sha256
        or tracked_runtime.descriptor_sha256
        != profile_binding.runtime_continuity_descriptor_sha256
        or audit.baseline_id != profile_binding.active_baseline_id
        or audit.baseline_sha256 != profile_binding.active_baseline_sha256
        or campaign.profile_binding_sha256 != profile_binding.binding_sha256
        or campaign.runtime_continuity_descriptor_sha256
        != tracked_runtime.descriptor_sha256
        or campaign.active_baseline_id != audit.baseline_id
        or campaign.active_baseline_sha256 != audit.baseline_sha256
        or campaign.predecessor_head != context.predecessor_head
    ):
        raise ValueError("Product v0.2.3.1 No-Fault profile binding differs")
    processes = _ProductHostProcessesV023(
        root=root,
        data_root=product_data_root,
        private_root=private_root / "product-processes",
    )
    queue_state = verify_queue_default_v021(
        lifecycle.flag_file,
        expected_default_value=nofault_profile.queue_fault_flag,
    )
    flag_before = _sha256(lifecycle.flag_file)
    if queue_state.before_sha256 != flag_before:
        raise ValueError("Product v0.2.3.1 queue Baseline SHA differs")
    start = RuntimeContinuationSessionStartV0231.build(
        session_ordinal=1,
        continuity_descriptor_sha256=tracked_runtime.descriptor_sha256,
        execution_head=execution_head,
        private_root_locator=private_locator,
        stage="COMPOSE_VERIFIED",
        pre_start_compose_sha256=tracked_runtime.resolved_compose_sha256,
        incident_count_before=0,
        diagnosis_count_before=0,
    )
    session_consumed = False

    def consume_session() -> None:
        nonlocal session_consumed
        fresh_context = admit_product_baseline_continuation_context_v0231(
            predecessor_root=predecessor,
            binding=binding,
            predecessor=cast(dict[str, Any], manifest["predecessor"]),
        )
        fresh_counts = _read_only_database_counts(
            product_data_root, audit.environment_id
        )
        if fresh_context != context or fresh_counts != before_counts:
            raise ValueError("Product v0.2.3.1 start-boundary Baseline differs")
        private_root.mkdir(parents=True, mode=0o700)
        write_private_json(
            private_root / "session-start.json",
            start.model_dump(mode="json"),
            create_once=True,
        )
        session_consumed = True

    backend: Any = None
    fresh_authority: Any = None
    authority_proof: RuntimeAuthorityContinuityProofV0231 | None = None
    inner_restart: BaselineRestartProofV023 | None = None
    restart_proof: BaselineRestartProofV0231 | None = None
    rotations: list[dict[str, object]] = []
    incident: IncidentRecordV1 | None = None
    diagnosis: DiagnosisResultV1 | None = None
    evidence: EvidenceBundleV1 | None = None
    traffic_result: NoFaultTrafficResultV023 | None = None
    queue_snapshot: NoFaultQueueSnapshotV023 | None = None
    inner_result: NoFaultAcceptanceResultV023 | None = None
    result: NoFaultAcceptanceResultV0231 | None = None
    outer_baseline_before: str | None = None
    queue_default_unchanged = False
    outer_baseline_unchanged = False
    product_cleanup: dict[str, object] = {"verdict": "BLOCKED"}
    demo_cleanup: dict[str, object] = {"verdict": "BLOCKED"}
    live_error: BaseException | None = None
    incident_mutation_possible = False
    stage = "COMPOSE_VERIFIED"
    try:
        lifecycle.start(on_boundary_verified=consume_session)
        stage = "SANDBOX_STARTED"
        lifecycle.wait_ready()
        stage = "SANDBOX_READY"
        backend = lifecycle.authorize_reads()
        fresh_authority = lifecycle.rebound_authority
        if (
            backend.authority != authority.read_authority
            or fresh_authority is None
            or fresh_authority != authority
        ):
            raise ValueError("post-start read authority differs")
        outer_baseline_before = lifecycle.read_baseline_sha256()
        runtime_path = product_data_root / "pilot/runtime-readiness.json"
        first_rotation = _rotate_runtime_snapshot_v0231(
            data_root=product_data_root,
            path=runtime_path,
            snapshot=_runtime_snapshot(backend=backend, authority=fresh_authority),
            private_root=private_root,
            ordinal=1,
        )
        rotations.append(first_rotation)
        authority_proof = _authority_proof(
            descriptor=tracked_runtime,
            authority=fresh_authority,
            backend=backend,
            rotation=first_rotation,
        )
        stage = "AUTHORITY_VERIFIED"

        processes.start()
        stage = "PRODUCT_STARTED"
        before = _restart_snapshot(
            processes,
            environment_id=audit.environment_id,
            service_identity_sha256=expected_identity_sha256,
            baseline_candidate_identity_sha256=candidate_sha256,
            capability_sha256=expected_capability_sha256,
        )
        processes.restart()
        stage = "PRODUCT_RESTARTED"
        restart_identity, restart_capability, restart_candidate = (
            _load_persisted_bindings(product_data_root, audit)
        )
        rebound_audit = ProductBaselineReadinessAuditV023.model_validate(
            _request_json(
                processes,
                "GET",
                f"/v1/baselines/{audit.baseline_id}/window-audit-v023",
            )
        )
        if (
            restart_identity.identity_sha256 != expected_identity_sha256
            or restart_capability.capability_sha256 != expected_capability_sha256
            or restart_candidate != candidate_sha256
            or rebound_audit.audit_sha256 != audit.audit_sha256
        ):
            raise ValueError("Product v0.2.3.1 restart binding differs")
        after = _restart_snapshot(
            processes,
            environment_id=audit.environment_id,
            service_identity_sha256=restart_identity.identity_sha256,
            baseline_candidate_identity_sha256=restart_candidate,
            capability_sha256=restart_capability.capability_sha256,
        )
        inner_restart = BaselineRestartProofV023.build(
            before=before,
            after=after,
            connector_verification_count=0,
        )
        after_restart_counts = _database_counts(product_data_root, audit.environment_id)
        pending, running, failed = _queue_counts(product_data_root)
        for name in (
            "baseline_count",
            "baseline_job_count",
            "verify_job_count",
            "incident_count",
            "diagnosis_count",
            "fault_family_count",
            "knowledge_artifact_count",
        ):
            if after_restart_counts[name] != before_counts[name]:
                raise ValueError("Product v0.2.3.1 restart counts changed")
        if any((pending, running, failed)):
            raise ValueError("Product v0.2.3.1 restart queue is not healthy")
        restart_proof = BaselineRestartProofV0231.build(
            inner_proof=inner_restart,
            active_profile_sha256=audit.active_opensearch_profile_sha256,
            readiness_audit_sha256=audit.audit_sha256,
            baseline_job_count_before=before_counts["baseline_job_count"],
            baseline_job_count_after=after_restart_counts["baseline_job_count"],
            verify_job_count_before=before_counts["verify_job_count"],
            verify_job_count_after=after_restart_counts["verify_job_count"],
            pending_jobs_after=pending,
            running_jobs_after=running,
            failed_jobs_after=failed,
            terminal=RESTART_PASS,
        )
        write_private_json(
            private_root / "restart-checkpoint.json",
            {
                "schema_version": "ecomsre.product.restart-checkpoint.v0231",
                "stage": "BASELINE_RESTART_VERIFIED",
                "runtime_terminal": RUNTIME_PASS,
                "restart_terminal": RESTART_PASS,
                "runtime_authority_proof": authority_proof.model_dump(mode="json"),
                "baseline_restart_proof": restart_proof.model_dump(mode="json"),
                "incident_count": 0,
                "diagnosis_count": 0,
            },
            create_once=True,
        )
        stage = "BASELINE_RESTART_VERIFIED"

        verify_queue_default_v021(
            lifecycle.flag_file,
            expected_default_value=nofault_profile.queue_fault_flag,
            expected_sha256=flag_before,
        )
        episode_started_at = datetime.now(UTC)
        healthy_profile = HealthyTrafficProfileV021(
            request_seed=nofault_profile.seed,
            maximum_request_count=nofault_profile.request_count,
            requests_per_second=nofault_profile.requests_per_second,
            error_budget=max(
                1,
                int(
                    nofault_profile.request_count
                    * nofault_profile.maximum_error_fraction
                )
                + 1,
            ),
        )
        with httpx.Client() as traffic_client:
            measured = BoundedHealthyCheckoutTrafficV021(client=traffic_client).run(
                endpoint="http://127.0.0.1:18080/api/checkout",
                profile=healthy_profile,
            )
        remaining = (
            episode_started_at
            + timedelta(seconds=_EPISODE_MINIMUM_SECONDS)
            - datetime.now(UTC)
        ).total_seconds()
        if remaining > 0:
            time.sleep(remaining)
        rotations.append(
            _rotate_runtime_snapshot_v0231(
                data_root=product_data_root,
                path=runtime_path,
                snapshot=_runtime_snapshot(
                    backend=backend,
                    authority=fresh_authority,
                ),
                private_root=private_root,
                ordinal=2,
            )
        )
        episode_ended_at = datetime.now(UTC)
        incident_mutation_possible = True
        incident = IncidentRecordV1.model_validate(
            _request_json(
                processes,
                "POST",
                "/v1/incidents",
                payload={
                    "environment_id": audit.environment_id,
                    "external_incident_key": (
                        f"product-v0231-nofault-{secrets.token_hex(8)}"
                    ),
                    "alert_name": "Product v0.2.3.1 No-Fault acceptance",
                    "summary": "Fresh healthy checkout observation with no fault active.",
                    "started_at": episode_started_at.isoformat(),
                    "ended_at": episode_ended_at.isoformat(),
                    "candidate_service_ids": list(audit.baseline_entity_service_ids),
                    "labels": {"fault": nofault_profile.incident_fault_label},
                },
            )
        )
        if (
            incident.service_identity_sha256 != expected_identity_sha256
            or incident.source_capability_sha256 != expected_capability_sha256
            or incident.baseline_id != audit.baseline_id
            or incident.baseline_sha256 != audit.baseline_sha256
        ):
            raise RuntimeError("Product v0.2.3.1 Incident binding changed")
        stage = "INCIDENT_CREATED"
        traffic_result = NoFaultTrafficResultV023.build(
            environment_id=audit.environment_id,
            incident_id=incident.incident_id,
            window=ConnectorWindowV1(
                started_at=incident.started_at,
                ended_at=incident.diagnosis_observed_at,
            ),
            profile_sha256=nofault_profile.profile_sha256,
            planned_request_count=nofault_profile.request_count,
            completed_request_count=measured.attempted,
            error_count=measured.failed,
            requests_per_second=nofault_profile.requests_per_second,
            maximum_error_fraction=nofault_profile.maximum_error_fraction,
            queue_fault_flag=nofault_profile.queue_fault_flag,
            passed=(
                measured.attempted == nofault_profile.request_count
                and measured.failed / max(1, measured.attempted)
                <= nofault_profile.maximum_error_fraction
            ),
        )
        queued_diagnosis = _request_json(
            processes,
            "POST",
            f"/v1/incidents/{incident.incident_id}/diagnosis-jobs",
            payload=None,
        )
        diagnosis_job = _wait_job(
            processes,
            str(queued_diagnosis["job_id"]),
            data_root=product_data_root,
            timeout_seconds=240,
        )
        if diagnosis_job.status is not ProductJobStatusV1.SUCCEEDED or not isinstance(
            diagnosis_job.result, dict
        ):
            raise RuntimeError(
                diagnosis_job.safe_error_code or "Product v0.2.3.1 Diagnosis failed"
            )
        diagnosis = DiagnosisResultV1.model_validate(diagnosis_job.result)
        evidence = EvidenceBundleV1.model_validate(
            _request_json(
                processes,
                "GET",
                f"/v1/incidents/{incident.incident_id}/evidence",
            )
        )
        after_counts = _database_counts(product_data_root, audit.environment_id)
        pending, running, failed = _queue_counts(product_data_root)
        queue_snapshot = NoFaultQueueSnapshotV023.build(
            environment_id=audit.environment_id,
            observed_at=datetime.now(UTC),
            pending_jobs=pending,
            running_jobs=running,
            failed_jobs=failed,
            queue_fault_flag=nofault_profile.queue_fault_flag,
        )
        capability_assessment = NoFaultCapabilityAssessmentV023.build(
            runtime_healthy=_successful_runtime_ref(evidence) is not None,
            runtime_evidence_ref=_successful_runtime_ref(evidence),
            successful_sources=_successful_evidence_sources(
                evidence,
                incident=incident,
            ),
            healthy_traffic_passed=traffic_result.passed,
            healthy_traffic_result_sha256=traffic_result.result_sha256,
            limitation_evidence_refs=_limitation_evidence_refs(diagnosis, evidence),
        )
        if inner_restart is None or authority_proof is None or restart_proof is None:
            raise AssertionError("Product v0.2.3.1 session checkpoint is absent")
        inner_result = score_nofault_v023(
            baseline_audit=audit,
            restart_proof=inner_restart,
            incident=incident,
            diagnosis=diagnosis,
            bundle=evidence,
            capability_assessment=capability_assessment,
            execution_profile=nofault_profile,
            traffic_result=traffic_result,
            queue_snapshot=queue_snapshot,
            active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
            incident_count=after_counts["incident_count"],
            diagnosis_count=after_counts["diagnosis_count"],
            fault_family_count=after_counts["fault_family_count"],
            action_authority_violations=0,
            agent_writes=0,
            runbook_executions=0,
        )
        result = NoFaultAcceptanceResultV0231.build(
            wrapped_v023_result=inner_result,
            runtime_continuity_descriptor_sha256=tracked_runtime.descriptor_sha256,
            profile_binding_sha256=profile_binding.binding_sha256,
            runtime_authority_proof_sha256=authority_proof.proof_sha256,
            baseline_restart_proof_sha256=restart_proof.proof_sha256,
            session_start_sha256=start.start_sha256,
        )
        if (
            after_counts["incident_count"] != 1
            or after_counts["diagnosis_count"] != 1
            or after_counts["fault_family_count"] != 0
            or after_counts["knowledge_artifact_count"] != 0
            or any((pending, running, failed))
            or diagnosis.provider_calls != 0
            or diagnosis.agent_writes != 0
            or diagnosis.runbook_executions != 0
            or diagnosis.action_authority.value != "NONE"
        ):
            raise RuntimeError("Product v0.2.3.1 No-Fault contract counters differ")
        stage = "DIAGNOSIS_COMPLETED"
    except BaseException as error:
        live_error = error
    finally:
        try:
            verify_queue_default_v021(
                lifecycle.flag_file,
                expected_default_value=nofault_profile.queue_fault_flag,
                expected_sha256=flag_before,
            )
            queue_default_unchanged = True
        except BaseException as closure_error:
            if live_error is None:
                live_error = closure_error
        if outer_baseline_before is not None:
            try:
                outer_baseline_unchanged = (
                    lifecycle.read_baseline_sha256() == outer_baseline_before
                )
                if not outer_baseline_unchanged:
                    raise RuntimeError("Product v0.2.3.1 outer Baseline changed")
            except BaseException as closure_error:
                if live_error is None:
                    live_error = closure_error
        else:
            outer_baseline_unchanged = queue_default_unchanged
        try:
            product_cleanup = processes.cleanup_observation()
            if product_cleanup.get("verdict") != "CLEAN" and live_error is None:
                live_error = RuntimeError("Product v0.2.3.1 Product cleanup is blocked")
        except BaseException as cleanup_error:
            product_cleanup = {
                "verdict": "BLOCKED",
                "safe_error": f"{type(cleanup_error).__name__}: {cleanup_error}"[:1000],
            }
            if live_error is None:
                live_error = cleanup_error
        try:
            cleanup = lifecycle.cleanup_owned(
                baseline_unchanged=(
                    queue_default_unchanged and outer_baseline_unchanged
                )
            )
            demo_cleanup = cleanup.model_dump(mode="json")
            if cleanup.verdict != "CLEAN" and live_error is None:
                live_error = RuntimeError("Product v0.2.3.1 Demo cleanup is blocked")
        except BaseException as cleanup_error:
            demo_cleanup = {
                "verdict": "BLOCKED",
                "safe_error": f"{type(cleanup_error).__name__}: {cleanup_error}"[:1000],
            }
            if live_error is None:
                live_error = cleanup_error

    cleanup_state = (
        "CLEAN"
        if product_cleanup.get("verdict") == "CLEAN"
        and demo_cleanup.get("verdict") == "CLEAN"
        and queue_default_unchanged
        and outer_baseline_unchanged
        else "BLOCKED"
    )
    if live_error is not None and not session_consumed:
        write_private_json(
            pre_session_root / "failure.json",
            {
                "schema_version": "ecomsre.product.live-preflight-failure.v0231",
                "execution_head": execution_head,
                "stage": stage,
                "safe_error_type": type(live_error).__name__,
                "safe_error": str(live_error)[:1000],
                "cleanup": cleanup_state,
                "live_session_count": 0,
                "incident_count": 0,
                "diagnosis_count": 0,
                "fault_attempt_count": 0,
                "action_authority": "NONE",
            },
            create_once=True,
        )
        raise live_error
    if live_error is not None:
        failure_counts = (
            _database_counts(product_data_root, audit.environment_id)
            if processes.launches
            else _read_only_database_counts(product_data_root, audit.environment_id)
        )
        failure_incident_count = failure_counts["incident_count"]
        failure_diagnosis_count = failure_counts["diagnosis_count"]
        if failure_incident_count not in {0, 1} or failure_diagnosis_count not in {
            0,
            1,
        }:
            raise RuntimeError(
                "BLOCKED_ECOMSRE_PRODUCT_V0231_FAILURE_COUNTS_UNBOUNDED"
            ) from live_error
        failure_class = (
            "POST_INCIDENT_FAILURE"
            if incident_mutation_possible or failure_incident_count == 1
            else (
                "UNCLEAN_CLOSURE"
                if cleanup_state != "CLEAN"
                else _failure_class(stage, live_error)
            )
        )
        failure = RuntimeContinuationSessionCompletionV0231.build(
            session_ordinal=1,
            start_sha256=start.start_sha256,
            continuity_descriptor_sha256=tracked_runtime.descriptor_sha256,
            execution_head=execution_head,
            private_root_locator=private_locator,
            stage="CLOSED",
            pre_start_compose_sha256=tracked_runtime.resolved_compose_sha256,
            post_start_read_authority_sha256=(
                authority.read_authority.authority_sha256
                if authority_proof is not None
                else None
            ),
            post_start_pilot_authority_sha256=(
                authority.pilot_authority_sha256
                if authority_proof is not None
                else None
            ),
            post_start_connector_binding_sha256=(
                authority.connector_binding_sha256
                if authority_proof is not None
                else None
            ),
            product_process_launches=tuple(processes.launches),
            incident_count_before=0,
            incident_count_after=failure_incident_count,
            diagnosis_count_before=0,
            diagnosis_count_after=failure_diagnosis_count,
            cleanup=cleanup_state,
            runtime_terminal=RUNTIME_PASS if authority_proof is not None else None,
            restart_terminal=RESTART_PASS if restart_proof is not None else None,
            nofault_terminal=None,
            terminal="BLOCKED_ECOMSRE_PRODUCT_V0231_SESSION_1",
            failure_class=failure_class,
        )
        write_private_json(
            private_root / "session-completion.json",
            failure.model_dump(mode="json"),
            create_once=True,
        )
        write_private_json(
            private_root / "acceptance.json",
            {
                "schema_version": "ecomsre.product.private-nofault-acceptance.v0231",
                "stage": stage,
                "execution_head": execution_head,
                "session_start_sha256": start.start_sha256,
                "completion_sha256": failure.completion_sha256,
                "incident": None
                if incident is None
                else incident.model_dump(mode="json"),
                "diagnosis": None
                if diagnosis is None
                else diagnosis.model_dump(mode="json"),
                "evidence": None
                if evidence is None
                else evidence.model_dump(mode="json"),
                "safe_error_type": type(live_error).__name__,
                "safe_error": str(live_error)[:1000],
                "failure_class": failure_class,
                "incident_mutation_possible": incident_mutation_possible,
                "database_counts": failure_counts,
                "cleanup": cleanup_state,
                "fault_attempt_count": 0,
                "knowledge_loop_campaign_count": 0,
                "agent_writes": 0,
                "runbook_executions": 0,
                "action_authority": "NONE",
            },
            create_once=True,
        )
        raise RuntimeError(
            f"BLOCKED_ECOMSRE_PRODUCT_V0231_SESSION_1: {failure_class}: {live_error}"
        ) from live_error

    if (
        any(
            item is None
            for item in (
                authority_proof,
                restart_proof,
                incident,
                diagnosis,
                evidence,
                traffic_result,
                queue_snapshot,
                inner_result,
                result,
            )
        )
        or cleanup_state != "CLEAN"
    ):
        raise RuntimeError("Product v0.2.3.1 Session 1 closure is incomplete")
    assert authority_proof is not None
    assert restart_proof is not None
    assert incident is not None
    assert diagnosis is not None
    assert evidence is not None
    assert traffic_result is not None
    assert queue_snapshot is not None
    assert result is not None
    final_counts = _database_counts(product_data_root, audit.environment_id)
    if (
        final_counts["incident_count"] != 1
        or final_counts["diagnosis_count"] != 1
        or final_counts["fault_family_count"] != 0
        or final_counts["knowledge_artifact_count"] != 0
        or final_counts["baseline_count"] != 1
        or final_counts["baseline_job_count"] != 1
        or final_counts["verify_job_count"] != 1
        or any(_queue_counts(product_data_root))
    ):
        raise RuntimeError("Product v0.2.3.1 final Product state differs")

    completion = RuntimeContinuationSessionCompletionV0231.build(
        session_ordinal=1,
        start_sha256=start.start_sha256,
        continuity_descriptor_sha256=tracked_runtime.descriptor_sha256,
        execution_head=execution_head,
        private_root_locator=private_locator,
        stage="CLOSED",
        pre_start_compose_sha256=tracked_runtime.resolved_compose_sha256,
        post_start_read_authority_sha256=authority.read_authority.authority_sha256,
        post_start_pilot_authority_sha256=authority.pilot_authority_sha256,
        post_start_connector_binding_sha256=authority.connector_binding_sha256,
        product_process_launches=tuple(processes.launches),
        incident_count_before=0,
        incident_count_after=1,
        diagnosis_count_before=0,
        diagnosis_count_after=1,
        cleanup="CLEAN",
        runtime_terminal=RUNTIME_PASS,
        restart_terminal=RESTART_PASS,
        nofault_terminal=result.terminal,
        terminal=NOFAULT_ACCEPTANCE_COMPLETE_V0231,
        failure_class=None,
    )
    ledger = RuntimeContinuationSessionLedgerV0231.build(
        starts=(start,), completions=(completion,)
    )
    completion_bytes = canonical_json_bytes(completion.model_dump(mode="json"))
    closure = {
        "queue_default_before_sha256": flag_before,
        "queue_default_unchanged": queue_default_unchanged,
        "outer_baseline_before_sha256": outer_baseline_before,
        "outer_baseline_unchanged": outer_baseline_unchanged,
        "product_cleanup": product_cleanup.get("verdict"),
        "product_cleanup_observation": product_cleanup,
        "demo_cleanup": demo_cleanup.get("verdict"),
        "demo_cleanup_observation": demo_cleanup,
        "non_owned_resources_changed": False,
    }
    cleanup_proof_sha256 = semantic_sha256_v22(closure)
    private_payload = {
        "schema_version": "ecomsre.product.private-nofault-acceptance.v0231",
        "stage": "CLOSED",
        "execution_head": execution_head,
        "session_start": start.model_dump(mode="json"),
        "session_completion": completion.model_dump(mode="json"),
        "runtime_authority_proof": authority_proof.model_dump(mode="json"),
        "baseline_restart_proof": restart_proof.model_dump(mode="json"),
        "runtime_snapshot_rotations": rotations,
        "incident": incident.model_dump(mode="json"),
        "diagnosis": diagnosis.model_dump(mode="json"),
        "evidence": evidence.model_dump(mode="json"),
        "traffic_result": traffic_result.model_dump(mode="json"),
        "queue_snapshot": queue_snapshot.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
        "closure": closure,
        "cleanup_proof_sha256": cleanup_proof_sha256,
        "fault_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority": "NONE",
    }
    private_payload_bytes = canonical_json_bytes(private_payload)

    session_payload: dict[str, Any] = {
        "schema_version": "ecomsre.product.continuation-session.v0231",
        "start": start.model_dump(mode="json"),
        "completion": completion.model_dump(mode="json"),
        "ledger": ledger.model_dump(mode="json"),
        "runtime_authority_proof": authority_proof.model_dump(mode="json"),
        "baseline_restart_proof_sha256": restart_proof.proof_sha256,
        "runtime_snapshot_rotations": rotations,
        "product_cleanup": product_cleanup,
        "demo_cleanup": demo_cleanup,
        "fault_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority": "NONE",
    }
    session_payload["report_sha256"] = semantic_sha256_v22(session_payload)
    restart_payload: dict[str, Any] = {
        "schema_version": "ecomsre.product.baseline-restart.v0231",
        "terminal": RESTART_PASS,
        "runtime_terminal": RUNTIME_PASS,
        "proof": restart_proof.model_dump(mode="json"),
        "runtime_authority_proof_sha256": authority_proof.proof_sha256,
        "session_completion_sha256": completion.completion_sha256,
        "incident_count": 1,
        "diagnosis_count": 1,
        "fault_attempt_count": 0,
        "action_authority": "NONE",
    }
    restart_payload["report_sha256"] = semantic_sha256_v22(restart_payload)
    public_payload: dict[str, Any] = {
        "schema_version": "ecomsre.product.public-nofault-acceptance.v0231",
        "terminal": result.terminal,
        "acceptance_terminal": NOFAULT_ACCEPTANCE_COMPLETE_V0231,
        "execution_head": execution_head,
        "runtime_continuity_descriptor_sha256": tracked_runtime.descriptor_sha256,
        "flagd_bind_descriptor_sha256": tracked_runtime.flagd_bind_descriptor_sha256,
        "runtime_authority_proof_sha256": authority_proof.proof_sha256,
        "environment_configuration_sha256": _sha256(
            root / "config/product-v023/environment.otel-demo.json"
        ),
        "active_profile_sha256": ACTIVE_PROFILE_SHA256_V023,
        "execution_profile_sha256": nofault_profile.profile_sha256,
        "profile_binding_sha256": profile_binding.binding_sha256,
        "active_baseline_id": audit.baseline_id,
        "active_baseline_sha256": audit.baseline_sha256,
        "readiness_audit_sha256": audit.audit_sha256,
        "restart_proof_sha256": restart_proof.proof_sha256,
        "result": result.model_dump(mode="json"),
        "incident_count": 1,
        "diagnosis_count": 1,
        "fault_family_count": 0,
        "knowledge_artifact_count": 0,
        "provider_calls": 0,
        "private_report_sha256": hashlib.sha256(private_payload_bytes).hexdigest(),
        "cleanup_proof_sha256": cleanup_proof_sha256,
        **closure,
        "fault_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "action_authority": "NONE",
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    handoff_ready = result.terminal == NOFAULT_FULLY_SUPPORTED_V0231
    handoff_payload: dict[str, Any] = {
        "schema_version": "ecomsre.product.knowledge-loop-handoff.v0231",
        "terminal": KNOWLEDGE_READY if handoff_ready else KNOWLEDGE_NOT_AUTHORIZED,
        "authorized": handoff_ready,
        "execution_head": execution_head,
        "runtime_continuity_descriptor_sha256": tracked_runtime.descriptor_sha256,
        "flagd_bind_descriptor_sha256": tracked_runtime.flagd_bind_descriptor_sha256,
        "post_start_authority_proof_sha256": authority_proof.proof_sha256,
        "environment_configuration_sha256": public_payload[
            "environment_configuration_sha256"
        ],
        "active_profile_sha256": ACTIVE_PROFILE_SHA256_V023,
        "service_identity_sha256": expected_identity_sha256,
        "capability_sha256": expected_capability_sha256,
        "active_baseline_id": audit.baseline_id,
        "active_baseline_sha256": audit.baseline_sha256,
        "readiness_audit_sha256": audit.audit_sha256,
        "restart_proof_sha256": restart_proof.proof_sha256,
        "incident_sha256": incident.incident_sha256,
        "diagnosis_result_sha256": diagnosis.result_sha256,
        "evidence_bundle_sha256": result.wrapped_v023_result.evidence_bundle_sha256,
        "queue_default_sha256": flag_before,
        "cleanup_proof_sha256": cleanup_proof_sha256,
        "required_repair_reasons": list(result.wrapped_v023_result.reasons),
        "fault_calibration_authorized": handoff_ready,
    }
    handoff_payload["handoff_sha256"] = semantic_sha256_v22(handoff_payload)
    progress_payload: dict[str, Any] = {
        "schema_version": "ecomsre.product.progress.v0231",
        "terminal": NOFAULT_ACCEPTANCE_COMPLETE_V0231,
        "runtime_terminal": RUNTIME_PASS,
        "restart_terminal": RESTART_PASS,
        "measured_nofault_terminal": result.terminal,
        "live_session_count": 1,
        "baseline_attempt_count": 1,
        "incident_count": 1,
        "diagnosis_count": 1,
        "fault_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "action_authority": "NONE",
        "repository_acceptance": "REVIEW_REQUIRED",
    }
    progress_payload["progress_sha256"] = semantic_sha256_v22(progress_payload)

    public_files = {
        session_path.relative_to(root).as_posix(): canonical_json_bytes(
            session_payload
        ),
        restart_path.relative_to(root).as_posix(): canonical_json_bytes(
            restart_payload
        ),
        result_path.relative_to(root).as_posix(): canonical_json_bytes(public_payload),
        handoff_path.relative_to(root).as_posix(): canonical_json_bytes(
            handoff_payload
        ),
        progress_path.relative_to(root).as_posix(): canonical_json_bytes(
            progress_payload
        ),
        "docs/results/product-v0231-nofault-acceptance.md": _markdown_bytes(
            (
                "# Product v0.2.3.1 No-Fault Acceptance",
                "",
                f"Measured terminal: `{result.terminal}`",
                f"Acceptance terminal: `{NOFAULT_ACCEPTANCE_COMPLETE_V0231}`",
                "Incident / Diagnosis: `1 / 1`",
                "Action authority / Agent writes / Runbooks: `NONE / 0 / 0`",
                "Cleanup: `Product CLEAN / Demo CLEAN`",
            )
        ),
        "docs/results/product-v0231-limitations.md": _markdown_bytes(
            (
                "# Product v0.2.3.1 Limitations",
                "",
                f"Measured terminal: `{result.terminal}`",
                *(
                    f"- `{reason}`"
                    for reason in (result.wrapped_v023_result.reasons or ("NONE",))
                ),
                "",
                "This is one owned local No-Fault episode. It does not authorize deployment or remediation.",
            )
        ),
        "docs/results/product-v0231-interview-brief.md": _markdown_bytes(
            (
                "# Product v0.2.3.1 Interview Brief",
                "",
                "One exact-path Runtime authority session preserved the active P01 Baseline across an ordinary Product restart.",
                "The same session then measured one frozen healthy checkout episode with no action authority.",
                f"Measured terminal: `{result.terminal}`",
                "Claim boundary: local owned environment, no fault injection, no Agent write, and no Runbook.",
            )
        ),
        "docs/analysis/product-v0231-knowledge-loop-handoff.md": _markdown_bytes(
            (
                "# Product v0.2.3.1 Knowledge-Loop Handoff",
                "",
                f"Terminal: `{handoff_payload['terminal']}`",
                f"Authorized: `{str(handoff_ready).lower()}`",
                *(
                    f"- `{reason}`"
                    for reason in (result.wrapped_v023_result.reasons or ("NONE",))
                ),
            )
        ),
    }
    _freeze_publication_bundle(
        private_root=private_root,
        execution_head=execution_head,
        private_files={
            "acceptance.json": private_payload_bytes,
            "session-completion.json": completion_bytes,
        },
        public_files=public_files,
    )
    recover_publication_v0231(project_root=root)
    return session_payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--predecessor-root", type=Path)
    parser.add_argument("--recover-publication", action="store_true")
    args = parser.parse_args(argv)
    if args.recover_publication:
        completion = recover_publication_v0231(project_root=args.project_root)
        print(completion["terminal"])
        return 0
    if args.predecessor_root is None:
        parser.error(
            "--predecessor-root is required unless --recover-publication is used"
        )
    result = run_live_authority_restart_v0231(
        project_root=args.project_root,
        predecessor_root=args.predecessor_root,
    )
    completion = cast(dict[str, Any], result["completion"])
    print(completion["runtime_terminal"])
    print(completion["restart_terminal"])
    print(completion["nofault_terminal"])
    print(completion["terminal"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
