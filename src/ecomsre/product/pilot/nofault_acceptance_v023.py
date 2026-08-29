"""Fail-closed measured No-Fault scorer for Product v0.2.3."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.product.connectors.base import ConnectorQueryResultV1, ConnectorWindowV1
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    ACTIVE_PROFILE_SHA256_V023,
    OpenSearchConnectorDiagnosticsV023,
)
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.errors import ProductError
from ecomsre.product.incidents.contracts import (
    DiagnosisResultV1,
    DiagnosisTerminalV1,
    EvidenceBundleV1,
    IncidentRecordV1,
)
from ecomsre.product.pilot.baseline_readiness_v023 import (
    ProductBaselineReadinessAuditV023,
)
from ecomsre.product.pilot.baseline_restart_v023 import BaselineRestartProofV023


NOFAULT_FULLY_SUPPORTED_V023 = "ECOMSRE_PRODUCT_V023_NOFAULT_FULLY_SUPPORTED"
NOFAULT_CAPABILITY_LIMITED_V023 = (
    "ECOMSRE_PRODUCT_V023_NOFAULT_CAPABILITY_LIMITED"
)
NOFAULT_NOT_SUPPORTED_V023 = "ECOMSRE_PRODUCT_V023_NOFAULT_NOT_SUPPORTED"
_REQUIRED_NOFAULT_SOURCES_V023 = (
    EvidenceSourceV22.LOGS,
    EvidenceSourceV22.METRICS,
    EvidenceSourceV22.RUNTIME,
)
_NOFAULT_PROFILE_BODY_V023 = {
    "schema_version": "ecomsre.product.nofault-execution-profile.v023",
    "seed": 23082901,
    "candidate_services": ("checkout",),
    "incident_fault_label": "none",
    "request_count": 30,
    "requests_per_second": 1.0,
    "maximum_error_fraction": 0.01,
    "queue_fault_flag": 0,
}
_NOFAULT_PROFILE_SHA256_V023 = semantic_sha256_v22(_NOFAULT_PROFILE_BODY_V023)


class NoFaultMeasuredTerminalV023(str, Enum):
    FULLY_SUPPORTED = NOFAULT_FULLY_SUPPORTED_V023
    CAPABILITY_LIMITED = NOFAULT_CAPABILITY_LIMITED_V023
    NOT_SUPPORTED = NOFAULT_NOT_SUPPORTED_V023


class NoFaultExecutionProfileV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.nofault-execution-profile.v023"
    ] = "ecomsre.product.nofault-execution-profile.v023"
    seed: int = Field(ge=1)
    candidate_services: tuple[Literal["checkout"], ...]
    incident_fault_label: Literal["none"]
    request_count: Literal[30]
    requests_per_second: float = Field(gt=0, le=10, allow_inf_nan=False)
    maximum_error_fraction: float = Field(ge=0, le=0.05, allow_inf_nan=False)
    queue_fault_flag: Literal[0]
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_frozen_profile(self) -> "NoFaultExecutionProfileV023":
        normalized = self.model_dump(mode="json", exclude={"profile_sha256"})
        expected_body = {
            **_NOFAULT_PROFILE_BODY_V023,
            "candidate_services": ["checkout"],
        }
        expected = semantic_sha256_v22(normalized)
        if (
            normalized != expected_body
            or self.profile_sha256 != expected
            or expected != _NOFAULT_PROFILE_SHA256_V023
        ):
            raise ValueError("No-Fault execution profile digest differs")
        return self

    @classmethod
    def load(cls, path: Path) -> "NoFaultExecutionProfileV023":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("No-Fault execution profile must be an object")
        return cls.model_validate(payload)

    @classmethod
    def default(cls) -> "NoFaultExecutionProfileV023":
        body = dict(_NOFAULT_PROFILE_BODY_V023)
        return cls.model_validate(
            {**body, "profile_sha256": semantic_sha256_v22(body)}
        )


class NoFaultTrafficResultV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.nofault-traffic-result.v023"
    ] = "ecomsre.product.nofault-traffic-result.v023"
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    window: ConnectorWindowV1
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    planned_request_count: int = Field(ge=1)
    completed_request_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    requests_per_second: float = Field(gt=0, allow_inf_nan=False)
    maximum_error_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    queue_fault_flag: int = Field(ge=0, le=1)
    passed: bool
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_measured_result(self) -> "NoFaultTrafficResultV023":
        if self.completed_request_count > self.planned_request_count:
            raise ValueError("No-Fault traffic completed count exceeds its plan")
        if self.error_count > self.completed_request_count:
            raise ValueError("No-Fault traffic error count exceeds completion")
        error_fraction = (
            1.0
            if self.completed_request_count == 0
            else self.error_count / self.completed_request_count
        )
        measured_pass = (
            self.completed_request_count == self.planned_request_count
            and error_fraction <= self.maximum_error_fraction
            and self.queue_fault_flag == 0
        )
        if self.passed != measured_pass:
            raise ValueError("No-Fault traffic disposition differs from measured counts")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("No-Fault traffic result digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "NoFaultTrafficResultV023":
        window = payload.get("window")
        if isinstance(window, Mapping):
            window = ConnectorWindowV1.model_validate(window)
        body = {
            "schema_version": "ecomsre.product.nofault-traffic-result.v023",
            **payload,
            "window": window,
        }
        draft = cls.model_construct(**body, result_sha256="0" * 64)
        normalized = draft.model_dump(mode="json", exclude={"result_sha256"})
        return cls.model_validate(
            {**normalized, "result_sha256": semantic_sha256_v22(normalized)}
        )


def _canonical_object_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _walk(value: Any):
    yield value
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk(item)


def _contains_pair(value: Any, key: str, expected: Any) -> bool:
    return any(
        isinstance(item, Mapping) and item.get(key) == expected
        for item in _walk(value)
    )


def _contains_control_truth(value: Any) -> bool:
    forbidden_keys = {
        "queue_fault_flag",
        "queue_flag",
        "fault_flag",
        "evaluator_fault",
        "evaluator_truth",
    }
    return any(
        isinstance(item, Mapping)
        and any(str(key).casefold() in forbidden_keys for key in item)
        for item in _walk(value)
    )


def _logs_profile_binding_visible(
    value: Any,
    *,
    active_profile_sha256: str,
    incident: IncidentRecordV1,
) -> bool:
    if not isinstance(value, Mapping):
        return False
    diagnostics = value.get("connector_diagnostics")
    connector_result = value.get("connector_result")
    if not isinstance(diagnostics, (list, tuple)) or not isinstance(
        connector_result, Mapping
    ):
        return False
    try:
        parsed_result = ConnectorQueryResultV1.model_validate(
            connector_result,
            strict=False,
        )
    except (TypeError, ValueError):
        return False
    if (
        parsed_result.source is not EvidenceSourceV22.LOGS
        or not _episode_result_is_fresh(parsed_result, incident=incident)
    ):
        return False
    result_status = parsed_result.status.value
    for item in diagnostics:
        try:
            parsed = OpenSearchConnectorDiagnosticsV023.model_validate(item)
        except (TypeError, ValueError):
            continue
        if (
            parsed.profile_sha256 == active_profile_sha256
            and parsed.terminal == "ECOMSRE_PRODUCT_V023_PROFILE_BINDING_PASS"
            and parsed.last_query_status == result_status
            and parsed.last_query_status in {"SUCCESS_EMPTY", "SUCCESS_NONEMPTY"}
            and parsed.last_normalization_status
            in {"SUCCESS_EMPTY", "SUCCESS_NONEMPTY"}
            and parsed.last_accepted_record_count == len(parsed_result.records)
        ):
            return True
    return False


def _successful_evidence_sources(
    bundle: EvidenceBundleV1,
    *,
    incident: IncidentRecordV1,
) -> tuple[EvidenceSourceV22, ...]:
    successful: set[EvidenceSourceV22] = set()
    for item in bundle.objects:
        connector = item.payload.get("connector_result")
        if not isinstance(connector, Mapping):
            continue
        try:
            result = ConnectorQueryResultV1.model_validate(connector, strict=False)
        except (TypeError, ValueError):
            continue
        if (
            result.source is not item.source
            or result.status.value != "SUCCESS_NONEMPTY"
            or not _episode_result_is_fresh(result, incident=incident)
            or result.requested_services != ("checkout",)
            or result.covered_services != ("checkout",)
            or not result.records
            or any(
                getattr(record, "service", None) != "checkout"
                for record in result.records
            )
        ):
            continue
        successful.add(item.source)
    return tuple(sorted(successful, key=lambda source: source.value))


def _episode_result_is_fresh(
    result: ConnectorQueryResultV1,
    *,
    incident: IncidentRecordV1,
) -> bool:
    return (
        not result.truncated
        and result.window.started_at >= incident.started_at
        and result.window.ended_at == incident.diagnosis_observed_at
    )


def _limitation_evidence_backed(
    *,
    limitation: str,
    evidence_ref: str,
    bundle: EvidenceBundleV1,
    incident: IncidentRecordV1,
) -> bool:
    source = next(
        (
            item
            for item in EvidenceSourceV22
            if item.value in limitation.upper().split("_")
        ),
        None,
    )
    evidence = next(
        (item for item in bundle.objects if item.evidence_ref == evidence_ref),
        None,
    )
    if source is None or evidence is None or evidence.source is not source:
        return False
    connector = evidence.payload.get("connector_result")
    if not isinstance(connector, Mapping):
        return False
    try:
        result = ConnectorQueryResultV1.model_validate(connector, strict=False)
    except (TypeError, ValueError):
        return False
    return (
        result.source is source
        and result.status.value.startswith("FAILURE_")
        and result.requested_services == ("checkout",)
        and result.covered_services == ()
        and result.records == ()
        and isinstance(result.safe_error_code, str)
        and bool(result.safe_error_code)
        and _episode_result_is_fresh(result, incident=incident)
    )


def _hidden_connector_failure(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    connector = value.get("connector_result")
    outcome = value.get("read_outcome")
    if not isinstance(connector, Mapping):
        return False
    status = str(connector.get("status", ""))
    if not status.startswith("FAILURE_"):
        return False
    if not connector.get("safe_error_code"):
        return True
    if isinstance(outcome, Mapping):
        return str(outcome.get("status", "")).startswith("SUCCESS_")
    return False


def _fresh_healthy_runtime_evidence(
    *,
    bundle: EvidenceBundleV1,
    evidence_ref: str | None,
    incident: IncidentRecordV1,
    baseline_audit: ProductBaselineReadinessAuditV023,
) -> bool:
    if evidence_ref is None:
        return False
    objects_by_ref = {item.evidence_ref: item for item in bundle.objects}
    runtime_object = objects_by_ref.get(evidence_ref)
    if runtime_object is None or runtime_object.source is not EvidenceSourceV22.RUNTIME:
        return False
    connector = runtime_object.payload.get("connector_result")
    if not isinstance(connector, Mapping):
        return False
    try:
        result = ConnectorQueryResultV1.model_validate(connector, strict=False)
    except (TypeError, ValueError):
        return False
    if (
        result.source is not EvidenceSourceV22.RUNTIME
        or result.status.value != "SUCCESS_NONEMPTY"
        or result.truncated
        or "checkout" not in result.covered_services
    ):
        return False
    window = result.window
    latest_baseline_end = max(
        item.window.ended_at for item in baseline_audit.evaluation.windows
    )
    if (
        window.started_at < latest_baseline_end
        or window.started_at < incident.started_at
        or window.ended_at != incident.diagnosis_observed_at
    ):
        return False
    checkout_records = tuple(
        item
        for item in result.records
        if getattr(item, "service", None) == "checkout"
    )
    return bool(checkout_records) and all(
        getattr(item, "healthy", None) is True
        and getattr(getattr(item, "state", None), "value", None) == "RUNNING"
        for item in checkout_records
    )


class NoFaultEvidenceResolutionV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.nofault-evidence-resolution.v023"
    ] = "ecomsre.product.nofault-evidence-resolution.v023"
    all_references_resolved: bool
    all_object_sha256_resolved: bool
    logs_profile_binding_visible: bool
    source_failures_explicit: bool
    agent_visible_control_truth_absent: bool
    resolved_evidence_refs: tuple[str, ...]
    resolved_object_sha256_by_ref: dict[str, str]
    evidence_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolution_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_resolution(self) -> "NoFaultEvidenceResolutionV023":
        if self.resolved_evidence_refs != tuple(sorted(set(self.resolved_evidence_refs))):
            raise ValueError("No-Fault resolved evidence refs are not canonical")
        if self.resolved_object_sha256_by_ref != dict(
            sorted(self.resolved_object_sha256_by_ref.items())
        ):
            raise ValueError("No-Fault resolved object SHA map is not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"resolution_sha256"})
        )
        if self.resolution_sha256 != expected:
            raise ValueError("No-Fault evidence resolution digest differs")
        return self


def resolve_nofault_evidence_v023(
    *,
    incident: IncidentRecordV1,
    diagnosis: DiagnosisResultV1,
    bundle: EvidenceBundleV1,
    active_profile_sha256: str,
) -> NoFaultEvidenceResolutionV023:
    objects_by_ref = {item.evidence_ref: item for item in bundle.objects}
    duplicate_refs = len(objects_by_ref) != len(bundle.objects)
    expected_refs = tuple(
        sorted(
            set(diagnosis.supporting_evidence_refs).union(
                diagnosis.contradicting_evidence_refs
            )
        )
    )
    bundle_refs = tuple(
        sorted(
            set(bundle.supporting_evidence_refs).union(
                bundle.contradicting_evidence_refs
            )
        )
    )
    ids_match = (
        bundle.incident_id == incident.incident_id
        and bundle.diagnosis_id == diagnosis.diagnosis_id
        and diagnosis.incident_id == incident.incident_id
    )
    refs_resolved = (
        ids_match
        and not duplicate_refs
        and expected_refs == bundle_refs
        and set(objects_by_ref) == set(expected_refs)
        and all(item in objects_by_ref for item in expected_refs)
    )
    sha_resolved = all(
        _canonical_object_sha256(item.payload) == item.object_sha256
        for item in bundle.objects
    )
    profile_visible = any(
        item.source is EvidenceSourceV22.LOGS
        and _logs_profile_binding_visible(
            item.payload,
            active_profile_sha256=active_profile_sha256,
            incident=incident,
        )
        for item in bundle.objects
    )
    source_failures_explicit = not any(
        _hidden_connector_failure(item.payload) for item in bundle.objects
    )
    control_truth_absent = not any(
        _contains_control_truth(item.payload) for item in bundle.objects
    )
    object_sha256_by_ref = dict(
        sorted(
            (item.evidence_ref, item.object_sha256) for item in bundle.objects
        )
    )
    evidence_bundle_sha256 = semantic_sha256_v22(bundle.model_dump(mode="json"))
    body = {
        "schema_version": "ecomsre.product.nofault-evidence-resolution.v023",
        "all_references_resolved": refs_resolved,
        "all_object_sha256_resolved": sha_resolved,
        "logs_profile_binding_visible": profile_visible,
        "source_failures_explicit": source_failures_explicit,
        "agent_visible_control_truth_absent": control_truth_absent,
        "resolved_evidence_refs": expected_refs,
        "resolved_object_sha256_by_ref": object_sha256_by_ref,
        "evidence_bundle_sha256": evidence_bundle_sha256,
    }
    return NoFaultEvidenceResolutionV023.model_validate(
        {**body, "resolution_sha256": semantic_sha256_v22(body)}
    )


class NoFaultCapabilityAssessmentV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.nofault-capability-assessment.v023"
    ] = "ecomsre.product.nofault-capability-assessment.v023"
    runtime_healthy: bool
    runtime_evidence_ref: str | None
    required_sources: tuple[EvidenceSourceV22, ...]
    successful_sources: tuple[EvidenceSourceV22, ...]
    healthy_traffic_passed: bool
    healthy_traffic_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    limitation_evidence_refs: dict[str, str]
    assessment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_assessment(self) -> "NoFaultCapabilityAssessmentV023":
        if self.runtime_healthy != (self.runtime_evidence_ref is not None):
            raise ValueError("No-Fault Runtime assessment binding differs")
        if self.limitation_evidence_refs != dict(
            sorted(self.limitation_evidence_refs.items())
        ):
            raise ValueError("No-Fault limitation evidence is not canonical")
        if self.required_sources != _REQUIRED_NOFAULT_SOURCES_V023:
            raise ValueError("No-Fault required source set differs")
        if self.successful_sources != tuple(
            sorted(set(self.successful_sources), key=lambda source: source.value)
        ):
            raise ValueError("No-Fault successful source set is not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"assessment_sha256"})
        )
        if self.assessment_sha256 != expected:
            raise ValueError("No-Fault capability assessment digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "NoFaultCapabilityAssessmentV023":
        body = {
            "schema_version": "ecomsre.product.nofault-capability-assessment.v023",
            **payload,
            "required_sources": _REQUIRED_NOFAULT_SOURCES_V023,
            "successful_sources": tuple(
                sorted(
                    {
                        (
                            item
                            if isinstance(item, EvidenceSourceV22)
                            else EvidenceSourceV22(item)
                        )
                        for item in payload.get("successful_sources", ())
                    },
                    key=lambda source: source.value,
                )
            ),
            "limitation_evidence_refs": dict(
                sorted(dict(payload.get("limitation_evidence_refs", {})).items())
            ),
        }
        return cls.model_validate(
            {**body, "assessment_sha256": semantic_sha256_v22(body)}
        )


class NoFaultQueueSnapshotV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.nofault-queue-snapshot.v023"
    ] = "ecomsre.product.nofault-queue-snapshot.v023"
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    observed_at: datetime
    pending_jobs: int = Field(ge=0)
    running_jobs: int = Field(ge=0)
    failed_jobs: int = Field(ge=0)
    queue_fault_flag: int = Field(ge=0, le=1)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_snapshot(self) -> "NoFaultQueueSnapshotV023":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("No-Fault queue snapshot time must be UTC")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"snapshot_sha256"})
        )
        if self.snapshot_sha256 != expected:
            raise ValueError("No-Fault queue snapshot digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "NoFaultQueueSnapshotV023":
        body = {
            "schema_version": "ecomsre.product.nofault-queue-snapshot.v023",
            **payload,
        }
        draft = cls.model_construct(**body, snapshot_sha256="0" * 64)
        normalized = draft.model_dump(mode="json", exclude={"snapshot_sha256"})
        return cls.model_validate(
            {**normalized, "snapshot_sha256": semantic_sha256_v22(normalized)}
        )


class NoFaultAcceptanceResultV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.nofault-acceptance-result.v023"
    ] = "ecomsre.product.nofault-acceptance-result.v023"
    terminal: NoFaultMeasuredTerminalV023
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    diagnosis_id: str = Field(pattern=r"^diag-[0-9a-f]{24}$")
    baseline_id: str = Field(pattern=r"^base-[0-9a-f]{24}$")
    baseline_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    service_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    diagnosis_terminal: DiagnosisTerminalV1
    restart_proof_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    incident_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    diagnosis_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_resolution: NoFaultEvidenceResolutionV023
    capability_assessment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    traffic_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    queue_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reasons: tuple[str, ...]
    incident_count: int = Field(ge=0)
    diagnosis_count: int = Field(ge=0)
    fault_family_count: int = Field(ge=0)
    action_authority: Literal["NONE"]
    action_authority_violations: int = Field(ge=0)
    agent_writes: int = Field(ge=0)
    runbook_executions: int = Field(ge=0)
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_result(self) -> "NoFaultAcceptanceResultV023":
        if self.reasons != tuple(sorted(set(self.reasons))):
            raise ValueError("No-Fault measured reasons are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("No-Fault acceptance result digest differs")
        return self


def score_nofault_v023(
    *,
    baseline_audit: ProductBaselineReadinessAuditV023,
    restart_proof: BaselineRestartProofV023,
    incident: IncidentRecordV1,
    diagnosis: DiagnosisResultV1,
    bundle: EvidenceBundleV1,
    capability_assessment: NoFaultCapabilityAssessmentV023,
    execution_profile: NoFaultExecutionProfileV023,
    traffic_result: NoFaultTrafficResultV023,
    queue_snapshot: NoFaultQueueSnapshotV023,
    active_profile_sha256: str,
    incident_count: int,
    diagnosis_count: int,
    fault_family_count: int,
    action_authority_violations: int,
    agent_writes: int,
    runbook_executions: int,
) -> NoFaultAcceptanceResultV023:
    if (
        not baseline_audit.final_builder_would_pass
        or restart_proof.after.environment_id != baseline_audit.environment_id
        or restart_proof.after.profile_sha256
        != baseline_audit.active_opensearch_profile_sha256
        or restart_proof.after.active_baseline_id != baseline_audit.baseline_id
        or restart_proof.after.active_baseline_sha256 != baseline_audit.baseline_sha256
        or restart_proof.after.service_identity_sha256
        != baseline_audit.service_identity_sha256
        or restart_proof.after.capability_sha256 != baseline_audit.capability_sha256
    ):
        raise ProductError(
            "NOFAULT_BASELINE_RESTART_REQUIRED",
            "No-Fault scoring requires a passing Baseline and restart proof.",
        )
    resolution = resolve_nofault_evidence_v023(
        incident=incident,
        diagnosis=diagnosis,
        bundle=bundle,
        active_profile_sha256=active_profile_sha256,
    )
    reasons: set[str] = set()
    incident_end = incident.ended_at or incident.diagnosis_observed_at
    latest_baseline_end = max(
        window.window.ended_at for window in baseline_audit.evaluation.windows
    )
    fresh_nonoverlap = all(
        incident.started_at >= window.window.ended_at
        or incident_end <= window.window.started_at
        for window in baseline_audit.evaluation.windows
    )
    binding_valid = (
        incident.baseline_id == baseline_audit.baseline_id
        and incident.baseline_sha256 == baseline_audit.baseline_sha256
        and incident.service_identity_sha256
        == baseline_audit.service_identity_sha256
        and incident.source_capability_sha256 == baseline_audit.capability_sha256
        and active_profile_sha256
        == baseline_audit.active_opensearch_profile_sha256
        and active_profile_sha256 == ACTIVE_PROFILE_SHA256_V023
        and incident.labels.get("fault") == "none"
        and incident.environment_id == baseline_audit.environment_id
        and incident.candidate_logical_services == ("checkout",)
        and incident.candidate_service_ids
        == baseline_audit.baseline_entity_service_ids
        and restart_proof.before.observed_at >= latest_baseline_end
        and restart_proof.after.observed_at <= incident.started_at
        and fresh_nonoverlap
    )
    if not binding_valid:
        reasons.add("NOFAULT_EPISODE_BINDING_INVALID")
    if incident_count != 1 or diagnosis_count != 1:
        reasons.add("NOFAULT_EXACT_COUNT_INVALID")
    if fault_family_count != 0:
        reasons.add("UNEXPECTED_FAULT_FAMILY")
    traffic_binding_valid = (
        traffic_result.environment_id == incident.environment_id
        and traffic_result.environment_id == baseline_audit.environment_id
        and traffic_result.incident_id == incident.incident_id
        and traffic_result.window.started_at == incident.started_at
        and traffic_result.window.ended_at == incident.diagnosis_observed_at
        and traffic_result.profile_sha256 == execution_profile.profile_sha256
        and traffic_result.planned_request_count == execution_profile.request_count
        and traffic_result.requests_per_second
        == execution_profile.requests_per_second
        and traffic_result.maximum_error_fraction
        == execution_profile.maximum_error_fraction
        and traffic_result.queue_fault_flag == execution_profile.queue_fault_flag
        and capability_assessment.healthy_traffic_passed == traffic_result.passed
        and capability_assessment.healthy_traffic_result_sha256
        == traffic_result.result_sha256
    )
    if not traffic_binding_valid or not traffic_result.passed:
        reasons.add("HEALTHY_TRAFFIC_FAILED_OR_UNBOUND")
    queue_snapshot_fresh = (
        queue_snapshot.environment_id == incident.environment_id
        and incident.environment_id == baseline_audit.environment_id
        and diagnosis.created_at
        <= queue_snapshot.observed_at
        <= diagnosis.created_at + timedelta(seconds=60)
    )
    if (
        not queue_snapshot_fresh
        or queue_snapshot.queue_fault_flag != 0
        or any(
            (
                queue_snapshot.pending_jobs,
                queue_snapshot.running_jobs,
                queue_snapshot.failed_jobs,
            )
        )
    ):
        reasons.add("NOFAULT_QUEUE_NOT_EMPTY_OR_FRESH")
    if not resolution.all_references_resolved:
        reasons.add("EVIDENCE_REFERENCE_UNRESOLVED")
    if not resolution.all_object_sha256_resolved:
        reasons.add("EVIDENCE_OBJECT_SHA256_UNRESOLVED")
    if not resolution.logs_profile_binding_visible:
        reasons.add("LOGS_PROFILE_BINDING_MISSING")
    if not resolution.source_failures_explicit:
        reasons.add("HIDDEN_CONNECTOR_FAILURE")
    if not resolution.agent_visible_control_truth_absent:
        reasons.add("AGENT_VISIBLE_CONTROL_TRUTH_PRESENT")
    if diagnosis.action_authority.value != "NONE" or action_authority_violations:
        reasons.add("UNEXPECTED_ACTION_AUTHORITY")
    if (
        diagnosis.agent_writes
        or diagnosis.runbook_executions
        or agent_writes
        or runbook_executions
    ):
        reasons.add("UNEXPECTED_ACTION_COUNTER")
    resolved_refs = set(resolution.resolved_evidence_refs)
    successful_sources = _successful_evidence_sources(bundle, incident=incident)
    coverage_sufficient = set(_REQUIRED_NOFAULT_SOURCES_V023).issubset(
        successful_sources
    )
    if capability_assessment.successful_sources != successful_sources:
        reasons.add("SOURCE_COVERAGE_ASSESSMENT_UNBOUND")
    if (
        capability_assessment.runtime_evidence_ref not in resolved_refs
        or not capability_assessment.runtime_healthy
        or not _fresh_healthy_runtime_evidence(
            bundle=bundle,
            evidence_ref=capability_assessment.runtime_evidence_ref,
            incident=incident,
            baseline_audit=baseline_audit,
        )
    ):
        reasons.add("FRESH_HEALTHY_RUNTIME_MISSING")
    if (
        diagnosis.terminal is DiagnosisTerminalV1.NO_INCIDENT
        and not coverage_sufficient
    ):
        reasons.add("REQUIRED_SOURCE_COVERAGE_INSUFFICIENT")
    classified_or_conflicting = diagnosis.terminal in {
        DiagnosisTerminalV1.CORE_KNOWN,
        DiagnosisTerminalV1.EXTENSION_KNOWN,
        DiagnosisTerminalV1.OPEN_WORLD,
        DiagnosisTerminalV1.CONFLICTING_EVIDENCE,
    }
    if classified_or_conflicting:
        reasons.add("FALSE_INCIDENT_TERMINAL")
    if diagnosis.terminal is DiagnosisTerminalV1.INSUFFICIENT_EVIDENCE:
        limitation_map = capability_assessment.limitation_evidence_refs
        if (
            not diagnosis.capability_limitations
            or set(limitation_map) != set(diagnosis.capability_limitations)
            or any(ref not in resolved_refs for ref in limitation_map.values())
            or any(
                not _limitation_evidence_backed(
                    limitation=limitation,
                    evidence_ref=evidence_ref,
                    bundle=bundle,
                    incident=incident,
                )
                for limitation, evidence_ref in limitation_map.items()
            )
        ):
            reasons.add("CAPABILITY_LIMITATION_NOT_EVIDENCE_BACKED")
    hard_failures = bool(reasons)
    if (
        not hard_failures
        and diagnosis.terminal is DiagnosisTerminalV1.NO_INCIDENT
        and not diagnosis.capability_limitations
        and not diagnosis.root_service_ids
        and diagnosis.mechanism is None
        and diagnosis.broad_domain is None
        and diagnosis.provisional_report is None
    ):
        measured = NoFaultMeasuredTerminalV023.FULLY_SUPPORTED
    elif (
        not hard_failures
        and diagnosis.terminal is DiagnosisTerminalV1.INSUFFICIENT_EVIDENCE
    ):
        measured = NoFaultMeasuredTerminalV023.CAPABILITY_LIMITED
    else:
        measured = NoFaultMeasuredTerminalV023.NOT_SUPPORTED
    body = {
        "schema_version": "ecomsre.product.nofault-acceptance-result.v023",
        "terminal": measured.value,
        "incident_id": incident.incident_id,
        "diagnosis_id": diagnosis.diagnosis_id,
        "baseline_id": baseline_audit.baseline_id,
        "baseline_sha256": baseline_audit.baseline_sha256,
        "profile_sha256": active_profile_sha256,
        "service_identity_sha256": baseline_audit.service_identity_sha256,
        "capability_sha256": baseline_audit.capability_sha256,
        "diagnosis_terminal": diagnosis.terminal.value,
        "restart_proof_sha256": restart_proof.proof_sha256,
        "incident_sha256": incident.incident_sha256,
        "diagnosis_result_sha256": diagnosis.result_sha256,
        "evidence_bundle_sha256": resolution.evidence_bundle_sha256,
        "evidence_resolution": resolution.model_dump(mode="json"),
        "capability_assessment_sha256": capability_assessment.assessment_sha256,
        "execution_profile_sha256": execution_profile.profile_sha256,
        "traffic_result_sha256": traffic_result.result_sha256,
        "queue_snapshot_sha256": queue_snapshot.snapshot_sha256,
        "reasons": tuple(sorted(reasons)),
        "incident_count": incident_count,
        "diagnosis_count": diagnosis_count,
        "fault_family_count": fault_family_count,
        "action_authority": diagnosis.action_authority.value,
        "action_authority_violations": action_authority_violations,
        "agent_writes": diagnosis.agent_writes + agent_writes,
        "runbook_executions": diagnosis.runbook_executions + runbook_executions,
    }
    return NoFaultAcceptanceResultV023.model_validate(
        {**body, "result_sha256": semantic_sha256_v22(body)}
    )


__all__ = (
    "NOFAULT_CAPABILITY_LIMITED_V023",
    "NOFAULT_FULLY_SUPPORTED_V023",
    "NOFAULT_NOT_SUPPORTED_V023",
    "NoFaultAcceptanceResultV023",
    "NoFaultCapabilityAssessmentV023",
    "NoFaultEvidenceResolutionV023",
    "NoFaultExecutionProfileV023",
    "NoFaultMeasuredTerminalV023",
    "NoFaultQueueSnapshotV023",
    "NoFaultTrafficResultV023",
    "resolve_nofault_evidence_v023",
    "score_nofault_v023",
)
