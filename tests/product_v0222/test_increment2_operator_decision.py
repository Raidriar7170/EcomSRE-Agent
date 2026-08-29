from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ecomsre.product.connectors.opensearch_candidates_v0222 import (
    OpenSearchOperatorDecisionV0222,
    build_operator_profile_decision_v0222,
)
from tests.product_v0222.test_increment2_candidates import (
    CAPTURE_SHA,
    _ambiguous_components,
)
from ecomsre.product.connectors.opensearch_candidates_v0222 import (
    build_profile_candidate_set_v0222,
)


NOW = datetime(2026, 8, 29, 5, 0, tzinfo=UTC)


def test_operator_selection_binds_frozen_candidate_set_and_reviewer() -> None:
    candidate_set = build_profile_candidate_set_v0222(
        capture_bundle_sha256=CAPTURE_SHA,
        components=_ambiguous_components(),
    )
    decision = build_operator_profile_decision_v0222(
        candidate_set=candidate_set,
        selected_candidate_alias="P00",
        reviewer="Raidriar",
        review_note="Selected for machine holdout verification.",
        previous_decisions=(),
        decided_at=NOW,
    )

    assert decision.decision is OpenSearchOperatorDecisionV0222.SELECT_PROFILE
    assert decision.selection_ordinal == 1
    assert decision.candidate_set_sha256 == candidate_set.candidate_set_sha256
    assert len(decision.decision_sha256) == 64


def test_operator_selection_rejects_test_reviewer_unknown_and_duplicate_alias() -> None:
    candidate_set = build_profile_candidate_set_v0222(
        capture_bundle_sha256=CAPTURE_SHA,
        components=_ambiguous_components(),
    )
    with pytest.raises(ValueError, match="TEST_REVIEWER"):
        build_operator_profile_decision_v0222(
            candidate_set=candidate_set,
            selected_candidate_alias="P00",
            reviewer="TEST_REVIEWER",
            review_note="not live",
            previous_decisions=(),
            decided_at=NOW,
        )
    with pytest.raises(ValueError, match="unknown"):
        build_operator_profile_decision_v0222(
            candidate_set=candidate_set,
            selected_candidate_alias="P99",
            reviewer="Raidriar",
            review_note="invalid alias",
            previous_decisions=(),
            decided_at=NOW,
        )
    first = build_operator_profile_decision_v0222(
        candidate_set=candidate_set,
        selected_candidate_alias="P00",
        reviewer="Raidriar",
        review_note="first",
        previous_decisions=(),
        decided_at=NOW,
    )
    with pytest.raises(ValueError, match="cannot be selected twice"):
        build_operator_profile_decision_v0222(
            candidate_set=candidate_set,
            selected_candidate_alias="P00",
            reviewer="Raidriar",
            review_note="duplicate",
            previous_decisions=(first,),
            decided_at=NOW,
        )


def test_operator_selection_stops_after_two_distinct_candidates() -> None:
    candidate_set = build_profile_candidate_set_v0222(
        capture_bundle_sha256=CAPTURE_SHA,
        components=_ambiguous_components(),
    )
    first = build_operator_profile_decision_v0222(
        candidate_set=candidate_set,
        selected_candidate_alias="P00",
        reviewer="Raidriar",
        review_note="first",
        previous_decisions=(),
        decided_at=NOW,
    )
    second = build_operator_profile_decision_v0222(
        candidate_set=candidate_set,
        selected_candidate_alias="P01",
        reviewer="Raidriar",
        review_note="second",
        previous_decisions=(first,),
        decided_at=NOW,
    )
    with pytest.raises(ValueError, match="selection budget exhausted"):
        build_operator_profile_decision_v0222(
            candidate_set=candidate_set,
            selected_candidate_alias="P02",
            reviewer="Raidriar",
            review_note="third",
            previous_decisions=(first, second),
            decided_at=NOW,
        )
