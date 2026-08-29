from __future__ import annotations

from ecomsre.product.connectors.opensearch_candidates_v0222 import (
    OpenSearchProfileComponentKindV0222,
    OpenSearchProfileRecommendationStatusV0222,
    build_component_candidate_v0222,
    build_profile_candidate_set_v0222,
    render_operator_brief_v0222,
)


CAPTURE_SHA = "a" * 64


def _component(
    alias: str,
    kind: OpenSearchProfileComponentKindV0222,
    accessor: str,
    *,
    score_count: int = 5,
    query_status: str = "NOT_APPLICABLE",
    checkout_matches: int = 0,
    optional: bool = False,
) -> object:
    return build_component_candidate_v0222(
        component_alias=alias,
        kind=kind,
        accessor=accessor,
        encoding_or_mode="OPTIONAL" if optional else "STRING",
        mapping_types=() if optional else ("keyword",),
        field_caps_types=() if optional else ("keyword",),
        sample_presence_count=0 if optional else 5,
        sample_parse_success_count=0 if optional else score_count,
        checkout_match_count=checkout_matches,
        query_verification_status=query_status,
        supporting_capture_refs=(f"objects/sha256/aa/{'b' * 64}",),
        contradicting_capture_refs=(),
    )


def _ambiguous_components() -> tuple[object, ...]:
    return (
        _component(
            "ts-observed",
            OpenSearchProfileComponentKindV0222.TIMESTAMP,
            "observedTimestamp",
        ),
        _component(
            "ts-at",
            OpenSearchProfileComponentKindV0222.TIMESTAMP,
            "@timestamp",
        ),
        _component(
            "source-resource",
            OpenSearchProfileComponentKindV0222.SERVICE_SOURCE,
            "resource.service.name",
            checkout_matches=5,
        ),
        _component(
            "source-flat",
            OpenSearchProfileComponentKindV0222.SERVICE_SOURCE,
            "service.name",
            checkout_matches=5,
        ),
        _component(
            "query-resource",
            OpenSearchProfileComponentKindV0222.SERVICE_QUERY,
            "resource.service.name.keyword",
            query_status="PASS",
        ),
        _component(
            "query-flat",
            OpenSearchProfileComponentKindV0222.SERVICE_QUERY,
            "service.name.keyword",
            query_status="PASS",
        ),
        _component(
            "message-body",
            OpenSearchProfileComponentKindV0222.MESSAGE,
            "body",
        ),
        _component(
            "severity-optional",
            OpenSearchProfileComponentKindV0222.SEVERITY,
            "__OPTIONAL__",
            optional=True,
        ),
        _component(
            "trace-optional",
            OpenSearchProfileComponentKindV0222.TRACE_ID,
            "__OPTIONAL__",
            optional=True,
        ),
    )


def test_ambiguous_evidence_produces_bounded_distinct_candidate_set() -> None:
    candidate_set = build_profile_candidate_set_v0222(
        capture_bundle_sha256=CAPTURE_SHA,
        components=_ambiguous_components(),
    )

    assert 2 <= len(candidate_set.candidates) <= 12
    assert candidate_set.recommendation_status is (
        OpenSearchProfileRecommendationStatusV0222.OPERATOR_SELECTION_REQUIRED
    )
    assert candidate_set.recommended_candidate_alias is None
    assert candidate_set.score_margin == 0
    field_sets = {
        tuple(sorted(candidate.profile_fields.items()))
        for candidate in candidate_set.candidates
    }
    assert len(field_sets) == len(candidate_set.candidates)
    assert {
        candidate.profile_fields["service_source"]
        for candidate in candidate_set.candidates
    } == {"resource.service.name", "service.name"}
    assert all(
        candidate.profile_fields["service_query"].startswith(
            candidate.profile_fields["service_source"]
        )
        for candidate in candidate_set.candidates
    )
    assert all(candidate.supporting_capture_refs for candidate in candidate_set.candidates)

    brief = render_operator_brief_v0222(
        candidate_set=candidate_set,
        capture_session_id="product-v0222-capture-1",
    )
    assert candidate_set.candidate_set_sha256 in brief
    assert "P00" in brief and "P01" in brief
    assert "OPERATOR_SELECTION_REQUIRED" in brief
    assert "type arbitrary field" not in brief.lower()


def test_measured_score_outranks_lexically_earlier_accessor() -> None:
    components = list(_ambiguous_components())
    components[0] = _component(
        "ts-z-strong",
        OpenSearchProfileComponentKindV0222.TIMESTAMP,
        "zObservedTimestamp",
        score_count=5,
    )
    components[1] = _component(
        "ts-a-partial",
        OpenSearchProfileComponentKindV0222.TIMESTAMP,
        "aTimestamp",
        score_count=4,
    )
    candidate_set = build_profile_candidate_set_v0222(
        capture_bundle_sha256=CAPTURE_SHA,
        components=tuple(components),
    )

    assert candidate_set.candidates[0].profile_fields["timestamp"] == (
        "zObservedTimestamp"
    )
    assert all(
        candidate.profile_fields["timestamp"] != "aTimestamp"
        for candidate in candidate_set.candidates
    )


def test_component_without_capture_evidence_is_rejected() -> None:
    try:
        build_component_candidate_v0222(
            component_alias="timestamp-unbound",
            kind=OpenSearchProfileComponentKindV0222.TIMESTAMP,
            accessor="@timestamp",
            encoding_or_mode="RFC3339",
            mapping_types=("date",),
            field_caps_types=("date",),
            sample_presence_count=5,
            sample_parse_success_count=5,
            checkout_match_count=0,
            query_verification_status="NOT_APPLICABLE",
            supporting_capture_refs=(),
            contradicting_capture_refs=(),
        )
    except ValueError as error:
        assert "capture evidence" in str(error)
    else:
        raise AssertionError("unbound component was accepted")


def test_single_measured_profile_is_a_unique_machine_recommendation() -> None:
    components = _ambiguous_components()
    candidate_set = build_profile_candidate_set_v0222(
        capture_bundle_sha256=CAPTURE_SHA,
        components=(
            components[0],
            components[2],
            components[4],
            components[6],
            components[7],
            components[8],
        ),
    )

    assert len(candidate_set.candidates) == 1
    assert candidate_set.recommendation_status is (
        OpenSearchProfileRecommendationStatusV0222.UNIQUE_RECOMMENDATION
    )
    assert candidate_set.recommended_candidate_alias == "P00"
    assert candidate_set.score_margin >= 3


def test_no_valid_candidate_is_explicit_when_hard_constraint_fails() -> None:
    components = list(_ambiguous_components())
    components[0] = _component(
        "ts-observed-partial",
        OpenSearchProfileComponentKindV0222.TIMESTAMP,
        "observedTimestamp",
        score_count=4,
    )
    candidate_set = build_profile_candidate_set_v0222(
        capture_bundle_sha256=CAPTURE_SHA,
        components=tuple(components[2:]) + (components[0],),
    )

    assert candidate_set.candidates == ()
    assert candidate_set.recommendation_status is (
        OpenSearchProfileRecommendationStatusV0222.NO_VALID_CANDIDATE
    )
    assert any("TIMESTAMP_PARSE_INCOMPLETE" in item for item in candidate_set.eliminated_candidates)
