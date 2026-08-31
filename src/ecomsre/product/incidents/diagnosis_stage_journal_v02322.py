"""Append-only Diagnosis stage journal for Product v0.2.3.2.2."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
import json
from typing import Any

from pydantic import ConfigDict, Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


DIAGNOSIS_STAGE_JOURNAL_PASS_V02322 = (
    "ECOMSRE_PRODUCT_V02322_DIAGNOSIS_STAGE_JOURNAL_PASS"
)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class DiagnosisPipelineStageV02322(str, Enum):
    JOB_CLAIMED = "JOB_CLAIMED"
    INCIDENT_LOAD_STARTED = "INCIDENT_LOAD_STARTED"
    INCIDENT_LOADED = "INCIDENT_LOADED"
    BASELINE_BINDING_STARTED = "BASELINE_BINDING_STARTED"
    BASELINE_BOUND = "BASELINE_BOUND"
    SERVICE_IDENTITY_BINDING_STARTED = "SERVICE_IDENTITY_BINDING_STARTED"
    SERVICE_IDENTITY_BOUND = "SERVICE_IDENTITY_BOUND"
    CAPABILITY_BINDING_STARTED = "CAPABILITY_BINDING_STARTED"
    CAPABILITY_BOUND = "CAPABILITY_BOUND"
    ENVIRONMENT_LOAD_STARTED = "ENVIRONMENT_LOAD_STARTED"
    ENVIRONMENT_LOADED = "ENVIRONMENT_LOADED"
    READ_ACQUISITION_STARTED = "READ_ACQUISITION_STARTED"
    READ_ACQUISITION_COMPLETED = "READ_ACQUISITION_COMPLETED"
    BRIDGE_DIAGNOSIS_STARTED = "BRIDGE_DIAGNOSIS_STARTED"
    BRIDGE_DIAGNOSIS_COMPLETED = "BRIDGE_DIAGNOSIS_COMPLETED"
    EVIDENCE_PREPARE_STARTED = "EVIDENCE_PREPARE_STARTED"
    EVIDENCE_OBJECTS_PREPARED = "EVIDENCE_OBJECTS_PREPARED"
    LIMITATION_BINDING_STARTED = "LIMITATION_BINDING_STARTED"
    LIMITATION_BINDING_COMPLETED = "LIMITATION_BINDING_COMPLETED"
    EVIDENCE_INDEX_STARTED = "EVIDENCE_INDEX_STARTED"
    EVIDENCE_INDEX_VALIDATED = "EVIDENCE_INDEX_VALIDATED"
    OBJECT_STORE_PREPARE_STARTED = "OBJECT_STORE_PREPARE_STARTED"
    OBJECT_STORE_PREPARED = "OBJECT_STORE_PREPARED"
    SQL_TRANSACTION_STARTED = "SQL_TRANSACTION_STARTED"
    DIAGNOSIS_PERSISTED = "DIAGNOSIS_PERSISTED"
    JOB_RESULT_PREPARED = "JOB_RESULT_PREPARED"
    JOB_SUCCEEDED = "JOB_SUCCEEDED"
    FAILED = "FAILED"


class DiagnosisStageStatusV02322(str, Enum):
    STARTED = "STARTED"
    PASSED = "PASSED"
    FAILED = "FAILED"


class DiagnosisStageEventV02322(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "ecomsre.product.diagnosis-stage-event.v02322"
    journal_id: str = Field(pattern=r"^journal-[0-9a-f]{24}$")
    job_id: str = Field(pattern=r"^job-[0-9a-f]{24}$")
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    ordinal: int = Field(ge=1)
    stage: DiagnosisPipelineStageV02322
    status: DiagnosisStageStatusV02322
    input_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    output_artifact_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    source_code_sha256: str = Field(pattern=_SHA256_PATTERN)
    observed_at: datetime
    safe_error_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,95}$")
    exception_fingerprint: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    previous_event_sha256: str = Field(pattern=_SHA256_PATTERN)
    event_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def event_is_self_sealed(self) -> DiagnosisStageEventV02322:
        if self.status is DiagnosisStageStatusV02322.FAILED:
            if (
                self.stage is not DiagnosisPipelineStageV02322.FAILED
                or self.safe_error_code is None
                or self.exception_fingerprint is None
            ):
                raise ValueError("failed Diagnosis stage event differs")
        elif self.safe_error_code is not None or self.exception_fingerprint is not None:
            raise ValueError("non-failure Diagnosis stage event contains failure evidence")
        body = self.model_dump(mode="json", exclude={"event_sha256"})
        if self.event_sha256 != semantic_sha256_v22(body):
            raise ValueError("Diagnosis stage event digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> DiagnosisStageEventV02322:
        body = {
            "schema_version": "ecomsre.product.diagnosis-stage-event.v02322",
            **values,
        }
        normalized = cls.model_construct(
            **body,
            event_sha256="0" * 64,
        ).model_dump(mode="json", exclude={"event_sha256"})
        return cls.model_validate(
            {**body, "event_sha256": semantic_sha256_v22(normalized)}
        )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class DiagnosisStageJournalRepositoryV02322:
    def __init__(self, store: SqliteStoreV1) -> None:
        self.store = store

    def append(
        self,
        *,
        journal_id: str,
        job_id: str,
        incident_id: str,
        stage: DiagnosisPipelineStageV02322,
        status: DiagnosisStageStatusV02322,
        input_binding_sha256: str,
        output_artifact_sha256: str | None,
        source_code_sha256: str,
        observed_at: datetime | None = None,
        safe_error_code: str | None = None,
        exception_fingerprint: str | None = None,
    ) -> DiagnosisStageEventV02322:
        timestamp = datetime.now(UTC) if observed_at is None else observed_at
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT ordinal, payload_json, event_sha256 "
                    "FROM diagnosis_stage_events_v02322 WHERE job_id = ? "
                    "ORDER BY ordinal DESC LIMIT 1",
                    (job_id,),
                ).fetchone()
                if row is None:
                    ordinal = 1
                    previous = "0" * 64
                else:
                    prior = DiagnosisStageEventV02322.model_validate_json(
                        row["payload_json"]
                    )
                    if prior.stage in {
                        DiagnosisPipelineStageV02322.JOB_SUCCEEDED,
                        DiagnosisPipelineStageV02322.FAILED,
                    } and prior.status in {
                        DiagnosisStageStatusV02322.PASSED,
                        DiagnosisStageStatusV02322.FAILED,
                    }:
                        raise ValueError("Diagnosis stage journal is already terminal")
                    ordinal = int(row["ordinal"]) + 1
                    previous = str(row["event_sha256"])
                event = DiagnosisStageEventV02322.build(
                    journal_id=journal_id,
                    job_id=job_id,
                    incident_id=incident_id,
                    ordinal=ordinal,
                    stage=stage,
                    status=status,
                    input_binding_sha256=input_binding_sha256,
                    output_artifact_sha256=output_artifact_sha256,
                    source_code_sha256=source_code_sha256,
                    observed_at=timestamp,
                    safe_error_code=safe_error_code,
                    exception_fingerprint=exception_fingerprint,
                    previous_event_sha256=previous,
                )
                self._insert(connection, event)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return event

    def append_event(self, event: DiagnosisStageEventV02322) -> None:
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._insert(connection, event)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _insert(connection: Any, event: DiagnosisStageEventV02322) -> None:
        connection.execute(
            """INSERT INTO diagnosis_stage_events_v02322(
                job_id, incident_id, ordinal, stage, status, payload_json,
                event_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.job_id,
                event.incident_id,
                event.ordinal,
                event.stage.value,
                event.status.value,
                _json(event.model_dump(mode="json")),
                event.event_sha256,
                event.observed_at.isoformat(),
            ),
        )

    def list_events(self, job_id: str) -> tuple[DiagnosisStageEventV02322, ...]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM diagnosis_stage_events_v02322 "
                "WHERE job_id = ? ORDER BY ordinal",
                (job_id,),
            ).fetchall()
        events: list[DiagnosisStageEventV02322] = []
        for row in rows:
            event = DiagnosisStageEventV02322.model_validate_json(row["payload_json"])
            if (
                row["incident_id"] != event.incident_id
                or row["ordinal"] != event.ordinal
                or row["stage"] != event.stage.value
                or row["status"] != event.status.value
                or row["event_sha256"] != event.event_sha256
                or row["created_at"] != event.observed_at.isoformat()
            ):
                raise ValueError("Diagnosis stage journal row differs")
            events.append(event)
        return tuple(events)

    def verify(self, job_id: str) -> dict[str, object]:
        events = self.list_events(job_id)
        if not events:
            raise ValueError("Diagnosis stage journal is empty")
        journal_ids = {item.journal_id for item in events}
        incident_ids = {item.incident_id for item in events}
        if len(journal_ids) != 1 or len(incident_ids) != 1:
            raise ValueError("Diagnosis stage journal identity differs")
        previous = "0" * 64
        terminal_events: list[DiagnosisStageEventV02322] = []
        for expected_ordinal, event in enumerate(events, start=1):
            if (
                event.ordinal != expected_ordinal
                or event.previous_event_sha256 != previous
            ):
                raise ValueError("Diagnosis stage journal chain differs")
            previous = event.event_sha256
            if (
                event.stage is DiagnosisPipelineStageV02322.JOB_SUCCEEDED
                and event.status is DiagnosisStageStatusV02322.PASSED
            ) or (
                event.stage is DiagnosisPipelineStageV02322.FAILED
                and event.status is DiagnosisStageStatusV02322.FAILED
            ):
                terminal_events.append(event)
        if len(terminal_events) != 1 or terminal_events[0] != events[-1]:
            raise ValueError("Diagnosis stage journal terminal differs")
        terminal = terminal_events[0]
        return {
            "terminal": DIAGNOSIS_STAGE_JOURNAL_PASS_V02322,
            "job_id": job_id,
            "incident_id": terminal.incident_id,
            "event_count": len(events),
            "terminal_stage": terminal.stage.value,
            "journal_tail_sha256": terminal.event_sha256,
        }


__all__ = (
    "DIAGNOSIS_STAGE_JOURNAL_PASS_V02322",
    "DiagnosisPipelineStageV02322",
    "DiagnosisStageEventV02322",
    "DiagnosisStageJournalRepositoryV02322",
    "DiagnosisStageStatusV02322",
)
