"""Synthetic runtime contracts, not live cases, human decisions, or Promotion."""

from datetime import UTC, datetime, timedelta
from itertools import combinations
import json

import pytest

from ecomsre.dta_v2.tool_contracts import build_fake_read_authority
from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.memory import BaselineProfileV22, build_memory_views_v22
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    MetricUnitV22,
    ReadSourceStatusV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v23.extension_runtime_v234 import ExtensionSupportPolicyV234
from ecomsre.dta_v2.v23.generic_anomalies import extract_generic_anomalies_v23
from ecomsre.product.connectors.base import ConnectorQueryResultV1, ConnectorWindowV1
from ecomsre.product.errors import ProductError
from ecomsre.product.incidents.contracts import (
    DiagnosisResultV1,
    DiagnosisTerminalV1,
    IncidentRecordV1,
)
from ecomsre.product.incidents.diagnosis_bridge import _domain_for_anomalies
from ecomsre.product.incidents.extensions import (
    build_product_extension_runtime_input_v1,
)
from ecomsre.product.incidents.queue_action import build_queue_lag_action_v030
from ecomsre.product.incidents.read_backend import _build_outcome, _runtime_memory
from ecomsre.product.knowledge.compiler import build_product_shadow_candidate_v1
from ecomsre.product.knowledge.contracts import (
    FamilyRegistrationDraftV1,
    FingerprintObservationV1,
    PredicateMatrixCellV1,
    PredicateMatrixRowV1,
    RegistrationImplementationModeV1,
)
from ecomsre.product.knowledge.repository import KnowledgeRepositoryV1
from ecomsre.product.knowledge.runtime import (
    CLUSTER_ASSIGNMENT_THRESHOLD_V1,
    build_incident_fingerprint_v1,
    build_predicate_matrix_v1,
    cluster_similarity_v1,
    mine_candidate_clauses_v1,
)
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


NOW = datetime(2026, 9, 3, tzinfo=UTC)
SERVICES = ("checkout", "fraud-detection", "kafka")
QUEUE_PREDICATE = "ga:METRIC_QUEUE_LAG_OUTLIER"


def _runtime_input(slot, lag, *, failed_source=None):
    observed_at = NOW + timedelta(minutes=slot)
    window = ConnectorWindowV1(
        started_at=observed_at - timedelta(seconds=60), ended_at=observed_at
    )
    queue_action = build_queue_lag_action_v030()
    catalog = build_action_catalog_v22(
        candidate_services=SERVICES,
        topology=StaticTopologyV22.build(services=SERVICES, edges=()),
        capability_registry=build_tool_capability_registry_v22(),
        executed_action_ids=(),
        remaining_budget=100.0,
    )
    runtime_action = next(
        action
        for action in catalog.registry_actions
        if action.source is EvidenceSourceV22.RUNTIME
        and action.target_services == SERVICES
    )
    outcomes = []
    memory_outcomes = []
    for action in (queue_action, runtime_action):
        failed = action.source.value == failed_source
        records = (
            (
                MetricFactV22(
                    schema_version="dta-v22.metric-fact.v1",
                    service="fraud-detection",
                    metric_kind=MetricKindV22.QUEUE_LAG,
                    support_status=MetricSupportStatusV22.SUPPORTED,
                    sample_count=3,
                    value=float(lag),
                    unit=MetricUnitV22.COUNT,
                    window_started_at=window.started_at,
                    window_ended_at=window.ended_at,
                ),
            )
            if action is queue_action
            else tuple(
                RuntimeRecordV22(
                    schema_version="dta-v22.runtime-record.v1",
                    service=service,
                    state=RuntimeStateV22.RUNNING,
                    healthy=True,
                    restart_count=0,
                )
                for service in SERVICES
            )
        )
        result = ConnectorQueryResultV1.build(
            source=action.source,
            status=(
                ReadSourceStatusV22.FAILURE_UNAVAILABLE
                if failed
                else ReadSourceStatusV22.SUCCESS_NONEMPTY
            ),
            requested_services=action.target_services,
            covered_services=() if failed else action.target_services,
            window=window,
            records=() if failed else records,
            truncated=False,
            safe_error_code="SYNTHETIC_SOURCE_FAILURE" if failed else None,
            latency_ms=0.0,
        )
        outcome = _build_outcome(action, result)
        outcomes.append(outcome)
        if action is runtime_action and not failed:
            memory_outcomes.append(
                _runtime_memory(
                    incident=IncidentRecordV1.model_construct(
                        incident_sha256=f"{slot:064x}"
                    ),
                    action=action,
                    outcome=outcome,
                    window=window,
                    latency_ms=0.0,
                    authority=build_fake_read_authority(),
                )
            )
        elif action is queue_action:
            memory_outcomes.append(outcome)
    baseline = BaselineProfileV22.build(
        metric_stats=(("fraud-detection", MetricKindV22.QUEUE_LAG, 0.0, 0.0),),
        trace_stats=(),
        resource_stats=(),
    )
    memory, _ = build_memory_views_v22(
        outcomes=tuple(memory_outcomes),
        baseline=baseline,
        observed_at=observed_at,
        top_k=64,
    )
    return build_product_extension_runtime_input_v1(
        case_id=f"synthetic-{slot}",
        candidate_services=SERVICES,
        topology_edges=(),
        baseline=baseline,
        memory=memory,
        generic_anomalies=extract_generic_anomalies_v23(
            memory=memory, candidate_services=SERVICES
        ),
        raw_outcomes=tuple(outcomes),
    )


def _mining(negative_queue_state="ABSENT_WITH_COMPLETE_COVERAGE"):
    rows = []
    for index, (name, kind, queue_state) in enumerate(
        (
            *((f"P{index}", "POSITIVE_FAMILY", "PRESENT") for index in (1, 2, 3)),
            ("N0-A", "NO_INCIDENT_CONTROL", negative_queue_state),
            ("N0-B", "NO_INCIDENT_CONTROL", negative_queue_state),
            ("C1", "CORE_KNOWN_CONTROL", negative_queue_state),
        ),
        start=1,
    ):
        rows.append(
            PredicateMatrixRowV1(
                row_id=name,
                incident_id=f"inc-{index:024x}",
                row_kind=kind,
                cells=(
                    PredicateMatrixCellV1(
                        predicate_id="core:RUNTIME_HEALTHY",
                        source="RUNTIME",
                        state="PRESENT",
                    ),
                    PredicateMatrixCellV1(
                        predicate_id=QUEUE_PREDICATE,
                        source="METRICS",
                        state=queue_state,
                    ),
                ),
            )
        )
    matrix = build_predicate_matrix_v1(
        environment_id="synthetic-environment",
        family_id="synthetic-family",
        rows=tuple(rows),
    )
    return matrix, mine_candidate_clauses_v1(matrix)


def test_three_window_queue_fingerprints_cluster_with_frozen_threshold():
    fingerprints = []
    for index, lag in enumerate((40.0, 60.0, 80.0), start=1):
        runtime_input = _runtime_input(index, lag)
        anomalies = runtime_input.generic_anomalies
        assert len(anomalies) == 1
        assert anomalies[0].service == "fraud-detection"
        fingerprints.append(
            build_incident_fingerprint_v1(
                FingerprintObservationV1(
                    environment_id="synthetic-environment",
                    incident_id=runtime_input.case_id,
                    root_service_ids=(f"svc-{anomalies[0].service}",),
                    broad_domain=_domain_for_anomalies(anomalies).value,
                    generic_anomaly_kinds=tuple(item.kind.value for item in anomalies),
                    evidence_sources=tuple(
                        item.source.value for item in runtime_input.source_coverage
                    ),
                    topology_edges=(),
                    runtime_state_signature=tuple(
                        f"{service}:RUNNING:True" for service in SERVICES
                    ),
                    resource_state_signature=(),
                    normalized_log_tokens=(),
                    trace_first_error_roles=(),
                    # Deliberately not claiming endpoint-wide or incident-query completeness.
                    source_coverage=(),
                )
            )
        )
    assert CLUSTER_ASSIGNMENT_THRESHOLD_V1 == 0.65
    assert [
        cluster_similarity_v1(left, right)
        for left, right in combinations(fingerprints, 2)
    ] == [1.0] * 3
    assert all(item.broad_domain == "CONCURRENCY" for item in fingerprints)


def test_runtime_mines_expected_two_source_queue_clause_from_conclusive_matrix():
    _, mining = _mining()
    assert mining.status == "CANDIDATES_READY"
    assert len(mining.candidates) == 1
    selected = mining.candidates[0]
    assert selected.predicate_ids == ("core:RUNTIME_HEALTHY", QUEUE_PREDICATE)
    assert selected.evidence_sources == ("METRICS", "RUNTIME")
    assert selected.predicate_count == 2
    assert selected.positive_recall == 1.0
    assert selected.false_positive_rate == 0.0
    assert selected.core_known_overlap_rate == 0.0
    assert selected.no_incident_false_positive_rate == 0.0
    assert selected.action_authority == "NONE"


@pytest.mark.parametrize("state", ["UNKNOWN", "SOURCE_FAILED"])
def test_unobserved_queue_controls_cannot_yield_a_passing_candidate(state):
    _, mining = _mining(state)
    assert mining.status != "CANDIDATES_READY"
    assert mining.candidates == ()


@pytest.mark.parametrize("terminal", ["NO_INCIDENT", "CORE_KNOWN"])
def test_controls_cannot_enter_family_ingestion(tmp_path, monkeypatch, terminal):
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    repository = KnowledgeRepositoryV1(
        store, ContentAddressedObjectStoreV1(tmp_path / "objects", metadata_store=store)
    )
    # Input fixture for the terminal-routing guard; no real Diagnosis is claimed.
    monkeypatch.setattr(
        repository,
        "_diagnosis",
        lambda _: DiagnosisResultV1.model_construct(
            terminal=DiagnosisTerminalV1(terminal)
        ),
    )
    with pytest.raises(ProductError, match="Only an OpenWorld incident"):
        repository.ingest_open_world("synthetic-control")
    with store.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM fault_family_members").fetchone()[
                0
            ]
            == 0
        )


def test_mined_queue_shadow_candidate_matches_only_healthy_consumer_with_lag():
    matrix, mining = _mining()
    selected = mining.candidates[0]
    payload = dict(
        schema_version="ecomsre.product.family-registration-draft.v1",
        registration_id="synthetic-registration",
        environment_id=matrix.environment_id,
        family_id=matrix.family_id,
        human_review_id="SYNTHETIC_NOT_A_HUMAN_DECISION",
        human_canonical_label="KAFKA_QUEUE_BACKLOG",
        broad_domain="CONCURRENCY",
        positive_incident_ids=tuple(
            sorted(
                row.incident_id
                for row in matrix.rows
                if row.row_kind.value == "POSITIVE_FAMILY"
            )
        ),
        negative_incident_ids=tuple(
            sorted(
                row.incident_id
                for row in matrix.rows
                if row.row_kind.value != "POSITIVE_FAMILY"
            )
        ),
        predicate_matrix_sha256=matrix.predicate_matrix_sha256,
        candidate_clauses=mining.candidates,
        selected_candidate_id=selected.candidate_id,
        llm_explanation="Synthetic unit fixture; the Runtime owns the clause.",
        unresolved_gaps=(),
        implementation_mode=RegistrationImplementationModeV1.DECLARATIVE_READY,
        created_at=NOW,
    )
    draft = FamilyRegistrationDraftV1.model_construct(**payload, draft_sha256="0" * 64)
    serialized = draft.model_dump(mode="json", exclude={"draft_sha256"})
    draft = FamilyRegistrationDraftV1.model_validate_json(
        json.dumps(
            {
                **serialized,
                "draft_sha256": semantic_sha256_v22(serialized),
            }
        )
    )
    registration = build_product_shadow_candidate_v1(draft=draft, selected=selected)
    assert registration.mechanism.mechanism_slug == "kafka-queue-backlog"
    assert registration.action_authority == "NONE"
    assert registration.remediation_registration == "NOT_INCLUDED"
    assert registration.repository_write_authority == "NONE"
    policy = ExtensionSupportPolicyV234()
    for slot, lag, failed_source, expected in (
        (10, 40.0, None, True),
        (11, 0.0, None, False),
        (12, 40.0, "METRICS", False),
        (13, 40.0, "RUNTIME", False),
    ):
        runtime_input = _runtime_input(slot, lag, failed_source=failed_source)
        decisions = policy.evaluate(
            registration=registration, runtime_input=runtime_input
        )
        admitted = [item for item in decisions if item.admitted]
        assert len(admitted) == int(expected)
        if expected:
            assert admitted[0].target_service == "fraud-detection"
            assert set(admitted[0].supporting_evidence_refs).issubset(
                ref.evidence_ref for ref in runtime_input.memory.evidence_refs
            )
