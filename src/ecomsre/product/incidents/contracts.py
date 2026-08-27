"""Closed Product contracts for incident diagnosis and retrievable evidence."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1


class IncidentCreateV1(ProductModelV1):
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    external_incident_key: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    alert_name: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=2000)
    started_at: datetime
    ended_at: datetime | None = None
    candidate_service_ids: tuple[str, ...] = Field(min_length=1, max_length=4)
    labels: dict[str, str] = Field(default_factory=dict, max_length=32)

    @field_validator("labels")
    @classmethod
    def labels_are_bounded(cls, value: dict[str, str]) -> dict[str, str]:
        if any(
            not key
            or len(key) > 80
            or len(label) > 240
            or any(character in key + label for character in "\r\n\x00")
            for key, label in value.items()
        ):
            raise ValueError("incident label is invalid")
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def require_canonical_utc_incident(self) -> "IncidentCreateV1":
        if self.started_at.tzinfo is None or self.started_at.utcoffset() != timedelta(0):
            raise ValueError("incident start must be UTC")
        if self.ended_at is not None:
            if self.ended_at.tzinfo is None or self.ended_at.utcoffset() != timedelta(0):
                raise ValueError("incident end must be UTC")
            if self.ended_at < self.started_at:
                raise ValueError("incident end precedes start")
        if self.candidate_service_ids != tuple(sorted(set(self.candidate_service_ids))):
            raise ValueError("incident candidate services are not canonical")
        return self


class IncidentRecordV1(IncidentCreateV1):
    schema_version: Literal["ecomsre.product.incident.v1"] = (
        "ecomsre.product.incident.v1"
    )
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    baseline_id: str = Field(pattern=r"^base-[0-9a-f]{24}$")
    baseline_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    service_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_capability_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_logical_services: tuple[str, ...] = Field(min_length=1, max_length=4)
    diagnosis_observed_at: datetime
    created_at: datetime
    incident_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_frozen_incident(self) -> "IncidentRecordV1":
        for value in (self.diagnosis_observed_at, self.created_at):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError("incident frozen timestamp must be UTC")
        if self.candidate_logical_services != tuple(
            sorted(set(self.candidate_logical_services))
        ):
            raise ValueError("incident logical services are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"incident_sha256"})
        )
        if self.incident_sha256 != expected:
            raise ValueError("incident digest differs")
        return self


class DiagnosisTerminalV1(str, Enum):
    CORE_KNOWN = "CORE_KNOWN"
    EXTENSION_KNOWN = "EXTENSION_KNOWN"
    NO_INCIDENT = "NO_INCIDENT"
    OPEN_WORLD = "OPEN_WORLD"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"


class DiagnosisLaneV1(str, Enum):
    CORE = "CORE"
    EXTENSION = "EXTENSION"
    NO_INCIDENT = "NO_INCIDENT"
    OPEN_WORLD = "OPEN_WORLD"
    ABSTAIN = "ABSTAIN"


class ActionAuthorityV1(str, Enum):
    NONE = "NONE"


class DiagnosisResultV1(ProductModelV1):
    schema_version: Literal["ecomsre.product.diagnosis-result.v1"] = (
        "ecomsre.product.diagnosis-result.v1"
    )
    diagnosis_id: str = Field(pattern=r"^diag-[0-9a-f]{24}$")
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    terminal: DiagnosisTerminalV1
    core_or_extension_or_open_world: DiagnosisLaneV1
    root_service_ids: tuple[str, ...] = Field(max_length=4)
    mechanism: MechanismV22 | str | None = None
    broad_domain: str | None = None
    supporting_evidence_refs: tuple[str, ...] = Field(max_length=40)
    contradicting_evidence_refs: tuple[str, ...] = Field(max_length=40)
    capability_limitations: tuple[str, ...] = Field(max_length=40)
    provisional_report: dict[str, Any] | None = None
    action_authority: ActionAuthorityV1 = ActionAuthorityV1.NONE
    agent_writes: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    provider_calls: Literal[0] = 0
    memory_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_non_actionable_bound_result(self) -> "DiagnosisResultV1":
        for values in (
            self.root_service_ids,
            self.supporting_evidence_refs,
            self.contradicting_evidence_refs,
            self.capability_limitations,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("diagnosis set-like fields are not canonical")
        if set(self.supporting_evidence_refs).intersection(
            self.contradicting_evidence_refs
        ):
            raise ValueError("diagnosis support and contradiction overlap")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() != timedelta(0):
            raise ValueError("diagnosis creation time must be UTC")
        lane_by_terminal = {
            DiagnosisTerminalV1.CORE_KNOWN: DiagnosisLaneV1.CORE,
            DiagnosisTerminalV1.EXTENSION_KNOWN: DiagnosisLaneV1.EXTENSION,
            DiagnosisTerminalV1.NO_INCIDENT: DiagnosisLaneV1.NO_INCIDENT,
            DiagnosisTerminalV1.OPEN_WORLD: DiagnosisLaneV1.OPEN_WORLD,
            DiagnosisTerminalV1.INSUFFICIENT_EVIDENCE: DiagnosisLaneV1.ABSTAIN,
            DiagnosisTerminalV1.CONFLICTING_EVIDENCE: DiagnosisLaneV1.ABSTAIN,
        }
        if self.core_or_extension_or_open_world is not lane_by_terminal[self.terminal]:
            raise ValueError("diagnosis lane differs from terminal")
        classified = self.terminal in {
            DiagnosisTerminalV1.CORE_KNOWN,
            DiagnosisTerminalV1.EXTENSION_KNOWN,
            DiagnosisTerminalV1.OPEN_WORLD,
        }
        if classified != bool(self.root_service_ids):
            raise ValueError("diagnosis root-service semantics differ")
        if classified != (self.mechanism is not None and self.broad_domain is not None):
            raise ValueError("diagnosis mechanism semantics differ")
        if self.terminal is DiagnosisTerminalV1.OPEN_WORLD and self.provisional_report is None:
            raise ValueError("open-world diagnosis lacks a provisional report")
        if self.terminal is not DiagnosisTerminalV1.OPEN_WORLD and self.provisional_report is not None:
            raise ValueError("non-open diagnosis contains a provisional report")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("diagnosis result digest differs")
        return self


class EvidenceObjectV1(ProductModelV1):
    evidence_ref: str
    source: EvidenceSourceV22
    action_id: str
    object_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    role: Literal["OBSERVATION"] = "OBSERVATION"
    payload: dict[str, Any]


class EvidenceBundleV1(ProductModelV1):
    schema_version: Literal["ecomsre.product.evidence-bundle.v1"] = (
        "ecomsre.product.evidence-bundle.v1"
    )
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    diagnosis_id: str = Field(pattern=r"^diag-[0-9a-f]{24}$")
    objects: tuple[EvidenceObjectV1, ...]
    supporting_evidence_refs: tuple[str, ...]
    contradicting_evidence_refs: tuple[str, ...]


__all__ = (
    "ActionAuthorityV1",
    "DiagnosisLaneV1",
    "DiagnosisResultV1",
    "DiagnosisTerminalV1",
    "EvidenceBundleV1",
    "EvidenceObjectV1",
    "IncidentCreateV1",
    "IncidentRecordV1",
)
