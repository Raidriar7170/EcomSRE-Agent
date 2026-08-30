"""Pure offline failure-injection state machine for Product v0.2.3.2.1."""

from __future__ import annotations

from enum import Enum
from typing import NoReturn

from pydantic import ConfigDict, Field

from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.pilot.traffic_harness_closure_v02321 import (
    ChangedSourceBindingV02321,
    InfrastructureSessionCompletionV02321,
    InfrastructureSessionStartV02321,
    TrafficDispatchFailureEvidenceV02321,
    TrafficHarnessClosureV02321,
    TrafficHarnessFailureInjectionScenarioV02321,
    TrafficHarnessStageV02321,
    TrafficPreflightAttemptCompletionV02321,
    TrafficPreflightAttemptStartV02321,
    TrafficPreflightEventV02321,
    TrafficPreflightLedgerV02321,
    invoke_first_cart_transport_v02321,
    request_sandbox_start_v02321,
)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class OfflineFailurePointV02321(str, Enum):
    REQUEST_PLAN_CONSTRUCTION_FAILURE = "REQUEST_PLAN_CONSTRUCTION_FAILURE"
    SANDBOX_START_FAILURE = "SANDBOX_START_FAILURE"
    RUNTIME_INSPECT_FAILURE = "RUNTIME_INSPECT_FAILURE"
    FIRST_CART_SEND_FAILURE = "FIRST_CART_SEND_FAILURE"


_FAILURE_BINDINGS: dict[
    OfflineFailurePointV02321, tuple[TrafficHarnessStageV02321, str]
] = {
    OfflineFailurePointV02321.REQUEST_PLAN_CONSTRUCTION_FAILURE: (
        TrafficHarnessStageV02321.REQUEST_PLAN_CONSTRUCTION,
        "RUN_ID_SCHEMA_PATTERN_MISMATCH",
    ),
    OfflineFailurePointV02321.SANDBOX_START_FAILURE: (
        TrafficHarnessStageV02321.SANDBOX_START_REQUESTED,
        "SANDBOX_START_INJECTED",
    ),
    OfflineFailurePointV02321.RUNTIME_INSPECT_FAILURE: (
        TrafficHarnessStageV02321.RUNTIME_INSPECT_REQUESTED,
        "RUNTIME_INSPECT_INJECTED",
    ),
    OfflineFailurePointV02321.FIRST_CART_SEND_FAILURE: (
        TrafficHarnessStageV02321.FIRST_CART_SEND_REQUESTED,
        "FIRST_CART_TRANSPORT_INJECTED",
    ),
}


class OfflineTrafficPreflightBindingsV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    state_clone_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_continuity_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_inspect_request_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    queue_sha256: str = Field(pattern=_SHA256_PATTERN)
    outer_baseline_sha256: str = Field(pattern=_SHA256_PATTERN)
    endpoint_sha256: str = Field(pattern=_SHA256_PATTERN)
    first_cart_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    changed_source_bindings: tuple[ChangedSourceBindingV02321, ...] = Field(
        min_length=1
    )


class _InjectedOfflineFailureV02321(RuntimeError):
    def __init__(self, stage: TrafficHarnessStageV02321, safe_error_code: str) -> None:
        super().__init__(safe_error_code)
        self.stage = stage
        self.safe_error_code = safe_error_code


def _event_sha256(event: TrafficPreflightEventV02321) -> str:
    if isinstance(event, TrafficHarnessClosureV02321):
        return event.closure_sha256
    return event.event_sha256


def _event_meta(
    events: list[TrafficPreflightEventV02321],
    *,
    second_base: int,
) -> dict[str, object]:
    ordinal = len(events) + 1
    return {
        "event_ordinal": ordinal,
        "prior_event_sha256": _event_sha256(events[-1]) if events else None,
        "observed_at_utc": f"2026-08-30T00:{second_base:02d}:{ordinal:02d}+00:00",
    }


def _clean_observations() -> dict[str, object]:
    return {
        "product_cleanup": {
            "observation_complete": True,
            "verdict": "CLEAN",
            "owned_host_processes": 0,
            "database_owner_count_before": 0,
            "database_owner_count_after": 0,
            "product_api_port_available": True,
            "non_owned_resources_changed": False,
            "safe_error_code": None,
        },
        "demo_cleanup": {
            "observation_complete": True,
            "verdict": "CLEAN",
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
            "safe_error_code": None,
        },
        "owned_resource_counts": {
            "containers": 0,
            "networks": 0,
            "volumes": 0,
            "host_processes": 0,
        },
        "non_owned_resources_changed": False,
    }


def _raise_injected_failure(
    failure_point: OfflineFailurePointV02321,
    stage: TrafficHarnessStageV02321,
) -> NoReturn:
    expected_stage, safe_error_code = _FAILURE_BINDINGS[failure_point]
    if stage is not expected_stage:
        raise AssertionError("offline failure point was invoked at the wrong stage")
    raise _InjectedOfflineFailureV02321(stage, safe_error_code)


def execute_offline_failure_injection_v02321(
    bindings: OfflineTrafficPreflightBindingsV02321,
    failure_point: OfflineFailurePointV02321,
) -> TrafficHarnessFailureInjectionScenarioV02321:
    """Execute one injected failure through the same admission and closure flow."""

    target_stage, _ = _FAILURE_BINDINGS[failure_point]
    events: list[TrafficPreflightEventV02321] = []
    trace: list[TrafficHarnessStageV02321] = []
    session: InfrastructureSessionStartV02321 | None = None
    attempt_start: TrafficPreflightAttemptStartV02321 | None = None
    attempt_completion: TrafficPreflightAttemptCompletionV02321 | None = None
    second_base = list(OfflineFailurePointV02321).index(failure_point) * 10

    def observe(stage: TrafficHarnessStageV02321) -> None:
        trace.append(stage)

    def inject_if_target(stage: TrafficHarnessStageV02321) -> None:
        if stage is target_stage:
            _raise_injected_failure(failure_point, stage)

    try:
        observe(TrafficHarnessStageV02321.REQUEST_PLAN_CONSTRUCTION)
        inject_if_target(TrafficHarnessStageV02321.REQUEST_PLAN_CONSTRUCTION)
        observe(TrafficHarnessStageV02321.REQUEST_PLAN_VALIDATED)

        observe(TrafficHarnessStageV02321.SANDBOX_START_REQUESTED)
        session = InfrastructureSessionStartV02321.build(
            **_event_meta(events, second_base=second_base),
            request_plan_sha256=bindings.request_plan_sha256,
            runtime_inspect_request_sha256=(
                bindings.runtime_inspect_request_sha256
            ),
            runtime_continuity_descriptor_sha256=(
                bindings.runtime_continuity_descriptor_sha256
            ),
            state_clone_sha256=bindings.state_clone_sha256,
            stage=TrafficHarnessStageV02321.SANDBOX_START_REQUESTED,
            sandbox_start_requested=True,
            infrastructure_session_count_after=1,
        )
        request_sandbox_start_v02321(
            session,
            persist_start=events.append,
            request_start=lambda: inject_if_target(
                TrafficHarnessStageV02321.SANDBOX_START_REQUESTED
            ),
        )
        observe(TrafficHarnessStageV02321.SANDBOX_READY)

        observe(
            TrafficHarnessStageV02321.RUNTIME_AUTHORITY_VERIFICATION_REQUESTED
        )
        observe(TrafficHarnessStageV02321.RUNTIME_AUTHORITY_VERIFIED)
        observe(TrafficHarnessStageV02321.QUEUE_PRESTATE_CAPTURED)
        observe(TrafficHarnessStageV02321.BASELINE_PRESTATE_CAPTURED)
        observe(TrafficHarnessStageV02321.RUNTIME_INSPECT_REQUESTED)
        inject_if_target(TrafficHarnessStageV02321.RUNTIME_INSPECT_REQUESTED)
        observe(TrafficHarnessStageV02321.RUNTIME_INSPECTED)

        observe(TrafficHarnessStageV02321.TRAFFIC_ATTEMPT_CONSUMED)
        attempt_start = TrafficPreflightAttemptStartV02321.build(
            **_event_meta(events, second_base=second_base),
            attempt_ordinal=1,
            prior_attempt_completion_sha256=None,
            prior_failure_stage=None,
            prior_safe_error_code=None,
            prior_implementation_sha256=None,
            changed_surface="INITIAL",
            changed_source_bindings=[
                item.model_dump(mode="json")
                for item in bindings.changed_source_bindings
            ],
            repair_rationale=(
                "initial successor admission after the typed request and "
                "cleanup contracts passed offline"
            ),
            session_id=session.session_id,
            session_start_sha256=session.event_sha256,
            request_plan_sha256=bindings.request_plan_sha256,
            traffic_contract_sha256=bindings.traffic_contract_sha256,
            profile_sha256=bindings.profile_sha256,
            runtime_inspect_request_sha256=(
                bindings.runtime_inspect_request_sha256
            ),
            runtime_authority_sha256=(
                bindings.runtime_continuity_descriptor_sha256
            ),
            endpoint_sha256=bindings.endpoint_sha256,
            first_cart_payload_sha256=bindings.first_cart_payload_sha256,
            queue_before_sha256=bindings.queue_sha256,
            outer_baseline_before_sha256=bindings.outer_baseline_sha256,
            sandbox_ready=True,
            runtime_authority_equal=True,
            request_plan_equal=True,
            checkout_state="RUNNING",
            checkout_healthy=True,
            checkout_restart_count=0,
            endpoint_validator_ready=True,
            payload_validator_ready=True,
            stage=TrafficHarnessStageV02321.TRAFFIC_ATTEMPT_CONSUMED,
            traffic_attempt_count_after=1,
        )

        def invoke_transport() -> None:
            observe(TrafficHarnessStageV02321.FIRST_CART_SEND_REQUESTED)
            inject_if_target(TrafficHarnessStageV02321.FIRST_CART_SEND_REQUESTED)

        invoke_first_cart_transport_v02321(
            attempt_start,
            persist_start=events.append,
            invoke_transport=invoke_transport,
        )
        raise AssertionError("offline failure injection did not terminate")
    except _InjectedOfflineFailureV02321 as failure:
        if attempt_start is not None:
            dispatch_failure = TrafficDispatchFailureEvidenceV02321.build(
                attempt_id=attempt_start.attempt_id,
                endpoint_sha256=attempt_start.endpoint_sha256,
                first_cart_payload_sha256=attempt_start.first_cart_payload_sha256,
                transport_invoked=True,
                remote_delivery="UNKNOWN",
                safe_error_code=failure.safe_error_code,
            )
            attempt_completion = TrafficPreflightAttemptCompletionV02321.build(
                **_event_meta(events, second_base=second_base),
                attempt_id=attempt_start.attempt_id,
                attempt_ordinal=attempt_start.attempt_ordinal,
                attempt_start_sha256=attempt_start.event_sha256,
                session_id=attempt_start.session_id,
                traffic_execution_sha256=None,
                traffic_dispatch_failure=dispatch_failure.model_dump(mode="json"),
                stage=TrafficHarnessStageV02321.FIRST_CART_SEND_REQUESTED,
                first_cart_transport_invoked=True,
                planned_transactions=10,
                completed_transactions=0,
                successful_transactions=0,
                failed_transactions=0,
                safe_error_code=failure.safe_error_code,
                terminal="ATTEMPT_FAILED",
                monotonic_duration_ms=0,
            )
            events.append(attempt_completion)

        prestate_available = (
            TrafficHarnessStageV02321.BASELINE_PRESTATE_CAPTURED in trace
        )
        if prestate_available:
            observe(TrafficHarnessStageV02321.QUEUE_POSTSTATE_CAPTURED)
            observe(TrafficHarnessStageV02321.BASELINE_POSTSTATE_CAPTURED)
        observe(TrafficHarnessStageV02321.CLEANUP_COMPLETE)

        if attempt_start is not None:
            terminal = "CLEAN_POST_TRAFFIC"
        elif prestate_available:
            terminal = "CLEAN_PRE_TRAFFIC"
        else:
            terminal = "BLOCKED_PRESTATE_UNAVAILABLE"

        closure = TrafficHarnessClosureV02321.build(
            **_event_meta(events, second_base=second_base),
            session_id=session.session_id if session is not None else None,
            attempt_id=(
                attempt_start.attempt_id if attempt_start is not None else None
            ),
            stage_reached=failure.stage,
            observed_stage_sequence=trace,
            request_plan_sha256=(
                bindings.request_plan_sha256 if session is not None else None
            ),
            queue_before_sha256=(
                bindings.queue_sha256 if prestate_available else None
            ),
            queue_after_sha256=(
                bindings.queue_sha256 if prestate_available else None
            ),
            outer_baseline_before_sha256=(
                bindings.outer_baseline_sha256 if prestate_available else None
            ),
            outer_baseline_after_sha256=(
                bindings.outer_baseline_sha256 if prestate_available else None
            ),
            runtime_inspect_request_sha256=(
                bindings.runtime_inspect_request_sha256
                if TrafficHarnessStageV02321.RUNTIME_INSPECT_REQUESTED in trace
                else None
            ),
            traffic_execution_sha256=(
                attempt_completion.traffic_execution_sha256
                if attempt_completion is not None
                else None
            ),
            traffic_dispatch_failure_sha256=(
                attempt_completion.traffic_dispatch_failure.dispatch_failure_sha256
                if attempt_completion is not None
                and attempt_completion.traffic_dispatch_failure is not None
                else None
            ),
            failure_stage=failure.stage,
            safe_error_code=failure.safe_error_code,
            closure_terminal=terminal,
            **_clean_observations(),
        )
        events.append(closure)
        if session is not None:
            events.append(
                InfrastructureSessionCompletionV02321.build(
                    **_event_meta(events, second_base=second_base),
                    session_id=session.session_id,
                    session_start_sha256=session.event_sha256,
                    closure_sha256=closure.closure_sha256,
                    stage=TrafficHarnessStageV02321.CLEANUP_COMPLETE,
                    stage_reached=closure.stage_reached,
                    monotonic_duration_ms=1,
                    infrastructure_session_count_after=1,
                    cleanup_stage="OBSERVATION_COMPLETE",
                    terminal=(
                        "SESSION_CLOSED_CLEAN"
                        if terminal in {"CLEAN_PRE_TRAFFIC", "CLEAN_POST_TRAFFIC"}
                        else "SESSION_CLOSED_BLOCKED"
                    ),
                )
            )

        ledger = TrafficPreflightLedgerV02321.build(events=tuple(events))
        return TrafficHarnessFailureInjectionScenarioV02321.build(
            scenario_id=failure_point.value,
            safe_error_code=failure.safe_error_code,
            expected_infrastructure_session_count=(
                ledger.infrastructure_session_count
            ),
            expected_traffic_attempt_count=ledger.traffic_attempt_count,
            execution_trace=trace,
            closure=closure.model_dump(mode="json"),
            ledger=ledger.model_dump(mode="json"),
        )


def execute_offline_failure_matrix_v02321(
    bindings: OfflineTrafficPreflightBindingsV02321,
) -> tuple[TrafficHarnessFailureInjectionScenarioV02321, ...]:
    return tuple(
        execute_offline_failure_injection_v02321(bindings, failure_point)
        for failure_point in OfflineFailurePointV02321
    )


__all__ = (
    "OfflineFailurePointV02321",
    "OfflineTrafficPreflightBindingsV02321",
    "execute_offline_failure_injection_v02321",
    "execute_offline_failure_matrix_v02321",
)
