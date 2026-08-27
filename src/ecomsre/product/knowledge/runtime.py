"""Deterministic fingerprinting, clustering, clause mining, and shadow gates."""

from __future__ import annotations

from typing import Iterable

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.knowledge.contracts import (
    CandidateClauseV1,
    CandidateClauseSetV1,
    ClauseMiningResultV1,
    FingerprintObservationV1,
    IncidentFingerprintV1,
    PredicateCellStateV1,
    PredicateMatrixRowKindV1,
    PredicateMatrixRowV1,
    PredicateMatrixV1,
    ShadowCaseOriginV1,
    ShadowCaseOutcomeV1,
    ShadowEvaluationV1,
    ShadowEvaluationStratumV1,
)


CLUSTER_ASSIGNMENT_THRESHOLD_V1 = 0.65
DEFAULT_BEAM_WIDTH_V1 = 20
_DIRECT_STATE_ALLOWLIST_V1 = frozenset({"core:RUNTIME_NOT_RUNNING"})


def _canonical(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _mining_result(
    *,
    matrix: PredicateMatrixV1,
    status: str,
    candidates: tuple[CandidateClauseV1, ...],
    beam_width: int,
) -> ClauseMiningResultV1:
    canonical = tuple(
        sorted(
            candidates,
            key=lambda item: (
                -item.score,
                abs(len(item.evidence_sources) - 2),
                item.predicate_count,
                item.candidate_id,
            ),
        )
    )
    payload = {
        "schema_version": "ecomsre.product.candidate-clause-set.v1",
        "environment_id": matrix.environment_id,
        "family_id": matrix.family_id,
        "beam_width": beam_width,
        "items": canonical,
    }
    serialized = {
        **payload,
        "items": tuple(item.model_dump(mode="json") for item in canonical),
    }
    candidate_set = CandidateClauseSetV1.model_validate(
        {
            **payload,
            "clause_set_sha256": semantic_sha256_v22(serialized),
        }
    )
    return ClauseMiningResultV1.model_validate(
        {"status": status, "candidate_set": candidate_set}
    )


def build_incident_fingerprint_v1(
    observation: FingerprintObservationV1,
) -> IncidentFingerprintV1:
    payload = {
        "schema_version": "ecomsre.product.incident-fingerprint.v1",
        "environment_id": observation.environment_id,
        "incident_id": observation.incident_id,
        "root_service_ids": _canonical(observation.root_service_ids),
        "broad_domain": observation.broad_domain,
        "generic_anomaly_kinds": _canonical(observation.generic_anomaly_kinds),
        "evidence_sources": _canonical(observation.evidence_sources),
        "topology_edges": tuple(sorted(set(observation.topology_edges))),
        "runtime_state_signature": _canonical(observation.runtime_state_signature),
        "resource_state_signature": _canonical(observation.resource_state_signature),
        "normalized_log_tokens": _canonical(observation.normalized_log_tokens),
        "trace_first_error_roles": _canonical(observation.trace_first_error_roles),
        "source_coverage": _canonical(observation.source_coverage),
    }
    return IncidentFingerprintV1.model_validate(
        {**payload, "fingerprint_sha256": semantic_sha256_v22(payload)}
    )


def _jaccard(left: Iterable[object], right: Iterable[object]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 1.0
    union = left_set | right_set
    return len(left_set & right_set) / len(union) if union else 1.0


def cluster_similarity_v1(
    left: IncidentFingerprintV1,
    right: IncidentFingerprintV1,
) -> float | None:
    if left.environment_id != right.environment_id:
        return None
    state_match = (
        float(left.runtime_state_signature == right.runtime_state_signature)
        + float(left.resource_state_signature == right.resource_state_signature)
    ) / 2.0
    score = (
        0.30 * _jaccard(left.generic_anomaly_kinds, right.generic_anomaly_kinds)
        + 0.20 * _jaccard(left.evidence_sources, right.evidence_sources)
        + 0.15 * float(left.broad_domain == right.broad_domain)
        + 0.10 * _jaccard(left.root_service_ids, right.root_service_ids)
        + 0.10 * _jaccard(left.topology_edges, right.topology_edges)
        + 0.10 * _jaccard(left.normalized_log_tokens, right.normalized_log_tokens)
        + 0.05 * state_match
    )
    return round(score, 12)


def build_predicate_matrix_v1(
    *,
    environment_id: str,
    family_id: str,
    rows: tuple[PredicateMatrixRowV1, ...],
) -> PredicateMatrixV1:
    canonical_rows = tuple(sorted(rows, key=lambda item: item.row_id))
    payload = {
        "schema_version": "ecomsre.product.predicate-matrix.v1",
        "environment_id": environment_id,
        "family_id": family_id,
        "rows": canonical_rows,
    }
    digest_payload = {
        **payload,
        "rows": tuple(item.model_dump(mode="json") for item in canonical_rows),
    }
    return PredicateMatrixV1.model_validate(
        {
            **payload,
            "predicate_matrix_sha256": semantic_sha256_v22(digest_payload),
        }
    )


def _row_map(row: PredicateMatrixRowV1) -> dict[str, PredicateCellStateV1]:
    return {cell.predicate_id: cell.state for cell in row.cells}


def _clause_state(
    row: PredicateMatrixRowV1,
    predicate_ids: tuple[str, ...],
) -> bool | None:
    states = tuple(_row_map(row).get(item, PredicateCellStateV1.UNKNOWN) for item in predicate_ids)
    if any(
        state in {PredicateCellStateV1.UNKNOWN, PredicateCellStateV1.SOURCE_FAILED}
        for state in states
    ):
        return None
    return all(state is PredicateCellStateV1.PRESENT for state in states)


def _rate(matches: int, total: int) -> float:
    return matches / total if total else 0.0


def mine_candidate_clauses_v1(
    matrix: PredicateMatrixV1,
    *,
    beam_width: int = DEFAULT_BEAM_WIDTH_V1,
    existing_clause_predicates: tuple[tuple[str, ...], ...] = (),
) -> ClauseMiningResultV1:
    positives = tuple(
        row
        for row in matrix.rows
        if row.row_kind is PredicateMatrixRowKindV1.POSITIVE_FAMILY
    )
    negatives = tuple(row for row in matrix.rows if row not in positives)
    if len(positives) < 2:
        return _mining_result(
            matrix=matrix,
            status="NEEDS_MORE_INCIDENTS",
            candidates=(),
            beam_width=beam_width,
        )
    if len(negatives) < 3:
        return _mining_result(
            matrix=matrix,
            status="NEEDS_MORE_NEGATIVES",
            candidates=(),
            beam_width=beam_width,
        )
    source_by_predicate = {
        cell.predicate_id: cell.source for row in matrix.rows for cell in row.cells
    }
    predicate_ids = tuple(sorted(source_by_predicate))
    existing = {tuple(sorted(item)) for item in existing_clause_predicates}
    candidates: list[CandidateClauseV1] = []
    saw_three_conclusive_negatives = False
    frontier: tuple[tuple[str, ...], ...] = tuple((item,) for item in predicate_ids)
    for size in range(1, 4):
        scored_frontier: list[tuple[float, tuple[str, ...]]] = []
        for predicates in frontier:
            sources = _canonical(source_by_predicate[item] for item in predicates)
            if any(
                _row_map(row).get(predicate) is PredicateCellStateV1.SOURCE_FAILED
                for row in positives
                for predicate in predicates
            ):
                continue
            positive_states = tuple(_clause_state(row, predicates) for row in positives)
            positive_recall = _rate(
                sum(state is True for state in positive_states),
                len(positives),
            )
            conclusive_negatives = tuple(
                state
                for row in negatives
                if (state := _clause_state(row, predicates)) is not None
            )
            if len(conclusive_negatives) < 3:
                continue
            saw_three_conclusive_negatives = True
            false_positive_rate = _rate(
                sum(state is True for state in conclusive_negatives),
                len(conclusive_negatives),
            )
            core_rows = tuple(
                row
                for row in negatives
                if row.row_kind is PredicateMatrixRowKindV1.CORE_KNOWN_CONTROL
            )
            core_matches = sum(_clause_state(row, predicates) is True for row in core_rows)
            core_overlap = _rate(core_matches, len(core_rows))
            no_incident_rows = tuple(
                row
                for row in negatives
                if row.row_kind is PredicateMatrixRowKindV1.NO_INCIDENT_CONTROL
            )
            no_incident_matches = sum(
                _clause_state(row, predicates) is True for row in no_incident_rows
            )
            no_incident_rate = _rate(no_incident_matches, len(no_incident_rows))
            score = (
                positive_recall
                - 2.0 * false_positive_rate
                - 1.5 * core_overlap
                - 1.5 * no_incident_rate
                - 0.1 * len(predicates)
                - 0.1 * len(sources)
            )
            scored_frontier.append((round(score, 12), predicates))
            if (
                predicates in existing
                or (size == 1 and predicates[0] not in _DIRECT_STATE_ALLOWLIST_V1)
                or (size > 1 and len(sources) < 2)
                or
                positive_recall < 0.60
                or false_positive_rate > 0.20
                or core_matches > 0
                or no_incident_matches > 0
            ):
                continue
            candidate_id = "candidate-" + semantic_sha256_v22(
                {
                    "matrix": matrix.predicate_matrix_sha256,
                    "predicate_ids": predicates,
                }
            )[:16]
            candidates.append(
                CandidateClauseV1(
                    candidate_id=candidate_id,
                    predicate_ids=predicates,
                    evidence_sources=sources,
                    positive_recall=round(positive_recall, 12),
                    false_positive_rate=round(false_positive_rate, 12),
                    core_known_overlap_rate=round(core_overlap, 12),
                    no_incident_false_positive_rate=round(no_incident_rate, 12),
                    score=round(score, 12),
                )
            )
        beam = tuple(
            predicates
            for _score, predicates in sorted(
                scored_frontier,
                key=lambda item: (-item[0], item[1]),
            )[:beam_width]
        )
        if size < 3:
            frontier = tuple(
                sorted(
                    {
                        tuple(sorted((*predicates, candidate)))
                        for predicates in beam
                        for candidate in predicate_ids
                        if candidate not in predicates
                    }
                )
            )
    ranked = tuple(
        sorted(
            candidates,
            key=lambda item: (
                -item.score,
                abs(len(item.evidence_sources) - 2),
                item.predicate_count,
                item.candidate_id,
            ),
        )[: min(10, beam_width)]
    )
    return _mining_result(
        matrix=matrix,
        status=(
            "CANDIDATES_READY"
            if ranked
            else (
                "NO_ACCEPTABLE_CANDIDATE"
                if saw_three_conclusive_negatives
                else "NEEDS_MORE_NEGATIVES"
            )
        ),
        candidates=ranked,
        beam_width=beam_width,
    )


class CandidateClauseMinerV1:
    """Closed deterministic wrapper for the Product candidate-clause miner."""

    def __init__(self, *, beam_width: int = DEFAULT_BEAM_WIDTH_V1) -> None:
        if beam_width < 1:
            raise ValueError("candidate-clause beam width must be positive")
        self.beam_width = beam_width

    def mine(
        self,
        matrix: PredicateMatrixV1,
        *,
        existing_clause_predicates: tuple[tuple[str, ...], ...] = (),
    ) -> ClauseMiningResultV1:
        return mine_candidate_clauses_v1(
            matrix,
            beam_width=self.beam_width,
            existing_clause_predicates=existing_clause_predicates,
        )


def evaluate_shadow_gate_v1(
    *,
    registration_id: str,
    outcomes: tuple[ShadowCaseOutcomeV1, ...],
) -> ShadowEvaluationV1:
    canonical_outcomes = tuple(sorted(outcomes, key=lambda item: item.case_id))
    evaluated = tuple(
        item
        for item in canonical_outcomes
        if item.origin is not ShadowCaseOriginV1.NOT_AVAILABLE
    )
    positives = tuple(
        item
        for item in evaluated
        if item.stratum is ShadowEvaluationStratumV1.POSITIVE_INCIDENT
    )
    negatives = tuple(
        item
        for item in evaluated
        if item.stratum is not ShadowEvaluationStratumV1.POSITIVE_INCIDENT
    )
    core_controls = tuple(
        item
        for item in evaluated
        if item.stratum is ShadowEvaluationStratumV1.CONFUSABLE_CORE_KNOWN
    )
    no_incident_controls = tuple(
        item
        for item in evaluated
        if item.stratum is ShadowEvaluationStratumV1.NO_INCIDENT
    )
    other_extension_controls = tuple(
        item
        for item in evaluated
        if item.stratum is ShadowEvaluationStratumV1.OTHER_EXTENSION
    )
    counterfactuals = tuple(
        item
        for item in evaluated
        if item.stratum is ShadowEvaluationStratumV1.TARGET_COUNTERFACTUAL
    )
    source_failures = tuple(
        item
        for item in evaluated
        if item.stratum is ShadowEvaluationStratumV1.SOURCE_FAILURE
    )
    positive_recall = _rate(sum(item.matched is True for item in positives), len(positives))
    false_positive_rate = _rate(
        sum(item.matched is True for item in negatives),
        len(negatives),
    )
    core_known_overlap_rate = _rate(
        sum(item.matched is True for item in core_controls),
        len(core_controls),
    )
    no_incident_false_positives = sum(
        item.matched is True for item in no_incident_controls
    )
    other_extension_destructive_overlaps = sum(
        item.matched is True for item in other_extension_controls
    )
    evidence_ref_validity = _rate(
        sum(
            set(item.supporting_evidence_refs).issubset(item.available_evidence_refs)
            for item in evaluated
        ),
        len(evaluated),
    )
    source_reachability = _rate(
        sum(item.source_reachable is True for item in positives),
        len(positives),
    )
    counterfactual_consistency = _rate(
        sum(item.matched is False for item in counterfactuals),
        len(counterfactuals),
    )
    source_failure_safe = bool(source_failures) and all(
        item.matched is False for item in source_failures
    )
    action_authority_violations = sum(
        item.action_authority_violations for item in evaluated
    )
    reasons = []
    unavailable_strata = {
        item.stratum
        for item in canonical_outcomes
        if item.origin is ShadowCaseOriginV1.NOT_AVAILABLE
    }
    if not positives:
        reasons.append("POSITIVE_RUNTIME_CASES_MISSING")
    if not core_controls:
        reasons.append("CONFUSABLE_CORE_CONTROL_MISSING")
    if not no_incident_controls:
        reasons.append("NO_INCIDENT_CONTROL_MISSING")
    if not counterfactuals:
        reasons.append("COUNTERFACTUAL_CONTROL_MISSING")
    if not source_failures:
        reasons.append("SOURCE_FAILURE_CONTROL_MISSING")
    if (
        ShadowEvaluationStratumV1.OTHER_EXTENSION in unavailable_strata
        and other_extension_controls
    ):
        reasons.append("OTHER_EXTENSION_CONTROL_INCONSISTENT")
    if positive_recall < 0.75:
        reasons.append("POSITIVE_RECALL_BELOW_GATE")
    if false_positive_rate > 0.10:
        reasons.append("FALSE_POSITIVE_RATE_ABOVE_GATE")
    if core_known_overlap_rate != 0.0:
        reasons.append("CORE_KNOWN_OVERLAP")
    if no_incident_false_positives != 0:
        reasons.append("NO_INCIDENT_REGRESSION")
    if other_extension_destructive_overlaps != 0:
        reasons.append("OTHER_EXTENSION_DESTRUCTIVE_OVERLAP")
    if evidence_ref_validity != 1.0:
        reasons.append("EVIDENCE_REF_VALIDITY_BELOW_GATE")
    if source_reachability != 1.0:
        reasons.append("SOURCE_REACHABILITY_BELOW_GATE")
    if counterfactual_consistency < 0.80:
        reasons.append("COUNTERFACTUAL_CONSISTENCY_BELOW_GATE")
    if not source_failure_safe:
        reasons.append("SOURCE_FAILURE_NOT_FAIL_CLOSED")
    if action_authority_violations != 0:
        reasons.append("ACTION_AUTHORITY_VIOLATION")
    canonical = tuple(sorted(reasons))
    payload = {
        "schema_version": "ecomsre.product.shadow-evaluation.v1",
        "evaluation_id": "shadow-" + semantic_sha256_v22(
            {
                "registration_id": registration_id,
                "positive_recall": positive_recall,
                "false_positive_rate": false_positive_rate,
                "core_known_overlap_rate": core_known_overlap_rate,
                "no_incident_false_positives": no_incident_false_positives,
                "other_extension_destructive_overlaps": other_extension_destructive_overlaps,
                "evidence_ref_validity": evidence_ref_validity,
                "source_reachability": source_reachability,
                "counterfactual_consistency": counterfactual_consistency,
                "source_failure_safe": source_failure_safe,
                "action_authority_violations": action_authority_violations,
                "runtime_evaluation_sha256": semantic_sha256_v22(
                    tuple(item.outcome_sha256 for item in canonical_outcomes)
                ),
            }
        )[:16],
        "registration_id": registration_id,
        "positive_recall": positive_recall,
        "false_positive_rate": false_positive_rate,
        "core_known_overlap_rate": core_known_overlap_rate,
        "no_incident_false_positives": no_incident_false_positives,
        "other_extension_destructive_overlaps": other_extension_destructive_overlaps,
        "evidence_ref_validity": evidence_ref_validity,
        "source_reachability": source_reachability,
        "counterfactual_consistency": counterfactual_consistency,
        "source_failure_safe": source_failure_safe,
        "action_authority_violations": action_authority_violations,
        "outcomes": canonical_outcomes,
        "runtime_evaluation_sha256": semantic_sha256_v22(
            tuple(item.outcome_sha256 for item in canonical_outcomes)
        ),
        "gate_passed": not canonical,
        "reason_codes": canonical,
        "action_authority": "NONE",
    }
    digest_payload = {
        **payload,
        "outcomes": tuple(item.model_dump(mode="json") for item in canonical_outcomes),
    }
    return ShadowEvaluationV1.model_validate(
        {**payload, "evaluation_sha256": semantic_sha256_v22(digest_payload)}
    )


__all__ = (
    "CLUSTER_ASSIGNMENT_THRESHOLD_V1",
    "CandidateClauseMinerV1",
    "build_incident_fingerprint_v1",
    "build_predicate_matrix_v1",
    "cluster_similarity_v1",
    "evaluate_shadow_gate_v1",
    "mine_candidate_clauses_v1",
)
