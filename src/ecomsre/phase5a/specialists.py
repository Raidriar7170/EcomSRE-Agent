"""Mechanism-level v2 finding policy for the four existing Specialists."""

from __future__ import annotations

import hashlib

from ecomsre.phase1.contracts import Evidence, EvidenceSource, Incident
from ecomsre.phase2.contracts import SpecialistRole
from ecomsre.phase5a.contracts import (
    MechanismCandidateV2,
    ObservationsStatusV2,
    SpecialistFindingV2,
    UnifiedMechanismV2,
)
from ecomsre.phase5a.semantics import (
    classify_evidence_candidate,
    evidence_contradicts_candidate,
    evidence_supports_candidate,
    is_anomalous_service_signal,
    is_normal_sli_signal,
)


def _identity(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:16]


def _status_gap(source: EvidenceSource, status: ObservationsStatusV2) -> str:
    label = source.value
    return {
        ObservationsStatusV2.EMPTY: (
            f"{label} returned no observations for the bounded window."
        ),
        ObservationsStatusV2.SOURCE_UNAVAILABLE: (
            f"{label} source was unavailable for the bounded window."
        ),
        ObservationsStatusV2.QUERY_FAILED: (
            f"{label} query did not return a usable observation set."
        ),
        ObservationsStatusV2.AVAILABLE: (
            f"{label} did not provide mechanism-specific evidence."
        ),
    }[status]


def build_specialist_finding(
    *,
    run_id: str,
    incident: Incident,
    plan_id: str,
    node_id: str,
    source: EvidenceSource,
    specialist_role: SpecialistRole,
    observations_status: ObservationsStatusV2,
    evidence: tuple[Evidence, ...],
) -> SpecialistFindingV2:
    """Build a deterministic mechanism-level finding from one source view."""

    ordered = tuple(sorted(evidence, key=lambda item: item.evidence_ref))
    if any(item.run_id != run_id or item.source is not source for item in ordered):
        raise ValueError("Specialist evidence must use the current run and source")
    if observations_status is ObservationsStatusV2.AVAILABLE and not ordered:
        raise ValueError("AVAILABLE observations require at least one Evidence record")
    if observations_status is not ObservationsStatusV2.AVAILABLE and ordered:
        raise ValueError("non-available observations cannot carry Evidence records")

    grouped: dict[tuple[str, UnifiedMechanismV2], list[Evidence]] = {}
    for item in ordered:
        classified = classify_evidence_candidate(item)
        if classified is not None:
            grouped.setdefault(classified, []).append(item)

    candidates: list[MechanismCandidateV2] = []
    all_supporting: set[str] = set()
    all_contradicting: set[str] = set()
    for (service, mechanism), items in sorted(
        grouped.items(),
        key=lambda entry: (entry[0][0], entry[0][1].value),
    ):
        supporting = tuple(
            item.evidence_ref
            for item in items
            if evidence_supports_candidate(
                item,
                root_service=service,
                fault_mechanism=mechanism,
            )
        )
        contradicting = tuple(
            item.evidence_ref
            for item in ordered
            if evidence_contradicts_candidate(
                item,
                root_service=service,
                fault_mechanism=mechanism,
            )
            and item.evidence_ref not in supporting
        )
        all_supporting.update(supporting)
        all_contradicting.update(contradicting)
        candidates.append(
            MechanismCandidateV2(
                schema_version="phase5a.mechanism-candidate.v2",
                candidate_id=(
                    f"candidate-{_identity(run_id, node_id, service, mechanism.value)}"
                ),
                run_id=run_id,
                root_service=service,
                fault_mechanism=mechanism,
                claim=(
                    f"Bounded {source.value} observations support "
                    f"{mechanism.value} in {service}."
                ),
                supporting_evidence=supporting,
                contradicting_evidence=contradicting,
                missing_evidence=(),
                confidence=0.75 if supporting else 0.2,
            )
        )

    all_contradicting.update(
        item.evidence_ref
        for item in ordered
        if is_normal_sli_signal(item)
        and item.evidence_ref not in all_supporting
    )
    mechanism_missing = observations_status is not ObservationsStatusV2.AVAILABLE or (
        not candidates and any(is_anomalous_service_signal(item) for item in ordered)
    )
    missing = (
        (_status_gap(source, observations_status),)
        if mechanism_missing
        else ()
    )
    rationale = (
        f"The {source.value} finding classifies only native bounded observations "
        "and preserves explicit contradiction and missing-evidence state."
    )
    finding_id = f"finding-{_identity(run_id, plan_id, node_id, source.value)}"
    return SpecialistFindingV2(
        schema_version="phase5a.specialist-finding.v2",
        finding_id=finding_id,
        run_id=run_id,
        incident_id=incident.incident_id,
        plan_id=plan_id,
        node_id=node_id,
        source=source,
        specialist_role=specialist_role,
        observations_status=observations_status,
        candidates=tuple(candidates),
        supporting_evidence=tuple(sorted(all_supporting)),
        contradicting_evidence=tuple(sorted(all_contradicting)),
        missing_evidence=missing,
        confidence=(
            max((candidate.confidence for candidate in candidates), default=0.0)
        ),
        finding_rationale=rationale,
    )
