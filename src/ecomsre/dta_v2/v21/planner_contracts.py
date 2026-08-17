"""Evidence-guided Planner decisions and immutable trace contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, cast

from pydantic import Field, StrictInt, field_validator, model_validator

from ecomsre.dta_v2.agent_contracts import ProviderUsage
from ecomsre.dta_v2.tool_contracts import ReadToolRequest, ToolName
from ecomsre.dta_v2.v21.contracts import (
    DtaDiagnosisV21,
    DtaModelV21,
    EvidenceSourceV21,
    FaultDomainV21,
    FaultMechanismV21,
    IdentifierV21,
    Sha256V21,
    TerminalV21,
    semantic_sha256,
    validate_evidence_refs,
)


_SOURCE_BY_TOOL = {
    ToolName.QUERY_METRICS: EvidenceSourceV21.METRICS,
    ToolName.SEARCH_LOGS: EvidenceSourceV21.LOGS,
    ToolName.QUERY_TRACE_NEIGHBORHOOD: EvidenceSourceV21.TRACES,
    ToolName.INSPECT_SERVICE_RUNTIME: EvidenceSourceV21.RUNTIME,
    ToolName.INSPECT_RESOURCE_USAGE: EvidenceSourceV21.RESOURCES,
}
_SOURCE_ORDER = {source: index for index, source in enumerate(EvidenceSourceV21)}


class HypothesisStatusV21(str, Enum):
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"


class PlannerNextStepV21(str, Enum):
    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"
    SUBMIT_DIAGNOSIS = "SUBMIT_DIAGNOSIS"
    ABSTAIN = "ABSTAIN"


class DiagnosticHypothesisV21(DtaModelV21):
    hypothesis_id: IdentifierV21
    root_service: IdentifierV21
    fault_domain: FaultDomainV21
    fault_mechanism: FaultMechanismV21
    status: HypothesisStatusV21
    supporting_evidence_refs: tuple[str, ...] = Field(max_length=32)
    contradicting_evidence_refs: tuple[str, ...] = Field(max_length=32)
    unresolved_evidence_sources: tuple[EvidenceSourceV21, ...] = Field(max_length=6)

    @model_validator(mode="after")
    def require_canonical_hypothesis(self) -> DiagnosticHypothesisV21:
        if set(self.supporting_evidence_refs).intersection(
            self.contradicting_evidence_refs
        ):
            raise ValueError("hypothesis evidence cannot both support and contradict")
        if len(self.unresolved_evidence_sources) != len(
            set(self.unresolved_evidence_sources)
        ) or self.unresolved_evidence_sources != tuple(
            sorted(self.unresolved_evidence_sources, key=_SOURCE_ORDER.__getitem__)
        ):
            raise ValueError("hypothesis gaps are not canonical and unique")
        if self.status is HypothesisStatusV21.REJECTED and self.unresolved_evidence_sources:
            raise ValueError("rejected hypothesis retains unresolved evidence gaps")
        return self


class EvidencePlanDecisionV21(DtaModelV21):
    schema_version: Literal["dta-v21.evidence-plan-decision.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    turn_ordinal: StrictInt = Field(ge=1, le=5)
    hypotheses: tuple[DiagnosticHypothesisV21, ...] = Field(max_length=3)
    next_step: PlannerNextStepV21
    evidence_gap_sources: tuple[EvidenceSourceV21, ...] = Field(max_length=6)
    read_request: ReadToolRequest | None
    diagnosis: DtaDiagnosisV21 | None
    bounded_rationale: str = Field(min_length=1, max_length=1000)
    decision_sha256: Sha256V21

    @field_validator("bounded_rationale", mode="before")
    @classmethod
    def require_bounded_rationale(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Planner rationale must be nonempty text")
        if len(value.strip()) > 1000:
            raise ValueError("Planner rationale exceeds 1000 characters")
        return value.strip()

    @model_validator(mode="after")
    def require_plan_semantics(self) -> EvidencePlanDecisionV21:
        ids = tuple(item.hypothesis_id for item in self.hypotheses)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise ValueError("Planner hypotheses are not canonical and unique")
        for hypothesis in self.hypotheses:
            validate_evidence_refs(
                hypothesis.supporting_evidence_refs,
                run_id=self.run_id,
                label="hypothesis supporting evidence",
            )
            validate_evidence_refs(
                hypothesis.contradicting_evidence_refs,
                run_id=self.run_id,
                label="hypothesis contradicting evidence",
            )
        if len(self.evidence_gap_sources) != len(set(self.evidence_gap_sources)) or (
            self.evidence_gap_sources
            != tuple(sorted(self.evidence_gap_sources, key=_SOURCE_ORDER.__getitem__))
        ):
            raise ValueError("Planner evidence gaps are not canonical and unique")
        active = tuple(
            item for item in self.hypotheses if item.status is HypothesisStatusV21.ACTIVE
        )
        if self.next_step is PlannerNextStepV21.REQUEST_EVIDENCE:
            if not active:
                raise ValueError("request requires at least one active hypothesis")
            if self.read_request is None or self.diagnosis is not None:
                raise ValueError("request plan has an invalid semantic output")
            source = _SOURCE_BY_TOOL[self.read_request.tool]
            if source not in self.evidence_gap_sources or not any(
                source in item.unresolved_evidence_sources for item in active
            ):
                raise ValueError("request source is not an active unresolved gap")
        elif self.next_step is PlannerNextStepV21.SUBMIT_DIAGNOSIS:
            if self.read_request is not None or self.diagnosis is None:
                raise ValueError("submit plan must carry exactly one Diagnosis")
            if self.diagnosis.run_id != self.run_id:
                raise ValueError("Planner Diagnosis belongs to another run")
            if self.diagnosis.terminal is not TerminalV21.COMPLETED:
                raise ValueError("submit plan must carry a completed Diagnosis")
        elif self.read_request is not None or self.diagnosis is not None:
            raise ValueError("abstain plan cannot carry a request or Diagnosis")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"decision_sha256"})
        )
        if self.decision_sha256 != expected:
            raise ValueError("Planner decision digest does not bind the decision")
        return self


def build_evidence_plan_decision_v21(
    *,
    run_id: str,
    turn_ordinal: int,
    hypotheses: tuple[DiagnosticHypothesisV21, ...],
    next_step: PlannerNextStepV21,
    evidence_gap_sources: tuple[EvidenceSourceV21, ...],
    read_request: ReadToolRequest | None,
    diagnosis: DtaDiagnosisV21 | None,
    bounded_rationale: str,
) -> EvidencePlanDecisionV21:
    ordered_hypotheses = tuple(sorted(hypotheses, key=lambda item: item.hypothesis_id))
    ordered_gaps = tuple(sorted(evidence_gap_sources, key=_SOURCE_ORDER.__getitem__))
    payload: dict[str, object] = {
        "schema_version": "dta-v21.evidence-plan-decision.v1",
        "run_id": run_id,
        "turn_ordinal": turn_ordinal,
        "hypotheses": ordered_hypotheses,
        "next_step": next_step,
        "evidence_gap_sources": ordered_gaps,
        "read_request": read_request,
        "diagnosis": diagnosis,
        "bounded_rationale": bounded_rationale,
    }
    draft = cast(Any, EvidencePlanDecisionV21).model_construct(
        **payload, decision_sha256="0" * 64
    )
    return EvidencePlanDecisionV21.model_validate(
        {
            **payload,
            "decision_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"decision_sha256"})
            ),
        }
    )


class PlannerTraceEntryV21(DtaModelV21):
    schema_version: Literal["dta-v21.planner-trace-entry.v1"]
    turn_ordinal: StrictInt = Field(ge=1, le=5)
    prior_evidence_index_sha256: Sha256V21
    decision: EvidencePlanDecisionV21
    resulting_observation_ref: str | None
    resulting_observation_sha256: Sha256V21 | None
    raw_provider_response_sha256: Sha256V21
    usage: ProviderUsage
    monotonic_latency_ms: StrictInt = Field(ge=0)
    semantic_sha256: Sha256V21

    @model_validator(mode="after")
    def require_trace_binding(self) -> PlannerTraceEntryV21:
        if self.turn_ordinal != self.decision.turn_ordinal:
            raise ValueError("Planner trace and decision ordinals differ")
        if (self.resulting_observation_ref is None) != (
            self.resulting_observation_sha256 is None
        ):
            raise ValueError("Planner trace observation binding is partial")
        expects_observation = (
            self.decision.next_step is PlannerNextStepV21.REQUEST_EVIDENCE
        )
        if expects_observation != (self.resulting_observation_ref is not None):
            raise ValueError("Planner trace does not bind the decision result")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"semantic_sha256"})
        )
        if self.semantic_sha256 != expected:
            raise ValueError("Planner trace digest differs")
        return self


__all__ = (
    "DiagnosticHypothesisV21",
    "EvidencePlanDecisionV21",
    "HypothesisStatusV21",
    "PlannerNextStepV21",
    "PlannerTraceEntryV21",
    "build_evidence_plan_decision_v21",
)
