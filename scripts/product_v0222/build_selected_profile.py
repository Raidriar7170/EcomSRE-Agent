"""Build the selected Product v0.2.2.2 profile from frozen capture bytes."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_candidates_v0222 import (
    OpenSearchOperatorDecisionLedgerV0222,
    OpenSearchProfileCandidateSetV0222,
)
from ecomsre.product.connectors.opensearch_capture_v0222 import (
    OpenSearchCaptureRequestKindV0222,
    OpenSearchSchemaCaptureBundleV0222,
)
from ecomsre.product.connectors.opensearch_profile_v0222 import (
    OFFLINE_PROFILE_PASS_V0222,
    assemble_selected_profile_v0222,
    build_sanitized_selected_profile_fixture_v0222,
    evaluate_offline_selected_profile_v0222,
)
from ecomsre.product.pilot.live_capture_v0222 import (
    load_capture_profile_v0222,
)


def _write_public(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.product-v0222.tmp"
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_private_once(path: Path, payload: Mapping[str, object]) -> str:
    body = dict(payload)
    body["report_sha256"] = semantic_sha256_v22(body)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        encoded = (json.dumps(body, indent=2, sort_keys=True) + "\n").encode()
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return str(body["report_sha256"])


def _response_for_kind(
    bundle: OpenSearchSchemaCaptureBundleV0222,
    kind: OpenSearchCaptureRequestKindV0222,
):
    matches = tuple(item for item in bundle.responses if item.request_kind is kind)
    if len(matches) != 1:
        raise ValueError(f"Product v0.2.2.2 capture kind {kind.value} differs")
    response = matches[0]
    if response.http_status != 200:
        raise ValueError(f"Product v0.2.2.2 capture kind {kind.value} failed")
    return response


def build_selected_profile_artifacts(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    profile_config = load_capture_profile_v0222(
        root / "config/product-v0222/opensearch/profile.json"
    )
    private_root = root / profile_config.private_root
    bundle = OpenSearchSchemaCaptureBundleV0222.model_validate_json(
        (private_root / "capture-bundle.json").read_text(encoding="utf-8")
    )
    candidate_set = OpenSearchProfileCandidateSetV0222.model_validate_json(
        (
            root / "config/product-v0222/opensearch/candidate-set.json"
        ).read_text(encoding="utf-8")
    )
    decision_ledger = OpenSearchOperatorDecisionLedgerV0222.model_validate_json(
        (
            root / "config/product-v0222/opensearch/operator-decision.json"
        ).read_text(encoding="utf-8")
    )
    if bundle.bundle_sha256 != candidate_set.capture_bundle_sha256:
        raise ValueError("Product v0.2.2.2 Candidate Set capture binding differs")
    mapping = _response_for_kind(bundle, OpenSearchCaptureRequestKindV0222.MAPPING)
    field_caps = _response_for_kind(
        bundle,
        OpenSearchCaptureRequestKindV0222.FIELD_CAPS,
    )
    sample = _response_for_kind(
        bundle,
        OpenSearchCaptureRequestKindV0222.STRUCTURAL_SAMPLE,
    )
    sample_path = private_root / sample.response_object_ref
    if sample_path.name != sample.response_sha256:
        raise ValueError("Product v0.2.2.2 private sample object binding differs")
    sample_response = json.loads(sample_path.read_text(encoding="utf-8"))
    selected_profile = assemble_selected_profile_v0222(
        candidate_set=candidate_set,
        decision_ledger=decision_ledger,
        index_pattern=profile_config.index_pattern,
        mapping_response_sha256=mapping.response_sha256,
        field_caps_response_sha256=field_caps.response_sha256,
        structural_sample_response_sha256=sample.response_sha256,
        structural_sample_response=sample_response,
    )
    from datetime import UTC, datetime

    fixture = build_sanitized_selected_profile_fixture_v0222(
        live_response=sample_response,
        profile=selected_profile,
        capture_bundle_sha256=bundle.bundle_sha256,
        private_sample_response_sha256=sample.response_sha256,
        started_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        service_aliases={alias: "checkout" for alias in profile_config.checkout_aliases},
    )
    offline = evaluate_offline_selected_profile_v0222(
        fixture=fixture,
        profile=selected_profile,
        offline_changed_iteration_count=2,
    )
    if offline.terminal != OFFLINE_PROFILE_PASS_V0222:
        raise ValueError(OFFLINE_PROFILE_PASS_V0222)
    private_iteration = private_root / "offline-profile-iteration-2.json"
    if private_iteration.exists():
        raise ValueError("Product v0.2.2.2 offline profile iteration is consumed")
    private_iteration_sha256 = _write_private_once(
        private_iteration,
        {
            "schema_version": (
                "ecomsre.product.offline-profile-iteration.v0222"
            ),
            "offline_changed_iteration_count": 2,
            "capture_bundle_sha256": bundle.bundle_sha256,
            "candidate_set_sha256": candidate_set.candidate_set_sha256,
            "operator_decision_sha256": (
                decision_ledger.decisions[-1].decision_sha256
            ),
            "normalization_profile_sha256": selected_profile.profile_sha256,
            "sanitized_fixture_sha256": fixture.fixture_sha256,
            "offline_profile_report_sha256": offline.report_sha256,
            "terminal": offline.terminal,
        },
    )
    _write_public(
        root / "config/product-v0222/opensearch/normalization-profile.json",
        selected_profile.model_dump(mode="json"),
    )
    _write_public(
        root
        / "tests/fixtures/product_v0222/opensearch_selected_profile_shape.json",
        fixture.model_dump(mode="json"),
    )
    _write_public(
        root / "docs/analysis/product-v0222-offline-profile.json",
        offline.model_dump(mode="json"),
    )
    progress_path = root / "docs/analysis/product-v0222-progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    if not isinstance(progress, dict):
        raise ValueError("Product v0.2.2.2 progress is not an object")
    progress.pop("progress_sha256", None)
    progress.update(
        {
            "increment": 4,
            "operator_selection_count": len(decision_ledger.decisions),
            "selected_candidate_alias": selected_profile.selected_candidate_alias,
            "operator_decision_sha256": selected_profile.operator_decision_sha256,
            "normalization_profile_status": selected_profile.profile_status.value,
            "normalization_profile_sha256": selected_profile.profile_sha256,
            "sanitized_fixture_sha256": fixture.fixture_sha256,
            "offline_profile_report_sha256": offline.report_sha256,
            "offline_profile_terminal": offline.terminal,
            "offline_changed_iteration_count": 2,
            "holdout_verification_session_count": 0,
            "terminal": offline.terminal,
            "next_boundary": "RUN_FRESH_HOLDOUT_VERIFICATION",
        }
    )
    progress["progress_sha256"] = semantic_sha256_v22(progress)
    _write_public(progress_path, progress)
    return {
        "terminal": offline.terminal,
        "selected_candidate_alias": selected_profile.selected_candidate_alias,
        "operator_decision_sha256": selected_profile.operator_decision_sha256,
        "normalization_profile_sha256": selected_profile.profile_sha256,
        "sanitized_fixture_sha256": fixture.fixture_sha256,
        "offline_profile_report_sha256": offline.report_sha256,
        "private_iteration_sha256": private_iteration_sha256,
        "offline_changed_iteration_count": 2,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            build_selected_profile_artifacts(args.project_root),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("build_selected_profile_artifacts", "main")
