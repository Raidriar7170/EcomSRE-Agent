#!/usr/bin/env python3
"""Verify the immutable Product v0.2.3.2 Attempt 1 blocker closure."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, ValidationError, model_validator

from ecomsre.dta_v2.tool_contracts import (
    build_inspect_service_runtime_request,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    load_checkout_traffic_contract_v0232,
)
from ecomsre.product.pilot.traffic_preflight_v0232 import (
    TrafficProductCleanupV0232,
    load_traffic_campaign_v0232,
)


BLOCKER_V0232 = "BLOCKED_ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT"
ATTEMPT_SHA256_V0232 = (
    "5080a440ca6a96cb8b93f104f873ace85ddef01cdc508d6a5656e4219a0221f9"
)
PROGRESS_SHA256_V0232 = (
    "1b36274cddb457186ddd02d86ccdd285f52aa1ca992cad4a0e72d58ae772cdfe"
)
RUNNER_SOURCE_SHA256_V0232 = (
    "aa2ed5e22bfc85889e03a6714813394e19753f3f41be42be7ca562440db17ff7"
)
TOOL_CONTRACT_SOURCE_SHA256_V0232 = (
    "105f6dee91b4ed4506a94cba3a4f1a7594a87cc0807ec86146d60213e8a651e1"
)
RUN_ID_CONTRACT_SOURCE_SHA256_V0232 = (
    "de2c98f72375a350fefa60879f3b91397cae644e90d0753c1a8ef96cb732c281"
)
FAILED_RUN_ID_V0232 = "product-v0232-traffic-preflight-1"
RUN_ID_PATTERN_V0232 = r"^[0-9a-f]{32}$"


class TrafficPreflightBlockerAddendumV0232(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.traffic-preflight-blocker-addendum.v0232"
    ]
    terminal: Literal["BLOCKED_ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT"]
    attempt_ordinal: Literal[1]
    attempt_consumed: Literal[True]
    attempt_sha256: Literal[
        "5080a440ca6a96cb8b93f104f873ace85ddef01cdc508d6a5656e4219a0221f9"
    ]
    progress_sha256: Literal[
        "1b36274cddb457186ddd02d86ccdd285f52aa1ca992cad4a0e72d58ae772cdfe"
    ]
    failure_stage: Literal["RUNTIME_INSPECT_REQUEST_BUILD"]
    safe_error_code: Literal["RUN_ID_SCHEMA_PATTERN_MISMATCH"]
    safe_error_type: Literal["ValidationError"]
    validation_error_type: Literal["string_pattern_mismatch"]
    request_model: Literal["InspectServiceRuntimeRequest"]
    request_field: Literal["run_id"]
    observed_run_id: Literal["product-v0232-traffic-preflight-1"]
    required_pattern: Literal["^[0-9a-f]{32}$"]
    traffic_started: Literal[False]
    completed_transactions: Literal[0]
    http_status: None
    response_content_type: None
    response_shape_summary: None
    not_applicable_reason: Literal["NO_HTTP_REQUEST_SENT"]
    queue_baseline_observation_complete: Literal[False]
    demo_cleanup: Literal["BLOCKED_BASELINE_UNCHANGED_UNPROVEN"]
    product_cleanup: Literal["CLEAN"]
    attempt_2_authorized: Literal[False]
    attempt_2_authorization_reason: Literal[
        "RUN_ID_REPAIR_OUTSIDE_ALLOWED_PARAMETER_SET"
    ]
    formal_healthy_traffic_execution_count: Literal[0]
    runner_source_sha256: Literal[
        "aa2ed5e22bfc85889e03a6714813394e19753f3f41be42be7ca562440db17ff7"
    ]
    tool_contract_source_sha256: Literal[
        "105f6dee91b4ed4506a94cba3a4f1a7594a87cc0807ec86146d60213e8a651e1"
    ]
    run_id_contract_source_sha256: Literal[
        "de2c98f72375a350fefa60879f3b91397cae644e90d0753c1a8ef96cb732c281"
    ]
    action_authority: Literal["NONE"]
    addendum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_self_seal(self) -> "TrafficPreflightBlockerAddendumV0232":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"addendum_sha256"})
        )
        if self.addendum_sha256 != expected:
            raise ValueError("Product v0.2.3.2 blocker addendum digest differs")
        return self


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Product v0.2.3.2 JSON object is invalid: {path.name}")
    return payload


def _require_seal(payload: Mapping[str, Any], field: str) -> None:
    body = dict(payload)
    supplied = body.pop(field, None)
    if supplied != semantic_sha256_v22(body):
        raise ValueError(f"Product v0.2.3.2 {field} differs")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contains_private_locator(value: object) -> bool:
    if isinstance(value, str):
        return value.startswith(("/Users/", "/home/", "file:"))
    if isinstance(value, Mapping):
        return any(_contains_private_locator(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_private_locator(item) for item in value)
    return False


def _verify_offline_reproduction() -> None:
    try:
        build_inspect_service_runtime_request(
            run_id=FAILED_RUN_ID_V0232,
            services=("checkout",),
            max_results=1,
        )
    except ValidationError as error:
        errors = error.errors(include_url=False, include_input=False)
    else:
        raise ValueError("Product v0.2.3.2 blocker no longer reproduces")
    expected = [
        {
            "type": "string_pattern_mismatch",
            "loc": ("run_id",),
            "msg": f"String should match pattern '{RUN_ID_PATTERN_V0232}'",
            "ctx": {"pattern": RUN_ID_PATTERN_V0232},
        }
    ]
    if errors != expected:
        raise ValueError("Product v0.2.3.2 blocker reproduction differs")


def verify_product_v0232_blocker(
    root: Path,
    *,
    addendum_path: Path | None = None,
) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    attempt_payload = _load_object(
        project / "docs/analysis/product-v0232-traffic-preflight-attempt-1.json"
    )
    progress_payload = _load_object(
        project / "docs/analysis/product-v0232-progress.json"
    )
    addendum_payload = _load_object(
        addendum_path
        or project
        / "docs/analysis/product-v0232-traffic-preflight-attempt-1-blocker-addendum.json"
    )
    _require_seal(attempt_payload, "attempt_sha256")
    _require_seal(progress_payload, "progress_sha256")
    addendum = TrafficPreflightBlockerAddendumV0232.model_validate(
        addendum_payload
    )
    campaign = load_traffic_campaign_v0232(project)
    contract = load_checkout_traffic_contract_v0232(project)
    product_cleanup = TrafficProductCleanupV0232.model_validate(
        attempt_payload.get("product_cleanup")
    )
    expected_progress = {
        "terminal": BLOCKER_V0232,
        "increment": 4,
        "offline_changed_iteration_count": 3,
        "live_traffic_preflight_attempt_count": 1,
        "formal_healthy_traffic_execution_count": 0,
        "accepted_successor_incident_count": 0,
        "successor_diagnosis_count": 0,
        "fault_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "provider_calls": 0,
        "action_authority": "NONE",
        "traffic_preflight_attempt_sha256": ATTEMPT_SHA256_V0232,
    }
    exact_attempt = (
        attempt_payload.get("terminal") == BLOCKER_V0232
        and attempt_payload.get("attempt_ordinal") == 1
        and attempt_payload.get("attempt_consumed") is True
        and attempt_payload.get("safe_error_type") == "ValidationError"
        and attempt_payload.get("stage") == "BEFORE_TRAFFIC"
        and attempt_payload.get("attempt_sha256") == ATTEMPT_SHA256_V0232
        and attempt_payload.get("profile_sha256")
        == campaign.preflight_profile_sha256
        and attempt_payload.get("contract_sha256") == contract.contract_sha256
        and attempt_payload.get("source_file_bindings")
        == [item.model_dump(mode="json") for item in contract.source_file_bindings]
        and attempt_payload.get("runtime_continuity_descriptor_sha256")
        == campaign.runtime_continuity_descriptor_sha256
        and attempt_payload.get("resolved_compose_sha256")
        == campaign.resolved_compose_sha256
        and attempt_payload.get("source_state_before_sha256")
        == campaign.source_state_sha256
        and attempt_payload.get("source_state_after_sha256")
        == campaign.source_state_sha256
        and attempt_payload.get("product_state_clone_sha256")
        == campaign.product_state_clone_sha256
        and attempt_payload.get("product_state_before_sha256")
        == campaign.product_state_sha256
        and attempt_payload.get("product_state_after_sha256")
        == campaign.product_state_sha256
        and attempt_payload.get("demo_cleanup") == {"verdict": "BLOCKED"}
        and product_cleanup.verdict == "CLEAN"
        and attempt_payload.get("action_authority") == "NONE"
    )
    if (
        not exact_attempt
        or progress_payload.get("progress_sha256") != PROGRESS_SHA256_V0232
        or any(progress_payload.get(key) != value for key, value in expected_progress.items())
        or addendum.attempt_sha256 != attempt_payload["attempt_sha256"]
        or addendum.progress_sha256 != progress_payload["progress_sha256"]
        or _sha256_file(project / "scripts/product_v0232/run_traffic_preflight.py")
        != RUNNER_SOURCE_SHA256_V0232
        or _sha256_file(project / "src/ecomsre/dta_v2/tool_contracts.py")
        != TOOL_CONTRACT_SOURCE_SHA256_V0232
        or _sha256_file(project / "src/ecomsre/dta_v2/contracts.py")
        != RUN_ID_CONTRACT_SOURCE_SHA256_V0232
        or (project / "docs/analysis/product-v0232-traffic-preflight-attempt-2.json").exists()
        or (project / "docs/analysis/product-v0232-traffic-preflight.json").exists()
        or _contains_private_locator(attempt_payload)
        or _contains_private_locator(progress_payload)
        or _contains_private_locator(addendum_payload)
    ):
        raise ValueError("Product v0.2.3.2 blocker binding differs")
    _verify_offline_reproduction()
    return {
        "terminal": BLOCKER_V0232,
        "attempt_ordinal": 1,
        "attempt_consumed": True,
        "attempt_2_authorized": False,
        "failure_stage": addendum.failure_stage,
        "safe_error_code": addendum.safe_error_code,
        "completed_transactions": 0,
        "formal_healthy_traffic_execution_count": 0,
        "action_authority": "NONE",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    arguments = parser.parse_args(argv)
    print(json.dumps(verify_product_v0232_blocker(arguments.root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
