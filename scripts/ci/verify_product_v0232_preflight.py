#!/usr/bin/env python3
"""Verify the Product v0.2.3.2 live traffic-preflight checkpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ecomsre.product.pilot.healthy_traffic_v0232 import (
    load_checkout_traffic_contract_v0232,
)
from ecomsre.product.pilot.product_state_clone_v0232 import (
    ProductStateCloneV0232,
    ProductStateSourceV0232,
)
from ecomsre.product.pilot.runtime_continuity_v0231 import (
    FlagdBindDescriptorV0231,
    RuntimeAuthorityContinuityDescriptorV0231,
)
from ecomsre.product.pilot.traffic_preflight_v0232 import (
    TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0232,
    TRAFFIC_PREFLIGHT_PASS_V0232,
    TrafficPreflightAttemptV0232,
    TrafficPreflightEvidenceV0232,
    load_traffic_campaign_v0232,
    load_traffic_profile_v0232,
)
from scripts.ci.verify_product_v0232_history import (
    verify_product_v0232_written_reports,
)


_FROZEN_PRODUCT_STATE_SHA256_V0232 = (
    "076b41e929c8700f1663ea2e3063197cbaa35898e0942daa7dccc6ceb7bc1129"
)


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Product v0.2.3.2 JSON object is invalid: {path.name}")
    return payload


def _contains_private_locator(value: object) -> bool:
    if isinstance(value, str):
        return value.startswith(("/Users/", "/home/", "file:"))
    if isinstance(value, Mapping):
        return any(_contains_private_locator(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_private_locator(item) for item in value)
    return False


def _frozen_bindings(root: Path) -> dict[str, object]:
    flagd = FlagdBindDescriptorV0231.model_validate(
        _load_object(root / "docs/analysis/product-v0231-flagd-bind-descriptor.json")
    )
    runtime = RuntimeAuthorityContinuityDescriptorV0231.model_validate(
        _load_object(
            root / "docs/analysis/product-v0231-runtime-authority-descriptor.json"
        )
    )
    audit = _load_object(root / "docs/analysis/product-v0232-predecessor-audit.json")
    source = ProductStateSourceV0232.model_validate(audit.get("source_state"))
    clone = ProductStateCloneV0232.model_validate(
        _load_object(root / "docs/analysis/product-v0232-product-state-clone.json")
    )
    if (
        runtime.flagd_bind_descriptor_sha256 != flagd.descriptor_sha256
        or runtime.resolved_compose_sha256 != flagd.resolved_compose_sha256
        or flagd.flag_file_bytes_sha256 != flagd.baseline_document_sha256
        or clone.clone_sha256 != audit.get("clone_sha256")
        or clone.source_locator != source.source_locator
        or clone.source_database_file_sha256_before
        != source.source_database_file_sha256
        or clone.source_database_file_sha256_after
        != source.source_database_file_sha256
        or clone.source_database_logical_sha256
        != source.source_database_logical_sha256
        or clone.source_object_inventory_sha256
        != source.source_object_inventory_sha256
        or clone.source_runtime_file_inventory_sha256
        != source.source_runtime_file_inventory_sha256
        or clone.source_counts != source.source_counts
        or clone.source_environment_id != source.source_environment_id
        or clone.source_active_baseline_id != source.source_active_baseline_id
        or clone.source_active_baseline_sha256
        != source.source_active_baseline_sha256
        or clone.source_profile_sha256 != source.source_profile_sha256
    ):
        raise ValueError("Product v0.2.3.2 frozen predecessor binding differs")
    return {
        "source_state_sha256": source.source_sha256,
        "product_state_clone_sha256": clone.clone_sha256,
        "product_state_sha256": _FROZEN_PRODUCT_STATE_SHA256_V0232,
        "flagd_bind_descriptor_sha256": flagd.descriptor_sha256,
        "runtime_continuity_descriptor_sha256": runtime.descriptor_sha256,
        "resolved_compose_sha256": runtime.resolved_compose_sha256,
        "read_authority_sha256": runtime.read_authority_sha256,
        "pilot_runtime_authority_sha256": runtime.pilot_runtime_authority_sha256,
        "queue_default_bytes_sha256": flagd.flag_file_bytes_sha256,
        "outer_baseline_document_sha256": flagd.baseline_document_sha256,
        "incident_count": clone.destination_counts.incident_count,
        "diagnosis_count": clone.destination_counts.diagnosis_count,
    }


def verify_product_v0232_preflight(
    root: Path,
    *,
    attempt_path: Path | None = None,
    preflight_path: Path | None = None,
    progress_path: Path | None = None,
    require_review: bool = False,
) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    campaign = load_traffic_campaign_v0232(project)
    frozen = _frozen_bindings(project)
    preflight_profile = load_traffic_profile_v0232(project, role="PREFLIGHT")
    formal_profile = load_traffic_profile_v0232(project, role="FORMAL")
    contract = load_checkout_traffic_contract_v0232(project)
    attempt_file = attempt_path or (
        project / "docs/analysis/product-v0232-traffic-preflight-attempt-1.json"
    )
    attempt_payload = _load_object(attempt_file)
    attempt = TrafficPreflightAttemptV0232.model_validate(attempt_payload)
    preflight_file = preflight_path or (
        project / "docs/analysis/product-v0232-traffic-preflight.json"
    )
    preflight_payload = _load_object(preflight_file)
    preflight = TrafficPreflightEvidenceV0232.model_validate(preflight_payload)
    rebuilt = TrafficPreflightEvidenceV0232.build(
        attempt=attempt,
        formal_profile=formal_profile,
        campaign=campaign,
    )
    expected_campaign_bindings = {
        key: value
        for key, value in frozen.items()
        if key not in {"incident_count", "diagnosis_count"}
    }
    observed_campaign_bindings = {
        key: getattr(campaign, key) for key in expected_campaign_bindings
    }
    exact_attempt_bindings = (
        attempt.flagd_bind_descriptor_sha256
        == campaign.flagd_bind_descriptor_sha256
        and attempt.runtime_continuity_descriptor_sha256
        == campaign.runtime_continuity_descriptor_sha256
        and attempt.resolved_compose_sha256 == campaign.resolved_compose_sha256
        and attempt.read_authority_sha256 == campaign.read_authority_sha256
        and attempt.pilot_runtime_authority_sha256
        == campaign.pilot_runtime_authority_sha256
        and attempt.queue_before_sha256 == campaign.queue_default_bytes_sha256
        and attempt.queue_after_sha256 == campaign.queue_default_bytes_sha256
        and attempt.outer_baseline_before_sha256
        == campaign.outer_baseline_document_sha256
        and attempt.outer_baseline_after_sha256
        == campaign.outer_baseline_document_sha256
        and attempt.source_state_before_sha256 == campaign.source_state_sha256
        and attempt.source_state_after_sha256 == campaign.source_state_sha256
        and attempt.product_state_clone_sha256
        == campaign.product_state_clone_sha256
        and attempt.product_state_before_sha256 == campaign.product_state_sha256
        and attempt.product_state_after_sha256 == campaign.product_state_sha256
        and attempt.incident_count_before == frozen["incident_count"]
        and attempt.incident_count_after == frozen["incident_count"]
        and attempt.diagnosis_count_before == frozen["diagnosis_count"]
        and attempt.diagnosis_count_after == frozen["diagnosis_count"]
    )
    if (
        observed_campaign_bindings != expected_campaign_bindings
        or not exact_attempt_bindings
        or attempt.terminal != TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0232
        or attempt.attempt_ordinal != 1
        or attempt.profile_sha256 != preflight_profile.profile_sha256
        or attempt.contract_sha256 != contract.contract_sha256
        or attempt.source_file_bindings != contract.source_file_bindings
        or preflight != rebuilt
        or preflight.terminal != TRAFFIC_PREFLIGHT_PASS_V0232
        or (project / "docs/analysis/product-v0232-traffic-preflight-attempt-2.json").exists()
        or _contains_private_locator(attempt_payload)
        or _contains_private_locator(preflight_payload)
    ):
        raise ValueError("Product v0.2.3.2 traffic preflight binding differs")
    expected_progress_bindings = {
        "traffic_contract_sha256": contract.contract_sha256,
        "traffic_contract_report_sha256": (
            "94da03d8b19fac0928e74a95ae3abb70ceb7fe6575fe12109d8e2a8dfd024213"
        ),
        "evidence_binding_preflight_sha256": (
            "13b0f45f6943152ed2d13259d35955bcffe1432023ef941269be7d88e47d9160"
        ),
        "reference_evidence_index_sha256": (
            "54d740fbf929ba960491cc0480911802c3178f3c802eaa87c76664e489f0bfbe"
        ),
        "traffic_preflight_attempt_sha256": attempt.attempt_sha256,
        "traffic_preflight_sha256": preflight.preflight_sha256,
        "formal_profile_sha256": formal_profile.profile_sha256,
        "campaign_sha256": campaign.campaign_sha256,
    }
    verify_product_v0232_written_reports(
        project,
        progress_path=progress_path,
        expected_progress_terminal=TRAFFIC_PREFLIGHT_PASS_V0232,
        expected_progress_increment=4,
        expected_offline_changed_iteration_count=3,
        expected_progress_bindings=expected_progress_bindings,
        expected_live_traffic_preflight_attempt_count=1,
    )
    if require_review:
        review = (
            project
            / "docs/external-reviews/product-v0232-pre-execution-review.md"
        ).read_text(encoding="utf-8")
        review_lines = tuple(line.strip() for line in review.splitlines())
        required_fields = {
            "Review verdict:": "Review verdict: `PASS`",
            "Must Fix:": "Must Fix: `0`",
            "Should Fix:": "Should Fix: `0`",
            "Traffic preflight SHA-256:": (
                f"Traffic preflight SHA-256: `{preflight.preflight_sha256}`"
            ),
            "Formal profile SHA-256:": (
                f"Formal profile SHA-256: `{formal_profile.profile_sha256}`"
            ),
            "Formal execution authorized by review:": (
                "Formal execution authorized by review: `true`"
            ),
        }
        for prefix, expected_line in required_fields.items():
            matching_lines = tuple(
                line for line in review_lines if line.startswith(prefix)
            )
            if matching_lines != (expected_line,):
                raise ValueError("Product v0.2.3.2 pre-execution review differs")
    return {
        "terminal": TRAFFIC_PREFLIGHT_PASS_V0232,
        "live_traffic_preflight_attempt_count": 1,
        "transaction_count": 10,
        "successful_transaction_count": 10,
        "formal_profile_sha256": formal_profile.profile_sha256,
        "traffic_preflight_sha256": preflight.preflight_sha256,
        "action_authority": "NONE",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--require-review", action="store_true")
    arguments = parser.parse_args(argv)
    print(
        json.dumps(
            verify_product_v0232_preflight(
                arguments.root,
                require_review=arguments.require_review,
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
