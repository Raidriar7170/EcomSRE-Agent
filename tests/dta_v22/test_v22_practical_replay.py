from __future__ import annotations

from pathlib import Path

from ecomsre.dta_v2.v22.read_contracts import (
    MetricKindV22,
    MetricSupportStatusV22,
)
from ecomsre.dta_v2.v22.practical_replay import (
    load_and_normalize_practical_case_v22,
)


ROOT = Path(__file__).resolve().parents[2]


def test_legacy_replay_normalization_is_agent_visible_and_maps_unsupported() -> None:
    case = load_and_normalize_practical_case_v22(
        ROOT / "config/dta-v2/evaluation/development/agent-visible/dta-case-001.json"
    )

    assert case.case_id == "dta-case-001"
    assert case.candidate_services == ("payment",)
    assert case.capture.changes == ()
    unsupported = next(
        item
        for item in case.capture.metrics
        if item.metric_kind is MetricKindV22.CPU_PERCENT
    )
    assert unsupported.support_status is MetricSupportStatusV22.UNSUPPORTED
    assert unsupported.value is None
    assert case.source_bytes_sha256 == (
        "258c80a419ae4b7760205f05d8c5cf44ca192effb98ea891c5a3bd99d5fdfeff"
    )


def test_dependency_normalization_derives_visible_two_service_topology() -> None:
    case = load_and_normalize_practical_case_v22(
        ROOT
        / "config/dta-v21/evaluation/development/agent-visible/dta21-case-010.json"
    )

    assert case.candidate_services == ("checkout", "shipping")
    assert case.topology_edges == (("checkout", "shipping"),)
    assert {item.service for item in case.capture.runtime} == {
        "checkout",
        "shipping",
    }
    assert case.normalization_notes == (
        "baseline-derived healthy bootstrap facts added for visible missing services",
        "legacy resource samples resampled to the canonical read window",
        "legacy trace paths compressed without changing span service or parent",
    )


def test_source_errors_are_typed_without_reading_evaluator_truth() -> None:
    case = load_and_normalize_practical_case_v22(
        ROOT / "config/dta-v21/evaluation/development/agent-visible/dta21-case-006.json"
    )

    assert tuple(item.source.value for item in case.capture.source_failures) == (
        "TRACES",
    )
    assert all("truth" not in note.casefold() for note in case.normalization_notes)
