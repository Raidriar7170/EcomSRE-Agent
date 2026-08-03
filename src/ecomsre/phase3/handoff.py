"""Read-only adapter from a completed Phase 2 trace into Phase 3."""

from __future__ import annotations

from enum import Enum

from pydantic import ValidationError

from ecomsre.phase1.contracts import Evidence, Incident
from ecomsre.phase1.evidence import EvidenceStore, EvidenceStoreError
from ecomsre.phase1.validator import (
    EvidenceValidationError,
    validate_rca_result,
)
from ecomsre.phase2.workflows import WorkflowRunTrace
from ecomsre.phase2.contracts import JudgeFinalResult
from ecomsre.phase3.contracts import DiagnosisHandoff


class HandoffErrorCode(str, Enum):
    INVALID_TRACE = "INVALID_TRACE"
    INCOMPLETE_TRACE = "INCOMPLETE_TRACE"
    INCIDENT_MISMATCH = "INCIDENT_MISMATCH"
    EVIDENCE_MISMATCH = "EVIDENCE_MISMATCH"


class HandoffError(ValueError):
    def __init__(self, code: HandoffErrorCode, detail: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}")


def resolve_current_run_supporting_evidence(
    *,
    handoff: DiagnosisHandoff,
    evidence_store: EvidenceStore,
) -> tuple[Evidence, ...]:
    """Resolve cited evidence against the exact current-run Evidence Store."""

    if type(evidence_store) is not EvidenceStore:
        raise HandoffError(
            HandoffErrorCode.EVIDENCE_MISMATCH,
            "evidence_store must be an exact EvidenceStore",
        )
    if evidence_store.run_id != handoff.run_id:
        raise HandoffError(
            HandoffErrorCode.EVIDENCE_MISMATCH,
            "Evidence Store belongs to another run",
        )
    resolved: list[Evidence] = []
    for reference in handoff.supporting_evidence_refs:
        try:
            resolved.append(evidence_store.resolve(reference))
        except EvidenceStoreError:
            continue
    return tuple(resolved)


def build_diagnosis_handoff_from_judge(
    *,
    final: JudgeFinalResult,
    evidence_store: EvidenceStore,
    incident: Incident,
) -> DiagnosisHandoff:
    """Build a handoff from the exact Phase 2 Judge output contract."""

    try:
        validated_final = JudgeFinalResult.model_validate(
            final.model_dump(mode="python")
        )
        validated_incident = Incident.model_validate(incident.model_dump(mode="python"))
        if evidence_store.run_id != validated_final.run_id:
            raise HandoffError(
                HandoffErrorCode.EVIDENCE_MISMATCH,
                "Judge output and Evidence Store runs differ",
            )
        if validated_final.incident_id != validated_incident.incident_id:
            raise HandoffError(
                HandoffErrorCode.INCIDENT_MISMATCH,
                "Judge output and incident identities differ",
            )
        rca = validate_rca_result(
            validated_final.rca_result,
            evidence_store,
            validated_incident,
        )
    except HandoffError:
        raise
    except (
        AttributeError,
        EvidenceStoreError,
        EvidenceValidationError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        raise HandoffError(
            HandoffErrorCode.EVIDENCE_MISMATCH,
            str(error),
        ) from error
    return DiagnosisHandoff(
        schema_version="phase3.diagnosis-handoff.v1",
        run_id=validated_final.run_id,
        incident_id=validated_final.incident_id,
        decision=rca.decision,
        root_service=rca.root_service,
        fault_mechanism=rca.fault_mechanism,
        supporting_evidence_refs=rca.supporting_evidence,
        missing_evidence=rca.missing_evidence,
    )


def build_diagnosis_handoff(
    *,
    trace: WorkflowRunTrace,
    incident: Incident,
) -> DiagnosisHandoff:
    """Freshly validate Phase 2 RCA and Evidence without changing its schema."""

    try:
        validated_trace = WorkflowRunTrace.model_validate(
            trace.model_dump(mode="python")
        )
        validated_incident = Incident.model_validate(incident.model_dump(mode="python"))
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise HandoffError(
            HandoffErrorCode.INVALID_TRACE,
            str(error),
        ) from error
    if (
        validated_trace.status != "COMPLETED"
        or validated_trace.final_rca is None
        or validated_trace.admitted_graph is None
    ):
        raise HandoffError(
            HandoffErrorCode.INCOMPLETE_TRACE,
            "Phase 3 requires a completed multi-agent Phase 2 trace",
        )
    graph = validated_trace.admitted_graph
    if (
        graph.run_id != validated_trace.run_id
        or graph.incident_id != validated_incident.incident_id
        or any(
            record.incident_id != validated_incident.incident_id
            for record in validated_trace.tool_call_records
        )
    ):
        raise HandoffError(
            HandoffErrorCode.INCIDENT_MISMATCH,
            "trace, graph, and incident identities differ",
        )

    store = EvidenceStore(validated_trace.run_id)
    seen: dict[str, object] = {}
    try:
        for record in validated_trace.tool_call_records:
            for item in record.evidence:
                prior = seen.get(item.evidence_ref)
                if prior is not None:
                    if prior != item:
                        raise HandoffError(
                            HandoffErrorCode.EVIDENCE_MISMATCH,
                            "duplicate Evidence reference has different content",
                        )
                    continue
                rebuilt = store.add(
                    source=item.source,
                    observation_type=item.observation_type,
                    attributes=item.attributes,
                    raw_artifact_ref=item.raw_artifact_ref,
                    raw_artifact_sha256=item.raw_artifact_sha256,
                    limitations=item.limitations,
                    summary=item.summary,
                    started_at=item.started_at,
                    ended_at=item.ended_at,
                    service=item.service,
                )
                if rebuilt != item:
                    raise HandoffError(
                        HandoffErrorCode.EVIDENCE_MISMATCH,
                        "reconstructed Evidence differs from the trace",
                    )
                seen[item.evidence_ref] = item
        rca = validate_rca_result(
            validated_trace.final_rca,
            store,
            validated_incident,
        )
    except HandoffError:
        raise
    except (EvidenceStoreError, EvidenceValidationError, ValueError) as error:
        raise HandoffError(
            HandoffErrorCode.EVIDENCE_MISMATCH,
            str(error),
        ) from error

    return DiagnosisHandoff(
        schema_version="phase3.diagnosis-handoff.v1",
        run_id=validated_trace.run_id,
        incident_id=validated_incident.incident_id,
        decision=rca.decision,
        root_service=rca.root_service,
        fault_mechanism=rca.fault_mechanism,
        supporting_evidence_refs=rca.supporting_evidence,
        missing_evidence=rca.missing_evidence,
    )
