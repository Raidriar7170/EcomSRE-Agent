from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.product.app import create_app
from ecomsre.product.connectors.base import (
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.errors import ProductError
from ecomsre.product.incidents.contracts import (
    ActionAuthorityV1,
    DiagnosisLaneV1,
    DiagnosisResultV1,
    DiagnosisTerminalV1,
)
from ecomsre.product.incidents.extensions import (
    ProductExtensionMatchV1,
    ProductExtensionMatcherV1,
)
from ecomsre.product.incidents.evidence_binding_v0232 import (
    CapabilityEvidenceObservationV0232,
    CapabilityLimitationCandidateV0232,
    DiagnosisDecisionTraceV0232,
)
from ecomsre.product.environment.capabilities import SourceCapabilityStatusV1
from ecomsre.product.incidents.read_backend import ProductReadBackendV1, _combine_results
from ecomsre.product.incidents.repository import DiagnosisRepositoryV1
from ecomsre.product.jobs.contracts import JobLeaseFenceV1, ProductJobTypeV1
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.jobs.worker import run_one_job
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


NOW = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)


def _settings(tmp_path: Path) -> ProductSettingsV1:
    return ProductSettingsV1(
        data_root=tmp_path,
        sqlite_path=tmp_path / "product.sqlite3",
        object_store_root=tmp_path / "objects",
    )


def _environment_payload(dataset: str, name: str) -> dict[str, object]:
    return {
        "name": name,
        "description": "Increment 3 deterministic fixture environment",
        "timezone": "UTC",
        "service_identity_policy": {
            "services": [{"logical_service": "payment"}]
        },
        "connector_configs": [
            {
                "name": "fixture",
                "kind": "FIXTURE",
                "settings": {"dataset": dataset},
                "credential_refs": {},
            }
        ],
        "explicit_service_catalog": ["payment"],
    }


def _prepare_environment(
    client: TestClient,
    settings: ProductSettingsV1,
    *,
    dataset: str,
    name: str,
) -> tuple[str, str]:
    environment = client.post(
        "/v1/environments",
        json=_environment_payload(dataset, name),
    )
    assert environment.status_code == 201
    environment_id = environment.json()["environment_id"]

    verify = client.post(f"/v1/environments/{environment_id}/verify-jobs")
    assert verify.status_code == 202
    assert run_one_job(settings, worker_id=f"worker-{name}") is True
    verified_job = client.get(f"/v1/jobs/{verify.json()['job_id']}").json()
    assert verified_job["status"] == "SUCCEEDED", verified_job["safe_error_code"]

    baseline = client.post(
        f"/v1/environments/{environment_id}/baseline-jobs",
        json={"activate": True},
    )
    assert baseline.status_code == 202
    assert run_one_job(settings, worker_id=f"worker-{name}") is True
    assert client.get(f"/v1/jobs/{baseline.json()['job_id']}").json()["status"] == "SUCCEEDED"
    active = client.get(f"/v1/environments/{environment_id}/baselines").json()["items"]
    assert len(active) == 1
    assert active[0]["active"] is True

    service_id = verified_job["result"]["service_identity_map"]["services"][0]["service_id"]
    return environment_id, service_id


def _diagnose(
    client: TestClient,
    settings: ProductSettingsV1,
    *,
    environment_id: str,
    service_id: str,
    external_key: str,
) -> tuple[dict[str, object], dict[str, object]]:
    incident_payload = {
        "environment_id": environment_id,
        "external_incident_key": external_key,
        "alert_name": "payment-observation",
        "summary": "A bounded fixture observation requires diagnosis.",
        "started_at": NOW.isoformat(),
        "candidate_service_ids": [service_id],
        "labels": {"source": "increment3-checkpoint"},
    }
    first = client.post("/v1/incidents", json=incident_payload)
    repeated = client.post("/v1/incidents", json=incident_payload)
    assert first.status_code == 201
    assert repeated.status_code == 201
    assert repeated.json()["incident_id"] == first.json()["incident_id"]
    incident_id = first.json()["incident_id"]

    queued = client.post(f"/v1/incidents/{incident_id}/diagnosis-jobs")
    assert queued.status_code == 202
    assert run_one_job(settings, worker_id=f"worker-{external_key}") is True
    assert client.get(f"/v1/jobs/{queued.json()['job_id']}").json()["status"] == "SUCCEEDED"

    diagnosis = client.get(f"/v1/incidents/{incident_id}/diagnosis")
    evidence = client.get(f"/v1/incidents/{incident_id}/evidence")
    assert diagnosis.status_code == 200
    assert evidence.status_code == 200
    assert evidence.json()["incident_id"] == incident_id
    assert evidence.json()["objects"]
    assert all(item["object_sha256"] for item in evidence.json()["objects"])
    linked_refs = {item["evidence_ref"] for item in evidence.json()["objects"]}
    assert set(evidence.json()["supporting_evidence_refs"]).issubset(linked_refs)
    return diagnosis.json(), evidence.json()


def test_increment3_known_and_open_world_incidents_persist_evidence(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        known_environment, known_service = _prepare_environment(
            client,
            settings,
            dataset="capture-7f31",
            name="known",
        )
        known, known_evidence = _diagnose(
            client,
            settings,
            environment_id=known_environment,
            service_id=known_service,
            external_key="known-1",
        )
        assert known["terminal"] == "CORE_KNOWN"
        assert known["mechanism"] == "SERVICE_UNAVAILABLE"
        assert known["action_authority"] == "NONE"
        assert known["provisional_report"] is None
        assert known_evidence["supporting_evidence_refs"]

        unknown_environment, unknown_service = _prepare_environment(
            client,
            settings,
            dataset="capture-c2aa",
            name="unknown",
        )
        unknown, unknown_evidence = _diagnose(
            client,
            settings,
            environment_id=unknown_environment,
            service_id=unknown_service,
            external_key="unknown-1",
        )
        assert unknown["terminal"] == "OPEN_WORLD"
        assert unknown["core_or_extension_or_open_world"] == "OPEN_WORLD"
        assert unknown["provisional_report"]["action_authority"] == "NONE"
        assert unknown["provisional_report"]["terminal"] == "UNREGISTERED_INCIDENT_SUSPECTED"
        assert unknown["action_authority"] == "NONE"
        assert unknown_evidence["supporting_evidence_refs"]

        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert {
            "ecomsre_http_requests_total",
            "ecomsre_jobs_total",
            "ecomsre_job_duration_seconds",
            "ecomsre_connector_requests_total",
            "ecomsre_connector_failures_total",
            "ecomsre_diagnosis_terminals_total",
            "ecomsre_open_world_reports_total",
            "ecomsre_fault_families_total",
            "ecomsre_registration_promotions_total",
        }.issubset(set(metrics.text.split()))


def test_increment3_incident_requires_an_active_baseline(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        environment = client.post(
            "/v1/environments",
            json=_environment_payload("capture-7f31", "no-baseline"),
        ).json()
        verify = client.post(
            f"/v1/environments/{environment['environment_id']}/verify-jobs"
        ).json()
        assert run_one_job(settings, worker_id="worker-no-baseline") is True
        service_id = client.get(f"/v1/jobs/{verify['job_id']}").json()["result"][
            "service_identity_map"
        ]["services"][0]["service_id"]
        response = client.post(
            "/v1/incidents",
            json={
                "environment_id": environment["environment_id"],
                "external_incident_key": "missing-baseline",
                "alert_name": "payment-observation",
                "summary": "No active baseline exists.",
                "started_at": NOW.isoformat(),
                "candidate_service_ids": [service_id],
                "labels": {},
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BASELINE_REQUIRED"


def test_product_mvp_demo_fixture_can_verify_and_build_baseline(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        environment_id, service_id = _prepare_environment(
            client,
            settings,
            dataset="product-mvp-demo",
            name="demo",
        )
        capability = client.get(
            f"/v1/environments/{environment_id}/capabilities"
        )
        diagnosis, evidence = _diagnose(
            client,
            settings,
            environment_id=environment_id,
            service_id=service_id,
            external_key="demo-no-incident",
        )

    assert service_id.startswith("svc-")
    assert capability.status_code == 200
    assert all(item["status"] != "UNAVAILABLE" for item in capability.json()["sources"])
    assert diagnosis["terminal"] == "NO_INCIDENT"
    assert diagnosis["core_or_extension_or_open_world"] == "NO_INCIDENT"
    assert diagnosis["supporting_evidence_refs"]
    objects_by_ref = {item["evidence_ref"]: item for item in evidence["objects"]}
    assert set(diagnosis["supporting_evidence_refs"]).issubset(objects_by_ref)
    assert {
        objects_by_ref[reference]["source"]
        for reference in diagnosis["supporting_evidence_refs"]
    }.issuperset({"LOGS", "METRICS", "RUNTIME"})
    assert diagnosis["agent_writes"] == 0
    assert diagnosis["runbook_executions"] == 0
    assert diagnosis["provider_calls"] == 0
    assert len(evidence["objects"]) == 8


def test_v0232_diagnosis_evidence_index_is_retrievable_and_deterministic(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        environment_id, service_id = _prepare_environment(
            client,
            settings,
            dataset="product-mvp-demo",
            name="v0232-index",
        )
        diagnosis, evidence = _diagnose(
            client,
            settings,
            environment_id=environment_id,
            service_id=service_id,
            external_key="v0232-index",
        )
        first = client.get(
            f"/v1/incidents/{diagnosis['incident_id']}/evidence-index"
        )
        second = client.get(
            f"/v1/incidents/{diagnosis['incident_id']}/evidence-index"
        )

    assert first.status_code == 200
    assert second.json() == first.json()
    index = first.json()
    assert index["diagnosis_id"] == diagnosis["diagnosis_id"]
    assert index["all_object_refs"] == sorted(
        item["evidence_ref"] for item in evidence["objects"]
    )
    assert index["all_object_sha256_by_ref"] == {
        item["evidence_ref"]: item["object_sha256"]
        for item in sorted(evidence["objects"], key=lambda item: item["evidence_ref"])
    }
    assert index["linked_support_refs"] == diagnosis["supporting_evidence_refs"]
    assert index["evidence_bundle_sha256"] == semantic_sha256_v22(evidence)
    assert index["decision_trace_sha256"]
    assert index["index_sha256"]


def test_extension_lane_runs_after_core_and_before_open_world(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[str] = []

    def match(_self, **kwargs):
        calls.append(str(kwargs["case_id"]))
        memory = kwargs["memory"]
        return (
            ProductExtensionMatchV1(
                registration_id="registration-v234-0123456789abcdef",
                mechanism_slug="opaque-convoy",
                broad_fault_domain="RUNTIME",
                root_service=kwargs["candidate_services"][0],
                supporting_evidence_refs=(memory.evidence_refs[0].evidence_ref,),
            ),
        )

    monkeypatch.setattr(ProductExtensionMatcherV1, "match", match)
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        known_environment, known_service = _prepare_environment(
            client,
            settings,
            dataset="capture-7f31",
            name="known-priority",
        )
        known, _ = _diagnose(
            client,
            settings,
            environment_id=known_environment,
            service_id=known_service,
            external_key="known-priority",
        )
        assert known["terminal"] == "CORE_KNOWN"
        assert calls == []

        unknown_environment, unknown_service = _prepare_environment(
            client,
            settings,
            dataset="capture-c2aa",
            name="extension-priority",
        )
        extension, evidence = _diagnose(
            client,
            settings,
            environment_id=unknown_environment,
            service_id=unknown_service,
            external_key="extension-priority",
        )

    assert len(calls) == 1
    assert extension["terminal"] == "EXTENSION_KNOWN"
    assert extension["core_or_extension_or_open_world"] == "EXTENSION"
    assert extension["mechanism"] == "opaque-convoy"
    assert extension["provisional_report"] is None
    assert extension["provider_calls"] == 0
    assert evidence["supporting_evidence_refs"]


def test_multiple_extension_admissions_fail_closed(tmp_path: Path, monkeypatch) -> None:
    def match(_self, **kwargs):
        reference = kwargs["memory"].evidence_refs[0].evidence_ref
        return tuple(
            ProductExtensionMatchV1(
                registration_id=f"registration-v234-{suffix}",
                mechanism_slug=f"opaque-{suffix}",
                broad_fault_domain="RUNTIME",
                root_service=kwargs["candidate_services"][0],
                supporting_evidence_refs=(reference,),
            )
            for suffix in ("0123456789abcdef", "fedcba9876543210")
        )

    monkeypatch.setattr(ProductExtensionMatcherV1, "match", match)
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        environment_id, service_id = _prepare_environment(
            client,
            settings,
            dataset="capture-c2aa",
            name="extension-conflict",
        )
        result, _evidence = _diagnose(
            client,
            settings,
            environment_id=environment_id,
            service_id=service_id,
            external_key="extension-conflict",
        )

    assert result["terminal"] == "CONFLICTING_EVIDENCE"
    assert result["core_or_extension_or_open_world"] == "ABSTAIN"
    assert "EXTENSION_MULTIPLE_ADMISSIONS" not in result["capability_limitations"]
    assert result["provider_calls"] == 0


def test_non_fixture_runtime_is_explicitly_diagnosis_limited(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_execute = ProductReadBackendV1._execute

    def execute(self, **kwargs):
        result, fixture_backed, components = original_execute(self, **kwargs)
        if kwargs["action"].source is EvidenceSourceV22.RUNTIME:
            return result, False, components
        return result, fixture_backed, components

    monkeypatch.setattr(ProductReadBackendV1, "_execute", execute)
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        environment_id, service_id = _prepare_environment(
            client,
            settings,
            dataset="capture-c2aa",
            name="runtime-limited",
        )
        result, _evidence = _diagnose(
            client,
            settings,
            environment_id=environment_id,
            service_id=service_id,
            external_key="runtime-limited",
        )

    assert result["terminal"] == "INSUFFICIENT_EVIDENCE"
    assert "RUNTIME_MEMORY_AUTHORITY_UNAVAILABLE" in result["capability_limitations"]
    assert "RUNTIME_DIAGNOSIS_UNAVAILABLE" in result["capability_limitations"]
    assert result["provider_calls"] == 0


def test_evidence_action_binding_rejects_partial_metrics_and_preserves_empty_coverage() -> None:
    catalog = build_action_catalog_v22(
        candidate_services=("payment",),
        topology=StaticTopologyV22.build(services=("payment",), edges=()),
        capability_registry=build_tool_capability_registry_v22(),
        executed_action_ids=(),
        remaining_budget=100.0,
    )
    action = next(
        item
        for item in catalog.registry_actions
        if item.source is EvidenceSourceV22.METRICS
    )
    window = ConnectorWindowV1(
        started_at=NOW - timedelta(minutes=5),
        ended_at=NOW,
    )
    partial = MetricFactV22(
        schema_version="dta-v22.metric-fact.v1",
        service="payment",
        metric_kind=MetricKindV22.ERROR_RATE,
        support_status=MetricSupportStatusV22.SUPPORTED,
        sample_count=4,
        value=0.1,
        unit=METRIC_UNIT_BY_KIND_V22[MetricKindV22.ERROR_RATE],
        window_started_at=window.started_at,
        window_ended_at=window.ended_at,
    )
    partial_result = ConnectorQueryResultV1.build(
        source=EvidenceSourceV22.METRICS,
        status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
        requested_services=("payment",),
        covered_services=("payment",),
        window=window,
        records=(partial,),
        truncated=False,
        safe_error_code=None,
        latency_ms=1.0,
    )
    rejected = _combine_results(
        action=action,
        window=window,
        results=(partial_result,),
    )
    assert rejected.status is ReadSourceStatusV22.FAILURE_SCHEMA
    assert rejected.covered_services == ()

    empty_result = ConnectorQueryResultV1.build(
        source=EvidenceSourceV22.METRICS,
        status=ReadSourceStatusV22.SUCCESS_EMPTY,
        requested_services=("payment",),
        covered_services=("payment",),
        window=window,
        records=(),
        truncated=False,
        safe_error_code=None,
        latency_ms=1.0,
    )
    preserved = _combine_results(
        action=action,
        window=window,
        results=(empty_result,),
    )
    assert preserved.status is ReadSourceStatusV22.SUCCESS_EMPTY
    assert preserved.covered_services == ("payment",)


def test_lost_diagnosis_lease_cannot_bind_evidence_metadata(tmp_path: Path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    jobs = JobRepositoryV1(store)
    queued = jobs.enqueue(ProductJobTypeV1.DIAGNOSIS, {}, now=100.0)
    claimed = jobs.claim_next("expired-worker", lease_seconds=10, now=100.0)
    assert claimed is not None and claimed.job_id == queued.job_id

    payload = {
        "schema_version": "ecomsre.product.diagnosis-result.v1",
        "diagnosis_id": "diag-0123456789abcdef01234567",
        "incident_id": "inc-0123456789abcdef01234567",
        "terminal": DiagnosisTerminalV1.INSUFFICIENT_EVIDENCE,
        "core_or_extension_or_open_world": DiagnosisLaneV1.ABSTAIN,
        "root_service_ids": (),
        "mechanism": None,
        "broad_domain": None,
        "supporting_evidence_refs": (),
        "contradicting_evidence_refs": (),
        "capability_limitations": ("TEST_LIMITATION",),
        "provisional_report": None,
        "action_authority": ActionAuthorityV1.NONE,
        "agent_writes": 0,
        "runbook_executions": 0,
        "provider_calls": 0,
        "memory_sha256": None,
        "created_at": NOW,
    }
    draft = DiagnosisResultV1.model_construct(**payload, result_sha256="0" * 64)
    result = DiagnosisResultV1.model_validate(
        {
            **payload,
            "result_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"result_sha256"})
            ),
        }
    )
    object_store = ContentAddressedObjectStoreV1(
        tmp_path / "objects",
        metadata_store=store,
    )
    diagnoses = DiagnosisRepositoryV1(store, object_store)
    capability_observation = CapabilityEvidenceObservationV0232.build(
        source=EvidenceSourceV22.LOGS,
        capability_matrix_sha256="a" * 64,
        capability_status=SourceCapabilityStatusV1.UNAVAILABLE,
        required_services=("payment",),
        available_services=(),
        reason_code="TEST_LIMITATION",
    )
    limitation_candidate = CapabilityLimitationCandidateV0232.build(
        limitation_code="TEST_LIMITATION",
        category="SOURCE_UNAVAILABLE",
        source=EvidenceSourceV22.LOGS,
        capability_status=SourceCapabilityStatusV1.UNAVAILABLE,
        connector_action_id=None,
        connector_result_sha256=None,
        safe_error_code=None,
        coverage_required_services=("payment",),
        coverage_observed_services=(),
    )
    decision_trace = DiagnosisDecisionTraceV0232.build(
        incident_id=result.incident_id,
        diagnosis_id=result.diagnosis_id,
        known_admission_status="NONE",
        extension_match_count=0,
        no_incident_admissible=False,
        required_coverage_satisfied=False,
        failed_sources=(EvidenceSourceV22.LOGS,),
        novelty_gate_disposition=None,
        novelty_gate_reason_codes=(),
        residual_anomaly_ids=(),
    )

    with pytest.raises(ProductError, match="no longer owns"):
        diagnoses.put(
            result=result,
            observations=(
                {
                    "evidence_ref": capability_observation.evidence_ref,
                    "source": "LOGS",
                    "action_id": "capability:v0232:logs",
                    "payload": capability_observation.model_dump(mode="json"),
                },
            ),
            fence=JobLeaseFenceV1(
                job_id=claimed.job_id,
                claimed_by="expired-worker",
                attempt_count=claimed.attempt_count,
                checked_at=111.0,
            ),
            decision_trace_v0232=decision_trace,
            limitation_candidates_v0232=(limitation_candidate,),
        )

    with store.connect() as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()[
                "count"
            ]
            for table in (
                "evidence_objects",
                "diagnosis_results",
                "diagnosis_evidence_links",
                "diagnosis_evidence_indexes",
            )
        }
    assert counts == {
        "evidence_objects": 0,
        "diagnosis_results": 0,
        "diagnosis_evidence_links": 0,
        "diagnosis_evidence_indexes": 0,
    }


def test_idempotent_diagnosis_reentry_cannot_bind_unlinked_metadata(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        environment_id, service_id = _prepare_environment(
            client,
            settings,
            dataset="capture-c2aa",
            name="idempotent-diagnosis",
        )
        diagnosis, _evidence = _diagnose(
            client,
            settings,
            environment_id=environment_id,
            service_id=service_id,
            external_key="idempotent-diagnosis",
        )

    store = SqliteStoreV1(settings.sqlite_path)
    object_store = ContentAddressedObjectStoreV1(
        settings.object_store_root,
        metadata_store=store,
    )
    diagnoses = DiagnosisRepositoryV1(store, object_store)
    jobs = JobRepositoryV1(store)
    timestamp = time.time()
    queued = jobs.enqueue(ProductJobTypeV1.DIAGNOSIS, {}, now=timestamp)
    claimed = jobs.claim_next(
        "idempotent-worker",
        lease_seconds=60,
        now=timestamp,
    )
    assert claimed is not None and claimed.job_id == queued.job_id
    with store.connect() as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()[
                "count"
            ]
            for table in ("evidence_objects", "diagnosis_evidence_links")
        }

    returned = diagnoses.put(
        result=DiagnosisResultV1.model_validate(diagnosis),
        observations=(
            {
                "evidence_ref": "o:a:logs:payment:unlinked-reentry",
                "source": "LOGS",
                "action_id": "a:logs:payment",
                "payload": {"new": "unlinked-observation"},
            },
        ),
        fence=JobLeaseFenceV1(
            job_id=claimed.job_id,
            claimed_by="idempotent-worker",
            attempt_count=claimed.attempt_count,
            checked_at=timestamp,
        ),
    )
    with store.connect() as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()[
                "count"
            ]
            for table in ("evidence_objects", "diagnosis_evidence_links")
        }

    assert returned.result_sha256 == diagnosis["result_sha256"]
    assert after == before


def test_v0232_diagnosis_reentry_requires_matching_immutable_index(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        environment_id, service_id = _prepare_environment(
            client,
            settings,
            dataset="product-mvp-demo",
            name="immutable-v0232-index",
        )
        diagnosis, evidence = _diagnose(
            client,
            settings,
            environment_id=environment_id,
            service_id=service_id,
            external_key="immutable-v0232-index",
        )
    assert diagnosis["capability_limitations"] == []

    store = SqliteStoreV1(settings.sqlite_path)
    diagnoses = DiagnosisRepositoryV1(
        store,
        ContentAddressedObjectStoreV1(
            settings.object_store_root,
            metadata_store=store,
        ),
    )
    jobs = JobRepositoryV1(store)
    timestamp = time.time()
    queued = jobs.enqueue(ProductJobTypeV1.DIAGNOSIS, {}, now=timestamp)
    claimed = jobs.claim_next("v0232-reentry", lease_seconds=60, now=timestamp)
    assert claimed is not None and claimed.job_id == queued.job_id
    fence = JobLeaseFenceV1(
        job_id=claimed.job_id,
        claimed_by="v0232-reentry",
        attempt_count=claimed.attempt_count,
        checked_at=timestamp,
    )
    observations = tuple(
        {
            "evidence_ref": item["evidence_ref"],
            "source": item["source"],
            "action_id": item["action_id"],
            "payload": item["payload"],
        }
        for item in evidence["objects"]
    )
    conflicting_trace = DiagnosisDecisionTraceV0232.build(
        incident_id=str(diagnosis["incident_id"]),
        diagnosis_id=str(diagnosis["diagnosis_id"]),
        known_admission_status="NONE",
        extension_match_count=0,
        no_incident_admissible=True,
        required_coverage_satisfied=True,
        failed_sources=(),
        novelty_gate_disposition="INSUFFICIENT_EVIDENCE",
        novelty_gate_reason_codes=("IMMUTABLE_CONFLICT_PROBE",),
        residual_anomaly_ids=(),
    )

    with pytest.raises(
        ProductError,
        match="Evidence Index differs",
    ):
        diagnoses.put(
            result=DiagnosisResultV1.model_validate(diagnosis),
            observations=observations,
            fence=fence,
            decision_trace_v0232=conflicting_trace,
            limitation_candidates_v0232=(),
        )

    with store.connect() as connection:
        connection.execute(
            "DELETE FROM diagnosis_evidence_indexes WHERE incident_id = ?",
            (diagnosis["incident_id"],),
        )
    with pytest.raises(ProductError, match="has no v0.2.3.2 Evidence Index"):
        diagnoses.put(
            result=DiagnosisResultV1.model_validate(diagnosis),
            observations=observations,
            fence=fence,
            decision_trace_v0232=conflicting_trace,
            limitation_candidates_v0232=(),
        )
