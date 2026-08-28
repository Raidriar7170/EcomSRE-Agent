"""Successor profile-calibration admission and live execution boundary."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import sqlite3
import stat
import time
from typing import Any, Mapping, Sequence, cast
from urllib.parse import quote

from fastapi.testclient import TestClient
import httpx

from ecomsre.dta_v2.read_only_smoke import _SandboxOwnedSmokeLifecycle
from ecomsre.dta_v2.telemetry_adapters import LocalSandboxReadBackend
from ecomsre.dta_v2.tool_contracts import (
    semantic_sha256 as authority_semantic_sha256,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts import ProvisionalIncidentReportV23
from ecomsre.dta_v2.v23.contracts_v231 import (
    ProvisionalIncidentReportV231,
    ReportUncertaintyModeV231,
)
from ecomsre.product.app import create_app
from ecomsre.product.baselines import EnvironmentBaselineV1
from ecomsre.product.connectors.pilot_runtime import (
    PilotRuntimeSnapshotV02,
)
from ecomsre.product.pilot.episode_runner_v02 import PilotEpisodeRepositoryV02
from ecomsre.product.pilot.baseline_audit_v021 import BaselineReadinessAuditV021
from ecomsre.product.pilot.baseline_readiness_v021 import (
    PilotBaselineBindingV021,
    load_pilot_baseline_binding_v021,
    verify_queue_default_v021,
)
from ecomsre.product.pilot.calibration_v021 import (
    QueueProfileV021,
    render_public_calibration_markdown_v021,
)
from ecomsre.product.pilot.contracts_v02 import (
    PilotAttemptFailureDomainV02,
    PilotAttemptStageV02,
    PilotEpisodeTerminalV02,
    QueueProfileV02,
    TrafficProfileV02,
)
from ecomsre.product.pilot.live_calibration_v02 import (
    _AttemptLedgerV02,
    _authority_inputs,
    _candidate_service_binding,
    _connector_health,
    _request_json,
    _run_product_job,
    _runtime_services,
)
from ecomsre.product.pilot.queue_profile_v02 import QueueFlagControllerV02
from ecomsre.product.pilot.readiness_attempts_v021 import (
    write_private_bound_json_v021,
)
from ecomsre.product.pilot.recovery_v02 import verify_baseline_recovery_v02
from ecomsre.product.pilot.runtime_authority_v02 import (
    PilotRuntimeAuthorityV02,
    load_pilot_runtime_authority_v02,
)
from ecomsre.product.pilot.traffic_v02 import BoundedCheckoutTrafficV02
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from ecomsre_live_sandbox.contracts import (
    ensure_private_directory,
    load_bundle,
    write_private_json,
)
from ecomsre_live_sandbox.control import build_flag_documents
from ecomsre_live_sandbox.environment import SandboxEnvironment
from scripts.ci.verify_product_v021_history import verify_product_v021_history
from scripts.ci.verify_product_v021_increment1 import verify_product_v021_increment1


CALIBRATION_CONTRACT_READY_V021 = (
    "ECOMSRE_PRODUCT_V021_UNKNOWN_FAULT_PROFILE_CONTRACT_READY"
)
CALIBRATION_PASS_V021 = "ECOMSRE_PRODUCT_V021_UNKNOWN_FAULT_PROFILE_PASS"
CALIBRATION_BLOCKED_V021 = "BLOCKED_ECOMSRE_PRODUCT_V021_UNKNOWN_FAULT_PROFILE"
READINESS_BLOCKED_V021 = "BLOCKED_ECOMSRE_PRODUCT_V021_BASELINE_READINESS"


def _read_regular_bytes_v021(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"calibration input is not a regular file: {path}")
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def _load_object_v021(path: Path) -> dict[str, Any]:
    payload = json.loads(_read_regular_bytes_v021(path))
    if not isinstance(payload, dict):
        raise ValueError(f"calibration input is not an object: {path}")
    return payload


def _exact_bound_path_v021(
    repository_root: Path,
    relative_path: str,
    *,
    expected: str,
) -> Path:
    root = Path(repository_root)
    if not root.is_absolute():
        raise ValueError("exact bound root must be absolute")
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or expected not in {"file", "directory"}
    ):
        raise ValueError("exact bound relative path differs")
    current = root
    root_metadata = os.lstat(current)
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(
        root_metadata.st_mode
    ):
        raise ValueError("exact bound root is not a regular directory")
    for index, part in enumerate(relative.parts):
        current = current / part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError as error:
            raise ValueError("exact bound path is absent") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("exact bound path contains a symlink")
        final = index == len(relative.parts) - 1
        should_be_directory = not final or expected == "directory"
        if should_be_directory and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("exact bound path component is not a directory")
        if final and expected == "file" and not stat.S_ISREG(metadata.st_mode):
            raise ValueError("exact bound path target is not a regular file")
    return current


def _load_exact_repository_object_v021(
    repository_root: Path,
    relative_path: str,
) -> dict[str, Any]:
    return _load_object_v021(
        _exact_bound_path_v021(
            Path(repository_root),
            relative_path,
            expected="file",
        )
    )


def _require_absent_public_targets_v021(
    repository_root: Path,
    targets: Sequence[Path],
) -> None:
    root = Path(repository_root)
    if not root.is_absolute():
        root = root.resolve(strict=True)
    for raw_target in targets:
        target = Path(raw_target)
        if not target.is_absolute():
            target = root / target
        try:
            relative = target.relative_to(root)
        except ValueError as error:
            raise ValueError("calibration public target escapes repository") from error
        _exact_bound_path_v021(
            root,
            relative.parent.as_posix(),
            expected="directory",
        )
        try:
            os.lstat(target)
        except FileNotFoundError:
            continue
        raise ValueError("calibration public target already exists or is a symlink")


def _should_continue_calibration_v021(episode_terminal: object) -> bool:
    return episode_terminal in {
        PilotEpisodeTerminalV02.NO_INCIDENT_FALSELY_ADMITTED.value,
        PilotEpisodeTerminalV02.OPEN_WORLD_NOT_REACHED.value,
        PilotEpisodeTerminalV02.PROFILE_NOT_OBSERVABLE.value,
    }


def _summarize_calibration_evidence_v021(
    diagnosis: Mapping[str, object],
    evidence: Mapping[str, object],
    *,
    injected_value: int,
) -> dict[str, object]:
    objects = evidence.get("objects")
    if not isinstance(objects, list):
        raise RuntimeError("Product calibration evidence bundle is incomplete")
    by_ref = {
        str(item.get("evidence_ref")): item
        for item in objects
        if isinstance(item, dict) and item.get("evidence_ref")
    }
    linked: set[str] = set()
    for owner in (diagnosis, evidence):
        for key in ("supporting_evidence_refs", "contradicting_evidence_refs"):
            refs = owner.get(key)
            if isinstance(refs, list):
                linked.update(str(item) for item in refs)
    supporting = diagnosis.get("supporting_evidence_refs")
    support_refs = (
        tuple(str(item) for item in supporting)
        if isinstance(supporting, list)
        else ()
    )
    support_sources = tuple(
        sorted(
            {
                str(by_ref[reference].get("source"))
                for reference in support_refs
                if reference in by_ref
            }
        )
    )
    source_payloads: list[tuple[str, Mapping[str, object]]] = []
    for item in objects:
        if not isinstance(item, dict):
            continue
        raw_payload = item.get("payload")
        payload: Mapping[str, object] = (
            raw_payload if isinstance(raw_payload, dict) else {}
        )
        source_payloads.append((str(item.get("source")), payload))
    supporting_log_payloads: list[Mapping[str, object]] = []
    for reference in support_refs:
        item = by_ref.get(reference)
        if not isinstance(item, dict) or item.get("source") != "LOGS":
            continue
        raw_payload = item.get("payload")
        supporting_log_payloads.append(
            raw_payload if isinstance(raw_payload, dict) else {}
        )
    log_payload = " ".join(
        json.dumps(payload, sort_keys=True).casefold()
        for payload in supporting_log_payloads
    )

    def runtime_covers_checkout(payload: Mapping[str, object]) -> bool:
        connector_result = payload.get("connector_result")
        covered = (
            connector_result.get("covered_services")
            if isinstance(connector_result, dict)
            else None
        )
        return isinstance(covered, list) and "checkout" in covered

    runtime_root_coverage = any(
        source == "RUNTIME"
        and runtime_covers_checkout(payload)
        for source, payload in source_payloads
    )
    private_markers = (
        "kafkaqueueproblems",
        "featureflag",
        "checkout_kafka_queue_overload",
        f"ecomsre-v02-{injected_value}",
        f"done with #{injected_value} messages",
    )
    product_projection = json.dumps(
        {"diagnosis": diagnosis, "evidence": evidence},
        sort_keys=True,
    ).casefold()
    provisional = diagnosis.get("provisional_report")
    provisional_terminal: str | None = None
    provisional_report_valid = False
    if isinstance(provisional, dict):
        try:
            schema_version = provisional.get("schema_version")
            if schema_version == "dta-v23.provisional-incident-report.v1":
                typed_report: (
                    ProvisionalIncidentReportV23
                    | ProvisionalIncidentReportV231
                ) = ProvisionalIncidentReportV23.model_validate(provisional)
                provisional_terminal = typed_report.terminal
                expected_uncertainty = True
            elif schema_version == "dta-v231.provisional-incident-report.v1":
                typed_report = ProvisionalIncidentReportV231.model_validate(
                    provisional
                )
                expected_uncertainty = (
                    typed_report.uncertainty_mode
                    is ReportUncertaintyModeV231.COMPETING_HYPOTHESES
                )
                provisional_terminal = (
                    "UNREGISTERED_INCIDENT_WITH_COMPETING_HYPOTHESES"
                    if expected_uncertainty
                    else typed_report.terminal
                )
            else:
                raise ValueError("unsupported provisional report schema")
            report_support = set(typed_report.supporting_evidence_refs)
            diagnosis_support = set(support_refs)
            provisional_report_valid = (
                typed_report.terminal == "UNREGISTERED_INCIDENT_SUSPECTED"
                and expected_uncertainty
                and tuple(typed_report.suspected_root_services) == ("checkout",)
                and typed_report.action_authority == "NONE"
                and bool(report_support)
                and report_support.issubset(by_ref)
                and report_support.issubset(diagnosis_support)
            )
        except (TypeError, ValueError):
            provisional_terminal = str(provisional.get("terminal") or "") or None
    corroborating_sources = {
        "METRICS",
        "TRACES",
        "RUNTIME",
        "RESOURCES",
    }.intersection(support_sources)
    return {
        "evidence_object_count": len(objects),
        "evidence_refs_resolve": linked.issubset(by_ref),
        "support_sources": support_sources,
        "useful_evidence_source_count": len(support_sources),
        "queue_log_observed": (
            "overloading queue" in log_payload
            or "queue overload activity" in log_payload
        ),
        "corroborating_source_available": bool(corroborating_sources),
        "runtime_root_coverage": runtime_root_coverage,
        "provisional_terminal": provisional_terminal,
        "provisional_report_valid": provisional_report_valid,
        "truth_isolation_pass": not any(
            marker in product_projection for marker in private_markers
        ),
    }


def _classify_calibration_attempt_v021(
    diagnosis: Mapping[str, object],
    summary: Mapping[str, object],
    *,
    logical_root_services: tuple[str, ...],
) -> PilotEpisodeTerminalV02:
    terminal = diagnosis.get("terminal")
    source_count = summary.get("useful_evidence_source_count")
    support_sources = summary.get("support_sources")
    if terminal == "CORE_KNOWN":
        return PilotEpisodeTerminalV02.CORE_ABSORBED
    if terminal == "EXTENSION_KNOWN":
        return PilotEpisodeTerminalV02.EXTENSION_ABSORBED
    if terminal == "NO_INCIDENT":
        return PilotEpisodeTerminalV02.NO_INCIDENT_FALSELY_ADMITTED
    if terminal != "OPEN_WORLD":
        return PilotEpisodeTerminalV02.OPEN_WORLD_NOT_REACHED
    if (
        summary.get("provisional_terminal")
        not in {
            "UNREGISTERED_INCIDENT_SUSPECTED",
            "UNREGISTERED_INCIDENT_WITH_COMPETING_HYPOTHESES",
        }
        or summary.get("provisional_report_valid") is not True
        or summary.get("queue_log_observed") is not True
        or not isinstance(support_sources, (list, tuple))
        or "LOGS" not in support_sources
        or summary.get("corroborating_source_available") is not True
        or summary.get("runtime_root_coverage") is not True
        or summary.get("evidence_refs_resolve") is not True
        or summary.get("truth_isolation_pass") is not True
        or not isinstance(source_count, int)
        or source_count < 2
        or logical_root_services != ("checkout",)
        or diagnosis.get("action_authority") != "NONE"
        or diagnosis.get("agent_writes") != 0
        or diagnosis.get("runbook_executions") != 0
    ):
        return PilotEpisodeTerminalV02.PROFILE_NOT_OBSERVABLE
    return PilotEpisodeTerminalV02.PASS


def _verify_campaign_contract_v021(campaign: dict[str, Any]) -> None:
    if (
        campaign.get("schema_version") != "ecomsre.product.pilot.campaign.v021"
        or campaign.get("goal_version")
        != "ecomsre-product-v021-live-baseline-knowledge-loop-successor-v1"
        or campaign.get("required_readiness_terminal")
        != "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_PASS"
        or campaign.get("required_profile_terminal") != CALIBRATION_PASS_V021
        or campaign.get("accepted_schedule") != ["N0", "P1", "P2", "P3"]
        or campaign.get("heldout_schedule") != ["H1"]
        or campaign.get("maximum_changed_calibration_iterations") != 2
        or campaign.get("human_checkpoint_a") != "UNFULFILLED"
        or campaign.get("human_checkpoint_b") != "UNFULFILLED"
        or campaign.get("action_authority") != "NONE"
        or campaign.get("runbook_authority") != "NONE"
    ):
        raise ValueError("v0.2.1 calibration campaign contract differs")
    raw_traffic = campaign.get("traffic_profiles")
    if not isinstance(raw_traffic, dict) or set(raw_traffic) != {
        "CALIBRATION",
        "N0",
        "P1",
        "P2",
        "P3",
        "H1",
    }:
        raise ValueError("v0.2.1 traffic profile set differs")
    traffic = {
        name: TrafficProfileV02.model_validate(payload)
        for name, payload in raw_traffic.items()
    }
    if (
        len({traffic[name].request_seed for name in ("P1", "P2", "P3")}) != 3
        or len(
            {traffic[name].requests_per_second for name in ("P1", "P2", "P3")}
        )
        < 2
    ):
        raise ValueError("v0.2.1 positive traffic independence differs")


def _verify_frozen_product_state_v021(
    repository_root: Path,
    *,
    binding_path: Path,
) -> dict[str, object]:
    root = repository_root.resolve(strict=True)
    binding = load_pilot_baseline_binding_v021(binding_path)
    _exact_bound_path_v021(
        root,
        binding.product_data_root,
        expected="directory",
    )
    sqlite_path = _exact_bound_path_v021(
        root,
        f"{binding.product_data_root}/product.sqlite3",
        expected="file",
    )
    database_uri = f"file:{quote(str(sqlite_path), safe='/')}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        baseline_row = connection.execute(
            "SELECT payload_json, active FROM baseline_versions "
            "WHERE baseline_id = ?",
            (binding.baseline_id,),
        ).fetchone()
        audit_row = connection.execute(
            "SELECT payload_json FROM baseline_readiness_audits_v021 "
            "WHERE baseline_id = ?",
            (binding.baseline_id,),
        ).fetchone()
        active_rows = connection.execute(
            "SELECT baseline_id FROM baseline_versions "
            "WHERE environment_id = ? AND active = 1",
            (binding.environment_id,),
        ).fetchall()
    if baseline_row is None or audit_row is None:
        raise ValueError("frozen Product baseline or audit is absent")
    baseline_payload = json.loads(str(baseline_row["payload_json"]))
    if not isinstance(baseline_payload, dict):
        raise ValueError("frozen Product baseline payload differs")
    baseline_payload["active"] = bool(baseline_row["active"])
    baseline = EnvironmentBaselineV1.model_validate(baseline_payload)
    audit = BaselineReadinessAuditV021.model_validate_json(
        str(audit_row["payload_json"])
    )
    accepted_ordinals = tuple(
        item.window_ordinal for item in audit.windows if item.accepted
    )
    if (
        baseline.environment_id != binding.environment_id
        or baseline.baseline_sha256 != binding.baseline_sha256
        or baseline.active is not True
        or tuple(str(item["baseline_id"]) for item in active_rows)
        != (binding.baseline_id,)
        or audit.environment_id != binding.environment_id
        or audit.audit_sha256 != binding.audit_sha256
        or audit.parity_sha256 != binding.parity_sha256
        or audit.build_policy != binding.build_policy.model_dump(mode="json")
        or accepted_ordinals != binding.accepted_window_ordinals
        or audit.coverage_matrix != binding.source_coverage_matrix
        or audit.capability_sha256 != binding.capability_matrix_sha256
    ):
        raise ValueError("frozen Product baseline binding differs from persisted state")
    authority_path = _exact_bound_path_v021(
        root,
        f"{binding.product_data_root}/pilot/runtime-authority.json",
        expected="file",
    )
    authority = load_pilot_runtime_authority_v02(authority_path)
    snapshot_path = _exact_bound_path_v021(
        root,
        f"{binding.product_data_root}/{binding.runtime_snapshot_ref}",
        expected="file",
    )
    snapshot = PilotRuntimeSnapshotV02.model_validate_json(
        _read_regular_bytes_v021(snapshot_path)
    )
    if (
        authority.environment_id != binding.environment_id
        or authority.connector_binding_sha256 != binding.runtime_authority_sha256
        or snapshot.environment_id != binding.environment_id
        or snapshot.authority_sha256 != binding.runtime_authority_sha256
    ):
        raise ValueError("frozen Product Runtime binding differs")
    _exact_bound_path_v021(
        root,
        binding.readiness_private_root,
        expected="directory",
    )
    queue_flag_path = _exact_bound_path_v021(
        root,
        f"{binding.readiness_private_root}/{binding.queue_flag_ref}",
        expected="file",
    )
    verify_queue_default_v021(
        queue_flag_path,
        expected_default_value=0,
    )
    return {
        "baseline_id": baseline.baseline_id,
        "baseline_sha256": baseline.baseline_sha256,
        "environment_id": baseline.environment_id,
        "audit_sha256": audit.audit_sha256,
        "runtime_authority_sha256": binding.runtime_authority_sha256,
        "product_data_root": binding.product_data_root,
        "readiness_private_root": binding.readiness_private_root,
    }


def verify_calibration_contract_v021(repository_root: Path) -> dict[str, object]:
    root = Path(repository_root).resolve(strict=True)
    history = verify_product_v021_history(root)
    increment1 = verify_product_v021_increment1(root)
    profile = QueueProfileV021.model_validate_json(
        _read_regular_bytes_v021(root / "config/product-v021/live-pilot/profile.json")
    )
    if profile.selected_value is not None or profile.profile_sha256 is not None:
        raise ValueError("v0.2.1 calibration profile is already frozen")
    campaign = _load_object_v021(
        root / "config/product-v021/live-pilot/campaign.json"
    )
    _verify_campaign_contract_v021(campaign)
    binding_path = root / "config/product-v021/live-pilot/baseline-binding.json"
    sentinel = root / ".local/product-v021/private-live-control/calibration-start.json"
    common: dict[str, object] = {
        "history_status": history["status"],
        "increment1_status": increment1["status"],
        "profile_contract_sha256": profile.contract_sha256,
        "candidate_values": profile.candidate_values,
        "maximum_changed_calibration_iterations": 2,
        "calibration_execution_count": int(sentinel.exists()),
        "fault_attempt_count": 0,
        "action_authority": "NONE",
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    if not binding_path.exists():
        return {
            **common,
            "terminal": READINESS_BLOCKED_V021,
            "baseline_binding_status": "ABSENT",
        }
    if sentinel.exists():
        return {
            **common,
            "terminal": CALIBRATION_BLOCKED_V021,
            "baseline_binding_status": "BOUND",
            "campaign_status": "ALREADY_STARTED",
        }
    frozen = _verify_frozen_product_state_v021(
        root,
        binding_path=binding_path,
    )
    return {
        **common,
        **frozen,
        "terminal": CALIBRATION_CONTRACT_READY_V021,
        "baseline_binding_status": "BOUND_ACTIVE_VERIFIED",
        "campaign_status": "NOT_STARTED",
    }


def _atomic_replace_text_v021(path: Path, content: str) -> None:
    destination = Path(path)
    if destination.is_symlink() or not destination.is_file():
        raise ValueError("calibration output target is not a regular file")
    temporary = destination.parent / f".{destination.name}.product-v021.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, destination)
        os.chmod(destination, 0o644, follow_symlinks=False)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _rotate_runtime_snapshot_v021(
    *,
    snapshot_path: Path,
    snapshot: PilotRuntimeSnapshotV02,
    private_report_root: Path,
) -> str:
    before_bytes = _read_regular_bytes_v021(snapshot_path)
    before = PilotRuntimeSnapshotV02.model_validate_json(before_bytes)
    if (
        before.environment_id != snapshot.environment_id
        or before.authority_sha256 != snapshot.authority_sha256
    ):
        raise ValueError("Runtime snapshot rotation binding differs")
    encoded = (snapshot.model_dump_json() + "\n").encode("utf-8")
    temporary = snapshot_path.parent / f".{snapshot_path.name}.rotate-v021.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, snapshot_path)
        os.chmod(snapshot_path, 0o600, follow_symlinks=False)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()
    if _read_regular_bytes_v021(snapshot_path) != encoded:
        raise RuntimeError("Runtime snapshot rotation readback differs")
    return write_private_bound_json_v021(
        private_report_root / "runtime-snapshot-rotation.json",
        {
            "schema_version": "ecomsre.product.runtime-snapshot-rotation.v021",
            "environment_id": snapshot.environment_id,
            "authority_sha256": snapshot.authority_sha256,
            "before_snapshot_sha256": before.snapshot_sha256,
            "after_snapshot_sha256": snapshot.snapshot_sha256,
            "rotated_at": datetime.now(UTC).isoformat(),
        },
    )


def _verify_active_binding_via_api_v021(
    client: TestClient,
    binding: PilotBaselineBindingV021,
) -> None:
    listed = _request_json(
        client,
        "GET",
        f"/v1/environments/{binding.environment_id}/baselines",
    )
    items = listed.get("items")
    active = (
        tuple(
            item
            for item in items
            if isinstance(item, dict) and item.get("active") is True
        )
        if isinstance(items, list)
        else ()
    )
    if (
        len(active) != 1
        or active[0].get("baseline_id") != binding.baseline_id
        or active[0].get("baseline_sha256") != binding.baseline_sha256
        or active[0].get("environment_id") != binding.environment_id
    ):
        raise RuntimeError("active Product baseline differs from the frozen binding")
    audit = BaselineReadinessAuditV021.model_validate(
        _request_json(
            client,
            "GET",
            f"/v1/baselines/{binding.baseline_id}/window-audit",
        )
    )
    if (
        audit.audit_sha256 != binding.audit_sha256
        or audit.parity_sha256 != binding.parity_sha256
        or tuple(item.window_ordinal for item in audit.windows if item.accepted)
        != binding.accepted_window_ordinals
    ):
        raise RuntimeError("active Product baseline audit differs from the binding")


def _controller_profile_v021(profile: QueueProfileV021) -> QueueProfileV02:
    return QueueProfileV02(
        profile_id=profile.profile_id,
        profile_name=profile.profile_name,
        candidate_values=profile.candidate_values,
        maximum_calibration_changes=profile.maximum_calibration_changes,
        expected_default_value=profile.expected_default_value,
    )


def _resume_readiness_lifecycle_v021(
    *,
    repository_root: Path,
    binding: PilotBaselineBindingV021,
    stabilization_seconds: int,
    expected_resolved_sandbox_sha256: str,
) -> _SandboxOwnedSmokeLifecycle:
    root = repository_root.resolve(strict=True)
    private_root = _exact_bound_path_v021(
        root,
        binding.readiness_private_root,
        expected="directory",
    )
    runtime_root = _exact_bound_path_v021(
        root,
        f"{binding.readiness_private_root}/runtime",
        expected="directory",
    )
    control_root = _exact_bound_path_v021(
        root,
        f"{binding.readiness_private_root}/control",
        expected="directory",
    )
    ensure_private_directory(runtime_root)
    ensure_private_directory(control_root)
    bundle = load_bundle(
        root / "config/live-telemetry-controlled-remediation-v1"
    )
    upstream_flag = _load_object_v021(
        root / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
    )
    baseline_document, fault_document = build_flag_documents(
        upstream_flag,
        bundle,
    )
    flag_file = _exact_bound_path_v021(
        root,
        f"{binding.readiness_private_root}/{binding.queue_flag_ref}",
        expected="file",
    )
    flag_directory = flag_file.parent
    write_private_json(
        flag_file,
        baseline_document,
        create_once=True,
    )
    environment = SandboxEnvironment(
        repository_root=root,
        bundle=bundle,
        flagd_directory=flag_directory,
    )
    environment.verify_local_docker()
    environment.verify_upstream()
    resolved, raw_compose = environment.resolve()
    resolved_sha256 = authority_semantic_sha256(
        resolved.model_dump(mode="json")
    )
    if resolved_sha256 != expected_resolved_sandbox_sha256:
        raise ValueError("readiness resumed Sandbox identity differs")
    write_private_json(
        control_root / "resolved-compose.json",
        raw_compose,
        create_once=True,
    )
    environment.verify_cached_images(resolved, control_root)
    lifecycle = _SandboxOwnedSmokeLifecycle(
        repository_root=root,
        private_root=private_root,
        stabilization_seconds=stabilization_seconds,
    )
    lifecycle.bundle = bundle
    lifecycle.flag_file = flag_file
    lifecycle.baseline_document = baseline_document
    lifecycle.fault_document = fault_document
    lifecycle.environment = environment
    lifecycle.admitted_resolved_sha256 = resolved_sha256
    return lifecycle


def _run_candidate_attempt_v021(
    *,
    private_report_root: Path,
    attempt_number: int,
    value: int,
    profile: QueueProfileV021,
    traffic_profile: TrafficProfileV02,
    observation_seconds: int,
    controller: QueueFlagControllerV02,
    lifecycle_backend: LocalSandboxReadBackend,
    client: TestClient,
    settings: ProductSettingsV1,
    binding: PilotBaselineBindingV021,
    snapshot_path: Path,
    authority: PilotRuntimeAuthorityV02,
    candidate_service_ids: tuple[str, ...],
    logical_by_service_id: Mapping[str, str],
    initial_connector_health: Mapping[str, bool],
) -> dict[str, object]:
    private_attempt_binding_sha256 = semantic_sha256_v22(
        {
            "profile_contract_sha256": profile.contract_sha256,
            "baseline_binding_sha256": binding.binding_sha256,
            "value": value,
            "traffic_profile": traffic_profile.model_dump(mode="json"),
            "observation_seconds": observation_seconds,
        }
    )
    # The Product ledger receives only an opaque random correlation digest. The
    # low-entropy injected value remains bound exclusively inside private evidence.
    ledger_signature = secrets.token_hex(32)
    attempt_id = f"attempt-{secrets.token_hex(12)}"
    repository = PilotEpisodeRepositoryV02(SqliteStoreV1(settings.sqlite_path))
    ledger = _AttemptLedgerV02(
        repository,
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        signature=ledger_signature,
    )
    ledger.append(PilotAttemptStageV02.PLANNED)
    diagnosis: dict[str, Any] | None = None
    evidence_summary: dict[str, object] | None = None
    incident_id: str | None = None
    product_job_id: str | None = None
    traffic_result: dict[str, object] | None = None
    attempt_error: BaseException | None = None
    failure_domain = PilotAttemptFailureDomainV02.NONE
    flag_restored = False
    try:
        _verify_active_binding_via_api_v021(client, binding)
        runtime_services, before_drift = _runtime_services(
            lifecycle_backend,
            run_id=secrets.token_hex(16),
        )
        before_snapshot = PilotRuntimeSnapshotV02.build(
            environment_id=binding.environment_id,
            authority_sha256=authority.connector_binding_sha256,
            observed_at=datetime.now(UTC),
            services=runtime_services,
        )
        _rotate_runtime_snapshot_v021(
            snapshot_path=snapshot_path,
            snapshot=before_snapshot,
            private_report_root=(
                private_report_root / f"attempt-{attempt_number}-runtime-before"
            ),
        )
        before = verify_baseline_recovery_v02(
            environment_id=binding.environment_id,
            expected_baseline_sha256=controller.expected_baseline_sha256,
            current_flag_bytes=controller._read_bytes(),
            connector_health=initial_connector_health,
            traffic_active=False,
            owned_drift_refs=before_drift,
        )
        if before.status != "PASS":
            raise RuntimeError("pre-attempt baseline recovery verification failed")
        ledger.append(PilotAttemptStageV02.BASELINE_VERIFIED)
        started_at = datetime.now(UTC)
        with controller.activated(value):
            ledger.append(PilotAttemptStageV02.CONTROL_APPLIED)
            try:
                with httpx.Client() as traffic_client:
                    traffic = BoundedCheckoutTrafficV02(
                        client=traffic_client
                    ).run(
                        endpoint="http://127.0.0.1:18080/api/checkout",
                        profile=traffic_profile,
                    )
                traffic_result = traffic.model_dump(mode="json")
            except httpx.HTTPError as error:
                failure_domain = PilotAttemptFailureDomainV02.TRAFFIC
                raise RuntimeError("bounded checkout traffic failed") from error
            ledger.append(PilotAttemptStageV02.TRAFFIC_STOPPED)
            if observation_seconds:
                time.sleep(observation_seconds)
            ended_at = datetime.now(UTC)
            ledger.append(PilotAttemptStageV02.OBSERVATION_CAPTURED)
            incident = _request_json(
                client,
                "POST",
                "/v1/incidents",
                payload={
                    "environment_id": binding.environment_id,
                    "external_incident_key": (
                        f"successor-observation-{secrets.token_hex(12)}"
                    ),
                    "alert_name": "checkout-observer-signal",
                    "summary": (
                        "Observer-visible checkout degradation in a bounded local window."
                    ),
                    "started_at": started_at.isoformat(),
                    "ended_at": ended_at.isoformat(),
                    "candidate_service_ids": candidate_service_ids,
                    "labels": {"observation": "bounded-local-successor"},
                },
            )
            incident_id = str(incident["incident_id"])
            product_job_id, diagnosis = _run_product_job(
                client,
                settings,
                path=f"/v1/incidents/{incident_id}/diagnosis-jobs",
                worker_id=f"product-v021-calibration-{attempt_number}",
            )
            ledger.append(PilotAttemptStageV02.DIAGNOSIS_PERSISTED)
            evidence = _request_json(
                client,
                "GET",
                f"/v1/incidents/{incident_id}/evidence",
            )
            evidence_summary = _summarize_calibration_evidence_v021(
                diagnosis,
                evidence,
                injected_value=value,
            )
    except BaseException as error:
        attempt_error = error
        if failure_domain is PilotAttemptFailureDomainV02.NONE:
            message = str(error).casefold()
            failure_domain = (
                PilotAttemptFailureDomainV02.CONNECTOR
                if "connector" in message or "runtime" in message
                else PilotAttemptFailureDomainV02.PRODUCT
            )
    finally:
        try:
            if hashlib.sha256(controller._read_bytes()).hexdigest() != (
                controller.expected_baseline_sha256
            ):
                controller.restore()
            flag_restored = (
                hashlib.sha256(controller._read_bytes()).hexdigest()
                == controller.expected_baseline_sha256
            )
        except BaseException as error:
            if attempt_error is None:
                attempt_error = error
            failure_domain = PilotAttemptFailureDomainV02.CLEANUP
        ledger.append(
            PilotAttemptStageV02.FLAG_RESTORED
            if flag_restored
            else PilotAttemptStageV02.FLAG_RESTORE_BLOCKED
        )

    recovery_error: BaseException | None = None
    recovery_connector_health: dict[str, bool]
    owned_drift: tuple[str, ...]
    observed_flag_bytes = b""
    try:
        if not flag_restored:
            raise RuntimeError("queue flag exact restoration failed")
        runtime_services, owned_drift = _runtime_services(
            lifecycle_backend,
            run_id=secrets.token_hex(16),
        )
        after_snapshot = PilotRuntimeSnapshotV02.build(
            environment_id=binding.environment_id,
            authority_sha256=authority.connector_binding_sha256,
            observed_at=datetime.now(UTC),
            services=runtime_services,
        )
        _rotate_runtime_snapshot_v021(
            snapshot_path=snapshot_path,
            snapshot=after_snapshot,
            private_report_root=(
                private_report_root / f"attempt-{attempt_number}-runtime-after"
            ),
        )
        _, verification = _run_product_job(
            client,
            settings,
            path=f"/v1/environments/{binding.environment_id}/verify-jobs",
            worker_id=f"product-v021-calibration-recovery-{attempt_number}",
        )
        recovery_connector_health = _connector_health(verification)
        _verify_active_binding_via_api_v021(client, binding)
        observed_flag_bytes = controller._read_bytes()
    except BaseException as error:
        recovery_error = error
        recovery_connector_health = {"verification": False}
        owned_drift = ("runtime-or-restoration-verification-failed",)
    recovery = verify_baseline_recovery_v02(
        environment_id=binding.environment_id,
        expected_baseline_sha256=controller.expected_baseline_sha256,
        current_flag_bytes=observed_flag_bytes,
        connector_health=recovery_connector_health,
        traffic_active=False,
        owned_drift_refs=owned_drift,
    )
    if flag_restored and recovery.status == "PASS":
        ledger.append(PilotAttemptStageV02.RECOVERY_VERIFIED)
        ledger.append(PilotAttemptStageV02.CLEANUP_CLEAN)
        cleanup_status = "CLEAN"
    else:
        ledger.append(PilotAttemptStageV02.CLEANUP_BLOCKED)
        cleanup_status = "BLOCKED"

    root_ids = diagnosis.get("root_service_ids") if diagnosis is not None else None
    logical_roots = (
        tuple(
            logical_by_service_id.get(str(item), "")
            for item in root_ids
        )
        if isinstance(root_ids, list)
        else ()
    )
    logical_roots = tuple(item for item in logical_roots if item)
    terminal = (
        _classify_calibration_attempt_v021(
            diagnosis,
            evidence_summary,
            logical_root_services=logical_roots,
        )
        if diagnosis is not None and evidence_summary is not None
        else PilotEpisodeTerminalV02.DIAGNOSIS_FAILED
    )
    observed_root_service = (
        "checkout" if terminal is PilotEpisodeTerminalV02.PASS else None
    )
    if recovery.status != "PASS":
        terminal = PilotEpisodeTerminalV02.BASELINE_NOT_RESTORED
        observed_root_service = None
    if terminal is PilotEpisodeTerminalV02.PASS and attempt_error is not None:
        terminal = PilotEpisodeTerminalV02.DIAGNOSIS_FAILED
        observed_root_service = None
    if terminal is not PilotEpisodeTerminalV02.PASS and failure_domain is (
        PilotAttemptFailureDomainV02.NONE
    ):
        failure_domain = PilotAttemptFailureDomainV02.SEMANTIC
    ledger.append(
        PilotAttemptStageV02.FINALIZED,
        failure_domain=failure_domain,
        usable_fault_observation=(
            evidence_summary is not None
            and evidence_summary.get("queue_log_observed") is True
        ),
        diagnosis_result_exists=diagnosis is not None,
        flag_restored=recovery.baseline_restored,
        cleanup_status=cleanup_status,
        episode_terminal=terminal,
    )
    result: dict[str, object] = {
        "attempt_id": attempt_id,
        "attempt_number": attempt_number,
        "attempt_signature_sha256": ledger_signature,
        "private_attempt_binding_sha256": private_attempt_binding_sha256,
        "injected_value": value,
        "traffic_result": traffic_result,
        "incident_id": incident_id,
        "product_job_id": product_job_id,
        "diagnosis": diagnosis,
        "evidence_summary": evidence_summary,
        "baseline_recovery": recovery.model_dump(mode="json"),
        "active_baseline_id": binding.baseline_id,
        "active_baseline_sha256": binding.baseline_sha256,
        "episode_terminal": terminal.value,
        "observed_root_service": observed_root_service,
        "failure_domain": failure_domain.value,
        "safe_error_type": (
            None if attempt_error is None else type(attempt_error).__name__
        ),
        "safe_error": None if attempt_error is None else str(attempt_error)[:1000],
        "recovery_error_type": (
            None if recovery_error is None else type(recovery_error).__name__
        ),
    }
    write_private_bound_json_v021(
        private_report_root / f"calibration-attempt-{attempt_number}.json",
        result,
    )
    return result


def _build_public_calibration_payload_v021(
    *,
    terminal: str,
    observed_at: str,
    attempt_results: Sequence[Mapping[str, object]],
    selected_root_service: str | None,
    selected_profile_sha256: str | None,
    private_report_sha256: str,
    baseline_binding_sha256: str,
    owned_demo_cleanup: str,
    outer_baseline_restored: bool,
    active_baseline_unchanged: bool,
) -> dict[str, object]:
    normalized_attempts: list[dict[str, object]] = []
    for item in attempt_results:
        diagnosis = item.get("diagnosis")
        diagnosis_payload = diagnosis if isinstance(diagnosis, dict) else {}
        evidence = item.get("evidence_summary")
        evidence_payload = evidence if isinstance(evidence, dict) else {}
        recovery = item.get("baseline_recovery")
        recovery_payload = recovery if isinstance(recovery, dict) else {}
        normalized_attempts.append(
            {
                "episode_terminal": item.get("episode_terminal"),
                "diagnosis_terminal": diagnosis_payload.get("terminal"),
                "support_sources": evidence_payload.get("support_sources", ()),
                "queue_log_observed": evidence_payload.get(
                    "queue_log_observed", False
                ),
                "corroborating_source_available": evidence_payload.get(
                    "corroborating_source_available", False
                ),
                "runtime_root_coverage": evidence_payload.get(
                    "runtime_root_coverage", False
                ),
                "evidence_refs_resolve": evidence_payload.get(
                    "evidence_refs_resolve", False
                ),
                "provisional_report_valid": evidence_payload.get(
                    "provisional_report_valid", False
                ),
                "truth_isolation_pass": evidence_payload.get(
                    "truth_isolation_pass", False
                ),
                "baseline_recovery": recovery_payload.get("status", "FAIL"),
            }
        )
    iteration_count = len(normalized_attempts)
    payload: dict[str, object] = {
        "schema_version": "ecomsre.product.profile-calibration.v021",
        "terminal": terminal,
        "observed_at": observed_at,
        "calibration_execution_count": 1,
        "calibration_iteration_count": iteration_count,
        "changed_calibration_iteration_count": max(0, iteration_count - 1),
        "selected_root_service": selected_root_service,
        "selected_profile_sha256": selected_profile_sha256,
        "private_report_sha256": private_report_sha256,
        "baseline_binding_sha256": baseline_binding_sha256,
        "attempts": normalized_attempts,
        "active_baseline_unchanged": active_baseline_unchanged,
        "outer_baseline_restored": outer_baseline_restored,
        "baseline_restoration": (
            outer_baseline_restored
            and active_baseline_unchanged
            and bool(normalized_attempts)
            and all(
                item.get("baseline_recovery") == "PASS"
                for item in normalized_attempts
            )
        ),
        "owned_demo_cleanup": owned_demo_cleanup,
        "fault_attempt_count": 0,
        "action_authority": "NONE",
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    payload["report_sha256"] = semantic_sha256_v22(payload)
    return payload


def _write_create_once_public_text_v021(path: Path, content: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ValueError("calibration public output parent differs")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(destination, flags, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _write_public_calibration_v021(
    *,
    repository_root: Path,
    payload: Mapping[str, object],
) -> None:
    analysis_root = repository_root / "docs/analysis"
    _write_create_once_public_text_v021(
        analysis_root / "product-v021-profile-calibration.json",
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )
    _write_create_once_public_text_v021(
        analysis_root / "product-v021-profile-calibration.md",
        render_public_calibration_markdown_v021(payload),
    )


def _run_admitted_calibration_v021(
    *,
    repository_root: Path,
    stabilization_seconds: int,
    observation_seconds: int,
) -> dict[str, object]:
    root = Path(repository_root).resolve(strict=True)
    if not 0 <= stabilization_seconds <= 120:
        raise ValueError("stabilization seconds must be between 0 and 120")
    if not 15 <= observation_seconds <= 120:
        raise ValueError("observation seconds must be between 15 and 120")
    profile_path = root / "config/product-v021/live-pilot/profile.json"
    profile = QueueProfileV021.model_validate_json(
        _read_regular_bytes_v021(profile_path)
    )
    if profile.selected_value is not None or profile.profile_sha256 is not None:
        raise ValueError("v0.2.1 calibration profile is already frozen")
    campaign = _load_object_v021(
        root / "config/product-v021/live-pilot/campaign.json"
    )
    _verify_campaign_contract_v021(campaign)
    traffic_raw = campaign.get("traffic_profiles")
    if not isinstance(traffic_raw, dict):
        raise ValueError("v0.2.1 calibration traffic contract differs")
    traffic_profile = TrafficProfileV02.model_validate(
        traffic_raw.get("CALIBRATION")
    )
    binding_path = root / "config/product-v021/live-pilot/baseline-binding.json"
    binding = load_pilot_baseline_binding_v021(binding_path)
    _verify_frozen_product_state_v021(root, binding_path=binding_path)
    calibration_root = root / ".local/product-v021/private-live-control"
    campaign_sentinel = calibration_root / "calibration-start.json"
    if campaign_sentinel.exists():
        raise ValueError("v0.2.1 calibration campaign was already started")
    public_paths = (
        root / "docs/analysis/product-v021-profile-calibration.json",
        root / "docs/analysis/product-v021-profile-calibration.md",
    )
    _require_absent_public_targets_v021(root, public_paths)
    prior_progress = _load_exact_repository_object_v021(
        root,
        "docs/analysis/product-v021-progress.json",
    )

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(4)
    private_root = (calibration_root / run_id).resolve()
    product_data_root = _exact_bound_path_v021(
        root,
        binding.product_data_root,
        expected="directory",
    )
    if not private_root.is_relative_to(root):
        raise ValueError("v0.2.1 calibration roots escape the repository")
    authority_path = product_data_root / "pilot/runtime-authority.json"
    authority = load_pilot_runtime_authority_v02(authority_path)
    resolved_sandbox_sha256 = authority.read_authority.resolved_sandbox_sha256
    if not isinstance(resolved_sandbox_sha256, str):
        raise ValueError("readiness Runtime authority lacks owned Sandbox identity")
    lifecycle = _resume_readiness_lifecycle_v021(
        repository_root=root,
        binding=binding,
        stabilization_seconds=stabilization_seconds,
        expected_resolved_sandbox_sha256=resolved_sandbox_sha256,
    )

    # Resumed admission and default verification are pre-mutation. A failure here
    # does not create the one-shot sentinel or consume a calibration execution.
    if lifecycle.flag_file is None:
        raise RuntimeError("owned lifecycle did not bind the private flag file")
    queue_before = verify_queue_default_v021(
        lifecycle.flag_file,
        expected_default_value=profile.expected_default_value,
    )
    controller = QueueFlagControllerV02(
        runtime_path=lifecycle.flag_file,
        profile=_controller_profile_v021(profile),
        expected_baseline_sha256=queue_before.before_sha256,
    )
    calibration_start_sha256 = write_private_bound_json_v021(
        campaign_sentinel,
        {
            "schema_version": "ecomsre.product.calibration-start.v021",
            "run_id": run_id,
            "started_at": datetime.now(UTC).isoformat(),
            "profile_contract_sha256": profile.contract_sha256,
            "baseline_binding_sha256": binding.binding_sha256,
            "traffic_profile_sha256": semantic_sha256_v22(
                traffic_profile.model_dump(mode="json")
            ),
            "maximum_attempts": 3,
            "maximum_changed_iterations": 2,
            "action_authority": "NONE",
        },
    )

    attempt_results: list[dict[str, object]] = []
    selected_value: int | None = None
    selected_root: str | None = None
    cleanup_status = "NOT_ATTEMPTED"
    lifecycle_failure: BaseException | None = None
    failure_before_cleanup_sha256: str | None = None
    outer_baseline_restored = False
    active_baseline_unchanged = False
    runtime_rotation_sha256: str | None = None
    try:
        lifecycle.start()
        lifecycle.wait_ready()
        backend = cast(LocalSandboxReadBackend, lifecycle.authorize_reads())
        rebuilt_authority = PilotRuntimeAuthorityV02.build(
            environment_id=binding.environment_id,
            allowed_logical_services=authority.allowed_logical_services,
            profile_sha256=authority.profile_sha256,
            **_authority_inputs(backend),
        )
        if (
            rebuilt_authority != authority
            or authority.connector_binding_sha256
            != binding.runtime_authority_sha256
            or authority.allowed_logical_services != ("checkout",)
        ):
            raise RuntimeError("owned Runtime authority differs from readiness")
        runtime_services, runtime_drift = _runtime_services(
            backend,
            run_id=secrets.token_hex(16),
        )
        if runtime_drift:
            raise RuntimeError("calibration Runtime baseline contains owned drift")
        snapshot_path = product_data_root / binding.runtime_snapshot_ref
        snapshot = PilotRuntimeSnapshotV02.build(
            environment_id=binding.environment_id,
            authority_sha256=authority.connector_binding_sha256,
            observed_at=datetime.now(UTC),
            services=runtime_services,
        )
        runtime_rotation_sha256 = _rotate_runtime_snapshot_v021(
            snapshot_path=snapshot_path,
            snapshot=snapshot,
            private_report_root=private_root / "report/runtime-start",
        )
        settings = ProductSettingsV1(
            data_root=product_data_root,
            pilot_runtime_authority_path=authority_path,
            connector_timeout_seconds=15,
            job_lease_seconds=900,
        )
        with TestClient(create_app(settings)) as client:
            ready = _request_json(client, "GET", "/readyz")
            if ready.get("status") != "ready":
                raise RuntimeError("in-process Product API is not ready")
            _verify_active_binding_via_api_v021(client, binding)
            _, verification = _run_product_job(
                client,
                settings,
                path=f"/v1/environments/{binding.environment_id}/verify-jobs",
                worker_id="product-v021-calibration-verify",
            )
            connector_health = _connector_health(verification)
            candidate_service_ids, logical_by_service_id = (
                _candidate_service_binding(verification)
            )
            identity = verification.get("service_identity_map")
            if (
                not isinstance(identity, dict)
                or identity.get("identity_sha256")
                != binding.service_identity_map_sha256
            ):
                raise RuntimeError("Product service identity differs from readiness")
            for attempt_number, value in enumerate(profile.candidate_values, 1):
                attempt = _run_candidate_attempt_v021(
                    private_report_root=private_root / "report",
                    attempt_number=attempt_number,
                    value=value,
                    profile=profile,
                    traffic_profile=traffic_profile,
                    observation_seconds=observation_seconds,
                    controller=controller,
                    lifecycle_backend=backend,
                    client=client,
                    settings=settings,
                    binding=binding,
                    snapshot_path=snapshot_path,
                    authority=authority,
                    candidate_service_ids=candidate_service_ids,
                    logical_by_service_id=logical_by_service_id,
                    initial_connector_health=connector_health,
                )
                attempt_results.append(attempt)
                if (
                    attempt.get("episode_terminal")
                    == PilotEpisodeTerminalV02.PASS.value
                ):
                    selected_value = value
                    selected_root = str(attempt["observed_root_service"])
                    break
                if not _should_continue_calibration_v021(
                    attempt.get("episode_terminal")
                ):
                    break
            _verify_active_binding_via_api_v021(client, binding)
        _verify_frozen_product_state_v021(root, binding_path=binding_path)
        active_baseline_unchanged = True
        verify_queue_default_v021(
            lifecycle.flag_file,
            expected_default_value=profile.expected_default_value,
            expected_sha256=queue_before.before_sha256,
        )
        outer_baseline_restored = True
    except BaseException as error:
        lifecycle_failure = error
        try:
            failure_before_cleanup_sha256 = write_private_bound_json_v021(
                private_root / "report/failure-before-cleanup.json",
                {
                    "schema_version": (
                        "ecomsre.product.calibration-failure-before-cleanup.v021"
                    ),
                    "run_id": run_id,
                    "failed_at": datetime.now(UTC).isoformat(),
                    "safe_error_type": type(error).__name__,
                    "safe_error": str(error)[:1000],
                    "attempt_count": len(attempt_results),
                    "cleanup_started": False,
                },
            )
        except BaseException as evidence_error:
            lifecycle_failure = evidence_error
        try:
            controller.restore()
            verify_queue_default_v021(
                lifecycle.flag_file,
                expected_default_value=profile.expected_default_value,
                expected_sha256=queue_before.before_sha256,
            )
            outer_baseline_restored = True
        except BaseException:
            outer_baseline_restored = False
        try:
            _verify_frozen_product_state_v021(root, binding_path=binding_path)
            active_baseline_unchanged = True
        except BaseException:
            active_baseline_unchanged = False
    finally:
        try:
            cleanup = lifecycle.cleanup_owned(
                baseline_unchanged=outer_baseline_restored
            )
            cleanup_status = cleanup.verdict
            if cleanup_status != "CLEAN" and lifecycle_failure is None:
                lifecycle_failure = RuntimeError(
                    "owned Demo cleanup did not close CLEAN"
                )
        except BaseException as error:
            cleanup_status = "BLOCKED"
            if lifecycle_failure is None:
                lifecycle_failure = error

    observed_at = datetime.now(UTC)
    terminal = (
        CALIBRATION_PASS_V021
        if selected_value is not None
        and selected_root == "checkout"
        and 1 <= len(attempt_results) <= 3
        and max(0, len(attempt_results) - 1) <= 2
        and lifecycle_failure is None
        and outer_baseline_restored
        and active_baseline_unchanged
        and cleanup_status == "CLEAN"
        else CALIBRATION_BLOCKED_V021
    )
    private_payload: dict[str, object] = {
        "schema_version": "ecomsre.product.private-profile-calibration.v021",
        "terminal": terminal,
        "observed_at": observed_at.isoformat(),
        "run_id": run_id,
        "environment_id": binding.environment_id,
        "profile_name": profile.profile_name,
        "profile_contract_sha256": profile.contract_sha256,
        "baseline_binding_sha256": binding.binding_sha256,
        "baseline_id": binding.baseline_id,
        "baseline_sha256": binding.baseline_sha256,
        "runtime_authority_sha256": binding.runtime_authority_sha256,
        "runtime_rotation_sha256": runtime_rotation_sha256,
        "calibration_start_sha256": calibration_start_sha256,
        "selected_value": selected_value,
        "selected_root_service": selected_root,
        "calibration_iteration_count": len(attempt_results),
        "changed_calibration_iteration_count": max(
            0, len(attempt_results) - 1
        ),
        "attempts": attempt_results,
        "active_baseline_unchanged": active_baseline_unchanged,
        "outer_baseline_restored": outer_baseline_restored,
        "owned_demo_cleanup": cleanup_status,
        "failure_before_cleanup_sha256": failure_before_cleanup_sha256,
        "safe_failure_type": (
            None if lifecycle_failure is None else type(lifecycle_failure).__name__
        ),
        "safe_failure": (
            None if lifecycle_failure is None else str(lifecycle_failure)[:1000]
        ),
        "fault_attempt_count": 0,
        "action_authority": "NONE",
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    private_report_sha256 = write_private_bound_json_v021(
        private_root / "report/product-v021-profile-calibration-private.json",
        private_payload,
    )
    selected_profile_sha256: str | None = None
    frozen_profile: QueueProfileV021 | None = None
    if terminal == CALIBRATION_PASS_V021:
        assert selected_value is not None and selected_root is not None
        frozen_profile = profile.freeze(
            selected_value=selected_value,
            selected_root_service=selected_root,
            calibration_report_sha256=private_report_sha256,
            calibration_runtime_binding_sha256=binding.runtime_authority_sha256,
            calibrated_at=observed_at,
        )
        selected_profile_sha256 = frozen_profile.profile_sha256
    public_payload = _build_public_calibration_payload_v021(
        terminal=terminal,
        observed_at=observed_at.isoformat(),
        attempt_results=attempt_results,
        selected_root_service=selected_root,
        selected_profile_sha256=selected_profile_sha256,
        private_report_sha256=private_report_sha256,
        baseline_binding_sha256=binding.binding_sha256,
        owned_demo_cleanup=cleanup_status,
        outer_baseline_restored=outer_baseline_restored,
        active_baseline_unchanged=active_baseline_unchanged,
    )
    _write_public_calibration_v021(
        repository_root=root,
        payload=public_payload,
    )
    if frozen_profile is not None:
        _atomic_replace_text_v021(
            profile_path,
            frozen_profile.model_dump_json(indent=2) + "\n",
        )
    progress: dict[str, object] = {
        "schema_version": "ecomsre.product.v021.progress.v1",
        "goal_version": (
            "ecomsre-product-v021-live-baseline-knowledge-loop-successor-v1"
        ),
        "branch": "codex/product-v021-baseline-readiness-successor",
        "increment": 2,
        "terminal": terminal,
        "baseline_readiness_attempt_count": prior_progress.get(
            "baseline_readiness_attempt_count", 0
        ),
        "baseline_readiness_run_count": prior_progress.get(
            "baseline_readiness_run_count", 0
        ),
        "infrastructure_replacement_count": prior_progress.get(
            "infrastructure_replacement_count", 0
        ),
        "profile_calibration_iteration_count": len(attempt_results),
        "profile_calibration_changed_iteration_count": max(
            0, len(attempt_results) - 1
        ),
        "calibration_execution_count": 1,
        "fault_attempt_count": 0,
        "accepted_positive_episode_count": 0,
        "heldout_recurrence_count": 0,
        "current_human_gate": "NOT_REACHED",
        "next_boundary": (
            "INDEPENDENT_PRE_CAMPAIGN_REVIEW"
            if terminal == CALIBRATION_PASS_V021
            else "STOPPED_UNKNOWN_FAULT_PROFILE"
        ),
        "action_authority": "NONE",
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    progress["progress_sha256"] = semantic_sha256_v22(progress)
    _atomic_replace_text_v021(
        root / "docs/analysis/product-v021-progress.json",
        json.dumps(progress, indent=2, sort_keys=True) + "\n",
    )
    return {
        "terminal": terminal,
        "calibration_execution_count": 1,
        "calibration_iteration_count": len(attempt_results),
        "changed_calibration_iteration_count": max(
            0, len(attempt_results) - 1
        ),
        "selected_root_service": selected_root,
        "selected_profile_sha256": selected_profile_sha256,
        "private_report_sha256": private_report_sha256,
        "baseline_binding_sha256": binding.binding_sha256,
        "active_baseline_unchanged": active_baseline_unchanged,
        "outer_baseline_restored": outer_baseline_restored,
        "owned_demo_cleanup": cleanup_status,
        "fault_attempt_count": 0,
        "action_authority": "NONE",
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }


def run_live_calibration_v021(
    *,
    repository_root: Path,
    stabilization_seconds: int = 30,
    observation_seconds: int = 30,
) -> dict[str, object]:
    contract = verify_calibration_contract_v021(repository_root)
    if contract.get("terminal") != CALIBRATION_CONTRACT_READY_V021:
        return contract
    return _run_admitted_calibration_v021(
        repository_root=repository_root,
        stabilization_seconds=stabilization_seconds,
        observation_seconds=observation_seconds,
    )


__all__ = (
    "CALIBRATION_BLOCKED_V021",
    "CALIBRATION_CONTRACT_READY_V021",
    "CALIBRATION_PASS_V021",
    "READINESS_BLOCKED_V021",
    "run_live_calibration_v021",
    "verify_calibration_contract_v021",
)
