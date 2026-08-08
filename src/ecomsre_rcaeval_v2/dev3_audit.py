"""Read-only, identity-free audit of immutable v2-dev.2 Provider failures."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Literal, Mapping

from pydantic import AwareDatetime

from ecomsre_rcaeval_v2.contracts import Sha256, V2Model
from ecomsre_rcaeval_v2.dev3_provider import (
    Dev2FailureAudit,
    Dev2FailureEvidence,
    FailureClass,
    audit_dev2_failures,
)


class Dev2FailureAuditLock(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev3.failure-audit-lock.v1"]
    protocol_id: Literal["rcaeval-re2-v2-dev.3"]
    audited_at_utc: AwareDatetime
    evaluation_root_lock_sha256: Sha256
    dev2_smoke_gate_sha256: Sha256
    dev2_smoke_schedule_sha256: Sha256
    dev2_smoke_journal_tree_sha256: Sha256
    audit: Dev2FailureAudit


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit_tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    files = tuple(sorted(path for path in root.rglob("*") if path.is_file()))
    if not files or any(path.is_symlink() for path in files):
        raise ValueError("dev.2 Smoke journal tree is empty or contains a symlink")
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def _object(path: Path) -> Mapping[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("dev.2 audit source artifact is missing or invalid")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("dev.2 audit source artifact must be an object")
    return value


def _latency_bucket(seconds: float) -> str:
    if seconds < 10:
        return "<10s"
    if seconds < 30:
        return "10-30s"
    if seconds < 60:
        return "30-60s"
    if seconds < 120:
        return "60-120s"
    return "120s+"


def _hour_bucket(value: object) -> str:
    if not isinstance(value, str):
        return "UNKNOWN"
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00Z")


def _v1_operation(architecture: str, model_calls: int) -> str:
    if architecture == "single":
        return "FINAL_JUDGE"
    if architecture == "fixed":
        return {
            1: "METRICS_SPECIALIST",
            2: "LOGS_SPECIALIST",
            3: "TRACES_SPECIALIST",
            4: "FINAL_JUDGE",
        }.get(model_calls, "UNKNOWN")
    if architecture == "dynamic":
        if model_calls == 1:
            return "METRICS_SPECIALIST"
        if model_calls == 2:
            return "COMMANDER"
        if model_calls == 5:
            return "FINAL_JUDGE"
        return "DYNAMIC_FOLLOWUP_SPECIALIST"
    return "UNKNOWN"


def audit_dev2_failure_artifacts(
    *,
    smoke_schedule_path: Path,
    smoke_journal_root: Path,
    smoke_gate_path: Path,
    evaluation_root_lock_sha256: str,
) -> Dev2FailureAuditLock:
    schedule = _object(smoke_schedule_path)
    records = schedule.get("records")
    if not isinstance(records, list) or len(records) != 72:
        raise ValueError("dev.2 Smoke schedule must contain exactly 72 records")
    variants: dict[str, str] = {}
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("dev.2 Smoke schedule record is invalid")
        run_id = item.get("run_id")
        variant = item.get("variant")
        if not isinstance(run_id, str) or not isinstance(variant, str):
            raise ValueError("dev.2 Smoke schedule identity is invalid")
        variants[run_id] = variant

    gate = _object(smoke_gate_path)
    accounting = gate.get("run_accounting")
    if not isinstance(accounting, dict) or accounting.get("terminalized") != 72:
        raise ValueError("dev.2 canonical Smoke gate does not bind 72 terminals")

    failures: list[Dev2FailureEvidence] = []
    v1_paths = tuple(sorted((smoke_journal_root / "v1-terminal-records").glob("*.json")))
    v2_paths = tuple(sorted((smoke_journal_root / "v2-runs").glob("*/terminal-record.json")))
    if len(v1_paths) != 36 or len(v2_paths) != 36:
        raise ValueError("dev.2 Smoke journal does not contain the frozen 36+36 terminals")
    for path in v1_paths:
        terminal = _object(path)
        if terminal.get("terminal_status") != "PROVIDER_FAILURE":
            continue
        run_id = terminal.get("run_id")
        architecture = terminal.get("architecture")
        model_calls = terminal.get("model_calls")
        latency = terminal.get("latency_seconds")
        if (
            not isinstance(run_id, str)
            or not isinstance(architecture, str)
            or type(model_calls) is not int
            or not isinstance(latency, (int, float))
        ):
            raise ValueError("dev.2 v1 failed terminal is incomplete")
        failures.append(
            Dev2FailureEvidence(
                architecture_family="V1_REFERENCE",
                variant=variants[run_id],
                operation_type=_v1_operation(architecture, model_calls),
                operation_stage="PROVIDER_CALL",
                failure_code=str(terminal.get("failure_code")),
                safe_http_status_class=None,
                provider_attempt_index=1,
                provider_call_index=model_calls,
                latency_bucket=_latency_bucket(float(latency)),
                valid_response_received=False,
                usage_object_received=False,
                token_usage_known=False,
                timestamp_bucket="UNKNOWN",
                canonical_request_sha256=None,
            )
        )
    for path in v2_paths:
        terminal = _object(path)
        if terminal.get("terminal_status") != "PROVIDER_FAILURE":
            continue
        run_id = terminal.get("run_id")
        operation_index = terminal.get("failure_operation_index")
        operation_type = terminal.get("failure_operation_type")
        if (
            not isinstance(run_id, str)
            or type(operation_index) is not int
            or not isinstance(operation_type, str)
        ):
            raise ValueError("dev.2 v2 failed terminal is incomplete")
        operation_path = (
            path.parent
            / "operations"
            / f"{operation_index:04d}-{operation_type}.json"
        )
        operation = _object(operation_path)
        usage = operation.get("usage_delta")
        provider_call_index = operation.get("provider_call_index")
        latency_ms = operation.get("latency_ms")
        if (
            not isinstance(usage, dict)
            or type(provider_call_index) is not int
            or not isinstance(latency_ms, (int, float))
        ):
            raise ValueError("dev.2 v2 failed operation is incomplete")
        failures.append(
            Dev2FailureEvidence(
                architecture_family="V2",
                variant=variants[run_id],
                operation_type=operation_type,
                operation_stage=str(operation.get("failure_stage")),
                failure_code=str(operation.get("failure_code")),
                safe_http_status_class=None,
                provider_attempt_index=1,
                provider_call_index=provider_call_index,
                latency_bucket=_latency_bucket(float(latency_ms) / 1_000.0),
                valid_response_received=False,
                usage_object_received=False,
                token_usage_known=bool(usage.get("token_usage_known")),
                timestamp_bucket=_hour_bucket(operation.get("started_at_utc")),
                canonical_request_sha256=None,
            )
        )
    audit = audit_dev2_failures(tuple(failures))
    if (
        audit.failure_count != 5
        or audit.retry_eligible_count != 0
        or audit.failure_class_counts
        != {FailureClass.UNKNOWN_INSUFFICIENT_EVIDENCE: 5}
    ):
        raise ValueError("dev.2 failure audit differs from frozen safe disposition")
    return Dev2FailureAuditLock(
        schema_version="rcaeval-re2-v2-dev3.failure-audit-lock.v1",
        protocol_id="rcaeval-re2-v2-dev.3",
        audited_at_utc=datetime.now(timezone.utc),
        evaluation_root_lock_sha256=evaluation_root_lock_sha256,
        dev2_smoke_gate_sha256=_sha256_file(smoke_gate_path),
        dev2_smoke_schedule_sha256=_sha256_file(smoke_schedule_path),
        dev2_smoke_journal_tree_sha256=audit_tree_sha256(smoke_journal_root),
        audit=audit,
    )


def write_audit_lock_create_once(path: Path, audit: Dev2FailureAuditLock) -> None:
    payload = (
        json.dumps(
            audit.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


__all__ = [
    "Dev2FailureAuditLock",
    "audit_tree_sha256",
    "audit_dev2_failure_artifacts",
    "write_audit_lock_create_once",
]
