from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.tool_contracts import (
    ToolName,
    build_inspect_service_runtime_request,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.tool_request_ids_v02321 import (
    CanonicalToolRunIdV02321,
    derive_tool_run_id_v02321,
)
from ecomsre.product.pilot.typed_request_plan_v02321 import (
    build_traffic_harness_typed_request_plan_v02321,
    materialize_planned_request_v02321,
    require_live_request_in_plan_v02321,
)
from scripts.ci.verify_product_v02321_history import (
    HISTORY_AND_REUSE_PASS_V02321,
    verify_product_v02321_history,
)
from scripts.product_v02321.run_harness_contract_preflight import (
    TYPED_REQUEST_PLAN_PASS_V02321,
    build_increment1_artifacts_v02321,
    run_harness_contract_preflight_v02321,
)


ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_SHA256 = "c0c47c293ff038b2d9bbd71b649eba6aed43330ff02a738cdcfa721fa59150d4"
STATE_CLONE_SHA256 = (
    "6920044cea06a68f38624803468aeeb0f854caee695f7f876ff2d6f6ef074205"
)


def _canonical_id(
    *,
    role: str = "PREFLIGHT",
    attempt_ordinal: int = 1,
    tool_name: str = ToolName.INSPECT_SERVICE_RUNTIME.value,
) -> CanonicalToolRunIdV02321:
    return CanonicalToolRunIdV02321.build(
        namespace="ECOMSRE_PRODUCT_V02321",
        role=role,
        campaign_sha256=CAMPAIGN_SHA256,
        state_clone_sha256=STATE_CLONE_SHA256,
        attempt_ordinal=attempt_ordinal,
        tool_name=tool_name,
        target_services=("checkout",),
    )


def test_predecessor_human_readable_run_id_still_reproduces_exact_failure() -> None:
    with pytest.raises(ValidationError) as caught:
        build_inspect_service_runtime_request(
            run_id="product-v0232-traffic-preflight-1",
            services=("checkout",),
            max_results=1,
        )

    assert caught.value.errors(include_url=False, include_input=False) == [
        {
            "type": "string_pattern_mismatch",
            "loc": ("run_id",),
            "msg": "String should match pattern '^[0-9a-f]{32}$'",
            "ctx": {"pattern": "^[0-9a-f]{32}$"},
        }
    ]


def test_canonical_tool_run_id_is_stable_bound_and_semantically_distinct() -> None:
    canonical = _canonical_id()

    assert len(canonical.run_id) == 32
    assert canonical.run_id == canonical.run_id.lower()
    assert set(canonical.run_id) <= set("0123456789abcdef")
    assert canonical.run_id == derive_tool_run_id_v02321(
        namespace=canonical.namespace,
        role=canonical.role,
        campaign_sha256=canonical.campaign_sha256,
        state_clone_sha256=canonical.state_clone_sha256,
        attempt_ordinal=canonical.attempt_ordinal,
        tool_name=canonical.tool_name,
        target_services=canonical.target_services,
    )
    assert _canonical_id().run_id == canonical.run_id
    assert _canonical_id(role="FORMAL").run_id != canonical.run_id
    assert _canonical_id(attempt_ordinal=2).run_id != canonical.run_id
    assert _canonical_id(tool_name=ToolName.INSPECT_RESOURCE_USAGE.value).run_id != (
        canonical.run_id
    )
    different_clone = CanonicalToolRunIdV02321.build(
        **{
            **canonical.model_dump(
                mode="python",
                exclude={
                    "schema_version",
                    "semantic_inputs_sha256",
                    "run_id",
                    "binding_sha256",
                },
            ),
            "state_clone_sha256": "0" * 64,
        }
    )
    assert different_clone.run_id != canonical.run_id
    assert CanonicalToolRunIdV02321.model_validate(
        canonical.model_dump(mode="json")
    ) == canonical
    with pytest.raises(ValidationError, match="frozen"):
        canonical.run_id = "0" * 32  # type: ignore[misc]


def test_typed_request_plan_constructs_and_freezes_the_actual_runtime_request() -> None:
    plan = build_traffic_harness_typed_request_plan_v02321(
        campaign_sha256=CAMPAIGN_SHA256,
        role="PREFLIGHT",
        state_clone_sha256=STATE_CLONE_SHA256,
        attempt_ordinal=1,
    )

    assert len(plan.request_entries) == 1
    entry = plan.request_entries[0]
    assert entry.tool_name == ToolName.INSPECT_SERVICE_RUNTIME.value
    assert entry.request_model == "InspectServiceRuntimeRequest"
    assert entry.target_services == ("checkout",)

    request = materialize_planned_request_v02321(
        plan,
        tool_name=ToolName.INSPECT_SERVICE_RUNTIME.value,
    )
    assert request.run_id == entry.run_id
    assert request.normalized_request_sha256 == entry.request_sha256
    assert require_live_request_in_plan_v02321(plan, request) == entry

    restored = type(plan).model_validate_json(plan.model_dump_json())
    assert restored == plan
    assert materialize_planned_request_v02321(
        restored,
        tool_name=ToolName.INSPECT_SERVICE_RUNTIME.value,
    ) == request
    with pytest.raises(ValidationError, match="frozen"):
        plan.attempt_ordinal = 2  # type: ignore[misc]


def test_typed_request_plan_rejects_an_unplanned_live_request() -> None:
    plan = build_traffic_harness_typed_request_plan_v02321(
        campaign_sha256=CAMPAIGN_SHA256,
        role="PREFLIGHT",
        state_clone_sha256=STATE_CLONE_SHA256,
        attempt_ordinal=1,
    )
    unplanned = build_inspect_service_runtime_request(
        run_id=_canonical_id(attempt_ordinal=2).run_id,
        services=("checkout",),
        max_results=1,
    )

    with pytest.raises(ValueError, match="not present in frozen typed request plan"):
        require_live_request_in_plan_v02321(plan, unplanned)

    same_run_id_different_request = build_inspect_service_runtime_request(
        run_id=plan.request_entries[0].run_id,
        services=("checkout", "frontend"),
        max_results=2,
    )
    with pytest.raises(ValueError, match="not present in frozen typed request plan"):
        require_live_request_in_plan_v02321(plan, same_run_id_different_request)


def test_history_verifier_binds_the_frozen_pr82_blocker_and_bytes() -> None:
    result = verify_product_v02321_history(ROOT)

    assert result == {
        "terminal": HISTORY_AND_REUSE_PASS_V02321,
        "predecessor_head": "cc270e5624af573a12bc31f3df9ca8cacad8685d",
        "blocker_terminal": "BLOCKED_ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT",
        "attempt_sha256": (
            "5080a440ca6a96cb8b93f104f873ace85ddef01cdc508d6a5656e4219a0221f9"
        ),
        "traffic_contract_sha256": (
            "8e2e6fabb139413ff5ff54efe516023e00f7d04c7b84b4d296b1aa42bf39ce1b"
        ),
        "formal_healthy_traffic_execution_count": 0,
        "successor_incident_count": 0,
        "successor_diagnosis_count": 0,
        "product_cleanup": "CLEAN",
        "demo_cleanup": "BLOCKED_BASELINE_UNCHANGED_UNPROVEN",
    }


def test_history_verifier_rejects_a_rewritten_predecessor_binding(
    tmp_path: Path,
) -> None:
    source = ROOT / "config/product-v02321/historical-results.v1.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["predecessor"]["attempt_sha256"] = "0" * 64
    changed = tmp_path / "historical-results.v1.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="historical manifest differs"):
        verify_product_v02321_history(ROOT, manifest_path=changed)


def test_history_verifier_rejects_substituted_tracked_path_with_fresh_seal(
    tmp_path: Path,
) -> None:
    source = ROOT / "config/product-v02321/historical-results.v1.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    tracked = payload["tracked_files"]
    tracked = [
        item
        for item in tracked
        if item["path"]
        != "docs/analysis/product-v0232-evidence-binding-preflight.json"
    ]
    replacement_path = ROOT / "pyproject.toml"
    replacement_bytes = replacement_path.read_bytes()
    tracked.append(
        {
            "path": "pyproject.toml",
            "revision": "cc270e5624af573a12bc31f3df9ca8cacad8685d",
            "sha256": hashlib.sha256(replacement_bytes).hexdigest(),
            "size_bytes": len(replacement_bytes),
        }
    )
    payload["tracked_files"] = sorted(tracked, key=lambda item: item["path"])
    body = dict(payload)
    body.pop("manifest_sha256")
    payload["manifest_sha256"] = semantic_sha256_v22(body)
    changed = tmp_path / "historical-results.v1.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="historical tracked files differ"):
        verify_product_v02321_history(ROOT, manifest_path=changed)


def test_offline_harness_preflight_reproduces_repairs_and_audits_call_sites() -> None:
    report = run_harness_contract_preflight_v02321(ROOT)

    assert report["terminal"] == TYPED_REQUEST_PLAN_PASS_V02321
    assert report["history_terminal"] == HISTORY_AND_REUSE_PASS_V02321
    assert report["predecessor_failure"] == {
        "failure_stage": "RUNTIME_INSPECT_REQUEST_BUILD",
        "safe_error_code": "RUN_ID_SCHEMA_PATTERN_MISMATCH",
        "observed_run_id": "product-v0232-traffic-preflight-1",
        "required_pattern": "^[0-9a-f]{32}$",
        "reproduced": True,
    }
    assert report["typed_request_count"] == 1
    assert report["campaign_sha256"] == CAMPAIGN_SHA256
    assert report["all_requests_validated_before_sandbox"] is True
    assert report["live_authorization"] is False
    assert report["live_request_plan_status"] == "PENDING_FRESH_SUCCESSOR_CLONE"
    assert report["infrastructure_session_count"] == 0
    assert report["traffic_attempt_count"] == 0
    audit = report["call_site_audit"]
    assert isinstance(audit, list)
    assert report["request_builder_call_site_count"] == 8
    assert report["run_id_argument_call_site_count"] == 21
    assert len(audit) == 29
    assert {item["site_kind"] for item in audit} == {
        "REQUEST_BUILDER_CALL",
        "RUN_ID_ARGUMENT",
    }
    assert all(
        {
            "path",
            "line",
            "site_kind",
            "caller",
            "callee",
            "source_expression",
            "disposition",
            "site_sha256",
        }
        <= set(item)
        for item in audit
    )
    assert {item["disposition"] for item in audit} == {
        "EXISTING_SEMANTIC_SHA_PREFIX",
        "HISTORICAL_ATTEMPT_LEDGER_ID",
        "HISTORICAL_CALLER_SUPPLIED_VALIDATED",
        "HISTORICAL_RANDOM_32HEX",
        "FROZEN_INVALID_PREDECESSOR",
        "FROZEN_INVALID_PREDECESSOR_SOURCE",
        "FROZEN_PREDECESSOR_OFFLINE_REPRODUCTION",
        "V02321_CANONICAL_FACTORY",
        "V02321_OFFLINE_PREDECESSOR_REPRODUCTION",
        "V02321_TYPED_PLAN_BINDING",
    }


def test_increment1_public_artifacts_are_exactly_rebuildable() -> None:
    expected = build_increment1_artifacts_v02321(ROOT)

    for relative, payload in expected.items():
        assert json.loads((ROOT / relative).read_text(encoding="utf-8")) == payload
