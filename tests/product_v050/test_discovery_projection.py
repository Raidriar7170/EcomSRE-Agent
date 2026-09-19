from copy import deepcopy

from ecomsre.product.knowledge.evolution_v050 import proposal_observation_view


def test_truncated_records_are_explicitly_omitted_without_changing_evidence():
    raw = {
        "evidence_ref": "e:truncated",
        "source": "LOGS",
        "status": "SUCCESS_NONEMPTY",
        "truncated": True,
        "covered_services": ["service"],
        "window": {"started_at": "a", "ended_at": "b"},
        "records": [{"service": "service"}] * 200,
    }
    before = deepcopy(raw)
    view = proposal_observation_view(raw)
    assert raw == before
    assert view["records"] == []
    assert view["record_count"] == 200
    assert view["records_omitted"] == "TRUNCATED_NOT_CANDIDATE_EVIDENCE"
    assert {
        k: v
        for k, v in view.items()
        if k not in {"records", "record_count", "records_omitted"}
    } == {k: v for k, v in raw.items() if k != "records"}


def test_complete_resource_and_empty_source_views_are_unchanged():
    for status, records in [
        ("SUCCESS_NONEMPTY", [{"cpu_percent": 2.0}]),
        ("SUCCESS_EMPTY", []),
    ]:
        raw = {
            "truncated": False,
            "status": status,
            "records": records,
            "resource_dependency": {"sample_count": 5},
        }
        assert proposal_observation_view(raw) == raw
