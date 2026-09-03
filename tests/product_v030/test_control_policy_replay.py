"""Read-only replay of exact retained controls; no live rerun or evidence rewrite."""

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.incidents.diagnosis_bridge import ProductDiagnosisBridgeV1
from ecomsre.product.incidents.read_backend import ProductReadAcquisitionV1
from ecomsre.product.knowledge.repository import KnowledgeRepositoryV1
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.pilot.control_gate_v030 import (
    case_gate_passes_v030,
    control_record_passes_v030,
    evaluate_c1_queue_negative_v030,
)


PRIVATE = Path(__file__).resolve().parents[2] / ".local/product-v030/live-003"
HASHES = {
    "baseline-resource-repair/baseline-result.json": "7b5e390906e73f0038a94ae0a10a14c60dc9ab4c31cf9bd7e4f7a09e3e24fe0b",
    "cases/N0-A/evidence.json": "63ab1306b7ded640ae3b7102e535a32c6c8cfcf9f81c48f735f78a65d41e691a",
    "cases/N0-B/evidence.json": "3ada24273cdf0ba507f8c3d5f7e964e50503593b6693fba5b0c7b58919785d59",
    "cases/C1/evidence.json": "1682ef75b8f660e172b0a980a8b1f68989b6d56767e57510096d6b7cc5953613",
    "cases/N0-A/result.json": "ac890dacbac82c932cd6bb322a95cb1cfbebc54ed6a675ee0d86654a25e074ed",
    "cases/N0-B/result.json": "e82c646d187aaaf92390aa93c83b44e629aa31f550a272fa7e1e7b5e01771999",
    "cases/C1/result.json": "fd162f7f10a1ff602cea87af297d14136c54a2201f216b116ede12fea2bbc468",
}


@pytest.fixture
def retained_repo():
    if not PRIVATE.exists():
        pytest.skip("private retained live-003 evidence is not distributed with Git")
    for name, expected in HASHES.items():
        assert hashlib.sha256((PRIVATE / name).read_bytes()).hexdigest() == expected
    product = PRIVATE / "product-formal"
    database = product / "product.sqlite3"
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    class ReadOnlyStore:
        @contextmanager
        def connect(self):
            connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            try:
                yield connection
            finally:
                connection.close()

    store = ReadOnlyStore()
    objects = ContentAddressedObjectStoreV1.__new__(ContentAddressedObjectStoreV1)
    objects.root = product / "objects"
    objects.sha_root = objects.root / "sha256"
    objects.metadata_store = store
    yield KnowledgeRepositoryV1(store, objects)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    for name, expected in HASHES.items():
        assert hashlib.sha256((PRIVATE / name).read_bytes()).hexdigest() == expected


def replay_case(repo, case):
    recorded = json.loads((PRIVATE / f"cases/{case}/result.json").read_text())
    incident_id = recorded["incident"]["incident_id"]
    material = repo._shadow_runtime_material(incident_id)
    original = repo._diagnosis(incident_id)
    evidence = repo._evidence(incident_id, original.diagnosis_id)
    snapshots = {
        item.action_id: item.payload
        for item in evidence.objects
        if isinstance(item.payload.get("action"), dict)
    }
    coverage = {}
    for snapshot in snapshots.values():
        outcome = snapshot["read_outcome"]
        source = EvidenceSourceV22(outcome["source"])
        coverage.setdefault(source, set()).update(
            snapshot["connector_result"]["covered_services"]
        )
    acquisition = ProductReadAcquisitionV1(
        raw_outcomes=material.raw_outcomes,
        memory_outcomes=material.memory_outcomes,
        snapshots=tuple(snapshots.values()),
        covered_services_by_source={
            key: tuple(sorted(value)) for key, value in coverage.items()
        },
        capability_limitations=original.capability_limitations,
        capability_observations_v0232=(),
        capability_limitation_candidates_v0232=(),
    )
    identity = ServiceCatalogRepositoryV1(repo.store).get_map(
        material.incident.environment_id
    )
    result, observations, trace = ProductDiagnosisBridgeV1().diagnose(
        incident=material.incident,
        baseline=material.baseline,
        identity_map=identity,
        acquisition=acquisition,
        diagnosis_id=None,
        created_at=original.created_at,
    )
    assert result.memory_sha256 == original.memory_sha256
    assert set(item["evidence_ref"] for item in observations).issuperset(
        result.supporting_evidence_refs
    )
    assert result.capability_limitations == original.capability_limitations
    return result, material, evidence, identity, trace


@pytest.mark.parametrize("case", ["N0-A", "N0-B"])
def test_exact_retained_healthy_control_becomes_no_incident(retained_repo, case):
    result, material, evidence, _identity, _trace = replay_case(retained_repo, case)
    assert result.terminal.value == "NO_INCIDENT"
    assert not result.provisional_report
    assert any(item.source.value == "RESOURCES" for item in evidence.objects)
    assert not any(
        item.kind.value == "RESOURCE_MEMORY_TREND"
        for item in material.runtime_input.generic_anomalies
    )


def test_exact_retained_c1_core_diagnosis_and_gaps_are_preserved(retained_repo):
    result, _material, _evidence, identity, _trace = replay_case(retained_repo, "C1")
    assert result.terminal.value == "CORE_KNOWN"
    assert result.mechanism == "CONFIGURATION_ERROR"
    assert result.root_service_ids == tuple(
        item.service_id
        for item in identity.services
        if item.logical_service == "payment"
    )
    assert result.capability_limitations == (
        "SOURCE_LOGS_COVERAGE_GAP",
        "SOURCE_TRACES_COVERAGE_GAP",
    )


def test_exact_retained_c1_passes_scoped_queue_negative_gate(retained_repo):
    recorded = json.loads((PRIVATE / "cases/C1/result.json").read_text())
    queue_negative = evaluate_c1_queue_negative_v030(
        retained_repo, recorded["incident"]["incident_id"]
    )
    assert queue_negative["status"] == "CONCLUSIVE"
    assert queue_negative["intended_clause_state"] is False
    assert queue_negative["queue_values"] == [0.0]
    assert queue_negative["queue_threshold"] == 20.0
    assert (
        queue_negative["capability_limitations_preserved"]
        == recorded["diagnosis"]["capability_limitations"]
    )
    assert case_gate_passes_v030(
        {**recorded, "queue_negative_evidence": queue_negative}, "CORE_KNOWN"
    )
    passing = {**recorded, "status": "PASS", "queue_negative_evidence": queue_negative}
    assert control_record_passes_v030(passing)
    for key, value in [
        ("status", "INCONCLUSIVE"),
        ("intended_clause_state", None),
        ("incident_id", "different"),
    ]:
        assert not control_record_passes_v030(
            {**passing, "queue_negative_evidence": {**queue_negative, key: value}}
        )


@pytest.mark.parametrize(
    "fault",
    [
        "queue_missing",
        "queue_failed",
        "queue_high",
        "queue_short",
        "runtime_missing",
        "runtime_unhealthy",
        "wrong_root",
        "unresolved_support",
    ],
)
def test_c1_relevance_gate_fails_closed_on_required_evidence(
    retained_repo, monkeypatch, fault
):
    from ecomsre.dta_v2.v22.read_contracts import ReadSourceStatusV22

    incident_id = json.loads((PRIVATE / "cases/C1/result.json").read_text())[
        "incident"
    ]["incident_id"]
    material = retained_repo._shadow_runtime_material(incident_id)
    outcomes = []
    for item in material.raw_outcomes:
        if "queue-lag" in item.action_id:
            if fault == "queue_missing":
                continue
            if fault == "queue_failed":
                item = item.model_copy(
                    update={"status": ReadSourceStatusV22.FAILURE_UNAVAILABLE}
                )
            if fault in {"queue_high", "queue_short"}:
                update = (
                    {"value": 20.0} if fault == "queue_high" else {"sample_count": 2}
                )
                item = item.model_copy(
                    update={
                        "records": tuple(
                            r.model_copy(update=update) for r in item.records
                        )
                    }
                )
        if item.source.value == "RUNTIME":
            if fault == "runtime_missing":
                item = item.model_copy(
                    update={
                        "records": tuple(
                            r for r in item.records if r.service != "fraud-detection"
                        )
                    }
                )
            if fault == "runtime_unhealthy":
                item = item.model_copy(
                    update={
                        "records": tuple(
                            r.model_copy(update={"healthy": False})
                            if r.service == "fraud-detection"
                            else r
                            for r in item.records
                        )
                    }
                )
        outcomes.append(item)
    monkeypatch.setattr(
        retained_repo,
        "_shadow_runtime_material",
        lambda _: replace(material, raw_outcomes=tuple(outcomes)),
    )
    if fault in {"wrong_root", "unresolved_support"}:
        original = retained_repo._diagnosis(incident_id)
        update = (
            {"root_service_ids": ("svc-not-payment",)}
            if fault == "wrong_root"
            else {"supporting_evidence_refs": ("missing",)}
        )
        monkeypatch.setattr(
            retained_repo, "_diagnosis", lambda _: original.model_copy(update=update)
        )
    assert (
        evaluate_c1_queue_negative_v030(retained_repo, incident_id)["status"]
        == "INCONCLUSIVE"
    )
