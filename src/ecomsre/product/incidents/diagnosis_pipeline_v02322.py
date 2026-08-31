"""Stage-visible Diagnosis execution and bounded private failures."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import traceback
from typing import Any, TypeVar

from pydantic import ConfigDict, Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.incidents.diagnosis_stage_journal_v02322 import (
    DIAGNOSIS_STAGE_JOURNAL_PASS_V02322,
    DiagnosisPipelineStageV02322,
    DiagnosisStageJournalRepositoryV02322,
    DiagnosisStageStatusV02322,
)


PRIVATE_FAILURE_EVIDENCE_PASS_V02322 = (
    "ECOMSRE_PRODUCT_V02322_PRIVATE_FAILURE_EVIDENCE_PASS"
)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)\bauthorization\s*:\s*(?:bearer\s+)?[^\s,;]+"
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:api[_-]?key|credential|password|secret|token)"
    r"\s*(?::|=)\s*[^\s,;]+"
)
_T = TypeVar("_T")


class DiagnosisPipelineContextV02322(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "ecomsre.product.diagnosis-pipeline-context.v02322"
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    incident_sha256: str = Field(pattern=_SHA256_PATTERN)
    baseline_sha256: str = Field(pattern=_SHA256_PATTERN)
    identity_sha256: str = Field(pattern=_SHA256_PATTERN)
    capability_sha256: str = Field(pattern=_SHA256_PATTERN)
    environment_sha256: str = Field(pattern=_SHA256_PATTERN)
    context_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def context_is_sealed(self) -> DiagnosisPipelineContextV02322:
        body = self.model_dump(mode="json", exclude={"context_sha256"})
        if self.context_sha256 != semantic_sha256_v22(body):
            raise ValueError("Diagnosis pipeline context digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> DiagnosisPipelineContextV02322:
        body = {"schema_version": cls.model_fields["schema_version"].default, **values}
        return cls.model_validate(
            {**body, "context_sha256": semantic_sha256_v22(body)}
        )


class DiagnosisAcquisitionArtifactV02322(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "ecomsre.product.diagnosis-acquisition.v02322"
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    raw_outcomes_sha256: str = Field(pattern=_SHA256_PATTERN)
    memory_outcomes_sha256: str = Field(pattern=_SHA256_PATTERN)
    read_snapshots_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_coverage_sha256: str = Field(pattern=_SHA256_PATTERN)
    capability_observations_sha256: str = Field(pattern=_SHA256_PATTERN)
    limitation_candidates_sha256: str = Field(pattern=_SHA256_PATTERN)
    acquisition_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def acquisition_is_sealed(self) -> DiagnosisAcquisitionArtifactV02322:
        body = self.model_dump(mode="json", exclude={"acquisition_sha256"})
        if self.acquisition_sha256 != semantic_sha256_v22(body):
            raise ValueError("Diagnosis acquisition artifact digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> DiagnosisAcquisitionArtifactV02322:
        body = {"schema_version": cls.model_fields["schema_version"].default, **values}
        return cls.model_validate(
            {**body, "acquisition_sha256": semantic_sha256_v22(body)}
        )


class DiagnosisBridgeArtifactV02322(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "ecomsre.product.diagnosis-bridge.v02322"
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    diagnosis_id: str = Field(pattern=r"^diag-[0-9a-f]{24}$")
    result_sha256: str = Field(pattern=_SHA256_PATTERN)
    observations_sha256: str = Field(pattern=_SHA256_PATTERN)
    decision_trace_sha256: str = Field(pattern=_SHA256_PATTERN)
    bridge_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def bridge_is_sealed(self) -> DiagnosisBridgeArtifactV02322:
        body = self.model_dump(mode="json", exclude={"bridge_sha256"})
        if self.bridge_sha256 != semantic_sha256_v22(body):
            raise ValueError("Diagnosis bridge artifact digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> DiagnosisBridgeArtifactV02322:
        body = {"schema_version": cls.model_fields["schema_version"].default, **values}
        return cls.model_validate({**body, "bridge_sha256": semantic_sha256_v22(body)})


class DiagnosisPersistencePlanV02322(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "ecomsre.product.diagnosis-persistence-plan.v02322"
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    diagnosis_id: str = Field(pattern=r"^diag-[0-9a-f]{24}$")
    bridge_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_object_sha256_by_ref: dict[str, str]
    limitation_bindings_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_bundle_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    evidence_index_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    decision_trace_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    persistence_plan_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def persistence_plan_is_sealed(self) -> DiagnosisPersistencePlanV02322:
        if (
            tuple(sorted(self.evidence_object_sha256_by_ref))
            != tuple(self.evidence_object_sha256_by_ref)
            or any(
                re.fullmatch(_SHA256_PATTERN, value) is None
                for value in self.evidence_object_sha256_by_ref.values()
            )
        ):
            raise ValueError("Diagnosis persistence evidence map differs")
        body = self.model_dump(mode="json", exclude={"persistence_plan_sha256"})
        if self.persistence_plan_sha256 != semantic_sha256_v22(body):
            raise ValueError("Diagnosis persistence plan digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> DiagnosisPersistencePlanV02322:
        body = {"schema_version": cls.model_fields["schema_version"].default, **values}
        return cls.model_validate(
            {**body, "persistence_plan_sha256": semantic_sha256_v22(body)}
        )


class DiagnosisBoundedStackFrameV02322(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    file: str = Field(pattern=r"^src/ecomsre/product/[A-Za-z0-9_./-]+\.py$")
    function: str = Field(min_length=1, max_length=200)
    line_number: int = Field(ge=1)
    source_line_sha256: str = Field(pattern=_SHA256_PATTERN)


class DiagnosisPrivateFailureEnvelopeV02322(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "ecomsre.product.diagnosis-private-failure.v02322"
    failure_id: str = Field(pattern=r"^failure-[0-9a-f]{24}$")
    job_id: str = Field(pattern=r"^job-[0-9a-f]{24}$")
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    failing_stage: DiagnosisPipelineStageV02322
    last_passed_stage: DiagnosisPipelineStageV02322 | None
    exception_module: str = Field(min_length=1, max_length=200)
    exception_class: str = Field(min_length=1, max_length=200)
    bounded_message: str = Field(max_length=512)
    bounded_cause_chain: tuple[str, ...] = Field(max_length=5)
    bounded_stack_frames: tuple[DiagnosisBoundedStackFrameV02322, ...] = Field(
        max_length=12
    )
    traceback_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_code_sha256_by_frame: dict[str, str]
    job_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    incident_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    baseline_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    identity_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    capability_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    read_acquisition_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    bridge_output_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    prepared_evidence_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    journal_tail_sha256: str = Field(pattern=_SHA256_PATTERN)
    exception_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    created_at: datetime
    failure_envelope_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def envelope_is_safe_and_sealed(self) -> DiagnosisPrivateFailureEnvelopeV02322:
        serialized_private_text = " ".join(
            (self.bounded_message, *self.bounded_cause_chain)
        )
        if _AUTHORIZATION_PATTERN.search(
            serialized_private_text
        ) or _SECRET_ASSIGNMENT_PATTERN.search(serialized_private_text):
            raise ValueError("private failure envelope contains secret-bearing text")
        body = self.model_dump(mode="json", exclude={"failure_envelope_sha256"})
        if self.failure_envelope_sha256 != semantic_sha256_v22(body):
            raise ValueError("private failure envelope digest differs")
        return self


class DiagnosisPublicFailureProjectionV02322(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    safe_error_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,95}$")
    failure_stage: DiagnosisPipelineStageV02322
    exception_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    journal_tail_sha256: str = Field(pattern=_SHA256_PATTERN)


def _redact(value: str, *, maximum: int = 512) -> str:
    bounded = value.replace("\x00", " ").replace("\r", " ").replace("\n", " ")
    bounded = _AUTHORIZATION_PATTERN.sub("[REDACTED]", bounded)
    bounded = _SECRET_ASSIGNMENT_PATTERN.sub("[REDACTED]", bounded)
    return bounded[:maximum]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"type": type(value).__qualname__}


def _value_sha256(value: Any) -> str:
    return semantic_sha256_v22(_jsonable(value))


def _source_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _cause_chain(error: BaseException) -> tuple[str, ...]:
    causes: list[str] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and len(causes) < 5 and id(current) not in seen:
        seen.add(id(current))
        causes.append(
            _redact(
                f"{type(current).__module__}.{type(current).__qualname__}: {current}"
            )
        )
        current = current.__cause__ or current.__context__
    return tuple(causes)


def _product_stack_frames(
    error: BaseException,
) -> tuple[DiagnosisBoundedStackFrameV02322, ...]:
    frames: list[DiagnosisBoundedStackFrameV02322] = []
    for item in traceback.extract_tb(error.__traceback__):
        normalized = item.filename.replace("\\", "/")
        marker = "/src/ecomsre/product/"
        if marker not in normalized:
            continue
        relative = "src/ecomsre/product/" + normalized.split(marker, 1)[1]
        source_line = item.line or ""
        frames.append(
            DiagnosisBoundedStackFrameV02322(
                file=relative,
                function=item.name,
                line_number=item.lineno or 1,
                source_line_sha256=hashlib.sha256(
                    source_line.encode("utf-8")
                ).hexdigest(),
            )
        )
    return tuple(frames[-12:])


def _persist_private_envelope(
    data_root: Path,
    envelope: DiagnosisPrivateFailureEnvelopeV02322,
) -> Path:
    root = Path(data_root).expanduser().resolve()
    failure_root = root / "private" / "diagnosis-failures" / envelope.job_id
    failure_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(failure_root, 0o700)
    path = failure_root / f"{envelope.failure_id}.json"
    payload = (
        json.dumps(
            envelope.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != payload:
            raise ValueError("private failure envelope create-once conflict")
        return path
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)
    return path


class DiagnosisPipelineV02322:
    """One journal-bound Diagnosis execution context."""

    def __init__(
        self,
        journal: DiagnosisStageJournalRepositoryV02322,
        *,
        job_id: str,
        incident_id: str,
        observed_at: datetime | None = None,
        failure_injector: Callable[[DiagnosisPipelineStageV02322], None] | None = None,
    ) -> None:
        self.journal = journal
        self.job_id = job_id
        self.incident_id = incident_id
        self.observed_at = datetime.now(UTC) if observed_at is None else observed_at
        self.failure_injector = failure_injector
        self.journal_id = "journal-" + semantic_sha256_v22(
            {"job_id": job_id, "incident_id": incident_id}
        )[:24]
        self.last_passed_stage: DiagnosisPipelineStageV02322 | None = None
        self.failing_stage: DiagnosisPipelineStageV02322 | None = None
        self.output_sha256_by_stage: dict[DiagnosisPipelineStageV02322, str] = {}
        self.artifact_bindings: dict[str, str] = {}

    def bind_artifacts(self, **values: str | None) -> None:
        allowed = {
            "incident_sha256",
            "baseline_sha256",
            "identity_sha256",
            "capability_sha256",
            "read_acquisition_sha256",
            "bridge_output_sha256",
            "prepared_evidence_sha256",
        }
        if set(values) - allowed:
            raise ValueError("Diagnosis artifact binding name differs")
        for name, value in values.items():
            if value is None:
                continue
            if re.fullmatch(_SHA256_PATTERN, value) is None:
                raise ValueError("Diagnosis artifact binding digest differs")
            existing = self.artifact_bindings.get(name)
            if existing is not None and existing != value:
                raise ValueError("Diagnosis artifact binding is immutable")
            self.artifact_bindings[name] = value

    def run(
        self,
        stage: DiagnosisPipelineStageV02322,
        *,
        input_binding_sha256: str,
        operation: Callable[[], _T],
    ) -> _T:
        if stage is DiagnosisPipelineStageV02322.FAILED:
            raise ValueError("FAILED is a terminal event, not an executable stage")
        self.failing_stage = stage
        source_sha256 = _source_sha256()
        self.journal.append(
            journal_id=self.journal_id,
            job_id=self.job_id,
            incident_id=self.incident_id,
            stage=stage,
            status=DiagnosisStageStatusV02322.STARTED,
            input_binding_sha256=input_binding_sha256,
            output_artifact_sha256=None,
            source_code_sha256=source_sha256,
            observed_at=self.observed_at,
        )
        if self.failure_injector is not None:
            self.failure_injector(stage)
        result = operation()
        output_sha256 = _value_sha256(result)
        self.journal.append(
            journal_id=self.journal_id,
            job_id=self.job_id,
            incident_id=self.incident_id,
            stage=stage,
            status=DiagnosisStageStatusV02322.PASSED,
            input_binding_sha256=input_binding_sha256,
            output_artifact_sha256=output_sha256,
            source_code_sha256=source_sha256,
            observed_at=self.observed_at,
        )
        self.last_passed_stage = stage
        self.failing_stage = None
        self.output_sha256_by_stage[stage] = output_sha256
        return result

    def complete_success(self, *, result: Mapping[str, Any]) -> None:
        result_sha256 = _value_sha256(result)
        self.run(
            DiagnosisPipelineStageV02322.JOB_RESULT_PREPARED,
            input_binding_sha256=result_sha256,
            operation=lambda: dict(result),
        )
        self.run(
            DiagnosisPipelineStageV02322.JOB_SUCCEEDED,
            input_binding_sha256=result_sha256,
            operation=lambda: {"status": "SUCCEEDED"},
        )

    def capture_failure(
        self,
        error: BaseException,
        *,
        data_root: Path,
        job_payload: Mapping[str, Any],
        artifact_bindings: Mapping[str, str | None] | None = None,
        safe_error_code: str = "INTERNAL_CONTRACT_FAILURE",
    ) -> tuple[
        DiagnosisPublicFailureProjectionV02322,
        DiagnosisPrivateFailureEnvelopeV02322,
        Path,
    ]:
        failing_stage = self.failing_stage or DiagnosisPipelineStageV02322.JOB_CLAIMED
        events = self.journal.list_events(self.job_id)
        journal_tail_sha256 = events[-1].event_sha256 if events else "0" * 64
        frames = _product_stack_frames(error)
        frame_hashes = {
            f"{frame.file}:{frame.function}:{frame.line_number}": frame.source_line_sha256
            for frame in frames
        }
        traceback_sha256 = semantic_sha256_v22(
            {
                "frames": [item.model_dump(mode="json") for item in frames],
                "causes": list(_cause_chain(error)),
            }
        )
        exception_fingerprint = semantic_sha256_v22(
            {
                "module": type(error).__module__,
                "class": type(error).__qualname__,
                "traceback_sha256": traceback_sha256,
            }
        )
        failure_id = "failure-" + semantic_sha256_v22(
            {
                "job_id": self.job_id,
                "failing_stage": failing_stage.value,
                "exception_fingerprint": exception_fingerprint,
                "journal_tail_sha256": journal_tail_sha256,
            }
        )[:24]
        bindings = {**self.artifact_bindings, **dict(artifact_bindings or {})}
        body: dict[str, Any] = {
            "schema_version": "ecomsre.product.diagnosis-private-failure.v02322",
            "failure_id": failure_id,
            "job_id": self.job_id,
            "incident_id": self.incident_id,
            "failing_stage": failing_stage,
            "last_passed_stage": self.last_passed_stage,
            "exception_module": type(error).__module__,
            "exception_class": type(error).__qualname__,
            "bounded_message": _redact(str(error)),
            "bounded_cause_chain": _cause_chain(error),
            "bounded_stack_frames": frames,
            "traceback_sha256": traceback_sha256,
            "source_code_sha256_by_frame": frame_hashes,
            "job_payload_sha256": _value_sha256(job_payload),
            "incident_sha256": bindings.get("incident_sha256"),
            "baseline_sha256": bindings.get("baseline_sha256"),
            "identity_sha256": bindings.get("identity_sha256"),
            "capability_sha256": bindings.get("capability_sha256"),
            "read_acquisition_sha256": bindings.get("read_acquisition_sha256"),
            "bridge_output_sha256": bindings.get("bridge_output_sha256"),
            "prepared_evidence_sha256": bindings.get("prepared_evidence_sha256"),
            "journal_tail_sha256": journal_tail_sha256,
            "exception_fingerprint": exception_fingerprint,
            "created_at": self.observed_at,
        }
        normalized = DiagnosisPrivateFailureEnvelopeV02322.model_construct(
            **body,
            failure_envelope_sha256="0" * 64,
        ).model_dump(mode="json", exclude={"failure_envelope_sha256"})
        envelope = DiagnosisPrivateFailureEnvelopeV02322.model_validate(
            {**body, "failure_envelope_sha256": semantic_sha256_v22(normalized)}
        )
        path = _persist_private_envelope(data_root, envelope)
        failed = self.journal.append(
            journal_id=self.journal_id,
            job_id=self.job_id,
            incident_id=self.incident_id,
            stage=DiagnosisPipelineStageV02322.FAILED,
            status=DiagnosisStageStatusV02322.FAILED,
            input_binding_sha256=journal_tail_sha256,
            output_artifact_sha256=envelope.failure_envelope_sha256,
            source_code_sha256=_source_sha256(),
            observed_at=self.observed_at,
            safe_error_code=safe_error_code,
            exception_fingerprint=exception_fingerprint,
        )
        projection = DiagnosisPublicFailureProjectionV02322(
            safe_error_code=safe_error_code,
            failure_stage=failing_stage,
            exception_fingerprint=exception_fingerprint,
            journal_tail_sha256=failed.event_sha256,
        )
        return projection, envelope, path


__all__ = (
    "DIAGNOSIS_STAGE_JOURNAL_PASS_V02322",
    "PRIVATE_FAILURE_EVIDENCE_PASS_V02322",
    "DiagnosisAcquisitionArtifactV02322",
    "DiagnosisBoundedStackFrameV02322",
    "DiagnosisBridgeArtifactV02322",
    "DiagnosisPipelineStageV02322",
    "DiagnosisPipelineContextV02322",
    "DiagnosisPipelineV02322",
    "DiagnosisPersistencePlanV02322",
    "DiagnosisPrivateFailureEnvelopeV02322",
    "DiagnosisPublicFailureProjectionV02322",
)
