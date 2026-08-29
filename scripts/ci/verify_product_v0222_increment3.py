#!/usr/bin/env python3
"""Verify the Product v0.2.2.2 capture and Candidate Set checkpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_candidates_v0222 import (
    OpenSearchProfileCandidateSetV0222,
    OpenSearchProfileRecommendationStatusV0222,
)
from ecomsre.product.connectors.opensearch_capture_v0222 import (
    OpenSearchCaptureStoreV0222,
)
from ecomsre.product.pilot.live_capture_v0222 import (
    CANDIDATE_READY_V0222,
    CAPTURE_PASS_V0222,
    CAPTURE_PROTOCOL_BLOCKED_V0222,
    OPERATOR_BLOCKED_V0222,
    load_capture_profile_v0222,
)
from scripts.ci.verify_product_v0222_increment2 import (
    verify_product_v0222_increment2,
)


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.2.2 checkpoint artifact is not an object")
    return payload


def _verify_digest(payload: Mapping[str, Any], field: str) -> None:
    expected = semantic_sha256_v22(
        {key: value for key, value in payload.items() if key != field}
    )
    if payload.get(field) != expected:
        raise ValueError(f"Product v0.2.2.2 {field} differs")


def verify_product_v0222_increment3(project_root: Path) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    increment2 = verify_product_v0222_increment2(root)
    profile = load_capture_profile_v0222(
        root / "config/product-v0222/opensearch/profile.json"
    )
    candidate_set = OpenSearchProfileCandidateSetV0222.model_validate_json(
        (root / "config/product-v0222/opensearch/candidate-set.json").read_text(
            encoding="utf-8"
        )
    )
    tracked_copy = OpenSearchProfileCandidateSetV0222.model_validate_json(
        (root / "docs/analysis/product-v0222-candidate-set.json").read_text(
            encoding="utf-8"
        )
    )
    if tracked_copy != candidate_set:
        raise ValueError("Product v0.2.2.2 Candidate Set copies differ")
    if (
        candidate_set.recommendation_status
        is not OpenSearchProfileRecommendationStatusV0222.OPERATOR_SELECTION_REQUIRED
        or candidate_set.recommended_candidate_alias is not None
        or not 2 <= len(candidate_set.candidates) <= 12
        or candidate_set.score_margin != 0
        or any(
            not candidate.static_compatibility
            or candidate.rejection_codes
            or candidate.sample_parse_report.accepted_records
            != candidate.sample_parse_report.total_records
            or candidate.empirical_query_report.checkout_query_verification != "PASS"
            for candidate in candidate_set.candidates
        )
    ):
        raise ValueError("Product v0.2.2.2 Candidate Set acceptance differs")
    session = _load_object(
        root / "config/product-v0222/opensearch/capture-session.json"
    )
    summary = _load_object(root / "docs/analysis/product-v0222-capture-summary.json")
    progress = _load_object(root / "docs/analysis/product-v0222-progress.json")
    _verify_digest(session, "session_sha256")
    _verify_digest(summary, "summary_sha256")
    _verify_digest(progress, "progress_sha256")
    frozen_required = {
        "terminal": OPERATOR_BLOCKED_V0222,
        "capture_terminal": CAPTURE_PASS_V0222,
        "candidate_set_terminal": CANDIDATE_READY_V0222,
        "initial_consumed_session_terminal": CAPTURE_PROTOCOL_BLOCKED_V0222,
        "capture_bundle_sha256": candidate_set.capture_bundle_sha256,
        "candidate_set_sha256": candidate_set.candidate_set_sha256,
        "candidate_count": len(candidate_set.candidates),
        "cleanup": "CLEAN",
        "action_authority": "NONE",
    }
    for payload in (session, summary):
        if any(payload.get(key) != value for key, value in frozen_required.items()):
            raise ValueError("Product v0.2.2.2 checkpoint truth surfaces differ")
    progressing_required = {
        key: value
        for key, value in frozen_required.items()
        if key != "terminal"
    }
    if any(progress.get(key) != value for key, value in progressing_required.items()):
        raise ValueError("Product v0.2.2.2 progressing truth surface differs")
    zero_fields = (
        "fault_attempt_count",
        "baseline_readiness_attempt_count",
        "product_diagnosis_attempt_count",
        "knowledge_loop_campaign_count",
        "agent_writes",
        "runbook_executions",
    )
    if any(
        payload.get(field) != 0
        for payload in (summary, progress)
        for field in zero_fields
    ):
        raise ValueError("Product v0.2.2.2 forbidden activity count differs")
    if (
        progress.get("capture_session_count") != 1
        or progress.get("capture_read_only_request_count") != 7
        or progress.get("capture_changed_request_plan_count") != 1
        or progress.get("transport_retry_count") != 0
        or not 1 <= int(progress.get("offline_changed_iteration_count", -1)) <= 3
        or not 0 <= int(progress.get("operator_selection_count", -1)) <= 2
        or not 0 <= int(progress.get("holdout_verification_session_count", -1)) <= 2
    ):
        raise ValueError("Product v0.2.2.2 Increment 3 bounds differ")
    brief = (
        root / "docs/human-briefs/product-v0222-opensearch-profile-selection.md"
    ).read_text(encoding="utf-8")
    if (
        candidate_set.candidate_set_sha256 not in brief
        or "Machine recommendation: `NONE`" not in brief
        or any(
            candidate.candidate_alias not in brief
            for candidate in candidate_set.candidates
        )
    ):
        raise ValueError("Product v0.2.2.2 operator brief differs")

    private_validation = "TRACKED_HASH_ONLY"
    private_root = root / profile.private_root
    if private_root.exists():
        start = _load_object(private_root / "capture-session-start.json")
        completion = _load_object(private_root / "capture-session-complete.json")
        replay = _load_object(private_root / "offline-analysis-iteration-1.json")
        for payload in (start, completion, replay):
            _verify_digest(payload, "report_sha256")
        store = OpenSearchCaptureStoreV0222(
            private_root=private_root,
            session_id=profile.session_id,
            maximum_response_bytes=profile.maximum_response_bytes,
        )
        bundle = OpenSearchCaptureStoreV0222.load_bundle(private_root=private_root)
        if (
            start.get("capture_session_count") != 1
            or completion.get("terminal") != CAPTURE_PROTOCOL_BLOCKED_V0222
            or completion.get("report_sha256")
            != progress.get("initial_consumed_session_completion_sha256")
            or replay.get("source_completion_sha256") != completion.get("report_sha256")
            or replay.get("capture_bundle_sha256") != bundle.bundle_sha256
            or replay.get("candidate_set_sha256") != candidate_set.candidate_set_sha256
            or replay.get("iteration_ordinal") != 1
            or replay.get("additional_live_request_count") != 0
            or not bundle.capture_completeness
            or len(bundle.requests) != 7
            or store.verify_content_addressed_objects() != len(bundle.responses)
        ):
            raise ValueError("Product v0.2.2.2 private capture binding differs")
        private_validation = "PRIVATE_CAPTURE_VERIFIED"
    return {
        "status": OPERATOR_BLOCKED_V0222,
        "capture_terminal": CAPTURE_PASS_V0222,
        "candidate_set_terminal": CANDIDATE_READY_V0222,
        "increment2_status": increment2["status"],
        "initial_consumed_session_terminal": CAPTURE_PROTOCOL_BLOCKED_V0222,
        "capture_session_count": 1,
        "read_only_request_count": 7,
        "changed_request_plan_count": 1,
        "transport_retry_count": 0,
        "offline_changed_iteration_count": 1,
        "operator_selection_count": 0,
        "holdout_verification_session_count": 0,
        "candidate_count": len(candidate_set.candidates),
        "capture_bundle_sha256": candidate_set.capture_bundle_sha256,
        "candidate_set_sha256": candidate_set.candidate_set_sha256,
        "private_capture_validation": private_validation,
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
            verify_product_v0222_increment3(arguments.project_root),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_product_v0222_increment3",)
