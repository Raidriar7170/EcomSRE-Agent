from __future__ import annotations

from typing import Any

import pytest

from ecomsre.dta_v2.v22.controller_contracts import (
    ABSTAIN_HYPOTHESIS_ID_V22,
    NO_ACTION_ID_V22,
    NO_INCIDENT_HYPOTHESIS_ID_V22,
    ControllerDecisionKindV22,
    ControllerDecisionV22,
)
from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.controller_modes import (
    PRIMARY_MODEL_V22,
    ProviderOutputModeV22,
    ProviderProbeStatusV22,
    probe_provider_output_mode_v22,
)
from ecomsre.dta_v2.v22.controller_provider import (
    ProviderControllerTurnV22,
    ProviderTurnRequestV22,
)
from ecomsre.dta_v2.v22.protocol_suite import (
    ProviderProtocolPartialFailureReceiptV3,
    ProviderProtocolFailureClassV3,
    ProviderProtocolSuiteTerminalV22,
    ProviderProtocolSuiteTerminalV3,
    SyntheticTransitionCategoryV22,
    run_provider_protocol_capability_suite_v22,
    run_provider_protocol_capability_suite_v3,
    run_provider_protocol_replicate_v3,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.dta_v2.v22.predicates import evaluate_support_v22


def _decision(request: ProviderTurnRequestV22) -> ControllerDecisionV22:
    controller_input = request.controller_input
    if request.plan_correction is not None:
        action = controller_input.action_catalog.actions[0]
        return ControllerDecisionV22(
            decision=ControllerDecisionKindV22.READ,
            working_hypothesis_id="h:payment:configuration-error",
            action_id=action.action_id,
            supporting_evidence_refs=(),
            contradicting_evidence_refs=(),
        )
    if (
        controller_input.action_catalog.remaining_budget == 0
        or any(
            item.status.value.startswith("FAILURE_")
            for item in controller_input.salient_memory.observation_summaries
        )
    ):
        return ControllerDecisionV22(
            decision=ControllerDecisionKindV22.ABSTAIN,
            working_hypothesis_id=ABSTAIN_HYPOTHESIS_ID_V22,
            action_id=NO_ACTION_ID_V22,
            supporting_evidence_refs=(),
            contradicting_evidence_refs=(),
        )
    config = controller_input.hypothesis_catalog.require(
        "h:payment:configuration-error"
    )
    support = evaluate_support_v22(
        policy=controller_input.evidence_support_policy,
        mechanism=config.mechanism,
        target_service="payment",
        parent_service=None,
        predicates=controller_input.salient_memory.predicates,
    )
    if support.accepted:
        return ControllerDecisionV22(
            decision=ControllerDecisionKindV22.COMMIT,
            working_hypothesis_id=config.hypothesis_id,
            action_id=NO_ACTION_ID_V22,
            supporting_evidence_refs=support.supporting_evidence_refs,
            contradicting_evidence_refs=(),
        )
    if not controller_input.bootstrap.strong_anomaly_predicate_ids:
        return ControllerDecisionV22(
            decision=ControllerDecisionKindV22.NO_INCIDENT,
            working_hypothesis_id=NO_INCIDENT_HYPOTHESIS_ID_V22,
            action_id=NO_ACTION_ID_V22,
            supporting_evidence_refs=(),
            contradicting_evidence_refs=(),
        )
    action = controller_input.action_catalog.actions[0]
    return ControllerDecisionV22(
        decision=ControllerDecisionKindV22.READ,
        working_hypothesis_id="h:payment:configuration-error",
        action_id=action.action_id,
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
    )


class ScriptedProtocolProvider:
    def __init__(
        self,
        *,
        fail_first: bool = False,
        fail_calls: frozenset[int] = frozenset(),
        constant_raw_response: bool = False,
    ) -> None:
        self.calls = 0
        self.fail_first = fail_first
        self.fail_calls = fail_calls
        self.constant_raw_response = constant_raw_response

    def complete(
        self,
        *,
        request: ProviderTurnRequestV22,
    ) -> ProviderControllerTurnV22:
        self.calls += 1
        decision = (
            ControllerDecisionV22(
                decision=ControllerDecisionKindV22.ABSTAIN,
                working_hypothesis_id=ABSTAIN_HYPOTHESIS_ID_V22,
                action_id=NO_ACTION_ID_V22,
                supporting_evidence_refs=(),
                contradicting_evidence_refs=(),
            )
            if (self.fail_first and self.calls == 1) or self.calls in self.fail_calls
            else _decision(request)
        )
        visible_state = request.visible_state()
        payload: dict[str, Any] = {
            "schema_version": "dta-v22.provider-controller-turn.v1",
            "model": PRIMARY_MODEL_V22,
            "mode": request.identity.provider_output_mode,
            "controller_identity_sha256": request.identity.identity_sha256,
            "prompt_sha256": request.identity.prompt_sha256,
            "visible_input_sha256": semantic_sha256_v22(visible_state),
            "provider_request_sha256": request.request_sha256,
            "request_payload_sha256": semantic_sha256_v22(
                {"scripted_request": request.request_sha256, "call": self.calls}
            ),
            "decision": decision,
            "parse_error_code": None,
            "raw_decision_sha256": semantic_sha256_v22(
                {"raw_decision": decision.model_dump(mode="json")}
            ),
            "raw_response_sha256": semantic_sha256_v22(
                {"constant_provider_response": True}
                if self.constant_raw_response
                else {
                    "call": self.calls,
                    "request": request.request_sha256,
                }
            ),
            "input_tokens": 100 + self.calls,
            "output_tokens": 20,
            "total_tokens": 120 + self.calls,
            "monotonic_latency_ms": self.calls,
        }
        draft = ProviderControllerTurnV22.model_construct(
            **payload,
            turn_sha256="0" * 64,
        )
        return ProviderControllerTurnV22.model_validate(
            {
                **payload,
                "turn_sha256": semantic_sha256_v22(
                    draft.model_dump(mode="json", exclude={"turn_sha256"})
                ),
            },
            context={"request": request},
        )


def _probe():
    return probe_provider_output_mode_v22(
        probe=lambda _model, _mode, _schema: ProviderProbeStatusV22.SUPPORTED
    )


def test_provider_protocol_suite_covers_both_primary_arms_and_meets_gate() -> None:
    provider = ScriptedProtocolProvider()
    report = run_provider_protocol_capability_suite_v22(
        provider_probe=_probe(),
        complete=provider.complete,
    )
    assert report.transition_count == 50
    assert report.first_pass_accepted_count == 48
    assert report.post_correction_accepted_count == 50
    assert report.first_pass_protocol_acceptance == 0.96
    assert report.post_correction_protocol_acceptance == 1.0
    assert report.correction_count == 2
    assert report.correction_rate == 0.04
    assert report.invalid_dispatches == 0
    assert report.provider_calls == provider.calls == 50
    assert report.provider_gate_eligible is True
    assert report.terminal is ProviderProtocolSuiteTerminalV22.PROVIDER_PROTOCOL_GATE_PASS
    assert {item.category for item in report.transitions} == set(
        SyntheticTransitionCategoryV22
    )
    assert sum(
        item.arm is ControllerArmV22.FLAT_CANONICAL for item in report.transitions
    ) == 25
    assert sum(
        item.arm is ControllerArmV22.PLANNER_LITE for item in report.transitions
    ) == 25
    assert len(
        {
            turn.raw_response_sha256
            for item in report.transitions
            for turn in (item.provider_turn,)
        }
    ) == 50


def test_provider_protocol_report_rejects_rehashed_transition_omission() -> None:
    report = run_provider_protocol_capability_suite_v22(
        provider_probe=_probe(),
        complete=ScriptedProtocolProvider().complete,
    )
    forged = report.model_copy(
        update={
            "transitions": report.transitions[:-1],
            "transition_count": 49,
            "provider_calls": 49,
        }
    )
    with pytest.raises(ValueError, match="identity or matrix differs"):
        forged.model_copy(
            update={
                "report_sha256": semantic_sha256_v22(
                    forged.model_dump(mode="json", exclude={"report_sha256"})
                )
            }
        ).require_report()


def test_provider_protocol_report_rejects_wrong_mode_and_fake_gate_counts() -> None:
    report = run_provider_protocol_capability_suite_v22(
        provider_probe=_probe(),
        complete=ScriptedProtocolProvider().complete,
    )
    first = report.transitions[0]
    turn = first.provider_turn.model_copy(
        update={"mode": ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON}
    )
    turn = ProviderControllerTurnV22.model_validate(
        turn.model_copy(
            update={
                "turn_sha256": semantic_sha256_v22(
                    turn.model_dump(mode="json", exclude={"turn_sha256"})
                )
            }
        ).model_dump(mode="python")
    )
    transition = first.model_copy(update={"provider_turn": turn})
    transition = transition.model_copy(
        update={
            "transition_sha256": semantic_sha256_v22(
                transition.model_dump(
                    mode="json",
                    exclude={"transition_sha256"},
                )
            )
        }
    )
    forged = report.model_copy(
        update={"transitions": (transition, *report.transitions[1:])}
    )
    with pytest.raises(ValueError, match="transition binding differs"):
        forged.model_copy(
            update={
                "report_sha256": semantic_sha256_v22(
                    forged.model_dump(mode="json", exclude={"report_sha256"})
                )
            }
        ).require_report()


def test_provider_protocol_suite_records_a_semantic_failure_without_crashing() -> None:
    report = run_provider_protocol_capability_suite_v22(
        provider_probe=_probe(),
        complete=ScriptedProtocolProvider(fail_first=True).complete,
    )
    assert report.transition_count == 50
    assert report.first_pass_accepted_count == 47
    assert report.post_correction_accepted_count == 49
    assert report.provider_gate_eligible is False
    assert report.terminal is (
        ProviderProtocolSuiteTerminalV22.BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE
    )


def _run_v3(
    provider: ScriptedProtocolProvider,
    *,
    replicate_id: str = "A",
):
    return run_provider_protocol_capability_suite_v3(
        provider_probe=_probe(),
        complete=provider.complete,
        replicate_id=replicate_id,
        implementation_commit="a" * 40,
        implementation_tree="b" * 40,
        preregistration_sha256="c" * 64,
    )


def test_provider_protocol_v3_balances_ordinary_and_correction_matrix() -> None:
    provider = ScriptedProtocolProvider()
    report = _run_v3(provider)

    assert report.schema_version == "dta-v22.provider-protocol-capability-report.v3"
    assert report.transition_count == report.provider_calls == provider.calls == 52
    assert report.ordinary_transition_count == 48
    assert report.correction_transition_count == 4
    assert report.ordinary_first_pass_accepted_count == 48
    assert report.correction_envelope_accepted_count == 4
    assert report.final_accepted_count == 52
    assert report.ordinary_first_pass_protocol_acceptance == 1.0
    assert report.correction_envelope_acceptance == 1.0
    assert report.final_protocol_acceptance == 1.0
    assert report.ordinary_first_pass_by_arm["FLAT_CANONICAL"].transition_count == 24
    assert report.ordinary_first_pass_by_arm["PLANNER_LITE"].transition_count == 24
    assert report.correction_acceptance_by_arm["FLAT_CANONICAL"].transition_count == 2
    assert report.correction_acceptance_by_arm["PLANNER_LITE"].transition_count == 2
    correction_surface = {
        (item.category.value, item.arm.value)
        for item in report.transitions
        if item.transition_kind == "CORRECTION_ENVELOPE"
    }
    assert correction_surface == {
        ("STALE_ACTION_CORRECTION", "FLAT_CANONICAL"),
        ("STALE_ACTION_CORRECTION", "PLANNER_LITE"),
        ("INVALID_REF_CORRECTION", "FLAT_CANONICAL"),
        ("INVALID_REF_CORRECTION", "PLANNER_LITE"),
    }
    assert report.provider_gate_eligible is True
    assert report.failed_gate_codes == ()
    assert report.terminal is ProviderProtocolSuiteTerminalV3.PROVIDER_PROTOCOL_GATE_PASS


def test_provider_protocol_v3_excludes_correction_envelopes_from_ordinary_denominator() -> None:
    report = _run_v3(
        ScriptedProtocolProvider(fail_calls=frozenset({49, 50, 51, 52}))
    )

    assert report.ordinary_first_pass_accepted_count == 48
    assert report.ordinary_first_pass_protocol_acceptance == 1.0
    assert report.correction_envelope_accepted_count == 0
    assert report.correction_envelope_acceptance == 0.0
    assert report.final_accepted_count == 48
    assert report.failure_taxonomy["CORRECTION_NOT_RECOVERED"] == 4
    assert "CORRECTION_ALL_REQUIRED" in report.failed_gate_codes
    assert report.provider_gate_eligible is False


def test_provider_protocol_v3_integer_and_per_arm_gates_cannot_be_hidden() -> None:
    one_miss = _run_v3(ScriptedProtocolProvider(fail_calls=frozenset({1})))
    assert one_miss.ordinary_first_pass_accepted_count == 47
    assert one_miss.final_accepted_count == 51
    assert one_miss.provider_gate_eligible is True

    flat_two_misses = _run_v3(
        ScriptedProtocolProvider(fail_calls=frozenset({1, 3}))
    )
    assert flat_two_misses.ordinary_first_pass_accepted_count == 46
    assert flat_two_misses.ordinary_first_pass_protocol_acceptance >= 0.95
    assert flat_two_misses.ordinary_first_pass_by_arm[
        "FLAT_CANONICAL"
    ].accepted_count == 22
    assert "FLAT_ORDINARY_MINIMUM" in flat_two_misses.failed_gate_codes
    assert "FINAL_MINIMUM" in flat_two_misses.failed_gate_codes
    assert flat_two_misses.provider_gate_eligible is False
    assert flat_two_misses.terminal is (
        ProviderProtocolSuiteTerminalV3.BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE
    )


def test_provider_protocol_v3_failure_taxonomy_is_complete_and_bounded() -> None:
    report = _run_v3(ScriptedProtocolProvider(fail_calls=frozenset({1, 49})))
    assert set(report.failure_taxonomy) == {
        item.value for item in ProviderProtocolFailureClassV3
    }
    assert report.failure_taxonomy["SEMANTIC_CATEGORY_MISMATCH"] == 1
    assert report.failure_taxonomy["CORRECTION_NOT_RECOVERED"] == 1
    assert sum(report.failure_taxonomy.values()) == 2
    assert all(
        item.failure_classification is None
        for item in report.transitions
        if item.final_accepted
    )


def test_provider_protocol_v3_allows_repeated_raw_response_content_digests() -> None:
    report = _run_v3(ScriptedProtocolProvider(constant_raw_response=True))
    assert len({item.provider_turn.raw_response_sha256 for item in report.transitions}) == 1
    assert len({item.provider_turn.turn_sha256 for item in report.transitions}) == 52
    assert report.provider_gate_eligible is True


def test_provider_protocol_v3_transport_abort_returns_typed_complete_taxonomy() -> None:
    provider = ScriptedProtocolProvider()

    def abort_fifth(*, request: ProviderTurnRequestV22) -> ProviderControllerTurnV22:
        if provider.calls == 4:
            provider.calls += 1
            raise ConnectionError("unpublished Provider transport detail")
        return provider.complete(request=request)

    receipt = run_provider_protocol_replicate_v3(
        provider_probe=_probe(),
        complete=abort_fifth,
        attempted_calls=lambda: provider.calls,
        replicate_id="A",
        implementation_commit="a" * 40,
        implementation_tree="b" * 40,
        preregistration_sha256="c" * 64,
    )

    assert isinstance(receipt, ProviderProtocolPartialFailureReceiptV3)
    assert receipt.completed_transition_count == 4
    assert receipt.provider_calls == 5
    assert receipt.failure_classification is (
        ProviderProtocolFailureClassV3.PROVIDER_TRANSPORT_ABORT
    )
    assert receipt.failure_taxonomy["PROVIDER_TRANSPORT_ABORT"] == 48
    assert sum(receipt.failure_taxonomy.values()) == 52 - sum(
        item.semantic_category_accepted for item in receipt.completed_transitions
    )
    assert receipt.provider_gate_eligible is False
    assert receipt.terminal is (
        ProviderProtocolSuiteTerminalV3.BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE
    )
