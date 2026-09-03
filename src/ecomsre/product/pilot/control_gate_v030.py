"""Case-specific queue-negative control; not a mined or registered candidate."""

from __future__ import annotations

from ecomsre.dta_v2.v22.read_contracts import (
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    MetricUnitV22,
    ReadSourceStatusV22,
    RuntimeRecordV22,
    RuntimeStateV22,
)
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.incidents.queue_action import build_queue_lag_action_v030
from ecomsre.product.knowledge.contracts import (
    PredicateCellStateV1,
    PredicateMatrixCellV1,
    PredicateMatrixRowKindV1,
    PredicateMatrixRowV1,
)
from ecomsre.product.knowledge.repository import (
    KnowledgeRepositoryV1,
    _present_predicates,
    _predicate_source,
)
from ecomsre.product.knowledge.runtime import _clause_state


C1_CANDIDATES_V030 = ("checkout", "fraud-detection", "payment")
EXPECTED_QUEUE_PREDICATES_V030 = ("core:RUNTIME_HEALTHY", "ga:METRIC_QUEUE_LAG_OUTLIER")


def evaluate_c1_queue_negative_v030(
    knowledge: KnowledgeRepositoryV1, incident_id: str
) -> dict:
    material = knowledge._shadow_runtime_material(incident_id)
    incident = material.incident
    diagnosis = knowledge._diagnosis(incident_id)
    evidence = knowledge._evidence(incident_id, diagnosis.diagnosis_id)
    identity = ServiceCatalogRepositoryV1(knowledge.store).get_map(
        incident.environment_id
    )
    payment_ids = tuple(
        item.service_id
        for item in identity.services
        if item.logical_service == "payment"
    )
    objects = {item.evidence_ref: item for item in evidence.objects}
    support = diagnosis.supporting_evidence_refs
    payment_refs = []
    for reference in material.runtime_input.memory.evidence_refs:
        if (
            reference.evidence_ref not in support
            or reference.evidence_ref not in objects
        ):
            continue
        records = (
            objects[reference.evidence_ref]
            .payload.get("read_outcome", {})
            .get("records", ())
        )
        if (
            reference.record_index < len(records)
            and records[reference.record_index].get("service") == "payment"
        ):
            payment_refs.append(reference.evidence_ref)
    queue_action = build_queue_lag_action_v030()
    queue_outcomes = tuple(
        o for o in material.raw_outcomes if o.action_id == queue_action.action_id
    )
    queue_records = tuple(
        r for o in queue_outcomes for r in o.records if isinstance(r, MetricFactV22)
    )
    stat = material.baseline.v22_baseline_profile.metric(
        "fraud-detection", MetricKindV22.QUEUE_LAG
    )
    threshold = (
        None
        if stat is None
        else max(20.0, stat.mean + 5 * max(stat.standard_deviation, 1.0))
    )
    queue_observed_low = (
        threshold is not None
        and len(queue_outcomes) == 1
        and len(queue_records) == 1
        and queue_outcomes[0].status is ReadSourceStatusV22.SUCCESS_NONEMPTY
        and not queue_outcomes[0].truncated
        and "METRICS" in material.complete_sources
        and all(
            r.service == "fraud-detection"
            and r.metric_kind is MetricKindV22.QUEUE_LAG
            and r.unit is MetricUnitV22.COUNT
            and r.support_status is MetricSupportStatusV22.SUPPORTED
            and r.sample_count >= 3
            and r.value is not None
            and r.value < threshold
            for r in queue_records
        )
    )
    runtime_records = tuple(
        r
        for o in material.raw_outcomes
        if o.status is ReadSourceStatusV22.SUCCESS_NONEMPTY and not o.truncated
        for r in o.records
        if isinstance(r, RuntimeRecordV22) and r.service == "fraud-detection"
    )
    runtime_healthy = (
        bool(runtime_records)
        and "RUNTIME" in material.complete_sources
        and all(
            r.state is RuntimeStateV22.RUNNING and r.healthy for r in runtime_records
        )
    )
    queue_absent = not any(
        a.kind.value == "METRIC_QUEUE_LAG_OUTLIER"
        for a in material.runtime_input.generic_anomalies
    )
    fingerprint = knowledge.fingerprint_for(incident_id)
    present = _present_predicates(fingerprint)
    cells = tuple(
        PredicateMatrixCellV1(
            predicate_id=predicate,
            source=_predicate_source(predicate),
            state=(
                PredicateCellStateV1.PRESENT
                if predicate in present
                else PredicateCellStateV1.ABSENT_WITH_COMPLETE_COVERAGE
                if _predicate_source(predicate) in fingerprint.source_coverage
                else PredicateCellStateV1.UNKNOWN
            ),
        )
        for predicate in EXPECTED_QUEUE_PREDICATES_V030
    )
    row = PredicateMatrixRowV1(
        row_id=f"queue-negative:{incident_id}",
        incident_id=incident_id,
        row_kind=PredicateMatrixRowKindV1.CORE_KNOWN_CONTROL,
        cells=cells,
    )
    clause_state = _clause_state(row, EXPECTED_QUEUE_PREDICATES_V030)
    passed = (
        diagnosis.terminal.value == "CORE_KNOWN"
        and diagnosis.mechanism == "CONFIGURATION_ERROR"
        and bool(payment_ids)
        and diagnosis.root_service_ids == payment_ids
        and bool(support)
        and set(support).issubset(objects)
        and bool(payment_refs)
        and queue_observed_low
        and runtime_healthy
        and queue_absent
        and clause_state is False
    )
    return {
        "status": "CONCLUSIVE" if passed else "INCONCLUSIVE",
        "incident_id": incident_id,
        "payment_supporting_refs": sorted(payment_refs),
        "queue_metric_observed_below_threshold": queue_observed_low,
        "queue_values": [r.value for r in queue_records],
        "queue_threshold": threshold,
        "fraud_runtime_running_healthy": runtime_healthy,
        "queue_outlier_absent": queue_absent,
        "intended_predicates": list(EXPECTED_QUEUE_PREDICATES_V030),
        "predicate_cells": [cell.model_dump(mode="json") for cell in cells],
        "intended_clause_state": clause_state,
        "capability_limitations_preserved": list(diagnosis.capability_limitations),
        "scope": "Expected queue-negative control check only; not Runtime candidate selection or source-completeness reclassification.",
    }


def case_gate_passes_v030(record: dict, expected: str) -> bool:
    diagnosis = record["diagnosis"]
    if record["case"] == "C1":
        queue = record.get("queue_negative_evidence", {})
        coverage_ready = (
            queue.get("status") == "CONCLUSIVE"
            and queue.get("incident_id") == record["incident"]["incident_id"]
            and queue.get("intended_clause_state") is False
            and all(
                queue.get(key) is True
                for key in (
                    "queue_metric_observed_below_threshold",
                    "fraud_runtime_running_healthy",
                    "queue_outlier_absent",
                )
            )
            and queue.get("capability_limitations_preserved")
            == diagnosis["capability_limitations"]
        )
    else:
        coverage_ready = not diagnosis["capability_limitations"]
    return bool(
        diagnosis["terminal"] == expected
        and coverage_ready
        and not record["leaked_tokens"]
        and record["supporting_refs_resolve"]
        and diagnosis["action_authority"] == "NONE"
        and all(
            diagnosis[key] == 0
            for key in ("provider_calls", "agent_writes", "runbook_executions")
        )
        and (record["case"] != "C1" or diagnosis["mechanism"] == "CONFIGURATION_ERROR")
    )


def control_record_passes_v030(record: dict) -> bool:
    return record.get("status") == "PASS" and case_gate_passes_v030(
        record, "CORE_KNOWN" if record["case"] == "C1" else "NO_INCIDENT"
    )
