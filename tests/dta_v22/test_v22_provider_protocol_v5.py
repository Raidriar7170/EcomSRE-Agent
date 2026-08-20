from __future__ import annotations

import pytest

from typing import Literal

from ecomsre.dta_v2.v22.provider_compatibility_v5 import (
    ProviderDecisionAliasV5,
    materialize_protocol_requests_v5,
    resolve_provider_alias_decision_v5,
)
from ecomsre.dta_v2.v22.controller_modes import ProviderOutputModeV22
from ecomsre.dta_v2.v22.controller_provider import ProviderHttpErrorV22
from ecomsre.dta_v2.v22.provider_protocol_v5 import (
    ProviderBoundaryTurnV5,
    ProviderBoundaryFailureCodeV5,
    ProviderRequestFailureV5,
    ProviderResponseProtocolErrorV5,
    provider_request_payload_v5,
    safe_provider_failure_v5,
)
from ecomsre.dta_v2.v22.protocol_suite_v5 import (
    ProviderProtocolFailureClassV5,
    ProviderProtocolReplicateTerminalV5,
    ProviderProtocolTransitionV5,
    ProviderTransitionStatusV5,
    build_protocol_replicate_report_v5,
    completed_transition_v5,
    transport_abort_transition_v5,
    unattempted_after_abort_transition_v5,
    run_protocol_replicate_v5,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.dta_v2.v22 import protocol_suite_v5 as protocol_module


def _passing_transitions(replicate_id: Literal["A", "B"]):
    return tuple(
        completed_transition_v5(
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
            failure_class=ProviderProtocolFailureClassV5.ACCEPTED,
            provider_turn_sha256=f"{index + 200:064x}",
            raw_alias_decision_sha256=f"{index + 300:064x}",
            resolved_canonical_decision_sha256=f"{index + 400:064x}",
            alias_binding_sha256=spec.request.alias_binding.binding_sha256,
        )
        for index, spec in enumerate(
            materialize_protocol_requests_v5(replicate_id=replicate_id),
            start=1,
        )
    )


def _report(replicate_id: Literal["A", "B"], transitions):
    return build_protocol_replicate_report_v5(
        replicate_id=replicate_id,
        implementation_commit="1" * 40,
        implementation_tree="2" * 40,
        manifest_sha256="3" * 64,
        probe_report_sha256="4" * 64,
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
    assert report.terminal is ProviderProtocolReplicateTerminalV5.PASS


def test_ordinary_and_per_arm_integer_gates_cannot_be_hidden() -> None:
    values = list(_passing_transitions("A"))
    ordinary_flat = [
        index
        for index, item in enumerate(values)
        if item.transition_kind == "ORDINARY" and item.arm.value == "FLAT_CANONICAL"
    ]
    for index in ordinary_flat[:2]:
        spec = materialize_protocol_requests_v5(replicate_id="A")[index]
        values[index] = completed_transition_v5(
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
            failure_class=ProviderProtocolFailureClassV5.RUNTIME_REJECTED,
            provider_turn_sha256=f"{index + 200:064x}",
            raw_alias_decision_sha256=f"{index + 300:064x}",
            resolved_canonical_decision_sha256=f"{index + 400:064x}",
            alias_binding_sha256=spec.request.alias_binding.binding_sha256,
        )
    report = _report("A", tuple(values))
    assert report.ordinary_first_pass_accepted_count == 18
    assert report.ordinary_first_pass_by_arm["FLAT_CANONICAL"]["accepted"] == 8
    assert report.terminal is ProviderProtocolReplicateTerminalV5.BLOCKED


def test_partial_report_separates_actual_abort_from_later_unattempted() -> None:
    specs = materialize_protocol_requests_v5(replicate_id="A")
    completed = list(_passing_transitions("A")[:4])
    completed.append(
        transport_abort_transition_v5(
            spec=specs[4],
            provider_request_sha256=specs[4].request.request_sha256,
            transport_reason_code="PROVIDER_TIMEOUT",
        )
    )
    completed.extend(
        unattempted_after_abort_transition_v5(spec=item) for item in specs[5:]
    )
    report = _report("A", tuple(completed))
    assert report.attempted_transition_count == 5
    assert report.completed_response_count == 4
    assert report.transport_abort_event_count == 1
    assert report.timeout_event_count == 1
    assert report.request_rejection_event_count == 0
    assert report.rate_limit_event_count == 0
    assert report.not_attempted_after_abort_count == 19
    assert report.ordinary_first_pass_acceptance is None
    assert report.final_acceptance is None
    assert report.terminal is ProviderProtocolReplicateTerminalV5.BLOCKED


def test_request_rejection_has_its_own_exact_event_count() -> None:
    specs = materialize_protocol_requests_v5(replicate_id="B")
    transitions = (
        transport_abort_transition_v5(
            spec=specs[0],
            provider_request_sha256=specs[0].request.request_sha256,
            transport_reason_code="PROVIDER_REQUEST_REJECTED",
        ),
        *(unattempted_after_abort_transition_v5(spec=item) for item in specs[1:]),
    )
    report = _report("B", transitions)
    assert report.request_rejection_event_count == 1
    assert report.transport_abort_event_count == 0
    assert transitions[0].status is ProviderTransitionStatusV5.PROVIDER_REQUEST_REJECTED
    assert report.rate_limit_event_count == 0
    assert report.server_error_event_count == 0
    assert report.timeout_event_count == 0
    assert report.connection_error_event_count == 0
    assert report.completed_response_count == 0
    assert report.provider_gate_eligible is False


@pytest.mark.parametrize("safe_code", ("invalid_parameter", None))
def test_request_rejection_persists_with_typed_uppercase_transition_detail(
    safe_code: str | None,
) -> None:
    specs = materialize_protocol_requests_v5(replicate_id="A")
    failure = safe_provider_failure_v5(
        error=ProviderHttpErrorV22(
            status=400,
            code=safe_code,
            error_type=None,
            param=None,
        ),
        failure_stage="TRANSITION",
        request_payload_sha256=semantic_sha256_v22(
            provider_request_payload_v5(request=specs[0].request)
        ),
    )
    first = transport_abort_transition_v5(
        spec=specs[0],
        provider_request_sha256=specs[0].request.request_sha256,
        transport_reason_code="PROVIDER_REQUEST_REJECTED",
        safe_provider_failure=failure,
    )
    transitions = (
        first,
        *(unattempted_after_abort_transition_v5(spec=item) for item in specs[1:]),
    )
    report = _report("A", transitions)
    assert first.failure_detail_code == "PROVIDER_REQUEST_REJECTED"
    assert first.safe_provider_failure == failure
    assert report.request_rejection_event_count == 1
    assert report.attempted_transition_count == 1


def test_protocol_taxonomy_covers_every_local_alias_rejection() -> None:
    assert {
        item.value for item in ProviderBoundaryFailureCodeV5
    }.issubset({item.value for item in ProviderProtocolFailureClassV5})


def test_request_rejection_returns_a_durable_report_with_one_provider_call() -> None:
    calls = 0

    def reject(request):
        nonlocal calls
        calls += 1
        failure = safe_provider_failure_v5(
            error=ProviderHttpErrorV22(
                status=400,
                code=None,
                error_type=None,
                param=None,
            ),
            failure_stage="TRANSITION",
            request_payload_sha256=semantic_sha256_v22(
                provider_request_payload_v5(request=request)
            ),
        )
        raise ProviderRequestFailureV5(failure)

    report = run_protocol_replicate_v5(
        replicate_id="A",
        implementation_commit="1" * 40,
        implementation_tree="2" * 40,
        manifest_sha256="3" * 64,
        probe_report_sha256="4" * 64,
        complete=reject,
        attempted_calls=lambda: calls,
    )
    assert report.provider_calls == 1
    assert report.request_rejection_event_count == 1
    assert report.attempted_transition_count == 1
    assert report.terminal is ProviderProtocolReplicateTerminalV5.BLOCKED


def test_unexpected_post_call_exception_returns_a_durable_partial_report() -> None:
    calls = 0

    def abort_after_call(_request):
        nonlocal calls
        calls += 1
        raise RuntimeError("must never be persisted")

    report = run_protocol_replicate_v5(
        replicate_id="A",
        implementation_commit="1" * 40,
        implementation_tree="2" * 40,
        manifest_sha256="3" * 64,
        probe_report_sha256="4" * 64,
        complete=abort_after_call,
        attempted_calls=lambda: calls,
    )
    assert report.provider_calls == 1
    assert report.attempted_transition_count == 1
    assert report.not_attempted_after_abort_count == 23
    assert report.failure_taxonomy["LOCAL_EXECUTION_ABORT"] == 1
    assert report.transitions[0].failure_detail_code == "LOCAL_EXECUTION_ABORT"
    assert report.terminal is ProviderProtocolReplicateTerminalV5.BLOCKED


def test_post_response_runtime_abort_retains_completed_response_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    spec = next(
        item
        for item in materialize_protocol_requests_v5(replicate_id="A")
        if item.protocol_intent == "READ" and item.transition_kind == "ORDINARY"
    )

    def complete(request):
        nonlocal calls
        calls += 1
        alias = ProviderDecisionAliasV5(
            decision="READ",
            hypothesis_alias=request.alias_binding.hypotheses[0].alias,
            action_alias=next(
                item.alias for item in request.alias_binding.actions if item.available
            ),
            support_aliases=(),
            contradict_aliases=(),
        )
        canonical = resolve_provider_alias_decision_v5(
            alias_decision=alias,
            binding=request.alias_binding,
        )
        return ProviderBoundaryTurnV5.model_construct(
            alias_decision=alias,
            canonical_decision=canonical,
            failure_code=None,
            provider_request_sha256=request.request_sha256,
            raw_response_sha256="a" * 64,
            turn_sha256="b" * 64,
            raw_alias_decision_sha256="c" * 64,
            resolved_canonical_decision_sha256=semantic_sha256_v22(
                canonical.model_dump(mode="json")
            ),
            alias_binding_sha256=request.alias_binding.binding_sha256,
            input_tokens=100,
            output_tokens=10,
            monotonic_latency_ms=1,
        )

    monkeypatch.setattr(
        protocol_module,
        "process_controller_decision_v22",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("local only")),
    )
    # The frozen order puts this ordinary READ at a later ordinal; return valid
    # turns only until the targeted request is reached.
    def ordered_complete(request):
        if request.request_sha256 == spec.request.request_sha256:
            return complete(request)
        return complete(request)

    report = run_protocol_replicate_v5(
        replicate_id="A",
        implementation_commit="1" * 40,
        implementation_tree="2" * 40,
        manifest_sha256="3" * 64,
        probe_report_sha256="4" * 64,
        complete=ordered_complete,
        attempted_calls=lambda: calls,
    )
    first = report.transitions[0]
    assert first.status is ProviderTransitionStatusV5.COMPLETED_RESPONSE
    assert first.failure_class is ProviderProtocolFailureClassV5.LOCAL_EXECUTION_ABORT
    assert first.raw_response_sha256 == "a" * 64
    assert first.provider_turn_sha256 == "b" * 64
    assert report.completed_response_count == 1
    assert report.transport_abort_event_count == 0
    assert report.not_attempted_after_abort_count == 23
    assert report.terminal is ProviderProtocolReplicateTerminalV5.BLOCKED


def test_report_rejects_rehashed_alias_binding_different_from_frozen_request() -> None:
    values = list(_passing_transitions("A"))
    forged = values[0].model_dump(mode="python")
    forged["alias_binding_sha256"] = "f" * 64
    forged["transition_sha256"] = semantic_sha256_v22(
        {key: value for key, value in forged.items() if key != "transition_sha256"}
    )
    values[0] = ProviderProtocolTransitionV5.model_validate(forged)
    with pytest.raises(ValueError, match="preregistered matrix"):
        _report("A", tuple(values))


def test_alias_rejection_records_runtime_as_not_evaluated() -> None:
    spec = materialize_protocol_requests_v5(replicate_id="A")[0]
    transition = completed_transition_v5(
        spec=spec,
        accepted=False,
        parsed_alias=True,
        alias_resolved=False,
        runtime_admitted=False,
        intent_conformant=False,
        input_tokens=100,
        output_tokens=10,
        latency_ms=1,
        provider_request_sha256=spec.request.request_sha256,
        raw_response_sha256="a" * 64,
        failure_class=ProviderProtocolFailureClassV5.UNKNOWN_ALIAS,
        provider_turn_sha256="b" * 64,
        raw_alias_decision_sha256="c" * 64,
        resolved_canonical_decision_sha256=None,
        alias_binding_sha256=spec.request.alias_binding.binding_sha256,
    )
    assert transition.runtime_admission_disposition == "NOT_EVALUATED"


def test_primary_failure_taxonomy_is_bound_to_the_exact_stage_chain() -> None:
    transition = _passing_transitions("A")[0]
    forged = transition.model_dump(mode="python")
    forged.update(
        {
            "accepted": False,
            "runtime_admitted": False,
            "runtime_admission_disposition": "REJECTED",
            "failure_class": ProviderProtocolFailureClassV5.UNKNOWN_ALIAS,
        }
    )
    forged["transition_sha256"] = semantic_sha256_v22(
        {key: value for key, value in forged.items() if key != "transition_sha256"}
    )
    with pytest.raises(ValueError, match="primary taxonomy"):
        ProviderProtocolTransitionV5.model_validate(forged)


def test_partial_report_rejects_two_actual_abort_events() -> None:
    specs = materialize_protocol_requests_v5(replicate_id="A")
    transitions = (
        transport_abort_transition_v5(
            spec=specs[0],
            provider_request_sha256=specs[0].request.request_sha256,
            transport_reason_code="PROVIDER_TIMEOUT",
        ),
        transport_abort_transition_v5(
            spec=specs[1],
            provider_request_sha256=specs[1].request.request_sha256,
            transport_reason_code="PROVIDER_CONNECTION_ERROR",
        ),
        *(unattempted_after_abort_transition_v5(spec=item) for item in specs[2:]),
    )
    with pytest.raises(ValueError, match="one actual request failure"):
        _report("A", transitions)


def test_transition_status_partition_is_exact() -> None:
    report = _report("B", _passing_transitions("B"))
    assert report.status_counts == {
        ProviderTransitionStatusV5.COMPLETED_RESPONSE.value: 24,
        ProviderTransitionStatusV5.PROVIDER_REQUEST_REJECTED.value: 0,
        ProviderTransitionStatusV5.PROVIDER_TRANSPORT_ABORT.value: 0,
        ProviderTransitionStatusV5.NOT_ATTEMPTED_AFTER_ABORT.value: 0,
        ProviderTransitionStatusV5.NOT_ATTEMPTED_AFTER_PROBE_FAILURE.value: 0,
    }


def test_report_binds_required_aggregate_chain_mode_and_category_counts() -> None:
    report = _report("A", _passing_transitions("A"))
    assert report.selected_mode is ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON
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
    assert report.failure_taxonomy[ProviderProtocolFailureClassV5.ACCEPTED.value] == 24


def test_bounded_response_protocol_error_is_completed_not_transport_abort() -> None:
    calls = 0

    def complete(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderResponseProtocolErrorV5(
                provider_request_sha256=request.request_sha256,
                request_payload_sha256=semantic_sha256_v22(
                    provider_request_payload_v5(request=request)
                ),
                raw_response_sha256="9" * 64,
                safe_failure_code="RESPONSE_MODEL_MISMATCH",
                input_tokens=None,
                output_tokens=None,
                monotonic_latency_ms=1,
                parsed_alias=True,
                alias_resolved=True,
                intent_conformant=True,
                raw_alias_decision_sha256="a" * 64,
                resolved_canonical_decision_sha256="b" * 64,
                alias_binding_sha256=request.alias_binding.binding_sha256,
            )
        hypothesis_by_id = {
            item.canonical_id: item.alias for item in request.alias_binding.hypotheses
        }
        if request.protocol_intent == "NO_INCIDENT":
            hypothesis_alias = hypothesis_by_id["h:none:no_incident"]
        elif request.protocol_intent == "ABSTAIN":
            hypothesis_alias = hypothesis_by_id["h:none:unresolved"]
        else:
            hypothesis_alias = next(
                item.alias
                for item in request.alias_binding.hypotheses
                if item.canonical_id not in {"h:none:no_incident", "h:none:unresolved"}
            )
        action_alias = (
            next(item.alias for item in request.alias_binding.actions if item.available)
            if request.protocol_intent == "READ"
            else "NONE"
        )
        evidence = tuple(item.alias for item in request.alias_binding.evidence)
        alias_decision = ProviderDecisionAliasV5(
            decision=request.protocol_intent,
            hypothesis_alias=hypothesis_alias,
            action_alias=action_alias,
            support_aliases=(evidence[0],) if request.protocol_intent == "COMMIT" else (),
            contradict_aliases=(),
        )
        canonical = resolve_provider_alias_decision_v5(
            alias_decision=alias_decision,
            binding=request.alias_binding,
        )
        return ProviderBoundaryTurnV5.model_construct(
            schema_version="dta-v22.provider-boundary-turn.v5",
            model="gpt-5.4-mini-2026-03-17",
            mode=ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON,
            provider_request_sha256=request.request_sha256,
            projection_sha256=request.projection_sha256,
            static_schema_sha256=request.static_schema_sha256,
            prompt_sha256="1" * 64,
            request_payload_sha256="2" * 64,
            alias_decision=alias_decision,
            canonical_decision=canonical,
            failure_code=None,
            raw_alias_decision_sha256="3" * 64,
            resolved_canonical_decision_sha256="6" * 64,
            alias_binding_sha256=request.alias_binding.binding_sha256,
            raw_response_sha256="4" * 64,
            input_tokens=100,
            output_tokens=10,
            total_tokens=110,
            monotonic_latency_ms=1,
            turn_sha256="5" * 64,
        )

    report = run_protocol_replicate_v5(
        replicate_id="A",
        implementation_commit="1" * 40,
        implementation_tree="2" * 40,
        manifest_sha256="3" * 64,
        probe_report_sha256="4" * 64,
        complete=complete,
        attempted_calls=lambda: calls,
    )
    assert report.completed_response_count == 24
    assert report.transport_abort_event_count == 0
    assert (
        report.failure_taxonomy[
            ProviderProtocolFailureClassV5.PROVIDER_RESPONSE_PROTOCOL_FAILURE.value
        ]
        == 1
    )
    assert report.transport_reason_counts == {}
    assert report.completed_response_with_known_usage_count == 23
    assert report.completed_response_with_unknown_usage_count == 1
    assert report.parsed_output_count == 24
    assert report.alias_resolved_output_count == 24
    assert report.runtime_admitted_output_count == 23
    assert report.runtime_admission_failure_count == 0
    assert report.transitions[0].runtime_admission_disposition == "NOT_EVALUATED"
    assert report.transitions[0].raw_alias_decision_sha256 == "a" * 64
    assert report.transitions[0].resolved_canonical_decision_sha256 == "b" * 64
    forged = report.transitions[0].model_dump(mode="python")
    forged["raw_alias_decision_sha256"] = None
    forged["transition_sha256"] = semantic_sha256_v22(
        {key: value for key, value in forged.items() if key != "transition_sha256"}
    )
    with pytest.raises(ValueError, match="decision provenance"):
        ProviderProtocolTransitionV5.model_validate(forged)

    for mutation in ("PROBE_STAGE", "MISMATCHED_CODE"):
        forged = report.transitions[0].model_dump(mode="python")
        safe = dict(forged["safe_provider_failure"])
        if mutation == "PROBE_STAGE":
            safe["failure_stage"] = "PROBE"
        else:
            safe["safe_code"] = "RESPONSE_ENVELOPE_INVALID"
        safe["failure_sha256"] = semantic_sha256_v22(
            {key: value for key, value in safe.items() if key != "failure_sha256"}
        )
        forged["safe_provider_failure"] = safe
        forged["transition_sha256"] = semantic_sha256_v22(
            {
                key: value
                for key, value in forged.items()
                if key != "transition_sha256"
            }
        )
        with pytest.raises(ValueError, match="detail taxonomy"):
            ProviderProtocolTransitionV5.model_validate(forged)
    assert report.input_tokens is None
    assert report.mean_input_tokens is None
    assert report.max_input_tokens is None
    assert report.output_tokens is None
    assert report.provider_gate_eligible is False
    assert report.terminal is ProviderProtocolReplicateTerminalV5.BLOCKED
