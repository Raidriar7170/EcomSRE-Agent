from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.traffic_harness_closure_v02321 import (
    InfrastructureSessionStartV02321,
    TrafficHarnessClosureV02321,
    TrafficHarnessStageV02321,
    TrafficPreflightAttemptCompletionV02321,
    TrafficPreflightAttemptStartV02321,
    TrafficPreflightLedgerV02321,
    append_traffic_preflight_event_file_v02321,
    invoke_first_cart_transport_v02321,
    request_sandbox_start_v02321,
)
from scripts.product_v02321.run_harness_contract_preflight import (
    PREFLIGHT_CLOSURE_CONTRACT_PASS_V02321,
    build_increment2_artifacts_v02321,
    build_increment2_closure_contract_v02321,
    run_harness_contract_preflight_v02321,
)


ROOT = Path(__file__).resolve().parents[2]


def _scenarios():
    contract = build_increment2_closure_contract_v02321(ROOT)
    return contract, {item.scenario_id: item for item in contract.scenarios}


def _rebuild_closure(
    closure: TrafficHarnessClosureV02321,
    **changes: object,
) -> TrafficHarnessClosureV02321:
    body = closure.model_dump(mode="json", exclude={"closure_sha256"})
    body.update(changes)
    return TrafficHarnessClosureV02321.model_validate(
        {**body, "closure_sha256": semantic_sha256_v22(body)}
    )


def test_failure_injection_scenarios_separate_sessions_from_attempts() -> None:
    contract, scenarios = _scenarios()

    assert contract.terminal == PREFLIGHT_CLOSURE_CONTRACT_PASS_V02321
    assert tuple(scenarios) == (
        "REQUEST_PLAN_CONSTRUCTION_FAILURE",
        "SANDBOX_START_FAILURE",
        "RUNTIME_INSPECT_FAILURE",
        "FIRST_CART_SEND_FAILURE",
    )

    request_plan = scenarios["REQUEST_PLAN_CONSTRUCTION_FAILURE"]
    assert request_plan.ledger.infrastructure_session_count == 0
    assert request_plan.ledger.traffic_attempt_count == 0
    assert request_plan.closure.session_id is None
    assert request_plan.closure.attempt_id is None
    assert request_plan.closure.closure_terminal == "BLOCKED_PRESTATE_UNAVAILABLE"

    sandbox_start = scenarios["SANDBOX_START_FAILURE"]
    assert sandbox_start.ledger.infrastructure_session_count == 1
    assert sandbox_start.ledger.traffic_attempt_count == 0
    assert sandbox_start.closure.session_id is not None
    assert sandbox_start.closure.attempt_id is None
    assert sandbox_start.closure.closure_terminal == "BLOCKED_PRESTATE_UNAVAILABLE"

    runtime_inspect = scenarios["RUNTIME_INSPECT_FAILURE"]
    assert runtime_inspect.ledger.infrastructure_session_count == 1
    assert runtime_inspect.ledger.traffic_attempt_count == 0
    assert runtime_inspect.closure.closure_terminal == "CLEAN_PRE_TRAFFIC"
    assert runtime_inspect.closure.queue_before_sha256 == (
        runtime_inspect.closure.queue_after_sha256
    )
    assert runtime_inspect.closure.outer_baseline_before_sha256 == (
        runtime_inspect.closure.outer_baseline_after_sha256
    )

    first_cart = scenarios["FIRST_CART_SEND_FAILURE"]
    assert first_cart.ledger.infrastructure_session_count == 1
    assert first_cart.ledger.traffic_attempt_count == 1
    assert first_cart.closure.attempt_id is not None
    assert first_cart.closure.closure_terminal == "CLEAN_POST_TRAFFIC"
    completion = next(
        item
        for item in first_cart.ledger.events
        if isinstance(item, TrafficPreflightAttemptCompletionV02321)
    )
    assert completion.traffic_execution_sha256 is None
    assert completion.traffic_dispatch_failure is not None
    assert completion.traffic_dispatch_failure.remote_delivery == "UNKNOWN"
    assert first_cart.execution_trace == first_cart.closure.observed_stage_sequence
    stages = first_cart.closure.observed_stage_sequence
    assert stages.index(TrafficHarnessStageV02321.TRAFFIC_ATTEMPT_CONSUMED) + 1 == (
        stages.index(TrafficHarnessStageV02321.FIRST_CART_SEND_REQUESTED)
    )


def test_every_contract_fixture_is_self_sealed_and_frozen() -> None:
    contract, scenarios = _scenarios()

    assert type(contract).model_validate_json(contract.model_dump_json()) == contract
    for scenario in scenarios.values():
        assert (
            TrafficPreflightLedgerV02321.model_validate_json(
                scenario.ledger.model_dump_json()
            )
            == scenario.ledger
        )
        assert (
            TrafficHarnessClosureV02321.model_validate_json(
                scenario.closure.model_dump_json()
            )
            == scenario.closure
        )
    with pytest.raises(ValidationError, match="frozen"):
        contract.terminal = "BLOCKED"  # type: ignore[misc]


def test_clean_closure_requires_observable_equal_prestate() -> None:
    _, scenarios = _scenarios()
    request_plan = scenarios["REQUEST_PLAN_CONSTRUCTION_FAILURE"].closure

    with pytest.raises(ValueError, match="pre-state"):
        _rebuild_closure(request_plan, closure_terminal="CLEAN_PRE_TRAFFIC")


def test_queue_and_baseline_drift_cannot_be_resealed_as_clean() -> None:
    _, scenarios = _scenarios()
    closure = scenarios["RUNTIME_INSPECT_FAILURE"].closure

    with pytest.raises(ValueError, match="queue"):
        _rebuild_closure(closure, queue_after_sha256="a" * 64)
    with pytest.raises(ValueError, match="Baseline"):
        _rebuild_closure(closure, outer_baseline_after_sha256="b" * 64)


def test_owned_or_non_owned_resource_change_blocks_clean_closure() -> None:
    _, scenarios = _scenarios()
    closure = scenarios["RUNTIME_INSPECT_FAILURE"].closure
    counts = closure.owned_resource_counts.model_dump(mode="json")

    with pytest.raises(ValueError, match="resource cleanup"):
        _rebuild_closure(
            closure,
            owned_resource_counts={**counts, "containers": 1},
        )
    with pytest.raises(ValueError, match="resource cleanup"):
        _rebuild_closure(closure, non_owned_resources_changed=True)


def test_cleanup_unknown_is_typed_and_cannot_be_promoted_to_clean() -> None:
    _, scenarios = _scenarios()
    closure = scenarios["REQUEST_PLAN_CONSTRUCTION_FAILURE"].closure
    blocked = _rebuild_closure(
        closure,
        product_cleanup={
            "observation_complete": False,
            "verdict": "BLOCKED",
            "owned_host_processes": None,
            "database_owner_count_before": None,
            "database_owner_count_after": None,
            "product_api_port_available": None,
            "non_owned_resources_changed": None,
            "safe_error_code": "PRODUCT_CLEANUP_OBSERVATION_UNAVAILABLE",
        },
        owned_resource_counts={
            "containers": 0,
            "networks": 0,
            "volumes": 0,
            "host_processes": None,
        },
        non_owned_resources_changed=None,
        failure_stage=TrafficHarnessStageV02321.CLEANUP_COMPLETE,
        safe_error_code="PRODUCT_CLEANUP_OBSERVATION_UNAVAILABLE",
        closure_terminal="BLOCKED_RESOURCE_CLEANUP",
    )

    assert blocked.closure_terminal == "BLOCKED_RESOURCE_CLEANUP"
    assert blocked.owned_resource_counts.host_processes is None


def test_clean_post_traffic_success_has_no_fake_failure() -> None:
    _, scenarios = _scenarios()
    closure = scenarios["FIRST_CART_SEND_FAILURE"].closure
    stages = list(closure.observed_stage_sequence)
    stages.insert(
        stages.index(TrafficHarnessStageV02321.QUEUE_POSTSTATE_CAPTURED),
        TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE,
    )
    success = _rebuild_closure(
        closure,
        stage_reached=TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE,
        observed_stage_sequence=stages,
        traffic_execution_sha256="d" * 64,
        traffic_dispatch_failure_sha256=None,
        failure_stage=None,
        safe_error_code=None,
    )

    assert success.closure_terminal == "CLEAN_POST_TRAFFIC"
    assert success.failure_stage is None
    assert success.safe_error_code is None


def test_attempt_start_requires_every_admission_gate() -> None:
    _, scenarios = _scenarios()
    ledger = scenarios["FIRST_CART_SEND_FAILURE"].ledger
    start = next(
        item
        for item in ledger.events
        if isinstance(item, TrafficPreflightAttemptStartV02321)
    )
    body = start.model_dump(mode="json", exclude={"event_sha256"})
    body["checkout_healthy"] = False

    with pytest.raises(ValueError, match="attempt admission gates"):
        TrafficPreflightAttemptStartV02321.model_validate(
            {**body, "event_sha256": semantic_sha256_v22(body)}
        )


def test_successor_attempt_cannot_be_an_identical_rerun() -> None:
    _, scenarios = _scenarios()
    start = next(
        item
        for item in scenarios["FIRST_CART_SEND_FAILURE"].ledger.events
        if isinstance(item, TrafficPreflightAttemptStartV02321)
    )
    body = start.model_dump(mode="json", exclude={"event_sha256"})
    body.update(attempt_ordinal=2, traffic_attempt_count_after=2)

    with pytest.raises(ValueError, match="changed-surface evidence"):
        TrafficPreflightAttemptStartV02321.model_validate(
            {**body, "event_sha256": semantic_sha256_v22(body)}
        )


def test_attempt_changed_surface_is_derived_from_file_bindings() -> None:
    _, scenarios = _scenarios()
    start = next(
        item
        for item in scenarios["FIRST_CART_SEND_FAILURE"].ledger.events
        if isinstance(item, TrafficPreflightAttemptStartV02321)
    )
    body = start.model_dump(mode="json", exclude={"event_sha256"})
    body["changed_implementation_sha256"] = "f" * 64

    for binding in start.changed_source_bindings:
        source = ROOT / binding.path
        content = source.read_bytes()
        assert binding.sha256 == hashlib.sha256(content).hexdigest()
        assert binding.size_bytes == len(content)

    with pytest.raises(ValueError, match="changed implementation binding"):
        TrafficPreflightAttemptStartV02321.model_validate(
            {**body, "event_sha256": semantic_sha256_v22(body)}
        )


def test_ledger_cross_binds_closure_to_session_and_attempt() -> None:
    _, scenarios = _scenarios()
    ledger = scenarios["FIRST_CART_SEND_FAILURE"].ledger
    closure_index = next(
        index
        for index, item in enumerate(ledger.events)
        if isinstance(item, TrafficHarnessClosureV02321)
    )
    closure = ledger.events[closure_index]
    assert isinstance(closure, TrafficHarnessClosureV02321)

    wrong_request = _rebuild_closure(closure, request_plan_sha256="f" * 64)
    with pytest.raises(ValueError, match="closure session binding"):
        TrafficPreflightLedgerV02321.build(
            events=(*ledger.events[:closure_index], wrong_request)
        )

    wrong_dispatch = _rebuild_closure(
        closure,
        traffic_dispatch_failure_sha256="e" * 64,
    )
    with pytest.raises(ValueError, match="closure attempt binding"):
        TrafficPreflightLedgerV02321.build(
            events=(*ledger.events[:closure_index], wrong_dispatch)
        )


def test_ledger_rejects_missing_observed_runtime_request_binding() -> None:
    _, scenarios = _scenarios()
    ledger = scenarios["RUNTIME_INSPECT_FAILURE"].ledger
    closure_index = next(
        index
        for index, item in enumerate(ledger.events)
        if isinstance(item, TrafficHarnessClosureV02321)
    )
    closure = ledger.events[closure_index]
    assert isinstance(closure, TrafficHarnessClosureV02321)
    missing_runtime_request = _rebuild_closure(
        closure,
        runtime_inspect_request_sha256=None,
    )

    with pytest.raises(ValueError, match="closure session binding"):
        TrafficPreflightLedgerV02321.build(
            events=(*ledger.events[:closure_index], missing_runtime_request)
        )


def test_ledger_cross_binds_attempt_runtime_authority_to_session() -> None:
    _, scenarios = _scenarios()
    ledger = scenarios["FIRST_CART_SEND_FAILURE"].ledger
    session = ledger.events[0]
    start = next(
        item
        for item in ledger.events
        if isinstance(item, TrafficPreflightAttemptStartV02321)
    )
    body = start.model_dump(mode="json", exclude={"event_sha256"})
    body["runtime_authority_sha256"] = "f" * 64
    forged = TrafficPreflightAttemptStartV02321.model_validate(
        {**body, "event_sha256": semantic_sha256_v22(body)}
    )

    with pytest.raises(ValueError, match="traffic attempt start session binding"):
        TrafficPreflightLedgerV02321.build(events=(session, forged))


def test_passed_attempt_cannot_be_resealed_as_first_cart_failure() -> None:
    _, scenarios = _scenarios()
    ledger = scenarios["FIRST_CART_SEND_FAILURE"].ledger
    completion_index = next(
        index
        for index, item in enumerate(ledger.events)
        if isinstance(item, TrafficPreflightAttemptCompletionV02321)
    )
    original_completion = ledger.events[completion_index]
    assert isinstance(
        original_completion, TrafficPreflightAttemptCompletionV02321
    )
    completion_body = original_completion.model_dump(
        mode="json", exclude={"event_sha256"}
    )
    completion_body.update(
        traffic_execution_sha256="d" * 64,
        traffic_dispatch_failure=None,
        stage=TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE,
        completed_transactions=10,
        successful_transactions=10,
        failed_transactions=0,
        safe_error_code=None,
        terminal="ATTEMPT_PASS",
    )
    passed = TrafficPreflightAttemptCompletionV02321.model_validate(
        {
            **completion_body,
            "event_sha256": semantic_sha256_v22(completion_body),
        }
    )
    original_closure = ledger.events[completion_index + 1]
    assert isinstance(original_closure, TrafficHarnessClosureV02321)
    stages = list(original_closure.observed_stage_sequence)
    stages.insert(
        stages.index(TrafficHarnessStageV02321.QUEUE_POSTSTATE_CAPTURED),
        TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE,
    )
    forged_closure = _rebuild_closure(
        original_closure,
        prior_event_sha256=passed.event_sha256,
        observed_stage_sequence=stages,
        traffic_execution_sha256="d" * 64,
        traffic_dispatch_failure_sha256=None,
    )

    with pytest.raises(ValueError, match="closure attempt binding"):
        TrafficPreflightLedgerV02321.build(
            events=(*ledger.events[:completion_index], passed, forged_closure)
        )


def test_partial_prestate_is_preserved_as_a_typed_blocker() -> None:
    _, scenarios = _scenarios()
    closure = scenarios["SANDBOX_START_FAILURE"].closure
    partial = _rebuild_closure(
        closure,
        queue_before_sha256="a" * 64,
    )

    assert partial.closure_terminal == "BLOCKED_PRESTATE_UNAVAILABLE"
    assert partial.queue_before_sha256 == "a" * 64
    assert partial.queue_after_sha256 is None


def test_runtime_authority_blocker_requires_authority_stage_evidence() -> None:
    _, scenarios = _scenarios()
    closure = scenarios["SANDBOX_START_FAILURE"].closure

    with pytest.raises(ValueError, match="Runtime authority blocker"):
        _rebuild_closure(
            closure,
            closure_terminal="BLOCKED_RUNTIME_AUTHORITY",
        )


def test_ledger_rejects_reorder_and_duplicate_attempt_event() -> None:
    _, scenarios = _scenarios()
    ledger = scenarios["FIRST_CART_SEND_FAILURE"].ledger
    events = ledger.events
    attempt_start = next(
        item for item in events if isinstance(item, TrafficPreflightAttemptStartV02321)
    )

    with pytest.raises(ValueError, match="event chain|duplicate"):
        TrafficPreflightLedgerV02321.build(events=events + (attempt_start,))
    with pytest.raises(ValueError, match="event chain"):
        TrafficPreflightLedgerV02321.build(events=tuple(reversed(events)))


def test_event_file_persistence_is_private_create_once_and_head_bound(
    tmp_path: Path,
) -> None:
    _, scenarios = _scenarios()
    events = scenarios["SANDBOX_START_FAILURE"].ledger.events
    event = events[0]
    assert isinstance(event, InfrastructureSessionStartV02321)

    path = append_traffic_preflight_event_file_v02321(
        tmp_path, "offline-sandbox-start-failure", event
    )
    assert path == (
        tmp_path
        / ".local/product-v02321/traffic-preflight"
        / "offline-sandbox-start-failure/ledger/event-000001.json"
    )
    assert json.loads(path.read_text(encoding="utf-8")) == event.model_dump(mode="json")
    with pytest.raises(ValueError, match="append head"):
        append_traffic_preflight_event_file_v02321(
            tmp_path, "offline-sandbox-start-failure", event
        )

    second = events[1]
    append_traffic_preflight_event_file_v02321(
        tmp_path, "offline-sandbox-start-failure", second
    )
    with pytest.raises(ValueError, match="append head"):
        append_traffic_preflight_event_file_v02321(
            tmp_path, "offline-sandbox-start-failure", second
        )


def test_event_file_persistence_rejects_ordinal_two_into_empty_root(
    tmp_path: Path,
) -> None:
    _, scenarios = _scenarios()
    second = scenarios["SANDBOX_START_FAILURE"].ledger.events[1]

    with pytest.raises(ValueError, match="append head"):
        append_traffic_preflight_event_file_v02321(
            tmp_path, "empty-ledger", second
        )


def test_event_file_persistence_rejects_escape_and_symlink_root(
    tmp_path: Path,
) -> None:
    _, scenarios = _scenarios()
    first = scenarios["SANDBOX_START_FAILURE"].ledger.events[0]

    with pytest.raises(ValueError, match="attempt ID"):
        append_traffic_preflight_event_file_v02321(tmp_path, "../escape", first)

    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".local").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="ledger root"):
        append_traffic_preflight_event_file_v02321(
            tmp_path, "symlink-ledger", first
        )


def test_session_and_attempt_journals_are_persisted_before_external_io() -> None:
    _, scenarios = _scenarios()
    session_start = next(
        item
        for item in scenarios["SANDBOX_START_FAILURE"].ledger.events
        if isinstance(item, InfrastructureSessionStartV02321)
    )
    attempt_start = next(
        item
        for item in scenarios["FIRST_CART_SEND_FAILURE"].ledger.events
        if isinstance(item, TrafficPreflightAttemptStartV02321)
    )
    calls: list[str] = []

    def reject_session(_event: InfrastructureSessionStartV02321) -> None:
        calls.append("session-journal")
        raise OSError("session journal injected failure")

    with pytest.raises(OSError, match="session journal"):
        request_sandbox_start_v02321(
            session_start,
            persist_start=reject_session,
            request_start=lambda: calls.append("sandbox-start"),
        )
    assert calls == ["session-journal"]

    calls.clear()

    def reject_attempt(_event: TrafficPreflightAttemptStartV02321) -> None:
        calls.append("attempt-journal")
        raise OSError("attempt journal injected failure")

    with pytest.raises(OSError, match="attempt journal"):
        invoke_first_cart_transport_v02321(
            attempt_start,
            persist_start=reject_attempt,
            invoke_transport=lambda: calls.append("first-cart-transport"),
        )
    assert calls == ["attempt-journal"]


def test_increment2_artifacts_are_exact_and_keep_live_counters_zero() -> None:
    artifacts = build_increment2_artifacts_v02321(ROOT)
    for relative, payload in artifacts.items():
        assert json.loads((ROOT / relative).read_text(encoding="utf-8")) == payload
    closure = artifacts[
        "docs/analysis/product-v02321-preflight-closure-contract.json"
    ]
    progress = artifacts["docs/analysis/product-v02321-progress.json"]
    report = run_harness_contract_preflight_v02321(ROOT)

    assert closure["terminal"] == PREFLIGHT_CLOSURE_CONTRACT_PASS_V02321
    assert len(closure["scenarios"]) == 4
    assert report["terminal"] == PREFLIGHT_CLOSURE_CONTRACT_PASS_V02321
    assert report["typed_request_plan_terminal"] == (
        "ECOMSRE_PRODUCT_V02321_TYPED_REQUEST_PLAN_PASS"
    )
    assert progress["increment"] == 2
    assert progress["terminal"] == PREFLIGHT_CLOSURE_CONTRACT_PASS_V02321
    assert progress["infrastructure_session_count"] == 0
    assert progress["traffic_attempt_count"] == 0
    assert progress["formal_healthy_traffic_execution_count"] == 0
    assert progress["offline_failure_injection_scenario_count"] == 4
