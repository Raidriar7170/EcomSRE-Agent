"""Read-only replay of exact retained cases; no live rerun or evidence rewrite."""

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

LIVE_004 = PRIVATE.parent / "live-004"
LIVE_004_ROOT_HASHES = {
    "cases/P1/result.json": "82beceb9f8cfddf680dd87cffa1bf546adff3385768f8934fb80eb189fcb7dc5",
    "cases/P1/evidence.json": "e22c4c5f5edaa121dfa8454f9a01624083af5011dc0e2e856b982761653d1a5e",
    "cases/P2/result.json": "158a522a288111d18d6b11fed4935ebca8e5885081811f6d546d353f847f8f7d",
    "cases/P2/evidence.json": "18d32f1325bc26e73ee2a656b16fb1a58c3709c7b5e4b40563af15eb71738442",
    "cases/P3/result.json": "573b9924129908de0e180ce80d457521b9b56261f88470bb29cf83f40506b897",
    "cases/P3/evidence.json": "911cd11a0086caf263c73d0d4cc4837d5af54ea49a6e9e2f24d6edac9871d226",
    "cases/H1/result.json": "d6ffabfdbde3d0493035278d1bf7bbc6b216232b16ac9548e52c4a4dd3137838",
    "family-review.json": "f89b53f2c19eb8bf6e45c417fa5fa14f42f96f56c1ad4bbac25f2cc61233db18",
}


@pytest.fixture
def retained_repo(request):
    private, hashes = getattr(request, "param", (PRIVATE, HASHES))
    if not private.exists():
        pytest.skip("private retained evidence is not distributed with Git")
    for name, expected in hashes.items():
        assert hashlib.sha256((private / name).read_bytes()).hexdigest() == expected
    product = private / "product-formal"
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
    for name, expected in hashes.items():
        assert hashlib.sha256((private / name).read_bytes()).hexdigest() == expected


def replay_case(repo, case, private=PRIVATE):
    recorded = json.loads((private / f"cases/{case}/result.json").read_text())
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


@pytest.mark.parametrize(
    "retained_repo", [(LIVE_004, LIVE_004_ROOT_HASHES)], indirect=True
)
@pytest.mark.parametrize("case", ["P1", "P2", "P3"])
def test_retained_positive_root_follows_its_unique_domain_evidence(
    retained_repo, case
):
    result, material, _evidence, identity, _trace = replay_case(
        retained_repo, case, private=LIVE_004
    )
    queue_services = {
        item.service
        for item in material.runtime_input.generic_anomalies
        if item.kind.value == "METRIC_QUEUE_LAG_OUTLIER"
        and item.strength.value == "STRONG"
    }
    assert len(queue_services) == 1
    expected_roots = tuple(
        item.service_id
        for item in identity.services
        if item.logical_service in queue_services
    )
    assert result.terminal.value == "OPEN_WORLD"
    assert result.broad_domain == "CONCURRENCY"
    assert result.root_service_ids == expected_roots
    h1 = json.loads((LIVE_004 / "cases/H1/result.json").read_text())
    assert list(result.root_service_ids) == h1["diagnosis"]["root_service_ids"]
    # This is an unpersisted regression replay, not a replacement measured run.
    assert h1["status"] == "CASE_GATE_FAILED"
    family = json.loads((LIVE_004 / "family-review.json").read_text())
    assert family["majority_root_logical_services"] == ["checkout"]


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
