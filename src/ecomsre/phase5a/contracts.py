"""Strict versioned contracts for Phase 5A diagnosis quality v2."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    Strict,
    StrictFloat,
    StringConstraints,
    field_validator,
    model_validator,
)

from ecomsre.phase1.contracts import (
    MAX_CAUSAL_CHAIN_ITEMS,
    MAX_EVIDENCE_REFS,
    MAX_ID_LENGTH,
    MAX_MISSING_EVIDENCE_ITEMS,
    MAX_SERVICE_LENGTH,
    MAX_SLI_LENGTH,
    MAX_TEXT_ENTRY_LENGTH,
    EvidenceSource,
    RecommendedNextAction,
    _reject_evaluator_markers,
    _reject_executable_text,
    _trimmed,
    _validate_evidence_ref,
)
from ecomsre.phase2.contracts import SpecialistRole


IdentifierV2 = Annotated[
    str,
    Strict(),
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_ID_LENGTH,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
RunIdV2 = Annotated[str, Strict(), StringConstraints(pattern=r"^[0-9a-f]{32}$")]


class Phase5AModel(BaseModel):
    """Immutable strict Phase 5A record with evaluator-marker rejection."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        strict=True,
    )

    @model_validator(mode="before")
    @classmethod
    def reject_evaluator_markers(cls, value: object) -> object:
        _reject_evaluator_markers(value)
        return value


class UnifiedMechanismV2(str, Enum):
    RUNTIME_CONFIGURATION_FAILURE = "runtime_configuration_failure"
    REQUEST_PROCESSING_FAILURE = "request_processing_failure"
    CACHE_BACKEND_TIMEOUT = "cache_backend_timeout"
    FEATURE_FRESHNESS_LAG = "feature_freshness_lag"
    MODEL_FEATURE_SCHEMA_MISMATCH = "model_feature_schema_mismatch"
    RANKING_CONFIGURATION_FAILURE = "ranking_configuration_failure"


class ObservationsStatusV2(str, Enum):
    AVAILABLE = "AVAILABLE"
    EMPTY = "EMPTY"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    QUERY_FAILED = "QUERY_FAILED"


class DiagnosisDecisionV2(str, Enum):
    RCA_CONFIRMED = "RCA_CONFIRMED"
    NEED_MORE_EVIDENCE = "NEED_MORE_EVIDENCE"
    ABSTAIN = "ABSTAIN"


def _safe_text(value: object, *, field_name: str, maximum: int) -> str:
    bounded = _trimmed(value, field_name=field_name, maximum=maximum)
    return _reject_executable_text(bounded, field_name=field_name)


def _safe_entries(
    values: tuple[str, ...],
    *,
    field_name: str,
) -> tuple[str, ...]:
    return tuple(
        _safe_text(value, field_name=field_name, maximum=MAX_TEXT_ENTRY_LENGTH)
        for value in values
    )


def _evidence_refs(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    validated = tuple(_validate_evidence_ref(value) for value in values)
    if len(validated) != len(set(validated)):
        raise ValueError(f"{label} contains duplicate evidence references")
    return validated


def _require_current_run(
    values: tuple[str, ...],
    *,
    run_id: str,
    label: str,
) -> None:
    if any(reference.split("/")[2] != run_id for reference in values):
        raise ValueError(f"{label} contains a reference outside the current run")


class MechanismCandidateV2(Phase5AModel):
    schema_version: Literal["phase5a.mechanism-candidate.v2"]
    candidate_id: IdentifierV2
    run_id: RunIdV2
    root_service: str = Field(min_length=1, max_length=MAX_SERVICE_LENGTH)
    fault_mechanism: UnifiedMechanismV2
    claim: str = Field(min_length=1, max_length=1000)
    supporting_evidence: tuple[str, ...] = Field(max_length=MAX_EVIDENCE_REFS)
    contradicting_evidence: tuple[str, ...] = Field(max_length=MAX_EVIDENCE_REFS)
    missing_evidence: tuple[str, ...] = Field(
        max_length=MAX_MISSING_EVIDENCE_ITEMS
    )
    confidence: StrictFloat = Field(ge=0, le=1)

    @field_validator("root_service", mode="before")
    @classmethod
    def require_root_service(cls, value: object) -> str:
        return _trimmed(value, field_name="root_service", maximum=MAX_SERVICE_LENGTH)

    @field_validator("claim", mode="before")
    @classmethod
    def require_safe_claim(cls, value: object) -> str:
        return _safe_text(value, field_name="claim", maximum=1000)

    @field_validator("supporting_evidence", "contradicting_evidence")
    @classmethod
    def require_valid_evidence_refs(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _evidence_refs(values, label="candidate evidence")

    @field_validator("missing_evidence")
    @classmethod
    def require_safe_missing(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_entries(values, field_name="missing_evidence")

    @model_validator(mode="after")
    def require_bounded_evidence_claim(self) -> MechanismCandidateV2:
        if set(self.supporting_evidence).intersection(self.contradicting_evidence):
            raise ValueError("evidence cannot both support and contradict a candidate")
        _require_current_run(
            self.supporting_evidence + self.contradicting_evidence,
            run_id=self.run_id,
            label="candidate evidence",
        )
        return self


_ROLE_BY_SOURCE = {
    EvidenceSource.METRICS: SpecialistRole.METRICS_AGENT,
    EvidenceSource.LOGS: SpecialistRole.LOGS_AGENT,
    EvidenceSource.TRACES: SpecialistRole.TRACE_AGENT,
    EvidenceSource.CHANGES: SpecialistRole.CHANGE_AGENT,
}


class SpecialistFindingV2(Phase5AModel):
    schema_version: Literal["phase5a.specialist-finding.v2"]
    finding_id: IdentifierV2
    run_id: RunIdV2
    incident_id: IdentifierV2
    plan_id: IdentifierV2
    node_id: IdentifierV2
    source: EvidenceSource
    specialist_role: SpecialistRole
    observations_status: ObservationsStatusV2
    candidates: tuple[MechanismCandidateV2, ...] = Field(max_length=8)
    supporting_evidence: tuple[str, ...] = Field(max_length=MAX_EVIDENCE_REFS)
    contradicting_evidence: tuple[str, ...] = Field(max_length=MAX_EVIDENCE_REFS)
    missing_evidence: tuple[str, ...] = Field(
        max_length=MAX_MISSING_EVIDENCE_ITEMS
    )
    confidence: StrictFloat = Field(ge=0, le=1)
    finding_rationale: str = Field(min_length=1, max_length=1000)

    @field_validator("supporting_evidence", "contradicting_evidence")
    @classmethod
    def require_valid_evidence_refs(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _evidence_refs(values, label="finding evidence")

    @field_validator("missing_evidence")
    @classmethod
    def require_safe_missing(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_entries(values, field_name="missing_evidence")

    @field_validator("finding_rationale", mode="before")
    @classmethod
    def require_safe_rationale(cls, value: object) -> str:
        return _safe_text(value, field_name="finding_rationale", maximum=1000)

    @model_validator(mode="after")
    def require_finding_scope(self) -> SpecialistFindingV2:
        if self.specialist_role is not _ROLE_BY_SOURCE[self.source]:
            raise ValueError("Specialist role does not match the evidence source")
        if set(self.supporting_evidence).intersection(self.contradicting_evidence):
            raise ValueError("evidence cannot both support and contradict a finding")
        all_refs = self.supporting_evidence + self.contradicting_evidence
        _require_current_run(all_refs, run_id=self.run_id, label="finding evidence")
        candidate_refs: set[str] = set()
        for candidate in self.candidates:
            if candidate.run_id != self.run_id:
                raise ValueError("candidate is outside the finding run")
            candidate_refs.update(candidate.supporting_evidence)
            candidate_refs.update(candidate.contradicting_evidence)
        if not candidate_refs.issubset(set(all_refs)):
            raise ValueError("candidate evidence is outside the finding projection")
        if (
            self.observations_status is not ObservationsStatusV2.AVAILABLE
            and not self.missing_evidence
        ):
            raise ValueError("non-available observations require missing evidence")
        if self.observations_status is not ObservationsStatusV2.AVAILABLE and (
            self.candidates
            or self.supporting_evidence
            or self.contradicting_evidence
        ):
            raise ValueError("non-available observations cannot carry evidence claims")
        return self


class DiagnosisResultV2(Phase5AModel):
    schema_version: Literal["phase5a.diagnosis-result.v2"]
    run_id: RunIdV2
    decision: DiagnosisDecisionV2
    root_service: str | None = Field(default=None, max_length=MAX_SERVICE_LENGTH)
    fault_mechanism: UnifiedMechanismV2 | None = None
    causal_chain: tuple[str, ...] = Field(max_length=MAX_CAUSAL_CHAIN_ITEMS)
    affected_sli: str | None = Field(default=None, max_length=MAX_SLI_LENGTH)
    supporting_evidence: tuple[str, ...] = Field(max_length=MAX_EVIDENCE_REFS)
    contradicting_evidence: tuple[str, ...] = Field(max_length=MAX_EVIDENCE_REFS)
    missing_evidence: tuple[str, ...] = Field(
        max_length=MAX_MISSING_EVIDENCE_ITEMS
    )
    confidence: StrictFloat = Field(ge=0, le=1)
    decision_rationale: str = Field(min_length=1, max_length=1000)
    recommended_next_action: RecommendedNextAction

    @field_validator("root_service", "affected_sli", mode="before")
    @classmethod
    def require_optional_claim(cls, value: object | None) -> str | None:
        if value is None:
            return None
        return _trimmed(value, field_name="diagnosis claim")

    @field_validator("causal_chain", "missing_evidence")
    @classmethod
    def require_safe_entries(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_entries(values, field_name="diagnosis explanation")

    @field_validator("supporting_evidence", "contradicting_evidence")
    @classmethod
    def require_valid_evidence_refs(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _evidence_refs(values, label="diagnosis evidence")

    @field_validator("decision_rationale", mode="before")
    @classmethod
    def require_safe_rationale(cls, value: object) -> str:
        return _safe_text(value, field_name="decision_rationale", maximum=1000)

    @model_validator(mode="after")
    def require_decision_semantics(self) -> DiagnosisResultV2:
        if set(self.supporting_evidence).intersection(self.contradicting_evidence):
            raise ValueError("evidence cannot both support and contradict a diagnosis")
        refs = self.supporting_evidence + self.contradicting_evidence
        _require_current_run(
            refs,
            run_id=self.run_id,
            label="diagnosis evidence",
        )

        if self.decision is DiagnosisDecisionV2.RCA_CONFIRMED:
            if self.root_service is None or self.fault_mechanism is None:
                raise ValueError("confirmed diagnosis requires one root and mechanism")
            if not self.causal_chain or self.affected_sli is None:
                raise ValueError("confirmed diagnosis requires one causal SLI chain")
            if len(self.supporting_evidence) < 2:
                raise ValueError("confirmed diagnosis requires two evidence references")
            if len({ref.split("/")[3] for ref in self.supporting_evidence}) < 2:
                raise ValueError("confirmed diagnosis requires two evidence sources")
            if self.missing_evidence:
                raise ValueError("confirmed diagnosis cannot retain evidence gaps")
        elif self.decision is DiagnosisDecisionV2.NEED_MORE_EVIDENCE:
            if self.root_service is not None or self.fault_mechanism is not None:
                raise ValueError("need-more cannot claim a root or mechanism")
            if self.causal_chain:
                raise ValueError("need-more cannot claim a causal chain")
            if not self.missing_evidence:
                raise ValueError("need-more requires concrete missing evidence")
        else:
            if self.root_service is not None or self.fault_mechanism is not None:
                raise ValueError("abstain cannot claim a root or mechanism")
            if self.causal_chain or self.supporting_evidence or self.missing_evidence:
                raise ValueError("abstain cannot carry causal support or evidence gaps")
        return self
