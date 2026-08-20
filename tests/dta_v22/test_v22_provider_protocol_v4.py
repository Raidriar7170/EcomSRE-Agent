from __future__ import annotations

import pytest

from typing import Literal

from ecomsre.dta_v2.v22.provider_boundary_v4 import (
    ProviderDecisionAliasV4,
    materialize_protocol_requests_v4,
    resolve_provider_alias_decision_v4,
)
from ecomsre.dta_v2.v22.controller_modes import ProviderOutputModeV22
from ecomsre.dta_v2.v22.provider_protocol_v4 import (
    ProviderBoundaryTurnV4,
    ProviderResponseProtocolErrorV4,
)
from ecomsre.dta_v2.v22.protocol_suite_v4 import (
    ProviderProtocolFailureClassV4,
    ProviderProtocolReplicateTerminalV4,
    ProviderTransitionStatusV4,
    build_protocol_replicate_report_v4,
    completed_transition_v4,
    transport_abort_transition_v4,
    unattempted_after_abort_transition_v4,
    run_protocol_replicate_v4,
)


def _passing_transitions(replicate_id: Literal["A", "B"]):
    return tuple(
        completed_transition_v4(
            spec=spec,
            accepted=True,
            parsed_alias=True,
            alias_resolved=True,
            runtime_admitted=True,
            intent_conformant=True,
            input_tokens=100,
            output_tokens=10,
            latency_ms=50,
            provider_request_sha256=spec.request.request_sha256,
            raw_response_sha256=f"{index + 100:064x}",
            failure_class=ProviderProtocolFailureClassV4.ACCEPTED,
        )
        for index, spec in enumerate(
            materialize_protocol_requests_v4(replicate_id=replicate_id),
            start=1,
        )
    )


def _report(replicate_id: Literal["A", "B"], transitions):
    return build_protocol_replicate_report_v4(
        replicate_id=replicate_id,
        implementation_commit="1" * 40,
        implementation_tree="2" * 40,
        manifest_sha256="3" * 64,
        probe_report_sha256="4" * 64,
        selected_mode=ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT,
        transitions=transitions,
    )


def test_complete_replicate_passes_all_frozen_integer_gates() -> None:
    report = _report("A", _passing_transitions("A"))
    assert report.completed_response_count == 24
    assert report.ordinary_first_pass_accepted_count == 20
    assert report.ordinary_first_pass_by_arm == {
        "FLAT_CANONICAL": {"accepted": 10, "planned": 10},
        "PLANNER_LITE": {"accepted": 10, "planned": 10},
    }
    assert report.correction_accepted_count == 4
    assert report.correction_by_arm == {
        "FLAT_CANONICAL": {"accepted": 2, "planned": 2},
        "PLANNER_LITE": {"accepted": 2, "planned": 2},
    }
    assert report.final_accepted_count == 24
    assert report.bounded_response_count == 24
    assert report.transport_abort_event_count == 0
    assert report.not_attempted_after_abort_count == 0
    assert report.input_tokens == 2400
    assert report.max_input_tokens == 100
    assert report.completed_response_with_known_usage_count == 24
    assert report.completed_response_with_unknown_usage_count == 0
    assert report.terminal is ProviderProtocolReplicateTerminalV4.PASS


def test_ordinary_and_per_arm_integer_gates_cannot_be_hidden() -> None:
    values = list(_passing_transitions("A"))
    ordinary_flat = [
        index
        for index, item in enumerate(values)
        if item.transition_kind == "ORDINARY" and item.arm.value == "FLAT_CANONICAL"
    ]
    for index in ordinary_flat[:2]:
        spec = materialize_protocol_requests_v4(replicate_id="A")[index]
        values[index] = completed_transition_v4(
            spec=spec,
            accepted=False,
            parsed_alias=True,
            alias_resolved=True,
            runtime_admitted=False,
            intent_conformant=True,
            input_tokens=100,
            output_tokens=10,
            latency_ms=50,
            provider_request_sha256=spec.request.request_sha256,
            raw_response_sha256=f"{index + 100:064x}",
            failure_class=ProviderProtocolFailureClassV4.RUNTIME_REJECTED,
        )
    report = _report("A", tuple(values))
    assert report.ordinary_first_pass_accepted_count == 18
    assert report.ordinary_first_pass_by_arm["FLAT_CANONICAL"]["accepted"] == 8
    assert report.terminal is ProviderProtocolReplicateTerminalV4.BLOCKED


def test_partial_report_separates_actual_abort_from_later_unattempted() -> None:
    specs = materialize_protocol_requests_v4(replicate_id="A")
    completed = list(_passing_transitions("A")[:4])
    completed.append(
        transport_abort_transition_v4(
            spec=specs[4],
            provider_request_sha256=specs[4].request.request_sha256,
            transport_reason_code="TIMEOUT_ERROR",
        )
    )
    completed.extend(
        unattempted_after_abort_transition_v4(spec=item) for item in specs[5:]
    )
    report = _report("A", tuple(completed))
    assert report.attempted_transition_count == 5
    assert report.completed_response_count == 4
    assert report.transport_abort_event_count == 1
    assert report.not_attempted_after_abort_count == 19
    assert report.ordinary_first_pass_acceptance is None
    assert report.final_acceptance is None
    assert report.terminal is ProviderProtocolReplicateTerminalV4.BLOCKED


def test_partial_report_rejects_two_actual_abort_events() -> None:
    specs = materialize_protocol_requests_v4(replicate_id="A")
    transitions = (
        transport_abort_transition_v4(
            spec=specs[0],
            provider_request_sha256=specs[0].request.request_sha256,
            transport_reason_code="TIMEOUT_ERROR",
        ),
        transport_abort_transition_v4(
            spec=specs[1],
            provider_request_sha256=specs[1].request.request_sha256,
            transport_reason_code="CONNECTION_ERROR",
        ),
        *(unattempted_after_abort_transition_v4(spec=item) for item in specs[2:]),
    )
    with pytest.raises(ValueError, match="one actual transport abort"):
        _report("A", transitions)


def test_transition_status_partition_is_exact() -> None:
    report = _report("B", _passing_transitions("B"))
    assert report.status_counts == {
        ProviderTransitionStatusV4.COMPLETED_RESPONSE.value: 24,
        ProviderTransitionStatusV4.PROVIDER_TRANSPORT_ABORT.value: 0,
        ProviderTransitionStatusV4.NOT_ATTEMPTED_AFTER_ABORT.value: 0,
        ProviderTransitionStatusV4.NOT_ATTEMPTED_AFTER_PROBE_FAILURE.value: 0,
    }


def test_report_binds_required_aggregate_chain_mode_and_category_counts() -> None:
    report = _report("A", _passing_transitions("A"))
    assert report.selected_mode is ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT
    assert report.parsed_output_count == 24
    assert report.alias_resolved_output_count == 24
    assert report.runtime_admitted_output_count == 24
    assert report.protocol_intent_accepted_output_count == 24
    assert report.mean_input_tokens == 100.0
    assert report.counts_by_arm["FLAT_CANONICAL"] == {
        "planned": 12,
        "attempted": 12,
        "completed_response": 12,
        "accepted": 12,
    }
    assert report.counts_by_category["BUDGET_EXHAUSTED"]["planned"] == 2
    assert report.counts_by_category["SOURCE_UNAVAILABLE"]["planned"] == 2
    assert sum(report.failure_taxonomy.values()) == 24
    assert report.failure_taxonomy[ProviderProtocolFailureClassV4.ACCEPTED.value] == 24


def test_bounded_response_protocol_error_is_completed_not_transport_abort() -> None:
    calls = 0

    def complete(request, mode):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderResponseProtocolErrorV4(
                provider_request_sha256=request.request_sha256,
                raw_response_sha256="9" * 64,
                safe_failure_code="RESPONSE_MODEL_MISMATCH",
                input_tokens=None,
                output_tokens=None,
                monotonic_latency_ms=1,
            )
        properties = request.dynamic_schema["properties"]
        support_rule = properties["support_aliases"]
        evidence = support_rule["items"]["enum"]
        alias_decision = ProviderDecisionAliasV4(
            decision=properties["decision"]["enum"][0],
            hypothesis_alias=properties["hypothesis_alias"]["enum"][0],
            action_alias=properties["action_alias"]["enum"][0],
            support_aliases=(evidence[0],) if support_rule.get("minItems") else (),
            contradict_aliases=(),
        )
        canonical = resolve_provider_alias_decision_v4(
            alias_decision=alias_decision,
            binding=request.alias_binding,
        )
        return ProviderBoundaryTurnV4.model_construct(
            schema_version="dta-v22.provider-boundary-turn.v4",
            model="gpt-5.4-mini-2026-03-17",
            mode=mode,
            provider_request_sha256=request.request_sha256,
            projection_sha256=request.projection_sha256,
            schema_sha256=request.schema_sha256,
            prompt_sha256="1" * 64,
            request_payload_sha256="2" * 64,
            alias_decision=alias_decision,
            canonical_decision=canonical,
            failure_code=None,
            raw_decision_sha256="3" * 64,
            raw_response_sha256="4" * 64,
            input_tokens=100,
            output_tokens=10,
            total_tokens=110,
            monotonic_latency_ms=1,
            turn_sha256="5" * 64,
        )

    report = run_protocol_replicate_v4(
        replicate_id="A",
        implementation_commit="1" * 40,
        implementation_tree="2" * 40,
        manifest_sha256="3" * 64,
        probe_report_sha256="4" * 64,
        selected_mode=ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT,
        complete=complete,
        attempted_calls=lambda: calls,
    )
    assert report.completed_response_count == 24
    assert report.transport_abort_event_count == 0
    assert (
        report.failure_taxonomy[
            ProviderProtocolFailureClassV4.PROVIDER_RESPONSE_PROTOCOL_FAILURE.value
        ]
        == 1
    )
    assert report.transport_reason_counts == {}
    assert report.completed_response_with_known_usage_count == 23
    assert report.completed_response_with_unknown_usage_count == 1
    assert report.input_tokens is None
    assert report.mean_input_tokens is None
    assert report.max_input_tokens is None
    assert report.output_tokens is None
    assert report.provider_gate_eligible is False
    assert report.terminal is ProviderProtocolReplicateTerminalV4.BLOCKED
