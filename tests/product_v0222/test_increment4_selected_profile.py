from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

from ecomsre.product.connectors.opensearch_candidates_v0222 import (
    OpenSearchOperatorDecisionLedgerV0222,
    OpenSearchProfileCandidateSetV0222,
)
from ecomsre.product.connectors.opensearch_profile_v0222 import (
    OFFLINE_PROFILE_PASS_V0222,
    HOLDOUT_VERIFICATION_PASS_V0222,
    OpenSearchProfileStatusV0222,
    assemble_selected_profile_v0222,
    build_sanitized_selected_profile_fixture_v0222,
    evaluate_offline_selected_profile_v0222,
    evaluate_holdout_verification_v0222,
)


ROOT = Path(__file__).resolve().parents[2]


def _candidate_set() -> OpenSearchProfileCandidateSetV0222:
    return OpenSearchProfileCandidateSetV0222.model_validate_json(
        (ROOT / "config/product-v0222/opensearch/candidate-set.json").read_text(
            encoding="utf-8"
        )
    )


def _decision() -> OpenSearchOperatorDecisionLedgerV0222:
    return OpenSearchOperatorDecisionLedgerV0222.model_validate_json(
        (ROOT / "config/product-v0222/opensearch/operator-decision.json").read_text(
            encoding="utf-8"
        )
    )


def _private_shape() -> dict[str, object]:
    return {
        "took": 4,
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
        "hits": {
            "total": {"value": 2, "relation": "eq"},
            "max_score": None,
            "hits": [
                {
                    "_index": "private-index",
                    "_id": "private-id-1",
                    "_score": None,
                    "_source": {
                        "@timestamp": "2026-08-29T04:00:01Z",
                        "resource": {
                            "service.name": "checkoutservice",
                            "service.version": "private-version",
                        },
                        "body": "private checkout message",
                        "severity": {"number": 9, "text": "INFO"},
                    },
                },
                {
                    "_index": "private-index",
                    "_id": "private-id-2",
                    "_score": None,
                    "_source": {
                        "@timestamp": "2026-08-29T04:00:02Z",
                        "resource": {
                            "service.name": "checkoutservice",
                            "service.version": "another-private-version",
                        },
                        "body": "another private message",
                        "severity": {"number": 13, "text": "ERROR"},
                        "traceId": "b" * 32,
                    },
                },
            ],
        },
    }


def test_p01_profile_and_fixture_preserve_observed_dotted_key_shape() -> None:
    candidate_set = _candidate_set()
    decision = _decision()
    profile = assemble_selected_profile_v0222(
        candidate_set=candidate_set,
        decision_ledger=decision,
        index_pattern="otel-logs-*",
        mapping_response_sha256="1" * 64,
        field_caps_response_sha256="2" * 64,
        structural_sample_response_sha256="3" * 64,
        structural_sample_response=_private_shape(),
    )
    fixture = build_sanitized_selected_profile_fixture_v0222(
        live_response=_private_shape(),
        profile=profile,
        capture_bundle_sha256=candidate_set.capture_bundle_sha256,
        private_sample_response_sha256="3" * 64,
        started_at=datetime(2026, 8, 29, 4, 0, tzinfo=UTC),
        ended_at=datetime(2026, 8, 29, 4, 5, tzinfo=UTC),
        service_aliases={"checkout": "checkout", "checkoutservice": "checkout"},
    )
    report = evaluate_offline_selected_profile_v0222(
        fixture=fixture,
        profile=profile,
        offline_changed_iteration_count=2,
    )

    assert profile.profile_status is OpenSearchProfileStatusV0222.OPERATOR_SELECTED
    assert profile.selected_candidate_alias == "P01"
    assert profile.candidate_set_sha256 == candidate_set.candidate_set_sha256
    assert profile.operator_decision_sha256 == decision.decisions[-1].decision_sha256
    assert profile.severity_extraction.extraction.paths == ("severity.text",)
    first_source = fixture.response["hits"]["hits"][0]["_source"]  # type: ignore[index]
    assert "service.name" in first_source["resource"]  # type: ignore[operator]
    assert "service" not in first_source["resource"]  # type: ignore[operator]
    assert first_source["severity"] == {"number": 1, "text": "INFO"}  # type: ignore[index]
    serialized = json.dumps(fixture.model_dump(mode="json"), sort_keys=True)
    assert "private checkout message" not in serialized
    assert "another private message" not in serialized
    assert "private-version" not in serialized
    assert report.terminal == OFFLINE_PROFILE_PASS_V0222
    assert report.accepted_checkout_record_count == 2
    assert [item.disposition for item in report.record_dispositions] == [
        "ACCEPTED",
        "ACCEPTED",
    ]
    assert report.rejection_fraction == 0
    assert report.outer_schema_failure_code is None
    assert report.timestamp_parse_failures == 0
    assert report.service_alias_failures == 0
    assert report.message_extraction_failures == 0
    assert report.observer_projection_failures == 0


def test_selected_profile_offline_acceptance_records_each_rejection() -> None:
    candidate_set = _candidate_set()
    profile = assemble_selected_profile_v0222(
        candidate_set=candidate_set,
        decision_ledger=_decision(),
        index_pattern="otel-logs-*",
        mapping_response_sha256="1" * 64,
        field_caps_response_sha256="2" * 64,
        structural_sample_response_sha256="3" * 64,
        structural_sample_response=_private_shape(),
    )
    fixture = build_sanitized_selected_profile_fixture_v0222(
        live_response=_private_shape(),
        profile=profile,
        capture_bundle_sha256=candidate_set.capture_bundle_sha256,
        private_sample_response_sha256="3" * 64,
        started_at=datetime(2026, 8, 29, 4, 0, tzinfo=UTC),
        ended_at=datetime(2026, 8, 29, 4, 5, tzinfo=UTC),
        service_aliases={"checkout": "checkout", "checkoutservice": "checkout"},
    )
    broken = fixture.model_copy(deep=True)
    broken.response["hits"]["hits"][0]["_source"].pop("body")  # type: ignore[index,union-attr]
    broken = broken.rebind()

    report = evaluate_offline_selected_profile_v0222(
        fixture=broken,
        profile=profile,
        offline_changed_iteration_count=2,
    )

    assert report.terminal == "BLOCKED_ECOMSRE_PRODUCT_V0222_OFFLINE_PROFILE"
    assert [item.disposition for item in report.record_dispositions] == [
        "REJECTED",
        "ACCEPTED",
    ]
    assert report.record_dispositions[0].rejection_code == (
        "OPENSEARCH_MESSAGE_FIELD_MISSING"
    )
    assert report.rejection_fraction == 0.5


def test_fresh_holdout_requires_queries_and_profile_driven_parse() -> None:
    candidate_set = _candidate_set()
    profile = assemble_selected_profile_v0222(
        candidate_set=candidate_set,
        decision_ledger=_decision(),
        index_pattern="otel-logs-*",
        mapping_response_sha256="1" * 64,
        field_caps_response_sha256="2" * 64,
        structural_sample_response_sha256="3" * 64,
        structural_sample_response=_private_shape(),
    )
    selected_bytes_sha256 = hashlib.sha256(
        (profile.model_dump_json(indent=2) + "\n").encode()
    ).hexdigest()
    holdout = evaluate_holdout_verification_v0222(
        profile=profile,
        service_aggregation_response={
            "took": 1,
            "timed_out": False,
            "_shards": {"failed": 0},
            "hits": {"total": {"value": 0}, "hits": []},
            "aggregations": {
                "services": {
                    "buckets": [{"key": "checkoutservice", "doc_count": 2}]
                }
            },
        },
        timestamp_range_response={
            "took": 1,
            "timed_out": False,
            "_shards": {"failed": 0},
            "hits": {"total": {"value": 2}, "hits": []},
        },
        targeted_response=_private_shape(),
        started_at=datetime(2026, 8, 29, 4, 0, tzinfo=UTC),
        ended_at=datetime(2026, 8, 29, 4, 5, tzinfo=UTC),
        service_aliases={"checkout": "checkout", "checkoutservice": "checkout"},
        read_only_request_count=3,
        transport_retry_count=0,
        selected_profile_file_sha256_before=selected_bytes_sha256,
        selected_profile_file_sha256_after=selected_bytes_sha256,
        cleanup="CLEAN",
    )

    assert holdout.terminal == HOLDOUT_VERIFICATION_PASS_V0222
    assert holdout.service_aggregation_observed_aliases == ("checkoutservice",)
    assert holdout.timestamp_range_query_status == "PASS"
    assert holdout.targeted_checkout_query_status == "PASS"
    assert holdout.accepted_checkout_record_count == 2
    assert holdout.profile_bytes_unchanged is True
    assert holdout.read_only_request_count == 3
    assert holdout.outer_schema_failure_count == 0
