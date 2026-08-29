#!/usr/bin/env python3
"""Run the Product v0.2.2.2 bounded Candidate Set checkpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from ecomsre.product.connectors.opensearch_candidates_v0222 import (
    OpenSearchProfileComponentKindV0222,
    OpenSearchProfileRecommendationStatusV0222,
    build_component_candidate_v0222,
    build_profile_candidate_set_v0222,
    render_operator_brief_v0222,
)
from scripts.ci.verify_product_v0222_increment1 import (
    _verify_truth_surface,
    verify_product_v0222_increment1,
)


TERMINAL = "ECOMSRE_PRODUCT_V0222_CANDIDATE_SET_ENGINE_READY"
CAPTURE_SHA = "a" * 64
CAPTURE_REF = f"objects/sha256/aa/{'b' * 64}"


def _component(
    alias: str,
    kind: OpenSearchProfileComponentKindV0222,
    accessor: str,
    *,
    checkout_match_count: int = 0,
    query_status: str = "NOT_APPLICABLE",
    optional: bool = False,
):
    return build_component_candidate_v0222(
        component_alias=alias,
        kind=kind,
        accessor=accessor,
        encoding_or_mode="OPTIONAL" if optional else "STRING",
        mapping_types=() if optional else ("keyword",),
        field_caps_types=() if optional else ("keyword",),
        sample_presence_count=0 if optional else 5,
        sample_parse_success_count=0 if optional else 5,
        checkout_match_count=checkout_match_count,
        query_verification_status=query_status,
        supporting_capture_refs=(CAPTURE_REF,),
        contradicting_capture_refs=(),
    )


def _synthetic_ambiguous_components():
    kind = OpenSearchProfileComponentKindV0222
    return (
        _component("ts-observed", kind.TIMESTAMP, "observedTimestamp"),
        _component("ts-at", kind.TIMESTAMP, "@timestamp"),
        _component(
            "source-resource",
            kind.SERVICE_SOURCE,
            "resource.service.name",
            checkout_match_count=5,
        ),
        _component(
            "source-flat",
            kind.SERVICE_SOURCE,
            "service.name",
            checkout_match_count=5,
        ),
        _component(
            "query-resource",
            kind.SERVICE_QUERY,
            "resource.service.name.keyword",
            query_status="PASS",
        ),
        _component(
            "query-flat",
            kind.SERVICE_QUERY,
            "service.name.keyword",
            query_status="PASS",
        ),
        _component("message-body", kind.MESSAGE, "body"),
        _component(
            "severity-optional",
            kind.SEVERITY,
            "__OPTIONAL__",
            optional=True,
        ),
        _component(
            "trace-optional",
            kind.TRACE_ID,
            "__OPTIONAL__",
            optional=True,
        ),
    )


def verify_product_v0222_increment2(project_root: Path) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    increment1 = verify_product_v0222_increment1(root)
    progress = _verify_truth_surface(
        root / "docs/analysis/product-v0222-progress.json",
        digest_field="progress_sha256",
        status_field="terminal",
        expected_status=None,
    )
    progress_increment = progress.get("increment")
    if not isinstance(progress_increment, int) or progress_increment < 2:
        raise ValueError("Product v0.2.2.2 progress has not reached Increment 2")
    candidate_set = build_profile_candidate_set_v0222(
        capture_bundle_sha256=CAPTURE_SHA,
        components=_synthetic_ambiguous_components(),
    )
    if (
        not 2 <= len(candidate_set.candidates) <= 12
        or candidate_set.recommendation_status
        is not OpenSearchProfileRecommendationStatusV0222.OPERATOR_SELECTION_REQUIRED
        or candidate_set.recommended_candidate_alias is not None
    ):
        raise ValueError("Product v0.2.2.2 synthetic candidate set differs")
    if any(
        not candidate.supporting_capture_refs
        or not candidate.static_compatibility
        or not candidate.profile_fields["service_query"].startswith(
            candidate.profile_fields["service_source"]
        )
        for candidate in candidate_set.candidates
    ):
        raise ValueError("Product v0.2.2.2 candidate evidence binding differs")
    brief = render_operator_brief_v0222(
        candidate_set=candidate_set,
        capture_session_id="product-v0222-synthetic-capture",
    )
    if any(candidate.candidate_alias not in brief for candidate in candidate_set.candidates):
        raise ValueError("Product v0.2.2.2 operator brief is incomplete")
    return {
        "status": TERMINAL,
        "increment1_status": increment1["status"],
        "synthetic_candidate_count": len(candidate_set.candidates),
        "candidate_count_bound": 12,
        "beam_width": 24,
        "component_count_per_kind_bound": 8,
        "recommendation_status": candidate_set.recommendation_status.value,
        "operator_decision_status": "NOT_EXECUTED_SYNTHETIC_CHECKPOINT",
        "live_capture_session_count": 0,
        "operator_selection_count": 0,
        "fault_attempt_count": 0,
        "baseline_readiness_attempt_count": 0,
        "product_diagnosis_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority": "NONE",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    arguments = parser.parse_args(argv)
    print(
        json.dumps(
            verify_product_v0222_increment2(arguments.project_root),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("TERMINAL", "verify_product_v0222_increment2")
