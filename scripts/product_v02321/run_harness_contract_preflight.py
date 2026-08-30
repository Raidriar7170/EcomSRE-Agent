#!/usr/bin/env python3
"""Run the offline Product v0.2.3.2.1 typed-request contract preflight."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any, Sequence, cast

from pydantic import ValidationError

from ecomsre.dta_v2.tool_contracts import build_inspect_service_runtime_request
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.typed_request_plan_v02321 import (
    build_traffic_harness_typed_request_plan_v02321,
    materialize_planned_request_v02321,
)
from scripts.ci.verify_product_v02321_history import (
    verify_product_v02321_history,
)


TYPED_REQUEST_PLAN_PASS_V02321 = (
    "ECOMSRE_PRODUCT_V02321_TYPED_REQUEST_PLAN_PASS"
)
_OFFLINE_STATE_CLONE_FIXTURE_SHA256 = semantic_sha256_v22(
    {
        "namespace": "ECOMSRE_PRODUCT_V02321",
        "purpose": "OFFLINE_TYPED_REQUEST_PLAN_FIXTURE_ONLY",
        "live_authorization": False,
    }
)
_SiteSignature = tuple[str, str, str, str, str]
_EXPECTED_CALL_SITES: dict[_SiteSignature, tuple[int, str]] = {
    (
        "REQUEST_BUILDER_CALL",
        "scripts/product_v0232/run_traffic_preflight.py",
        "_checkout_runtime",
        "build_inspect_service_runtime_request",
        "build_inspect_service_runtime_request(run_id=run_id, services=('checkout',), max_results=1)",
    ): (1, "FROZEN_INVALID_PREDECESSOR"),
    (
        "REQUEST_BUILDER_CALL",
        "scripts/ci/verify_product_v0232_blocker.py",
        "_verify_offline_reproduction",
        "build_inspect_service_runtime_request",
        "build_inspect_service_runtime_request(run_id=FAILED_RUN_ID_V0232, services=('checkout',), max_results=1)",
    ): (1, "FROZEN_PREDECESSOR_OFFLINE_REPRODUCTION"),
    (
        "REQUEST_BUILDER_CALL",
        "scripts/product_v02321/run_harness_contract_preflight.py",
        "_reproduce_predecessor_failure",
        "build_inspect_service_runtime_request",
        "build_inspect_service_runtime_request(run_id='product-v0232-traffic-preflight-1', services=('checkout',), max_results=1)",
    ): (1, "V02321_OFFLINE_PREDECESSOR_REPRODUCTION"),
    (
        "REQUEST_BUILDER_CALL",
        "src/ecomsre/product/incidents/read_backend.py",
        "_runtime_memory",
        "build_inspect_service_runtime_request",
        "build_inspect_service_runtime_request(run_id=incident.incident_sha256[:32], services=action.target_services, max_results=len(action.target_services))",
    ): (1, "EXISTING_SEMANTIC_SHA_PREFIX"),
    (
        "REQUEST_BUILDER_CALL",
        "src/ecomsre/product/pilot/live_calibration_v02.py",
        "_runtime_services",
        "build_inspect_service_runtime_request",
        "build_inspect_service_runtime_request(run_id=run_id, services=_CANDIDATE_SERVICES_V02, max_results=len(_CANDIDATE_SERVICES_V02))",
    ): (1, "HISTORICAL_CALLER_SUPPLIED_VALIDATED"),
    (
        "REQUEST_BUILDER_CALL",
        "src/ecomsre/product/pilot/typed_request_plan_v02321.py",
        "build_traffic_harness_typed_request_plan_v02321",
        "build_inspect_service_runtime_request",
        "build_inspect_service_runtime_request(run_id=identifier.run_id, services=identifier.target_services, max_results=1)",
    ): (1, "V02321_CANONICAL_FACTORY"),
    (
        "REQUEST_BUILDER_CALL",
        "src/ecomsre/product/pilot/typed_request_plan_v02321.py",
        "materialize_planned_request_v02321",
        "build_inspect_service_runtime_request",
        "build_inspect_service_runtime_request(run_id=entry.run_id, services=entry.target_services, max_results=len(entry.target_services))",
    ): (1, "V02321_CANONICAL_FACTORY"),
    (
        "REQUEST_BUILDER_CALL",
        "src/ecomsre/product/pilot/typed_request_plan_v02321.py",
        "require_complete_bound_plan",
        "build_inspect_service_runtime_request",
        "build_inspect_service_runtime_request(run_id=identifier.run_id, services=entry.target_services, max_results=len(entry.target_services))",
    ): (1, "V02321_CANONICAL_FACTORY"),
    (
        "RUN_ID_ARGUMENT",
        "scripts/product_v0232/run_traffic_preflight.py",
        "_checkout_runtime",
        "build_inspect_service_runtime_request",
        "run_id",
    ): (1, "FROZEN_INVALID_PREDECESSOR"),
    (
        "RUN_ID_ARGUMENT",
        "scripts/product_v0232/run_traffic_preflight.py",
        "run_traffic_preflight_v0232",
        "_checkout_runtime",
        "f'product-v0232-traffic-preflight-{attempt_ordinal}'",
    ): (1, "FROZEN_INVALID_PREDECESSOR_SOURCE"),
    (
        "RUN_ID_ARGUMENT",
        "scripts/ci/verify_product_v0232_blocker.py",
        "_verify_offline_reproduction",
        "build_inspect_service_runtime_request",
        "FAILED_RUN_ID_V0232",
    ): (1, "FROZEN_PREDECESSOR_OFFLINE_REPRODUCTION"),
    (
        "RUN_ID_ARGUMENT",
        "scripts/product_v02321/run_harness_contract_preflight.py",
        "_reproduce_predecessor_failure",
        "build_inspect_service_runtime_request",
        "'product-v0232-traffic-preflight-1'",
    ): (1, "V02321_OFFLINE_PREDECESSOR_REPRODUCTION"),
    (
        "RUN_ID_ARGUMENT",
        "src/ecomsre/product/incidents/read_backend.py",
        "_runtime_memory",
        "build_inspect_service_runtime_request",
        "incident.incident_sha256[:32]",
    ): (1, "EXISTING_SEMANTIC_SHA_PREFIX"),
    (
        "RUN_ID_ARGUMENT",
        "src/ecomsre/product/pilot/live_baseline_readiness_v021.py",
        "run_live_baseline_readiness_v021",
        "_runtime_services",
        "secrets.token_hex(16)",
    ): (1, "HISTORICAL_RANDOM_32HEX"),
    (
        "RUN_ID_ARGUMENT",
        "src/ecomsre/product/pilot/live_baseline_readiness_v021.py",
        "run_live_baseline_readiness_v021",
        "reserve_readiness_attempt_v021",
        "run_id",
    ): (1, "HISTORICAL_ATTEMPT_LEDGER_ID"),
    (
        "RUN_ID_ARGUMENT",
        "src/ecomsre/product/pilot/live_baseline_readiness_v023.py",
        "_require_clean_head",
        "AuditedSubprocessRunner",
        "secrets.token_hex(16)",
    ): (1, "HISTORICAL_RANDOM_32HEX"),
    (
        "RUN_ID_ARGUMENT",
        "src/ecomsre/product/pilot/live_baseline_readiness_v023.py",
        "run_live_baseline_readiness_v023",
        "_runtime_services",
        "secrets.token_hex(16)",
    ): (1, "HISTORICAL_RANDOM_32HEX"),
    (
        "RUN_ID_ARGUMENT",
        "src/ecomsre/product/pilot/live_calibration_v02.py",
        "_run_candidate_attempt",
        "_runtime_services",
        "secrets.token_hex(16)",
    ): (2, "HISTORICAL_RANDOM_32HEX"),
    (
        "RUN_ID_ARGUMENT",
        "src/ecomsre/product/pilot/live_calibration_v02.py",
        "_runtime_services",
        "build_inspect_service_runtime_request",
        "run_id",
    ): (1, "HISTORICAL_CALLER_SUPPLIED_VALIDATED"),
    (
        "RUN_ID_ARGUMENT",
        "src/ecomsre/product/pilot/live_calibration_v02.py",
        "run_live_calibration_v02",
        "_runtime_services",
        "secrets.token_hex(16)",
    ): (1, "HISTORICAL_RANDOM_32HEX"),
    (
        "RUN_ID_ARGUMENT",
        "src/ecomsre/product/pilot/live_calibration_v021.py",
        "_run_admitted_calibration_v021",
        "_runtime_services",
        "secrets.token_hex(16)",
    ): (1, "HISTORICAL_RANDOM_32HEX"),
    (
        "RUN_ID_ARGUMENT",
        "src/ecomsre/product/pilot/live_calibration_v021.py",
        "_run_candidate_attempt_v021",
        "_runtime_services",
        "secrets.token_hex(16)",
    ): (2, "HISTORICAL_RANDOM_32HEX"),
    (
        "RUN_ID_ARGUMENT",
        "src/ecomsre/product/pilot/live_nofault_acceptance_v023.py",
        "_runtime_snapshot",
        "_runtime_services",
        "secrets.token_hex(16)",
    ): (1, "HISTORICAL_RANDOM_32HEX"),
    (
        "RUN_ID_ARGUMENT",
        "src/ecomsre/product/pilot/typed_request_plan_v02321.py",
        "build_traffic_harness_typed_request_plan_v02321",
        "TrafficHarnessTypedRequestEntryV02321",
        "identifier.run_id",
    ): (1, "V02321_TYPED_PLAN_BINDING"),
    (
        "RUN_ID_ARGUMENT",
        "src/ecomsre/product/pilot/typed_request_plan_v02321.py",
        "build_traffic_harness_typed_request_plan_v02321",
        "build_inspect_service_runtime_request",
        "identifier.run_id",
    ): (1, "V02321_CANONICAL_FACTORY"),
    (
        "RUN_ID_ARGUMENT",
        "src/ecomsre/product/pilot/typed_request_plan_v02321.py",
        "materialize_planned_request_v02321",
        "build_inspect_service_runtime_request",
        "entry.run_id",
    ): (1, "V02321_CANONICAL_FACTORY"),
    (
        "RUN_ID_ARGUMENT",
        "src/ecomsre/product/pilot/typed_request_plan_v02321.py",
        "require_complete_bound_plan",
        "build_inspect_service_runtime_request",
        "identifier.run_id",
    ): (1, "V02321_CANONICAL_FACTORY"),
}


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


class _CallSiteVisitor(ast.NodeVisitor):
    def __init__(self, relative: str) -> None:
        self.relative = relative
        self.callers: list[str] = []
        self.sites: list[tuple[_SiteSignature, int]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.callers.append(node.name)
        self.generic_visit(node)
        self.callers.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.callers.append(node.name)
        self.generic_visit(node)
        self.callers.pop()

    def visit_Call(self, node: ast.Call) -> None:
        callee = _call_name(node) or ast.unparse(node.func)
        caller = self.callers[-1] if self.callers else "<module>"
        if re.fullmatch(r"build_[A-Za-z0-9_]+_request", callee):
            self.sites.append(
                (
                    (
                        "REQUEST_BUILDER_CALL",
                        self.relative,
                        caller,
                        callee,
                        ast.unparse(node),
                    ),
                    node.lineno,
                )
            )
        for keyword in node.keywords:
            if keyword.arg == "run_id":
                self.sites.append(
                    (
                        (
                            "RUN_ID_ARGUMENT",
                            self.relative,
                            caller,
                            callee,
                            ast.unparse(keyword.value),
                        ),
                        node.lineno,
                    )
                )
        self.generic_visit(node)


def _request_call_site_audit(root: Path) -> list[dict[str, object]]:
    candidates = tuple((root / "src/ecomsre/product").rglob("*.py")) + tuple(
        (root / "scripts").glob("product_v*/**/*.py")
    ) + tuple((root / "scripts/ci").glob("verify_product_*.py"))
    observed: list[tuple[_SiteSignature, int]] = []
    for source in sorted(set(candidates)):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        visitor = _CallSiteVisitor(source.relative_to(root).as_posix())
        visitor.visit(tree)
        observed.extend(visitor.sites)
    observed_counts = Counter(signature for signature, _ in observed)
    expected_counts = Counter(
        {signature: count for signature, (count, _) in _EXPECTED_CALL_SITES.items()}
    )
    if observed_counts != expected_counts:
        raise ValueError("Product/Pilot typed request call-site audit differs")
    audit: list[dict[str, object]] = []
    for signature, line in observed:
        site_kind, relative, caller, callee, expression = signature
        disposition = _EXPECTED_CALL_SITES[signature][1]
        body: dict[str, object] = {
            "path": relative,
            "line": line,
            "site_kind": site_kind,
            "caller": caller,
            "callee": callee,
            "source_expression": expression,
            "disposition": disposition,
        }
        audit.append({**body, "site_sha256": semantic_sha256_v22(body)})
    return sorted(
        audit,
        key=lambda item: (
            str(item["path"]),
            cast(int, item["line"]),
            str(item["site_kind"]),
        ),
    )


def _reproduce_predecessor_failure() -> dict[str, object]:
    try:
        build_inspect_service_runtime_request(
            run_id="product-v0232-traffic-preflight-1",
            services=("checkout",),
            max_results=1,
        )
    except ValidationError as error:
        observed = error.errors(include_url=False, include_input=False)
    else:
        raise ValueError("Product v0.2.3.2 invalid run ID no longer reproduces")
    if observed != [
        {
            "type": "string_pattern_mismatch",
            "loc": ("run_id",),
            "msg": "String should match pattern '^[0-9a-f]{32}$'",
            "ctx": {"pattern": "^[0-9a-f]{32}$"},
        }
    ]:
        raise ValueError("Product v0.2.3.2 invalid run ID failure differs")
    return {
        "failure_stage": "RUNTIME_INSPECT_REQUEST_BUILD",
        "safe_error_code": "RUN_ID_SCHEMA_PATTERN_MISMATCH",
        "observed_run_id": "product-v0232-traffic-preflight-1",
        "required_pattern": "^[0-9a-f]{32}$",
        "reproduced": True,
    }


from ecomsre.product.pilot.traffic_harness_closure_v02321 import (  # noqa: E402
    PREFLIGHT_CLOSURE_CONTRACT_PASS_V02321,
    TrafficHarnessClosureContractV02321,
    bind_changed_source_files_v02321,
)
from ecomsre.product.pilot.traffic_preflight_harness_v02321 import (  # noqa: E402
    OfflineTrafficPreflightBindingsV02321,
    execute_offline_failure_matrix_v02321,
)


def _load_successor_campaign_sha256(root: Path) -> str:
    campaign_path = root / "config/product-v02321/campaign.json"
    campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
    if not isinstance(campaign, dict):
        raise ValueError("Product v0.2.3.2.1 campaign differs")
    body = dict(campaign)
    supplied = body.pop("campaign_sha256", None)
    if (
        supplied != semantic_sha256_v22(body)
        or campaign.get("predecessor_head")
        != "cc270e5624af573a12bc31f3df9ca8cacad8685d"
        or campaign.get("traffic_contract_sha256")
        != "8e2e6fabb139413ff5ff54efe516023e00f7d04c7b84b4d296b1aa42bf39ce1b"
        or campaign.get("preflight_profile_sha256")
        != "20481ac92973ccf5de7510f565f066f13b9e1161e0e36faecec11cd12a40aa4a"
        or campaign.get("formal_profile_sha256")
        != "0110803ab9b39bf397295f1fd8904aee31fabf9b82b314bf586fae98188f6ce7"
    ):
        raise ValueError("Product v0.2.3.2.1 campaign differs")
    assert isinstance(supplied, str)
    return supplied


def _run_increment1_contract_preflight_v02321(
    project_root: Path,
) -> dict[str, Any]:
    root = Path(project_root).resolve(strict=True)
    history = verify_product_v02321_history(root)
    predecessor_failure = _reproduce_predecessor_failure()
    campaign_sha256 = _load_successor_campaign_sha256(root)
    plan = build_traffic_harness_typed_request_plan_v02321(
        campaign_sha256=campaign_sha256,
        role="PREFLIGHT",
        state_clone_sha256=_OFFLINE_STATE_CLONE_FIXTURE_SHA256,
        attempt_ordinal=1,
    )
    materialized = materialize_planned_request_v02321(
        plan,
        tool_name="inspect_service_runtime",
    )
    audit = _request_call_site_audit(root)
    request_builder_call_site_count = sum(
        item["site_kind"] == "REQUEST_BUILDER_CALL" for item in audit
    )
    run_id_argument_call_site_count = sum(
        item["site_kind"] == "RUN_ID_ARGUMENT" for item in audit
    )
    body: dict[str, Any] = {
        "schema_version": "ecomsre.product.harness-contract-preflight.v02321",
        "terminal": TYPED_REQUEST_PLAN_PASS_V02321,
        "history_terminal": history["terminal"],
        "predecessor_head": history["predecessor_head"],
        "campaign_sha256": campaign_sha256,
        "predecessor_failure": predecessor_failure,
        "offline_fixture_state_clone_sha256": (
            _OFFLINE_STATE_CLONE_FIXTURE_SHA256
        ),
        "offline_fixture_request_plan": plan.model_dump(mode="json"),
        "typed_request_count": len(plan.request_entries),
        "materialized_request_sha256": materialized.normalized_request_sha256,
        "all_requests_validated_before_sandbox": True,
        "live_request_plan_status": "PENDING_FRESH_SUCCESSOR_CLONE",
        "call_site_audit": audit,
        "request_builder_call_site_count": request_builder_call_site_count,
        "run_id_argument_call_site_count": run_id_argument_call_site_count,
        "live_authorization": False,
        "infrastructure_session_count": 0,
        "traffic_attempt_count": 0,
        "formal_healthy_traffic_execution_count": 0,
        "accepted_successor_incident_count": 0,
        "successor_diagnosis_count": 0,
        "fault_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "provider_calls": 0,
        "action_authority": "NONE",
    }
    return {**body, "preflight_sha256": semantic_sha256_v22(body)}


def build_increment2_closure_contract_v02321(
    project_root: Path,
) -> TrafficHarnessClosureContractV02321:
    root = Path(project_root).resolve(strict=True)
    increment1 = _run_increment1_contract_preflight_v02321(root)
    plan_payload = increment1["offline_fixture_request_plan"]
    if not isinstance(plan_payload, dict):
        raise ValueError("offline fixture request plan differs")
    entries = plan_payload.get("request_entries")
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError("offline fixture request entries differ")
    request_entry = entries[0]
    if not isinstance(request_entry, dict):
        raise ValueError("offline fixture request entry differs")
    runtime_descriptor = json.loads(
        (
            root
            / "docs/analysis/product-v0231-runtime-authority-descriptor.json"
        ).read_text(encoding="utf-8")
    )
    if not isinstance(runtime_descriptor, dict):
        raise ValueError("Runtime continuity descriptor differs")
    changed_source_bindings = bind_changed_source_files_v02321(
        root,
        (
            "scripts/product_v02321/run_harness_contract_preflight.py",
            "src/ecomsre/product/pilot/traffic_harness_closure_v02321.py",
            "src/ecomsre/product/pilot/traffic_preflight_harness_v02321.py",
        ),
    )
    bindings = OfflineTrafficPreflightBindingsV02321(
        request_plan_sha256=cast(str, plan_payload["plan_sha256"]),
        state_clone_sha256=_OFFLINE_STATE_CLONE_FIXTURE_SHA256,
        runtime_continuity_descriptor_sha256=cast(
            str, runtime_descriptor["descriptor_sha256"]
        ),
        runtime_inspect_request_sha256=cast(
            str, request_entry["request_sha256"]
        ),
        traffic_contract_sha256=(
            "8e2e6fabb139413ff5ff54efe516023e00f7d04c7b84b4d296b1aa42bf39ce1b"
        ),
        profile_sha256=(
            "20481ac92973ccf5de7510f565f066f13b9e1161e0e36faecec11cd12a40aa4a"
        ),
        queue_sha256=(
            "14bd13734d46566828779fd61b16e654cc260274a0e30ae9948371a9dbba5beb"
        ),
        outer_baseline_sha256=(
            "14bd13734d46566828779fd61b16e654cc260274a0e30ae9948371a9dbba5beb"
        ),
        endpoint_sha256=semantic_sha256_v22(
            {"endpoint": "http://127.0.0.1:18080/api/checkout"}
        ),
        first_cart_payload_sha256=semantic_sha256_v22(
            {
                "userId": "successor-contract-fixture",
                "item": {"productId": "0PUK6V6EV0", "quantity": 1},
            }
        ),
        changed_source_bindings=changed_source_bindings,
    )
    scenarios = execute_offline_failure_matrix_v02321(bindings)
    return TrafficHarnessClosureContractV02321.build(
        terminal=PREFLIGHT_CLOSURE_CONTRACT_PASS_V02321,
        typed_request_plan_terminal=TYPED_REQUEST_PLAN_PASS_V02321,
        offline_fixture_request_plan_sha256=bindings.request_plan_sha256,
        offline_fixture_state_clone_sha256=bindings.state_clone_sha256,
        runtime_continuity_descriptor_sha256=(
            bindings.runtime_continuity_descriptor_sha256
        ),
        traffic_contract_sha256=bindings.traffic_contract_sha256,
        scenarios=[item.model_dump(mode="json") for item in scenarios],
        request_plan_failure_consumes_neither=True,
        sandbox_start_consumes_session_only=True,
        runtime_failure_consumes_session_only=True,
        first_cart_send_consumes_attempt=True,
        queue_baseline_prestate_before_runtime_inspect=True,
        resource_absence_not_promoted_to_clean=True,
        append_only_ledger=True,
        live_authorization=False,
        infrastructure_session_count=0,
        traffic_attempt_count=0,
        formal_healthy_traffic_execution_count=0,
    )


def run_harness_contract_preflight_v02321(project_root: Path) -> dict[str, Any]:
    increment1 = _run_increment1_contract_preflight_v02321(project_root)
    closure = build_increment2_closure_contract_v02321(project_root)
    body = {
        **{key: value for key, value in increment1.items() if key != "preflight_sha256"},
        "terminal": closure.terminal,
        "typed_request_plan_terminal": increment1["terminal"],
        "preflight_closure_contract_sha256": closure.contract_sha256,
        "offline_failure_injection_scenario_count": len(closure.scenarios),
    }
    return {**body, "preflight_sha256": semantic_sha256_v22(body)}


def build_increment1_artifacts_v02321(
    project_root: Path,
) -> dict[str, dict[str, Any]]:
    root = Path(project_root).resolve(strict=True)
    report = _run_increment1_contract_preflight_v02321(root)
    manifest = json.loads(
        (root / "config/product-v02321/historical-results.v1.json").read_text(
            encoding="utf-8"
        )
    )
    if not isinstance(manifest, dict):
        raise ValueError("Product v0.2.3.2.1 historical manifest differs")
    audit_body: dict[str, Any] = {
        "schema_version": "ecomsre.product.predecessor-audit.v02321",
        "terminal": report["history_terminal"],
        "starting_main": manifest["starting_main"],
        "predecessor": manifest["predecessor"],
        "frozen_bindings": manifest["frozen_bindings"],
        "tracked_file_count": len(manifest["tracked_files"]),
        "historical_manifest_sha256": manifest["manifest_sha256"],
        "frozen_files_match_predecessor_head": True,
    }
    audit = {**audit_body, "audit_sha256": semantic_sha256_v22(audit_body)}
    return {
        "docs/analysis/product-v02321-predecessor-audit.json": audit,
        "docs/analysis/product-v02321-harness-contract-preflight.json": report,
    }


def _build_increment1_progress_v02321(
    report: dict[str, Any],
) -> dict[str, Any]:
    fixture_plan = report["offline_fixture_request_plan"]
    progress_body: dict[str, Any] = {
        "schema_version": "ecomsre.product.progress.v02321",
        "goal_version": "ecomsre-product-v02321-traffic-harness-repair-nofault-v1",
        "terminal": report["terminal"],
        "increment": 1,
        "history_terminal": report["history_terminal"],
        "predecessor_head": report["predecessor_head"],
        "campaign_sha256": report["campaign_sha256"],
        "offline_fixture_request_plan_sha256": fixture_plan["plan_sha256"],
        "harness_contract_preflight_sha256": report["preflight_sha256"],
        "offline_harness_iteration_count": 1,
        "live_authorization": False,
        "live_request_plan_status": "PENDING_FRESH_SUCCESSOR_CLONE",
        "infrastructure_session_count": 0,
        "traffic_attempt_count": 0,
        "formal_healthy_traffic_execution_count": 0,
        "accepted_successor_incident_count": 0,
        "successor_diagnosis_count": 0,
        "fault_attempt_count": 0,
        "new_baseline_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "fault_family_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "provider_calls": 0,
        "action_authority": "NONE",
    }
    return {
        **progress_body,
        "progress_sha256": semantic_sha256_v22(progress_body),
    }


def build_increment2_artifacts_v02321(
    project_root: Path,
) -> dict[str, dict[str, Any]]:
    root = Path(project_root).resolve(strict=True)
    contract = build_increment2_closure_contract_v02321(root)
    progress = _build_increment1_progress_v02321(
        _run_increment1_contract_preflight_v02321(root)
    )
    base = dict(progress)
    supplied = base.pop("progress_sha256", None)
    if (
        supplied != semantic_sha256_v22(base)
        or base.get("increment") != 1
        or base.get("terminal") != TYPED_REQUEST_PLAN_PASS_V02321
        or base.get("infrastructure_session_count") != 0
        or base.get("traffic_attempt_count") != 0
        or base.get("formal_healthy_traffic_execution_count") != 0
    ):
        raise ValueError("Product v0.2.3.2.1 Increment 1 progress differs")
    progress_body = {
        **base,
        "increment": 2,
        "terminal": contract.terminal,
        "typed_request_plan_terminal": TYPED_REQUEST_PLAN_PASS_V02321,
        "preflight_closure_contract_sha256": contract.contract_sha256,
        "offline_failure_injection_scenario_count": len(contract.scenarios),
        "offline_harness_iteration_count": 2,
    }
    updated_progress = {
        **progress_body,
        "progress_sha256": semantic_sha256_v22(progress_body),
    }
    return {
        "docs/analysis/product-v02321-preflight-closure-contract.json": (
            contract.model_dump(mode="json")
        ),
        "docs/analysis/product-v02321-progress.json": updated_progress,
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
            run_harness_contract_preflight_v02321(arguments.project_root),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "TYPED_REQUEST_PLAN_PASS_V02321",
    "build_increment1_artifacts_v02321",
    "build_increment2_artifacts_v02321",
    "build_increment2_closure_contract_v02321",
    "run_harness_contract_preflight_v02321",
)
