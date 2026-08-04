from __future__ import annotations

from collections import Counter

import pytest
from pydantic import ValidationError

from ecomsre.phase5b.dry_run import build_mock_dry_run_report
from ecomsre.phase5b.worker import MockWorkerRequest


def test_mock_worker_contract_excludes_evaluator_identity_and_truth() -> None:
    fields = set(MockWorkerRequest.model_fields)
    assert fields == {"instance_id", "variant", "agent_visible"}
    with pytest.raises(ValidationError, match="extra"):
        MockWorkerRequest.model_validate(
            {
                "instance_id": "a" * 32,
                "variant": "SINGLE_AGENT_V2",
                "agent_visible": {"synthetic_decision_signal": "ABSTAIN"},
                "template_id": "synthetic-template-a",
            }
        )


def test_mock_dry_run_is_deterministic_paired_and_retains_failure() -> None:
    first = build_mock_dry_run_report()
    second = build_mock_dry_run_report()
    assert first == second
    assert first["report_type"] == "MOCK_PROTOCOL_DRY_RUN"
    assert first["evidence_class"] == "NOT_MODEL_EVIDENCE"
    assert first["provider_call_count"] == 0
    assert first["run_count"] == 12
    assert first["failure_denominator_count"] == 1
    schedule = first["schedule"]
    assert isinstance(schedule, list)
    by_instance = Counter(item["instance_id"] for item in schedule)
    assert sorted(by_instance.values()) == [3, 3, 3, 3]
    for counts in first["call_position_balance"].values():
        assert max(counts) - min(counts) <= 1


def test_mock_dry_run_needs_no_network_or_hidden_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden in the protocol dry run")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    report = build_mock_dry_run_report()
    assert report["actual_hidden_pack_used"] is False
    assert report["ground_truth_read"] is False
