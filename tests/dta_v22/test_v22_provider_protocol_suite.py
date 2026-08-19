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
    ProviderProtocolSuiteTerminalV22,
    SyntheticTransitionCategoryV22,
    run_provider_protocol_capability_suite_v22,
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
    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self,
        *,
        request: ProviderTurnRequestV22,
    ) -> ProviderControllerTurnV22:
        self.calls += 1
        decision = _decision(request)
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
                {
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
