"""Fail-closed Product v0.2.1 readiness attempt ledger contracts."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Literal, Mapping

from pydantic import Field, ValidationInfo, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.pilot.baseline_audit_v021 import BaselineReadinessAuditV021
from ecomsre.product.pilot.baseline_readiness_v021 import (
    HealthyTrafficRunResultV021,
    ReadinessAttemptDispositionV021,
    ReadinessAttemptSignatureV021,
    ReadinessChangeParameterV021,
    ReadinessFailureDomainV021,
    ReadinessSemanticInputsV021,
)
from ecomsre_live_sandbox.contracts import (
    ensure_private_directory,
    verify_private_tree_permissions,
)


READINESS_PASS_V021 = "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_PASS"
READINESS_BLOCKED_V021 = "BLOCKED_ECOMSRE_PRODUCT_V021_BASELINE_READINESS"
READINESS_REPAIR_REQUIRED_V021 = (
    "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_REPAIR_REQUIRED"
)


def _seal_payload_v021(payload: Mapping[str, object]) -> dict[str, object]:
    body = dict(payload)
    body["report_sha256"] = semantic_sha256_v22(body)
    return body


def _build_bound_model_v021(
    model_type: type[ReadinessAttemptStartV021]
    | type[ReadinessAttemptFinalV021]
    | type[PublicReadinessAttemptV021],
    payload: Mapping[str, object],
) -> ReadinessAttemptStartV021 | ReadinessAttemptFinalV021 | PublicReadinessAttemptV021:
    draft = model_type.model_validate(
        {**payload, "report_sha256": "0" * 64},
        context={"skip_report_digest": True},
    )
    normalized = draft.model_dump(mode="json", exclude={"report_sha256"})
    return model_type.model_validate(
        {**normalized, "report_sha256": semantic_sha256_v22(normalized)}
    )


def _read_regular_bytes_v021(path: Path, *, private: bool) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"bound JSON is not a regular file: {path}")
        if private and metadata.st_mode & 0o077:
            raise PermissionError(f"private JSON permissions are too broad: {path}")
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def _load_bound_object_v021(path: Path, *, private: bool) -> dict[str, Any]:
    payload = json.loads(_read_regular_bytes_v021(path, private=private))
    if not isinstance(payload, dict):
        raise ValueError(f"bound JSON is not an object: {path}")
    supplied = payload.get("report_sha256")
    body = dict(payload)
    body.pop("report_sha256", None)
    if supplied != semantic_sha256_v22(body):
        raise ValueError(f"bound JSON digest differs: {path}")
    return payload


def _write_create_once_bound_json_v021(
    path: Path,
    payload: Mapping[str, object],
    *,
    private: bool,
) -> str:
    body = _seal_payload_v021(payload)
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    if private:
        ensure_private_directory(path.parent)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.parent.is_symlink() or not path.parent.is_dir():
            raise ValueError("public readiness output parent is not a regular directory")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600 if private else 0o644)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    return str(body["report_sha256"])


class ReadinessAttemptStartV021(ProductModelV1):
    schema_version: Literal["ecomsre.product.readiness-attempt-start.v021"] = (
        "ecomsre.product.readiness-attempt-start.v021"
    )
    run_number: int = Field(ge=1, le=3)
    changed_attempt_number: int = Field(ge=1, le=2)
    run_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")
    started_at: datetime
    signature: ReadinessAttemptSignatureV021
    infrastructure_replacement_for_run_id: str | None = Field(
        default=None,
        pattern=r"^[a-zA-Z0-9_.-]{1,120}$",
    )
    maximum_changed_attempts: Literal[2] = 2
    maximum_infrastructure_replacements: Literal[1] = 1
    fault_state: Literal["READ_ONLY_DEFAULT"] = "READ_ONLY_DEFAULT"
    action_authority: Literal["NONE"] = "NONE"
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_start(self, info: ValidationInfo) -> "ReadinessAttemptStartV021":
        if info.context and info.context.get("skip_report_digest") is True:
            return self
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("readiness attempt-start digest differs")
        return self


class ReadinessAttemptFinalV021(ProductModelV1):
    schema_version: Literal["ecomsre.product.readiness-attempt-final.v021"] = (
        "ecomsre.product.readiness-attempt-final.v021"
    )
    run_number: int = Field(ge=1, le=3)
    changed_attempt_number: int = Field(ge=1, le=2)
    run_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")
    attempt_signature_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed_parameter: ReadinessChangeParameterV021
    infrastructure_replacement_for_run_id: str | None = Field(
        default=None,
        pattern=r"^[a-zA-Z0-9_.-]{1,120}$",
    )
    terminal: Literal[
        "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_PASS",
        "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_REPAIR_REQUIRED",
        "BLOCKED_ECOMSRE_PRODUCT_V021_BASELINE_READINESS",
    ]
    disposition: ReadinessAttemptDispositionV021
    failure_domain: ReadinessFailureDomainV021
    audit_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rejection_reason_codes: tuple[str, ...] = ()
    scheduled_window_count: int = Field(ge=0, le=5)
    usable_audit: bool
    queue_default_unchanged: bool
    outer_baseline_restored: bool
    owned_demo_cleanup: Literal["CLEAN", "BLOCKED", "UNKNOWN_BLOCKED"]
    failure_before_cleanup_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    private_attempt_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    public_attempt_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    interrupted: bool
    action_authority: Literal["NONE"] = "NONE"
    action_authority_violations: Literal[0] = 0
    agent_writes: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_final(self, info: ValidationInfo) -> "ReadinessAttemptFinalV021":
        if info.context and info.context.get("skip_report_digest") is True:
            return self
        safe_continuation = (
            self.queue_default_unchanged
            and self.outer_baseline_restored
            and self.owned_demo_cleanup == "CLEAN"
            and self.failure_before_cleanup_sha256 is not None
            and not self.interrupted
        )
        if self.disposition is ReadinessAttemptDispositionV021.PASS:
            if (
                self.terminal != READINESS_PASS_V021
                or not self.usable_audit
                or self.failure_domain is not ReadinessFailureDomainV021.NONE
                or not self.queue_default_unchanged
                or not self.outer_baseline_restored
                or self.owned_demo_cleanup != "CLEAN"
                or self.failure_before_cleanup_sha256 is not None
            ):
                raise ValueError("readiness PASS final is inconsistent")
        elif self.disposition is ReadinessAttemptDispositionV021.TARGETED_REPAIR_ELIGIBLE:
            if (
                self.terminal != READINESS_REPAIR_REQUIRED_V021
                or not self.usable_audit
                or not safe_continuation
                or self.changed_attempt_number >= 2
                or self.audit_sha256 is None
                or not self.rejection_reason_codes
            ):
                raise ValueError("targeted readiness repair is not eligible")
        elif self.disposition is (
            ReadinessAttemptDispositionV021.INFRASTRUCTURE_REPLACEMENT_ELIGIBLE
        ):
            if (
                self.terminal != READINESS_REPAIR_REQUIRED_V021
                or self.usable_audit
                or not safe_continuation
                or self.failure_domain
                is not ReadinessFailureDomainV021.INFRASTRUCTURE_STARTUP
                or self.infrastructure_replacement_for_run_id is not None
            ):
                raise ValueError("readiness infrastructure replacement is not eligible")
        elif self.terminal != READINESS_BLOCKED_V021:
            raise ValueError("blocked readiness final terminal differs")
        if self.rejection_reason_codes != tuple(
            sorted(set(self.rejection_reason_codes))
        ):
            raise ValueError("readiness final rejection reasons are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("readiness attempt-final digest differs")
        return self


class PublicReadinessAttemptV021(ProductModelV1):
    schema_version: Literal["ecomsre.product.public-baseline-readiness-attempt.v021"] = (
        "ecomsre.product.public-baseline-readiness-attempt.v021"
    )
    run_number: int = Field(ge=1, le=3)
    changed_attempt_number: int = Field(ge=1, le=2)
    attempt_signature_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed_parameter: ReadinessChangeParameterV021
    infrastructure_replacement: bool
    terminal: Literal[
        "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_PASS",
        "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_REPAIR_REQUIRED",
        "BLOCKED_ECOMSRE_PRODUCT_V021_BASELINE_READINESS",
    ]
    disposition: ReadinessAttemptDispositionV021
    failure_domain: ReadinessFailureDomainV021
    observed_at: datetime
    environment_id: str | None
    baseline_id: str | None
    baseline_sha256: str | None
    baseline_active: bool
    audit: BaselineReadinessAuditV021 | None
    audit_sha256: str | None
    parity_sha256: str | None
    scheduled_window_count: int = Field(ge=0, le=5)
    accepted_window_count: int = Field(ge=0, le=5)
    traffic_result: HealthyTrafficRunResultV021 | None
    queue_default_unchanged: bool
    healthy_traffic_stopped: bool
    api_restart_verified: bool
    worker_restart_verified: bool
    outer_baseline_restored: bool
    owned_demo_cleanup: Literal["CLEAN", "BLOCKED", "UNKNOWN_BLOCKED"]
    baseline_job_safe_error_code: str | None
    safe_error_type: str | None
    private_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    failure_before_cleanup_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    fault_attempt_count: Literal[0] = 0
    action_authority: Literal["NONE"] = "NONE"
    action_authority_violations: Literal[0] = 0
    agent_writes: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_public_attempt(
        self,
        info: ValidationInfo,
    ) -> "PublicReadinessAttemptV021":
        if info.context and info.context.get("skip_report_digest") is True:
            return self
        if self.audit is None:
            if self.audit_sha256 is not None or self.parity_sha256 is not None:
                raise ValueError("public readiness audit references exist without an audit")
        elif (
            self.audit_sha256 != self.audit.audit_sha256
            or self.parity_sha256 != self.audit.parity_sha256
            or self.scheduled_window_count != self.audit.scheduled_window_count
            or self.accepted_window_count != self.audit.accepted_window_count
        ):
            raise ValueError("public readiness audit projection differs")
        expected_terminal = (
            READINESS_PASS_V021
            if self.disposition is ReadinessAttemptDispositionV021.PASS
            else (
                READINESS_REPAIR_REQUIRED_V021
                if self.disposition
                in {
                    ReadinessAttemptDispositionV021.TARGETED_REPAIR_ELIGIBLE,
                    ReadinessAttemptDispositionV021.INFRASTRUCTURE_REPLACEMENT_ELIGIBLE,
                }
                else READINESS_BLOCKED_V021
            )
        )
        if self.terminal != expected_terminal:
            raise ValueError("public readiness disposition terminal differs")
        safe_continuation = (
            self.failure_before_cleanup_sha256 is not None
            and self.queue_default_unchanged
            and self.outer_baseline_restored
            and self.owned_demo_cleanup == "CLEAN"
        )
        rejection_reason_codes = (
            {
                reason
                for window in self.audit.windows
                for reason in window.rejection_reason_codes
            }
            if self.audit is not None
            else set()
        )
        if self.disposition is (
            ReadinessAttemptDispositionV021.TARGETED_REPAIR_ELIGIBLE
        ):
            if (
                self.audit is None
                or self.audit.scheduled_window_count != 5
                or self.audit.configured_window_count != 5
                or self.audit.final_builder_would_pass
                or not rejection_reason_codes
                or not safe_continuation
                or self.failure_domain is not ReadinessFailureDomainV021.CAMPAIGN
                or self.changed_attempt_number >= 2
            ):
                raise ValueError("public targeted readiness repair is not eligible")
        elif self.disposition is (
            ReadinessAttemptDispositionV021.INFRASTRUCTURE_REPLACEMENT_ELIGIBLE
        ):
            if (
                self.audit is not None
                or self.scheduled_window_count != 0
                or self.accepted_window_count != 0
                or self.environment_id is not None
                or self.baseline_id is not None
                or self.baseline_sha256 is not None
                or self.baseline_active
                or self.traffic_result is not None
                or self.healthy_traffic_stopped
                or self.api_restart_verified
                or self.worker_restart_verified
                or not safe_continuation
                or self.failure_domain
                is not ReadinessFailureDomainV021.INFRASTRUCTURE_STARTUP
                or self.infrastructure_replacement
            ):
                raise ValueError(
                    "public readiness infrastructure replacement is not eligible"
                )
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("public readiness attempt digest differs")
        return self


def _load_starts_and_finals_v021(
    private_root: Path,
) -> tuple[tuple[ReadinessAttemptStartV021, ...], tuple[ReadinessAttemptFinalV021, ...]]:
    attempts_root = private_root / "attempts"
    ensure_private_directory(attempts_root)
    verify_private_tree_permissions(attempts_root)
    starts: list[ReadinessAttemptStartV021] = []
    finals: list[ReadinessAttemptFinalV021] = []
    entries = tuple(sorted(attempts_root.iterdir(), key=lambda item: item.name))
    allowed = re.compile(r"run-([1-3])-(start|final)\.json")
    if any(allowed.fullmatch(path.name) is None for path in entries):
        raise ValueError("readiness attempt ledger contains an unknown entry")
    for run_number in range(1, 4):
        start_path = attempts_root / f"run-{run_number}-start.json"
        final_path = attempts_root / f"run-{run_number}-final.json"
        if start_path.exists():
            starts.append(
                ReadinessAttemptStartV021.model_validate(
                    _load_bound_object_v021(start_path, private=True)
                )
            )
            if starts[-1].run_number != run_number:
                raise ValueError("readiness attempt-start order differs")
            if final_path.exists():
                finals.append(
                    ReadinessAttemptFinalV021.model_validate(
                        _load_bound_object_v021(final_path, private=True)
                    )
                )
                if (
                    finals[-1].run_number != run_number
                    or finals[-1].run_id != starts[-1].run_id
                    or finals[-1].changed_attempt_number
                    != starts[-1].changed_attempt_number
                    or finals[-1].attempt_signature_sha256
                    != starts[-1].signature.attempt_signature_sha256
                    or finals[-1].changed_parameter
                    is not starts[-1].signature.changed_parameter
                    or finals[-1].infrastructure_replacement_for_run_id
                    != starts[-1].infrastructure_replacement_for_run_id
                ):
                    raise ValueError("readiness attempt start/final binding differs")
            elif run_number != len(entries) + 1:
                pass
        elif final_path.exists():
            raise ValueError("readiness attempt final exists without a start")
    if tuple(item.run_number for item in starts) != tuple(range(1, len(starts) + 1)):
        raise ValueError("readiness attempt-start sequence is not contiguous")
    return tuple(starts), tuple(finals)


_ALLOWED_SEMANTIC_CHANGES_V021: dict[ReadinessChangeParameterV021, frozenset[str]] = {
    ReadinessChangeParameterV021.HEALTHY_TRAFFIC_DURATION: frozenset(
        {"healthy_traffic_maximum_request_count"}
    ),
    ReadinessChangeParameterV021.HEALTHY_TRAFFIC_RATE: frozenset(
        {"healthy_traffic_requests_per_second"}
    ),
    ReadinessChangeParameterV021.OTEL_STABILIZATION_DURATION: frozenset(
        {"stabilization_seconds"}
    ),
    ReadinessChangeParameterV021.BASELINE_ACCUMULATION_DURATION: frozenset(
        {"baseline_accumulation_seconds"}
    ),
    ReadinessChangeParameterV021.BASELINE_MINIMUM_SUCCESSFUL_WINDOWS: frozenset(
        {"minimum_successful_windows"}
    ),
    ReadinessChangeParameterV021.CONNECTOR_QUERY_TEMPLATE: frozenset(
        {"connector_query_bindings_sha256", "connector_query_templates_sha256"}
    ),
    ReadinessChangeParameterV021.SERVICE_ALIAS_MAPPING: frozenset(
        {"service_alias_mapping_sha256"}
    ),
    ReadinessChangeParameterV021.TARGET_COMPLETE_CAPABILITY_DECLARATION: frozenset(
        {"required_source_policy_id"}
    ),
}


def _semantic_changed_fields_v021(
    before: ReadinessSemanticInputsV021,
    after: ReadinessSemanticInputsV021,
) -> frozenset[str]:
    ignored = {"schema_version", "profile_sha256", "semantics_sha256"}
    left = before.model_dump(mode="json")
    right = after.model_dump(mode="json")
    return frozenset(
        key for key in left if key not in ignored and left.get(key) != right.get(key)
    )


def reserve_readiness_attempt_v021(
    *,
    private_root: Path,
    signature: ReadinessAttemptSignatureV021,
    run_id: str,
    started_at: str,
    infrastructure_replacement_for_run_id: str | None = None,
) -> ReadinessAttemptStartV021:
    root = Path(private_root)
    ensure_private_directory(root)
    starts, finals = _load_starts_and_finals_v021(root)
    if len(finals) != len(starts):
        raise ValueError("a prior readiness run is unfinished")
    if len(starts) >= 3:
        raise ValueError("readiness run budget is exhausted")
    timestamp = datetime.fromisoformat(started_at)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("readiness attempt timestamp must be timezone-aware")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,120}", run_id):
        raise ValueError("readiness run ID is invalid")
    if not starts:
        if (
            signature.changed_parameter is not ReadinessChangeParameterV021.INITIAL
            or infrastructure_replacement_for_run_id is not None
        ):
            raise ValueError("the first readiness run must use INITIAL inputs")
        changed_attempt_number = 1
    elif infrastructure_replacement_for_run_id is not None:
        last_start = starts[-1]
        last_final = finals[-1]
        if (
            infrastructure_replacement_for_run_id != last_start.run_id
            or last_final.disposition
            is not ReadinessAttemptDispositionV021.INFRASTRUCTURE_REPLACEMENT_ELIGIBLE
            or any(item.infrastructure_replacement_for_run_id is not None for item in starts)
            or signature != last_start.signature
        ):
            raise ValueError("readiness infrastructure replacement is not eligible")
        changed_attempt_number = last_start.changed_attempt_number
    else:
        last_start = starts[-1]
        last_final = finals[-1]
        changed_starts = tuple(
            item
            for item in starts
            if item.infrastructure_replacement_for_run_id is None
        )
        if (
            len(changed_starts) >= 2
            or last_final.disposition
            is not ReadinessAttemptDispositionV021.TARGETED_REPAIR_ELIGIBLE
            or signature.changed_parameter is ReadinessChangeParameterV021.INITIAL
            or signature.prior_audit_sha256 != last_final.audit_sha256
            or signature.prior_rejection_reason_codes
            != last_final.rejection_reason_codes
        ):
            raise ValueError("changed readiness attempt lacks eligible prior audit")
        changed_fields = _semantic_changed_fields_v021(
            last_start.signature.semantic_inputs,
            signature.semantic_inputs,
        )
        allowed = _ALLOWED_SEMANTIC_CHANGES_V021[signature.changed_parameter]
        if not changed_fields or not changed_fields.issubset(allowed):
            raise ValueError("changed readiness inputs do not match the declared parameter")
        changed_attempt_number = last_start.changed_attempt_number + 1
    run_number = len(starts) + 1
    payload = {
        "schema_version": "ecomsre.product.readiness-attempt-start.v021",
        "run_number": run_number,
        "changed_attempt_number": changed_attempt_number,
        "run_id": run_id,
        "started_at": timestamp.isoformat(),
        "signature": signature.model_dump(mode="json"),
        "infrastructure_replacement_for_run_id": (
            infrastructure_replacement_for_run_id
        ),
        "maximum_changed_attempts": 2,
        "maximum_infrastructure_replacements": 1,
        "fault_state": "READ_ONLY_DEFAULT",
        "action_authority": "NONE",
    }
    start = ReadinessAttemptStartV021.model_validate(
        _build_bound_model_v021(ReadinessAttemptStartV021, payload)
    )
    _write_create_once_bound_json_v021(
        root / "attempts" / f"run-{run_number}-start.json",
        start.model_dump(mode="json", exclude={"report_sha256"}),
        private=True,
    )
    return start


def readiness_attempt_ledger_state_v021(
    private_root: Path,
) -> tuple[tuple[ReadinessAttemptStartV021, ...], tuple[ReadinessAttemptFinalV021, ...]]:
    root = Path(private_root)
    ensure_private_directory(root)
    return _load_starts_and_finals_v021(root)


def write_private_bound_json_v021(
    path: Path,
    payload: Mapping[str, object],
) -> str:
    return _write_create_once_bound_json_v021(path, payload, private=True)


def write_public_bound_json_v021(
    path: Path,
    payload: Mapping[str, object],
) -> str:
    return _write_create_once_bound_json_v021(path, payload, private=False)


def write_readiness_attempt_final_v021(
    *,
    private_root: Path,
    payload: Mapping[str, object],
) -> ReadinessAttemptFinalV021:
    final = ReadinessAttemptFinalV021.model_validate(
        _build_bound_model_v021(ReadinessAttemptFinalV021, payload)
    )
    _write_create_once_bound_json_v021(
        Path(private_root) / "attempts" / f"run-{final.run_number}-final.json",
        final.model_dump(mode="json", exclude={"report_sha256"}),
        private=True,
    )
    return final


def write_public_readiness_attempt_v021(
    path: Path,
    payload: Mapping[str, object],
) -> PublicReadinessAttemptV021:
    attempt = PublicReadinessAttemptV021.model_validate(
        _build_bound_model_v021(PublicReadinessAttemptV021, payload)
    )
    _write_create_once_bound_json_v021(
        path,
        attempt.model_dump(mode="json", exclude={"report_sha256"}),
        private=False,
    )
    return attempt


def load_public_readiness_attempt_v021(path: Path) -> PublicReadinessAttemptV021:
    return PublicReadinessAttemptV021.model_validate(
        _load_bound_object_v021(path, private=False)
    )


__all__ = (
    "PublicReadinessAttemptV021",
    "READINESS_BLOCKED_V021",
    "READINESS_PASS_V021",
    "READINESS_REPAIR_REQUIRED_V021",
    "ReadinessAttemptFinalV021",
    "ReadinessAttemptStartV021",
    "load_public_readiness_attempt_v021",
    "readiness_attempt_ledger_state_v021",
    "reserve_readiness_attempt_v021",
    "write_public_readiness_attempt_v021",
    "write_public_bound_json_v021",
    "write_private_bound_json_v021",
    "write_readiness_attempt_final_v021",
)
