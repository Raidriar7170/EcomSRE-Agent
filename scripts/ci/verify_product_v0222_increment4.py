"""Verify the Product v0.2.2.2 selected-profile and holdout checkpoint."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_candidates_v0222 import (
    OpenSearchOperatorDecisionLedgerV0222,
    OpenSearchProfileCandidateSetV0222,
)
from ecomsre.product.connectors.opensearch_profile_v0222 import (
    HOLDOUT_VERIFICATION_PASS_V0222,
    OFFLINE_PROFILE_PASS_V0222,
    OpenSearchHoldoutVerificationReportV0222,
    OpenSearchNormalizationProfileV0222,
    OpenSearchOfflineProfileReportV0222,
    OpenSearchProfileStatusV0222,
    OpenSearchSelectedProfileFixtureV0222,
)


EXPECTED_CAPTURE_BUNDLE_SHA256 = (
    "4084941d8368c4f74ec2db95ac2215f36c9531367f9904b9b90cd653bceeea94"
)
EXPECTED_CANDIDATE_SET_SHA256 = (
    "f3aeaf272ab199c1284238c9e7785ec89f46b1cb54ad1608188a052c27f9d4de"
)
EXPECTED_INITIAL_BLOCKER = "BLOCKED_ECOMSRE_PRODUCT_V0222_CAPTURE_PROTOCOL"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Product v0.2.2.2 artifact is not an object: {path}")
    return payload


def _string_values(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        return tuple(item for child in value.values() for item in _string_values(child))
    if isinstance(value, (list, tuple)):
        return tuple(item for child in value for item in _string_values(child))
    return ()


def verify_product_v0222_increment4(root: Path) -> dict[str, object]:
    repository = root.resolve(strict=True)
    candidate_set = OpenSearchProfileCandidateSetV0222.model_validate_json(
        (
            repository / "config/product-v0222/opensearch/candidate-set.json"
        ).read_text(encoding="utf-8")
    )
    decisions = OpenSearchOperatorDecisionLedgerV0222.model_validate_json(
        (
            repository / "config/product-v0222/opensearch/operator-decision.json"
        ).read_text(encoding="utf-8")
    )
    active = OpenSearchNormalizationProfileV0222.model_validate_json(
        (
            repository
            / "config/product-v0222/opensearch/normalization-profile.json"
        ).read_text(encoding="utf-8")
    )
    fixture = OpenSearchSelectedProfileFixtureV0222.model_validate_json(
        (
            repository
            / "tests/fixtures/product_v0222/opensearch_selected_profile_shape.json"
        ).read_text(encoding="utf-8")
    )
    offline = OpenSearchOfflineProfileReportV0222.model_validate_json(
        (
            repository / "docs/analysis/product-v0222-offline-profile.json"
        ).read_text(encoding="utf-8")
    )
    holdout = OpenSearchHoldoutVerificationReportV0222.model_validate_json(
        (
            repository
            / "docs/analysis/product-v0222-holdout-verification.json"
        ).read_text(encoding="utf-8")
    )
    progress = _load_json(
        repository / "docs/analysis/product-v0222-progress.json"
    )
    progress_sha256 = progress.pop("progress_sha256", None)
    if progress_sha256 != semantic_sha256_v22(progress):
        raise ValueError("Product v0.2.2.2 Increment 4 progress digest differs")

    if (
        candidate_set.capture_bundle_sha256 != EXPECTED_CAPTURE_BUNDLE_SHA256
        or candidate_set.candidate_set_sha256 != EXPECTED_CANDIDATE_SET_SHA256
        or decisions.candidate_set_sha256 != candidate_set.candidate_set_sha256
        or len(decisions.decisions) != 1
        or decisions.decisions[0].selected_candidate_alias != "P01"
        or decisions.decisions[0].reviewer.strip().upper() == "TEST_REVIEWER"
    ):
        raise ValueError("Product v0.2.2.2 Increment 4 operator binding differs")
    if (
        active.profile_status is not OpenSearchProfileStatusV0222.ACTIVE
        or active.selected_candidate_alias != "P01"
        or active.capture_bundle_sha256 != EXPECTED_CAPTURE_BUNDLE_SHA256
        or active.candidate_set_sha256 != EXPECTED_CANDIDATE_SET_SHA256
        or active.operator_decision_sha256
        != decisions.decisions[0].decision_sha256
        or active.severity_extraction.extraction.paths != ("severity.text",)
    ):
        raise ValueError("Product v0.2.2.2 active profile differs")
    selected = OpenSearchNormalizationProfileV0222.build(
        **active.model_dump(
            mode="python",
            exclude={"schema_version", "profile_status", "profile_sha256"},
        ),
        profile_status=OpenSearchProfileStatusV0222.OPERATOR_SELECTED,
    )
    if (
        selected.profile_sha256 != holdout.selected_profile_sha256
        or selected.profile_sha256 != offline.normalization_profile_sha256
        or selected.profile_sha256 != fixture.normalization_profile_sha256
        or holdout.selected_profile_file_sha256_before
        != holdout.selected_profile_file_sha256_after
        or not holdout.profile_bytes_unchanged
    ):
        raise ValueError("Product v0.2.2.2 selected profile immutability differs")

    response_hits = fixture.response.get("hits")
    hits = response_hits.get("hits") if isinstance(response_hits, dict) else None
    if not isinstance(hits, list) or len(hits) != 5:
        raise ValueError("Product v0.2.2.2 sanitized fixture hit count differs")
    for hit in hits:
        if not isinstance(hit, dict) or not isinstance(hit.get("_source"), dict):
            raise ValueError("Product v0.2.2.2 sanitized fixture source differs")
        source = hit["_source"]
        resource = source.get("resource")
        severity = source.get("severity")
        if (
            not isinstance(resource, dict)
            or "service.name" not in resource
            or "service" in resource
            or not isinstance(severity, dict)
            or not isinstance(severity.get("text"), str)
            or not isinstance(severity.get("number"), int)
        ):
            raise ValueError("Product v0.2.2.2 sanitized dotted-key shape differs")
    if any(
        "private" in value.lower()
        for value in _string_values(fixture.model_dump(mode="json"))
    ):
        raise ValueError("Product v0.2.2.2 sanitized fixture retained private values")

    if (
        offline.terminal != OFFLINE_PROFILE_PASS_V0222
        or offline.offline_changed_iteration_count != 2
        or offline.sampled_record_count != 5
        or offline.accepted_checkout_record_count != 5
        or offline.rejected_record_count != 0
        or offline.outer_schema_failure_code is not None
        or any(
            value != 0
            for value in (
                offline.timestamp_parse_failures,
                offline.service_alias_failures,
                offline.message_extraction_failures,
                offline.observer_projection_failures,
            )
        )
    ):
        raise ValueError("Product v0.2.2.2 offline profile terminal differs")
    if (
        holdout.terminal != HOLDOUT_VERIFICATION_PASS_V0222
        or holdout.holdout_verification_session_count != 1
        or holdout.read_only_request_count != 3
        or holdout.transport_retry_count != 0
        or holdout.service_aggregation_observed_aliases != ("checkout",)
        or holdout.timestamp_range_query_status != "PASS"
        or holdout.targeted_checkout_query_status != "PASS"
        or holdout.accepted_checkout_record_count != 5
        or holdout.rejected_record_count != 0
        or holdout.outer_schema_failure_count != 0
        or not holdout.baseline_unchanged
        or holdout.cleanup != "CLEAN"
        or any(
            value != 0
            for value in (
                holdout.timestamp_parse_failures,
                holdout.service_alias_ambiguity_count,
                holdout.service_alias_failures,
                holdout.message_extraction_failures,
                holdout.observer_projection_failures,
            )
        )
    ):
        raise ValueError("Product v0.2.2.2 holdout terminal differs")
    zero_keys = (
        "fault_attempt_count",
        "baseline_readiness_attempt_count",
        "product_diagnosis_attempt_count",
        "knowledge_loop_campaign_count",
        "agent_writes",
        "runbook_executions",
    )
    if (
        int(progress.get("increment", -1)) < 4
        or progress.get("initial_consumed_session_terminal")
        != EXPECTED_INITIAL_BLOCKER
        or progress.get("capture_session_count") != 1
        or progress.get("operator_selection_count") != 1
        or progress.get("holdout_verification_session_count") != 1
        or progress.get("holdout_read_only_request_count") != 3
        or progress.get("offline_changed_iteration_count") != 2
        or progress.get("normalization_profile_status") != "ACTIVE"
        or progress.get("normalization_profile_sha256") != active.profile_sha256
        or progress.get("selected_profile_sha256") != selected.profile_sha256
        or progress.get("holdout_verification_sha256")
        != holdout.verification_sha256
        or progress.get("cleanup") != "CLEAN"
        or progress.get("action_authority") != "NONE"
        or any(progress.get(key) != 0 for key in zero_keys)
    ):
        raise ValueError("Product v0.2.2.2 Increment 4 progress differs")
    return {
        "status": holdout.terminal,
        "selected_candidate_alias": active.selected_candidate_alias,
        "operator_selection_count": 1,
        "offline_changed_iteration_count": offline.offline_changed_iteration_count,
        "holdout_verification_session_count": (
            holdout.holdout_verification_session_count
        ),
        "holdout_read_only_request_count": holdout.read_only_request_count,
        "holdout_transport_retry_count": holdout.transport_retry_count,
        "accepted_checkout_record_count": (
            holdout.accepted_checkout_record_count
        ),
        "selected_profile_sha256": selected.profile_sha256,
        "active_profile_sha256": active.profile_sha256,
        "holdout_verification_sha256": holdout.verification_sha256,
        "normalization_profile_status": active.profile_status.value,
        "cleanup": holdout.cleanup,
    }


def main() -> int:
    print(
        json.dumps(
            verify_product_v0222_increment4(Path.cwd()),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_product_v0222_increment4",)
