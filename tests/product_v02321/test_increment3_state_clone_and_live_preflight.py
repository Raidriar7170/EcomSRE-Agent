from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import httpx
import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot import formal_contract_v02321
from ecomsre.product.pilot.product_state_clone_v0232 import (
    ProductStateCloneV0232,
    ProductStateSourceV0232,
)
from ecomsre.product.pilot.product_state_clone_v02321 import (
    PREFLIGHT_STATE_CLONE_PASS_V02321,
    PreflightStateCloneReportV02321,
)
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    HealthyTrafficExecutionV0232,
    HealthyTrafficProfileV0232,
    HealthyTrafficRunnerV0232,
    load_checkout_traffic_contract_v0232,
)
from ecomsre.product.pilot.formal_contract_v02321 import (
    FORMAL_CONTRACT_FREEZE_PASS_V02321,
    FormalContractFreezeV02321,
    FormalPreExecutionReviewV02321,
    build_formal_contract_freeze_v02321,
    require_formal_pre_execution_review_binding_v02321,
    verify_formal_contract_freeze_v02321,
    verify_formal_pre_execution_review_v02321,
)
from ecomsre.product.pilot.traffic_harness_closure_v02321 import (
    InfrastructureSessionCompletionV02321,
    InfrastructureSessionStartV02321,
    TrafficHarnessClosureV02321,
    TrafficHarnessStageV02321,
    TrafficPreflightAttemptCompletionV02321,
    TrafficPreflightAttemptStartV02321,
    TrafficPreflightLedgerV02321,
)
from ecomsre.product.pilot.traffic_preflight_live_v02321 import (
    TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V02321,
    TRAFFIC_PREFLIGHT_PASS_V02321,
    LiveTrafficPreflightAttemptV02321,
    LiveTrafficPreflightPassV02321,
)
from ecomsre.product.pilot.typed_request_plan_v02321 import (
    build_traffic_harness_typed_request_plan_v02321,
)
from scripts.ci.verify_product_v0232_history import (
    expected_source_repository_binding_v0232,
)
from scripts.product_v02321 import run_state_clone
from scripts.product_v02321 import run_traffic_preflight
from scripts.product_v02321.run_state_clone import (
    create_preflight_state_clone_v02321,
)
from scripts.product_v02321.run_harness_contract_preflight import (
    build_increment2_closure_contract_v02321,
)
from ecomsre_live_sandbox.contracts import CleanupResult, canonical_json_bytes


ROOT = Path(__file__).resolve().parents[2]

_FORMAL_FREEZE_INPUTS = (
    "config/product-v0232/traffic/contract.json",
    "config/product-v0232/traffic/preflight-profile.json",
    "config/product-v0232/traffic/formal-profile.json",
    "config/product-v02321/traffic/preflight-profile-binding.json",
    "config/product-v02321/traffic/formal-profile-binding.json",
    "config/product-v02321/typed-request-plan.json",
    "docs/analysis/product-v0232-predecessor-audit.json",
    "docs/analysis/product-v02321-product-state-clone-preflight.json",
    "docs/analysis/product-v02321-traffic-preflight-attempt-1.json",
    "docs/analysis/product-v02321-traffic-preflight-ledger.json",
    "docs/analysis/product-v02321-traffic-preflight.json",
    "docs/analysis/product-v02321-progress.json",
    "src/ecomsre/product/pilot/typed_request_plan_v02321.py",
    "src/ecomsre/product/incidents/contracts.py",
    "src/ecomsre/product/incidents/evidence_binding_v0232.py",
    "src/ecomsre/product/pilot/nofault_acceptance_v0232.py",
)


def _formal_freeze_fixture(tmp_path: Path) -> Path:
    for relative in _FORMAL_FREEZE_INPUTS:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    progress_path = tmp_path / "docs/analysis/product-v02321-progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    progress.pop("progress_sha256")
    plan = json.loads(
        (tmp_path / "config/product-v02321/typed-request-plan.json").read_text(
            encoding="utf-8"
        )
    )
    clone = json.loads(
        (
            tmp_path
            / "docs/analysis/product-v02321-product-state-clone-preflight.json"
        ).read_text(encoding="utf-8")
    )
    progress.update(
        {
            "live_request_plan_status": "PASS",
            "live_traffic_preflight_status": "PASS",
            "typed_request_plan_sha256": plan["plan_sha256"],
            "product_state_clone_report_sha256": clone["report_sha256"],
            "product_state_clone_sha256": clone["clone"]["clone_sha256"],
            "source_state_sha256": clone["source_state"]["source_sha256"],
        }
    )
    progress_path.write_bytes(
        canonical_json_bytes(
            {**progress, "progress_sha256": semantic_sha256_v22(progress)}
        )
    )
    return tmp_path


def _use_root_traffic_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        formal_contract_v02321,
        "load_checkout_traffic_contract_v0232",
        lambda _project: load_checkout_traffic_contract_v0232(ROOT),
    )


def _pre_session_reservation_payload(
    *,
    attempt_label: str,
    reservation_ordinal: int = 1,
    infrastructure_session_count_before: int = 0,
    traffic_attempt_count_before: int = 0,
) -> dict[str, object]:
    changed_source_bindings = [
        {
            "path": "scripts/product_v02321/run_traffic_preflight.py",
            "sha256": "b" * 64,
            "size_bytes": 1,
        }
    ]
    return {
        "attempt_label": attempt_label,
        "reservation_ordinal": reservation_ordinal,
        "prior_pre_session_sha256": None,
        "prior_evidence_path": None,
        "prior_evidence_file_sha256": None,
        "changed_surface": "INITIAL",
        "changed_source_bindings": changed_source_bindings,
        "changed_implementation_sha256": semantic_sha256_v22(
            {"changed_source_bindings": changed_source_bindings}
        ),
        "repair_rationale": "initial recovery fixture",
        "infrastructure_session_count_before": (
            infrastructure_session_count_before
        ),
        "traffic_attempt_count_before": traffic_attempt_count_before,
        "formal_healthy_traffic_execution_count": 0,
        "accepted_successor_incident_count": 0,
        "successor_diagnosis_count": 0,
        "action_authority": "NONE",
    }


def _preserved_runtime_fixture(tmp_path: Path) -> tuple[Path, Path]:
    predecessor_root = tmp_path / "predecessor"
    source_root = predecessor_root / run_traffic_preflight.SOURCE_LOCATOR_V0232
    source_root.mkdir(parents=True)
    return predecessor_root, source_root


def _successful_execution() -> HealthyTrafficExecutionV0232:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if request.url.path == "/api/cart":
            return httpx.Response(
                200,
                json={
                    "userId": payload["userId"],
                    "items": [{"productId": "0PUK6V6EV0", "quantity": 1}],
                },
            )
        return httpx.Response(
            200,
            json={
                "orderId": "order-fixture",
                "shippingTrackingId": "tracking-fixture",
                "shippingCost": {
                    "currencyCode": "USD",
                    "units": 1,
                    "nanos": 0,
                },
                "shippingAddress": {
                    "streetAddress": "1 Contract Way",
                    "city": "Local",
                    "state": "CA",
                    "country": "United States",
                    "zipCode": "94016",
                },
                "items": [
                    {
                        "item": {
                            "productId": "0PUK6V6EV0",
                            "quantity": 1,
                            "product": {"id": "0PUK6V6EV0"},
                        },
                        "cost": {
                            "currencyCode": "USD",
                            "units": 1,
                            "nanos": 0,
                        },
                    }
                ],
            },
        )

    profile = HealthyTrafficProfileV0232.build(
        profile_id="product-v02321-preflight",
        transactions=10,
        requests_per_second=1.0,
        request_seed=23083211,
        maximum_failures=0,
        stabilization_seconds=0,
        minimum_full_episode_duration_seconds=0,
        queue_fault_flag=0,
    )
    with HealthyTrafficRunnerV0232(
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    ) as runner:
        return runner.run(
            endpoint="http://127.0.0.1:18080/api/checkout",
            profile=profile,
            contract=load_checkout_traffic_contract_v0232(ROOT),
            role="PREFLIGHT",
        )


def _successful_ledger(
    execution: HealthyTrafficExecutionV0232,
    *,
    state_clone_sha256: str,
) -> TrafficPreflightLedgerV02321:
    scenario = next(
        item
        for item in build_increment2_closure_contract_v02321(ROOT).scenarios
        if item.scenario_id == "FIRST_CART_SEND_FAILURE"
    )
    prior_session_start = scenario.ledger.events[0]
    prior_attempt_start = scenario.ledger.events[1]
    prior_completion = scenario.ledger.events[2]
    prior_closure = scenario.ledger.events[3]
    prior_session_completion = scenario.ledger.events[4]
    assert isinstance(prior_session_start, InfrastructureSessionStartV02321)
    assert isinstance(prior_attempt_start, TrafficPreflightAttemptStartV02321)
    assert isinstance(prior_completion, TrafficPreflightAttemptCompletionV02321)
    assert isinstance(prior_closure, TrafficHarnessClosureV02321)
    assert isinstance(
        prior_session_completion, InfrastructureSessionCompletionV02321
    )

    session_start_body = prior_session_start.model_dump(
        mode="json", exclude={"session_id", "event_sha256"}
    )
    session_start_body["state_clone_sha256"] = state_clone_sha256
    session_start = InfrastructureSessionStartV02321.build(
        **session_start_body
    )

    attempt_start_body = prior_attempt_start.model_dump(
        mode="json", exclude={"attempt_id", "event_sha256"}
    )
    attempt_start_body.update(
        prior_event_sha256=session_start.event_sha256,
        session_id=session_start.session_id,
        session_start_sha256=session_start.event_sha256,
    )
    attempt_start_body["profile_sha256"] = execution.run.profile_sha256
    attempt_start = TrafficPreflightAttemptStartV02321.build(
        **attempt_start_body
    )

    completion_body = prior_completion.model_dump(
        mode="json", exclude={"event_sha256"}
    )
    completion_body.update(
        attempt_id=attempt_start.attempt_id,
        attempt_start_sha256=attempt_start.event_sha256,
        prior_event_sha256=attempt_start.event_sha256,
        session_id=attempt_start.session_id,
        traffic_execution_sha256=execution.execution_sha256,
        traffic_dispatch_failure=None,
        stage=TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE,
        completed_transactions=10,
        successful_transactions=10,
        failed_transactions=0,
        safe_error_code=None,
        terminal="ATTEMPT_PASS",
    )
    completion = TrafficPreflightAttemptCompletionV02321.build(
        **completion_body
    )

    stages = list(prior_closure.observed_stage_sequence)
    stages.insert(
        stages.index(TrafficHarnessStageV02321.QUEUE_POSTSTATE_CAPTURED),
        TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE,
    )
    closure_body = prior_closure.model_dump(
        mode="json", exclude={"closure_sha256"}
    )
    closure_body.update(
        prior_event_sha256=completion.event_sha256,
        session_id=session_start.session_id,
        attempt_id=attempt_start.attempt_id,
        stage_reached=TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE,
        observed_stage_sequence=stages,
        traffic_execution_sha256=execution.execution_sha256,
        traffic_dispatch_failure_sha256=None,
        failure_stage=None,
        safe_error_code=None,
    )
    closure = TrafficHarnessClosureV02321.build(**closure_body)

    session_completion_body = prior_session_completion.model_dump(
        mode="json", exclude={"event_sha256"}
    )
    session_completion_body.update(
        prior_event_sha256=closure.closure_sha256,
        session_id=session_start.session_id,
        session_start_sha256=session_start.event_sha256,
        closure_sha256=closure.closure_sha256,
        stage_reached=closure.stage_reached,
    )
    session_completion = InfrastructureSessionCompletionV02321.build(
        **session_completion_body
    )
    return TrafficPreflightLedgerV02321.build(
        events=(
            session_start,
            attempt_start,
            completion,
            closure,
            session_completion,
        )
    )


def _successful_live_attempt() -> LiveTrafficPreflightAttemptV02321:
    source, predecessor_clone = _frozen_source_and_clone()
    destination_locator = (
        ".local/product-v02321/product-state/"
        f"preflight-{source.source_sha256[:24]}/product"
    )
    clone = _rebind_clone_destination(predecessor_clone, destination_locator)
    destination_state = _rebind_source_locator(source, destination_locator)
    clone_report = PreflightStateCloneReportV02321.build(
        source_repository_binding=expected_source_repository_binding_v0232(),
        predecessor_private_acceptance={"terminal": "PRIVATE_HISTORY_VERIFIED"},
        source_state=source.model_dump(mode="json"),
        clone=clone.model_dump(mode="json"),
        destination_state=destination_state.model_dump(mode="json"),
        destination_locator=destination_locator,
        source_incident_count=1,
        source_diagnosis_count=1,
        fault_family_count=0,
        knowledge_artifact_count=0,
    )
    execution = _successful_execution()
    ledger = _successful_ledger(
        execution, state_clone_sha256=clone.clone_sha256
    )
    session_start = ledger.events[0]
    attempt_start = ledger.events[1]
    closure = ledger.events[3]
    assert isinstance(session_start, InfrastructureSessionStartV02321)
    assert isinstance(attempt_start, TrafficPreflightAttemptStartV02321)
    assert isinstance(closure, TrafficHarnessClosureV02321)
    return LiveTrafficPreflightAttemptV02321.build(
        attempt_id=attempt_start.attempt_id,
        attempt_ordinal=attempt_start.attempt_ordinal,
        typed_request_plan_sha256=attempt_start.request_plan_sha256,
        product_state_clone_report_sha256=clone_report.report_sha256,
        product_state_clone_report=clone_report,
        product_state_clone_sha256=clone.clone_sha256,
        traffic_contract_sha256=execution.run.contract_sha256,
        traffic_profile_sha256=execution.run.profile_sha256,
        formal_profile_sha256="b" * 64,
        runtime_continuity_descriptor_sha256=(
            session_start.runtime_continuity_descriptor_sha256
        ),
        traffic_execution=execution,
        closure_sha256=closure.closure_sha256,
        ledger=ledger,
        source_state_before_sha256=source.source_sha256,
        source_state_after_sha256=source.source_sha256,
        product_state_before_sha256=destination_state.source_sha256,
        product_state_after_sha256=destination_state.source_sha256,
        incident_count_before=1,
        incident_count_after=1,
        diagnosis_count_before=1,
        diagnosis_count_after=1,
        infrastructure_session_count_after=1,
        traffic_attempt_count_after=1,
        formal_healthy_traffic_execution_count=0,
        accepted_successor_incident_count=0,
        successor_diagnosis_count=0,
        fault_attempt_count=0,
        knowledge_loop_campaign_count=0,
        agent_writes=0,
        runbook_executions=0,
        provider_calls=0,
        action_authority="NONE",
    )


def _frozen_source_and_clone() -> tuple[
    ProductStateSourceV0232, ProductStateCloneV0232
]:
    audit = json.loads(
        (ROOT / "docs/analysis/product-v0232-predecessor-audit.json").read_text(
            encoding="utf-8"
        )
    )
    source = ProductStateSourceV0232.model_validate(audit["source_state"])
    clone = ProductStateCloneV0232.model_validate_json(
        (
            ROOT / "docs/analysis/product-v0232-product-state-clone.json"
        ).read_bytes()
    )
    return source, clone


def _rebind_clone_destination(
    clone: ProductStateCloneV0232,
    destination_locator: str,
) -> ProductStateCloneV0232:
    body = clone.model_dump(mode="json", exclude={"clone_sha256"})
    body["destination_locator"] = destination_locator
    return ProductStateCloneV0232.model_validate(
        {**body, "clone_sha256": semantic_sha256_v22(body)}
    )


def _rebind_source_locator(
    source: ProductStateSourceV0232,
    destination_locator: str,
) -> ProductStateSourceV0232:
    body = source.model_dump(mode="json", exclude={"source_sha256"})
    body["source_locator"] = destination_locator
    return ProductStateSourceV0232.model_validate(
        {**body, "source_sha256": semantic_sha256_v22(body)}
    )


def _rebind_source_object_inventory(
    source: ProductStateSourceV0232,
    object_inventory_sha256: str,
) -> ProductStateSourceV0232:
    body = source.model_dump(mode="json", exclude={"source_sha256"})
    body["source_object_inventory_sha256"] = object_inventory_sha256
    return ProductStateSourceV0232.model_validate(
        {**body, "source_sha256": semantic_sha256_v22(body)}
    )


def test_preflight_clone_runner_writes_one_cross_bound_successor_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, predecessor_clone = _frozen_source_and_clone()
    source_root = tmp_path / "source" / "product"
    source_root.mkdir(parents=True)
    acceptance = tmp_path / "acceptance.json"
    acceptance.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        run_state_clone,
        "verify_product_v02321_history",
        lambda _root: {"terminal": "ECOMSRE_PRODUCT_V02321_HISTORY_AND_REUSE_PASS"},
    )
    monkeypatch.setattr(
        run_state_clone,
        "verify_product_v0232_private_result",
        lambda _path: {"terminal": "PRIVATE_HISTORY_VERIFIED"},
    )
    monkeypatch.setattr(
        run_state_clone,
        "_require_fixed_source_root",
        lambda _root: expected_source_repository_binding_v0232(),
    )
    monkeypatch.setattr(run_state_clone, "_require_source_unowned", lambda _path: None)
    monkeypatch.setattr(
        run_state_clone, "_load_frozen_source_state", lambda _root: source
    )
    def admit_fixture(_root: Path, *, locator: str) -> ProductStateSourceV0232:
        if locator == source.source_locator:
            return source
        return _rebind_source_locator(source, locator)

    monkeypatch.setattr(run_state_clone, "_admit_state", admit_fixture)

    def clone_fixture(
        _source_root: Path,
        _destination_root: Path,
        **payload: object,
    ) -> ProductStateCloneV0232:
        _destination_root.mkdir(parents=True)
        destination_locator = payload["destination_locator"]
        assert isinstance(destination_locator, str)
        return _rebind_clone_destination(
            predecessor_clone,
            destination_locator,
        )

    monkeypatch.setattr(run_state_clone, "clone_product_state_v0232", clone_fixture)

    report = create_preflight_state_clone_v02321(
        project_root=tmp_path,
        source_root=source_root,
        predecessor_private_acceptance=acceptance,
    )

    assert report.terminal == PREFLIGHT_STATE_CLONE_PASS_V02321
    assert report.destination_locator.startswith(
        ".local/product-v02321/product-state/preflight-"
    )
    assert report.source_incident_count == report.source_diagnosis_count == 1
    assert report.fault_family_count == 0
    assert report.knowledge_artifact_count == 0
    written = PreflightStateCloneReportV02321.model_validate_json(
        (
            tmp_path
            / "docs/analysis/product-v02321-product-state-clone-preflight.json"
        ).read_bytes()
    )
    assert written == report

    with pytest.raises(FileExistsError, match="clone report exists"):
        create_preflight_state_clone_v02321(
            project_root=tmp_path,
            source_root=source_root,
            predecessor_private_acceptance=acceptance,
        )

    report_path = (
        tmp_path
        / "docs/analysis/product-v02321-product-state-clone-preflight.json"
    )
    report_path.unlink()
    monkeypatch.setattr(
        run_state_clone,
        "clone_product_state_v0232",
        lambda *_args, **_kwargs: pytest.fail(
            "an admitted deterministic clone must not be copied twice"
        ),
    )
    recovered = create_preflight_state_clone_v02321(
        project_root=tmp_path,
        source_root=source_root,
        predecessor_private_acceptance=acceptance,
    )
    assert recovered == report


def test_formal_contract_freeze_binds_preflight_semantics_and_uncreated_clone_plan(
) -> None:
    freeze = build_formal_contract_freeze_v02321(ROOT)

    assert freeze.terminal == FORMAL_CONTRACT_FREEZE_PASS_V02321
    assert verify_formal_contract_freeze_v02321(ROOT) == freeze
    assert freeze.traffic_contract_sha256 == (
        "8e2e6fabb139413ff5ff54efe516023e00f7d04c7b84b4d296b1aa42bf39ce1b"
    )
    assert freeze.preflight_profile_sha256 == (
        "20481ac92973ccf5de7510f565f066f13b9e1161e0e36faecec11cd12a40aa4a"
    )
    assert freeze.formal_profile_sha256 == (
        "0110803ab9b39bf397295f1fd8904aee31fabf9b82b314bf586fae98188f6ce7"
    )
    assert freeze.formal_clone_plan.status == "PLANNED_NOT_CREATED"
    assert freeze.formal_clone_plan.source_state_sha256 == (
        freeze.source_state_sha256
    )
    assert not (ROOT / freeze.formal_clone_plan.destination_locator).exists()
    assert freeze.formal_healthy_traffic_execution_count == 0
    assert freeze.accepted_successor_incident_count == 0
    assert freeze.successor_diagnosis_count == 0
    assert freeze.action_authority == "NONE"

    body = freeze.model_dump(mode="json", exclude={"freeze_sha256"})
    body["formal_profile_sha256"] = "f" * 64
    body["freeze_sha256"] = semantic_sha256_v22(body)
    with pytest.raises(ValueError, match="formal contract freeze binding differs"):
        FormalContractFreezeV02321.model_validate(body)


def test_formal_contract_freeze_rejects_independent_typed_plan_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _formal_freeze_fixture(tmp_path)
    _use_root_traffic_contract(monkeypatch)
    plan_path = project / "config/product-v02321/typed-request-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    drifted = build_traffic_harness_typed_request_plan_v02321(
        campaign_sha256="f" * 64,
        role=plan["role"],
        state_clone_sha256=plan["state_clone_sha256"],
        attempt_ordinal=plan["attempt_ordinal"],
    )
    plan_path.write_bytes(canonical_json_bytes(drifted.model_dump(mode="json")))

    with pytest.raises(ValueError, match="public preflight binding differs"):
        build_formal_contract_freeze_v02321(project)


def test_formal_contract_freeze_rejects_broken_formal_clone_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _formal_freeze_fixture(tmp_path)
    _use_root_traffic_contract(monkeypatch)
    source = ProductStateSourceV0232.model_validate(
        json.loads(
            (
                project / "docs/analysis/product-v0232-predecessor-audit.json"
            ).read_text(encoding="utf-8")
        )["source_state"]
    )
    clone_root = (
        project
        / ".local/product-v02321/product-state"
        / f"formal-{source.source_sha256[:24]}"
    )
    clone_root.mkdir(parents=True)
    os.symlink(clone_root / "missing-target", clone_root / "product")

    with pytest.raises(FileExistsError, match="formal clone already exists"):
        build_formal_contract_freeze_v02321(project)


def test_formal_contract_freeze_rejects_premature_formal_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _formal_freeze_fixture(tmp_path)
    _use_root_traffic_contract(monkeypatch)
    output = project / "docs/analysis/product-v02321-formal-traffic.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError, match="formal output already exists"):
        build_formal_contract_freeze_v02321(project)


def test_formal_contract_freeze_rejects_progress_counter_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _formal_freeze_fixture(tmp_path)
    _use_root_traffic_contract(monkeypatch)
    progress_path = project / "docs/analysis/product-v02321-progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    progress.pop("progress_sha256")
    progress["formal_healthy_traffic_execution_count"] = 1
    progress_path.write_bytes(
        canonical_json_bytes(
            {**progress, "progress_sha256": semantic_sha256_v22(progress)}
        )
    )

    with pytest.raises(ValueError, match="progress binding differs"):
        build_formal_contract_freeze_v02321(project)


def test_completed_preflight_progress_replaces_pending_clone_status() -> None:
    preflight = LiveTrafficPreflightPassV02321.model_validate_json(
        (ROOT / "docs/analysis/product-v02321-traffic-preflight.json").read_bytes()
    )

    progress = run_traffic_preflight._updated_progress(
        ROOT,
        terminal=TRAFFIC_PREFLIGHT_PASS_V02321,
        ledger=preflight.attempt.ledger,
        attempt=preflight.attempt,
        preflight=preflight,
    )

    assert progress["live_request_plan_status"] == "PASS"
    assert progress["live_traffic_preflight_status"] == "PASS"
    assert progress["typed_request_plan_sha256"] == (
        preflight.attempt.typed_request_plan_sha256
    )
    assert progress["product_state_clone_report_sha256"] == (
        preflight.attempt.product_state_clone_report_sha256
    )
    assert progress["product_state_clone_sha256"] == (
        preflight.attempt.product_state_clone_sha256
    )
    assert "PENDING_FRESH_SUCCESSOR_CLONE" not in progress.values()


def test_pre_execution_review_is_self_sealed_and_exactly_freeze_bound() -> None:
    freeze = verify_formal_contract_freeze_v02321(ROOT)
    review = verify_formal_pre_execution_review_v02321(ROOT)

    assert review.review_disposition == "PASS"
    assert review.must_fix_count == 0
    assert review.claim_accuracy == "PASS"
    assert review.formal_execution_authorized is True
    assert review.formal_clone_observed_status == "ABSENT"

    body = review.model_dump(mode="json", exclude={"review_sha256"})
    body["source_state_sha256"] = "f" * 64
    drifted = FormalPreExecutionReviewV02321.model_validate(
        {**body, "review_sha256": semantic_sha256_v22(body)}
    )
    with pytest.raises(ValueError, match="pre-execution review binding differs"):
        require_formal_pre_execution_review_binding_v02321(
            drifted,
            freeze,
            freeze_file_sha256=review.formal_contract_freeze_file_sha256,
        )


def test_preflight_clone_runner_rejects_state_not_equal_to_frozen_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen, _clone = _frozen_source_and_clone()
    drifted = _rebind_source_object_inventory(frozen, "f" * 64)
    source_root = tmp_path / "source/product"
    source_root.mkdir(parents=True)
    acceptance = tmp_path / "acceptance.json"
    acceptance.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(run_state_clone, "verify_product_v02321_history", lambda _r: {})
    monkeypatch.setattr(
        run_state_clone,
        "verify_product_v0232_private_result",
        lambda _p: {"terminal": "PRIVATE_HISTORY_VERIFIED"},
    )
    monkeypatch.setattr(
        run_state_clone,
        "_require_fixed_source_root",
        lambda _r: expected_source_repository_binding_v0232(),
    )
    monkeypatch.setattr(run_state_clone, "_require_source_unowned", lambda _p: None)
    monkeypatch.setattr(run_state_clone, "_admit_state", lambda *_a, **_k: drifted)
    monkeypatch.setattr(
        run_state_clone, "_load_frozen_source_state", lambda _r: frozen
    )
    monkeypatch.setattr(
        run_state_clone,
        "clone_product_state_v0232",
        lambda *_a, **_k: pytest.fail("drifted source must not be cloned"),
    )

    with pytest.raises(ValueError, match="frozen source state differs"):
        create_preflight_state_clone_v02321(
            project_root=tmp_path,
            source_root=source_root,
            predecessor_private_acceptance=acceptance,
        )


def test_preflight_clone_report_rejects_destination_rebinding() -> None:
    source, clone = _frozen_source_and_clone()
    destination_locator = (
        ".local/product-v02321/product-state/"
        f"preflight-{source.source_sha256[:24]}/product"
    )
    rebound = _rebind_clone_destination(clone, destination_locator)
    destination_state = _rebind_source_locator(source, destination_locator)
    report = PreflightStateCloneReportV02321.build(
        source_repository_binding=expected_source_repository_binding_v0232(),
        predecessor_private_acceptance={"terminal": "PRIVATE_HISTORY_VERIFIED"},
        source_state=source.model_dump(mode="json"),
        clone=rebound.model_dump(mode="json"),
        destination_state=destination_state.model_dump(mode="json"),
        destination_locator=destination_locator,
        source_incident_count=1,
        source_diagnosis_count=1,
        fault_family_count=0,
        knowledge_artifact_count=0,
    )
    body = report.model_dump(mode="json", exclude={"report_sha256"})
    body["destination_locator"] = (
        ".local/product-v02321/product-state/preflight-"
        + "f" * 24
        + "/product"
    )

    with pytest.raises(ValueError, match="clone binding differs"):
        PreflightStateCloneReportV02321.model_validate(
            {**body, "report_sha256": semantic_sha256_v22(body)}
        )


def test_live_preflight_pass_requires_exact_10_of_10_clean_evidence() -> None:
    attempt = _successful_live_attempt()
    report = LiveTrafficPreflightPassV02321.build(
        attempt=attempt,
        frozen_traffic_contract_sha256=attempt.traffic_contract_sha256,
        frozen_preflight_profile_sha256=attempt.traffic_profile_sha256,
        frozen_formal_profile_sha256=attempt.formal_profile_sha256,
        typed_request_plan_schema_sha256="e" * 64,
        closure_contract_schema_sha256="f" * 64,
        live_traffic_preflight_attempt_count=1,
        infrastructure_session_count=1,
        traffic_attempt_count=1,
        formal_healthy_traffic_execution_count=0,
        accepted_successor_incident_count=0,
        successor_diagnosis_count=0,
        action_authority="NONE",
    )

    assert attempt.terminal == TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V02321
    assert attempt.traffic_execution.run.completed_transactions == 10
    assert attempt.traffic_execution.run.successful_transactions == 10
    assert attempt.traffic_execution.run.transport_retry_count == 0
    assert report.terminal == TRAFFIC_PREFLIGHT_PASS_V02321
    assert report.formal_healthy_traffic_execution_count == 0
    assert report.accepted_successor_incident_count == 0
    assert report.successor_diagnosis_count == 0
    assert report.action_authority == "NONE"


def test_live_preflight_attempt_rejects_source_or_clone_report_drift() -> None:
    attempt = _successful_live_attempt()
    payload = attempt.model_dump(
        mode="json", exclude={"terminal", "attempt_sha256"}
    )
    payload["source_state_after_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="evidence binding differs"):
        LiveTrafficPreflightAttemptV02321.build(**payload)

    payload = attempt.model_dump(
        mode="json", exclude={"terminal", "attempt_sha256"}
    )
    payload["product_state_after_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="evidence binding differs"):
        LiveTrafficPreflightAttemptV02321.build(**payload)


def test_live_preflight_rejects_resealed_ledger_or_profile_rebinding() -> None:
    attempt = _successful_live_attempt()
    payload = attempt.model_dump(
        mode="json", exclude={"terminal", "attempt_sha256"}
    )
    payload["traffic_profile_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="evidence binding differs"):
        LiveTrafficPreflightAttemptV02321.build(**payload)

    report_payload = {
        "attempt": attempt,
        "frozen_traffic_contract_sha256": attempt.traffic_contract_sha256,
        "frozen_preflight_profile_sha256": attempt.traffic_profile_sha256,
        "frozen_formal_profile_sha256": "0" * 64,
        "typed_request_plan_schema_sha256": "e" * 64,
        "closure_contract_schema_sha256": "f" * 64,
        "live_traffic_preflight_attempt_count": 1,
        "infrastructure_session_count": 1,
        "traffic_attempt_count": 1,
        "formal_healthy_traffic_execution_count": 0,
        "accepted_successor_incident_count": 0,
        "successor_diagnosis_count": 0,
        "action_authority": "NONE",
    }
    with pytest.raises(ValueError, match="PASS binding differs"):
        LiveTrafficPreflightPassV02321.build(**report_payload)


def test_publication_bundle_recovers_without_replaying_traffic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = _successful_live_attempt()
    ledger = attempt.ledger
    artifacts = [
        {
            "path": "docs/analysis/product-v02321-traffic-preflight-attempt-1.json",
            "mode": "CREATE_EXACT",
            "payload": attempt.model_dump(mode="json"),
        },
        {
            "path": "docs/analysis/product-v02321-traffic-preflight-ledger.json",
            "mode": "REPLACE",
            "payload": ledger.model_dump(mode="json"),
        },
        {
            "path": "docs/analysis/product-v02321-progress.json",
            "mode": "REPLACE",
            "payload": {"terminal": "TEST_PASS"},
        },
    ]
    bundle = run_traffic_preflight._publication_bundle(
        attempt_label="publication-recovery",
        terminal=TRAFFIC_PREFLIGHT_PASS_V02321,
        ledger_tail=[event.model_dump(mode="json") for event in ledger.events],
        artifacts=artifacts,
        attempt=attempt.model_dump(mode="json"),
    )
    exact_create = run_traffic_preflight._write_public_exact_or_create
    injected = False

    def fail_after_first_exact_create(path: Path, payload: object) -> None:
        nonlocal injected
        assert isinstance(payload, dict)
        exact_create(path, payload)
        if not injected:
            injected = True
            raise OSError("injected post-create publication failure")

    monkeypatch.setattr(
        run_traffic_preflight,
        "_write_public_exact_or_create",
        fail_after_first_exact_create,
    )
    with pytest.raises(OSError, match="post-create publication"):
        run_traffic_preflight._publish_publication_bundle(tmp_path, bundle)

    monkeypatch.setattr(
        run_traffic_preflight,
        "_write_public_exact_or_create",
        exact_create,
    )
    recovered = run_traffic_preflight._publish_publication_bundle(
        tmp_path, bundle
    )
    assert recovered == attempt
    assert run_traffic_preflight._load_private_ledger(tmp_path) == ledger
    assert json.loads(
        (
            tmp_path
            / "docs/analysis/product-v02321-traffic-preflight-attempt-1.json"
        ).read_text(encoding="utf-8")
    ) == attempt.model_dump(mode="json")


def test_pre_session_failure_requires_changed_source_before_new_reservation(
    tmp_path: Path,
) -> None:
    for relative in (
        "scripts/product_v02321/run_traffic_preflight.py",
        "src/ecomsre/product/pilot/traffic_preflight_live_v02321.py",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{relative}\n", encoding="utf-8")

    first = run_traffic_preflight._prepare_pre_session_start(
        tmp_path,
        attempt_label="pre-session-1",
        prior_attempt=None,
        changed_surface=None,
        changed_source_paths=(),
        repair_rationale=None,
    )
    first_root = (
        tmp_path / ".local/product-v02321/traffic-preflight/pre-session-1"
    )
    first_root.mkdir(parents=True)
    (first_root / "pre-session-start.json").write_text(
        json.dumps(first), encoding="utf-8"
    )
    run_traffic_preflight._write_pre_session_blocker(
        first_root,
        reservation=first,
        error=ValueError("injected request-plan construction failure"),
    )
    blocker = first_root / "pre-session-blocker.json"
    changed_paths = (
        "scripts/product_v02321/run_traffic_preflight.py",
        "src/ecomsre/product/pilot/traffic_preflight_live_v02321.py",
    )

    with pytest.raises(ValueError, match="identical pre-session replay"):
        run_traffic_preflight._prepare_pre_session_start(
            tmp_path,
            attempt_label="pre-session-2",
            prior_attempt=blocker,
            changed_surface="REQUEST_PLAN_CONSTRUCTION",
            changed_source_paths=changed_paths,
            repair_rationale="repair the injected construction failure",
        )

    changed = tmp_path / changed_paths[0]
    changed.write_text("changed implementation\n", encoding="utf-8")
    second = run_traffic_preflight._prepare_pre_session_start(
        tmp_path,
        attempt_label="pre-session-2",
        prior_attempt=blocker,
        changed_surface="REQUEST_PLAN_CONSTRUCTION",
        changed_source_paths=changed_paths,
        repair_rationale="repair the injected construction failure",
    )
    assert second["reservation_ordinal"] == 2
    assert second["prior_pre_session_sha256"] is not None
    assert (
        second["changed_implementation_sha256"]
        != first["changed_implementation_sha256"]
    )


def test_pre_session_history_advances_one_append_only_blocker_frontier(
    tmp_path: Path,
) -> None:
    changed_paths = (
        "scripts/product_v02321/run_traffic_preflight.py",
        "src/ecomsre/product/pilot/traffic_preflight_live_v02321.py",
    )
    for relative in changed_paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"initial {relative}\n", encoding="utf-8")

    prior_blocker: Path | None = None
    for ordinal, label in enumerate(("pre-session-1", "pre-session-2"), start=1):
        reservation = run_traffic_preflight._prepare_pre_session_start(
            tmp_path,
            attempt_label=label,
            prior_attempt=prior_blocker,
            changed_surface=None if ordinal == 1 else "PRE_SESSION_REPAIR",
            changed_source_paths=() if ordinal == 1 else changed_paths,
            repair_rationale=None if ordinal == 1 else f"repair {ordinal}",
        )
        attempt_root = tmp_path / ".local/product-v02321/traffic-preflight" / label
        attempt_root.mkdir(parents=True)
        (attempt_root / "pre-session-start.json").write_text(
            json.dumps(reservation), encoding="utf-8"
        )
        run_traffic_preflight._write_pre_session_blocker(
            attempt_root,
            reservation=reservation,
            error=RuntimeError(f"injected failure {ordinal}"),
        )
        prior_blocker = attempt_root / "pre-session-blocker.json"
        (tmp_path / changed_paths[0]).write_text(
            f"changed implementation {ordinal}\n", encoding="utf-8"
        )

    assert prior_blocker is not None
    third = run_traffic_preflight._prepare_pre_session_start(
        tmp_path,
        attempt_label="pre-session-3",
        prior_attempt=prior_blocker,
        changed_surface="PRESERVED_RUNTIME_ROOT_BINDING",
        changed_source_paths=changed_paths,
        repair_rationale="bind the preserved Runtime root",
    )

    latest_blocker = json.loads(prior_blocker.read_text(encoding="utf-8"))
    assert third["reservation_ordinal"] == 3
    assert third["prior_pre_session_sha256"] == latest_blocker["blocker_sha256"]


def test_pre_session_history_rejects_relative_prior_evidence_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changed_paths = (
        "scripts/product_v02321/run_traffic_preflight.py",
        "src/ecomsre/product/pilot/traffic_preflight_live_v02321.py",
    )
    for relative in changed_paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"initial {relative}\n", encoding="utf-8")
    first = run_traffic_preflight._prepare_pre_session_start(
        tmp_path,
        attempt_label="pre-session-1",
        prior_attempt=None,
        changed_surface=None,
        changed_source_paths=(),
        repair_rationale=None,
    )
    first_root = tmp_path / ".local/product-v02321/traffic-preflight/pre-session-1"
    first_root.mkdir(parents=True)
    (first_root / "pre-session-start.json").write_text(
        json.dumps(first), encoding="utf-8"
    )
    run_traffic_preflight._write_pre_session_blocker(
        first_root,
        reservation=first,
        error=RuntimeError("injected first failure"),
    )
    blocker = first_root / "pre-session-blocker.json"
    (tmp_path / changed_paths[0]).write_text(
        "changed implementation\n", encoding="utf-8"
    )
    second = run_traffic_preflight._prepare_pre_session_start(
        tmp_path,
        attempt_label="pre-session-2",
        prior_attempt=blocker,
        changed_surface="PRE_SESSION_REPAIR",
        changed_source_paths=changed_paths,
        repair_rationale="repair the first failure",
    )
    second_body = {
        **second,
        "prior_evidence_path": (
            ".local/product-v02321/traffic-preflight/"
            "pre-session-1/pre-session-blocker.json"
        ),
    }
    second_body.pop("reservation_sha256")
    second = {
        **second_body,
        "reservation_sha256": semantic_sha256_v22(second_body),
    }
    second_root = tmp_path / ".local/product-v02321/traffic-preflight/pre-session-2"
    second_root.mkdir()
    (second_root / "pre-session-start.json").write_text(
        json.dumps(second), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="pre-session history differs"):
        run_traffic_preflight._incomplete_pre_session(tmp_path)


def test_closed_session_only_retry_also_rejects_identical_implementation(
    tmp_path: Path,
) -> None:
    changed_paths = (
        "scripts/product_v02321/run_traffic_preflight.py",
        "src/ecomsre/product/pilot/traffic_preflight_live_v02321.py",
    )
    for relative in changed_paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{relative}\n", encoding="utf-8")
    first = run_traffic_preflight._prepare_pre_session_start(
        tmp_path,
        attempt_label="closed-session-1",
        prior_attempt=None,
        changed_surface=None,
        changed_source_paths=(),
        repair_rationale=None,
    )
    first_root = (
        tmp_path / ".local/product-v02321/traffic-preflight/closed-session-1"
    )
    first_root.mkdir(parents=True)
    (first_root / "pre-session-start.json").write_text(
        json.dumps(first), encoding="utf-8"
    )
    run_traffic_preflight._write_checkpoint(
        first_root / "pre-session-completion.json",
        schema_version="ecomsre.product.pre-session-completion.v02321",
        digest_field="completion_sha256",
        payload={
            "reservation_sha256": first["reservation_sha256"],
            "session_id": "session-" + "1" * 32,
        },
    )
    scenario = next(
        item
        for item in build_increment2_closure_contract_v02321(ROOT).scenarios
        if item.scenario_id == "SANDBOX_START_FAILURE"
    )
    assert scenario.ledger.traffic_attempt_count == 0
    for event in scenario.ledger.events:
        run_traffic_preflight.append_traffic_preflight_event_file_v02321(
            tmp_path, "campaign", event
        )
    prior = tmp_path / "prior-session-blocker.json"
    prior.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="identical pre-session replay"):
        run_traffic_preflight._prepare_pre_session_start(
            tmp_path,
            attempt_label="closed-session-2",
            prior_attempt=prior,
            changed_surface="SANDBOX_START",
            changed_source_paths=changed_paths,
            repair_rationale="repair the prior session-only failure",
        )


def test_open_attempt_recovery_closes_ledger_without_transport_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = _successful_live_attempt()
    session = attempt.ledger.events[0]
    attempt_start = attempt.ledger.events[1]
    assert isinstance(session, InfrastructureSessionStartV02321)
    assert isinstance(attempt_start, TrafficPreflightAttemptStartV02321)
    for event in (session, attempt_start):
        run_traffic_preflight.append_traffic_preflight_event_file_v02321(
            tmp_path, "campaign", event
        )

    private_root = tmp_path / ".local/product-v02321/traffic-preflight/recover-1"
    private_root.mkdir(parents=True)
    reservation = run_traffic_preflight._write_checkpoint(
        private_root / "pre-session-start.json",
        schema_version="ecomsre.product.pre-session-start.v02321",
        digest_field="reservation_sha256",
        payload=_pre_session_reservation_payload(attempt_label="recover-1"),
    )
    (private_root / "typed-request-plan.json").write_text("{}", encoding="utf-8")
    run_traffic_preflight._write_checkpoint(
        private_root / "traffic-execution.json",
        schema_version="ecomsre.product.traffic-execution-checkpoint.v02321",
        digest_field="checkpoint_sha256",
        payload={
            "attempt_id": attempt_start.attempt_id,
            "attempt_start_sha256": attempt_start.event_sha256,
            "traffic_execution": attempt.traffic_execution.model_dump(mode="json"),
        },
    )
    clone_report = attempt.product_state_clone_report
    (tmp_path / "docs/analysis").mkdir(parents=True)
    (tmp_path / "config/product-v0231").mkdir(parents=True)
    for relative in (
        "config/product-v0231/historical-results.v1.json",
        "docs/analysis/product-v0231-baseline-continuation-context.json",
        "docs/analysis/product-v0231-runtime-authority-descriptor.json",
        "docs/analysis/product-v02321-progress.json",
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())
    (
        tmp_path / "docs/analysis/product-v02321-product-state-clone-preflight.json"
    ).write_text(clone_report.model_dump_json(), encoding="utf-8")
    (tmp_path / "docs/analysis/product-v0232-predecessor-audit.json").write_text(
        json.dumps({"source_state": clone_report.source_state.model_dump(mode="json")}),
        encoding="utf-8",
    )
    predecessor_root, source_root = _preserved_runtime_fixture(tmp_path)
    product_root = tmp_path / "product"
    product_root.mkdir()

    class FakeProcesses:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def cleanup_observation(self) -> dict[str, object]:
            return {
                "verdict": "CLEAN",
                "owned_host_processes": 0,
                "product_api_port_available": True,
                "non_owned_resources_changed": False,
                "launches": (),
            }

    class FakeLifecycle:
        def __init__(self, **_kwargs: object) -> None:
            self.flag_file = tmp_path / "flag.json"
            self.runtime_descriptor = None

        def recover_cleanup_owned(self, **_kwargs: object) -> CleanupResult:
            return CleanupResult(
                baseline_restored=False,
                owned_containers=0,
                owned_networks=0,
                owned_volumes=0,
                non_owned_resources_changed=False,
                verdict="BLOCKED",
            )

    monkeypatch.setattr(run_traffic_preflight, "verify_product_v02321_history", lambda _r: {})
    monkeypatch.setattr(
        run_traffic_preflight.TrafficHarnessTypedRequestPlanV02321,
        "model_validate_json",
        classmethod(
            lambda _cls, _payload: SimpleNamespace(
                plan_sha256=session.request_plan_sha256,
                state_clone_sha256=session.state_clone_sha256,
            )
        ),
    )
    monkeypatch.setattr(run_traffic_preflight, "_verify_profile_binding", lambda *_a, **_k: "a" * 64)
    monkeypatch.setattr(run_traffic_preflight, "_admit_source_state", lambda _r: clone_report.source_state)
    monkeypatch.setattr(run_traffic_preflight, "_admit_clone_state", lambda *_a: (clone_report.destination_state, product_root))
    monkeypatch.setattr(run_traffic_preflight, "_database_owner_count", lambda _p: 0)
    monkeypatch.setattr(run_traffic_preflight, "_ProductHostProcessesV023", FakeProcesses)
    monkeypatch.setattr(run_traffic_preflight, "load_traffic_profile_v0232", lambda *_a, **_k: SimpleNamespace(queue_fault_flag=0))
    monkeypatch.setattr(run_traffic_preflight, "load_bundle", lambda _p: object())
    monkeypatch.setattr(run_traffic_preflight, "load_preserved_runtime_inputs_v0231", lambda **_k: (object(), {}))
    monkeypatch.setattr(run_traffic_preflight, "AuthorityContinuousSandboxLifecycleV0231", FakeLifecycle)
    monkeypatch.setattr(
        run_traffic_preflight,
        "verify_queue_default_v021",
        lambda *_a, **_k: SimpleNamespace(
            after_sha256=attempt_start.queue_before_sha256
        ),
    )

    run_traffic_preflight._recover_interrupted_traffic_preflight_v02321(
        root=tmp_path,
        predecessor_root=predecessor_root,
        source_product_root=source_root,
        attempt_label="recover-1",
        private_root=private_root,
    )

    recovered = run_traffic_preflight._load_private_ledger(tmp_path)
    assert recovered.infrastructure_session_count == 1
    assert recovered.traffic_attempt_count == 1
    assert isinstance(recovered.events[-1], InfrastructureSessionCompletionV02321)
    pre_session_completion = json.loads(
        (private_root / "pre-session-completion.json").read_text(encoding="utf-8")
    )
    assert pre_session_completion["attempt_label"] == "recover-1"
    assert pre_session_completion["reservation_sha256"] == reservation[
        "reservation_sha256"
    ]
    assert pre_session_completion["infrastructure_session_count_after"] == 1
    assert pre_session_completion["traffic_attempt_count_before"] == 0
    assert (
        json.loads(
            (
                tmp_path
                / "docs/analysis/product-v02321-traffic-preflight-attempt-1.json"
            ).read_text(encoding="utf-8")
        )["terminal"]
        == "BLOCKED_ECOMSRE_PRODUCT_V02321_INTERRUPTED_SESSION"
    )


@pytest.mark.parametrize(
    ("reservation_ordinal", "traffic_attempt_count_before"),
    ((999, 0), (1, 999)),
)
def test_open_attempt_recovery_rejects_reservation_sequence_or_counter_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reservation_ordinal: int,
    traffic_attempt_count_before: int,
) -> None:
    attempt = _successful_live_attempt()
    session = attempt.ledger.events[0]
    attempt_start = attempt.ledger.events[1]
    assert isinstance(session, InfrastructureSessionStartV02321)
    assert isinstance(attempt_start, TrafficPreflightAttemptStartV02321)
    for event in (session, attempt_start):
        run_traffic_preflight.append_traffic_preflight_event_file_v02321(
            tmp_path, "campaign", event
        )

    private_root = tmp_path / ".local/product-v02321/traffic-preflight/recover-1"
    private_root.mkdir(parents=True)
    run_traffic_preflight._write_checkpoint(
        private_root / "pre-session-start.json",
        schema_version="ecomsre.product.pre-session-start.v02321",
        digest_field="reservation_sha256",
        payload=_pre_session_reservation_payload(
            attempt_label="recover-1",
            reservation_ordinal=reservation_ordinal,
            traffic_attempt_count_before=traffic_attempt_count_before,
        ),
    )
    (private_root / "typed-request-plan.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        run_traffic_preflight, "verify_product_v02321_history", lambda _r: {}
    )
    monkeypatch.setattr(
        run_traffic_preflight,
        "_verify_profile_binding",
        lambda *_a, **_k: "a" * 64,
    )
    predecessor_root, source_root = _preserved_runtime_fixture(tmp_path)

    with pytest.raises(ValueError, match="recovery reservation differs"):
        run_traffic_preflight._recover_interrupted_traffic_preflight_v02321(
            root=tmp_path,
            predecessor_root=predecessor_root,
            source_product_root=source_root,
            attempt_label="recover-1",
            private_root=private_root,
        )


def test_open_attempt_recovery_rejects_cross_root_runtime_source_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = _successful_live_attempt()
    session = attempt.ledger.events[0]
    attempt_start = attempt.ledger.events[1]
    assert isinstance(session, InfrastructureSessionStartV02321)
    assert isinstance(attempt_start, TrafficPreflightAttemptStartV02321)
    for event in (session, attempt_start):
        run_traffic_preflight.append_traffic_preflight_event_file_v02321(
            tmp_path, "campaign", event
        )
    private_root = tmp_path / ".local/product-v02321/traffic-preflight/recover-1"
    private_root.mkdir(parents=True)
    run_traffic_preflight._write_checkpoint(
        private_root / "pre-session-start.json",
        schema_version="ecomsre.product.pre-session-start.v02321",
        digest_field="reservation_sha256",
        payload=_pre_session_reservation_payload(attempt_label="recover-1"),
    )
    (private_root / "typed-request-plan.json").write_text("{}", encoding="utf-8")
    preserved_root = tmp_path / "preserved"
    source_root = preserved_root / run_traffic_preflight.SOURCE_LOCATOR_V0232
    source_root.mkdir(parents=True)
    unrelated_runtime_root = tmp_path / "unrelated-runtime"
    unrelated_runtime_root.mkdir()
    monkeypatch.setattr(
        run_traffic_preflight, "verify_product_v02321_history", lambda _r: {}
    )
    monkeypatch.setattr(
        run_traffic_preflight,
        "_verify_profile_binding",
        lambda *_a, **_k: "a" * 64,
    )

    with pytest.raises(ValueError, match="preserved Runtime root differs"):
        run_traffic_preflight._recover_interrupted_traffic_preflight_v02321(
            root=tmp_path,
            predecessor_root=unrelated_runtime_root,
            source_product_root=source_root,
            attempt_label="recover-1",
            private_root=private_root,
        )


def test_same_label_recovers_session_start_before_completion_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_root = tmp_path / ".local/product-v02321/traffic-preflight/session-only"
    private_root.mkdir(parents=True)
    (private_root / "pre-session-start.json").write_text("{}", encoding="utf-8")
    (private_root / "typed-request-plan.json").write_text("{}", encoding="utf-8")
    recovered: list[str] = []

    def recover(**payload: object) -> None:
        recovered.append(str(payload["attempt_label"]))
        (private_root / "publication-bundle.json").write_text(
            "{}", encoding="utf-8"
        )

    monkeypatch.setattr(
        run_traffic_preflight,
        "_recover_interrupted_traffic_preflight_v02321",
        recover,
    )
    monkeypatch.setattr(
        run_traffic_preflight,
        "_publish_publication_bundle",
        lambda _root, _publication: None,
    )

    with pytest.raises(
        RuntimeError, match="BLOCKED_ECOMSRE_PRODUCT_V02321_TRAFFIC_PREFLIGHT"
    ):
        run_traffic_preflight.run_traffic_preflight_v02321(
            project_root=tmp_path,
            predecessor_root=tmp_path / "missing-predecessor",
            source_product_root=tmp_path / "missing-source",
            attempt_label="session-only",
        )
    assert recovered == ["session-only"]
    assert not (private_root / "pre-session-completion.json").exists()


def test_reserved_runner_admits_the_existing_create_once_attempt_root(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / ".local/product-v02321/traffic-preflight/attempt-1"
    private_root.mkdir(parents=True, mode=0o700)

    run_traffic_preflight._require_reserved_private_root(private_root)

    assert private_root.is_dir()


def test_preserved_runtime_root_must_own_the_fixed_source_locator(
    tmp_path: Path,
) -> None:
    preserved_root = tmp_path / "preserved"
    source_root = preserved_root / run_traffic_preflight.SOURCE_LOCATOR_V0232
    source_root.mkdir(parents=True)
    unrelated_root = tmp_path / "unrelated"
    unrelated_root.mkdir()

    run_traffic_preflight._require_preserved_runtime_root_v02321(
        preserved_root, source_root
    )
    with pytest.raises(ValueError, match="preserved Runtime root differs"):
        run_traffic_preflight._require_preserved_runtime_root_v02321(
            unrelated_root, source_root
        )
