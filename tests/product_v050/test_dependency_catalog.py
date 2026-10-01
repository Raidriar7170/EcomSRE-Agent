from datetime import UTC, datetime
from types import SimpleNamespace

from ecomsre.product.knowledge.observations_v050 import dependency_catalog


def catalog_fixture():
    incident = SimpleNamespace(
        diagnosis_observed_at=datetime(2026, 9, 18, tzinfo=UTC),
        candidate_logical_services=("payment",),
    )
    entry = {
        "action_id": "read-fixture",
        "source": "RESOURCES",
        "targets": ["payment"],
        "template": {"sampling_window_seconds": 10, "sample_count": 5},
        "window": {
            "started_at": "2026-09-17T23:59:30Z",
            "ended_at": "2026-09-18T00:00:00Z",
        },
    }
    return incident, entry


def test_initial_resource_is_not_deployable_supplement():
    incident, entry = catalog_fixture()
    result = dependency_catalog(incident, [entry], [])
    assert result[0]["availability"] == "LEGAL_NOT_COLLECTED"
    assert result[0]["supporting_refs"] == []
    assert result[0]["dependency"]["query_window_seconds"] == 30
    assert result[0]["fields"] == {"cpu_percent": "PERCENT", "memory_bytes": "BYTES"}


def test_verified_dependency_and_wrong_target_or_window():
    incident, entry = catalog_fixture()
    dep = dependency_catalog(incident, [entry], [])[0]["dependency"]
    observation = dict(
        resource_dependency=dep,
        targets=["payment"],
        window=entry["window"],
        source="RESOURCES",
        evidence_ref="investigation:fixture",
        status="SUCCESS_NONEMPTY",
        truncated=False,
        covered_services=["payment"],
        records=[{"service": "payment"}],
    )
    assert (
        dependency_catalog(incident, [entry], [observation])[0]["availability"]
        == "BOUND_OBSERVATION"
    )
    for change in (
        {"targets": ["checkout"]},
        {"window": dict(entry["window"], ended_at="2026-09-17T23:59:30Z")},
    ):
        row = dependency_catalog(incident, [entry], [observation | change])[0]
        assert row["availability"] == "LEGAL_NOT_COLLECTED"
        assert not row["supporting_refs"]
    failed = dependency_catalog(
        incident, [entry], [observation | {"status": "FAILED"}]
    )[0]
    assert failed["availability"] == "COLLECTED_INCOMPLETE"
    assert failed["supporting_refs"] == []
