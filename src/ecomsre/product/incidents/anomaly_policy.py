"""Product residual policy; original Resource evidence and frozen Core stay intact."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from ecomsre.dta_v2.v22.memory import (
    PredicateKindV22,
    SalientEvidenceMemoryV22,
    SignalStrengthV22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    ResourceUsageRecordV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    GenericAnomalyV23,
    extract_generic_anomalies_v23,
)
from ecomsre.product.connectors.base import ConnectorQueryResultV1


_CORROBORATING_KINDS = frozenset(
    {
        GenericAnomalyKindV23.RUNTIME_RESTART_ANOMALY,
        GenericAnomalyKindV23.RUNTIME_UNHEALTHY,
        GenericAnomalyKindV23.METRIC_ERROR_OUTLIER,
        GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION,
    }
)


def _independent_resource_refs(memory, snapshots):
    """Bind disjoint actual windows, not duplicate records or renamed actions."""
    windows = {}
    ambiguous = set()
    refs = {ref.evidence_ref: ref for ref in memory.evidence_refs}
    for snapshot in snapshots:
        if snapshot.get("read_outcome", {}).get("source") != "RESOURCES":
            continue
        try:
            outcome = ReadOutcomeV22.model_validate_json(
                json.dumps(snapshot["read_outcome"])
            )
            result = ConnectorQueryResultV1.model_validate_json(
                json.dumps(snapshot["connector_result"])
            )
        except (KeyError, ValidationError):
            continue
        if (
            outcome.status is not ReadSourceStatusV22.SUCCESS_NONEMPTY
            or result.status is not ReadSourceStatusV22.SUCCESS_NONEMPTY
            or outcome.truncated
            or result.truncated
            or outcome.records != result.records
            or result.window.ended_at > memory.observed_at
        ):
            continue
        for reference in refs.values():
            if (
                reference.outcome_sha256 != outcome.outcome_sha256
                or reference.action_id != outcome.action_id
            ):
                continue
            if reference.record_index >= len(outcome.records):
                continue
            record = outcome.records[reference.record_index]
            if (
                not isinstance(record, ResourceUsageRecordV22)
                or reference.record_sha256
                != semantic_sha256_v22(record.model_dump(mode="json"))
                or (result.window.ended_at - result.window.started_at).total_seconds()
                != record.sampling_window_seconds
            ):
                continue
            value = (
                result.window.started_at,
                result.window.ended_at,
                reference.record_sha256,
            )
            ref = reference.evidence_ref
            if ref in windows and windows[ref] != value:
                ambiguous.add(ref)
            windows[ref] = value
    return {ref: value for ref, value in windows.items() if ref not in ambiguous}


def extract_product_anomalies_v1(
    *,
    memory: SalientEvidenceMemoryV22,
    candidate_services: tuple[str, ...],
    baseline_known_log_templates: tuple[tuple[str, str], ...] = (),
    snapshots: tuple[dict[str, Any], ...] = (),
) -> tuple[GenericAnomalyV23, ...]:
    anomalies = extract_generic_anomalies_v23(
        memory=memory,
        candidate_services=candidate_services,
        baseline_known_log_templates=baseline_known_log_templates,
        healthy_noise_guard_v024=True,
    )
    trends = tuple(
        a for a in anomalies if a.kind is GenericAnomalyKindV23.RESOURCE_MEMORY_TREND
    )
    corroborated = {
        a.service
        for a in anomalies
        if a.kind in _CORROBORATING_KINDS and a.strength is SignalStrengthV22.STRONG
    }
    # Baseline-suppressed diagnostic logs must not re-enter through predicates.
    for predicate in memory.predicates:
        if predicate.predicate_kind is PredicateKindV22.LOG_MEMORY_PRESSURE and any(
            a.service == predicate.service
            and a.kind is GenericAnomalyKindV23.LOG_ERROR_CLUSTER
            and a.strength is SignalStrengthV22.STRONG
            and set(a.evidence_refs).intersection(predicate.evidence_refs)
            for a in anomalies
        ):
            corroborated.add(predicate.service)
    if len(trends) > 1:
        windows = _independent_resource_refs(memory, snapshots)
        for left in trends:
            for right in trends:
                if (
                    left.service != right.service
                    or left.strength is not SignalStrengthV22.STRONG
                    or right.strength is not SignalStrengthV22.STRONG
                ):
                    continue
                for left_ref in left.evidence_refs:
                    for right_ref in right.evidence_refs:
                        first, second = windows.get(left_ref), windows.get(right_ref)
                        if (
                            first
                            and second
                            and first[2] != second[2]
                            and (first[1] <= second[0] or second[1] <= first[0])
                        ):
                            corroborated.add(left.service)
    # This catalog feeds residual admission. Do not change Memory, predicates,
    # source records, refs, or numeric thresholds when excluding isolated trends.
    return tuple(
        a
        for a in anomalies
        if a.kind is not GenericAnomalyKindV23.RESOURCE_MEMORY_TREND
        or a.service in corroborated
    )
