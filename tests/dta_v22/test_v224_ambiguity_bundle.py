from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.ci.verify_dta_v224_historical_results import (
    DEFAULT_MANIFEST,
    verify_historical_results_v224,
)
from ecomsre.dta_v2.v22.ambiguity_audit_v224 import (
    audit_v223_target_ambiguity_v224,
)
from ecomsre.dta_v2.v22.memory import PredicateThresholdsV22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22
from ecomsre.dta_v2.v22.replay import ReplayCaptureV22
from ecomsre.dta_v2.v22.replay_target_coverage_v224 import (
    ReplayTargetCoverageModeV224,
    build_replay_target_coverage_v224,
    complete_resource_records_v224,
    normal_resource_record_v224,
    require_capture_matches_target_coverage_v224,
)


ROOT = Path(__file__).resolve().parents[2]


def test_v224_binds_every_merged_v22_through_v223_result_byte() -> None:
    assert verify_historical_results_v224(
        repository_root=ROOT,
        manifest_path=DEFAULT_MANIFEST,
    ) == 29


def test_v224_audit_proves_the_three_frozen_wrong_target_cases_are_symmetric() -> None:
    report = audit_v223_target_ambiguity_v224(repository_root=ROOT)

    assert report.wrong_target_case_ids == ("d05", "d06", "d08")
    assert report.ambiguity_audit_passed is True
    by_id = {item.case_id: item for item in report.cases}
    for case_id in report.wrong_target_case_ids:
        item = by_id[case_id]
        assert item.resource_ambiguity_set_size == 2
        assert item.single_target_preference_available is False
        assert item.actual_resource_action_target != item.truth_target_service
        assert item.actual_outcome_class == "EMPTY_CAPTURED"
        assert item.actual_predicate_yield is False


def test_v224_counterfactual_pairs_reverse_truth_without_changing_visible_signature() -> None:
    report = audit_v223_target_ambiguity_v224(repository_root=ROOT)
    by_id = {item.case_id: item for item in report.cases}

    for left_id, right_id in (("d05", "d06"), ("d07", "d08")):
        left = by_id[left_id]
        right = by_id[right_id]
        assert left.mechanism == right.mechanism
        assert left.truth_target_ordinal != right.truth_target_ordinal
        assert len(set(left.target_visibility_signatures)) == 1
        assert len(set(right.target_visibility_signatures)) == 1


def test_v224_visibility_signature_declares_no_truth_or_future_fields() -> None:
    report = audit_v223_target_ambiguity_v224(repository_root=ROOT)
    assert report.signature_input_fields == (
        "runtime_predicates",
        "metric_predicates_and_support",
        "topology_role",
        "already_covered_sources",
        "current_gap_requirements",
        "negative_coverage",
    )
    assert set(report.signature_input_fields).isdisjoint(
        {"truth_target", "future_read_result", "case_id", "fixture_modifier"}
    )


def test_v224_target_complete_resources_requires_one_record_per_candidate() -> None:
    coverage = build_replay_target_coverage_v224(
        source=EvidenceSourceV22.RESOURCES,
        candidate_services=("checkout", "payment"),
        covered_target_services=("checkout", "payment"),
    )
    assert coverage.coverage_mode is ReplayTargetCoverageModeV224.TARGET_COMPLETE

    empty_capture = ReplayCaptureV22(
        schema_version="dta-v22.replay-capture.v1",
        captured_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        metrics=(),
        logs=(),
        traces=(),
        runtime=(),
        resources=(normal_resource_record_v224(service="checkout"),),
        changes=(),
        source_failures=(),
    )
    with pytest.raises(ValueError, match="TARGET_COMPLETE Resources coverage"):
        require_capture_matches_target_coverage_v224(
            coverage=coverage,
            capture=empty_capture,
        )


def test_v224_normal_resource_records_are_explicit_and_below_strong_thresholds() -> None:
    records = complete_resource_records_v224(
        candidate_services=("checkout", "payment"),
        records=(),
    )
    thresholds = PredicateThresholdsV22.frozen()

    assert tuple(item.service for item in records) == ("checkout", "payment")
    assert all(len(item.samples) == 5 for item in records)
    assert all(
        max(sample.cpu_percent for sample in item.samples)
        < thresholds.cpu_strong_p95_percent
        and item.memory_slope_bytes_per_second
        < thresholds.memory_growth_strong_bytes_per_second
        for item in records
    )


def test_v224_target_coverage_metadata_is_canonical_and_fail_closed() -> None:
    with pytest.raises(ValueError, match="canonical"):
        build_replay_target_coverage_v224(
            source=EvidenceSourceV22.RESOURCES,
            candidate_services=("payment", "checkout"),
            covered_target_services=("checkout",),
        )

    partial = build_replay_target_coverage_v224(
        source=EvidenceSourceV22.RESOURCES,
        candidate_services=("checkout", "payment"),
        covered_target_services=("checkout",),
    )
    assert partial.coverage_mode is ReplayTargetCoverageModeV224.TARGET_PARTIAL
