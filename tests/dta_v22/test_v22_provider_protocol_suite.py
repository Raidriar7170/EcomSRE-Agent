from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

import pytest

from ecomsre.dta_v2.v22.controller_contracts import ControllerDecisionV22
from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.controller_modes import (
    PRIMARY_MODEL_V22,
    ProviderOutputModeV22,
    ProviderProbeStatusV22,
    probe_provider_output_mode_v22,
)
from ecomsre.dta_v2.v22.controller_provider import ProviderControllerTurnV22
from ecomsre.dta_v2.v22.protocol_suite import (
    ProviderProtocolCapabilityReportV22,
    ProviderProtocolSuiteTerminalV22,
    SyntheticTransitionCategoryV22,
    run_provider_protocol_capability_suite_v22,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22


class ScriptedProtocolProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self,
        *,
        mode: ProviderOutputModeV22,
        visible_state: Mapping[str, object],
    ) -> ProviderControllerTurnV22:
        self.calls += 1
        decision_payload = visible_state["required_decision"]
        decision = ControllerDecisionV22.model_validate_json(
            json.dumps(decision_payload, sort_keys=True)
        )
        payload: dict[str, Any] = {
            "schema_version": "dta-v22.provider-controller-turn.v1",
            "model": PRIMARY_MODEL_V22,
            "mode": mode,
            "decision": decision,
            "raw_response_sha256": semantic_sha256_v22(
                {
                    "call": self.calls,
                    "transition": visible_state["transition_id"],
                    "correction": visible_state["correction_ordinal"],
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
            }
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
    assert report.provider_calls == provider.calls == 52
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
            for turn in item.provider_turns
        }
    ) == 52


def test_provider_protocol_report_rejects_rehashed_transition_omission() -> None:
    report = run_provider_protocol_capability_suite_v22(
        provider_probe=_probe(),
        complete=ScriptedProtocolProvider().complete,
    )
    forged = report.model_copy(
        update={
            "transitions": report.transitions[:-1],
            "transition_count": 49,
            "provider_calls": 50,
        }
    )
    with pytest.raises(ValueError, match="canonical transition matrix"):
        ProviderProtocolCapabilityReportV22.model_validate(
            forged.model_copy(
                update={
                    "report_sha256": semantic_sha256_v22(
                        forged.model_dump(mode="json", exclude={"report_sha256"})
                    )
                }
            ).model_dump(mode="python")
        )


def test_provider_protocol_report_rejects_wrong_mode_and_fake_gate_counts() -> None:
    report = run_provider_protocol_capability_suite_v22(
        provider_probe=_probe(),
        complete=ScriptedProtocolProvider().complete,
    )
    first = report.transitions[0]
    turn = first.provider_turns[0].model_copy(
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
    transition = first.model_copy(update={"provider_turns": (turn,)})
    transition = type(first).model_validate(
        transition.model_copy(
            update={
                "transition_sha256": semantic_sha256_v22(
                    transition.model_dump(
                        mode="json",
                        exclude={"transition_sha256"},
                    )
                )
            }
        ).model_dump(mode="python")
    )
    forged = report.model_copy(
        update={"transitions": (transition, *report.transitions[1:])}
    )
    with pytest.raises(ValueError, match="selected Provider mode"):
        ProviderProtocolCapabilityReportV22.model_validate(
            forged.model_copy(
                update={
                    "report_sha256": semantic_sha256_v22(
                        forged.model_dump(mode="json", exclude={"report_sha256"})
                    )
                }
            ).model_dump(mode="python")
        )
